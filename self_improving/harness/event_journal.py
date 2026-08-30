"""Durable append-only authority for replayable Harness run events."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from .events import RunEvent
from .schemas import Event, RunStatus


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
    """SQLite WAL journal with per-run lifecycle validation and cursor replay."""

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
            existing = connection.execute(
                "SELECT envelope_json FROM run_events WHERE run_id = ? AND seq = ?",
                (str(event.run_id), event.event.seq),
            ).fetchone()
            if existing is not None:
                if existing["envelope_json"] != canonical:
                    raise EventJournalConflictError(
                        f"run {event.run_id} seq {event.event.seq} already has different content"
                    )
            else:
                previous = connection.execute(
                    """
                    SELECT skill_id, skill_version, seq, timestamp, attempt, to_status
                    FROM run_events
                    WHERE run_id = ?
                    ORDER BY seq DESC
                    LIMIT 1
                    """,
                    (str(event.run_id),),
                ).fetchone()
                _validate_next(event, previous)
                connection.execute(
                    """
                    INSERT INTO run_events (
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
            "SELECT event_id, envelope_json FROM run_events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY event_id LIMIT ?"
        )
        with self._connection() as connection:
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
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS run_events (
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
                    UNIQUE(run_id, seq)
                );
                CREATE INDEX IF NOT EXISTS run_events_by_run
                    ON run_events(run_id, event_id);
                """
            )


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
    if (
        event.skill_id != previous["skill_id"]
        or event.skill_version != previous["skill_version"]
    ):
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
    return StoredRunEvent(event_id=int(row["event_id"]), envelope=envelope)
