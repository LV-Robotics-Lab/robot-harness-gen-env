"""Public exact native image evidence policy contracts."""

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.golden_image2env_policy import NativeImageEvidencePolicy
from self_improving.harness.run_store import SQLiteRunStore


@pytest.fixture
def capture(tmp_path):
    import json
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4

    from self_improving.harness.event_journal import SQLiteEventJournal
    from self_improving.harness.events import RunRecorder
    from self_improving.harness.schemas.common import Invocation, RunStatus
    from self_improving.harness.schemas.genesis_fresh_capture import GenesisFreshCaptureReceiptV1

    store = LocalArtifactStore(tmp_path / "cas")
    authority = SQLiteRunStore(tmp_path / "runs.sqlite")
    start = datetime(2026, 9, 11, tzinfo=timezone.utc)

    def put(value, schema=None, media="application/json"):
        path = tmp_path / str(uuid4())
        path.write_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
        return store.put_file(path, name=path.name, media_type=media, schema_version=schema)

    replay_id = uuid4()
    output = GenesisFreshCaptureReceiptV1(
        schema_version="harness.genesis_fresh_capture_receipt.v1",
        replay_run_id=replay_id,
        replay_receipt=put({}, "harness.genesis_scene_execution_receipt.v2"),
        request=put({}, "harness.genesis_fresh_capture_request.v1"),
        capture_result=put({}, "harness.genesis_fresh_capture_result.v1"),
        image=put({}, media="image/png"),
        captured_at=start,
        checked_at=start,
        expires_at=start + timedelta(seconds=300),
        ttl_seconds=300,
        authority="observed_restored_final_pose",
        process_continuation=False,
    )
    receipt = put(output.model_dump(mode="json"), output.schema_version)
    invocation = Invocation(
        run_id=uuid4(),
        skill_id="genesis.observe",
        skill_version="3.0.0",
        effective_parameters={
            "schema_version": "harness.genesis_fresh_capture_input.v1",
            "replay_run_id": str(replay_id),
            "ttl_seconds": 300,
        },
        dependencies=(),
        max_attempts=1,
        invocation_digest="a" * 64,
    )
    authority.put_invocation(invocation)
    recorder = RunRecorder(
        run_id=invocation.run_id,
        skill_id=invocation.skill_id,
        skill_version=invocation.skill_version,
        clock=lambda: start,
        sink=SQLiteEventJournal(authority.path),
    )
    recorder.start(stage="preflight", attempt=1)
    recorder.finish(status=RunStatus.SUCCEEDED, stage="complete", artifact_refs=(receipt,))
    state = recorder.build_state(
        invocation_digest=invocation.invocation_digest,
        max_attempts=1,
        artifacts=(receipt,),
        output=output.model_dump(mode="json"),
        blocker=None,
    )
    authority.put_run_state(state)
    policy = NativeImageEvidencePolicy(store, authority, "genesis.observe@3.0.0")
    return policy, state, invocation, receipt, output, put


def test_native_capture_projects_exact_sqlite_receipt_and_ttl(capture):
    policy, state, invocation, receipt, output, _ = capture
    claims = policy.claims(state)
    assert claims[0].value["run_id"] == str(state.run_id)
    assert policy.diagnostics(state) == ()
    (observed,) = policy.fresh_observations(state, invocation)
    assert observed.key == "image.fresh_capture"
    assert observed.source_artifact == receipt
    assert observed.valid_until == output.expires_at
    assert next(claim for claim in claims if claim.key == observed.key).value == observed.value


def test_native_capture_rejects_foreign_terminal_and_invocation(capture):
    from uuid import uuid4

    policy, state, invocation, _, _, _ = capture
    with pytest.raises(ValueError, match="SQLite"):
        policy.claims(state.model_copy(update={"run_id": uuid4()}))
    with pytest.raises(ValueError, match="invocation"):
        policy.fresh_observations(state, invocation.model_copy(update={"run_id": uuid4()}))


def test_native_capture_rechecks_receipt_bytes(capture):
    policy, state, _, receipt, _, _ = capture
    policy.artifact_store.resolve(receipt).path.write_bytes(b"{}")
    with pytest.raises(ValueError):
        policy.claims(state)


