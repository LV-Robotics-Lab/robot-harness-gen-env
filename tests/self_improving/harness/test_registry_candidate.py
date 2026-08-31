from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

from self_improving.harness import (
    ArtifactRef,
    Blocker,
    DependencyRef,
    ExecutionReproducibility,
    HandlerResult,
    LocalArtifactStore,
    QualificationCandidate,
    QualificationCandidateEvaluation,
    RecordingEventSink,
    RegistryLookupError,
    RegistryRegistrationError,
    RunContext,
    RunPersistenceError,
    RunStatus,
    SkillBlocked,
    SkillDescriptor,
    SkillDescriptorV2,
    SkillRegistry,
)
from self_improving.harness.registry import _EvidenceInvariantRegistration


class SequenceClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(milliseconds=1)
        return value


class RecordingResolver:
    def __init__(self, store: LocalArtifactStore) -> None:
        self.store = store
        self.resolved: list[ArtifactRef] = []
        self.resolved_digests: list[str] = []

    def resolve(self, artifact: ArtifactRef):
        self.resolved.append(artifact)
        return self.store.resolve(artifact)

    def resolve_digest(self, sha256: str):
        self.resolved_digests.append(sha256)
        return self.store.resolve_digest(sha256)


class FixedDependencies:
    def __init__(self, dependency: DependencyRef) -> None:
        self.dependency = dependency
        self.calls: list[tuple[str, ArtifactRef]] = []

    def resolve(
        self,
        skill_ref: str,
        effective_parameters: ArtifactRef,
    ) -> tuple[DependencyRef, ...]:
        self.calls.append((skill_ref, effective_parameters))
        return (self.dependency,)


def _put_json(
    store: LocalArtifactStore,
    tmp_path: Path,
    *,
    name: str,
    schema_version: str,
    payload: dict[str, object],
) -> ArtifactRef:
    source = tmp_path / f"{name}.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return store.put_file(
        source,
        name=name,
        media_type="application/json",
        schema_version=schema_version,
    )


def _candidate() -> QualificationCandidate:
    return QualificationCandidate(
        skill_id="test.echo",
        version="1.0.0",
        input_schema="harness.artifact_ref.v1",
        output_schema="harness.artifact_ref.v1",
        max_attempts=2,
    )


def _registry(
    tmp_path: Path,
) -> tuple[
    SkillRegistry,
    LocalArtifactStore,
    RecordingResolver,
    FixedDependencies,
    RecordingEventSink,
]:
    store = LocalArtifactStore(tmp_path / "cas")
    artifacts = RecordingResolver(store)
    dependencies = FixedDependencies(
        DependencyRef(name="echo-runtime", version="1", sha256="d" * 64)
    )
    sink = RecordingEventSink()
    ids = iter(
        (
            UUID("12345678-1234-4234-9234-123456789abc"),
            UUID("22345678-1234-4234-9234-123456789abc"),
        )
    )
    registry = SkillRegistry(
        artifact_resolver=artifacts,
        dependency_resolver=dependencies,
        event_sink=sink,
        clock=SequenceClock(),
        run_id_factory=lambda: next(ids),
    )
    return registry, store, artifacts, dependencies, sink


def test_candidate_evaluation_uses_production_execution_without_registration_or_qualification(
    tmp_path: Path,
) -> None:
    registry, store, artifacts, dependencies, sink = _registry(tmp_path)
    payload = _put_json(
        store,
        tmp_path,
        name="payload",
        schema_version="robotwin.asset_catalog.v1",
        payload={"value": 1},
    )
    calls = 0

    def echo(value: ArtifactRef, context: RunContext) -> HandlerResult:
        nonlocal calls
        calls += 1
        assert context.attempt == 1
        context.emit("echo.copy", artifact_refs=(value,))
        return HandlerResult(output=value, artifacts=(value,))

    result = registry.evaluate_candidate(
        _candidate(),
        echo,
        payload.model_dump(mode="json"),
    )

    assert isinstance(result, QualificationCandidateEvaluation)
    assert result.mode == "qualification_candidate"
    assert result.candidate == _candidate()
    assert result.state.status == RunStatus.SUCCEEDED
    assert result.state.attempt == 1
    assert result.state.max_attempts == 2
    assert result.state.output == payload.model_dump(mode="json")
    assert result.state.artifacts == (payload,)
    assert result.invocation is not None
    assert result.invocation == registry.invocation(result.state.run_id)
    assert result.invocation.dependencies == (dependencies.dependency,)
    assert [event.stage for event in result.state.events] == [
        "qualification_candidate.preflight",
        "qualification_candidate.echo.copy",
        "qualification_candidate.complete",
    ]
    assert [event.event for event in sink.events] == list(result.state.events)
    assert calls == 1
    assert dependencies.calls == [("test.echo@1.0.0", payload)]
    assert artifacts.resolved == [payload, payload, payload, payload]
    assert artifacts.resolved_digests == []
    assert "qualification_artifact" not in QualificationCandidate.model_fields
    assert registry.list() == ()
    with pytest.raises(RegistryLookupError, match="Skill not found"):
        registry.resolve("test.echo", "1.0.0")


