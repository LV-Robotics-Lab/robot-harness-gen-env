"""Public-seam RED for advisory and prompt-revision workflow records."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import BaseModel

from self_improving.harness import schemas as public_schemas
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.event_journal import SQLiteEventJournal
from self_improving.harness.golden_execution import (
    QualifiedApplicationRunPendingError,
    SkillAuthorizationContext,
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
)
from self_improving.harness.schemas.registry_snapshot import RegistrySnapshot
from self_improving.harness.schemas.workflow import (
    OperationSnapshot,
    RunCommandRequest,
    RunReadRequest,
    RunSnapshot,
    RunStartRequest,
)
from self_improving.harness.schemas.workflow_operation import (
    ReceiptDerivedFactClaimV2,
    ToolResultV2,
)
from self_improving.harness.trusted_state import TrustedWorldState, WorldFact, WorldFactEvidence
from tests.self_improving.harness import test_golden_observation_submit as observe_base

_CONTROL_SCHEMA_NAMES = (
    "AdvisoryRecordCommand",
    "PromptRevisionCommand",
    "ControlCommitSnapshot",
)
_PUBLIC_MODELS_READY = all(hasattr(public_schemas, name) for name in _CONTROL_SCHEMA_NAMES)
_PUBLIC_SEAM_READY = _PUBLIC_MODELS_READY and hasattr(GoldenRunHarness, "submit_control")
_requires_control_seam = pytest.mark.skipif(
    not _PUBLIC_SEAM_READY,
    reason="blocked by the intentional GoldenRunHarness.submit_control RED",
)

_T0 = datetime(2026, 9, 11, 2, tzinfo=timezone.utc)
_SKILL_REF = "tests.evidence@1.0.0"
_WORKFLOW_ID = UUID("71000000-0000-4000-8000-000000000001")
_OBSERVE_CHILD = UUID("72000000-0000-4000-8000-000000000001")
_FAILURE_CHILD = UUID("72000000-0000-4000-8000-000000000002")
_ADVISORY_ID = UUID("73000000-0000-4000-8000-000000000001")
_PROMPT_REVISION_ID = UUID("73000000-0000-4000-8000-000000000002")
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class _Clock:
    def __init__(self, value: datetime = _T0) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


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
    ).encode("utf-8")


def _put_json(
    store: LocalArtifactStore,
    root: Path,
    *,
    name: str,
    value: object,
    schema_version: str,
) -> ArtifactRef:
    source = root / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(_canonical(value))
    return store.put_file(
        source,
        name=name,
        media_type="application/json",
        schema_version=schema_version,
    )


def _put_binary(
    store: LocalArtifactStore,
    root: Path,
    *,
    name: str,
    payload: bytes,
    media_type: str,
) -> ArtifactRef:
    source = root / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(payload)
    return store.put_file(
        source,
        name=name,
        media_type=media_type,
        schema_version=None,
    )


@dataclass(frozen=True)
class _EvidencePolicy:
    store: LocalArtifactStore
    observation: ArtifactRef
    diagnostic: ArtifactRef
    skill_ref: str = _SKILL_REF
    input_model: type[BaseModel] = ArtifactRef
    evidence_policy_id: str = "tests.control_evidence"
    evidence_policy_version: str = "1.0.0"
    evidence_policy_implementation_sha256: str = "9" * 64

    def normalize_parameters(self, parameters: object) -> ArtifactRef:
        return ArtifactRef.model_validate(parameters)

    def authorize(self, state: TrustedWorldState, parameters: BaseModel) -> None:
        assert state.facts
        assert type(parameters) is ArtifactRef

    def authorize_workflow(
        self,
        state: TrustedWorldState,
        parameters: BaseModel,
        context: SkillAuthorizationContext,
    ) -> None:
        assert state.state_sha256 == context.state_ref.sha256 or state.facts
        assert type(parameters) is ArtifactRef

    def claims(self, state: RunState) -> tuple[ReceiptDerivedFactClaimV2, ...]:
        if state.status is not RunStatus.SUCCEEDED:
            return ()
        return (ReceiptDerivedFactClaimV2(key="observation.scene_visible", value=True),)

    def diagnostics(self, state: RunState) -> tuple[ArtifactRef, ...]:
        return (self.diagnostic,) if state.status is RunStatus.BLOCKED else ()

    def fresh_observations(
        self,
        state: RunState,
        invocation: Invocation | None,
    ) -> tuple[WorldFact, ...]:
        if state.status is not RunStatus.SUCCEEDED:
            return ()
        evidence = WorldFactEvidence.model_validate_json(
            self.store.resolve(self.observation).path.read_bytes()
        )
        return (
            WorldFact(
                key=evidence.key,
                value=evidence.value,
                source_kind="fresh_observation",
                source_artifact=self.observation,
                observed_at=evidence.observed_at,
                valid_until=evidence.valid_until,
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
        assert result.diagnostics == self.diagnostics(state)
        assert result.fresh_observations == self.fresh_observations(state, None)
        assert claims == self.claims(state)
        if state.status is RunStatus.SUCCEEDED:
            assert self.observation in supporting
        else:
            assert self.diagnostic in supporting


@dataclass(frozen=True)
class _EvidenceApplication:
    artifact_root: Path
    authority_path: Path
    registry: SkillRegistry
    descriptor: object
    policy: _EvidencePolicy
    skill_ref: str = _SKILL_REF

    @property
    def skills(self) -> tuple[object, ...]:
        return (self.descriptor,)

    def invoke(self, parameters: BaseModel, *, run_id: UUID) -> RunState:
        try:
            return self.registry.invoke_with_run_id(
                "tests.evidence",
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


@dataclass
class _Case:
    store: LocalArtifactStore
    workflow_store: SQLiteGoldenRunStore
    child_store: SQLiteRunStore
    application: _EvidenceApplication
    policy: _EvidencePolicy
    harness: GoldenRunHarness
    clock: _Clock
    objective: ArtifactRef
    observation: ArtifactRef
    diagnostic: ArtifactRef
    predecessor_package: ArtifactRef
    video: ArtifactRef
    frame: ArtifactRef
    frame_manifest: ArtifactRef
    observation_operation: OperationSnapshot
    failure_operation: OperationSnapshot
    observation_result: ToolResultV2
    failure_result: ToolResultV2
    head: RunSnapshot


def _read_model(store: LocalArtifactStore, ref: ArtifactRef, model: type[BaseModel]) -> BaseModel:
    return model.model_validate_json(store.resolve(ref).path.read_bytes())


def _case(tmp_path: Path) -> _Case:
    store = LocalArtifactStore(tmp_path / "cas")
    objective_value = "Use the source video to build a stable robot-policy scene."
    objective = _put_json(
        store,
        tmp_path,
        name="objective.json",
        value=WorldFactEvidence(
            source_kind="user_input",
            key="request.objective",
            value=objective_value,
            observed_at=_T0,
            valid_until=None,
        ),
        schema_version="harness.world_fact_evidence.v1",
    )
    observation = _put_json(
        store,
        tmp_path,
        name="fresh-observation.json",
        value=WorldFactEvidence(
            source_kind="fresh_observation",
            key="observation.scene_visible",
            value=True,
            observed_at=_T0,
            valid_until=_T0 + timedelta(seconds=60),
        ),
        schema_version="harness.fresh_observation.v1",
    )
    diagnostic = _put_json(
        store,
        tmp_path,
        name="diagnostic.json",
        value={"code": "FRAME_ROUTE_REQUIRED", "message": "use a cited source frame"},
        schema_version="harness.failure_diagnostic.v1",
    )
    predecessor_package = _put_json(
        store,
        tmp_path,
        name="predecessor-package.json",
        value={"package_id": "1" * 64, "route_id": "video2env"},
        schema_version="harness.x2env_environment_package.v1",
    )
    video = _put_binary(
        store,
        tmp_path,
        name="source.mp4",
        payload=b"controlled-video-bytes",
        media_type="video/mp4",
    )
    frame = _put_binary(
        store,
        tmp_path,
        name="frame-000007.png",
        payload=_PNG,
        media_type="image/png",
    )
    frame_manifest = _put_json(
        store,
        tmp_path,
        name="frame-sequence.json",
        value={
            "schema_version": "harness.video_frame_sequence.v1",
            "source_video": video.model_dump(mode="json"),
            "frames": [{"index": 7, "artifact": frame.model_dump(mode="json")}],
        },
        schema_version="harness.video_frame_sequence.v1",
    )
    descriptor, entry = observe_base._qualified_fixture(
        store,
        tmp_path,
        skill_id="tests.evidence",
        input_schema="harness.artifact_ref.v1",
        output_schema="harness.artifact_ref.v1",
        implementation_sha256="3" * 64,
    )
    _, image_compile_entry = observe_base._qualified_fixture(
        store,
        tmp_path,
        skill_id="image2env.compile",
        input_schema="harness.image2env_compile_input.v1",
        output_schema="harness.image2env_compile_output.v1",
        implementation_sha256="4" * 64,
    )
    registry_ref = _put_json(
        store,
        tmp_path,
        name="registry.json",
        value=RegistrySnapshot(
            schema_version="harness.registry_snapshot.v1",
            qualified_skills=tuple(
                sorted((entry, image_compile_entry), key=lambda item: item.skill_ref)
            ),
        ),
        schema_version="harness.registry_snapshot.v1",
    )
    policy = _EvidencePolicy(store=store, observation=observation, diagnostic=diagnostic)
    child_store = SQLiteRunStore(tmp_path / "children.sqlite3")
    registry = SkillRegistry(
        artifact_resolver=store,
        dependency_resolver=StaticDependencyResolver({_SKILL_REF: ()}),
        event_sink=SQLiteEventJournal(child_store.path),
        clock=_Clock(),
        run_id_factory=lambda: pytest.fail("Golden must supply the child run id"),
        run_store=child_store,
    )

    def execute(value: ArtifactRef, context: object) -> HandlerResult:
        if value == observation:
            return HandlerResult(output=value, artifacts=(value,))
        artifacts = tuple(
            sorted(
                (diagnostic, predecessor_package, video, frame, frame_manifest),
                key=lambda ref: (ref.sha256, ref.schema_version or "", ref.media_type),
            )
        )
        raise SkillBlocked(
            Blocker(
                code="HARN_ROUTE_REPAIR_REQUIRED",
                message="the failed video route requires an explicit frame fallback",
                stage="diagnose",
                retryable=False,
                details={},
                unknowns=(),
                artifact_refs=artifacts,
            )
        )

    registry.register(descriptor, execute)
    application = _EvidenceApplication(
        artifact_root=store.root,
        authority_path=child_store.path,
        registry=registry,
        descriptor=descriptor,
        policy=policy,
    )
    binding = _create_golden_execution_binding(
        policy=policy,
        application=application,
        child_authority=child_store,
    )
    workflow_store = SQLiteGoldenRunStore(tmp_path / "golden.sqlite3")
    children = iter((_OBSERVE_CHILD, _FAILURE_CHILD))
    clock = _Clock()
    harness = GoldenRunHarness(
        artifact_store=store,
        workflow_store=workflow_store,
        execution_catalog=create_golden_execution_catalog(bindings=(binding,)),
        clock=clock,
        workflow_id_factory=lambda: _WORKFLOW_ID,
        child_run_id_factory=lambda: next(children),
    )
    head = harness.start(
        RunStartRequest(
            schema_version="harness.run_start_request.v1",
            principal_id="tests/golden-control",
            idempotency_key="control-start",
            workspace="ephemeral",
            requested_profile="genesis.robot_policy@1",
            initial_fact_evidence=(objective,),
            registry_snapshot=registry_ref,
        )
    )

    def submit(command_id: UUID, key: str, value: ArtifactRef) -> OperationSnapshot:
        nonlocal head
        operation = harness.submit(
            RunCommandRequest(
                schema_version="harness.run_command_request.v1",
                command_id=command_id,
                principal_id=head.principal_id,
                workflow_run_id=head.workflow_run_id,
                idempotency_key=key,
                expected_workflow_revision=head.revision,
                expected_state_sha256=head.state_sha256,
                expected_receipt_head_sha256=head.receipt_head.sha256,
                skill_ref=_SKILL_REF,
                parameters=value.model_dump(mode="json"),
            )
        )
        head = harness.read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id=head.principal_id,
                workflow_run_id=head.workflow_run_id,
            )
        )
        return operation

    observation_operation = submit(_OBSERVE_CHILD, "fresh-observation", observation)
    failure_operation = submit(_FAILURE_CHILD, "failed-video-route", diagnostic)
    assert observation_operation.tool_result is not None
    assert failure_operation.tool_result is not None
    observation_result = _read_model(store, observation_operation.tool_result, ToolResultV2)
    failure_result = _read_model(store, failure_operation.tool_result, ToolResultV2)
    assert type(observation_result) is ToolResultV2
    assert type(failure_result) is ToolResultV2
    return _Case(
        store=store,
        workflow_store=workflow_store,
        child_store=child_store,
        application=application,
        policy=policy,
        harness=harness,
        clock=clock,
        objective=objective,
        observation=observation,
        diagnostic=diagnostic,
        predecessor_package=predecessor_package,
        video=video,
        frame=frame,
        frame_manifest=frame_manifest,
        observation_operation=observation_operation,
        failure_operation=failure_operation,
        observation_result=observation_result,
        failure_result=failure_result,
        head=head,
    )


def _models() -> tuple[type[BaseModel], type[BaseModel], type[BaseModel]]:
    return tuple(getattr(public_schemas, name) for name in _CONTROL_SCHEMA_NAMES)  # type: ignore[return-value]


def _advisory_command(case: _Case, *, command_id: UUID = _ADVISORY_ID) -> BaseModel:
    advisory_model, _, _ = _models()
    assert case.failure_result.trusted_receipt is not None
    return advisory_model.model_validate(
        {
            "schema_version": "harness.advisory_record_command.v1",
            "command_id": str(command_id),
            "principal_id": case.head.principal_id,
            "workflow_run_id": str(case.head.workflow_run_id),
            "idempotency_key": "diagnosis-001",
            "expected_workflow_revision": case.head.revision,
            "expected_state_sha256": case.head.state_sha256,
            "expected_receipt_head_sha256": case.head.receipt_head.sha256,
            "advisory_kind": "diagnosis",
            "summary": "The video route failed; reuse the cited frame through image2env.",
            "failure_receipt": case.failure_result.trusted_receipt.model_dump(mode="json"),
            "diagnostic_refs": [case.diagnostic.model_dump(mode="json")],
            "observation_refs": [case.observation.model_dump(mode="json")],
            "external_agent_executed": False,
        }
    )


def _prompt_command(case: _Case, advisory: BaseModel) -> BaseModel:
    _, prompt_model, _ = _models()
    revised = "Build the scene from cited frame 7 and preserve the failed package lineage."
    return prompt_model.model_validate(
        {
            "schema_version": "harness.prompt_revision_command.v1",
            "command_id": str(_PROMPT_REVISION_ID),
            "principal_id": case.head.principal_id,
            "workflow_run_id": str(case.head.workflow_run_id),
            "idempotency_key": "prompt-revision-001",
            "expected_workflow_revision": case.head.revision,
            "expected_state_sha256": case.head.state_sha256,
            "expected_receipt_head_sha256": case.head.receipt_head.sha256,
            "source_prompt": case.objective.model_dump(mode="json"),
            "advisory_receipt": advisory.payload.model_dump(mode="json"),
            "failure_receipt": case.failure_result.trusted_receipt.model_dump(mode="json"),
            "predecessor_package": case.predecessor_package.model_dump(mode="json"),
            "patch": {
                "kind": "full_replace",
                "base_text_sha256": hashlib.sha256(
                    "Use the source video to build a stable robot-policy scene.".encode()
                ).hexdigest(),
                "replacement_text": revised,
                "reason": "The failed route requires an explicit frame-derived retry.",
            },
            "fallback": {
                "kind": "video_frame_to_image",
                "source_video": case.video.model_dump(mode="json"),
                "selected_frame": case.frame.model_dump(mode="json"),
                "frame_index": 7,
                "frame_sequence_manifest": case.frame_manifest.model_dump(mode="json"),
            },
            "next_compile_skill_ref": "image2env.compile@1.0.0",
        }
    )


def _submit_advisory(case: _Case) -> tuple[BaseModel, BaseModel]:
    command = _advisory_command(case)
    committed = case.harness.submit_control(command)  # type: ignore[attr-defined]
    case.head = case.harness.read(  # type: ignore[misc]
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=case.head.principal_id,
            workflow_run_id=case.head.workflow_run_id,
        )
    )
    return command, committed


def test_submit_control_models_are_public_and_exact() -> None:
    missing = [name for name in _CONTROL_SCHEMA_NAMES if not hasattr(public_schemas, name)]
    if not hasattr(GoldenRunHarness, "submit_control"):
        missing.append("GoldenRunHarness.submit_control")
    assert not missing, "missing accepted public S1 control seam: " + ", ".join(missing)


@_requires_control_seam
def test_failure_advisory_and_prompt_revision_advance_history_not_world_state(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    state_before = case.head.state_ref
    state_sha_before = case.head.state_sha256
    children_before = case.head.child_runs
    turn_before = case.head.turn_seq
    failure_head = case.head.receipt_head

    _, advisory = _submit_advisory(case)

    assert advisory.control_kind == "advisory"
    assert advisory.base_revision == 2
    assert advisory.committed_revision == 3
    assert advisory.predecessor_receipt == failure_head
    assert case.head.revision == 3
    assert case.head.turn_seq == turn_before + 1
    assert case.head.receipt_head == advisory.receipt
    assert case.head.state_ref == state_before
    assert case.head.state_sha256 == state_sha_before
    assert case.head.child_runs == children_before
    advisory_payload = json.loads(case.store.resolve(advisory.payload).path.read_bytes())
    assert advisory_payload["schema_version"] == "harness.external_agent_advisory_receipt.v1"
    assert advisory_payload["authority"] == "advisory_only"
    assert advisory_payload["grants_physical_validity"] is False
    assert advisory_payload["grants_publishability"] is False
    assert advisory_payload["failure_receipt"] == case.failure_result.trusted_receipt.model_dump(
        mode="json"
    )
    assert advisory_payload["diagnostic_refs"] == [case.diagnostic.model_dump(mode="json")]
    assert advisory_payload["observation_refs"] == [case.observation.model_dump(mode="json")]
    assert advisory_payload["external_agent_executed"] is False

    prompt_command = _prompt_command(case, advisory)
    prompt = case.harness.submit_control(prompt_command)  # type: ignore[attr-defined]
    current = case.harness.read(
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=case.head.principal_id,
            workflow_run_id=case.head.workflow_run_id,
        )
    )
    assert prompt.control_kind == "prompt_revision"
    assert (prompt.base_revision, prompt.committed_revision) == (3, 4)
    assert prompt.predecessor_receipt == advisory.receipt
    assert current.receipt_head == prompt.receipt
    assert current.turn_seq == turn_before + 2
    assert current.state_ref == state_before
    assert current.state_sha256 == state_sha_before
    assert current.child_runs == children_before
    revision = json.loads(case.store.resolve(prompt.payload).path.read_bytes())
    assert revision["schema_version"] == "harness.prompt_revision.v1"
    assert revision["patch"]["kind"] == "full_replace"
    assert revision["patch"]["replacement_text"].startswith("Build the scene from cited frame 7")
    assert revision["fallback"] == {
        "kind": "video_frame_to_image",
        "source_video": case.video.model_dump(mode="json"),
        "selected_frame": case.frame.model_dump(mode="json"),
        "frame_index": 7,
        "frame_sequence_manifest": case.frame_manifest.model_dump(mode="json"),
    }
    assert revision["next_compile_skill_ref"] == "image2env.compile@1.0.0"


@_requires_control_seam
def test_control_submit_is_idempotent_and_reopens_without_a_live_application(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    _, advisory = _submit_advisory(case)
    prompt_command = _prompt_command(case, advisory)
    first = case.harness.submit_control(prompt_command)  # type: ignore[attr-defined]
    terminal = case.harness.read(
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=case.head.principal_id,
            workflow_run_id=case.head.workflow_run_id,
        )
    )
    reopened = GoldenRunHarness(
        artifact_store=case.store,
        workflow_store=case.workflow_store,
        execution_catalog=create_golden_execution_catalog(
            bindings=(
                _create_golden_execution_binding(
                    policy=case.policy,
                    application=None,
                    child_authority=case.child_store,
                ),
            )
        ),
        clock=lambda: pytest.fail("terminal control retry must not read the clock"),
        child_run_id_factory=lambda: pytest.fail("control retry must not reserve a child"),
    )
    assert reopened.submit_control(prompt_command) == first  # type: ignore[attr-defined]
    assert (
        reopened.read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id=case.head.principal_id,
                workflow_run_id=case.head.workflow_run_id,
            )
        )
        == terminal
    )
    changed = prompt_command.model_copy(
        update={"next_compile_skill_ref": "video2env.compile@1.0.0"}
    )
    with pytest.raises(GoldenRunConflictError, match="idempotency"):
        reopened.submit_control(changed)  # type: ignore[attr-defined]


@_requires_control_seam
def test_advisory_rejects_stale_foreign_unbound_and_expired_evidence(tmp_path: Path) -> None:
    case = _case(tmp_path)
    command = _advisory_command(case)
    stale = command.model_copy(update={"expected_workflow_revision": 1})
    with pytest.raises(GoldenRunHeadConflictError):
        case.harness.submit_control(stale)  # type: ignore[attr-defined]
    foreign = command.model_copy(update={"principal_id": "tests/foreign-principal"})
    with pytest.raises(GoldenRunNotFoundError):
        case.harness.submit_control(foreign)  # type: ignore[attr-defined]
    unbound = _put_json(
        case.store,
        tmp_path,
        name="unbound-diagnostic.json",
        value={"unbound": True},
        schema_version="harness.failure_diagnostic.v1",
    )
    unbound_command = command.model_copy(update={"diagnostic_refs": (unbound,)})
    with pytest.raises((GoldenRunNotFoundError, GoldenRunRegistryError), match="reachable|history"):
        case.harness.submit_control(unbound_command)  # type: ignore[attr-defined]

    expired_harness = GoldenRunHarness(
        artifact_store=case.store,
        workflow_store=case.workflow_store,
        execution_catalog=create_golden_execution_catalog(
            bindings=(
                _create_golden_execution_binding(
                    policy=case.policy,
                    application=None,
                    child_authority=case.child_store,
                ),
            )
        ),
        clock=_Clock(_T0 + timedelta(seconds=61)),
    )
    with pytest.raises(GoldenRunRegistryError, match="expired") as error:
        expired_harness.submit_control(command)  # type: ignore[attr-defined]
    assert error.value.code == "HARN_OBSERVATION_EXPIRED"


@_requires_control_seam
def test_prompt_revision_rejects_silent_or_unbound_video_frame_fallback(tmp_path: Path) -> None:
    case = _case(tmp_path)
    _, advisory = _submit_advisory(case)
    command = _prompt_command(case, advisory)
    silent = command.model_copy(update={"fallback": {"kind": "none"}})
    with pytest.raises(GoldenRunRegistryError, match="explicit.*fallback"):
        case.harness.submit_control(silent)  # type: ignore[attr-defined]
    foreign_frame = _put_binary(
        case.store,
        tmp_path,
        name="foreign-frame.png",
        payload=_PNG,
        media_type="image/png",
    )
    bad_fallback = {
        **command.fallback.model_dump(mode="json"),
        "selected_frame": foreign_frame.model_dump(mode="json"),
    }
    unbound = command.model_copy(update={"fallback": bad_fallback})
    with pytest.raises((GoldenRunNotFoundError, GoldenRunRegistryError), match="frame|lineage"):
        case.harness.submit_control(unbound)  # type: ignore[attr-defined]


@_requires_control_seam
def test_reopen_fails_closed_when_a_control_payload_is_tampered(tmp_path: Path) -> None:
    case = _case(tmp_path)
    _, advisory = _submit_advisory(case)
    prompt = case.harness.submit_control(_prompt_command(case, advisory))  # type: ignore[attr-defined]
    case.store.resolve(prompt.payload).path.write_bytes(b"{}\n")
    reopened = GoldenRunHarness(
        artifact_store=case.store,
        workflow_store=case.workflow_store,
        execution_catalog=create_golden_execution_catalog(
            bindings=(
                _create_golden_execution_binding(
                    policy=case.policy,
                    application=None,
                    child_authority=case.child_store,
                ),
            )
        ),
    )
    with pytest.raises(GoldenRunCorruptionError, match="CAS closure|control"):
        reopened.read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id=case.head.principal_id,
                workflow_run_id=case.head.workflow_run_id,
            )
        )
