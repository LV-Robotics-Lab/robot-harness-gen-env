"""Fail-closed exact-byte staging for the S5 asset-repair seam."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .artifacts import ArtifactResolutionError, LocalArtifactStore
from .runtime_assets import (
    RuntimeAssetSnapshotError,
    loader_document_references,
    normalize_loader_reference,
    robotwin_rigid_model_sidecar_scale,
)
from .schemas.asset_repair import (
    AssetRepairPlan,
    AssetRepairPlanRequest,
    RepairDisposition,
)
from .schemas.asset_staging import (
    AssetSourceSnapshotManifest,
    AssetStageRequest,
    AssetStageResult,
    StagedAssetMember,
    canonical_sha256,
    loader_closure_sha256,
)
from .schemas.common import ArtifactRef

_READ_CHUNK_BYTES = 1024 * 1024
_Model = TypeVar("_Model", bound=BaseModel)


class AssetStageError(ValueError):
    """Stable fail-closed error raised by the public S5 stage operation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class LocalAssetSourceSnapshotBinding:
    """Deployment-only locator for one immutable, path-free source manifest."""

    manifest_ref: ArtifactRef
    root: Path


@dataclass(frozen=True, slots=True)
class _CapturedMember:
    logical_path: str
    sha256: str
    bytes: int
    temporary_path: Path


def validate_source_snapshot_bindings(
    values: tuple[LocalAssetSourceSnapshotBinding, ...],
) -> tuple[LocalAssetSourceSnapshotBinding, ...]:
    if type(values) is not tuple:
        raise TypeError("source_snapshots must be a tuple")
    for binding in values:
        if type(binding) is not LocalAssetSourceSnapshotBinding:
            raise TypeError("source snapshot bindings must use the exact binding type")
        if type(binding.manifest_ref) is not ArtifactRef:
            raise TypeError("source snapshot manifest_ref must be an exact ArtifactRef")
        if not isinstance(binding.root, Path):
            raise TypeError("source snapshot root must be a Path")
    refs = tuple(binding.manifest_ref for binding in values)
    if len(refs) != len(set(refs)):
        raise TypeError("source snapshot manifest refs must be unique")
    return values


