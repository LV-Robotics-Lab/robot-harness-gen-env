"""Typed, provider-agnostic contracts for constrained RoboTwin scenes."""

from __future__ import annotations

import hashlib
import json
import math
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCENE_SCHEMA_VERSION = "robotwin.scene_spec.v1"
RESOLVED_SCHEMA_VERSION = "robotwin.resolved_scene.v1"
FORBIDDEN_SCENE_KEYS = {
    "asset_id",
    "asset_path",
    "file_path",
    "model_id",
    "pose",
    "position",
    "position_m",
    "xyz",
    "qpos",
    "quaternion",
    "orientation",
    "orientation_wxyz",
    "python",
    "code",
}


class SceneSpecError(ValueError):
    """Raised when an input violates the constrained scene contract."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())


class RelationType(str, Enum):
    ON_TABLE = "on_table"
    LEFT_OF = "left_of"
    RIGHT_OF = "right_of"
    FRONT_OF = "front_of"
    BEHIND = "behind"
    NEAR = "near"
    DISTANCE_AT_LEAST = "distance_at_least"


class FrameSpec(StrictModel):
    name: Literal["robotwin_world"] = "robotwin_world"
    x_axis: Literal["right"] = "right"
    y_axis: Literal["front"] = "front"
    z_axis: Literal["up"] = "up"
    handedness: Literal["right_handed"] = "right_handed"


class WorkspaceSpec(StrictModel):
    support_surface: Literal["table"] = "table"
    table_height_m: float = Field(default=0.741, ge=0.5, le=1.2)
    x_bounds_m: tuple[float, float] = (-0.35, 0.35)
    y_bounds_m: tuple[float, float] = (-0.20, 0.30)
    robot_keepout_x_m: tuple[float, float] = (-0.16, 0.16)
    robot_keepout_y_m: tuple[float, float] = (-0.20, -0.08)

    @model_validator(mode="after")
    def ordered_bounds(self) -> "WorkspaceSpec":
        for name in (
            "x_bounds_m",
            "y_bounds_m",
            "robot_keepout_x_m",
            "robot_keepout_y_m",
        ):
            low, high = getattr(self, name)
            if not (math.isfinite(low) and math.isfinite(high) and low < high):
                raise SceneSpecError(f"{name} must contain finite ascending bounds")
        return self


class SceneObjectSpec(StrictModel):
    object_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    category: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    color: str | None = Field(default=None, max_length=32, pattern=r"^[a-z][a-z0-9_-]*$")
    material: str | None = Field(default=None, max_length=32, pattern=r"^[a-z][a-z0-9_-]*$")
    region: Literal["left", "right", "front", "back", "center"] = "center"


class RelationSpec(StrictModel):
    relation: RelationType
    source: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    target: str = Field(pattern=r"^(table|[a-z][a-z0-9_]{0,63})$")
    max_distance_m: float | None = Field(default=None, gt=0.0, le=1.0)
    min_distance_m: float | None = Field(default=None, gt=0.0, le=1.0)

    @model_validator(mode="after")
    def relation_parameters(self) -> "RelationSpec":
        if self.source == self.target:
            raise SceneSpecError("relation source and target must differ")
        if self.relation == RelationType.ON_TABLE:
            if self.target != "table":
                raise SceneSpecError("on_table must target table")
            if self.max_distance_m is not None or self.min_distance_m is not None:
                raise SceneSpecError("on_table does not accept distance parameters")
        elif self.target == "table":
            raise SceneSpecError(f"{self.relation.value} cannot target table")
        if self.relation == RelationType.NEAR:
            if self.min_distance_m is not None:
                raise SceneSpecError("near accepts max_distance_m, not min_distance_m")
        elif self.max_distance_m is not None:
            raise SceneSpecError(f"{self.relation.value} does not accept max_distance_m")
        if self.relation == RelationType.DISTANCE_AT_LEAST:
            if self.min_distance_m is None:
                raise SceneSpecError("distance_at_least requires min_distance_m")
        elif self.min_distance_m is not None:
            raise SceneSpecError(f"{self.relation.value} does not accept min_distance_m")
        return self


def _reject_forbidden_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().strip()
            if normalized in FORBIDDEN_SCENE_KEYS:
                raise SceneSpecError(f"SceneSpec input cannot contain {key!r} at {path}")
            _reject_forbidden_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden_keys(child, f"{path}[{index}]")


def _has_cycle(nodes: set[str], edges: list[tuple[str, str]]) -> bool:
    graph = {node: [] for node in nodes}
    for source, target in edges:
        graph[source].append(target)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for target in graph[node]:
            if visit(target):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in sorted(nodes))


class SceneSpec(StrictModel):
    schema_version: Literal[SCENE_SCHEMA_VERSION] = SCENE_SCHEMA_VERSION
    scene_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,95}$")
    request: str = Field(min_length=3, max_length=2000)
    language: Literal["en", "zh", "mixed"]
    frame: FrameSpec = FrameSpec()
    unit: Literal["m"] = "m"
    seed: int = Field(default=0, ge=0, le=2_147_483_647)
    workspace: WorkspaceSpec = WorkspaceSpec()
    objects: tuple[SceneObjectSpec, ...] = Field(min_length=1, max_length=12)
    relations: tuple[RelationSpec, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="before")
    @classmethod
    def input_boundary(cls, value: Any) -> Any:
        _reject_forbidden_keys(value)
        return value

    @model_validator(mode="after")
    def semantic_consistency(self) -> "SceneSpec":
        object_ids = [item.object_id for item in self.objects]
        if len(object_ids) != len(set(object_ids)):
            raise SceneSpecError("object_id values must be unique")
        known = set(object_ids)
        on_table: set[str] = set()
        axis_x: list[tuple[str, str]] = []
        axis_y: list[tuple[str, str]] = []
        pair_constraints: dict[frozenset[str], dict[str, float]] = {}
        relation_keys: set[tuple[Any, ...]] = set()
        for relation in self.relations:
            if relation.source not in known:
                raise SceneSpecError(f"unknown relation source {relation.source!r}")
            if relation.target != "table" and relation.target not in known:
                raise SceneSpecError(f"unknown relation target {relation.target!r}")
            key = (
                relation.relation,
                relation.source,
                relation.target,
                relation.max_distance_m,
                relation.min_distance_m,
            )
            if key in relation_keys:
                raise SceneSpecError(f"duplicate relation {relation.relation.value}")
            relation_keys.add(key)
            if relation.relation == RelationType.ON_TABLE:
                on_table.add(relation.source)
            elif relation.relation == RelationType.LEFT_OF:
                axis_x.append((relation.source, relation.target))
            elif relation.relation == RelationType.RIGHT_OF:
                axis_x.append((relation.target, relation.source))
            elif relation.relation == RelationType.BEHIND:
                axis_y.append((relation.source, relation.target))
            elif relation.relation == RelationType.FRONT_OF:
                axis_y.append((relation.target, relation.source))
            elif relation.relation in {RelationType.NEAR, RelationType.DISTANCE_AT_LEAST}:
                pair = frozenset((relation.source, relation.target))
                values = pair_constraints.setdefault(pair, {})
                if relation.relation == RelationType.NEAR:
                    values["max"] = relation.max_distance_m or 0.18
                else:
                    values["min"] = relation.min_distance_m or 0.0
        missing_support = sorted(known - on_table)
        if missing_support:
            raise SceneSpecError(f"every object requires on_table: {missing_support}")
        if _has_cycle(known, axis_x):
            raise SceneSpecError("contradictory left/right relation cycle")
        if _has_cycle(known, axis_y):
            raise SceneSpecError("contradictory front/behind relation cycle")
        for pair, values in pair_constraints.items():
            if values.get("min", 0.0) > values.get("max", float("inf")):
                raise SceneSpecError(
                    f"distance constraints contradict for {sorted(pair)}: "
                    f"min={values['min']} max={values['max']}"
                )
        return self

    def canonical_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)

    def digest(self) -> str:
        payload = json.dumps(
            self.canonical_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class RejectedCandidate(StrictModel):
    asset_id: str
    model_id: int | None = None
    score: float
    reasons: tuple[str, ...]


class ResolvedPose(StrictModel):
    position_m: tuple[float, float, float]
    orientation_wxyz: tuple[float, float, float, float]
    yaw_rad: float

    @model_validator(mode="after")
    def normalized_quaternion(self) -> "ResolvedPose":
        if not all(math.isfinite(value) for value in (*self.position_m, *self.orientation_wxyz, self.yaw_rad)):
            raise SceneSpecError("resolved pose values must be finite")
        norm = math.sqrt(sum(value * value for value in self.orientation_wxyz))
        if abs(norm - 1.0) > 1e-6:
            raise SceneSpecError(f"orientation quaternion is not normalized: {norm}")
        return self


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


class ResolvedObject(StrictModel):
    object_id: str
    category: str
    color: str | None = None
    material: str | None = None
    asset_id: str
    model_id: int
    load_type: Literal["rigid", "urdf"]
    stable_pose_id: str
    stable_orientation_wxyz: tuple[float, float, float, float]
    dimensions_m: tuple[float, float, float]
    mass_kg: float | None = Field(default=None, gt=0.0)
    collision_available: bool
    source_files: tuple[str, ...]
    grounding_score: float
    grounding_reasons: tuple[str, ...]
    rejected_candidates: tuple[RejectedCandidate, ...] = ()
    pose: ResolvedPose
    is_static: bool = False

    @model_validator(mode="after")
    def stable_pose_plus_yaw_only(self) -> "ResolvedObject":
        base_norm = math.sqrt(sum(value * value for value in self.stable_orientation_wxyz))
        if abs(base_norm - 1.0) > 1e-6:
            raise SceneSpecError(f"stable orientation is not normalized for {self.object_id}")
        yaw = (
            math.cos(self.pose.yaw_rad / 2.0),
            0.0,
            0.0,
            math.sin(self.pose.yaw_rad / 2.0),
        )
        expected = _quat_multiply(yaw, self.stable_orientation_wxyz)
        if max(abs(a - b) for a, b in zip(expected, self.pose.orientation_wxyz)) > 1e-6:
            raise SceneSpecError(
                f"resolved orientation for {self.object_id} must be stable pose plus world-z yaw"
            )
        return self


class SolverAttempt(StrictModel):
    attempt: int
    object_id: str
    candidate_xy_m: tuple[float, float]
    yaw_rad: float
    accepted: bool
    reasons: tuple[str, ...] = ()


class SolverTrace(StrictModel):
    algorithm: Literal["bounded_rejection_backtracking_v1"] = "bounded_rejection_backtracking_v1"
    seed: int
    max_attempts_per_object: int
    total_attempts: int
    attempts: tuple[SolverAttempt, ...]
    status: Literal["pass", "fail"]
    blocker: str | None = None


class ResolvedSceneSpec(StrictModel):
    schema_version: Literal[RESOLVED_SCHEMA_VERSION] = RESOLVED_SCHEMA_VERSION
    scene_id: str
    request: str
    frame: FrameSpec
    unit: Literal["m"] = "m"
    seed: int
    workspace: WorkspaceSpec
    source_scene_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    asset_catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    compiler_version: str
    objects: tuple[ResolvedObject, ...]
    relations: tuple[RelationSpec, ...]
    solver_trace: SolverTrace

    def canonical_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)

    def digest(self) -> str:
        payload = json.dumps(
            self.canonical_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def scene_spec_json_schema() -> dict[str, Any]:
    return SceneSpec.model_json_schema()


def write_scene_spec_schema(path: str) -> None:
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(scene_spec_json_schema(), stream, indent=2, ensure_ascii=False)
        stream.write("\n")
