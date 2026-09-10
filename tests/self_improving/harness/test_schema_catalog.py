from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from self_improving.harness.schema_catalog import (
    DEFAULT_SCHEMA_ROOT,
    SCHEMA_MODELS,
    SchemaSnapshotMismatch,
    expected_schema_snapshots,
    export_schema_snapshots,
    render_schema_document,
    schema_documents,
    schema_model,
    schema_snapshot_name,
)
from self_improving.harness.schemas.asset_repair import (
    AssetDebtInventory,
    AssetRepairPlan,
    AssetRepairPlanRequest,
)
from self_improving.harness.schemas.asset_staging import (
    AssetSourceSnapshotManifest,
    AssetStageRequest,
    AssetStageResult,
)
from self_improving.harness.schemas.common import ArtifactRef, SkillDescriptorV2
from self_improving.harness.schemas.genesis_observe import (
    GenesisObservationReceipt,
    GenesisObserveInput,
    GenesisObserveOutput,
)
from self_improving.harness.schemas.golden_control import (
    AdvisoryRecordCommand,
    ControlCommitSnapshot,
    ExternalAgentAdvisoryReceipt,
    PromptRevision,
    PromptRevisionCommand,
)
from self_improving.harness.schemas.qualification_report import PublicQualificationReportV1
from self_improving.harness.schemas.registry_snapshot import RegistrySnapshot
from self_improving.harness.schemas.text2env_validate_v2 import (
    Text2EnvValidateV2Input,
    Text2EnvValidateV2Output,
)
from self_improving.harness.schemas.workflow import (
    OperationSnapshot,
    RunCommandRequest,
    RunReadRequest,
    RunSnapshot,
    RunStartRequest,
    WorkflowStartReceipt,
)
from self_improving.harness.schemas.workflow_operation import (
    QualifiedSkillExecutionV2,
    StateDeltaV2,
    ToolResultV2,
    TrustedToolReceiptV2,
    WorkflowOperationReceiptV2,
)
from self_improving.harness.schemas.x2env import (
    Image2EnvCompileInput,
    Image2EnvCompileOutput,
    Image2EnvReplayInput,
    Image2EnvReplayOutput,
    Image2EnvValidateInput,
    Image2EnvValidateOutput,
    ImageSourceMedia,
    Video2EnvCompileInput,
    Video2EnvCompileOutput,
    Video2EnvReplayInput,
    Video2EnvReplayOutput,
    Video2EnvValidateInput,
    Video2EnvValidateOutput,
    VideoSourceMedia,
    X2EnvAssetDecision,
    X2EnvEnvironmentPackage,
)

EXPECTED_SCHEMA_IDS = {
    "harness.advisory_record_command.v1",
    "harness.asset_debt_inventory.v1",
    "harness.asset_repair_plan.v1",
    "harness.asset_repair_plan_request.v1",
    "harness.asset_source_snapshot_manifest.v1",
    "harness.asset_stage_request.v1",
    "harness.asset_stage_result.v1",
    "harness.artifact_ref.v1",
    "harness.blocker.v1",
    "harness.control_commit_snapshot.v1",
    "harness.environment_package.v1",
    "harness.event.v1",
    "harness.external_agent_advisory_receipt.v1",
    "harness.genesis_observation_receipt.v1",
    "harness.genesis_observe_input.v1",
    "harness.genesis_observe_output.v1",
    "harness.image2env_compile_input.v1",
    "harness.image2env_compile_output.v1",
    "harness.image2env_replay_input.v1",
    "harness.image2env_replay_output.v1",
    "harness.image2env_validate_input.v1",
    "harness.image2env_validate_output.v1",
    "harness.image_source_media.v1",
    "harness.operation_snapshot.v1",
    "harness.prompt_revision.v1",
    "harness.prompt_revision_command.v1",
    "harness.qualified_skill_execution.v2",
    "harness.replay_vlm_assessment_input.v1",
    "harness.replay_vlm_assessment_output.v1",
    "harness.replay_vlm_assessment_output.v2",
    "harness.registry_snapshot.v1",
    "harness.run_command_request.v1",
    "harness.run_read_request.v1",
    "harness.run_state.v1",
    "harness.run_snapshot.v1",
    "harness.run_start_request.v1",
    "harness.skill_descriptor.v1",
    "harness.skill_descriptor.v2",
    "harness.skill_invocation.v1",
    "harness.skill_qualification.v1",
    "harness.skill_qualification_report.v1",
    "harness.state_delta.v2",
    "harness.text2env_compile_input.v1",
    "harness.text2env_compile_output.v1",
    "harness.text2env_replay_input.v1",
    "harness.text2env_replay_output.v1",
    "harness.text2env_validate_input.v1",
    "harness.text2env_validate_input.v2",
    "harness.text2env_validate_output.v1",
    "harness.text2env_validate_output.v2",
    "harness.tool_result.v2",
    "harness.video2env_compile_input.v1",
    "harness.video2env_compile_output.v1",
    "harness.video2env_replay_input.v1",
    "harness.video2env_replay_output.v1",
    "harness.video2env_validate_input.v1",
    "harness.video2env_validate_output.v1",
    "harness.video_source_media.v1",
    "harness.trusted_tool_receipt.v2",
    "harness.workflow_operation_receipt.v2",
    "harness.workflow_start_receipt.v1",
    "harness.x2env_asset_decision.v1",
    "harness.x2env_environment_package.v1",
}