def stage_asset_repair(
    *,
    artifact_store: LocalArtifactStore,
    source_snapshots: tuple[LocalAssetSourceSnapshotBinding, ...],
    request: AssetStageRequest,
    rebuild_plan: Callable[[AssetRepairPlanRequest], AssetRepairPlan],
) -> AssetStageResult:
    """Stage the complete selected loader closure without publishing authority."""

    binding = next(
        (
            item
            for item in source_snapshots
            if item.manifest_ref == request.source_snapshot_manifest_ref
        ),
        None,
    )
    if binding is None:
        raise AssetStageError(
            "HARN_UNTRUSTED_ASSET_SOURCE_SNAPSHOT",
            "source_snapshot_manifest_ref has no explicit local-root binding",
        )
    plan, plan_payload = _load_canonical_model(
        artifact_store,
        request.repair_plan_ref,
        AssetRepairPlan,
        label="asset repair plan",
    )
    if plan.inventory_ref != request.inventory_ref:
        raise AssetStageError(
            "HARN_REPAIR_PLAN_MISMATCH",
            "asset repair plan is bound to another inventory",
        )
    expected_plan = rebuild_plan(
        AssetRepairPlanRequest(
            schema_version="harness.asset_repair_plan_request.v1",
            inventory_ref=request.inventory_ref,
            selected_asset_ids=plan.selected_asset_ids,
        )
    )
    if plan_payload != _canonical_model_bytes(expected_plan):
        raise AssetStageError(
            "HARN_REPAIR_PLAN_MISMATCH",
            "asset repair plan does not equal the plan rebuilt from trusted inventory",
        )
    source_manifest, _ = _load_canonical_model(
        artifact_store,
        request.source_snapshot_manifest_ref,
        AssetSourceSnapshotManifest,
        label="asset source snapshot manifest",
    )
    if source_manifest.inventory_ref != request.inventory_ref:
        raise AssetStageError(
            "HARN_ASSET_SOURCE_MANIFEST_MISMATCH",
            "asset source snapshot manifest is bound to another inventory",
        )
    manifest_asset_ids = tuple(asset.asset_id for asset in source_manifest.assets)
    if manifest_asset_ids != plan.selected_asset_ids:
        raise AssetStageError(
            "HARN_ASSET_SOURCE_MANIFEST_MISMATCH",
            "source snapshot assets must equal the repair plan selection",
        )
    manifest_assets = {asset.asset_id: asset for asset in source_manifest.assets}
    for item in plan.items:
        source_asset = manifest_assets[item.asset_id]
        source_members = {member.logical_path: member for member in source_asset.members}
        selected_model_ids = tuple(sorted({value.model_id for value in item.representations}))
        sidecar_model_ids = tuple(value.model_id for value in source_asset.model_sidecars)
        if sidecar_model_ids != selected_model_ids:
            raise AssetStageError(
                "HARN_ASSET_SOURCE_MANIFEST_MISMATCH",
                f"source model sidecars must equal the plan model selection for {item.asset_id}",
            )
        for representation in item.representations:
            if representation.disposition != RepairDisposition.OBSERVED_DIGEST_MATCH:
                raise AssetStageError(
                    "HARN_ASSET_STAGE_INELIGIBLE",
                    "representation is not an exact observed identity: "
                    f"{representation.logical_path}",
                )
            source_member = source_members.get(representation.logical_path)
            if source_member is None:
                raise AssetStageError(
                    "HARN_ASSET_SOURCE_MANIFEST_MISMATCH",
                    f"source manifest omits primary loader file: {representation.logical_path}",
                )
            if (
                source_member.sha256 != representation.observed_sha256
                or source_member.bytes != representation.observed_bytes
            ):
                raise AssetStageError(
                    "HARN_ASSET_SOURCE_MANIFEST_MISMATCH",
                    f"source manifest changes primary identity: {representation.logical_path}",
                )

    with tempfile.TemporaryDirectory(prefix="harness-asset-stage-") as temporary_name:
        temporary_root = Path(temporary_name)
        captures: dict[str, _CapturedMember] = {}
        for source_asset in source_manifest.assets:
            for member in source_asset.members:
                capture = _capture_source_member(
                    root=binding.root,
                    logical_path=member.logical_path,
                    expected_sha256=member.sha256,
                    expected_bytes=member.bytes,
                    destination=temporary_root / f"member-{len(captures):08d}",
                )
                captures[member.logical_path] = capture

        sidecars: dict[tuple[str, int], tuple[str, tuple[float, float, float]]] = {}
        for source_asset in source_manifest.assets:
            for sidecar in source_asset.model_sidecars:
                capture = captures[sidecar.logical_path]
                try:
                    scale = robotwin_rigid_model_sidecar_scale(
                        sidecar.logical_path,
                        capture.temporary_path.read_bytes(),
                        expected_model_id=sidecar.model_id,
                    )
                except (OSError, RuntimeAssetSnapshotError) as error:
                    reason = (
                        error.reason
                        if isinstance(error, RuntimeAssetSnapshotError)
                        else type(error).__name__
                    )
                    raise AssetStageError(
                        "HARN_ASSET_MODEL_SIDECAR_INVALID",
                        f"invalid model sidecar for "
                        f"{source_asset.asset_id}/model{sidecar.model_id}: {reason}",
                    ) from error
                if scale != sidecar.scale:
                    raise AssetStageError(
                        "HARN_ASSET_MODEL_SIDECAR_INVALID",
                        f"model sidecar scale does not match the manifest for "
                        f"{source_asset.asset_id}/model{sidecar.model_id}",
                    )
                sidecars[(source_asset.asset_id, sidecar.model_id)] = (
                    sidecar.logical_path,
                    scale,
                )

        closure_paths: dict[tuple[str, int, str, str], tuple[str, ...]] = {}
        for item in plan.items:
            source_asset = manifest_assets[item.asset_id]
            allowed_paths = frozenset(member.logical_path for member in source_asset.members)
            used: set[str] = set()
            for representation in item.representations:
                sidecar_path, _ = sidecars[(item.asset_id, representation.model_id)]
                closure = _enumerate_glb_loader_closure(
                    root_logical_path=representation.logical_path,
                    model_sidecar_logical_path=sidecar_path,
                    asset_logical_root=source_asset.logical_root,
                    allowed_paths=allowed_paths,
                    captures=captures,
                )
                closure_paths[
                    (
                        item.asset_id,
                        representation.model_id,
                        representation.role.value,
                        representation.logical_path,
                    )
                ] = closure
                used.update(closure)
            if used != set(allowed_paths):
                raise AssetStageError(
                    "HARN_ASSET_LOADER_CLOSURE_INVALID",
                    f"source manifest contains unreferenced members for {item.asset_id}",
                )

        refs: dict[str, ArtifactRef] = {}
        for logical_path in sorted(captures):
            capture = captures[logical_path]
            try:
                ref = artifact_store.put_file(
                    capture.temporary_path,
                    name=f"asset_stage_{capture.sha256[:16]}",
                    media_type=_staged_member_media_type(logical_path),
                    schema_version=None,
                )
            except (ArtifactResolutionError, OSError) as error:
                raise AssetStageError(
                    "HARN_ASSET_STAGE_WRITE_FAILED",
                    f"failed to admit exact source bytes into CAS: {logical_path}",
                ) from error
            refs[logical_path] = ref

        assets: list[dict[str, Any]] = []
        for item in plan.items:
            source_asset = manifest_assets[item.asset_id]
            members = [
                {
                    "logical_path": member.logical_path,
                    "source_sha256": member.sha256,
                    "source_bytes": member.bytes,
                    "artifact_ref": refs[member.logical_path].model_dump(mode="json"),
                }
                for member in source_asset.members
            ]
            member_models = {
                member.logical_path: member
                for member in (StagedAssetMember.model_validate(value) for value in members)
            }
            loader_closures = []
            for representation in item.representations:
                paths = closure_paths[
                    (
                        item.asset_id,
                        representation.model_id,
                        representation.role.value,
                        representation.logical_path,
                    )
                ]
                loader_closures.append(
                    {
                        "model_id": representation.model_id,
                        "role": representation.role.value,
                        "root_logical_path": representation.logical_path,
                        "model_sidecar_logical_path": sidecars[
                            (item.asset_id, representation.model_id)
                        ][0],
                        "loader_scale": sidecars[(item.asset_id, representation.model_id)][1],
                        "member_logical_paths": paths,
                        "closure_sha256": loader_closure_sha256(
                            tuple(member_models[path] for path in paths)
                        ),
                    }
                )
            asset_payload: dict[str, Any] = {
                "asset_id": item.asset_id,
                "source_ledger_sha256": item.ledger_sha256,
                "members": members,
                "loader_closures": loader_closures,
            }
            asset_payload["asset_closure_sha256"] = canonical_sha256(asset_payload)
            assets.append(asset_payload)

    result_payload: dict[str, Any] = {
        "schema_version": "harness.asset_stage_result.v1",
        "inventory_ref": request.inventory_ref.model_dump(mode="json"),
        "repair_plan_ref": request.repair_plan_ref.model_dump(mode="json"),
        "source_snapshot_manifest_ref": request.source_snapshot_manifest_ref.model_dump(
            mode="json"
        ),
        "selected_asset_ids": plan.selected_asset_ids,
        "staged_asset_count": len(assets),
        "staged_member_count": sum(len(asset["members"]) for asset in assets),
        "staged_total_bytes": sum(
            member["source_bytes"] for asset in assets for member in asset["members"]
        ),
        "assets": assets,
        "exact_source_bytes_staged": True,
        "loader_closure_enumerated": True,
        "simulator_executed": False,
        "runtime_qualification_executed": False,
        "promotion_executed": False,
        "authoritative_ledger_writes_performed": False,
    }
    result_payload["stage_binding_sha256"] = canonical_sha256(result_payload)
    return AssetStageResult.model_validate(result_payload)


