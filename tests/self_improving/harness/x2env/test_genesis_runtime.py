"""Public runtime seam; offline checks do not claim simulator execution."""

import hashlib
import json
import os
import struct

import pytest

from self_improving.harness.x2env.genesis_child import audit_geometry, audit_loaded_geometry
from self_improving.harness.x2env.genesis_runtime import RuntimeScene, run_scene


@pytest.mark.parametrize(
    "fault", [None, "surface", "selection", "asset_record", "position", "intent_dimensions"]
)
def test_dynamic_support_preflight_recomputes_copied_geometry_before_runtime(tmp_path, fault):
    import shutil

    from self_improving.harness.x2env.compile import StructuralPolicy, compile_scene
    from tests.self_improving.harness.x2env.test_compile import dynamic_stack_inputs

    source = tmp_path / "source"
    source.mkdir()
    store, registry, versions, ref, assets = dynamic_stack_inputs(source)
    compiled = compile_scene(
        ref,
        assets,
        registry=registry,
        store=store,
        output_root=source / "compiled",
        policy=StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5),
        seed=11,
    )
    package = tmp_path / "copy"
    shutil.copytree(source / "compiled", package)
    # Deliberately remove the original state/Registry and build assets from reach.
    source.rename(tmp_path / "original-not-used")
    payload = compiled.runtime_scene.model_dump(mode="json")
    edge = next(b for b in payload["support_bindings"] if b["source_id"] == "box")
    if fault in {"surface", "selection", "asset_record"}:
        name = edge[
            {
                "surface": "surface_path",
                "selection": "selection_receipt_path",
                "asset_record": "target_asset_record_path",
            }[fault]
        ]
        doc = json.loads((package / name).read_bytes())
        if fault == "surface":
            doc["plane_z_m"] += 0.1
        elif fault == "selection":
            doc["candidate_surface_sha256s"] = []
        else:
            doc["category"] = "forged"
        raw = json.dumps(doc).encode()
        (package / name).write_bytes(raw)
        sha = hashlib.sha256(raw).hexdigest()
        for m in payload["members"]:
            if m["path"] == name:
                m.update(sha256=sha, size_bytes=len(raw))
        if fault == "surface":
            edge["surface_sha256"] = sha
    if fault == "position":
        payload["entities"][0]["position_m"][2] += 0.1
    if fault == "intent_dimensions":
        name = payload["scene_ir_path"]
        doc = json.loads((package / name).read_bytes())
        doc["entities"][0]["dimensions"] = [0.2, 0.2, 0.2]
        raw = json.dumps(doc).encode()
        (package / name).write_bytes(raw)
        sha = hashlib.sha256(raw).hexdigest()
        payload["scene_ir_sha256"] = sha
        for member in payload["members"]:
            if member["path"] == name:
                member.update(sha256=sha, size_bytes=len(raw))
    result = run_scene(payload, package_root=package, runtime_roots={}, output_dir=tmp_path / "out")
    assert not result["simulator_executed"]
    assert result["error_code"] == (
        "invalid_runtime_request" if fault else "missing_runtime_dependency"
    )


