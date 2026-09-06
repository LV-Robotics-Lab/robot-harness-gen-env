"""Publish one durable Harness run as a path-free, CAS-complete receipt.

The module's interface is deliberately smaller than either production
application.  Compile and replay applications already expose the same durable
authorities (descriptor list, Invocation/RunState readers, event pages and a
local CAS root); this module concentrates their cross-checking and portable
copy semantics behind one publisher.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, TypeVar
from urllib.parse import urlparse
from uuid import UUID

from pydantic import model_validator

from .artifacts import ArtifactResolutionError, LocalArtifactStore
from .event_journal import EventPage, StoredRunEvent
from .qualification import (
    QUALIFICATION_REPORT_SCHEMA_ID,
    QualificationReportV1,
)
from .registry import _invocation_digest
from .schema_catalog import schema_model
from .schemas import (
    RUN_STATE_SCHEMA_ID,
    SKILL_INVOCATION_SCHEMA_ID,
    ArtifactRef,
    Event,
    Invocation,
    RegisteredSkillDescriptor,
    RunState,
    RunStatus,
    SkillQualification,
)
from .schemas.base import CanonicalSkillRef, HarnessModel, NonNegativeInt, Sha256

PORTABLE_RUN_RECEIPT_SCHEMA = "harness.portable_run_receipt.v1"
RUN_EVENT_TRANSCRIPT_SCHEMA = "harness.run_event_transcript.v1"
_ModelT = TypeVar("_ModelT", bound=HarnessModel)


class RunEvidenceSource(Protocol):
    """Durable application seam shared by compile and replay facades."""

    @property
    def artifact_root(self) -> Path: ...

    @property
    def skills(self) -> tuple[RegisteredSkillDescriptor, ...]: ...

    def events(
        self,
        *,
        after_event_id: int = 0,
        run_id: UUID | None = None,
        limit: int = 200,
    ) -> EventPage: ...

    def invocation(self, run_id: UUID) -> Invocation | None: ...

    def run_state(self, run_id: UUID) -> RunState | None: ...


class PortableRunEventEnvelope(HarnessModel):
    run_id: UUID
    skill_id: str
    skill_version: str
    event: Event


class PortableStoredRunEvent(HarnessModel):
    event_id: int
    envelope: PortableRunEventEnvelope


class RunEventTranscript(HarnessModel):
    schema_version: Literal["harness.run_event_transcript.v1"]
    run_id: UUID
    events: tuple[PortableStoredRunEvent, ...]

    @model_validator(mode="after")
    def event_ids_are_strictly_increasing(self) -> "RunEventTranscript":
        event_ids = [item.event_id for item in self.events]
        if (
            any(event_id <= 0 for event_id in event_ids)
            or event_ids != sorted(event_ids)
            or len(event_ids) != len(set(event_ids))
        ):
            raise ValueError("event transcript ids must be positive and strictly increasing")
        return self


class PortableRunReceipt(HarnessModel):
    """Canonical receipt whose complete closure is readable from one CAS."""

    schema_version: Literal["harness.portable_run_receipt.v1"]
    run_id: UUID
    skill_ref: CanonicalSkillRef
    status: RunStatus
    attempt: NonNegativeInt
    max_attempts: NonNegativeInt
    invocation_digest: Sha256
    descriptor: RegisteredSkillDescriptor
    qualification: ArtifactRef
    qualification_report: ArtifactRef
    invocation: ArtifactRef
    run_state: ArtifactRef
    event_transcript: ArtifactRef
    input_artifacts: tuple[ArtifactRef, ...]
    artifacts: tuple[ArtifactRef, ...]
    closure: tuple[ArtifactRef, ...]

    @model_validator(mode="after")
    def closure_is_canonical(self) -> "PortableRunReceipt":
        if any(
            artifacts != _canonical_artifacts(artifacts)
            for artifacts in (self.input_artifacts, self.artifacts)
        ):
            raise ValueError("portable run artifact summaries must be sorted and unique")
        identities = [_artifact_sort_key(item) for item in self.closure]
        digests = [item.sha256 for item in self.closure]
        if identities != sorted(identities) or len(digests) != len(set(digests)):
            raise ValueError("portable run receipt closure must be sorted and unique")
        required = {
            self.qualification.sha256,
            self.qualification_report.sha256,
            self.invocation.sha256,
            self.run_state.sha256,
            self.event_transcript.sha256,
            *(item.sha256 for item in self.input_artifacts),
            *(item.sha256 for item in self.artifacts),
        }
        if {item.sha256 for item in self.closure} != required:
            raise ValueError("portable run receipt closure is incomplete or contains extras")
        return self


class PortableRunReceiptError(RuntimeError):
    """A durable run or one of its named CAS objects failed cross-binding."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class LoadedPortableRunReceipt:
    receipt_ref: ArtifactRef
    receipt: PortableRunReceipt
    qualification: SkillQualification
    qualification_report: QualificationReportV1
    invocation: Invocation
    run_state: RunState
    event_transcript: RunEventTranscript


