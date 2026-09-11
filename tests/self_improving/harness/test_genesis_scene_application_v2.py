"""Registered compile provenance is explicit in the final scene Skill input."""

from uuid import uuid1, uuid4

import pytest

from self_improving.harness.schemas.genesis_scene_application_v2 import GenesisSceneReplayInputV2
from tests.self_improving.harness.test_genesis_scene_execution import reference


def test_application_rejects_untrusted_producer_before_creating_state(tmp_path):
    from self_improving.harness.genesis_scene_application_v2 import GenesisSceneApplication

    with pytest.raises(TypeError, match="fixed v2 execution producer"):
        GenesisSceneApplication(tmp_path / "state", object(), roots={}, compile_application=None)
    assert not (tmp_path / "state").exists()


def build_formal_application(manifest_path, handoff_path, *, roots_enabled=True):
    """Real deployment construction shared by qualification and restart tests."""
    import json
    from pathlib import Path

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
    from self_improving.harness.schemas.common import ArtifactRef

    config = json.loads(manifest_path.read_bytes())
    handoff = json.loads(handoff_path.read_bytes())
    store = LocalArtifactStore(Path(config["cas_root"]))
    asset = GenesisAssetQualificationApplication(
        Path(handoff["asset_state_path"]),
        store,
        ArtifactRef.model_validate(handoff["approved_manifest_ref"]),
    )
    compiler = Image2EnvGenesisCompiler(store, asset)
    compile_app = Image2EnvGenesisCompileApplication(
        Path(handoff["state_root"]),
        compiler,
        qualification=ArtifactRef.model_validate(handoff["qualification_ref"]),
    )
    producer = GenesisSceneExecutionProducerV2(
        store, compiler, ArtifactRef.model_validate(config["manifest_ref"])
    )
    app = GenesisSceneApplication(
        manifest_path.parent / "scene-application-v2",
        producer,
        roots={role: Path(path) for role, path in config["roots"].items()} if roots_enabled else {},
        compile_application=compile_app,
    )
    return app, config, handoff


def test_formal_qualifies_two_registered_compile_candidates_and_restarts():
    import json
    import os
    from pathlib import Path
    from uuid import UUID

    configured = os.environ.get("GENESIS_SCENE_V2_QUALIFY")
    if not configured:
        pytest.skip("requires two actual successful v2 candidates")
    manifest_path = Path(os.environ["GENESIS_SCENE_V2_MANIFEST"])
    handoff_path = Path(os.environ["GENESIS_REGISTERED_COMPILE_HANDOFF"])
    app, _, _ = build_formal_application(manifest_path, handoff_path, roots_enabled=False)
    run_ids = tuple(UUID(value) for value in configured.split(","))
    descriptor = app.qualify(run_ids)
    qualification = descriptor.qualification_artifact
    summary = dict(
        qualification_ref=qualification.model_dump(mode="json"),
        candidates=list(configured.split(",")),
        implementation_sha256=app.implementation_sha256,
        numeric_identity=app.numeric_identity,
    )
    (manifest_path.parent / "replay-qualification-summary.json").write_text(
        json.dumps(summary, indent=2)
    )
    restarted, _, _ = build_formal_application(manifest_path, handoff_path, roots_enabled=False)
    restarted.bind(qualification)
    assert restarted.skills == app.skills
    assert len(restarted.skills) == 1
    for run_id in run_ids:
        assert restarted.run_state(run_id) == app.run_state(run_id)


def test_formal_existing_qualification_restarts_without_runtime_roots():
    import json
    import os
    from pathlib import Path

    from self_improving.harness.schemas.common import ArtifactRef

    configured = os.environ.get("GENESIS_SCENE_V2_EXISTING_QUALIFICATION")
    if not configured:
        pytest.skip("requires an already issued replay qualification")
    manifest_path = Path(os.environ["GENESIS_SCENE_V2_MANIFEST"])
    handoff_path = Path(os.environ["GENESIS_REGISTERED_COMPILE_HANDOFF"])
    qualification = ArtifactRef.model_validate(json.loads(Path(configured).read_bytes()))
    app, _, _ = build_formal_application(manifest_path, handoff_path, roots_enabled=False)
    descriptor = app.bind(qualification)
    assert len(app.skills) == 1
    assert descriptor.qualification_artifact == qualification
    summary = dict(
        qualification_ref=qualification.model_dump(mode="json"),
        registered=True,
        restart_without_roots=True,
        descriptor=descriptor.model_dump(mode="json"),
        qualification_report_sha256=json.loads(
            app.producer.artifact_store.resolve(qualification).path.read_bytes()
        )["report_sha256"],
        implementation_sha256=app.implementation_sha256,
        numeric_identity=app.numeric_identity,
    )
    (manifest_path.parent / "replay-qualification-summary.json").write_text(
        json.dumps(summary, indent=2)
    )


