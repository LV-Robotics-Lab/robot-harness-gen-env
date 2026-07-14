#!/usr/bin/env python3
"""Build strict same-scene, two-task execution evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from selection2env_contract import ROOT, read_json, validate_scene_task_pair, workspace_path


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify two executable task specs over one placement.")
    parser.add_argument("--primary-task", required=True)
    parser.add_argument("--alternate-task", required=True)
    parser.add_argument("--primary-rollout", required=True)
    parser.add_argument("--alternate-rollout", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    primary_path = workspace_path(args.primary_task)
    alternate_path = workspace_path(args.alternate_task)
    primary_rollout_path = workspace_path(args.primary_rollout)
    alternate_rollout_path = workspace_path(args.alternate_rollout)
    primary = read_json(primary_path)
    alternate = read_json(alternate_path)
    pair = validate_scene_task_pair(primary, alternate)

    rollout_rows = []
    for label, program, report_path in (
        ("primary", primary, primary_rollout_path),
        ("alternate", alternate, alternate_rollout_path),
    ):
        report = read_json(report_path)
        checks = {
            "status_pass": report.get("status") == "pass_generated_action_rollout",
            "check_success": report.get("check_success") is True,
            "task_id_matches": report.get("task_id") == program.get("task_id"),
            "scene_id_matches": report.get("scene_id") == program.get("scene_id"),
            "placement_sha256_matches": (
                report.get("placement_sha256") == program.get("placement_sha256")
            ),
        }
        rollout_rows.append(
            {
                "label": label,
                "task_id": program["task_id"],
                "task_program": str(primary_path.relative_to(ROOT) if label == "primary" else alternate_path.relative_to(ROOT)),
                "rollout_report": str(report_path.relative_to(ROOT)),
                "checks": checks,
                "status": "pass" if all(checks.values()) else "fail",
                "images": report.get("images", {}),
                "observer_video": report.get("observer_video"),
                "relation_metrics": report.get("relation_metrics", {}),
            }
        )

    status = (
        "pass_same_scene_two_executable_tasks"
        if pair["status"] == "pass" and all(row["status"] == "pass" for row in rollout_rows)
        else "fail_scene_task_decoupling"
    )
    result = {
        "schema_version": "alchedata.scene_task_decoupling_evidence.v0",
        "status": status,
        "scene_id": pair.get("scene_id"),
        "placement_spec": pair.get("placement_spec"),
        "placement_sha256": pair.get("placement_sha256"),
        "task_count": 2,
        "pair_validation": pair,
        "rollouts": rollout_rows,
        "claim_boundary": (
            "Both task programs reset from byte-identical placement JSON and pass RoboTwin generated play_once/check_success. "
            "This proves scene-task decoupling for one scene, not learned-policy cross-task generalization."
        ),
    }
    write_json(workspace_path(args.out), result)
    print(f"{status.upper()} task_count=2")
    return 0 if status.startswith("pass_") else 1


if __name__ == "__main__":
    raise SystemExit(main())
