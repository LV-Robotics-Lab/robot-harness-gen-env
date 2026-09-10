"""Strict contracts for one fresh Genesis observation capture."""

from __future__ import annotations

from datetime import timedelta, timezone
from typing import Annotated, Literal

from pydantic import UUID4, AwareDatetime, Field, JsonValue, StrictBool, model_validator

from ..trusted_state import FactKey
from .base import HarnessModel, NonNegativeInt, public_schema_config
from .common import ArtifactRef

GENESIS_OBSERVE_INPUT_SCHEMA_ID = "harness.genesis_observe_input.v1"
GENESIS_OBSERVE_OUTPUT_SCHEMA_ID = "harness.genesis_observe_output.v1"
GENESIS_OBSERVATION_RECEIPT_SCHEMA_ID = "harness.genesis_observation_receipt.v1"

X2ENV_ENVIRONMENT_PACKAGE_SCHEMA = "harness.x2env_environment_package.v1"
WORKFLOW_OPERATION_RECEIPT_SCHEMA = "harness.workflow_operation_receipt.v2"
X2ENV_RUNTIME_RECEIPT_SCHEMA = "harness.x2env_runtime_receipt.v1"
GENESIS_CHECKPOINT_SCHEMA = "harness.genesis_checkpoint.v1"
OBSERVATION_MEDIA_VERIFICATION_SCHEMA = "harness.genesis_observation_media_verification.v1"

CameraId = Annotated[
    str,
    Field(strict=True, pattern=r"^[a-z][a-z0-9_]{0,63}$"),
]
TTLSeconds = Annotated[int, Field(strict=True, ge=1, le=300)]


def _require_cas_ref(
    ref: ArtifactRef,
    field_name: str,
    *,
    schema_version: str | None = None,
    media_type: str | None = None,
    media_prefix: str | None = None,
) -> None:
    if ref.uri != f"artifact://sha256/{ref.sha256}":
        raise ValueError(f"{field_name}.uri must be content-addressed by its sha256")
    if schema_version is not None and ref.schema_version != schema_version:
        raise ValueError(f"{field_name}.schema_version must be {schema_version!r}")
    if media_type is not None and ref.media_type != media_type:
        raise ValueError(f"{field_name}.media_type must be {media_type!r}")
    if media_prefix is not None and not ref.media_type.startswith(media_prefix):
        raise ValueError(f"{field_name}.media_type must start with {media_prefix!r}")


def _require_utc(value: AwareDatetime, *, field_name: str) -> None:
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{field_name} must use UTC")


def _require_media_refs(
    *,
    images: tuple[ArtifactRef, ...],
    video: ArtifactRef | None,
    media_verification: ArtifactRef,
) -> None:
    identities: list[tuple[str, str, str]] = []
    for index, image in enumerate(images):
        _require_cas_ref(image, f"images[{index}]", media_prefix="image/")
        identities.append((image.name, image.sha256, image.media_type))
    if identities != sorted(identities) or len(identities) != len(set(identities)):
        raise ValueError("images must be canonically sorted and unique")
    if video is not None:
        _require_cas_ref(video, "video", media_prefix="video/")
    _require_cas_ref(
        media_verification,
        "media_verification",
        schema_version=OBSERVATION_MEDIA_VERIFICATION_SCHEMA,
        media_type="application/json",
    )


class GenesisObservationCapturePlan(HarnessModel):
    """Bounded capture request; a production adapter must execute the requested steps."""

    camera_ids: tuple[CameraId, ...] = Field(min_length=1, max_length=8)
    stills_per_camera: Literal[1] = 1
    video_frames: Annotated[int, Field(strict=True, ge=0, le=300)] = 0
    fps: Annotated[int, Field(strict=True, ge=1, le=120)] | None = None
    post_restore_steps: Annotated[int, Field(strict=True, ge=1, le=10_000)]

    @model_validator(mode="after")
    def plan_is_canonical_and_coherent(self) -> "GenesisObservationCapturePlan":
        if tuple(sorted(self.camera_ids)) != self.camera_ids or len(self.camera_ids) != len(
            set(self.camera_ids)
        ):
            raise ValueError("camera_ids must be canonically sorted and unique")
        if (self.video_frames == 0) != (self.fps is None):
            raise ValueError("fps must exist exactly when video_frames is nonzero")
        return self


