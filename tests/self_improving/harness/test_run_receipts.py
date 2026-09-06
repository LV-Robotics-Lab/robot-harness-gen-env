from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.event_journal import EventPage, SQLiteEventJournal, StoredRunEvent
from self_improving.harness.events import RunRecorder
from self_improving.harness.qualification import QualificationCheckV1, QualificationReportV1
from self_improving.harness.run_receipts import (
    PortableRunReceipt,
    PortableRunReceiptError,
    PortableRunReceiptPublisher,
    RunEventTranscript,
    load_portable_run_receipt,
)
from self_improving.harness.run_store import SQLiteRunStore
from self_improving.harness.schemas import (
    ArtifactRef,
    Blocker,
    CompileConfig,
    Invocation,
    RegisteredSkillDescriptor,
    RunState,
    RunStatus,
    SkillDescriptor,
    SkillQualification,
    Text2EnvCompileInput,
    Text2EnvReplayOutput,
)

RUN_ID = UUID("12345678-1234-4234-9234-123456789abc")
START = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)
SKILL_ID = "fixture.portable"
SKILL_REF = f"{SKILL_ID}@1.0.0"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _put_bytes(
    store: LocalArtifactStore,
    root: Path,
    payload: bytes,
    *,
    name: str,
    schema_version: str,
) -> ArtifactRef:
    source = root / f"{name}.json"
    source.write_bytes(payload)
    return store.put_file(
        source,
        name=name,
        media_type="application/json",
        schema_version=schema_version,
    )


@dataclass(frozen=True)
class _RunEvidenceSource:
    artifact_root: Path
    descriptor: RegisteredSkillDescriptor
    journal: SQLiteEventJournal
    store: SQLiteRunStore

    @property
    def skills(self) -> tuple[RegisteredSkillDescriptor, ...]:
        return (self.descriptor,)

    def events(
        self,
        *,
        after_event_id: int = 0,
        run_id: UUID | None = None,
        limit: int = 200,
    ) -> EventPage:
        return self.journal.read(after_event_id=after_event_id, run_id=run_id, limit=limit)

    def invocation(self, run_id: UUID) -> Invocation | None:
        return self.store.read_invocation(run_id)

    def run_state(self, run_id: UUID) -> RunState | None:
        return self.store.read_run_state(run_id)


@dataclass(frozen=True)
class _EventOverrideSource:
    delegate: _RunEvidenceSource
    page: EventPage

    @property
    def artifact_root(self) -> Path:
        return self.delegate.artifact_root

    @property
    def skills(self) -> tuple[RegisteredSkillDescriptor, ...]:
        return self.delegate.skills

    def events(
        self,
        *,
        after_event_id: int = 0,
        run_id: UUID | None = None,
        limit: int = 200,
    ) -> EventPage:
        del after_event_id, run_id, limit
        return self.page

    def invocation(self, run_id: UUID) -> Invocation | None:
        return self.delegate.invocation(run_id)

    def run_state(self, run_id: UUID) -> RunState | None:
        return self.delegate.run_state(run_id)


@dataclass(frozen=True)
class _FaultingSource:
    delegate: _RunEvidenceSource
    fault: str

    @property
    def artifact_root(self) -> Path:
        return self.delegate.artifact_root

    @property
    def skills(self) -> tuple[RegisteredSkillDescriptor, ...]:
        if self.fault == "skills":
            raise TypeError("descriptor authority failed")
        if self.fault == "descriptor_missing":
            return ()
        return self.delegate.skills

    def events(
        self,
        *,
        after_event_id: int = 0,
        run_id: UUID | None = None,
        limit: int = 200,
    ) -> EventPage:
        if self.fault == "events":
            raise RuntimeError("event authority failed")
        if self.fault == "events_invalid":
            return object()  # type: ignore[return-value]
        if self.fault == "events_empty_more":
            return EventPage(events=(), last_event_id=after_event_id, has_more=True)
        if self.fault == "events_empty":
            return EventPage(events=(), last_event_id=after_event_id, has_more=False)
        return self.delegate.events(
            after_event_id=after_event_id,
            run_id=run_id,
            limit=limit,
        )

    def invocation(self, run_id: UUID) -> Invocation | None:
        if self.fault == "invocation":
            raise RuntimeError("invocation authority failed")
        if self.fault == "invocation_missing":
            return None
        if self.fault == "invocation_invalid":
            return object()  # type: ignore[return-value]
        return self.delegate.invocation(run_id)

    def run_state(self, run_id: UUID) -> RunState | None:
        if self.fault == "run_state":
            raise RuntimeError("run-state authority failed")
        if self.fault == "run_missing":
            return None
        if self.fault == "run_state_invalid":
            return object()  # type: ignore[return-value]
        if self.fault == "run_running":
            state = self.delegate.run_state(run_id)
            assert state is not None
            return state.model_copy(update={"status": RunStatus.RUNNING})
        if self.fault == "run_binding":
            state = self.delegate.run_state(run_id)
            assert state is not None
            return state.model_copy(update={"max_attempts": 2})
        if self.fault == "output_invalid":
            state = self.delegate.run_state(run_id)
            assert state is not None
            return state.model_copy(update={"output": {}})
        return self.delegate.run_state(run_id)


