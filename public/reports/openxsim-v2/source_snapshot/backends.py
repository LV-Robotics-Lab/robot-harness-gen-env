"""Backend compilers for EnvironmentPackage v1."""

from __future__ import annotations

import ast
import html
import json
import math
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .ir import AssetBundle, AssetRepresentation, EnvironmentPackage, SceneObject
from .robotwin import RoboTwinExportError, write_robotwin_bundle


class BackendCompileError(RuntimeError):
    """Raised when strict backend compilation cannot represent the package."""


@dataclass(frozen=True)
class CompileResult:
    backend: str
    status: str
    artifact_path: str
    manifest_path: str
    package_path: str
    package_digest: str
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    runtime_command: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def read(cls, path: str | Path) -> "CompileResult":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            backend=str(data["backend"]),
            status=str(data["status"]),
            artifact_path=str(data["artifact_path"]),
            manifest_path=str(data["manifest_path"]),
            package_path=str(data["package_path"]),
            package_digest=str(data["package_digest"]),
            blockers=tuple(str(value) for value in data.get("blockers", [])),
            warnings=tuple(str(value) for value in data.get("warnings", [])),
            runtime_command=tuple(str(value) for value in data.get("runtime_command", [])),
            metadata=dict(data.get("metadata") or {}),
        )


