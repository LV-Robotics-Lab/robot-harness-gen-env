"""Revision invalidates downstream state atomically through the public journal seam."""

import pytest

from self_improving.harness.x2env.contracts import ToolResult, X2EnvRequest
from self_improving.harness.x2env.store import Store


def test_package_pointer_requires_its_successful_materialization_operation(tmp_path):
    store = Store(tmp_path)
    snapshot = store.claim(
        store.submit(
            X2EnvRequest(
                text="a table", seed=1, idempotency_key="package", output_dir=str(tmp_path / "out")
            )
        ).workflow_id
    )
    ref = store.write_artifact(b"test package metadata", "text/plain")
    with pytest.raises(ValueError, match="package checkpoint"):
        store.complete_operation(
            snapshot,
            ToolResult(
                operation_id=snapshot.operations[-1].operation_id,
                status="succeeded",
                outputs=(ref,),
            ),
            ref,
            status="succeeded",
            environment_package=ref,
        )
    snapshot = store.complete_operation(
        snapshot,
        ToolResult(
            operation_id=snapshot.operations[-1].operation_id, status="succeeded", outputs=(ref,)
        ),
        ref,
        status="active",
    )
    snapshot = store.begin_operation(snapshot, "package.materialize")
    final = store.complete_operation(
        snapshot,
        ToolResult(
            operation_id=snapshot.operations[-1].operation_id, status="succeeded", outputs=(ref,)
        ),
        ref,
        status="succeeded",
        environment_package=ref,
    )
    assert final.environment_package == ref
    assert store.status(final.workflow_id) == final and final.owner is None


def test_committed_revision_clears_old_replay_observation_and_validation(tmp_path):
    store = Store(tmp_path)
    snapshot = store.claim(
        store.submit(
            X2EnvRequest(
                text="a scene",
                seed=1,
                idempotency_key="revision",
                output_dir=str(tmp_path / "output"),
            )
        ).workflow_id
    )
    old = store.write_artifact(b"old test artifact", "text/plain")
    new = store.write_artifact(b"new test artifact", "text/plain")
    snapshot = store.complete_operation(
        snapshot,
        ToolResult(
            operation_id=snapshot.operations[-1].operation_id, status="succeeded", outputs=(old,)
        ),
        old,
        status="active",
        scene_ir=old,
        resolved_assets=old,
        compiled_scene=old,
        replay_result=old,
        observation=old,
        diagnosis=old,
        validation=old,
    )
    snapshot = store.begin_operation(snapshot, "revise")
    result = ToolResult(
        operation_id=snapshot.operations[-1].operation_id, status="succeeded", outputs=(new,)
    )
    revised = store.complete_operation(
        snapshot,
        result,
        old,
        status="active",
        scene_ir=new,
        resolved_assets=new,
        revision_receipt=new,
    )
    assert revised.revisions == (new,)
    assert revised.scene_ir == new and revised.resolved_assets == new
    assert all(
        getattr(revised, name) is None
        for name in ("compiled_scene", "replay_result", "observation", "diagnosis", "validation")
    )
    assert store.status(revised.workflow_id) == revised
    assert store.read_artifact(old) == b"old test artifact"
    other = store.begin_operation(revised, "observe")
    with pytest.raises(ValueError, match="revision checkpoint"):
        store.complete_operation(
            other,
            ToolResult(
                operation_id=other.operations[-1].operation_id, status="succeeded", outputs=(old,)
            ),
            old,
            status="active",
            scene_ir=old,
            resolved_assets=old,
            revision_receipt=old,
        )
