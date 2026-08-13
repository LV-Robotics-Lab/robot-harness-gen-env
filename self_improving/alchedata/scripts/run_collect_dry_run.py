#!/usr/bin/env python3
"""Run a bounded RoboTwin /collect dry-run for a selection2env placement."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import imageio.v2 as imageio
import numpy as np
import yaml
from PIL import Image


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def save_rgb(path: Path, rgb: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb.astype("uint8")).save(path)


def pose_record(actor) -> dict[str, list[float]]:
    pose = actor.get_pose()
    return {"p": pose.p.tolist(), "q": pose.q.tolist()}


def pixel_stats(rgb: np.ndarray) -> dict[str, float]:
    return {
        "mean": float(np.mean(rgb)),
        "std": float(np.std(rgb)),
        "min": float(np.min(rgb)),
        "max": float(np.max(rgb)),
    }


def load_robotwin_args(robotwin_root: Path, task_config: str) -> dict[str, Any]:
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
    args["task_name"] = "tabletop_selection2env_collect_dry_run"
    args["render_freq"] = 0
    args["save_data"] = False
    args["collect_data"] = False
    args["need_plan"] = False
    args["eval_mode"] = False
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


def main() -> int:
    parser = argparse.ArgumentParser(description="RoboTwin /collect dry-run for selection2env placements.")
    parser.add_argument("--robotwin-root", required=True)
    parser.add_argument("--placement", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--task-config", default="demo_clean")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--steps", type=int, default=96)
    parser.add_argument("--capture-every", type=int, default=24)
    parser.add_argument("--fps", type=int, default=12)
    args = parser.parse_args()
    if args.steps < 1:
        parser.error("--steps must be at least 1")
    if args.capture_every < 1:
        parser.error("--capture-every must be at least 1")
    if args.fps < 1:
        parser.error("--fps must be at least 1")

    robotwin_root = Path(args.robotwin_root).expanduser().resolve()
    placement_path = Path(args.placement).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    episode_dir = out_dir / "episode_000"
    obs_dir = episode_dir / "observations"
    out_dir.mkdir(parents=True, exist_ok=True)

    placement = read_json(placement_path)
    report: dict[str, Any] = {
        "schema_version": "alchedata.collect_dry_run.v0",
        "command": "/collect",
        "task_id": args.task_id,
        "status": "started",
        "placement": str(placement_path),
        "robotwin_root": str(robotwin_root),
        "out_dir": str(out_dir),
        "task_config": args.task_config,
        "seed": args.seed,
        "steps": args.steps,
        "capture_every": args.capture_every,
        "limitations": [
            "This dry-run loads the selected scene in RoboTwin Base_Task and records observations/object states.",
            "It does not execute a generated manipulation play_once policy.",
            "It does not claim task success, policy success rate, or train/eval completion.",
        ],
    }
    write_json(out_dir / "collect_report.json", report)

    os.chdir(robotwin_root)
    sys.path.insert(0, str(robotwin_root))

    import sapien.core as sapien
    from envs._base_task import Base_Task
    from envs.utils import create_actor, create_sapien_urdf_obj

    class TabletopCollectDryRun(Base_Task):
        def __init__(self, placement_spec: dict[str, Any]):
            super().__init__()
            self.placement_spec = placement_spec
            self.placement_objects = {}

        def setup_demo(self, **kwargs: Any) -> None:
            super()._init_task_env_(**kwargs)

        def load_actors(self) -> None:
            table_z = 0.741 + self.table_z_bias
            for obj in self.placement_spec["objects"]:
                pose_data = obj["pose"]
                xyz = list(pose_data["xyz"])
                if pose_data.get("z_policy") == "snap_to_tabletop_on_load":
                    xyz[2] = table_z
                qpos = list(pose_data.get("qpos", [1, 0, 0, 0]))
                if obj["asset_id"] == "003_plate" and qpos == [1, 0, 0, 0]:
                    qpos = [0.5, 0.5, 0.5, 0.5]

                metadata = obj.get("asset_metadata", {})
                defaults = metadata.get("placement_defaults", {})
                loader = defaults.get("loader")
                asset_type = metadata.get("asset_type", "rigid")
                if loader == "sapien_urdf" or asset_type == "articulated":
                    actor = create_sapien_urdf_obj(
                        self,
                        pose=sapien.Pose(xyz, qpos),
                        modelname=obj["asset_id"],
                        modelid=obj.get("model_id", 0),
                        fix_root_link=defaults.get("fix_root_link", obj.get("physical", {}).get("is_static", False)),
                    )
                    if "articulation_qpos" in defaults:
                        actor.set_qpos(defaults["articulation_qpos"])
                else:
                    actor = create_actor(
                        self,
                        pose=sapien.Pose(xyz, qpos),
                        modelname=obj["asset_id"],
                        scale=metadata.get("scale", (1, 1, 1)) or (1, 1, 1),
                        model_id=obj.get("model_id", 0),
                        convex=True,
                        is_static=obj.get("physical", {}).get("is_static", False),
                    )
                if actor is None:
                    raise RuntimeError(f"Failed to load asset {obj['asset_id']}")
                actor.set_name(obj["id"])
                self.placement_objects[obj["id"]] = actor

        def play_once(self):
            return {"dry_run": True, "policy_execution": "not_run"}

        def check_success(self):
            return False

    rt_args = load_robotwin_args(robotwin_root, args.task_config)
    rt_args["save_path"] = str(episode_dir)
    task = TabletopCollectDryRun(placement)

    try:
        task.setup_demo(now_ep_num=0, seed=args.seed, **rt_args)
        initial_poses = {name: pose_record(actor) for name, actor in task.placement_objects.items()}
        state_rows: list[dict[str, Any]] = []
        frame_paths: list[str] = []
        frame_arrays: list[np.ndarray] = []

        for step in range(args.steps + 1):
            task.scene.step()
            task.scene.update_render()
            states = {name: pose_record(actor) for name, actor in task.placement_objects.items()}
            state_rows.append({"step": step, "objects": states})
            task._update_render()
            task.cameras.update_picture()
            observer_rgb = task.cameras.get_observer_rgb()
            frame_arrays.append(observer_rgb)
            if step % args.capture_every == 0 or step == args.steps:
                head_rgb = task.cameras.get_rgb()["head_camera"]["rgb"]
                observer_path = obs_dir / f"observer_camera_{step:04d}.png"
                head_path = obs_dir / f"head_camera_{step:04d}.png"
                save_rgb(observer_path, observer_rgb)
                save_rgb(head_path, head_rgb)
                frame_paths.extend([str(observer_path), str(head_path)])

        if frame_arrays:
            imageio.mimsave(episode_dir / "observer_camera.mp4", frame_arrays, fps=args.fps)

        final_poses = {name: pose_record(actor) for name, actor in task.placement_objects.items()}
        pose_delta_norm_m = {}
        for name in final_poses:
            a = np.array(initial_poses[name]["p"], dtype=float)
            b = np.array(final_poses[name]["p"], dtype=float)
            pose_delta_norm_m[name] = float(np.linalg.norm(b - a))

        write_jsonl(episode_dir / "object_states.jsonl", state_rows)
        task_info = task.play_once()
        scene_info = {
            "episode_0": {
                "task_id": args.task_id,
                "placement": str(placement_path),
                "info": task_info,
                "objects": list(task.placement_objects.keys()),
            }
        }
        write_json(episode_dir / "scene_info.json", scene_info)

        manifest = {
            "schema_version": "alchedata.collect_dataset_manifest.v0",
            "command": "/collect",
            "task_id": args.task_id,
            "episode_count": 1,
            "episodes": [
                {
                    "episode_id": "episode_000",
                    "scene_info": str((episode_dir / "scene_info.json").relative_to(out_dir)),
                    "object_states": str((episode_dir / "object_states.jsonl").relative_to(out_dir)),
                    "observation_files": [str(Path(path).relative_to(out_dir)) for path in frame_paths],
                    "observer_video": str((episode_dir / "observer_camera.mp4").relative_to(out_dir)),
                    "video_capture": {
                        "frame_count": len(frame_arrays),
                        "fps": args.fps,
                        "duration_sec": len(frame_arrays) / args.fps,
                        "simulator_step_stride": 1,
                        "video_endpoint_only": False,
                    },
                }
            ],
            "policy_execution": "not_run",
            "task_success_claim": "not_claimed",
        }
        write_json(out_dir / "dataset_manifest.json", manifest)

        last_observer = np.array(Image.open(frame_paths[-2])) if len(frame_paths) >= 2 else np.zeros((1, 1, 3), dtype=np.uint8)
        report.update(
            {
                "status": "pass_collect_dry_run",
                "episode_count": 1,
                "object_count": len(task.placement_objects),
                "dataset_manifest": str(out_dir / "dataset_manifest.json"),
                "scene_info": str(episode_dir / "scene_info.json"),
                "object_states": str(episode_dir / "object_states.jsonl"),
                "observation_file_count": len(frame_paths),
                "observer_video": str(episode_dir / "observer_camera.mp4"),
                "video_capture": {
                    "frame_count": len(frame_arrays),
                    "fps": args.fps,
                    "duration_sec": len(frame_arrays) / args.fps,
                    "simulator_step_stride": 1,
                    "video_endpoint_only": False,
                },
                "initial_poses": initial_poses,
                "final_poses": final_poses,
                "pose_delta_norm_m": pose_delta_norm_m,
                "pixel_stats_last_observer": pixel_stats(last_observer),
                "policy_execution": "not_run",
                "task_success_claim": "not_claimed",
            }
        )
        write_json(out_dir / "collect_report.json", report)
        print(f"PASS {out_dir / 'collect_report.json'}")
    except Exception as exc:
        report.update({"status": "fail_collect_dry_run", "error": repr(exc)})
        write_json(out_dir / "collect_report.json", report)
        print(f"FAIL {out_dir / 'collect_report.json'}")
        raise
    finally:
        try:
            task.close_env(clear_cache=True)
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
