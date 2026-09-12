import subprocess

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.experiment_inputs import ExperimentInputs
from self_improving.harness.experiment_model_stages import execute_model_stage


class AdvisoryBackend:
    """Controlled model-output seam; this fixture does not execute a model."""

    def __init__(self, proposal):
        self.proposal = proposal

    def propose(self, **kwargs):
        return {
            "status": "completed",
            "proposal": self.proposal,
            "external_agent_executed": False,
            "authority": "advisory_only",
        }


def test_interpret_rejects_model_substituted_input_identity(tmp_path):
    store = LocalArtifactStore(tmp_path / "cas")
    inputs = ExperimentInputs(store)
    reference = inputs.save(inputs.ingest(text="a red block on table"))
    proposal = {
        "category": "block",
        "color": "red",
        "source": "generated",
        "selection": "exact",
        "dimensions_m": None,
        "xy_m": [0.0, 0.0],
        "yaw_deg": 0.0,
        "support": "table",
        "object_count": 1,
        "input_sha256": "a" * 64,
        "unknowns": [],
    }
    with pytest.raises(ValueError, match="input identity"):
        execute_model_stage(
            "interpret",
            {"input_ref": reference},
            store=store,
            root=tmp_path / "operation",
            settings={},
            backend=AdvisoryBackend(proposal),
        )


def test_scene_revision_cannot_change_asset_source(tmp_path):
    store = LocalArtifactStore(tmp_path / "cas")
    inputs = ExperimentInputs(store)
    reference = inputs.save(inputs.ingest(text="a red block on table"))
    intent = {
        "category": "block",
        "color": "red",
        "source": "generated",
        "selection": "exact",
        "dimensions_m": None,
        "xy_m": [0.0, 0.0],
        "yaw_deg": 0.0,
        "support": "table",
        "object_count": 1,
        "input_sha256": reference.sha256,
        "unknowns": [],
    }
    prior = inputs.publish({"intent": intent}, name="prior.json")
    with pytest.raises(ValueError, match="layout"):
        execute_model_stage(
            "revise_scene",
            {"input_ref": reference, "dependencies": {"experiment.intent": prior}},
            store=store,
            root=tmp_path / "operation",
            settings={},
            backend=AdvisoryBackend({**intent, "source": "web", "xy_m": [0.1, 0.0]}),
        )


def test_video_samples_keep_explicit_original_frame_indices(tmp_path):
    video = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=32x32:r=10",
            "-frames:v",
            "6",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
    )
    store = LocalArtifactStore(tmp_path / "cas")
    inputs = ExperimentInputs(store)
    reference = inputs.save(inputs.ingest(video=video))
    proposal = {
        "category": "block",
        "color": "red",
        "source": "generated",
        "selection": "exact",
        "dimensions_m": None,
        "xy_m": [0.0, 0.0],
        "yaw_deg": 0.0,
        "support": "table",
        "object_count": 1,
        "input_sha256": reference.sha256,
        "unknowns": [],
    }
    result = execute_model_stage(
        "interpret",
        {"input_ref": reference},
        store=store,
        root=tmp_path / "operation",
        settings={},
        backend=AdvisoryBackend(proposal),
    )
    assert result["status"] == "passed"
    assert [sample["frame_index"] for sample in result["video_samples"]] == [0, 3, 5]
    assert result["model"]["external_agent_executed"] is False


def test_visual_asset_patch_comes_from_explicit_model_fields(tmp_path):
    from PIL import Image

    store = LocalArtifactStore(tmp_path / "cas")
    inputs = ExperimentInputs(store)
    reference = inputs.save(inputs.ingest(text="a block"))
    image = tmp_path / "camera.png"
    Image.new("RGB", (4, 4)).save(image)
    image_ref = store.put_file(image, name=image.name, media_type="image/png", schema_version=None)
    scene_ref = inputs.publish({"position_m": [0.0, 0.0, 0.4]}, name="scene.json")
    scene = inputs.publish({"scene_ref": scene_ref.model_dump(mode="json")}, name="compiled.json")
    observation = inputs.publish(
        {
            "status": "passed",
            "image": image_ref.model_dump(mode="json"),
            "scene": scene_ref.model_dump(mode="json"),
        },
        name="observation.json",
    )
    proposal = {
        "status": "failed",
        "visual_status": "failed",
        "reason": "Visible size mismatch; no physical claim.",
        "input_sha256": reference.sha256,
        "scene_sha256": scene_ref.sha256,
        "repair_kind": "asset",
        "asset_patch": {"scale": 0.5, "mass_kg": None, "friction": None},
    }
    result = execute_model_stage(
        "visual",
        {
            "input_ref": reference,
            "dependencies": {"environment.scene": scene, "environment.observation": observation},
        },
        store=store,
        root=tmp_path / "operation",
        settings={},
        backend=AdvisoryBackend(proposal),
    )
    assert result["repair_kind"] == "asset"
    assert result["asset_patch"] == {"scale": 0.5}


def test_visual_pass_can_accompany_programmatic_failure_advice():
    from self_improving.harness.experiment_model_stages import VisualAdvisory

    value = VisualAdvisory.model_validate(
        {
            "status": "failed",
            "visual_status": "passed",
            "reason": "Support margin failed.",
            "input_sha256": "a" * 64,
            "scene_sha256": "b" * 64,
            "repair_kind": "scene",
            "asset_patch": None,
        }
    )
    assert value.visual_status == "passed"