class PortableRunReceiptPublisher:
    """Copy and bind one terminal run into a destination content store."""

    def __init__(self, artifact_store: LocalArtifactStore) -> None:
        if type(artifact_store) is not LocalArtifactStore:
            raise TypeError("artifact_store must be LocalArtifactStore")
        self._artifact_store = artifact_store

    def publish(self, source: RunEvidenceSource, run_id: UUID) -> ArtifactRef:
        if not isinstance(run_id, UUID):
            raise TypeError("run_id must be a UUID")
        source_store = _source_store(source)
        try:
            state = source.run_state(run_id)
        except Exception as error:
            raise PortableRunReceiptError(
                "run_state_unavailable",
                "durable RunState authority could not be read",
            ) from error
        if state is not None and not isinstance(state, RunState):
            raise PortableRunReceiptError(
                "run_state_unavailable",
                "durable RunState authority returned an invalid record",
            )
        if state is None:
            raise PortableRunReceiptError("run_missing", f"run state is unavailable: {run_id}")
        if state.status is RunStatus.RUNNING:
            raise PortableRunReceiptError("run_not_terminal", "run state is not terminal")
        try:
            invocation = source.invocation(run_id)
        except Exception as error:
            raise PortableRunReceiptError(
                "invocation_unavailable",
                "durable Invocation authority could not be read",
            ) from error
        if invocation is not None and not isinstance(invocation, Invocation):
            raise PortableRunReceiptError(
                "invocation_unavailable",
                "durable Invocation authority returned an invalid record",
            )
        if invocation is None:
            raise PortableRunReceiptError(
                "invocation_missing",
                "portable execution receipts require a persisted Invocation",
            )
        descriptor = _descriptor(source, invocation)
        typed_parameters = _verify_invocation_identity(invocation, descriptor)
        _verify_state(state, invocation, descriptor)
        _verify_run_artifact_closure(state, descriptor, typed_parameters)
        transcript = _event_transcript(source, state)
        _, _, qualification_payload, report_payload = _qualification(
            source_store,
            descriptor,
        )
        _require_json_schema_ref(
            descriptor.qualification_artifact,
            schema_version="harness.skill_qualification.v1",
            label="qualification",
        )

        qualification_ref = _put_bytes(
            self._artifact_store,
            qualification_payload,
            name=descriptor.qualification_artifact.name,
            media_type="application/json",
            schema_version="harness.skill_qualification.v1",
        )
        report_ref = _put_bytes(
            self._artifact_store,
            report_payload,
            name="qualification_report",
            media_type="application/json",
            schema_version=QUALIFICATION_REPORT_SCHEMA_ID,
        )
        invocation_ref = _put_model(
            self._artifact_store,
            invocation,
            name="invocation",
            schema_version=SKILL_INVOCATION_SCHEMA_ID,
        )
        state_ref = _put_model(
            self._artifact_store,
            state,
            name="run_state",
            schema_version=RUN_STATE_SCHEMA_ID,
        )
        transcript_ref = _put_bytes(
            self._artifact_store,
            _canonical_bytes(transcript.model_dump(mode="json")),
            name="run_event_transcript",
            media_type="application/json",
            schema_version=RUN_EVENT_TRANSCRIPT_SCHEMA,
        )
        copied_artifacts = _canonical_artifacts(
            tuple(
                _copy_artifact(source_store, self._artifact_store, item) for item in state.artifacts
            )
        )
        copied_input_artifacts = _canonical_artifacts(
            tuple(
                _copy_artifact(source_store, self._artifact_store, item)
                for item in _artifact_refs(typed_parameters)
            )
        )
        closure = _canonical_artifacts(
            (
                qualification_ref,
                report_ref,
                invocation_ref,
                state_ref,
                transcript_ref,
                *copied_input_artifacts,
                *copied_artifacts,
            )
        )
        receipt = PortableRunReceipt(
            schema_version=PORTABLE_RUN_RECEIPT_SCHEMA,
            run_id=run_id,
            skill_ref=f"{descriptor.skill_id}@{descriptor.version}",
            status=state.status,
            attempt=state.attempt,
            max_attempts=state.max_attempts,
            invocation_digest=invocation.invocation_digest,
            descriptor=descriptor,
            qualification=qualification_ref,
            qualification_report=report_ref,
            invocation=invocation_ref,
            run_state=state_ref,
            event_transcript=transcript_ref,
            input_artifacts=copied_input_artifacts,
            artifacts=copied_artifacts,
            closure=closure,
        )
        return _put_model(
            self._artifact_store,
            receipt,
            name="portable_run_receipt",
            schema_version=PORTABLE_RUN_RECEIPT_SCHEMA,
        )


