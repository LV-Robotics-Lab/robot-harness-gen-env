"""Bounded rejection/backtracking solver for tabletop footprint constraints."""

from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .catalog import AssetCatalog, CatalogModel, load_catalog
from .grounding import GroundedSelection, ground_scene
from .schema import (
    RelationSpec,
    RelationType,
    ResolvedObject,
    ResolvedPose,
    ResolvedSceneSpec,
    SceneSpec,
    SolverAttempt,
    SolverTrace,
)
from .support_geometry import (
    footprint_2d,
    sample_supported_offset,
    support_footprint_margin,
    support_surface_dimensions,
    support_surface_shape,
    support_surface_z,
)

COMPILER_VERSION = "scene_gen.stage5_solver.v2"


class SceneSolveError(RuntimeError):
    def __init__(self, report: dict[str, Any]):
        self.report = report
        super().__init__(report.get("blocker", "scene solve failed"))


@dataclass(frozen=True)
class CandidatePose:
    x: float
    y: float
    pose_z: float
    yaw: float
    half_x: float
    half_y: float
    half_z: float
    radius: float
    dimensions_m: tuple[float, float, float]
    bottom_z: float
    top_z: float
    footprint_shape: str
    support_surface_shape: str
    support_surface_dimensions_m: tuple[float, float]
    support_surface_z: float
    support_margin_m: float
    support_spawn_clearance_m: float
    interior_half_x: float | None = None
    interior_half_y: float | None = None
    interior_dimensions_m: tuple[float, float, float] | None = None
    interior_height: float | None = None
    interior_floor_z: float | None = None


