#!/usr/bin/env python3
"""Schema helpers and static validation for TabletopPlacementSpec v0."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

REQUIRED_TOP_LEVEL_FIELDS = [
    "schema_version",
    "placement_name",
    "stage",
    "language_prompt",
    "workspace",
    "objects",
]


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _catalog_entries(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["asset_id"]: entry for entry in catalog.get("entries", [])}


def _model_metadata(entry: dict[str, Any], model_id: int) -> dict[str, Any] | None:
    for metadata in entry.get("model_metadata", []):
        if metadata.get("model_id") == model_id:
            return metadata
    return None


def _add(checks: list[dict[str, Any]], name: str, status: str, message: str) -> None:
    checks.append({"name": name, "status": status, "message": message})


def _is_number_list(value: Any, length: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) == length
        and all(isinstance(item, (int, float)) for item in value)
    )


def _in_range(value: float, bounds: list[float]) -> bool:
    return bounds[0] <= value <= bounds[1]


def _xy_radius(entry: dict[str, Any], model_id: int) -> float | None:
    metadata = _model_metadata(entry, model_id)
    if not metadata:
        return None
    extents = metadata.get("approx_scaled_extents_m")
    if not _is_number_list(extents, 3):
        return None
    return max(float(extents[0]), float(extents[1])) / 2.0


def _containment_pairs(spec: dict[str, Any]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for relation in spec.get("relations", []):
        if not isinstance(relation, dict):
            continue
        relation_type = str(relation.get("type", "")).lower()
        source = relation.get("source")
        target = relation.get("target")
        if not source or not target:
            continue
        if relation_type in {"inside", "in", "contained_in", "within"}:
            pairs.add((str(source), str(target)))
        elif relation_type in {"contains", "container_for"}:
            pairs.add((str(target), str(source)))
    return pairs


def validate_placement_spec(
    spec: dict[str, Any],
    catalog: dict[str, Any],
    *,
    robotwin_root: str | None = None,
) -> dict[str, Any]:
    """Validate schema, catalog references, bounds, and approximate collisions."""

    checks: list[dict[str, Any]] = []
    entries = _catalog_entries(catalog)

    missing = [field for field in REQUIRED_TOP_LEVEL_FIELDS if field not in spec]
    if missing:
        _add(checks, "required_top_level_fields", "fail", f"Missing fields: {missing}")
    else:
        _add(checks, "required_top_level_fields", "pass", "All required top-level fields are present.")

    if spec.get("schema_version") != "robotwin.tabletop_placement.v0":
        _add(checks, "schema_version", "fail", "Expected robotwin.tabletop_placement.v0.")
    else:
        _add(checks, "schema_version", "pass", "Schema version is v0.")

    objects = spec.get("objects")
    if not isinstance(objects, list) or not objects:
        _add(checks, "objects_nonempty", "fail", "objects must be a non-empty list.")
        objects = []
    else:
        _add(checks, "objects_nonempty", "pass", f"Found {len(objects)} object(s).")

    object_ids = [obj.get("id") for obj in objects if isinstance(obj, dict)]
    if len(object_ids) != len(set(object_ids)):
        _add(checks, "object_ids_unique", "fail", "Object ids must be unique.")
    else:
        _add(checks, "object_ids_unique", "pass", "Object ids are unique.")

    bounds = spec.get("workspace", {}).get("bounds", {})
    bounds_ok = all(_is_number_list(bounds.get(axis), 2) for axis in ["x", "y", "z"])
    if bounds_ok:
        _add(checks, "workspace_bounds_shape", "pass", "Workspace bounds contain numeric x/y/z ranges.")
    else:
        _add(checks, "workspace_bounds_shape", "fail", "Workspace bounds must contain numeric x/y/z ranges.")

    prompt = str(spec.get("language_prompt", "")).lower()
    object_positions: list[tuple[dict[str, Any], dict[str, Any], list[float], float | None]] = []
    containment_pairs = _containment_pairs(spec)

    for obj in objects:
        obj_id = obj.get("id", "<missing>")
        asset_id = obj.get("asset_id")
        model_id = obj.get("model_id", 0)
        pose = obj.get("pose", {})
        xyz = pose.get("xyz")
        qpos = pose.get("qpos", [1, 0, 0, 0])

        entry = entries.get(asset_id)
        if entry is None:
            _add(checks, f"asset_exists:{obj_id}", "fail", f"Unknown asset_id {asset_id!r}.")
            continue
        _add(checks, f"asset_exists:{obj_id}", "pass", f"{asset_id} exists in the asset catalog.")

        if model_id not in entry.get("available_model_ids", []):
            _add(checks, f"model_id_exists:{obj_id}", "fail", f"model_id {model_id} is unavailable for {asset_id}.")
        else:
            _add(checks, f"model_id_exists:{obj_id}", "pass", f"model_id {model_id} is available for {asset_id}.")

        semantic = str(obj.get("semantic", "")).lower()
        aliases = [str(item).lower() for item in entry.get("aliases", [])]
        semantic_terms = [semantic, str(entry.get("semantic_name", "")).lower(), *aliases]
        if any(term and term in prompt for term in semantic_terms):
            _add(checks, f"semantic_prompt_match:{obj_id}", "pass", f"{semantic or asset_id} is grounded in the prompt.")
        else:
            _add(checks, f"semantic_prompt_match:{obj_id}", "warning", f"{semantic or asset_id} is not directly mentioned in the prompt.")

        if not _is_number_list(xyz, 3):
            _add(checks, f"pose_xyz_shape:{obj_id}", "fail", "pose.xyz must be a numeric [x, y, z] list.")
            continue
        if not _is_number_list(qpos, 4):
            _add(checks, f"pose_qpos_shape:{obj_id}", "fail", "pose.qpos must be a numeric quaternion [w, x, y, z].")
        else:
            norm = math.sqrt(sum(float(v) * float(v) for v in qpos))
            status = "pass" if 0.95 <= norm <= 1.05 else "warning"
            _add(checks, f"pose_qpos_norm:{obj_id}", status, f"Quaternion norm is {norm:.3f}.")

        if bounds_ok:
            inside = all(_in_range(float(xyz[idx]), bounds[axis]) for idx, axis in enumerate(["x", "y", "z"]))
            status = "pass" if inside else "fail"
            _add(checks, f"pose_in_workspace_bounds:{obj_id}", status, f"xyz={xyz} checked against workspace bounds.")

        region_name = pose.get("region")
        regions = spec.get("workspace", {}).get("spatial_regions", {})
        region = regions.get(region_name) if region_name else None
        if region:
            region_inside = _in_range(float(xyz[0]), region["x"]) and _in_range(float(xyz[1]), region["y"])
            status = "pass" if region_inside else "warning"
            _add(checks, f"pose_in_named_region:{obj_id}", status, f"xy={xyz[:2]} checked against region {region_name}.")

        if obj.get("physical", {}).get("stable_on_table") is True:
            _add(checks, f"stable_on_table_flag:{obj_id}", "pass", "Object marks stable_on_table=true.")
        else:
            _add(checks, f"stable_on_table_flag:{obj_id}", "warning", "Object does not mark stable_on_table=true.")

        object_positions.append((obj, entry, [float(v) for v in xyz], _xy_radius(entry, int(model_id))))

    for idx, (obj_a, _entry_a, xyz_a, radius_a) in enumerate(object_positions):
        for obj_b, _entry_b, xyz_b, radius_b in object_positions[idx + 1 :]:
            if radius_a is None or radius_b is None:
                _add(checks, f"approx_xy_collision:{obj_a['id']}:{obj_b['id']}", "warning", "Missing extents for approximate collision check.")
                continue
            distance = math.dist(xyz_a[:2], xyz_b[:2])
            clearance = distance - radius_a - radius_b
            is_containment_pair = (str(obj_a["id"]), str(obj_b["id"])) in containment_pairs or (
                str(obj_b["id"]),
                str(obj_a["id"]),
            ) in containment_pairs
            if clearance >= 0.01 or is_containment_pair:
                status = "pass"
            elif clearance >= -0.05:
                status = "warning"
            else:
                status = "fail"
            suffix = " containment relation allows close XY overlap; smoke/VLM must verify no visible penetration." if is_containment_pair else ""
            _add(
                checks,
                f"approx_xy_collision:{obj_a['id']}:{obj_b['id']}",
                status,
                f"xy distance={distance:.3f}m, approximate clearance={clearance:.3f}m.{suffix}",
            )

    if robotwin_root:
        root = Path(robotwin_root).expanduser()
        for obj in objects:
            asset_id = obj.get("asset_id")
            if not asset_id:
                continue
            path = root / "assets" / "objects" / asset_id
            status = "pass" if path.exists() else "warning"
            _add(checks, f"robotwin_asset_path:{obj.get('id', asset_id)}", status, f"Checked {path}.")

    fail_count = sum(1 for check in checks if check["status"] == "fail")
    warning_count = sum(1 for check in checks if check["status"] == "warning")
    return {
        "schema_version": "robotwin.tabletop_placement_validation.v0",
        "placement_name": spec.get("placement_name"),
        "status": "pass" if fail_count == 0 else "fail",
        "fail_count": fail_count,
        "warning_count": warning_count,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a TabletopPlacementSpec v0 JSON file.")
    parser.add_argument("--placement", required=True)
    parser.add_argument("--asset-catalog", required=True)
    parser.add_argument("--robotwin-root")
    parser.add_argument("--out")
    args = parser.parse_args()

    from generate_scene.asset_catalog import load_asset_catalog

    report = validate_placement_spec(
        read_json(Path(args.placement)),
        load_asset_catalog(Path(args.asset_catalog)),
        robotwin_root=args.robotwin_root,
    )
    if args.out:
        write_json(Path(args.out), report)
    print(f"{report['status'].upper()} fail_count={report['fail_count']} warning_count={report['warning_count']}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
