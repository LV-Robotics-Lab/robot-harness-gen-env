"""Scene execution with actual camera events and directly addressable browser media."""

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from .base import HarnessModel, Sha256, public_schema_config
from .common import ArtifactRef
from .genesis_runtime_manifest import RuntimeManifestMemberV1, RuntimeProcessIdentityV1
from .genesis_scene_execution import SCENE_FILESYSTEM_POLICY, _reference

SCENE_EXECUTION_REQUEST_V2_ID = "harness.genesis_scene_execution_request.v2"
SCENE_PROCESS_OBSERVATION_V2_ID = "harness.genesis_scene_process_observation.v2"
SCENE_LAUNCH_RESULT_V2_ID = "harness.genesis_scene_launch_result.v2"
SCENE_EXECUTION_RECEIPT_V2_ID = "harness.genesis_scene_execution_receipt.v2"


class GenesisSceneExecutionRequestV2(HarnessModel):
    model_config = public_schema_config(SCENE_EXECUTION_REQUEST_V2_ID)
    schema_version: Literal[SCENE_EXECUTION_REQUEST_V2_ID]
    scene: ArtifactRef
    compile_receipt: ArtifactRef
    runtime_manifest: ArtifactRef
    asset_qualification: ArtifactRef

    @model_validator(mode="after")
    def exact_inputs(self):
        for ref, schema in (
            (self.scene, "harness.genesis_scene_spec.v1"),
            (self.compile_receipt, "harness.image2env_genesis_compile_receipt.v1"),
            (self.runtime_manifest, "harness.genesis_runtime_manifest.v1"),
            (self.asset_qualification, "harness.asset_qualification.v1"),
        ):
            _reference(ref, schema)
        return self


class GenesisSceneCaptureEventV2(HarnessModel):
    step: int = Field(strict=True, ge=0)
    time_s: float = Field(strict=True, ge=0, allow_inf_nan=False)
    captured_at: AwareDatetime
    pixels_sha256: Sha256
    width: int = Field(strict=True, gt=0)
    height: int = Field(strict=True, gt=0)
    image: ArtifactRef

    @model_validator(mode="after")
    def actual_utc_png(self):
        if self.captured_at.utcoffset().total_seconds() != 0:
            raise ValueError("capture timestamp must be UTC")
        if (
            self.image.uri != f"artifact://sha256/{self.image.sha256}"
            or self.image.media_type != "image/png"
            or self.image.schema_version is not None
        ):
            raise ValueError("capture event requires an exact PNG CAS reference")
        return self


class GenesisSceneProcessObservationV2(HarnessModel):
    model_config = public_schema_config(SCENE_PROCESS_OBSERVATION_V2_ID)
    schema_version: Literal[SCENE_PROCESS_OBSERVATION_V2_ID]
    request: ArtifactRef
    profile: Literal["baseline", "half_dt"]
    process_identity: RuntimeProcessIdentityV1
    environment_delta: dict[Literal["LD_LIBRARY_PATH"], str]
    modules: tuple[RuntimeManifestMemberV1, ...] = Field(min_length=1)
    native_files: tuple[RuntimeManifestMemberV1, ...] = Field(min_length=1)
    external_native_files: tuple[()]
    torch_threads: tuple[Literal[1], Literal[1]]
    landlock_abi: int = Field(strict=True, ge=6)
    network_denied: bool = Field(strict=True)
    runtime_write_denied: bool = Field(strict=True)
    kernel_devices: tuple[Literal["/dev/null"], Literal["/dev/urandom"]]
    network_policy: Literal["socket_syscalls_denied"]
    filesystem_policy: Literal[SCENE_FILESYSTEM_POLICY]
    stdout: ArtifactRef
    stderr: ArtifactRef
    raw_observation: ArtifactRef
    captures: tuple[GenesisSceneCaptureEventV2, ...] = Field(min_length=41, max_length=41)

    @model_validator(mode="after")
    def actual_denials(self):
        _reference(self.request, SCENE_EXECUTION_REQUEST_V2_ID)
        _reference(self.raw_observation, None)
        if not self.network_denied or not self.runtime_write_denied:
            raise ValueError("both actual confinement denial probes must pass")
        return self


class GenesisSceneLaunchResultV2(HarnessModel):
    model_config = public_schema_config(SCENE_LAUNCH_RESULT_V2_ID)
    schema_version: Literal[SCENE_LAUNCH_RESULT_V2_ID]
    request: ArtifactRef
    replay: ArtifactRef
    baseline_observation: ArtifactRef
    half_dt_observation: ArtifactRef
    baseline_preview: ArtifactRef
    half_dt_preview: ArtifactRef
    retained_index: ArtifactRef

    @model_validator(mode="after")
    def exact_outputs(self):
        _reference(self.request, SCENE_EXECUTION_REQUEST_V2_ID)
        _reference(self.replay, "harness.genesis_scene_replay.v1")
        for ref in (self.baseline_observation, self.half_dt_observation):
            _reference(ref, SCENE_PROCESS_OBSERVATION_V2_ID)
        _media(self.baseline_preview, self.half_dt_preview, self.retained_index)
        return self


class GenesisSceneExecutionReceiptV2(HarnessModel):
    model_config = public_schema_config(SCENE_EXECUTION_RECEIPT_V2_ID)
    schema_version: Literal[SCENE_EXECUTION_RECEIPT_V2_ID]
    authority: Literal["observed_execution_only"]
    request: ArtifactRef
    launch_result: ArtifactRef
    replay: ArtifactRef
    report: ArtifactRef
    baseline_preview: ArtifactRef
    half_dt_preview: ArtifactRef
    retained_index: ArtifactRef

    @model_validator(mode="after")
    def exact_closure(self):
        for ref, schema in (
            (self.request, SCENE_EXECUTION_REQUEST_V2_ID),
            (self.launch_result, SCENE_LAUNCH_RESULT_V2_ID),
            (self.replay, "harness.genesis_scene_replay.v1"),
            (self.report, "harness.genesis_scene_report.v1"),
        ):
            _reference(ref, schema)
        _media(self.baseline_preview, self.half_dt_preview, self.retained_index)
        return self


def _media(baseline, half_dt, index):
    for ref in (baseline, half_dt):
        if (
            ref.uri != f"artifact://sha256/{ref.sha256}"
            or ref.media_type != "video/mp4"
            or ref.schema_version is not None
        ):
            raise ValueError("scene preview must be a direct MP4 CAS reference")
    _reference(index, None)