@dataclass(frozen=True)
class _RootOverrideSource:
    delegate: _RunEvidenceSource
    root: object
    raises: bool = False

    @property
    def artifact_root(self) -> object:
        if self.raises:
            raise AttributeError("artifact root unavailable")
        return self.root

    @property
    def skills(self) -> tuple[RegisteredSkillDescriptor, ...]:
        return self.delegate.skills

    def events(
        self,
        *,
        after_event_id: int = 0,
        run_id: UUID | None = None,
        limit: int = 200,
    ) -> EventPage:
        return self.delegate.events(
            after_event_id=after_event_id,
            run_id=run_id,
            limit=limit,
        )

    def invocation(self, run_id: UUID) -> Invocation | None:
        return self.delegate.invocation(run_id)

    def run_state(self, run_id: UUID) -> RunState | None:
        return self.delegate.run_state(run_id)


@dataclass(frozen=True)
class _PaginatedSource:
    delegate: _RunEvidenceSource

    @property
    def artifact_root(self) -> Path:
        return self.delegate.artifact_root

    @property
    def skills(self) -> tuple[RegisteredSkillDescriptor, ...]:
        return self.delegate.skills

    def events(
        self,
        *,
        after_event_id: int = 0,
        run_id: UUID | None = None,
        limit: int = 200,
    ) -> EventPage:
        del limit
        full = self.delegate.events(run_id=run_id)
        selected = tuple(item for item in full.events if item.event_id > after_event_id)[:1]
        last_event_id = selected[-1].event_id if selected else after_event_id
        return EventPage(
            events=selected,
            last_event_id=last_event_id,
            has_more=last_event_id < full.last_event_id,
        )

    def invocation(self, run_id: UUID) -> Invocation | None:
        return self.delegate.invocation(run_id)

    def run_state(self, run_id: UUID) -> RunState | None:
        return self.delegate.run_state(run_id)


def _source(
    tmp_path: Path,
    *,
    status: RunStatus = RunStatus.SUCCEEDED,
    replay_output: bool = False,
    replay_media: bool = True,
    compile_input: bool = False,
) -> tuple[_RunEvidenceSource, ArtifactRef]:
    artifact_store = LocalArtifactStore(tmp_path / "source-cas")
    report = QualificationReportV1(
        schema_version="harness.skill_qualification_report.v1",
        skill_ref=SKILL_REF,
        status="pass",
        deterministic_case_id="portable-run",
        regression_command="pytest -q tests/self_improving/harness/test_run_receipts.py",
        implementation_sha256="a" * 64,
        scene_gen_tree_sha256="b" * 64,
        ledger_contract_tree_sha256="c" * 64,
        checks=(QualificationCheckV1(name="run", status="pass", evidence={"runs": 1}),),
    )
    report_payload = _canonical(report.model_dump(mode="json"))
    report_ref = _put_bytes(
        artifact_store,
        tmp_path,
        report_payload,
        name="qualification_report",
        schema_version="harness.skill_qualification_report.v1",
    )
    qualification = SkillQualification(
        skill_ref=SKILL_REF,
        status="pass",
        deterministic_case_id="portable-run",
        regression_command="pytest -q tests/self_improving/harness/test_run_receipts.py",
        report_sha256=report_ref.sha256,
    )
    qualification_ref = _put_bytes(
        artifact_store,
        tmp_path,
        _canonical(qualification.model_dump(mode="json")),
        name="qualification",
        schema_version="harness.skill_qualification.v1",
    )
    descriptor = SkillDescriptor(
        skill_id=SKILL_ID,
        version="1.0.0",
        mcp_tool_name="fixture_portable_v1_0_0",
        input_schema=(
            "harness.text2env_compile_input.v1" if compile_input else "harness.artifact_ref.v1"
        ),
        output_schema=(
            "harness.text2env_replay_output.v1" if replay_output else "harness.artifact_ref.v1"
        ),
        implementation_name="tests.fixture.portable",
        implementation_version="1",
        implementation_sha256=report.implementation_sha256,
        deterministic=True,
        max_attempts=1,
        qualification_artifact=qualification_ref,
    )

    evidence_ref = _put_bytes(
        artifact_store,
        tmp_path,
        _canonical({"schema_version": "fixture.evidence.v1", "value": 7}),
        name="runtime_evidence",
        schema_version=(
            "robotwin.scene_runtime_evidence.v2" if replay_output else "fixture.evidence.v1"
        ),
    )
    request_ref = _put_bytes(
        artifact_store,
        tmp_path,
        _canonical({"schema_version": "fixture.request.v1", "value": 3}),
        name="request",
        schema_version=("robotwin.asset_catalog.v1" if compile_input else "fixture.request.v1"),
    )
    database = tmp_path / "source.sqlite3"
    journal = SQLiteEventJournal(database)
    run_store = SQLiteRunStore(database)
    from self_improving.harness.registry import _invocation_digest

    effective_parameters: ArtifactRef | Text2EnvCompileInput = request_ref
    if compile_input:
        effective_parameters = Text2EnvCompileInput(
            request="put the can on the plate",
            seed=7,
            asset_catalog=request_ref,
            config=CompileConfig(generate_missing_assets=False),
        )
    invocation_digest = _invocation_digest(
        skill_id=SKILL_ID,
        skill_version="1.0.0",
        effective_parameters=effective_parameters,
        dependencies=(),
        max_attempts=1,
    )
    invocation = Invocation(
        run_id=RUN_ID,
        skill_id=SKILL_ID,
        skill_version="1.0.0",
        effective_parameters=effective_parameters.model_dump(mode="json"),
        dependencies=(),
        max_attempts=1,
        invocation_digest=invocation_digest,
    )
    run_store.put_invocation(invocation)
    clock_values = iter((START, START + timedelta(seconds=1)))
    recorder = RunRecorder(
        run_id=RUN_ID,
        skill_id=SKILL_ID,
        skill_version="1.0.0",
        clock=lambda: next(clock_values),
        sink=journal,
    )
    recorder.start(stage="preflight", attempt=1)
    blocker = None
    run_artifacts = (evidence_ref,)
    output: dict[str, object] | None = evidence_ref.model_dump(mode="json")
    if replay_output:
        replay_artifacts: tuple[ArtifactRef, ...] = ()
        if replay_media:
            media_ref = _put_bytes(
                artifact_store,
                tmp_path,
                b"fixture-media",
                name="observer_video",
                schema_version="fixture.video.v1",
            )
            replay_artifacts = (media_ref,)
            run_artifacts = (evidence_ref, media_ref)
        output = Text2EnvReplayOutput(
            runtime_evidence=evidence_ref,
            replay_artifacts=replay_artifacts,
        ).model_dump(mode="json")
    if status is not RunStatus.SUCCEEDED:
        blocker = Blocker(
            code="T2E_REPLAY_FAILED",
            message="fixture runtime blocked",
            stage="runtime",
            retryable=False,
            details={"reason": "fixture"},
            unknowns=(),
            artifact_refs=run_artifacts,
        )
        output = None
    recorder.finish(
        status=status,
        stage="complete",
        artifact_refs=run_artifacts,
    )
    state = recorder.build_state(
        invocation_digest=invocation.invocation_digest,
        max_attempts=1,
        artifacts=run_artifacts,
        output=output,
        blocker=blocker,
    )
    run_store.put_run_state(state)
    return (
        _RunEvidenceSource(
            artifact_root=artifact_store.root,
            descriptor=descriptor,
            journal=journal,
            store=run_store,
        ),
        evidence_ref,
    )


