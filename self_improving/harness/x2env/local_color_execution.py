"""Execute one controller-reserved local color revision and reverify its child.

No workflow, retries or search authority lives here. Model/render ports are deployment-owned.
"""

import hashlib
import json
import os
import time
from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image

from .artifacts import artifact_closure
from .asset_advisory import AssetVisualAssessment, VisualCandidate
from .asset_preview import AssetPreviewProof
from .asset_revision import AssetPatch, AssetRevision
from .assets import AssetVersion
from .compile import ResolvedAsset
from .contracts import ArtifactRef, InputBundle, Model, RepairReservation, SceneIR
from .local_color_advisory import (
    ColorRepairCandidate,
    ColorRepairProposalResult,
    classify_color_repair,
)
from .store import process_identity


class ColorRepairReservationEnvelope(Model):
    workflow_id: str
    operation_id: str
    reservation: RepairReservation
    input_bundle: ArtifactRef
    scene_ir: ArtifactRef
    candidate: ArtifactRef
    proposal: ArtifactRef


class ColorRepairExecutionResult(Model):
    status: Literal["succeeded", "failed", "blocked"]
    resolved: ResolvedAsset | None
    child: AssetVersion | None
    preview: AssetPreviewProof | None
    assessment: AssetVisualAssessment | None
    receipt: ArtifactRef
    evidence: tuple[ArtifactRef, ...]
    error_code: str | None
    physical_evaluated: Literal[False] = False


