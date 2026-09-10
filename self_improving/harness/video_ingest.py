"""Complete, deterministic source-video evidence ingestion and consumption."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol, runtime_checkable

from pydantic import ValidationError

from .artifacts import ArtifactResolutionError, LocalArtifactStore
from .schemas.common import ArtifactRef
from .schemas.video_ingest import (
    VIDEO_DECODED_FRAME_SEQUENCE_SCHEMA_ID,
    VIDEO_MEDIA_PROBE_SCHEMA_ID,
    VideoDecodedFrameSequenceV1,
    VideoDecodedFrameV1,
    VideoMediaProbeV1,
    VideoSourceMediaV2,
    decoded_frame_sequence_sha256,
)


class VideoMediaIngestError(ValueError):
    """Stable source-video failure without leaking local paths or decoder output."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class DecodedVideoFrame:
    """One raw frame reported by a complete external decoder boundary."""

    index: int
    dts: int
    pts: int
    duration: int
    bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class DecodedVideoStream:
    """All decoder-derived facts needed by the controlled MP4/H.264/CFR slice."""

    detected_format: str
    codec_name: str
    pixel_format: str
    width: int
    height: int
    duration_ms: int
    fps_numerator: int
    fps_denominator: int
    time_base_numerator: int
    time_base_denominator: int
    probed_frame_count: int
    frames: tuple[DecodedVideoFrame, ...]
    decoder_name: str
    decoder_version: str
    full_decode: bool


@runtime_checkable
class VideoContainerDecoder(Protocol):
    """External full-decoder seam; production implementations require qualification."""

    def decode(self, source: ArtifactRef, payload: bytes) -> DecodedVideoStream: ...


@dataclass(frozen=True, slots=True)
class VideoMediaIngestor:
    """Publish complete ordered source-video evidence into the Harness CAS."""

    artifact_store: LocalArtifactStore
    decoder: VideoContainerDecoder
    max_source_bytes: int
    max_decoded_pixels: int
    max_decoded_frames: int

    def __post_init__(self) -> None:
        _validate_configuration(
            artifact_store=self.artifact_store,
            decoder=self.decoder,
            max_source_bytes=self.max_source_bytes,
            max_decoded_pixels=self.max_decoded_pixels,
            max_decoded_frames=self.max_decoded_frames,
        )

    def ingest(self, source: ArtifactRef) -> VideoSourceMediaV2:
        if type(source) is not ArtifactRef:
            raise TypeError("source must be the exact ArtifactRef")
        _validate_source_contract(source, max_source_bytes=self.max_source_bytes)
        payload = _read_verified_artifact(self.artifact_store, source)
        stream = _decode_complete(
            self.decoder,
            source,
            payload,
            max_decoded_pixels=self.max_decoded_pixels,
            max_decoded_frames=self.max_decoded_frames,
        )
        sequence = _sequence(source, stream)
        sequence_payload = _canonical_json_bytes(sequence.model_dump(mode="json"))

        with TemporaryDirectory(prefix="harness-video-ingest-") as temporary_root:
            root = Path(temporary_root)
            sequence_path = root / "decoded-frame-sequence.json"
            sequence_path.write_bytes(sequence_payload)
            sequence_ref = self.artifact_store.put_file(
                sequence_path,
                name=f"{source.sha256}.decoded-frame-sequence.json",
                media_type="application/json",
                schema_version=VIDEO_DECODED_FRAME_SEQUENCE_SCHEMA_ID,
            )
            probe = _probe(source, stream, sequence_ref, sequence.decoded_frame_sequence_sha256)
            probe_path = root / "probe.json"
            probe_path.write_bytes(_canonical_json_bytes(probe.model_dump(mode="json")))
            probe_ref = self.artifact_store.put_file(
                probe_path,
                name=f"{source.sha256}.video-probe.json",
                media_type="application/json",
                schema_version=VIDEO_MEDIA_PROBE_SCHEMA_ID,
            )
        return _source_media(source, stream, sequence_ref, probe_ref)