def _replace_run_state(source: _RunEvidenceSource, state: RunState) -> None:
    payload = _canonical(state.model_dump(mode="json"))
    with closing(sqlite3.connect(source.store.path)) as connection:
        connection.execute(
            """
            UPDATE harness_run_states
            SET payload_json = ?, payload_sha256 = ?
            WHERE run_id = ?
            """,
            (payload, hashlib.sha256(payload).hexdigest(), str(state.run_id)),
        )
        connection.commit()


def _replace_invocation(source: _RunEvidenceSource, invocation: Invocation) -> None:
    payload = _canonical(invocation.model_dump(mode="json"))
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    with closing(sqlite3.connect(source.store.path)) as connection:
        connection.execute(
            """
            UPDATE harness_invocations
            SET payload_json = ?, payload_sha256 = ?
            WHERE run_id = ?
            """,
            (payload, payload_sha256, str(invocation.run_id)),
        )
        connection.execute(
            """
            UPDATE harness_run_states
            SET invocation_payload_sha256 = ?
            WHERE run_id = ?
            """,
            (payload_sha256, str(invocation.run_id)),
        )
        connection.commit()


def test_publisher_creates_path_free_portable_run_receipt_from_durable_authorities(
    tmp_path: Path,
) -> None:
    source, evidence_ref = _source(tmp_path)
    destination = LocalArtifactStore(tmp_path / "portable-cas")

    receipt_ref = PortableRunReceiptPublisher(destination).publish(source, RUN_ID)

    payload = destination.resolve(receipt_ref).path.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == receipt_ref.sha256
    receipt = PortableRunReceipt.model_validate_json(payload)
    assert receipt.schema_version == "harness.portable_run_receipt.v1"
    assert receipt.run_id == RUN_ID
    assert receipt.skill_ref == SKILL_REF
    assert receipt.status == RunStatus.SUCCEEDED
    invocation = source.invocation(RUN_ID)
    assert invocation is not None
    request_ref = ArtifactRef.model_validate(invocation.effective_parameters)
    assert receipt.invocation_digest == invocation.invocation_digest
    assert receipt.descriptor == source.descriptor
    assert receipt.input_artifacts == (request_ref,)
    assert receipt.artifacts == (evidence_ref,)
    assert {item.sha256 for item in receipt.closure} == {
        receipt.qualification.sha256,
        receipt.qualification_report.sha256,
        receipt.invocation.sha256,
        receipt.run_state.sha256,
        receipt.event_transcript.sha256,
        request_ref.sha256,
        evidence_ref.sha256,
    }
    assert all(item.uri == f"artifact://sha256/{item.sha256}" for item in receipt.closure)
    for item in receipt.closure:
        destination.resolve(item)

    loaded = load_portable_run_receipt(destination, receipt_ref)
    assert loaded.receipt == receipt
    assert loaded.invocation == invocation
    assert loaded.run_state == source.run_state(RUN_ID)
    assert loaded.qualification.skill_ref == SKILL_REF
    assert loaded.qualification_report.skill_ref == SKILL_REF
    transcript_events = tuple(item.envelope.event for item in loaded.event_transcript.events)
    assert transcript_events == loaded.run_state.events


