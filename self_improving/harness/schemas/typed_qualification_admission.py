"""Portable admission record for native typed Skill qualification evidence."""

from typing import Literal

from pydantic import UUID4, Field

from .base import (
    CanonicalSkillRef,
    HarnessModel,
    McpToolName,
    Sha256,
    ShortString,
    public_schema_config,
)
from .common import ArtifactRef, Invocation, RunState

TYPED_QUALIFICATION_ADMISSION_SCHEMA_ID = "harness.typed_qualification_admission.v1"


class QualificationImplementationMemberV1(HarnessModel):
    """One portable implementation member whose bytes live in CAS."""

    logical_path: ShortString
    artifact: ArtifactRef


class QualificationCandidateIntentV1(HarnessModel):
    """A qualification candidate's immutable invocation, terminal, and typed output."""

    run_id: UUID4
    invocation_digest: Sha256
    invocation: Invocation
    run_state: RunState
    output: ArtifactRef


class TypedQualificationAdmissionV1(HarnessModel):
    """Frozen native qualification evidence accepted without generic translation."""

    model_config = public_schema_config(TYPED_QUALIFICATION_ADMISSION_SCHEMA_ID)

    schema_version: Literal[TYPED_QUALIFICATION_ADMISSION_SCHEMA_ID]
    skill_ref: CanonicalSkillRef
    mcp_tool_name: McpToolName
    descriptor: ArtifactRef
    qualification: ArtifactRef
    qualification_report: ArtifactRef
    implementation_sha256: Sha256
    implementation_members: tuple[QualificationImplementationMemberV1, ...] = Field(min_length=1)
    candidate_intents: tuple[QualificationCandidateIntentV1, ...] = Field(min_length=1)
    admission_sha256: Sha256
