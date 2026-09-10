from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event as ThreadEvent
from threading import Lock
from uuid import UUID

import pytest

from self_improving.harness import (
    ArtifactRef,
    DependencyRef,
    HandlerResult,
    LocalArtifactStore,
    RunEvent,
    RunState,
    RunStatus,
    SkillDescriptor,
    SkillRegistry,
    StaticDependencyResolver,
)
from self_improving.harness.event_journal import (
    EventJournalCorruptionError,
    SQLiteEventJournal,
)
from self_improving.harness.registry import (
    ControlledRunIdentityError,
    ControlledRunPendingError,
    DependencyResolutionError,
    RunPersistenceError,
)
from self_improving.harness.run_store import (
    ControlledRunIntent,
    ControlledRunLifecycle,
    RunStoreConflictError,
    SQLiteRunStore,
)
from self_improving.harness.schemas import Event, Invocation

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

    def read_controlled_lifecycle(self, run_id: UUID) -> ControlledRunLifecycle:
        with self.lock:
            return ControlledRunLifecycle(
                intent=self.intents.get(run_id),
                invocation=self.invocations.get(run_id),
                terminal=self.states.get(run_id),
            )


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
    dependency_resolver=None,
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
        dependency_resolver=dependency_resolver or StaticDependencyResolver({}),
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
    intent = run_store.read_controlled_intent(RUN_ID)
    assert intent is not None
    assert intent.phase == "input_validation"
    assert intent.dependencies is None
    assert recovered == first
    assert len(event_sink.events) == event_count


@pytest.mark.parametrize("input_is_valid", [False, True], ids=("preflight", "invocation"))
def test_controlled_recovery_consumes_one_atomic_lifecycle_snapshot(
    tmp_path: Path,
    input_is_valid: bool,
) -> None:
    trace: list[str] = []

    class AdvancingRunStore(TracingRunStore):
        recovering = False
        split_reads = 0

        def read_controlled_lifecycle(self, run_id: UUID) -> ControlledRunLifecycle:
            with self.lock:
                return ControlledRunLifecycle(
                    intent=self.intents.get(run_id),
                    invocation=self.invocations.get(run_id),
                    terminal=self.states.get(run_id),
                )

        def read_controlled_intent(self, run_id: UUID) -> ControlledRunIntent | None:
            if self.recovering:
                self.split_reads += 1
            return super().read_controlled_intent(run_id)

        def read_invocation(self, run_id: UUID) -> Invocation | None:
            if self.recovering:
                self.split_reads += 1
                # This is the pre-commit observation in the old three-read race.
                return None
            return super().read_invocation(run_id)

        def read_run_state(self, run_id: UUID) -> RunState | None:
            if self.recovering:
                self.split_reads += 1
                # This is the post-commit observation from the same old recovery.
                return self.states.get(run_id)
            return super().read_run_state(run_id)

    store = AdvancingRunStore(trace)
    event_sink = TracingEventSink(trace)
    registry, payload = _registry(
        tmp_path,
        event_sink=event_sink,
        run_store=store,
    )
    parameters = payload.model_dump(mode="json") if input_is_valid else {}
    first = registry.invoke_with_run_id("test.echo", "1.0.0", parameters, run_id=RUN_ID)
    event_count = len(event_sink.events)
    store.recovering = True

    recovered = registry.invoke_with_run_id(
        "test.echo",
        "1.0.0",
        parameters,
        run_id=RUN_ID,
    )

    assert recovered == first
    assert len(event_sink.events) == event_count
    assert store.split_reads == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"status": RunStatus.SUCCEEDED, "output": {}, "blocker": None},
        {"blocker": None},
        {"attempt": 1},
        {"max_attempts": 1},
        {"events": ()},
    ],
    ids=("success", "no-blocker", "attempt", "max-attempts", "events"),
)
def test_controlled_preflight_recovery_rejects_forged_terminal_semantics(
    tmp_path: Path,
    changes: dict[str, object],
) -> None:
    trace: list[str] = []
    run_store = TracingRunStore(trace)
    registry, _ = _registry(
        tmp_path,
        event_sink=TracingEventSink(trace),
        run_store=run_store,
    )
    first = registry.invoke_with_run_id("test.echo", "1.0.0", {}, run_id=RUN_ID)
    run_store.states[RUN_ID] = first.model_copy(update=changes)

    with pytest.raises(ControlledRunIdentityError, match="preflight intent"):
        registry.invoke_with_run_id("test.echo", "1.0.0", {}, run_id=RUN_ID)


