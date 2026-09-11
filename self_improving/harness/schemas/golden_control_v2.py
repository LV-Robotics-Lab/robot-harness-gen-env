"""Advisory-only controls for native scenes and actual restored-state captures.

These records bind bytes and control lineage, never execution or publication authority.
Runtime consumers must still validate trusted workflow ownership and receipt availability.
"""

import hashlib
import json
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from .base import HarnessModel, PositiveInt, Sha256, public_schema_config
from .common import ArtifactRef
from .genesis_fresh_capture import GenesisFreshCaptureReceiptV1
from .golden_control import (
    AdvisoryRecordCommand,
    ControlCommitSnapshot,
    ExactFalse,
    ExternalAgentAdvisoryReceipt,
    PromptFallback,
    ReplacePromptPatch,
    _ControlCommand,
    _ControlReceipt,
    _require_sorted_refs,
)


def _ref(ref: ArtifactRef, schema: str) -> None:
    if (
        ref.schema_version != schema
        or ref.media_type != "application/json"
        or ref.uri != "artifact://sha256/" + ref.sha256
    ):
        raise ValueError("reference must identify exact canonical CAS schema")


class ObservationCitationV2(HarnessModel):
    """Self-contained exact citation; it does not authenticate the cited producer."""

    model_config = public_schema_config("harness.observation_citation.v2")
    schema_version: Literal["harness.observation_citation.v2"]
    receipt: ArtifactRef
    observation: GenesisFreshCaptureReceiptV1
    captured_at: AwareDatetime
    expires_at: AwareDatetime
    ttl_seconds: int = Field(strict=True, ge=1, le=300)
    authority: Literal["observed_restored_final_pose"]
    process_continuation: ExactFalse

    @model_validator(mode="after")
    def exact_capture(self):
        _ref(self.receipt, "harness.genesis_fresh_capture_receipt.v1")
        raw = json.dumps(
            self.observation.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        if hashlib.sha256(raw).hexdigest() != self.receipt.sha256 or len(raw) != self.receipt.bytes:
            raise ValueError("citation payload differs from receipt bytes")
        for name in (
            "captured_at",
            "expires_at",
            "ttl_seconds",
            "authority",
            "process_continuation",
        ):
            if getattr(self, name) != getattr(self.observation, name):
                raise ValueError("citation lifecycle differs from exact receipt")
        if (
            self.captured_at.utcoffset().total_seconds()
            or self.expires_at.utcoffset().total_seconds()
        ):
            raise ValueError("citation lifecycle must use UTC")
        return self


class _RevisionBody(HarnessModel):
    source_prompt: ArtifactRef
    advisory_receipt: ArtifactRef
    failure_receipts: tuple[ArtifactRef, ...] = Field(min_length=1)
    predecessor_environment: ArtifactRef
    patch: ReplacePromptPatch
    fallback: PromptFallback
    next_compile_skill_ref: Literal["image2env.compile@2.0.0"]
    external_agent_executed: ExactFalse

    @model_validator(mode="after")
    def native_revision(self):
        if (
            self.source_prompt.schema_version != "harness.prompt_text.v1"
            or self.source_prompt.media_type != "text/plain"
            or self.source_prompt.uri != "artifact://sha256/" + self.source_prompt.sha256
        ):
            raise ValueError("source prompt must be canonical typed text")
        _ref(self.advisory_receipt, "harness.external_agent_advisory_receipt.v2")
        if self.predecessor_environment.schema_version not in {
            "harness.image2env_genesis_compile_receipt.v1",
            "harness.genesis_scene_execution_receipt.v2",
        }:
            raise ValueError("predecessor environment must be a native compile or replay receipt")
        _ref(self.predecessor_environment, self.predecessor_environment.schema_version)
        _require_sorted_refs(
            self.failure_receipts,
            label="failure_receipts",
            schema="harness.workflow_operation_receipt.v2",
        )
        if self.patch.base_text_sha256 != self.source_prompt.sha256:
            raise ValueError("patch base must equal source prompt digest")
        return self


class PromptRevisionCommandV2(_RevisionBody, _ControlCommand):
    """Control-side proposal only; current native compile does not consume it."""

    model_config = public_schema_config("harness.prompt_revision_command.v2")
    schema_version: Literal["harness.prompt_revision_command.v2"]


def _citations(citations, at=None):
    keys = tuple(c.receipt.sha256 for c in citations)
    if keys != tuple(sorted(set(keys))):
        raise ValueError("observation citations must be sorted and unique")
    if at is not None and any(not c.captured_at <= at <= c.expires_at for c in citations):
        raise ValueError("citation is not fresh at control commit")


class AdvisoryRecordCommandV2(AdvisoryRecordCommand):
    """Versioned reasoning input; the external agent remains outside authority."""

    model_config = public_schema_config("harness.advisory_record_command.v2")
    schema_version: Literal["harness.advisory_record_command.v2"]
    observation_citations: tuple[ObservationCitationV2, ...]

    @model_validator(mode="after")
    def advisory_is_bounded(self):
        if (
            self.source_prompt.schema_version != "harness.prompt_text.v1"
            or self.source_prompt.media_type != "text/plain"
        ):
            raise ValueError("source prompt must be typed text")
        if self.source_prompt_revision is not None:
            _ref(self.source_prompt_revision, "harness.prompt_revision.v2")
        _require_sorted_refs(
            self.failure_receipts,
            label="failure_receipts",
            schema="harness.workflow_operation_receipt.v2",
        )
        _require_sorted_refs(self.diagnostic_refs, label="diagnostic_refs")
        _require_sorted_refs(
            self.resource_access_receipts,
            label="resource_access_receipts",
            schema="harness.resource_access_receipt.v1",
        )
        _citations(self.observation_citations)
        if self.advisory_kind == "visual_assessment" and not self.observation_citations:
            raise ValueError("visual assessment requires an actual observation citation")
        if self.advisory_kind in {"diagnosis", "repair_proposal"}:
            if not self.failure_receipts:
                raise ValueError("diagnosis requires failure receipts")
            if not self.observation_citations and not self.diagnostic_refs:
                raise ValueError("diagnosis requires observation or diagnostics")
        if (self.disposition == "abstain") != (self.recommended_action == "none"):
            raise ValueError("disposition and recommendation disagree")
        return self


def _control(record, command_schema):
    _ref(record.command, command_schema)
    if record.predecessor_receipt.schema_version not in {
        "harness.workflow_start_receipt.v1",
        "harness.workflow_operation_receipt.v2",
        "harness.external_agent_advisory_receipt.v2",
        "harness.prompt_revision.v2",
    }:
        raise ValueError("control predecessor must be a workflow receipt")
    _ref(record.predecessor_receipt, record.predecessor_receipt.schema_version)
    _ref(record.base_state, "harness.trusted_world_state.v1")
    _ref(record.resulting_state, "harness.trusted_world_state.v1")
    if record.committed_at.utcoffset().total_seconds():
        raise ValueError("control timestamp must use UTC")
    if (
        record.committed_revision != record.base_revision + 1
        or record.committed_turn_seq != record.base_turn_seq + 1
    ):
        raise ValueError("control must advance exactly one revision and turn")
    if (
        record.resulting_state_sha256 != record.base_state_sha256
        or record.resulting_state != record.base_state
    ):
        raise ValueError("advisory controls must preserve trusted state")


def _digest(model, field, domain, key):
    data = {"domain": domain, key: model.model_dump(mode="json", exclude={field})}
    return hashlib.sha256(
        (
            json.dumps(
                data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            + "\n"
        ).encode()
    ).hexdigest()


def external_agent_advisory_receipt_v2_sha256(receipt):
    return _digest(
        receipt, "receipt_sha256", "harness.external_agent_advisory_receipt.identity.v2", "receipt"
    )


class ExternalAgentAdvisoryReceiptV2(ExternalAgentAdvisoryReceipt):
    model_config = public_schema_config("harness.external_agent_advisory_receipt.v2")
    schema_version: Literal["harness.external_agent_advisory_receipt.v2"]
    observation_citations: tuple[ObservationCitationV2, ...]

    @model_validator(mode="after")
    def receipt_is_bound(self):
        _control(self, "harness.advisory_record_command.v2")
        header = dict(
            schema_version="harness.advisory_record_command.v2",
            idempotency_key="receipt-validation",
            expected_workflow_revision=self.base_revision,
            expected_state_sha256=self.base_state_sha256,
            expected_receipt_head_sha256=self.predecessor_receipt.sha256,
        )
        AdvisoryRecordCommandV2.model_validate(
            {
                name: header[name] if name in header else getattr(self, name)
                for name in AdvisoryRecordCommandV2.model_fields
            }
        )
        _citations(self.observation_citations, self.committed_at)
        if self.receipt_sha256 != external_agent_advisory_receipt_v2_sha256(self):
            raise ValueError("advisory receipt identity mismatch")
        return self


def prompt_revision_v2_sha256(revision):
    return _digest(revision, "revision_sha256", "harness.prompt_revision.identity.v2", "revision")


class PromptRevisionV2(_RevisionBody, _ControlReceipt):
    model_config = public_schema_config("harness.prompt_revision.v2")
    schema_version: Literal["harness.prompt_revision.v2"]
    prompt_revision_number: PositiveInt
    predecessor_prompt_revision: ArtifactRef | None
    observation_citations: tuple[ObservationCitationV2, ...]
    revised_prompt: ArtifactRef
    authority: Literal["advisory_only"]
    grants_physical_validity: ExactFalse
    grants_publishability: ExactFalse
    revision_sha256: Sha256

    @model_validator(mode="after")
    def revision_is_bound(self):
        _control(self, "harness.prompt_revision_command.v2")
        if (
            self.revised_prompt.schema_version != "harness.prompt_text.v1"
            or self.revised_prompt.media_type != "text/plain"
            or self.revised_prompt.uri != "artifact://sha256/" + self.revised_prompt.sha256
        ):
            raise ValueError("revised prompt must be canonical typed text")
        if self.predecessor_receipt != self.advisory_receipt:
            raise ValueError("revision predecessor must be advisory receipt")
        if (self.prompt_revision_number == 1) != (self.predecessor_prompt_revision is None):
            raise ValueError("revision predecessor presence differs from revision number")
        if self.predecessor_prompt_revision is not None:
            _ref(self.predecessor_prompt_revision, "harness.prompt_revision.v2")
        _citations(self.observation_citations, self.committed_at)
        if self.revision_sha256 != prompt_revision_v2_sha256(self):
            raise ValueError("prompt revision identity mismatch")
        return self


class ControlCommitSnapshotV2(ControlCommitSnapshot):
    model_config = public_schema_config("harness.control_commit_snapshot.v2")
    schema_version: Literal["harness.control_commit_snapshot.v2"]

    @model_validator(mode="after")
    def snapshot_is_bound(self):
        command_schema, record_schema = (
            ("harness.advisory_record_command.v2", "harness.external_agent_advisory_receipt.v2")
            if self.control_kind == "advisory"
            else ("harness.prompt_revision_command.v2", "harness.prompt_revision.v2")
        )
        _control(self, command_schema)
        _ref(self.receipt, record_schema)
        if self.payload != self.receipt or self.receipt_head != self.receipt:
            raise ValueError("snapshot must point to one identical control receipt")
        return self
