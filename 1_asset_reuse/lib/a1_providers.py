"""a1: tier providers + registry for the acquire retrieval layer."""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from agenticsim.openxsim.assets import AssetCandidate

BUCKET = "https://omniverse-content-production.s3-us-west-2.amazonaws.com"


def _tokens(query: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 1}


# Entries that are not candidate objects at all: material libraries, physics-
# only proxies, scene scaffolding. Measured 2026-08-11 over the NVIDIA prop
# listing: 88 of 465 (19%). Letting them into the candidate pool costs a gate
# round-trip each and, worse, lets a query like "a metal wrench" rank five
# metal SHADERS above any actual tool.
NON_OBJECT = re.compile(
    r"(^|_)(materials?|physics_material|plane|frame_prim|checkerboard|"
    r"instaceable_meshes|instanceable_meshes)(\.|$)"
    r"|^(Acrylic|Aluminium|Metal|MetalPainted|Plastic|Rubber|Steel)_"
    r"|_physics$"
    r"|^M_ConveyorBelt",
    re.IGNORECASE,
)


def _words(stem: str) -> set[str]:
    """Split an asset stem into words. USD names delimit with _ - . and
    camelCase (`035_power_drill`, `SM_Mug_A2`, `sm_whitecorrugatedbox_b04`),
    so camel boundaries are made explicit before splitting."""
    hay = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", stem)
    return {w for w in re.sub(r"[^A-Za-z0-9]+", "_", hay).lower().split("_") if w}


def boundary_hits(toks: set[str], stem: str) -> int:
    """Number of query tokens that match `stem` AT A WORD BOUNDARY.

    Replaces a plain substring test, which produced confidently wrong results
    rather than no result -- measured on the real listing:
        "trash bin"  -> sektion_ca(bin)et   3 hits
        "teddy bear" -> caster_(bear)ing    2 hits
    Neither object exists in the corpus, but the retriever returned a cabinet
    and a bearing, and the ledger then records the identity we ASKED for
    (`identity.basis = requested_by_acquire`), so nothing downstream can tell.

    Measured cost of the tightening (2026-08-11, 30 labelled queries): gold
    stays top-1 on 26/30 -- identical to substring -- nothing broke, and the
    candidate pool for "cardboard box" fell 33 -> 6. A trailing digit run is
    still absorbed (`b04` matches token `b`) because USD names suffix variants
    that way."""
    words = _words(stem)
    hits = 0
    for t in toks:
        if t in words or any(w.startswith(t) and w[len(t) :].isdigit() for w in words):
            hits += 1
    return hits


def list_bucket_keys(prefix, bucket=BUCKET, timeout_s=120):
    ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"
    out, token = [], None
    while True:
        url = f"{bucket}/?list-type=2&prefix={urllib.parse.quote(prefix)}"
        if token:
            url += f"&continuation-token={urllib.parse.quote(token)}"
        root = ET.fromstring(urllib.request.urlopen(url, timeout=timeout_s).read())
        for c in root.iter(f"{ns}Contents"):
            out.append((c.find(f"{ns}Key").text, int(c.find(f"{ns}Size").text)))
        t = root.find(f"{ns}NextContinuationToken")
        if t is None:
            return out
        token = t.text


class NvidiaAssetServerProvider:
    name = "nvidia_server"

    def __init__(self, prefixes, index_path, bucket=BUCKET, list_keys_fn=None):
        self.prefixes = list(prefixes)
        self.index_path = Path(index_path)
        self.bucket = bucket
        self._list = list_keys_fn or list_bucket_keys

    def ensure_index(self, refresh=False):
        if self.index_path.is_file() and not refresh:
            return json.loads(self.index_path.read_text())
        index = {
            p: [[k, s] for k, s in self._list(p, self.bucket)] for p in self.prefixes
        }
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(index, indent=1))
        return index

    def search(self, query, limit=20):
        toks = _tokens(query)
        out = []
        scanned = 0
        token_miss = 0
        non_object = 0
        for prefix, entries in self.ensure_index().items():
            for key, size in entries:
                scanned += 1
                base = key.rsplit("/", 1)[-1]
                if ".thumbs" in key or not base.lower().endswith(".usd"):
                    continue
                stem = base[:-4]
                if NON_OBJECT.search(stem):
                    non_object += 1
                    continue
                hits = boundary_hits(toks, stem)
                if toks and not hits:
                    token_miss += 1
                    continue
                out.append(
                    AssetCandidate(
                        candidate_id=f"nvidia:{key}",
                        name=key.rsplit("/", 1)[-1],
                        category=key.rsplit("/", 2)[-2].lower(),
                        download_url=f"{self.bucket}/{urllib.parse.quote(key)}",
                        source_page=f"{self.bucket}/{urllib.parse.quote(key)}",
                        format="usd",
                        provider=self.name,
                        license="unknown (NVIDIA Omniverse asset server)",
                        score=float(hits),
                        metadata={"key": key, "size_bytes": size, "prefix": prefix},
                    )
                )
        self.last_stats = {
            "scanned": scanned,
            "token_miss": token_miss,
            "non_object": non_object,
        }
        return sorted(out, key=lambda c: (-c.score, c.candidate_id))[:limit]


