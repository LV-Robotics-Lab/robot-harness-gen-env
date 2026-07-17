"""Geometric, replay, physical, and visibility validation for resolved scenes."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from .builder import verify_package
from .catalog import AssetCatalog, load_catalog
from .schema import RelationType, ResolvedSceneSpec
from .support_geometry import (
    footprint_2d,
    support_footprint_margin,
    support_surface_dimensions,
    support_surface_shape,
    support_surface_z,
)


def _check(checks: list[dict[str, Any]], name: str, status: str, evidence: Any) -> None:
    checks.append({"name": name, "status": status, "evidence": evidence})


def _aabb(item: Any) -> tuple[float, float, float, float]:
    footprint = footprint_2d(item.dimensions_m, item.pose.yaw_rad, item.footprint_shape)
    x, y, _ = item.pose.position_m
    return (
        x - footprint.half_x,
        x + footprint.half_x,
        y - footprint.half_y,
        y + footprint.half_y,
    )


def _vertical_bounds(item: Any) -> tuple[float, float]:
    height = item.dimensions_m[2]
    pose_z = item.pose.position_m[2]
    bottom = pose_z if item.z_policy == "origin_on_table" else pose_z - height / 2.0
    return bottom, bottom + height


def _aabb3(item: Any) -> tuple[float, float, float, float, float, float]:
    horizontal = _aabb(item)
    vertical = _vertical_bounds(item)
    return (*horizontal, *vertical)


def _interior_aabb(item: Any) -> tuple[float, float, float, float, float, float] | None:
    if item.interior_dimensions_m is None:
        return None
    width, depth, height = item.interior_dimensions_m
    yaw = item.pose.yaw_rad
    half_x = 0.5 * (abs(math.cos(yaw)) * width + abs(math.sin(yaw)) * depth)
    half_y = 0.5 * (abs(math.sin(yaw)) * width + abs(math.cos(yaw)) * depth)
    x, y, _ = item.pose.position_m
    bottom, _ = _vertical_bounds(item)
    interior_bottom = bottom + (item.interior_floor_z_offset_m or 0.0)
    return (
        x - half_x,
        x + half_x,
        y - half_y,
        y + half_y,
        interior_bottom,
        interior_bottom + height,
    )


def _support_surface(item: Any) -> tuple[str, tuple[float, float], float]:
    bottom, top = _vertical_bounds(item)
    return (
        support_surface_shape(item.footprint_shape, item.support_surface_shape),
        support_surface_dimensions(item.dimensions_m, item.support_surface_dimensions_m),
        support_surface_z(
            bottom_z=bottom,
            top_z=top,
            explicit_offset_m=item.support_surface_z_offset_m,
        ),
    )


def _relation_pass(relation: Any, source: Any, target: Any) -> tuple[bool, dict[str, Any]]:
    source_box = _aabb(source)
    target_box = _aabb(target)
    distance = math.dist(source.pose.position_m[:2], target.pose.position_m[:2])
    gap = 0.015
    inside_margin: float | None = None
    if relation.relation == RelationType.ON_TOP_OF:
        source_3d = _aabb3(source)
        target_3d = _aabb3(target)
        surface_shape, surface_dimensions, surface_z = _support_surface(target)
        footprint_margin = support_footprint_margin(
            source_dimensions_m=source.dimensions_m,
            source_yaw=source.pose.yaw_rad,
            source_shape=source.footprint_shape,
            target_surface_dimensions_m=surface_dimensions,
            target_yaw=target.pose.yaw_rad,
            target_surface_shape=surface_shape,
            dx=source.pose.position_m[0] - target.pose.position_m[0],
            dy=source.pose.position_m[1] - target.pose.position_m[1],
        )
        expected_bottom_z = surface_z + target.support_spawn_clearance_m
        passed = (
            abs(source_3d[4] - expected_bottom_z) <= 0.003
            and footprint_margin + 1e-9 >= target.support_margin_m
        )
    elif relation.relation == RelationType.INSIDE:
        source_3d = _aabb3(source)
        interior = _interior_aabb(target)
        if target.interior_dimensions_m is not None:
            inside_margin = support_footprint_margin(
                source_dimensions_m=source.dimensions_m,
                source_yaw=source.pose.yaw_rad,
                source_shape=source.footprint_shape,
                target_surface_dimensions_m=target.interior_dimensions_m[:2],
                target_yaw=target.pose.yaw_rad,
                target_surface_shape="box",
                dx=source.pose.position_m[0] - target.pose.position_m[0],
                dy=source.pose.position_m[1] - target.pose.position_m[1],
            )
        passed = (
            interior is not None
            and inside_margin is not None
            and inside_margin >= -1e-9
            and source_3d[4] >= interior[4] - 1e-9
            and source_3d[5] <= interior[5] + 1e-9
        )
    elif relation.relation == RelationType.LEFT_OF:
        passed = source_box[1] + gap <= target_box[0] + 1e-9
    elif relation.relation == RelationType.RIGHT_OF:
        passed = source_box[0] - gap >= target_box[1] - 1e-9
    elif relation.relation == RelationType.FRONT_OF:
        passed = source_box[2] - gap >= target_box[3] - 1e-9
    elif relation.relation == RelationType.BEHIND:
        passed = source_box[3] + gap <= target_box[2] + 1e-9
    elif relation.relation == RelationType.NEAR:
        passed = distance <= (relation.max_distance_m or 0.25) + 1e-9
    elif relation.relation == RelationType.DISTANCE_AT_LEAST:
        passed = distance + 1e-9 >= (relation.min_distance_m or 0.0)
    else:
        passed = True
    evidence: dict[str, Any] = {
        "center_distance_m": distance,
        "source_aabb": source_box,
        "target_aabb": target_box,
    }
    if relation.relation in {RelationType.ON_TOP_OF, RelationType.INSIDE}:
        evidence["source_aabb_3d"] = _aabb3(source)
        evidence["target_aabb_3d"] = _aabb3(target)
        evidence["target_interior_aabb_3d"] = _interior_aabb(target)
    if relation.relation == RelationType.INSIDE:
        evidence["inside_footprint_margin_m"] = inside_margin
    if relation.relation == RelationType.ON_TOP_OF:
        evidence.update(
            {
                "support_surface_shape": surface_shape,
                "support_surface_dimensions_m": surface_dimensions,
                "support_surface_z_m": surface_z,
                "spawn_clearance_m": target.support_spawn_clearance_m,
                "expected_source_bottom_z_m": expected_bottom_z,
                "support_footprint_margin_m": footprint_margin,
                "required_support_margin_m": target.support_margin_m,
            }
        )
    return passed, evidence


def validate_resolved_scene(
    resolved: ResolvedSceneSpec,
    *,
    catalog: AssetCatalog | None = None,
    package_root: Path | None = None,
    runtime_evidence: dict[str, Any] | None = None,
    require_runtime: bool = False,
    min_visible_pixels: int = 64,
    max_translation_drift_m: float = 0.02,
    max_rotation_drift_deg: float = 3.0,
    max_resolved_translation_error_m: float = 0.02,
    max_resolved_rotation_error_deg: float = 5.0,
    min_support_contact_fraction: float = 0.8,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    objects = {item.object_id: item for item in resolved.objects}
    supports = {
        relation.source: relation
        for relation in resolved.relations
        if relation.relation in {
            RelationType.ON_TABLE,
            RelationType.ON_TOP_OF,
            RelationType.INSIDE,
        }
    }
    x_bounds = resolved.workspace.x_bounds_m
    y_bounds = resolved.workspace.y_bounds_m
    for item in resolved.objects:
        box = _aabb(item)
        in_bounds = box[0] >= x_bounds[0] and box[1] <= x_bounds[1] and box[2] >= y_bounds[0] and box[3] <= y_bounds[1]
        _check(checks, f"workspace_bounds:{item.object_id}", "pass" if in_bounds else "fail", {"aabb": box})
        support = supports[item.object_id]
        bottom, _ = _vertical_bounds(item)
        on_table = (
            support.relation != RelationType.ON_TABLE
            or abs(bottom - resolved.workspace.table_height_m) <= 0.001
        )
        _check(
            checks,
            f"table_support_height:{item.object_id}",
            "pass" if on_table else "fail",
            {
                "bottom_z_m": bottom,
                "table_height_m": resolved.workspace.table_height_m,
                "support_relation": support.relation.value,
            },
        )
        existing_sources = [Path(path).is_file() for path in item.source_files]
        if catalog is None:
            source_status = "not_applicable"
        else:
            source_status = "pass" if existing_sources and all(existing_sources) else "fail"
        _check(
            checks,
            f"real_asset_files:{item.object_id}",
            source_status,
            {"paths": list(item.source_files), "exists": existing_sources},
        )

    ordered = sorted(resolved.objects, key=lambda item: item.object_id)
    support_pairs = {
        frozenset((relation.source, relation.target))
        for relation in resolved.relations
        if relation.relation in {RelationType.ON_TOP_OF, RelationType.INSIDE}
    }
    for index, first in enumerate(ordered):
        first_box = _aabb3(first)
        for second in ordered[index + 1:]:
            second_box = _aabb3(second)
            separated = (
                first_box[1] + 0.005 <= second_box[0]
                or second_box[1] + 0.005 <= first_box[0]
                or first_box[3] + 0.005 <= second_box[2]
                or second_box[3] + 0.005 <= first_box[2]
                or first_box[5] + 0.002 <= second_box[4]
                or second_box[5] + 0.002 <= first_box[4]
            )
            expected_support = frozenset((first.object_id, second.object_id)) in support_pairs
            _check(
                checks,
                f"no_overlap:{first.object_id}:{second.object_id}",
                "pass" if separated or expected_support else "fail",
                {
                    "first_aabb_3d": first_box,
                    "second_aabb_3d": second_box,
                    "expected_support_pair": expected_support,
                },
            )
    for relation in resolved.relations:
        if relation.relation == RelationType.ON_TABLE:
            _check(checks, f"relation:on_table:{relation.source}", "pass", {"target": "table"})
            continue
        passed, evidence = _relation_pass(relation, objects[relation.source], objects[relation.target])
        _check(
            checks,
            f"relation:{relation.relation.value}:{relation.source}:{relation.target}",
            "pass" if passed else "fail",
            evidence,
        )

    replayed = ResolvedSceneSpec.model_validate_json(
        json.dumps(resolved.canonical_dict(), sort_keys=True, ensure_ascii=False)
    )
    _check(
        checks,
        "resolved_only_roundtrip",
        "pass" if replayed.digest() == resolved.digest() else "fail",
        {"before": resolved.digest(), "after": replayed.digest()},
    )
    if package_root is not None:
        package_report = verify_package(package_root)
        _check(checks, "package_manifest", package_report["status"], package_report)

    if runtime_evidence is None:
        _check(checks, "runtime_evidence", "fail" if require_runtime else "not_run", {"required": require_runtime})
    else:
        runtime_status = runtime_evidence.get("status") == "pass"
        _check(checks, "runtime_status", "pass" if runtime_status else "fail", runtime_evidence.get("error"))
        _check(
            checks,
            "robot_initial_collision",
            "pass" if runtime_evidence.get("robot_initial_collision_count") == 0 else "fail",
            runtime_evidence.get("robot_initial_collision_count"),
        )
        video_frame_count = runtime_evidence.get("video_frame_count", 0)
        if isinstance(video_frame_count, int) and video_frame_count > 0:
            unique_video_frame_count = runtime_evidence.get("unique_video_frame_count")
            _check(
                checks,
                "observer_video_frame_count",
                "pass" if video_frame_count >= 3 else "fail",
                {"frames": video_frame_count, "minimum": 3},
            )
            _check(
                checks,
                "observer_video_unique_frames",
                "pass"
                if isinstance(unique_video_frame_count, int)
                and unique_video_frame_count >= 3
                else "fail",
                {"unique_frames": unique_video_frame_count, "minimum": 3},
            )
        for item in resolved.objects:
            evidence = (runtime_evidence.get("objects") or {}).get(item.object_id) or {}
            drift = evidence.get("translation_drift_m")
            rotation = evidence.get("rotation_drift_deg")
            visibility = evidence.get("visible_pixels")
            penetration = evidence.get("penetration_count")
            moving = evidence.get("still_moving")
            support_contact = evidence.get("support_contact")
            support_mode = evidence.get("support_mode")
            support_target = evidence.get("support_target")
            support_contact_fraction = evidence.get("support_contact_fraction")
            support_margin = evidence.get("support_footprint_margin_m")
            inside_contained = evidence.get("inside_contained")
            dropped = evidence.get("dropped")
            articulation_error = evidence.get("articulation_max_abs_error")
            resolved_translation_error = evidence.get("resolved_translation_error_m")
            resolved_rotation_error = evidence.get("resolved_rotation_error_deg")
            _check(
                checks,
                f"translation_drift:{item.object_id}",
                "pass" if isinstance(drift, (int, float)) and drift <= max_translation_drift_m else "fail",
                drift,
            )
            _check(
                checks,
                f"rotation_drift:{item.object_id}",
                "pass" if isinstance(rotation, (int, float)) and rotation <= max_rotation_drift_deg else "fail",
                rotation,
            )
            _check(
                checks,
                f"resolved_translation_error:{item.object_id}",
                "pass"
                if isinstance(resolved_translation_error, (int, float))
                and resolved_translation_error <= max_resolved_translation_error_m
                else "fail",
                {
                    "error_m": resolved_translation_error,
                    "threshold_m": max_resolved_translation_error_m,
                },
            )
            _check(
                checks,
                f"resolved_rotation_error:{item.object_id}",
                "pass"
                if isinstance(resolved_rotation_error, (int, float))
                and resolved_rotation_error <= max_resolved_rotation_error_deg
                else "fail",
                {
                    "error_deg": resolved_rotation_error,
                    "threshold_deg": max_resolved_rotation_error_deg,
                },
            )
            _check(
                checks,
                f"penetration:{item.object_id}",
                "pass" if penetration == 0 else "fail",
                penetration,
            )
            _check(
                checks,
                f"settled:{item.object_id}",
                "pass" if moving is False else "fail",
                moving,
            )
            fixed_table_support = (
                item.is_static
                and item.support_relation == RelationType.ON_TABLE
                and support_mode == "fixed_static_pose"
                and dropped is False
            )
            physical_support = (
                support_contact is True
                and support_target == item.support_target
                and isinstance(support_contact_fraction, (int, float))
                and support_contact_fraction >= min_support_contact_fraction
            )
            _check(
                checks,
                f"support_contact:{item.object_id}",
                "pass" if fixed_table_support or physical_support else "fail",
                {
                    "raw_contact": support_contact,
                    "mode": support_mode,
                    "is_static": item.is_static,
                    "expected_target": item.support_target,
                    "observed_target": support_target,
                    "contact_fraction": support_contact_fraction,
                    "minimum_contact_fraction": min_support_contact_fraction,
                },
            )
            if item.support_relation == RelationType.ON_TOP_OF:
                required_margin = objects[item.support_target].support_margin_m
                _check(
                    checks,
                    f"runtime_support_margin:{item.object_id}",
                    "pass"
                    if isinstance(support_margin, (int, float))
                    and support_margin + 1e-9 >= required_margin
                    else "fail",
                    {
                        "margin_m": support_margin,
                        "required_margin_m": required_margin,
                    },
                )
            if item.support_relation == RelationType.INSIDE:
                _check(
                    checks,
                    f"runtime_inside_containment:{item.object_id}",
                    "pass" if inside_contained is True else "fail",
                    {"inside_contained": inside_contained},
                )
            _check(
                checks,
                f"not_dropped:{item.object_id}",
                "pass" if dropped is False else "fail",
                dropped,
            )
            _check(
                checks,
                f"head_visibility:{item.object_id}",
                "pass" if isinstance(visibility, int) and visibility >= min_visible_pixels else "fail",
                {"pixels": visibility, "threshold": min_visible_pixels},
            )
            if item.articulation_qpos:
                _check(
                    checks,
                    f"articulation_qpos:{item.object_id}",
                    "pass"
                    if isinstance(articulation_error, (int, float)) and articulation_error <= 0.02
                    else "fail",
                    {
                        "max_abs_error": articulation_error,
                        "target_qpos": list(item.articulation_qpos),
                    },
                )

    fail_count = sum(item["status"] == "fail" for item in checks)
    not_run_count = sum(item["status"] == "not_run" for item in checks)
    status = "fail" if fail_count else "incomplete" if not_run_count else "pass"
    return {
        "schema_version": "robotwin.scene_validation.v1",
        "scene_id": resolved.scene_id,
        "resolved_scene_sha256": resolved.digest(),
        "status": status,
        "fail_count": fail_count,
        "not_run_count": not_run_count,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a ResolvedSceneSpec and optional runtime evidence.")
    parser.add_argument("--resolved-scene", required=True)
    parser.add_argument("--asset-catalog")
    parser.add_argument("--package-root")
    parser.add_argument("--runtime-evidence")
    parser.add_argument("--require-runtime", action="store_true")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    resolved = ResolvedSceneSpec.model_validate_json(Path(args.resolved_scene).read_text(encoding="utf-8"))
    catalog = load_catalog(Path(args.asset_catalog)) if args.asset_catalog else None
    runtime = json.loads(Path(args.runtime_evidence).read_text(encoding="utf-8")) if args.runtime_evidence else None
    report = validate_resolved_scene(
        resolved,
        catalog=catalog,
        package_root=Path(args.package_root) if args.package_root else None,
        runtime_evidence=runtime,
        require_runtime=args.require_runtime,
    )
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{report['status'].upper()} fail={report['fail_count']} not_run={report['not_run_count']}")
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
