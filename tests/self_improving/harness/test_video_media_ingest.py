"""Public-seam RED for complete, ordered video source identity ingestion.

The deterministic decoder below is an external-boundary test adapter over facts measured once
from the embedded 18-frame MP4 with FFmpeg 6.1.1.  It is not a production decoder, sandbox,
qualification, or simulator claim.  In particular, every decoded frame identity is returned;
the Gujie-style 15-frame sample is used only as a rejection case.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
from importlib import import_module
from pathlib import Path

import pytest
from pydantic import ValidationError

import self_improving.harness as public_harness
from self_improving.harness import schemas as public_schemas
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.schema_catalog import schema_model
from self_improving.harness.schemas.common import ArtifactRef

try:
    _video_ingest = import_module("self_improving.harness.video_ingest")
except ModuleNotFoundError as error:
    if error.name != "self_improving.harness.video_ingest":
        raise
    _video_ingest = None

DecodedVideoFrame = getattr(_video_ingest, "DecodedVideoFrame", None)
DecodedVideoStream = getattr(_video_ingest, "DecodedVideoStream", None)
VideoContainerDecoder = getattr(_video_ingest, "VideoContainerDecoder", None)
VideoMediaConsumerVerifier = getattr(_video_ingest, "VideoMediaConsumerVerifier", None)
VideoMediaIngestError = getattr(_video_ingest, "VideoMediaIngestError", None)
VideoMediaIngestor = getattr(_video_ingest, "VideoMediaIngestor", None)
VideoDecodedFrameSequenceV1 = getattr(public_schemas, "VideoDecodedFrameSequenceV1", None)
VideoMediaProbeV1 = getattr(public_schemas, "VideoMediaProbeV1", None)
VideoSourceMediaV2 = getattr(public_schemas, "VideoSourceMediaV2", None)

_SEQUENCE_SCHEMA_ID = "harness.video_decoded_frame_sequence.v1"
_PROBE_SCHEMA_ID = "harness.video_media_probe.v1"
_SOURCE_MEDIA_SCHEMA_ID = "harness.video_source_media.v2"
_PUBLIC_SEAM_READY = all(
    symbol is not None
    for symbol in (
        DecodedVideoFrame,
        DecodedVideoStream,
        VideoContainerDecoder,
        VideoMediaConsumerVerifier,
        VideoMediaIngestError,
        VideoMediaIngestor,
        VideoDecodedFrameSequenceV1,
        VideoMediaProbeV1,
        VideoSourceMediaV2,
    )
)
_requires_video_ingest = pytest.mark.skipif(
    not _PUBLIC_SEAM_READY,
    reason="waiting for the public complete VideoMediaIngestor seam",
)

# Valid H.264/yuv420p MP4: 16x16, 6/1 fps, 3 seconds, 18 decoded frames, 6 unique.
_FIXED_MP4 = base64.b64decode(
    "AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAANybW9vdgAAAGxtdmhkAAAAAAAAAAAAAAAAAAAD"
    "6AAAC7gAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAApx0cmFrAAAAXHRraGQAAAADAAAAAAAAAAAAAAABAAAAAAAAC7gA"
    "AAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAABAAAAAQAAAAAAAk"
    "ZWR0cwAAABxlbHN0AAAAAAAAAAEAAAu4AAAAAAABAAAAAAIUbWRpYQAAACBtZGhkAAAAAAAAAAAAAAAAAAAw"
    "AAAAkABVxAAAAAAALWhkbHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABWaWRlb0hhbmRsZXIAAAABv21pbmYA"
    "AAAUdm1oZAAAAAEAAAAAAAAAAAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAAAQAAAX9zdGJsAA"
    "AAv3N0c2QAAAAAAAAAAQAAAK9hdmMxAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAABAAEABIAAAASAAAAAAAAAA"
    "BFUxhdmM2MC4zMS4xMDIgbGlieDI2NAAAAAAAAAAAAAAAGP//AAAANWF2Y0MB9AAK/+EAGGf0AAquTCL2AiAA"
    "AAMAIAAAAwGB4kTCMAEABmjoQwGvLP34+AAAAAAQcGFzcAAAAAEAAAABAAAAFGJ0cnQAAAAAAAAQiAAAEIgAA"
    "AAYc3R0cwAAAAAAAAABAAAAEgAACAAAAAAUc3RzcwAAAAAAAAABAAAAAQAAABxzdHNjAAAAAAAAAAEAAAABAA"
    "AAEgAAAAEAAABcc3RzegAAAAAAAAAAAAAAEgAAA2oAAAALAAAADwAAAG0AAAAQAAAAEAAAAF8AAAASAAAAWgAA"
    "ABMAAAATAAAAFAAAAHcAAAAVAAAAFgAAAE8AAAAWAAAAFgAAABRzdGNvAAAAAAAAAAEAAAOiAAAAYnVkdGEA"
    "AABabWV0YQAAAAAAAAAhaGRscgAAAAAAAAAAbWRpcmFwcGwAAAAAAAAAAAAAAAAtaWxzdAAAACWpdG9vAAAAHW"
    "RhdGEAAAABAAAAAExhdmY2MC4xNi4xMDAAAAAIZnJlZQAABjttZGF0AAACDgYF//8K3EXpvebZSLeWLNgg2SPu"
    "73gyNjQgLSBjb3JlIDE2NCByMzEwOCAzMWUxOWY5IC0gSC4yNjQvTVBFRy00IEFWQyBjb2RlYyAtIENvcHls"
    "ZWZ0IDIwMDMtMjAyMyAtIGh0dHA6Ly93d3cudmlkZW9sYW4ub3JnL3gyNjQuaHRtbCAtIG9wdGlvbnM6IGNh"
    "YmFjPTEgcmVmPTE2IGRlYmxvY2s9MTowOjAgYW5hbHlzZT0weDM6MHgxMzMgbWU9dW1oIHN1Ym1lPTkgcHN5"
    "PTAgbWl4ZWRfcmVmPTEgbWVfcmFuZ2U9MjQgY2hyb21hX21lPTEgdHJlbGxpcz0wIDh4OGRjdD0xIGNxbT0w"
    "IGRlYWR6b25lPTIxLDExIGZhc3RfcHNraXA9MCBjaHJvbWFfcXBfb2Zmc2V0PTAgdGhyZWFkcz0xIGxvb2th"
    "aGVhZF90aHJlYWRzPTEgc2xpY2VkX3RocmVhZHM9MCBucj0wIGRlY2ltYXRlPTEgaW50ZXJsYWNlZD0wIGJs"
    "dXJheV9jb21wYXQ9MCBjb25zdHJhaW5lZF9pbnRyYT0wIGJmcmFtZXM9MCB3ZWlnaHRwPTIga2V5aW50PTI1"
    "MCBrZXlpbnRfbWluPTYgc2NlbmVjdXQ9NDAgaW50cmFfcmVmcmVzaD0wIHJjPWNxcCBtYnRyZWU9MCBxcD0w"
    "AIAAAAFUZYiCV9fjwICW1KosL8dfa/slhZC4qBapmCfPo8GByZv8lpbrSKDG/////s0SEJw/SIr7vylbubEBb4"
    "eq+12+eIoumhIHTvwk43DJzTtdhVYcbKyXoJvL82qQyXy+MoNZWDPUnFVmf3gea5JK30hcfiyp7eQnjyKa1nz"
    "VVtd71VvaeT5itdMvPSYPZL1ajky9zzd10kP0No3x030D+B8+Usv+DNMSqOojeyJ0YevIrRE+gzmXf9M4rOmr"
    "EcJrtajWtcD6/ipq2h/i6LmlfMXKfSO5NWj49E54YrB7VG2+tRNdHkNTFjxbd6jT0jwpZdBb1AZLv6fw7NL7"
    "w+wLI8INswqL7AlQPvtTaEThMhdimYzKitLqdWSW6xp5IX3zfU50/5pUHEd7w/XRYuGANNngPpRLHfQIEVo8t"
    "ptzRsGzuBE4aH2zzggDpltZl8NT6rt2SX+RgwAAAAdBmh2Nf/7wAAAAC0GaK/BBkymGv/7xAAAAaUGaOTwQeT"
    "KYGqyssgQ3EyC+X//ge++fHf4JzbHJIVZ2I3kGYcU1phmxGGYl7R4uwzYJ2q2l75gQanlOrZphXbDMsVyD+/x"
    "LqgF5vsuf//5/P/8Cl06YCo4N2LmGf6/ZX/8GIVYVvtwmfAAAAAxBmkl8EH5MpgGv/vEAAAAMQZpZvBB/kymA"
    "Gv7xAAAAW0GaafwQf+TKYAGvrHkNFCH65i/j3FK48cTR8Xgf2tBAo+Q4IQP8iqRk4PlMFRe5gzVlPH7HIi4xg"
    "OBu7IsP//8/a/4FK+XAVEAK+kcq0v5QUpGdFa1VZuz6v4EAAAAOQZp4jwQf/kymAAa//vEAAABWQZqInwQf/5"
    "MpgABrrCJBnz0j86XwWWxsqVfILtdx+CNgM/kxNhJJoc7fhLbGv+792ct8O/kMN0H//z4v+BQND7COyp8K2ga"
    "L6OC/NBaqOU+ehEUio8AAAAAPQZqYrwQf/+TKYAAGv/7wAAAAD0GaqL8EH//5MpgAAGv+8QAAABBBmrjPBB///"
    "kymAAAGv/7wAAAAc0GayN8EH///kymAAABrrFvDv62jUaoO+wT/4LLFGVKGEzOxyLwYrSZc+a0hEvqtzYLtqEq"
    "X6EUGPtOY795MINkfSHBuQvZMdig33//Pi/4FA0PsNCuPS0TIAILDHyXe0kYLTQ6NRDwNYFIsJE41lf9/quAAAA"
    "ARQZrY7wQf///kymAAAAa//vEAAAASQZro/wQf///5MpgAAAMAa/7xAAAAS0Ga94IP////JlMAAAMAA1+nDd+jbt"
    "ndr8DskhkP4RvxgYQ2bpEVIH8qySOsJvKkQbSf//3+BOks+0yKHJGW8DyuOgtD+vVkoDqhZwAAABJBmweCD////"
    "yZTAAADAANf/vAAAAASQZsXgg////8mUwAAAwADX/7w"
)
_SOURCE_SHA256 = "9aad9a27be8d91a4f7f68dbf447e1bfa18974cc21ff67675466604fe8d170e97"
_FRAME_HASHES = (
    "7496a5837480a949a4342dc0c41f2ea8091d62935b9cebb76eb76a3f84e1cba4",
    "7496a5837480a949a4342dc0c41f2ea8091d62935b9cebb76eb76a3f84e1cba4",
    "7496a5837480a949a4342dc0c41f2ea8091d62935b9cebb76eb76a3f84e1cba4",
    "a5aad829870d90b1d82c369de63e01bbd45eba9dbb4093b977aaddf76be61cc6",
    "a5aad829870d90b1d82c369de63e01bbd45eba9dbb4093b977aaddf76be61cc6",
    "a5aad829870d90b1d82c369de63e01bbd45eba9dbb4093b977aaddf76be61cc6",
    "0326e7082e94070d4ae4e8c2f1c7e2a089b39f1ac800356d57443fda030932a3",
    "0326e7082e94070d4ae4e8c2f1c7e2a089b39f1ac800356d57443fda030932a3",
    "a89e2fbc08fd059da51005fc54d395a118e233ab4d9bc8215587c7649e74e205",
    "a89e2fbc08fd059da51005fc54d395a118e233ab4d9bc8215587c7649e74e205",
    "a89e2fbc08fd059da51005fc54d395a118e233ab4d9bc8215587c7649e74e205",
    "a89e2fbc08fd059da51005fc54d395a118e233ab4d9bc8215587c7649e74e205",
    "2e73c1b7d67c823c2a1154911014172e5968b876224274a36785bcb4c3d501f6",
    "2e73c1b7d67c823c2a1154911014172e5968b876224274a36785bcb4c3d501f6",
    "2e73c1b7d67c823c2a1154911014172e5968b876224274a36785bcb4c3d501f6",
    "97a86a34d5fbf5a4625ac2b34a1108f4e0736a2dc29c71074425096978531af0",
    "97a86a34d5fbf5a4625ac2b34a1108f4e0736a2dc29c71074425096978531af0",
    "97a86a34d5fbf5a4625ac2b34a1108f4e0736a2dc29c71074425096978531af0",
)
# SHA-256 of the one-newline canonical JSON identity document whose fields are
# schema_version=harness.video_decoded_frame_sequence_identity.v1,
# time_base_numerator/denominator, and the ordered frame records including index,
# dts, pts, duration, byte count, and SHA-256.  This literal is independently fixed
# from the FFmpeg 6.1.1 framemd5 evidence above; 15 samples produce a different digest.
_SEQUENCE_SHA256 = "a70265aea01ec383f2975dacba11f2d331bc885893292e3b701f03293e7a31b8"


def _replace(value, **updates):
    if hasattr(value, "model_copy"):
        return value.model_copy(update=updates)
    return dataclasses.replace(value, **updates)


def _frames():
    assert DecodedVideoFrame is not None
    return tuple(
        DecodedVideoFrame(
            index=index,
            dts=index,
            pts=index,
            duration=1,
            bytes=384,
            sha256=sha256,
        )
        for index, sha256 in enumerate(_FRAME_HASHES)
    )


def _stream(**updates):
    assert DecodedVideoStream is not None
    values = {
        "detected_format": "MP4",
        "codec_name": "h264",
        "pixel_format": "yuv420p",
        "width": 16,
        "height": 16,
        "duration_ms": 3000,
        "fps_numerator": 6,
        "fps_denominator": 1,
        "time_base_numerator": 1,
        "time_base_denominator": 6,
        "probed_frame_count": 18,
        "frames": _frames(),
        "decoder_name": "controlled-complete-decoder",
        "decoder_version": "ffmpeg-6.1.1-fixture-facts-v1",
        "full_decode": True,
    }
    values.update(updates)
    return DecodedVideoStream(**values)


class ControlledCompleteDecoder:
    """Deterministic external decoder boundary, not a production qualification."""

    def __init__(self, stream=None) -> None:
        self.stream = _stream() if stream is None else stream
        self.calls: list[tuple[ArtifactRef, bytes]] = []

    def decode(self, source: ArtifactRef, payload: bytes):
        assert hashlib.sha256(payload).hexdigest() == _SOURCE_SHA256
        self.calls.append((source, payload))
        return self.stream


def _put_source(store: LocalArtifactStore, root: Path, *, media_type: str = "video/mp4"):
    path = root / "fixed-18-frame.mp4"
    path.write_bytes(_FIXED_MP4)
    source = store.put_file(
        path,
        name=path.name,
        media_type=media_type,
        schema_version=None,
    )
    assert source.sha256 == _SOURCE_SHA256
    return source


def _manifest(store: LocalArtifactStore):
    return tuple(
        (
            str(path.relative_to(store.root)),
            path.stat().st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted((store.root / "sha256").glob("*/*"))
    )


def _ingestor(store: LocalArtifactStore, decoder=None, **limits):
    assert VideoMediaIngestor is not None
    return VideoMediaIngestor(
        artifact_store=store,
        decoder=ControlledCompleteDecoder() if decoder is None else decoder,
        max_source_bytes=limits.pop("max_source_bytes", 16 * 1024),
        max_decoded_pixels=limits.pop("max_decoded_pixels", 1024),
        max_decoded_frames=limits.pop("max_decoded_frames", 100),
        **limits,
    )


def _consumer(store: LocalArtifactStore, decoder=None):
    assert VideoMediaConsumerVerifier is not None
    return VideoMediaConsumerVerifier(
        artifact_store=store,
        decoder=ControlledCompleteDecoder() if decoder is None else decoder,
        max_source_bytes=16 * 1024,
        max_decoded_pixels=1024,
        max_decoded_frames=100,
    )


def _canonical_json(model) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def test_video_ingest_public_seam_and_strict_evidence_schemas_are_available() -> None:
    missing = []
    for name, symbol in (
        ("DecodedVideoFrame", DecodedVideoFrame),
        ("DecodedVideoStream", DecodedVideoStream),
        ("VideoContainerDecoder", VideoContainerDecoder),
        ("VideoMediaConsumerVerifier", VideoMediaConsumerVerifier),
        ("VideoMediaIngestError", VideoMediaIngestError),
        ("VideoMediaIngestor", VideoMediaIngestor),
        ("VideoDecodedFrameSequenceV1", VideoDecodedFrameSequenceV1),
        ("VideoMediaProbeV1", VideoMediaProbeV1),
        ("VideoSourceMediaV2", VideoSourceMediaV2),
    ):
        if symbol is None:
            missing.append(name)
        elif getattr(public_harness, name, None) is not symbol:
            missing.append(f"self_improving.harness.{name}")
    for schema_id, model in (
        (_SEQUENCE_SCHEMA_ID, VideoDecodedFrameSequenceV1),
        (_PROBE_SCHEMA_ID, VideoMediaProbeV1),
        (_SOURCE_MEDIA_SCHEMA_ID, VideoSourceMediaV2),
    ):
        try:
            catalog_model = schema_model(schema_id)
        except KeyError:
            missing.append(schema_id)
        else:
            if catalog_model is not model:
                missing.append(f"{schema_id} exact catalog identity")
    assert not missing, "missing accepted X1 video ingest seam: " + ", ".join(missing)


@_requires_video_ingest
def test_video_ingest_publishes_full_ordered_sequence_probe_and_source_media(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    source = _put_source(store, tmp_path)
    decoder = ControlledCompleteDecoder()

    media = _ingestor(store, decoder).ingest(source)

    assert type(media) is VideoSourceMediaV2
    assert type(media) is not public_schemas.VideoSourceMedia
    assert media.source == source
    assert media.source_media_sha256 == _SOURCE_SHA256
    assert (media.width, media.height) == (16, 16)
    assert (media.fps_numerator, media.fps_denominator) == (6, 1)
    assert media.duration_ms == 3000
    assert media.decoded_frame_count == 18
    assert media.unique_frame_count == 6
    assert media.decoded_frame_sequence_sha256 == _SEQUENCE_SHA256
    assert media.decoded_frame_sequence_manifest.schema_version == _SEQUENCE_SCHEMA_ID
    assert media.probe.schema_version == _PROBE_SCHEMA_ID
    assert len(decoder.calls) == 1
    assert decoder.calls[0] == (source, _FIXED_MP4)

    manifest_payload = store.resolve(media.decoded_frame_sequence_manifest).path.read_bytes()
    probe_payload = store.resolve(media.probe).path.read_bytes()
    manifest = VideoDecodedFrameSequenceV1.model_validate_json(manifest_payload)
    probe = VideoMediaProbeV1.model_validate_json(probe_payload)
    assert manifest_payload == _canonical_json(manifest)
    assert probe_payload == _canonical_json(probe)
    assert manifest.source == source
    assert manifest.full_decode is True
    assert (manifest.time_base_numerator, manifest.time_base_denominator) == (1, 6)
    assert tuple(frame.index for frame in manifest.frames) == tuple(range(18))
    assert tuple(frame.pts for frame in manifest.frames) == tuple(range(18))
    assert tuple(frame.sha256 for frame in manifest.frames) == _FRAME_HASHES
    assert len(manifest.frames) == manifest.decoded_frame_count == 18
    assert manifest.unique_frame_count == 6
    assert manifest.decoded_frame_sequence_sha256 == _SEQUENCE_SHA256
    assert probe.source == source
    assert probe.decoded_frame_sequence_manifest == media.decoded_frame_sequence_manifest
    assert probe.decoded_frame_sequence_sha256 == _SEQUENCE_SHA256
    assert probe.full_decode is True
    assert probe.decoded_frame_count == 18
    assert probe.unique_frame_count == 6
    assert probe.duration_ms == 3000


@_requires_video_ingest
def test_video_ingest_retry_is_content_idempotent_with_a_fresh_ingestor(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    source = _put_source(store, tmp_path)
    first = _ingestor(store).ingest(source)
    before = _manifest(store)

    second = _ingestor(store).ingest(source)

    assert second == first
    assert _manifest(store) == before


@_requires_video_ingest
def test_video_media_consumer_fully_redecodes_and_is_pure_read(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    source = _put_source(store, tmp_path)
    media = _ingestor(store).ingest(source)
    expected_probe = VideoMediaProbeV1.model_validate_json(
        store.resolve(media.probe).path.read_bytes()
    )
    decoder = ControlledCompleteDecoder()
    before = _manifest(store)

    assert _consumer(store, decoder).verify(media) == expected_probe
    assert decoder.calls == [(source, _FIXED_MP4)]
    assert _manifest(store) == before


@_requires_video_ingest
@pytest.mark.parametrize(
    ("mutation", "code"),
    (
        ({"frames": ()}, "HARN_VIDEO_EMPTY"),
        ({"frames": _FRAME_HASHES[:15]}, "HARN_VIDEO_INCOMPLETE_DECODE"),
        ({"probed_frame_count": 19}, "HARN_VIDEO_INCOMPLETE_DECODE"),
        ({"full_decode": False}, "HARN_VIDEO_INCOMPLETE_DECODE"),
        ({"frames": "reversed"}, "HARN_VIDEO_SEQUENCE_INVALID"),
        ({"fps_denominator": 0}, "HARN_VIDEO_TIMING_INVALID"),
        ({"duration_ms": 2999}, "HARN_VIDEO_TIMING_INVALID"),
    ),
)
def test_video_ingest_rejects_empty_sampled_unordered_or_timing_drift(
    tmp_path: Path,
    mutation: dict[str, object],
    code: str,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    source = _put_source(store, tmp_path)
    updates = dict(mutation)
    if updates.get("frames") == _FRAME_HASHES[:15]:
        updates["frames"] = _frames()[:15]
    elif updates.get("frames") == "reversed":
        updates["frames"] = tuple(reversed(_frames()))
    elif updates.get("frames") == ():
        updates["probed_frame_count"] = 0
    decoder = ControlledCompleteDecoder(_stream(**updates))
    before = _manifest(store)

    with pytest.raises(VideoMediaIngestError) as captured:
        _ingestor(store, decoder).ingest(source)

    assert captured.value.code == code
    assert _manifest(store) == before


@_requires_video_ingest
@pytest.mark.parametrize(
    ("limits", "code"),
    (
        ({"max_source_bytes": len(_FIXED_MP4) - 1}, "HARN_VIDEO_LIMIT_EXCEEDED"),
        ({"max_decoded_pixels": 255}, "HARN_VIDEO_LIMIT_EXCEEDED"),
        ({"max_decoded_frames": 17}, "HARN_VIDEO_LIMIT_EXCEEDED"),
    ),
)
def test_video_ingest_applies_source_pixel_and_full_frame_limits_before_publish(
    tmp_path: Path,
    limits: dict[str, int],
    code: str,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    source = _put_source(store, tmp_path)
    before = _manifest(store)

    with pytest.raises(VideoMediaIngestError) as captured:
        _ingestor(store, **limits).ingest(source)

    assert captured.value.code == code
    assert _manifest(store) == before


@_requires_video_ingest
def test_video_ingest_rejects_declared_media_type_drift_without_derivatives(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    source = _put_source(store, tmp_path, media_type="video/webm")
    before = _manifest(store)

    with pytest.raises(VideoMediaIngestError) as captured:
        _ingestor(store).ingest(source)

    assert captured.value.code == "HARN_MEDIA_TYPE_MISMATCH"
    assert _manifest(store) == before


@_requires_video_ingest
def test_video_media_consumer_rejects_all_identity_and_mirrored_field_drift_purely(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    source = _put_source(store, tmp_path)
    media = _ingestor(store).ingest(source)
    manifest_payload = store.resolve(media.decoded_frame_sequence_manifest).path.read_bytes()
    probe_payload = store.resolve(media.probe).path.read_bytes()
    noncanonical_manifest = json.dumps(json.loads(manifest_payload), indent=2).encode("utf-8")
    noncanonical_probe = json.dumps(json.loads(probe_payload), indent=2).encode("utf-8")
    manifest_path = tmp_path / "noncanonical-manifest.json"
    manifest_path.write_bytes(noncanonical_manifest)
    manifest_ref = store.put_file(
        manifest_path,
        name=manifest_path.name,
        media_type="application/json",
        schema_version=_SEQUENCE_SCHEMA_ID,
    )
    probe_path = tmp_path / "noncanonical-probe.json"
    probe_path.write_bytes(noncanonical_probe)
    probe_ref = store.put_file(
        probe_path,
        name=probe_path.name,
        media_type="application/json",
        schema_version=_PROBE_SCHEMA_ID,
    )
    same_bytes_different_name = source.model_copy(update={"name": "renamed-source.mp4"})
    missing_sha = "0" * 64
    missing_manifest = media.decoded_frame_sequence_manifest.model_copy(
        update={
            "sha256": missing_sha,
            "uri": f"artifact://sha256/{missing_sha}",
        }
    )
    mutations = {
        "source": {"source": same_bytes_different_name},
        "manifest": {"decoded_frame_sequence_manifest": manifest_ref},
        "probe": {"probe": probe_ref},
        "width": {"width": 17},
        "height": {"height": 17},
        "duration": {"duration_ms": 2999},
        "fps": {"fps_numerator": 5},
        "decoded": {"decoded_frame_count": 17},
        "unique": {"unique_frame_count": 5},
        "sequence": {"decoded_frame_sequence_sha256": "f" * 64},
        "missing_cas": {"decoded_frame_sequence_manifest": missing_manifest},
    }
    before = _manifest(store)
    for label, mutation in mutations.items():
        drifted = media.model_copy(update=mutation)
        with pytest.raises(VideoMediaIngestError) as captured:
            _consumer(store).verify(drifted)
        assert captured.value.code == (
            "HARN_ARTIFACT_UNAVAILABLE"
            if label == "missing_cas"
            else "HARN_VIDEO_EVIDENCE_MISMATCH"
        )
        assert _manifest(store) == before


@_requires_video_ingest
def test_video_consumer_rejects_cas_content_tamper_without_publishing(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    source = _put_source(store, tmp_path)
    media = _ingestor(store).ingest(source)
    probe_path = store.resolve(media.probe).path
    before_paths = tuple(
        path.relative_to(store.root) for path in (store.root / "sha256").glob("*/*")
    )
    probe_path.write_bytes(b"{}\n")

    with pytest.raises(VideoMediaIngestError) as captured:
        _consumer(store).verify(media)

    after_paths = tuple(
        path.relative_to(store.root) for path in (store.root / "sha256").glob("*/*")
    )
    assert captured.value.code == "HARN_ARTIFACT_UNAVAILABLE"
    assert sorted(after_paths) == sorted(before_paths)


@_requires_video_ingest
def test_video_evidence_schemas_forbid_extra_fields_at_every_level(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    source = _put_source(store, tmp_path)
    media = _ingestor(store).ingest(source)
    manifest = json.loads(store.resolve(media.decoded_frame_sequence_manifest).path.read_bytes())
    probe = json.loads(store.resolve(media.probe).path.read_bytes())

    with pytest.raises(ValidationError):
        VideoDecodedFrameSequenceV1.model_validate(
            {**manifest, "sampled_frame_indices": list(range(15))}
        )
    nested_extra = json.loads(json.dumps(manifest))
    nested_extra["frames"][0]["sample_only"] = True
    with pytest.raises(ValidationError):
        VideoDecodedFrameSequenceV1.model_validate(nested_extra)
    with pytest.raises(ValidationError):
        VideoMediaProbeV1.model_validate({**probe, "legacy_probe_passed": True})