@dataclass(frozen=True, slots=True)
class VideoMediaConsumerVerifier:
    """Pure-read verification of source, full sequence, probe, and mirrored fields."""

    artifact_store: LocalArtifactStore
    decoder: VideoContainerDecoder
    max_source_bytes: int
    max_decoded_pixels: int
    max_decoded_frames: int

    def __post_init__(self) -> None:
        _validate_configuration(
            artifact_store=self.artifact_store,
            decoder=self.decoder,
            max_source_bytes=self.max_source_bytes,
            max_decoded_pixels=self.max_decoded_pixels,
            max_decoded_frames=self.max_decoded_frames,
        )

    def verify(self, media: VideoSourceMediaV2) -> VideoMediaProbeV1:
        if type(media) is not VideoSourceMediaV2:
            raise TypeError("media must be the exact VideoSourceMediaV2")
        _validate_source_contract(media.source, max_source_bytes=self.max_source_bytes)
        payload = _read_verified_artifact(self.artifact_store, media.source)
        stream = _decode_complete(
            self.decoder,
            media.source,
            payload,
            max_decoded_pixels=self.max_decoded_pixels,
            max_decoded_frames=self.max_decoded_frames,
        )
        sequence = _sequence(media.source, stream)
        sequence_payload = _canonical_json_bytes(sequence.model_dump(mode="json"))
        expected_sequence_ref = _artifact_ref_for_payload(
            sequence_payload,
            name=f"{media.source.sha256}.decoded-frame-sequence.json",
            media_type="application/json",
            schema_version=VIDEO_DECODED_FRAME_SEQUENCE_SCHEMA_ID,
        )
        stored_sequence_payload = _read_verified_artifact(
            self.artifact_store,
            media.decoded_frame_sequence_manifest,
        )
        try:
            stored_sequence = VideoDecodedFrameSequenceV1.model_validate_json(
                stored_sequence_payload
            )
        except ValidationError:
            _evidence_mismatch("decoded frame sequence is not strict typed evidence")
        if (
            media.decoded_frame_sequence_manifest != expected_sequence_ref
            or stored_sequence_payload != sequence_payload
            or stored_sequence != sequence
        ):
            _evidence_mismatch("decoded frame sequence does not match a complete source decode")

        expected_probe = _probe(
            media.source,
            stream,
            expected_sequence_ref,
            sequence.decoded_frame_sequence_sha256,
        )
        probe_payload = _canonical_json_bytes(expected_probe.model_dump(mode="json"))
        expected_probe_ref = _artifact_ref_for_payload(
            probe_payload,
            name=f"{media.source.sha256}.video-probe.json",
            media_type="application/json",
            schema_version=VIDEO_MEDIA_PROBE_SCHEMA_ID,
        )
        stored_probe_payload = _read_verified_artifact(self.artifact_store, media.probe)
        try:
            stored_probe = VideoMediaProbeV1.model_validate_json(stored_probe_payload)
        except ValidationError:
            _evidence_mismatch("video probe is not strict typed evidence")
        if (
            media.probe != expected_probe_ref
            or stored_probe_payload != probe_payload
            or stored_probe != expected_probe
        ):
            _evidence_mismatch("video probe does not canonically bind the complete source decode")

        expected_media = _source_media(
            media.source,
            stream,
            expected_sequence_ref,
            expected_probe_ref,
        )
        if media != expected_media:
            _evidence_mismatch("video source media fields do not match decoded evidence")
        return expected_probe


