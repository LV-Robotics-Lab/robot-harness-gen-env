from __future__ import annotations

from pathlib import Path

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.schemas.common import ArtifactRef

try:
    from self_improving.harness.x2env_compile_closure import (
        ExactReuseCompileClosureVerifier,
        VerifiedExactReuseCompileClosure,
        X2EnvCompileClosureError,
    )
except ImportError:
    ExactReuseCompileClosureVerifier = None  # type: ignore[assignment,misc]
    VerifiedExactReuseCompileClosure = None  # type: ignore[assignment,misc]
    X2EnvCompileClosureError = None  # type: ignore[assignment,misc]


def _ref(digest: str = "1" * 64) -> ArtifactRef:
    return ArtifactRef(
        name="runtime-lock",
        uri=f"artifact://sha256/{digest}",
        media_type="application/json",
        sha256=digest,
        bytes=37,
        schema_version="harness.genesis_runtime_lock.v1",
    )


def test_exact_reuse_compile_closure_public_seam_is_frozen(tmp_path: Path) -> None:
    assert ExactReuseCompileClosureVerifier is not None, "deep compile verifier is not implemented"
    assert VerifiedExactReuseCompileClosure is not None
    assert X2EnvCompileClosureError is not None

    verifier = ExactReuseCompileClosureVerifier(
        artifact_store=LocalArtifactStore(tmp_path / "cas"),
        max_source_bytes=1024,
        max_decoded_pixels=4096,
        expected_implementation_sha256="2" * 64,
        trusted_qualification_issuer_sha256="3" * 64,
        expected_runtime_lock=_ref(),
    )
    assert verifier.expected_runtime_lock == _ref()
    with pytest.raises(TypeError, match="compile_input"):
        verifier.verify(object(), object())
