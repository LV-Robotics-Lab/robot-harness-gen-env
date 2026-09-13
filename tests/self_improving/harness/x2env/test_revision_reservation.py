"""A real Store reservation authorizes one current controller revision, not future reuse."""

import json

import pytest

from self_improving.harness.x2env.compile import ResolvedAsset, ResolvedAssetSet
from self_improving.harness.x2env.contracts import Pose, RepairReservation, ToolResult, X2EnvRequest
from self_improving.harness.x2env.diagnosis import DiagnosisProposal, DiagnosisResult, ScenePatch
from self_improving.harness.x2env.revision import apply_revision
from tests.self_improving.harness.x2env.test_resolver import inputs


def prepared(tmp_path):
    store, registry, version, scene, _ = inputs(tmp_path)
    assets = ResolvedAssetSet(
        scene_ir=scene,
        assets=(
            ResolvedAsset(
                entity_id="box",
                version_sha256=version.version_sha256,
                acquisition_source="local",
                selection="exact",
            ),
        ),
    )
    bundle = store.write_artifact(b'{"fixture":"input"}', "application/json")
    log = store.write_artifact(b"explicit external model double", "text/plain")
    diagnosis = DiagnosisResult(
        status="completed",
        receipt=log,
        proposal=DiagnosisProposal(
            base_revision=0,
            visual_intent="failed",
            reason="move x",
            evidence_sha256=(scene.sha256,),
            asset_patches=(),
            scene_patches=(
                ScenePatch(
                    entity_id="box",
                    joints=(),
                    pose=Pose(frame="world", position=(0.1, None, None), yaw_degrees=None),
                ),
            ),
        ),
    )
    diagnosis_ref = store.write_artifact(diagnosis.model_dump_json().encode(), "application/json")
    current = store.claim(
        store.submit(
            X2EnvRequest(
                text="fixture",
                seed=0,
                idempotency_key="reservation",
                output_dir=str(tmp_path / "output"),
            )
        ).workflow_id
    )
    current = store.complete_operation(
        current,
        ToolResult(operation_id=current.operations[-1].operation_id, status="succeeded"),
        bundle,
        status="active",
        scene_ir=scene,
        diagnosis=diagnosis_ref,
    )
    approval_body = dict(
        authority="harness_controller",
        approved=True,
        workflow_id=current.workflow_id,
        base_revision=current.revision,
        input_bundle=bundle.model_dump(),
        scene_ir=scene.model_dump(),
        diagnosis=diagnosis_ref.model_dump(),
        cost=1,
        failure_fingerprint="a" * 64,
    )
    approval = store.write_artifact(json.dumps(approval_body).encode(), "application/json")
    reservation = RepairReservation(
        kind="scene",
        cost=1,
        failure_fingerprint="a" * 64,
        approval=approval,
        base_revision=current.revision,
    )
    current = store.begin_operation(current, "revise", repair_reservation=reservation)
    envelope = dict(
        workflow_id=current.workflow_id,
        operation_id=current.operations[-1].operation_id,
        reservation=reservation.model_dump(),
        input_bundle=bundle.model_dump(),
        scene_ir=scene.model_dump(),
        diagnosis=diagnosis_ref.model_dump(),
    )
    return store, registry, scene, assets, diagnosis_ref, current, envelope


def test_revision_binds_current_reservation_and_rejects_terminal_reuse(tmp_path):
    store, registry, scene, assets, diagnosis, current, envelope = prepared(tmp_path)
    ref = store.write_artifact(json.dumps(envelope).encode(), "application/json")
    args = dict(
        output_root=tmp_path / "first",
        history=(),
        failure_fingerprint="a" * 64,
        reservation_ref=ref,
    )
    result = apply_revision(store, registry, scene, assets, diagnosis, **args)
    assert json.loads(store.read_artifact(result.receipt))["reservation"] == ref.model_dump()
    store.complete_operation(
        current,
        ToolResult(
            operation_id=current.operations[-1].operation_id,
            status="failed",
            error_code="post_write_failure",
        ),
        current.input_bundle,
        status="failed",
    )
    args["output_root"] = tmp_path / "again"
    with pytest.raises(ValueError, match="revision_reservation_mismatch"):
        apply_revision(store, registry, scene, assets, diagnosis, **args)
    assert not (tmp_path / "again").exists()


@pytest.mark.parametrize("fault", ["cost", "operation", "scene", "fingerprint", "approval"])
def test_foreign_reservation_cannot_create_revision_directory(tmp_path, fault):
    store, registry, scene, assets, diagnosis, current, envelope = prepared(tmp_path)
    if fault == "cost":
        envelope["reservation"]["cost"] = 2
    elif fault == "operation":
        envelope["operation_id"] = "other-operation"
    elif fault == "scene":
        envelope["scene_ir"] = diagnosis.model_dump()
    elif fault == "fingerprint":
        envelope["reservation"]["failure_fingerprint"] = "b" * 64
    else:
        envelope["reservation"]["approval"] = diagnosis.model_dump()
    ref = store.write_artifact(json.dumps(envelope).encode(), "application/json")
    with pytest.raises(ValueError, match="revision_reservation_mismatch"):
        apply_revision(
            store,
            registry,
            scene,
            assets,
            diagnosis,
            output_root=tmp_path / "bad",
            history=(),
            failure_fingerprint="a" * 64,
            reservation_ref=ref,
        )
    assert not (tmp_path / "bad").exists()
    assert store.status(current.workflow_id) == current
