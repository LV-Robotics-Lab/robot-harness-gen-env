"""Typed native qualifications enter Registry v1 and Golden without relabeling."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import BaseModel

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.event_journal import SQLiteEventJournal
from self_improving.harness.events import RunRecorder
from self_improving.harness.golden_execution import (
    QualifiedApplicationRunPendingError,
    _create_golden_execution_binding,
    create_golden_execution_catalog,
)
from self_improving.harness.golden_image2env_execution import _NativeApplicationAdapter
from self_improving.harness.golden_image2env_policy import NativeImageEvidencePolicy
from self_improving.harness.golden_run import GoldenRunHarness
from self_improving.harness.golden_store import SQLiteGoldenRunStore
from self_improving.harness.mcp_adapter import MCPToolCall, create_harness_mcp_adapter
from self_improving.harness.native_skill_projection import (
    NativeSkillProjection,
    NativeSkillProjectionError,
    issue_native_skill_projection,
    load_native_skill_projection,
    publish_native_registry_snapshot,
)
from self_improving.harness.qualification import QualificationReportV1
from self_improving.harness.registry import (
    HandlerResult,
    SkillRegistry,
    StaticDependencyResolver,
    _EvidenceInvariantRegistration,
    _invocation_digest,
)
from self_improving.harness.run_store import ControlledRunIntent, SQLiteRunStore
from self_improving.harness.schema_catalog import schema_model
from self_improving.harness.schemas.common import (
    ArtifactRef,
    Invocation,
    RegisteredSkillDescriptor,
    RunStatus,
    SkillDescriptor,
    SkillDescriptorV2,
)
from self_improving.harness.schemas.genesis_fresh_capture import (
    GenesisFreshCaptureInputV1,
    GenesisFreshCaptureReceiptV1,
)
from self_improving.harness.schemas.genesis_scene_application_v2 import GenesisSceneReplayInputV2
from self_improving.harness.schemas.genesis_scene_assessment import (
    GenesisSceneValidateInputV1,
    GenesisSceneValidationV1,
)
from self_improving.harness.schemas.genesis_scene_execution_v2 import (
    GenesisSceneExecutionReceiptV2,
    GenesisSceneExecutionRequestV2,
)
from self_improving.harness.schemas.image2env_genesis_compile import (
    Image2EnvGenesisCompileReceiptV1,
)
from self_improving.harness.schemas.workflow import (
    RunCommandRequest,
    RunReadRequest,
    RunStartRequest,
)
from self_improving.harness.schemas.workflow_operation import StateDeltaV2, ToolResultV2
from self_improving.harness.trusted_state import TrustedWorldState, WorldFactEvidence
from self_improving.harness.typed_qualification_admission import (
    freeze_typed_qualification_admission,
    verify_typed_qualification_admission,
)
from tests.self_improving.harness import test_golden_run as golden_base
from tests.self_improving.harness.test_typed_qualification_admission import (
    STAMP,
    _fixture,
)


def _read(store: LocalArtifactStore, ref: ArtifactRef, model: type[BaseModel]):
    return model.model_validate_json(store.resolve(ref).path.read_bytes())


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _put_model(
    store: LocalArtifactStore,
    tmp_path: Path,
    name: str,
    model: BaseModel,
) -> ArtifactRef:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (
            json.dumps(
                model.model_dump(mode="json"),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
    )
    return store.put_file(
        path,
        name=name,
        media_type="application/json",
        schema_version=model.model_dump(mode="json").get("schema_version"),
    )


def _admission(tmp_path: Path, kind: str):
    fixture = _fixture(tmp_path / f"{kind}-qualification", kind)
    return fixture, freeze_typed_qualification_admission(
        artifact_store=fixture.store,
        child_authority=fixture.authority,
        descriptor_ref=fixture.descriptor,
        qualification_ref=fixture.qualification,
        qualification_report_ref=fixture.report,
        implementation_members=fixture.implementation,
    )


def _native_image_binding(
    *,
    store: LocalArtifactStore,
    authority: SQLiteRunStore,
    skill_ref: str,
    application: object,
    predecessors: dict[str, SQLiteRunStore] | None = None,
):
    policy = NativeImageEvidencePolicy(
        store,
        authority,
        skill_ref,
        {} if predecessors is None else predecessors,
    )
    return _create_golden_execution_binding(
        policy=policy,
        application=_NativeApplicationAdapter(application, policy),
        child_authority=authority,
    )


def _issue_projection(
    *,
    store: LocalArtifactStore,
    admission,
    authority: SQLiteRunStore,
    application: object,
    predecessors: dict[str, SQLiteRunStore] | None = None,
):
    binding = _native_image_binding(
        store=store,
        authority=authority,
        skill_ref=admission.admission.skill_ref,
        application=application,
        predecessors=predecessors,
    )
    return issue_native_skill_projection(
        artifact_store=store,
        admission=admission,
        native_binding=binding,
    )


@dataclass(frozen=True)
class _ProjectionOnlyNativeApplication:
    descriptor: RegisteredSkillDescriptor

    @property
    def skills(self):
        return (self.descriptor,)

    def invoke(self, parameters, *, run_id):  # pragma: no cover - listing-only fixture
        raise AssertionError((parameters, run_id))

    invoke_typed = invoke


class _KernelHandler:
    def __init__(
        self,
        output: BaseModel,
        output_ref: ArtifactRef,
        *,
        fail: bool = False,
    ) -> None:
        self.output = output
        self.output_ref = output_ref
        self.fail = fail

    def __call__(self, _parameters, _context):
        if self.fail:
            raise RuntimeError("fixture native handler failed")
        return HandlerResult(self.output, (self.output_ref,))


class _TypedRegistrationPolicy:
    def __init__(
        self,
        *,
        descriptor: SkillDescriptorV2,
        handler: _KernelHandler,
        resolver: StaticDependencyResolver,
        qualification_bytes: bytes,
        report_bytes: bytes,
    ) -> None:
        self.descriptor = descriptor
        self.handler = handler
        self.resolver = resolver
        self.qualification_bytes = qualification_bytes
        self.report_bytes = report_bytes

    def verify_and_build(self, request, *, artifact_resolver):
        if request != self.descriptor.qualification_artifact:
            raise ValueError("wrong typed qualification")
        artifact_resolver.resolve(request)
        return _EvidenceInvariantRegistration(
            self.descriptor,
            self.handler,
            self.resolver,
            self.qualification_bytes,
            self.report_bytes,
        )


class _KernelNativeApplication:
    """Real Registry/SQLite child kernel behind the public projection API."""

    def __init__(
        self,
        *,
        store: LocalArtifactStore,
        authority: SQLiteRunStore,
        admission,
        advertised_descriptor: SkillDescriptorV2 | None = None,
        child_implementation_sha256: str | None = None,
        fail_handler: bool = False,
        output: BaseModel | None = None,
        output_ref: ArtifactRef | None = None,
        clock=lambda: STAMP,
    ) -> None:
        descriptor = _read(store, admission.admission.descriptor, SkillDescriptorV2)
        first = admission.admission.candidate_intents[0]
        output_model = (
            schema_model(descriptor.output_schema).model_validate(first.run_state.output)
            if output is None
            else output
        )
        registered_output_ref = first.output if output_ref is None else output_ref
        dependencies = first.invocation.dependencies
        resolver = StaticDependencyResolver({admission.admission.skill_ref: dependencies})
        child_descriptor = (
            descriptor
            if child_implementation_sha256 is None
            else descriptor.model_copy(
                update={"implementation_sha256": child_implementation_sha256}
            )
        )
        registration = _TypedRegistrationPolicy(
            descriptor=child_descriptor,
            handler=_KernelHandler(output_model, registered_output_ref, fail=fail_handler),
            resolver=resolver,
            qualification_bytes=store.resolve(admission.admission.qualification).path.read_bytes(),
            report_bytes=store.resolve(admission.admission.qualification_report).path.read_bytes(),
        )
        self.registry = SkillRegistry(
            artifact_resolver=store,
            dependency_resolver=resolver,
            event_sink=SQLiteEventJournal(authority.path),
            clock=clock,
            run_id_factory=lambda: UUID("91000000-0000-4000-8000-000000000099"),
            run_store=authority,
            evidence_invariant_policy=registration,
        )
        self.registry.register_evidence_invariant(child_descriptor.qualification_artifact)
        self._advertised = advertised_descriptor or descriptor
        self._authority = authority

    @property
    def skills(self):
        return (self._advertised,)

    def invoke(self, parameters, *, run_id):
        skill_id, version = f"{self._advertised.skill_id}@{self._advertised.version}".split("@")
        return self.registry.invoke_with_run_id(
            skill_id,
            version,
            parameters.model_dump(mode="json"),
            run_id=run_id,
        )

    invoke_typed = invoke

    def invocation(self, run_id):
        return self._authority.read_invocation(run_id)

    def run_state(self, run_id):
        return self._authority.read_run_state(run_id)


class _ControlledV1NativeApplication:
    """Exact controlled v1 child using the public SQLite/event persistence seams."""

    def __init__(
        self,
        *,
        store: LocalArtifactStore,
        authority: SQLiteRunStore,
        admission,
        output: BaseModel,
        output_ref: ArtifactRef,
        clock,
    ) -> None:
        self.store = store
        self.authority = authority
        self.admission = admission
        self.descriptor = _read(store, admission.admission.descriptor, SkillDescriptor)
        self.output = output
        self.output_ref = output_ref
        self.clock = clock
        self.dependencies = admission.admission.candidate_intents[0].invocation.dependencies

    @property
    def skills(self):
        return (self.descriptor,)

    def invoke(self, parameters, *, run_id):
        typed = schema_model(self.descriptor.input_schema).model_validate(parameters)
        effective = typed.model_dump(mode="json")
        existing = self.authority.read_controlled_lifecycle(run_id)
        if existing.terminal is not None:
            if existing.invocation is None or existing.invocation.effective_parameters != effective:
                raise ValueError("controlled compile UUID has different parameters")
            return existing.terminal
        digest = _invocation_digest(
            skill_id=self.descriptor.skill_id,
            skill_version=self.descriptor.version,
            effective_parameters=typed,
            dependencies=self.dependencies,
            max_attempts=self.descriptor.max_attempts,
        )
        parameters_sha256 = _sha(effective)
        intent = ControlledRunIntent(
            run_id=run_id,
            skill_id=self.descriptor.skill_id,
            skill_version=self.descriptor.version,
            input_schema=self.descriptor.input_schema,
            phase="validated_input",
            raw_parameters_sha256=parameters_sha256,
            effective_parameters_sha256=parameters_sha256,
            dependencies=self.dependencies,
            descriptor_sha256=_sha(self.descriptor.model_dump(mode="json")),
            implementation_sha256=self.admission.admission.implementation_sha256,
            qualification_sha256=self.admission.admission.qualification.sha256,
            max_attempts=self.descriptor.max_attempts,
        )
        if not self.authority.reserve_controlled_intent(intent):
            raise RuntimeError("controlled compile is pending and will not be repeated")
        invocation = Invocation(
            run_id=run_id,
            skill_id=self.descriptor.skill_id,
            skill_version=self.descriptor.version,
            effective_parameters=effective,
            dependencies=self.dependencies,
            max_attempts=self.descriptor.max_attempts,
            invocation_digest=digest,
        )
        self.authority.put_invocation(invocation)
        recorder = RunRecorder(
            run_id=run_id,
            skill_id=self.descriptor.skill_id,
            skill_version=self.descriptor.version,
            clock=self.clock,
            sink=SQLiteEventJournal(self.authority.path),
        )
        recorder.start(stage="preflight", attempt=1)
        recorder.finish(
            status=RunStatus.SUCCEEDED,
            stage="complete",
            artifact_refs=(self.output_ref,),
        )
        state = recorder.build_state(
            invocation_digest=digest,
            max_attempts=self.descriptor.max_attempts,
            artifacts=(self.output_ref,),
            output=self.output.model_dump(mode="json"),
            blocker=None,
        )
        self.authority.put_run_state(state)
        return state

    invoke_typed = invoke

    def invocation(self, run_id):
        return self.authority.read_invocation(run_id)

    def run_state(self, run_id):
        return self.authority.read_run_state(run_id)


def test_projection_accepts_only_a_sealed_rich_native_image_binding(tmp_path: Path) -> None:
    fixture, admission = _admission(tmp_path, "replay")
    authority = SQLiteRunStore(tmp_path / "production.sqlite3")
    application = _KernelNativeApplication(
        store=fixture.store,
        authority=authority,
        admission=admission,
    )
    native_binding = _native_image_binding(
        store=fixture.store,
        authority=authority,
        skill_ref=admission.admission.skill_ref,
        application=application,
    )

    projection = issue_native_skill_projection(
        artifact_store=fixture.store,
        admission=admission,
        native_binding=native_binding,
    )

    assert projection.binding.policy is native_binding.policy
    assert (
        projection.binding.child_authority.path.resolve()
        == native_binding.child_authority.path.resolve()
    )


def test_projected_rich_native_policy_commits_all_four_linked_stage_facts(
    tmp_path: Path,
) -> None:
    workflow = _four_stage_workflow(tmp_path)
    snapshot = workflow.started
    expected_keys = (
        ("image.compile",),
        ("image.compile", "image.replay"),
        ("image.compile", "image.fresh_capture", "image.observation", "image.replay"),
        (
            "image.compile",
            "image.fresh_capture",
            "image.observation",
            "image.replay",
            "image.validation",
        ),
    )
    expected_mutations = (
        ("image.compile",),
        ("image.replay",),
        ("image.fresh_capture", "image.observation"),
        ("image.validation",),
    )

    results = []
    for index, wanted in enumerate(expected_keys):
        assert workflow.projections[index].binding.policy is workflow.bindings[index].policy
        operation, snapshot = _submit_stage(workflow, snapshot, index)
        assert operation.status is RunStatus.SUCCEEDED
        assert operation.tool_result is not None
        result = _read(workflow.store, operation.tool_result, ToolResultV2)
        results.append(result)
        assert result.state_delta is not None
        delta = _read(workflow.store, result.state_delta, StateDeltaV2)
        assert tuple(mutation.key for mutation in delta.mutations) == expected_mutations[index]
        state = _read(workflow.store, snapshot.state_ref, TrustedWorldState)
        assert tuple(fact.key for fact in state.facts if fact.key.startswith("image.")) == wanted

    assert tuple(item.key for item in results[2].fresh_observations) == ("image.fresh_capture",)


def _start_harness(
    tmp_path: Path,
    store: LocalArtifactStore,
    registry: ArtifactRef,
    bindings,
    *,
    child_run_id: UUID = UUID("92000000-0000-4000-8000-000000000001"),
):
    objective = golden_base._put(
        store,
        tmp_path,
        name="objective.json",
        payload=golden_base.GENERATED_OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    harness = GoldenRunHarness(
        artifact_store=store,
        workflow_store=SQLiteGoldenRunStore(tmp_path / "golden.sqlite3"),
        execution_catalog=create_golden_execution_catalog(bindings=bindings),
        clock=lambda: golden_base.NOW,
        workflow_id_factory=lambda: golden_base.WORKFLOW_ID,
        child_run_id_factory=lambda: child_run_id,
    )
    return harness, harness.start(golden_base._request(objective, registry))


@dataclass(frozen=True)
class _FourStageWorkflow:
    store: LocalArtifactStore
    projections: tuple
    bindings: tuple
    inputs: tuple[BaseModel, ...]
    child_run_ids: tuple[UUID, ...]
    times: tuple
    registry: ArtifactRef
    harness: GoldenRunHarness
    started: object
    instant: list


def _four_stage_workflow(tmp_path: Path, *, replay_implementation_drift: bool = False):
    central = LocalArtifactStore(tmp_path / "four-stage-cas")
    admissions = []
    for kind in ("compile", "replay", "observe", "validate"):
        fixture, issued = _admission(tmp_path / kind, kind)
        shutil.copytree(fixture.store.root, central.root, dirs_exist_ok=True)
        admissions.append(
            verify_typed_qualification_admission(
                artifact_store=central,
                admission=issued.admission,
            )
        )

    child_run_ids = tuple(
        UUID(value)
        for value in (
            "97000000-0000-4000-8000-000000000001",
            "97000000-0000-4000-8000-000000000002",
            "97000000-0000-4000-8000-000000000003",
            "97000000-0000-4000-8000-000000000004",
        )
    )
    times = tuple(STAMP + timedelta(seconds=index) for index in range(4))
    authorities = tuple(
        SQLiteRunStore(tmp_path / "four-stage-state" / kind / "runs.sqlite3")
        for kind in ("compile", "replay", "observe", "validate")
    )

    compile_first = admissions[0].admission.candidate_intents[0]
    compile_input = schema_model("harness.image2env_genesis_compile_request.v1").model_validate(
        compile_first.invocation.effective_parameters
    )
    compile_output = Image2EnvGenesisCompileReceiptV1.model_validate(compile_first.run_state.output)
    compile_output_ref = compile_first.output

    runtime_manifest = golden_base._put(
        central,
        tmp_path,
        name="runtime-manifest.json",
        payload=b"{}",
        schema_version="harness.genesis_runtime_manifest.v1",
    )
    execution_request = GenesisSceneExecutionRequestV2(
        schema_version="harness.genesis_scene_execution_request.v2",
        scene=compile_output.scene,
        compile_receipt=compile_output_ref,
        runtime_manifest=runtime_manifest,
        asset_qualification=compile_output.qualification,
    )
    execution_request_ref = _put_model(
        central,
        tmp_path,
        "linked-execution-request.json",
        execution_request,
    )
    replay_input = GenesisSceneReplayInputV2(
        schema_version="harness.genesis_scene_replay_input.v2",
        compile_run_id=child_run_ids[0],
        execution_request=execution_request_ref,
    )
    replay_first = admissions[1].admission.candidate_intents[0]
    replay_output = GenesisSceneExecutionReceiptV2.model_validate(
        replay_first.run_state.output
    ).model_copy(update={"request": execution_request_ref})
    replay_output_ref = _put_model(
        central,
        tmp_path,
        "linked-replay-receipt.json",
        replay_output,
    )

    observe_input = GenesisFreshCaptureInputV1(
        schema_version="harness.genesis_fresh_capture_input.v1",
        replay_run_id=child_run_ids[1],
        ttl_seconds=300,
    )
    observe_first = admissions[2].admission.candidate_intents[0]
    observe_output = GenesisFreshCaptureReceiptV1.model_validate(
        observe_first.run_state.output
    ).model_copy(
        update={
            "replay_run_id": child_run_ids[1],
            "replay_receipt": replay_output_ref,
            "captured_at": times[2],
            "checked_at": times[2],
            "expires_at": times[2] + timedelta(seconds=300),
        }
    )
    observe_output_ref = _put_model(
        central,
        tmp_path,
        "linked-observation-receipt.json",
        observe_output,
    )

    validate_input = GenesisSceneValidateInputV1(
        schema_version="harness.genesis_scene_validate_input.v1",
        replay_run_id=child_run_ids[1],
    )
    validate_first = admissions[3].admission.candidate_intents[0]
    validate_output = GenesisSceneValidationV1.model_validate(
        validate_first.run_state.output
    ).model_copy(
        update={
            "replay_run_id": child_run_ids[1],
            "execution_receipt": replay_output_ref,
            "scene": execution_request.scene,
            "replay": replay_output.replay,
            "report": replay_output.report,
            "assessed_at": times[3],
            "candidate_only": False,
        }
    )
    validate_output_ref = _put_model(
        central,
        tmp_path,
        "linked-validation-receipt.json",
        validate_output,
    )

    compile_application = _ControlledV1NativeApplication(
        store=central,
        authority=authorities[0],
        admission=admissions[0],
        output=compile_output,
        output_ref=compile_output_ref,
        clock=lambda: times[0],
    )
    applications = (
        compile_application,
        _KernelNativeApplication(
            store=central,
            authority=authorities[1],
            admission=admissions[1],
            child_implementation_sha256=("f" * 64 if replay_implementation_drift else None),
            output=replay_output,
            output_ref=replay_output_ref,
            clock=lambda: times[1],
        ),
        _KernelNativeApplication(
            store=central,
            authority=authorities[2],
            admission=admissions[2],
            output=observe_output,
            output_ref=observe_output_ref,
            clock=lambda: times[2],
        ),
        _KernelNativeApplication(
            store=central,
            authority=authorities[3],
            admission=admissions[3],
            output=validate_output,
            output_ref=validate_output_ref,
            clock=lambda: times[3],
        ),
    )
    skills = tuple(admission.admission.skill_ref for admission in admissions)
    predecessors = dict(zip(skills, authorities, strict=True))
    native_bindings = tuple(
        _native_image_binding(
            store=central,
            authority=authority,
            skill_ref=skill_ref,
            application=application,
            predecessors=predecessors,
        )
        for authority, skill_ref, application in zip(
            authorities,
            skills,
            applications,
            strict=True,
        )
    )
    projections = tuple(
        issue_native_skill_projection(
            artifact_store=central,
            admission=admission,
            native_binding=binding,
        )
        for admission, binding in zip(admissions, native_bindings, strict=True)
    )
    registry = publish_native_registry_snapshot(
        artifact_store=central,
        projections=projections,
    )

    proposal_ref = compile_input.compile_input.interpretation_proposal
    proposal = _read(
        central,
        proposal_ref,
        schema_model("harness.scene_interpretation_proposal.v1"),
    )
    prompt = central.resolve(proposal.effective_prompt).path.read_text()
    initial_time = times[0] - timedelta(seconds=1)
    evidence = tuple(
        sorted(
            (
                _put_model(
                    central,
                    tmp_path,
                    f"initial-{index}.json",
                    WorldFactEvidence(
                        source_kind="user_input",
                        key=key,
                        value=value,
                        observed_at=initial_time,
                        valid_until=None,
                    ),
                )
                for index, (key, value) in enumerate(
                    (
                        ("request.objective", prompt),
                        (
                            "request.image_source",
                            compile_input.compile_input.source_media.model_dump(mode="json"),
                        ),
                    )
                )
            ),
            key=lambda ref: (ref.sha256, ref.bytes, ref.media_type, ref.schema_version or ""),
        )
    )
    instant = [initial_time]
    harness = GoldenRunHarness(
        artifact_store=central,
        workflow_store=SQLiteGoldenRunStore(tmp_path / "four-stage-workflow.sqlite3"),
        execution_catalog=create_golden_execution_catalog(
            bindings=tuple(projection.binding for projection in projections)
        ),
        clock=lambda: instant[0],
        workflow_id_factory=lambda: UUID("98000000-0000-4000-8000-000000000001"),
        child_run_id_factory=iter(child_run_ids).__next__,
    )
    started = harness.start(
        RunStartRequest(
            schema_version="harness.run_start_request.v1",
            principal_id="tests/native-rich-projection",
            idempotency_key="native-rich-start-001",
            workspace="ephemeral",
            requested_profile="genesis.rigid_scene@1",
            initial_fact_evidence=evidence,
            registry_snapshot=registry,
        )
    )
    return _FourStageWorkflow(
        store=central,
        projections=projections,
        bindings=native_bindings,
        inputs=(compile_input, replay_input, observe_input, validate_input),
        child_run_ids=child_run_ids,
        times=times,
        registry=registry,
        harness=harness,
        started=started,
        instant=instant,
    )


def _submit_stage(workflow: _FourStageWorkflow, snapshot, index: int):
    workflow.instant[0] = workflow.times[index]
    operation = workflow.harness.submit(
        RunCommandRequest(
            schema_version="harness.run_command_request.v1",
            command_id=UUID(f"99000000-0000-4000-8000-{index + 1:012d}"),
            principal_id=snapshot.principal_id,
            workflow_run_id=snapshot.workflow_run_id,
            idempotency_key=f"native-rich-stage-{index}",
            expected_workflow_revision=snapshot.revision,
            expected_state_sha256=snapshot.state_sha256,
            expected_receipt_head_sha256=snapshot.receipt_head.sha256,
            skill_ref=workflow.projections[index].binding.skill_ref,
            parameters=workflow.inputs[index].model_dump(mode="json"),
        )
    )
    return operation, workflow.harness.read(
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=snapshot.principal_id,
            workflow_run_id=snapshot.workflow_run_id,
        )
    )


def test_four_native_typed_reports_project_as_four_exact_mcp_tools(tmp_path: Path) -> None:
    central = LocalArtifactStore(tmp_path / "central-cas")
    projections = []
    native_report_schemas = set()
    for kind in ("compile", "replay", "observe", "validate"):
        fixture, issued = _admission(tmp_path / kind, kind)
        shutil.copytree(fixture.store.root, central.root, dirs_exist_ok=True)
        admission = verify_typed_qualification_admission(
            artifact_store=central,
            admission=issued.admission,
        )
        descriptor_model = (
            SkillDescriptorV2
            if admission.admission.descriptor.schema_version == "harness.skill_descriptor.v2"
            else schema_model("harness.skill_descriptor.v1")
        )
        descriptor = _read(central, admission.admission.descriptor, descriptor_model)
        projection = _issue_projection(
            store=central,
            admission=admission,
            application=_ProjectionOnlyNativeApplication(descriptor),
            authority=SQLiteRunStore(tmp_path / kind / "production.sqlite3"),
        )
        projections.append(projection)
        generic_report = _read(
            central,
            projection.qualification_report_ref,
            QualificationReportV1,
        )
        evidence = generic_report.checks[0].evidence
        assert evidence["typed_admission"] == admission.admission_ref.model_dump(mode="json")
        assert evidence["native_qualification_report"] == (
            admission.admission.qualification_report.model_dump(mode="json")
        )
        assert evidence["native_evidence_policy"] == {
            "id": projection.binding.policy.evidence_policy_id,
            "version": projection.binding.policy.evidence_policy_version,
            "implementation_sha256": (
                projection.binding.policy.evidence_policy_implementation_sha256
            ),
        }
        native_report_schemas.add(admission.admission.qualification_report.schema_version)

    registry = publish_native_registry_snapshot(
        artifact_store=central,
        projections=tuple(projections),
    )
    harness, started = _start_harness(
        tmp_path,
        central,
        registry,
        tuple(projection.binding for projection in projections),
    )
    adapter = create_harness_mcp_adapter(
        harness=harness,
        principal_id=started.principal_id,
        registry_snapshot=registry,
    )

    assert tuple(tool.name for tool in adapter.list_tools(registry)[3:]) == (
        "genesis_observe_v3_0_0",
        "image2env_compile_v2_0_0",
        "image2env_replay_v2_0_0",
        "image2env_validate_v2_0_0",
    )
    assert all(tool.name != "skill.invoke" for tool in adapter.list_tools(registry))
    assert native_report_schemas == {
        "harness.image2env_genesis_skill_qualification_report.v1",
        "harness.genesis_scene_skill_qualification_report.v2",
        "harness.genesis_fresh_capture_qualification.v1",
        "harness.genesis_scene_validation_qualification.v1",
    }


def test_projection_reload_recovers_each_existing_native_child_without_reexecution(
    tmp_path: Path,
) -> None:
    workflow = _four_stage_workflow(tmp_path)
    snapshot = workflow.started
    for index in range(4):
        _, snapshot = _submit_stage(workflow, snapshot, index)

    for index, (projection, native_binding) in enumerate(
        zip(workflow.projections, workflow.bindings, strict=True)
    ):
        loaded = load_native_skill_projection(
            artifact_store=workflow.store,
            admission_ref=projection.admission_ref,
            descriptor_ref=projection.descriptor_ref,
            qualification_ref=projection.qualification_ref,
            qualification_report_ref=projection.qualification_report_ref,
            native_binding=native_binding,
        )
        application = loaded.binding.application
        assert application is not None
        parameters = loaded.binding.policy.normalize_parameters(
            workflow.inputs[index].model_dump(mode="json")
        )
        before = native_binding.child_authority.read_controlled_lifecycle(
            workflow.child_run_ids[index]
        )
        recovered = application.invoke(parameters, run_id=workflow.child_run_ids[index])
        assert recovered == before.terminal
        assert application.invocation(workflow.child_run_ids[index]) == before.invocation
        assert application.run_state(workflow.child_run_ids[index]) == before.terminal
        assert (
            native_binding.child_authority.read_controlled_lifecycle(workflow.child_run_ids[index])
            == before
        )


def test_projected_native_failure_has_no_rich_policy_claim(
    tmp_path: Path,
) -> None:
    fixture, admission = _admission(tmp_path, "replay")
    authority = SQLiteRunStore(tmp_path / "failed-production.sqlite3")
    application = _KernelNativeApplication(
        store=fixture.store,
        authority=authority,
        admission=admission,
        fail_handler=True,
    )
    projection = _issue_projection(
        store=fixture.store,
        admission=admission,
        application=application,
        authority=authority,
    )
    application = projection.binding.application
    assert application is not None
    run_id = UUID("92000000-0000-4000-8000-000000000002")
    terminal = application.invoke(
        projection.binding.policy.normalize_parameters(
            admission.admission.candidate_intents[0].invocation.effective_parameters
        ),
        run_id=run_id,
    )
    assert terminal.status is RunStatus.FAILED
    assert projection.binding.policy.claims(terminal) == ()


def test_projection_rejects_substitution_foreign_admission_and_missing_source(
    tmp_path: Path,
) -> None:
    replay_fixture, replay = _admission(tmp_path / "replay", "replay")
    observe_fixture, observe = _admission(tmp_path / "observe", "observe")
    replay_application = _ProjectionOnlyNativeApplication(
        _read(replay_fixture.store, replay.admission.descriptor, SkillDescriptorV2)
    )
    replay_authority = SQLiteRunStore(tmp_path / "replay.sqlite3")
    replay_binding = _native_image_binding(
        store=replay_fixture.store,
        authority=replay_authority,
        skill_ref=replay.admission.skill_ref,
        application=replay_application,
    )
    with pytest.raises(TypeError, match="verified typed admission"):
        issue_native_skill_projection(
            artifact_store=replay_fixture.store,
            admission=replay.admission,
            native_binding=replay_binding,
        )
    shutil.copytree(observe_fixture.store.root, replay_fixture.store.root, dirs_exist_ok=True)
    relocated_observe = verify_typed_qualification_admission(
        artifact_store=replay_fixture.store,
        admission=observe.admission,
    )
    with pytest.raises(NativeSkillProjectionError, match="foreign"):
        issue_native_skill_projection(
            artifact_store=replay_fixture.store,
            admission=relocated_observe,
            native_binding=replay_binding,
        )

    projection = issue_native_skill_projection(
        artifact_store=replay_fixture.store,
        admission=replay,
        native_binding=replay_binding,
    )
    observe_application = _ProjectionOnlyNativeApplication(
        _read(observe_fixture.store, observe.admission.descriptor, SkillDescriptorV2)
    )
    observe_authority = SQLiteRunStore(tmp_path / "observe.sqlite3")
    observe_binding = _native_image_binding(
        store=observe_fixture.store,
        authority=observe_authority,
        skill_ref=observe.admission.skill_ref,
        application=observe_application,
    )
    observe_projection = issue_native_skill_projection(
        artifact_store=observe_fixture.store,
        admission=observe,
        native_binding=observe_binding,
    )
    with pytest.raises(NativeSkillProjectionError, match="document reference"):
        load_native_skill_projection(
            artifact_store=replay_fixture.store,
            admission_ref=projection.admission_ref,
            descriptor_ref=projection.descriptor_ref,
            qualification_ref=projection.qualification_ref,
            qualification_report_ref=observe_projection.qualification_report_ref,
            native_binding=replay_binding,
        )
    relocated_root = tmp_path / "relocated-cas"
    shutil.copytree(replay_fixture.store.root, relocated_root)
    relocated = LocalArtifactStore(relocated_root)
    relocated_binding = _native_image_binding(
        store=relocated,
        authority=replay_authority,
        skill_ref=replay.admission.skill_ref,
        application=replay_application,
    )
    assert (
        load_native_skill_projection(
            artifact_store=relocated,
            admission_ref=projection.admission_ref,
            descriptor_ref=projection.descriptor_ref,
            qualification_ref=projection.qualification_ref,
            qualification_report_ref=projection.qualification_report_ref,
            native_binding=relocated_binding,
        ).registry_entry
        == projection.registry_entry
    )
    relocated.resolve(projection.implementation_sources[0]).path.unlink()
    with pytest.raises(NativeSkillProjectionError, match="source"):
        load_native_skill_projection(
            artifact_store=relocated,
            admission_ref=projection.admission_ref,
            descriptor_ref=projection.descriptor_ref,
            qualification_ref=projection.qualification_ref,
            qualification_report_ref=projection.qualification_report_ref,
            native_binding=relocated_binding,
        )

    broken_fixture, broken = _admission(tmp_path / "broken", "replay")
    broken_application = _ProjectionOnlyNativeApplication(
        _read(broken_fixture.store, broken.admission.descriptor, SkillDescriptorV2)
    )
    broken_binding = _native_image_binding(
        store=broken_fixture.store,
        authority=SQLiteRunStore(tmp_path / "broken.sqlite3"),
        skill_ref=broken.admission.skill_ref,
        application=broken_application,
    )
    broken_member = broken.admission.implementation_members[0].artifact
    broken_fixture.store.resolve(broken_member).path.unlink()
    with pytest.raises(NativeSkillProjectionError, match="issuance"):
        issue_native_skill_projection(
            artifact_store=broken_fixture.store,
            admission=broken,
            native_binding=broken_binding,
        )


def test_projection_public_guards_are_fail_closed(tmp_path: Path) -> None:
    fixture, admission = _admission(tmp_path, "replay")
    authority = SQLiteRunStore(tmp_path / "guards.sqlite3")
    application = _ProjectionOnlyNativeApplication(
        _read(fixture.store, admission.admission.descriptor, SkillDescriptorV2)
    )
    native_binding = _native_image_binding(
        store=fixture.store,
        authority=authority,
        skill_ref=admission.admission.skill_ref,
        application=application,
    )
    projection = issue_native_skill_projection(
        artifact_store=fixture.store,
        admission=admission,
        native_binding=native_binding,
    )
    with pytest.raises(AttributeError, match="immutable"):
        projection._admission_ref = projection.admission_ref
    with pytest.raises(TypeError, match="deep verifier"):
        NativeSkillProjection(
            admission_ref=projection.admission_ref,
            descriptor_ref=projection.descriptor_ref,
            qualification_ref=projection.qualification_ref,
            qualification_report_ref=projection.qualification_report_ref,
            implementation_sources=projection.implementation_sources,
            registry_entry=projection.registry_entry,
            binding=projection.binding,
            _token=object(),
        )
    with pytest.raises(TypeError, match="artifact_store"):
        publish_native_registry_snapshot(artifact_store=object(), projections=())
    with pytest.raises(TypeError, match="projections"):
        publish_native_registry_snapshot(artifact_store=fixture.store, projections=[])
    with pytest.raises(TypeError, match="verified native projections"):
        publish_native_registry_snapshot(
            artifact_store=fixture.store,
            projections=(object(),),
        )
    with pytest.raises(NativeSkillProjectionError, match="share one exact CAS"):
        publish_native_registry_snapshot(
            artifact_store=LocalArtifactStore(tmp_path / "other-cas"),
            projections=(projection,),
        )
    with pytest.raises(TypeError, match="artifact_store"):
        issue_native_skill_projection(
            artifact_store=object(),
            admission=admission,
            native_binding=native_binding,
        )
    with pytest.raises(TypeError, match="same exact CAS"):
        issue_native_skill_projection(
            artifact_store=LocalArtifactStore(tmp_path / "foreign-issue-cas"),
            admission=admission,
            native_binding=native_binding,
        )
    with pytest.raises(TypeError, match="sealed native image binding"):
        issue_native_skill_projection(
            artifact_store=fixture.store,
            admission=admission,
            native_binding=object(),
        )
    arbitrary_policy = NativeImageEvidencePolicy(
        fixture.store,
        authority,
        "image2env.replay@2.0.0",
    )
    foreign_policy = _create_golden_execution_binding(
        policy=arbitrary_policy,
        application=None,
        child_authority=authority,
    )
    with pytest.raises(TypeError, match="sealed native image binding"):
        issue_native_skill_projection(
            artifact_store=fixture.store,
            admission=admission,
            native_binding=foreign_policy,
        )


def test_projection_preserves_a_native_in_flight_child_without_reexecution(
    tmp_path: Path,
) -> None:
    fixture, admission = _admission(tmp_path, "replay")
    authority = SQLiteRunStore(tmp_path / "pending.sqlite3")
    application = _KernelNativeApplication(
        store=fixture.store,
        authority=authority,
        admission=admission,
    )
    projection = _issue_projection(
        store=fixture.store,
        admission=admission,
        application=application,
        authority=authority,
    )
    native_descriptor = _read(
        fixture.store,
        admission.admission.descriptor,
        SkillDescriptorV2,
    )
    parameters = admission.admission.candidate_intents[0].invocation.effective_parameters
    parameters_sha256 = _sha(parameters)
    run_id = UUID("96000000-0000-4000-8000-000000000001")
    authority.reserve_controlled_intent(
        ControlledRunIntent(
            run_id=run_id,
            skill_id=native_descriptor.skill_id,
            skill_version=native_descriptor.version,
            input_schema=native_descriptor.input_schema,
            phase="validated_input",
            raw_parameters_sha256=parameters_sha256,
            effective_parameters_sha256=parameters_sha256,
            dependencies=admission.admission.candidate_intents[0].invocation.dependencies,
            descriptor_sha256=_sha(native_descriptor.model_dump(mode="json")),
            implementation_sha256=admission.admission.implementation_sha256,
            qualification_sha256=admission.admission.qualification.sha256,
            max_attempts=native_descriptor.max_attempts,
        )
    )
    assert projection.binding.application is not None
    with pytest.raises(QualifiedApplicationRunPendingError, match="will not be repeated"):
        projection.binding.application.invoke(
            schema_model(native_descriptor.input_schema).model_validate(parameters),
            run_id=run_id,
        )
    assert authority.read_run_state(run_id) is None


def test_golden_hook_rejects_native_child_implementation_drift(tmp_path: Path) -> None:
    workflow = _four_stage_workflow(tmp_path, replay_implementation_drift=True)
    _, snapshot = _submit_stage(workflow, workflow.started, 0)
    adapter = create_harness_mcp_adapter(
        harness=workflow.harness,
        principal_id=snapshot.principal_id,
        registry_snapshot=workflow.registry,
    )
    workflow.instant[0] = workflow.times[1]
    arguments = {
        "schema_version": "harness.run_command_request.v1",
        "command_id": "95000000-0000-4000-8000-000000000001",
        "workflow_run_id": str(snapshot.workflow_run_id),
        "idempotency_key": "drifted-native-child-001",
        "expected_workflow_revision": snapshot.revision,
        "expected_state_sha256": snapshot.state_sha256,
        "expected_receipt_head_sha256": snapshot.receipt_head.sha256,
        "parameters": workflow.inputs[1].model_dump(mode="json"),
    }
    result = adapter.call_tool(MCPToolCall(name="image2env_replay_v2_0_0", arguments=arguments))
    assert result.is_error is True
    assert "native child" in result.structured_content["message"]
