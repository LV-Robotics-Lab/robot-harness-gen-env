"""Stage audited legacy Genesis asset bytes without granting runtime authority."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .artifacts import LocalArtifactStore
from .schemas.common import ArtifactRef
from .schemas.x2env_compile_evidence import (
    AssetModelSubjectV1,
    AssetRepresentationMemberV1,
    AssetRepresentationV1,
    asset_loader_closure_sha256,
    canonical_evidence_sha256,
)

_LEGACY_SCHEMA = "genenv.standard_urdf_asset.v1"
_REPRESENTATION_SCHEMA = "harness.asset_representation.v1"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MANIFEST_FIELDS = {
    "asset_id",
    "category",
    "entrypoint",
    "files",
    "path_mapping",
    "physics_file",
    "schema_version",
    "source",
}


class LegacyGenesisAssetStageError(ValueError):
    """A legacy source is unavailable or does not match its frozen byte closure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class LegacyGenesisAssetSourceBinding:
    """Deployment locator plus the exact identity of one legacy asset manifest."""

    root: Path
    manifest_relative_path: str
    expected_manifest_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.root, Path):
            raise TypeError("root must be a pathlib Path")
        _require_relative_path(self.manifest_relative_path, "manifest_relative_path")
        if _SHA256.fullmatch(self.expected_manifest_sha256) is None:
            raise ValueError("expected_manifest_sha256 must be 64 lowercase hex")


@dataclass(frozen=True, slots=True)
class StagedLegacyGenesisAssetRepresentation:
    """Portable bytes and typed representation, intentionally without qualification."""

    representation: AssetRepresentationV1
    representation_ref: ArtifactRef


@dataclass(frozen=True, slots=True)
class _VerifiedMember:
    logical_path: str
    path: Path
    size: int
    sha256: str
    role: str


def stage_legacy_genesis_asset_representation(
    *,
    artifact_store: LocalArtifactStore,
    source: LegacyGenesisAssetSourceBinding,
    subject: AssetModelSubjectV1,
) -> StagedLegacyGenesisAssetRepresentation:
    """Verify a complete legacy URDF closure, then publish only representation bytes."""

    if type(artifact_store) is not LocalArtifactStore:
        raise TypeError("artifact_store must be an exact LocalArtifactStore")
    if type(source) is not LegacyGenesisAssetSourceBinding:
        raise TypeError("source must be an exact LegacyGenesisAssetSourceBinding")
    if type(subject) is not AssetModelSubjectV1:
        raise TypeError("subject must be an exact AssetModelSubjectV1")
    manifest, members = _verify_source(source)
    if (
        subject.source_record_sha256 != source.expected_manifest_sha256
        or subject.asset_key != manifest["asset_id"]
    ):
        raise LegacyGenesisAssetStageError(
            "HARN_LEGACY_ASSET_CLOSURE_MISMATCH",
            "asset subject does not bind the legacy manifest",
        )

    staged_members = tuple(
        AssetRepresentationMemberV1(
            logical_path=member.logical_path,
            role=member.role,
            artifact=artifact_store.put_file(
                member.path,
                name=member.logical_path,
                media_type=_media_type(member.logical_path),
                schema_version=None,
            ),
        )
        for member in members
    )
    root_logical_path = manifest["entrypoint"]
    loader_closure = asset_loader_closure_sha256(
        subject,
        root_logical_path,
        staged_members,
    )
    representation_payload: dict[str, Any] = {
        "schema_version": _REPRESENTATION_SCHEMA,
        "representation_sha256": "0" * 64,
        "subject": subject,
        "representation_kind": "urdf",
        "root_logical_path": root_logical_path,
        "loader_scale": (1.0, 1.0, 1.0),
        "loader_closure_sha256": loader_closure,
        "members": staged_members,
        "collision_member_paths": tuple(
            member.logical_path for member in members if member.role == "collision"
        ),
        "visual_member_paths": tuple(
            member.logical_path for member in members if member.role == "visual"
        ),
    }
    representation_payload["representation_sha256"] = canonical_evidence_sha256(
        representation_payload,
        "representation_sha256",
    )
    representation = AssetRepresentationV1.model_validate(representation_payload)
    encoded = _canonical_json_bytes(representation.model_dump(mode="json"))
    with tempfile.TemporaryDirectory(prefix="legacy-genesis-representation-") as directory:
        document = Path(directory) / "representation.json"
        document.write_bytes(encoded)
        representation_ref = artifact_store.put_file(
            document,
            name="legacy-genesis-asset-representation.json",
            media_type="application/json",
            schema_version=_REPRESENTATION_SCHEMA,
        )
    return StagedLegacyGenesisAssetRepresentation(
        representation=representation,
        representation_ref=representation_ref,
    )


