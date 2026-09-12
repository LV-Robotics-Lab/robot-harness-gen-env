"""Restricted preview evidence uses the existing durable child journal."""

import pytest

from self_improving.harness.limited_image_preview_policy import LimitedImagePreviewPolicy
from tests.self_improving.harness.test_golden_image2env_policy import (  # noqa: F401
    capture,
    parent_capture,
)


def test_policy_rejects_callers_without_exact_shared_application():
    with pytest.raises(TypeError, match="exact"):
        LimitedImagePreviewPolicy(object(), "image2env.validate@3.0.0", {})


def test_policy_rejects_unknown_skill_before_creating_any_authority():
    with pytest.raises(ValueError, match="exact"):
        LimitedImagePreviewPolicy(object(), "skill.invoke@1.0.0", {})


def test_observation_after_validation_requires_same_workflow_replay(
    parent_capture,  # noqa: F811
    policy,
    tmp_path,
):
    import json
    from dataclasses import replace
    from uuid import uuid4

    from self_improving.harness.event_journal import SQLiteEventJournal
    from self_improving.harness.events import RunRecorder
    from self_improving.harness.limited_image_preview_workflow_policies import (
        LimitedPreviewObservationPolicy,
        LimitedPreviewValidationPolicy,
    )
    from self_improving.harness.schemas.common import Invocation, RunStatus
    from self_improving.harness.schemas.genesis_scene_execution_v2 import (
        GenesisSceneExecutionReceiptV2,
    )
    from self_improving.harness.trusted_state import WorldFact, build_world_state

    shared_application = policy.application
    original, _, _, context, old_head, parent, put = parent_capture
    store = original.artifact_store
    authority = original.child_authority

    def put_golden(value, schema):
        path = tmp_path / str(uuid4())
        path.write_bytes(
            json.dumps(
                value.model_dump(mode="json"),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )
        return store.put_file(
            path,
            name=path.name,
            media_type="application/json",
            schema_version=schema,
        )

    output = GenesisSceneExecutionReceiptV2(
        schema_version="harness.genesis_scene_execution_receipt.v2",
        authority="observed_execution_only",
        request=put({}, "harness.genesis_scene_execution_request.v2"),
        launch_result=put({}, "harness.genesis_scene_launch_result.v2"),
        replay=put({}, "harness.genesis_scene_replay.v1"),
        report=put({}, "harness.genesis_scene_report.v1"),
        baseline_preview=put({}, media="video/mp4"),
        half_dt_preview=put({}, media="video/mp4"),
        retained_index=put({}),
    )
    ref = put(output.model_dump(mode="json"), output.schema_version)
    old = authority.read_run_state(parent.child_run_id)
    run_id = uuid4()
    invocation = Invocation(
        run_id=run_id,
        skill_id="image2env.replay",
        skill_version="2.0.0",
        effective_parameters={},
        dependencies=(),
        max_attempts=1,
        invocation_digest=old.invocation_digest,
    )
    authority.put_invocation(invocation)
    recorder = RunRecorder(
        run_id=run_id,
        skill_id="image2env.replay",
        skill_version="2.0.0",
        clock=lambda: old.ended_at,
        sink=SQLiteEventJournal(authority.path),
    )
    recorder.start(stage="preflight", attempt=1)
    recorder.finish(status=RunStatus.SUCCEEDED, stage="complete", artifact_refs=(ref,))
    terminal = recorder.build_state(
        invocation_digest=invocation.invocation_digest,
        max_attempts=1,
        artifacts=(ref,),
        output=output.model_dump(mode="json"),
        blocker=None,
    )
    authority.put_run_state(terminal)
    parent = parent.model_copy(
        update={
            "child_run_id": run_id,
            "skill": parent.skill.model_copy(update={"skill_ref": "image2env.replay@2.0.0"}),
            "typed_output": terminal.output,
            "run_state": put_golden(terminal, "harness.run_state.v1"),
            "invocation": put_golden(invocation, "harness.skill_invocation.v1"),
            "supporting_artifacts": (ref,),
        }
    )
    fact = WorldFact(
        key="image.replay",
        value={"run_id": str(run_id), "receipt": ref.model_dump(mode="json")},
        source_kind="trusted_receipt",
        source_artifact=put_golden(parent, parent.schema_version),
        observed_at=terminal.ended_at,
        valid_until=None,
    )
    world = build_world_state((fact,), as_of=terminal.ended_at)
    policy = LimitedPreviewObservationPolicy(
        store, authority, "genesis.observe@3.0.0", {"image2env.replay@2.0.0": authority}
    )
    parameters = policy.normalize_parameters(
        {
            "schema_version": "harness.genesis_fresh_capture_input.v1",
            "replay_run_id": str(run_id),
            "ttl_seconds": 300,
        }
    )
    # Current head is deliberately later than replay; ownership comes from the trusted fact.
    context = replace(context, revision=3)
    policy.authorize_workflow(world, parameters, context)
    with pytest.raises(ValueError, match="this workflow"):
        policy.authorize_workflow(world, parameters, replace(context, workflow_run_id=uuid4()))
    with pytest.raises(ValueError, match="trusted workflow"):
        policy.authorize_workflow(
            build_world_state((), as_of=terminal.ended_at), parameters, context
        )
    with pytest.raises(ValueError, match="this workflow"):
        policy.authorize_workflow(
            world, parameters.model_copy(update={"replay_run_id": uuid4()}), context
        )
    world_ref = put(world.model_dump(mode="json"), "harness.trusted_world_state.v1")
    head = old_head.model_copy(
        update={
            "tool_receipt": fact.source_artifact,
            "resulting_state": world_ref,
            "resulting_state_sha256": world.state_sha256,
        }
    )
    context = replace(
        context,
        revision=1,
        receipt_head=put_golden(head, head.schema_version),
        state_ref=world_ref,
    )
    validation = LimitedPreviewValidationPolicy(
        shared_application, "image2env.validate@3.0.0", {"image2env.replay@2.0.0": authority}
    )
    requested = validation.normalize_parameters(
        {
            "schema_version": "harness.limited_scene_validate_input.v1",
            "execution_receipt": ref.model_dump(mode="json"),
            "expected_layout": None,
        }
    )
    assert not store.resolve(ref).path.read_bytes().endswith(b"\n")
    validation.authorize_workflow(world, requested, context)
    with pytest.raises(ValueError, match="this workflow"):
        validation.authorize_workflow(world, requested, replace(context, workflow_run_id=uuid4()))


@pytest.mark.parametrize(
    ("prefix", "suffix"),
    ((b"", b"\r\n"), (b"", b"\n\n"), (b" ", b""), (b"", b" ")),
    ids=("crlf", "double-lf", "leading-space", "trailing-space"),
)
def test_validation_rejects_noncanonical_formal_head_bytes(
    parent_capture,  # noqa: F811
    policy,
    tmp_path,
    prefix,
    suffix,
):
    import json
    from dataclasses import replace
    from uuid import uuid4

    from self_improving.harness.limited_image_preview_workflow_policies import (
        LimitedPreviewValidationPolicy,
    )
    from self_improving.harness.schemas.genesis_fresh_capture import GenesisFreshCaptureReceiptV1

    original, world, _, context, head, parent, _ = parent_capture
    terminal = original.child_authority.read_run_state(parent.child_run_id)
    output = GenesisFreshCaptureReceiptV1.model_validate(terminal.output)
    validation = LimitedPreviewValidationPolicy(
        policy.application,
        "image2env.validate@3.0.0",
        {"genesis.observe@3.0.0": original.child_authority},
    )
    requested = validation.normalize_parameters(
        {
            "schema_version": "harness.limited_scene_validate_input.v1",
            "execution_receipt": output.replay_receipt.model_dump(mode="json"),
            "expected_layout": None,
        }
    )
    canonical = json.dumps(
        head.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    path = tmp_path / str(uuid4())
    path.write_bytes(prefix + canonical + suffix)
    ref = validation.artifact_store.put_file(
        path,
        name=path.name,
        media_type="application/json",
        schema_version=head.schema_version,
    )
    with pytest.raises(ValueError, match="canonical"):
        validation.authorize_workflow(world, requested, replace(context, receipt_head=ref))


def test_validation_rejects_canonical_formal_head_content_drift(
    parent_capture,  # noqa: F811
    policy,
    tmp_path,
):
    import json
    from dataclasses import replace
    from uuid import uuid4

    from self_improving.harness.limited_image_preview_workflow_policies import (
        LimitedPreviewValidationPolicy,
    )
    from self_improving.harness.schemas.genesis_fresh_capture import GenesisFreshCaptureReceiptV1

    original, world, _, context, head, parent, _ = parent_capture
    terminal = original.child_authority.read_run_state(parent.child_run_id)
    output = GenesisFreshCaptureReceiptV1.model_validate(terminal.output)
    validation = LimitedPreviewValidationPolicy(
        policy.application,
        "image2env.validate@3.0.0",
        {"genesis.observe@3.0.0": original.child_authority},
    )
    requested = validation.normalize_parameters(
        {
            "schema_version": "harness.limited_scene_validate_input.v1",
            "execution_receipt": output.replay_receipt.model_dump(mode="json"),
            "expected_layout": None,
        }
    )
    changed = head.model_copy(update={"principal_id": "tests/drift"})
    path = tmp_path / str(uuid4())
    path.write_bytes(
        json.dumps(
            changed.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    ref = validation.artifact_store.put_file(
        path,
        name=path.name,
        media_type="application/json",
        schema_version=changed.schema_version,
    )
    with pytest.raises(ValueError, match="predecessor"):
        validation.authorize_workflow(world, requested, replace(context, receipt_head=ref))


@pytest.fixture
def policy(tmp_path):
    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_scene_execution_v3 import GenesisSceneExecutionProducerV3
    from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
    from self_improving.harness.limited_image_preview_application import (
        LimitedImagePreviewApplication,
    )
    from self_improving.harness.revised_image_compile_application import (
        RevisedImageCompileApplication,
    )
    from self_improving.harness.revised_image_compiler import RevisedImage2EnvGenesisCompiler
    from tests.self_improving.harness.test_genesis_scene_execution import reference

    store = LocalArtifactStore(tmp_path / "cas")
    compiler = RevisedImage2EnvGenesisCompiler(store, Image2EnvGenesisCompiler(store, object()))
    compile_app = RevisedImageCompileApplication(tmp_path / "compile", compiler)
    app = LimitedImagePreviewApplication(
        tmp_path / "shared",
        compile_app,
        GenesisSceneExecutionProducerV3(
            store, compiler, reference("harness.genesis_runtime_manifest.v1")
        ),
        roots={},
    )
    return LimitedImagePreviewPolicy(app, "image2env.validate@3.0.0", {})


def test_semantic_failure_diagnostic_is_derived_from_durable_events(policy):
    from datetime import datetime, timezone
    from uuid import uuid4

    from self_improving.harness.event_journal import SQLiteEventJournal
    from self_improving.harness.events import RunRecorder
    from self_improving.harness.schemas.common import Blocker, Invocation, RunStatus
    from self_improving.harness.schemas.limited_image_preview_execution import (
        LimitedSceneValidationV1,
    )
    from tests.self_improving.harness.test_genesis_scene_execution import reference

    value = LimitedSceneValidationV1(
        schema_version="harness.limited_scene_validation.v1",
        execution_receipt=reference("harness.genesis_scene_execution_receipt.v2"),
        scene=reference("harness.genesis_scene_spec.v1"),
        physical_report=reference("harness.genesis_scene_report.v1"),
        expected_layout={"x_m": 0.1, "y_m": 0.0, "yaw_rad": 0.0},
        actual_layout={"x_m": 0.0, "y_m": 0.0, "yaw_rad": 0.0},
        semantic_status="mismatch",
        status="failed",
        position_tolerance_m=0.001,
        yaw_tolerance_rad=0.001,
        physical_scope="dual_dt_trace_contact_settling_and_media",
    )
    diagnostic = policy.application.put(value, "semantic-failure.json")
    invocation = Invocation(
        run_id=uuid4(),
        skill_id="image2env.validate",
        skill_version="3.0.0",
        effective_parameters={},
        dependencies=(),
        max_attempts=1,
        invocation_digest="b" * 64,
    )
    policy.child_authority.put_invocation(invocation)
    recorder = RunRecorder(
        run_id=invocation.run_id,
        skill_id=invocation.skill_id,
        skill_version=invocation.skill_version,
        clock=lambda: datetime.now(timezone.utc),
        sink=SQLiteEventJournal(policy.child_authority.path),
    )
    recorder.start(stage="preflight", attempt=1)
    recorder.progress(stage="preview.semantic_validation", artifact_refs=(diagnostic,))
    recorder.finish(status=RunStatus.FAILED, stage="invoke")
    state = recorder.build_state(
        invocation_digest=invocation.invocation_digest,
        max_attempts=1,
        artifacts=(),
        output=None,
        blocker=Blocker(
            code="HARN_INTERNAL",
            message="semantic mismatch",
            stage="invoke",
            retryable=False,
            artifact_refs=(),
            unknowns=(),
            details={},
        ),
    )
    policy.child_authority.put_run_state(state)
    assert policy.claims(state) == ()
    assert policy.diagnostics(state) == (diagnostic,)
    assert policy.fresh_observations(state, invocation) == ()
    assert policy.evidence_policy_id == "image2env.validate.limited_preview"
    assert len(policy.evidence_policy_implementation_sha256) == 64
    with pytest.raises(ValueError, match="SQLite"):
        policy.diagnostics(state.model_copy(update={"run_id": uuid4()}))
    policy.artifact_store.resolve(diagnostic).path.unlink()
    with pytest.raises(ValueError):
        policy.diagnostics(state)
