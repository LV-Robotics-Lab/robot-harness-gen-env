"""Common audit and artifact records for Harness MVP v1."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import UUID4, AwareDatetime, Field, StrictBool, model_validator

from .base import (
    BlockerCode,
    CanonicalSkillRef,
    HarnessModel,
    JsonObject,
    McpToolName,
    NonEmptyString,
    NonNegativeInt,
    PositiveInt,
    SchemaId,
    Sha256,
    ShortString,
    SkillId,
    StableSemVer,
    derive_mcp_tool_name,
    public_schema_config,
)

SKILL_DESCRIPTOR_SCHEMA_ID = "harness.skill_descriptor.v1"
SKILL_INVOCATION_SCHEMA_ID = "harness.skill_invocation.v1"
RUN_STATE_SCHEMA_ID = "harness.run_state.v1"
EVENT_SCHEMA_ID = "harness.event.v1"
ARTIFACT_REF_SCHEMA_ID = "harness.artifact_ref.v1"
BLOCKER_SCHEMA_ID = "harness.blocker.v1"
SKILL_QUALIFICATION_SCHEMA_ID = "harness.skill_qualification.v1"


class RunStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    FAILED = "failed"


class ValidationStatus(str, Enum):
    PASS = "pass"
    INCOMPLETE = "incomplete"
    FAIL = "fail"


class UnknownField(HarnessModel):
    field: NonEmptyString
    reason: NonEmptyString
    source: NonEmptyString


class DependencyRef(HarnessModel):
    name: ShortString
    version: ShortString
    sha256: Sha256


class ArtifactRef(HarnessModel):
    model_config = public_schema_config(ARTIFACT_REF_SCHEMA_ID)

    name: ShortString
    uri: NonEmptyString
    media_type: ShortString
    sha256: Sha256
    bytes: NonNegativeInt
    schema_version: ShortString | None


class Blocker(HarnessModel):
    model_config = public_schema_config(BLOCKER_SCHEMA_ID)

    code: BlockerCode
    message: NonEmptyString
    stage: ShortString
    retryable: StrictBool
    details: JsonObject
    unknowns: tuple[UnknownField, ...]
    artifact_refs: tuple[ArtifactRef, ...]


class Event(HarnessModel):
    model_config = public_schema_config(EVENT_SCHEMA_ID)

    seq: PositiveInt
    timestamp: AwareDatetime
    stage: ShortString
    attempt: NonNegativeInt
    from_status: RunStatus | None
    to_status: RunStatus
    artifact_refs: tuple[ArtifactRef, ...]


class SkillQualification(HarnessModel):
    model_config = public_schema_config(SKILL_QUALIFICATION_SCHEMA_ID)

    skill_ref: CanonicalSkillRef
    status: Literal["pass"]
    deterministic_case_id: ShortString
    regression_command: NonEmptyString
    report_sha256: Sha256


class SkillDescriptor(HarnessModel):
    model_config = public_schema_config(SKILL_DESCRIPTOR_SCHEMA_ID)

    skill_id: SkillId
    version: StableSemVer
    mcp_tool_name: McpToolName
    input_schema: SchemaId
    output_schema: SchemaId
    implementation_name: ShortString
    implementation_version: ShortString
    implementation_sha256: Sha256
    deterministic: Literal[True]
    max_attempts: PositiveInt
    qualification_artifact: ArtifactRef

    @model_validator(mode="after")
    def descriptor_is_self_consistent(self) -> "SkillDescriptor":
        expected_name = derive_mcp_tool_name(self.skill_id, self.version)
        if self.mcp_tool_name != expected_name:
            raise ValueError(f"mcp_tool_name must be {expected_name!r}")
        if self.qualification_artifact.schema_version != SKILL_QUALIFICATION_SCHEMA_ID:
            raise ValueError(
                "qualification_artifact.schema_version must be "
                f"{SKILL_QUALIFICATION_SCHEMA_ID!r}"
            )
        return self


class Invocation(HarnessModel):
    model_config = public_schema_config(SKILL_INVOCATION_SCHEMA_ID)

    run_id: UUID4
    skill_id: SkillId
    skill_version: StableSemVer
    effective_parameters: JsonObject
    dependencies: tuple[DependencyRef, ...]
    max_attempts: PositiveInt
    invocation_digest: Sha256

    @model_validator(mode="after")
    def dependencies_are_sorted_and_unique(self) -> "Invocation":
        names = [dependency.name for dependency in self.dependencies]
        if names != sorted(names):
            raise ValueError("dependencies must be sorted by name")
        if len(names) != len(set(names)):
            raise ValueError("dependency names must be unique")
        return self


class RunState(HarnessModel):
    model_config = public_schema_config(RUN_STATE_SCHEMA_ID)

    run_id: UUID4
    invocation_digest: Sha256 | None
    skill_id: NonEmptyString
    skill_version: NonEmptyString
    status: RunStatus
    attempt: NonNegativeInt
    max_attempts: NonNegativeInt
    started_at: AwareDatetime
    ended_at: AwareDatetime | None
    events: tuple[Event, ...] = Field(min_length=1)
    artifacts: tuple[ArtifactRef, ...]
    output: JsonObject | None
    blocker: Blocker | None

    @model_validator(mode="after")
    def run_state_is_consistent(self) -> "RunState":
        expected_sequences = list(range(1, len(self.events) + 1))
        if [event.seq for event in self.events] != expected_sequences:
            raise ValueError("event seq values must be consecutive and start at 1")

        first = self.events[0]
        if first.from_status is not None or first.to_status != RunStatus.RUNNING:
            raise ValueError("the first event must transition null -> running")
        if first.timestamp != self.started_at:
            raise ValueError("started_at must equal the first event timestamp")

        previous = first
        for event in self.events[1:]:
            if event.from_status != previous.to_status:
                raise ValueError("event status transitions must form one continuous chain")
            if previous.to_status != RunStatus.RUNNING:
                raise ValueError("events cannot follow a terminal transition")
            if event.timestamp < previous.timestamp:
                raise ValueError("event timestamps must be nondecreasing")
            if event.attempt < previous.attempt or event.attempt > previous.attempt + 1:
                raise ValueError("event attempts must be nondecreasing and increment by at most 1")
            previous = event

        if previous.to_status != self.status:
            raise ValueError("the final event status must match RunState.status")
        if previous.attempt != self.attempt:
            raise ValueError("the final event attempt must match RunState.attempt")
        if self.max_attempts == 0 and self.attempt != 0:
            raise ValueError("max_attempts=0 is only valid for preflight attempt 0")
        if self.max_attempts > 0 and self.attempt > self.max_attempts:
            raise ValueError("attempt cannot exceed max_attempts")
        if self.attempt == 0 and self.invocation_digest is not None:
            raise ValueError("preflight attempt 0 cannot have an invocation_digest")
        if self.attempt > 0 and self.invocation_digest is None:
            raise ValueError("execution attempts require an invocation_digest")

        if self.status == RunStatus.RUNNING:
            if self.ended_at is not None or self.blocker is not None or self.output is not None:
                raise ValueError("running state requires ended_at, blocker, and output to be null")
            return self

        if self.ended_at is None:
            raise ValueError("terminal state requires ended_at")
        if self.ended_at < self.started_at:
            raise ValueError("ended_at cannot precede started_at")
        if self.ended_at != previous.timestamp:
            raise ValueError("ended_at must equal the final event timestamp")

        if self.status == RunStatus.SUCCEEDED:
            if self.blocker is not None or self.output is None:
                raise ValueError("succeeded state requires output and a null blocker")
        elif self.blocker is None or self.output is not None:
            raise ValueError("blocked and failed states require a blocker and null output")
        return self
