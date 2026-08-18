#!/usr/bin/env python3
"""Repeat the full contact grid in reverse order and quantify scan-order sensitivity."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import numpy as np

from run_physics_experiments import (
    ROOT,
    body_entity_name,
    entity_collision_component,
    grid_values,
    load_basket,
    load_cabinet,
    load_json,
    lock_articulation_qpos,
    make_context,
    pair_contact_metrics,
    pose_values,
    write_csv,
    write_json,
)


def key(qpos: float, x_offset: float, y_offset: float) -> tuple[float, float, float]:
    return tuple(round(value, 9) for value in (qpos, x_offset, y_offset))


def bool_value(value: str) -> bool:
    if value not in {"True", "False"}:
        raise ValueError(f"unexpected boolean value: {value!r}")
    return value == "True"


def load_primary(path: Path) -> dict[tuple[float, float, float], dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    indexed = {
        key(float(row["qpos_m"]), float(row["basket_offset_x_m"]), float(row["basket_offset_y_m"])): row
        for row in rows
    }
    if len(indexed) != len(rows):
        raise ValueError("primary sweep contains duplicate grid keys")
    return indexed


def unlocked_joint_drift_probe(
    context: Any,
    config: dict[str, Any],
    x_values: np.ndarray,
    y_values: np.ndarray,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Reproduce the flawed protocol and measure how contacts mutate cabinet qpos."""
    import sapien

    sweep = config["contact_sweep"]
    qpos = float(config["scene"]["half_open_qpos_m"])
    scene = sapien.Scene()
    scene.set_timestep(1e-4)
    cabinet = load_cabinet(scene, context, fixed_root=True, qpos=qpos)
    cabinet_names = {link.entity.name for link in cabinet.get_links()}
    basket = load_basket(scene, context, dynamic=True, offset_xy=(1.0, 1.0))
    body = entity_collision_component(basket)
    body.set_disable_gravity(True)
    isolation_xy = context.cabinet_xy + np.asarray([1.0, 1.0])
    isolation_pose = sapien.Pose(
        [isolation_xy[0], isolation_xy[1], context.table_height],
        context.basket_model["stable_orientation_wxyz"],
    )
    rows: list[dict[str, Any]] = []
    step_index = 0
    for y_offset in y_values:
        for x_offset in x_values:
            basket.set_pose(isolation_pose)
            body.set_linear_velocity([0.0, 0.0, 0.0])
            body.set_angular_velocity([0.0, 0.0, 0.0])
            body.wake_up()
            scene.step()
            basket_xy = context.cabinet_xy + np.asarray([x_offset, y_offset])
            basket.set_pose(
                sapien.Pose(
                    [basket_xy[0], basket_xy[1], context.table_height],
                    context.basket_model["stable_orientation_wxyz"],
                )
            )
            body.set_linear_velocity([0.0, 0.0, 0.0])
            body.set_angular_velocity([0.0, 0.0, 0.0])
            body.wake_up()
            scene.step()
            step_index += 1
            actual_qpos = np.asarray(cabinet.get_qpos(), dtype=float)
            actual_qvel = np.asarray(cabinet.get_qvel(), dtype=float)
            metrics = pair_contact_metrics(
                scene.get_contacts(),
                first_entity_names=cabinet_names,
                second_entity_names={"basket"},
                active_separation=float(sweep["active_separation_m"]),
                penetration_separation=float(sweep["penetration_separation_m"]),
            )
            rows.append(
                {
                    "query_index": step_index,
                    "requested_qpos_m": qpos,
                    "basket_offset_x_m": float(x_offset),
                    "basket_offset_y_m": float(y_offset),
                    "actual_qpos_0_m": actual_qpos[0],
                    "actual_qpos_1_m": actual_qpos[1],
                    "actual_qpos_2_m": actual_qpos[2],
                    "qpos_max_abs_error_m": float(np.max(np.abs(actual_qpos - qpos))),
                    "qvel_norm_m_s": float(np.linalg.norm(actual_qvel)),
                    "oracle_contact": metrics["active_point_count"] > 0,
                    "oracle_penetration": metrics["penetration_point_count"] > 0,
                    "min_separation_m": metrics["min_separation_m"],
                }
            )
    threshold = 0.001
    first_exceedance = next(
        (row for row in rows if float(row["qpos_max_abs_error_m"]) > threshold), None
    )
    summary = {
        "schema_version": "usg_env_quality_reproduction.unlocked_joint_drift.v1",
        "purpose": "adversarial measurement-protocol failure probe; excluded from corrected rates",
        "requested_qpos_m": qpos,
        "query_count": len(rows),
        "maximum_qpos_abs_error_m": max(float(row["qpos_max_abs_error_m"]) for row in rows),
        "final_actual_qpos_m": [rows[-1][f"actual_qpos_{index}_m"] for index in range(3)],
        "first_error_above_1mm": first_exceedance,
        "joint_lock_applied": False,
    }
    return rows, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = load_json(args.config.resolve())
    context = make_context(config)
    sweep = config["contact_sweep"]
    x_values = grid_values(
        float(sweep["x_min_m"]), float(sweep["x_max_m"]), float(sweep["step_m"])
    )
    y_values = grid_values(
        float(sweep["y_min_m"]), float(sweep["y_max_m"]), float(sweep["step_m"])
    )
    active_threshold = float(sweep["active_separation_m"])
    penetration_threshold = float(sweep["penetration_separation_m"])
    primary = load_primary(ROOT / "data" / "raw" / "cabinet_basket_contact_sweep.csv")

    import sapien

    rows: list[dict[str, Any]] = []
    for qpos in reversed([float(value) for value in sweep["qpos_m"]]):
        dynamic_scene = sapien.Scene()
        dynamic_scene.set_timestep(1e-4)
        dynamic_cabinet = load_cabinet(dynamic_scene, context, fixed_root=True, qpos=qpos)
        lock_articulation_qpos(dynamic_cabinet, qpos)
        dynamic_basket = load_basket(
            dynamic_scene, context, dynamic=True, offset_xy=(1.0, 1.0)
        )
        dynamic_body = entity_collision_component(dynamic_basket)
        dynamic_body.set_disable_gravity(True)
        cabinet_names = {link.entity.name for link in dynamic_cabinet.get_links()}
        isolation_xy = context.cabinet_xy + np.asarray([1.0, 1.0])
        isolation_pose = sapien.Pose(
            [isolation_xy[0], isolation_xy[1], context.table_height],
            context.basket_model["stable_orientation_wxyz"],
        )

        static_scene = sapien.Scene()
        static_scene.set_timestep(1e-4)
        static_cabinet = load_cabinet(static_scene, context, fixed_root=True, qpos=qpos)
        lock_articulation_qpos(static_cabinet, qpos)
        static_basket = load_basket(
            static_scene, context, dynamic=False, offset_xy=(1.0, 1.0)
        )
        static_names = {link.entity.name for link in static_cabinet.get_links()}

        for y_offset in reversed(y_values):
            for x_offset in reversed(x_values):
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
                before, _ = pose_values(dynamic_basket.pose)
                dynamic_scene.step()
                after, _ = pose_values(dynamic_basket.pose)
                dynamic = pair_contact_metrics(
                    dynamic_scene.get_contacts(),
                    first_entity_names=cabinet_names,
                    second_entity_names={"basket"},
                    active_separation=active_threshold,
                    penetration_separation=penetration_threshold,
                )

                static_basket.set_pose(basket_pose)
                static_scene.step()
                static = pair_contact_metrics(
                    static_scene.get_contacts(),
                    first_entity_names=static_names,
                    second_entity_names={"basket"},
                    active_separation=active_threshold,
                    penetration_separation=penetration_threshold,
                )
                primary_row = primary[key(qpos, float(x_offset), float(y_offset))]
                reverse_contact = dynamic["active_point_count"] > 0
                reverse_penetration = dynamic["penetration_point_count"] > 0
                reverse_static = static["active_point_count"] > 0
                rows.append(
                    {
                        "qpos_m": qpos,
                        "basket_offset_x_m": float(x_offset),
                        "basket_offset_y_m": float(y_offset),
                        "primary_oracle_contact": bool_value(primary_row["oracle_contact"]),
                        "reverse_oracle_contact": reverse_contact,
                        "contact_label_match": (
                            bool_value(primary_row["oracle_contact"]) == reverse_contact
                        ),
                        "primary_oracle_penetration": bool_value(
                            primary_row["oracle_penetration"]
                        ),
                        "reverse_oracle_penetration": reverse_penetration,
                        "penetration_label_match": (
                            bool_value(primary_row["oracle_penetration"]) == reverse_penetration
                        ),
                        "primary_static_contact": bool_value(
                            primary_row["production_static_detects_contact"]
                        ),
                        "reverse_static_contact": reverse_static,
                        "static_label_match": (
                            bool_value(primary_row["production_static_detects_contact"])
                            == reverse_static
                        ),
                        "reverse_min_separation_m": dynamic["min_separation_m"],
                        "reverse_one_step_displacement_m": float(np.linalg.norm(after - before)),
                        "reverse_dynamic_cabinet_qpos_max_abs_error_m": float(
                            np.max(np.abs(np.asarray(dynamic_cabinet.get_qpos()) - qpos))
                        ),
                        "reverse_static_cabinet_qpos_max_abs_error_m": float(
                            np.max(np.abs(np.asarray(static_cabinet.get_qpos()) - qpos))
                        ),
                        "reverse_contact_body_names": "|".join(
                            sorted(
                                {
                                    body_entity_name(body)
                                    for contact in dynamic_scene.get_contacts()
                                    for body in getattr(contact, "bodies", ())
                                }
                            )
                        ),
                    }
                )

    expected = len(x_values) * len(y_values) * len(sweep["qpos_m"])
    if len(rows) != expected or len(primary) != expected:
        raise AssertionError(
            f"grid coverage mismatch: reverse={len(rows)} primary={len(primary)} expected={expected}"
        )
    fields = ("contact_label_match", "penetration_label_match", "static_label_match")
    summary = {
        "schema_version": "usg_env_quality_reproduction.sweep_sensitivity.v1",
        "primary_scan_order": "qpos, y ascending, x ascending",
        "sensitivity_scan_order": "qpos, y descending, x descending",
        "row_count": len(rows),
        "fresh_scene_scope": "new dynamic and static scenes per qpos, not per grid point",
        "contact_cache_protocol": (
            "before every query, teleport the basket to a non-contact isolation pose, "
            "advance one step, then teleport to the target and advance one measured step"
        ),
        "joint_state_protocol": (
            "set every active joint limit to [qpos, qpos] before scanning; record the "
            "post-step maximum absolute qpos error for every query"
        ),
        "label_mismatch_counts": {
            field: sum(not bool(row[field]) for row in rows) for field in fields
        },
        "label_agreement_rates": {
            field: sum(bool(row[field]) for row in rows) / len(rows) for field in fields
        },
        "maximum_post_step_qpos_error_m": {
            "dynamic": max(
                float(row["reverse_dynamic_cabinet_qpos_max_abs_error_m"]) for row in rows
            ),
            "static": max(
                float(row["reverse_static_cabinet_qpos_max_abs_error_m"]) for row in rows
            ),
        },
    }
    write_csv(ROOT / "data" / "raw" / "contact_sweep_reverse_order.csv", rows)
    write_json(ROOT / "data" / "raw" / "contact_sweep_sensitivity.json", summary)
    unlocked_rows, unlocked_summary = unlocked_joint_drift_probe(
        context, config, x_values, y_values
    )
    write_csv(ROOT / "data" / "raw" / "unlocked_joint_drift_probe.csv", unlocked_rows)
    write_json(ROOT / "data" / "raw" / "unlocked_joint_drift_probe.json", unlocked_summary)
    print(
        "PASS reverse_rows="
        f"{len(rows)} contact_mismatches={summary['label_mismatch_counts']['contact_label_match']} "
        f"penetration_mismatches={summary['label_mismatch_counts']['penetration_label_match']} "
        f"static_mismatches={summary['label_mismatch_counts']['static_label_match']} "
        f"unlocked_max_qpos_error_m={unlocked_summary['maximum_qpos_abs_error_m']:.6f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
