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
