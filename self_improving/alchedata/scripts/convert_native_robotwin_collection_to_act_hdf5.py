#!/usr/bin/env python3
"""Convert RoboTwin-native synchronized rollouts into ACT training HDF5 files."""

from __future__ import annotations

import argparse
import io
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import h5py
import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def decode_rgb(encoded: Any, size: tuple[int, int]) -> np.ndarray:
    raw = bytes(encoded).rstrip(b"\0")
    with Image.open(io.BytesIO(raw)) as image:
        decoded = np.asarray(image.convert("RGB").resize(size), dtype=np.uint8)
    # RoboTwin passes RGB arrays directly to cv2.imencode, which interprets them as BGR.
    return np.ascontiguousarray(decoded[..., ::-1])


def source_episode_passed(episode: dict[str, Any]) -> bool:
    return (
        str(episode.get("status", "")).startswith("pass_")
        and episode.get("check_success") is True
        and episode.get("native_synchronized_data", {}).get("status") == "pass_native_synchronized_recording"
    )


def convert_episode(
    episode: dict[str, Any],
    output_path: Path,
    camera_size: tuple[int, int],
    source_collection_report: Path,
) -> dict[str, Any]:
    if not source_episode_passed(episode):
        return {
            "status": "skipped_source_episode_not_passed",
            "episode_index": episode.get("episode_index"),
            "seed": episode.get("seed"),
            "source_status": episode.get("status"),
            "native_status": episode.get("native_synchronized_data", {}).get("status"),
        }

    native = episode["native_synchronized_data"]
    source_path = resolve_path(native["hdf5"])
    if not source_path.exists():
        return {
            "status": "blocked_missing_native_hdf5",
            "episode_index": episode.get("episode_index"),
            "seed": episode.get("seed"),
            "source": str(source_path),
        }

    with h5py.File(source_path, "r") as source:
        raw_qpos = np.asarray(source["joint_action/vector"], dtype=np.float32)
        encoded_frames = source["observation/head_camera/rgb"][:]

    if raw_qpos.ndim != 2 or raw_qpos.shape[1] != 14:
        return {
            "status": "blocked_invalid_native_qpos_shape",
            "episode_index": episode.get("episode_index"),
            "seed": episode.get("seed"),
            "source": str(source_path),
            "shape": list(raw_qpos.shape),
        }
    if len(raw_qpos) != len(encoded_frames) or len(raw_qpos) < 2:
        return {
            "status": "blocked_native_alignment_mismatch",
            "episode_index": episode.get("episode_index"),
            "seed": episode.get("seed"),
            "source": str(source_path),
            "qpos_frames": len(raw_qpos),
            "camera_frames": len(encoded_frames),
        }

    # Match RoboTwin ACT's process_data convention: observation t predicts state t+1.
    qpos = raw_qpos[:-1]
    action = raw_qpos[1:]
    frames = np.stack([decode_rgb(frame, camera_size) for frame in encoded_frames[:-1]])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(output_path, "w") as root:
        root.attrs["sim"] = True
        root.attrs["source_native_hdf5"] = str(source_path)
        root.attrs["source_collection_report"] = str(source_collection_report)
        root.attrs["source_seed"] = int(episode["seed"])
        root.attrs["source_placement_id"] = str(episode.get("placement_id", "fixed"))
        root.attrs["source_placement_split"] = str(episode.get("placement_split", "fixed"))
        root.attrs["source_pose_signature"] = str(episode.get("pose_signature") or "")
        root.attrs["source_placement_path"] = str(episode.get("placement") or "")
        root.attrs["temporal_alignment"] = "qpos[t], head_camera_rgb[t] -> action=qpos[t+1]"
        obs = root.create_group("observations")
        obs.create_dataset("qpos", data=qpos)
        obs.create_dataset("qvel", data=np.zeros_like(qpos))
        image_group = obs.create_group("images")
        image_group.create_dataset(
            "cam_high",
            data=frames,
            dtype=np.uint8,
            compression="gzip",
            compression_opts=4,
            shuffle=True,
            chunks=(min(len(frames), 32), camera_size[1], camera_size[0], 3),
        )
        root.create_dataset("action", data=action)

    return {
        "status": "pass_native_act_hdf5_episode",
        "episode_index": episode.get("episode_index"),
        "seed": episode.get("seed"),
        "placement_id": episode.get("placement_id", "fixed"),
        "placement_split": episode.get("placement_split", "fixed"),
        "pose_signature": episode.get("pose_signature"),
        "placement": episode.get("placement"),
        "source": str(source_path),
        "source_collection_report": str(source_collection_report),
        "output": str(output_path),
        "native_frame_count": int(len(raw_qpos)),
        "act_timestep_count": int(len(qpos)),
        "action_dim": int(action.shape[1]),
        "camera_names": ["cam_high"],
        "camera_source": "observation/head_camera/rgb",
        "native_jpeg_color_repair": "swap decoded red/blue channels to invert cv2.imencode-on-RGB",
        "camera_size": {"width": camera_size[0], "height": camera_size[1]},
        "temporal_alignment": "qpos[t], head_camera_rgb[t] -> action=qpos[t+1]",
        "mean_next_action_delta": float(np.linalg.norm(action - qpos, axis=1).mean()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert native synchronized RoboTwin rollout data to ACT HDF5.")
    parser.add_argument("--collection", action="append", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--camera-width", type=int, default=96)
    parser.add_argument("--camera-height", type=int, default=72)
    args = parser.parse_args()

    collection_paths = [Path(value).expanduser().resolve() for value in args.collection]
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    collections = [(path, read_json(path)) for path in collection_paths]
    camera_size = (args.camera_width, args.camera_height)

    report: dict[str, Any] = {
        "schema_version": "alchedata.native_robotwin_act_adapter.v0",
        "status": "started",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_collection": str(collection_paths[0]) if len(collection_paths) == 1 else None,
        "source_collections": [str(path) for path in collection_paths],
        "out_dir": str(out_dir),
        "episodes": [],
        "claim_boundary": (
            "This adapter uses RoboTwin-native equal-length per-frame head RGB and 14-D joint state records. "
            "It proves synchronized ACT-format demonstrations, not learned-policy task success."
        ),
        "native_jpeg_color_repair": {
            "source_behavior": "RoboTwin pkl2hdf5.py passes RGB camera arrays directly to cv2.imencode, which assumes BGR.",
            "adapter_behavior": "Decode the JPEG as RGB, then swap red and blue channels back to runtime head-camera RGB order.",
        },
    }
    write_json(out_dir / "conversion_report.json", report)

    next_episode = 0
    for collection_path, collection in collections:
        for episode in collection.get("episodes", []):
            converted = convert_episode(
                episode,
                out_dir / "data" / f"episode_{next_episode}.hdf5",
                camera_size,
                collection_path,
            )
            converted.setdefault("source_collection_report", str(collection_path))
            report["episodes"].append(converted)
            if converted["status"] == "pass_native_act_hdf5_episode":
                next_episode += 1

    pass_count = sum(1 for episode in report["episodes"] if episode["status"] == "pass_native_act_hdf5_episode")
    skip_count = sum(1 for episode in report["episodes"] if episode["status"].startswith("skipped_"))
    fail_count = len(report["episodes"]) - pass_count - skip_count
    task_name = f"sim-native-generated_selection2env-demo_clean-{pass_count}"
    task_config = {
        "dataset_dir": str(out_dir / "data"),
        "num_episodes": pass_count,
        "episode_len": max((episode.get("act_timestep_count", 0) for episode in report["episodes"]), default=0),
        "camera_names": ["cam_high"],
    }
    report.update(
        {
            "status": "pass_native_act_hdf5_adapter" if pass_count >= 2 and fail_count == 0 else "blocked_native_act_hdf5_adapter",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "pass_count": pass_count,
            "skip_count": skip_count,
            "fail_count": fail_count,
            "act_sim_task_name": task_name,
            "act_sim_task_config": task_config,
            "act_sim_task_config_json": str(out_dir / "SIM_TASK_CONFIGS.generated.json"),
            "next_step": "Run the RoboTwin ACT loader gate, train beyond one epoch, then evaluate held-out seeds using head-camera RGB.",
        }
    )
    write_json(out_dir / "SIM_TASK_CONFIGS.generated.json", {task_name: task_config})
    write_json(out_dir / "conversion_report.json", report)
    print(json.dumps({"status": report["status"], "pass_count": pass_count, "report": str(out_dir / "conversion_report.json")}))
    return 0 if report["status"].startswith("pass_") else 1


if __name__ == "__main__":
    raise SystemExit(main())
