"""RED tracer for receipt-bound fresh observations in one Golden workflow."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import BaseModel

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.event_journal import SQLiteEventJournal
from self_improving.harness.genesis_observe import (
    ControlledImageCapture,
    ControlledObservationCapture,
    ControlledObservationCaptureAdapter,
    ControlledObservationValue,
    ControlledObserveApplicationSettings,
    create_controlled_genesis_observe_application,
)
from self_improving.harness.golden_execution import (
    QualifiedApplicationRunPendingError,
    SkillAuthorizationContext,
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
    DependencyRef,
    Invocation,
    RunState,
    RunStatus,
    SkillDescriptor,
    SkillQualification,
)
from self_improving.harness.schemas.genesis_observe import (
    GenesisObservationCapturePlan,
    GenesisObservationReceipt,
    GenesisObserveInput,
    GenesisObserveOutput,
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
from self_improving.harness.schemas.x2env import (
    Image2EnvReplayInput,
    Image2EnvReplayOutput,
    X2EnvEnvironmentPackage,
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

    def authorize_workflow(
        self,
        state: TrustedWorldState,
        parameters: BaseModel,
        context: SkillAuthorizationContext,
    ) -> None:
        assert type(parameters) is ArtifactRef
        persisted = TrustedWorldState.model_validate_json(
            self.store.resolve(context.state_ref).path.read_bytes()
        )
        assert persisted == state

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


_INTEGRATION_CHILD_IDS = tuple(
    UUID(f"64000000-0000-4000-8000-{index:012d}") for index in range(1, 4)
)
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@dataclass(frozen=True)
class _FixturePolicy:
    skill_ref: str
    input_model: type[BaseModel]

    evidence_policy_id: str = "tests.fixture.evidence"
    evidence_policy_version: str = "1.0.0"

    @property
    def evidence_policy_implementation_sha256(self) -> str:
        return hashlib.sha256(self.skill_ref.encode()).hexdigest()

    def normalize_parameters(self, parameters):
        return self.input_model.model_validate(parameters)

    def authorize(self, state: TrustedWorldState, parameters: BaseModel) -> None:
        assert type(parameters) is self.input_model

    def authorize_workflow(
        self,
        state: TrustedWorldState,
        parameters: BaseModel,
        context: SkillAuthorizationContext,
    ) -> None:
        assert type(parameters) is self.input_model
        assert type(context) is SkillAuthorizationContext

    def claims(self, state: RunState) -> tuple[ReceiptDerivedFactClaimV2, ...]:
        return ()

    def diagnostics(self, state: RunState) -> tuple[ArtifactRef, ...]:
        return ()

    def fresh_observations(
        self,
        state: RunState,
        invocation: Invocation | None,
    ) -> tuple[WorldFact, ...]:
        return ()

    def verify_result(
        self,
        *,
        state: RunState,
        claims: tuple[ReceiptDerivedFactClaimV2, ...],
        result: ToolResultV2,
        supporting: tuple[ArtifactRef, ...],
    ) -> None:
        assert claims == ()
        assert result.typed_output == state.output
        assert result.fresh_observations == ()


@dataclass(frozen=True)
class _FixtureApplication:
    artifact_root: Path
    authority_path: Path
    registry: SkillRegistry
    descriptor: SkillDescriptor
    policy: _FixturePolicy
    skill_ref: str

    @property
    def skills(self):
        return (self.descriptor,)

    def invoke(self, parameters: BaseModel, *, run_id: UUID) -> RunState:
        skill_id, version = self.skill_ref.split("@", 1)
        return self.registry.invoke_with_run_id(
            skill_id,
            version,
            parameters.model_dump(mode="json"),
            run_id=run_id,
        )

    def invocation(self, run_id: UUID) -> Invocation | None:
        return self.registry.invocation(run_id)

    def run_state(self, run_id: UUID) -> RunState | None:
        return SQLiteRunStore(self.authority_path).read_run_state(run_id)


class _ControlledCapture(ControlledObservationCaptureAdapter):
    identity = DependencyRef(
        name="genesis.observe.controlled_capture",
        version="1",
        sha256="5" * 64,
    )

    def __init__(self) -> None:
        self.calls = 0

    def capture(
        self,
        request: GenesisObserveInput,
        *,
        captured_at: datetime,
    ) -> ControlledObservationCapture:
        self.calls += 1
        return ControlledObservationCapture(
            captured_at=captured_at,
            images=(ControlledImageCapture(camera_id="observer", payload=_PNG),),
            observations=(ControlledObservationValue(key="observation.scene_visible", value=True),),
        )


def _put_binary(
    store: LocalArtifactStore,
    root: Path,
    *,
    name: str,
    payload: bytes,
    media_type: str,
    schema_version: str | None,
) -> ArtifactRef:
    source = root / f"source-{name}"
    source.write_bytes(payload)
    return store.put_file(
        source,
        name=name,
        media_type=media_type,
        schema_version=schema_version,
    )


def _qualified_fixture(
    store: LocalArtifactStore,
    root: Path,
    *,
    skill_id: str,
    input_schema: str,
    output_schema: str,
    implementation_sha256: str,
) -> tuple[SkillDescriptor, QualifiedSkillSnapshot]:
    skill_ref = f"{skill_id}@1.0.0"
    report = QualificationReportV1(
        schema_version="harness.skill_qualification_report.v1",
        skill_ref=skill_ref,
        status="pass",
        deterministic_case_id=f"controlled-{skill_id}",
        regression_command=(
            "pytest -q tests/self_improving/harness/test_golden_observation_submit.py"
        ),
        implementation_sha256=implementation_sha256,
        scene_gen_tree_sha256="6" * 64,
        ledger_contract_tree_sha256="7" * 64,
        checks=(QualificationCheckV1(name="controlled", status="pass", evidence={}),),
    )
    report_ref = _put(
        store,
        root,
        name=f"{skill_id}-report.json",
        value=report,
        schema_version="harness.skill_qualification_report.v1",
    )
    qualification = SkillQualification(
        skill_ref=skill_ref,
        status="pass",
        deterministic_case_id=report.deterministic_case_id,
        regression_command=report.regression_command,
        report_sha256=report_ref.sha256,
    )
    qualification_ref = _put(
        store,
        root,
        name=f"{skill_id}-qualification.json",
        value=qualification,
        schema_version="harness.skill_qualification.v1",
    )
    descriptor = SkillDescriptor(
        skill_id=skill_id,
        version="1.0.0",
        mcp_tool_name=skill_id.replace(".", "_") + "_v1_0_0",
        input_schema=input_schema,
        output_schema=output_schema,
        implementation_name=f"tests.{skill_id}",
        implementation_version="1",
        implementation_sha256=implementation_sha256,
        deterministic=True,
        max_attempts=1,
        qualification_artifact=qualification_ref,
    )
    descriptor_ref = _put(
        store,
        root,
        name=f"{skill_id}-descriptor.json",
        value=descriptor,
        schema_version="harness.skill_descriptor.v1",
    )
    return descriptor, QualifiedSkillSnapshot(
        skill_ref=skill_ref,
        mcp_tool_name=descriptor.mcp_tool_name,
        descriptor_ref=descriptor_ref,
        qualification_ref=qualification_ref,
        qualification_report_ref=report_ref,
    )


def _sqlite_counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(path) as connection:
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
        return {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in tables
        }


def _assert_cas_objects_are_content_addressed(store: LocalArtifactStore) -> int:
    objects = tuple((store.root / "sha256").glob("*/*"))
    for path in objects:
        digest = path.name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    return len(objects)


def test_real_controlled_observe_runs_inside_one_golden_revision_two_to_three(
    tmp_path: Path,
) -> None:
    from self_improving.harness.golden_observe_execution import (
        create_controlled_genesis_observe_execution_binding,
    )

    store = LocalArtifactStore(tmp_path / "cas")
    clock = _Clock()
    setup_descriptor, setup_entry = _qualified_fixture(
        store,
        tmp_path,
        skill_id="tests.setup",
        input_schema="harness.artifact_ref.v1",
        output_schema="harness.artifact_ref.v1",
        implementation_sha256="1" * 64,
    )
    replay_descriptor, replay_entry = _qualified_fixture(
        store,
        tmp_path,
        skill_id="image2env.replay",
        input_schema="harness.image2env_replay_input.v1",
        output_schema="harness.image2env_replay_output.v1",
        implementation_sha256="2" * 64,
    )
    observe_descriptor, observe_entry = _qualified_fixture(
        store,
        tmp_path,
        skill_id="genesis.observe",
        input_schema="harness.genesis_observe_input.v1",
        output_schema="harness.genesis_observe_output.v1",
        implementation_sha256="3" * 64,
    )
    registry_ref = _put(
        store,
        tmp_path,
        name="integration-registry.json",
        value=RegistrySnapshot(
            schema_version="harness.registry_snapshot.v1",
            qualified_skills=(observe_entry, replay_entry, setup_entry),
        ),
        schema_version="harness.registry_snapshot.v1",
    )

    json_refs = {
        name: _put(
            store, tmp_path, name=f"{name}.json", value={"name": name}, schema_version=schema
        )
        for name, schema in (
            ("probe", "harness.image_media_probe.v1"),
            ("scene", "harness.scene_ir.v1"),
            ("task", "harness.task_ir.v1"),
            ("assets", "harness.asset_closure_manifest.v1"),
            ("bindings", "harness.x2env_binding_manifest.v1"),
            ("runtime-lock", "harness.genesis_runtime_lock.v1"),
            ("compile", "harness.x2env_compile_receipt.v1"),
            ("attestation", "harness.genesis_runtime_attestation.v1"),
            ("runtime-evidence", "harness.genesis_runtime_evidence.v1"),
            ("physics", "harness.genesis_physics_report.v1"),
            ("frames", "harness.video_frame_sequence.v1"),
            ("media", "harness.x2env_media_verification.v1"),
            ("runtime", "harness.x2env_runtime_receipt.v1"),
            ("replay-fresh", "harness.fresh_observation.v1"),
        )
    }
    source = _put_binary(
        store,
        tmp_path,
        name="source.jpg",
        payload=b"controlled-jpeg",
        media_type="image/jpeg",
        schema_version=None,
    )
    canonical_image = _put_binary(
        store,
        tmp_path,
        name="source.png",
        payload=_PNG,
        media_type="image/png",
        schema_version=None,
    )
    video = _put_binary(
        store,
        tmp_path,
        name="replay.mp4",
        payload=b"controlled-video",
        media_type="video/mp4",
        schema_version=None,
    )
    package = X2EnvEnvironmentPackage.model_validate(
        {
            "schema_version": "harness.x2env_environment_package.v1",
            "package_id": "4" * 64,
            "route_id": "image2env",
            "producer_skill_ref": "image2env.compile@1.0.0",
            "source_media": {
                "schema_version": "harness.image_source_media.v1",
                "source_media_sha256": source.sha256,
                "source": source,
                "canonical_image": canonical_image,
                "probe": json_refs["probe"],
                "width": 1,
                "height": 1,
                "color_mode": "RGB",
                "decoded_frame_count": 1,
                "unique_frame_count": 1,
            },
            "requested_profile": "genesis.rigid_scene@1",
            "environment_format": "harness.genesis_portable.v1",
            "backend": "genesis",
            "representation_origin": "harness_native",
            "legacy_adapter_receipt": None,
            "scene_ir": json_refs["scene"],
            "task_ir": json_refs["task"],
            "asset_closure_manifest": json_refs["assets"],
            "binding_manifest": json_refs["bindings"],
            "runtime_lock": json_refs["runtime-lock"],
            "simulator_ready": False,
            "collision_qualified": False,
        }
    )
    package_ref = _put(
        store,
        tmp_path,
        name="environment-package.json",
        value=package,
        schema_version="harness.x2env_environment_package.v1",
    )
    replay_input = Image2EnvReplayInput(
        schema_version="harness.image2env_replay_input.v1",
        environment_package=package,
        compile_receipt=json_refs["compile"],
        requested_profile="genesis.rigid_scene@1",
        runtime_attestation=json_refs["attestation"],
        seed=17,
        physics_plan={
            "collision_enabled": True,
            "settle_steps": 1,
            "observation_steps": 1,
            "video_frames": 1,
            "fps": 1,
            "robot_probe_steps": 0,
        },
    )
    replay_output = Image2EnvReplayOutput(
        schema_version="harness.image2env_replay_output.v1",
        environment_package=package,
        package_id=package.package_id,
        requested_profile="genesis.rigid_scene@1",
        runtime_evidence=json_refs["runtime-evidence"],
        physics_report=json_refs["physics"],
        robot_probe_report=None,
        trajectory=None,
        video=video,
        frame_sequence_manifest=json_refs["frames"],
        media_verification=json_refs["media"],
        runtime_receipt=json_refs["runtime"],
        fresh_observation_refs=(json_refs["replay-fresh"],),
        steps_executed=2,
        total_frame_count=1,
        unique_frame_count=1,
        collisions_enabled=True,
        simulator_executed=True,
    )

    setup_policy = _FixturePolicy("tests.setup@1.0.0", ArtifactRef)
    replay_policy = _FixturePolicy("image2env.replay@1.0.0", Image2EnvReplayInput)
    fixture_store = SQLiteRunStore(tmp_path / "fixture.sqlite3")
    fixture_registry = SkillRegistry(
        artifact_resolver=store,
        dependency_resolver=StaticDependencyResolver(
            {"tests.setup@1.0.0": (), "image2env.replay@1.0.0": ()}
        ),
        event_sink=SQLiteEventJournal(fixture_store.path),
        clock=clock,
        run_id_factory=lambda: pytest.fail(
            "fixture Registry must receive its child run id from Golden"
        ),
        run_store=fixture_store,
    )
    fixture_registry.register(
        setup_descriptor,
        lambda value, context: HandlerResult(output=value, artifacts=(value,)),
    )
    replay_artifacts = tuple(
        sorted(
            (*json_refs.values(), source, canonical_image, video),
            key=lambda ref: (ref.sha256, ref.schema_version or "", ref.media_type),
        )
    )
    fixture_registry.register(
        replay_descriptor,
        lambda value, context: HandlerResult(
            output=replay_output,
            artifacts=replay_artifacts,
        ),
    )
    setup_application = _FixtureApplication(
        store.root,
        fixture_store.path,
        fixture_registry,
        setup_descriptor,
        setup_policy,
        setup_policy.skill_ref,
    )
    replay_application = _FixtureApplication(
        store.root,
        fixture_store.path,
        fixture_registry,
        replay_descriptor,
        replay_policy,
        replay_policy.skill_ref,
    )

    capture = _ControlledCapture()
    observe_application = create_controlled_genesis_observe_application(
        ControlledObserveApplicationSettings(tmp_path / "observe-state", store.root),
        capture_adapter=capture,
        clock=clock,
        run_id_factory=lambda: _INTEGRATION_CHILD_IDS[2],
    )
    observe_binding = create_controlled_genesis_observe_execution_binding(
        application=observe_application,
        qualified_descriptor=observe_descriptor,
    )
    workflow_store = SQLiteGoldenRunStore(tmp_path / "golden.sqlite3")
    child_ids = iter(_INTEGRATION_CHILD_IDS)
    harness = GoldenRunHarness(
        artifact_store=store,
        workflow_store=workflow_store,
        execution_catalog=create_golden_execution_catalog(
            bindings=(
                _create_golden_execution_binding(
                    policy=setup_policy,
                    application=setup_application,
                    child_authority=fixture_store,
                ),
                _create_golden_execution_binding(
                    policy=replay_policy,
                    application=replay_application,
                    child_authority=fixture_store,
                ),
                observe_binding,
            )
        ),
        clock=clock,
        workflow_id_factory=lambda: WORKFLOW_ID,
        child_run_id_factory=lambda: next(child_ids),
    )
    objective = WorldFactEvidence(
        source_kind="user_input",
        key="request.objective",
        value="Capture a fresh controlled Genesis observation.",
        observed_at=START,
        valid_until=None,
    )
    objective_ref = _put(
        store,
        tmp_path,
        name="integration-objective.json",
        value=objective,
        schema_version="harness.world_fact_evidence.v1",
    )
    snapshot = harness.start(
        RunStartRequest(
            schema_version="harness.run_start_request.v1",
            principal_id="tests/real-controlled-observe",
            idempotency_key="real-observe-start",
            workspace="ephemeral",
            requested_profile="genesis.robot_policy@1",
            initial_fact_evidence=(objective_ref,),
            registry_snapshot=registry_ref,
        )
    )

    def submit(skill_ref: str, parameters: BaseModel, index: int):
        nonlocal snapshot
        command = RunCommandRequest(
            schema_version="harness.run_command_request.v1",
            command_id=UUID(f"65000000-0000-4000-8000-{index:012d}"),
            principal_id=snapshot.principal_id,
            workflow_run_id=snapshot.workflow_run_id,
            idempotency_key=f"real-observe-{index}",
            expected_workflow_revision=snapshot.revision,
            expected_state_sha256=snapshot.state_sha256,
            expected_receipt_head_sha256=snapshot.receipt_head.sha256,
            skill_ref=skill_ref,
            parameters=parameters.model_dump(mode="json"),
        )
        operation = harness.submit(command)
        snapshot = harness.read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id=snapshot.principal_id,
                workflow_run_id=snapshot.workflow_run_id,
            )
        )
        return command, operation

    submit("tests.setup@1.0.0", objective_ref, 1)
    submit("image2env.replay@1.0.0", replay_input, 2)
    assert snapshot.revision == 2
    observe_input = GenesisObserveInput(
        schema_version="harness.genesis_observe_input.v1",
        environment_package=package_ref,
        replay_workflow_receipt=snapshot.receipt_head,
        replay_runtime_receipt=json_refs["runtime"],
        checkpoint=None,
        capture_plan=GenesisObservationCapturePlan(
            camera_ids=("observer",),
            stills_per_camera=1,
            video_frames=0,
            fps=None,
            post_restore_steps=1,
        ),
        requested_observation_keys=("observation.scene_visible",),
        ttl_seconds=60,
    )
    observe_command, observe_operation = submit("genesis.observe@1.0.0", observe_input, 3)

    assert observe_operation.status is RunStatus.SUCCEEDED
    assert snapshot.revision == 3
    assert capture.calls == 1
    result = ToolResultV2.model_validate_json(
        store.resolve(observe_operation.tool_result).path.read_bytes()
    )
    output = GenesisObserveOutput.model_validate(result.typed_output)
    inner = GenesisObservationReceipt.model_validate_json(
        store.resolve(output.observation_receipt).path.read_bytes()
    )
    observation = result.fresh_observations[0]
    receipt = TrustedToolReceiptV2.model_validate_json(
        store.resolve(result.trusted_receipt).path.read_bytes()
    )
    claim = receipt.derived_fact_claims[0]
    assert output.observation_receipt in receipt.supporting_artifacts
    assert observation.source_artifact == output.observation_receipt
    assert (claim.key, claim.value) == (observation.key, observation.value)
    assert observation.observed_at == inner.captured_at
    assert observation.valid_until == inner.valid_until
    delta = StateDeltaV2.model_validate_json(store.resolve(result.state_delta).path.read_bytes())
    fact = delta.mutations[0].fact
    assert fact is not None
    assert fact.source_artifact == result.trusted_receipt
    assert (fact.key, fact.value) == (observation.key, observation.value)
    assert (fact.observed_at, fact.valid_until) == (
        observation.observed_at,
        observation.valid_until,
    )
    state = TrustedWorldState.model_validate_json(
        store.resolve(snapshot.state_ref).path.read_bytes()
    )
    assert next(item for item in state.facts if item.key == observation.key) == fact

    cas_count = _assert_cas_objects_are_content_addressed(store)
    golden_rows = _sqlite_counts(workflow_store.path)
    fixture_rows = _sqlite_counts(fixture_store.path)
    observe_rows = _sqlite_counts(observe_application.authority_path)
    reopened = GoldenRunHarness(
        artifact_store=store,
        workflow_store=workflow_store,
        execution_catalog=create_golden_execution_catalog(
            bindings=(
                _create_golden_execution_binding(
                    policy=setup_policy,
                    application=None,
                    child_authority=fixture_store,
                ),
                _create_golden_execution_binding(
                    policy=replay_policy,
                    application=None,
                    child_authority=fixture_store,
                ),
                create_controlled_genesis_observe_execution_binding(
                    application=None,
                    child_authority=SQLiteRunStore(observe_application.authority_path),
                    artifact_store=store,
                ),
            )
        ),
        clock=lambda: pytest.fail("terminal observe retry must not read the clock"),
        child_run_id_factory=lambda: pytest.fail("terminal observe retry must not reserve a child"),
    )
    assert reopened.submit(observe_command) == observe_operation
    assert _assert_cas_objects_are_content_addressed(store) == cas_count
    assert _sqlite_counts(workflow_store.path) == golden_rows
    assert _sqlite_counts(fixture_store.path) == fixture_rows
    assert _sqlite_counts(observe_application.authority_path) == observe_rows


@dataclass(frozen=True)
class _ControlledFactoryCase:
    store: LocalArtifactStore
    application: object
    descriptor: SkillDescriptor
    request: GenesisObserveInput


def _controlled_factory_case(tmp_path: Path) -> _ControlledFactoryCase:
    store = LocalArtifactStore(tmp_path / "cas")
    descriptor, _ = _qualified_fixture(
        store,
        tmp_path,
        skill_id="genesis.observe",
        input_schema="harness.genesis_observe_input.v1",
        output_schema="harness.genesis_observe_output.v1",
        implementation_sha256="8" * 64,
    )
    request = GenesisObserveInput(
        schema_version="harness.genesis_observe_input.v1",
        environment_package=_put(
            store,
            tmp_path,
            name="factory-package.json",
            value={"package": True},
            schema_version="harness.x2env_environment_package.v1",
        ),
        replay_workflow_receipt=_put(
            store,
            tmp_path,
            name="factory-workflow.json",
            value={"workflow": True},
            schema_version="harness.workflow_operation_receipt.v2",
        ),
        replay_runtime_receipt=_put(
            store,
            tmp_path,
            name="factory-runtime.json",
            value={"runtime": True},
            schema_version="harness.x2env_runtime_receipt.v1",
        ),
        checkpoint=None,
        capture_plan=GenesisObservationCapturePlan(
            camera_ids=("observer",),
            stills_per_camera=1,
            video_frames=0,
            fps=None,
            post_restore_steps=1,
        ),
        requested_observation_keys=("observation.scene_visible",),
        ttl_seconds=60,
    )
    application = create_controlled_genesis_observe_application(
        ControlledObserveApplicationSettings(tmp_path / "state", store.root),
        capture_adapter=_ControlledCapture(),
        clock=_Clock(),
        run_id_factory=lambda: pytest.fail(
            "bound controlled application must receive its child identity from the caller"
        ),
    )
    return _ControlledFactoryCase(store, application, descriptor, request)


def test_controlled_observe_binding_factory_enforces_exact_live_and_restart_inputs(
    tmp_path: Path,
) -> None:
    from self_improving.harness.golden_observe_execution import (
        create_controlled_genesis_observe_execution_binding,
    )

    case = _controlled_factory_case(tmp_path)
    with pytest.raises(TypeError, match="application must be the exact"):
        create_controlled_genesis_observe_execution_binding(application=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="qualified_descriptor must be the exact"):
        create_controlled_genesis_observe_execution_binding(
            application=case.application,  # type: ignore[arg-type]
            qualified_descriptor=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="child_authority must be the exact"):
        create_controlled_genesis_observe_execution_binding(
            application=None,
            child_authority=object(),  # type: ignore[arg-type]
            artifact_store=case.store,
        )
    with pytest.raises(TypeError, match="artifact_store must be the exact"):
        create_controlled_genesis_observe_execution_binding(
            application=None,
            artifact_store=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="live binding requires"):
        create_controlled_genesis_observe_execution_binding(
            application=case.application,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="restart binding cannot install"):
        create_controlled_genesis_observe_execution_binding(
            application=None,
            qualified_descriptor=case.descriptor,
            artifact_store=case.store,
        )
    with pytest.raises(ValueError, match="restart binding requires"):
        create_controlled_genesis_observe_execution_binding(application=None)
    foreign_store = LocalArtifactStore(tmp_path / "foreign-cas")
    with pytest.raises(ValueError, match="differs from controlled observe application CAS"):
        create_controlled_genesis_observe_execution_binding(
            application=case.application,  # type: ignore[arg-type]
            qualified_descriptor=case.descriptor,
            artifact_store=foreign_store,
        )

    live = create_controlled_genesis_observe_execution_binding(
        application=case.application,  # type: ignore[arg-type]
        qualified_descriptor=case.descriptor,
        child_authority=SQLiteRunStore(case.application.authority_path),  # type: ignore[union-attr]
        artifact_store=case.store,
    )
    assert live.skill_ref == "genesis.observe@1.0.0"
    assert live.application is not None
    assert live.child_authority is not None
    restart = create_controlled_genesis_observe_execution_binding(
        application=None,
        child_authority=SQLiteRunStore(case.application.authority_path),  # type: ignore[union-attr]
        artifact_store=case.store,
    )
    assert restart.skill_ref == live.skill_ref
    assert restart.application is None
    assert restart.child_authority is not None


@pytest.mark.parametrize(
    "update",
    [
        {"skill_id": "genesis.other"},
        {"version": "2.0.0"},
        {"input_schema": "harness.artifact_ref.v1"},
        {"output_schema": "harness.artifact_ref.v1"},
        {"mcp_tool_name": "genesis_other_v1_0_0"},
    ],
)
def test_controlled_observe_binding_rejects_non_exact_descriptor(
    tmp_path: Path,
    update: dict[str, object],
) -> None:
    from self_improving.harness.golden_observe_execution import (
        create_controlled_genesis_observe_execution_binding,
    )

    case = _controlled_factory_case(tmp_path)
    wrong = case.descriptor.model_copy(update=update)
    with pytest.raises(ValueError, match="does not describe exact genesis.observe"):
        create_controlled_genesis_observe_execution_binding(
            application=case.application,  # type: ignore[arg-type]
            qualified_descriptor=wrong,
        )
    assert case.application.skills == ()  # type: ignore[union-attr]


def test_controlled_observe_binding_is_single_identity_and_disables_candidate_mode(
    tmp_path: Path,
) -> None:
    from self_improving.harness.golden_observe_execution import (
        create_controlled_genesis_observe_execution_binding,
    )

    case = _controlled_factory_case(tmp_path)
    first = create_controlled_genesis_observe_execution_binding(
        application=case.application,  # type: ignore[arg-type]
        qualified_descriptor=case.descriptor,
    )
    second = create_controlled_genesis_observe_execution_binding(
        application=case.application,  # type: ignore[arg-type]
        qualified_descriptor=case.descriptor,
    )
    assert first.skill_ref == second.skill_ref
    assert case.application.skills == (case.descriptor,)  # type: ignore[union-attr]
    with pytest.raises(RuntimeError, match="not a candidate evaluator"):
        case.application.evaluate(case.request)  # type: ignore[union-attr]

    other_descriptor, _ = _qualified_fixture(
        case.store,
        tmp_path / "other",
        skill_id="genesis.observe",
        input_schema="harness.genesis_observe_input.v1",
        output_schema="harness.genesis_observe_output.v1",
        implementation_sha256="9" * 64,
    )
    with pytest.raises(ValueError, match="already bound to another qualification"):
        create_controlled_genesis_observe_execution_binding(
            application=case.application,  # type: ignore[arg-type]
            qualified_descriptor=other_descriptor,
        )


def test_controlled_observe_live_adapter_enforces_input_identity_and_pending_recovery(
    tmp_path: Path,
) -> None:
    from self_improving.harness.genesis_observe import (
        ControlledObserveApplicationRunIdentityError,
    )
    from self_improving.harness.golden_observe_execution import (
        create_controlled_genesis_observe_execution_binding,
    )

    unbound = _controlled_factory_case(tmp_path / "unbound")
    with pytest.raises(RuntimeError, match="no Golden-bound qualification"):
        unbound.application.invoke_typed(  # type: ignore[union-attr]
            unbound.request,
            run_id=UUID("66000000-0000-4000-8000-000000000001"),
        )

    case = _controlled_factory_case(tmp_path / "bound")
    binding = create_controlled_genesis_observe_execution_binding(
        application=case.application,  # type: ignore[arg-type]
        qualified_descriptor=case.descriptor,
    )
    adapter = binding.application
    assert adapter is not None
    missing = UUID("66000000-0000-4000-8000-000000000002")
    assert adapter.invocation(missing) is None
    assert adapter.run_state(missing) is None
    with pytest.raises(TypeError, match="adapter requires GenesisObserveInput"):
        adapter.invoke(object(), run_id=missing)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="requires exact GenesisObserveInput"):
        case.application.invoke_typed(object(), run_id=missing)  # type: ignore[union-attr,arg-type]

    completed_id = UUID("66000000-0000-4000-8000-000000000003")
    completed = adapter.invoke(case.request, run_id=completed_id)
    assert completed.status is RunStatus.SUCCEEDED
    changed = case.request.model_copy(update={"ttl_seconds": 59})
    with pytest.raises(ControlledObserveApplicationRunIdentityError, match="another intent"):
        case.application.invoke_typed(changed, run_id=completed_id)  # type: ignore[union-attr]

    authority = SQLiteRunStore(case.application.authority_path)  # type: ignore[union-attr]
    lifecycle = authority.read_controlled_lifecycle(completed_id)
    assert lifecycle.intent is not None
    pending_id = UUID("66000000-0000-4000-8000-000000000004")
    pending_intent = lifecycle.intent.model_copy(update={"run_id": pending_id})
    assert authority.reserve_controlled_intent(pending_intent) is True
    with pytest.raises(QualifiedApplicationRunPendingError, match="no durable invocation"):
        adapter.invoke(case.request, run_id=pending_id)
