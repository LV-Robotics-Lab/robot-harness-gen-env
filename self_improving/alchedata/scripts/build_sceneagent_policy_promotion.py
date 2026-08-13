#!/usr/bin/env python3
"""Validate and bind the SceneAgent bounded learned-policy promotion evidence."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pose_conditioned_trajectory_policy import read_json, sha256_file, write_json
from video_evidence import probe_video


ROOT = Path(__file__).resolve().parents[1]
REMOTE_ROOT_MARKER = "/alchedata-self-improving-agents/"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def resolve_evidence_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.exists():
        return path.resolve()
    text = str(path)
    if REMOTE_ROOT_MARKER in text:
        relative = text.split(REMOTE_ROOT_MARKER, 1)[1]
        candidate = ROOT / relative
        if candidate.exists():
            return candidate.resolve()
    if not path.is_absolute():
        candidate = ROOT / path
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError(f"Evidence path is not available: {value}")


def validated_videos(report: dict[str, Any]) -> list[dict[str, Any]]:
    videos = []
    for episode in report["episodes"]:
        path = resolve_evidence_path(episode["observer_video"])
        evidence = probe_video(path)
        require(evidence["frame_count"] > 100, f"Policy video is too short: {path}")
        videos.append(
            {
                "placement_id": episode["placement_id"],
                "seed": episode["seed"],
                "path": str(path.relative_to(ROOT)),
                "sha256": sha256_file(path),
                **evidence,
            }
        )
    return videos


def validate_four_case_report(report_path: Path, randomized: bool) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    report = read_json(report_path)
    require(report["status"] == "pass_pose_conditioned_evaluate_execution", "Evaluation execution did not pass")
    require(report["episode_count"] == 4, "Expected exactly four evaluation episodes")
    require(report["execution_count"] == 4, "Expected four completed evaluation episodes")
    require(report["success_count"] == 4, "Expected four successful evaluation episodes")
    require(report["policy_success_rate"] == 1.0, "Expected 100% task success")
    require(report["all_eval_placements_held_out"] is True, "Evaluation placements are not held out")
    require(len(set(report["eval_pose_signatures"])) == 4, "Expected four unique evaluation signatures")
    require(report["model"]["predictor"] == "affine", "Unexpected predictor")
    contract = report["policy_contract"]
    require(contract["learned_from_demonstrations"] is True, "Policy is not marked learned")
    require(contract["privileged_initial_object_pose"] is True, "Privileged observation boundary is missing")
    require(contract["scripted_expert_called_at_evaluation"] is False, "Scripted expert was called during evaluation")
    require(all(episode["held_out_placement"] for episode in report["episodes"]), "Episode holdout flag failed")
    require(all(episode["policy_success"] for episode in report["episodes"]), "Episode task verifier failed")
    require(all(episode["execution_complete"] for episode in report["episodes"]), "Episode infrastructure failed")
    if randomized:
        domain = report["domain_randomization"]
        require(domain["random_background"] is True, "Background randomization is disabled")
        require(domain["random_light"] is True, "Light randomization is disabled")
        require(domain["random_table_height"] > 0, "Table-height randomization is disabled")
        require(domain["random_head_camera_dis"] > 0, "Camera randomization is disabled")
        for episode in report["episodes"]:
            realized = episode["realized_domain_randomization"]
            require(realized["random_background_enabled"] is True, "Background randomization was not realized")
            require(realized["random_light_enabled"] is True, "Light randomization was not realized")
            require(realized["random_head_camera_dis_max_m"] > 0, "Camera randomization was not realized")
            require(abs(realized["sampled_table_z_bias_m"]) > 0, "Sampled table-height bias is zero")
    else:
        require(not any(bool(value) for value in report["domain_randomization"].values()), "Control report is randomized")
    return report, validated_videos(report)


def validate_second_task(report_path: Path, training_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    report = read_json(report_path)
    training = read_json(training_path)
    require(report["task_id"] == "task_can_basket", "Second task is not can-to-basket")
    require(report["status"] == "pass_pose_conditioned_evaluate_execution", "Second-task execution failed")
    require(report["episode_count"] == 3, "Expected exactly three second-task episodes")
    require(report["execution_count"] == 3 and report["success_count"] == 3, "Second task did not pass 3/3")
    require(report["policy_success_rate"] == 1.0, "Second-task success rate is not 100%")
    training_seeds = {int(row["seed"]) for row in training["training_episodes"]}
    eval_seeds = {int(row["seed"]) for row in report["episodes"]}
    require(len(eval_seeds) == 3, "Second-task evaluation seeds are not unique")
    require(not (training_seeds & eval_seeds), "Second-task evaluation seeds overlap training seeds")
    require(report["model"]["checkpoint_sha256"] == training["checkpoint_sha256"], "Second-task checkpoint mismatch")
    require(all(row["policy_success"] for row in report["episodes"]), "Second-task verifier failed")
    return report, validated_videos(report)


def build_promotion(
    heldout_path: Path,
    randomized_path: Path,
    apple_training_path: Path,
    second_task_path: Path,
    second_training_path: Path,
    parent_act_path: Path,
    diagnosis_path: Path,
    asset_receipt_path: Path,
    out_path: Path,
) -> dict[str, Any]:
    heldout, heldout_videos = validate_four_case_report(heldout_path, randomized=False)
    randomized, randomized_videos = validate_four_case_report(randomized_path, randomized=True)
    apple_training = read_json(apple_training_path)
    second_task, second_videos = validate_second_task(second_task_path, second_training_path)
    second_training = read_json(second_training_path)
    parent_act = read_json(parent_act_path)
    diagnosis = read_json(diagnosis_path)
    asset_receipt = read_json(asset_receipt_path)

    require(heldout["model"]["checkpoint_sha256"] == apple_training["checkpoint_sha256"], "Apple checkpoint mismatch")
    require(randomized["model"]["checkpoint_sha256"] == apple_training["checkpoint_sha256"], "Randomized checkpoint mismatch")
    require(heldout["eval_pose_signatures"] == randomized["eval_pose_signatures"], "Evaluation splits differ")
    require(parent_act["episode_count"] == 4 and parent_act["success_count"] == 1, "Parent ACT result is not 1/4")
    require(asset_receipt["status"] == "pass_official_robotwin_background_asset", "Background asset receipt failed")
    require(asset_receipt["operational_subset"]["file_count"] >= 256, "Background asset subset is too small")

    record = {
        "schema_version": "alchedata.sceneagent_policy_promotion.v0",
        "status": "pass_bounded_pose_conditioned_policy_promotion",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "decision": "accept_for_bounded_sceneagent_selection2env_gate",
        "candidate": {
            "policy_type": apple_training["policy_type"],
            "checkpoint": str(Path(apple_training["checkpoint"]).name),
            "checkpoint_sha256": apple_training["checkpoint_sha256"],
            "demonstration_count": apple_training["demonstration_count"],
            "unique_training_placement_count": apple_training["unique_placement_count"],
            "predictor": "affine",
            "privileged_initial_object_pose": True,
            "open_loop_after_initial_observation": True,
        },
        "parent_state": {
            "status": "retained_negative_act_result",
            "evaluate_report": str(parent_act_path.relative_to(ROOT)),
            "evaluate_report_sha256": sha256_file(parent_act_path),
            "checkpoint_sha256": parent_act.get("model", {}).get("checkpoint_sha256"),
            "success_count": parent_act["success_count"],
            "episode_count": parent_act["episode_count"],
            "diagnosis": str(diagnosis_path.relative_to(ROOT)),
            "diagnosis_sha256": sha256_file(diagnosis_path),
        },
        "gates": {
            "heldout_varied_placement": {
                "status": "pass",
                "success_count": heldout["success_count"],
                "episode_count": heldout["episode_count"],
                "signature_disjoint": heldout["all_eval_placements_held_out"],
                "report": str(heldout_path.relative_to(ROOT)),
                "report_sha256": sha256_file(heldout_path),
                "videos": heldout_videos,
            },
            "declared_domain_randomization": {
                "status": "pass",
                "success_count": randomized["success_count"],
                "episode_count": randomized["episode_count"],
                "requested": randomized["domain_randomization"],
                "sampled_table_z_bias_m": [
                    row["realized_domain_randomization"]["sampled_table_z_bias_m"]
                    for row in randomized["episodes"]
                ],
                "report": str(randomized_path.relative_to(ROOT)),
                "report_sha256": sha256_file(randomized_path),
                "videos": randomized_videos,
                "asset_receipt": str(asset_receipt_path.relative_to(ROOT)),
                "asset_receipt_sha256": sha256_file(asset_receipt_path),
            },
            "cross_task_learned_policy": {
                "status": "pass",
                "task_id": second_task["task_id"],
                "success_count": second_task["success_count"],
                "episode_count": second_task["episode_count"],
                "training_seeds": sorted(int(row["seed"]) for row in second_training["training_episodes"]),
                "heldout_eval_seeds": sorted(int(row["seed"]) for row in second_task["episodes"]),
                "checkpoint_sha256": second_training["checkpoint_sha256"],
                "report": str(second_task_path.relative_to(ROOT)),
                "report_sha256": sha256_file(second_task_path),
                "videos": second_videos,
                "placement_boundary": "fixed placement; seed holdout only",
            },
            "promotion_record": {"status": "pass"},
        },
        "rollback_state": {
            "action": "restore_parent_act_checkpoint_and_negative_result",
            "parent_evaluate_report": str(parent_act_path.relative_to(ROOT)),
            "trigger": "candidate verifier regression, evidence hash mismatch, or use outside declared policy boundary",
        },
        "claim_boundary": (
            "Promotion is limited to a privileged, initial-pose-conditioned, open-loop learned trajectory baseline in "
            "the generated RoboTwin apple/plate task, plus fixed-placement seed holdout on can/basket. It does not "
            "promote ACT, establish visual robustness, prove language-conditioned control, or establish broad task transfer."
        ),
        "parent_diagnosis_status": diagnosis.get("status"),
    }
    write_json(out_path, record)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--heldout", required=True)
    parser.add_argument("--randomized", required=True)
    parser.add_argument("--apple-training", required=True)
    parser.add_argument("--second-task", required=True)
    parser.add_argument("--second-training", required=True)
    parser.add_argument("--parent-act", required=True)
    parser.add_argument("--diagnosis", required=True)
    parser.add_argument("--asset-receipt", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    record = build_promotion(
        *(resolve_evidence_path(value) for value in (
            args.heldout,
            args.randomized,
            args.apple_training,
            args.second_task,
            args.second_training,
            args.parent_act,
            args.diagnosis,
            args.asset_receipt,
        )),
        Path(args.out).expanduser().resolve(),
    )
    print(json.dumps({"status": record["status"], "decision": record["decision"], "out": args.out}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