def load_portable_run_receipt(
    artifact_store: LocalArtifactStore,
    receipt_ref: ArtifactRef,
) -> LoadedPortableRunReceipt:
    """Deeply reconstruct one portable receipt from its destination CAS only."""

    if type(artifact_store) is not LocalArtifactStore:
        raise TypeError("artifact_store must be LocalArtifactStore")
    if (
        receipt_ref.media_type != "application/json"
        or receipt_ref.schema_version != PORTABLE_RUN_RECEIPT_SCHEMA
    ):
        raise PortableRunReceiptError(
            "receipt_ref_invalid",
            "portable receipt reference has the wrong media type or schema",
        )
    receipt = _parse_canonical(
        _read_artifact(artifact_store, receipt_ref),
        PortableRunReceipt,
        label="portable run receipt",
    )
    for artifact, schema_version, label in (
        (receipt.qualification, "harness.skill_qualification.v1", "qualification"),
        (receipt.qualification_report, QUALIFICATION_REPORT_SCHEMA_ID, "qualification report"),
        (receipt.invocation, SKILL_INVOCATION_SCHEMA_ID, "Invocation"),
        (receipt.run_state, RUN_STATE_SCHEMA_ID, "RunState"),
        (receipt.event_transcript, RUN_EVENT_TRANSCRIPT_SCHEMA, "event transcript"),
    ):
        _require_json_schema_ref(artifact, schema_version=schema_version, label=label)
    for artifact in receipt.closure:
        _read_artifact(artifact_store, artifact)
    if receipt.descriptor.qualification_artifact != receipt.qualification:
        raise PortableRunReceiptError(
            "qualification_binding_mismatch",
            "receipt descriptor does not bind the copied qualification",
        )
    qualification, report, _, _ = _qualification(artifact_store, receipt.descriptor)
    if receipt.qualification_report.sha256 != qualification.report_sha256:
        raise PortableRunReceiptError(
            "qualification_binding_mismatch",
            "receipt report does not bind the copied qualification",
        )
    invocation = _parse_canonical(
        _read_artifact(artifact_store, receipt.invocation),
        Invocation,
        label="Invocation",
    )
    state = _parse_canonical(
        _read_artifact(artifact_store, receipt.run_state),
        RunState,
        label="RunState",
    )
    transcript = _parse_canonical(
        _read_artifact(artifact_store, receipt.event_transcript),
        RunEventTranscript,
        label="event transcript",
    )
    typed_parameters = _verify_invocation_identity(invocation, receipt.descriptor)
    _verify_state(state, invocation, receipt.descriptor)
    _verify_run_artifact_closure(state, receipt.descriptor, typed_parameters)
    _verify_transcript(transcript, state)
    expected_input_artifacts = _canonical_artifacts(_artifact_refs(typed_parameters))
    expected_run_artifacts = _canonical_artifacts(state.artifacts)
    if (
        receipt.run_id != state.run_id
        or receipt.skill_ref != f"{state.skill_id}@{state.skill_version}"
        or receipt.status != state.status
        or receipt.attempt != state.attempt
        or receipt.max_attempts != state.max_attempts
        or receipt.invocation_digest != invocation.invocation_digest
        or _canonical_artifacts(receipt.input_artifacts) != expected_input_artifacts
        or _canonical_artifacts(receipt.artifacts) != expected_run_artifacts
    ):
        raise PortableRunReceiptError(
            "receipt_binding_mismatch",
            "portable receipt fields do not match its reconstructed run",
        )
    return LoadedPortableRunReceipt(
        receipt_ref=receipt_ref,
        receipt=receipt,
        qualification=qualification,
        qualification_report=report,
        invocation=invocation,
        run_state=state,
        event_transcript=transcript,
    )


