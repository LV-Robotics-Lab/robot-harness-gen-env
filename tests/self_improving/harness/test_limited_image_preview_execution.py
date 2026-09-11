"""Shared modified-scene execution consumes real saved evidence, never fake physics."""

from datetime import datetime, timezone

import pytest

from self_improving.harness.limited_image_preview_execution import LimitedImagePreviewExecution
from tests.self_improving.harness.test_genesis_scene_execution_v3 import saved_run  # noqa: F401
from tests.self_improving.harness.test_genesis_scene_replay import replay  # noqa: F401
from tests.self_improving.harness.test_gujie_scene_adapter import candidate  # noqa: F401


def test_saved_runner_replay_observe_validate_uses_same_modified_scene(saved_run):  # noqa: F811
    from self_improving.harness.genesis_scene_execution_v3 import publish_scene_run_evidence_v3

    store, request, replay_ref, directory, _ = saved_run

    class SavedRunner:
        execution_authority = False

        def run(self, request_ref):
            assert request_ref == request
            return publish_scene_run_evidence_v3(
                store, request_ref=request_ref, replay_ref=replay_ref, output_dir=directory
            )

    execution = LimitedImagePreviewExecution(store, SavedRunner())
    result = execution.replay(request)
    assert result.request == request
    observed = execution.observe(result, now=datetime(2026, 9, 11, tzinfo=timezone.utc))
    assert observed.image.media_type == "image/png"
    validated = execution.validate(result)
    assert validated.report.status == "passed"
    assert not validated.execution_authority


@pytest.fixture
def execution(saved_run):  # noqa: F811
    from self_improving.harness.genesis_scene_execution_v3 import publish_scene_run_evidence_v3

    store, request, replay_ref, directory, _ = saved_run

    class SavedRunner:
        # Self-declared live authority must never be trusted.
        execution_authority = True

        def run(self, request_ref):
            return publish_scene_run_evidence_v3(
                store, request_ref=request_ref, replay_ref=replay_ref, output_dir=directory
            )

    module = LimitedImagePreviewExecution(store, SavedRunner())
    return module, module.replay(request)


@pytest.mark.parametrize("ttl", [False, 0, 301])
def test_observation_cannot_extend_ttl(execution, ttl):
    from self_improving.harness.limited_image_preview_execution import PreviewExecutionError

    module, replayed = execution
    with pytest.raises(PreviewExecutionError, match="TTL") as error:
        module.observe(replayed, ttl_seconds=ttl)
    assert error.value.stage == "observe"
    assert error.value.elapsed_seconds >= 0


def test_historical_frame_cannot_become_fresh_by_reading_it(execution):
    from self_improving.harness.limited_image_preview_execution import PreviewExecutionError

    module, replayed = execution
    with pytest.raises(PreviewExecutionError, match="stale"):
        module.observe(replayed, now=datetime(2026, 9, 11, 0, 5, 1, tzinfo=timezone.utc))
    boundary = module.observe(replayed, now=datetime(2026, 9, 11, 0, 5, tzinfo=timezone.utc))
    assert boundary.expires_at == boundary.checked_at


def test_missing_cas_is_validation_failure_not_success(execution):
    from self_improving.harness.limited_image_preview_execution import PreviewExecutionError

    module, replayed = execution
    module.artifact_store.resolve(replayed.launch).path.unlink()
    with pytest.raises(PreviewExecutionError) as error:
        module.validate(replayed)
    assert error.value.stage == "validate"


def test_runner_failure_retains_diagnostic_and_does_not_retry(saved_run):  # noqa: F811
    from self_improving.harness.limited_image_preview_execution import PreviewExecutionError

    store, request, _, _, _ = saved_run
    calls = []

    class FailedRunner:
        def run(self, request_ref):
            calls.append(request_ref)
            error = ValueError("runner dependency unavailable")
            error.diagnostic_ref = request_ref
            raise error

    with pytest.raises(PreviewExecutionError) as error:
        LimitedImagePreviewExecution(store, FailedRunner()).replay(request)
    assert calls == [request]
    assert error.value.diagnostic_ref == request
    assert error.value.stage == "replay"


def test_production_runner_rejects_unqualified_application():
    from self_improving.harness.limited_image_preview_execution import RegisteredRevisedSceneRunner

    with pytest.raises(TypeError, match="exact registered"):
        RegisteredRevisedSceneRunner(object(), object(), compile_run_id=None, roots={})


