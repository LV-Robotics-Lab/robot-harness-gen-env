#!/usr/bin/env python3
"""Load a tabletop placement spec in RoboTwin and save render evidence."""

from __future__ import annotations

import argparse
import importlib.util
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
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def save_rgb(path: Path, rgb: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb.astype("uint8")).save(path)


def load_scene_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import generated scene module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "load_scene"):
        raise RuntimeError(f"Generated scene module has no load_scene(task, placement_spec=None): {path}")
    return module


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
    args["task_name"] = "tabletop_placement_smoke"
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--robotwin-root", default=str(Path.home() / "RoboTwin"))
    parser.add_argument("--placement", required=True)
    parser.add_argument("--scene-module", help="Optional generated scene module with load_scene(task, placement_spec=None).")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--task-config", default="demo_smoke")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--settle-steps", type=int, default=240)
    parser.add_argument("--video-frames", type=int, default=60)
    parser.add_argument("--fps", type=int, default=15)
    args = parser.parse_args()

    robotwin_root = Path(args.robotwin_root).expanduser().resolve()
    placement_path = Path(args.placement).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    spec = read_json(placement_path)
    scene_module_path = Path(args.scene_module).expanduser().resolve() if args.scene_module else None

    os.chdir(robotwin_root)
    sys.path.insert(0, str(robotwin_root))

    import sapien.core as sapien
    from envs._base_task import Base_Task
    from envs.utils import create_actor, create_sapien_urdf_obj
    scene_module = load_scene_module(scene_module_path) if scene_module_path else None

    class TabletopPlacementSmoke(Base_Task):
        def __init__(self, placement_spec: dict[str, Any], generated_scene_module=None):
            super().__init__()
            self.placement_spec = placement_spec
            self.generated_scene_module = generated_scene_module
            self.placement_objects = {}

        def setup_demo(self, **kwargs: Any) -> None:
            super()._init_task_env_(**kwargs)

        def load_actors(self) -> None:
            if self.generated_scene_module is not None:
                self.placement_objects = self.generated_scene_module.load_scene(self, self.placement_spec)
                return

            table_z = 0.741 + self.table_z_bias
            for obj in self.placement_spec["objects"]:
                pose_data = obj["pose"]
                xyz = list(pose_data["xyz"])
                if pose_data.get("z_policy") == "snap_to_tabletop_on_load":
                    xyz[2] = table_z

                qpos = list(pose_data.get("qpos", [1, 0, 0, 0]))
                # RoboTwin's plate task uses this orientation for 003_plate.
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
            return {}

        def check_success(self):
            return True

    rt_args = load_robotwin_args(robotwin_root, args.task_config)
    rt_args["save_path"] = str(out_dir)

    task = TabletopPlacementSmoke(spec, scene_module)
    report: dict[str, Any] = {
        "placement": str(placement_path),
        "out_dir": str(out_dir),
        "stage": "robotwin_smoke",
        "scene_module": str(scene_module_path) if scene_module_path else None,
        "seed": args.seed,
        "status": "started",
    }

    try:
        task.setup_demo(now_ep_num=0, seed=args.seed, **rt_args)

        initial_poses = {
            name: {
                "p": actor.get_pose().p.tolist(),
                "q": actor.get_pose().q.tolist(),
            }
            for name, actor in task.placement_objects.items()
        }

        frames = []
        for idx in range(max(args.settle_steps, args.video_frames)):
            task.scene.step()
            task.scene.update_render()
            if idx < args.video_frames:
                frame = task.cameras.get_observer_rgb()
                frames.append(frame)

        final_poses = {
            name: {
                "p": actor.get_pose().p.tolist(),
                "q": actor.get_pose().q.tolist(),
            }
            for name, actor in task.placement_objects.items()
        }

        task._update_render()
        task.cameras.update_picture()
        head_rgb = task.cameras.get_rgb()["head_camera"]["rgb"]
        observer_rgb = task.cameras.get_observer_rgb()

        save_rgb(out_dir / "head_camera.png", head_rgb)
        save_rgb(out_dir / "observer_camera.png", observer_rgb)
        if frames:
            imageio.mimsave(out_dir / "observer_camera.mp4", frames, fps=args.fps)

        max_pose_delta = {}
        for name in final_poses:
            a = np.array(initial_poses[name]["p"], dtype=float)
            b = np.array(final_poses[name]["p"], dtype=float)
            max_pose_delta[name] = float(np.linalg.norm(b - a))

        report.update(
            {
                "status": "pass",
                "images": {
                    "head_camera": str(out_dir / "head_camera.png"),
                    "observer_camera": str(out_dir / "observer_camera.png"),
                },
                "video": str(out_dir / "observer_camera.mp4"),
                "initial_poses": initial_poses,
                "final_poses": final_poses,
                "pose_delta_norm_m": max_pose_delta,
                "notes": [
                    "Objects were loaded from RoboTwin assets through create_actor.",
                    "If scene_module is set, objects were loaded through generated load_scene(task).",
                    "Object-specific scale and pose metadata are read from the placement spec when provided.",
                    "This smoke confirms load/render evidence; human or VLM visual review should inspect the saved image/video.",
                ],
            }
        )
        write_json(out_dir / "smoke_report.json", report)
        print(f"PASS {out_dir / 'smoke_report.json'}")
    except Exception as exc:
        report.update({"status": "fail", "error": repr(exc)})
        write_json(out_dir / "smoke_report.json", report)
        print(f"FAIL {out_dir / 'smoke_report.json'}")
        raise
    finally:
        try:
            task.close_env(clear_cache=True)
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
