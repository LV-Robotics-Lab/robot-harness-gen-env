"""Finite tabletop scene and Gujie-derived dual-dt evidence; no execution issuer."""

import math
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .base import HarnessModel, public_schema_config
from .common import ArtifactRef

Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Vec3 = tuple[Finite, Finite, Finite]
Quaternion = tuple[Finite, Finite, Finite, Finite]
Profile = Literal["baseline", "half_dt"]
SCENE_SPEC_ID = "harness.genesis_scene_spec.v1"
SCENE_ROW_ID = "harness.genesis_scene_trace_row.v1"
SCENE_REPLAY_ID = "harness.genesis_scene_replay.v1"
SCENE_REPORT_ID = "harness.genesis_scene_report.v1"


def quaternion(value):
    if abs(sum(x * x for x in value) - 1) > 1e-5:
        raise ValueError("orientation must be a unit wxyz quaternion")


class GenesisSceneSpecV1(HarnessModel):
    model_config = public_schema_config(SCENE_SPEC_ID)
    schema_version: Literal[SCENE_SPEC_ID]
    source: ArtifactRef
    representation: ArtifactRef
    geometry: ArtifactRef
    object_id: Literal["object"]
    position_m: Vec3
    orientation_wxyz: Quaternion
    local_vertices_m: tuple[Vec3, ...] = Field(min_length=4)
    table_center_m: Vec3
    table_size_m: Vec3
    table_friction: Finite = Field(ge=0)
    ground_z_m: Literal[0.0]

    @field_validator("ground_z_m", mode="before")
    @classmethod
    def numeric_ground(cls, value):
        if isinstance(value, bool):
            raise ValueError("ground height cannot be boolean")
        return value

    @model_validator(mode="after")
    def valid_scene(self):
        quaternion(self.orientation_wxyz)
        if min(self.table_size_m) <= 0 or self.table_center_m[2] - self.table_size_m[2] / 2 < 0:
            raise ValueError("finite table must have positive extents above ground")
        for ref in (self.source, self.representation, self.geometry):
            if ref.uri != f"artifact://sha256/{ref.sha256}":
                raise ValueError("scene inputs require CAS identities")
        if self.representation.schema_version != "harness.asset_representation.v1":
            raise ValueError("scene requires portable asset representation")
        return self


class GenesisSceneBodyStateV1(HarnessModel):
    position: Vec3
    orientation_wxyz: Quaternion
    velocity: Vec3
    angular_velocity: Vec3

    @model_validator(mode="after")
    def valid_orientation(self):
        quaternion(self.orientation_wxyz)
        return self


class GenesisSceneContactV1(HarnessModel):
    a: Literal["object", "table", "ground"]
    b: Literal["object", "table", "ground"]
    position: Vec3
    normal: Vec3
    penetration: Finite = Field(ge=0)
    force_a: Vec3 | None
    force_b: Vec3 | None


class GenesisSceneTraceRowV1(HarnessModel):
    model_config = public_schema_config(SCENE_ROW_ID)
    schema_version: Literal[SCENE_ROW_ID]
    step: int = Field(strict=True, ge=0)
    time_s: Finite = Field(ge=0)
    contact_phase: Literal["initial_detection", "solved_step"]
    objects: dict[str, GenesisSceneBodyStateV1]
    contacts: tuple[GenesisSceneContactV1, ...]

    @model_validator(mode="after")
    def valid_row(self):
        if set(self.objects) != {"object", "table", "ground"}:
            raise ValueError("scene trace must contain object, finite table and ground")
        if self.contact_phase != ("initial_detection" if self.step == 0 else "solved_step"):
            raise ValueError("contact phase differs from row step")
        for contact in self.contacts:
            if (
                contact.a == contact.b
                or abs(math.sqrt(sum(v * v for v in contact.normal)) - 1) > 1e-4
            ):
                raise ValueError("invalid contact pair or unit normal")
            if self.step == 0:
                if contact.force_a is not None or contact.force_b is not None:
                    raise ValueError("initial contact force is unavailable")
            elif (
                contact.force_a is None
                or contact.force_b is None
                or any(
                    not math.isclose(a, -b, abs_tol=1e-7, rel_tol=1e-6)
                    for a, b in zip(contact.force_a, contact.force_b)
                )
            ):
                raise ValueError("solved contact forces must be equal and opposite")
        return self


class GenesisSceneFrameV1(HarnessModel):
    step: int = Field(strict=True, ge=0)
    image: ArtifactRef


class GenesisSceneProfileEvidenceV1(HarnessModel):
    profile: Profile
    rows: ArtifactRef
    loaded_scene: ArtifactRef
    frames: tuple[GenesisSceneFrameV1, ...] = Field(min_length=41, max_length=41)
    video: ArtifactRef


class GenesisSceneReplayV1(HarnessModel):
    model_config = public_schema_config(SCENE_REPLAY_ID)
    schema_version: Literal[SCENE_REPLAY_ID]
    scene: ArtifactRef
    runtime_manifest: ArtifactRef
    baseline: GenesisSceneProfileEvidenceV1
    half_dt: GenesisSceneProfileEvidenceV1


class GenesisSceneCheckV1(HarnessModel):
    profile: Literal["baseline", "half_dt", "agreement"]
    name: str
    category: Literal["scene_truth", "numerics_coupled", "diagnostic"]
    observed: Finite
    limit: Finite
    passed: bool = Field(strict=True)


class GenesisSceneMediaSummaryV1(HarnessModel):
    profile: Profile
    total_frames: Literal[41]
    unique_frames: int = Field(strict=True, ge=1, le=41)
    width: int = Field(strict=True, gt=0)
    height: int = Field(strict=True, gt=0)
    fps: Literal[10]
    last_frame_time_s: Literal[4.0]


class GenesisSceneReportV1(HarnessModel):
    model_config = public_schema_config(SCENE_REPORT_ID)
    schema_version: Literal[SCENE_REPORT_ID]
    authority: Literal["trace_consistency_only"]
    status: Literal["passed", "failed", "inconclusive"]
    numerics_tunable: bool = Field(strict=True)
    checks: tuple[GenesisSceneCheckV1, ...]
    media: tuple[GenesisSceneMediaSummaryV1, ...] = ()