def _validate_configuration(
    *,
    artifact_store: LocalArtifactStore,
    decoder: VideoContainerDecoder,
    max_source_bytes: int,
    max_decoded_pixels: int,
    max_decoded_frames: int,
) -> None:
    if type(artifact_store) is not LocalArtifactStore:
        raise TypeError("artifact_store must be the exact LocalArtifactStore")
    if not isinstance(decoder, VideoContainerDecoder):
        raise TypeError("decoder must implement the VideoContainerDecoder protocol")
    for value, label in (
        (max_source_bytes, "max_source_bytes"),
        (max_decoded_pixels, "max_decoded_pixels"),
        (max_decoded_frames, "max_decoded_frames"),
    ):
        if type(value) is not int or value <= 0:
            raise ValueError(f"{label} must be a positive exact integer")


def _validate_source_contract(source: ArtifactRef, *, max_source_bytes: int) -> None:
    if source.schema_version is not None or source.media_type != "video/mp4":
        raise VideoMediaIngestError(
            "HARN_MEDIA_TYPE_MISMATCH",
            "source video must be raw video/mp4 media",
        )
    if source.bytes > max_source_bytes:
        raise VideoMediaIngestError(
            "HARN_VIDEO_LIMIT_EXCEEDED",
            "source video exceeds the configured byte limit",
        )


def _decode_complete(
    decoder: VideoContainerDecoder,
    source: ArtifactRef,
    payload: bytes,
    *,
    max_decoded_pixels: int,
    max_decoded_frames: int,
) -> DecodedVideoStream:
    try:
        stream = decoder.decode(source, payload)
    except VideoMediaIngestError:
        raise
    except Exception as error:
        raise VideoMediaIngestError(
            "HARN_VIDEO_DECODE_FAILED",
            "source video could not be completely decoded",
        ) from error
    if type(stream) is not DecodedVideoStream:
        raise VideoMediaIngestError(
            "HARN_VIDEO_DECODE_FAILED",
            "decoder returned an invalid source-video result",
        )
    if stream.detected_format != "MP4":
        raise VideoMediaIngestError(
            "HARN_MEDIA_TYPE_MISMATCH",
            "decoded container is not the supported MP4 format",
        )
    if stream.codec_name != "h264" or stream.pixel_format != "yuv420p":
        raise VideoMediaIngestError(
            "HARN_VIDEO_DECODE_FAILED",
            "decoded stream is not the supported H.264/yuv420p profile",
        )
    if not stream.frames:
        raise VideoMediaIngestError("HARN_VIDEO_EMPTY", "source video decoded no frames")
    if (
        type(stream.full_decode) is not bool
        or not stream.full_decode
        or type(stream.probed_frame_count) is not int
        or stream.probed_frame_count != len(stream.frames)
    ):
        raise VideoMediaIngestError(
            "HARN_VIDEO_INCOMPLETE_DECODE",
            "decoder did not return the complete probed frame sequence",
        )
    if len(stream.frames) > max_decoded_frames:
        raise VideoMediaIngestError(
            "HARN_VIDEO_LIMIT_EXCEEDED",
            "decoded video exceeds the configured frame limit",
        )
    if (
        type(stream.width) is not int
        or type(stream.height) is not int
        or stream.width <= 0
        or stream.height <= 0
        or stream.width * stream.height > max_decoded_pixels
    ):
        raise VideoMediaIngestError(
            "HARN_VIDEO_LIMIT_EXCEEDED",
            "decoded video exceeds the configured pixel limit",
        )
    if stream.width % 2 or stream.height % 2:
        raise VideoMediaIngestError(
            "HARN_VIDEO_DECODE_FAILED",
            "decoded yuv420p video dimensions must be even",
        )
    timing = (
        stream.fps_numerator,
        stream.fps_denominator,
        stream.time_base_numerator,
        stream.time_base_denominator,
        stream.duration_ms,
    )
    if any(type(value) is not int or value <= 0 for value in timing):
        raise VideoMediaIngestError(
            "HARN_VIDEO_TIMING_INVALID",
            "decoded video has invalid positive timing values",
        )
    if (
        math.gcd(stream.fps_numerator, stream.fps_denominator) != 1
        or math.gcd(stream.time_base_numerator, stream.time_base_denominator) != 1
    ):
        raise VideoMediaIngestError(
            "HARN_VIDEO_TIMING_INVALID",
            "decoded video timing fractions must be reduced",
        )
    if (
        stream.time_base_numerator * stream.fps_numerator
        != stream.time_base_denominator * stream.fps_denominator
        or stream.duration_ms
        != _duration_ms(
            len(stream.frames),
            stream.time_base_numerator,
            stream.time_base_denominator,
        )
    ):
        raise VideoMediaIngestError(
            "HARN_VIDEO_TIMING_INVALID",
            "decoded video timing is not complete constant-frame-rate evidence",
        )
    expected_frame_bytes = stream.width * stream.height * 3 // 2
    for index, frame in enumerate(stream.frames):
        if type(frame) is not DecodedVideoFrame:
            raise VideoMediaIngestError(
                "HARN_VIDEO_SEQUENCE_INVALID",
                "decoder returned an invalid frame record",
            )
        if (
            type(frame.index) is not int
            or type(frame.dts) is not int
            or type(frame.pts) is not int
            or type(frame.duration) is not int
            or frame.index != index
            or frame.dts != index
            or frame.pts != index
            or frame.duration != 1
            or type(frame.bytes) is not int
            or frame.bytes != expected_frame_bytes
            or type(frame.sha256) is not str
            or len(frame.sha256) != 64
            or any(character not in "0123456789abcdef" for character in frame.sha256)
        ):
            raise VideoMediaIngestError(
                "HARN_VIDEO_SEQUENCE_INVALID",
                "decoded frame sequence is incomplete, unordered, or malformed",
            )
    if (
        type(stream.decoder_name) is not str
        or not stream.decoder_name
        or type(stream.decoder_version) is not str
        or not stream.decoder_version
    ):
        raise VideoMediaIngestError(
            "HARN_VIDEO_DECODE_FAILED",
            "decoder identity is unavailable",
        )
    return stream


