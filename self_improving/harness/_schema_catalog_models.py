"""Extensible global schema-model directory outside Skill qualification closures."""

from __future__ import annotations

from importlib import import_module
from types import MappingProxyType
from typing import Any

_SCHEMA_MODEL_LOCATORS = MappingProxyType(
    {
        "harness.artifact_ref.v1": (".schemas.common", "ArtifactRef"),
        "harness.asset_debt_inventory.v1": (
            ".schemas.asset_repair",
            "AssetDebtInventory",
        ),
        "harness.asset_repair_plan_request.v1": (
            ".schemas.asset_repair",
            "AssetRepairPlanRequest",
        ),
        "harness.asset_repair_plan.v1": (".schemas.asset_repair", "AssetRepairPlan"),
        "harness.asset_source_snapshot_manifest.v1": (
            ".schemas.asset_staging",
            "AssetSourceSnapshotManifest",
        ),
        "harness.asset_stage_request.v1": (
            ".schemas.asset_staging",
            "AssetStageRequest",
        ),
        "harness.asset_stage_result.v1": (
            ".schemas.asset_staging",
            "AssetStageResult",
        ),
        "harness.blocker.v1": (".schemas.common", "Blocker"),
        "harness.environment_package.v1": (".schemas.text2env", "EnvironmentPackage"),
        "harness.event.v1": (".schemas.common", "Event"),
        "harness.registry_snapshot.v1": (".schemas.registry_snapshot", "RegistrySnapshot"),
        "harness.replay_vlm_assessment_input.v1": (
            ".schemas.replay_vlm",
            "ReplayVlmAssessmentInput",
        ),
        "harness.replay_vlm_assessment_output.v1": (
            ".schemas.replay_vlm",
            "ReplayVlmAssessmentOutput",
        ),
        "harness.replay_vlm_assessment_output.v2": (
            ".schemas.replay_vlm",
            "ReplayVlmAssessmentOutputV2",
        ),
        "harness.run_read_request.v1": (".schemas.workflow", "RunReadRequest"),
        "harness.run_snapshot.v1": (".schemas.workflow", "RunSnapshot"),
        "harness.run_start_request.v1": (".schemas.workflow", "RunStartRequest"),
        "harness.run_state.v1": (".schemas.common", "RunState"),
        "harness.skill_descriptor.v1": (".schemas.common", "SkillDescriptor"),
        "harness.skill_descriptor.v2": (".schemas.common", "SkillDescriptorV2"),
        "harness.skill_invocation.v1": (".schemas.common", "Invocation"),
        "harness.skill_qualification.v1": (".schemas.common", "SkillQualification"),
        "harness.skill_qualification_report.v1": (
            ".schemas.qualification_report",
            "PublicQualificationReportV1",
        ),
        "harness.text2env_compile_input.v1": (".schemas.text2env", "Text2EnvCompileInput"),
        "harness.text2env_compile_output.v1": (
            ".schemas.text2env",
            "Text2EnvCompileOutput",
        ),
        "harness.text2env_replay_input.v1": (".schemas.text2env", "Text2EnvReplayInput"),
        "harness.text2env_replay_output.v1": (".schemas.text2env", "Text2EnvReplayOutput"),
        "harness.text2env_validate_input.v1": (
            ".schemas.text2env",
            "Text2EnvValidateInput",
        ),
        "harness.text2env_validate_input.v2": (
            ".schemas.text2env_validate_v2",
            "Text2EnvValidateV2Input",
        ),
        "harness.text2env_validate_output.v1": (
            ".schemas.text2env",
            "Text2EnvValidateOutput",
        ),
        "harness.text2env_validate_output.v2": (
            ".schemas.text2env_validate_v2",
            "Text2EnvValidateV2Output",
        ),
        "harness.workflow_start_receipt.v1": (
            ".schemas.workflow",
            "WorkflowStartReceipt",
        ),
    }
)


def schema_ids() -> tuple[str, ...]:
    return tuple(_SCHEMA_MODEL_LOCATORS)


def schema_model(schema_id: str, package: str) -> type[Any]:
    module_name, model_name = _SCHEMA_MODEL_LOCATORS[schema_id]
    return getattr(import_module(module_name, package), model_name)
