from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest

from self_improving.harness.event_journal import SQLiteEventJournal
from self_improving.harness.events import RunEvent
from self_improving.harness.run_store import (
    InvocationSink,
    RunStateSink,
    RunStore,
    RunStoreConflictError,
    RunStoreCorruptionError,
    SQLiteRunStore,
)
from self_improving.harness.schemas import (
    ArtifactRef,
    Blocker,
    DependencyRef,
    Event,
    Invocation,
    RunState,
    RunStatus,
)

RUN_ID = UUID("12345678-1234-4234-9234-123456789abc")
INVOCATION_DIGEST = "a" * 64
START = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)


def _invocation(
    *,
    run_id: UUID = RUN_ID,
    digest: str = INVOCATION_DIGEST,
    skill_id: str = "text2env.compile",
    max_attempts: int = 1,
) -> Invocation:
    return Invocation(
        run_id=run_id,
        skill_id=skill_id,
        skill_version="1.0.0",
        effective_parameters={"prompt": "把红色罐子放在盘子上"},
        dependencies=(DependencyRef(name="scene-gen", version="1", sha256="b" * 64),),
        max_attempts=max_attempts,
        invocation_digest=digest,
    )


def _terminal_state(
    *,
    run_id: UUID = RUN_ID,
    digest: str | None = INVOCATION_DIGEST,
    skill_id: str = "text2env.compile",
    max_attempts: int = 1,
) -> RunState:
    ended = START + timedelta(seconds=1)
    return RunState(
        run_id=run_id,
        invocation_digest=digest,
        skill_id=skill_id,
        skill_version="1.0.0",
        status=RunStatus.SUCCEEDED,
        attempt=1,
        max_attempts=max_attempts,
        started_at=START,
        ended_at=ended,
        events=(
            Event(
                seq=1,
                timestamp=START,
                stage="preflight",
                attempt=1,
                from_status=None,
                to_status=RunStatus.RUNNING,
                artifact_refs=(),
            ),
            Event(
                seq=2,
                timestamp=ended,
                stage="complete",
                attempt=1,
                from_status=RunStatus.RUNNING,
                to_status=RunStatus.SUCCEEDED,
                artifact_refs=(),
            ),
        ),
        artifacts=(),
        output={"result": "ok"},
        blocker=None,
    )


def _running_state() -> RunState:
    return RunState(
        run_id=RUN_ID,
        invocation_digest=INVOCATION_DIGEST,
        skill_id="text2env.compile",
        skill_version="1.0.0",
        status=RunStatus.RUNNING,
        attempt=1,
        max_attempts=1,
        started_at=START,
        ended_at=None,
        events=(
            Event(
                seq=1,
                timestamp=START,
                stage="preflight",
                attempt=1,
                from_status=None,
                to_status=RunStatus.RUNNING,
                artifact_refs=(),
            ),
        ),
        artifacts=(),
        output=None,
        blocker=None,
    )


def _preflight_state() -> RunState:
    ended = START + timedelta(seconds=1)
    return RunState(
        run_id=RUN_ID,
        invocation_digest=None,
        skill_id="text2env.missing",
        skill_version="1.0.0",
        status=RunStatus.BLOCKED,
        attempt=0,
        max_attempts=0,
        started_at=START,
        ended_at=ended,
        events=(
            Event(
                seq=1,
                timestamp=START,
                stage="preflight",
                attempt=0,
                from_status=None,
                to_status=RunStatus.RUNNING,
                artifact_refs=(),
            ),
            Event(
                seq=2,
                timestamp=ended,
                stage="preflight",
                attempt=0,
                from_status=RunStatus.RUNNING,
                to_status=RunStatus.BLOCKED,
                artifact_refs=(),
            ),
        ),
        artifacts=(),
        output=None,
        blocker=Blocker(
            code="HARN_SKILL_NOT_FOUND",
            message="Skill not found",
            stage="preflight",
            retryable=False,
            details={},
            unknowns=(),
            artifact_refs=(),
        ),
    )