class GenesisObserveInput(HarnessModel):
    """Exact source lineage and bounded capture plan for ``genesis.observe@1.0.0``."""

    model_config = public_schema_config(GENESIS_OBSERVE_INPUT_SCHEMA_ID)

    schema_version: Literal[GENESIS_OBSERVE_INPUT_SCHEMA_ID]
    environment_package: ArtifactRef
    replay_workflow_receipt: ArtifactRef
    replay_runtime_receipt: ArtifactRef
    checkpoint: ArtifactRef | None
    capture_plan: GenesisObservationCapturePlan
    requested_observation_keys: tuple[FactKey, ...] = Field(min_length=1, max_length=32)
    ttl_seconds: TTLSeconds

    @model_validator(mode="after")
    def source_lineage_is_typed(self) -> "GenesisObserveInput":
        for ref, name, schema in (
            (
                self.environment_package,
                "environment_package",
                X2ENV_ENVIRONMENT_PACKAGE_SCHEMA,
            ),
            (
                self.replay_workflow_receipt,
                "replay_workflow_receipt",
                WORKFLOW_OPERATION_RECEIPT_SCHEMA,
            ),
            (
                self.replay_runtime_receipt,
                "replay_runtime_receipt",
                X2ENV_RUNTIME_RECEIPT_SCHEMA,
            ),
        ):
            _require_cas_ref(
                ref,
                name,
                schema_version=schema,
                media_type="application/json",
            )
        if self.checkpoint is not None:
            _require_cas_ref(
                self.checkpoint,
                "checkpoint",
                schema_version=GENESIS_CHECKPOINT_SCHEMA,
                media_type="application/json",
            )
        if tuple(sorted(self.requested_observation_keys)) != self.requested_observation_keys or len(
            self.requested_observation_keys
        ) != len(set(self.requested_observation_keys)):
            raise ValueError("requested_observation_keys must be canonically sorted and unique")
        return self

    def source_artifacts(self) -> tuple[ArtifactRef, ...]:
        """Return the complete caller-declared source closure in canonical field order."""

        values = (
            self.environment_package,
            self.replay_workflow_receipt,
            self.replay_runtime_receipt,
        )
        return values if self.checkpoint is None else (*values, self.checkpoint)