def test_publisher_recomputes_invocation_identity_from_typed_parameters(
    tmp_path: Path,
) -> None:
    source, evidence_ref = _source(tmp_path)
    invocation = source.invocation(RUN_ID)
    assert invocation is not None
    changed_ref = evidence_ref.model_copy(
        update={
            "uri": f"artifact://sha256/{'e' * 64}",
            "sha256": "e" * 64,
        }
    )
    changed = invocation.model_copy(
        update={"effective_parameters": changed_ref.model_dump(mode="json")}
    )
    _replace_invocation(source, changed)

    with pytest.raises(PortableRunReceiptError) as captured:
        PortableRunReceiptPublisher(LocalArtifactStore(tmp_path / "portable-cas")).publish(
            source,
            RUN_ID,
        )

    assert captured.value.reason == "invocation_identity_mismatch"


def test_publisher_preserves_invoked_blocker_evidence(tmp_path: Path) -> None:
    source, evidence_ref = _source(tmp_path, status=RunStatus.BLOCKED)
    destination = LocalArtifactStore(tmp_path / "portable-cas")

    receipt_ref = PortableRunReceiptPublisher(destination).publish(source, RUN_ID)
    loaded = load_portable_run_receipt(destination, receipt_ref)

    assert loaded.receipt.status is RunStatus.BLOCKED
    assert loaded.run_state.blocker is not None
    assert loaded.run_state.blocker.artifact_refs == (evidence_ref,)
    assert loaded.receipt.artifacts == (evidence_ref,)


@pytest.mark.parametrize("replay_media", [False, True])
def test_publisher_collects_nested_and_tuple_output_artifacts(
    tmp_path: Path,
    replay_media: bool,
) -> None:
    source, evidence_ref = _source(
        tmp_path,
        replay_output=True,
        replay_media=replay_media,
    )
    destination = LocalArtifactStore(tmp_path / "portable-cas")

    receipt_ref = PortableRunReceiptPublisher(destination).publish(source, RUN_ID)
    loaded = load_portable_run_receipt(destination, receipt_ref)

    assert len(loaded.receipt.artifacts) == (2 if replay_media else 1)
    assert evidence_ref in loaded.receipt.artifacts
    assert (
        Text2EnvReplayOutput.model_validate(loaded.run_state.output).runtime_evidence
        == evidence_ref
    )


def test_publisher_collects_nested_compile_input_artifact(tmp_path: Path) -> None:
    source, _ = _source(tmp_path, compile_input=True)
    destination = LocalArtifactStore(tmp_path / "portable-cas")

    receipt_ref = PortableRunReceiptPublisher(destination).publish(source, RUN_ID)
    loaded = load_portable_run_receipt(destination, receipt_ref)
    typed_input = Text2EnvCompileInput.model_validate(loaded.invocation.effective_parameters)

    assert loaded.receipt.input_artifacts == (typed_input.asset_catalog,)


def test_publisher_accepts_qualified_source_documents_with_pretty_json(tmp_path: Path) -> None:
    source, _ = _source(tmp_path)
    source_store = LocalArtifactStore(source.artifact_root)
    qualification = SkillQualification.model_validate_json(
        source_store.resolve(source.descriptor.qualification_artifact).path.read_bytes()
    )
    report = QualificationReportV1.model_validate_json(
        source_store.resolve_digest(qualification.report_sha256).read_bytes()
    )
    pretty_report_ref = _put_bytes(
        source_store,
        tmp_path,
        json.dumps(report.model_dump(mode="json"), indent=2).encode(),
        name="pretty_qualification_report",
        schema_version="harness.skill_qualification_report.v1",
    )
    pretty_qualification = qualification.model_copy(
        update={"report_sha256": pretty_report_ref.sha256}
    )
    pretty_qualification_ref = _put_bytes(
        source_store,
        tmp_path,
        json.dumps(pretty_qualification.model_dump(mode="json"), indent=2).encode(),
        name="pretty_qualification",
        schema_version="harness.skill_qualification.v1",
    )
    pretty_source = _RunEvidenceSource(
        artifact_root=source.artifact_root,
        descriptor=source.descriptor.model_copy(
            update={"qualification_artifact": pretty_qualification_ref}
        ),
        journal=source.journal,
        store=source.store,
    )
    destination = LocalArtifactStore(tmp_path / "portable-cas")

    receipt_ref = PortableRunReceiptPublisher(destination).publish(pretty_source, RUN_ID)
    loaded = load_portable_run_receipt(destination, receipt_ref)

    assert loaded.qualification == pretty_qualification
    assert loaded.qualification_report == report


