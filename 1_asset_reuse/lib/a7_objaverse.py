"""a7: Objaverse (LVIS subset) retrieval provider -- the first asset source
outside the NVIDIA server, added deliberately as a DIFFERENT kind of source:

  - different host (Hugging Face + Sketchfab ecosystem vs NVIDIA S3),
  - different curation (46,207 community objects labelled into 1,156 LVIS
    categories by the Objaverse authors vs NVIDIA's in-house props),
  - different licensing model (a machine-readable per-object Creative Commons
    tag vs one umbrella EULA) -- which slots straight into the ledger's
    SPDX auto-declare allowlist: a CC-BY hammer arrives license-declared with
    evidence, no human in the loop.

Category matching leans on what LVIS actually is -- a curated category
vocabulary -- so the lexical channel here matches CATEGORY NAMES exactly
(`hammer`, `teddy_bear`), not filename substrings. That sidesteps the whole
class of substring accidents the NVIDIA channel needed word-boundary logic
for.

Candidate hydration is lazy and happens only for the top few results per
query: the per-object metadata (real name, CC license, Sketchfab thumbnail
URL) lives in ~160 sharded json.gz files; we fetch just the shards those
candidates need, cache them, and pull the thumbnail down so the pre-download
identity gate has a picture to look at. A dead thumbnail URL (the 2022-era
CDN links do rot) simply leaves the candidate unverifiable -- the post-render
check at materialize time covers identity then, same as the GitHub path.
"""

from __future__ import annotations

import gzip
import json
import re
import urllib.request
from pathlib import Path

from agenticsim.openxsim.assets import AssetCandidate

HF_BASE = "https://huggingface.co/datasets/allenai/objaverse/resolve/main"

