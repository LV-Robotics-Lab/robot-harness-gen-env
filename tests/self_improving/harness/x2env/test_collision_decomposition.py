"""Real immutable Registry/CAS; explicit external decomposition double, no physics claim."""

import hashlib
import json
import sys
from pathlib import Path

import pytest
import trimesh

from tests.self_improving.harness.x2env.test_asset_revision import fixture


def test_collision_candidate_preserves_parent_and_visual_physics(tmp_path):
    from self_improving.harness.x2env.collision_decomposition import (
        CollisionPolicy,
        decompose_collision,
    )

    store, registry, parent = fixture(tmp_path)
    policy = CollisionPolicy(seed=23)
    approval = store.write_artifact(
        json.dumps(
            {
                "authority": "harness_controller",
                "approved": True,
                "parent_version": parent.version_sha256,
                "policy": policy.model_dump(mode="json"),
            }
        ).encode(),
        "application/json",
    )

    class BackendDouble:
        def decompose(self, input_path, *, policy, output_root, timeout):
            body = json.loads(input_path.read_bytes())
            mesh = trimesh.Trimesh(vertices=body["vertices"], faces=body["faces"], process=False)
            assert mesh.is_volume and timeout > 0
            output_root.mkdir()
            (output_root / "stdout.log").write_text("explicit decomposition boundary double")
            mesh.export(output_root / "part-0.obj")
            return {
                "status": "succeeded",
                "pieces": ["part-0.obj"],
                "scope": "external_test_double",
            }

    result = decompose_collision(
        parent.version_sha256,
        registry=registry,
        store=store,
        backend=BackendDouble(),
        policy=policy,
        approval=approval,
        output_root=tmp_path / "decompose",
        timeout=30,
    )
    assert result.status == "succeeded", result.error_code
    assert result.candidate.parent_version == parent.version_sha256
    assert result.surface_refs and result.candidate.sim_ready is False
    before = {f.path: store.read_artifact(f.artifact) for f in parent.files}
    after = {f.path: store.read_artifact(f.artifact) for f in result.candidate.files}
    assert before["visual.glb"] == after["visual.glb"]
    assert before["physics.json"] == after["physics.json"]
    assert "collision.obj" not in after and "collision/part-0.obj" in after
    assert registry.inspect(parent.version_sha256) == parent
    receipt = json.loads(store.read_artifact(result.candidate.receipt))
    assert receipt["backend_artifacts"]["stdout.log"]
    assert result.candidate_generated and result.shared_surface_available
    assert result.collision_preservation_status == "not_run"


@pytest.mark.parametrize("fault", ["approval", "escape", "open", "wrong_plane", "cancelled"])
def test_failed_collision_keeps_parent_and_any_registered_candidate(tmp_path, fault):
    from self_improving.harness.x2env.collision_decomposition import (
        CollisionPolicy,
        decompose_collision,
    )

    store, registry, parent = fixture(tmp_path)
    policy = CollisionPolicy(seed=1)
    approval = store.write_artifact(
        json.dumps(
            {
                "authority": "harness_controller",
                "approved": fault != "approval",
                "parent_version": parent.version_sha256,
                "policy": policy.model_dump(mode="json"),
            }
        ).encode(),
        "application/json",
    )

    class BackendDouble:
        def decompose(self, input_path, *, policy, output_root, timeout):
            assert fault != "approval"
            output_root.mkdir()
            (output_root / "stderr.log").write_text("external test double, not native CoACD")
            if fault == "cancelled":
                return {"status": "cancelled", "error_code": "timed_out"}
            raw = json.loads(input_path.read_bytes())
            mesh = trimesh.Trimesh(vertices=raw["vertices"], faces=raw["faces"], process=False)
            if fault == "open":
                mesh.update_faces(range(len(mesh.faces) - 1))
            if fault == "wrong_plane":
                mesh.apply_translation([0, 0, 0.5])
            mesh.export(output_root / "piece.obj")
            return {
                "status": "succeeded",
                "pieces": ["../piece.obj" if fault == "escape" else "piece.obj"],
                "scope": "external_test_double",
            }

    result = decompose_collision(
        parent.version_sha256,
        registry=registry,
        store=store,
        backend=BackendDouble(),
        policy=policy,
        approval=approval,
        output_root=tmp_path / "attempt",
        timeout=30,
    )
    assert result.status == ("cancelled" if fault == "cancelled" else "failed")
    assert registry.inspect(parent.version_sha256) == parent
    assert (result.candidate is not None) == (fault == "wrong_plane")
    if fault != "approval":
        evidence = json.loads(store.read_artifact(result.receipt))
        assert evidence["backend_artifacts"]["stderr.log"]


