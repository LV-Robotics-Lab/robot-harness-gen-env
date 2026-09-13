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
from .assets import AssetRegistry, AssetVersion
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
        _verify_child_assessment(
            store,
            assessment,
            child,
            proof.image,
            entity.category,
            model=backend.model,
            executable_sha=backend.executable_sha,
            want_color=entity.color,
            want_material=entity.material,
        )
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


def verify_color_repair_approval(
    store, *, workflow_id: str, operation_id: str
) -> tuple[ColorRepairCandidate, ColorRepairProposalResult, RepairReservation]:
    """Audit a reserved attempt, including failures without an execution result.

    Historical executable identity comes from the committed proposal and its transport,
    not a newly installed executable. This function grants no execution authority.
    """
    snapshot = store.status(workflow_id)
    matches = [
        (i, op)
        for i, op in enumerate(snapshot.operations)
        if op.operation_id == operation_id and op.capability == "asset.revise"
    ]
    if len(matches) != 1 or matches[0][1].repair_reservation is None:
        raise ValueError("color_history_missing_reservation")
    index, operation = matches[0]
    reservation = operation.repair_reservation
    artifact_closure(store, (reservation.approval,))

    def read(ref):
        return json.loads(store.read_artifact(ref))

    approved = read(reservation.approval)
    candidate_ref = ArtifactRef.model_validate(approved["candidate"])
    proposal_ref = ArtifactRef.model_validate(approved["proposal"])
    candidate = ColorRepairCandidate.model_validate_json(store.read_artifact(candidate_ref))
    proposal = ColorRepairProposalResult.model_validate_json(store.read_artifact(proposal_ref))
    if (
        reservation.kind != "asset"
        or reservation.cost != 1
        or reservation.failure_fingerprint != color_failure_fingerprint(candidate)
        or proposal.candidate != candidate
        or proposal.status != "completed"
        or proposal.error_code is not None
        or proposal.proposal is None
        or proposal.proposal.declared_color != candidate.requested_color
        or not any(
            op.capability == "codex.asset_color"
            and op.status == "succeeded"
            and op.result
            and proposal_ref in op.result.outputs
            for op in snapshot.operations[:index]
        )
    ):
        raise ValueError("color_history_reservation_mismatch")
    bundle = InputBundle.model_validate_json(store.read_artifact(snapshot.input_bundle))
    scene = SceneIR.model_validate_json(store.read_artifact(candidate.scene_ir))
    entity = next((e for e in scene.entities if e.id == candidate.entity_id), None)
    if (
        scene.input_sha256 != bundle.request_sha256
        or bundle.seed != snapshot.request.seed
        or entity is None
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
        raise ValueError("color_history_candidate_mismatch")
    patch = AssetPatch(base_color=proposal.proposal.rgba, color_mode="uniform_replace")
    expected_approval = {
        "authority": "harness_controller",
        "approved": True,
        "parent_version": candidate.parent_version,
        "patch": patch.model_dump(mode="json", exclude_none=True),
        "workflow_id": workflow_id,
        "base_revision": reservation.base_revision,
        "input_bundle": snapshot.input_bundle.model_dump(),
        "scene_ir": candidate.scene_ir.model_dump(),
        "candidate": candidate_ref.model_dump(),
        "proposal": proposal_ref.model_dump(),
        "cost": 1,
        "failure_fingerprint": reservation.failure_fingerprint,
    }
    if (
        approved != expected_approval
        or approved.get("approved") is not True
        or type(approved.get("cost")) is not int
        or type(approved.get("base_revision")) is not int
    ):
        raise ValueError("color_history_approval_mismatch")
    artifact_closure(store, (proposal_ref, proposal.receipt, *proposal.evidence))
    proposal_receipt = read(proposal.receipt)
    sha = proposal_receipt.get("executable_sha256")
    expected_proposal = {
        "schema_version": "x2env.local_color_proposal.v1",
        "status": "completed",
        "error_code": None,
        "candidate": candidate.model_dump(mode="json"),
        "proposal": proposal.proposal.model_dump(mode="json"),
        "authority": "advisory_only",
        "physical_evaluated": False,
        "external_agent_executed": True,
        "evidence": [ref.model_dump() for ref in proposal.evidence if ref != proposal.receipt],
    }
    if (
        any(proposal_receipt.get(k) != v for k, v in expected_proposal.items())
        or proposal_receipt.get("external_agent_executed") is not True
        or proposal_receipt.get("physical_evaluated") is not False
        or not isinstance(sha, str)
        or len(sha) != 64
        or any(c not in "0123456789abcdef" for c in sha)
    ):
        raise ValueError("color_history_proposal_binding_mismatch")
    records = [read(ref) for ref in proposal.evidence if ref.media_type == "application/json"]
    if not _executed(records, sha) or proposal.proposal.model_dump(mode="json") not in records:
        raise ValueError("color_history_model_evidence_missing")
    return candidate, proposal, reservation


def verify_color_repair_result(
    store, result_ref: ArtifactRef, *, workflow_id: str
) -> tuple[ColorRepairCandidate, ColorRepairExecutionResult]:
    """Audit a committed repair without new execution or live-owner authority.

    The controller's successful operation is required, not inferred from CAS presence.
    Return the original candidate and checked execution for downstream provenance binding.
    """
    result = ColorRepairExecutionResult.model_validate_json(store.read_artifact(result_ref))
    snapshot = store.status(workflow_id)
    operations = [
        (i, op)
        for i, op in enumerate(snapshot.operations)
        if op.capability == "asset.revise"
        and op.status == "succeeded"
        and op.result
        and result_ref in op.result.outputs
        and result.receipt in op.result.outputs
    ]
    if len(operations) != 1:
        raise ValueError("color_history_execution_not_committed")
    _, operation = operations[0]
    artifact_closure(store, (result_ref, result.receipt, *result.evidence))

    def read(ref):
        return json.loads(store.read_artifact(ref))

    receipt = read(result.receipt)
    expected = {
        "schema_version": "x2env.local_color_execution.v1",
        "status": "succeeded",
        "error_code": None,
        "physical_evaluated": False,
        "parent_mismatch_preserved": True,
        **{
            name: getattr(result, name).model_dump(mode="json") if getattr(result, name) else None
            for name in ("resolved", "child", "preview", "assessment")
        },
        "evidence": [ref.model_dump() for ref in result.evidence if ref != result.receipt],
    }
    if (
        result.status != "succeeded"
        or result.error_code is not None
        or any(
            getattr(result, name) is None for name in ("resolved", "child", "preview", "assessment")
        )
        or any(receipt.get(k) != v for k, v in expected.items())
    ):
        raise ValueError("color_history_result_binding_mismatch")
    candidate_ref = ArtifactRef.model_validate(receipt["candidate"])
    proposal_ref = ArtifactRef.model_validate(receipt["proposal"])
    envelope_ref = ArtifactRef.model_validate(receipt["reservation"])
    if not {candidate_ref, proposal_ref, envelope_ref} <= set(result.evidence):
        raise ValueError("color_history_evidence_missing")
    candidate, proposal, reservation = verify_color_repair_approval(
        store, workflow_id=workflow_id, operation_id=operation.operation_id
    )
    envelope = ColorRepairReservationEnvelope.model_validate_json(store.read_artifact(envelope_ref))
    if (
        envelope.workflow_id != workflow_id
        or envelope.operation_id != operation.operation_id
        or envelope.input_bundle != snapshot.input_bundle
        or envelope.scene_ir != candidate.scene_ir
        or envelope.candidate != candidate_ref
        or envelope.proposal != proposal_ref
        or envelope.reservation != reservation
        or ColorRepairCandidate.model_validate_json(store.read_artifact(candidate_ref)) != candidate
        or ColorRepairProposalResult.model_validate_json(store.read_artifact(proposal_ref))
        != proposal
        or read(reservation.approval)["candidate"] != candidate_ref.model_dump()
        or read(reservation.approval)["proposal"] != proposal_ref.model_dump()
    ):
        raise ValueError("color_history_reservation_mismatch")
    patch = AssetPatch(base_color=proposal.proposal.rgba, color_mode="uniform_replace")
    child, proof, assessment = result.child, result.preview, result.assessment
    if (
        AssetRegistry(store).inspect(child.version_sha256) != child
        or child.parent_version != candidate.parent_version
        or child.geometry_sha256 != candidate.version.geometry_sha256
        or child.license != candidate.version.license
        or child.source != candidate.version.source
        or child.category != candidate.version.category
        or child.asset_id != candidate.version.asset_id
        or result.resolved
        != ResolvedAsset(
            entity_id=candidate.entity_id,
            version_sha256=child.version_sha256,
            acquisition_source="local",
            selection="exact",
        )
    ):
        raise ValueError("color_history_child_mismatch")
    child_receipt = read(child.receipt)
    if (
        child_receipt.get("attribute_provenance")
        != {
            name: {
                "kind": "controller_approved_revision",
                "approval": reservation.approval.model_dump(),
            }
            for name in ("base_color", "color_mode")
        }
        or child_receipt.get("parent_version") != candidate.parent_version
        or child_receipt.get("patch") != patch.model_dump(mode="json", exclude_none=True)
    ):
        raise ValueError("color_history_child_approval_mismatch")
    parent_report = read(candidate.version.normalization_report)
    child_report = read(child.normalization_report)
    revised_keys = {"files", "asset_revision", "checks"}
    if (
        child_receipt.get("normalization_report") != child.normalization_report.model_dump()
        or child.entrypoint != candidate.version.entrypoint
        or {f.path for f in child.files} != {f.path for f in candidate.version.files}
        or {k: v for k, v in child_report.items() if k not in revised_keys}
        != {k: v for k, v in parent_report.items() if k not in revised_keys}
        or child_report.get("files")
        != [
            {"path": f.path, "sha256": f.artifact.sha256, "size_bytes": f.artifact.size_bytes}
            for f in sorted(child.files, key=lambda f: f.path)
        ]
        or child_report.get("asset_revision")
        != {
            "parent_version": candidate.parent_version,
            "patch": patch.model_dump(mode="json", exclude_none=True),
            "approval": reservation.approval.model_dump(),
            "basis": "supplied_not_measured",
            "color_replacement": child_receipt.get("color_replacement"),
        }
        or child_report.get("checks")
        != {
            **parent_report.get("checks", {}),
            "physical_profile": "not_run",
            "genesis_load_step": "not_run",
            "visual_intent": "not_run",
        }
    ):
        raise ValueError("color_history_normalization_mismatch")
    preview_receipt = read(proof.receipt)
    if (
        proof.status != "passed"
        or proof.image is None
        or proof.version_sha256 != child.version_sha256
        or proof.receipt == candidate.preview_proof.receipt
        or preview_receipt.get("scope") != "asset_preview_scope"
        or preview_receipt.get("status") != "passed"
        or preview_receipt.get("version_sha256") != child.version_sha256
        or proof.image.model_dump() not in preview_receipt.get("outputs", {}).values()
    ):
        raise ValueError("color_history_preview_mismatch")
    with Image.open(BytesIO(store.read_artifact(proof.image))) as image:
        image.verify()
    identity = read(proposal.receipt)
    _verify_child_assessment(
        store,
        assessment,
        child,
        proof.image,
        candidate.version.category,
        model=identity.get("model"),
        executable_sha=identity.get("executable_sha256"),
        want_color=candidate.requested_color,
        want_material=candidate.requested_material,
    )
    return candidate, result


def _verify_child_assessment(
    store, assessment, child, image, category, *, model, executable_sha, want_color, want_material
):
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
    _audit_child_model_calls(
        store,
        assessment,
        image,
        child,
        category,
        model,
        executable_sha,
        want_color,
        want_material,
        detail,
    )


def _audit_child_model_calls(
    store, assessment, image, child, category, model, sha, want_color, want_material, detail
):
    """Reconsume recorded Codex responses through original a6; no external inference.

    Transport layout is CodexBackend.assess_asset_candidates/_invoke. Only the two
    image-path fields are relocated to a temporary file containing verified CAS bytes.
    """
    from tempfile import TemporaryDirectory
    from types import SimpleNamespace

    from self_improving.asset_pipeline.active.asset_reuse.lib.a6_verify import verify_candidate

    from .asset_advisory import VisualAnswer
    from .schema_export import structured_output_schema

    receipt = json.loads(store.read_artifact(assessment.receipt))
    if (
        not isinstance(model, str)
        or not model
        or receipt.get("model") != model
        or receipt.get("executable_sha256") != sha
    ):
        raise ValueError("color_execution_child_model_identity_mismatch")
    prefix = (
        "You are the Harness advisory visual backend. Use no tools. Answer the "
        "following question about the attached actual candidate image. Return "
        "the supplied JSON schema, null for unasked scalar fields and empty "
        "lists for unasked list fields. Do not claim simulation or acquisition "
        "success. Candidate input SHA256: " + image.sha256 + "\n"
    )
    records = []
    for ref in assessment.evidence:
        raw = store.read_artifact(ref)
        records.append(
            json.loads(raw)
            if ref.media_type == "application/json"
            else raw.decode()
            if ref.media_type == "text/plain"
            else None
        )
    starts = [
        i
        for i, value in enumerate(records)
        if isinstance(value, str)
        and value.startswith("You are the Harness advisory visual backend.")
    ]
    calls, identities = [], set()
    for i, start in enumerate(starts):
        group = records[start : starts[i + 1] if i + 1 < len(starts) else len(records)]
        objects = [value for value in group if isinstance(value, dict)]
        invocations = [value for value in objects if "argv" in value]
        processes = [value for value in objects if "start_ticks" in value]
        terminals = [value for value in objects if "reaped" in value]
        responses = [value for value in objects if set(value) == set(VisualAnswer.model_fields)]
        if (
            len(invocations) != 1
            or len(processes) != 1
            or len(terminals) != 1
            or len(responses) != 1
            or not _executed([*processes, *terminals], sha)
            or structured_output_schema(VisualAnswer) not in objects
        ):
            raise ValueError("color_execution_child_model_execution_mismatch")
        if (
            objects.index(invocations[0]) >= objects.index(processes[0])
            or objects.index(processes[0]) >= objects.index(terminals[0])
            or objects.index(terminals[0]) >= objects.index(responses[0])
            or (processes[0]["pid"], processes[0]["start_ticks"]) in identities
        ):
            raise ValueError("color_execution_child_model_execution_mismatch")
        identities.add((processes[0]["pid"], processes[0]["start_ticks"]))
        invocation = invocations[0]
        argv, media = invocation.get("argv"), invocation.get("media")

        def argument(flag):
            if (
                not isinstance(argv, list)
                or argv.count(flag) != 1
                or argv.index(flag) + 1 >= len(argv)
            ):
                return None
            return argv[argv.index(flag) + 1]

        if (
            not isinstance(argv, list)
            or argv.count("--model") != 1
            or argv.index("--model") + 1 >= len(argv)
            or argv[argv.index("--model") + 1] != model
            or not isinstance(media, list)
            or len(media) != 1
            or media[0].get("input_sha256") != image.sha256
            or media[0].get("path") != detail.get("image")
            or argument("-i") != media[0].get("path")
            or argument("--output-last-message")
            != str(Path(processes[0].get("attempt_root", "")) / "proposal.json")
            or argument("--output-schema")
            != str(Path(processes[0].get("attempt_root", "")) / "proposal.schema.json")
            or detail.get("thumbnail") != detail.get("image")
            or not group[0].startswith(prefix)
        ):
            raise ValueError("color_execution_child_model_context_mismatch")
        response = VisualAnswer.model_validate_json(json.dumps(responses[0])).model_dump_json()
        calls.append((group[0], response))
    if not calls or sum(isinstance(r, dict) and "start_ticks" in r for r in records) != len(calls):
        raise ValueError("color_execution_child_model_execution_mismatch")
    consumed, errors = 0, []

    def infer(path, question):
        nonlocal consumed
        if consumed >= len(calls) or calls[consumed][0] != prefix + question:
            errors.append("question mismatch")
            raise ValueError("recorded question mismatch")
        response = calls[consumed][1]
        consumed += 1
        return response

    with TemporaryDirectory(prefix="x2env-child-visual-audit-") as directory:
        path = Path(directory) / "preview.png"
        path.write_bytes(store.read_artifact(image))
        replayed = verify_candidate(
            SimpleNamespace(
                candidate_id=child.version_sha256,
                name=child.asset_id,
                metadata={"thumbnail": str(path)},
            ),
            category,
            infer=infer,
            model_name=model,
            want_color=want_color,
            want_material=want_material,
        )
        replayed["image"] = detail.get("image")
        replayed["thumbnail"] = detail.get("thumbnail")
    if errors or consumed != len(calls) or replayed != detail:
        raise ValueError("color_execution_child_model_context_mismatch")
