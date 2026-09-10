"""Failure, recovery, and integrity cases for the GoldenRun submit boundary."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from self_improving.harness import golden_run as golden_run_module
from self_improving.harness import golden_store as golden_store_module
from self_improving.harness.application import (
    CompileApplication,
    CompileApplicationRunPendingError,
    create_compile_application,
)
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.golden_compile_execution import (
    _Text2EnvCompileApplicationAdapter,
    create_text2env_compile_execution_binding,
)
from self_improving.harness.golden_compile_policy import Text2EnvCompileEvidencePolicy
from self_improving.harness.golden_execution import (
    GoldenExecutionBinding,
    GoldenExecutionCatalog,
    QualifiedApplicationRunPendingError,
    _create_golden_execution_binding,
    create_golden_execution_catalog,
)
from self_improving.harness.golden_run import (
    GoldenRunConflictError,
    GoldenRunCorruptionError,
    GoldenRunHarness,
    GoldenRunHeadConflictError,
    GoldenRunNotFoundError,
    GoldenRunRegistryError,
)
from self_improving.harness.golden_store import SQLiteGoldenRunStore
from self_improving.harness.handlers.text2env_compile import Text2EnvCompileHandler
from self_improving.harness.run_store import ControlledRunLifecycle, SQLiteRunStore
from self_improving.harness.schemas.common import ArtifactRef, Blocker, RunState, RunStatus
from self_improving.harness.schemas.text2env import CompileConfig, Text2EnvCompileInput
from self_improving.harness.schemas.workflow import (
    OperationSnapshot,
    RunCommandRequest,
    RunReadRequest,
    RunSnapshot,
    WorkflowStartReceipt,
    WorkflowStatus,
)
from self_improving.harness.schemas.workflow_operation import (
    QualifiedSkillExecutionV2,
    ReceiptDerivedFactClaimV2,
    StateDeltaV2,
    ToolResultV2,
    TrustedToolReceiptV2,
    WorkflowOperationReceiptV2,
    state_delta_v2_sha256,
)
from self_improving.harness.trusted_state import TrustedWorldState, WorldFact, build_world_state
from tests.self_improving.harness import test_golden_run as base


@dataclass(frozen=True)
class GoldenCase:
    application: CompileApplication
    artifact_store: LocalArtifactStore
    workflow_store: SQLiteGoldenRunStore
    harness: GoldenRunHarness
    command: RunCommandRequest
    operation: OperationSnapshot


def _compile_catalog(
    *,
    application: CompileApplication | None = None,
    child_authority: SQLiteRunStore | None = None,
) -> GoldenExecutionCatalog:
    return create_golden_execution_catalog(
        bindings=(
            create_text2env_compile_execution_binding(
                application=application,
                child_authority=child_authority,
            ),
        )
    )


def _build_case(tmp_path: Path, *, generate_missing_assets: bool = True) -> GoldenCase:
    settings = base._compile_settings(tmp_path)
    application = create_compile_application(settings)
    artifact_store = LocalArtifactStore(application.artifact_root)
    objective = base._put(
        artifact_store,
        tmp_path,
        name="objective.json",
        payload=base.GENERATED_OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = base._qualified_registry_for_application(artifact_store, tmp_path, application)
    catalog_path = settings.external_catalog_roots[0] / "empty.json"
    base._empty_catalog(
        catalog_path,
        objects_root=settings.allowed_asset_roots[0] / "objects",
    )
    asset_catalog = application.snapshot_asset_catalog(catalog_path)
    workflow_store = SQLiteGoldenRunStore(tmp_path / "golden.sqlite3")
    harness = GoldenRunHarness(
        artifact_store=artifact_store,
        workflow_store=workflow_store,
        execution_catalog=_compile_catalog(application=application),
        clock=lambda: base.NOW,
        workflow_id_factory=lambda: base.WORKFLOW_ID,
        child_run_id_factory=lambda: UUID("30000000-0000-4000-8000-000000000001"),
    )
    started = harness.start(base._request(objective, registry))
    command = RunCommandRequest(
        schema_version="harness.run_command_request.v1",
        command_id=UUID("20000000-0000-4000-8000-000000000001"),
        principal_id=started.principal_id,
        workflow_run_id=started.workflow_run_id,
        idempotency_key="compile-001",
        expected_workflow_revision=started.revision,
        expected_state_sha256=started.state_sha256,
        expected_receipt_head_sha256=started.receipt_head.sha256,
        skill_ref="text2env.compile@1.0.0",
        parameters=Text2EnvCompileInput(
            request=base.GENERATED_OBJECTIVE,
            seed=77,
            asset_catalog=asset_catalog,
            config=CompileConfig(generate_missing_assets=generate_missing_assets),
        ).model_dump(mode="json"),
    )
    operation = harness.submit(command)
    return GoldenCase(
        application=application,
        artifact_store=artifact_store,
        workflow_store=workflow_store,
        harness=harness,
        command=command,
        operation=operation,
    )


@pytest.fixture
def golden_case(tmp_path: Path) -> GoldenCase:
    return _build_case(tmp_path)


def _read_json(case: GoldenCase, ref: ArtifactRef, model):
    return model.model_validate_json(case.artifact_store.resolve(ref).path.read_bytes())


def _result(case: GoldenCase) -> ToolResultV2:
    assert case.operation.tool_result is not None
    return _read_json(case, case.operation.tool_result, ToolResultV2)


def _store_transition_fixture(
    case: GoldenCase,
    path: Path,
) -> tuple[SQLiteGoldenRunStore, RunSnapshot, OperationSnapshot, RunSnapshot]:
    final = case.harness.read(
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=case.command.principal_id,
            workflow_run_id=case.command.workflow_run_id,
        )
    )
    workflow_receipt = _read_json(
        case,
        final.receipt_head,
        WorkflowOperationReceiptV2,
    )
    start_receipt = _read_json(
        case,
        workflow_receipt.predecessor_receipt,
        WorkflowStartReceipt,
    )
    initial = RunSnapshot.model_validate(
        final.model_copy(
            update={
                "revision": 0,
                "turn_seq": 0,
                "state_sha256": start_receipt.state_sha256,
                "state_ref": start_receipt.state_ref,
                "receipt_head": workflow_receipt.predecessor_receipt,
                "active_operation": None,
                "child_runs": (),
                "updated_at": final.started_at,
            }
        ).model_dump(mode="python")
    )
    running = OperationSnapshot.model_validate(
        case.operation.model_copy(
            update={
                "status": RunStatus.RUNNING,
                "committed_revision": None,
                "tool_result": None,
                "ended_at": None,
            }
        ).model_dump(mode="python")
    )
    source = case.workflow_store.read_snapshot(
        principal_id=case.command.principal_id,
        workflow_run_id=case.command.workflow_run_id,
    )
    assert source is not None
    store = SQLiteGoldenRunStore(path)
    store.get_or_create_start(
        principal_id=initial.principal_id,
        idempotency_key=source.idempotency_key,
        request_sha256=source.request_sha256,
        start_request_sha256=source.start_request_sha256,
        factory=lambda: initial,
    )
    return store, initial, running, final


def _reserve_transition(
    store: SQLiteGoldenRunStore,
    initial: RunSnapshot,
    running: OperationSnapshot,
):
    return store.reserve_operation(
        principal_id=running.principal_id,
        workflow_run_id=running.workflow_run_id,
        skill_ref=running.skill_ref,
        idempotency_key=running.idempotency_key,
        command_sha256=running.command.sha256,
        expected_workflow_revision=initial.revision,
        expected_state_sha256=initial.state_sha256,
        expected_receipt_head_sha256=initial.receipt_head.sha256,
        operation=running,
    )


def _terminal_parts(case: GoldenCase):
    _, prior, _, final = _store_transition_fixture(
        case,
        case.workflow_store.path.parent / "verification-copy.sqlite3",
    )
    receipt = _read_json(case, final.receipt_head, WorkflowOperationReceiptV2)
    return prior, final.receipt_head, receipt


def _restart_window_harness(
    case: GoldenCase,
    store: SQLiteGoldenRunStore,
    *,
    child_run_id: UUID,
) -> GoldenRunHarness:
    return GoldenRunHarness(
        artifact_store=case.artifact_store,
        workflow_store=store,
        execution_catalog=_compile_catalog(application=case.application),
        clock=lambda: base.NOW,
        child_run_id_factory=lambda: child_run_id,
    )


def test_execution_catalog_is_sealed_exact_and_actor_neutral(
    golden_case: GoldenCase,
) -> None:
    with pytest.raises(TypeError, match="authority factory"):
        GoldenExecutionCatalog(_token=object(), entries={})
    catalog = _compile_catalog(application=golden_case.application)
    policy = catalog.policy_for("text2env.compile@1.0.0")
    adapter = catalog.application_for("text2env.compile@1.0.0")
    assert policy is not None and adapter is not None
    assert catalog.artifact_roots == {golden_case.artifact_store.root}
    assert catalog.live_descriptors_for("missing.skill@1.0.0") == ()
    assert catalog.policy_for("missing.skill@1.0.0") is None
    assert catalog.child_authority_for("missing.skill@1.0.0") is None
    with pytest.raises(LookupError, match="no live qualified application"):
        catalog.invoke(
            "missing.skill@1.0.0", Text2EnvCompileInput.model_construct(), run_id=base.WORKFLOW_ID
        )
    assert adapter.invocation(golden_case.operation.child_run_id) is not None
    assert adapter.run_state(golden_case.operation.child_run_id) is not None
    with pytest.raises(TypeError, match="Text2EnvCompileInput"):
        adapter.invoke(build_world_state((), as_of=base.NOW), run_id=base.WORKFLOW_ID)


def test_generic_catalog_admits_only_sealed_independent_skill_bindings(
    golden_case: GoldenCase,
) -> None:
    compile_binding = create_text2env_compile_execution_binding(application=golden_case.application)
    compile_digest = compile_binding.policy.evidence_policy_implementation_sha256
    replay_policy = replace(
        Text2EnvCompileEvidencePolicy(),
        skill_ref="text2env.replay@1.0.0",
        evidence_policy_id="text2env.replay.test-evidence",
    )
    replay_binding = _create_golden_execution_binding(
        policy=replay_policy,
        application=None,
        child_authority=None,
    )
    catalog = create_golden_execution_catalog(bindings=(compile_binding, replay_binding))
    assert catalog.policy_for(compile_binding.skill_ref) is compile_binding.policy
    assert compile_binding.policy.evidence_policy_implementation_sha256 == compile_digest
    with pytest.raises(TypeError, match="exact tuple"):
        create_golden_execution_catalog(bindings=[])  # type: ignore[arg-type]
    unsealed = GoldenExecutionBinding(
        _token=object(),
        skill_ref=compile_binding.skill_ref,
        policy=compile_binding.policy,
        application=compile_binding.application,
        child_authority=compile_binding.child_authority,
    )
    with pytest.raises(TypeError, match="exact Skill binding factory"):
        create_golden_execution_catalog(bindings=(unsealed,))
    with pytest.raises(ValueError, match="duplicate"):
        create_golden_execution_catalog(bindings=(compile_binding, compile_binding))
    with pytest.raises(TypeError, match="exact SQLiteRunStore"):
        _create_golden_execution_binding(
            policy=compile_binding.policy,
            application=None,
            child_authority=object(),  # type: ignore[arg-type]
        )
    assert compile_binding.application is not None
    with pytest.raises(ValueError, match="not exactly bound"):
        _create_golden_execution_binding(
            policy=Text2EnvCompileEvidencePolicy(),
            application=compile_binding.application,
            child_authority=compile_binding.child_authority,
        )


@pytest.mark.parametrize("field", ["application", "child_authority"])
def test_execution_catalog_rejects_lookalike_authorities(field: str) -> None:
    with pytest.raises(TypeError, match=field):
        create_text2env_compile_execution_binding(**{field: object()})


def test_execution_catalog_rejects_a_different_child_authority(
    golden_case: GoldenCase,
    tmp_path: Path,
) -> None:
    other = SQLiteRunStore(tmp_path / "other.sqlite3")
    with pytest.raises(ValueError, match="child authority"):
        create_text2env_compile_execution_binding(
            application=golden_case.application,
            child_authority=other,
        )


def test_harness_rejects_catalog_from_another_cas(
    golden_case: GoldenCase,
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="share one exact CAS"):
        GoldenRunHarness(
            artifact_store=LocalArtifactStore(tmp_path / "other-cas"),
            workflow_store=golden_case.workflow_store,
            execution_catalog=_compile_catalog(application=golden_case.application),
        )
    with pytest.raises(TypeError, match="execution_catalog"):
        GoldenRunHarness(
            artifact_store=golden_case.artifact_store,
            workflow_store=golden_case.workflow_store,
            execution_catalog=object(),
        )


def test_submit_hides_a_workflow_from_another_principal(golden_case: GoldenCase) -> None:
    command = golden_case.command.model_copy(update={"principal_id": "tests/other"})
    with pytest.raises(GoldenRunNotFoundError):
        golden_case.harness.submit(RunCommandRequest.model_validate(command.model_dump()))


def test_submit_rejects_workflow_disappearance_at_operation_reservation(
    golden_case: GoldenCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _, _, _ = _store_transition_fixture(
        golden_case,
        tmp_path / "reservation-disappeared.sqlite3",
    )
    harness = _restart_window_harness(
        golden_case,
        store,
        child_run_id=UUID("30000000-0000-4000-8000-000000000031"),
    )
    monkeypatch.setattr(SQLiteGoldenRunStore, "reserve_operation", lambda *args, **kwargs: None)
    with pytest.raises(GoldenRunNotFoundError):
        harness.submit(golden_case.command)


@pytest.mark.parametrize("different_command", [False, True])
def test_submit_rechecks_a_concurrent_reservation_before_resume(
    golden_case: GoldenCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    different_command: bool,
) -> None:
    store, _, running, _ = _store_transition_fixture(
        golden_case,
        tmp_path / f"reservation-race-{different_command}.sqlite3",
    )
    displaced = running
    if different_command:
        changed = RunCommandRequest.model_validate(
            golden_case.command.model_copy(
                update={"command_id": UUID("20000000-0000-4000-8000-000000000032")}
            ).model_dump(mode="python")
        )
        changed_ref = base._put(
            golden_case.artifact_store,
            tmp_path,
            name="changed-command.json",
            payload=base._canonical_bytes(changed),
            schema_version="harness.run_command_request.v1",
        )
        displaced = running.model_copy(update={"command": changed_ref})
    stored = golden_store_module._StoredOperation(
        operation=displaced,
        command_sha256=displaced.command.sha256,
    )
    monkeypatch.setattr(
        SQLiteGoldenRunStore,
        "reserve_operation",
        lambda *args, **kwargs: stored,
    )
    harness = _restart_window_harness(
        golden_case,
        store,
        child_run_id=UUID("30000000-0000-4000-8000-000000000033"),
    )
    if different_command:
        with pytest.raises(GoldenRunConflictError, match="bound to another command"):
            harness.submit(golden_case.command)
        return
    monkeypatch.setattr(GoldenRunHarness, "_resume_operation", lambda self, op, request: op)
    assert harness.submit(golden_case.command) == displaced


@pytest.mark.parametrize(
    ("change", "error_type"),
    [
        ({"idempotency_key": "different"}, GoldenRunHeadConflictError),
        ({"expected_workflow_revision": 2}, GoldenRunHeadConflictError),
        ({"expected_state_sha256": "f" * 64}, GoldenRunHeadConflictError),
        ({"expected_receipt_head_sha256": "e" * 64}, GoldenRunHeadConflictError),
    ],
)
def test_submit_rejects_stale_or_conflicting_command_identity(
    golden_case: GoldenCase,
    change: dict[str, object],
    error_type: type[Exception],
) -> None:
    values = {
        "command_id": UUID("20000000-0000-4000-8000-000000000099"),
        "idempotency_key": "stale-command",
        **change,
    }
    command = RunCommandRequest.model_validate(
        golden_case.command.model_copy(update=values).model_dump(mode="python")
    )
    with pytest.raises(error_type):
        golden_case.harness.submit(command)


def test_submit_rejects_reused_idempotency_key_with_different_command(
    golden_case: GoldenCase,
) -> None:
    changed = RunCommandRequest.model_validate(
        golden_case.command.model_copy(
            update={"command_id": UUID("20000000-0000-4000-8000-000000000002")}
        ).model_dump(mode="python")
    )
    with pytest.raises(GoldenRunConflictError, match="different command"):
        golden_case.harness.submit(changed)


def test_submit_requires_live_registry_application_intersection(tmp_path: Path) -> None:
    settings = base._compile_settings(tmp_path)
    application = create_compile_application(settings)
    store = LocalArtifactStore(application.artifact_root)
    objective = base._put(
        store,
        tmp_path,
        name="objective.json",
        payload=base.GENERATED_OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = base._qualified_registry_for_application(store, tmp_path, application)
    started = GoldenRunHarness(
        artifact_store=store,
        workflow_store=SQLiteGoldenRunStore(tmp_path / "golden.sqlite3"),
        workflow_id_factory=lambda: base.WORKFLOW_ID,
        clock=lambda: base.NOW,
    ).start(base._request(objective, registry))
    command = RunCommandRequest(
        schema_version="harness.run_command_request.v1",
        command_id=UUID("20000000-0000-4000-8000-000000000001"),
        principal_id=started.principal_id,
        workflow_run_id=started.workflow_run_id,
        idempotency_key="compile-001",
        expected_workflow_revision=0,
        expected_state_sha256=started.state_sha256,
        expected_receipt_head_sha256=started.receipt_head.sha256,
        skill_ref="text2env.compile@1.0.0",
        parameters={},
    )
    harness = GoldenRunHarness(
        artifact_store=store,
        workflow_store=SQLiteGoldenRunStore(tmp_path / "golden.sqlite3"),
        clock=lambda: base.NOW,
    )
    with pytest.raises(GoldenRunRegistryError, match="no live qualified") as captured:
        harness.submit(command)
    assert captured.value.code == "HARN_SKILL_UNQUALIFIED"


@pytest.mark.parametrize(
    "change",
    [
        {"request": "A different objective."},
        {"request": base.GENERATED_OBJECTIVE, "seed": -1},
    ],
)
def test_submit_enforces_typed_input_and_trusted_objective(
    golden_case: GoldenCase,
    change: dict[str, object],
) -> None:
    parameters = {**golden_case.command.parameters, **change}
    command = RunCommandRequest.model_validate(
        golden_case.command.model_copy(
            update={
                "command_id": UUID("20000000-0000-4000-8000-000000000002"),
                "idempotency_key": "compile-invalid",
                "parameters": parameters,
            }
        ).model_dump(mode="python")
    )
    with pytest.raises(GoldenRunRegistryError) as captured:
        golden_case.harness.submit(command)
    assert captured.value.code == "HARN_INPUT_SCHEMA_INVALID"


def test_terminal_recovery_needs_only_policy_and_child_authority(
    golden_case: GoldenCase,
) -> None:
    recovered = GoldenRunHarness(
        artifact_store=golden_case.artifact_store,
        workflow_store=golden_case.workflow_store,
        execution_catalog=_compile_catalog(
            child_authority=SQLiteRunStore(golden_case.application.journal_path)
        ),
        clock=lambda: pytest.fail("terminal recovery cannot execute or read the clock"),
    )
    assert recovered.submit(golden_case.command) == golden_case.operation


def test_two_exact_commands_reconstruct_the_entire_receipt_chain(
    golden_case: GoldenCase,
) -> None:
    current = golden_case.harness.read(
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=golden_case.command.principal_id,
            workflow_run_id=golden_case.command.workflow_run_id,
        )
    )
    second = RunCommandRequest.model_validate(
        golden_case.command.model_copy(
            update={
                "command_id": UUID("20000000-0000-4000-8000-000000000002"),
                "idempotency_key": "compile-002",
                "expected_workflow_revision": 1,
                "expected_state_sha256": current.state_sha256,
                "expected_receipt_head_sha256": current.receipt_head.sha256,
            }
        ).model_dump(mode="python")
    )
    second_harness = GoldenRunHarness(
        artifact_store=golden_case.artifact_store,
        workflow_store=golden_case.workflow_store,
        execution_catalog=_compile_catalog(application=golden_case.application),
        clock=lambda: datetime.now(timezone.utc),
        child_run_id_factory=lambda: UUID("30000000-0000-4000-8000-000000000002"),
    )
    completed = second_harness.submit(second)
    assert completed.committed_revision == 2
    assert (
        second_harness.read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id=second.principal_id,
                workflow_run_id=second.workflow_run_id,
            )
        ).revision
        == 2
    )


def test_blocked_compile_commits_no_delta_and_no_state_change(tmp_path: Path) -> None:
    case = _build_case(tmp_path, generate_missing_assets=False)
    assert case.operation.status is RunStatus.BLOCKED
    result = _result(case)
    assert result.state_delta is None
    assert result.diagnostics
    current = case.harness.read(
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=case.command.principal_id,
            workflow_run_id=case.command.workflow_run_id,
        )
    )
    start_receipt = _read_json(
        case,
        _read_json(case, current.receipt_head, WorkflowOperationReceiptV2).predecessor_receipt,
        base.WorkflowStartReceipt,
    )
    assert current.state_ref == start_receipt.state_ref


def test_read_fails_closed_when_terminal_tool_result_disappears(
    golden_case: GoldenCase,
) -> None:
    assert golden_case.operation.tool_result is not None
    golden_case.artifact_store.resolve(golden_case.operation.tool_result).path.unlink()
    with pytest.raises(GoldenRunCorruptionError, match="CAS closure"):
        golden_case.harness.read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id=golden_case.command.principal_id,
                workflow_run_id=golden_case.command.workflow_run_id,
            )
        )


def test_adapter_translates_a_pending_controlled_child(
    golden_case: GoldenCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def pending(*args, **kwargs):
        raise CompileApplicationRunPendingError("pending")

    monkeypatch.setattr(CompileApplication, "invoke_typed", pending)
    adapter = _Text2EnvCompileApplicationAdapter(golden_case.application)
    with pytest.raises(QualifiedApplicationRunPendingError, match="pending"):
        adapter.invoke(
            Text2EnvCompileInput.model_validate(golden_case.command.parameters),
            run_id=UUID("30000000-0000-4000-8000-000000000099"),
        )


def test_compile_policy_rejects_wrong_owner_state_and_result_claims(
    golden_case: GoldenCase,
) -> None:
    catalog = _compile_catalog(application=golden_case.application)
    policy = catalog.policy_for("text2env.compile@1.0.0")
    assert policy is not None
    with pytest.raises(ValueError, match="does not own"):
        policy.normalize(
            RunCommandRequest.model_validate(
                golden_case.command.model_copy(
                    update={"skill_ref": "text2env.replay@1.0.0"}
                ).model_dump(mode="python")
            )
        )
    parameters = Text2EnvCompileInput.model_validate(golden_case.command.parameters)
    with pytest.raises(TypeError, match="Text2EnvCompileInput"):
        policy.authorize(
            build_world_state((), as_of=base.NOW), build_world_state((), as_of=base.NOW)
        )
    with pytest.raises(ValueError, match="one trusted"):
        policy.authorize(build_world_state((), as_of=base.NOW), parameters)
    result = _result(golden_case)
    child_state = _read_json(golden_case, result.run_state, RunState)
    claims = policy.claims(child_state)
    supporting = tuple(sorted(child_state.artifacts, key=lambda ref: ref.sha256))
    with pytest.raises(ValueError, match="fresh observations"):
        policy.verify_result(
            state=child_state,
            claims=claims,
            result=result.model_copy(
                update={
                    "fresh_observations": (
                        WorldFact(
                            key="observation.test",
                            value=True,
                            source_kind="fresh_observation",
                            source_artifact=result.trusted_receipt,
                            observed_at=result.started_at,
                            valid_until=None,
                        ),
                    )
                }
            ),
            supporting=supporting,
        )
    with pytest.raises(ValueError, match="output does not equal"):
        policy.verify_result(
            state=child_state,
            claims=claims,
            result=result.model_copy(update={"typed_output": {}}),
            supporting=supporting,
        )
    with pytest.raises(ValueError, match="claims"):
        policy.verify_result(
            state=child_state,
            claims=(),
            result=result,
            supporting=supporting,
        )
    with pytest.raises(ValueError, match="diagnostics"):
        policy.verify_result(
            state=child_state,
            claims=claims,
            result=result.model_copy(update={"diagnostics": (result.run_state,)}),
            supporting=supporting,
        )


def test_compile_policy_rejects_diagnostic_not_in_supporting_evidence(
    tmp_path: Path,
) -> None:
    case = _build_case(tmp_path, generate_missing_assets=False)
    result = _result(case)
    child_state = _read_json(case, result.run_state, RunState)
    policy = _compile_catalog(application=case.application).policy_for("text2env.compile@1.0.0")
    assert policy is not None
    assert policy.claims(child_state) == ()
    assert policy.diagnostics(child_state) == result.diagnostics
    with pytest.raises(ValueError, match="supporting evidence"):
        policy.verify_result(
            state=child_state,
            claims=(),
            result=result,
            supporting=(),
        )


def test_private_compile_adapter_requires_exact_application() -> None:
    with pytest.raises(TypeError, match="exact CompileApplication"):
        _Text2EnvCompileApplicationAdapter(object())


@pytest.mark.parametrize("mode", ["missing", "schema"])
def test_catalog_factory_rejects_application_descriptor_mismatch(
    golden_case: GoldenCase,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    descriptor = golden_case.application.skills[0]
    descriptors = (
        ()
        if mode == "missing"
        else (descriptor.model_copy(update={"input_schema": descriptor.output_schema}),)
    )
    monkeypatch.setattr(CompileApplication, "skills", property(lambda self: descriptors))
    with pytest.raises(ValueError, match="does not expose|not exactly bound"):
        _compile_catalog(application=golden_case.application)


def test_operation_store_detects_payload_and_authority_corruption(
    golden_case: GoldenCase,
) -> None:
    database = golden_case.workflow_store.path
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "UPDATE golden_workflow_operations SET operation_sha256 = ?",
            ("f" * 64,),
        )
    with pytest.raises(GoldenRunCorruptionError, match="payload checksum"):
        golden_case.workflow_store.read_operations(
            principal_id=golden_case.command.principal_id,
            workflow_run_id=golden_case.command.workflow_run_id,
        )


def test_operation_store_reserve_is_private_idempotent_and_head_bound(
    golden_case: GoldenCase,
    tmp_path: Path,
) -> None:
    store, initial, running, _ = _store_transition_fixture(
        golden_case,
        tmp_path / "transition.sqlite3",
    )
    assert (
        store.reserve_operation(
            principal_id="tests/other",
            workflow_run_id=running.workflow_run_id,
            skill_ref=running.skill_ref,
            idempotency_key=running.idempotency_key,
            command_sha256=running.command.sha256,
            expected_workflow_revision=0,
            expected_state_sha256=initial.state_sha256,
            expected_receipt_head_sha256=initial.receipt_head.sha256,
            operation=running,
        )
        is None
    )
    reserved = _reserve_transition(store, initial, running)
    assert reserved is not None and reserved.operation == running
    assert _reserve_transition(store, initial, running) == reserved
    with pytest.raises(GoldenRunConflictError, match="different command"):
        store.reserve_operation(
            principal_id=running.principal_id,
            workflow_run_id=running.workflow_run_id,
            skill_ref=running.skill_ref,
            idempotency_key=running.idempotency_key,
            command_sha256="f" * 64,
            expected_workflow_revision=0,
            expected_state_sha256=initial.state_sha256,
            expected_receipt_head_sha256=initial.receipt_head.sha256,
            operation=running,
        )
    assert (
        store.read_operation(
            principal_id="tests/other",
            workflow_run_id=running.workflow_run_id,
            skill_ref=running.skill_ref,
            idempotency_key=running.idempotency_key,
        )
        is None
    )
    assert (
        store.read_operation(
            principal_id=running.principal_id,
            workflow_run_id=running.workflow_run_id,
            skill_ref=running.skill_ref,
            idempotency_key="missing",
        )
        is None
    )
    assert (
        store.read_operations(
            principal_id="tests/other",
            workflow_run_id=running.workflow_run_id,
        )
        == ()
    )


@pytest.mark.parametrize(
    "change",
    [
        {"workflow_run_id": UUID("10000000-0000-4000-8000-000000000099")},
        {"principal_id": "tests/other"},
        {"skill_ref": "text2env.replay@1.0.0"},
        {"idempotency_key": "other"},
        {"command": None},
        {"base_revision": 1},
        {"started_at": base.NOW - timedelta(seconds=1)},
    ],
)
def test_operation_store_rejects_invalid_reservation_metadata(
    golden_case: GoldenCase,
    tmp_path: Path,
    change: dict[str, object],
) -> None:
    store, initial, running, _ = _store_transition_fixture(
        golden_case,
        tmp_path / "invalid-reserve.sqlite3",
    )
    if change == {"command": None}:
        changed = running.model_copy(
            update={
                "command": running.command.model_copy(
                    update={
                        "sha256": "f" * 64,
                        "uri": f"artifact://sha256/{'f' * 64}",
                    }
                )
            }
        )
    else:
        changed = running.model_copy(update=change)
    with pytest.raises(GoldenRunCorruptionError, match="authority metadata"):
        store.reserve_operation(
            principal_id=running.principal_id,
            workflow_run_id=running.workflow_run_id,
            skill_ref=running.skill_ref,
            idempotency_key=running.idempotency_key,
            command_sha256=running.command.sha256,
            expected_workflow_revision=initial.revision,
            expected_state_sha256=initial.state_sha256,
            expected_receipt_head_sha256=initial.receipt_head.sha256,
            operation=changed,
        )


@pytest.mark.parametrize(
    "field",
    [
        "expected_workflow_revision",
        "expected_state_sha256",
        "expected_receipt_head_sha256",
    ],
)
def test_operation_store_rejects_stale_reservation_head(
    golden_case: GoldenCase,
    tmp_path: Path,
    field: str,
) -> None:
    store, initial, running, _ = _store_transition_fixture(
        golden_case,
        tmp_path / "stale-reserve.sqlite3",
    )
    values = {
        "principal_id": running.principal_id,
        "workflow_run_id": running.workflow_run_id,
        "skill_ref": running.skill_ref,
        "idempotency_key": running.idempotency_key,
        "command_sha256": running.command.sha256,
        "expected_workflow_revision": initial.revision,
        "expected_state_sha256": initial.state_sha256,
        "expected_receipt_head_sha256": initial.receipt_head.sha256,
        "operation": running,
    }
    values[field] = 9 if field == "expected_workflow_revision" else "f" * 64
    with pytest.raises(GoldenRunHeadConflictError):
        store.reserve_operation(**values)


def test_operation_store_rejects_operation_id_collision(
    golden_case: GoldenCase,
    tmp_path: Path,
) -> None:
    store, initial, running, _ = _store_transition_fixture(
        golden_case,
        tmp_path / "collision.sqlite3",
    )
    payload = base._canonical_bytes(running)
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute(
            """
            INSERT INTO golden_workflow_operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(running.operation_id),
                str(UUID("10000000-0000-4000-8000-000000000099")),
                running.principal_id,
                running.skill_ref,
                "occupied",
                running.command.sha256,
                0,
                payload,
                base.sha256(payload).hexdigest(),
            ),
        )
    with pytest.raises(GoldenRunConflictError, match="operation identity"):
        _reserve_transition(store, initial, running)