def test_controlled_validated_intent_freezes_exact_dependency_identity(
    tmp_path: Path,
) -> None:
    trace: list[str] = []
    run_store = TracingRunStore(trace)
    dependency = DependencyRef(name="scene-gen", version="1", sha256="9" * 64)
    registry, payload = _registry(
        tmp_path,
        event_sink=TracingEventSink(trace),
        run_store=run_store,
        dependency_resolver=StaticDependencyResolver({"test.echo@1.0.0": (dependency,)}),
    )

    registry.invoke_with_run_id(
        "test.echo",
        "1.0.0",
        payload.model_dump(mode="json"),
        run_id=RUN_ID,
    )

    intent = run_store.read_controlled_intent(RUN_ID)
    invocation = run_store.read_invocation(RUN_ID)
    assert intent is not None
    assert invocation is not None
    assert intent.phase == "validated_input"
    assert intent.dependencies == invocation.dependencies == (dependency,)


def test_controlled_dependency_preflight_reserves_final_phase_and_resolves_once(
    tmp_path: Path,
) -> None:
    trace: list[str] = []
    run_store = TracingRunStore(trace)

    class FailingResolver:
        def __init__(self) -> None:
            self.calls = 0

        def resolve(self, skill_ref: str, effective_parameters) -> tuple[DependencyRef, ...]:
            self.calls += 1
            raise DependencyResolutionError(f"missing dependency for {skill_ref}")

    resolver = FailingResolver()
    registry, payload = _registry(
        tmp_path,
        event_sink=TracingEventSink(trace),
        run_store=run_store,
        dependency_resolver=resolver,
    )

    first = registry.invoke_with_run_id(
        "test.echo",
        "1.0.0",
        payload.model_dump(mode="json"),
        run_id=RUN_ID,
    )
    recovered = registry.invoke_with_run_id(
        "test.echo",
        "1.0.0",
        payload.model_dump(mode="json"),
        run_id=RUN_ID,
    )

    intent = run_store.read_controlled_intent(RUN_ID)
    assert first.status is RunStatus.BLOCKED
    assert recovered == first
    assert resolver.calls == 1
    assert intent is not None
    assert intent.phase == "preflight"
    assert intent.dependencies is None
    assert run_store.read_invocation(RUN_ID) is None


def test_controlled_success_snapshots_empty_dependencies_once_before_reservation(
    tmp_path: Path,
) -> None:
    trace: list[str] = []
    run_store = TracingRunStore(trace)

    class OneShotResolver:
        def __init__(self) -> None:
            self.calls = 0

        def resolve(self, skill_ref: str, effective_parameters) -> tuple[DependencyRef, ...]:
            self.calls += 1
            if self.calls > 1:
                raise AssertionError("dependency authority was read more than once")
            return ()

    resolver = OneShotResolver()
    registry, payload = _registry(
        tmp_path,
        event_sink=TracingEventSink(trace),
        run_store=run_store,
        dependency_resolver=resolver,
    )

    terminal = registry.invoke_with_run_id(
        "test.echo",
        "1.0.0",
        payload.model_dump(mode="json"),
        run_id=RUN_ID,
    )

    intent = run_store.read_controlled_intent(RUN_ID)
    invocation = run_store.read_invocation(RUN_ID)
    assert terminal.status is RunStatus.SUCCEEDED
    assert resolver.calls == 1
    assert intent is not None
    assert intent.phase == "validated_input"
    assert intent.dependencies == ()
    assert invocation is not None
    assert invocation.dependencies == ()