def _publish_state(path: Path, state: RunState) -> SQLiteEventJournal:
    journal = SQLiteEventJournal(path)
    for event in state.events:
        journal.publish(
            RunEvent(
                run_id=state.run_id,
                skill_id=state.skill_id,
                skill_version=state.skill_version,
                event=event,
            )
        )
    return journal


def _canonical_envelope(state: RunState, event: Event) -> str:
    return json.dumps(
        {
            "run_id": str(state.run_id),
            "skill_id": state.skill_id,
            "skill_version": state.skill_version,
            "event": event.model_dump(mode="json"),
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def test_sqlite_run_store_recovers_records_alongside_event_journal(tmp_path: Path) -> None:
    path = tmp_path / "harness.sqlite3"
    store = SQLiteRunStore(path)
    invocation = _invocation()
    terminal = _terminal_state()

    store.put_invocation(invocation)
    journal = _publish_state(path, terminal)
    store.put_run_state(terminal)

    restarted = SQLiteRunStore(path)
    assert restarted.read_invocation(RUN_ID) == invocation
    assert restarted.read_run_state(RUN_ID) == terminal
    assert tuple(item.envelope.event for item in journal.read(run_id=RUN_ID).events) == (
        terminal.events
    )


def test_sqlite_run_store_is_idempotent_but_run_records_are_immutable(tmp_path: Path) -> None:
    path = tmp_path / "harness.sqlite3"
    store = SQLiteRunStore(path)
    invocation = _invocation()
    terminal = _terminal_state()

    store.put_invocation(invocation)
    store.put_invocation(invocation)
    _publish_state(path, terminal)
    store.put_run_state(terminal)
    store.put_run_state(terminal)

    assert store.read_invocation(RUN_ID) == invocation
    assert store.read_run_state(RUN_ID) == terminal
    with pytest.raises(RunStoreConflictError, match="different invocation"):
        store.put_invocation(_invocation(digest="c" * 64))
    with pytest.raises(RunStoreConflictError, match="different terminal state"):
        store.put_run_state(terminal.model_copy(update={"output": {"result": "changed"}}))


def test_invocation_must_be_persisted_before_the_first_run_event(tmp_path: Path) -> None:
    path = tmp_path / "harness.sqlite3"
    store = SQLiteRunStore(path)
    invocation = _invocation()
    terminal = _terminal_state()
    journal = SQLiteEventJournal(path)
    journal.publish(
        RunEvent(
            run_id=RUN_ID,
            skill_id=invocation.skill_id,
            skill_version=invocation.skill_version,
            event=terminal.events[0],
        )
    )

    with pytest.raises(RunStoreConflictError, match="before the first event"):
        store.put_invocation(invocation)
    assert store.read_invocation(RUN_ID) is None


def test_run_store_accepts_only_terminal_states_bound_to_their_invocation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "harness.sqlite3"
    store = SQLiteRunStore(path)

    with pytest.raises(RunStoreConflictError, match="terminal"):
        store.put_run_state(_running_state())
    with pytest.raises(RunStoreConflictError, match="requires a persisted invocation"):
        store.put_run_state(_terminal_state())

    invocation = _invocation()
    store.put_invocation(invocation)
    _publish_state(path, _terminal_state())
    mismatches = (
        (_terminal_state(digest="c" * 64), "invocation_digest"),
        (_terminal_state(skill_id="text2env.replay"), "skill_id"),
        (
            _terminal_state().model_copy(update={"skill_version": "2.0.0"}),
            "skill_version",
        ),
        (_terminal_state(max_attempts=2), "max_attempts"),
    )
    for state, field in mismatches:
        with pytest.raises(RunStoreConflictError, match=field):
            store.put_run_state(state)

    store.put_run_state(_terminal_state())
    assert store.read_run_state(RUN_ID) == _terminal_state()


def test_preflight_terminal_state_is_stored_without_an_invocation(tmp_path: Path) -> None:
    path = tmp_path / "harness.sqlite3"
    store = SQLiteRunStore(path)
    state = _preflight_state()

    _publish_state(path, state)
    store.put_run_state(state)

    assert store.read_invocation(RUN_ID) is None
    assert store.read_run_state(RUN_ID) == state


def test_run_store_keeps_canonical_utf8_bytes_and_detects_database_tampering(
    tmp_path: Path,
) -> None:
    path = tmp_path / "harness.sqlite3"
    store = SQLiteRunStore(path)
    store.put_invocation(_invocation())
    expected = (
        '{"dependencies":[{"name":"scene-gen","sha256":"'
        + "b" * 64
        + '","version":"1"}],"effective_parameters":{"prompt":"把红色罐子放在盘子上"},'
        + '"invocation_digest":"'
        + "a" * 64
        + '","max_attempts":1,"run_id":"12345678-1234-4234-9234-123456789abc",'
        + '"skill_id":"text2env.compile","skill_version":"1.0.0"}'
    ).encode()
    with closing(sqlite3.connect(path)) as connection:
        stored = connection.execute(
            "SELECT typeof(payload_json), payload_json FROM harness_invocations"
        ).fetchone()
    assert stored == ("blob", expected)

    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "UPDATE harness_invocations SET payload_json = ? WHERE run_id = ?",
            (b'{"broken":true}', str(RUN_ID)),
        )
    with pytest.raises(RunStoreCorruptionError, match="invocation.*12345678"):
        store.read_invocation(RUN_ID)

    state_path = tmp_path / "state.sqlite3"
    state_store = SQLiteRunStore(state_path)
    state_store.put_invocation(_invocation())
    _publish_state(state_path, _terminal_state())
    state_store.put_run_state(_terminal_state())
    with closing(sqlite3.connect(state_path)) as connection, connection:
        connection.execute(
            "UPDATE harness_run_states SET payload_json = CAST(payload_json AS TEXT)"
        )
    with pytest.raises(RunStoreCorruptionError, match="terminal state.*canonical JSON bytes"):
        state_store.read_run_state(RUN_ID)


def test_run_store_serializes_concurrent_idempotent_and_conflicting_writes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "harness.sqlite3"
    stores = (SQLiteRunStore(path), SQLiteRunStore(path))
    invocation = _invocation()
    terminal = _terminal_state()

    with ThreadPoolExecutor(max_workers=8) as executor:
        invocation_results = list(
            executor.map(lambda index: stores[index % 2].put_invocation(invocation), range(8))
        )
        _publish_state(path, terminal)
        state_results = list(
            executor.map(lambda index: stores[index % 2].put_run_state(terminal), range(8))
        )
    assert invocation_results == [None] * 8
    assert state_results == [None] * 8

    conflict_path = tmp_path / "conflict.sqlite3"
    conflicting_stores = (SQLiteRunStore(conflict_path), SQLiteRunStore(conflict_path))

    def attempt(index: int) -> str:
        try:
            conflicting_stores[index].put_invocation(
                _invocation(digest=("a" if index == 0 else "c") * 64)
            )
        except RunStoreConflictError:
            return "conflict"
        return "stored"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, range(2)))
    assert sorted(outcomes) == ["conflict", "stored"]