def test_operation_store_scopes_idempotency_to_the_exact_skill(
    golden_case: GoldenCase,
    tmp_path: Path,
) -> None:
    store, initial, running, final = _store_transition_fixture(
        golden_case,
        tmp_path / "cross-skill-idempotency.sqlite3",
    )
    _reserve_transition(store, initial, running)
    committed = store.commit_operation(
        principal_id=running.principal_id,
        running=running,
        terminal=golden_case.operation,
        snapshot=final,
    )
    assert committed is not None
    replay_command = RunCommandRequest.model_validate(
        golden_case.command.model_copy(
            update={
                "command_id": UUID("20000000-0000-4000-8000-000000000022"),
                "skill_ref": "text2env.replay@1.0.0",
                "expected_workflow_revision": final.revision,
                "expected_state_sha256": final.state_sha256,
                "expected_receipt_head_sha256": final.receipt_head.sha256,
            }
        ).model_dump(mode="python")
    )
    command_ref = base._put(
        golden_case.artifact_store,
        tmp_path,
        name="replay-command.json",
        payload=base._canonical_bytes(replay_command),
        schema_version="harness.run_command_request.v1",
    )
    replay = OperationSnapshot(
        schema_version="harness.operation_snapshot.v1",
        operation_id=replay_command.command_id,
        workflow_run_id=running.workflow_run_id,
        principal_id=running.principal_id,
        idempotency_key=running.idempotency_key,
        command=command_ref,
        qualified_execution=running.qualified_execution,
        skill_ref=replay_command.skill_ref,
        status=RunStatus.RUNNING,
        base_revision=final.revision,
        committed_revision=None,
        child_run_id=UUID("30000000-0000-4000-8000-000000000022"),
        tool_result=None,
        started_at=final.updated_at,
        ended_at=None,
    )
    reserved = store.reserve_operation(
        principal_id=replay.principal_id,
        workflow_run_id=replay.workflow_run_id,
        skill_ref=replay.skill_ref,
        idempotency_key=replay.idempotency_key,
        command_sha256=replay.command.sha256,
        expected_workflow_revision=final.revision,
        expected_state_sha256=final.state_sha256,
        expected_receipt_head_sha256=final.receipt_head.sha256,
        operation=replay,
    )
    assert reserved is not None and reserved.operation == replay
    assert (
        store.read_operation(
            principal_id=running.principal_id,
            workflow_run_id=running.workflow_run_id,
            skill_ref=running.skill_ref,
            idempotency_key=running.idempotency_key,
        )
        == committed
    )
    assert (
        store.read_operation(
            principal_id=replay.principal_id,
            workflow_run_id=replay.workflow_run_id,
            skill_ref=replay.skill_ref,
            idempotency_key=replay.idempotency_key,
        )
        == reserved
    )


