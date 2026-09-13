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


def video_decoder_double(monkeypatch, *, probe=None, framehash=None, failure=None):
    """Explicit ffprobe/FFmpeg subprocess boundary double; not a real video proof."""
    if probe is None:
        probe = {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 2,
                    "height": 2,
                    "avg_frame_rate": "1/1",
                    "duration": "1",
                    "codec_name": "fixture",
                }
            ],
            "format": {"duration": "1"},
        }
    if framehash is None:
        framehash = (
            "#tb 0: 1/1\n#software: explicit decoder double\n0, 0, 0, 1, 12, " + "a" * 64 + "\n"
        )

    def run(argv, **kwargs):
        if failure:
            raise failure
        raw = json.dumps(probe).encode() if argv[0] == "ffprobe" else framehash.encode()
        return subprocess.CompletedProcess(argv, 0, raw, b"")

    monkeypatch.setattr(subprocess, "run", run)


def test_stream_duration_does_not_require_redundant_container_duration(tmp_path, monkeypatch):
    from self_improving.harness.x2env.input import ingest

    probe = {
        "streams": [
            {
                "codec_type": "video",
                "width": 2,
                "height": 2,
                "avg_frame_rate": "1/1",
                "duration": "1",
                "codec_name": "fixture",
            }
        ],
        "format": {},
    }
    video_decoder_double(monkeypatch, probe=probe)
    path = tmp_path / "source.mp4"
    path.write_bytes(b"explicit decoder fixture")
    result = ingest(request(tmp_path, video=InputMedia(path=str(path))), Store(tmp_path / "state"))
    assert result.video.frame_count == 1


def test_container_duration_is_used_when_stream_duration_is_absent(tmp_path, monkeypatch):
    from self_improving.harness.x2env.input import ingest

    probe = {
        "streams": [
            {
                "codec_type": "video",
                "width": 2,
                "height": 2,
                "avg_frame_rate": "1/1",
                "codec_name": "fixture",
            }
        ],
        "format": {"duration": "1"},
    }
    video_decoder_double(monkeypatch, probe=probe)
    path = tmp_path / "source.mp4"
    path.write_bytes(b"explicit decoder fixture")
    result = ingest(request(tmp_path, video=InputMedia(path=str(path))), Store(tmp_path / "state"))
    assert result.video.frame_count == 1


@pytest.mark.parametrize(
    "fault",
    [
        "no_video",
        "duration",
        "pixels",
        "bytes",
        "frame_record",
        "frame_size",
        "timestamps",
        "no_frames",
        "container_count",
    ],
)
def test_invalid_decoder_evidence_keeps_raw_and_logs(tmp_path, monkeypatch, fault):
    from self_improving.harness.x2env.input import IngestLimits, InputIngestError, ingest

    stream = {
        "codec_type": "video",
        "width": 2,
        "height": 2,
        "avg_frame_rate": "1/1",
        "duration": "1",
        "codec_name": "double",
        "nb_frames": "1",
    }
    probe = {"streams": [stream], "format": {"duration": "1"}}
    header = "\n#tb 0: 1/1\n#software: decoder double\n"
    row = "0, 0, 0, 1, 12, " + "a" * 64 + "\n"
    hashes = header + row
    limits = IngestLimits()
    if fault == "no_video":
        probe["streams"] = []
    if fault == "duration":
        stream["duration"] = "61"
    if fault == "pixels":
        limits = IngestLimits(decoded_pixels=1)
    if fault == "bytes":
        limits = IngestLimits(decoded_bytes=1)
    if fault == "frame_record":
        hashes = header + "1, bad\n"
    if fault == "frame_size":
        hashes = header + row.replace(", 12,", ", 9,")
    if fault == "timestamps":
        hashes = header + row + row
    if fault == "no_frames":
        hashes = header
    if fault == "container_count":
        stream["nb_frames"] = "2"
    video_decoder_double(monkeypatch, probe=probe, framehash=hashes)
    path = tmp_path / "input.mp4"
    path.write_bytes(b"decoder fixture")
    store = Store(tmp_path / "state")
    with pytest.raises(InputIngestError) as caught:
        ingest(request(tmp_path, video=InputMedia(path=str(path))), store, limits=limits)
    assert caught.value.code == (
        "input_limit_exceeded" if fault in {"duration", "pixels", "bytes"} else "invalid_video"
    )
    assert store.read_artifact(caught.value.artifacts[0]) == b"decoder fixture"
    assert len(caught.value.artifacts) >= 3


@pytest.mark.parametrize("fault", ["missing", "timeout", "timeout_with_partial"])
def test_decoder_external_failure_is_structured_with_partial_logs(tmp_path, monkeypatch, fault):
    from self_improving.harness.x2env.input import InputIngestError, ingest

    failure = (
        FileNotFoundError("ffprobe")
        if fault == "missing"
        else subprocess.TimeoutExpired(
            ["ffprobe"],
            1,
            output=b"partial decoder log" if fault == "timeout_with_partial" else None,
        )
    )
    video_decoder_double(monkeypatch, failure=failure)
    path = tmp_path / "input.mp4"
    path.write_bytes(b"source")
    store = Store(tmp_path / "state")
    with pytest.raises(InputIngestError) as caught:
        ingest(request(tmp_path, video=InputMedia(path=str(path))), store)
    assert caught.value.code == (
        "blocked_external_resource" if fault == "missing" else "input_decode_timeout"
    )
    if fault == "timeout_with_partial":
        assert any(store.read_artifact(r) == b"partial decoder log" for r in caught.value.artifacts)


@pytest.mark.parametrize("fault", ["grow_over_limit", "changed", "permission"])
def test_source_change_or_read_failure_cannot_be_accepted(tmp_path, monkeypatch, fault):
    from pathlib import Path

    from self_improving.harness.x2env.input import IngestLimits, InputIngestError, ingest

    path = tmp_path / "image.png"
    path.write_bytes(b"source")
    original = Path.open

    def external_open(self, *args, **kwargs):
        if self == path and args and args[0] == "rb":
            if fault == "permission":
                raise PermissionError("explicit filesystem failure")
            with original(self, "ab") as writer:
                writer.write(b"x")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", external_open)
    limits = IngestLimits(source_bytes=6 if fault == "grow_over_limit" else 64)
    with pytest.raises(InputIngestError) as caught:
        ingest(
            request(tmp_path, images=(InputMedia(path=str(path)),)),
            Store(tmp_path / "store"),
            limits=limits,
        )
    assert (
        caught.value.code
        == {
            "grow_over_limit": "input_limit_exceeded",
            "changed": "input_changed",
            "permission": "input_unavailable",
        }[fault]
    )


@pytest.mark.parametrize("animated", [False, True])
def test_unsupported_or_animated_images_do_not_silently_drop_frames(tmp_path, animated):
    from self_improving.harness.x2env.input import InputIngestError, ingest

    path = tmp_path / ("animated.png" if animated else "image.bmp")
    first = Image.new("RGB", (2, 2), "red")
    if animated:
        first.save(path, save_all=True, append_images=[Image.new("RGB", (2, 2), "blue")])
    else:
        first.save(path)
    with pytest.raises(InputIngestError) as caught:
        ingest(request(tmp_path, images=(InputMedia(path=str(path)),)), Store(tmp_path / "store"))
    assert caught.value.code == "unsupported_image"


def test_ingest_limits_reject_nonpositive_configuration():
    from self_improving.harness.x2env.input import IngestLimits

    with pytest.raises(ValueError, match="positive integers"):
        IngestLimits(video_frames=0)