class GenesisObservationValue(HarnessModel):
    """One caller-requested fact value bound to the captured media payload."""

    key: FactKey
    value: JsonValue
    captured_at: AwareDatetime
    valid_until: AwareDatetime
    payload_refs: tuple[ArtifactRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def value_is_time_and_payload_bound(self) -> "GenesisObservationValue":
        _require_utc(self.captured_at, field_name="captured_at")
        _require_utc(self.valid_until, field_name="valid_until")
        if self.valid_until <= self.captured_at:
            raise ValueError("observation valid_until must follow captured_at")
        identities: list[tuple[str, str, str]] = []
        for index, ref in enumerate(self.payload_refs):
            _require_cas_ref(ref, f"payload_refs[{index}]")
            identities.append((ref.sha256, ref.schema_version or "", ref.media_type))
        if identities != sorted(identities) or len(identities) != len(set(identities)):
            raise ValueError("payload_refs must be canonically sorted and unique")
        return self


class GenesisObservationReceipt(HarnessModel):
    """Capture receipt later bound to a parent workflow by its trusted ToolResult."""

    model_config = public_schema_config(GENESIS_OBSERVATION_RECEIPT_SCHEMA_ID)

    schema_version: Literal[GENESIS_OBSERVATION_RECEIPT_SCHEMA_ID]
    observation_child_run_id: UUID4
    environment_package: ArtifactRef
    replay_workflow_receipt: ArtifactRef
    replay_runtime_receipt: ArtifactRef
    checkpoint: ArtifactRef | None
    capture_plan: GenesisObservationCapturePlan
    requested_observation_keys: tuple[FactKey, ...] = Field(min_length=1, max_length=32)
    captured_at: AwareDatetime
    valid_until: AwareDatetime
    ttl_seconds: TTLSeconds
    images: tuple[ArtifactRef, ...] = Field(min_length=1)
    video: ArtifactRef | None
    media_verification: ArtifactRef
    observations: tuple[GenesisObservationValue, ...] = Field(min_length=1)
    simulator_executed: StrictBool
    post_restore_steps_executed: NonNegativeInt

    @model_validator(mode="after")
    def capture_is_fresh_and_internally_bound(self) -> "GenesisObservationReceipt":
        GenesisObserveInput(
            schema_version=GENESIS_OBSERVE_INPUT_SCHEMA_ID,
            environment_package=self.environment_package,
            replay_workflow_receipt=self.replay_workflow_receipt,
            replay_runtime_receipt=self.replay_runtime_receipt,
            checkpoint=self.checkpoint,
            capture_plan=self.capture_plan,
            requested_observation_keys=self.requested_observation_keys,
            ttl_seconds=self.ttl_seconds,
        )
        _require_utc(self.captured_at, field_name="captured_at")
        _require_utc(self.valid_until, field_name="valid_until")
        if self.valid_until != self.captured_at + timedelta(seconds=self.ttl_seconds):
            raise ValueError("valid_until must equal captured_at plus ttl_seconds")
        _require_media_refs(
            images=self.images,
            video=self.video,
            media_verification=self.media_verification,
        )
        if len(self.images) != len(self.capture_plan.camera_ids):
            raise ValueError("images must contain one still for every requested camera")
        if (self.capture_plan.video_frames == 0) != (self.video is None):
            raise ValueError("video must exist exactly when video_frames is nonzero")
        keys = tuple(item.key for item in self.observations)
        if keys != self.requested_observation_keys:
            raise ValueError("observation keys must equal requested_observation_keys")
        payloads = {*self.images}
        if self.video is not None:
            payloads.add(self.video)
        for observation in self.observations:
            if (
                observation.captured_at != self.captured_at
                or observation.valid_until != self.valid_until
            ):
                raise ValueError("observation times must equal the receipt capture window")
            if not set(observation.payload_refs).issubset(payloads):
                raise ValueError("observation payload_refs must belong to receipt media")
        if not self.simulator_executed and self.post_restore_steps_executed != 0:
            raise ValueError("controlled observation cannot claim runtime steps")
        if self.simulator_executed and (
            self.post_restore_steps_executed != self.capture_plan.post_restore_steps
        ):
            raise ValueError("runtime observation must execute every requested post-restore step")
        return self


class GenesisObserveOutput(HarnessModel):
    """Structured capture result; media remain reachable by explicit CAS refs."""

    model_config = public_schema_config(GENESIS_OBSERVE_OUTPUT_SCHEMA_ID)

    schema_version: Literal[GENESIS_OBSERVE_OUTPUT_SCHEMA_ID]
    observation_receipt: ArtifactRef
    images: tuple[ArtifactRef, ...] = Field(min_length=1)
    video: ArtifactRef | None
    media_verification: ArtifactRef
    captured_at: AwareDatetime
    valid_until: AwareDatetime
    simulator_executed: StrictBool

    @model_validator(mode="after")
    def output_refs_are_typed(self) -> "GenesisObserveOutput":
        _require_cas_ref(
            self.observation_receipt,
            "observation_receipt",
            schema_version=GENESIS_OBSERVATION_RECEIPT_SCHEMA_ID,
            media_type="application/json",
        )
        _require_media_refs(
            images=self.images,
            video=self.video,
            media_verification=self.media_verification,
        )
        _require_utc(self.captured_at, field_name="captured_at")
        _require_utc(self.valid_until, field_name="valid_until")
        if self.valid_until <= self.captured_at:
            raise ValueError("valid_until must follow captured_at")
        return self


__all__ = [
    "GENESIS_OBSERVATION_RECEIPT_SCHEMA_ID",
    "GENESIS_OBSERVE_INPUT_SCHEMA_ID",
    "GENESIS_OBSERVE_OUTPUT_SCHEMA_ID",
    "GenesisObservationCapturePlan",
    "GenesisObservationReceipt",
    "GenesisObservationValue",
    "GenesisObserveInput",
    "GenesisObserveOutput",
]
