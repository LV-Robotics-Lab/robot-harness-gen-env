#!/usr/bin/env python3
"""Utilities for the SceneSmith-lite Text2Env flow.

This script intentionally has no third-party dependencies. It does not call an
LLM; it provides local checks and template bootstrapping around the prompt-based
Designer/Critic/Orchestrator workflow.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = ROOT / "examples" / "tabletop_tasks"
SCHEMA_VERSION = "text2env.tabletop.v0"
SNAKE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
PLACEHOLDER_RE = re.compile(r"{([^}]+)}")


Issue = Dict[str, str]


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def issue(level: str, code: str, path: str, message: str) -> Issue:
    return {
        "severity": level,
        "code": code,
        "path": path,
        "message": message,
    }


def as_set(items: list[dict[str, Any]], key: str) -> tuple[set[str], list[str]]:
    values: list[str] = []
    duplicates: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = item.get(key)
        if isinstance(value, str):
            values.append(value)
            if value in seen:
                duplicates.append(value)
            seen.add(value)
    return set(values), duplicates


def inside_range(value: float, limits: Any) -> bool:
    return (
        isinstance(limits, list)
        and len(limits) == 2
        and isinstance(limits[0], (int, float))
        and isinstance(limits[1], (int, float))
        and limits[0] <= value <= limits[1]
    )


def check_pose_bounds(data: dict[str, Any], issues: list[Issue]) -> None:
    workspace = data.get("workspace", {})
    bounds = workspace.get("bounds", {}) if isinstance(workspace, dict) else {}
    xlim = bounds.get("x")
    ylim = bounds.get("y")
    zlim = bounds.get("z")

    for idx, obj in enumerate(data.get("objects", [])):
        pose = obj.get("initial_pose", {})
        if not isinstance(pose, dict):
            continue
        path = f"objects[{idx}].initial_pose"
        mode = pose.get("mode")
        if mode == "fixed" and isinstance(pose.get("xyz"), list) and len(pose["xyz"]) == 3:
            x, y, z = pose["xyz"]
            if not inside_range(x, xlim):
                issues.append(issue("warning", "pose_x_out_of_bounds", path, f"x={x} is outside workspace x bounds"))
            if not inside_range(y, ylim):
                issues.append(issue("warning", "pose_y_out_of_bounds", path, f"y={y} is outside workspace y bounds"))
            if not inside_range(z, zlim):
                issues.append(issue("warning", "pose_z_out_of_bounds", path, f"z={z} is outside workspace z bounds"))
        elif mode == "random_uniform":
            for axis, limits, workspace_limits in (
                ("x", pose.get("xlim"), xlim),
                ("y", pose.get("ylim"), ylim),
                ("z", pose.get("zlim"), zlim),
            ):
                if not isinstance(limits, list) or len(limits) != 2:
                    issues.append(issue("error", f"missing_{axis}lim", path, f"random_uniform pose requires {axis}lim"))
                    continue
                if not inside_range(limits[0], workspace_limits) or not inside_range(limits[1], workspace_limits):
                    issues.append(
                        issue(
                            "warning",
                            f"{axis}lim_out_of_bounds",
                            path,
                            f"{axis}lim={limits} is not fully inside workspace {axis} bounds",
                        )
                    )


def check_assets(data: dict[str, Any], issues: list[Issue], robotwin_root: Path | None) -> None:
    if robotwin_root is None:
        return
    objects_root = robotwin_root / "assets" / "objects"
    if not objects_root.exists():
        issues.append(issue("warning", "robotwin_assets_missing", "robotwin_root", f"{objects_root} does not exist"))
        return
    for idx, obj in enumerate(data.get("objects", [])):
        if obj.get("kind") not in {"asset", "urdf"}:
            continue
        modelname = obj.get("asset", {}).get("modelname")
        if not modelname:
            issues.append(issue("error", "missing_asset_modelname", f"objects[{idx}].asset", "asset object needs modelname"))
            continue
        if not (objects_root / modelname).exists():
            issues.append(
                issue(
                    "error",
                    "asset_not_found",
                    f"objects[{idx}].asset.modelname",
                    f"{modelname} not found under {objects_root}",
                )
            )


def validate_text2env(data: dict[str, Any], robotwin_root: Path | None = None) -> list[Issue]:
    issues: list[Issue] = []
    required = {
        "schema_version",
        "task_name",
        "language_instruction",
        "intent",
        "status",
        "workspace",
        "objects",
        "plan",
        "success",
        "language",
    }
    for key in sorted(required - set(data)):
        issues.append(issue("error", "missing_required", key, f"missing required field: {key}"))

    if data.get("schema_version") != SCHEMA_VERSION:
        issues.append(issue("error", "bad_schema_version", "schema_version", f"expected {SCHEMA_VERSION}"))

    task_name = data.get("task_name")
    if not isinstance(task_name, str) or not SNAKE_RE.match(task_name):
        issues.append(issue("error", "bad_task_name", "task_name", "task_name must be snake_case"))

    objects = data.get("objects", [])
    regions = data.get("regions", [])
    if not isinstance(objects, list) or not objects:
        issues.append(issue("error", "objects_empty", "objects", "objects must be a non-empty list"))
        objects = []
    if not isinstance(regions, list):
        issues.append(issue("error", "regions_not_list", "regions", "regions must be a list"))
        regions = []

    object_ids, duplicate_objects = as_set(objects, "id")
    region_ids, duplicate_regions = as_set(regions, "id")
    for obj_id in duplicate_objects:
        issues.append(issue("error", "duplicate_object_id", "objects", f"duplicate object id: {obj_id}"))
    for region_id in duplicate_regions:
        issues.append(issue("error", "duplicate_region_id", "regions", f"duplicate region id: {region_id}"))

    valid_targets = object_ids | region_ids
    for idx, obj in enumerate(objects):
        obj_path = f"objects[{idx}]"
        obj_id = obj.get("id", "<missing>")
        if obj.get("role") == "manipulated" and not obj.get("physical", {}).get("graspable"):
            issues.append(issue("error", "manipulated_not_graspable", obj_path, f"{obj_id} must be graspable"))
        if data.get("status") == "ready_for_scaffold" and obj.get("kind") == "urdf":
            issues.append(issue("error", "ready_uses_urdf", obj_path, "ready_for_scaffold cannot depend on urdf in v0"))
        if obj.get("kind") == "asset" and "asset" not in obj:
            issues.append(issue("error", "asset_missing_config", obj_path, f"{obj_id} kind=asset needs asset config"))

    arm_policy = data.get("arm_policy", {})
    if isinstance(arm_policy, dict) and arm_policy.get("object") and arm_policy["object"] not in object_ids:
        issues.append(issue("error", "bad_arm_policy_object", "arm_policy.object", "arm_policy object does not exist"))

    for idx, step in enumerate(data.get("plan", [])):
        step_path = f"plan[{idx}]"
        obj_id = step.get("object")
        target = step.get("target")
        if obj_id and obj_id not in object_ids:
            issues.append(issue("error", "bad_plan_object", step_path, f"plan object does not exist: {obj_id}"))
        if target and target not in valid_targets:
            issues.append(issue("error", "bad_plan_target", step_path, f"plan target does not exist: {target}"))
        if step.get("op") == "place" and not target and not step.get("target_pose"):
            issues.append(issue("error", "place_missing_target", step_path, "place step needs target or target_pose"))

    has_release = any(step.get("op") == "place" and step.get("is_open", True) for step in data.get("plan", []))
    has_grippers_open = any(pred.get("type") == "grippers_open" for pred in data.get("success", []))
    if has_release and not has_grippers_open:
        issues.append(issue("warning", "missing_grippers_open", "success", "released-object task should check grippers_open"))

    for idx, pred in enumerate(data.get("success", [])):
        pred_path = f"success[{idx}]"
        obj_id = pred.get("object")
        region = pred.get("region")
        target_object = pred.get("target_object")
        if obj_id and obj_id not in object_ids:
            issues.append(issue("error", "bad_success_object", pred_path, f"success object does not exist: {obj_id}"))
        if region and region not in region_ids:
            issues.append(issue("error", "bad_success_region", pred_path, f"success region does not exist: {region}"))
        if target_object and target_object not in object_ids:
            issues.append(
                issue("error", "bad_success_target_object", pred_path, f"target_object does not exist: {target_object}")
            )

    language = data.get("language", {})
    placeholders = set(language.get("placeholders", {}).keys()) if isinstance(language, dict) else set()
    used: set[str] = set()
    if isinstance(language, dict):
        for key in ("seen_templates", "unseen_templates"):
            for template in language.get(key, []):
                if isinstance(template, str):
                    used.update("{" + name + "}" for name in PLACEHOLDER_RE.findall(template))
    for missing in sorted(used - placeholders):
        issues.append(issue("error", "unbound_placeholder", "language.placeholders", f"{missing} is used but not bound"))
    for unused in sorted(placeholders - used):
        issues.append(issue("note", "unused_placeholder", "language.placeholders", f"{unused} is bound but not used"))

    if data.get("intent") == "articulated_place" and data.get("status") == "ready_for_scaffold":
        issues.append(
            issue(
                "error",
                "articulated_ready_for_scaffold",
                "status",
                "articulated_place should be draft_requires_articulation in v0",
            )
        )

    check_pose_bounds(data, issues)
    check_assets(data, issues, robotwin_root)
    return issues


def command_check(args: argparse.Namespace) -> int:
    path = Path(args.path)
    data = load_json(path)
    robotwin_root = Path(args.robotwin_root) if args.robotwin_root else None
    issues = validate_text2env(data, robotwin_root)
    errors = [item for item in issues if item["severity"] == "error"]
    warnings = [item for item in issues if item["severity"] == "warning"]
    report = {
        "path": str(path),
        "valid": not errors,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "issues": issues,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if errors else 0


def slug_from_instruction(instruction: str) -> str:
    words = re.findall(r"[a-zA-Z0-9]+", instruction.lower())
    stop = {"the", "a", "an", "to", "from", "without", "with", "and", "of", "in", "on", "into"}
    kept = [word for word in words if word not in stop][:6]
    slug = "_".join(kept) or "text2env_task"
    if not re.match(r"^[a-z]", slug):
        slug = "task_" + slug
    return slug


def command_from_template(args: argparse.Namespace) -> int:
    template_name = args.template
    if template_name.endswith(".json"):
        template_path = Path(template_name)
    else:
        template_path = EXAMPLES_DIR / f"{template_name}.json"
    data = load_json(template_path)
    task_name = args.task_name or data.get("task_name") or slug_from_instruction(args.instruction)
    if not SNAKE_RE.match(task_name):
        raise ValueError(f"task name must be snake_case: {task_name}")

    result = copy.deepcopy(data)
    result["task_name"] = task_name
    result["language_instruction"] = args.instruction
    if args.status:
        result["status"] = args.status
    targets = result.setdefault("generation_targets", {})
    targets["robotwin_task_file"] = f"envs/{task_name}.py"
    targets["instruction_file"] = f"description/task_instruction/{task_name}.json"
    targets.setdefault("preferred_task_config", "demo_smoke")

    out = Path(args.out) if args.out else EXAMPLES_DIR / f"{task_name}.json"
    write_json(out, result)
    print(f"WROTE {out}")
    return 0


def command_init_run(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    brief = {
        "user_task": args.instruction,
        "designer_prompt": str(ROOT / "prompts" / "text2env_designer.md"),
        "critic_prompt": str(ROOT / "prompts" / "text2env_critic.md"),
        "orchestrator_prompt": str(ROOT / "prompts" / "text2env_orchestrator.md"),
        "schema_doc": str(ROOT / "schemas" / "text2env_schema_v0.md"),
        "reference_example": str(EXAMPLES_DIR / "move_object_between_zones.json"),
    }
    write_json(run_dir / "run_brief.json", brief)
    print(f"WROTE {run_dir / 'run_brief.json'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SceneSmith-lite Text2Env utilities")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="validate core Text2Env references and v0 constraints")
    check.add_argument("path", help="Path to Text2Env JSON")
    check.add_argument("--robotwin-root", help="Optional RoboTwin root for local asset existence checks")
    check.set_defaults(func=command_check)

    templ = sub.add_parser("from-template", help="copy a Text2Env example template and update task metadata")
    templ.add_argument("--template", required=True, help="Template name under examples/tabletop_tasks or path to JSON")
    templ.add_argument("--instruction", required=True, help="Natural-language instruction")
    templ.add_argument("--task-name", help="snake_case task name")
    templ.add_argument("--status", choices=["ready_for_scaffold", "draft_requires_review", "draft_requires_articulation"])
    templ.add_argument("--out", help="Output path")
    templ.set_defaults(func=command_from_template)

    init_run = sub.add_parser("init-run", help="create a small run brief for the prompt-based agent flow")
    init_run.add_argument("--instruction", required=True, help="Natural-language instruction")
    init_run.add_argument("--run-dir", required=True, help="Directory for run_brief.json")
    init_run.set_defaults(func=command_init_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