def test_operation_store_commit_is_idempotent_private_and_transition_bound(
    golden_case: GoldenCase,
    tmp_path: Path,
) -> None:
    store, initial, running, final = _store_transition_fixture(
        golden_case,
        tmp_path / "commit.sqlite3",
    )
    assert (
        store.commit_operation(
            principal_id="tests/other",
            running=running,
            terminal=golden_case.operation,
            snapshot=final,
        )
        is None
    )
    with pytest.raises(GoldenRunCorruptionError, match="reserved operation is absent"):
        store.commit_operation(
            principal_id=running.principal_id,
            running=running,
            terminal=golden_case.operation,
            snapshot=final,
        )
    _reserve_transition(store, initial, running)
    committed = store.commit_operation(
        principal_id=running.principal_id,
        running=running,
        terminal=golden_case.operation,
        snapshot=final,
    )
    assert committed is not None and committed.operation == golden_case.operation
    assert (
        store.commit_operation(
            principal_id=running.principal_id,
            running=running,
            terminal=golden_case.operation,
            snapshot=final,
        )
        == committed
    )
    with pytest.raises(GoldenRunCorruptionError, match="committed workflow head"):
        store.commit_operation(
            principal_id=running.principal_id,
            running=running,
            terminal=golden_case.operation.model_copy(update={"idempotency_key": "different"}),
            snapshot=final,
        )


