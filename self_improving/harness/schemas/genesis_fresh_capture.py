"""A new camera event restoring a verified final pose in a separate fixed runtime."""

from typing import Literal

from pydantic import UUID4, AwareDatetime, Field, model_validator

from .base import HarnessModel, Sha256, public_schema_config
from .common import ArtifactRef
from .genesis_runtime_manifest import RuntimeManifestMemberV1, RuntimeProcessIdentityV1
from .genesis_scene_replay import GenesisSceneBodyStateV1

FRESH_CAPTURE_INPUT_ID = "harness.genesis_fresh_capture_input.v1"
FRESH_CAPTURE_REQUEST_ID = "harness.genesis_fresh_capture_request.v1"
FRESH_CAPTURE_RESULT_ID = "harness.genesis_fresh_capture_result.v1"
FRESH_CAPTURE_RECEIPT_ID = "harness.genesis_fresh_capture_receipt.v1"
FRESH_CAPTURE_QUALIFICATION_ID = "harness.genesis_fresh_capture_qualification.v1"


class GenesisFreshCaptureInputV1(HarnessModel):
    model_config = public_schema_config(FRESH_CAPTURE_INPUT_ID)
    schema_version: Literal[FRESH_CAPTURE_INPUT_ID]
    replay_run_id: UUID4
    ttl_seconds: int = Field(strict=True, ge=1, le=300)


class GenesisFreshCaptureRequestV1(HarnessModel):
    model_config = public_schema_config(FRESH_CAPTURE_REQUEST_ID)
    schema_version: Literal[FRESH_CAPTURE_REQUEST_ID]
    replay_run_id: UUID4
    replay_receipt: ArtifactRef
    scene: ArtifactRef
    half_dt_rows: ArtifactRef
    final_state: GenesisSceneBodyStateV1
    source_step: Literal[2000]
    source_time_s: Literal[4.0]
    capture_runtime_manifest: ArtifactRef
    restoration: Literal["restored_final_pose_zero_velocity"]

    @model_validator(mode="after")
    def exact_sources(self):
        for ref, schema in (
            (self.replay_receipt, "harness.genesis_scene_execution_receipt.v2"),
            (self.scene, "harness.genesis_scene_spec.v1"),
            (self.capture_runtime_manifest, "harness.genesis_runtime_manifest.v1"),
        ):
            if ref.schema_version != schema or ref.media_type != "application/json":
                raise ValueError("fresh capture requires exact versioned source references")
        for ref in (
            self.replay_receipt,
            self.scene,
            self.capture_runtime_manifest,
            self.half_dt_rows,
        ):
            if ref.uri != "artifact://sha256/" + ref.sha256:
                raise ValueError("fresh capture sources must be content-addressed")
        return self


class GenesisFreshCaptureResultV1(HarnessModel):
    model_config = public_schema_config(FRESH_CAPTURE_RESULT_ID)
    schema_version: Literal[FRESH_CAPTURE_RESULT_ID]
    request: ArtifactRef
    image: ArtifactRef
    captured_at: AwareDatetime
    pixels_sha256: Sha256
    width: Literal[640]
    height: Literal[480]
    raw_load: ArtifactRef
    raw_observation: ArtifactRef
    process_identity: RuntimeProcessIdentityV1
    environment_delta: dict[Literal["LD_LIBRARY_PATH"], str]
    modules: tuple[RuntimeManifestMemberV1, ...] = Field(min_length=1)
    native_files: tuple[RuntimeManifestMemberV1, ...] = Field(min_length=1)
    landlock_abi: int = Field(strict=True, ge=6)
    network_denied: bool = Field(strict=True)
    runtime_write_denied: bool = Field(strict=True)
    torch_threads: tuple[Literal[1], Literal[1]]
    physics_steps: Literal[0]
    restore_velocity_policy: Literal["zero"]
    process_continuation: Literal[False]
    stdout: ArtifactRef
    stderr: ArtifactRef
    retained_index: ArtifactRef

    @model_validator(mode="after")
    def actual_capture(self):
        if self.captured_at.utcoffset().total_seconds() != 0:
            raise ValueError("actual capture timestamp must be UTC")
        if not self.network_denied or not self.runtime_write_denied:
            raise ValueError("capture must observe both runtime restrictions")
        if self.image.media_type != "image/png":
            raise ValueError("capture requires actual PNG bytes")
        return self


class GenesisFreshCaptureReceiptV1(HarnessModel):
    model_config = public_schema_config(FRESH_CAPTURE_RECEIPT_ID)
    schema_version: Literal[FRESH_CAPTURE_RECEIPT_ID]
    replay_run_id: UUID4
    replay_receipt: ArtifactRef
    request: ArtifactRef
    capture_result: ArtifactRef
    image: ArtifactRef
    captured_at: AwareDatetime
    checked_at: AwareDatetime
    expires_at: AwareDatetime
    ttl_seconds: int = Field(strict=True, ge=1, le=300)
    authority: Literal["observed_restored_final_pose"]
    process_continuation: Literal[False]

    @model_validator(mode="after")
    def fresh_at_issuance(self):
        if any(
            value.utcoffset().total_seconds() != 0
            for value in (self.captured_at, self.checked_at, self.expires_at)
        ):
            raise ValueError("capture lifecycle timestamps must be UTC")
        if not 0 <= (self.checked_at - self.captured_at).total_seconds() <= self.ttl_seconds:
            raise ValueError("capture was not fresh at the recorded check")
        if (self.expires_at - self.captured_at).total_seconds() != self.ttl_seconds:
            raise ValueError("capture expiration must preserve its original timestamp")
        return self


class GenesisFreshCaptureQualificationV1(HarnessModel):
    model_config = public_schema_config(FRESH_CAPTURE_QUALIFICATION_ID)
    schema_version: Literal[FRESH_CAPTURE_QUALIFICATION_ID]
    skill_ref: Literal["genesis.observe@3.0.0"]
    candidate_runs: tuple[UUID4, UUID4]
    receipts: tuple[ArtifactRef, ArtifactRef]
    capture_manifest: ArtifactRef
    replay_qualification: ArtifactRef
    implementation: tuple[ArtifactRef, ...] = Field(min_length=1)
    implementation_sha256: Sha256