def test_native_policy_rejects_non_exact_authorities(tmp_path):
    with pytest.raises(TypeError, match="exact CAS"):
        NativeImageEvidencePolicy(
            object(), SQLiteRunStore(tmp_path / "runs.sqlite"), "genesis.observe@3.0.0"
        )
    with pytest.raises(TypeError, match="predecessors"):
        NativeImageEvidencePolicy(
            LocalArtifactStore(tmp_path / "cas"),
            SQLiteRunStore(tmp_path / "other.sqlite"),
            "genesis.observe@3.0.0",
            {"image2env.replay@2.0.0": object()},
        )


def test_native_policy_requires_typed_parameters_and_context(capture):
    policy, _, _, _, _, _ = capture
    with pytest.raises(TypeError, match="input"):
        policy.authorize(None, {})
    value = policy.normalize_parameters(
        {
            "schema_version": "harness.genesis_fresh_capture_input.v1",
            "replay_run_id": "71000000-0000-4000-8000-000000000001",
            "ttl_seconds": 300,
        }
    )
    with pytest.raises(TypeError, match="context"):
        policy.authorize_workflow(None, value, object())


@pytest.fixture
def parent_capture(capture):
    from uuid import uuid4

    from self_improving.harness.golden_execution import SkillAuthorizationContext
    from self_improving.harness.schemas.workflow_operation import (
        QualifiedSkillExecutionV2,
        TrustedToolReceiptV2,
        WorkflowOperationReceiptV2,
    )
    from self_improving.harness.trusted_state import build_world_state

    policy, terminal, invocation, _, _, put = capture
    world = build_world_state((), as_of=terminal.ended_at)
    world_ref = put(world.model_dump(mode="json"), "harness.trusted_world_state.v1")
    workflow, operation = uuid4(), uuid4()
    command = put({}, "harness.run_command_request.v1")
    qualified = QualifiedSkillExecutionV2(
        schema_version="harness.qualified_skill_execution.v2",
        skill_ref=policy.skill_ref,
        descriptor=put({}, "harness.skill_descriptor.v2"),
        input_schema="harness.genesis_fresh_capture_input.v1",
        output_schema="harness.genesis_fresh_capture_receipt.v1",
        implementation_sha256="a" * 64,
        qualification_artifact=put({}, "harness.skill_qualification.v1"),
        qualification_report=put({}, "harness.skill_qualification_report.v1"),
        max_attempts=1,
        evidence_policy_id=policy.evidence_policy_id,
        evidence_policy_version="1.0.0",
        evidence_policy_implementation_sha256=policy.evidence_policy_implementation_sha256,
    )
    parent = TrustedToolReceiptV2(
        schema_version="harness.trusted_tool_receipt.v2",
        workflow_run_id=workflow,
        operation_id=operation,
        principal_id="tests/native",
        command=command,
        base_revision=0,
        base_state_sha256=world.state_sha256,
        base_state=world_ref,
        registry_snapshot=put({}, "harness.registry_snapshot.v1"),
        skill=qualified,
        status=terminal.status,
        started_at=terminal.started_at,
        ended_at=terminal.ended_at,
        child_run_id=terminal.run_id,
        invocation=put(invocation.model_dump(mode="json"), "harness.skill_invocation.v1"),
        run_state=put(terminal.model_dump(mode="json"), "harness.run_state.v1"),
        invocation_digest=invocation.invocation_digest,
        typed_output=terminal.output,
        blocker=None,
        supporting_artifacts=terminal.artifacts,
        derived_fact_claims=(),
    )
    head = WorkflowOperationReceiptV2(
        schema_version="harness.workflow_operation_receipt.v2",
        workflow_run_id=workflow,
        operation_id=operation,
        principal_id="tests/native",
        command=command,
        predecessor_receipt=put({}, "harness.workflow_start_receipt.v1"),
        tool_receipt=put(parent.model_dump(mode="json"), parent.schema_version),
        tool_result=put({}, "harness.tool_result.v2"),
        state_delta=put({}, "harness.state_delta.v2"),
        base_revision=0,
        committed_revision=1,
        base_state_sha256=world.state_sha256,
        resulting_state_sha256=world.state_sha256,
        resulting_state=world_ref,
        status="succeeded",
        committed_at=terminal.ended_at,
    )
    context = SkillAuthorizationContext(
        workflow_run_id=workflow,
        principal_id="tests/native",
        receipt_head=put(head.model_dump(mode="json"), head.schema_version),
        revision=1,
        state_ref=world_ref,
    )
    validator = NativeImageEvidencePolicy(
        policy.artifact_store,
        policy.child_authority,
        "image2env.validate@2.0.0",
        {policy.skill_ref: policy.child_authority},
    )
    parameters = validator.normalize_parameters(
        {
            "schema_version": "harness.genesis_scene_validate_input.v1",
            "replay_run_id": terminal.output["replay_run_id"],
        }
    )
    return validator, world, parameters, context, head, parent, put


