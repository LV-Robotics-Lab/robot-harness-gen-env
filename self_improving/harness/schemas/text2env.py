"""Harness-facing Text2Env input, output, and package records."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StrictBool, model_validator

from .base import (
    CanonicalSkillRef,
    HarnessModel,
    NonNegativeInt,
    PositiveInt,
    Seed,
    Sha256,
    public_schema_config,
)
from .common import ArtifactRef, Blocker, ValidationStatus

TEXT2ENV_COMPILE_INPUT_SCHEMA_ID = "harness.text2env_compile_input.v1"
TEXT2ENV_COMPILE_OUTPUT_SCHEMA_ID = "harness.text2env_compile_output.v1"
TEXT2ENV_REPLAY_INPUT_SCHEMA_ID = "harness.text2env_replay_input.v1"
TEXT2ENV_REPLAY_OUTPUT_SCHEMA_ID = "harness.text2env_replay_output.v1"
TEXT2ENV_VALIDATE_INPUT_SCHEMA_ID = "harness.text2env_validate_input.v1"
TEXT2ENV_VALIDATE_OUTPUT_SCHEMA_ID = "harness.text2env_validate_output.v1"
ENVIRONMENT_PACKAGE_SCHEMA_ID = "harness.environment_package.v1"

ASSET_CATALOG_SCHEMA_VERSION = "robotwin.asset_catalog.v1"
SCENE_SPEC_SCHEMA_VERSION = "robotwin.scene_spec.v1"
RESOLVED_SCENE_SCHEMA_VERSION = "robotwin.resolved_scene.v1"
PACKAGE_MANIFEST_SCHEMA_VERSION = "robotwin.generated_scene_package.v1"
RUNTIME_EVIDENCE_SCHEMA_VERSION = "robotwin.scene_runtime_evidence.v2"
VALIDATION_REPORT_SCHEMA_VERSION = "robotwin.scene_validation.v1"
GATE_PROFILE = VALIDATION_REPORT_SCHEMA_VERSION
RequestText = Annotated[str, Field(strict=True, min_length=3, max_length=2000)]


def _require_artifact_schema(
    artifact: ArtifactRef,
    expected_schema: str,
    field_name: str,
) -> None:
    if artifact.schema_version != expected_schema:
        raise ValueError(f"{field_name}.schema_version must be {expected_schema!r}")


class CompileConfig(HarnessModel):
    generate_missing_assets: StrictBool = False


class RuntimeConfig(HarnessModel):
    precheck_steps: NonNegativeInt = 0
    settle_steps: PositiveInt = 900
    contact_window_steps: PositiveInt = 120
    video_frames: NonNegativeInt = 120
    fps: PositiveInt = 12


class EnvironmentPackage(HarnessModel):
    model_config = public_schema_config(ENVIRONMENT_PACKAGE_SCHEMA_ID)

    package_id: Sha256
    route_id: Literal["text2env"]
    producer_skill_ref: CanonicalSkillRef
    seed: Seed
    scene_spec_sha256: Sha256
    resolved_scene_sha256: Sha256
    asset_catalog: ArtifactRef
    package_manifest: ArtifactRef

    @model_validator(mode="after")
    def package_is_hash_bound(self) -> "EnvironmentPackage":
        if self.package_id != self.resolved_scene_sha256:
            raise ValueError("package_id must equal resolved_scene_sha256")
        if not self.producer_skill_ref.startswith("text2env.compile@"):
            raise ValueError("producer_skill_ref must identify text2env.compile")
        _require_artifact_schema(
            self.asset_catalog,
            ASSET_CATALOG_SCHEMA_VERSION,
            "asset_catalog",
        )
        _require_artifact_schema(
            self.package_manifest,
            PACKAGE_MANIFEST_SCHEMA_VERSION,
            "package_manifest",
        )
        return self


class Text2EnvCompileInput(HarnessModel):
    model_config = public_schema_config(TEXT2ENV_COMPILE_INPUT_SCHEMA_ID)

    request: RequestText
    seed: Seed
    asset_catalog: ArtifactRef
    config: CompileConfig

    @model_validator(mode="after")
    def catalog_is_typed(self) -> "Text2EnvCompileInput":
        _require_artifact_schema(
            self.asset_catalog,
            ASSET_CATALOG_SCHEMA_VERSION,
            "asset_catalog",
        )
        return self


class Text2EnvCompileOutput(HarnessModel):
    model_config = public_schema_config(TEXT2ENV_COMPILE_OUTPUT_SCHEMA_ID)

    scene_spec: ArtifactRef
    resolved_scene: ArtifactRef
    environment_package: EnvironmentPackage
    static_validation: ArtifactRef

    @model_validator(mode="after")
    def output_artifacts_are_typed(self) -> "Text2EnvCompileOutput":
        _require_artifact_schema(self.scene_spec, SCENE_SPEC_SCHEMA_VERSION, "scene_spec")
        _require_artifact_schema(
            self.resolved_scene,
            RESOLVED_SCENE_SCHEMA_VERSION,
            "resolved_scene",
        )
        _require_artifact_schema(
            self.static_validation,
            VALIDATION_REPORT_SCHEMA_VERSION,
            "static_validation",
        )
        return self


class Text2EnvReplayInput(HarnessModel):
    model_config = public_schema_config(TEXT2ENV_REPLAY_INPUT_SCHEMA_ID)

    environment_package: EnvironmentPackage
    runtime_config: RuntimeConfig


class Text2EnvReplayOutput(HarnessModel):
    model_config = public_schema_config(TEXT2ENV_REPLAY_OUTPUT_SCHEMA_ID)

    runtime_evidence: ArtifactRef
    replay_artifacts: tuple[ArtifactRef, ...]

    @model_validator(mode="after")
    def runtime_evidence_is_typed(self) -> "Text2EnvReplayOutput":
        _require_artifact_schema(
            self.runtime_evidence,
            RUNTIME_EVIDENCE_SCHEMA_VERSION,
            "runtime_evidence",
        )
        return self


class Text2EnvValidateInput(HarnessModel):
    model_config = public_schema_config(TEXT2ENV_VALIDATE_INPUT_SCHEMA_ID)

    environment_package: EnvironmentPackage
    runtime_evidence: ArtifactRef
    gate_profile: Literal[GATE_PROFILE] = GATE_PROFILE

    @model_validator(mode="after")
    def runtime_evidence_is_typed(self) -> "Text2EnvValidateInput":
        _require_artifact_schema(
            self.runtime_evidence,
            RUNTIME_EVIDENCE_SCHEMA_VERSION,
            "runtime_evidence",
        )
        return self


class Text2EnvValidateOutput(HarnessModel):
    model_config = public_schema_config(TEXT2ENV_VALIDATE_OUTPUT_SCHEMA_ID)

    validation_report: ArtifactRef
    validation_status: ValidationStatus
    publishable: StrictBool
    blockers: tuple[Blocker, ...]

    @model_validator(mode="after")
    def publication_state_is_consistent(self) -> "Text2EnvValidateOutput":
        _require_artifact_schema(
            self.validation_report,
            VALIDATION_REPORT_SCHEMA_VERSION,
            "validation_report",
        )
        if self.publishable:
            if self.validation_status != ValidationStatus.PASS or self.blockers:
                raise ValueError(
                    "publishable output requires validation_status=pass and no blockers"
                )
        elif not self.blockers:
            raise ValueError("non-publishable output requires at least one blocker")
        return self
