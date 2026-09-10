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
        "harness.genesis_observation_receipt.v1": (
            ".schemas.genesis_observe",
            "GenesisObservationReceipt",
        ),
        "harness.genesis_observe_input.v1": (
            ".schemas.genesis_observe",
            "GenesisObserveInput",
        ),
        "harness.genesis_observe_output.v1": (
            ".schemas.genesis_observe",
            "GenesisObserveOutput",
        ),
        "harness.initial_compile_attempt.v1": (
            ".schemas.compile_attempt",
            "InitialCompileAttemptV1",
        ),
        "harness.advisory_record_command.v1": (
            ".schemas.golden_control",
            "AdvisoryRecordCommand",
        ),
        "harness.control_commit_snapshot.v1": (
            ".schemas.golden_control",
            "ControlCommitSnapshot",
        ),
        "harness.external_agent_advisory_receipt.v1": (
            ".schemas.golden_control",
            "ExternalAgentAdvisoryReceipt",
        ),
        "harness.prompt_revision_command.v1": (
            ".schemas.golden_control",
            "PromptRevisionCommand",
        ),
        "harness.prompt_revision.v1": (
            ".schemas.golden_control",
            "PromptRevision",
        ),
        "harness.prompt_revision_compile_attempt.v1": (
            ".schemas.compile_attempt",
            "PromptRevisionCompileAttemptV1",
        ),
        "harness.resource_access_receipt.v1": (
            ".schemas.resource_access",
            "ResourceAccessReceipt",
        ),
        "harness.image2env_compile_input.v1": (
            ".schemas.x2env",
            "Image2EnvCompileInput",
        ),
        "harness.image2env_compile_input.v2": (
            ".schemas.x2env",
            "Image2EnvCompileV2Input",
        ),
        "harness.image2env_compile_output.v1": (
            ".schemas.x2env",
            "Image2EnvCompileOutput",
        ),
        "harness.image2env_replay_input.v1": (
            ".schemas.x2env",
            "Image2EnvReplayInput",
        ),
        "harness.image2env_replay_output.v1": (
            ".schemas.x2env",
            "Image2EnvReplayOutput",
        ),
        "harness.image2env_validate_input.v1": (
            ".schemas.x2env",
            "Image2EnvValidateInput",
        ),
        "harness.image2env_validate_output.v1": (
            ".schemas.x2env",
            "Image2EnvValidateOutput",
        ),
        "harness.image_source_media.v1": (".schemas.x2env", "ImageSourceMedia"),
        "harness.operation_snapshot.v1": (".schemas.workflow", "OperationSnapshot"),
        "harness.qualified_skill_execution.v2": (
            ".schemas.workflow_operation",
            "QualifiedSkillExecutionV2",
        ),
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
        "harness.run_command_request.v1": (".schemas.workflow", "RunCommandRequest"),
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
        "harness.state_delta.v2": (".schemas.workflow_operation", "StateDeltaV2"),
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
        "harness.tool_result.v2": (".schemas.workflow_operation", "ToolResultV2"),
        "harness.trusted_tool_receipt.v2": (
            ".schemas.workflow_operation",
            "TrustedToolReceiptV2",
        ),
        "harness.video2env_compile_input.v1": (
            ".schemas.x2env",
            "Video2EnvCompileInput",
        ),
        "harness.video2env_compile_input.v2": (
            ".schemas.x2env",
            "Video2EnvCompileV2Input",
        ),
        "harness.video2env_compile_output.v1": (
            ".schemas.x2env",
            "Video2EnvCompileOutput",
        ),
        "harness.video2env_replay_input.v1": (
            ".schemas.x2env",
            "Video2EnvReplayInput",
        ),
        "harness.video2env_replay_output.v1": (
            ".schemas.x2env",
            "Video2EnvReplayOutput",
        ),
        "harness.video2env_validate_input.v1": (
            ".schemas.x2env",
            "Video2EnvValidateInput",
        ),
        "harness.video2env_validate_output.v1": (
            ".schemas.x2env",
            "Video2EnvValidateOutput",
        ),
        "harness.video_source_media.v1": (".schemas.x2env", "VideoSourceMedia"),
        "harness.workflow_operation_receipt.v2": (
            ".schemas.workflow_operation",
            "WorkflowOperationReceiptV2",
        ),
        "harness.workflow_start_receipt.v1": (
            ".schemas.workflow",
            "WorkflowStartReceipt",
        ),
        "harness.x2env_asset_decision.v1": (".schemas.x2env", "X2EnvAssetDecision"),
        "harness.x2env_environment_package.v1": (
            ".schemas.x2env",
            "X2EnvEnvironmentPackage",
        ),
    }
)


def schema_ids() -> tuple[str, ...]:
    return tuple(_SCHEMA_MODEL_LOCATORS)


def schema_model(schema_id: str, package: str) -> type[Any]:
    module_name, model_name = _SCHEMA_MODEL_LOCATORS[schema_id]
    return getattr(import_module(module_name, package), model_name)