def color_failure_fingerprint(candidate: ColorRepairCandidate) -> str:
    candidate = ColorRepairCandidate.model_validate_json(candidate.model_dump_json())
    payload = {
        "scene_ir": candidate.scene_ir.model_dump(),
        "entity_id": candidate.entity_id,
        "parent_version": candidate.parent_version,
        "preview_image": candidate.preview_proof.image.model_dump()
        if candidate.preview_proof.image
        else None,
        "original_detail": candidate.assessment.verdicts[0].detail.model_dump(),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def execute_color_repair(
    candidate_ref,
    proposal_ref,
    reservation_ref,
    *,
    store,
    registry,
    backend,
    preview,
    output_root: Path,
    timeout: int = 600,
):
    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise ValueError("invalid_color_execution_timeout")
    root = Path(output_root)
    if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("unsafe_color_execution_output")
    root.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    deadline = started + timeout
    evidence = [candidate_ref, proposal_ref, reservation_ref]
    child = proof = assessment = resolved = None
    status, error, cancellation = "failed", None, None

    def record(name, value):
        raw = json.dumps(value, sort_keys=True).encode()
        (root / name).write_bytes(raw)
        ref = store.write_artifact(raw, "application/json")
        evidence.append(ref)
        return ref

    def remaining():
        left = int(deadline - time.monotonic())
        if left < 1:
            raise ValueError("color_execution_timeout")
        return left

    def read(ref):
        return json.loads(store.read_artifact(ref))

    try:
        remaining()
        if backend is None or preview is None:
            raise FileNotFoundError("managed_codex_asset_assessment_and_asset_preview_required")
        artifact_closure(store, evidence)
        candidate = ColorRepairCandidate.model_validate_json(store.read_artifact(candidate_ref))
        proposed = ColorRepairProposalResult.model_validate_json(store.read_artifact(proposal_ref))
        envelope = ColorRepairReservationEnvelope.model_validate_json(
            store.read_artifact(reservation_ref)
        )
        current = store.status(envelope.workflow_id)
        operation = current.operations[-1] if current.operations else None
        reservation = envelope.reservation
        if (
            current.status != "active"
            or current.owner != process_identity(os.getpid())
            or operation is None
            or operation.capability != "asset.revise"
            or operation.status != "running"
            or operation.operation_id != envelope.operation_id
            or operation.repair_reservation != reservation
            or reservation.kind != "asset"
            or reservation.cost != 1
            or reservation.base_revision != current.revision
            or envelope.input_bundle != current.input_bundle
            or envelope.scene_ir != (current.scene_ir or current.pending_scene_ir)
            or envelope.candidate != candidate_ref
            or envelope.proposal != proposal_ref
            or candidate.scene_ir != envelope.scene_ir
            or reservation.failure_fingerprint != color_failure_fingerprint(candidate)
        ):
            raise ValueError("color_execution_reservation_mismatch")
        if not any(
            op.capability == "codex.asset_color"
            and op.status == "succeeded"
            and op.result
            and proposal_ref in op.result.outputs
            for op in current.operations[:-1]
        ):
            raise ValueError("color_execution_proposal_not_committed")
        bundle = InputBundle.model_validate_json(store.read_artifact(envelope.input_bundle))
        scene = SceneIR.model_validate_json(store.read_artifact(candidate.scene_ir))
        if scene.input_sha256 != bundle.request_sha256 or bundle.seed != current.request.seed:
            raise ValueError("color_execution_input_mismatch")
        entity = next(
            (entity for entity in scene.entities if entity.id == candidate.entity_id), None
        )
        if (
            entity is None
            or proposed.status != "completed"
            or proposed.error_code is not None
            or proposed.proposal is None
            or proposed.candidate != candidate
            or proposed.proposal.declared_color != candidate.requested_color
            or classify_color_repair(
                store,
                candidate.scene_ir,
                entity,
                candidate.version,
                candidate.preview_proof,
                candidate.assessment,
            )
            != candidate
        ):
            raise ValueError("color_execution_candidate_mismatch")
        proposal_receipt = read(proposed.receipt)
        expected = {
            "schema_version": "x2env.local_color_proposal.v1",
            "status": "completed",
            "error_code": None,
            "candidate": candidate.model_dump(mode="json"),
            "proposal": proposed.proposal.model_dump(mode="json"),
            "authority": "advisory_only",
            "physical_evaluated": False,
            "external_agent_executed": True,
            "executable_sha256": backend.executable_sha,
        }
        if any(proposal_receipt.get(k) != v for k, v in expected.items()) or proposal_receipt.get(
            "evidence"
        ) != [ref.model_dump() for ref in proposed.evidence if ref != proposed.receipt]:
            raise ValueError("color_execution_proposal_binding_mismatch")
        model_records = [
            read(ref) for ref in proposed.evidence if ref.media_type == "application/json"
        ]
        if (
            not _executed(model_records, backend.executable_sha)
            or proposed.proposal.model_dump(mode="json") not in model_records
        ):
            raise ValueError("color_execution_model_evidence_missing")
        patch = AssetPatch(base_color=proposed.proposal.rgba, color_mode="uniform_replace")
        approval = read(reservation.approval)
        expected_approval = {
            "authority": "harness_controller",
            "approved": True,
            "parent_version": candidate.parent_version,
            "patch": patch.model_dump(mode="json", exclude_none=True),
            "workflow_id": current.workflow_id,
            "base_revision": current.revision,
            "input_bundle": envelope.input_bundle.model_dump(),
            "scene_ir": envelope.scene_ir.model_dump(),
            "candidate": candidate_ref.model_dump(),
            "proposal": proposal_ref.model_dump(),
            "cost": 1,
            "failure_fingerprint": reservation.failure_fingerprint,
        }
        if (
            approval != expected_approval
            or approval.get("approved") is not True
            or type(approval.get("cost")) is not int
            or type(approval.get("base_revision")) is not int
        ):
            raise ValueError("color_execution_approval_mismatch")
        remaining()
        if store.status(envelope.workflow_id) != current:
            raise ValueError("color_execution_reservation_head_changed")
        child = AssetRevision(registry, store).revise(
            candidate.parent_version,
            patch,
            approval=reservation.approval,
            output_root=root / "child",
        )
        record("child.json", child.model_dump(mode="json"))
        if (
            child.parent_version != candidate.parent_version
            or child.geometry_sha256 != candidate.version.geometry_sha256
        ):
            raise ValueError("color_execution_child_geometry_mismatch")
        proof = preview(child, timeout=remaining())
        proof = AssetPreviewProof.model_validate_json(proof.model_dump_json())
        record("preview.json", proof.model_dump(mode="json"))
        artifact_closure(store, (proof.receipt,))
        proof_receipt = read(proof.receipt)
        if (
            proof.status != "passed"
            or proof.image is None
            or proof.version_sha256 != child.version_sha256
            or proof.receipt == candidate.preview_proof.receipt
            or proof_receipt.get("scope") != "asset_preview_scope"
            or proof_receipt.get("status") != "passed"
            or proof_receipt.get("version_sha256") != child.version_sha256
            or proof.image.model_dump() not in proof_receipt.get("outputs", {}).values()
        ):
            raise ValueError("color_execution_child_preview_mismatch")
        with Image.open(BytesIO(store.read_artifact(proof.image))) as image:
            image.verify()
        assessment = backend.assess_asset_candidates(
            (
                VisualCandidate(
                    candidate_id=child.version_sha256,
                    name=child.asset_id,
                    category=entity.category,
                    want_color=entity.color,
                    want_material=entity.material,
                    preview=proof.image,
                ),
            ),
            output_root=root / "assessment",
            timeout=remaining(),
        )
        assessment = AssetVisualAssessment.model_validate_json(assessment.model_dump_json())
        record("assessment.json", assessment.model_dump(mode="json"))
        artifact_closure(store, (assessment.receipt, *assessment.evidence))
        remaining()
        if assessment.status == "blocked":
            raise FileNotFoundError(
                assessment.error_code or "managed_codex_asset_assessment_required"
            )
        _verify_child_assessment(store, assessment, child, proof.image, entity.category)
        resolved = ResolvedAsset(
            entity_id=entity.id,
            version_sha256=child.version_sha256,
            acquisition_source="local",
            selection="exact",
        )
        status = "succeeded"
    except FileNotFoundError as exc:
        status, error = "blocked", "blocked_external_resource"
        record("error.json", {"error_code": error, "reason": str(exc)})
    except KeyboardInterrupt as exc:
        error, cancellation = "color_execution_interrupted", exc
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        error = (
            str(exc)
            if str(exc).startswith("color_execution_")
            else "invalid_color_execution_evidence"
        )
        record("error.json", {"error_code": error, "reason": str(exc)})
    receipt = record(
        "result.json",
        {
            "schema_version": "x2env.local_color_execution.v1",
            "status": status,
            "error_code": error,
            "candidate": candidate_ref.model_dump(),
            "proposal": proposal_ref.model_dump(),
            "reservation": reservation_ref.model_dump(),
            "resolved": resolved.model_dump(mode="json") if resolved else None,
            "child": child.model_dump(mode="json") if child else None,
            "preview": proof.model_dump(mode="json") if proof else None,
            "assessment": assessment.model_dump(mode="json") if assessment else None,
            "evidence": [ref.model_dump() for ref in evidence],
            "elapsed_seconds": time.monotonic() - started,
            "physical_evaluated": False,
            "parent_mismatch_preserved": True,
        },
    )
    if cancellation is not None:
        raise cancellation
    return ColorRepairExecutionResult(
        status=status,
        resolved=resolved,
        child=child,
        preview=proof,
        assessment=assessment,
        receipt=receipt,
        evidence=tuple(evidence),
        error_code=error,
    )


def _executed(records, sha):
    return any(
        isinstance(p, dict)
        and p.get("executable_sha256") == sha
        and all(type(p.get(k)) is int and p[k] > 0 for k in ("pid", "pgid", "start_ticks"))
        and p["pid"] == p["pgid"]
        and any(
            isinstance(t, dict)
            and type(t.get("pid")) is int
            and t["pid"] == p["pid"]
            and type(t.get("returncode")) is int
            and t["returncode"] == 0
            and t.get("reaped") is True
            and "failure" in t
            and t["failure"] is None
            for t in records
        )
        for p in records
    )


def _verify_child_assessment(store, assessment, child, image, category):
    def read(ref):
        return json.loads(store.read_artifact(ref))

    receipt = read(assessment.receipt)
    if (
        assessment.status != "completed"
        or assessment.error_code is not None
        or receipt.get("verifier") != "asset_reuse.lib.a6_verify.verify_candidate"
        or receipt.get("status") != assessment.status
        or receipt.get("error_code") != assessment.error_code
        or receipt.get("verdicts") != [v.model_dump(mode="json") for v in assessment.verdicts]
        or receipt.get("evidence")
        != [ref.model_dump() for ref in assessment.evidence if ref != assessment.receipt]
        or receipt.get("physical_evaluated") is not False
        or len(assessment.verdicts) != 1
    ):
        raise ValueError("color_execution_child_assessment_mismatch")
    verdict = assessment.verdicts[0]
    if (
        verdict.candidate_id != child.version_sha256
        or verdict.preview != image
        or verdict.detail not in assessment.evidence
    ):
        raise ValueError("color_execution_child_assessment_mismatch")
    detail = read(verdict.detail)
    if (
        detail.get("candidate_id") != child.version_sha256
        or detail.get("asked_category") != category
        or detail.get("name") != child.asset_id
        or detail.get("verdict") != verdict.verdict
    ):
        raise ValueError("color_execution_child_assessment_mismatch")
    if verdict.verdict != "match":
        raise ValueError("color_execution_child_visual_mismatch")
