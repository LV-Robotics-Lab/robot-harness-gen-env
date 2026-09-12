"""Typed Skill execution bindings; workflow and lifecycle remain owned by Harness."""

import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from .assets import AssetRegistry
from .capabilities import CapabilityRegistry
from .compile import CompiledScene, ResolvedAssetSet, compile_scene
from .contracts import ArtifactRef, Model, SceneIR
from .diagnosis import DiagnosisResult
from .genesis_runtime import RuntimeScene
from .observation import ObservationResult
from .replay import ReplayResult


class CompileCall(Model):
    scene_ir: ArtifactRef
    assets: ResolvedAssetSet
    output_root: str
    seed: int


class ReplayCall(Model):
    scene: RuntimeScene
    package_root: str
    output_root: str
    timeout: int = Field(ge=1, le=1770)


class ValidateCall(Model):
    scene_ir: ArtifactRef
    observation: ArtifactRef
    diagnosis: ArtifactRef


class ValidationResult(Model):
    status: Literal["succeeded", "failed"]
    report: ArtifactRef
    repairable: bool
    error_code: str | None


def build_capabilities(store, *, compile_policy, replay_executor):
    capabilities = CapabilityRegistry()

    def compile_handler(call):
        if compile_policy is None:
            raise ValueError("compile_policy_required")
        return compile_scene(
            call.scene_ir,
            call.assets,
            registry=AssetRegistry(store),
            store=store,
            output_root=Path(call.output_root),
            policy=compile_policy,
            seed=call.seed,
        )

    capabilities.register("x2env.compile", "1.0.0", CompileCall, CompiledScene, compile_handler)

    def replay_handler(call):
        if replay_executor is None:
            raise ValueError("replay_executor_required")
        return replay_executor.replay(
            call.scene,
            package_root=Path(call.package_root),
            output_root=Path(call.output_root),
            timeout=call.timeout,
        )

    capabilities.register("x2env.replay", "1.0.0", ReplayCall, ReplayResult, replay_handler)

    def validate_handler(call):
        scene = SceneIR.model_validate_json(store.read_artifact(call.scene_ir))
        observed = ObservationResult.model_validate_json(store.read_artifact(call.observation))
        diagnosis = DiagnosisResult.model_validate_json(store.read_artifact(call.diagnosis))
        physical = json.loads(store.read_artifact(observed.physics_report))
        proposal = diagnosis.proposal
        if observed.observation.scene_ir != call.scene_ir or (
            proposal is not None and proposal.base_revision != scene.revision
        ):
            raise ValueError("validation_scene_binding_mismatch")
        physical_pass = (
            physical.get("physical_status") == "passed"
            and physical.get("execution_evidence_bound") is True
        )
        visual_pass = (
            diagnosis.status == "completed"
            and proposal is not None
            and proposal.visual_intent == "passed"
        )
        patches = proposal is not None and (proposal.scene_patches or proposal.asset_patches)
        repairable = bool(patches) and physical.get("execution_evidence_bound") is True
        code = (
            "revision_required"
            if patches
            else "physical_validation_not_passed"
            if not physical_pass
            else "visual_intent_not_passed"
            if not visual_pass
            else None
        )
        status = "succeeded" if physical_pass and visual_pass and not patches else "failed"
        report = {
            "physical_status": physical.get("physical_status", "not_run"),
            "visual_status": proposal.visual_intent if proposal else "not_run",
            "physics_report": observed.physics_report.model_dump(mode="json"),
            "diagnosis": call.diagnosis.model_dump(mode="json"),
            "scene_ir": call.scene_ir.model_dump(mode="json"),
            "status": status,
            "error_code": code,
            "sim_ready": False,
        }
        ref = store.write_artifact(json.dumps(report, sort_keys=True).encode(), "application/json")
        return ValidationResult(status=status, report=ref, repairable=repairable, error_code=code)

    capabilities.register(
        "x2env.validate", "1.0.0", ValidateCall, ValidationResult, validate_handler
    )
    return capabilities
