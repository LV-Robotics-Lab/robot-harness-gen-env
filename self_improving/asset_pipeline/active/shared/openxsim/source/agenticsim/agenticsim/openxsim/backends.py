"""Backend compilers for EnvironmentPackage v1."""

from __future__ import annotations

import ast
import html
import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .ir import AssetBundle, AssetRepresentation, EnvironmentPackage
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
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def _status(blockers: list[str]) -> str:
    return "compiled" if not blockers else "partial"


class BackendCompiler:
    backend = "unknown"

    def compile(
        self, package: EnvironmentPackage, output_dir: str | Path, *, strict: bool = False
    ) -> CompileResult:
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

    def compile(
        self, package: EnvironmentPackage, output_dir: str | Path, *, strict: bool = False
    ) -> CompileResult:
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
            (
                "        vector3f physics:gravityDirection = ("
                f"{package.env.gravity_mps2[0]}"
                ", "
                f"{package.env.gravity_mps2[1]}"
                ", "
                f"{package.env.gravity_mps2[2]}"
                ")"
            ),
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
                        (
                            "            color3f[] primvars:displayColor = [("
                            f"{float(color[0])}"
                            ", "
                            f"{float(color[1])}"
                            ", "
                            f"{float(color[2])}"
                            ")]"
                        ),
                        (
                            "            double3 xformOp:scale = ("
                            f"{2 * float(half[0]) * obj.scale[0]}"
                            ", "
                            f"{2 * float(half[1]) * obj.scale[1]}"
                            ", "
                            f"{2 * float(half[2]) * obj.scale[2]}"
                            ")"
                        ),
                        (
                            "            quatd xformOp:orient = ("
                            f"{orientation[0]}"
                            ", "
                            f"{orientation[1]}"
                            ", "
                            f"{orientation[2]}"
                            ", "
                            f"{orientation[3]}"
                            ")"
                        ),
                        (
                            "            double3 xformOp:translate = ("
                            f"{position[0]}"
                            ", "
                            f"{position[1]}"
                            ", "
                            f"{position[2]}"
                            ")"
                        ),
                        (
                            '            uniform token[] xformOpOrder = ["xformOp:translate'
                            '", "xformOp:orient", "xformOp:scale"]'
                        ),
                        "        }",
                    ]
                )
                continue
            representation = _representation(
                asset, backend=self.backend, formats=("usd", "usda", "usdc")
            )
            path = _path_uri(representation.uri) if representation else None
            if not representation or path is None or not path.is_file():
                blockers.append(f"{obj.instance_id}: no existing USD representation")
                lines.extend(
                    [
                        f'        def Xform "{name}"',
                        "        {",
                        (
                            "            custom string agenticsim:blocker = "
                            f"{_usda_string('missing USD representation')}"
                        ),
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
                    (
                        "            quatd xformOp:orient = ("
                        f"{orientation[0]}"
                        ", "
                        f"{orientation[1]}"
                        ", "
                        f"{orientation[2]}"
                        ", "
                        f"{orientation[3]}"
                        ")"
                    ),
                    (
                        "            double3 xformOp:translate = ("
                        f"{position[0]}"
                        ", "
                        f"{position[1]}"
                        ", "
                        f"{position[2]}"
                        ")"
                    ),
                    (
                        "            double3 xformOp:scale = ("
                        f"{obj.scale[0]}"
                        ", "
                        f"{obj.scale[1]}"
                        ", "
                        f"{obj.scale[2]}"
                        ")"
                    ),
                    (
                        '            uniform token[] xformOpOrder = ["xformOp:translate'
                        '", "xformOp:orient", "xformOp:scale"]'
                    ),
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
                    (
                        "            color3f[] primvars:displayColor = [("
                        f"{color[0]}"
                        ", "
                        f"{color[1]}"
                        ", "
                        f"{color[2]}"
                        ")]"
                    ),
                    f"            double3 xformOp:scale = ({size[0]}, {size[1]}, {size[2]})",
                    (
                        "            double3 xformOp:translate = ("
                        f"{center[0]}"
                        ", "
                        f"{center[1]}"
                        ", "
                        f"{center[2]}"
                        ")"
                    ),
                    (
                        '            uniform token[] xformOpOrder = ["xformOp:translate'
                        '", "xformOp:scale"]'
                    ),
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
            metadata={
                "artifact_format": "usda",
                "runtime_scene_builder": str(output / "run_isaac_smoke.py"),
            },
        )
        (output / "run_isaac_smoke.py").write_text(_isaac_smoke_script(), encoding="utf-8")
        return self._finish(result, strict=strict)


def _isaac_smoke_script() -> str:
    return (
        "#!/usr/bin/env python3\nimport argparse, hashlib, json, os\nfrom"
        " pathlib import Path\n\nparser = argparse.ArgumentParser()\nparse"
        'r.add_argument("--package", required=True)\nparser.add_argument'
        '("--output", required=True)\nparser.add_argument("--steps", typ'
        "e=int, default=20)\nargs = parser.parse_args()\nos.environ.setde"
        'fault("OMNI_KIT_ACCEPT_EULA", "YES")\nfrom isaacsim import Simu'
        'lationApp\napp = SimulationApp({"headless": True, "hide_ui": Tr'
        'ue, "fast_shutdown": True, "active_gpu": 0, "physics_gpu": 0, '
        '"multi_gpu": False, "max_gpu_count": 1})\ntry:\n    import numpy'
        " as np\n    from isaacsim.core.api import World\n    from isaacs"
        "im.core.api.objects import DynamicCuboid, FixedCuboid\n    from"
        " isaacsim.core.prims import SingleXFormPrim\n    from isaacsim."
        "core.utils.stage import add_reference_to_stage\n\n    package = "
        'json.loads(Path(args.package).read_text(encoding="utf-8"))\n   '
        ' contract = package["task"]\n    semantic_contract = {key: cont'
        'ract[key] for key in ("reset", "action", "observation", "succe'
        'ss", "termination")}\n    contract_hash = hashlib.sha256(json.d'
        'umps(semantic_contract, sort_keys=True, separators=(",", ":"))'
        ".encode()).hexdigest()\n    action_interface = str(contract.get"
        '("action", {}).get("interface", ""))\n    success_types = {str('
        'item.get("type", "")) for item in contract.get("success", [])}'
        '\n    supported_success = {"state_trace_available", "object_bel'
        'ow", "settled"}\n    asset_map = {item["asset_id"]: item for it'
        'em in package["assets"]}\n    world = World(physics_dt=0.01, re'
        "ndering_dt=0.01, stage_units_in_meters=1.0)\n    world.scene.ad"
        "d_default_ground_plane()\n    actors = {}\n    dynamic_names = ["
        ']\n    external_static_names = []\n    for item in package["env"'
        ']["objects"]:\n        asset = asset_map[item["asset_id"]]\n    '
        '    primitive = next((rep for rep in asset["representations"] '
        'if rep["format"] == "primitive_box"), None)\n        if primiti'
        "ve is None:\n            representation = next(\n               "
        " (\n                    rep\n                    for rep in asse"
        't["representations"]\n                    if rep["format"] in {'
        '"usd", "usda", "usdc"} and Path(rep["uri"]).is_file()\n        '
        "        ),\n                None,\n            )\n            if "
        'representation is None:\n                raise RuntimeError(f"I'
        "saac smoke needs primitive_box or USD for {item['instance_id']"
        '}")\n            name = item["instance_id"]\n            prim_pa'
        'th = f"/World/OpenXSim/{name}"\n            add_reference_to_st'
        'age(usd_path=representation["uri"], prim_path=prim_path)\n     '
        "       actors[name] = SingleXFormPrim(\n                prim_pa"
        "th=prim_path,\n                name=name,\n                posit"
        'ion=np.asarray(item["pose"]["position"]),\n                orie'
        'ntation=np.asarray(item["pose"]["orientation_wxyz"]),\n        '
        '        scale=np.asarray(item.get("scale", [1, 1, 1])),\n      '
        "      )\n            external_static_names.append(name)\n       "
        '     continue\n        half = primitive["metadata"].get("half_s'
        'ize_m", [0.025, 0.025, 0.025])\n        scale = np.asarray([2 *'
        ' half[i] * item.get("scale", [1, 1, 1])[i] for i in range(3)])'
        '\n        color = np.asarray(primitive["metadata"].get("color_r'
        'gb", [0.8, 0.8, 0.8]))\n        position = np.asarray(item["pos'
        'e"]["position"])\n        orientation = np.asarray(item["pose"]'
        '["orientation_wxyz"])\n        name = item["instance_id"]\n     '
        '   cls = FixedCuboid if item.get("static", False) else Dynamic'
        'Cuboid\n        kwargs = {"prim_path": f"/World/OpenXSim/{name}'
        '", "name": name, "position": position, "orientation": orientat'
        'ion, "scale": scale, "color": color}\n        if cls is Dynamic'
        'Cuboid:\n            kwargs["mass"] = float(asset.get("physical'
        '", {}).get("mass_kg", 0.1))\n            dynamic_names.append(n'
        "ame)\n        actors[name] = world.scene.add(cls(**kwargs))\n   "
        " world.reset()\n    for name in dynamic_names:\n        actors[n"
        "ame].set_linear_velocity(np.zeros(3))\n        actors[name].set"
        "_angular_velocity(np.zeros(3))\n\n    def snapshot(step):\n      "
        '  return {"step": step, "objects": {name: [float(value) for va'
        "lue in actor.get_world_pose()[0]] for name, actor in actors.it"
        'ems()}, "contacts": []}\n\n    trajectory = [snapshot(0)]\n    fo'
        "r step in range(1, args.steps + 1):\n        world.step(render="
        "False)\n        trajectory.append(snapshot(step))\n    runtime_s"
        'tage = Path(args.output).with_name("runtime_scene.usda")\n    w'
        "orld.stage.GetRootLayer().Export(str(runtime_stage))\n    paylo"
        'ad = {\n        "schema": "agenticsim.runtime_evidence.v1",\n   '
        '     "backend": "isaacsim",\n        "reset_ok": True,\n        '
        '"step_ok": True,\n        "steps": args.steps,\n        "action_'
        'interface": action_interface,\n        "action_interface_bound"'
        ': action_interface in {"none", "zero_action"},\n        "succes'
        's_evaluator_bound": bool(success_types) and success_types <= s'
        'upported_success,\n        "task_contract_hash": contract_hash,'
        '\n        "observation_keys": ["contact", "object_pose"],\n     '
        '   "trajectory_mode": "zero_action_physics_rollout",\n        "'
        'trajectory": trajectory,\n        "runtime_stage": str(runtime_'
        'stage),\n        "external_asset_static_references": external_s'
        "tatic_names,\n    }\n    Path(args.output).write_text(json.dumps"
        '(payload, indent=2, sort_keys=True) + "\\n", encoding="utf-8")\n'
        "    print(json.dumps(payload, sort_keys=True))\nfinally:\n    ap"
        "p.close()\n"
    )


class MuJoCoCompiler(BackendCompiler):
    backend = "mujoco"

    def compile(
        self, package: EnvironmentPackage, output_dir: str | Path, *, strict: bool = False
    ) -> CompileResult:
        package.validate()
        output = Path(output_dir).expanduser().resolve() / self.backend
        output.mkdir(parents=True, exist_ok=True)
        artifact = output / "scene.xml"
        assets = _asset_map(package)
        blockers: list[str] = []
        mesh_entries: list[str] = []
        body_entries: list[str] = []
        for obj in package.env.objects:
            asset = assets[obj.asset_id]
            name = _slug(obj.instance_id)
            position = " ".join(f"{value:.9g}" for value in obj.pose.position)
            quat = " ".join(f"{value:.9g}" for value in obj.pose.orientation_wxyz)
            primitive = _primitive(asset)
            if primitive:
                half = primitive.metadata.get("half_size_m") or [0.025, 0.025, 0.025]
                size = " ".join(
                    f"{float(half[index]) * obj.scale[index]:.9g}" for index in range(3)
                )
                color = list(primitive.metadata.get("color_rgb") or [0.8, 0.8, 0.8]) + [1.0]
                rgba = " ".join(f"{float(value):.9g}" for value in color[:4])
                joint = "" if obj.static else f'<freejoint name="{name}_free"/>'
                body_entries.append(
                    (
                        '    <body name="'
                        f"{html.escape(name)}"
                        '" pos="'
                        f"{position}"
                        '" quat="'
                        f"{quat}"
                        '">'
                        f"{joint}"
                        '<geom name="'
                        f"{html.escape(name)}"
                        '_geom" type="box" size="'
                        f"{size}"
                        '" rgba="'
                        f"{rgba}"
                        '"/></body>'
                    )
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
                (
                    '    <mesh name="'
                    f"{html.escape(mesh_name)}"
                    '" file="'
                    f"{html.escape(relative)}"
                    '" scale="'
                    f"{scale}"
                    '"/>'
                )
            )
            joint = "" if obj.static else f'<freejoint name="{name}_free"/>'
            body_entries.append(
                (
                    '    <body name="'
                    f"{html.escape(name)}"
                    '" pos="'
                    f"{position}"
                    '" quat="'
                    f"{quat}"
                    '">'
                    f"{joint}"
                    '<geom name="'
                    f"{html.escape(name)}"
                    '_geom" type="mesh" mesh="'
                    f"{html.escape(mesh_name)}"
                    '"/></body>'
                )
            )
        for region in package.env.regions:
            name = _slug(str(region.get("id") or "region"))
            center = region.get("center") or [0.0, 0.0, 0.0]
            size = region.get("size") or [0.1, 0.1, 0.005]
            half = [float(value) / 2.0 for value in size]
            color = list(region.get("color") or [0.2, 0.4, 0.8, 0.35])
            body_entries.append(
                (
                    '    <body name="'
                    f"{html.escape(name)}"
                    '" pos="'
                    f"{' '.join((str(value) for value in center))}"
                    '"><geom type="box" size="'
                    f"{' '.join((str(value) for value in half))}"
                    '" rgba="'
                    f"{' '.join((str(value) for value in color[:4]))}"
                    '" contype="0" conaffinity="0"/></body>'
                )
            )
        task_text = html.escape(
            json.dumps(package.task.semantic_contract(), sort_keys=True, separators=(",", ":"))
        )
        content = "\n".join(
            [
                f'<mujoco model="{html.escape(package.package_id)}">',
                '  <compiler angle="radian" coordinate="local"/>',
                (
                    '  <option gravity="'
                    f"{' '.join((str(value) for value in package.env.gravity_mps2))}"
                    '" timestep="0.01"/>'
                ),
                "  <asset>",
                *mesh_entries,
                "  </asset>",
                f'  <custom><text name="agenticsim_task_contract" data="{task_text}"/></custom>',
                "  <worldbody>",
                '    <geom name="ground" type="plane" size="3 3 0.1" rgba="0.3 0.3 0.3 1"/>',
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
            runtime_command=(
                "python",
                str(smoke),
                "--model",
                str(artifact),
                "--output",
                str(output / "runtime_evidence.json"),
            ),
            metadata={"artifact_format": "mjcf", "xml_validated": True},
        )
        return self._finish(result, strict=strict)


def _mujoco_smoke_script() -> str:
    return (
        "#!/usr/bin/env python3\nimport argparse, hashlib, json, xml.etr"
        "ee.ElementTree as ET\nfrom pathlib import Path\nimport mujoco\n\np"
        'arser = argparse.ArgumentParser()\nparser.add_argument("--model'
        '", required=True)\nparser.add_argument("--output", required=Tru'
        'e)\nparser.add_argument("--steps", type=int, default=20)\nargs ='
        " parser.parse_args()\nroot = ET.parse(args.model).getroot()\ncus"
        "tom = root.find(\"./custom/text[@name='agenticsim_task_contract"
        '\']")\ncontract = json.loads(custom.get("data")) if custom is no'
        "t None else {}\ncontract_hash = hashlib.sha256(json.dumps(contr"
        'act, sort_keys=True, separators=(",", ":")).encode()).hexdiges'
        't()\naction_interface = str(contract.get("action", {}).get("int'
        'erface", ""))\nsuccess_types = {str(item.get("type", "")) for i'
        'tem in contract.get("success", [])}\nsupported_success = {"stat'
        'e_trace_available", "object_below", "settled"}\nmodel = mujoco.'
        "MjModel.from_xml_path(args.model)\ndata = mujoco.MjData(model)\n"
        "mujoco.mj_resetData(model, data)\nmujoco.mj_forward(model, data"
        ")\ninitial = data.qpos.tolist()\nbody_ids = [index for index in "
        "range(1, model.nbody) if model.body_jntnum[index] > 0]\n\ndef sn"
        "apshot(step):\n    objects = {}\n    for body_id in body_ids:\n  "
        "      name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY"
        ", body_id)\n        objects[name] = [float(value) for value in "
        "data.xpos[body_id]]\n    contacts = []\n    for index in range(d"
        "ata.ncon):\n        contact = data.contact[index]\n        body1"
        " = model.geom_bodyid[contact.geom1]\n        body2 = model.geom"
        "_bodyid[contact.geom2]\n        names = sorted(filter(None, [mu"
        "joco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body1), mujoc"
        "o.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body2)]))\n      "
        '  if names:\n            contacts.append(":".join(names))\n    r'
        'eturn {"step": step, "objects": objects, "contacts": sorted(se'
        "t(contacts))}\n\ntrajectory = [snapshot(0)]\nfor step in range(1,"
        " args.steps + 1):\n    mujoco.mj_step(model, data)\n    mujoco.m"
        "j_forward(model, data)\n    trajectory.append(snapshot(step))\np"
        'ayload = {\n    "schema": "agenticsim.runtime_evidence.v1",\n   '
        ' "backend": "mujoco",\n    "reset_ok": True,\n    "step_ok": Tru'
        'e,\n    "steps": args.steps,\n    "qpos_initial": initial,\n    "'
        'qpos_final": data.qpos.tolist(),\n    "action_interface": actio'
        'n_interface,\n    "action_interface_bound": action_interface in'
        ' {"none", "zero_action"},\n    "success_evaluator_bound": bool('
        'success_types) and success_types <= supported_success,\n    "ta'
        'sk_contract_hash": contract_hash,\n    "observation_keys": ["co'
        'ntact", "object_pose"],\n    "trajectory_mode": "zero_action_ph'
        'ysics_rollout",\n    "trajectory": trajectory,\n}\nPath(args.outp'
        "ut).write_text(json.dumps(payload, indent=2, sort_keys=True) +"
        ' "\\n", encoding="utf-8")\nprint(json.dumps(payload, sort_keys=T'
        "rue))\n"
    )


class SapienCompiler(BackendCompiler):
    backend = "sapien"

    def compile(
        self, package: EnvironmentPackage, output_dir: str | Path, *, strict: bool = False
    ) -> CompileResult:
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
                        "half_size_m": list(
                            primitive.metadata.get("half_size_m") or [0.025, 0.025, 0.025]
                        ),
                        "color_rgb": list(primitive.metadata.get("color_rgb") or [0.8, 0.8, 0.8]),
                    }
                )
            else:
                representation = _representation(
                    asset, backend=self.backend, formats=("urdf", "obj", "stl", "ply", "glb")
                )
                path = _path_uri(representation.uri) if representation else None
                if not representation or path is None or not path.exists():
                    blockers.append(
                        f"{obj.instance_id}: no existing SAPIEN-loadable representation"
                    )
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
            "task_contract": package.task.semantic_contract(),
        }
        artifact.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
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
            runtime_command=(
                "python",
                str(runner),
                "--spec",
                str(artifact),
                "--output",
                str(output / "runtime_evidence.json"),
            ),
            metadata={"artifact_format": "sapien_scene_json", "runner_syntax_validated": True},
        )
        return self._finish(result, strict=strict)


