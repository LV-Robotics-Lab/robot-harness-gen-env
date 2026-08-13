#!/usr/bin/env python3
"""Predeclare a placement failure score and a 12-case RoboTwin evaluation manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

try:
    from pose_conditioned_trajectory_policy import read_json, sha256_file, write_json
except ModuleNotFoundError:  # Imported as scripts.build_failure_score_protocol in tests.
    from scripts.pose_conditioned_trajectory_policy import read_json, sha256_file, write_json


ROOT = Path(__file__).resolve().parents[1]


def selected_entries(manifest: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    train = manifest["splits"]["train"][:8]
    evaluation = manifest["splits"]["eval"][:4]
    if len(train) != 8 or len(evaluation) != 4:
        raise ValueError("Source manifest must contain at least eight train and four eval placements")
    return [("source_train", entry) for entry in train] + [("source_eval", entry) for entry in evaluation]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", required=True)
    parser.add_argument("--training-report", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--seed-start", type=int, default=8100)
    args = parser.parse_args()

    source_path = Path(args.source_manifest).expanduser().resolve()
    training_path = Path(args.training_report).expanduser().resolve()
    checkpoint_path = Path(args.checkpoint).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    source = read_json(source_path)
    training = read_json(training_path)
    training_features = np.asarray(training["training_features_xy_m"], dtype=np.float64)
    mean = np.mean(training_features, axis=0)
    scale = np.std(training_features, axis=0)
    scale = np.where(scale < 1e-6, 1.0, scale)
    normalized_training = (training_features - mean) / scale

    cases = []
    manifest_entries = []
    for index, (source_split, entry) in enumerate(selected_entries(source)):
        feature = np.asarray(entry["pose_vector"], dtype=np.float64)
        normalized = (feature - mean) / scale
        distances = np.linalg.norm(normalized_training - normalized, axis=1)
        score = float(np.min(distances))
        source_placement = Path(entry["placement"])
        if not source_placement.is_absolute():
            source_placement = source_path.parent / source_placement
        source_placement = source_placement.resolve()
        placement_id = f"case_{index:03d}"
        relative_placement = Path("..") / source_path.parent.name / source_placement.relative_to(source_path.parent)
        case = {
            "case_id": placement_id,
            "source_split": source_split,
            "source_placement_id": entry["placement_id"],
            "pose_signature": entry["pose_signature"],
            "pose_vector": feature.tolist(),
            "failure_score": score,
            "nearest_training_feature_index": int(np.argmin(distances)),
            "placement": str(relative_placement),
            "placement_sha256": sha256_file(source_placement),
            "episode_seed": args.seed_start + index,
        }
        cases.append(case)
        manifest_entries.append(
            {
                "placement_id": placement_id,
                "candidate_placement_id": entry["placement_id"],
                "placement": str(relative_placement),
                "episode_seed": args.seed_start + index,
                "pose_signature": entry["pose_signature"],
                "pose_vector": feature.tolist(),
                "predeclared_failure_score": score,
            }
        )

    signatures = [case["pose_signature"] for case in cases]
    if len(set(signatures)) != 12:
        raise ValueError("Protocol placements are not twelve unique signatures")
    manifest = {
        "schema_version": "alchedata.failure_score_placement_manifest.v0",
        "status": "predeclared_before_outcomes",
        "task_id": "task_apple_plate",
        "splits": {"eval": manifest_entries},
    }
    manifest_path = out_dir / "placement_manifest.json"
    write_json(manifest_path, manifest)
    generated_at = datetime.now(timezone.utc).isoformat()
    score_payload = json.dumps(cases, sort_keys=True, separators=(",", ":")).encode("utf-8")
    protocol = {
        "schema_version": "alchedata.failure_score_protocol.v0",
        "status": "predeclared_before_outcomes",
        "generated_at": generated_at,
        "task_id": "task_apple_plate",
        "sample_count": 12,
        "unique_pose_signature_count": len(set(signatures)),
        "score_name": "nearest_training_pose_distance_z4",
        "score_formula": (
            "minimum Euclidean distance from the case [source_x, source_y, target_x, target_y] to any training "
            "placement after per-coordinate standardization by the training-set mean and population standard deviation"
        ),
        "score_direction": "higher_predeclared_score_means_higher_predicted_failure_risk",
        "outcome_definition": "failure = 1 when execution completes and RoboTwin policy_success is false; else 0",
        "analysis": {
            "primary": "Pearson correlation between continuous failure score and binary failure outcome (point-biserial)",
            "secondary": "Spearman rank correlation",
            "uncertainty": "exact label-permutation two-sided p-value when both outcome classes are present",
            "retention": "all twelve cases retained regardless of score or outcome",
            "positive_effect_required": False,
        },
        "training_reference": {
            "training_report": str(training_path),
            "training_report_sha256": sha256_file(training_path),
            "training_feature_count": len(training_features),
            "feature_mean": mean.tolist(),
            "feature_scale": scale.tolist(),
        },
        "policy_reference": {
            "checkpoint": str(checkpoint_path),
            "checkpoint_sha256": sha256_file(checkpoint_path),
        },
        "source_manifest": str(source_path),
        "source_manifest_sha256": sha256_file(source_path),
        "placement_manifest": str(manifest_path),
        "case_scores_sha256": hashlib.sha256(score_payload).hexdigest(),
        "cases": cases,
        "claim_boundary": (
            "This protocol tests one geometric distance heuristic against one ACT checkpoint. A correlation, including "
            "a positive one, would not prove causality or transfer to other policies, tasks, or simulators."
        ),
    }
    write_json(out_dir / "protocol.json", protocol)
    print(
        json.dumps(
            {
                "status": protocol["status"],
                "sample_count": protocol["sample_count"],
                "score_min": min(case["failure_score"] for case in cases),
                "score_max": max(case["failure_score"] for case in cases),
                "protocol": str(out_dir / "protocol.json"),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
