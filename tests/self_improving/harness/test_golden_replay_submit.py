"""Public P3 tracer for one qualified compile then replay workflow."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from self_improving.harness.application import (
    CompileApplication,
    CompileApplicationSettings,
    create_compile_application,
)
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.golden_compile_execution import (
    create_text2env_compile_execution_binding,
)
from self_improving.harness.golden_compile_policy import Text2EnvCompileEvidencePolicy
from self_improving.harness.golden_execution import (
    GoldenExecutionCatalog,
    QualifiedApplicationRunPendingError,
    create_golden_execution_catalog,
)
from self_improving.harness.golden_replay_execution import (
    _Text2EnvReplayApplicationAdapter,
    create_text2env_replay_execution_binding,
)
from self_improving.harness.golden_replay_policy import Text2EnvReplayEvidencePolicy
from self_improving.harness.golden_run import (
    GoldenRunCorruptionError,
    GoldenRunHarness,
    GoldenRunRegistryError,
)
from self_improving.harness.golden_store import SQLiteGoldenRunStore
from self_improving.harness.replay_application import (
    ReplayApplication,
    ReplayApplicationConfigurationError,
    ReplayApplicationRunPendingError,
    create_replay_application,
)
from self_improving.harness.run_store import ControlledRunLifecycle, SQLiteRunStore
from self_improving.harness.runtime_executor import RuntimeFailureCode
from self_improving.harness.schemas.common import ArtifactRef, RunState, RunStatus
from self_improving.harness.schemas.registry_snapshot import (
    QualifiedSkillSnapshot,
    RegistrySnapshot,
)
from self_improving.harness.schemas.text2env import (
    CompileConfig,
    RuntimeConfig,
    Text2EnvCompileInput,
    Text2EnvReplayInput,
    Text2EnvReplayOutput,
)
from self_improving.harness.schemas.workflow import (
    OperationSnapshot,
    RunCommandRequest,
    RunReadRequest,
    RunSnapshot,
)
from self_improving.harness.schemas.workflow_operation import (
    StateDeltaV2,
    ToolResultV2,
    TrustedToolReceiptV2,
    WorkflowOperationReceiptV2,
)
from self_improving.harness.trusted_state import TrustedWorldState, WorldFact, build_world_state
from tests.self_improving.harness import test_golden_run as base
from tests.self_improving.harness import test_replay_application as replay_application_fixture
from tests.self_improving.harness import test_text2env_replay_handler as replay_fixture

ROOT = Path(__file__).resolve().parents[3]
REQUEST = "Place a can on top of a plate."
COMPILE_RUN_ID = UUID("30000000-0000-4000-8000-000000000001")
REPLAY_RUN_ID = UUID("30000000-0000-4000-8000-000000000002")


@dataclass(frozen=True)
class ReplayCase:
    compile_application: CompileApplication
    replay_application: ReplayApplication
    executor: replay_fixture.RecordingExecutor
    artifact_store: LocalArtifactStore
    workflow_store: SQLiteGoldenRunStore
    harness: GoldenRunHarness
    revision_one: RunSnapshot
    compile_operation: OperationSnapshot
    compile_command: RunCommandRequest
    replay_input: Text2EnvReplayInput
    replay_command: RunCommandRequest


def _put_json(
    store: LocalArtifactStore,
    root: Path,
    *,
    name: str,
    payload: bytes,
    schema_version: str,
) -> ArtifactRef:
    return base._put(
        store,
        root,
        name=name,
        payload=payload,
        schema_version=schema_version,
    )


def _replay_application(
    tmp_path: Path,
    *,
    store: LocalArtifactStore,
    source: replay_fixture.Fixture,
) -> tuple[ReplayApplication, replay_fixture.RecordingExecutor]:
    settings = replay_application_fixture._factory_settings(
        tmp_path,
        evidence_root=store.root,
        allowed_asset_root=source.robotwin_root,
    )
    patches, executors = replay_application_fixture._factory_boundary_patches(
        store=store,
        robotwin_root=source.robotwin_root,
        runtime_root=tmp_path / "runtime-test-adapter",
    )
    with patches:
        application = create_replay_application(settings)
    return application, executors[0]


def _registry_snapshot(
    store: LocalArtifactStore,
    tmp_path: Path,
    *,
    compile_application: object,
    replay_application: ReplayApplication,
) -> ArtifactRef:
    entries = []
    for label, application in (
        ("compile", compile_application),
        ("replay", replay_application),
    ):
        descriptor = application.skills[0]
        bundle = ROOT / f"self_improving/harness/qualified_skills/text2env.{label}/1.0.0"
        report = _put_json(
            store,
            tmp_path,
            name=f"{label}-report.json",
            payload=(bundle / "report.json").read_bytes(),
            schema_version="harness.skill_qualification_report.v1",
        )
        descriptor_ref = _put_json(
            store,
            tmp_path,
            name=f"{label}-descriptor.json",
            payload=base._canonical_bytes(descriptor),
            schema_version=(
                "harness.skill_descriptor.v2"
                if label == "replay"
                else "harness.skill_descriptor.v1"
            ),
        )
        entries.append(
            QualifiedSkillSnapshot(
                skill_ref=f"text2env.{label}@1.0.0",
                mcp_tool_name=f"text2env_{label}_v1_0_0",
                descriptor_ref=descriptor_ref,
                qualification_ref=descriptor.qualification_artifact,
                qualification_report_ref=report,
            )
        )
    snapshot = RegistrySnapshot(
        schema_version="harness.registry_snapshot.v1",
        qualified_skills=tuple(entries),
    )
    return _put_json(
        store,
        tmp_path,
        name="compile-replay-registry.json",
        payload=base._canonical_bytes(snapshot),
        schema_version="harness.registry_snapshot.v1",
    )


def _read(store: LocalArtifactStore, ref: ArtifactRef, model):
    return model.model_validate_json(store.resolve(ref).path.read_bytes())


def _execution_catalog(
    case: ReplayCase | None = None,
    *,
    compile_application: CompileApplication | None = None,
    replay_application: ReplayApplication | None = None,
    replay_child_authority: SQLiteRunStore | None = None,
):
    if case is not None:
        compile_application = case.compile_application
        replay_application = case.replay_application
    return create_golden_execution_catalog(
        bindings=(
            create_text2env_compile_execution_binding(application=compile_application),
            create_text2env_replay_execution_binding(
                application=replay_application,
                child_authority=replay_child_authority,
            ),
        )
    )


def _build_revision_one(tmp_path: Path) -> ReplayCase:
    source = replay_fixture._fixture(tmp_path / "source")
    compile_application = create_compile_application(
        CompileApplicationSettings(
            state_root=tmp_path / "compile-state",
            external_catalog_roots=(tmp_path / "source",),
            allowed_asset_roots=(source.robotwin_root,),
            admission_date=date(2026, 9, 10),
            asset_library_root=tmp_path / "asset-library",
        )
    )
    store = LocalArtifactStore(compile_application.artifact_root)
    replay_application, executor = _replay_application(
        tmp_path,
        store=store,
        source=source,
    )
    registry = _registry_snapshot(
        store,
        tmp_path,
        compile_application=compile_application,
        replay_application=replay_application,
    )
    objective = _put_json(
        store,
        tmp_path,
        name="objective.json",
        payload=base.OBJECTIVE_EVIDENCE.replace(b"Place a can on a plate.", REQUEST.encode()),
        schema_version="harness.world_fact_evidence.v1",
    )
    workflow_store = SQLiteGoldenRunStore(tmp_path / "golden.sqlite3")
    harness = GoldenRunHarness(
        artifact_store=store,
        workflow_store=workflow_store,
        execution_catalog=_execution_catalog(
            compile_application=compile_application,
            replay_application=replay_application,
        ),
        clock=lambda: datetime.now(timezone.utc),
        workflow_id_factory=lambda: base.WORKFLOW_ID,
        child_run_id_factory=iter((COMPILE_RUN_ID, REPLAY_RUN_ID)).__next__,
    )
    revision_zero = harness.start(base._request(objective, registry))
    catalog = compile_application.snapshot_asset_catalog(tmp_path / "source/catalog.json")
    compile_command = RunCommandRequest(
        schema_version="harness.run_command_request.v1",
        command_id=UUID("20000000-0000-4000-8000-000000000001"),
        principal_id=revision_zero.principal_id,
        workflow_run_id=revision_zero.workflow_run_id,
        idempotency_key="qualified-step-001",
        expected_workflow_revision=0,
        expected_state_sha256=revision_zero.state_sha256,
        expected_receipt_head_sha256=revision_zero.receipt_head.sha256,
        skill_ref="text2env.compile@1.0.0",
        parameters=Text2EnvCompileInput(
            request=REQUEST,
            seed=7,
            asset_catalog=catalog,
            config=CompileConfig(generate_missing_assets=False),
        ).model_dump(mode="json"),
    )
    compile_operation = harness.submit(compile_command)
    revision_one = harness.read(
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=revision_zero.principal_id,
            workflow_run_id=revision_zero.workflow_run_id,
        )
    )
    trusted = _read(store, revision_one.state_ref, TrustedWorldState)
    package_fact = next(fact for fact in trusted.facts if fact.key == "environment.package")
    replay_input = Text2EnvReplayInput(
        environment_package=package_fact.value,
        runtime_config=RuntimeConfig(
            precheck_steps=0,
            settle_steps=900,
            contact_window_steps=120,
            video_frames=120,
            fps=12,
        ),
    )
    replay_command = RunCommandRequest(
        schema_version="harness.run_command_request.v1",
        command_id=UUID("20000000-0000-4000-8000-000000000002"),
        principal_id=revision_one.principal_id,
        workflow_run_id=revision_one.workflow_run_id,
        idempotency_key="qualified-step-001",
        expected_workflow_revision=1,
        expected_state_sha256=revision_one.state_sha256,
        expected_receipt_head_sha256=revision_one.receipt_head.sha256,
        skill_ref="text2env.replay@1.0.0",
        parameters=replay_input.model_dump(mode="json"),
    )
    return ReplayCase(
        compile_application=compile_application,
        replay_application=replay_application,
        executor=executor,
        artifact_store=store,
        workflow_store=workflow_store,
        harness=harness,
        revision_one=revision_one,
        compile_operation=compile_operation,
        compile_command=compile_command,
        replay_input=replay_input,
        replay_command=replay_command,
    )


def _restart_harness(
    case: ReplayCase,
    *,
    replay_application: ReplayApplication | None,
    replay_child_authority: SQLiteRunStore | None = None,
    child_run_id: UUID = REPLAY_RUN_ID,
) -> GoldenRunHarness:
    return GoldenRunHarness(
        artifact_store=case.artifact_store,
        workflow_store=case.workflow_store,
        execution_catalog=_execution_catalog(
            compile_application=None,
            replay_application=replay_application,
            replay_child_authority=replay_child_authority,
        ),
        clock=lambda: datetime.now(timezone.utc),
        child_run_id_factory=lambda: child_run_id,
    )


def _result_and_child(
    case: ReplayCase,
    operation: OperationSnapshot,
) -> tuple[ToolResultV2, RunState]:
    assert operation.tool_result is not None
    result = _read(case.artifact_store, operation.tool_result, ToolResultV2)
    return result, _read(case.artifact_store, result.run_state, RunState)


def test_same_golden_workflow_runs_qualified_compile_then_replay(tmp_path: Path) -> None:
    source = replay_fixture._fixture(tmp_path / "source")
    compile_settings = CompileApplicationSettings(
        state_root=tmp_path / "compile-state",
        external_catalog_roots=(tmp_path / "source",),
        allowed_asset_roots=(source.robotwin_root,),
        admission_date=date(2026, 9, 10),
        asset_library_root=tmp_path / "asset-library",
    )
    compile_application = create_compile_application(compile_settings)
    store = LocalArtifactStore(compile_application.artifact_root)
    replay_application, executor = _replay_application(
        tmp_path,
        store=store,
        source=source,
    )
    registry = _registry_snapshot(
        store,
        tmp_path,
        compile_application=compile_application,
        replay_application=replay_application,
    )
    objective = _put_json(
        store,
        tmp_path,
        name="objective.json",
        payload=base.OBJECTIVE_EVIDENCE.replace(b"Place a can on a plate.", REQUEST.encode()),
        schema_version="harness.world_fact_evidence.v1",
    )
    child_ids = iter((COMPILE_RUN_ID, REPLAY_RUN_ID))
    harness = GoldenRunHarness(
        artifact_store=store,
        workflow_store=SQLiteGoldenRunStore(tmp_path / "golden.sqlite3"),
        execution_catalog=create_golden_execution_catalog(
            bindings=(
                create_text2env_compile_execution_binding(application=compile_application),
                create_text2env_replay_execution_binding(application=replay_application),
            )
        ),
        clock=lambda: datetime.now(timezone.utc),
        workflow_id_factory=lambda: base.WORKFLOW_ID,
        child_run_id_factory=lambda: next(child_ids),
    )

    revision_zero = harness.start(base._request(objective, registry))
    assert revision_zero.revision == 0
    catalog = compile_application.snapshot_asset_catalog(tmp_path / "source/catalog.json")
    shared_key = "qualified-step-001"
    compile_operation = harness.submit(
        RunCommandRequest(
            schema_version="harness.run_command_request.v1",
            command_id=UUID("20000000-0000-4000-8000-000000000001"),
            principal_id=revision_zero.principal_id,
            workflow_run_id=revision_zero.workflow_run_id,
            idempotency_key=shared_key,
            expected_workflow_revision=0,
            expected_state_sha256=revision_zero.state_sha256,
            expected_receipt_head_sha256=revision_zero.receipt_head.sha256,
            skill_ref="text2env.compile@1.0.0",
            parameters=Text2EnvCompileInput(
                request=REQUEST,
                seed=7,
                asset_catalog=catalog,
                config=CompileConfig(generate_missing_assets=False),
            ).model_dump(mode="json"),
        )
    )
    assert compile_operation.status is RunStatus.SUCCEEDED
    revision_one = harness.read(
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=revision_zero.principal_id,
            workflow_run_id=revision_zero.workflow_run_id,
        )
    )
    assert revision_one.revision == 1
    trusted = _read(store, revision_one.state_ref, TrustedWorldState)
    package_fact = next(fact for fact in trusted.facts if fact.key == "environment.package")
    catalog_fact = next(fact for fact in trusted.facts if fact.key == "assets.catalog")
    assert package_fact.source_kind == catalog_fact.source_kind == "trusted_receipt"
    assert package_fact.source_artifact == catalog_fact.source_artifact
    assert package_fact.value["asset_catalog"] == catalog_fact.value
    replay_input = Text2EnvReplayInput(
        environment_package=package_fact.value,
        runtime_config=RuntimeConfig(
            precheck_steps=0,
            settle_steps=900,
            contact_window_steps=120,
            video_frames=120,
            fps=12,
        ),
    )
    replay_operation = harness.submit(
        RunCommandRequest(
            schema_version="harness.run_command_request.v1",
            command_id=UUID("20000000-0000-4000-8000-000000000002"),
            principal_id=revision_one.principal_id,
            workflow_run_id=revision_one.workflow_run_id,
            idempotency_key=shared_key,
            expected_workflow_revision=1,
            expected_state_sha256=revision_one.state_sha256,
            expected_receipt_head_sha256=revision_one.receipt_head.sha256,
            skill_ref="text2env.replay@1.0.0",
            parameters=replay_input.model_dump(mode="json"),
        )
    )

    assert replay_operation.status is RunStatus.SUCCEEDED
    assert replay_operation.committed_revision == 2
    assert len(executor.calls) == 1
    revision_two = harness.read(
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=revision_one.principal_id,
            workflow_run_id=revision_one.workflow_run_id,
        )
    )
    assert revision_two.revision == 2
    assert revision_two.child_runs == (COMPILE_RUN_ID, REPLAY_RUN_ID)
    replay_receipt = _read(store, revision_two.receipt_head, WorkflowOperationReceiptV2)
    assert replay_receipt.predecessor_receipt == revision_one.receipt_head
    assert replay_operation.tool_result is not None
    result = _read(store, replay_operation.tool_result, ToolResultV2)
    output = Text2EnvReplayOutput.model_validate(result.typed_output)
    assert result.fresh_observations == ()
    assert len(
        {(ref.sha256, ref.media_type, ref.schema_version) for ref in output.replay_artifacts}
    ) == len(output.replay_artifacts)
    tool_receipt = _read(store, result.trusted_receipt, TrustedToolReceiptV2)
    base_state = _read(store, tool_receipt.base_state, TrustedWorldState)
    linked_package = next(fact for fact in base_state.facts if fact.key == "environment.package")
    assert linked_package.value == replay_input.environment_package.model_dump(mode="json")
    assert (
        linked_package.source_artifact
        == _read(
            store,
            revision_one.receipt_head,
            WorkflowOperationReceiptV2,
        ).tool_receipt
    )
    assert result.state_delta is not None
    delta = _read(store, result.state_delta, StateDeltaV2)
    assert len(delta.mutations) == 1
    assert delta.mutations[0].key == "replay.runtime_evidence"
    assert delta.mutations[0].fact is not None
    assert delta.mutations[0].fact.value == output.runtime_evidence.model_dump(mode="json")
    assert hashlib.sha256(store.resolve(result.state_delta).path.read_bytes()).hexdigest() == (
        result.state_delta.sha256
    )


@pytest.mark.parametrize(
    ("failure_code", "expected_status"),
    [
        (RuntimeFailureCode.CAPABILITY_MISMATCH, RunStatus.BLOCKED),
        (RuntimeFailureCode.EVENT_PROTOCOL_ERROR, RunStatus.FAILED),
    ],
)
def test_replay_terminal_failure_does_not_advance_trusted_state(
    tmp_path: Path,
    failure_code: RuntimeFailureCode,
    expected_status: RunStatus,
) -> None:
    case = _build_revision_one(tmp_path)
    case.executor.failure_code = failure_code

    operation = case.harness.submit(case.replay_command)

    assert operation.status is expected_status
    assert operation.committed_revision == 2
    assert operation.tool_result is not None
    result = _read(case.artifact_store, operation.tool_result, ToolResultV2)
    assert result.status is expected_status
    assert result.state_delta is None
    assert result.typed_output is None
    assert result.fresh_observations == ()
    assert result.blocker is not None
    assert result.diagnostics == tuple(
        sorted(
            result.blocker.artifact_refs,
            key=lambda ref: (ref.sha256, ref.schema_version or "", ref.media_type),
        )
    )
    current = case.harness.read(
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=case.replay_command.principal_id,
            workflow_run_id=case.replay_command.workflow_run_id,
        )
    )
    assert current.revision == 2
    assert current.state_ref == case.revision_one.state_ref
    assert current.state_sha256 == case.revision_one.state_sha256


def test_replay_rejects_cross_workflow_or_stale_compile_output_before_child_intent(
    tmp_path: Path,
) -> None:
    first = _build_revision_one(tmp_path / "first")
    second = _build_revision_one(tmp_path / "second")
    changed = RunCommandRequest.model_validate(
        first.replay_command.model_copy(
            update={"parameters": second.replay_input.model_dump(mode="json")}
        ).model_dump(mode="python")
    )

    with pytest.raises(GoldenRunRegistryError) as captured:
        first.harness.submit(changed)

    assert captured.value.code == "HARN_INPUT_SCHEMA_INVALID"
    assert first.executor.calls == []
    assert SQLiteRunStore(first.replay_application.journal_path).read_controlled_lifecycle(
        REPLAY_RUN_ID
    ) == ControlledRunLifecycle(intent=None, invocation=None, terminal=None)


def test_restart_after_replay_reservation_before_child_intent_executes_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _build_revision_one(tmp_path)
    interrupted = _restart_harness(
        case,
        replay_application=case.replay_application,
        child_run_id=UUID("30000000-0000-4000-8000-000000000011"),
    )
    with monkeypatch.context() as scoped:
        scoped.setattr(
            GoldenExecutionCatalog,
            "invoke",
            lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        with pytest.raises(KeyboardInterrupt):
            interrupted.submit(case.replay_command)
    child = interrupted.read(
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=case.replay_command.principal_id,
            workflow_run_id=case.replay_command.workflow_run_id,
        )
    ).active_operation
    assert child == case.replay_command.command_id
    lifecycle = SQLiteRunStore(case.replay_application.journal_path).read_controlled_lifecycle(
        UUID("30000000-0000-4000-8000-000000000011")
    )
    assert lifecycle == ControlledRunLifecycle(intent=None, invocation=None, terminal=None)

    completed = _restart_harness(
        case,
        replay_application=case.replay_application,
        child_run_id=UUID("30000000-0000-4000-8000-000000000099"),
    ).submit(case.replay_command)
    assert completed.status is RunStatus.SUCCEEDED
    assert completed.child_run_id == UUID("30000000-0000-4000-8000-000000000011")
    assert len(case.executor.calls) == 1


def test_restart_after_replay_intent_before_invocation_stays_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _build_revision_one(tmp_path)
    child_run_id = UUID("30000000-0000-4000-8000-000000000012")
    interrupted = _restart_harness(
        case,
        replay_application=case.replay_application,
        child_run_id=child_run_id,
    )
    with monkeypatch.context() as scoped:
        scoped.setattr(
            SQLiteRunStore,
            "put_invocation",
            lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        with pytest.raises(KeyboardInterrupt):
            interrupted.submit(case.replay_command)
    lifecycle = SQLiteRunStore(case.replay_application.journal_path).read_controlled_lifecycle(
        child_run_id
    )
    assert lifecycle.intent is not None
    assert lifecycle.invocation is None
    assert lifecycle.terminal is None

    with monkeypatch.context() as scoped:
        scoped.setattr(
            replay_fixture.Text2EnvReplayHandler,
            "__call__",
            lambda *args, **kwargs: pytest.fail("intent-only recovery cannot call replay"),
        )
        recovered = _restart_harness(
            case,
            replay_application=case.replay_application,
        ).submit(case.replay_command)
    assert recovered.status is RunStatus.RUNNING
    assert recovered.child_run_id == child_run_id
    assert case.executor.calls == []


def test_restart_after_replay_invocation_before_terminal_never_reexecutes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _build_revision_one(tmp_path)
    child_run_id = UUID("30000000-0000-4000-8000-000000000013")
    interrupted = _restart_harness(
        case,
        replay_application=case.replay_application,
        child_run_id=child_run_id,
    )
    with monkeypatch.context() as scoped:
        scoped.setattr(
            replay_fixture.Text2EnvReplayHandler,
            "__call__",
            lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        with pytest.raises(KeyboardInterrupt):
            interrupted.submit(case.replay_command)
    lifecycle = SQLiteRunStore(case.replay_application.journal_path).read_controlled_lifecycle(
        child_run_id
    )
    assert lifecycle.intent is not None
    assert lifecycle.invocation is not None
    assert lifecycle.terminal is None

    recovered = _restart_harness(
        case,
        replay_application=case.replay_application,
    ).submit(case.replay_command)
    assert recovered.status is RunStatus.RUNNING
    assert recovered.child_run_id == child_run_id
    assert case.executor.calls == []


def test_restart_after_replay_terminal_before_parent_commit_only_commits_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _build_revision_one(tmp_path)
    child_run_id = UUID("30000000-0000-4000-8000-000000000014")
    interrupted = _restart_harness(
        case,
        replay_application=case.replay_application,
        child_run_id=child_run_id,
    )
    with monkeypatch.context() as scoped:
        scoped.setattr(
            SQLiteGoldenRunStore,
            "commit_operation",
            lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        with pytest.raises(KeyboardInterrupt):
            interrupted.submit(case.replay_command)
    lifecycle = SQLiteRunStore(case.replay_application.journal_path).read_controlled_lifecycle(
        child_run_id
    )
    assert lifecycle.terminal is not None
    assert len(case.executor.calls) == 1

    completed = _restart_harness(
        case,
        replay_application=None,
        replay_child_authority=SQLiteRunStore(case.replay_application.journal_path),
    ).submit(case.replay_command)
    assert completed.status is RunStatus.SUCCEEDED
    assert completed.child_run_id == child_run_id
    assert completed.committed_revision == 2
    assert len(case.executor.calls) == 1


def test_replay_policy_accepts_only_the_exact_compile_receipt_state(tmp_path: Path) -> None:
    case = _build_revision_one(tmp_path)
    policy = Text2EnvReplayEvidencePolicy()
    trusted = _read(case.artifact_store, case.revision_one.state_ref, TrustedWorldState)
    parameters = policy.normalize_parameters(case.replay_command.parameters)

    assert type(parameters) is Text2EnvReplayInput
    assert policy.evidence_policy_implementation_sha256 == (
        Text2EnvReplayEvidencePolicy().evidence_policy_implementation_sha256
    )
    policy.authorize(trusted, parameters)
    with pytest.raises(ValidationError):
        policy.normalize_parameters(case.replay_command.model_dump(mode="json"))
    with pytest.raises(TypeError, match="Text2EnvReplayInput"):
        policy.authorize(trusted, build_world_state((), as_of=trusted.as_of))
    with pytest.raises(ValueError, match="one trusted package"):
        policy.authorize(build_world_state((), as_of=trusted.as_of), parameters)


@pytest.mark.parametrize(
    "mode",
    ["package_provenance", "catalog_provenance", "different_receipt", "wrong_schema"],
)
def test_replay_policy_rejects_untrusted_or_unlinked_compile_facts(
    tmp_path: Path,
    mode: str,
) -> None:
    case = _build_revision_one(tmp_path)
    policy = Text2EnvReplayEvidencePolicy()
    trusted = _read(case.artifact_store, case.revision_one.state_ref, TrustedWorldState)
    package = next(fact for fact in trusted.facts if fact.key == "environment.package")
    catalog = next(fact for fact in trusted.facts if fact.key == "assets.catalog")
    other_receipt = package.source_artifact.model_copy(
        update={
            "name": "other-receipt.json",
            "uri": f"artifact://sha256/{'f' * 64}",
            "sha256": "f" * 64,
        }
    )
    if mode == "package_provenance":
        package = package.model_copy(update={"source_kind": "derived"})
    elif mode == "catalog_provenance":
        catalog = catalog.model_copy(update={"source_kind": "derived"})
    elif mode == "different_receipt":
        catalog = catalog.model_copy(update={"source_artifact": other_receipt})
    else:
        wrong_schema = package.source_artifact.model_copy(update={"schema_version": "other.v1"})
        package = package.model_copy(update={"source_artifact": wrong_schema})
        catalog = catalog.model_copy(update={"source_artifact": wrong_schema})
    changed = build_world_state((catalog, package), as_of=trusted.as_of)

    with pytest.raises(ValueError, match="share one trusted compile receipt"):
        policy.authorize(changed, case.replay_input)


@pytest.mark.parametrize("mode", ["package", "catalog"])
def test_replay_policy_rejects_parameters_different_from_trusted_compile_facts(
    tmp_path: Path,
    mode: str,
) -> None:
    case = _build_revision_one(tmp_path)
    policy = Text2EnvReplayEvidencePolicy()
    trusted = _read(case.artifact_store, case.revision_one.state_ref, TrustedWorldState)
    facts = list(trusted.facts)
    fact_key = "assets.catalog" if mode == "catalog" else "environment.package"
    index = next(index for index, fact in enumerate(facts) if fact.key == fact_key)
    fact = facts[index]
    if mode == "package":
        value = dict(fact.value)
        value["seed"] = int(value["seed"]) + 1
    else:
        value = dict(fact.value)
        value["name"] = "different-catalog.json"
    facts[index] = fact.model_copy(update={"value": value})
    changed = build_world_state(tuple(facts), as_of=trusted.as_of)

    with pytest.raises(ValueError, match="must equal"):
        policy.authorize(changed, case.replay_input)


def test_replay_policy_reconstructs_exact_output_claim_and_rejects_result_drift(
    tmp_path: Path,
) -> None:
    case = _build_revision_one(tmp_path)
    operation = case.harness.submit(case.replay_command)
    result, child = _result_and_child(case, operation)
    policy = Text2EnvReplayEvidencePolicy()
    claims = policy.claims(child)
    supporting = child.artifacts

    assert len(claims) == 1
    assert claims[0].key == "replay.runtime_evidence"
    assert policy.diagnostics(child) == ()
    policy.verify_result(state=child, claims=claims, result=result, supporting=supporting)
    observation = WorldFact(
        key="observation.test",
        value=True,
        source_kind="fresh_observation",
        source_artifact=result.run_state,
        observed_at=result.started_at,
        valid_until=None,
    )
    invalid_results = (
        (result.model_copy(update={"fresh_observations": (observation,)}), "fresh observation"),
        (result.model_copy(update={"typed_output": {}}), "output does not equal"),
    )
    for invalid_result, message in invalid_results:
        with pytest.raises(ValueError, match=message):
            policy.verify_result(
                state=child,
                claims=claims,
                result=invalid_result,
                supporting=supporting,
            )
    with pytest.raises(ValueError, match="claims"):
        policy.verify_result(state=child, claims=(), result=result, supporting=supporting)
    with pytest.raises(ValueError, match="diagnostics"):
        policy.verify_result(
            state=child,
            claims=claims,
            result=result.model_copy(update={"diagnostics": (result.run_state,)}),
            supporting=supporting,
        )
    output = Text2EnvReplayOutput.model_validate(child.output)
    supporting_without_output = tuple(
        ref
        for ref in supporting
        if (ref.sha256, ref.schema_version, ref.media_type)
        != (
            output.runtime_evidence.sha256,
            output.runtime_evidence.schema_version,
            output.runtime_evidence.media_type,
        )
    )
    with pytest.raises(ValueError, match="typed output references"):
        policy.verify_result(
            state=child,
            claims=claims,
            result=result,
            supporting=supporting_without_output,
        )


def test_replay_policy_uses_terminal_blocker_diagnostics_only(tmp_path: Path) -> None:
    case = _build_revision_one(tmp_path)
    case.executor.failure_code = RuntimeFailureCode.CAPABILITY_MISMATCH
    operation = case.harness.submit(case.replay_command)
    result, child = _result_and_child(case, operation)
    policy = Text2EnvReplayEvidencePolicy()

    assert policy.claims(child) == ()
    assert policy.diagnostics(child) == result.diagnostics
    policy.verify_result(state=child, claims=(), result=result, supporting=child.artifacts)
    with pytest.raises(ValueError, match="supporting evidence"):
        policy.verify_result(state=child, claims=(), result=result, supporting=())


def test_replay_binding_uses_only_factory_issued_application_authority(tmp_path: Path) -> None:
    case = _build_revision_one(tmp_path)
    adapter = _Text2EnvReplayApplicationAdapter(case.replay_application)

    assert adapter.artifact_root == case.artifact_store.root
    assert adapter.authority_path == case.replay_application.journal_path
    assert adapter.skills == case.replay_application.skills
    assert adapter.invocation(REPLAY_RUN_ID) is None
    assert adapter.run_state(REPLAY_RUN_ID) is None
    with pytest.raises(TypeError, match="exact ReplayApplication"):
        _Text2EnvReplayApplicationAdapter(object())  # type: ignore[arg-type]
    unsealed = ReplayApplication(
        artifact_store=case.replay_application._artifact_store,
        event_journal=case.replay_application._event_journal,
        run_store=case.replay_application._run_store,
        registry=case.replay_application._registry,
    )
    with pytest.raises(ReplayApplicationConfigurationError, match="factory-issued"):
        _Text2EnvReplayApplicationAdapter(unsealed)


def test_replay_adapter_checks_input_pending_translation_and_authority_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _build_revision_one(tmp_path)
    adapter = _Text2EnvReplayApplicationAdapter(case.replay_application)
    with pytest.raises(TypeError, match="Text2EnvReplayInput"):
        adapter.invoke(
            Text2EnvCompileInput.model_validate(case.compile_command.parameters),
            run_id=REPLAY_RUN_ID,
        )

    def pending(*args, **kwargs):
        raise ReplayApplicationRunPendingError("pending replay")

    with monkeypatch.context() as scoped:
        scoped.setattr(ReplayApplication, "invoke_typed", pending)
        with pytest.raises(QualifiedApplicationRunPendingError, match="pending replay"):
            adapter.invoke(case.replay_input, run_id=REPLAY_RUN_ID)

    case.replay_application._assembly_identity = ("changed",)
    with pytest.raises(ReplayApplicationConfigurationError, match="identity changed"):
        adapter.skills


def test_replay_binding_factory_rejects_lookalike_or_different_authorities(
    tmp_path: Path,
) -> None:
    case = _build_revision_one(tmp_path)
    with pytest.raises(TypeError, match="application"):
        create_text2env_replay_execution_binding(application=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="child_authority"):
        create_text2env_replay_execution_binding(child_authority=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="child authority"):
        create_text2env_replay_execution_binding(
            application=case.replay_application,
            child_authority=SQLiteRunStore(tmp_path / "other.sqlite3"),
        )
    authority_only = create_text2env_replay_execution_binding(
        child_authority=SQLiteRunStore(case.replay_application.journal_path)
    )
    assert authority_only.application is None

    drifted = create_text2env_replay_execution_binding(application=case.replay_application)
    object.__setattr__(drifted.policy, "skill_ref", "text2env.compile@1.0.0")
    with pytest.raises(ValueError, match="binding and evidence policy"):
        create_golden_execution_catalog(bindings=(drifted,))


def test_compile_and_replay_bindings_are_order_independent_and_exactly_unique(
    tmp_path: Path,
) -> None:
    case = _build_revision_one(tmp_path)
    compile_binding = create_text2env_compile_execution_binding(
        application=case.compile_application
    )
    replay_binding = create_text2env_replay_execution_binding(application=case.replay_application)
    compile_digest = compile_binding.policy.evidence_policy_implementation_sha256
    forward = create_golden_execution_catalog(bindings=(compile_binding, replay_binding))
    reverse = create_golden_execution_catalog(bindings=(replay_binding, compile_binding))

    assert forward.policy_for(compile_binding.skill_ref) is compile_binding.policy
    assert reverse.policy_for(replay_binding.skill_ref) is replay_binding.policy
    assert compile_binding.policy.evidence_policy_implementation_sha256 == compile_digest
    assert compile_digest == Text2EnvCompileEvidencePolicy().evidence_policy_implementation_sha256
    with pytest.raises(ValueError, match="duplicate"):
        create_golden_execution_catalog(bindings=(replay_binding, replay_binding))


def test_old_compile_operation_remains_byte_identical_after_replay_is_added(
    tmp_path: Path,
) -> None:
    case = _build_revision_one(tmp_path)
    replay = case.harness.submit(case.replay_command)

    assert replay.status is RunStatus.SUCCEEDED
    assert case.harness.submit(case.compile_command) == case.compile_operation
    assert len(case.executor.calls) == 1


def test_replay_runtime_evidence_is_part_of_the_verified_workflow_cas_closure(
    tmp_path: Path,
) -> None:
    case = _build_revision_one(tmp_path)
    operation = case.harness.submit(case.replay_command)
    _, child = _result_and_child(case, operation)
    output = Text2EnvReplayOutput.model_validate(child.output)
    case.artifact_store.resolve(output.runtime_evidence).path.unlink()

    with pytest.raises(GoldenRunCorruptionError, match="CAS closure"):
        case.harness.read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id=case.replay_command.principal_id,
                workflow_run_id=case.replay_command.workflow_run_id,
            )
        )