def test_publisher_allows_compile_input_ref_in_terminal_artifact_closure(
    tmp_path: Path,
) -> None:
    source, evidence_ref = _source(tmp_path, compile_input=True)
    invocation = source.invocation(RUN_ID)
    state = source.run_state(RUN_ID)
    assert invocation is not None
    assert state is not None
    input_ref = Text2EnvCompileInput.model_validate(invocation.effective_parameters).asset_catalog
    _replace_run_state(
        source,
        state.model_copy(update={"artifacts": (evidence_ref, input_ref)}),
    )
    destination = LocalArtifactStore(tmp_path / "portable-cas")

    receipt_ref = PortableRunReceiptPublisher(destination).publish(source, RUN_ID)
    loaded = load_portable_run_receipt(destination, receipt_ref)

    assert loaded.receipt.input_artifacts == (input_ref,)
    assert input_ref in loaded.receipt.artifacts


def test_publisher_binds_same_cas_input_under_output_facing_name(tmp_path: Path) -> None:
    source, evidence_ref = _source(tmp_path, compile_input=True)
    invocation = source.invocation(RUN_ID)
    state = source.run_state(RUN_ID)
    assert invocation is not None
    assert state is not None
    input_ref = Text2EnvCompileInput.model_validate(invocation.effective_parameters).asset_catalog
    effective_catalog_ref = input_ref.model_copy(update={"name": "effective_asset_catalog"})
    _replace_run_state(
        source,
        state.model_copy(update={"artifacts": (evidence_ref, effective_catalog_ref)}),
    )
    destination = LocalArtifactStore(tmp_path / "portable-cas")

    receipt_ref = PortableRunReceiptPublisher(destination).publish(source, RUN_ID)
    loaded = load_portable_run_receipt(destination, receipt_ref)

    assert effective_catalog_ref in loaded.receipt.artifacts
    assert input_ref in loaded.receipt.input_artifacts


def test_publisher_rejects_non_cas_input_locator(tmp_path: Path) -> None:
    source, _ = _source(tmp_path)
    invocation = source.invocation(RUN_ID)
    assert invocation is not None
    request_ref = ArtifactRef.model_validate(invocation.effective_parameters)
    request_path = LocalArtifactStore(source.artifact_root).resolve(request_ref).path
    file_ref = request_ref.model_copy(update={"uri": request_path.as_uri()})
    _replace_invocation(
        source,
        invocation.model_copy(update={"effective_parameters": file_ref.model_dump(mode="json")}),
    )

    with pytest.raises(PortableRunReceiptError) as captured:
        PortableRunReceiptPublisher(LocalArtifactStore(tmp_path / "portable-cas")).publish(
            source,
            RUN_ID,
        )

    assert captured.value.reason == "artifact_locator_invalid"


def test_publisher_rejects_output_artifacts_missing_from_terminal_closure(
    tmp_path: Path,
) -> None:
    source, evidence_ref = _source(tmp_path)
    state = source.run_state(RUN_ID)
    assert state is not None
    unbound_ref = evidence_ref.model_copy(
        update={
            "uri": f"artifact://sha256/{'f' * 64}",
            "sha256": "f" * 64,
        }
    )
    _replace_run_state(
        source,
        state.model_copy(update={"output": unbound_ref.model_dump(mode="json")}),
    )

    with pytest.raises(PortableRunReceiptError) as captured:
        PortableRunReceiptPublisher(LocalArtifactStore(tmp_path / "portable-cas")).publish(
            source,
            RUN_ID,
        )

    assert captured.value.reason == "run_artifact_closure_mismatch"


def test_publisher_rejects_nonmonotonic_event_cursors(tmp_path: Path) -> None:
    source, _ = _source(tmp_path)
    original = source.events(run_id=RUN_ID)
    assert len(original.events) == 2
    forged = EventPage(
        events=(
            StoredRunEvent(event_id=2, envelope=original.events[0].envelope),
            StoredRunEvent(event_id=1, envelope=original.events[1].envelope),
        ),
        last_event_id=2,
        has_more=False,
    )

    with pytest.raises(PortableRunReceiptError) as captured:
        PortableRunReceiptPublisher(LocalArtifactStore(tmp_path / "portable-cas")).publish(
            _EventOverrideSource(delegate=source, page=forged),
            RUN_ID,
        )

    assert captured.value.reason == "event_transcript_invalid"


