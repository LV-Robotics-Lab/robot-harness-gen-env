"""Preview scope tests use an explicit external-runtime double, not real Genesis evidence."""

import hashlib
import json

import pytest
from PIL import Image

from tests.self_improving.harness.x2env.test_asset_revision import fixture


def test_preview_binds_actual_version_package_and_runtime_image(tmp_path):
    from self_improving.harness.x2env.asset_preview import AssetPreviewRenderer

    store, _, version = fixture(tmp_path)

    def runner(scene, **kwargs):
        assert kwargs["profile"] == "load_step_smoke"
        assert scene.entities[0].version_sha256 == version.version_sha256
        assert len(scene.members) == 4
        for member in scene.members:
            data = (kwargs["package_root"] / member.path).read_bytes()
            assert hashlib.sha256(data).hexdigest() == member.sha256
        out = kwargs["output_dir"]
        out.mkdir()
        Image.new("RGB", (16, 16), "red").save(out / "actual.png")
        return {
            "status": "passed",
            "simulator_executed": True,
            "media": {
                "frames": [
                    {
                        "path": "actual.png",
                        "png_sha256": hashlib.sha256((out / "actual.png").read_bytes()).hexdigest(),
                    }
                ]
            },
        }

    proof = AssetPreviewRenderer(store, {}, tmp_path / "preview", 1, runner=runner).render(version)
    assert proof.status == "passed" and proof.version_sha256 == version.version_sha256
    assert store.read_artifact(proof.image).startswith(b"\x89PNG")
    receipt = json.loads(store.read_artifact(proof.receipt))
    assert receipt["scope"] == "asset_preview_scope"
    assert receipt["physical_profile"] == "not_run"
    assert receipt["outputs"]["actual.png"] == proof.image.model_dump(mode="json")


def test_failed_runtime_retains_partial_artifact_without_preview_success(tmp_path):
    from self_improving.harness.x2env.asset_preview import AssetPreviewRenderer

    store, _, version = fixture(tmp_path)

    def runner(scene, **kwargs):
        out = kwargs["output_dir"]
        out.mkdir()
        (out / "stderr.log").write_text("intentional runtime double failure")
        return {"status": "failed", "error_code": "runtime_execution_failed"}

    proof = AssetPreviewRenderer(store, {}, tmp_path / "preview", 1, runner=runner).render(version)
    assert proof.status == "failed" and proof.image is None
    assert "stderr.log" in json.loads(store.read_artifact(proof.receipt))["outputs"]


@pytest.mark.parametrize("fault", ["hash", "escape", "corrupt_parent"])
def test_unbound_preview_or_corrupt_parent_never_yields_image(tmp_path, fault):
    from self_improving.harness.x2env.asset_preview import AssetPreviewRenderer

    store, _, version = fixture(tmp_path)
    if fault == "corrupt_parent":
        version = version.model_copy(update={"category": "forged"})

    def runner(scene, **kwargs):
        assert fault != "corrupt_parent", "registry mismatch must fail before runtime"
        out = kwargs["output_dir"]
        out.mkdir()
        Image.new("RGB", (4, 4)).save(out / "real.png")
        return {
            "status": "passed",
            "simulator_executed": True,
            "media": {
                "frames": [
                    {
                        "path": "../escape.png" if fault == "escape" else "real.png",
                        "png_sha256": "0" * 64,
                    }
                ]
            },
        }

    proof = AssetPreviewRenderer(store, {}, tmp_path / "preview", 1, runner=runner).render(version)
    assert proof.status == "failed" and proof.image is None
