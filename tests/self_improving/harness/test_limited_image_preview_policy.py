"""Restricted preview evidence uses the existing durable child journal."""

import pytest

from self_improving.harness.limited_image_preview_policy import LimitedImagePreviewPolicy


def test_policy_rejects_callers_without_exact_shared_application():
    with pytest.raises(TypeError, match="exact"):
        LimitedImagePreviewPolicy(object(), "image2env.validate@3.0.0", {})


def test_policy_rejects_unknown_skill_before_creating_any_authority():
    with pytest.raises(ValueError, match="exact"):
        LimitedImagePreviewPolicy(object(), "skill.invoke@1.0.0", {})


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
