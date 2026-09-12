"""Controller revision seam changes actual scene bytes and preserves previous versions."""

import json

import pytest

from self_improving.harness.x2env.compile import ResolvedAsset, ResolvedAssetSet
from self_improving.harness.x2env.contracts import Pose, SceneIR
from self_improving.harness.x2env.diagnosis import DiagnosisProposal, DiagnosisResult, ScenePatch
from tests.self_improving.harness.x2env.test_resolver import inputs


def test_layout_revision_changes_scene_bytes_and_stops_repeated_failure(tmp_path):
    from self_improving.harness.x2env.revision import apply_revision

    store, registry, version, scene_ref, _ = inputs(tmp_path)
    assets = ResolvedAssetSet(
        scene_ir=scene_ref,
        assets=(
            ResolvedAsset(
                entity_id="box",
                version_sha256=version.version_sha256,
                acquisition_source="local",
                selection="exact",
            ),
        ),
    )
    model_log = store.write_artifact(b"explicit advisory double", "text/plain")
    proposal = DiagnosisProposal(
        base_revision=0,
        visual_intent="failed",
        reason="move x",
        evidence_sha256=(scene_ref.sha256,),
        scene_patches=(
            ScenePatch(
                entity_id="box",
                pose=Pose(frame="world", position=(0.1, None, None), yaw_degrees=30.0),
                joints=(),
            ),
        ),
        asset_patches=(),
    )
    diagnosis = DiagnosisResult(status="completed", proposal=proposal, receipt=model_log)
    diagnosis_ref = store.write_artifact(diagnosis.model_dump_json().encode(), "application/json")
    result = apply_revision(
        store,
        registry,
        scene_ref,
        assets,
        diagnosis_ref,
        output_root=tmp_path / "revision",
        history=(),
        failure_fingerprint="a" * 64,
    )
    revised = SceneIR.model_validate_json(store.read_artifact(result.scene_ir))
    assert revised.revision == 1
    assert revised.entities[0].pose.position == (0.1, 0.0, 0.0)
    assert revised.entities[0].pose.yaw_degrees == 30.0
    assert result.scene_ir != scene_ref and result.assets.scene_ir == result.scene_ir
    assert result.assets.assets == assets.assets
    assert SceneIR.model_validate_json(store.read_artifact(scene_ref)).entities[
        0
    ].pose.position == (0.0, 0.0, 0.0)
    assert registry.inspect(version.version_sha256) == version
    assert json.loads(store.read_artifact(result.receipt))["cost"] == 1
    with pytest.raises(ValueError, match="repeated_failure"):
        apply_revision(
            store,
            registry,
            scene_ref,
            assets,
            diagnosis_ref,
            output_root=tmp_path / "repeat",
            history=(result.receipt,),
            failure_fingerprint="a" * 64,
        )
    assert not (tmp_path / "repeat").exists()
    forged = json.loads(store.read_artifact(result.receipt))
    forged["cost"] = -10
    bad_history = store.write_artifact(json.dumps(forged).encode(), "application/json")
    with pytest.raises(ValueError, match="revision_history_mismatch"):
        apply_revision(
            store,
            registry,
            scene_ref,
            assets,
            diagnosis_ref,
            output_root=tmp_path / "negative",
            history=(bad_history,),
            failure_fingerprint="b" * 64,
        )


def test_asset_revision_consumes_the_same_budget_and_preserves_old_bytes(tmp_path):
    from self_improving.harness.x2env.asset_revision import AssetPatch
    from self_improving.harness.x2env.diagnosis import SuggestedAssetPatch
    from self_improving.harness.x2env.revision import apply_revision

    store, registry, version, scene_ref, _ = inputs(tmp_path)
    assets = ResolvedAssetSet(
        scene_ir=scene_ref,
        assets=(
            ResolvedAsset(
                entity_id="box",
                version_sha256=version.version_sha256,
                acquisition_source="local",
                selection="exact",
            ),
        ),
    )
    model_log = store.write_artifact(b"explicit advisory double", "text/plain")
    proposal = DiagnosisProposal(
        base_revision=0,
        visual_intent="failed",
        reason="friction diagnosis",
        evidence_sha256=(scene_ref.sha256,),
        scene_patches=(),
        asset_patches=(
            SuggestedAssetPatch(
                entity_id="box",
                parent_version=version.version_sha256,
                patch=AssetPatch(friction=0.4),
            ),
        ),
    )
    diagnosis = DiagnosisResult(status="completed", proposal=proposal, receipt=model_log)
    diagnosis_ref = store.write_artifact(diagnosis.model_dump_json().encode(), "application/json")
    first = apply_revision(
        store,
        registry,
        scene_ref,
        assets,
        diagnosis_ref,
        output_root=tmp_path / "first",
        history=(),
        failure_fingerprint="a" * 64,
    )
    child = registry.inspect(first.assets.assets[0].version_sha256)
    assert (
        child.parent_version == version.version_sha256
        and child.geometry_sha256 == version.geometry_sha256
    )
    assert registry.inspect(version.version_sha256) == version
    proposal = proposal.model_copy(
        update={
            "base_revision": 1,
            "asset_patches": (),
            "scene_patches": (
                ScenePatch(
                    entity_id="box",
                    pose=Pose(frame="world", position=(0.1, None, None), yaw_degrees=0.0),
                    joints=(),
                ),
            ),
        }
    )
    second_ref = store.write_artifact(
        diagnosis.model_copy(update={"proposal": proposal}).model_dump_json().encode(),
        "application/json",
    )
    second = apply_revision(
        store,
        registry,
        first.scene_ir,
        first.assets,
        second_ref,
        output_root=tmp_path / "second",
        history=(first.receipt,),
        failure_fingerprint="b" * 64,
    )
    proposal = proposal.model_copy(update={"base_revision": 2})
    third_ref = store.write_artifact(
        diagnosis.model_copy(update={"proposal": proposal}).model_dump_json().encode(),
        "application/json",
    )
    with pytest.raises(ValueError, match="revision_budget_exhausted"):
        apply_revision(
            store,
            registry,
            second.scene_ir,
            second.assets,
            third_ref,
            output_root=tmp_path / "third",
            history=(first.receipt, second.receipt),
            failure_fingerprint="c" * 64,
        )
    assert not (tmp_path / "third").exists()
