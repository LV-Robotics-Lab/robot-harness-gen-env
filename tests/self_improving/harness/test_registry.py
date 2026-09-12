from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.events import RecordingEventSink
from self_improving.harness.registry import (
    DependencyResolutionError,
    HandlerResult,
    RegistryLookupError,
    RegistryRegistrationError,
    RunContext,
    SkillBlocked,
    SkillRegistry,
    StaticDependencyResolver,
    _artifact_refs,
    _content_identity,
)
from self_improving.harness.schemas import (
    ArtifactRef,
    Blocker,
    CompileConfig,
    DependencyRef,
    RunStatus,
    SkillDescriptor,
    Text2EnvCompileInput,
)


class SequenceClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(milliseconds=1)
        return value


def _put_json(
    store: LocalArtifactStore,
    tmp_path: Path,
    *,
    name: str,
    schema_version: str,
    payload: dict,
) -> ArtifactRef:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return store.put_file(
        path,
        name=name,
        media_type="application/json",
        schema_version=schema_version,
    )


def test_registry_invokes_exact_skill_with_content_identity_and_real_events(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
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
            "regression_command": "pytest -q tests/self_improving/harness/test_registry.py",
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
        max_attempts=1,
        qualification_artifact=qualification,
    )
    payload = _put_json(
        store,
        tmp_path,
        name="payload",
        schema_version="robotwin.asset_catalog.v1",
        payload={"value": 1},
    )
    sink = RecordingEventSink()
    resolved_inputs: list[ArtifactRef] = []
    handler_dependencies: list[tuple[DependencyRef, ...]] = []

    class ParameterAwareResolver:
        def resolve(
            self,
            skill_ref: str,
            effective_parameters: ArtifactRef,
        ) -> tuple[DependencyRef, ...]:
            assert skill_ref == "test.echo@1.0.0"
            resolved_inputs.append(effective_parameters)
            return (DependencyRef(name="echo-runtime", version="1", sha256="d" * 64),)

    run_ids = iter(
        (
            UUID("12345678-1234-4234-9234-123456789abc"),
            UUID("22345678-1234-4234-9234-123456789abc"),
        )
    )
    registry = SkillRegistry(
        artifact_resolver=store,
        dependency_resolver=ParameterAwareResolver(),
        event_sink=sink,
        clock=SequenceClock(),
        run_id_factory=lambda: next(run_ids),
    )

    def echo(value: ArtifactRef, context: RunContext) -> HandlerResult:
        handler_dependencies.append(context.dependencies)
        context.emit("echo.copy", artifact_refs=(value,))
        return HandlerResult(output=value, artifacts=(value,))

    registry.register(descriptor, echo)
    first = registry.invoke("test.echo", "1.0.0", payload.model_dump(mode="json"))

    assert registry.list() == (descriptor,)
    assert registry.resolve("test.echo", "1.0.0") == descriptor
    assert first.status == RunStatus.SUCCEEDED
    assert first.output == payload.model_dump(mode="json")
    assert first.artifacts == (payload,)
    assert [event.stage for event in first.events] == [
        "preflight",
        "echo.copy",
        "complete",
    ]
    invocation = registry.invocation(first.run_id)
    assert invocation is not None
    assert invocation.effective_parameters == payload.model_dump(mode="json")
    assert invocation.dependencies[0].name == "echo-runtime"
    assert handler_dependencies == [invocation.dependencies]
    assert resolved_inputs == [payload]

    alternate = tmp_path / "alternate.json"
    alternate.write_bytes(store.resolve(payload).path.read_bytes())
    relocated = payload.model_copy(update={"name": "renamed", "uri": alternate.as_uri()})
    second = registry.invoke("test.echo", "1.0.0", relocated.model_dump(mode="json"))
    assert second.invocation_digest == first.invocation_digest
    assert handler_dependencies == [invocation.dependencies, invocation.dependencies]
    assert resolved_inputs == [payload, relocated]
    assert len(sink.events) == 6


