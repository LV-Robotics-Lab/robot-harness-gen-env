import pytest


def test_assessment_has_separate_candidate_only_entrypoints():
    from self_improving.harness.genesis_scene_assessment import GenesisSceneAssessment

    assert callable(GenesisSceneAssessment.observe_candidate)
    assert callable(GenesisSceneAssessment.validate_candidate)


def test_capture_freshness_preserves_actual_time_and_rejects_expiry():
    from datetime import datetime, timedelta, timezone

    from self_improving.harness.genesis_scene_assessment import require_fresh_capture

    captured = datetime(2026, 9, 11, tzinfo=timezone.utc)
    assert (
        require_fresh_capture(captured, now=captured + timedelta(seconds=30), ttl_seconds=30)
        == captured
    )
    for now in (
        captured - timedelta(microseconds=1),
        captured + timedelta(seconds=30, microseconds=1),
    ):
        with pytest.raises(ValueError, match="fresh"):
            require_fresh_capture(captured, now=now, ttl_seconds=30)
    for ttl in (True, 0, 301, 1.5):
        with pytest.raises(ValueError, match="TTL"):
            require_fresh_capture(captured, now=captured, ttl_seconds=ttl)


def test_fresh_observation_rejects_non_camera_steps_and_invalid_lifetime():
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4

    from self_improving.harness.schemas.genesis_scene_assessment import (
        GenesisSceneFreshObservationV1,
    )
    from tests.self_improving.harness.test_genesis_scene_execution import reference

    stamp = datetime(2026, 9, 11, tzinfo=timezone.utc)
    image = reference(None)
    image["media_type"] = "image/png"
    payload = dict(
        schema_version="harness.genesis_scene_fresh_observation.v1",
        replay_run_id=uuid4(),
        execution_receipt=reference("harness.genesis_scene_execution_receipt.v2"),
        process_observation=reference("harness.genesis_scene_process_observation.v2"),
        image=image,
        captured_at=stamp,
        checked_at=stamp,
        expires_at=stamp + timedelta(seconds=30),
        simulation_time_s=4.0,
        step=2000,
        profile="half_dt",
        simulator_rerun=False,
    )
    assert GenesisSceneFreshObservationV1(**payload).captured_at == stamp
    for field, value in (
        ("step", True),
        ("step", 1999),
        ("simulation_time_s", 3.9),
        ("checked_at", stamp + timedelta(seconds=31)),
    ):
        with pytest.raises(ValueError):
            GenesisSceneFreshObservationV1(**{**payload, field: value})


def test_assessment_rejects_an_untrusted_terminal_provider():
    from self_improving.harness.genesis_scene_assessment import GenesisSceneAssessment

    with pytest.raises(TypeError, match="trusted GenesisSceneApplication"):
        GenesisSceneAssessment(object())


def test_assessment_requires_a_qualified_replay_before_reading_terminal(tmp_path):
    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_asset_qualification_application import (
        GenesisAssetQualificationApplication,
    )
    from self_improving.harness.genesis_scene_application_v2 import GenesisSceneApplication
    from self_improving.harness.genesis_scene_execution_v2 import GenesisSceneExecutionProducerV2
    from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
    from self_improving.harness.image2env_genesis_compile_application import (
        Image2EnvGenesisCompileApplication,
    )

    store = LocalArtifactStore(tmp_path / "cas")
    source = tmp_path / "manifest.json"
    source.write_text("{}")
    ref = store.put_file(
        source,
        name=source.name,
        media_type="application/json",
        schema_version="harness.genesis_runtime_manifest.v1",
    )
    asset_app = GenesisAssetQualificationApplication(tmp_path / "asset.sqlite", store, ref)
    compiler = Image2EnvGenesisCompiler(store, asset_app)
    producer = GenesisSceneExecutionProducerV2(store, compiler, ref)
    compile_app = Image2EnvGenesisCompileApplication(tmp_path / "compile", compiler)
    with pytest.raises(ValueError, match="registered native compile"):
        GenesisSceneApplication(
            tmp_path / "scene", producer, roots={}, compile_application=compile_app
        )


