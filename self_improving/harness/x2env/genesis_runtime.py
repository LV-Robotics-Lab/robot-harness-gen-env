"""Canonical load/step/render adapter. No workflow, asset acquisition or qualification.

Derived from Harness c0236bd genesis_scene_cpu_backend / portable_genesis_package,
and Gujie eb0b710 standard_urdf morph/audit semantics. Genesis owns simulation.
RuntimeScene contains resolved world poses: this adapter never repairs placement.
"""

import hashlib
import json
import math
import struct
import time
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from . import package_loader

Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Number = Annotated[float, Field(strict=True)]
Vec3 = tuple[Number, Number, Number]
ColorChannel = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]


class RuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class RuntimeMember(RuntimeModel):
    path: str
    sha256: Sha
    size_bytes: int = Field(ge=0)


class RuntimeEntity(RuntimeModel):
    id: str = Field(pattern=r"^[A-Za-z][\w-]*$")
    kind: Literal["rigid", "structural_box"]
    category: str
    position_m: Vec3
    orientation_wxyz: tuple[Number, Number, Number, Number]
    urdf_path: str | None = None
    physics_path: str | None = None
    version_sha256: Sha | None = None
    size_m: Vec3 | None = None
    friction: float | None = Field(default=None, ge=0)
    surface_rgba: tuple[ColorChannel, ColorChannel, ColorChannel, ColorChannel] | None = None

    @model_serializer(mode="wrap")
    def retain_legacy_shape(self, handler):
        value = handler(self)
        if self.surface_rgba is None:
            value.pop("surface_rgba", None)
        return value

    @model_validator(mode="after")
    def complete(self):
        if abs(sum(v * v for v in self.orientation_wxyz) - 1) > 1e-6:
            raise ValueError("orientation must be a unit wxyz quaternion")
        if self.kind == "rigid":
            if not all((self.urdf_path, self.physics_path, self.version_sha256)):
                raise ValueError("rigid needs explicit URDF, physics and version identity")
            if (
                self.size_m is not None
                or self.friction is not None
                or self.surface_rgba is not None
            ):
                raise ValueError("rigid geometry/physics must come from asset members")
        elif (
            self.category not in {"table", "worktop", "counter"}
            or self.size_m is None
            or min(self.size_m) <= 0
            or self.friction is None
            or self.urdf_path is not None
            or self.physics_path is not None
            or self.version_sha256 is not None
        ):
            raise ValueError("structural boxes only support table/worktop/counter")
        return self


class RuntimeRelation(RuntimeModel):
    source: str
    target: str
    relation: Literal["on", "left_of", "right_of", "front_of", "behind", "near"]


class RuntimeScene(RuntimeModel):
    schema_version: Literal["x2env.runtime_scene.v1"] = "x2env.runtime_scene.v1"
    seed: int = Field(strict=True, ge=0, le=2147483647)
    scene_ir_sha256: Sha
    entities: tuple[RuntimeEntity, ...] = Field(min_length=1, max_length=8)
    relations: tuple[RuntimeRelation, ...] = ()
    members: tuple[RuntimeMember, ...] = ()

    @model_validator(mode="after")
    def graph(self):
        ids = {e.id for e in self.entities}
        if len(ids) != len(self.entities) or "ground" in ids:
            raise ValueError("duplicate or reserved entity ID")
        if len({m.path for m in self.members}) != len(self.members):
            raise ValueError("duplicate member")
        for r in self.relations:
            if r.source not in ids or r.target not in ids or r.source == r.target:
                raise ValueError("invalid relation")
            if r.relation == "on":
                by_id = {e.id: e for e in self.entities}
                if by_id[r.source].kind != "rigid" or by_id[r.target].kind != "structural_box":
                    raise ValueError("runtime profile supports rigid on structural support only")
        return self


class RuntimeSupportBinding(RuntimeModel):
    kind: Literal["structural_top", "measured_surface"]
    source_id: str
    target_id: str
    source_version_sha256: Sha
    source_asset_record_path: str
    target_version_sha256: Sha | None
    target_asset_record_path: str | None
    surface_path: str | None
    surface_sha256: Sha | None
    selection_receipt_path: str


