"""Path-independent release contracts for the limited image preview library."""

from __future__ import annotations

import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import UUID4, BaseModel, Field, model_validator

from .base import HarnessModel, NonNegativeInt, PositiveInt, Sha256, public_schema_config
from .common import ArtifactRef
from .limited_image_preview_execution import LimitedSceneValidationV1, PreviewLayout
from .workflow import PrincipalId

LIMITED_IMAGE_PREVIEW_MANIFEST_SCHEMA_ID = "harness.limited_image_preview_manifest.v1"
LIMITED_IMAGE_PREVIEW_LIBRARY_INDEX_SCHEMA_ID = "harness.limited_image_preview_library_index.v1"

_TIMING_SEGMENTS = (
    "input_asset",
    "compile",
    "runtime_integrity",
    "simulation_render",
    "cas_deep_read",
    "codex_mcp",
    "validate_publish",
)
TimingSegment = Literal[
    "input_asset",
    "compile",
    "runtime_integrity",
    "simulation_render",
    "cas_deep_read",
    "codex_mcp",
    "validate_publish",
]


def _json_value(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _json_value(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(child) for child in value]
    return value


def _self_hash(value: object, *, field: str, domain: str) -> str:
    document = _json_value(value)
    if not isinstance(document, dict):
        raise TypeError("self-hashed preview value must be a model or dict")
    body = dict(document)
    body.pop(field, None)
    payload = json.dumps(
        {"domain": domain, "record": body},
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload + b"\n").hexdigest()


def limited_image_preview_manifest_sha256(value: object) -> str:
    return _self_hash(
        value,
        field="preview_id",
        domain="harness.limited_image_preview_manifest.identity.v1",
    )


def limited_image_preview_library_index_sha256(value: object) -> str:
    return _self_hash(
        value,
        field="index_sha256",
        domain="harness.limited_image_preview_library_index.identity.v1",
    )


def _require_ref(
    ref: ArtifactRef,
    *,
    label: str,
    schema_version: str | None = None,
    media_type: str | None = None,
    media_prefix: str | None = None,
) -> None:
    if ref.uri != f"artifact://sha256/{ref.sha256}":
        raise ValueError(f"{label} must use its content-addressed artifact URI")
    if schema_version is not None and ref.schema_version != schema_version:
        raise ValueError(f"{label} must use schema_version={schema_version}")
    if media_type is not None and ref.media_type != media_type:
        raise ValueError(f"{label} must use media_type={media_type}")
    if media_prefix is not None and not ref.media_type.startswith(media_prefix):
        raise ValueError(f"{label} must use a {media_prefix}* media type")


def _require_json_ref(ref: ArtifactRef, *, label: str, schema_version: str) -> None:
    _require_ref(
        ref,
        label=label,
        schema_version=schema_version,
        media_type="application/json",
    )


class LimitedImagePreviewTimingV1(HarnessModel):
    """One measured monotonic duration in the fixed seven-segment vocabulary."""

    segment: TimingSegment
    duration_ns: PositiveInt


class LimitedImagePreviewStageV1(HarnessModel):
    """One existing native compile/replay/validate stage bound for preview."""

    phase: Literal["initial", "revised"]
    compile_skill_ref: Literal["image2env.compile@2.0.0", "image2env.compile@3.0.0"]
    compile_request: ArtifactRef
    compile_receipt: ArtifactRef
    prompt_revision: ArtifactRef | None
    scene: ArtifactRef
    runtime_manifest: ArtifactRef
    replay_receipt: ArtifactRef
    replay: ArtifactRef
    replay_report: ArtifactRef
    baseline_preview: ArtifactRef
    half_dt_preview: ArtifactRef
    retained_media_index: ArtifactRef
    fresh_observation: ArtifactRef | None
    validation: ArtifactRef
    validation_result: LimitedSceneValidationV1
    workflow_head: ArtifactRef

    @model_validator(mode="after")
    def exact_native_stage(self) -> "LimitedImagePreviewStageV1":
        if self.phase == "initial":
            expected = (
                "image2env.compile@2.0.0",
                "harness.image2env_genesis_compile_request.v1",
                "harness.image2env_genesis_compile_receipt.v1",
                "harness.genesis_scene_execution_receipt.v2",
            )
            if self.prompt_revision is not None or self.fresh_observation is None:
                raise ValueError("initial stage requires observation and forbids prompt revision")
            _require_json_ref(
                self.fresh_observation,
                label="fresh_observation",
                schema_version="harness.genesis_fresh_capture_receipt.v1",
            )
        else:
            expected = (
                "image2env.compile@3.0.0",
                "harness.image2env_genesis_compile_request.v2",
                "harness.image2env_genesis_compile_receipt.v2",
                "harness.genesis_scene_execution_receipt.v3",
            )
            if self.prompt_revision is None or self.fresh_observation is not None:
                raise ValueError("revised stage requires PromptRevisionV3 and v3 replay media")
            _require_json_ref(
                self.prompt_revision,
                label="prompt_revision",
                schema_version="harness.prompt_revision.v3",
            )
        skill, request_schema, receipt_schema, replay_receipt_schema = expected
        if self.compile_skill_ref != skill:
            raise ValueError("stage compile Skill differs from its phase")
        for ref, label, schema in (
            (self.compile_request, "compile_request", request_schema),
            (self.compile_receipt, "compile_receipt", receipt_schema),
            (self.scene, "scene", "harness.genesis_scene_spec.v1"),
            (self.runtime_manifest, "runtime_manifest", "harness.genesis_runtime_manifest.v1"),
            (self.replay_receipt, "replay_receipt", replay_receipt_schema),
            (self.replay, "replay", "harness.genesis_scene_replay.v1"),
            (self.replay_report, "replay_report", "harness.genesis_scene_report.v1"),
            (self.validation, "validation", "harness.limited_scene_validation.v1"),
            (self.workflow_head, "workflow_head", "harness.workflow_operation_receipt.v2"),
        ):
            _require_json_ref(ref, label=label, schema_version=schema)
        for ref, label in (
            (self.baseline_preview, "baseline_preview"),
            (self.half_dt_preview, "half_dt_preview"),
        ):
            _require_ref(ref, label=label, media_type="video/mp4")
            if ref.schema_version is not None:
                raise ValueError(f"{label} cannot claim a JSON schema")
        _require_ref(
            self.retained_media_index,
            label="retained_media_index",
            media_type="application/json",
        )
        if self.retained_media_index.schema_version is not None:
            raise ValueError("retained_media_index is the native untyped retained-output index")
        result = self.validation_result
        if (
            result.execution_receipt != self.replay_receipt
            or result.scene != self.scene
            or result.physical_report != self.replay_report
        ):
            raise ValueError("limited validation differs from the exact stage lineage")
        return self


class LimitedImagePreviewManifestV1(HarnessModel):
    """One publishable normal or semantic-repair image workflow."""

    model_config = public_schema_config(LIMITED_IMAGE_PREVIEW_MANIFEST_SCHEMA_ID)

    schema_version: Literal[LIMITED_IMAGE_PREVIEW_MANIFEST_SCHEMA_ID]
    preview_id: Sha256
    project_id: str = Field(strict=True, pattern=r"^[a-z][a-z0-9-]{0,62}[a-z0-9]$")
    workflow_run_id: UUID4
    principal_id: PrincipalId
    mode: Literal["normal", "repair"]
    scope: Literal["limited_image_exact_reuse_rigid_scene"]
    requested_profile: Literal["genesis.rigid_scene@1"]
    requested_layout: PreviewLayout
    source_media: ArtifactRef
    raw_image: ArtifactRef
    input_text: ArtifactRef
    asset_query: ArtifactRef
    asset_representation: ArtifactRef
    asset_qualification: ArtifactRef
    initial_stage: LimitedImagePreviewStageV1
    revised_stage: LimitedImagePreviewStageV1 | None
    timings: tuple[LimitedImagePreviewTimingV1, ...] = Field(min_length=7, max_length=7)
    total_duration_ns: NonNegativeInt
    authority: Literal["validated_limited_preview_only"]
    robot_policy_evaluated: Literal[False]

    @model_validator(mode="after")
    def complete_release_gate(self) -> "LimitedImagePreviewManifestV1":
        _require_json_ref(
            self.source_media,
            label="source_media",
            schema_version="harness.image_source_media.v1",
        )
        _require_ref(self.raw_image, label="raw_image", media_prefix="image/")
        if self.raw_image.schema_version is not None:
            raise ValueError("raw_image cannot claim a JSON schema")
        _require_ref(
            self.input_text,
            label="input_text",
            schema_version="harness.prompt_text.v1",
            media_type="text/plain",
        )
        for ref, label, schema in (
            (self.asset_query, "asset_query", "harness.scene_interpretation_proposal.v1"),
            (
                self.asset_representation,
                "asset_representation",
                "harness.asset_representation.v1",
            ),
            (
                self.asset_qualification,
                "asset_qualification",
                "harness.asset_qualification.v1",
            ),
        ):
            _require_json_ref(ref, label=label, schema_version=schema)
        if self.initial_stage.phase != "initial":
            raise ValueError("initial_stage must have phase=initial")
        initial = self.initial_stage.validation_result
        if self.mode == "normal":
            if self.revised_stage is not None:
                raise ValueError("normal preview may contain only the initial stage")
            if initial.expected_layout is not None:
                raise ValueError("normal preview cannot invent an absolute requested layout")
            if initial.actual_layout != self.requested_layout:
                raise ValueError("normal preview must preserve its deep-verified actual layout")
            if initial.semantic_status != "pass" or initial.status != "passed":
                raise ValueError("normal preview requires physical and semantic pass")
        else:
            revised_stage = self.revised_stage
            if revised_stage is None or revised_stage.phase != "revised":
                raise ValueError("repair preview requires an exact revised stage")
            revised = revised_stage.validation_result
            if initial.expected_layout != self.requested_layout:
                raise ValueError("repair initial validation must use the requested layout")
            if initial.semantic_status != "mismatch" or initial.status != "failed":
                raise ValueError("repair initial stage must preserve semantic mismatch")
            if revised.semantic_status != "pass" or revised.status != "passed":
                raise ValueError("repair revised stage must pass physical and semantic gates")
            if revised.expected_layout != self.requested_layout:
                raise ValueError("repair must retain the same user-requested layout")
            if self.initial_stage.scene.sha256 == revised_stage.scene.sha256:
                raise ValueError("repair must produce a different scene content digest")
            if initial.actual_layout == revised.actual_layout:
                raise ValueError("repair must change actual scene position or yaw")
            if self.initial_stage.workflow_head == revised_stage.workflow_head:
                raise ValueError("repair stages must bind different workflow heads")
        segments = tuple(item.segment for item in self.timings)
        if segments != _TIMING_SEGMENTS:
            raise ValueError("timings must use the fixed seven segments in canonical order")
        if self.total_duration_ns != sum(item.duration_ns for item in self.timings):
            raise ValueError("total_duration_ns must equal the seven measured segments")
        if self.preview_id != limited_image_preview_manifest_sha256(self):
            raise ValueError("preview_id does not bind the canonical manifest")
        return self


class LimitedImagePreviewIndexEntryV1(HarnessModel):
    preview_id: Sha256
    manifest: ArtifactRef
    mode: Literal["normal", "repair"]
    final_scene_sha256: Sha256

    @model_validator(mode="after")
    def exact_manifest_ref(self) -> "LimitedImagePreviewIndexEntryV1":
        _require_json_ref(
            self.manifest,
            label="manifest",
            schema_version=LIMITED_IMAGE_PREVIEW_MANIFEST_SCHEMA_ID,
        )
        return self


class LimitedImagePreviewLibraryIndexV1(HarnessModel):
    """Canonical project-local index; the library owns its atomic replacement."""

    model_config = public_schema_config(LIMITED_IMAGE_PREVIEW_LIBRARY_INDEX_SCHEMA_ID)

    schema_version: Literal[LIMITED_IMAGE_PREVIEW_LIBRARY_INDEX_SCHEMA_ID]
    index_sha256: Sha256
    project_id: str = Field(strict=True, pattern=r"^[a-z][a-z0-9-]{0,62}[a-z0-9]$")
    revision: NonNegativeInt
    entries: tuple[LimitedImagePreviewIndexEntryV1, ...]

    @model_validator(mode="after")
    def canonical_index(self) -> "LimitedImagePreviewLibraryIndexV1":
        identities = tuple(entry.preview_id for entry in self.entries)
        if identities != tuple(sorted(set(identities))):
            raise ValueError("preview index entries must be sorted and unique")
        if self.revision != len(self.entries):
            raise ValueError("preview index revision must equal append-only entry count")
        if self.index_sha256 != limited_image_preview_library_index_sha256(self):
            raise ValueError("index_sha256 does not bind the canonical project index")
        return self


__all__ = [
    "LIMITED_IMAGE_PREVIEW_LIBRARY_INDEX_SCHEMA_ID",
    "LIMITED_IMAGE_PREVIEW_MANIFEST_SCHEMA_ID",
    "LimitedImagePreviewIndexEntryV1",
    "LimitedImagePreviewLibraryIndexV1",
    "LimitedImagePreviewManifestV1",
    "LimitedImagePreviewStageV1",
    "LimitedImagePreviewTimingV1",
    "limited_image_preview_library_index_sha256",
    "limited_image_preview_manifest_sha256",
]
