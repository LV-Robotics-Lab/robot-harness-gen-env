"""Artifact persistence seam: immutable content identities, verified on read."""

import pytest

from self_improving.harness.x2env.store import Store


def test_artifacts_are_reused_across_restart_and_verified_on_read(tmp_path):
    store = Store(tmp_path)
    ref = store.write_artifact(b"abc", "text/plain")
    assert ref.sha256 == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert ref.size_bytes == 3
    restarted = Store(tmp_path)
    assert restarted.write_artifact(b"abc", "text/plain") == ref
    assert restarted.read_artifact(ref) == b"abc"
    path = tmp_path / "cas" / ref.sha256[:2] / ref.sha256
    path.write_bytes(b"def")
    with pytest.raises(ValueError, match="integrity"):
        restarted.read_artifact(ref)
    with pytest.raises(ValueError, match="integrity"):
        restarted.write_artifact(b"abc", "text/plain")