# Objaverse annotations carry Sketchfab's short license slugs.
SPDX_BY_SLUG = {
    "cc0": "CC0-1.0",
    "by": "CC-BY-4.0",
    "by-sa": "CC-BY-SA-4.0",
    "by-nd": "CC-BY-ND-4.0",
    "by-nc": "CC-BY-NC-4.0",
    "by-nc-sa": "CC-BY-NC-SA-4.0",
    "by-nc-nd": "CC-BY-NC-ND-4.0",
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")


class ObjaverseLvisProvider:
    name = "objaverse"

    def __init__(self, data_dir, *, per_category_cap=6, fetch=None, timeout_s=60):
        self.data_dir = Path(data_dir)
        self.per_category_cap = int(per_category_cap)
        self.timeout_s = timeout_s
        self._fetch = fetch  # injectable for tests
        self._lvis = None
        self._paths = None
        self._shard_mem = {}  # shard -> (mtime_ns, parsed) -- see _annotation
        self.last_stats = {}
        self.last_errors = []

    # ---- data loading ----------------------------------------------------

    def _load(self):
        if self._lvis is not None:
            return
        lvis = json.load(gzip.open(self.data_dir / "lvis-annotations.json.gz"))
        paths = json.load(gzip.open(self.data_dir / "object-paths.json.gz"))
        # keep only the LVIS subset's paths: 798k -> 46k entries in memory
        keep = {u for uids in lvis.values() for u in uids}
        self._lvis = {_norm(cat): uids for cat, uids in lvis.items()}
        self._paths = {u: p for u, p in paths.items() if u in keep}

    def _http(self, url):
        if self._fetch is not None:
            return self._fetch(url)
        with urllib.request.urlopen(url, timeout=self.timeout_s) as r:
            return r.read()

    def _annotation(self, uid):
        """Per-object metadata via its shard, cached on disk AND in memory.
        The shard id is the same partition the object's glb lives in
        (glbs/000-118/<uid>.glb -> metadata/000-118.json.gz).

        The in-memory layer exists because re-parsing a gzipped shard per
        candidate put a warm 6-candidate query at 929ms; keyed by mtime (the
        a1 catalog-cache idiom) so a rewritten shard file is still picked up."""
        shard = Path(self._paths[uid]).parent.name
        cache = self.data_dir / "metadata" / f"{shard}.json.gz"
        if not cache.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(self._http(f"{HF_BASE}/metadata/{shard}.json.gz"))
        stamp = cache.stat().st_mtime_ns
        hit = self._shard_mem.get(shard)
        if hit is None or hit[0] != stamp:
            self._shard_mem[shard] = (stamp, json.load(gzip.open(cache)))
        return self._shard_mem[shard][1].get(uid, {})

    def _thumbnail(self, uid, ann):
        """Best-effort local copy of the object's Sketchfab preview. Smallest
        image >=200px wide, so the 7B gate gets roughly the same input scale
        as the NVIDIA 256x256 thumbs."""
        dest = self.data_dir / "thumbs" / f"{uid}.jpg"
        if dest.exists():
            return str(dest)
        images = (ann.get("thumbnails") or {}).get("images") or []
        images = sorted(
            (i for i in images if i.get("url")),
            key=lambda i: abs(int(i.get("width") or 0) - 256),
        )
        for img in images[:2]:
            try:
                data = self._http(img["url"])
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                return str(dest)
            except Exception as exc:  # noqa: BLE001 -- rot is expected, recorded
                self.last_errors.append({"uid": uid, "thumbnail": repr(exc)})
        return None

    # ---- search ----------------------------------------------------------

    def search(self, query, limit=10):
        self._load()
        self.last_errors = []
        q = _norm(query)
        q_tokens = set(q.split("_"))
        scored = []
        for cat, uids in self._lvis.items():
            cat_tokens = set(cat.split("_"))
            if cat == q:
                score = 3.0  # the whole query IS this category
            elif cat_tokens and cat_tokens <= q_tokens:
                score = 2.0  # query mentions the full category name
            elif q_tokens and q_tokens <= cat_tokens:
                score = 1.5  # query is a strict part of the category name
            else:
                continue
            scored.append((score, cat, uids))
        scored.sort(key=lambda t: (-t[0], t[1]))

        out, hydrated = [], 0
        for score, cat, uids in scored:
            for uid in uids[: self.per_category_cap]:
                if len(out) >= limit:
                    break
                if uid not in self._paths:
                    continue
                try:
                    ann = self._annotation(uid)
                except Exception as exc:  # noqa: BLE001
                    self.last_errors.append({"uid": uid, "annotation": repr(exc)})
                    ann = {}
                hydrated += 1
                slug = str(ann.get("license") or "").lower()
                spdx = SPDX_BY_SLUG.get(slug)
                thumb = self._thumbnail(uid, ann) if ann else None
                pretty = ann.get("name") or f"{cat}_{uid[:8]}"
                out.append(
                    AssetCandidate(
                        candidate_id=f"objaverse:{uid}",
                        name=f"{pretty} ({uid[:8]}.glb)",
                        category=cat,
                        download_url=f"{HF_BASE}/{self._paths[uid]}",
                        source_page=(
                            (ann.get("viewerUrl") or ann.get("uri"))
                            or f"{HF_BASE}/{self._paths[uid]}"
                        ),
                        format="glb",
                        provider=self.name,
                        license=(
                            f"{spdx} (Objaverse per-object metadata)"
                            if spdx
                            else f"unknown (objaverse slug: {slug or 'missing'})"
                        ),
                        score=score,
                        metadata={
                            "uid": uid,
                            "lvis_category": cat,
                            "thumbnail": thumb,
                            "license_spdx": spdx,
                            "license_slug": slug,
                            "license_metadata_url": f"{HF_BASE}/metadata/"
                            f"{Path(self._paths[uid]).parent.name}.json.gz",
                            "sketchfab_name": ann.get("name"),
                        },
                    )
                )
            if len(out) >= limit:
                break
        self.last_stats = {
            "lvis_categories": len(self._lvis),
            "matched_categories": len(scored),
            "hydrated": hydrated,
            "thumbnail_failures": sum(1 for e in self.last_errors if "thumbnail" in e),
        }
        return out[:limit]
