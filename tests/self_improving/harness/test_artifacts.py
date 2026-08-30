from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import self_improving.harness.artifacts as artifact_module
from self_improving.harness import ArtifactRef, ArtifactResolutionError, LocalArtifactStore


def test_local_artifact_store_round_trips_content_by_digest(tmp_path: Path) -> None:
    source = tmp_path / "asset_catalog.json"
    payload = b'{"schema_version":"robotwin.asset_catalog.v1"}\n'
    source.write_bytes(payload)

    store = LocalArtifactStore(tmp_path / "artifacts")
    artifact = store.put_file(
        source,
        name="asset_catalog",
        media_type="application/json",
        schema_version="robotwin.asset_catalog.v1",
    )
    resolved = store.resolve(artifact)
    reused = store.put_file(
        source,
        name="same_content_new_name",
        media_type="application/json",
        schema_version="robotwin.asset_catalog.v1",
    )

    assert artifact.sha256 == hashlib.sha256(payload).hexdigest()
    assert artifact.uri == f"artifact://sha256/{artifact.sha256}"
    assert artifact.bytes == len(payload)
    assert resolved.path.read_bytes() == payload
    assert resolved.ref == artifact
    assert reused.sha256 == artifact.sha256
    assert reused.uri == artifact.uri


def test_resolver_accepts_a_verified_file_uri_for_external_input(tmp_path: Path) -> None:
    source = tmp_path / "catalog.json"
    payload = b"{}\n"
    source.write_bytes(payload)
    ref = ArtifactRef(
        name="catalog",
        uri=source.resolve().as_uri(),
        media_type="application/json",
        sha256=hashlib.sha256(payload).hexdigest(),
        bytes=len(payload),
        schema_version="robotwin.asset_catalog.v1",
    )

    resolved = LocalArtifactStore(tmp_path / "artifacts").resolve(ref)

    assert resolved.path == source.resolve()


def test_resolver_fails_closed_for_locator_and_content_mismatches(tmp_path: Path) -> None:
    source = tmp_path / "payload.json"
    source.write_bytes(b"{}\n")
    store = LocalArtifactStore(tmp_path / "artifacts")
    ref = store.put_file(
        source,
        name="payload",
        media_type="application/json",
        schema_version="robotwin.asset_catalog.v1",
    )

    bad_uri = ref.model_copy(update={"uri": f"artifact://sha256/{'0' * 64}"})
    with pytest.raises(ArtifactResolutionError) as uri_error:
        store.resolve(bad_uri)
    assert uri_error.value.reason == "uri_digest_mismatch"

    bad_bytes = ref.model_copy(update={"bytes": ref.bytes + 1})
    with pytest.raises(ArtifactResolutionError) as size_error:
        store.resolve(bad_bytes)
    assert size_error.value.reason == "byte_count_mismatch"

    store.resolve(ref).path.write_bytes(b"[]\n")
    with pytest.raises(ArtifactResolutionError) as digest_error:
        store.resolve(ref)
    assert digest_error.value.reason == "sha256_mismatch"

    unsupported = ref.model_copy(update={"uri": "https://example.invalid/payload.json"})
    with pytest.raises(ArtifactResolutionError) as scheme_error:
        store.resolve(unsupported)
    assert scheme_error.value.reason == "unsupported_uri"

    missing = ref.model_copy(update={"sha256": "f" * 64, "uri": f"artifact://sha256/{'f' * 64}"})
    with pytest.raises(ArtifactResolutionError) as missing_error:
        store.resolve(missing)
    assert missing_error.value.reason == "not_found"


def test_put_file_copies_and_hashes_one_coherent_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "mutable.bin"
    original = b"original artifact bytes"
    source.write_bytes(original)
    real_sha256 = artifact_module._sha256

    def swap_after_pre_hash(path: Path) -> str:
        digest = real_sha256(path)
        if path == source.resolve():
            source.write_bytes(b"bytes swapped after the pre-hash")
        return digest

    monkeypatch.setattr(artifact_module, "_sha256", swap_after_pre_hash)
    store = LocalArtifactStore(tmp_path / "artifacts")

    artifact = store.put_file(
        source,
        name="mutable",
        media_type="application/octet-stream",
        schema_version=None,
    )

    assert store.resolve(artifact).path.read_bytes() == original
    assert artifact.sha256 == hashlib.sha256(original).hexdigest()
    assert artifact.bytes == len(original)


def test_put_file_rejects_an_existing_corrupt_cas_object(tmp_path: Path) -> None:
    source = tmp_path / "payload.bin"
    source.write_bytes(b"trusted bytes")
    store = LocalArtifactStore(tmp_path / "artifacts")
    artifact = store.put_file(
        source,
        name="payload",
        media_type="application/octet-stream",
        schema_version=None,
    )
    store.resolve(artifact).path.write_bytes(b"poisoned CAS")

    with pytest.raises(ArtifactResolutionError) as error:
        store.put_file(
            source,
            name="payload",
            media_type="application/octet-stream",
            schema_version=None,
        )

    assert error.value.reason == "cas_object_corrupt"
