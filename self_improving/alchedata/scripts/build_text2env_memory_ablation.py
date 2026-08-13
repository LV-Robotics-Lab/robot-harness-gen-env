#!/usr/bin/env python3
"""Validate the matched memory/no-memory adapter-selection ablation."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from pose_conditioned_trajectory_policy import read_json, sha256_file, write_json


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-memory", required=True)
    parser.add_argument("--memory", required=True)
    parser.add_argument("--failure-memory", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    no_path = Path(args.no_memory).expanduser().resolve()
    yes_path = Path(args.memory).expanduser().resolve()
    memory_path = Path(args.failure_memory).expanduser().resolve()
    no = read_json(no_path)
    yes = read_json(yes_path)
    memory = read_json(memory_path)

    require(no["status"] == yes["status"] == "completed", "Both controller arms must complete")
    require(no["mode"] == "no-memory" and yes["mode"] == "read-memory", "Controller modes are wrong")
    require(no["fixed_protocol"] == yes["fixed_protocol"], "Fixed protocols differ")
    require(no["default_adapter"] == yes["default_adapter"] == "swap_red_blue", "Default adapters differ")
    require(no["selected_adapter"] == "swap_red_blue", "No-memory arm did not use the default")
    require(yes["selected_adapter"] == "identity", "Memory arm did not select the recommendation")
    require(yes["selection"]["memory_sha256"] == sha256_file(memory_path), "Memory hash mismatch")
    require(memory["source_failure"]["controller_sha256"] == sha256_file(no_path), "Memory source hash mismatch")
    require(no["execution_count"] == yes["execution_count"] == 3, "Both arms must execute 3/3")
    require(no["success_count"] == 0 and yes["success_count"] == 3, "Expected observed 0/3 versus 3/3 outcome")

    result = {
        "schema_version": "alchedata.text2env_memory_ablation.v0",
        "status": "pass_matched_memory_ablation",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment": {
            "fixed_protocol": no["fixed_protocol"],
            "intervention": "availability of one matching structured failure memory to the adapter selector",
            "default_adapter": no["default_adapter"],
            "no_memory_selected_adapter": no["selected_adapter"],
            "memory_selected_adapter": yes["selected_adapter"],
            "only_declared_controller_difference": True,
        },
        "outcomes": {
            "no_memory_success_count": no["success_count"],
            "memory_success_count": yes["success_count"],
            "episode_count_per_arm": 3,
            "success_delta": yes["success_count"] - no["success_count"],
            "both_arms_execution_complete": True,
        },
        "evidence": {
            "no_memory_controller": str(no_path),
            "no_memory_controller_sha256": sha256_file(no_path),
            "memory_controller": str(yes_path),
            "memory_controller_sha256": sha256_file(yes_path),
            "failure_memory": str(memory_path),
            "failure_memory_sha256": sha256_file(memory_path),
            "no_memory_evaluator_report_sha256": no["evaluator_report_sha256"],
            "memory_evaluator_report_sha256": yes["evaluator_report_sha256"],
        },
        "causal_result": (
            "Within this fixed checkpoint, fixed placement, three-seed protocol, making the matching failure memory "
            "available changed the controller's RGB adapter selection and increased measured success from 0/3 to 3/3."
        ),
        "claim_boundary": (
            "This is a one-memory, one-decision harness ablation. It does not establish general long-term memory, "
            "retrieval quality, policy-weight improvement, placement robustness, or transfer to other failure classes."
        ),
    }
    out_path = Path(args.out).expanduser().resolve()
    write_json(out_path, result)
    print(json.dumps({"status": result["status"], **result["outcomes"], "out": str(out_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
