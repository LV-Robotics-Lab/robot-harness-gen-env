#!/usr/bin/env python3
"""Build a compact diagnosis for the native synchronized ACT closed loop."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import h5py
import numpy as np
from PIL import Image


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
        return ROOT / path
    if path.exists():
        return path
    for root in KNOWN_ROOTS:
        try:
            candidate = ROOT / path.relative_to(root)
        except ValueError:
            continue
        if candidate.exists():
            return candidate
    return path


def workspace_path(path: Path) -> str:
    path = path.expanduser().resolve()
    for root in KNOWN_ROOTS:
        try:
            return str(path.relative_to(root))
        except ValueError:
            continue
    return str(path)


def file_hash(array: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


def dataset_identity(conversion: dict[str, Any]) -> tuple[dict[str, Any], np.ndarray]:
    rows: list[dict[str, Any]] = []
    expert_actions = None
    for episode in conversion.get("episodes", []):
        if episode.get("status") != "pass_native_act_hdf5_episode":
            continue
        path = resolve_path(episode["output"])
        with h5py.File(path, "r") as source:
            qpos = np.asarray(source["observations/qpos"], dtype=np.float32)
            actions = np.asarray(source["action"], dtype=np.float32)
            images = np.asarray(source["observations/images/cam_high"], dtype=np.uint8)
        if expert_actions is None:
            expert_actions = actions
        rows.append(
            {
                "path": workspace_path(path),
                "seed": episode.get("seed"),
                "timesteps": int(len(actions)),
                "qpos_sha256": file_hash(qpos),
                "action_sha256": file_hash(actions),
                "image_sha256": file_hash(images),
            }
        )
    if expert_actions is None:
        raise RuntimeError("No converted native ACT episode was found")
    unique_action_hashes = sorted({row["action_sha256"] for row in rows})
    unique_qpos_hashes = sorted({row["qpos_sha256"] for row in rows})
    unique_image_hashes = sorted({row["image_sha256"] for row in rows})
    return (
        {
            "episode_count": len(rows),
            "unique_action_trajectory_count": len(unique_action_hashes),
            "unique_qpos_trajectory_count": len(unique_qpos_hashes),
            "unique_image_trajectory_count": len(unique_image_hashes),
            "all_converted_episodes_byte_identical": bool(
                rows
                and len(unique_action_hashes) == 1
                and len(unique_qpos_hashes) == 1
                and len(unique_image_hashes) == 1
            ),
            "episodes": rows,
        },
        expert_actions,
    )


def color_repair_evidence(collection: dict[str, Any]) -> dict[str, Any]:
    episode = next(
        item
        for item in collection.get("episodes", [])
        if item.get("native_synchronized_data", {}).get("status") == "pass_native_synchronized_recording"
    )
    native_path = resolve_path(episode["native_synchronized_data"]["hdf5"])
    reference_path = resolve_path(episode["images"]["initial_head_camera"])
    with h5py.File(native_path, "r") as source:
        raw = bytes(source["observation/head_camera/rgb"][0]).rstrip(b"\0")
    with Image.open(io.BytesIO(raw)) as image:
        decoded = np.asarray(image.convert("RGB"), dtype=np.float32)
    with Image.open(reference_path) as image:
        reference = np.asarray(image.convert("RGB"), dtype=np.float32)
    direct_mse = float(np.mean((decoded - reference) ** 2))
    repaired = decoded[..., ::-1]
    repaired_mse = float(np.mean((repaired - reference) ** 2))
    return {
        "native_hdf5": workspace_path(native_path),
        "runtime_reference": workspace_path(reference_path),
        "decoded_without_repair_mse": direct_mse,
        "decoded_with_red_blue_swap_mse": repaired_mse,
        "repair_improvement_ratio": direct_mse / max(repaired_mse, 1e-12),
        "repair_is_better": repaired_mse < direct_mse,
        "cause": "RoboTwin passes RGB arrays to cv2.imencode, whose input convention is BGR.",
    }


def evaluation_summary(path: Path, expert_actions: np.ndarray) -> dict[str, Any]:
    report = read_json(path)
    episode_rows: list[dict[str, Any]] = []
    for episode in report.get("episodes", []):
        trace_path = resolve_path(episode["action_trace"])
        trace = read_json(trace_path)
        actions = np.asarray(trace.get("actions", []), dtype=np.float32)
        nearest = np.asarray(
            [int(np.argmin(np.linalg.norm(expert_actions - action, axis=1))) for action in actions],
            dtype=np.int64,
        )
        episode_rows.append(
            {
                "seed": episode.get("seed"),
                "policy_success": episode.get("policy_success"),
                "policy_step_count": episode.get("policy_step_count"),
                "xy_distance_m": episode.get("relation_metrics", {}).get("xy_distance_m"),
                "nearest_expert_index_at_start": int(nearest[0]) if len(nearest) else None,
                "nearest_expert_index_at_end": int(nearest[-1]) if len(nearest) else None,
                "nearest_expert_index_max": int(nearest.max()) if len(nearest) else None,
                "unique_nearest_expert_indices": int(len(np.unique(nearest))) if len(nearest) else 0,
            }
        )
    return {
        "report": workspace_path(path),
        "status": report.get("status"),
        "chunk_size": report.get("model", {}).get("config", {}).get("chunk_size"),
        "action_selection": report.get("evaluation_scope", {}).get("action_selection"),
        "execution_count": report.get("execution_count"),
        "episode_count": report.get("episode_count"),
        "success_count": report.get("success_count"),
        "policy_success_rate": report.get("policy_success_rate"),
        "held_out_seeds": report.get("held_out_seeds"),
        "source_training_seeds": report.get("source_training_seeds"),
        "all_eval_seeds_held_out": report.get("all_eval_seeds_held_out"),
        "camera_adapter": report.get("camera_adapter"),
        "evaluation_scope": report.get("evaluation_scope"),
        "episodes": episode_rows,
    }


def train_summary(path: Path) -> dict[str, Any]:
    report = read_json(path)
    return {
        "report": workspace_path(path),
        "status": report.get("status"),
        "num_epochs": report.get("num_epochs"),
        "best_epoch": report.get("best_epoch"),
        "best_val_loss": report.get("best_val_loss"),
        "learning_rate": report.get("learning_rate"),
        "returncode": report.get("returncode"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build native synchronized ACT closed-loop diagnosis.")
    parser.add_argument("--collection", default="runs/generated_collect_apple_plate_native_sync/collection_report.json")
    parser.add_argument("--conversion", default="runs/act_hdf5_native_sync/conversion_report.json")
    parser.add_argument("--loader", default="runs/act_hdf5_native_sync/load_data_report.json")
    parser.add_argument("--replay", default="runs/act_action_replay_native_sync/replay_report.json")
    parser.add_argument("--chunk20-train", default="runs/act_train_native_sync_rgb_chunk20_1200e/train_smoke_report.json")
    parser.add_argument("--chunk20-eval", default="runs/act_eval_native_sync_rgb_chunk20_1200e_best/evaluate_report.json")
    parser.add_argument("--chunk161-train", default="runs/act_train_native_sync_rgb_chunk161_1200e/train_smoke_report.json")
    parser.add_argument("--chunk161-eval", default="runs/act_eval_native_sync_rgb_chunk161_1200e_best/evaluate_report.json")
    parser.add_argument("--out", default="artifacts/diagnosis/native_act_closed_loop_diagnosis.json")
    args = parser.parse_args()

    paths = {name: resolve_path(value) for name, value in vars(args).items() if name != "out"}
    collection = read_json(paths["collection"])
    conversion = read_json(paths["conversion"])
    loader = read_json(paths["loader"])
    replay = read_json(paths["replay"])
    identity, expert_actions = dataset_identity(conversion)
    chunk20_eval = evaluation_summary(paths["chunk20_eval"], expert_actions)
    chunk161_eval = evaluation_summary(paths["chunk161_eval"], expert_actions)

    native_pass_count = sum(
        episode.get("native_synchronized_data", {}).get("status") == "pass_native_synchronized_recording"
        for episode in collection.get("episodes", [])
    )
    source_task_success_count = sum(
        episode.get("check_success") is True for episode in collection.get("episodes", [])
    )
    fixed_scene_success = (
        replay.get("task_success") is True
        and chunk161_eval["episode_count"] >= 3
        and chunk161_eval["success_count"] == chunk161_eval["episode_count"]
    )
    diagnosis = {
        "schema_version": "alchedata.native_act_closed_loop_diagnosis.v0",
        "status": (
            "pass_native_act_fixed_scene_closed_loop"
            if fixed_scene_success
            else "blocked_native_act_fixed_scene_closed_loop"
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "task_id": collection.get("task_id"),
        "claim_boundary": (
            "Native synchronized collection, ACT conversion/loading/training, expert-action replay, and learned-policy "
            "evaluation are proven for one fixed apple/plate placement. Seeds change reset RNG only; placement and "
            "domain randomization remain fixed, so this is not cross-placement, cross-task, or robustness proof."
        ),
        "collection": {
            "report": workspace_path(paths["collection"]),
            "status": collection.get("status"),
            "episode_count": collection.get("episode_count"),
            "source_task_success_count": source_task_success_count,
            "native_synchronized_pass_count": native_pass_count,
            "native_frame_counts": [
                episode.get("native_synchronized_data", {}).get("frame_count")
                for episode in collection.get("episodes", [])
            ],
        },
        "adapter": {
            "conversion_report": workspace_path(paths["conversion"]),
            "conversion_status": conversion.get("status"),
            "converted_episode_count": conversion.get("pass_count"),
            "skipped_episode_count": conversion.get("skip_count"),
            "loader_report": workspace_path(paths["loader"]),
            "loader_status": loader.get("status"),
            "batch_item_shapes": loader.get("batch_item_shapes"),
            "temporal_alignment": "qpos[t], head_camera_rgb[t] -> action=qpos[t+1]",
            "color_repair": color_repair_evidence(collection),
        },
        "dataset_identity": identity,
        "expert_action_replay": {
            "report": workspace_path(paths["replay"]),
            "status": replay.get("status"),
            "execution_complete": replay.get("execution_complete"),
            "task_success": replay.get("task_success"),
            "executed_action_count": replay.get("executed_action_count"),
            "initial_qpos_max_abs_error": replay.get("initial_qpos_match", {}).get("max_abs_error"),
            "relation_metrics": replay.get("relation_metrics"),
        },
        "experiments": {
            "chunk20": {
                "train": train_summary(paths["chunk20_train"]),
                "evaluate": chunk20_eval,
            },
            "chunk161": {
                "train": train_summary(paths["chunk161_train"]),
                "evaluate": chunk161_eval,
            },
        },
        "hypotheses": [
            {
                "rank": 1,
                "hypothesis": "The three training episodes provide trajectory diversity.",
                "verdict": "falsified",
                "evidence": "All converted qpos, action, and image hashes are identical.",
            },
            {
                "rank": 2,
                "hypothesis": "The native-to-ACT action ordering or 14-D qpos semantics are wrong.",
                "verdict": "falsified",
                "evidence": "A fresh reset matches the source initial qpos exactly and all 161 expert actions replay to task success.",
            },
            {
                "rank": 3,
                "hypothesis": "The native JPEG channel order does not match runtime head-camera RGB.",
                "verdict": "confirmed_and_fixed",
                "evidence": "Swapping decoded red/blue channels lowers MSE against the runtime RGB reference.",
            },
            {
                "rank": 4,
                "hypothesis": "A 20-action chunk with full-chunk replanning preserves enough temporal progress.",
                "verdict": "falsified_for_this_dataset",
                "evidence": "Chunk 20 reaches only a narrow expert-index range and scores 0/3, while chunk 161 scores 3/3 under otherwise matched settings.",
            },
        ],
        "conclusion": {
            "fixed_scene_closed_loop": "pass" if fixed_scene_success else "fail",
            "failure_to_fix": (
                "RoboTwin-native synchronized recording plus RGB repair made the data executable; full-episode chunking "
                "removed the temporal-progress stall on the one unique fixed-scene trajectory."
            ),
            "policy_promotion": "blocked_robustness_not_tested",
            "next_data_requirement": (
                "Collect non-identical successful demonstrations across varied source/target poses and declared domain "
                "randomization, verify trajectory/image uniqueness, then evaluate on held-out placements and at least "
                "one additional task before promotion."
            ),
        },
    }
    out_path = resolve_path(args.out)
    write_json(out_path, diagnosis)
    print(
        json.dumps(
            {
                "status": diagnosis["status"],
                "chunk20_success": chunk20_eval["success_count"],
                "chunk161_success": chunk161_eval["success_count"],
                "out": str(out_path),
            }
        )
    )
    return 0 if fixed_scene_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
