"""Real Store, normalization, revisions and verifier; model/render ports are explicit doubles."""

import hashlib
import json
import sys

import pytest

from self_improving.harness.x2env.codex import CodexBackend
from self_improving.harness.x2env.contracts import RepairReservation, ToolResult, X2EnvRequest
from self_improving.harness.x2env.local_color_advisory import (
    classify_color_repair,
    propose_color_repair,
)
from tests.self_improving.harness.x2env.test_local_color_advisory import color_inputs
from tests.self_improving.harness.x2env.test_resolver import preview_proof


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "uncommitted",
        "receipt",
        "preview",
        "verdict",
        "license",
        "workflow",
        "candidate",
        "proposal",
        "reservation",
    ],
)
def test_public_history_audit_requires_bound_committed_execution(tmp_path, fault):
    from self_improving.harness.x2env.contracts import ArtifactRef
    from self_improving.harness.x2env.local_color_execution import verify_color_repair_result
    from tests.self_improving.harness.x2env.test_source_router import (
        continuation_inputs,
        start_continuation,
    )

    store, _, snapshot, _, execution, execution_ref = continuation_inputs(tmp_path)
    original_ref = execution_ref
    payload = json.loads(store.read_artifact(execution.receipt))
    if fault == "receipt":
        payload["resolved"]["entity_id"] = "mouse"
    if fault == "preview":
        envelope = json.loads(store.read_artifact(ArtifactRef.model_validate(payload["candidate"])))
        execution = execution.model_copy(
            update={"preview": execution.preview.model_validate(envelope["preview_proof"])}
        )
        payload["preview"] = execution.preview.model_dump(mode="json")
    if fault == "verdict":
        execution = execution.model_copy(
            update={
                "assessment": execution.assessment.model_copy(
                    update={
                        "verdicts": (
                            execution.assessment.verdicts[0].model_copy(
                                update={"verdict": "mismatch"}
                            ),
                        )
                    }
                )
            }
        )
        payload["assessment"] = execution.assessment.model_dump(mode="json")
    if fault == "license":
        execution = execution.model_copy(
            update={
                "child": execution.child.model_copy(
                    update={
                        "license": execution.child.license.model_copy(
                            update={"source_url": "wrong"}
                        )
                    }
                )
            }
        )
        payload["child"] = execution.child.model_dump(mode="json")
    if fault in {"workflow", "candidate", "proposal", "reservation"}:
        env = json.loads(store.read_artifact(ArtifactRef.model_validate(payload["reservation"])))
        if fault == "workflow":
            env["workflow_id"] = "other-workflow"
        elif fault == "reservation":
            env["reservation"]["failure_fingerprint"] = "a" * 64
        else:
            env[fault] = execution.receipt.model_dump()
        payload["reservation"] = store.write_artifact(
            json.dumps(env).encode(), "application/json"
        ).model_dump()
    if fault not in {None, "uncommitted"}:
        receipt = store.write_artifact(json.dumps(payload).encode(), "application/json")
        execution = execution.model_copy(update={"receipt": receipt})
        execution_ref = store.write_artifact(
            execution.model_dump_json().encode(), "application/json"
        )
    snapshot = start_continuation(store, snapshot, execution, execution_ref)
    if fault == "uncommitted":
        execution_ref = store.write_artifact(
            execution.model_dump_json(indent=2).encode(), "application/json"
        )
        assert execution_ref != original_ref
    if fault:
        with pytest.raises((ValueError, OSError, KeyError)):
            verify_color_repair_result(store, execution_ref, workflow_id=snapshot.workflow_id)
    else:
        candidate, checked = verify_color_repair_result(
            store, execution_ref, workflow_id=snapshot.workflow_id
        )
        assert checked == execution and candidate.parent_version == checked.child.parent_version