@pytest.mark.parametrize(
    ("target", "change", "message"),
    [
        ("running", {"idempotency_key": "different"}, "changed before terminal"),
        ("terminal", {"idempotency_key": "different"}, "immutable authority"),
        ("snapshot", {"revision": 9}, "exact operation transition"),
        ("snapshot", {"workspace": "production"}, "immutable aggregate"),
    ],
)
def test_operation_store_rejects_invalid_terminal_transition(
    golden_case: GoldenCase,
    tmp_path: Path,
    target: str,
    change: dict[str, object],
    message: str,
) -> None:
    store, initial, running, final = _store_transition_fixture(
        golden_case,
        tmp_path / f"commit-{target}-{message[:3]}.sqlite3",
    )
    _reserve_transition(store, initial, running)
    supplied_running = running.model_copy(update=change) if target == "running" else running
    terminal = (
        golden_case.operation.model_copy(update=change)
        if target == "terminal"
        else golden_case.operation
    )
    snapshot = final.model_copy(update=change) if target == "snapshot" else final
    with pytest.raises(GoldenRunCorruptionError, match=message):
        store.commit_operation(
            principal_id=running.principal_id,
            running=supplied_running,
            terminal=terminal,
            snapshot=snapshot,
        )


def test_operation_store_rejects_non_successful_state_change(
    golden_case: GoldenCase,
    tmp_path: Path,
) -> None:
    store, initial, running, final = _store_transition_fixture(
        golden_case,
        tmp_path / "blocked-change.sqlite3",
    )
    _reserve_transition(store, initial, running)
    blocked = golden_case.operation.model_copy(update={"status": RunStatus.BLOCKED})
    with pytest.raises(GoldenRunCorruptionError, match="cannot change trusted state"):
        store.commit_operation(
            principal_id=running.principal_id,
            running=running,
            terminal=blocked,
            snapshot=final,
        )


