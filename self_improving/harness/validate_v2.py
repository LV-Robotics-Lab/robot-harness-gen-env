"""Strict one-CAS evidence loading for ``text2env.validate@2.0.0``.

This first validation-v2 module does not decide publication.  It makes the
byte authority unambiguous: every caller-supplied reference is checked before
content identities may be deduplicated, and every byte is read from a regular
file beneath one concrete :class:`LocalArtifactStore`.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .artifacts import LocalArtifactStore
from .schemas import ArtifactRef
from .schemas.text2env_validate_v2 import (
    _INPUT_ARTIFACT_CONTRACTS,
    Text2EnvValidateV2Input,
)

_MAX_JSON_ARTIFACT_BYTES = 16 * 1024 * 1024
_MAX_RUNTIME_ASSET_MANIFEST_BYTES = 32 * 1024 * 1024
_MAX_TRANSCRIPT_BYTES = 1024 * 1024
_MAX_TOTAL_ARTIFACT_BYTES = 64 * 1024 * 1024


class ValidateV2EvidenceError(RuntimeError):
    """A raw v2 reference or its content failed the CAS-only contract."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ValidateV2ArtifactBytes:
    """Opaque bytes loaded for one named role; this is not semantic proof."""

    field_name: str
    ref: ArtifactRef
    payload: bytes


class ValidateV2EvidenceBytes:
    """Immutable CAS bytes, deliberately not an authorization or verification token."""

    __slots__ = ("_artifacts", "_by_name")

    def __init__(self, artifacts: tuple[ValidateV2ArtifactBytes, ...]) -> None:
        self._artifacts = artifacts
        self._by_name = MappingProxyType({item.field_name: item for item in artifacts})

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(item.field_name for item in self._artifacts)

    def artifact(self, field_name: str) -> ValidateV2ArtifactBytes:
        return self._by_name[field_name]


class StrictValidateV2CasReader:
    """Read all raw validation evidence through one fixed local CAS layout."""

    __slots__ = ("_root",)

    def __init__(self, artifact_store: LocalArtifactStore) -> None:
        if type(artifact_store) is not LocalArtifactStore:
            raise TypeError("artifact_store must be LocalArtifactStore")
        self._root = artifact_store.root

    def load(self, value: Text2EnvValidateV2Input) -> ValidateV2EvidenceBytes:
        if type(value) is not Text2EnvValidateV2Input:
            raise TypeError("value must be Text2EnvValidateV2Input")
        try:
            strict_value = Text2EnvValidateV2Input.model_validate(value.model_dump(mode="python"))
        except (TypeError, ValueError) as error:
            raise ValidateV2EvidenceError(
                "invalid_reference",
                f"validation input must satisfy its strict typed schema: {error}",
            ) from error
        entries = _validated_raw_refs(strict_value)
        artifacts: list[ValidateV2ArtifactBytes] = []
        payloads: dict[str, bytes] = {}
        sha256_directory = _open_sha256_directory(self._root)
        try:
            for field_name, contract, ref in entries:
                payload = payloads.get(ref.sha256)
                if payload is None:
                    payload = _read_cas_bytes(sha256_directory, ref)
                    payloads[ref.sha256] = payload
                artifacts.append(
                    ValidateV2ArtifactBytes(
                        field_name=field_name,
                        ref=ref,
                        payload=payload,
                    )
                )
        finally:
            os.close(sha256_directory)
        return ValidateV2EvidenceBytes(tuple(artifacts))


