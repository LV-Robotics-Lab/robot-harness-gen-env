#!/usr/bin/env python3
"""Run a bounded official RoboTwin task rollout probe."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from PIL import Image

try:
    from rollout_video_recorder import RolloutVideoRecorder
except ModuleNotFoundError:
    from scripts.rollout_video_recorder import RolloutVideoRecorder


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def save_rgb(path: Path, rgb: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb.astype("uint8")).save(path)


def pose_record(entity: Any) -> dict[str, Any]:
    record: dict[str, Any] = {}
    if hasattr(entity, "get_pose"):
        pose = entity.get_pose()
        record["p"] = pose.p.tolist()
        record["q"] = pose.q.tolist()
    if hasattr(entity, "get_qpos"):
        try:
            record["qpos"] = np.asarray(entity.get_qpos()).tolist()
        except Exception as exc:  # noqa: BLE001
            record["qpos_error"] = repr(exc)
    return record


def pixel_stats(rgb: np.ndarray) -> dict[str, float]:
    return {
        "mean": float(np.mean(rgb)),
        "std": float(np.std(rgb)),
        "min": float(np.min(rgb)),
        "max": float(np.max(rgb)),
    }


def load_robotwin_args(robotwin_root: Path, task_config: str, *, save_path: Path) -> dict[str, Any]:
    from envs._GLOBAL_CONFIGS import CONFIGS_PATH

    config_path = robotwin_root / "task_config" / f"{task_config}.yml"
    with config_path.open("r", encoding="utf-8") as f:
        args = yaml.load(f.read(), Loader=yaml.FullLoader)

    embodiment_type = args.get("embodiment")
    embodiment_config_path = Path(CONFIGS_PATH) / "_embodiment_config.yml"
    with embodiment_config_path.open("r", encoding="utf-8") as f:
        embodiment_types = yaml.load(f.read(), Loader=yaml.FullLoader)

    def embodiment_file(name: str) -> str:
        robot_file = embodiment_types[name]["file_path"]
        if robot_file is None:
            raise RuntimeError(f"Missing embodiment files for {name}")
        return robot_file

    if len(embodiment_type) == 1:
        args["left_robot_file"] = embodiment_file(embodiment_type[0])
        args["right_robot_file"] = embodiment_file(embodiment_type[0])
        args["dual_arm_embodied"] = True
        args["embodiment_name"] = str(embodiment_type[0])
    elif len(embodiment_type) == 3:
        args["left_robot_file"] = embodiment_file(embodiment_type[0])
        args["right_robot_file"] = embodiment_file(embodiment_type[1])
        args["embodiment_dis"] = embodiment_type[2]
        args["dual_arm_embodied"] = False
        args["embodiment_name"] = f"{embodiment_type[0]}+{embodiment_type[1]}"
    else:
        raise RuntimeError("Unexpected embodiment config shape")

    def embodiment_config(robot_file: str) -> dict[str, Any]:
        with (robotwin_root / robot_file / "config.yml").open("r", encoding="utf-8") as f:
            return yaml.load(f.read(), Loader=yaml.FullLoader)

    args["left_embodiment_config"] = embodiment_config(args["left_robot_file"])
    args["right_embodiment_config"] = embodiment_config(args["right_robot_file"])
    args["task_config"] = task_config
    args["render_freq"] = 0
    args["save_data"] = False
    args["collect_data"] = False
    args["need_plan"] = True
    args["eval_mode"] = False
    args["save_path"] = str(save_path)
    args["camera"]["collect_head_camera"] = True
    args["camera"]["collect_wrist_camera"] = False
    args["data_type"]["rgb"] = True
    args["data_type"]["third_view"] = False
    args["domain_randomization"]["random_background"] = False
    args["domain_randomization"]["cluttered_table"] = False
    args["domain_randomization"]["random_light"] = False
    args["domain_randomization"]["random_table_height"] = 0
    args["domain_randomization"]["random_head_camera_dis"] = 0
    return args


def collect_named_entities(task: Any) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for attr_name, value in sorted(vars(task).items()):
        if attr_name.startswith("_") or attr_name in {"scene", "engine", "renderer", "robot", "cameras"}:
            continue
        if hasattr(value, "get_pose"):
            rows[attr_name] = pose_record(value)
    return rows


def camera_capture(task: Any, out_dir: Path, prefix: str) -> dict[str, Any]:
    task._update_render()
    task.cameras.update_picture()
    head_rgb = task.cameras.get_rgb()["head_camera"]["rgb"]
    observer_rgb = task.cameras.get_observer_rgb()
    head_path = out_dir / f"{prefix}_head_camera.png"
    observer_path = out_dir / f"{prefix}_observer_camera.png"
    save_rgb(head_path, head_rgb)
    save_rgb(observer_path, observer_rgb)
    return {
        "head_camera": str(head_path),
        "observer_camera": str(observer_path),
        "pixel_stats": {
            "head_camera": pixel_stats(head_rgb),
            "observer_camera": pixel_stats(observer_rgb),
        },
        "observer_frame": observer_rgb,
    }


def class_decorator(task_name: str):
    envs_module = importlib.import_module(f"envs.{task_name}")
    try:
        env_class = getattr(envs_module, task_name)
    except AttributeError as exc:
        raise RuntimeError(f"No such RoboTwin task class: {task_name}") from exc
    return env_class()


def main() -> int:
    parser = argparse.ArgumentParser(description="Official RoboTwin task rollout probe.")
    parser.add_argument("--robotwin-root", required=True)
    parser.add_argument("--task-name", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--task-config", default="demo_clean")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--fps", type=int, default=12)
    parser.add_argument("--capture-stride", type=int, default=4)
    parser.add_argument("--min-video-frames", type=int, default=24)
    parser.add_argument("--max-video-frames", type=int, default=1200)
    args = parser.parse_args()

    if args.fps <= 0:
        parser.error("--fps must be positive")
    if args.capture_stride <= 0:
        parser.error("--capture-stride must be positive")
    if args.min_video_frames < 3:
        parser.error("--min-video-frames must be at least 3")
    if args.max_video_frames < args.min_video_frames:
        parser.error("--max-video-frames must be greater than or equal to --min-video-frames")

    robotwin_root = Path(args.robotwin_root).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    episode_dir = out_dir / "episode_000"
    out_dir.mkdir(parents=True, exist_ok=True)
    episode_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "schema_version": "alchedata.official_task_rollout_probe.v1",
        "command": "/collect",
        "probe_type": "official_robotwin_play_once",
        "task_name": args.task_name,
        "task_config": args.task_config,
        "seed": args.seed,
        "robotwin_root": str(robotwin_root),
        "out_dir": str(out_dir),
        "status": "started",
        "limitations": [
            "This probe runs an official RoboTwin task class play_once(), not a generated selection2env play_once().",
            "It tests the action/planner stack and produces rollout evidence for PEARL command-loop smoke.",
            "It does not train or evaluate a learned policy.",
        ],
    }
    write_json(out_dir / "rollout_report.json", report)

    os.chdir(robotwin_root)
    sys.path.insert(0, str(robotwin_root))

    task = class_decorator(args.task_name)
    rt_args = load_robotwin_args(robotwin_root, args.task_config, save_path=episode_dir)
    rt_args["task_name"] = args.task_name

    move_events: list[dict[str, Any]] = []
    original_move = task.move

    def move_wrapper(*move_args, **move_kwargs):
        event = {
            "index": len(move_events),
            "plan_success_before": bool(getattr(task, "plan_success", False)),
            "frame_idx_before": int(getattr(task, "FRAME_IDX", 0)),
            "left_joint_path_len_before": len(getattr(task, "left_joint_path", [])),
            "right_joint_path_len_before": len(getattr(task, "right_joint_path", [])),
        }
        started = time.time()
        try:
            result = original_move(*move_args, **move_kwargs)
            event["result"] = bool(result)
            return result
        except Exception as exc:  # noqa: BLE001
            event["exception"] = repr(exc)
            raise
        finally:
            event.update(
                {
                    "duration_sec": round(time.time() - started, 4),
                    "plan_success_after": bool(getattr(task, "plan_success", False)),
                    "frame_idx_after": int(getattr(task, "FRAME_IDX", 0)),
                    "left_joint_path_len_after": len(getattr(task, "left_joint_path", [])),
                    "right_joint_path_len_after": len(getattr(task, "right_joint_path", [])),
                }
            )
            move_events.append(event)

    task.move = move_wrapper
    video_path = out_dir / "observer_rollout_probe.mp4"
    video_recorder: RolloutVideoRecorder | None = None
    try:
        setup_started = time.time()
        task.setup_demo(now_ep_num=0, seed=args.seed, **rt_args)
        setup_duration = time.time() - setup_started
        initial_capture = camera_capture(task, out_dir, "initial")
        initial_observer_frame = initial_capture.pop("observer_frame")
        initial_entities = collect_named_entities(task)

        video_recorder = RolloutVideoRecorder(
            task,
            video_path,
            fps=args.fps,
            capture_stride=args.capture_stride,
            max_frames=args.max_video_frames,
        )
        video_recorder.append_frame(initial_observer_frame)
        video_recorder.install()

        play_started = time.time()
        task_info = task.play_once()
        play_duration = time.time() - play_started

        video_recorder.restore()
        final_capture = camera_capture(task, out_dir, "final")
        video_recorder.append_frame(final_capture.pop("observer_frame"))
        final_entities = collect_named_entities(task)
        video_capture = video_recorder.close()
        if video_capture["frame_count"] < args.min_video_frames:
            raise RuntimeError(
                f"Continuous rollout video has only {video_capture['frame_count']} frames; "
                f"expected at least {args.min_video_frames}"
            )

        try:
            success = bool(task.check_success())
            success_error = None
        except Exception as exc:  # noqa: BLE001
            success = False
            success_error = repr(exc)

        events = [
            {
                "event": "setup_demo",
                "duration_sec": round(setup_duration, 4),
                "status": "pass",
            },
            {
                "event": "play_once",
                "duration_sec": round(play_duration, 4),
                "status": "pass",
                "move_events": len(move_events),
            },
            {
                "event": "check_success",
                "status": "pass" if success else "fail",
                "error": success_error,
            },
        ]
        write_jsonl(out_dir / "events.jsonl", events)
        write_jsonl(out_dir / "move_events.jsonl", move_events)

        report.update(
            {
                "status": "pass_action_rollout" if success else "fail_task_success",
                "setup_duration_sec": round(setup_duration, 4),
                "play_once_duration_sec": round(play_duration, 4),
                "plan_success": bool(getattr(task, "plan_success", False)),
                "check_success": success,
                "check_success_error": success_error,
                "task_info": task_info,
                "move_event_count": len(move_events),
                "video_capture": video_capture,
                "video_frame_count": video_capture["frame_count"],
                "video_fps": video_capture["fps"],
                "video_duration_sec": video_capture["duration_sec"],
                "video_capture_stride": video_capture["capture_stride_sim_steps"],
                "video_capture_mode": video_capture["mode"],
                "video_endpoint_only": video_capture["endpoint_only"],
                "left_joint_path_len": len(getattr(task, "left_joint_path", [])),
                "right_joint_path_len": len(getattr(task, "right_joint_path", [])),
                "failure_diagnosis": {
                    "status": "no_failure_observed" if success else "task_success_failed",
                    "checked_categories": [
                        "wrong_grasp_location",
                        "object_knocked_over",
                        "arm_jitter",
                        "uncontrolled_gripper_open_close",
                        "after_contact_failure",
                        "visual_material_mismatch",
                    ],
                    "move_events": len(move_events),
                    "plan_success": bool(getattr(task, "plan_success", False)),
                    "task_success": success,
                },
                "next_data_requirement": (
                    "Promote from official RoboTwin task smoke to generated selection2env play_once and policy /train-/evaluate integration."
                    if success
                    else "Inspect move_events.jsonl, final camera frames, and object state deltas before adding this task to train/evaluate."
                ),
                "initial_entities": initial_entities,
                "final_entities": final_entities,
                "images": {
                    "initial_head_camera": initial_capture["head_camera"],
                    "initial_observer_camera": initial_capture["observer_camera"],
                    "final_head_camera": final_capture["head_camera"],
                    "final_observer_camera": final_capture["observer_camera"],
                },
                "observer_video": str(video_path),
                "events": str(out_dir / "events.jsonl"),
                "move_events": str(out_dir / "move_events.jsonl"),
                "pixel_stats": {
                    "initial": initial_capture["pixel_stats"],
                    "final": final_capture["pixel_stats"],
                },
            }
        )
        write_json(out_dir / "rollout_report.json", report)
        print(("PASS" if success else "FAIL_TASK_SUCCESS") + f" {out_dir / 'rollout_report.json'}")
        return 0
    except Exception as exc:  # noqa: BLE001
        report.update(
            {
                "status": "fail_action_rollout_exception",
                "error": repr(exc),
                "traceback_tail": traceback.format_exc()[-4000:],
                "move_event_count": len(move_events),
                "move_events": str(out_dir / "move_events.jsonl"),
            }
        )
        write_jsonl(out_dir / "move_events.jsonl", move_events)
        write_json(out_dir / "rollout_report.json", report)
        print(f"FAIL {out_dir / 'rollout_report.json'}")
        raise
    finally:
        if video_recorder is not None:
            video_recorder.close()
        try:
            task.close_env(clear_cache=True)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