def _sequence(source: ArtifactRef, stream: DecodedVideoStream) -> VideoDecodedFrameSequenceV1:
    frames = tuple(
        VideoDecodedFrameV1(
            index=frame.index,
            dts=frame.dts,
            pts=frame.pts,
            duration=frame.duration,
            bytes=frame.bytes,
            sha256=frame.sha256,
        )
        for frame in stream.frames
    )
    sequence_sha256 = decoded_frame_sequence_sha256(
        time_base_numerator=stream.time_base_numerator,
        time_base_denominator=stream.time_base_denominator,
        frames=frames,
    )
    return VideoDecodedFrameSequenceV1(
        schema_version=VIDEO_DECODED_FRAME_SEQUENCE_SCHEMA_ID,
        source=source,
        detected_format="MP4",
        codec_name="h264",
        pixel_format="yuv420p",
        width=stream.width,
        height=stream.height,
        time_base_numerator=stream.time_base_numerator,
        time_base_denominator=stream.time_base_denominator,
        fps_numerator=stream.fps_numerator,
        fps_denominator=stream.fps_denominator,
        duration_ms=stream.duration_ms,
        frames=frames,
        decoded_frame_count=len(frames),
        unique_frame_count=len({frame.sha256 for frame in frames}),
        decoded_frame_sequence_sha256=sequence_sha256,
        decoder_name=stream.decoder_name,
        decoder_version=stream.decoder_version,
        full_decode=True,
    )


