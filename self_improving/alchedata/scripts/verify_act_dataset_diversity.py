#!/usr/bin/env python3
"""Verify that a converted ACT dataset contains real placement and trajectory diversity."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import h5py
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
KNOWN_ROOTS = (
    ROOT,
    Path("/home/jingxiang/workspace/alchedata-self-improving-agents"),
    Path("/Users/boris/workspace/alchedata-self-improving-agents"),
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def resolve_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        return (ROOT / path).resolve()
    if path.exists():
        return path
    for known_root in KNOWN_ROOTS:
        try:
            candidate = ROOT / path.relative_to(known_root)
        except ValueError:
            continue
        if candidate.exists():
            return candidate
    return path


def workspace_path(path: Path) -> str:
    path = path.expanduser().resolve()
    for known_root in KNOWN_ROOTS:
        try:
            return str(path.relative_to(known_root))
        except ValueError:
            continue
    return str(path)


def array_hash(array: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


def inspect_episode(episode: dict[str, Any]) -> dict[str, Any]:
    path = resolve_path(episode["output"])
    with h5py.File(path, "r") as source:
        qpos = np.asarray(source["observations/qpos"])
        actions = np.asarray(source["action"])
        images = np.asarray(source["observations/images/cam_high"])
    if not (len(qpos) == len(actions) == len(images)):
        raise ValueError(f"ACT arrays have mismatched lengths: {path}")
    return {
        "path": workspace_path(path),
        "seed": episode.get("seed"),
        "placement_id": episode.get("placement_id"),
        "placement_split": episode.get("placement_split"),
        "pose_signature": episode.get("pose_signature"),
        "timesteps": int(len(actions)),
        "qpos_sha256": array_hash(qpos),
        "action_sha256": array_hash(actions),
        "image_sha256": array_hash(images),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify ACT dataset placement and trajectory diversity.")
    parser.add_argument("--conversion", required=True)
    parser.add_argument("--loader", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--min-episodes", type=int, default=6)
    parser.add_argument("--min-placements", type=int, default=6)
    parser.add_argument("--min-action-trajectories", type=int, default=6)
    parser.add_argument("--min-qpos-trajectories", type=int, default=6)
    parser.add_argument("--min-image-trajectories", type=int, default=6)
    args = parser.parse_args()

    conversion_path = resolve_path(args.conversion)
    loader_path = resolve_path(args.loader)
    conversion = read_json(conversion_path)
    loader = read_json(loader_path)
    episodes = [
        inspect_episode(episode)
        for episode in conversion.get("episodes", [])
        if episode.get("status") == "pass_native_act_hdf5_episode"
    ]
    pose_signatures = [row["pose_signature"] for row in episodes if row.get("pose_signature")]
    action_hashes = [row["action_sha256"] for row in episodes]
    qpos_hashes = [row["qpos_sha256"] for row in episodes]
    image_hashes = [row["image_sha256"] for row in episodes]
    placements: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(episodes):
        if row.get("pose_signature"):
            placements[row["pose_signature"]].append(index)

    observed = {
        "episode_count": len(episodes),
        "unique_placement_signature_count": len(set(pose_signatures)),
        "unique_action_trajectory_count": len(set(action_hashes)),
        "unique_qpos_trajectory_count": len(set(qpos_hashes)),
        "unique_image_trajectory_count": len(set(image_hashes)),
    }
    thresholds = {
        "min_episodes": args.min_episodes,
        "min_placements": args.min_placements,
        "min_action_trajectories": args.min_action_trajectories,
        "min_qpos_trajectories": args.min_qpos_trajectories,
        "min_image_trajectories": args.min_image_trajectories,
    }
    gates = {
        "conversion_passed": conversion.get("status") == "pass_native_act_hdf5_adapter",
        "loader_passed": loader.get("status") == "pass_act_hdf5_loader_smoke",
        "episode_count_met": observed["episode_count"] >= args.min_episodes,
        "placement_count_met": observed["unique_placement_signature_count"] >= args.min_placements,
        "action_trajectory_count_met": (
            observed["unique_action_trajectory_count"] >= args.min_action_trajectories
        ),
        "qpos_trajectory_count_met": observed["unique_qpos_trajectory_count"] >= args.min_qpos_trajectories,
        "image_trajectory_count_met": observed["unique_image_trajectory_count"] >= args.min_image_trajectories,
        "all_episodes_have_pose_signatures": len(pose_signatures) == len(episodes),
    }
    passed = all(gates.values())
    report = {
        "schema_version": "alchedata.act_dataset_diversity.v0",
        "status": "pass_act_dataset_diversity" if passed else "blocked_act_dataset_diversity",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "conversion_report": workspace_path(conversion_path),
        "loader_report": workspace_path(loader_path),
        "thresholds": thresholds,
        "observed": observed,
        "gates": gates,
        "placement_episode_indices": dict(placements),
        "duplicate_hash_counts": {
            "action": {key: count for key, count in Counter(action_hashes).items() if count > 1},
            "qpos": {key: count for key, count in Counter(qpos_hashes).items() if count > 1},
            "image": {key: count for key, count in Counter(image_hashes).items() if count > 1},
        },
        "episodes": episodes,
        "claim_boundary": (
            "This gate proves byte-level diversity across successful synchronized ACT demonstrations and explicit "
            "placement signatures. It does not prove policy learning or held-out task success."
        ),
    }
    out_path = resolve_path(args.out)
    write_json(out_path, report)
    print(json.dumps({"status": report["status"], "observed": observed, "out": str(out_path)}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
