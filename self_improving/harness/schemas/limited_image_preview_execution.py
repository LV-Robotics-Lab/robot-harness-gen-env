"""One semantic gate over existing native scene execution evidence."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .base import HarnessModel, public_schema_config
from .common import ArtifactRef
from .genesis_scene_execution import _reference

Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class PreviewLayout(HarnessModel):
    x_m: Finite
    y_m: Finite
    yaw_rad: Finite


class LimitedSceneValidateInputV1(HarnessModel):
    model_config = public_schema_config("harness.limited_scene_validate_input.v1")
    schema_version: Literal["harness.limited_scene_validate_input.v1"]
    execution_receipt: ArtifactRef
    expected_layout: PreviewLayout | None

    @model_validator(mode="after")
    def exact_execution(self):
        if self.execution_receipt.schema_version not in {
            "harness.genesis_scene_execution_receipt.v2",
            "harness.genesis_scene_execution_receipt.v3",
        }:
            raise ValueError("semantic validation requires native replay evidence")
        _reference(self.execution_receipt, self.execution_receipt.schema_version)
        return self


class LimitedSceneValidationV1(HarnessModel):
    model_config = public_schema_config("harness.limited_scene_validation.v1")
    schema_version: Literal["harness.limited_scene_validation.v1"]
    execution_receipt: ArtifactRef
    scene: ArtifactRef
    physical_report: ArtifactRef
    expected_layout: PreviewLayout | None
    actual_layout: PreviewLayout
    position_tolerance_m: Literal[0.001]
    yaw_tolerance_rad: Literal[0.001]
    semantic_status: Literal["pass", "mismatch"]
    status: Literal["passed", "failed"]
    physical_scope: Literal["dual_dt_trace_contact_settling_and_media"]

    @model_validator(mode="after")
    def exact_result(self):
        LimitedSceneValidateInputV1(
            schema_version="harness.limited_scene_validate_input.v1",
            execution_receipt=self.execution_receipt,
            expected_layout=self.expected_layout,
        )
        _reference(self.scene, "harness.genesis_scene_spec.v1")
        _reference(self.physical_report, "harness.genesis_scene_report.v1")
        if (self.semantic_status == "pass") != (self.status == "passed"):
            raise ValueError("semantic mismatch cannot pass")
        return self