@pytest.mark.parametrize(
    "mode",
    [
        "invalid_authority",
        "invalid_checksum",
        "invalid_payload",
        "noncanonical",
        "metadata",
        "skill_metadata",
    ],
)
def test_operation_store_rejects_each_corrupt_operation_row(
    golden_case: GoldenCase,
    mode: str,
) -> None:
    with closing(sqlite3.connect(golden_case.workflow_store.path)) as connection:
        if mode == "invalid_authority":
            connection.execute("UPDATE golden_workflow_operations SET base_revision = 'bad'")
        elif mode == "invalid_checksum":
            connection.execute("UPDATE golden_workflow_operations SET operation_sha256 = 'bad'")
        elif mode == "invalid_payload":
            payload = b"{}"
            connection.execute(
                "UPDATE golden_workflow_operations SET operation_json = ?, operation_sha256 = ?",
                (payload, base.sha256(payload).hexdigest()),
            )
        elif mode == "noncanonical":
            payload = connection.execute(
                "SELECT operation_json FROM golden_workflow_operations"
            ).fetchone()[0]
            payload = payload.replace(b"{", b"{ ", 1)
            connection.execute(
                "UPDATE golden_workflow_operations SET operation_json = ?, operation_sha256 = ?",
                (payload, base.sha256(payload).hexdigest()),
            )
        elif mode == "metadata":
            connection.execute("UPDATE golden_workflow_operations SET principal_id = 'tests/other'")
        else:
            connection.execute(
                "UPDATE golden_workflow_operations SET skill_ref = 'text2env.replay@1.0.0'"
            )
        connection.commit()
    with pytest.raises(GoldenRunCorruptionError):
        golden_case.workflow_store.read_operations(
            principal_id=golden_case.command.principal_id,
            workflow_run_id=golden_case.command.workflow_run_id,
        )


@pytest.mark.parametrize("mode", ["missing", "changed", "lost_start"])
def test_operation_store_rolls_back_incomplete_reservation_publication(
    golden_case: GoldenCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    store, initial, running, _ = _store_transition_fixture(
        golden_case,
        tmp_path / f"reserve-post-{mode}.sqlite3",
    )
    if mode == "missing":
        monkeypatch.setattr(golden_store_module, "_select_operation", lambda *args: None)
    elif mode == "changed":
        original = golden_store_module._decode_operation

        def changed(row):
            stored = original(row)
            return golden_store_module._StoredOperation(
                operation=stored.operation.model_copy(update={"idempotency_key": "changed"}),
                command_sha256=stored.command_sha256,
            )

        monkeypatch.setattr(golden_store_module, "_decode_operation", changed)
    else:
        original_update = golden_store_module._update_snapshot

        def lose_start(connection, snapshot):
            connection.execute("DELETE FROM golden_workflow_starts")
            original_update(connection, snapshot)

        monkeypatch.setattr(golden_store_module, "_update_snapshot", lose_start)
    with pytest.raises(GoldenRunCorruptionError):
        _reserve_transition(store, initial, running)
    assert (
        store.read_operation(
            principal_id=running.principal_id,
            workflow_run_id=running.workflow_run_id,
            skill_ref=running.skill_ref,
            idempotency_key=running.idempotency_key,
        )
        is None
    )


def test_operation_store_detects_head_change_before_terminal_commit(
    golden_case: GoldenCase,
    tmp_path: Path,
) -> None:
    store, initial, running, final = _store_transition_fixture(
        golden_case,
        tmp_path / "commit-head.sqlite3",
    )
    _reserve_transition(store, initial, running)
    active = store.read_snapshot(
        principal_id=running.principal_id,
        workflow_run_id=running.workflow_run_id,
    )
    assert active is not None
    changed = active.snapshot.model_copy(update={"active_operation": None})
    payload = base._canonical_bytes(changed)
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute(
            "UPDATE golden_workflow_starts SET snapshot_json = ?, snapshot_sha256 = ?",
            (payload, base.sha256(payload).hexdigest()),
        )
    with pytest.raises(GoldenRunHeadConflictError):
        store.commit_operation(
            principal_id=running.principal_id,
            running=running,
            terminal=golden_case.operation,
            snapshot=final,
        )


@pytest.mark.parametrize("mode", ["missing", "changed"])
def test_operation_store_rolls_back_incomplete_terminal_publication(
    golden_case: GoldenCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    store, initial, running, final = _store_transition_fixture(
        golden_case,
        tmp_path / f"commit-post-{mode}.sqlite3",
    )
    _reserve_transition(store, initial, running)
    if mode == "missing":
        original_select = golden_store_module._select_operation
        calls = 0

        def disappear(connection, operation_id):
            nonlocal calls
            calls += 1
            return original_select(connection, operation_id) if calls == 1 else None

        monkeypatch.setattr(golden_store_module, "_select_operation", disappear)
    else:
        original_decode = golden_store_module._decode_operation
        calls = 0

        def changed(row):
            nonlocal calls
            calls += 1
            stored = original_decode(row)
            if calls == 1:
                return stored
            return golden_store_module._StoredOperation(
                operation=stored.operation.model_copy(update={"idempotency_key": "changed"}),
                command_sha256=stored.command_sha256,
            )

        monkeypatch.setattr(golden_store_module, "_decode_operation", changed)
    with pytest.raises(GoldenRunCorruptionError):
        store.commit_operation(
            principal_id=running.principal_id,
            running=running,
            terminal=golden_case.operation,
            snapshot=final,
        )


def test_running_operation_recovers_without_child_authority(
    golden_case: GoldenCase,
    tmp_path: Path,
) -> None:
    store, initial, running, _ = _store_transition_fixture(
        golden_case,
        tmp_path / "running-no-child.sqlite3",
    )
    _reserve_transition(store, initial, running)
    recovered = GoldenRunHarness(
        artifact_store=golden_case.artifact_store,
        workflow_store=store,
        execution_catalog=_compile_catalog(),
        clock=lambda: pytest.fail("running recovery must not reserve again"),
    )
    assert recovered.submit(golden_case.command) == running
    assert (
        recovered.read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id=running.principal_id,
                workflow_run_id=running.workflow_run_id,
            )
        ).active_operation
        == running.operation_id
    )


