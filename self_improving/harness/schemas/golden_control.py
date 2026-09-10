"""Strict contracts for advisory and prompt-revision workflow controls."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from pydantic import UUID4, AwareDatetime, BeforeValidator, Field, model_validator

from .base import (
    CanonicalSkillRef,
    HarnessModel,
    NonEmptyString,
    NonNegativeInt,
    PositiveInt,
    Sha256,
    public_schema_config,
)
from .common import ArtifactRef
from .workflow import IdempotencyKey, PrincipalId

ADVISORY_RECORD_COMMAND_SCHEMA_ID = "harness.advisory_record_command.v1"
EXTERNAL_AGENT_ADVISORY_RECEIPT_SCHEMA_ID = "harness.external_agent_advisory_receipt.v1"
PROMPT_REVISION_COMMAND_SCHEMA_ID = "harness.prompt_revision_command.v1"
PROMPT_REVISION_SCHEMA_ID = "harness.prompt_revision.v1"
CONTROL_COMMIT_SNAPSHOT_SCHEMA_ID = "harness.control_commit_snapshot.v1"

_ADVISORY_DOMAIN = "harness.external_agent_advisory_receipt.identity.v1"
_PROMPT_REVISION_DOMAIN = "harness.prompt_revision.identity.v1"
_WORKFLOW_RECEIPT_SCHEMAS = frozenset(
    {
        "harness.workflow_start_receipt.v1",
        "harness.workflow_operation_receipt.v2",
        EXTERNAL_AGENT_ADVISORY_RECEIPT_SCHEMA_ID,
        PROMPT_REVISION_SCHEMA_ID,
    }
)
_COMPILE_SKILLS = frozenset(
    {
        "text2env.compile@1.0.0",
        "image2env.compile@1.0.0",
        "video2env.compile@1.0.0",
    }
)


def _require_exact_bool(value: object) -> object:
    if type(value) is not bool:
        raise ValueError("value must be an exact boolean")
    return value


ExactFalse = Annotated[Literal[False], BeforeValidator(_require_exact_bool)]


class ObservationCitation(HarnessModel):
    """Fresh observation receipt and its exact bounded validity window."""

    receipt: ArtifactRef
    captured_at: AwareDatetime
    valid_until: AwareDatetime
    ttl_seconds: Annotated[int, Field(strict=True, ge=1, le=300)]

    @model_validator(mode="after")
    def citation_is_fresh_and_bound(self) -> "ObservationCitation":
        _require_ref(
            self.receipt,
            label="observation_citations receipt",
            schema="harness.genesis_observation_receipt.v1",
        )
        _require_utc(self.captured_at, label="captured_at")
        _require_utc(self.valid_until, label="valid_until")
        if self.valid_until != self.captured_at + timedelta(seconds=self.ttl_seconds):
            raise ValueError(
                "observation citation expired or captured_at plus ttl_seconds "
                "does not equal valid_until"
            )
        return self


class ReplacePromptPatch(HarnessModel):
    kind: Literal["full_replace"]
    base_text_sha256: Sha256
    replacement_text: NonEmptyString
    reason: NonEmptyString


class NoPromptFallback(HarnessModel):
    kind: Literal["none"]


class VideoFrameToImageFallback(HarnessModel):
    kind: Literal["video_frame_to_image"]
    source_video: ArtifactRef
    selected_frame: ArtifactRef
    frame_index: NonNegativeInt
    frame_sequence_manifest: ArtifactRef

    @model_validator(mode="after")
    def frame_is_an_image(self) -> "VideoFrameToImageFallback":
        _require_ref(self.source_video, label="source_video", schema=None, media_type="video/mp4")
        _require_ref(
            self.selected_frame,
            label="selected_frame",
            schema=None,
            media_type="image/png",
        )
        _require_ref(
            self.frame_sequence_manifest,
            label="frame_sequence_manifest",
            schema="harness.video_frame_sequence.v1",
        )
        return self


PromptFallback = Annotated[
    NoPromptFallback | VideoFrameToImageFallback,
    Field(discriminator="kind"),
]


class _ControlCommand(HarnessModel):
    command_id: UUID4
    principal_id: PrincipalId
    workflow_run_id: UUID4
    idempotency_key: IdempotencyKey
    expected_workflow_revision: NonNegativeInt
    expected_state_sha256: Sha256
    expected_receipt_head_sha256: Sha256


class AdvisoryRecordCommand(_ControlCommand):
    """Record one bounded, non-authoritative external-agent advisory."""

    model_config = public_schema_config(ADVISORY_RECORD_COMMAND_SCHEMA_ID)

    schema_version: Literal["harness.advisory_record_command.v1"]
    advisory_kind: Literal["visual_assessment", "diagnosis", "repair_proposal"]
    disposition: Literal["recommend", "abstain"]
    summary: NonEmptyString
    recommended_action: Literal[
        "none", "retry", "revise_prompt", "fallback_image", "repair_assets", "stop"
    ]
    source_prompt: ArtifactRef
    source_prompt_revision: ArtifactRef | None
    failure_receipts: tuple[ArtifactRef, ...]
    observation_citations: tuple[ObservationCitation, ...]
    diagnostic_refs: tuple[ArtifactRef, ...]
    external_agent_executed: ExactFalse

    @model_validator(mode="after")
    def advisory_is_bounded(self) -> "AdvisoryRecordCommand":
        _require_ref(
            self.source_prompt,
            label="source_prompt",
            schema="harness.prompt_text.v1",
            media_type="text/plain",
        )
        if self.source_prompt_revision is not None:
            _require_ref(
                self.source_prompt_revision,
                label="source_prompt_revision",
                schema=PROMPT_REVISION_SCHEMA_ID,
            )
        _require_sorted_refs(
            self.failure_receipts,
            label="failure_receipts",
            schema="harness.workflow_operation_receipt.v2",
        )
        _require_sorted_refs(self.diagnostic_refs, label="diagnostic_refs")
        _require_sorted_citations(self.observation_citations)
        if self.advisory_kind == "visual_assessment" and not self.observation_citations:
            raise ValueError("visual_assessment requires observation_citations")
        if self.advisory_kind in {"diagnosis", "repair_proposal"}:
            if not self.failure_receipts:
                raise ValueError(f"{self.advisory_kind} requires failure_receipts")
            if not self.observation_citations and not self.diagnostic_refs:
                raise ValueError(
                    "diagnosis and repair_proposal require observation_citations or diagnostic_refs"
                )
        if self.disposition == "abstain" and self.recommended_action != "none":
            raise ValueError("abstain disposition requires recommended_action none")
        if self.disposition == "recommend" and self.recommended_action == "none":
            raise ValueError("recommend disposition requires a non-none recommended_action")
        return self


class _ControlReceipt(HarnessModel):
    command_id: UUID4
    workflow_run_id: UUID4
    principal_id: PrincipalId
    command: ArtifactRef
    predecessor_receipt: ArtifactRef
    base_revision: NonNegativeInt
    committed_revision: PositiveInt
    base_turn_seq: NonNegativeInt
    committed_turn_seq: PositiveInt
    base_state_sha256: Sha256
    base_state: ArtifactRef
    resulting_state_sha256: Sha256
    resulting_state: ArtifactRef
    committed_at: AwareDatetime

    def _validate_authority(self, *, command_schema: str) -> None:
        _require_ref(self.command, label="command", schema=command_schema)
        _require_workflow_receipt(self.predecessor_receipt, label="predecessor_receipt")
        _require_ref(
            self.base_state,
            label="base_state",
            schema="harness.trusted_world_state.v1",
        )
        _require_ref(
            self.resulting_state,
            label="resulting_state",
            schema="harness.trusted_world_state.v1",
        )
        _require_utc(self.committed_at, label="committed_at")
        if self.committed_revision != self.base_revision + 1:
            raise ValueError("control receipt must advance exactly one revision")
        if self.committed_turn_seq != self.base_turn_seq + 1:
            raise ValueError("control receipt turn must advance exactly one")
        if (
            self.resulting_state_sha256 != self.base_state_sha256
            or self.resulting_state != self.base_state
        ):
            raise ValueError("control receipt must keep trusted state unchanged")


class ExternalAgentAdvisoryReceipt(_ControlReceipt):
    """Immutable workflow receipt for advisory-only external reasoning."""

    model_config = public_schema_config(EXTERNAL_AGENT_ADVISORY_RECEIPT_SCHEMA_ID)

    schema_version: Literal["harness.external_agent_advisory_receipt.v1"]
    advisory_kind: Literal["visual_assessment", "diagnosis", "repair_proposal"]
    disposition: Literal["recommend", "abstain"]
    summary: NonEmptyString
    recommended_action: Literal[
        "none", "retry", "revise_prompt", "fallback_image", "repair_assets", "stop"
    ]
    source_prompt: ArtifactRef
    source_prompt_revision: ArtifactRef | None
    failure_receipts: tuple[ArtifactRef, ...]
    observation_citations: tuple[ObservationCitation, ...]
    diagnostic_refs: tuple[ArtifactRef, ...]
    authority: Literal["advisory_only"]
    external_agent_executed: ExactFalse
    grants_physical_validity: ExactFalse
    grants_publishability: ExactFalse
    receipt_sha256: Sha256

    @model_validator(mode="after")
    def receipt_is_bound(self) -> "ExternalAgentAdvisoryReceipt":
        self._validate_authority(command_schema=ADVISORY_RECORD_COMMAND_SCHEMA_ID)
        AdvisoryRecordCommand.model_validate(
            {
                "schema_version": ADVISORY_RECORD_COMMAND_SCHEMA_ID,
                "command_id": self.command_id,
                "principal_id": self.principal_id,
                "workflow_run_id": self.workflow_run_id,
                "idempotency_key": "receipt-validation",
                "expected_workflow_revision": self.base_revision,
                "expected_state_sha256": self.base_state_sha256,
                "expected_receipt_head_sha256": self.predecessor_receipt.sha256,
                "external_agent_executed": False,
                "advisory_kind": self.advisory_kind,
                "disposition": self.disposition,
                "summary": self.summary,
                "recommended_action": self.recommended_action,
                "source_prompt": self.source_prompt,
                "source_prompt_revision": self.source_prompt_revision,
                "failure_receipts": self.failure_receipts,
                "observation_citations": self.observation_citations,
                "diagnostic_refs": self.diagnostic_refs,
            }
        )
        _require_citations_fresh(self.observation_citations, at=self.committed_at)
        if self.receipt_sha256 != external_agent_advisory_receipt_sha256(self):
            raise ValueError("receipt_sha256 does not match advisory receipt identity")
        return self


class PromptRevisionCommand(_ControlCommand):
    """Request one immutable full-replacement prompt revision."""

    model_config = public_schema_config(PROMPT_REVISION_COMMAND_SCHEMA_ID)

    schema_version: Literal["harness.prompt_revision_command.v1"]
    source_prompt: ArtifactRef
    advisory_receipt: ArtifactRef
    failure_receipts: tuple[ArtifactRef, ...]
    predecessor_package: ArtifactRef
    patch: ReplacePromptPatch
    fallback: PromptFallback
    next_compile_skill_ref: CanonicalSkillRef
    external_agent_executed: ExactFalse

    @model_validator(mode="after")
    def prompt_request_is_exact(self) -> "PromptRevisionCommand":
        _require_ref(
            self.source_prompt,
            label="source_prompt",
            schema="harness.prompt_text.v1",
            media_type="text/plain",
        )
        _require_ref(
            self.advisory_receipt,
            label="advisory_receipt",
            schema=EXTERNAL_AGENT_ADVISORY_RECEIPT_SCHEMA_ID,
        )
        _require_ref(
            self.predecessor_package,
            label="predecessor_package",
            schema="harness.x2env_environment_package.v1",
        )
        _require_sorted_refs(
            self.failure_receipts,
            label="failure_receipts",
            schema="harness.workflow_operation_receipt.v2",
        )
        if not self.failure_receipts:
            raise ValueError("prompt revision requires failure_receipts")
        if self.patch.base_text_sha256 != self.source_prompt.sha256:
            raise ValueError("patch base_text_sha256 must equal source_prompt identity")
        if self.next_compile_skill_ref not in _COMPILE_SKILLS:
            raise ValueError("next_compile_skill_ref must name an exact compile Skill")
        if (
            isinstance(self.fallback, VideoFrameToImageFallback)
            and self.next_compile_skill_ref != "image2env.compile@1.0.0"
        ):
            raise ValueError("video frame fallback must route to image2env.compile@1.0.0")
        return self


class PromptRevision(_ControlReceipt):
    """Immutable prompt replacement and explicit cross-modal fallback lineage."""

    model_config = public_schema_config(PROMPT_REVISION_SCHEMA_ID)

    schema_version: Literal["harness.prompt_revision.v1"]
    prompt_revision_number: PositiveInt
    source_prompt: ArtifactRef
    predecessor_prompt_revision: ArtifactRef | None
    advisory_receipt: ArtifactRef
    failure_receipts: tuple[ArtifactRef, ...]
    observation_citations: tuple[ObservationCitation, ...]
    predecessor_package: ArtifactRef
    patch: ReplacePromptPatch
    revised_prompt: ArtifactRef
    fallback: PromptFallback
    next_compile_skill_ref: CanonicalSkillRef
    external_agent_executed: ExactFalse
    revision_sha256: Sha256

    @model_validator(mode="after")
    def revision_is_bound(self) -> "PromptRevision":
        self._validate_authority(command_schema=PROMPT_REVISION_COMMAND_SCHEMA_ID)
        _require_ref(
            self.source_prompt,
            label="source_prompt",
            schema="harness.prompt_text.v1",
            media_type="text/plain",
        )
        if self.patch.base_text_sha256 != self.source_prompt.sha256:
            raise ValueError("patch base_text_sha256 must equal source_prompt identity")
        _require_ref(
            self.advisory_receipt,
            label="advisory_receipt",
            schema=EXTERNAL_AGENT_ADVISORY_RECEIPT_SCHEMA_ID,
        )
        _require_ref(
            self.predecessor_package,
            label="predecessor_package",
            schema="harness.x2env_environment_package.v1",
        )
        _require_ref(
            self.revised_prompt,
            label="revised_prompt",
            schema="harness.prompt_text.v1",
            media_type="text/plain",
        )
        if self.predecessor_receipt != self.advisory_receipt:
            raise ValueError("predecessor_receipt must equal advisory_receipt")
        if self.prompt_revision_number == 1:
            if self.predecessor_prompt_revision is not None:
                raise ValueError("first revision cannot have predecessor_prompt_revision")
        elif self.predecessor_prompt_revision is None:
            raise ValueError("later revision requires predecessor_prompt_revision")
        else:
            _require_ref(
                self.predecessor_prompt_revision,
                label="predecessor_prompt_revision",
                schema=PROMPT_REVISION_SCHEMA_ID,
            )
        if not self.failure_receipts:
            raise ValueError("prompt revision requires failure_receipts")
        _require_sorted_refs(
            self.failure_receipts,
            label="failure_receipts",
            schema="harness.workflow_operation_receipt.v2",
        )
        _require_sorted_citations(self.observation_citations)
        _require_citations_fresh(self.observation_citations, at=self.committed_at)
        if self.next_compile_skill_ref not in _COMPILE_SKILLS:
            raise ValueError("next_compile_skill_ref must name an exact compile Skill")
        if (
            isinstance(self.fallback, VideoFrameToImageFallback)
            and self.next_compile_skill_ref != "image2env.compile@1.0.0"
        ):
            raise ValueError("video frame fallback must route to image2env.compile@1.0.0")
        if self.revision_sha256 != prompt_revision_sha256(self):
            raise ValueError("revision_sha256 does not match prompt revision identity")
        return self


class ControlCommitSnapshot(_ControlReceipt):
    """Public result of one terminal-only workflow control commit."""

    model_config = public_schema_config(CONTROL_COMMIT_SNAPSHOT_SCHEMA_ID)

    schema_version: Literal["harness.control_commit_snapshot.v1"]
    control_kind: Literal["advisory", "prompt_revision"]
    idempotency_key: IdempotencyKey
    payload: ArtifactRef
    receipt: ArtifactRef
    receipt_head: ArtifactRef

    @model_validator(mode="after")
    def snapshot_is_bound(self) -> "ControlCommitSnapshot":
        command_schema, record_schema = (
            (
                ADVISORY_RECORD_COMMAND_SCHEMA_ID,
                EXTERNAL_AGENT_ADVISORY_RECEIPT_SCHEMA_ID,
            )
            if self.control_kind == "advisory"
            else (PROMPT_REVISION_COMMAND_SCHEMA_ID, PROMPT_REVISION_SCHEMA_ID)
        )
        self._validate_authority(command_schema=command_schema)
        _require_ref(self.receipt, label="receipt", schema=record_schema)
        if self.payload != self.receipt:
            raise ValueError("payload must equal receipt")
        if self.receipt_head != self.receipt:
            raise ValueError("receipt_head must equal receipt")
        return self


def external_agent_advisory_receipt_sha256(receipt: ExternalAgentAdvisoryReceipt) -> str:
    return _domain_hash(
        domain=_ADVISORY_DOMAIN,
        key="receipt",
        model=receipt,
        hash_field="receipt_sha256",
    )


def prompt_revision_sha256(revision: PromptRevision) -> str:
    return _domain_hash(
        domain=_PROMPT_REVISION_DOMAIN,
        key="revision",
        model=revision,
        hash_field="revision_sha256",
    )


def _domain_hash(*, domain: str, key: str, model: HarnessModel, hash_field: str) -> str:
    payload = {
        "domain": domain,
        key: model.model_dump(mode="json", exclude={hash_field}),
    }
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_ref(
    ref: ArtifactRef,
    *,
    label: str,
    schema: str | None,
    media_type: str = "application/json",
) -> None:
    if ref.schema_version != schema or ref.media_type != media_type:
        raise ValueError(f"{label} must have schema_version {schema!r} and media type {media_type}")


def _require_workflow_receipt(ref: ArtifactRef, *, label: str) -> None:
    if ref.media_type != "application/json" or ref.schema_version not in _WORKFLOW_RECEIPT_SCHEMAS:
        raise ValueError(f"{label} must be a typed workflow receipt")


def _artifact_key(ref: ArtifactRef) -> tuple[str, str, str]:
    return (ref.sha256, ref.schema_version or "", ref.media_type)


def _require_sorted_refs(
    refs: tuple[ArtifactRef, ...],
    *,
    label: str,
    schema: str | None = None,
) -> None:
    identities = tuple(_artifact_key(ref) for ref in refs)
    if identities != tuple(sorted(identities)) or len(identities) != len(set(identities)):
        raise ValueError(f"{label} must be sorted and unique")
    for ref in refs:
        if ref.media_type != "application/json" or (
            schema is not None and ref.schema_version != schema
        ):
            raise ValueError(f"{label} has the wrong schema_version or media type")


def _require_sorted_citations(citations: tuple[ObservationCitation, ...]) -> None:
    identities = tuple(item.receipt.sha256 for item in citations)
    if identities != tuple(sorted(identities)) or len(identities) != len(set(identities)):
        raise ValueError("observation_citations must be sorted and unique")


def _require_citations_fresh(
    citations: tuple[ObservationCitation, ...],
    *,
    at: datetime,
) -> None:
    if any(not citation.captured_at <= at <= citation.valid_until for citation in citations):
        raise ValueError("observation citation expired before the control commit")


def _require_utc(value: datetime, *, label: str) -> None:
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{label} must use UTC")


__all__ = [
    "CONTROL_COMMIT_SNAPSHOT_SCHEMA_ID",
    "ADVISORY_RECORD_COMMAND_SCHEMA_ID",
    "EXTERNAL_AGENT_ADVISORY_RECEIPT_SCHEMA_ID",
    "PROMPT_REVISION_COMMAND_SCHEMA_ID",
    "PROMPT_REVISION_SCHEMA_ID",
    "ControlCommitSnapshot",
    "AdvisoryRecordCommand",
    "ExternalAgentAdvisoryReceipt",
    "NoPromptFallback",
    "ObservationCitation",
    "PromptRevision",
    "PromptRevisionCommand",
    "ReplacePromptPatch",
    "VideoFrameToImageFallback",
    "external_agent_advisory_receipt_sha256",
    "prompt_revision_sha256",
]
