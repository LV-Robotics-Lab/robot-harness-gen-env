"""Scene execution provenance; distinct from mathematical consistency and Skill qualification."""

from typing import Literal

from pydantic import Field, model_validator

from .base import HarnessModel, public_schema_config
from .common import ArtifactRef
from .genesis_runtime_manifest import RuntimeManifestMemberV1, RuntimeProcessIdentityV1

SCENE_EXECUTION_REQUEST_ID = "harness.genesis_scene_execution_request.v1"
SCENE_PROCESS_OBSERVATION_ID = "harness.genesis_scene_process_observation.v1"
SCENE_LAUNCH_RESULT_ID = "harness.genesis_scene_launch_result.v1"
SCENE_EXECUTION_RECEIPT_ID = "harness.genesis_scene_execution_receipt.v1"
SCENE_FILESYSTEM_POLICY = (
    "runtime_roots_readonly_kernel_devices_run_output_write_inherited_children"
)


def _reference(ref, schema):
    if (
        ref.uri != f"artifact://sha256/{ref.sha256}"
        or ref.schema_version != schema
        or ref.media_type != "application/json"
    ):
        raise ValueError("reference must be canonical CAS with the exact JSON schema")


class GenesisSceneExecutionRequestV1(HarnessModel):
    model_config = public_schema_config(SCENE_EXECUTION_REQUEST_ID)
    schema_version: Literal[SCENE_EXECUTION_REQUEST_ID]
    scene: ArtifactRef
    compile_receipt: ArtifactRef
    runtime_manifest: ArtifactRef
    asset_qualification: ArtifactRef

    @model_validator(mode="after")
    def exact_inputs(self):
        for value, schema in (
            (self.scene, "harness.genesis_scene_spec.v1"),
            (self.compile_receipt, "harness.image2env_genesis_compile_receipt.v1"),
            (self.runtime_manifest, "harness.genesis_runtime_manifest.v1"),
            (self.asset_qualification, "harness.asset_qualification.v1"),
        ):
            _reference(value, schema)
        return self


class GenesisSceneProcessObservationV1(HarnessModel):
    model_config = public_schema_config(SCENE_PROCESS_OBSERVATION_ID)
    schema_version: Literal[SCENE_PROCESS_OBSERVATION_ID]
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

    @model_validator(mode="after")
    def actual_denials(self):
        _reference(self.request, SCENE_EXECUTION_REQUEST_ID)
        _reference(self.raw_observation, None)
        if not self.network_denied or not self.runtime_write_denied:
            raise ValueError("both actual confinement denial probes must pass")
        return self


class GenesisSceneLaunchResultV1(HarnessModel):
    model_config = public_schema_config(SCENE_LAUNCH_RESULT_ID)
    schema_version: Literal[SCENE_LAUNCH_RESULT_ID]
    request: ArtifactRef
    replay: ArtifactRef
    baseline_observation: ArtifactRef
    half_dt_observation: ArtifactRef

    @model_validator(mode="after")
    def exact_outputs(self):
        _reference(self.request, SCENE_EXECUTION_REQUEST_ID)
        _reference(self.replay, "harness.genesis_scene_replay.v1")
        for ref in (self.baseline_observation, self.half_dt_observation):
            _reference(ref, SCENE_PROCESS_OBSERVATION_ID)
        return self


class GenesisSceneExecutionReceiptV1(HarnessModel):
    model_config = public_schema_config(SCENE_EXECUTION_RECEIPT_ID)
    schema_version: Literal[SCENE_EXECUTION_RECEIPT_ID]
    authority: Literal["observed_execution_only"]
    request: ArtifactRef
    launch_result: ArtifactRef
    replay: ArtifactRef
    report: ArtifactRef

    @model_validator(mode="after")
    def exact_closure(self):
        for ref, schema in (
            (self.request, SCENE_EXECUTION_REQUEST_ID),
            (self.launch_result, SCENE_LAUNCH_RESULT_ID),
            (self.replay, "harness.genesis_scene_replay.v1"),
            (self.report, "harness.genesis_scene_report.v1"),
        ):
            _reference(ref, schema)
        return self