def test_registered_real_scene_validation_and_capture_lifetime():
    """Consume an actual registered replay; this test never reruns Genesis."""
    import json
    import os
    from pathlib import Path
    from uuid import UUID, uuid4

    configured = os.environ.get("GENESIS_SCENE_ASSESSMENT_CONFIG")
    if not configured:
        pytest.skip("requires registered production scene replay configuration")
    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_asset_qualification_application import (
        GenesisAssetQualificationApplication,
    )
    from self_improving.harness.genesis_scene_application_v2 import GenesisSceneApplication
    from self_improving.harness.genesis_scene_assessment import GenesisSceneAssessment
    from self_improving.harness.genesis_scene_execution_v2 import GenesisSceneExecutionProducerV2
    from self_improving.harness.genesis_scene_validation_application import (
        GenesisSceneValidationApplication,
    )
    from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
    from self_improving.harness.image2env_genesis_compile_application import (
        Image2EnvGenesisCompileApplication,
    )
    from self_improving.harness.schemas.common import ArtifactRef
    from self_improving.harness.schemas.genesis_scene_assessment import GenesisSceneValidateInputV1

    config = json.loads(Path(configured).read_bytes())
    ref = ArtifactRef.model_validate
    store = LocalArtifactStore(Path(config["cas_root"]))
    assets = GenesisAssetQualificationApplication(
        Path(config["asset_state_path"]), store, ref(config["asset_manifest"])
    )
    compiler = Image2EnvGenesisCompiler(store, assets)
    compile_app = Image2EnvGenesisCompileApplication(
        Path(config["compile_state_root"]),
        compiler,
        qualification=ref(config["compile_qualification"]),
    )
    producer = GenesisSceneExecutionProducerV2(store, compiler, ref(config["scene_manifest"]))
    replay_app = GenesisSceneApplication(
        Path(config["scene_state_root"]),
        producer,
        roots=config["roots"],
        compile_application=compile_app,
    )
    replay_app.bind(ref(config["scene_qualification"]))
    assessment = GenesisSceneAssessment(replay_app)
    replay_run = UUID(config["replay_run_id"])
    try:
        observation = assessment.observe(replay_run, ttl_seconds=300)
        freshness = {"status": "fresh", "receipt": observation.model_dump(mode="json")}
    except ValueError as error:
        assert "capture is not fresh" in str(error)
        freshness = {"status": "expired", "error": str(error)}
    state_root = Path(config["validation_state_root"])
    app = GenesisSceneValidationApplication(state_root, assessment)
    request = GenesisSceneValidateInputV1(
        schema_version="harness.genesis_scene_validate_input.v1", replay_run_id=replay_run
    )
    candidates = [
        app.evaluate(
            GenesisSceneValidateInputV1(
                schema_version="harness.genesis_scene_validate_input.v1",
                replay_run_id=UUID(candidate_run),
            )
        )
        for candidate_run in config["candidate_replay_runs"]
    ]
    assert all(item.state.status.value == "succeeded" for item in candidates)
    descriptor = app.qualify(tuple(item.state.run_id for item in candidates))
    run_id = uuid4()
    state = app.invoke(request, run_id=run_id)
    assert state.status.value == "succeeded"
    original = app.read(run_id)
    restored = GenesisSceneValidationApplication(state_root, assessment)
    restored.bind(descriptor.qualification_artifact)
    assert restored.read(run_id) == original
    assert restored.invoke(request, run_id=run_id) == state
    Path(config["summary_path"]).write_text(
        json.dumps(
            {
                "freshness": freshness,
                "run_id": str(run_id),
                "validation": original[0].model_dump(mode="json"),
                "qualification": descriptor.qualification_artifact.model_dump(mode="json"),
                "restart_equal": True,
                "simulator_rerun": False,
            },
            indent=2,
        )
    )
