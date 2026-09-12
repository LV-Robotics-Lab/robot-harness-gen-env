"""Delivery with real CAS/package verification; synthetic runtime is not qualification."""

from pathlib import Path

import pytest

from self_improving.harness.x2env.completion import materialize_completion
from self_improving.harness.x2env.contracts import ToolResult
from tests.self_improving.harness.x2env.test_completion import completed_fixture


def finished(tmp_path):
    store, snapshot = completed_fixture(tmp_path)
    snapshot = store.begin_operation(snapshot, "package.materialize")
    completion = materialize_completion(snapshot, store, tmp_path / "internal")
    assert completion.status == "materialized"
    ref = store.write_artifact(completion.model_dump_json().encode(), "application/json")
    snapshot = store.complete_operation(
        snapshot,
        ToolResult(
            operation_id=snapshot.operations[-1].operation_id, status="succeeded", outputs=(ref,)
        ),
        snapshot.input_bundle,
        status="succeeded",
        environment_package=ref,
    )
    return store, snapshot, completion


def test_export_copies_only_manifest_members_and_reuses_readonly(tmp_path):
    from self_improving.harness.x2env.delivery import export_completion

    store, snapshot, completion = finished(tmp_path)
    source = Path(completion.package_path)
    direct = export_completion(snapshot, store)
    assert direct["environment_package"] == str(source)
    (source / "not-a-member.txt").write_text("must not copy")
    result = export_completion(snapshot, store, tmp_path / "export")
    assert result["environment_package"] == str(tmp_path / "export" / "environment")
    assert result["physical_profile"] == "passed" and result["visual_intent"] == "passed"
    assert result["copy_run"] == "not_run" and result["sim_ready"] is False
    assert result["images"] and result["videos"]
    assert not (tmp_path / "export" / "environment" / "not-a-member.txt").exists()
    before = {str(p): p.read_bytes() for p in (tmp_path / "export").rglob("*") if p.is_file()}
    assert export_completion(snapshot, store, tmp_path / "export", reuse_existing=True) == result
    assert before == {
        str(p): p.read_bytes() for p in (tmp_path / "export").rglob("*") if p.is_file()
    }
    assert store.status(snapshot.workflow_id) == snapshot


@pytest.mark.parametrize("fault", ["source", "destination", "uncommitted", "manifest", "symlink"])
def test_export_rejects_drift_without_overwriting(tmp_path, fault):
    from self_improving.harness.x2env.delivery import export_completion

    store, snapshot, completion = finished(tmp_path)
    if fault == "uncommitted":
        with pytest.raises(ValueError):
            export_completion(
                snapshot.model_copy(update={"environment_package": snapshot.validation}), store
            )
        return
    output = tmp_path / "export"
    export_completion(snapshot, store, output)
    if fault == "manifest":
        target = Path(completion.package_path) / "manifest.json"
        target.write_bytes(target.read_bytes() + b"\n")
        with pytest.raises(ValueError, match="completion_manifest_mismatch"):
            export_completion(snapshot, store)
        return
    if fault == "symlink":
        (output / "extra-link").symlink_to(Path(completion.package_path) / "scene.json")
        with pytest.raises(ValueError, match="delivery_existing_members_differ"):
            export_completion(snapshot, store, output, reuse_existing=True)
        assert (output / "extra-link").is_symlink()
        return
    target = (
        Path(completion.package_path) if fault == "source" else output / "environment"
    ) / "scene.json"
    target.write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        export_completion(snapshot, store, output, reuse_existing=True)
    assert target.read_bytes() == b"corrupt"