def _source_store(source: RunEvidenceSource) -> LocalArtifactStore:
    try:
        root = source.artifact_root
    except (AttributeError, TypeError) as error:
        raise PortableRunReceiptError(
            "source_invalid",
            "run evidence source does not expose an artifact root",
        ) from error
    if not isinstance(root, Path):
        raise PortableRunReceiptError("source_invalid", "artifact_root must be a Path")
    try:
        resolved = root.expanduser().resolve(strict=True)
    except OSError as error:
        raise PortableRunReceiptError("source_invalid", "artifact_root is unavailable") from error
    if not resolved.is_dir():
        raise PortableRunReceiptError("source_invalid", "artifact_root is not a directory")
    return LocalArtifactStore(resolved)


def _descriptor(
    source: RunEvidenceSource,
    invocation: Invocation,
) -> RegisteredSkillDescriptor:
    try:
        matches = tuple(
            item
            for item in source.skills
            if item.skill_id == invocation.skill_id and item.version == invocation.skill_version
        )
    except (AttributeError, TypeError) as error:
        raise PortableRunReceiptError(
            "descriptor_unavailable",
            "run evidence source does not expose descriptors",
        ) from error
    if len(matches) != 1:
        raise PortableRunReceiptError(
            "descriptor_unavailable",
            "run descriptor is unavailable or ambiguous",
        )
    return matches[0]


def _verify_state(
    state: RunState,
    invocation: Invocation,
    descriptor: RegisteredSkillDescriptor,
) -> None:
    if (
        state.run_id != invocation.run_id
        or state.invocation_digest != invocation.invocation_digest
        or state.skill_id != invocation.skill_id
        or state.skill_version != invocation.skill_version
        or state.max_attempts != invocation.max_attempts
        or descriptor.max_attempts != invocation.max_attempts
    ):
        raise PortableRunReceiptError(
            "run_binding_mismatch",
            "descriptor, Invocation and RunState do not share one identity",
        )


def _verify_invocation_identity(
    invocation: Invocation,
    descriptor: RegisteredSkillDescriptor,
) -> HarnessModel:
    try:
        typed_parameters = schema_model(descriptor.input_schema).model_validate(
            invocation.effective_parameters
        )
    except Exception as error:
        raise PortableRunReceiptError(
            "invocation_parameters_invalid",
            "Invocation parameters do not satisfy the registered input schema",
        ) from error
    expected = _invocation_digest(
        skill_id=invocation.skill_id,
        skill_version=invocation.skill_version,
        effective_parameters=typed_parameters,
        dependencies=invocation.dependencies,
        max_attempts=invocation.max_attempts,
    )
    if invocation.invocation_digest != expected:
        raise PortableRunReceiptError(
            "invocation_identity_mismatch",
            "Invocation digest does not match its typed content identity",
        )
    return typed_parameters


def _verify_run_artifact_closure(
    state: RunState,
    descriptor: RegisteredSkillDescriptor,
    typed_parameters: HarnessModel,
) -> None:
    output_artifacts: tuple[ArtifactRef, ...] = ()
    if state.status is RunStatus.SUCCEEDED:
        try:
            typed_output = schema_model(descriptor.output_schema).model_validate(state.output)
        except Exception as error:
            raise PortableRunReceiptError(
                "run_output_invalid",
                "terminal output does not satisfy the registered output schema",
            ) from error
        output_artifacts = _artifact_refs(typed_output)
    required = (
        *output_artifacts,
        *(state.blocker.artifact_refs if state.blocker is not None else ()),
        *(artifact for event in state.events for artifact in event.artifact_refs),
    )
    declared = {_artifact_content_key(item) for item in state.artifacts}
    required_set = {_artifact_content_key(item) for item in required}
    permitted = {
        *required_set,
        *(_artifact_content_key(item) for item in _artifact_refs(typed_parameters)),
    }
    if (
        len(declared) != len(state.artifacts)
        or not required_set.issubset(declared)
        or not declared.issubset(permitted)
    ):
        raise PortableRunReceiptError(
            "run_artifact_closure_mismatch",
            "RunState.artifacts does not bind only input, output, blocker, and event artifacts",
        )


