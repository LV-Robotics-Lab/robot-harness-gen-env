"""A copied receipt must not hide missing evidence in its referenced source graph."""

import json

import pytest

from self_improving.harness.x2env.store import Store


def test_receipt_graph_requires_all_referenced_bytes_not_just_root(tmp_path):
    from self_improving.harness.x2env.artifacts import artifact_closure

    old, new = Store(tmp_path / "source"), Store(tmp_path / "target")
    data = old.write_artifact(b"licensed source bytes", "application/octet-stream")
    raw = json.dumps(
        {"candidate": {"source": data.model_dump()}, "unrelated_hash": "f" * 64}
    ).encode()
    root = new.write_artifact(raw, "application/json")
    with pytest.raises(FileNotFoundError):
        artifact_closure(new, (root,))
    new.write_artifact(old.read_artifact(data), data.media_type)
    assert set(artifact_closure(new, (root,))) == {root, data}
    with pytest.raises(ValueError, match="budget"):
        artifact_closure(new, (root,), max_refs=1)
    with pytest.raises(ValueError, match="budget"):
        artifact_closure(new, (root,), max_bytes=1)