def _load_canonical_model(
    store: LocalArtifactStore,
    ref: ArtifactRef,
    model: type[_Model],
    *,
    label: str,
) -> tuple[_Model, bytes]:
    try:
        payload = store.resolve(ref).path.read_bytes()
    except (ArtifactResolutionError, OSError) as error:
        raise AssetStageError(
            "HARN_ARTIFACT_UNAVAILABLE",
            f"{label} is unavailable: {error}",
        ) from error
    try:
        parsed = model.model_validate_json(payload)
    except (ValidationError, TypeError, ValueError) as error:
        raise AssetStageError(
            "HARN_INPUT_SCHEMA_INVALID",
            f"{label} is invalid: {error}",
        ) from error
    if payload != _canonical_model_bytes(parsed):
        raise AssetStageError(
            "HARN_INPUT_SCHEMA_INVALID",
            f"{label} must use strict canonical JSON bytes",
        )
    return parsed, payload


def _canonical_model_bytes(model: BaseModel) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _capture_source_member(
    *,
    root: Path,
    logical_path: str,
    expected_sha256: str,
    expected_bytes: int,
    destination: Path,
) -> _CapturedMember:
    parts = PurePosixPath(logical_path).parts
    directory_fds: list[int] = []
    source_fd: int | None = None
    try:
        current_fd = os.open(
            root.expanduser().absolute(),
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        directory_fds.append(current_fd)
        for component in parts[:-1]:
            current_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=current_fd,
            )
            directory_fds.append(current_fd)
        source_fd = os.open(
            parts[-1],
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=current_fd,
        )
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            raise AssetStageError(
                "HARN_ASSET_SOURCE_UNSAFE",
                f"source snapshot member is not a regular file: {logical_path}",
            )
        digest = hashlib.sha256()
        size = _copy_source_fd_to_destination(
            source_fd=source_fd,
            destination=destination,
            digest=digest,
            logical_path=logical_path,
        )
        after = os.fstat(source_fd)
    except AssetStageError:
        raise
    except FileNotFoundError as error:
        raise AssetStageError(
            "HARN_ASSET_SOURCE_UNAVAILABLE",
            f"source snapshot member is unavailable: {logical_path}",
        ) from error
    except OSError as error:
        code = (
            "HARN_ASSET_SOURCE_UNSAFE"
            if error.errno in {errno.ELOOP, errno.ENOTDIR}
            else "HARN_ASSET_SOURCE_UNAVAILABLE"
        )
        raise AssetStageError(
            code,
            f"source snapshot member cannot be read: {logical_path}",
        ) from error
    finally:
        if source_fd is not None:
            os.close(source_fd)
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)
    signature_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    signature_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if signature_before != signature_after:
        raise AssetStageError(
            "HARN_DEPENDENCY_DRIFT",
            f"source snapshot member changed during capture: {logical_path}",
        )
    if size != expected_bytes:
        raise AssetStageError(
            "HARN_DEPENDENCY_DRIFT",
            f"source snapshot member byte count changed: {logical_path}",
        )
    actual_sha256 = digest.hexdigest()
    if actual_sha256 != expected_sha256:
        raise AssetStageError(
            "HARN_DEPENDENCY_DRIFT",
            f"source snapshot member digest changed: {logical_path}",
        )
    return _CapturedMember(
        logical_path=logical_path,
        sha256=actual_sha256,
        bytes=size,
        temporary_path=destination,
    )


