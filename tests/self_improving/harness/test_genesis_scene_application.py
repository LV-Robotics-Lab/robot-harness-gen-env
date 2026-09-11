"""Scene candidate execution uses the existing durable Harness kernel."""

from uuid import uuid4

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.genesis_scene_application import GenesisSceneApplication
from self_improving.harness.genesis_scene_execution import GenesisSceneExecutionProducer
from self_improving.harness.schemas.genesis_scene_execution import GenesisSceneExecutionRequestV1
from tests.self_improving.harness.test_genesis_scene_execution import request


def test_candidate_failure_is_durable_and_does_not_register_skill(tmp_path):
    store = LocalArtifactStore(tmp_path / "cas")
    producer = GenesisSceneExecutionProducer(store, None, None)
    app = GenesisSceneApplication(tmp_path / "state", producer, roots={})
    result = app.evaluate(GenesisSceneExecutionRequestV1.model_validate(request()))
    assert result.state.status.value == "blocked"
    assert app.skills == ()
    restarted = GenesisSceneApplication(tmp_path / "state", producer, roots={})
    assert restarted.run_state(result.state.run_id) == result.state
    with pytest.raises(ValueError, match="two distinct"):
        app.qualify((result.state.run_id, result.state.run_id))
    with pytest.raises(ValueError, match="successful candidate"):
        app.qualify((result.state.run_id, uuid4()))


def test_application_rejects_caller_supplied_execution_implementations(tmp_path):
    with pytest.raises(TypeError, match="fixed execution producer"):
        GenesisSceneApplication(tmp_path / "state", object(), roots={})
    assert not (tmp_path / "state").exists()


def test_formal_scene_candidate_executes_fixed_dual_profiles_in_durable_kernel():
    import json
    import os
    import time
    from pathlib import Path
    from uuid import UUID

    from self_improving.harness.genesis_asset_qualification_application import (
        GenesisAssetQualificationApplication,
    )
    from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
    from self_improving.harness.schemas.common import ArtifactRef

    configured = os.environ.get("GENESIS_SCENE_EXECUTION_CONFIG")
    if not configured:
        pytest.skip("requires the fixed scene runtime deployment")
    config_path = Path(configured)
    config = json.loads(config_path.read_bytes())
    native_path = Path(os.environ["GENESIS_NATIVE_COMPILE_CONFIG"])
    native = json.loads(native_path.read_bytes())
    store = LocalArtifactStore(Path(config["cas_root"]))
    asset_app = GenesisAssetQualificationApplication(
        Path(native["state_path"]),
        store,
        ArtifactRef.model_validate(native["approved_manifest_ref"]),
    )
    compiler = Image2EnvGenesisCompiler(store, asset_app)
    producer = GenesisSceneExecutionProducer(
        store, compiler, ArtifactRef.model_validate(config["runtime_manifest_ref"])
    )
    app = GenesisSceneApplication(
        config_path.parent / "scene-application",
        producer,
        roots={role: Path(path) for role, path in config["roots"].items()},
    )
    request_ref = ArtifactRef.model_validate(config["request_ref"])
    parameters = GenesisSceneExecutionRequestV1.model_validate_json(
        store.resolve(request_ref).path.read_bytes()
    )
    started = time.monotonic()
    result = app.evaluate(parameters)
    elapsed = time.monotonic() - started
    summary = {
        "run_id": str(result.state.run_id),
        "elapsed_seconds": elapsed,
        "implementation_sha256": app.implementation_sha256,
        "state": result.state.model_dump(mode="json"),
        "invocation": result.invocation.model_dump(mode="json") if result.invocation else None,
    }
    output = config_path.parent / ("scene-candidate-" + str(result.state.run_id) + ".json")
    output.write_text(json.dumps(summary, indent=2))
    print(str(output), flush=True)
    assert result.state.status.value == "succeeded", result.state.blocker
    assert app.skills == ()
    restarted = GenesisSceneApplication(
        config_path.parent / "scene-application", producer, roots={}
    )
    assert restarted.run_state(result.state.run_id) == result.state
    assert restarted.invocation(result.state.run_id) == result.invocation
    for saved in config_path.parent.glob("scene-candidate-*.json"):
        previous = json.loads(saved.read_bytes())
        if previous["state"]["status"] == "failed":
            assert (
                restarted.run_state(UUID(previous["run_id"])).model_dump(mode="json")
                == previous["state"]
            )


def test_formal_two_candidates_qualify_then_durable_production_and_restart():
    import json
    import os
    from pathlib import Path
    from uuid import UUID

    from self_improving.harness.genesis_asset_qualification_application import (
        GenesisAssetQualificationApplication,
    )
    from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
    from self_improving.harness.schemas.common import ArtifactRef

    configured = os.environ.get("GENESIS_SCENE_EXECUTION_CONFIG")
    if not configured:
        pytest.skip("requires two fixed-runtime successful candidate runs")
    config_path = Path(configured)
    config = json.loads(config_path.read_bytes())
    native = json.loads(Path(os.environ["GENESIS_NATIVE_COMPILE_CONFIG"]).read_bytes())
    store = LocalArtifactStore(Path(config["cas_root"]))
    asset_app = GenesisAssetQualificationApplication(
        Path(native["state_path"]),
        store,
        ArtifactRef.model_validate(native["approved_manifest_ref"]),
    )
    producer = GenesisSceneExecutionProducer(
        store,
        Image2EnvGenesisCompiler(store, asset_app),
        ArtifactRef.model_validate(config["runtime_manifest_ref"]),
    )
    roots = {role: Path(path) for role, path in config["roots"].items()}
    app = GenesisSceneApplication(config_path.parent / "scene-application", producer, roots=roots)
    summaries = [
        json.loads(path.read_bytes())
        for path in sorted(config_path.parent.glob("scene-candidate-*.json"))
    ]
    passed = [
        summary
        for summary in summaries
        if summary["state"]["status"] == "succeeded"
        and summary["implementation_sha256"] == app.implementation_sha256
    ]
    assert len(passed) >= 2, "two fresh successful fixed-implementation candidates are required"
    run_ids = tuple(UUID(summary["run_id"]) for summary in passed[-2:])
    descriptor = app.qualify(run_ids)
    assert descriptor.reproducibility.value == "evidence_invariant_repeatable"
    qualified = config_path.parent / "scene-skill-descriptor.json"
    qualified.write_text(json.dumps(descriptor.model_dump(mode="json"), indent=2))
    request_ref = ArtifactRef.model_validate(config["request_ref"])
    parameters = GenesisSceneExecutionRequestV1.model_validate_json(
        store.resolve(request_ref).path.read_bytes()
    )
    run_id = uuid4()
    result = app.invoke(parameters, run_id=run_id)
    summary_path = config_path.parent / ("scene-production-" + str(run_id) + ".json")
    summary_path.write_text(json.dumps(result.model_dump(mode="json"), indent=2))
    assert result.status.value == "succeeded", result.blocker
    del app
    restarted = GenesisSceneApplication(
        config_path.parent / "scene-application", producer, roots={}
    )
    assert restarted.bind(descriptor.qualification_artifact) == descriptor
    assert restarted.invoke(parameters, run_id=run_id) == result
    assert restarted.run_state(run_id) == result
    summary = dict(
        candidate_run_ids=[str(run_id) for run_id in run_ids],
        production_run_id=str(run_id),
        descriptor=descriptor.model_dump(mode="json"),
        production_terminal=str(summary_path),
        restart_without_live_roots=True,
    )
    (config_path.parent / "scene-skill-qualification-summary.json").write_text(
        json.dumps(summary, indent=2)
    )
