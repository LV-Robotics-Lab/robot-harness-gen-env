import shutil
import subprocess

import pytest
from PIL import Image

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.experiment_inputs import ExperimentInputs


def test_text_only_ingest_keeps_original_bytes_and_routes_without_fake_image(tmp_path):
    store = LocalArtifactStore(tmp_path / "cas")
    inputs = ExperimentInputs(store)
    bundle = inputs.ingest(text="在桌子左侧放一个红色方块。")
    assert bundle.route == "text"
    assert bundle.images == () and bundle.video is None
    assert store.resolve(bundle.text).path.read_text() == "在桌子左侧放一个红色方块。"
    assert inputs.ingest(text="在桌子左侧放一个红色方块。") == bundle


def test_image_only_and_combined_inputs_preserve_media_identity(tmp_path):
    path = tmp_path / "object.png"
    Image.new("RGB", (16, 16), "red").save(path)
    inputs = ExperimentInputs(LocalArtifactStore(tmp_path / "cas"))
    image_only = inputs.ingest(images=(path,))
    combined = inputs.ingest(text="place it on a table", images=(path,))
    assert image_only.route == "image" and image_only.text is None
    assert combined.route == "multimodal"
    assert combined.images == image_only.images
    assert combined.images[0].source.bytes == path.stat().st_size


def test_empty_and_link_inputs_are_rejected(tmp_path):
    inputs = ExperimentInputs(LocalArtifactStore(tmp_path / "cas"))
    with pytest.raises(ValueError, match="nonempty"):
        inputs.ingest()
    link = tmp_path / "link.png"
    link.symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="regular"):
        inputs.ingest(images=(link,))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg unavailable")
def test_video_keeps_complete_sequence_and_combination_route(tmp_path):
    video = tmp_path / "video.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=32x32:rate=6",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        timeout=30,
    )
    inputs = ExperimentInputs(LocalArtifactStore(tmp_path / "cas"))
    bundle = inputs.ingest(video=video)
    assert bundle.route == "video" and bundle.images == ()
    assert bundle.video.decoded_frame_count == 12
    assert inputs.ingest(text="use the final arrangement", video=video).route == "multimodal"


def test_intent_is_bound_to_input_and_rejects_unsupported_or_authority_fields(tmp_path):
    inputs = ExperimentInputs(LocalArtifactStore(tmp_path / "cas"))
    bundle = inputs.ingest(text="a red cube on the left of the table")
    ref = inputs.save(bundle)
    proposal = dict(
        input_sha256=ref.sha256,
        category="cube",
        color="red",
        source="generated",
        selection="exact",
        dimensions_m=[0.08, 0.08, 0.08],
        xy_m=[-0.15, 0.0],
        yaw_deg=0.0,
        support="table",
        object_count=1,
        unknowns=[],
    )
    intent = inputs.accept_intent(ref, proposal)
    assert intent.xy_m == (-0.15, 0.0)
    for change in (
        {"input_sha256": "0" * 64},
        {"object_count": 2},
        {"publishable": True},
        {"unknowns": ["ambiguous quantity"]},
        {"xy_m": [float("nan"), 0.0]},
        {"support": "inside"},
    ):
        with pytest.raises(ValueError):
            inputs.accept_intent(ref, {**proposal, **change})
