"""Durable authority for Harness invocations and terminal run states."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import Protocol, TypeVar, runtime_checkable
from uuid import UUID

from pydantic import ValidationError

from .schemas import Event, Invocation, RunState, RunStatus

_Record = TypeVar("_Record", Invocation, RunState)


@runtime_checkable
class InvocationSink(Protocol):
    """Seam used before a run publishes its first event."""

    def put_invocation(self, invocation: Invocation) -> None: ...


@runtime_checkable
class RunStateSink(Protocol):
    """Seam used once when a run reaches a terminal state."""

    def put_run_state(self, state: RunState) -> None: ...


@runtime_checkable
class RunStore(InvocationSink, RunStateSink, Protocol):
    """Unified durable write/read seam for immutable Harness run records."""

    def read_invocation(self, run_id: UUID) -> Invocation | None: ...

    def read_run_state(self, run_id: UUID) -> RunState | None: ...


class RunStoreConflictError(RuntimeError):
    """A write conflicts with an immutable record or run lifecycle."""


class RunStoreCorruptionError(RuntimeError):
    """Persisted bytes cannot reconstruct their declared immutable record."""


class SQLiteRunStore:
    """Persist immutable run records in a SQLite WAL database."""

    def __init__(self, path: Path, *, busy_timeout_ms: int = 5_000) -> None:
        if busy_timeout_ms <= 0:
            raise ValueError("busy_timeout_ms must be positive")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._busy_timeout_ms = busy_timeout_ms
        self._initialize()

    def put_invocation(self, invocation: Invocation) -> None:
        payload = _canonical_bytes(invocation)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_row = _select_invocation(connection, invocation.run_id)
            if existing_row is not None:
                _decode_record(existing_row, Invocation, "invocation")
                if existing_row["payload_json"] != payload:
                    raise RunStoreConflictError(
                        f"run {invocation.run_id} already has a different invocation"
                    )
                return
            if _run_has_events(connection, invocation.run_id):
                raise RunStoreConflictError(
                    f"run {invocation.run_id} invocation must be persisted before the first event"
                )
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

    def put_run_state(self, state: RunState) -> None:
        if state.status == RunStatus.RUNNING:
            raise RunStoreConflictError("only a terminal RunState can be persisted")
        payload = _canonical_bytes(state)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_row = _select_run_state(connection, state.run_id)
            existing_state = (
                _decode_record(existing_row, RunState, "terminal state")
                if existing_row is not None
                else None
            )
            invocation_row = _select_invocation(connection, state.run_id)
            invocation = (
                _decode_record(invocation_row, Invocation, "invocation")
                if invocation_row is not None
                else None
            )
            if existing_state is not None:
                _validate_persisted_binding(existing_state, invocation)
                _validate_invocation_payload_anchor(
                    existing_state,
                    existing_row,
                    invocation_row,
                )
                _validate_event_journal(
                    connection,
                    existing_state,
                    error_type=RunStoreCorruptionError,
                )
                if existing_row["payload_json"] != payload:
                    raise RunStoreConflictError(
                        f"run {state.run_id} already has a different terminal state"
                    )
                return
            _validate_state_binding(state, invocation)
            _validate_event_journal(
                connection,
                state,
                error_type=RunStoreConflictError,
            )
            connection.execute(
                """
                INSERT INTO harness_run_states (
                    run_id, invocation_digest, skill_id, skill_version,
                    max_attempts, status, invocation_payload_sha256,
                    payload_json, payload_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(state.run_id),
                    state.invocation_digest,
                    state.skill_id,
                    state.skill_version,
                    state.max_attempts,
                    state.status.value,
                    invocation_row["payload_sha256"] if invocation_row is not None else None,
                    payload,
                    sha256(payload).hexdigest(),
                ),
            )

    def read_invocation(self, run_id: UUID) -> Invocation | None:
        with self._connection() as connection:
            row = _select_invocation(connection, run_id)
        if row is None:
            return None
        return _decode_record(row, Invocation, "invocation")

    def read_run_state(self, run_id: UUID) -> RunState | None:
        with self._connection() as connection:
            connection.execute("BEGIN")
            row = _select_run_state(connection, run_id)
            if row is None:
                return None
            state = _decode_record(row, RunState, "terminal state")
            invocation_row = _select_invocation(connection, run_id)
            invocation = (
                _decode_record(invocation_row, Invocation, "invocation")
                if invocation_row is not None
                else None
            )
            _validate_persisted_binding(state, invocation)
            _validate_invocation_payload_anchor(state, row, invocation_row)
            _validate_event_journal(
                connection,
                state,
                error_type=RunStoreCorruptionError,
            )
            return state

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.path,
            timeout=self._busy_timeout_ms / 1_000,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            connection.execute("PRAGMA synchronous = FULL")
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS harness_invocations (
                    run_id TEXT PRIMARY KEY,
                    invocation_digest TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    skill_version TEXT NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    payload_json BLOB NOT NULL,
                    payload_sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS harness_run_states (
                    run_id TEXT PRIMARY KEY,
                    invocation_digest TEXT,
                    skill_id TEXT NOT NULL,
                    skill_version TEXT NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    invocation_payload_sha256 TEXT,
                    payload_json BLOB NOT NULL,
                    payload_sha256 TEXT NOT NULL
                );
                """
            )


def _canonical_bytes(value: Invocation | RunState) -> bytes:
    return json.dumps(
        value.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _select_invocation(connection: sqlite3.Connection, run_id: UUID) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT run_id, invocation_digest, skill_id, skill_version,
               max_attempts, payload_json, payload_sha256
        FROM harness_invocations
        WHERE run_id = ?
        """,
        (str(run_id),),
    ).fetchone()