def test_schema_catalog_is_exact_and_immutable() -> None:
    assert set(SCHEMA_MODELS) == EXPECTED_SCHEMA_IDS
    assert schema_model("harness.artifact_ref.v1") is ArtifactRef
    assert schema_model("harness.skill_descriptor.v2") is SkillDescriptorV2
    assert schema_model("harness.text2env_validate_input.v2") is Text2EnvValidateV2Input
    assert schema_model("harness.text2env_validate_output.v2") is Text2EnvValidateV2Output
    assert schema_model("harness.run_start_request.v1") is RunStartRequest
    assert schema_model("harness.run_snapshot.v1") is RunSnapshot
    assert schema_model("harness.workflow_start_receipt.v1") is WorkflowStartReceipt
    assert schema_model("harness.registry_snapshot.v1") is RegistrySnapshot
    assert schema_model("harness.run_read_request.v1") is RunReadRequest
    assert schema_model("harness.run_command_request.v1") is RunCommandRequest
    assert schema_model("harness.operation_snapshot.v1") is OperationSnapshot
    assert schema_model("harness.qualified_skill_execution.v2") is QualifiedSkillExecutionV2
    assert schema_model("harness.state_delta.v2") is StateDeltaV2
    assert schema_model("harness.tool_result.v2") is ToolResultV2
    assert schema_model("harness.trusted_tool_receipt.v2") is TrustedToolReceiptV2
    assert schema_model("harness.workflow_operation_receipt.v2") is WorkflowOperationReceiptV2
    assert schema_model("harness.skill_qualification_report.v1") is PublicQualificationReportV1
    assert schema_model("harness.asset_debt_inventory.v1") is AssetDebtInventory
    assert schema_model("harness.asset_repair_plan_request.v1") is AssetRepairPlanRequest
    assert schema_model("harness.asset_repair_plan.v1") is AssetRepairPlan
    assert schema_model("harness.asset_source_snapshot_manifest.v1") is AssetSourceSnapshotManifest
    assert schema_model("harness.asset_stage_request.v1") is AssetStageRequest
    assert schema_model("harness.asset_stage_result.v1") is AssetStageResult
    assert schema_model("harness.genesis_observe_input.v1") is GenesisObserveInput
    assert schema_model("harness.genesis_observe_output.v1") is GenesisObserveOutput
    assert schema_model("harness.genesis_observation_receipt.v1") is GenesisObservationReceipt
    assert schema_model("harness.advisory_record_command.v1") is AdvisoryRecordCommand
    assert schema_model("harness.control_commit_snapshot.v1") is ControlCommitSnapshot
    assert (
        schema_model("harness.external_agent_advisory_receipt.v1") is ExternalAgentAdvisoryReceipt
    )
    assert schema_model("harness.prompt_revision_command.v1") is PromptRevisionCommand
    assert schema_model("harness.prompt_revision.v1") is PromptRevision
    assert schema_model("harness.image_source_media.v1") is ImageSourceMedia
    assert schema_model("harness.video_source_media.v1") is VideoSourceMedia
    assert schema_model("harness.x2env_asset_decision.v1") is X2EnvAssetDecision
    assert schema_model("harness.x2env_environment_package.v1") is X2EnvEnvironmentPackage
    assert schema_model("harness.image2env_compile_input.v1") is Image2EnvCompileInput
    assert schema_model("harness.image2env_compile_output.v1") is Image2EnvCompileOutput
    assert schema_model("harness.image2env_replay_input.v1") is Image2EnvReplayInput
    assert schema_model("harness.image2env_replay_output.v1") is Image2EnvReplayOutput
    assert schema_model("harness.image2env_validate_input.v1") is Image2EnvValidateInput
    assert schema_model("harness.image2env_validate_output.v1") is Image2EnvValidateOutput
    assert schema_model("harness.video2env_compile_input.v1") is Video2EnvCompileInput
    assert schema_model("harness.video2env_compile_output.v1") is Video2EnvCompileOutput
    assert schema_model("harness.video2env_replay_input.v1") is Video2EnvReplayInput
    assert schema_model("harness.video2env_replay_output.v1") is Video2EnvReplayOutput
    assert schema_model("harness.video2env_validate_input.v1") is Video2EnvValidateInput
    assert schema_model("harness.video2env_validate_output.v1") is Video2EnvValidateOutput
    with pytest.raises(KeyError):
        schema_model("harness.missing.v1")
    with pytest.raises(TypeError):
        SCHEMA_MODELS["harness.other.v1"] = ArtifactRef  # type: ignore[index]


