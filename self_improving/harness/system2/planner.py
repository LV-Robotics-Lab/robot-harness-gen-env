"""Receipt-producing System 2 planner provider boundary.

An LLM remains an untrusted proposal generator.  The planner publishes the
exact prompt and raw response, rechecks provider identity before and after the
call, validates one bounded decision, and publishes a terminal receipt before
returning success.  Progress events are emitted at those real code boundaries;
an observer cannot change the execution result.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Callable, Literal, Protocol
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from ..artifacts import LocalArtifactStore
from ..schemas import ArtifactRef
from ..schemas.base import HarnessModel, JsonObject, Sha256
from .context import (
    PlannerContext,
    PlannerDecision,
    build_planner_prompt,
    decision_sha256,
    parse_planner_decision,
    verify_planner_context,
)

PROVIDER_IDENTITY_SCHEMA = "harness.planner_provider_identity.v1"
PROGRESS_EVENT_SCHEMA = "harness.planner_progress_event.v1"
EXECUTION_RECEIPT_SCHEMA = "harness.planner_execution_receipt.v1"
PROMPT_SCHEMA = "harness.planner_prompt.v1"
RAW_RESPONSE_SCHEMA = "harness.planner_raw_response.v1"

_CAS_PREFIX = "artifact://sha256/"
_Stage = Annotated[
    str,
    Field(strict=True, pattern=r"^[a-z][a-z0-9_.]{0,126}[a-z0-9]$", max_length=128),
]
_CanonicalText = Annotated[str, Field(strict=True, min_length=1, max_length=255)]
_FailureReason = Literal[
    "provider_failed",
    "provider_identity_drift",
    "response_invalid",
    "evidence_publish_failed",
]
_PROVIDER_STAGE = re.compile(r"^[a-z][a-z0-9_.]{0,62}[a-z0-9]$")


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
    ref: ArtifactRef,
    *,
    label: str,
    schema: str,
    media_type: str | None = None,
) -> None:
    if ref.uri != f"{_CAS_PREFIX}{ref.sha256}":
        raise ValueError(f"{label} must use its content-addressed artifact URI")
    if ref.schema_version != schema:
        raise ValueError(f"{label} must have schema_version={schema}")
    if media_type is not None and ref.media_type != media_type:
        raise ValueError(f"{label} has the wrong media_type")


class PlannerProviderIdentity(HarnessModel):
    """Path-free identity of one exact model provider configuration."""

    schema_version: Literal["harness.planner_provider_identity.v1"] = PROVIDER_IDENTITY_SCHEMA
    provider_id: _CanonicalText
    model_id: _CanonicalText
    model_revision: _CanonicalText
    model_snapshot_sha256: Sha256
    implementation_sha256: Sha256
    inference: JsonObject
    network_access: bool

    @model_validator(mode="after")
    def text_fields_are_canonical(self) -> "PlannerProviderIdentity":
        for label, value in (
            ("provider_id", self.provider_id),
            ("model_id", self.model_id),
            ("model_revision", self.model_revision),
        ):
            if value.strip() != value:
                raise ValueError(f"{label} must not have surrounding whitespace")
        return self


def provider_identity_sha256(identity: PlannerProviderIdentity) -> str:
    """Return the domain-separated identity of a provider configuration."""

    return hashlib.sha256(
        _canonical_json_bytes(
            {
                "domain": "harness.planner_provider.identity.v1",
                "identity": identity.model_dump(mode="json"),
            }
        )
    ).hexdigest()


def _verify_provider_identity(identity: PlannerProviderIdentity) -> PlannerProviderIdentity:
    return PlannerProviderIdentity.model_validate(identity.model_dump(mode="python"))


class PlannerUsage(HarnessModel):
    """Measured provider resource facts; absent facts remain explicit nulls."""

    input_tokens: Annotated[int, Field(strict=True, ge=0)]
    output_tokens: Annotated[int, Field(strict=True, ge=0)]
    wall_time_ms: Annotated[int, Field(strict=True, ge=0)]
    peak_vram_bytes: Annotated[int, Field(strict=True, ge=0)] | None
    finish_reason: _CanonicalText

    @model_validator(mode="after")
    def finish_reason_is_canonical(self) -> "PlannerUsage":
        if self.finish_reason.strip() != self.finish_reason:
            raise ValueError("finish_reason must not have surrounding whitespace")
        return self


@dataclass(frozen=True, slots=True)
class PlannerProviderResponse:
    """Raw provider bytes plus measured usage, before decision parsing."""

    raw: bytes
    usage: PlannerUsage

    def __post_init__(self) -> None:
        if type(self.raw) is not bytes:
            raise TypeError("provider raw response must be bytes")
        if type(self.usage) is not PlannerUsage:
            raise TypeError("provider usage must be PlannerUsage")


class PlannerProvider(Protocol):
    """One exact model backend.  Provider progress cannot publish artifacts."""

    @property
    def identity(self) -> PlannerProviderIdentity: ...

    def invoke(
        self,
        prompt: bytes,
        progress: Callable[[str], None],
    ) -> PlannerProviderResponse: ...


class PlannerArtifactPublisher(Protocol):
    """Publish exact planner bytes and return their content identity."""

    def publish(
        self,
        payload: bytes,
        *,
        name: str,
        media_type: str,
        schema_version: str,
    ) -> ArtifactRef: ...


@dataclass(frozen=True, slots=True)
class LocalPlannerArtifactPublisher:
    """Adapter from exact in-memory bytes to the repository CAS."""

    artifact_store: LocalArtifactStore
    scratch_root: Path

    def publish(
        self,
        payload: bytes,
        *,
        name: str,
        media_type: str,
        schema_version: str,
    ) -> ArtifactRef:
        if type(payload) is not bytes:
            raise TypeError("planner artifact payload must be bytes")
        scratch = self.scratch_root.expanduser().resolve()
        scratch.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(dir=scratch, prefix=".planner.")
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            return self.artifact_store.put_file(
                temporary,
                name=name,
                media_type=media_type,
                schema_version=schema_version,
            )
        finally:
            temporary.unlink(missing_ok=True)


class PlannerProgressEvent(HarnessModel):
    """A callback projection emitted immediately after a real boundary."""

    schema_version: Literal["harness.planner_progress_event.v1"] = PROGRESS_EVENT_SCHEMA
    call_id: UUID
    seq: Annotated[int, Field(strict=True, ge=1)]
    timestamp: AwareDatetime
    stage: _Stage
    artifact_refs: tuple[ArtifactRef, ...]

    @model_validator(mode="after")
    def event_is_canonical(self) -> "PlannerProgressEvent":
        _require_utc(self.timestamp, label="timestamp")
        for artifact in self.artifact_refs:
            if artifact.uri != f"{_CAS_PREFIX}{artifact.sha256}":
                raise ValueError("progress artifacts must be content-addressed")
        return self


class PlannerExecutionReceipt(HarnessModel):
    """Terminal, content-addressed evidence for one model planning call."""

    schema_version: Literal["harness.planner_execution_receipt.v1"] = EXECUTION_RECEIPT_SCHEMA
    call_id: UUID
    status: Literal["succeeded", "failed"]
    started_at: AwareDatetime
    ended_at: AwareDatetime
    world_state_sha256: Sha256
    context_sha256: Sha256
    provider_identity: PlannerProviderIdentity
    provider_identity_sha256: Sha256
    prompt: ArtifactRef
    raw_response: ArtifactRef | None
    decision: ArtifactRef | None
    decision_sha256: Sha256 | None
    usage: PlannerUsage | None
    event_stages: tuple[_Stage, ...]
    observer_failure_stages: tuple[_Stage, ...]
    failure_reason: _FailureReason | None
    failure_stage: _Stage | None
    failure_type: _CanonicalText | None

    @model_validator(mode="after")
    def receipt_is_terminal_and_cross_bound(self) -> "PlannerExecutionReceipt":
        _require_utc(self.started_at, label="started_at")
        _require_utc(self.ended_at, label="ended_at")
        if self.ended_at < self.started_at:
            raise ValueError("ended_at cannot precede started_at")
        if self.provider_identity_sha256 != provider_identity_sha256(self.provider_identity):
            raise ValueError("provider identity digest mismatch")
        _require_cas(
            self.prompt,
            label="planner prompt",
            schema=PROMPT_SCHEMA,
            media_type="application/json",
        )
        if self.raw_response is not None:
            _require_cas(
                self.raw_response,
                label="planner raw response",
                schema=RAW_RESPONSE_SCHEMA,
                media_type="application/octet-stream",
            )
        if self.decision is not None:
            _require_cas(
                self.decision,
                label="planner decision",
                schema="harness.planner_decision.v1",
                media_type="application/json",
            )
        if not self.event_stages or self.event_stages[0] != "context.validated":
            raise ValueError("receipt must start from a validated context boundary")
        if not set(self.observer_failure_stages).issubset(self.event_stages):
            raise ValueError("observer failures must refer to emitted event stages")
        if self.status == "succeeded":
            if (
                self.raw_response is None
                or self.decision is None
                or self.decision_sha256 is None
                or self.usage is None
                or self.failure_reason is not None
                or self.failure_stage is not None
                or self.failure_type is not None
            ):
                raise ValueError("successful planner receipt is incomplete or contradictory")
        elif (
            self.decision is not None
            or self.decision_sha256 is not None
            or self.failure_reason is None
            or self.failure_stage is None
            or self.failure_type is None
        ):
            raise ValueError("failed planner receipt is incomplete or contradictory")
        return self


@dataclass(frozen=True, slots=True)
class PlannerExecution:
    """Validated proposal and its complete local evidence closure."""

    call_id: UUID
    decision: PlannerDecision
    decision_sha256: str
    prompt_ref: ArtifactRef
    raw_response_ref: ArtifactRef
    decision_ref: ArtifactRef
    receipt: PlannerExecutionReceipt
    receipt_ref: ArtifactRef
    events: tuple[PlannerProgressEvent, ...]
    observer_failure_stages: tuple[str, ...]


class PlannerExecutionError(RuntimeError):
    """A planning call failed without exposing exception text in evidence."""

    def __init__(
        self,
        *,
        reason: str,
        receipt_ref: ArtifactRef | None,
        cause: Exception,
    ) -> None:
        self.reason = reason
        self.receipt_ref = receipt_ref
        self.cause_type = type(cause).__name__
        super().__init__(f"planner execution failed: {reason}")


class _IdentityDrift(RuntimeError):
    pass


class _EvidencePublishFailure(RuntimeError):
    pass


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


class System2Planner:
    """Deep module that turns one provider call into a validated proposal receipt."""

    def __init__(
        self,
        *,
        provider: PlannerProvider,
        publisher: PlannerArtifactPublisher,
        progress: Callable[[PlannerProgressEvent], None] | None = None,
        clock: Callable[[], datetime] = _default_clock,
        uuid_factory: Callable[[], UUID] = uuid4,
        max_response_bytes: int = 65_536,
    ) -> None:
        if type(max_response_bytes) is not int or max_response_bytes < 1:
            raise ValueError("max_response_bytes must be a positive integer")
        self._provider = provider
        self._publisher = publisher
        self._progress = progress
        self._clock = clock
        self._uuid_factory = uuid_factory
        self._max_response_bytes = max_response_bytes

    def plan(self, context: PlannerContext) -> PlannerExecution:
        """Call the provider once and return only after receipt publication."""

        call_id = self._uuid_factory()
        started_at = self._clock()
        events: list[PlannerProgressEvent] = []
        observer_failures: list[str] = []

        def emit(stage: str, *artifacts: ArtifactRef) -> None:
            event = PlannerProgressEvent(
                call_id=call_id,
                seq=len(events) + 1,
                timestamp=self._clock(),
                stage=stage,
                artifact_refs=artifacts,
            )
            events.append(event)
            if self._progress is not None:
                try:
                    self._progress(event)
                except Exception:
                    observer_failures.append(stage)

        try:
            trusted_context = verify_planner_context(context)
        except Exception as exc:
            raise PlannerExecutionError(
                reason="context_invalid",
                receipt_ref=None,
                cause=exc,
            ) from exc
        emit("context.validated")
        prompt_bytes = build_planner_prompt(trusted_context)
        try:
            prompt_ref = self._publish(
                prompt_bytes,
                name="planner_prompt",
                media_type="application/json",
                schema_version=PROMPT_SCHEMA,
            )
        except Exception as exc:
            raise PlannerExecutionError(
                reason="evidence_publish_failed",
                receipt_ref=None,
                cause=exc,
            ) from exc
        emit("prompt.published", prompt_ref)

        try:
            identity = _verify_provider_identity(self._provider.identity)
        except Exception as exc:
            raise PlannerExecutionError(
                reason="provider_identity_invalid",
                receipt_ref=None,
                cause=exc,
            ) from exc
        identity_sha256 = provider_identity_sha256(identity)
        emit("provider.started")

        def provider_progress(stage: str) -> None:
            if type(stage) is not str or _PROVIDER_STAGE.fullmatch(stage) is None:
                raise ValueError("provider progress stage is invalid")
            emit(f"provider.{stage}")

        try:
            response = self._provider.invoke(prompt_bytes, provider_progress)
            if type(response) is not PlannerProviderResponse:
                raise TypeError("provider must return PlannerProviderResponse")
        except Exception as exc:
            receipt_ref = self._publish_failure_receipt(
                call_id=call_id,
                started_at=started_at,
                context=trusted_context,
                identity=identity,
                identity_sha256=identity_sha256,
                prompt_ref=prompt_ref,
                raw_ref=None,
                usage=None,
                reason="provider_failed",
                stage="provider.invoke",
                cause=exc,
                events=events,
                observer_failures=observer_failures,
                emit=emit,
            )
            raise PlannerExecutionError(
                reason="provider_failed",
                receipt_ref=receipt_ref,
                cause=exc,
            ) from exc
        emit("provider.completed")

        try:
            raw_ref = self._publish(
                response.raw,
                name="planner_raw_response",
                media_type="application/octet-stream",
                schema_version=RAW_RESPONSE_SCHEMA,
            )
        except Exception as exc:
            receipt_ref = self._publish_failure_receipt(
                call_id=call_id,
                started_at=started_at,
                context=trusted_context,
                identity=identity,
                identity_sha256=identity_sha256,
                prompt_ref=prompt_ref,
                raw_ref=None,
                usage=response.usage,
                reason="evidence_publish_failed",
                stage="raw_response.publish",
                cause=exc,
                events=events,
                observer_failures=observer_failures,
                emit=emit,
            )
            raise PlannerExecutionError(
                reason="evidence_publish_failed",
                receipt_ref=receipt_ref,
                cause=exc,
            ) from exc
        emit("raw_response.published", raw_ref)

        try:
            post_identity = _verify_provider_identity(self._provider.identity)
            if provider_identity_sha256(post_identity) != identity_sha256:
                raise _IdentityDrift("provider identity changed during invocation")
        except Exception as exc:
            receipt_ref = self._publish_failure_receipt(
                call_id=call_id,
                started_at=started_at,
                context=trusted_context,
                identity=identity,
                identity_sha256=identity_sha256,
                prompt_ref=prompt_ref,
                raw_ref=raw_ref,
                usage=response.usage,
                reason="provider_identity_drift",
                stage="provider.postflight",
                cause=exc,
                events=events,
                observer_failures=observer_failures,
                emit=emit,
            )
            raise PlannerExecutionError(
                reason="provider_identity_drift",
                receipt_ref=receipt_ref,
                cause=exc,
            ) from exc

        try:
            decision = parse_planner_decision(
                response.raw,
                context=trusted_context,
                max_response_bytes=self._max_response_bytes,
            )
        except Exception as exc:
            receipt_ref = self._publish_failure_receipt(
                call_id=call_id,
                started_at=started_at,
                context=trusted_context,
                identity=identity,
                identity_sha256=identity_sha256,
                prompt_ref=prompt_ref,
                raw_ref=raw_ref,
                usage=response.usage,
                reason="response_invalid",
                stage="response.validate",
                cause=exc,
                events=events,
                observer_failures=observer_failures,
                emit=emit,
            )
            raise PlannerExecutionError(
                reason="response_invalid",
                receipt_ref=receipt_ref,
                cause=exc,
            ) from exc
        emit("decision.validated")
        decision_digest = decision_sha256(decision)
        decision_bytes = _canonical_json_bytes(decision.model_dump(mode="json"))
        try:
            decision_ref = self._publish(
                decision_bytes,
                name="planner_decision",
                media_type="application/json",
                schema_version="harness.planner_decision.v1",
            )
        except Exception as exc:
            receipt_ref = self._publish_failure_receipt(
                call_id=call_id,
                started_at=started_at,
                context=trusted_context,
                identity=identity,
                identity_sha256=identity_sha256,
                prompt_ref=prompt_ref,
                raw_ref=raw_ref,
                usage=response.usage,
                reason="evidence_publish_failed",
                stage="decision.publish",
                cause=exc,
                events=events,
                observer_failures=observer_failures,
                emit=emit,
            )
            raise PlannerExecutionError(
                reason="evidence_publish_failed",
                receipt_ref=receipt_ref,
                cause=exc,
            ) from exc
        emit("decision.published", decision_ref)
        receipt = PlannerExecutionReceipt(
            call_id=call_id,
            status="succeeded",
            started_at=started_at,
            ended_at=self._clock(),
            world_state_sha256=trusted_context.world_state_sha256,
            context_sha256=trusted_context.context_sha256,
            provider_identity=identity,
            provider_identity_sha256=identity_sha256,
            prompt=prompt_ref,
            raw_response=raw_ref,
            decision=decision_ref,
            decision_sha256=decision_digest,
            usage=response.usage,
            event_stages=tuple(event.stage for event in events),
            observer_failure_stages=tuple(observer_failures),
            failure_reason=None,
            failure_stage=None,
            failure_type=None,
        )
        try:
            receipt_ref = self._publish_receipt(receipt)
        except Exception as exc:
            raise PlannerExecutionError(
                reason="evidence_publish_failed",
                receipt_ref=None,
                cause=exc,
            ) from exc
        emit("receipt.published", receipt_ref)
        return PlannerExecution(
            call_id=call_id,
            decision=decision,
            decision_sha256=decision_digest,
            prompt_ref=prompt_ref,
            raw_response_ref=raw_ref,
            decision_ref=decision_ref,
            receipt=receipt,
            receipt_ref=receipt_ref,
            events=tuple(events),
            observer_failure_stages=tuple(observer_failures),
        )

    def _publish(
        self,
        payload: bytes,
        *,
        name: str,
        media_type: str,
        schema_version: str,
    ) -> ArtifactRef:
        try:
            ref = self._publisher.publish(
                payload,
                name=name,
                media_type=media_type,
                schema_version=schema_version,
            )
        except Exception as exc:
            raise _EvidencePublishFailure("artifact publisher failed") from exc
        digest = hashlib.sha256(payload).hexdigest()
        if (
            type(ref) is not ArtifactRef
            or ref.name != name
            or ref.uri != f"{_CAS_PREFIX}{digest}"
            or ref.media_type != media_type
            or ref.sha256 != digest
            or ref.bytes != len(payload)
            or ref.schema_version != schema_version
        ):
            raise _EvidencePublishFailure("artifact publisher returned a false identity")
        return ref

    def _publish_receipt(self, receipt: PlannerExecutionReceipt) -> ArtifactRef:
        return self._publish(
            _canonical_json_bytes(receipt.model_dump(mode="json")),
            name="planner_execution_receipt",
            media_type="application/json",
            schema_version=EXECUTION_RECEIPT_SCHEMA,
        )

    def _publish_failure_receipt(
        self,
        *,
        call_id: UUID,
        started_at: datetime,
        context: PlannerContext,
        identity: PlannerProviderIdentity,
        identity_sha256: str,
        prompt_ref: ArtifactRef,
        raw_ref: ArtifactRef | None,
        usage: PlannerUsage | None,
        reason: _FailureReason,
        stage: str,
        cause: Exception,
        events: list[PlannerProgressEvent],
        observer_failures: list[str],
        emit: Callable[..., None],
    ) -> ArtifactRef | None:
        receipt = PlannerExecutionReceipt(
            call_id=call_id,
            status="failed",
            started_at=started_at,
            ended_at=self._clock(),
            world_state_sha256=context.world_state_sha256,
            context_sha256=context.context_sha256,
            provider_identity=identity,
            provider_identity_sha256=identity_sha256,
            prompt=prompt_ref,
            raw_response=raw_ref,
            decision=None,
            decision_sha256=None,
            usage=usage,
            event_stages=tuple(event.stage for event in events),
            observer_failure_stages=tuple(observer_failures),
            failure_reason=reason,
            failure_stage=stage,
            failure_type=type(cause).__name__,
        )
        try:
            receipt_ref = self._publish_receipt(receipt)
        except Exception:
            return None
        emit("receipt.published", receipt_ref)
        return receipt_ref