def _select_run_state(connection: sqlite3.Connection, run_id: UUID) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT run_id, invocation_digest, skill_id, skill_version,
               max_attempts, status, invocation_payload_sha256,
               payload_json, payload_sha256
        FROM harness_run_states
        WHERE run_id = ?
        """,
        (str(run_id),),
    ).fetchone()


def _decode_record(
    row: sqlite3.Row,
    model: type[_Record],
    label: str,
) -> _Record:
    raw = row["payload_json"]
    run_id = row["run_id"]
    if not isinstance(raw, bytes):
        raise RunStoreCorruptionError(
            f"stored {label} for run {run_id} is not canonical JSON bytes"
        )
    if sha256(raw).hexdigest() != row["payload_sha256"]:
        raise RunStoreCorruptionError(
            f"stored {label} for run {run_id} failed its payload checksum"
        )
    try:
        decoded = json.loads(raw)
        record = model.model_validate(decoded)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        ValidationError,
    ) as error:
        raise RunStoreCorruptionError(
            f"cannot reconstruct stored {label} for run {run_id}: {error}"
        ) from error
    if _canonical_bytes(record) != raw:
        raise RunStoreCorruptionError(
            f"stored {label} for run {run_id} is not canonical JSON bytes"
        )
    expected_metadata: dict[str, object] = {
        "run_id": str(record.run_id),
        "invocation_digest": record.invocation_digest,
        "skill_id": record.skill_id,
        "skill_version": record.skill_version,
        "max_attempts": record.max_attempts,
    }
    if isinstance(record, RunState):
        expected_metadata["status"] = record.status.value
    for field, expected in expected_metadata.items():
        if row[field] != expected:
            raise RunStoreCorruptionError(
                f"stored {label} for run {run_id} has inconsistent {field} metadata"
            )
    return record


def _validate_state_binding(state: RunState, invocation: Invocation | None) -> None:
    if state.invocation_digest is None:
        if invocation is not None:
            raise RunStoreConflictError(
                "preflight terminal state cannot be bound to a persisted invocation"
            )
        return
    if invocation is None:
        raise RunStoreConflictError("terminal RunState requires a persisted invocation")
    expected = {
        "run_id": invocation.run_id,
        "invocation_digest": invocation.invocation_digest,
        "skill_id": invocation.skill_id,
        "skill_version": invocation.skill_version,
        "max_attempts": invocation.max_attempts,
    }
    for field, value in expected.items():
        if getattr(state, field) != value:
            raise RunStoreConflictError(f"RunState {field} does not match its invocation")


def _validate_persisted_binding(state: RunState, invocation: Invocation | None) -> None:
    try:
        _validate_state_binding(state, invocation)
    except RunStoreConflictError as error:
        raise RunStoreCorruptionError(
            f"stored terminal state for run {state.run_id} has inconsistent binding: {error}"
        ) from error


def _validate_invocation_payload_anchor(
    state: RunState,
    state_row: sqlite3.Row,
    invocation_row: sqlite3.Row | None,
) -> None:
    expected = state_row["invocation_payload_sha256"]
    actual = invocation_row["payload_sha256"] if invocation_row is not None else None
    if expected != actual:
        raise RunStoreCorruptionError(
            f"stored terminal state for run {state.run_id} has inconsistent invocation payload"
        )


def _validate_event_journal(
    connection: sqlite3.Connection,
    state: RunState,
    *,
    error_type: type[RunStoreConflictError] | type[RunStoreCorruptionError],
) -> None:
    if not _event_table_exists(connection):
        raise error_type(f"run {state.run_id} event journal table is missing")
    rows = connection.execute(
        """
        SELECT run_id, seq, skill_id, skill_version, timestamp, stage,
               attempt, from_status, to_status, envelope_json
        FROM run_events
        WHERE run_id = ?
        ORDER BY seq
        """,
        (str(state.run_id),),
    ).fetchall()
    if len(rows) != len(state.events):
        raise error_type(
            f"run {state.run_id} event journal has {len(rows)} events but "
            f"RunState declares {len(state.events)}"
        )
    for row, event in zip(rows, state.events, strict=True):
        if row["envelope_json"] != _canonical_event_envelope(state, event):
            raise error_type(
                f"run {state.run_id} event {event.seq} does not canonically match RunState.events"
            )
        expected_metadata = (
            str(state.run_id),
            event.seq,
            state.skill_id,
            state.skill_version,
            event.timestamp.isoformat(),
            event.stage,
            event.attempt,
            event.from_status.value if event.from_status is not None else None,
            event.to_status.value,
        )
        observed_metadata = tuple(
            row[field]
            for field in (
                "run_id",
                "seq",
                "skill_id",
                "skill_version",
                "timestamp",
                "stage",
                "attempt",
                "from_status",
                "to_status",
            )
        )
        if observed_metadata != expected_metadata:
            raise error_type(
                f"run {state.run_id} event {event.seq} has inconsistent journal metadata"
            )


def _canonical_event_envelope(state: RunState, event: Event) -> str:
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


def _event_table_exists(connection: sqlite3.Connection) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'run_events'"
        ).fetchone()
        is not None
    )


def _run_has_events(connection: sqlite3.Connection, run_id: UUID) -> bool:
    if not _event_table_exists(connection):
        return False
    return (
        connection.execute(
            "SELECT 1 FROM run_events WHERE run_id = ? LIMIT 1",
            (str(run_id),),
        ).fetchone()
        is not None
    )
