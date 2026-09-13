"""Approved immutable collision candidates. No controller or physical authority."""

import hashlib
import io
import json
import os
import signal
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Literal

import numpy as np
import trimesh
from pydantic import Field
from scipy.spatial.transform import Rotation

from .assets import AssetVersion
from .contracts import ArtifactRef, Model, Sha256
from .measured_support import measure_support_surfaces


class CollisionPolicy(Model):
    seed: int = Field(ge=0, le=2147483647)
    threshold: float = Field(default=0.05, gt=0, le=1)
    max_convex_hull: int = Field(default=32, ge=1, le=64)
    preprocess_mode: Literal["off"] = "off"
    preprocess_resolution: Literal[50] = 50
    resolution: int = Field(default=2000, ge=100, le=10000)
    mcts_nodes: int = Field(default=20, ge=1, le=100)
    mcts_iterations: int = Field(default=150, ge=1, le=500)
    mcts_max_depth: int = Field(default=3, ge=1, le=10)
    pca: Literal[False] = False
    merge: Literal[True] = True
    decimate: Literal[False] = False
    max_ch_vertex: Literal[256] = 256
    extrude: Literal[False] = False
    extrude_margin: Literal[0.01] = 0.01
    apx_mode: Literal["ch"] = "ch"
    real_metric: Literal[False] = False
    max_input_triangles: int = Field(default=100000, ge=1, le=200000)
    max_output_triangles: int = Field(default=100000, ge=1, le=200000)


class CollisionResult(Model):
    status: Literal["succeeded", "failed", "cancelled"]
    candidate: AssetVersion | None
    surface_refs: tuple[ArtifactRef, ...]
    receipt: ArtifactRef
    error_code: str | None
    physical_evaluated: Literal[False] = False
    candidate_generated: bool = False
    shared_surface_available: bool = False
    collision_preservation_status: Literal["not_run"] = "not_run"


def _stop_owned(process, lifecycle):
    for sig in (signal.SIGINT, signal.SIGTERM):
        if process.poll() is not None:
            break
        try:
            os.killpg(process.pid, sig)
            lifecycle["signals"].append(sig.name)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            continue


