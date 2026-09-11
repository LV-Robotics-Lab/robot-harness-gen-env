import pytest


def test_fresh_capture_rejects_an_untyped_request_before_execution(tmp_path):
    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_scene_fresh_capture_launcher import (
        execute_genesis_fresh_capture,
    )

    source = tmp_path / "request.json"
    source.write_text("{}")
    store = LocalArtifactStore(tmp_path / "cas")
    ref = store.put_file(
        source, name=source.name, media_type="application/json", schema_version=None
    )
    with pytest.raises(ValueError, match="exact typed"):
        execute_genesis_fresh_capture(store=store, request_ref=ref, roots={})


def test_missing_runtime_retains_failure_index_without_execution(tmp_path):
    import json
    from uuid import uuid4

    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_scene_fresh_capture_launcher import (
        GenesisFreshCaptureError,
        execute_genesis_fresh_capture,
    )
    from self_improving.harness.schemas.genesis_fresh_capture import GenesisFreshCaptureRequestV1
    from tests.self_improving.harness.test_genesis_scene_execution import reference

    rows = reference("harness.genesis_scene_trace_row.v1")
    rows["media_type"] = "application/x-ndjson"
    request = GenesisFreshCaptureRequestV1(
        schema_version="harness.genesis_fresh_capture_request.v1",
        replay_run_id=uuid4(),
        replay_receipt=reference("harness.genesis_scene_execution_receipt.v2"),
        scene=reference("harness.genesis_scene_spec.v1"),
        half_dt_rows=rows,
        final_state={
            "position": [0, 0, 1],
            "orientation_wxyz": [1, 0, 0, 0],
            "velocity": [0, 0, 0],
            "angular_velocity": [0, 0, 0],
        },
        source_step=2000,
        source_time_s=4.0,
        capture_runtime_manifest=reference("harness.genesis_runtime_manifest.v1"),
        restoration="restored_final_pose_zero_velocity",
    )
    source = tmp_path / "request.json"
    source.write_text(request.model_dump_json())
    store = LocalArtifactStore(tmp_path / "cas")
    ref = store.put_file(
        source,
        name=source.name,
        media_type="application/json",
        schema_version=request.schema_version,
    )
    with pytest.raises(GenesisFreshCaptureError) as raised:
        execute_genesis_fresh_capture(store=store, request_ref=ref, roots={})
    assert len(raised.value.failure_artifacts) == 1
    retained = json.loads(store.resolve(raised.value.failure_artifacts[0]).path.read_bytes())
    assert retained == {"files": [], "qualified": False, "execution_authority": False}