def test_validate_uses_only_current_workflow_observation_replay(parent_capture):
    from dataclasses import replace
    from uuid import uuid4

    policy, world, parameters, context, _, _, _ = parent_capture
    policy.authorize_workflow(world, parameters, context)
    with pytest.raises(ValueError, match="workflow context"):
        policy.authorize_workflow(world, parameters, replace(context, workflow_run_id=uuid4()))
    with pytest.raises(ValueError, match="replay UUID"):
        policy.authorize_workflow(
            world, parameters.model_copy(update={"replay_run_id": uuid4()}), context
        )
    without = replace(policy, predecessor_authorities={})
    with pytest.raises(ValueError, match="SQLite authority"):
        without.authorize_workflow(world, parameters, context)


def test_parent_receipt_and_sqlite_must_agree(parent_capture):
    from dataclasses import replace
    from uuid import uuid4

    policy, world, parameters, context, head, parent, put = parent_capture
    for changes, message in (
        ({"principal_id": "tests/foreign"}, "required successful"),
        ({"child_run_id": uuid4()}, "child journal"),
    ):
        changed = parent.model_copy(update=changes)
        changed_ref = put(changed.model_dump(mode="json"), changed.schema_version)
        changed_head = head.model_copy(update={"tool_receipt": changed_ref})
        changed_context = replace(
            context, receipt_head=put(changed_head.model_dump(mode="json"), head.schema_version)
        )
        with pytest.raises(ValueError, match=message):
            policy.authorize_workflow(world, parameters, changed_context)


def test_parent_receipt_requires_canonical_cas_bytes(parent_capture, tmp_path):
    from dataclasses import replace

    policy, world, parameters, context, head, _, _ = parent_capture
    path = tmp_path / "noncanonical.json"
    path.write_text(head.model_dump_json(indent=2))
    ref = policy.artifact_store.put_file(
        path, name="head.json", media_type="application/json", schema_version=head.schema_version
    )
    with pytest.raises(ValueError, match="canonical"):
        policy.authorize_workflow(world, parameters, replace(context, receipt_head=ref))


def test_result_projects_only_exact_terminal_cas_and_invocation(capture):
    from uuid import uuid4

    from self_improving.harness.schemas.workflow_operation import ToolResultV2

    policy, state, invocation, _, _, put = capture
    result = ToolResultV2(
        schema_version="harness.tool_result.v2",
        workflow_run_id=uuid4(),
        operation_id=uuid4(),
        skill_ref=policy.skill_ref,
        status=state.status,
        started_at=state.started_at,
        ended_at=state.ended_at,
        child_run_id=state.run_id,
        invocation=put(invocation.model_dump(mode="json"), "harness.skill_invocation.v1"),
        run_state=put(state.model_dump(mode="json"), "harness.run_state.v1"),
        typed_output=state.output,
        state_delta=put({}, "harness.state_delta.v2"),
        diagnostics=(),
        trusted_receipt=put({}, "harness.trusted_tool_receipt.v2"),
        fresh_observations=policy.fresh_observations(state, invocation),
        blocker=None,
    )
    claims = policy.claims(state)
    policy.verify_result(state=state, claims=claims, result=result, supporting=state.artifacts)
    for changed, message in (
        ({"child_run_id": uuid4()}, "child identity"),
        ({"invocation": None}, "invocation"),
        ({"typed_output": {}}, "exact native"),
        ({"fresh_observations": ()}, "fresh observation"),
    ):
        with pytest.raises(ValueError, match=message):
            policy.verify_result(
                state=state,
                claims=claims,
                result=result.model_copy(update=changed),
                supporting=state.artifacts,
            )
    with pytest.raises(ValueError, match="supporting evidence"):
        policy.verify_result(state=state, claims=claims, result=result, supporting=())


