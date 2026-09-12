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


@pytest.mark.parametrize("missing_journal", [False, True, "runtime_manifest"])
def test_current_validation_exports_inputs_media_and_history_without_execution_claim(
    tmp_path, missing_journal
):
    from PIL import Image

    store = LocalArtifactStore(tmp_path / "cas")

    def put(data, name, media="application/json", schema=None):
        path = tmp_path / name
        path.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())
        return store.put_file(path, name=name, media_type=media, schema_version=schema).model_dump(
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
    delta = put({"fixture": "state delta"}, "delta.json", schema="harness.state_delta.v1")
    journal_refs = {
        "command": put(
            {"fixture": "command"}, "command.json", schema="harness.run_command_request.v1"
        ),
        "qualified_execution": put(
            {"fixture": "development"},
            "execution.json",
            schema="harness.development_skill_execution.v1",
        ),
        "tool_result": put(
            {"state_delta": delta}, "tool-result.json", schema="harness.tool_result.v2"
        ),
    }
    snapshot_refs = {
        key: put({"fixture": key}, key + ".json", schema=schema)
        for key, schema in {
            "state_ref": "harness.trusted_world_state.v1",
            "receipt_head": "harness.workflow_operation_receipt.v1",
            "start_request_ref": "harness.run_start_request.v1",
            "registry_snapshot": "harness.development_registry_snapshot.v1",
        }.items()
    }
    service = StatusService(
        {
            "snapshot": {"revision": 7, **snapshot_refs},
            "qualification_status": "development_unqualified",
            "facts": facts,
            "history": [
                {
                    "stage": "validate",
                    "status": "passed",
                    "receipt": validation,
                    "operation": {
                        "committed_revision": 7,
                        "operation_id": "fixture",
                        **journal_refs,
                    },
                }
            ],
        }
    )
    service.store = store
    if missing_journal == "runtime_manifest":
        service.value["history"][0]["operation"]["command"]["schema_version"] = (
            "harness.genesis_runtime_manifest.v1"
        )
        with pytest.raises(ValueError, match="runtime manifest"):
            package_workflow(service, "fixture", tmp_path / "exported")
        assert not (tmp_path / "exported").exists()
        return
    if missing_journal:
        del service.value["history"][0]["operation"]["tool_result"]
        with pytest.raises(ValueError, match="journal"):
            package_workflow(service, "fixture", tmp_path / "exported")
        assert not (tmp_path / "exported").exists()
        return
    result = package_workflow(service, "fixture", tmp_path / "exported")
    assert result["checks"]["relocated_execution"] == "not_run"
    manifest = load_package(tmp_path / "exported")
    provenance = json.loads((tmp_path / "exported" / "provenance.json").read_bytes())
    assert provenance["input"] == source
    assert provenance["history"][0]["committed_revision"] == 7
    mapped_hashes = {ref["sha256"] for ref in provenance["artifact_mapping"].values()}
    assert {
        ref["sha256"] for ref in [delta, *journal_refs.values(), *snapshot_refs.values()]
    } <= mapped_hashes
    assert any(member["path"].endswith(".mp4") for member in manifest["members"])
