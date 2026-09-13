"""Public Harness resume with Store/CAS and explicit synthetic external evidence."""

import hashlib
import json

import pytest

from self_improving.harness.x2env.contracts import ToolResult
from self_improving.harness.x2env.diagnosis import DiagnosisResult, ScenePatch
from self_improving.harness.x2env.harness import Harness
from tests.self_improving.harness.x2env.test_completion import completed_fixture


def ready(tmp_path, *, invalid_patch=False, spent=False, bad_proposal=False):
    from self_improving.harness.x2env.contracts import RepairReservation, SceneIR

    store, snapshot = completed_fixture(tmp_path, grounding=True)
    if spent:
        from self_improving.harness.x2env.observation import ObservationResult

        observed = ObservationResult.model_validate_json(store.read_artifact(snapshot.observation))
        report = json.loads(store.read_artifact(observed.physics_report))
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "input": report["input_sha256"],
                    "error_code": report.get("error_code"),
                    "checks": [c for c in report.get("checks", []) if c.get("status") != "passed"],
                    "images": [f.image.sha256 for f in observed.observation.frames],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        approval = store.write_artifact(b"fixture historical reservation", "text/plain")
        snapshot = store.begin_operation(
            snapshot,
            "asset.revise",
            repair_reservation=RepairReservation(
                kind="asset",
                cost=1 if spent == "repeated" else 2,
                failure_fingerprint=fingerprint if spent == "repeated" else "a" * 64,
                approval=approval,
                base_revision=snapshot.revision,
            ),
        )
        snapshot = store.complete_operation(
            snapshot,
            ToolResult(
                operation_id=snapshot.operations[-1].operation_id,
                status="failed",
                error_code="fixture_failure",
            ),
            snapshot.input_bundle,
            status="active",
        )
    scene = SceneIR.model_validate_json(store.read_artifact(snapshot.scene_ir))
    original = DiagnosisResult.model_validate_json(store.read_artifact(snapshot.diagnosis))
    proposal = original.proposal.model_copy(
        update={
            "visual_intent": "failed",
            "scene_patches": (
                ScenePatch(
                    entity_id="unknown" if invalid_patch else "item",
                    pose=scene.entities[0].pose.model_copy(update={"yaw_degrees": 30.0}),
                    joints=(),
                ),
            ),
        }
    )
    receipt = json.loads(store.read_artifact(original.receipt))
    receipt["proposal"] = proposal.model_dump(mode="json")
    receipt_ref = store.write_artifact(json.dumps(receipt).encode(), "application/json")
    diagnostic = original.model_copy(update={"proposal": proposal, "receipt": receipt_ref})
    if bad_proposal:
        diagnostic = diagnostic.model_copy(
            update={"status": "failed", "error_code": "fixture_failure"}
        )
    ref = store.write_artifact(diagnostic.model_dump_json().encode(), "application/json")
    snapshot = store.begin_operation(snapshot, "codex.diagnose")
    snapshot = store.complete_operation(
        snapshot,
        ToolResult(
            operation_id=snapshot.operations[-1].operation_id, status="succeeded", outputs=(ref,)
        ),
        snapshot.input_bundle,
        status="blocked",
        reason="environment_package_materializer",
        diagnosis=ref,
    )
    return store, snapshot


def test_public_resume_reserves_before_layout_write_and_binds_success_receipt(tmp_path):
    store, snapshot = ready(tmp_path)
    result = Harness(store.database.parent).resume(snapshot.workflow_id)
    operation = next(op for op in result.operations if op.capability == "revise")
    assert operation.repair_reservation is not None
    assert operation.status == "succeeded"
    row = json.loads(store.read_artifact(result.revisions[-1]))
    from self_improving.harness.x2env.contracts import ArtifactRef

    envelope = json.loads(store.read_artifact(ArtifactRef.model_validate(row["reservation"])))
    assert envelope["operation_id"] == operation.operation_id
    assert envelope["reservation"] == operation.repair_reservation.model_dump(mode="json")


def test_failed_patch_still_consumes_its_persistent_reservation(tmp_path):
    from self_improving.harness.x2env.store import Store

    store, snapshot = ready(tmp_path, invalid_patch=True)
    result = Harness(store.database.parent).resume(snapshot.workflow_id)
    operation = next(op for op in result.operations if op.capability == "revise")
    assert operation.status == "failed" and operation.repair_reservation.cost == 1
    assert result.revisions == ()
    assert Store(store.database.parent).status(result.workflow_id) == result


def test_bad_advisory_is_rejected_before_reserving_or_executing_repair(tmp_path):
    store, snapshot = ready(tmp_path, bad_proposal=True)
    result = Harness(store.database.parent).resume(snapshot.workflow_id)
    assert result.status == "failed" and result.stop_reason == "repair_preflight_rejected"
    assert result.operations[-1].capability == "repair.reject"
    assert not any(op.capability == "revise" for op in result.operations)
    assert result.operations[: len(snapshot.operations)] == snapshot.operations


@pytest.mark.parametrize(
    "spent,code", [(True, "revision_budget_exhausted"), ("repeated", "repeated_failure")]
)
def test_gate_rejection_is_not_executable_and_never_overwrites_prior_operations(
    tmp_path, spent, code
):
    store, snapshot = ready(tmp_path, spent=spent)
    result = Harness(store.database.parent).resume(snapshot.workflow_id)
    assert result.status == "failed" and result.stop_reason == code
    assert result.operations[: len(snapshot.operations)] == snapshot.operations
    assert result.operations[-1].capability == "repair.reject"
    assert result.operations[-1].repair_reservation is None
    assert not any(op.capability == "revise" for op in result.operations)
    receipt = json.loads(store.read_artifact(result.operations[-1].result.outputs[0]))
    assert receipt["executable_repair"] is False
    assert receipt["reason"] in {"repair_budget_exhausted", "repair_repeated_failure"}
