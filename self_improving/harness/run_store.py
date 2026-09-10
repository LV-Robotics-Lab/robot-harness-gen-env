"""Durable authority for Harness invocations and terminal run states."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol, TypeVar, runtime_checkable
from uuid import UUID

from pydantic import UUID4, ValidationError, model_validator

from .event_journal import EventJournalCorruptionError, verify_persisted_event_chain
from .schemas import DependencyRef, Invocation, RunState, RunStatus
from .schemas.base import (
    HarnessModel,
    PositiveInt,
    SchemaId,
    Sha256,
    SkillId,
    StableSemVer,
)

_SQL_QUOTED_OR_COMMENT = re.compile(
    r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`|\[(?:\]\]|[^\]])*\]|"
    r"--[^\r\n]*|/\*.*?\*/",
    re.DOTALL,
)
_TABLE_FORBIDDEN_SEMANTICS = re.compile(
    r"\b(?:ON\s+CONFLICT|CHECK)\b",
    re.IGNORECASE,
)
_PRIMARY_KEY_INDEX = (
    (
        "pk",
        1,
        0,
        (
            (0, 0, "run_id", 0, "BINARY", 1),
            (1, -1, None, 0, "BINARY", 0),
        ),
    ),
)
_LEGACY_INVOCATION_COLUMNS = (
    (0, "run_id", "TEXT", 0, None, 1, 0),
    (1, "invocation_digest", "TEXT", 1, None, 0, 0),
    (2, "skill_id", "TEXT", 1, None, 0, 0),
    (3, "skill_version", "TEXT", 1, None, 0, 0),
    (4, "max_attempts", "INTEGER", 1, None, 0, 0),
    (5, "payload_json", "BLOB", 1, None, 0, 0),
    (6, "payload_sha256", "TEXT", 1, None, 0, 0),
)
_CURRENT_INVOCATION_COLUMNS = (
    (0, "run_id", "TEXT", 1, None, 1, 0),
    *_LEGACY_INVOCATION_COLUMNS[1:],
)
_LEGACY_STATE_COLUMNS = (
    (0, "run_id", "TEXT", 0, None, 1, 0),
    (1, "invocation_digest", "TEXT", 0, None, 0, 0),
    (2, "skill_id", "TEXT", 1, None, 0, 0),
    (3, "skill_version", "TEXT", 1, None, 0, 0),
    (4, "max_attempts", "INTEGER", 1, None, 0, 0),
    (5, "status", "TEXT", 1, None, 0, 0),
    (6, "invocation_payload_sha256", "TEXT", 0, None, 0, 0),
    (7, "payload_json", "BLOB", 1, None, 0, 0),
    (8, "payload_sha256", "TEXT", 1, None, 0, 0),
)
_CURRENT_STATE_COLUMNS = (
    (0, "run_id", "TEXT", 1, None, 1, 0),
    *_LEGACY_STATE_COLUMNS[1:7],
    (7, "controlled_intent_payload_sha256", "TEXT", 0, None, 0, 0),
    (8, "payload_json", "BLOB", 1, None, 0, 0),
    (9, "payload_sha256", "TEXT", 1, None, 0, 0),
)
_CURRENT_INTENT_COLUMNS = (
    (0, "run_id", "TEXT", 1, None, 1, 0),
    (1, "skill_id", "TEXT", 1, None, 0, 0),
    (2, "skill_version", "TEXT", 1, None, 0, 0),
    (3, "input_schema", "TEXT", 1, None, 0, 0),
    (4, "phase", "TEXT", 1, None, 0, 0),
    (5, "raw_parameters_sha256", "TEXT", 1, None, 0, 0),
    (6, "effective_parameters_sha256", "TEXT", 0, None, 0, 0),
    (7, "descriptor_sha256", "TEXT", 1, None, 0, 0),
    (8, "implementation_sha256", "TEXT", 1, None, 0, 0),
    (9, "qualification_sha256", "TEXT", 1, None, 0, 0),
    (10, "max_attempts", "INTEGER", 1, None, 0, 0),
    (11, "payload_json", "BLOB", 1, None, 0, 0),
    (12, "payload_sha256", "TEXT", 1, None, 0, 0),
)


class ControlledRunIntent(HarnessModel):
    """Private durable reservation for one caller-selected production run identity."""

    run_id: UUID4
    skill_id: SkillId
    skill_version: StableSemVer
    input_schema: SchemaId
    phase: Literal["input_validation", "preflight", "validated_input"]
    raw_parameters_sha256: Sha256
    effective_parameters_sha256: Sha256 | None
    dependencies: tuple[DependencyRef, ...] | None
    descriptor_sha256: Sha256
    implementation_sha256: Sha256
    qualification_sha256: Sha256
    max_attempts: PositiveInt

    @model_validator(mode="after")
    def intent_is_phase_complete_and_canonical(self) -> "ControlledRunIntent":
        if self.phase == "input_validation":
            if self.effective_parameters_sha256 is not None or self.dependencies is not None:
                raise ValueError(
                    "input_validation intent cannot claim effective input or dependencies"
                )
        elif self.effective_parameters_sha256 is None:
            raise ValueError("validated intent requires effective_parameters_sha256")
        elif self.phase == "preflight" and self.dependencies is not None:
            raise ValueError("preflight intent cannot claim resolved dependencies")
        elif self.phase == "validated_input" and self.dependencies is None:
            raise ValueError("validated_input intent requires exact dependencies")
        if self.dependencies is not None:
            names = [dependency.name for dependency in self.dependencies]
            if names != sorted(names) or len(names) != len(set(names)):
                raise ValueError("intent dependencies must be sorted and unique by name")
        return self


_Record = TypeVar("_Record", ControlledRunIntent, Invocation, RunState)


@dataclass(frozen=True)
class ControlledRunLifecycle:
    """One transactionally consistent view of a controlled run lifecycle."""

    intent: ControlledRunIntent | None
    invocation: Invocation | None
    terminal: RunState | None


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

    def reserve_controlled_intent(self, intent: ControlledRunIntent) -> bool: ...

    def read_controlled_intent(self, run_id: UUID) -> ControlledRunIntent | None: ...

    def read_invocation(self, run_id: UUID) -> Invocation | None: ...

    def read_run_state(self, run_id: UUID) -> RunState | None: ...

    def read_controlled_lifecycle(self, run_id: UUID) -> ControlledRunLifecycle: ...


class RunStoreConflictError(RuntimeError):
    """A write conflicts with an immutable record or run lifecycle."""


class RunStoreCorruptionError(RuntimeError):
    """Persisted bytes cannot reconstruct their declared immutable record."""


class SQLiteRunStore:
    """Persist immutable run records in a durable SQLite authority."""

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
            _require_current_layout(connection)
            intent_row = _select_controlled_intent(connection, invocation.run_id)
            if intent_row is not None:
                intent = _decode_record(
                    intent_row,
                    ControlledRunIntent,
                    "controlled run intent",
                )
                _validate_controlled_intent_invocation_binding(intent, invocation)
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
            _require_current_layout(connection)
            stored_row = _select_invocation(connection, invocation.run_id)
            if (
                stored_row is None
                or _decode_record(
                    stored_row,
                    Invocation,
                    "invocation",
                )
                != invocation
            ):
                raise RunStoreCorruptionError(
                    f"invocation changed while committing run {invocation.run_id}"
                )
            if _select_run_state(connection, invocation.run_id) is not None or _run_has_events(
                connection,
                invocation.run_id,
            ):
                raise RunStoreCorruptionError(
                    f"invocation commit mixed lifecycle for run {invocation.run_id}"
                )

    def reserve_controlled_intent(self, intent: ControlledRunIntent) -> bool:
        """Atomically reserve exact execution intent; return false for an exact retry."""

        payload = _canonical_bytes(intent)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _require_current_layout(connection)
            existing_row = _select_controlled_intent(connection, intent.run_id)
            if existing_row is not None:
                _decode_record(existing_row, ControlledRunIntent, "controlled run intent")
                if existing_row["payload_json"] != payload:
                    raise RunStoreConflictError(
                        f"run {intent.run_id} already has a different controlled run intent"
                    )
                return False
            if _select_invocation(connection, intent.run_id) is not None:
                raise RunStoreConflictError(f"run {intent.run_id} already has an invocation")
            if _select_run_state(connection, intent.run_id) is not None:
                raise RunStoreConflictError(
                    f"run {intent.run_id} already has a terminal state without this intent"
                )
            if _run_has_events(connection, intent.run_id):
                raise RunStoreConflictError(
                    f"run {intent.run_id} intent must be persisted before the first event"
                )
            connection.execute(
                """
                INSERT INTO harness_controlled_run_intents (
                    run_id, skill_id, skill_version, input_schema, phase,
                    raw_parameters_sha256, effective_parameters_sha256,
                    descriptor_sha256, implementation_sha256, qualification_sha256,
                    max_attempts, payload_json, payload_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(intent.run_id),
                    intent.skill_id,
                    intent.skill_version,
                    intent.input_schema,
                    intent.phase,
                    intent.raw_parameters_sha256,
                    intent.effective_parameters_sha256,
                    intent.descriptor_sha256,
                    intent.implementation_sha256,
                    intent.qualification_sha256,
                    intent.max_attempts,
                    payload,
                    sha256(payload).hexdigest(),
                ),
            )
            _require_current_layout(connection)
            stored = _select_controlled_intent(connection, intent.run_id)
            if (
                stored is None
                or _decode_record(
                    stored,
                    ControlledRunIntent,
                    "controlled run intent",
                )
                != intent
            ):
                raise RunStoreCorruptionError(
                    f"controlled run intent changed while reserving run {intent.run_id}"
                )
            if (
                _select_invocation(connection, intent.run_id) is not None
                or _select_run_state(connection, intent.run_id) is not None
                or _run_has_events(connection, intent.run_id)
            ):
                raise RunStoreCorruptionError(
                    f"controlled run intent reservation mixed lifecycle for run {intent.run_id}"
                )
            return True

    def put_run_state(self, state: RunState) -> None:
        if state.status == RunStatus.RUNNING:
            raise RunStoreConflictError("only a terminal RunState can be persisted")
        payload = _canonical_bytes(state)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _require_current_layout(connection)
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
            intent_row = _select_controlled_intent(connection, state.run_id)
            intent = (
                _decode_record(intent_row, ControlledRunIntent, "controlled run intent")
                if intent_row is not None
                else None
            )
            if existing_state is not None:
                _validate_persisted_binding(existing_state, invocation, intent)
                _validate_invocation_payload_anchor(
                    existing_state,
                    existing_row,
                    invocation_row,
                )
                _validate_controlled_intent_payload_anchor(
                    existing_state,
                    existing_row,
                    intent_row,
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
            _validate_state_binding(state, invocation, intent)
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
                    controlled_intent_payload_sha256, payload_json, payload_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(state.run_id),
                    state.invocation_digest,
                    state.skill_id,
                    state.skill_version,
                    state.max_attempts,
                    state.status.value,
                    invocation_row["payload_sha256"] if invocation_row is not None else None,
                    intent_row["payload_sha256"] if intent_row is not None else None,
                    payload,
                    sha256(payload).hexdigest(),
                ),
            )
            _require_current_layout(connection)
            stored_row = _select_run_state(connection, state.run_id)
            stored_intent_row = _select_controlled_intent(connection, state.run_id)
            stored_invocation_row = _select_invocation(connection, state.run_id)
            if stored_row is None:
                raise RunStoreCorruptionError(
                    f"terminal state disappeared while committing run {state.run_id}"
                )
            stored_state = _decode_record(stored_row, RunState, "terminal state")
            stored_intent = (
                _decode_record(
                    stored_intent_row,
                    ControlledRunIntent,
                    "controlled run intent",
                )
                if stored_intent_row is not None
                else None
            )
            stored_invocation = (
                _decode_record(stored_invocation_row, Invocation, "invocation")
                if stored_invocation_row is not None
                else None
            )
            _validate_persisted_binding(stored_state, stored_invocation, stored_intent)
            _validate_invocation_payload_anchor(
                stored_state,
                stored_row,
                stored_invocation_row,
            )
            _validate_controlled_intent_payload_anchor(
                stored_state,
                stored_row,
                stored_intent_row,
            )
            _validate_event_journal(
                connection,
                stored_state,
                error_type=RunStoreCorruptionError,
            )
            if stored_state != state or stored_row["payload_json"] != payload:
                raise RunStoreCorruptionError(
                    f"terminal state changed while committing run {state.run_id}"
                )

    def read_controlled_intent(self, run_id: UUID) -> ControlledRunIntent | None:
        with self._connection() as connection:
            row = _select_controlled_intent(connection, run_id)
        if row is None:
            return None
        return _decode_record(row, ControlledRunIntent, "controlled run intent")

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
            intent_row = _select_controlled_intent(connection, run_id)
            intent = (
                _decode_record(intent_row, ControlledRunIntent, "controlled run intent")
                if intent_row is not None
                else None
            )
            _validate_persisted_binding(state, invocation, intent)
            _validate_invocation_payload_anchor(state, row, invocation_row)
            _validate_controlled_intent_payload_anchor(state, row, intent_row)
            _validate_event_journal(
                connection,
                state,
                error_type=RunStoreCorruptionError,
            )
            return state

    def read_controlled_lifecycle(self, run_id: UUID) -> ControlledRunLifecycle:
        """Read and verify one candidate controlled identity from one SQLite snapshot."""

        with self._connection() as connection:
            connection.execute("BEGIN")
            _require_current_layout(connection)
            intent_row = _select_controlled_intent(connection, run_id)
            invocation_row = _select_invocation(connection, run_id)
            terminal_row = _select_run_state(connection, run_id)
            intent = (
                _decode_record(intent_row, ControlledRunIntent, "controlled run intent")
                if intent_row is not None
                else None
            )
            invocation = (
                _decode_record(invocation_row, Invocation, "invocation")
                if invocation_row is not None
                else None
            )
            terminal = (
                _decode_record(terminal_row, RunState, "terminal state")
                if terminal_row is not None
                else None
            )
            _validate_controlled_lifecycle(
                connection,
                run_id=run_id,
                intent=intent,
                intent_row=intent_row,
                invocation=invocation,
                invocation_row=invocation_row,
                terminal=terminal,
                terminal_row=terminal_row,
            )
            return ControlledRunLifecycle(
                intent=intent,
                invocation=invocation,
                terminal=terminal,
            )

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
            connection.execute("BEGIN IMMEDIATE")
            invocation_columns = _read_column_layout(connection, "harness_invocations")
            state_columns = _read_column_layout(connection, "harness_run_states")
            intent_columns = _read_column_layout(
                connection,
                "harness_controlled_run_intents",
            )
            if not invocation_columns and not state_columns and not intent_columns:
                _create_current_tables(connection)
            elif (
                invocation_columns == _LEGACY_INVOCATION_COLUMNS
                and state_columns == _LEGACY_STATE_COLUMNS
                and not intent_columns
            ):
                _require_table_layout(
                    connection,
                    table="harness_invocations",
                    columns=_LEGACY_INVOCATION_COLUMNS,
                )
                _require_table_layout(
                    connection,
                    table="harness_run_states",
                    columns=_LEGACY_STATE_COLUMNS,
                )
                _migrate_legacy_tables(connection)
            elif (
                invocation_columns == _CURRENT_INVOCATION_COLUMNS
                and state_columns == _CURRENT_STATE_COLUMNS
                and intent_columns == _CURRENT_INTENT_COLUMNS
            ):
                pass
            else:
                raise RunStoreCorruptionError(
                    "run database has an unsupported authority table layout"
                )
            _require_current_layout(connection)