def _artifact_refs(value: object) -> tuple[ArtifactRef, ...]:
    found: list[ArtifactRef] = []

    def visit(item: object) -> None:
        if isinstance(item, ArtifactRef):
            found.append(item)
        elif isinstance(item, HarnessModel):
            for name in item.__class__.model_fields:
                visit(getattr(item, name))
        elif isinstance(item, tuple):
            for nested in item:
                visit(nested)

    visit(value)
    return tuple(found)


def _event_transcript(source: RunEvidenceSource, state: RunState) -> RunEventTranscript:
    after_event_id = 0
    collected: list[StoredRunEvent] = []
    while True:
        try:
            page = source.events(
                after_event_id=after_event_id,
                run_id=state.run_id,
                limit=200,
            )
        except Exception as error:
            raise PortableRunReceiptError(
                "event_transcript_unavailable",
                "run event transcript could not be read",
            ) from error
        if not isinstance(page, EventPage):
            raise PortableRunReceiptError(
                "event_transcript_invalid",
                "event source returned an invalid page",
            )
        event_ids = tuple(item.event_id for item in page.events)
        expected_last_event_id = event_ids[-1] if event_ids else after_event_id
        if (
            event_ids != tuple(sorted(event_ids))
            or len(event_ids) != len(set(event_ids))
            or any(event_id <= after_event_id for event_id in event_ids)
            or page.last_event_id != expected_last_event_id
        ):
            raise PortableRunReceiptError(
                "event_transcript_invalid",
                "event page cursor is not strictly monotonic",
            )
        if page.events:
            collected.extend(page.events)
            after_event_id = page.last_event_id
        elif page.has_more:
            raise PortableRunReceiptError(
                "event_transcript_invalid",
                "event source claims more events without advancing",
            )
        if not page.has_more:
            break
    transcript = RunEventTranscript(
        schema_version=RUN_EVENT_TRANSCRIPT_SCHEMA,
        run_id=state.run_id,
        events=tuple(
            PortableStoredRunEvent(
                event_id=item.event_id,
                envelope=PortableRunEventEnvelope(
                    run_id=item.envelope.run_id,
                    skill_id=item.envelope.skill_id,
                    skill_version=item.envelope.skill_version,
                    event=item.envelope.event,
                ),
            )
            for item in collected
        ),
    )
    _verify_transcript(transcript, state)
    return transcript


def _verify_transcript(transcript: RunEventTranscript, state: RunState) -> None:
    envelopes = tuple(item.envelope for item in transcript.events)
    if (
        transcript.run_id != state.run_id
        or tuple(item.event for item in envelopes) != state.events
        or any(item.run_id != state.run_id for item in envelopes)
        or any(item.skill_id != state.skill_id for item in envelopes)
        or any(item.skill_version != state.skill_version for item in envelopes)
    ):
        raise PortableRunReceiptError(
            "event_transcript_mismatch",
            "event journal differs from the terminal RunState",
        )


def _qualification(
    store: LocalArtifactStore,
    descriptor: RegisteredSkillDescriptor,
) -> tuple[SkillQualification, QualificationReportV1, bytes, bytes]:
    qualification_payload = _read_artifact(store, descriptor.qualification_artifact)
    qualification = _parse_document(
        qualification_payload,
        SkillQualification,
        label="qualification",
    )
    try:
        report_path = store.resolve_digest(qualification.report_sha256)
        report_payload = report_path.read_bytes()
        if _sha256(report_payload) != qualification.report_sha256:
            raise ArtifactResolutionError(
                "sha256_mismatch",
                "qualification report changed while it was being read",
            )
    except (ArtifactResolutionError, OSError) as error:
        raise PortableRunReceiptError(
            "qualification_report_unavailable",
            "qualification report is unavailable or corrupt",
        ) from error
    report = _parse_document(
        report_payload,
        QualificationReportV1,
        label="qualification report",
    )
    skill_ref = f"{descriptor.skill_id}@{descriptor.version}"
    if (
        qualification.skill_ref != skill_ref
        or report.skill_ref != skill_ref
        or qualification.deterministic_case_id != report.deterministic_case_id
        or qualification.regression_command != report.regression_command
        or descriptor.implementation_sha256 != report.implementation_sha256
    ):
        raise PortableRunReceiptError(
            "qualification_binding_mismatch",
            "descriptor and qualification documents do not share one identity",
        )
    return qualification, report, qualification_payload, report_payload