class CoACDBackend(Model):
    python: str
    python_sha256: Sha256
    coacd_package: str
    package_sha256: Sha256
    library_sha256: Sha256
    version: Literal["1.0.14"] = "1.0.14"

    def decompose(self, input_path, *, policy, output_root, timeout):
        policy = CollisionPolicy.model_validate_json(policy.model_dump_json())
        root = Path(output_root)
        if (
            not root.is_absolute()
            or any(p.is_symlink() for p in (root, *root.parents))
            or type(timeout) is not int
            or not 1 <= timeout <= 1770
        ):
            raise ValueError("invalid_coacd_execution_scope")
        root.mkdir(parents=True, exist_ok=False)
        executable = Path(self.python)
        package = Path(self.coacd_package)
        if not executable.is_absolute() or not package.is_absolute():
            raise ValueError("invalid_coacd_deployment")
        for path, digest in (
            (executable, self.python_sha256),
            (package / "__init__.py", self.package_sha256),
            (package / "lib_coacd.so", self.library_sha256),
        ):
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("coacd_deployment_identity_mismatch")
        job = {
            **self.model_dump(mode="json"),
            "input_path": str(input_path),
            "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
            "policy": policy.model_dump(mode="json"),
            "output_root": str(root),
        }
        (root / "job.json").write_bytes(_json(job))
        child = root / "collision_child.py"
        child.write_bytes(Path(__file__).with_name("collision_child.py").read_bytes())
        command = [str(executable), "-I", str(child), str(root / "job.json")]
        lifecycle = {
            "command": command,
            "child_sha256": hashlib.sha256(child.read_bytes()).hexdigest(),
            "python_sha256": self.python_sha256,
            "signals": [],
            "started_monotonic": time.monotonic(),
        }
        result = {"status": "failed", "error_code": "coacd_process_failed"}
        with (root / "stdout.log").open("wb") as stdout, (root / "stderr.log").open("wb") as stderr:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                cwd=root,
                start_new_session=True,
                env={
                    "PATH": "/usr/bin:/bin",
                    "HOME": str(root),
                    "OMP_NUM_THREADS": "1",
                    "OPENBLAS_NUM_THREADS": "1",
                },
            )
            lifecycle.update(pid=process.pid, pgid=process.pid)
            try:
                stat = Path(f"/proc/{process.pid}/stat").read_text()
                lifecycle["start_ticks"] = int(stat.rsplit(")", 1)[1].split()[19])
                lifecycle["actual_executable_sha256"] = hashlib.sha256(
                    Path(f"/proc/{process.pid}/exe").read_bytes()
                ).hexdigest()
                if lifecycle["actual_executable_sha256"] != self.python_sha256:
                    raise ValueError("coacd_running_interpreter_mismatch")
                (root / "process.json").write_bytes(_json(lifecycle))
                process.wait(timeout=timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
                lifecycle["stop_reason"] = (
                    "timed_out" if isinstance(exc, subprocess.TimeoutExpired) else "interrupted"
                )
                _stop_owned(process, lifecycle)
                result = {"status": "cancelled", "error_code": lifecycle["stop_reason"]}
            except (OSError, ValueError, IndexError) as exc:
                lifecycle["stop_reason"] = "coacd_process_identity_unavailable"
                _stop_owned(process, lifecycle)
                result = {"status": "failed", "error_code": str(exc)}
            finally:
                lifecycle.update(exit_code=process.poll(), ended_monotonic=time.monotonic())
                (root / "process.json").write_bytes(_json(lifecycle))
        if not lifecycle.get("stop_reason") and (root / "result.json").is_file():
            result = json.loads((root / "result.json").read_bytes())
            if process.returncode != 0:
                result["status"] = "failed"
        result["process"] = lifecycle
        if process.poll() is None:
            result.update(status="failed", error_code="coacd_process_cleanup_incomplete")
        return result


def _json(value):
    return json.dumps(value, sort_keys=True, allow_nan=False).encode()


def _capture_logs(root, store, record):
    record["backend_artifacts"] = {}
    for name in (
        "job.json",
        "process.json",
        "result.json",
        "stdout.log",
        "stderr.log",
        "collision_child.py",
    ):
        path = root / "backend" / name
        if any(p.is_symlink() for p in (path, *path.parents)):
            record.update(status="failed", error_code="unsafe_backend_evidence")
            continue
        if path.is_file():
            ref = store.write_artifact(
                path.read_bytes(), "application/json" if name.endswith(".json") else "text/plain"
            )
            record["backend_artifacts"][name] = ref.model_dump(mode="json")


def _solid(mesh, *, convex=False):
    if (
        not isinstance(mesh, trimesh.Trimesh)
        or not len(mesh.faces)
        or not np.isfinite(mesh.vertices).all()
        or mesh.faces.min() < 0
        or mesh.faces.max() >= len(mesh.vertices)
    ):
        raise ValueError("invalid_collision_mesh")
    # Exact coordinate welding only: no rounding, remeshing or surface repair.
    vertices, inverse = np.unique(mesh.vertices, axis=0, return_inverse=True)
    mesh = trimesh.Trimesh(vertices=vertices, faces=inverse[mesh.faces], process=False)
    if not mesh.is_volume or not np.isfinite(mesh.volume) or mesh.volume <= 0:
        raise ValueError("collision_input_not_closed_positive_volume")
    if convex and not mesh.is_convex:
        raise ValueError("collision_piece_not_convex")
    return mesh


def _visual(document, contents, policy):
    if document.findall("joint") or len(document.findall("link")) != 1:
        raise ValueError("unsupported_collision_articulation")
    pieces = []
    for element in document.findall("link/visual"):
        geometries = element.findall("geometry")
        if len(geometries) != 1 or len(list(geometries[0])) != 1 or geometries[0][0].tag != "mesh":
            raise ValueError("unsupported_collision_visual")
        if len(element.findall("origin")) > 1:
            raise ValueError("invalid_collision_transform")
        node = element.find("geometry/mesh")
        name = node.get("filename", "") if node is not None else ""
        if name not in contents or PurePosixPath(name).suffix != ".glb":
            raise ValueError("unsupported_collision_visual")

        def vector(raw):
            tokens = raw.split()
            if len(tokens) != 3:
                raise ValueError("invalid_collision_transform")
            value = np.asarray([float(token) for token in tokens])
            if not np.isfinite(value).all():
                raise ValueError("invalid_collision_transform")
            return value

        scale = vector(node.get("scale", "1 1 1"))
        origin = element.find("origin")
        xyz = vector(origin.get("xyz", "0 0 0") if origin is not None else "0 0 0")
        rpy = vector(origin.get("rpy", "0 0 0") if origin is not None else "0 0 0")
        if np.any(scale <= 0):
            raise ValueError("invalid_collision_transform")
        transform = np.eye(4)
        transform[:3, :3] = Rotation.from_euler("xyz", rpy).as_matrix() @ np.diag(scale)
        transform[:3, 3] = xyz
        scene = trimesh.load(
            io.BytesIO(contents[name]), file_type="glb", force="scene", process=False
        )
        for key in sorted(scene.graph.nodes_geometry):
            matrix, geometry = scene.graph[key]
            if (
                matrix.shape != (4, 4)
                or not np.isfinite(matrix).all()
                or not np.array_equal(matrix[3], [0, 0, 0, 1])
                or not np.isfinite(np.linalg.det(matrix[:3, :3]))
                or np.linalg.det(matrix[:3, :3]) <= 0
            ):
                raise ValueError("invalid_collision_transform")
            mesh = scene.geometry[geometry].copy()
            with np.errstate(over="ignore", invalid="ignore"):
                combined = transform @ matrix
            if not np.isfinite(combined).all():
                raise ValueError("invalid_collision_transform")
            mesh.apply_transform(combined)
            pieces.append(_solid(mesh))
    if not pieces or sum(len(mesh.faces) for mesh in pieces) > policy.max_input_triangles:
        raise ValueError("collision_input_budget_or_empty")
    return _solid(trimesh.util.concatenate(pieces))


def decompose_collision(
    parent_version, *, registry, store, backend, policy, approval, output_root, timeout
):
    """Create one candidate and separately measure authored surfaces; retain failures."""
    root = Path(output_root)
    if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("unsafe_collision_output")
    if type(timeout) is not int or not 1 <= timeout <= 1770:
        raise ValueError("invalid_collision_timeout")
    root.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()

    def remaining():
        value = int(timeout - (time.monotonic() - started))
        if value <= 0:
            raise ValueError("collision_timeout")
        return value

    candidate, surfaces, refs = None, [], []
    record = {
        "parent_version": parent_version,
        "approval": approval.model_dump(mode="json"),
        "physical_evaluated": False,
        "status": "failed",
        "error_code": None,
        "collision_preservation_status": "not_run",
    }
    try:
        policy = CollisionPolicy.model_validate_json(policy.model_dump_json())
        expected = {
            "authority": "harness_controller",
            "approved": True,
            "parent_version": parent_version,
            "policy": policy.model_dump(mode="json"),
        }
        if json.loads(store.read_artifact(approval)) != expected:
            raise ValueError("collision_approval_mismatch")
        parent = registry.inspect(parent_version)
        contents = {item.path: store.read_artifact(item.artifact) for item in parent.files}
        document = ET.fromstring(contents[parent.entrypoint])
        mesh = _visual(document, contents, policy)
        input_path = root / "input.json"
        input_path.write_bytes(
            _json({"vertices": mesh.vertices.tolist(), "faces": mesh.faces.tolist()})
        )
        input_ref = store.write_artifact(input_path.read_bytes(), "application/json")
        record.update(
            input_ref=input_ref.model_dump(mode="json"),
            policy=policy.model_dump(mode="json"),
            welding="exact_coordinate_reindex_only",
            inertia="preserved_parent_basis",
        )
        result = backend.decompose(
            input_path, policy=policy, output_root=root / "backend", timeout=remaining()
        )
        record["backend"] = result
        remaining()
        if not isinstance(result, dict):
            raise ValueError("invalid_collision_backend_result")
        if result.get("status") != "succeeded":
            if result.get("status") == "cancelled":
                record["status"] = "cancelled"
            raise ValueError(result.get("error_code", "collision_backend_failed"))
        names = result["pieces"]
        if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
            raise ValueError("invalid_collision_piece_names")
        if (
            not isinstance(names, list)
            or not 1 <= len(names) <= policy.max_convex_hull
            or len(set(names)) != len(names)
        ):
            raise ValueError("collision_piece_budget")
        body = document.find("link")
        old_collision = {
            node.get("filename") for node in document.findall("link/collision/geometry/mesh")
        }
        for node in body.findall("collision"):
            body.remove(node)
        remaining_refs = {node.get("filename") for node in document.findall(".//mesh")}
        for name in old_collision - remaining_refs:
            contents.pop(name, None)
        triangles = 0
        for index, name in enumerate(names):
            remaining()
            relative = PurePosixPath(name)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or str(relative) != name
                or relative.suffix != ".obj"
            ):
                raise ValueError("unsafe_collision_piece")
            path = root / "backend" / name
            if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
                raise ValueError("unsafe_collision_piece")
            raw = path.read_bytes()
            if any(
                line.split() and line.split()[0] in {b"mtllib", b"usemtl"}
                for line in raw.splitlines()
            ):
                raise ValueError("unsupported_collision_material")
            part = _solid(
                trimesh.load(io.BytesIO(raw), file_type="obj", force="mesh", process=False),
                convex=True,
            )
            triangles += len(part.faces)
            if triangles > policy.max_output_triangles:
                raise ValueError("collision_output_budget")
            newname = f"collision/part-{index}.obj"
            contents[newname] = part.export(file_type="obj").encode()
            elem = ET.SubElement(
                ET.SubElement(ET.SubElement(body, "collision"), "geometry"), "mesh"
            )
            elem.set("filename", newname)
        contents[parent.entrypoint] = ET.tostring(document)
        report = json.loads(store.read_artifact(parent.normalization_report))
        report["files"] = [
            {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
            for name, raw in sorted(contents.items())
        ]
        report["collision"] = {
            "basis": "coacd_multi_convex_candidate",
            "piece_count": len(names),
            "fills_concavities": "not_evaluated",
            "parent_version": parent_version,
        }
        report["checks"] = {
            "geometry_normalization": "passed",
            "genesis_load_step": "not_run",
            "physical_profile": "not_run",
        }
        report_ref = store.write_artifact(_json(report), "application/json")
        record["normalization_report"] = report_ref.model_dump(mode="json")
        _capture_logs(root, store, record)
        if record["error_code"]:
            raise ValueError(record["error_code"])
        candidate_receipt = store.write_artifact(
            _json({**record, "status": "candidate_generated"}), "application/json"
        )
        remaining()
        asset_root = root / "candidate"
        asset_root.mkdir()
        for name, raw in contents.items():
            path = asset_root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        remaining()
        candidate = registry.register(
            parent.asset_id,
            parent.category,
            asset_root,
            parent.entrypoint,
            files=tuple(contents),
            normalization_report=report_ref,
            license=parent.license,
            source=parent.source,
            receipt=candidate_receipt,
            parent_version=parent_version,
        )
        surfaces = measure_support_surfaces(
            candidate.version_sha256, registry=registry, store=store
        )
        remaining()
        refs = [
            store.write_artifact(surface.model_dump_json().encode(), "application/json")
            for surface in surfaces
        ]
        record["status"] = "succeeded"
    except (ValueError, OSError, KeyError, TypeError) as exc:
        record["error_code"] = str(exc)
    except KeyboardInterrupt:
        record.update(status="cancelled", error_code="collision_interrupted")
    _capture_logs(root, store, record)
    record.update(
        candidate=candidate.model_dump(mode="json") if candidate else None,
        surface_refs=[ref.model_dump(mode="json") for ref in refs],
        wall_seconds=time.monotonic() - started,
        candidate_generated=candidate is not None,
        shared_surface_available=bool(refs),
    )
    raw = _json(record)
    (root / "receipt.json").write_bytes(raw)
    return CollisionResult(
        status=record["status"],
        candidate=candidate,
        surface_refs=tuple(refs),
        receipt=store.write_artifact(raw, "application/json"),
        error_code=record["error_code"],
        candidate_generated=candidate is not None,
        shared_surface_available=bool(refs),
    )
