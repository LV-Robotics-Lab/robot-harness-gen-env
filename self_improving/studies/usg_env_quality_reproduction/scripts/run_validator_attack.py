#!/usr/bin/env python3
"""Feed measured SAPIEN evidence through the current /gen-env validator."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from run_physics_experiments import (
    ROOT,
    add_camera,
    add_table,
    articulation_collision_aabb,
    body_entity_name,
    entity_collision_aabb,
    load_basket,
    load_cabinet,
    load_json,
    lock_articulation_qpos,
    make_context,
    pair_contact_metrics,
    pose_values,
    quaternion_angle_deg,
    sha256_file,
    write_json,
    yaw_orientation,
)


def import_runtime_module(gen_env_root: Path) -> Any:
    module_path = gen_env_root / "script" / "run_scene_runtime.py"
    spec = importlib.util.spec_from_file_location("measured_gen_env_runtime", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError("empty contact sample set")
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def source_file_list(model: dict[str, Any]) -> list[str]:
    return [
        str(value)
        for key in ("metadata_path", "visual_path", "collision_path", "urdf_path")
        if (value := model.get(key))
    ]


def resolved_scene_dict(config: dict[str, Any], context: Any) -> dict[str, Any]:
    from scene_gen.catalog import AssetCatalog
    from scene_gen.schema import SceneSpec

    cabinet_xy = context.cabinet_xy
    offset = np.asarray(config["scene"]["adversarial_basket_offset_m"], dtype=float)
    basket_xy = cabinet_xy + offset
    table_height = context.table_height
    qpos = float(config["scene"]["half_open_qpos_m"])
    relations = [
        {"relation": "on_table", "source": "cabinet_1", "target": "table"},
        {"relation": "on_table", "source": "basket_1", "target": "table"},
    ]
    source_spec = SceneSpec.model_validate(
        {
            "scene_id": "measured_cabinet_basket_gate_attack",
            "request": "Place a half-open cabinet upper-right of a yellow basket on the table.",
            "language": "en",
            "seed": int(config["random_seed"]),
            "objects": [
                {
                    "object_id": "cabinet_1",
                    "category": "cabinet",
                    "region": "center",
                    "articulation": {
                        "state": "partially_open",
                        "open_fraction": 0.5,
                        "joint_selector": "all_movable",
                    },
                },
                {
                    "object_id": "basket_1",
                    "category": "basket",
                    "color": "yellow",
                    "region": "center",
                },
            ],
            "relations": relations,
        }
    )
    catalog = AssetCatalog.model_validate(load_json(Path(config["catalog_path"])))
    cabinet_orientation = yaw_orientation(
        context.cabinet_model["stable_orientation_wxyz"], context.cabinet_yaw
    )
    basket_orientation = context.basket_model["stable_orientation_wxyz"]
    cabinet_joints = context.cabinet_model["articulation_joints"]
    objects = [
        {
            "object_id": "cabinet_1",
            "category": "cabinet",
            "asset_id": config["cabinet"]["asset_id"],
            "model_id": int(config["cabinet"]["model_id"]),
            "load_type": "urdf",
            "stable_pose_id": context.cabinet_model["stable_pose_id"],
            "stable_orientation_wxyz": context.cabinet_model["stable_orientation_wxyz"],
            "dimensions_m": context.cabinet_model["dimensions_m"],
            "footprint_shape": context.cabinet_model["footprint_shape"],
            "support_margin_m": context.cabinet_model["support_margin_m"],
            "support_spawn_clearance_m": context.cabinet_model["support_spawn_clearance_m"],
            "z_policy": context.cabinet_model["z_policy"],
            "mesh_scale": context.cabinet_model["scale"],
            "collision_available": True,
            "source_files": source_file_list(context.cabinet_model),
            "grounding_score": 100.0,
            "grounding_reasons": ["exact category match", "measured adversarial fixture"],
            "pose": {
                "position_m": [cabinet_xy[0], cabinet_xy[1], table_height],
                "orientation_wxyz": cabinet_orientation.tolist(),
                "yaw_rad": context.cabinet_yaw,
            },
            "is_static": True,
            "support_relation": "on_table",
            "support_target": "table",
            "articulation_state": {
                "state": "partially_open",
                "open_fraction": 0.5,
                "joint_selector": "all_movable",
            },
            "articulation_joint_names": [item["name"] for item in cabinet_joints],
            "articulation_joint_limits": [
                [item["lower"], item["upper"]] for item in cabinet_joints
            ],
            "articulation_qpos": [qpos, qpos, qpos],
            "asset_provenance": "robotwin_catalog",
        },
        {
            "object_id": "basket_1",
            "category": "basket",
            "color": "yellow",
            "asset_id": config["basket"]["asset_id"],
            "model_id": int(config["basket"]["model_id"]),
            "load_type": "rigid",
            "stable_pose_id": context.basket_model["stable_pose_id"],
            "stable_orientation_wxyz": basket_orientation,
            "dimensions_m": context.basket_model["dimensions_m"],
            "interior_dimensions_m": context.basket_model["interior_dimensions_m"],
            "interior_floor_z_offset_m": context.basket_model["interior_floor_z_offset_m"],
            "footprint_shape": context.basket_model["footprint_shape"],
            "support_margin_m": context.basket_model["support_margin_m"],
            "support_spawn_clearance_m": context.basket_model["support_spawn_clearance_m"],
            "z_policy": context.basket_model["z_policy"],
            "mesh_scale": context.basket_model["scale"],
            "collision_available": True,
            "source_files": source_file_list(context.basket_model),
            "grounding_score": 100.0,
            "grounding_reasons": ["exact category match", "measured adversarial fixture"],
            "pose": {
                "position_m": [basket_xy[0], basket_xy[1], table_height],
                "orientation_wxyz": basket_orientation,
                "yaw_rad": 0.0,
            },
            "is_static": True,
            "support_relation": "on_table",
            "support_target": "table",
            "articulation_joint_names": [],
            "articulation_joint_limits": [],
            "articulation_qpos": [],
            "asset_provenance": "robotwin_catalog",
        },
    ]
    return {
        "scene_id": source_spec.scene_id,
        "request": source_spec.request,
        "frame": source_spec.frame.model_dump(mode="json"),
        "unit": "m",
        "seed": source_spec.seed,
        "workspace": source_spec.workspace.model_dump(mode="json"),
        "source_scene_spec_sha256": source_spec.digest(),
        "asset_catalog_sha256": catalog.digest(),
        "compiler_version": "measured-adversarial-probe-current-source",
        "objects": objects,
        "relations": relations,
        "solver_trace": {
            "algorithm": "bounded_rejection_backtracking_v1",
            "seed": source_spec.seed,
            "max_attempts_per_object": 96,
            "total_attempts": 2,
            "attempts": [
                {
                    "attempt": 1,
                    "object_id": "cabinet_1",
                    "candidate_xy_m": cabinet_xy.tolist(),
                    "yaw_rad": context.cabinet_yaw,
                    "accepted": True,
                    "reasons": [],
                },
                {
                    "attempt": 2,
                    "object_id": "basket_1",
                    "candidate_xy_m": basket_xy.tolist(),
                    "yaw_rad": 0.0,
                    "accepted": True,
                    "reasons": [],
                },
            ],
            "status": "pass",
        },
    }


def visible_pixels(camera: Any, identifiers: list[int]) -> int:
    segmentation = camera.get_picture("Segmentation")
    labels = segmentation[..., 1].astype(np.int64)
    return int(np.count_nonzero(np.isin(labels, identifiers)))


def actor_state(actor: Any, *, articulation: bool) -> dict[str, Any]:
    pose = actor.get_root_pose() if articulation else actor.pose
    position, orientation = pose_values(pose)
    qpos = np.asarray(actor.get_qpos(), dtype=float) if articulation else np.asarray([])
    return {
        "position_m": position.tolist(),
        "orientation_wxyz": orientation.tolist(),
        "qpos": qpos.tolist(),
    }


def run_unlocked_production_probe(
    config: dict[str, Any], context: Any
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Measure the current fixed-root loader without modifying its joint limits."""
    import sapien

    scene = sapien.Scene()
    scene.set_timestep(float(config["rollout"]["timestep_s"]))
    add_table(scene, context.table_height)
    target_qpos = float(config["scene"]["half_open_qpos_m"])
    cabinet = load_cabinet(scene, context, fixed_root=True, qpos=target_qpos)
    cabinet.set_name("cabinet_1")
    basket = load_basket(
        scene,
        context,
        dynamic=False,
        offset_xy=config["scene"]["adversarial_basket_offset_m"],
    )
    basket.set_name("basket_1")
    cabinet_names = {link.entity.name for link in cabinet.get_links()}
    rows: list[dict[str, Any]] = []
    for step in range(1, 61):
        scene.step()
        actual_qpos = np.asarray(cabinet.get_qpos(), dtype=float)
        direct = pair_contact_metrics(
            scene.get_contacts(),
            first_entity_names=cabinet_names,
            second_entity_names={"basket_1"},
            active_separation=0.001,
            penetration_separation=-0.002,
        )
        rows.append(
            {
                "step": step,
                "target_qpos_m": target_qpos,
                "actual_qpos_0_m": actual_qpos[0],
                "actual_qpos_1_m": actual_qpos[1],
                "actual_qpos_2_m": actual_qpos[2],
                "qpos_max_abs_error_m": float(np.max(np.abs(actual_qpos - target_qpos))),
                "direct_active_point_count": direct["active_point_count"],
                "direct_penetration_point_count": direct["penetration_point_count"],
                "direct_min_separation_m": direct["min_separation_m"],
            }
        )
    maximum_error = max(float(row["qpos_max_abs_error_m"]) for row in rows)
    summary = {
        "schema_version": "usg_env_quality_reproduction.validator_attack_unlocked.v1",
        "protocol": "current loader behavior: fixed root, movable joints, no joint-limit lock",
        "sample_steps": len(rows),
        "target_qpos_m": target_qpos,
        "initial_qpos_m": [target_qpos] * 3,
        "final_qpos_m": [rows[-1][f"actual_qpos_{index}_m"] for index in range(3)],
        "maximum_qpos_abs_error_m": maximum_error,
        "qpos_error_above_validator_threshold": maximum_error > 0.02,
        "direct_active_contact_steps": sum(
            int(row["direct_active_point_count"]) > 0 for row in rows
        ),
        "direct_penetration_steps": sum(
            int(row["direct_penetration_point_count"]) > 0 for row in rows
        ),
        "joint_lock_applied": False,
    }
    return rows, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = load_json(args.config.resolve())
    gen_env_root = Path(config["gen_env_root"])
    sys.path.insert(0, str(gen_env_root))

    from scene_gen.schema import ResolvedSceneSpec
    from scene_gen.validator import validate_resolved_scene

    runtime_module = import_runtime_module(gen_env_root)
    context = make_context(config)
    resolved = ResolvedSceneSpec.model_validate(resolved_scene_dict(config, context))
    unlocked_rows, unlocked_summary = run_unlocked_production_probe(config, context)

    import sapien

    scene = sapien.Scene()
    scene.set_timestep(float(config["rollout"]["timestep_s"]))
    scene.set_ambient_light([0.55, 0.55, 0.55])
    scene.add_directional_light([0.4, 0.6, -1.0], [1.0, 0.96, 0.90], shadow=True)
    add_table(scene, context.table_height)
    qpos = float(config["scene"]["half_open_qpos_m"])
    cabinet = load_cabinet(scene, context, fixed_root=True, qpos=qpos)
    cabinet.set_name("cabinet_1")
    lock_articulation_qpos(cabinet, qpos)
    basket = load_basket(
        scene,
        context,
        dynamic=False,
        offset_xy=config["scene"]["adversarial_basket_offset_m"],
    )
    basket.set_name("basket_1")
    camera = add_camera(scene, config)
    initial_states = {
        "cabinet_1": actor_state(cabinet, articulation=True),
        "basket_1": actor_state(basket, articulation=False),
    }
    generated_names = {"cabinet_1", "basket_1"}
    expected_targets = {"cabinet_1": "table", "basket_1": "table"}
    initial_summary = runtime_module.summarize_contacts(
        list(scene.get_contacts()), generated_names, expected_targets
    )
    cabinet_link_names = {link.entity.name for link in cabinet.get_links()}
    collision_link_names = {
        link.entity.name for link in cabinet.get_links() if list(link.get_collision_shapes())
    }

    samples: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    late_states: dict[str, dict[str, Any]] | None = None
    for step in range(1, 61):
        scene.step()
        if step == 31:
            late_states = {
                "cabinet_1": actor_state(cabinet, articulation=True),
                "basket_1": actor_state(basket, articulation=False),
            }
        contacts = list(scene.get_contacts())
        summary = runtime_module.summarize_contacts(contacts, generated_names, expected_targets)
        summaries.append(summary)
        direct = pair_contact_metrics(
            contacts,
            first_entity_names=cabinet_link_names,
            second_entity_names={"basket_1"},
            active_separation=0.001,
            penetration_separation=-0.002,
        )
        samples.append(
            {
                "step": step,
                "direct_contact_records": direct["contact_records"],
                "direct_active_point_count": direct["active_point_count"],
                "direct_penetration_point_count": direct["penetration_point_count"],
                "direct_min_separation_m": direct["min_separation_m"],
                "runtime_robot_collision_count": summary["robot_collision_count"],
                "runtime_cabinet_unexpected_target_count": len(
                    summary["unexpected_targets_by_object"]["cabinet_1"]
                ),
                "runtime_basket_unexpected_target_count": len(
                    summary["unexpected_targets_by_object"]["basket_1"]
                ),
                "runtime_cabinet_penetration_count": summary["penetration_by_object"]["cabinet_1"],
                "runtime_basket_penetration_count": summary["penetration_by_object"]["basket_1"],
            }
        )
    final_states = {
        "cabinet_1": actor_state(cabinet, articulation=True),
        "basket_1": actor_state(basket, articulation=False),
    }
    if late_states is None:
        raise AssertionError("late-window states were not measured")

    scene.update_render()
    camera.take_picture()
    image = (np.clip(camera.get_picture("Color")[..., :3], 0.0, 1.0) * 255).round().astype(np.uint8)
    frame_path = ROOT / "media" / "frames" / "validator-attack-scene.png"
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(image).save(frame_path)
    cabinet_ids = [int(link.entity.per_scene_id) for link in cabinet.get_links()]
    basket_ids = [int(basket.per_scene_id)]
    visibility = {
        "cabinet_1": visible_pixels(camera, cabinet_ids),
        "basket_1": visible_pixels(camera, basket_ids),
    }
    final_summary = summaries[-1]
    final_runtime_name_pairs: list[list[str]] = []
    final_entity_name_pairs: list[list[str]] = []
    for contact in scene.get_contacts():
        bodies = list(getattr(contact, "bodies", ()))
        if len(bodies) != 2:
            continue
        entity_names = [body_entity_name(body) for body in bodies]
        if not (
            "basket_1" in entity_names
            and any(name in cabinet_link_names for name in entity_names)
        ):
            continue
        final_entity_name_pairs.append(entity_names)
        final_runtime_name_pairs.append(
            [runtime_module.contact_body_name(body) for body in bodies]
        )
    active_steps = sum(row["direct_active_point_count"] > 0 for row in samples)
    penetration_steps = sum(row["direct_penetration_point_count"] > 0 for row in samples)
    support_hits = {
        name: sum(bool(item["support_by_object"][name]) for item in summaries)
        for name in generated_names
    }
    unexpected_hits = {
        name: sum(bool(item["unexpected_targets_by_object"][name]) for item in summaries)
        for name in generated_names
    }
    cabinet_box = articulation_collision_aabb(cabinet)
    basket_box = entity_collision_aabb(basket)

    runtime_objects: dict[str, Any] = {}
    resolved_by_id = {item.object_id: item for item in resolved.objects}
    for name in ("cabinet_1", "basket_1"):
        item = resolved_by_id[name]
        before = initial_states[name]
        after = final_states[name]
        late = late_states[name]
        before_position = np.asarray(before["position_m"], dtype=float)
        after_position = np.asarray(after["position_m"], dtype=float)
        late_position = np.asarray(late["position_m"], dtype=float)
        loader_translation_offset = (
            before_position - np.asarray(item.pose.position_m, dtype=float)
            if item.load_type == "urdf"
            else np.zeros(3, dtype=float)
        )
        logical_final_position = after_position - loader_translation_offset
        target_qpos = np.asarray(item.articulation_qpos, dtype=float)
        final_qpos = np.asarray(after["qpos"], dtype=float)
        articulation_error = (
            float(np.max(np.abs(final_qpos - target_qpos)))
            if len(target_qpos) and len(final_qpos) == len(target_qpos)
            else None
        )
        late_translation = float(np.linalg.norm(after_position - late_position))
        late_rotation = quaternion_angle_deg(
            after["orientation_wxyz"], late["orientation_wxyz"]
        )
        support_target = final_summary["observed_support_targets"][name]
        if support_target is None and support_hits[name] > 0:
            support_target = "table"
        runtime_objects[name] = {
            "initial_pose": before,
            "final_pose": after,
            "translation_drift_m": float(np.linalg.norm(after_position - before_position)),
            "rotation_drift_deg": quaternion_angle_deg(
                after["orientation_wxyz"], before["orientation_wxyz"]
            ),
            "resolved_translation_error_m": float(
                np.linalg.norm(logical_final_position - np.asarray(item.pose.position_m))
            ),
            "loader_translation_offset_m": loader_translation_offset.tolist(),
            "resolved_rotation_error_deg": quaternion_angle_deg(
                after["orientation_wxyz"], list(item.pose.orientation_wxyz)
            ),
            "late_window_translation_m": late_translation,
            "late_window_rotation_deg": late_rotation,
            "visible_pixels": visibility[name],
            "penetration_count": final_summary["penetration_by_object"][name],
            "still_moving": late_translation > 0.001 or late_rotation > 0.5,
            "support_contact": final_summary["support_by_object"][name] or support_hits[name] > 0,
            "support_contact_fraction": support_hits[name] / len(summaries),
            "unexpected_contact_fraction": unexpected_hits[name] / len(summaries),
            "unexpected_contact_targets": final_summary["unexpected_targets_by_object"][name],
            "support_mode": "fixed_static_pose",
            "support_target": support_target,
            "support_footprint_margin_m": None,
            "inside_contained": None,
            "dropped": bool(after_position[2] < resolved.workspace.table_height_m - 0.03),
            "articulation_target_qpos": list(item.articulation_qpos),
            "articulation_final_qpos": after["qpos"],
            "articulation_max_abs_error": articulation_error,
        }
    runtime_evidence = {
        "schema_version": "robotwin.scene_runtime_evidence.v2",
        "scene_id": resolved.scene_id,
        "resolved_scene_sha256": resolved.digest(),
        "status": "pass",
        "robot_initial_collision_count": initial_summary["robot_collision_count"],
        "robot_initial_collision_pairs": initial_summary["robot_collision_pairs"],
        "robot_final_collision_count": final_summary["robot_collision_count"],
        "robot_final_collision_pairs": final_summary["robot_collision_pairs"],
        "objects": runtime_objects,
        "relations": {},
        "video_frame_count": 0,
        "unique_video_frame_count": 0,
        "measurement_protocol": {
            "name": "locked-ownership-isolation",
            "joint_lock_applied": True,
            "qpos_lock_tolerance_m": 1e-7,
            "cabinet_joint_limits_m": np.asarray(cabinet.get_qlimits(), dtype=float).tolist(),
            "runtime_fields": "computed from measured initial, late-window, and final states",
        },
    }
    validation = validate_resolved_scene(
        resolved,
        runtime_evidence=runtime_evidence,
        require_runtime=True,
    )

    ownership = {
        "logical_object_ids": sorted(generated_names),
        "cabinet_articulation_name": cabinet.get_name(),
        "cabinet_link_entity_names": sorted(cabinet_link_names),
        "cabinet_collision_link_entity_names": sorted(collision_link_names),
        "runtime_generated_names": sorted(generated_names),
        "collision_links_exactly_matching_a_logical_object": sorted(
            collision_link_names & generated_names
        ),
        "cabinet_collision_link_ownership_coverage": (
            len(collision_link_names & generated_names) / len(collision_link_names)
        ),
        "final_direct_contact_entity_name_pairs": final_entity_name_pairs,
        "final_direct_contact_runtime_name_pairs": final_runtime_name_pairs,
        "runtime_names_on_direct_contacts_matching_logical_objects": sorted(
            {
                name
                for pair in final_runtime_name_pairs
                for name in pair
                if name in generated_names
            }
        ),
        "observed_final_contact_pairs": final_summary["records"],
        "runtime_final_robot_collision_pairs": final_summary["robot_collision_pairs"],
    }
    attack_summary = {
        "status": validation["status"],
        "fail_count": validation["fail_count"],
        "sample_steps": len(samples),
        "direct_cabinet_basket_active_contact_steps": active_steps,
        "direct_cabinet_basket_penetration_steps": penetration_steps,
        "minimum_direct_separation_m": min(
            row["direct_min_separation_m"]
            for row in samples
            if row["direct_min_separation_m"] is not None
        ),
        "initial_robot_collision_count": initial_summary["robot_collision_count"],
        "final_robot_collision_count": final_summary["robot_collision_count"],
        "final_direct_contact_records": samples[-1]["direct_contact_records"],
        "final_runtime_retained_contact_records": len(final_summary["records"]),
        "measurement_protocol": "locked-ownership-isolation",
        "joint_lock_applied": True,
        "qpos_lock_tolerance_m": 1e-7,
        "measured_initial_cabinet_qpos_m": initial_states["cabinet_1"]["qpos"],
        "measured_final_cabinet_qpos_m": final_states["cabinet_1"]["qpos"],
        "measured_cabinet_qpos_max_abs_error_m": runtime_objects["cabinet_1"][
            "articulation_max_abs_error"
        ],
        "unlocked_production_probe": unlocked_summary,
        "validator_checks_final_robot_collision": any(
            "robot_final" in item["name"] for item in validation["checks"]
        ),
        "validator_on_table_unexpected_contact_checks": [
            item["name"]
            for item in validation["checks"]
            if item["name"].startswith("no_unexpected_support_contact")
        ],
        "visibility_pixels": visibility,
        "cabinet_physical_aabb_m": cabinet_box.tolist(),
        "basket_physical_aabb_m": basket_box.tolist(),
        "resolved_scene_sha256": resolved.digest(),
        "runtime_evidence_sha256": hashlib.sha256(
            json.dumps(runtime_evidence, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "validator_source_sha256": sha256_file(gen_env_root / "scene_gen" / "validator.py"),
        "runtime_source_sha256": sha256_file(gen_env_root / "script" / "run_scene_runtime.py"),
    }

    raw_dir = ROOT / "data" / "raw"
    write_json(raw_dir / "validator_attack_resolved_scene.json", resolved.canonical_dict())
    write_json(raw_dir / "validator_attack_runtime_evidence.json", runtime_evidence)
    write_json(raw_dir / "validator_attack_validation_report.json", validation)
    write_json(raw_dir / "validator_attack_body_ownership.json", ownership)
    write_json(raw_dir / "validator_attack_summary.json", attack_summary)
    write_csv(raw_dir / "validator_attack_contact_samples.csv", samples)
    write_json(raw_dir / "validator_attack_unlocked_qpos.json", unlocked_summary)
    write_csv(raw_dir / "validator_attack_unlocked_qpos.csv", unlocked_rows)

    no_overlap = next(
        item for item in validation["checks"] if item["name"] == "no_overlap:basket_1:cabinet_1"
    )
    if active_steps != len(samples):
        raise AssertionError(f"physical contact was not persistent: {active_steps}/{len(samples)}")
    if initial_summary["robot_collision_count"] != 0:
        raise AssertionError("initial pre-step query unexpectedly contains a robot collision")
    if not final_entity_name_pairs:
        raise AssertionError("fresh static query contains no direct cabinet-basket contact")
    if any(name in generated_names for pair in final_runtime_name_pairs for name in pair):
        raise AssertionError("runtime contact naming unexpectedly maps to a logical object")
    if final_summary["records"] or final_summary["robot_collision_count"]:
        raise AssertionError("unowned contact was unexpectedly retained by runtime summarization")
    if any(final_summary["penetration_by_object"].values()):
        raise AssertionError("unowned penetration was unexpectedly attributed to a logical object")
    if no_overlap["status"] != "pass":
        raise AssertionError("proxy geometry did not pass")
    measured_qpos_error = runtime_objects["cabinet_1"]["articulation_max_abs_error"]
    if measured_qpos_error is None or measured_qpos_error > 1e-7:
        raise AssertionError(f"joint lock did not preserve qpos: {measured_qpos_error}")
    if validation["status"] != "pass":
        failed = [item["name"] for item in validation["checks"] if item["status"] == "fail"]
        raise AssertionError(f"validator attack did not pass: {failed}")
    print(
        f"PASS validator={validation['status']} active_steps={active_steps}/{len(samples)} "
        f"direct_penetration_steps={penetration_steps} retained_contacts="
        f"{len(final_summary['records'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
