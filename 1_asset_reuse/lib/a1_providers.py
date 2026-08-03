"""a1: tier providers + registry for the acquire retrieval layer."""

from __future__ import annotations

import json
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
        for prefix, entries in self.ensure_index().items():
            for key, size in entries:
                base = key.rsplit("/", 1)[-1].lower()
                if ".thumbs" in key or not base.endswith(".usd"):
                    continue
                hits = sum(1 for t in toks if t in base)
                if toks and not hits:
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
        for e in entries:
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
        return sorted(out, key=lambda c: (-c.score, c.candidate_id))[:limit]
