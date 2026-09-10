"""Evidence-policy tests for the controlled Genesis observation candidate."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.golden_observe_policy import (
    ControlledGenesisObserveEvidencePolicy,
)
from self_improving.harness.schemas.common import (
    ArtifactRef,
    Blocker,
    Event,
    Invocation,
    RunState,
    RunStatus,
)
from self_improving.harness.schemas.genesis_observe import (
    GENESIS_OBSERVATION_RECEIPT_SCHEMA_ID,
    GenesisObservationCapturePlan,
    GenesisObservationReceipt,
    GenesisObservationValue,
    GenesisObserveInput,
    GenesisObserveOutput,
)
from self_improving.harness.schemas.workflow_operation import (
    QualifiedSkillExecutionV2,
    ReceiptDerivedFactClaimV2,
    ToolResultV2,
    TrustedToolReceiptV2,
    WorkflowOperationReceiptV2,
)
from self_improving.harness.schemas.x2env import (
    Image2EnvReplayInput,
    Image2EnvReplayOutput,
    Video2EnvReplayInput,
    Video2EnvReplayOutput,
    X2EnvEnvironmentPackage,
)
from self_improving.harness.trusted_state import TrustedWorldState, WorldFact, build_world_state

T0 = datetime(2026, 9, 11, 2, 0, tzinfo=timezone.utc)
REPLAY_RUN_ID = UUID("71000000-0000-4000-8000-000000000001")
OBSERVE_RUN_ID = UUID("71000000-0000-4000-8000-000000000002")
WORKFLOW_ID = UUID("71000000-0000-4000-8000-000000000003")
REPLAY_OPERATION_ID = UUID("71000000-0000-4000-8000-000000000004")
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


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
    schema: str | None,
    media_type: str = "application/json",
    canonical: bool = True,
) -> ArtifactRef:
    source = root / f"source-{name}"
    payload = (
        value
        if isinstance(value, bytes)
        else _canonical(value)
        if canonical
        else json.dumps(value, indent=2).encode()
    )
    source.write_bytes(payload)
    return store.put_file(source, name=name, media_type=media_type, schema_version=schema)


def _fake_ref(
    name: str,
    schema: str | None,
    digest: str,
    *,
    media_type: str = "application/json",
) -> ArtifactRef:
    return ArtifactRef(
        name=name,
        uri=f"artifact://sha256/{digest}",
        media_type=media_type,
        sha256=digest,
        bytes=1,
        schema_version=schema,
    )


def _package_value(route: str, *, package_id: str = SHA_D) -> dict[str, object]:
    source = {
        "schema_version": "harness.image_source_media.v1",
        "source_media_sha256": SHA_A,
        "source": _fake_ref("source.jpg", None, SHA_A, media_type="image/jpeg").model_dump(
            mode="json"
        ),
        "canonical_image": _fake_ref(
            "source.png", None, SHA_B, media_type="image/png"
        ).model_dump(mode="json"),
        "probe": _fake_ref("probe.json", "harness.image_media_probe.v1", SHA_C).model_dump(
            mode="json"
        ),
        "width": 16,
        "height": 16,
        "color_mode": "RGB",
        "decoded_frame_count": 1,
        "unique_frame_count": 1,
    }
    if route == "video2env":
        source = {
            "schema_version": "harness.video_source_media.v1",
            "source_media_sha256": SHA_A,
            "source": _fake_ref("source.mp4", None, SHA_A, media_type="video/mp4").model_dump(
                mode="json"
            ),
            "probe": _fake_ref("probe.json", "harness.video_media_probe.v1", SHA_B).model_dump(
                mode="json"
            ),
            "frame_sequence_manifest": _fake_ref(
                "frames.json", "harness.video_frame_sequence.v1", SHA_C
            ).model_dump(mode="json"),
            "width": 16,
            "height": 16,
            "duration_ms": 1000,
            "fps_numerator": 12,
            "fps_denominator": 1,
            "decoded_frame_count": 12,
            "unique_frame_count": 10,
            "frame_sequence_sha256": SHA_D,
        }
    return {
        "schema_version": "harness.x2env_environment_package.v1",
        "package_id": package_id,
        "route_id": route,
        "producer_skill_ref": f"{route}.compile@1.0.0",
        "source_media": source,
        "requested_profile": "genesis.rigid_scene@1",
        "environment_format": "harness.genesis_portable.v1",
        "backend": "genesis",
        "representation_origin": "harness_native",
        "legacy_adapter_receipt": None,
        "scene_ir": _fake_ref("scene.json", "harness.scene_ir.v1", SHA_A).model_dump(mode="json"),
        "task_ir": _fake_ref("task.json", "harness.task_ir.v1", SHA_B).model_dump(mode="json"),
        "asset_closure_manifest": _fake_ref(
            "assets.json", "harness.asset_closure_manifest.v1", SHA_C
        ).model_dump(mode="json"),
        "binding_manifest": _fake_ref(
            "bindings.json", "harness.x2env_binding_manifest.v1", SHA_D
        ).model_dump(mode="json"),
        "runtime_lock": _fake_ref(
            "runtime-lock.json", "harness.genesis_runtime_lock.v1", SHA_A
        ).model_dump(mode="json"),
        "simulator_ready": False,
        "collision_qualified": False,
    }


def _replay_input(route: str, package: X2EnvEnvironmentPackage) -> BaseModel:
    model = Image2EnvReplayInput if route == "image2env" else Video2EnvReplayInput
    return model(
        schema_version=f"harness.{route}_replay_input.v1",
        environment_package=package,
        compile_receipt=_fake_ref(
            "compile.json", "harness.x2env_compile_receipt.v1", SHA_A
        ),
        requested_profile="genesis.rigid_scene@1",
        runtime_attestation=_fake_ref(
            "attestation.json", "harness.genesis_runtime_attestation.v1", SHA_B
        ),
        seed=17,
        physics_plan={
            "collision_enabled": True,
            "settle_steps": 2,
            "observation_steps": 2,
            "video_frames": 2,
            "fps": 2,
            "robot_probe_steps": 0,
        },
    )


def _replay_output(
    route: str,
    package: X2EnvEnvironmentPackage,
    runtime_receipt: ArtifactRef,
) -> BaseModel:
    model = Image2EnvReplayOutput if route == "image2env" else Video2EnvReplayOutput
    return model(
        schema_version=f"harness.{route}_replay_output.v1",
        environment_package=package,
        package_id=package.package_id,
        requested_profile="genesis.rigid_scene@1",
        runtime_evidence=_fake_ref(
            "runtime-evidence.json", "harness.genesis_runtime_evidence.v1", SHA_A
        ),
        physics_report=_fake_ref(
            "physics.json", "harness.genesis_physics_report.v1", SHA_B
        ),
        robot_probe_report=None,
        trajectory=None,
        video=_fake_ref("replay.mp4", None, SHA_C, media_type="video/mp4"),
        frame_sequence_manifest=_fake_ref(
            "frames.json", "harness.video_frame_sequence.v1", SHA_D
        ),
        media_verification=_fake_ref(
            "media.json", "harness.x2env_media_verification.v1", SHA_A
        ),
        runtime_receipt=runtime_receipt,
        fresh_observation_refs=(
            _fake_ref("fresh.json", "harness.fresh_observation.v1", SHA_C),
        ),
        steps_executed=4,
        total_frame_count=2,
        unique_frame_count=2,
        collisions_enabled=True,
        simulator_executed=True,
    )


def _event(
    seq: int,
    timestamp: datetime,
    from_status: RunStatus | None,
    to_status: RunStatus,
) -> Event:
    return Event(
        seq=seq,
        timestamp=timestamp,
        stage="execute",
        attempt=1,
        from_status=from_status,
        to_status=to_status,
        artifact_refs=(),
    )


def _run_state(
    *,
    run_id: UUID,
    skill_id: str,
    output: BaseModel,
    artifacts: tuple[ArtifactRef, ...],
    started_at: datetime,
    ended_at: datetime,
    digest: str,
) -> RunState:
    return RunState(
        run_id=run_id,
        invocation_digest=digest,
        skill_id=skill_id,
        skill_version="1.0.0",
        status="succeeded",
        attempt=1,
        max_attempts=1,
        started_at=started_at,
        ended_at=ended_at,
        events=(
            _event(1, started_at, None, RunStatus.RUNNING),
            _event(2, ended_at, RunStatus.RUNNING, RunStatus.SUCCEEDED),
        ),
        artifacts=artifacts,
        output=output.model_dump(mode="json"),
        blocker=None,
    )


@dataclass(frozen=True)
class _Case:
    root: Path
    store: LocalArtifactStore
    policy: ControlledGenesisObserveEvidencePolicy
    world: TrustedWorldState
    request: GenesisObserveInput
    replay_receipt: TrustedToolReceiptV2
    replay_workflow: WorkflowOperationReceiptV2
    observation_receipt: GenesisObservationReceipt
    observation_receipt_ref: ArtifactRef
    output: GenesisObserveOutput
    invocation: Invocation
    invocation_ref: ArtifactRef
    state: RunState
    result: ToolResultV2


def _case(tmp_path: Path, *, route: str = "image2env", checkpoint: bool = False) -> _Case:
    store = LocalArtifactStore(tmp_path / "cas")
    policy = ControlledGenesisObserveEvidencePolicy(store)
    world = build_world_state((), as_of=T0)
    world_ref = _put(
        store,
        tmp_path,
        name="world.json",
        value=world,
        schema="harness.trusted_world_state.v1",
    )
    package = X2EnvEnvironmentPackage.model_validate(_package_value(route))
    package_ref = _put(
        store,
        tmp_path,
        name="package.json",
        value=package,
        schema="harness.x2env_environment_package.v1",
    )
    runtime_ref = _put(
        store,
        tmp_path,
        name="runtime.json",
        value={"runtime": "controlled-fixture"},
        schema="harness.x2env_runtime_receipt.v1",
    )
    checkpoint_ref = (
        _put(
            store,
            tmp_path,
            name="checkpoint.json",
            value={"checkpoint": "controlled-fixture"},
            schema="harness.genesis_checkpoint.v1",
        )
        if checkpoint
        else None
    )
    replay_input = _replay_input(route, package)
    replay_output = _replay_output(route, package, runtime_ref)
    replay_digest = "1" * 64
    replay_invocation = Invocation(
        run_id=REPLAY_RUN_ID,
        skill_id=f"{route}.replay",
        skill_version="1.0.0",
        effective_parameters=replay_input.model_dump(mode="json"),
        dependencies=(),
        max_attempts=1,
        invocation_digest=replay_digest,
    )
    replay_invocation_ref = _put(
        store,
        tmp_path,
        name="replay-invocation.json",
        value=replay_invocation,
        schema="harness.skill_invocation.v1",
    )
    replay_artifacts = tuple(
        sorted(
            (runtime_ref,) if checkpoint_ref is None else (runtime_ref, checkpoint_ref),
            key=lambda ref: (ref.sha256, ref.schema_version or "", ref.media_type),
        )
    )
    replay_state = _run_state(
        run_id=REPLAY_RUN_ID,
        skill_id=f"{route}.replay",
        output=replay_output,
        artifacts=replay_artifacts,
        started_at=T0 - timedelta(seconds=3),
        ended_at=T0 - timedelta(seconds=2),
        digest=replay_digest,
    )
    replay_state_ref = _put(
        store,
        tmp_path,
        name="replay-state.json",
        value=replay_state,
        schema="harness.run_state.v1",
    )
    replay_receipt = TrustedToolReceiptV2(
        schema_version="harness.trusted_tool_receipt.v2",
        workflow_run_id=WORKFLOW_ID,
        operation_id=REPLAY_OPERATION_ID,
        principal_id="tests/observation-policy",
        command=_fake_ref("command.json", "harness.run_command_request.v1", SHA_A),
        base_revision=1,
        base_state_sha256=SHA_A,
        base_state=_fake_ref("base.json", "harness.trusted_world_state.v1", SHA_B),
        registry_snapshot=_fake_ref(
            "registry.json", "harness.registry_snapshot.v1", SHA_C
        ),
        skill=QualifiedSkillExecutionV2(
            schema_version="harness.qualified_skill_execution.v2",
            skill_ref=f"{route}.replay@1.0.0",
            descriptor=_fake_ref("descriptor.json", "harness.skill_descriptor.v2", SHA_A),
            input_schema=f"harness.{route}_replay_input.v1",
            output_schema=f"harness.{route}_replay_output.v1",
            implementation_sha256=SHA_A,
            qualification_artifact=_fake_ref(
                "qualification.json", "harness.skill_qualification.v1", SHA_B
            ),
            qualification_report=_fake_ref(
                "qualification-report.json", "harness.skill_qualification_report.v1", SHA_C
            ),
            max_attempts=1,
            evidence_policy_id=f"{route}.replay.evidence",
            evidence_policy_version="1.0.0",
            evidence_policy_implementation_sha256=SHA_D,
        ),
        status="succeeded",
        started_at=replay_state.started_at,
        ended_at=replay_state.ended_at,
        child_run_id=REPLAY_RUN_ID,
        invocation=replay_invocation_ref,
        run_state=replay_state_ref,
        invocation_digest=replay_digest,
        typed_output=replay_output.model_dump(mode="json"),
        blocker=None,
        supporting_artifacts=replay_artifacts,
        derived_fact_claims=(),
    )
    replay_receipt_ref = _put(
        store,
        tmp_path,
        name="replay-receipt.json",
        value=replay_receipt,
        schema="harness.trusted_tool_receipt.v2",
    )
    replay_workflow = WorkflowOperationReceiptV2(
        schema_version="harness.workflow_operation_receipt.v2",
        workflow_run_id=WORKFLOW_ID,
        operation_id=REPLAY_OPERATION_ID,
        principal_id="tests/observation-policy",
        command=_fake_ref("command.json", "harness.run_command_request.v1", SHA_A),
        predecessor_receipt=_fake_ref(
            "previous.json", "harness.workflow_operation_receipt.v2", SHA_B
        ),
        tool_receipt=replay_receipt_ref,
        tool_result=_fake_ref("result.json", "harness.tool_result.v2", SHA_C),
        state_delta=_fake_ref("delta.json", "harness.state_delta.v2", SHA_D),
        base_revision=1,
        committed_revision=2,
        base_state_sha256=SHA_A,
        resulting_state_sha256=world.state_sha256,
        resulting_state=world_ref,
        status="succeeded",
        committed_at=T0 - timedelta(seconds=1),
    )
    workflow_ref = _put(
        store,
        tmp_path,
        name="workflow.json",
        value=replay_workflow,
        schema="harness.workflow_operation_receipt.v2",
    )
    request = GenesisObserveInput(
        schema_version="harness.genesis_observe_input.v1",
        environment_package=package_ref,
        replay_workflow_receipt=workflow_ref,
        replay_runtime_receipt=runtime_ref,
        checkpoint=checkpoint_ref,
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
    image_ref = _put(
        store,
        tmp_path,
        name="observation.png",
        value=b"controlled-png",
        schema=None,
        media_type="image/png",
    )
    verification_ref = _put(
        store,
        tmp_path,
        name="verification.json",
        value={"fully_decoded": True, "simulator_executed": False},
        schema="harness.genesis_observation_media_verification.v1",
    )
    captured_at = T0 + timedelta(seconds=1)
    valid_until = captured_at + timedelta(seconds=60)
    observation_receipt = GenesisObservationReceipt(
        schema_version=GENESIS_OBSERVATION_RECEIPT_SCHEMA_ID,
        observation_child_run_id=OBSERVE_RUN_ID,
        environment_package=request.environment_package,
        replay_workflow_receipt=request.replay_workflow_receipt,
        replay_runtime_receipt=request.replay_runtime_receipt,
        checkpoint=request.checkpoint,
        capture_plan=request.capture_plan,
        requested_observation_keys=request.requested_observation_keys,
        captured_at=captured_at,
        valid_until=valid_until,
        ttl_seconds=request.ttl_seconds,
        images=(image_ref,),
        video=None,
        media_verification=verification_ref,
        observations=(
            GenesisObservationValue(
                key="observation.scene_visible",
                value=True,
                captured_at=captured_at,
                valid_until=valid_until,
                payload_refs=(image_ref,),
            ),
        ),
        simulator_executed=False,
        post_restore_steps_executed=0,
    )
    observation_receipt_ref = _put(
        store,
        tmp_path,
        name="observation-receipt.json",
        value=observation_receipt,
        schema=GENESIS_OBSERVATION_RECEIPT_SCHEMA_ID,
    )
    output = GenesisObserveOutput(
        schema_version="harness.genesis_observe_output.v1",
        observation_receipt=observation_receipt_ref,
        images=observation_receipt.images,
        video=None,
        media_verification=verification_ref,
        captured_at=captured_at,
        valid_until=valid_until,
        simulator_executed=False,
    )
    observe_digest = "2" * 64
    invocation = Invocation(
        run_id=OBSERVE_RUN_ID,
        skill_id="genesis.observe",
        skill_version="1.0.0",
        effective_parameters=request.model_dump(mode="json"),
        dependencies=(),
        max_attempts=1,
        invocation_digest=observe_digest,
    )
    invocation_ref = _put(
        store,
        tmp_path,
        name="observe-invocation.json",
        value=invocation,
        schema="harness.skill_invocation.v1",
    )
    state = _run_state(
        run_id=OBSERVE_RUN_ID,
        skill_id="genesis.observe",
        output=output,
        artifacts=(observation_receipt_ref, image_ref, verification_ref),
        started_at=T0,
        ended_at=T0 + timedelta(seconds=2),
        digest=observe_digest,
    )
    state_ref = _put(
        store,
        tmp_path,
        name="observe-state.json",
        value=state,
        schema="harness.run_state.v1",
    )
    claims = policy.claims(state)
    observations = policy.fresh_observations(state, invocation)
    result = ToolResultV2(
        schema_version="harness.tool_result.v2",
        workflow_run_id=WORKFLOW_ID,
        operation_id=UUID("71000000-0000-4000-8000-000000000005"),
        skill_ref="genesis.observe@1.0.0",
        status="succeeded",
        started_at=state.started_at,
        ended_at=state.ended_at,
        child_run_id=state.run_id,
        invocation=invocation_ref,
        run_state=state_ref,
        typed_output=state.output,
        state_delta=_fake_ref("observe-delta.json", "harness.state_delta.v2", SHA_A),
        diagnostics=(),
        trusted_receipt=_fake_ref(
            "observe-receipt.json", "harness.trusted_tool_receipt.v2", SHA_B
        ),
        fresh_observations=observations,
        blocker=None,
    )
    assert claims
    return _Case(
        root=tmp_path,
        store=store,
        policy=policy,
        world=world,
        request=request,
        replay_receipt=replay_receipt,
        replay_workflow=replay_workflow,
        observation_receipt=observation_receipt,
        observation_receipt_ref=observation_receipt_ref,
        output=output,
        invocation=invocation,
        invocation_ref=invocation_ref,
        state=state,
        result=result,
    )


def _publish_receipt(
    case: _Case,
    receipt: GenesisObservationReceipt,
) -> tuple[RunState, Invocation]:
    ref = _put(
        case.store,
        case.root,
        name=f"altered-observation-{receipt.observation_child_run_id}.json",
        value=receipt,
        schema=GENESIS_OBSERVATION_RECEIPT_SCHEMA_ID,
    )
    output = case.output.model_copy(
        update={
            "observation_receipt": ref,
            "images": receipt.images,
            "video": receipt.video,
            "media_verification": receipt.media_verification,
            "captured_at": receipt.captured_at,
            "valid_until": receipt.valid_until,
            "simulator_executed": receipt.simulator_executed,
        }
    )
    required = (
        ref,
        *receipt.images,
        *(x for x in (receipt.video,) if x),
        receipt.media_verification,
    )
    return (
        case.state.model_copy(
            update={"output": output.model_dump(mode="json"), "artifacts": required}
        ),
        case.invocation,
    )


def test_policy_metadata_constructor_and_strict_normalization(tmp_path: Path) -> None:
    case = _case(tmp_path)
    assert case.policy.skill_ref == "genesis.observe@1.0.0"
    assert case.policy.input_model is GenesisObserveInput
    assert case.policy.simulator_executed is False
    assert len(case.policy.evidence_policy_implementation_sha256) == 64
    assert case.policy.normalize_parameters(case.request.model_dump(mode="json")) == case.request
    with pytest.raises(ValidationError):
        case.policy.normalize_parameters(
            case.request.model_dump(mode="json") | {"ttl_seconds": "60"}
        )

    class _DerivedStore(LocalArtifactStore):
        pass

    with pytest.raises(TypeError, match="exact LocalArtifactStore"):
        ControlledGenesisObserveEvidencePolicy(_DerivedStore(tmp_path / "derived"))
    with pytest.raises(TypeError, match="GenesisObserveInput"):
        case.policy.authorize(case.world, case.request.capture_plan)


@pytest.mark.parametrize("route", ["image2env", "video2env"])
@pytest.mark.parametrize("checkpoint", [False, True])
def test_authorize_closes_exact_image_or_video_replay_lineage(
    tmp_path: Path,
    route: str,
    checkpoint: bool,
) -> None:
    case = _case(tmp_path, route=route, checkpoint=checkpoint)
    case.policy.authorize(case.world, case.request)


def _replace_replay_receipt(case: _Case, receipt: TrustedToolReceiptV2) -> GenesisObserveInput:
    receipt_ref = _put(
        case.store,
        case.root,
        name="altered-replay-receipt.json",
        value=receipt,
        schema="harness.trusted_tool_receipt.v2",
    )
    workflow = case.replay_workflow.model_copy(update={"tool_receipt": receipt_ref})
    workflow_ref = _put(
        case.store,
        case.root,
        name="altered-workflow.json",
        value=workflow,
        schema="harness.workflow_operation_receipt.v2",
    )
    return case.request.model_copy(update={"replay_workflow_receipt": workflow_ref})


def _replace_replay_supporting(
    case: _Case, supporting: tuple[ArtifactRef, ...]
) -> TrustedToolReceiptV2:
    replay_state = RunState.model_validate_json(
        case.store.resolve(case.replay_receipt.run_state).path.read_bytes()
    ).model_copy(update={"artifacts": supporting})
    state_ref = _put(
        case.store,
        case.root,
        name="altered-replay-state.json",
        value=replay_state,
        schema="harness.run_state.v1",
    )
    return case.replay_receipt.model_copy(
        update={"run_state": state_ref, "supporting_artifacts": supporting}
    )


def test_authorize_rejects_noncanonical_or_noncurrent_workflow(tmp_path: Path) -> None:
    case = _case(tmp_path)
    wrong_hash = case.replay_workflow.model_copy(update={"resulting_state_sha256": SHA_A})
    wrong_ref = _put(
        case.store,
        tmp_path,
        name="wrong-hash-workflow.json",
        value=wrong_hash,
        schema="harness.workflow_operation_receipt.v2",
    )
    with pytest.raises(ValueError, match="current state"):
        case.policy.authorize(
            case.world, case.request.model_copy(update={"replay_workflow_receipt": wrong_ref})
        )
    noncanonical_ref = _put(
        case.store,
        tmp_path,
        name="noncanonical-workflow.json",
        value=case.replay_workflow.model_dump(mode="json"),
        schema="harness.workflow_operation_receipt.v2",
        canonical=False,
    )
    with pytest.raises(ValueError, match="not canonical"):
        case.policy.authorize(
            case.world,
            case.request.model_copy(update={"replay_workflow_receipt": noncanonical_ref}),
        )

    other_world = build_world_state((), as_of=T0 + timedelta(seconds=1))
    other_ref = _put(
        case.store,
        tmp_path,
        name="other-world.json",
        value=other_world,
        schema="harness.trusted_world_state.v1",
    )
    wrong_state_ref = case.replay_workflow.model_copy(update={"resulting_state": other_ref})
    wrong_ref = _put(
        case.store,
        tmp_path,
        name="wrong-state-ref-workflow.json",
        value=wrong_state_ref,
        schema="harness.workflow_operation_receipt.v2",
    )
    with pytest.raises(ValueError, match="resulting state"):
        case.policy.authorize(
            case.world, case.request.model_copy(update={"replay_workflow_receipt": wrong_ref})
        )


def test_authorize_rejects_wrong_skill_and_open_replay_child_closure(tmp_path: Path) -> None:
    case = _case(tmp_path)
    wrong_skill = case.replay_receipt.model_copy(
        update={
            "skill": case.replay_receipt.skill.model_copy(
                update={"skill_ref": "text2env.replay@1.0.0"}
            )
        }
    )
    with pytest.raises(ValueError, match="image2env or video2env"):
        case.policy.authorize(case.world, _replace_replay_receipt(case, wrong_skill))
    wrong_schema = case.replay_receipt.model_copy(
        update={
            "skill": case.replay_receipt.skill.model_copy(
                update={"output_schema": "harness.video2env_replay_output.v1"}
            )
        }
    )
    with pytest.raises(ValueError, match="wrong exact schemas"):
        case.policy.authorize(case.world, _replace_replay_receipt(case, wrong_schema))
    mismatches = (
        {"operation_id": UUID("71000000-0000-4000-8000-000000000099")},
        {"child_run_id": UUID("71000000-0000-4000-8000-000000000098")},
        {"invocation_digest": "f" * 64},
        {"typed_output": case.replay_receipt.typed_output | {"steps_executed": 5}},
        {"supporting_artifacts": ()},
    )
    for update in mismatches:
        with pytest.raises(ValueError, match="one success|close its invocation"):
            case.policy.authorize(
                case.world,
                _replace_replay_receipt(case, case.replay_receipt.model_copy(update=update)),
            )


def test_authorize_rejects_package_runtime_and_checkpoint_mismatches(tmp_path: Path) -> None:
    case = _case(tmp_path, checkpoint=True)
    other_package = X2EnvEnvironmentPackage.model_validate(
        _package_value("image2env", package_id="e" * 64)
    )
    other_package_ref = _put(
        case.store,
        tmp_path,
        name="other-package.json",
        value=other_package,
        schema="harness.x2env_environment_package.v1",
    )
    other_runtime = _put(
        case.store,
        tmp_path,
        name="other-runtime.json",
        value={"runtime": "other"},
        schema="harness.x2env_runtime_receipt.v1",
    )
    for update, message in (
        ({"environment_package": other_package_ref}, "exact replay input and output"),
        ({"replay_runtime_receipt": other_runtime}, "exact replay input and output"),
    ):
        with pytest.raises(ValueError, match=message):
            case.policy.authorize(case.world, case.request.model_copy(update=update))
    assert case.request.checkpoint is not None
    missing_checkpoint = _replace_replay_supporting(
        case,
        tuple(
            ref
            for ref in case.replay_receipt.supporting_artifacts
            if ref != case.request.checkpoint
        ),
    )
    with pytest.raises(ValueError, match="checkpoint"):
        case.policy.authorize(case.world, _replace_replay_receipt(case, missing_checkpoint))
    missing_runtime = _replace_replay_supporting(
        case,
        tuple(
            ref
            for ref in case.replay_receipt.supporting_artifacts
            if ref != case.request.replay_runtime_receipt
        ),
    )
    with pytest.raises(ValueError, match="supporting evidence|close its invocation"):
        case.policy.authorize(case.world, _replace_replay_receipt(case, missing_runtime))


def test_claims_and_fresh_observations_come_from_one_canonical_receipt(tmp_path: Path) -> None:
    case = _case(tmp_path)
    claims = case.policy.claims(case.state)
    fresh = case.policy.fresh_observations(case.state, case.invocation)
    assert claims == (
        ReceiptDerivedFactClaimV2(key="observation.scene_visible", value=True),
    )
    assert fresh == (
        WorldFact(
            key="observation.scene_visible",
            value=True,
            source_kind="fresh_observation",
            source_artifact=case.observation_receipt_ref,
            observed_at=T0 + timedelta(seconds=1),
            valid_until=T0 + timedelta(seconds=61),
        ),
    )


def test_fresh_observations_reject_invocation_identity_and_input_mismatches(tmp_path: Path) -> None:
    case = _case(tmp_path)
    with pytest.raises(ValueError, match="omitted its invocation"):
        case.policy.fresh_observations(case.state, None)
    for update in (
        {"run_id": UUID("71000000-0000-4000-8000-000000000099")},
        {"skill_id": "image2env.replay"},
        {"skill_version": "2.0.0"},
        {"invocation_digest": "f" * 64},
    ):
        with pytest.raises(ValueError, match="invocation differs"):
            case.policy.fresh_observations(
                case.state, case.invocation.model_copy(update=update)
            )

    alternate = _put(
        case.store,
        tmp_path,
        name="alternate-runtime.json",
        value={"alternate": True},
        schema="harness.x2env_runtime_receipt.v1",
    )
    altered_input = case.request.model_copy(update={"replay_runtime_receipt": alternate})
    altered_invocation = case.invocation.model_copy(
        update={"effective_parameters": altered_input.model_dump(mode="json")}
    )
    with pytest.raises(ValueError, match="exact invocation input"):
        case.policy.fresh_observations(case.state, altered_invocation)


def test_fresh_observations_recheck_every_receipt_request_field(tmp_path: Path) -> None:
    case = _case(tmp_path)
    alternate_package = _put(
        case.store,
        tmp_path,
        name="alternate-package-ref.json",
        value=X2EnvEnvironmentPackage.model_validate(
            _package_value("image2env", package_id="e" * 64)
        ),
        schema="harness.x2env_environment_package.v1",
    )
    alternate_workflow = case.request.replay_workflow_receipt.model_copy(
        update={"name": "same-workflow-other-name.json"}
    )
    alternate_runtime = case.request.replay_runtime_receipt.model_copy(
        update={"name": "same-runtime-other-name.json"}
    )
    checkpoint = _put(
        case.store,
        tmp_path,
        name="observation-checkpoint.json",
        value={"checkpoint": True},
        schema="harness.genesis_checkpoint.v1",
    )
    plan = GenesisObservationCapturePlan(
        camera_ids=("observer",),
        stills_per_camera=1,
        video_frames=0,
        fps=None,
        post_restore_steps=2,
    )
    keys = ("observation.other",)
    variants = (
        {"environment_package": alternate_package},
        {"replay_workflow_receipt": alternate_workflow},
        {"replay_runtime_receipt": alternate_runtime},
        {"checkpoint": checkpoint},
        {"capture_plan": plan},
        {
            "requested_observation_keys": keys,
            "observations": (
                case.observation_receipt.observations[0].model_copy(update={"key": keys[0]}),
            ),
        },
        {
            "ttl_seconds": 61,
            "valid_until": case.observation_receipt.captured_at + timedelta(seconds=61),
            "observations": (
                case.observation_receipt.observations[0].model_copy(
                    update={
                        "valid_until": case.observation_receipt.captured_at
                        + timedelta(seconds=61)
                    }
                ),
            ),
        },
    )
    for update in variants:
        receipt = GenesisObservationReceipt.model_validate(
            case.observation_receipt.model_copy(update=update).model_dump(mode="python")
        )
        state, invocation = _publish_receipt(case, receipt)
        with pytest.raises(ValueError, match="exact invocation input"):
            case.policy.fresh_observations(state, invocation)


def test_fresh_observations_reject_open_child_output_time_and_supporting_closure(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    wrong_child = case.observation_receipt.model_copy(
        update={"observation_child_run_id": UUID("71000000-0000-4000-8000-000000000099")}
    )
    state, invocation = _publish_receipt(case, wrong_child)
    with pytest.raises(ValueError, match="output differs"):
        case.policy.fresh_observations(state, invocation)

    other_image = _put(
        case.store,
        tmp_path,
        name="other.png",
        value=b"other",
        schema=None,
        media_type="image/png",
    )
    bad_output = case.output.model_copy(update={"images": (other_image,)})
    bad_state = case.state.model_copy(
        update={
            "output": bad_output.model_dump(mode="json"),
            "artifacts": (*case.state.artifacts, other_image),
        }
    )
    with pytest.raises(ValueError, match="output differs"):
        case.policy.fresh_observations(bad_state, case.invocation)

    outside = case.observation_receipt.model_copy(
        update={
            "captured_at": T0 - timedelta(seconds=1),
            "valid_until": T0 + timedelta(seconds=59),
            "observations": (
                case.observation_receipt.observations[0].model_copy(
                    update={
                        "captured_at": T0 - timedelta(seconds=1),
                        "valid_until": T0 + timedelta(seconds=59),
                    }
                ),
            ),
        }
    )
    state, invocation = _publish_receipt(case, outside)
    with pytest.raises(ValueError, match="execution window"):
        case.policy.fresh_observations(state, invocation)

    missing_media = case.state.model_copy(
        update={
            "artifacts": tuple(
                ref for ref in case.state.artifacts if ref != case.output.images[0]
            )
        }
    )
    with pytest.raises(ValueError, match="absent from supporting"):
        case.policy.fresh_observations(missing_media, case.invocation)


def test_controlled_policy_rejects_simulator_claim_and_noncanonical_receipt(tmp_path: Path) -> None:
    case = _case(tmp_path)
    runtime_receipt = GenesisObservationReceipt.model_validate(
        case.observation_receipt.model_copy(
            update={
                "simulator_executed": True,
                "post_restore_steps_executed": 1,
            }
        ).model_dump(mode="python")
    )
    state, invocation = _publish_receipt(case, runtime_receipt)
    with pytest.raises(ValueError, match="cannot claim simulator"):
        case.policy.fresh_observations(state, invocation)

    noncanonical_ref = _put(
        case.store,
        tmp_path,
        name="noncanonical-observation.json",
        value=case.observation_receipt.model_dump(mode="json"),
        schema=GENESIS_OBSERVATION_RECEIPT_SCHEMA_ID,
        canonical=False,
    )
    output = case.output.model_copy(update={"observation_receipt": noncanonical_ref})
    state = case.state.model_copy(
        update={
            "output": output.model_dump(mode="json"),
            "artifacts": (noncanonical_ref, *case.state.artifacts[1:]),
        }
    )
    with pytest.raises(ValueError, match="not canonical"):
        case.policy.claims(state)


def _failed_state(case: _Case, *, include_diagnostic: bool = True) -> RunState:
    diagnostic = _put(
        case.store,
        case.root,
        name="diagnostic.json",
        value={"failure": "controlled"},
        schema="harness.observation_diagnostic.v1",
    )
    blocker = Blocker(
        code="HARN_OBSERVATION_UNBOUND",
        message="controlled failure",
        stage="observe",
        retryable=False,
        details={},
        unknowns=(),
        artifact_refs=(diagnostic,),
    )
    return RunState(
        **case.state.model_dump(
            mode="python", exclude={"status", "events", "artifacts", "output", "blocker"}
        ),
        status="failed",
        events=(
            case.state.events[0],
            case.state.events[1].model_copy(update={"to_status": RunStatus.FAILED}),
        ),
        artifacts=(diagnostic,) if include_diagnostic else (),
        output=None,
        blocker=blocker,
    )


def test_diagnostics_and_verify_result_close_success_and_failure(tmp_path: Path) -> None:
    case = _case(tmp_path)
    supporting = tuple(
        sorted(
            case.state.artifacts,
            key=lambda ref: (ref.sha256, ref.schema_version or "", ref.media_type),
        )
    )
    claims = case.policy.claims(case.state)
    case.policy.verify_result(
        state=case.state,
        claims=claims,
        result=case.result,
        supporting=supporting,
    )
    assert case.policy.diagnostics(case.state) == ()

    failed = _failed_state(case)
    diagnostics = case.policy.diagnostics(failed)
    assert case.policy.claims(failed) == ()
    assert case.policy.fresh_observations(failed, None) == ()
    failed_result = case.result.model_copy(
        update={
            "status": RunStatus.FAILED,
            "typed_output": None,
            "state_delta": None,
            "diagnostics": diagnostics,
            "fresh_observations": (),
            "blocker": failed.blocker,
        }
    )
    case.policy.verify_result(
        state=failed,
        claims=(),
        result=failed_result,
        supporting=failed.artifacts,
    )


def test_verify_result_rejects_every_open_projection_edge(tmp_path: Path) -> None:
    case = _case(tmp_path)
    claims = case.policy.claims(case.state)
    supporting = tuple(
        sorted(
            case.state.artifacts,
            key=lambda ref: (ref.sha256, ref.schema_version or "", ref.media_type),
        )
    )
    attacks = (
        ({"supporting": ()}, "supporting evidence"),
        ({"result": case.result.model_copy(update={"typed_output": {}})}, "output differs"),
        ({"claims": ()}, "claims differ"),
        (
            {"result": case.result.model_copy(update={"diagnostics": (case.output.images[0],)})},
            "diagnostics differ",
        ),
        (
            {
                "result": case.result.model_copy(
                    update={
                        "fresh_observations": (
                            case.result.fresh_observations[0].model_copy(update={"value": False}),
                        )
                    }
                )
            },
            "fresh observations differ",
        ),
        ({"result": case.result.model_copy(update={"invocation": None})}, "omitted its invocation"),
    )
    for change, message in attacks:
        with pytest.raises(ValueError, match=message):
            case.policy.verify_result(
                state=case.state,
                claims=change.get("claims", claims),
                result=change.get("result", case.result),
                supporting=change.get("supporting", supporting),
            )

    failed = _failed_state(case, include_diagnostic=False)
    failed_result = case.result.model_copy(
        update={
            "status": RunStatus.FAILED,
            "typed_output": None,
            "state_delta": None,
            "diagnostics": case.policy.diagnostics(failed),
            "fresh_observations": (),
            "blocker": failed.blocker,
        }
    )
    with pytest.raises(ValueError, match="diagnostics are absent"):
        case.policy.verify_result(
            state=failed,
            claims=(),
            result=failed_result,
            supporting=(),
        )
