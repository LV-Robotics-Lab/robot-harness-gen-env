#!/usr/bin/env python3
"""Measure whether runtime evidence is bound to the exact resolved scene."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scene_gen.catalog import load_catalog  # noqa: E402
from scene_gen.parser import parse_rule_based  # noqa: E402
from scene_gen.solver import solve_scene  # noqa: E402
from scene_gen.validator import validate_resolved_scene  # noqa: E402


def _valid_evidence(resolved: Any) -> dict[str, Any]:
    return {
        "schema_version": "robotwin.scene_runtime_evidence.v1",
        "scene_id": resolved.scene_id,
        "resolved_scene_sha256": resolved.digest(),
        "status": "pass",
        "robot_initial_collision_count": 0,
        "objects": {
            item.object_id: {
                "translation_drift_m": 0.001,
                "rotation_drift_deg": 0.2,
                "resolved_translation_error_m": 0.001,
                "resolved_rotation_error_deg": 0.2,
                "penetration_count": 0,
                "still_moving": False,
                "support_contact": not item.is_static,
                "support_contact_fraction": 1.0 if not item.is_static else 0.0,
                "unexpected_contact_fraction": 0.0,
                "unexpected_contact_targets": [],
                "support_mode": "fixed_static_pose" if item.is_static else "on_table_contact",
                "support_target": None if item.is_static else "table",
                "dropped": False,
                "visible_pixels": 512,
            }
            for item in resolved.objects
        },
    }


def run_probe() -> dict[str, Any]:
    catalog = load_catalog(ROOT / "tests" / "fixtures" / "asset_catalog.json")
    spec = parse_rule_based(
        "A red can is left of a plastic basket near the center.",
        seed=23,
    )
    resolved = solve_scene(spec, catalog)
    valid = _valid_evidence(resolved)

    cases: list[tuple[str, dict[str, Any], str]] = []
    cases.append(("matching_identity", copy.deepcopy(valid), "pass"))

    missing_digest = copy.deepcopy(valid)
    missing_digest.pop("resolved_scene_sha256")
    cases.append(("missing_digest", missing_digest, "fail"))

    wrong_digest = copy.deepcopy(valid)
    wrong_digest["resolved_scene_sha256"] = "0" * 64
    cases.append(("mismatched_digest", wrong_digest, "fail"))

    missing_scene_id = copy.deepcopy(valid)
    missing_scene_id.pop("scene_id")
    cases.append(("missing_scene_id", missing_scene_id, "fail"))

    wrong_scene_id = copy.deepcopy(valid)
    wrong_scene_id["scene_id"] = f"{resolved.scene_id}-other"
    cases.append(("mismatched_scene_id", wrong_scene_id, "fail"))

    outcomes: list[dict[str, Any]] = []
    for name, evidence, expected in cases:
        report = validate_resolved_scene(
            resolved,
            runtime_evidence=evidence,
            require_runtime=True,
        )
        binding_checks = [
            item
            for item in report["checks"]
            if item["name"] in {"runtime_scene_identity", "runtime_resolved_scene_binding"}
        ]
        outcomes.append(
            {
                "case": name,
                "expected_status": expected,
                "actual_status": report["status"],
                "correct": report["status"] == expected,
                "binding_checks": binding_checks,
            }
        )

    correct = sum(item["correct"] for item in outcomes)
    unsafe_accepts = sum(
        item["expected_status"] == "fail" and item["actual_status"] == "pass"
        for item in outcomes
    )
    return {
        "schema_version": "robot_harness.aspire_runtime_binding_probe.v1",
        "scene_id": resolved.scene_id,
        "resolved_scene_sha256": resolved.digest(),
        "case_count": len(outcomes),
        "correct_decisions": correct,
        "accuracy": correct / len(outcomes),
        "unsafe_accept_count": unsafe_accepts,
        "outcomes": outcomes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = run_probe()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("accuracy", "unsafe_accept_count")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
