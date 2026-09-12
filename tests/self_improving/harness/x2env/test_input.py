"""Input ingest public seam, using the real canonical Store and media decoders."""

import hashlib
import json
import subprocess

import pytest
from PIL import Image

from self_improving.harness.x2env.contracts import InputMedia, X2EnvRequest
from self_improving.harness.x2env.store import Store


def request(tmp_path, **fields):
    return X2EnvRequest(seed=11, idempotency_key="input", output_dir=str(tmp_path), **fields)


def test_text_ingest_preserves_exact_utf8_and_identity(tmp_path):
    from self_improving.harness.x2env.input import ingest

    store = Store(tmp_path / "state")
    value = "  粉红色鼠标\n"
    result = ingest(request(tmp_path, text=value), store)
    assert result.modality == "text"
    assert store.read_artifact(result.text) == value.encode()
    assert result.text.sha256 == hashlib.sha256(value.encode()).hexdigest()
    assert result.seed == 11
    assert result.override_policy == "explicit_text_over_media_with_provenance"
    assert result == ingest(request(tmp_path, text=value), store)


def test_real_image_preserves_raw_bytes_and_binds_changed_content(tmp_path):
    from self_improving.harness.x2env.input import ingest

    path = tmp_path / "image.png"
    Image.new("RGB", (4, 3), "red").save(path)
    raw = path.read_bytes()
    store = Store(tmp_path / "state")
    req = request(tmp_path, images=(InputMedia(path=str(path)),))
    result = ingest(req, store)
    assert result.modality == "image"
    assert store.read_artifact(result.images[0].source) == raw
    assert (result.images[0].width, result.images[0].height) == (4, 3)
    Image.new("RGB", (4, 3), "blue").save(path)
    assert ingest(req, store).request_sha256 != result.request_sha256


def test_bad_image_preserves_raw_artifact_and_classifies_failure(tmp_path):
    from self_improving.harness.x2env.input import InputIngestError, ingest

    path = tmp_path / "broken.png"
    path.write_bytes(b"not an image")
    store = Store(tmp_path / "state")
    with pytest.raises(InputIngestError) as caught:
        ingest(request(tmp_path, images=(InputMedia(path=str(path)),)), store)
    assert caught.value.code == "invalid_image"
    assert store.read_artifact(caught.value.artifacts[0]) == b"not an image"


def test_real_video_ingest_records_every_frame_in_sequence(tmp_path):
    from self_improving.harness.x2env.input import ingest

    video = tmp_path / "input.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=32x24:rate=4:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        timeout=30,
    )
    store = Store(tmp_path / "state")
    result = ingest(request(tmp_path, video=InputMedia(path=str(video))), store)
    assert result.modality == "video"
    assert result.video.frame_count == 4
    sequence = json.loads(store.read_artifact(result.video.sequence))
    assert [f["index"] for f in sequence["frames"]] == [0, 1, 2, 3]
    assert len({f["sha256"] for f in sequence["frames"]}) == result.video.unique_frame_count
    assert sequence["full_decode"] is True
    assert store.read_artifact(result.video.source) == video.read_bytes()
    assert (
        ingest(request(tmp_path, video=InputMedia(path=str(video))), store).request_sha256
        == result.request_sha256
    )
    from self_improving.harness.x2env.input import IngestLimits, InputIngestError

    with pytest.raises(InputIngestError) as caught:
        ingest(
            request(tmp_path, video=InputMedia(path=str(video))),
            store,
            limits=IngestLimits(video_frames=3),
        )
    assert caught.value.code == "input_limit_exceeded"
    assert len(caught.value.artifacts) >= 3


@pytest.mark.parametrize(
    "limit", [{"source_bytes": 1}, {"decoded_pixels": 1}, {"decoded_bytes": 1}]
)
def test_image_limits_fail_closed(tmp_path, limit):
    from self_improving.harness.x2env.input import IngestLimits, InputIngestError, ingest

    path = tmp_path / "image.png"
    Image.new("RGB", (4, 3)).save(path)
    with pytest.raises(InputIngestError) as caught:
        ingest(
            request(tmp_path, images=(InputMedia(path=str(path)),)),
            Store(tmp_path / "state"),
            limits=IngestLimits(**limit),
        )
    assert caught.value.code == "input_limit_exceeded"


def test_palette_transparency_and_mixed_input_preserved(tmp_path):
    from io import BytesIO

    from self_improving.harness.x2env.input import ingest

    path = tmp_path / "image.png"
    Image.new("P", (4, 3)).save(path, transparency=0)
    store = Store(tmp_path / "state")
    result = ingest(
        request(tmp_path, text="explicit request", images=(InputMedia(path=str(path)),)), store
    )
    assert result.modality == "multimodal"
    assert result.images[0].mode == "RGBA"
    assert (
        Image.open(BytesIO(store.read_artifact(result.images[0].canonical))).getpixel((0, 0))[3]
        == 0
    )


def test_missing_and_symlink_sources_rejected(tmp_path):
    from self_improving.harness.x2env.input import InputIngestError, ingest

    store = Store(tmp_path / "state")
    path = tmp_path / "missing.png"
    with pytest.raises(InputIngestError) as caught:
        ingest(request(tmp_path, images=(InputMedia(path=str(path)),)), store)
    assert caught.value.code == "input_unavailable"
    path.symlink_to(tmp_path / "target")
    with pytest.raises(InputIngestError) as caught:
        ingest(request(tmp_path, images=(InputMedia(path=str(path)),)), store)
    assert caught.value.code == "unsafe_input_path"


def test_bad_video_retains_raw_and_decoder_failure(tmp_path):
    from self_improving.harness.x2env.input import InputIngestError, ingest

    path = tmp_path / "bad.mp4"
    path.write_bytes(b"broken video")
    store = Store(tmp_path / "state")
    with pytest.raises(InputIngestError) as caught:
        ingest(request(tmp_path, video=InputMedia(path=str(path))), store)
    assert caught.value.code == "invalid_video"
    assert store.read_artifact(caught.value.artifacts[0]) == b"broken video"
    assert any(b"Invalid data" in store.read_artifact(ref) for ref in caught.value.artifacts[1:])
