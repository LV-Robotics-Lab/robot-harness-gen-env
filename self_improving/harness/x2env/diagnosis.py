"""Fresh evidence-bound advisory diagnosis; no workflow or physical authority."""

import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import Field, model_validator

from .assessment import ASSERTIONS_SHA256
from .asset_revision import AssetPatch
from .contracts import ArtifactRef, JointPosition, Model, Pose, SceneIR, Sha256
from .genesis_runtime import parse_runtime_scene


class ObservationFrame(Model):
    image: ArtifactRef
    media_ref: ArtifactRef
    frame_index: int = Field(ge=0)
    captured_at: str


class FreshObservation(Model):
    scene_ir: ArtifactRef
    runtime_scene: ArtifactRef
    replay_receipt: ArtifactRef
    frames: tuple[ObservationFrame, ...] = Field(min_length=1, max_length=8)


class ScenePatch(Model):
    """Null pose components mean no change, never erase an accepted axis."""

    entity_id: str
    pose: Pose | None
    joints: tuple[JointPosition, ...]

    @model_validator(mode="after")
    def nonempty(self):
        if self.pose is None and not self.joints:
            raise ValueError("empty_scene_patch")
        return self


class SuggestedAssetPatch(Model):
    entity_id: str
    parent_version: Sha256
    patch: AssetPatch


class DiagnosisProposal(Model):
    base_revision: int = Field(ge=0)
    visual_intent: Literal["passed", "failed", "uncertain"]
    reason: str = Field(min_length=1, max_length=8000)
    evidence_sha256: tuple[Sha256, ...] = Field(min_length=1, max_length=32)
    scene_patches: tuple[ScenePatch, ...] = Field(max_length=2)
    asset_patches: tuple[SuggestedAssetPatch, ...] = Field(max_length=2)

    @model_validator(mode="after")
    def budget(self):
        if len(self.scene_patches) + len(self.asset_patches) > 2:
            raise ValueError("proposal_patch_limit")
        return self


class DiagnosisResult(Model):
    status: Literal["completed", "failed", "blocked"]
    proposal: DiagnosisProposal | None
    receipt: ArtifactRef
    error_code: str | None = None


