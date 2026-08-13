#!/usr/bin/env python3
"""Replay converted ACT actions in RoboTwin to validate adapter semantics."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import h5py
import imageio.v2 as imageio
import numpy as np

from run_generated_act_eval_smoke import (
    build_task_class,
    pose_record,
    resize_rgb,
    save_rgb,
    utc_now,
)
from run_generated_selection2env_rollout_probe import (
    infer_task_binding,
    load_robotwin_args,
    read_json,
    write_json,
)


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay ACT HDF5 actions in a generated RoboTwin task.")
    parser.add_argument("--robotwin-root", required=True)
    parser.add_argument("--placement", required=True)
    parser.add_argument("--task-id", default="task_apple_plate")
    parser.add_argument("--act-hdf5", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--task-config", default="demo_clean")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-steps", type=int, default=0, help="Zero replays the full action sequence.")
    parser.add_argument("--fps", type=int, default=12)
    parser.add_argument("--camera-width", type=int, default=160)
    parser.add_argument("--camera-height", type=int, default=120)
    parser.add_argument("--video-stride", type=int, default=2)
    args = parser.parse_args()

    robotwin_root = Path(args.robotwin_root).expanduser().resolve()
    placement_path = Path(args.placement).expanduser().resolve()
    act_hdf5_path = Path(args.act_hdf5).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    placement = read_json(placement_path)
    binding = infer_task_binding(args.task_id, placement)
    with h5py.File(act_hdf5_path, "r") as source:
        source_qpos = np.asarray(source["observations/qpos"], dtype=np.float32)
        actions = np.asarray(source["action"], dtype=np.float32)
        alignment = str(source.attrs.get("temporal_alignment", ""))
    if source_qpos.ndim != 2 or actions.shape != source_qpos.shape or actions.shape[1] != 14:
        raise ValueError(f"Expected equal [T,14] qpos/action arrays, got {source_qpos.shape} and {actions.shape}")
    action_limit = len(actions) if args.max_steps <= 0 else min(args.max_steps, len(actions))

    report_path = out_dir / "replay_report.json"
    report: dict[str, Any] = {
        "schema_version": "alchedata.act_action_replay_probe.v0",
        "status": "started",
        "started_at": utc_now(),
        "task_id": args.task_id,
        "task_config": args.task_config,
        "seed": args.seed,
        "placement": str(placement_path),
        "act_hdf5": str(act_hdf5_path),
        "source_temporal_alignment": alignment,
        "source_timestep_count": int(len(actions)),
        "scheduled_action_count": int(action_limit),
        "task_binding": binding,
        "claim_boundary": (
            "This probe replays converted expert qpos actions from a fresh deterministic task reset. "
            "Task success supports action ordering and semantics; it does not test learned-policy inference."
        ),
    }
    write_json(report_path, report)

    frames: list[np.ndarray] = []
    executed_actions: list[list[float]] = []
    previous_cwd = Path.cwd()
    infrastructure_error = None
    execution_complete = False
    task_success = False
    task = None
    try:
        os.chdir(robotwin_root)
        sys.path.insert(0, str(robotwin_root))
        import sapien.core as sapien
        from envs._base_task import Base_Task
        from envs.utils import create_actor, create_sapien_urdf_obj

        task_class = build_task_class(Base_Task, sapien, create_actor, create_sapien_urdf_obj)
        task = task_class(placement, binding)
        rt_args = load_robotwin_args(robotwin_root, args.task_config, save_path=out_dir / "episode_000")
        rt_args["task_name"] = f"generated_selection2env_{args.task_id}"
        rt_args["data_type"]["third_view"] = True

        setup_started = time.perf_counter()
        task.setup_demo(now_ep_num=0, seed=args.seed, **rt_args)
        task.step_lim = max(action_limit + 10, 1000)
        setup_seconds = time.perf_counter() - setup_started
        initial_poses = {name: pose_record(actor) for name, actor in task.placement_objects.items()}
        observation = task.get_obs()
        runtime_qpos = np.asarray(observation["joint_action"]["vector"], dtype=np.float32)
        initial_qpos_abs_error = np.abs(runtime_qpos - source_qpos[0])
        initial_head = observation["observation"]["head_camera"]["rgb"]
        initial_observer = observation["third_view_rgb"]
        save_rgb(out_dir / "initial_head_camera.png", initial_head)
        save_rgb(out_dir / "initial_observer_camera.png", initial_observer)
        frames.append(resize_rgb(initial_observer, args.camera_width, args.camera_height))

        for index, action in enumerate(actions[:action_limit]):
            task.take_action(action)
            executed_actions.append(action.tolist())
            task_success = bool(task.eval_success or task.check_success())
            if index % max(args.video_stride, 1) == 0 or index + 1 == action_limit:
                post_observation = task.get_obs()
                frames.append(
                    resize_rgb(post_observation["third_view_rgb"], args.camera_width, args.camera_height)
                )

        final_observation = task.get_obs()
        final_head = final_observation["observation"]["head_camera"]["rgb"]
        final_observer = final_observation["third_view_rgb"]
        save_rgb(out_dir / "final_head_camera.png", final_head)
        save_rgb(out_dir / "final_observer_camera.png", final_observer)
        final_poses = {name: pose_record(actor) for name, actor in task.placement_objects.items()}
        task_success = bool(task.eval_success or task.check_success())
        execution_complete = len(executed_actions) == action_limit
        report.update(
            {
                "setup_seconds": setup_seconds,
                "initial_qpos_match": {
                    "max_abs_error": float(np.max(initial_qpos_abs_error)),
                    "mean_abs_error": float(np.mean(initial_qpos_abs_error)),
                    "runtime_qpos": runtime_qpos.tolist(),
                    "source_qpos": source_qpos[0].tolist(),
                },
                "initial_object_poses": initial_poses,
                "final_object_poses": final_poses,
                "relation_metrics": task.relation_metrics(),
            }
        )
    except Exception as exc:  # noqa: BLE001
        infrastructure_error = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
    finally:
        os.chdir(previous_cwd)

    video_path = out_dir / "observer_action_replay.mp4"
    if frames:
        imageio.mimsave(video_path, frames, fps=args.fps)
    action_trace_path = out_dir / "executed_actions.json"
    write_json(
        action_trace_path,
        {
            "schema_version": "alchedata.act_action_replay_trace.v0",
            "source": str(act_hdf5_path),
            "action_count": len(executed_actions),
            "actions": executed_actions,
        },
    )
    report.update(
        {
            "status": "pass_act_action_replay_execution" if execution_complete else "blocked_act_action_replay_execution",
            "finished_at": utc_now(),
            "execution_complete": execution_complete,
            "executed_action_count": len(executed_actions),
            "task_success": task_success,
            "policy_type": "expert_action_replay_not_learned",
            "infrastructure_error": infrastructure_error,
            "action_trace": str(action_trace_path),
            "observer_video": str(video_path) if video_path.exists() else None,
            "images": {
                "initial_head_camera": str(out_dir / "initial_head_camera.png"),
                "initial_observer_camera": str(out_dir / "initial_observer_camera.png"),
                "final_head_camera": str(out_dir / "final_head_camera.png"),
                "final_observer_camera": str(out_dir / "final_observer_camera.png"),
            },
        }
    )
    write_json(report_path, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "task_success": task_success,
                "executed_action_count": len(executed_actions),
                "report": str(report_path),
            }
        )
    )
    return 0 if execution_complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
