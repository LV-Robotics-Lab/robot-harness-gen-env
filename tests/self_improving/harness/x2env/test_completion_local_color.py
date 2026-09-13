"""Real local color pipeline; explicit external model/render/runtime boundary fixtures."""

import hashlib
import json
import sys
from io import BytesIO

import pytest
from PIL import Image

from self_improving.harness.x2env.codex import CodexBackend
from self_improving.harness.x2env.contracts import BackendProposal, SceneIntentProposal
from self_improving.harness.x2env.harness import Harness
from self_improving.harness.x2env.resolver import LocalAssetResolver
from self_improving.harness.x2env.source_router import SourceRouter
from tests.self_improving.harness.x2env.test_resolver import preview_proof


def execute_fixture_color(store, registry, request, scene, parent, root, *, grounding=False):
    program = root / "color-process-boundary-double"
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

    class Backend(CodexBackend):
        def interpret(self, bundle, **kwargs):
            from self_improving.harness.x2env.contracts import UnknownField

            return BackendProposal(
                status="completed",
                proposal=SceneIntentProposal(
                    scene=scene,
                    unknowns=(
                        UnknownField(
                            field="scene.entities[*].dimensions",
                            reason="fixture design scale unknown",
                            critical=True,
                            reason_kind="scale_unobservable",
                            provenance=scene.entities[0].provenance.dimensions,
                        ),
                    )
                    if grounding
                    else (),
                ),
                evidence=process_fixture_evidence(store, {"fixture_original": True})
                if grounding
                else (bundle.text,),
                error_code=None,
                elapsed_seconds=0.0,
            )

        def ground_scene(self, *args, **kwargs):
            from self_improving.harness.x2env.grounding import GroundingResult

            return GroundingResult(
                status="blocked",
                error_code="fixture_grounding_deferred",
                receipt=store.write_artifact(b"explicit external grounding pause", "text/plain"),
            )

    def preview_factory(store, root, seed):
        def render(version, *, timeout):
            raw = BytesIO()
            Image.new("RGB", (3, 3), "blue" if version == parent else (230, 102, 153)).save(
                raw, format="PNG"
            )
            return preview_proof(store, version, store.write_artifact(raw.getvalue(), "image/png"))

        return render

    from self_improving.harness.x2env.grounding import SceneDesignPolicy

    harness = Harness(
        store.database.parent,
        backend_factory=lambda store: Backend(
            program,
            hashlib.sha256(program.read_bytes()).hexdigest(),
            "boundary-double",
            store,
        ),
        resolver_factory=lambda store, backend: SourceRouter(
            store,
            local=LocalAssetResolver(store, registry, backend, preview_factory(store, root, 0)),
        ),
        asset_preview_factory=preview_factory,
        scene_design_policy=SceneDesignPolicy(enabled=grounding),
    )
    snapshot = harness.resume(harness.submit(request).workflow_id)
    assert snapshot.resolved_assets, snapshot
    child_sha = json.loads(store.read_artifact(snapshot.resolved_assets))["assets"][0][
        "version_sha256"
    ]
    assert child_sha != parent.version_sha256
    return snapshot, registry.inspect(child_sha)


def process_fixture_evidence(store, value):
    """Short real OS process producing synthetic model payload; never qualification."""
    import subprocess
    from pathlib import Path

    def put(value):
        return store.write_artifact(json.dumps(value).encode(), "application/json")

    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys,time; print(sys.argv[1],flush=True); time.sleep(.05)",
            json.dumps(value),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    stat = Path(f"/proc/{process.pid}/stat").read_text()
    record = put(
        {
            "pid": process.pid,
            "pgid": process.pid,
            "start_ticks": int(stat.rsplit(")", 1)[1].split()[19]),
            "executable_sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
            "fixture_only": True,
        }
    )
    out, err = process.communicate(timeout=10)
    assert process.returncode == 0 and not err
    terminal = put(
        {"pid": process.pid, "returncode": 0, "reaped": True, "failure": None, "fixture_only": True}
    )
    return record, terminal, put(json.loads(out))


