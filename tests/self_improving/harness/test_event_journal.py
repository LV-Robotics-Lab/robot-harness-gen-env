from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Condition
from threading import Event as ThreadEvent
from uuid import UUID

import pytest

from self_improving.harness.event_journal import (
    EventJournalConflictError,
    EventJournalCorruptionError,
    SQLiteEventJournal,
)
from self_improving.harness.events import RunEvent
from self_improving.harness.schemas import Event, RunStatus

RUN_ID = UUID("12345678-1234-4234-9234-123456789abc")
OTHER_RUN_ID = UUID("22345678-1234-4234-9234-123456789abc")
START = datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc)


def _envelope(
    seq: int,
    *,
    run_id: UUID = RUN_ID,
    stage: str | None = None,
    attempt: int = 1,
    from_status: RunStatus | None = None,
    to_status: RunStatus = RunStatus.RUNNING,
    timestamp: datetime | None = None,
    skill_id: str = "text2env.compile",
) -> RunEvent:
    return RunEvent(
        run_id=run_id,
        skill_id=skill_id,
        skill_version="1.0.0",
        event=Event(
            seq=seq,
            timestamp=timestamp or START + timedelta(seconds=seq),
            stage=stage or f"stage.{seq}",
            attempt=attempt,
            from_status=from_status,
            to_status=to_status,
            artifact_refs=(),
        ),
    )


def test_sqlite_event_journal_replays_after_restart_and_filters_runs(tmp_path: Path) -> None:
    path = tmp_path / "events.sqlite3"
    journal = SQLiteEventJournal(path)
    first = _envelope(1)
    second = _envelope(2, from_status=RunStatus.RUNNING)
    other = _envelope(1, run_id=OTHER_RUN_ID)

    journal.publish(first)
    journal.publish(other)
    journal.publish(second)
    journal.publish(first)

    assert journal.latest_event_id() == 3
    page = journal.read(after_event_id=0, limit=2)
    assert [item.event_id for item in page.events] == [1, 2]
    assert page.last_event_id == 2
    assert page.has_more is True

    restarted = SQLiteEventJournal(path)
    run_page = restarted.read(after_event_id=0, run_id=RUN_ID, limit=10)
    assert [item.envelope for item in run_page.events] == [first, second]
    assert [item.event_id for item in run_page.events] == [1, 3]
    assert run_page.last_event_id == 3
    assert run_page.has_more is False
    empty = restarted.read(after_event_id=3, run_id=RUN_ID, limit=10)
    assert empty.events == ()
    assert empty.last_event_id == 3
    assert empty.has_more is False


def test_sqlite_event_journal_waits_for_committed_event_without_polling(tmp_path: Path) -> None:
    journal = SQLiteEventJournal(tmp_path / "events.sqlite3")
    waiting_started = ThreadEvent()

    class ObservableCondition(Condition):
        def wait(self, timeout: float | None = None) -> bool:
            waiting_started.set()
            return super().wait(timeout)

    journal._condition = ObservableCondition()

    with ThreadPoolExecutor(max_workers=1) as executor:
        waiting = executor.submit(journal.wait, after_event_id=0, timeout_s=1.0)
        assert waiting_started.wait(timeout=1)
        journal.publish(_envelope(1))
        page = waiting.result(timeout=2)

    assert len(page.events) == 1
    assert page.events[0].event_id == 1
    assert journal.wait(after_event_id=1, timeout_s=0).events == ()


def test_sqlite_event_journal_is_idempotent_but_rejects_conflicts(tmp_path: Path) -> None:
    journal = SQLiteEventJournal(tmp_path / "events.sqlite3")
    first = _envelope(1)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(journal.publish, (first, first)))
    assert results == [None, None]
    assert journal.latest_event_id() == 1

    with pytest.raises(EventJournalConflictError, match="different content"):
        journal.publish(_envelope(1, stage="changed"))
    with pytest.raises(EventJournalConflictError, match="expected seq 2"):
        journal.publish(_envelope(3, from_status=RunStatus.RUNNING))
    with pytest.raises(EventJournalConflictError, match="null -> running"):
        journal.publish(
            _envelope(
                1,
                run_id=OTHER_RUN_ID,
                from_status=RunStatus.RUNNING,
            )
        )


@pytest.mark.parametrize(
    ("events", "message"),
    [
        (
            (
                _envelope(1),
                _envelope(2, from_status=None),
            ),
            "continuous chain",
        ),
        (
            (
                _envelope(1),
                _envelope(
                    2,
                    from_status=RunStatus.RUNNING,
                    timestamp=START,
                ),
            ),
            "nondecreasing",
        ),
        (
            (
                _envelope(1),
                _envelope(2, from_status=RunStatus.RUNNING, attempt=3),
            ),
            "increment by at most 1",
        ),
        (
            (
                _envelope(1),
                _envelope(
                    2,
                    from_status=RunStatus.RUNNING,
                    to_status=RunStatus.SUCCEEDED,
                ),
                _envelope(3, from_status=RunStatus.SUCCEEDED),
            ),
            "terminal",
        ),
        (
            (
                _envelope(1),
                _envelope(
                    2,
                    from_status=RunStatus.RUNNING,
                    skill_id="text2env.replay",
                ),
            ),
            "Skill identity",
        ),
    ],
)
def test_sqlite_event_journal_rejects_broken_lifecycle(
    tmp_path: Path,
    events: tuple[RunEvent, ...],
    message: str,
) -> None:
    journal = SQLiteEventJournal(tmp_path / "events.sqlite3")
    for event in events[:-1]:
        journal.publish(event)
    with pytest.raises(EventJournalConflictError, match=message):
        journal.publish(events[-1])


def test_sqlite_event_journal_validates_queries_and_detects_corruption(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.sqlite3"
    with pytest.raises(ValueError, match="busy_timeout_ms"):
        SQLiteEventJournal(path, busy_timeout_ms=0)
    journal = SQLiteEventJournal(path)
    journal.publish(_envelope(1))

    with pytest.raises(ValueError, match="after_event_id"):
        journal.read(after_event_id=-1)
    with pytest.raises(ValueError, match="limit"):
        journal.read(limit=0)
    with pytest.raises(ValueError, match="timeout_s"):
        journal.wait(after_event_id=0, timeout_s=-0.1)

    import sqlite3

    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "UPDATE run_events SET envelope_json = ? WHERE event_id = 1",
            ('{"broken":true}',),
        )
    with pytest.raises(EventJournalCorruptionError, match="event_id 1"):
        journal.read()
