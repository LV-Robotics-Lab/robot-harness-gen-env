import json
import subprocess

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.experiment_package import package_workflow
from self_improving.harness.portable_genesis_package import load_package


class StatusService:
    def __init__(self, status):
        self.value = status

    def status(self, *, workflow_id):
        return self.value


@pytest.mark.parametrize(
    "history,revision",
    [
        ([], 0),
        ([{"stage": "validate", "status": "failed"}], 1),
        ([{"stage": "validate", "status": "passed", "operation": {"committed_revision": 2}}], 3),
    ],
)
def test_unvalidated_or_stale_workflow_cannot_be_exported(tmp_path, history, revision):
    service = StatusService({"history": history, "snapshot": {"revision": revision}})
    with pytest.raises(ValueError, match="validat"):
        package_workflow(service, "workflow", tmp_path / "package")
    assert not (tmp_path / "package").exists()


def test_current_validation_exports_inputs_media_and_history_without_execution_claim(tmp_path):
    from PIL import Image

    store = LocalArtifactStore(tmp_path / "cas")

    def put(data, name, media="application/json"):
        path = tmp_path / name
        path.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())
        return store.put_file(path, name=name, media_type=media, schema_version=None).model_dump(
            mode="json"
        )

    source = put({"text": "red block"}, "input.json")
    urdf = put(
        b'<robot name="test"><link name="base"/></robot>', "body.urdf", "application/octet-stream"
    )
    physics = put({"friction": 0.6}, "physics.json")
    representation = put(
        {
            "root_logical_path": "urdf/body.urdf",
            "members": [
                {"logical_path": "urdf/body.urdf", "artifact": urdf},
                {"logical_path": "physics.json", "artifact": physics},
            ],
        },
        "representation.json",
    )
    scene = put(
        {"schema_version": "harness.genesis_scene_spec.v1", "representation": representation},
        "scene.json",
    )
    png = tmp_path / "sample.png"
    Image.new("RGB", (16, 16)).save(png)
    image = put(png.read_bytes(), "image.png", "image/png")
    video_path = tmp_path / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=16x16:r=10",
            "-frames:v",
            "2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video_path),
        ],
        check=True,
    )
    video = put(video_path.read_bytes(), "media.mp4", "video/mp4")
    replay_receipt = put({"fixture": "static consumer only"}, "replay-inner.json")
    observation_receipt = put({"fixture": "static consumer only"}, "observation-inner.json")
    validation = put(
        {
            "status": "passed",
            "input": source,
            "scene": scene,
            "replay": replay_receipt,
            "observation": observation_receipt,
        },
        "validation.json",
    )
    facts = {
        "request.experiment_input": source,
        "environment.scene": put({"scene_ref": scene}, "compiled.json"),
        "environment.replay": put(
            {"receipt": replay_receipt, "media": [image, video]}, "replay.json"
        ),
        "environment.observation": put({"receipt": observation_receipt}, "observe.json"),
        "experiment.visual": put({"status": "passed"}, "visual.json"),
        "environment.validation": validation,
        "experiment.intent": put({"intent": "fixture"}, "intent.json"),
        "experiment.asset": put({"version": "fixture"}, "asset.json"),
    }
    service = StatusService(
        {
            "snapshot": {"revision": 7},
            "facts": facts,
            "history": [
                {
                    "stage": "validate",
                    "status": "passed",
                    "receipt": validation,
                    "operation": {"committed_revision": 7, "operation_id": "fixture"},
                }
            ],
        }
    )
    service.store = store
    result = package_workflow(service, "fixture", tmp_path / "exported")
    assert result["checks"]["relocated_execution"] == "not_run"
    manifest = load_package(tmp_path / "exported")
    provenance = json.loads((tmp_path / "exported" / "provenance.json").read_bytes())
    assert provenance["input"] == source
    assert provenance["history"][0]["committed_revision"] == 7
    assert any(member["path"].endswith(".mp4") for member in manifest["members"])
