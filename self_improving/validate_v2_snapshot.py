"""Recompute physical validation from a package and immutable asset snapshot.

This module is deliberately narrower than ``text2env.validate@2.0.0``.  It
proves that the deterministic validator can consume package, runtime, and
runtime-asset bytes from one local CAS after the original asset tree has gone
away.  It does not attest who produced those bytes and never makes a
publication decision.
"""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from scene_gen.builder import generated_module_source
from scene_gen.catalog import AssetCatalog
from scene_gen.schema import RelationType, ResolvedSceneSpec, SceneSpec
from scene_gen.support_geometry import footprint_2d
from scene_gen.validator import validate_resolved_scene
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.runtime_assets import (
    RUNTIME_ASSET_SNAPSHOT_SCHEMA,
    RuntimeAssetSnapshotError,
    RuntimeAssetStore,
    verify_runtime_asset_snapshot,
)
from self_improving.harness.schemas import (
    ArtifactRef,
    EnvironmentPackage,
    ValidationStatus,
)
from self_improving.harness.schemas.text2env import GATE_PROFILE

_PACKAGE_MANIFEST_SCHEMA = "robotwin.generated_scene_package.v1"
_CATALOG_SCHEMA = "robotwin.asset_catalog.v1"
_RUNTIME_EVIDENCE_SCHEMA = "robotwin.scene_runtime_evidence.v2"
_VALIDATION_REPORT_SCHEMA = "robotwin.scene_validation.v1"
_PACKAGE_MEMBERS = (
    "request.txt",
    "scene_spec.json",
    "resolved_scene.json",
    "generated_scene.py",
)
_PACKAGE_MANIFEST_FIELDS = frozenset(
    {
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
)
_PACKAGE_MEMBER_FIELDS = frozenset({"path", "sha256", "bytes"})
_VALIDATION_REPORT_FIELDS = frozenset(
    {
        "schema_version",
        "scene_id",
        "resolved_scene_sha256",
        "status",
        "fail_count",
        "not_run_count",
        "checks",
    }
)
_RUNTIME_RELATION_FIELDS = frozenset(
    {
        "relation",
        "source",
        "target",
        "pass",
        "center_delta_m",
        "center_distance_m",
        "source_half_extent_m",
        "target_half_extent_m",
    }
)
_RESERVED_REPORT_KEYS = frozenset({"decision", "publishable", "validation_decision_receipt"})
_SHA256_HEX = frozenset("0123456789abcdef")
_CHECK_NAME = re.compile(r"[a-z][a-z0-9_]*(?::[a-z][a-z0-9_]*)*")
_MAX_PACKAGE_MANIFEST_BYTES = 1024 * 1024
_MAX_PACKAGE_MEMBER_BYTES = 16 * 1024 * 1024
_MAX_CATALOG_BYTES = 16 * 1024 * 1024
_MAX_RUNTIME_EVIDENCE_BYTES = 16 * 1024 * 1024
_MAX_RUNTIME_ASSET_MANIFEST_BYTES = 32 * 1024 * 1024
_MAX_REPORT_BYTES = 16 * 1024 * 1024
_MAX_JSON_DEPTH = 64
_MAX_RELATION_MEASUREMENT_M = 1_000_000
_READ_BYTES = 1024 * 1024


class SnapshotValidationError(RuntimeError):
    """CAS bytes or their semantic bindings cannot support recomputation."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class SnapshotValidationRequest:
    """The three authorities needed for deterministic report recomputation."""

    environment_package: EnvironmentPackage
    runtime_evidence: ArtifactRef
    runtime_asset_snapshot_manifest: ArtifactRef
    gate_profile: str = GATE_PROFILE


@dataclass(frozen=True, slots=True)
class SnapshotValidationResult:
    """A report identity and derived gate status, never a promotion decision."""

    validation_report: ArtifactRef
    validation_status: ValidationStatus
    fail_count: int
    not_run_count: int


@dataclass(frozen=True, slots=True)
class _BoundPackage:
    resolved: ResolvedSceneSpec
    catalog: AssetCatalog
    manifest: dict[str, Any]


class ValidateV2SnapshotAdapter:
    """Recompute one path-free report from a fixed local CAS snapshot."""

    __slots__ = ("_artifact_store", "_scratch_parent")

    def __init__(
        self,
        *,
        artifact_store: LocalArtifactStore,
        scratch_parent: Path,
    ) -> None:
        if type(artifact_store) is not LocalArtifactStore:
            raise TypeError("artifact_store must be LocalArtifactStore")
        if not isinstance(scratch_parent, Path):
            raise TypeError("scratch_parent must be Path")
        scratch = scratch_parent.expanduser().absolute()
        try:
            metadata = scratch.lstat()
        except OSError as error:
            raise SnapshotValidationError(
                "scratch_unavailable",
                "snapshot validation scratch parent is unavailable",
            ) from error
        try:
            resolved_scratch = scratch.resolve(strict=True)
        except RuntimeError as error:
            raise SnapshotValidationError(
                "scratch_unsafe",
                "snapshot validation scratch parent must not traverse symlinks",
            ) from error
        except OSError as error:
            raise SnapshotValidationError(
                "scratch_unavailable",
                "snapshot validation scratch parent could not be resolved",
            ) from error
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or resolved_scratch != scratch
        ):
            raise SnapshotValidationError(
                "scratch_unsafe",
                "snapshot validation scratch parent must be a direct real directory",
            )
        self._artifact_store = artifact_store
        self._scratch_parent = scratch

    def recompute(self, request: SnapshotValidationRequest) -> SnapshotValidationResult:
        """Recompute gates without requiring the package's original asset contents."""

        strict_request = _validate_request(request)
        try:
            attempt_root = Path(
                tempfile.mkdtemp(prefix=".validate-v2-snapshot.", dir=self._scratch_parent)
            )
        except OSError as error:
            raise SnapshotValidationError(
                "scratch_unavailable",
                "snapshot validation scratch directory could not be allocated",
            ) from error
        try:
            bound = self._load_bound_package(strict_request)
            runtime = _json_object(
                _read_cas_ref(
                    self._artifact_store,
                    strict_request.runtime_evidence,
                    limit=_MAX_RUNTIME_EVIDENCE_BYTES,
                ),
                label="runtime evidence",
            )
            _bind_runtime_evidence(strict_request, bound.resolved, runtime)
            snapshot_document = _json_object(
                _read_cas_ref(
                    self._artifact_store,
                    strict_request.runtime_asset_snapshot_manifest,
                    limit=_MAX_RUNTIME_ASSET_MANIFEST_BYTES,
                ),
                label="runtime asset snapshot manifest",
            )
            materialized = RuntimeAssetStore(self._artifact_store).materialize(
                strict_request.runtime_asset_snapshot_manifest,
                attempt_root / "runtime-assets",
            )
            verified = verify_runtime_asset_snapshot(
                root=materialized.root,
                manifest_path=materialized.manifest_path,
                expected_sha256=strict_request.runtime_asset_snapshot_manifest.sha256,
                resolved=bound.resolved,
                catalog=bound.catalog,
            )
            if verified.manifest_sha256 != strict_request.runtime_asset_snapshot_manifest.sha256:
                raise SnapshotValidationError(
                    "snapshot_binding",
                    "runtime asset verification returned a different manifest identity",
                )
            report = validate_resolved_scene(
                bound.resolved,
                catalog=None,
                package_root=None,
                runtime_evidence=runtime,
                require_runtime=True,
            )
            _verify_report(report, bound.resolved)
            _bind_package_check(
                report,
                manifest=bound.manifest,
                resolved=bound.resolved,
            )
            _bind_snapshot_source_checks(
                report,
                resolved=bound.resolved,
                snapshot=snapshot_document,
                snapshot_sha256=strict_request.runtime_asset_snapshot_manifest.sha256,
            )
            _project_runtime_object_checks(
                report,
                resolved=bound.resolved,
                runtime=runtime,
            )
            _bind_report(
                report,
                request=strict_request,
                resolved=bound.resolved,
            )
            payload = _canonical_json_bytes(report)
            if len(payload) > _MAX_REPORT_BYTES:
                raise SnapshotValidationError(
                    "report_too_large",
                    "snapshot validation report exceeds its byte limit",
                )
            _reject_reserved_report_keys(report)
            _reject_absolute_locators(report)
            report_path = attempt_root / "snapshot_validation_report.json"
            report_path.write_bytes(payload)
            report_ref = self._artifact_store.put_file(
                report_path,
                name="snapshot_validation_report",
                media_type="application/json",
                schema_version=_VALIDATION_REPORT_SCHEMA,
            )
            if (
                _read_cas_ref(
                    self._artifact_store,
                    report_ref,
                    limit=_MAX_REPORT_BYTES,
                )
                != payload
            ):
                raise SnapshotValidationError(
                    "report_mismatch",
                    "published snapshot validation report could not be reread exactly",
                )
            return SnapshotValidationResult(
                validation_report=report_ref,
                validation_status=ValidationStatus(report["status"]),
                fail_count=report["fail_count"],
                not_run_count=report["not_run_count"],
            )
        except SnapshotValidationError:
            raise
        except RuntimeAssetSnapshotError as error:
            raise SnapshotValidationError(
                "runtime_asset_snapshot_invalid",
                "runtime asset snapshot could not be verified",
            ) from error
        except (AttributeError, OSError, TypeError, ValueError, KeyError) as error:
            raise SnapshotValidationError(
                "recompute_failed",
                "snapshot validation inputs could not be reconstructed",
            ) from error
        finally:
            _remove_attempt(attempt_root)

    def _load_bound_package(
        self,
        request: SnapshotValidationRequest,
    ) -> _BoundPackage:
        package = request.environment_package
        manifest_payload = _read_cas_ref(
            self._artifact_store,
            package.package_manifest,
            limit=_MAX_PACKAGE_MANIFEST_BYTES,
        )
        manifest = _package_manifest(manifest_payload)
        member_payloads: dict[str, bytes] = {}
        for record in manifest["files"]:
            member_ref = ArtifactRef(
                name=PurePosixPath(record["path"]).stem,
                uri=f"artifact://sha256/{record['sha256']}",
                media_type=_package_member_type(record["path"])[0],
                sha256=record["sha256"],
                bytes=record["bytes"],
                schema_version=_package_member_type(record["path"])[1],
            )
            member_payloads[record["path"]] = _read_cas_ref(
                self._artifact_store,
                member_ref,
                limit=_MAX_PACKAGE_MEMBER_BYTES,
            )
        catalog_payload = _read_cas_ref(
            self._artifact_store,
            package.asset_catalog,
            limit=_MAX_CATALOG_BYTES,
        )
        scene = SceneSpec.model_validate(
            _json_object(member_payloads["scene_spec.json"], label="SceneSpec")
        )
        resolved = ResolvedSceneSpec.model_validate(
            _json_object(member_payloads["resolved_scene.json"], label="ResolvedSceneSpec")
        )
        catalog = AssetCatalog.model_validate(_json_object(catalog_payload, label="asset catalog"))
        _verify_package_bindings(
            package,
            manifest=manifest,
            scene=scene,
            resolved=resolved,
            catalog=catalog,
            members=member_payloads,
        )
        return _BoundPackage(
            resolved=resolved,
            catalog=catalog,
            manifest=manifest,
        )


def _validate_request(value: SnapshotValidationRequest) -> SnapshotValidationRequest:
    if type(value) is not SnapshotValidationRequest:
        raise TypeError("request must be SnapshotValidationRequest")
    try:
        package = EnvironmentPackage.model_validate(
            value.environment_package.model_dump(mode="python")
        )
        runtime = ArtifactRef.model_validate(value.runtime_evidence.model_dump(mode="python"))
        snapshot = ArtifactRef.model_validate(
            value.runtime_asset_snapshot_manifest.model_dump(mode="python")
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise SnapshotValidationError(
            "invalid_request",
            "snapshot validation request does not satisfy its typed contract",
        ) from error
    if value.gate_profile != GATE_PROFILE:
        raise SnapshotValidationError(
            "gate_profile_unsupported",
            "snapshot validation gate profile is unsupported",
        )
    _require_cas_ref(
        package.package_manifest,
        media_type="application/json",
        schema_version=_PACKAGE_MANIFEST_SCHEMA,
        label="package manifest",
    )
    _require_cas_ref(
        package.asset_catalog,
        media_type="application/json",
        schema_version=_CATALOG_SCHEMA,
        label="asset catalog",
    )
    _require_cas_ref(
        runtime,
        media_type="application/json",
        schema_version=_RUNTIME_EVIDENCE_SCHEMA,
        label="runtime evidence",
    )
    _require_cas_ref(
        snapshot,
        media_type="application/json",
        schema_version=RUNTIME_ASSET_SNAPSHOT_SCHEMA,
        label="runtime asset snapshot",
    )
    return SnapshotValidationRequest(
        environment_package=package,
        runtime_evidence=runtime,
        runtime_asset_snapshot_manifest=snapshot,
        gate_profile=value.gate_profile,
    )


def _require_cas_ref(
    ref: ArtifactRef,
    *,
    media_type: str,
    schema_version: str,
    label: str,
) -> None:
    if (
        ref.uri != f"artifact://sha256/{ref.sha256}"
        or ref.media_type != media_type
        or ref.schema_version != schema_version
    ):
        raise SnapshotValidationError(
            "invalid_reference",
            f"{label} must be an exactly typed canonical CAS reference",
        )


def _package_manifest(payload: bytes) -> dict[str, Any]:
    value = _json_object(payload, label="package manifest")
    if set(value) != _PACKAGE_MANIFEST_FIELDS:
        raise SnapshotValidationError(
            "package_manifest_invalid", "package manifest shape is invalid"
        )
    if (
        value["schema_version"] != _PACKAGE_MANIFEST_SCHEMA
        or value["entrypoint"] != "generated_scene.py:load_scene"
        or value["resolved_only_entrypoint"] != "scene_gen.envs.generated_scene:load_resolved_scene"
        or not isinstance(value["scene_id"], str)
        or not value["scene_id"]
        or type(value["seed"]) is not int
        or not 0 <= value["seed"] <= 2_147_483_647
        or not _is_sha256(value["source_scene_spec_sha256"])
        or not _is_sha256(value["resolved_scene_sha256"])
        or not _is_sha256(value["asset_catalog_sha256"])
        or not isinstance(value["compiler_version"], str)
        or not value["compiler_version"]
    ):
        raise SnapshotValidationError(
            "package_manifest_invalid",
            "package manifest contract is unsupported",
        )
    files = value["files"]
    if not isinstance(files, list) or [
        item.get("path") for item in files if isinstance(item, dict)
    ] != list(_PACKAGE_MEMBERS):
        raise SnapshotValidationError(
            "package_manifest_invalid",
            "package manifest must contain the exact ordered package members",
        )
    for record in files:
        if (
            not isinstance(record, dict)
            or set(record) != _PACKAGE_MEMBER_FIELDS
            or not _is_sha256(record["sha256"])
            or type(record["bytes"]) is not int
            or record["bytes"] < 0
            or record["bytes"] > _MAX_PACKAGE_MEMBER_BYTES
        ):
            raise SnapshotValidationError(
                "package_manifest_invalid",
                "package manifest contains an invalid member identity",
            )
    return value


def _verify_package_bindings(
    package: EnvironmentPackage,
    *,
    manifest: dict[str, Any],
    scene: SceneSpec,
    resolved: ResolvedSceneSpec,
    catalog: AssetCatalog,
    members: dict[str, bytes],
) -> None:
    expected = {
        "package id": (package.package_id, resolved.digest()),
        "package scene digest": (package.scene_spec_sha256, scene.digest()),
        "package resolved digest": (package.resolved_scene_sha256, resolved.digest()),
        "package catalog digest": (package.asset_catalog.sha256, catalog.digest()),
        "resolved source scene": (resolved.source_scene_spec_sha256, scene.digest()),
        "resolved catalog": (resolved.asset_catalog_sha256, catalog.digest()),
        "manifest scene": (manifest["source_scene_spec_sha256"], scene.digest()),
        "manifest resolved": (manifest["resolved_scene_sha256"], resolved.digest()),
        "manifest catalog": (manifest["asset_catalog_sha256"], catalog.digest()),
        "manifest scene id": (manifest["scene_id"], scene.scene_id),
        "resolved scene id": (resolved.scene_id, scene.scene_id),
        "manifest seed": (manifest["seed"], scene.seed),
        "resolved seed": (resolved.seed, scene.seed),
        "package seed": (package.seed, scene.seed),
        "manifest compiler": (manifest["compiler_version"], resolved.compiler_version),
    }
    for label, (actual, wanted) in expected.items():
        if actual != wanted:
            raise SnapshotValidationError(
                "package_binding",
                f"{label} does not match the reconstructed package",
            )
    scene_projection = (
        scene.request,
        scene.frame,
        scene.unit,
        scene.workspace,
        scene.relations,
    )
    resolved_projection = (
        resolved.request,
        resolved.frame,
        resolved.unit,
        resolved.workspace,
        resolved.relations,
    )
    if resolved_projection != scene_projection:
        raise SnapshotValidationError(
            "package_binding",
            "ResolvedSceneSpec differs from the source SceneSpec",
        )
    supports = {
        relation.source: (relation.relation, relation.target)
        for relation in scene.relations
        if relation.relation in {RelationType.ON_TABLE, RelationType.ON_TOP_OF, RelationType.INSIDE}
    }
    scene_objects = {
        item.object_id: (
            item.category,
            item.color,
            item.material,
            item.articulation,
            *supports[item.object_id],
        )
        for item in scene.objects
    }
    resolved_objects = {
        item.object_id: (
            item.category,
            item.color,
            item.material,
            item.articulation_state,
            item.support_relation,
            item.support_target,
        )
        for item in resolved.objects
    }
    if (len(resolved.objects), resolved_objects) != (len(scene.objects), scene_objects):
        raise SnapshotValidationError(
            "package_binding",
            "ResolvedSceneSpec objects differ from the source SceneSpec",
        )
    if members["request.txt"] != (scene.request + "\n").encode("utf-8"):
        raise SnapshotValidationError("package_binding", "request member does not match SceneSpec")
    if members["generated_scene.py"] != generated_module_source(resolved).encode("utf-8"):
        raise SnapshotValidationError(
            "package_binding",
            "generated module does not match ResolvedSceneSpec",
        )


def _bind_runtime_evidence(
    request: SnapshotValidationRequest,
    resolved: ResolvedSceneSpec,
    runtime: dict[str, Any],
) -> None:
    if (
        runtime.get("schema_version") != _RUNTIME_EVIDENCE_SCHEMA
        or runtime.get("scene_id") != resolved.scene_id
        or type(runtime.get("seed")) is not int
        or runtime.get("seed") != resolved.seed
        or runtime.get("resolved_scene_sha256") != resolved.digest()
        or runtime.get("runtime_asset_snapshot_sha256")
        != request.runtime_asset_snapshot_manifest.sha256
    ):
        raise SnapshotValidationError(
            "runtime_binding",
            "runtime evidence does not match the package and asset snapshot",
        )
    objects = runtime.get("objects")
    relations = runtime.get("relations")
    if (
        not isinstance(objects, dict)
        or set(objects) != {item.object_id for item in resolved.objects}
        or any(not isinstance(value, dict) for value in objects.values())
        or not isinstance(relations, dict)
        or any(
            not isinstance(key, str) or not isinstance(value, dict)
            for key, value in relations.items()
        )
    ):
        raise SnapshotValidationError(
            "runtime_evidence_invalid",
            "runtime evidence containers do not match the resolved scene",
        )
    if not _runtime_scalars_are_safe(runtime):
        raise SnapshotValidationError(
            "runtime_evidence_invalid",
            "runtime evidence timeline scalars have invalid producer types",
        )
    for item in resolved.objects:
        if not _runtime_object_is_safe(item, objects[item.object_id]):
            raise SnapshotValidationError(
                "runtime_evidence_invalid",
                "runtime object evidence has invalid producer types",
            )
    expected_relations = {
        f"{relation.relation.value}:{relation.source}:{relation.target}": relation
        for relation in resolved.relations
        if relation.target != "table" and relation.relation.value not in {"on_top_of", "inside"}
    }
    if set(relations) != set(expected_relations):
        raise SnapshotValidationError(
            "runtime_evidence_invalid",
            "runtime relation evidence does not match the resolved scene",
        )
    for key, relation in expected_relations.items():
        evidence = relations[key]
        if (
            set(evidence) != _RUNTIME_RELATION_FIELDS
            or evidence["relation"] != relation.relation.value
            or evidence["source"] != relation.source
            or evidence["target"] != relation.target
            or type(evidence["pass"]) is not bool
            or not _finite_vector(evidence["center_delta_m"], length=2)
            or not _finite_number(evidence["center_distance_m"], nonnegative=True)
            or not _finite_vector(
                evidence["source_half_extent_m"],
                length=2,
                nonnegative=True,
            )
            or not _finite_vector(
                evidence["target_half_extent_m"],
                length=2,
                nonnegative=True,
            )
            or not _runtime_relation_matches(
                relation,
                evidence,
                resolved=resolved,
            )
        ):
            raise SnapshotValidationError(
                "runtime_evidence_invalid",
                "runtime relation evidence has an invalid producer shape",
            )


def _runtime_relation_matches(
    relation: Any,
    evidence: dict[str, Any],
    *,
    resolved: ResolvedSceneSpec,
) -> bool:
    by_id = {item.object_id: item for item in resolved.objects}
    source = by_id[relation.source]
    target = by_id[relation.target]
    source_footprint = footprint_2d(
        source.dimensions_m,
        source.pose.yaw_rad,
        source.footprint_shape,
    )
    target_footprint = footprint_2d(
        target.dimensions_m,
        target.pose.yaw_rad,
        target.footprint_shape,
    )
    dx, dy = evidence["center_delta_m"]
    distance = math.hypot(dx, dy)
    source_half = (source_footprint.half_x, source_footprint.half_y)
    target_half = (target_footprint.half_x, target_footprint.half_y)
    if not (
        _measurements_match(evidence["center_distance_m"], distance)
        and _vectors_match(evidence["source_half_extent_m"], source_half)
        and _vectors_match(evidence["target_half_extent_m"], target_half)
    ):
        return False
    gap = 0.015
    if relation.relation == RelationType.LEFT_OF:
        passed = dx + source_half[0] + gap <= -target_half[0]
    elif relation.relation == RelationType.RIGHT_OF:
        passed = dx - source_half[0] - gap >= target_half[0]
    elif relation.relation == RelationType.FRONT_OF:
        passed = dy - source_half[1] - gap >= target_half[1]
    elif relation.relation == RelationType.BEHIND:
        passed = dy + source_half[1] + gap <= -target_half[1]
    elif relation.relation == RelationType.NEAR:
        passed = distance <= (relation.max_distance_m or 0.25)
    else:
        passed = distance >= (relation.min_distance_m or 0.0)
    return evidence["pass"] is bool(passed)


def _measurements_match(actual: float, expected: float) -> bool:
    return math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12)


def _vectors_match(actual: list[float], expected: tuple[float, float]) -> bool:
    return all(
        _measurements_match(actual_value, expected_value)
        for actual_value, expected_value in zip(actual, expected, strict=True)
    )


def _runtime_scalars_are_safe(runtime: dict[str, Any]) -> bool:
    indices = runtime.get("video_sample_step_indices")
    video_frames = runtime.get("video_frame_count")
    unique_video_frames = runtime.get("unique_video_frame_count")
    return (
        runtime.get("status") in {"pass", "fail"}
        and _nonnegative_int(runtime.get("robot_initial_collision_count"))
        and _nonnegative_int(video_frames)
        and _nonnegative_int(unique_video_frames)
        and unique_video_frames <= video_frames
        and _positive_int(runtime.get("base_simulation_step_count"))
        and _positive_int(runtime.get("simulation_step_count"))
        and _nonnegative_int(runtime.get("settle_extra_steps"))
        and isinstance(indices, list)
        and all(_nonnegative_int(index) for index in indices)
    )


def _runtime_object_is_safe(item: Any, evidence: dict[str, Any]) -> bool:
    measured_fields = (
        "translation_drift_m",
        "rotation_drift_deg",
        "resolved_translation_error_m",
        "resolved_rotation_error_deg",
    )
    targets = evidence.get("unexpected_contact_targets")
    expected_mode = f"{item.support_relation.value}_contact"
    allowed_modes = (
        {"fixed_static_pose"}
        if item.is_static and item.support_relation.value == "on_table"
        else {"none", expected_mode}
    )
    margin = evidence.get("support_footprint_margin_m")
    inside = evidence.get("inside_contained")
    articulation_error = evidence.get("articulation_max_abs_error")
    return (
        all(_finite_number(evidence.get(field), nonnegative=True) for field in measured_fields)
        and _nonnegative_int(evidence.get("penetration_count"))
        and type(evidence.get("still_moving")) is bool
        and type(evidence.get("support_contact")) is bool
        and _unit_interval(evidence.get("support_contact_fraction"))
        and _unit_interval(evidence.get("unexpected_contact_fraction"))
        and isinstance(targets, list)
        and len(targets) <= 256
        and all(isinstance(target, str) and 0 < len(target) <= 255 for target in targets)
        and targets == sorted(set(targets))
        and evidence.get("support_mode") in allowed_modes
        and evidence.get("support_target") in {None, item.support_target}
        and type(evidence.get("dropped")) is bool
        and _nonnegative_int(evidence.get("visible_pixels"))
        and (
            _finite_number(margin) if item.support_relation.value == "on_top_of" else margin is None
        )
        and (type(inside) is bool if item.support_relation.value == "inside" else inside is None)
        and (articulation_error is None or _finite_number(articulation_error, nonnegative=True))
    )


def _project_runtime_object_checks(
    report: dict[str, Any],
    *,
    resolved: ResolvedSceneSpec,
    runtime: dict[str, Any],
) -> None:
    by_name = {
        check.get("name"): check for check in report.get("checks", []) if isinstance(check, dict)
    }
    objects = runtime["objects"]
    for item in resolved.objects:
        evidence = objects[item.object_id]
        support = by_name.get(f"support_contact:{item.object_id}")
        if not isinstance(support, dict) or not isinstance(support.get("evidence"), dict):
            raise SnapshotValidationError(
                "report_invalid",
                "validator support-contact check is invalid",
            )
        support["evidence"] = {
            "raw_contact": evidence["support_contact"],
            "mode": evidence["support_mode"],
            "is_static": item.is_static,
            "expected_target": item.support_target,
            "observed_target": evidence.get("support_target"),
            "contact_fraction": evidence["support_contact_fraction"],
            "minimum_contact_fraction": support["evidence"]["minimum_contact_fraction"],
        }
        if item.support_relation.value == "on_table":
            continue
        unexpected = by_name.get(f"no_unexpected_support_contact:{item.object_id}")
        if not isinstance(unexpected, dict):
            raise SnapshotValidationError(
                "report_invalid",
                "validator unexpected-contact check is invalid",
            )
        targets = evidence["unexpected_contact_targets"]
        unexpected["evidence"] = {
            "contact_fraction": evidence["unexpected_contact_fraction"],
            "target_count": len(targets),
            "has_unexpected": bool(targets),
        }


def _nonnegative_int(value: Any) -> bool:
    return type(value) is int and 0 <= value <= 2_147_483_647


def _positive_int(value: Any) -> bool:
    return _nonnegative_int(value) and value > 0


def _unit_interval(value: Any) -> bool:
    return _finite_number(value, nonnegative=True) and value <= 1


def _bind_package_check(
    report: dict[str, Any],
    *,
    manifest: dict[str, Any],
    resolved: ResolvedSceneSpec,
) -> None:
    checks = report.get("checks")
    if not isinstance(checks, list) or any(
        isinstance(check, dict) and check.get("name") == "package_manifest" for check in checks
    ):
        raise SnapshotValidationError(
            "report_invalid",
            "validator package-check boundary is invalid",
        )
    member_checks = [
        {
            "path": record["path"],
            "exists": True,
            "expected_sha256": record["sha256"],
            "actual_sha256": record["sha256"],
            "pass": True,
        }
        for record in manifest["files"]
    ]
    member_checks.append(
        {
            "path": "resolved_scene.json#canonical_digest",
            "expected_sha256": manifest["resolved_scene_sha256"],
            "actual_sha256": resolved.digest(),
            "pass": True,
        }
    )
    package_check = {
        "name": "package_manifest",
        "status": "pass",
        "evidence": {
            "schema_version": "robotwin.generated_scene_package_verification.v1",
            "status": "pass",
            "checks": member_checks,
        },
    }
    roundtrip_index = next(
        (
            index
            for index, check in enumerate(checks)
            if isinstance(check, dict) and check.get("name") == "resolved_only_roundtrip"
        ),
        None,
    )
    if roundtrip_index is None:
        raise SnapshotValidationError(
            "report_invalid",
            "validator omitted its resolved-scene roundtrip check",
        )
    checks.insert(roundtrip_index + 1, package_check)


def _bind_snapshot_source_checks(
    report: dict[str, Any],
    *,
    resolved: ResolvedSceneSpec,
    snapshot: dict[str, Any],
    snapshot_sha256: str,
) -> None:
    assets = {
        value.get("asset_id"): value
        for value in snapshot.get("assets", [])
        if isinstance(value, dict)
    }
    by_name = {
        check.get("name"): check for check in report.get("checks", []) if isinstance(check, dict)
    }
    for item in resolved.objects:
        check = by_name.get(f"real_asset_files:{item.object_id}")
        asset = assets.get(item.asset_id)
        if (
            not isinstance(check, dict)
            or check.get("status") != "not_applicable"
            or not isinstance(asset, dict)
            or item.model_id not in asset.get("selected_model_ids", [])
        ):
            raise SnapshotValidationError(
                "snapshot_binding",
                "validator source checks do not match the verified runtime asset snapshot",
            )
        check["status"] = "pass"
        check["evidence"] = {
            "asset_id": item.asset_id,
            "model_id": item.model_id,
            "required_files": list(asset["required_files"]),
            "runtime_asset_snapshot_sha256": snapshot_sha256,
            "tree_sha256": asset["tree_sha256"],
        }
    _recount_report(report)


def _bind_report(
    report: dict[str, Any],
    *,
    request: SnapshotValidationRequest,
    resolved: ResolvedSceneSpec,
) -> None:
    _verify_report(report, resolved)
    for check in report["checks"]:
        if check["name"] == "runtime_status":
            check["evidence"] = {"reported_status": check["status"]}
    if any(check["name"] == "snapshot_validation_binding" for check in report["checks"]):
        raise SnapshotValidationError(
            "report_invalid",
            "validator supplied the adapter-owned snapshot binding check",
        )
    report["checks"].append(
        {
            "name": "snapshot_validation_binding",
            "status": "pass",
            "evidence": {
                "asset_catalog_sha256": request.environment_package.asset_catalog.sha256,
                "environment_package_id": request.environment_package.package_id,
                "gate_profile": request.gate_profile,
                "package_manifest_sha256": request.environment_package.package_manifest.sha256,
                "runtime_asset_snapshot_sha256": request.runtime_asset_snapshot_manifest.sha256,
                "runtime_evidence_sha256": request.runtime_evidence.sha256,
            },
        }
    )
    _verify_report(report, resolved)


def _verify_report(report: dict[str, Any], resolved: ResolvedSceneSpec) -> None:
    if (
        not isinstance(report, dict)
        or set(report) != _VALIDATION_REPORT_FIELDS
        or report.get("schema_version") != _VALIDATION_REPORT_SCHEMA
        or report.get("scene_id") != resolved.scene_id
        or report.get("resolved_scene_sha256") != resolved.digest()
    ):
        raise SnapshotValidationError("report_invalid", "validator report identity is invalid")
    checks = report.get("checks")
    if not isinstance(checks, list) or not checks:
        raise SnapshotValidationError("report_invalid", "validator report checks are invalid")
    names: set[str] = set()
    statuses: list[str] = []
    for check in checks:
        if (
            not isinstance(check, dict)
            or set(check) != {"name", "status", "evidence"}
            or not isinstance(check["name"], str)
            or not check["name"]
            or len(check["name"]) > 255
            or _CHECK_NAME.fullmatch(check["name"]) is None
            or check["name"] in names
            or check["status"] not in {"pass", "fail", "not_run", "not_applicable"}
        ):
            raise SnapshotValidationError("report_invalid", "validator report check is invalid")
        names.add(check["name"])
        statuses.append(check["status"])
    if (
        type(report.get("fail_count")) is not int
        or type(report.get("not_run_count")) is not int
        or report["fail_count"] != statuses.count("fail")
        or report["not_run_count"] != statuses.count("not_run")
    ):
        raise SnapshotValidationError("report_invalid", "validator report counts are invalid")
    expected_status = (
        "fail" if report["fail_count"] else "incomplete" if report["not_run_count"] else "pass"
    )
    if report.get("status") != expected_status:
        raise SnapshotValidationError("report_invalid", "validator report status is invalid")


def _recount_report(report: dict[str, Any]) -> None:
    statuses = [
        check.get("status") for check in report.get("checks", []) if isinstance(check, dict)
    ]
    report["fail_count"] = statuses.count("fail")
    report["not_run_count"] = statuses.count("not_run")
    report["status"] = (
        "fail" if report["fail_count"] else "incomplete" if report["not_run_count"] else "pass"
    )


def _package_member_type(path: str) -> tuple[str, str | None]:
    return {
        "request.txt": ("text/plain", None),
        "scene_spec.json": ("application/json", "robotwin.scene_spec.v1"),
        "resolved_scene.json": ("application/json", "robotwin.resolved_scene.v1"),
        "generated_scene.py": ("text/x-python", None),
    }[path]


def _read_cas_ref(store: LocalArtifactStore, ref: ArtifactRef, *, limit: int) -> bytes:
    if ref.bytes > limit:
        raise SnapshotValidationError("artifact_too_large", "CAS artifact exceeds its byte limit")
    directory_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptors: list[int] = []
    try:
        descriptors.append(os.open(store.root, directory_flags))
        descriptors.append(os.open("sha256", directory_flags, dir_fd=descriptors[-1]))
        descriptors.append(os.open(ref.sha256[:2], directory_flags, dir_fd=descriptors[-1]))
        descriptors.append(os.open(ref.sha256, file_flags, dir_fd=descriptors[-1]))
        metadata = os.fstat(descriptors[-1])
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != ref.bytes:
            raise SnapshotValidationError(
                "artifact_mismatch",
                "CAS artifact does not match its declared byte identity",
            )
        payload = bytearray()
        while len(payload) < ref.bytes:
            chunk = os.read(descriptors[-1], min(_READ_BYTES, ref.bytes - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptors[-1])
    except SnapshotValidationError:
        raise
    except OSError as error:
        reason = (
            "artifact_unsafe"
            if error.errno in {errno.EISDIR, errno.ELOOP, errno.ENOTDIR}
            else "artifact_unavailable"
        )
        raise SnapshotValidationError(
            reason, "CAS artifact is unavailable in the local CAS"
        ) from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    if (
        _file_identity(metadata) != _file_identity(after)
        or len(payload) != ref.bytes
        or hashlib.sha256(payload).hexdigest() != ref.sha256
    ):
        raise SnapshotValidationError(
            "artifact_mismatch",
            "CAS artifact does not match its declared content identity",
        )
    return bytes(payload)


def _json_object(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise SnapshotValidationError("json_invalid", f"{label} is not strict JSON") from error
    if not isinstance(value, dict):
        raise SnapshotValidationError("json_invalid", f"{label} must be a JSON object")
    _validate_json_depth(value)
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON member")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"unsupported JSON constant: {value}")


def _validate_json_depth(value: Any, depth: int = 1) -> None:
    if depth > _MAX_JSON_DEPTH:
        raise SnapshotValidationError(
            "json_invalid",
            "JSON nesting exceeds the fixed depth limit",
        )
    if isinstance(value, dict):
        for item in value.values():
            _validate_json_depth(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _validate_json_depth(item, depth + 1)
    elif isinstance(value, float) and not math.isfinite(value):
        raise SnapshotValidationError(
            "json_invalid",
            "JSON numbers must be finite",
        )


def _finite_number(value: Any, *, nonnegative: bool = False) -> bool:
    if type(value) not in {int, float}:
        return False
    lower = 0 if nonnegative else -_MAX_RELATION_MEASUREMENT_M
    return lower <= value <= _MAX_RELATION_MEASUREMENT_M


def _finite_vector(
    value: Any,
    *,
    length: int,
    nonnegative: bool = False,
) -> bool:
    return (
        isinstance(value, list)
        and len(value) == length
        and all(_finite_number(item, nonnegative=nonnegative) for item in value)
    )


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _SHA256_HEX


def _file_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _reject_absolute_locators(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_absolute_locators(key)
            _reject_absolute_locators(item)
        return
    if isinstance(value, list):
        for item in value:
            _reject_absolute_locators(item)
        return
    if not isinstance(value, str):
        return
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or value.casefold().startswith("file:")
    ):
        raise SnapshotValidationError(
            "report_locator",
            "snapshot validation report contains an absolute locator",
        )


def _reject_reserved_report_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.casefold() in _RESERVED_REPORT_KEYS:
                raise SnapshotValidationError(
                    "report_invalid",
                    "snapshot validation report contains a reserved authority claim",
                )
            _reject_reserved_report_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_reserved_report_keys(item)


def _remove_attempt(root: Path) -> None:
    for directory, directories, _ in os.walk(root, topdown=True, followlinks=False):
        os.chmod(directory, 0o700)
        for name in directories:
            os.chmod(Path(directory) / name, 0o700, follow_symlinks=False)
    shutil.rmtree(root)


__all__ = [
    "SnapshotValidationError",
    "SnapshotValidationRequest",
    "SnapshotValidationResult",
    "ValidateV2SnapshotAdapter",
]