def test_candidate_digest_matches_later_qualified_registry_execution(tmp_path: Path) -> None:
    registry, store, _, _, _ = _registry(tmp_path)
    payload = _put_json(
        store,
        tmp_path,
        name="payload",
        schema_version="robotwin.asset_catalog.v1",
        payload={"value": 1},
    )

    def echo(value: ArtifactRef, _context: RunContext) -> HandlerResult:
        return HandlerResult(output=value, artifacts=(value,))

    candidate = registry.evaluate_candidate(
        _candidate(),
        echo,
        payload.model_dump(mode="json"),
    )
    report = _put_json(
        store,
        tmp_path,
        name="qualification_report",
        schema_version="harness.skill_qualification_report.v1",
        payload={"case": "echo-v1", "status": "pass"},
    )
    qualification = _put_json(
        store,
        tmp_path,
        name="qualification",
        schema_version="harness.skill_qualification.v1",
        payload={
            "skill_ref": "test.echo@1.0.0",
            "status": "pass",
            "deterministic_case_id": "echo-v1",
            "regression_command": "pytest -q",
            "report_sha256": report.sha256,
        },
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
        max_attempts=2,
        qualification_artifact=qualification,
    )
    registry.register(descriptor, echo)

    qualified = registry.invoke("test.echo", "1.0.0", payload.model_dump(mode="json"))

    assert candidate.invocation is not None
    assert qualified.invocation_digest == candidate.invocation.invocation_digest
    assert registry.list() == (descriptor,)


def test_ordinary_registration_rejects_v2_even_with_generic_pass_and_lambda(
    tmp_path: Path,
) -> None:
    registry, store, _, _, _ = _registry(tmp_path)
    report = _put_json(
        store,
        tmp_path,
        name="replay_qualification_report",
        schema_version="harness.skill_qualification_report.v1",
        payload={"case": "replay-v1", "status": "pass"},
    )
    qualification = _put_json(
        store,
        tmp_path,
        name="replay_qualification",
        schema_version="harness.skill_qualification.v1",
        payload={
            "skill_ref": "text2env.replay@1.0.0",
            "status": "pass",
            "deterministic_case_id": "replay-v1",
            "regression_command": "robot-harness-qualify-replay",
            "report_sha256": report.sha256,
        },
    )
    descriptor = SkillDescriptorV2(
        skill_id="text2env.replay",
        version="1.0.0",
        mcp_tool_name="text2env_replay_v1_0_0",
        input_schema="harness.text2env_replay_input.v1",
        output_schema="harness.text2env_replay_output.v1",
        implementation_name="self_improving.harness.handlers.text2env_replay",
        implementation_version="1",
        implementation_sha256="c" * 64,
        reproducibility=ExecutionReproducibility.EVIDENCE_INVARIANT_REPEATABLE,
        max_attempts=2,
        qualification_artifact=qualification,
    )

    with pytest.raises(RegistryRegistrationError, match="verified evidence-invariant"):
        registry.register(
            descriptor,  # type: ignore[arg-type]
            lambda value, _context: HandlerResult(output=value),
        )
    with pytest.raises(RegistryRegistrationError, match="v1 descriptor"):
        registry.register(  # type: ignore[arg-type]
            object(),
            lambda value, _context: HandlerResult(output=value),
        )

    assert registry.list() == ()


