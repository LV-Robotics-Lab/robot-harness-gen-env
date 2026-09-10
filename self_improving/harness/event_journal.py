"""Durable append-only authority for replayable Harness run events."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from .events import RunEvent
from .schemas import Event, RunStatus

_RUN_EVENTS_TABLE_SQL = """
CREATE TABLE run_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    skill_id TEXT NOT NULL,
    skill_version TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    stage TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    envelope_json TEXT NOT NULL,
    UNIQUE(run_id, seq) ON CONFLICT ABORT
)
"""
_RUN_EVENTS_BY_RUN_SQL = """
CREATE INDEX run_events_by_run ON run_events(run_id, event_id)
"""
_RUN_EVENT_COLUMNS = (
    (0, "event_id", "INTEGER", 0, None, 1, 0),
    (1, "run_id", "TEXT", 1, None, 0, 0),
    (2, "seq", "INTEGER", 1, None, 0, 0),
    (3, "skill_id", "TEXT", 1, None, 0, 0),
    (4, "skill_version", "TEXT", 1, None, 0, 0),
    (5, "timestamp", "TEXT", 1, None, 0, 0),
    (6, "stage", "TEXT", 1, None, 0, 0),
    (7, "attempt", "INTEGER", 1, None, 0, 0),
    (8, "from_status", "TEXT", 0, None, 0, 0),
    (9, "to_status", "TEXT", 1, None, 0, 0),
    (10, "envelope_json", "TEXT", 1, None, 0, 0),
)
_RUN_EVENT_INDEXES = (
    (
        "run_events_by_run",
        "c",
        0,
        0,
        (
            (0, 1, "run_id", 0, "BINARY", 1),
            (1, 0, "event_id", 0, "BINARY", 1),
            (2, -1, None, 0, "BINARY", 0),
        ),
        "CREATE INDEX run_events_by_run ON run_events(run_id, event_id)",
    ),
    (
        "sqlite_autoindex_run_events_1",
        "u",
        1,
        0,
        (
            (0, 1, "run_id", 0, "BINARY", 1),
            (1, 2, "seq", 0, "BINARY", 1),
            (2, -1, None, 0, "BINARY", 0),
        ),
        None,
    ),
)
_EVENT_ROW_FIELDS = (
    "event_id",
    "run_id",
    "seq",
    "skill_id",
    "skill_version",
    "timestamp",
    "stage",
    "attempt",
    "from_status",
    "to_status",
    "envelope_json",
)


@dataclass(frozen=True)
class StoredRunEvent:
    """A run event paired with its global resumable cursor."""

    event_id: int
    envelope: RunEvent


@dataclass(frozen=True)
class EventPage:
    """One ordered journal page and the cursor safe for the next read."""

    events: tuple[StoredRunEvent, ...]
    last_event_id: int
    has_more: bool


class EventJournalConflictError(RuntimeError):
    """A new write conflicts with immutable history or its lifecycle."""


class EventJournalCorruptionError(RuntimeError):
    """Persisted bytes cannot reconstruct their declared RunEvent."""


class SQLiteEventJournal:
    """Durable SQLite journal with per-run lifecycle validation and cursor replay."""

    def __init__(self, path: Path, *, busy_timeout_ms: int = 5_000) -> None:
        if busy_timeout_ms <= 0:
            raise ValueError("busy_timeout_ms must be positive")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._busy_timeout_ms = busy_timeout_ms
        self._condition = threading.Condition()
        self._initialize()

    def publish(self, event: RunEvent) -> None:
        """Commit one event, ignoring only an exact idempotent duplicate."""

        canonical = _canonical_envelope(event)
        inserted = False
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _require_event_journal_layout(connection)
            predecessor_rows = _select_run_event_rows(connection, event.run_id)
            _validate_stored_chain(predecessor_rows)
            predecessor_chain = tuple(_row_snapshot(row) for row in predecessor_rows)
            existing = connection.execute(
                """
                SELECT event_id, run_id, seq, skill_id, skill_version, timestamp,
                       stage, attempt, from_status, to_status, envelope_json
                FROM run_events
                WHERE run_id = ? AND seq = ?
                """,
                (str(event.run_id), event.event.seq),
            ).fetchone()
            if existing is not None:
                _stored_event(existing)
                if existing["envelope_json"] != canonical:
                    raise EventJournalConflictError(
                        f"run {event.run_id} seq {event.event.seq} already has different content"
                    )
            else:
                previous = predecessor_rows[-1] if predecessor_rows else None
                _validate_next(event, previous)
                connection.execute(
                    """
                    INSERT OR ABORT INTO run_events (
                        run_id, seq, skill_id, skill_version, timestamp, stage,
                        attempt, from_status, to_status, envelope_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(event.run_id),
                        event.event.seq,
                        event.skill_id,
                        event.skill_version,
                        event.event.timestamp.isoformat(),
                        event.event.stage,
                        event.event.attempt,
                        (
                            event.event.from_status.value
                            if event.event.from_status is not None
                            else None
                        ),
                        event.event.to_status.value,
                        canonical,
                    ),
                )
                _require_event_journal_layout(connection)
                stored = connection.execute(
                    """
                    SELECT event_id, run_id, seq, skill_id, skill_version, timestamp,
                           stage, attempt, from_status, to_status, envelope_json
                    FROM run_events
                    WHERE run_id = ? AND seq = ?
                    """,
                    (str(event.run_id), event.event.seq),
                ).fetchone()
                if stored is None or _stored_event(stored).envelope != event:
                    raise EventJournalCorruptionError(
                        f"event changed while committing run {event.run_id} seq {event.event.seq}"
                    )
                committed_rows = _select_run_event_rows(connection, event.run_id)
                _validate_stored_chain(committed_rows)
                committed_chain = tuple(_row_snapshot(row) for row in committed_rows)
                if committed_chain != (*predecessor_chain, _row_snapshot(stored)):
                    raise EventJournalCorruptionError(
                        f"event chain changed while committing run {event.run_id}"
                    )
                inserted = True
        if inserted:
            with self._condition:
                self._condition.notify_all()

    def read(
        self,
        *,
        after_event_id: int = 0,
        run_id: UUID | None = None,
        limit: int = 200,
    ) -> EventPage:
        """Read committed events after a global cursor, optionally for one run."""

        if after_event_id < 0:
            raise ValueError("after_event_id must be nonnegative")
        if limit <= 0:
            raise ValueError("limit must be positive")
        clauses = ["event_id > ?"]
        parameters: list[object] = [after_event_id]
        if run_id is not None:
            clauses.append("run_id = ?")
            parameters.append(str(run_id))
        parameters.append(limit + 1)
        query = (
            "SELECT event_id, run_id, seq, skill_id, skill_version, timestamp, stage, "
            "attempt, from_status, to_status, envelope_json FROM run_events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY event_id LIMIT ?"
        )
        with self._connection() as connection:
            connection.execute("BEGIN")
            _require_event_journal_layout(connection)
            rows = connection.execute(query, parameters).fetchall()
        has_more = len(rows) > limit
        selected = rows[:limit]
        events = tuple(_stored_event(row) for row in selected)
        last_event_id = events[-1].event_id if events else after_event_id
        return EventPage(
            events=events,
            last_event_id=last_event_id,
            has_more=has_more,
        )

    def wait(
        self,
        *,
        after_event_id: int,
        run_id: UUID | None = None,
        limit: int = 200,
        timeout_s: float = 15.0,
    ) -> EventPage:
        """Wait for an in-process publish, returning immediately on existing data."""

        if timeout_s < 0:
            raise ValueError("timeout_s must be nonnegative")
        with self._condition:
            page = self.read(
                after_event_id=after_event_id,
                run_id=run_id,
                limit=limit,
            )
            if page.events or timeout_s == 0:
                return page
            self._condition.wait(timeout=timeout_s)
            return self.read(
                after_event_id=after_event_id,
                run_id=run_id,
                limit=limit,
            )

    def latest_event_id(self) -> int:
        """Return the latest committed global cursor, or zero for an empty journal."""

        with self._connection() as connection:
            connection.execute("BEGIN")
            _require_event_journal_layout(connection)
            row = connection.execute(
                "SELECT COALESCE(MAX(event_id), 0) AS event_id FROM run_events"
            ).fetchone()
        return int(row["event_id"])

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
            existing = connection.execute(
                "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'run_events'"
            ).fetchone()
            if existing is None:
                connection.execute(_RUN_EVENTS_TABLE_SQL)
                connection.execute(_RUN_EVENTS_BY_RUN_SQL)
            _require_event_journal_layout(connection)