@pytest.mark.parametrize("status", ["failed", "cancelled"])
@pytest.mark.parametrize(
    "fault",
    [
        None,
        "model",
        "model_raw",
        "model_sha",
        "model_receipt",
        "model_evidence",
        "proposal",
        "candidate",
        "approval",
        "uncommitted",
    ],
)
def test_failed_repair_approval_audit_requires_original_model_transport(tmp_path, status, fault):
    from self_improving.harness.x2env.local_color_execution import verify_color_repair_approval

    store, _, _, _, _, candidate_ref, proposal_ref, envelope_ref = prepared(tmp_path, fault)
    envelope = json.loads(store.read_artifact(envelope_ref))
    snapshot = store.status(envelope["workflow_id"])
    operation = snapshot.operations[-1]
    store.complete_operation(
        snapshot,
        ToolResult(
            operation_id=operation.operation_id, status=status, error_code="explicit_failed_attempt"
        ),
        snapshot.input_bundle,
        status=status,
    )
    if fault:
        with pytest.raises((ValueError, OSError, KeyError)):
            verify_color_repair_approval(
                store, workflow_id=snapshot.workflow_id, operation_id=operation.operation_id
            )
    else:
        candidate, proposal, reservation = verify_color_repair_approval(
            store, workflow_id=snapshot.workflow_id, operation_id=operation.operation_id
        )
        assert candidate.model_dump(mode="json") == json.loads(store.read_artifact(candidate_ref))
        assert proposal.model_dump(mode="json") == json.loads(store.read_artifact(proposal_ref))
        assert proposal.candidate == candidate
        assert reservation == operation.repair_reservation


@pytest.mark.parametrize("fault", ["dimensions", "metadata", "approval", "checks"])
def test_history_rejects_registered_child_with_uninherited_normalization(tmp_path, fault):
    from self_improving.harness.x2env.asset_advisory import AssetVisualAssessment, VisualVerdict
    from self_improving.harness.x2env.local_color_execution import verify_color_repair_result
    from tests.self_improving.harness.x2env.test_source_router import (
        continuation_inputs,
        start_continuation,
    )

    store, registry, snapshot, _, execution, _ = continuation_inputs(tmp_path)

    def put(value):
        return store.write_artifact(json.dumps(value).encode(), "application/json")

    report = json.loads(store.read_artifact(execution.child.normalization_report))
    if fault == "dimensions":
        report["dimensions_m"] = [3.0, 3.0, 3.0]
    elif fault == "metadata":
        report["normalization_was_measured"] = "unearned claim"
    elif fault == "approval":
        report["asset_revision"]["approval"] = execution.receipt.model_dump()
    else:
        report["checks"]["genesis_load_step"] = "passed"
    report_ref = put(report)
    receipt = json.loads(store.read_artifact(execution.child.receipt))
    receipt["normalization_report"] = report_ref.model_dump()
    child = registry.register(
        execution.child.asset_id,
        execution.child.category,
        tmp_path / "execution/child",
        "asset.urdf",
        files=tuple(f["path"] for f in report["files"]),
        normalization_report=report_ref,
        license=execution.child.license,
        source=execution.child.source,
        receipt=put(receipt),
        parent_version=execution.child.parent_version,
    )
    proof = preview_proof(store, child, execution.preview.image)
    detail = json.loads(store.read_artifact(execution.assessment.verdicts[0].detail))
    detail["candidate_id"] = child.version_sha256
    detail_ref = put(detail)
    verdict = VisualVerdict(
        candidate_id=child.version_sha256, preview=proof.image, verdict="match", detail=detail_ref
    )
    assessment_receipt = json.loads(store.read_artifact(execution.assessment.receipt))
    assessment_receipt["verdicts"] = [verdict.model_dump(mode="json")]
    assessment_receipt["evidence"] = [detail_ref.model_dump()]
    assessment = AssetVisualAssessment(
        status="completed",
        verdicts=(verdict,),
        receipt=put(assessment_receipt),
        evidence=(detail_ref,),
    )
    payload = json.loads(store.read_artifact(execution.receipt))
    result = execution.model_copy(
        update={
            "child": child,
            "preview": proof,
            "assessment": assessment,
            "resolved": execution.resolved.model_copy(
                update={"version_sha256": child.version_sha256}
            ),
        }
    )
    for name in ("child", "preview", "assessment", "resolved"):
        payload[name] = getattr(result, name).model_dump(mode="json")
    receipt_ref = put(payload)
    result = result.model_copy(
        update={
            "receipt": receipt_ref,
            "evidence": (*[r for r in result.evidence if r != execution.receipt], receipt_ref),
        }
    )
    result_ref = put(result.model_dump(mode="json"))
    start_continuation(store, snapshot, result, result_ref)
    with pytest.raises(ValueError, match="color_history_normalization"):
        verify_color_repair_result(store, result_ref, workflow_id=snapshot.workflow_id)


