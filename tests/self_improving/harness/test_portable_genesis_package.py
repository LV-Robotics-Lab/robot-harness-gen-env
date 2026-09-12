import hashlib
import json

import pytest

from self_improving.harness.portable_genesis_package import (
    export_package,
    load_package,
    run_package,
)


def package(tmp_path, name="scene.json", data=b"{}"):
    root = tmp_path / "package"
    root.mkdir()
    (root / "scene.json").write_bytes(data)
    (root / "environment.json").write_text(
        json.dumps(
            {
                "schema_version": "harness.portable_genesis_package.v1",
                "members": [
                    {"path": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
                ],
                "scene": "scene.json",
            }
        )
    )
    return root


def test_portable_member_cannot_escape_package(tmp_path):
    root = package(tmp_path, "../scene.json")
    with pytest.raises(ValueError, match="path"):
        load_package(root)


def test_corrupt_member_is_rejected_before_loading(tmp_path):
    root = package(tmp_path)
    (root / "scene.json").write_bytes(b"changed")
    with pytest.raises(ValueError, match="member"):
        load_package(root)


def test_missing_runtime_is_structured_failure_without_simulation(tmp_path):
    root = package(tmp_path)
    result = run_package(root, runtime_roots={}, output=tmp_path / "run", denied_roots=[])
    assert result["status"] == "failed"
    assert result["error_code"] == "missing_runtime_dependency"
    assert result["simulator_executed"] is False


def test_custom_profile_is_not_accepted_as_fixed_physics(tmp_path):
    root = package(tmp_path)
    result = run_package(root, runtime_roots={}, output=tmp_path / "run", profile="easy")
    assert result["error_code"] == "unsupported_replay_profile"
    assert result["simulator_executed"] is False


def test_unlisted_loader_entrypoint_is_rejected(tmp_path):
    root = package(tmp_path)
    manifest = json.loads((root / "environment.json").read_text())
    manifest["entrypoint"] = "not-listed.py"
    (root / "environment.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="entrypoint"):
        load_package(root)


def test_symlink_member_is_rejected(tmp_path):
    root = package(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"{}")
    (root / "scene.json").unlink()
    (root / "scene.json").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        load_package(root)


def test_export_can_be_copied_and_loaded_without_source_cas(tmp_path):
    import shutil

    from self_improving.harness.artifacts import LocalArtifactStore

    store = LocalArtifactStore(tmp_path / "cas")

    def put(data, name):
        source = tmp_path / name
        source.write_bytes(data)
        return store.put_file(source, name=name, media_type="application/json", schema_version=None)

    urdf = put(b'<robot name="box"><link name="base"/></robot>', "object.urdf")
    physics = put(b'{"friction":0.5}', "physics.json")
    representation = put(
        json.dumps(
            {
                "root_logical_path": "urdf/object.urdf",
                "members": [
                    {"logical_path": "urdf/object.urdf", "artifact": urdf.model_dump(mode="json")},
                    {"logical_path": "physics.json", "artifact": physics.model_dump(mode="json")},
                ],
            }
        ).encode(),
        "representation.json",
    )
    scene = put(
        json.dumps(
            {
                "schema_version": "harness.genesis_scene_spec.v1",
                "representation": representation.model_dump(mode="json"),
            }
        ).encode(),
        "scene-source.json",
    )
    exported = export_package(store, scene_ref=scene, output=tmp_path / "built")
    shutil.copytree(tmp_path / "built", tmp_path / "copied")
    shutil.rmtree(tmp_path / "cas")
    assert load_package(tmp_path / "copied") == exported
    assert (tmp_path / "copied" / exported["urdf"]).read_bytes().startswith(b"<robot")
    assert exported["checks"]["physical_profile"] == "not_run"


def test_loader_rejects_urdf_absolute_resource_reference(tmp_path):
    root = package(tmp_path, data=b'<robot><mesh filename="/outside.obj"/></robot>')
    (root / "scene.json").rename(root / "object.urdf")
    manifest = json.loads((root / "environment.json").read_text())
    manifest["scene"] = "object.urdf"
    manifest["members"][0]["path"] = "object.urdf"
    (root / "environment.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="resource"):
        load_package(root)