@pytest.mark.parametrize("grounding", [False, True])
def test_completion_consumes_committed_local_color_child(tmp_path, monkeypatch, grounding):
    from pathlib import Path

    from self_improving.harness.x2env.completion import materialize_completion
    from tests.self_improving.harness.x2env.test_completion import completed_fixture

    repo = Path(__file__).resolve().parents[4]
    monkeypatch.syspath_prepend(
        str(repo / "self_improving/asset_pipeline/active/shared/openxsim/source/agenticsim")
    )
    store, snapshot = completed_fixture(tmp_path, local_color=True, grounding=grounding)
    result = materialize_completion(snapshot, store, tmp_path / "completion")
    assert result.status == "materialized", result


def test_completion_rejects_old_parent_compiled_after_successful_color(tmp_path, monkeypatch):
    from pathlib import Path

    from self_improving.harness.x2env.completion import materialize_completion
    from tests.self_improving.harness.x2env.test_completion import completed_fixture

    repo = Path(__file__).resolve().parents[4]
    monkeypatch.syspath_prepend(
        str(repo / "self_improving/asset_pipeline/active/shared/openxsim/source/agenticsim")
    )
    store, snapshot = completed_fixture(tmp_path, local_color="parent")
    result = materialize_completion(snapshot, store, tmp_path / "completion")
    assert result.status == "failed"
    assert result.error_code == "completion_color_compiled_child_mismatch"


@pytest.mark.parametrize("fault", ["approval", "model_evidence", "continuation"])
def test_completion_rejects_damaged_color_chain(tmp_path, monkeypatch, fault):
    from pathlib import Path

    from self_improving.harness.x2env.completion import materialize_completion
    from self_improving.harness.x2env.contracts import ArtifactRef
    from tests.self_improving.harness.x2env.test_completion import completed_fixture

    repo = Path(__file__).resolve().parents[4]
    monkeypatch.syspath_prepend(
        str(repo / "self_improving/asset_pipeline/active/shared/openxsim/source/agenticsim")
    )
    store, snapshot = completed_fixture(tmp_path, local_color=True)
    operation = next(op for op in snapshot.operations if op.capability == "asset.revise")
    ref = operation.repair_reservation.approval
    if fault == "model_evidence":
        approval = json.loads(store.read_artifact(ref))
        proposal = json.loads(store.read_artifact(ArtifactRef.model_validate(approval["proposal"])))
        ref = next(
            ArtifactRef.model_validate(r)
            for r in proposal["evidence"]
            if r["media_type"] == "application/json"
        )
    elif fault == "continuation":
        ref = snapshot.asset_resolution
    # Explicit storage-corruption attack; no journal or evidence is rewritten to look valid.
    (store.cas / ref.sha256[:2] / ref.sha256).write_bytes(b"corrupted")
    result = materialize_completion(snapshot, store, tmp_path / "completion")
    assert result.status == "failed" and result.package_path is None


def test_completion_rejects_uncommitted_resolution_pointer(tmp_path, monkeypatch):
    from pathlib import Path

    from self_improving.harness.x2env.completion import materialize_completion
    from tests.self_improving.harness.x2env.test_completion import completed_fixture

    repo = Path(__file__).resolve().parents[4]
    monkeypatch.syspath_prepend(
        str(repo / "self_improving/asset_pipeline/active/shared/openxsim/source/agenticsim")
    )
    store, snapshot = completed_fixture(tmp_path, local_color=True)
    replacement = store.write_artifact(
        json.dumps(json.loads(store.read_artifact(snapshot.asset_resolution)), indent=2).encode(),
        "application/json",
    )
    snapshot = store.complete_operation(
        snapshot,
        None,
        snapshot.input_bundle,
        status="active",
        asset_resolution=replacement,
    )
    result = materialize_completion(snapshot, store, tmp_path / "completion")
    assert result.status == "failed"
    assert result.error_code == "completion_color_continuation_not_current"


