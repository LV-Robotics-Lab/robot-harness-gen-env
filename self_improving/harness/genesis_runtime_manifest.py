"""Capture and recheck explicit runtime roots; no execution/qualification authority.

Deployment locators never enter CAS identity. Callers must first construct immutable,
symlink-free roots containing their complete intended deployment; this module does
not discover imports or prove that a process is confined to the supplied roots.
"""

import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path

from .artifacts import LocalArtifactStore
from .schemas.common import ArtifactRef
from .schemas.genesis_runtime_manifest import (
    GENESIS_RUNTIME_MANIFEST_SCHEMA_ID,
    RUNTIME_ROOT_ROLES,
    GenesisRuntimeManifestV1,
    RuntimeProcessIdentityV1,
)


class RuntimeManifestError(ValueError):
    """Declared closure, deployment identity, or canonical evidence does not match."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _file_identity(value):
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _walk_error(error):
    raise RuntimeManifestError("runtime directory cannot be enumerated") from error


def _read_tree(root: Path, role: str, store: LocalArtifactStore | None) -> dict[str, ArtifactRef]:
    if not root.is_absolute() or ".." in root.parts:
        raise RuntimeManifestError("deployment roots must be absolute without parent traversal")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in root.parts[1:]:
            following = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = following
        result = {}
        for directory, directories, files, directory_fd in os.fwalk(
            ".", dir_fd=descriptor, follow_symlinks=False, onerror=_walk_error
        ):
            for name in directories:
                if stat.S_ISLNK(os.stat(name, dir_fd=directory_fd, follow_symlinks=False).st_mode):
                    raise RuntimeManifestError("symlink directory in runtime root")
            for name in files:
                before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if not stat.S_ISREG(before.st_mode):
                    raise RuntimeManifestError("runtime members must be regular files")
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
                logical = (Path(directory) / name).as_posix()
                with os.fdopen(fd, "rb") as stream, tempfile.TemporaryDirectory() as temporary:
                    opened = os.fstat(stream.fileno())
                    digest = hashlib.sha256()
                    size = 0
                    staged = Path(temporary) / "member"
                    with staged.open("wb") as target:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(chunk)
                            size += len(chunk)
                            if store is not None:
                                target.write(chunk)
                    after = os.fstat(stream.fileno())
                    if _file_identity(before) != _file_identity(opened) or (
                        _file_identity(opened) != _file_identity(after)
                    ):
                        raise RuntimeManifestError("runtime member changed during capture")
                    ref = ArtifactRef(
                        name=f"{role}/{logical}",
                        uri=f"artifact://sha256/{digest.hexdigest()}",
                        sha256=digest.hexdigest(),
                        bytes=size,
                        media_type="application/octet-stream",
                        schema_version=None,
                    )
                    if store is not None:
                        store.put_file(
                            staged, name=ref.name, media_type=ref.media_type, schema_version=None
                        )
                    result[logical] = ref
        return result
    except OSError as error:
        raise RuntimeManifestError("runtime root unavailable or symlinked") from error
    finally:
        os.close(descriptor)


def _deployment(roots, store=None):
    if tuple(sorted(roots)) != RUNTIME_ROOT_ROLES:
        raise RuntimeManifestError("runtime roots must have the exact six roles")
    result = {}
    for role, root in sorted(roots.items()):
        tree = _read_tree(root, role, store)
        if not tree:
            raise RuntimeManifestError("runtime root must not be empty")
        result.update({(role, name): payload for name, payload in tree.items()})
    return result


def capture_runtime_manifest(
    store: LocalArtifactStore,
    *,
    roots: Mapping[str, Path],
    process_identity: RuntimeProcessIdentityV1,
) -> ArtifactRef:
    """Store every declared member, then publish the canonical manifest last."""
    captured = _deployment(roots, store)
    members = [
        {"root": role, "path": logical, "artifact": ref}
        for (role, logical), ref in sorted(captured.items())
    ]
    if _deployment(roots) != captured:
        raise RuntimeManifestError("runtime deployment changed during capture")
    manifest = GenesisRuntimeManifestV1(
        schema_version=GENESIS_RUNTIME_MANIFEST_SCHEMA_ID,
        authority="declared_byte_closure_only",
        process_identity=process_identity,
        members=tuple(members),
    )
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "manifest.json"
        path.write_bytes(_canonical(manifest.model_dump(mode="json")))
        return store.put_file(
            path,
            name="genesis-runtime-manifest.json",
            media_type="application/json",
            schema_version=GENESIS_RUNTIME_MANIFEST_SCHEMA_ID,
        )


def read_runtime_manifest(store: LocalArtifactStore, ref: ArtifactRef) -> GenesisRuntimeManifestV1:
    """Pure read of canonical manifest and every member in CAS."""
    if (
        ref.uri != f"artifact://sha256/{ref.sha256}"
        or ref.media_type != "application/json"
        or ref.schema_version != GENESIS_RUNTIME_MANIFEST_SCHEMA_ID
        or ref.name != "genesis-runtime-manifest.json"
    ):
        raise RuntimeManifestError("manifest must have its exact canonical CAS reference")
    payload = store.resolve(ref).path.read_bytes()
    manifest = GenesisRuntimeManifestV1.model_validate_json(payload)
    if payload != _canonical(manifest.model_dump(mode="json")):
        raise RuntimeManifestError("manifest bytes must be canonical")
    for member in manifest.members:
        store.resolve(member.artifact)
    return manifest


def verify_runtime_deployment(
    store: LocalArtifactStore,
    ref: ArtifactRef,
    *,
    roots: Mapping[str, Path],
    process_identity: RuntimeProcessIdentityV1,
) -> GenesisRuntimeManifestV1:
    """Check mapped bytes and supplied process facts; this does not attest execution."""
    manifest = read_runtime_manifest(store, ref)
    actual = _deployment(roots)
    expected = {(member.root, member.path): member.artifact for member in manifest.members}
    if actual != expected:
        raise RuntimeManifestError("runtime member inventory or bytes differ")
    if process_identity != manifest.process_identity:
        raise RuntimeManifestError("runtime process identity differs")
    return manifest
