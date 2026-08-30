from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

from self_improving.harness import (
    ArtifactRef,
    HandlerResult,
    LocalArtifactStore,
    RunEvent,
    RunState,
    RunStatus,
    SkillDescriptor,
    SkillRegistry,
    StaticDependencyResolver,
)
from self_improving.harness.event_journal import SQLiteEventJournal
from self_improving.harness.registry import RunPersistenceError
from self_improving.harness.run_store import SQLiteRunStore
from self_improving.harness.schemas import Invocation

RUN_ID = UUID("12345678-1234-4234-9234-123456789abc")


class SequenceClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(milliseconds=1)
        return value


class TracingRunStore:
    def __init__(
        self,
        trace: list[str],
        *,
        fail_invocation: bool = False,
        fail_state: bool = False,
    ) -> None:
        self.trace = trace
        self.fail_invocation = fail_invocation
        self.fail_state = fail_state
        self.invocations: dict[UUID, Invocation] = {}
        self.states: dict[UUID, RunState] = {}

    def put_invocation(self, invocation: Invocation) -> None:
        self.trace.append("invocation")
        if self.fail_invocation:
            raise OSError("invocation disk unavailable")
        self.invocations[invocation.run_id] = invocation

    def put_run_state(self, state: RunState) -> None:
        self.trace.append("state")
        if self.fail_state:
            raise OSError("state disk unavailable")
        self.states[state.run_id] = state

    def read_invocation(self, run_id: UUID) -> Invocation | None:
        return self.invocations.get(run_id)

    def read_run_state(self, run_id: UUID) -> RunState | None:
        return self.states.get(run_id)


class TracingEventSink:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.events: list[RunEvent] = []

    def publish(self, event: RunEvent) -> None:
        self.trace.append(f"event:{event.event.seq}:{event.event.stage}")
        self.events.append(event)


def _put_json(
    store: LocalArtifactStore,
    root: Path,
    *,
    name: str,
    schema_version: str,
    payload: object,
) -> ArtifactRef:
    path = root / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return store.put_file(
        path,
        name=name,
        media_type="application/json",
        schema_version=schema_version,
    )


def _registry(
    tmp_path: Path,
    *,
    event_sink,
    run_store,
    handler=None,
) -> tuple[SkillRegistry, ArtifactRef]:
    artifacts = LocalArtifactStore(tmp_path / "cas")
    report = _put_json(
        artifacts,
        tmp_path,
        name="qualification_report",
        schema_version="harness.skill_qualification_report.v1",
        payload={"case": "registry-persistence", "status": "pass"},
    )
    qualification = _put_json(
        artifacts,
        tmp_path,
        name="qualification",
        schema_version="harness.skill_qualification.v1",
        payload={
            "skill_ref": "test.echo@1.0.0",
            "status": "pass",
            "deterministic_case_id": "registry-persistence",
            "regression_command": "pytest -q",
            "report_sha256": report.sha256,
        },
    )
    payload = _put_json(
        artifacts,
        tmp_path,
        name="payload",
        schema_version="robotwin.asset_catalog.v1",
        payload={"value": 1},
    )
    registry = SkillRegistry(
        artifact_resolver=artifacts,
        dependency_resolver=StaticDependencyResolver({}),
        event_sink=event_sink,
        clock=SequenceClock(),
        run_id_factory=lambda: RUN_ID,
        run_store=run_store,
    )
    descriptor = SkillDescriptor(
        skill_id="test.echo",
        version="1.0.0",
        mcp_tool_name="test_echo_v1_0_0",
        input_schema="harness.artifact_ref.v1",
        output_schema="harness.artifact_ref.v1",
        implementation_name="tests.echo",
        implementation_version="1",
        implementation_sha256="c" * 64,
        deterministic=True,
        max_attempts=1,
        qualification_artifact=qualification,
    )

    def default_handler(value: ArtifactRef, context) -> HandlerResult:
        context.emit("echo.completed", artifact_refs=(value,))
        return HandlerResult(output=value, artifacts=(value,))

    registry.register(descriptor, handler or default_handler)
    return registry, payload


