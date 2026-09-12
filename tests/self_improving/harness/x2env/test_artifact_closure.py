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


@pytest.mark.parametrize(
    "limits", [{"max_bytes": 0}, {"max_bytes": True}, {"max_refs": 0}, {"max_refs": 1.5}]
)
def test_invalid_graph_budgets_are_not_unbounded(tmp_path, limits):
    from self_improving.harness.x2env.artifacts import artifact_closure

    with pytest.raises(ValueError, match="budget"):
        artifact_closure(Store(tmp_path / "store"), (), **limits)


def test_plain_hash_is_not_a_trusted_root(tmp_path):
    from self_improving.harness.x2env.artifacts import artifact_closure

    with pytest.raises(TypeError, match="typed"):
        artifact_closure(Store(tmp_path / "store"), ("a" * 64,))


def test_nested_list_refs_and_duplicate_media_views_charge_bytes_once(tmp_path):
    from self_improving.harness.x2env.artifacts import artifact_closure

    store = Store(tmp_path / "store")
    text = store.write_artifact(b"null", "text/plain")
    document = store.write_artifact(b"null", "application/json")
    raw = json.dumps(
        {"files": [{"path": "a", **text.model_dump()}, document.model_dump(), text.model_dump()]}
    ).encode()
    root = store.write_artifact(raw, "application/json")
    closure = artifact_closure(store, (root, root), max_bytes=len(raw) + 4)
    assert set(closure) == {root, text, document}
    assert len(closure) == 3


def test_excessive_json_work_is_bounded_even_without_asset_refs(tmp_path):
    from self_improving.harness.x2env.artifacts import artifact_closure

    store = Store(tmp_path / "store")
    root = store.write_artifact(json.dumps([0] * 200001).encode(), "application/json")
    with pytest.raises(ValueError, match="node budget"):
        artifact_closure(store, (root,))
