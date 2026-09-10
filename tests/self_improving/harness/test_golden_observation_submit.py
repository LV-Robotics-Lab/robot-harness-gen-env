"""RED tracer for receipt-bound fresh observations in one Golden workflow."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import BaseModel

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.event_journal import SQLiteEventJournal
from self_improving.harness.golden_execution import (
    QualifiedApplicationRunPendingError,
    _create_golden_execution_binding,
    create_golden_execution_catalog,
)
from self_improving.harness.golden_run import GoldenRunHarness, GoldenRunNotFoundError
from self_improving.harness.golden_store import GoldenRunCorruptionError, SQLiteGoldenRunStore
from self_improving.harness.qualification import QualificationCheckV1, QualificationReportV1
from self_improving.harness.registry import (
    ControlledRunPendingError,
    HandlerResult,
    SkillBlocked,
    SkillRegistry,
    StaticDependencyResolver,
)
from self_improving.harness.run_store import SQLiteRunStore
from self_improving.harness.schemas.common import (
    ArtifactRef,
    Blocker,
    Invocation,
    RunState,
    RunStatus,
    SkillDescriptor,
    SkillQualification,
)
from self_improving.harness.schemas.registry_snapshot import (
    QualifiedSkillSnapshot,
    RegistrySnapshot,
)
from self_improving.harness.schemas.workflow import (
    RunCommandRequest,
    RunReadRequest,
    RunStartRequest,
)
from self_improving.harness.schemas.workflow_operation import (
    ReceiptDerivedFactClaimV2,
    StateDeltaV2,
    ToolResultV2,
    TrustedToolReceiptV2,
    WorkflowOperationReceiptV2,
)
from self_improving.harness.trusted_state import TrustedWorldState, WorldFact, WorldFactEvidence

SKILL_REF = "genesis.observe@1.0.0"
WORKFLOW_ID = UUID("61000000-0000-4000-8000-000000000001")
CHILD_IDS = tuple(UUID(f"62000000-0000-4000-8000-{index:012d}") for index in range(1, 4))
START = datetime(2026, 9, 11, 1, tzinfo=timezone.utc)


class _Clock:
    def __init__(self) -> None:
        self.value = START

    def __call__(self) -> datetime:
        result = self.value
        self.value += timedelta(milliseconds=1)
        return result


def _canonical(value: object) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def _put(
    store: LocalArtifactStore,
    root: Path,
    *,
    name: str,
    value: object,
    schema_version: str,
) -> ArtifactRef:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(value))
    return store.put_file(
        path,
        name=name,
        media_type="application/json",
        schema_version=schema_version,
    )


def _put_payload(
    store: LocalArtifactStore,
    root: Path,
    *,
    name: str,
    payload: bytes,
    schema_version: str,
) -> ArtifactRef:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return store.put_file(
        path,
        name=name,
        media_type="application/json",
        schema_version=schema_version,
    )


@dataclass(frozen=True)
class _ObservationPolicy:
    store: LocalArtifactStore
    observation: ArtifactRef
    projection_attack: str | None = None
    skill_ref: str = SKILL_REF
    input_model: type[BaseModel] = ArtifactRef
    evidence_policy_id: str = "genesis.observe.evidence"
    evidence_policy_version: str = "1.0.0"

    @property
    def evidence_policy_implementation_sha256(self) -> str:
        return "9" * 64

    def normalize_parameters(self, parameters):
        return ArtifactRef.model_validate(parameters)

    def authorize(self, state: TrustedWorldState, parameters: BaseModel) -> None:
        assert type(parameters) is ArtifactRef
        assert state.facts

    def claims(self, state: RunState) -> tuple[ReceiptDerivedFactClaimV2, ...]:
        if state.status is not RunStatus.SUCCEEDED:
            return ()
        if state.run_id == CHILD_IDS[0]:
            return (ReceiptDerivedFactClaimV2(key="setup.revision_one", value=True),)
        if state.run_id == CHILD_IDS[1]:
            return (ReceiptDerivedFactClaimV2(key="setup.revision_two", value=True),)
        if self.projection_attack == "missing_claim":
            return ()
        if self.projection_attack == "retract_claim":
            return (
                ReceiptDerivedFactClaimV2(
                    operation="retract",
                    key="observation.scene_visible",
                    value=None,
                ),
            )
        if self.projection_attack == "wrong_value":
            return (ReceiptDerivedFactClaimV2(key="observation.scene_visible", value=False),)
        return (ReceiptDerivedFactClaimV2(key="observation.scene_visible", value=True),)

    def diagnostics(self, state: RunState) -> tuple[ArtifactRef, ...]:
        return ()

    def fresh_observations(
        self,
        state: RunState,
        invocation: Invocation | None,
    ) -> tuple[WorldFact, ...]:
        if state.run_id != CHILD_IDS[2] or (
            state.status is not RunStatus.SUCCEEDED
            and self.projection_attack != "non_success_observation"
        ):
            return ()
        assert state.ended_at is not None
        payload = self.store.resolve(self.observation).path.read_bytes()
        evidence = WorldFactEvidence.model_validate_json(payload)
        if payload != _canonical(evidence):
            raise ValueError("fresh observation receipt is not canonical JSON")
        if evidence.source_kind != "fresh_observation":
            raise ValueError("fresh observation receipt has the wrong source kind")
        if evidence.valid_until is None or evidence.valid_until <= evidence.observed_at:
            raise ValueError("fresh observation receipt must have positive TTL")
        source_artifact = self.observation
        if self.projection_attack == "unsupported_source":
            source_artifact = self.observation.model_copy(
                update={"schema_version": "harness.unbound_observation.v1"}
            )
        return (
            WorldFact(
                key=evidence.key,
                value=evidence.value,
                source_kind="fresh_observation",
                source_artifact=source_artifact,
                observed_at=evidence.observed_at,
                valid_until=(
                    None
                    if self.projection_attack == "missing_valid_until"
                    else evidence.valid_until
                ),
            ),
        )

    def verify_result(
        self,
        *,
        state: RunState,
        claims: tuple[ReceiptDerivedFactClaimV2, ...],
        result: ToolResultV2,
        supporting: tuple[ArtifactRef, ...],
    ) -> None:
        assert claims == self.claims(state)
        if state.status is RunStatus.SUCCEEDED:
            assert self.observation in supporting
        if result.fresh_observations != self.fresh_observations(state, None):
            raise ValueError("observe ToolResult omitted its policy-projected fresh observations")


@dataclass(frozen=True)
class _ObservationApplication:
    artifact_root: Path
    authority_path: Path
    registry: SkillRegistry
    descriptor: SkillDescriptor
    policy: _ObservationPolicy
    skill_ref: str = SKILL_REF

    @property
    def skills(self):
        return (self.descriptor,)

    def invoke(self, parameters: BaseModel, *, run_id: UUID) -> RunState:
        try:
            return self.registry.invoke_with_run_id(
                "genesis.observe",
                "1.0.0",
                parameters.model_dump(mode="json"),
                run_id=run_id,
            )
        except ControlledRunPendingError as error:
            raise QualifiedApplicationRunPendingError(str(error)) from error

    def invocation(self, run_id: UUID) -> Invocation | None:
        return self.registry.invocation(run_id)

    def run_state(self, run_id: UUID) -> RunState | None:
        return SQLiteRunStore(self.authority_path).read_run_state(run_id)


@dataclass(frozen=True)
class _Case:
    store: LocalArtifactStore
    harness: GoldenRunHarness
    workflow_store: SQLiteGoldenRunStore
    application: _ObservationApplication
    revision_two: object
    observation: ArtifactRef
    command: RunCommandRequest


def _case(
    tmp_path: Path,
    *,
    terminal_status: RunStatus = RunStatus.SUCCEEDED,
    observation_attack: str | None = None,
    projection_attack: str | None = None,
) -> _Case:
    store = LocalArtifactStore(tmp_path / "cas")
    report = QualificationReportV1(
        schema_version="harness.skill_qualification_report.v1",
        skill_ref=SKILL_REF,
        status="pass",
        deterministic_case_id="controlled-observe",
        regression_command=(
            "pytest -q tests/self_improving/harness/test_golden_observation_submit.py"
        ),
        implementation_sha256="8" * 64,
        scene_gen_tree_sha256="7" * 64,
        ledger_contract_tree_sha256="6" * 64,
        checks=(QualificationCheckV1(name="controlled", status="pass", evidence={}),),
    )
    report_ref = _put(
        store,
        tmp_path,
        name="observe-report.json",
        value=report,
        schema_version="harness.skill_qualification_report.v1",
    )
    qualification = SkillQualification(
        skill_ref=SKILL_REF,
        status="pass",
        deterministic_case_id="controlled-observe",
        regression_command=report.regression_command,
        report_sha256=report_ref.sha256,
    )
    qualification_ref = _put(
        store,
        tmp_path,
        name="observe-qualification.json",
        value=qualification,
        schema_version="harness.skill_qualification.v1",
    )
    descriptor = SkillDescriptor(
        skill_id="genesis.observe",
        version="1.0.0",
        mcp_tool_name="genesis_observe_v1_0_0",
        input_schema="harness.artifact_ref.v1",
        output_schema="harness.artifact_ref.v1",
        implementation_name="tests.controlled_observe",
        implementation_version="1",
        implementation_sha256=report.implementation_sha256,
        deterministic=True,
        max_attempts=1,
        qualification_artifact=qualification_ref,
    )
    descriptor_ref = _put(
        store,
        tmp_path,
        name="observe-descriptor.json",
        value=descriptor,
        schema_version="harness.skill_descriptor.v1",
    )
    registry_ref = _put(
        store,
        tmp_path,
        name="registry.json",
        value=RegistrySnapshot(
            schema_version="harness.registry_snapshot.v1",
            qualified_skills=(
                QualifiedSkillSnapshot(
                    skill_ref=SKILL_REF,
                    mcp_tool_name=descriptor.mcp_tool_name,
                    descriptor_ref=descriptor_ref,
                    qualification_ref=qualification_ref,
                    qualification_report_ref=report_ref,
                ),
            ),
        ),
        schema_version="harness.registry_snapshot.v1",
    )
    observed_at = START + timedelta(milliseconds=12)
    observation_evidence = WorldFactEvidence(
        source_kind="fresh_observation",
        key="observation.scene_visible",
        value=True,
        observed_at=observed_at,
        valid_until=(
            observed_at if observation_attack == "expired" else observed_at + timedelta(seconds=60)
        ),
    )
    observation_payload = _canonical(observation_evidence)
    if observation_attack == "noncanonical":
        observation_payload = observation_payload.replace(b"{", b"{ ", 1)
    observation = _put_payload(
        store,
        tmp_path,
        name="fresh-observation.json",
        payload=observation_payload,
        schema_version="harness.fresh_observation.v1",
    )
    run_store = SQLiteRunStore(tmp_path / "observe.sqlite3")
    clock = _Clock()
    registry = SkillRegistry(
        artifact_resolver=store,
        dependency_resolver=StaticDependencyResolver({SKILL_REF: ()}),
        event_sink=SQLiteEventJournal(run_store.path),
        clock=clock,
        run_id_factory=lambda: CHILD_IDS[0],
        run_store=run_store,
    )

    def observe(value: ArtifactRef, context) -> HandlerResult:
        if context.run_id == CHILD_IDS[2] and terminal_status is RunStatus.BLOCKED:
            raise SkillBlocked(
                Blocker(
                    code="HARN_OBSERVATION_UNAVAILABLE",
                    message="controlled observation unavailable",
                    stage="observe",
                    retryable=False,
                    details={},
                    unknowns=(),
                    artifact_refs=(),
                )
            )
        if context.run_id == CHILD_IDS[2] and terminal_status is RunStatus.FAILED:
            raise RuntimeError("controlled observation failure")
        context.emit("observe.capture", artifact_refs=(value,))
        return HandlerResult(output=value, artifacts=(value,))

    registry.register(descriptor, observe)
    policy = _ObservationPolicy(
        store=store,
        observation=observation,
        projection_attack=projection_attack,
    )
    application = _ObservationApplication(
        artifact_root=store.root,
        authority_path=run_store.path,
        registry=registry,
        descriptor=descriptor,
        policy=policy,
    )
    binding = _create_golden_execution_binding(
        policy=policy,
        application=application,
        child_authority=run_store,
    )
    workflow_store = SQLiteGoldenRunStore(tmp_path / "golden.sqlite3")
    children = iter(CHILD_IDS)
    harness = GoldenRunHarness(
        artifact_store=store,
        workflow_store=workflow_store,
        execution_catalog=create_golden_execution_catalog(bindings=(binding,)),
        clock=clock,
        workflow_id_factory=lambda: WORKFLOW_ID,
        child_run_id_factory=lambda: next(children),
    )
    objective = WorldFactEvidence(
        source_kind="user_input",
        key="request.objective",
        value="Observe the simulated scene.",
        observed_at=START,
        valid_until=None,
    )
    objective_ref = _put(
        store,
        tmp_path,
        name="objective.json",
        value=objective,
        schema_version="harness.world_fact_evidence.v1",
    )
    snapshot = harness.start(
        RunStartRequest(
            schema_version="harness.run_start_request.v1",
            principal_id="tests/golden-observation",
            idempotency_key="observe-start",
            workspace="ephemeral",
            requested_profile="genesis.robot_policy@1",
            initial_fact_evidence=(objective_ref,),
            registry_snapshot=registry_ref,
        )
    )
    for index in range(2):
        harness.submit(
            RunCommandRequest(
                schema_version="harness.run_command_request.v1",
                command_id=UUID(f"63000000-0000-4000-8000-{index + 1:012d}"),
                principal_id=snapshot.principal_id,
                workflow_run_id=snapshot.workflow_run_id,
                idempotency_key=f"observe-{index + 1}",
                expected_workflow_revision=snapshot.revision,
                expected_state_sha256=snapshot.state_sha256,
                expected_receipt_head_sha256=snapshot.receipt_head.sha256,
                skill_ref=SKILL_REF,
                parameters=observation.model_dump(mode="json"),
            )
        )
        snapshot = harness.read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id=snapshot.principal_id,
                workflow_run_id=snapshot.workflow_run_id,
            )
        )
    command = RunCommandRequest(
        schema_version="harness.run_command_request.v1",
        command_id=UUID("63000000-0000-4000-8000-000000000003"),
        principal_id=snapshot.principal_id,
        workflow_run_id=snapshot.workflow_run_id,
        idempotency_key="observe-3",
        expected_workflow_revision=2,
        expected_state_sha256=snapshot.state_sha256,
        expected_receipt_head_sha256=snapshot.receipt_head.sha256,
        skill_ref=SKILL_REF,
        parameters=observation.model_dump(mode="json"),
    )
    return _Case(
        store=store,
        harness=harness,
        workflow_store=workflow_store,
        application=application,
        revision_two=snapshot,
        observation=observation,
        command=command,
    )


def test_observe_revision_two_to_three_projects_receipt_bound_fresh_state(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)

    operation = case.harness.submit(case.command)

    assert operation.status is RunStatus.SUCCEEDED
    assert operation.committed_revision == 3
    assert operation.tool_result is not None
    result = ToolResultV2.model_validate_json(
        case.store.resolve(operation.tool_result).path.read_bytes()
    )
    assert len(result.fresh_observations) == 1
    observed = result.fresh_observations[0]
    assert observed.source_artifact == case.observation
    assert observed.valid_until == observed.observed_at + timedelta(seconds=60)
    receipt = TrustedToolReceiptV2.model_validate_json(
        case.store.resolve(result.trusted_receipt).path.read_bytes()
    )
    claim = receipt.derived_fact_claims[0]
    assert (claim.key, claim.value) == (observed.key, observed.value)
    assert observed.source_artifact in receipt.supporting_artifacts
    assert result.state_delta is not None
    delta = StateDeltaV2.model_validate_json(
        case.store.resolve(result.state_delta).path.read_bytes()
    )
    mutation = delta.mutations[0]
    assert mutation.fact is not None
    assert mutation.fact.source_kind == "trusted_receipt"
    assert mutation.fact.source_artifact == result.trusted_receipt
    assert (mutation.key, mutation.fact.value) == (observed.key, observed.value)
    assert mutation.fact.observed_at == observed.observed_at
    assert mutation.fact.valid_until == observed.valid_until
    current = case.harness.read(
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=case.command.principal_id,
            workflow_run_id=case.command.workflow_run_id,
        )
    )
    assert current.revision == 3
    state = TrustedWorldState.model_validate_json(
        case.store.resolve(current.state_ref).path.read_bytes()
    )
    assert next(fact for fact in state.facts if fact.key == observed.key) == mutation.fact
    workflow_receipt = WorkflowOperationReceiptV2.model_validate_json(
        case.store.resolve(current.receipt_head).path.read_bytes()
    )
    assert workflow_receipt.predecessor_receipt == case.revision_two.receipt_head
    reachable, payload = case.harness.read_workflow_artifact(
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=case.command.principal_id,
            workflow_run_id=case.command.workflow_run_id,
        ),
        sha256=observed.source_artifact.sha256,
    )
    assert reachable == observed.source_artifact
    assert hashlib.sha256(payload).hexdigest() == reachable.sha256

    cas_count = len(tuple((case.store.root / "sha256").glob("*/*")))
    retry = GoldenRunHarness(
        artifact_store=case.store,
        workflow_store=case.workflow_store,
        execution_catalog=create_golden_execution_catalog(
            bindings=(
                _create_golden_execution_binding(
                    policy=case.application.policy,
                    application=None,
                    child_authority=SQLiteRunStore(case.application.authority_path),
                ),
            )
        ),
        clock=lambda: pytest.fail("terminal retry must not read the clock"),
        child_run_id_factory=lambda: pytest.fail("terminal retry must not reserve a child"),
    ).submit(case.command)
    assert retry == operation
    assert len(tuple((case.store.root / "sha256").glob("*/*"))) == cas_count

    with pytest.raises(GoldenRunNotFoundError):
        case.harness.read_workflow_artifact(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id="tests/foreign-principal",
                workflow_run_id=case.command.workflow_run_id,
            ),
            sha256=observed.source_artifact.sha256,
        )
    unbound = _put(
        case.store,
        tmp_path,
        name="unbound-observation.json",
        value={"captured": "elsewhere"},
        schema_version="harness.fresh_observation.v1",
    )
    with pytest.raises(GoldenRunNotFoundError):
        case.harness.read_workflow_artifact(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id=case.command.principal_id,
                workflow_run_id=case.command.workflow_run_id,
            ),
            sha256=unbound.sha256,
        )


@pytest.mark.parametrize("terminal_status", [RunStatus.BLOCKED, RunStatus.FAILED])
def test_observe_terminal_failure_records_history_without_changing_trusted_state(
    tmp_path: Path,
    terminal_status: RunStatus,
) -> None:
    case = _case(tmp_path, terminal_status=terminal_status)

    operation = case.harness.submit(case.command)

    assert operation.status is terminal_status
    assert operation.committed_revision == 3
    assert operation.tool_result is not None
    result = ToolResultV2.model_validate_json(
        case.store.resolve(operation.tool_result).path.read_bytes()
    )
    assert result.state_delta is None
    assert result.fresh_observations == ()
    current = case.harness.read(
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=case.command.principal_id,
            workflow_run_id=case.command.workflow_run_id,
        )
    )
    assert current.revision == 3
    assert current.state_ref == case.revision_two.state_ref
    assert current.state_sha256 == case.revision_two.state_sha256
    receipt = WorkflowOperationReceiptV2.model_validate_json(
        case.store.resolve(current.receipt_head).path.read_bytes()
    )
    assert receipt.predecessor_receipt == case.revision_two.receipt_head


@pytest.mark.parametrize(
    ("observation_attack", "message"),
    [
        ("noncanonical", "not canonical JSON"),
        ("expired", "positive TTL"),
    ],
)
def test_observe_rejects_invalid_inner_receipt_without_advancing_trusted_state(
    tmp_path: Path,
    observation_attack: str,
    message: str,
) -> None:
    case = _case(tmp_path, observation_attack=observation_attack)

    with pytest.raises(ValueError, match=message):
        case.harness.submit(case.command)

    stored = case.workflow_store.read_snapshot(
        principal_id=case.command.principal_id,
        workflow_run_id=case.command.workflow_run_id,
    )
    assert stored is not None
    assert stored.snapshot.revision == 2
    assert stored.snapshot.state_ref == case.revision_two.state_ref
    assert stored.snapshot.state_sha256 == case.revision_two.state_sha256
    assert stored.snapshot.receipt_head == case.revision_two.receipt_head


@pytest.mark.parametrize(
    ("projection_attack", "terminal_status", "message"),
    [
        (
            "non_success_observation",
            RunStatus.FAILED,
            "non-successful child cannot project fresh observations",
        ),
        ("missing_claim", RunStatus.SUCCEEDED, "does not equal a receipt claim"),
        ("retract_claim", RunStatus.SUCCEEDED, "does not equal a receipt claim"),
        ("wrong_value", RunStatus.SUCCEEDED, "does not equal a receipt claim"),
        ("missing_valid_until", RunStatus.SUCCEEDED, "does not equal a receipt claim"),
        ("unsupported_source", RunStatus.SUCCEEDED, "does not equal a receipt claim"),
    ],
)
def test_observe_rejects_invalid_projection_without_advancing_trusted_state(
    tmp_path: Path,
    projection_attack: str,
    terminal_status: RunStatus,
    message: str,
) -> None:
    case = _case(
        tmp_path,
        terminal_status=terminal_status,
        projection_attack=projection_attack,
    )

    with pytest.raises(GoldenRunCorruptionError, match=message):
        case.harness.submit(case.command)

    stored = case.workflow_store.read_snapshot(
        principal_id=case.command.principal_id,
        workflow_run_id=case.command.workflow_run_id,
    )
    assert stored is not None
    assert stored.snapshot.revision == 2
    assert stored.snapshot.state_ref == case.revision_two.state_ref
    assert stored.snapshot.state_sha256 == case.revision_two.state_sha256
    assert stored.snapshot.receipt_head == case.revision_two.receipt_head


def test_observe_reopen_rejects_tampered_observation_receipt_in_cas_closure(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    operation = case.harness.submit(case.command)
    observation_path = case.store.resolve(case.observation).path
    observation_path.write_bytes(b"{}\n")

    reopened = GoldenRunHarness(
        artifact_store=case.store,
        workflow_store=case.workflow_store,
        execution_catalog=create_golden_execution_catalog(
            bindings=(
                _create_golden_execution_binding(
                    policy=case.application.policy,
                    application=None,
                    child_authority=SQLiteRunStore(case.application.authority_path),
                ),
            )
        ),
    )
    with pytest.raises(GoldenRunCorruptionError, match="CAS closure"):
        reopened.read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id=case.command.principal_id,
                workflow_run_id=case.command.workflow_run_id,
            )
        )
    stored = case.workflow_store.read_operation(
        principal_id=case.command.principal_id,
        workflow_run_id=case.command.workflow_run_id,
        skill_ref=case.command.skill_ref,
        idempotency_key=case.command.idempotency_key,
    )
    assert stored is not None
    assert stored.operation == operation
