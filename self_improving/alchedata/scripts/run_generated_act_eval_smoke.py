#!/usr/bin/env python3
"""Run a bounded learned-ACT evaluation on a generated selection2env task."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pickle
import sys
import time
import traceback
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import imageio.v2 as imageio
import numpy as np
import torch
from PIL import Image

from run_generated_selection2env_rollout_probe import (
    apply_domain_randomization,
    infer_task_binding,
    load_robotwin_args,
    object_specs_by_id,
    read_json,
    requested_domain_randomization,
    write_json,
)
from placement_manifest_utils import (
    fixed_placement_cases,
    load_placement_cases,
    passed_training_placement_signatures,
)
from harness_observation_adapter import (
    RUNTIME_COLOR_ADAPTERS,
    apply_runtime_color_adapter,
    color_adapter_reason,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLACEMENT = ROOT / "runs" / "probe_static_apple_plate_action_repair" / "final_placement.json"
DEFAULT_CHECKPOINT_DIR = ROOT / "runs" / "act_train_smoke_generated" / "act_ckpt"
DEFAULT_OUT_DIR = ROOT / "runs" / "act_eval_smoke_generated"
TRAIN_COLLECTION_REPORTS = (
    ROOT / "runs" / "generated_collect_apple_plate_action_repair" / "collection_report.json",
    ROOT / "runs" / "generated_collect_can_basket_action_repair" / "collection_report.json",
)
REQUIRED_DIAGNOSIS_CATEGORIES = (
    "wrong_grasp_location",
    "object_knocked_over",
    "arm_jitter",
    "uncontrolled_gripper_open_close",
    "after_contact_failure",
    "visual_material_mismatch",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def pose_record(actor: Any) -> dict[str, list[float]]:
    pose = actor.get_pose()
    return {"p": np.asarray(pose.p, dtype=float).tolist(), "q": np.asarray(pose.q, dtype=float).tolist()}


def save_rgb(path: Path, rgb: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.asarray(rgb, dtype=np.uint8)).save(path)


def resize_rgb(rgb: np.ndarray, width: int, height: int) -> np.ndarray:
    return np.asarray(Image.fromarray(np.asarray(rgb, dtype=np.uint8)).resize((width, height)), dtype=np.uint8)


def source_training_seeds(report_paths: list[Path]) -> set[int]:
    seeds: set[int] = set()
    for report_path in report_paths:
        if not report_path.exists():
            continue
        report = read_json(report_path)
        for episode in report.get("episodes", []):
            source_passed = str(episode.get("status", "")).startswith("pass_") and episode.get("check_success") is True
            native = episode.get("native_synchronized_data", {})
            native_passed = not native or native.get("status") == "pass_native_synchronized_recording"
            if source_passed and native_passed and isinstance(episode.get("seed"), int):
                seeds.add(episode["seed"])
    return seeds


def quaternion_angle_deg(first: list[float], second: list[float]) -> float:
    q1 = np.asarray(first, dtype=float)
    q2 = np.asarray(second, dtype=float)
    q1 /= max(float(np.linalg.norm(q1)), 1e-9)
    q2 /= max(float(np.linalg.norm(q2)), 1e-9)
    return float(np.degrees(2.0 * np.arccos(np.clip(abs(float(np.dot(q1, q2))), 0.0, 1.0))))


def build_task_class(base_task: type, sapien: Any, create_actor: Any, create_sapien_urdf_obj: Any) -> type:
    class GeneratedSelection2EnvEvalTask(base_task):
        def __init__(self, placement: dict[str, Any], binding: dict[str, str]):
            super().__init__()
            self.placement_spec = placement
            self.task_binding = binding
            self.object_specs = object_specs_by_id(placement)
            self.placement_objects: dict[str, Any] = {}

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
                asset_type = metadata.get("asset_type", "rigid")
                if defaults.get("loader") == "sapien_urdf" or asset_type == "articulated":
                    actor = create_sapien_urdf_obj(
                        self,
                        pose=sapien.Pose(xyz, qpos),
                        modelname=obj["asset_id"],
                        modelid=obj.get("model_id", 0),
                        fix_root_link=defaults.get("fix_root_link", obj.get("physical", {}).get("is_static", False)),
                    )
                    if actor is not None and "articulation_qpos" in defaults:
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
                    raise RuntimeError(f"Failed to load generated asset {obj['asset_id']}")
                actor.set_name(obj["id"])
                self.placement_objects[obj["id"]] = actor
                setattr(self, obj["id"], actor)

        def play_once(self) -> dict[str, Any]:
            return {
                "info": {
                    "{source}": self.task_binding["source_id"],
                    "{target}": self.task_binding["target_id"],
                    "{template}": self.task_binding["template"],
                }
            }

        def relation_metrics(self) -> dict[str, Any]:
            source = self.placement_objects[self.task_binding["source_id"]]
            target = self.placement_objects[self.task_binding["target_id"]]
            source_p = np.asarray(source.get_pose().p, dtype=float)
            target_p = np.asarray(target.get_pose().p, dtype=float)
            return {
                "xy_distance_m": float(np.linalg.norm(source_p[:2] - target_p[:2])),
                "source_z_m": float(source_p[2]),
                "target_z_m": float(target_p[2]),
                "source_minus_target_z_m": float(source_p[2] - target_p[2]),
                "left_gripper_open": bool(self.robot.is_left_gripper_open()),
                "right_gripper_open": bool(self.robot.is_right_gripper_open()),
            }

        def check_success(self) -> bool:
            metrics = self.relation_metrics()
            if not metrics["left_gripper_open"] or not metrics["right_gripper_open"]:
                return False
            if self.task_binding["template"] == "place_in":
                return bool(metrics["xy_distance_m"] < 0.13 and metrics["source_minus_target_z_m"] > 0.0)
            target_radius = 0.08
            try:
                target_spec = self.object_specs[self.task_binding["target_id"]]
                extents = np.asarray(target_spec["asset_metadata"]["approx_scaled_extents_m"], dtype=float)
                target_radius = float(max(extents[0], extents[1]) / 2.0)
            except Exception:
                pass
            return bool(
                metrics["xy_distance_m"] < min(max(target_radius, 0.08), 0.13)
                and metrics["source_minus_target_z_m"] >= -0.03
            )

    return GeneratedSelection2EnvEvalTask


def model_config(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "kl_weight": 1.0,
        "chunk_size": args.chunk_size,
        "hidden_dim": args.hidden_dim,
        "dim_feedforward": args.dim_feedforward,
        "action_dim": 14,
        "state_dim": 14,
        "device": args.device,
        "camera_names": ["cam_high"],
        "position_embedding": "sine",
        "lr_backbone": 1e-5,
        "weight_decay": 1e-4,
        "lr": 1e-5,
        "masks": False,
        "dilation": False,
        "backbone": "resnet18",
        "nheads": 8,
        "enc_layers": 4,
        "dec_layers": 7,
        "pre_norm": False,
        "dropout": 0.1,
        "policy_class": "ACT",
        "num_epochs": 1,
    }


def load_policy(args: argparse.Namespace, checkpoint_dir: Path) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    from policy.ACT.act_policy import ACTPolicy

    config = model_config(args)
    policy = ACTPolicy(config, Namespace(**config)).to(torch.device(args.device)).eval()
    checkpoint_path = checkpoint_dir / args.checkpoint_name
    stats_path = checkpoint_dir / "dataset_stats.pkl"
    if not checkpoint_path.exists() or not stats_path.exists():
        raise FileNotFoundError(f"Missing checkpoint or dataset stats under {checkpoint_dir}")
    state = torch.load(checkpoint_path, map_location=args.device)
    load_status = policy.load_state_dict(state)
    with stats_path.open("rb") as handle:
        stats = pickle.load(handle)
    model_record = {
        "checkpoint": str(checkpoint_path),
        "checkpoint_size_bytes": checkpoint_path.stat().st_size,
        "checkpoint_sha256": sha256_file(checkpoint_path),
        "dataset_stats": str(stats_path),
        "dataset_stats_sha256": sha256_file(stats_path),
        "load_status": str(load_status),
        "all_keys_matched": "All keys matched successfully" in str(load_status),
        "config": config,
        "parameter_count": int(sum(parameter.numel() for parameter in policy.parameters())),
    }
    return policy, stats, model_record


def infer_action(
    policy: Any,
    stats: dict[str, Any],
    qpos: np.ndarray,
    rgb: np.ndarray,
    args: argparse.Namespace,
) -> tuple[np.ndarray, dict[str, Any]]:
    resized = resize_rgb(rgb, args.camera_width, args.camera_height)
    image = np.moveaxis(resized, -1, 0).astype(np.float32) / 255.0
    qpos_normalized = (qpos - stats["qpos_mean"]) / stats["qpos_std"]
    qpos_tensor = torch.from_numpy(qpos_normalized).float().to(args.device).unsqueeze(0)
    image_tensor = torch.from_numpy(image).float().to(args.device).unsqueeze(0).unsqueeze(0)
    started = time.perf_counter()
    with torch.inference_mode():
        normalized_actions = policy(qpos_tensor, image_tensor)
    inference_ms = (time.perf_counter() - started) * 1000.0
    normalized_chunk = normalized_actions[0].detach().cpu().numpy()
    action_chunk = normalized_chunk * stats["action_std"] + stats["action_mean"]
    return action_chunk, {
        "inference_ms": inference_ms,
        "predicted_action_count": int(len(action_chunk)),
        "first_normalized_action": normalized_chunk[0].tolist(),
        "first_action": action_chunk[0].tolist(),
        "action_min": float(np.min(action_chunk)),
        "action_max": float(np.max(action_chunk)),
        "finite": bool(np.isfinite(action_chunk).all()),
    }


def policy_camera_rgb(observation: dict[str, Any], source: str, color_adapter: str) -> np.ndarray:
    if source == "head_camera":
        rgb = observation["observation"]["head_camera"]["rgb"]
    else:
        rgb = observation["third_view_rgb"]
    return apply_runtime_color_adapter(rgb, color_adapter)


def diagnose_episode(
    initial_poses: dict[str, dict[str, list[float]]],
    final_poses: dict[str, dict[str, list[float]]],
    binding: dict[str, str],
    actions: list[list[float]],
    success: bool,
    infrastructure_error: str | None,
) -> dict[str, Any]:
    source_id = binding["source_id"]
    target_id = binding["target_id"]
    missing_pose_ids = [
        object_id
        for object_id in (source_id, target_id)
        if object_id not in initial_poses or object_id not in final_poses
    ]
    if missing_pose_ids:
        categories = {
            category: {
                "status": "not_measured",
                "evidence": {"missing_pose_ids": missing_pose_ids},
            }
            for category in REQUIRED_DIAGNOSIS_CATEGORIES
        }
        categories["target_relation_not_satisfied"] = {
            "status": "not_measured",
            "evidence": {"missing_pose_ids": missing_pose_ids},
        }
        return {
            "status": "infrastructure_error",
            "required_categories_checked": list(REQUIRED_DIAGNOSIS_CATEGORIES),
            "categories": categories,
            "infrastructure_error": infrastructure_error or "Required object poses were not captured.",
        }
    source_delta = float(
        np.linalg.norm(np.asarray(final_poses[source_id]["p"]) - np.asarray(initial_poses[source_id]["p"]))
    )
    target_delta = float(
        np.linalg.norm(np.asarray(final_poses[target_id]["p"]) - np.asarray(initial_poses[target_id]["p"]))
    )
    target_rotation_deg = quaternion_angle_deg(initial_poses[target_id]["q"], final_poses[target_id]["q"])
    action_array = np.asarray(actions, dtype=float) if actions else np.zeros((0, 14), dtype=float)
    max_action_delta = float(np.max(np.abs(np.diff(action_array, axis=0)))) if len(action_array) > 1 else 0.0
    gripper_toggles = 0
    if len(action_array) > 1:
        for index in (6, 13):
            state = action_array[:, index] > 0.5
            gripper_toggles += int(np.count_nonzero(state[1:] != state[:-1]))

    categories = {
        "wrong_grasp_location": {
            "status": "suspected" if not success and source_delta < 0.02 else "not_observed",
            "evidence": {"source_displacement_m": source_delta},
        },
        "object_knocked_over": {
            "status": "observed" if target_delta > 0.03 or target_rotation_deg > 20.0 else "not_observed",
            "evidence": {"target_displacement_m": target_delta, "target_rotation_deg": target_rotation_deg},
        },
        "arm_jitter": {
            "status": "observed" if max_action_delta > 1.0 else "not_observed",
            "evidence": {"max_consecutive_action_delta": max_action_delta},
        },
        "uncontrolled_gripper_open_close": {
            "status": "observed" if gripper_toggles >= 3 else "not_observed",
            "evidence": {"gripper_threshold_toggle_count": gripper_toggles},
        },
        "after_contact_failure": {
            "status": "suspected" if not success and source_delta >= 0.02 else "not_observed",
            "evidence": {"source_displacement_m": source_delta, "success_verifier": success},
        },
        "visual_material_mismatch": {
            "status": "not_measured",
            "evidence": "No calibrated material reference or material-sidecar render gate is available for this task.",
        },
        "target_relation_not_satisfied": {
            "status": "not_observed" if success else "observed",
            "evidence": {"success_verifier": success},
        },
    }
    return {
        "status": "infrastructure_error" if infrastructure_error else ("no_failure_observed" if success else "policy_failure_observed"),
        "required_categories_checked": list(REQUIRED_DIAGNOSIS_CATEGORIES),
        "categories": categories,
        "infrastructure_error": infrastructure_error,
    }


def run_episode(
    task_class: type,
    placement: dict[str, Any],
    binding: dict[str, str],
    policy: Any,
    stats: dict[str, Any],
    args: argparse.Namespace,
    seed: int,
    training_seeds: set[int],
    placement_case: dict[str, Any],
    training_placement_signatures: set[str],
    episode_dir: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    actions: list[list[float]] = []
    frames: list[np.ndarray] = []
    task = task_class(placement, binding)
    rt_args = load_robotwin_args(Path(args.robotwin_root), args.task_config, save_path=episode_dir)
    rt_args["task_name"] = f"generated_selection2env_{args.task_id}"
    rt_args["data_type"]["third_view"] = True
    apply_domain_randomization(rt_args, requested_domain_randomization(args))
    initial_poses: dict[str, dict[str, list[float]]] = {}
    final_poses: dict[str, dict[str, list[float]]] = {}
    infrastructure_error = None
    setup_seconds = None
    success = False
    relation_metrics_snapshot = None

    try:
        setup_started = time.perf_counter()
        task.setup_demo(now_ep_num=0, seed=seed, **rt_args)
        task.step_lim = args.max_steps
        setup_seconds = time.perf_counter() - setup_started
        initial_poses = {name: pose_record(actor) for name, actor in task.placement_objects.items()}
        observation = task.get_obs()
        initial_head = observation["observation"]["head_camera"]["rgb"]
        initial_observer = observation["third_view_rgb"]
        save_rgb(episode_dir / "initial_head_camera.png", initial_head)
        save_rgb(episode_dir / "initial_observer_camera.png", initial_observer)
        frames.append(resize_rgb(initial_observer, args.camera_width, args.camera_height))
        events.append(
            {
                "at": utc_now(),
                "event": "episode_ready",
                "seed": seed,
                "placement_id": placement_case["placement_id"],
                "pose_signature": placement_case.get("pose_signature"),
                "setup_seconds": setup_seconds,
            }
        )

        step = 0
        while step < args.max_steps:
            observation = task.get_obs()
            qpos = np.asarray(observation["joint_action"]["vector"], dtype=np.float32)
            action_chunk, inference = infer_action(
                policy,
                stats,
                qpos,
                policy_camera_rgb(observation, args.runtime_camera_source, args.runtime_color_adapter),
                args,
            )
            if not inference["finite"]:
                raise RuntimeError("ACT produced a non-finite action chunk")
            if args.action_execution == "receding-first":
                execution_chunk = action_chunk[:1]
            elif args.action_execution == "chunk-prefix":
                execution_chunk = action_chunk[: args.execution_horizon]
            else:
                execution_chunk = action_chunk
            events.append(
                {
                    "at": utc_now(),
                    "event": "policy_inference",
                    "step": step,
                    "seed": seed,
                    "inference_ms": inference["inference_ms"],
                    "predicted_action_count": inference["predicted_action_count"],
                    "scheduled_action_count": int(min(len(execution_chunk), args.max_steps - step)),
                    "action_min": inference["action_min"],
                    "action_max": inference["action_max"],
                }
            )
            for chunk_index, action in enumerate(execution_chunk):
                if step >= args.max_steps:
                    break
                task.take_action(action)
                actions.append(action.tolist())
                success = bool(task.eval_success or task.check_success())
                post_observation = task.get_obs()
                frames.append(resize_rgb(post_observation["third_view_rgb"], args.camera_width, args.camera_height))
                events.append(
                    {
                        "at": utc_now(),
                        "event": "policy_step",
                        "step": step,
                        "chunk_index": chunk_index,
                        "seed": seed,
                        "success_verifier": success,
                    }
                )
                step += 1
                if success:
                    break
            if success:
                break
    except Exception as exc:  # noqa: BLE001
        infrastructure_error = f"{type(exc).__name__}: {exc}"
        events.append(
            {
                "at": utc_now(),
                "event": "episode_error",
                "seed": seed,
                "error": infrastructure_error,
                "traceback": traceback.format_exc(),
            }
        )
    finally:
        if getattr(task, "placement_objects", None):
            final_poses = {name: pose_record(actor) for name, actor in task.placement_objects.items()}
            try:
                relation_metrics_snapshot = task.relation_metrics()
            except Exception:
                relation_metrics_snapshot = None
            try:
                final_observation = task.get_obs()
                save_rgb(episode_dir / "final_head_camera.png", final_observation["observation"]["head_camera"]["rgb"])
                save_rgb(episode_dir / "final_observer_camera.png", final_observation["third_view_rgb"])
            except Exception:
                pass
        try:
            task.close_env(clear_cache=True)
        except Exception:
            pass

    action_trace_path = episode_dir / "action_trace.json"
    write_json(
        action_trace_path,
        {
            "schema_version": "alchedata.generated_act_action_trace.v0",
            "seed": seed,
            "action_count": len(actions),
            "actions": actions,
        },
    )
    events_path = episode_dir / "events.jsonl"
    write_jsonl(events_path, events)
    video_path = episode_dir / "observer_policy_rollout.mp4"
    if frames:
        imageio.mimsave(video_path, frames, fps=args.fps, macro_block_size=1)
    diagnosis = diagnose_episode(initial_poses, final_poses, binding, actions, success, infrastructure_error)
    diagnosis_path = episode_dir / "failure_diagnosis.json"
    write_json(diagnosis_path, diagnosis)
    episode_report = {
        "schema_version": "alchedata.generated_act_evaluate_episode.v0",
        "command": "/evaluate",
        "task_id": args.task_id,
        "seed": seed,
        "held_out_seed": seed not in training_seeds,
        "placement_id": placement_case["placement_id"],
        "placement": str(placement_case["placement_path"]),
        "placement_split": placement_case["split"],
        "pose_signature": placement_case.get("pose_signature"),
        "pose_vector": placement_case.get("pose_vector"),
        "held_out_placement": bool(
            placement_case.get("pose_signature")
            and placement_case["pose_signature"] not in training_placement_signatures
        ),
        "status": "completed_policy_success" if success else ("completed_policy_failure" if infrastructure_error is None else "blocked_infrastructure_error"),
        "execution_complete": infrastructure_error is None,
        "policy_success": success,
        "policy_step_count": len(actions),
        "setup_seconds": setup_seconds,
        "relation_metrics": relation_metrics_snapshot,
        "initial_poses": initial_poses,
        "final_poses": final_poses,
        "failure_diagnosis": str(diagnosis_path),
        "events": str(events_path),
        "action_trace": str(action_trace_path),
        "observer_video": str(video_path) if video_path.exists() else None,
        "images": {
            "initial_head_camera": str(episode_dir / "initial_head_camera.png"),
            "initial_observer_camera": str(episode_dir / "initial_observer_camera.png"),
            "final_head_camera": str(episode_dir / "final_head_camera.png"),
            "final_observer_camera": str(episode_dir / "final_observer_camera.png"),
        },
        "infrastructure_error": infrastructure_error,
    }
    write_json(episode_dir / "episode_report.json", episode_report)
    return episode_report, events


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded learned-ACT /evaluate runner for generated selection2env tasks.")
    parser.add_argument("--robotwin-root", default=str(ROOT / "external" / "RoboTwin"))
    parser.add_argument("--placement", default=str(DEFAULT_PLACEMENT))
    parser.add_argument("--placement-manifest")
    parser.add_argument("--placement-split", default="eval")
    parser.add_argument("--task-id", default="task_apple_plate")
    parser.add_argument("--checkpoint-dir", default=str(DEFAULT_CHECKPOINT_DIR))
    parser.add_argument("--checkpoint-name", default="policy_best.ckpt")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--task-config", default="demo_clean")
    parser.add_argument("--seed", action="append", type=int, dest="seeds")
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--fps", type=int, default=8)
    parser.add_argument("--camera-width", type=int, default=96)
    parser.add_argument("--camera-height", type=int, default=72)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--hidden-dim", type=int, default=64)
    parser.add_argument("--dim-feedforward", type=int, default=256)
    parser.add_argument("--chunk-size", type=int, default=20)
    parser.add_argument(
        "--action-execution",
        choices=["receding-first", "chunk-prefix", "full-chunk"],
        default="receding-first",
    )
    parser.add_argument(
        "--execution-horizon",
        type=int,
        default=20,
        help="Number of predicted actions to execute before replanning in chunk-prefix mode.",
    )
    parser.add_argument("--runtime-camera-source", choices=["observer_camera", "head_camera"], default="observer_camera")
    parser.add_argument("--runtime-color-adapter", choices=RUNTIME_COLOR_ADAPTERS, default="identity")
    parser.add_argument("--training-collection-report", action="append")
    parser.add_argument("--random-background", action="store_true")
    parser.add_argument("--cluttered-table", action="store_true")
    parser.add_argument("--random-light", action="store_true")
    parser.add_argument("--random-table-height", type=float, default=0.0)
    parser.add_argument("--random-head-camera-dis", type=float, default=0.0)
    args = parser.parse_args()
    if args.action_execution == "chunk-prefix" and (
        args.execution_horizon < 1 or args.execution_horizon > args.chunk_size
    ):
        parser.error("--execution-horizon must be between 1 and --chunk-size")
    args.robotwin_root = str(Path(args.robotwin_root).expanduser().resolve())

    out_dir = Path(args.out_dir).expanduser().resolve()
    checkpoint_dir = Path(args.checkpoint_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.placement_manifest:
        placement_manifest, placement_cases = load_placement_cases(
            Path(args.placement_manifest),
            args.placement_split,
            args.seeds,
        )
        placement_path = None
    else:
        args.seeds = args.seeds or [4]
        placement_path = Path(args.placement).expanduser().resolve()
        placement_cases = fixed_placement_cases(placement_path, args.seeds)
        placement_manifest = None
    args.seeds = [case["seed"] for case in placement_cases]
    first_placement = read_json(placement_cases[0]["placement_path"])
    binding = infer_task_binding(args.task_id, first_placement)
    training_report_paths = (
        [Path(value).expanduser().resolve() for value in args.training_collection_report]
        if args.training_collection_report
        else list(TRAIN_COLLECTION_REPORTS)
    )
    training_seeds = source_training_seeds(training_report_paths)
    training_placement_signatures = passed_training_placement_signatures(training_report_paths)
    eval_signatures = {
        str(case["pose_signature"])
        for case in placement_cases
        if case.get("pose_signature")
    }
    all_eval_placements_held_out = bool(
        eval_signatures
        and len(eval_signatures) == len(placement_cases)
        and training_placement_signatures
        and not (eval_signatures & training_placement_signatures)
    )
    domain_randomization = requested_domain_randomization(args)
    report_path = out_dir / "evaluate_report.json"
    report: dict[str, Any] = {
        "schema_version": "alchedata.generated_act_evaluate.v0",
        "command": "/evaluate",
        "status": "started",
        "started_at": utc_now(),
        "task_id": args.task_id,
        "task_config": args.task_config,
        "argv": sys.argv,
        "held_out_seeds": [case["seed"] for case in placement_cases],
        "source_training_seeds": sorted(training_seeds),
        "source_training_collection_reports": [str(path) for path in training_report_paths],
        "all_eval_seeds_held_out": all(case["seed"] not in training_seeds for case in placement_cases),
        "placement": str(placement_path) if placement_path else None,
        "placement_manifest": str(Path(args.placement_manifest).expanduser().resolve()) if args.placement_manifest else None,
        "placement_split": args.placement_split if args.placement_manifest else "fixed",
        "eval_placements": [
            {
                "placement_id": case["placement_id"],
                "placement": str(case["placement_path"]),
                "placement_sha256": sha256_file(Path(case["placement_path"])),
                "pose_signature": case.get("pose_signature"),
                "pose_vector": case.get("pose_vector"),
                "seed": case["seed"],
            }
            for case in placement_cases
        ],
        "source_training_placement_signatures": sorted(training_placement_signatures),
        "all_eval_placements_held_out": all_eval_placements_held_out,
        "checkpoint_dir": str(checkpoint_dir),
        "process_logs": {
            "stdout": str(out_dir / "process_stdout.log"),
            "stderr": str(out_dir / "process_stderr.log"),
        },
        "task_binding": binding,
        "camera_adapter": {
            "training_key": "cam_high",
            "runtime_source": args.runtime_camera_source,
            "source_reason": (
                "The native synchronized converter maps RoboTwin head_camera RGB to cam_high."
                if args.runtime_camera_source == "head_camera"
                else "The sparse generated ACT smoke converter stores observer frames under cam_high."
            ),
            "runtime_color_adapter": args.runtime_color_adapter,
            "color_reason": color_adapter_reason(args.runtime_color_adapter),
        },
        "evaluation_scope": {
            "placement_randomization": (
                f"explicit_manifest_split:{args.placement_split}"
                if args.placement_manifest
                else "fixed_action_repair_placement"
            ),
            "domain_randomization": domain_randomization,
            "action_selection": (
                f"execute_full_{args.chunk_size}_action_chunk_before_replan"
                if args.action_execution == "full-chunk"
                else f"execute_first_{args.execution_horizon}_of_{args.chunk_size}_actions_before_replan"
                if args.action_execution == "chunk-prefix"
                else f"receding_horizon_first_action_from_each_{args.chunk_size}_action_chunk"
            ),
            "seed_boundary": (
                f"Seeds are held out from {len(training_seeds)} source collection seeds. "
                + (
                    f"The {len(placement_cases)} explicit evaluation placements are signature-disjoint from passed training placements."
                    if all_eval_placements_held_out
                    else "Placement holdout is not established by signature."
                )
            ),
        },
        "claim_boundary": (
            f"This is a bounded learned-policy evaluation: it loads {args.checkpoint_name}, performs ACT inference from the "
            "generated task observation, sends predicted qpos actions through RoboTwin take_action, and records the "
            "success verifier. Infrastructure completion is separate from policy task success and does not establish "
            "robust policy quality."
        ),
        "episodes": [],
    }
    write_json(report_path, report)
    aggregate_events: list[dict[str, Any]] = []

    previous_cwd = Path.cwd()
    os.chdir(args.robotwin_root)
    sys.path.insert(0, args.robotwin_root)
    try:
        import sapien.core as sapien
        from envs._base_task import Base_Task
        from envs.utils import create_actor, create_sapien_urdf_obj

        policy, stats, model_record = load_policy(args, checkpoint_dir)
        task_class = build_task_class(Base_Task, sapien, create_actor, create_sapien_urdf_obj)
        report["model"] = model_record
        for episode_index, placement_case in enumerate(placement_cases):
            seed = placement_case["seed"]
            placement = read_json(placement_case["placement_path"])
            episode_binding = infer_task_binding(args.task_id, placement)
            episode_dir = out_dir / f"episode_{episode_index:03d}_{placement_case['placement_id']}_seed_{seed}"
            episode, events = run_episode(
                task_class,
                placement,
                episode_binding,
                policy,
                stats,
                args,
                seed,
                training_seeds,
                placement_case,
                training_placement_signatures,
                episode_dir,
            )
            report["episodes"].append(episode)
            aggregate_events.extend(events)
    except Exception as exc:  # noqa: BLE001
        report["top_level_error"] = f"{type(exc).__name__}: {exc}"
        report["top_level_traceback"] = traceback.format_exc()
    finally:
        os.chdir(previous_cwd)

    execution_count = sum(1 for episode in report["episodes"] if episode.get("execution_complete"))
    success_count = sum(1 for episode in report["episodes"] if episode.get("policy_success"))
    episode_count = len(placement_cases)
    infrastructure_pass = execution_count == episode_count and episode_count > 0 and "top_level_error" not in report
    report.update(
        {
            "status": "pass_generated_act_evaluate_execution" if infrastructure_pass else "blocked_generated_act_evaluate_execution",
            "finished_at": utc_now(),
            "episode_count": episode_count,
            "execution_count": execution_count,
            "success_count": success_count,
            "failure_count": episode_count - success_count,
            "policy_success_rate": success_count / episode_count if episode_count else 0.0,
            "policy_result": "task_success_observed" if success_count else "zero_task_success",
            "next_data_requirement": (
                "Held-out placement success was observed; add visual/physics domain randomization and another task before promotion."
                if success_count == episode_count and all_eval_placements_held_out
                else "Task success was observed; expand placement and domain randomization before making a robustness claim."
                if success_count
                else "Inspect full-chunk action traces and failure media, then add synchronized demonstrations or tune training before rerunning held-out seeds."
            ),
        }
    )
    aggregate_events_path = out_dir / "events.jsonl"
    run_state_path = out_dir / "run_state.json"
    report["events"] = str(aggregate_events_path)
    report["run_state"] = str(run_state_path)
    write_json(report_path, report)
    write_jsonl(aggregate_events_path, aggregate_events)
    run_state = {
        "schema_version": "alchedata.pearl_run_state.v0",
        "command": "/evaluate",
        "state": "completed" if infrastructure_pass else "blocked",
        "task_id": args.task_id,
        "held_out_seeds": [case["seed"] for case in placement_cases],
        "all_eval_placements_held_out": all_eval_placements_held_out,
        "evaluate_report": str(report_path),
        "events": str(aggregate_events_path),
        "policy_success_rate": report["policy_success_rate"],
        "next_data_requirement": report["next_data_requirement"],
        "updated_at": utc_now(),
    }
    write_json(run_state_path, run_state)
    print(
        json.dumps(
            {
                "status": report["status"],
                "execution_count": execution_count,
                "success_count": success_count,
                "report": str(report_path),
            },
            ensure_ascii=False,
        )
    )
    return 0 if infrastructure_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
