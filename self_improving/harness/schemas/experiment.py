"""Exact experimental operation envelopes, with no release authority."""

from typing import Literal

from pydantic import Field

from .base import HarnessModel, JsonObject, public_schema_config
from .common import ArtifactRef


class ExperimentSkillInput(HarnessModel):
    model_config = public_schema_config("harness.experiment_skill_input.v1")
    schema_version: Literal["harness.experiment_skill_input.v1"] = "harness.experiment_skill_input.v1"
    input_ref: ArtifactRef
    dependencies: dict[str, ArtifactRef] = Field(default_factory=dict)
    proposal: JsonObject = Field(default_factory=dict)


class ExperimentSkillOutput(HarnessModel):
    model_config = public_schema_config("harness.experiment_skill_output.v1")
    schema_version: Literal["harness.experiment_skill_output.v1"] = "harness.experiment_skill_output.v1"
    stage: str
    status: Literal["passed", "failed", "blocked"]
    receipt: ArtifactRef
