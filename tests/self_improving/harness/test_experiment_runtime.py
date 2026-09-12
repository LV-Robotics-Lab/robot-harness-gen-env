import json

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.experiment_runtime import observe, replay, validate
from self_improving.harness.schemas.common import ArtifactRef
from tests.self_improving.harness.test_genesis_scene_replay import (
    rows,
    scene,  # noqa: F401
)


def test_invalid_scene_returns_failure_receipt_and_retains_input(tmp_path):
    store = LocalArtifactStore(tmp_path / "cas")
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps({"not": "a scene"}))
    reference = store.put_file(
        path, name=path.name, media_type="application/json", schema_version=None
    )
    result = replay(store, reference, tmp_path / "output", {})
    assert result["status"] == "failed"
    assert result["simulator_executed"] is False
    assert result["scene"] == reference.model_dump(mode="json")
    assert store.resolve(ArtifactRef.model_validate(result["receipt"])).path.is_file()


def test_visual_pass_cannot_validate_a_failed_replay(tmp_path):
    store = LocalArtifactStore(tmp_path / "cas")
    path = tmp_path / "invalid.json"
    path.write_text("{}")
    reference = store.put_file(
        path, name=path.name, media_type="application/json", schema_version=None
    )
    result = replay(store, reference, tmp_path / "output", {})
    validation = validate(
        store,
        input_ref=reference,
        scene_ref=reference,
        replay_result=result,
        visual_advisory={"status": "passed"},
    )
    assert validation["status"] == "failed"
    assert validation["physical_status"] == "not_run"
    assert validation["visual_status"] == "passed"


def test_observation_does_not_relabel_an_expired_capture(tmp_path):
    from PIL import Image

    store = LocalArtifactStore(tmp_path / "cas")
    image = tmp_path / "frame.png"
    Image.new("RGB", (2, 2)).save(image)
    ref = store.put_file(image, name=image.name, media_type="image/png", schema_version=None)
    media = tmp_path / "media.json"
    media.write_text(
        json.dumps(
            {"frames": [{"captured_at": "2000-01-01T00:00:00Z", "path": "frames/frame.png"}]}
        )
    )
    media_ref = store.put_file(
        media, name=media.name, media_type="application/json", schema_version=None
    )
    receipt = tmp_path / "replay.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "harness.experiment_replay.v1",
                "status": "passed",
                "simulator_executed": True,
                "scene": ref.model_dump(mode="json"),
                "profiles": {
                    "half_dt": {
                        "members": {
                            "half_dt/media.json": media_ref.model_dump(mode="json"),
                            "half_dt/frames/frame.png": ref.model_dump(mode="json"),
                        }
                    }
                },
            }
        )
    )
    receipt_ref = store.put_file(
        receipt, name=receipt.name, media_type="application/json", schema_version=None
    )
    result = observe(store, receipt_ref)
    assert result["status"] == "failed"
    assert result["error_code"] == "fresh_capture_required"
    assert result["captured_at"] == "2000-01-01T00:00:00Z"


@pytest.mark.parametrize("attack", ["scene", "report"])
def test_validation_rejects_scene_or_report_substitution(tmp_path, scene, attack):  # noqa: F811
    from self_improving.harness.genesis_scene_replay import evaluate_scene_replay

    store = LocalArtifactStore(tmp_path / "cas")

    def put(value, name, lines=False):
        path = tmp_path / name
        path.write_text("\n".join(json.dumps(row) for row in value) if lines else json.dumps(value))
        return store.put_file(
            path, name=name, media_type="application/json", schema_version=None
        ).model_dump(mode="json")

    source = put(scene.model_dump(mode="json"), "scene.json")
    baseline, half = rows(scene, "baseline"), rows(scene, "half_dt")
    report = evaluate_scene_replay(scene, baseline, half).model_dump(mode="json")
    if attack == "report":
        baseline[-1]["contacts"][0]["penetration"] = 0.1
    profiles = {
        p: {"members": {p + "/trace.ndjson": put(r, p + ".json", True)}}
        for p, r in (("baseline", baseline), ("half_dt", half))
    }
    receipt = put(
        {
            "schema_version": "harness.experiment_replay.v1",
            "status": "passed",
            "scene": source,
            "profiles": profiles,
            "report": put(report, "report.json"),
        },
        "receipt.json",
    )
    supplied_scene = put({"different": True}, "other.json") if attack == "scene" else source
    result = validate(
        store,
        input_ref=source,
        scene_ref=supplied_scene,
        replay_result=receipt,
        visual_advisory={"status": "passed"},
    )
    assert result["status"] == "failed"
    assert result["error_code"] == "validation_binding_or_evidence_failed"


@pytest.mark.parametrize(
    "case", ["physical_failure", "passed", "truncated", "execution_failure", "not_executed"]
)
def test_replay_execution_is_separate_from_physical_assessment(tmp_path, scene, monkeypatch, case):
    """Analytical traces and a runner double test orchestration, not Genesis execution."""
    from self_improving.harness import experiment_runtime as runtime

    store = LocalArtifactStore(tmp_path / "cas")
    path = tmp_path / "scene.json"
    path.write_text(scene.model_dump_json())
    source = store.put_file(
        path, name=path.name, media_type="application/json", schema_version=None
    )
    monkeypatch.setattr(runtime, "export_package", lambda *args, **kwargs: None)

    def execute(*args, output, profile, **kwargs):
        directory = output / profile
        directory.mkdir(parents=True)
        trace = rows(scene, profile)
        if case == "physical_failure":
            trace[-1]["contacts"][0]["penetration"] = 0.1
        if case == "truncated" and profile == "half_dt":
            trace = trace[:-1]
        (directory / "trace.ndjson").write_text("\n".join(json.dumps(row) for row in trace))
        return {
            "status": "failed" if case == "execution_failure" else "passed",
            "simulator_executed": case != "not_executed",
        }

    monkeypatch.setattr(runtime, "run_package", execute)
    result = replay(store, source, tmp_path / "run", {})
    complete = case in {"physical_failure", "passed"}
    assert result["status"] == ("passed" if complete else "failed")
    assert result["physical_status"] == (
        "failed" if case == "physical_failure" else "passed" if case == "passed" else "not_run"
    )
    assert ("report" in result) == complete
    if case == "physical_failure":
        validation = validate(
            store,
            input_ref=source,
            scene_ref=source,
            replay_result=result,
            visual_advisory={"status": "failed", "visual_status": "passed"},
        )
        assert validation["status"] == "failed"
        assert validation["physical_status"] == "failed"
        assert validation["visual_status"] == "passed"