def test_controlled_validated_retry_recomputes_and_rejects_dependency_drift(
    tmp_path: Path,
) -> None:
    trace: list[str] = []
    run_store = TracingRunStore(trace)
    original = DependencyRef(name="scene-gen", version="1", sha256="8" * 64)
    changed = DependencyRef(name="scene-gen", version="1", sha256="9" * 64)

    class MutableResolver:
        def __init__(self) -> None:
            self.dependencies = (original,)
            self.calls = 0

        def resolve(self, skill_ref: str, effective_parameters) -> tuple[DependencyRef, ...]:
            self.calls += 1
            return self.dependencies

    resolver = MutableResolver()
    registry, payload = _registry(
        tmp_path,
        event_sink=TracingEventSink(trace),
        run_store=run_store,
        dependency_resolver=resolver,
    )
    parameters = payload.model_dump(mode="json")
    registry.invoke_with_run_id("test.echo", "1.0.0", parameters, run_id=RUN_ID)
    resolver.dependencies = (changed,)

    with pytest.raises(ControlledRunIdentityError, match="another invocation"):
        registry.invoke_with_run_id("test.echo", "1.0.0", parameters, run_id=RUN_ID)

    assert resolver.calls == 2


def test_controlled_identity_requires_a_durable_run_store(tmp_path: Path) -> None:
    registry, payload = _registry(
        tmp_path,
        event_sink=TracingEventSink([]),
        run_store=None,
    )

    with pytest.raises(ControlledRunPendingError, match="durable RunStore"):
        registry.invoke_with_run_id(
            "test.echo",
            "1.0.0",
            payload.model_dump(mode="json"),
            run_id=RUN_ID,
        )


@pytest.mark.parametrize("failure", ["conflict", "io"])
def test_controlled_intent_reservation_translates_authority_failures(
    tmp_path: Path,
    failure: str,
) -> None:
    class FailingReserveStore(TracingRunStore):
        def reserve_controlled_intent(self, intent: ControlledRunIntent) -> bool:
            if failure == "conflict":
                raise RunStoreConflictError("occupied")
            raise OSError("intent store unavailable")

    store = FailingReserveStore([])
    registry, _ = _registry(
        tmp_path,
        event_sink=TracingEventSink([]),
        run_store=store,
    )
    expected = ControlledRunIdentityError if failure == "conflict" else RunPersistenceError

    with pytest.raises(expected):
        registry.invoke_with_run_id("test.echo", "1.0.0", {}, run_id=RUN_ID)


def test_controlled_retry_rejects_artifact_or_dependency_authority_loss(tmp_path: Path) -> None:
    trace: list[str] = []
    store = TracingRunStore(trace)

    class SwitchableResolver:
        def __init__(self) -> None:
            self.failure: Exception | None = None

        def resolve(self, skill_ref: str, effective_parameters) -> tuple[DependencyRef, ...]:
            if self.failure is not None:
                raise self.failure
            return ()

    resolver = SwitchableResolver()
    registry, payload = _registry(
        tmp_path,
        event_sink=TracingEventSink(trace),
        run_store=store,
        dependency_resolver=resolver,
    )
    parameters = payload.model_dump(mode="json")
    registry.invoke_with_run_id("test.echo", "1.0.0", parameters, run_id=RUN_ID)
    resolved_payload = registry._artifact_resolver.resolve(payload).path
    resolved_payload.unlink()
    with pytest.raises(ControlledRunIdentityError, match="can no longer be resolved"):
        registry.invoke_with_run_id("test.echo", "1.0.0", parameters, run_id=RUN_ID)

    second_run = UUID("22345678-1234-4234-9234-123456789abc")
    second_payload = _put_json(
        registry._artifact_resolver,
        tmp_path,
        name="second-authority-payload",
        schema_version="robotwin.asset_catalog.v1",
        payload={"value": 2},
    )
    second_parameters = second_payload.model_dump(mode="json")
    registry.invoke_with_run_id("test.echo", "1.0.0", second_parameters, run_id=second_run)
    resolver.failure = DependencyResolutionError("dependency unavailable")
    with pytest.raises(ControlledRunIdentityError, match="can no longer be resolved"):
        registry.invoke_with_run_id("test.echo", "1.0.0", second_parameters, run_id=second_run)

    third_run = UUID("32345678-1234-4234-9234-123456789abc")
    third_payload = _put_json(
        registry._artifact_resolver,
        tmp_path,
        name="third-authority-payload",
        schema_version="robotwin.asset_catalog.v1",
        payload={"value": 3},
    )
    third_parameters = third_payload.model_dump(mode="json")
    resolver.failure = None
    registry.invoke_with_run_id("test.echo", "1.0.0", third_parameters, run_id=third_run)
    resolver.failure = ValueError("resolver crashed")
    with pytest.raises(ControlledRunIdentityError, match="can no longer be resolved"):
        registry.invoke_with_run_id("test.echo", "1.0.0", third_parameters, run_id=third_run)


