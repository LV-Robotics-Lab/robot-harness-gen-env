"""Evidence-invariant scene Skill qualification from trusted candidate terminals."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from .base import HarnessModel, Sha256, public_schema_config
from .common import ArtifactRef

SCENE_SKILL_REPORT_ID = "harness.genesis_scene_skill_qualification_report.v1"


class GenesisSceneSkillQualificationReportV1(HarnessModel):
    model_config = public_schema_config(SCENE_SKILL_REPORT_ID)
    schema_version: Literal[SCENE_SKILL_REPORT_ID]
    skill_ref: Literal["image2env.replay@2.0.0"]
    candidate_runs: tuple[UUID, UUID]
    receipts: tuple[ArtifactRef, ArtifactRef]
    invocation_digests: tuple[Sha256, Sha256]
    approved_manifest: ArtifactRef
    implementation: tuple[ArtifactRef, ...] = Field(min_length=1)
    implementation_sha256: Sha256
    invariant: Literal["same_request_dual_profile_34_checks_and_41_exact_decoded_frames_each"]
