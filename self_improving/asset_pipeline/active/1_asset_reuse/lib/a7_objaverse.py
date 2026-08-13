"""a7: Objaverse retrieval provider -- the first asset source outside the
NVIDIA server, added deliberately as a DIFFERENT kind of source:

  - different host (Hugging Face + Sketchfab ecosystem vs NVIDIA S3),
  - different curation (46,207 community objects labelled into 1,156 LVIS
    categories by the Objaverse authors vs NVIDIA's in-house props),
  - different licensing model (a machine-readable per-object Creative Commons
    tag vs one umbrella EULA) -- which slots straight into the ledger's
    SPDX auto-declare allowlist: a CC-BY hammer arrives license-declared with
    evidence, no human in the loop.

Two search channels, ranked together then hydrated:

  lvis_category  exact curated-category matching ("hammer", "teddy_bear") --
                 no filename substrings, so the whole class of substring
                 accidents the NVIDIA channel needed word-boundary logic for
                 never arises. Partial-coverage category hits are scaled by
                 how much of the query they cover: unscaled, category "box"
                 (1 of 2 query words) outranked full-name "tissue box"
                 matches and the identity gate's top-3 window saw only
                 crates (measured 2026-08-13).
  name_match     the 798k objects OUTSIDE the LVIS subset, matched by what
                 their authors NAMED them (index built by
                 scripts/1_search/build_objaverse_name_index.py). Requires
                 >=2 name-word agreement for multi-word queries so "tissue
                 box" pulls tissue boxes, not every box on Sketchfab.

Candidate hydration is lazy and happens only for the ranked winners: the
per-object metadata (real name, CC license, Sketchfab thumbnail URL) lives in
~160 sharded json.gz files, cached on disk and memoized in memory by mtime
(the a1 catalog-cache idiom; re-parsing per candidate once put a warm query
at 929 ms). A dead thumbnail URL simply leaves the candidate unverifiable --
the post-render check at materialize time covers identity then, same as the
GitHub path.
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
        self._paths_full = None  # 798k, loaded only when the name channel fires
        self._names = None
        self._tokens = None
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
        # the NAME channel (see search) reaches outside the LVIS subset, so
        # keep the full uid->path map -- but only when its index exists
        if (self.data_dir / "name_index.json.gz").exists():
            self._paths_full = paths

    def _load_names(self):
        """Full-corpus name index (798k objects). Lazy: costs ~150 MB in
        memory, paid only on the first query that needs it."""
        if self._names is not None:
            return True
        idx = self.data_dir / "name_index.json.gz"
        tok = self.data_dir / "token_index.json.gz"
        if not (idx.exists() and tok.exists()):
            return False
        self._names = json.load(gzip.open(idx))
        self._tokens = json.load(gzip.open(tok))
        return True

    def _path_of(self, uid):
        p = self._paths.get(uid)
        if p is None and self._paths_full:
            p = self._paths_full.get(uid)
        return p

    def _http(self, url):
        if self._fetch is not None:
            return self._fetch(url)
        with urllib.request.urlopen(url, timeout=self.timeout_s) as r:
            return r.read()

    def _annotation(self, uid):
        """Per-object metadata via its shard, cached on disk AND in memory.
        The shard id is the same partition the object's glb lives in
        (glbs/000-118/<uid>.glb -> metadata/000-118.json.gz). Memoized by
        mtime so a rewritten shard file is still picked up."""
        shard = Path(self._path_of(uid)).parent.name
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
                # query mentions the category, scaled by query coverage
                score = 2.0 * len(cat_tokens) / max(1, len(q_tokens))
            elif q_tokens and q_tokens <= cat_tokens:
                score = 1.5  # query is a strict part of the category name
            else:
                continue
            scored.append((score, cat, uids))
        scored.sort(key=lambda t: (-t[0], t[1]))

        # Both channels become (score, uid, label, channel) specs first;
        # hydration -- the expensive part -- happens only for ranked winners.
        specs, seen = [], set()
        for score, cat, uids in scored:
            for uid in uids[: self.per_category_cap]:
                if uid in seen or uid not in self._paths:
                    continue
                seen.add(uid)
                specs.append((score, uid, cat, "lvis_category"))

        name_matches = 0
        if self._load_names():
            words = [w for w in q_tokens if len(w) > 1 and w in self._tokens]
            need = min(2, len(words)) if len(words) > 1 else 1
            counts: dict[int, int] = {}
            for w in words:
                for row in self._tokens[w]:
                    counts[row] = counts.get(row, 0) + 1
            ranked = sorted(
                (r for r, n in counts.items() if n >= need),
                key=lambda r: (-counts[r], r),
            )
            for row in ranked[: 6 * self.per_category_cap]:
                uid = self._names["uids"][row]
                if uid in seen or self._path_of(uid) is None:
                    continue
                seen.add(uid)
                specs.append((1.2, uid, q, "name_match"))
                name_matches += 1

        specs.sort(key=lambda t: -t[0])
        out, hydrated = [], 0
        for score, uid, label, channel in specs:
            if len(out) >= limit:
                break
            c = self._candidate(uid, label, score, channel=channel)
            if c is not None:
                out.append(c)
                hydrated += 1

        self.last_stats = {
            "lvis_categories": len(self._lvis),
            "matched_categories": len(scored),
            "hydrated": hydrated,
            "name_matches": name_matches,
            "thumbnail_failures": sum(1 for e in self.last_errors if "thumbnail" in e),
        }
        return out

    def _candidate(self, uid, category, score, *, channel):
        path = self._path_of(uid)
        if path is None:
            return None
        try:
            ann = self._annotation(uid)
        except Exception as exc:  # noqa: BLE001
            self.last_errors.append({"uid": uid, "annotation": repr(exc)})
            ann = {}
        slug = str(ann.get("license") or "").lower()
        spdx = SPDX_BY_SLUG.get(slug)
        thumb = self._thumbnail(uid, ann) if ann else None
        pretty = ann.get("name") or f"{category}_{uid[:8]}"
        return AssetCandidate(
            candidate_id=f"objaverse:{uid}",
            name=f"{pretty} ({uid[:8]}.glb)",
            category=category,
            download_url=f"{HF_BASE}/{path}",
            source_page=(
                (ann.get("viewerUrl") or ann.get("uri")) or f"{HF_BASE}/{path}"
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
                "lvis_category": category if channel == "lvis_category" else None,
                "channel": channel,
                "thumbnail": thumb,
                "license_spdx": spdx,
                "license_slug": slug,
                "license_metadata_url": f"{HF_BASE}/metadata/"
                f"{Path(path).parent.name}.json.gz",
                "sketchfab_name": ann.get("name"),
            },
        )
