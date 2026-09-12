"""Non-authoritative provenance for one isolated development preview result."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import UUID4, AwareDatetime, BaseModel, Field, model_validator

from .base import HarnessModel, NonEmptyString, NonNegativeInt, Sha256, public_schema_config
from .common import ArtifactRef
from .workflow import IdempotencyKey, PrincipalId

DEVELOPMENT_EXECUTION_RECORD_SCHEMA_ID = "harness.development_execution_record.v1"
DEVELOPMENT_EXECUTION_CONTEXT_SCHEMA_ID = "harness.development_execution_context.v1"

_CHECKS = (
    ("input_output_hash_binding", "executed"),
    ("exact_skill_qualification", "executed"),
    ("workflow_receipt_closure", "executed"),
    ("physical_validation", "executed"),
    ("media_closure", "executed"),
    ("codex_attempt_journal", "executed"),
    ("development_source_capture", "executed"),
    ("clean_release_tree", "omitted"),
    ("formal_preview_library_admission", "omitted"),
    ("formal_preview_publication", "omitted"),
)


def _json_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _json_value(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(child) for child in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (Path, UUID)):
        return str(value)
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def development_execution_record_sha256(value: object) -> str:
    document = value.model_dump(mode="json") if isinstance(value, HarnessModel) else dict(value)
    document.pop("record_id", None)
    return hashlib.sha256(
        _canonical(
            {
                "domain": "harness.development_execution_record.identity.v1",
                "record": document,
            }
        )
        + b"\n"
    ).hexdigest()


def _require_ref(ref: ArtifactRef, *, label: str, schema_version: str | None = None) -> None:
    if ref.uri != f"artifact://sha256/{ref.sha256}":
        raise ValueError(f"{label} must use its exact CAS URI")
    if schema_version is not None and ref.schema_version != schema_version:
        raise ValueError(f"{label} must use schema_version={schema_version}")


def _canonical_refs(refs: tuple[ArtifactRef, ...], *, label: str) -> None:
    identities = tuple(
        (ref.sha256, ref.name, ref.media_type, ref.schema_version or "") for ref in refs
    )
    if identities != tuple(sorted(identities)) or len(identities) != len(set(identities)):
        raise ValueError(f"{label} must be sorted and unique")
    for ref in refs:
        _require_ref(ref, label=label)


class DevelopmentGitSourceIdentityV1(HarnessModel):
    repository_locator: NonEmptyString
    commit: str = Field(strict=True, pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    dirty: bool
    tracked_diff_sha256: Sha256
    untracked_manifest_sha256: Sha256
    tree_state_sha256: Sha256


class DevelopmentEnvironmentVariableV1(HarnessModel):
    name: Literal[
        "CUDA_VISIBLE_DEVICES",
        "GENESIS_BACKEND",
        "MKL_NUM_THREADS",
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "PYTHONHASHSEED",
        "PYTHONPATH",
    ]
    value: str | None


class DevelopmentExecutionEnvironmentV1(HarnessModel):
    python_executable: NonEmptyString
    python_executable_sha256: Sha256
    python_executable_bytes: NonNegativeInt
    python_implementation: NonEmptyString
    python_version: NonEmptyString
    python_abi: NonEmptyString
    platform: NonEmptyString
    variables: tuple[DevelopmentEnvironmentVariableV1, ...]

    @model_validator(mode="after")
    def variables_are_complete_and_ordered(self):
        names = tuple(item.name for item in self.variables)
        expected = tuple(
            sorted(DevelopmentEnvironmentVariableV1.model_fields["name"].annotation.__args__)
        )
        if names != expected:
            raise ValueError(
                "development environment variables must use the complete fixed vocabulary"
            )
        return self


class DevelopmentExecutionContextV1(HarnessModel):
    schema_version: Literal[DEVELOPMENT_EXECUTION_CONTEXT_SCHEMA_ID]
    execution_mode: Literal["development"]
    qualification_status: Literal["development_unqualified"]
    source_identity: DevelopmentGitSourceIdentityV1
    environment: DevelopmentExecutionEnvironmentV1
    configuration: ArtifactRef

    @model_validator(mode="after")
    def configuration_is_cas_bound(self):
        _require_ref(self.configuration, label="development configuration")
        return self


class DevelopmentCheckV1(HarnessModel):
    name: Literal[
        "input_output_hash_binding",
        "exact_skill_qualification",
        "workflow_receipt_closure",
        "physical_validation",
        "media_closure",
        "codex_attempt_journal",
        "development_source_capture",
        "clean_release_tree",
        "formal_preview_library_admission",
        "formal_preview_publication",
    ]
    status: Literal["executed", "omitted"]
    detail: NonEmptyString
    evidence: tuple[ArtifactRef, ...]

    @model_validator(mode="after")
    def evidence_is_canonical(self):
        _canonical_refs(self.evidence, label=f"{self.name} evidence")
        if (self.status == "executed") != bool(self.evidence):
            raise ValueError("executed checks require evidence and omitted checks forbid it")
        return self


class DevelopmentExecutionRecordV1(HarnessModel):
    """Debug package: inspectable evidence without release or qualification authority."""

    model_config = public_schema_config(DEVELOPMENT_EXECUTION_RECORD_SCHEMA_ID)

    schema_version: Literal[DEVELOPMENT_EXECUTION_RECORD_SCHEMA_ID]
    record_id: Sha256
    workflow_run_id: UUID4
    principal_id: PrincipalId
    idempotency_key: IdempotencyKey
    workspace: Literal["ephemeral"]
    execution_mode: Literal["development"]
    qualification_status: Literal["development_unqualified"]
    exact_skills_qualified: Literal[True]
    changed_exact_skill_closure_allowed: Literal[False]
    formal_library_written: Literal[False]
    workflow_head: ArtifactRef
    development_context: ArtifactRef
    source_identity: DevelopmentGitSourceIdentityV1
    environment: DevelopmentExecutionEnvironmentV1
    configuration: ArtifactRef
    input_artifacts: tuple[ArtifactRef, ...]
    output_artifacts: tuple[ArtifactRef, ...]
    log_artifacts: tuple[ArtifactRef, ...]
    physical_evidence: tuple[ArtifactRef, ...]
    checks: tuple[DevelopmentCheckV1, ...]
    created_at: AwareDatetime

    @model_validator(mode="after")
    def unreleased_evidence_is_complete(self):
        if self.created_at.utcoffset() != timezone.utc.utcoffset(self.created_at):
            raise ValueError("development record timestamp must use UTC")
        _require_ref(
            self.workflow_head,
            label="development workflow head",
            schema_version="harness.workflow_operation_receipt.v2",
        )
        _require_ref(
            self.development_context,
            label="development context",
            schema_version=DEVELOPMENT_EXECUTION_CONTEXT_SCHEMA_ID,
        )
        for refs, label in (
            (self.input_artifacts, "input_artifacts"),
            (self.output_artifacts, "output_artifacts"),
            (self.log_artifacts, "log_artifacts"),
            (self.physical_evidence, "physical_evidence"),
        ):
            if not refs:
                raise ValueError(f"{label} cannot be empty")
            _canonical_refs(refs, label=label)
        if tuple((check.name, check.status) for check in self.checks) != _CHECKS:
            raise ValueError("development checks must name the fixed executed/omitted boundary")
        if self.record_id != development_execution_record_sha256(self):
            raise ValueError("record_id does not bind the canonical development record")
        return self


__all__ = [
    "DEVELOPMENT_EXECUTION_CONTEXT_SCHEMA_ID",
    "DEVELOPMENT_EXECUTION_RECORD_SCHEMA_ID",
    "DevelopmentCheckV1",
    "DevelopmentExecutionContextV1",
    "DevelopmentExecutionEnvironmentV1",
    "DevelopmentExecutionRecordV1",
    "DevelopmentGitSourceIdentityV1",
    "development_execution_record_sha256",
]
