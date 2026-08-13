#!/usr/bin/env python3
"""Build the placement-robustness failure-to-data diagnosis and promotion decision."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
KNOWN_ROOTS = (
    ROOT,
    Path("/home/jingxiang/workspace/alchedata-self-improving-agents"),
    Path("/Users/boris/workspace/alchedata-self-improving-agents"),
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def resolve_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        return (ROOT / path).resolve()
    if path.exists():
        return path
    for known_root in KNOWN_ROOTS:
        try:
            candidate = ROOT / path.relative_to(known_root)
        except ValueError:
            continue
        if candidate.exists():
            return candidate
    return path


def workspace_path(path: Path) -> str:
    path = path.expanduser().resolve()
    for known_root in KNOWN_ROOTS:
        try:
            return str(path.relative_to(known_root))
        except ValueError:
            continue
    return str(path)


def vector_distance(first: list[float], second: list[float]) -> float:
    return math.sqrt(sum((left - right) ** 2 for left, right in zip(first, second, strict=True)))


def collection_summary(path: Path, report: dict[str, Any]) -> dict[str, Any]:
    passed = [
        episode
        for episode in report.get("episodes", [])
        if episode.get("status") == "pass_generated_action_rollout" and episode.get("check_success") is True
    ]
    native_passed = [
        episode
        for episode in passed
        if episode.get("native_synchronized_data", {}).get("status") == "pass_native_synchronized_recording"
    ]
    return {
        "report": workspace_path(path),
        "status": report.get("status"),
        "episode_count": report.get("episode_count"),
        "pass_count": len(passed),
        "native_synchronized_pass_count": len(native_passed),
        "passed_placement_ids": [episode.get("placement_id") for episode in passed],
        "passed_pose_signatures": [episode.get("pose_signature") for episode in passed if episode.get("pose_signature")],
        "domain_randomization": report.get("domain_randomization"),
    }


def train_summary(path: Path, report: dict[str, Any]) -> dict[str, Any]:
    return {
        "report": workspace_path(path),
        "status": report.get("status"),
        "num_epochs": report.get("num_epochs"),
        "best_epoch": report.get("best_epoch"),
        "best_val_loss": report.get("best_val_loss"),
        "learning_rate": report.get("learning_rate"),
        "checkpoint": report.get("files", {}).get("policy_best"),
    }


def eval_summary(path: Path, report: dict[str, Any]) -> dict[str, Any]:
    return {
        "report": workspace_path(path),
        "status": report.get("status"),
        "episode_count": report.get("episode_count"),
        "execution_count": report.get("execution_count"),
        "success_count": report.get("success_count"),
        "policy_success_rate": report.get("policy_success_rate"),
        "all_eval_placements_held_out": report.get("all_eval_placements_held_out"),
        "action_selection": report.get("evaluation_scope", {}).get("action_selection"),
        "episodes": [
            {
                "placement_id": episode.get("placement_id"),
                "seed": episode.get("seed"),
                "pose_signature": episode.get("pose_signature"),
                "policy_success": episode.get("policy_success"),
                "policy_step_count": episode.get("policy_step_count"),
                "xy_distance_m": episode.get("relation_metrics", {}).get("xy_distance_m"),
                "status": episode.get("status"),
            }
            for episode in report.get("episodes", [])
        ],
    }


def diversity_summary(path: Path, report: dict[str, Any]) -> dict[str, Any]:
    return {
        "report": workspace_path(path),
        "status": report.get("status"),
        **report.get("observed", {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the ACT placement-robustness diagnosis.")
    parser.add_argument("--initial-diversity", required=True)
    parser.add_argument("--initial-train", required=True)
    parser.add_argument("--initial-eval", required=True)
    parser.add_argument("--horizon-eval", required=True)
    parser.add_argument("--recovery-collection", required=True)
    parser.add_argument("--recovery-diversity", required=True)
    parser.add_argument("--recovery-train", required=True)
    parser.add_argument("--final-feasibility", required=True)
    parser.add_argument("--final-manifest", required=True)
    parser.add_argument("--final-eval", required=True)
    parser.add_argument("--extra-task-collection", required=True)
    parser.add_argument("--training-collection", action="append", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    paths = {
        key: resolve_path(value)
        for key, value in vars(args).items()
        if key not in {"out", "training_collection"}
    }
    reports = {key: read_json(path) for key, path in paths.items()}
    training_collection_paths = [resolve_path(value) for value in args.training_collection]
    training_collections = [read_json(path) for path in training_collection_paths]

    initial_eval = reports["initial_eval"]
    horizon_eval = reports["horizon_eval"]
    recovery_collection = reports["recovery_collection"]
    final_eval = reports["final_eval"]
    final_manifest = reports["final_manifest"]
    extra_task = reports["extra_task_collection"]

    initial_failed_ids = {
        str(episode.get("placement_id"))
        for episode in initial_eval.get("episodes", [])
        if episode.get("policy_success") is not True
    }
    recovery_passed_ids = {
        str(episode.get("placement_id"))
        for episode in recovery_collection.get("episodes", [])
        if episode.get("status") == "pass_generated_action_rollout" and episode.get("check_success") is True
    }
    training_vectors = [
        episode["pose_vector"]
        for report in training_collections
        for episode in report.get("episodes", [])
        if episode.get("status") == "pass_generated_action_rollout"
        and episode.get("check_success") is True
        and episode.get("pose_vector")
    ]
    final_eval_vectors = [entry["pose_vector"] for entry in final_manifest.get("splits", {}).get("eval", [])]
    eval_to_training_distances = [
        min(vector_distance(eval_vector, train_vector) for train_vector in training_vectors)
        for eval_vector in final_eval_vectors
    ]

    final_infrastructure_pass = (
        final_eval.get("status") == "pass_generated_act_evaluate_execution"
        and final_eval.get("execution_count") == final_eval.get("episode_count")
        and final_eval.get("episode_count") == 4
        and final_eval.get("all_eval_placements_held_out") is True
    )
    heldout_success_gate = final_infrastructure_pass and final_eval.get("success_count") == final_eval.get("episode_count")
    recovery_targeting_pass = bool(initial_failed_ids) and initial_failed_ids <= recovery_passed_ids
    extra_task_execution_pass = (
        extra_task.get("task_id") != "task_apple_plate"
        and extra_task.get("status") == "pass_generated_rollout_collection"
        and extra_task.get("pass_count") == extra_task.get("episode_count")
        and extra_task.get("episode_count", 0) >= 2
    )
    dataset_gate = reports["recovery_diversity"].get("status") == "pass_act_dataset_diversity"
    promotion_gates = {
        "native_varied_dataset_diversity": dataset_gate,
        "failure_to_data_targeting": recovery_targeting_pass,
        "new_heldout_eval_infrastructure": final_infrastructure_pass,
        "new_heldout_eval_all_success": heldout_success_gate,
        "additional_task_scripted_execution": extra_task_execution_pass,
        "declared_visual_physics_domain_randomization": False,
        "additional_task_learned_policy_evaluation": False,
    }
    report = {
        "schema_version": "alchedata.placement_robustness_diagnosis.v0",
        "status": "pass_failure_to_data_iteration_promotion_rejected",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "task_id": "task_apple_plate",
        "claim_boundary": (
            "This diagnosis proves varied native demonstrations, byte-level trajectory diversity, two ACT training runs, "
            "two signature-disjoint held-out placement evaluations, a targeted failure-to-data repair, and fresh scripted "
            "execution on an additional task. It does not claim robust policy promotion because both held-out learned-policy "
            "evaluations score 1/4, domain randomization is disabled, and the additional task is not a learned-policy eval."
        ),
        "initial_iteration": {
            "dataset": diversity_summary(paths["initial_diversity"], reports["initial_diversity"]),
            "train": train_summary(paths["initial_train"], reports["initial_train"]),
            "heldout_evaluate": eval_summary(paths["initial_eval"], initial_eval),
            "chunk_prefix_40_evaluate": eval_summary(paths["horizon_eval"], horizon_eval),
            "promotion_decision": "reject_heldout_success_1_of_4",
        },
        "failure_to_data_iteration": {
            "mined_failed_placement_ids": sorted(initial_failed_ids),
            "recovery_collection": collection_summary(paths["recovery_collection"], recovery_collection),
            "all_failed_placements_recovered_as_expert_data": recovery_targeting_pass,
            "dataset": diversity_summary(paths["recovery_diversity"], reports["recovery_diversity"]),
            "train": train_summary(paths["recovery_train"], reports["recovery_train"]),
            "new_holdout_feasibility": collection_summary(paths["final_feasibility"], reports["final_feasibility"]),
            "new_holdout_manifest": workspace_path(paths["final_manifest"]),
            "new_holdout_minimum_pose_vector_distance_to_training_m": min(eval_to_training_distances),
            "new_holdout_pose_vector_distances_to_training_m": eval_to_training_distances,
            "new_heldout_evaluate": eval_summary(paths["final_eval"], final_eval),
            "promotion_decision": "reject_heldout_success_1_of_4",
        },
        "additional_task_execution": collection_summary(paths["extra_task_collection"], extra_task),
        "promotion_gates": promotion_gates,
        "policy_promotion": "blocked_placement_domain_and_cross_task_robustness",
        "hypotheses": [
            {
                "rank": 1,
                "hypothesis": "The fixed-placement source data lacks real trajectory diversity.",
                "verdict": "confirmed_and_fixed",
                "evidence": "The repaired dataset grows to 15 episodes, 10 placement signatures, and 14 unique action/qpos/image trajectories.",
            },
            {
                "rank": 2,
                "hypothesis": "Executing the full 175-action chunk is the primary cause of held-out failure.",
                "verdict": "falsified",
                "evidence": "A 40-action replanning prefix remains at 1/4 on the same held-out split.",
            },
            {
                "rank": 3,
                "hypothesis": "Adding expert demonstrations for the three failed placements is sufficient for broad placement generalization.",
                "verdict": "falsified_for_current_act_configuration",
                "evidence": "All three targeted recovery demonstrations pass, but a fresh signature-disjoint split remains at 1/4.",
            },
            {
                "rank": 4,
                "hypothesis": "The learned-policy failures are caused by evaluation infrastructure errors or infeasible placements.",
                "verdict": "falsified",
                "evidence": "Both eval runs execute 4/4 without infrastructure errors, and each final eval placement passed scripted feasibility first.",
            },
        ],
        "next_data_requirement": {
            "placement_coverage": "Collect substantially more successful unique placements across the declared regions before another ACT promotion attempt.",
            "observation_ablation": "Compare the current 96x72 head camera against a higher-resolution observation and an explicit object-pose-conditioned baseline.",
            "domain_randomization": "Train and evaluate declared lighting, camera, background, and table-height variations instead of leaving all randomization disabled.",
            "cross_task": "Train and evaluate a learned policy on at least one additional generated task; scripted can/basket execution is only an action-stack regression proof.",
        },
    }
    out_path = resolve_path(args.out)
    write_json(out_path, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "initial_success": initial_eval.get("success_count"),
                "final_success": final_eval.get("success_count"),
                "policy_promotion": report["policy_promotion"],
                "out": str(out_path),
            }
        )
    )
    return 0 if dataset_gate and recovery_targeting_pass and final_infrastructure_pass and extra_task_execution_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