def assess_and_diagnose(backend, scene_ir, observation, physics_report, *, output_root, timeout):
    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise ValueError("invalid diagnosis timeout")
    root = Path(output_root)
    if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("diagnosis root must be absolute new nonsymbolic directory")
    root.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    status, error, proposal = "failed", None, None
    evidence, physical, admitted_at = [], {}, None

    def record(name, data, media_type="application/json"):
        (root / name).write_bytes(data)
        ref = backend.store.write_artifact(data, media_type)
        evidence.append(ref)
        return ref

    try:
        scene = SceneIR.model_validate_json(backend.store.read_artifact(scene_ir))
        observation = FreshObservation.model_validate_json(observation.model_dump_json())
        if observation.scene_ir != scene_ir:
            raise ValueError("observation_scene_mismatch")
        runtime = parse_runtime_scene(
            json.loads(backend.store.read_artifact(observation.runtime_scene))
        )
        if runtime.scene_ir_sha256 != scene_ir.sha256:
            raise ValueError("runtime_scene_mismatch")
        replay = json.loads(backend.store.read_artifact(observation.replay_receipt))
        if replay.get("scene") != runtime.model_dump(mode="json"):
            raise ValueError("replay_scene_mismatch")
        physical = json.loads(backend.store.read_artifact(physics_report))
        digest = hashlib.sha256(
            json.dumps(
                runtime.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        if (
            physical.get("schema_version") != "x2env.physical_assessment.v1"
            or physical.get("scene_ir") != scene_ir.model_dump(mode="json")
            or physical.get("runtime_scene") != observation.runtime_scene.model_dump(mode="json")
            or physical.get("replay_receipt") != observation.replay_receipt.model_dump(mode="json")
            or physical.get("scene_sha256") != digest
            or physical.get("input_sha256") != scene.input_sha256
            or physical.get("assertions_sha256") != ASSERTIONS_SHA256
        ):
            raise ValueError("unbound_physics_report")
        images, times = [], []
        for index, frame in enumerate(observation.frames):
            media = json.loads(backend.store.read_artifact(frame.media_ref))
            row = media["frames"][frame.frame_index]
            if row["captured_at"] != frame.captured_at or row["png_sha256"] != frame.image.sha256:
                raise ValueError("observation_camera_evidence_mismatch")
            if not any(
                any(
                    m["path"] == "media.json"
                    and m["artifact"] == frame.media_ref.model_dump(mode="json")
                    for m in profile["files"]
                )
                and any(
                    m["path"] == row["path"]
                    and m["artifact"] == frame.image.model_dump(mode="json")
                    for m in profile["files"]
                )
                for profile in replay["profiles"]
            ):
                raise ValueError("unbound_observation_frame")
            captured = datetime.fromisoformat(frame.captured_at.replace("Z", "+00:00"))
            if captured.tzinfo is None:
                raise ValueError("camera_time_requires_timezone")
            times.append(captured)
            data = backend.store.read_artifact(frame.image)
            with Image.open(BytesIO(data)) as image:
                if image.format != "PNG":
                    raise ValueError("observation_requires_png")
                image.verify()
            record(f"camera-{index}.png", data, "image/png")
            images.append(
                {"path": str(root / f"camera-{index}.png"), "input_sha256": frame.image.sha256}
            )
        admitted_at = datetime.now(timezone.utc)
        if any(not 0 <= (admitted_at - captured).total_seconds() <= 300 for captured in times):
            raise ValueError("stale_or_future_observation")
        if (
            not backend.executable.is_absolute()
            or hashlib.sha256(backend.executable.read_bytes()).hexdigest() != backend.executable_sha
        ):
            raise ValueError("executable_identity_mismatch")
        allowed = {
            scene_ir.sha256,
            physics_report.sha256,
            observation.runtime_scene.sha256,
            observation.replay_receipt.sha256,
            *[f.image.sha256 for f in observation.frames],
            *[f.media_ref.sha256 for f in observation.frames],
        }
        context = {
            "scene": scene.model_dump(mode="json"),
            "runtime": runtime.model_dump(mode="json"),
            "observation": observation.model_dump(mode="json"),
            "physical_report": physical,
            "allowed_evidence_sha256": sorted(allowed),
            "physics_report_ref": physics_report.model_dump(mode="json"),
        }
        record("context.json", json.dumps(context).encode())
        prompt = (
            "You are the Harness advisory visual diagnosis backend. Use no tools. Compare "
            "all entities, attributes, articulation and relations with the actual attached camera "
            "images and SceneIR. Separately report visual_intent; never replace "
            "programmatic physical "
            "checks or change thresholds. Return the static schema only. Bind base_revision and "
            "each parent_version to supplied evidence. Scene patches only pose/joints; null pose "
            "components mean unchanged. Asset patches use the supplied whitelist; unsupported "
            "execution remains controller authority. Do not claim qualification or Skill success. "
            "Suggest at most two patches total, do not execute them. Cite only allowed evidence "
            "SHA values. No issue means empty patches.\n" + json.dumps(context)
        )
        response = backend._invoke(root, prompt, images, DiagnosisProposal, record, timeout, start)
        proposal = DiagnosisProposal.model_validate_json(response)
        if proposal.base_revision != scene.revision or not set(proposal.evidence_sha256) <= allowed:
            raise ValueError("unbound_diagnosis_proposal")
        entities = {e.id for e in scene.entities}
        versions = {e.id: e.version_sha256 for e in runtime.entities if e.kind == "rigid"}
        for patch in proposal.scene_patches:
            if patch.entity_id not in entities:
                raise ValueError("unknown_patch_entity")
            if patch.pose is not None and patch.pose.frame not in {"world", *entities}:
                raise ValueError("unknown_patch_frame")
        for patch in proposal.asset_patches:
            if versions.get(patch.entity_id) != patch.parent_version:
                raise ValueError("unknown_asset_version")
        status = "completed"
    except FileNotFoundError as exc:
        status, error, proposal = "blocked", "blocked_external_resource", None
        record("error.json", json.dumps({"reason": str(exc)}).encode())
    except (
        ValueError,
        OSError,
        KeyError,
        IndexError,
        TypeError,
        subprocess.SubprocessError,
    ) as exc:
        known = {
            "observation_scene_mismatch",
            "runtime_scene_mismatch",
            "replay_scene_mismatch",
            "unbound_physics_report",
            "observation_camera_evidence_mismatch",
            "unbound_observation_frame",
            "camera_time_requires_timezone",
            "observation_requires_png",
            "stale_or_future_observation",
            "executable_identity_mismatch",
            "unbound_diagnosis_proposal",
            "unknown_patch_entity",
            "unknown_patch_frame",
            "unknown_asset_version",
            "model_timeout",
            "model_interrupted",
            "model_exit_failure",
            "model_incomplete_turn",
            "advisory_tool_violation",
        }
        error, proposal = str(exc) if str(exc) in known else "invalid_diagnosis_evidence", None
        record("error.json", json.dumps({"reason": str(exc)}).encode())
    receipt = record(
        "result.json",
        json.dumps(
            {
                "status": status,
                "error_code": error,
                "authority": "advisory_only",
                "physical_status": physical.get("physical_status", "not_run"),
                "physical_report": physics_report.model_dump(mode="json"),
                "scene_ir": scene_ir.model_dump(mode="json"),
                "observation": observation.model_dump(mode="json"),
                "model": backend.model,
                "executable_sha256": backend.executable_sha,
                "external_agent_executed": (root / "process.json").is_file(),
                "admitted_at": admitted_at.isoformat() if admitted_at else None,
                "ttl_seconds": 300,
                "freshness_basis": "original_camera_captured_at_at_admission",
                "proposal": proposal.model_dump(mode="json") if proposal else None,
                "evidence": [r.model_dump(mode="json") for r in evidence],
                "wall_seconds": time.monotonic() - start,
            }
        ).encode(),
    )
    return DiagnosisResult(status=status, proposal=proposal, receipt=receipt, error_code=error)
