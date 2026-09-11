"""New host lineage for revised compiles over unchanged fixed scene observations."""

from typing import Literal

from pydantic import UUID4, model_validator

from .base import HarnessModel, public_schema_config
from .common import ArtifactRef
from .genesis_scene_execution import _reference
from .genesis_scene_execution_v2 import (
    GenesisSceneExecutionReceiptV2,
    GenesisSceneExecutionRequestV2,
    GenesisSceneLaunchResultV2,
    GenesisSceneProcessObservationV2,
    _media,
)

SCENE_COMPILE_BINDING_V2_ID = "harness.genesis_scene_compile_binding.v2"
SCENE_EXECUTION_REQUEST_V3_ID = "harness.genesis_scene_execution_request.v3"
SCENE_PROCESS_OBSERVATION_V3_ID = "harness.genesis_scene_process_observation.v3"
SCENE_LAUNCH_RESULT_V3_ID = "harness.genesis_scene_launch_result.v3"
SCENE_EXECUTION_RECEIPT_V3_ID = "harness.genesis_scene_execution_receipt.v3"


class GenesisSceneCompileBindingV2(HarnessModel):
    """Declared binding; only the future exact application can admit its authority."""

    model_config = public_schema_config(SCENE_COMPILE_BINDING_V2_ID)
    schema_version: Literal[SCENE_COMPILE_BINDING_V2_ID]
    compile_skill_ref: Literal["image2env.compile@3.0.0"]
    compile_run_id: UUID4
    compile_request: ArtifactRef
    compile_receipt: ArtifactRef
    execution_request: ArtifactRef

    @model_validator(mode="after")
    def exact_lineage(self):
        for ref, schema in (
            (self.compile_request, "harness.image2env_genesis_compile_request.v2"),
            (self.compile_receipt, "harness.image2env_genesis_compile_receipt.v2"),
            (self.execution_request, SCENE_EXECUTION_REQUEST_V3_ID),
        ):
            _reference(ref, schema)
        return self


class GenesisSceneExecutionRequestV3(GenesisSceneExecutionRequestV2):
    model_config = public_schema_config(SCENE_EXECUTION_REQUEST_V3_ID)
    schema_version: Literal[SCENE_EXECUTION_REQUEST_V3_ID]

    @model_validator(mode="after")
    def exact_inputs(self):
        for ref, schema in (
            (self.scene, "harness.genesis_scene_spec.v1"),
            (self.compile_receipt, "harness.image2env_genesis_compile_receipt.v2"),
            (self.runtime_manifest, "harness.genesis_runtime_manifest.v1"),
            (self.asset_qualification, "harness.asset_qualification.v1"),
        ):
            _reference(ref, schema)
        return self


class GenesisSceneProcessObservationV3(GenesisSceneProcessObservationV2):
    model_config = public_schema_config(SCENE_PROCESS_OBSERVATION_V3_ID)
    schema_version: Literal[SCENE_PROCESS_OBSERVATION_V3_ID]

    @model_validator(mode="after")
    def actual_denials(self):
        _reference(self.request, SCENE_EXECUTION_REQUEST_V3_ID)
        _reference(self.raw_observation, None)
        if not self.network_denied or not self.runtime_write_denied:
            raise ValueError("both actual confinement denial probes must pass")
        return self


class GenesisSceneLaunchResultV3(GenesisSceneLaunchResultV2):
    model_config = public_schema_config(SCENE_LAUNCH_RESULT_V3_ID)
    schema_version: Literal[SCENE_LAUNCH_RESULT_V3_ID]

    @model_validator(mode="after")
    def exact_outputs(self):
        _reference(self.request, SCENE_EXECUTION_REQUEST_V3_ID)
        _reference(self.replay, "harness.genesis_scene_replay.v1")
        for ref in (self.baseline_observation, self.half_dt_observation):
            _reference(ref, SCENE_PROCESS_OBSERVATION_V3_ID)
        _media(self.baseline_preview, self.half_dt_preview, self.retained_index)
        return self


class GenesisSceneExecutionReceiptV3(GenesisSceneExecutionReceiptV2):
    model_config = public_schema_config(SCENE_EXECUTION_RECEIPT_V3_ID)
    schema_version: Literal[SCENE_EXECUTION_RECEIPT_V3_ID]

    @model_validator(mode="after")
    def exact_closure(self):
        for ref, schema in (
            (self.request, SCENE_EXECUTION_REQUEST_V3_ID),
            (self.launch_result, SCENE_LAUNCH_RESULT_V3_ID),
            (self.replay, "harness.genesis_scene_replay.v1"),
            (self.report, "harness.genesis_scene_report.v1"),
        ):
            _reference(ref, schema)
        _media(self.baseline_preview, self.half_dt_preview, self.retained_index)
        return self
