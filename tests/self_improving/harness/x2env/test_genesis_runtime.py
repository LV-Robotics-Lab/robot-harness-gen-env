"""Public runtime seam; offline checks do not claim simulator execution."""

import hashlib
import json
import struct
import os

import pytest

from self_improving.harness.x2env.genesis_runtime import RuntimeScene, run_scene
from self_improving.harness.x2env.genesis_child import audit_geometry


def scene():
    return {
        "schema_version": "x2env.runtime_scene.v1",
        "seed": 0,
        "scene_ir_sha256": "a" * 64,
        "entities": [
            {
                "id": "table",
                "kind": "structural_box",
                "category": "table",
                "position_m": [0.0, 0.0, 0.35],
                "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                "size_m": [1.0, 1.0, 0.1],
                "friction": 0.6,
            }
        ],
        "relations": [],
        "members": [],
    }


def test_runtime_rejects_unresolved_pose_and_foreground_primitive():
    payload = scene()
    payload["entities"][0]["position_m"][2] = None
    with pytest.raises(ValueError):
        RuntimeScene.model_validate(payload)
    payload = scene()
    payload["entities"][0]["category"] = "mouse"
    with pytest.raises(ValueError):
        RuntimeScene.model_validate(payload)


def test_missing_runtime_retains_failure_without_execution(tmp_path):
    result = run_scene(
        scene(), package_root=tmp_path, runtime_roots={}, output_dir=tmp_path / "execution"
    )
    assert result["status"] == "failed"
    assert result["error_code"] == "missing_runtime_dependency"
    assert result["simulator_executed"] is False
    assert (tmp_path / "execution/result.json").is_file()


@pytest.mark.parametrize("path", ["../outside", "/tmp/outside", "asset\\outside"])
def test_manifest_paths_cannot_escape_package(tmp_path, path):
    payload = scene()
    payload["members"] = [{"path": path, "sha256": "a" * 64, "size_bytes": 1}]
    roots = {
        k: str(tmp_path) for k in ["interpreter", "stdlib", "distributions", "native", "genesis"]
    }
    result = run_scene(
        payload, package_root=tmp_path, runtime_roots=roots, output_dir=tmp_path / "execution"
    )
    assert result["status"] == "failed"
    assert "unsafe member path" in result["reason"]


def test_runtime_does_not_relax_profiles_or_allow_denied_root_overlap(tmp_path):
    roots = {
        k: str(tmp_path) for k in ["interpreter", "stdlib", "distributions", "native", "genesis"]
    }
    result = run_scene(
        scene(),
        package_root=tmp_path,
        runtime_roots=roots,
        output_dir=tmp_path / "bad-profile",
        profile="one_step_pass",
    )
    assert result["error_code"] == "unsupported_profile"
    result = run_scene(
        scene(),
        package_root=tmp_path,
        runtime_roots=roots,
        output_dir=tmp_path / "overlap",
        denied_roots=[str(tmp_path)],
    )
    assert "overlaps denied root" in result["reason"]


def test_corrupt_or_unbound_assets_never_launch(tmp_path):
    source = tmp_path / "mesh.obj"
    source.write_text("v 0 0 0\n")
    payload = scene()
    payload["members"] = [
        {"path": "mesh.obj", "sha256": "a" * 64, "size_bytes": source.stat().st_size}
    ]
    result = run_scene(
        payload, package_root=tmp_path, runtime_roots={}, output_dir=tmp_path / "bad"
    )
    assert "corrupt runtime member" in result["reason"]
    payload = scene()
    payload["entities"].append(
        {
            "id": "item",
            "kind": "rigid",
            "category": "cup",
            "position_m": [0.0, 0.0, 0.4],
            "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
            "urdf_path": "unlisted.urdf",
            "physics_path": "unlisted.json",
            "version_sha256": "b" * 64,
        }
    )
    result = run_scene(
        payload, package_root=tmp_path, runtime_roots={}, output_dir=tmp_path / "unbound"
    )
    assert "unbound URDF/physics member" in result["reason"]


def test_inside_and_jointed_objects_are_not_flattened(tmp_path):
    payload = scene()
    payload["relations"] = [{"source": "a", "target": "table", "relation": "inside"}]
    with pytest.raises(ValueError):
        RuntimeScene.model_validate(payload)
    payload = scene()
    (tmp_path / "asset.urdf").write_text(
        '<robot><link name="body"/><link name="door"/><joint name="hinge" type="revolute"/></robot>'
    )
    (tmp_path / "physics.json").write_text(json.dumps({"mass_kg": 1.0, "friction": 0.4}))
    payload["members"] = [
        {
            "path": p.name,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "size_bytes": p.stat().st_size,
        }
        for p in tmp_path.iterdir()
    ]
    payload["entities"].append(
        {
            "id": "item",
            "kind": "rigid",
            "category": "cabinet",
            "position_m": [0.0, 0.0, 0.4],
            "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
            "urdf_path": "asset.urdf",
            "physics_path": "physics.json",
            "version_sha256": "b" * 64,
        }
    )
    result = run_scene(
        payload, package_root=tmp_path, runtime_roots={}, output_dir=tmp_path / "out"
    )
    assert "unsupported articulation" in result["reason"]