def test_lazy_schema_catalog_preserves_mappingproxy_copy_and_union_behavior() -> None:
    copied = SCHEMA_MODELS.copy()
    assert type(copied) is dict
    assert copied["harness.artifact_ref.v1"] is ArtifactRef

    marker = {"harness.other.v1": ArtifactRef}
    merged = SCHEMA_MODELS | marker
    reverse_merged = marker | SCHEMA_MODELS
    assert type(merged) is type(reverse_merged) is dict
    assert merged["harness.artifact_ref.v1"] is ArtifactRef
    assert merged["harness.other.v1"] is ArtifactRef
    assert reverse_merged["harness.artifact_ref.v1"] is ArtifactRef
    assert reverse_merged["harness.other.v1"] is ArtifactRef


def test_public_documents_are_strict_draft_2020_12_schemas() -> None:
    documents = schema_documents()
    assert list(documents) == sorted(EXPECTED_SCHEMA_IDS)
    for schema_id, document in documents.items():
        assert document["$id"] == schema_id
        assert document["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert document["additionalProperties"] is False
        if schema_id in {
            "harness.asset_debt_inventory.v1",
            "harness.asset_repair_plan.v1",
            "harness.asset_repair_plan_request.v1",
            "harness.asset_source_snapshot_manifest.v1",
            "harness.asset_stage_request.v1",
            "harness.asset_stage_result.v1",
            "harness.run_start_request.v1",
            "harness.run_command_request.v1",
            "harness.run_snapshot.v1",
            "harness.operation_snapshot.v1",
            "harness.registry_snapshot.v1",
            "harness.run_read_request.v1",
            "harness.skill_qualification_report.v1",
            "harness.workflow_start_receipt.v1",
            "harness.qualified_skill_execution.v2",
            "harness.state_delta.v2",
            "harness.tool_result.v2",
            "harness.trusted_tool_receipt.v2",
            "harness.workflow_operation_receipt.v2",
            "harness.genesis_observation_receipt.v1",
            "harness.genesis_observe_input.v1",
            "harness.genesis_observe_output.v1",
            "harness.advisory_record_command.v1",
            "harness.control_commit_snapshot.v1",
            "harness.external_agent_advisory_receipt.v1",
            "harness.prompt_revision_command.v1",
            "harness.prompt_revision.v1",
            "harness.image_source_media.v1",
            "harness.video_source_media.v1",
            "harness.x2env_asset_decision.v1",
            "harness.x2env_environment_package.v1",
            "harness.image2env_compile_input.v1",
            "harness.image2env_compile_output.v1",
            "harness.image2env_replay_input.v1",
            "harness.image2env_replay_output.v1",
            "harness.image2env_validate_input.v1",
            "harness.image2env_validate_output.v1",
            "harness.video2env_compile_input.v1",
            "harness.video2env_compile_output.v1",
            "harness.video2env_replay_input.v1",
            "harness.video2env_replay_output.v1",
            "harness.video2env_validate_input.v1",
            "harness.video2env_validate_output.v1",
        }:
            assert document["properties"]["schema_version"]["const"] == schema_id
            assert "schema_version" in document["required"]
        elif schema_id != "harness.artifact_ref.v1":
            assert "schema_version" not in document["properties"]

    assert documents["harness.artifact_ref.v1"]["required"] == [
        "name",
        "uri",
        "media_type",
        "sha256",
        "bytes",
        "schema_version",
    ]
    assert "from_status" in documents["harness.event.v1"]["required"]
    assert (
        documents["harness.asset_repair_plan.v1"]["properties"]["writes_performed"]["const"]
        is False
    )
    stage_result = documents["harness.asset_stage_result.v1"]
    for field in {
        "authoritative_ledger_writes_performed",
        "promotion_executed",
        "runtime_qualification_executed",
        "simulator_executed",
    }:
        assert stage_result["properties"][field]["const"] is False
    assert stage_result["properties"]["exact_source_bytes_staged"]["const"] is True
    assert stage_result["properties"]["loader_closure_enumerated"]["const"] is True
    assert (
        documents["harness.asset_repair_plan.v1"]["properties"]["runtime_qualification_executed"][
            "const"
        ]
        is False
    )
    assert (
        documents["harness.asset_repair_plan.v1"]["properties"]["full_baseline_evaluated"]["type"]
        == "boolean"
    )
    assert (
        documents["harness.asset_repair_plan.v1"]["properties"]["probe_bytes_in_cas"]["const"]
        is False
    )
    for schema_id in {
        "harness.asset_repair_plan.v1",
        "harness.asset_repair_plan_request.v1",
    }:
        constraints = documents[schema_id]["properties"]["inventory_ref"]["allOf"][1]["properties"]
        assert constraints == {
            "uri": {
                "maxLength": 82,
                "minLength": 82,
                "pattern": r"^artifact://sha256/[0-9a-f]{64}$",
            },
            "media_type": {"const": "application/json"},
            "schema_version": {"const": "harness.asset_debt_inventory.v1"},
        }
    staging_ref_contracts = {
        "harness.asset_source_snapshot_manifest.v1": {
            "inventory_ref": "harness.asset_debt_inventory.v1",
        },
        "harness.asset_stage_request.v1": {
            "inventory_ref": "harness.asset_debt_inventory.v1",
            "repair_plan_ref": "harness.asset_repair_plan.v1",
            "source_snapshot_manifest_ref": "harness.asset_source_snapshot_manifest.v1",
        },
        "harness.asset_stage_result.v1": {
            "inventory_ref": "harness.asset_debt_inventory.v1",
            "repair_plan_ref": "harness.asset_repair_plan.v1",
            "source_snapshot_manifest_ref": "harness.asset_source_snapshot_manifest.v1",
        },
    }
    for schema_id, fields in staging_ref_contracts.items():
        for field, schema_version in fields.items():
            constraints = documents[schema_id]["properties"][field]["allOf"][1]["properties"]
            assert constraints == {
                "uri": {
                    "maxLength": 82,
                    "minLength": 82,
                    "pattern": r"^artifact://sha256/[0-9a-f]{64}$",
                },
                "media_type": {"const": "application/json"},
                "schema_version": {"const": schema_version},
            }
    portable_path_pattern = (
        r"^(?![A-Za-z]:)(?!\.{1,2}(?:/|$))"
        r"[^/\\\u0000-\u001f\u007f\u2028\u2029]+"
        r"(?:/(?!\.{1,2}(?:/|$))[^/\\\u0000-\u001f\u007f\u2028\u2029]+)*$"
    )
    inventory_defs = documents["harness.asset_debt_inventory.v1"]["$defs"]
    assert (
        inventory_defs["AssetDebtEntry"]["properties"]["ledger_path"]["pattern"]
        == portable_path_pattern
    )
    assert (
        inventory_defs["RepresentationRecoveryProbe"]["properties"]["logical_path"]["pattern"]
        == portable_path_pattern
    )
    assert (
        documents["harness.asset_repair_plan.v1"]["$defs"]["RepresentationRepairPlan"][
            "properties"
        ]["logical_path"]["pattern"]
        == portable_path_pattern
    )
    run_required = documents["harness.run_state.v1"]["required"]
    assert {"invocation_digest", "ended_at", "output", "blocker"} <= set(run_required)
    assert documents["harness.run_read_request.v1"]["required"] == [
        "schema_version",
        "principal_id",
        "workflow_run_id",
    ]

    compile_input = documents["harness.text2env_compile_input.v1"]
    assert "config" in compile_input["required"]
    config = compile_input["$defs"]["CompileConfig"]
    assert config["properties"]["generate_missing_assets"]["default"] is False

    runtime_input = documents["harness.text2env_replay_input.v1"]
    runtime = runtime_input["$defs"]["RuntimeConfig"]
    assert runtime["properties"]["contact_window_steps"]["default"] == 120

    validate_v2_input = documents["harness.text2env_validate_input.v2"]
    assert validate_v2_input["properties"]["gate_profile"]["default"] == (
        "robotwin.scene_validation.v1"
    )
    assert set(validate_v2_input["required"]) == {
        "environment_package",
        "compile_run_receipt",
        "compile_qualification",
        "replay_run_receipt",
        "replay_qualification",
        "replay_execution_receipt",
        "runtime_evidence",
        "runtime_validation_report",
        "runtime_asset_snapshot_manifest",
        "media_verification_receipt",
        "request_provenance",
        "runtime_event_transcript",
    }
    validate_v2_contracts = {
        "environment_package": ("application/json", "harness.environment_package.v1"),
        "compile_run_receipt": ("application/json", "harness.portable_run_receipt.v1"),
        "compile_qualification": ("application/json", "harness.skill_qualification.v1"),
        "replay_run_receipt": ("application/json", "harness.portable_run_receipt.v1"),
        "replay_qualification": ("application/json", "harness.skill_qualification.v1"),
        "replay_execution_receipt": (
            "application/json",
            "harness.text2env_replay_receipt.v1",
        ),
        "runtime_evidence": ("application/json", "robotwin.scene_runtime_evidence.v2"),
        "runtime_validation_report": ("application/json", "robotwin.scene_validation.v1"),
        "runtime_asset_snapshot_manifest": (
            "application/json",
            "harness.runtime_asset_snapshot.v1",
        ),
        "media_verification_receipt": (
            "application/json",
            "harness.replay_media_verification.v1",
        ),
        "request_provenance": (
            "application/json",
            "harness.text2env_request_provenance.v1",
        ),
        "runtime_event_transcript": (
            "application/x-ndjson",
            "harness.runtime_event_transcript.v1",
        ),
    }
    for field_name, (media_type, schema_version) in validate_v2_contracts.items():
        constraints = validate_v2_input["properties"][field_name]["allOf"][1]["properties"]
        assert constraints == {
            "uri": {
                "maxLength": 82,
                "minLength": 82,
                "pattern": r"^artifact://sha256/[0-9a-f]{64}$",
            },
            "media_type": {"const": media_type},
            "schema_version": {"const": schema_version},
        }

    validate_v2_output = documents["harness.text2env_validate_output.v2"]
    for field_name, schema_version in {
        "validation_report": "robotwin.scene_validation.v1",
        "validation_decision_receipt": "harness.text2env_validation_decision.v2",
    }.items():
        constraints = validate_v2_output["properties"][field_name]["allOf"][1]["properties"]
        assert constraints["uri"] == {
            "maxLength": 82,
            "minLength": 82,
            "pattern": r"^artifact://sha256/[0-9a-f]{64}$",
        }
        assert constraints["media_type"] == {"const": "application/json"}
        assert constraints["schema_version"] == {"const": schema_version}

    descriptor_v2 = documents["harness.skill_descriptor.v2"]
    assert descriptor_v2["required"] == [
        "skill_id",
        "version",
        "mcp_tool_name",
        "input_schema",
        "output_schema",
        "implementation_name",
        "implementation_version",
        "implementation_sha256",
        "reproducibility",
        "max_attempts",
        "qualification_artifact",
    ]
    assert "deterministic" not in descriptor_v2["properties"]

    image_media = documents["harness.image_source_media.v1"]
    assert image_media["properties"]["decoded_frame_count"]["const"] == 1
    video_media = documents["harness.video_source_media.v1"]
    assert "frame_sequence_sha256" in video_media["required"]
    for route in ("image2env", "video2env"):
        compile_output = documents[f"harness.{route}_compile_output.v1"]
        assert compile_output["properties"]["simulator_executed"]["const"] is False
        assert compile_output["properties"]["publishable"]["const"] is False
        replay_output = documents[f"harness.{route}_replay_output.v1"]
        assert replay_output["properties"]["simulator_executed"]["const"] is True
        assert replay_output["properties"]["collisions_enabled"]["const"] is True


def test_schema_rendering_and_snapshot_names_are_canonical() -> None:
    name = schema_snapshot_name("harness.artifact_ref.v1")
    assert name == "harness.artifact_ref.v1.schema.json"
    rendered = render_schema_document({"z": 1, "a": "值"})
    assert rendered == '{\n  "a": "值",\n  "z": 1\n}\n'

    snapshots = expected_schema_snapshots()
    assert set(snapshots) == {f"{schema_id}.schema.json" for schema_id in EXPECTED_SCHEMA_IDS}
    for content in snapshots.values():
        assert content.endswith("\n")
        assert json.loads(content)["$id"] in EXPECTED_SCHEMA_IDS


def test_export_writes_and_verifies_exact_snapshots(tmp_path: Path) -> None:
    paths = export_schema_snapshots(tmp_path)
    assert len(paths) == len(EXPECTED_SCHEMA_IDS)
    assert all(path.is_file() for path in paths)
    assert export_schema_snapshots(tmp_path, check=True) == paths


def test_snapshot_check_reports_missing_changed_and_unexpected(tmp_path: Path) -> None:
    paths = export_schema_snapshots(tmp_path)
    paths[0].unlink()
    paths[1].write_text("{}\n", encoding="utf-8")
    (tmp_path / "harness.unexpected.v1.schema.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(SchemaSnapshotMismatch) as error:
        export_schema_snapshots(tmp_path, check=True)

    assert any(problem.startswith("missing ") for problem in error.value.problems)
    assert any(problem.startswith("changed ") for problem in error.value.problems)
    assert any(problem.startswith("unexpected ") for problem in error.value.problems)
    assert str(error.value).startswith("schema snapshot mismatch:")


def test_committed_snapshots_have_no_drift() -> None:
    paths = export_schema_snapshots(DEFAULT_SCHEMA_ROOT, check=True)
    assert len(paths) == len(EXPECTED_SCHEMA_IDS)


def test_direct_schema_export_script_is_bound_to_its_own_checkout(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[3]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = ""

    completed = subprocess.run(
        [sys.executable, str(repository_root / "script/export_harness_schemas.py"), "--check"],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == (
        f"verified {len(EXPECTED_SCHEMA_IDS)} Harness schema snapshots in {DEFAULT_SCHEMA_ROOT}"
    )
