"""Public recovery and completion; synthetic producer evidence, not qualification."""

from self_improving.harness.x2env.harness import Harness
from tests.self_improving.harness.x2env.test_completion import completed_fixture


def test_resume_materializes_validated_checkpoint_without_replay(tmp_path):
    store, snapshot = completed_fixture(tmp_path)
    stopped = store.complete_operation(
        snapshot,
        None,
        snapshot.input_bundle,
        status="blocked",
        reason="environment_package_materializer",
    )
    harness = Harness(store.database.parent)
    final = harness.resume(stopped.workflow_id)
    assert final.status == "succeeded", final.stop_reason
    assert final.environment_package is not None
    assert final.operations[-1].capability == "package.materialize"
    assert final.operations[-1].status == "succeeded"
    assert [op for op in final.operations if op.capability == "x2env.replay"] == [
        op for op in stopped.operations if op.capability == "x2env.replay"
    ]
    assert harness.resume(final.workflow_id) == final
    exported = harness.package(final.workflow_id, output=tmp_path / "exported")
    assert exported["status"] == "succeeded" and exported["copy_run"] == "not_run"
    assert exported["images"] and exported["videos"]
    assert (
        harness.package(final.workflow_id, output=tmp_path / "exported", reuse_existing=True)
        == exported
    )