class RuntimeSceneV2(RuntimeModel):
    """Explicit authored-geometry profile; does not grant loaded/physical validity."""

    schema_version: Literal["x2env.runtime_scene.v2"] = "x2env.runtime_scene.v2"
    support_profile: Literal["measured_single_support_dag.v1"] = "measured_single_support_dag.v1"
    seed: int = Field(strict=True, ge=0, le=2147483647)
    scene_ir_sha256: Sha
    scene_ir_path: str
    entities: tuple[RuntimeEntity, ...] = Field(min_length=1, max_length=8)
    relations: tuple[RuntimeRelation, ...]
    members: tuple[RuntimeMember, ...]
    support_bindings: tuple[RuntimeSupportBinding, ...]

    @model_validator(mode="after")
    def graph(self):
        by_id = {e.id: e for e in self.entities}
        members = {m.path: m for m in self.members}
        if len(by_id) != len(self.entities) or "ground" in by_id:
            raise ValueError("duplicate or reserved entity ID")
        if len(members) != len(self.members) or self.scene_ir_path not in members:
            raise ValueError("duplicate or missing scene member")
        if members[self.scene_ir_path].sha256 != self.scene_ir_sha256:
            raise ValueError("scene member identity mismatch")
        edges = {}
        for relation in self.relations:
            if (
                relation.source not in by_id
                or relation.target not in by_id
                or relation.source == relation.target
            ):
                raise ValueError("invalid relation")
            if relation.relation == "on":
                if by_id[relation.source].kind != "rigid" or relation.source in edges:
                    raise ValueError("multiple or invalid support targets")
                edges[relation.source] = relation.target
        if set(edges) != {e.id for e in self.entities if e.kind == "rigid"}:
            raise ValueError("each dynamic entity needs exactly one support")
        for start in edges:
            seen, node = set(), start
            while node in edges:
                if node in seen:
                    raise ValueError("cyclic support graph")
                seen.add(node)
                node = edges[node]
        if len(self.support_bindings) != len(edges):
            raise ValueError("missing support bindings")
        sources = set()
        for binding in self.support_bindings:
            if (
                binding.source_id in sources
                or edges.get(binding.source_id) != binding.target_id
                or by_id[binding.source_id].version_sha256 != binding.source_version_sha256
            ):
                raise ValueError("support binding identity mismatch")
            sources.add(binding.source_id)
            target = by_id[binding.target_id]
            required = [binding.source_asset_record_path, binding.selection_receipt_path]
            if target.kind == "rigid":
                if (
                    binding.kind != "measured_surface"
                    or binding.target_version_sha256 != target.version_sha256
                    or not all(
                        (
                            binding.target_asset_record_path,
                            binding.surface_path,
                            binding.surface_sha256,
                        )
                    )
                ):
                    raise ValueError("missing measured support identity")
                required += [binding.target_asset_record_path, binding.surface_path]
                if (
                    binding.surface_path not in members
                    or members[binding.surface_path].sha256 != binding.surface_sha256
                ):
                    raise ValueError("support surface identity mismatch")
            elif binding.kind != "structural_top" or any(
                v is not None
                for v in (
                    binding.target_version_sha256,
                    binding.target_asset_record_path,
                    binding.surface_path,
                    binding.surface_sha256,
                )
            ):
                raise ValueError("invalid structural support binding")
            if any(path not in members for path in required):
                raise ValueError("unbound support proof member")
        return self


def parse_runtime_scene(value):
    """Version dispatch without adding any fields to historical v1 serialization."""
    if isinstance(value, (RuntimeScene, RuntimeSceneV2)):
        value = value.model_dump(mode="json")
    if not isinstance(value, dict):
        raise ValueError("invalid runtime scene")
    if value.get("schema_version", "x2env.runtime_scene.v1") == "x2env.runtime_scene.v1":
        return RuntimeScene.model_validate(value)
    return RuntimeSceneV2.model_validate(value)


def _write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, allow_nan=False) + "\n")


def _member(root, name):
    relative = PurePosixPath(name)
    if not name or relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
        raise ValueError("unsafe member path")
    path = root / name
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.resolve().is_relative_to(
        root
    ):
        raise ValueError("unsafe member path: symlink/escape")
    return path