def test_formal_registered_compile_candidate():
    import json
    import os
    import time
    from pathlib import Path

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
    from self_improving.harness.schemas.common import ArtifactRef
    from self_improving.harness.schemas.genesis_scene_execution_v2 import (
        GenesisSceneExecutionRequestV2,
    )

    configured = os.environ.get("GENESIS_SCENE_V2_MANIFEST")
    if not configured:
        pytest.skip("requires the fixed v2 runtime and registered compile deployment")
    manifest_path = Path(configured)
    config = json.loads(manifest_path.read_bytes())
    handoff = json.loads(Path(os.environ["GENESIS_REGISTERED_COMPILE_HANDOFF"]).read_bytes())
    selected = handoff["runs"][int(os.environ.get("GENESIS_SCENE_COMPILE_INDEX", "0"))]
    store = LocalArtifactStore(Path(config["cas_root"]))
    asset = GenesisAssetQualificationApplication(
        Path(handoff["asset_state_path"]),
        store,
        ArtifactRef.model_validate(handoff["approved_manifest_ref"]),
    )
    compiler = Image2EnvGenesisCompiler(store, asset)
    compile_app = Image2EnvGenesisCompileApplication(
        Path(handoff["state_root"]),
        compiler,
        qualification=ArtifactRef.model_validate(handoff["qualification_ref"]),
    )
    approved = ArtifactRef.model_validate(config["manifest_ref"])
    producer = GenesisSceneExecutionProducerV2(store, compiler, approved)
    app = GenesisSceneApplication(
        manifest_path.parent / "scene-application-v2",
        producer,
        roots={role: Path(path) for role, path in config["roots"].items()},
        compile_application=compile_app,
    )
    request = GenesisSceneExecutionRequestV2.model_validate(
        dict(
            schema_version="harness.genesis_scene_execution_request.v2",
            runtime_manifest=config["manifest_ref"],
            scene=selected["scene_ref"],
            compile_receipt=selected["compile_receipt_ref"],
            asset_qualification=selected["asset_qualification_ref"],
        )
    )
    request_path = manifest_path.parent / f"request-{selected['seed']}.json"
    request_path.write_text(
        json.dumps(
            request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    )
    request_ref = store.put_file(
        request_path,
        name=request_path.name,
        media_type="application/json",
        schema_version=request.schema_version,
    )
    parameters = GenesisSceneReplayInputV2.model_validate(
        dict(
            schema_version="harness.genesis_scene_replay_input.v2",
            compile_run_id=selected["run_id"],
            execution_request=request_ref,
        )
    )
    started = time.monotonic()
    result = app.evaluate(parameters)
    summary = dict(
        run_id=str(result.state.run_id),
        elapsed_seconds=time.monotonic() - started,
        parameters=parameters.model_dump(mode="json"),
        implementation_sha256=app.implementation_sha256,
        state=result.state.model_dump(mode="json"),
        invocation=result.invocation.model_dump(mode="json") if result.invocation else None,
    )
    output = manifest_path.parent / f"candidate-{result.state.run_id}.json"
    output.write_text(json.dumps(summary, indent=2))
    print(output, flush=True)
    assert result.state.status.value == "succeeded", result.state.blocker
    assert app.skills == ()
    from tests.self_improving.harness.test_genesis_scene_observation_application import (
        evaluate_real_assessment_candidates,
    )

    assessment = evaluate_real_assessment_candidates(app, result.state.run_id, manifest_path.parent)
    print(json.dumps(assessment), flush=True)


def test_final_scene_input_requires_compile_uuid4_and_typed_execution_request():
    payload = dict(
        schema_version="harness.genesis_scene_replay_input.v2",
        compile_run_id=uuid4(),
        execution_request=reference("harness.genesis_scene_execution_request.v2"),
    )
    assert (
        GenesisSceneReplayInputV2.model_validate(payload).compile_run_id
        == payload["compile_run_id"]
    )
    payload["compile_run_id"] = uuid1()
    with pytest.raises(ValueError):
        GenesisSceneReplayInputV2.model_validate(payload)
    payload["compile_run_id"] = uuid4()
    payload["execution_request"]["schema_version"] = "harness.genesis_scene_replay.v1"
    with pytest.raises(ValueError, match="exact lower-level"):
        GenesisSceneReplayInputV2.model_validate(payload)
