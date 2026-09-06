"""Public contract for advisory VLM assessment of one completed replay."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .base import HarnessModel, public_schema_config
from .common import ArtifactRef

REPLAY_VLM_ASSESSMENT_INPUT_SCHEMA_ID = "harness.replay_vlm_assessment_input.v1"
REPLAY_VLM_ASSESSMENT_OUTPUT_SCHEMA_ID = "harness.replay_vlm_assessment_output.v1"
REPLAY_VLM_ASSESSMENT_SCHEMA_VERSION = "harness.replay_vlm_assessment.v1"
ReplayRunId = Annotated[
    str,
    Field(
        strict=True,
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        ),
    ),
]


class ReplayVlmAssessmentInput(HarnessModel):
    """Identify the already-persisted replay to inspect."""

    model_config = public_schema_config(REPLAY_VLM_ASSESSMENT_INPUT_SCHEMA_ID)

    replay_run_id: ReplayRunId


class ReplayVlmAssessmentOutput(HarnessModel):
    """Point to the immutable advisory receipt produced by the VLM."""

    model_config = public_schema_config(REPLAY_VLM_ASSESSMENT_OUTPUT_SCHEMA_ID)

    replay_run_id: ReplayRunId
    assessment: ArtifactRef
    advisory_status: Literal["pass", "fail", "abstain", "format_invalid"]
    claims_physical_pass: Literal[False] = False

    @model_validator(mode="after")
    def assessment_is_typed(self) -> "ReplayVlmAssessmentOutput":
        if self.assessment.schema_version != REPLAY_VLM_ASSESSMENT_SCHEMA_VERSION:
            raise ValueError(
                f"assessment.schema_version must be {REPLAY_VLM_ASSESSMENT_SCHEMA_VERSION!r}"
            )
        return self
