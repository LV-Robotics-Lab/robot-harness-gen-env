"""Publish and safely materialize hash-bound scene package members through CAS."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from scene_gen.builder import verify_package

from .artifacts import ArtifactResolutionError, LocalArtifactStore
from .schemas import ArtifactRef

MANIFEST_SCHEMA = "robotwin.generated_scene_package.v1"


@dataclass(frozen=True)
class PublishedPackage:
    """The manifest and every raw member persisted in content-addressed storage."""

    manifest: ArtifactRef
    members: tuple[ArtifactRef, ...]


class PackageStoreError(RuntimeError):
    """A package is unsafe, incomplete, unavailable, or hash-inconsistent."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


class PackageStore:
    """Bridge relative package manifests to immutable CAS members and back."""

    def __init__(self, artifact_store: LocalArtifactStore) -> None:
        self.artifact_store = artifact_store

    def publish(self, package_root: Path) -> PublishedPackage:
        root = package_root.expanduser().resolve()
        manifest_path = root / "package_manifest.json"
        manifest = _read_manifest(manifest_path)
        records = _member_records(manifest)
        members: list[ArtifactRef] = []
        for record in records:
            relative = _safe_relative(record["path"])
            member = (root / relative).resolve()
            if not member.is_relative_to(root):
                raise PackageStoreError(
                    "member_path_escape",
                    f"package member escapes root through a symlink: {record['path']}",
                )
            if not member.is_file():
                raise PackageStoreError(
                    "member_missing",
                    f"package member is missing: {record['path']}",
                )
            _verify_member(member, record)
            media_type, schema_version = _member_type(member)
            members.append(
                self.artifact_store.put_file(
                    member,
                    name=member.stem,
                    media_type=media_type,
                    schema_version=schema_version,
                )
            )
        try:
            verification = verify_package(root)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            raise PackageStoreError(
                "package_verification",
                f"package verification could not run: {error}",
            ) from error
        if verification["status"] != "pass":
            raise PackageStoreError("package_verification", "package verification failed")
        manifest_ref = self.artifact_store.put_file(
            manifest_path,
            name="package_manifest",
            media_type="application/json",
            schema_version=MANIFEST_SCHEMA,
        )
        return PublishedPackage(manifest=manifest_ref, members=tuple(members))

    def materialize(self, manifest_ref: ArtifactRef, destination: Path) -> Path:
        if manifest_ref.schema_version != MANIFEST_SCHEMA:
            raise PackageStoreError(
                "manifest_schema_mismatch",
                f"manifest schema must be {MANIFEST_SCHEMA}",
            )
        try:
            resolved_manifest = self.artifact_store.resolve(manifest_ref).path
        except ArtifactResolutionError as error:
            raise PackageStoreError("manifest_unavailable", str(error)) from error
        manifest = _read_manifest(resolved_manifest)
        records = _member_records(manifest)
        target = destination.expanduser().resolve()
        if target.exists():
            raise PackageStoreError("destination_exists", f"destination already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
        try:
            for record in records:
                relative = _safe_relative(record["path"])
                member_ref = ArtifactRef(
                    name=relative.stem,
                    uri=f"artifact://sha256/{record['sha256']}",
                    media_type=_media_type(relative),
                    sha256=record["sha256"],
                    bytes=record["bytes"],
                    schema_version=_known_schema(relative),
                )
                try:
                    source = self.artifact_store.resolve(member_ref).path
                except ArtifactResolutionError as error:
                    raise PackageStoreError(
                        "member_unavailable",
                        f"package member unavailable: {record['path']}: {error}",
                    ) from error
                output = (staging / relative).resolve()
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, output)
                _verify_member(output, record)
            shutil.copyfile(resolved_manifest, staging / "package_manifest.json")
            try:
                verification = verify_package(staging)
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
                raise PackageStoreError(
                    "package_verification",
                    f"materialized package verification could not run: {error}",
                ) from error
            if verification["status"] != "pass":
                raise PackageStoreError(
                    "package_verification",
                    "materialized package verification failed",
                )
            os.replace(staging, target)
            return target
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PackageStoreError(
            "manifest_invalid",
            f"cannot read package manifest: {error}",
        ) from error
    if not isinstance(value, dict) or value.get("schema_version") != MANIFEST_SCHEMA:
        raise PackageStoreError("manifest_invalid", "package manifest schema is invalid")
    return value


def _member_records(manifest: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise PackageStoreError(
            "manifest_invalid",
            "package manifest files must be a nonempty list",
        )
    records: list[dict[str, Any]] = []
    names: set[Path] = set()
    for value in files:
        if not isinstance(value, dict):
            raise PackageStoreError("manifest_invalid", "package member record must be an object")
        try:
            relative = _safe_relative(value["path"])
            sha256 = value["sha256"]
            size = value["bytes"]
        except KeyError as error:
            raise PackageStoreError(
                "manifest_invalid",
                f"package member field missing: {error}",
            ) from error
        if relative in names:
            raise PackageStoreError("duplicate_member", f"duplicate package member: {relative}")
        if (
            not isinstance(sha256, str)
            or len(sha256) != 64
            or any(character not in "0123456789abcdef" for character in sha256)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
        ):
            raise PackageStoreError("manifest_invalid", f"invalid member identity: {relative}")
        names.add(relative)
        records.append({"path": relative.as_posix(), "sha256": sha256, "bytes": size})
    return tuple(records)


def _safe_relative(value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise PackageStoreError("unsafe_member_path", "package member path must be nonempty text")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or ".." in posix.parts
        or "\\" in value
    ):
        raise PackageStoreError("unsafe_member_path", f"unsafe package member path: {value}")
    return Path(*posix.parts)


def _verify_member(path: Path, record: dict[str, Any]) -> None:
    if path.stat().st_size != record["bytes"]:
        raise PackageStoreError("member_size_mismatch", f"member size mismatch: {record['path']}")
    if _sha256(path) != record["sha256"]:
        raise PackageStoreError(
            "member_digest_mismatch",
            f"member digest mismatch: {record['path']}",
        )


def _member_type(path: Path) -> tuple[str, str | None]:
    schema_version = None
    if path.suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise PackageStoreError(
                "member_invalid",
                f"invalid JSON member: {path.name}",
            ) from error
        if isinstance(payload, dict) and isinstance(payload.get("schema_version"), str):
            schema_version = payload["schema_version"]
    return _media_type(path), schema_version


def _media_type(path: Path) -> str:
    if path.suffix == ".json":
        return "application/json"
    if path.suffix == ".py":
        return "text/x-python"
    if path.suffix == ".txt":
        return "text/plain"
    return "application/octet-stream"


def _known_schema(path: Path) -> str | None:
    return {
        "scene_spec.json": "robotwin.scene_spec.v1",
        "resolved_scene.json": "robotwin.resolved_scene.v1",
    }.get(path.as_posix())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