def test_event_transcript_schema_rejects_duplicate_global_ids(tmp_path: Path) -> None:
    source, _ = _source(tmp_path)
    destination = LocalArtifactStore(tmp_path / "portable-cas")
    receipt_ref = PortableRunReceiptPublisher(destination).publish(source, RUN_ID)
    transcript = load_portable_run_receipt(destination, receipt_ref).event_transcript
    payload = transcript.model_dump(mode="json")
    payload["events"][1]["event_id"] = payload["events"][0]["event_id"]

    with pytest.raises(ValidationError, match="positive and strictly increasing"):
        RunEventTranscript.model_validate(payload)


def test_publisher_reads_the_complete_event_transcript_across_pages(tmp_path: Path) -> None:
    source, _ = _source(tmp_path)
    destination = LocalArtifactStore(tmp_path / "portable-cas")

    receipt_ref = PortableRunReceiptPublisher(destination).publish(
        _PaginatedSource(source),
        RUN_ID,
    )
    loaded = load_portable_run_receipt(destination, receipt_ref)

    assert [item.event_id for item in loaded.event_transcript.events] == [1, 2]


def test_receipt_rejects_duplicate_content_under_conflicting_metadata(tmp_path: Path) -> None:
    source, _ = _source(tmp_path)
    destination = LocalArtifactStore(tmp_path / "portable-cas")
    receipt_ref = PortableRunReceiptPublisher(destination).publish(source, RUN_ID)
    receipt_payload = destination.resolve(receipt_ref).path.read_bytes()
    receipt = PortableRunReceipt.model_validate_json(receipt_payload)
    aliased = receipt.closure[0].model_copy(update={"name": "conflicting_alias"})
    payload = receipt.model_dump(mode="json")
    closure = (*receipt.closure, aliased)
    payload["closure"] = [
        item.model_dump(mode="json")
        for item in sorted(
            closure,
            key=lambda item: (
                item.sha256,
                item.media_type,
                item.schema_version or "",
                item.name,
            ),
        )
    ]

    with pytest.raises(ValidationError, match="sorted and unique"):
        PortableRunReceipt.model_validate(payload)

    artifact_alias = receipt.artifacts[0].model_copy(update={"name": "artifact_alias"})
    payload = receipt.model_dump(mode="json")
    payload["artifacts"] = [
        receipt.artifacts[0].model_dump(mode="json"),
        artifact_alias.model_dump(mode="json"),
    ]
    with pytest.raises(ValidationError, match="artifact summaries must be sorted and unique"):
        PortableRunReceipt.model_validate(payload)

    payload["artifacts"][1]["name"] = "zzzz_artifact_alias"
    with pytest.raises(ValidationError, match="artifact summaries must be sorted and unique"):
        PortableRunReceipt.model_validate(payload)

    payload = receipt.model_dump(mode="json")
    payload["closure"] = payload["closure"][:-1]
    with pytest.raises(ValidationError, match="incomplete or contains extras"):
        PortableRunReceipt.model_validate(payload)


def test_loader_rejects_relabelled_named_document(tmp_path: Path) -> None:
    source, _ = _source(tmp_path)
    destination = LocalArtifactStore(tmp_path / "portable-cas")
    receipt_ref = PortableRunReceiptPublisher(destination).publish(source, RUN_ID)
    receipt_payload = destination.resolve(receipt_ref).path.read_bytes()
    receipt = PortableRunReceipt.model_validate_json(receipt_payload)
    forged = receipt.model_copy(
        update={
            "invocation": receipt.invocation.model_copy(
                update={"schema_version": "attacker.invocation.v1"}
            )
        }
    )
    forged_ref = _put_bytes(
        destination,
        tmp_path,
        _canonical(forged.model_dump(mode="json")),
        name="portable_run_receipt",
        schema_version="harness.portable_run_receipt.v1",
    )

    with pytest.raises(PortableRunReceiptError) as captured:
        load_portable_run_receipt(destination, forged_ref)

    assert captured.value.reason == "artifact_schema_mismatch"


def test_loader_rehashes_bytes_read_after_cas_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _ = _source(tmp_path)
    destination = LocalArtifactStore(tmp_path / "portable-cas")
    receipt_ref = PortableRunReceiptPublisher(destination).publish(source, RUN_ID)
    receipt_path = destination.resolve(receipt_ref).path
    receipt = PortableRunReceipt.model_validate_json(receipt_path.read_bytes())
    forged = receipt.model_copy(
        update={
            "descriptor": receipt.descriptor.model_copy(
                update={"implementation_name": "attacker.fixture"}
            )
        }
    )
    forged_payload = _canonical(forged.model_dump(mode="json"))
    original_read_bytes = Path.read_bytes

    def drifted_read_bytes(path: Path) -> bytes:
        if path == receipt_path:
            return forged_payload
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", drifted_read_bytes)

    with pytest.raises(PortableRunReceiptError) as captured:
        load_portable_run_receipt(destination, receipt_ref)

    assert captured.value.reason == "artifact_unavailable"


