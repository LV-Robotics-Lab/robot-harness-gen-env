"""Actor-neutral operation evidence contracts for one Golden Workflow."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import UUID4, AwareDatetime, Field, JsonValue, model_validator

from ..trusted_state import StateMutation, WorldFact
from .base import (
    CanonicalSkillRef,
    HarnessModel,
    JsonObject,
    PositiveInt,
    SchemaId,
    Sha256,
    public_schema_config,
)
from .common import ArtifactRef, Blocker, RunStatus
from .workflow import PrincipalId

QUALIFIED_SKILL_EXECUTION_V2_SCHEMA_ID = "harness.qualified_skill_execution.v2"
STATE_DELTA_V2_SCHEMA_ID = "harness.state_delta.v2"
TOOL_RESULT_V2_SCHEMA_ID = "harness.tool_result.v2"
TRUSTED_TOOL_RECEIPT_V2_SCHEMA_ID = "harness.trusted_tool_receipt.v2"
WORKFLOW_OPERATION_RECEIPT_V2_SCHEMA_ID = "harness.workflow_operation_receipt.v2"

_DESCRIPTOR_SCHEMAS = frozenset({"harness.skill_descriptor.v1", "harness.skill_descriptor.v2"})
_WORKFLOW_RECEIPT_SCHEMAS = frozenset(
    {
        "harness.external_agent_advisory_receipt.v1",
        "harness.prompt_revision.v1",
        "harness.external_agent_advisory_receipt.v2",
        "harness.prompt_revision.v2",
        "harness.workflow_start_receipt.v1",
        WORKFLOW_OPERATION_RECEIPT_V2_SCHEMA_ID,
    }
)


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _require_utc(value: datetime, *, label: str) -> None:
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{label} must use UTC")


def _require_cas(
    artifact: ArtifactRef,
    *,
    label: str,
    schema_version: str | None = None,
    schema_versions: frozenset[str] | None = None,
) -> None:
    if artifact.uri != f"artifact://sha256/{artifact.sha256}":
        raise ValueError(f"{label} must use its content-addressed artifact URI")
    if schema_version is not None and artifact.schema_version != schema_version:
        raise ValueError(f"{label} must have schema_version={schema_version}")
    if schema_versions is not None and artifact.schema_version not in schema_versions:
        raise ValueError(f"{label} has an unsupported schema_version")
    if (schema_version is not None or schema_versions is not None) and (
        artifact.media_type != "application/json"
    ):
        raise ValueError(f"{label} must have media_type=application/json")


class ReceiptDerivedFactClaimV2(HarnessModel):
    """One state claim derived from a qualified Skill output."""

    operation: Literal["upsert", "retract"] = "upsert"
    key: Annotated[
        str,
        Field(
            strict=True,
            pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$",
            max_length=255,
        ),
    ]
    value: JsonValue

    @model_validator(mode="after")
    def claim_shape_is_unambiguous(self) -> "ReceiptDerivedFactClaimV2":
        if self.operation == "retract" and self.value is not None:
            raise ValueError("retract claim value must be null")
        return self


class QualifiedSkillExecutionV2(HarnessModel):
    """The live qualified Skill identity intersected with the frozen registry."""

    model_config = public_schema_config(QUALIFIED_SKILL_EXECUTION_V2_SCHEMA_ID)

    schema_version: Literal["harness.qualified_skill_execution.v2"]
    skill_ref: CanonicalSkillRef
    descriptor: ArtifactRef
    input_schema: SchemaId
    output_schema: SchemaId
    implementation_sha256: Sha256
    qualification_artifact: ArtifactRef
    qualification_report: ArtifactRef
    max_attempts: PositiveInt
    evidence_policy_id: Annotated[
        str,
        Field(
            strict=True,
            pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$",
            max_length=255,
        ),
    ]
    evidence_policy_version: Annotated[
        str,
        Field(
            strict=True,
            pattern=r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$",
        ),
    ]
    evidence_policy_implementation_sha256: Sha256

    @model_validator(mode="after")
    def authority_refs_are_cas_bound(self) -> "QualifiedSkillExecutionV2":
        _require_cas(
            self.descriptor,
            label="descriptor",
            schema_versions=_DESCRIPTOR_SCHEMAS,
        )
        _require_cas(
            self.qualification_artifact,
            label="qualification_artifact",
            schema_version="harness.skill_qualification.v1",
        )
        _require_cas(
            self.qualification_report,
            label="qualification_report",
            schema_version="harness.skill_qualification_report.v1",
        )
        return self


class TrustedToolReceiptV2(HarnessModel):
    """Terminal qualified execution evidence, independent of workflow commit."""

    model_config = public_schema_config(TRUSTED_TOOL_RECEIPT_V2_SCHEMA_ID)

    schema_version: Literal["harness.trusted_tool_receipt.v2"]
    workflow_run_id: UUID4
    operation_id: UUID4
    principal_id: PrincipalId
    command: ArtifactRef
    base_revision: Annotated[int, Field(strict=True, ge=0)]
    base_state_sha256: Sha256
    base_state: ArtifactRef
    registry_snapshot: ArtifactRef
    skill: QualifiedSkillExecutionV2
    status: RunStatus
    started_at: AwareDatetime
    ended_at: AwareDatetime
    child_run_id: UUID4
    invocation: ArtifactRef | None
    run_state: ArtifactRef
    invocation_digest: Sha256 | None
    typed_output: JsonObject | None
    blocker: Blocker | None
    supporting_artifacts: tuple[ArtifactRef, ...]
    derived_fact_claims: tuple[ReceiptDerivedFactClaimV2, ...]

    @model_validator(mode="after")
    def receipt_is_terminal_and_actor_neutral(self) -> "TrustedToolReceiptV2":
        _require_utc(self.started_at, label="started_at")
        _require_utc(self.ended_at, label="ended_at")
        if self.ended_at < self.started_at:
            raise ValueError("ended_at cannot precede started_at")
        _require_cas(
            self.command,
            label="command",
            schema_version="harness.run_command_request.v1",
        )
        _require_cas(
            self.base_state,
            label="base_state",
            schema_version="harness.trusted_world_state.v1",
        )
        _require_cas(
            self.registry_snapshot,
            label="registry_snapshot",
            schema_version="harness.registry_snapshot.v1",
        )
        _require_cas(
            self.run_state,
            label="run_state",
            schema_version="harness.run_state.v1",
        )
        if self.invocation is not None:
            _require_cas(
                self.invocation,
                label="invocation",
                schema_version="harness.skill_invocation.v1",
            )
        identities = []
        for artifact in self.supporting_artifacts:
            _require_cas(artifact, label="supporting_artifact")
            identities.append((artifact.sha256, artifact.schema_version or "", artifact.media_type))
        if identities != sorted(identities) or len(identities) != len(set(identities)):
            raise ValueError("supporting_artifacts must be canonically sorted and unique")
        claim_keys = [claim.key for claim in self.derived_fact_claims]
        if claim_keys != sorted(claim_keys) or len(claim_keys) != len(set(claim_keys)):
            raise ValueError("derived_fact_claims must be sorted and unique")
        if self.status is RunStatus.RUNNING:
            raise ValueError("trusted tool receipt must be terminal")
        if self.status is RunStatus.SUCCEEDED:
            if (
                self.invocation is None
                or self.invocation_digest is None
                or self.typed_output is None
                or self.blocker is not None
            ):
                raise ValueError("successful trusted tool receipt is incomplete")
        elif self.typed_output is not None or self.blocker is None or self.derived_fact_claims:
            raise ValueError("blocked or failed trusted tool receipt is contradictory")
        if (self.invocation is None) != (self.invocation_digest is None):
            raise ValueError("invocation and invocation_digest must be present together")
        return self


def state_delta_v2_sha256(delta: "StateDeltaV2") -> str:
    payload = {
        "domain": "harness.state_delta.identity.v2",
        "delta": delta.model_dump(mode="json", exclude={"delta_sha256"}),
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


class StateDeltaV2(HarnessModel):
    """An actor-neutral receipt-bound compare-and-swap state change."""

    model_config = public_schema_config(STATE_DELTA_V2_SCHEMA_ID)

    schema_version: Literal["harness.state_delta.v2"]
    base_state_sha256: Sha256
    effective_at: AwareDatetime
    receipt: ArtifactRef
    mutations: tuple[StateMutation, ...]
    delta_sha256: Sha256

    @model_validator(mode="after")
    def delta_is_canonical(self) -> "StateDeltaV2":
        _require_utc(self.effective_at, label="effective_at")
        _require_cas(
            self.receipt,
            label="receipt",
            schema_version=TRUSTED_TOOL_RECEIPT_V2_SCHEMA_ID,
        )
        keys = [mutation.key for mutation in self.mutations]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("state mutations must be sorted and unique")
        if self.delta_sha256 != state_delta_v2_sha256(self):
            raise ValueError("delta_sha256 does not match canonical state delta")
        return self


class ToolResultV2(HarnessModel):
    """Actor-neutral result envelope returned to an external Codex caller."""

    model_config = public_schema_config(TOOL_RESULT_V2_SCHEMA_ID)

    schema_version: Literal["harness.tool_result.v2"]
    workflow_run_id: UUID4
    operation_id: UUID4
    skill_ref: CanonicalSkillRef
    status: RunStatus
    started_at: AwareDatetime
    ended_at: AwareDatetime
    child_run_id: UUID4
    invocation: ArtifactRef | None
    run_state: ArtifactRef
    typed_output: JsonObject | None
    state_delta: ArtifactRef | None
    diagnostics: tuple[ArtifactRef, ...]
    trusted_receipt: ArtifactRef
    fresh_observations: tuple[WorldFact, ...]
    blocker: Blocker | None

    @model_validator(mode="after")
    def result_is_terminal_and_consistent(self) -> "ToolResultV2":
        _require_utc(self.started_at, label="started_at")
        _require_utc(self.ended_at, label="ended_at")
        if self.ended_at < self.started_at or self.status is RunStatus.RUNNING:
            raise ValueError("ToolResult requires a valid terminal time window")
        _require_cas(
            self.run_state,
            label="run_state",
            schema_version="harness.run_state.v1",
        )
        _require_cas(
            self.trusted_receipt,
            label="trusted_receipt",
            schema_version=TRUSTED_TOOL_RECEIPT_V2_SCHEMA_ID,
        )
        if self.invocation is not None:
            _require_cas(
                self.invocation,
                label="invocation",
                schema_version="harness.skill_invocation.v1",
            )
        if self.status is RunStatus.SUCCEEDED:
            if (
                self.invocation is None
                or self.typed_output is None
                or self.state_delta is None
                or self.blocker is not None
            ):
                raise ValueError("successful ToolResult is incomplete")
            _require_cas(
                self.state_delta,
                label="state_delta",
                schema_version=STATE_DELTA_V2_SCHEMA_ID,
            )
        elif self.typed_output is not None or self.state_delta is not None or self.blocker is None:
            raise ValueError("blocked or failed ToolResult is contradictory")
        diagnostic_identities = []
        for artifact in self.diagnostics:
            _require_cas(artifact, label="diagnostic")
            diagnostic_identities.append(
                (artifact.sha256, artifact.schema_version or "", artifact.media_type)
            )
        if diagnostic_identities != sorted(diagnostic_identities) or len(
            diagnostic_identities
        ) != len(set(diagnostic_identities)):
            raise ValueError("diagnostics must be canonically sorted and unique")
        keys = [observation.key for observation in self.fresh_observations]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("fresh observations must be sorted and unique")
        for observation in self.fresh_observations:
            if observation.source_kind != "fresh_observation":
                raise ValueError("fresh observation must declare fresh_observation provenance")
            if not self.started_at <= observation.observed_at <= self.ended_at:
                raise ValueError("fresh observation was not captured during this execution")
            if observation.valid_until is not None and observation.valid_until < self.ended_at:
                raise ValueError("fresh observation expired before execution completion")
        return self


class WorkflowOperationReceiptV2(HarnessModel):
    """Commit receipt binding a terminal ToolResult to the resulting workflow head."""

    model_config = public_schema_config(WORKFLOW_OPERATION_RECEIPT_V2_SCHEMA_ID)

    schema_version: Literal["harness.workflow_operation_receipt.v2"]
    workflow_run_id: UUID4
    operation_id: UUID4
    principal_id: PrincipalId
    command: ArtifactRef
    predecessor_receipt: ArtifactRef
    tool_receipt: ArtifactRef
    tool_result: ArtifactRef
    state_delta: ArtifactRef | None
    base_revision: Annotated[int, Field(strict=True, ge=0)]
    committed_revision: PositiveInt
    base_state_sha256: Sha256
    resulting_state_sha256: Sha256
    resulting_state: ArtifactRef
    status: RunStatus
    committed_at: AwareDatetime

    @model_validator(mode="after")
    def commit_is_closed_and_terminal(self) -> "WorkflowOperationReceiptV2":
        _require_utc(self.committed_at, label="committed_at")
        if self.committed_revision != self.base_revision + 1:
            raise ValueError("committed_revision must advance exactly once")
        if self.status is RunStatus.RUNNING:
            raise ValueError("workflow operation receipt must be terminal")
        _require_cas(
            self.command,
            label="command",
            schema_version="harness.run_command_request.v1",
        )
        _require_cas(
            self.predecessor_receipt,
            label="predecessor_receipt",
            schema_versions=_WORKFLOW_RECEIPT_SCHEMAS,
        )
        _require_cas(
            self.tool_receipt,
            label="tool_receipt",
            schema_version=TRUSTED_TOOL_RECEIPT_V2_SCHEMA_ID,
        )
        _require_cas(
            self.tool_result,
            label="tool_result",
            schema_version=TOOL_RESULT_V2_SCHEMA_ID,
        )
        _require_cas(
            self.resulting_state,
            label="resulting_state",
            schema_version="harness.trusted_world_state.v1",
        )
        if self.status is RunStatus.SUCCEEDED:
            if self.state_delta is None:
                raise ValueError("successful workflow operation requires a state delta")
            _require_cas(
                self.state_delta,
                label="state_delta",
                schema_version=STATE_DELTA_V2_SCHEMA_ID,
            )
        elif self.state_delta is not None:
            raise ValueError("blocked or failed workflow operation cannot bind a state delta")
        return self


__all__ = [
    "QUALIFIED_SKILL_EXECUTION_V2_SCHEMA_ID",
    "STATE_DELTA_V2_SCHEMA_ID",
    "TOOL_RESULT_V2_SCHEMA_ID",
    "TRUSTED_TOOL_RECEIPT_V2_SCHEMA_ID",
    "WORKFLOW_OPERATION_RECEIPT_V2_SCHEMA_ID",
    "QualifiedSkillExecutionV2",
    "ReceiptDerivedFactClaimV2",
    "StateDeltaV2",
    "ToolResultV2",
    "TrustedToolReceiptV2",
    "WorkflowOperationReceiptV2",
    "state_delta_v2_sha256",
]
