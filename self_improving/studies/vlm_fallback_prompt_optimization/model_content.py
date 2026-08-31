"""Content-bound identity manifests for local Hugging Face model snapshots."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

SCHEMA_VERSION = "vlm_fallback.model_content_manifest.v2"
_BLOCK_SIZE = 1024 * 1024
_SNAPSHOT_AUTHORITY = os.urandom(32)
_TOP_LEVEL_FIELDS = {"schema_version", "model_id", "revision", "entries"}
_ENTRY_FIELDS = {
    "path",
    "blob_id",
    "blob_id_algorithm",
    "size_bytes",
    "content_sha256",
}
_STAT_FIELDS = (
    "st_dev",
    "st_ino",
    "st_mode",
    "st_nlink",
    "st_uid",
    "st_gid",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
)


@dataclass(frozen=True)
class _RosterEntry:
    path: str
    snapshot_lstat: tuple[int, ...]
    blob_id: str
    target_fstat: tuple[int, ...]


@dataclass(frozen=True)
class ModelContentSnapshot:
    """Opaque runtime baseline used to detect snapshot roster drift."""

    manifest_sha256: str
    roster_sha256: str
    _roster: tuple[_RosterEntry, ...] = field(repr=False)
    _attestation: str = field(default="", repr=False, compare=False)


def _roster_value(roster: tuple[_RosterEntry, ...]) -> list[dict[str, Any]]:
    if type(roster) is not tuple:
        raise ValueError("model content snapshot roster is malformed")
    value: list[dict[str, Any]] = []
    for item in roster:
        if (
            type(item) is not _RosterEntry
            or type(item.path) is not str
            or type(item.snapshot_lstat) is not tuple
            or type(item.blob_id) is not str
            or type(item.target_fstat) is not tuple
            or any(type(part) is not int for part in item.snapshot_lstat)
            or any(type(part) is not int for part in item.target_fstat)
        ):
            raise ValueError("model content snapshot roster is malformed")
        value.append(
            {
                "path": item.path,
                "snapshot_lstat": item.snapshot_lstat,
                "blob_id": item.blob_id,
                "target_fstat": item.target_fstat,
            }
        )
    return value


def _snapshot_attestation(
    manifest_digest: str,
    roster_digest: str,
    roster: tuple[_RosterEntry, ...],
) -> str:
    payload = {
        "manifest_sha256": manifest_digest,
        "roster": _roster_value(roster),
        "roster_sha256": roster_digest,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(_SNAPSHOT_AUTHORITY, encoded, hashlib.sha256).hexdigest()


def _require_issued_snapshot(baseline: ModelContentSnapshot) -> None:
    if (
        type(baseline) is not ModelContentSnapshot
        or type(baseline.manifest_sha256) is not str
        or type(baseline.roster_sha256) is not str
        or type(baseline._attestation) is not str
    ):
        raise ValueError("baseline must be issued by verify_model_content")
    try:
        expected = _snapshot_attestation(
            baseline.manifest_sha256,
            baseline.roster_sha256,
            baseline._roster,
        )
    except ValueError as error:
        raise ValueError("baseline must be issued by verify_model_content") from error
    if not hmac.compare_digest(baseline._attestation, expected):
        raise ValueError("baseline must be issued by verify_model_content")


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    """Encode a manifest as compact, sorted UTF-8 JSON without a trailing newline."""

    validate_model_content_manifest(manifest)
    return json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def manifest_sha256(manifest: dict[str, Any]) -> str:
    """Return the digest of the canonical manifest bytes."""

    return hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()


def _validate_relative_path(path: Any) -> str:
    if not isinstance(path, str) or not path or "\\" in path or "\0" in path:
        raise ValueError("entry path must be a non-empty relative POSIX path")
    if unicodedata.normalize("NFC", path) != path:
        raise ValueError("entry path must use NFC normalization")
    relative = PurePosixPath(path)
    windows = PureWindowsPath(path)
    if (
        relative.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in relative.parts
        or relative == PurePosixPath(".")
        or relative.as_posix() != path
    ):
        raise ValueError("entry path must be a canonical relative POSIX path")
    return path


def _is_lower_hex(value: Any, length: int) -> bool:
    if not isinstance(value, str) or len(value) != length or value != value.lower():
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _require_exact_json_types(value: Any) -> None:
    value_type = type(value)
    if value_type is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("manifest must use exact built-in JSON types")
            _require_exact_json_types(item)
        return
    if value_type is list:
        for item in value:
            _require_exact_json_types(item)
        return
    if value_type not in {str, int, float, bool, type(None)}:
        raise ValueError("manifest must use exact built-in JSON types")


def validate_model_content_manifest(manifest: dict[str, Any]) -> None:
    """Validate the exact, location-independent v2 identity schema."""

    _require_exact_json_types(manifest)
    if not isinstance(manifest, dict) or set(manifest) != _TOP_LEVEL_FIELDS:
        raise ValueError("manifest must contain the exact top-level fields")
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
    model_id = manifest["model_id"]
    if (
        not isinstance(model_id, str)
        or not model_id
        or model_id.strip() != model_id
        or unicodedata.normalize("NFC", model_id) != model_id
    ):
        raise ValueError("model_id must be a non-empty NFC string")
    if not _is_lower_hex(manifest["revision"], 40):
        raise ValueError("revision must be 40 lowercase hex characters")
    entries = manifest["entries"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("entries must be a list with at least one item")
    paths: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _ENTRY_FIELDS:
            raise ValueError("each entry must contain the exact entry fields")
        paths.append(_validate_relative_path(entry.get("path")))
        blob_id = entry["blob_id"]
        if not (_is_lower_hex(blob_id, 40) or _is_lower_hex(blob_id, 64)):
            raise ValueError("blob_id must be 40 or 64 lowercase hex characters")
        expected_algorithm = "git_blob_sha1" if len(blob_id) == 40 else "sha256"
        if entry["blob_id_algorithm"] != expected_algorithm:
            raise ValueError(f"blob_id_algorithm must be {expected_algorithm}")
        content_sha256 = entry["content_sha256"]
        if not _is_lower_hex(content_sha256, 64):
            raise ValueError("content_sha256 must be 64 lowercase hex characters")
        size_bytes = entry["size_bytes"]
        if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0:
            raise ValueError("size_bytes must be a non-negative integer")
        if len(blob_id) == 64 and blob_id != content_sha256:
            raise ValueError("64-hex blob_id must match content_sha256")
    if len(paths) != len(set(paths)):
        raise ValueError("duplicate entry path")
    if paths != sorted(paths, key=lambda path: path.encode("utf-8")):
        raise ValueError("entries must be UTF-8 sorted by path")


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def load_model_content_manifest(
    path: Path,
    expected_sha256: str,
    *,
    model_id: str,
    revision: str,
) -> dict[str, Any]:
    """Load one exact canonical manifest bound to its declared model identity."""

    if any(type(value) is not str for value in (expected_sha256, model_id, revision)):
        raise ValueError("model content identity pins must be exact built-in strings")
    if not _is_lower_hex(expected_sha256, 64):
        raise ValueError("expected manifest SHA-256 must be 64 lowercase hex characters")
    raw = path.read_bytes()
    observed_sha256 = hashlib.sha256(raw).hexdigest()
    if observed_sha256 != expected_sha256:
        raise ValueError("model content manifest digest mismatch")
    try:
        manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=_object_without_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("model content manifest must be UTF-8 JSON") from error
    if not isinstance(manifest, dict):
        raise ValueError("model content manifest must be a JSON object")
    validate_model_content_manifest(manifest)
    if canonical_manifest_bytes(manifest) != raw:
        raise ValueError("model content manifest must use canonical JSON bytes")
    if manifest["model_id"] != model_id:
        raise ValueError("model content manifest model_id mismatch")
    if manifest["revision"] != revision:
        raise ValueError("model content manifest revision mismatch")
    return manifest


def _hash_blob(path: Path) -> tuple[int, str, str, tuple[int, ...]]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor: int | None = os.open(path, flags)
    except OSError as error:
        raise ValueError(f"resolved snapshot entry is not a regular file: {path}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"resolved snapshot entry is not a regular file: {path}")
        stream = os.fdopen(descriptor, "rb")
        descriptor = None
        with stream:
            content_digest = hashlib.sha256()
            git_blob_digest = hashlib.sha1(f"blob {before.st_size}\0".encode())
            for block in iter(lambda: stream.read(_BLOCK_SIZE), b""):
                content_digest.update(block)
                git_blob_digest.update(block)
            after = os.fstat(stream.fileno())
    finally:
        if descriptor is not None:
            os.close(descriptor)
    size_bytes = before.st_size
    if _stat_fingerprint(before) != _stat_fingerprint(after):
        raise ValueError(f"resolved snapshot entry changed while hashing: {path}")
    return (
        size_bytes,
        content_digest.hexdigest(),
        git_blob_digest.hexdigest(),
        _stat_fingerprint(after),
    )


def _snapshot_files(snapshot: Path) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    try:
        candidates = list(snapshot.rglob("*"))
    except OSError as error:
        raise ValueError("model snapshot roster could not be enumerated") from error
    for candidate in candidates:
        relative_path = _validate_relative_path(candidate.relative_to(snapshot).as_posix())
        if candidate.is_symlink() or not candidate.is_dir():
            files.append((relative_path, candidate))
    return sorted(files, key=lambda item: item[0].encode("utf-8"))


def _stat_fingerprint(metadata: os.stat_result) -> tuple[int, ...]:
    return tuple(getattr(metadata, field_name) for field_name in _STAT_FIELDS)


def _snapshot_lstat(entry: Path, relative_path: str) -> os.stat_result:
    try:
        return entry.lstat()
    except OSError as error:
        raise ValueError(f"snapshot entry changed or disappeared: {relative_path}") from error


def _resolve_snapshot_root(snapshot_path: Path) -> Path:
    try:
        snapshot = snapshot_path.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError("model snapshot is missing or cannot be resolved") from error
    if not snapshot.is_dir():
        raise ValueError("model snapshot must resolve to a directory")
    return snapshot


def _resolve_snapshot_entry(entry: Path, relative_path: str, repository_root: Path) -> Path:
    try:
        pending = list(entry.relative_to(repository_root).parts)
    except ValueError as error:
        raise ValueError(f"snapshot entry escapes model repository: {relative_path}") from error
    current = repository_root
    seen: set[tuple[str, tuple[str, ...]]] = set()
    symlink_hops = 0
    try:
        while pending:
            candidate = current / pending.pop(0)
            metadata = candidate.lstat()
            if not stat.S_ISLNK(metadata.st_mode):
                current = candidate
                continue
            symlink_hops += 1
            if symlink_hops > 64:
                raise RuntimeError("too many symbolic links")
            raw_target = Path(os.readlink(candidate))
            target = raw_target if raw_target.is_absolute() else candidate.parent / raw_target
            target = Path(os.path.normpath(os.fspath(target)))
            if not target.is_relative_to(repository_root):
                raise ValueError(f"snapshot entry escapes model repository: {relative_path}")
            state = (os.fspath(target), tuple(pending))
            if state in seen:
                raise RuntimeError("symbolic link loop")
            seen.add(state)
            pending = list(target.relative_to(repository_root).parts) + pending
            current = repository_root
    except ValueError:
        raise
    except (OSError, RuntimeError) as error:
        raise ValueError(f"broken snapshot link: {relative_path}") from error
    return current


def _capture_roster(snapshot: Path) -> tuple[tuple[_RosterEntry, ...], str]:
    repository_root = snapshot.parent.parent.resolve(strict=True)
    roster: list[_RosterEntry] = []
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    for relative_path, entry in _snapshot_files(snapshot):
        before_link = _snapshot_lstat(entry, relative_path)
        resolved = _resolve_snapshot_entry(entry, relative_path, repository_root)
        try:
            descriptor = os.open(resolved, flags)
        except OSError as error:
            raise ValueError(
                f"resolved snapshot entry is not a regular file: {relative_path}"
            ) from error
        try:
            target_metadata = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if not stat.S_ISREG(target_metadata.st_mode):
            raise ValueError(f"resolved snapshot entry is not a regular file: {relative_path}")
        after_link = _snapshot_lstat(entry, relative_path)
        if _stat_fingerprint(before_link) != _stat_fingerprint(after_link):
            raise ValueError(f"snapshot entry changed while capturing roster: {relative_path}")
        if _resolve_snapshot_entry(entry, relative_path, repository_root) != resolved:
            raise ValueError(f"snapshot entry changed while capturing roster: {relative_path}")
        try:
            current_target = resolved.stat(follow_symlinks=False)
        except OSError as error:
            raise ValueError(
                f"resolved snapshot entry changed while capturing roster: {relative_path}"
            ) from error
        if _stat_fingerprint(current_target) != _stat_fingerprint(target_metadata):
            raise ValueError(f"snapshot entry changed while capturing roster: {relative_path}")
        roster.append(
            _RosterEntry(
                path=relative_path,
                snapshot_lstat=_stat_fingerprint(after_link),
                blob_id=resolved.name,
                target_fstat=_stat_fingerprint(target_metadata),
            )
        )
    frozen_roster = tuple(roster)
    roster_value = _roster_value(frozen_roster)
    roster_bytes = json.dumps(roster_value, sort_keys=True, separators=(",", ":")).encode()
    return frozen_roster, hashlib.sha256(roster_bytes).hexdigest()


def build_model_content_manifest(
    snapshot_path: Path,
    *,
    model_id: str,
    revision: str,
) -> dict[str, Any]:
    """Hash every file in one local Hugging Face snapshot."""

    snapshot = _resolve_snapshot_root(snapshot_path)
    repository_root = snapshot.parent.parent.resolve(strict=True)
    entries: list[dict[str, Any]] = []
    for relative_path, entry in _snapshot_files(snapshot):
        before_link = _snapshot_lstat(entry, relative_path)
        resolved = _resolve_snapshot_entry(entry, relative_path, repository_root)
        size_bytes, content_sha256, git_blob_sha1, target_fingerprint = _hash_blob(resolved)
        try:
            after_link = _snapshot_lstat(entry, relative_path)
            current_resolved = _resolve_snapshot_entry(entry, relative_path, repository_root)
            current_target = current_resolved.stat(follow_symlinks=False)
        except OSError as error:
            raise ValueError(f"snapshot entry changed while hashing: {relative_path}") from error
        if (
            _stat_fingerprint(before_link) != _stat_fingerprint(after_link)
            or current_resolved != resolved
            or _stat_fingerprint(current_target) != target_fingerprint
        ):
            raise ValueError(f"snapshot entry changed while hashing: {relative_path}")
        blob_id = resolved.name
        if len(blob_id) == 64 and blob_id == content_sha256:
            algorithm = "sha256"
        elif len(blob_id) == 40 and blob_id == git_blob_sha1:
            algorithm = "git_blob_sha1"
        else:
            raise ValueError(f"resolved blob id does not match content: {relative_path}")
        entries.append(
            {
                "path": relative_path,
                "blob_id": blob_id,
                "blob_id_algorithm": algorithm,
                "size_bytes": size_bytes,
                "content_sha256": content_sha256,
            }
        )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "model_id": model_id,
        "revision": revision,
        "entries": entries,
    }
    validate_model_content_manifest(manifest)
    return manifest


def verify_model_content(
    snapshot_path: Path,
    manifest: dict[str, Any],
) -> ModelContentSnapshot:
    """Fully rehash a snapshot and bind a stable runtime roster to the manifest."""

    validate_model_content_manifest(manifest)
    snapshot = _resolve_snapshot_root(snapshot_path)
    roster_before, _ = _capture_roster(snapshot)
    observed = build_model_content_manifest(
        snapshot,
        model_id=manifest["model_id"],
        revision=manifest["revision"],
    )
    roster_after, roster_sha256 = _capture_roster(snapshot)
    if roster_before != roster_after:
        raise ValueError("model snapshot changed during content verification")
    if observed != manifest:
        raise ValueError("model snapshot content mismatch")
    manifest_digest = manifest_sha256(manifest)
    return ModelContentSnapshot(
        manifest_sha256=manifest_digest,
        roster_sha256=roster_sha256,
        _roster=roster_after,
        _attestation=_snapshot_attestation(manifest_digest, roster_sha256, roster_after),
    )


def refresh_model_content(
    snapshot_path: Path,
    manifest: dict[str, Any],
    baseline: ModelContentSnapshot,
) -> ModelContentSnapshot:
    """Stat the complete roster and fully rehash whenever any metadata drifts."""

    validate_model_content_manifest(manifest)
    expected_manifest_sha256 = manifest_sha256(manifest)
    _require_issued_snapshot(baseline)
    if baseline.manifest_sha256 != expected_manifest_sha256:
        raise ValueError("baseline model content manifest digest mismatch")
    snapshot = _resolve_snapshot_root(snapshot_path)
    current_roster, current_roster_sha256 = _capture_roster(snapshot)
    if current_roster == baseline._roster:
        if current_roster_sha256 != baseline.roster_sha256:
            raise ValueError("baseline model content roster digest mismatch")
        return baseline
    return verify_model_content(snapshot, manifest)
