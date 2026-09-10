from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from threading import Barrier
from uuid import UUID

import pytest
from pydantic import ValidationError

import self_improving.harness.run_store as run_store_module
from self_improving.harness.event_journal import SQLiteEventJournal
from self_improving.harness.events import RunEvent
from self_improving.harness.run_store import (
    ControlledRunIntent,
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
    UnknownField,
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


def _preflight_state(*, skill_id: str = "text2env.missing") -> RunState:
    ended = START + timedelta(seconds=1)
    return RunState(
        run_id=RUN_ID,
        invocation_digest=None,
        skill_id=skill_id,
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


def _controlled_intent(
    *,
    raw_parameters_sha256: str = "d" * 64,
    descriptor_sha256: str = "e" * 64,
    phase: str = "input_validation",
    effective_parameters_sha256: str | None = None,
    dependencies: tuple[DependencyRef, ...] | None = None,
) -> ControlledRunIntent:
    return ControlledRunIntent(
        run_id=RUN_ID,
        skill_id="text2env.compile",
        skill_version="1.0.0",
        input_schema="harness.artifact_ref.v1",
        phase=phase,
        raw_parameters_sha256=raw_parameters_sha256,
        effective_parameters_sha256=effective_parameters_sha256,
        dependencies=dependencies,
        descriptor_sha256=descriptor_sha256,
        implementation_sha256="f" * 64,
        qualification_sha256="1" * 64,
        max_attempts=1,
    )


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"effective_parameters_sha256": "2" * 64}, "input_validation"),
        ({"phase": "preflight"}, "effective_parameters"),
        (
            {
                "phase": "preflight",
                "effective_parameters_sha256": "2" * 64,
                "dependencies": (),
            },
            "preflight",
        ),
        (
            {"phase": "validated_input", "effective_parameters_sha256": "2" * 64},
            "dependencies",
        ),
        (
            {
                "phase": "validated_input",
                "effective_parameters_sha256": "2" * 64,
                "dependencies": (
                    DependencyRef(name="z", version="1", sha256="2" * 64),
                    DependencyRef(name="a", version="1", sha256="3" * 64),
                ),
            },
            "sorted and unique",
        ),
        (
            {
                "phase": "validated_input",
                "effective_parameters_sha256": "2" * 64,
                "dependencies": (
                    DependencyRef(name="same", version="1", sha256="2" * 64),
                    DependencyRef(name="same", version="2", sha256="3" * 64),
                ),
            },
            "sorted and unique",
        ),
    ],
)
def test_controlled_intent_phase_is_a_complete_single_immutable_snapshot(
    changes: dict[str, object],
    message: str,
) -> None:
    payload = _controlled_intent().model_dump(mode="python")
    payload.update(changes)

    with pytest.raises(ValidationError, match=message):
        ControlledRunIntent.model_validate(payload)


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