class RoboTwinLocalProvider:
    name = "robotwin_local"

    def __init__(self, catalog_path):
        self.catalog_path = Path(catalog_path)

    def search(self, query, limit=20):
        data = json.loads(self.catalog_path.read_text())
        entries = data["entries"] if isinstance(data, dict) else data
        toks = _tokens(query)
        out = []
        scanned = 0
        token_miss = 0
        for e in entries:
            scanned += 1
            names = {
                str(n).lower()
                for n in [
                    e.get("category", ""),
                    e.get("semantic_name", ""),
                    *e.get("aliases", []),
                ]
            }
            hits = sum(1 for t in toks if t in names)
            if not hits:
                token_miss += 1
                continue
            out.append(
                AssetCandidate(
                    candidate_id=f"catalog:{e['asset_id']}",
                    name=e["asset_id"],
                    category=e.get("category", ""),
                    download_url=f"file://{e.get('asset_path', '')}",
                    source_page=str(self.catalog_path),
                    format="catalog_entry",
                    provider=self.name,
                    license="already registered",
                    score=float(hits),
                    metadata={"asset_id": e["asset_id"], "colors": e.get("colors", [])},
                )
            )
        self.last_stats = {"scanned": scanned, "token_miss": token_miss}
        return sorted(out, key=lambda c: (-c.score, c.candidate_id))[:limit]


from dataclasses import dataclass

from agenticsim.openxsim.assets import AssetScout


@dataclass
class Tier:
    tier: int
    provider: object


class DedupedProvider:
    """Wrap a provider that truncates its result list *before* de-duplicating.

    ``GitHubRepositoryDiscoveryProvider`` walks several repositories, pools every
    match, then does ``sorted(...)[:limit]`` with no de-duplication (openxsim
    ``assets.py``). Asset repos are heavily forked/mirrored on GitHub, so the same
    model can occupy several of those ``limit`` slots. ``AssetScout`` does de-dup
    afterwards, but by then the truncation already happened -- de-duping can only
    shrink the list, never pull replacements up from rank ``limit + 1``.

    So ask the inner provider for ``overfetch`` times as many candidates, collapse
    duplicate ``download_url``s (keeping the higher-scored one), and only then
    truncate. openxsim itself stays unmodified, per the retrieval design decision.
    """

    def __init__(self, inner, overfetch=4):
        if overfetch < 1:
            raise ValueError("overfetch must be >= 1")
        self.inner = inner
        self.overfetch = overfetch
        self.name = inner.name

    def search(self, query, limit=20):
        raw = self.inner.search(query, limit=limit * self.overfetch)
        best = {}
        for c in raw:
            prev = best.get(c.download_url)
            if prev is None or c.score > prev.score:
                best[c.download_url] = c
        ranked = sorted(best.values(), key=lambda c: (-c.score, c.candidate_id))
        self.last_stats = {
            "fetched": len(raw),
            "after_dedup": len(ranked),
            "returned": min(len(ranked), limit),
            "duplicates_dropped": len(raw) - len(ranked),
        }
        return ranked[:limit]

    @property
    def last_errors(self):
        return getattr(self.inner, "last_errors", [])

    def __getattr__(self, item):
        """Stay transparent: anything this wrapper does not define itself
        (``token``, ``repository_limit``, ``timeout_s`` ...) resolves on the
        wrapped provider, so wrapping does not change the provider's surface."""
        return getattr(self.inner, item)


