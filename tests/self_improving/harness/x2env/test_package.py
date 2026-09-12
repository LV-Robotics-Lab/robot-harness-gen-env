"""Portable development packages. Test doubles are not live qualification evidence."""

import hashlib
import json

import pytest

from self_improving.harness.x2env.package import build_package
from self_improving.harness.x2env.package_loader import run_package, verify_package


def test_package_rejects_member_escape_before_read(tmp_path):
    manifest = {
        "schema_version": "x2env.development_package.v1",
        "members": [{"path": "../outside", "sha256": "a" * 64, "size_bytes": 1}],
        "scene": "scene.json",
        "entrypoint": "package_loader.py",
        "child": "genesis_child.py",
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="unsafe"):
        verify_package(tmp_path)


def fixture_compiled(tmp_path):
    from self_improving.harness.x2env.assets import AssetRegistry
    from self_improving.harness.x2env.compile import (
        CompiledScene,
        ResolvedAssetSet,
        StructuralPolicy,
    )
    from self_improving.harness.x2env.genesis_runtime import RuntimeEntity, RuntimeScene
    from self_improving.harness.x2env.store import Store

    store = Store(tmp_path / "state")
    text = store.write_artifact(b"unit support scene", "text/plain")
    ir = store.write_artifact(
        json.dumps({"input_sha256": text.sha256}).encode(), "application/json"
    )
    scene = RuntimeScene(
        scene_ir_sha256=ir.sha256,
        seed=17,
        entities=(
            RuntimeEntity(
                id="table",
                kind="structural_box",
                category="table",
                position_m=(0.0, 0.0, 0.35),
                orientation_wxyz=(1.0, 0.0, 0.0, 0.0),
                size_m=(1.0, 1.0, 0.1),
                friction=0.6,
            ),
        ),
    )
    receipt = store.write_artifact(
        json.dumps({"fixture_only": True, "scene_ir": ir.model_dump()}).encode(), "application/json"
    )
    compiled = CompiledScene(
        runtime_scene=scene,
        scene_ir=ir,
        resolved_assets=ResolvedAssetSet(scene_ir=ir, assets=()),
        policy=StructuralPolicy(thickness_m=0.1, surface_height_m=0.4, friction=0.6),
        defaults_applied=(),
        receipt=receipt,
    )
    return compiled, store, AssetRegistry(store), text