def prepared(tmp_path, fault=None, pending=False):
    from self_improving.harness.x2env.local_color_execution import color_failure_fingerprint

    store, registry, scene, entity, parent, proof, advisory = color_inputs(tmp_path)
    from self_improving.harness.x2env.input import ingest

    request = X2EnvRequest(
        text="pink box", seed=0, idempotency_key="color", output_dir=str(tmp_path / "output")
    )
    bundle_value = ingest(request, store)
    if fault == "input":
        bundle_value = bundle_value.model_copy(update={"seed": 1})
    body = json.loads(store.read_artifact(scene))
    body["input_sha256"] = bundle_value.request_sha256
    scene = store.write_artifact(json.dumps(body).encode(), "application/json")
    candidate = classify_color_repair(store, scene, entity, parent, proof, advisory)
    program = tmp_path / "model-double"
    program.write_text(
        f"#!{sys.executable}\n"
        + """import sys,json,pathlib
sys.stdin.read()
schema=json.loads(pathlib.Path(sys.argv[sys.argv.index('--output-schema')+1]).read_text())
answer=({'declared_color':'pink','rgba':[0.9,0.4,0.6,1.0]}
if 'declared_color' in schema['properties']
else {'object':'box','match':True,'colors':['pink'],'materials':['plastic'],'confidence':0.99,
'same_kind':True,'plausible':True,'suggests':None})
pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(json.dumps(answer))
print(json.dumps({'type':'turn.completed'}))
"""
    )
    program.chmod(0o700)
    if fault == "mismatch":
        program.write_text(program.read_text().replace("'colors':['pink']", "'colors':['blue']"))
    backend = CodexBackend(
        program, hashlib.sha256(program.read_bytes()).hexdigest(), "test-double", store
    )
    proposal = propose_color_repair(
        backend, candidate, output_root=tmp_path / "proposal", timeout=10
    )
    assert proposal.status == "completed"
    if fault in {"model", "model_raw", "model_sha", "model_receipt", "model_evidence"}:
        kept = []
        for ref in proposal.evidence:
            if ref == proposal.receipt:
                continue
            body = (
                json.loads(store.read_artifact(ref))
                if ref.media_type == "application/json"
                else None
            )
            if fault == "model" and isinstance(body, dict) and "start_ticks" in body:
                continue
            if fault == "model_raw" and body == proposal.proposal.model_dump(mode="json"):
                continue
            kept.append(ref)
        body = json.loads(store.read_artifact(proposal.receipt))
        body["evidence"] = [ref.model_dump() for ref in kept]
        if fault == "model_sha":
            body["executable_sha256"] = "b" * 64
        if fault == "model_receipt":
            body["proposal"]["rgba"][0] = 0.1
        if fault == "model_evidence":
            body["evidence"] = []
        replaced = store.write_artifact(json.dumps(body).encode(), "application/json")
        proposal = proposal.model_copy(update={"receipt": replaced, "evidence": (*kept, replaced)})
    if fault == "proposal":
        proposal = proposal.model_copy(
            update={"proposal": proposal.proposal.model_copy(update={"rgba": (0.0, 0.0, 1.0, 1.0)})}
        )
    if fault == "candidate":
        proposal = proposal.model_copy(
            update={"candidate": candidate.model_copy(update={"requested_color": "blue"})}
        )

    def put(value):
        return store.write_artifact(json.dumps(value).encode(), "application/json")

    candidate_ref = put(candidate.model_dump(mode="json"))
    proposal_ref = put(proposal.model_dump(mode="json"))
    bundle = put(bundle_value.model_dump(mode="json"))
    snapshot = store.claim(store.submit(request).workflow_id)
    snapshot = store.complete_operation(
        snapshot,
        ToolResult(
            operation_id=snapshot.operations[-1].operation_id,
            status="succeeded",
            outputs=(bundle, scene, candidate_ref),
        ),
        bundle,
        status="active",
        **({"pending_scene_ir": scene} if pending else {"scene_ir": scene}),
    )
    snapshot = store.begin_operation(
        snapshot, "uncommitted-proposal-test" if fault == "uncommitted" else "codex.asset_color"
    )
    snapshot = store.complete_operation(
        snapshot,
        ToolResult(
            operation_id=snapshot.operations[-1].operation_id,
            status="succeeded",
            outputs=(proposal_ref,),
        ),
        bundle,
        status="active",
    )
    fingerprint = color_failure_fingerprint(candidate)
    approval = put(
        {
            "authority": "harness_controller",
            "approved": True,
            "parent_version": parent.version_sha256,
            "patch": {"base_color": [0.9, 0.4, 0.6, 1.0], "color_mode": "uniform_replace"},
            "workflow_id": snapshot.workflow_id,
            "base_revision": snapshot.revision,
            "input_bundle": bundle.model_dump(),
            "scene_ir": scene.model_dump(),
            "candidate": candidate_ref.model_dump(),
            "proposal": proposal_ref.model_dump(),
            "cost": 1,
            "failure_fingerprint": fingerprint,
        }
    )
    if fault == "approval":
        wrong = json.loads(store.read_artifact(approval))
        wrong["patch"]["base_color"] = [0.0, 0.0, 1.0, 1.0]
        approval = put(wrong)
    reservation = RepairReservation(
        kind="asset",
        cost=1,
        failure_fingerprint=fingerprint,
        approval=approval,
        base_revision=snapshot.revision,
    )
    snapshot = store.begin_operation(snapshot, "asset.revise", repair_reservation=reservation)
    envelope = put(
        {
            "workflow_id": snapshot.workflow_id,
            "operation_id": snapshot.operations[-1].operation_id,
            "reservation": reservation.model_dump(mode="json"),
            "input_bundle": bundle.model_dump(),
            "scene_ir": scene.model_dump(),
            "candidate": candidate_ref.model_dump(),
            "proposal": proposal_ref.model_dump(),
        }
    )
    return store, registry, backend, parent, proof, candidate_ref, proposal_ref, envelope


