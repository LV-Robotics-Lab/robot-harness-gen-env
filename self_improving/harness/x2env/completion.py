"""Materialized development completion, separate from workflow and release authority."""

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Literal

from .artifacts import artifact_closure
from .assessment import ASSERTIONS_SHA256, assess_scene
from .assets import AssetRegistry
from .compile import CompiledScene, ResolvedAssetSet
from .contracts import ArtifactRef, BackendProposal, InputBundle, Model, SceneIR, WorkflowSnapshot
from .diagnosis import DiagnosisResult
from .observation import ObservationResult
from .package import build_package
from .replay import ReplayResult


class CompletionResult(Model):
    status: Literal["materialized", "failed"]
    package_path: str | None
    manifest: ArtifactRef | None
    receipt: ArtifactRef
    error_code: str | None


def materialize_completion(snapshot, store, output):
    """Read current journal evidence; never commit workflow state or release assets."""
    snapshot = WorkflowSnapshot.model_validate(snapshot)
    root = Path(output)
    if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("completion output must be new absolute nonsymbolic directory")
    root.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    error = None
    manifest_ref = None
    package_path = None
    assessment_ref = None
    status = "failed"
    try:
        if store.status(snapshot.workflow_id) != snapshot:
            raise ValueError("stale_completion_snapshot")
        if any(
            getattr(snapshot, field) is None
            for field in (
                "input_bundle",
                "scene_ir",
                "compiled_scene",
                "replay_result",
                "observation",
                "diagnosis",
                "validation",
            )
        ):
            raise ValueError("incomplete_completion_evidence")
        allowed_block = (
            snapshot.status == "blocked"
            and snapshot.stop_reason == "environment_package_materializer"
        )
        if snapshot.status != "active" and not allowed_block:
            raise ValueError("invalid_completion_state")
        fields = (
            "input_bundle",
            "scene_ir",
            "compiled_scene",
            "replay_result",
            "observation",
            "diagnosis",
            "validation",
        )
        refs = {name: getattr(snapshot, name) for name in fields}
        budget_refs = _audit_repair_reservations(snapshot, store)
        if snapshot.grounding is not None:
            refs["grounding"] = snapshot.grounding
        if snapshot.proposal is not None:
            refs["proposal"] = snapshot.proposal
        capabilities = {
            "input_bundle": {"ingest"},
            "proposal": {"codex.interpret"},
            "scene_ir": {"codex.interpret", "revise", "codex.ground"},
            "grounding": {"codex.ground"},
            "compiled_scene": {"x2env.compile"},
            "replay_result": {"x2env.replay"},
            "observation": {"observe"},
            "diagnosis": {"codex.diagnose"},
            "validation": {"x2env.validate"},
        }
        for name, ref in refs.items():
            operations = [
                op
                for op in snapshot.operations
                if op.capability in capabilities[name] and op.result and ref in op.result.outputs
            ]
            if not operations or not any(
                op.status == "succeeded"
                or (name == "validation" and allowed_block and op.status == "blocked")
                for op in operations
            ):
                raise ValueError("uncommitted_completion_evidence")
        if snapshot.grounding is not None:
            refs.update({f"revision_{i}": ref for i, ref in enumerate(snapshot.revisions)})
        refs.update({f"repair_evidence_{i}": ref for i, ref in enumerate(budget_refs)})
        artifact_closure(store, list(refs.values()))

        def read(ref):
            return json.loads(store.read_artifact(ref))

        scene = SceneIR.model_validate_json(store.read_artifact(snapshot.scene_ir))
        compiled = CompiledScene.model_validate_json(store.read_artifact(snapshot.compiled_scene))
        from .design_plan import needs_design_grounding

        if needs_design_grounding(scene, compiled.policy):
            raise ValueError("completion_unresolved_scene_values")
        if snapshot.proposal is not None:
            original = BackendProposal.model_validate_json(store.read_artifact(snapshot.proposal))
            if original.proposal is not None and any(
                unknown.reason_kind == "conflict" for unknown in original.proposal.unknowns
            ):
                raise ValueError("completion_unresolved_intent_conflict")
        if snapshot.grounding is not None:
            _verify_grounding(snapshot, store, scene, compiled)
        elif any(
            op.capability == "codex.ground" and op.result and snapshot.scene_ir in op.result.outputs
            for op in snapshot.operations
        ):
            raise ValueError("completion_missing_grounding_receipt")
        bundle = InputBundle.model_validate_json(store.read_artifact(snapshot.input_bundle))
        request = snapshot.request
        if (
            (bundle.text is None) != (request.text is None)
            or len(bundle.images) != len(request.images)
            or (bundle.video is None) != (request.video is None)
            or (
                bundle.text is not None
                and store.read_artifact(bundle.text) != request.text.encode("utf-8")
            )
        ):
            raise ValueError("completion_request_input_mismatch")
        sources = (
            *((bundle.text,) if bundle.text is not None else ()),
            *(image.source for image in bundle.images),
            *((bundle.video.source,) if bundle.video is not None else ()),
        )
        # Exact identity serialization owned by input.ingest: ordered original
        # source refs and the complete request, not relocated filenames or a new
        # digest convention. Reuse immutable CAS; do not reopen user media paths.
        input_digest = hashlib.sha256(
            json.dumps(
                {
                    "request": request.model_dump(mode="json"),
                    "source_refs": [ref.model_dump(mode="json") for ref in sources],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        modalities = [
            name
            for name, present in (
                ("text", bundle.text is not None),
                ("image", bool(bundle.images)),
                ("video", bundle.video is not None),
            )
            if present
        ]
        if (
            bundle.request_sha256 != input_digest
            or scene.input_sha256 != input_digest
            or bundle.modality != ("multimodal" if len(modalities) > 1 else modalities[0])
            or bundle.seed != request.seed
            or compiled.runtime_scene.seed != request.seed
            or compiled.runtime_scene.scene_ir_sha256 != snapshot.scene_ir.sha256
        ):
            raise ValueError("completion_input_binding_mismatch")
        replay = ReplayResult.model_validate_json(store.read_artifact(snapshot.replay_result))
        observed = ObservationResult.model_validate_json(store.read_artifact(snapshot.observation))
        diagnosis = DiagnosisResult.model_validate_json(store.read_artifact(snapshot.diagnosis))
        physical = read(observed.physics_report)
        validation = read(snapshot.validation)
        observation = observed.observation
        observation_receipt = read(observed.receipt)
        if observation_receipt != {
            "observation": observation.model_dump(mode="json"),
            "physics_report": observed.physics_report.model_dump(mode="json"),
            "authority": "harness_observation",
            "camera_time_renewed": False,
        }:
            raise ValueError("completion_observation_binding_mismatch")
        runtime = compiled.runtime_scene
        runtime_dump = runtime.model_dump(mode="json")
        digest = hashlib.sha256(
            json.dumps(runtime_dump, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if (
            compiled.scene_ir != snapshot.scene_ir
            or observation.scene_ir != snapshot.scene_ir
            or read(observation.runtime_scene) != runtime_dump
            or observation.replay_receipt != replay.receipt
        ):
            raise ValueError("completion_scene_binding_mismatch")
        if (
            replay.status != "succeeded"
            or {p.profile for p in replay.profiles} != {"baseline", "half_dt"}
            or len(replay.profiles) != 2
            or any(p.status != "passed" for p in replay.profiles)
        ):
            raise ValueError("completion_replay_not_passed")
        replay_record = read(replay.receipt)
        if (
            replay_record.get("scene") != runtime_dump
            or replay_record.get("status") != "succeeded"
            or replay_record.get("profiles") != [p.model_dump(mode="json") for p in replay.profiles]
        ):
            raise ValueError("completion_replay_binding_mismatch")
        for key, value in {
            "schema_version": "x2env.physical_assessment.v1",
            "scene_ir": snapshot.scene_ir.model_dump(),
            "runtime_scene": observation.runtime_scene.model_dump(),
            "replay_receipt": replay.receipt.model_dump(),
            "scene_sha256": digest,
            "input_sha256": scene.input_sha256,
            "assertions_sha256": ASSERTIONS_SHA256,
            "physical_status": "passed",
            "execution_evidence_bound": True,
        }.items():
            if physical.get(key) != value:
                raise ValueError("completion_physical_binding_mismatch")
        if any(
            validation.get(key) != value
            for key, value in {
                "scene_ir": snapshot.scene_ir.model_dump(),
                "physics_report": observed.physics_report.model_dump(),
                "diagnosis": snapshot.diagnosis.model_dump(),
                "physical_status": "passed",
                "visual_status": "passed",
            }.items()
        ):
            raise ValueError("completion_validation_binding_mismatch")
        if validation.get("status") not in ("succeeded", "passed") and not (
            allowed_block
            and validation.get("status") == "blocked"
            and validation.get("error_code") == "environment_package_materializer"
        ):
            raise ValueError("completion_validation_not_passed")
        proposal = diagnosis.proposal
        if (
            diagnosis.status != "completed"
            or diagnosis.error_code is not None
            or proposal is None
            or proposal.visual_intent != "passed"
            or proposal.base_revision != scene.revision
            or proposal.scene_patches
            or proposal.asset_patches
        ):
            raise ValueError("completion_visual_not_passed")
        advisory = read(diagnosis.receipt)
        expected = {
            "authority": "advisory_only",
            "status": "completed",
            "error_code": None,
            "scene_ir": snapshot.scene_ir.model_dump(),
            "physical_report": observed.physics_report.model_dump(),
            "observation": observation.model_dump(mode="json"),
            "proposal": proposal.model_dump(mode="json"),
            "external_agent_executed": True,
            "ttl_seconds": 300,
            "freshness_basis": "original_camera_captured_at_at_admission",
        }
        if any(advisory.get(k) != v for k, v in expected.items()):
            raise ValueError("completion_advisory_binding_mismatch")
        admitted = datetime.fromisoformat(advisory["admitted_at"])
        if admitted.tzinfo is None:
            raise ValueError("completion_invalid_admission_time")
        allowed_evidence = {
            snapshot.scene_ir.sha256,
            observed.physics_report.sha256,
            observation.runtime_scene.sha256,
            replay.receipt.sha256,
        }
        for frame in observation.frames:
            matching = []
            for profile in replay.profiles:
                files = {f.path: f.artifact for f in profile.files}
                if frame.media_ref in files.values():
                    media = read(frame.media_ref)
                    row = media["frames"][frame.frame_index]
                    matching.append(
                        files.get(row["path"]) == frame.image
                        and row["png_sha256"] == frame.image.sha256
                        and row["captured_at"] == frame.captured_at
                    )
            captured = datetime.fromisoformat(frame.captured_at)
            if (
                not matching
                or not all(matching)
                or captured.tzinfo is None
                or not 0 <= (admitted - captured).total_seconds() <= 300
            ):
                raise ValueError("completion_stale_or_unbound_observation")
            allowed_evidence.update((frame.image.sha256, frame.media_ref.sha256))
        if not set(proposal.evidence_sha256) <= allowed_evidence:
            raise ValueError("completion_unknown_visual_evidence")
        # Audit original managed process records without spawning or refreshing observation.
        model_records = [
            read(ArtifactRef.model_validate(ref))
            for ref in advisory.get("evidence", [])
            if ref.get("media_type") == "application/json"
        ]
        processes = [
            r
            for r in model_records
            if isinstance(r, dict)
            and "start_ticks" in r
            and r.get("executable_sha256") == advisory.get("executable_sha256")
        ]
        if (
            not any(
                p.get("pid") == p.get("pgid")
                and any(
                    t.get("pid") == p["pid"]
                    and t.get("returncode") == 0
                    and t.get("reaped") is True
                    and t.get("failure") is None
                    for t in model_records
                    if isinstance(t, dict)
                )
                for p in processes
            )
            or proposal.model_dump(mode="json") not in model_records
        ):
            raise ValueError("completion_missing_model_execution")
        registry = AssetRegistry(store)
        asset_root = root / "verification-assets"
        for asset in compiled.resolved_assets.assets:
            version = registry.inspect(asset.version_sha256)
            for member in version.files:
                target = (
                    asset_root / "assets" / asset.entity_id / version.version_sha256 / member.path
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(store.read_artifact(member.artifact))
        profiles = {}
        for profile in replay.profiles:
            profile_root = root / "verification-profiles" / profile.profile
            members = []
            seen = set()
            for item in profile.files:
                path = Path(item.path)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in item.path
                    or item.path in seen
                ):
                    raise ValueError("completion_unsafe_replay_member")
                seen.add(item.path)
                target = profile_root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(store.read_artifact(item.artifact))
                members.append(
                    {
                        "path": item.path,
                        "sha256": item.artifact.sha256,
                        "size_bytes": item.artifact.size_bytes,
                    }
                )
            profiles[profile.profile] = {"root": str(profile_root), "files": members}
        assessed = assess_scene(
            runtime,
            package_root=asset_root,
            scene_ir_bytes=store.read_artifact(snapshot.scene_ir),
            input_sha256=scene.input_sha256,
            profiles=profiles,
            visual_status="passed",
        )
        assessment_ref = store.write_artifact(
            json.dumps(assessed, sort_keys=True).encode(), "application/json"
        )
        if (
            assessed.get("physical_status") != "passed"
            or assessed.get("execution_evidence_bound") is not True
            or assessed.get("checks") != physical.get("checks")
        ):
            raise ValueError("completion_physical_reassessment_failed")
        build_package(
            compiled,
            registry=registry,
            store=store,
            input_refs={"input_bundle": snapshot.input_bundle},
            replay_refs={"replay_result": snapshot.replay_result},
            assessment_ref=assessment_ref,
            output=root / "environment",
            evidence_refs=refs,
        )
        manifest_ref = store.write_artifact(
            (root / "environment" / "manifest.json").read_bytes(), "application/json"
        )
        if store.status(snapshot.workflow_id) != snapshot:
            raise ValueError("stale_completion_snapshot")
        package_path = str(root / "environment")
        status = "materialized"
    except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
        error = str(exc)
    receipt = {
        "schema_version": "x2env.completion.v1",
        "workflow_id": snapshot.workflow_id,
        "revision": snapshot.revision,
        "status": status,
        "error_code": error,
        "copy_run": "not_run",
        "release_qualified": False,
        "sim_ready": False,
        "physical_profile": "passed" if status == "materialized" else "not_run",
        "visual_intent": "passed" if status == "materialized" else "not_run",
        "package_path": package_path,
        "manifest": manifest_ref.model_dump() if manifest_ref else None,
        "assessment": assessment_ref.model_dump() if assessment_ref else None,
        "evidence": {
            name: getattr(snapshot, name).model_dump()
            for name in (
                "input_bundle",
                "scene_ir",
                "compiled_scene",
                "replay_result",
                "observation",
                "diagnosis",
                "validation",
                "grounding",
            )
            if getattr(snapshot, name) is not None
        },
        "wall_seconds": time.monotonic() - started,
        "revisions": [ref.model_dump() for ref in snapshot.revisions],
        "robot_policy_evaluated": False,
        "data_collection_evaluated": False,
    }
    raw = json.dumps(receipt, sort_keys=True).encode()
    (root / "completion.json").write_bytes(raw)
    return CompletionResult(
        status=status,
        package_path=package_path,
        manifest=manifest_ref,
        receipt=store.write_artifact(raw, "application/json"),
        error_code=error,
    )


def _audit_repair_reservations(snapshot, store):
    """Historical journal authority; never require a live owner or re-approve a repair."""
    from .revision import RevisionReservationEnvelope

    refs, spent, fingerprints, base_revision = [], 0, set(), -1
    successful = []
    for index, op in enumerate(snapshot.operations):
        reservation = op.repair_reservation
        if (
            op.capability in {"revise", "asset.revise"}
            and op.status == "succeeded"
            and reservation is None
        ):
            raise ValueError("completion_repair_reservation_missing")
        if reservation is None:
            continue
        if op.capability not in {"revise", "asset.revise"} or op.status == "running":
            raise ValueError("completion_repair_reservation_operation_mismatch")
        spent += reservation.cost
        if (
            spent > 2
            or reservation.failure_fingerprint in fingerprints
            # Dead-owner recovery can finish an operation without incrementing workflow revision.
            or not base_revision <= reservation.base_revision < snapshot.revision
        ):
            raise ValueError("completion_repair_reservation_budget_mismatch")
        fingerprints.add(reservation.failure_fingerprint)
        base_revision = reservation.base_revision
        if op.capability == "asset.revise":
            refs.extend(_audit_local_color(snapshot, store, index, op))
            continue
        approval = json.loads(store.read_artifact(reservation.approval))
        scene_ref = ArtifactRef.model_validate(approval["scene_ir"])
        diagnosis_ref = ArtifactRef.model_validate(approval["diagnosis"])
        expected = {
            "authority": "harness_controller",
            "approved": True,
            "workflow_id": snapshot.workflow_id,
            "base_revision": reservation.base_revision,
            "input_bundle": snapshot.input_bundle.model_dump(),
            "scene_ir": scene_ref.model_dump(),
            "diagnosis": diagnosis_ref.model_dump(),
            "cost": reservation.cost,
            "failure_fingerprint": reservation.failure_fingerprint,
        }
        if (
            approval != expected
            or approval.get("approved") is not True
            or type(approval.get("base_revision")) is not int
            or type(approval.get("cost")) is not int
        ):
            raise ValueError("completion_repair_approval_mismatch")
        before = snapshot.operations[:index]
        for reference in (scene_ref, diagnosis_ref):
            if not any(
                prior.status == "succeeded" and prior.result and reference in prior.result.outputs
                for prior in before
            ):
                raise ValueError("completion_repair_approval_source_not_committed")
        refs.append(reservation.approval)
        if op.status != "succeeded":
            continue
        rows = [
            (ref, json.loads(store.read_artifact(ref)))
            for ref in snapshot.revisions
            if op.result and ref in op.result.outputs
        ]
        if len(rows) != 1:
            raise ValueError("completion_repair_receipt_not_committed")
        revision_ref, row = rows[0]
        if "reservation" not in row:
            raise ValueError("completion_repair_reservation_missing")
        envelope_ref = ArtifactRef.model_validate(row["reservation"])
        envelope = RevisionReservationEnvelope.model_validate_json(
            store.read_artifact(envelope_ref)
        )
        diagnosis = DiagnosisResult.model_validate_json(store.read_artifact(diagnosis_ref))
        proposal = diagnosis.proposal
        if proposal is None:
            raise ValueError("completion_repair_reservation_proposal_missing")
        kind = (
            "scene_asset"
            if proposal.scene_patches and proposal.asset_patches
            else "scene"
            if proposal.scene_patches
            else "asset"
        )
        cost = int(bool(proposal.scene_patches)) + len(proposal.asset_patches)
        if (
            envelope.workflow_id != snapshot.workflow_id
            or envelope.operation_id != op.operation_id
            or envelope.reservation != reservation
            or envelope.input_bundle != snapshot.input_bundle
            or envelope.scene_ir != scene_ref
            or envelope.diagnosis != diagnosis_ref
            or row.get("base_scene") != scene_ref.model_dump()
            or row.get("diagnosis") != diagnosis_ref.model_dump()
            or row.get("cost") != reservation.cost
            or type(row.get("cost")) is not int
            or row.get("failure_fingerprint") != reservation.failure_fingerprint
            or reservation.kind != kind
            or reservation.cost != cost
        ):
            raise ValueError("completion_repair_reservation_binding_mismatch")
        refs.extend((revision_ref, envelope_ref))
        successful.append(revision_ref)
    if tuple(successful) != snapshot.revisions:
        raise ValueError("completion_repair_revision_history_mismatch")
    if fingerprints:
        refs.append(
            store.write_artifact(
                json.dumps(
                    {
                        "schema_version": "x2env.repair_budget_audit.v1",
                        "workflow_id": snapshot.workflow_id,
                        "workflow_revision": snapshot.revision,
                        "input_bundle": snapshot.input_bundle.model_dump(),
                        "basis": "historical_store_snapshot_not_live_reauthorization",
                        "reserved_cost": spent,
                        "budget_limit": 2,
                        "operations": [
                            op.model_dump(mode="json")
                            for op in snapshot.operations
                            if op.repair_reservation is not None
                        ],
                    },
                    sort_keys=True,
                ).encode(),
                "application/json",
            )
        )
    return tuple(dict.fromkeys(refs))


def _audit_local_color(snapshot, store, index, operation):
    """Consume the existing historical color verifier, then bind the committed continuation."""
    from .local_color_execution import (
        ColorRepairExecutionResult,
        verify_color_repair_approval,
        verify_color_repair_result,
    )
    from .resolver import ResolutionResult

    approval = json.loads(store.read_artifact(operation.repair_reservation.approval))
    if not {"candidate", "proposal"} <= approval.keys():
        raise ValueError("completion_asset_repair_not_supported")
    candidate, _, reservation = verify_color_repair_approval(
        store, workflow_id=snapshot.workflow_id, operation_id=operation.operation_id
    )
    if not any(
        op.capability == "codex.interpret"
        and op.status == "succeeded"
        and op.result
        and candidate.scene_ir in op.result.outputs
        for op in snapshot.operations[:index]
    ):
        raise ValueError("completion_color_initial_scene_not_committed")
    refs = [reservation.approval]
    if operation.result:
        refs.extend(operation.result.outputs)  # Preserve failed/cancelled partial artifacts too.
    if operation.status != "succeeded":
        return refs
    executions = []
    for ref in operation.result.outputs:
        if ref.media_type != "application/json":
            continue
        body = json.loads(store.read_artifact(ref))
        if isinstance(body, dict) and {"receipt", "resolved", "child"} <= body.keys():
            ColorRepairExecutionResult.model_validate_json(store.read_artifact(ref))
            checked_candidate, result = verify_color_repair_result(
                store, ref, workflow_id=snapshot.workflow_id
            )
            if checked_candidate != candidate:
                raise ValueError("completion_color_candidate_mismatch")
            executions.append((ref, result))
    if len(executions) != 1:
        raise ValueError("completion_color_execution_not_committed")
    execution_ref, execution = executions[0]
    continuations = []
    for continuation_index, op in enumerate(snapshot.operations[index + 1 :], start=index + 1):
        if op.capability != "asset.resolve" or op.status != "succeeded" or not op.result:
            continue
        for ref in op.result.outputs:
            if ref.media_type != "application/json":
                continue
            body = json.loads(store.read_artifact(ref))
            if (
                not isinstance(body, dict)
                or not {"receipt", "resolved", "pending_color_repairs"} <= body.keys()
            ):
                continue
            resolution = ResolutionResult.model_validate_json(store.read_artifact(ref))
            receipt = json.loads(store.read_artifact(resolution.receipt))
            if receipt.get("schema_version") != "x2env.local_color_continuation.v1":
                continue
            repair_refs = tuple(ArtifactRef.model_validate(r) for r in receipt["repair_results"])
            if execution_ref not in repair_refs:
                continue
            if ref != snapshot.asset_resolution:
                raise ValueError("completion_color_continuation_not_current")
            if (
                resolution.receipt not in op.result.outputs
                or resolution.status != "succeeded"
                or resolution.error_code is not None
                or resolution.pending_color_repairs
                or receipt.get("status") != "succeeded"
                or receipt.get("error_code") is not None
                or receipt.get("workflow_id") != snapshot.workflow_id
                or receipt.get("operation_id") != op.operation_id
                or receipt.get("resolved") != resolution.resolved.model_dump(mode="json")
                or resolution.resolved.scene_ir != candidate.scene_ir
                or execution.resolved not in resolution.resolved.assets
                or len(set(repair_refs)) != len(repair_refs)
            ):
                raise ValueError("completion_color_continuation_mismatch")
            original_ref = ArtifactRef.model_validate(receipt["original_resolution"])
            original = ResolutionResult.model_validate_json(store.read_artifact(original_ref))
            origins = [
                prior
                for prior in snapshot.operations[:index]
                if prior.capability == "asset.resolve"
                and prior.status == "blocked"
                and prior.result
                and original_ref in prior.result.outputs
                and original.receipt in prior.result.outputs
            ]
            if (
                len(origins) != 1
                or original.error_code != "local_color_repair_pending"
                or candidate not in original.pending_color_repairs
                or original.resolved.scene_ir != candidate.scene_ir
                or any(
                    asset not in resolution.resolved.assets for asset in original.resolved.assets
                )
                or len({asset.entity_id for asset in resolution.resolved.assets})
                != len(resolution.resolved.assets)
            ):
                raise ValueError("completion_color_original_not_committed")
            repaired = []
            for repair_ref in repair_refs:
                other, result = verify_color_repair_result(
                    store, repair_ref, workflow_id=snapshot.workflow_id
                )
                if (
                    other not in original.pending_color_repairs
                    or result.resolved not in resolution.resolved.assets
                ):
                    raise ValueError("completion_color_continuation_mismatch")
                repaired.append(other)
                if not any(
                    prior.capability == "asset.revise"
                    and prior.status == "succeeded"
                    and prior.result
                    and repair_ref in prior.result.outputs
                    for prior in snapshot.operations[:continuation_index]
                ):
                    raise ValueError("completion_color_execution_order_mismatch")
            if len(repaired) != len(original.pending_color_repairs) or set(
                p.entity_id for p in repaired
            ) != set(p.entity_id for p in original.pending_color_repairs):
                raise ValueError("completion_color_continuation_incomplete")
            continuations.append((continuation_index, resolution.resolved))
            refs.extend((ref, resolution.receipt, original_ref, *repair_refs))
    if len(continuations) != 1:
        raise ValueError("completion_color_continuation_not_committed")
    compiled = CompiledScene.model_validate_json(store.read_artifact(snapshot.compiled_scene))
    if execution.resolved not in compiled.resolved_assets.assets:
        raise ValueError("completion_color_compiled_child_mismatch")
    if not any(
        op.capability == "x2env.compile"
        and op.status == "succeeded"
        and op.result
        and snapshot.compiled_scene in op.result.outputs
        for op in snapshot.operations[continuations[0][0] + 1 :]
    ):
        raise ValueError("completion_color_compile_order_mismatch")
    return refs


def _verify_grounding(snapshot, store, scene, compiled):
    """Verify the original grounding node, then the committed layout revision chain."""
    ground_ops = [
        (i, op)
        for i, op in enumerate(snapshot.operations)
        if op.capability == "codex.ground"
        and op.status == "succeeded"
        and op.result
        and snapshot.grounding in op.result.outputs
    ]
    if len(ground_ops) != 1:
        raise ValueError("completion_grounding_not_committed")
    index, operation = ground_ops[0]
    receipt = json.loads(store.read_artifact(snapshot.grounding))
    accepted = SceneIR.model_validate_json(json.dumps(receipt["proposed_scene"]))
    documents = [(ref, json.loads(store.read_artifact(ref))) for ref in operation.result.outputs]
    scenes = [ref for ref, body in documents if body == accepted.model_dump(mode="json")]
    if len(scenes) != 1:
        raise ValueError("completion_grounding_revision_chain_not_verified")
    scene_ref = scenes[0]
    assets = [
        (ref, ResolvedAssetSet.model_validate_json(json.dumps(body)))
        for ref, body in documents
        if isinstance(body, dict)
        and body.get("scene_ir") == scene_ref.model_dump()
        and "assets" in body
    ]
    if len(assets) != 1:
        raise ValueError("completion_grounding_asset_binding_mismatch")
    assets_ref, current_assets = assets[0]
    _verify_grounding_origin(
        snapshot.model_copy(update={"scene_ir": scene_ref, "resolved_assets": assets_ref}),
        store,
        accepted,
        compiled.model_copy(update={"resolved_assets": current_assets}),
    )
    previous_index, spent, fingerprints, prefix = index, 0, set(), []
    for revision_ref in snapshot.revisions:
        row = json.loads(store.read_artifact(revision_ref))
        positions = [
            (i, op)
            for i, op in enumerate(snapshot.operations)
            if op.capability == "revise"
            and op.status == "succeeded"
            and op.result
            and revision_ref in op.result.outputs
        ]
        if len(positions) != 1 or positions[0][0] <= previous_index:
            raise ValueError("completion_grounding_revision_journal_mismatch")
        next_index, op = positions[0]
        next_ref = ArtifactRef.model_validate(row["scene_ir"])
        if (
            row.get("schema_version") != "x2env.revision.v1"
            or row.get("base_scene") != scene_ref.model_dump()
            or row.get("input_sha256") != scene.input_sha256
            or row.get("history") != [r.model_dump() for r in prefix]
            or next_ref not in op.result.outputs
        ):
            raise ValueError("completion_grounding_revision_chain_mismatch")
        diagnosis_ref = ArtifactRef.model_validate(row["diagnosis"])
        if not any(
            o.capability == "codex.diagnose"
            and o.status == "succeeded"
            and o.result
            and diagnosis_ref in o.result.outputs
            for o in snapshot.operations[previous_index + 1 : next_index]
        ):
            raise ValueError("completion_grounding_revision_diagnosis_not_committed")
        diagnosis = DiagnosisResult.model_validate_json(store.read_artifact(diagnosis_ref))
        proposal = diagnosis.proposal
        advisory = json.loads(store.read_artifact(diagnosis.receipt))
        if (
            diagnosis.status != "completed"
            or proposal is None
            or proposal.base_revision != accepted.revision
            or advisory.get("status") != "completed"
            or advisory.get("authority") != "advisory_only"
            or advisory.get("scene_ir") != scene_ref.model_dump()
            or advisory.get("proposal") != proposal.model_dump(mode="json")
        ):
            raise ValueError("completion_grounding_revision_diagnosis_mismatch")
        model_records = [
            json.loads(store.read_artifact(ArtifactRef.model_validate(r)))
            for r in advisory.get("evidence", [])
            if r.get("media_type") == "application/json"
        ]
        ground_records = [
            json.loads(store.read_artifact(ArtifactRef.model_validate(r)))
            for r in receipt["evidence"]
            if r.get("media_type") == "application/json"
        ]
        if (
            advisory.get("executable_sha256")
            not in (_execution_shas(model_records) & _execution_shas(ground_records))
            or proposal.model_dump(mode="json") not in model_records
        ):
            raise ValueError("completion_grounding_revision_model_mismatch")
        if proposal.asset_patches or row.get("asset_receipts") != []:
            raise ValueError("completion_grounding_asset_revision_not_verified")
        _verify_revision_observation(
            store,
            snapshot.operations[previous_index + 1 : next_index],
            diagnosis_ref,
            advisory,
            proposal,
            scene_ref,
            scene.input_sha256,
            row,
        )
        cost = int(bool(proposal.scene_patches))
        fingerprint = row.get("failure_fingerprint")
        if (
            type(row.get("cost")) is not int
            or row["cost"] != cost
            or cost != 1
            or spent + cost > 2
            or not isinstance(fingerprint, str)
            or len(fingerprint) != 64
            or any(c not in "0123456789abcdef" for c in fingerprint)
            or fingerprint in fingerprints
        ):
            raise ValueError("completion_grounding_revision_budget_mismatch")
        document = accepted.model_dump(mode="json")
        entities = {e["id"]: e for e in document["entities"]}
        seen = set()
        for patch in proposal.scene_patches:
            if patch.entity_id in seen or patch.entity_id not in entities or patch.joints:
                raise ValueError("completion_grounding_revision_patch_mismatch")
            seen.add(patch.entity_id)
            entity = entities[patch.entity_id]
            if patch.pose is not None:
                if patch.pose.frame != entity["pose"]["frame"]:
                    raise ValueError("completion_grounding_revision_patch_mismatch")
                entity["pose"]["position"] = [
                    old if new is None else new
                    for old, new in zip(
                        entity["pose"]["position"], patch.pose.position, strict=True
                    )
                ]
                if patch.pose.yaw_degrees is not None:
                    entity["pose"]["yaw_degrees"] = patch.pose.yaw_degrees
        if document == accepted.model_dump(mode="json"):
            raise ValueError("completion_grounding_revision_no_effect")
        for entity_id in seen:
            for provenance in entities[entity_id]["provenance"]["pose"]:
                provenance["kind"] = "override"
        document["revision"] += 1
        following = SceneIR.model_validate_json(store.read_artifact(next_ref))
        next_assets = ResolvedAssetSet.model_validate_json(json.dumps(row["resolved_assets"]))
        if (
            following.model_dump(mode="json") != document
            or next_assets.scene_ir != next_ref
            or next_assets.assets != current_assets.assets
            or not any(
                json.loads(store.read_artifact(ref)) == next_assets.model_dump(mode="json")
                for ref in op.result.outputs
            )
        ):
            raise ValueError("completion_grounding_revision_output_mismatch")
        accepted, scene_ref, current_assets = following, next_ref, next_assets
        previous_index, spent = next_index, spent + cost
        fingerprints.add(fingerprint)
        prefix.append(revision_ref)
    committed = [
        op
        for op in snapshot.operations[index + 1 :]
        if op.capability == "revise" and op.status == "succeeded"
    ]
    if (
        len(committed) != len(prefix)
        or scene_ref != snapshot.scene_ir
        or accepted != scene
        or current_assets != compiled.resolved_assets
        or current_assets
        != ResolvedAssetSet.model_validate_json(store.read_artifact(snapshot.resolved_assets))
    ):
        raise ValueError("completion_grounding_revision_chain_not_verified")


def _verify_grounding_origin(snapshot, store, scene, compiled):
    """Audit the accepted design source and its original managed execution, never rerun it."""
    from .grounding import GroundingValues, SceneDesignPolicy

    if snapshot.proposal is None or snapshot.resolved_assets is None:
        raise ValueError("completion_grounding_missing_original_binding")

    def read(ref):
        return json.loads(store.read_artifact(ref))

    receipt = read(snapshot.grounding)
    if receipt.get("proposed_scene", {}).get("revision") != scene.revision:
        raise ValueError("completion_grounding_revision_chain_not_verified")
    expected = {
        "schema_version": "x2env.scene_grounding.v1",
        "status": "completed",
        "error_code": None,
        "bundle_ref": snapshot.input_bundle.model_dump(),
        "proposal_ref": snapshot.proposal.model_dump(),
        "proposed_scene": scene.model_dump(mode="json"),
        "real_world_scale_recovered": False,
        "authority": "advisory_design_only",
    }
    if any(receipt.get(k) != v for k, v in expected.items()):
        raise ValueError("completion_grounding_binding_mismatch")
    policy = SceneDesignPolicy.model_validate_json(json.dumps(receipt["policy"]))
    if not policy.enabled:
        raise ValueError("completion_grounding_policy_disabled")
    original = BackendProposal.model_validate_json(store.read_artifact(snapshot.proposal))
    if original.status != "completed" or original.proposal.scene is None:
        raise ValueError("completion_grounding_original_proposal_missing")
    base = original.proposal.scene
    assets_ref = ArtifactRef.model_validate(receipt["assets_ref"])
    assets = ResolvedAssetSet.model_validate_json(store.read_artifact(assets_ref))
    rebound = ResolvedAssetSet.model_validate_json(store.read_artifact(snapshot.resolved_assets))
    if (
        SceneIR.model_validate_json(store.read_artifact(assets.scene_ir)) != base
        or assets.assets != rebound.assets
        or rebound.scene_ir != snapshot.scene_ir
        or rebound != compiled.resolved_assets
        or base.input_sha256 != scene.input_sha256
        or base.revision != scene.revision
    ):
        raise ValueError("completion_grounding_asset_binding_mismatch")
    ground_ops = [
        (i, op)
        for i, op in enumerate(snapshot.operations)
        if op.capability == "codex.ground"
        and op.status == "succeeded"
        and op.result
        and all(
            ref in op.result.outputs
            for ref in (snapshot.grounding, snapshot.scene_ir, snapshot.resolved_assets)
        )
    ]
    if len(ground_ops) != 1:
        raise ValueError("completion_grounding_not_committed")
    before = snapshot.operations[: ground_ops[0][0]]
    for capability, ref in [
        ("codex.interpret", snapshot.proposal),
        ("codex.interpret", assets.scene_ir),
        ("asset.resolve", assets_ref),
    ]:
        if not any(
            op.capability == capability
            and op.status == "succeeded"
            and op.result
            and ref in op.result.outputs
            for op in before
        ):
            raise ValueError("completion_grounding_missing_original_journal")
    unknowns = original.proposal.unknowns
    indices = receipt.get("resolved_unknowns")
    new_design = "design_plan" in receipt
    plan = None
    fixed_values = {}
    structural_policy = None
    if new_design:
        from .compile import StructuralPolicy
        from .design_plan import classify_design_unknowns

        if receipt.get("structural_policy") is not None:
            structural_policy = StructuralPolicy.model_validate_json(
                json.dumps(receipt["structural_policy"])
            )
        if policy.structural_defaults_enabled and structural_policy != compiled.policy:
            raise ValueError("completion_grounding_structural_policy_mismatch")
        try:
            plan = classify_design_unknowns(original.proposal, policy, structural_policy)
        except ValueError as exc:
            raise ValueError("completion_grounding_invalid_design_plan") from exc
        if receipt["design_plan"] != plan.model_dump(mode="json") or indices != list(
            plan.resolved_unknown_indices
        ):
            raise ValueError("completion_grounding_design_plan_mismatch")
        version = AssetRegistry(store).inspect(assets.assets[0].version_sha256)
        metrics = read(version.normalization_report)
        dims = metrics.get("dimensions_m")
        import math

        if (
            not isinstance(dims, list)
            or len(dims) != 3
            or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in dims)
        ):
            raise ValueError("completion_grounding_missing_measured_geometry")
        for rule in plan.rules:
            value = rule.value
            if rule.basis == "on_geometry_derived":
                value = dims[2] / 2
            if rule.basis == "asset_anchor":
                value = dims[int(rule.path[-2])]
            if value is not None:
                fixed_values[rule.entity_id + "." + rule.path] = value
        if receipt.get("fixed_values") != fixed_values:
            raise ValueError("completion_grounding_fixed_values_mismatch")
        anchor = next(e for e in base.entities if e.role == "foreground")
        support = next(e for e in base.entities if e.role == "structural_support")
        if anchor.pose.frame == support.id and anchor.pose.position[2] is not None:
            if not math.isclose(anchor.pose.position[2], dims[2] / 2, rel_tol=0, abs_tol=1e-9):
                raise ValueError("completion_grounding_known_height_conflict")
    elif policy.structural_defaults_enabled or receipt.get("structural_policy") is not None:
        raise ValueError("completion_grounding_legacy_default_authority_missing")
    if (
        receipt.get("original_unknowns") != [u.model_dump(mode="json") for u in unknowns]
        or not isinstance(indices, list)
        or (not indices and not new_design)
        or any(type(i) is not int or not 0 <= i < len(unknowns) for i in indices)
        or len(set(indices)) != len(indices)
        or (
            not new_design
            and any(
                unknowns[i].reason_kind not in {"scale_unobservable", "pose_unobservable"}
                for i in indices
            )
        )
        or any(u.critical and i not in indices for i, u in enumerate(unknowns))
    ):
        raise ValueError("completion_grounding_unknowns_mismatch")
    records = [
        read(ArtifactRef.model_validate(ref))
        for ref in receipt["evidence"]
        if ref.get("media_type") == "application/json"
    ]
    if new_design:
        contexts = [
            r
            for r in records
            if isinstance(r, dict) and "design_plan" in r and "original_proposal" in r
        ]
        if len(contexts) != 1:
            raise ValueError("completion_grounding_design_context_missing")
        context = contexts[0]
        expected_context = {
            "bundle_ref": snapshot.input_bundle.model_dump(),
            "original_proposal": original.proposal.model_dump(mode="json"),
            "policy": policy.model_dump(mode="json"),
            "structural_policy": structural_policy.model_dump(mode="json")
            if structural_policy
            else None,
            "design_plan": plan.model_dump(mode="json"),
            "fixed_values": fixed_values,
            "asset_version": version.model_dump(mode="json"),
            "anchor_dimensions_m": dims,
        }
        if any(context.get(k) != v for k, v in expected_context.items()):
            raise ValueError("completion_grounding_design_context_mismatch")
        bundle = InputBundle.model_validate_json(store.read_artifact(snapshot.input_bundle))
        media = context.get("media_selection")
        if media is None:
            if plan.requires_media or bundle.text is None or bundle.images or bundle.video:
                raise ValueError("completion_grounding_media_missing")
            store.read_artifact(bundle.text)
        else:
            image_ref = ArtifactRef.model_validate(media["image"])
            store.read_artifact(image_ref)
            selection = read(ArtifactRef.model_validate(media["provenance"]))
            if (
                selection.get("scene_ir") != assets.scene_ir.model_dump()
                or selection.get("entity_id") != anchor.id
            ):
                raise ValueError("completion_grounding_media_binding_mismatch")
            if selection.get("request_sha256") != bundle.request_sha256:
                raise ValueError("completion_grounding_media_binding_mismatch")
            if bundle.images:
                expected_media = {
                    "selection_basis": "first_canonical_input_image",
                    "image_index": 0,
                    "source_ref": bundle.images[0].source.model_dump(),
                }
                if image_ref != bundle.images[0].canonical or any(
                    selection.get(k) != v for k, v in expected_media.items()
                ):
                    raise ValueError("completion_grounding_media_binding_mismatch")
            elif bundle.video:
                from io import BytesIO

                from PIL import Image

                sequence = read(bundle.video.sequence)
                frames = sequence.get("frames", [])
                if (
                    sequence.get("source") != bundle.video.source.model_dump()
                    or sequence.get("full_decode") is not True
                    or len(frames) != bundle.video.frame_count
                    or not frames
                    or frames[0].get("index") != 0
                ):
                    raise ValueError("completion_grounding_video_sequence_mismatch")
                with Image.open(BytesIO(store.read_artifact(image_ref))) as png:
                    if png.size != (bundle.video.width, bundle.video.height):
                        raise ValueError("completion_grounding_video_pixels_mismatch")
                    pixels = png.convert("RGB").tobytes()
                expected_media = {
                    "selection_basis": "first_verified_decoded_video_frame",
                    "frame_index": 0,
                    "frame_sha256": hashlib.sha256(pixels).hexdigest(),
                    "sequence_ref": bundle.video.sequence.model_dump(),
                    "source_ref": bundle.video.source.model_dump(),
                    "full_frame_count": bundle.video.frame_count,
                }
                if (
                    len(pixels) != frames[0].get("size_bytes")
                    or hashlib.sha256(pixels).hexdigest() != frames[0].get("sha256")
                    or any(selection.get(k) != v for k, v in expected_media.items())
                ):
                    raise ValueError("completion_grounding_video_pixels_mismatch")
            else:
                raise ValueError("completion_grounding_fabricated_media")

    original_records = [
        read(ref) for ref in original.evidence if ref.media_type == "application/json"
    ]
    if not (_execution_shas(records) & _execution_shas(original_records)):
        raise ValueError("completion_grounding_missing_model_execution")
    values = [
        GroundingValues.model_validate_json(json.dumps(r))
        for r in records
        if isinstance(r, dict) and set(r) == {"entities"}
    ]
    if len(values) != 1 or {v.id for v in values[0].entities} != {e.id for e in scene.entities}:
        raise ValueError("completion_grounding_model_output_mismatch")
    by_id = {e.id: e for e in base.entities}
    if set(by_id) != {e.id for e in scene.entities} or scene.relations != base.relations:
        raise ValueError("completion_grounding_changed_semantics")
    for entity in scene.entities:
        old = by_id[entity.id]
        value = next(v for v in values[0].entities if v.id == entity.id)
        if new_design:
            actual = {
                **{f"dimensions[{i}]": v for i, v in enumerate(value.dimensions)},
                **{f"pose.position[{i}]": v for i, v in enumerate(value.position)},
                "pose.yaw_degrees": value.yaw_degrees,
            }
            if any(
                entity.id + "." + k in fixed_values and fixed_values[entity.id + "." + k] != v
                for k, v in actual.items()
            ):
                raise ValueError("completion_grounding_changed_fixed_value")
            if entity.id == anchor.id and any(
                not math.isclose(a, b, abs_tol=1e-9, rel_tol=0)
                for a, b in zip(value.dimensions, dims, strict=True)
            ):
                raise ValueError("completion_grounding_changed_anchor_geometry")
        if (
            value.dimensions != entity.dimensions
            or value.position != entity.pose.position
            or value.frame != entity.pose.frame
            or value.yaw_degrees != entity.pose.yaw_degrees
        ):
            raise ValueError("completion_grounding_model_output_mismatch")
        if any(
            getattr(entity, k) != getattr(old, k)
            for k in ("category", "role", "color", "material", "articulation_state")
        ):
            raise ValueError("completion_grounding_changed_semantics")
        for before_values, after_values in (
            (old.dimensions or (None, None, None), entity.dimensions),
            (old.pose.position, entity.pose.position),
        ):
            if any(
                a is not None and a != b for a, b in zip(before_values, after_values, strict=True)
            ):
                raise ValueError("completion_grounding_changed_explicit_axis")
        if old.pose.frame != entity.pose.frame or (
            old.pose.yaw_degrees is not None and old.pose.yaw_degrees != entity.pose.yaw_degrees
        ):
            raise ValueError("completion_grounding_changed_explicit_axis")


def _execution_shas(records):
    """Historical successful session identities, not a live deployment qualification."""
    return {
        p["executable_sha256"]
        for p in records
        if isinstance(p, dict)
        and all(type(p.get(k)) is int and p[k] > 0 for k in ("pid", "pgid", "start_ticks"))
        and p["pid"] == p["pgid"]
        and isinstance(p.get("executable_sha256"), str)
        and len(p["executable_sha256"]) == 64
        and all(c in "0123456789abcdef" for c in p["executable_sha256"])
        and any(
            isinstance(t, dict)
            and type(t.get("pid")) is int
            and t["pid"] == p["pid"]
            and t.get("reaped") is True
            and type(t.get("returncode")) is int
            and t["returncode"] == 0
            and "failure" in t
            and t["failure"] is None
            for t in records
        )
    }


def _verify_revision_observation(
    store, operations, diagnosis_ref, advisory, proposal, scene_ref, input_sha256, revision
):
    """Bind historical repair inputs without recapturing or refreshing their timestamps."""
    diagnoses = [
        i
        for i, op in enumerate(operations)
        if op.capability == "codex.diagnose"
        and op.status == "succeeded"
        and op.result
        and diagnosis_ref in op.result.outputs
    ]
    if len(diagnoses) != 1:
        raise ValueError("completion_grounding_revision_diagnosis_mismatch")
    observations = []
    for op in operations[: diagnoses[0]]:
        if op.capability == "observe" and op.status == "succeeded" and op.result:
            for ref in op.result.outputs:
                body = json.loads(store.read_artifact(ref))
                if isinstance(body, dict) and set(body) == {
                    "observation",
                    "physics_report",
                    "receipt",
                }:
                    observations.append(ObservationResult.model_validate_json(json.dumps(body)))
    if not observations:
        raise ValueError("completion_grounding_revision_observation_missing")
    observed = observations[-1]
    observation = observed.observation
    report = json.loads(store.read_artifact(observed.physics_report))
    runtime = json.loads(store.read_artifact(observation.runtime_scene))
    digest = hashlib.sha256(
        json.dumps(runtime, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if (
        observation.scene_ir != scene_ref
        or runtime.get("scene_ir_sha256") != scene_ref.sha256
        or advisory.get("observation") != observation.model_dump(mode="json")
        or advisory.get("physical_report") != observed.physics_report.model_dump()
        or report.get("execution_evidence_bound") is not True
        or report.get("input_sha256") != input_sha256
        or report.get("scene_sha256") != digest
    ):
        raise ValueError("completion_grounding_revision_observation_mismatch")
    allowed = {
        scene_ref.sha256,
        observed.physics_report.sha256,
        observation.runtime_scene.sha256,
        observation.replay_receipt.sha256,
    }
    admitted = datetime.fromisoformat(advisory["admitted_at"])
    for frame in observation.frames:
        captured = datetime.fromisoformat(frame.captured_at)
        media = json.loads(store.read_artifact(frame.media_ref))
        entry = media["frames"][frame.frame_index]
        if (
            admitted.tzinfo is None
            or captured.tzinfo is None
            or not 0 <= (admitted - captured).total_seconds() <= 300
            or entry["png_sha256"] != frame.image.sha256
            or entry["captured_at"] != frame.captured_at
        ):
            raise ValueError("completion_grounding_revision_observation_mismatch")
        store.read_artifact(frame.image)
        allowed.update((frame.image.sha256, frame.media_ref.sha256))
    expected = hashlib.sha256(
        json.dumps(
            {
                "input": report["input_sha256"],
                "error_code": report.get("error_code"),
                "checks": [c for c in report.get("checks", []) if c.get("status") != "passed"],
                "images": [f.image.sha256 for f in observation.frames],
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    if (
        revision.get("failure_fingerprint") != expected
        or not set(proposal.evidence_sha256) <= allowed
    ):
        raise ValueError("completion_grounding_revision_failure_binding_mismatch")
