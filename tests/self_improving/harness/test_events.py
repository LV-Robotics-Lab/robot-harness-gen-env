from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from self_improving.harness import (
    RecordingEventSink,
    RunRecorder,
    RunRecordingError,
    RunStatus,
)


class SequenceClock:
    def __init__(self) -> None:
        self._current = datetime(2026, 8, 31, 2, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        value = self._current
        self._current += timedelta(seconds=1)
        return value


def test_run_recorder_emits_real_progress_retry_and_terminal_events() -> None:
    run_id = UUID("12345678-1234-4234-9234-123456789abc")
    sink = RecordingEventSink()
    recorder = RunRecorder(
        run_id=run_id,
        skill_id="text2env.replay",
        skill_version="1.0.0",
        clock=SequenceClock(),
        sink=sink,
    )

    recorder.start(stage="preflight", attempt=1)
    recorder.progress(stage="runtime.load")
    recorder.retry(stage="runtime.retry")
    recorder.progress(stage="runtime.play")
    recorder.finish(status=RunStatus.SUCCEEDED, stage="runtime.complete")
    state = recorder.build_state(
        invocation_digest="a" * 64,
        max_attempts=2,
        artifacts=(),
        output={"runtime_evidence": "recorded"},
        blocker=None,
    )

    assert [event.seq for event in state.events] == [1, 2, 3, 4, 5]
    assert [event.attempt for event in state.events] == [1, 1, 2, 2, 2]
    assert [event.stage for event in state.events] == [
        "preflight",
        "runtime.load",
        "runtime.retry",
        "runtime.play",
        "runtime.complete",
    ]
    assert state.status == RunStatus.SUCCEEDED
    assert state.attempt == 2
    assert state.ended_at == state.events[-1].timestamp
    assert [envelope.event for envelope in sink.events] == list(state.events)
    assert all(envelope.run_id == run_id for envelope in sink.events)


def test_run_recorder_rejects_invalid_lifecycle_and_non_monotonic_time() -> None:
    run_id = UUID("12345678-1234-4234-9234-123456789abc")
    recorder = RunRecorder(
        run_id=run_id,
        skill_id="text2env.compile",
        skill_version="1.0.0",
        clock=SequenceClock(),
        sink=RecordingEventSink(),
    )

    with pytest.raises(RunRecordingError, match="not active"):
        recorder.progress(stage="too_early")
    with pytest.raises(RunRecordingError, match="not started"):
        recorder.build_state(
            invocation_digest=None,
            max_attempts=0,
            artifacts=(),
            output=None,
            blocker=None,
        )

    recorder.start(stage="preflight", attempt=1)
    running = recorder.build_state(
        invocation_digest="a" * 64,
        max_attempts=1,
        artifacts=(),
        output=None,
        blocker=None,
    )
    assert running.status == RunStatus.RUNNING
    assert running.ended_at is None
    with pytest.raises(RunRecordingError, match="already started"):
        recorder.start(stage="again", attempt=1)
    with pytest.raises(RunRecordingError, match="terminal status"):
        recorder.finish(status=RunStatus.RUNNING, stage="not_terminal")

    recorder.finish(status=RunStatus.SUCCEEDED, stage="done")
    with pytest.raises(RunRecordingError, match="not active"):
        recorder.retry(stage="too_late")

    forward = datetime(2026, 8, 31, 3, 0, 1, tzinfo=timezone.utc)
    backward = forward - timedelta(seconds=1)
    times = iter((forward, backward))
    bad_clock = RunRecorder(
        run_id=run_id,
        skill_id="text2env.compile",
        skill_version="1.0.0",
        clock=lambda: next(times),
        sink=RecordingEventSink(),
    )
    bad_clock.start(stage="preflight", attempt=1)
    with pytest.raises(RunRecordingError, match="clock moved backwards"):
        bad_clock.progress(stage="compile")