def _policy_registration(
    resolver: FixedDependencies,
    *,
    handler: object | None = None,
) -> _EvidenceInvariantRegistration:
    report_bytes = b'{"case":"policy","status":"pass"}'
    report_sha256 = hashlib.sha256(report_bytes).hexdigest()
    qualification_bytes = json.dumps(
        {
            "deterministic_case_id": "policy",
            "regression_command": "pytest -q",
            "report_sha256": report_sha256,
            "skill_ref": "test.echo@1.0.0",
            "status": "pass",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    qualification_sha256 = hashlib.sha256(qualification_bytes).hexdigest()
    qualification = ArtifactRef(
        name="test_echo_qualification",
        uri=f"artifact://sha256/{qualification_sha256}",
        media_type="application/json",
        sha256=qualification_sha256,
        bytes=len(qualification_bytes),
        schema_version="harness.skill_qualification.v1",
    )
    descriptor = SkillDescriptorV2(
        skill_id="test.echo",
        version="1.0.0",
        mcp_tool_name="test_echo_v1_0_0",
        input_schema="harness.artifact_ref.v1",
        output_schema="harness.artifact_ref.v1",
        implementation_name="tests.echo",
        implementation_version="1",
        implementation_sha256="f" * 64,
        reproducibility=ExecutionReproducibility.EVIDENCE_INVARIANT_REPEATABLE,
        max_attempts=2,
        qualification_artifact=qualification,
    )

    class Handler:
        def __call__(self, value, _context):
            return HandlerResult(output=value)

    return _EvidenceInvariantRegistration(
        descriptor=descriptor,
        handler=handler if handler is not None else Handler(),  # type: ignore[arg-type]
        dependency_resolver=resolver,
        qualification_bytes=qualification_bytes,
        report_bytes=report_bytes,
    )


def test_evidence_policy_is_reverified_in_registry_without_handler_call_surface(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    resolver = FixedDependencies(DependencyRef(name="runtime", version="1", sha256="d" * 64))
    registration = _policy_registration(resolver)

    class Policy:
        calls = 0

        def verify_and_build(self, request, *, artifact_resolver):
            self.calls += 1
            assert request == {"bundle": "raw"}
            assert artifact_resolver is store
            return registration

    policy = Policy()
    registry = SkillRegistry(
        artifact_resolver=store,
        dependency_resolver=None,
        event_sink=RecordingEventSink(),
        clock=SequenceClock(),
        run_id_factory=lambda: UUID("12345678-1234-4234-9234-123456789abc"),
        evidence_invariant_policy=policy,
    )

    descriptor = registry.register_evidence_invariant({"bundle": "raw"})

    assert descriptor is registration.descriptor
    assert registry.list() == (registration.descriptor,)
    assert policy.calls == 1
    with pytest.raises(TypeError):
        registry.register_evidence_invariant({}, lambda *_args: None)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "attack",
    ["no_policy", "policy_error", "registry_error", "invalid_result", "lambda"],
)
def test_evidence_policy_attacks_fail_before_registry_mutation(
    tmp_path: Path,
    attack: str,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    resolver = FixedDependencies(DependencyRef(name="runtime", version="1", sha256="d" * 64))

    class Policy:
        def verify_and_build(self, request, *, artifact_resolver):
            if attack == "policy_error":
                raise ValueError("fake report or empty closure")
            if attack == "registry_error":
                raise RegistryRegistrationError("explicit policy refusal")
            if attack == "invalid_result":
                return object()
            return _policy_registration(
                resolver,
                handler=lambda value, context: HandlerResult(output=value),
            )

    registry = SkillRegistry(
        artifact_resolver=store,
        dependency_resolver=None,
        event_sink=RecordingEventSink(),
        clock=SequenceClock(),
        run_id_factory=lambda: UUID("12345678-1234-4234-9234-123456789abc"),
        evidence_invariant_policy=None if attack == "no_policy" else Policy(),
    )

    with pytest.raises(RegistryRegistrationError):
        registry.register_evidence_invariant({"bundle": "raw"})
    assert registry.list() == ()


def test_evidence_policy_supports_an_exact_preconfigured_resolver(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    resolver = FixedDependencies(DependencyRef(name="runtime", version="1", sha256="d" * 64))
    registration = _policy_registration(resolver)

    class Policy:
        def verify_and_build(self, request, *, artifact_resolver):
            return registration

    registry = SkillRegistry(
        artifact_resolver=store,
        dependency_resolver=resolver,
        event_sink=RecordingEventSink(),
        clock=SequenceClock(),
        run_id_factory=lambda: UUID("12345678-1234-4234-9234-123456789abc"),
        evidence_invariant_policy=Policy(),
    )

    registry.register_evidence_invariant({})

    assert registry.list() == (registration.descriptor,)


@pytest.mark.parametrize(
    "attack",
    [
        "not_bytes",
        "report_not_bytes",
        "invalid_qualification",
        "binding",
        "resolver_mismatch",
        "not_callable",
        "bound_method",
        "bad_resolver",
        "v1_descriptor",
        "wrong_reproducibility",
    ],
)
def test_evidence_policy_result_is_checked_by_generic_install_invariants(
    tmp_path: Path,
    attack: str,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    resolver = FixedDependencies(DependencyRef(name="runtime", version="1", sha256="d" * 64))
    registration = _policy_registration(resolver)
    configured_resolver = None

    class Handler:
        def __call__(self, value, context):
            return HandlerResult(output=value)

    if attack == "not_bytes":
        registration = replace(registration, qualification_bytes="bad")  # type: ignore[arg-type]
    elif attack == "report_not_bytes":
        registration = replace(registration, report_bytes="bad")  # type: ignore[arg-type]
    elif attack == "invalid_qualification":
        registration = replace(registration, qualification_bytes=b"{}")
    elif attack == "binding":
        registration = replace(registration, report_bytes=b"different")
    elif attack == "resolver_mismatch":
        configured_resolver = FixedDependencies(
            DependencyRef(name="other", version="1", sha256="e" * 64)
        )
    elif attack == "not_callable":
        registration = replace(registration, handler=object())  # type: ignore[arg-type]
    elif attack == "bound_method":
        registration = replace(registration, handler=Handler().__call__)
    elif attack == "bad_resolver":
        registration = replace(registration, dependency_resolver=object())  # type: ignore[arg-type]
    elif attack == "v1_descriptor":
        registration = replace(
            registration,
            descriptor=SkillDescriptor(  # type: ignore[arg-type]
                skill_id="test.echo",
                version="1.0.0",
                mcp_tool_name="test_echo_v1_0_0",
                input_schema="harness.artifact_ref.v1",
                output_schema="harness.artifact_ref.v1",
                implementation_name="tests.echo",
                implementation_version="1",
                implementation_sha256="f" * 64,
                deterministic=True,
                max_attempts=2,
                qualification_artifact=registration.descriptor.qualification_artifact,
            ),
        )
    else:
        registration = replace(
            registration,
            descriptor=registration.descriptor.model_copy(
                update={"reproducibility": ExecutionReproducibility.CONTENT_BITWISE_DETERMINISTIC}
            ),
        )

    class Policy:
        def verify_and_build(self, request, *, artifact_resolver):
            return registration

    registry = SkillRegistry(
        artifact_resolver=store,
        dependency_resolver=configured_resolver,
        event_sink=RecordingEventSink(),
        clock=SequenceClock(),
        run_id_factory=lambda: UUID("12345678-1234-4234-9234-123456789abc"),
        evidence_invariant_policy=Policy(),
    )

    with pytest.raises(RegistryRegistrationError):
        registry.register_evidence_invariant({})
    assert registry.list() == ()


def test_policy_validation_and_persistence_errors_are_fail_closed(
    tmp_path: Path,
) -> None:
    run_id = UUID("12345678-1234-4234-9234-123456789abc")
    cause = RuntimeError("persistence failed")
    direct = RunPersistenceError(
        operation="direct",
        run_id=run_id,
        state=None,
        cause=cause,
    )
    assert direct.operation == "direct"
    assert direct.run_id == run_id
    assert direct.state is None
    assert direct.cause is cause
    assert "persistence failed" in str(direct)

    class FailingRunStore:
        def __init__(self, failure: str) -> None:
            self.failure = failure

        def put_invocation(self, invocation):
            if self.failure == "invocation":
                raise OSError("invocation unavailable")

        def put_run_state(self, state):
            if self.failure == "terminal":
                raise OSError("terminal unavailable")

        def read_invocation(self, requested_run_id):
            return ("durable", requested_run_id)

        def read_run_state(self, requested_run_id):
            return None

    store = LocalArtifactStore(tmp_path / "cas")
    payload = _put_json(
        store,
        tmp_path,
        name="payload",
        schema_version="robotwin.asset_catalog.v1",
        payload={"value": 1},
    )

    def make_registry(run_store):
        return SkillRegistry(
            artifact_resolver=store,
            dependency_resolver=StaticDependencies(
                (DependencyRef(name="runtime", version="1", sha256="d" * 64),)
            ),
            event_sink=RecordingEventSink(),
            clock=SequenceClock(),
            run_id_factory=lambda: run_id,
            run_store=run_store,
        )

    class StaticDependencies:
        def __init__(self, values):
            self.values = values

        def resolve(self, skill_ref, value):
            return self.values

    invocation_failure = make_registry(FailingRunStore("invocation"))
    with pytest.raises(RunPersistenceError) as captured:
        invocation_failure.evaluate_candidate(
            _candidate(),
            lambda value, context: HandlerResult(output=value),
            payload.model_dump(mode="json"),
        )
    assert captured.value.operation == "invocation"

    terminal_store = FailingRunStore("terminal")
    terminal_failure = make_registry(terminal_store)
    with pytest.raises(RunPersistenceError) as captured:
        terminal_failure.evaluate_candidate(
            _candidate(),
            lambda value, context: HandlerResult(output=value),
            payload.model_dump(mode="json"),
        )
    assert captured.value.operation == "terminal state"

    handler_failure = make_registry(None)
    expected = RunPersistenceError(
        operation="handler",
        run_id=run_id,
        state=None,
        cause=RuntimeError("handler persistence"),
    )

    def raise_persistence(value, context):
        raise expected

    with pytest.raises(RunPersistenceError) as captured:
        handler_failure.evaluate_candidate(
            _candidate(),
            raise_persistence,
            payload.model_dump(mode="json"),
        )
    assert captured.value is expected

    fallback = make_registry(terminal_store)
    unknown = UUID("22345678-1234-4234-9234-123456789abc")
    assert fallback.invocation(unknown) == ("durable", unknown)


def test_candidate_retries_with_explicit_mode_and_never_becomes_resolvable(
    tmp_path: Path,
) -> None:
    registry, store, _, _, _ = _registry(tmp_path)
    payload = _put_json(
        store,
        tmp_path,
        name="payload",
        schema_version="robotwin.asset_catalog.v1",
        payload={"value": 1},
    )
    calls = 0

    def retry_once(value: ArtifactRef, context: RunContext) -> HandlerResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise SkillBlocked(
                Blocker(
                    code="T2E_REPLAY_FAILED",
                    message="retry qualification candidate",
                    stage="replay",
                    retryable=True,
                    details={},
                    unknowns=(),
                    artifact_refs=(value,),
                )
            )
        return HandlerResult(output=value)

    result = registry.evaluate_candidate(
        _candidate(),
        retry_once,
        payload.model_dump(mode="json"),
    )

    assert result.state.status == RunStatus.SUCCEEDED
    assert result.state.attempt == 2
    assert [event.stage for event in result.state.events] == [
        "qualification_candidate.preflight",
        "qualification_candidate.replay.retry",
        "qualification_candidate.complete",
    ]
    assert calls == 2
    assert registry.list() == ()


def test_candidate_preflight_failure_is_marked_and_has_no_invocation(tmp_path: Path) -> None:
    registry, _, artifacts, dependencies, _ = _registry(tmp_path)

    result = registry.evaluate_candidate(_candidate(), lambda value, context: value, {})

    assert result.mode == "qualification_candidate"
    assert result.invocation is None
    assert result.state.status == RunStatus.BLOCKED
    assert result.state.attempt == 0
    assert result.state.blocker is not None
    assert result.state.blocker.code == "HARN_INPUT_INVALID"
    assert [event.stage for event in result.state.events] == [
        "qualification_candidate.preflight",
        "qualification_candidate.preflight",
    ]
    assert artifacts.resolved == []
    assert dependencies.calls == []
    assert registry.list() == ()
