"""Final replay input binds a registered compile invocation, not only its content."""

from typing import Literal

from pydantic import UUID4, Field, model_validator

from .base import HarnessModel, Sha256, ShortString, public_schema_config
from .common import ArtifactRef

SCENE_REPLAY_INPUT_V2_ID = "harness.genesis_scene_replay_input.v2"
SCENE_SKILL_REPORT_V2_ID = "harness.genesis_scene_skill_qualification_report.v2"


class GenesisSceneReplayInputV2(HarnessModel):
    model_config = public_schema_config(SCENE_REPLAY_INPUT_V2_ID)
    schema_version: Literal[SCENE_REPLAY_INPUT_V2_ID]
    compile_run_id: UUID4
    execution_request: ArtifactRef

    @model_validator(mode="after")
    def exact_request(self):
        ref = self.execution_request
        if (
            ref.schema_version != "harness.genesis_scene_execution_request.v2"
            or ref.media_type != "application/json"
            or ref.uri != f"artifact://sha256/{ref.sha256}"
        ):
            raise ValueError("replay input requires the exact lower-level execution request")
        return self


class GenesisSceneSkillCaseV2(HarnessModel):
    candidate_run_id: UUID4
    compile_run_id: UUID4
    invocation_digest: Sha256
    execution_request: ArtifactRef
    execution_receipt: ArtifactRef


class GenesisSceneSkillQualificationReportV2(HarnessModel):
    model_config = public_schema_config(SCENE_SKILL_REPORT_V2_ID)
    schema_version: Literal[SCENE_SKILL_REPORT_V2_ID]
    skill_ref: Literal["image2env.replay@2.0.0"]
    cases: tuple[GenesisSceneSkillCaseV2, GenesisSceneSkillCaseV2]
    approved_manifest: ArtifactRef
    compile_qualification: ArtifactRef
    implementation: tuple[ArtifactRef, ...] = Field(min_length=1)
    implementation_sha256: Sha256
    numpy_version: ShortString
    python_abi: ShortString
    invariant: Literal["registered_compile_dual_profile_34_checks_and_41_exact_decoded_frames_each"]
