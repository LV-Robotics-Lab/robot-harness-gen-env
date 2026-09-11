"""Shared runtime skills must not grow separate authorities or accept caller runners."""

import pytest

from self_improving.harness.limited_image_preview_application import LimitedImagePreviewApplication


def test_shared_application_rejects_unqualified_compiler_before_state_creation(tmp_path):
    root = tmp_path / "shared"
    with pytest.raises(TypeError, match="exact"):
        LimitedImagePreviewApplication(root, object(), object(), roots={})
    assert not root.exists()


def test_replay_candidate_missing_evidence_is_durably_blocked(tmp_path):
    from uuid import uuid4

    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_scene_execution_v3 import GenesisSceneExecutionProducerV3
    from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
    from self_improving.harness.revised_image_compile_application import (
        RevisedImageCompileApplication,
    )
    from self_improving.harness.revised_image_compiler import RevisedImage2EnvGenesisCompiler
    from self_improving.harness.schemas.genesis_scene_execution_v3 import (
        GenesisSceneCompileBindingV2,
    )
    from tests.self_improving.harness.test_genesis_scene_execution import reference

    store = LocalArtifactStore(tmp_path / "cas")
    compiler = RevisedImage2EnvGenesisCompiler(store, Image2EnvGenesisCompiler(store, object()))
    compile_app = RevisedImageCompileApplication(tmp_path / "compile", compiler)
    producer = GenesisSceneExecutionProducerV3(
        store, compiler, reference("harness.genesis_runtime_manifest.v1")
    )
    shared = LimitedImagePreviewApplication(tmp_path / "shared", compile_app, producer, roots={})
    request = GenesisSceneCompileBindingV2(
        schema_version="harness.genesis_scene_compile_binding.v2",
        compile_skill_ref="image2env.compile@3.0.0",
        compile_run_id=uuid4(),
        compile_request=reference("harness.image2env_genesis_compile_request.v2"),
        compile_receipt=reference("harness.image2env_genesis_compile_receipt.v2"),
        execution_request=reference("harness.genesis_scene_execution_request.v3"),
    )
    evaluated = shared.evaluate("image2env.replay@3.0.0", request)
    assert evaluated.state.status.value == "blocked"
    assert shared.run_state(evaluated.state.run_id) == evaluated.state
    assert shared.invocation(evaluated.state.run_id) == evaluated.invocation
    assert shared.skills == ()
    assert (tmp_path / "shared/runs.sqlite").is_file()
    with pytest.raises(ValueError, match="successful candidate"):
        shared.qualify(evaluated.state.run_id, evaluated.state.run_id)
    from tests.self_improving.harness.test_limited_image_preview_library import _normal_manifest

    manifest, _ = _normal_manifest(store, tmp_path)
    rejected = shared.evaluate("image2env.preview_publish@1.0.0", manifest)
    assert rejected.state.status.value == "failed"
    assert "configured verified library" in rejected.state.blocker.details["error"]
    assert shared.run_state(rejected.state.run_id) == rejected.state
    with pytest.raises(ValueError, match="successful candidate"):
        shared.qualify_publish(rejected.state.run_id)


def test_formal_semantic_mismatch_retains_diagnostic_in_shared_registry():
    import json
    import os
    import shutil
    import sqlite3
    import tempfile
    from pathlib import Path

    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_asset_qualification_application import (
        GenesisAssetQualificationApplication,
    )
    from self_improving.harness.genesis_scene_execution_v3 import GenesisSceneExecutionProducerV3
    from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
    from self_improving.harness.revised_image_compile_application import (
        RevisedImageCompileApplication,
    )
    from self_improving.harness.revised_image_compiler import RevisedImage2EnvGenesisCompiler
    from self_improving.harness.schemas.common import ArtifactRef
    from self_improving.harness.schemas.limited_image_preview_execution import (
        LimitedSceneValidateInputV1,
    )

    path = os.environ.get("LIMITED_PREVIEW_VALIDATION_CONFIG")
    if not path:
        pytest.skip("requires immutable saved M1 evidence; no Genesis execution")
    config = json.loads(Path(path).read_bytes())
    root = Path(
        tempfile.mkdtemp(
            prefix="golden-preview-semantic-registry-", dir="/home/jingxiang/bingsheng"
        )
    )
    shutil.copytree(config["cas_root"], root / "cas", copy_function=os.link)
    source = sqlite3.connect("file:" + config["asset_state_path"] + "?mode=ro", uri=True)
    target = sqlite3.connect(root / "asset.sqlite")
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    store = LocalArtifactStore(root / "cas")
    asset = GenesisAssetQualificationApplication(
        root / "asset.sqlite", store, ArtifactRef.model_validate(config["asset_manifest"])
    )
    compiler = RevisedImage2EnvGenesisCompiler(store, Image2EnvGenesisCompiler(store, asset))
    revised = RevisedImageCompileApplication(root / "compile", compiler)
    producer = GenesisSceneExecutionProducerV3(
        store, compiler, ArtifactRef.model_validate(config["asset_manifest"])
    )
    shared = LimitedImagePreviewApplication(root / "shared", revised, producer, roots={})
    outcome = shared.evaluate(
        "image2env.validate@3.0.0",
        LimitedSceneValidateInputV1(
            schema_version="harness.limited_scene_validate_input.v1",
            execution_receipt=config["execution_receipt"],
            expected_layout={"x_m": 9.0, "y_m": 9.0, "yaw_rad": 0.0},
        ),
    )
    assert outcome.state.status.value == "blocked"
    assert outcome.state.blocker.code == "PREVIEW_LAYOUT_MISMATCH"
    assert outcome.state.blocker.retryable is False
    refs = [
        ref
        for event in outcome.state.events
        for ref in event.artifact_refs
        if ref.schema_version == "harness.limited_scene_validation.v1"
    ]
    assert len(refs) == 1
    assert refs[0] in outcome.state.blocker.artifact_refs
    result = json.loads(store.resolve(refs[0]).path.read_bytes())
    assert result["semantic_status"] == "mismatch"
    assert any(refs[0] in event.artifact_refs for event in outcome.state.events)
    assert shared.run_state(outcome.state.run_id) == outcome.state
    (root / "result.json").write_text(
        json.dumps(
            {
                "run_id": str(outcome.state.run_id),
                "diagnostic": refs[0].model_dump(mode="json"),
                "state": outcome.state.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    Path("/tmp/preview-semantic-registry-root.txt").write_text(str(root))
