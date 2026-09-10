from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event as ThreadEvent
from threading import Lock
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
from self_improving.harness.registry import (
    ControlledRunIdentityError,
    ControlledRunPendingError,
    RunPersistenceError,
)
from self_improving.harness.run_store import (
    ControlledRunIntent,
    RunStoreConflictError,
    SQLiteRunStore,
)
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
        self.intents: dict[UUID, ControlledRunIntent] = {}
        self.invocations: dict[UUID, Invocation] = {}
        self.states: dict[UUID, RunState] = {}
        self.lock = Lock()

    def reserve_controlled_intent(self, intent: ControlledRunIntent) -> bool:
        with self.lock:
            existing = self.intents.get(intent.run_id)
            if existing is not None:
                if existing != intent:
                    raise RunStoreConflictError("different controlled preflight intent")
                return False
            if intent.run_id in self.invocations or intent.run_id in self.states:
                raise RunStoreConflictError("controlled identity is already occupied")
            self.trace.append("intent")
            self.intents[intent.run_id] = intent
            return True

    def read_controlled_intent(self, run_id: UUID) -> ControlledRunIntent | None:
        with self.lock:
            return self.intents.get(run_id)

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
    implementation_sha256: str = "c" * 64,
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
        implementation_sha256=implementation_sha256,
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


def test_controlled_preflight_terminal_uses_and_recovers_the_reserved_run_id(
    tmp_path: Path,
) -> None:
    trace: list[str] = []
    run_store = TracingRunStore(trace)
    event_sink = TracingEventSink(trace)
    registry, _ = _registry(
        tmp_path,
        event_sink=event_sink,
        run_store=run_store,
    )

    first = registry.invoke_with_run_id("test.echo", "1.0.0", {}, run_id=RUN_ID)
    event_count = len(event_sink.events)
    recovered = registry.invoke_with_run_id("test.echo", "1.0.0", {}, run_id=RUN_ID)

    assert first.run_id == RUN_ID
    assert first.status is RunStatus.BLOCKED
    assert first.attempt == 0
    assert run_store.read_invocation(RUN_ID) is None
    assert run_store.read_run_state(RUN_ID) == first
    assert recovered == first
    assert len(event_sink.events) == event_count


def test_controlled_preflight_rejects_different_raw_inputs_with_the_same_validation_error(
    tmp_path: Path,
) -> None:
    trace: list[str] = []
    run_store = TracingRunStore(trace)
    event_sink = TracingEventSink(trace)
    registry, _ = _registry(
        tmp_path,
        event_sink=event_sink,
        run_store=run_store,
    )
    common = {
        "name": "x" * 256,
        "uri": "not-an-artifact-uri",
        "sha256": "a" * 64,
        "bytes": 1,
        "schema_version": None,
    }

    first = registry.invoke_with_run_id(
        "test.echo",
        "1.0.0",
        {**common, "media_type": "application/first+json"},
        run_id=RUN_ID,
    )

    assert first.status is RunStatus.BLOCKED
    with pytest.raises(ControlledRunIdentityError, match="another intent"):
        registry.invoke_with_run_id(
            "test.echo",
            "1.0.0",
            {**common, "media_type": "application/second+json"},
            run_id=RUN_ID,
        )
    assert len(event_sink.events) == 2


def test_controlled_preflight_intent_binds_the_exact_registered_descriptor(
    tmp_path: Path,
) -> None:
    trace: list[str] = []
    run_store = TracingRunStore(trace)
    first_registry, _ = _registry(
        tmp_path,
        event_sink=TracingEventSink(trace),
        run_store=run_store,
        implementation_sha256="c" * 64,
    )
    invalid = {
        "name": "x" * 256,
        "uri": "not-an-artifact-uri",
        "media_type": "application/json",
        "sha256": "a" * 64,
        "bytes": 1,
        "schema_version": None,
    }
    first_registry.invoke_with_run_id("test.echo", "1.0.0", invalid, run_id=RUN_ID)
    changed_registry, _ = _registry(
        tmp_path,
        event_sink=TracingEventSink(trace),
        run_store=run_store,
        implementation_sha256="d" * 64,
    )

    with pytest.raises(ControlledRunIdentityError, match="another intent"):
        changed_registry.invoke_with_run_id("test.echo", "1.0.0", invalid, run_id=RUN_ID)


