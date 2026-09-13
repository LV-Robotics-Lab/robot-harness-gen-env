"""Preview scope tests use an explicit external-runtime double, not real Genesis evidence."""

import hashlib
import json

import pytest
from PIL import Image

from tests.self_improving.harness.x2env.test_asset_revision import fixture


def multi_collision_version(tmp_path, fault=None):
    """New registered fixture version; no real decomposition/Genesis claim."""
    import xml.etree.ElementTree as ET

    store, registry, parent = fixture(tmp_path)
    contents = {item.path: store.read_artifact(item.artifact) for item in parent.files}
    document = ET.fromstring(contents["asset.urdf"])
    body = document.find("link")
    body.remove(body.find("collision"))
    raw_collision = contents.pop("collision.obj")
    for index in range(2):
        name = f"collision/part-{index}.obj"
        contents[name] = raw_collision
        mesh = ET.SubElement(ET.SubElement(ET.SubElement(body, "collision"), "geometry"), "mesh")
        mesh.set("filename", name)
    if fault == "joint":
        ET.SubElement(document, "joint", name="not_supported")
    if fault == "extra":
        contents["unused.txt"] = b"not part of URDF closure"
    if fault == "material":
        contents["collision/part-0.obj"] += b"\nmtllib material.mtl\n"
        contents["collision/material.mtl"] = b"newmtl fixture\n"
    if fault == "material_inline":
        contents["collision/part-0.obj"] += b"\nusemtl unsupported\n"
    if fault == "missing_visual":
        body.remove(body.find("visual"))
    if fault == "physics":
        contents["physics.json"] = b'{"mass_kg":-1,"friction":0.5}'
    if fault == "mixed_geometry":
        ET.SubElement(body.find("collision/geometry"), "box", size="1 1 1")
    contents["asset.urdf"] = ET.tostring(document)
    root = tmp_path / "child"
    root.mkdir()
    for name, data in contents.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    report = json.loads(store.read_artifact(parent.normalization_report))
    report["files"] = [
        {"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
        for name, data in contents.items()
    ]
    child = registry.register(
        parent.asset_id,
        parent.category,
        root,
        parent.entrypoint,
        files=tuple(contents),
        normalization_report=store.write_artifact(json.dumps(report).encode(), "application/json"),
        license=parent.license,
        source=parent.source,
        receipt=parent.receipt,
        parent_version=parent.version_sha256,
    )
    return store, registry, parent, child


def test_registered_multi_collision_closure_reaches_preview_runner(tmp_path):
    from self_improving.harness.x2env.asset_preview import AssetPreviewRenderer

    store, registry, parent, child = multi_collision_version(tmp_path)

    def runner(scene, **kwargs):
        assert scene.entities[0].version_sha256 == child.version_sha256
        assert {m.path for m in scene.members} == {m.path for m in child.files}
        assert (kwargs["package_root"] / "collision/part-0.obj").is_file()
        output = kwargs["output_dir"]
        output.mkdir()
        Image.new("RGB", (8, 8), "red").save(output / "fixture.png")
        return {
            "status": "passed",
            "simulator_executed": True,
            "fixture_only": True,
            "media": {
                "frames": [
                    {
                        "path": "fixture.png",
                        "png_sha256": hashlib.sha256(
                            (output / "fixture.png").read_bytes()
                        ).hexdigest(),
                    }
                ]
            },
        }

    proof = AssetPreviewRenderer(store, {}, tmp_path / "preview", 1, runner=runner).render(child)
    assert proof.status == "passed", proof.error_code
    assert registry.inspect(parent.version_sha256) == parent
    assert json.loads(store.read_artifact(proof.receipt))["physical_profile"] == "not_run"


@pytest.mark.parametrize(
    "fault,error",
    [
        ("joint", "unsupported_preview_articulation"),
        ("extra", "unsupported_preview_asset_closure"),
        ("material", "unsupported_preview_asset_closure"),
        ("missing_visual", "unsupported_preview_asset_closure"),
        ("material_inline", "unsupported OBJ material closure"),
        ("physics", "invalid supplied physics"),
        ("mixed_geometry", "unsupported_preview_asset_closure"),
    ],
)
def test_registered_unsupported_closure_never_reaches_runtime(tmp_path, fault, error):
    from self_improving.harness.x2env.asset_preview import AssetPreviewRenderer

    store, _, _, child = multi_collision_version(tmp_path, fault)

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid closure reached runtime")

    proof = AssetPreviewRenderer(store, {}, tmp_path / "preview", 1, runner=forbidden).render(child)
    assert proof.status == "failed" and proof.image is None
    assert error in proof.error_code


def test_symbolic_preview_root_is_rejected_before_writing(tmp_path):
    from self_improving.harness.x2env.asset_preview import AssetPreviewRenderer

    store, _, version = fixture(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "symbolic"
    link.symlink_to(outside, target_is_directory=True)

    def forbidden(*args, **kwargs):
        raise AssertionError("symlink reached runtime")

    with pytest.raises(ValueError, match="unsafe_preview_root"):
        AssetPreviewRenderer(store, {}, link, 1, runner=forbidden).render(version)
    assert list(outside.iterdir()) == []


def test_explicit_git_mismatch_retains_failure_and_never_runs_preview(tmp_path):
    from pathlib import Path

    from self_improving.harness.x2env.asset_preview import AssetPreviewRenderer
    from self_improving.harness.x2env.source_identity import GitSourcePolicy

    store, _, version = fixture(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("source pin failure reached runtime")

    proof = AssetPreviewRenderer(
        store,
        {},
        tmp_path / "preview",
        1,
        runner=forbidden,
        source_identity_policy=GitSourcePolicy(
            root=str(Path(__file__).resolve().parents[4]), expected_commit="0" * 40
        ),
    ).render(version)
    assert proof.status == "failed" and proof.image is None
    assert proof.error_code == "source_git_commit_mismatch"
    receipt = json.loads(store.read_artifact(proof.receipt))
    assert not receipt["package"] and not receipt["outputs"]


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


def test_preview_respects_caller_remaining_budget(tmp_path):
    from self_improving.harness.x2env.asset_preview import AssetPreviewRenderer

    store, _, version = fixture(tmp_path)

    def runner(scene, **kwargs):
        assert 0 < kwargs["timeout_seconds"] <= 2
        kwargs["output_dir"].mkdir()
        return {"status": "failed", "error_code": "intentional_double"}

    result = AssetPreviewRenderer(store, {}, tmp_path / "preview", 1, runner=runner).render(
        version, timeout=2
    )
    assert result.status == "failed"