@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        ("run_state", "run_state_unavailable"),
        ("invocation", "invocation_unavailable"),
    ],
)
def test_publisher_normalizes_durable_authority_read_failures(
    tmp_path: Path,
    fault: str,
    reason: str,
) -> None:
    source, _ = _source(tmp_path)

    with pytest.raises(PortableRunReceiptError) as captured:
        PortableRunReceiptPublisher(LocalArtifactStore(tmp_path / "portable-cas")).publish(
            _FaultingSource(delegate=source, fault=fault),
            RUN_ID,
        )

    assert captured.value.reason == reason


@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        ("run_missing", "run_missing"),
        ("run_state_invalid", "run_state_unavailable"),
        ("run_running", "run_not_terminal"),
        ("invocation_missing", "invocation_missing"),
        ("invocation_invalid", "invocation_unavailable"),
        ("skills", "descriptor_unavailable"),
        ("descriptor_missing", "descriptor_unavailable"),
        ("events", "event_transcript_unavailable"),
        ("events_invalid", "event_transcript_invalid"),
        ("events_empty_more", "event_transcript_invalid"),
        ("events_empty", "event_transcript_mismatch"),
        ("run_binding", "run_binding_mismatch"),
        ("output_invalid", "run_output_invalid"),
    ],
)
def test_publisher_fails_closed_on_incomplete_source_authority(
    tmp_path: Path,
    fault: str,
    reason: str,
) -> None:
    source, _ = _source(tmp_path)

    with pytest.raises(PortableRunReceiptError) as captured:
        PortableRunReceiptPublisher(LocalArtifactStore(tmp_path / "portable-cas")).publish(
            _FaultingSource(delegate=source, fault=fault),
            RUN_ID,
        )

    assert captured.value.reason == reason


def test_public_api_rejects_wrong_authority_types(tmp_path: Path) -> None:
    source, _ = _source(tmp_path)
    destination = LocalArtifactStore(tmp_path / "portable-cas")
    publisher = PortableRunReceiptPublisher(destination)

    with pytest.raises(TypeError, match="LocalArtifactStore"):
        PortableRunReceiptPublisher(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="UUID"):
        publisher.publish(source, "not-a-uuid")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="LocalArtifactStore"):
        load_portable_run_receipt(object(), source.descriptor.qualification_artifact)  # type: ignore[arg-type]


@pytest.mark.parametrize("root_kind", ["missing_attribute", "not_path", "missing", "file"])
def test_publisher_rejects_invalid_source_artifact_root(
    tmp_path: Path,
    root_kind: str,
) -> None:
    source, _ = _source(tmp_path)
    root: object = "not-a-path"
    raises = root_kind == "missing_attribute"
    if root_kind == "missing":
        root = tmp_path / "does-not-exist"
    elif root_kind == "file":
        root = tmp_path / "not-a-directory"
        Path(root).write_text("file", encoding="utf-8")

    with pytest.raises(PortableRunReceiptError) as captured:
        PortableRunReceiptPublisher(LocalArtifactStore(tmp_path / "portable-cas")).publish(
            _RootOverrideSource(delegate=source, root=root, raises=raises),  # type: ignore[arg-type]
            RUN_ID,
        )

    assert captured.value.reason == "source_invalid"


def test_publisher_rejects_parameters_that_do_not_parse_as_registered_input(
    tmp_path: Path,
) -> None:
    source, _ = _source(tmp_path)
    invocation = source.invocation(RUN_ID)
    assert invocation is not None
    _replace_invocation(source, invocation.model_copy(update={"effective_parameters": {}}))

    with pytest.raises(PortableRunReceiptError) as captured:
        PortableRunReceiptPublisher(LocalArtifactStore(tmp_path / "portable-cas")).publish(
            source,
            RUN_ID,
        )

    assert captured.value.reason == "invocation_parameters_invalid"


def test_publisher_rejects_qualification_bound_to_another_implementation(
    tmp_path: Path,
) -> None:
    source, _ = _source(tmp_path)
    forged_source = _RunEvidenceSource(
        artifact_root=source.artifact_root,
        descriptor=source.descriptor.model_copy(update={"implementation_sha256": "d" * 64}),
        journal=source.journal,
        store=source.store,
    )

    with pytest.raises(PortableRunReceiptError) as captured:
        PortableRunReceiptPublisher(LocalArtifactStore(tmp_path / "portable-cas")).publish(
            forged_source,
            RUN_ID,
        )

    assert captured.value.reason == "qualification_binding_mismatch"


@pytest.mark.parametrize("mutation", ["missing", "read_drift"])
def test_publisher_rejects_unavailable_qualification_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    source, _ = _source(tmp_path)
    store = LocalArtifactStore(source.artifact_root)
    qualification_payload = store.resolve(
        source.descriptor.qualification_artifact
    ).path.read_bytes()
    qualification = SkillQualification.model_validate_json(qualification_payload)
    report_path = store.resolve_digest(qualification.report_sha256)
    if mutation == "missing":
        report_path.unlink()
    else:
        original_read_bytes = Path.read_bytes

        def drifted_read_bytes(path: Path) -> bytes:
            if path == report_path:
                return b"{}"
            return original_read_bytes(path)

        monkeypatch.setattr(Path, "read_bytes", drifted_read_bytes)

    with pytest.raises(PortableRunReceiptError) as captured:
        PortableRunReceiptPublisher(LocalArtifactStore(tmp_path / "portable-cas")).publish(
            source,
            RUN_ID,
        )

    assert captured.value.reason == "qualification_report_unavailable"