@pytest.mark.parametrize(
    "variation", ["candidate", "missing_receipt", "wrong_receipt", "wrong_replay", "late_terminal"]
)
def test_registered_capture_rejects_open_terminal_edges(capture, tmp_path, variation):
    from dataclasses import replace
    from datetime import timedelta

    from self_improving.harness.event_journal import SQLiteEventJournal
    from self_improving.harness.events import RunEvent
    from self_improving.harness.schemas.common import RunState

    policy, state, invocation, receipt, output, put = capture
    data = state.model_dump(mode="python")
    if variation == "candidate":
        data["events"] = tuple(
            event.model_copy(update={"stage": "qualification_candidate.execute"})
            for event in state.events
        )
    elif variation == "missing_receipt":
        data["artifacts"] = ()
    elif variation == "wrong_receipt":
        changed = output.model_copy(update={"checked_at": output.checked_at + timedelta(seconds=1)})
        data["artifacts"] = (put(changed.model_dump(mode="json"), output.schema_version),)
    elif variation == "wrong_replay":
        from uuid import uuid4

        changed = output.model_copy(update={"replay_run_id": uuid4()})
        data["output"] = changed.model_dump(mode="json")
        data["artifacts"] = (put(changed.model_dump(mode="json"), output.schema_version),)
    else:
        late = output.expires_at + timedelta(seconds=1)
        data["ended_at"] = late
        data["events"] = (state.events[0], state.events[1].model_copy(update={"timestamp": late}))
    terminal = RunState.model_validate(data)
    authority = SQLiteRunStore(tmp_path / "changed.sqlite")
    authority.put_invocation(invocation)
    journal = SQLiteEventJournal(authority.path)
    for event in terminal.events:
        journal.publish(
            RunEvent(
                run_id=terminal.run_id,
                skill_id=terminal.skill_id,
                skill_version=terminal.skill_version,
                event=event,
            )
        )
    authority.put_run_state(terminal)
    changed_policy = replace(policy, child_authority=authority)
    with pytest.raises(ValueError):
        changed_policy.claims(terminal)


def test_failed_capture_carries_diagnostics_without_observation(capture, tmp_path):
    from dataclasses import replace

    from self_improving.harness.event_journal import SQLiteEventJournal
    from self_improving.harness.events import RunRecorder
    from self_improving.harness.schemas.common import Blocker, RunStatus

    policy, prior, invocation, _, _, put = capture
    diagnostic = put({"error": "fixed capture failed"})
    authority = SQLiteRunStore(tmp_path / "failed.sqlite")
    authority.put_invocation(invocation)
    recorder = RunRecorder(
        run_id=invocation.run_id,
        skill_id=invocation.skill_id,
        skill_version=invocation.skill_version,
        clock=lambda: prior.started_at,
        sink=SQLiteEventJournal(authority.path),
    )
    recorder.start(stage="preflight", attempt=1)
    recorder.finish(status=RunStatus.FAILED, stage="execute", artifact_refs=(diagnostic,))
    blocker = Blocker(
        code="HARN_EXECUTION_FAILED",
        message="fixed capture failed",
        stage="execute",
        retryable=False,
        details={},
        unknowns=(),
        artifact_refs=(diagnostic,),
    )
    state = recorder.build_state(
        invocation_digest=invocation.invocation_digest,
        max_attempts=1,
        artifacts=(diagnostic,),
        output=None,
        blocker=blocker,
    )
    authority.put_run_state(state)
    policy = replace(policy, child_authority=authority)
    assert policy.claims(state) == ()
    assert policy.fresh_observations(state, invocation) == ()
    assert policy.diagnostics(state) == (diagnostic,)


