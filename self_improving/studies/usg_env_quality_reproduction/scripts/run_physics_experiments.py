#!/usr/bin/env python3
"""Run first-party geometry, collision, and release tests on RoboTwin assets."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import imageio.v2 as imageio
import numpy as np
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path}")
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quat_multiply(first: Iterable[float], second: Iterable[float]) -> np.ndarray:
    aw, ax, ay, az = first
    bw, bx, by, bz = second
    value = np.array(
        [
            aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
        ],
        dtype=float,
    )
    return value / np.linalg.norm(value)


def yaw_orientation(stable_wxyz: Iterable[float], yaw: float) -> np.ndarray:
    yaw_q = (math.cos(yaw / 2.0), 0.0, 0.0, math.sin(yaw / 2.0))
    return quat_multiply(yaw_q, stable_wxyz)


def quaternion_angle_deg(first: Iterable[float], second: Iterable[float]) -> float:
    a = np.asarray(first, dtype=float)
    b = np.asarray(second, dtype=float)
    a /= np.linalg.norm(a)
    b /= np.linalg.norm(b)
    cosine = float(np.clip(abs(np.dot(a, b)), -1.0, 1.0))
    return math.degrees(2.0 * math.acos(cosine))


def pose_values(pose: Any) -> tuple[np.ndarray, np.ndarray]:
    return np.asarray(pose.p, dtype=float), np.asarray(pose.q, dtype=float)


def aabb_union(boxes: list[np.ndarray]) -> np.ndarray:
    if not boxes:
        raise ValueError("no collision AABBs")
    return np.asarray(
        [
            np.min([box[0] for box in boxes], axis=0),
            np.max([box[1] for box in boxes], axis=0),
        ]
    )


def articulation_collision_aabb(articulation: Any) -> np.ndarray:
    boxes = [
        np.asarray(link.compute_global_aabb_tight(), dtype=float)
        for link in articulation.get_links()
        if list(link.get_collision_shapes())
    ]
    return aabb_union(boxes)


def entity_collision_component(entity: Any) -> Any:
    import sapien

    candidates = [
        component
        for component in entity.components
        if isinstance(component, sapien.physx.PhysxRigidBaseComponent)
    ]
    if len(candidates) != 1:
        raise RuntimeError(f"expected one PhysX body on {entity.name}, found {len(candidates)}")
    return candidates[0]


def entity_collision_aabb(entity: Any) -> np.ndarray:
    return np.asarray(entity_collision_component(entity).compute_global_aabb_tight(), dtype=float)


def catalog_model(catalog: dict[str, Any], asset_id: str, model_id: int) -> dict[str, Any]:
    entry = next(item for item in catalog["entries"] if item["asset_id"] == asset_id)
    return next(item for item in entry["models"] if item["model_id"] == model_id)


@dataclass(frozen=True)
class AssetContext:
    config: dict[str, Any]
    cabinet_model: dict[str, Any]
    basket_model: dict[str, Any]
    robotwin_root: Path
    cabinet_metadata: dict[str, Any]

    @property
    def table_height(self) -> float:
        return float(self.config["scene"]["table_height_m"])

    @property
    def cabinet_yaw(self) -> float:
        return float(self.config["scene"]["cabinet_yaw_rad"])

    @property
    def cabinet_xy(self) -> np.ndarray:
        return np.asarray(self.config["scene"]["cabinet_xy_m"], dtype=float)

    @property
    def cabinet_orientation(self) -> np.ndarray:
        return yaw_orientation(self.cabinet_model["stable_orientation_wxyz"], self.cabinet_yaw)

    @property
    def cabinet_transform_z(self) -> float:
        return float(np.asarray(self.cabinet_metadata["transform_matrix"])[2, 3])


def make_context(config: dict[str, Any]) -> AssetContext:
    catalog = load_json(Path(config["catalog_path"]))
    cabinet = config["cabinet"]
    basket = config["basket"]
    robotwin_root = Path(config["robotwin_root"])
    cabinet_metadata = load_json(robotwin_root / cabinet["metadata_relative_path"])
    return AssetContext(
        config=config,
        cabinet_model=catalog_model(catalog, cabinet["asset_id"], int(cabinet["model_id"])),
        basket_model=catalog_model(catalog, basket["asset_id"], int(basket["model_id"])),
        robotwin_root=robotwin_root,
        cabinet_metadata=cabinet_metadata,
    )


def load_cabinet(
    scene: Any,
    context: AssetContext,
    *,
    fixed_root: bool,
    qpos: float,
    corrected_z_m: float = 0.0,
) -> Any:
    import sapien

    loader = scene.create_urdf_loader()
    loader.scale = float(context.config["cabinet"]["scale"])
    loader.fix_root_link = fixed_root
    loader.load_multiple_collisions_from_file = True
    urdf = context.robotwin_root / context.config["cabinet"]["urdf_relative_path"]
    loaded = loader.load_multiple(str(urdf))
    articulation = loaded[0][0]
    root_position = np.array(
        [
            context.cabinet_xy[0],
            context.cabinet_xy[1],
            context.table_height + context.cabinet_transform_z - corrected_z_m,
        ]
    )
    articulation.set_root_pose(sapien.Pose(root_position, context.cabinet_orientation))
    articulation.set_name("cabinet")
    articulation.set_qpos(np.full(articulation.get_dof(), qpos, dtype=float))
    articulation.set_qvel(np.zeros(articulation.get_dof(), dtype=float))
    if not fixed_root:
        for joint in articulation.get_active_joints():
            joint.set_drive_properties(stiffness=10000.0, damping=400.0, force_limit=5000.0)
            joint.set_drive_target(float(qpos))
    return articulation


def lock_articulation_qpos(articulation: Any, qpos: float) -> None:
    """Hold every movable joint at the requested final qpos for geometry queries."""
    import numpy as np

    for joint in articulation.get_active_joints():
        joint.set_limits(np.asarray([[qpos, qpos]], dtype=float))
    articulation.set_qpos(np.full(articulation.get_dof(), qpos, dtype=float))
    articulation.set_qvel(np.zeros(articulation.get_dof(), dtype=float))


def tint_entity(entity: Any, rgb: tuple[float, float, float]) -> int:
    material_ids: set[int] = set()
    for component in entity.components:
        for shape in getattr(component, "render_shapes", ()):
            candidates = [getattr(shape, "material", None)]
            candidates.extend(getattr(part, "material", None) for part in getattr(shape, "parts", ()))
            for material in candidates:
                if material is None or id(material) in material_ids:
                    continue
                material.base_color = [*rgb, 1.0]
                material_ids.add(id(material))
    return len(material_ids)


def load_basket(
    scene: Any,
    context: AssetContext,
    *,
    dynamic: bool,
    offset_xy: Iterable[float],
) -> Any:
    import sapien

    builder = scene.create_actor_builder()
    builder.set_physx_body_type("dynamic" if dynamic else "static")
    collision = context.robotwin_root / context.config["basket"]["collision_relative_path"]
    visual = context.robotwin_root / context.config["basket"]["visual_relative_path"]
    scale = context.config["basket"]["scale"]
    builder.add_multiple_convex_collisions_from_file(filename=str(collision), scale=scale)
    builder.add_visual_from_file(filename=str(visual), scale=scale)
    entity = builder.build(name="basket")
    xy = context.cabinet_xy + np.asarray(offset_xy, dtype=float)
    entity.set_pose(
        sapien.Pose(
            [xy[0], xy[1], context.table_height],
            context.basket_model["stable_orientation_wxyz"],
        )
    )
    tint_entity(entity, (0.95, 0.72, 0.05))
    return entity


def add_table(scene: Any, table_height: float) -> Any:
    import sapien

    builder = scene.create_actor_builder()
    builder.set_physx_body_type("static")
    half_size = [0.62, 0.50, 0.03]
    builder.add_box_collision(half_size=half_size)
    builder.add_box_visual(half_size=half_size, material=[0.48, 0.50, 0.52])
    table = builder.build(name="table")
    table.set_pose(sapien.Pose([0.0, 0.0, table_height - half_size[2]]))
    return table


def proxy_aabb(
    dimensions: Iterable[float],
    *,
    center_xy: Iterable[float],
    yaw: float,
    bottom_z: float,
) -> np.ndarray:
    width, depth, height = (float(value) for value in dimensions)
    cosine = abs(math.cos(yaw))
    sine = abs(math.sin(yaw))
    half_x = 0.5 * (cosine * width + sine * depth)
    half_y = 0.5 * (sine * width + cosine * depth)
    x, y = center_xy
    return np.asarray(
        [[x - half_x, y - half_y, bottom_z], [x + half_x, y + half_y, bottom_z + height]],
        dtype=float,
    )


def boxes_separated(
    first: np.ndarray,
    second: np.ndarray,
    *,
    xy_clearance: float,
    z_clearance: float,
) -> bool:
    return bool(
        first[1, 0] + xy_clearance <= second[0, 0]
        or second[1, 0] + xy_clearance <= first[0, 0]
        or first[1, 1] + xy_clearance <= second[0, 1]
        or second[1, 1] + xy_clearance <= first[0, 1]
        or first[1, 2] + z_clearance <= second[0, 2]
        or second[1, 2] + z_clearance <= first[0, 2]
    )


def aabb_overlap(first: np.ndarray, second: np.ndarray) -> bool:
    return bool(np.all(first[1] >= second[0]) and np.all(second[1] >= first[0]))


def aabb_iou(first: np.ndarray, second: np.ndarray) -> float:
    overlap = np.maximum(0.0, np.minimum(first[1], second[1]) - np.maximum(first[0], second[0]))
    intersection = float(np.prod(overlap))
    first_volume = float(np.prod(first[1] - first[0]))
    second_volume = float(np.prod(second[1] - second[0]))
    union = first_volume + second_volume - intersection
    return intersection / union if union > 0 else 0.0


def body_entity_name(body: Any) -> str:
    entity = getattr(body, "entity", None)
    return str(getattr(entity, "name", ""))


def pair_contact_metrics(
    contacts: Iterable[Any],
    *,
    first_entity_names: set[str],
    second_entity_names: set[str],
    active_separation: float,
    penetration_separation: float,
) -> dict[str, Any]:
    separations: list[float] = []
    impulses: list[float] = []
    pair_records = 0
    for contact in contacts:
        bodies = list(getattr(contact, "bodies", ()))
        if len(bodies) != 2:
            continue
        names = [body_entity_name(body) for body in bodies]
        if not (
            (names[0] in first_entity_names and names[1] in second_entity_names)
            or (names[1] in first_entity_names and names[0] in second_entity_names)
        ):
            continue
        pair_records += 1
        for point in list(getattr(contact, "points", ())):
            separations.append(float(point.separation))
            impulses.append(float(np.linalg.norm(np.asarray(point.impulse, dtype=float))))
    return {
        "contact_records": pair_records,
        "point_count": len(separations),
        "active_point_count": sum(value <= active_separation for value in separations),
        "penetration_point_count": sum(value < penetration_separation for value in separations),
        "min_separation_m": min(separations) if separations else None,
        "max_impulse_norm": max(impulses) if impulses else 0.0,
    }


def envelope_experiment(context: AssetContext) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import sapien

    scene = sapien.Scene()
    scene.set_timestep(1e-4)
    cabinet = load_cabinet(scene, context, fixed_root=True, qpos=0.0)
    qlimits = np.asarray(cabinet.get_qlimits(), dtype=float)
    if qlimits.shape != (3, 2) or not np.allclose(qlimits, qlimits[0]):
        raise RuntimeError(f"unexpected cabinet qlimits: {qlimits}")
    q_low, q_high = (float(value) for value in qlimits[0])
    proxy = proxy_aabb(
        context.cabinet_model["dimensions_m"],
        center_xy=context.cabinet_xy,
        yaw=context.cabinet_yaw,
        bottom_z=context.table_height,
    )
    rows: list[dict[str, Any]] = []
    count = int(context.config["envelope"]["fractions"])
    for fraction in np.linspace(0.0, 1.0, count):
        qpos = q_low + float(fraction) * (q_high - q_low)
        cabinet.set_qpos(np.full(3, qpos))
        physical = articulation_collision_aabb(cabinet)
        extent = physical[1] - physical[0]
        outside_low = np.maximum(0.0, proxy[0] - physical[0])
        outside_high = np.maximum(0.0, physical[1] - proxy[1])
        rows.append(
            {
                "fraction_of_joint_limit": float(fraction),
                "qpos_m": qpos,
                "physical_min_x_m": physical[0, 0],
                "physical_min_y_m": physical[0, 1],
                "physical_min_z_m": physical[0, 2],
                "physical_max_x_m": physical[1, 0],
                "physical_max_y_m": physical[1, 1],
                "physical_max_z_m": physical[1, 2],
                "physical_extent_x_m": extent[0],
                "physical_extent_y_m": extent[1],
                "physical_extent_z_m": extent[2],
                "proxy_min_x_m": proxy[0, 0],
                "proxy_min_y_m": proxy[0, 1],
                "proxy_min_z_m": proxy[0, 2],
                "proxy_max_x_m": proxy[1, 0],
                "proxy_max_y_m": proxy[1, 1],
                "proxy_max_z_m": proxy[1, 2],
                "proxy_extent_x_m": proxy[1, 0] - proxy[0, 0],
                "proxy_extent_y_m": proxy[1, 1] - proxy[0, 1],
                "proxy_extent_z_m": proxy[1, 2] - proxy[0, 2],
                "outside_proxy_neg_x_m": outside_low[0],
                "outside_proxy_neg_y_m": outside_low[1],
                "outside_proxy_neg_z_m": outside_low[2],
                "outside_proxy_pos_x_m": outside_high[0],
                "outside_proxy_pos_y_m": outside_high[1],
                "outside_proxy_pos_z_m": outside_high[2],
                "aabb_iou": aabb_iou(proxy, physical),
            }
        )
    configured_open = float(context.cabinet_model["articulation_open_qpos"][0])
    metadata = {
        "joint_limits_m": qlimits.tolist(),
        "configured_closed_qpos_m": context.cabinet_model["articulation_closed_qpos"],
        "configured_open_qpos_m": context.cabinet_model["articulation_open_qpos"],
        "configured_open_fraction_of_limit": (configured_open - q_low) / (q_high - q_low),
        "proxy_dimensions_m": context.cabinet_model["dimensions_m"],
        "stable_orientation_wxyz": context.cabinet_model["stable_orientation_wxyz"],
        "runtime_yaw_rad": context.cabinet_yaw,
        "runtime_transform_z_m": context.cabinet_transform_z,
        "proxy_aabb_m": proxy.tolist(),
        "sample_count": len(rows),
    }
    return rows, metadata


def grid_values(low: float, high: float, step: float) -> np.ndarray:
    count = int(round((high - low) / step))
    values = low + np.arange(count + 1, dtype=float) * step
    if not math.isclose(float(values[-1]), high, abs_tol=1e-9):
        raise ValueError(f"grid endpoint mismatch: {values[-1]} != {high}")
    return values


def contact_sweep_experiment(context: AssetContext) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import sapien

    sweep = context.config["contact_sweep"]
    x_values = grid_values(float(sweep["x_min_m"]), float(sweep["x_max_m"]), float(sweep["step_m"]))
    y_values = grid_values(float(sweep["y_min_m"]), float(sweep["y_max_m"]), float(sweep["step_m"]))
    active_threshold = float(sweep["active_separation_m"])
    penetration_threshold = float(sweep["penetration_separation_m"])
    xy_clearance = float(sweep["proxy_clearance_xy_m"])
    z_clearance = float(sweep["proxy_clearance_z_m"])
    cabinet_proxy = proxy_aabb(
        context.cabinet_model["dimensions_m"],
        center_xy=context.cabinet_xy,
        yaw=context.cabinet_yaw,
        bottom_z=context.table_height,
    )
    rows: list[dict[str, Any]] = []
    for qpos in (float(value) for value in sweep["qpos_m"]):
        dynamic_scene = sapien.Scene()
        dynamic_scene.set_timestep(1e-4)
        dynamic_cabinet = load_cabinet(dynamic_scene, context, fixed_root=True, qpos=qpos)
        lock_articulation_qpos(dynamic_cabinet, qpos)
        dynamic_basket = load_basket(dynamic_scene, context, dynamic=True, offset_xy=(1.0, 1.0))
        dynamic_body = entity_collision_component(dynamic_basket)
        dynamic_body.set_disable_gravity(True)
        cabinet_entity_names = {link.entity.name for link in dynamic_cabinet.get_links()}
        isolation_xy = context.cabinet_xy + np.asarray([1.0, 1.0])
        isolation_pose = sapien.Pose(
            [isolation_xy[0], isolation_xy[1], context.table_height],
            context.basket_model["stable_orientation_wxyz"],
        )

        static_scene = sapien.Scene()
        static_scene.set_timestep(1e-4)
        static_cabinet = load_cabinet(static_scene, context, fixed_root=True, qpos=qpos)
        lock_articulation_qpos(static_cabinet, qpos)
        static_basket = load_basket(static_scene, context, dynamic=False, offset_xy=(1.0, 1.0))
        static_cabinet_names = {link.entity.name for link in static_cabinet.get_links()}

        physical_cabinet = articulation_collision_aabb(dynamic_cabinet)
        for y_offset in y_values:
            for x_offset in x_values:
                basket_xy = context.cabinet_xy + np.asarray([x_offset, y_offset])
                basket_pose = sapien.Pose(
                    [basket_xy[0], basket_xy[1], context.table_height],
                    context.basket_model["stable_orientation_wxyz"],
                )
                dynamic_basket.set_pose(isolation_pose)
                dynamic_body.set_linear_velocity([0.0, 0.0, 0.0])
                dynamic_body.set_angular_velocity([0.0, 0.0, 0.0])
                dynamic_body.wake_up()
                dynamic_scene.step()
                static_basket.set_pose(isolation_pose)
                static_scene.step()

                dynamic_basket.set_pose(basket_pose)
                dynamic_body.set_linear_velocity([0.0, 0.0, 0.0])
                dynamic_body.set_angular_velocity([0.0, 0.0, 0.0])
                dynamic_body.wake_up()
                basket_before = entity_collision_aabb(dynamic_basket)
                pose_before, _ = pose_values(dynamic_basket.pose)
                dynamic_scene.step()
                pose_after, _ = pose_values(dynamic_basket.pose)
                dynamic_metrics = pair_contact_metrics(
                    dynamic_scene.get_contacts(),
                    first_entity_names=cabinet_entity_names,
                    second_entity_names={"basket"},
                    active_separation=active_threshold,
                    penetration_separation=penetration_threshold,
                )

                static_basket.set_pose(basket_pose)
                static_scene.step()
                static_metrics = pair_contact_metrics(
                    static_scene.get_contacts(),
                    first_entity_names=static_cabinet_names,
                    second_entity_names={"basket"},
                    active_separation=active_threshold,
                    penetration_separation=penetration_threshold,
                )

                basket_proxy = proxy_aabb(
                    context.basket_model["dimensions_m"],
                    center_xy=basket_xy,
                    yaw=0.0,
                    bottom_z=context.table_height,
                )
                proxy_pass = boxes_separated(
                    cabinet_proxy,
                    basket_proxy,
                    xy_clearance=xy_clearance,
                    z_clearance=z_clearance,
                )
                oracle_contact = dynamic_metrics["active_point_count"] > 0
                oracle_penetration = dynamic_metrics["penetration_point_count"] > 0
                rows.append(
                    {
                        "qpos_m": qpos,
                        "basket_offset_x_m": float(x_offset),
                        "basket_offset_y_m": float(y_offset),
                        "proxy_pass": proxy_pass,
                        "proxy_predicts_collision": not proxy_pass,
                        "physical_aabb_overlap": aabb_overlap(physical_cabinet, basket_before),
                        "oracle_contact": oracle_contact,
                        "oracle_penetration": oracle_penetration,
                        "false_pass": proxy_pass and oracle_contact,
                        "false_reject": (not proxy_pass) and (not oracle_contact),
                        "dynamic_contact_records": dynamic_metrics["contact_records"],
                        "dynamic_point_count": dynamic_metrics["point_count"],
                        "dynamic_active_point_count": dynamic_metrics["active_point_count"],
                        "dynamic_penetration_point_count": dynamic_metrics["penetration_point_count"],
                        "dynamic_min_separation_m": dynamic_metrics["min_separation_m"],
                        "dynamic_max_impulse_norm": dynamic_metrics["max_impulse_norm"],
                        "dynamic_one_step_displacement_m": float(np.linalg.norm(pose_after - pose_before)),
                        "dynamic_cabinet_qpos_max_abs_error_m": float(
                            np.max(np.abs(np.asarray(dynamic_cabinet.get_qpos()) - qpos))
                        ),
                        "production_static_contact_records": static_metrics["contact_records"],
                        "production_static_active_point_count": static_metrics["active_point_count"],
                        "production_static_detects_contact": static_metrics["active_point_count"] > 0,
                        "production_static_cabinet_qpos_max_abs_error_m": float(
                            np.max(np.abs(np.asarray(static_cabinet.get_qpos()) - qpos))
                        ),
                    }
                )
    metadata = {
        "x_values_m": x_values.tolist(),
        "y_values_m": y_values.tolist(),
        "qpos_m": sweep["qpos_m"],
        "row_count": len(rows),
        "dynamic_shadow_definition": "same basket collision geometry with a dynamic body, gravity disabled",
        "production_definition": "cabinet fixed-root articulation and static basket, matching catalog is_static",
        "oracle_definition": f"at least one PhysX contact point with separation <= {active_threshold} m",
        "penetration_definition": f"at least one PhysX contact point with separation < {penetration_threshold} m",
        "query_timestep_s": 0.0001,
        "contact_cache_protocol": (
            "before every query, teleport the basket to a non-contact isolation pose, "
            "advance one step, then teleport to the target and advance one measured step"
        ),
        "joint_state_protocol": (
            "set every active joint limit to [qpos, qpos] before scanning; record the "
            "post-step maximum absolute qpos error for every query"
        ),
        "isolation_offset_xy_m": [1.0, 1.0],
        "proxy_xy_clearance_m": xy_clearance,
        "proxy_z_clearance_m": z_clearance,
        "scan_order": "qpos, y ascending, x ascending",
    }
    return rows, metadata


def look_at_pose(eye: Iterable[float], target: Iterable[float]) -> Any:
    import sapien
    from scipy.spatial.transform import Rotation

    eye_array = np.asarray(eye, dtype=float)
    forward = np.asarray(target, dtype=float) - eye_array
    forward /= np.linalg.norm(forward)
    world_up = np.array([0.0, 0.0, 1.0])
    left = np.cross(world_up, forward)
    left /= np.linalg.norm(left)
    up = np.cross(forward, left)
    rotation = np.stack([forward, left, up], axis=1)
    xyzw = Rotation.from_matrix(rotation).as_quat()
    wxyz = [xyzw[3], xyzw[0], xyzw[1], xyzw[2]]
    return sapien.Pose(eye_array, wxyz)


def add_camera(scene: Any, config: dict[str, Any]) -> Any:
    import sapien

    rollout = config["rollout"]
    mount = scene.create_actor_builder().build_kinematic(name="camera_mount")
    mount.set_pose(look_at_pose([-1.05, -1.15, 1.35], [-0.08, 0.02, 0.84]))
    return scene.add_mounted_camera(
        "evidence_camera",
        mount,
        sapien.Pose(),
        int(rollout["render_width"]),
        int(rollout["render_height"]),
        0.85,
        0.01,
        10.0,
    )


def render_rgb(scene: Any, camera: Any) -> np.ndarray:
    scene.update_render()
    camera.take_picture()
    rgba = camera.get_picture("Color")
    return (np.clip(rgba[..., :3], 0.0, 1.0) * 255.0).round().astype(np.uint8)


def rollout_contact_flags(scene: Any, cabinet: Any) -> tuple[bool, bool, int]:
    cabinet_names = {link.entity.name for link in cabinet.get_links()}
    table_metrics = pair_contact_metrics(
        scene.get_contacts(),
        first_entity_names=cabinet_names,
        second_entity_names={"table"},
        active_separation=0.001,
        penetration_separation=-0.002,
    )
    basket_metrics = pair_contact_metrics(
        scene.get_contacts(),
        first_entity_names=cabinet_names,
        second_entity_names={"basket"},
        active_separation=0.001,
        penetration_separation=-0.002,
    )
    return (
        table_metrics["active_point_count"] > 0,
        basket_metrics["active_point_count"] > 0,
        table_metrics["penetration_point_count"] + basket_metrics["penetration_point_count"],
    )


def run_rollout(
    context: AssetContext,
    *,
    scenario: str,
    fixed: bool,
    basket_offset: Iterable[float],
    correction_m: float,
    perturbation_xy: tuple[float, float],
    render: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[np.ndarray]]:
    import sapien

    rollout = context.config["rollout"]
    sapien.physx.set_scene_config(gravity=[0.0, 0.0, -9.81])
    scene = sapien.Scene()
    timestep = float(rollout["timestep_s"])
    scene.set_timestep(timestep)
    scene.set_ambient_light([0.55, 0.55, 0.55])
    scene.add_directional_light([0.4, 0.6, -1.0], [1.0, 0.96, 0.90], shadow=True)
    scene.add_directional_light([-0.5, -0.2, -1.0], [0.35, 0.40, 0.50], shadow=False)
    add_table(scene, context.table_height)
    qpos = float(context.config["scene"]["half_open_qpos_m"])
    cabinet = load_cabinet(
        scene,
        context,
        fixed_root=fixed,
        qpos=qpos,
        corrected_z_m=correction_m,
    )
    basket = load_basket(scene, context, dynamic=not fixed, offset_xy=basket_offset)
    if not fixed:
        cabinet.set_root_linear_velocity([perturbation_xy[0], perturbation_xy[1], 0.0])
        cabinet.set_root_angular_velocity([0.0, 0.0, 0.0])
        basket_body = entity_collision_component(basket)
        basket_body.set_linear_velocity([0.0, 0.0, 0.0])
        basket_body.set_angular_velocity([0.0, 0.0, 0.0])

    camera = add_camera(scene, context.config) if render else None
    frames: list[np.ndarray] = []
    initial_frame = render_rgb(scene, camera) if camera is not None else None
    if initial_frame is not None:
        frames.append(initial_frame)

    steps = int(rollout["steps"])
    video_frames = int(rollout["video_frames"])
    sample_steps = set(np.linspace(1, steps, video_frames - 1, dtype=int).tolist()) if render else set()
    trajectory: list[dict[str, Any]] = []
    initial_position, initial_quaternion = pose_values(cabinet.get_root_pose())
    for step in range(1, steps + 1):
        scene.step()
        root_position, root_quaternion = pose_values(cabinet.get_root_pose())
        basket_position, basket_quaternion = pose_values(basket.pose)
        collision_box = articulation_collision_aabb(cabinet)
        table_contact, basket_contact, penetration_points = rollout_contact_flags(scene, cabinet)
        root_linear_velocity = np.asarray(cabinet.get_root_linear_velocity(), dtype=float)
        root_angular_velocity = np.asarray(cabinet.get_root_angular_velocity(), dtype=float)
        trajectory.append(
            {
                "scenario": scenario,
                "step": step,
                "time_s": step * timestep,
                "cabinet_x_m": root_position[0],
                "cabinet_y_m": root_position[1],
                "cabinet_z_m": root_position[2],
                "cabinet_qw": root_quaternion[0],
                "cabinet_qx": root_quaternion[1],
                "cabinet_qy": root_quaternion[2],
                "cabinet_qz": root_quaternion[3],
                "cabinet_speed_m_s": float(np.linalg.norm(root_linear_velocity)),
                "cabinet_angular_speed_rad_s": float(np.linalg.norm(root_angular_velocity)),
                "cabinet_collision_bottom_z_m": collision_box[0, 2],
                "cabinet_collision_top_z_m": collision_box[1, 2],
                "basket_x_m": basket_position[0],
                "basket_y_m": basket_position[1],
                "basket_z_m": basket_position[2],
                "basket_qw": basket_quaternion[0],
                "basket_qx": basket_quaternion[1],
                "basket_qy": basket_quaternion[2],
                "basket_qz": basket_quaternion[3],
                "table_contact": table_contact,
                "unexpected_basket_contact": basket_contact,
                "penetration_point_count": penetration_points,
            }
        )
        if camera is not None and step in sample_steps:
            frames.append(render_rgb(scene, camera))

    if render and len(frames) != video_frames:
        raise RuntimeError(f"{scenario}: expected {video_frames} frames, got {len(frames)}")
    final_position, final_quaternion = pose_values(cabinet.get_root_pose())
    terminal = trajectory[-int(rollout["terminal_window_steps"]):]
    late_start = trajectory[-int(rollout["terminal_window_steps"])]
    late_position = np.asarray(
        [late_start["cabinet_x_m"], late_start["cabinet_y_m"], late_start["cabinet_z_m"]]
    )
    late_quaternion = np.asarray(
        [late_start["cabinet_qw"], late_start["cabinet_qx"], late_start["cabinet_qy"], late_start["cabinet_qz"]]
    )
    translation_drift = float(np.linalg.norm(final_position - initial_position))
    rotation_drift = quaternion_angle_deg(final_quaternion, initial_quaternion)
    late_translation = float(np.linalg.norm(final_position - late_position))
    late_rotation = quaternion_angle_deg(final_quaternion, late_quaternion)
    support_fraction = sum(bool(row["table_contact"]) for row in terminal) / len(terminal)
    unexpected_fraction = sum(bool(row["unexpected_basket_contact"]) for row in terminal) / len(terminal)
    pose_preserved = (
        translation_drift <= float(rollout["max_translation_drift_m"])
        and rotation_drift <= float(rollout["max_rotation_drift_deg"])
    )
    physical_support = support_fraction >= float(rollout["min_support_contact_fraction"])
    no_unexpected = unexpected_fraction <= float(rollout["min_unexpected_contact_fraction"])
    settled = late_translation <= 0.002 and late_rotation <= 1.0
    dropped = bool(
        min(row["cabinet_collision_bottom_z_m"] for row in trajectory)
        < context.table_height - 0.03
    )
    metrics = {
        "scenario": scenario,
        "fixed": fixed,
        "basket_offset_m": list(basket_offset),
        "height_correction_m": correction_m,
        "perturbation_velocity_xy_m_s": list(perturbation_xy),
        "steps": steps,
        "timestep_s": timestep,
        "initial_root_position_m": initial_position.tolist(),
        "final_root_position_m": final_position.tolist(),
        "translation_drift_m": translation_drift,
        "rotation_drift_deg": rotation_drift,
        "late_translation_m": late_translation,
        "late_rotation_deg": late_rotation,
        "terminal_table_contact_fraction": support_fraction,
        "terminal_unexpected_basket_contact_fraction": unexpected_fraction,
        "max_penetration_point_count": max(row["penetration_point_count"] for row in trajectory),
        "minimum_collision_bottom_z_m": min(row["cabinet_collision_bottom_z_m"] for row in trajectory),
        "pose_preserved": bool(pose_preserved),
        "physical_table_support": bool(physical_support),
        "no_unexpected_basket_contact": bool(no_unexpected),
        "settled": bool(settled),
        "dropped": dropped,
        "dynamic_gate_pass": bool(
            (not fixed)
            and pose_preserved
            and physical_support
            and no_unexpected
            and settled
            and not dropped
        ),
        "frame_count": len(frames),
    }
    return trajectory, metrics, frames


def label_frame(frame: np.ndarray, label: str) -> np.ndarray:
    image = Image.fromarray(frame)
    draw = ImageDraw.Draw(image)
    draw.rectangle((12, 12, 240, 48), fill=(0, 0, 0, 190))
    draw.text((22, 21), label, fill=(255, 255, 255))
    return np.asarray(image)


def write_video(path: Path, frames: list[np.ndarray], fps: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    imageio.mimwrite(
        path,
        frames,
        fps=fps,
        codec="libx264",
        quality=8,
        macro_block_size=16,
        ffmpeg_log_level="error",
    )


def rollout_experiments(
    context: AssetContext,
    closed_bottom_offset_m: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    scene_config = context.config["scene"]
    rollout_config = context.config["rollout"]
    adversarial_offset = tuple(float(value) for value in scene_config["adversarial_basket_offset_m"])
    control_offset = tuple(float(value) for value in scene_config["control_basket_offset_m"])
    trajectories: list[dict[str, Any]] = []
    trial_metrics: list[dict[str, Any]] = []
    rendered: dict[str, list[np.ndarray]] = {}
    for scenario, fixed, offset, correction in (
        ("fixed_adversarial", True, adversarial_offset, 0.0),
        ("released_adversarial", False, adversarial_offset, 0.0),
        ("released_corrected_control", False, control_offset, closed_bottom_offset_m),
    ):
        trajectory, metrics, frames = run_rollout(
            context,
            scenario=scenario,
            fixed=fixed,
            basket_offset=offset,
            correction_m=correction,
            perturbation_xy=(0.0, 0.0),
            render=True,
        )
        trajectories.extend(trajectory)
        trial_metrics.append(metrics)
        rendered[scenario] = frames

    velocities = [float(value) for value in rollout_config["perturbation_velocities_m_s"]]
    for placement, offset, correction in (
        ("adversarial", adversarial_offset, 0.0),
        ("corrected_control", control_offset, closed_bottom_offset_m),
    ):
        for velocity_y in velocities:
            for velocity_x in velocities:
                scenario = f"perturb_{placement}_vx{velocity_x:+.3f}_vy{velocity_y:+.3f}"
                _, metrics, _ = run_rollout(
                    context,
                    scenario=scenario,
                    fixed=False,
                    basket_offset=offset,
                    correction_m=correction,
                    perturbation_xy=(velocity_x, velocity_y),
                    render=False,
                )
                metrics["placement_class"] = placement
                trial_metrics.append(metrics)

    frames_dir = ROOT / "media" / "frames"
    videos_dir = ROOT / "media" / "videos"
    frames_dir.mkdir(parents=True, exist_ok=True)
    videos_dir.mkdir(parents=True, exist_ok=True)
    for scenario, frames in rendered.items():
        Image.fromarray(frames[0]).save(frames_dir / f"{scenario}-initial.png")
        Image.fromarray(frames[-1]).save(frames_dir / f"{scenario}-final.png")
        write_video(
            videos_dir / f"{scenario}.mp4",
            [label_frame(frame, scenario.replace("_", " ")) for frame in frames],
            int(rollout_config["video_fps"]),
        )

    fixed_frames = rendered["fixed_adversarial"]
    released_frames = rendered["released_adversarial"]
    comparison = [
        np.concatenate(
            [label_frame(first, "production: fixed"), label_frame(second, "dynamic shadow: released")],
            axis=1,
        )
        for first, second in zip(fixed_frames, released_frames)
    ]
    write_video(videos_dir / "fixed-vs-released.mp4", comparison, int(rollout_config["video_fps"]))
    Image.fromarray(comparison[0]).save(frames_dir / "fixed-vs-released-initial.png")
    Image.fromarray(comparison[-1]).save(frames_dir / "fixed-vs-released-final.png")

    fixed_initial = fixed_frames[0].astype(np.int16)
    released_initial = released_frames[0].astype(np.int16)
    difference = fixed_initial - released_initial
    initial_comparison = {
        "shape": list(fixed_initial.shape),
        "exactly_equal": bool(np.array_equal(fixed_initial, released_initial)),
        "differing_channel_fraction": float(np.count_nonzero(difference) / difference.size),
        "max_absolute_channel_difference": int(np.max(np.abs(difference))),
        "rmse_channel_value": float(np.sqrt(np.mean(difference.astype(float) ** 2))),
        "fixed_initial_pixel_sha256": hashlib.sha256(fixed_frames[0].tobytes()).hexdigest(),
        "released_initial_pixel_sha256": hashlib.sha256(released_frames[0].tobytes()).hexdigest(),
    }
    raw_frame_counts = {
        scenario: {
            "total_frame_count": len(frames),
            "unique_frame_count": len(
                {hashlib.sha256(frame.tobytes()).digest() for frame in frames}
            ),
        }
        for scenario, frames in rendered.items()
    }
    raw_frame_counts["fixed-vs-released"] = {
        "total_frame_count": len(comparison),
        "unique_frame_count": len(
            {hashlib.sha256(frame.tobytes()).digest() for frame in comparison}
        ),
    }
    metadata = {
        "closed_physical_bottom_offset_from_declared_table_m": closed_bottom_offset_m,
        "rendered_scenarios": list(rendered),
        "perturbation_trial_count": sum("placement_class" in row for row in trial_metrics),
        "initial_fixed_vs_released": initial_comparison,
        "pre_encoding_video_frame_counts": raw_frame_counts,
        "video_files": [
            "media/videos/fixed_adversarial.mp4",
            "media/videos/released_adversarial.mp4",
            "media/videos/released_corrected_control.mp4",
            "media/videos/fixed-vs-released.mp4",
        ],
    }
    return trajectories, trial_metrics, metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = load_json(args.config.resolve())
    context = make_context(config)

    envelope_rows, envelope_metadata = envelope_experiment(context)
    write_csv(ROOT / "data" / "raw" / "cabinet_articulation_envelope.csv", envelope_rows)
    write_json(ROOT / "data" / "raw" / "cabinet_articulation_envelope_metadata.json", envelope_metadata)

    contact_rows, contact_metadata = contact_sweep_experiment(context)
    write_csv(ROOT / "data" / "raw" / "cabinet_basket_contact_sweep.csv", contact_rows)
    write_json(ROOT / "data" / "raw" / "cabinet_basket_contact_sweep_metadata.json", contact_metadata)

    half_open_qpos = float(config["scene"]["half_open_qpos_m"])
    closest = min(envelope_rows, key=lambda row: abs(float(row["qpos_m"]) - half_open_qpos))
    closed_bottom_offset = float(closest["physical_min_z_m"]) - context.table_height
    trajectories, trials, rollout_metadata = rollout_experiments(context, closed_bottom_offset)
    write_csv(ROOT / "data" / "raw" / "rollout_trajectories.csv", trajectories)
    write_json(ROOT / "data" / "raw" / "rollout_trials.json", trials)
    write_json(ROOT / "data" / "raw" / "rollout_metadata.json", rollout_metadata)

    import numpy
    import sapien

    provenance = {
        "schema_version": "usg_env_quality_reproduction.physics_run.v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "numpy": numpy.__version__,
        "sapien": sapien.__version__,
        "config_path": str(args.config.resolve()),
        "config_sha256": sha256_file(args.config.resolve()),
        "catalog_sha256": sha256_file(Path(config["catalog_path"])),
        "cabinet_urdf_sha256": sha256_file(
            context.robotwin_root / config["cabinet"]["urdf_relative_path"]
        ),
        "cabinet_metadata_sha256": sha256_file(
            context.robotwin_root / config["cabinet"]["metadata_relative_path"]
        ),
        "basket_collision_sha256": sha256_file(
            context.robotwin_root / config["basket"]["collision_relative_path"]
        ),
        "basket_visual_sha256": sha256_file(
            context.robotwin_root / config["basket"]["visual_relative_path"]
        ),
        "outputs": {
            "envelope_rows": len(envelope_rows),
            "contact_sweep_rows": len(contact_rows),
            "trajectory_rows": len(trajectories),
            "rollout_trials": len(trials),
        },
    }
    write_json(ROOT / "data" / "raw" / "physics_run.json", provenance)
    print(
        f"PASS envelope={len(envelope_rows)} contact_sweep={len(contact_rows)} "
        f"trajectory={len(trajectories)} trials={len(trials)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