def test_loader_rejects_wrong_receipt_ref_and_forged_summary(tmp_path: Path) -> None:
    source, _ = _source(tmp_path)
    destination = LocalArtifactStore(tmp_path / "portable-cas")
    receipt_ref = PortableRunReceiptPublisher(destination).publish(source, RUN_ID)

    with pytest.raises(PortableRunReceiptError) as captured:
        load_portable_run_receipt(
            destination,
            receipt_ref.model_copy(update={"schema_version": "attacker.receipt.v1"}),
        )
    assert captured.value.reason == "receipt_ref_invalid"

    receipt = load_portable_run_receipt(destination, receipt_ref).receipt
    forged = receipt.model_copy(update={"status": RunStatus.BLOCKED})
    forged_ref = _put_bytes(
        destination,
        tmp_path,
        _canonical(forged.model_dump(mode="json")),
        name="portable_run_receipt",
        schema_version="harness.portable_run_receipt.v1",
    )
    with pytest.raises(PortableRunReceiptError) as captured:
        load_portable_run_receipt(destination, forged_ref)
    assert captured.value.reason == "receipt_binding_mismatch"


def test_loader_rejects_qualification_refs_rebound_inside_receipt(tmp_path: Path) -> None:
    source, _ = _source(tmp_path)
    destination = LocalArtifactStore(tmp_path / "portable-cas")
    receipt_ref = PortableRunReceiptPublisher(destination).publish(source, RUN_ID)
    receipt = load_portable_run_receipt(destination, receipt_ref).receipt

    descriptor = receipt.descriptor.model_copy(
        update={
            "qualification_artifact": receipt.qualification.model_copy(
                update={"name": "rebound_qualification"}
            )
        }
    )
    forged = receipt.model_copy(update={"descriptor": descriptor})
    forged_ref = _put_bytes(
        destination,
        tmp_path,
        _canonical(forged.model_dump(mode="json")),
        name="portable_run_receipt",
        schema_version="harness.portable_run_receipt.v1",
    )
    with pytest.raises(PortableRunReceiptError) as captured:
        load_portable_run_receipt(destination, forged_ref)
    assert captured.value.reason == "qualification_binding_mismatch"

    bogus_report = QualificationReportV1.model_validate(
        {
            **load_portable_run_receipt(destination, receipt_ref).qualification_report.model_dump(
                mode="json"
            ),
            "regression_command": "pytest -q attacker",
        }
    )
    bogus_report_ref = _put_bytes(
        destination,
        tmp_path,
        _canonical(bogus_report.model_dump(mode="json")),
        name="qualification_report",
        schema_version="harness.skill_qualification_report.v1",
    )
    forged = receipt.model_copy(update={"qualification_report": bogus_report_ref})
    closure = tuple(
        item for item in receipt.closure if item.sha256 != receipt.qualification_report.sha256
    ) + (bogus_report_ref,)
    forged = forged.model_copy(
        update={
            "closure": tuple(
                sorted(
                    closure,
                    key=lambda item: (
                        item.sha256,
                        item.media_type,
                        item.schema_version or "",
                        item.name,
                    ),
                )
            )
        }
    )
    forged_ref = _put_bytes(
        destination,
        tmp_path,
        _canonical(forged.model_dump(mode="json")),
        name="portable_run_receipt",
        schema_version="harness.portable_run_receipt.v1",
    )
    with pytest.raises(PortableRunReceiptError) as captured:
        load_portable_run_receipt(destination, forged_ref)
    assert captured.value.reason == "qualification_binding_mismatch"


@pytest.mark.parametrize("payload_kind", ["invalid", "noncanonical"])
def test_loader_rejects_invalid_or_noncanonical_receipt_document(
    tmp_path: Path,
    payload_kind: str,
) -> None:
    source, _ = _source(tmp_path)
    destination = LocalArtifactStore(tmp_path / "portable-cas")
    receipt_ref = PortableRunReceiptPublisher(destination).publish(source, RUN_ID)
    receipt = load_portable_run_receipt(destination, receipt_ref).receipt
    if payload_kind == "invalid":
        payload = b"{}"
        reason = "document_invalid"
    else:
        payload = json.dumps(receipt.model_dump(mode="json"), ensure_ascii=False).encode()
        reason = "document_noncanonical"
    malformed_ref = _put_bytes(
        destination,
        tmp_path,
        payload,
        name="portable_run_receipt",
        schema_version="harness.portable_run_receipt.v1",
    )

    with pytest.raises(PortableRunReceiptError) as captured:
        load_portable_run_receipt(destination, malformed_ref)

    assert captured.value.reason == reason