def _registered_echo(
    tmp_path: Path,
    *,
    skill_id: str = "test.echo",
    max_attempts: int = 1,
    handler=None,
    dependency_resolver=None,
):
    store = LocalArtifactStore(tmp_path / "cas")
    skill_ref = f"{skill_id}@1.0.0"
    report = _put_json(
        store,
        tmp_path,
        name=f"{skill_id.replace('.', '_')}_qualification_report",
        schema_version="harness.skill_qualification_report.v1",
        payload={"case": "case-v1", "status": "pass"},
    )
    qualification = _put_json(
        store,
        tmp_path,
        name=f"{skill_id.replace('.', '_')}_qualification",
        schema_version="harness.skill_qualification.v1",
        payload={
            "skill_ref": skill_ref,
            "status": "pass",
            "deterministic_case_id": "case-v1",
            "regression_command": "pytest -q tests/self_improving/harness/test_registry.py",
            "report_sha256": report.sha256,
        },
    )
    descriptor = SkillDescriptor(
        skill_id=skill_id,
        version="1.0.0",
        mcp_tool_name=f"{skill_id.replace('.', '_')}_v1_0_0",
        input_schema="harness.artifact_ref.v1",
        output_schema="harness.artifact_ref.v1",
        implementation_name="tests.handler",
        implementation_version="1",
        implementation_sha256="c" * 64,
        deterministic=True,
        max_attempts=max_attempts,
        qualification_artifact=qualification,
    )
    sink = RecordingEventSink()
    counter = iter(range(1, 100))
    registry = SkillRegistry(
        artifact_resolver=store,
        dependency_resolver=dependency_resolver or StaticDependencyResolver({}),
        event_sink=sink,
        clock=SequenceClock(),
        run_id_factory=lambda: UUID(f"{next(counter):08d}-1234-4234-9234-123456789abc"),
    )

    def default_handler(value: ArtifactRef, context: RunContext) -> HandlerResult:
        return HandlerResult(output=value, artifacts=(value,))

    selected_handler = handler or default_handler
    registry.register(descriptor, selected_handler)
    payload = _put_json(
        store,
        tmp_path,
        name=f"{skill_id.replace('.', '_')}_payload",
        schema_version="robotwin.asset_catalog.v1",
        payload={"value": 1},
    )
    return registry, store, descriptor, selected_handler, payload, sink


def test_registration_is_qualified_idempotent_and_immutable(tmp_path: Path) -> None:
    registry, store, descriptor, handler, _, _ = _registered_echo(tmp_path)
    registry.register(descriptor, handler)
    assert registry.list() == (descriptor,)

    with pytest.raises(RegistryRegistrationError, match="immutable Skill identity"):
        registry.register(descriptor, lambda value, context: HandlerResult(output=value))

    wrong_qualification = _put_json(
        store,
        tmp_path,
        name="wrong_qualification",
        schema_version="harness.skill_qualification.v1",
        payload={
            "skill_ref": "test.other@1.0.0",
            "status": "pass",
            "deterministic_case_id": "wrong",
            "regression_command": "pytest -q",
            "report_sha256": "f" * 64,
        },
    )
    wrong_descriptor = descriptor.model_copy(
        update={
            "skill_id": "test.other",
            "mcp_tool_name": "test_other_v1_0_0",
            "qualification_artifact": wrong_qualification,
        }
    )
    with pytest.raises(RegistryRegistrationError, match="qualification skill_ref"):
        registry.register(
            wrong_descriptor.model_copy(
                update={
                    "skill_id": "test.third",
                    "mcp_tool_name": "test_third_v1_0_0",
                }
            ),
            handler,
        )

    missing_report_qualification = _put_json(
        store,
        tmp_path,
        name="missing_report_qualification",
        schema_version="harness.skill_qualification.v1",
        payload={
            "skill_ref": "test.missing@1.0.0",
            "status": "pass",
            "deterministic_case_id": "missing-report",
            "regression_command": "pytest -q",
            "report_sha256": "e" * 64,
        },
    )
    missing_report_descriptor = descriptor.model_copy(
        update={
            "skill_id": "test.missing",
            "mcp_tool_name": "test_missing_v1_0_0",
            "qualification_artifact": missing_report_qualification,
        }
    )
    with pytest.raises(RegistryRegistrationError, match="qualification report"):
        registry.register(missing_report_descriptor, handler)


