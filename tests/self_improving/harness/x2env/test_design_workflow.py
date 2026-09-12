"""Controller design grounding with an explicit external-model executable double."""

import json

import pytest

from self_improving.harness.x2env.compile import ResolvedAssetSet, StructuralPolicy
from self_improving.harness.x2env.contracts import BackendProposal, InputMedia, X2EnvRequest
from self_improving.harness.x2env.grounding import SceneDesignPolicy
from self_improving.harness.x2env.harness import Harness
from self_improving.harness.x2env.resolver import ResolutionResult
from tests.self_improving.harness.x2env.test_grounding import setup


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("conflict", [False, True])
@pytest.mark.parametrize("measured", [False, True])
def test_unknown_scale_is_pending_until_asset_anchored_design_commits(
    tmp_path, enabled, conflict, measured
):
    store, backend, bundle, proposal, assets = setup(tmp_path, measured=measured, conflict=conflict)
    original = BackendProposal.model_validate_json(store.read_artifact(proposal))
    calls = []

    class ModelDouble:
        def interpret(self, bundle, **kwargs):
            return original

        def ground_scene(self, *args, **kwargs):
            calls.append("ground")
            return backend.ground_scene(*args, **kwargs)

    class ResolverDouble:
        def resolve(self, scene, **kwargs):
            checkpoint = harness.status(handle.workflow_id)
            assert checkpoint.scene_ir is None
            assert checkpoint.pending_scene_ir == scene
            calls.append("resolve")
            resolved = ResolvedAssetSet.model_validate_json(store.read_artifact(assets))
            resolved = resolved.model_copy(update={"scene_ir": scene})
            return ResolutionResult(status="succeeded", resolved=resolved, receipt=assets)

    harness = Harness(
        tmp_path / "state",
        backend_factory=lambda _: ModelDouble(),
        resolver_factory=lambda *_: ResolverDouble(),
        scene_design_policy=SceneDesignPolicy(enabled=enabled),
        compile_policy=StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5),
    )
    handle = harness.submit(
        X2EnvRequest(
            images=(InputMedia(path=str(tmp_path / "input.png")),),
            seed=23,
            idempotency_key="ground",
            output_dir=str(tmp_path / "out"),
        )
    )
    result = harness.resume(handle.workflow_id)
    if not enabled or conflict:
        assert result.stop_reason == "clarification_required" and calls == []
        assert result.scene_ir is None
        return
    assert calls == ["resolve", "ground"]
    if not measured:
        assert result.stop_reason == "missing_measured_anchor_dimensions"
        assert result.scene_ir is None and result.compiled_scene is None
        assert harness.resume(handle.workflow_id) == result
        assert calls == ["resolve", "ground"]
        return
    assert result.pending_scene_ir is None
    assert result.scene_ir is not None and result.compiled_scene is not None, result
    assert [o.capability for o in result.operations][-2:] == ["codex.ground", "x2env.compile"]
    grounded_assets = ResolvedAssetSet.model_validate_json(
        store.read_artifact(result.resolved_assets)
    )
    assert grounded_assets.scene_ir == result.scene_ir
    assert BackendProposal.model_validate_json(store.read_artifact(result.proposal)) == original
    receipt = json.loads(store.read_artifact(result.grounding))
    assert receipt["real_world_scale_recovered"] is False
    assert original.proposal.unknowns[0].critical is True
