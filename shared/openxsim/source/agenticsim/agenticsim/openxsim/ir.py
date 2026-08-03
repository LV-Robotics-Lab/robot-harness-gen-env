"""Typed, simulator-neutral intermediate representation for Open-X-Sim.

The IR keeps task semantics separate from backend asset formats.  USD, MJCF,
URDF, and SAPIEN files are representations inside an :class:`AssetBundle`,
not the canonical environment model.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "agenticsim.environment_package.v1"


class IRValidationError(ValueError):
    """Raised when an EnvironmentPackage violates the shared contract."""


def _tuple_floats(values: Iterable[Any], *, length: int, field_name: str) -> tuple[float, ...]:
    result = tuple(float(value) for value in values)
    if len(result) != length or not all(math.isfinite(value) for value in result):
        raise IRValidationError(f"{field_name} must contain {length} finite numbers")
    return result


def _identifier(value: str, *, field_name: str) -> str:
    value = str(value).strip()
    if not value or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", value):
        raise IRValidationError(f"{field_name} is not a valid identifier: {value!r}")
    return value


@dataclass(frozen=True)
class Pose:
    """Rigid transform using metres and a wxyz quaternion."""

    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    orientation_wxyz: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)

    def validate(self) -> None:
        _tuple_floats(self.position, length=3, field_name="pose.position")
        quat = _tuple_floats(self.orientation_wxyz, length=4, field_name="pose.orientation_wxyz")
        norm = math.sqrt(sum(value * value for value in quat))
        if norm < 1e-8:
            raise IRValidationError("pose.orientation_wxyz must be non-zero")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "Pose":
        data = data or {}
        return cls(
            position=_tuple_floats(data.get("position", (0.0, 0.0, 0.0)), length=3, field_name="pose.position"),
            orientation_wxyz=_tuple_floats(
                data.get("orientation_wxyz", (1.0, 0.0, 0.0, 0.0)),
                length=4,
                field_name="pose.orientation_wxyz",
            ),
        )


@dataclass(frozen=True)
class AssetRepresentation:
    """One concrete representation of an asset."""

    format: str
    uri: str
    backend: str = "portable"
    role: str = "visual_and_collision"
    sha256: str = ""
    size_bytes: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.format.strip():
            raise IRValidationError("asset representation format is empty")
        if not self.uri.strip():
            raise IRValidationError("asset representation uri is empty")
        if self.sha256 and not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise IRValidationError(f"invalid sha256 for asset representation: {self.sha256!r}")
        if self.size_bytes < 0:
            raise IRValidationError("asset representation size_bytes must be non-negative")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AssetRepresentation":
        return cls(
            format=str(data["format"]),
            uri=str(data["uri"]),
            backend=str(data.get("backend", "portable")),
            role=str(data.get("role", "visual_and_collision")),
            sha256=str(data.get("sha256", "")),
            size_bytes=int(data.get("size_bytes", 0)),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class AssetBundle:
    """Semantic asset plus all known simulator-specific representations."""

    asset_id: str
    category: str
    representations: tuple[AssetRepresentation, ...]
    source: dict[str, Any] = field(default_factory=dict)
    physical: dict[str, Any] = field(default_factory=dict)
    articulation: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()

    def validate(self) -> None:
        _identifier(self.asset_id, field_name="asset_id")
        if not self.category.strip():
            raise IRValidationError(f"asset {self.asset_id!r} has an empty category")
        if not self.representations:
            raise IRValidationError(f"asset {self.asset_id!r} has no representation")
        for representation in self.representations:
            representation.validate()

    def representation_for(
        self,
        backend: str,
        formats: Iterable[str] = (),
    ) -> AssetRepresentation | None:
        accepted = {value.lower() for value in formats}
        exact = [item for item in self.representations if item.backend in {backend, "portable"}]
        for item in exact:
            if not accepted or item.format.lower() in accepted:
                return item
        return None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AssetBundle":
        return cls(
            asset_id=str(data["asset_id"]),
            category=str(data["category"]),
            representations=tuple(
                AssetRepresentation.from_dict(item) for item in data.get("representations", [])
            ),
            source=dict(data.get("source") or {}),
            physical=dict(data.get("physical") or {}),
            articulation=dict(data.get("articulation") or {}),
            tags=tuple(str(value) for value in data.get("tags", [])),
        )


@dataclass(frozen=True)
class SceneObject:
    """One asset instance in a scene."""

    instance_id: str
    asset_id: str
    pose: Pose = field(default_factory=Pose)
    role: str = "object"
    static: bool = False
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    randomization: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        _identifier(self.instance_id, field_name="instance_id")
        _identifier(self.asset_id, field_name="scene_object.asset_id")
        self.pose.validate()
        scale = _tuple_floats(self.scale, length=3, field_name="scene_object.scale")
        if any(value <= 0.0 for value in scale):
            raise IRValidationError(f"scene object {self.instance_id!r} has non-positive scale")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SceneObject":
        return cls(
            instance_id=str(data["instance_id"]),
            asset_id=str(data["asset_id"]),
            pose=Pose.from_dict(data.get("pose")),
            role=str(data.get("role", "object")),
            static=bool(data.get("static", False)),
            scale=_tuple_floats(data.get("scale", (1.0, 1.0, 1.0)), length=3, field_name="scene_object.scale"),
            randomization=dict(data.get("randomization") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class EnvSpec:
    """Simulator-neutral world, robot, sensor, and scene definition."""

    name: str
    objects: tuple[SceneObject, ...]
    units: str = "m"
    up_axis: str = "Z"
    gravity_mps2: tuple[float, float, float] = (0.0, 0.0, -9.81)
    workspace_bounds_m: tuple[float, float, float, float, float, float] = (
        -1.0,
        -1.0,
        0.0,
        1.0,
        1.0,
        2.0,
    )
    robots: tuple[dict[str, Any], ...] = ()
    sensors: tuple[dict[str, Any], ...] = ()
    regions: tuple[dict[str, Any], ...] = ()
    randomization: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        _identifier(self.name, field_name="env.name")
        if self.units != "m":
            raise IRValidationError("EnvironmentPackage v1 canonical units must be metres")
        if self.up_axis not in {"X", "Y", "Z"}:
            raise IRValidationError("env.up_axis must be X, Y, or Z")
        _tuple_floats(self.gravity_mps2, length=3, field_name="env.gravity_mps2")
        bounds = _tuple_floats(self.workspace_bounds_m, length=6, field_name="env.workspace_bounds_m")
        if any(bounds[index] >= bounds[index + 3] for index in range(3)):
            raise IRValidationError("env.workspace_bounds_m minimums must be below maximums")
        instance_ids: set[str] = set()
        for obj in self.objects:
            obj.validate()
            if obj.instance_id in instance_ids:
                raise IRValidationError(f"duplicate scene object id: {obj.instance_id}")
            instance_ids.add(obj.instance_id)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EnvSpec":
        return cls(
            name=str(data["name"]),
            objects=tuple(SceneObject.from_dict(item) for item in data.get("objects", [])),
            units=str(data.get("units", "m")),
            up_axis=str(data.get("up_axis", "Z")),
            gravity_mps2=_tuple_floats(
                data.get("gravity_mps2", (0.0, 0.0, -9.81)), length=3, field_name="env.gravity_mps2"
            ),
            workspace_bounds_m=_tuple_floats(
                data.get("workspace_bounds_m", (-1.0, -1.0, 0.0, 1.0, 1.0, 2.0)),
                length=6,
                field_name="env.workspace_bounds_m",
            ),
            robots=tuple(dict(item) for item in data.get("robots", [])),
            sensors=tuple(dict(item) for item in data.get("sensors", [])),
            regions=tuple(dict(item) for item in data.get("regions", [])),
            randomization=dict(data.get("randomization") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class TaskSpec:
    """Task semantics that must survive backend compilation."""

    instruction: str
    intent: str
    reset: dict[str, Any]
    action: dict[str, Any]
    observation: dict[str, Any]
    plan: tuple[dict[str, Any], ...]
    success: tuple[dict[str, Any], ...]
    termination: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.instruction.strip():
            raise IRValidationError("task.instruction is empty")
        if not self.intent.strip():
            raise IRValidationError("task.intent is empty")
        if not self.success:
            raise IRValidationError("task.success must contain at least one condition")
        for condition in self.success:
            if not str(condition.get("type", "")).strip():
                raise IRValidationError("every task.success condition must have a type")

    def semantic_contract(self) -> dict[str, Any]:
        """Return the fields required for L2 semantic comparison."""

        return {
            "reset": self.reset,
            "action": self.action,
            "observation": self.observation,
            "success": list(self.success),
            "termination": list(self.termination),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TaskSpec":
        return cls(
            instruction=str(data["instruction"]),
            intent=str(data["intent"]),
            reset=dict(data.get("reset") or {}),
            action=dict(data.get("action") or {}),
            observation=dict(data.get("observation") or {}),
            plan=tuple(dict(item) for item in data.get("plan", [])),
            success=tuple(dict(item) for item in data.get("success", [])),
            termination=tuple(dict(item) for item in data.get("termination", [])),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class AnchorSpec:
    """Uncertain visual constraints extracted from an image or video."""

    anchor_id: str
    media_type: str
    media_uri: str
    media_sha256: str
    confidence: float
    observations: dict[str, Any]
    object_constraints: tuple[dict[str, Any], ...] = ()
    spatial_constraints: tuple[dict[str, Any], ...] = ()
    camera_constraints: tuple[dict[str, Any], ...] = ()
    appearance_constraints: tuple[dict[str, Any], ...] = ()
    motion_constraints: tuple[dict[str, Any], ...] = ()
    evidence: tuple[dict[str, Any], ...] = ()
    uncertainty: tuple[str, ...] = ()

    def validate(self) -> None:
        _identifier(self.anchor_id, field_name="anchor_id")
        if self.media_type not in {"image", "video"}:
            raise IRValidationError("anchor.media_type must be image or video")
        if not self.media_uri:
            raise IRValidationError("anchor.media_uri is empty")
        if not re.fullmatch(r"[0-9a-f]{64}", self.media_sha256):
            raise IRValidationError("anchor.media_sha256 is invalid")
        if not 0.0 <= self.confidence <= 1.0:
            raise IRValidationError("anchor.confidence must be in [0, 1]")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AnchorSpec":
        return cls(
            anchor_id=str(data["anchor_id"]),
            media_type=str(data["media_type"]),
            media_uri=str(data["media_uri"]),
            media_sha256=str(data["media_sha256"]),
            confidence=float(data["confidence"]),
            observations=dict(data.get("observations") or {}),
            object_constraints=tuple(dict(item) for item in data.get("object_constraints", [])),
            spatial_constraints=tuple(dict(item) for item in data.get("spatial_constraints", [])),
            camera_constraints=tuple(dict(item) for item in data.get("camera_constraints", [])),
            appearance_constraints=tuple(dict(item) for item in data.get("appearance_constraints", [])),
            motion_constraints=tuple(dict(item) for item in data.get("motion_constraints", [])),
            evidence=tuple(dict(item) for item in data.get("evidence", [])),
            uncertainty=tuple(str(value) for value in data.get("uncertainty", [])),
        )


@dataclass(frozen=True)
class EnvironmentPackage:
    """Complete portable environment and task package."""

    package_id: str
    env: EnvSpec
    assets: tuple[AssetBundle, ...]
    task: TaskSpec
    anchors: tuple[AnchorSpec, ...] = ()
    source: dict[str, Any] = field(default_factory=dict)
    target_backends: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise IRValidationError(
                f"unsupported schema_version {self.schema_version!r}; expected {SCHEMA_VERSION!r}"
            )
        _identifier(self.package_id, field_name="package_id")
        self.env.validate()
        self.task.validate()
        asset_ids: set[str] = set()
        for asset in self.assets:
            asset.validate()
            if asset.asset_id in asset_ids:
                raise IRValidationError(f"duplicate asset id: {asset.asset_id}")
            asset_ids.add(asset.asset_id)
        missing = sorted({obj.asset_id for obj in self.env.objects} - asset_ids)
        if missing:
            raise IRValidationError(f"scene objects reference missing assets: {missing}")
        for anchor in self.anchors:
            anchor.validate()
        if len(set(self.target_backends)) != len(self.target_backends):
            raise IRValidationError("target_backends contains duplicates")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    def canonical_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def write_json(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(path)
        return path

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EnvironmentPackage":
        package = cls(
            package_id=str(data["package_id"]),
            env=EnvSpec.from_dict(data["env"]),
            assets=tuple(AssetBundle.from_dict(item) for item in data.get("assets", [])),
            task=TaskSpec.from_dict(data["task"]),
            anchors=tuple(AnchorSpec.from_dict(item) for item in data.get("anchors", [])),
            source=dict(data.get("source") or {}),
            target_backends=tuple(str(value) for value in data.get("target_backends", [])),
            metadata=dict(data.get("metadata") or {}),
            schema_version=str(data.get("schema_version", SCHEMA_VERSION)),
        )
        package.validate()
        return package

    @classmethod
    def read_json(cls, path: str | Path) -> "EnvironmentPackage":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