def test_sqlite_adapter_satisfies_the_run_store_protocol_seams(tmp_path: Path) -> None:
    path = tmp_path / "harness.sqlite3"
    store = SQLiteRunStore(path)

    assert isinstance(store, InvocationSink)
    assert isinstance(store, RunStateSink)
    assert isinstance(store, RunStore)


def test_run_store_validates_configuration_empty_reads_and_preflight_binding(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="busy_timeout_ms"):
        SQLiteRunStore(tmp_path / "invalid.sqlite3", busy_timeout_ms=0)

    path = tmp_path / "harness.sqlite3"
    store = SQLiteRunStore(path)
    assert store.read_run_state(RUN_ID) is None
    store.put_invocation(_invocation())
    _publish_state(path, _preflight_state())
    with pytest.raises(RunStoreConflictError, match="preflight terminal state"):
        store.put_run_state(_preflight_state())


def test_run_store_detects_malformed_noncanonical_and_metadata_corruption(
    tmp_path: Path,
) -> None:
    malformed_path = tmp_path / "malformed.sqlite3"
    malformed = SQLiteRunStore(malformed_path)
    malformed.put_invocation(_invocation())
    malformed_bytes = b"{"
    with closing(sqlite3.connect(malformed_path)) as connection, connection:
        connection.execute(
            "UPDATE harness_invocations SET payload_json = ?, payload_sha256 = ?",
            (malformed_bytes, sha256(malformed_bytes).hexdigest()),
        )
    with pytest.raises(RunStoreCorruptionError, match="cannot reconstruct"):
        malformed.read_invocation(RUN_ID)

    noncanonical_path = tmp_path / "noncanonical.sqlite3"
    noncanonical = SQLiteRunStore(noncanonical_path)
    noncanonical.put_invocation(_invocation())
    with closing(sqlite3.connect(noncanonical_path)) as connection, connection:
        original = connection.execute("SELECT payload_json FROM harness_invocations").fetchone()[0]
        changed = original.replace(b'"dependencies":', b'"dependencies" :', 1)
        connection.execute(
            "UPDATE harness_invocations SET payload_json = ?, payload_sha256 = ?",
            (changed, sha256(changed).hexdigest()),
        )
    with pytest.raises(RunStoreCorruptionError, match="canonical JSON bytes"):
        noncanonical.read_invocation(RUN_ID)

    metadata_path = tmp_path / "metadata.sqlite3"
    metadata = SQLiteRunStore(metadata_path)
    metadata.put_invocation(_invocation())
    with closing(sqlite3.connect(metadata_path)) as connection, connection:
        connection.execute("UPDATE harness_invocations SET skill_id = 'text2env.replay'")
    with pytest.raises(RunStoreCorruptionError, match="inconsistent skill_id metadata"):
        metadata.read_invocation(RUN_ID)


