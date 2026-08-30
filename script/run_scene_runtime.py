#!/usr/bin/env python3
"""Run a ResolvedSceneSpec in RoboTwin/SAPIEN and emit physical evidence."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

import imageio.v2 as imageio
import numpy as np
import yaml
from PIL import Image

from scene_gen.envs.generated_scene import load_resolved_scene
from scene_gen.runtime_sampling import video_sample_steps
from scene_gen.schema import RelationType, ResolvedSceneSpec
from scene_gen.support_geometry import (
    footprint_2d,
    support_footprint_margin,
    support_surface_dimensions,
    support_surface_shape,
)
from scene_gen.validator import validate_resolved_scene
from self_improving.harness.runtime_capability import (
    RUNTIME_ARTIFACT_PATHS,
    RUNTIME_EVIDENCE_ARTIFACT_PATHS,
    RUNTIME_EVIDENCE_SCHEMA,
    RUNTIME_MEDIA_ARTIFACT_PATHS,
    canonical_capability_bytes,
    describe_runtime_capability,
    runtime_capability_sha256,
)
from self_improving.harness.runtime_events import (
    RUNTIME_EVENT_SCHEMA,
    RuntimeEventCodec,
    RuntimeEventEmitter,
    RuntimeEventKind,
)


def describe_runtime_capabilities(
    *,
    robotwin_root: Path,
    task_config: str,
) -> dict[str, Any]:
    return describe_runtime_capability(
        robotwin_root=robotwin_root,
        task_config=task_config,
        runner_path=Path(__file__).resolve(),
    )


def _write_canonical_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_capability_bytes(value))


def _runtime_event_emitter(
    *,
    event_fd: int | None,
    event_protocol: str,
) -> RuntimeEventEmitter | None:
    if event_protocol != RUNTIME_EVENT_SCHEMA:
        raise ValueError(f"unsupported runtime event protocol {event_protocol!r}")
    if event_fd is None:
        return None
    try:
        os.fstat(event_fd)
    except OSError as error:
        raise ValueError("runtime event file descriptor is not open") from error
    return RuntimeEventEmitter(
        fd=event_fd,
        codec=RuntimeEventCodec(allowed_artifact_paths=RUNTIME_ARTIFACT_PATHS),
    )


def _emit_runtime_event(
    emitter: RuntimeEventEmitter | None,
    *,
    kind: RuntimeEventKind,
    completed_steps: int | None = None,
    artifact_paths: Sequence[str] = (),
) -> None:
    if emitter is not None:
        emitter.emit(
            kind=kind,
            completed_steps=completed_steps,
            artifact_paths=artifact_paths,
        )


def _prepare_output_root(root: Path) -> None:
    if root.is_symlink():
        raise RuntimeError("runtime output root must not be a symlink")
    if root.exists():
        if not root.is_dir() or any(root.iterdir()):
            raise RuntimeError("runtime output root must be an empty directory")
        return
    root.mkdir(parents=True)


def _require_exact_artifacts(root: Path, paths: Sequence[str]) -> None:
    expected = set(paths)
    actual: set[str] = set()
    for artifact in root.iterdir():
        if artifact.is_symlink() or not artifact.is_file() or artifact.stat().st_size < 1:
            raise RuntimeError("runtime output root contains a non-regular artifact")
        actual.add(artifact.name)
    if actual != expected:
        raise RuntimeError(
            f"runtime output artifacts do not match the allowlist: {sorted(actual ^ expected)!r}"
        )


def _close_runtime_task(task: Any | None) -> None:
    if task is not None:
        task.close_env(clear_cache=True)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def save_rgb(path: Path, value: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(value.astype(np.uint8)).save(path)


def synchronize_video_timeline(
    frames: list[np.ndarray],
    sample_step_indices: tuple[int, ...],
    *,
    base_simulation_step_count: int,
    settle_extra_steps: int,
    capture_final_frame: Callable[[], np.ndarray],
) -> tuple[list[np.ndarray], tuple[int, ...]]:
    """Bind the final video slot to the actual adaptive-settle endpoint."""

    if settle_extra_steps <= 0 or not frames:
        return frames, sample_step_indices
    synchronized_frames = [*frames]
    synchronized_frames[-1] = np.asarray(capture_final_frame()).copy()
    actual_final_step = base_simulation_step_count + settle_extra_steps - 1
    return synchronized_frames, (*sample_step_indices[:-1], actual_final_step)


def runtime_timeline_evidence(
    *,
    base_simulation_step_count: int,
    settle_extra_steps: int,
    video_sample_step_indices: tuple[int, ...],
) -> dict[str, Any]:
    """Return the step-count fields that bind evidence to its real timeline."""

    return {
        "base_simulation_step_count": base_simulation_step_count,
        "simulation_step_count": base_simulation_step_count + settle_extra_steps,
        "video_sample_step_indices": list(video_sample_step_indices),
    }


def _robotwin_task_config_path(
    robotwin_root: Path,
    task_config: str,
) -> Path:
    candidates = (
        robotwin_root / "task_config" / f"{task_config}.yml",
        robotwin_root / "env_cfg" / "task_config" / f"{task_config}.yml",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate

    searched = ", ".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(
        f"RoboTwin task config {task_config!r} was not found; searched: {searched}"
    )


def load_robotwin_args(robotwin_root: Path, task_config: str) -> dict[str, Any]:
    from envs._GLOBAL_CONFIGS import CONFIGS_PATH

    task_config_path = _robotwin_task_config_path(robotwin_root, task_config)
    with task_config_path.open("r", encoding="utf-8") as stream:
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


def object_bottom_z(item: Any, position_m: list[float]) -> float:
    if item.z_policy == "origin_on_table":
        return float(position_m[2])
    return float(position_m[2]) - item.dimensions_m[2] / 2.0


def runtime_support_margin(item: Any, target: Any, positions: dict[str, list[float]]) -> float:
    target_dimensions = support_surface_dimensions(
        target.dimensions_m,
        target.support_surface_dimensions_m,
    )
    target_shape = support_surface_shape(
        target.footprint_shape,
        target.support_surface_shape,
    )
    source_position = positions[item.object_id]
    target_position = positions[target.object_id]
    return support_footprint_margin(
        source_dimensions_m=item.dimensions_m,
        source_yaw=item.pose.yaw_rad,
        source_shape=item.footprint_shape,
        target_surface_dimensions_m=target_dimensions,
        target_yaw=target.pose.yaw_rad,
        target_surface_shape=target_shape,
        dx=source_position[0] - target_position[0],
        dy=source_position[1] - target_position[1],
    )


def runtime_inside_contained(
    item: Any,
    target: Any,
    positions: dict[str, list[float]],
    tolerance_m: float = 0.005,
) -> bool:
    if target.interior_dimensions_m is None:
        return False
    source_position = positions[item.object_id]
    target_position = positions[target.object_id]
    dx = source_position[0] - target_position[0]
    dy = source_position[1] - target_position[1]
    cosine = math.cos(target.pose.yaw_rad)
    sine = math.sin(target.pose.yaw_rad)
    local_x = cosine * dx + sine * dy
    local_y = -sine * dx + cosine * dy
    source_footprint = footprint_2d(
        item.dimensions_m,
        item.pose.yaw_rad - target.pose.yaw_rad,
        item.footprint_shape,
    )
    interior_width, interior_depth, interior_height = target.interior_dimensions_m
    horizontal = (
        abs(local_x) + source_footprint.half_x <= interior_width / 2.0 + tolerance_m
        and abs(local_y) + source_footprint.half_y <= interior_depth / 2.0 + tolerance_m
    )
    source_bottom = object_bottom_z(item, source_position)
    target_bottom = object_bottom_z(target, target_position) + (
        target.interior_floor_z_offset_m or 0.0
    )
    vertical = (
        source_bottom >= target_bottom - tolerance_m
        and source_bottom + item.dimensions_m[2] <= target_bottom + interior_height + tolerance_m
    )
    return horizontal and vertical


def runtime_relation_results(
    resolved: ResolvedSceneSpec,
    positions: dict[str, list[float]],
) -> dict[str, dict[str, Any]]:
    by_id = {item.object_id: item for item in resolved.objects}
    results: dict[str, dict[str, Any]] = {}
    gap = 0.015
    for relation in resolved.relations:
        if relation.target == "table" or relation.relation in {
            RelationType.ON_TOP_OF,
            RelationType.INSIDE,
        }:
            continue
        source = by_id[relation.source]
        target = by_id[relation.target]
        source_position = positions[source.object_id]
        target_position = positions[target.object_id]
        source_footprint = footprint_2d(
            source.dimensions_m,
            source.pose.yaw_rad,
            source.footprint_shape,
        )
        target_footprint = footprint_2d(
            target.dimensions_m,
            target.pose.yaw_rad,
            target.footprint_shape,
        )
        dx = source_position[0] - target_position[0]
        dy = source_position[1] - target_position[1]
        distance = math.hypot(dx, dy)
        if relation.relation == RelationType.LEFT_OF:
            passed = (
                source_position[0] + source_footprint.half_x + gap
                <= target_position[0] - target_footprint.half_x
            )
        elif relation.relation == RelationType.RIGHT_OF:
            passed = (
                source_position[0] - source_footprint.half_x - gap
                >= target_position[0] + target_footprint.half_x
            )
        elif relation.relation == RelationType.FRONT_OF:
            passed = (
                source_position[1] - source_footprint.half_y - gap
                >= target_position[1] + target_footprint.half_y
            )
        elif relation.relation == RelationType.BEHIND:
            passed = (
                source_position[1] + source_footprint.half_y + gap
                <= target_position[1] - target_footprint.half_y
            )
        elif relation.relation == RelationType.NEAR:
            passed = distance <= (relation.max_distance_m or 0.25)
        elif relation.relation == RelationType.DISTANCE_AT_LEAST:
            passed = distance >= (relation.min_distance_m or 0.0)
        else:
            continue
        key = f"{relation.relation.value}:{relation.source}:{relation.target}"
        results[key] = {
            "relation": relation.relation.value,
            "source": relation.source,
            "target": relation.target,
            "pass": bool(passed),
            "center_delta_m": [dx, dy],
            "center_distance_m": distance,
            "source_half_extent_m": [
                source_footprint.half_x,
                source_footprint.half_y,
            ],
            "target_half_extent_m": [
                target_footprint.half_x,
                target_footprint.half_y,
            ],
        }
    return results


def entity_id(actor: Any) -> int | None:
    wrapped = getattr(actor, "actor", None)
    candidates = (
        actor,
        wrapped,
        getattr(actor, "entity", None),
        getattr(wrapped, "entity", None),
    )
    for candidate in candidates:
        if candidate is None:
            continue
        for name in ("per_scene_id", "id"):
            value = getattr(candidate, name, None)
            if isinstance(value, (int, np.integer)):
                return int(value)
        getter = getattr(candidate, "get_per_scene_id", None)
        if callable(getter):
            return int(getter())
    return None


def entity_ids(actor: Any) -> list[int]:
    candidates = [actor, getattr(actor, "actor", None)]
    articulation = getattr(actor, "actor", None)
    link_getter = getattr(articulation, "get_links", None)
    if callable(link_getter):
        candidates.extend(link_getter())
    identifiers: set[int] = set()
    for candidate in candidates:
        if candidate is None:
            continue
        identifier = entity_id(candidate)
        if identifier is not None:
            identifiers.add(identifier)
    return sorted(identifiers)


def actor_qpos(actor: Any) -> list[float]:
    getter = getattr(actor, "get_qpos", None)
    if not callable(getter):
        return []
    return np.asarray(getter(), dtype=float).reshape(-1).tolist()


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


def contact_point_metrics(contact: Any, active_separation_m: float = 0.001) -> dict[str, Any]:
    points = list(getattr(contact, "points", []) or [])
    separations = [
        float(value)
        for point in points
        if isinstance((value := getattr(point, "separation", None)), (int, float))
    ]
    impulses = []
    for point in points:
        impulse = getattr(point, "impulse", None)
        if impulse is not None:
            impulses.append(float(np.linalg.norm(np.asarray(impulse, dtype=float))))
    active_points = sum(value <= active_separation_m for value in separations)
    return {
        "point_count": len(points),
        "active_point_count": active_points,
        "min_separation_m": min(separations) if separations else None,
        "max_impulse_norm": max(impulses) if impulses else None,
        "active": active_points > 0,
    }


def summarize_contacts(
    contacts: list[Any],
    generated_names: set[str],
    expected_support_targets: dict[str, str],
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    penetration_by_object = {name: 0 for name in generated_names}
    support_by_object = {name: False for name in generated_names}
    observed_support_targets: dict[str, str | None] = {name: None for name in generated_names}
    unexpected_targets_by_object: dict[str, set[str]] = {name: set() for name in generated_names}
    robot_collision_pairs: set[tuple[str, str]] = set()
    for contact in contacts:
        first, second = contact_pair(contact)
        if first not in generated_names and second not in generated_names:
            continue
        penetrations = contact_penetration_count(contact)
        metrics = contact_point_metrics(contact)
        records.append(
            {
                "bodies": [first, second],
                "penetration_points": penetrations,
                **metrics,
            }
        )
        for generated, other in ((first, second), (second, first)):
            if generated not in generated_names:
                continue
            if not metrics["active"]:
                continue
            other_lower = other.lower()
            if other in generated_names:
                if expected_support_targets.get(generated) == other:
                    support_by_object[generated] = True
                    observed_support_targets[generated] = other
                elif expected_support_targets.get(other) == generated:
                    continue
                else:
                    penetration_by_object[generated] += penetrations
                    unexpected_targets_by_object[generated].add(other)
            elif "table" in other_lower:
                if expected_support_targets.get(generated) == "table":
                    support_by_object[generated] = True
                    observed_support_targets[generated] = "table"
                else:
                    unexpected_targets_by_object[generated].add("table")
            elif any(token in other_lower for token in ("ground", "wall")):
                unexpected_targets_by_object[generated].add(other)
            else:
                robot_collision_pairs.add((generated, other))
    return {
        "records": records,
        "penetration_by_object": penetration_by_object,
        "support_by_object": support_by_object,
        "observed_support_targets": observed_support_targets,
        "unexpected_targets_by_object": {
            name: sorted(targets) for name, targets in unexpected_targets_by_object.items()
        },
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


def camera_rgb(camera: Any) -> np.ndarray:
    camera.take_picture()
    rgba = camera.get_picture("Color")
    return (rgba * 255).clip(0, 255).astype("uint8")[:, :, :3]


def segmentation_preview(labels: np.ndarray) -> np.ndarray:
    red = (labels * 53) % 255
    green = (labels * 97) % 255
    blue = (labels * 193) % 255
    return np.stack([red, green, blue], axis=-1).astype(np.uint8)


def _nonnegative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a nonnegative integer")
    return parsed


def _positive_int(value: str) -> int:
    parsed = _nonnegative_int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _task_config_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value) or ".." in value:
        raise argparse.ArgumentTypeError("must be a canonical RoboTwin task config name")
    return value


def _sha256_argument(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise argparse.ArgumentTypeError("must be a lowercase 64-character SHA-256")
    return value


def main(
    argv: Sequence[str] | None = None,
    *,
    base_task_class: type[Any] | None = None,
) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--robotwin-root", required=True)
    parser.add_argument("--resolved-scene")
    parser.add_argument("--asset-catalog")
    parser.add_argument("--out-dir")
    parser.add_argument("--task-config", type=_task_config_name, default="demo_clean")
    parser.add_argument("--describe-capabilities")
    parser.add_argument("--settle-steps", type=_positive_int, default=900)
    parser.add_argument(
        "--settle-converge-max",
        type=_nonnegative_int,
        default=0,
        help="extra steps the settle phase may take, in blocks, while any "
        "object still reads as moving. 0 (default) keeps the fixed horizon "
        "and reproduces previous runs exactly.",
    )
    parser.add_argument("--precheck-steps", type=_nonnegative_int, default=0)
    parser.add_argument("--video-frames", type=_nonnegative_int, default=120)
    parser.add_argument("--fps", type=_positive_int, default=12)
    parser.add_argument("--min-visible-pixels", type=_nonnegative_int, default=64)
    parser.add_argument("--contact-window-steps", type=_positive_int, default=60)
    parser.add_argument("--checkpoint-steps", type=_positive_int, default=120)
    parser.add_argument("--event-fd", type=int)
    parser.add_argument("--expected-capability-sha256", type=_sha256_argument)
    parser.add_argument(
        "--event-protocol",
        choices=(RUNTIME_EVENT_SCHEMA,),
        default=RUNTIME_EVENT_SCHEMA,
    )
    parser.add_argument(
        "--evidence-only",
        action="store_true",
        help="return success after complete evidence acquisition even when validation gates fail",
    )
    args = parser.parse_args(argv)

    robotwin_root = Path(args.robotwin_root).expanduser().resolve()
    if args.describe_capabilities:
        capability = describe_runtime_capabilities(
            robotwin_root=robotwin_root,
            task_config=args.task_config,
        )
        _write_canonical_json(
            Path(args.describe_capabilities).expanduser().resolve(),
            capability,
        )
        return 0
    if args.event_fd is not None and args.expected_capability_sha256 is None:
        parser.error("--expected-capability-sha256 is required with --event-fd")
    if args.expected_capability_sha256 is not None:
        current_capability = describe_runtime_capabilities(
            robotwin_root=robotwin_root,
            task_config=args.task_config,
        )
        current_capability_sha256 = runtime_capability_sha256(current_capability)
        if not hmac.compare_digest(
            current_capability_sha256,
            args.expected_capability_sha256,
        ):
            raise RuntimeError("runtime capability digest does not match the described worker")
    if not args.resolved_scene:
        parser.error("--resolved-scene is required unless --describe-capabilities is used")
    if not args.out_dir:
        parser.error("--out-dir is required unless --describe-capabilities is used")
    resolved_path = Path(args.resolved_scene).expanduser().resolve()
    catalog_path = Path(args.asset_catalog).expanduser().resolve() if args.asset_catalog else None
    # Preserve the final path component so `_prepare_output_root` can reject a
    # symlink instead of silently following it to a different output tree.
    out_dir = Path(args.out_dir).expanduser().absolute()
    _prepare_output_root(out_dir)
    resolved = ResolvedSceneSpec.model_validate_json(resolved_path.read_text(encoding="utf-8"))
    event_emitter = _runtime_event_emitter(
        event_fd=args.event_fd,
        event_protocol=args.event_protocol,
    )

    os.chdir(robotwin_root)
    sys.path.insert(0, str(robotwin_root))
    if base_task_class is None:
        from envs._base_task import Base_Task
    else:
        Base_Task = base_task_class

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
            return True, []

        def play_once(self):
            raise NotImplementedError("environment generation does not define a task policy")

        def check_success(self):
            raise NotImplementedError("environment generation does not define task success")

    report: dict[str, Any] = {
        "schema_version": RUNTIME_EVIDENCE_SCHEMA,
        "scene_id": resolved.scene_id,
        "resolved_scene_sha256": resolved.digest(),
        "seed": resolved.seed,
        "status": "started",
    }
    task: GeneratedSceneRuntime | None = None
    try:
        runtime_args = load_robotwin_args(robotwin_root, args.task_config)
        runtime_args["save_path"] = str(out_dir)
        catalog = None
        if catalog_path:
            from scene_gen.catalog import load_catalog

            catalog = load_catalog(catalog_path)
        _emit_runtime_event(
            event_emitter,
            kind=RuntimeEventKind.PREFLIGHT_COMPLETED,
        )
        task = GeneratedSceneRuntime(resolved)
        task.setup_demo(now_ep_num=0, seed=resolved.seed, **runtime_args)
        _emit_runtime_event(
            event_emitter,
            kind=RuntimeEventKind.SCENE_LOADED,
        )
        completed_steps = 0
        _emit_runtime_event(
            event_emitter,
            kind=RuntimeEventKind.SIMULATION_STARTED,
        )
        for _ in range(args.precheck_steps):
            task.scene.step()
            completed_steps += 1
            if completed_steps % args.checkpoint_steps == 0:
                _emit_runtime_event(
                    event_emitter,
                    kind=RuntimeEventKind.SIMULATION_CHECKPOINT,
                    completed_steps=completed_steps,
                )
        generated_names = set(task.generated_objects)
        expected_support_targets = {
            item.object_id: item.support_target for item in resolved.objects
        }
        initial_contacts = summarize_contacts(
            list(task.scene.get_contacts()), generated_names, expected_support_targets
        )
        initial = {
            name: {
                "position_m": actor.get_pose().p.tolist(),
                "orientation_wxyz": actor.get_pose().q.tolist(),
                "qpos": actor_qpos(actor),
            }
            for name, actor in task.generated_objects.items()
        }
        frames: list[np.ndarray] = []
        prior_window: dict[str, dict[str, list[float]]] | None = None
        total_steps = max(args.settle_steps, args.video_frames)
        video_steps = video_sample_steps(total_steps, args.video_frames)
        video_step_set = set(video_steps)
        contact_window_steps = min(max(1, args.contact_window_steps), total_steps)
        support_contact_hits = {name: 0 for name in generated_names}
        unexpected_contact_hits = {name: 0 for name in generated_names}
        unexpected_contact_targets = {name: set() for name in generated_names}
        support_contact_samples = 0
        for index in range(total_steps):
            task.scene.step()
            completed_steps += 1
            if completed_steps % args.checkpoint_steps == 0:
                _emit_runtime_event(
                    event_emitter,
                    kind=RuntimeEventKind.SIMULATION_CHECKPOINT,
                    completed_steps=completed_steps,
                )
            if index == max(0, total_steps - 30):
                prior_window = {
                    name: {
                        "position_m": actor.get_pose().p.tolist(),
                        "orientation_wxyz": actor.get_pose().q.tolist(),
                        "qpos": actor_qpos(actor),
                    }
                    for name, actor in task.generated_objects.items()
                }
            if index in video_step_set:
                task.scene.update_render()
                frames.append(task.cameras.get_observer_rgb())
            if index >= total_steps - contact_window_steps:
                contact_sample = summarize_contacts(
                    list(task.scene.get_contacts()),
                    generated_names,
                    expected_support_targets,
                )
                support_contact_samples += 1
                for name in generated_names:
                    if contact_sample["support_by_object"][name]:
                        support_contact_hits[name] += 1
                    targets = contact_sample["unexpected_targets_by_object"][name]
                    if targets:
                        unexpected_contact_hits[name] += 1
                        unexpected_contact_targets[name].update(targets)

        def _snapshot():
            return {
                name: {
                    "position_m": actor.get_pose().p.tolist(),
                    "orientation_wxyz": actor.get_pose().q.tolist(),
                    "qpos": actor_qpos(actor),
                }
                for name, actor in task.generated_objects.items()
            }

        def _any_moving(window, now):
            for name in task.generated_objects:
                a, b = window[name], now[name]
                trans = float(
                    np.linalg.norm(np.asarray(b["position_m"]) - np.asarray(a["position_m"]))
                )
                rot = quaternion_angle_deg(b["orientation_wxyz"], a["orientation_wxyz"])
                if trans > 0.001 or rot > 0.5:
                    return True
            return False

        final = _snapshot()

        # ---- adaptive settle extension (opt-in; default keeps old behaviour) ----
        # A fixed horizon judges "is it moving" at one arbitrary instant, so an
        # object that is merely SLOW to settle is condemned alongside one that
        # never settles. Measured 2026-08-20, same scenes at 900 vs 2700 steps:
        #   rescued  321_dustbin 0.586 -> 0.461 deg, 035_apple 99.00 -> 0.000
        #   still caught  002_bowl 144.5 -> 37.9, 021_cup 168.3 -> 4.70,
        #                 304_bottle 2.30 -> 6.62 (a limit cycle: it got worse)
        # so a longer horizon separates "slow" from "never" -- which no
        # threshold on the 900-step numbers can, because the two classes overlap
        # on rotation (rotation-only failures span 0.54-1.71 deg, genuinely
        # moving ones start at 1.07). Translation is what separates cleanly
        # (<=0.00092 vs >=0.00232 m), and it is left untouched here.
        settle_extra_steps = 0
        if args.settle_converge_max > 0:
            block = max(30, contact_window_steps)
            while settle_extra_steps < args.settle_converge_max and _any_moving(
                prior_window or initial, final
            ):
                step_count = min(block, args.settle_converge_max - settle_extra_steps)
                # contact stats describe the FINAL window, so they are re-counted
                # over each extension rather than carried over from the old one.
                support_contact_hits = {name: 0 for name in generated_names}
                unexpected_contact_hits = {name: 0 for name in generated_names}
                unexpected_contact_targets = {name: set() for name in generated_names}
                support_contact_samples = 0
                for extra_index in range(step_count):
                    task.scene.step()
                    completed_steps += 1
                    if completed_steps % args.checkpoint_steps == 0:
                        _emit_runtime_event(
                            event_emitter,
                            kind=RuntimeEventKind.SIMULATION_CHECKPOINT,
                            completed_steps=completed_steps,
                        )
                    if extra_index == max(0, step_count - 30):
                        prior_window = _snapshot()
                    if extra_index >= step_count - contact_window_steps:
                        contact_sample = summarize_contacts(
                            list(task.scene.get_contacts()),
                            generated_names,
                            expected_support_targets,
                        )
                        support_contact_samples += 1
                        for name in generated_names:
                            if contact_sample["support_by_object"][name]:
                                support_contact_hits[name] += 1
                            targets = contact_sample["unexpected_targets_by_object"][name]
                            if targets:
                                unexpected_contact_hits[name] += 1
                                unexpected_contact_targets[name].update(targets)
                settle_extra_steps += step_count
                final = _snapshot()

        _emit_runtime_event(
            event_emitter,
            kind=RuntimeEventKind.SIMULATION_COMPLETED,
            completed_steps=completed_steps,
        )

        def _capture_final_observer_frame() -> np.ndarray:
            task.scene.update_render()
            return task.cameras.get_observer_rgb()

        frames, video_steps = synchronize_video_timeline(
            frames,
            video_steps,
            base_simulation_step_count=total_steps,
            settle_extra_steps=settle_extra_steps,
            capture_final_frame=_capture_final_observer_frame,
        )
        head_rgb, actor_labels = head_camera_arrays(task)
        save_rgb(out_dir / "preview_head.png", head_rgb)
        save_rgb(out_dir / "preview_segmentation.png", segmentation_preview(actor_labels))
        world_left = camera_rgb(task.cameras.world_camera1)
        world_right = camera_rgb(task.cameras.world_camera2)
        save_rgb(out_dir / "preview_world_left.png", world_left)
        save_rgb(out_dir / "preview_world_right.png", world_right)
        if frames:
            save_rgb(out_dir / "observer_start.png", frames[0])
            save_rgb(out_dir / "observer_mid.png", frames[len(frames) // 2])
            save_rgb(out_dir / "observer_end.png", frames[-1])
            imageio.mimsave(
                out_dir / "observer_runtime.mp4",
                frames,
                fps=args.fps,
                output_params=["-movflags", "+faststart"],
            )
        media_artifact_paths = (
            RUNTIME_MEDIA_ARTIFACT_PATHS
            if frames
            else (
                "preview_head.png",
                "preview_segmentation.png",
                "preview_world_left.png",
                "preview_world_right.png",
            )
        )
        _require_exact_artifacts(out_dir, media_artifact_paths)
        _emit_runtime_event(
            event_emitter,
            kind=RuntimeEventKind.MEDIA_COMPLETED,
            artifact_paths=media_artifact_paths,
        )
        unique_video_frame_count = len(
            {hashlib.sha256(frame.tobytes()).digest() for frame in frames}
        )

        final_contacts = summarize_contacts(
            list(task.scene.get_contacts()), generated_names, expected_support_targets
        )

        objects: dict[str, Any] = {}
        by_id = {item.object_id: item for item in resolved.objects}
        final_positions = {name: value["position_m"] for name, value in final.items()}
        for name, actor in task.generated_objects.items():
            before = initial[name]
            after = final[name]
            late = (prior_window or initial)[name]
            identifiers = entity_ids(actor)
            visible_pixels = (
                int(np.count_nonzero(np.isin(actor_labels, identifiers))) if identifiers else 0
            )
            late_translation = float(
                np.linalg.norm(np.asarray(after["position_m"]) - np.asarray(late["position_m"]))
            )
            late_rotation = quaternion_angle_deg(
                after["orientation_wxyz"], late["orientation_wxyz"]
            )
            dropped = after["position_m"][2] < resolved.workspace.table_height_m - 0.03
            contact_fraction = support_contact_hits[name] / max(1, support_contact_samples)
            unexpected_contact_fraction = unexpected_contact_hits[name] / max(
                1, support_contact_samples
            )
            raw_support_contact = (
                final_contacts["support_by_object"][name] or support_contact_hits[name] > 0
            )
            observed_support_target = final_contacts["observed_support_targets"][name]
            if observed_support_target is None and support_contact_hits[name] > 0:
                observed_support_target = expected_support_targets[name]
            support_mode = (
                "fixed_static_pose"
                if by_id[name].is_static and by_id[name].support_relation == RelationType.ON_TABLE
                else f"{by_id[name].support_relation.value}_contact"
                if raw_support_contact
                else "none"
            )
            support_margin = None
            inside_contained = None
            if by_id[name].support_relation == RelationType.ON_TOP_OF:
                support_margin = runtime_support_margin(
                    by_id[name],
                    by_id[by_id[name].support_target],
                    final_positions,
                )
            elif by_id[name].support_relation == RelationType.INSIDE:
                inside_contained = runtime_inside_contained(
                    by_id[name],
                    by_id[by_id[name].support_target],
                    final_positions,
                )
            target_qpos = np.asarray(by_id[name].articulation_qpos, dtype=float)
            final_qpos = np.asarray(after.get("qpos") or [], dtype=float)
            articulation_error = (
                float(np.max(np.abs(final_qpos - target_qpos)))
                if len(target_qpos) and len(final_qpos) == len(target_qpos)
                else None
            )
            loader_translation_offset = (
                np.asarray(before["position_m"]) - np.asarray(by_id[name].pose.position_m)
                if by_id[name].load_type == "urdf"
                else np.zeros(3, dtype=float)
            )
            logical_final_position = np.asarray(after["position_m"]) - loader_translation_offset
            objects[name] = {
                "asset_id": by_id[name].asset_id,
                "entity_id": identifiers[0] if len(identifiers) == 1 else None,
                "entity_ids": identifiers,
                "initial_pose": before,
                "final_pose": after,
                "translation_drift_m": float(
                    np.linalg.norm(
                        np.asarray(after["position_m"]) - np.asarray(before["position_m"])
                    )
                ),
                "rotation_drift_deg": quaternion_angle_deg(
                    after["orientation_wxyz"], before["orientation_wxyz"]
                ),
                "resolved_translation_error_m": float(
                    np.linalg.norm(logical_final_position - np.asarray(by_id[name].pose.position_m))
                ),
                "loader_translation_offset_m": loader_translation_offset.tolist(),
                "resolved_rotation_error_deg": quaternion_angle_deg(
                    after["orientation_wxyz"],
                    list(by_id[name].pose.orientation_wxyz),
                ),
                "late_window_translation_m": late_translation,
                "late_window_rotation_deg": late_rotation,
                "still_moving": late_translation > 0.001 or late_rotation > 0.5,
                "visible_pixels": visible_pixels,
                "penetration_count": final_contacts["penetration_by_object"][name],
                "support_contact": raw_support_contact,
                "support_contact_fraction": contact_fraction,
                "support_contact_samples": support_contact_samples,
                "unexpected_contact_fraction": unexpected_contact_fraction,
                "unexpected_contact_targets": sorted(unexpected_contact_targets[name]),
                "support_mode": support_mode,
                "support_target": observed_support_target,
                "expected_support_target": by_id[name].support_target,
                "support_footprint_margin_m": support_margin,
                "inside_contained": inside_contained,
                "dropped": dropped,
                "articulation_target_qpos": list(by_id[name].articulation_qpos),
                "articulation_final_qpos": after.get("qpos") or [],
                "articulation_max_abs_error": articulation_error,
            }
        report.update(
            {
                "status": "pass",
                "robot_initial_collision_count": initial_contacts["robot_collision_count"],
                "robot_initial_collision_pairs": initial_contacts["robot_collision_pairs"],
                "robot_final_collision_count": final_contacts["robot_collision_count"],
                "robot_final_collision_pairs": final_contacts["robot_collision_pairs"],
                "objects": objects,
                "relations": runtime_relation_results(resolved, final_positions),
                "initial_contact_records": initial_contacts["records"],
                "final_contact_records": final_contacts["records"],
                "images": {
                    "head": "preview_head.png",
                    "world_left": "preview_world_left.png",
                    "world_right": "preview_world_right.png",
                    "segmentation": "preview_segmentation.png",
                    "observer_start": "observer_start.png" if frames else None,
                    "observer_mid": "observer_mid.png" if frames else None,
                    "observer_end": "observer_end.png" if frames else None,
                },
                "video": "observer_runtime.mp4" if frames else None,
                "video_frame_count": len(frames),
                "unique_video_frame_count": unique_video_frame_count,
                "fps": args.fps,
                **runtime_timeline_evidence(
                    base_simulation_step_count=total_steps,
                    settle_extra_steps=settle_extra_steps,
                    video_sample_step_indices=video_steps,
                ),
                "precheck_steps": args.precheck_steps,
                "total_physics_step_count": completed_steps,
                "contact_window_steps": contact_window_steps,
                "settle_extra_steps": settle_extra_steps,
                "settle_converge_max": args.settle_converge_max,
            }
        )
        write_json(out_dir / "runtime_evidence.json", report)
        validation = validate_resolved_scene(
            resolved,
            catalog=catalog,
            runtime_evidence=report,
            require_runtime=True,
            min_visible_pixels=args.min_visible_pixels,
        )
        write_json(out_dir / "runtime_validation_report.json", validation)
        _require_exact_artifacts(
            out_dir,
            (*media_artifact_paths, *RUNTIME_EVIDENCE_ARTIFACT_PATHS),
        )
        _emit_runtime_event(
            event_emitter,
            kind=RuntimeEventKind.EVIDENCE_COMPLETED,
            artifact_paths=RUNTIME_EVIDENCE_ARTIFACT_PATHS,
        )
        print(
            f"{validation['status'].upper()} scene={resolved.scene_id} "
            f"fail={validation['fail_count']} video_frames={len(frames)}"
        )
        exit_code = 0 if args.evidence_only or validation["status"] == "pass" else 2
    except Exception as error:
        report.update({"status": "fail", "error": repr(error)})
        write_json(out_dir / "runtime_evidence.json", report)
        print(f"FAIL {out_dir / 'runtime_evidence.json'}")
        raise
    finally:
        _close_runtime_task(task)
    _emit_runtime_event(
        event_emitter,
        kind=RuntimeEventKind.WORKER_COMPLETED,
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
