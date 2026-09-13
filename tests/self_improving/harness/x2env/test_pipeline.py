"""Public controller wiring with an explicit model double, real Store and compiler."""

import json
from datetime import datetime, timezone

import pytest
from PIL import Image

from self_improving.harness.x2env.contracts import (
    BackendProposal,
    SceneIntentProposal,
    X2EnvRequest,
)
from self_improving.harness.x2env.harness import Harness


@pytest.mark.parametrize(
    "reason,expected", [("command_deadline", "timed_out"), ("stop", "interrupted")]
)
def test_cancelled_backend_is_journaled_without_fallback(tmp_path, reason, expected):
    class CancelledBackend:
        def interpret(self, bundle, **kwargs):
            raise KeyboardInterrupt(reason)

    harness = Harness(tmp_path / "state", backend_factory=lambda store: CancelledBackend())
    handle = harness.submit(
        X2EnvRequest(
            text="a table", seed=1, idempotency_key="cancel", output_dir=str(tmp_path / "out")
        )
    )
    with pytest.raises(KeyboardInterrupt):
        harness.resume(handle.workflow_id)
    stopped = harness.status(handle.workflow_id)
    assert stopped.status == "cancelled" and stopped.stop_reason == expected
    assert stopped.operations[-1].status == "cancelled"
    assert stopped.operations[-1].result.error_code == expected
    assert harness.resume(handle.workflow_id) == stopped


def test_resume_budget_reaches_managed_model(tmp_path, monkeypatch):
    import time

    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    class ExpiredBackend:
        def interpret(self, bundle, *, timeout, **kwargs):
            assert timeout == 5
            clock[0] += 6
            raise KeyboardInterrupt("command_deadline")

    harness = Harness(tmp_path / "state", backend_factory=lambda store: ExpiredBackend())
    handle = harness.submit(
        X2EnvRequest(
            text="a table", seed=1, idempotency_key="budget", output_dir=str(tmp_path / "out")
        )
    )
    with pytest.raises(KeyboardInterrupt):
        harness.resume(handle.workflow_id, timeout=5)
    assert harness.status(handle.workflow_id).stop_reason == "timed_out"


