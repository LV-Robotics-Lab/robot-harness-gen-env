"""Observe uses stored frame bytes and original timestamps, never new wrapper time."""

import json

import pytest

from self_improving.harness.x2env.replay import ReplayFile, ReplayProfile, ReplayResult
from tests.self_improving.harness.x2env.test_diagnosis import evidence


def dynamic_replay_fixture(tmp_path):
    """Real compiler assets and synthetic camera-only replay, explicitly no physics pass."""
    from self_improving.harness.x2env.compile import StructuralPolicy, compile_scene
    from tests.self_improving.harness.x2env.test_compile import dynamic_stack_inputs

    camera_root = tmp_path / "camera"
    camera_root.mkdir()
    camera_store, _, original, _ = evidence(camera_root)
    source = tmp_path / "dynamic"
    source.mkdir()
    store, registry, _, ref, assets = dynamic_stack_inputs(source)
    compiled = compile_scene(
        ref,
        assets,
        registry=registry,
        store=store,
        output_root=source / "compiled",
        policy=StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5),
        seed=11,
    )
    frame = original.frames[0]
    for artifact in [frame.image, frame.media_ref]:
        assert (
            store.write_artifact(camera_store.read_artifact(artifact), artifact.media_type)
            == artifact
        )
    runtime = store.write_artifact(
        compiled.runtime_scene.model_dump_json().encode(), "application/json"
    )
    profile = ReplayProfile(
        profile="baseline",
        root=str(tmp_path / "missing-runtime"),
        status="passed",
        files=(
            ReplayFile(path="media.json", artifact=frame.media_ref),
            ReplayFile(path="frames/one.png", artifact=frame.image),
        ),
    )
    receipt = store.write_artifact(
        json.dumps(
            {
                "scene": compiled.runtime_scene.model_dump(mode="json"),
                "status": "succeeded",
                "profiles": [profile.model_dump(mode="json")],
            }
        ).encode(),
        "application/json",
    )
    replay = ReplayResult(status="succeeded", profiles=(profile,), receipt=receipt, error_code=None)
    return store, compiled.scene_ir, runtime, replay, source / "compiled", frame


def test_dynamic_observation_retains_camera_and_missing_dual_profile_semantics(tmp_path):
    from self_improving.harness.x2env.observation import observe_replay

    store, scene, runtime, replay, package, frame = dynamic_replay_fixture(tmp_path)
    result = observe_replay(store, scene, runtime, replay, package_root=package)
    assert result.observation.frames == (frame,)
    physical = json.loads(store.read_artifact(result.physics_report))
    assert physical["physical_status"] == "not_run"
    assert physical["error_code"] == "incomplete_dual_profile"


@pytest.mark.parametrize("schema", ["x2env.runtime_scene.v1", "x2env.runtime_scene.v999"])
def test_dynamic_observation_rejects_schema_downgrade_and_unknown_version(tmp_path, schema):
    from self_improving.harness.x2env.observation import observe_replay

    store, scene, runtime, replay, package, _ = dynamic_replay_fixture(tmp_path)
    body = json.loads(store.read_artifact(runtime))
    body["schema_version"] = schema
    changed = store.write_artifact(json.dumps(body).encode(), "application/json")
    with pytest.raises(ValueError):
        observe_replay(store, scene, changed, replay, package_root=package)


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