def test_approved_color_child_requires_new_preview_and_original_verifier_match(tmp_path):
    from self_improving.harness.x2env.local_color_execution import execute_color_repair

    store, registry, backend, parent, proof, candidate, proposal, envelope = prepared(tmp_path)

    def preview(version, *, timeout):
        assert version.version_sha256 != parent.version_sha256 and 0 < timeout <= 30
        return preview_proof(store, version, proof.image)

    result = execute_color_repair(
        candidate,
        proposal,
        envelope,
        store=store,
        registry=registry,
        backend=backend,
        preview=preview,
        output_root=tmp_path / "execution",
        timeout=30,
    )
    assert result.status == "succeeded", result
    assert result.resolved.version_sha256 == result.child.version_sha256 != parent.version_sha256
    assert result.child.parent_version == parent.version_sha256
    assert result.assessment.verdicts[0].verdict == "match"
    assert registry.inspect(parent.version_sha256) == parent


def test_pending_intent_can_repair_color_without_accepting_the_scene(tmp_path):
    from self_improving.harness.x2env.local_color_execution import execute_color_repair

    store, registry, backend, parent, proof, candidate, proposal, envelope = prepared(
        tmp_path, pending=True
    )
    result = execute_color_repair(
        candidate,
        proposal,
        envelope,
        store=store,
        registry=registry,
        backend=backend,
        preview=lambda version, timeout: preview_proof(store, version, proof.image),
        output_root=tmp_path / "execution",
        timeout=30,
    )
    assert result.status == "succeeded", result
    workflow = json.loads(store.read_artifact(envelope))["workflow_id"]
    snapshot = store.status(workflow)
    assert snapshot.scene_ir is None and snapshot.pending_scene_ir is not None