def _verify_source(
    source: LegacyGenesisAssetSourceBinding,
) -> tuple[dict[str, Any], tuple[_VerifiedMember, ...]]:
    root = source.root
    if _is_symlink(root) or not root.is_dir():
        _source_unavailable("legacy asset root is unavailable or unsafe")
    manifest_path = root / source.manifest_relative_path
    if _has_symlink_component(root, manifest_path) or not manifest_path.is_file():
        _source_unavailable("legacy asset manifest is unavailable or unsafe")
    payload = manifest_path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != source.expected_manifest_sha256:
        _closure_mismatch("legacy asset manifest digest changed")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LegacyGenesisAssetStageError(
            "HARN_LEGACY_ASSET_CLOSURE_MISMATCH",
            "legacy asset manifest is not valid JSON",
        ) from error
    if (
        type(value) is not dict
        or set(value) != _MANIFEST_FIELDS
        or value.get("schema_version") != _LEGACY_SCHEMA
        or type(value.get("asset_id")) is not str
        or not value["asset_id"]
        or type(value.get("category")) is not str
        or not value["category"]
        or value.get("physics_file") != "physics.json"
    ):
        _closure_mismatch("legacy asset manifest has the wrong schema")
    entrypoint = value.get("entrypoint")
    _require_manifest_relative_path(entrypoint, "entrypoint")
    declared = value.get("files")
    if type(declared) is not list or not declared:
        _closure_mismatch("legacy asset manifest files are absent")

    members: list[_VerifiedMember] = []
    logical_paths: list[str] = []
    for item in declared:
        if type(item) is not dict or set(item) != {"path", "sha256", "size_bytes"}:
            _closure_mismatch("legacy asset member declaration is invalid")
        logical_path = item.get("path")
        declared_sha256 = item.get("sha256")
        declared_size = item.get("size_bytes")
        _require_manifest_relative_path(logical_path, "member path")
        if type(declared_sha256) is not str or _SHA256.fullmatch(declared_sha256) is None:
            _closure_mismatch("legacy asset member digest is invalid")
        if type(declared_size) is not int or declared_size < 0:
            _closure_mismatch("legacy asset member size is invalid")
        member_path = root / logical_path
        if _has_symlink_component(root, member_path) or not member_path.is_file():
            _source_unavailable("legacy asset member is unavailable or unsafe")
        actual_size = member_path.stat().st_size
        actual_sha256 = _sha256(member_path)
        if actual_size != declared_size or actual_sha256 != declared_sha256:
            _closure_mismatch("legacy asset member bytes changed")
        logical_paths.append(logical_path)
        members.append(
            _VerifiedMember(
                logical_path=logical_path,
                path=member_path,
                size=actual_size,
                sha256=actual_sha256,
                role=_role(logical_path),
            )
        )
    if logical_paths != sorted(logical_paths) or len(logical_paths) != len(set(logical_paths)):
        _closure_mismatch("legacy asset members must be sorted and unique")
    if entrypoint not in set(logical_paths):
        _closure_mismatch("legacy asset entrypoint is not a declared member")

    expected_files = {source.manifest_relative_path, *logical_paths}
    actual_files: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            _source_unavailable("legacy asset source contains a symlink")
        if path.is_file():
            actual_files.add(path.relative_to(root).as_posix())
    if actual_files != expected_files:
        _closure_mismatch("legacy asset source has missing or extra members")
    return value, tuple(members)


def _require_relative_path(value: str, label: str) -> None:
    if type(value) is not str or not value:
        raise ValueError(f"{label} must be a non-empty relative path")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or "\\" in value
        or posix.as_posix() != value
        or any(part in {"", ".", ".."} for part in posix.parts)
    ):
        raise ValueError(f"{label} must be a normalized relative path")


def _require_manifest_relative_path(value: object, label: str) -> None:
    try:
        _require_relative_path(value, label)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise LegacyGenesisAssetStageError(
            "HARN_LEGACY_ASSET_CLOSURE_MISMATCH",
            f"legacy asset {label} is not portable",
        ) from error


def _has_symlink_component(root: Path, path: Path) -> bool:
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if _is_symlink(current):
            return True
    return False


def _is_symlink(path: Path) -> bool:
    try:
        return stat.S_ISLNK(path.lstat().st_mode)
    except FileNotFoundError:
        return False


def _role(logical_path: str) -> str:
    if logical_path.endswith(".urdf"):
        return "urdf"
    if "/collision/" in logical_path:
        return "collision"
    if logical_path == "physics.json":
        return "metadata"
    return "visual"


def _media_type(logical_path: str) -> str:
    suffix = PurePosixPath(logical_path).suffix.lower()
    return {
        ".json": "application/json",
        ".mtl": "text/plain",
        ".obj": "text/plain",
        ".png": "image/png",
        ".urdf": "application/xml",
    }.get(suffix, "application/octet-stream")


def _canonical_json_bytes(value: object) -> bytes:
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_unavailable(message: str) -> None:
    raise LegacyGenesisAssetStageError("HARN_LEGACY_ASSET_SOURCE_UNAVAILABLE", message)


def _closure_mismatch(message: str) -> None:
    raise LegacyGenesisAssetStageError("HARN_LEGACY_ASSET_CLOSURE_MISMATCH", message)


__all__ = [
    "LegacyGenesisAssetSourceBinding",
    "LegacyGenesisAssetStageError",
    "StagedLegacyGenesisAssetRepresentation",
    "stage_legacy_genesis_asset_representation",
]
