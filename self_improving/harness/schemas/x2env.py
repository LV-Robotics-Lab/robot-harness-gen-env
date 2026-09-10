"""Strict contract-only schemas for image/video to Genesis environment Skills.

These records describe the public seams.  They do not register handlers, call a model,
or claim that the legacy Gujie output has passed a current Harness replay.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import Field, StrictBool, model_validator

from .base import (
    CanonicalSkillRef,
    HarnessModel,
    NonEmptyString,
    NonNegativeInt,
    PositiveInt,
    Seed,
    Sha256,
    public_schema_config,
)
from .common import ArtifactRef, Blocker, ValidationStatus
from .compile_attempt import CompileAttemptV1

IMAGE_SOURCE_MEDIA_SCHEMA_ID = "harness.image_source_media.v1"
VIDEO_SOURCE_MEDIA_SCHEMA_ID = "harness.video_source_media.v1"
X2ENV_ASSET_DECISION_SCHEMA_ID = "harness.x2env_asset_decision.v1"
X2ENV_ENVIRONMENT_PACKAGE_SCHEMA_ID = "harness.x2env_environment_package.v1"

IMAGE2ENV_COMPILE_INPUT_SCHEMA_ID = "harness.image2env_compile_input.v1"
IMAGE2ENV_COMPILE_V2_INPUT_SCHEMA_ID = "harness.image2env_compile_input.v2"
IMAGE2ENV_COMPILE_OUTPUT_SCHEMA_ID = "harness.image2env_compile_output.v1"
IMAGE2ENV_REPLAY_INPUT_SCHEMA_ID = "harness.image2env_replay_input.v1"
IMAGE2ENV_REPLAY_OUTPUT_SCHEMA_ID = "harness.image2env_replay_output.v1"
IMAGE2ENV_VALIDATE_INPUT_SCHEMA_ID = "harness.image2env_validate_input.v1"
IMAGE2ENV_VALIDATE_OUTPUT_SCHEMA_ID = "harness.image2env_validate_output.v1"
VIDEO2ENV_COMPILE_INPUT_SCHEMA_ID = "harness.video2env_compile_input.v1"
VIDEO2ENV_COMPILE_V2_INPUT_SCHEMA_ID = "harness.video2env_compile_input.v2"
VIDEO2ENV_COMPILE_OUTPUT_SCHEMA_ID = "harness.video2env_compile_output.v1"
VIDEO2ENV_REPLAY_INPUT_SCHEMA_ID = "harness.video2env_replay_input.v1"
VIDEO2ENV_REPLAY_OUTPUT_SCHEMA_ID = "harness.video2env_replay_output.v1"
VIDEO2ENV_VALIDATE_INPUT_SCHEMA_ID = "harness.video2env_validate_input.v1"
VIDEO2ENV_VALIDATE_OUTPUT_SCHEMA_ID = "harness.video2env_validate_output.v1"

IMAGE_MEDIA_PROBE_SCHEMA = "harness.image_media_probe.v1"
VIDEO_MEDIA_PROBE_SCHEMA = "harness.video_media_probe.v1"
VIDEO_FRAME_SEQUENCE_SCHEMA = "harness.video_frame_sequence.v1"
SCENE_INTERPRETATION_SCHEMA = "harness.scene_interpretation_proposal.v1"
PORTABLE_ASSET_CATALOG_SCHEMA = "harness.portable_asset_catalog.v1"
SCENE_IR_SCHEMA = "harness.scene_ir.v1"
TASK_IR_SCHEMA = "harness.task_ir.v1"
ASSET_CLOSURE_SCHEMA = "harness.asset_closure_manifest.v1"
BINDING_MANIFEST_SCHEMA = "harness.x2env_binding_manifest.v1"
GENESIS_RUNTIME_LOCK_SCHEMA = "harness.genesis_runtime_lock.v1"
LEGACY_ADAPTER_RECEIPT_SCHEMA = "harness.legacy_genenv_adapter_receipt.v1"
ASSET_REPRESENTATION_SCHEMA = "harness.asset_representation.v1"
ASSET_SELECTION_SCHEMA = "harness.x2env_asset_selection.v1"
ASSET_QUALIFICATION_SCHEMA = "harness.asset_qualification.v1"
DIGITAL_COUSIN_SCHEMA = "harness.digital_cousin_equivalence.v1"
ASSET_GENERATION_RECEIPT_SCHEMA = "harness.asset_generation_receipt.v1"
STAGED_ASSET_SCHEMA = "harness.staged_asset_candidate.v1"
STATIC_VALIDATION_SCHEMA = "harness.x2env_static_validation.v1"
COMPILE_RECEIPT_SCHEMA = "harness.x2env_compile_receipt.v1"
GENESIS_RUNTIME_ATTESTATION_SCHEMA = "harness.genesis_runtime_attestation.v1"
GENESIS_RUNTIME_EVIDENCE_SCHEMA = "harness.genesis_runtime_evidence.v1"
GENESIS_PHYSICS_REPORT_SCHEMA = "harness.genesis_physics_report.v1"
GENESIS_ROBOT_PROBE_SCHEMA = "harness.genesis_robot_probe.v1"
GENESIS_TRAJECTORY_SCHEMA = "harness.genesis_trajectory.v1"
MEDIA_VERIFICATION_SCHEMA = "harness.x2env_media_verification.v1"
RUNTIME_RECEIPT_SCHEMA = "harness.x2env_runtime_receipt.v1"
FRESH_OBSERVATION_SCHEMA = "harness.fresh_observation.v1"
SKILL_QUALIFICATION_SCHEMA = "harness.skill_qualification.v1"
VALIDATION_DECISION_SCHEMA = "harness.x2env_validation_decision.v1"


class RequestedGenesisProfile(str, Enum):
    RIGID_SCENE = "genesis.rigid_scene@1"
    ROBOT_POLICY = "genesis.robot_policy@1"


class AcquisitionPolicy(str, Enum):
    AUTO = "auto"
    EXACT_ONLY = "exact_only"
    DIGITAL_COUSIN_ALLOWED = "digital_cousin_allowed"
    GENERATE_ON_MISS = "generate_on_miss"


class AssetAcquisitionLane(str, Enum):
    ENVIRONMENT_REUSE = "environment_reuse"
    EXACT_ASSET_REUSE = "exact_asset_reuse"
    DIGITAL_COUSIN_REUSE = "digital_cousin_reuse"
    FRESH_ASSET_GENERATION = "fresh_asset_generation"


class RepresentationOrigin(str, Enum):
    HARNESS_NATIVE = "harness_native"
    LEGACY_GENENV_ADAPTED = "legacy_genenv_adapted"


def _require_cas_ref(
    artifact: ArtifactRef,
    field_name: str,
    *,
    schema_version: str | None = None,
    media_type: str | None = None,
    media_prefix: str | None = None,
) -> None:
    if artifact.uri != f"artifact://sha256/{artifact.sha256}":
        raise ValueError(f"{field_name}.uri must be content-addressed by its sha256")
    if schema_version is not None and artifact.schema_version != schema_version:
        raise ValueError(f"{field_name}.schema_version must be {schema_version!r}")
    if media_type is not None and artifact.media_type != media_type:
        raise ValueError(f"{field_name}.media_type must be {media_type!r}")
    if media_prefix is not None and not artifact.media_type.startswith(media_prefix):
        raise ValueError(f"{field_name}.media_type must start with {media_prefix!r}")


class ImageSourceMedia(HarnessModel):
    model_config = public_schema_config(IMAGE_SOURCE_MEDIA_SCHEMA_ID)

    schema_version: Literal[IMAGE_SOURCE_MEDIA_SCHEMA_ID]
    source_media_sha256: Sha256
    source: ArtifactRef
    canonical_image: ArtifactRef
    probe: ArtifactRef
    width: PositiveInt
    height: PositiveInt
    color_mode: Literal["RGB", "RGBA", "L"]
    decoded_frame_count: Literal[1]
    unique_frame_count: Literal[1]

    @model_validator(mode="after")
    def identity_is_verifiable(self) -> "ImageSourceMedia":
        _require_cas_ref(self.source, "source", media_prefix="image/")
        _require_cas_ref(self.canonical_image, "canonical_image", media_type="image/png")
        _require_cas_ref(
            self.probe,
            "probe",
            schema_version=IMAGE_MEDIA_PROBE_SCHEMA,
            media_type="application/json",
        )
        if self.source_media_sha256 != self.source.sha256:
            raise ValueError("source_media_sha256 must equal source.sha256")
        return self


class VideoSourceMedia(HarnessModel):
    model_config = public_schema_config(VIDEO_SOURCE_MEDIA_SCHEMA_ID)

    schema_version: Literal[VIDEO_SOURCE_MEDIA_SCHEMA_ID]
    source_media_sha256: Sha256
    source: ArtifactRef
    probe: ArtifactRef
    frame_sequence_manifest: ArtifactRef
    width: PositiveInt
    height: PositiveInt
    duration_ms: PositiveInt
    fps_numerator: PositiveInt
    fps_denominator: PositiveInt
    decoded_frame_count: PositiveInt
    unique_frame_count: PositiveInt
    frame_sequence_sha256: Sha256

    @model_validator(mode="after")
    def identity_is_verifiable(self) -> "VideoSourceMedia":
        _require_cas_ref(self.source, "source", media_prefix="video/")
        _require_cas_ref(
            self.probe,
            "probe",
            schema_version=VIDEO_MEDIA_PROBE_SCHEMA,
            media_type="application/json",
        )
        _require_cas_ref(
            self.frame_sequence_manifest,
            "frame_sequence_manifest",
            schema_version=VIDEO_FRAME_SEQUENCE_SCHEMA,
            media_type="application/json",
        )
        if self.source_media_sha256 != self.source.sha256:
            raise ValueError("source_media_sha256 must equal source.sha256")
        if self.unique_frame_count > self.decoded_frame_count:
            raise ValueError("unique_frame_count cannot exceed decoded_frame_count")
        return self


class X2EnvAssetDecision(HarnessModel):
    model_config = public_schema_config(X2ENV_ASSET_DECISION_SCHEMA_ID)

    schema_version: Literal[X2ENV_ASSET_DECISION_SCHEMA_ID]
    object_id: NonEmptyString
    lane: AssetAcquisitionLane
    selected_artifact: ArtifactRef
    decision_evidence: ArtifactRef
    source_qualification: ArtifactRef | None
    digital_cousin_evidence: ArtifactRef | None
    generation_receipt: ArtifactRef | None

    @model_validator(mode="after")
    def lane_evidence_is_consistent(self) -> "X2EnvAssetDecision":
        _require_cas_ref(self.selected_artifact, "selected_artifact")
        _require_cas_ref(
            self.decision_evidence,
            "decision_evidence",
            schema_version=ASSET_SELECTION_SCHEMA,
            media_type="application/json",
        )
        if self.source_qualification is not None:
            _require_cas_ref(
                self.source_qualification,
                "source_qualification",
                schema_version=ASSET_QUALIFICATION_SCHEMA,
                media_type="application/json",
            )
        if self.digital_cousin_evidence is not None:
            _require_cas_ref(
                self.digital_cousin_evidence,
                "digital_cousin_evidence",
                schema_version=DIGITAL_COUSIN_SCHEMA,
                media_type="application/json",
            )
        if self.generation_receipt is not None:
            _require_cas_ref(
                self.generation_receipt,
                "generation_receipt",
                schema_version=ASSET_GENERATION_RECEIPT_SCHEMA,
                media_type="application/json",
            )

        if self.lane is AssetAcquisitionLane.DIGITAL_COUSIN_REUSE:
            if self.digital_cousin_evidence is None:
                raise ValueError("digital_cousin_evidence is required for digital cousin reuse")
        elif self.digital_cousin_evidence is not None:
            raise ValueError("digital_cousin_evidence is only valid for digital cousin reuse")

        if self.lane is AssetAcquisitionLane.FRESH_ASSET_GENERATION:
            if self.generation_receipt is None:
                raise ValueError("generation_receipt is required for fresh asset generation")
            if self.source_qualification is not None:
                raise ValueError("fresh generation cannot claim an existing source_qualification")
        else:
            if self.generation_receipt is not None:
                raise ValueError("generation_receipt is only valid for fresh asset generation")
            if self.source_qualification is None:
                raise ValueError("source_qualification is required for reused assets")
        return self


SourceMedia = Annotated[
    ImageSourceMedia | VideoSourceMedia,
    Field(discriminator="schema_version"),
]


class X2EnvEnvironmentPackage(HarnessModel):
    model_config = public_schema_config(X2ENV_ENVIRONMENT_PACKAGE_SCHEMA_ID)

    schema_version: Literal[X2ENV_ENVIRONMENT_PACKAGE_SCHEMA_ID]
    package_id: Sha256
    route_id: Literal["image2env", "video2env"]
    producer_skill_ref: CanonicalSkillRef
    source_media: SourceMedia
    requested_profile: RequestedGenesisProfile
    environment_format: Literal["harness.genesis_portable.v1"]
    backend: Literal["genesis"]
    representation_origin: RepresentationOrigin
    legacy_adapter_receipt: ArtifactRef | None
    scene_ir: ArtifactRef
    task_ir: ArtifactRef
    asset_closure_manifest: ArtifactRef
    binding_manifest: ArtifactRef
    runtime_lock: ArtifactRef
    simulator_ready: Literal[False] = False
    collision_qualified: Literal[False] = False

    @model_validator(mode="after")
    def package_is_a_route_bound_candidate(self) -> "X2EnvEnvironmentPackage":
        expected_skill = f"{self.route_id}.compile@1.0.0"
        if self.producer_skill_ref != expected_skill:
            raise ValueError(f"producer_skill_ref must be {expected_skill!r}")
        if self.route_id == "image2env" and not isinstance(self.source_media, ImageSourceMedia):
            raise ValueError("image2env package requires ImageSourceMedia")
        if self.route_id == "video2env" and not isinstance(self.source_media, VideoSourceMedia):
            raise ValueError("video2env package requires VideoSourceMedia")

        for value, name, schema in (
            (self.scene_ir, "scene_ir", SCENE_IR_SCHEMA),
            (self.task_ir, "task_ir", TASK_IR_SCHEMA),
            (self.asset_closure_manifest, "asset_closure_manifest", ASSET_CLOSURE_SCHEMA),
            (self.binding_manifest, "binding_manifest", BINDING_MANIFEST_SCHEMA),
            (self.runtime_lock, "runtime_lock", GENESIS_RUNTIME_LOCK_SCHEMA),
        ):
            _require_cas_ref(
                value,
                name,
                schema_version=schema,
                media_type="application/json",
            )
        if self.representation_origin is RepresentationOrigin.LEGACY_GENENV_ADAPTED:
            if self.legacy_adapter_receipt is None:
                raise ValueError("legacy_genenv_adapted package requires legacy_adapter_receipt")
            _require_cas_ref(
                self.legacy_adapter_receipt,
                "legacy_adapter_receipt",
                schema_version=LEGACY_ADAPTER_RECEIPT_SCHEMA,
                media_type="application/json",
            )
        elif self.legacy_adapter_receipt is not None:
            raise ValueError("legacy_adapter_receipt is only valid for legacy_genenv_adapted")
        return self


class _CompileInput(HarnessModel):
    interpretation_proposal: ArtifactRef
    requested_profile: RequestedGenesisProfile
    acquisition_policy: AcquisitionPolicy
    asset_catalog: ArtifactRef
    seed: Seed
    predecessor_package: ArtifactRef | None
    repair_evidence_refs: tuple[ArtifactRef, ...]
    model_execution_allowed: Literal[False] = False

    @model_validator(mode="after")
    def input_refs_are_typed(self) -> "_CompileInput":
        _require_cas_ref(
            self.interpretation_proposal,
            "interpretation_proposal",
            schema_version=SCENE_INTERPRETATION_SCHEMA,
            media_type="application/json",
        )
        _require_cas_ref(
            self.asset_catalog,
            "asset_catalog",
            schema_version=PORTABLE_ASSET_CATALOG_SCHEMA,
            media_type="application/json",
        )
        if self.predecessor_package is not None:
            _require_cas_ref(
                self.predecessor_package,
                "predecessor_package",
                schema_version=X2ENV_ENVIRONMENT_PACKAGE_SCHEMA_ID,
                media_type="application/json",
            )
        for index, evidence in enumerate(self.repair_evidence_refs):
            _require_cas_ref(evidence, f"repair_evidence_refs[{index}]")
        return self


class Image2EnvCompileInput(_CompileInput):
    model_config = public_schema_config(IMAGE2ENV_COMPILE_INPUT_SCHEMA_ID)

    schema_version: Literal[IMAGE2ENV_COMPILE_INPUT_SCHEMA_ID]
    source_media: ImageSourceMedia


class Video2EnvCompileInput(_CompileInput):
    model_config = public_schema_config(VIDEO2ENV_COMPILE_INPUT_SCHEMA_ID)

    schema_version: Literal[VIDEO2ENV_COMPILE_INPUT_SCHEMA_ID]
    source_media: VideoSourceMedia


class Image2EnvCompileV2Input(_CompileInput):
    """First executable image compile input with explicit attempt lineage."""

    model_config = public_schema_config(IMAGE2ENV_COMPILE_V2_INPUT_SCHEMA_ID)

    schema_version: Literal[IMAGE2ENV_COMPILE_V2_INPUT_SCHEMA_ID]
    source_media: ImageSourceMedia
    attempt: CompileAttemptV1


class Video2EnvCompileV2Input(_CompileInput):
    """First executable video compile input with explicit attempt lineage."""

    model_config = public_schema_config(VIDEO2ENV_COMPILE_V2_INPUT_SCHEMA_ID)

    schema_version: Literal[VIDEO2ENV_COMPILE_V2_INPUT_SCHEMA_ID]
    source_media: VideoSourceMedia
    attempt: CompileAttemptV1


class _CompileOutput(HarnessModel):
    environment_package: X2EnvEnvironmentPackage
    scene_ir: ArtifactRef
    task_ir: ArtifactRef
    asset_decisions: tuple[X2EnvAssetDecision, ...] = Field(min_length=1)
    staged_assets: tuple[ArtifactRef, ...]
    static_validation: ArtifactRef
    compile_receipt: ArtifactRef
    simulator_executed: Literal[False] = False
    publishable: Literal[False] = False

    @model_validator(mode="after")
    def output_is_a_static_candidate(self) -> "_CompileOutput":
        for value, name, schema in (
            (self.scene_ir, "scene_ir", SCENE_IR_SCHEMA),
            (self.task_ir, "task_ir", TASK_IR_SCHEMA),
            (self.static_validation, "static_validation", STATIC_VALIDATION_SCHEMA),
            (self.compile_receipt, "compile_receipt", COMPILE_RECEIPT_SCHEMA),
        ):
            _require_cas_ref(
                value,
                name,
                schema_version=schema,
                media_type="application/json",
            )
        for index, staged in enumerate(self.staged_assets):
            _require_cas_ref(
                staged,
                f"staged_assets[{index}]",
                schema_version=STAGED_ASSET_SCHEMA,
                media_type="application/json",
            )
        has_generation = any(
            item.lane is AssetAcquisitionLane.FRESH_ASSET_GENERATION
            for item in self.asset_decisions
        )
        if has_generation != bool(self.staged_assets):
            raise ValueError("staged_assets must exist exactly when fresh generation was selected")
        return self


class Image2EnvCompileOutput(_CompileOutput):
    model_config = public_schema_config(IMAGE2ENV_COMPILE_OUTPUT_SCHEMA_ID)

    schema_version: Literal[IMAGE2ENV_COMPILE_OUTPUT_SCHEMA_ID]

    @model_validator(mode="after")
    def package_route_matches(self) -> "Image2EnvCompileOutput":
        if self.environment_package.route_id != "image2env":
            raise ValueError("image2env output requires an image2env package")
        return self


class Video2EnvCompileOutput(_CompileOutput):
    model_config = public_schema_config(VIDEO2ENV_COMPILE_OUTPUT_SCHEMA_ID)

    schema_version: Literal[VIDEO2ENV_COMPILE_OUTPUT_SCHEMA_ID]

    @model_validator(mode="after")
    def package_route_matches(self) -> "Video2EnvCompileOutput":
        if self.environment_package.route_id != "video2env":
            raise ValueError("video2env output requires a video2env package")
        return self


class GenesisPhysicsPlan(HarnessModel):
    collision_enabled: Literal[True]
    settle_steps: PositiveInt
    observation_steps: PositiveInt
    video_frames: PositiveInt
    fps: PositiveInt
    robot_probe_steps: NonNegativeInt


class _ReplayInput(HarnessModel):
    environment_package: X2EnvEnvironmentPackage
    compile_receipt: ArtifactRef
    requested_profile: RequestedGenesisProfile
    runtime_attestation: ArtifactRef
    seed: Seed
    physics_plan: GenesisPhysicsPlan

    @model_validator(mode="after")
    def replay_dependencies_are_bound(self) -> "_ReplayInput":
        _require_cas_ref(
            self.compile_receipt,
            "compile_receipt",
            schema_version=COMPILE_RECEIPT_SCHEMA,
            media_type="application/json",
        )
        _require_cas_ref(
            self.runtime_attestation,
            "runtime_attestation",
            schema_version=GENESIS_RUNTIME_ATTESTATION_SCHEMA,
            media_type="application/json",
        )
        if self.requested_profile is not self.environment_package.requested_profile:
            raise ValueError("requested_profile must match environment_package")
        if (
            self.requested_profile is RequestedGenesisProfile.ROBOT_POLICY
            and self.physics_plan.robot_probe_steps == 0
        ):
            raise ValueError("genesis.robot_policy@1 requires nonzero robot_probe_steps")
        return self


class Image2EnvReplayInput(_ReplayInput):
    model_config = public_schema_config(IMAGE2ENV_REPLAY_INPUT_SCHEMA_ID)

    schema_version: Literal[IMAGE2ENV_REPLAY_INPUT_SCHEMA_ID]

    @model_validator(mode="after")
    def package_route_matches(self) -> "Image2EnvReplayInput":
        if self.environment_package.route_id != "image2env":
            raise ValueError("image2env replay requires an image2env package")
        return self


class Video2EnvReplayInput(_ReplayInput):
    model_config = public_schema_config(VIDEO2ENV_REPLAY_INPUT_SCHEMA_ID)

    schema_version: Literal[VIDEO2ENV_REPLAY_INPUT_SCHEMA_ID]

    @model_validator(mode="after")
    def package_route_matches(self) -> "Video2EnvReplayInput":
        if self.environment_package.route_id != "video2env":
            raise ValueError("video2env replay requires a video2env package")
        return self


class _ReplayOutput(HarnessModel):
    environment_package: X2EnvEnvironmentPackage
    package_id: Sha256
    requested_profile: RequestedGenesisProfile
    runtime_evidence: ArtifactRef
    physics_report: ArtifactRef
    robot_probe_report: ArtifactRef | None
    trajectory: ArtifactRef | None
    video: ArtifactRef
    frame_sequence_manifest: ArtifactRef
    media_verification: ArtifactRef
    runtime_receipt: ArtifactRef
    fresh_observation_refs: tuple[ArtifactRef, ...] = Field(min_length=1)
    steps_executed: PositiveInt
    total_frame_count: PositiveInt
    unique_frame_count: PositiveInt
    collisions_enabled: Literal[True]
    simulator_executed: Literal[True]

    @model_validator(mode="after")
    def replay_evidence_is_real_and_bound(self) -> "_ReplayOutput":
        if self.package_id != self.environment_package.package_id:
            raise ValueError("package_id must match environment_package.package_id")
        if self.requested_profile is not self.environment_package.requested_profile:
            raise ValueError("requested_profile must match environment_package")
        if self.unique_frame_count > self.total_frame_count:
            raise ValueError("unique_frame_count cannot exceed total_frame_count")
        for value, name, schema in (
            (self.runtime_evidence, "runtime_evidence", GENESIS_RUNTIME_EVIDENCE_SCHEMA),
            (self.physics_report, "physics_report", GENESIS_PHYSICS_REPORT_SCHEMA),
            (self.frame_sequence_manifest, "frame_sequence_manifest", VIDEO_FRAME_SEQUENCE_SCHEMA),
            (self.media_verification, "media_verification", MEDIA_VERIFICATION_SCHEMA),
            (self.runtime_receipt, "runtime_receipt", RUNTIME_RECEIPT_SCHEMA),
        ):
            _require_cas_ref(
                value,
                name,
                schema_version=schema,
                media_type="application/json",
            )
        _require_cas_ref(self.video, "video", media_prefix="video/")
        for index, observation in enumerate(self.fresh_observation_refs):
            _require_cas_ref(
                observation,
                f"fresh_observation_refs[{index}]",
                schema_version=FRESH_OBSERVATION_SCHEMA,
                media_type="application/json",
            )
        if self.robot_probe_report is not None:
            _require_cas_ref(
                self.robot_probe_report,
                "robot_probe_report",
                schema_version=GENESIS_ROBOT_PROBE_SCHEMA,
                media_type="application/json",
            )
        if self.trajectory is not None:
            _require_cas_ref(
                self.trajectory,
                "trajectory",
                schema_version=GENESIS_TRAJECTORY_SCHEMA,
                media_type="application/json",
            )
        if self.requested_profile is RequestedGenesisProfile.ROBOT_POLICY and (
            self.robot_probe_report is None or self.trajectory is None
        ):
            raise ValueError("robot_probe_report and trajectory are required for robot policy")
        return self


class Image2EnvReplayOutput(_ReplayOutput):
    model_config = public_schema_config(IMAGE2ENV_REPLAY_OUTPUT_SCHEMA_ID)

    schema_version: Literal[IMAGE2ENV_REPLAY_OUTPUT_SCHEMA_ID]

    @model_validator(mode="after")
    def package_route_matches(self) -> "Image2EnvReplayOutput":
        if self.environment_package.route_id != "image2env":
            raise ValueError("image2env replay output requires an image2env package")
        return self


class Video2EnvReplayOutput(_ReplayOutput):
    model_config = public_schema_config(VIDEO2ENV_REPLAY_OUTPUT_SCHEMA_ID)

    schema_version: Literal[VIDEO2ENV_REPLAY_OUTPUT_SCHEMA_ID]

    @model_validator(mode="after")
    def package_route_matches(self) -> "Video2EnvReplayOutput":
        if self.environment_package.route_id != "video2env":
            raise ValueError("video2env replay output requires a video2env package")
        return self


class _ValidateInput(HarnessModel):
    environment_package: X2EnvEnvironmentPackage
    compile_receipt: ArtifactRef
    compile_qualification: ArtifactRef
    replay_receipt: ArtifactRef
    replay_qualification: ArtifactRef
    runtime_evidence: ArtifactRef
    physics_report: ArtifactRef
    media_verification: ArtifactRef
    requested_profile: RequestedGenesisProfile
    fresh_observation_refs: tuple[ArtifactRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validation_dependencies_are_bound(self) -> "_ValidateInput":
        for value, name, schema in (
            (self.compile_receipt, "compile_receipt", COMPILE_RECEIPT_SCHEMA),
            (self.compile_qualification, "compile_qualification", SKILL_QUALIFICATION_SCHEMA),
            (self.replay_receipt, "replay_receipt", RUNTIME_RECEIPT_SCHEMA),
            (self.replay_qualification, "replay_qualification", SKILL_QUALIFICATION_SCHEMA),
            (self.runtime_evidence, "runtime_evidence", GENESIS_RUNTIME_EVIDENCE_SCHEMA),
            (self.physics_report, "physics_report", GENESIS_PHYSICS_REPORT_SCHEMA),
            (self.media_verification, "media_verification", MEDIA_VERIFICATION_SCHEMA),
        ):
            _require_cas_ref(
                value,
                name,
                schema_version=schema,
                media_type="application/json",
            )
        for index, observation in enumerate(self.fresh_observation_refs):
            _require_cas_ref(
                observation,
                f"fresh_observation_refs[{index}]",
                schema_version=FRESH_OBSERVATION_SCHEMA,
                media_type="application/json",
            )
        if self.requested_profile is not self.environment_package.requested_profile:
            raise ValueError("requested_profile must match environment_package")
        return self


class Image2EnvValidateInput(_ValidateInput):
    model_config = public_schema_config(IMAGE2ENV_VALIDATE_INPUT_SCHEMA_ID)

    schema_version: Literal[IMAGE2ENV_VALIDATE_INPUT_SCHEMA_ID]

    @model_validator(mode="after")
    def package_route_matches(self) -> "Image2EnvValidateInput":
        if self.environment_package.route_id != "image2env":
            raise ValueError("image2env validate requires an image2env package")
        return self


class Video2EnvValidateInput(_ValidateInput):
    model_config = public_schema_config(VIDEO2ENV_VALIDATE_INPUT_SCHEMA_ID)

    schema_version: Literal[VIDEO2ENV_VALIDATE_INPUT_SCHEMA_ID]

    @model_validator(mode="after")
    def package_route_matches(self) -> "Video2EnvValidateInput":
        if self.environment_package.route_id != "video2env":
            raise ValueError("video2env validate requires a video2env package")
        return self


class _ValidateOutput(HarnessModel):
    validation_decision: ArtifactRef
    validation_status: ValidationStatus
    publishable: StrictBool
    requested_profile: RequestedGenesisProfile
    satisfied_profiles: tuple[RequestedGenesisProfile, ...]
    blockers: tuple[Blocker, ...]
    portable_closure_reloaded: Literal[True]
    evidence_recomputed: Literal[True]

    @model_validator(mode="after")
    def publication_state_is_consistent(self) -> "_ValidateOutput":
        _require_cas_ref(
            self.validation_decision,
            "validation_decision",
            schema_version=VALIDATION_DECISION_SCHEMA,
            media_type="application/json",
        )
        if self.publishable:
            if (
                self.validation_status is not ValidationStatus.PASS
                or self.blockers
                or self.requested_profile not in self.satisfied_profiles
            ):
                raise ValueError(
                    "publishable output requires pass, no blockers, and requested profile satisfied"
                )
        elif not self.blockers:
            raise ValueError("non-publishable output requires at least one blocker")
        return self


class Image2EnvValidateOutput(_ValidateOutput):
    model_config = public_schema_config(IMAGE2ENV_VALIDATE_OUTPUT_SCHEMA_ID)

    schema_version: Literal[IMAGE2ENV_VALIDATE_OUTPUT_SCHEMA_ID]


class Video2EnvValidateOutput(_ValidateOutput):
    model_config = public_schema_config(VIDEO2ENV_VALIDATE_OUTPUT_SCHEMA_ID)

    schema_version: Literal[VIDEO2ENV_VALIDATE_OUTPUT_SCHEMA_ID]
