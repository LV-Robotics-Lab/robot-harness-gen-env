"""Run lifecycle recording driven by actual execution callbacks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from .schemas import ArtifactRef, Blocker, Event, RunState, RunStatus
from .schemas.base import JsonObject


@dataclass(frozen=True)
class RunEvent:
    """Internal envelope that gives the public Event its run identity."""

    run_id: UUID
    skill_id: str
    skill_version: str
    event: Event


class EventSink(Protocol):
    """Receive an event after the recorder has validated its lifecycle position."""

    def publish(self, event: RunEvent) -> None: ...


class RecordingEventSink:
    """In-memory sink used by tests and embedded callers."""

    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    def publish(self, event: RunEvent) -> None:
        self.events.append(event)


class RunRecordingError(RuntimeError):
    """Raised before an invalid or non-monotonic event can reach a sink."""


class RunRecorder:
    """Own event sequencing, attempts, timestamps, transitions, and RunState assembly."""

    def __init__(
        self,
        *,
        run_id: UUID,
        skill_id: str,
        skill_version: str,
        clock: Callable[[], datetime],
        sink: EventSink,
    ) -> None:
        self.run_id = run_id
        self.skill_id = skill_id
        self.skill_version = skill_version
        self._clock = clock
        self._sink = sink
        self._events: list[Event] = []
        self._status: RunStatus | None = None
        self._attempt: int | None = None

    def start(self, *, stage: str, attempt: int) -> Event:
        if self._events:
            raise RunRecordingError("run already started")
        self._attempt = attempt
        return self._append(stage=stage, to_status=RunStatus.RUNNING)

    def progress(
        self,
        *,
        stage: str,
        artifact_refs: tuple[ArtifactRef, ...] = (),
    ) -> Event:
        self._require_running()
        return self._append(
            stage=stage,
            to_status=RunStatus.RUNNING,
            artifact_refs=artifact_refs,
        )

    def retry(
        self,
        *,
        stage: str,
        artifact_refs: tuple[ArtifactRef, ...] = (),
    ) -> Event:
        self._require_running()
        assert self._attempt is not None
        self._attempt += 1
        return self._append(
            stage=stage,
            to_status=RunStatus.RUNNING,
            artifact_refs=artifact_refs,
        )

    def finish(
        self,
        *,
        status: RunStatus,
        stage: str,
        artifact_refs: tuple[ArtifactRef, ...] = (),
    ) -> Event:
        self._require_running()
        if status == RunStatus.RUNNING:
            raise RunRecordingError("finish requires a terminal status")
        return self._append(
            stage=stage,
            to_status=status,
            artifact_refs=artifact_refs,
        )

    def build_state(
        self,
        *,
        invocation_digest: str | None,
        max_attempts: int,
        artifacts: tuple[ArtifactRef, ...],
        output: JsonObject | None,
        blocker: Blocker | None,
    ) -> RunState:
        if not self._events or self._status is None or self._attempt is None:
            raise RunRecordingError("run has not started")
        terminal = self._status != RunStatus.RUNNING
        return RunState(
            run_id=self.run_id,
            invocation_digest=invocation_digest,
            skill_id=self.skill_id,
            skill_version=self.skill_version,
            status=self._status,
            attempt=self._attempt,
            max_attempts=max_attempts,
            started_at=self._events[0].timestamp,
            ended_at=self._events[-1].timestamp if terminal else None,
            events=tuple(self._events),
            artifacts=artifacts,
            output=output,
            blocker=blocker,
        )

    def _require_running(self) -> None:
        if self._status != RunStatus.RUNNING:
            raise RunRecordingError("run is not active")

    def _append(
        self,
        *,
        stage: str,
        to_status: RunStatus,
        artifact_refs: tuple[ArtifactRef, ...] = (),
    ) -> Event:
        assert self._attempt is not None
        timestamp = self._clock()
        if self._events and timestamp < self._events[-1].timestamp:
            raise RunRecordingError("event clock moved backwards")
        event = Event(
            seq=len(self._events) + 1,
            timestamp=timestamp,
            stage=stage,
            attempt=self._attempt,
            from_status=self._status,
            to_status=to_status,
            artifact_refs=artifact_refs,
        )
        self._events.append(event)
        self._status = to_status
        self._sink.publish(
            RunEvent(
                run_id=self.run_id,
                skill_id=self.skill_id,
                skill_version=self.skill_version,
                event=event,
            )
        )
        return event