def _copy_source_fd_to_destination(
    *,
    source_fd: int,
    destination: Path,
    digest: Any,
    logical_path: str,
) -> int:
    size = 0
    try:
        with destination.open("xb") as output:
            while True:
                try:
                    chunk = os.read(source_fd, _READ_CHUNK_BYTES)
                except OSError as error:
                    raise AssetStageError(
                        "HARN_ASSET_SOURCE_UNAVAILABLE",
                        f"source snapshot member cannot be read: {logical_path}",
                    ) from error
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
    except AssetStageError:
        raise
    except OSError as error:
        raise AssetStageError(
            "HARN_ASSET_STAGE_WRITE_FAILED",
            f"staging destination cannot store source member: {logical_path}",
        ) from error
    return size


def _staged_member_media_type(logical_path: str) -> str:
    suffix = PurePosixPath(logical_path).suffix.lower()
    if suffix == ".glb":
        return "model/gltf-binary"
    if suffix == ".json":
        return "application/json"
    return "application/octet-stream"


def _enumerate_glb_loader_closure(
    *,
    root_logical_path: str,
    model_sidecar_logical_path: str,
    asset_logical_root: str,
    allowed_paths: frozenset[str],
    captures: dict[str, _CapturedMember],
) -> tuple[str, ...]:
    if PurePosixPath(root_logical_path).suffix.lower() != ".glb":
        raise AssetStageError(
            "HARN_ASSET_LOADER_CLOSURE_INVALID",
            f"exact staging currently requires a GLB loader root: {root_logical_path}",
        )
    pending = [root_logical_path, model_sidecar_logical_path]
    visited: set[str] = set()
    while pending:
        logical_path = pending.pop(0)
        if logical_path in visited:
            continue
        visited.add(logical_path)
        try:
            references = loader_document_references(
                logical_path,
                captures[logical_path].temporary_path.read_bytes(),
            )
        except (OSError, RuntimeAssetSnapshotError) as error:
            raise AssetStageError(
                "HARN_ASSET_LOADER_CLOSURE_INVALID",
                f"loader document is invalid: {logical_path}",
            ) from error
        for reference in references:
            if reference.startswith("data:"):
                continue
            try:
                dependency = normalize_loader_reference(logical_path, reference)
            except RuntimeAssetSnapshotError as error:
                raise AssetStageError(
                    "HARN_ASSET_LOADER_CLOSURE_INVALID",
                    f"loader reference is unsafe: {logical_path}",
                ) from error
            if not dependency.startswith(asset_logical_root + "/"):
                raise AssetStageError(
                    "HARN_ASSET_LOADER_CLOSURE_INVALID",
                    f"loader reference escapes the asset root: {logical_path}",
                )
            if dependency not in allowed_paths:
                raise AssetStageError(
                    "HARN_ASSET_LOADER_CLOSURE_INVALID",
                    f"loader dependency is absent from source snapshot: {dependency}",
                )
            if dependency not in visited:
                pending.append(dependency)
        pending.sort()
    return tuple(sorted(visited))


__all__ = [
    "AssetStageError",
    "LocalAssetSourceSnapshotBinding",
    "stage_asset_repair",
    "validate_source_snapshot_bindings",
]