def test_terminal_state_requires_the_complete_canonical_event_journal(tmp_path: Path) -> None:
    state = _terminal_state()

    absent_path = tmp_path / "absent.sqlite3"
    absent = SQLiteRunStore(absent_path)
    absent.put_invocation(_invocation())
    with pytest.raises(RunStoreConflictError, match="journal table is missing"):
        absent.put_run_state(state)

    missing_tail_path = tmp_path / "missing-tail.sqlite3"
    missing_tail = SQLiteRunStore(missing_tail_path)
    missing_tail.put_invocation(_invocation())
    journal = SQLiteEventJournal(missing_tail_path)
    journal.publish(
        RunEvent(
            run_id=RUN_ID,
            skill_id=state.skill_id,
            skill_version=state.skill_version,
            event=state.events[0],
        )
    )
    with pytest.raises(RunStoreConflictError, match="has 1 events.*declares 2"):
        missing_tail.put_run_state(state)

    wrong_path = tmp_path / "wrong-content.sqlite3"
    wrong = SQLiteRunStore(wrong_path)
    wrong.put_invocation(_invocation())
    wrong_journal = SQLiteEventJournal(wrong_path)
    extra_artifact = ArtifactRef(
        name="unexpected",
        uri="artifact://sha256/" + "d" * 64,
        media_type="application/json",
        sha256="d" * 64,
        bytes=1,
        schema_version=None,
    )
    wrong_events = (
        state.events[0],
        state.events[1].model_copy(update={"artifact_refs": (extra_artifact,)}),
    )
    for event in wrong_events:
        wrong_journal.publish(
            RunEvent(
                run_id=RUN_ID,
                skill_id=state.skill_id,
                skill_version=state.skill_version,
                event=event,
            )
        )
    with pytest.raises(RunStoreConflictError, match="event 2.*canonical"):
        wrong.put_run_state(state)

    wrong_skill_path = tmp_path / "wrong-skill.sqlite3"
    wrong_skill = SQLiteRunStore(wrong_skill_path)
    wrong_skill.put_invocation(_invocation())
    wrong_skill_journal = SQLiteEventJournal(wrong_skill_path)
    for event in state.events:
        wrong_skill_journal.publish(
            RunEvent(
                run_id=RUN_ID,
                skill_id="text2env.replay",
                skill_version=state.skill_version,
                event=event,
            )
        )
    with pytest.raises(RunStoreConflictError, match="event 1.*canonical"):
        wrong_skill.put_run_state(state)


