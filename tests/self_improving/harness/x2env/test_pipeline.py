"""Public controller wiring with an explicit model double, real Store and compiler."""

import json

import pytest

from self_improving.harness.x2env.contracts import (
    BackendProposal,
    SceneIntentProposal,
    X2EnvRequest,
)
from self_improving.harness.x2env.harness import Harness


@pytest.mark.parametrize("missing_width", [False, True])
@pytest.mark.parametrize("with_replay", [False, True])
def test_single_workflow_advances_from_model_to_resolver_and_compile(
    tmp_path, missing_width, with_replay, monkeypatch
):
    from self_improving.harness.x2env.assets import AssetRegistry
    from self_improving.harness.x2env.compile import StructuralPolicy
    from self_improving.harness.x2env.replay import GenesisReplayExecutor
    from self_improving.harness.x2env.resolver import LocalAssetResolver

    def runtime_double(scene, **kwargs):
        out = kwargs["output_dir"]
        out.mkdir(parents=True)
        result = {"status": "passed", "simulator_executed": True}
        (out / "result.json").write_text(json.dumps(result))
        return result

    monkeypatch.setattr("self_improving.harness.x2env.replay.run_scene", runtime_double)

    class AdvisoryDouble:
        def interpret(self, bundle, **kwargs):
            fields = ("category", "color", "dimensions", "material", "pose", "articulation_state")
            evidence = [{"source": "text", "input_sha256": bundle.text.sha256, "kind": "explicit"}]
            proposal = SceneIntentProposal.model_validate_json(
                json.dumps(
                    {
                        "scene": {
                            "revision": 0,
                            "input_sha256": bundle.request_sha256,
                            "entities": [
                                {
                                    "id": "table",
                                    "category": "table",
                                    "role": "structural_support",
                                    "color": None,
                                    "dimensions": [None if missing_width else 0.9, 0.7, None],
                                    "material": None,
                                    "pose": {
                                        "frame": "world",
                                        "position": [None] * 3,
                                        "yaw_degrees": 0,
                                    },
                                    "articulation_state": None,
                                    "provenance": {key: evidence for key in fields},
                                }
                            ],
                            "relations": [],
                        },
                        "unknowns": [],
                    }
                )
            )
            return BackendProposal(
                status="completed",
                proposal=proposal,
                evidence=(),
                elapsed_seconds=0.0,
                error_code=None,
            )

    harness = Harness(
        tmp_path / "state",
        backend_factory=lambda store: AdvisoryDouble(),
        resolver_factory=lambda store, backend: LocalAssetResolver(
            store, AssetRegistry(store), backend, preview=None
        ),
        compile_policy=StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5),
        replay_factory=(lambda store: GenesisReplayExecutor(store, runtime_roots={}))
        if with_replay
        else None,
    )
    handle = harness.submit(
        X2EnvRequest(
            text="a 90 by 70 centimetre table",
            seed=11,
            idempotency_key="pipeline",
            output_dir=str(tmp_path / "package"),
        )
    )
    snapshot = harness.resume(handle.workflow_id)
    assert snapshot.workflow_id == handle.workflow_id
    expected = [
        "ingest",
        "codex.interpret",
        "asset.resolve",
        "x2env.compile",
    ]
    if with_replay and not missing_width:
        expected.append("x2env.replay")
    assert [op.capability for op in snapshot.operations] == expected
    if missing_width:
        assert snapshot.status == "failed" and snapshot.stop_reason == "scene_compile_failed"
        assert snapshot.operations[-1].result.outputs
        assert snapshot.compiled_scene is None
        assert harness.resume(handle.workflow_id) == snapshot
        assert not (tmp_path / "package").exists()
        return
    assert all(op.status == "succeeded" for op in snapshot.operations)
    assert snapshot.resolved_assets is not None and snapshot.compiled_scene is not None
    assert snapshot.status == "blocked"
    assert snapshot.required_resources == (
        ("fresh_observation",) if with_replay else ("genesis_replay_executor",)
    )
    assert (snapshot.replay_result is not None) == with_replay
    assert harness.resume(handle.workflow_id) == snapshot
    assert not (tmp_path / "package").exists()
