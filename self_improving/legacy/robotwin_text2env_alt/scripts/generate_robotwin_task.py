#!/usr/bin/env python3
"""Generate a RoboTwin2 task scaffold from Text2Env JSON."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from generate_text2env import load_json, validate_text2env  # noqa: E402


SNAKE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def py(value: Any) -> str:
    return repr(value)


def ident(value: str) -> str:
    if not SNAKE_RE.match(value):
        raise ValueError(f"not a snake_case identifier: {value}")
    return value


def as_tuple(values: List[float]) -> str:
    return "(" + ", ".join(repr(v) for v in values) + ")"


def color3(values: List[float] | None) -> List[float]:
    if not values:
        return [0.8, 0.8, 0.8]
    return [float(v) for v in values[:3]]


def pose_expr(pose: Dict[str, Any]) -> str:
    mode = pose.get("mode")
    qpos = pose.get("qpos", [1, 0, 0, 0])
    if mode == "fixed":
        return f"sapien.Pose({py(pose['xyz'])}, {py(qpos)})"
    if mode == "random_uniform":
        args = [
            f"xlim={py(pose['xlim'])}",
            f"ylim={py(pose['ylim'])}",
            f"zlim={py(pose['zlim'])}",
            f"qpos={py(qpos)}",
            f"rotate_rand={py(bool(pose.get('rotate_rand', False)))}",
            f"rotate_lim={py(pose.get('rotate_lim', [0, 0, 0]))}",
        ]
        if pose.get("ylim_prop"):
            args.append("ylim_prop=True")
        return "rand_pose(" + ", ".join(args) + ")"
    raise ValueError(f"unsupported pose mode: {mode}")


def region_attr(region_id: str) -> str:
    return f"{ident(region_id)}_marker"


def arm_expr(arm_ref: str | None, default: str = "main_arm") -> str:
    if not arm_ref or arm_ref == "auto":
        return default
    if arm_ref.startswith("$"):
        return ident(arm_ref[1:])
    if arm_ref in {"left", "right"}:
        return f'ArmTag("{arm_ref}")'
    raise ValueError(f"unsupported arm reference: {arm_ref}")


def generate_load_actors(data: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    lines.append("    def load_actors(self):")
    lines.append("        self.regions = {}")
    lines.append("        self._initial_positions = {}")
    lines.append("")

    for obj in data["objects"]:
        obj_id = ident(obj["id"])
        attr = ident(obj_id)
        kind = obj["kind"]
        physical = obj.get("physical", {})
        pose = pose_expr(obj["initial_pose"])

        if kind == "box":
            geometry = obj["geometry"]
            half_size = geometry["half_size"]
            color = color3(geometry.get("color"))
            lines.append(f"        self.{attr} = create_box(")
            lines.append("            scene=self,")
            lines.append(f"            pose={pose},")
            lines.append(f"            half_size={as_tuple(half_size)},")
            lines.append(f"            color={as_tuple(color)},")
            lines.append(f'            name="{obj_id}",')
            lines.append(f"            is_static={py(bool(physical.get('is_static', False)))},")
            lines.append("        )")
        elif kind == "asset":
            asset = obj["asset"]
            lines.append(f"        self.{attr} = create_actor(")
            lines.append("            scene=self,")
            lines.append(f"            pose={pose},")
            lines.append(f'            modelname="{asset["modelname"]}",')
            lines.append(f"            convex={py(bool(asset.get('convex', True)))},")
            lines.append(f"            is_static={py(bool(physical.get('is_static', False)))},")
            lines.append(f"            model_id={py(asset.get('model_id', 0))},")
            lines.append("        )")
        elif kind == "urdf":
            asset = obj["asset"]
            lines.append(f"        self.{attr} = create_urdf_obj(")
            lines.append("            scene=self,")
            lines.append(f"            pose={pose},")
            lines.append(f'            modelname="{asset["modelname"]}",')
            lines.append(f"            fix_root_link={py(bool(asset.get('fix_root_link', True)))},")
            lines.append("        )")
        else:
            raise ValueError(f"unsupported object kind for scaffold: {kind}")

        if physical.get("mass_kg"):
            lines.append(f"        self.{attr}.set_mass({py(float(physical['mass_kg']))})")
        if obj.get("protected_region", {}).get("enabled"):
            padding = float(obj.get("protected_region", {}).get("padding_m", 0.05))
            lines.append(f"        self.add_prohibit_area(self.{attr}, padding={py(padding)})")
        lines.append(f'        self._initial_positions["{obj_id}"] = self.{attr}.get_pose().p.copy()')
        lines.append("")

    for region in data.get("regions", []):
        region_id = ident(region["id"])
        center = region["center"]
        size = region["size"]
        color = color3(region.get("color"))
        tolerance = region.get("success_tolerance_m", 0.04)
        marker_expr = "None"
        if region.get("visible", True):
            marker = region_attr(region_id)
            marker_expr = f"self.{marker}"
            half_size = [size[0] / 2, size[1] / 2, size[2] / 2]
            lines.append(f"        self.{marker} = create_box(")
            lines.append("            scene=self,")
            lines.append(f"            pose=sapien.Pose({py(center)}, [1, 0, 0, 0]),")
            lines.append(f"            half_size={as_tuple(half_size)},")
            lines.append(f"            color={as_tuple(color)},")
            lines.append(f'            name="{region_id}",')
            lines.append("            is_static=True,")
            lines.append("        )")
        lines.append(f'        self.regions["{region_id}"] = {{')
        lines.append(f'            "center": np.array({py(center)}, dtype=np.float64),')
        lines.append(f'            "size": np.array({py(size)}, dtype=np.float64),')
        lines.append(f'            "tolerance": {py(float(tolerance))},')
        lines.append(f'            "marker": {marker_expr},')
        lines.append("        }")
        lines.append("")

    if len(lines) == 4:
        lines.append("        pass")
    return lines


def generate_arm_policy(data: Dict[str, Any]) -> List[str]:
    policy = data.get("arm_policy") or {}
    save_as = ident(policy.get("save_as", "main_arm"))
    lines: List[str] = []
    if policy.get("type") == "fixed":
        lines.append(f'        {save_as} = ArmTag("{policy.get("fixed_arm", "left")}")')
    elif policy.get("type") == "by_object_x":
        obj_attr = ident(policy["object"])
        negative = policy.get("negative_arm", "left")
        nonnegative = policy.get("nonnegative_arm", "right")
        lines.append(f"        {obj_attr}_pose = self.{obj_attr}.get_pose().p")
        lines.append(f'        {save_as} = ArmTag("{negative}" if {obj_attr}_pose[0] < 0 else "{nonnegative}")')
    else:
        manipulated = next((obj["id"] for obj in data["objects"] if obj.get("role") == "manipulated"), data["objects"][0]["id"])
        obj_attr = ident(manipulated)
        lines.append(f"        {obj_attr}_pose = self.{obj_attr}.get_pose().p")
        lines.append(f'        {save_as} = ArmTag("left" if {obj_attr}_pose[0] < 0 else "right")')
    lines.append("")
    return lines


def place_target_expr(step: Dict[str, Any], obj_id: str, data: Dict[str, Any]) -> tuple[str, List[str]]:
    target = step.get("target")
    if not target:
        if step.get("target_pose"):
            return pose_expr(step["target_pose"]), []
        raise ValueError("place step requires target or target_pose")

    region_ids = {region["id"] for region in data.get("regions", [])}
    if target in region_ids:
        marker = region_attr(target)
        helper = [
            f'        {target}_marker = self.regions["{target}"].get("marker")',
            f"        if {target}_marker is not None:",
            f"            {target}_pose = {target}_marker.get_functional_point(1)",
            "        else:",
            f'            {target}_center = self.regions["{target}"]["center"]',
            f"            {target}_pose = {target}_center.tolist() + [1, 0, 0, 0]",
        ]
        return f"{target}_pose", helper

    object_ids = {obj["id"] for obj in data["objects"]}
    if target in object_ids:
        return f"self.{ident(target)}.get_pose().p.tolist() + [1, 0, 0, 0]", []
    raise ValueError(f"unknown place target: {target}")


def generate_plan(data: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    for step in data["plan"]:
        op = step["op"]
        arm = arm_expr(step.get("arm"))
        if op == "grasp":
            obj = ident(step["object"])
            lines.append("        self.move(self.grasp_actor(")
            lines.append(f"            self.{obj},")
            lines.append(f"            arm_tag={arm},")
            lines.append(f"            pre_grasp_dis={py(float(step.get('pre_grasp_dis', 0.1)))},")
            lines.append(f"            grasp_dis={py(float(step.get('grasp_dis', 0.0)))},")
            if "target_gripper_pos" in step:
                lines.append(f"            gripper_pos={py(float(step['target_gripper_pos']))},")
            lines.append("        ))")
        elif op == "move_by":
            delta = step.get("delta", [0, 0, 0])
            lines.append("        self.move(self.move_by_displacement(")
            lines.append(f"            arm_tag={arm},")
            lines.append(f"            x={py(float(delta[0]))},")
            lines.append(f"            y={py(float(delta[1]))},")
            lines.append(f"            z={py(float(delta[2]))},")
            lines.append(f'            move_axis="{step.get("axis", "world")}",')
            lines.append("        ))")
        elif op == "place":
            obj = ident(step["object"])
            target_expr, helper = place_target_expr(step, obj, data)
            lines.extend(helper)
            lines.append("        self.move(self.place_actor(")
            lines.append(f"            self.{obj},")
            lines.append(f"            arm_tag={arm},")
            lines.append(f"            target_pose={target_expr},")
            if "functional_point_id" in step and step["functional_point_id"] is not None:
                lines.append(f"            functional_point_id={int(step['functional_point_id'])},")
            elif step.get("target") in {region["id"] for region in data.get("regions", [])}:
                lines.append("            functional_point_id=0,")
            lines.append(f"            pre_dis={py(float(step.get('pre_dis', 0.1)))},")
            lines.append(f"            dis={py(float(step.get('dis', 0.02)))},")
            lines.append(f"            is_open={py(bool(step.get('is_open', True)))},")
            if step.get("pre_dis_axis"):
                lines.append(f'            pre_dis_axis="{step["pre_dis_axis"]}",')
            elif step.get("target") in {region["id"] for region in data.get("regions", [])}:
                lines.append('            pre_dis_axis="fp",')
            lines.append("        ))")
        elif op == "open_gripper":
            lines.append(f"        self.move(self.open_gripper(arm_tag={arm}))")
        elif op == "close_gripper":
            pos = float(step.get("target_gripper_pos", 0.0))
            lines.append(f"        self.move(self.close_gripper(arm_tag={arm}, pos={py(pos)}))")
        elif op == "back_to_origin":
            lines.append(f"        self.move(self.back_to_origin(arm_tag={arm}))")
        elif op == "wait":
            lines.append(f"        self.delay({int(step.get('duration_steps', 20))})")
        else:
            raise ValueError(f"unsupported plan op: {op}")
        lines.append("")
    return lines


def placeholder_expr(value: str) -> str:
    if isinstance(value, str) and value.startswith("$"):
        return f"str({ident(value[1:])})"
    return py(value)


def generate_play_once(data: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    lines.append("    def play_once(self):")
    lines.extend(generate_arm_policy(data))
    lines.extend(generate_plan(data))
    lines.append('        self.info["info"] = {')
    for key, value in data["language"]["placeholders"].items():
        lines.append(f"            {py(key)}: {placeholder_expr(value)},")
    lines.append("        }")
    lines.append("        return self.info")
    return lines


def generate_success(data: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    lines.append("    def check_success(self):")
    lines.append("        checks = []")
    for pred in data["success"]:
        ptype = pred["type"]
        if ptype == "in_region":
            obj = ident(pred["object"])
            region = ident(pred["region"])
            tolerance = float(pred.get("tolerance_m", 0.04))
            lines.append(f"        {obj}_pose = self.{obj}.get_pose().p")
            lines.append(f'        {region}_region = self.regions["{region}"]')
            lines.append(f"        {region}_center = {region}_region[\"center\"]")
            lines.append(f"        {region}_half = {region}_region[\"size\"][:2] / 2.0 + {py(tolerance)}")
            lines.append(f"        checks.append(np.all(np.abs({obj}_pose[:2] - {region}_center[:2]) <= {region}_half))")
        elif ptype == "max_displacement":
            obj = ident(pred["object"])
            threshold = float(pred.get("threshold_m", 0.03))
            lines.append(f'        {obj}_delta = np.linalg.norm(self.{obj}.get_pose().p - self._initial_positions["{obj}"])')
            lines.append(f"        checks.append({obj}_delta <= {py(threshold)})")
        elif ptype == "grippers_open":
            lines.append("        checks.append(self.is_left_gripper_open() and self.is_right_gripper_open())")
        elif ptype == "near":
            obj = ident(pred["object"])
            target = ident(pred["target_object"])
            threshold = float(pred.get("threshold_m", pred.get("tolerance_m", 0.05)))
            lines.append(f"        checks.append(np.linalg.norm(self.{obj}.get_pose().p - self.{target}.get_pose().p) <= {py(threshold)})")
        elif ptype == "contact":
            obj = ident(pred["object"])
            target = ident(pred["target_object"])
            lines.append(f"        checks.append(self.check_actors_contact(self.{obj}.get_name(), self.{target}.get_name()))")
        elif ptype == "no_contact":
            obj = ident(pred["object"])
            target = ident(pred["target_object"])
            lines.append(f"        checks.append(not self.check_actors_contact(self.{obj}.get_name(), self.{target}.get_name()))")
        else:
            lines.append(f"        # TODO: unsupported success predicate in scaffold: {ptype}")
            lines.append("        checks.append(False)")
    lines.append("        return all(checks)")
    return lines


def generate_task_py(data: Dict[str, Any]) -> str:
    task_name = ident(data["task_name"])
    lines: List[str] = [
        "from ._base_task import Base_Task",
        "from .utils import *",
        "import numpy as np",
        "import sapien",
        "",
        "",
        f"class {task_name}(Base_Task):",
        "",
        "    def setup_demo(self, **kwags):",
        "        super()._init_task_env_(**kwags)",
        "",
    ]
    lines.extend(generate_load_actors(data))
    lines.append("")
    lines.extend(generate_play_once(data))
    lines.append("")
    lines.extend(generate_success(data))
    lines.append("")
    return "\n".join(lines)


def generate_instruction_json(data: Dict[str, Any]) -> Dict[str, Any]:
    language = data["language"]
    return {
        "full_description": language.get("full_description", data.get("language_instruction", "")),
        "schema": language.get("schema", ""),
        "preference": language.get("preference", "Short imperative instructions."),
        "seen": language.get("seen_templates", []),
        "unseen": language.get("unseen_templates", []),
    }


def output_paths(out_root: Path, task_name: str) -> tuple[Path, Path, Path]:
    task_py = out_root / "envs" / f"{task_name}.py"
    instruction = out_root / "description" / "task_instruction" / f"{task_name}.json"
    manifest = out_root / "manifest.json"
    return task_py, instruction, manifest


def command_generate(args: argparse.Namespace) -> int:
    spec_path = Path(args.spec)
    data = load_json(spec_path)
    issues = validate_text2env(data, Path(args.robotwin_root) if args.robotwin_root else None)
    errors = [item for item in issues if item["severity"] == "error"]
    if errors and not args.allow_errors:
        print(json.dumps({"valid": False, "issues": errors}, indent=2, ensure_ascii=False), file=sys.stderr)
        return 1
    if data.get("status") != "ready_for_scaffold" and not args.allow_draft:
        print(f"ERROR: spec status is {data.get('status')}; pass --allow-draft to scaffold anyway", file=sys.stderr)
        return 1

    task_name = ident(data["task_name"])
    out_root = Path(args.out_root) if args.out_root else ROOT / "generated" / "robotwin_tasks" / task_name
    task_py, instruction_path, manifest_path = output_paths(out_root, task_name)
    task_py.parent.mkdir(parents=True, exist_ok=True)
    instruction_path.parent.mkdir(parents=True, exist_ok=True)

    task_py.write_text(generate_task_py(data), encoding="utf-8")
    instruction_path.write_text(json.dumps(generate_instruction_json(data), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest = {
        "task_name": task_name,
        "source_spec": str(spec_path),
        "generated_files": [
            str(task_py.relative_to(out_root)),
            str(instruction_path.relative_to(out_root)),
        ],
        "preferred_task_config": data.get("generation_targets", {}).get("preferred_task_config", "demo_smoke"),
        "run_command": f"bash collect_data.sh {task_name} {data.get('generation_targets', {}).get('preferred_task_config', 'demo_smoke')} 0",
        "notes": data.get("notes", ""),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"out_root": str(out_root), **manifest}, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate RoboTwin2 task scaffold from Text2Env JSON")
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate", help="generate envs/<task>.py and instruction JSON")
    gen.add_argument("spec", help="Path to Text2Env JSON")
    gen.add_argument("--out-root", help="Output root. Defaults to generated/robotwin_tasks/<task_name>")
    gen.add_argument("--robotwin-root", help="Optional local RoboTwin root for asset checks")
    gen.add_argument("--allow-draft", action="store_true", help="Allow scaffolding non-ready specs")
    gen.add_argument("--allow-errors", action="store_true", help="Allow scaffolding even when Text2Env validation has errors")
    gen.set_defaults(func=command_generate)
    return parser


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
