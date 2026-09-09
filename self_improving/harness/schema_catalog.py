"""Immutable catalog and reproducible snapshots for public Harness schemas."""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .schemas import (
    ARTIFACT_REF_SCHEMA_ID,
    ASSET_DEBT_INVENTORY_SCHEMA_ID,
    ASSET_REPAIR_PLAN_REQUEST_SCHEMA_ID,
    ASSET_REPAIR_PLAN_SCHEMA_ID,
    ASSET_SOURCE_SNAPSHOT_MANIFEST_SCHEMA_ID,
    ASSET_STAGE_REQUEST_SCHEMA_ID,
    ASSET_STAGE_RESULT_SCHEMA_ID,
    BLOCKER_SCHEMA_ID,
    ENVIRONMENT_PACKAGE_SCHEMA_ID,
    EVENT_SCHEMA_ID,
    REGISTRY_SNAPSHOT_SCHEMA_ID,
    REPLAY_VLM_ASSESSMENT_INPUT_SCHEMA_ID,
    REPLAY_VLM_ASSESSMENT_OUTPUT_SCHEMA_ID,
    REPLAY_VLM_ASSESSMENT_OUTPUT_V2_SCHEMA_ID,
    RUN_READ_REQUEST_SCHEMA_ID,
    RUN_SNAPSHOT_SCHEMA_ID,
    RUN_START_REQUEST_SCHEMA_ID,
    RUN_STATE_SCHEMA_ID,
    SKILL_DESCRIPTOR_SCHEMA_ID,
    SKILL_DESCRIPTOR_V2_SCHEMA_ID,
    SKILL_INVOCATION_SCHEMA_ID,
    SKILL_QUALIFICATION_SCHEMA_ID,
    TEXT2ENV_COMPILE_INPUT_SCHEMA_ID,
    TEXT2ENV_COMPILE_OUTPUT_SCHEMA_ID,
    TEXT2ENV_REPLAY_INPUT_SCHEMA_ID,
    TEXT2ENV_REPLAY_OUTPUT_SCHEMA_ID,
    TEXT2ENV_VALIDATE_INPUT_SCHEMA_ID,
    TEXT2ENV_VALIDATE_OUTPUT_SCHEMA_ID,
    TEXT2ENV_VALIDATE_V2_INPUT_SCHEMA_ID,
    TEXT2ENV_VALIDATE_V2_OUTPUT_SCHEMA_ID,
    WORKFLOW_START_RECEIPT_SCHEMA_ID,
    ArtifactRef,
    AssetDebtInventory,
    AssetRepairPlan,
    AssetRepairPlanRequest,
    AssetSourceSnapshotManifest,
    AssetStageRequest,
    AssetStageResult,
    Blocker,
    EnvironmentPackage,
    Event,
    Invocation,
    RegistrySnapshot,
    ReplayVlmAssessmentInput,
    ReplayVlmAssessmentOutput,
    ReplayVlmAssessmentOutputV2,
    RunReadRequest,
    RunSnapshot,
    RunStartRequest,
    RunState,
    SkillDescriptor,
    SkillDescriptorV2,
    SkillQualification,
    Text2EnvCompileInput,
    Text2EnvCompileOutput,
    Text2EnvReplayInput,
    Text2EnvReplayOutput,
    Text2EnvValidateInput,
    Text2EnvValidateOutput,
    Text2EnvValidateV2Input,
    Text2EnvValidateV2Output,
    WorkflowStartReceipt,
)
from .schemas.base import HarnessModel
from .schemas.qualification_report import (
    QUALIFICATION_REPORT_SCHEMA_ID,
    PublicQualificationReportV1,
)

DEFAULT_SCHEMA_ROOT = Path(__file__).with_name("json_schemas")