def test_registry_returns_typed_preflight_blockers_instead_of_raising(
    tmp_path: Path,
) -> None:
    registry, store, _, _, payload, _ = _registered_echo(tmp_path)

    missing_skill = registry.invoke("test.missing", "1.0.0", {})
    assert missing_skill.status == RunStatus.BLOCKED
    assert missing_skill.attempt == 0
    assert missing_skill.invocation_digest is None
    assert missing_skill.blocker is not None
    assert missing_skill.blocker.code == "HARN_SKILL_NOT_FOUND"

    missing_version = registry.invoke("test.echo", "2.0.0", {})
    assert missing_version.blocker is not None
    assert missing_version.blocker.code == "HARN_VERSION_UNSUPPORTED"
    with pytest.raises(RegistryLookupError) as lookup_error:
        registry.resolve("test.echo", "2.0.0")
    assert lookup_error.value.code == "HARN_VERSION_UNSUPPORTED"

    invalid = registry.invoke("test.echo", "1.0.0", {"name": "incomplete"})
    assert invalid.blocker is not None
    assert invalid.blocker.code == "HARN_INPUT_INVALID"

    stored_path = store.resolve(payload).path
    stored_path.unlink()
    unavailable = registry.invoke("test.echo", "1.0.0", payload.model_dump(mode="json"))
    assert unavailable.blocker is not None
    assert unavailable.blocker.code == "HARN_DEPENDENCY_UNAVAILABLE"
    assert unavailable.blocker.artifact_refs == (payload,)

    class MissingRuntime:
        def resolve(
            self,
            skill_ref: str,
            effective_parameters: ArtifactRef,
        ) -> tuple[DependencyRef, ...]:
            raise DependencyResolutionError(f"runtime missing for {skill_ref}")

    dependency_registry, _, _, _, dependency_payload, _ = _registered_echo(
        tmp_path / "dependency",
        skill_id="test.dependency",
        dependency_resolver=MissingRuntime(),
    )
    dependency_unavailable = dependency_registry.invoke(
        "test.dependency",
        "1.0.0",
        dependency_payload.model_dump(mode="json"),
    )
    assert dependency_unavailable.blocker is not None
    assert dependency_unavailable.blocker.code == "HARN_DEPENDENCY_UNAVAILABLE"

    class BrokenResolver:
        def resolve(
            self,
            skill_ref: str,
            effective_parameters: ArtifactRef,
        ) -> tuple[DependencyRef, ...]:
            raise RuntimeError("resolver implementation failed")

    broken_registry, _, _, _, broken_payload, _ = _registered_echo(
        tmp_path / "broken_dependency",
        skill_id="test.broken_dependency",
        dependency_resolver=BrokenResolver(),
    )
    broken = broken_registry.invoke(
        "test.broken_dependency",
        "1.0.0",
        broken_payload.model_dump(mode="json"),
    )
    assert broken.status == RunStatus.FAILED
    assert broken.blocker is not None
    assert broken.blocker.code == "HARN_INTERNAL"
    assert broken.attempt == 0


def test_registry_retries_only_retryable_handler_blockers(tmp_path: Path) -> None:
    calls = 0

    def flaky(value: ArtifactRef, context: RunContext) -> HandlerResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise SkillBlocked(
                Blocker(
                    code="T2E_REPLAY_FAILED",
                    message="runtime transient",
                    stage="replay",
                    retryable=True,
                    details={"attempt": calls},
                    unknowns=(),
                    artifact_refs=(),
                )
            )
        context.emit("replay.recovered")
        return HandlerResult(output=value, artifacts=(value,))

    registry, _, _, _, payload, _ = _registered_echo(
        tmp_path,
        skill_id="test.retry",
        max_attempts=2,
        handler=flaky,
    )
    state = registry.invoke("test.retry", "1.0.0", payload.model_dump(mode="json"))

    assert state.status == RunStatus.SUCCEEDED
    assert state.attempt == 2
    assert [event.attempt for event in state.events] == [1, 2, 2, 2]
    assert [event.stage for event in state.events] == [
        "preflight",
        "replay.retry",
        "replay.recovered",
        "complete",
    ]


def test_registry_maps_terminal_domain_and_unexpected_failures(tmp_path: Path) -> None:
    def blocked(value: ArtifactRef, context: RunContext) -> HandlerResult:
        raise SkillBlocked(
            Blocker(
                code="T2E_ASSET_UNAVAILABLE",
                message="no candidate",
                stage="compile",
                retryable=False,
                details={},
                unknowns=(),
                artifact_refs=(value,),
            )
        )

    blocked_registry, _, _, _, blocked_payload, _ = _registered_echo(
        tmp_path / "blocked",
        skill_id="test.blocked",
        handler=blocked,
    )
    blocked_state = blocked_registry.invoke(
        "test.blocked", "1.0.0", blocked_payload.model_dump(mode="json")
    )
    assert blocked_state.status == RunStatus.BLOCKED
    assert blocked_state.blocker is not None
    assert blocked_state.blocker.code == "T2E_ASSET_UNAVAILABLE"

    def broken(value: ArtifactRef, context: RunContext) -> HandlerResult:
        raise RuntimeError("implementation exploded")

    failed_registry, _, _, _, failed_payload, _ = _registered_echo(
        tmp_path / "failed",
        skill_id="test.failed",
        handler=broken,
    )
    failed_state = failed_registry.invoke(
        "test.failed", "1.0.0", failed_payload.model_dump(mode="json")
    )
    assert failed_state.status == RunStatus.FAILED
    assert failed_state.blocker is not None
    assert failed_state.blocker.code == "HARN_INTERNAL"
    assert failed_state.output is None