@pytest.mark.parametrize(
    "resolver_error,expected_status",
    [
        (DependencyResolutionError("dependency unavailable"), RunStatus.BLOCKED),
        (ValueError("resolver crashed"), RunStatus.FAILED),
    ],
)
def test_controlled_dependency_failures_persist_exact_preflight_terminal(
    tmp_path: Path,
    resolver_error: Exception,
    expected_status: RunStatus,
) -> None:
    class FailingResolver:
        def resolve(self, skill_ref: str, effective_parameters) -> tuple[DependencyRef, ...]:
            raise resolver_error

    trace: list[str] = []
    store = TracingRunStore(trace)
    registry, payload = _registry(
        tmp_path,
        event_sink=TracingEventSink(trace),
        run_store=store,
        dependency_resolver=FailingResolver(),
    )

    terminal = registry.invoke_with_run_id(
        "test.echo",
        "1.0.0",
        payload.model_dump(mode="json"),
        run_id=RUN_ID,
    )

    assert terminal.status is expected_status
    assert terminal.invocation_digest is None
    assert store.read_controlled_intent(RUN_ID).phase == "preflight"


def test_controlled_missing_artifact_persists_exact_preflight_terminal(tmp_path: Path) -> None:
    trace: list[str] = []
    store = TracingRunStore(trace)
    registry, payload = _registry(
        tmp_path,
        event_sink=TracingEventSink(trace),
        run_store=store,
    )
    missing = payload.model_copy(
        update={
            "uri": f"artifact://sha256/{'7' * 64}",
            "sha256": "7" * 64,
        }
    )

    terminal = registry.invoke_with_run_id(
        "test.echo",
        "1.0.0",
        missing.model_dump(mode="json"),
        run_id=RUN_ID,
    )

    intent = store.read_controlled_intent(RUN_ID)
    assert terminal.status is RunStatus.BLOCKED
    assert terminal.blocker.code == "HARN_DEPENDENCY_UNAVAILABLE"
    assert terminal.blocker.artifact_refs == (missing,)
    assert intent is not None
    assert intent.phase == "preflight"


def test_controlled_parameters_reject_non_json_values_before_reservation(tmp_path: Path) -> None:
    trace: list[str] = []
    store = TracingRunStore(trace)
    registry, _ = _registry(
        tmp_path,
        event_sink=TracingEventSink(trace),
        run_store=store,
    )

    with pytest.raises(ControlledRunIdentityError, match="finite canonical JSON"):
        registry.invoke_with_run_id(
            "test.echo",
            "1.0.0",
            {"not_json": object()},
            run_id=RUN_ID,
        )

    assert store.intents == {}


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
    assert (
        retry_registry.invoke_with_run_id(
            "test.echo",
            "1.0.0",
            parameters,
            run_id=RUN_ID,
        )
        == terminal
    )
    assert len(journal.read(run_id=RUN_ID).events) == 3


