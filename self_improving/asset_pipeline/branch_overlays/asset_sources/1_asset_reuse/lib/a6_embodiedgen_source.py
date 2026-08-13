"""a6: standalone asset-search provider for the HF dataset
HorizonRobotics/EmbodiedGenData (4,168 assets, Apache-2.0).

Standalone on purpose -- NOT wired into lib/a1_providers.py's tier registry
yet (see configs/providers.json comment). Shaped like a1's providers
(ensure_index/search/AssetCandidate contract) so future wiring is ~2 lines.
Pure stdlib (urllib) -- no new pip dependencies.

Real repo layout (verified against the live dataset, 2026-08-10 -- this
deviates from an earlier assumption that asset_dir/urdf_path were resolvable
directly under the repo root): every asset lives under a "dataset/" root
directory in the HF repo, e.g. metadata row asset_dir=
"bathroom_supplies/bath_products/<uuid>" resolves on disk at
"dataset/bathroom_supplies/bath_products/<uuid>/...". REPO_DATA_ROOT below
carries that prefix. The datasets-server `rows` API paginates 100/page and
is what ensure_index uses; HF's tree API (a separate "/api/datasets/..."
host path, not "/datasets/...") is used by fetch_asset to list an asset's
mesh/ subdirectory, since resolve URLs alone can't list a directory.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from pathlib import Path

from agenticsim.openxsim.assets import AssetCandidate

DEFAULT_BASE_URL = "https://huggingface.co/datasets/HorizonRobotics/EmbodiedGenData"
DATASETS_SERVER_ROWS = (
    "https://datasets-server.huggingface.co/rows"
    "?dataset=HorizonRobotics%2FEmbodiedGenData&config=default&split=train"
)
PAGE_SIZE = 100
REPO_DATA_ROOT = "dataset"
# Referenced-but-not-needed-for-import file kinds (video previews, gaussian-
# splat point clouds) that may show up alongside mesh/ files -- skip them.
SKIP_SUFFIXES = (".mp4", ".ply")


def _tokens(query):
    return {t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 1}


def _default_fetch(url, timeout_s=60):
    with urllib.request.urlopen(url, timeout=timeout_s) as r:
        return r.read()


def _api_tree_url(base_url, path):
    """base_url is the human "/datasets/<org>/<name>" URL; the tree-listing
    API lives under "/api/datasets/<org>/<name>/tree/main/<path>" instead."""
    api_base = base_url.replace(
        "huggingface.co/datasets/", "huggingface.co/api/datasets/", 1
    )
    return f"{api_base}/tree/main/{path}"


class EmbodiedGenSourceProvider:
    name = "embodiedgen_data"

    def __init__(
        self, index_path, base_url=DEFAULT_BASE_URL, fetch_fn=None, page_size=PAGE_SIZE
    ):
        self.index_path = Path(index_path)
        self.base_url = base_url.rstrip("/")
        self._fetch = fetch_fn or _default_fetch
        self.page_size = page_size

    def ensure_index(self, refresh=False):
        if self.index_path.is_file() and not refresh:
            return json.loads(self.index_path.read_text())
        rows = []
        offset = 0
        while True:
            url = f"{DATASETS_SERVER_ROWS}&offset={offset}&length={self.page_size}"
            page = json.loads(self._fetch(url))
            for entry in page["rows"]:
                row = entry["row"]
                rows.append(
                    {
                        "uuid": row["uuid"],
                        "primary_category": row.get("primary_category", ""),
                        "secondary_category": row.get("secondary_category", ""),
                        "category": row.get("category", ""),
                        "description": row.get("description", ""),
                        "asset_dir": row["asset_dir"],
                        "urdf_path": row.get("urdf_path", ""),
                    }
                )
            offset += self.page_size
            if offset >= page["num_rows_total"]:
                break
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(rows, indent=1))
        return rows

    def search(self, query, limit=20):
        toks = _tokens(query)
        out = []
        for row in self.ensure_index():
            haystack = " ".join(
                [
                    row.get("category", ""),
                    row.get("secondary_category", ""),
                    row.get("primary_category", ""),
                    row.get("description", ""),
                ]
            ).lower()
            hits = sum(1 for t in toks if t in haystack)
            if not hits:
                continue
            uuid = row["uuid"]
            asset_dir = row["asset_dir"]
            out.append(
                AssetCandidate(
                    candidate_id=f"embodiedgen:{uuid}",
                    name=row.get("category") or uuid,
                    category=row.get("category", ""),
                    download_url=f"{self.base_url}/resolve/main/{REPO_DATA_ROOT}/{asset_dir}",
                    source_page=f"{self.base_url}/tree/main/{REPO_DATA_ROOT}/{asset_dir}",
                    format="urdf",
                    provider=self.name,
                    license="Apache-2.0 (HorizonRobotics/EmbodiedGenData)",
                    score=float(hits),
                    metadata={
                        "uuid": uuid,
                        "asset_dir": asset_dir,
                        "urdf_path": row.get("urdf_path", ""),
                        "description": row.get("description", ""),
                        "size_hint": None,
                    },
                )
            )
        return sorted(out, key=lambda c: (-c.score, c.candidate_id))[:limit]

    def fetch_asset(self, candidate, dest_dir):
        """Download the URDF plus every file under the asset's mesh/
        subdirectory (visual + collision meshes, .mtl, textures). Directory
        listing isn't possible over a resolve URL, so this uses HF's tree
        API for mesh/ and resolve URLs for the actual file bytes."""
        meta = candidate.metadata
        asset_dir = meta["asset_dir"]
        urdf_path = meta.get("urdf_path") or f"{asset_dir}/{Path(asset_dir).name}.urdf"
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)

        files, sha256s = [], {}

        def _download(remote_path, local_relpath):
            url = f"{self.base_url}/resolve/main/{remote_path}"
            data = self._fetch(url)
            local_path = dest / local_relpath
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            files.append(local_relpath)
            sha256s[local_relpath] = digest

        _download(f"{REPO_DATA_ROOT}/{urdf_path}", Path(urdf_path).name)

        mesh_remote_dir = f"{REPO_DATA_ROOT}/{asset_dir}/mesh"
        listing = json.loads(self._fetch(_api_tree_url(self.base_url, mesh_remote_dir)))
        for entry in listing:
            if entry.get("type") != "file":
                continue
            remote_path = entry["path"]
            if remote_path.lower().endswith(SKIP_SUFFIXES):
                continue
            _download(remote_path, f"mesh/{Path(remote_path).name}")

        return {"files": files, "sha256": sha256s}
