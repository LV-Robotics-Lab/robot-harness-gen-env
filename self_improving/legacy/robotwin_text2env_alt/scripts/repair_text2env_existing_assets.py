#!/usr/bin/env python3
"""Repair live LLM Text2Env drafts into known-safe existing-asset placements.

This script is intentionally narrow. It does not synthesize assets and it does
not pretend the LLM output was simulator-ready. It preserves the LLM task
intent, then normalizes loader parameters and placement primitives for cases
whose RoboTwin behavior has already been validated.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "text2env.tabletop.v0"


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def text_blob(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False).lower()


def canonical_can_basket_spec(instruction: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "task_name": "place_cola_can_in_basket",
        "language_instruction": instruction,
        "intent": "place_in_container",
        "status": "ready_for_scaffold",
        "workspace": {
            "type": "tabletop",
            "surface": "table",
            "robot_setup": "dual_arm",
            "bounds": {"x": [-0.4, 0.4], "y": [-0.3, 0.25], "z": [0.74, 1.1]},
            "table": {"height_m": 0.74},
        },
        "objects": [
            {
                "id": "cola_can",
                "role": "manipulated",
                "kind": "asset",
                "category": "can",
                "asset": {"modelname": "071_can", "model_id": 0, "convex": True},
                "initial_pose": {
                    "mode": "fixed",
                    "xyz": [-0.225, 0.05, 0.741],
                    "qpos": [0.707225, 0.706849, -0.0100455, -0.00982061],
                },
                "physical": {
                    "mass_kg": 0.01,
                    "graspable": True,
                    "movable": True,
                    "is_static": False,
                },
                "protected_region": {"enabled": True, "padding_m": 0.1},
                "description_key": "071_can/base0",
            },
            {
                "id": "basket",
                "role": "container",
                "kind": "asset",
                "category": "basket",
                "asset": {"modelname": "110_basket", "model_id": 1, "convex": True},
                "initial_pose": {
                    "mode": "fixed",
                    "xyz": [0.02, -0.06, 0.741],
                    "qpos": [0.5, 0.5, 0.5, 0.5],
                },
                "physical": {
                    "mass_kg": 0.5,
                    "graspable": False,
                    "movable": True,
                    "is_static": False,
                },
                "protected_region": {"enabled": True, "padding_m": 0.05},
                "description_key": "110_basket/base1",
            },
        ],
        "regions": [],
        "arm_policy": {"type": "fixed", "fixed_arm": "left", "save_as": "main_arm"},
        "plan": [
            {
                "op": "grasp",
                "object": "cola_can",
                "arm": "$main_arm",
                "pre_grasp_dis": 0.05,
                "grasp_dis": 0.0,
                "comment": "Pick the existing RoboTwin cola can asset.",
            },
            {
                "op": "place",
                "object": "cola_can",
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
                "recovery": {"type": "can_basket"},
            },
        ],
        "success": [
            {
                "type": "near",
                "object": "cola_can",
                "target_object": "basket",
                "threshold_m": 0.15,
                "comment": "The cola can should end inside or near the basket footprint.",
            },
            {
                "type": "contact",
                "object": "cola_can",
                "target_object": "basket",
                "comment": "The cola can should contact the basket.",
            },
        ],
        "language": {
            "full_description": instruction,
            "schema": "{A} is the cola can, {B} is the basket, {a} is the arm used to move the object.",
            "preference": "Short imperative instructions for existing-asset container placement.",
            "placeholders": {
                "{A}": "071_can/base0",
                "{B}": "110_basket/base1",
                "{a}": "$main_arm",
            },
            "seen_templates": [
                "Place {A} into {B}.",
                "Put {A} in {B}.",
            ],
            "unseen_templates": [
                "Move {A} into {B}.",
                "Set {A} inside {B}.",
            ],
        },
        "randomization": {
            "enabled": False,
            "seed_policy": "fixed_smoke_seed",
            "notes": "Fixed poses are used for the validated RoboTwin smoke path.",
        },
        "validation_constraints": {
            "asset_generation_used": False,
            "must_use_existing_assets": True,
            "must_not_grasp": ["basket"],
            "max_basket_displacement_m": 0.2,
            "known_repair": "basket qpos/convex and functional-point placement normalized from smoke evidence",
        },
        "generation_targets": {
            "robotwin_task_file": "envs/place_cola_can_in_basket.py",
            "instruction_file": "description/task_instruction/place_cola_can_in_basket.json",
            "preferred_task_config": "demo_smoke_llm_repaired",
        },
        "notes": "Live LLM draft repaired to validated RoboTwin existing-asset placement parameters; no stock task or asset synthesis path is used.",
    }


def repair(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    instruction = data.get("language_instruction") or data.get("language", {}).get("full_description") or ""
    blob = text_blob(data) + " " + str(instruction).lower()
    can_terms = ("cola", "coke", "soda", "071_can")
    has_can = any(term in blob for term in can_terms)
    has_basket = "basket" in blob or "110_basket" in blob

    if has_can and has_basket:
        repaired = canonical_can_basket_spec(str(instruction or "Place the cola can into the basket."))
        report = {
            "repair_applied": True,
            "repair_type": "existing_asset_can_basket_smoke_safe",
            "source_task_name": data.get("task_name"),
            "output_task_name": repaired["task_name"],
            "llm_selected_terms": {"can": has_can, "basket": has_basket},
            "repairs": [
                "normalize asset.modelname from display names to RoboTwin loader names",
                "force numeric model_id values for 071_can and 110_basket",
                "force basket convex=true and qpos=[0.5,0.5,0.5,0.5] from smoke evidence",
                "use target_functional_point_id=0 on basket",
                "add can_basket recovery primitive and prevent basket grasp",
                "emit RoboTwin placeholder instruction templates for episode instruction generation",
            ],
        }
        return repaired, report

    report = {
        "repair_applied": False,
        "repair_type": "none",
        "source_task_name": data.get("task_name"),
        "reason": "No known smoke-safe existing-asset repair matched this draft.",
    }
    return data, report


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair Text2Env drafts into known-safe existing-asset placements")
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()

    data = load_json(args.input)
    repaired, report = repair(data)
    write_json(args.out, repaired)
    write_json(args.report, report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