@pytest.mark.parametrize(
    "mode",
    [
        "return",
        "timeout",
        "bad_identity",
        "fractional",
        "boolean",
        "nan",
        "range",
        "shape",
        "ragged",
        "huge",
    ],
)
def test_fixed_cpu_adapter_real_process_with_explicit_library_double(tmp_path, mode):
    """Actual process lifecycle; pinned miniature library is NOT real CoACD."""
    from self_improving.harness.x2env.collision_decomposition import CoACDBackend, CollisionPolicy

    package = tmp_path / "coacd"
    package.mkdir()
    (package / "lib_coacd.so").write_bytes(b"not loaded: external library boundary double")
    mutation = {
        "fractional": "mesh.f=mesh.f.astype(float);mesh.f[0,0]=0.5;",
        "boolean": "mesh.f=mesh.f.tolist();mesh.f[0][0]=False;",
        "nan": "mesh.v[0,0]=float('nan');",
        "range": "mesh.f[0,0]=99999;",
        "shape": "mesh.f=[1,2,3];",
        "ragged": "mesh.f=[[0,1,2],[0,1]];",
        "huge": "mesh.v=mesh.v.tolist();mesh.v[0][0]=10**1000;",
    }.get(mode, "")
    (package / "__init__.py").write_text(
        ("import time\ntime.sleep(30)\n" if mode == "timeout" else "")
        + "class Mesh:\n def __init__(self,v,f): self.v,self.f=v,f\n"
        + f"def run_coacd(mesh,**kwargs): {mutation}return [(mesh.v,mesh.f)]\n"
    )
    metadata = tmp_path / "coacd-1.0.14.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Version: 1.0.14\n")
    mesh = trimesh.creation.box()
    input_path = tmp_path / "input.json"
    input_path.write_text(
        json.dumps({"vertices": mesh.vertices.tolist(), "faces": mesh.faces.tolist()})
    )

    def sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    backend = CoACDBackend(
        python=sys.executable,
        python_sha256=sha(sys.executable),
        coacd_package=str(package),
        package_sha256=sha(package / "__init__.py"),
        library_sha256="0" * 64 if mode == "bad_identity" else sha(package / "lib_coacd.so"),
    )
    if mode == "bad_identity":
        with pytest.raises(ValueError, match="coacd_deployment_identity_mismatch"):
            backend.decompose(
                input_path, policy=CollisionPolicy(seed=1), output_root=tmp_path / "out", timeout=2
            )
        assert not (tmp_path / "out/process.json").exists()
        return
    result = backend.decompose(
        input_path, policy=CollisionPolicy(seed=1), output_root=tmp_path / "out", timeout=2
    )
    assert result["status"] == (
        "cancelled" if mode == "timeout" else "succeeded" if mode == "return" else "failed"
    )
    if mutation:
        assert result["error_code"] == "invalid_coacd_piece_arrays"
        assert not (tmp_path / "out/part-0.obj").exists()
    process = result["process"]
    assert process["pid"] == process["pgid"] and process["start_ticks"] > 0
    assert process["exit_code"] is not None
    assert not Path(f"/proc/{process['pid']}").exists()
    if mode == "timeout":
        assert process["signals"] == ["SIGINT"]


@pytest.mark.parametrize(
    "bad",
    [None, [], {"status": "succeeded", "pieces": [None]}, {"status": "succeeded", "pieces": [{}]}],
)
def test_malformed_backend_results_keep_structured_receipt(tmp_path, bad):
    from self_improving.harness.x2env.collision_decomposition import (
        CollisionPolicy,
        decompose_collision,
    )

    store, registry, parent = fixture(tmp_path)
    policy = CollisionPolicy(seed=1)
    approval = store.write_artifact(
        json.dumps(
            {
                "authority": "harness_controller",
                "approved": True,
                "parent_version": parent.version_sha256,
                "policy": policy.model_dump(mode="json"),
            }
        ).encode(),
        "application/json",
    )

    class BackendDouble:
        def decompose(self, *args, **kwargs):
            return bad

    result = decompose_collision(
        parent.version_sha256,
        registry=registry,
        store=store,
        backend=BackendDouble(),
        policy=policy,
        approval=approval,
        output_root=tmp_path / "attempt",
        timeout=30,
    )
    assert result.status == "failed" and result.candidate is None
    assert result.error_code in {
        "invalid_collision_backend_result",
        "invalid_collision_piece_names",
    }
    assert store.read_artifact(result.receipt)