def _sapien_smoke_script() -> str:
    return (
        "#!/usr/bin/env python3\nimport argparse, hashlib, json\nfrom pat"
        "hlib import Path\nimport sapien\n\nparser = argparse.ArgumentPars"
        'er()\nparser.add_argument("--spec", required=True)\nparser.add_a'
        'rgument("--output", required=True)\nparser.add_argument("--step'
        's", type=int, default=20)\nargs = parser.parse_args()\nspec = js'
        'on.loads(Path(args.spec).read_text(encoding="utf-8"))\ncontract'
        ' = spec.get("task_contract", {})\ncontract_hash = hashlib.sha25'
        '6(json.dumps(contract, sort_keys=True, separators=(",", ":")).'
        'encode()).hexdigest()\naction_interface = str(contract.get("act'
        'ion", {}).get("interface", ""))\nsuccess_types = {str(item.get('
        '"type", "")) for item in contract.get("success", [])}\nsupporte'
        'd_success = {"state_trace_available", "object_below", "settled'
        '"}\n\nif hasattr(sapien, "Engine"):\n    engine = sapien.Engine()'
        "\n    scene = engine.create_scene()\nelse:\n    scene = sapien.Sc"
        'ene()\nscene.set_timestep(0.01)\nif hasattr(scene, "set_gravity"'
        '):\n    scene.set_gravity(spec["gravity_mps2"])\nscene.add_groun'
        'd(0.0)\nactors = {}\nfor item in spec["objects"]:\n    if item["k'
        'ind"] == "missing":\n        raise RuntimeError(item["blocker"]'
        ')\n    if item["kind"] == "urdf":\n        loader = scene.create'
        '_urdf_loader()\n        loader.fix_root_link = bool(item["stati'
        'c"])\n        actor = loader.load(item["path"])\n    else:\n     '
        '   builder = scene.create_actor_builder()\n        if item["kin'
        'd"] == "box":\n            half = [item["half_size_m"][i] * ite'
        'm["scale"][i] for i in range(3)]\n            builder.add_box_c'
        "ollision(half_size=half)\n            builder.add_box_visual(ha"
        'lf_size=half, material=item["color_rgb"])\n        else:\n      '
        '      builder.add_visual_from_file(item["path"], scale=item["s'
        'cale"])\n            builder.add_multiple_convex_collisions_fro'
        'm_file(item["path"], scale=item["scale"])\n        actor = buil'
        'der.build_static(name=item["name"]) if item["static"] else bui'
        'lder.build(name=item["name"])\n    actor.set_pose(sapien.Pose(i'
        'tem["position"], item["orientation_wxyz"]))\n    actors[item["n'
        'ame"]] = actor\ninitial = {name: actor.get_pose().p.tolist() fo'
        "r name, actor in actors.items()}\n\ndef snapshot(step):\n    cont"
        'acts = []\n    if hasattr(scene, "get_contacts"):\n        try:\n'
        "            for contact in scene.get_contacts():\n             "
        '   names = sorted(filter(None, [getattr(getattr(contact, "bodi'
        'es", [None, None])[0], "name", None), getattr(getattr(contact,'
        ' "bodies", [None, None])[1], "name", None)]))\n                '
        'if names:\n                    contacts.append(":".join(names))'
        "\n        except Exception:\n            contacts = []\n    retur"
        'n {"step": step, "objects": {name: [float(value) for value in '
        'actor.get_pose().p] for name, actor in actors.items()}, "conta'
        'cts": sorted(set(contacts))}\n\ntrajectory = [snapshot(0)]\nfor s'
        "tep in range(1, args.steps + 1):\n    scene.step()\n    trajecto"
        "ry.append(snapshot(step))\nfinal = {name: actor.get_pose().p.to"
        'list() for name, actor in actors.items()}\npayload = {\n    "sch'
        'ema": "agenticsim.runtime_evidence.v1",\n    "backend": "sapien'
        '",\n    "reset_ok": True,\n    "step_ok": True,\n    "steps": arg'
        's.steps,\n    "object_positions_initial": initial,\n    "object_'
        'positions_final": final,\n    "action_interface": action_interf'
        'ace,\n    "action_interface_bound": action_interface in {"none"'
        ', "zero_action"},\n    "success_evaluator_bound": bool(success_'
        'types) and success_types <= supported_success,\n    "task_contr'
        'act_hash": contract_hash,\n    "observation_keys": ["contact", '
        '"object_pose"],\n    "trajectory_mode": "zero_action_physics_ro'
        'llout",\n    "trajectory": trajectory,\n}\nPath(args.output).writ'
        'e_text(json.dumps(payload, indent=2, sort_keys=True) + "\\n", e'
        'ncoding="utf-8")\nprint(json.dumps(payload, sort_keys=True))\n'
    )


class MetaSimCompiler(BackendCompiler):
    backend = "metasim"

    def compile(
        self, package: EnvironmentPackage, output_dir: str | Path, *, strict: bool = False
    ) -> CompileResult:
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
            paths: dict[str, str | None] = {
                "mesh_path": None,
                "usd_path": None,
                "urdf_path": None,
                "mjcf_path": None,
            }
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
                (
                    "cfg = ScenarioCfg(objects=objects, gravity="
                    f"{package.env.gravity_mps2!r}"
                    ", headless=True, simulator=None)"
                ),
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

    def compile(
        self, package: EnvironmentPackage, output_dir: str | Path, *, strict: bool = False
    ) -> CompileResult:
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
