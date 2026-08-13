#!/usr/bin/env python3
"""Evaluate a privileged pose-conditioned learned trajectory in generated RoboTwin tasks."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import imageio.v2 as imageio
import numpy as np

from placement_manifest_utils import fixed_placement_cases, load_placement_cases
from pose_conditioned_trajectory_policy import (
    PoseConditionedTrajectoryPolicy,
    runtime_feature,
    sha256_file,
    write_json,
)
from run_generated_act_eval_smoke import (
    build_task_class,
    diagnose_episode,
    pose_record,
    resize_rgb,
    save_rgb,
    utc_now,
    write_jsonl,
)
from run_generated_selection2env_rollout_probe import (
    apply_domain_randomization,
    infer_task_binding,
    load_robotwin_args,
    read_json,
    requested_domain_randomization,
)


ROOT = Path(__file__).resolve().parents[1]


def run_episode(
    task_class: type,
    placement: dict[str, Any],
    binding: dict[str, str],
    policy: PoseConditionedTrajectoryPolicy,
    args: argparse.Namespace,
    placement_case: dict[str, Any],
    episode_dir: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    seed = int(placement_case["seed"])
    events: list[dict[str, Any]] = []
    executed_actions: list[list[float]] = []
    frames: list[np.ndarray] = []
    initial_poses: dict[str, dict[str, list[float]]] = {}
    final_poses: dict[str, dict[str, list[float]]] = {}
    relation_metrics_snapshot = None
    infrastructure_error = None
    success = False
    setup_seconds = None
    prediction_record: dict[str, Any] | None = None
    realized_domain_randomization: dict[str, Any] | None = None
    task = task_class(placement, binding)

    rt_args = load_robotwin_args(Path(args.robotwin_root), args.task_config, save_path=episode_dir)
    rt_args["task_name"] = f"pose_conditioned_selection2env_{args.task_id}"
    rt_args["data_type"]["third_view"] = True
    apply_domain_randomization(rt_args, requested_domain_randomization(args))

    try:
        started = time.perf_counter()
        task.setup_demo(now_ep_num=0, seed=seed, **rt_args)
        task.step_lim = args.max_steps
        setup_seconds = time.perf_counter() - started
        realized_domain_randomization = {
            "random_background_enabled": bool(task.random_background),
            "random_light_enabled": bool(task.random_light),
            "random_head_camera_dis_max_m": float(task.random_head_camera_dis),
            "sampled_table_z_bias_m": float(task.table_z_bias),
        }
        initial_poses = {name: pose_record(actor) for name, actor in task.placement_objects.items()}
        observation = task.get_obs()
        save_rgb(episode_dir / "initial_head_camera.png", observation["observation"]["head_camera"]["rgb"])
        save_rgb(episode_dir / "initial_observer_camera.png", observation["third_view_rgb"])
        frames.append(resize_rgb(observation["third_view_rgb"], args.camera_width, args.camera_height))

        feature = runtime_feature(task.placement_objects, binding["source_id"], binding["target_id"])
        actions, prediction_record = policy.predict(
            feature,
            predictor=args.predictor,
            extrapolation_margin=args.extrapolation_margin,
        )
        if not prediction_record["finite"]:
            raise RuntimeError("Pose-conditioned policy produced non-finite actions")
        if len(actions) > args.max_steps:
            raise RuntimeError(f"Predicted {len(actions)} actions but --max-steps is {args.max_steps}")
        events.append(
            {
                "at": utc_now(),
                "event": "trajectory_predicted",
                "seed": seed,
                "placement_id": placement_case["placement_id"],
                **prediction_record,
            }
        )

        for step, action in enumerate(actions):
            task.take_action(action)
            executed_actions.append(action.tolist())
            success = bool(task.eval_success or task.check_success())
            observation = task.get_obs()
            frames.append(resize_rgb(observation["third_view_rgb"], args.camera_width, args.camera_height))
            events.append(
                {
                    "at": utc_now(),
                    "event": "policy_step",
                    "seed": seed,
                    "step": step,
                    "success_verifier": success,
                }
            )
            if success:
                break
    except Exception as exc:  # noqa: BLE001
        infrastructure_error = f"{type(exc).__name__}: {exc}"
        events.append(
            {
                "at": utc_now(),
                "event": "episode_error",
                "seed": seed,
                "error": infrastructure_error,
                "traceback": traceback.format_exc(),
            }
        )
    finally:
        if getattr(task, "placement_objects", None):
            final_poses = {name: pose_record(actor) for name, actor in task.placement_objects.items()}
            try:
                relation_metrics_snapshot = task.relation_metrics()
            except Exception:
                relation_metrics_snapshot = None
            try:
                observation = task.get_obs()
                save_rgb(episode_dir / "final_head_camera.png", observation["observation"]["head_camera"]["rgb"])
                save_rgb(episode_dir / "final_observer_camera.png", observation["third_view_rgb"])
            except Exception:
                pass
        try:
            task.close_env(clear_cache=True)
        except Exception:
            pass

    action_trace_path = episode_dir / "action_trace.json"
    write_json(
        action_trace_path,
        {
            "schema_version": "alchedata.pose_conditioned_action_trace.v0",
            "seed": seed,
            "action_count": len(executed_actions),
            "actions": executed_actions,
        },
    )
    events_path = episode_dir / "events.jsonl"
    write_jsonl(events_path, events)
    video_path = episode_dir / "observer_policy_rollout.mp4"
    if frames:
        imageio.mimsave(video_path, frames, fps=args.fps, macro_block_size=1)
    diagnosis = diagnose_episode(
        initial_poses,
        final_poses,
        binding,
        executed_actions,
        success,
        infrastructure_error,
    )
    diagnosis_path = episode_dir / "failure_diagnosis.json"
    write_json(diagnosis_path, diagnosis)
    episode_report = {
        "schema_version": "alchedata.pose_conditioned_evaluate_episode.v0",
        "command": "/evaluate",
        "task_id": args.task_id,
        "seed": seed,
        "placement_id": placement_case["placement_id"],
        "placement": str(placement_case["placement_path"]),
        "placement_sha256": sha256_file(Path(placement_case["placement_path"])),
        "placement_split": placement_case["split"],
        "pose_signature": placement_case.get("pose_signature"),
        "pose_vector": placement_case.get("pose_vector"),
        "held_out_placement": bool(
            placement_case.get("pose_signature")
            and placement_case["pose_signature"] not in policy.metadata["training_pose_signatures"]
        ),
        "status": (
            "completed_policy_success"
            if success
            else "completed_policy_failure"
            if infrastructure_error is None
            else "blocked_infrastructure_error"
        ),
        "execution_complete": infrastructure_error is None,
        "policy_success": success,
        "policy_step_count": len(executed_actions),
        "setup_seconds": setup_seconds,
        "prediction": prediction_record,
        "realized_domain_randomization": realized_domain_randomization,
        "relation_metrics": relation_metrics_snapshot,
        "initial_poses": initial_poses,
        "final_poses": final_poses,
        "failure_diagnosis": str(diagnosis_path),
        "events": str(events_path),
        "action_trace": str(action_trace_path),
        "observer_video": str(video_path) if video_path.exists() else None,
        "infrastructure_error": infrastructure_error,
    }
    write_json(episode_dir / "episode_report.json", episode_report)
    return episode_report, events


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robotwin-root", default=str(ROOT / "external" / "RoboTwin"))
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--placement")
    parser.add_argument("--placement-manifest")
    parser.add_argument("--placement-split", default="eval")
    parser.add_argument("--task-id", default="task_apple_plate")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--task-config", default="demo_clean")
    parser.add_argument("--seed", action="append", type=int, dest="seeds")
    parser.add_argument("--predictor", choices=["affine", "rbf", "blend", "nearest"], default="affine")
    parser.add_argument("--extrapolation-margin", type=float, default=0.35)
    parser.add_argument("--max-steps", type=int, default=220)
    parser.add_argument("--fps", type=int, default=8)
    parser.add_argument("--camera-width", type=int, default=640)
    parser.add_argument("--camera-height", type=int, default=480)
    parser.add_argument("--random-background", action="store_true")
    parser.add_argument("--cluttered-table", action="store_true")
    parser.add_argument("--random-light", action="store_true")
    parser.add_argument("--random-table-height", type=float, default=0.0)
    parser.add_argument("--random-head-camera-dis", type=float, default=0.0)
    args = parser.parse_args()
    if not args.placement_manifest and not args.placement:
        parser.error("one of --placement-manifest or --placement is required")
    if args.extrapolation_margin < 0:
        parser.error("--extrapolation-margin must be non-negative")

    args.robotwin_root = str(Path(args.robotwin_root).expanduser().resolve())
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    policy = PoseConditionedTrajectoryPolicy(Path(args.checkpoint), Path(args.metadata))
    if args.placement_manifest:
        _, placement_cases = load_placement_cases(
            Path(args.placement_manifest),
            args.placement_split,
            args.seeds,
        )
        placement_path = None
    else:
        seeds = args.seeds or [4]
        placement_path = Path(args.placement).expanduser().resolve()
        placement_cases = fixed_placement_cases(placement_path, seeds)

    first_placement = read_json(placement_cases[0]["placement_path"])
    binding = infer_task_binding(args.task_id, first_placement)
    training_signatures = set(policy.metadata["training_pose_signatures"])
    eval_signatures = {
        str(case["pose_signature"])
        for case in placement_cases
        if case.get("pose_signature")
    }
    all_eval_placements_held_out = bool(
        eval_signatures
        and len(eval_signatures) == len(placement_cases)
        and not (eval_signatures & training_signatures)
    )
    domain_randomization = requested_domain_randomization(args)
    report_path = out_dir / "evaluate_report.json"
    report: dict[str, Any] = {
        "schema_version": "alchedata.pose_conditioned_evaluate.v0",
        "command": "/evaluate",
        "status": "started",
        "started_at": utc_now(),
        "task_id": args.task_id,
        "task_config": args.task_config,
        "argv": sys.argv,
        "placement": str(placement_path) if placement_path else None,
        "placement_manifest": str(Path(args.placement_manifest).expanduser().resolve()) if args.placement_manifest else None,
        "placement_split": args.placement_split if args.placement_manifest else "fixed",
        "all_eval_placements_held_out": all_eval_placements_held_out,
        "eval_pose_signatures": sorted(eval_signatures),
        "training_pose_signatures": sorted(training_signatures),
        "task_binding": binding,
        "domain_randomization": domain_randomization,
        "model": {
            "policy_type": policy.metadata["policy_type"],
            "predictor": args.predictor,
            "checkpoint": str(policy.checkpoint_path),
            "checkpoint_sha256": sha256_file(policy.checkpoint_path),
            "metadata": str(policy.metadata_path),
            "metadata_sha256": sha256_file(policy.metadata_path),
            "demonstration_count": policy.metadata["demonstration_count"],
            "unique_placement_count": policy.metadata["unique_placement_count"],
            "canonical_action_count": policy.metadata["canonical_action_count"],
        },
        "policy_contract": {
            "learned_from_demonstrations": True,
            "privileged_initial_object_pose": True,
            "open_loop_after_initial_observation": True,
            "visual_observation_used": False,
            "language_observation_used": False,
            "scripted_expert_called_at_evaluation": False,
        },
        "claim_boundary": policy.metadata["claim_boundary"],
        "episodes": [],
    }
    write_json(report_path, report)

    aggregate_events: list[dict[str, Any]] = []
    previous_cwd = Path.cwd()
    os.chdir(args.robotwin_root)
    sys.path.insert(0, args.robotwin_root)
    try:
        import sapien.core as sapien
        from envs._base_task import Base_Task
        from envs.utils import create_actor, create_sapien_urdf_obj

        task_class = build_task_class(Base_Task, sapien, create_actor, create_sapien_urdf_obj)
        for index, placement_case in enumerate(placement_cases):
            placement = read_json(placement_case["placement_path"])
            episode_binding = infer_task_binding(args.task_id, placement)
            episode_dir = out_dir / f"episode_{index:03d}_{placement_case['placement_id']}_seed_{placement_case['seed']}"
            episode, events = run_episode(
                task_class,
                placement,
                episode_binding,
                policy,
                args,
                placement_case,
                episode_dir,
            )
            report["episodes"].append(episode)
            aggregate_events.extend(events)
    except Exception as exc:  # noqa: BLE001
        report["top_level_error"] = f"{type(exc).__name__}: {exc}"
        report["top_level_traceback"] = traceback.format_exc()
    finally:
        os.chdir(previous_cwd)

    episode_count = len(placement_cases)
    execution_count = sum(1 for episode in report["episodes"] if episode.get("execution_complete"))
    success_count = sum(1 for episode in report["episodes"] if episode.get("policy_success"))
    infrastructure_pass = (
        episode_count > 0
        and execution_count == episode_count
        and "top_level_error" not in report
    )
    report.update(
        {
            "status": (
                "pass_pose_conditioned_evaluate_execution"
                if infrastructure_pass
                else "blocked_pose_conditioned_evaluate_execution"
            ),
            "finished_at": utc_now(),
            "episode_count": episode_count,
            "execution_count": execution_count,
            "success_count": success_count,
            "failure_count": episode_count - success_count,
            "policy_success_rate": success_count / episode_count if episode_count else 0.0,
            "policy_result": (
                "all_task_success"
                if success_count == episode_count and episode_count > 0
                else "partial_task_success"
                if success_count
                else "zero_task_success"
            ),
        }
    )
    events_path = out_dir / "events.jsonl"
    run_state_path = out_dir / "run_state.json"
    write_jsonl(events_path, aggregate_events)
    report["events"] = str(events_path)
    report["run_state"] = str(run_state_path)
    write_json(report_path, report)
    write_json(
        run_state_path,
        {
            "schema_version": "alchedata.pearl_run_state.v0",
            "command": "/evaluate",
            "state": "completed" if infrastructure_pass else "blocked",
            "task_id": args.task_id,
            "all_eval_placements_held_out": all_eval_placements_held_out,
            "domain_randomization": domain_randomization,
            "policy_success_rate": report["policy_success_rate"],
            "evaluate_report": str(report_path),
            "events": str(events_path),
            "updated_at": utc_now(),
        },
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "execution_count": execution_count,
                "success_count": success_count,
                "episode_count": episode_count,
                "report": str(report_path),
            }
        )
    )
    return 0 if infrastructure_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