_SCHEMA_MODELS: dict[str, type[HarnessModel]] = {
    ARTIFACT_REF_SCHEMA_ID: ArtifactRef,
    ASSET_DEBT_INVENTORY_SCHEMA_ID: AssetDebtInventory,
    ASSET_REPAIR_PLAN_REQUEST_SCHEMA_ID: AssetRepairPlanRequest,
    ASSET_REPAIR_PLAN_SCHEMA_ID: AssetRepairPlan,
    ASSET_SOURCE_SNAPSHOT_MANIFEST_SCHEMA_ID: AssetSourceSnapshotManifest,
    ASSET_STAGE_REQUEST_SCHEMA_ID: AssetStageRequest,
    ASSET_STAGE_RESULT_SCHEMA_ID: AssetStageResult,
    BLOCKER_SCHEMA_ID: Blocker,
    ENVIRONMENT_PACKAGE_SCHEMA_ID: EnvironmentPackage,
    EVENT_SCHEMA_ID: Event,
    RUN_STATE_SCHEMA_ID: RunState,
    RUN_SNAPSHOT_SCHEMA_ID: RunSnapshot,
    RUN_START_REQUEST_SCHEMA_ID: RunStartRequest,
    REPLAY_VLM_ASSESSMENT_INPUT_SCHEMA_ID: ReplayVlmAssessmentInput,
    REPLAY_VLM_ASSESSMENT_OUTPUT_SCHEMA_ID: ReplayVlmAssessmentOutput,
    REPLAY_VLM_ASSESSMENT_OUTPUT_V2_SCHEMA_ID: ReplayVlmAssessmentOutputV2,
    REGISTRY_SNAPSHOT_SCHEMA_ID: RegistrySnapshot,
    RUN_READ_REQUEST_SCHEMA_ID: RunReadRequest,
    SKILL_DESCRIPTOR_SCHEMA_ID: SkillDescriptor,
    SKILL_DESCRIPTOR_V2_SCHEMA_ID: SkillDescriptorV2,
    SKILL_INVOCATION_SCHEMA_ID: Invocation,
    SKILL_QUALIFICATION_SCHEMA_ID: SkillQualification,
    QUALIFICATION_REPORT_SCHEMA_ID: PublicQualificationReportV1,
    TEXT2ENV_COMPILE_INPUT_SCHEMA_ID: Text2EnvCompileInput,
    TEXT2ENV_COMPILE_OUTPUT_SCHEMA_ID: Text2EnvCompileOutput,
    TEXT2ENV_REPLAY_INPUT_SCHEMA_ID: Text2EnvReplayInput,
    TEXT2ENV_REPLAY_OUTPUT_SCHEMA_ID: Text2EnvReplayOutput,
    TEXT2ENV_VALIDATE_INPUT_SCHEMA_ID: Text2EnvValidateInput,
    TEXT2ENV_VALIDATE_OUTPUT_SCHEMA_ID: Text2EnvValidateOutput,
    TEXT2ENV_VALIDATE_V2_INPUT_SCHEMA_ID: Text2EnvValidateV2Input,
    TEXT2ENV_VALIDATE_V2_OUTPUT_SCHEMA_ID: Text2EnvValidateV2Output,
    WORKFLOW_START_RECEIPT_SCHEMA_ID: WorkflowStartReceipt,
}
SCHEMA_MODELS: Mapping[str, type[HarnessModel]] = MappingProxyType(_SCHEMA_MODELS)


class SchemaSnapshotMismatch(RuntimeError):
    """Raised when committed JSON Schema snapshots differ from the models."""

    def __init__(self, problems: tuple[str, ...]):
        self.problems = problems
        super().__init__("schema snapshot mismatch: " + "; ".join(problems))


def schema_model(schema_id: str) -> type[HarnessModel]:
    """Resolve one exact public schema identifier."""

    return SCHEMA_MODELS[schema_id]


def schema_documents() -> dict[str, dict[str, object]]:
    """Build every public JSON Schema document in stable identifier order."""

    return {
        schema_id: SCHEMA_MODELS[schema_id].model_json_schema(mode="validation")
        for schema_id in sorted(SCHEMA_MODELS)
    }


def schema_snapshot_name(schema_id: str) -> str:
    return f"{schema_id}.schema.json"


def render_schema_document(document: dict[str, object]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def expected_schema_snapshots() -> dict[str, str]:
    return {
        schema_snapshot_name(schema_id): render_schema_document(document)
        for schema_id, document in schema_documents().items()
    }


def export_schema_snapshots(
    root: Path = DEFAULT_SCHEMA_ROOT,
    *,
    check: bool = False,
) -> tuple[Path, ...]:
    """Write snapshots or fail if an existing snapshot directory has drifted."""

    expected = expected_schema_snapshots()
    paths = tuple(root / name for name in sorted(expected))
    if check:
        existing_names = {path.name for path in root.glob("*.schema.json")}
        expected_names = set(expected)
        problems = [f"missing {name}" for name in sorted(expected_names - existing_names)]
        problems.extend(f"unexpected {name}" for name in sorted(existing_names - expected_names))
        for name in sorted(expected_names & existing_names):
            if (root / name).read_text(encoding="utf-8") != expected[name]:
                problems.append(f"changed {name}")
        if problems:
            raise SchemaSnapshotMismatch(tuple(problems))
        return paths

    root.mkdir(parents=True, exist_ok=True)
    for path in paths:
        path.write_text(expected[path.name], encoding="utf-8")
    return paths
