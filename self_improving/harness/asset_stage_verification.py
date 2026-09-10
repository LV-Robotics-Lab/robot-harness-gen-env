"""Deep authority verification and contained materialization for staged assets."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .artifacts import LocalArtifactStore
from .asset_repair import AssetRepairApplication, AssetRepairError
from .runtime_assets import (
    RuntimeAssetSnapshotError,
    loader_document_references,
    normalize_loader_reference,
    robotwin_rigid_model_sidecar_scale,
)
from .schemas.asset_repair import AssetDebtInventory, AssetRepairPlan, AssetRepairPlanRequest
from .schemas.asset_staging import (
    AssetLoaderClosure,
    AssetSourceSnapshotManifest,
    AssetStageResult,
    StagedAssetMember,
)
from .schemas.common import ArtifactRef

_READ_CHUNK_BYTES = 1024 * 1024
_Model = TypeVar("_Model", bound=BaseModel)


class AssetStageVerificationError(ValueError):
    """Stable fail-closed error for verification or contained materialization."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class VerifiedAssetStageAuthority:
    """Typed result and all canonical CAS authorities it was checked against."""

    result: AssetStageResult
    inventory: AssetDebtInventory
    repair_plan: AssetRepairPlan
    source_manifest: AssetSourceSnapshotManifest