def _require_event_journal_layout(connection: sqlite3.Connection) -> None:
    metadata = tuple(
        tuple(row[key] for key in ("schema", "name", "type", "ncol", "wr", "strict"))
        for row in connection.execute("PRAGMA table_list(run_events)").fetchall()
    )
    table_row = connection.execute(
        "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = 'run_events'"
    ).fetchone()
    columns = tuple(
        tuple(row[key] for key in ("cid", "name", "type", "notnull", "dflt_value", "pk", "hidden"))
        for row in connection.execute("PRAGMA table_xinfo(run_events)").fetchall()
    )
    indexes = []
    for row in connection.execute("PRAGMA index_list(run_events)").fetchall():
        index_row = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type = 'index' AND name = ?",
            (row["name"],),
        ).fetchone()
        index_columns = tuple(
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
        indexes.append(
            (
                row["name"],
                row["origin"],
                row["unique"],
                row["partial"],
                index_columns,
                (
                    _normalize_sql(index_row["sql"])
                    if index_row is not None and index_row["sql"] is not None
                    else None
                ),
            )
        )
    triggers = connection.execute(
        "SELECT name FROM sqlite_schema WHERE type = 'trigger' AND tbl_name = 'run_events'"
    ).fetchall()
    if (
        metadata != (("main", "run_events", "table", len(_RUN_EVENT_COLUMNS), 0, 0),)
        or table_row is None
        or _normalize_sql(table_row["sql"]) != _normalize_sql(_RUN_EVENTS_TABLE_SQL)
        or columns != _RUN_EVENT_COLUMNS
        or Counter(indexes) != Counter(_RUN_EVENT_INDEXES)
        or connection.execute("PRAGMA foreign_key_list(run_events)").fetchall()
        or triggers
    ):
        raise EventJournalCorruptionError("run_events has an unsupported authority layout")


def _normalize_sql(value: str) -> str:
    return " ".join(value.split())


def _row_snapshot(row: sqlite3.Row) -> tuple[object, ...]:
    return tuple(row[field] for field in _EVENT_ROW_FIELDS)


def _select_run_event_rows(
    connection: sqlite3.Connection,
    run_id: UUID,
) -> tuple[sqlite3.Row, ...]:
    return tuple(
        connection.execute(
            """
            SELECT event_id, run_id, seq, skill_id, skill_version, timestamp,
                   stage, attempt, from_status, to_status, envelope_json
            FROM run_events
            WHERE run_id = ?
            ORDER BY seq
            """,
            (str(run_id),),
        ).fetchall()
    )


def _validate_stored_chain(rows: tuple[sqlite3.Row, ...]) -> tuple[StoredRunEvent, ...]:
    previous: sqlite3.Row | None = None
    stored_events = []
    for row in rows:
        stored = _stored_event(row)
        try:
            _validate_next(stored.envelope, previous)
        except EventJournalConflictError as error:
            raise EventJournalCorruptionError(
                f"stored event chain is invalid at event_id {stored.event_id}: {error}"
            ) from error
        stored_events.append(stored)
        previous = row
    return tuple(stored_events)


def verify_persisted_event_chain(
    connection: sqlite3.Connection,
    *,
    run_id: UUID,
    skill_id: str,
    skill_version: str,
    minimum_attempt: int | None = None,
    maximum_attempt: int | None = None,
) -> tuple[StoredRunEvent, ...]:
    """Verify one run's exact persisted event chain inside the caller transaction."""

    _require_event_journal_layout(connection)
    rows = _select_run_event_rows(connection, run_id)
    stored_events = _validate_stored_chain(rows)
    for stored in stored_events:
        envelope = stored.envelope
        if (
            envelope.run_id != run_id
            or envelope.skill_id != skill_id
            or envelope.skill_version != skill_version
        ):
            raise EventJournalCorruptionError(
                f"event_id {stored.event_id} does not canonically match run {run_id}"
            )
        if minimum_attempt is not None and envelope.event.attempt < minimum_attempt:
            raise EventJournalCorruptionError(
                f"event_id {stored.event_id} attempt is below the run minimum"
            )
        if maximum_attempt is not None and envelope.event.attempt > maximum_attempt:
            raise EventJournalCorruptionError(
                f"event_id {stored.event_id} attempt exceeds the run maximum"
            )
    return stored_events


def _validate_next(event: RunEvent, previous: sqlite3.Row | None) -> None:
    current = event.event
    if previous is None:
        if (
            current.seq != 1
            or current.from_status is not None
            or current.to_status != RunStatus.RUNNING
        ):
            raise EventJournalConflictError(
                "the first event must be seq 1 and transition null -> running"
            )
        return
    expected_seq = int(previous["seq"]) + 1
    if current.seq != expected_seq:
        raise EventJournalConflictError(
            f"expected seq {expected_seq} for run {event.run_id}, got {current.seq}"
        )
    if event.skill_id != previous["skill_id"] or event.skill_version != previous["skill_version"]:
        raise EventJournalConflictError("run Skill identity cannot change")
    previous_status = RunStatus(previous["to_status"])
    if previous_status != RunStatus.RUNNING:
        raise EventJournalConflictError("events cannot follow a terminal transition")
    if current.from_status != previous_status:
        raise EventJournalConflictError("event status transitions must form one continuous chain")
    previous_timestamp = datetime.fromisoformat(previous["timestamp"])
    if current.timestamp < previous_timestamp:
        raise EventJournalConflictError("event timestamps must be nondecreasing")
    previous_attempt = int(previous["attempt"])
    if current.attempt < previous_attempt or current.attempt > previous_attempt + 1:
        raise EventJournalConflictError(
            "event attempts must be nondecreasing and increment by at most 1"
        )


def _canonical_envelope(event: RunEvent) -> str:
    return json.dumps(
        {
            "run_id": str(event.run_id),
            "skill_id": event.skill_id,
            "skill_version": event.skill_version,
            "event": event.event.model_dump(mode="json"),
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _stored_event(row: sqlite3.Row) -> StoredRunEvent:
    try:
        payload = json.loads(row["envelope_json"])
        envelope = RunEvent(
            run_id=UUID(payload["run_id"]),
            skill_id=payload["skill_id"],
            skill_version=payload["skill_version"],
            event=Event.model_validate(payload["event"]),
        )
    except (KeyError, TypeError, ValueError, ValidationError) as error:
        raise EventJournalCorruptionError(
            f"cannot reconstruct event_id {row['event_id']}: {error}"
        ) from error
    if row["envelope_json"] != _canonical_envelope(envelope):
        raise EventJournalCorruptionError(f"event_id {row['event_id']} is not canonical JSON")
    expected_metadata = (
        str(envelope.run_id),
        envelope.event.seq,
        envelope.skill_id,
        envelope.skill_version,
        envelope.event.timestamp.isoformat(),
        envelope.event.stage,
        envelope.event.attempt,
        (envelope.event.from_status.value if envelope.event.from_status is not None else None),
        envelope.event.to_status.value,
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
        raise EventJournalCorruptionError(f"event_id {row['event_id']} has inconsistent metadata")
    return StoredRunEvent(event_id=int(row["event_id"]), envelope=envelope)