@pytest.mark.parametrize("terminal", ["failed", "cancelled", "foreign"])
def test_completion_keeps_failed_asset_reservation_and_unused_proposal(
    tmp_path, monkeypatch, terminal
):
    from pathlib import Path

    from self_improving.harness.x2env.asset_advisory import VisualCandidate
    from self_improving.harness.x2env.completion import materialize_completion
    from self_improving.harness.x2env.contracts import (
        RepairReservation,
        SceneIR,
        ToolResult,
    )
    from self_improving.harness.x2env.local_color_advisory import (
        classify_color_repair,
        propose_color_repair,
    )
    from self_improving.harness.x2env.local_color_execution import (
        color_failure_fingerprint,
        verify_color_repair_approval,
    )
    from tests.self_improving.harness.x2env.test_completion import completed_fixture

    repo = Path(__file__).resolve().parents[4]
    monkeypatch.syspath_prepend(
        str(repo / "self_improving/asset_pipeline/active/shared/openxsim/source/agenticsim")
    )
    store, snapshot = completed_fixture(tmp_path, local_color=True)
    first = next(op for op in snapshot.operations if op.capability == "asset.revise")
    prior, _, _ = verify_color_repair_approval(
        store, workflow_id=snapshot.workflow_id, operation_id=first.operation_id
    )
    program = tmp_path / "color-process-boundary-double"
    backend = CodexBackend(
        program, hashlib.sha256(program.read_bytes()).hexdigest(), "boundary-double", store
    )
    raw = BytesIO()
    Image.new("RGB", (4, 4), "blue").save(raw, format="PNG")
    image = store.write_artifact(raw.getvalue(), "image/png")
    proof = preview_proof(store, prior.version, image)
    visual = backend.assess_asset_candidates(
        (
            VisualCandidate(
                candidate_id=prior.parent_version,
                name=prior.version.asset_id,
                category=prior.version.category,
                want_color=prior.requested_color,
                want_material=prior.requested_material,
                preview=image,
            ),
        ),
        output_root=tmp_path / "fresh-failed-candidate",
    )
    scene = SceneIR.model_validate_json(store.read_artifact(prior.scene_ir))
    candidate = classify_color_repair(
        store, prior.scene_ir, scene.entities[0], prior.version, proof, visual
    )
    assert candidate is not None

    def put(value):
        return store.write_artifact(value.model_dump_json().encode(), "application/json")

    snapshot = store.begin_operation(snapshot, "codex.asset_color")
    proposal = propose_color_repair(backend, candidate, output_root=tmp_path / "unused-proposal")
    assert proposal.status == "completed"
    candidate_ref, proposal_ref = put(candidate), put(proposal)
    snapshot = store.complete_operation(
        snapshot,
        ToolResult(
            operation_id=snapshot.operations[-1].operation_id,
            status="succeeded",
            outputs=(candidate_ref, proposal_ref, proposal.receipt),
        ),
        snapshot.input_bundle,
        status="active",
    )
    approval = store.write_artifact(
        json.dumps(
            {
                "authority": "harness_controller",
                "approved": True,
                "parent_version": candidate.parent_version,
                "patch": {
                    "base_color": list(proposal.proposal.rgba),
                    "color_mode": "uniform_replace",
                },
                "workflow_id": "foreign-workflow"
                if terminal == "foreign"
                else snapshot.workflow_id,
                "base_revision": snapshot.revision,
                "input_bundle": snapshot.input_bundle.model_dump(),
                "scene_ir": candidate.scene_ir.model_dump(),
                "candidate": candidate_ref.model_dump(),
                "proposal": proposal_ref.model_dump(),
                "cost": 1,
                "failure_fingerprint": color_failure_fingerprint(candidate),
            }
        ).encode(),
        "application/json",
    )
    reservation = RepairReservation(
        kind="asset",
        cost=1,
        failure_fingerprint=color_failure_fingerprint(candidate),
        approval=approval,
        base_revision=snapshot.revision,
    )
    snapshot = store.begin_operation(snapshot, "asset.revise", repair_reservation=reservation)
    snapshot = store.complete_operation(
        snapshot,
        ToolResult(
            operation_id=snapshot.operations[-1].operation_id,
            status="failed" if terminal == "foreign" else terminal,
            outputs=(approval, candidate_ref, proposal_ref),
            error_code="explicit_prewrite_boundary_failure",
        ),
        snapshot.input_bundle,
        status="active",
    )
    result = materialize_completion(snapshot, store, tmp_path / "completion")
    if terminal == "foreign":
        assert result.status == "failed"
        assert result.error_code == "color_history_approval_mismatch"
        return
    assert result.status == "materialized", result
    documents = [
        json.loads(path.read_bytes())
        for path in (Path(result.package_path) / "evidence").glob("*.json")
    ]
    audits = [
        doc
        for doc in documents
        if isinstance(doc, dict) and doc.get("schema_version") == "x2env.repair_budget_audit.v1"
    ]
    assert len(audits) == 1 and audits[0]["reserved_cost"] == 2
    assert audits[0]["operations"][-1]["status"] == terminal
