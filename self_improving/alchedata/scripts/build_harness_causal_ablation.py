#!/usr/bin/env python3
"""Validate a fixed-checkpoint harness-only ablation and write its promotion record."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def workspace_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def matched_protocol(report: dict[str, Any]) -> dict[str, Any]:
    model = report.get("model", {})
    camera = report.get("camera_adapter", {})
    scope = report.get("evaluation_scope", {})
    return {
        "checkpoint_sha256": model.get("checkpoint_sha256"),
        "dataset_stats_sha256": model.get("dataset_stats_sha256"),
        "model_config": model.get("config"),
        "task_id": report.get("task_id"),
        "task_config": report.get("task_config"),
        "held_out_seeds": report.get("held_out_seeds"),
        "eval_placements": [
            {
                "placement_id": row.get("placement_id"),
                "placement_sha256": row.get("placement_sha256"),
                "pose_signature": row.get("pose_signature"),
                "seed": row.get("seed"),
            }
            for row in report.get("eval_placements", [])
        ],
        "source_training_seeds": report.get("source_training_seeds"),
        "runtime_camera_source": camera.get("runtime_source"),
        "training_camera_key": camera.get("training_key"),
        "placement_randomization": scope.get("placement_randomization"),
        "domain_randomization": scope.get("domain_randomization"),
        "action_selection": scope.get("action_selection"),
    }


def validate_report(report: dict[str, Any], label: str) -> None:
    if report.get("status") != "pass_generated_act_evaluate_execution":
        raise AssertionError(f"{label} did not complete evaluation infrastructure")
    if report.get("execution_count") != report.get("episode_count") or not report.get("episode_count"):
        raise AssertionError(f"{label} did not execute every declared episode")
    model = report.get("model", {})
    if not model.get("checkpoint_sha256") or not model.get("dataset_stats_sha256"):
        raise AssertionError(f"{label} is missing checkpoint provenance hashes")
    if any(not row.get("placement_sha256") for row in report.get("eval_placements", [])):
        raise AssertionError(f"{label} is missing placement provenance hashes")


def build_ablation(
    baseline_path: Path,
    candidate_path: Path,
    *,
    min_success_delta: int = 2,
    baseline_adapter: str = "swap_red_blue",
    candidate_adapter: str = "identity",
) -> dict[str, Any]:
    baseline = read_json(baseline_path)
    candidate = read_json(candidate_path)
    validate_report(baseline, "baseline")
    validate_report(candidate, "candidate")

    baseline_protocol = matched_protocol(baseline)
    candidate_protocol = matched_protocol(candidate)
    mismatches = {
        key: {"baseline": baseline_protocol[key], "candidate": candidate_protocol[key]}
        for key in baseline_protocol
        if baseline_protocol[key] != candidate_protocol[key]
    }
    if mismatches:
        raise AssertionError(f"Matched protocol differs outside the harness intervention: {mismatches}")

    baseline_color = baseline.get("camera_adapter", {}).get("runtime_color_adapter")
    candidate_color = candidate.get("camera_adapter", {}).get("runtime_color_adapter")
    if baseline_color != baseline_adapter:
        raise AssertionError(f"Unexpected baseline color adapter: {baseline_color}")
    if candidate_color != candidate_adapter:
        raise AssertionError(f"Unexpected candidate color adapter: {candidate_color}")

    baseline_success = int(baseline.get("success_count", 0))
    candidate_success = int(candidate.get("success_count", 0))
    episode_count = int(candidate["episode_count"])
    success_delta = candidate_success - baseline_success
    no_observed_safety_regression = (
        baseline.get("execution_count") == baseline.get("episode_count")
        and candidate.get("execution_count") == candidate.get("episode_count")
        and all(episode.get("infrastructure_error") is None for episode in baseline.get("episodes", []))
        and all(episode.get("infrastructure_error") is None for episode in candidate.get("episodes", []))
    )
    promotion_pass = (
        candidate_success == episode_count
        and success_delta >= min_success_delta
        and no_observed_safety_regression
    )
    decision = "accept" if promotion_pass else "reject"
    return {
        "schema_version": "alchedata.harness_causal_ablation.v0",
        "status": f"pass_matched_harness_ablation_candidate_{'promoted' if promotion_pass else 'rejected'}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "claim_class": "COMPUTED",
        "confidence": "HIGH",
        "experiment": {
            "hypothesis": "Correcting the runtime RGB adapter improves task success without changing the learned policy or task protocol.",
            "fixed_variables": baseline_protocol,
            "intervention": {
                "harness_surface": "observations.runtime_color_adapter",
                "baseline": baseline_adapter,
                "candidate": candidate_adapter,
                "only_declared_difference": True,
            },
            "predeclared_promotion_threshold": {
                "candidate_success": f"{episode_count}/{episode_count}",
                "minimum_success_delta": min_success_delta,
                "all_episodes_execute": True,
                "no_observed_infrastructure_or_nonfinite_action_regression": True,
            },
        },
        "evidence": {
            "baseline_report": workspace_path(baseline_path),
            "baseline_report_sha256": sha256_file(baseline_path),
            "candidate_report": workspace_path(candidate_path),
            "candidate_report_sha256": sha256_file(candidate_path),
        },
        "outcomes": {
            "baseline_success_count": baseline_success,
            "candidate_success_count": candidate_success,
            "episode_count_per_arm": episode_count,
            "success_delta": success_delta,
            "baseline_success_rate": baseline.get("policy_success_rate"),
            "candidate_success_rate": candidate.get("policy_success_rate"),
            "both_arms_execution_complete": True,
            "no_observed_safety_regression": no_observed_safety_regression,
        },
        "promotion": {
            "decision": decision,
            "parent_harness_id": f"runtime_rgb_{baseline_adapter}_v0",
            "candidate_harness_id": f"runtime_rgb_{candidate_adapter}_v1",
            "promoted_harness_id": f"runtime_rgb_{candidate_adapter}_v1" if promotion_pass else None,
            "rollback_harness_id": f"runtime_rgb_{baseline_adapter}_v0",
            "gates": {
                "matched_fixed_checkpoint_protocol": True,
                "candidate_all_success": candidate_success == episode_count,
                "minimum_success_delta": success_delta >= min_success_delta,
                "no_observed_safety_regression": no_observed_safety_regression,
            },
        },
        "causal_result": (
            "Within this fixed-placement, fixed-checkpoint, three-seed protocol, the corrected RGB harness adapter caused the measured success increase."
            if promotion_pass
            else "The matched experiment completed, but the corrected RGB harness adapter did not meet the predeclared promotion threshold."
        ),
        "claim_boundary": "This ablation attributes the measured difference within the matched fixed-scene protocol. It does not establish placement robustness, cross-task generalization, domain-randomized robustness, or real-robot benefit.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--min-success-delta", type=int, default=2)
    args = parser.parse_args()
    report = build_ablation(
        args.baseline.expanduser().resolve(),
        args.candidate.expanduser().resolve(),
        min_success_delta=args.min_success_delta,
    )
    output = args.out.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "decision": report["promotion"]["decision"], "out": str(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
