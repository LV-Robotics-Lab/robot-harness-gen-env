"""Observe uses stored frame bytes and original timestamps, never new wrapper time."""

import json

import pytest

from self_improving.harness.x2env.replay import ReplayFile, ReplayProfile, ReplayResult
from tests.self_improving.harness.x2env.test_diagnosis import evidence


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("failed_replay", "bound completed replay"),
        ("foreign_scene", "bound completed replay"),
        ("receipt_scene", "replay scene differs"),
        ("receipt_status", "replay profile binding differs"),
        ("duplicate_profile", "duplicate replay profile"),
        ("duplicate_member", "duplicate replay member"),
        ("missing_media", "missing replay camera observation"),
        ("image_digest", "camera image differs"),
    ],
)
def test_observation_rejects_rebound_or_incomplete_camera_evidence(tmp_path, fault, reason):
    from self_improving.harness.x2env.observation import observe_replay

    store, scene_ir, original, _ = evidence(tmp_path)
    payload = json.loads(store.read_artifact(original.replay_receipt))
    files = tuple(ReplayFile.model_validate(row) for row in payload["profiles"][0]["files"])
    if fault == "duplicate_member":
        files = (*files, files[0])
    if fault == "missing_media":
        files = tuple(row for row in files if row.path != "media.json")
    if fault == "image_digest":
        media = json.loads(store.read_artifact(files[0].artifact))
        media["frames"][0]["png_sha256"] = "0" * 64
        ref = store.write_artifact(json.dumps(media).encode(), "application/json")
        files = (ReplayFile(path="media.json", artifact=ref), files[1])
    profile = ReplayProfile(
        profile="baseline", root=str(tmp_path / "missing"), status="passed", files=files
    )
    profiles = (profile, profile) if fault == "duplicate_profile" else (profile,)
    runtime = original.runtime_scene
    if fault == "foreign_scene":
        raw = json.loads(store.read_artifact(runtime))
        raw["scene_ir_sha256"] = "0" * 64
        runtime = store.write_artifact(json.dumps(raw).encode(), "application/json")
    payload.update(status="succeeded", profiles=[p.model_dump(mode="json") for p in profiles])
    if fault == "receipt_scene":
        payload["scene"]["seed"] += 1
    if fault == "receipt_status":
        payload["status"] = "failed"
    receipt = store.write_artifact(json.dumps(payload).encode(), "application/json")
    replay = ReplayResult(
        status="failed" if fault == "failed_replay" else "succeeded",
        profiles=profiles,
        receipt=receipt,
        error_code=None,
    )
    with pytest.raises(ValueError, match=reason):
        observe_replay(store, scene_ir, runtime, replay, package_root=tmp_path)


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
