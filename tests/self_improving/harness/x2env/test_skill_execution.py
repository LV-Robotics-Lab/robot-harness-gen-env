"""Exact capability execution over real compiler/Store; not runtime qualification."""

from self_improving.harness.x2env.compile import ResolvedAsset, ResolvedAssetSet, StructuralPolicy
from tests.self_improving.harness.x2env.test_resolver import inputs


def test_compile_capability_consumes_typed_scene_and_versions(tmp_path):
    from self_improving.harness.x2env.skill_execution import CompileCall, build_capabilities

    store, registry, version, scene, _ = inputs(tmp_path)
    capabilities = build_capabilities(
        store,
        compile_policy=StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5),
        replay_executor=None,
    )
    result = capabilities.invoke(
        "x2env.compile",
        "1.0.0",
        CompileCall(
            scene_ir=scene,
            assets=ResolvedAssetSet(
                scene_ir=scene,
                assets=(
                    ResolvedAsset(
                        entity_id="box",
                        version_sha256=version.version_sha256,
                        acquisition_source="local",
                        selection="exact",
                    ),
                ),
            ),
            output_root=str(tmp_path / "compiled"),
            seed=11,
        ),
    )
    assert result.runtime_scene.seed == 11
    assert result.scene_ir == scene and result.physical_evaluated is False
    assert (tmp_path / "compiled" / "scene.json").is_file()


def test_replay_capability_preserves_runtime_failure(tmp_path):
    from self_improving.harness.x2env.genesis_runtime import RuntimeEntity, RuntimeScene
    from self_improving.harness.x2env.replay import GenesisReplayExecutor
    from self_improving.harness.x2env.skill_execution import ReplayCall, build_capabilities
    from self_improving.harness.x2env.store import Store

    store = Store(tmp_path / "state")
    capabilities = build_capabilities(
        store, compile_policy=None, replay_executor=GenesisReplayExecutor(store, runtime_roots={})
    )
    scene = RuntimeScene(
        seed=1,
        scene_ir_sha256="a" * 64,
        entities=(
            RuntimeEntity(
                id="table",
                category="table",
                kind="structural_box",
                position_m=(0.0, 0.0, 0.7),
                orientation_wxyz=(1.0, 0.0, 0.0, 0.0),
                size_m=(1.0, 1.0, 0.04),
                friction=0.5,
            ),
        ),
    )
    result = capabilities.invoke(
        "x2env.replay",
        "1.0.0",
        ReplayCall(
            scene=scene, package_root=str(tmp_path), output_root=str(tmp_path / "replay"), timeout=2
        ),
    )
    assert result.status == "failed" and result.physical_evaluated is False
    assert result.receipt and result.profiles[0].files


def test_validate_capability_keeps_release_authority_separate(tmp_path):
    import json

    from self_improving.harness.x2env.skill_execution import ValidateCall, build_capabilities
    from tests.self_improving.harness.x2env.test_completion import completed_fixture

    store, snapshot = completed_fixture(tmp_path)
    capabilities = build_capabilities(store, compile_policy=None, replay_executor=None)
    result = capabilities.invoke(
        "x2env.validate",
        "1.0.0",
        ValidateCall(
            scene_ir=snapshot.scene_ir,
            observation=snapshot.observation,
            diagnosis=snapshot.diagnosis,
        ),
    )
    assert result.status == "succeeded" and not result.repairable
    report = json.loads(store.read_artifact(result.report))
    assert report["physical_status"] == "passed" and report["sim_ready"] is False
    assert {d.name for d in capabilities.describe()} == {
        "x2env.compile",
        "x2env.replay",
        "x2env.validate",
    }
