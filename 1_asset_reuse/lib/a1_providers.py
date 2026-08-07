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
        for prefix, entries in self.ensure_index().items():
            for key, size in entries:
                scanned += 1
                base = key.rsplit("/", 1)[-1].lower()
                if ".thumbs" in key or not base.endswith(".usd"):
                    continue
                hits = sum(1 for t in toks if t in base)
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
        self.last_stats = {"scanned": scanned, "token_miss": token_miss}
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
                GitHubRepositoryDiscoveryProvider(**kwargs),
            )
        )
    return tiers, g


def tiered_search(tiers, query, *, viable_fn, limit=20):
    consulted, errors, provider_stats = [], [], []
    for tier_no in sorted({t.tier for t in tiers}):
        group = [t.provider for t in tiers if t.tier == tier_no]
        consulted.append(tier_no)
        scout = AssetScout(group)
        try:
            cands = scout.search(query, limit=limit)
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
