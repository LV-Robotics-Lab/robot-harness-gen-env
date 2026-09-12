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
from .compile import CompiledScene
from .contracts import ArtifactRef, InputBundle, Model, SceneIR, WorkflowSnapshot
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
        capabilities = {
            "input_bundle": {"ingest"},
            "scene_ir": {"codex.interpret", "revise"},
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
        artifact_closure(store, list(refs.values()))

        def read(ref):
            return json.loads(store.read_artifact(ref))

        scene = SceneIR.model_validate_json(store.read_artifact(snapshot.scene_ir))
        compiled = CompiledScene.model_validate_json(store.read_artifact(snapshot.compiled_scene))
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
            )
            if getattr(snapshot, name) is not None
        },
        "wall_seconds": time.monotonic() - started,
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
