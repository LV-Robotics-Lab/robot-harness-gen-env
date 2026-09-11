"""Assessment of an already qualified rigid-scene replay, without robot-policy claims."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .base import HarnessModel, public_schema_config
from .common import ArtifactRef

SCENE_VALIDATE_INPUT_ID = "harness.genesis_scene_validate_input.v1"
SCENE_VALIDATION_ID = "harness.genesis_scene_validation.v1"
SCENE_VALIDATION_QUALIFICATION_ID = "harness.genesis_scene_validation_qualification.v1"
SCENE_FRESH_OBSERVATION_ID = "harness.genesis_scene_fresh_observation.v1"
SCENE_OBSERVE_INPUT_ID = "harness.genesis_scene_observe_input.v1"
SCENE_OBSERVE_QUALIFICATION_ID = "harness.genesis_scene_observe_qualification.v1"


class GenesisSceneObserveInputV1(HarnessModel):
    model_config = public_schema_config(SCENE_OBSERVE_INPUT_ID)
    schema_version: Literal[SCENE_OBSERVE_INPUT_ID]
    replay_run_id: UUID
    ttl_seconds: int = Field(strict=True, ge=1, le=300)


class GenesisSceneObserveQualificationV1(HarnessModel):
    model_config = public_schema_config(SCENE_OBSERVE_QUALIFICATION_ID)
    schema_version: Literal[SCENE_OBSERVE_QUALIFICATION_ID]
    skill_ref: Literal["genesis.observe@2.0.0"]
    candidate_runs: tuple[UUID, UUID]
    observations: tuple[ArtifactRef, ArtifactRef]
    implementation_sha256: str


class GenesisSceneValidationQualificationV1(HarnessModel):
    model_config = public_schema_config(SCENE_VALIDATION_QUALIFICATION_ID)
    schema_version: Literal[SCENE_VALIDATION_QUALIFICATION_ID]
    skill_ref: Literal["image2env.validate@2.0.0"]
    candidate_runs: tuple[UUID, UUID]
    validations: tuple[ArtifactRef, ArtifactRef]
    implementation_sha256: str


class GenesisSceneValidateInputV1(HarnessModel):
    model_config = public_schema_config(SCENE_VALIDATE_INPUT_ID)
    schema_version: Literal[SCENE_VALIDATE_INPUT_ID]
    replay_run_id: UUID


class GenesisSceneValidationV1(HarnessModel):
    model_config = public_schema_config(SCENE_VALIDATION_ID)
    schema_version: Literal[SCENE_VALIDATION_ID]
    scope: Literal["genesis.rigid_scene@1"]
    status: Literal["passed"]
    replay_run_id: UUID
    invocation_digest: str
    execution_receipt: ArtifactRef
    scene: ArtifactRef
    replay: ArtifactRef
    report: ArtifactRef
    assessed_at: AwareDatetime
    simulator_rerun: Literal[False]
    robot_policy_evaluated: Literal[False]
    candidate_only: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def exact_lineage(self):
        for ref, schema in (
            (self.execution_receipt, "harness.genesis_scene_execution_receipt.v2"),
            (self.scene, "harness.genesis_scene_spec.v1"),
            (self.replay, "harness.genesis_scene_replay.v1"),
            (self.report, "harness.genesis_scene_report.v1"),
        ):
            if (
                ref.schema_version != schema
                or ref.media_type != "application/json"
                or ref.uri != "artifact://sha256/" + ref.sha256
            ):
                raise ValueError("scene assessment requires exact canonical lineage references")
        if self.assessed_at.utcoffset().total_seconds() != 0:
            raise ValueError("assessment timestamp must be UTC")
        if len(self.invocation_digest) != 64 or any(
            c not in "0123456789abcdef" for c in self.invocation_digest
        ):
            raise ValueError("assessment invocation digest must be canonical SHA-256")
        return self


class GenesisSceneFreshObservationV1(HarnessModel):
    """An existing actual camera event whose freshness was checked when read."""

    model_config = public_schema_config(SCENE_FRESH_OBSERVATION_ID)
    schema_version: Literal[SCENE_FRESH_OBSERVATION_ID]
    replay_run_id: UUID
    execution_receipt: ArtifactRef
    process_observation: ArtifactRef
    image: ArtifactRef
    captured_at: AwareDatetime
    checked_at: AwareDatetime
    expires_at: AwareDatetime
    simulation_time_s: float = Field(strict=True, ge=4.0, le=4.0)
    step: int = Field(strict=True, ge=2000, le=2000)
    profile: Literal["half_dt"]
    simulator_rerun: Literal[False]
    candidate_only: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def actual_capture_lineage(self):
        from .genesis_scene_execution import _reference

        _reference(self.execution_receipt, "harness.genesis_scene_execution_receipt.v2")
        _reference(self.process_observation, "harness.genesis_scene_process_observation.v2")
        if (
            self.image.media_type != "image/png"
            or self.image.uri != "artifact://sha256/" + self.image.sha256
        ):
            raise ValueError("fresh observation requires a canonical PNG")
        for stamp in (self.captured_at, self.checked_at, self.expires_at):
            if stamp.utcoffset().total_seconds() != 0:
                raise ValueError("fresh observation timestamps must be UTC")
        if not self.captured_at <= self.checked_at <= self.expires_at:
            raise ValueError("fresh observation is outside its capture lifetime")
        if not 1 <= (self.expires_at - self.captured_at).total_seconds() <= 300:
            raise ValueError("fresh observation lifetime exceeds TTL bounds")
        return self
