from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from uuid import UUID

import pytest
from pydantic import ValidationError

import self_improving.harness.application as application_module
from scene_gen.builder import build_scene_package
from scene_gen.catalog import load_catalog
from scene_gen.parser import parse_rule_based
from scene_gen.solver import solve_scene
from scene_gen.validator import validate_resolved_scene
from self_improving.harness.application import (
    CompileApplication,
    CompileApplicationConfigurationError,
    create_compile_application,
)
from self_improving.harness.artifacts import LocalArtifactStore, ResolvedArtifact
from self_improving.harness.handlers.text2env_compile import text2env_compile_descriptor
from self_improving.harness.registry import _invocation_digest
from self_improving.harness.schemas import (
    ArtifactRef,
    Blocker,
    DependencyRef,
    EnvironmentPackage,
    Event,
    ExecutionReproducibility,
    Invocation,
    RunState,
    RunStatus,
    SkillDescriptorV2,
    SkillQualification,
    Text2EnvCompileInput,
    Text2EnvCompileOutput,
)
from self_improving.harness.system2.context import (
    ContextBudget,
    PlannerContext,
    PlannerDecision,
    PlannerHistoryEntry,
    PlannerSkillCard,
    compile_planner_context,
    decision_sha256,
    planner_context_sha256,
)
from self_improving.harness.system2.dispatcher import (
    CompileSystem2Application,
    ReceiptDerivedFactClaim,
    System2Dispatcher,
    System2DispatchError,
    TrustedToolReceipt,
    verify_trusted_tool_receipt,
)
from self_improving.harness.system2.domain import (
    TrustedWorldState,
    WorldFact,
    apply_state_delta,
    build_world_state,
)
from self_improving.harness.system2.history import publish_planner_history_authority
from self_improving.harness.system2.planner import (
    LocalPlannerArtifactPublisher,
    PlannerExecutionReceipt,
    PlannerProviderIdentity,
    PlannerProviderResponse,
    PlannerUsage,
    System2Planner,
    build_planner_prompt,
)

_T0 = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _put(
    store: LocalArtifactStore,
    tmp_path: Path,
    *,
    name: str,
    schema: str,
    value: object,
    media_type: str = "application/json",
) -> ArtifactRef:
    path = tmp_path / f"{name}-{len(list(tmp_path.iterdir()))}.bin"
    path.write_bytes(_canonical(value))
    return store.put_file(
        path,
        name=name,
        media_type=media_type,
        schema_version=schema,
    )


def _put_file(
    store: LocalArtifactStore,
    path: Path,
    *,
    name: str | None = None,
    media_type: str,
    schema: str | None,
) -> ArtifactRef:
    return store.put_file(
        path,
        name=name or path.stem,
        media_type=media_type,
        schema_version=schema,
    )


class _Clock:
    def __init__(self, start: datetime = _T0) -> None:
        self.value = start

    def __call__(self) -> datetime:
        value = self.value
        self.value += timedelta(milliseconds=1)
        return value


@dataclass
class _Provider:
    raw: bytes
    calls: int = 0

    @property
    def identity(self) -> PlannerProviderIdentity:
        return PlannerProviderIdentity(
            provider_id="fixture_local",
            model_id="fixture/planner",
            model_revision="revision-1",
            model_snapshot_sha256="1" * 64,
            implementation_sha256="2" * 64,
            inference={"do_sample": False},
            network_access=False,
        )

    def invoke(
        self,
        prompt: bytes,
        progress: Callable[[str], None],
    ) -> PlannerProviderResponse:
        self.calls += 1
        assert prompt
        progress("inference")
        return PlannerProviderResponse(
            raw=self.raw,
            usage=PlannerUsage(
                input_tokens=10,
                output_tokens=5,
                wall_time_ms=2,
                peak_vram_bytes=None,
                finish_reason="stop",
            ),
        )


@dataclass
class _CompileBackend:
    artifact_root: Path
    descriptor: object
    output: Text2EnvCompileOutput
    support: ArtifactRef
    package_members: tuple[ArtifactRef, ...] = ()
    blocked_preflight: bool = False
    mutate_invocation: bool = False
    run_max_attempts: int = 1
    started_at_override: datetime | None = None
    ended_at_override: datetime | None = None
    calls: list[Text2EnvCompileInput] = field(default_factory=list)
    _invocations: dict[UUID, Invocation] = field(default_factory=dict)

    @property
    def skills(self) -> tuple[object, ...]:
        return (self.descriptor,)

    def invoke_typed(self, parameters: Text2EnvCompileInput) -> RunState:
        self.calls.append(parameters)
        run_id = UUID("00000000-0000-4000-8000-000000000456")
        started = self.started_at_override or (_T0 + timedelta(seconds=1))
        ended = self.ended_at_override or (_T0 + timedelta(seconds=2))
        if self.blocked_preflight:
            blocker = Blocker(
                code="HARN_DEPENDENCY_UNAVAILABLE",
                message="compile dependency unavailable",
                stage="preflight",
                retryable=False,
                details={},
                unknowns=(),
                artifact_refs=(),
            )
            return RunState(
                run_id=run_id,
                invocation_digest=None,
                skill_id="text2env.compile",
                skill_version="1.0.0",
                status=RunStatus.BLOCKED,
                attempt=0,
                max_attempts=0,
                started_at=started,
                ended_at=ended,
                events=(
                    Event(
                        seq=1,
                        timestamp=started,
                        stage="preflight",
                        attempt=0,
                        from_status=None,
                        to_status=RunStatus.RUNNING,
                        artifact_refs=(),
                    ),
                    Event(
                        seq=2,
                        timestamp=ended,
                        stage="preflight",
                        attempt=0,
                        from_status=RunStatus.RUNNING,
                        to_status=RunStatus.BLOCKED,
                        artifact_refs=(),
                    ),
                ),
                artifacts=(),
                output=None,
                blocker=blocker,
            )
        dependencies = (DependencyRef(name="compile-runtime", version="1", sha256="3" * 64),)
        digest = _invocation_digest(
            skill_id="text2env.compile",
            skill_version="1.0.0",
            effective_parameters=parameters,
            dependencies=dependencies,
            max_attempts=1,
        )
        effective = parameters.model_dump(mode="json")
        if self.mutate_invocation:
            effective["seed"] = 999
        self._invocations[run_id] = Invocation(
            run_id=run_id,
            skill_id="text2env.compile",
            skill_version="1.0.0",
            effective_parameters=effective,
            dependencies=dependencies,
            max_attempts=1,
            invocation_digest=digest,
        )
        artifacts = tuple(
            sorted(
                {
                    artifact.sha256: artifact
                    for artifact in (
                        self.output.scene_spec,
                        self.output.resolved_scene,
                        self.output.environment_package.asset_catalog,
                        self.output.environment_package.package_manifest,
                        self.output.static_validation,
                        self.support,
                        *self.package_members,
                    )
                }.values(),
                key=lambda artifact: artifact.sha256,
            )
        )
        return RunState(
            run_id=run_id,
            invocation_digest=digest,
            skill_id="text2env.compile",
            skill_version="1.0.0",
            status=RunStatus.SUCCEEDED,
            attempt=1,
            max_attempts=self.run_max_attempts,
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
                    artifact_refs=artifacts,
                ),
            ),
            artifacts=artifacts,
            output=self.output.model_dump(mode="json"),
            blocker=None,
        )

    def invocation(self, run_id: UUID) -> Invocation | None:
        return self._invocations.get(run_id)


@dataclass(frozen=True)
class _Fixture:
    store: LocalArtifactStore
    state: object
    context: object
    planning: object
    app: _CompileBackend
    adapter: CompileSystem2Application
    dispatcher: System2Dispatcher