@pytest.mark.parametrize("phase", ["artifact", "dependency", "validated"])
def test_controlled_reservation_loser_never_executes_without_the_winner_record(
    tmp_path: Path,
    phase: str,
) -> None:
    class LostRaceStore(TracingRunStore):
        def reserve_controlled_intent(self, intent: ControlledRunIntent) -> bool:
            self.intents[intent.run_id] = intent
            return False

    class FailingResolver:
        def resolve(self, skill_ref: str, effective_parameters) -> tuple[DependencyRef, ...]:
            raise DependencyResolutionError("dependency unavailable")

    trace: list[str] = []
    store = LostRaceStore(trace)
    registry, payload = _registry(
        tmp_path,
        event_sink=TracingEventSink(trace),
        run_store=store,
        dependency_resolver=(FailingResolver() if phase == "dependency" else None),
    )
    if phase == "artifact":
        parameters = payload.model_copy(
            update={
                "uri": f"artifact://sha256/{'7' * 64}",
                "sha256": "7" * 64,
            }
        ).model_dump(mode="json")
    else:
        parameters = payload.model_dump(mode="json")

    with pytest.raises(ControlledRunPendingError, match="will not be repeated"):
        registry.invoke_with_run_id("test.echo", "1.0.0", parameters, run_id=RUN_ID)

    assert store.read_invocation(RUN_ID) is None


def test_controlled_invocation_persistence_error_recovers_a_terminal_committed_by_winner(
    tmp_path: Path,
) -> None:
    handler_called = False

    class WinnerStore(TracingRunStore):
        def put_invocation(self, invocation: Invocation) -> None:
            self.invocations[invocation.run_id] = invocation
            started = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)
            ended = started + timedelta(milliseconds=1)
            self.states[invocation.run_id] = RunState(
                run_id=invocation.run_id,
                invocation_digest=invocation.invocation_digest,
                skill_id=invocation.skill_id,
                skill_version=invocation.skill_version,
                status=RunStatus.SUCCEEDED,
                attempt=1,
                max_attempts=invocation.max_attempts,
                started_at=started,
                ended_at=ended,
                events=(
                    Event(
                        seq=1,
                        timestamp=started,
                        stage="preflight",
                        attempt=1,
                        from_status=None,
                        to_status=RunStatus.RUNNING,
                        artifact_refs=(),
                    ),
                    Event(
                        seq=2,
                        timestamp=ended,
                        stage="complete",
                        attempt=1,
                        from_status=RunStatus.RUNNING,
                        to_status=RunStatus.SUCCEEDED,
                        artifact_refs=(),
                    ),
                ),
                artifacts=(),
                output={"winner": True},
                blocker=None,
            )
            raise OSError("lost acknowledgement")

    def handler(value: ArtifactRef, context) -> HandlerResult:
        nonlocal handler_called
        handler_called = True
        return HandlerResult(output=value)

    store = WinnerStore([])
    registry, payload = _registry(
        tmp_path,
        event_sink=TracingEventSink([]),
        run_store=store,
        handler=handler,
    )

    terminal = registry.invoke_with_run_id(
        "test.echo",
        "1.0.0",
        payload.model_dump(mode="json"),
        run_id=RUN_ID,
    )

    assert terminal == store.read_run_state(RUN_ID)
    assert handler_called is False


def test_controlled_recovery_rejects_changed_intent_invocation_and_preflight_occupancy(
    tmp_path: Path,
) -> None:
    trace: list[str] = []
    store = TracingRunStore(trace)
    registry, payload = _registry(
        tmp_path,
        event_sink=TracingEventSink(trace),
        run_store=store,
    )
    parameters = payload.model_dump(mode="json")
    registry.invoke_with_run_id("test.echo", "1.0.0", parameters, run_id=RUN_ID)
    invocation = store.invocations[RUN_ID]
    store.invocations[RUN_ID] = invocation.model_copy(
        update={"effective_parameters": {"forged": True}}
    )
    with pytest.raises(ControlledRunIdentityError, match="another invocation"):
        registry.invoke_with_run_id("test.echo", "1.0.0", parameters, run_id=RUN_ID)

    preflight_run = UUID("42345678-1234-4234-9234-123456789abc")
    registry.invoke_with_run_id("test.echo", "1.0.0", {}, run_id=preflight_run)
    store.invocations[preflight_run] = invocation.model_copy(update={"run_id": preflight_run})
    with pytest.raises(ControlledRunIdentityError, match="preflight intent"):
        registry.invoke_with_run_id("test.echo", "1.0.0", {}, run_id=preflight_run)