def _parse_canonical(payload: bytes, model: type[_ModelT], *, label: str) -> _ModelT:
    value = _parse_document(payload, model, label=label)
    if payload != _canonical_bytes(value.model_dump(mode="json")):
        raise PortableRunReceiptError(
            "document_noncanonical",
            f"{label} is not canonical JSON",
        )
    return value


def _parse_document(payload: bytes, model: type[_ModelT], *, label: str) -> _ModelT:
    try:
        value = model.model_validate_json(payload)
    except Exception as error:
        raise PortableRunReceiptError(
            "document_invalid",
            f"{label} is not a strict document",
        ) from error
    return value


def _read_artifact(store: LocalArtifactStore, artifact: ArtifactRef) -> bytes:
    _require_cas_ref(artifact)
    try:
        payload = store.resolve(artifact).path.read_bytes()
        if len(payload) != artifact.bytes or _sha256(payload) != artifact.sha256:
            raise ArtifactResolutionError(
                "sha256_mismatch",
                "artifact changed while it was being read",
            )
        return payload
    except (ArtifactResolutionError, OSError) as error:
        raise PortableRunReceiptError(
            "artifact_unavailable",
            f"artifact is unavailable or corrupt: {artifact.name}",
        ) from error


def _copy_artifact(
    source: LocalArtifactStore,
    destination: LocalArtifactStore,
    artifact: ArtifactRef,
) -> ArtifactRef:
    payload = _read_artifact(source, artifact)
    return _put_bytes(
        destination,
        payload,
        name=artifact.name,
        media_type=artifact.media_type,
        schema_version=artifact.schema_version,
    )


def _require_cas_ref(artifact: ArtifactRef) -> None:
    parsed = urlparse(artifact.uri)
    if (
        parsed.scheme != "artifact"
        or parsed.netloc != "sha256"
        or parsed.path != f"/{artifact.sha256}"
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise PortableRunReceiptError(
            "artifact_locator_invalid",
            "portable receipts only accept canonical artifact:// references",
        )


def _require_json_schema_ref(
    artifact: ArtifactRef,
    *,
    schema_version: str,
    label: str,
) -> None:
    if artifact.media_type != "application/json" or artifact.schema_version != schema_version:
        raise PortableRunReceiptError(
            "artifact_schema_mismatch",
            f"{label} reference has the wrong media type or schema",
        )


def _put_model(
    store: LocalArtifactStore,
    value: HarnessModel,
    *,
    name: str,
    schema_version: str,
) -> ArtifactRef:
    return _put_bytes(
        store,
        _canonical_bytes(value.model_dump(mode="json")),
        name=name,
        media_type="application/json",
        schema_version=schema_version,
    )


def _put_bytes(
    store: LocalArtifactStore,
    payload: bytes,
    *,
    name: str,
    media_type: str,
    schema_version: str | None,
) -> ArtifactRef:
    with tempfile.NamedTemporaryFile(prefix=".portable-run.", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(payload)
        stream.flush()
    try:
        return store.put_file(
            temporary,
            name=name,
            media_type=media_type,
            schema_version=schema_version,
        )
    finally:
        temporary.unlink(missing_ok=True)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_artifacts(artifacts: tuple[ArtifactRef, ...]) -> tuple[ArtifactRef, ...]:
    by_digest: dict[str, ArtifactRef] = {}
    for artifact in artifacts:
        current = by_digest.get(artifact.sha256)
        if current is None or _artifact_sort_key(artifact) < _artifact_sort_key(current):
            by_digest[artifact.sha256] = artifact
    return tuple(sorted(by_digest.values(), key=_artifact_sort_key))


def _artifact_sort_key(artifact: ArtifactRef) -> tuple[str, str, str, str]:
    return (
        artifact.sha256,
        artifact.media_type,
        artifact.schema_version or "",
        artifact.name,
    )


def _artifact_content_key(artifact: ArtifactRef) -> tuple[str, int, str, str]:
    return (
        artifact.sha256,
        artifact.bytes,
        artifact.media_type,
        artifact.schema_version or "",
    )


def _sha256(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "LoadedPortableRunReceipt",
    "PORTABLE_RUN_RECEIPT_SCHEMA",
    "PortableRunReceipt",
    "PortableRunReceiptError",
    "PortableRunReceiptPublisher",
    "RUN_EVENT_TRANSCRIPT_SCHEMA",
    "RunEvidenceSource",
    "RunEventTranscript",
    "load_portable_run_receipt",
]
