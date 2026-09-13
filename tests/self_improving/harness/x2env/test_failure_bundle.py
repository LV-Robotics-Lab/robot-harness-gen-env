"""Failed/blocked workflow outputs are reviewable without pretending an environment exists."""

import json

import pytest

from self_improving.harness.x2env.contracts import X2EnvRequest
from self_improving.harness.x2env.harness import Harness


def test_missing_backend_produces_failure_bundle_with_original_input_and_no_scene(tmp_path):
    harness = Harness(tmp_path / "state")
    handle = harness.submit(
        X2EnvRequest(
            text="a red mouse",
            seed=11,
            idempotency_key="failure",
            output_dir=str(tmp_path / "output"),
        )
    )
    snapshot = harness.resume(handle.workflow_id)
    result = harness.package(handle.workflow_id, output=tmp_path / "review")
    assert result["status"] == "blocked"
    assert result["environment_package"] is None
    assert result["scene_ir"] == {"status": "not_produced"}
    root = tmp_path / "review"
    manifest = json.loads((root / "failure/manifest.json").read_text())
    assert manifest["workflow_id"] == handle.workflow_id
    assert any(
        (root / "failure" / row["path"]).read_bytes() == b"a red mouse"
        for row in manifest["members"]
    )
    assert harness.status(handle.workflow_id) == snapshot
    with pytest.raises(FileExistsError):
        harness.package(handle.workflow_id, output=root)
    assert harness.package(handle.workflow_id, output=root, reuse_existing=True) == result
    (root / "failure/error.json").write_text("changed")
    with pytest.raises(ValueError, match="existing failure bundle differs"):
        harness.package(handle.workflow_id, output=root, reuse_existing=True)
    assert not (root / "environment").exists()


@pytest.mark.parametrize("member", ["failure/manifest.json", "result.json", "failure/error.json"])
@pytest.mark.parametrize("symlink", [False, True])
def test_failure_bundle_reuse_rejects_changed_or_symbolic_members(tmp_path, member, symlink):
    harness = Harness(tmp_path / "state")
    handle = harness.submit(
        X2EnvRequest(
            text="a red mouse",
            seed=11,
            idempotency_key="tampered-failure",
            output_dir=str(tmp_path / "output"),
        )
    )
    snapshot = harness.resume(handle.workflow_id)
    root = tmp_path / "review"
    harness.package(handle.workflow_id, output=root)
    target = root / member
    if symlink:
        original = tmp_path / "original-member"
        target.rename(original)
        target.symlink_to(original)
    else:
        target.write_text("changed")
    with pytest.raises(ValueError, match="existing failure bundle differs|unsafe failure member"):
        harness.package(handle.workflow_id, output=root, reuse_existing=True)
    assert harness.status(handle.workflow_id) == snapshot
    assert target.is_symlink() if symlink else target.read_text() == "changed"


@pytest.mark.parametrize("output_kind", ["relative", "symbolic_root", "symbolic_ancestor"])
def test_failure_bundle_rejects_unsafe_destination_before_writing(tmp_path, output_kind):
    from self_improving.harness.x2env.failure_bundle import materialize_failure
    from self_improving.harness.x2env.store import Store

    harness = Harness(tmp_path / "state")
    handle = harness.submit(
        X2EnvRequest(
            text="a mouse",
            seed=11,
            idempotency_key="unsafe-failure",
            output_dir=str(tmp_path / "output"),
        )
    )
    snapshot = harness.resume(handle.workflow_id)
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    output = "relative-review" if output_kind == "relative" else alias
    if output_kind == "symbolic_ancestor":
        output = alias / "nested"
    with pytest.raises(ValueError, match="absolute non-symbolic"):
        materialize_failure(snapshot, Store(tmp_path / "state"), output)
    assert list(real.iterdir()) == []


def test_active_workflow_cannot_be_exported_as_failure(tmp_path):
    from self_improving.harness.x2env.failure_bundle import materialize_failure
    from self_improving.harness.x2env.store import Store

    harness = Harness(tmp_path / "state")
    handle = harness.submit(
        X2EnvRequest(
            text="a mouse",
            seed=11,
            idempotency_key="active-not-failure",
            output_dir=str(tmp_path / "output"),
        )
    )
    with pytest.raises(ValueError, match="requires stopped workflow"):
        materialize_failure(
            harness.status(handle.workflow_id), Store(tmp_path / "state"), tmp_path / "review"
        )
    assert not (tmp_path / "review").exists()