def _probe(
    source: ArtifactRef,
    stream: DecodedVideoStream,
    sequence_ref: ArtifactRef,
    sequence_sha256: str,
) -> VideoMediaProbeV1:
    return VideoMediaProbeV1(
        schema_version=VIDEO_MEDIA_PROBE_SCHEMA_ID,
        source=source,
        detected_format="MP4",
        declared_media_type="video/mp4",
        codec_name="h264",
        pixel_format="yuv420p",
        video_stream_count=1,
        width=stream.width,
        height=stream.height,
        duration_ms=stream.duration_ms,
        time_base_numerator=stream.time_base_numerator,
        time_base_denominator=stream.time_base_denominator,
        fps_numerator=stream.fps_numerator,
        fps_denominator=stream.fps_denominator,
        decoded_frame_count=len(stream.frames),
        unique_frame_count=len({frame.sha256 for frame in stream.frames}),
        decoded_frame_sequence_manifest=sequence_ref,
        decoded_frame_sequence_sha256=sequence_sha256,
        decoder_name=stream.decoder_name,
        decoder_version=stream.decoder_version,
        full_decode=True,
    )


def _source_media(
    source: ArtifactRef,
    stream: DecodedVideoStream,
    sequence_ref: ArtifactRef,
    probe_ref: ArtifactRef,
) -> VideoSourceMediaV2:
    frames = tuple(
        VideoDecodedFrameV1(
            index=frame.index,
            dts=frame.dts,
            pts=frame.pts,
            duration=frame.duration,
            bytes=frame.bytes,
            sha256=frame.sha256,
        )
        for frame in stream.frames
    )
    sequence_sha256 = decoded_frame_sequence_sha256(
        time_base_numerator=stream.time_base_numerator,
        time_base_denominator=stream.time_base_denominator,
        frames=frames,
    )
    return VideoSourceMediaV2(
        schema_version="harness.video_source_media.v2",
        source_media_sha256=source.sha256,
        source=source,
        probe=probe_ref,
        decoded_frame_sequence_manifest=sequence_ref,
        width=stream.width,
        height=stream.height,
        duration_ms=stream.duration_ms,
        time_base_numerator=stream.time_base_numerator,
        time_base_denominator=stream.time_base_denominator,
        fps_numerator=stream.fps_numerator,
        fps_denominator=stream.fps_denominator,
        decoded_frame_count=len(frames),
        unique_frame_count=len({frame.sha256 for frame in frames}),
        decoded_frame_sequence_sha256=sequence_sha256,
    )


def _duration_ms(frame_count: int, numerator: int, denominator: int) -> int:
    return (frame_count * numerator * 1000 + denominator - 1) // denominator


def _read_verified_artifact(store: LocalArtifactStore, ref: ArtifactRef) -> bytes:
    try:
        resolved = store.resolve(ref)
        payload = resolved.path.read_bytes()
    except (ArtifactResolutionError, OSError) as error:
        raise VideoMediaIngestError(
            "HARN_ARTIFACT_UNAVAILABLE",
            "video evidence is unavailable or failed content verification",
        ) from error
    if (  # pragma: no cover - concurrent external CAS tamper defense
        len(payload) != ref.bytes or hashlib.sha256(payload).hexdigest() != ref.sha256
    ):
        raise VideoMediaIngestError(
            "HARN_ARTIFACT_UNAVAILABLE",
            "video evidence changed during verified consumption",
        )
    return payload


def _artifact_ref_for_payload(
    payload: bytes,
    *,
    name: str,
    media_type: str,
    schema_version: str | None,
) -> ArtifactRef:
    digest = hashlib.sha256(payload).hexdigest()
    return ArtifactRef(
        name=name,
        uri=f"artifact://sha256/{digest}",
        media_type=media_type,
        sha256=digest,
        bytes=len(payload),
        schema_version=schema_version,
    )


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _evidence_mismatch(message: str) -> None:
    raise VideoMediaIngestError("HARN_VIDEO_EVIDENCE_MISMATCH", message)


__all__ = [
    "DecodedVideoFrame",
    "DecodedVideoStream",
    "VideoContainerDecoder",
    "VideoMediaConsumerVerifier",
    "VideoMediaIngestError",
    "VideoMediaIngestor",
]