def _create_current_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE harness_invocations (
            run_id TEXT NOT NULL PRIMARY KEY,
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
        CREATE TABLE harness_controlled_run_intents (
            run_id TEXT NOT NULL PRIMARY KEY,
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
    connection.execute(
        """
        CREATE TABLE harness_run_states (
            run_id TEXT NOT NULL PRIMARY KEY,
            invocation_digest TEXT,
            skill_id TEXT NOT NULL,
            skill_version TEXT NOT NULL,
            max_attempts INTEGER NOT NULL,
            status TEXT NOT NULL,
            invocation_payload_sha256 TEXT,
            controlled_intent_payload_sha256 TEXT,
            payload_json BLOB NOT NULL,
            payload_sha256 TEXT NOT NULL
        )
        """
    )


def _migrate_legacy_tables(connection: sqlite3.Connection) -> None:
    invocation_rows = connection.execute(
        """
        SELECT run_id, invocation_digest, skill_id, skill_version,
               max_attempts, payload_json, payload_sha256
        FROM harness_invocations
        ORDER BY run_id
        """
    ).fetchall()
    state_rows = connection.execute(
        """
        SELECT run_id, invocation_digest, skill_id, skill_version,
               max_attempts, status, invocation_payload_sha256,
               payload_json, payload_sha256
        FROM harness_run_states
        ORDER BY run_id
        """
    ).fetchall()
    invocation_rows_by_id = {row["run_id"]: row for row in invocation_rows}
    invocations = {
        str(invocation.run_id): invocation
        for row in invocation_rows
        for invocation in (_decode_record(row, Invocation, "invocation"),)
    }
    for row in state_rows:
        state = _decode_record(row, RunState, "terminal state")
        invocation = invocations.get(str(state.run_id))
        _validate_persisted_binding(state, invocation, None)
        _validate_invocation_payload_anchor(
            state,
            row,
            invocation_rows_by_id.get(str(state.run_id)),
        )
        _validate_event_journal(
            connection,
            state,
            error_type=RunStoreCorruptionError,
        )

    connection.execute("ALTER TABLE harness_invocations RENAME TO harness_invocations_legacy")
    connection.execute("ALTER TABLE harness_run_states RENAME TO harness_run_states_legacy")
    _create_current_tables(connection)
    connection.executemany(
        """
        INSERT OR ABORT INTO harness_invocations (
            run_id, invocation_digest, skill_id, skill_version,
            max_attempts, payload_json, payload_sha256
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (tuple(row) for row in invocation_rows),
    )
    connection.executemany(
        """
        INSERT OR ABORT INTO harness_run_states (
            run_id, invocation_digest, skill_id, skill_version,
            max_attempts, status, invocation_payload_sha256,
            controlled_intent_payload_sha256, payload_json, payload_sha256
        ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
        """,
        (tuple(row) for row in state_rows),
    )
    connection.execute("DROP TABLE harness_invocations_legacy")
    connection.execute("DROP TABLE harness_run_states_legacy")


def _require_current_layout(connection: sqlite3.Connection) -> None:
    for table, columns in (
        ("harness_invocations", _CURRENT_INVOCATION_COLUMNS),
        ("harness_controlled_run_intents", _CURRENT_INTENT_COLUMNS),
        ("harness_run_states", _CURRENT_STATE_COLUMNS),
    ):
        _require_table_layout(connection, table=table, columns=columns)


def _read_column_layout(
    connection: sqlite3.Connection,
    table: str,
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        tuple(row[key] for key in ("cid", "name", "type", "notnull", "dflt_value", "pk", "hidden"))
        for row in connection.execute(f"PRAGMA table_xinfo({table})").fetchall()
    )


def _read_index_layouts(
    connection: sqlite3.Connection,
    table: str,
) -> tuple[tuple[object, ...], ...]:
    indexes = []
    for row in connection.execute(f"PRAGMA index_list({table})").fetchall():
        columns = tuple(
            tuple(column[key] for key in ("seqno", "cid", "name", "desc", "coll", "key"))
            for column in connection.execute(
                """
                SELECT seqno, cid, name, desc, coll, key
                FROM pragma_index_xinfo(?)
                ORDER BY seqno
                """,
                (row["name"],),
            ).fetchall()
        )
        indexes.append((row["origin"], row["unique"], row["partial"], columns))
    return tuple(indexes)


def _require_table_layout(
    connection: sqlite3.Connection,
    *,
    table: str,
    columns: tuple[tuple[object, ...], ...],
) -> None:
    metadata = tuple(
        tuple(row[key] for key in ("schema", "name", "type", "ncol", "wr", "strict"))
        for row in connection.execute(f"PRAGMA table_list({table})").fetchall()
    )
    table_row = connection.execute(
        "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    triggers = connection.execute(
        "SELECT name FROM sqlite_schema WHERE type = 'trigger' AND tbl_name = ?",
        (table,),
    ).fetchall()
    if (
        metadata != (("main", table, "table", len(columns), 0, 0),)
        or table_row is None
        or _read_column_layout(connection, table) != columns
        or Counter(_read_index_layouts(connection, table)) != Counter(_PRIMARY_KEY_INDEX)
        or _TABLE_FORBIDDEN_SEMANTICS.search(_SQL_QUOTED_OR_COMMENT.sub(" ", table_row["sql"]))
        or connection.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        or triggers
    ):
        raise RunStoreCorruptionError(
            f"run database table {table} has an unsupported authority layout"
        )


def _canonical_bytes(value: ControlledRunIntent | Invocation | RunState) -> bytes:
    return json.dumps(
        value.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _select_controlled_intent(
    connection: sqlite3.Connection,
    run_id: UUID,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT run_id, skill_id, skill_version, input_schema, phase,
               raw_parameters_sha256, effective_parameters_sha256,
               descriptor_sha256, implementation_sha256, qualification_sha256,
               max_attempts, payload_json, payload_sha256
        FROM harness_controlled_run_intents
        WHERE run_id = ?
        """,
        (str(run_id),),
    ).fetchone()


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
               controlled_intent_payload_sha256, payload_json, payload_sha256
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
    if isinstance(record, ControlledRunIntent):
        expected_metadata: dict[str, object] = {
            "run_id": str(record.run_id),
            "skill_id": record.skill_id,
            "skill_version": record.skill_version,
            "input_schema": record.input_schema,
            "phase": record.phase,
            "raw_parameters_sha256": record.raw_parameters_sha256,
            "effective_parameters_sha256": record.effective_parameters_sha256,
            "descriptor_sha256": record.descriptor_sha256,
            "implementation_sha256": record.implementation_sha256,
            "qualification_sha256": record.qualification_sha256,
            "max_attempts": record.max_attempts,
        }
    else:
        expected_metadata = {
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


def _validate_state_binding(
    state: RunState,
    invocation: Invocation | None,
    intent: ControlledRunIntent | None,
) -> None:
    if state.invocation_digest is None:
        if invocation is not None:
            raise RunStoreConflictError(
                "preflight terminal state cannot be bound to a persisted invocation"
            )
        if intent is not None:
            if intent.phase not in {"input_validation", "preflight"}:
                raise RunStoreConflictError(
                    "preflight terminal state requires a preflight controlled intent"
                )
            expected = {
                "run_id": intent.run_id,
                "skill_id": intent.skill_id,
                "skill_version": intent.skill_version,
            }
            for field, value in expected.items():
                if getattr(state, field) != value:
                    raise RunStoreConflictError(
                        f"preflight RunState {field} does not match its controlled intent"
                    )
        if (
            state.status not in {RunStatus.BLOCKED, RunStatus.FAILED}
            or state.blocker is None
            or state.blocker.stage != "preflight"
            or state.blocker.retryable
            or state.blocker.unknowns
            or state.output is not None
            or state.attempt != 0
            or state.max_attempts != 0
            or state.ended_at is None
            or len(state.events) != 2
            or state.events[0].seq != 1
            or state.events[0].timestamp != state.started_at
            or state.events[0].attempt != 0
            or state.events[0].from_status is not None
            or state.events[0].to_status is not RunStatus.RUNNING
            or not state.events[0].stage.endswith("preflight")
            or state.events[0].artifact_refs
            or state.events[1].seq != 2
            or state.events[1].timestamp != state.ended_at
            or state.events[1].attempt != 0
            or state.events[1].from_status is not RunStatus.RUNNING
            or state.events[1].to_status is not state.status
            or not state.events[1].stage.endswith("preflight")
            or state.events[1].artifact_refs != state.artifacts
            or state.artifacts != state.blocker.artifact_refs
        ):
            raise RunStoreConflictError("preflight terminal state has invalid terminal semantics")
        return
    if intent is not None:
        if invocation is None:
            raise RunStoreConflictError(
                "controlled execution terminal state requires a persisted invocation"
            )
        _validate_controlled_intent_invocation_binding(intent, invocation)
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


def _validate_controlled_intent_invocation_binding(
    intent: ControlledRunIntent,
    invocation: Invocation,
) -> None:
    expected = {
        "run_id": invocation.run_id,
        "skill_id": invocation.skill_id,
        "skill_version": invocation.skill_version,
        "max_attempts": invocation.max_attempts,
    }
    for field, value in expected.items():
        if getattr(intent, field) != value:
            raise RunStoreConflictError(
                f"controlled run intent {field} does not match its invocation"
            )
    effective_parameters_sha256 = sha256(
        json.dumps(
            invocation.effective_parameters,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    if intent.effective_parameters_sha256 != effective_parameters_sha256:
        raise RunStoreConflictError(
            "controlled run intent effective input does not match its invocation"
        )
    if intent.phase != "validated_input" or intent.dependencies != invocation.dependencies:
        raise RunStoreConflictError(
            "controlled run intent dependencies do not match its invocation"
        )


def _validate_persisted_binding(
    state: RunState,
    invocation: Invocation | None,
    intent: ControlledRunIntent | None,
) -> None:
    try:
        _validate_state_binding(state, invocation, intent)
    except RunStoreConflictError as error:
        raise RunStoreCorruptionError(
            f"stored terminal state for run {state.run_id} has inconsistent binding: {error}"
        ) from error


def _validate_controlled_lifecycle(
    connection: sqlite3.Connection,
    *,
    run_id: UUID,
    intent: ControlledRunIntent | None,
    intent_row: sqlite3.Row | None,
    invocation: Invocation | None,
    invocation_row: sqlite3.Row | None,
    terminal: RunState | None,
    terminal_row: sqlite3.Row | None,
) -> None:
    if intent is None:
        if terminal is not None:
            assert terminal_row is not None
            _validate_persisted_binding(terminal, invocation, None)
            _validate_invocation_payload_anchor(terminal, terminal_row, invocation_row)
            _validate_controlled_intent_payload_anchor(terminal, terminal_row, None)
            _validate_event_journal(
                connection,
                terminal,
                error_type=RunStoreCorruptionError,
            )
            return
        if invocation is not None:
            _validate_incomplete_event_journal(connection, invocation)
            return
        if _run_has_events(connection, run_id):
            raise RunStoreCorruptionError(
                f"controlled lifecycle for run {run_id} has events without its invocation"
            )
        return
    if invocation is not None:
        try:
            _validate_controlled_intent_invocation_binding(intent, invocation)
        except RunStoreConflictError as error:
            raise RunStoreCorruptionError(
                f"controlled lifecycle for run {run_id} has inconsistent invocation: {error}"
            ) from error
    if terminal is not None:
        assert terminal_row is not None
        _validate_persisted_binding(terminal, invocation, intent)
        _validate_invocation_payload_anchor(terminal, terminal_row, invocation_row)
        _validate_controlled_intent_payload_anchor(terminal, terminal_row, intent_row)
        _validate_event_journal(
            connection,
            terminal,
            error_type=RunStoreCorruptionError,
        )
        return
    if invocation is None:
        if _run_has_events(connection, run_id):
            raise RunStoreCorruptionError(
                f"controlled lifecycle for run {run_id} has events without its invocation"
            )
        return
    _validate_incomplete_event_journal(connection, invocation)


def _validate_incomplete_event_journal(
    connection: sqlite3.Connection,
    invocation: Invocation,
) -> None:
    if not _event_table_exists(connection):
        return
    try:
        verify_persisted_event_chain(
            connection,
            run_id=invocation.run_id,
            skill_id=invocation.skill_id,
            skill_version=invocation.skill_version,
            minimum_attempt=1,
            maximum_attempt=invocation.max_attempts,
        )
    except EventJournalCorruptionError as error:
        raise RunStoreCorruptionError(
            f"controlled run {invocation.run_id} has an invalid event journal: {error}"
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


def _validate_controlled_intent_payload_anchor(
    state: RunState,
    state_row: sqlite3.Row,
    intent_row: sqlite3.Row | None,
) -> None:
    expected = state_row["controlled_intent_payload_sha256"]
    actual = intent_row["payload_sha256"] if intent_row is not None else None
    if expected != actual:
        raise RunStoreCorruptionError(
            f"stored terminal state for run {state.run_id} has inconsistent "
            "controlled intent payload"
        )


def _validate_event_journal(
    connection: sqlite3.Connection,
    state: RunState,
    *,
    error_type: type[RunStoreConflictError] | type[RunStoreCorruptionError],
) -> None:
    if not _event_table_exists(connection):
        raise error_type(f"run {state.run_id} event journal table is missing")
    try:
        stored_events = verify_persisted_event_chain(
            connection,
            run_id=state.run_id,
            skill_id=state.skill_id,
            skill_version=state.skill_version,
        )
    except EventJournalCorruptionError as error:
        detail = str(error).replace("event_id ", "event ")
        raise error_type(f"run {state.run_id} event journal is invalid: {detail}") from error
    if len(stored_events) != len(state.events):
        raise error_type(
            f"run {state.run_id} event journal has {len(stored_events)} events but "
            f"RunState declares {len(state.events)}"
        )
    for stored, event in zip(stored_events, state.events, strict=True):
        if stored.envelope.event != event:
            raise error_type(
                f"run {state.run_id} event {event.seq} does not canonically match RunState.events"
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