def _validated_raw_refs(
    value: Text2EnvValidateV2Input,
) -> tuple[tuple[str, Any, ArtifactRef], ...]:
    entries: list[tuple[str, Any, ArtifactRef]] = []
    declared_bytes: dict[str, int] = {}
    total_bytes = 0
    for field_name, contract in _INPUT_ARTIFACT_CONTRACTS.items():
        ref = getattr(value, field_name)
        limit = _artifact_limit(field_name, contract.media_type)
        if ref.bytes > limit:
            raise ValidateV2EvidenceError(
                "artifact_too_large",
                f"{ref.name} exceeds the validation evidence byte limit",
            )
        previous_bytes = declared_bytes.get(ref.sha256)
        if previous_bytes is None:
            declared_bytes[ref.sha256] = ref.bytes
            total_bytes += ref.bytes
        elif previous_bytes != ref.bytes:
            raise ValidateV2EvidenceError(
                "invalid_reference",
                f"{ref.name} conflicts with another declaration of the same content identity",
            )
        entries.append((field_name, contract, ref))
    if total_bytes > _MAX_TOTAL_ARTIFACT_BYTES:
        raise ValidateV2EvidenceError(
            "artifact_too_large",
            "the validation evidence set exceeds the aggregate byte limit",
        )
    return tuple(entries)


def _artifact_limit(field_name: str, media_type: str) -> int:
    if media_type == "application/x-ndjson":
        return _MAX_TRANSCRIPT_BYTES
    if field_name == "runtime_asset_snapshot_manifest":
        return _MAX_RUNTIME_ASSET_MANIFEST_BYTES
    return _MAX_JSON_ARTIFACT_BYTES


def _open_sha256_directory(root: os.PathLike[str]) -> int:
    directory_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
    root_descriptor = -1
    try:
        root_descriptor = os.open(root, directory_flags)
        return os.open("sha256", directory_flags, dir_fd=root_descriptor)
    except OSError as error:
        reason = (
            "artifact_unsafe"
            if error.errno in {errno.EISDIR, errno.ELOOP, errno.ENOTDIR}
            else "artifact_unavailable"
        )
        message = (
            "the validation CAS root must be a regular directory tree"
            if reason == "artifact_unsafe"
            else "the validation CAS root is unavailable"
        )
        raise ValidateV2EvidenceError(reason, message) from error
    finally:
        if root_descriptor >= 0:
            os.close(root_descriptor)


def _read_cas_bytes(sha256_directory: int, ref: ArtifactRef) -> bytes:
    directory_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    opened: list[int] = []
    try:
        opened.append(os.open(ref.sha256[:2], directory_flags, dir_fd=sha256_directory))
        file_descriptor = os.open(ref.sha256, file_flags, dir_fd=opened[-1])
        opened.append(file_descriptor)
        metadata = os.fstat(file_descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValidateV2EvidenceError(
                "artifact_unsafe",
                f"{ref.name} must resolve to a regular CAS file",
            )
        if metadata.st_size != ref.bytes:
            raise ValidateV2EvidenceError(
                "artifact_mismatch",
                f"{ref.name} content identity does not match its byte count",
            )
        chunks: list[bytes] = []
        remaining = ref.bytes
        while remaining:
            chunk = os.read(file_descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(file_descriptor, 1):
            raise ValidateV2EvidenceError(
                "artifact_mismatch",
                f"{ref.name} content identity changed while it was read",
            )
        payload = b"".join(chunks)
    except ValidateV2EvidenceError:
        raise
    except OSError as error:
        reason = (
            "artifact_unsafe"
            if error.errno in {errno.EISDIR, errno.ELOOP, errno.ENOTDIR}
            else "artifact_unavailable"
        )
        message = (
            f"{ref.name} must resolve to a regular CAS file"
            if reason == "artifact_unsafe"
            else f"{ref.name} is unavailable in the validation CAS"
        )
        raise ValidateV2EvidenceError(reason, message) from error
    finally:
        for file_descriptor in reversed(opened):
            os.close(file_descriptor)
    if len(payload) != ref.bytes or hashlib.sha256(payload).hexdigest() != ref.sha256:
        raise ValidateV2EvidenceError(
            "artifact_mismatch",
            f"{ref.name} content identity does not match its ArtifactRef",
        )
    return payload


__all__ = [
    "StrictValidateV2CasReader",
    "ValidateV2ArtifactBytes",
    "ValidateV2EvidenceBytes",
    "ValidateV2EvidenceError",
]