def test_scene_lifecycle_resets_built_state_before_zero_velocity_audit(tmp_path):
    import numpy as np

    from self_improving.harness.x2env.genesis_child import build_reset_scene

    class TensorBoundary:
        def __init__(self, value):
            self.value = np.asarray(value)

        def detach(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return self.value

    class SceneBoundary:
        velocity = [9.0, 0.0, 0.0]
        built = False
        reset_called = False

        def build(self):
            self.built = True

        def reset(self):
            assert self.built
            self.reset_called = True
            self.velocity = [0.0, 0.0, 0.0]

        def get_vel(self):
            assert self.reset_called
            return TensorBoundary(self.velocity)

        def get_ang(self):
            return TensorBoundary([0.0, 0.0, 0.0])

    scene = SceneBoundary()
    result = build_reset_scene(scene, {"body": scene}, tmp_path)
    assert result["status"] == "passed"
    assert result["reset_invoked"] is True
    assert result["post_step_reset_evaluated"] is False
    assert result["objects"]["body"]["velocity"] == [0.0, 0.0, 0.0]
    assert result == json.loads((tmp_path / "reset-lifecycle.json").read_bytes())


@pytest.mark.parametrize(
    "fault", ["linear", "angular", "nonfinite", "reset_exception", "interrupt"]
)
def test_reset_failure_preserves_lifecycle_and_does_not_grant_zero_state(tmp_path, fault):
    import numpy as np

    from self_improving.harness.x2env.genesis_child import build_reset_scene

    class TensorBoundary:
        def __init__(self, value):
            self.value = np.asarray(value)

        def detach(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return self.value

    class SceneBoundary:
        built = False
        reset_called = False

        def build(self):
            self.built = True

        def reset(self):
            assert self.built
            self.reset_called = True
            if fault == "reset_exception":
                raise RuntimeError("external reset failed")
            if fault == "interrupt":
                raise KeyboardInterrupt("external cancellation")

        def get_vel(self):
            assert self.reset_called
            return TensorBoundary(
                [
                    float("nan") if fault == "nonfinite" else 0.1 if fault == "linear" else 0.0,
                    0.0,
                    0.0,
                ]
            )

        def get_ang(self):
            return TensorBoundary([0.1 if fault == "angular" else 0.0, 0.0, 0.0])

    scene = SceneBoundary()
    expected = (
        RuntimeError
        if fault == "reset_exception"
        else KeyboardInterrupt
        if fault == "interrupt"
        else ValueError
    )
    with pytest.raises(expected):
        build_reset_scene(scene, {"body": scene}, tmp_path)
    result = json.loads((tmp_path / "reset-lifecycle.json").read_bytes())
    assert result["status"] == "failed" and result["reset_invoked"] is True
    assert result["events"][0]["status"] == "completed"
    assert result["events"][1]["status"] == (
        "started" if fault in {"reset_exception", "interrupt"} else "completed"
    )
    assert result["error_type"] == expected.__name__


def test_typed_runtime_delegates_verified_scene_to_shared_launcher(tmp_path, monkeypatch):
    from self_improving.harness.x2env import package_loader
    from self_improving.harness.x2env.genesis_runtime import RuntimeEntity

    scene = RuntimeScene(
        seed=37,
        scene_ir_sha256="a" * 64,
        entities=(
            RuntimeEntity(
                id="table",
                kind="structural_box",
                category="table",
                position_m=(0.0, 0.0, 0.5),
                orientation_wxyz=(1.0, 0.0, 0.0, 0.0),
                size_m=(1.0, 1.0, 0.1),
                friction=0.5,
            ),
        ),
    )
    calls = []

    def launcher(payload, **kwargs):
        calls.append((payload, kwargs))
        kwargs["output"].mkdir()
        return {"status": "failed", "error_code": "boundary_double", "wall_seconds": 0.01}

    monkeypatch.setattr(package_loader, "launch_child", launcher)
    result = run_scene(
        scene,
        package_root=tmp_path,
        runtime_roots={},
        output_dir=tmp_path / "out",
        profile="half_dt",
        denied_roots=[tmp_path / "denied"],
        timeout_seconds=123,
    )
    assert result["error_code"] == "boundary_double"
    payload, args = calls[0]
    assert payload["seed"] == 37 and args["profile"] == "half_dt"
    assert args["timeout_seconds"] == 123 and args["child_path"].name == "genesis_child.py"


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
        'if child: open("probe-start-ticks.txt","w").write('
        'open("/proc/self/stat").read().rsplit(")",1)[1].split()[19])\n'
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
    assert lifecycle["start_ticks"] == int((tmp_path / "out/probe-start-ticks.txt").read_text())
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
    assert result["topology_status"] == "not_run"
    bad = actual.copy()
    bad[0, 0] += 0.01
    with pytest.raises(ValueError, match="loaded geometry differs"):
        audit_geometry(bad, actual, position, [1.0, 0.0, 0.0, 0.0], urdf)


def test_loaded_same_vertices_with_changed_collision_faces_are_rejected(tmp_path):
    import numpy as np
    import trimesh

    mesh = trimesh.creation.box(extents=[0.1, 0.2, 0.3])
    mesh.export(tmp_path / "shape.obj")
    urdf = tmp_path / "asset.urdf"
    urdf.write_text(
        '<robot><link name="body">'
        + "".join(
            f'<{kind}><geometry><mesh filename="shape.obj"/></geometry></{kind}>'
            for kind in ("visual", "collision")
        )
        + "</link></robot>"
    )
    vertices = np.asarray(mesh.vertices)
    faces = np.asarray(mesh.faces).copy()
    faces[0] = faces[1]
    part = {"geom_id": 0, "local_vertices_m": vertices.tolist(), "faces": faces.tolist()}
    visual = {"geom_id": 0, "local_vertices_m": vertices.tolist(), "faces": mesh.faces.tolist()}
    with pytest.raises(ValueError, match="loaded triangle topology differs"):
        audit_geometry(
            vertices,
            vertices,
            [0, 0, 0],
            [1, 0, 0, 0],
            urdf,
            geometry_parts={"visual": [visual], "collision": [part]},
        )


@pytest.fixture
def topology_fixture(tmp_path):
    """Authored mesh and explicitly synthetic Genesis-array boundary, never live evidence."""
    import numpy as np
    import trimesh

    mesh = trimesh.creation.box(extents=[0.1, 0.2, 0.3])
    mesh.export(tmp_path / "shape.obj")
    urdf = tmp_path / "asset.urdf"
    urdf.write_text(
        '<robot><link name="body">'
        + "".join(
            f'<{kind}><geometry><mesh filename="shape.obj"/></geometry></{kind}>'
            for kind in ("visual", "collision")
        )
        + "</link></robot>"
    )
    return np.asarray(mesh.vertices), np.asarray(mesh.faces), urdf


def test_loaded_topology_accepts_reindexed_faces_and_records_each_geom(topology_fixture):
    import numpy as np

    vertices, faces, urdf = topology_fixture
    order = np.arange(len(vertices))[::-1]
    inverse = np.argsort(order)
    permuted_faces = np.roll(inverse[faces[::-1]], 1, axis=1)
    parts = [
        {
            "geom_id": 4,
            "local_vertices_m": vertices[order].tolist(),
            "faces": permuted_faces[:6].tolist(),
        },
        {
            "geom_id": 9,
            "local_vertices_m": vertices[order].tolist(),
            "faces": permuted_faces[6:].tolist(),
        },
    ]
    result = audit_geometry(
        vertices,
        vertices,
        [0, 0, 0],
        [1, 0, 0, 0],
        urdf,
        geometry_parts={"visual": parts, "collision": parts},
    )
    assert result["topology_status"] == "passed"
    assert [p["geom_id"] for p in result["geometry_parts"]["collision"]] == [4, 9]
    for part in result["geometry_parts"]["collision"]:
        raw = {k: v for k, v in part.items() if k != "sha256"}
        assert (
            hashlib.sha256(
                json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            == part["sha256"]
        )


@pytest.mark.parametrize(
    "fault", ["negative", "overflow", "float", "boolean", "mixed_boolean", "degenerate", "winding"]
)
def test_loaded_triangle_indices_and_winding_fail_closed(topology_fixture, fault):
    vertices, faces, urdf = topology_fixture
    faces = faces.tolist()
    if fault == "negative":
        faces[0][0] = -1
    elif fault == "overflow":
        faces[0][0] = len(vertices)
    elif fault == "float":
        faces[0][0] = 0.5
    elif fault == "boolean":
        faces = [[True, False, True]]
    elif fault == "mixed_boolean":
        for face in faces:
            if 0 in face:
                face[face.index(0)] = False
                break
    elif fault == "degenerate":
        faces[0][1] = faces[0][0]
    else:
        faces[0] = faces[0][::-1]
    part = {"geom_id": 0, "local_vertices_m": vertices.tolist(), "faces": faces}
    with pytest.raises(ValueError, match="triangle"):
        audit_geometry(
            vertices,
            vertices,
            [0, 0, 0],
            [1, 0, 0, 0],
            urdf,
            geometry_parts={"visual": [part], "collision": [part]},
        )


@pytest.mark.parametrize("sdf", [False, True])
def test_public_loaded_geometry_reads_per_geom_faces_and_rejects_separate_sdf(
    topology_fixture, sdf
):
    from types import SimpleNamespace

    import numpy as np

    vertices, faces, urdf = topology_fixture
    position = np.array([0.2, 0.3, 0.4])
    # Exact quarter turn: authored local geometry must be recovered, not world-axis AABB.
    rotation = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]])
    world = vertices @ rotation.T + position

    class TensorBoundary:
        def __init__(self, value):
            self.value = np.asarray(value)

        def detach(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return self.value

    geom = SimpleNamespace(
        idx=7,
        init_faces=faces,
        get_verts=lambda: TensorBoundary(world),
        mesh=SimpleNamespace(metadata={"sdf_mesh": object()} if sdf else {}),
    )
    visual = SimpleNamespace(idx=2, init_vfaces=faces, get_vverts=lambda: TensorBoundary(world))
    entity = SimpleNamespace(
        links=[SimpleNamespace(geoms=[geom], vgeoms=[visual])],
        get_pos=lambda: TensorBoundary(position),
        get_quat=lambda: TensorBoundary([2**-0.5, 0, 0, 2**-0.5]),
    )
    if sdf:
        with pytest.raises(ValueError, match="^unaudited independent SDF geometry$"):
            audit_loaded_geometry(entity, urdf)
    else:
        result = audit_loaded_geometry(entity, urdf)
        assert result["topology_status"] == "passed"
        np.testing.assert_allclose(
            result["geometry_parts"]["collision"][0]["local_vertices_m"], vertices
        )
