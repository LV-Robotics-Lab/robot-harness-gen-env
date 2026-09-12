import hashlib
import json
import os
import struct
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from self_improving.harness.experiment_assets import (
    AssetAcquisitionError,
    AssetRequest,
    acquire,
    revise,
)


def test_generated_block_has_portable_geometry_and_immutable_revision(tmp_path):
    asset = acquire(AssetRequest(source="generated", category="block", color="red"), tmp_path)
    assert asset.receipt["source_kind"] == "generated"
    assert asset.receipt["checks"]["physics"] == "not_run"
    assert (asset.root / "asset.urdf").is_file()
    before = (asset.root / "receipt.json").read_bytes()
    changed = revise(asset, {"mass_kg": 0.2}, tmp_path)
    assert changed.version_id != asset.version_id
    assert changed.receipt["parent_version"] == asset.version_id
    assert (asset.root / "receipt.json").read_bytes() == before
    for member in changed.receipt["members"]:
        assert (
            hashlib.sha256((changed.root / member["path"]).read_bytes()).hexdigest()
            == member["sha256"]
        )


def test_local_search_reuses_non_mouse_primitive_without_source_changes(tmp_path):
    generated = acquire(AssetRequest(source="generated", category="block"), tmp_path / "source")
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "asset_id": "block_one",
                        "category": "block",
                        "asset_path": str(generated.root),
                        "models": [
                            {"usable": True, "visual_path": str(generated.root / "visual.glb")}
                        ],
                    }
                ]
            }
        )
    )
    local = acquire(
        AssetRequest(source="local", category="block", catalog_path=catalog), tmp_path / "dest"
    )
    assert local.receipt["source_kind"] == "local"
    assert local.receipt["candidate_id"] == "catalog:block_one"
    assert local.receipt["checks"]["physics"] == "not_run"


@pytest.mark.skipif(
    os.environ.get("EXPERIMENT_ASSETS_WEB") != "1", reason="explicit live web check"
)
def test_web_download_search_license_and_geometry(tmp_path):
    asset = acquire(AssetRequest(source="web", category="Box", scale=0.08), tmp_path)
    assert asset.receipt["source_kind"] == "web"
    assert asset.receipt["license"] == "CC-BY-4.0"
    assert len(asset.receipt["source_ref"]) == 40
    assert asset.receipt["source_url"].endswith("/Box.glb")


@pytest.mark.parametrize("patch", [{"path": "../escape"}, {"mass_kg": float("nan")}, {"scale": 0}])
def test_invalid_patch_preserves_asset_and_records_failure(tmp_path, patch):
    asset = acquire(AssetRequest(source="generated", category="cube"), tmp_path)
    with pytest.raises(AssetAcquisitionError) as error:
        revise(asset, patch, tmp_path)
    assert (error.value.partial_root / "failure.json").is_file()


def test_unrecognized_generated_shape_fails_with_partial_artifacts(tmp_path):
    with pytest.raises(AssetAcquisitionError) as error:
        acquire(AssetRequest(source="generated", category="mouse"), tmp_path)
    assert "primitive" in str(error.value)
    assert (error.value.partial_root / "failure.json").is_file()


def test_symlinked_version_directory_is_not_trusted(tmp_path):
    asset = acquire(AssetRequest(source="generated", category="cube"), tmp_path)
    moved = tmp_path / "moved"
    asset.root.rename(moved)
    asset.root.symlink_to(moved, target_is_directory=True)
    with pytest.raises(AssetAcquisitionError):
        acquire(AssetRequest(source="generated", category="cube"), tmp_path)


def test_local_glb_external_resources_are_rejected_before_decode(tmp_path):
    chunk = json.dumps(
        {"asset": {"version": "2.0"}, "buffers": [{"uri": "../secret.bin"}]}
    ).encode()
    chunk += b" " * (-len(chunk) % 4)
    (tmp_path / "bad.glb").write_bytes(
        struct.pack("<4sII", b"glTF", 2, 20 + len(chunk))
        + struct.pack("<I4s", len(chunk), b"JSON")
        + chunk
    )
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "asset_id": "block",
                        "category": "block",
                        "asset_path": str(tmp_path),
                        "models": [{"usable": True, "visual_path": "bad.glb"}],
                    }
                ]
            }
        )
    )
    with pytest.raises(AssetAcquisitionError, match="external GLB"):
        acquire(
            AssetRequest(source="local", category="block", catalog_path=catalog), tmp_path / "out"
        )


def test_concurrent_identical_asset_registration_reuses_verified_winner(tmp_path, monkeypatch):
    acquire(AssetRequest(source="generated", category="block"), tmp_path / "warmup")
    barrier = Barrier(8)
    original = Path.rename

    def together(path, target):
        if path.name == "candidate":
            barrier.wait(timeout=10)
        return original(path, target)

    monkeypatch.setattr(Path, "rename", together)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(
                acquire, AssetRequest(source="generated", category="block"), tmp_path / "parallel"
            )
            for _ in range(8)
        ]
        assets = [future.result() for future in futures]
    assert len({asset.version_id for asset in assets}) == 1
    for asset in assets:
        assert json.loads((asset.root / "receipt.json").read_bytes()) == asset.receipt


def test_cousin_selection_is_explicitly_unsupported(tmp_path):
    with pytest.raises(AssetAcquisitionError) as error:
        acquire(AssetRequest(source="generated", category="block", selection="cousin"), tmp_path)
    assert error.value.code == "unsupported_selection"
    assert (
        json.loads((error.value.partial_root / "failure.json").read_bytes())["code"]
        == "unsupported_selection"
    )
    assert not (tmp_path / "versions").exists()


@pytest.mark.skipif(
    os.environ.get("EXPERIMENT_ASSETS_WEB") != "1", reason="explicit live web check"
)
def test_web_y_up_asymmetric_mesh_is_rotated_into_z_up(tmp_path):
    import trimesh

    asset = acquire(AssetRequest(source="web", category="Avocado"), tmp_path)
    source = next(tmp_path.glob("acquire-*/download/*/Avocado.glb"))
    old = trimesh.load(source, force="mesh").extents
    assert abs(float(old[1] - old[2])) > 0.001
    assert asset.receipt["dimensions_m"] == pytest.approx([old[0], old[2], old[1]])
    assert asset.receipt["source_up_axis"] == "Y"
    assert asset.receipt["normalized_up_axis"] == "Z"
