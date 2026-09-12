"""Real Yuxin provider seam; no module substitutes.

Run with deployment PYTHONPATH including
self_improving/asset_pipeline/active/shared/openxsim/source/agenticsim.
Only urllib network transport is replaced; no real web success is claimed here.
"""

import hashlib
import json
from types import SimpleNamespace

import pytest

from self_improving.harness.x2env.store import Store


def test_web_query_changes_only_original_engine_query_not_entity(tmp_path, monkeypatch):
    import urllib.request
    from io import BytesIO

    from self_improving.harness.x2env.adapters.yuxin import YuxinProviderAdapter

    calls = []

    def fetch(request, timeout):
        url = request if isinstance(request, str) else request.full_url
        calls.append(url)
        payload = (
            {"tree": [{"type": "blob", "path": "Models/Vessel/glTF-Binary/Vessel.glb"}]}
            if "git/trees" in url
            else {"legal": [{"spdx": "CC0-1.0", "owner": "Fixture creator", "text": "fixture"}]}
        )
        return BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fetch)
    store = Store(tmp_path / "state")
    provider = YuxinProviderAdapter(
        {
            "providers": {
                "github_tree": {
                    "enabled": True,
                    "repositories": [{"repository": "fixture/models", "branch": "a" * 40}],
                }
            }
        },
        store,
    )
    entity = SimpleNamespace(id="cup", category="mug")
    result = provider.search(entity, "web", query="vessel")
    assert result.status == "succeeded" and result.candidates[0].entity_id == "cup"
    assert entity.category == "mug"
    receipt = json.loads(store.read_artifact(result.receipt))
    assert receipt["category"] == "mug" and receipt["query"] == "vessel"
    assert len([url for url in calls if "git/trees" in url]) == 1
    with pytest.raises(ValueError):
        provider.search(entity, "local", query="vessel")


def test_registered_local_candidate_does_not_imply_permission(tmp_path):
    from self_improving.harness.x2env.adapters.yuxin import YuxinProviderAdapter

    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "asset_id": "mouse",
                        "category": "mouse",
                        "asset_path": str(tmp_path / "asset"),
                        "models": [{"model_id": 0, "usable": True}],
                    }
                ]
            }
        )
    )
    config = {"providers": {"robotwin_local": {"enabled": True, "catalog": str(catalog)}}}
    adapter = YuxinProviderAdapter(config, Store(tmp_path / "state"))
    result = adapter.search(SimpleNamespace(id="mouse", category="mouse", color="pink"), "local", 8)
    assert result.status == "blocked"
    assert result.error_code == "blocked_license"
    assert result.candidates[0].license.status == "unknown"
    fetched = adapter.fetch(result.candidates[0], tmp_path / "fetch")
    assert fetched.status == "blocked"
    assert not (tmp_path / "fetch").exists()


@pytest.mark.parametrize("fault", [None, "missing_member", "escape"])
def test_licensed_local_urdf_closure_is_copied_without_flattening(tmp_path, fault):
    from self_improving.harness.x2env.adapters.yuxin import YuxinProviderAdapter

    asset = tmp_path / "asset"
    asset.mkdir()
    (asset / "mesh.obj").write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    urdf = asset / "model.urdf"
    urdf.write_text(
        '<robot name="cabinet"><link name="body"><visual><geometry>'
        '<mesh filename="mesh.obj"/></geometry></visual></link></robot>'
    )
    files = [
        {
            "uri": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
        for path in [urdf, asset / "mesh.obj"]
    ]
    if fault == "missing_member":
        files = files[:1]
    elif fault == "escape":
        files[1]["uri"] = str(asset / ".." / "asset" / "mesh.obj")
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "model_id": 0,
                        "representations": [
                            {
                                "format": "urdf",
                                "uri": str(urdf),
                                "sha256": files[0]["sha256"],
                                "files": files,
                            }
                        ],
                        "source": {
                            "source_url": "https://example.org/original",
                            "license": {
                                "status": "declared",
                                "spdx": "CC-BY-4.0",
                                "attribution": "Fixture author",
                            },
                        },
                    }
                ]
            }
        )
    )
    index = tmp_path / "license-index.json"
    index.write_text(json.dumps({"cabinet": str(ledger)}))
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "asset_id": "cabinet",
                        "category": "cabinet",
                        "asset_path": str(asset),
                        "models": [{"model_id": 0, "usable": True, "urdf_path": str(urdf)}],
                    }
                ]
            }
        )
    )
    adapter = YuxinProviderAdapter(
        {"providers": {"robotwin_local": {"enabled": True, "catalog": str(catalog)}}},
        Store(tmp_path / "state"),
        index,
    )
    found = adapter.search(SimpleNamespace(id="cabinet", category="cabinet"), "local")
    if fault:
        assert found.status == "failed"
        return
    assert found.status == "succeeded"
    fetched = adapter.fetch(found.candidates[0], tmp_path / "fetched")
    assert fetched.status == "succeeded"
    assert __import__("pathlib").Path(fetched.source_path).read_bytes() == urdf.read_bytes()
    assert (tmp_path / "fetched" / "mesh.obj").read_bytes() == (asset / "mesh.obj").read_bytes()
    (asset / "mesh.obj").write_text("changed")
    failed = adapter.fetch(found.candidates[0], tmp_path / "changed")
    assert failed.status == "failed"
    assert failed.error_code == "asset_integrity_mismatch"


@pytest.mark.parametrize(
    "license_id,download_error", [("CC0-1.0", False), ("unknown", False), ("CC0-1.0", True)]
)
def test_web_uses_original_search_download_and_per_model_license(
    tmp_path, monkeypatch, license_id, download_error
):
    import urllib.request
    from email.message import Message
    from io import BytesIO

    import trimesh

    from self_improving.harness.x2env.adapters.yuxin import YuxinProviderAdapter

    mesh = trimesh.creation.box().export(file_type="glb")

    class Response(BytesIO):
        headers = Message()

    def urlopen(request, timeout):
        url = request if isinstance(request, str) else request.full_url
        if "git/trees" in url:
            raw = json.dumps(
                {"tree": [{"type": "blob", "path": "Models/Box/glTF-Binary/Box.glb"}]}
            ).encode()
        elif url.endswith("metadata.json"):
            raw = json.dumps(
                {"legal": [{"spdx": license_id, "owner": "Fixture creator", "text": "fixture"}]}
            ).encode()
        else:
            if download_error:
                raise OSError("network unavailable")
            raw = mesh
        return Response(raw)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    config = {
        "providers": {
            "github_tree": {
                "enabled": True,
                "repositories": [{"repository": "fixture/models", "branch": "a" * 40}],
            }
        }
    }
    adapter = YuxinProviderAdapter(config, Store(tmp_path / "state"))
    found = adapter.search(SimpleNamespace(id="box", category="box"), "web")
    if license_id == "unknown":
        assert found.status == "blocked"
        assert found.error_code == "blocked_license"
        return
    assert found.status == "succeeded"
    assert found.candidates[0].license.spdx == "CC0-1.0"
    fetched = adapter.fetch(found.candidates[0], tmp_path / "fetched")
    if download_error:
        assert fetched.status == "failed"
        assert fetched.error_code is not None
        return
    assert fetched.status == "succeeded"
    manifest = json.loads(__import__("pathlib").Path(fetched.staging_manifest).read_text())
    assert manifest[0]["up_axis"] == "Y"
    assert manifest[0]["source_provider"] == "github_tree"