@pytest.mark.parametrize("missing_width", [False, True])
@pytest.mark.parametrize("with_replay", [False, True])
@pytest.mark.parametrize("with_diagnosis", [False, True, "resume"])
@pytest.mark.parametrize("contextual", [False, True])
def test_single_workflow_advances_from_model_to_resolver_and_compile(
    tmp_path, missing_width, with_replay, with_diagnosis, contextual, monkeypatch
):
    import time

    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    from self_improving.harness.x2env.assets import AssetRegistry
    from self_improving.harness.x2env.compile import StructuralPolicy
    from self_improving.harness.x2env.replay import GenesisReplayExecutor
    from self_improving.harness.x2env.resolver import LocalAssetResolver

    def runtime_double(scene, **kwargs):
        assert kwargs["timeout_seconds"] == 8
        out = kwargs["output_dir"]
        out.mkdir(parents=True)
        result = {"status": "passed", "simulator_executed": True}
        if with_diagnosis:
            import hashlib

            (out / "frames").mkdir()
            Image.new("RGB", (8, 8)).save(out / "frames/end.png")
            (out / "media.json").write_text(
                json.dumps(
                    {
                        "frames": [
                            {
                                "path": "frames/end.png",
                                "captured_at": datetime.now(timezone.utc).isoformat(),
                                "png_sha256": hashlib.sha256(
                                    (out / "frames/end.png").read_bytes()
                                ).hexdigest(),
                            }
                        ]
                    }
                )
            )
        (out / "result.json").write_text(json.dumps(result))
        return result

    monkeypatch.setattr("self_improving.harness.x2env.replay.run_scene", runtime_double)

    class AdvisoryDouble:
        def interpret(self, bundle, **kwargs):
            assert kwargs["timeout"] == 10
            clock[0] += 2
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

    class DiagnosticDouble(AdvisoryDouble):
        def __init__(self, store):
            self.store = store

        def assess_and_diagnose(self, scene_ir, observation, physics_report, **kwargs):
            assert kwargs["timeout"] == 8
            from self_improving.harness.x2env.diagnosis import DiagnosisProposal, DiagnosisResult

            assert observation.scene_ir == scene_ir
            assert (
                json.loads(self.store.read_artifact(physics_report))["physical_status"] == "not_run"
            )
            receipt = self.store.write_artifact(b"explicit diagnostic double", "text/plain")
            return DiagnosisResult(
                status="completed",
                receipt=receipt,
                proposal=DiagnosisProposal(
                    base_revision=0,
                    visual_intent="passed",
                    reason="test-only table image",
                    evidence_sha256=(physics_report.sha256,),
                    scene_patches=(),
                    asset_patches=(),
                ),
            )

    def context_factory(store, backend, request, bundle):
        assert request.seed == 11 and bundle.text is not None
        assert store.read_artifact(bundle.text).decode() == request.text
        return LocalAssetResolver(store, AssetRegistry(store), backend, preview=None)

    harness = Harness(
        tmp_path / "state",
        backend_factory=lambda store: (
            DiagnosticDouble(store) if with_diagnosis is True else AdvisoryDouble()
        ),
        resolver_factory=None
        if contextual
        else lambda store, backend: LocalAssetResolver(
            store, AssetRegistry(store), backend, preview=None
        ),
        contextual_resolver_factory=context_factory if contextual else None,
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
    assert {d.name for d in harness.describe_capabilities()} == {
        "x2env.compile",
        "x2env.replay",
        "x2env.validate",
    }
    snapshot = harness.resume(handle.workflow_id, timeout=10)
    if missing_width:
        # Necessary design values are now rejected before asset execution even
        # when the model omitted unknown rows; no implicit grounding policy here.
        assert snapshot.status == "blocked" and snapshot.stop_reason == "clarification_required"
        assert [op.capability for op in snapshot.operations] == ["ingest", "codex.interpret"]
        assert snapshot.proposal in snapshot.operations[-1].result.outputs
        assert snapshot.scene_ir is None and snapshot.compiled_scene is None
        assert snapshot.asset_resolution is None
        assert harness.resume(handle.workflow_id) == snapshot
        assert not (tmp_path / "package").exists()
        return
    if with_diagnosis == "resume" and with_replay and not missing_width:
        assert snapshot.status == "blocked" and snapshot.required_resources == (
            "fresh_observation",
        )
        before = snapshot.operations
        harness = Harness(tmp_path / "state", backend_factory=DiagnosticDouble)
        snapshot = harness.resume(handle.workflow_id, timeout=8)
        assert snapshot.observation is not None
        assert snapshot.operations[: len(before)] == before
    assert snapshot.scene_ir in snapshot.operations[1].result.outputs
    from self_improving.harness.x2env.store import Store

    evidence_store = Store(tmp_path / "state")
    for op in snapshot.operations:
        if op.capability.startswith("x2env.") and op.status == "succeeded":
            records = [
                json.loads(evidence_store.read_artifact(ref))
                for ref in op.result.outputs
                if ref.media_type == "application/json"
            ]
            assert any(
                isinstance(row, dict)
                and row.get("name") == op.capability
                and row.get("version") == op.version
                and row.get("schema_sha256")
                for row in records
            )
    assert snapshot.workflow_id == handle.workflow_id
    expected = [
        "ingest",
        "codex.interpret",
        "asset.resolve",
        "x2env.compile",
    ]
    if with_replay and not missing_width:
        expected.append("x2env.replay")
        if with_diagnosis:
            expected.extend(["observe", "codex.diagnose", "x2env.validate"])
    assert [op.capability for op in snapshot.operations] == expected
    if with_replay and with_diagnosis:
        assert (
            snapshot.status == "failed" and snapshot.stop_reason == "physical_validation_not_passed"
        )
        assert snapshot.observation is not None and snapshot.diagnosis is not None
        assert snapshot.operations[-1].result.outputs
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
