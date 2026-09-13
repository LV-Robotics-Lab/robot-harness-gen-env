"""One public Harness workflow; actual providers/store/revision, explicit model/render doubles."""

import hashlib
import json
import sys
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from self_improving.harness.x2env.codex import CodexBackend
from self_improving.harness.x2env.contracts import (
    BackendProposal,
    SceneIntentProposal,
    SceneIR,
    X2EnvRequest,
)
from self_improving.harness.x2env.harness import Harness
from self_improving.harness.x2env.resolver import LocalAssetResolver
from self_improving.harness.x2env.source_router import SourceRouter
from tests.self_improving.harness.x2env.test_local_color_advisory import color_inputs
from tests.self_improving.harness.x2env.test_resolver import preview_proof


def configured(tmp_path, monkeypatch, *, child_mode="pink", contextual=False):
    repo = Path(__file__).resolve().parents[4]
    monkeypatch.syspath_prepend(
        str(repo / "self_improving/asset_pipeline/active/shared/openxsim/source/agenticsim")
    )
    store, registry, scene, entity, parent, oldproof, _ = color_inputs(tmp_path)
    template = SceneIR.model_validate_json(store.read_artifact(scene))
    program = tmp_path / "managed-model-boundary-double"
    program.write_text(
        f"#!{sys.executable}\n"
        + """import sys,json,pathlib
from PIL import Image
sys.stdin.read()
schema=json.loads(pathlib.Path(sys.argv[sys.argv.index('--output-schema')+1]).read_text())
if 'declared_color' in schema['properties']:
 answer={'declared_color':'pink','rgba':[0.9,0.4,0.6,1.0]}
else:
 image=Image.open(sys.argv[sys.argv.index('-i')+1]).convert('RGB')
 color='blue' if image.getpixel((0,0))[2]>200 else 'pink'
 answer={'object':'box','match':True,'colors':[color],'materials':['plastic'],'confidence':0.99,
 'same_kind':True,'plausible':True,'suggests':None}
pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(json.dumps(answer))
print(json.dumps({'type':'turn.completed'}))
"""
    )
    program.chmod(0o700)

    class BackendDouble(CodexBackend):
        def interpret(self, bundle, **kwargs):
            # Explicit external interpretation double, never a qualification proposal.
            scene = template.model_copy(update={"input_sha256": bundle.request_sha256})
            return BackendProposal(
                status="completed",
                proposal=SceneIntentProposal(scene=scene, unknowns=()),
                evidence=(bundle.text,),
                error_code=None,
                elapsed_seconds=0.0,
            )

    def renderer(store, output_root, seed):
        def render(version, *, timeout):
            if version.version_sha256 != parent.version_sha256 and child_mode == "crash":
                import os

                os._exit(42)  # Deliberate external renderer process-loss boundary double.
            if version.version_sha256 != parent.version_sha256 and child_mode == "cancel":
                raise KeyboardInterrupt("command_deadline")
            raw = BytesIO()
            Image.new(
                "RGB",
                (3, 3),
                "blue"
                if version.version_sha256 == parent.version_sha256 or child_mode == "blue"
                else (230, 102, 153),
            ).save(raw, format="PNG")
            image = store.write_artifact(raw.getvalue(), "image/png")
            return preview_proof(store, version, image)

        return render

    def resolver(store, backend):
        return SourceRouter(
            store, local=LocalAssetResolver(store, registry, backend, renderer(store, tmp_path, 0))
        )

    harness = Harness(
        store.database.parent,
        backend_factory=lambda store: BackendDouble(
            program, hashlib.sha256(program.read_bytes()).hexdigest(), "double", store
        ),
        resolver_factory=None if contextual else resolver,
        contextual_resolver_factory=(
            lambda store, backend, request, bundle: resolver(store, backend)
        )
        if contextual
        else None,
        asset_preview_factory=renderer,
    )
    return harness, store, parent


@pytest.mark.parametrize("contextual", [False, True])
def test_one_workflow_repairs_color_and_continues_without_researching_local(
    tmp_path, monkeypatch, contextual
):
    harness, store, parent = configured(tmp_path, monkeypatch, contextual=contextual)
    handle = harness.submit(
        X2EnvRequest(
            text="one pink plastic box",
            seed=19,
            allowed_sources=("local",),
            idempotency_key="color-line",
            output_dir=str(tmp_path / "output"),
        )
    )
    snapshot = harness.resume(handle.workflow_id)
    stages = [op.capability for op in snapshot.operations]
    assert "codex.asset_color" in stages and "asset.revise" in stages, snapshot
    assert snapshot.resolved_assets is not None
    assert len([op for op in snapshot.operations if op.capability == "asset.resolve"]) == 2
    versions = json.loads(store.read_artifact(snapshot.resolved_assets))["assets"]
    assert versions[0]["version_sha256"] != parent.version_sha256


