#!/usr/bin/env python3
"""Convert generated play_once joint-path traces into an ACT-compatible HDF5 smoke dataset."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import h5py
import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COLLECTIONS = [
    ROOT / "runs" / "generated_collect_apple_plate_action_repair" / "collection_report.json",
    ROOT / "runs" / "generated_collect_can_basket_action_repair" / "collection_report.json",
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return ROOT / path


def load_rgb(path: Path, size: tuple[int, int]) -> np.ndarray:
    image = Image.open(path).convert("RGB").resize(size)
    return np.asarray(image, dtype=np.uint8)


def normalize_action(row: Any, dim: int = 14) -> np.ndarray:
    arr = np.asarray(row, dtype=np.float32).reshape(-1)
    if arr.size >= dim:
        return arr[:dim]
    out = np.zeros(dim, dtype=np.float32)
    out[: arr.size] = arr
    return out


def source_episode_passed(episode: dict[str, Any]) -> bool:
    return str(episode.get("status", "")).startswith("pass_") and bool(episode.get("check_success"))


def stitch_segment_positions(segments: list[Any]) -> np.ndarray:
    rows: list[np.ndarray] = []
    for segment in segments:
        if isinstance(segment, dict):
            if segment.get("status") and segment.get("status") != "Success":
                continue
            value = segment.get("position")
        else:
            value = segment
        if value is None:
            continue
        arr = np.asarray(value, dtype=np.float32)
        if arr.ndim == 1:
            rows.append(arr)
        elif arr.ndim == 2:
            rows.extend(arr)
        else:
            rows.extend(arr.reshape((-1, arr.shape[-1])))
    if not rows:
        return np.zeros((0, 0), dtype=np.float32)
    return np.stack([normalize_action(row) for row in rows]).astype(np.float32)


def episode_trace_path(episode: dict[str, Any]) -> Path | None:
    report_path = resolve_path(episode.get("report", ""))
    if report_path.exists():
        report = read_json(report_path)
        if report.get("policy_trace"):
            path = resolve_path(report["policy_trace"])
            if path.exists():
                return path
    fallback = report_path.parent / "policy_trace.json"
    return fallback if fallback.exists() else None


def convert_episode(episode: dict[str, Any], output_path: Path, camera_size: tuple[int, int]) -> dict[str, Any]:
    if not source_episode_passed(episode):
        return {
            "status": "skipped_source_episode_not_passed",
            "episode_index": episode.get("episode_index"),
            "source_status": episode.get("status"),
            "check_success": episode.get("check_success"),
            "report": episode.get("report"),
        }
    trace_path = episode_trace_path(episode)
    if trace_path is None:
        return {
            "status": "blocked_missing_policy_trace",
            "episode_index": episode.get("episode_index"),
            "report": episode.get("report"),
        }
    trace = read_json(trace_path)
    left_path = trace.get("left_joint_path", [])
    right_path = trace.get("right_joint_path", [])
    qpos = stitch_segment_positions(left_path or right_path)
    if not len(qpos):
        return {
            "status": "blocked_empty_joint_path",
            "episode_index": episode.get("episode_index"),
            "trace": str(trace_path),
        }
    if len(qpos) < 2:
        return {
            "status": "blocked_joint_path_too_short",
            "episode_index": episode.get("episode_index"),
            "trace": str(trace_path),
            "joint_path_len": len(qpos),
        }
    action = qpos.copy()
    images = episode.get("images", {})
    initial = load_rgb(resolve_path(images["initial_observer_camera"]), camera_size)
    final = load_rgb(resolve_path(images["final_observer_camera"]), camera_size)
    frames = np.stack([initial if index < len(qpos) - 1 else final for index in range(len(qpos))], axis=0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(output_path, "w") as root:
        root.attrs["sim"] = True
        root.attrs["source_episode_report"] = str(resolve_path(episode["report"]))
        root.attrs["source_policy_trace"] = str(trace_path)
        root.attrs["claim_boundary"] = (
            "ACT-compatible smoke dataset from generated planner joint paths and sparse observer frames; "
            "not synchronized teleoperation data or a learned-policy-quality dataset."
        )
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
            chunks=(min(len(frames), 64), camera_size[1], camera_size[0], 3),
        )
        root.create_dataset("action", data=action)
    return {
        "status": "pass_act_hdf5_episode",
        "episode_index": episode.get("episode_index"),
        "output": str(output_path),
        "source_policy_trace": str(trace_path),
        "timesteps": int(len(qpos)),
        "action_dim": int(qpos.shape[1]),
        "camera_names": ["cam_high"],
        "camera_size": {"width": camera_size[0], "height": camera_size[1]},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert generated play_once collection traces into ACT HDF5 smoke data.")
    parser.add_argument("--collection", action="append", default=None)
    parser.add_argument("--out-dir", default=str(ROOT / "runs" / "act_hdf5_generated_smoke"))
    parser.add_argument("--camera-width", type=int, default=96)
    parser.add_argument("--camera-height", type=int, default=72)
    args = parser.parse_args()

    collections = [Path(item).expanduser().resolve() for item in args.collection] if args.collection else DEFAULT_COLLECTIONS
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    camera_size = (args.camera_width, args.camera_height)
    report: dict[str, Any] = {
        "schema_version": "alchedata.act_hdf5_adapter.v0",
        "status": "started",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "out_dir": str(out_dir),
        "collections": [],
        "episodes": [],
        "claim_boundary": (
            "This is a data-format adapter smoke for RoboTwin ACT. It converts generated planner joint paths into HDF5 files "
            "that ACT utilities can read. It does not claim high-quality policy data, synchronized camera trajectories, /train success, or /evaluate success."
        ),
    }
    write_json(out_dir / "conversion_report.json", report)

    next_episode = 0
    for collection_path in collections:
        collection = read_json(collection_path)
        collection_record = {
            "path": str(collection_path),
            "task_id": collection.get("task_id"),
            "status": collection.get("status"),
            "episode_count": len(collection.get("episodes", [])),
        }
        report["collections"].append(collection_record)
        for episode in collection.get("episodes", []):
            converted = convert_episode(episode, out_dir / "data" / f"episode_{next_episode}.hdf5", camera_size)
            converted["source_collection"] = str(collection_path)
            converted["act_episode_index"] = next_episode
            report["episodes"].append(converted)
            if converted["status"] == "pass_act_hdf5_episode":
                next_episode += 1

    pass_count = sum(1 for episode in report["episodes"] if episode["status"] == "pass_act_hdf5_episode")
    skip_count = sum(1 for episode in report["episodes"] if episode["status"].startswith("skipped_"))
    fail_count = len(report["episodes"]) - pass_count - skip_count
    sim_task_name = "sim-generated_selection2env-demo_clean-" + str(pass_count)
    sim_task_config = {
        sim_task_name: {
            "dataset_dir": str(out_dir / "data"),
            "num_episodes": pass_count,
            "episode_len": max((episode.get("timesteps", 0) for episode in report["episodes"]), default=0),
            "camera_names": ["cam_high"],
        }
    }
    report.update(
        {
            "status": "pass_act_hdf5_adapter_smoke" if pass_count >= 2 and fail_count == 0 else "blocked_act_hdf5_adapter_smoke",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "pass_count": pass_count,
            "skip_count": skip_count,
            "fail_count": fail_count,
            "act_sim_task_name": sim_task_name,
            "act_sim_task_config": sim_task_config[sim_task_name],
            "act_sim_task_config_json": str(out_dir / "SIM_TASK_CONFIGS.generated.json"),
            "next_step": "Merge or point ACT SIM_TASK_CONFIGS at this config, then run a one-epoch train import/data-loader smoke.",
        }
    )
    write_json(out_dir / "SIM_TASK_CONFIGS.generated.json", sim_task_config)
    write_json(out_dir / "conversion_report.json", report)
    print(json.dumps({"status": report["status"], "pass_count": pass_count, "report": str(out_dir / "conversion_report.json")}, ensure_ascii=False))
    return 0 if report["status"].startswith("pass_") else 1


if __name__ == "__main__":
    raise SystemExit(main())