@pytest.mark.parametrize("candidate", [False, True])
def test_validation_projects_registered_scope_only(capture, tmp_path, candidate):
    from self_improving.harness.event_journal import SQLiteEventJournal
    from self_improving.harness.events import RunEvent
    from self_improving.harness.schemas.common import RunState
    from self_improving.harness.schemas.genesis_scene_assessment import GenesisSceneValidationV1

    policy, prior, invocation, _, capture_output, put = capture
    output = GenesisSceneValidationV1(
        schema_version="harness.genesis_scene_validation.v1",
        scope="genesis.rigid_scene@1",
        status="passed",
        replay_run_id=capture_output.replay_run_id,
        invocation_digest="b" * 64,
        execution_receipt=put({}, "harness.genesis_scene_execution_receipt.v2"),
        scene=put({}, "harness.genesis_scene_spec.v1"),
        replay=put({}, "harness.genesis_scene_replay.v1"),
        report=put({}, "harness.genesis_scene_report.v1"),
        assessed_at=prior.ended_at,
        simulator_rerun=False,
        robot_policy_evaluated=False,
        candidate_only=candidate,
    )
    receipt = put(output.model_dump(mode="json"), output.schema_version)
    invocation = invocation.model_copy(
        update={
            "skill_id": "image2env.validate",
            "skill_version": "2.0.0",
            "effective_parameters": {
                "schema_version": "harness.genesis_scene_validate_input.v1",
                "replay_run_id": str(output.replay_run_id),
            },
        }
    )
    terminal = RunState.model_validate(
        {
            **prior.model_dump(mode="python"),
            "skill_id": "image2env.validate",
            "skill_version": "2.0.0",
            "output": output.model_dump(mode="json"),
            "artifacts": (receipt,),
        }
    )
    authority = SQLiteRunStore(tmp_path / "validate.sqlite")
    authority.put_invocation(invocation)
    journal = SQLiteEventJournal(authority.path)
    for event in terminal.events:
        journal.publish(
            RunEvent(
                run_id=terminal.run_id,
                skill_id=terminal.skill_id,
                skill_version=terminal.skill_version,
                event=event,
            )
        )
    authority.put_run_state(terminal)
    validator = NativeImageEvidencePolicy(
        policy.artifact_store, authority, "image2env.validate@2.0.0"
    )
    if candidate:
        with pytest.raises(ValueError, match="candidate output"):
            validator.claims(terminal)
    else:
        assert validator.claims(terminal)[0].key == "image.validation"
        assert validator.fresh_observations(terminal, invocation) == ()


def test_compile_is_bound_to_initial_image_and_objective(tmp_path):
    from datetime import datetime, timezone
    from uuid import uuid4

    from self_improving.harness.golden_execution import SkillAuthorizationContext
    from self_improving.harness.schemas.image2env_genesis_compile import (
        Image2EnvGenesisCompileRequestV1,
    )
    from self_improving.harness.schemas.x2env_compile_evidence import SceneInterpretationProposalV1
    from self_improving.harness.trusted_state import WorldFact, build_world_state
    from tests.self_improving.harness.test_x2env_compile_closure import _case

    case = _case(tmp_path)
    source = tmp_path / "source.json"
    source.write_text("{}")
    source_ref = case.store.put_file(
        source, name="source.json", media_type="application/json", schema_version=None
    )
    request = Image2EnvGenesisCompileRequestV1(
        schema_version="harness.image2env_genesis_compile_request.v1",
        compile_input=case.compile_input,
        qualification_run_id=uuid4(),
        source_scene=source_ref,
        table_center_m=(0.0, 0.0, 0.2),
        table_size_m=(1.0, 0.8, 0.4),
        table_friction=0.5,
    )
    proposal = SceneInterpretationProposalV1.model_validate_json(
        case.store.resolve(case.compile_input.interpretation_proposal).path.read_bytes()
    )
    objective = case.store.resolve(proposal.effective_prompt).path.read_text()
    at = datetime(2026, 9, 11, tzinfo=timezone.utc)
    facts = tuple(
        WorldFact(
            key=key,
            value=value,
            source_kind="user_input",
            source_artifact=source_ref,
            observed_at=at,
            valid_until=None,
        )
        for key, value in (
            ("request.objective", objective),
            ("request.image_source", request.compile_input.source_media.model_dump(mode="json")),
        )
    )
    world = build_world_state(facts, as_of=at)
    policy = NativeImageEvidencePolicy(
        case.store, SQLiteRunStore(tmp_path / "runs.sqlite"), "image2env.compile@2.0.0"
    )
    policy.authorize(world, request)
    policy.authorize_workflow(
        world,
        request,
        SkillAuthorizationContext(
            workflow_run_id=uuid4(),
            principal_id="tests/native",
            receipt_head=source_ref,
            revision=0,
            state_ref=source_ref,
        ),
    )
    for missing in ("request.objective", "request.image_source"):
        with pytest.raises(ValueError, match="initial facts"):
            policy.authorize(
                build_world_state(tuple(fact for fact in facts if fact.key != missing), as_of=at),
                request,
            )