def test_controlled_parameters_are_snapshotted_once_before_validation_and_execution(
    tmp_path: Path,
) -> None:
    trace: list[str] = []
    run_store = TracingRunStore(trace)
    registry, first = _registry(
        tmp_path,
        event_sink=TracingEventSink(trace),
        run_store=run_store,
    )
    second = _put_json(
        LocalArtifactStore(tmp_path / "cas"),
        tmp_path,
        name="second-payload",
        schema_version="robotwin.asset_catalog.v1",
        payload={"value": 2},
    )

    class FlipMapping(Mapping[str, object]):
        def __init__(self, left: ArtifactRef, right: ArtifactRef) -> None:
            self._values = (
                left.model_dump(mode="json"),
                right.model_dump(mode="json"),
            )
            self._active = self._values[0]
            self.iterations = 0

        def __iter__(self) -> Iterator[str]:
            self._active = self._values[min(self.iterations, 1)]
            self.iterations += 1
            return iter(self._active)

        def __getitem__(self, key: str) -> object:
            return self._active[key]

        def __len__(self) -> int:
            return len(self._active)

    parameters = FlipMapping(first, second)
    terminal = registry.invoke_with_run_id("test.echo", "1.0.0", parameters, run_id=RUN_ID)

    invocation = registry.invocation(RUN_ID)
    assert invocation is not None
    assert terminal.output == first.model_dump(mode="json")
    assert invocation.effective_parameters == first.model_dump(mode="json")
    assert parameters.iterations == 1


def test_concurrent_same_preflight_intent_never_mixes_events_or_reexecutes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "controlled-concurrent.sqlite3"
    entered = ThreadEvent()
    release = ThreadEvent()
    journal = SQLiteEventJournal(database)

    class BlockingEventSink:
        def publish(self, event: RunEvent) -> None:
            entered.set()
            assert release.wait(timeout=5), "test did not release the first event"
            journal.publish(event)

    first_registry, _ = _registry(
        tmp_path,
        event_sink=BlockingEventSink(),
        run_store=SQLiteRunStore(database),
    )
    retry_registry, _ = _registry(
        tmp_path,
        event_sink=SQLiteEventJournal(database),
        run_store=SQLiteRunStore(database),
    )
    invalid = {
        "name": "x" * 256,
        "uri": "not-an-artifact-uri",
        "media_type": "application/json",
        "sha256": "a" * 64,
        "bytes": 1,
        "schema_version": None,
    }

    with ThreadPoolExecutor(max_workers=1) as executor:
        first_future = executor.submit(
            first_registry.invoke_with_run_id,
            "test.echo",
            "1.0.0",
            invalid,
            run_id=RUN_ID,
        )
        assert entered.wait(timeout=5), "first invocation did not reserve its intent"
        with pytest.raises(ControlledRunPendingError, match="will not be repeated"):
            retry_registry.invoke_with_run_id(
                "test.echo",
                "1.0.0",
                invalid,
                run_id=RUN_ID,
            )
        release.set()
        terminal = first_future.result(timeout=5)

    recovered = retry_registry.invoke_with_run_id(
        "test.echo",
        "1.0.0",
        invalid,
        run_id=RUN_ID,
    )
    assert recovered == terminal
    assert len(journal.read(run_id=RUN_ID).events) == 2


def test_concurrent_different_preflight_intents_fail_closed_without_event_mixing(
    tmp_path: Path,
) -> None:
    database = tmp_path / "controlled-conflict.sqlite3"
    entered = ThreadEvent()
    release = ThreadEvent()
    journal = SQLiteEventJournal(database)

    class BlockingEventSink:
        def publish(self, event: RunEvent) -> None:
            entered.set()
            assert release.wait(timeout=5), "test did not release the winning event"
            journal.publish(event)

    first_registry, _ = _registry(
        tmp_path,
        event_sink=BlockingEventSink(),
        run_store=SQLiteRunStore(database),
    )
    contender, _ = _registry(
        tmp_path,
        event_sink=SQLiteEventJournal(database),
        run_store=SQLiteRunStore(database),
    )
    common = {
        "name": "x" * 256,
        "uri": "not-an-artifact-uri",
        "sha256": "a" * 64,
        "bytes": 1,
        "schema_version": None,
    }

    with ThreadPoolExecutor(max_workers=1) as executor:
        first_future = executor.submit(
            first_registry.invoke_with_run_id,
            "test.echo",
            "1.0.0",
            {**common, "media_type": "application/first+json"},
            run_id=RUN_ID,
        )
        assert entered.wait(timeout=5), "first invocation did not reserve its intent"
        with pytest.raises(ControlledRunIdentityError, match="another intent"):
            contender.invoke_with_run_id(
                "test.echo",
                "1.0.0",
                {**common, "media_type": "application/second+json"},
                run_id=RUN_ID,
            )
        release.set()
        terminal = first_future.result(timeout=5)

    assert terminal.status is RunStatus.BLOCKED
    assert len(journal.read(run_id=RUN_ID).events) == 2


