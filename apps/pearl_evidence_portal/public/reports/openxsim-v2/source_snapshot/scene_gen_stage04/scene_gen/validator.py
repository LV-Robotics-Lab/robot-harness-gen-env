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


def _check(checks: list[dict[str, Any]], name: str, status: str, evidence: Any) -> None:
    checks.append({"name": name, "status": status, "evidence": evidence})


def _aabb(item: Any) -> tuple[float, float, float, float]:
    width, depth, _ = item.dimensions_m
    yaw = item.pose.yaw_rad
    half_x = 0.5 * (abs(math.cos(yaw)) * width + abs(math.sin(yaw)) * depth)
    half_y = 0.5 * (abs(math.sin(yaw)) * width + abs(math.cos(yaw)) * depth)
    x, y, _ = item.pose.position_m
    return (x - half_x, x + half_x, y - half_y, y + half_y)


def _relation_pass(relation: Any, source: Any, target: Any) -> tuple[bool, dict[str, float]]:
    source_box = _aabb(source)
    target_box = _aabb(target)
    distance = math.dist(source.pose.position_m[:2], target.pose.position_m[:2])
    gap = 0.015
    if relation.relation == RelationType.LEFT_OF:
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
    return passed, {"center_distance_m": distance, "source_aabb": source_box, "target_aabb": target_box}


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
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    objects = {item.object_id: item for item in resolved.objects}
    x_bounds = resolved.workspace.x_bounds_m
    y_bounds = resolved.workspace.y_bounds_m
    for item in resolved.objects:
        box = _aabb(item)
        in_bounds = box[0] >= x_bounds[0] and box[1] <= x_bounds[1] and box[2] >= y_bounds[0] and box[3] <= y_bounds[1]
        _check(checks, f"workspace_bounds:{item.object_id}", "pass" if in_bounds else "fail", {"aabb": box})
        on_table = abs(item.pose.position_m[2] - resolved.workspace.table_height_m) <= max(0.001, item.dimensions_m[2] / 2.0)
        _check(
            checks,
            f"table_support_height:{item.object_id}",
            "pass" if on_table else "fail",
            {"z_m": item.pose.position_m[2], "table_height_m": resolved.workspace.table_height_m},
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
    for index, first in enumerate(ordered):
        first_box = _aabb(first)
        for second in ordered[index + 1:]:
            second_box = _aabb(second)
            separated = (
                first_box[1] + 0.005 <= second_box[0]
                or second_box[1] + 0.005 <= first_box[0]
                or first_box[3] + 0.005 <= second_box[2]
                or second_box[3] + 0.005 <= first_box[2]
            )
            _check(
                checks,
                f"no_overlap:{first.object_id}:{second.object_id}",
                "pass" if separated else "fail",
                {"first_aabb": first_box, "second_aabb": second_box},
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
        for item in resolved.objects:
            evidence = (runtime_evidence.get("objects") or {}).get(item.object_id) or {}
            drift = evidence.get("translation_drift_m")
            rotation = evidence.get("rotation_drift_deg")
            visibility = evidence.get("visible_pixels")
            penetration = evidence.get("penetration_count")
            moving = evidence.get("still_moving")
            support_contact = evidence.get("support_contact")
            support_mode = evidence.get("support_mode")
            dropped = evidence.get("dropped")
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
            _check(
                checks,
                f"support_contact:{item.object_id}",
                "pass"
                if support_contact is True
                or (item.is_static and support_mode == "fixed_static_pose" and dropped is False)
                else "fail",
                {
                    "raw_contact": support_contact,
                    "mode": support_mode,
                    "is_static": item.is_static,
                },
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
