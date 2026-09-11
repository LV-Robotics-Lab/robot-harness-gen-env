"""Extensible global schema-model directory outside Skill qualification closures."""

from __future__ import annotations

from importlib import import_module
from types import MappingProxyType
from typing import Any

_SCHEMA_MODEL_LOCATORS = MappingProxyType(
    {
        "harness.tabletop_layout_revision.v1": (
            ".schemas.native_prompt_revision",
            "TabletopLayoutRevisionV1",
        ),
        "harness.prompt_revision_command.v3": (
            ".schemas.native_prompt_revision",
            "PromptRevisionCommandV3",
        ),
        "harness.prompt_revision.v3": (".schemas.native_prompt_revision", "PromptRevisionV3"),
        "harness.prompt_revision_compile_attempt.v2": (
            ".schemas.native_prompt_revision",
            "PromptRevisionCompileAttemptV2",
        ),
        "harness.image2env_compile_input.v3": (
            ".schemas.native_prompt_revision",
            "Image2EnvCompileV3Input",
        ),
        "harness.image2env_genesis_compile_request.v2": (
            ".schemas.native_prompt_revision",
            "Image2EnvGenesisCompileRequestV2",
        ),
        "harness.image2env_genesis_compile_receipt.v2": (
            ".schemas.native_prompt_revision",
            "Image2EnvGenesisCompileReceiptV2",
        ),
        "harness.observation_citation.v2": (".schemas.golden_control_v2", "ObservationCitationV2"),
        "harness.advisory_record_command.v2": (
            ".schemas.golden_control_v2",
            "AdvisoryRecordCommandV2",
        ),
        "harness.external_agent_advisory_receipt.v2": (
            ".schemas.golden_control_v2",
            "ExternalAgentAdvisoryReceiptV2",
        ),
        "harness.prompt_revision_command.v2": (
            ".schemas.golden_control_v2",
            "PromptRevisionCommandV2",
        ),
        "harness.prompt_revision.v2": (".schemas.golden_control_v2", "PromptRevisionV2"),
        "harness.control_commit_snapshot.v2": (
            ".schemas.golden_control_v2",
            "ControlCommitSnapshotV2",
        ),
        "harness.typed_qualification_admission.v1": (
            ".schemas.typed_qualification_admission",
            "TypedQualificationAdmissionV1",
        ),
        "harness.genesis_fresh_capture_input.v1": (
            ".schemas.genesis_fresh_capture",
            "GenesisFreshCaptureInputV1",
        ),
        "harness.genesis_fresh_capture_request.v1": (
            ".schemas.genesis_fresh_capture",
            "GenesisFreshCaptureRequestV1",
        ),
        "harness.genesis_fresh_capture_result.v1": (
            ".schemas.genesis_fresh_capture",
            "GenesisFreshCaptureResultV1",
        ),
        "harness.genesis_fresh_capture_receipt.v1": (
            ".schemas.genesis_fresh_capture",
            "GenesisFreshCaptureReceiptV1",
        ),
        "harness.genesis_fresh_capture_qualification.v1": (
            ".schemas.genesis_fresh_capture",
            "GenesisFreshCaptureQualificationV1",
        ),
        "harness.genesis_scene_observe_input.v1": (
            ".schemas.genesis_scene_assessment",
            "GenesisSceneObserveInputV1",
        ),
        "harness.genesis_scene_observe_qualification.v1": (
            ".schemas.genesis_scene_assessment",
            "GenesisSceneObserveQualificationV1",
        ),
        "harness.genesis_scene_execution_request.v2": (
            ".schemas.genesis_scene_execution_v2",
            "GenesisSceneExecutionRequestV2",
        ),
        "harness.genesis_scene_process_observation.v2": (
            ".schemas.genesis_scene_execution_v2",
            "GenesisSceneProcessObservationV2",
        ),
        "harness.genesis_scene_launch_result.v2": (
            ".schemas.genesis_scene_execution_v2",
            "GenesisSceneLaunchResultV2",
        ),
        "harness.genesis_scene_execution_receipt.v2": (
            ".schemas.genesis_scene_execution_v2",
            "GenesisSceneExecutionReceiptV2",
        ),
        "harness.genesis_scene_replay_input.v2": (
            ".schemas.genesis_scene_application_v2",
            "GenesisSceneReplayInputV2",
        ),
        "harness.genesis_scene_skill_qualification_report.v2": (
            ".schemas.genesis_scene_application_v2",
            "GenesisSceneSkillQualificationReportV2",
        ),
        "harness.genesis_scene_validate_input.v1": (
            ".schemas.genesis_scene_assessment",
            "GenesisSceneValidateInputV1",
        ),
        "harness.genesis_scene_validation.v1": (
            ".schemas.genesis_scene_assessment",
            "GenesisSceneValidationV1",
        ),
        "harness.genesis_scene_validation_qualification.v1": (
            ".schemas.genesis_scene_assessment",
            "GenesisSceneValidationQualificationV1",
        ),
        "harness.genesis_scene_fresh_observation.v1": (
            ".schemas.genesis_scene_assessment",
            "GenesisSceneFreshObservationV1",
        ),
        "harness.genesis_scene_skill_qualification_report.v1": (
            ".schemas.genesis_scene_application",
            "GenesisSceneSkillQualificationReportV1",
        ),
        "harness.image2env_genesis_skill_qualification_report.v1": (
            ".schemas.image2env_genesis_compile_application",
            "Image2EnvGenesisSkillQualificationReportV1",
        ),
        "harness.genesis_scene_execution_request.v1": (
            ".schemas.genesis_scene_execution",
            "GenesisSceneExecutionRequestV1",
        ),
        "harness.genesis_scene_process_observation.v1": (
            ".schemas.genesis_scene_execution",
            "GenesisSceneProcessObservationV1",
        ),
        "harness.genesis_scene_launch_result.v1": (
            ".schemas.genesis_scene_execution",
            "GenesisSceneLaunchResultV1",
        ),
        "harness.genesis_scene_execution_receipt.v1": (
            ".schemas.genesis_scene_execution",
            "GenesisSceneExecutionReceiptV1",
        ),
        "harness.image2env_genesis_compile_request.v1": (
            ".schemas.image2env_genesis_compile",
            "Image2EnvGenesisCompileRequestV1",
        ),
        "harness.image2env_genesis_compile_receipt.v1": (
            ".schemas.image2env_genesis_compile",
            "Image2EnvGenesisCompileReceiptV1",
        ),
        "harness.genesis_scene_spec.v1": (".schemas.genesis_scene_replay", "GenesisSceneSpecV1"),
        "harness.genesis_scene_trace_row.v1": (
            ".schemas.genesis_scene_replay",
            "GenesisSceneTraceRowV1",
        ),
        "harness.genesis_scene_replay.v1": (
            ".schemas.genesis_scene_replay",
            "GenesisSceneReplayV1",
        ),
        "harness.genesis_scene_report.v1": (
            ".schemas.genesis_scene_replay",
            "GenesisSceneReportV1",
        ),
        "harness.genesis_runtime_execution_request.v1": (
            ".schemas.genesis_runtime_execution",
            "GenesisRuntimeExecutionRequestV1",
        ),
        "harness.genesis_runtime_observation.v1": (
            ".schemas.genesis_runtime_execution",
            "GenesisRuntimeObservationV1",
        ),
        "harness.genesis_runtime_execution_receipt.v1": (
            ".schemas.genesis_runtime_execution",
            "GenesisRuntimeExecutionReceiptV1",
        ),
        "harness.genesis_runtime_manifest.v1": (
            ".schemas.genesis_runtime_manifest",
            "GenesisRuntimeManifestV1",
        ),
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
        "harness.genesis_asset_contact_trace.v1": (
            ".schemas.genesis_asset_probe",
            "GenesisAssetContactTraceV1",
        ),
        "harness.genesis_asset_contact_trace_row.v1": (
            ".schemas.genesis_asset_probe",
            "GenesisAssetContactTraceRowV1",
        ),
        "harness.genesis_asset_load_observation.v1": (
            ".schemas.genesis_asset_probe",
            "GenesisAssetLoadObservationV1",
        ),
        "harness.genesis_asset_load_evidence.v1": (
            ".schemas.genesis_asset_probe",
            "GenesisAssetLoadEvidenceV1",
        ),
        "harness.genesis_asset_probe_input.v1": (
            ".schemas.genesis_asset_probe",
            "GenesisAssetProbeInputV1",
        ),
        "harness.genesis_asset_probe_report.v1": (
            ".schemas.genesis_asset_probe",
            "GenesisAssetProbeReportV1",
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
        "harness.image2env_compile_output.v2": (
            ".schemas.x2env",
            "Image2EnvCompileV2Output",
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
        "harness.image_media_probe.v1": (
            ".schemas.media_ingest",
            "ImageMediaProbeV1",
        ),
        "harness.image_source_media.v1": (".schemas.x2env", "ImageSourceMedia"),
        "harness.asset_closure_manifest.v1": (
            ".schemas.x2env_compile_evidence",
            "AssetClosureManifestV1",
        ),
        "harness.asset_qualification.v1": (
            ".schemas.x2env_compile_evidence",
            "GenesisAssetQualificationV1",
        ),
        "harness.asset_representation.v1": (
            ".schemas.x2env_compile_evidence",
            "AssetRepresentationV1",
        ),
        "harness.genesis_runtime_lock.v1": (
            ".schemas.x2env_compile_evidence",
            "GenesisRuntimeLockV1",
        ),
        "harness.portable_asset_catalog.v1": (
            ".schemas.x2env_compile_evidence",
            "PortableAssetCatalogV1",
        ),
        "harness.scene_interpretation_proposal.v1": (
            ".schemas.x2env_compile_evidence",
            "SceneInterpretationProposalV1",
        ),
        "harness.scene_ir.v1": (".schemas.x2env_compile_evidence", "SceneIRV1"),
        "harness.task_ir.v1": (".schemas.x2env_compile_evidence", "TaskIRV1"),
        "harness.x2env_asset_selection.v1": (
            ".schemas.x2env_compile_evidence",
            "X2EnvAssetSelectionV1",
        ),
        "harness.x2env_binding_manifest.v1": (
            ".schemas.x2env_compile_evidence",
            "X2EnvBindingManifestV1",
        ),
        "harness.x2env_compile_receipt.v1": (
            ".schemas.x2env_compile_evidence",
            "X2EnvCompileReceiptV1",
        ),
        "harness.x2env_compile_receipt.v2": (
            ".schemas.x2env_compile_evidence",
            "X2EnvCompileReceiptV2",
        ),
        "harness.x2env_static_validation.v1": (
            ".schemas.x2env_compile_evidence",
            "X2EnvStaticValidationV1",
        ),
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
        "harness.video2env_compile_output.v2": (
            ".schemas.x2env",
            "Video2EnvCompileV2Output",
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
        "harness.video_decoded_frame_sequence.v1": (
            ".schemas.video_ingest",
            "VideoDecodedFrameSequenceV1",
        ),
        "harness.video_media_probe.v1": (
            ".schemas.video_ingest",
            "VideoMediaProbeV1",
        ),
        "harness.video_source_media.v1": (".schemas.x2env", "VideoSourceMedia"),
        "harness.video_source_media.v2": (
            ".schemas.video_ingest",
            "VideoSourceMediaV2",
        ),
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