def test_review_package_is_self_contained_and_never_promotes_bare_pass_fields(tmp_path):
    compiled, store, registry, text = fixture_compiled(tmp_path)
    fake = store.write_artifact(
        json.dumps({"physical_status": "passed", "visual_status": "passed"}).encode(),
        "application/json",
    )
    result = build_package(
        compiled,
        registry=registry,
        store=store,
        input_refs={"text": text},
        replay_refs={},
        assessment_ref=fake,
        output=tmp_path / "package",
    )
    assert result["status"] == "development_review"
    assert result["sim_ready"] is False
    assert verify_package(tmp_path / "package") == result
    assert (tmp_path / "package/genesis_child.py").is_file()
    assert (tmp_path / "package/package_loader.py").is_file()
    assert json.loads((tmp_path / "package/scene.json").read_bytes())["seed"] == 17
    for entry in result["artifact_index"]:
        data = (tmp_path / "package" / entry["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["ref"]["sha256"]


def test_rehashed_scene_cannot_add_absolute_asset_locator(tmp_path):
    compiled, store, registry, text = fixture_compiled(tmp_path)
    package = tmp_path / "package"
    manifest = build_package(
        compiled,
        registry=registry,
        store=store,
        input_refs={"text": text},
        replay_refs={},
        assessment_ref=None,
        output=package,
    )
    scene = json.loads((package / "scene.json").read_bytes())
    scene["entities"].append(
        {
            "id": "item",
            "kind": "rigid",
            "category": "box",
            "position_m": [0.0, 0.0, 0.4],
            "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
            "urdf_path": "/original/asset.urdf",
            "physics_path": "physics.json",
            "version_sha256": "a" * 64,
        }
    )
    raw = json.dumps(scene).encode()
    (package / "scene.json").write_bytes(raw)
    for m in manifest["members"]:
        if m["path"] == "scene.json":
            m.update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
    (package / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="unsafe|unlisted"):
        verify_package(package)


def test_loader_rejects_broad_runtime_roots_and_retains_failure(tmp_path):
    compiled, store, registry, text = fixture_compiled(tmp_path)
    package = tmp_path / "package"
    build_package(
        compiled,
        registry=registry,
        store=store,
        input_refs={"text": text},
        replay_refs={},
        assessment_ref=None,
        output=package,
    )
    roots = {k: "/" for k in ["interpreter", "stdlib", "distributions", "native", "genesis"]}
    result = run_package(package, runtime_roots=roots, output=tmp_path / "execution")
    assert result["status"] == "failed"
    assert "root filesystem" in result["reason"]
    assert (tmp_path / "execution/result.json").is_file()
    assert result["simulator_executed"] is False


def test_missing_nested_evidence_fails_without_promoting_partial_export(tmp_path):
    from self_improving.harness.x2env.contracts import ArtifactRef

    compiled, store, registry, text = fixture_compiled(tmp_path)
    missing = ArtifactRef(sha256="a" * 64, size_bytes=3, media_type="text/plain")
    outer = store.write_artifact(
        json.dumps({"actual_source": missing.model_dump()}).encode(), "application/json"
    )
    with pytest.raises((FileNotFoundError, ValueError)):
        build_package(
            compiled,
            registry=registry,
            store=store,
            input_refs={"text": text},
            replay_refs={},
            assessment_ref=None,
            evidence_refs={"source": outer},
            output=tmp_path / "package",
        )
    failure = json.loads((tmp_path / "package/export-failure.json").read_bytes())
    assert failure["status"] == "failed"
    assert not (tmp_path / "package/manifest.json").exists()


def test_nested_refs_export_but_arbitrary_hash_strings_are_not_followed(tmp_path):
    compiled, store, registry, text = fixture_compiled(tmp_path)
    inner = store.write_artifact(b"actual nested source bytes", "text/plain")
    outer = store.write_artifact(
        json.dumps({"nested": inner.model_dump(), "audit_locator": "f" * 64}).encode(),
        "application/json",
    )
    result = build_package(
        compiled,
        registry=registry,
        store=store,
        input_refs={"text": text},
        replay_refs={},
        assessment_ref=None,
        evidence_refs={"source": outer},
        output=tmp_path / "package",
    )
    assert inner.sha256 in {entry["ref"]["sha256"] for entry in result["artifact_index"]}
    (tmp_path / "package/genesis_child.py").write_bytes(b"changed")
    with pytest.raises(ValueError, match="corrupt"):
        verify_package(tmp_path / "package")


def test_package_symlink_member_and_reused_output_are_rejected(tmp_path):
    compiled, store, registry, text = fixture_compiled(tmp_path)
    output = tmp_path / "package"
    kwargs = dict(
        registry=registry,
        store=store,
        input_refs={"text": text},
        replay_refs={},
        assessment_ref=None,
        output=output,
    )
    build_package(compiled, **kwargs)
    original = (output / "scene.json").read_bytes()
    with pytest.raises(FileExistsError):
        build_package(compiled, **kwargs)
    assert (output / "scene.json").read_bytes() == original
    outside = tmp_path / "outside.json"
    outside.write_bytes(original)
    (output / "scene.json").unlink()
    (output / "scene.json").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        verify_package(output)


def test_portable_cli_retains_missing_dependency_failure(tmp_path, monkeypatch, capsys):
    from self_improving.harness.x2env import package_loader

    compiled, store, registry, text = fixture_compiled(tmp_path)
    package = tmp_path / "package"
    build_package(
        compiled,
        registry=registry,
        store=store,
        input_refs={"text": text},
        replay_refs={},
        assessment_ref=None,
        output=package,
    )
    roots = tmp_path / "runtime_roots.json"
    roots.write_text("{}")
    monkeypatch.setattr(package_loader, "__file__", str(package / "package_loader.py"))
    assert package_loader.main(["--runtime", str(roots), "--output", str(tmp_path / "run")]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["error_code"] == "missing_runtime_dependency"
    assert json.loads((tmp_path / "run/result.json").read_text()) == result


def test_allow_deny_overlap_never_starts_process(tmp_path, monkeypatch):
    from self_improving.harness.x2env import package_loader

    compiled, store, registry, text = fixture_compiled(tmp_path)
    package = tmp_path / "package"
    build_package(
        compiled,
        registry=registry,
        store=store,
        input_refs={"text": text},
        replay_refs={},
        assessment_ref=None,
        output=package,
    )

    def prohibited(*args, **kwargs):
        raise AssertionError("process must not start")

    monkeypatch.setattr(package_loader.subprocess, "Popen", prohibited)
    roots = {
        k: str(tmp_path) for k in ["interpreter", "stdlib", "distributions", "native", "genesis"]
    }
    result = run_package(
        package, runtime_roots=roots, output=tmp_path / "run", denied_roots=[package]
    )
    assert result["status"] == "failed"
    assert "overlaps denied" in result["reason"]