def test_restart_after_reservation_before_child_intent_executes_once(
    golden_case: GoldenCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _, _, _ = _store_transition_fixture(
        golden_case,
        tmp_path / "reservation-before-intent.sqlite3",
    )
    child_run_id = UUID("30000000-0000-4000-8000-000000000011")
    interrupted = _restart_window_harness(
        golden_case,
        store,
        child_run_id=child_run_id,
    )
    with monkeypatch.context() as scoped:
        scoped.setattr(
            GoldenExecutionCatalog,
            "invoke",
            lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        with pytest.raises(KeyboardInterrupt):
            interrupted.submit(golden_case.command)
    assert SQLiteRunStore(golden_case.application.journal_path).read_controlled_lifecycle(
        child_run_id
    ) == ControlledRunLifecycle(intent=None, invocation=None, terminal=None)

    completed = _restart_window_harness(
        golden_case,
        store,
        child_run_id=UUID("30000000-0000-4000-8000-000000000099"),
    ).submit(golden_case.command)
    assert completed.status is RunStatus.SUCCEEDED
    assert completed.child_run_id == child_run_id


def test_restart_after_child_intent_before_invocation_stays_conservatively_running(
    golden_case: GoldenCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _, _, _ = _store_transition_fixture(
        golden_case,
        tmp_path / "intent-before-invocation.sqlite3",
    )
    child_run_id = UUID("30000000-0000-4000-8000-000000000012")
    interrupted = _restart_window_harness(
        golden_case,
        store,
        child_run_id=child_run_id,
    )
    with monkeypatch.context() as scoped:
        scoped.setattr(
            SQLiteRunStore,
            "put_invocation",
            lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        with pytest.raises(KeyboardInterrupt):
            interrupted.submit(golden_case.command)
    lifecycle = SQLiteRunStore(golden_case.application.journal_path).read_controlled_lifecycle(
        child_run_id
    )
    assert lifecycle.intent is not None
    assert lifecycle.invocation is None
    assert lifecycle.terminal is None

    with monkeypatch.context() as scoped:
        scoped.setattr(
            Text2EnvCompileHandler,
            "__call__",
            lambda *args, **kwargs: pytest.fail("intent-only recovery cannot invoke the handler"),
        )
        recovered = _restart_window_harness(
            golden_case,
            store,
            child_run_id=UUID("30000000-0000-4000-8000-000000000099"),
        ).submit(golden_case.command)
    assert recovered.status is RunStatus.RUNNING
    assert recovered.child_run_id == child_run_id


def test_restart_after_invocation_before_child_terminal_never_reexecutes(
    golden_case: GoldenCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _, _, _ = _store_transition_fixture(
        golden_case,
        tmp_path / "invocation-before-terminal.sqlite3",
    )
    child_run_id = UUID("30000000-0000-4000-8000-000000000013")
    interrupted = _restart_window_harness(
        golden_case,
        store,
        child_run_id=child_run_id,
    )
    with monkeypatch.context() as scoped:
        scoped.setattr(
            Text2EnvCompileHandler,
            "__call__",
            lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        with pytest.raises(KeyboardInterrupt):
            interrupted.submit(golden_case.command)
    lifecycle = SQLiteRunStore(golden_case.application.journal_path).read_controlled_lifecycle(
        child_run_id
    )
    assert lifecycle.intent is not None
    assert lifecycle.invocation is not None
    assert lifecycle.terminal is None

    with monkeypatch.context() as scoped:
        scoped.setattr(
            Text2EnvCompileHandler,
            "__call__",
            lambda *args, **kwargs: pytest.fail("invoked child recovery cannot invoke twice"),
        )
        recovered = _restart_window_harness(
            golden_case,
            store,
            child_run_id=UUID("30000000-0000-4000-8000-000000000099"),
        ).submit(golden_case.command)
    assert recovered.status is RunStatus.RUNNING
    assert recovered.child_run_id == child_run_id


def test_restart_after_child_terminal_before_parent_commit_only_commits_parent(
    golden_case: GoldenCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _, _, _ = _store_transition_fixture(
        golden_case,
        tmp_path / "terminal-before-parent-commit.sqlite3",
    )
    child_run_id = UUID("30000000-0000-4000-8000-000000000014")
    interrupted = _restart_window_harness(
        golden_case,
        store,
        child_run_id=child_run_id,
    )
    with monkeypatch.context() as scoped:
        scoped.setattr(
            SQLiteGoldenRunStore,
            "commit_operation",
            lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        with pytest.raises(KeyboardInterrupt):
            interrupted.submit(golden_case.command)
    lifecycle = SQLiteRunStore(golden_case.application.journal_path).read_controlled_lifecycle(
        child_run_id
    )
    assert lifecycle.terminal is not None

    with monkeypatch.context() as scoped:
        scoped.setattr(
            Text2EnvCompileHandler,
            "__call__",
            lambda *args, **kwargs: pytest.fail("terminal child recovery cannot invoke twice"),
        )
        completed = _restart_window_harness(
            golden_case,
            store,
            child_run_id=UUID("30000000-0000-4000-8000-000000000099"),
        ).submit(golden_case.command)
    assert completed.status is RunStatus.SUCCEEDED
    assert completed.child_run_id == child_run_id
    assert completed.committed_revision == 1


def test_running_operation_waits_when_child_authority_has_no_live_application(
    golden_case: GoldenCase,
    tmp_path: Path,
) -> None:
    store, initial, running, _ = _store_transition_fixture(
        golden_case,
        tmp_path / "running-child-only.sqlite3",
    )
    _reserve_transition(store, initial, running)
    child = SQLiteRunStore(tmp_path / "empty-child.sqlite3")
    recovered = GoldenRunHarness(
        artifact_store=golden_case.artifact_store,
        workflow_store=store,
        execution_catalog=_compile_catalog(child_authority=child),
    )
    assert recovered.submit(golden_case.command) == running


def test_running_operation_with_persisted_invocation_is_never_reexecuted(
    golden_case: GoldenCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, initial, running, _ = _store_transition_fixture(
        golden_case,
        tmp_path / "running-invoked.sqlite3",
    )
    _reserve_transition(store, initial, running)
    invocation = golden_case.application.invocation(golden_case.operation.child_run_id)
    assert invocation is not None
    child = SQLiteRunStore(tmp_path / "empty-child.sqlite3")
    monkeypatch.setattr(
        SQLiteRunStore,
        "read_controlled_lifecycle",
        lambda self, run_id: ControlledRunLifecycle(
            intent=None,
            invocation=invocation,
            terminal=None,
        ),
    )
    recovered = GoldenRunHarness(
        artifact_store=golden_case.artifact_store,
        workflow_store=store,
        execution_catalog=_compile_catalog(child_authority=child),
    )
    assert recovered.submit(golden_case.command) == running


def test_pending_application_leaves_recoverable_running_operation(
    golden_case: GoldenCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, initial, running, _ = _store_transition_fixture(
        golden_case,
        tmp_path / "running-pending.sqlite3",
    )
    child = SQLiteRunStore(golden_case.application.journal_path)
    empty = ControlledRunLifecycle(intent=None, invocation=None, terminal=None)
    monkeypatch.setattr(
        SQLiteRunStore,
        "read_controlled_lifecycle",
        lambda self, run_id: empty,
    )
    monkeypatch.setattr(
        CompileApplication,
        "invoke_typed",
        lambda *args, **kwargs: (_ for _ in ()).throw(CompileApplicationRunPendingError("pending")),
    )
    recovered = GoldenRunHarness(
        artifact_store=golden_case.artifact_store,
        workflow_store=store,
        execution_catalog=_compile_catalog(
            application=golden_case.application,
            child_authority=child,
        ),
        clock=lambda: running.started_at,
        child_run_id_factory=lambda: running.child_run_id,
    )
    assert recovered.submit(golden_case.command) == running


def test_child_lifecycle_must_match_the_frozen_execution(
    golden_case: GoldenCase,
) -> None:
    lifecycle = SQLiteRunStore(golden_case.application.journal_path).read_controlled_lifecycle(
        golden_case.operation.child_run_id
    )
    _, qualified = golden_case.harness._resolve_frozen_execution(golden_case.operation)
    with pytest.raises(GoldenRunCorruptionError, match="lacks"):
        golden_case.harness._verify_child_lifecycle(
            ControlledRunLifecycle(
                intent=None,
                invocation=lifecycle.invocation,
                terminal=lifecycle.terminal,
            ),
            golden_case.operation,
            golden_case.command,
            qualified,
        )
    assert lifecycle.intent is not None and lifecycle.invocation is not None
    with pytest.raises(GoldenRunCorruptionError, match="frozen execution"):
        golden_case.harness._verify_child_lifecycle(
            ControlledRunLifecycle(
                intent=lifecycle.intent.model_copy(update={"implementation_sha256": "f" * 64}),
                invocation=lifecycle.invocation,
                terminal=lifecycle.terminal,
            ),
            golden_case.operation,
            golden_case.command,
            qualified,
        )
    with pytest.raises(GoldenRunCorruptionError, match="frozen command"):
        golden_case.harness._verify_child_lifecycle(
            ControlledRunLifecycle(
                intent=lifecycle.intent,
                invocation=lifecycle.invocation.model_copy(update={"effective_parameters": {}}),
                terminal=lifecycle.terminal,
            ),
            golden_case.operation,
            golden_case.command,
            qualified,
        )


def test_live_resolution_rejects_absent_and_drifted_descriptor(
    golden_case: GoldenCase,
    tmp_path: Path,
) -> None:
    with pytest.raises(GoldenRunRegistryError, match="does not admit"):
        golden_case.harness._resolve_live_execution(
            golden_case.harness.read(
                RunReadRequest(
                    schema_version="harness.run_read_request.v1",
                    principal_id=golden_case.command.principal_id,
                    workflow_run_id=golden_case.command.workflow_run_id,
                )
            ).registry_snapshot,
            "text2env.replay@1.0.0",
        )
    drifted = base._qualified_registry(
        golden_case.artifact_store,
        tmp_path,
    )
    with pytest.raises(GoldenRunRegistryError, match="live descriptor differs"):
        golden_case.harness._resolve_live_execution(
            drifted,
            "text2env.compile@1.0.0",
        )


def test_submit_requires_utc_execution_clock(
    golden_case: GoldenCase,
    tmp_path: Path,
) -> None:
    store, _, _, _ = _store_transition_fixture(
        golden_case,
        tmp_path / "non-utc.sqlite3",
    )
    harness = GoldenRunHarness(
        artifact_store=golden_case.artifact_store,
        workflow_store=store,
        execution_catalog=_compile_catalog(application=golden_case.application),
        clock=lambda: base.NOW.astimezone(timezone(timedelta(hours=1))),
    )
    with pytest.raises(ValueError, match="UTC"):
        harness.submit(golden_case.command)


def test_helpers_reject_duplicate_evidence_and_invalid_retraction(
    golden_case: GoldenCase,
) -> None:
    result = _result(golden_case)
    with pytest.raises(GoldenRunCorruptionError, match="repeats"):
        golden_run_module._canonical_artifacts((result.run_state, result.run_state))
    current = golden_case.harness.read(
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=golden_case.command.principal_id,
            workflow_run_id=golden_case.command.workflow_run_id,
        )
    )
    state = _read_json(golden_case, current.state_ref, TrustedWorldState)
    existing = ReceiptDerivedFactClaimV2(
        operation="retract",
        key="assets.catalog",
        value=None,
    )
    mutation = golden_run_module._mutations_from_claims(
        state,
        (existing,),
        receipt_ref=result.trusted_receipt,
        observed_at=result.ended_at,
    )
    assert mutation[0].operation == "retract"
    absent = ReceiptDerivedFactClaimV2(
        operation="retract",
        key="assets.missing",
        value=None,
    )
    with pytest.raises(GoldenRunCorruptionError, match="absent"):
        golden_run_module._mutations_from_claims(
            state,
            (absent,),
            receipt_ref=result.trusted_receipt,
            observed_at=result.ended_at,
        )


def test_frozen_execution_read_rejects_missing_policy_binding_and_snapshot(
    golden_case: GoldenCase,
) -> None:
    missing_skill = golden_case.operation.model_copy(update={"skill_ref": "text2env.replay@1.0.0"})
    with pytest.raises(GoldenRunCorruptionError, match="one frozen"):
        golden_case.harness._resolve_frozen_execution(missing_skill)
    missing_workflow = golden_case.operation.model_copy(
        update={"workflow_run_id": UUID("10000000-0000-4000-8000-000000000099")}
    )
    with pytest.raises(GoldenRunNotFoundError):
        golden_case.harness._read_visible_snapshot(missing_workflow)


def test_frozen_execution_read_rejects_changed_execution_document(
    golden_case: GoldenCase,
) -> None:
    qualified = _read_json(
        golden_case,
        golden_case.operation.qualified_execution,
        QualifiedSkillExecutionV2,
    ).model_copy(update={"evidence_policy_version": "2.0.0"})
    ref = golden_case.harness._publish_model(
        qualified,
        name="changed-qualified.json",
        schema_version="harness.qualified_skill_execution.v2",
    )
    operation = golden_case.operation.model_copy(update={"qualified_execution": ref})
    with pytest.raises(GoldenRunCorruptionError, match="binding is inconsistent"):
        golden_case.harness._resolve_frozen_execution(operation)


def test_resume_rejects_operation_command_cross_binding(golden_case: GoldenCase) -> None:
    changed = golden_case.command.model_copy(
        update={"command_id": UUID("20000000-0000-4000-8000-000000000099")}
    )
    with pytest.raises(GoldenRunConflictError, match="command identity"):
        golden_case.harness._resume_operation(golden_case.operation, changed)


def test_resume_detects_active_head_and_live_execution_change(
    golden_case: GoldenCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, initial, running, _ = _store_transition_fixture(
        golden_case,
        tmp_path / "resume-head.sqlite3",
    )
    running = running.model_copy(
        update={"child_run_id": UUID("30000000-0000-4000-8000-000000000099")}
    )
    _reserve_transition(store, initial, running)
    harness = GoldenRunHarness(
        artifact_store=golden_case.artifact_store,
        workflow_store=store,
        execution_catalog=_compile_catalog(application=golden_case.application),
    )
    stored = store.read_snapshot(
        principal_id=running.principal_id,
        workflow_run_id=running.workflow_run_id,
    )
    assert stored is not None
    monkeypatch.setattr(
        GoldenRunHarness,
        "_read_visible_snapshot",
        lambda self, operation: stored.snapshot.model_copy(update={"active_operation": None}),
    )
    with pytest.raises(GoldenRunHeadConflictError, match="not the active"):
        harness._resume_operation(running, golden_case.command)

    monkeypatch.setattr(
        GoldenRunHarness,
        "_read_visible_snapshot",
        lambda self, operation: stored.snapshot,
    )
    policy, qualified = harness._resolve_frozen_execution(running)
    monkeypatch.setattr(
        GoldenRunHarness,
        "_resolve_live_execution",
        lambda self, registry, skill: (object(), qualified),
    )
    with pytest.raises(GoldenRunRegistryError, match="changed after reservation"):
        harness._resume_operation(running, golden_case.command)
    assert policy is not None


def test_terminal_builder_rejects_changed_head_and_hidden_workflow(
    golden_case: GoldenCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = SQLiteRunStore(golden_case.application.journal_path).read_controlled_lifecycle(
        golden_case.operation.child_run_id
    )
    running = OperationSnapshot.model_validate(
        golden_case.operation.model_copy(
            update={
                "status": RunStatus.RUNNING,
                "committed_revision": None,
                "tool_result": None,
                "ended_at": None,
            }
        ).model_dump(mode="python")
    )
    policy, qualified = golden_case.harness._resolve_frozen_execution(golden_case.operation)
    with pytest.raises(GoldenRunHeadConflictError, match="before terminal"):
        golden_case.harness._commit_terminal_operation(
            running,
            golden_case.command,
            policy,
            qualified,
            lifecycle,
        )

    store, initial, _, _ = _store_transition_fixture(
        golden_case,
        tmp_path / "terminal-none.sqlite3",
    )
    _reserve_transition(store, initial, running)
    harness = GoldenRunHarness(
        artifact_store=golden_case.artifact_store,
        workflow_store=store,
        execution_catalog=_compile_catalog(application=golden_case.application),
    )
    monkeypatch.setattr(SQLiteGoldenRunStore, "commit_operation", lambda *args, **kwargs: None)
    with pytest.raises(GoldenRunNotFoundError):
        harness._commit_terminal_operation(
            running,
            golden_case.command,
            policy,
            qualified,
            lifecycle,
        )


@pytest.mark.parametrize(
    "mode",
    ["journal", "receipt_order", "head_reconstruction", "running_binding"],
)
def test_read_rejects_operation_journal_and_chain_cross_binding(
    golden_case: GoldenCase,
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if mode == "journal":
        stored = golden_case.workflow_store.read_snapshot(
            principal_id=golden_case.command.principal_id,
            workflow_run_id=golden_case.command.workflow_run_id,
        )
        assert stored is not None
        changed = stored.snapshot.model_copy(update={"turn_seq": 2})
        payload = base._canonical_bytes(changed)
        with closing(sqlite3.connect(golden_case.workflow_store.path)) as connection, connection:
            connection.execute(
                "UPDATE golden_workflow_starts SET snapshot_json = ?, snapshot_sha256 = ?",
                (payload, base.sha256(payload).hexdigest()),
            )
    elif mode == "receipt_order":
        original = GoldenRunHarness._resolve_canonical_model

        def wrong_receipt(self, artifact, model):
            value = original(self, artifact, model)
            if model is WorkflowOperationReceiptV2:
                return value.model_copy(
                    update={"operation_id": UUID("20000000-0000-4000-8000-000000000099")}
                )
            return value

        monkeypatch.setattr(GoldenRunHarness, "_resolve_canonical_model", wrong_receipt)
    elif mode == "head_reconstruction":
        stored = golden_case.workflow_store.read_snapshot(
            principal_id=golden_case.command.principal_id,
            workflow_run_id=golden_case.command.workflow_run_id,
        )
        assert stored is not None
        changed = stored.snapshot.model_copy(update={"status": WorkflowStatus.WAITING_INPUT})
        payload = base._canonical_bytes(changed)
        with closing(sqlite3.connect(golden_case.workflow_store.path)) as connection, connection:
            connection.execute(
                "UPDATE golden_workflow_starts SET snapshot_json = ?, snapshot_sha256 = ?",
                (payload, base.sha256(payload).hexdigest()),
            )
    else:
        prior, _, _ = _terminal_parts(golden_case)
        running = OperationSnapshot.model_validate(
            golden_case.operation.model_copy(
                update={
                    "status": RunStatus.RUNNING,
                    "committed_revision": None,
                    "tool_result": None,
                    "ended_at": None,
                    "idempotency_key": "wrong",
                }
            ).model_dump(mode="python")
        )
        with pytest.raises(GoldenRunCorruptionError, match="reserved head"):
            golden_case.harness._verify_running_operation(prior, running)
        return
    with pytest.raises(GoldenRunCorruptionError):
        golden_case.harness.read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id=golden_case.command.principal_id,
                workflow_run_id=golden_case.command.workflow_run_id,
            )
        )


@pytest.mark.parametrize(
    "mode",
    [
        "tool_binding",
        "missing_delta",
        "delta_binding",
        "mutations",
        "resulting_state",
        "workflow_receipt",
    ],
)
def test_terminal_verifier_rejects_each_cross_binding(
    golden_case: GoldenCase,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    prior, receipt_ref, receipt = _terminal_parts(golden_case)
    original = GoldenRunHarness._resolve_canonical_model
    if mode == "tool_binding":

        def changed(self, artifact, model):
            value = original(self, artifact, model)
            return (
                value.model_copy(
                    update={"operation_id": UUID("20000000-0000-4000-8000-000000000099")}
                )
                if model is ToolResultV2
                else value
            )

        monkeypatch.setattr(GoldenRunHarness, "_resolve_canonical_model", changed)
    elif mode == "missing_delta":
        receipt = receipt.model_copy(update={"state_delta": None})
    elif mode == "delta_binding":

        def changed(self, artifact, model):
            value = original(self, artifact, model)
            return (
                value.model_copy(update={"base_state_sha256": "f" * 64})
                if model is StateDeltaV2
                else value
            )

        monkeypatch.setattr(GoldenRunHarness, "_resolve_canonical_model", changed)
    elif mode == "mutations":

        def changed(self, artifact, model):
            value = original(self, artifact, model)
            return _changed_delta(value, {"mutations": ()}) if model is StateDeltaV2 else value

        monkeypatch.setattr(GoldenRunHarness, "_resolve_canonical_model", changed)
    elif mode == "resulting_state":
        state_reads = 0

        def changed(self, artifact, model):
            nonlocal state_reads
            value = original(self, artifact, model)
            if model is TrustedWorldState:
                state_reads += 1
                if state_reads == 2:
                    return value.model_copy(update={"version": value.version + 1})
            return value

        monkeypatch.setattr(GoldenRunHarness, "_resolve_canonical_model", changed)
    else:
        receipt = receipt.model_copy(update={"principal_id": "tests/other"})
    with pytest.raises(GoldenRunCorruptionError):
        golden_case.harness._verify_terminal_evidence(
            prior,
            golden_case.operation,
            receipt_ref=receipt_ref,
            receipt=receipt,
        )


def test_terminal_verifier_rejects_delta_on_non_successful_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _build_case(tmp_path, generate_missing_assets=False)
    prior, receipt_ref, receipt = _terminal_parts(case)
    original = GoldenRunHarness._resolve_canonical_model

    def changed(self, artifact, model):
        value = original(self, artifact, model)
        if model is ToolResultV2:
            return value.model_copy(update={"state_delta": value.run_state})
        return value

    monkeypatch.setattr(GoldenRunHarness, "_resolve_canonical_model", changed)
    with pytest.raises(GoldenRunCorruptionError, match="cannot advance state"):
        case.harness._verify_terminal_evidence(
            prior,
            case.operation,
            receipt_ref=receipt_ref,
            receipt=receipt,
        )


def test_operation_snapshot_rejects_contradictory_lifecycle(
    golden_case: GoldenCase,
) -> None:
    operation = golden_case.operation
    invalid = [
        {"status": RunStatus.RUNNING},
        {"tool_result": None},
        {"committed_revision": 9},
        {"ended_at": operation.started_at - timedelta(seconds=1)},
        {"tool_result": operation.command},
    ]
    for change in invalid:
        with pytest.raises(ValidationError):
            OperationSnapshot.model_validate(
                operation.model_copy(update=change).model_dump(mode="python")
            )


def _invalid_ref(ref: ArtifactRef, *, field: str, value: object) -> ArtifactRef:
    return ArtifactRef.model_construct(**{**ref.model_dump(mode="python"), field: value})


def _validate_changed(model, changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        type(model).model_validate(model.model_copy(update=changes).model_dump(mode="python"))


def _changed_delta(delta: StateDeltaV2, changes: dict[str, object]) -> StateDeltaV2:
    unhashed = delta.model_copy(
        update={
            **changes,
            "delta_sha256": "0" * 64,
        }
    )
    return StateDeltaV2.model_validate(
        unhashed.model_copy(update={"delta_sha256": state_delta_v2_sha256(unhashed)}).model_dump(
            mode="python"
        )
    )


def test_qualified_execution_rejects_non_cas_and_wrong_typed_authority_refs(
    golden_case: GoldenCase,
) -> None:
    qualified = _read_json(
        golden_case,
        golden_case.operation.qualified_execution,
        QualifiedSkillExecutionV2,
    )
    cases = (
        {
            "descriptor": _invalid_ref(
                qualified.descriptor,
                field="uri",
                value=f"artifact://sha256/{'f' * 64}",
            )
        },
        {
            "descriptor": _invalid_ref(
                qualified.descriptor,
                field="schema_version",
                value="harness.unknown_descriptor.v1",
            )
        },
        {
            "qualification_artifact": _invalid_ref(
                qualified.qualification_artifact,
                field="schema_version",
                value="harness.skill_qualification.v2",
            )
        },
        {
            "qualification_report": _invalid_ref(
                qualified.qualification_report,
                field="media_type",
                value="text/plain",
            )
        },
    )
    for change in cases:
        _validate_changed(qualified, change)
    _validate_changed(
        ReceiptDerivedFactClaimV2(operation="retract", key="assets.catalog", value=None),
        {"value": {}},
    )


def test_trusted_receipt_rejects_contradictions_and_noncanonical_evidence(
    golden_case: GoldenCase,
) -> None:
    result = _result(golden_case)
    receipt = _read_json(golden_case, result.trusted_receipt, TrustedToolReceiptV2)
    blocker = Blocker(
        code="HARN_TEST",
        message="test blocker",
        stage="compile",
        retryable=False,
        details={},
        unknowns=(),
        artifact_refs=(),
    )
    refs = receipt.supporting_artifacts
    cases = [
        {"ended_at": receipt.started_at - timedelta(seconds=1)},
        {"command": _invalid_ref(receipt.command, field="schema_version", value="wrong")},
        {"base_state": _invalid_ref(receipt.base_state, field="schema_version", value="wrong")},
        {
            "registry_snapshot": _invalid_ref(
                receipt.registry_snapshot,
                field="schema_version",
                value="wrong",
            )
        },
        {"run_state": _invalid_ref(receipt.run_state, field="schema_version", value="wrong")},
        {
            "invocation": _invalid_ref(
                receipt.invocation,
                field="schema_version",
                value="wrong",
            )
        },
        {"supporting_artifacts": tuple(reversed(refs))},
        {"supporting_artifacts": (refs[0], refs[0])},
        {
            "derived_fact_claims": tuple(reversed(receipt.derived_fact_claims)),
        },
        {
            "derived_fact_claims": (
                receipt.derived_fact_claims[0],
                receipt.derived_fact_claims[0],
            )
        },
        {"status": RunStatus.RUNNING},
        {"invocation": None},
        {"invocation_digest": None},
        {"typed_output": None},
        {"blocker": blocker},
        {
            "status": RunStatus.BLOCKED,
            "typed_output": None,
            "blocker": blocker,
        },
        {
            "status": RunStatus.BLOCKED,
            "typed_output": None,
            "blocker": blocker,
            "derived_fact_claims": (),
            "invocation_digest": None,
        },
    ]
    for change in cases:
        _validate_changed(receipt, change)


def test_state_delta_rejects_wrong_receipt_order_and_digest(golden_case: GoldenCase) -> None:
    result = _result(golden_case)
    assert result.state_delta is not None
    delta = _read_json(golden_case, result.state_delta, StateDeltaV2)
    cases = (
        {"receipt": _invalid_ref(delta.receipt, field="schema_version", value="wrong")},
        {"mutations": tuple(reversed(delta.mutations))},
        {"mutations": (delta.mutations[0], delta.mutations[0])},
        {"delta_sha256": "f" * 64},
    )
    for change in cases:
        _validate_changed(delta, change)


def test_tool_result_rejects_terminal_and_evidence_contradictions(
    golden_case: GoldenCase,
) -> None:
    result = _result(golden_case)
    assert result.state_delta is not None and result.invocation is not None
    blocker = Blocker(
        code="HARN_TEST",
        message="test blocker",
        stage="compile",
        retryable=False,
        details={},
        unknowns=(),
        artifact_refs=(),
    )
    diagnostics = _read_json(
        golden_case,
        result.trusted_receipt,
        TrustedToolReceiptV2,
    ).supporting_artifacts[:2]
    observed = WorldFact(
        key="observation.test",
        value=True,
        source_kind="fresh_observation",
        source_artifact=result.trusted_receipt,
        observed_at=result.started_at,
        valid_until=None,
    )
    wrong_source = observed.model_copy(update={"source_kind": "user_input"})
    too_early = observed.model_copy(
        update={"observed_at": result.started_at - timedelta(seconds=1)}
    )
    expired = observed.model_copy(update={"valid_until": result.started_at})
    valid_until_after_end = observed.model_copy(
        update={"valid_until": result.ended_at + timedelta(seconds=1)}
    )
    cases = [
        {"status": RunStatus.RUNNING},
        {"ended_at": result.started_at - timedelta(seconds=1)},
        {"run_state": _invalid_ref(result.run_state, field="schema_version", value="wrong")},
        {
            "trusted_receipt": _invalid_ref(
                result.trusted_receipt,
                field="schema_version",
                value="wrong",
            )
        },
        {"invocation": _invalid_ref(result.invocation, field="schema_version", value="wrong")},
        {"invocation": None},
        {"typed_output": None},
        {"state_delta": None},
        {"blocker": blocker},
        {"state_delta": _invalid_ref(result.state_delta, field="schema_version", value="wrong")},
        {"status": RunStatus.BLOCKED, "typed_output": None, "blocker": blocker},
        {"diagnostics": tuple(reversed(diagnostics))},
        {"diagnostics": (diagnostics[0], diagnostics[0])},
        {"fresh_observations": (observed, observed)},
        {"fresh_observations": (wrong_source,)},
        {"fresh_observations": (too_early,)},
        {"fresh_observations": (expired,)},
    ]
    for change in cases:
        _validate_changed(result, change)
    assert ToolResultV2.model_validate(
        result.model_copy(update={"fresh_observations": (valid_until_after_end,)}).model_dump(
            mode="python"
        )
    ).fresh_observations == (valid_until_after_end,)


def test_operation_evidence_requires_utc_not_merely_an_aware_clock(
    golden_case: GoldenCase,
) -> None:
    result = _result(golden_case)
    non_utc = result.started_at.astimezone(timezone(timedelta(hours=1)))
    _validate_changed(result, {"started_at": non_utc})


def test_workflow_receipt_rejects_nonterminal_and_cross_typed_refs(
    golden_case: GoldenCase,
) -> None:
    current = golden_case.harness.read(
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=golden_case.command.principal_id,
            workflow_run_id=golden_case.command.workflow_run_id,
        )
    )
    receipt = _read_json(
        golden_case,
        current.receipt_head,
        WorkflowOperationReceiptV2,
    )
    assert receipt.state_delta is not None
    cases = (
        {"committed_revision": 9},
        {"status": RunStatus.RUNNING},
        {"command": _invalid_ref(receipt.command, field="schema_version", value="wrong")},
        {
            "predecessor_receipt": _invalid_ref(
                receipt.predecessor_receipt,
                field="schema_version",
                value="wrong",
            )
        },
        {"tool_receipt": _invalid_ref(receipt.tool_receipt, field="schema_version", value="wrong")},
        {"tool_result": _invalid_ref(receipt.tool_result, field="schema_version", value="wrong")},
        {
            "resulting_state": _invalid_ref(
                receipt.resulting_state,
                field="schema_version",
                value="wrong",
            )
        },
        {"state_delta": None},
        {"status": RunStatus.BLOCKED},
    )
    for change in cases:
        _validate_changed(receipt, change)
