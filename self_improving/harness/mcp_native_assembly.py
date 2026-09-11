"""Strict operator settings for the exact native image MCP assembly."""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .artifacts import LocalArtifactStore
from .genesis_asset_qualification_application import GenesisAssetQualificationApplication
from .genesis_fresh_capture import GenesisFreshCaptureProducer
from .genesis_fresh_capture_application import GenesisFreshCaptureApplication
from .genesis_scene_application_v2 import GenesisSceneApplication
from .genesis_scene_assessment import GenesisSceneAssessment
from .genesis_scene_execution_v2 import GenesisSceneExecutionProducerV2
from .genesis_scene_validation_application import GenesisSceneValidationApplication
from .golden_image2env_execution import create_native_image_execution_bindings
from .image2env_genesis_compile import Image2EnvGenesisCompiler
from .image2env_genesis_compile_application import Image2EnvGenesisCompileApplication
from .schemas.common import ArtifactRef


def _exact_json_ref(value: ArtifactRef, schema: str) -> ArtifactRef:
    if (
        value.schema_version != schema
        or value.media_type != "application/json"
        or value.uri != f"artifact://sha256/{value.sha256}"
    ):
        raise ValueError("operator evidence requires its exact JSON schema and CAS identity")
    return value


class NativeImageApplicationSettings(BaseModel):
    """Four qualified applications share CAS while retaining their own journals.

    Deployment roots are operator-owned. Empty roots allow historical recovery;
    runtime launchers require their complete verified deployment before execution.
    No production clock or child UUID override is accepted.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    cas_root: Path
    asset_state_path: Path
    compile_state_root: Path
    scene_state_root: Path
    observation_state_root: Path
    validation_state_root: Path
    compile_qualification: ArtifactRef
    scene_qualification: ArtifactRef
    observation_qualification: ArtifactRef
    validation_qualification: ArtifactRef
    asset_manifest: ArtifactRef
    scene_manifest: ArtifactRef
    capture_manifest: ArtifactRef
    scene_roots: dict[str, Path]
    capture_roots: dict[str, Path]

    @field_validator(
        "cas_root",
        "asset_state_path",
        "compile_state_root",
        "scene_state_root",
        "observation_state_root",
        "validation_state_root",
    )
    @classmethod
    def absolute_authority(cls, value: Path) -> Path:
        if not value.is_absolute() or ".." in value.parts:
            raise ValueError("native authority paths must be absolute")
        return value

    @field_validator("scene_roots", "capture_roots")
    @classmethod
    def exact_deployment(cls, value: dict[str, Path]) -> dict[str, Path]:
        if value and set(value) != {
            "backend",
            "distributions",
            "genesis",
            "interpreter",
            "native",
            "stdlib",
        }:
            raise ValueError("native deployments require all six exact runtime roles")
        if any(not path.is_absolute() or ".." in path.parts for path in value.values()):
            raise ValueError("native deployment paths must be absolute")
        return value

    @field_validator("asset_manifest", "scene_manifest", "capture_manifest")
    @classmethod
    def runtime_identity(cls, value: ArtifactRef) -> ArtifactRef:
        return _exact_json_ref(value, "harness.genesis_runtime_manifest.v1")

    @field_validator(
        "compile_qualification",
        "scene_qualification",
        "observation_qualification",
        "validation_qualification",
    )
    @classmethod
    def qualification_identity(cls, value: ArtifactRef) -> ArtifactRef:
        return _exact_json_ref(value, "harness.skill_qualification.v1")


class NativeImageMCPServerSettings(NativeImageApplicationSettings):
    """A frozen MCP authority adds workflow and input identities to restored apps."""

    schema_version: Literal["harness.native_image_mcp_server_settings.v1"]
    principal_id: Annotated[str, Field(min_length=1)]
    golden_database_path: Path
    compile_template_ref: ArtifactRef
    registry_snapshot: ArtifactRef

    @field_validator("golden_database_path")
    @classmethod
    def absolute_golden_authority(cls, value: Path) -> Path:
        if not value.is_absolute() or ".." in value.parts:
            raise ValueError("Golden authority path must be absolute")
        return value

    @model_validator(mode="after")
    def exact_server_authority(self):
        _exact_json_ref(self.compile_template_ref, "harness.image2env_genesis_compile_request.v1")
        _exact_json_ref(self.registry_snapshot, "harness.registry_snapshot.v1")
        databases = (
            self.asset_state_path,
            self.golden_database_path,
            self.compile_state_root / "harness.sqlite3",
            self.scene_state_root / "runs.sqlite",
            self.observation_state_root / "runs.sqlite",
            self.validation_state_root / "runs.sqlite",
        )
        if len({path.resolve() for path in databases}) != len(databases):
            raise ValueError("native applications and Golden require distinct SQLite authorities")
        return self


@dataclass(frozen=True)
class RestoredNativeImageApplications:
    """Restored applications, not a replacement for their durable authority."""

    store: LocalArtifactStore
    compile: Image2EnvGenesisCompileApplication
    replay: GenesisSceneApplication
    observe: GenesisFreshCaptureApplication
    validate: GenesisSceneValidationApplication

    def execution_bindings(self):
        return create_native_image_execution_bindings(
            compile_application=self.compile,
            replay_application=self.replay,
            observation_application=self.observe,
            validation_application=self.validate,
        )


def restore_native_image_applications(
    settings: NativeImageApplicationSettings,
) -> RestoredNativeImageApplications:
    """Reopen all four registered M1 apps without executing or requalifying any Skill.

    Operators supply an isolated writable authority deployment. This function never
    copies evidence or falls back to another interpreter to bypass identity checks.
    """

    if type(settings) not in (NativeImageApplicationSettings, NativeImageMCPServerSettings):
        raise TypeError("restore requires exact native application or MCP server settings")
    databases = (
        settings.asset_state_path,
        settings.compile_state_root / "harness.sqlite3",
        settings.scene_state_root / "runs.sqlite",
        settings.observation_state_root / "runs.sqlite",
        settings.validation_state_root / "runs.sqlite",
    )
    if not (settings.cas_root / "sha256").is_dir() or any(not path.is_file() for path in databases):
        raise ValueError("restore requires existing native CAS and all five authority journals")
    store = LocalArtifactStore(settings.cas_root)
    compiler = Image2EnvGenesisCompiler(
        store,
        GenesisAssetQualificationApplication(
            settings.asset_state_path, store, settings.asset_manifest
        ),
    )
    compile_app = Image2EnvGenesisCompileApplication(
        settings.compile_state_root, compiler, qualification=settings.compile_qualification
    )
    replay = GenesisSceneApplication(
        settings.scene_state_root,
        GenesisSceneExecutionProducerV2(store, compiler, settings.scene_manifest),
        roots=settings.scene_roots,
        compile_application=compile_app,
    )
    replay.bind(settings.scene_qualification)
    observe = GenesisFreshCaptureApplication(
        settings.observation_state_root,
        GenesisFreshCaptureProducer(store, replay, settings.capture_manifest),
        roots=settings.capture_roots,
    )
    observe.bind(settings.observation_qualification)
    validate = GenesisSceneValidationApplication(
        settings.validation_state_root, GenesisSceneAssessment(replay)
    )
    validate.bind(settings.validation_qualification)
    return RestoredNativeImageApplications(store, compile_app, replay, observe, validate)
