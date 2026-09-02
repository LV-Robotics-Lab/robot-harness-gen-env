"""Strict replay semantics layered over the generic three-document qualification.

The generic loader proves document, implementation, source-tree, and CAS identity.
This module then rejects any report that does not carry the one fixed replay case,
the exact eight replay checks, and mutually consistent typed evidence claims.  It
does not generate evidence or run a simulator.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, TypeVar

from pydantic import UUID4, Field, StrictBool, ValidationError

from scene_gen.builder import generated_module_source
from scene_gen.catalog import AssetCatalog
from scene_gen.schema import RelationType, ResolvedSceneSpec, SceneSpec
from scene_gen.validator import validate_resolved_scene

from .artifacts import ArtifactResolutionError, LocalArtifactStore
from .handlers.text2env_replay import _verify_runtime_evidence, text2env_replay_descriptor
from .qualification import (
    ImplementationFileV1,
    LoadedQualification,
    QualificationBundleError,
    QualificationCheckV1,
    _publish_qualification_inspection,
    _QualificationInspection,
    verify_qualification_bundle,
    verify_qualification_documents,
)
from .registry import _invocation_digest
from .replay_dependencies import (
    REPLAY_CAPABILITY_DEPENDENCY,
    REPLAY_EXECUTOR_DEPENDENCY,
    REPLAY_HANDLER_CONFIG_DEPENDENCY,
    REPLAY_MEDIA_VERIFIER_DEPENDENCY,
    REPLAY_RUNTIME_ASSET_DEPENDENCY,
    TEXT2ENV_REPLAY_SKILL_REF,
)
from .runtime_assets import canonical_runtime_asset_manifest_bytes
from .runtime_capability import (
    RUNTIME_ARTIFACT_PATHS,
    RUNTIME_CAPABILITY_SCHEMA,
    RUNTIME_MEDIA_ARTIFACT_PATHS,
    RuntimeCapabilityError,
    canonical_capability_bytes,
    validate_runtime_capability_document,
)
from .runtime_events import RuntimeEventCodec, RuntimeEventKind
from .schemas import (
    ArtifactRef,
    DependencyRef,
    Event,
    ExecutionReproducibility,
    Invocation,
    RunState,
    RunStatus,
    SkillDescriptorV2,
    Text2EnvReplayInput,
    Text2EnvReplayOutput,
)
from .schemas.base import (
    HarnessModel,
    NonEmptyString,
    NonNegativeInt,
    PositiveInt,
    Sha256,
    ShortString,
)

REPLAY_QUALIFICATION_CASE_ID = "can-on-plate-seed-7-900-120-120-12"
REPLAY_QUALIFICATION_REQUEST = "Place a can on top of a plate."
REPLAY_QUALIFICATION_CHECK_NAMES = (
    "01.case_binding",
    "02.exact_dependencies",
    "03.event_lifecycle",
    "04.runtime_asset_snapshot",
    "05.media_decode",
    "06.physics_validation",
    "07.source_stability",
    "08.candidate_kernel_executions",
)
_DEPENDENCY_NAMES = (
    REPLAY_CAPABILITY_DEPENDENCY,
    REPLAY_EXECUTOR_DEPENDENCY,
    REPLAY_HANDLER_CONFIG_DEPENDENCY,
    REPLAY_MEDIA_VERIFIER_DEPENDENCY,
    REPLAY_RUNTIME_ASSET_DEPENDENCY,
)
_CHECKPOINTS = (120, 240, 360, 480, 600, 720, 840)
_MIN_UNIQUE_VIDEO_FRAMES = 30
_EVENT_KINDS = (
    "preflight.completed",
    "scene.loaded",
    "simulation.started",
    *("simulation.checkpoint",) * 7,
    "simulation.completed",
    "media.completed",
    "evidence.completed",
    "worker.completed",
)
_PROBE_ARTIFACT_NAMES = frozenset(
    f"{phase}_probe_{label}"
    for phase in ("preflight", "postflight")
    for label in ("stdout", "stderr", "capability_output", "diagnostics")
)
_ReplayClaim = TypeVar("_ReplayClaim", bound=HarnessModel)


class ReplayQualificationRuntimeConfig(HarnessModel):
    precheck_steps: NonNegativeInt
    settle_steps: PositiveInt
    contact_window_steps: PositiveInt
    video_frames: NonNegativeInt
    fps: PositiveInt


class ReplayCaseBindingClaim(HarnessModel):
    schema_version: Literal["harness.replay_qualification.case_binding.v1"]
    case_id: ShortString
    skill_ref: Literal["text2env.replay@1.0.0"]
    request: ShortString
    seed: NonNegativeInt
    runtime_config: ReplayQualificationRuntimeConfig
    scene_spec_sha256: Sha256
    resolved_scene_sha256: Sha256
    environment_package_id: Sha256
    asset_catalog_sha256: Sha256
    package_manifest_sha256: Sha256
    operator_before_manifest: ArtifactRef
    operator_after_manifest: ArtifactRef


class ReplayExactDependenciesClaim(HarnessModel):
    schema_version: Literal["harness.replay_qualification.exact_dependencies.v1"]
    dependencies: tuple[DependencyRef, ...]


class ReplayLifecycleRunClaim(HarnessModel):
    transcript_sha256: Sha256
    event_count: PositiveInt
    event_kinds: tuple[ShortString, ...]
    checkpoint_completed_steps: tuple[PositiveInt, ...]
    simulation_completed_steps: PositiveInt
    transcript_complete: StrictBool
    live_observer_matched: StrictBool
    streams_complete: StrictBool


class ReplayEventLifecycleClaim(HarnessModel):
    schema_version: Literal["harness.replay_qualification.event_lifecycle.v1"]
    checkpoint_steps: PositiveInt
    candidate_direct: ReplayLifecycleRunClaim
    production_kernel_candidate: ReplayLifecycleRunClaim


class ReplayRuntimeAssetSnapshotClaim(HarnessModel):
    schema_version: Literal["harness.replay_qualification.runtime_asset_snapshot.v1"]
    manifest_sha256: Sha256
    member_count: PositiveInt
    all_members_verified: StrictBool
    self_contained: StrictBool
    candidate_direct_manifest_sha256: Sha256
    production_kernel_candidate_manifest_sha256: Sha256


class ReplayMediaDecodeRunClaim(HarnessModel):
    video_sha256: Sha256
    fully_decoded: StrictBool
    frame_count: PositiveInt
    source_unique_frame_count: PositiveInt
    decoded_unique_frame_count: PositiveInt
    fps_numerator: PositiveInt
    fps_denominator: PositiveInt
    width: PositiveInt
    height: PositiveInt
    format_name: ShortString
    codec_name: ShortString
    pixel_format: ShortString
    sample_aspect_ratio: ShortString
    square_sample_aspect_ratio_defaulted: StrictBool
    decoded_png_count: PositiveInt
    all_pngs_decoded: StrictBool


class ReplayMediaDecodeClaim(HarnessModel):
    schema_version: Literal["harness.replay_qualification.media_decode.v1"]
    verifier_identity_sha256: Sha256
    ffmpeg_sha256: Sha256
    launcher_sha256: Sha256
    candidate_direct: ReplayMediaDecodeRunClaim
    production_kernel_candidate: ReplayMediaDecodeRunClaim


class ReplayPhysicsValidationRunClaim(HarnessModel):
    runtime_evidence_sha256: Sha256
    validation_report_sha256: Sha256
    status: Literal["pass"]
    fail_count: NonNegativeInt
    not_run_count: NonNegativeInt
    resolved_scene_sha256: Sha256
    settle_steps: PositiveInt
    contact_window_steps: PositiveInt
    video_frame_count: PositiveInt
    unique_video_frame_count: PositiveInt


class ReplayPhysicsValidationClaim(HarnessModel):
    schema_version: Literal["harness.replay_qualification.physics_validation.v1"]
    candidate_direct: ReplayPhysicsValidationRunClaim
    production_kernel_candidate: ReplayPhysicsValidationRunClaim


class ReplaySourceStabilityClaim(HarnessModel):
    schema_version: Literal["harness.replay_qualification.source_stability.v1"]
    changed_during_qualification: StrictBool
    implementation_sha256: Sha256
    implementation_file_count: PositiveInt
    scene_gen_tree_sha256: Sha256
    ledger_contract_tree_sha256: Sha256
    harness_tree_sha256: Sha256
    before_manifest: ArtifactRef
    after_execution_manifest: ArtifactRef
    before_publish_manifest: ArtifactRef


class ReplayExecutionClaim(HarnessModel):
    mode: Literal["candidate_direct"]
    status: Literal["succeeded"]
    attempt_count: PositiveInt
    handler_call_count: PositiveInt
    execution_receipt_sha256: Sha256
    output_sha256: Sha256
    runtime_evidence_sha256: Sha256
    validation_report_sha256: Sha256
    event_transcript_sha256: Sha256
    runtime_asset_manifest_sha256: Sha256
    video_sha256: Sha256
    environment_package_id: Sha256
    dependencies: tuple[DependencyRef, ...]
    cas_reread_verified: StrictBool
    all_artifacts_verified: StrictBool
    run_id: UUID4
    invocation_digest: Sha256
    supervisor_receipt: ArtifactRef


class ReplayQualificationCandidateExecutionClaim(HarnessModel):
    """One unregistered evaluation through the Registry production kernel."""

    mode: Literal["qualification_candidate"]
    status: Literal["succeeded"]
    attempt_count: PositiveInt
    handler_call_count: PositiveInt
    execution_receipt_sha256: Sha256
    output_sha256: Sha256
    runtime_evidence_sha256: Sha256
    validation_report_sha256: Sha256
    event_transcript_sha256: Sha256
    runtime_asset_manifest_sha256: Sha256
    video_sha256: Sha256
    environment_package_id: Sha256
    dependencies: tuple[DependencyRef, ...]
    cas_reread_verified: StrictBool
    all_artifacts_verified: StrictBool
    run_id: UUID4
    invocation_digest: Sha256
    evaluation_receipt: ArtifactRef


class ReplayExecutionsClaim(HarnessModel):
    schema_version: Literal["harness.replay_qualification.candidate_kernel_executions.v1"]
    candidate_direct: ReplayExecutionClaim
    production_kernel_candidate: ReplayQualificationCandidateExecutionClaim
    evidence_closure_manifest: ArtifactRef


class ReplayOperatorFileIdentity(HarnessModel):
    label: NonEmptyString
    path: NonEmptyString
    bytes: NonNegativeInt
    sha256: Sha256


class ReplayOperatorInputsManifest(HarnessModel):
    schema_version: Literal["harness.replay_qualification.operator_inputs.v1"]
    phase: Literal["before_execution", "before_publish"]
    machine_local: Literal[True]
    skill_ref: Literal["text2env.replay@1.0.0"]
    case_id: Literal["can-on-plate-seed-7-900-120-120-12"]
    request: Literal["Place a can on top of a plate."]
    seed: Literal[7]
    runtime_config: ReplayQualificationRuntimeConfig
    task_config: Literal["demo_clean"]
    min_visible_pixels: Literal[64]
    checkpoint_steps: Literal[120]
    allowed_asset_roots: tuple[NonEmptyString, ...] = Field(min_length=1)
    runtime_module_root: NonEmptyString
    delegated_cgroup_root: NonEmptyString
    files: tuple[ReplayOperatorFileIdentity, ...] = Field(min_length=6, max_length=6)
    input_asset_catalog: ArtifactRef
    runtime_capability: ArtifactRef
    inputs_sha256: Sha256


class ReplayHarnessSourceManifest(HarnessModel):
    schema_version: Literal["harness.replay_qualification.harness_source_manifest.v1"]
    phase: Literal["before_execution", "after_execution", "before_publish"]
    machine_local: Literal[True]
    distribution_root: NonEmptyString
    harness_root: NonEmptyString
    files: tuple[ImplementationFileV1, ...] = Field(min_length=1)
    tree_sha256: Sha256


class ReplayHarnessEventTranscript(HarnessModel):
    schema_version: Literal["harness.replay_qualification.harness_event_transcript.v1"]
    mode: Literal["candidate_direct", "qualification_candidate"]
    run_id: UUID4
    events: tuple[Event, ...] = Field(min_length=1)


class _ReplayRunEvidenceReceipt(HarnessModel):
    skill_ref: Literal["text2env.replay@1.0.0"]
    run_id: UUID4
    invocation: Invocation
    run_state: RunState
    harness_event_transcript: ArtifactRef
    runtime_event_transcript: ArtifactRef
    handler_call_count: PositiveInt
    runtime_call_count: PositiveInt
    typed_output: Text2EnvReplayOutput
    exact_dependencies: tuple[DependencyRef, ...]
    artifact_closure: tuple[ArtifactRef, ...] = Field(min_length=1)


class ReplayDirectSupervisorReceipt(_ReplayRunEvidenceReceipt):
    schema_version: Literal["harness.replay_qualification.direct_supervisor_receipt.v1"]
    mode: Literal["candidate_direct"]


class ReplayKernelEvaluationReceipt(_ReplayRunEvidenceReceipt):
    schema_version: Literal["harness.replay_qualification.kernel_evaluation_receipt.v1"]
    mode: Literal["qualification_candidate"]


class ReplayEvidenceClosureManifest(HarnessModel):
    schema_version: Literal["harness.replay_qualification.evidence_closure.v1"]
    skill_ref: Literal["text2env.replay@1.0.0"]
    case_id: Literal["can-on-plate-seed-7-900-120-120-12"]
    refs: tuple[ArtifactRef, ...] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class LoadedReplayQualification:
    """A generic qualification narrowed to the complete replay claim set."""

    generic: LoadedQualification
    descriptor: SkillDescriptorV2
    case_binding: ReplayCaseBindingClaim
    exact_dependencies: ReplayExactDependenciesClaim
    event_lifecycle: ReplayEventLifecycleClaim
    runtime_asset_snapshot: ReplayRuntimeAssetSnapshotClaim
    media_decode: ReplayMediaDecodeClaim
    physics_validation: ReplayPhysicsValidationClaim
    source_stability: ReplaySourceStabilityClaim
    executions: ReplayExecutionsClaim
    operator_before: ReplayOperatorInputsManifest
    operator_after: ReplayOperatorInputsManifest
    source_before: ReplayHarnessSourceManifest
    source_after_execution: ReplayHarnessSourceManifest
    source_before_publish: ReplayHarnessSourceManifest
    direct_supervisor: ReplayDirectSupervisorReceipt
    kernel_evaluation: ReplayKernelEvaluationReceipt
    evidence_closure: ReplayEvidenceClosureManifest


@dataclass(frozen=True, slots=True)
class _ReplayQualificationInspection:
    """Pure deep-verification data; never accepted as registration authority."""

    generic: _QualificationInspection
    descriptor: SkillDescriptorV2
    case_binding: ReplayCaseBindingClaim
    exact_dependencies: ReplayExactDependenciesClaim
    event_lifecycle: ReplayEventLifecycleClaim
    runtime_asset_snapshot: ReplayRuntimeAssetSnapshotClaim
    media_decode: ReplayMediaDecodeClaim
    physics_validation: ReplayPhysicsValidationClaim
    source_stability: ReplaySourceStabilityClaim
    executions: ReplayExecutionsClaim
    evidence: _LoadedReplayEvidence


class ReplayQualificationError(QualificationBundleError):
    """A generic bundle or replay-specific evidence claim failed closed."""


@dataclass(frozen=True, slots=True)
class _LoadedReplayEvidence:
    operator_before: ReplayOperatorInputsManifest
    operator_after: ReplayOperatorInputsManifest
    source_before: ReplayHarnessSourceManifest
    source_after_execution: ReplayHarnessSourceManifest
    source_before_publish: ReplayHarnessSourceManifest
    direct_supervisor: ReplayDirectSupervisorReceipt
    kernel_evaluation: ReplayKernelEvaluationReceipt
    closure: ReplayEvidenceClosureManifest


_KNOWN_JSON_MODELS: dict[str, type[HarnessModel]] = {
    "harness.replay_qualification.direct_supervisor_receipt.v1": (ReplayDirectSupervisorReceipt),
    "harness.replay_qualification.evidence_closure.v1": ReplayEvidenceClosureManifest,
    "harness.replay_qualification.harness_event_transcript.v1": ReplayHarnessEventTranscript,
    "harness.replay_qualification.harness_source_manifest.v1": ReplayHarnessSourceManifest,
    "harness.replay_qualification.kernel_evaluation_receipt.v1": (ReplayKernelEvaluationReceipt),
    "harness.replay_qualification.operator_inputs.v1": ReplayOperatorInputsManifest,
}
_KNOWN_JSON_SCHEMAS = frozenset(
    {
        *_KNOWN_JSON_MODELS,
        "harness.runtime_asset_snapshot.v1",
        "harness.runtime_execution_diagnostics.v1",
        "harness.runtime_probe_diagnostics.v1",
        "harness.text2env_replay_receipt.v1",
        "robotwin.asset_catalog.v1",
        "robotwin.generated_scene_package.v1",
        "robotwin.resolved_scene.v1",
        "harness.robotwin_runtime_capability.v1",
        "robotwin.scene_runtime_evidence.v2",
        "robotwin.scene_spec.v1",
        "robotwin.scene_validation.v1",
    }
)


def load_replay_qualification_bundle(
    bundle_root: Path,
    *,
    artifact_store: LocalArtifactStore,
    implementation_root: Path,
    scene_gen_root: Path,
    ledger_contract_root: Path,
) -> LoadedReplayQualification:
    """Load and strictly narrow one fixed ``text2env.replay@1.0.0`` bundle."""

    return publish_replay_qualification_bundle(
        bundle_root,
        artifact_store=artifact_store,
        implementation_root=implementation_root,
        scene_gen_root=scene_gen_root,
        ledger_contract_root=ledger_contract_root,
    )


def publish_replay_qualification_bundle(
    bundle_root: Path,
    *,
    artifact_store: LocalArtifactStore,
    implementation_root: Path,
    scene_gen_root: Path,
    ledger_contract_root: Path,
) -> LoadedReplayQualification:
    """Reverify the raw replay bundle and deep CAS closure before atomic pass publication."""

    inspected = verify_replay_qualification_bundle(
        bundle_root,
        artifact_store=artifact_store,
        implementation_root=implementation_root,
        scene_gen_root=scene_gen_root,
        ledger_contract_root=ledger_contract_root,
    )
    return _publish_replay_inspection(inspected, artifact_store=artifact_store)


def verify_replay_qualification_bundle(
    bundle_root: Path,
    *,
    artifact_store: LocalArtifactStore,
    implementation_root: Path,
    scene_gen_root: Path,
    ledger_contract_root: Path,
) -> _ReplayQualificationInspection:
    """Strictly verify generic and replay evidence without publishing pass documents."""

    try:
        verified = verify_qualification_bundle(
            bundle_root,
            skill_ref=TEXT2ENV_REPLAY_SKILL_REF,
            implementation_root=implementation_root,
            scene_gen_root=scene_gen_root,
            ledger_contract_root=ledger_contract_root,
        )
    except QualificationBundleError as error:
        raise ReplayQualificationError(error.reason, str(error)) from error

    return _verify_verified_replay_qualification(
        verified,
        artifact_store=artifact_store,
        implementation_root=implementation_root,
    )


def verify_replay_qualification_documents(
    documents: Mapping[str, bytes],
    *,
    artifact_store: LocalArtifactStore,
    implementation_root: Path,
    scene_gen_root: Path,
    ledger_contract_root: Path,
) -> _ReplayQualificationInspection:
    """Strictly verify in-memory replay documents without staging pass receipts."""

    try:
        verified = verify_qualification_documents(
            documents,
            skill_ref=TEXT2ENV_REPLAY_SKILL_REF,
            implementation_root=implementation_root,
            scene_gen_root=scene_gen_root,
            ledger_contract_root=ledger_contract_root,
        )
    except QualificationBundleError as error:
        raise ReplayQualificationError(error.reason, str(error)) from error
    return _verify_verified_replay_qualification(
        verified,
        artifact_store=artifact_store,
        implementation_root=implementation_root,
    )


def _verify_verified_replay_qualification(
    verified: _QualificationInspection,
    *,
    artifact_store: LocalArtifactStore,
    implementation_root: Path,
) -> _ReplayQualificationInspection:
    """Apply all replay-specific claim and evidence gates to verified generic bytes."""

    checks = verified.report.checks
    names = tuple(check.name for check in checks)
    if names != REPLAY_QUALIFICATION_CHECK_NAMES:
        raise ReplayQualificationError(
            "replay_check_set_mismatch",
            "replay qualification checks are missing, extra, or out of fixed order",
        )

    case_binding = _parse_claim(checks[0], ReplayCaseBindingClaim)
    exact_dependencies = _parse_claim(checks[1], ReplayExactDependenciesClaim)
    event_lifecycle = _parse_claim(checks[2], ReplayEventLifecycleClaim)
    runtime_asset_snapshot = _parse_claim(checks[3], ReplayRuntimeAssetSnapshotClaim)
    media_decode = _parse_claim(checks[4], ReplayMediaDecodeClaim)
    physics_validation = _parse_claim(checks[5], ReplayPhysicsValidationClaim)
    source_stability = _parse_claim(checks[6], ReplaySourceStabilityClaim)
    executions = _parse_claim(checks[7], ReplayExecutionsClaim)

    _verify_replay_claims(
        generic=verified,
        case_binding=case_binding,
        exact_dependencies=exact_dependencies,
        event_lifecycle=event_lifecycle,
        runtime_asset_snapshot=runtime_asset_snapshot,
        media_decode=media_decode,
        physics_validation=physics_validation,
        source_stability=source_stability,
        executions=executions,
    )
    descriptor = text2env_replay_descriptor(
        qualification_artifact=verified.expected_qualification_artifact,
        implementation_sha256=verified.implementation_sha256,
    )
    if (
        type(descriptor) is not SkillDescriptorV2
        or descriptor.reproducibility is not ExecutionReproducibility.EVIDENCE_INVARIANT_REPEATABLE
        or f"{descriptor.skill_id}@{descriptor.version}" != TEXT2ENV_REPLAY_SKILL_REF
        or descriptor.mcp_tool_name != "text2env_replay_v1_0_0"
        or descriptor.input_schema != "harness.text2env_replay_input.v1"
        or descriptor.output_schema != "harness.text2env_replay_output.v1"
        or descriptor.implementation_name != "self_improving.harness.handlers.text2env_replay"
        or descriptor.implementation_version != "1"
        or descriptor.implementation_sha256 != verified.implementation_sha256
        or descriptor.max_attempts != 2
        or descriptor.qualification_artifact != verified.expected_qualification_artifact
    ):
        raise ReplayQualificationError(
            "descriptor_semantics_mismatch",
            "replay descriptor does not declare v2 evidence-invariant repeatability",
        )
    evidence = _load_and_verify_evidence_closure(
        artifact_store=artifact_store,
        generic=verified,
        case_binding=case_binding,
        exact_dependencies=exact_dependencies,
        event_lifecycle=event_lifecycle,
        runtime_asset_snapshot=runtime_asset_snapshot,
        media_decode=media_decode,
        physics_validation=physics_validation,
        source_stability=source_stability,
        executions=executions,
        implementation_root=implementation_root,
    )

    return _ReplayQualificationInspection(
        generic=verified,
        descriptor=descriptor,
        case_binding=case_binding,
        exact_dependencies=exact_dependencies,
        event_lifecycle=event_lifecycle,
        runtime_asset_snapshot=runtime_asset_snapshot,
        media_decode=media_decode,
        physics_validation=physics_validation,
        source_stability=source_stability,
        executions=executions,
        evidence=evidence,
    )


def _publish_replay_inspection(
    inspected: _ReplayQualificationInspection,
    *,
    artifact_store: LocalArtifactStore,
) -> LoadedReplayQualification:
    """Publish generic pass documents from a fresh internal replay inspection."""

    generic = _publish_qualification_inspection(inspected.generic, artifact_store=artifact_store)
    evidence = inspected.evidence
    return LoadedReplayQualification(
        generic=generic,
        descriptor=inspected.descriptor,
        case_binding=inspected.case_binding,
        exact_dependencies=inspected.exact_dependencies,
        event_lifecycle=inspected.event_lifecycle,
        runtime_asset_snapshot=inspected.runtime_asset_snapshot,
        media_decode=inspected.media_decode,
        physics_validation=inspected.physics_validation,
        source_stability=inspected.source_stability,
        executions=inspected.executions,
        operator_before=evidence.operator_before,
        operator_after=evidence.operator_after,
        source_before=evidence.source_before,
        source_after_execution=evidence.source_after_execution,
        source_before_publish=evidence.source_before_publish,
        direct_supervisor=evidence.direct_supervisor,
        kernel_evaluation=evidence.kernel_evaluation,
        evidence_closure=evidence.closure,
    )


def _parse_claim(
    check: QualificationCheckV1,
    model: type[_ReplayClaim],
) -> _ReplayClaim:
    try:
        return model.model_validate(check.evidence)
    except ValidationError as error:
        raise ReplayQualificationError(
            "invalid_replay_claim",
            f"{check.name} evidence is not the strict replay claim: {error}",
        ) from error


def _verify_replay_claims(
    *,
    generic: _QualificationInspection,
    case_binding: ReplayCaseBindingClaim,
    exact_dependencies: ReplayExactDependenciesClaim,
    event_lifecycle: ReplayEventLifecycleClaim,
    runtime_asset_snapshot: ReplayRuntimeAssetSnapshotClaim,
    media_decode: ReplayMediaDecodeClaim,
    physics_validation: ReplayPhysicsValidationClaim,
    source_stability: ReplaySourceStabilityClaim,
    executions: ReplayExecutionsClaim,
) -> None:
    config = case_binding.runtime_config
    if (
        generic.qualification.deterministic_case_id != REPLAY_QUALIFICATION_CASE_ID
        or case_binding.case_id != REPLAY_QUALIFICATION_CASE_ID
        or case_binding.skill_ref != TEXT2ENV_REPLAY_SKILL_REF
        or case_binding.request != REPLAY_QUALIFICATION_REQUEST
        or case_binding.seed != 7
        or config.precheck_steps != 0
        or config.settle_steps != 900
        or config.contact_window_steps != 120
        or config.video_frames != 120
        or config.fps != 12
        or case_binding.environment_package_id != case_binding.resolved_scene_sha256
    ):
        _claim_mismatch("fixed replay case binding is inconsistent")

    dependencies = exact_dependencies.dependencies
    if tuple(item.name for item in dependencies) != _DEPENDENCY_NAMES or any(
        item.version != "1" for item in dependencies
    ):
        _claim_mismatch("replay dependency closure is not the exact five version-1 records")
    dependencies_by_name = {item.name: item for item in dependencies}

    if event_lifecycle.checkpoint_steps != 120:
        _claim_mismatch("replay lifecycle checkpoint interval is not fixed")
    for lifecycle in (
        event_lifecycle.candidate_direct,
        event_lifecycle.production_kernel_candidate,
    ):
        if (
            lifecycle.event_count != len(_EVENT_KINDS)
            or lifecycle.event_kinds != _EVENT_KINDS
            or lifecycle.checkpoint_completed_steps != _CHECKPOINTS
            or lifecycle.simulation_completed_steps != 900
            or not lifecycle.transcript_complete
            or not lifecycle.live_observer_matched
            or not lifecycle.streams_complete
        ):
            _claim_mismatch("replay event lifecycle is incomplete or inconsistent")

    if (
        not runtime_asset_snapshot.all_members_verified
        or not runtime_asset_snapshot.self_contained
        or runtime_asset_snapshot.candidate_direct_manifest_sha256
        != runtime_asset_snapshot.manifest_sha256
        or runtime_asset_snapshot.production_kernel_candidate_manifest_sha256
        != runtime_asset_snapshot.manifest_sha256
        or dependencies_by_name[REPLAY_RUNTIME_ASSET_DEPENDENCY].sha256
        != runtime_asset_snapshot.manifest_sha256
    ):
        _claim_mismatch("runtime asset snapshot is not fully verified and dependency-bound")
    if (
        dependencies_by_name[REPLAY_MEDIA_VERIFIER_DEPENDENCY].sha256
        != media_decode.verifier_identity_sha256
    ):
        _claim_mismatch("media verifier identity is not dependency-bound")

    for media in (
        media_decode.candidate_direct,
        media_decode.production_kernel_candidate,
    ):
        _verify_media_claim(media)
    for physics, media in (
        (physics_validation.candidate_direct, media_decode.candidate_direct),
        (
            physics_validation.production_kernel_candidate,
            media_decode.production_kernel_candidate,
        ),
    ):
        if (
            physics.fail_count != 0
            or physics.not_run_count != 0
            or physics.resolved_scene_sha256 != case_binding.resolved_scene_sha256
            or physics.settle_steps != config.settle_steps
            or physics.contact_window_steps != config.contact_window_steps
            or physics.video_frame_count != config.video_frames
            or physics.unique_video_frame_count != media.source_unique_frame_count
        ):
            _claim_mismatch("physics validation is not a complete fixed-case pass")

    if (
        source_stability.changed_during_qualification
        or source_stability.implementation_sha256 != generic.implementation_sha256
        or source_stability.implementation_file_count != len(generic.manifest.files)
        or source_stability.scene_gen_tree_sha256 != generic.report.scene_gen_tree_sha256
        or source_stability.ledger_contract_tree_sha256
        != generic.report.ledger_contract_tree_sha256
    ):
        _claim_mismatch("source stability claim disagrees with the verified generic bundle")

    if (
        executions.candidate_direct.attempt_count != 1
        or executions.candidate_direct.handler_call_count != 1
        or not executions.candidate_direct.cas_reread_verified
        or not executions.candidate_direct.all_artifacts_verified
        or executions.candidate_direct.runtime_evidence_sha256
        != physics_validation.candidate_direct.runtime_evidence_sha256
        or executions.candidate_direct.validation_report_sha256
        != physics_validation.candidate_direct.validation_report_sha256
        or executions.candidate_direct.event_transcript_sha256
        != event_lifecycle.candidate_direct.transcript_sha256
        or executions.candidate_direct.runtime_asset_manifest_sha256
        != runtime_asset_snapshot.manifest_sha256
        or executions.candidate_direct.video_sha256 != media_decode.candidate_direct.video_sha256
        or executions.candidate_direct.environment_package_id != case_binding.environment_package_id
        or executions.candidate_direct.dependencies != dependencies
        or executions.production_kernel_candidate.attempt_count != 1
        or executions.production_kernel_candidate.handler_call_count != 1
        or not executions.production_kernel_candidate.cas_reread_verified
        or not executions.production_kernel_candidate.all_artifacts_verified
        or executions.production_kernel_candidate.runtime_evidence_sha256
        != physics_validation.production_kernel_candidate.runtime_evidence_sha256
        or executions.production_kernel_candidate.validation_report_sha256
        != physics_validation.production_kernel_candidate.validation_report_sha256
        or executions.production_kernel_candidate.event_transcript_sha256
        != event_lifecycle.production_kernel_candidate.transcript_sha256
        or executions.production_kernel_candidate.runtime_asset_manifest_sha256
        != runtime_asset_snapshot.manifest_sha256
        or executions.production_kernel_candidate.video_sha256
        != media_decode.production_kernel_candidate.video_sha256
        or executions.production_kernel_candidate.environment_package_id
        != case_binding.environment_package_id
        or executions.production_kernel_candidate.dependencies != dependencies
    ):
        _claim_mismatch(
            "direct and production-kernel candidate executions are not independently verified once"
        )


def _load_and_verify_evidence_closure(
    *,
    artifact_store: LocalArtifactStore,
    generic: _QualificationInspection,
    case_binding: ReplayCaseBindingClaim,
    exact_dependencies: ReplayExactDependenciesClaim,
    event_lifecycle: ReplayEventLifecycleClaim,
    runtime_asset_snapshot: ReplayRuntimeAssetSnapshotClaim,
    media_decode: ReplayMediaDecodeClaim,
    physics_validation: ReplayPhysicsValidationClaim,
    source_stability: ReplaySourceStabilityClaim,
    executions: ReplayExecutionsClaim,
    implementation_root: Path,
) -> _LoadedReplayEvidence:
    closure = _load_evidence_model(
        artifact_store,
        executions.evidence_closure_manifest,
        ReplayEvidenceClosureManifest,
        label="replay evidence closure",
    )
    _require_actual_distribution_root(implementation_root)
    operator_before = _load_evidence_model(
        artifact_store,
        case_binding.operator_before_manifest,
        ReplayOperatorInputsManifest,
        label="operator inputs before execution",
    )
    operator_after = _load_evidence_model(
        artifact_store,
        case_binding.operator_after_manifest,
        ReplayOperatorInputsManifest,
        label="operator inputs before publish",
    )
    source_before = _load_evidence_model(
        artifact_store,
        source_stability.before_manifest,
        ReplayHarnessSourceManifest,
        label="Harness source before execution",
    )
    source_after_execution = _load_evidence_model(
        artifact_store,
        source_stability.after_execution_manifest,
        ReplayHarnessSourceManifest,
        label="Harness source after execution",
    )
    source_before_publish = _load_evidence_model(
        artifact_store,
        source_stability.before_publish_manifest,
        ReplayHarnessSourceManifest,
        label="Harness source before publish",
    )
    direct = _load_evidence_model(
        artifact_store,
        executions.candidate_direct.supervisor_receipt,
        ReplayDirectSupervisorReceipt,
        label="direct supervisor receipt",
    )
    kernel = _load_evidence_model(
        artifact_store,
        executions.production_kernel_candidate.evaluation_receipt,
        ReplayKernelEvaluationReceipt,
        label="production-kernel evaluation receipt",
    )

    _verify_operator_manifests(
        artifact_store,
        before=operator_before,
        after=operator_after,
        case_binding=case_binding,
        exact_dependencies=exact_dependencies,
        media_decode=media_decode,
    )
    _verify_source_manifests(
        implementation_root=implementation_root,
        generic=generic,
        claim=source_stability,
        before=source_before,
        after_execution=source_after_execution,
        before_publish=source_before_publish,
    )
    _verify_run_evidence(
        artifact_store,
        receipt=direct,
        execution=executions.candidate_direct,
        lifecycle=event_lifecycle.candidate_direct,
        media=media_decode.candidate_direct,
        physics=physics_validation.candidate_direct,
        runtime_asset_snapshot=runtime_asset_snapshot,
        exact_dependencies=exact_dependencies,
        case_binding=case_binding,
    )
    _verify_run_evidence(
        artifact_store,
        receipt=kernel,
        execution=executions.production_kernel_candidate,
        lifecycle=event_lifecycle.production_kernel_candidate,
        media=media_decode.production_kernel_candidate,
        physics=physics_validation.production_kernel_candidate,
        runtime_asset_snapshot=runtime_asset_snapshot,
        exact_dependencies=exact_dependencies,
        case_binding=case_binding,
    )
    roots = (
        case_binding.operator_before_manifest,
        case_binding.operator_after_manifest,
        source_stability.before_manifest,
        source_stability.after_execution_manifest,
        source_stability.before_publish_manifest,
        executions.candidate_direct.supervisor_receipt,
        executions.production_kernel_candidate.evaluation_receipt,
    )
    discovered = _discover_evidence_closure(artifact_store, roots)
    declared = closure.refs
    if (
        tuple(sorted(declared, key=_artifact_sort_key)) != declared
        or len({item.sha256 for item in declared}) != len(declared)
        or declared != discovered
    ):
        raise ReplayQualificationError(
            "replay_evidence_closure_mismatch",
            "replay evidence closure has extra, omitted, duplicate, or unordered references",
        )
    if runtime_asset_snapshot.manifest_sha256 not in {item.sha256 for item in declared}:
        raise ReplayQualificationError(
            "replay_evidence_closure_mismatch",
            "runtime asset manifest is absent from replay evidence closure",
        )
    return _LoadedReplayEvidence(
        operator_before=operator_before,
        operator_after=operator_after,
        source_before=source_before,
        source_after_execution=source_after_execution,
        source_before_publish=source_before_publish,
        direct_supervisor=direct,
        kernel_evaluation=kernel,
        closure=closure,
    )


def _load_evidence_model(
    store: LocalArtifactStore,
    ref: ArtifactRef,
    model: type[_ReplayClaim],
    *,
    label: str,
) -> _ReplayClaim:
    path = _resolve_evidence_ref(store, ref, label=label)
    try:
        payload = path.read_bytes()
        parsed = model.model_validate_json(payload)
    except (OSError, ValidationError) as error:
        raise ReplayQualificationError(
            "invalid_replay_evidence",
            f"{label} is not its strict canonical evidence schema",
        ) from error
    canonical = (
        json.dumps(
            parsed.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    if payload != canonical:
        raise ReplayQualificationError(
            "invalid_replay_evidence",
            f"{label} does not use canonical evidence bytes",
        )
    if ref.media_type != "application/json" or ref.schema_version != parsed.schema_version:
        raise ReplayQualificationError(
            "invalid_replay_evidence_type",
            f"{label} ArtifactRef does not declare its exact JSON schema",
        )
    return parsed


def _resolve_evidence_ref(
    store: LocalArtifactStore,
    ref: ArtifactRef,
    *,
    label: str,
) -> Path:
    if ref.uri != f"artifact://sha256/{ref.sha256}":
        raise ReplayQualificationError(
            "replay_evidence_locator_invalid",
            f"{label} must use its exact content-addressed artifact URI",
        )
    try:
        digest_path = store.resolve_digest(ref.sha256)
        resolved = store.resolve(ref)
    except ArtifactResolutionError as error:
        raise ReplayQualificationError(
            "replay_evidence_unavailable",
            f"{label} is absent or corrupt in the qualification CAS",
        ) from error
    if digest_path != resolved.path:
        raise ReplayQualificationError(
            "replay_evidence_locator_invalid",
            f"{label} digest and ArtifactRef resolve to different objects",
        )
    return resolved.path


def _discover_evidence_closure(
    store: LocalArtifactStore,
    roots: tuple[ArtifactRef, ...],
) -> tuple[ArtifactRef, ...]:
    by_digest: dict[str, ArtifactRef] = {}
    pending = list(roots)
    while pending:
        ref = pending.pop()
        existing = by_digest.get(ref.sha256)
        if existing is not None:
            if _artifact_content_identity(existing) != _artifact_content_identity(ref):
                raise ReplayQualificationError(
                    "replay_evidence_closure_mismatch",
                    "one evidence digest is named by conflicting ArtifactRefs",
                )
            by_digest[ref.sha256] = min((existing, ref), key=_artifact_sort_key)
            continue
        path = _resolve_evidence_ref(store, ref, label=f"evidence {ref.name}")
        by_digest[ref.sha256] = ref
        known_json = ref.schema_version in _KNOWN_JSON_SCHEMAS
        if known_json and ref.media_type != "application/json":
            raise ReplayQualificationError(
                "invalid_replay_evidence_type",
                "known JSON evidence is relabeled with another media type",
            )
        if ref.media_type != "application/json":
            continue
        value = _load_strict_json(path, label=f"evidence {ref.name}")
        if known_json and value.get("schema_version") != ref.schema_version:
            raise ReplayQualificationError(
                "invalid_replay_evidence_type",
                "known JSON evidence payload and ArtifactRef schema differ",
            )
        model = _KNOWN_JSON_MODELS.get(ref.schema_version or "")
        if model is not None:
            try:
                model.model_validate(value)
            except ValidationError as error:
                raise ReplayQualificationError(
                    "invalid_replay_evidence",
                    "known JSON evidence does not satisfy its strict model",
                ) from error
        pending.extend(_artifact_refs_in_json(value))
        pending.extend(_package_member_refs(value))
    return tuple(sorted(by_digest.values(), key=_artifact_sort_key))


def _artifact_refs_in_json(value: Any) -> tuple[ArtifactRef, ...]:
    refs: list[ArtifactRef] = []
    if isinstance(value, dict):
        required = {"name", "uri", "media_type", "sha256", "bytes", "schema_version"}
        keys = frozenset(value)
        if required <= keys:
            if keys not in {frozenset(required), frozenset((*required, "locator"))}:
                raise ReplayQualificationError(
                    "invalid_replay_evidence",
                    "evidence contains an ArtifactRef-shaped object with extra fields",
                )
            try:
                refs.append(ArtifactRef.model_validate({key: value[key] for key in required}))
            except ValidationError as error:
                raise ReplayQualificationError(
                    "invalid_replay_evidence",
                    "evidence contains an invalid ArtifactRef-shaped object",
                ) from error
        else:
            for nested in value.values():
                refs.extend(_artifact_refs_in_json(nested))
    elif isinstance(value, list):
        for nested in value:
            refs.extend(_artifact_refs_in_json(nested))
    return tuple(refs)


def _artifact_sort_key(ref: ArtifactRef) -> tuple[str, str, str, str, int, str]:
    return (
        ref.sha256,
        ref.name,
        ref.media_type,
        ref.schema_version or "",
        ref.bytes,
        ref.uri,
    )


def _artifact_content_identity(ref: ArtifactRef) -> tuple[str, str, str | None, int]:
    return (ref.uri, ref.media_type, ref.schema_version, ref.bytes)


def _package_member_refs(value: dict[str, Any]) -> tuple[ArtifactRef, ...]:
    if value.get("schema_version") != "robotwin.generated_scene_package.v1":
        return ()
    expected_keys = {
        "schema_version",
        "scene_id",
        "seed",
        "source_scene_spec_sha256",
        "resolved_scene_sha256",
        "asset_catalog_sha256",
        "compiler_version",
        "entrypoint",
        "resolved_only_entrypoint",
        "files",
    }
    if set(value) != expected_keys:
        _evidence_mismatch("environment package manifest has an open or incomplete shape")
    records = _require_array(value.get("files"), label="environment package files")
    layout = (
        ("request.txt", "text/plain", None),
        ("scene_spec.json", "application/json", "robotwin.scene_spec.v1"),
        ("resolved_scene.json", "application/json", "robotwin.resolved_scene.v1"),
        ("generated_scene.py", "text/x-python", None),
    )
    if len(records) != len(layout):
        _evidence_mismatch("environment package member set is not exact")
    refs: list[ArtifactRef] = []
    for raw, (expected_path, media_type, schema_version) in zip(records, layout, strict=True):
        record = _require_object(raw, label="environment package member")
        if set(record) != {"path", "sha256", "bytes"} or record.get("path") != expected_path:
            _evidence_mismatch("environment package member shape or ordering is invalid")
        try:
            refs.append(
                ArtifactRef(
                    name=Path(expected_path).stem,
                    uri=f"artifact://sha256/{record['sha256']}",
                    media_type=media_type,
                    sha256=record["sha256"],
                    bytes=record["bytes"],
                    schema_version=schema_version,
                )
            )
        except (KeyError, ValidationError) as error:
            raise ReplayQualificationError(
                "invalid_replay_evidence",
                "environment package member identity is invalid",
            ) from error
    return tuple(refs)


def _verify_environment_package(
    store: LocalArtifactStore,
    *,
    package: Any,
    manifest: dict[str, Any],
    case_binding: ReplayCaseBindingClaim,
) -> ResolvedSceneSpec:
    refs = _package_member_refs(manifest)
    by_name = {item.name: item for item in refs}
    try:
        scene_path = _resolve_evidence_ref(store, by_name["scene_spec"], label="scene spec")
        resolved_path = _resolve_evidence_ref(
            store,
            by_name["resolved_scene"],
            label="resolved scene",
        )
        request_path = _resolve_evidence_ref(store, by_name["request"], label="scene request")
        source_path = _resolve_evidence_ref(
            store,
            by_name["generated_scene"],
            label="generated scene source",
        )
        catalog_path = _resolve_evidence_ref(
            store,
            package.asset_catalog,
            label="environment asset catalog",
        )
        scene = SceneSpec.model_validate_json(scene_path.read_bytes())
        resolved = ResolvedSceneSpec.model_validate_json(resolved_path.read_bytes())
        catalog = AssetCatalog.model_validate_json(catalog_path.read_bytes())
    except (KeyError, OSError, ValidationError) as error:
        raise ReplayQualificationError(
            "invalid_replay_evidence",
            "environment package members do not satisfy their production schemas",
        ) from error
    if (
        catalog.digest() != package.asset_catalog.sha256
        or scene.digest() != package.scene_spec_sha256
        or resolved.digest() != package.resolved_scene_sha256
        or package.package_id != resolved.digest()
        or package.seed != scene.seed
        or scene.request != case_binding.request
        or scene.seed != case_binding.seed
        or resolved.source_scene_spec_sha256 != scene.digest()
        or resolved.asset_catalog_sha256 != catalog.digest()
        or resolved.scene_id != scene.scene_id
        or resolved.seed != scene.seed
        or manifest.get("scene_id") != scene.scene_id
        or manifest.get("seed") != scene.seed
        or manifest.get("source_scene_spec_sha256") != scene.digest()
        or manifest.get("resolved_scene_sha256") != resolved.digest()
        or manifest.get("asset_catalog_sha256") != catalog.digest()
        or manifest.get("compiler_version") != resolved.compiler_version
        or manifest.get("entrypoint") != "generated_scene.py:load_scene"
        or manifest.get("resolved_only_entrypoint")
        != "scene_gen.envs.generated_scene:load_resolved_scene"
    ):
        _evidence_mismatch("environment package members are not model- and digest-bound")
    try:
        request_bytes = request_path.read_bytes()
        source_bytes = source_path.read_bytes()
    except OSError as error:
        raise ReplayQualificationError(
            "replay_evidence_unavailable",
            "environment package source members cannot be reread",
        ) from error
    if (
        request_bytes != f"{scene.request}\n".encode()
        or source_bytes != generated_module_source(resolved).encode()
    ):
        _evidence_mismatch("environment package request or generated source is not reproducible")
    return resolved


def _verify_operator_manifests(
    store: LocalArtifactStore,
    *,
    before: ReplayOperatorInputsManifest,
    after: ReplayOperatorInputsManifest,
    case_binding: ReplayCaseBindingClaim,
    exact_dependencies: ReplayExactDependenciesClaim,
    media_decode: ReplayMediaDecodeClaim,
) -> None:
    if before.phase != "before_execution" or after.phase != "before_publish":
        _evidence_mismatch("operator input manifests have wrong phases")
    before_payload = before.model_dump(mode="json", exclude={"phase", "inputs_sha256"})
    after_payload = after.model_dump(mode="json", exclude={"phase", "inputs_sha256"})
    digest = _canonical_sha256(before_payload)
    if before_payload != after_payload or not (
        before.inputs_sha256 == after.inputs_sha256 == digest
    ):
        _evidence_mismatch("operator inputs changed during replay qualification")
    if (
        before.request != case_binding.request
        or before.seed != case_binding.seed
        or before.runtime_config != case_binding.runtime_config
    ):
        _evidence_mismatch("operator input manifest is not bound to the fixed case")
    labels = tuple(item.label for item in before.files)
    expected_labels = (
        "asset_catalog",
        "interpreter",
        "media_launcher",
        "runtime_capability",
        "runtime_runner",
        "static_ffmpeg",
    )
    if labels != expected_labels:
        _evidence_mismatch("operator file locator set is not exact and sorted")
    for identity in before.files:
        path = _checked_machine_file(identity.path, label=identity.label)
        size, sha256 = _file_identity(path)
        if (size, sha256) != (identity.bytes, identity.sha256):
            _evidence_mismatch(f"operator file changed: {identity.label}")
    for root in (
        *before.allowed_asset_roots,
        before.runtime_module_root,
        before.delegated_cgroup_root,
    ):
        _checked_machine_directory(root)
    catalog_path = _resolve_evidence_ref(
        store,
        before.input_asset_catalog,
        label="operator asset catalog",
    )
    catalog = _load_strict_json(catalog_path, label="operator asset catalog")
    try:
        AssetCatalog.model_validate(catalog)
    except ValidationError as error:
        raise ReplayQualificationError(
            "invalid_replay_evidence",
            "operator asset catalog does not satisfy the production schema",
        ) from error
    capability_path = _resolve_evidence_ref(
        store,
        before.runtime_capability,
        label="runtime capability",
    )
    capability = _load_strict_json(capability_path, label="runtime capability")
    try:
        validate_runtime_capability_document(capability)
    except RuntimeCapabilityError as error:
        raise ReplayQualificationError(
            "invalid_replay_evidence",
            "operator runtime capability does not satisfy the production protocol",
        ) from error
    if canonical_capability_bytes(capability) != capability_path.read_bytes():
        _evidence_mismatch("operator runtime capability bytes are not canonical")
    files = {item.label: item for item in before.files}
    dependencies = {item.name: item for item in exact_dependencies.dependencies}
    if (
        before.input_asset_catalog.sha256 != files["asset_catalog"].sha256
        or before.runtime_capability.sha256 != files["runtime_capability"].sha256
        or dependencies[REPLAY_CAPABILITY_DEPENDENCY].sha256 != before.runtime_capability.sha256
        or files["media_launcher"].sha256 != media_decode.launcher_sha256
        or files["static_ffmpeg"].sha256 != media_decode.ffmpeg_sha256
    ):
        _evidence_mismatch("operator catalog or capability is not dependency-bound")


def _verify_source_manifests(
    *,
    implementation_root: Path,
    generic: _QualificationInspection,
    claim: ReplaySourceStabilityClaim,
    before: ReplayHarnessSourceManifest,
    after_execution: ReplayHarnessSourceManifest,
    before_publish: ReplayHarnessSourceManifest,
) -> None:
    if (
        before.phase != "before_execution"
        or after_execution.phase != "after_execution"
        or before_publish.phase != "before_publish"
    ):
        _evidence_mismatch("Harness source manifests have wrong phases")
    manifests = (before, after_execution, before_publish)
    expected_root = implementation_root.resolve(strict=True)
    expected_harness = expected_root / "self_improving" / "harness"
    if any(
        Path(item.distribution_root) != expected_root or Path(item.harness_root) != expected_harness
        for item in manifests
    ):
        _evidence_mismatch("Harness source manifests are not bound to the actual source root")
    if not (
        before.files == after_execution.files == before_publish.files == generic.manifest.files
        and before.tree_sha256
        == after_execution.tree_sha256
        == before_publish.tree_sha256
        == claim.harness_tree_sha256
    ):
        _evidence_mismatch("Harness source bytes changed or the generic manifest is incomplete")
    actual = _snapshot_harness_source(implementation_root, phase="before_publish")
    if actual.files != before_publish.files or actual.tree_sha256 != before_publish.tree_sha256:
        _evidence_mismatch("current complete Harness source tree differs from qualification")


def _verify_run_evidence(
    store: LocalArtifactStore,
    *,
    receipt: ReplayDirectSupervisorReceipt | ReplayKernelEvaluationReceipt,
    execution: ReplayExecutionClaim | ReplayQualificationCandidateExecutionClaim,
    lifecycle: ReplayLifecycleRunClaim,
    media: ReplayMediaDecodeRunClaim,
    physics: ReplayPhysicsValidationRunClaim,
    runtime_asset_snapshot: ReplayRuntimeAssetSnapshotClaim,
    exact_dependencies: ReplayExactDependenciesClaim,
    case_binding: ReplayCaseBindingClaim,
) -> None:
    invocation = receipt.invocation
    state = receipt.run_state
    if (
        receipt.run_id != execution.run_id
        or invocation.run_id != receipt.run_id
        or state.run_id != receipt.run_id
        or invocation.invocation_digest != execution.invocation_digest
        or state.invocation_digest != invocation.invocation_digest
        or invocation.skill_id != "text2env.replay"
        or invocation.skill_version != "1.0.0"
        or invocation.max_attempts != 2
        or invocation.dependencies != exact_dependencies.dependencies
        or receipt.exact_dependencies != exact_dependencies.dependencies
        or state.status is not RunStatus.SUCCEEDED
        or state.attempt != 1
        or state.max_attempts != 2
        or receipt.handler_call_count != 1
        or receipt.runtime_call_count != 1
        or state.artifacts != receipt.artifact_closure
        or state.output != receipt.typed_output.model_dump(mode="json")
    ):
        _evidence_mismatch("persisted Invocation/RunState receipt is inconsistent")
    try:
        replay_input = Text2EnvReplayInput.model_validate(invocation.effective_parameters)
        typed_state_output = Text2EnvReplayOutput.model_validate(state.output)
    except ValidationError as error:
        raise ReplayQualificationError(
            "invalid_replay_evidence",
            "persisted replay input or output is not the production schema",
        ) from error
    if typed_state_output != receipt.typed_output:
        _evidence_mismatch("receipt typed output differs from persisted RunState")
    expected_invocation_digest = _invocation_digest(
        skill_id=invocation.skill_id,
        skill_version=invocation.skill_version,
        effective_parameters=replay_input,
        dependencies=invocation.dependencies,
        max_attempts=invocation.max_attempts,
    )
    if invocation.invocation_digest != expected_invocation_digest:
        _evidence_mismatch("persisted Invocation digest is not canonical")
    package = replay_input.environment_package
    if (
        package.package_id != case_binding.environment_package_id
        or package.scene_spec_sha256 != case_binding.scene_spec_sha256
        or package.resolved_scene_sha256 != case_binding.resolved_scene_sha256
        or package.asset_catalog.sha256 != case_binding.asset_catalog_sha256
        or package.package_manifest.sha256 != case_binding.package_manifest_sha256
        or replay_input.runtime_config.model_dump(mode="json")
        != case_binding.runtime_config.model_dump(mode="json")
    ):
        _evidence_mismatch("persisted replay input differs from fixed case binding")
    transcript = _load_evidence_model(
        store,
        receipt.harness_event_transcript,
        ReplayHarnessEventTranscript,
        label=f"{receipt.mode} Harness event transcript",
    )
    if (
        transcript.mode != receipt.mode
        or transcript.run_id != receipt.run_id
        or transcript.events != state.events
        or (
            receipt.mode == "qualification_candidate"
            and any(
                not event.stage.startswith("qualification_candidate.")
                for event in transcript.events
            )
        )
    ):
        _evidence_mismatch("Harness event journal transcript differs from persisted RunState")
    runtime_transcript_path = _resolve_evidence_ref(
        store,
        receipt.runtime_event_transcript,
        label=f"{receipt.mode} runtime delivery transcript",
    )
    try:
        runtime_events = RuntimeEventCodec(
            allowed_artifact_paths=RUNTIME_ARTIFACT_PATHS
        ).parse_transcript(runtime_transcript_path.read_bytes())
    except (OSError, ValueError) as error:
        raise ReplayQualificationError(
            "invalid_replay_evidence",
            "runtime delivery transcript is invalid",
        ) from error
    checkpoints = tuple(
        item.completed_steps
        for item in runtime_events
        if item.kind is RuntimeEventKind.SIMULATION_CHECKPOINT
    )
    completed = next(
        (
            item.completed_steps
            for item in runtime_events
            if item.kind is RuntimeEventKind.SIMULATION_COMPLETED
        ),
        None,
    )
    if (
        receipt.runtime_event_transcript.sha256 != lifecycle.transcript_sha256
        or tuple(item.kind.value for item in runtime_events) != lifecycle.event_kinds
        or checkpoints != lifecycle.checkpoint_completed_steps
        or completed != lifecycle.simulation_completed_steps
    ):
        _evidence_mismatch("runtime transcript differs from lifecycle claim")
    by_digest = {item.sha256: item for item in receipt.artifact_closure}
    if len(by_digest) != len(receipt.artifact_closure):
        _evidence_mismatch("run artifact closure contains duplicate digests")
    required_digests = {
        execution.execution_receipt_sha256,
        execution.runtime_evidence_sha256,
        execution.validation_report_sha256,
        execution.event_transcript_sha256,
        execution.runtime_asset_manifest_sha256,
        execution.video_sha256,
    }
    if not required_digests.issubset(by_digest):
        _evidence_mismatch("run receipt omits a claimed execution artifact")
    if _canonical_sha256(receipt.typed_output.model_dump(mode="json")) != execution.output_sha256:
        _evidence_mismatch("typed output digest differs from execution claim")
    package_manifest = _load_strict_json(
        _resolve_evidence_ref(
            store, package.package_manifest, label="environment package manifest"
        ),
        label="environment package manifest",
    )
    resolved_scene = _verify_environment_package(
        store,
        package=package,
        manifest=package_manifest,
        case_binding=case_binding,
    )
    try:
        asset_catalog = AssetCatalog.model_validate_json(
            _resolve_evidence_ref(
                store,
                package.asset_catalog,
                label="environment asset catalog",
            ).read_bytes()
        )
    except (OSError, ValidationError) as error:
        raise ReplayQualificationError(
            "invalid_replay_evidence",
            "environment asset catalog cannot be reloaded for runtime validation",
        ) from error
    execution_receipt = _load_strict_json(
        _resolve_evidence_ref(
            store,
            by_digest[execution.execution_receipt_sha256],
            label="handler execution receipt",
        ),
        label="handler execution receipt",
    )
    if set(execution_receipt) != {
        "schema_version",
        "skill_ref",
        "environment_package_id",
        "package_manifest_sha256",
        "asset_catalog_sha256",
        "resolved_scene_sha256",
        "runtime_config",
        "invocation_dependencies",
        "handler_configuration",
        "capability",
        "transcript",
        "exit_code",
        "runtime_evidence",
        "runtime_validation_report",
        "media",
        "media_verification",
        "runtime_assets",
    }:
        _evidence_mismatch("handler execution receipt has an open or incomplete shape")
    try:
        receipt_dependencies = tuple(
            DependencyRef.model_validate(item)
            for item in _require_array(
                execution_receipt.get("invocation_dependencies"),
                label="receipt dependencies",
            )
        )
    except ValidationError as error:
        raise ReplayQualificationError(
            "invalid_replay_evidence",
            "handler execution receipt dependencies are not typed",
        ) from error
    if (
        execution_receipt.get("schema_version") != "harness.text2env_replay_receipt.v1"
        or execution_receipt.get("exit_code") != 0
        or execution_receipt.get("skill_ref") != TEXT2ENV_REPLAY_SKILL_REF
        or execution_receipt.get("environment_package_id") != package.package_id
        or execution_receipt.get("package_manifest_sha256") != package.package_manifest.sha256
        or execution_receipt.get("asset_catalog_sha256") != package.asset_catalog.sha256
        or execution_receipt.get("resolved_scene_sha256") != package.resolved_scene_sha256
        or execution_receipt.get("runtime_config")
        != replay_input.runtime_config.model_dump(mode="json")
        or receipt_dependencies != exact_dependencies.dependencies
    ):
        _evidence_mismatch("handler execution receipt is not invocation-bound")
    dependencies_by_name = {item.name: item for item in exact_dependencies.dependencies}
    handler_configuration = _require_object(
        execution_receipt.get("handler_configuration"),
        label="handler configuration receipt",
    )
    if set(handler_configuration) != {
        "sha256",
        "dependency",
        "allowed_asset_roots",
        "expected_capability_sha256",
        "expected_runtime_asset_snapshot_sha256",
        "min_visible_pixels",
        "checkpoint_steps",
        "task_config",
        "media_verifier",
        "runtime_executor",
    }:
        _evidence_mismatch("handler configuration receipt has an open or incomplete shape")
    media_verifier = _require_object(
        handler_configuration.get("media_verifier"),
        label="handler media verifier identity",
    )
    runtime_executor = _require_object(
        handler_configuration.get("runtime_executor"),
        label="handler runtime executor identity",
    )
    capability = _require_object(
        execution_receipt.get("capability"),
        label="runtime capability receipt",
    )
    allowed_roots = _require_object(
        handler_configuration.get("allowed_asset_roots"),
        label="handler allowed asset roots identity",
    )
    try:
        handler_dependency_record = DependencyRef.model_validate(
            handler_configuration.get("dependency")
        )
    except ValidationError as error:
        raise ReplayQualificationError(
            "invalid_replay_evidence",
            "handler dependency receipt is not typed",
        ) from error
    if (
        set(media_verifier) != {"sha256", "document", "scope", "handler_scope"}
        or set(runtime_executor) != {"sha256", "document", "scope"}
        or set(capability) != {"sha256", "bytes", "postflight_sha256"}
        or set(allowed_roots) != {"count", "configuration_sha256"}
        or type(allowed_roots.get("count")) is not int
        or allowed_roots.get("count", 0) < 1
        or handler_configuration.get("expected_capability_sha256")
        != dependencies_by_name[REPLAY_CAPABILITY_DEPENDENCY].sha256
        or handler_configuration.get("expected_runtime_asset_snapshot_sha256") is not None
        or handler_configuration.get("min_visible_pixels") != 64
        or handler_configuration.get("checkpoint_steps") != 120
        or handler_configuration.get("task_config") != "demo_clean"
        or _identity_document_sha256(media_verifier.get("document")) != media_verifier.get("sha256")
        or _identity_document_sha256(runtime_executor.get("document"))
        != runtime_executor.get("sha256")
        or handler_dependency_record != dependencies_by_name[REPLAY_HANDLER_CONFIG_DEPENDENCY]
        or handler_configuration.get("sha256")
        != dependencies_by_name[REPLAY_HANDLER_CONFIG_DEPENDENCY].sha256
        or media_verifier.get("sha256")
        != dependencies_by_name[REPLAY_MEDIA_VERIFIER_DEPENDENCY].sha256
        or runtime_executor.get("sha256") != dependencies_by_name[REPLAY_EXECUTOR_DEPENDENCY].sha256
        or capability.get("sha256") != dependencies_by_name[REPLAY_CAPABILITY_DEPENDENCY].sha256
        or capability.get("postflight_sha256")
        != dependencies_by_name[REPLAY_CAPABILITY_DEPENDENCY].sha256
    ):
        _evidence_mismatch(
            "handler, executor, media, or capability identity is not dependency-bound"
        )
    transcript_identity = _require_object(
        execution_receipt.get("transcript"),
        label="handler runtime transcript identity",
    )
    runtime_evidence_identity = _require_object(
        execution_receipt.get("runtime_evidence"),
        label="handler runtime evidence identity",
    )
    validation_identity = _require_object(
        execution_receipt.get("runtime_validation_report"),
        label="handler runtime validation identity",
    )
    if (
        not _artifact_identity_matches(transcript_identity, receipt.runtime_event_transcript)
        or not _artifact_identity_matches(
            runtime_evidence_identity,
            by_digest[execution.runtime_evidence_sha256],
        )
        or set(validation_identity) != {"sha256", "bytes", "media_type", "schema_version", "status"}
        or validation_identity.get("status") != "pass"
        or not _artifact_identity_matches(
            {key: value for key, value in validation_identity.items() if key != "status"},
            by_digest[execution.validation_report_sha256],
        )
    ):
        _evidence_mismatch("handler receipt artifact identities are not closure-bound")
    diagnostics_ref = _one_schema_artifact(
        receipt.artifact_closure,
        schema_version="harness.runtime_execution_diagnostics.v1",
        label="runtime execution diagnostics",
    )
    diagnostics = _load_strict_json(
        _resolve_evidence_ref(store, diagnostics_ref, label="runtime execution diagnostics"),
        label="runtime execution diagnostics",
    )
    if set(diagnostics) != {
        "schema_version",
        "status",
        "failure",
        "secondary_failures",
        "exit_code",
        "capability_sha256",
        "postflight_capability_sha256",
        "transcript",
        "stdout",
        "stderr",
        "probe_artifacts",
    }:
        _evidence_mismatch("runtime diagnostics have an open or incomplete shape")
    diagnostic_transcript = _require_object(
        diagnostics.get("transcript"),
        label="runtime diagnostic transcript",
    )
    diagnostic_stdout = _require_object(diagnostics.get("stdout"), label="runtime stdout")
    diagnostic_stderr = _require_object(diagnostics.get("stderr"), label="runtime stderr")
    probe_records = _require_array(
        diagnostics.get("probe_artifacts"),
        label="runtime probe artifacts",
    )
    try:
        capability_path = store.resolve_digest(
            dependencies_by_name[REPLAY_CAPABILITY_DEPENDENCY].sha256
        )
    except ArtifactResolutionError as error:
        raise ReplayQualificationError(
            "replay_evidence_unavailable",
            "runtime capability named by diagnostics is unavailable",
        ) from error
    if (
        diagnostics.get("schema_version") != "harness.runtime_execution_diagnostics.v1"
        or diagnostics.get("status") != "succeeded"
        or diagnostics.get("failure") is not None
        or diagnostics.get("secondary_failures") != []
        or diagnostics.get("exit_code") != 0
        or diagnostics.get("capability_sha256")
        != dependencies_by_name[REPLAY_CAPABILITY_DEPENDENCY].sha256
        or diagnostics.get("postflight_capability_sha256")
        != dependencies_by_name[REPLAY_CAPABILITY_DEPENDENCY].sha256
        or capability_path.stat().st_size != capability.get("bytes")
        or set(diagnostic_transcript)
        != {"sha256", "bytes", "media_type", "schema_version", "complete", "truncated"}
        or not _artifact_identity_matches(
            {
                key: value
                for key, value in diagnostic_transcript.items()
                if key not in {"complete", "truncated"}
            },
            receipt.runtime_event_transcript,
        )
        or diagnostic_transcript.get("complete") is not True
        or diagnostic_transcript.get("truncated") is not False
        or not _diagnostic_stream_identity_is_bound(diagnostic_stdout, by_digest)
        or not _diagnostic_stream_identity_is_bound(diagnostic_stderr, by_digest)
        or diagnostic_stdout.get("truncated") is not False
        or diagnostic_stderr.get("truncated") is not False
        or not _probe_artifact_records_are_bound(
            probe_records,
            by_digest,
            store=store,
            expected_capability_sha256=(dependencies_by_name[REPLAY_CAPABILITY_DEPENDENCY].sha256),
        )
    ):
        _evidence_mismatch("runtime diagnostics do not bind the complete delivery transcript")
    runtime_assets = _require_object(
        execution_receipt.get("runtime_assets"),
        label="receipt runtime assets",
    )
    member_records = _require_array(runtime_assets.get("members"), label="runtime asset members")
    manifest_record = _require_object(
        runtime_assets.get("manifest"),
        label="runtime asset manifest",
    )
    try:
        member_refs = tuple(ArtifactRef.model_validate(item) for item in member_records)
        runtime_asset_manifest_ref = ArtifactRef.model_validate(manifest_record)
        runtime_asset_dependency = DependencyRef.model_validate(runtime_assets.get("dependency"))
    except ValidationError as error:
        raise ReplayQualificationError(
            "invalid_replay_evidence",
            "runtime asset receipt identities are not typed",
        ) from error
    if (
        set(runtime_assets)
        != {
            "self_contained",
            "dependency",
            "expected_sha256",
            "dependency_enforced",
            "configured_snapshot_pin_enforced",
            "manifest",
            "members",
        }
        or runtime_assets.get("self_contained") is not True
        or runtime_assets.get("dependency_enforced") is not True
        or runtime_assets.get("expected_sha256") is not None
        or runtime_assets.get("configured_snapshot_pin_enforced") is not False
        or runtime_asset_manifest_ref != by_digest[execution.runtime_asset_manifest_sha256]
        or runtime_asset_dependency != dependencies_by_name[REPLAY_RUNTIME_ASSET_DEPENDENCY]
        or any(item.sha256 not in by_digest for item in member_refs)
        or len(member_refs) != runtime_asset_snapshot.member_count
    ):
        _evidence_mismatch("runtime asset manifest and members are not closure-bound")
    _verify_runtime_asset_manifest(
        store,
        manifest=by_digest[execution.runtime_asset_manifest_sha256],
        members=member_refs,
        resolved_scene=resolved_scene,
        asset_catalog_sha256=package.asset_catalog.sha256,
    )
    media_verification = _require_object(
        execution_receipt.get("media_verification"),
        label="receipt media verification",
    )
    video = _require_object(media_verification.get("video"), label="receipt decoded video")
    if (
        set(media_verification) != {"verifier_identity_sha256", "pngs", "video"}
        or media_verification.get("verifier_identity_sha256")
        != dependencies_by_name[REPLAY_MEDIA_VERIFIER_DEPENDENCY].sha256
        or set(video)
        != {
            "locator",
            "sha256",
            "bytes",
            "frame_count",
            "source_unique_frame_count",
            "decoded_unique_frame_count",
            "fps_numerator",
            "fps_denominator",
            "width",
            "height",
            "format_name",
            "codec_name",
            "pixel_format",
            "sample_aspect_ratio",
            "square_sample_aspect_ratio_defaulted",
            "sandbox_metrics",
        }
        or video.get("sha256") != media.video_sha256
        or video.get("frame_count") != media.frame_count
        or video.get("source_unique_frame_count") != media.source_unique_frame_count
        or video.get("decoded_unique_frame_count") != media.decoded_unique_frame_count
        or video.get("fps_numerator") != media.fps_numerator
        or video.get("fps_denominator") != media.fps_denominator
        or video.get("width") != media.width
        or video.get("height") != media.height
        or video.get("format_name") != media.format_name
        or video.get("codec_name") != media.codec_name
        or video.get("pixel_format") != media.pixel_format
        or video.get("sample_aspect_ratio") != media.sample_aspect_ratio
        or video.get("square_sample_aspect_ratio_defaulted")
        != media.square_sample_aspect_ratio_defaulted
        or not _valid_sandbox_metrics(video.get("sandbox_metrics"))
    ):
        _evidence_mismatch("media verifier receipt differs from media claim")
    media_records = _require_array(execution_receipt.get("media"), label="receipt media")
    media_by_locator: dict[str, ArtifactRef] = {}
    for record in media_records:
        value = _require_object(record, label="receipt media record")
        locator = value.get("locator")
        try:
            ref = ArtifactRef.model_validate(
                {key: nested for key, nested in value.items() if key != "locator"}
            )
        except ValidationError as error:
            raise ReplayQualificationError(
                "invalid_replay_evidence",
                "receipt media record is not a typed artifact identity",
            ) from error
        if (
            not isinstance(locator, str)
            or locator in media_by_locator
            or ref.sha256 not in by_digest
        ):
            _evidence_mismatch("receipt media artifacts are not uniquely closure-bound")
        media_by_locator[locator] = ref
    if tuple(media_by_locator) != RUNTIME_MEDIA_ARTIFACT_PATHS:
        _evidence_mismatch("receipt media artifact set is not the exact runtime output set")
    png_facts = _require_array(media_verification.get("pngs"), label="decoded PNG facts")
    if len(png_facts) != len(RUNTIME_MEDIA_ARTIFACT_PATHS) - 1:
        _evidence_mismatch("decoded PNG fact set is incomplete")
    for raw_fact, locator in zip(
        png_facts,
        RUNTIME_MEDIA_ARTIFACT_PATHS[:-1],
        strict=True,
    ):
        fact = _require_object(raw_fact, label="decoded PNG fact")
        artifact = media_by_locator[locator]
        if (
            set(fact)
            != {
                "locator",
                "sha256",
                "bytes",
                "width",
                "height",
                "mode",
                "format",
                "sandbox_metrics",
            }
            or fact.get("locator") != locator
            or fact.get("sha256") != artifact.sha256
            or fact.get("bytes") != artifact.bytes
            or type(fact.get("width")) is not int
            or fact.get("width", 0) < 1
            or type(fact.get("height")) is not int
            or fact.get("height", 0) < 1
            or not isinstance(fact.get("mode"), str)
            or not fact.get("mode")
            or fact.get("format") != "PNG"
            or not _valid_sandbox_metrics(fact.get("sandbox_metrics"))
        ):
            _evidence_mismatch("decoded PNG facts are not exact and artifact-bound")
    video_artifact = media_by_locator["observer_runtime.mp4"]
    if (
        video.get("locator") != "observer_runtime.mp4"
        or video.get("sha256") != video_artifact.sha256
        or video.get("bytes") != video_artifact.bytes
    ):
        _evidence_mismatch("decoded video facts are not artifact-bound")
    runtime_evidence = _load_strict_json(
        _resolve_evidence_ref(
            store,
            by_digest[physics.runtime_evidence_sha256],
            label="runtime physics evidence",
        ),
        label="runtime physics evidence",
    )
    validation = _load_strict_json(
        _resolve_evidence_ref(
            store,
            by_digest[physics.validation_report_sha256],
            label="runtime validation",
        ),
        label="runtime validation",
    )
    try:
        _verify_runtime_evidence(
            runtime_evidence,
            resolved=resolved_scene,
            config=replay_input.runtime_config,
            events=runtime_events,
            runtime_asset_snapshot_sha256=execution.runtime_asset_manifest_sha256,
        )
    except (TypeError, ValueError) as error:
        raise ReplayQualificationError(
            "invalid_replay_evidence",
            "runtime physics evidence fails the production replay schema gate",
        ) from error
    if (
        runtime_evidence.get("resolved_scene_sha256") != package.resolved_scene_sha256
        or runtime_evidence.get("simulation_step_count") != physics.settle_steps
        or runtime_evidence.get("contact_window_steps") != physics.contact_window_steps
        or runtime_evidence.get("video_frame_count") != physics.video_frame_count
        or runtime_evidence.get("unique_video_frame_count") != media.source_unique_frame_count
    ):
        _evidence_mismatch("physics evidence and validation do not prove the fixed run")
    _verify_authoritative_runtime_validation(
        runtime_evidence=runtime_evidence,
        persisted_validation=validation,
        resolved_scene=resolved_scene,
        asset_catalog=asset_catalog,
    )


def _verify_authoritative_runtime_validation(
    *,
    runtime_evidence: dict[str, Any],
    persisted_validation: dict[str, Any],
    resolved_scene: ResolvedSceneSpec,
    asset_catalog: AssetCatalog,
) -> None:
    """Recompute the production validator report from the persisted runtime facts."""

    objects = runtime_evidence.get("objects")
    relations = runtime_evidence.get("relations")
    expected_object_ids = {item.object_id for item in resolved_scene.objects}
    expected_relation_ids = {
        f"{relation.relation.value}:{relation.source}:{relation.target}"
        for relation in resolved_scene.relations
        if relation.target != "table"
        and relation.relation not in {RelationType.ON_TOP_OF, RelationType.INSIDE}
    }
    if (
        not isinstance(objects, dict)
        or set(objects) != expected_object_ids
        or any(not isinstance(value, dict) for value in objects.values())
        or not isinstance(relations, dict)
        or set(relations) != expected_relation_ids
        or any(not isinstance(value, dict) for value in relations.values())
    ):
        _evidence_mismatch(
            "runtime object or relation facts do not exactly cover the resolved scene"
        )
    try:
        recomputed = validate_resolved_scene(
            resolved_scene,
            catalog=asset_catalog,
            runtime_evidence=runtime_evidence,
            require_runtime=True,
            min_visible_pixels=64,
        )
        reports_match = _canonical_sha256(persisted_validation) == _canonical_sha256(recomputed)
    except (KeyError, TypeError, ValueError) as error:
        raise ReplayQualificationError(
            "invalid_replay_evidence",
            "runtime evidence cannot be evaluated by the production scene validator",
        ) from error
    if (
        recomputed.get("status") != "pass"
        or recomputed.get("fail_count") != 0
        or recomputed.get("not_run_count") != 0
        or not reports_match
    ):
        _evidence_mismatch(
            "persisted runtime validation is not the canonical production validator report"
        )


def _one_schema_artifact(
    refs: tuple[ArtifactRef, ...],
    *,
    schema_version: str,
    label: str,
) -> ArtifactRef:
    matches = tuple(item for item in refs if item.schema_version == schema_version)
    if len(matches) != 1:
        _evidence_mismatch(f"{label} is missing or duplicated in the run artifact closure")
    return matches[0]


def _verify_runtime_asset_manifest(
    store: LocalArtifactStore,
    *,
    manifest: ArtifactRef,
    members: tuple[ArtifactRef, ...],
    resolved_scene: ResolvedSceneSpec,
    asset_catalog_sha256: str,
) -> None:
    document = _load_strict_json(
        _resolve_evidence_ref(store, manifest, label="runtime asset manifest"),
        label="runtime asset manifest",
    )
    if set(document) != {
        "schema_version",
        "resolved_scene_sha256",
        "asset_catalog_sha256",
        "assets",
    } or (
        document.get("schema_version") != "harness.runtime_asset_snapshot.v1"
        or document.get("resolved_scene_sha256") != resolved_scene.digest()
        or document.get("asset_catalog_sha256") != asset_catalog_sha256
    ):
        _evidence_mismatch("runtime asset manifest is not package-bound or has extra fields")
    assets = _require_array(document.get("assets"), label="runtime asset manifest assets")
    if not assets:
        _evidence_mismatch("runtime asset manifest has no selected assets")
    expected_selections: dict[str, set[int]] = {}
    for item in resolved_scene.objects:
        expected_selections.setdefault(item.asset_id, set()).add(item.model_id)
    observed_selections: list[tuple[str, tuple[int, ...]]] = []
    expected_members: dict[str, int] = {}
    previous_asset_id: str | None = None
    for raw_asset in assets:
        asset = _require_object(raw_asset, label="runtime asset manifest asset")
        if set(asset) != {
            "asset_id",
            "selected_model_ids",
            "tree_sha256",
            "file_count",
            "bytes",
            "directories",
            "required_files",
            "files",
        }:
            _evidence_mismatch("runtime asset manifest asset has an open or incomplete shape")
        asset_id = asset.get("asset_id")
        directories = _require_array(asset.get("directories"), label="runtime asset directories")
        required_files = _require_array(
            asset.get("required_files"),
            label="runtime asset required files",
        )
        file_records = _require_array(asset.get("files"), label="runtime asset files")
        selected_models = _require_array(
            asset.get("selected_model_ids"),
            label="runtime selected model ids",
        )
        if (
            not isinstance(asset_id, str)
            or not asset_id
            or (previous_asset_id is not None and asset_id <= previous_asset_id)
            or not selected_models
            or any(type(item) is not int or item < 0 for item in selected_models)
            or selected_models != sorted(set(selected_models))
            or any(
                not isinstance(item, str) or not _safe_runtime_asset_path(item, allow_dot=True)
                for item in directories
            )
            or directories != sorted(set(directories))
            or not directories
            or any(
                not isinstance(item, str) or not _safe_runtime_asset_path(item, allow_dot=False)
                for item in required_files
            )
            or required_files != sorted(set(required_files))
            or not required_files
            or type(asset.get("file_count")) is not int
            or asset.get("file_count") != len(file_records)
            or type(asset.get("bytes")) is not int
        ):
            _evidence_mismatch("runtime asset manifest ordering or aggregate facts are invalid")
        previous_asset_id = asset_id
        observed_selections.append((asset_id, tuple(selected_models)))
        normalized_files: list[dict[str, Any]] = []
        previous_path: str | None = None
        total_bytes = 0
        for raw_record in file_records:
            record = _require_object(raw_record, label="runtime asset file")
            if set(record) != {"path", "sha256", "bytes"}:
                _evidence_mismatch("runtime asset file record has an open or incomplete shape")
            path = record.get("path")
            sha256 = record.get("sha256")
            size = record.get("bytes")
            if not isinstance(path, str):
                _evidence_mismatch("runtime asset file path is invalid")
            if (
                not _safe_runtime_asset_path(path, allow_dot=False)
                or (previous_path is not None and path <= previous_path)
                or not isinstance(sha256, str)
                or len(sha256) != 64
                or any(character not in "0123456789abcdef" for character in sha256)
                or type(size) is not int
                or size < 0
            ):
                _evidence_mismatch("runtime asset file identity is unsafe or unordered")
            previous_path = path
            previous_size = expected_members.get(sha256)
            if previous_size is not None and previous_size != size:
                _evidence_mismatch("one runtime asset digest declares conflicting sizes")
            expected_members[sha256] = size
            total_bytes += size
            normalized_files.append(record)
        if (
            asset.get("bytes") != total_bytes
            or asset.get("tree_sha256")
            != hashlib.sha256(
                canonical_runtime_asset_manifest_bytes(
                    {"directories": directories, "files": normalized_files}
                )
            ).hexdigest()
            or any(
                item not in {record["path"] for record in normalized_files}
                for item in required_files
            )
        ):
            _evidence_mismatch("runtime asset manifest aggregate or tree digest is invalid")
    if tuple(observed_selections) != tuple(
        (asset_id, tuple(sorted(model_ids)))
        for asset_id, model_ids in sorted(expected_selections.items())
    ):
        _evidence_mismatch("runtime asset manifest selection differs from the resolved scene")
    member_by_digest = {item.sha256: item.bytes for item in members}
    if (
        len(member_by_digest) != len(members)
        or member_by_digest != expected_members
        or tuple(sorted(members, key=lambda item: (item.sha256, item.bytes, item.name))) != members
    ):
        _evidence_mismatch("runtime asset manifest members are extra, omitted, or unordered")
    for member in members:
        _resolve_evidence_ref(store, member, label=f"runtime asset member {member.name}")


def _safe_runtime_asset_path(value: str, *, allow_dot: bool) -> bool:
    if (
        "\\" in value
        or value == ""
        or (value == "." and not allow_dot)
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return False
    path = PurePosixPath(value)
    return value == path.as_posix() and not path.is_absolute() and ".." not in path.parts


def _actual_distribution_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _require_actual_distribution_root(root: Path) -> Path:
    try:
        candidate = root.expanduser()
        resolved = candidate.resolve(strict=True)
    except (AttributeError, OSError) as error:
        raise ReplayQualificationError(
            "implementation_root_mismatch",
            "replay qualification implementation_root is invalid",
        ) from error
    if (
        candidate.is_symlink()
        or candidate.absolute() != resolved
        or resolved != _actual_distribution_root()
    ):
        raise ReplayQualificationError(
            "implementation_root_mismatch",
            "replay qualification must bind the repository containing qualify_replay.py",
        )
    return resolved


def _snapshot_harness_source(
    distribution_root: Path,
    *,
    phase: Literal["before_execution", "after_execution", "before_publish"],
) -> ReplayHarnessSourceManifest:
    root = distribution_root.expanduser().resolve(strict=True)
    harness_root = root / "self_improving" / "harness"
    files: list[ImplementationFileV1] = []
    for path in sorted(harness_root.rglob("*"), key=lambda item: item.as_posix()):
        relative_to_harness = path.relative_to(harness_root)
        if (
            "qualified_skills" in relative_to_harness.parts
            or "__pycache__" in relative_to_harness.parts
            or path.suffix in {".pyc", ".pyo"}
        ):
            continue
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ReplayQualificationError(
                "implementation_path_invalid",
                f"Harness source tree contains a symlink: {relative}",
            )
        if not path.is_file():
            continue
        size, sha256 = _file_identity(path)
        files.append(ImplementationFileV1(path=relative, bytes=size, sha256=sha256))
    if not files:
        raise ReplayQualificationError(
            "implementation_path_invalid",
            "Harness source tree is empty",
        )
    records = tuple(files)
    return ReplayHarnessSourceManifest(
        schema_version="harness.replay_qualification.harness_source_manifest.v1",
        phase=phase,
        machine_local=True,
        distribution_root=str(root),
        harness_root=str(harness_root),
        files=records,
        tree_sha256=_canonical_sha256(
            {"files": [item.model_dump(mode="json") for item in records]}
        ),
    )


def _checked_machine_file(value: str, *, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or path.is_symlink():
        _evidence_mismatch(f"operator file locator is unsafe: {label}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ReplayQualificationError(
            "replay_evidence_unavailable",
            f"operator file locator is unavailable: {label}",
        ) from error
    if resolved != path or not resolved.is_file():
        _evidence_mismatch(f"operator file locator is not canonical: {label}")
    return resolved


def _checked_machine_directory(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or path.is_symlink():
        _evidence_mismatch("operator directory locator is unsafe")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ReplayQualificationError(
            "replay_evidence_unavailable",
            "operator directory locator is unavailable",
        ) from error
    if resolved != path or not resolved.is_dir():
        _evidence_mismatch("operator directory locator is not canonical")
    return resolved


def _file_identity(path: Path) -> tuple[int, str]:
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise ReplayQualificationError(
            "replay_evidence_unavailable",
            f"evidence-bound file cannot be read: {path}",
        ) from error
    return len(payload), hashlib.sha256(payload).hexdigest()


def _load_strict_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_bytes(),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise ReplayQualificationError(
            "invalid_replay_evidence",
            f"{label} is not strict JSON",
        ) from error
    return _require_object(value, label=label)


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _require_object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReplayQualificationError(
            "invalid_replay_evidence",
            f"{label} must be an object",
        )
    return value


def _require_array(value: Any, *, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ReplayQualificationError(
            "invalid_replay_evidence",
            f"{label} must be an array",
        )
    return value


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _identity_document_sha256(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    try:
        payload = (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("ascii")
    except (TypeError, UnicodeEncodeError, ValueError):
        return ""
    return hashlib.sha256(payload).hexdigest()


def _artifact_identity_matches(value: dict[str, Any], artifact: ArtifactRef) -> bool:
    return set(value) == {"sha256", "bytes", "media_type", "schema_version"} and value == {
        "sha256": artifact.sha256,
        "bytes": artifact.bytes,
        "media_type": artifact.media_type,
        "schema_version": artifact.schema_version,
    }


def _valid_sandbox_metrics(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "memory_peak_bytes",
        "memory_events",
        "pids_peak",
        "pids_events",
        "cpu_stats",
    }:
        return False
    if (
        type(value.get("memory_peak_bytes")) is not int
        or value.get("memory_peak_bytes", -1) < 0
        or type(value.get("pids_peak")) is not int
        or value.get("pids_peak", -1) < 0
    ):
        return False
    for key in ("memory_events", "pids_events", "cpu_stats"):
        records = value.get(key)
        if not isinstance(records, list):
            return False
        names: list[str] = []
        for record in records:
            if (
                not isinstance(record, dict)
                or set(record) != {"name", "value"}
                or not isinstance(record.get("name"), str)
                or not record.get("name")
                or type(record.get("value")) is not int
                or record.get("value", -1) < 0
            ):
                return False
            names.append(record["name"])
        if names != sorted(set(names)):
            return False
    return True


def _diagnostic_stream_identity_is_bound(
    value: dict[str, Any],
    by_digest: dict[str, ArtifactRef],
) -> bool:
    if set(value) != {"sha256", "bytes", "media_type", "schema_version", "truncated"}:
        return False
    artifact = by_digest.get(value.get("sha256"))
    return artifact is not None and _artifact_identity_matches(
        {key: nested for key, nested in value.items() if key != "truncated"},
        artifact,
    )


def _probe_artifact_records_are_bound(
    values: list[Any],
    by_digest: dict[str, ArtifactRef],
    *,
    store: LocalArtifactStore,
    expected_capability_sha256: str,
) -> bool:
    seen_names: set[str] = set()
    records_by_name: dict[str, dict[str, Any]] = {}
    for value in values:
        if not isinstance(value, dict) or set(value) != {
            "name",
            "sha256",
            "bytes",
            "media_type",
            "schema_version",
        }:
            return False
        digest = value.get("sha256")
        name = value.get("name")
        artifact = by_digest.get(digest)
        if (
            not isinstance(digest, str)
            or not isinstance(name, str)
            or name not in _PROBE_ARTIFACT_NAMES
            or name in seen_names
            or artifact is None
            or not _artifact_identity_matches(
                {key: nested for key, nested in value.items() if key != "name"},
                artifact,
            )
        ):
            return False
        seen_names.add(name)
        records_by_name[name] = value
    if seen_names != _PROBE_ARTIFACT_NAMES:
        return False
    for phase in ("preflight", "postflight"):
        diagnostics_record = records_by_name[f"{phase}_probe_diagnostics"]
        diagnostics_ref = by_digest[diagnostics_record["sha256"]]
        if (
            diagnostics_ref.media_type != "application/json"
            or diagnostics_ref.schema_version != "harness.runtime_probe_diagnostics.v1"
        ):
            return False
        try:
            diagnostics = _load_strict_json(
                _resolve_evidence_ref(
                    store,
                    diagnostics_ref,
                    label=f"{phase} runtime probe diagnostics",
                ),
                label=f"{phase} runtime probe diagnostics",
            )
        except ReplayQualificationError:
            return False
        if (
            set(diagnostics) != {"schema_version", "phase", "exit_code", "streams"}
            or diagnostics.get("schema_version") != "harness.runtime_probe_diagnostics.v1"
            or diagnostics.get("phase") != phase
            or type(diagnostics.get("exit_code")) is not int
            or diagnostics.get("exit_code") != 0
        ):
            return False
        streams = diagnostics.get("streams")
        if not isinstance(streams, dict) or set(streams) != {
            "stdout",
            "stderr",
            "capability_output",
        }:
            return False
        for label in ("stdout", "stderr", "capability_output"):
            stream = streams.get(label)
            aggregate = records_by_name[f"{phase}_probe_{label}"]
            expected_type = (
                ("application/json", RUNTIME_CAPABILITY_SCHEMA)
                if label == "capability_output"
                else ("application/octet-stream", None)
            )
            if (
                not isinstance(stream, dict)
                or set(stream) != {"sha256", "bytes", "media_type", "schema_version", "truncated"}
                or stream.get("truncated") is not False
                or (stream.get("media_type"), stream.get("schema_version")) != expected_type
                or (
                    label == "capability_output"
                    and stream.get("sha256") != expected_capability_sha256
                )
                or {key: nested for key, nested in stream.items() if key != "truncated"}
                != {key: nested for key, nested in aggregate.items() if key != "name"}
            ):
                return False
    return True


def _evidence_mismatch(message: str) -> None:
    raise ReplayQualificationError("replay_evidence_mismatch", message)


def _verify_media_claim(media: ReplayMediaDecodeRunClaim) -> None:
    if (
        not media.fully_decoded
        or media.frame_count != 120
        or media.source_unique_frame_count < _MIN_UNIQUE_VIDEO_FRAMES
        or media.source_unique_frame_count > media.frame_count
        or media.decoded_unique_frame_count < _MIN_UNIQUE_VIDEO_FRAMES
        or media.decoded_unique_frame_count > media.frame_count
        or media.fps_numerator != 12
        or media.fps_denominator != 1
        or media.format_name != "iso-bmff/mp4"
        or media.codec_name != "h264"
        or media.pixel_format != "8bit-420"
        or media.sample_aspect_ratio not in {"0/1", "1/1"}
        or media.square_sample_aspect_ratio_defaulted != (media.sample_aspect_ratio == "0/1")
        or media.decoded_png_count != 7
        or not media.all_pngs_decoded
    ):
        _claim_mismatch("media decode facts do not prove the fixed complete media set")


def _claim_mismatch(message: str) -> None:
    raise ReplayQualificationError("replay_claim_mismatch", message)
