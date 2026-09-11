"""Operator-owned lazy restore for one CAS and the existing Golden journal."""

from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .artifacts import LocalArtifactStore
from .golden_execution import _create_golden_execution_binding
from .golden_image2env_execution import _NativeApplicationAdapter
from .golden_image2env_policy import NativeImageEvidencePolicy
from .golden_store import SQLiteGoldenRunStore
from .limited_image_preview_inputs import LimitedImagePreviewInputs
from .mcp_native_assembly import NativeImageApplicationSettings
from .schemas.common import ArtifactRef


class PreviewProjectionReferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    admission_ref: ArtifactRef
    descriptor_ref: ArtifactRef
    qualification_ref: ArtifactRef
    qualification_report_ref: ArtifactRef


class LimitedImagePreviewSettings(NativeImageApplicationSettings):
    """Deployment facts, never caller clocks, executable hooks or fabricated runtime results."""

    schema_version: Literal["harness.limited_image_preview_settings.v1"]
    principal_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    golden_database_path: Path
    registry_snapshot: ArtifactRef
    asset_catalog: ArtifactRef
    source_scene: ArtifactRef
    qualification_run_id: UUID
    table_center_m: tuple[float, float, float]
    table_size_m: tuple[float, float, float]
    table_friction: float = Field(ge=0, allow_inf_nan=False)
    seed: int = Field(ge=0)
    revised_state_root: Path
    revised_qualification: ArtifactRef
    shared_state_root: Path
    library_root: Path
    native_projections: dict[
        Literal["image2env.compile@2.0.0", "image2env.replay@2.0.0", "genesis.observe@3.0.0"],
        PreviewProjectionReferences,
    ]
    revised_projection: PreviewProjectionReferences | None = None
    shared_qualifications: dict[
        Literal[
            "image2env.replay@3.0.0", "image2env.validate@3.0.0", "image2env.preview_publish@1.0.0"
        ],
        ArtifactRef,
    ]

    @field_validator(
        "golden_database_path", "revised_state_root", "shared_state_root", "library_root"
    )
    @classmethod
    def absolute_storage(cls, value):
        if not value.is_absolute() or ".." in value.parts:
            raise ValueError("preview storage must be absolute")
        return value


