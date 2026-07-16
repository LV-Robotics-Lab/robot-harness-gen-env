#!/usr/bin/env python3
"""Run a ResolvedSceneSpec in RoboTwin/SAPIEN and emit physical evidence."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

import imageio.v2 as imageio
import numpy as np
import yaml
from PIL import Image

from scene_gen.envs.generated_scene import load_resolved_scene
from scene_gen.schema import ResolvedSceneSpec
from scene_gen.validator import validate_resolved_scene


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def save_rgb(path: Path, value: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(value.astype(np.uint8)).save(path)


def load_robotwin_args(robotwin_root: Path, task_config: str) -> dict[str, Any]:
    from envs._GLOBAL_CONFIGS import CONFIGS_PATH

    with (robotwin_root / "task_config" / f"{task_config}.yml").open("r", encoding="utf-8") as stream:
        args = yaml.load(stream.read(), Loader=yaml.FullLoader)
    embodiment_type = args.get("embodiment")
    with (Path(CONFIGS_PATH) / "_embodiment_config.yml").open("r", encoding="utf-8") as stream:
        embodiment_types = yaml.load(stream.read(), Loader=yaml.FullLoader)

    def embodiment_file(name: str) -> str:
        value = embodiment_types[name]["file_path"]
        if value is None:
            raise RuntimeError(f"missing embodiment files for {name}")
        return value

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
        raise RuntimeError("unexpected embodiment config shape")

    def embodiment_config(robot_file: str) -> dict[str, Any]:
        with (robotwin_root / robot_file / "config.yml").open("r", encoding="utf-8") as stream:
            return yaml.load(stream.read(), Loader=yaml.FullLoader)

    args["left_embodiment_config"] = embodiment_config(args["left_robot_file"])
    args["right_embodiment_config"] = embodiment_config(args["right_robot_file"])
    args["task_config"] = task_config
    args["task_name"] = "generated_scene_runtime"
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


def quaternion_angle_deg(first: list[float], second: list[float]) -> float:
    dot = abs(float(np.dot(np.asarray(first, dtype=float), np.asarray(second, dtype=float))))
    return math.degrees(2.0 * math.acos(min(1.0, max(-1.0, dot))))


def entity_id(actor: Any) -> int | None:
    entity = getattr(actor, "actor", actor)
    for name in ("per_scene_id", "id"):
        value = getattr(entity, name, None)
        if isinstance(value, (int, np.integer)):
            return int(value)
    getter = getattr(entity, "get_per_scene_id", None)
    if callable(getter):
        return int(getter())
    return None


def contact_body_name(body: Any) -> str:
    for candidate in (body, getattr(body, "entity", None)):
        if candidate is None:
            continue
        getter = getattr(candidate, "get_name", None)
        if callable(getter):
            value = getter()
            if value:
                return str(value)
        value = getattr(candidate, "name", None)
        if value:
            return str(value)
    return type(body).__name__


def contact_pair(contact: Any) -> tuple[str, str]:
    bodies = getattr(contact, "bodies", None)
    if bodies is None:
        bodies = [getattr(contact, "body0", None), getattr(contact, "body1", None)]
    names = [contact_body_name(body) for body in list(bodies or [])[:2]]
    while len(names) < 2:
        names.append("unknown")
    return names[0], names[1]


def contact_penetration_count(contact: Any, threshold_m: float = -0.002) -> int:
    count = 0
    for point in getattr(contact, "points", []) or []:
        separation = getattr(point, "separation", None)
        if isinstance(separation, (int, float)) and separation < threshold_m:
            count += 1
    return count


def summarize_contacts(contacts: list[Any], generated_names: set[str]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    penetration_by_object = {name: 0 for name in generated_names}
    support_by_object = {name: False for name in generated_names}
    robot_collision_pairs: set[tuple[str, str]] = set()
    for contact in contacts:
        first, second = contact_pair(contact)
        if first not in generated_names and second not in generated_names:
            continue
        penetrations = contact_penetration_count(contact)
        records.append({"bodies": [first, second], "penetration_points": penetrations})
        for generated, other in ((first, second), (second, first)):
            if generated not in generated_names:
                continue
            other_lower = other.lower()
            if other in generated_names:
                penetration_by_object[generated] += penetrations
            elif any(token in other_lower for token in ("table", "ground", "wall")):
                support_by_object[generated] = True
            else:
                robot_collision_pairs.add((generated, other))
    return {
        "records": records,
        "penetration_by_object": penetration_by_object,
        "support_by_object": support_by_object,
        "robot_collision_pairs": [list(pair) for pair in sorted(robot_collision_pairs)],
        "robot_collision_count": len(robot_collision_pairs),
    }


def head_camera_arrays(task: Any) -> tuple[np.ndarray, np.ndarray]:
    task._update_render()
    task.cameras.update_picture()
    rgb = task.cameras.get_rgb()["head_camera"]["rgb"]
    for camera, name in zip(task.cameras.static_camera_list, task.cameras.static_camera_name):
        if name == "head_camera":
            segmentation = camera.get_picture("Segmentation")
            return rgb, segmentation[..., 1].astype(np.int64)
    raise RuntimeError("head camera is not available")


def segmentation_preview(labels: np.ndarray) -> np.ndarray:
    red = (labels * 53) % 255
    green = (labels * 97) % 255
    blue = (labels * 193) % 255
    return np.stack([red, green, blue], axis=-1).astype(np.uint8)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--robotwin-root", required=True)
    parser.add_argument("--resolved-scene", required=True)
    parser.add_argument("--asset-catalog")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--task-config", default="demo_clean")
    parser.add_argument("--settle-steps", type=int, default=180)
    parser.add_argument("--precheck-steps", type=int, default=120)
    parser.add_argument("--video-frames", type=int, default=120)
    parser.add_argument("--fps", type=int, default=12)
    parser.add_argument("--min-visible-pixels", type=int, default=64)
    args = parser.parse_args()

    robotwin_root = Path(args.robotwin_root).expanduser().resolve()
    resolved_path = Path(args.resolved_scene).expanduser().resolve()
    catalog_path = Path(args.asset_catalog).expanduser().resolve() if args.asset_catalog else None
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    resolved = ResolvedSceneSpec.model_validate_json(resolved_path.read_text(encoding="utf-8"))

    os.chdir(robotwin_root)
    sys.path.insert(0, str(robotwin_root))
    from envs._base_task import Base_Task

    class GeneratedSceneRuntime(Base_Task):
        def __init__(self, scene: ResolvedSceneSpec):
            super().__init__()
            self.resolved_scene = scene
            self.generated_objects: dict[str, Any] = {}

        def setup_demo(self, **kwargs: Any) -> None:
            super()._init_task_env_(**kwargs)

        def load_actors(self) -> None:
            self.generated_objects = load_resolved_scene(self, self.resolved_scene)

        def check_stable(self):
            for _ in range(max(1, args.precheck_steps)):
                self.scene.step()
            return True, []

        def play_once(self):
            raise NotImplementedError("environment generation does not define a task policy")

        def check_success(self):
            raise NotImplementedError("environment generation does not define task success")

    task = GeneratedSceneRuntime(resolved)
    report: dict[str, Any] = {
        "schema_version": "robotwin.scene_runtime_evidence.v1",
        "scene_id": resolved.scene_id,
        "resolved_scene_sha256": resolved.digest(),
        "seed": resolved.seed,
        "status": "started",
    }
    try:
        runtime_args = load_robotwin_args(robotwin_root, args.task_config)
        runtime_args["save_path"] = str(out_dir)
        task.setup_demo(now_ep_num=0, seed=resolved.seed, **runtime_args)
        generated_names = set(task.generated_objects)
        initial_contacts = summarize_contacts(list(task.scene.get_contacts()), generated_names)
        initial = {
            name: {
                "position_m": actor.get_pose().p.tolist(),
                "orientation_wxyz": actor.get_pose().q.tolist(),
            }
            for name, actor in task.generated_objects.items()
        }
        frames: list[np.ndarray] = []
        prior_window: dict[str, dict[str, list[float]]] | None = None
        total_steps = max(args.settle_steps, args.video_frames)
        for index in range(total_steps):
            task.scene.step()
            if index == max(0, total_steps - 30):
                prior_window = {
                    name: {
                        "position_m": actor.get_pose().p.tolist(),
                        "orientation_wxyz": actor.get_pose().q.tolist(),
                    }
                    for name, actor in task.generated_objects.items()
                }
            if index < args.video_frames:
                task.scene.update_render()
                frames.append(task.cameras.get_observer_rgb())
        final = {
            name: {
                "position_m": actor.get_pose().p.tolist(),
                "orientation_wxyz": actor.get_pose().q.tolist(),
            }
            for name, actor in task.generated_objects.items()
        }
        head_rgb, actor_labels = head_camera_arrays(task)
        save_rgb(out_dir / "preview_head.png", head_rgb)
        save_rgb(out_dir / "preview_segmentation.png", segmentation_preview(actor_labels))
        if frames:
            imageio.mimsave(out_dir / "observer_runtime.mp4", frames, fps=args.fps)

        final_contacts = summarize_contacts(list(task.scene.get_contacts()), generated_names)

        objects: dict[str, Any] = {}
        by_id = {item.object_id: item for item in resolved.objects}
        for name, actor in task.generated_objects.items():
            before = initial[name]
            after = final[name]
            late = (prior_window or initial)[name]
            identifier = entity_id(actor)
            visible_pixels = int(np.count_nonzero(actor_labels == identifier)) if identifier is not None else 0
            late_translation = float(
                np.linalg.norm(np.asarray(after["position_m"]) - np.asarray(late["position_m"]))
            )
            late_rotation = quaternion_angle_deg(after["orientation_wxyz"], late["orientation_wxyz"])
            dropped = after["position_m"][2] < resolved.workspace.table_height_m - 0.03
            raw_support_contact = final_contacts["support_by_object"][name]
            support_mode = (
                "fixed_static_pose"
                if by_id[name].is_static
                else "table_contact"
                if raw_support_contact
                else "none"
            )
            objects[name] = {
                "asset_id": by_id[name].asset_id,
                "entity_id": identifier,
                "initial_pose": before,
                "final_pose": after,
                "translation_drift_m": float(
                    np.linalg.norm(np.asarray(after["position_m"]) - np.asarray(before["position_m"]))
                ),
                "rotation_drift_deg": quaternion_angle_deg(after["orientation_wxyz"], before["orientation_wxyz"]),
                "late_window_translation_m": late_translation,
                "late_window_rotation_deg": late_rotation,
                "still_moving": late_translation > 0.001 or late_rotation > 0.5,
                "visible_pixels": visible_pixels,
                "penetration_count": final_contacts["penetration_by_object"][name],
                "support_contact": raw_support_contact,
                "support_mode": support_mode,
                "dropped": dropped,
            }
        report.update(
            {
                "status": "pass",
                "robot_initial_collision_count": initial_contacts["robot_collision_count"],
                "robot_initial_collision_pairs": initial_contacts["robot_collision_pairs"],
                "robot_final_collision_count": final_contacts["robot_collision_count"],
                "robot_final_collision_pairs": final_contacts["robot_collision_pairs"],
                "objects": objects,
                "initial_contact_records": initial_contacts["records"],
                "final_contact_records": final_contacts["records"],
                "images": {
                    "head": str(out_dir / "preview_head.png"),
                    "segmentation": str(out_dir / "preview_segmentation.png"),
                },
                "video": str(out_dir / "observer_runtime.mp4") if frames else None,
                "video_frame_count": len(frames),
                "fps": args.fps,
            }
        )
        write_json(out_dir / "runtime_evidence.json", report)
        catalog = None
        if catalog_path:
            from scene_gen.catalog import load_catalog

            catalog = load_catalog(catalog_path)
        validation = validate_resolved_scene(
            resolved,
            catalog=catalog,
            runtime_evidence=report,
            require_runtime=True,
            min_visible_pixels=args.min_visible_pixels,
        )
        write_json(out_dir / "runtime_validation_report.json", validation)
        print(
            f"{validation['status'].upper()} scene={resolved.scene_id} "
            f"fail={validation['fail_count']} video_frames={len(frames)}"
        )
        return 0 if validation["status"] == "pass" else 2
    except Exception as error:
        report.update({"status": "fail", "error": repr(error)})
        write_json(out_dir / "runtime_evidence.json", report)
        print(f"FAIL {out_dir / 'runtime_evidence.json'}")
        raise
    finally:
        try:
            task.close_env(clear_cache=True)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