def test_concurrent_same_execution_intent_never_runs_the_handler_twice(
    tmp_path: Path,
) -> None:
    database = tmp_path / "controlled-execution.sqlite3"
    entered = ThreadEvent()
    release = ThreadEvent()
    journal = SQLiteEventJournal(database)

    class BlockingEventSink:
        def publish(self, event: RunEvent) -> None:
            entered.set()
            assert release.wait(timeout=5), "test did not release the first execution event"
            journal.publish(event)

    first_registry, payload = _registry(
        tmp_path,
        event_sink=BlockingEventSink(),
        run_store=SQLiteRunStore(database),
    )
    retry_registry, _ = _registry(
        tmp_path,
        event_sink=SQLiteEventJournal(database),
        run_store=SQLiteRunStore(database),
    )
    parameters = payload.model_dump(mode="json")

    with ThreadPoolExecutor(max_workers=1) as executor:
        first_future = executor.submit(
            first_registry.invoke_with_run_id,
            "test.echo",
            "1.0.0",
            parameters,
            run_id=RUN_ID,
        )
        assert entered.wait(timeout=5), "first invocation did not enter execution"
        with pytest.raises(ControlledRunPendingError, match="will not be repeated"):
            retry_registry.invoke_with_run_id(
                "test.echo",
                "1.0.0",
                parameters,
                run_id=RUN_ID,
            )
        release.set()
        terminal = first_future.result(timeout=5)

    assert terminal.status is RunStatus.SUCCEEDED
    assert retry_registry.invoke_with_run_id(
        "test.echo",
        "1.0.0",
        parameters,
        run_id=RUN_ID,
    ) == terminal
    assert len(journal.read(run_id=RUN_ID).events) == 3


def test_controlled_recovery_rejects_a_terminal_swapped_from_another_invocation(
    tmp_path: Path,
) -> None:
    trace: list[str] = []
    run_store = TracingRunStore(trace)
    registry, payload = _registry(
        tmp_path,
        event_sink=TracingEventSink(trace),
        run_store=run_store,
    )
    terminal = registry.invoke_with_run_id(
        "test.echo",
        "1.0.0",
        payload.model_dump(mode="json"),
        run_id=RUN_ID,
    )
    run_store.states[RUN_ID] = RunState.model_validate(
        terminal.model_copy(update={"skill_version": "9.9.9"}).model_dump(mode="python")
    )

    with pytest.raises(ControlledRunIdentityError, match="not bound to its invocation"):
        registry.invoke_with_run_id(
            "test.echo",
            "1.0.0",
            payload.model_dump(mode="json"),
            run_id=RUN_ID,
        )


def test_controlled_recovery_never_reexecutes_an_invocation_without_a_terminal(
    tmp_path: Path,
) -> None:
    trace: list[str] = []
    run_store = TracingRunStore(trace)

    class FailingEventSink:
        def publish(self, event: RunEvent) -> None:
            raise OSError(f"event journal unavailable at sequence {event.event.seq}")

    registry, payload = _registry(
        tmp_path,
        event_sink=FailingEventSink(),
        run_store=run_store,
    )
    with pytest.raises(OSError, match="event journal unavailable"):
        registry.invoke_with_run_id(
            "test.echo",
            "1.0.0",
            payload.model_dump(mode="json"),
            run_id=RUN_ID,
        )

    recovered_registry, _ = _registry(
        tmp_path,
        event_sink=TracingEventSink(trace),
        run_store=run_store,
    )
    with pytest.raises(ControlledRunPendingError, match="will not be repeated"):
        recovered_registry.invoke_with_run_id(
            "test.echo",
            "1.0.0",
            payload.model_dump(mode="json"),
            run_id=RUN_ID,
        )

    assert trace == ["intent", "invocation"]


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