def load_providers(config):
    g = dict(config.get("globals", {}))
    pc = config["providers"]
    tiers = []
    if pc.get("robotwin_local", {}).get("enabled"):
        tiers.append(
            Tier(
                pc["robotwin_local"].get("tier", 0),
                RoboTwinLocalProvider(pc["robotwin_local"]["catalog"]),
            )
        )
    if pc.get("nvidia_server", {}).get("enabled"):
        n = pc["nvidia_server"]
        tiers.append(
            Tier(
                n.get("tier", 1),
                NvidiaAssetServerProvider(n["prefixes"], n["index_path"]),
            )
        )
    if pc.get("nvidia_visual", {}).get("enabled"):
        from lib.a5_visual import VisualProvider

        v = pc["nvidia_visual"]
        tiers.append(
            Tier(
                # Same tier as the lexical NVIDIA provider on purpose: the two
                # are channels over ONE corpus, not a fallback chain, and
                # tiered_search fuses same-tier channels rather than letting
                # whichever answers first win.
                v.get("tier", 1),
                VisualProvider(
                    v.get("index_path", pc["nvidia_server"]["index_path"]),
                    v["thumbs_dir"],
                    v["cache_path"],
                    model_name=v.get("model", "openai/clip-vit-large-patch14"),
                ),
            )
        )
    if pc.get("github_tree", {}).get("enabled"):
        from agenticsim.openxsim.assets import GitHubTreeSearchProvider

        for repo in pc["github_tree"]["repositories"]:
            tiers.append(
                Tier(
                    pc["github_tree"].get("tier", 2),
                    GitHubTreeSearchProvider(
                        repo["repository"],
                        branch=repo.get("branch", "main"),
                        license=repo.get("license", "unknown"),
                    ),
                )
            )
    if pc.get("github_discovery", {}).get("enabled"):
        from agenticsim.openxsim.assets import GitHubRepositoryDiscoveryProvider

        d = pc["github_discovery"]
        kwargs = {"repository_limit": d.get("repository_limit", 5)}
        if "token_env" in d:
            kwargs["token"] = os.environ.get(d["token_env"])
        tiers.append(
            Tier(
                d.get("tier", 3),
                DedupedProvider(
                    GitHubRepositoryDiscoveryProvider(**kwargs),
                    overfetch=int(d.get("overfetch", 4)),
                ),
            )
        )
    return tiers, g


def tiered_search(tiers, query, *, viable_fn, limit=20):
    consulted, errors, provider_stats = [], [], []
    for tier_no in sorted({t.tier for t in tiers}):
        group = [t.provider for t in tiers if t.tier == tier_no]
        # The visual channel is scored in cosine similarity, the lexical one in
        # token-hit counts; AssetScout would sort them into one list as if the
        # numbers were comparable. Split it out and fuse by RANK instead.
        visual = [p for p in group if getattr(p, "name", "") == "nvidia_visual"]
        group = [p for p in group if getattr(p, "name", "") != "nvidia_visual"]
        consulted.append(tier_no)
        scout = AssetScout(group) if group else None
        try:
            cands = scout.search(query, limit=limit) if scout else []
            if visual:
                from lib.a5_visual import rrf_merge

                vis_cands = visual[0].search(query, limit=limit)
                provider_stats.append(
                    {
                        "tier": tier_no,
                        "provider": visual[0].name,
                        **visual[0].last_stats,
                    }
                )
                cands = (
                    rrf_merge([cands, vis_cands], limit=limit)
                    if cands
                    else vis_cands[:limit]
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(
                {"tier": tier_no, "provider": group[0].name, "error": str(exc)}
            )
            continue
        errors.extend({"tier": tier_no, **e} for e in scout.last_errors)
        provider_stats.extend(
            {"tier": tier_no, "provider": p.name, **p.last_stats}
            for p in group
            if hasattr(p, "last_stats")
        )
        if tier_no == 0:
            if cands:
                return {
                    "tier0_hit": cands[0],
                    "candidates": [],
                    "tiers_consulted": consulted,
                    "provider_errors": errors,
                    "provider_stats": provider_stats,
                }
            continue
        if any(viable_fn(c) for c in cands):
            return {
                "tier0_hit": None,
                "candidates": cands,
                "tiers_consulted": consulted,
                "provider_errors": errors,
                "provider_stats": provider_stats,
            }
    return {
        "tier0_hit": None,
        "candidates": [],
        "tiers_consulted": consulted,
        "provider_errors": errors,
        "provider_stats": provider_stats,
    }