def test_replay_requires_current_compile_uuid_and_exact_scene(parent_capture, tmp_path):
    from dataclasses import replace
    from uuid import uuid4

    from self_improving.harness.event_journal import SQLiteEventJournal
    from self_improving.harness.events import RunEvent
    from self_improving.harness.schemas.common import RunState
    from self_improving.harness.schemas.image2env_genesis_compile import (
        Image2EnvGenesisCompileReceiptV1,
        Image2EnvGenesisCompileRequestV1,
    )
    from tests.self_improving.harness.test_x2env_compile_closure import _case

    original, world, _, context, head, parent, put = parent_capture
    old = original.child_authority.read_run_state(parent.child_run_id)
    case = _case(tmp_path / "image-source")
    compile_request = Image2EnvGenesisCompileRequestV1(
        schema_version="harness.image2env_genesis_compile_request.v1",
        compile_input=case.compile_input,
        qualification_run_id=uuid4(),
        source_scene=put({}),
        table_center_m=(0.0, 0.0, 0.2),
        table_size_m=(1.0, 0.8, 0.4),
        table_friction=0.5,
    )
    invocation = original.child_authority.read_invocation(parent.child_run_id).model_copy(
        update={
            "skill_id": "image2env.compile",
            "skill_version": "2.0.0",
            "effective_parameters": compile_request.model_dump(mode="json"),
        }
    )
    output = Image2EnvGenesisCompileReceiptV1(
        schema_version="harness.image2env_genesis_compile_receipt.v1",
        request=put(compile_request.model_dump(mode="json"), compile_request.schema_version),
        scene=put({}, "harness.genesis_scene_spec.v1"),
        qualification=put({}, "harness.asset_qualification.v1"),
        source_object_id="mouse",
        proposal_object_id="mouse",
        scene_object_id="object",
        implementation_sha256="a" * 64,
        implementation=(put({}, media="text/x-python"),),
        scope="static_scene_candidate",
        simulator_executed=False,
    )
    receipt = put(output.model_dump(mode="json"), output.schema_version)
    state = RunState.model_validate(
        {
            **old.model_dump(mode="python"),
            "skill_id": "image2env.compile",
            "skill_version": "2.0.0",
            "output": output.model_dump(mode="json"),
            "artifacts": (receipt,),
        }
    )
    authority = SQLiteRunStore(tmp_path / "compile.sqlite")
    authority.put_invocation(invocation)
    journal = SQLiteEventJournal(authority.path)
    for event in state.events:
        journal.publish(
            RunEvent(
                run_id=state.run_id,
                skill_id=state.skill_id,
                skill_version=state.skill_version,
                event=event,
            )
        )
    authority.put_run_state(state)
    compile_policy = NativeImageEvidencePolicy(
        original.artifact_store, authority, "image2env.compile@2.0.0"
    )
    assert compile_policy.claims(state)[0].key == "image.compile"
    parent = parent.model_copy(
        update={
            "skill": parent.skill.model_copy(update={"skill_ref": "image2env.compile@2.0.0"}),
            "typed_output": state.output,
            "run_state": put(state.model_dump(mode="json"), "harness.run_state.v1"),
            "invocation": put(invocation.model_dump(mode="json"), "harness.skill_invocation.v1"),
            "supporting_artifacts": state.artifacts,
        }
    )
    head = head.model_copy(
        update={"tool_receipt": put(parent.model_dump(mode="json"), parent.schema_version)}
    )
    context = replace(context, receipt_head=put(head.model_dump(mode="json"), head.schema_version))
    execution = put(
        {
            "schema_version": "harness.genesis_scene_execution_request.v2",
            "scene": output.scene.model_dump(mode="json"),
            "compile_receipt": receipt.model_dump(mode="json"),
            "runtime_manifest": put({}, "harness.genesis_runtime_manifest.v1").model_dump(
                mode="json"
            ),
            "asset_qualification": output.qualification.model_dump(mode="json"),
        },
        "harness.genesis_scene_execution_request.v2",
    )
    policy = NativeImageEvidencePolicy(
        original.artifact_store,
        authority,
        "image2env.replay@2.0.0",
        {"image2env.compile@2.0.0": authority},
    )
    parameters = policy.normalize_parameters(
        {
            "schema_version": "harness.genesis_scene_replay_input.v2",
            "compile_run_id": str(state.run_id),
            "execution_request": execution.model_dump(mode="json"),
        }
    )
    policy.authorize_workflow(world, parameters, context)
    with pytest.raises(ValueError, match="current compile"):
        policy.authorize_workflow(
            world, parameters.model_copy(update={"compile_run_id": uuid4()}), context
        )
    altered = compile_request.model_copy(update={"table_friction": 0.1})
    wrong_authority = SQLiteRunStore(tmp_path / "wrong-compile-input.sqlite")
    wrong_authority.put_invocation(
        invocation.model_copy(update={"effective_parameters": altered.model_dump(mode="json")})
    )
    wrong_journal = SQLiteEventJournal(wrong_authority.path)
    for event in state.events:
        wrong_journal.publish(
            RunEvent(
                run_id=state.run_id,
                skill_id=state.skill_id,
                skill_version=state.skill_version,
                event=event,
            )
        )
    wrong_authority.put_run_state(state)
    with pytest.raises(ValueError, match="compile receipt input"):
        NativeImageEvidencePolicy(
            original.artifact_store, wrong_authority, "image2env.compile@2.0.0"
        ).claims(state)
    from self_improving.harness.schemas.genesis_scene_execution_v2 import (
        GenesisSceneExecutionReceiptV2,
    )

    replay_output = GenesisSceneExecutionReceiptV2(
        schema_version="harness.genesis_scene_execution_receipt.v2",
        authority="observed_execution_only",
        request=execution,
        launch_result=put({}, "harness.genesis_scene_launch_result.v2"),
        replay=put({}, "harness.genesis_scene_replay.v1"),
        report=put({}, "harness.genesis_scene_report.v1"),
        baseline_preview=put({}, media="video/mp4"),
        half_dt_preview=put({}, media="video/mp4"),
        retained_index=put({}),
    )
    replay_ref = put(replay_output.model_dump(mode="json"), replay_output.schema_version)
    for bad in (False, True):
        replay_input = (
            parameters.model_copy(
                update={"execution_request": put({}, "harness.genesis_scene_execution_request.v2")}
            )
            if bad
            else parameters
        )
        replay_invocation = invocation.model_copy(
            update={
                "skill_id": "image2env.replay",
                "effective_parameters": replay_input.model_dump(mode="json"),
            }
        )
        replay_state = RunState.model_validate(
            {
                **state.model_dump(mode="python"),
                "skill_id": "image2env.replay",
                "output": replay_output.model_dump(mode="json"),
                "artifacts": (replay_ref,),
            }
        )
        replay_authority = SQLiteRunStore(tmp_path / f"replay-{bad}.sqlite")
        replay_authority.put_invocation(replay_invocation)
        replay_journal = SQLiteEventJournal(replay_authority.path)
        for event in replay_state.events:
            replay_journal.publish(
                RunEvent(
                    run_id=replay_state.run_id,
                    skill_id=replay_state.skill_id,
                    skill_version=replay_state.skill_version,
                    event=event,
                )
            )
        replay_authority.put_run_state(replay_state)
        replay_policy = NativeImageEvidencePolicy(
            original.artifact_store, replay_authority, "image2env.replay@2.0.0"
        )
        if bad:
            with pytest.raises(ValueError, match="replay receipt input"):
                replay_policy.claims(replay_state)
        else:
            assert replay_policy.claims(replay_state)[0].key == "image.replay"


def test_native_policy_rejects_unqualified_or_legacy_skill(tmp_path):
    store = LocalArtifactStore(tmp_path / "cas")
    authority = SQLiteRunStore(tmp_path / "runs.sqlite")
    with pytest.raises(ValueError, match="exact"):
        NativeImageEvidencePolicy(store, authority, "genesis.observe@2.0.0")


@pytest.mark.parametrize(
    "skill",
    [
        "image2env.compile@2.0.0",
        "image2env.replay@2.0.0",
        "genesis.observe@3.0.0",
        "image2env.validate@2.0.0",
    ],
)
def test_native_policy_has_frozen_exact_input_and_source_identity(tmp_path, skill):
    policy = NativeImageEvidencePolicy(
        LocalArtifactStore(tmp_path / "cas"), SQLiteRunStore(tmp_path / "runs.sqlite"), skill
    )
    assert policy.skill_ref == skill
    assert len(policy.evidence_policy_implementation_sha256) == 64
    with pytest.raises(ValueError):
        policy.normalize_parameters({})
