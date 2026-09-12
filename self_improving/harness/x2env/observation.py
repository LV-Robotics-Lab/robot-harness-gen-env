"""Consume replay evidence into camera observations and independent physical diagnostics."""

import hashlib
import json

from .assessment import assess_scene
from .contracts import ArtifactRef, Model, SceneIR
from .diagnosis import FreshObservation, ObservationFrame
from .genesis_runtime import RuntimeScene
from .replay import ReplayResult


class ObservationResult(Model):
    observation: FreshObservation
    physics_report: ArtifactRef
    receipt: ArtifactRef


def observe_replay(store, scene_ir, runtime_scene, replay, *, package_root):
    """Use the last captured frame of the last profile; do not recapture or renew its time."""
    scene = SceneIR.model_validate_json(store.read_artifact(scene_ir))
    runtime = RuntimeScene.model_validate_json(store.read_artifact(runtime_scene))
    replay = ReplayResult.model_validate_json(replay.model_dump_json())
    if runtime.scene_ir_sha256 != scene_ir.sha256 or replay.status != "succeeded":
        raise ValueError("observation requires a bound completed replay")
    receipt = json.loads(store.read_artifact(replay.receipt))
    if receipt.get("scene") != runtime.model_dump(mode="json"):
        raise ValueError("replay scene differs")
    if receipt.get("status") != replay.status or receipt.get("profiles") != [
        p.model_dump(mode="json") for p in replay.profiles
    ]:
        raise ValueError("replay profile binding differs")
    profiles = {}
    selected = None
    for profile in replay.profiles:
        if profile.profile in profiles:
            raise ValueError("duplicate replay profile")
        files = {item.path: item.artifact for item in profile.files}
        if len(files) != len(profile.files):
            raise ValueError("duplicate replay member")
        for ref in files.values():
            store.read_artifact(ref)
        profiles[profile.profile] = {
            "root": profile.root,
            "files": [
                {"path": path, "sha256": ref.sha256, "size_bytes": ref.size_bytes}
                for path, ref in files.items()
            ],
        }
        if "media.json" not in files:
            continue
        media = json.loads(store.read_artifact(files["media.json"]))
        row = media["frames"][-1]
        image = files[row["path"]]
        if image.sha256 != row["png_sha256"]:
            raise ValueError("camera image differs")
        selected = ObservationFrame(
            image=image,
            media_ref=files["media.json"],
            frame_index=len(media["frames"]) - 1,
            captured_at=row["captured_at"],
        )
    if selected is None:
        raise ValueError("missing replay camera observation")
    physical = assess_scene(
        runtime,
        package_root=package_root,
        scene_ir_bytes=store.read_artifact(scene_ir),
        input_sha256=scene.input_sha256,
        profiles=profiles,
    )
    physical.update(
        scene_ir=scene_ir.model_dump(mode="json"),
        runtime_scene=runtime_scene.model_dump(mode="json"),
        replay_receipt=replay.receipt.model_dump(mode="json"),
        input_sha256=scene.input_sha256,
        scene_sha256=hashlib.sha256(
            json.dumps(
                runtime.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest(),
    )
    report = store.write_artifact(json.dumps(physical, sort_keys=True).encode(), "application/json")
    observation = FreshObservation(
        scene_ir=scene_ir,
        runtime_scene=runtime_scene,
        replay_receipt=replay.receipt,
        frames=(selected,),
    )
    result = {
        "observation": observation.model_dump(mode="json"),
        "physics_report": report.model_dump(mode="json"),
        "authority": "harness_observation",
        "camera_time_renewed": False,
    }
    ref = store.write_artifact(json.dumps(result, sort_keys=True).encode(), "application/json")
    return ObservationResult(observation=observation, physics_report=report, receipt=ref)
