from __future__ import annotations

import io
import json
import threading
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import replace
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from agenticsim.openxsim.assets import (
    AssetCandidate,
    AssetScout,
    AssetScoutError,
    CatalogSearchProvider,
    DownloadedAsset,
    GitHubRepositoryDiscoveryProvider,
    compile_downloaded_asset,
    download_candidate,
)
from agenticsim.openxsim.pipeline import OpenXSimPipeline


def write_obj(path: Path) -> Path:
    path.write_text(
        "\n".join(
            [
                "v 0 0 0",
                "v 0.1 0 0",
                "v 0 0.1 0",
                "v 0 0 0.1",
                "f 1 2 3",
                "f 1 2 4",
                "f 1 3 4",
                "f 2 3 4",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def asset_server(tmp_path: Path):
    root = tmp_path / "server"
    root.mkdir()
    write_obj(root / "ceramic_mug.obj")

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def log_message(self, format, *args):
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    catalog = {
        "assets": [
            {
                "candidate_id": "mug_obj",
                "name": "ceramic mug",
                "category": "mug",
                "download_url": f"{base_url}/ceramic_mug.obj",
                "source_page": f"{base_url}/catalog/mug",
                "format": "obj",
                "license": "CC-BY-4.0",
                "metadata": {"tags": ["mug", "tabletop", "ceramic"]},
            },
            {
                "candidate_id": "irrelevant",
                "name": "industrial gear",
                "category": "gear",
                "download_url": f"{base_url}/ceramic_mug.obj?gear=1",
                "source_page": f"{base_url}/catalog/gear",
                "format": "obj",
            },
        ]
    }
    (root / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    try:
        yield root, base_url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_catalog_search_download_hash_and_cache(asset_server, tmp_path: Path) -> None:
    _, base_url = asset_server
    provider = CatalogSearchProvider(f"{base_url}/catalog.json")
    scout = AssetScout([provider])

    candidates = scout.search("ceramic tabletop mug")
    assert candidates[0].candidate_id == "mug_obj"
    downloaded = download_candidate(candidates[0], tmp_path / "cache")
    cached = download_candidate(candidates[0], tmp_path / "cache")

    assert downloaded.cache_hit is False
    assert cached.cache_hit is True
    assert cached.sha256 == downloaded.sha256
    assert cached.size_bytes > 0
    provenance = json.loads(Path(cached.provenance_path).read_text())
    assert provenance["candidate"]["source_page"].endswith("/catalog/mug")
    assert provenance["candidate"]["license"] == "CC-BY-4.0"


def test_asset_conversion_produces_real_backend_files(asset_server, tmp_path: Path) -> None:
    _, base_url = asset_server
    candidate = AssetScout([CatalogSearchProvider(f"{base_url}/catalog.json")]).search("mug")[0]
    downloaded = download_candidate(candidate, tmp_path / "cache")
    bundle = compile_downloaded_asset(downloaded, tmp_path / "compiled", asset_id="ceramic_mug")

    formats = {item.format for item in bundle.representations}
    assert {
        "obj_source",
        "obj",
        "obj_collision_proxy",
        "usda",
        "mjcf",
        "urdf",
        "sapien_manifest",
        "metasim_object",
        "validation_manifest",
    } <= formats
    for representation in bundle.representations:
        assert Path(representation.uri).is_file()
        assert len(representation.sha256) == 64
    usda = next(item for item in bundle.representations if item.format == "usda")
    mjcf = next(item for item in bundle.representations if item.format == "mjcf")
    assert Path(usda.uri).read_text().startswith("#usda 1.0")
    ET.parse(mjcf.uri)
    assert bundle.source["download_sha256"] == downloaded.sha256
    validation = json.loads(
        Path(next(item.uri for item in bundle.representations if item.format == "validation_manifest")).read_text()
    )
    assert validation["normalization"]["target_max_extent_m"] == 1.0
    assert max(validation["normalization"]["normalized_bounds"]["extent"]) == pytest.approx(1.0)
    assert validation["material"]["status"] == "generated_default_material"
    assert validation["collision"]["strategy"] == "axis_aligned_convex_box"
    assert validation["articulation"] == {
        "articulated": False,
        "joint_count": 0,
        "joint_types": [],
        "status": "rigid_mesh_no_joint_schema",
    }
    assert bundle.physical["runtime_import_required"] is True


def test_asset_pipeline_records_search_selection_and_bundle(asset_server, tmp_path: Path) -> None:
    _, base_url = asset_server
    pipeline = OpenXSimPipeline(tmp_path / "runs")
    selected, bundle = pipeline.acquire_asset(
        "ceramic mug",
        AssetScout([CatalogSearchProvider(f"{base_url}/catalog.json")]),
        asset_id="mug_asset",
        smoke_backends=("isaacsim", "mujoco", "sapien", "metasim"),
    )

    root = tmp_path / "runs" / "assets" / "mug_asset"
    evidence = json.loads((root / "search_evidence.json").read_text())
    assert selected.candidate_id == "mug_obj"
    assert bundle.asset_id == "mug_asset"
    assert evidence["query"] == "ceramic mug"
    assert evidence["selected"]["download_url"].startswith(base_url)
    assert (root / "asset_bundle.json").is_file()
    smoke = json.loads((root / "import_smoke" / "manifest.json").read_text())
    assert set(smoke["compile_results"]) == {"isaacsim", "mujoco", "sapien", "metasim"}
    assert all(item["status"] == "compiled" for item in smoke["compile_results"].values())
    recovered = CatalogSearchProvider(root / "search_evidence.json").search("ceramic mug")
    assert recovered[0].download_url == selected.download_url
    assert recovered[0].provider == selected.provider


def test_downloader_enforces_size_limit(asset_server, tmp_path: Path) -> None:
    _, base_url = asset_server
    candidate = AssetCandidate(
        candidate_id="mug",
        name="mug.obj",
        category="mug",
        download_url=f"{base_url}/ceramic_mug.obj",
        source_page=base_url,
        format="obj",
        provider="test",
    )

    with pytest.raises(AssetScoutError, match="max_bytes"):
        download_candidate(candidate, tmp_path / "cache", max_bytes=8)


def test_zip_path_traversal_is_blocked(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("../escape.obj", "v 0 0 0\nf 1 1 1\n")
    candidate = AssetCandidate(
        candidate_id="bad",
        name="bad.zip",
        category="object",
        download_url="file:///bad.zip",
        source_page="file:///bad.zip",
        format="zip",
        provider="test",
    )
    downloaded = DownloadedAsset(
        candidate=candidate,
        path=str(archive),
        sha256="0" * 64,
        size_bytes=archive.stat().st_size,
        media_type="application/zip",
        detected_format="zip",
        provenance_path=str(tmp_path / "provenance.json"),
    )

    with pytest.raises(AssetScoutError, match="path traversal"):
        compile_downloaded_asset(downloaded, tmp_path / "compiled", asset_id="bad_asset")


def test_scout_keeps_provider_failure_evidence_and_uses_working_provider(asset_server) -> None:
    _, base_url = asset_server

    class BrokenProvider:
        name = "broken"

        def search(self, query, limit=20):
            raise RuntimeError("offline")

    scout = AssetScout([BrokenProvider(), CatalogSearchProvider(f"{base_url}/catalog.json")])
    candidates = scout.search("mug")

    assert candidates
    assert scout.last_errors == [{"provider": "broken", "error": "offline"}]


def test_asset_pipeline_persists_search_failure_for_offline_retry(tmp_path: Path) -> None:
    class BrokenProvider:
        name = "broken"

        def search(self, query, limit=20):
            raise RuntimeError("rate limited")

    pipeline = OpenXSimPipeline(tmp_path / "runs")
    with pytest.raises(RuntimeError, match="no asset candidate"):
        pipeline.acquire_asset("mug", AssetScout([BrokenProvider()]), asset_id="mug")

    failure = json.loads((tmp_path / "runs/assets/mug/search_failure.json").read_text())
    assert failure["status"] == "no_candidates"
    assert failure["provider_errors"] == [{"provider": "broken", "error": "rate limited"}]
    assert "search_evidence.json" in failure["recovery"]


def test_github_provider_discovers_repository_before_searching_asset_tree(monkeypatch) -> None:
    search_payload = {
        "items": [
            {
                "full_name": "acme/public-3d-assets",
                "default_branch": "main",
                "html_url": "https://github.com/acme/public-3d-assets",
                "description": "Public tabletop object meshes",
                "stargazers_count": 100,
                "license": {"spdx_id": "MIT"},
            }
        ]
    }
    tree_payload = {
        "truncated": False,
        "tree": [
            {"type": "blob", "path": "models/tabletop/ceramic_mug.obj"},
            {"type": "blob", "path": "README.md"},
        ],
    }
    requested: list[str] = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            self.close()

    def fake_urlopen(request, timeout):
        requested.append(request.full_url)
        payload = search_payload if "/search/repositories" in request.full_url else tree_payload
        return Response(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = GitHubRepositoryDiscoveryProvider(
        repository_query="public 3d assets",
        repository_limit=3,
    )

    candidates = provider.search("ceramic mug")

    assert len(requested) == 2
    assert "/search/repositories" in requested[0]
    assert "/repos/acme/public-3d-assets/git/trees/main" in requested[1]
    assert candidates[0].name == "ceramic_mug.obj"
    assert candidates[0].license == "MIT"
    assert candidates[0].metadata["repository_query"] == "public 3d assets"
