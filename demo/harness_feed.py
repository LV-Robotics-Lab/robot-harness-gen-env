"""Read-only projection of committed Harness events for the browser workbench."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any
from uuid import UUID

from self_improving.harness import (
    EventJournalCorruptionError,
    EventPage,
    SQLiteEventJournal,
)


class HarnessEventFeedCorruptionError(RuntimeError):
    """The configured source could not reconstruct trusted event history."""


class HarnessEventFeed:
    """Project one authoritative event journal behind a cursor-based read seam."""

    def __init__(self, read_page: Callable[..., EventPage]) -> None:
        self._read_page = read_page

    @classmethod
    def from_journal(cls, journal: SQLiteEventJournal) -> "HarnessEventFeed":
        return cls(journal.read)

    def page(
        self,
        *,
        after_event_id: int = 0,
        run_id: UUID | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        if type(after_event_id) is not int or after_event_id < 0 or after_event_id > 2**63 - 1:
            raise ValueError("after_event_id must fit a nonnegative SQLite integer")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer from 1 to 500")
        if run_id is not None and not isinstance(run_id, UUID):
            raise ValueError("run_id must be a UUID")
        try:
            page = self._read_page(
                after_event_id=after_event_id,
                run_id=run_id,
                limit=limit,
            )
        except (EventJournalCorruptionError, OSError, sqlite3.Error) as error:
            raise HarnessEventFeedCorruptionError(
                "Harness event history failed integrity checks"
            ) from error
        return {
            "schema_version": "harness.workbench_event_page.v1",
            "events": [
                {
                    "event_id": str(stored.event_id),
                    "run_id": str(stored.envelope.run_id),
                    "skill_id": stored.envelope.skill_id,
                    "skill_version": stored.envelope.skill_version,
                    "event": stored.envelope.event.model_dump(mode="json"),
                }
                for stored in page.events
            ],
            "last_event_id": str(page.last_event_id),
            "has_more": page.has_more,
        }