def _slug(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")
    return result or "object"


def _path_uri(value: str) -> Path | None:
    if value.startswith(("primitive://", "robotwin://", "http://", "https://")):
        return None
    return Path(value).expanduser().resolve()


def _asset_map(package: EnvironmentPackage) -> dict[str, AssetBundle]:
    return {asset.asset_id: asset for asset in package.assets}


def _primitive(asset: AssetBundle) -> AssetRepresentation | None:
    return next((item for item in asset.representations if item.format == "primitive_box"), None)


def _float_text(values: Iterable[Any]) -> str:
    return " ".join(f"{float(value):.9g}" for value in values)


def _quaternion_to_rpy(quaternion: Iterable[Any]) -> tuple[float, float, float]:
    w, x, y, z = (float(value) for value in quaternion)
    sin_roll_cos_pitch = 2.0 * (w * x + y * z)
    cos_roll_cos_pitch = 1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(sin_roll_cos_pitch, cos_roll_cos_pitch)
    sin_pitch = 2.0 * (w * y - z * x)
    pitch = math.copysign(math.pi / 2.0, sin_pitch) if abs(sin_pitch) >= 1.0 else math.asin(sin_pitch)
    sin_yaw_cos_pitch = 2.0 * (w * z + x * y)
    cos_yaw_cos_pitch = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.atan2(sin_yaw_cos_pitch, cos_yaw_cos_pitch)
    return roll, pitch, yaw


def _normalize_vector(values: Iterable[Any]) -> tuple[float, float, float]:
    vector = tuple(float(value) for value in values)
    if len(vector) != 3:
        raise BackendCompileError("camera vectors must contain three values")
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 1e-12:
        raise BackendCompileError("camera vector cannot be zero")
    return tuple(value / norm for value in vector)


def _cross(first: tuple[float, float, float], second: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def _camera_xyaxes(sensor: dict[str, Any]) -> tuple[float, float, float, float, float, float]:
    position = tuple(float(value) for value in sensor.get("position") or [0.0, -1.0, 1.0])
    target = tuple(float(value) for value in sensor.get("look_at") or [0.0, 0.0, 0.0])
    forward = _normalize_vector(target[index] - position[index] for index in range(3))
    up_hint = _normalize_vector(sensor.get("up") or [0.0, 0.0, 1.0])
    right = _normalize_vector(_cross(forward, up_hint))
    camera_up = _normalize_vector(_cross(right, forward))
    return (*right, *camera_up)


def _articulation_to_mjcf_body(obj: SceneObject, articulation: dict[str, Any]) -> str:
    links = {str(item["id"]): dict(item) for item in articulation.get("links") or []}
    joints = [dict(item) for item in articulation.get("joints") or []]
    root_link = str(articulation.get("root_link") or "")
    if root_link not in links:
        raise BackendCompileError(f"{obj.instance_id}: articulation root link is missing")
    children: dict[str, list[dict[str, Any]]] = {}
    for joint in joints:
        children.setdefault(str(joint.get("parent")), []).append(joint)

    def add_geometry(body: ET.Element, link: dict[str, Any], name: str) -> None:
        geometry = dict(link.get("geometry") or {})
        if geometry.get("type") != "box":
            raise BackendCompileError(f"{obj.instance_id}:{name}: only box articulation geometry is supported")
        half_size = geometry.get("half_size_m") or [0.05, 0.05, 0.05]
        color = list(geometry.get("color_rgb") or [0.65, 0.68, 0.72]) + [1.0]
        origin = dict(link.get("geometry_origin") or {})
        attributes = {
            "name": f"{_slug(obj.instance_id)}__{_slug(name)}_geom",
            "type": "box",
            "size": _float_text(half_size),
            "rgba": _float_text(color[:4]),
        }
        if origin.get("position"):
            attributes["pos"] = _float_text(origin["position"])
        if origin.get("orientation_wxyz"):
            attributes["quat"] = _float_text(origin["orientation_wxyz"])
        ET.SubElement(body, "geom", attributes)

    def append_children(parent_element: ET.Element, parent_link: str) -> None:
        for joint in sorted(children.get(parent_link, []), key=lambda item: str(item.get("id"))):
            child_id = str(joint.get("child"))
            child = links.get(child_id)
            if child is None:
                raise BackendCompileError(f"{obj.instance_id}: missing child link {child_id}")
            pose = dict(child.get("pose") or {})
            body = ET.SubElement(
                parent_element,
                "body",
                {
                    "name": f"{_slug(obj.instance_id)}__{_slug(child_id)}",
                    "pos": _float_text(pose.get("position") or [0.0, 0.0, 0.0]),
                    "quat": _float_text(pose.get("orientation_wxyz") or [1.0, 0.0, 0.0, 0.0]),
                },
            )
            joint_type = str(joint.get("type") or "fixed")
            if joint_type != "fixed":
                mjcf_type = "hinge" if joint_type in {"revolute", "continuous", "hinge"} else "slide"
                attributes = {
                    "name": str(joint.get("id") or f"{child_id}_joint"),
                    "type": mjcf_type,
                    "axis": _float_text(joint.get("axis") or [0.0, 0.0, 1.0]),
                    "damping": str(float(joint.get("damping", 0.05))),
                }
                limits = joint.get("range")
                if isinstance(limits, (list, tuple)) and len(limits) == 2:
                    attributes["range"] = _float_text(limits)
                    attributes["limited"] = "true"
                ET.SubElement(body, "joint", attributes)
            add_geometry(body, child, child_id)
            append_children(body, child_id)

    root = ET.Element(
        "body",
        {
            "name": _slug(obj.instance_id),
            "pos": _float_text(obj.pose.position),
            "quat": _float_text(obj.pose.orientation_wxyz),
        },
    )
    if not obj.static:
        ET.SubElement(root, "freejoint", {"name": f"{_slug(obj.instance_id)}_free"})
    add_geometry(root, links[root_link], root_link)
    append_children(root, root_link)
    return ET.tostring(root, encoding="unicode")


def _articulation_contact_exclusions(obj: SceneObject, articulation: dict[str, Any]) -> list[str]:
    root_link = str(articulation.get("root_link") or "")

    def body_name(link_id: str) -> str:
        if link_id == root_link:
            return _slug(obj.instance_id)
        return f"{_slug(obj.instance_id)}__{_slug(link_id)}"

    return [
        f'    <exclude body1="{html.escape(body_name(str(joint["parent"])))}" '
        f'body2="{html.escape(body_name(str(joint["child"])))}"/>'
        for joint in articulation.get("joints") or []
        if str(joint.get("type") or "fixed") != "fixed"
    ]


def _articulation_to_urdf(asset: AssetBundle, target: Path) -> Path:
    articulation = dict(asset.articulation or {})
    links = [dict(item) for item in articulation.get("links") or []]
    joints = [dict(item) for item in articulation.get("joints") or []]
    if not links or not articulation.get("root_link"):
        raise BackendCompileError(f"{asset.asset_id}: articulation tree is incomplete")
    robot = ET.Element("robot", {"name": _slug(asset.asset_id)})
    for link in links:
        link_id = str(link["id"])
        link_element = ET.SubElement(robot, "link", {"name": link_id})
        geometry = dict(link.get("geometry") or {})
        if geometry.get("type") != "box":
            raise BackendCompileError(f"{asset.asset_id}:{link_id}: only box geometry is supported")
        size = [2.0 * float(value) for value in geometry.get("half_size_m") or [0.05, 0.05, 0.05]]
        origin = dict(link.get("geometry_origin") or {})
        xyz = _float_text(origin.get("position") or [0.0, 0.0, 0.0])
        rpy = _float_text(_quaternion_to_rpy(origin.get("orientation_wxyz") or [1.0, 0.0, 0.0, 0.0]))
        for role in ("visual", "collision"):
            role_element = ET.SubElement(link_element, role)
            ET.SubElement(role_element, "origin", {"xyz": xyz, "rpy": rpy})
            geometry_element = ET.SubElement(role_element, "geometry")
            ET.SubElement(geometry_element, "box", {"size": _float_text(size)})
            if role == "visual":
                color = list(geometry.get("color_rgb") or [0.65, 0.68, 0.72]) + [1.0]
                material = ET.SubElement(role_element, "material", {"name": f"{link_id}_material"})
                ET.SubElement(material, "color", {"rgba": _float_text(color[:4])})
        inertial = ET.SubElement(link_element, "inertial")
        ET.SubElement(inertial, "mass", {"value": str(float(link.get("mass_kg", 0.2)))})
        ET.SubElement(inertial, "inertia", {"ixx": "0.01", "ixy": "0", "ixz": "0", "iyy": "0.01", "iyz": "0", "izz": "0.01"})
    for joint in joints:
        joint_type = str(joint.get("type") or "fixed")
        urdf_type = "revolute" if joint_type in {"revolute", "hinge"} else "continuous" if joint_type == "continuous" else "prismatic" if joint_type in {"prismatic", "slide"} else "fixed"
        joint_element = ET.SubElement(robot, "joint", {"name": str(joint["id"]), "type": urdf_type})
        ET.SubElement(joint_element, "parent", {"link": str(joint["parent"])})
        ET.SubElement(joint_element, "child", {"link": str(joint["child"])})
        child = next(item for item in links if str(item["id"]) == str(joint["child"]))
        pose = dict(child.get("pose") or {})
        ET.SubElement(
            joint_element,
            "origin",
            {
                "xyz": _float_text(pose.get("position") or [0.0, 0.0, 0.0]),
                "rpy": _float_text(_quaternion_to_rpy(pose.get("orientation_wxyz") or [1.0, 0.0, 0.0, 0.0])),
            },
        )
        if urdf_type != "fixed":
            ET.SubElement(joint_element, "axis", {"xyz": _float_text(joint.get("axis") or [0.0, 0.0, 1.0])})
        if urdf_type in {"revolute", "prismatic"}:
            limits = joint.get("range") or [-1.57, 1.57]
            ET.SubElement(
                joint_element,
                "limit",
                {
                    "lower": str(float(limits[0])),
                    "upper": str(float(limits[1])),
                    "effort": str(float(joint.get("effort", 10.0))),
                    "velocity": str(float(joint.get("velocity", 2.0))),
                },
            )
        if urdf_type != "fixed":
            ET.SubElement(joint_element, "dynamics", {"damping": str(float(joint.get("damping", 0.05)))})
    target.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(robot).write(target, encoding="utf-8", xml_declaration=True)
    return target


def _representation(
    asset: AssetBundle,
    *,
    backend: str,
    formats: Iterable[str],
) -> AssetRepresentation | None:
    accepted = tuple(format.lower() for format in formats)
    for representation in asset.representations:
        if representation.backend == backend and representation.format.lower() in accepted:
            return representation
    for representation in asset.representations:
        if representation.backend == "portable" and representation.format.lower() in accepted:
            return representation
    for representation in asset.representations:
        if representation.format.lower() in accepted:
            return representation
    return None


def _write_manifest(
    package: EnvironmentPackage,
    output: Path,
    *,
    backend: str,
    status: str,
    artifact: Path,
    blockers: list[str],
    warnings: list[str],
    runtime_command: tuple[str, ...],
    metadata: dict[str, Any] | None = None,
) -> CompileResult:
    package_path = package.write_json(output / "environment_package.json")
    manifest_path = output / "compile_manifest.json"
    result = CompileResult(
        backend=backend,
        status=status,
        artifact_path=str(artifact),
        manifest_path=str(manifest_path),
        package_path=str(package_path),
        package_digest=package.digest(),
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        runtime_command=runtime_command,
        metadata={
            "schema": "agenticsim.backend_compile.v1",
            "task_contract": package.task.semantic_contract(),
            "object_ids": [obj.instance_id for obj in package.env.objects],
            "asset_ids": [asset.asset_id for asset in package.assets],
            **(metadata or {}),
        },
    )
    manifest_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def _status(blockers: list[str]) -> str:
    return "compiled" if not blockers else "partial"


class BackendCompiler:
    backend = "unknown"

    def compile(self, package: EnvironmentPackage, output_dir: str | Path, *, strict: bool = False) -> CompileResult:
        raise NotImplementedError

    @staticmethod
    def _finish(result: CompileResult, *, strict: bool) -> CompileResult:
        if strict and result.blockers:
            raise BackendCompileError("; ".join(result.blockers))
        return result


def _usda_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


class IsaacSimCompiler(BackendCompiler):
    backend = "isaacsim"

    def compile(self, package: EnvironmentPackage, output_dir: str | Path, *, strict: bool = False) -> CompileResult:
        package.validate()
        output = Path(output_dir).expanduser().resolve() / self.backend
        output.mkdir(parents=True, exist_ok=True)
        artifact = output / "scene.usda"
        assets = _asset_map(package)
        blockers: list[str] = []
        warnings: list[str] = []
        lines = [
            "#usda 1.0",
            "(",
            '    defaultPrim = "World"',
            "    metersPerUnit = 1",
            f'    upAxis = "{package.env.up_axis}"',
            ")",
            "",
            'def Xform "World"',
            "{",
            '    def PhysicsScene "PhysicsScene"',
            "    {",
            f"        vector3f physics:gravityDirection = ({package.env.gravity_mps2[0]}, {package.env.gravity_mps2[1]}, {package.env.gravity_mps2[2]})",
            "        float physics:gravityMagnitude = 1",
            "    }",
            '    def Cube "Ground"',
            "    {",
            "        double size = 1",
            "        double3 xformOp:scale = (4, 4, 0.02)",
            "        double3 xformOp:translate = (0, 0, -0.02)",
            '        uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]',
            "    }",
            '    def Scope "Objects"',
            "    {",
        ]
        for obj in package.env.objects:
            asset = assets[obj.asset_id]
            name = _slug(obj.instance_id)
            position = obj.pose.position
            orientation = obj.pose.orientation_wxyz
            primitive = _primitive(asset)
            if primitive:
                half = primitive.metadata.get("half_size_m") or [0.025, 0.025, 0.025]
                color = primitive.metadata.get("color_rgb") or [0.8, 0.8, 0.8]
                lines.extend(
                    [
                        f'        def Cube "{name}"',
                        "        {",
                        "            double size = 1",
                        f"            color3f[] primvars:displayColor = [({float(color[0])}, {float(color[1])}, {float(color[2])})]",
                        f"            double3 xformOp:scale = ({2 * float(half[0]) * obj.scale[0]}, {2 * float(half[1]) * obj.scale[1]}, {2 * float(half[2]) * obj.scale[2]})",
                        f"            quatd xformOp:orient = ({orientation[0]}, ({orientation[1]}, {orientation[2]}, {orientation[3]}))",
                        f"            double3 xformOp:translate = ({position[0]}, {position[1]}, {position[2]})",
                        '            uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:orient", "xformOp:scale"]',
                        "        }",
                    ]
                )
                continue
            representation = _representation(asset, backend=self.backend, formats=("usd", "usda", "usdc"))
            path = _path_uri(representation.uri) if representation else None
            if not representation or path is None or not path.is_file():
                blockers.append(f"{obj.instance_id}: no existing USD representation")
                lines.extend(
                    [
                        f'        def Xform "{name}"',
                        "        {",
                        f"            custom string agenticsim:blocker = {_usda_string('missing USD representation')}",
                        "        }",
                    ]
                )
                continue
            relative = os.path.relpath(path, artifact.parent).replace(os.sep, "/")
            lines.extend(
                [
                    f'        def Xform "{name}" (',
                    f"            prepend references = @{relative}@",
                    "        )",
                    "        {",
                    f"            quatd xformOp:orient = ({orientation[0]}, ({orientation[1]}, {orientation[2]}, {orientation[3]}))",
                    f"            double3 xformOp:translate = ({position[0]}, {position[1]}, {position[2]})",
                    f"            double3 xformOp:scale = ({obj.scale[0]}, {obj.scale[1]}, {obj.scale[2]})",
                    '            uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:orient", "xformOp:scale"]',
                    "        }",
                ]
            )
        lines.extend(["    }", '    def Scope "Regions"', "    {"])
        for region in package.env.regions:
            name = _slug(str(region.get("id") or "region"))
            center = region.get("center") or [0.0, 0.0, 0.0]
            size = region.get("size") or [0.1, 0.1, 0.005]
            color = (region.get("color") or [0.2, 0.4, 0.8])[:3]
            lines.extend(
                [
                    f'        def Cube "{name}"',
                    "        {",
                    "            double size = 1",
                    f"            color3f[] primvars:displayColor = [({color[0]}, {color[1]}, {color[2]})]",
                    f"            double3 xformOp:scale = ({size[0]}, {size[1]}, {size[2]})",
                    f"            double3 xformOp:translate = ({center[0]}, {center[1]}, {center[2]})",
                    '            uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]',
                    "        }",
                ]
            )
        lines.extend(["    }", "}", ""])
        artifact.write_text("\n".join(lines), encoding="utf-8")
        result = _write_manifest(
            package,
            output,
            backend=self.backend,
            status=_status(blockers),
            artifact=artifact,
            blockers=blockers,
            warnings=warnings,
            runtime_command=(
                "python",
                str(output / "run_isaac_smoke.py"),
                "--package",
                str(output / "environment_package.json"),
                "--output",
                str(output / "runtime_evidence.json"),
            ),
            metadata={"artifact_format": "usda", "runtime_scene_builder": str(output / "run_isaac_smoke.py")},
        )
        (output / "run_isaac_smoke.py").write_text(_isaac_smoke_script(), encoding="utf-8")
        return self._finish(result, strict=strict)


def _isaac_smoke_script() -> str:
    return '''#!/usr/bin/env python3
import argparse, hashlib, json, os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--package", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--steps", type=int, default=20)
args = parser.parse_args()
os.environ.setdefault("OMNI_KIT_ACCEPT_EULA", "YES")
from isaacsim import SimulationApp
app = SimulationApp({"headless": True, "hide_ui": True, "fast_shutdown": True, "active_gpu": 0, "physics_gpu": 0, "multi_gpu": False, "max_gpu_count": 1})
try:
    import numpy as np
    from isaacsim.core.api import World
    from isaacsim.core.api.objects import DynamicCuboid, FixedCuboid
    from isaacsim.core.prims import SingleXFormPrim
    from isaacsim.core.utils.stage import add_reference_to_stage

    package = json.loads(Path(args.package).read_text(encoding="utf-8"))
    contract = package["task"]
    semantic_contract = {key: contract[key] for key in ("reset", "action", "observation", "success", "termination")}
    contract_hash = hashlib.sha256(json.dumps(semantic_contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    action_interface = str(contract.get("action", {}).get("interface", ""))
    success_types = {str(item.get("type", "")) for item in contract.get("success", [])}
    supported_success = {"state_trace_available", "object_below", "settled"}
    asset_map = {item["asset_id"]: item for item in package["assets"]}
    world = World(physics_dt=0.01, rendering_dt=0.01, stage_units_in_meters=1.0)
    world.scene.add_default_ground_plane()
    actors = {}
    dynamic_names = []
    external_static_names = []
    for item in package["env"]["objects"]:
        asset = asset_map[item["asset_id"]]
        primitive = next((rep for rep in asset["representations"] if rep["format"] == "primitive_box"), None)
        if primitive is None:
            representation = next(
                (
                    rep
                    for rep in asset["representations"]
                    if rep["format"] in {"usd", "usda", "usdc"} and Path(rep["uri"]).is_file()
                ),
                None,
            )
            if representation is None:
                raise RuntimeError(f"Isaac smoke needs primitive_box or USD for {item['instance_id']}")
            name = item["instance_id"]
            prim_path = f"/World/OpenXSim/{name}"
            add_reference_to_stage(usd_path=representation["uri"], prim_path=prim_path)
            actors[name] = SingleXFormPrim(
                prim_path=prim_path,
                name=name,
                position=np.asarray(item["pose"]["position"]),
                orientation=np.asarray(item["pose"]["orientation_wxyz"]),
                scale=np.asarray(item.get("scale", [1, 1, 1])),
            )
            external_static_names.append(name)
            continue
        half = primitive["metadata"].get("half_size_m", [0.025, 0.025, 0.025])
        scale = np.asarray([2 * half[i] * item.get("scale", [1, 1, 1])[i] for i in range(3)])
        color = np.asarray(primitive["metadata"].get("color_rgb", [0.8, 0.8, 0.8]))
        position = np.asarray(item["pose"]["position"])
        orientation = np.asarray(item["pose"]["orientation_wxyz"])
        name = item["instance_id"]
        cls = FixedCuboid if item.get("static", False) else DynamicCuboid
        kwargs = {"prim_path": f"/World/OpenXSim/{name}", "name": name, "position": position, "orientation": orientation, "scale": scale, "color": color}
        if cls is DynamicCuboid:
            kwargs["mass"] = float(asset.get("physical", {}).get("mass_kg", 0.1))
            dynamic_names.append(name)
        actors[name] = world.scene.add(cls(**kwargs))
    world.reset()
    for name in dynamic_names:
        actors[name].set_linear_velocity(np.zeros(3))
        actors[name].set_angular_velocity(np.zeros(3))

    def snapshot(step):
        return {"step": step, "objects": {name: [float(value) for value in actor.get_world_pose()[0]] for name, actor in actors.items()}, "contacts": []}

    trajectory = [snapshot(0)]
    for step in range(1, args.steps + 1):
        world.step(render=False)
        trajectory.append(snapshot(step))
    runtime_stage = Path(args.output).with_name("runtime_scene.usda")
    world.stage.GetRootLayer().Export(str(runtime_stage))
    payload = {
        "schema": "agenticsim.runtime_evidence.v1",
        "backend": "isaacsim",
        "reset_ok": True,
        "step_ok": True,
        "steps": args.steps,
        "action_interface": action_interface,
        "action_interface_bound": action_interface in {"none", "zero_action"},
        "success_evaluator_bound": bool(success_types) and success_types <= supported_success,
        "task_contract_hash": contract_hash,
        "observation_keys": ["contact", "object_pose"],
        "trajectory_mode": "zero_action_physics_rollout",
        "trajectory": trajectory,
        "runtime_stage": str(runtime_stage),
        "external_asset_static_references": external_static_names,
    }
    Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
    print(json.dumps(payload, sort_keys=True))
finally:
    app.close()
'''


class MuJoCoCompiler(BackendCompiler):
    backend = "mujoco"

    def compile(self, package: EnvironmentPackage, output_dir: str | Path, *, strict: bool = False) -> CompileResult:
        package.validate()
        output = Path(output_dir).expanduser().resolve() / self.backend
        output.mkdir(parents=True, exist_ok=True)
        artifact = output / "scene.xml"
        assets = _asset_map(package)
        blockers: list[str] = []
        mesh_entries: list[str] = []
        body_entries: list[str] = []
        contact_entries: list[str] = []
        for obj in package.env.objects:
            asset = assets[obj.asset_id]
            name = _slug(obj.instance_id)
            position = " ".join(f"{value:.9g}" for value in obj.pose.position)
            quat = " ".join(f"{value:.9g}" for value in obj.pose.orientation_wxyz)
            if asset.articulation.get("schema") == "agenticsim.articulation_tree.v1":
                body_entries.append(_articulation_to_mjcf_body(obj, asset.articulation))
                contact_entries.extend(_articulation_contact_exclusions(obj, asset.articulation))
                continue
            primitive = _primitive(asset)
            if primitive:
                half = primitive.metadata.get("half_size_m") or [0.025, 0.025, 0.025]
                size = " ".join(f"{float(half[index]) * obj.scale[index]:.9g}" for index in range(3))
                color = list(primitive.metadata.get("color_rgb") or [0.8, 0.8, 0.8]) + [1.0]
                rgba = " ".join(f"{float(value):.9g}" for value in color[:4])
                joint = "" if obj.static else f'<freejoint name="{name}_free"/>'
                body_entries.append(
                    f'    <body name="{html.escape(name)}" pos="{position}" quat="{quat}">{joint}'
                    f'<geom name="{html.escape(name)}_geom" type="box" size="{size}" rgba="{rgba}"/></body>'
                )
                continue
            representation = _representation(asset, backend=self.backend, formats=("obj", "stl"))
            path = _path_uri(representation.uri) if representation else None
            if not representation or path is None or not path.is_file():
                blockers.append(f"{obj.instance_id}: no existing OBJ/STL representation")
                continue
            mesh_name = f"{name}_mesh"
            relative = os.path.relpath(path, artifact.parent).replace(os.sep, "/")
            scale = " ".join(f"{value:.9g}" for value in obj.scale)
            mesh_entries.append(
                f'    <mesh name="{html.escape(mesh_name)}" file="{html.escape(relative)}" scale="{scale}"/>'
            )
            joint = "" if obj.static else f'<freejoint name="{name}_free"/>'
            body_entries.append(
                f'    <body name="{html.escape(name)}" pos="{position}" quat="{quat}">{joint}'
                f'<geom name="{html.escape(name)}_geom" type="mesh" mesh="{html.escape(mesh_name)}"/></body>'
            )
        for region in package.env.regions:
            name = _slug(str(region.get("id") or "region"))
            center = region.get("center") or [0.0, 0.0, 0.0]
            size = region.get("size") or [0.1, 0.1, 0.005]
            half = [float(value) / 2.0 for value in size]
            color = list(region.get("color") or [0.2, 0.4, 0.8, 0.35])
            body_entries.append(
                f'    <body name="{html.escape(name)}" pos="{" ".join(str(value) for value in center)}">'
                f'<geom type="box" size="{" ".join(str(value) for value in half)}" rgba="{" ".join(str(value) for value in color[:4])}" contype="0" conaffinity="0"/></body>'
            )
        task_text = html.escape(json.dumps(package.task.semantic_contract(), sort_keys=True, separators=(",", ":")))
        object_ids_text = html.escape(json.dumps([obj.instance_id for obj in package.env.objects], separators=(",", ":")))
        sensors_text = html.escape(json.dumps(list(package.env.sensors), sort_keys=True, separators=(",", ":")))
        camera_entries: list[str] = []
        for sensor in package.env.sensors:
            if str(sensor.get("type") or "") not in {"camera", "rgb", "rgb_camera"}:
                continue
            sensor_id = _slug(str(sensor.get("id") or "camera"))
            position = sensor.get("position") or [0.0, -1.0, 1.0]
            fovy = float(sensor.get("fov_y_deg", sensor.get("fovy", 60.0)))
            if sensor.get("look_at") is not None:
                camera_entries.append(
                    f'    <camera name="{html.escape(sensor_id)}" pos="{_float_text(position)}" '
                    f'xyaxes="{_float_text(_camera_xyaxes(sensor))}" fovy="{fovy:.9g}"/>'
                )
            else:
                orientation = sensor.get("orientation_wxyz") or [1.0, 0.0, 0.0, 0.0]
                camera_entries.append(
                    f'    <camera name="{html.escape(sensor_id)}" pos="{_float_text(position)}" '
                    f'quat="{_float_text(orientation)}" fovy="{fovy:.9g}"/>'
                )
        content = "\n".join(
            [
                f'<mujoco model="{html.escape(package.package_id)}">',
                '  <compiler angle="radian" coordinate="local"/>',
                f'  <option gravity="{" ".join(str(value) for value in package.env.gravity_mps2)}" timestep="0.01"/>',
                '  <visual><headlight ambient="0.45 0.45 0.45" diffuse="0.8 0.8 0.8" specular="0.15 0.15 0.15"/></visual>',
                "  <asset>",
                *mesh_entries,
                "  </asset>",
                "  <custom>",
                f'    <text name="agenticsim_task_contract" data="{task_text}"/>',
                f'    <text name="agenticsim_object_ids" data="{object_ids_text}"/>',
                f'    <text name="agenticsim_sensors" data="{sensors_text}"/>',
                "  </custom>",
                "  <contact>",
                *contact_entries,
                "  </contact>",
                "  <worldbody>",
                '    <geom name="ground" type="plane" size="3 3 0.1" rgba="0.3 0.3 0.3 1"/>',
                '    <light name="key_light" pos="0 -1 2" dir="0 0 -1" diffuse="0.8 0.8 0.8"/>',
                *camera_entries,
                *body_entries,
                "  </worldbody>",
                "</mujoco>",
                "",
            ]
        )
        artifact.write_text(content, encoding="utf-8")
        try:
            ET.parse(artifact)
        except ET.ParseError as exc:
            raise BackendCompileError(f"generated MJCF is not XML: {exc}") from exc
        smoke = output / "run_mujoco_smoke.py"
        smoke.write_text(_mujoco_smoke_script(), encoding="utf-8")
        result = _write_manifest(
            package,
            output,
            backend=self.backend,
            status=_status(blockers),
            artifact=artifact,
            blockers=blockers,
            warnings=[],
            runtime_command=("python", str(smoke), "--model", str(artifact), "--output", str(output / "runtime_evidence.json")),
            metadata={"artifact_format": "mjcf", "xml_validated": True},
        )
        return self._finish(result, strict=strict)


def _mujoco_smoke_script() -> str:
    return '''#!/usr/bin/env python3
import argparse, hashlib, json, xml.etree.ElementTree as ET
from pathlib import Path
import mujoco

parser = argparse.ArgumentParser()
parser.add_argument("--model", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--steps", type=int, default=20)
args = parser.parse_args()
root = ET.parse(args.model).getroot()
custom = root.find("./custom/text[@name='agenticsim_task_contract']")
contract = json.loads(custom.get("data")) if custom is not None else {}
objects_custom = root.find("./custom/text[@name='agenticsim_object_ids']")
object_names = json.loads(objects_custom.get("data")) if objects_custom is not None else []
sensors_custom = root.find("./custom/text[@name='agenticsim_sensors']")
sensors = json.loads(sensors_custom.get("data")) if sensors_custom is not None else []
contract_hash = hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
action_interface = str(contract.get("action", {}).get("interface", ""))
success_types = {str(item.get("type", "")) for item in contract.get("success", [])}
supported_success = {"state_trace_available", "object_below", "settled"}
model = mujoco.MjModel.from_xml_path(args.model)
data = mujoco.MjData(model)
mujoco.mj_resetData(model, data)
mujoco.mj_forward(model, data)
initial = data.qpos.tolist()
body_ids = []
for name in object_names:
    body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
    if body_id >= 0:
        body_ids.append(body_id)
joint_ids = [
    joint_id
    for joint_id in range(model.njnt)
    if model.jnt_type[joint_id] != mujoco.mjtJoint.mjJNT_FREE
    and mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, joint_id)
]

def snapshot(step):
    objects = {}
    for body_id in body_ids:
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body_id)
        objects[name] = [float(value) for value in data.xpos[body_id]]
    contacts = []
    for index in range(data.ncon):
        contact = data.contact[index]
        body1 = model.geom_bodyid[contact.geom1]
        body2 = model.geom_bodyid[contact.geom2]
        names = sorted(filter(None, ["ground" if body1 == 0 else mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body1), "ground" if body2 == 0 else mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body2)]))
        if names:
            contacts.append(":".join(names))
    return {"step": step, "objects": objects, "contacts": sorted(set(contacts))}

def joint_snapshot(step):
    return {
        "step": step,
        "joints": {
            mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, joint_id): float(data.qpos[model.jnt_qposadr[joint_id]])
            for joint_id in joint_ids
        },
    }

initial_snapshot = snapshot(0)
initial_snapshot["contacts"] = []
trajectory = [initial_snapshot]
joint_trajectory = [joint_snapshot(0)]
camera_evidence = []
for sensor in sensors:
    if str(sensor.get("type", "")) not in {"camera", "rgb", "rgb_camera"}:
        continue
    camera_name = str(sensor.get("id") or "camera")
    width = int(sensor.get("width", 640))
    height = int(sensor.get("height", 480))
    camera_path = Path(args.output).with_name(f"{camera_name}_rgb.png")
    try:
        from PIL import Image
        with mujoco.Renderer(model, height=height, width=width) as renderer:
            renderer.update_scene(data, camera=camera_name)
            Image.fromarray(renderer.render()).save(camera_path)
        camera_evidence.append({"id": camera_name, "status": "pass", "width": width, "height": height, "fov_y_deg": float(sensor.get("fov_y_deg", sensor.get("fovy", 60.0))), "rgb_path": str(camera_path)})
    except Exception as error:
        camera_evidence.append({"id": camera_name, "status": "fail", "error": repr(error)})
for step in range(1, args.steps + 1):
    mujoco.mj_step(model, data)
    mujoco.mj_forward(model, data)
    trajectory.append(snapshot(step))
    joint_trajectory.append(joint_snapshot(step))
payload = {
    "schema": "agenticsim.runtime_evidence.v1",
    "backend": "mujoco",
    "reset_ok": True,
    "step_ok": True,
    "steps": args.steps,
    "timestep_s": 0.01,
    "qpos_initial": initial,
    "qpos_final": data.qpos.tolist(),
    "action_interface": action_interface,
    "action_interface_bound": action_interface in {"none", "zero_action"},
    "success_evaluator_bound": bool(success_types) and success_types <= supported_success,
    "task_contract_hash": contract_hash,
    "observation_keys": ["contact", "object_pose"],
    "trajectory_mode": "zero_action_physics_rollout",
    "trajectory": trajectory,
    "joint_trajectory": joint_trajectory,
    "camera_evidence": camera_evidence,
}
Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
print(json.dumps(payload, sort_keys=True))
'''


class SapienCompiler(BackendCompiler):
    backend = "sapien"

    def compile(self, package: EnvironmentPackage, output_dir: str | Path, *, strict: bool = False) -> CompileResult:
        package.validate()
        output = Path(output_dir).expanduser().resolve() / self.backend
        output.mkdir(parents=True, exist_ok=True)
        artifact = output / "scene_spec.json"
        runner = output / "run_sapien_smoke.py"
        assets = _asset_map(package)
        blockers: list[str] = []
        object_specs: list[dict[str, Any]] = []
        for obj in package.env.objects:
            asset = assets[obj.asset_id]
            if asset.articulation.get("schema") == "agenticsim.articulation_tree.v1":
                urdf_path = _articulation_to_urdf(
                    asset,
                    output / "generated_urdf" / f"{_slug(asset.asset_id)}.urdf",
                )
                object_specs.append(
                    {
                        "name": obj.instance_id,
                        "position": list(obj.pose.position),
                        "orientation_wxyz": list(obj.pose.orientation_wxyz),
                        "static": obj.static,
                        "scale": list(obj.scale),
                        "kind": "urdf",
                        "path": str(urdf_path),
                        "articulation": asset.articulation,
                    }
                )
                continue
            primitive = _primitive(asset)
            item: dict[str, Any] = {
                "name": obj.instance_id,
                "position": list(obj.pose.position),
                "orientation_wxyz": list(obj.pose.orientation_wxyz),
                "static": obj.static,
                "scale": list(obj.scale),
            }
            if primitive:
                item.update(
                    {
                        "kind": "box",
                        "half_size_m": list(primitive.metadata.get("half_size_m") or [0.025, 0.025, 0.025]),
                        "color_rgb": list(primitive.metadata.get("color_rgb") or [0.8, 0.8, 0.8]),
                    }
                )
            else:
                representation = _representation(
                    asset, backend=self.backend, formats=("urdf", "obj", "stl", "ply", "glb")
                )
                path = _path_uri(representation.uri) if representation else None
                if not representation or path is None or not path.exists():
                    blockers.append(f"{obj.instance_id}: no existing SAPIEN-loadable representation")
                    item.update({"kind": "missing", "blocker": blockers[-1]})
                else:
                    item.update({"kind": representation.format, "path": str(path)})
            object_specs.append(item)
        payload = {
            "schema": "agenticsim.sapien_scene.v1",
            "package_id": package.package_id,
            "gravity_mps2": list(package.env.gravity_mps2),
            "objects": object_specs,
            "regions": list(package.env.regions),
            "sensors": list(package.env.sensors),
            "task_contract": package.task.semantic_contract(),
        }
        artifact.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        runner.write_text(_sapien_smoke_script(), encoding="utf-8")
        ast.parse(runner.read_text(encoding="utf-8"))
        result = _write_manifest(
            package,
            output,
            backend=self.backend,
            status=_status(blockers),
            artifact=artifact,
            blockers=blockers,
            warnings=[],
            runtime_command=("python", str(runner), "--spec", str(artifact), "--output", str(output / "runtime_evidence.json")),
            metadata={"artifact_format": "sapien_scene_json", "runner_syntax_validated": True},
        )
        return self._finish(result, strict=strict)


def _sapien_smoke_script() -> str:
    return '''#!/usr/bin/env python3
import argparse, hashlib, json, math
from pathlib import Path
import sapien

parser = argparse.ArgumentParser()
parser.add_argument("--spec", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--steps", type=int, default=20)
args = parser.parse_args()
spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
contract = spec.get("task_contract", {})
contract_hash = hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
action_interface = str(contract.get("action", {}).get("interface", ""))
success_types = {str(item.get("type", "")) for item in contract.get("success", [])}
supported_success = {"state_trace_available", "object_below", "settled"}

if hasattr(sapien, "Engine"):
    engine = sapien.Engine()
    renderer = sapien.SapienRenderer()
    engine.set_renderer(renderer)
    scene = engine.create_scene()
else:
    scene = sapien.Scene()
scene.set_timestep(0.01)
if hasattr(scene, "set_gravity"):
    scene.set_gravity(spec["gravity_mps2"])
scene.add_ground(0.0)
if hasattr(scene, "set_ambient_light"):
    scene.set_ambient_light([0.35, 0.35, 0.35])
if hasattr(scene, "add_directional_light"):
    scene.add_directional_light([0.2, 0.3, -1.0], [1.2, 1.2, 1.2], shadow=False)
actors = {}
joint_names_by_actor = {}

def set_actor_pose(actor, pose):
    if hasattr(actor, "set_root_pose"):
        actor.set_root_pose(pose)
    else:
        actor.set_pose(pose)

def get_actor_pose(actor):
    if hasattr(actor, "get_root_pose"):
        return actor.get_root_pose()
    return actor.get_pose()

for item in spec["objects"]:
    if item["kind"] == "missing":
        raise RuntimeError(item["blocker"])
    if item["kind"] == "urdf":
        loader = scene.create_urdf_loader()
        loader.fix_root_link = bool(item["static"])
        actor = loader.load(item["path"])
        joint_names_by_actor[item["name"]] = [
            str(joint.get("id"))
            for joint in (item.get("articulation") or {}).get("joints", [])
            if str(joint.get("type")) != "fixed"
        ]
    else:
        builder = scene.create_actor_builder()
        if item["kind"] == "box":
            half = [item["half_size_m"][i] * item["scale"][i] for i in range(3)]
            builder.add_box_collision(half_size=half)
            builder.add_box_visual(half_size=half, material=item["color_rgb"])
        else:
            builder.add_visual_from_file(item["path"], scale=item["scale"])
            builder.add_multiple_convex_collisions_from_file(item["path"], scale=item["scale"])
        actor = builder.build_static(name=item["name"]) if item["static"] else builder.build(name=item["name"])
    set_actor_pose(actor, sapien.Pose(item["position"], item["orientation_wxyz"]))
    actors[item["name"]] = actor
initial = {name: get_actor_pose(actor).p.tolist() for name, actor in actors.items()}

cameras = {}

def camera_pose(sensor):
    position = [float(value) for value in sensor.get("position", [0.0, -1.0, 1.0])]
    if sensor.get("look_at") is None:
        return sapien.Pose(position, sensor.get("orientation_wxyz", [1.0, 0.0, 0.0, 0.0]))
    target = [float(value) for value in sensor["look_at"]]
    up_hint = [float(value) for value in sensor.get("up", [0.0, 0.0, 1.0])]
    def normalize(vector):
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector]
    def cross(first, second):
        return [first[1] * second[2] - first[2] * second[1], first[2] * second[0] - first[0] * second[2], first[0] * second[1] - first[1] * second[0]]
    forward = normalize([target[index] - position[index] for index in range(3)])
    left = normalize(cross(up_hint, forward))
    camera_up = normalize(cross(forward, left))
    matrix = [
        [forward[0], left[0], camera_up[0]],
        [forward[1], left[1], camera_up[1]],
        [forward[2], left[2], camera_up[2]],
    ]
    trace = matrix[0][0] + matrix[1][1] + matrix[2][2]
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        quaternion = [0.25 * scale, (matrix[2][1] - matrix[1][2]) / scale, (matrix[0][2] - matrix[2][0]) / scale, (matrix[1][0] - matrix[0][1]) / scale]
    elif matrix[0][0] > matrix[1][1] and matrix[0][0] > matrix[2][2]:
        scale = math.sqrt(1.0 + matrix[0][0] - matrix[1][1] - matrix[2][2]) * 2.0
        quaternion = [(matrix[2][1] - matrix[1][2]) / scale, 0.25 * scale, (matrix[0][1] + matrix[1][0]) / scale, (matrix[0][2] + matrix[2][0]) / scale]
    elif matrix[1][1] > matrix[2][2]:
        scale = math.sqrt(1.0 + matrix[1][1] - matrix[0][0] - matrix[2][2]) * 2.0
        quaternion = [(matrix[0][2] - matrix[2][0]) / scale, (matrix[0][1] + matrix[1][0]) / scale, 0.25 * scale, (matrix[1][2] + matrix[2][1]) / scale]
    else:
        scale = math.sqrt(1.0 + matrix[2][2] - matrix[0][0] - matrix[1][1]) * 2.0
        quaternion = [(matrix[1][0] - matrix[0][1]) / scale, (matrix[0][2] + matrix[2][0]) / scale, (matrix[1][2] + matrix[2][1]) / scale, 0.25 * scale]
    return sapien.Pose(position, quaternion)

for sensor in spec.get("sensors", []):
    if str(sensor.get("type", "")) not in {"camera", "rgb", "rgb_camera"}:
        continue
    name = str(sensor.get("id") or "camera")
    camera = scene.add_camera(
        name,
        int(sensor.get("width", 640)),
        int(sensor.get("height", 480)),
        float(sensor.get("fov_y_rad", float(sensor.get("fov_y_deg", 60.0)) * 3.141592653589793 / 180.0)),
        float(sensor.get("near_m", 0.01)),
        float(sensor.get("far_m", 100.0)),
    )
    camera.set_pose(camera_pose(sensor))
    cameras[name] = (camera, sensor)

def snapshot(step):
    contacts = []
    if hasattr(scene, "get_contacts"):
        try:
            for contact in scene.get_contacts():
                raw_bodies = list(getattr(contact, "bodies", [None, None]))[:2]
                names = []
                for body in raw_bodies:
                    name = getattr(body, "name", None)
                    getter = getattr(body, "get_name", None)
                    if not name and callable(getter):
                        name = getter()
                    if not name:
                        entity = getattr(body, "entity", None)
                        name = getattr(entity, "name", None)
                    if name:
                        names.append(str(name))
                names = sorted(names)
                if names:
                    contacts.append(":".join(names))
        except Exception:
            contacts = []
    return {"step": step, "objects": {name: [float(value) for value in get_actor_pose(actor).p] for name, actor in actors.items()}, "contacts": sorted(set(contacts))}

def joint_snapshot(step):
    values = {}
    for actor_name, names in joint_names_by_actor.items():
        actor = actors[actor_name]
        qpos = actor.get_qpos() if hasattr(actor, "get_qpos") else []
        for index, name in enumerate(names):
            if index < len(qpos):
                values[name] = float(qpos[index])
    return {"step": step, "joints": values}

trajectory = [snapshot(0)]
joint_trajectory = [joint_snapshot(0)]
camera_evidence = []
if cameras:
    try:
        import numpy as np
        from PIL import Image
        scene.update_render()
        for name, (camera, sensor) in cameras.items():
            camera.take_picture()
            color = camera.get_picture("Color")[..., :3]
            path = Path(args.output).with_name(f"{name}_rgb.png")
            Image.fromarray((np.clip(color, 0.0, 1.0) * 255).astype(np.uint8)).save(path)
            camera_evidence.append({"id": name, "status": "pass", "width": int(sensor.get("width", 640)), "height": int(sensor.get("height", 480)), "fov_y_deg": float(sensor.get("fov_y_deg", 60.0)), "rgb_path": str(path)})
    except Exception as error:
        camera_evidence.append({"id": "camera_runtime", "status": "fail", "error": repr(error)})
for step in range(1, args.steps + 1):
    scene.step()
    trajectory.append(snapshot(step))
    joint_trajectory.append(joint_snapshot(step))
final = {name: get_actor_pose(actor).p.tolist() for name, actor in actors.items()}
payload = {
    "schema": "agenticsim.runtime_evidence.v1",
    "backend": "sapien",
    "reset_ok": True,
    "step_ok": True,
    "steps": args.steps,
    "timestep_s": 0.01,
    "object_positions_initial": initial,
    "object_positions_final": final,
    "action_interface": action_interface,
    "action_interface_bound": action_interface in {"none", "zero_action"},
    "success_evaluator_bound": bool(success_types) and success_types <= supported_success,
    "task_contract_hash": contract_hash,
    "observation_keys": ["contact", "object_pose"],
    "trajectory_mode": "zero_action_physics_rollout",
    "trajectory": trajectory,
    "joint_trajectory": joint_trajectory,
    "camera_evidence": camera_evidence,
}
Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
print(json.dumps(payload, sort_keys=True))
'''


class MetaSimCompiler(BackendCompiler):
    backend = "metasim"

    def compile(self, package: EnvironmentPackage, output_dir: str | Path, *, strict: bool = False) -> CompileResult:
        package.validate()
        output = Path(output_dir).expanduser().resolve() / self.backend
        output.mkdir(parents=True, exist_ok=True)
        artifact = output / "scenario.py"
        config_path = output / "scenario.json"
        assets = _asset_map(package)
        blockers: list[str] = []
        config_objects: list[dict[str, Any]] = []
        expressions: list[str] = []
        for obj in package.env.objects:
            asset = assets[obj.asset_id]
            primitive = _primitive(asset)
            if primitive:
                half = primitive.metadata.get("half_size_m") or [0.025, 0.025, 0.025]
                size = [2 * float(half[index]) * obj.scale[index] for index in range(3)]
                color = list(primitive.metadata.get("color_rgb") or [0.8, 0.8, 0.8])
                kwargs = {
                    "name": obj.instance_id,
                    "size": size,
                    "color": color,
                    "mass": float(asset.physical.get("mass_kg", 0.1)),
                    "default_position": list(obj.pose.position),
                    "default_orientation": list(obj.pose.orientation_wxyz),
                    "fix_base_link": obj.static,
                }
                expressions.append(f"PrimitiveCubeCfg(**{kwargs!r})")
                config_objects.append({"class": "PrimitiveCubeCfg", **kwargs})
                continue
            paths: dict[str, str | None] = {"mesh_path": None, "usd_path": None, "urdf_path": None, "mjcf_path": None}
            mappings = {
                "mesh_path": ("obj", "stl", "ply", "glb", "gltf"),
                "usd_path": ("usd", "usda", "usdc"),
                "urdf_path": ("urdf",),
                "mjcf_path": ("mjcf",),
            }
            for field_name, formats in mappings.items():
                representation = _representation(asset, backend=self.backend, formats=formats)
                if representation:
                    path = _path_uri(representation.uri)
                    if path and path.is_file():
                        paths[field_name] = str(path)
            if not any(paths.values()):
                blockers.append(f"{obj.instance_id}: no existing MetaSim-loadable representation")
                continue
            kwargs = {
                "name": obj.instance_id,
                **paths,
                "default_position": list(obj.pose.position),
                "default_orientation": list(obj.pose.orientation_wxyz),
                "scale": list(obj.scale),
                "fix_base_link": obj.static,
            }
            expressions.append(f"RigidObjCfg(**{kwargs!r})")
            config_objects.append({"class": "RigidObjCfg", **kwargs})
        script = "\n".join(
            [
                '"""Generated by AgenticSim Open-X-Sim."""',
                "from metasim.scenario.objects import PrimitiveCubeCfg, RigidObjCfg",
                "from metasim.scenario.scenario import ScenarioCfg",
                "",
                "objects = [",
                *[f"    {expression}," for expression in expressions],
                "]",
                f"cfg = ScenarioCfg(objects=objects, gravity={package.env.gravity_mps2!r}, headless=True, simulator=None)",
                "",
            ]
        )
        artifact.write_text(script, encoding="utf-8")
        ast.parse(script)
        config_path.write_text(
            json.dumps(
                {
                    "schema": "agenticsim.metasim_scenario.v1",
                    "package_id": package.package_id,
                    "simulator": None,
                    "gravity": list(package.env.gravity_mps2),
                    "objects": config_objects,
                    "task_contract": package.task.semantic_contract(),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        result = _write_manifest(
            package,
            output,
            backend=self.backend,
            status=_status(blockers),
            artifact=artifact,
            blockers=blockers,
            warnings=[],
            runtime_command=("python", "-c", f"exec(open({str(artifact)!r}).read()); print(cfg)"),
            metadata={
                "artifact_format": "metasim_scenario_cfg",
                "scenario_json": str(config_path),
                "runner_syntax_validated": True,
            },
        )
        return self._finish(result, strict=strict)


class RoboTwinCompiler(BackendCompiler):
    backend = "robotwin"

    def compile(self, package: EnvironmentPackage, output_dir: str | Path, *, strict: bool = False) -> CompileResult:
        package.validate()
        output = Path(output_dir).expanduser().resolve() / self.backend
        output.mkdir(parents=True, exist_ok=True)
        blockers: list[str] = []
        try:
            placement_path, task_program_path = write_robotwin_bundle(package, output)
        except RoboTwinExportError as exc:
            blockers.append(str(exc))
            placement_path = output / "placement.json"
            task_program_path = output / "task_program.json"
            placement_path.write_text("{}\n", encoding="utf-8")
            task_program_path.write_text("{}\n", encoding="utf-8")
        result = _write_manifest(
            package,
            output,
            backend=self.backend,
            status=_status(blockers),
            artifact=task_program_path,
            blockers=blockers,
            warnings=[],
            runtime_command=(
                "python",
                "scripts/run_generated_selection2env_rollout_probe.py",
                "--robotwin-root",
                "external/RoboTwin",
                "--task-program-input",
                str(task_program_path),
                "--out-dir",
                str(output / "runtime"),
            ),
            metadata={
                "artifact_format": "robotwin_selection2env_task_program",
                "placement_path": str(placement_path),
            },
        )
        return self._finish(result, strict=strict)


COMPILERS: dict[str, type[BackendCompiler]] = {
    "isaac": IsaacSimCompiler,
    "isaacsim": IsaacSimCompiler,
    "mujoco": MuJoCoCompiler,
    "sapien": SapienCompiler,
    "sapien3": SapienCompiler,
    "metasim": MetaSimCompiler,
    "robotwin": RoboTwinCompiler,
}


def compile_package(
    package: EnvironmentPackage,
    output_dir: str | Path,
    backends: Iterable[str],
    *,
    strict: bool = False,
) -> dict[str, CompileResult]:
    """Compile one canonical package through every requested backend."""

    results: dict[str, CompileResult] = {}
    for name in dict.fromkeys(value.lower() for value in backends):
        compiler_type = COMPILERS.get(name)
        if compiler_type is None:
            raise BackendCompileError(f"unknown backend: {name}")
        compiler = compiler_type()
        results[compiler.backend] = compiler.compile(package, output_dir, strict=strict)
    return results
