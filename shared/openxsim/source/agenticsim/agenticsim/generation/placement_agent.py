"""Placement-only Text2Env planner for RoboTwin-style tabletop tasks.

The agent turns a compact natural-language instruction into the Text2Env v0
schema used by the local RoboTwin scaffold generator. It deliberately treats
missing assets as blockers instead of invoking any asset-generation path.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "text2env.tabletop.v0"

COLOR_RGB: dict[str, list[float]] = {
    "red": [1.0, 0.0, 0.0],
    "green": [0.0, 0.8, 0.1],
    "blue": [0.0, 0.35, 1.0],
    "yellow": [1.0, 0.82, 0.0],
    "white": [0.92, 0.92, 0.92],
    "black": [0.02, 0.02, 0.02],
}

ASSET_ALIASES: dict[str, tuple[str, int]] = {
    "bowl": ("002_bowl", 3),
    "blue bowl": ("002_bowl", 3),
    "can": ("071_can", 0),
    "cola can": ("071_can", 0),
    "coke can": ("071_can", 0),
    "soda can": ("071_can", 0),
    "bottle": ("114_bottle", 1),
    "cola bottle": ("114_bottle", 1),
    "coke bottle": ("114_bottle", 1),
    "water bottle": ("114_bottle", 1),
    "cup": ("021_cup", 0),
    "drawer": ("036_cabinet", 0),
    "cabinet": ("036_cabinet", 0),
    "basket": ("110_basket", 1),
    "mouse": ("047_mouse", 0),
    "block_asset": ("108_block", 0),
}

ROBOTWIN_ASSET_ROOTS: tuple[Path, ...] = (
    Path("assets/vendor/robotwin/assets/objects"),
    Path("assets/vendor/robotwin-close/assets/objects"),
    Path("third_party/RoboTwin/assets/objects"),
    Path("third_party/RoboTwin_Close/assets/objects"),
    Path("third_party/RoboTwin/description/objects_description"),
    Path("external/RoboTwin/assets/objects"),
    Path("external/RoboTwin/description/objects_description"),
)


@dataclass(frozen=True)
class AssetCheck:
    object_id: str
    requirement: str
    kind: str
    resolved: bool
    source: str
    path: str | None = None
    blocker: str | None = None


@dataclass(frozen=True)
class PlacementResult:
    spec: dict[str, Any]
    manifest: dict[str, Any]


class PlacementError(ValueError):
    """Raised when the instruction cannot be represented as a placement task."""


def normalize_instruction(instruction: str) -> str:
    return re.sub(r"\s+", " ", instruction.strip().lower())


def snake_id(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    value = re.sub(r"_+", "_", value)
    if not value or not value[0].isalpha():
        value = f"task_{value}"
    return value


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(re.search(rf"\b{re.escape(term)}\b", text) for term in terms)


def resolve_robotwin_asset(modelname: str, repo_root: Path | None = None) -> AssetCheck:
    root = repo_root or Path.cwd()
    for relative_root in ROBOTWIN_ASSET_ROOTS:
        candidate = root / relative_root / modelname
        if candidate.exists():
            return AssetCheck(
                object_id="",
                requirement=modelname,
                kind="robotwin_asset",
                resolved=True,
                source=str(relative_root),
                path=str(candidate),
            )
    return AssetCheck(
        object_id="",
        requirement=modelname,
        kind="robotwin_asset",
        resolved=False,
        source="robotwin_asset_roots",
        blocker=f"missing existing asset: {modelname}",
    )


def _with_object_id(check: AssetCheck, object_id: str) -> AssetCheck:
    return AssetCheck(
        object_id=object_id,
        requirement=check.requirement,
        kind=check.kind,
        resolved=check.resolved,
        source=check.source,
        path=check.path,
        blocker=check.blocker,
    )


def primitive_box_check(object_id: str, color: str) -> AssetCheck:
    return AssetCheck(
        object_id=object_id,
        requirement=f"procedural_box:{color}",
        kind="robotwin_primitive",
        resolved=True,
        source="create_box",
    )


def logical_region_check(region_id: str) -> AssetCheck:
    return AssetCheck(
        object_id=region_id,
        requirement="logical_region_marker",
        kind="robotwin_primitive",
        resolved=True,
        source="create_box",
    )


def _workspace() -> dict[str, Any]:
    return {
        "type": "tabletop",
        "surface": "table",
        "robot_setup": "dual_arm",
        "bounds": {"x": [-0.4, 0.4], "y": [-0.3, 0.25], "z": [0.74, 1.1]},
        "table": {"height_m": 0.74},
    }


def _box_object(
    *,
    object_id: str,
    color: str,
    role: str = "manipulated",
    xlim: list[float] | None = None,
    ylim: list[float] | None = None,
) -> dict[str, Any]:
    return {
        "id": object_id,
        "role": role,
        "kind": "box",
        "category": "block",
        "geometry": {
            "shape": "box",
            "half_size": [0.025, 0.025, 0.025],
            "color": COLOR_RGB.get(color, [0.8, 0.8, 0.8]),
            "visible": True,
        },
        "initial_pose": {
            "mode": "random_uniform",
            "xlim": xlim or [-0.28, -0.12],
            "ylim": ylim or [-0.05, 0.08],
            "zlim": [0.766, 0.766],
            "qpos": [1, 0, 0, 0],
            "rotate_rand": True,
            "rotate_lim": [0, 0, 0.4],
        },
        "physical": {
            "mass_kg": 0.05,
            "graspable": True,
            "movable": True,
            "is_static": False,
        },
        "protected_region": {"enabled": True, "padding_m": 0.07},
        "description_key": f"{color} block",
    }


def _asset_object(
    *,
    object_id: str,
    role: str,
    modelname: str,
    model_id: int,
    category: str,
    xyz: list[float],
    qpos: list[float] | None = None,
    graspable: bool = True,
    movable: bool = True,
    mass_kg: float = 0.05,
    is_static: bool = False,
    padding_m: float = 0.06,
) -> dict[str, Any]:
    return {
        "id": object_id,
        "role": role,
        "kind": "asset",
        "category": category,
        "asset": {"modelname": modelname, "model_id": model_id, "convex": True},
        "initial_pose": {
            "mode": "fixed",
            "xyz": xyz,
            "qpos": qpos or [1, 0, 0, 0],
        },
        "physical": {
            "mass_kg": mass_kg,
            "graspable": graspable,
            "movable": movable,
            "is_static": is_static,
        },
        "protected_region": {"enabled": True, "padding_m": padding_m},
        "description_key": f"{modelname}/base{model_id}",
    }


def _zone(region_id: str, color: str, center: list[float], kind: str = "target_zone") -> dict[str, Any]:
    rgba = COLOR_RGB.get(color, [0.8, 0.8, 0.8]) + [0.45]
    return {
        "id": region_id,
        "type": kind,
        "center": center,
        "size": [0.16, 0.12, 0.004],
        "color": rgba,
        "visible": True,
        "success_tolerance_m": 0.04,
    }


def _base_spec(task_name: str, instruction: str, intent: str = "place_in_region") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "task_name": task_name,
        "language_instruction": instruction,
        "intent": intent,
        "status": "ready_for_scaffold",
        "workspace": _workspace(),
        "objects": [],
        "regions": [],
        "arm_policy": {
            "type": "by_object_x",
            "object": "",
            "negative_arm": "left",
            "nonnegative_arm": "right",
            "save_as": "main_arm",
        },
        "plan": [],
        "success": [],
        "language": {
            "full_description": instruction,
            "schema": "",
            "preference": "Short imperative instructions.",
            "placeholders": {},
            "seen_templates": [],
            "unseen_templates": [],
        },
        "randomization": {
            "object_pose": True,
            "lighting": False,
            "texture": False,
            "cluttered_table": False,
            "table_height": False,
        },
        "validation_constraints": [
            "no_initial_object_collision",
            "target_reachable_by_robot",
            "success_condition_sim_state_observable",
            "task_objects_stable",
            "protected_regions_defined",
            "all_placeholders_bound",
        ],
        "generation_targets": {
            "robotwin_task_file": f"envs/{task_name}.py",
            "instruction_file": f"description/task_instruction/{task_name}.json",
            "preferred_task_config": "demo_smoke",
        },
        "notes": "Generated by AgenticSim placement_agent without invoking asset generation.",
    }


def _place_plan(object_id: str, target_region: str) -> list[dict[str, Any]]:
    return [
        {
            "op": "grasp",
            "object": object_id,
            "arm": "$main_arm",
            "pre_grasp_dis": 0.09,
            "grasp_dis": 0.0,
            "comment": "Pick the object with the arm selected from its x position.",
        },
        {
            "op": "move_by",
            "arm": "$main_arm",
            "delta": [0.0, 0.0, 0.08],
            "axis": "world",
            "comment": "Lift before moving to the target region.",
        },
        {
            "op": "place",
            "object": object_id,
            "arm": "$main_arm",
            "target": target_region,
            "pre_dis": 0.05,
            "dis": 0.0,
            "is_open": True,
            "comment": "Place the object in the target region.",
        },
        {
            "op": "move_by",
            "arm": "$main_arm",
            "delta": [0.0, 0.0, 0.06],
            "axis": "world",
            "comment": "Move away after release.",
        },
    ]


def _asset_to_basket(
    instruction: str,
    *,
    object_id: str,
    object_label: str,
    modelname: str,
    model_id: int,
    category: str,
    qpos: list[float],
    repo_root: Path | None,
    lift_before_place: bool = True,
    basket_recovery: bool = False,
) -> PlacementResult:
    basket_model, basket_model_id = ASSET_ALIASES["basket"]
    task_name = snake_id(f"place_{object_id}_in_basket")
    spec = _base_spec(task_name, instruction, intent="place_in_container")
    spec["objects"] = [
        _asset_object(
            object_id=object_id,
            role="manipulated",
            modelname=modelname,
            model_id=model_id,
            category=category,
            xyz=[-0.225, 0.05, 0.741],
            qpos=qpos,
            mass_kg=0.01,
            padding_m=0.1,
        ),
        _asset_object(
            object_id="basket",
            role="container",
            modelname=basket_model,
            model_id=basket_model_id,
            category="basket",
            xyz=[0.02, -0.06, 0.741],
            qpos=[0.5, 0.5, 0.5, 0.5],
            graspable=False,
            movable=True,
            mass_kg=0.5,
            padding_m=0.05,
        ),
    ]
    spec["arm_policy"] = {"type": "fixed", "fixed_arm": "left", "save_as": "main_arm"}
    spec["plan"] = [
        {
            "op": "grasp",
            "object": object_id,
            "arm": "$main_arm",
            "pre_grasp_dis": 0.05,
            "grasp_dis": 0.0,
            "comment": f"Pick the existing RoboTwin {object_label} asset.",
        },
    ]
    if lift_before_place:
        spec["plan"].append(
            {
                "op": "move_by",
                "arm": "$main_arm",
                "delta": [0.0, 0.0, 0.15],
                "axis": "world",
                "comment": "Lift before moving over the basket.",
            }
        )
    place_step: dict[str, Any] = {
        "op": "place",
        "object": object_id,
        "arm": "$main_arm",
        "target": "basket",
        "target_functional_point_id": 0,
        "target_quat_by_arm": {
            "left": [-1, 0, 0, 0],
            "right": [0.05, 0, 0, 0.99],
        },
        "pre_dis": 0.1,
        "dis": 0.02,
        "is_open": False,
        "constrain": "free",
        "comment": "Use the basket functional point instead of a procedural target marker.",
    }
    if basket_recovery:
        place_step["recovery"] = {"type": "can_basket"}
    spec["plan"].append(place_step)
    if not basket_recovery:
        spec["plan"].extend(
            [
                {
                    "op": "open_gripper",
                    "arm": "$main_arm",
                    "comment": "Release the object after placing it in the basket.",
                },
                {
                    "op": "move_by",
                    "arm": "$main_arm",
                    "delta": [0.0, 0.0, 0.12],
                    "axis": "world",
                    "comment": "Move away after release.",
                },
            ]
        )
    spec["success"] = [
        {
            "type": "near",
            "object": object_id,
            "target_object": "basket",
            "threshold_m": 0.15,
            "comment": f"The {object_label} should end inside or near the basket footprint.",
        },
        {
            "type": "contact",
            "object": object_id,
            "target_object": "basket",
            "comment": f"The {object_label} should contact the basket.",
        },
    ]
    if not basket_recovery:
        spec["success"].append(
            {"type": "grippers_open", "required": True, "comment": "The robot should release the object."}
        )
    spec["validation_constraints"].extend(["assets_exist_in_robotwin", "target_object_functional_point_bound"])
    spec["language"] = {
        "full_description": instruction,
        "schema": "{A} is the manipulated object, {B} is the basket, {a} is the arm used to move the object.",
        "preference": "Short imperative instructions.",
        "placeholders": {
            "{A}": f"{modelname}/base{model_id}",
            "{B}": f"{basket_model}/base{basket_model_id}",
            "{a}": "$main_arm",
        },
        "seen_templates": [
            "Place {A} in {B}.",
            "Use {a} to put {A} into {B}.",
            "Move {A} into {B}.",
        ],
        "unseen_templates": [
            "Drop {A} into {B}.",
            "Carry {A} to {B}.",
            "Set {A} inside {B}.",
        ],
    }
    spec["notes"] = (
        "Generated by AgenticSim placement_agent using existing RoboTwin object assets only; "
        "no procedural manipulated object or asset generation path is used."
    )
    checks = [
        _with_object_id(resolve_robotwin_asset(modelname, repo_root), object_id),
        _with_object_id(resolve_robotwin_asset(basket_model, repo_root), "basket"),
    ]
    return _finalize(spec, checks)


def _block_to_zone(
    instruction: str,
    *,
    block_color: str,
    target_region: str,
    target_color: str,
    source_region: str | None,
    repo_root: Path | None,
    distractor_bowl: bool,
) -> PlacementResult:
    block_id = f"{block_color}_block"
    task_name = snake_id(f"move_{block_id}_to_{target_region}")
    spec = _base_spec(task_name, instruction)
    spec["objects"].append(_box_object(object_id=block_id, color=block_color))
    spec["regions"].append(_zone(target_region, target_color, [0.05, -0.1, 0.742]))

    if source_region:
        spec["regions"].insert(0, _zone(source_region, "blue", [-0.15, -0.12, 0.742], "start_zone"))
        spec["objects"][0]["initial_pose"]["xlim"] = [-0.2, -0.1]
        spec["objects"][0]["initial_pose"]["ylim"] = [-0.05, 0.08]

    asset_checks = [primitive_box_check(block_id, block_color), logical_region_check(target_region)]
    if source_region:
        asset_checks.append(logical_region_check(source_region))

    protected_object = None
    if distractor_bowl:
        modelname, model_id = ASSET_ALIASES["blue bowl"]
        bowl_check = _with_object_id(resolve_robotwin_asset(modelname, repo_root), "blue_bowl")
        asset_checks.append(bowl_check)
        spec["objects"].append(
            _asset_object(
                object_id="blue_bowl",
                role="distractor",
                modelname=modelname,
                model_id=model_id,
                category="bowl",
                xyz=[0.24, 0.06, 0.76],
                qpos=[0.5, 0.5, 0.5, 0.5],
            )
        )
        protected_object = "blue_bowl"

    spec["arm_policy"]["object"] = block_id
    spec["plan"] = _place_plan(block_id, target_region)
    spec["success"] = [
        {
            "type": "in_region",
            "object": block_id,
            "region": target_region,
            "tolerance_m": 0.04,
            "comment": f"The {block_color} block center should end inside {target_region}.",
        }
    ]
    if protected_object:
        spec["success"].append(
            {
                "type": "max_displacement",
                "object": protected_object,
                "reference": "initial",
                "threshold_m": 0.03,
                "comment": "The protected object should remain essentially unmoved.",
            }
        )
        spec["validation_constraints"].append("assets_exist_in_robotwin")
    spec["success"].append(
        {"type": "grippers_open", "required": True, "comment": "The robot should release the object."}
    )

    article = "the"
    target_label = target_region.replace("_", " ")
    spec["language"] = {
        "full_description": instruction,
        "schema": f"{{A}} is {article} {block_color} block, {{B}} is {article} {target_label}, {{a}} is the arm used to move the block.",
        "preference": "Short imperative instructions.",
        "placeholders": {
            "{A}": f"{block_color} block",
            "{B}": target_label,
            "{a}": "$main_arm",
        },
        "seen_templates": [
            "Move {A} onto {B}.",
            "Use {a} to place {A} on {B}.",
            "Put {A} in {B}.",
        ],
        "unseen_templates": [
            "Place {A} onto {B}.",
            "Carry {A} to {B}.",
            "Set {A} down on {B}.",
        ],
    }
    if protected_object:
        spec["language"]["schema"] += " {C} is the protected distractor."
        spec["language"]["placeholders"]["{C}"] = "002_bowl/base3"
        spec["language"]["seen_templates"] = [
            "Move {A} to {B} without moving {C}.",
            "Use {a} to place {A} in {B}.",
            "Transfer {A} into {B} and keep {C} still.",
        ]
        spec["language"]["unseen_templates"] = [
            "Put {A} in {B} while avoiding {C}.",
            "Place {A} at {B} without disturbing {C}.",
            "Carry {A} to {B} and leave {C} still.",
        ]

    return _finalize(spec, asset_checks)


def _drawer_draft(instruction: str, repo_root: Path | None) -> PlacementResult:
    spec = _base_spec("put_cup_in_drawer", instruction, intent="articulated_place")
    spec["status"] = "draft_requires_articulation"
    cup_model, cup_model_id = ASSET_ALIASES["cup"]
    drawer_model, drawer_model_id = ASSET_ALIASES["drawer"]
    spec["objects"] = [
        _asset_object(
            object_id="cup",
            role="manipulated",
            modelname=cup_model,
            model_id=cup_model_id,
            category="cup",
            xyz=[-0.16, -0.02, 0.77],
        ),
        _asset_object(
            object_id="drawer",
            role="fixture",
            modelname=drawer_model,
            model_id=drawer_model_id,
            category="cabinet",
            xyz=[0.12, -0.05, 0.76],
            graspable=False,
            movable=False,
        ),
    ]
    spec["arm_policy"]["object"] = "cup"
    spec["plan"] = [
        {"op": "grasp", "object": "cup", "arm": "$main_arm", "pre_grasp_dis": 0.09, "grasp_dis": 0.0},
        {"op": "wait", "duration_steps": 20, "comment": "Drawer articulation needs a task-specific open-drawer primitive."},
    ]
    spec["success"] = [
        {
            "type": "near",
            "object": "cup",
            "target_object": "drawer",
            "threshold_m": 0.08,
            "comment": "Draft placeholder until drawer interior region is bound.",
        }
    ]
    spec["language"] = {
        "full_description": instruction,
        "schema": "{A} is the cup, {B} is the drawer, {a} is the arm used to move the cup.",
        "preference": "Short imperative instructions.",
        "placeholders": {"{A}": "cup", "{B}": "drawer", "{a}": "$main_arm"},
        "seen_templates": ["Put {A} in {B}.", "Use {a} to move {A} into {B}."],
        "unseen_templates": ["Place {A} inside {B}.", "Move {A} into {B}."],
    }
    spec["notes"] = "Existing assets are selected, but this remains draft until an articulation-aware drawer placement primitive is bound."
    checks = [
        _with_object_id(resolve_robotwin_asset(cup_model, repo_root), "cup"),
        _with_object_id(resolve_robotwin_asset(drawer_model, repo_root), "drawer"),
    ]
    return _finalize(spec, checks, extra_blockers=["articulated drawer placement is not scaffold-ready in Text2Env v0"])


def _extract_color_before(noun: str, text: str) -> str | None:
    for color in COLOR_RGB:
        if re.search(rf"\b{re.escape(color)}\s+{re.escape(noun)}\b", text):
            return color
    return None


def plan_from_instruction(instruction: str, repo_root: str | Path | None = None) -> PlacementResult:
    text = normalize_instruction(instruction)
    if not text:
        raise PlacementError("instruction is empty")

    root = Path(repo_root) if repo_root else None
    has_basket_target = _contains_any(text, ("basket",))
    if has_basket_target and _contains_any(text, ("bottle", "cola bottle", "coke bottle", "water bottle")):
        modelname, model_id = ASSET_ALIASES["cola bottle"]
        return _asset_to_basket(
            instruction,
            object_id="cola_bottle",
            object_label="cola bottle",
            modelname=modelname,
            model_id=model_id,
            category="bottle",
            qpos=[0.707, 0.707, 0, 0],
            repo_root=root,
        )
    if has_basket_target and _contains_any(text, ("can", "cola can", "coke can", "soda can", "cola", "coke", "soda")):
        modelname, model_id = ASSET_ALIASES["cola can"]
        return _asset_to_basket(
            instruction,
            object_id="cola_can",
            object_label="cola can",
            modelname=modelname,
            model_id=model_id,
            category="can",
            qpos=[0.707225, 0.706849, -0.0100455, -0.00982061],
            repo_root=root,
            lift_before_place=False,
            basket_recovery=True,
        )
    if "drawer" in text and "cup" in text:
        return _drawer_draft(instruction, root)

    block_color = _extract_color_before("block", text) or "red"
    zone_color = _extract_color_before("zone", text)
    source_region = "left_zone" if "left zone" in text else None
    if "right zone" in text:
        target_region = "right_zone"
        target_color = "green"
    elif zone_color:
        target_region = f"{zone_color}_zone"
        target_color = zone_color
    else:
        raise PlacementError("expected a target zone, for example 'blue zone' or 'right zone'")

    return _block_to_zone(
        instruction,
        block_color=block_color,
        target_region=target_region,
        target_color=target_color,
        source_region=source_region,
        repo_root=root,
        distractor_bowl=("bowl" in text and ("without moving" in text or "keep" in text or "avoid" in text)),
    )


def _finalize(
    spec: dict[str, Any],
    asset_checks: Iterable[AssetCheck],
    extra_blockers: Iterable[str] = (),
) -> PlacementResult:
    checks = list(asset_checks)
    blockers = [check.blocker for check in checks if check.blocker]
    blockers.extend(extra_blockers)
    if blockers and spec["status"] == "ready_for_scaffold":
        spec["status"] = "draft_requires_review"
    manifest = {
        "agent": "agenticsim.generation.placement_agent",
        "schema_version": "placement_agent.v0",
        "instruction": spec["language_instruction"],
        "task_name": spec["task_name"],
        "status": spec["status"],
        "main_path": "use_existing_assets",
        "asset_generation_used": False,
        "asset_generation_route": "not_used",
        "asset_checks": [asdict(check) for check in checks],
        "blockers": blockers,
    }
    return PlacementResult(spec=spec, manifest=manifest)


def write_placement_outputs(
    instruction: str,
    out_dir: str | Path,
    repo_root: str | Path | None = None,
) -> PlacementResult:
    result = plan_from_instruction(instruction, repo_root=repo_root)
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    spec_path = out_path / "final_text2env.json"
    manifest_path = out_path / "placement_manifest.json"
    spec_path.write_text(json.dumps(result.spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest = {
        **result.manifest,
        "outputs": {
            "text2env_json": str(spec_path),
            "placement_manifest": str(manifest_path),
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return PlacementResult(spec=result.spec, manifest=manifest)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate placement-only Text2Env JSON from a task instruction")
    parser.add_argument("--instruction", required=True, help="Natural-language placement instruction")
    parser.add_argument("--out-dir", required=True, help="Output directory for final_text2env.json and placement_manifest.json")
    parser.add_argument("--repo-root", help="AgenticSim repository root for existing asset checks")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = write_placement_outputs(args.instruction, args.out_dir, repo_root=args.repo_root)
    except PlacementError as exc:
        parser.error(str(exc))
    print(json.dumps(result.manifest, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