def _quat_multiply(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    aw, ax, ay, az = first
    bw, bx, by, bz = second
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def _orientation(model: CatalogModel, yaw: float) -> tuple[float, float, float, float]:
    base = model.stable_orientation_wxyz or (1.0, 0.0, 0.0, 0.0)
    yaw_quaternion = (math.cos(yaw / 2.0), 0.0, 0.0, math.sin(yaw / 2.0))
    result = _quat_multiply(yaw_quaternion, base)
    norm = math.sqrt(sum(value * value for value in result))
    return tuple(value / norm for value in result)


def _footprint(model: CatalogModel, yaw: float) -> tuple[float, float, float]:
    if model.dimensions_m is None:
        raise ValueError(f"model {model.model_id} has no dimensions")
    footprint = footprint_2d(model.dimensions_m, yaw, model.footprint_shape)
    return footprint.half_x, footprint.half_y, footprint.radius


def _candidate_pose(
    model: CatalogModel,
    *,
    x: float,
    y: float,
    yaw: float,
    bottom_z: float,
) -> CandidatePose:
    if model.dimensions_m is None:
        raise ValueError(f"model {model.model_id} has no dimensions")
    half_x, half_y, radius = _footprint(model, yaw)
    height = model.dimensions_m[2]
    pose_z = bottom_z if model.z_policy == "origin_on_table" else bottom_z + height / 2.0
    interior_half_x: float | None = None
    interior_half_y: float | None = None
    interior_height: float | None = None
    interior_floor_z: float | None = None
    if model.interior_dimensions_m is not None:
        interior_width, interior_depth, interior_height = model.interior_dimensions_m
        cosine = abs(math.cos(yaw))
        sine = abs(math.sin(yaw))
        interior_half_x = 0.5 * (cosine * interior_width + sine * interior_depth)
        interior_half_y = 0.5 * (sine * interior_width + cosine * interior_depth)
        interior_floor_z = bottom_z + (model.interior_floor_z_offset_m or 0.0)
    top_z = bottom_z + height
    surface_dimensions = support_surface_dimensions(
        model.dimensions_m,
        model.support_surface_dimensions_m,
    )
    surface_shape = support_surface_shape(
        model.footprint_shape,
        model.support_surface_shape,
    )
    return CandidatePose(
        x=x,
        y=y,
        pose_z=pose_z,
        yaw=yaw,
        half_x=half_x,
        half_y=half_y,
        half_z=height / 2.0,
        radius=radius,
        dimensions_m=model.dimensions_m,
        bottom_z=bottom_z,
        top_z=top_z,
        footprint_shape=model.footprint_shape,
        support_surface_shape=surface_shape,
        support_surface_dimensions_m=surface_dimensions,
        support_surface_z=support_surface_z(
            bottom_z=bottom_z,
            top_z=top_z,
            explicit_offset_m=model.support_surface_z_offset_m,
        ),
        support_margin_m=model.support_margin_m,
        support_spawn_clearance_m=model.support_spawn_clearance_m,
        interior_half_x=interior_half_x,
        interior_half_y=interior_half_y,
        interior_dimensions_m=model.interior_dimensions_m,
        interior_height=interior_height,
        interior_floor_z=interior_floor_z,
    )


def _region_bounds(region: str, workspace: Any) -> tuple[tuple[float, float], tuple[float, float]]:
    x_low, x_high = workspace.x_bounds_m
    y_low, y_high = workspace.y_bounds_m
    x_mid = (x_low + x_high) / 2.0
    y_mid = (y_low + y_high) / 2.0
    if region == "left":
        return (x_low, x_mid - 0.03), (max(y_low, -0.10), y_high)
    if region == "right":
        return (x_mid + 0.03, x_high), (max(y_low, -0.10), y_high)
    if region == "front":
        return (x_low, x_high), (y_mid + 0.02, y_high)
    if region == "back":
        return (x_low, x_high), (max(y_low, -0.08), y_mid - 0.01)
    return (max(x_low, -0.25), min(x_high, 0.25)), (max(y_low, -0.06), min(y_high, 0.22))


def _overlaps_keepout(candidate: CandidatePose, workspace: Any) -> bool:
    keep_x_low, keep_x_high = workspace.robot_keepout_x_m
    keep_y_low, keep_y_high = workspace.robot_keepout_y_m
    return not (
        candidate.x + candidate.half_x <= keep_x_low
        or candidate.x - candidate.half_x >= keep_x_high
        or candidate.y + candidate.half_y <= keep_y_low
        or candidate.y - candidate.half_y >= keep_y_high
    )


def _pair_relation_reasons(
    relation: RelationSpec,
    source: CandidatePose,
    target: CandidatePose,
) -> list[str]:
    gap = 0.015
    reasons: list[str] = []
    if relation.relation == RelationType.ON_TOP_OF:
        expected_bottom_z = target.support_surface_z + target.support_spawn_clearance_m
        if abs(source.bottom_z - expected_bottom_z) > 0.003:
            reasons.append("on_top_of support height failed")
        margin = support_footprint_margin(
            source_dimensions_m=source.dimensions_m,
            source_yaw=source.yaw,
            source_shape=source.footprint_shape,
            target_surface_dimensions_m=target.support_surface_dimensions_m,
            target_yaw=target.yaw,
            target_surface_shape=target.support_surface_shape,
            dx=source.x - target.x,
            dy=source.y - target.y,
        )
        if margin + 1e-9 < target.support_margin_m:
            reasons.append("on_top_of stable support margin failed")
    elif relation.relation == RelationType.INSIDE:
        interior_dimensions = target.interior_dimensions_m
        interior_height = target.interior_height
        interior_floor_z = target.interior_floor_z
        if (
            interior_dimensions is None
            or interior_height is None
            or interior_floor_z is None
        ):
            reasons.append("inside target has no interior dimensions")
        else:
            margin = support_footprint_margin(
                source_dimensions_m=source.dimensions_m,
                source_yaw=source.yaw,
                source_shape=source.footprint_shape,
                target_surface_dimensions_m=interior_dimensions[:2],
                target_yaw=target.yaw,
                target_surface_shape="box",
                dx=source.x - target.x,
                dy=source.y - target.y,
            )
            if margin < -1e-9:
                reasons.append("inside footprint containment failed")
            if (
                source.bottom_z < interior_floor_z - 1e-9
                or source.top_z > interior_floor_z + interior_height + 1e-9
            ):
                reasons.append("inside vertical containment failed")
    elif relation.relation == RelationType.LEFT_OF:
        if source.x + source.half_x + gap > target.x - target.half_x:
            reasons.append("left_of footprint inequality failed")
    elif relation.relation == RelationType.RIGHT_OF:
        if source.x - source.half_x - gap < target.x + target.half_x:
            reasons.append("right_of footprint inequality failed")
    elif relation.relation == RelationType.FRONT_OF:
        if source.y - source.half_y - gap < target.y + target.half_y:
            reasons.append("front_of footprint inequality failed")
    elif relation.relation == RelationType.BEHIND:
        if source.y + source.half_y + gap > target.y - target.half_y:
            reasons.append("behind footprint inequality failed")
    elif relation.relation == RelationType.NEAR:
        distance = math.hypot(source.x - target.x, source.y - target.y)
        if distance > (relation.max_distance_m or 0.25) + 1e-9:
            reasons.append("near maximum center distance failed")
    elif relation.relation == RelationType.DISTANCE_AT_LEAST:
        distance = math.hypot(source.x - target.x, source.y - target.y)
        if distance + 1e-9 < (relation.min_distance_m or 0.0):
            reasons.append("distance_at_least minimum center distance failed")
    return reasons


def _candidate_reasons(
    object_id: str,
    candidate: CandidatePose,
    assigned: dict[str, CandidatePose],
    relations: tuple[RelationSpec, ...],
    workspace: Any,
) -> list[str]:
    reasons: list[str] = []
    if _overlaps_keepout(candidate, workspace):
        reasons.append("robot initial keepout overlap")
    support_pairs = {
        frozenset((relation.source, relation.target))
        for relation in relations
        if relation.relation in {RelationType.ON_TOP_OF, RelationType.INSIDE}
    }
    for other_id, other in assigned.items():
        if frozenset((object_id, other_id)) in support_pairs:
            continue
        separated = (
            candidate.x + candidate.half_x + 0.005 <= other.x - other.half_x
            or other.x + other.half_x + 0.005 <= candidate.x - candidate.half_x
            or candidate.y + candidate.half_y + 0.005 <= other.y - other.half_y
            or other.y + other.half_y + 0.005 <= candidate.y - candidate.half_y
            or candidate.top_z + 0.002 <= other.bottom_z
            or other.top_z + 0.002 <= candidate.bottom_z
        )
        if not separated:
            reasons.append(f"three-dimensional overlap with {other_id}")
    prospective = dict(assigned)
    prospective[object_id] = candidate
    for relation in relations:
        if relation.relation == RelationType.ON_TABLE or relation.target == "table":
            continue
        if relation.source in prospective and relation.target in prospective:
            reasons.extend(
                _pair_relation_reasons(
                    relation,
                    prospective[relation.source],
                    prospective[relation.target],
                )
            )
    return reasons


def _support_relations(spec: SceneSpec) -> dict[str, RelationSpec]:
    return {
        relation.source: relation
        for relation in spec.relations
        if relation.relation in {
            RelationType.ON_TABLE,
            RelationType.ON_TOP_OF,
            RelationType.INSIDE,
        }
    }


def _support_depth(object_id: str, supports: dict[str, RelationSpec]) -> int:
    relation = supports[object_id]
    if relation.target == "table":
        return 0
    return 1 + _support_depth(relation.target, supports)


def _articulation_qpos(query: Any, model: CatalogModel) -> tuple[float, ...]:
    joints = model.articulation_joints
    if not joints:
        if query.articulation is not None:
            raise ValueError(f"{query.object_id} requests articulation but selected model has no movable joints")
        return ()
    closed = model.articulation_closed_qpos or tuple(joint.lower for joint in joints)
    opened = model.articulation_open_qpos or tuple(joint.upper for joint in joints)
    fraction = query.articulation.open_fraction if query.articulation is not None else 0.0
    values = list(closed)
    selected = range(len(joints))
    if query.articulation is not None and query.articulation.joint_selector == "first_movable":
        selected = range(min(1, len(joints)))
    for index in selected:
        values[index] = closed[index] + fraction * (opened[index] - closed[index])
    return tuple(round(value, 9) for value in values)


def solve_scene(
    spec: SceneSpec,
    catalog: AssetCatalog,
    *,
    max_attempts_per_object: int = 96,
    max_backtracks: int = 48,
) -> ResolvedSceneSpec:
    grounded = ground_scene(spec.objects, catalog, seed=spec.seed)
    supports = _support_relations(spec)
    degree = {item.object_id: 0 for item in spec.objects}
    for relation in spec.relations:
        if relation.target != "table":
            degree[relation.source] += 1
            degree[relation.target] += 1
    order = sorted(
        spec.objects,
        key=lambda item: (
            _support_depth(item.object_id, supports),
            -degree[item.object_id],
            item.object_id,
        ),
    )
    rng = random.Random(spec.seed)
    attempts: list[SolverAttempt] = []
    assigned: dict[str, CandidatePose] = {}
    backtracks = 0

    def place(index: int) -> bool:
        nonlocal backtracks
        if index >= len(order):
            return True
        query = order[index]
        model = grounded[query.object_id].model
        support = supports[query.object_id]
        region_x, region_y = _region_bounds(query.region, spec.workspace)
        for _ in range(max_attempts_per_object):
            target = assigned.get(support.target) if support.target != "table" else None
            if query.articulation is not None and target is None:
                yaw = math.pi / 2.0
            else:
                yaw = rng.uniform(-math.pi, math.pi) if target is None else target.yaw + rng.uniform(-math.pi, math.pi)
            half_x, half_y, _ = _footprint(model, yaw)
            pre_reasons: list[str] = []
            if support.relation == RelationType.ON_TABLE:
                x_min, x_max = region_x[0] + half_x, region_x[1] - half_x
                y_min, y_max = region_y[0] + half_y, region_y[1] - half_y
                if x_min >= x_max or y_min >= y_max:
                    pre_reasons.append("asset footprint does not fit selected region")
                    x = (region_x[0] + region_x[1]) / 2.0
                    y = (region_y[0] + region_y[1]) / 2.0
                elif query.articulation is not None:
                    x = min(x_max, max(x_min, 0.0))
                    y = min(y_max, max(y_min, 0.16))
                else:
                    x = rng.uniform(x_min, x_max)
                    y = rng.uniform(y_min, y_max)
                bottom_z = spec.workspace.table_height_m
            elif target is None:
                pre_reasons.append(f"support target {support.target} is not assigned")
                x = y = 0.0
                bottom_z = spec.workspace.table_height_m
            elif support.relation == RelationType.ON_TOP_OF:
                offset = sample_supported_offset(
                    rng=rng,
                    source_dimensions_m=model.dimensions_m,
                    source_yaw=yaw,
                    source_shape=model.footprint_shape,
                    target_surface_dimensions_m=target.support_surface_dimensions_m,
                    target_yaw=target.yaw,
                    target_surface_shape=target.support_surface_shape,
                    required_margin_m=target.support_margin_m,
                )
                if offset is None:
                    pre_reasons.append("object footprint does not fit stable support surface")
                    offset = (0.0, 0.0)
                if model.is_static:
                    pre_reasons.append("nested support source cannot be static")
                x = target.x + offset[0]
                y = target.y + offset[1]
                bottom_z = target.support_surface_z + target.support_spawn_clearance_m
            else:
                interior_dimensions = target.interior_dimensions_m
                if interior_dimensions is None:
                    pre_reasons.append("container target has no interior dimensions")
                    offset = (0.0, 0.0)
                else:
                    offset = sample_supported_offset(
                        rng=rng,
                        source_dimensions_m=model.dimensions_m,
                        source_yaw=yaw,
                        source_shape=model.footprint_shape,
                        target_surface_dimensions_m=interior_dimensions[:2],
                        target_yaw=target.yaw,
                        target_surface_shape="box",
                        required_margin_m=0.005,
                    )
                    if offset is None:
                        pre_reasons.append("object footprint does not fit container interior")
                        offset = (0.0, 0.0)
                if model.is_static:
                    pre_reasons.append("nested support source cannot be static")
                x = target.x + offset[0] * 0.25
                y = target.y + offset[1] * 0.25
                bottom_z = (
                    (
                        target.interior_floor_z
                        if target.interior_floor_z is not None
                        else target.bottom_z
                    )
                    + target.support_spawn_clearance_m
                )
            candidate = _candidate_pose(
                model,
                x=x,
                y=y,
                yaw=yaw,
                bottom_z=bottom_z,
            )
            reasons = [
                *pre_reasons,
                *_candidate_reasons(
                    query.object_id,
                    candidate,
                    assigned,
                    spec.relations,
                    spec.workspace,
                ),
            ]
            attempts.append(
                SolverAttempt(
                    attempt=len(attempts) + 1,
                    object_id=query.object_id,
                    candidate_xy_m=(round(x, 9), round(y, 9)),
                    yaw_rad=round(yaw, 12),
                    accepted=not reasons,
                    reasons=tuple(reasons),
                )
            )
            if reasons:
                continue
            assigned[query.object_id] = candidate
            if place(index + 1):
                return True
            assigned.pop(query.object_id, None)
            backtracks += 1
            if backtracks >= max_backtracks:
                return False
        return False

    solved = place(0)
    if not solved:
        report = {
            "schema_version": "robotwin.scene_solver_failure.v1",
            "scene_id": spec.scene_id,
            "seed": spec.seed,
            "status": "fail",
            "blocker": "bounded solver exhausted",
            "max_attempts_per_object": max_attempts_per_object,
            "max_backtracks": max_backtracks,
            "total_attempts": len(attempts),
            "attempts": [item.model_dump(mode="json") for item in attempts],
        }
        raise SceneSolveError(report)

    resolved_objects: list[ResolvedObject] = []
    for query in sorted(spec.objects, key=lambda item: item.object_id):
        selection: GroundedSelection = grounded[query.object_id]
        model = selection.model
        pose = assigned[query.object_id]
        if model.dimensions_m is None or model.stable_pose_id is None:
            raise AssertionError("grounding selected an incomplete model")
        support = supports[query.object_id]
        try:
            articulation_qpos = _articulation_qpos(query, model)
        except ValueError as error:
            raise SceneSolveError(
                {
                    "schema_version": "robotwin.scene_solver_failure.v1",
                    "scene_id": spec.scene_id,
                    "seed": spec.seed,
                    "status": "fail",
                    "blocker": str(error),
                    "total_attempts": len(attempts),
                    "attempts": [item.model_dump(mode="json") for item in attempts],
                }
            ) from error
        files = tuple(
            sorted(
                {
                    value
                    for value in (
                        model.metadata_path,
                        model.visual_path,
                        model.collision_path,
                        model.urdf_path,
                        str(Path(selection.entry.asset_path) / "generation_provenance.json")
                        if "procedural_generated" in selection.entry.source_notes
                        else None,
                    )
                    if value
                }
            )
        )
        stable_orientation = model.stable_orientation_wxyz or (1.0, 0.0, 0.0, 0.0)
        generated_asset = "procedural_generated" in selection.entry.source_notes
        generation_metadata_path = Path(selection.entry.asset_path) / "generation_provenance.json"
        resolved_objects.append(
            ResolvedObject(
                object_id=query.object_id,
                category=query.category,
                color=query.color,
                material=query.material,
                asset_id=selection.entry.asset_id,
                model_id=model.model_id,
                load_type="urdf" if selection.entry.load_type == "urdf" else "rigid",
                stable_pose_id=model.stable_pose_id,
                stable_orientation_wxyz=stable_orientation,
                dimensions_m=model.dimensions_m,
                interior_dimensions_m=model.interior_dimensions_m,
                interior_floor_z_offset_m=model.interior_floor_z_offset_m,
                footprint_shape=model.footprint_shape,
                support_surface_shape=model.support_surface_shape,
                support_surface_dimensions_m=model.support_surface_dimensions_m,
                support_surface_z_offset_m=model.support_surface_z_offset_m,
                support_margin_m=model.support_margin_m,
                support_spawn_clearance_m=model.support_spawn_clearance_m,
                z_policy=model.z_policy,
                collision_available=bool(model.collision_path or model.urdf_path),
                source_files=files,
                grounding_score=selection.score,
                grounding_reasons=selection.reasons,
                rejected_candidates=selection.rejected_candidates,
                pose=ResolvedPose(
                    position_m=(round(pose.x, 9), round(pose.y, 9), round(pose.pose_z, 9)),
                    orientation_wxyz=tuple(round(value, 12) for value in _orientation(model, pose.yaw)),
                    yaw_rad=round(pose.yaw, 12),
                ),
                is_static=model.is_static,
                support_relation=support.relation,
                support_target=support.target,
                articulation_state=query.articulation,
                articulation_joint_names=tuple(joint.name for joint in model.articulation_joints),
                articulation_joint_limits=tuple(
                    (joint.lower, joint.upper) for joint in model.articulation_joints
                ),
                articulation_qpos=articulation_qpos,
                asset_provenance="procedural_generated" if generated_asset else "robotwin_catalog",
                generation_metadata_path=(
                    str(generation_metadata_path.resolve()) if generated_asset else None
                ),
            )
        )
    trace = SolverTrace(
        seed=spec.seed,
        max_attempts_per_object=max_attempts_per_object,
        total_attempts=len(attempts),
        attempts=tuple(attempts),
        status="pass",
    )
    return ResolvedSceneSpec(
        scene_id=spec.scene_id,
        request=spec.request,
        frame=spec.frame,
        seed=spec.seed,
        workspace=spec.workspace,
        source_scene_spec_sha256=spec.digest(),
        asset_catalog_sha256=catalog.digest(),
        compiler_version=COMPILER_VERSION,
        objects=tuple(resolved_objects),
        relations=spec.relations,
        solver_trace=trace,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Ground and solve a schema-valid SceneSpec.")
    parser.add_argument("--scene-spec", required=True)
    parser.add_argument("--asset-catalog", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--failure-out")
    parser.add_argument("--max-attempts-per-object", type=int, default=96)
    args = parser.parse_args()
    spec = SceneSpec.model_validate_json(Path(args.scene_spec).read_text(encoding="utf-8"))
    catalog = load_catalog(Path(args.asset_catalog))
    try:
        resolved = solve_scene(spec, catalog, max_attempts_per_object=args.max_attempts_per_object)
    except SceneSolveError as error:
        failure_path = Path(args.failure_out or f"{args.out}.failure.json")
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        failure_path.write_text(json.dumps(error.report, indent=2) + "\n", encoding="utf-8")
        print(f"FAIL {failure_path}")
        return 2
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(resolved.canonical_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"PASS scene_id={resolved.scene_id} sha256={resolved.digest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