def _verify(scene, root):
    names = {m.path for m in scene.members}
    for m in scene.members:
        content = _member(root, m.path).read_bytes()
        if len(content) != m.size_bytes or hashlib.sha256(content).hexdigest() != m.sha256:
            raise ValueError("corrupt runtime member")
        if Path(m.path).suffix.lower() == ".glb":
            if len(content) < 20 or content[:4] != b"glTF":
                raise ValueError("invalid GLB member")
            size = struct.unpack("<I", content[12:16])[0]
            doc = json.loads(content[20 : 20 + size])
            for group in ("buffers", "images"):
                if any(
                    item.get("uri") and not item["uri"].startswith("data:")
                    for item in doc.get(group, [])
                ):
                    raise ValueError("unsupported external GLB resource")
            if doc.get("skins") or doc.get("animations"):
                raise ValueError("unsupported articulated GLB")
        elif Path(m.path).suffix.lower() == ".obj":
            if any(
                line.strip().startswith(("mtllib ", "usemtl "))
                for line in content.decode().splitlines()
            ):
                raise ValueError("unsupported OBJ material closure; normalized GLB visual required")
    for e in scene.entities:
        if e.kind != "rigid":
            continue
        if e.urdf_path not in names or e.physics_path not in names:
            raise ValueError("unbound URDF/physics member")
        urdf = _member(root, e.urdf_path)
        document = ET.parse(urdf)
        if document.findall("joint") or len(document.findall("link")) != 1:
            raise ValueError("unsupported articulation/multilink asset")
        for mesh in document.findall(".//mesh"):
            ref = mesh.get("filename", "")
            path = _member(urdf.parent, ref)
            if path.relative_to(root).as_posix() not in names:
                raise ValueError("unbound mesh resource")
        physics = json.loads(_member(root, e.physics_path).read_bytes())
        for key in ("mass_kg", "friction"):
            value = physics[key]
            if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
                raise ValueError("invalid supplied physics")
        if physics["mass_kg"] == 0:
            raise ValueError("dynamic mass must be positive")
    if isinstance(scene, RuntimeSceneV2):
        _verify_support_bindings(scene, root)


def _verify_support_bindings(scene, root):
    """Recompute authored geometry entirely from the copied package's bound members."""
    import numpy as np
    from scipy.spatial.transform import Rotation

    from .assets import AssetVersion
    from .contracts import SceneIR
    from .measured_support import (
        measure_support_surfaces_from_members,
        read_asset_geometry_from_members,
        select_unique_support_surface,
    )

    intent = SceneIR.model_validate_json(_member(root, scene.scene_ir_path).read_bytes())
    by_id = {entity.id: entity for entity in scene.entities}
    originals = {entity.id: entity for entity in intent.entities}
    if set(by_id) != set(originals) or [r.model_dump() for r in scene.relations] != [
        {"source": r.source, "target": r.target, "relation": r.relation} for r in intent.relations
    ]:
        raise ValueError("support scene intent mismatch")
    members = {m.path: m for m in scene.members}
    geometry, versions, readers = {}, {}, {}
    for binding in scene.support_bindings:
        entity = by_id[binding.source_id]
        version = AssetVersion.model_validate_json(
            _member(root, binding.source_asset_record_path).read_bytes()
        )
        if version.version_sha256 != entity.version_sha256:
            raise ValueError("support asset version mismatch")
        prefix = PurePosixPath(entity.urdf_path).parent
        # Entrypoint may itself be nested; recover only its declared logical suffix.
        entry = PurePosixPath(version.entrypoint)
        for _ in entry.parent.parts:
            prefix = prefix.parent
        if str(prefix / version.entrypoint) != entity.urdf_path:
            raise ValueError("support asset entrypoint mismatch")
        for member in version.files:
            bound = members.get(str(prefix / member.path))
            if (
                bound is None
                or bound.sha256 != member.artifact.sha256
                or bound.size_bytes != member.artifact.size_bytes
            ):
                raise ValueError("unbound support asset member")

        def reader(path, prefix=prefix):
            return _member(root, str(prefix / path)).read_bytes()

        geometry[entity.id] = read_asset_geometry_from_members(version, reader)
        versions[entity.id], readers[entity.id] = version, reader

    def rotation(entity):
        q = entity.orientation_wxyz
        return Rotation.from_quat([q[1], q[2], q[3], q[0]]).as_matrix()

    def centre(entity):
        offset = (
            geometry[entity.id]["visual_center_m"]
            if entity.kind == "rigid"
            else [0, 0, entity.size_m[2] / 2]
        )
        return np.asarray(entity.position_m) + rotation(entity) @ offset

    for entity in scene.entities:
        original = originals[entity.id]
        if (entity.kind == "rigid") != (
            original.role == "foreground"
        ) or entity.category != original.category:
            raise ValueError("support entity intent mismatch")
        if original.dimensions is not None:
            measured = (
                np.diff(geometry[entity.id]["visual_bounds_m"], axis=0)[0]
                if entity.kind == "rigid"
                else entity.size_m
            )
            if any(
                wanted is not None and abs(wanted - actual) > 1e-6
                for wanted, actual in zip(original.dimensions, measured, strict=True)
            ):
                raise ValueError("support known dimensions mismatch")
        frame = original.pose.frame
        parent_pos = centre(by_id[frame]) if frame != "world" else np.zeros(3)
        parent_rotation = rotation(by_id[frame]) if frame != "world" else np.eye(3)
        actual_local = (centre(entity) - parent_pos) @ parent_rotation
        for wanted, actual in zip(original.pose.position, actual_local, strict=True):
            if wanted is not None and abs(wanted - actual) > 1e-6:
                raise ValueError("support known position mismatch")
        if original.pose.yaw_degrees is None:
            raise ValueError("support unresolved yaw")
        expected_rotation = (
            parent_rotation
            @ Rotation.from_euler("z", original.pose.yaw_degrees, degrees=True).as_matrix()
        )
        if not np.allclose(rotation(entity), expected_rotation, atol=1e-6):
            raise ValueError("support yaw mismatch")
    for binding in scene.support_bindings:
        source, target = by_id[binding.source_id], by_id[binding.target_id]
        original = originals[source.id]
        known_z = original.pose.position[2] is not None
        if not known_z and original.pose.frame != target.id:
            raise ValueError("support unknown Z requires target frame")
        expected = dict(
            schema_version="x2env.support_selection.v1",
            source_id=source.id,
            target_id=target.id,
            source_version_sha256=source.version_sha256,
            target_version_sha256=target.version_sha256,
            source_geometry_center_m=geometry[source.id]["visual_center_m"],
            target_geometry_center_m=geometry[target.id]["visual_center_m"]
            if target.kind == "rigid"
            else None,
            known_z=known_z,
            selection_basis="unique_feasible" if target.kind == "rigid" else "structural_top",
            physical_evaluated=False,
            source_urdf_position_m=list(source.position_m),
            target_urdf_position_m=list(target.position_m),
        )
        if target.kind == "rigid":
            target_record = AssetVersion.model_validate_json(
                _member(root, binding.target_asset_record_path).read_bytes()
            )
            if target_record != versions[target.id]:
                raise ValueError("support target record mismatch")
            surfaces = measure_support_surfaces_from_members(target_record, readers[target.id])
            surface, pose, choices = select_unique_support_surface(
                surfaces,
                source_geometry=geometry[source.id],
                source_pose=dict(
                    position_m=source.position_m, orientation_wxyz=source.orientation_wxyz
                ),
                target_pose=dict(
                    position_m=target.position_m, orientation_wxyz=target.orientation_wxyz
                ),
                known_z=known_z,
            )
            if surface.model_dump_json().encode() != _member(
                root, binding.surface_path
            ).read_bytes() or not np.allclose(pose["position_m"], source.position_m, atol=1e-6):
                raise ValueError("support measured plane or placement mismatch")
            expected.update(choices)
        else:
            if (
                abs(
                    source.position_m[2]
                    + geometry[source.id]["bounds_m"][0][2]
                    - target.position_m[2]
                    - target.size_m[2] / 2
                )
                > 1e-5
            ):
                raise ValueError("support structural plane mismatch")
        actual = json.loads(_member(root, binding.selection_receipt_path).read_bytes())
        if actual != expected:
            raise ValueError("support selection receipt mismatch")


