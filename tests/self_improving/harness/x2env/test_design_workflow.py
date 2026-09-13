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
@pytest.mark.parametrize("critical", [False, True])
@pytest.mark.parametrize("conflict", [False, True])
@pytest.mark.parametrize("measured", [False, True])
@pytest.mark.parametrize("structural_case", [False, True])
def test_unknown_scale_is_pending_until_asset_anchored_design_commits(
    tmp_path, enabled, critical, conflict, measured, structural_case
):
    store, backend, bundle, proposal, assets = setup(tmp_path, measured=measured, conflict=conflict)
    original = BackendProposal.model_validate_json(store.read_artifact(proposal))
    original = original.model_copy(
        update={
            "proposal": original.proposal.model_copy(
                update={
                    "unknowns": tuple(
                        u.model_copy(update={"critical": critical})
                        for u in original.proposal.unknowns
                    )
                }
            )
        }
    )
    if structural_case and not conflict:
        document = original.model_dump(mode="json")
        document["proposal"]["unknowns"][0].update(
            field="scene.entities.support.pose", reason_kind="unspecified"
        )
        original = BackendProposal.model_validate_json(json.dumps(document))
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
        scene_design_policy=SceneDesignPolicy(
            enabled=enabled,
            structural_defaults_enabled=structural_case,
            world_anchor_xy=(0.1, 0.0) if structural_case else None,
            world_anchor_yaw_degrees=0.0 if structural_case else None,
        ),
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
    assert original.proposal.unknowns[0].critical is critical


@pytest.mark.parametrize("enabled", [False, True])
def test_omitted_unknown_list_cannot_bypass_actual_missing_design_fields(tmp_path, enabled):
    store, backend, _, proposal, assets = setup(tmp_path, measured=True)
    original = BackendProposal.model_validate_json(store.read_artifact(proposal))
    original = original.model_copy(
        update={"proposal": original.proposal.model_copy(update={"unknowns": ()})}
    )
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
            assert checkpoint.scene_ir is None and checkpoint.pending_scene_ir == scene
            calls.append("resolve")
            resolved = ResolvedAssetSet.model_validate_json(store.read_artifact(assets))
            return ResolutionResult(
                status="succeeded",
                resolved=resolved.model_copy(update={"scene_ir": scene}),
                receipt=assets,
            )

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
    assert BackendProposal.model_validate_json(store.read_artifact(result.proposal)) == original
    if not enabled:
        assert result.stop_reason == "clarification_required" and calls == []
        assert result.compiled_scene is None
    else:
        assert calls == ["resolve", "ground"]
        assert result.compiled_scene is not None and result.pending_scene_ir is None
        receipt = json.loads(store.read_artifact(result.grounding))
        assert receipt["resolved_unknowns"] == receipt["original_unknowns"] == []
        assert receipt["design_plan"]["rules"]
        assert receipt["design_plan"]["requires_media"] is True


@pytest.mark.parametrize("enabled", [False, True])
def test_reported_conflict_blocks_even_when_fields_are_filled_and_noncritical(tmp_path, enabled):
    store, _, _, proposal, _ = setup(tmp_path, measured=True)
    document = json.loads(store.read_artifact(proposal))
    for entity in document["proposal"]["scene"]["entities"]:
        entity["dimensions"] = [0.1, 0.1, 0.1]
        entity["pose"]["position"] = [0, 0, 0.5]
        entity["pose"]["yaw_degrees"] = 0
    document["proposal"]["unknowns"][0].update(
        critical=False, reason_kind="conflict", field="scene.entities.support.dimensions"
    )
    original = BackendProposal.model_validate_json(json.dumps(document))

    class ModelDouble:
        def interpret(self, bundle, **kwargs):
            return original

    harness = Harness(
        tmp_path / "state",
        backend_factory=lambda _: ModelDouble(),
        scene_design_policy=SceneDesignPolicy(enabled=enabled),
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
    assert result.stop_reason == "clarification_required"
    assert result.scene_ir is None and result.pending_scene_ir is None
    assert BackendProposal.model_validate_json(store.read_artifact(result.proposal)) == original


def test_recovered_dead_grounding_owner_does_not_become_permanent_design_failure(tmp_path):
    from self_improving.harness.x2env.contracts import ToolResult

    store, backend, bundle, proposal, assets = setup(tmp_path, measured=True)
    pending = ResolvedAssetSet.model_validate_json(store.read_artifact(assets)).scene_ir
    handle = store.submit(
        X2EnvRequest(
            images=(InputMedia(path=str(tmp_path / "input.png")),),
            seed=23,
            idempotency_key="ground",
            output_dir=str(tmp_path / "out"),
        )
    )
    snapshot = store.claim(handle.workflow_id)
    for capability, refs, updates in [
        ("ingest", (bundle,), {}),
        (
            "codex.interpret",
            (proposal, pending),
            {"proposal": proposal, "pending_scene_ir": pending},
        ),
        ("asset.resolve", (assets,), {"resolved_assets": assets}),
    ]:
        if capability != "ingest":
            snapshot = store.begin_operation(snapshot, capability)
        snapshot = store.complete_operation(
            snapshot,
            ToolResult(
                operation_id=snapshot.operations[-1].operation_id,
                status="succeeded",
                outputs=refs,
            ),
            bundle,
            status="active",
            **updates,
        )
    snapshot = store.begin_operation(snapshot, "codex.ground")
    snapshot = store.complete_operation(
        snapshot,
        ToolResult(
            operation_id=snapshot.operations[-1].operation_id,
            status="failed",
            error_code="recoverable_dead_owner",
        ),
        bundle,
        status="blocked",
        reason="recoverable_dead_owner",
    )
    harness = Harness(
        tmp_path / "state",
        backend_factory=lambda _: backend,
        scene_design_policy=SceneDesignPolicy(enabled=True),
        compile_policy=StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5),
    )
    result = harness.resume(handle.workflow_id)
    assert result.compiled_scene is not None
    assert result.grounding is not None
