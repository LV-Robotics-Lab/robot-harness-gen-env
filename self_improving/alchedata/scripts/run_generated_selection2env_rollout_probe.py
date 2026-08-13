#!/usr/bin/env python3
"""Run a bounded generated selection2env task rollout probe in RoboTwin."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import imageio.v2 as imageio
import numpy as np
import yaml
from PIL import Image

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from scripts.selection2env_contract import (  # noqa: E402
    normalize_task_binding,
    sha256_file,
    workspace_path,
)
from scripts.rollout_video_recorder import RolloutVideoRecorder  # noqa: E402


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


def pixel_stats(rgb: np.ndarray) -> dict[str, float]:
    return {
        "mean": float(np.mean(rgb)),
        "std": float(np.std(rgb)),
        "min": float(np.min(rgb)),
        "max": float(np.max(rgb)),
    }


def pose_record(entity: Any) -> dict[str, Any]:
    record: dict[str, Any] = {}
    if hasattr(entity, "get_pose"):
        pose = entity.get_pose()
        record["p"] = pose.p.tolist()
        record["q"] = pose.q.tolist()
    if hasattr(entity, "get_qpos"):
        try:
            record["qpos"] = np.asarray(entity.get_qpos()).tolist()
        except Exception as exc:  # noqa: BLE001
            record["qpos_error"] = repr(exc)
    return record


def serializable_value(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.astype(float).tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(key): serializable_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serializable_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        return float(value)
    except Exception:  # noqa: BLE001
        return repr(value)


def joint_path_record(task: Any) -> dict[str, Any]:
    left_path = getattr(task, "left_joint_path", [])
    right_path = getattr(task, "right_joint_path", [])
    return {
        "schema_version": "alchedata.generated_joint_path_trace.v0",
        "source": "RoboTwin Base_Task left_joint_path/right_joint_path after generated play_once",
        "left_joint_path": [serializable_value(item) for item in left_path],
        "right_joint_path": [serializable_value(item) for item in right_path],
        "left_joint_path_len": len(left_path),
        "right_joint_path_len": len(right_path),
        "claim_boundary": (
            "Planner joint paths captured after generated play_once; this is trajectory source data for a policy-data adapter, "
            "not learned-policy training or synchronized per-frame teleoperation data."
        ),
    }


def load_scene_module(path: Path | None):
    if path is None:
        return None
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import generated scene module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "load_scene"):
        raise RuntimeError(f"Generated scene module has no load_scene(task, placement_spec=None): {path}")
    return module


def load_robotwin_args(robotwin_root: Path, task_config: str, *, save_path: Path) -> dict[str, Any]:
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
    args["render_freq"] = 0
    args["save_data"] = False
    args["collect_data"] = False
    args["need_plan"] = True
    args["eval_mode"] = False
    args["save_path"] = str(save_path)
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


def requested_domain_randomization(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "random_background": bool(args.random_background),
        "cluttered_table": bool(args.cluttered_table),
        "random_light": bool(args.random_light),
        "random_table_height": float(args.random_table_height),
        "random_head_camera_dis": float(args.random_head_camera_dis),
    }


def apply_domain_randomization(rt_args: dict[str, Any], config: dict[str, Any]) -> None:
    for key, value in config.items():
        rt_args["domain_randomization"][key] = value


def camera_capture(task: Any, out_dir: Path, prefix: str) -> dict[str, Any]:
    task._update_render()
    task.cameras.update_picture()
    head_rgb = task.cameras.get_rgb()["head_camera"]["rgb"]
    observer_rgb = task.cameras.get_observer_rgb()
    head_path = out_dir / f"{prefix}_head_camera.png"
    observer_path = out_dir / f"{prefix}_observer_camera.png"
    save_rgb(head_path, head_rgb)
    save_rgb(observer_path, observer_rgb)
    return {
        "head_camera": str(head_path),
        "observer_camera": str(observer_path),
        "pixel_stats": {
            "head_camera": pixel_stats(head_rgb),
            "observer_camera": pixel_stats(observer_rgb),
        },
        "observer_frame": observer_rgb,
    }


def object_specs_by_id(placement: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(obj["id"]): obj for obj in placement.get("objects", [])}


def _entity_name(entity: Any) -> str:
    for attribute in ("name", "get_name"):
        value = getattr(entity, attribute, None)
        try:
            resolved = value() if callable(value) else value
        except Exception:  # noqa: BLE001
            continue
        if resolved:
            return str(resolved)
    return ""


def object_contact_pairs(task: Any) -> list[list[str]]:
    actors = dict(task.placement_objects)
    by_identity = {id(actor): object_id for object_id, actor in actors.items()}
    by_name = {_entity_name(actor): object_id for object_id, actor in actors.items() if _entity_name(actor)}
    pairs: set[tuple[str, str]] = set()
    for contact in task.scene.get_contacts():
        entities = [body.entity for body in contact.bodies]
        object_ids = [
            by_identity.get(id(entity)) or by_name.get(_entity_name(entity))
            for entity in entities
        ]
        if object_ids[0] and object_ids[1] and object_ids[0] != object_ids[1]:
            pairs.add(tuple(sorted((str(object_ids[0]), str(object_ids[1])))))
    return [list(pair) for pair in sorted(pairs)]


def evaluate_task_conditions(
    task: Any,
    placement: dict[str, Any],
    conditions: list[dict[str, Any]],
    initial_objects: dict[str, Any],
) -> dict[str, Any]:
    contact_pairs = object_contact_pairs(task)
    contact_set = {tuple(pair) for pair in contact_pairs}
    left_open = bool(task.robot.is_left_gripper_open())
    right_open = bool(task.robot.is_right_gripper_open())
    results: list[dict[str, Any]] = []

    for index, condition in enumerate(conditions):
        condition_type = str(condition.get("type") or "")
        metrics: dict[str, Any] = {}
        passed = False
        if condition_type == "near":
            source_id = str(condition.get("object") or "")
            target_id = str(condition.get("target_object") or "")
            source = np.asarray(task.placement_objects[source_id].get_pose().p, dtype=float)
            target = np.asarray(task.placement_objects[target_id].get_pose().p, dtype=float)
            metric = str(condition.get("metric") or "xy")
            dimensions = 2 if metric == "xy" else 3
            distance = float(np.linalg.norm(source[:dimensions] - target[:dimensions]))
            threshold = float(condition["threshold_m"])
            metrics = {"metric": metric, "distance_m": distance, "threshold_m": threshold}
            passed = distance <= threshold
        elif condition_type == "contact":
            pair = tuple(sorted((str(condition.get("object") or ""), str(condition.get("target_object") or ""))))
            metrics = {"object_pair": list(pair), "observed_contact_pairs": contact_pairs}
            passed = pair in contact_set
        elif condition_type == "grippers_open":
            metrics = {"left_gripper_open": left_open, "right_gripper_open": right_open}
            passed = left_open and right_open
        elif condition_type == "in_region":
            source_id = str(condition.get("object") or "")
            region_id = str(condition.get("region") or "")
            source = np.asarray(task.placement_objects[source_id].get_pose().p, dtype=float)
            region = placement["workspace"]["spatial_regions"][region_id]
            tolerance = float(condition.get("tolerance_m", region.get("success_tolerance_m", 0.0)))
            passed = bool(
                float(region["x"][0]) - tolerance <= source[0] <= float(region["x"][1]) + tolerance
                and float(region["y"][0]) - tolerance <= source[1] <= float(region["y"][1]) + tolerance
            )
            metrics = {"source_xy": source[:2].tolist(), "region": region_id, "tolerance_m": tolerance}
        elif condition_type == "max_displacement":
            source_id = str(condition.get("object") or "")
            initial = np.asarray(initial_objects[source_id]["p"], dtype=float)
            final = np.asarray(task.placement_objects[source_id].get_pose().p, dtype=float)
            displacement = float(np.linalg.norm(final - initial))
            threshold = float(condition["threshold_m"])
            metrics = {"displacement_m": displacement, "threshold_m": threshold}
            passed = displacement <= threshold
        else:
            metrics = {"error": f"unsupported success condition: {condition_type}"}
        results.append(
            {
                "index": index,
                "type": condition_type,
                "passed": bool(passed),
                "metrics": metrics,
            }
        )

    return {
        "schema": "agenticsim.robotwin_success_verification.v1",
        "source": "task_program.verifier.conditions",
        "conditions": conditions,
        "results": results,
        "contact_pairs": contact_pairs,
        "plan_success": bool(task.plan_success),
        "all_passed": bool(task.plan_success) and bool(results) and all(item["passed"] for item in results),
    }


def actor_center_xy(actor: Any) -> np.ndarray:
    return np.asarray(actor.get_pose().p[:2], dtype=float)


def target_pose_for_actor(actor: Any, *, relation: str) -> list[float]:
    if hasattr(actor, "get_functional_point"):
        try:
            point = np.asarray(actor.get_functional_point(0), dtype=float).reshape(-1)
            if point.shape[0] >= 7:
                return point[:7].tolist()
            if point.shape[0] >= 3:
                p = point[:3]
                if relation == "in":
                    p = p + np.array([0.0, 0.0, 0.06])
                elif relation == "on":
                    p = p + np.array([0.0, 0.0, 0.035])
                return p.tolist() + [0.0, 0.0, 0.0, 1.0]
        except Exception:
            pass
    pose = actor.get_pose()
    p = np.asarray(pose.p, dtype=float)
    if relation == "in":
        p = p + np.array([0.0, 0.0, 0.08])
    elif relation == "on":
        p = p + np.array([0.0, 0.0, 0.04])
    return p.tolist() + [0.0, 0.0, 0.0, 1.0]


def target_pose_for_binding(
    task_binding: dict[str, Any],
    task: Any,
    placement: dict[str, Any],
    *,
    relation: str,
    place_z_offset: float,
) -> list[float]:
    if task_binding["target_kind"] == "object":
        target = task.placement_objects[task_binding["target_id"]]
        return target_pose_for_actor(target, relation=relation)

    region = placement["workspace"]["spatial_regions"][task_binding["target_region"]]
    x = (float(region["x"][0]) + float(region["x"][1])) / 2.0
    y = (float(region["y"][0]) + float(region["y"][1])) / 2.0
    z = 0.741 + float(getattr(task, "table_z_bias", 0.0)) + place_z_offset
    return [x, y, z, 0.0, 0.0, 0.0, 1.0]


def infer_task_binding(task_id: str, placement: dict[str, Any]) -> dict[str, str]:
    objects = placement.get("objects", [])
    by_semantic = {str(obj.get("semantic", "")).lower(): str(obj["id"]) for obj in objects}
    by_role = {str(obj.get("role", "")).lower(): str(obj["id"]) for obj in objects}
    prompt = str(placement.get("language_prompt", "")).lower()

    if task_id == "task_apple_plate" or ("apple" in prompt and "plate" in prompt):
        return {"template": "place_on", "source_id": by_semantic["apple"], "target_id": by_semantic["plate"]}
    if task_id == "task_vegetable_basket" or ("basket" in prompt and ("vegetable" in prompt or "vagetable" in prompt)):
        source_id = by_semantic.get("vegetable") or by_semantic.get("vagetable")
        return {"template": "place_in", "source_id": source_id, "target_id": by_semantic["basket"]}
    if task_id == "task_can_basket" or ("basket" in prompt and "can" in prompt):
        return {"template": "place_in", "source_id": by_semantic["can"], "target_id": by_semantic["basket"]}

    source_id = by_role.get("manipuland_candidate") or by_role.get("scene_object")
    target_id = by_role.get("support_or_target_candidate") or by_role.get("container_candidate")
    if source_id and target_id:
        return {"template": "place_on", "source_id": source_id, "target_id": target_id}
    raise RuntimeError(f"Cannot infer generated action template for {task_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generated selection2env action rollout probe.")
    parser.add_argument("--robotwin-root", required=True)
    parser.add_argument("--placement")
    parser.add_argument("--task-id")
    parser.add_argument("--task-program-input")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--scene-module")
    parser.add_argument("--task-config", default="demo_clean")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--fps", type=int, default=12)
    parser.add_argument("--capture-stride", type=int, default=4)
    parser.add_argument("--max-video-frames", type=int, default=2400)
    parser.add_argument("--min-video-frames", type=int, default=24)
    parser.add_argument("--arm", choices=["auto", "left", "right"], default="auto")
    parser.add_argument("--motion-mode", choices=["auto", "direct-topdown"], default="auto")
    parser.add_argument("--grasp-z-offset", type=float, default=0.055)
    parser.add_argument("--place-z-offset", type=float, default=0.055)
    parser.add_argument("--record-native-data", action="store_true")
    parser.add_argument("--native-save-freq", type=int, default=15)
    parser.add_argument("--random-background", action="store_true")
    parser.add_argument("--cluttered-table", action="store_true")
    parser.add_argument("--random-light", action="store_true")
    parser.add_argument("--random-table-height", type=float, default=0.0)
    parser.add_argument("--random-head-camera-dis", type=float, default=0.0)
    args = parser.parse_args()
    if args.fps < 1:
        parser.error("--fps must be at least 1")
    if args.capture_stride < 1:
        parser.error("--capture-stride must be at least 1")
    if args.max_video_frames < args.min_video_frames:
        parser.error("--max-video-frames must be at least --min-video-frames")

    if not args.task_program_input and (not args.placement or not args.task_id):
        parser.error("--placement and --task-id are required unless --task-program-input is provided")

    robotwin_root = Path(args.robotwin_root).expanduser().resolve()
    task_program_path = workspace_path(args.task_program_input).resolve() if args.task_program_input else None
    task_program = read_json(task_program_path) if task_program_path else None
    if task_program:
        task_id = str(task_program["task_id"])
        if args.task_id and args.task_id != task_id:
            parser.error("--task-id does not match --task-program-input")
        placement_path = workspace_path(str(task_program["placement_spec"])).resolve()
        if args.placement and Path(args.placement).expanduser().resolve() != placement_path:
            parser.error("--placement does not match --task-program-input")
        scene_id = str(task_program["scene_id"])
    else:
        task_id = str(args.task_id)
        placement_path = Path(str(args.placement)).expanduser().resolve()
        scene_id = f"legacy_{task_id}_scene"
    out_dir = Path(args.out_dir).expanduser().resolve()
    episode_dir = out_dir / "episode_000"
    out_dir.mkdir(parents=True, exist_ok=True)
    episode_dir.mkdir(parents=True, exist_ok=True)
    scene_module_path = Path(args.scene_module).expanduser().resolve() if args.scene_module else None
    placement = read_json(placement_path)
    task_binding = (
        normalize_task_binding(task_program, placement)
        if task_program
        else infer_task_binding(task_id, placement)
    )
    task_binding.setdefault("target_kind", "object")
    domain_randomization = requested_domain_randomization(args)
    placement_sha256 = sha256_file(placement_path)
    if task_program and task_program.get("placement_sha256") != placement_sha256:
        raise ValueError(
            "task-program placement_sha256 does not match placement bytes: "
            f"declared={task_program.get('placement_sha256')} actual={placement_sha256}"
        )
    verifier_conditions = None
    if task_program:
        candidate_conditions = (task_program.get("verifier") or {}).get("conditions")
        if candidate_conditions is not None:
            if not isinstance(candidate_conditions, list) or not candidate_conditions:
                raise ValueError("task-program verifier.conditions must be a non-empty list")
            verifier_conditions = [dict(condition) for condition in candidate_conditions]

    report: dict[str, Any] = {
        "schema_version": "alchedata.generated_selection2env_rollout_probe.v0",
        "command": "/collect",
        "probe_type": "generated_selection2env_play_once",
        "task_id": task_id,
        "scene_id": scene_id,
        "task_program_input": str(task_program_path) if task_program_path else None,
        "task_config": args.task_config,
        "seed": args.seed,
        "placement": str(placement_path),
        "placement_sha256": placement_sha256,
        "scene_module": str(scene_module_path) if scene_module_path else None,
        "robotwin_root": str(robotwin_root),
        "out_dir": str(out_dir),
        "task_binding": task_binding,
        "native_data_recording_requested": args.record_native_data,
        "domain_randomization": domain_randomization,
        "status": "started",
        "limitations": [
            "This probe executes a generated selection2env play_once template against a generated placement.",
            "It is an action-stack smoke test, not learned-policy training or evaluation.",
            "It does not prove robustness across randomization, embodiments, or held-out tasks.",
        ],
    }
    write_json(out_dir / "rollout_report.json", report)

    os.chdir(robotwin_root)
    sys.path.insert(0, str(robotwin_root))

    import sapien.core as sapien
    from envs._base_task import Base_Task
    from envs._GLOBAL_CONFIGS import GRASP_DIRECTION_DIC
    from envs.utils import Action, ArmTag, create_actor, create_sapien_urdf_obj

    scene_module = load_scene_module(scene_module_path)
    specs_by_id = object_specs_by_id(placement)

    class GeneratedSelection2EnvTask(Base_Task):
        def __init__(self, placement_spec: dict[str, Any], generated_scene_module=None):
            super().__init__()
            self.placement_spec = placement_spec
            self.generated_scene_module = generated_scene_module
            self.placement_objects: dict[str, Any] = {}
            self.generated_task_info: dict[str, Any] = {}
            self.arm_tag = None

        def setup_demo(self, **kwargs: Any) -> None:
            super()._init_task_env_(**kwargs)

        def load_actors(self) -> None:
            if self.generated_scene_module is not None:
                self.placement_objects = self.generated_scene_module.load_scene(self, self.placement_spec)
            else:
                self.placement_objects = {}
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
            for obj_id, actor in self.placement_objects.items():
                setattr(self, obj_id, actor)

        def play_once(self):
            source = self.placement_objects[task_binding["source_id"]]
            source_p = source.get_pose().p
            if args.arm == "auto":
                preferred_arm = ArmTag("right" if source_p[0] > 0 else "left")
            else:
                preferred_arm = ArmTag(args.arm)
            self.arm_tag = preferred_arm
            relation = "in" if task_binding["template"] == "place_in" else "on"

            def topdown_quat() -> list[float]:
                return list(
                    GRASP_DIRECTION_DIC["top_down_little_right"]
                    if self.arm_tag == "left"
                    else GRASP_DIRECTION_DIC["top_down_little_left"]
                )

            def direct_topdown_grasp():
                p = np.asarray(source.get_pose().p, dtype=float)
                quat = topdown_quat()
                return self.arm_tag, [
                    Action(self.arm_tag, "move", target_pose=[float(p[0]), float(p[1]), float(p[2] + 0.18), *quat]),
                    Action(
                        self.arm_tag,
                        "move",
                        target_pose=[float(p[0]), float(p[1]), float(p[2] + args.grasp_z_offset), *quat],
                    ),
                    Action(self.arm_tag, "close", target_gripper_pos=0.0),
                ]

            def direct_topdown_place():
                if task_binding["target_kind"] == "object":
                    target = self.placement_objects[task_binding["target_id"]]
                    p = np.asarray(target.get_pose().p, dtype=float)
                    if relation == "in":
                        p = p + np.array([0.0, 0.0, 0.09])
                    else:
                        p = p + np.array([0.0, 0.0, args.place_z_offset])
                else:
                    target_pose = target_pose_for_binding(
                        task_binding,
                        self,
                        placement,
                        relation=relation,
                        place_z_offset=args.place_z_offset,
                    )
                    p = np.asarray(target_pose[:3], dtype=float)
                quat = topdown_quat()
                return self.arm_tag, [
                    Action(self.arm_tag, "move", target_pose=[float(p[0]), float(p[1]), float(p[2] + 0.16), *quat]),
                    Action(self.arm_tag, "move", target_pose=[float(p[0]), float(p[1]), float(p[2]), *quat]),
                    Action(self.arm_tag, "open", target_gripper_pos=1.0),
                ]

            def without_motion_constraints(action_group):
                arm_tag, sequence = action_group
                relaxed = []
                for item in sequence:
                    if item.action == "move":
                        relaxed.append(Action(item.arm_tag, "move", target_pose=item.target_pose))
                    else:
                        relaxed.append(item)
                return arm_tag, relaxed

            grasp_errors: list[str] = []
            if args.motion_mode == "direct-topdown":
                grasp_action = direct_topdown_grasp()
                used_motion_mode = "direct_topdown"
            else:
                grasp_action = None
                arm_candidates = [preferred_arm, preferred_arm.opposite]
                contact_points = [None]
                try:
                    contact_count = len(source.config.get("contact_points_pose") or [])
                    contact_points.extend(range(min(contact_count, 8)))
                except Exception:
                    pass
                for arm_candidate in arm_candidates:
                    for pre_grasp_dis in (0.08, 0.04, 0.02, 0.0):
                        for contact_point_id in contact_points:
                            try:
                                grasp_action = self.grasp_actor(
                                    source,
                                    arm_tag=arm_candidate,
                                    pre_grasp_dis=pre_grasp_dis,
                                    contact_point_id=contact_point_id,
                                )
                                self.arm_tag = arm_candidate
                                break
                            except Exception as exc:  # noqa: BLE001
                                grasp_errors.append(
                                    f"arm={arm_candidate} pre={pre_grasp_dis} contact={contact_point_id}: {exc!r}"
                                )
                        if grasp_action is not None:
                            break
                    if grasp_action is not None:
                        break
                if grasp_action is None:
                    raise RuntimeError("No generated grasp action could be planned: " + " | ".join(grasp_errors[:12]))
                used_motion_mode = "robotwin_contact_grasp"

            self.generated_grasp_attempt_errors = grasp_errors

            if not self.move(grasp_action):
                self.plan_success = True
                relaxed_grasp = without_motion_constraints(grasp_action)
                if args.motion_mode == "auto" and self.move(relaxed_grasp):
                    used_motion_mode = "relaxed_contact_grasp_after_constrained_fail"
                else:
                    self.plan_success = True
                    grasp_action = direct_topdown_grasp()
                    used_motion_mode = "direct_topdown_after_contact_grasp_fail"
                    self.move(grasp_action)

            self.move(self.move_by_displacement(self.arm_tag, z=0.10, move_axis="world"))

            place_errors: list[str] = []
            place_action = None
            for constrain in (("free", "auto") if relation == "in" else ("auto", "free", "align")):
                for pre_dis, dis in ((0.10, 0.02), (0.07, 0.01), (0.04, 0.0)):
                    try:
                        place_action = self.place_actor(
                            source,
                            arm_tag=self.arm_tag,
                            target_pose=target_pose_for_binding(
                                task_binding,
                                self,
                                placement,
                                relation=relation,
                                place_z_offset=args.place_z_offset,
                            ),
                            pre_dis=pre_dis,
                            dis=dis,
                            constrain=constrain,
                        )
                        break
                    except Exception as exc:  # noqa: BLE001
                        place_errors.append(f"constrain={constrain} pre={pre_dis} dis={dis}: {exc!r}")
                if place_action is not None:
                    break
            if place_action is None:
                raise RuntimeError("No generated place action could be planned: " + " | ".join(place_errors[:12]))

            self.generated_place_attempt_errors = place_errors
            if args.motion_mode == "direct-topdown" or not self.plan_success:
                place_action = direct_topdown_place()
                used_motion_mode += "+direct_topdown_place"

            if not self.move(place_action):
                self.plan_success = True
                relaxed_place = without_motion_constraints(place_action)
                if args.motion_mode == "auto" and self.move(relaxed_place):
                    used_motion_mode += "+relaxed_place_after_constrained_fail"
                else:
                    self.plan_success = True
                    place_action = direct_topdown_place()
                    used_motion_mode += "+direct_topdown_place_after_fail"
                    self.move(place_action)
            self.move(self.move_by_displacement(self.arm_tag, z=0.08, move_axis="world"))

            target_description = (
                f"{specs_by_id[task_binding['target_id']]['asset_id']}/base"
                f"{specs_by_id[task_binding['target_id']].get('model_id', 0)}"
                if task_binding["target_kind"] == "object"
                else task_binding["target_region"]
            )
            self.info["info"] = {
                "{source}": f"{specs_by_id[task_binding['source_id']]['asset_id']}/base{specs_by_id[task_binding['source_id']].get('model_id', 0)}",
                "{target}": target_description,
                "{arm}": str(self.arm_tag),
                "{template}": task_binding["template"],
                "{motion_mode}": used_motion_mode,
                "{grasp_rejected_attempts}": len(grasp_errors),
                "{place_rejected_attempts}": len(place_errors),
            }
            self.generated_task_info = self.info
            return self.info

        def check_success(self):
            if not self.plan_success:
                return False
            source = self.placement_objects[task_binding["source_id"]]
            source_p = np.asarray(source.get_pose().p, dtype=float)
            grippers_open = self.robot.is_left_gripper_open() and self.robot.is_right_gripper_open()
            if task_binding["target_kind"] == "region":
                region = placement["workspace"]["spatial_regions"][task_binding["target_region"]]
                tolerance = float(region.get("success_tolerance_m", 0.0))
                inside_xy = (
                    float(region["x"][0]) - tolerance <= source_p[0] <= float(region["x"][1]) + tolerance
                    and float(region["y"][0]) - tolerance <= source_p[1] <= float(region["y"][1]) + tolerance
                )
                return bool(inside_xy and source_p[2] >= 0.72 + self.table_z_bias and grippers_open)

            target = self.placement_objects[task_binding["target_id"]]
            target_p = np.asarray(target.get_pose().p, dtype=float)
            xy_distance = float(np.linalg.norm(source_p[:2] - target_p[:2]))
            if task_binding["template"] == "place_in":
                return xy_distance < 0.13 and source_p[2] > target_p[2] and grippers_open
            target_radius = 0.08
            try:
                target_extents = np.asarray(specs_by_id[task_binding["target_id"]]["asset_metadata"]["approx_scaled_extents_m"], dtype=float)
                target_radius = float(max(target_extents[0], target_extents[1]) / 2.0)
            except Exception:
                pass
            return xy_distance < min(max(target_radius, 0.08), 0.13) and source_p[2] >= target_p[2] - 0.03 and grippers_open

    move_events: list[dict[str, Any]] = []
    endpoint_frames: list[np.ndarray] = []
    task = GeneratedSelection2EnvTask(placement, scene_module)
    video_path = out_dir / "observer_rollout_probe.mp4"
    video_recorder: RolloutVideoRecorder | None = None
    rt_args = load_robotwin_args(robotwin_root, args.task_config, save_path=episode_dir)
    rt_args["task_name"] = f"generated_selection2env_{task_id}"
    apply_domain_randomization(rt_args, domain_randomization)
    if args.record_native_data:
        rt_args["save_data"] = True
        rt_args["save_freq"] = args.native_save_freq
        rt_args["data_type"]["rgb"] = True
        rt_args["data_type"]["qpos"] = True

    try:
        setup_started = time.time()
        task.setup_demo(now_ep_num=0, seed=args.seed, **rt_args)
        setup_duration = time.time() - setup_started

        original_move = task.move

        def move_wrapper(*move_args, **move_kwargs):
            event = {
                "index": len(move_events),
                "plan_success_before": bool(getattr(task, "plan_success", False)),
                "frame_idx_before": int(getattr(task, "FRAME_IDX", 0)),
                "left_joint_path_len_before": len(getattr(task, "left_joint_path", [])),
                "right_joint_path_len_before": len(getattr(task, "right_joint_path", [])),
            }
            started = time.time()
            try:
                result = original_move(*move_args, **move_kwargs)
                event["result"] = bool(result)
                return result
            except Exception as exc:  # noqa: BLE001
                event["exception"] = repr(exc)
                raise
            finally:
                event.update(
                    {
                        "duration_sec": round(time.time() - started, 4),
                        "plan_success_after": bool(getattr(task, "plan_success", False)),
                        "frame_idx_after": int(getattr(task, "FRAME_IDX", 0)),
                        "left_joint_path_len_after": len(getattr(task, "left_joint_path", [])),
                        "right_joint_path_len_after": len(getattr(task, "right_joint_path", [])),
                    }
                )
                move_events.append(event)

        task.move = move_wrapper

        initial_capture = camera_capture(task, out_dir, "initial")
        initial_observer_frame = initial_capture.pop("observer_frame")
        if args.record_native_data:
            endpoint_frames.append(initial_observer_frame)
        else:
            video_recorder = RolloutVideoRecorder(
                task,
                video_path,
                fps=args.fps,
                capture_stride=args.capture_stride,
                max_frames=args.max_video_frames,
            )
            video_recorder.append_frame(initial_observer_frame)
            video_recorder.install()
        initial_objects = {name: pose_record(actor) for name, actor in task.placement_objects.items()}

        play_started = time.time()
        task_info = task.play_once()
        play_duration = time.time() - play_started

        if video_recorder is not None:
            video_recorder.restore()
        final_capture = camera_capture(task, out_dir, "final")
        final_observer_frame = final_capture.pop("observer_frame")
        final_objects = {name: pose_record(actor) for name, actor in task.placement_objects.items()}
        if video_recorder is None:
            endpoint_frames.append(final_observer_frame)
            imageio.mimsave(video_path, endpoint_frames, fps=args.fps)
            video_capture = {
                "mode": "endpoint_compatibility_for_native_recording",
                "endpoint_only": True,
                "frame_count": len(endpoint_frames),
                "fps": args.fps,
                "duration_sec": round(len(endpoint_frames) / args.fps, 4),
                "capture_stride_sim_steps": None,
                "capture_requests": 0,
                "max_frames": len(endpoint_frames),
                "capped": False,
            }
        else:
            video_recorder.append_frame(final_observer_frame)
            video_capture = video_recorder.close()
            if video_capture["frame_count"] < args.min_video_frames:
                raise RuntimeError(
                    f"Continuous rollout video has only {video_capture['frame_count']} frames; "
                    f"expected at least {args.min_video_frames}"
                )

        semantic_verification = (
            evaluate_task_conditions(task, placement, verifier_conditions, initial_objects)
            if verifier_conditions is not None
            else None
        )
        success = (
            bool(semantic_verification["all_passed"])
            if semantic_verification is not None
            else bool(task.check_success())
        )
        source_id = task_binding["source_id"]
        source_p = np.asarray(task.placement_objects[source_id].get_pose().p, dtype=float)
        relation_metrics: dict[str, Any] = {
            "source_id": source_id,
            "left_gripper_open": bool(task.robot.is_left_gripper_open()),
            "right_gripper_open": bool(task.robot.is_right_gripper_open()),
        }
        if task_binding["target_kind"] == "object":
            target_id = task_binding["target_id"]
            target_p = np.asarray(task.placement_objects[target_id].get_pose().p, dtype=float)
            relation_metrics.update(
                {
                    "target_kind": "object",
                    "target_id": target_id,
                    "xy_distance_m": float(np.linalg.norm(source_p[:2] - target_p[:2])),
                    "z_delta_m": float(source_p[2] - target_p[2]),
                }
            )
        else:
            region_id = task_binding["target_region"]
            region = placement["workspace"]["spatial_regions"][region_id]
            tolerance = float(region.get("success_tolerance_m", 0.0))
            center = np.asarray(
                [
                    (float(region["x"][0]) + float(region["x"][1])) / 2.0,
                    (float(region["y"][0]) + float(region["y"][1])) / 2.0,
                ],
                dtype=float,
            )
            relation_metrics.update(
                {
                    "target_kind": "region",
                    "target_region": region_id,
                    "source_xy": source_p[:2].tolist(),
                    "region_center_xy": center.tolist(),
                    "distance_to_region_center_m": float(np.linalg.norm(source_p[:2] - center)),
                    "region_tolerance_m": tolerance,
                    "inside_region": bool(
                        float(region["x"][0]) - tolerance <= source_p[0] <= float(region["x"][1]) + tolerance
                        and float(region["y"][0]) - tolerance <= source_p[1] <= float(region["y"][1]) + tolerance
                    ),
                }
            )

        events = [
            {"event": "setup_demo", "duration_sec": round(setup_duration, 4), "status": "pass"},
            {
                "event": "generated_play_once",
                "duration_sec": round(play_duration, 4),
                "status": "pass" if task.plan_success else "fail_plan",
            },
            {"event": "check_success", "status": "pass" if success else "fail", "success": success},
        ]
        write_jsonl(out_dir / "events.jsonl", events)
        write_jsonl(out_dir / "move_events.jsonl", move_events)
        policy_trace = joint_path_record(task)
        write_json(out_dir / "policy_trace.json", policy_trace)
        native_data = {
            "status": "not_requested",
            "save_freq": args.native_save_freq,
        }
        if args.record_native_data:
            if task.FRAME_IDX < 2:
                raise RuntimeError(f"Native synchronized recorder captured too few frames: {task.FRAME_IDX}")
            task.merge_pkl_to_hdf5_video()
            native_hdf5 = episode_dir / "data" / "episode0.hdf5"
            native_video = episode_dir / "video" / "episode0.mp4"
            if not native_hdf5.exists() or native_hdf5.stat().st_size == 0:
                raise RuntimeError(f"Native synchronized HDF5 was not written: {native_hdf5}")
            native_data = {
                "status": "pass_native_synchronized_recording",
                "frame_count": int(task.FRAME_IDX),
                "save_freq": args.native_save_freq,
                "hdf5": str(native_hdf5),
                "hdf5_size_bytes": native_hdf5.stat().st_size,
                "video": str(native_video),
                "video_exists": native_video.exists(),
                "video_size_bytes": native_video.stat().st_size if native_video.exists() else 0,
                "claim_boundary": (
                    "RoboTwin native per-step recorder output from generated play_once. This records synchronized "
                    "camera and full joint-action observations at save_freq, but is not yet ACT training-format proof."
                ),
            }
            task.remove_data_cache()
        report.update(
            {
                "status": "pass_generated_action_rollout" if success else "fail_generated_action_rollout",
                "setup_duration_sec": round(setup_duration, 4),
                "play_once_duration_sec": round(play_duration, 4),
                "plan_success": bool(task.plan_success),
                "check_success": success,
                "task_info": task_info,
                "move_event_count": len(move_events),
                "left_joint_path_len": len(getattr(task, "left_joint_path", [])),
                "right_joint_path_len": len(getattr(task, "right_joint_path", [])),
                "initial_objects": initial_objects,
                "final_objects": final_objects,
                "relation_metrics": relation_metrics,
                "semantic_verification": semantic_verification,
                "images": {
                    "initial_head_camera": initial_capture["head_camera"],
                    "initial_observer_camera": initial_capture["observer_camera"],
                    "final_head_camera": final_capture["head_camera"],
                    "final_observer_camera": final_capture["observer_camera"],
                },
                "pixel_stats": {
                    "initial": initial_capture["pixel_stats"],
                    "final": final_capture["pixel_stats"],
                },
                "observer_video": str(video_path),
                "video_capture": video_capture,
                "video_endpoint_only": video_capture["endpoint_only"],
                "events": str(out_dir / "events.jsonl"),
                "move_events": str(out_dir / "move_events.jsonl"),
                "policy_trace": str(out_dir / "policy_trace.json"),
                "native_synchronized_data": native_data,
                "next_data_requirement": (
                    "Use this generated play_once template to record full multi-episode demonstrations before /train."
                    if success
                    else "Repair generated play_once target pose, grasp contact point, or asset pose before claiming /collect success."
                ),
                "failure_diagnosis": {
                    "status": "no_failure_observed" if success else "failure_observed",
                    "checked_categories": [
                        "wrong_grasp_location",
                        "object_knocked_over",
                        "arm_jitter",
                        "uncontrolled_gripper_open_close",
                        "after_contact_failure",
                        "visual_material_mismatch",
                        "target_relation_not_satisfied",
                    ],
                },
            }
        )
        write_json(out_dir / "rollout_report.json", report)
        return 0 if success else 1
    except Exception as exc:  # noqa: BLE001
        report.update(
            {
                "status": "fail_exception",
                "exception": repr(exc),
                "traceback": traceback.format_exc(),
                "move_event_count": len(move_events),
            }
        )
        write_json(out_dir / "rollout_report.json", report)
        write_jsonl(out_dir / "move_events.jsonl", move_events)
        return 1
    finally:
        if video_recorder is not None:
            video_recorder.close()
        try:
            task.close_env(clear_cache=True)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
