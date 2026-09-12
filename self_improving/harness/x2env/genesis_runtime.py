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

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import package_loader

Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Number = Annotated[float, Field(strict=True)]
Vec3 = tuple[Number, Number, Number]


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

    @model_validator(mode="after")
    def complete(self):
        if abs(sum(v * v for v in self.orientation_wxyz) - 1) > 1e-6:
            raise ValueError("orientation must be a unit wxyz quaternion")
        if self.kind == "rigid":
            if not all((self.urdf_path, self.physics_path, self.version_sha256)):
                raise ValueError("rigid needs explicit URDF, physics and version identity")
            if self.size_m is not None or self.friction is not None:
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


def _write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")


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
        scene = RuntimeScene.model_validate(scene)
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