@pytest.mark.parametrize("validated", [False, True])
def test_controlled_execution_detects_intent_rewrite_after_reservation(
    tmp_path: Path,
    validated: bool,
) -> None:
    class RewritingStore(TracingRunStore):
        def __init__(self) -> None:
            super().__init__([])
            self.reserved = False

        def reserve_controlled_intent(self, intent: ControlledRunIntent) -> bool:
            self.intents[intent.run_id] = intent
            self.reserved = True
            return True

        def read_controlled_lifecycle(self, run_id: UUID) -> ControlledRunLifecycle:
            lifecycle = super().read_controlled_lifecycle(run_id)
            if self.reserved and lifecycle.intent is not None:
                return ControlledRunLifecycle(
                    intent=lifecycle.intent.model_copy(update={"raw_parameters_sha256": "9" * 64}),
                    invocation=lifecycle.invocation,
                    terminal=lifecycle.terminal,
                )
            return lifecycle

    store = RewritingStore()
    registry, payload = _registry(
        tmp_path,
        event_sink=TracingEventSink([]),
        run_store=store,
    )
    parameters = payload.model_dump(mode="json") if validated else {}

    with pytest.raises(ControlledRunIdentityError):
        registry.invoke_with_run_id("test.echo", "1.0.0", parameters, run_id=RUN_ID)


def test_late_invocation_delete_trigger_fails_before_handler_execution(
    tmp_path: Path,
) -> None:
    database = tmp_path / "late-invocation-trigger.sqlite3"
    handler_calls = 0

    def handler(value: ArtifactRef, context) -> HandlerResult:
        nonlocal handler_calls
        handler_calls += 1
        return HandlerResult(output=value, artifacts=(value,))

    registry, payload = _registry(
        tmp_path,
        event_sink=SQLiteEventJournal(database),
        run_store=SQLiteRunStore(database),
        handler=handler,
    )
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "CREATE TRIGGER late_invocation_delete "
            "AFTER INSERT ON harness_invocations "
            "BEGIN DELETE FROM harness_invocations WHERE run_id = NEW.run_id; END"
        )

    with pytest.raises(RunPersistenceError, match="controlled run lifecycle"):
        registry.invoke_with_run_id(
            "test.echo",
            "1.0.0",
            payload.model_dump(mode="json"),
            run_id=RUN_ID,
        )

    assert handler_calls == 0


@pytest.mark.parametrize(
    "trigger_body",
    [
        "DELETE FROM run_events WHERE event_id = NEW.event_id;",
        "UPDATE run_events SET stage = 'rewritten' WHERE event_id = NEW.event_id;",
    ],
    ids=("delete", "rewrite"),
)
def test_late_event_trigger_fails_before_handler_execution(
    tmp_path: Path,
    trigger_body: str,
) -> None:
    database = tmp_path / "late-event-trigger.sqlite3"
    handler_calls = 0

    def handler(value: ArtifactRef, context) -> HandlerResult:
        nonlocal handler_calls
        handler_calls += 1
        return HandlerResult(output=value, artifacts=(value,))

    registry, payload = _registry(
        tmp_path,
        event_sink=SQLiteEventJournal(database),
        run_store=SQLiteRunStore(database),
        handler=handler,
    )
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            f"CREATE TRIGGER late_event_change AFTER INSERT ON run_events BEGIN {trigger_body} END"
        )

    with pytest.raises(EventJournalCorruptionError, match="unsupported authority layout"):
        registry.invoke_with_run_id(
            "test.echo",
            "1.0.0",
            payload.model_dump(mode="json"),
            run_id=RUN_ID,
        )

    assert handler_calls == 0


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