def _fixture(
    tmp_path: Path,
    *,
    blocked_preflight: bool = False,
    request: str = "Place a can on top of a plate.",
) -> _Fixture:
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = LocalArtifactStore(tmp_path / "cas")
    request_ref = _put(
        store,
        tmp_path,
        name="request",
        schema="harness.world_fact_evidence.v1",
        value={
            "schema_version": "harness.world_fact_evidence.v1",
            "source_kind": "user_input",
            "key": "task.objective",
            "value": request,
            "observed_at": _T0.isoformat(),
            "valid_until": None,
        },
    )
    catalog = load_catalog(Path("tests/fixtures/asset_catalog.json"))
    catalog_path = tmp_path / "effective_asset_catalog.canonical.json"
    catalog_path.write_text(
        json.dumps(
            catalog.canonical_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    catalog_ref = _put_file(
        store,
        catalog_path,
        name="effective_asset_catalog",
        media_type="application/json",
        schema="robotwin.asset_catalog.v1",
    )
    report_ref = _put(
        store,
        tmp_path,
        name="qualification_report",
        schema="harness.skill_qualification_report.v1",
        value={
            "schema_version": "harness.skill_qualification_report.v1",
            "skill_ref": "text2env.compile@1.0.0",
            "status": "pass",
            "deterministic_case_id": "dispatcher-fixture",
            "regression_command": "pytest dispatcher fixture",
            "implementation_sha256": "4" * 64,
            "scene_gen_tree_sha256": "5" * 64,
            "ledger_contract_tree_sha256": "6" * 64,
            "checks": [
                {
                    "name": "dispatcher.fixture",
                    "status": "pass",
                    "evidence": {"fixture_only": True},
                }
            ],
        },
    )
    qualification_ref = _put(
        store,
        tmp_path,
        name="qualification",
        schema="harness.skill_qualification.v1",
        value={
            "skill_ref": "text2env.compile@1.0.0",
            "status": "pass",
            "deterministic_case_id": "dispatcher-fixture",
            "regression_command": "pytest dispatcher fixture",
            "report_sha256": report_ref.sha256,
        },
    )
    scene_spec = parse_rule_based(request, seed=7)
    resolved_scene = solve_scene(scene_spec, catalog)
    package_root = tmp_path / "package"
    build_scene_package(scene_spec, resolved_scene, package_root)
    scene_ref = _put_file(
        store,
        package_root / "scene_spec.json",
        media_type="application/json",
        schema="robotwin.scene_spec.v1",
    )
    resolved_ref = _put_file(
        store,
        package_root / "resolved_scene.json",
        media_type="application/json",
        schema="robotwin.resolved_scene.v1",
    )
    request_member = _put_file(
        store,
        package_root / "request.txt",
        media_type="text/plain",
        schema=None,
    )
    generated_member = _put_file(
        store,
        package_root / "generated_scene.py",
        media_type="text/x-python",
        schema=None,
    )
    manifest_ref = _put_file(
        store,
        package_root / "package_manifest.json",
        media_type="application/json",
        schema="robotwin.generated_scene_package.v1",
    )
    validation_ref = _put(
        store,
        tmp_path,
        name="static_validation",
        schema="robotwin.scene_validation.v1",
        value=validate_resolved_scene(
            resolved_scene,
            catalog=None,
            package_root=package_root,
            require_runtime=False,
        ),
    )
    support_ref = _put(
        store,
        tmp_path,
        name="admission",
        schema="harness.generated_asset_admission.v1",
        value={"schema_version": "harness.generated_asset_admission.v1", "status": "reused"},
    )
    descriptor = text2env_compile_descriptor(
        qualification_artifact=qualification_ref,
        implementation_sha256="4" * 64,
    )
    output = Text2EnvCompileOutput(
        scene_spec=scene_ref,
        resolved_scene=resolved_ref,
        environment_package=EnvironmentPackage(
            package_id=resolved_scene.digest(),
            route_id="text2env",
            producer_skill_ref="text2env.compile@1.0.0",
            seed=7,
            scene_spec_sha256=scene_spec.digest(),
            resolved_scene_sha256=resolved_scene.digest(),
            asset_catalog=catalog_ref,
            package_manifest=manifest_ref,
        ),
        static_validation=validation_ref,
    )
    objective = WorldFact(
        key="task.objective",
        value=request,
        source_kind="user_input",
        source_artifact=request_ref,
        observed_at=_T0,
        valid_until=None,
    )
    state = build_world_state((objective,), as_of=_T0)
    context = compile_planner_context(
        state=state,
        skills=(
            PlannerSkillCard(
                skill_ref="text2env.compile@1.0.0",
                purpose="Compile an environment package without claiming physical success.",
                input_schema=descriptor.input_schema,
                output_schema=descriptor.output_schema,
                qualification_sha256=qualification_ref.sha256,
                max_attempts=descriptor.max_attempts,
            ),
        ),
        history=(),
        required_fact_keys=(),
        budget=ContextBudget(max_facts=2, max_history=0, max_context_bytes=20_000),
    )
    decision = {
        "schema_version": "harness.planner_decision.v1",
        "base_state_sha256": state.state_sha256,
        "context_sha256": context.context_sha256,
        "action": "invoke_skill",
        "skill_ref": "text2env.compile@1.0.0",
        "parameters": {
            "request": request,
            "seed": 7,
            "asset_catalog": catalog_ref.model_dump(mode="json"),
            "config": {"generate_missing_assets": False},
        },
        "observation_keys": [],
        "stop_reason": None,
        "summary": "Compile before any physical replay claim.",
    }
    provider = _Provider(_canonical(decision))
    planning = System2Planner(
        provider=provider,
        publisher=LocalPlannerArtifactPublisher(store, tmp_path / "planner-scratch"),
        clock=_Clock(),
        uuid_factory=lambda: UUID("00000000-0000-4000-8000-000000000123"),
    ).plan(context)
    app = _CompileBackend(
        artifact_root=store.root,
        descriptor=descriptor,
        output=output,
        support=support_ref,
        package_members=(request_member, generated_member),
        blocked_preflight=blocked_preflight,
    )
    adapter = CompileSystem2Application._for_testing(
        backend=app,
        artifact_store=store,
    )
    dispatcher = System2Dispatcher(
        artifact_store=store,
        scratch_root=tmp_path / "dispatch-scratch",
        applications=(adapter,),
    )
    return _Fixture(store, state, context, planning, app, adapter, dispatcher)


def _rebind_receipt(
    fixture: _Fixture,
    tmp_path: Path,
    **updates: object,
):
    receipt = PlannerExecutionReceipt.model_validate(
        {**fixture.planning.receipt.model_dump(mode="python"), **updates}
    )
    receipt_ref = _put(
        fixture.store,
        tmp_path,
        name="planner_execution_receipt_rebound",
        schema="harness.planner_execution_receipt.v1",
        value=receipt.model_dump(mode="json"),
    )
    events = (
        *fixture.planning.events[:-1],
        fixture.planning.events[-1].model_copy(update={"artifact_refs": (receipt_ref,)}),
    )
    return replace(
        fixture.planning,
        receipt=receipt,
        receipt_ref=receipt_ref,
        events=events,
    )


def _rebind_decision(
    fixture: _Fixture,
    tmp_path: Path,
    **updates: object,
):
    decision = PlannerDecision.model_validate(
        {**fixture.planning.decision.model_dump(mode="python"), **updates}
    )
    decision_ref = _put(
        fixture.store,
        tmp_path,
        name="planner_decision_rebound",
        schema="harness.planner_decision.v1",
        value=decision.model_dump(mode="json"),
    )
    digest = decision_sha256(decision)
    receipt = PlannerExecutionReceipt.model_validate(
        {
            **fixture.planning.receipt.model_dump(mode="python"),
            "decision": decision_ref,
            "decision_sha256": digest,
        }
    )
    receipt_ref = _put(
        fixture.store,
        tmp_path,
        name="planner_execution_receipt_rebound",
        schema="harness.planner_execution_receipt.v1",
        value=receipt.model_dump(mode="json"),
    )
    events = (
        *fixture.planning.events[:-1],
        fixture.planning.events[-1].model_copy(update={"artifact_refs": (receipt_ref,)}),
    )
    return replace(
        fixture.planning,
        decision=decision,
        decision_sha256=digest,
        decision_ref=decision_ref,
        receipt=receipt,
        receipt_ref=receipt_ref,
        events=events,
    )


def _replan_for_context(
    fixture: _Fixture,
    tmp_path: Path,
    context: PlannerContext,
    *,
    request: str,
):
    decision = {
        **fixture.planning.decision.model_dump(mode="json"),
        "base_state_sha256": context.world_state_sha256,
        "context_sha256": context.context_sha256,
        "parameters": {
            **fixture.planning.decision.parameters,
            "request": request,
        },
    }
    return System2Planner(
        provider=_Provider(_canonical(decision)),
        publisher=LocalPlannerArtifactPublisher(fixture.store, tmp_path / "replanner-scratch"),
        clock=_Clock(_T0 + timedelta(milliseconds=100)),
        uuid_factory=lambda: UUID("00000000-0000-4000-8000-000000000321"),
    ).plan(context)


def _compile_output_with_manifest(
    fixture: _Fixture,
    manifest_ref: ArtifactRef,
) -> Text2EnvCompileOutput:
    package = fixture.app.output.environment_package.model_copy(
        update={"package_manifest": manifest_ref}
    )
    return fixture.app.output.model_copy(update={"environment_package": package})


def _compile_supporting(fixture: _Fixture) -> tuple[ArtifactRef, ...]:
    output = fixture.app.output
    return (
        output.scene_spec,
        output.resolved_scene,
        output.environment_package.asset_catalog,
        output.environment_package.package_manifest,
        output.static_validation,
        fixture.app.support,
        *fixture.app.package_members,
    )


def test_dispatch_compile_returns_receipt_bound_tool_result_and_state_delta(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    result = fixture.dispatcher.dispatch(
        planning=fixture.planning,
        context=fixture.context,
        state=fixture.state,
    )

    assert result.status is RunStatus.SUCCEEDED
    assert len(fixture.app.calls) == 1
    assert result.invocation is not None
    assert result.typed_output == fixture.app.output.model_dump(mode="json")
    assert tuple(mutation.key for mutation in result.state_delta.mutations) == (
        "assets.catalog",
        "environment.package",
    )
    assert result.fresh_observations == ()
    assert fixture.app.support in result.diagnostics
    assert set(fixture.app.package_members).issubset(result.diagnostics)
    assert result.typed_output is not None
    output_refs = {
        fixture.app.output.scene_spec,
        fixture.app.output.resolved_scene,
        fixture.app.output.environment_package.asset_catalog,
        fixture.app.output.environment_package.package_manifest,
        fixture.app.output.static_validation,
    }
    assert output_refs.isdisjoint(result.diagnostics)
    receipt = TrustedToolReceipt.model_validate_json(
        fixture.store.resolve(result.trusted_receipt).path.read_bytes()
    )
    assert receipt.planner_receipt == fixture.planning.receipt_ref
    assert receipt.invocation == result.invocation
    assert receipt.run_state == result.run_state
    assert (
        TrustedWorldState.model_validate_json(
            fixture.store.resolve(receipt.base_state).path.read_bytes()
        )
        == fixture.state
    )
    assert (
        PlannerContext.model_validate_json(
            fixture.store.resolve(receipt.planner_context).path.read_bytes()
        )
        == fixture.context
    )
    assert receipt.derived_fact_claims[0].key == "assets.catalog"
    updated = apply_state_delta(
        fixture.state,
        result.state_delta,
        artifact_store=fixture.store,
    )
    assert updated.version == 2
    assert tuple(fact.key for fact in updated.facts) == (
        "assets.catalog",
        "environment.package",
        "task.objective",
    )
    assert all(
        mutation.fact is not None
        and mutation.fact.source_artifact == result.trusted_receipt
        and mutation.fact.source_kind == "trusted_receipt"
        for mutation in result.state_delta.mutations
    )


def test_dispatch_rejects_context_that_cannot_be_reprojected_from_current_state(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    changed_objective = fixture.context.objective.model_copy(
        update={"value": "put a bottle in a drawer"}
    )
    unbound = fixture.context.model_copy(
        update={
            "objective": changed_objective,
            "context_sha256": "0" * 64,
        }
    )
    context = PlannerContext.model_validate(
        {
            **unbound.model_dump(mode="python"),
            "context_sha256": planner_context_sha256(unbound),
        }
    )
    planning = _replan_for_context(
        fixture,
        tmp_path,
        context,
        request="put a bottle in a drawer",
    )

    with pytest.raises(System2DispatchError) as raised:
        fixture.dispatcher.dispatch(
            planning=planning,
            context=context,
            state=fixture.state,
        )

    assert raised.value.reason == "planner_evidence_invalid"


def test_dispatch_rejects_compile_request_unbound_from_current_objective_before_execution(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    objective_value = "Open the laptop on the table."
    source = _put(
        fixture.store,
        tmp_path,
        name="different_objective",
        schema="harness.world_fact_evidence.v1",
        value={
            "schema_version": "harness.world_fact_evidence.v1",
            "source_kind": "user_input",
            "key": "task.objective",
            "value": objective_value,
            "observed_at": _T0.isoformat(),
            "valid_until": None,
        },
    )
    state = build_world_state(
        (
            WorldFact(
                key="task.objective",
                value=objective_value,
                source_kind="user_input",
                source_artifact=source,
                observed_at=_T0,
                valid_until=None,
            ),
        ),
        as_of=_T0,
    )
    context = compile_planner_context(
        state=state,
        skills=fixture.context.skills,
        history=(),
        required_fact_keys=(),
        budget=fixture.context.budget,
    )
    planning = _replan_for_context(
        fixture,
        tmp_path / "different-objective-plan",
        context,
        request=str(fixture.planning.decision.parameters["request"]),
    )

    with pytest.raises(System2DispatchError) as raised:
        fixture.dispatcher.dispatch(planning=planning, context=context, state=state)

    assert raised.value.reason == "planner_evidence_invalid"
    assert fixture.app.calls == []


def test_dispatch_rejects_unrecomputable_derived_facts_and_redundant_history(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path / "derived")
    objective = fixture.state.facts[0].model_copy(update={"source_kind": "derived"})
    state = build_world_state((objective,), as_of=fixture.state.as_of)
    context = compile_planner_context(
        state=state,
        skills=fixture.context.skills,
        history=(),
        required_fact_keys=(),
        budget=fixture.context.budget,
    )
    planning = _replan_for_context(
        fixture,
        tmp_path / "derived-plan",
        context,
        request=str(objective.value),
    )
    with pytest.raises(System2DispatchError) as derived:
        fixture.dispatcher.dispatch(planning=planning, context=context, state=state)
    assert derived.value.reason == "planner_evidence_invalid"

    fixture = _fixture(tmp_path / "redundant")
    authority = publish_planner_history_authority(
        artifact_store=fixture.store,
        scratch_root=tmp_path / "redundant-authority",
        lineage=(fixture.state,),
        entries=(),
    )
    with pytest.raises(System2DispatchError) as redundant:
        fixture.dispatcher.dispatch(
            planning=fixture.planning,
            context=fixture.context,
            state=fixture.state,
            history_authority=authority,
        )
    assert redundant.value.reason == "planner_evidence_invalid"


def test_dispatch_reprojects_omitted_history_from_a_complete_cas_authority(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    first = fixture.dispatcher.dispatch(
        planning=fixture.planning,
        context=fixture.context,
        state=fixture.state,
    )
    assert isinstance(fixture.state, TrustedWorldState)
    current = apply_state_delta(
        fixture.state,
        first.state_delta,
        artifact_store=fixture.store,
    )
    receipt = TrustedToolReceipt.model_validate_json(
        fixture.store.resolve(first.trusted_receipt).path.read_bytes()
    )
    run_state = RunState.model_validate_json(
        fixture.store.resolve(receipt.run_state).path.read_bytes()
    )
    entry = PlannerHistoryEntry(
        run_id=run_state.run_id,
        skill_ref=receipt.skill.skill_ref,
        status=receipt.status,
        ended_at=receipt.ended_at,
        receipt=first.trusted_receipt,
        blocker_code=None,
        blocker_stage=None,
    )
    context = compile_planner_context(
        state=current,
        skills=fixture.context.skills,
        history=(entry,),
        required_fact_keys=(),
        budget=ContextBudget(max_facts=3, max_history=0, max_context_bytes=20_000),
    )
    assert context.history == ()
    assert context.omitted_history_count == 1
    planning = _replan_for_context(
        fixture,
        tmp_path / "history-replan",
        context,
        request="Place a can on top of a plate.",
    )
    fixture.app.started_at_override = current.as_of + timedelta(seconds=1)
    fixture.app.ended_at_override = current.as_of + timedelta(seconds=2)

    with pytest.raises(System2DispatchError) as missing:
        fixture.dispatcher.dispatch(planning=planning, context=context, state=current)
    assert missing.value.reason == "planner_evidence_invalid"

    authority = publish_planner_history_authority(
        artifact_store=fixture.store,
        scratch_root=tmp_path / "history-authority",
        lineage=(fixture.state, current),
        entries=(entry,),
    )
    result = fixture.dispatcher.dispatch(
        planning=planning,
        context=context,
        state=current,
        history_authority=authority,
    )

    assert result.status is RunStatus.SUCCEEDED
    assert len(fixture.app.calls) == 2

    receipt = TrustedToolReceipt.model_validate_json(
        fixture.store.resolve(result.trusted_receipt).path.read_bytes()
    )
    without_history = _put(
        fixture.store,
        tmp_path,
        name="receipt_without_required_history",
        schema="harness.trusted_tool_receipt.v1",
        value=receipt.model_copy(update={"history_authority": None}).model_dump(mode="json"),
    )
    with pytest.raises(ValueError, match="history authority"):
        verify_trusted_tool_receipt(fixture.store, without_history)


def test_dispatch_requires_every_world_fact_source_in_its_own_cas(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    source = fixture.state.facts[0].source_artifact
    fixture.store.resolve(source).path.unlink()

    with pytest.raises(System2DispatchError) as raised:
        fixture.dispatcher.dispatch(
            planning=fixture.planning,
            context=fixture.context,
            state=fixture.state,
        )

    assert raised.value.reason == "planner_evidence_invalid"
    assert fixture.app.calls == []


@pytest.mark.parametrize("attack", ["unrelated_payload", "bool_integer_alias"])
def test_dispatch_cross_binds_each_non_receipt_fact_source_envelope(
    tmp_path: Path,
    attack: str,
) -> None:
    fixture = _fixture(tmp_path)
    fact_value: object = {"expected": True}
    evidence_value: object = {"unrelated": True}
    if attack == "bool_integer_alias":
        fact_value = 1
        evidence_value = True
    source = _put(
        fixture.store,
        tmp_path,
        name=f"fact_source_{attack}",
        schema="harness.world_fact_evidence.v1",
        value={
            "schema_version": "harness.world_fact_evidence.v1",
            "source_kind": "user_input",
            "key": "scene.probe",
            "value": evidence_value,
            "observed_at": _T0.isoformat(),
            "valid_until": None,
        },
    )
    state = build_world_state(
        (
            fixture.state.facts[0],
            WorldFact(
                key="scene.probe",
                value=fact_value,
                source_kind="user_input",
                source_artifact=source,
                observed_at=_T0,
                valid_until=None,
            ),
        ),
        as_of=_T0,
    )
    context = compile_planner_context(
        state=state,
        skills=fixture.context.skills,
        history=(),
        required_fact_keys=(),
        budget=ContextBudget(max_facts=2, max_history=0, max_context_bytes=20_000),
    )
    planning = _replan_for_context(
        fixture,
        tmp_path / "fact-source-replan",
        context,
        request="Place a can on top of a plate.",
    )

    with pytest.raises(System2DispatchError) as raised:
        fixture.dispatcher.dispatch(planning=planning, context=context, state=state)

    assert raised.value.reason == "planner_evidence_invalid"
    assert fixture.app.calls == []


def test_dispatch_reparses_exact_raw_planner_response(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    raw_ref = _put(
        fixture.store,
        tmp_path,
        name="different_raw_response",
        schema="harness.planner_raw_response.v1",
        media_type="application/octet-stream",
        value={"not": "the retained decision"},
    )
    changed = replace(fixture, planning=replace(fixture.planning, raw_response_ref=raw_ref))
    planning = _rebind_receipt(changed, tmp_path, raw_response=raw_ref)

    with pytest.raises(System2DispatchError) as raised:
        fixture.dispatcher.dispatch(
            planning=planning,
            context=fixture.context,
            state=fixture.state,
        )

    assert raised.value.reason == "planner_evidence_invalid"
    assert fixture.app.calls == []


def test_dispatch_rejects_receipt_backed_state_without_its_lineage_authority(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    result = fixture.dispatcher.dispatch(
        planning=fixture.planning,
        context=fixture.context,
        state=fixture.state,
    )
    updated = apply_state_delta(
        fixture.state,
        result.state_delta,
        artifact_store=fixture.store,
    )
    facts = list(updated.facts)
    claimed = next(index for index, fact in enumerate(facts) if fact.key == "assets.catalog")
    facts[claimed] = facts[claimed].model_copy(update={"value": {"forged": True}})
    forged = build_world_state(tuple(facts), as_of=updated.as_of)
    forged_context = compile_planner_context(
        state=forged,
        skills=fixture.context.skills,
        history=(),
        required_fact_keys=(),
        budget=ContextBudget(max_facts=4, max_history=0, max_context_bytes=30_000),
    )
    planning = _replan_for_context(
        fixture,
        tmp_path / "forged-state-replan",
        forged_context,
        request="Place a can on top of a plate.",
    )

    with pytest.raises(System2DispatchError) as raised:
        fixture.dispatcher.dispatch(
            planning=planning,
            context=forged_context,
            state=forged,
        )
    assert raised.value.reason == "planner_evidence_invalid"


@pytest.mark.parametrize("blocked_preflight", [False, True])
def test_trusted_receipt_reloads_its_exact_base_state_and_planner_context(
    tmp_path: Path,
    blocked_preflight: bool,
) -> None:
    fixture = _fixture(tmp_path, blocked_preflight=blocked_preflight)
    result = fixture.dispatcher.dispatch(
        planning=fixture.planning,
        context=fixture.context,
        state=fixture.state,
    )
    receipt = TrustedToolReceipt.model_validate_json(
        fixture.store.resolve(result.trusted_receipt).path.read_bytes()
    )
    other_request = "Open the laptop on the table."
    source = _put(
        fixture.store,
        tmp_path,
        name="other_objective",
        schema="harness.world_fact_evidence.v1",
        value={
            "schema_version": "harness.world_fact_evidence.v1",
            "source_kind": "user_input",
            "key": "task.objective",
            "value": other_request,
            "observed_at": _T0.isoformat(),
            "valid_until": None,
        },
    )
    other_state = build_world_state(
        (
            WorldFact(
                key="task.objective",
                value=other_request,
                source_kind="user_input",
                source_artifact=source,
                observed_at=_T0,
                valid_until=None,
            ),
        ),
        as_of=_T0,
    )
    other_context = compile_planner_context(
        state=other_state,
        skills=fixture.context.skills,
        history=(),
        required_fact_keys=(),
        budget=fixture.context.budget,
    )
    other_state_ref = _put(
        fixture.store,
        tmp_path,
        name="other_base_state",
        schema="harness.trusted_world_state.v1",
        value=other_state.model_dump(mode="json"),
    )
    other_context_ref = _put(
        fixture.store,
        tmp_path,
        name="other_planner_context",
        schema="harness.planner_context.v1",
        value=other_context.model_dump(mode="json"),
    )
    rebound_decision = fixture.planning.decision.model_copy(
        update={
            "base_state_sha256": other_state.state_sha256,
            "context_sha256": other_context.context_sha256,
        }
    )
    decision_ref = _put(
        fixture.store,
        tmp_path,
        name="rebound_decision",
        schema="harness.planner_decision.v1",
        value=rebound_decision.model_dump(mode="json"),
    )
    raw_ref = _put(
        fixture.store,
        tmp_path,
        name="rebound_raw_response",
        schema="harness.planner_raw_response.v1",
        media_type="application/octet-stream",
        value=rebound_decision.model_dump(mode="json"),
    )
    prompt_path = tmp_path / "rebound-prompt.bin"
    prompt_path.write_bytes(build_planner_prompt(other_context))
    prompt_ref = _put_file(
        fixture.store,
        prompt_path,
        name="rebound_prompt",
        media_type="application/json",
        schema="harness.planner_prompt.v1",
    )
    rebound_digest = decision_sha256(rebound_decision)
    planner_receipt = fixture.planning.receipt.model_copy(
        update={
            "world_state_sha256": other_state.state_sha256,
            "context_sha256": other_context.context_sha256,
            "prompt": prompt_ref,
            "raw_response": raw_ref,
            "decision": decision_ref,
            "decision_sha256": rebound_digest,
        }
    )
    planner_receipt_ref = _put(
        fixture.store,
        tmp_path,
        name="rebound_planner_receipt",
        schema="harness.planner_execution_receipt.v1",
        value=planner_receipt.model_dump(mode="json"),
    )
    rebound_receipt = receipt.model_copy(
        update={
            "planner_context_sha256": other_context.context_sha256,
            "planner_context": other_context_ref,
            "planner_receipt": planner_receipt_ref,
            "planner_decision": decision_ref,
            "planner_decision_sha256": rebound_digest,
            "base_state_sha256": other_state.state_sha256,
            "base_state": other_state_ref,
        }
    )
    rebound_receipt_ref = _put(
        fixture.store,
        tmp_path,
        name="rebound_trusted_receipt",
        schema="harness.trusted_tool_receipt.v1",
        value=rebound_receipt.model_dump(mode="json"),
    )

    with pytest.raises(ValueError, match="objective"):
        verify_trusted_tool_receipt(fixture.store, rebound_receipt_ref)


def test_trusted_receipt_rejects_a_base_state_digest_without_matching_cas_bytes(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    result = fixture.dispatcher.dispatch(
        planning=fixture.planning,
        context=fixture.context,
        state=fixture.state,
    )
    receipt = TrustedToolReceipt.model_validate_json(
        fixture.store.resolve(result.trusted_receipt).path.read_bytes()
    )
    rebound = _put(
        fixture.store,
        tmp_path,
        name="receipt_with_unbound_base_digest",
        schema="harness.trusted_tool_receipt.v1",
        value=receipt.model_copy(update={"base_state_sha256": "f" * 64}).model_dump(mode="json"),
    )

    with pytest.raises(ValueError, match="base state and planner context"):
        verify_trusted_tool_receipt(fixture.store, rebound)


def test_trusted_receipt_rejects_noncanonical_json_bytes(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = fixture.dispatcher.dispatch(
        planning=fixture.planning,
        context=fixture.context,
        state=fixture.state,
    )
    receipt = json.loads(fixture.store.resolve(result.trusted_receipt).path.read_bytes())
    source = tmp_path / "pretty_trusted_receipt.json"
    source.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    noncanonical = fixture.store.put_file(
        source,
        name="pretty_trusted_receipt",
        media_type="application/json",
        schema_version="harness.trusted_tool_receipt.v1",
    )

    with pytest.raises(ValueError, match="canonical"):
        verify_trusted_tool_receipt(fixture.store, noncanonical)


@pytest.mark.parametrize(
    ("attack", "match"),
    [
        ("duplicate", "duplicate JSON key"),
        ("nonfinite", "non-finite JSON constant"),
        ("malformed", "not strict JSON"),
        ("invalid_utf8", "not strict JSON"),
        ("nonobject", "JSON object"),
    ],
)
def test_trusted_receipt_rejects_non_strict_json_documents(
    tmp_path: Path,
    attack: str,
    match: str,
) -> None:
    fixture = _fixture(tmp_path)
    result = fixture.dispatcher.dispatch(
        planning=fixture.planning,
        context=fixture.context,
        state=fixture.state,
    )
    payload = fixture.store.resolve(result.trusted_receipt).path.read_bytes()
    if attack == "duplicate":
        payload = payload.replace(
            b"{",
            b'{"schema_version":"harness.trusted_tool_receipt.v1",',
            1,
        )
    elif attack == "nonfinite":
        payload = payload.replace(b'"max_attempts":1', b'"max_attempts":NaN', 1)
    elif attack == "malformed":
        payload = b"{\n"
    elif attack == "invalid_utf8":
        payload = b"\xff\n"
    else:
        payload = b"[]\n"
    source = tmp_path / f"invalid-receipt-{attack}.json"
    source.write_bytes(payload)
    ref = fixture.store.put_file(
        source,
        name=f"invalid_receipt_{attack}",
        media_type="application/json",
        schema_version="harness.trusted_tool_receipt.v1",
    )

    with pytest.raises(ValueError, match=match):
        verify_trusted_tool_receipt(fixture.store, ref)


def test_trusted_receipt_rejects_redundant_initial_history_authority(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = fixture.dispatcher.dispatch(
        planning=fixture.planning,
        context=fixture.context,
        state=fixture.state,
    )
    receipt = TrustedToolReceipt.model_validate_json(
        fixture.store.resolve(result.trusted_receipt).path.read_bytes()
    )
    authority = publish_planner_history_authority(
        artifact_store=fixture.store,
        scratch_root=tmp_path / "redundant-receipt-history",
        lineage=(fixture.state,),
        entries=(),
    )
    rebound = _put(
        fixture.store,
        tmp_path,
        name="receipt_with_redundant_history",
        schema="harness.trusted_tool_receipt.v1",
        value=receipt.model_copy(update={"history_authority": authority}).model_dump(mode="json"),
    )

    with pytest.raises(ValueError, match="must not carry"):
        verify_trusted_tool_receipt(fixture.store, rebound)


@pytest.mark.parametrize(
    "attack",
    [
        "qualification",
        "unsupported_skill",
        "planner_binding",
        "raw_decision",
        "prompt",
        "run_state",
        "noncanonical_run_state",
        "attempt",
        "artifact_closure",
        "invocation",
        "claims",
    ],
)
def test_trusted_receipt_rebuilds_each_cross_document_binding(
    tmp_path: Path,
    attack: str,
) -> None:
    fixture = _fixture(tmp_path)
    result = fixture.dispatcher.dispatch(
        planning=fixture.planning,
        context=fixture.context,
        state=fixture.state,
    )
    receipt = TrustedToolReceipt.model_validate_json(
        fixture.store.resolve(result.trusted_receipt).path.read_bytes()
    )
    payload = receipt.model_dump(mode="python")
    if attack == "qualification":
        payload["skill"] = receipt.skill.model_copy(update={"implementation_sha256": "f" * 64})
    elif attack == "unsupported_skill":
        payload["skill"] = receipt.skill.model_copy(update={"skill_ref": "text2env.validate@1.0.0"})
    elif attack == "planner_binding":
        payload["planner_call_id"] = UUID("00000000-0000-4000-8000-000000000999")
    elif attack in {"raw_decision", "prompt"}:
        planner_receipt = fixture.planning.receipt
        if attack == "raw_decision":
            decision_payload = fixture.planning.decision.model_dump(mode="json")
            assert isinstance(decision_payload["parameters"], dict)
            decision_payload["parameters"] = {
                **decision_payload["parameters"],
                "seed": 999,
            }
            changed_ref = _put(
                fixture.store,
                tmp_path,
                name="changed_raw_decision",
                schema="harness.planner_raw_response.v1",
                media_type="application/octet-stream",
                value=decision_payload,
            )
            planner_receipt = planner_receipt.model_copy(update={"raw_response": changed_ref})
        else:
            changed_ref = _put(
                fixture.store,
                tmp_path,
                name="changed_prompt",
                schema="harness.planner_prompt.v1",
                value={"schema_version": "harness.planner_prompt.v1", "changed": True},
            )
            planner_receipt = planner_receipt.model_copy(update={"prompt": changed_ref})
        payload["planner_receipt"] = _put(
            fixture.store,
            tmp_path,
            name=f"planner_receipt_{attack}",
            schema="harness.planner_execution_receipt.v1",
            value=planner_receipt.model_dump(mode="json"),
        )
    elif attack == "run_state":
        payload["started_at"] = receipt.started_at + timedelta(microseconds=1)
    elif attack == "noncanonical_run_state":
        run_state_payload = json.loads(fixture.store.resolve(receipt.run_state).path.read_bytes())
        source = tmp_path / "pretty_run_state.json"
        source.write_text(json.dumps(run_state_payload, indent=2) + "\n", encoding="utf-8")
        payload["run_state"] = fixture.store.put_file(
            source,
            name="pretty_run_state",
            media_type="application/json",
            schema_version="harness.run_state.v1",
        )
    elif attack == "attempt":
        payload["skill"] = receipt.skill.model_copy(update={"max_attempts": 2})
    elif attack == "artifact_closure":
        run_state = RunState.model_validate_json(
            fixture.store.resolve(receipt.run_state).path.read_bytes()
        )
        omitted = fixture.app.output.scene_spec
        run_state = run_state.model_copy(
            update={"artifacts": tuple(ref for ref in run_state.artifacts if ref != omitted)}
        )
        payload["run_state"] = _put(
            fixture.store,
            tmp_path,
            name="run_state_missing_output_member",
            schema="harness.run_state.v1",
            value=run_state.model_dump(mode="json"),
        )
        payload["supporting_artifacts"] = tuple(
            ref for ref in receipt.supporting_artifacts if ref != omitted
        )
    elif attack == "invocation":
        assert receipt.invocation is not None
        invocation = Invocation.model_validate_json(
            fixture.store.resolve(receipt.invocation).path.read_bytes()
        )
        effective = dict(invocation.effective_parameters)
        effective["seed"] = 999
        invocation = invocation.model_copy(update={"effective_parameters": effective})
        payload["invocation"] = _put(
            fixture.store,
            tmp_path,
            name="changed_invocation",
            schema="harness.skill_invocation.v1",
            value=invocation.model_dump(mode="json"),
        )
    else:
        first, *rest = receipt.derived_fact_claims
        payload["derived_fact_claims"] = (
            first.model_copy(update={"value": {"forged": True}}),
            *rest,
        )
    changed_receipt = _put(
        fixture.store,
        tmp_path,
        name=f"trusted_receipt_{attack}",
        schema="harness.trusted_tool_receipt.v1",
        value=TrustedToolReceipt.model_validate(payload).model_dump(mode="json"),
    )

    with pytest.raises(ValueError):
        verify_trusted_tool_receipt(fixture.store, changed_receipt)


def test_trusted_receipt_verifier_requires_exact_store_and_preflight_shape(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, blocked_preflight=True)
    result = fixture.dispatcher.dispatch(
        planning=fixture.planning,
        context=fixture.context,
        state=fixture.state,
    )
    with pytest.raises(TypeError, match="LocalArtifactStore"):
        verify_trusted_tool_receipt(object(), result.trusted_receipt)  # type: ignore[arg-type]
    receipt = TrustedToolReceipt.model_validate_json(
        fixture.store.resolve(result.trusted_receipt).path.read_bytes()
    )
    run_state = RunState.model_validate_json(
        fixture.store.resolve(receipt.run_state).path.read_bytes()
    )
    run_state = run_state.model_copy(update={"max_attempts": 1})
    payload = receipt.model_dump(mode="python")
    payload["run_state"] = _put(
        fixture.store,
        tmp_path,
        name="preflight_run_state_with_retry_budget",
        schema="harness.run_state.v1",
        value=run_state.model_dump(mode="json"),
    )
    changed_receipt = _put(
        fixture.store,
        tmp_path,
        name="preflight_receipt_with_invocation",
        schema="harness.trusted_tool_receipt.v1",
        value=TrustedToolReceipt.model_validate(payload).model_dump(mode="json"),
    )

    with pytest.raises(ValueError, match="preflight"):
        verify_trusted_tool_receipt(fixture.store, changed_receipt)


def test_dispatch_preserves_preflight_failure_without_fabricating_invocation(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, blocked_preflight=True)

    result = fixture.dispatcher.dispatch(
        planning=fixture.planning,
        context=fixture.context,
        state=fixture.state,
    )

    assert result.status is RunStatus.BLOCKED
    assert result.invocation is None
    assert result.blocker is not None
    assert result.state_delta is None
    assert result.typed_output is None
    receipt = TrustedToolReceipt.model_validate_json(
        fixture.store.resolve(result.trusted_receipt).path.read_bytes()
    )
    assert receipt.invocation is None
    assert receipt.blocker == result.blocker


@pytest.mark.parametrize(
    "attack,reason",
    [
        ("world_state", "planner_evidence_invalid"),
        ("decision", "planner_evidence_invalid"),
        ("descriptor", "skill_identity_mismatch"),
        ("invocation", "skill_execution_invalid"),
        ("output_artifact", "skill_execution_invalid"),
    ],
)
def test_dispatch_rechecks_planner_skill_and_execution_evidence_before_return(
    tmp_path: Path,
    attack: str,
    reason: str,
) -> None:
    fixture = _fixture(tmp_path)
    state = fixture.state
    if attack == "world_state":
        state = state.model_copy(update={"state_sha256": "f" * 64})
    elif attack == "decision":
        object.__setattr__(fixture.planning.decision, "summary", " changed")
    elif attack == "descriptor":
        object.__setattr__(fixture.app.descriptor, "implementation_sha256", "f" * 64)
    elif attack == "invocation":
        fixture.app.mutate_invocation = True
    else:
        fixture.store.resolve(fixture.app.support).path.unlink()

    with pytest.raises(System2DispatchError) as raised:
        fixture.dispatcher.dispatch(
            planning=fixture.planning,
            context=fixture.context,
            state=state,
        )

    assert raised.value.reason == reason
    if attack in {"world_state", "decision", "descriptor"}:
        assert fixture.app.calls == []


def test_dispatch_rejects_non_invoke_actions_without_calling_an_application(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    object.__setattr__(fixture.planning.decision, "action", "stop")

    with pytest.raises(System2DispatchError) as raised:
        fixture.dispatcher.dispatch(
            planning=fixture.planning,
            context=fixture.context,
            state=fixture.state,
        )

    assert raised.value.reason == "planner_evidence_invalid"
    assert fixture.app.calls == []


def test_dispatch_rejects_a_fully_bound_non_invoke_planner_result(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    decision = {
        **fixture.planning.decision.model_dump(mode="json"),
        "action": "stop",
        "skill_ref": None,
        "parameters": None,
        "stop_reason": "No further Skill call is justified.",
    }
    planning = System2Planner(
        provider=_Provider(_canonical(decision)),
        publisher=LocalPlannerArtifactPublisher(fixture.store, tmp_path / "stop-planner"),
        clock=_Clock(_T0 + timedelta(milliseconds=200)),
        uuid_factory=lambda: UUID("00000000-0000-4000-8000-000000000654"),
    ).plan(fixture.context)

    with pytest.raises(System2DispatchError) as raised:
        fixture.dispatcher.dispatch(
            planning=planning,
            context=fixture.context,
            state=fixture.state,
        )

    assert raised.value.reason == "planner_evidence_invalid"
    assert fixture.app.calls == []


@pytest.mark.parametrize(
    "attack",
    [
        "local_time",
        "time_order",
        "planner_receipt_uri",
        "planner_receipt_schema",
        "planner_decision_schema",
        "base_state_schema",
        "planner_context_schema",
        "history_media",
        "invocation_schema",
        "run_state_schema",
        "support_uri",
        "support_order",
        "support_duplicate",
        "claim_order",
        "claim_duplicate",
        "success_invocation",
        "success_digest",
        "success_output",
        "success_blocker",
        "blocked_output",
        "blocked_blocker",
        "invocation_pair",
        "failed_claim",
        "running",
    ],
)
def test_trusted_tool_receipt_rejects_noncanonical_or_contradictory_envelopes(
    tmp_path: Path,
    attack: str,
) -> None:
    fixture = _fixture(tmp_path)
    result = fixture.dispatcher.dispatch(
        planning=fixture.planning,
        context=fixture.context,
        state=fixture.state,
    )
    receipt = TrustedToolReceipt.model_validate_json(
        fixture.store.resolve(result.trusted_receipt).path.read_bytes()
    )
    payload = receipt.model_dump(mode="python")
    if attack == "local_time":
        payload["started_at"] = datetime(
            2026,
            8,
            31,
            18,
            tzinfo=timezone(timedelta(hours=8)),
        )
    elif attack == "time_order":
        payload["ended_at"] = payload["started_at"] - timedelta(microseconds=1)
    elif attack == "planner_receipt_uri":
        payload["planner_receipt"] = receipt.planner_receipt.model_copy(
            update={"uri": "file:///tmp/planner-receipt.json"}
        )
    elif attack == "planner_receipt_schema":
        payload["planner_receipt"] = receipt.planner_receipt.model_copy(
            update={"schema_version": "wrong.v1"}
        )
    elif attack == "planner_decision_schema":
        payload["planner_decision"] = receipt.planner_decision.model_copy(
            update={"schema_version": "wrong.v1"}
        )
    elif attack == "base_state_schema":
        payload["base_state"] = receipt.base_state.model_copy(update={"schema_version": "wrong.v1"})
    elif attack == "planner_context_schema":
        payload["planner_context"] = receipt.planner_context.model_copy(
            update={"schema_version": "wrong.v1"}
        )
    elif attack == "history_media":
        payload["history_authority"] = receipt.planner_decision.model_copy(
            update={
                "schema_version": "harness.planner_history_authority.v1",
                "media_type": "text/plain",
            }
        )
    elif attack == "invocation_schema":
        assert receipt.invocation is not None
        payload["invocation"] = receipt.invocation.model_copy(update={"schema_version": "wrong.v1"})
    elif attack == "run_state_schema":
        payload["run_state"] = receipt.run_state.model_copy(update={"schema_version": "wrong.v1"})
    elif attack == "support_uri":
        first = receipt.supporting_artifacts[0]
        payload["supporting_artifacts"] = (
            first.model_copy(update={"uri": "file:///tmp/support.json"}),
            *receipt.supporting_artifacts[1:],
        )
    elif attack == "support_order":
        payload["supporting_artifacts"] = tuple(reversed(receipt.supporting_artifacts))
    elif attack == "support_duplicate":
        payload["supporting_artifacts"] = (
            *receipt.supporting_artifacts,
            receipt.supporting_artifacts[-1],
        )
    elif attack == "claim_order":
        payload["derived_fact_claims"] = tuple(reversed(receipt.derived_fact_claims))
    elif attack == "claim_duplicate":
        payload["derived_fact_claims"] = (
            *receipt.derived_fact_claims,
            receipt.derived_fact_claims[-1],
        )
    elif attack == "success_invocation":
        payload["invocation"] = None
        payload["invocation_digest"] = None
    elif attack == "success_digest":
        payload["invocation_digest"] = None
    elif attack == "success_output":
        payload["typed_output"] = None
    elif attack == "success_blocker":
        payload["blocker"] = Blocker(
            code="HARN_INTERNAL",
            message="contradiction",
            stage="dispatch",
            retryable=False,
            details={},
            unknowns=(),
            artifact_refs=(),
        )
    elif attack == "running":
        payload["status"] = RunStatus.RUNNING
    else:
        blocker = Blocker(
            code="HARN_INTERNAL",
            message="failed",
            stage="dispatch",
            retryable=False,
            details={},
            unknowns=(),
            artifact_refs=(),
        )
        payload["status"] = RunStatus.BLOCKED
        payload["derived_fact_claims"] = ()
        payload["typed_output"] = None
        payload["blocker"] = blocker
        if attack == "blocked_output":
            payload["typed_output"] = {"wrong": True}
        elif attack == "blocked_blocker":
            payload["blocker"] = None
        elif attack == "invocation_pair":
            payload["invocation"] = None
        else:
            payload["derived_fact_claims"] = receipt.derived_fact_claims

    with pytest.raises(ValidationError):
        TrustedToolReceipt.model_validate(payload)


def test_dispatcher_constructor_rejects_ambiguous_or_untrusted_applications(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    wrapped = fixture.adapter

    with pytest.raises(TypeError, match="LocalArtifactStore"):
        System2Dispatcher(  # type: ignore[arg-type]
            artifact_store=object(),
            scratch_root=tmp_path / "scratch",
            applications=(wrapped,),
        )
    with pytest.raises(ValueError, match="at least one"):
        System2Dispatcher(
            artifact_store=fixture.store,
            scratch_root=tmp_path / "scratch",
            applications=(),
        )
    foreign = CompileSystem2Application._for_testing(
        backend=fixture.app,
        artifact_store=LocalArtifactStore(tmp_path / "foreign-cas"),
    )
    with pytest.raises(ValueError, match="dispatcher CAS"):
        System2Dispatcher(
            artifact_store=fixture.store,
            scratch_root=tmp_path / "scratch",
            applications=(foreign,),
        )
    with pytest.raises(ValueError, match="duplicate"):
        System2Dispatcher(
            artifact_store=fixture.store,
            scratch_root=tmp_path / "scratch",
            applications=(wrapped, wrapped),
        )


def test_compile_application_adapter_rejects_wrong_skill_input_and_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    wrapped = fixture.adapter

    with pytest.raises(TypeError, match="Text2EnvCompileInput"):
        wrapped.invoke(fixture.app.output)
    typed_input = Text2EnvCompileInput.model_validate(fixture.planning.decision.parameters)
    with pytest.raises(TypeError, match="Text2EnvCompileOutput"):
        wrapped.derived_fact_claims(typed_input, fixture.app.calls, _compile_supporting(fixture))
    with pytest.raises(TypeError, match="Text2EnvCompileInput"):
        wrapped.derived_fact_claims(
            fixture.app.output,
            fixture.app.output,
            _compile_supporting(fixture),
        )
    with pytest.raises(TypeError, match="LocalArtifactStore"):
        CompileSystem2Application._for_testing(  # type: ignore[arg-type]
            backend=fixture.app,
            artifact_store=object(),
        )
    monkeypatch.setattr(fixture.app, "invoke_typed", lambda _parameters: object())
    with pytest.raises(TypeError, match="RunState"):
        wrapped.invoke(typed_input)
    monkeypatch.setattr(fixture.app, "invocation", lambda _run_id: object())
    with pytest.raises(TypeError, match="Invocation"):
        wrapped.invocation(UUID("00000000-0000-4000-8000-000000000456"))

    @dataclass
    class EmptyBackend:
        artifact_root: Path

        @property
        def skills(self) -> tuple[object, ...]:
            return ()

    empty = CompileSystem2Application._for_testing(
        backend=EmptyBackend(fixture.store.root),
        artifact_store=fixture.store,
    )
    with pytest.raises(ValueError, match="only text2env.compile"):
        _ = empty.descriptor
    fixture.app.descriptor = fixture.app.descriptor.model_copy(
        update={"skill_id": "text2env.validate"}
    )
    with pytest.raises(ValueError, match="only text2env.compile"):
        _ = wrapped.descriptor


def test_compile_adapter_rejects_a_protocol_lookalike_instead_of_sealing_it(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    with pytest.raises(TypeError, match="CompileApplication"):
        CompileSystem2Application(fixture.app)  # type: ignore[arg-type]


def test_compile_adapter_uses_the_live_production_application_authorities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.self_improving.harness.test_application import (
        _empty_catalog,
        _qualification_bundle,
        _settings,
    )

    bundle = _qualification_bundle(tmp_path)
    monkeypatch.setattr(application_module, "_COMPILE_QUALIFICATION_ROOT", bundle)
    settings = _settings(tmp_path)
    catalog_path = _empty_catalog(
        settings.external_catalog_roots[0] / "empty.json",
        settings.allowed_asset_roots[0] / "objects",
    )
    application = create_compile_application(settings)
    adapter = CompileSystem2Application(application)
    state = application.compile(
        request="Place a purple hexagonal pedestal on the table.",
        seed=77,
        asset_catalog_path=catalog_path,
        generate_missing_assets=True,
    )
    invocation = application.invocation(state.run_id)
    assert invocation is not None
    typed_input = Text2EnvCompileInput.model_validate(invocation.effective_parameters)
    typed_output = Text2EnvCompileOutput.model_validate(state.output)

    assert adapter.artifact_root == application.artifact_root
    assert adapter.descriptor == application.skills[0]
    assert adapter.planner_skill_card.skill_ref == "text2env.compile@1.0.0"
    assert tuple(
        claim.key
        for claim in adapter.derived_fact_claims(
            typed_input,
            typed_output,
            state.artifacts,
        )
    ) == ("assets.catalog", "environment.package")


def test_compile_application_rejects_an_exact_shell_with_fake_authorities(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    with pytest.raises(CompileApplicationConfigurationError, match="production assembly"):
        CompileApplication(
            state_root=tmp_path / "fake-state",
            artifact_store=fixture.store,
            event_journal=object(),  # type: ignore[arg-type]
            run_store=object(),  # type: ignore[arg-type]
            registry=object(),  # type: ignore[arg-type]
            external_catalog_roots=(),
            asset_library_root=tmp_path / "fake-assets",
        )


@pytest.mark.parametrize(
    "drift",
    [
        "changed_bytes",
        "invalid_json",
        "not_object",
        "binding",
        "files_type",
        "bad_record",
        "duplicate_record",
        "extra_key",
        "record_identity",
        "member_binding",
    ],
)
def test_compile_fact_projection_rechecks_manifest_shape_and_bindings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    fixture = _fixture(tmp_path)
    wrapped = fixture.adapter
    typed_input = Text2EnvCompileInput.model_validate(fixture.planning.decision.parameters)
    original_ref = fixture.app.output.environment_package.package_manifest
    original = json.loads(fixture.store.resolve(original_ref).path.read_text(encoding="utf-8"))
    if drift == "changed_bytes":
        changed = tmp_path / "changed-manifest.json"
        changed.write_bytes(b"{}\n")
        original_resolve = fixture.store.resolve

        def changed_resolve(ref: ArtifactRef) -> ResolvedArtifact:
            if ref == original_ref:
                return ResolvedArtifact(ref=ref, path=changed)
            return original_resolve(ref)

        monkeypatch.setattr(fixture.store, "resolve", changed_resolve)
        output = fixture.app.output
    else:
        value: object = original
        if drift == "invalid_json":
            path = tmp_path / "invalid-manifest.json"
            path.write_bytes(b"{\n")
            manifest_ref = fixture.store.put_file(
                path,
                name="package_manifest_invalid",
                media_type="application/json",
                schema_version="robotwin.generated_scene_package.v1",
            )
            output = _compile_output_with_manifest(fixture, manifest_ref)
        else:
            if drift == "not_object":
                value = []
            elif drift == "binding":
                value = {**original, "seed": 999}
            elif drift == "files_type":
                value = {**original, "files": {}}
            elif drift == "bad_record":
                value = {**original, "files": [1]}
            elif drift == "duplicate_record":
                value = {**original, "files": [original["files"][0]] * 2}
            elif drift == "extra_key":
                value = {**original, "forged": True}
            elif drift == "record_identity":
                records = [dict(item) for item in original["files"]]
                records[0]["sha256"] = "f" * 64
                value = {**original, "files": records}
            else:
                value = {**original, "files": [original["files"][0]]}
            manifest_ref = _put(
                fixture.store,
                tmp_path,
                name=f"package_manifest_{drift}",
                schema="robotwin.generated_scene_package.v1",
                value=value,
            )
            output = _compile_output_with_manifest(fixture, manifest_ref)

    with pytest.raises(ValueError):
        wrapped.derived_fact_claims(typed_input, output, _compile_supporting(fixture))


@pytest.mark.parametrize(
    "attack",
    [
        "contradictory_status",
        "wrong_counts",
        "duplicate_check",
        "unexpected_check_shape",
        "unexpected_report_shape",
        "invalid_check_status",
    ],
)
def test_compile_fact_projection_recomputes_static_validation_summary(
    tmp_path: Path,
    attack: str,
) -> None:
    fixture = _fixture(tmp_path)
    typed_input = Text2EnvCompileInput.model_validate(fixture.planning.decision.parameters)
    resolved = fixture.app.output.resolved_scene
    resolved_payload = json.loads(fixture.store.resolve(resolved).path.read_text(encoding="utf-8"))
    check: dict[str, object] = {
        "name": "runtime_evidence",
        "status": "not_run",
        "evidence": {"reason": "compile_only"},
    }
    validation: dict[str, object] = {
        "schema_version": "robotwin.scene_validation.v1",
        "scene_id": resolved_payload["scene_id"],
        "resolved_scene_sha256": (fixture.app.output.environment_package.resolved_scene_sha256),
        "status": "incomplete",
        "fail_count": 0,
        "not_run_count": 1,
        "checks": [check],
    }
    if attack == "contradictory_status":
        validation["status"] = "pass"
    elif attack == "wrong_counts":
        validation["not_run_count"] = 0
    elif attack == "duplicate_check":
        validation["checks"] = [check, check]
        validation["not_run_count"] = 2
    elif attack == "unexpected_check_shape":
        validation["checks"] = [{**check, "forged": True}]
    elif attack == "unexpected_report_shape":
        validation["forged"] = True
    else:
        validation["checks"] = [{**check, "status": "unknown"}]
    validation_ref = _put(
        fixture.store,
        tmp_path,
        name=f"static_validation_{attack}",
        schema="robotwin.scene_validation.v1",
        value=validation,
    )
    output = fixture.app.output.model_copy(update={"static_validation": validation_ref})
    supporting = tuple(
        validation_ref if ref == fixture.app.output.static_validation else ref
        for ref in _compile_supporting(fixture)
    )

    with pytest.raises(ValueError, match="static validation"):
        fixture.adapter.derived_fact_claims(typed_input, output, supporting)


def test_compile_fact_projection_requires_the_official_static_check_set(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    typed_input = Text2EnvCompileInput.model_validate(fixture.planning.decision.parameters)
    resolved = json.loads(
        fixture.store.resolve(fixture.app.output.resolved_scene).path.read_text(encoding="utf-8")
    )
    validation_ref = _put(
        fixture.store,
        tmp_path,
        name="forged_static_validation",
        schema="robotwin.scene_validation.v1",
        value={
            "schema_version": "robotwin.scene_validation.v1",
            "scene_id": resolved["scene_id"],
            "resolved_scene_sha256": (fixture.app.output.environment_package.resolved_scene_sha256),
            "status": "pass",
            "fail_count": 0,
            "not_run_count": 0,
            "checks": [
                {
                    "name": "forged_without_validation",
                    "status": "pass",
                    "evidence": {"self_asserted": True},
                }
            ],
        },
    )
    output = fixture.app.output.model_copy(update={"static_validation": validation_ref})
    supporting = tuple(
        validation_ref if ref == fixture.app.output.static_validation else ref
        for ref in _compile_supporting(fixture)
    )

    with pytest.raises(ValueError, match="official static validator"):
        fixture.adapter.derived_fact_claims(typed_input, output, supporting)


@pytest.mark.parametrize(
    "attack",
    [
        "duplicate_json_key",
        "nonfinite_json",
        "wrong_json_media_type",
        "missing_package_member",
        "invalid_scene_document",
        "wrong_skill_identity",
    ],
)
def test_compile_fact_projection_rejects_document_and_closure_forgery(
    tmp_path: Path,
    attack: str,
) -> None:
    fixture = _fixture(tmp_path)
    typed_input = Text2EnvCompileInput.model_validate(fixture.planning.decision.parameters)
    output = fixture.app.output
    supporting = _compile_supporting(fixture)
    if attack in {"duplicate_json_key", "nonfinite_json"}:
        raw = tmp_path / f"static-{attack}.json"
        raw.write_bytes(
            b'{"schema_version":"robotwin.scene_validation.v1",'
            + (
                b'"schema_version":"robotwin.scene_validation.v1"}'
                if attack == "duplicate_json_key"
                else b'"forged":NaN}'
            )
        )
        ref = _put_file(
            fixture.store,
            raw,
            media_type="application/json",
            schema="robotwin.scene_validation.v1",
        )
        output = output.model_copy(update={"static_validation": ref})
        supporting = tuple(
            ref if item == fixture.app.output.static_validation else item for item in supporting
        )
    elif attack == "wrong_json_media_type":
        ref = output.scene_spec.model_copy(update={"media_type": "text/plain"})
        output = output.model_copy(update={"scene_spec": ref})
    elif attack == "missing_package_member":
        supporting = tuple(item for item in supporting if item != fixture.app.package_members[0])
    elif attack == "invalid_scene_document":
        ref = _put(
            fixture.store,
            tmp_path,
            name="invalid_scene",
            schema="robotwin.scene_spec.v1",
            value={"invalid": True},
        )
        output = output.model_copy(update={"scene_spec": ref})
        supporting = tuple(
            ref if item == fixture.app.output.scene_spec else item for item in supporting
        )
    else:
        fixture.app.descriptor = fixture.app.descriptor.model_copy(update={"version": "9.9.9"})

    with pytest.raises(ValueError):
        fixture.adapter.derived_fact_claims(typed_input, output, supporting)


def test_dispatcher_rejects_qualification_report_not_bound_to_implementation(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    fixture.app.descriptor = fixture.app.descriptor.model_copy(
        update={"implementation_sha256": "7" * 64}
    )

    with pytest.raises(ValueError, match="qualification report"):
        System2Dispatcher(
            artifact_store=fixture.store,
            scratch_root=tmp_path / "qualification-drift",
            applications=(fixture.adapter,),
        )


def test_dispatcher_accepts_a_strict_v2_descriptor_at_the_generic_boundary(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    old = fixture.app.descriptor
    fixture.app.descriptor = SkillDescriptorV2(
        skill_id=old.skill_id,
        version=old.version,
        mcp_tool_name=old.mcp_tool_name,
        input_schema=old.input_schema,
        output_schema=old.output_schema,
        implementation_name=old.implementation_name,
        implementation_version=old.implementation_version,
        implementation_sha256=old.implementation_sha256,
        reproducibility=ExecutionReproducibility.CONTENT_BITWISE_DETERMINISTIC,
        max_attempts=old.max_attempts,
        qualification_artifact=old.qualification_artifact,
    )

    dispatcher = System2Dispatcher(
        artifact_store=fixture.store,
        scratch_root=tmp_path / "v2-scratch",
        applications=(fixture.adapter,),
    )

    assert dispatcher is not None


@pytest.mark.parametrize(
    "attack",
    [
        "wrong_type",
        "context_state",
        "prompt",
        "decision_artifact",
        "decision_digest",
        "receipt_artifact",
        "receipt_binding",
        "event_seq",
        "event_call",
        "event_time",
        "event_empty",
        "event_stage",
        "event_artifacts",
        "event_history",
        "observer",
        "decision_state",
        "decision_context",
        "decision_stop",
    ],
)
def test_dispatch_rejects_each_planner_evidence_drift_before_skill_execution(
    tmp_path: Path,
    attack: str,
) -> None:
    fixture = _fixture(tmp_path)
    planning = fixture.planning
    state = fixture.state
    if attack == "wrong_type":
        planning = object()
    elif attack == "context_state":
        extra = WorldFact(
            key="scene.extra",
            value=True,
            source_kind="user_input",
            source_artifact=fixture.state.facts[0].source_artifact,
            observed_at=_T0,
            valid_until=None,
        )
        state = build_world_state((*fixture.state.facts, extra), as_of=_T0)
    elif attack == "prompt":
        ref = _put(
            fixture.store,
            tmp_path,
            name="wrong_prompt",
            schema="harness.planner_prompt.v1",
            value={"wrong": True},
        )
        planning = replace(planning, prompt_ref=ref)
    elif attack == "decision_artifact":
        ref = _put(
            fixture.store,
            tmp_path,
            name="wrong_decision",
            schema="harness.planner_decision.v1",
            value={"wrong": True},
        )
        planning = replace(planning, decision_ref=ref)
    elif attack == "decision_digest":
        planning = replace(planning, decision_sha256="f" * 64)
    elif attack == "receipt_artifact":
        ref = _put(
            fixture.store,
            tmp_path,
            name="wrong_receipt",
            schema="harness.planner_execution_receipt.v1",
            value={"wrong": True},
        )
        planning = replace(planning, receipt_ref=ref)
    elif attack == "receipt_binding":
        planning = _rebind_receipt(
            fixture,
            tmp_path,
            call_id=UUID("00000000-0000-4000-8000-000000000999"),
        )
    elif attack == "event_seq":
        events = list(planning.events)
        events[0] = events[0].model_copy(update={"seq": 2})
        planning = replace(planning, events=tuple(events))
    elif attack == "event_call":
        events = list(planning.events)
        events[0] = events[0].model_copy(
            update={"call_id": UUID("00000000-0000-4000-8000-000000000999")}
        )
        planning = replace(planning, events=tuple(events))
    elif attack == "event_time":
        events = list(planning.events)
        events[1] = events[1].model_copy(
            update={"timestamp": events[0].timestamp - timedelta(microseconds=1)}
        )
        planning = replace(planning, events=tuple(events))
    elif attack == "event_empty":
        planning = replace(planning, events=())
    elif attack == "event_stage":
        events = list(planning.events)
        events[-1] = events[-1].model_copy(update={"stage": "receipt.missing"})
        planning = replace(planning, events=tuple(events))
    elif attack == "event_artifacts":
        events = list(planning.events)
        events[-1] = events[-1].model_copy(update={"artifact_refs": ()})
        planning = replace(planning, events=tuple(events))
    elif attack == "event_history":
        events = list(planning.events)
        events[0] = events[0].model_copy(update={"stage": "context.changed"})
        planning = replace(planning, events=tuple(events))
    elif attack == "observer":
        planning = replace(planning, observer_failure_stages=("context.validated",))
    elif attack == "decision_state":
        planning = _rebind_decision(
            fixture,
            tmp_path,
            base_state_sha256="f" * 64,
        )
    elif attack == "decision_context":
        planning = _rebind_decision(
            fixture,
            tmp_path,
            context_sha256="f" * 64,
        )
    else:
        planning = _rebind_decision(
            fixture,
            tmp_path,
            action="stop",
            skill_ref=None,
            parameters=None,
            stop_reason="No executable action is justified.",
        )

    with pytest.raises(System2DispatchError) as raised:
        fixture.dispatcher.dispatch(
            planning=planning,  # type: ignore[arg-type]
            context=fixture.context,
            state=state,
        )

    assert raised.value.reason == "planner_evidence_invalid"
    assert fixture.app.calls == []


def test_dispatch_reports_unavailable_skill_and_application_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    fixture.dispatcher._applications.clear()
    with pytest.raises(System2DispatchError) as unavailable:
        fixture.dispatcher.dispatch(
            planning=fixture.planning,
            context=fixture.context,
            state=fixture.state,
        )
    assert unavailable.value.reason == "skill_not_available"

    fixture = _fixture(tmp_path / "raised")

    def fail(_parameters: Text2EnvCompileInput) -> RunState:
        raise OSError("injected application failure")

    monkeypatch.setattr(fixture.app, "invoke_typed", fail)
    with pytest.raises(System2DispatchError) as failed:
        fixture.dispatcher.dispatch(
            planning=fixture.planning,
            context=fixture.context,
            state=fixture.state,
        )
    assert failed.value.reason == "skill_execution_failed"
    assert failed.value.cause_type == "OSError"


@dataclass
class _DirectApplication:
    artifact_root: Path
    descriptor: object

    def invoke(self, _parameters: object) -> RunState:
        raise AssertionError("not called")

    def invocation(self, _run_id: UUID) -> None:
        return None

    def derived_fact_claims(
        self,
        _typed_input: object,
        _output: object,
        _supporting: tuple[ArtifactRef, ...],
    ) -> tuple[object, ...]:
        return ()


def test_dispatcher_rejects_unknown_descriptor_and_mismatched_qualification(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    with pytest.raises(TypeError, match="CompileSystem2Application"):
        System2Dispatcher(
            artifact_store=fixture.store,
            scratch_root=tmp_path / "protocol-lookalike",
            applications=(
                _DirectApplication(fixture.store.root, fixture.app.descriptor),  # type: ignore[arg-type]
            ),
        )
    fixture.app.descriptor = object()
    with pytest.raises(TypeError, match="SkillDescriptor"):
        System2Dispatcher(
            artifact_store=fixture.store,
            scratch_root=tmp_path / "invalid-descriptor",
            applications=(fixture.adapter,),
        )

    fixture = _fixture(tmp_path / "wrong-qualification-fixture")

    report = _put(
        fixture.store,
        tmp_path / "wrong-qualification-fixture",
        name="wrong_qualification_report",
        schema="harness.skill_qualification_report.v1",
        value={"status": "pass"},
    )
    wrong_qualification = _put(
        fixture.store,
        tmp_path / "wrong-qualification-fixture",
        name="wrong_qualification",
        schema="harness.skill_qualification.v1",
        value={
            "skill_ref": "text2env.validate@1.0.0",
            "status": "pass",
            "deterministic_case_id": "wrong-skill",
            "regression_command": "pytest wrong",
            "report_sha256": report.sha256,
        },
    )
    fixture.app.descriptor = fixture.app.descriptor.model_copy(
        update={"qualification_artifact": wrong_qualification}
    )
    with pytest.raises(ValueError, match="qualification does not match"):
        System2Dispatcher(
            artifact_store=fixture.store,
            scratch_root=tmp_path / "wrong-qualification",
            applications=(fixture.adapter,),
        )


def test_dispatch_rechecks_planner_card_against_current_descriptor(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    changed = fixture.app.descriptor.model_copy(update={"max_attempts": 2})
    fixture.app.descriptor = changed
    fixture.dispatcher._descriptors["text2env.compile@1.0.0"] = changed

    with pytest.raises(System2DispatchError) as raised:
        fixture.dispatcher.dispatch(
            planning=fixture.planning,
            context=fixture.context,
            state=fixture.state,
        )

    assert raised.value.reason == "skill_identity_mismatch"
    assert fixture.app.calls == []


def test_dispatch_rejects_compile_package_not_bound_to_input_and_output_refs(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    package = fixture.app.output.environment_package.model_copy(
        update={
            "package_id": "9" * 64,
            "resolved_scene_sha256": "9" * 64,
            "scene_spec_sha256": "8" * 64,
            "producer_skill_ref": "text2env.compile@9.9.9",
            "seed": 999,
        }
    )
    fixture.app.output = fixture.app.output.model_copy(update={"environment_package": package})

    with pytest.raises(System2DispatchError) as raised:
        fixture.dispatcher.dispatch(
            planning=fixture.planning,
            context=fixture.context,
            state=fixture.state,
        )

    assert raised.value.reason == "skill_execution_invalid"


def test_compile_projection_rederives_resolved_scene_from_scene_and_catalog(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    typed_input = Text2EnvCompileInput.model_validate(fixture.planning.decision.parameters)
    scene = parse_rule_based(typed_input.request, seed=typed_input.seed)
    catalog = load_catalog(Path("tests/fixtures/asset_catalog.json"))
    resolved = solve_scene(scene, catalog)
    altered = resolved.model_copy(
        update={
            "objects": (
                resolved.objects[0].model_copy(update={"mass_kg": 999.0}),
                *resolved.objects[1:],
            )
        }
    )
    package_root = tmp_path / "semantically-altered-package"
    build_scene_package(scene, altered, package_root)
    scene_ref = _put_file(
        fixture.store,
        package_root / "scene_spec.json",
        media_type="application/json",
        schema="robotwin.scene_spec.v1",
    )
    resolved_ref = _put_file(
        fixture.store,
        package_root / "resolved_scene.json",
        media_type="application/json",
        schema="robotwin.resolved_scene.v1",
    )
    manifest_ref = _put_file(
        fixture.store,
        package_root / "package_manifest.json",
        media_type="application/json",
        schema="robotwin.generated_scene_package.v1",
    )
    request_member = _put_file(
        fixture.store,
        package_root / "request.txt",
        media_type="text/plain",
        schema=None,
    )
    generated_member = _put_file(
        fixture.store,
        package_root / "generated_scene.py",
        media_type="text/x-python",
        schema=None,
    )
    validation_ref = _put(
        fixture.store,
        tmp_path,
        name="altered_static_validation",
        schema="robotwin.scene_validation.v1",
        value={
            "schema_version": "robotwin.scene_validation.v1",
            "scene_id": altered.scene_id,
            "resolved_scene_sha256": altered.digest(),
            "status": "incomplete",
            "fail_count": 0,
            "not_run_count": 1,
            "checks": [
                {
                    "name": "runtime_evidence",
                    "status": "not_run",
                    "evidence": {"reason": "compile_only"},
                }
            ],
        },
    )
    output = Text2EnvCompileOutput(
        scene_spec=scene_ref,
        resolved_scene=resolved_ref,
        environment_package=EnvironmentPackage(
            package_id=altered.digest(),
            route_id="text2env",
            producer_skill_ref="text2env.compile@1.0.0",
            seed=scene.seed,
            scene_spec_sha256=scene.digest(),
            resolved_scene_sha256=altered.digest(),
            asset_catalog=fixture.app.output.environment_package.asset_catalog,
            package_manifest=manifest_ref,
        ),
        static_validation=validation_ref,
    )
    supporting = (
        scene_ref,
        resolved_ref,
        output.environment_package.asset_catalog,
        manifest_ref,
        validation_ref,
        fixture.app.support,
        request_member,
        generated_member,
    )

    with pytest.raises(ValueError, match="deterministic solver"):
        fixture.adapter.derived_fact_claims(typed_input, output, supporting)


@pytest.mark.parametrize("attack", ["typed_input_request", "package_identity"])
def test_compile_projection_rejects_cross_document_semantic_identity_drift(
    tmp_path: Path,
    attack: str,
) -> None:
    fixture = _fixture(tmp_path)
    typed_input = Text2EnvCompileInput.model_validate(fixture.planning.decision.parameters)
    output = fixture.app.output
    if attack == "typed_input_request":
        typed_input = typed_input.model_copy(update={"request": "Open the laptop."})
        message = "SceneSpec"
    else:
        package = output.environment_package.model_copy(update={"package_id": "f" * 64})
        output = output.model_copy(update={"environment_package": package})
        message = "EnvironmentPackage"

    with pytest.raises(ValueError, match=message):
        fixture.adapter.derived_fact_claims(
            typed_input,
            output,
            _compile_supporting(fixture),
        )


def test_dispatch_accepts_a_distinct_fully_bound_effective_output_catalog(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    input_catalog = Text2EnvCompileInput.model_validate(
        fixture.planning.decision.parameters
    ).asset_catalog
    source_catalog = load_catalog(Path("tests/fixtures/asset_catalog.json"))
    effective_catalog = source_catalog.model_copy(
        update={"source_commit": "generated-admission-effective-v2"}
    )
    catalog_path = tmp_path / "admitted-effective-catalog.json"
    catalog_path.write_text(
        json.dumps(
            effective_catalog.canonical_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    catalog_ref = _put_file(
        fixture.store,
        catalog_path,
        name="admitted_effective_catalog",
        media_type="application/json",
        schema="robotwin.asset_catalog.v1",
    )
    scene = parse_rule_based("Place a can on top of a plate.", seed=7)
    resolved = solve_scene(scene, effective_catalog)
    package_root = tmp_path / "effective-package"
    build_scene_package(scene, resolved, package_root)
    scene_ref = _put_file(
        fixture.store,
        package_root / "scene_spec.json",
        media_type="application/json",
        schema="robotwin.scene_spec.v1",
    )
    resolved_ref = _put_file(
        fixture.store,
        package_root / "resolved_scene.json",
        media_type="application/json",
        schema="robotwin.resolved_scene.v1",
    )
    manifest_ref = _put_file(
        fixture.store,
        package_root / "package_manifest.json",
        media_type="application/json",
        schema="robotwin.generated_scene_package.v1",
    )
    request_member = _put_file(
        fixture.store,
        package_root / "request.txt",
        media_type="text/plain",
        schema=None,
    )
    generated_member = _put_file(
        fixture.store,
        package_root / "generated_scene.py",
        media_type="text/x-python",
        schema=None,
    )
    validation_ref = _put(
        fixture.store,
        tmp_path,
        name="effective_static_validation",
        schema="robotwin.scene_validation.v1",
        value=validate_resolved_scene(
            resolved,
            catalog=None,
            package_root=package_root,
            require_runtime=False,
        ),
    )
    fixture.app.output = Text2EnvCompileOutput(
        scene_spec=scene_ref,
        resolved_scene=resolved_ref,
        environment_package=EnvironmentPackage(
            package_id=resolved.digest(),
            route_id="text2env",
            producer_skill_ref="text2env.compile@1.0.0",
            seed=7,
            scene_spec_sha256=scene.digest(),
            resolved_scene_sha256=resolved.digest(),
            asset_catalog=catalog_ref,
            package_manifest=manifest_ref,
        ),
        static_validation=validation_ref,
    )
    fixture.app.package_members = (request_member, generated_member)

    result = fixture.dispatcher.dispatch(
        planning=fixture.planning,
        context=fixture.context,
        state=fixture.state,
    )

    assert result.status is RunStatus.SUCCEEDED
    assert input_catalog != catalog_ref
    assert result.typed_output is not None
    assert result.typed_output["environment_package"]["asset_catalog"] == (
        catalog_ref.model_dump(mode="json")
    )


@pytest.mark.parametrize("drift", ["max_attempts", "started_before_planning"])
def test_dispatch_rejects_run_state_not_bound_to_descriptor_and_planning_window(
    tmp_path: Path,
    drift: str,
) -> None:
    fixture = _fixture(tmp_path)
    if drift == "max_attempts":
        fixture.app.run_max_attempts = 99
    else:
        fixture.app.started_at_override = _T0

    with pytest.raises(System2DispatchError) as raised:
        fixture.dispatcher.dispatch(
            planning=fixture.planning,
            context=fixture.context,
            state=fixture.state,
        )

    assert raised.value.reason == "skill_execution_invalid"


@pytest.mark.parametrize(
    "attack",
    [
        "running",
        "run_identity",
        "preflight_invocation",
        "preflight_budget",
        "missing_invocation",
        "closure",
        "claims_order",
        "invocation_digest",
        "nested_blocker",
        "duplicate_artifact",
    ],
)
def test_dispatch_rejects_execution_drift_and_preserves_canonical_edge_cases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    fixture = _fixture(
        tmp_path,
        blocked_preflight=attack in {"preflight_invocation", "preflight_budget", "nested_blocker"},
    )
    parameters = Text2EnvCompileInput.model_validate(fixture.planning.decision.parameters)
    if attack == "preflight_invocation":
        fixture.app.blocked_preflight = False
        fixture.app.invoke_typed(parameters)
        fixture.app.blocked_preflight = True
        with pytest.raises(System2DispatchError) as raised:
            fixture.dispatcher.dispatch(
                planning=fixture.planning,
                context=fixture.context,
                state=fixture.state,
            )
        assert raised.value.reason == "skill_execution_invalid"
        return
    if attack == "preflight_budget":
        original = fixture.app.invoke_typed

        def invalid_budget(parameters: Text2EnvCompileInput) -> RunState:
            return original(parameters).model_copy(update={"max_attempts": 1})

        monkeypatch.setattr(fixture.app, "invoke_typed", invalid_budget)
        with pytest.raises(System2DispatchError) as raised:
            fixture.dispatcher.dispatch(
                planning=fixture.planning,
                context=fixture.context,
                state=fixture.state,
            )
        assert raised.value.reason == "skill_execution_invalid"
        return
    if attack == "nested_blocker":
        original = fixture.app.invoke_typed

        def nested(parameters: Text2EnvCompileInput) -> RunState:
            state = original(parameters)
            assert state.blocker is not None
            blocker = state.blocker.model_copy(update={"details": {"nested": {"values": [1, 2]}}})
            return state.model_copy(update={"blocker": blocker})

        monkeypatch.setattr(fixture.app, "invoke_typed", nested)
        result = fixture.dispatcher.dispatch(
            planning=fixture.planning,
            context=fixture.context,
            state=fixture.state,
        )
        assert result.status is RunStatus.BLOCKED
        return

    normal = fixture.app.invoke_typed(parameters)
    if attack == "running":
        altered = RunState(
            run_id=normal.run_id,
            invocation_digest=normal.invocation_digest,
            skill_id=normal.skill_id,
            skill_version=normal.skill_version,
            status=RunStatus.RUNNING,
            attempt=1,
            max_attempts=1,
            started_at=normal.started_at,
            ended_at=None,
            events=(normal.events[0],),
            artifacts=(),
            output=None,
            blocker=None,
        )
    elif attack == "run_identity":
        altered = normal.model_copy(update={"skill_version": "9.9.9"})
    elif attack == "missing_invocation":
        altered = normal
        fixture.app._invocations.clear()
    elif attack == "closure":
        altered = normal.model_copy(
            update={
                "artifacts": tuple(ref for ref in normal.artifacts if ref != fixture.app.support)
            }
        )
    elif attack == "invocation_digest":
        altered = normal.model_copy(update={"invocation_digest": "f" * 64})
        invocation = fixture.app._invocations[normal.run_id]
        fixture.app._invocations[normal.run_id] = invocation.model_copy(
            update={"invocation_digest": "f" * 64}
        )
    elif attack == "duplicate_artifact":
        first = fixture.app.support.model_copy(update={"name": "z_support"})
        second = fixture.app.support.model_copy(update={"name": "a_support"})
        artifacts = (first, second, *normal.artifacts)
        events = (
            normal.events[0],
            normal.events[-1].model_copy(update={"artifact_refs": artifacts}),
        )
        altered = normal.model_copy(update={"artifacts": artifacts, "events": events})
    else:
        altered = normal
        original_claims = CompileSystem2Application.derived_fact_claims

        def reversed_claims(
            self: CompileSystem2Application,
            typed_input: object,
            output: object,
            supporting: tuple[ArtifactRef, ...],
        ) -> tuple[ReceiptDerivedFactClaim, ...]:
            return tuple(
                reversed(  # type: ignore[arg-type]
                    original_claims(self, typed_input, output, supporting)
                )
            )

        monkeypatch.setattr(
            CompileSystem2Application,
            "derived_fact_claims",
            reversed_claims,
        )

    monkeypatch.setattr(fixture.app, "invoke_typed", lambda _parameters: altered)
    if attack == "duplicate_artifact":
        result = fixture.dispatcher.dispatch(
            planning=fixture.planning,
            context=fixture.context,
            state=fixture.state,
        )
        receipt = TrustedToolReceipt.model_validate_json(
            fixture.store.resolve(result.trusted_receipt).path.read_bytes()
        )
        names = [ref.name for ref in receipt.supporting_artifacts]
        assert "a_support" in names
        assert "z_support" not in names
        return
    with pytest.raises(System2DispatchError) as raised:
        fixture.dispatcher.dispatch(
            planning=fixture.planning,
            context=fixture.context,
            state=fixture.state,
        )
    assert raised.value.reason == "skill_execution_invalid"


def test_dispatch_fails_closed_when_publisher_or_cas_reader_lies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    monkeypatch.setattr(
        fixture.store,
        "put_file",
        lambda *_args, **_kwargs: fixture.app.support,
    )
    with pytest.raises(System2DispatchError) as publisher:
        fixture.dispatcher.dispatch(
            planning=fixture.planning,
            context=fixture.context,
            state=fixture.state,
        )
    assert publisher.value.reason == "skill_execution_invalid"

    fixture = _fixture(tmp_path / "reader")
    bad = tmp_path / "reader" / "drifted.bin"
    bad.write_bytes(b"drifted")
    qualification = fixture.app.descriptor.qualification_artifact
    original_resolve = fixture.store.resolve

    def drifted(ref: ArtifactRef) -> ResolvedArtifact:
        if ref == qualification:
            return ResolvedArtifact(ref=ref, path=bad)
        return original_resolve(ref)

    monkeypatch.setattr(fixture.store, "resolve", drifted)
    with pytest.raises(System2DispatchError) as reader:
        fixture.dispatcher.dispatch(
            planning=fixture.planning,
            context=fixture.context,
            state=fixture.state,
        )
    assert reader.value.reason == "skill_identity_mismatch"

    fixture = _fixture(tmp_path / "digest-reader")
    bad = tmp_path / "digest-reader" / "drifted-report.bin"
    bad.write_bytes(b"drifted report")
    qualification = SkillQualification.model_validate_json(
        fixture.store.resolve(fixture.app.descriptor.qualification_artifact).path.read_bytes()
    )
    original_resolve_digest = fixture.store.resolve_digest

    def drifted_digest(digest: str) -> Path:
        if digest == qualification.report_sha256:
            return bad
        return original_resolve_digest(digest)

    monkeypatch.setattr(fixture.store, "resolve_digest", drifted_digest)
    with pytest.raises(System2DispatchError) as digest_reader:
        fixture.dispatcher.dispatch(
            planning=fixture.planning,
            context=fixture.context,
            state=fixture.state,
        )
    assert digest_reader.value.reason == "skill_identity_mismatch"
