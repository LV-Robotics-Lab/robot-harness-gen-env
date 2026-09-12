"""Fresh camera evidence with explicit model subprocess double, not a real diagnosis run."""

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from self_improving.harness.x2env.assessment import ASSERTIONS_SHA256
from self_improving.harness.x2env.codex import CodexBackend
from self_improving.harness.x2env.contracts import SceneIR
from self_improving.harness.x2env.genesis_runtime import RuntimeEntity, RuntimeScene
from tests.self_improving.harness.x2env.test_codex import executable
from tests.self_improving.harness.x2env.test_resolver import inputs


def evidence(tmp_path, age=0):
    from self_improving.harness.x2env.diagnosis import FreshObservation, ObservationFrame

    store, _, version, scene_ref, image = inputs(tmp_path)
    scene = RuntimeScene(
        seed=1,
        scene_ir_sha256=scene_ref.sha256,
        entities=(
            RuntimeEntity(
                id="box",
                kind="rigid",
                category="box",
                position_m=(0.0, 0.0, 0.05),
                orientation_wxyz=(1.0, 0.0, 0.0, 0.0),
                urdf_path="asset.urdf",
                physics_path="physics.json",
                version_sha256=version.version_sha256,
            ),
        ),
    )
    runtime = store.write_artifact(scene.model_dump_json().encode(), "application/json")
    captured = (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()
    media = store.write_artifact(
        json.dumps(
            {
                "frames": [
                    {"path": "frames/one.png", "png_sha256": image.sha256, "captured_at": captured}
                ]
            }
        ).encode(),
        "application/json",
    )
    replay = store.write_artifact(
        json.dumps(
            {
                "scene": scene.model_dump(mode="json"),
                "profiles": [
                    {
                        "profile": "baseline",
                        "files": [
                            {"path": "media.json", "artifact": media.model_dump(mode="json")},
                            {"path": "frames/one.png", "artifact": image.model_dump(mode="json")},
                        ],
                    }
                ],
            }
        ).encode(),
        "application/json",
    )
    observation = FreshObservation(
        scene_ir=scene_ref,
        runtime_scene=runtime,
        replay_receipt=replay,
        frames=(
            ObservationFrame(image=image, media_ref=media, frame_index=0, captured_at=captured),
        ),
    )
    physical = store.write_artifact(
        json.dumps(
            {
                "schema_version": "x2env.physical_assessment.v1",
                "scene_ir": scene_ref.model_dump(mode="json"),
                "runtime_scene": runtime.model_dump(mode="json"),
                "replay_receipt": replay.model_dump(mode="json"),
                "input_sha256": SceneIR.model_validate_json(
                    store.read_artifact(scene_ref)
                ).input_sha256,
                "scene_sha256": hashlib.sha256(
                    json.dumps(
                        scene.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
                "physical_status": "failed",
                "checks": [],
                "assertions_sha256": ASSERTIONS_SHA256,
            }
        ).encode(),
        "application/json",
    )
    return store, scene_ref, observation, physical


def test_fresh_diagnosis_preserves_physics_as_separate_evidence(tmp_path):
    store, scene, observation, physics = evidence(tmp_path)
    answer = {
        "base_revision": 0,
        "visual_intent": "passed",
        "reason": "unit double",
        "evidence_sha256": [physics.sha256],
        "scene_patches": [],
        "asset_patches": [],
    }
    path = executable(tmp_path, answer)
    result = CodexBackend(
        path, hashlib.sha256(path.read_bytes()).hexdigest(), "test-double", store
    ).assess_and_diagnose(scene, observation, physics, output_root=tmp_path / "model", timeout=10)
    assert result.status == "completed" and result.proposal.visual_intent == "passed"
    receipt = json.loads(store.read_artifact(result.receipt))
    assert receipt["physical_status"] == "failed"
    assert receipt["authority"] == "advisory_only"


@pytest.mark.parametrize(
    "fault",
    ["stale", "future", "renewed_wrapper", "physics_binding", "frame_binding", "assertions"],
)
def test_untrusted_or_stale_observation_rejected_before_model(tmp_path, fault):
    store, scene, observation, physics = evidence(
        tmp_path,
        age=301 if fault in {"stale", "renewed_wrapper"} else -60 if fault == "future" else 0,
    )
    if fault == "renewed_wrapper":
        observation = observation.model_copy(
            update={
                "frames": (
                    observation.frames[0].model_copy(
                        update={"captured_at": datetime.now(timezone.utc).isoformat()}
                    ),
                )
            }
        )
    if fault in {"physics_binding", "assertions"}:
        report = json.loads(store.read_artifact(physics))
        report["scene_sha256" if fault == "physics_binding" else "assertions_sha256"] = "0" * 64
        physics = store.write_artifact(json.dumps(report).encode(), "application/json")
    if fault == "frame_binding":
        observation = observation.model_copy(
            update={"frames": (observation.frames[0].model_copy(update={"frame_index": 1}),)}
        )
    path = executable(tmp_path, {})
    result = CodexBackend(
        path, hashlib.sha256(path.read_bytes()).hexdigest(), "test-double", store
    ).assess_and_diagnose(scene, observation, physics, output_root=tmp_path / "model", timeout=10)
    assert result.status == "failed" and result.proposal is None
    assert not (tmp_path / "model" / "process.json").exists()


@pytest.mark.parametrize("fault", ["revision", "unknown_ref", "asset_version", "threshold", "tool"])
def test_model_cannot_rebind_evidence_or_grant_authority(tmp_path, fault):
    store, scene, observation, physics = evidence(tmp_path)
    answer = {
        "base_revision": 0,
        "visual_intent": "failed",
        "reason": "explicit double",
        "evidence_sha256": [physics.sha256],
        "scene_patches": [],
        "asset_patches": [],
    }
    if fault == "revision":
        answer["base_revision"] = 9
    if fault == "unknown_ref":
        answer["evidence_sha256"] = ["0" * 64]
    if fault == "threshold":
        answer["success_threshold"] = 1000
    if fault == "asset_version":
        answer["asset_patches"] = [
            {"entity_id": "box", "parent_version": "0" * 64, "patch": {"mass": 0.2}}
        ]
    path = executable(tmp_path, answer, tool=fault == "tool")
    result = CodexBackend(
        path, hashlib.sha256(path.read_bytes()).hexdigest(), "test-double", store
    ).assess_and_diagnose(scene, observation, physics, output_root=tmp_path / "model", timeout=10)
    assert result.status == "failed" and result.proposal is None
    assert (tmp_path / "model" / "process.json").exists()


def test_scene_and_asset_patch_suggestions_are_bound_but_not_executed(tmp_path):
    store, scene, observation, physics = evidence(tmp_path)
    runtime = RuntimeScene.model_validate_json(store.read_artifact(observation.runtime_scene))
    answer = {
        "base_revision": 0,
        "visual_intent": "failed",
        "reason": "explicit test suggestion",
        "evidence_sha256": [physics.sha256],
        "scene_patches": [
            {
                "entity_id": "box",
                "pose": {"frame": "world", "position": [0.1, None, None], "yaw_degrees": 0.0},
                "joints": [],
            }
        ],
        "asset_patches": [
            {
                "entity_id": "box",
                "parent_version": runtime.entities[0].version_sha256,
                "patch": {"mass": 0.2},
            }
        ],
    }
    before = store.read_artifact(scene)
    path = executable(tmp_path, answer)
    result = CodexBackend(
        path, hashlib.sha256(path.read_bytes()).hexdigest(), "test-double", store
    ).assess_and_diagnose(scene, observation, physics, output_root=tmp_path / "model", timeout=10)
    assert result.status == "completed"
    assert result.proposal.scene_patches[0].pose.position == (0.1, None, None)
    assert result.proposal.asset_patches[0].parent_version == runtime.entities[0].version_sha256
    assert store.read_artifact(scene) == before