@pytest.mark.parametrize("mode", ["lineage", "pixels", "dimensions"])
def test_changed_observation_is_not_accepted(execution, saved_run, mode):  # noqa: F811
    import json
    from dataclasses import replace

    from self_improving.harness.limited_image_preview_execution import PreviewExecutionError
    from self_improving.harness.schemas.common import ArtifactRef

    module, replayed = execution
    store, _, _, _, publish = saved_run
    launch = json.loads(store.resolve(replayed.launch).path.read_bytes())
    observation_ref = ArtifactRef.model_validate(launch["half_dt_observation"])
    observation = json.loads(store.resolve(observation_ref).path.read_bytes())
    if mode == "lineage":
        observation["request"]["name"] = "foreign-request.json"
    elif mode == "pixels":
        observation["captures"][-1]["pixels_sha256"] = "f" * 64
    else:
        observation["captures"][-1]["width"] = 100
    changed = publish(observation, "changed.json", observation["schema_version"])
    launch["half_dt_observation"] = changed.model_dump(mode="json")
    changed_launch = publish(launch, "changed-launch.json", launch["schema_version"])
    with pytest.raises(PreviewExecutionError):
        module.observe(
            replace(replayed, launch=changed_launch),
            now=datetime(2026, 9, 11, tzinfo=timezone.utc),
        )


def test_foreign_runner_request_is_rejected(saved_run):  # noqa: F811
    import json

    from self_improving.harness.genesis_scene_execution_v3 import publish_scene_run_evidence_v3
    from self_improving.harness.limited_image_preview_execution import PreviewExecutionError

    store, request, replay_ref, directory, publish = saved_run
    launch_ref = publish_scene_run_evidence_v3(
        store, request_ref=request, replay_ref=replay_ref, output_dir=directory
    )
    body = json.loads(store.resolve(launch_ref).path.read_bytes())
    body["request"]["name"] = "another-request.json"
    foreign = publish(body, "foreign.json", body["schema_version"])

    class ForeignRunner:
        def run(self, request_ref):
            return foreign

    with pytest.raises(PreviewExecutionError, match="foreign"):
        LimitedImagePreviewExecution(store, ForeignRunner()).replay(request)


def test_physical_pass_can_fail_explicit_user_layout(execution):
    from self_improving.harness.limited_image_preview_execution import compare_preview_layout
    from self_improving.harness.schemas.limited_image_preview_execution import PreviewLayout

    module, replayed = execution
    physical = module.validate(replayed)
    assert physical.report.status == "passed"
    actual = PreviewLayout(x_m=0.0, y_m=0.0, yaw_rad=0.0)
    desired = PreviewLayout(x_m=0.1, y_m=0.0, yaw_rad=0.3)
    assert compare_preview_layout(actual, desired) == "mismatch"
    assert compare_preview_layout(actual, None) == "pass"


def test_formal_saved_m1_physics_pass_reports_real_layout_mismatch():
    import json
    import os
    from pathlib import Path

    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_asset_qualification_application import (
        GenesisAssetQualificationApplication,
    )
    from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
    from self_improving.harness.limited_image_preview_execution import validate_preview_receipt
    from self_improving.harness.schemas.common import ArtifactRef
    from self_improving.harness.schemas.limited_image_preview_execution import (
        LimitedSceneValidateInputV1,
    )

    configured = os.environ.get("LIMITED_PREVIEW_VALIDATION_CONFIG")
    if not configured:
        pytest.skip("requires isolated saved M1 CAS and trusted asset SQLite")
    config = json.loads(Path(configured).read_bytes())
    store = LocalArtifactStore(Path(config["cas_root"]))
    asset = GenesisAssetQualificationApplication(
        Path(config["asset_state_path"]),
        store,
        ArtifactRef.model_validate(config["asset_manifest"]),
    )
    compiler = Image2EnvGenesisCompiler(store, asset)
    result = validate_preview_receipt(
        store,
        compiler,
        LimitedSceneValidateInputV1(
            schema_version="harness.limited_scene_validate_input.v1",
            execution_receipt=config["execution_receipt"],
            expected_layout={"x_m": 9.0, "y_m": 9.0, "yaw_rad": 0.0},
        ),
    )
    assert result.status == "failed" and result.semantic_status == "mismatch"
    assert result.physical_scope == "dual_dt_trace_contact_settling_and_media"
    assert abs(result.actual_layout.x_m) < 1.0 and abs(result.actual_layout.y_m) < 1.0
    Path(config["result_path"]).write_text(result.model_dump_json())
