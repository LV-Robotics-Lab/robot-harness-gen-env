#!/usr/bin/env python3
"""Run a native Isaac Sim physics/render baseline and capture proof artifacts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import shutil
import socket
import subprocess
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _isaac_gui import capture_active_viewport, encode_png_sequence_to_mp4


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _command_output(command: list[str]) -> str:
    try:
        return subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"{type(exc).__name__}: {exc}"


def _image_stats(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"ready": False, "reason": "missing_file"}
    from PIL import Image, ImageStat

    image = Image.open(path).convert("RGB")
    luma = ImageStat.Stat(image.convert("L"))
    return {
        "ready": True,
        "width": image.width,
        "height": image.height,
        "size_bytes": path.stat().st_size,
        "mean_luma": round(float(luma.mean[0]), 3),
        "std_luma": round(float(luma.stddev[0]), 3),
    }


def _file_evidence(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    if not resolved.is_file():
        return {"ready": False, "path": str(resolved), "reason": "missing_file"}
    return {
        "ready": True,
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
    }


def _frame_sequence_stats(paths: list[Path]) -> dict[str, Any]:
    if not paths:
        return {"ready": False, "reason": "no_frames", "frame_count": 0}

    from PIL import Image, ImageChops, ImageStat

    hashes: list[str] = []
    adjacent_deltas: list[float] = []
    previous = None
    for path in paths:
        hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
        current = Image.open(path).convert("RGB")
        if previous is not None:
            difference = ImageChops.difference(previous, current)
            channel_means = ImageStat.Stat(difference).mean
            adjacent_deltas.append(sum(float(value) for value in channel_means) / len(channel_means))
        previous = current

    return {
        "ready": True,
        "frame_count": len(paths),
        "unique_sha256_count": len(set(hashes)),
        "first_sha256": hashes[0],
        "last_sha256": hashes[-1],
        "adjacent_mean_abs_rgb_delta": {
            "min": round(min(adjacent_deltas), 4) if adjacent_deltas else 0.0,
            "mean": round(sum(adjacent_deltas) / len(adjacent_deltas), 4) if adjacent_deltas else 0.0,
            "max": round(max(adjacent_deltas), 4) if adjacent_deltas else 0.0,
        },
    }


def _pose_sequence_stats(records: list[dict[str, Any]], *, movement_threshold: float = 0.001) -> dict[str, Any]:
    positions = [record["cube_position"] for record in records]
    if not positions:
        return {"ready": False, "reason": "no_poses", "pose_count": 0}

    adjacent_distances = [
        math.dist(previous, current) for previous, current in zip(positions, positions[1:], strict=False)
    ]
    return {
        "ready": True,
        "pose_count": len(positions),
        "unique_position_count_at_1e_4m": len(
            {tuple(round(float(value), 4) for value in position) for position in positions}
        ),
        "movement_threshold_m": movement_threshold,
        "movement_transition_count": sum(distance > movement_threshold for distance in adjacent_distances),
        "adjacent_distance_m": {
            "min": round(min(adjacent_distances), 6) if adjacent_distances else 0.0,
            "mean": round(sum(adjacent_distances) / len(adjacent_distances), 6)
            if adjacent_distances
            else 0.0,
            "max": round(max(adjacent_distances), 6) if adjacent_distances else 0.0,
        },
        "first_to_last_distance_m": round(math.dist(positions[0], positions[-1]), 6),
    }


def _torch_info() -> dict[str, Any]:
    import torch

    available = bool(torch.cuda.is_available())
    result: dict[str, Any] = {
        "version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "cuda_available": available,
        "device_count": int(torch.cuda.device_count()) if available else 0,
        "device_name": torch.cuda.get_device_name(0) if available else None,
        "compute_passed": False,
    }
    if not available:
        return result

    try:
        capability = tuple(int(value) for value in torch.cuda.get_device_capability(0))
        architecture = f"sm_{capability[0]}{capability[1]}"
        architectures = list(torch.cuda.get_arch_list())
        torch.cuda.manual_seed_all(7)
        left = torch.randn((1024, 1024), device="cuda")
        right = torch.randn((1024, 1024), device="cuda")
        torch.cuda.synchronize()
        started = time.perf_counter()
        product = left @ right
        torch.cuda.synchronize()
        finite = bool(torch.isfinite(product).all().item())
        result.update(
            {
                "device_capability": list(capability),
                "architecture": architecture,
                "architecture_list": architectures,
                "architecture_supported": architecture in architectures,
                "matmul_shape": list(product.shape),
                "matmul_finite": finite,
                "matmul_elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
                "matmul_checksum": round(float(product[0, :32].sum().item()), 6),
                "compute_passed": finite and architecture in architectures,
            }
        )
    except Exception as exc:
        result["compute_error"] = f"{type(exc).__name__}: {exc}"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshot", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--video", type=Path)
    parser.add_argument("--frame-dir", type=Path)
    parser.add_argument("--video-fps", type=float, default=12.0)
    parser.add_argument("--video-frames", type=int, default=32)
    parser.add_argument("--video-horizon-steps", type=int, default=48)
    parser.add_argument("--steps", type=int, default=180)
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=540)
    args = parser.parse_args()

    os.environ.setdefault("OMNI_KIT_ACCEPT_EULA", "YES")
    nvidia_icd = Path("/usr/share/vulkan/icd.d/nvidia_icd.json")
    if nvidia_icd.is_file():
        os.environ.setdefault("VK_ICD_FILENAMES", str(nvidia_icd))
    started = time.time()
    started_at = datetime.now(timezone.utc).isoformat()
    torch_info = _torch_info()
    report: dict[str, Any] = {
        "schema": "agenticsim.isaac_runtime_visual_smoke.v1",
        "started_at": started_at,
        "status": "failed",
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "isaacsim_version": importlib.metadata.version("isaacsim"),
        "torch": torch_info,
        "nvidia_smi": _command_output(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"]
        ),
        "steps_requested": max(int(args.steps), 1),
        "steps_completed": 0,
        "screenshot": str(args.screenshot.resolve()),
        "report": str(args.report.resolve()),
        "video": str(args.video.resolve()) if args.video else None,
    }

    from isaacsim import SimulationApp

    app = SimulationApp(
        {
            "headless": True,
            "hide_ui": True,
            "renderer": "RaytracedLighting",
            "width": max(int(args.width), 64),
            "height": max(int(args.height), 64),
            "fast_shutdown": True,
            "active_gpu": 0,
            "physics_gpu": 0,
            "multi_gpu": False,
            "max_gpu_count": 1,
        }
    )
    try:
        import numpy as np
        import omni.timeline
        from isaacsim.core.api import World
        from isaacsim.core.api.objects import DynamicCuboid
        from isaacsim.core.utils.viewports import set_camera_view
        from pxr import Gf, UsdLux

        world = World(stage_units_in_meters=1.0)
        world.scene.add_default_ground_plane()
        cube = world.scene.add(
            DynamicCuboid(
                prim_path="/World/FallingCube",
                name="falling_cube",
                position=np.array([0.0, 0.0, 1.5]),
                scale=np.array([0.35, 0.35, 0.35]),
                color=np.array([0.05, 0.55, 0.95]),
                mass=1.0,
            )
        )
        stage = world.stage
        dome = UsdLux.DomeLight.Define(stage, "/World/SmokeDomeLight")
        dome.CreateIntensityAttr(500.0)
        dome.CreateColorAttr(Gf.Vec3f(1.0, 0.96, 0.9))
        key = UsdLux.DistantLight.Define(stage, "/World/SmokeKeyLight")
        key.CreateIntensityAttr(2200.0)
        key.CreateAngleAttr(0.5)

        set_camera_view([2.6, 2.4, 1.8], [0.0, 0.0, 0.35], camera_prim_path="/OmniverseKit_Persp")
        world.reset()
        initial_position = [float(value) for value in cube.get_world_pose()[0]]
        cube.set_linear_velocity(np.array([0.45, -0.08, 0.0], dtype=np.float32))
        cube.set_angular_velocity(np.array([0.4, 0.8, 1.2], dtype=np.float32))
        timeline = omni.timeline.get_timeline_interface()

        frame_paths: list[Path] = []
        frame_records: list[dict[str, Any]] = []
        sample_steps: set[int] = set()
        frame_dir: Path | None = None
        if args.video:
            desired_frames = max(int(args.video_frames), 2)
            horizon = min(report["steps_requested"], max(int(args.video_horizon_steps), 1))
            sample_steps = {
                round(index * horizon / (desired_frames - 1)) for index in range(desired_frames)
            }
            frame_dir = args.frame_dir or args.video.with_name(f"{args.video.stem}_frames")
            if frame_dir.exists():
                shutil.rmtree(frame_dir)
            frame_dir.mkdir(parents=True, exist_ok=True)

        def capture_video_frame(step: int, *, warmup_frames: int, completion_frames: int) -> None:
            if frame_dir is None:
                return
            frame_path = frame_dir / f"frame_{len(frame_paths):05d}.png"
            position = [float(value) for value in cube.get_world_pose()[0]]
            was_playing = timeline.is_playing()
            timeline.pause()
            try:
                app.run_coroutine(
                    capture_active_viewport(
                        frame_path.resolve(),
                        warmup_frames=warmup_frames,
                        completion_frames=completion_frames,
                        timeout_s=120,
                        prefer_new_viewport=True,
                        width=args.width,
                        height=args.height,
                        camera_eye=(2.6, 2.4, 1.8),
                        camera_lookat=(0.0, 0.0, 0.35),
                    )
                )
            finally:
                if was_playing:
                    timeline.play()
            frame_paths.append(frame_path)
            frame_records.append(
                {
                    "frame_index": len(frame_paths) - 1,
                    "simulation_step": step,
                    "cube_position": position,
                }
            )

        if 0 in sample_steps:
            capture_video_frame(0, warmup_frames=8, completion_frames=6)
        for index in range(report["steps_requested"]):
            world.step(render=True)
            report["steps_completed"] = index + 1
            if index + 1 in sample_steps:
                capture_video_frame(index + 1, warmup_frames=0, completion_frames=2)
        final_position = [float(value) for value in cube.get_world_pose()[0]]

        args.screenshot.parent.mkdir(parents=True, exist_ok=True)
        timeline.pause()
        try:
            app.run_coroutine(
                capture_active_viewport(
                    args.screenshot.resolve(),
                    warmup_frames=12,
                    timeout_s=120,
                    prefer_new_viewport=True,
                    width=args.width,
                    height=args.height,
                    camera_eye=(2.6, 2.4, 1.8),
                    camera_lookat=(0.0, 0.0, 0.35),
                )
            )
        finally:
            timeline.play()
        image = _image_stats(args.screenshot)
        physics_passed = final_position[2] < initial_position[2] - 0.5 and 0.0 < final_position[2] < 0.6
        render_passed = bool(image.get("ready")) and float(image.get("std_luma") or 0.0) > 2.0
        torch_passed = bool(torch_info.get("compute_passed"))
        video_passed = True
        video_evidence = None
        if args.video:
            sequence = _frame_sequence_stats(frame_paths)
            poses = _pose_sequence_stats(frame_records)
            encode = encode_png_sequence_to_mp4(frame_dir, args.video, fps=max(float(args.video_fps), 1.0))
            minimum_frames = min(24, len(sample_steps))
            minimum_unique = min(12, minimum_frames)
            minimum_pose_transitions = min(12, max(minimum_frames - 1, 0))
            video_passed = bool(
                encode.get("ready")
                and sequence.get("frame_count", 0) >= minimum_frames
                and sequence.get("unique_sha256_count", 0) >= minimum_unique
                and sequence.get("adjacent_mean_abs_rgb_delta", {}).get("max", 0.0) > 0.1
                and poses.get("movement_transition_count", 0) >= minimum_pose_transitions
            )
            video_evidence = {
                "passed": video_passed,
                "minimum_frame_count": minimum_frames,
                "minimum_unique_sha256_count": minimum_unique,
                "minimum_max_adjacent_mean_abs_rgb_delta": 0.1,
                "minimum_pose_movement_transition_count": minimum_pose_transitions,
                "sample_steps": sorted(sample_steps),
                "captures": frame_records,
                "sequence": sequence,
                "poses": poses,
                "encode": encode,
            }
        report.update(
            {
                "initial_cube_position": initial_position,
                "final_cube_position": final_position,
                "physics_passed": physics_passed,
                "render_passed": render_passed,
                "torch_passed": torch_passed,
                "video_passed": video_passed,
                "video_evidence": video_evidence,
                "image": image,
                "status": "passed"
                if physics_passed and render_passed and video_passed and torch_passed
                else "failed",
            }
        )
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
    finally:
        report["elapsed_s"] = round(time.time() - started, 3)
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["artifacts"] = {
            "screenshot": _file_evidence(args.screenshot),
            "video": _file_evidence(args.video) if args.video else None,
        }
        _write_json(args.report, report)
        app.close()

    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(args.report),
                "screenshot": str(args.screenshot),
                "video": str(args.video) if args.video else None,
            }
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
