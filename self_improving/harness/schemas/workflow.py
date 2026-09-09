"""Actor-neutral public contracts for a Golden Workflow."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal

from pydantic import UUID4, AwareDatetime, Field, StrictStr, model_validator

from .base import HarnessModel, NonNegativeInt, Sha256, public_schema_config
from .common import ArtifactRef

RUN_START_REQUEST_SCHEMA_ID = "harness.run_start_request.v1"
RUN_SNAPSHOT_SCHEMA_ID = "harness.run_snapshot.v1"
WORKFLOW_START_RECEIPT_SCHEMA_ID = "harness.workflow_start_receipt.v1"

_CAPABILITY_PROFILE_PATTERN = (
    r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+@(?:0|[1-9][0-9]*)$"
)
_CAS_URI = re.compile(r"^artifact://sha256/([0-9a-f]{64})$")

CapabilityProfileRef = Annotated[
    str,
    Field(strict=True, pattern=_CAPABILITY_PROFILE_PATTERN, max_length=255),
]
PrincipalId = Annotated[StrictStr, Field(min_length=1, max_length=255)]
IdempotencyKey = Annotated[StrictStr, Field(min_length=1, max_length=255)]


class WorkflowStatus(str, Enum):
    """Lifecycle of one parent Golden Workflow."""

    ACTIVE = "active"
    WAITING_INPUT = "waiting_input"
    PROMOTED = "promoted"
    CANCELLED = "cancelled"
    FAILED = "failed"


class RunStartRequest(HarnessModel):
    """Create a parent workflow from immutable user-input evidence."""

    model_config = public_schema_config(RUN_START_REQUEST_SCHEMA_ID)

    schema_version: Literal["harness.run_start_request.v1"]
    principal_id: PrincipalId
    idempotency_key: IdempotencyKey
    workspace: Literal["ephemeral", "production"]
    requested_profile: CapabilityProfileRef
    initial_fact_evidence: tuple[ArtifactRef, ...] = Field(min_length=1)
    registry_snapshot: ArtifactRef

    @model_validator(mode="after")
    def input_refs_are_canonical(self) -> "RunStartRequest":
        _require_cas_ref(
            self.registry_snapshot,
            label="registry_snapshot",
            schema_version="harness.registry_snapshot.v1",
        )
        identities: list[tuple[str, int, str, str | None]] = []
        for artifact in self.initial_fact_evidence:
            _require_cas_ref(
                artifact,
                label="initial_fact_evidence",
                schema_version="harness.world_fact_evidence.v1",
            )
            identities.append(
                (artifact.sha256, artifact.bytes, artifact.media_type, artifact.schema_version)
            )
        if identities != sorted(identities) or len(identities) != len(set(identities)):
            raise ValueError("initial_fact_evidence must be sorted and unique by content identity")
        return self


class WorkflowStartReceipt(HarnessModel):
    """Immutable head receipt for parent workflow creation."""

    model_config = public_schema_config(WORKFLOW_START_RECEIPT_SCHEMA_ID)

    schema_version: Literal["harness.workflow_start_receipt.v1"]
    workflow_run_id: UUID4
    principal_id: PrincipalId
    workspace: Literal["ephemeral", "production"]
    requested_profile: CapabilityProfileRef
    request_sha256: Sha256
    state_sha256: Sha256
    state_ref: ArtifactRef
    registry_snapshot: ArtifactRef
    started_at: AwareDatetime

    @model_validator(mode="after")
    def receipt_is_bound(self) -> "WorkflowStartReceipt":
        _require_utc(self.started_at, label="started_at")
        _require_cas_ref(
            self.state_ref,
            label="state_ref",
            schema_version="harness.trusted_world_state.v1",
        )
        _require_cas_ref(
            self.registry_snapshot,
            label="registry_snapshot",
            schema_version="harness.registry_snapshot.v1",
        )
        return self


class RunSnapshot(HarnessModel):
    """Current authoritative head of one parent Golden Workflow."""

    model_config = public_schema_config(RUN_SNAPSHOT_SCHEMA_ID)

    schema_version: Literal["harness.run_snapshot.v1"]
    workflow_run_id: UUID4
    principal_id: PrincipalId
    workspace: Literal["ephemeral", "production"]
    requested_profile: CapabilityProfileRef
    revision: NonNegativeInt
    status: WorkflowStatus
    turn_seq: NonNegativeInt
    state_sha256: Sha256
    state_ref: ArtifactRef
    registry_snapshot: ArtifactRef
    receipt_head: ArtifactRef
    active_operation: UUID4 | None
    child_runs: tuple[UUID4, ...]
    started_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def snapshot_is_bound(self) -> "RunSnapshot":
        _require_utc(self.started_at, label="started_at")
        _require_utc(self.updated_at, label="updated_at")
        if self.updated_at < self.started_at:
            raise ValueError("updated_at cannot precede started_at")
        _require_cas_ref(
            self.state_ref,
            label="state_ref",
            schema_version="harness.trusted_world_state.v1",
        )
        _require_cas_ref(
            self.registry_snapshot,
            label="registry_snapshot",
            schema_version="harness.registry_snapshot.v1",
        )
        _require_cas_ref(
            self.receipt_head,
            label="receipt_head",
            schema_version=WORKFLOW_START_RECEIPT_SCHEMA_ID,
        )
        return self


def _require_utc(value: datetime, *, label: str) -> None:
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{label} must use UTC")


def _require_cas_ref(
    artifact: ArtifactRef,
    *,
    label: str,
    schema_version: str,
) -> None:
    match = _CAS_URI.fullmatch(artifact.uri)
    if match is None or match.group(1) != artifact.sha256:
        raise ValueError(f"{label} must use its content-addressed artifact URI")
    if artifact.media_type != "application/json":
        raise ValueError(f"{label} must use media_type='application/json'")
    if artifact.schema_version != schema_version:
        raise ValueError(f"{label} must have schema_version={schema_version}")


__all__ = [
    "RUN_SNAPSHOT_SCHEMA_ID",
    "RUN_START_REQUEST_SCHEMA_ID",
    "WORKFLOW_START_RECEIPT_SCHEMA_ID",
    "CapabilityProfileRef",
    "RunSnapshot",
    "RunStartRequest",
    "WorkflowStartReceipt",
    "WorkflowStatus",
]