@dataclass(frozen=True, slots=True)
class RootedFileAttestation:
    """One-fd streamed identity and its before/after stable file signature."""

    logical_path: str
    sha256: str
    bytes: int
    signature: tuple[int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class MaterializedAssetMember:
    """Exact member successfully copied beneath a newly created trusted root."""

    logical_path: str
    sha256: str
    bytes: int


def verify_asset_stage_result_authority(
    *,
    artifact_store: LocalArtifactStore,
    result_payload: bytes,
    expected_result_sha256: str,
    expected_stage_binding_sha256: str,
    expected_inventory_sha256: str,
    expected_repair_plan_sha256: str,
    expected_source_snapshot_manifest_sha256: str,
) -> VerifiedAssetStageAuthority:
    """Verify one result against explicit trust inputs and all canonical CAS records."""

    if type(artifact_store) is not LocalArtifactStore:
        raise TypeError("artifact_store must be the exact LocalArtifactStore")
    if type(result_payload) is not bytes:
        raise TypeError("result_payload must be exact bytes")
    expected_digests = (
        expected_result_sha256,
        expected_stage_binding_sha256,
        expected_inventory_sha256,
        expected_repair_plan_sha256,
        expected_source_snapshot_manifest_sha256,
    )
    if any(not _is_sha256(value) for value in expected_digests):
        raise TypeError("expected authority identities must be lowercase SHA-256 values")
    if hashlib.sha256(result_payload).hexdigest() != expected_result_sha256:
        raise AssetStageVerificationError(
            "HARN_ASSET_STAGE_TRUST_MISMATCH",
            "asset stage authority result bytes do not match the expected identity",
        )
    result = _parse_canonical_bytes(
        result_payload,
        AssetStageResult,
        label="asset stage result",
    )
    if result.stage_binding_sha256 != expected_stage_binding_sha256:
        raise AssetStageVerificationError(
            "HARN_ASSET_STAGE_TRUST_MISMATCH",
            "asset stage authority binding does not match the expected identity",
        )
    expected_ref_digests = (
        (result.inventory_ref, expected_inventory_sha256, "inventory"),
        (result.repair_plan_ref, expected_repair_plan_sha256, "repair plan"),
        (
            result.source_snapshot_manifest_ref,
            expected_source_snapshot_manifest_sha256,
            "source manifest",
        ),
    )
    for ref, expected_sha256, label in expected_ref_digests:
        if ref.sha256 != expected_sha256:
            raise AssetStageVerificationError(
                "HARN_ASSET_STAGE_TRUST_MISMATCH",
                f"asset stage authority {label} does not match the expected identity",
            )

    inventory, _ = _load_canonical_ref(
        artifact_store,
        result.inventory_ref,
        AssetDebtInventory,
        label="asset debt inventory",
    )
    repair_plan, repair_plan_payload = _load_canonical_ref(
        artifact_store,
        result.repair_plan_ref,
        AssetRepairPlan,
        label="asset repair plan",
    )
    source_manifest, _ = _load_canonical_ref(
        artifact_store,
        result.source_snapshot_manifest_ref,
        AssetSourceSnapshotManifest,
        label="asset source snapshot manifest",
    )
    if source_manifest.inventory_ref != result.inventory_ref:
        _authority_mismatch("source manifest is bound to another inventory")
    if repair_plan.inventory_ref != result.inventory_ref:
        _authority_mismatch("repair plan is bound to another inventory")
    try:
        expected_plan = AssetRepairApplication(
            artifact_store=artifact_store,
            trusted_inventory_refs=(result.inventory_ref,),
        ).plan(
            AssetRepairPlanRequest(
                schema_version="harness.asset_repair_plan_request.v1",
                inventory_ref=result.inventory_ref,
                selected_asset_ids=result.selected_asset_ids,
            )
        )
    except AssetRepairError as error:
        raise AssetStageVerificationError(
            "HARN_ASSET_STAGE_AUTHORITY_MISMATCH",
            "asset stage authority selection is not derivable from the inventory",
        ) from error
    if repair_plan_payload != _canonical_model_bytes(expected_plan):
        _authority_mismatch("repair plan is not derived from the canonical inventory")

    inventory_entries = {entry.asset_id: entry for entry in inventory.entries}
    plan_items = {item.asset_id: item for item in repair_plan.items}
    manifest_assets = {asset.asset_id: asset for asset in source_manifest.assets}
    result_assets = {asset.asset_id: asset for asset in result.assets}
    selection = result.selected_asset_ids
    if (
        tuple(plan_items) != selection
        or tuple(manifest_assets) != selection
        or tuple(result_assets) != selection
        or any(asset_id not in inventory_entries for asset_id in selection)
    ):
        _authority_mismatch("asset selection differs across inventory, plan, manifest, or result")

    for asset_id in selection:
        inventory_entry = inventory_entries[asset_id]
        plan_item = plan_items[asset_id]
        manifest_asset = manifest_assets[asset_id]
        result_asset = result_assets[asset_id]
        if (
            result_asset.source_ledger_sha256 != inventory_entry.ledger_sha256
            or result_asset.source_ledger_sha256 != plan_item.ledger_sha256
        ):
            _authority_mismatch(f"source ledger identity differs for {asset_id}")
        expected_members = tuple(
            (member.logical_path, member.sha256, member.bytes)
            for member in manifest_asset.members
        )
        actual_members = tuple(
            (member.logical_path, member.source_sha256, member.source_bytes)
            for member in result_asset.members
        )
        if actual_members != expected_members:
            _authority_mismatch(f"staged members differ from the source manifest for {asset_id}")
        sidecar_paths = {
            sidecar.logical_path for sidecar in manifest_asset.model_sidecars
        }
        sidecar_payloads: dict[str, bytes] = {}
        for member in result_asset.members:
            try:
                cas_logical_path = (
                    f"sha256/{member.source_sha256[:2]}/{member.source_sha256}"
                )
                if member.logical_path in sidecar_paths:
                    payload, attestation = read_rooted_regular_file(
                        root=artifact_store.root,
                        logical_path=cas_logical_path,
                    )
                    sidecar_payloads[member.logical_path] = payload
                else:
                    attestation = attest_rooted_regular_file(
                        root=artifact_store.root,
                        logical_path=cas_logical_path,
                    )
            except AssetStageVerificationError as error:
                raise AssetStageVerificationError(
                    "HARN_ASSET_STAGE_ARTIFACT_UNAVAILABLE",
                    f"asset stage authority CAS member is unavailable: {member.logical_path}",
                ) from error
            if (
                attestation.sha256 != member.source_sha256
                or attestation.bytes != member.source_bytes
            ):
                _authority_mismatch(f"CAS member identity differs for {member.logical_path}")

        expected_representations = {
            (value.model_id, value.role, value.logical_path): value
            for value in plan_item.representations
        }
        actual_closures = {
            (value.model_id, value.role, value.root_logical_path): value
            for value in result_asset.loader_closures
        }
        if tuple(actual_closures) != tuple(expected_representations):
            _authority_mismatch(f"loader closures differ from the repair plan for {asset_id}")
        sidecars = {value.model_id: value for value in manifest_asset.model_sidecars}
        result_members_by_path = {
            member.logical_path: member for member in result_asset.members
        }
        for key, representation in expected_representations.items():
            closure = actual_closures[key]
            sidecar = sidecars.get(representation.model_id)
            if sidecar is None:
                _authority_mismatch(
                    f"model sidecar is absent for {asset_id}/model{closure.model_id}"
                )
            if (
                closure.model_sidecar_logical_path != sidecar.logical_path
                or closure.loader_scale != sidecar.scale
            ):
                _authority_mismatch(
                    f"loader sidecar contract differs for {asset_id}/model{closure.model_id}"
                )
            try:
                observed_scale = robotwin_rigid_model_sidecar_scale(
                    sidecar.logical_path,
                    sidecar_payloads[sidecar.logical_path],
                    expected_model_id=sidecar.model_id,
                )
            except RuntimeAssetSnapshotError as error:
                _authority_mismatch(
                    f"loader sidecar CAS payload is invalid for "
                    f"{asset_id}/model{closure.model_id}: {error.reason}"
                )
            if observed_scale != sidecar.scale:
                _authority_mismatch(
                    f"loader sidecar CAS scale differs for "
                    f"{asset_id}/model{closure.model_id}"
                )
            root_member = result_members_by_path[closure.root_logical_path]
            if (
                root_member.source_sha256 != representation.observed_sha256
                or root_member.source_bytes != representation.observed_bytes
            ):
                _authority_mismatch(f"loader root identity differs for {closure.root_logical_path}")
            _verify_exact_loader_closure(
                artifact_store=artifact_store,
                closure=closure,
                asset_logical_root=manifest_asset.logical_root,
                members_by_path=result_members_by_path,
            )

    return VerifiedAssetStageAuthority(
        result=result,
        inventory=inventory,
        repair_plan=repair_plan,
        source_manifest=source_manifest,
    )


def read_rooted_regular_file(
    *,
    root: Path,
    logical_path: str,
) -> tuple[bytes, RootedFileAttestation]:
    """Read one contained regular file once through a no-follow descriptor chain."""

    return _read_rooted_regular_file(root=root, logical_path=logical_path, capture_payload=True)


def attest_rooted_regular_file(*, root: Path, logical_path: str) -> RootedFileAttestation:
    """Stream-hash one contained file through a single stable no-follow descriptor."""

    _, attestation = _read_rooted_regular_file(
        root=root,
        logical_path=logical_path,
        capture_payload=False,
    )
    return attestation


def materialize_verified_asset_stage(
    *,
    artifact_store: LocalArtifactStore,
    authority: VerifiedAssetStageAuthority,
    destination_root: Path,
) -> tuple[MaterializedAssetMember, ...]:
    """Copy verified CAS members beneath one newly created, no-follow destination root."""

    if type(artifact_store) is not LocalArtifactStore:
        raise TypeError("artifact_store must be the exact LocalArtifactStore")
    if type(authority) is not VerifiedAssetStageAuthority:
        raise TypeError("authority must be the exact verified authority type")
    if not isinstance(destination_root, Path):
        raise TypeError("destination_root must be a Path")
    try:
        destination_root.mkdir(mode=0o700, parents=False, exist_ok=False)
        destination_root_fd = os.open(
            destination_root.absolute(),
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
    except OSError as error:
        raise AssetStageVerificationError(
            "HARN_ASSET_STAGE_MATERIALIZATION_FAILED",
            "asset stage materialization destination root is unsafe or unavailable",
        ) from error
    materialized: list[MaterializedAssetMember] = []
    try:
        for asset in authority.result.assets:
            for member in asset.members:
                _portable_parts(member.logical_path)
                _copy_cas_member_to_root(
                    artifact_store=artifact_store,
                    member=member,
                    destination_root_fd=destination_root_fd,
                )
                materialized.append(
                    MaterializedAssetMember(
                        logical_path=member.logical_path,
                        sha256=member.source_sha256,
                        bytes=member.source_bytes,
                    )
                )
    finally:
        os.close(destination_root_fd)
    return tuple(materialized)


def _load_canonical_ref(
    store: LocalArtifactStore,
    ref: ArtifactRef,
    model: type[_Model],
    *,
    label: str,
) -> tuple[_Model, bytes]:
    try:
        payload, attestation = read_rooted_regular_file(
            root=store.root,
            logical_path=f"sha256/{ref.sha256[:2]}/{ref.sha256}",
        )
    except AssetStageVerificationError as error:
        raise AssetStageVerificationError(
            "HARN_ASSET_STAGE_ARTIFACT_UNAVAILABLE",
            f"asset stage authority {label} is unavailable",
        ) from error
    if attestation.sha256 != ref.sha256 or attestation.bytes != ref.bytes:
        raise AssetStageVerificationError(
            "HARN_ASSET_STAGE_ARTIFACT_UNAVAILABLE",
            f"asset stage authority {label} identity differs from its ref",
        )
    return _parse_canonical_bytes(payload, model, label=label), payload


def _parse_canonical_bytes(payload: bytes, model: type[_Model], *, label: str) -> _Model:
    try:
        value = model.model_validate_json(payload)
    except (ValidationError, TypeError, ValueError) as error:
        raise AssetStageVerificationError(
            "HARN_ASSET_STAGE_RESULT_INVALID",
            f"asset stage authority {label} is invalid",
        ) from error
    if payload != _canonical_model_bytes(value):
        raise AssetStageVerificationError(
            "HARN_ASSET_STAGE_RESULT_INVALID",
            f"asset stage authority {label} is not strict canonical JSON",
        )
    return value


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


def _authority_mismatch(message: str) -> None:
    raise AssetStageVerificationError(
        "HARN_ASSET_STAGE_AUTHORITY_MISMATCH",
        f"asset stage authority mismatch: {message}",
    )


def _verify_exact_loader_closure(
    *,
    artifact_store: LocalArtifactStore,
    closure: AssetLoaderClosure,
    asset_logical_root: str,
    members_by_path: dict[str, StagedAssetMember],
) -> None:
    pending = [closure.root_logical_path, closure.model_sidecar_logical_path]
    visited: set[str] = set()
    while pending:
        logical_path = pending.pop(0)
        if logical_path in visited:
            continue
        member = members_by_path[logical_path]
        try:
            payload, attestation = read_rooted_regular_file(
                root=artifact_store.root,
                logical_path=f"sha256/{member.source_sha256[:2]}/{member.source_sha256}",
            )
        except AssetStageVerificationError as error:
            raise AssetStageVerificationError(
                "HARN_ASSET_STAGE_ARTIFACT_UNAVAILABLE",
                f"asset stage loader closure member is unavailable: {logical_path}",
            ) from error
        if (
            attestation.sha256 != member.source_sha256
            or attestation.bytes != member.source_bytes
        ):
            _authority_mismatch(f"loader closure member identity differs for {logical_path}")
        visited.add(logical_path)
        try:
            references = loader_document_references(logical_path, payload)
        except RuntimeAssetSnapshotError as error:
            _authority_mismatch(f"loader document is invalid: {logical_path}/{error.reason}")
        for reference in references:
            if reference.startswith("data:"):
                continue
            try:
                dependency = normalize_loader_reference(logical_path, reference)
            except RuntimeAssetSnapshotError as error:
                _authority_mismatch(f"loader reference is unsafe: {logical_path}/{error.reason}")
            if not dependency.startswith(asset_logical_root + "/"):
                _authority_mismatch(f"loader reference is unsafe: {logical_path}/asset escape")
            if dependency not in members_by_path:
                _authority_mismatch(f"loader dependency is absent: {dependency}")
            if dependency not in visited:
                pending.append(dependency)
        pending.sort()
    if tuple(sorted(visited)) != closure.member_logical_paths:
        _authority_mismatch(
            f"loader closure differs from exact CAS references for {closure.root_logical_path}"
        )


def _is_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _portable_parts(logical_path: str) -> tuple[str, ...]:
    if type(logical_path) is not str:
        raise AssetStageVerificationError(
            "HARN_ASSET_STAGE_MATERIALIZATION_FAILED",
            "asset stage materialization logical path must be a string",
        )
    path = PurePosixPath(logical_path)
    if (
        logical_path == "."
        or logical_path.startswith("/")
        or "\\" in logical_path
        or PureWindowsPath(logical_path).drive != ""
        or any(
            ord(character) < 32 or ord(character) in {127, 0x2028, 0x2029}
            for character in logical_path
        )
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != logical_path
    ):
        raise AssetStageVerificationError(
            "HARN_ASSET_STAGE_MATERIALIZATION_FAILED",
            "asset stage materialization logical path is unsafe",
        )
    return path.parts


def _read_rooted_regular_file(
    *,
    root: Path,
    logical_path: str,
    capture_payload: bool,
) -> tuple[bytes, RootedFileAttestation]:
    if not isinstance(root, Path):
        raise TypeError("root must be a Path")
    parts = _portable_parts(logical_path)
    directory_fds: list[int] = []
    file_descriptor: int | None = None
    chunks: list[bytes] = []
    try:
        current_fd = os.open(
            root.absolute(),
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
        file_descriptor = os.open(
            parts[-1],
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=current_fd,
        )
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise AssetStageVerificationError(
                "HARN_ASSET_STAGE_ATTESTATION_FAILED",
                f"asset stage attestation target is not regular: {logical_path}",
            )
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(file_descriptor, _READ_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            if capture_payload:
                chunks.append(chunk)
        after = os.fstat(file_descriptor)
    except AssetStageVerificationError:
        raise
    except OSError as error:
        code = (
            "HARN_ASSET_STAGE_ATTESTATION_UNSAFE"
            if error.errno in {errno.ELOOP, errno.ENOTDIR}
            else "HARN_ASSET_STAGE_ATTESTATION_FAILED"
        )
        raise AssetStageVerificationError(
            code,
            f"asset stage attestation cannot read: {logical_path}",
        ) from error
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)
    signature_before = _stat_signature(before)
    signature_after = _stat_signature(after)
    if signature_before != signature_after or size != before.st_size:
        raise AssetStageVerificationError(
            "HARN_ASSET_STAGE_ATTESTATION_DRIFT",
            f"asset stage attestation target changed during read: {logical_path}",
        )
    return b"".join(chunks), RootedFileAttestation(
        logical_path=logical_path,
        sha256=digest.hexdigest(),
        bytes=size,
        signature=signature_before,
    )


def _copy_cas_member_to_root(
    *,
    artifact_store: LocalArtifactStore,
    member: StagedAssetMember,
    destination_root_fd: int,
) -> None:
    source_sha256 = member.source_sha256
    source_parts = ("sha256", source_sha256[:2], source_sha256)
    source_directory_fds: list[int] = []
    source_fd: int | None = None
    destination_directory_fds: list[int] = []
    destination_fd: int | None = None
    destination_created = False
    destination_parent_fd: int | None = None
    destination_name: str | None = None
    try:
        current_source_fd = os.open(
            artifact_store.root.absolute(),
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        source_directory_fds.append(current_source_fd)
        for component in source_parts[:-1]:
            current_source_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=current_source_fd,
            )
            source_directory_fds.append(current_source_fd)
        source_fd = os.open(
            source_parts[-1],
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=current_source_fd,
        )
        source_before = os.fstat(source_fd)
        if not stat.S_ISREG(source_before.st_mode):
            raise AssetStageVerificationError(
                "HARN_ASSET_STAGE_MATERIALIZATION_FAILED",
                f"asset stage materialization CAS source is not regular: {member.logical_path}",
            )

        current_destination_fd = destination_root_fd
        for component in _portable_parts(member.logical_path)[:-1]:
            try:
                os.mkdir(component, mode=0o700, dir_fd=current_destination_fd)
            except FileExistsError:
                pass
            next_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=current_destination_fd,
            )
            destination_directory_fds.append(next_fd)
            current_destination_fd = next_fd
        destination_parent_fd = current_destination_fd
        destination_name = _portable_parts(member.logical_path)[-1]
        destination_fd = os.open(
            destination_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=destination_parent_fd,
        )
        destination_created = True
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(source_fd, _READ_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            pending = memoryview(chunk)
            while pending:
                written = os.write(destination_fd, pending)
                if written <= 0:
                    raise OSError(errno.EIO, "short destination write")
                pending = pending[written:]
        os.fsync(destination_fd)
        source_after = os.fstat(source_fd)
        destination_status = os.fstat(destination_fd)
        if _stat_signature(source_before) != _stat_signature(source_after):
            raise AssetStageVerificationError(
                "HARN_ASSET_STAGE_MATERIALIZATION_FAILED",
                f"asset stage materialization CAS source drifted: {member.logical_path}",
            )
        if (
            size != member.source_bytes
            or destination_status.st_size != member.source_bytes
            or digest.hexdigest() != member.source_sha256
        ):
            raise AssetStageVerificationError(
                "HARN_ASSET_STAGE_MATERIALIZATION_FAILED",
                f"asset stage materialization identity mismatch: {member.logical_path}",
            )
    except AssetStageVerificationError:
        if (
            destination_created
            and destination_name is not None
            and destination_parent_fd is not None
        ):
            try:
                os.unlink(destination_name, dir_fd=destination_parent_fd)
            except OSError:
                pass
        raise
    except OSError as error:
        if (
            destination_created
            and destination_name is not None
            and destination_parent_fd is not None
        ):
            try:
                os.unlink(destination_name, dir_fd=destination_parent_fd)
            except OSError:
                pass
        raise AssetStageVerificationError(
            "HARN_ASSET_STAGE_MATERIALIZATION_FAILED",
            f"asset stage materialization failed: {member.logical_path}",
        ) from error
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
        for directory_fd in reversed(destination_directory_fds):
            os.close(directory_fd)
        if source_fd is not None:
            os.close(source_fd)
        for directory_fd in reversed(source_directory_fds):
            os.close(directory_fd)


def _stat_signature(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


__all__ = [
    "AssetStageVerificationError",
    "MaterializedAssetMember",
    "RootedFileAttestation",
    "VerifiedAssetStageAuthority",
    "attest_rooted_regular_file",
    "materialize_verified_asset_stage",
    "read_rooted_regular_file",
    "verify_asset_stage_result_authority",
]