def _create_legacy_run_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE harness_invocations (
            run_id TEXT PRIMARY KEY,
            invocation_digest TEXT NOT NULL,
            skill_id TEXT NOT NULL,
            skill_version TEXT NOT NULL,
            max_attempts INTEGER NOT NULL,
            payload_json BLOB NOT NULL,
            payload_sha256 TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE harness_run_states (
            run_id TEXT PRIMARY KEY,
            invocation_digest TEXT,
            skill_id TEXT NOT NULL,
            skill_version TEXT NOT NULL,
            max_attempts INTEGER NOT NULL,
            status TEXT NOT NULL,
            invocation_payload_sha256 TEXT,
            payload_json BLOB NOT NULL,
            payload_sha256 TEXT NOT NULL
        )
        """
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


def test_controlled_intent_is_atomic_immutable_and_hash_anchors_its_terminal(
    tmp_path: Path,
) -> None:
    path = tmp_path / "controlled.sqlite3"
    store = SQLiteRunStore(path)
    intent = _controlled_intent()
    terminal = _preflight_state(skill_id=intent.skill_id)

    assert store.reserve_controlled_intent(intent) is True
    assert store.reserve_controlled_intent(intent) is False
    with pytest.raises(RunStoreConflictError, match="different controlled run intent"):
        store.reserve_controlled_intent(_controlled_intent(raw_parameters_sha256="2" * 64))
    _publish_state(path, terminal)
    store.put_run_state(terminal)

    restarted = SQLiteRunStore(path)
    assert restarted.read_controlled_intent(RUN_ID) == intent
    assert restarted.read_run_state(RUN_ID) == terminal

    changed = _controlled_intent(descriptor_sha256="3" * 64)
    changed_payload = json.dumps(
        changed.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            """
            UPDATE harness_controlled_run_intents
            SET descriptor_sha256 = ?, payload_json = ?, payload_sha256 = ?
            WHERE run_id = ?
            """,
            (
                changed.descriptor_sha256,
                changed_payload,
                sha256(changed_payload).hexdigest(),
                str(RUN_ID),
            ),
        )
    assert store.read_controlled_intent(RUN_ID) == changed
    with pytest.raises(RunStoreCorruptionError, match="controlled intent payload"):
        store.read_run_state(RUN_ID)


def test_validated_controlled_intent_requires_exact_invocation_dependencies(
    tmp_path: Path,
) -> None:
    path = tmp_path / "controlled-dependencies.sqlite3"
    store = SQLiteRunStore(path)
    invocation = _invocation()
    effective_sha256 = sha256(
        json.dumps(
            invocation.effective_parameters,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    intent = _controlled_intent(
        phase="validated_input",
        effective_parameters_sha256=effective_sha256,
        dependencies=(),
    )
    assert store.reserve_controlled_intent(intent)

    with pytest.raises(RunStoreConflictError, match="dependencies"):
        store.put_invocation(invocation)


def test_validated_controlled_intent_commits_exact_invocation_and_terminal(
    tmp_path: Path,
) -> None:
    path = tmp_path / "controlled-terminal.sqlite3"
    store = SQLiteRunStore(path)
    invocation = _invocation()
    terminal = _terminal_state()
    effective_sha256 = sha256(
        json.dumps(
            invocation.effective_parameters,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    intent = _controlled_intent(
        phase="validated_input",
        effective_parameters_sha256=effective_sha256,
        dependencies=invocation.dependencies,
    )

    assert store.reserve_controlled_intent(intent)
    store.put_invocation(invocation)
    _publish_state(path, terminal)
    store.put_run_state(terminal)

    assert store.read_run_state(RUN_ID) == terminal


def test_controlled_lifecycle_read_returns_one_verified_sqlite_snapshot(
    tmp_path: Path,
) -> None:
    path = tmp_path / "controlled-lifecycle.sqlite3"
    store = SQLiteRunStore(path)
    invocation = _invocation()
    terminal = _terminal_state()
    effective_sha256 = sha256(
        json.dumps(
            invocation.effective_parameters,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    intent = _controlled_intent(
        phase="validated_input",
        effective_parameters_sha256=effective_sha256,
        dependencies=invocation.dependencies,
    )

    assert store.read_controlled_lifecycle(RUN_ID) == run_store_module.ControlledRunLifecycle(
        intent=None,
        invocation=None,
        terminal=None,
    )
    assert store.reserve_controlled_intent(intent)
    assert store.read_controlled_lifecycle(RUN_ID).intent == intent
    store.put_invocation(invocation)
    assert store.read_controlled_lifecycle(RUN_ID).invocation == invocation
    _publish_state(path, terminal)
    running_snapshot = store.read_controlled_lifecycle(RUN_ID)
    assert running_snapshot.intent == intent
    assert running_snapshot.invocation == invocation
    assert running_snapshot.terminal is None
    store.put_run_state(terminal)

    assert store.read_controlled_lifecycle(RUN_ID) == run_store_module.ControlledRunLifecycle(
        intent=intent,
        invocation=invocation,
        terminal=terminal,
    )


@pytest.mark.parametrize("phase", ["running", "terminal", "preflight"])
def test_lifecycle_read_recognizes_verified_ordinary_run_occupancy(
    tmp_path: Path,
    phase: str,
) -> None:
    path = tmp_path / f"ordinary-{phase}.sqlite3"
    store = SQLiteRunStore(path)
    if phase == "preflight":
        terminal = _preflight_state()
        _publish_state(path, terminal)
        store.put_run_state(terminal)
        invocation = None
    else:
        invocation = _invocation()
        store.put_invocation(invocation)
        terminal = None
        if phase == "terminal":
            terminal = _terminal_state()
            _publish_state(path, terminal)
            store.put_run_state(terminal)

    assert store.read_controlled_lifecycle(RUN_ID) == run_store_module.ControlledRunLifecycle(
        intent=None,
        invocation=invocation,
        terminal=terminal,
    )


@pytest.mark.parametrize(
    "attack",
    ["event-without-intent", "invocation-binding", "event-without-invocation"],
)
def test_controlled_lifecycle_read_rejects_inconsistent_partial_records(
    tmp_path: Path,
    attack: str,
) -> None:
    path = tmp_path / f"controlled-partial-{attack}.sqlite3"
    store = SQLiteRunStore(path)
    invocation = _invocation()
    if attack == "event-without-intent":
        state = _preflight_state()
        SQLiteEventJournal(path).publish(
            RunEvent(
                run_id=state.run_id,
                skill_id=state.skill_id,
                skill_version=state.skill_version,
                event=state.events[0],
            )
        )
    elif attack == "invocation-binding":
        effective_sha256 = sha256(
            json.dumps(
                invocation.effective_parameters,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        intent = _controlled_intent(
            phase="validated_input",
            effective_parameters_sha256=effective_sha256,
            dependencies=invocation.dependencies,
        )
        assert store.reserve_controlled_intent(intent)
        store.put_invocation(invocation)
        changed = intent.model_copy(
            update={"dependencies": (DependencyRef(name="changed", version="1", sha256="3" * 64),)}
        )
        payload = json.dumps(
            changed.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "UPDATE harness_controlled_run_intents "
                "SET payload_json = ?, payload_sha256 = ? WHERE run_id = ?",
                (payload, sha256(payload).hexdigest(), str(RUN_ID)),
            )
    else:
        intent = _controlled_intent()
        assert store.reserve_controlled_intent(intent)
        state = _preflight_state(skill_id=intent.skill_id)
        SQLiteEventJournal(path).publish(
            RunEvent(
                run_id=state.run_id,
                skill_id=state.skill_id,
                skill_version=state.skill_version,
                event=state.events[0],
            )
        )

    with pytest.raises(RunStoreCorruptionError, match="controlled lifecycle"):
        store.read_controlled_lifecycle(RUN_ID)


@pytest.mark.parametrize(
    "attack",
    ["non-object", "binding", "lifecycle", "below-attempt", "above-attempt"],
)
def test_controlled_lifecycle_read_rejects_inconsistent_running_events(
    tmp_path: Path,
    attack: str,
) -> None:
    path = tmp_path / f"controlled-event-{attack}.sqlite3"
    store = SQLiteRunStore(path)
    invocation = _invocation()
    effective_sha256 = sha256(
        json.dumps(
            invocation.effective_parameters,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    intent = _controlled_intent(
        phase="validated_input",
        effective_parameters_sha256=effective_sha256,
        dependencies=invocation.dependencies,
    )
    assert store.reserve_controlled_intent(intent)
    store.put_invocation(invocation)
    first = _terminal_state().events[0]
    SQLiteEventJournal(path).publish(
        RunEvent(
            run_id=RUN_ID,
            skill_id=invocation.skill_id,
            skill_version=invocation.skill_version,
            event=first,
        )
    )
    with closing(sqlite3.connect(path)) as connection, connection:
        if attack == "non-object":
            connection.execute("UPDATE run_events SET envelope_json = '[]'")
        elif attack == "binding":
            changed_envelope = json.dumps(
                {
                    "run_id": str(RUN_ID),
                    "skill_id": "text2env.replay",
                    "skill_version": invocation.skill_version,
                    "event": first.model_dump(mode="json"),
                },
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            connection.execute(
                "UPDATE run_events SET skill_id = 'text2env.replay', envelope_json = ?",
                (changed_envelope,),
            )
        elif attack == "lifecycle":
            changed_event = first.model_copy(update={"from_status": RunStatus.RUNNING})
            changed_envelope = _canonical_envelope(_terminal_state(), changed_event)
            connection.execute(
                "UPDATE run_events SET from_status = 'running', envelope_json = ?",
                (changed_envelope,),
            )
        else:
            changed_attempt = 0 if attack == "below-attempt" else 2
            changed_event = first.model_copy(update={"attempt": changed_attempt})
            changed_envelope = _canonical_envelope(_terminal_state(), changed_event)
            connection.execute(
                "UPDATE run_events SET attempt = ?, envelope_json = ?",
                (changed_attempt, changed_envelope),
            )

    with pytest.raises(RunStoreCorruptionError):
        store.read_controlled_lifecycle(RUN_ID)


@pytest.mark.parametrize("occupied", ["invocation", "terminal", "event"])
def test_controlled_intent_cannot_reserve_an_identity_with_prior_lifecycle(
    tmp_path: Path,
    occupied: str,
) -> None:
    path = tmp_path / f"controlled-occupied-{occupied}.sqlite3"
    store = SQLiteRunStore(path)
    if occupied == "invocation":
        store.put_invocation(_invocation())
    elif occupied == "terminal":
        state = _preflight_state()
        _publish_state(path, state)
        store.put_run_state(state)
    else:
        state = _preflight_state()
        SQLiteEventJournal(path).publish(
            RunEvent(
                run_id=state.run_id,
                skill_id=state.skill_id,
                skill_version=state.skill_version,
                event=state.events[0],
            )
        )

    with pytest.raises(RunStoreConflictError):
        store.reserve_controlled_intent(_controlled_intent())


def _forge_preflight_terminal(state: RunState, attack: str) -> RunState:
    first, second = state.events
    artifact = ArtifactRef(
        name="forged",
        uri=f"artifact://sha256/{'4' * 64}",
        media_type="application/json",
        sha256="4" * 64,
        bytes=1,
        schema_version="harness.attack.v1",
    )
    if attack == "success":
        return state.model_copy(update={"status": RunStatus.SUCCEEDED})
    if attack == "missing_blocker":
        return state.model_copy(update={"blocker": None})
    if attack == "blocker_stage":
        return state.model_copy(update={"blocker": state.blocker.model_copy(update={"stage": "x"})})
    if attack == "blocker_retryable":
        return state.model_copy(
            update={"blocker": state.blocker.model_copy(update={"retryable": True})}
        )
    if attack == "blocker_unknowns":
        return state.model_copy(
            update={
                "blocker": state.blocker.model_copy(
                    update={
                        "unknowns": (UnknownField(field="forged", reason="attack", source="test"),)
                    }
                )
            }
        )
    if attack == "output":
        return state.model_copy(update={"output": {"forged": True}})
    if attack == "attempt":
        return state.model_copy(update={"attempt": 1})
    if attack == "max_attempts":
        return state.model_copy(update={"max_attempts": 1})
    if attack == "ended_at":
        return state.model_copy(update={"ended_at": None})
    if attack == "event_count":
        return state.model_copy(update={"events": (first,)})
    event_changes: dict[str, object]
    event_index: int
    if attack == "first_seq":
        event_index, event_changes = 0, {"seq": 9}
    elif attack == "first_timestamp":
        event_index, event_changes = 0, {"timestamp": START + timedelta(milliseconds=1)}
    elif attack == "first_attempt":
        event_index, event_changes = 0, {"attempt": 1}
    elif attack == "first_from":
        event_index, event_changes = 0, {"from_status": RunStatus.RUNNING}
    elif attack == "first_to":
        event_index, event_changes = 0, {"to_status": RunStatus.BLOCKED}
    elif attack == "first_stage":
        event_index, event_changes = 0, {"stage": "invoke"}
    elif attack == "first_artifacts":
        event_index, event_changes = 0, {"artifact_refs": (artifact,)}
    elif attack == "second_seq":
        event_index, event_changes = 1, {"seq": 9}
    elif attack == "second_timestamp":
        event_index, event_changes = 1, {"timestamp": START}
    elif attack == "second_attempt":
        event_index, event_changes = 1, {"attempt": 1}
    elif attack == "second_from":
        event_index, event_changes = 1, {"from_status": None}
    elif attack == "second_to":
        event_index, event_changes = 1, {"to_status": RunStatus.FAILED}
    elif attack == "second_stage":
        event_index, event_changes = 1, {"stage": "invoke"}
    elif attack == "second_artifacts":
        event_index, event_changes = 1, {"artifact_refs": (artifact,)}
    else:
        assert attack == "artifacts"
        return state.model_copy(update={"artifacts": (artifact,)})
    events = [first, second]
    events[event_index] = events[event_index].model_copy(update=event_changes)
    return state.model_copy(update={"events": tuple(events)})


@pytest.mark.parametrize(
    "attack",
    [
        "success",
        "missing_blocker",
        "blocker_stage",
        "blocker_retryable",
        "blocker_unknowns",
        "output",
        "attempt",
        "max_attempts",
        "ended_at",
        "event_count",
        "first_seq",
        "first_timestamp",
        "first_attempt",
        "first_from",
        "first_to",
        "first_stage",
        "first_artifacts",
        "second_seq",
        "second_timestamp",
        "second_attempt",
        "second_from",
        "second_to",
        "second_stage",
        "second_artifacts",
        "artifacts",
    ],
)
def test_controlled_preflight_terminal_rejects_every_forged_semantic(
    tmp_path: Path,
    attack: str,
) -> None:
    path = tmp_path / f"forged-preflight-{attack}.sqlite3"
    store = SQLiteRunStore(path)
    intent = _controlled_intent()
    assert store.reserve_controlled_intent(intent)
    forged = _forge_preflight_terminal(
        _preflight_state(skill_id=intent.skill_id),
        attack,
    )

    with pytest.raises(RunStoreConflictError, match="terminal semantics"):
        store.put_run_state(forged)


def test_preflight_terminal_rejects_a_validated_execution_intent(tmp_path: Path) -> None:
    path = tmp_path / "preflight-with-validated-intent.sqlite3"
    store = SQLiteRunStore(path)
    invocation = _invocation()
    effective_sha256 = sha256(
        json.dumps(
            invocation.effective_parameters,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert store.reserve_controlled_intent(
        _controlled_intent(
            phase="validated_input",
            effective_parameters_sha256=effective_sha256,
            dependencies=invocation.dependencies,
        )
    )

    with pytest.raises(RunStoreConflictError, match="preflight controlled intent"):
        store.put_run_state(_preflight_state(skill_id="text2env.compile"))


def test_controlled_terminal_requires_exact_intent_and_invocation_binding(tmp_path: Path) -> None:
    preflight_path = tmp_path / "preflight-binding.sqlite3"
    preflight_store = SQLiteRunStore(preflight_path)
    assert preflight_store.reserve_controlled_intent(_controlled_intent())
    with pytest.raises(RunStoreConflictError, match="skill_id"):
        preflight_store.put_run_state(_preflight_state())

    execution_path = tmp_path / "execution-binding.sqlite3"
    execution_store = SQLiteRunStore(execution_path)
    invocation = _invocation()
    effective_sha256 = sha256(
        json.dumps(
            invocation.effective_parameters,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert execution_store.reserve_controlled_intent(
        _controlled_intent(
            phase="validated_input",
            effective_parameters_sha256=effective_sha256,
            dependencies=invocation.dependencies,
        )
    )
    with pytest.raises(RunStoreConflictError, match="persisted invocation"):
        execution_store.put_run_state(_terminal_state())


@pytest.mark.parametrize("mismatch", ["skill_id", "max_attempts", "effective_parameters"])
def test_controlled_invocation_rejects_each_immutable_intent_mismatch(
    tmp_path: Path,
    mismatch: str,
) -> None:
    path = tmp_path / f"invocation-binding-{mismatch}.sqlite3"
    store = SQLiteRunStore(path)
    invocation = _invocation()
    effective_sha256 = sha256(
        json.dumps(
            invocation.effective_parameters,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    intent = _controlled_intent(
        phase="validated_input",
        effective_parameters_sha256=(
            "0" * 64 if mismatch == "effective_parameters" else effective_sha256
        ),
        dependencies=invocation.dependencies,
    )
    assert store.reserve_controlled_intent(intent)
    if mismatch == "skill_id":
        invocation = _invocation(skill_id="text2env.replay")
    elif mismatch == "max_attempts":
        invocation = _invocation(max_attempts=2)

    with pytest.raises(RunStoreConflictError, match=mismatch.split("_")[0]):
        store.put_invocation(invocation)


def test_invocation_commit_rereads_its_exact_row_before_returning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SQLiteRunStore(tmp_path / "invocation-reread.sqlite3")
    original = run_store_module._select_invocation

    def disappear_after_insert(connection, run_id):
        row = original(connection, run_id)
        return None if row is not None else row

    monkeypatch.setattr(run_store_module, "_select_invocation", disappear_after_insert)
    with pytest.raises(RunStoreCorruptionError, match="invocation changed"):
        store.put_invocation(_invocation())


def test_controlled_intent_commit_rereads_its_exact_row_before_returning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SQLiteRunStore(tmp_path / "intent-reread.sqlite3")
    original = run_store_module._select_controlled_intent

    def disappear_after_insert(connection, run_id):
        row = original(connection, run_id)
        return None if row is not None else row

    monkeypatch.setattr(run_store_module, "_select_controlled_intent", disappear_after_insert)
    with pytest.raises(RunStoreCorruptionError, match="intent changed"):
        store.reserve_controlled_intent(_controlled_intent())


def test_controlled_intent_commit_rejects_lifecycle_injected_after_insert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SQLiteRunStore(tmp_path / "intent-lifecycle.sqlite3")
    original = run_store_module._run_has_events

    def event_appears_after_insert(connection, run_id):
        exists = connection.execute(
            "SELECT 1 FROM harness_controlled_run_intents WHERE run_id = ?",
            (str(run_id),),
        ).fetchone()
        return exists is not None or original(connection, run_id)

    monkeypatch.setattr(run_store_module, "_run_has_events", event_appears_after_insert)
    with pytest.raises(RunStoreCorruptionError, match="mixed lifecycle"):
        store.reserve_controlled_intent(_controlled_intent())


def test_invocation_commit_rejects_lifecycle_injected_after_insert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SQLiteRunStore(tmp_path / "invocation-lifecycle.sqlite3")
    original = run_store_module._run_has_events

    def event_appears_after_insert(connection, run_id):
        exists = connection.execute(
            "SELECT 1 FROM harness_invocations WHERE run_id = ?",
            (str(run_id),),
        ).fetchone()
        return exists is not None or original(connection, run_id)

    monkeypatch.setattr(run_store_module, "_run_has_events", event_appears_after_insert)
    with pytest.raises(RunStoreCorruptionError, match="mixed lifecycle"):
        store.put_invocation(_invocation())


def test_terminal_commit_rereads_exact_state_before_returning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "terminal-reread.sqlite3"
    store = SQLiteRunStore(path)
    state = _terminal_state()
    store.put_invocation(_invocation())
    _publish_state(path, state)
    original_select = run_store_module._select_run_state

    def disappear_after_insert(connection, run_id):
        row = original_select(connection, run_id)
        return None if row is not None else row

    monkeypatch.setattr(run_store_module, "_select_run_state", disappear_after_insert)
    with pytest.raises(RunStoreCorruptionError, match="terminal state disappeared"):
        store.put_run_state(state)


def test_terminal_commit_rejects_a_changed_reread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "terminal-changed.sqlite3"
    store = SQLiteRunStore(path)
    state = _terminal_state()
    store.put_invocation(_invocation())
    _publish_state(path, state)
    original_decode = run_store_module._decode_record

    def changed_decode(row, model, label):
        record = original_decode(row, model, label)
        if label == "terminal state":
            return record.model_copy(update={"output": {"result": "forged"}})
        return record

    monkeypatch.setattr(run_store_module, "_decode_record", changed_decode)
    with pytest.raises(RunStoreCorruptionError, match="terminal state changed"):
        store.put_run_state(state)


def test_run_store_rejects_extra_check_constraint_with_matching_layout(
    tmp_path: Path,
) -> None:
    path = tmp_path / "forged-check.sqlite3"
    SQLiteRunStore(path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("DROP TABLE harness_invocations")
        connection.execute(
            """
            CREATE TABLE harness_invocations (
                run_id TEXT NOT NULL PRIMARY KEY,
                invocation_digest TEXT NOT NULL,
                skill_id TEXT NOT NULL CHECK (skill_id = 'never.valid'),
                skill_version TEXT NOT NULL,
                max_attempts INTEGER NOT NULL,
                payload_json BLOB NOT NULL,
                payload_sha256 TEXT NOT NULL
            )
            """
        )

    with pytest.raises(RunStoreCorruptionError, match="unsupported authority"):
        SQLiteRunStore(path)


def test_run_store_migrates_only_the_exact_known_legacy_layout(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    invocation = _invocation()
    payload = json.dumps(
        invocation.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        _create_legacy_run_tables(connection)
        connection.execute(
            """
            INSERT INTO harness_invocations (
                run_id, invocation_digest, skill_id, skill_version,
                max_attempts, payload_json, payload_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(invocation.run_id),
                invocation.invocation_digest,
                invocation.skill_id,
                invocation.skill_version,
                invocation.max_attempts,
                payload,
                sha256(payload).hexdigest(),
            ),
        )
    terminal = _terminal_state()
    _publish_state(path, terminal)
    terminal_payload = json.dumps(
        terminal.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            """
            INSERT INTO harness_run_states (
                run_id, invocation_digest, skill_id, skill_version,
                max_attempts, status, invocation_payload_sha256,
                payload_json, payload_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(terminal.run_id),
                terminal.invocation_digest,
                terminal.skill_id,
                terminal.skill_version,
                terminal.max_attempts,
                terminal.status.value,
                sha256(payload).hexdigest(),
                terminal_payload,
                sha256(terminal_payload).hexdigest(),
            ),
        )

    migrated = SQLiteRunStore(path)
    assert migrated.read_invocation(RUN_ID) == invocation
    assert migrated.read_run_state(RUN_ID) == terminal
    with closing(sqlite3.connect(path)) as connection:
        invocation_notnull = connection.execute(
            "SELECT \"notnull\" FROM pragma_table_xinfo('harness_invocations') "
            "WHERE name = 'run_id'"
        ).fetchone()[0]
        state_notnull = connection.execute(
            "SELECT \"notnull\" FROM pragma_table_xinfo('harness_run_states') WHERE name = 'run_id'"
        ).fetchone()[0]
        intent_columns = connection.execute(
            "SELECT count(*) FROM pragma_table_xinfo('harness_controlled_run_intents')"
        ).fetchone()[0]
    assert (invocation_notnull, state_notnull, intent_columns) == (1, 1, 13)


@pytest.mark.parametrize("attack", ["missing_pk", "extra_index", "conflict_policy", "trigger"])
def test_run_store_rejects_legacy_layouts_that_can_weaken_immutable_identity(
    tmp_path: Path,
    attack: str,
) -> None:
    path = tmp_path / f"legacy-{attack}.sqlite3"
    with closing(sqlite3.connect(path)) as connection, connection:
        if attack == "missing_pk":
            connection.execute(
                """
                CREATE TABLE harness_invocations (
                    run_id TEXT,
                    invocation_digest TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    skill_version TEXT NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    payload_json BLOB NOT NULL,
                    payload_sha256 TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE harness_run_states (
                    run_id TEXT PRIMARY KEY,
                    invocation_digest TEXT,
                    skill_id TEXT NOT NULL,
                    skill_version TEXT NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    invocation_payload_sha256 TEXT,
                    payload_json BLOB NOT NULL,
                    payload_sha256 TEXT NOT NULL
                )
                """
            )
        else:
            _create_legacy_run_tables(connection)
            if attack == "extra_index":
                connection.execute(
                    "CREATE UNIQUE INDEX weakened_identity ON harness_invocations(skill_id)"
                )
            elif attack == "conflict_policy":
                connection.execute("ALTER TABLE harness_invocations RENAME TO bad_invocations")
                connection.execute(
                    """
                    CREATE TABLE harness_invocations (
                        run_id TEXT PRIMARY KEY ON CONFLICT REPLACE,
                        invocation_digest TEXT NOT NULL,
                        skill_id TEXT NOT NULL,
                        skill_version TEXT NOT NULL,
                        max_attempts INTEGER NOT NULL,
                        payload_json BLOB NOT NULL,
                        payload_sha256 TEXT NOT NULL
                    )
                    """
                )
                connection.execute("DROP TABLE bad_invocations")
            else:
                connection.execute(
                    """
                    CREATE TRIGGER rewrite_invocation AFTER INSERT ON harness_invocations
                    BEGIN
                        UPDATE harness_invocations SET skill_id = 'text2env.replay';
                    END
                    """
                )

    with pytest.raises(RunStoreCorruptionError, match="unsupported authority"):
        SQLiteRunStore(path)


def test_run_store_rejects_a_same_named_current_intent_table_without_pk_authority(
    tmp_path: Path,
) -> None:
    path = tmp_path / "malformed-current.sqlite3"
    SQLiteRunStore(path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("DROP TABLE harness_controlled_run_intents")
        connection.execute(
            """
            CREATE TABLE harness_controlled_run_intents (
                run_id TEXT NOT NULL,
                skill_id TEXT NOT NULL,
                skill_version TEXT NOT NULL,
                input_schema TEXT NOT NULL,
                phase TEXT NOT NULL,
                raw_parameters_sha256 TEXT NOT NULL,
                effective_parameters_sha256 TEXT,
                descriptor_sha256 TEXT NOT NULL,
                implementation_sha256 TEXT NOT NULL,
                qualification_sha256 TEXT NOT NULL,
                max_attempts INTEGER NOT NULL,
                payload_json BLOB NOT NULL,
                payload_sha256 TEXT NOT NULL
            )
            """
        )

    with pytest.raises(RunStoreCorruptionError, match="unsupported authority"):
        SQLiteRunStore(path)


@pytest.mark.parametrize("attack", ["insert", "update", "delete"])
def test_controlled_intent_reservation_detects_late_trigger_mutation(
    tmp_path: Path,
    attack: str,
) -> None:
    path = tmp_path / f"late-{attack}.sqlite3"
    store = SQLiteRunStore(path)
    with closing(sqlite3.connect(path)) as connection, connection:
        if attack == "insert":
            connection.execute(
                """
                CREATE TRIGGER late_insert AFTER INSERT ON harness_controlled_run_intents
                BEGIN
                    INSERT INTO harness_invocations (
                        run_id, invocation_digest, skill_id, skill_version,
                        max_attempts, payload_json, payload_sha256
                    ) VALUES (
                        NEW.run_id,
                        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                        NEW.skill_id, NEW.skill_version, NEW.max_attempts,
                        CAST('{}' AS BLOB),
                        'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
                    );
                END
                """
            )
        elif attack == "update":
            connection.execute(
                """
                CREATE TRIGGER late_update AFTER INSERT ON harness_controlled_run_intents
                BEGIN
                    UPDATE harness_controlled_run_intents
                    SET raw_parameters_sha256 =
                        '9999999999999999999999999999999999999999999999999999999999999999'
                    WHERE run_id = NEW.run_id;
                END
                """
            )
        else:
            connection.execute(
                """
                CREATE TRIGGER late_delete AFTER INSERT ON harness_controlled_run_intents
                BEGIN
                    DELETE FROM harness_controlled_run_intents WHERE run_id = NEW.run_id;
                END
                """
            )

    with pytest.raises(RunStoreCorruptionError):
        store.reserve_controlled_intent(_controlled_intent())
    assert store.read_controlled_intent(RUN_ID) is None
    assert store.read_invocation(RUN_ID) is None


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


def test_fresh_run_store_constructors_are_bounded_and_share_controlled_identity(
    tmp_path: Path,
) -> None:
    for iteration in range(32):
        path = tmp_path / f"fresh-concurrent-{iteration}.sqlite3"
        ready = Barrier(2)

        def construct(_: int) -> SQLiteRunStore:
            ready.wait(timeout=2)
            return SQLiteRunStore(path, busy_timeout_ms=1_000)

        with ThreadPoolExecutor(max_workers=2) as executor:
            stores = tuple(executor.map(construct, range(2)))

        intent = _controlled_intent()
        assert stores[0].reserve_controlled_intent(intent)
        assert stores[1].read_controlled_intent(RUN_ID) == intent


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
