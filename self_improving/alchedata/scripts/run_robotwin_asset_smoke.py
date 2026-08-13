#!/usr/bin/env python3
"""Run a RoboTwin/SAPIEN asset-load render smoke without initializing planners."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

import imageio.v2 as imageio
import numpy as np
from PIL import Image


TABLE_Z = 0.741


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def save_rgb(path: Path, rgb: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb.astype("uint8")).save(path)


def camera_pose(sapien, position: list[float], forward: list[float], left: list[float]):
    forward_v = np.array(forward, dtype=float)
    forward_v = forward_v / np.linalg.norm(forward_v)
    left_v = np.array(left, dtype=float)
    left_v = left_v / np.linalg.norm(left_v)
    up_v = np.cross(forward_v, left_v)
    up_v = up_v / np.linalg.norm(up_v)
    mat44 = np.eye(4)
    mat44[:3, :3] = np.stack([forward_v, left_v, up_v], axis=1)
    mat44[:3, 3] = np.array(position, dtype=float)
    return sapien.Pose(mat44)


def get_rgb(camera) -> np.ndarray:
    camera.take_picture()
    rgba = camera.get_picture("Color")
    return (rgba[:, :, :3] * 255).clip(0, 255).astype("uint8")


def pixel_stats(rgb: np.ndarray) -> dict[str, float]:
    return {
        "mean": float(np.mean(rgb)),
        "std": float(np.std(rgb)),
        "min": float(np.min(rgb)),
        "max": float(np.max(rgb)),
    }


def pose_record(actor) -> dict[str, list[float]]:
    pose = actor.get_pose()
    return {"p": pose.p.tolist(), "q": pose.q.tolist()}


def main() -> int:
    parser = argparse.ArgumentParser(description="RoboTwin/SAPIEN asset placement smoke.")
    parser.add_argument("--robotwin-root", required=True)
    parser.add_argument("--placement", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--settle-steps", type=int, default=120)
    parser.add_argument("--video-frames", type=int, default=24)
    parser.add_argument("--fps", type=int, default=12)
    args = parser.parse_args()

    robotwin_root = Path(args.robotwin_root).expanduser().resolve()
    placement_path = Path(args.placement).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    spec = read_json(placement_path)
    report: dict[str, Any] = {
        "schema_version": "alchedata.robotwin_asset_smoke.v0",
        "status": "started",
        "placement": str(placement_path),
        "robotwin_root": str(robotwin_root),
        "out_dir": str(out_dir),
        "seed": args.seed,
        "settle_steps": args.settle_steps,
        "video_frames": args.video_frames,
        "notes": [
            "This smoke uses official RoboTwin object assets and SAPIEN rendering.",
            "It intentionally bypasses Robot/Base_Task planner initialization.",
            "It validates asset load, physics scene insertion, render capture, and pose trace evidence.",
            "It is not a CuRobo planner or manipulation policy smoke.",
        ],
    }
    write_json(out_dir / "smoke_report.json", report)

    np.random.seed(args.seed)
    os.chdir(robotwin_root)
    sys.path.insert(0, str(robotwin_root))

    import sapien.core as sapien
    from envs.utils import create_actor, create_box, create_sapien_urdf_obj

    engine = sapien.Engine()
    renderer = sapien.SapienRenderer()
    engine.set_renderer(renderer)

    try:
        from sapien.render import set_global_config

        set_global_config(max_num_materials=50000, max_num_textures=50000)
        sapien.render.set_camera_shader_dir("rt")
        sapien.render.set_ray_tracing_samples_per_pixel(8)
        sapien.render.set_ray_tracing_path_depth(4)
        sapien.render.set_ray_tracing_denoiser("oidn")
    except Exception as exc:
        report.setdefault("warnings", []).append(f"ray_tracing_config_warning: {exc!r}")

    scene = engine.create_scene(sapien.SceneConfig())
    scene.set_timestep(1 / 250)
    scene.add_ground(0)
    scene.default_physical_material = scene.create_physical_material(0.5, 0.5, 0)
    scene.set_ambient_light([0.5, 0.5, 0.5])
    scene.add_directional_light([0, 0.5, -1], [0.5, 0.5, 0.5], shadow=True)
    scene.add_point_light([1, 0, 1.8], [1, 1, 1], shadow=True)
    scene.add_point_light([-1, 0, 1.8], [1, 1, 1], shadow=True)

    create_box(
        scene,
        pose=sapien.Pose([0, 0, TABLE_Z - 0.025], [1, 0, 0, 0]),
        half_size=[0.55, 0.38, 0.025],
        color=[0.62, 0.62, 0.60],
        is_static=True,
        name="table",
    )

    loaded: dict[str, Any] = {}
    try:
        for obj in spec["objects"]:
            pose_data = obj["pose"]
            xyz = list(pose_data["xyz"])
            if pose_data.get("z_policy") == "snap_to_tabletop_on_load":
                xyz[2] = TABLE_Z
            qpos = list(pose_data.get("qpos", [1, 0, 0, 0]))
            pose = sapien.Pose(xyz, qpos)
            metadata = obj.get("asset_metadata", {})
            defaults = metadata.get("placement_defaults", {})
            asset_type = metadata.get("asset_type", "rigid")
            if defaults.get("loader") == "sapien_urdf" or asset_type == "articulated":
                actor = create_sapien_urdf_obj(
                    scene,
                    pose=pose,
                    modelname=obj["asset_id"],
                    modelid=obj.get("model_id", 0),
                    fix_root_link=defaults.get(
                        "fix_root_link",
                        obj.get("physical", {}).get("is_static", False),
                    ),
                )
                if "articulation_qpos" in defaults:
                    actor.set_qpos(defaults["articulation_qpos"])
            else:
                actor = create_actor(
                    scene,
                    pose=pose,
                    modelname=obj["asset_id"],
                    scale=metadata.get("scale", (1, 1, 1)) or (1, 1, 1),
                    convex=True,
                    is_static=obj.get("physical", {}).get("is_static", False),
                    model_id=obj.get("model_id", 0),
                )
            if actor is None:
                raise RuntimeError(f"failed to load asset {obj['asset_id']}")
            actor.set_name(obj["id"])
            loaded[obj["id"]] = actor
    except Exception as exc:
        report.update({"status": "fail_asset_load", "error": repr(exc)})
        write_json(out_dir / "smoke_report.json", report)
        raise

    observer_camera = scene.add_camera("observer_camera", width=640, height=480, fovy=np.deg2rad(70), near=0.1, far=100)
    observer_camera.entity.set_pose(camera_pose(sapien, [0.0, 0.34, 1.35], [0, -1, -1.05], [1, 0, 0]))
    head_camera = scene.add_camera("head_camera", width=640, height=480, fovy=np.deg2rad(62), near=0.1, far=100)
    head_camera.entity.set_pose(camera_pose(sapien, [0.0, -0.18, 1.55], [0, 0.2, -1], [1, 0, 0]))

    initial_poses = {name: pose_record(actor) for name, actor in loaded.items()}
    frames: list[np.ndarray] = []
    total_steps = max(args.settle_steps, args.video_frames)
    for idx in range(total_steps):
        scene.step()
        scene.update_render()
        if idx < args.video_frames:
            frames.append(get_rgb(observer_camera))

    scene.update_render()
    observer_rgb = get_rgb(observer_camera)
    head_rgb = get_rgb(head_camera)
    save_rgb(out_dir / "observer_camera.png", observer_rgb)
    save_rgb(out_dir / "head_camera.png", head_rgb)
    if frames:
        imageio.mimsave(out_dir / "observer_camera.mp4", frames, fps=args.fps)

    final_poses = {name: pose_record(actor) for name, actor in loaded.items()}
    pose_delta_norm_m = {}
    for name in final_poses:
        a = np.array(initial_poses[name]["p"], dtype=float)
        b = np.array(final_poses[name]["p"], dtype=float)
        pose_delta_norm_m[name] = float(np.linalg.norm(b - a))

    report.update(
        {
            "status": "pass_asset_load_render",
            "object_count": len(loaded),
            "objects": list(loaded.keys()),
            "images": {
                "observer_camera": str(out_dir / "observer_camera.png"),
                "head_camera": str(out_dir / "head_camera.png"),
            },
            "video": str(out_dir / "observer_camera.mp4"),
            "initial_poses": initial_poses,
            "final_poses": final_poses,
            "pose_delta_norm_m": pose_delta_norm_m,
            "pixel_stats": {
                "observer_camera": pixel_stats(observer_rgb),
                "head_camera": pixel_stats(head_rgb),
            },
            "runtime": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "sapien_module": getattr(sapien, "__file__", None),
            },
        }
    )
    write_json(out_dir / "smoke_report.json", report)
    print(f"PASS {out_dir / 'smoke_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