def test_read_run_state_detects_event_and_invocation_cross_table_tampering(
    tmp_path: Path,
) -> None:
    state = _terminal_state()

    event_path = tmp_path / "event.sqlite3"
    event_store = SQLiteRunStore(event_path)
    event_store.put_invocation(_invocation())
    _publish_state(event_path, state)
    event_store.put_run_state(state)
    changed_event = state.events[1].model_copy(update={"stage": "tampered"})
    with closing(sqlite3.connect(event_path)) as connection, connection:
        connection.execute(
            "UPDATE run_events SET stage = ?, envelope_json = ? WHERE run_id = ? AND seq = 2",
            (
                changed_event.stage,
                _canonical_envelope(state, changed_event),
                str(RUN_ID),
            ),
        )
    with pytest.raises(RunStoreCorruptionError, match="event 2.*canonical"):
        event_store.read_run_state(RUN_ID)

    metadata_path = tmp_path / "event-metadata.sqlite3"
    metadata_store = SQLiteRunStore(metadata_path)
    metadata_store.put_invocation(_invocation())
    _publish_state(metadata_path, state)
    metadata_store.put_run_state(state)
    with closing(sqlite3.connect(metadata_path)) as connection, connection:
        connection.execute(
            "UPDATE run_events SET stage = 'tampered-index' WHERE run_id = ? AND seq = 2",
            (str(RUN_ID),),
        )
    with pytest.raises(RunStoreCorruptionError, match="event 2.*metadata"):
        metadata_store.read_run_state(RUN_ID)

    invocation_path = tmp_path / "invocation.sqlite3"
    invocation_store = SQLiteRunStore(invocation_path)
    invocation_store.put_invocation(_invocation())
    _publish_state(invocation_path, state)
    invocation_store.put_run_state(state)
    changed_invocation = _invocation().model_copy(
        update={"effective_parameters": {"prompt": "tampered after terminal write"}}
    )
    changed_payload = json.dumps(
        changed_invocation.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    with closing(sqlite3.connect(invocation_path)) as connection, connection:
        connection.execute(
            """
            UPDATE harness_invocations
            SET payload_json = ?, payload_sha256 = ?
            WHERE run_id = ?
            """,
            (
                changed_payload,
                sha256(changed_payload).hexdigest(),
                str(RUN_ID),
            ),
        )
    with pytest.raises(RunStoreCorruptionError, match="invocation payload"):
        invocation_store.read_run_state(RUN_ID)

    binding_path = tmp_path / "binding.sqlite3"
    binding_store = SQLiteRunStore(binding_path)
    binding_store.put_invocation(_invocation())
    _publish_state(binding_path, state)
    binding_store.put_run_state(state)
    changed_binding = _invocation(digest="c" * 64)
    changed_binding_payload = json.dumps(
        changed_binding.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    with closing(sqlite3.connect(binding_path)) as connection, connection:
        connection.execute(
            """
            UPDATE harness_invocations
            SET invocation_digest = ?, payload_json = ?, payload_sha256 = ?
            WHERE run_id = ?
            """,
            (
                changed_binding.invocation_digest,
                changed_binding_payload,
                sha256(changed_binding_payload).hexdigest(),
                str(RUN_ID),
            ),
        )
    with pytest.raises(RunStoreCorruptionError, match="binding.*invocation_digest"):
        binding_store.read_run_state(RUN_ID)