@pytest.mark.parametrize("child_mode", ["blue", "cancel"])
def test_failed_or_cancelled_child_keeps_budget_and_original_pending_result(
    tmp_path, monkeypatch, child_mode
):
    harness, store, parent = configured(tmp_path, monkeypatch, child_mode=child_mode)
    handle = harness.submit(
        X2EnvRequest(
            text="one pink plastic box",
            seed=19,
            allowed_sources=("local",),
            idempotency_key="color-failure",
            output_dir=str(tmp_path / "output"),
        )
    )
    if child_mode == "cancel":
        with pytest.raises(KeyboardInterrupt, match="command_deadline"):
            harness.resume(handle.workflow_id)
        snapshot = harness.status(handle.workflow_id)
        assert snapshot.status == "cancelled"
    else:
        snapshot = harness.resume(handle.workflow_id)
        assert snapshot.status == "failed"
    repairs = [op for op in snapshot.operations if op.capability == "asset.revise"]
    assert len(repairs) == 1 and repairs[0].repair_reservation.cost == 1
    assert snapshot.resolved_assets is None
    original = json.loads(store.read_artifact(snapshot.asset_resolution))
    assert original["error_code"] == "local_color_repair_pending"
    from self_improving.harness.x2env.assets import AssetRegistry

    versions = AssetRegistry(store).find("box")
    assert len(versions) == 2
    assert any(version.version_sha256 == parent.version_sha256 for version in versions)
    assert harness.resume(handle.workflow_id) == snapshot


def test_dead_owner_after_child_creation_is_not_reexecuted(tmp_path, monkeypatch):
    import multiprocessing

    harness, store, parent = configured(tmp_path, monkeypatch, child_mode="crash")
    handle = harness.submit(
        X2EnvRequest(
            text="one pink plastic box",
            seed=19,
            allowed_sources=("local",),
            idempotency_key="color-crash",
            output_dir=str(tmp_path / "output"),
        )
    )
    worker = multiprocessing.get_context("fork").Process(
        target=harness.resume, args=(handle.workflow_id,)
    )
    worker.start()
    worker.join(10)
    if worker.is_alive():
        worker.terminate()
        worker.join(5)
        pytest.fail("external renderer boundary did not exit within test budget")
    assert worker.exitcode == 42
    before = harness.status(handle.workflow_id)
    assert before.operations[-1].capability == "asset.revise"
    assert before.operations[-1].status == "running"
    snapshot = harness.resume(handle.workflow_id)
    assert snapshot.status == "blocked"
    assert snapshot.stop_reason == "color_repair_recovery_requires_audit"
    assert snapshot.asset_resolution == before.asset_resolution
    assert snapshot.resolved_assets is None
    assert len([op for op in snapshot.operations if op.capability == "asset.revise"]) == 1
    assert (
        sum(op.repair_reservation.cost for op in snapshot.operations if op.repair_reservation) == 1
    )
    assert harness.resume(handle.workflow_id) == snapshot


def test_resume_reuses_committed_color_execution_without_model_or_mutation(tmp_path):
    from self_improving.harness.x2env.contracts import ToolResult
    from tests.self_improving.harness.x2env.test_source_router import continuation_inputs

    store, registry, snapshot, original_ref, execution, execution_ref = continuation_inputs(
        tmp_path
    )
    snapshot = store.complete_operation(
        snapshot,
        ToolResult(
            operation_id=snapshot.operations[-1].operation_id,
            status="succeeded",
            outputs=(execution_ref, execution.receipt),
        ),
        snapshot.input_bundle,
        status="blocked",
        reason="operator_test_pause_after_committed_child",
    )
    before = registry.find("box")
    program = tmp_path / "color-model-double"

    def unexpected_preview(*args, **kwargs):
        pytest.fail("a committed child must not be rendered/mutated again")

    harness = Harness(
        store.database.parent,
        backend_factory=lambda store: CodexBackend(
            program,
            hashlib.sha256(program.read_bytes()).hexdigest(),
            "test-double",
            store,
        ),
        resolver_factory=lambda store, backend: SourceRouter(store),
        asset_preview_factory=unexpected_preview,
    )
    after = harness.resume(snapshot.workflow_id)
    assert len(after.operations) == len(snapshot.operations) + 1
    assert after.operations[-1].capability == "asset.resolve"
    assert len([op for op in after.operations if op.capability == "codex.asset_color"]) == 1
    assert len([op for op in after.operations if op.capability == "asset.revise"]) == 1
    assert registry.find("box") == before
    assert after.asset_resolution == original_ref  # Ordinary missing mouse has no web adapter.
    assert after.status == "blocked"