def test_resolved_numeric_fields_are_not_coerced():
    payload = scene()
    payload["entities"][0]["position_m"][0] = "0.0"
    with pytest.raises(ValueError):
        RuntimeScene.model_validate(payload)


def test_mesh_cannot_read_unbound_external_resources(tmp_path):
    document = json.dumps(
        {"asset": {"version": "2.0"}, "buffers": [{"uri": "outside.bin"}]}
    ).encode()
    document += b" " * (-len(document) % 4)
    raw = (
        b"glTF" + struct.pack("<IIII", 2, 20 + len(document), len(document), 0x4E4F534A) + document
    )
    (tmp_path / "visual.glb").write_bytes(raw)
    payload = scene()
    payload["members"] = [
        {"path": "visual.glb", "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
    ]
    result = run_scene(
        payload, package_root=tmp_path, runtime_roots={}, output_dir=tmp_path / "out"
    )
    assert "external GLB resource" in result["reason"]


def test_runtime_requires_and_preserves_explicit_bounded_seed():
    payload = scene()
    payload["seed"] = 731
    assert RuntimeScene.model_validate(payload).seed == 731
    for value in [None, -1, 2147483648, True, "42"]:
        payload["seed"] = value
        with pytest.raises(ValueError):
            RuntimeScene.model_validate(payload)
    del payload["seed"]
    with pytest.raises(ValueError):
        RuntimeScene.model_validate(payload)


def test_timeout_interrupts_owned_process_group_and_retains_lifecycle(tmp_path):
    roots = {
        key: str(tmp_path / key)
        for key in ["interpreter", "stdlib", "distributions", "native", "genesis"]
    }
    for directory in roots.values():
        from pathlib import Path

        Path(directory).mkdir()
    loader = tmp_path / "native/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"
    loader.parent.mkdir(parents=True)
    loader.write_text(
        "#!/usr/bin/python3 -E\nimport os,signal,time,sys\n"
        "child=os.fork()\n"
        "def stop(sig,frame):\n"
        " if child: os.waitpid(child,0)\n"
        " raise SystemExit(2)\n"
        "signal.signal(signal.SIGINT,stop)\n"
        'if child: open("probe-pids.json","w").write(str(os.getpid())+" "+str(child))\n'
        "while True: time.sleep(.05)\n"
    )
    loader.chmod(0o700)
    package = tmp_path / "package"
    package.mkdir()
    result = run_scene(
        scene(),
        package_root=package,
        runtime_roots=roots,
        output_dir=tmp_path / "out",
        timeout_seconds=0.3,
    )
    assert result["status"] == "cancelled"
    assert result["error_code"] == "timed_out"
    lifecycle = json.loads((tmp_path / "out/process.json").read_bytes())
    assert lifecycle["pid"] == lifecycle["pgid"]
    assert lifecycle["signals"][0]["signal"] == "SIGINT"
    for pid in (tmp_path / "out/probe-pids.json").read_text().split():
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid), 0)


def test_loaded_geometry_must_match_actual_world_vertices(tmp_path):
    import numpy as np
    import trimesh

    mesh = trimesh.creation.box(extents=[0.1, 0.2, 0.3])
    mesh.export(tmp_path / "shape.obj")
    urdf = tmp_path / "asset.urdf"
    urdf.write_text(
        '<robot><link name="body"><visual><geometry><mesh filename="shape.obj"/>'
        '</geometry></visual><collision><geometry><mesh filename="shape.obj"/></geometry>'
        "</collision></link></robot>"
    )
    position = np.array([0.2, 0.3, 0.4])
    actual = mesh.vertices + position
    result = audit_geometry(actual, actual, position, [1.0, 0.0, 0.0, 0.0], urdf)
    np.testing.assert_allclose(result["local_visual_vertices_m"], mesh.vertices)
    assert result["visual_error_m"] < 1e-8
    bad = actual.copy()
    bad[0, 0] += 0.01
    with pytest.raises(ValueError, match="loaded geometry differs"):
        audit_geometry(bad, actual, position, [1.0, 0.0, 0.0, 0.0], urdf)
