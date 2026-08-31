from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from demo.harness_feed import HarnessEventFeed, HarnessEventFeedCorruptionError
from self_improving.harness import RunRecorder, SQLiteEventJournal

RUN_ID = UUID("12345678-1234-4234-9234-123456789abc")
STARTED_AT = datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc)


def test_feed_page_projects_one_committed_harness_event(tmp_path: Path) -> None:
    journal = SQLiteEventJournal(tmp_path / "harness.sqlite3")
    recorder = RunRecorder(
        run_id=RUN_ID,
        skill_id="text2env.compile",
        skill_version="1.0.0",
        clock=lambda: STARTED_AT,
        sink=journal,
    )
    recorder.start(stage="invoke.started", attempt=1)

    page = HarnessEventFeed.from_journal(journal).page(
        after_event_id=0,
        run_id=RUN_ID,
        limit=20,
    )

    assert page == {
        "schema_version": "harness.workbench_event_page.v1",
        "events": [
            {
                "event_id": "1",
                "run_id": "12345678-1234-4234-9234-123456789abc",
                "skill_id": "text2env.compile",
                "skill_version": "1.0.0",
                "event": {
                    "seq": 1,
                    "timestamp": "2026-08-31T05:00:00Z",
                    "stage": "invoke.started",
                    "attempt": 1,
                    "from_status": None,
                    "to_status": "running",
                    "artifact_refs": [],
                },
            }
        ],
        "last_event_id": "1",
        "has_more": False,
    }


def test_feed_page_rejects_a_boolean_cursor(tmp_path: Path) -> None:
    feed = HarnessEventFeed.from_journal(SQLiteEventJournal(tmp_path / "harness.sqlite3"))

    with pytest.raises(ValueError, match="after_event_id"):
        feed.page(after_event_id=False)


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"after_event_id": 2**63}, "after_event_id"),
        ({"limit": True}, "limit"),
        ({"limit": 501}, "limit"),
        ({"run_id": str(RUN_ID)}, "run_id"),
    ],
)
def test_feed_page_rejects_values_outside_its_public_query_contract(
    arguments: dict[str, object],
    message: str,
    tmp_path: Path,
) -> None:
    feed = HarnessEventFeed.from_journal(SQLiteEventJournal(tmp_path / "harness.sqlite3"))

    with pytest.raises(ValueError, match=message):
        feed.page(**arguments)


def test_feed_page_normalizes_sqlite_file_corruption() -> None:
    def broken_read(**_arguments):
        raise sqlite3.DatabaseError("file is not a database")

    feed = HarnessEventFeed(broken_read)

    with pytest.raises(HarnessEventFeedCorruptionError, match="integrity checks"):
        feed.page()
