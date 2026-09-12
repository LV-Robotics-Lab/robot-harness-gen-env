"""Observe uses stored frame bytes and original timestamps, never new wrapper time."""

import json

import pytest

from self_improving.harness.x2env.replay import ReplayFile, ReplayProfile, ReplayResult
from tests.self_improving.harness.x2env.test_diagnosis import evidence


def test_observe_keeps_camera_time_and_reports_missing_physical_evidence(tmp_path):
    from self_improving.harness.x2env.observation import observe_replay

    store, scene_ir, original, _ = evidence(tmp_path)
    recorded = json.loads(store.read_artifact(original.replay_receipt))
    raw = recorded["profiles"][0]
    replay = ReplayResult(
        status="succeeded",
        receipt=original.replay_receipt,
        error_code=None,
        profiles=(
            ReplayProfile(
                profile="baseline",
                root=str(tmp_path / "missing"),
                status="passed",
                files=tuple(ReplayFile.model_validate(row) for row in raw["files"]),
            ),
        ),
    )
    receipt = store.write_artifact(
        json.dumps(
            {
                "scene": recorded["scene"],
                "status": "succeeded",
                "profiles": [p.model_dump(mode="json") for p in replay.profiles],
            }
        ).encode(),
        "application/json",
    )
    replay = replay.model_copy(update={"receipt": receipt})
    result = observe_replay(store, scene_ir, original.runtime_scene, replay, package_root=tmp_path)
    assert result.observation.frames == original.frames
    report = json.loads(store.read_artifact(result.physics_report))
    assert report["physical_status"] == "not_run"
    assert report["error_code"] == "incomplete_dual_profile"
    assert report["scene_ir"] == scene_ir.model_dump(mode="json")
    assert result.observation.replay_receipt == receipt
    changed = replay.model_copy(
        update={
            "profiles": (replay.profiles[0].model_copy(update={"root": str(tmp_path / "other")}),)
        }
    )
    with pytest.raises(ValueError, match="replay profile binding"):
        observe_replay(store, scene_ir, original.runtime_scene, changed, package_root=tmp_path)