def test_registry_verifies_every_reference_before_content_deduplication(
    tmp_path: Path,
) -> None:
    invalid_ref = None

    def smuggle(value: ArtifactRef, context: RunContext) -> HandlerResult:
        assert invalid_ref is not None
        return HandlerResult(output=value, artifacts=(invalid_ref,))

    registry, _, _, _, payload, _ = _registered_echo(
        tmp_path / "output",
        skill_id="test.smuggle",
        handler=smuggle,
    )
    invalid_ref = payload.model_copy(update={"bytes": payload.bytes + 1})

    state = registry.invoke("test.smuggle", "1.0.0", payload.model_dump(mode="json"))

    assert state.status == RunStatus.FAILED
    assert state.blocker is not None
    assert state.blocker.code == "HARN_INTERNAL"
    assert state.blocker.details["error_type"] == "ArtifactResolutionError"

    blocker_ref = None

    def smuggle_in_blocker(value: ArtifactRef, context: RunContext) -> HandlerResult:
        assert blocker_ref is not None
        raise SkillBlocked(
            Blocker(
                code="T2E_ASSET_UNAVAILABLE",
                message="refused with unverified evidence",
                stage="compile",
                retryable=False,
                details={},
                unknowns=(),
                artifact_refs=(blocker_ref,),
            )
        )

    blocker_registry, _, _, _, blocker_payload, _ = _registered_echo(
        tmp_path / "blocker",
        skill_id="test.blocker_ref",
        handler=smuggle_in_blocker,
    )
    blocker_ref = blocker_payload.model_copy(update={"uri": "file:///missing/evidence.json"})

    blocker_state = blocker_registry.invoke(
        "test.blocker_ref",
        "1.0.0",
        blocker_payload.model_dump(mode="json"),
    )
    assert blocker_state.status == RunStatus.FAILED
    assert blocker_state.blocker is not None
    assert blocker_state.blocker.code == "HARN_INTERNAL"

    progress_ref = None

    def smuggle_in_progress(value: ArtifactRef, context: RunContext) -> HandlerResult:
        assert progress_ref is not None
        context.emit("malicious.progress", artifact_refs=(progress_ref,))
        return HandlerResult(output=value)

    progress_registry, _, _, _, progress_payload, progress_sink = _registered_echo(
        tmp_path / "progress",
        skill_id="test.progress_ref",
        handler=smuggle_in_progress,
    )
    progress_ref = progress_payload.model_copy(update={"sha256": "f" * 64})
    progress_state = progress_registry.invoke(
        "test.progress_ref",
        "1.0.0",
        progress_payload.model_dump(mode="json"),
    )
    assert progress_state.status == RunStatus.FAILED
    assert [event.event.stage for event in progress_sink.events] == ["preflight", "invoke"]


def test_registry_recursively_projects_content_and_artifact_references(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    artifact = _put_json(
        store,
        tmp_path,
        name="catalog",
        schema_version="robotwin.asset_catalog.v1",
        payload={"assets": []},
    )
    compile_input = Text2EnvCompileInput(
        request="put a can on the plate",
        seed=7,
        asset_catalog=artifact,
        config=CompileConfig(generate_missing_assets=False),
    )

    identity = _content_identity(compile_input)
    assert identity["asset_catalog"] == {
        "media_type": "application/json",
        "schema_version": "robotwin.asset_catalog.v1",
        "sha256": artifact.sha256,
    }
    assert identity["config"] == {"generate_missing_assets": False}
    assert _content_identity({"items": [artifact, "literal"]}) == {
        "items": [identity["asset_catalog"], "literal"]
    }
    assert _artifact_refs({"items": [compile_input, (artifact,)]}) == (
        artifact,
        artifact,
    )