def test_registry_orders_invocation_events_and_terminal_state(tmp_path: Path) -> None:
    trace: list[str] = []
    run_store = TracingRunStore(trace)
    event_sink = TracingEventSink(trace)
    registry, payload = _registry(
        tmp_path,
        event_sink=event_sink,
        run_store=run_store,
    )

    state = registry.invoke("test.echo", "1.0.0", payload.model_dump(mode="json"))

    assert state.status == RunStatus.SUCCEEDED
    assert trace == [
        "invocation",
        "event:1:preflight",
        "event:2:echo.completed",
        "event:3:complete",
        "state",
    ]
    assert run_store.read_invocation(RUN_ID) == registry.invocation(RUN_ID)
    assert run_store.read_run_state(RUN_ID) == state


def test_registry_persists_preflight_terminal_without_invocation(tmp_path: Path) -> None:
    trace: list[str] = []
    run_store = TracingRunStore(trace)
    event_sink = TracingEventSink(trace)
    registry, _ = _registry(
        tmp_path,
        event_sink=event_sink,
        run_store=run_store,
    )

    state = registry.invoke("test.missing", "1.0.0", {})

    assert state.attempt == 0
    assert state.status == RunStatus.BLOCKED
    assert trace == ["event:1:preflight", "event:2:preflight", "state"]
    assert run_store.read_invocation(RUN_ID) is None
    assert run_store.read_run_state(RUN_ID) == state


def test_registry_fails_before_first_event_when_invocation_is_not_durable(
    tmp_path: Path,
) -> None:
    trace: list[str] = []
    run_store = TracingRunStore(trace, fail_invocation=True)
    event_sink = TracingEventSink(trace)
    handler_called = False

    def handler(value: ArtifactRef, context) -> HandlerResult:
        nonlocal handler_called
        handler_called = True
        return HandlerResult(output=value)

    registry, payload = _registry(
        tmp_path,
        event_sink=event_sink,
        run_store=run_store,
        handler=handler,
    )

    with pytest.raises(RunPersistenceError, match="persist invocation") as captured:
        registry.invoke("test.echo", "1.0.0", payload.model_dump(mode="json"))

    assert captured.value.run_id == RUN_ID
    assert captured.value.state is None
    assert trace == ["invocation"]
    assert event_sink.events == []
    assert handler_called is False
    assert registry.invocation(RUN_ID) is None


def test_terminal_persistence_failure_does_not_emit_a_second_terminal_event(
    tmp_path: Path,
) -> None:
    trace: list[str] = []
    run_store = TracingRunStore(trace, fail_state=True)
    event_sink = TracingEventSink(trace)
    registry, payload = _registry(
        tmp_path,
        event_sink=event_sink,
        run_store=run_store,
    )

    with pytest.raises(RunPersistenceError, match="persist terminal state") as captured:
        registry.invoke("test.echo", "1.0.0", payload.model_dump(mode="json"))

    assert captured.value.state is not None
    assert captured.value.state.status == RunStatus.SUCCEEDED
    assert trace == [
        "invocation",
        "event:1:preflight",
        "event:2:echo.completed",
        "event:3:complete",
        "state",
    ]
    assert len(event_sink.events) == 3


def test_sqlite_registry_records_survive_process_assembly_restart(tmp_path: Path) -> None:
    database = tmp_path / "state" / "harness.sqlite3"
    run_store = SQLiteRunStore(database)
    registry, payload = _registry(
        tmp_path,
        event_sink=SQLiteEventJournal(database),
        run_store=run_store,
    )
    terminal = registry.invoke("test.echo", "1.0.0", payload.model_dump(mode="json"))

    restarted, _ = _registry(
        tmp_path / "restart",
        event_sink=SQLiteEventJournal(database),
        run_store=SQLiteRunStore(database),
    )
    preflight_database = tmp_path / "preflight" / "harness.sqlite3"
    preflight_store = SQLiteRunStore(preflight_database)
    preflight_registry, _ = _registry(
        tmp_path / "preflight",
        event_sink=SQLiteEventJournal(preflight_database),
        run_store=preflight_store,
    )
    preflight = preflight_registry.invoke("test.missing", "1.0.0", {})

    assert restarted.invocation(RUN_ID) == run_store.read_invocation(RUN_ID)
    assert run_store.read_run_state(RUN_ID) == terminal
    assert preflight_store.read_run_state(RUN_ID) == preflight