def run_scene(
    scene,
    *,
    package_root,
    runtime_roots,
    output_dir,
    profile="baseline",
    denied_roots=(),
    timeout_seconds=600,
):
    """Validate typed inputs, then use the same launcher as portable packages."""
    output = Path(output_dir).absolute()
    if output == Path("/") or any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("unsafe execution output")
    if output.exists():
        raise FileExistsError(output)
    start = time.monotonic()
    try:
        scene = parse_runtime_scene(scene)
        root = Path(package_root).resolve()
        _verify(scene, root)
    except (ValueError, OSError) as exc:
        output.mkdir(parents=True, exist_ok=False)
        result = {
            "status": "failed",
            "simulator_executed": False,
            "physical_profile": "not_run",
            "robot_policy_evaluated": False,
            "data_collection_evaluated": False,
            "error_code": "invalid_runtime_request",
            "reason": str(exc),
            "wall_seconds": time.monotonic() - start,
        }
        _write(output / "result.json", result)
        return result
    result = package_loader.launch_child(
        scene.model_dump(mode="json"),
        package_root=root,
        child_path=Path(__file__).with_name("genesis_child.py"),
        runtime_roots=runtime_roots,
        output=output,
        profile=profile,
        denied_roots=denied_roots,
        timeout_seconds=timeout_seconds,
    )
    result["launch_wall_seconds"] = result["wall_seconds"]
    result["wall_seconds"] = time.monotonic() - start
    _write(output / "result.json", result)
    return result