@pytest.mark.parametrize(
    "fault",
    ["proposal", "approval", "stale", "model", "backend", "input", "candidate", "uncommitted"],
)
def test_unbound_proposal_or_approval_cannot_create_a_child(tmp_path, fault):
    from self_improving.harness.x2env.local_color_execution import execute_color_repair

    store, registry, backend, parent, proof, candidate, proposal, envelope = prepared(
        tmp_path, fault
    )
    if fault == "stale":
        body = json.loads(store.read_artifact(envelope))
        snapshot = store.status(body["workflow_id"])
        store.complete_operation(
            snapshot,
            ToolResult(
                operation_id=snapshot.operations[-1].operation_id,
                status="cancelled",
                error_code="fixture_cancelled",
            ),
            snapshot.input_bundle,
            status="cancelled",
        )
    if fault == "backend":
        backend.executable_sha = "b" * 64

    def forbidden(version, *, timeout):
        pytest.fail("preflight failure must not reach preview")

    result = execute_color_repair(
        candidate,
        proposal,
        envelope,
        store=store,
        registry=registry,
        backend=backend,
        preview=forbidden,
        output_root=tmp_path / "execution",
        timeout=30,
    )
    assert result.status == "failed" and result.child is None and result.resolved is None
    assert not (tmp_path / "execution/child").exists()
    assert registry.inspect(parent.version_sha256) == parent


@pytest.mark.parametrize("fault", ["old_preview", "missing_preview", "mismatch"])
def test_failed_child_recheck_retains_new_version_without_resolving_parent(tmp_path, fault):
    from self_improving.harness.x2env.local_color_execution import execute_color_repair

    store, registry, backend, parent, proof, candidate, proposal, envelope = prepared(
        tmp_path, fault
    )

    def preview(version, *, timeout):
        if fault == "old_preview":
            return proof
        if fault == "missing_preview":
            raise FileNotFoundError("explicit render-port dependency double")
        return preview_proof(store, version, proof.image)

    result = execute_color_repair(
        candidate,
        proposal,
        envelope,
        store=store,
        registry=registry,
        backend=backend,
        preview=preview,
        output_root=tmp_path / "execution",
        timeout=30,
    )
    assert result.status == ("blocked" if fault == "missing_preview" else "failed"), result
    assert result.child is not None and result.resolved is None
    assert registry.inspect(result.child.version_sha256) == result.child
    assert registry.inspect(parent.version_sha256) == parent
    assert (tmp_path / "execution/result.json").is_file()


def test_cancellation_preserves_child_and_propagates_to_workflow_owner(tmp_path):
    from self_improving.harness.x2env.local_color_execution import execute_color_repair

    store, registry, backend, parent, proof, candidate, proposal, envelope = prepared(tmp_path)

    def cancel(version, *, timeout):
        raise KeyboardInterrupt("explicit render-port cancellation double")

    with pytest.raises(KeyboardInterrupt, match="explicit render-port"):
        execute_color_repair(
            candidate,
            proposal,
            envelope,
            store=store,
            registry=registry,
            backend=backend,
            preview=cancel,
            output_root=tmp_path / "execution",
            timeout=30,
        )
    result = json.loads((tmp_path / "execution/result.json").read_bytes())
    assert result["error_code"] == "color_execution_interrupted" and result["resolved"] is None
    assert (
        registry.inspect(result["child"]["version_sha256"]).parent_version == parent.version_sha256
    )
    workflow = json.loads(store.read_artifact(envelope))["workflow_id"]
    assert store.status(workflow).operations[-1].status == "running"


def test_missing_managed_backend_blocks_without_asset_mutation(tmp_path):
    from self_improving.harness.x2env.local_color_execution import execute_color_repair

    store, registry, backend, parent, proof, candidate, proposal, envelope = prepared(tmp_path)
    result = execute_color_repair(
        candidate,
        proposal,
        envelope,
        store=store,
        registry=registry,
        backend=None,
        preview=None,
        output_root=tmp_path / "execution",
        timeout=30,
    )
    assert result.status == "blocked" and result.child is None and result.resolved is None


@pytest.mark.parametrize(
    "timeout,path", [(0, "absolute"), (601, "absolute"), (True, "absolute"), (30, "relative")]
)
def test_invalid_invocation_is_rejected_before_any_execution(tmp_path, timeout, path):
    from pathlib import Path

    from self_improving.harness.x2env.local_color_execution import execute_color_repair

    with pytest.raises(ValueError):
        execute_color_repair(
            None,
            None,
            None,
            store=None,
            registry=None,
            backend=None,
            preview=None,
            output_root=tmp_path / "output" if path == "absolute" else Path("relative"),
            timeout=timeout,
        )
    assert not (tmp_path / "output").exists()
