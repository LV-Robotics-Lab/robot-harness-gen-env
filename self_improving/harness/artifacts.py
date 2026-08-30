"""Content-addressed artifact storage and verified local resolution."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlparse

from .schemas import ArtifactRef


@dataclass(frozen=True)
class ResolvedArtifact:
    """A verified local path paired with the reference that identified it."""

    ref: ArtifactRef
    path: Path


class ArtifactResolver(Protocol):
    """Resolve a content identity to a verified local artifact."""

    def resolve(self, ref: ArtifactRef) -> ResolvedArtifact: ...


class ArtifactResolutionError(ValueError):
    """A fail-closed locator, availability, or content-integrity error."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


class LocalArtifactStore:
    """Store immutable bytes by SHA-256 and resolve ``artifact://`` references."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def _content_path(self, sha256: str) -> Path:
        return self.root / "sha256" / sha256[:2] / sha256

    def put_file(
        self,
        source: Path,
        *,
        name: str,
        media_type: str,
        schema_version: str | None,
    ) -> ArtifactRef:
        source_path = source.expanduser().resolve()
        incoming = self.root / ".incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(dir=incoming, prefix=".artifact.")
        temporary_path = Path(temporary_name)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(file_descriptor, "wb") as target, source_path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            payload_sha256 = digest.hexdigest()
            destination = self._content_path(payload_sha256)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if (
                    not destination.is_file()
                    or destination.stat().st_size != size
                    or _sha256(destination) != payload_sha256
                ):
                    raise ArtifactResolutionError(
                        "cas_object_corrupt",
                        f"existing CAS object is corrupt: {destination}",
                    )
            else:
                os.replace(temporary_path, destination)
        finally:
            temporary_path.unlink(missing_ok=True)
        return ArtifactRef(
            name=name,
            uri=f"artifact://sha256/{payload_sha256}",
            media_type=media_type,
            sha256=payload_sha256,
            bytes=size,
            schema_version=schema_version,
        )

    def resolve(self, ref: ArtifactRef) -> ResolvedArtifact:
        parsed = urlparse(ref.uri)
        if parsed.scheme == "file" and parsed.netloc in {"", "localhost"}:
            path = Path(unquote(parsed.path)).resolve()
        elif parsed.scheme == "artifact" and parsed.netloc == "sha256":
            sha256 = parsed.path.removeprefix("/")
            if sha256 != ref.sha256:
                raise ArtifactResolutionError(
                    "uri_digest_mismatch",
                    "artifact URI digest does not match ArtifactRef.sha256",
                )
            path = self._content_path(sha256)
        else:
            raise ArtifactResolutionError(
                "unsupported_uri",
                f"unsupported artifact URI: {ref.uri}",
            )
        if not path.is_file():
            raise ArtifactResolutionError("not_found", f"artifact not found: {path}")
        if path.stat().st_size != ref.bytes:
            raise ArtifactResolutionError(
                "byte_count_mismatch",
                "artifact byte count does not match ArtifactRef.bytes",
            )
        if _sha256(path) != ref.sha256:
            raise ArtifactResolutionError(
                "sha256_mismatch",
                "artifact content does not match ArtifactRef.sha256",
            )
        return ResolvedArtifact(ref=ref, path=path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