class LimitedPreviewRuntime:
    """Per-process restored objects; every business operation remains in durable authorities."""

    def __init__(self, settings, store):
        if (
            type(settings) is not LimitedImagePreviewSettings
            or type(store) is not LocalArtifactStore
        ):
            raise TypeError("lazy runtime requires exact settings and CAS")
        self.settings, self.store = settings, store
        self.apps = {}
        self.bindings = {}

    def history_binding(self, skill):
        """No live app/qualification restore for native metadata and diagnosis reads.

        Golden still checks the entire committed receipt chain and exact child
        SQLite/CAS. Receipts carrying extra terminal roots require their hook and
        are deliberately rejected by Golden instead of silently dropping roots.
        """
        from .limited_image_preview_workflow_policies import LimitedPreviewObservationPolicy
        from .run_store import SQLiteRunStore

        paths = {
            "image2env.compile@2.0.0": self.settings.compile_state_root / "harness.sqlite3",
            "image2env.replay@2.0.0": self.settings.scene_state_root / "runs.sqlite",
            "genesis.observe@3.0.0": self.settings.observation_state_root / "runs.sqlite",
        }
        if skill in paths:
            needed = {skill: paths[skill]}
            if skill != "image2env.compile@2.0.0":
                prior = (
                    "image2env.compile@2.0.0"
                    if skill == "image2env.replay@2.0.0"
                    else "image2env.replay@2.0.0"
                )
                needed[prior] = paths[prior]
            if any(not path.is_file() for path in needed.values()):
                raise ValueError("history requires its existing child SQLite authority")
            authorities = {name: SQLiteRunStore(path) for name, path in needed.items()}
            policy_type = (
                LimitedPreviewObservationPolicy
                if skill == "genesis.observe@3.0.0"
                else NativeImageEvidencePolicy
            )
            policy = policy_type(self.store, authorities[skill], skill, authorities)
            return _create_golden_execution_binding(
                policy=policy, application=None, child_authority=authorities[skill]
            )
        # Shared/J3 histories keep their existing exact policy; no substitute validator.
        self.ensure(skill)
        live = self.bindings[skill]
        return _create_golden_execution_binding(
            policy=live.policy, application=None, child_authority=live.child_authority
        )

    def application(self, key):
        if key in self.apps:
            return self.apps[key]
        s = self.settings
        if key == "compile":
            from .genesis_asset_qualification_application import (
                GenesisAssetQualificationApplication,
            )
            from .image2env_genesis_compile import Image2EnvGenesisCompiler
            from .image2env_genesis_compile_application import Image2EnvGenesisCompileApplication

            compiler = Image2EnvGenesisCompiler(
                self.store,
                GenesisAssetQualificationApplication(
                    s.asset_state_path, self.store, s.asset_manifest
                ),
            )
            app = Image2EnvGenesisCompileApplication(
                s.compile_state_root, compiler, qualification=s.compile_qualification
            )
        elif key == "replay":
            from .genesis_scene_application_v2 import GenesisSceneApplication
            from .genesis_scene_execution_v2 import GenesisSceneExecutionProducerV2

            compile_app = self.application("compile")
            app = GenesisSceneApplication(
                s.scene_state_root,
                GenesisSceneExecutionProducerV2(
                    self.store, compile_app._compiler, s.scene_manifest
                ),
                roots=s.scene_roots,
                compile_application=compile_app,
            )
            app.bind(s.scene_qualification)
        elif key == "observe":
            from .genesis_fresh_capture import GenesisFreshCaptureProducer
            from .genesis_fresh_capture_application import GenesisFreshCaptureApplication

            app = GenesisFreshCaptureApplication(
                s.observation_state_root,
                GenesisFreshCaptureProducer(
                    self.store, self.application("replay"), s.capture_manifest
                ),
                roots=s.capture_roots,
            )
            app.bind(s.observation_qualification)
        elif key == "revised":
            from .revised_image_compile_application import RevisedImageCompileApplication
            from .revised_image_compiler import RevisedImage2EnvGenesisCompiler

            app = RevisedImageCompileApplication(
                s.revised_state_root,
                RevisedImage2EnvGenesisCompiler(self.store, self.application("compile")._compiler),
                qualification=s.revised_qualification,
            )
        elif key == "shared":
            from .genesis_scene_execution_v3 import GenesisSceneExecutionProducerV3
            from .limited_image_preview_application import LimitedImagePreviewApplication

            compile_app = self.application("revised")
            app = LimitedImagePreviewApplication(
                s.shared_state_root,
                compile_app,
                GenesisSceneExecutionProducerV3(self.store, compile_app.compiler, s.scene_manifest),
                roots=s.scene_roots,
                library=self.application("library"),
            )
        elif key == "library":
            from .limited_image_preview_library import (
                create_limited_image_preview_library,
                load_limited_image_preview_library,
            )

            factory = (
                load_limited_image_preview_library
                if s.library_root.exists()
                else create_limited_image_preview_library
            )
            app = factory(
                artifact_store=self.store,
                compiler=self.application("revised").compiler,
                root=s.library_root,
                project_id=s.project_id,
            )
        else:
            raise ValueError("unsupported exact preview application")
        self.apps[key] = app
        return app

    def ensure(self, skill):
        if skill in self.bindings:
            return
        if skill in self.settings.native_projections:
            from .native_skill_projection import load_native_skill_projection

            key = {
                "image2env.compile@2.0.0": "compile",
                "image2env.replay@2.0.0": "replay",
                "genesis.observe@3.0.0": "observe",
            }[skill]
            app = self.application(key)
            predecessors = {}
            if key != "compile":
                prior = "compile" if key == "replay" else "replay"
                prior_app = self.application(prior)
                predecessors[f"image2env.{prior}@2.0.0"] = (
                    prior_app._run_store if prior == "compile" else prior_app.run_store
                )
            authority = app._run_store if key == "compile" else app.run_store
            policy = NativeImageEvidencePolicy(self.store, authority, skill, predecessors)
            binding = _create_golden_execution_binding(
                policy=policy,
                application=_NativeApplicationAdapter(app, policy),
                child_authority=authority,
            )
            refs = self.settings.native_projections[skill]
            if key == "observe":
                from .limited_observation_registry_projection import (
                    load_limited_observation_registry_projection,
                )

                projection = load_limited_observation_registry_projection(
                    application=app,
                    replay_authority=self.application("replay").run_store,
                    **refs.model_dump(mode="python"),
                )
            else:
                projection = load_native_skill_projection(
                    artifact_store=self.store,
                    native_binding=binding,
                    **refs.model_dump(mode="python"),
                )
            self.bindings[skill] = projection.binding
        elif skill == "image2env.compile@3.0.0":
            from .limited_image_preview_policy import LimitedImagePreviewPolicy
            from .revised_compile_registry_projection import (
                load_revised_compile_registry_projection,
            )

            refs = self.settings.revised_projection
            if refs is None:
                raise ValueError("revised compile requires its sealed Registry projection")
            app = self.application("revised")
            projection = load_revised_compile_registry_projection(
                application=app,
                policy=LimitedImagePreviewPolicy(app, skill, {}),
                **refs.model_dump(mode="python"),
            )
            self.bindings[skill] = projection.binding
        elif skill in {
            "image2env.replay@3.0.0",
            "image2env.validate@3.0.0",
            "image2env.preview_publish@1.0.0",
        }:
            from .limited_image_preview_policy import create_shared_preview_bindings
            from .limited_image_preview_workflow_policies import create_limited_validation_binding

            qualification = self.settings.shared_qualifications.get(skill)
            if qualification is None:
                raise ValueError("shared preview Skill is not yet qualified")
            app = self.application("shared")
            app.bind(qualification)
            authorities = {
                "image2env.compile@3.0.0": self.application("revised").run_store,
                "image2env.replay@3.0.0": app.run_store,
                "image2env.replay@2.0.0": self.application("replay").run_store,
            }
            if skill == "image2env.preview_publish@1.0.0":
                from .limited_image_preview_publication_policy import (
                    create_limited_publication_binding,
                )

                binding = create_limited_publication_binding(app)
            elif skill == "image2env.validate@3.0.0":
                authorities["genesis.observe@3.0.0"] = self.application("observe").run_store
                binding = create_limited_validation_binding(
                    app, predecessor_authorities=authorities
                )
            else:
                binding = next(
                    b
                    for b in create_shared_preview_bindings(
                        app, predecessor_authorities=authorities
                    )
                    if b.skill_ref == skill
                )
            self.bindings[skill] = binding
        else:
            raise ValueError("preview Skill lacks an exact configured registered binding")


def create_limited_image_preview_service(settings_path):
    """Start/status/lookup require no runtime restoration; execution restores on demand."""
    from .limited_image_preview_service import LimitedImagePreviewService

    settings = LimitedImagePreviewSettings.model_validate_json(Path(settings_path).read_bytes())
    if not (settings.cas_root / "sha256").is_dir():
        raise ValueError("preview requires its existing isolated CAS")
    store = LocalArtifactStore(settings.cas_root)
    inputs = LimitedImagePreviewInputs(
        store,
        asset_catalog=settings.asset_catalog,
        source_scene=settings.source_scene,
        qualification_run_id=settings.qualification_run_id,
        table_center_m=settings.table_center_m,
        table_size_m=settings.table_size_m,
        table_friction=settings.table_friction,
        seed=settings.seed,
    )
    return LimitedImagePreviewService(
        artifact_store=store,
        workflow_store=SQLiteGoldenRunStore(settings.golden_database_path),
        input_builder=inputs,
        registry_snapshot=settings.registry_snapshot,
        principal_id=settings.principal_id,
        scene_manifest=settings.scene_manifest,
        runtime=LimitedPreviewRuntime(settings, store),
    )