def test_backend_success_after_deadline_cannot_register_candidate(tmp_path, monkeypatch):
    import time

    from self_improving.harness.x2env.collision_decomposition import (
        CollisionPolicy,
        decompose_collision,
    )

    store, registry, parent = fixture(tmp_path)
    policy = CollisionPolicy(seed=1)
    approval = store.write_artifact(
        json.dumps(
            {
                "authority": "harness_controller",
                "approved": True,
                "parent_version": parent.version_sha256,
                "policy": policy.model_dump(mode="json"),
            }
        ).encode(),
        "application/json",
    )
    original = time.monotonic

    class BackendDouble:
        def decompose(self, input_path, *, output_root, **kwargs):
            raw = json.loads(input_path.read_bytes())
            output_root.mkdir()
            trimesh.Trimesh(vertices=raw["vertices"], faces=raw["faces"], process=False).export(
                output_root / "piece.obj"
            )
            monkeypatch.setattr(time, "monotonic", lambda: original() + 100)
            return {"status": "succeeded", "pieces": ["piece.obj"]}

    result = decompose_collision(
        parent.version_sha256,
        registry=registry,
        store=store,
        backend=BackendDouble(),
        policy=policy,
        approval=approval,
        output_root=tmp_path / "attempt",
        timeout=30,
    )
    assert result.status == "failed" and result.candidate is None
    assert result.error_code == "collision_timeout"


@pytest.mark.parametrize("fault", ["mixed_geometry", "reflection", "duplicate_origin"])
def test_visual_geometry_is_not_silently_reinterpreted(tmp_path, fault):
    import io
    import xml.etree.ElementTree as ET

    import numpy as np

    from self_improving.harness.x2env.collision_decomposition import (
        CollisionPolicy,
        decompose_collision,
    )

    store, registry, parent = fixture(tmp_path)
    contents = {f.path: store.read_artifact(f.artifact) for f in parent.files}
    document = ET.fromstring(contents[parent.entrypoint])
    visual = document.find("link/visual")
    if fault == "mixed_geometry":
        ET.SubElement(visual.find("geometry"), "box", size="1 1 1")
    if fault == "duplicate_origin":
        ET.SubElement(visual, "origin", xyz="100 0 0")
    if fault == "reflection":
        scene = trimesh.load(
            io.BytesIO(contents["visual.glb"]), file_type="glb", force="scene", process=False
        )
        node = scene.graph.nodes_geometry[0]
        _, geometry = scene.graph[node]
        scene.graph.update(frame_to=node, matrix=np.diag([-1.0, 1.0, 1.0, 1.0]), geometry=geometry)
        contents["visual.glb"] = scene.export(file_type="glb")
    contents[parent.entrypoint] = ET.tostring(document)
    report = json.loads(store.read_artifact(parent.normalization_report))
    report["files"] = [
        {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
        for name, raw in contents.items()
    ]
    root = tmp_path / "new-source"
    root.mkdir()
    for name, raw in contents.items():
        (root / name).write_bytes(raw)
    changed = registry.register(
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
    policy = CollisionPolicy(seed=1)
    approval = store.write_artifact(
        json.dumps(
            {
                "authority": "harness_controller",
                "approved": True,
                "parent_version": changed.version_sha256,
                "policy": policy.model_dump(mode="json"),
            }
        ).encode(),
        "application/json",
    )

    class Forbidden:
        def decompose(self, *args, **kwargs):
            raise AssertionError("ambiguous visual reached backend")

    result = decompose_collision(
        changed.version_sha256,
        registry=registry,
        store=store,
        backend=Forbidden(),
        policy=policy,
        approval=approval,
        output_root=tmp_path / "attempt",
        timeout=30,
    )
    assert result.status == "failed" and result.candidate is None
    assert result.error_code in {"unsupported_collision_visual", "invalid_collision_transform"}


@pytest.mark.parametrize("error", [OSError("external_io_failed"), KeyboardInterrupt()])
def test_external_backend_exception_retains_parent_and_partial_log(tmp_path, error):
    from self_improving.harness.x2env.collision_decomposition import (
        CollisionPolicy,
        decompose_collision,
    )

    store, registry, parent = fixture(tmp_path)
    policy = CollisionPolicy(seed=1)
    approval = store.write_artifact(
        json.dumps(
            {
                "authority": "harness_controller",
                "approved": True,
                "parent_version": parent.version_sha256,
                "policy": policy.model_dump(mode="json"),
            }
        ).encode(),
        "application/json",
    )

    class BackendDouble:
        def decompose(self, *args, output_root, **kwargs):
            output_root.mkdir()
            (output_root / "stderr.log").write_text("external boundary double partial log")
            raise error

    result = decompose_collision(
        parent.version_sha256,
        registry=registry,
        store=store,
        backend=BackendDouble(),
        policy=policy,
        approval=approval,
        output_root=tmp_path / "attempt",
        timeout=30,
    )
    assert result.status == ("cancelled" if isinstance(error, KeyboardInterrupt) else "failed")
    assert result.candidate is None
    assert registry.inspect(parent.version_sha256) == parent
    receipt = json.loads(store.read_artifact(result.receipt))
    assert receipt["backend_artifacts"]["stderr.log"]
