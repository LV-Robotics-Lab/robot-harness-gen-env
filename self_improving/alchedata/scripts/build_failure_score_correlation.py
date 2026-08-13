#!/usr/bin/env python3
"""Join a predeclared failure-score protocol to executed outcomes and report correlation."""

from __future__ import annotations

import argparse
import itertools
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

try:
    from pose_conditioned_trajectory_policy import read_json, sha256_file, write_json
except ModuleNotFoundError:  # Imported as scripts.build_failure_score_correlation in tests.
    from scripts.pose_conditioned_trajectory_policy import read_json, sha256_file, write_json


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def average_ranks(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=np.float64)
    start = 0
    while start < len(values):
        end = start + 1
        while end < len(values) and values[order[end]] == values[order[start]]:
            end += 1
        ranks[order[start:end]] = (start + end - 1) / 2.0 + 1.0
        start = end
    return ranks


def correlation(first: np.ndarray, second: np.ndarray) -> float | None:
    if len(first) < 2 or np.std(first) == 0 or np.std(second) == 0:
        return None
    return float(np.corrcoef(first, second)[0, 1])


def exact_permutation_p_value(scores: np.ndarray, outcomes: np.ndarray, observed: float | None) -> float | None:
    if observed is None:
        return None
    positives = int(np.sum(outcomes))
    correlations = []
    for indices in itertools.combinations(range(len(outcomes)), positives):
        permuted = np.zeros(len(outcomes), dtype=np.float64)
        permuted[list(indices)] = 1.0
        value = correlation(scores, permuted)
        if value is not None:
            correlations.append(abs(value))
    return float(sum(value >= abs(observed) - 1e-12 for value in correlations) / len(correlations))


def build_result(protocol_path: Path, report_path: Path, out_path: Path) -> dict[str, Any]:
    protocol = read_json(protocol_path)
    report = read_json(report_path)
    require(protocol["status"] == "predeclared_before_outcomes", "Protocol was not predeclared")
    require(protocol["sample_count"] == 12, "Protocol does not contain twelve cases")
    require(report["episode_count"] == 12 and report["execution_count"] == 12, "Twelve episodes did not execute")
    require(report["status"] == "pass_generated_act_evaluate_execution", "ACT evaluation execution failed")
    require(report["started_at"] > protocol["generated_at"], "Evaluation did not start after protocol creation")

    by_id = {episode["placement_id"]: episode for episode in report["episodes"]}
    samples = []
    for case in protocol["cases"]:
        episode = by_id.get(case["case_id"])
        require(episode is not None, f"Missing outcome for {case['case_id']}")
        require(episode["pose_signature"] == case["pose_signature"], "Pose signature mismatch")
        require(episode["execution_complete"] is True, "Infrastructure failure is not a policy outcome")
        failure = 0 if episode["policy_success"] else 1
        samples.append(
            {
                **case,
                "policy_success": episode["policy_success"],
                "failure": failure,
                "status": episode["status"],
                "policy_step_count": episode["policy_step_count"],
                "relation_metrics": episode["relation_metrics"],
                "episode_report": str(Path(episode["events"]).parent / "episode_report.json"),
                "observer_video": episode["observer_video"],
            }
        )
    require(len(samples) == 12, "Joined sample count changed")
    scores = np.asarray([sample["failure_score"] for sample in samples], dtype=np.float64)
    failures = np.asarray([sample["failure"] for sample in samples], dtype=np.float64)
    pearson = correlation(scores, failures)
    spearman = correlation(average_ranks(scores), average_ranks(failures))
    p_value = exact_permutation_p_value(scores, failures, pearson)
    result = {
        "schema_version": "alchedata.failure_score_correlation.v0",
        "status": "pass_predeclared_failure_score_correlation_reported",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": str(protocol_path),
        "protocol_sha256": sha256_file(protocol_path),
        "evaluate_report": str(report_path),
        "evaluate_report_sha256": sha256_file(report_path),
        "sample_count": len(samples),
        "unique_pose_signature_count": len({sample["pose_signature"] for sample in samples}),
        "success_count": int(np.sum(1.0 - failures)),
        "failure_count": int(np.sum(failures)),
        "metrics": {
            "point_biserial_pearson_r": pearson,
            "spearman_rho": spearman,
            "exact_two_sided_label_permutation_p": p_value,
        },
        "interpretation": (
            "undefined because all outcomes are in one class"
            if pearson is None
            else "higher score was associated with more failures in this bounded sample"
            if pearson > 0
            else "higher score was not associated with more failures in this bounded sample"
        ),
        "all_samples_retained": True,
        "samples": samples,
        "claim_boundary": protocol["claim_boundary"],
    }
    write_json(out_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--evaluate-report", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = build_result(
        Path(args.protocol).expanduser().resolve(),
        Path(args.evaluate_report).expanduser().resolve(),
        Path(args.out).expanduser().resolve(),
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "sample_count": result["sample_count"],
                "success_count": result["success_count"],
                "failure_count": result["failure_count"],
                **result["metrics"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
