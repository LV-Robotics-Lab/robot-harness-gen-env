"""Strict evidence for complete, ordered source-video decoding."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Literal

from pydantic import Field, model_validator

from .base import HarnessModel, NonEmptyString, PositiveInt, Sha256, public_schema_config
from .common import ArtifactRef

VIDEO_DECODED_FRAME_SEQUENCE_SCHEMA_ID = "harness.video_decoded_frame_sequence.v1"
VIDEO_MEDIA_PROBE_SCHEMA_ID = "harness.video_media_probe.v1"
VIDEO_SOURCE_MEDIA_V2_SCHEMA_ID = "harness.video_source_media.v2"
_SEQUENCE_IDENTITY_SCHEMA = "harness.video_decoded_frame_sequence_identity.v1"


def _require_cas_ref(
    ref: ArtifactRef,
    *,
    label: str,
    media_type: str | None = None,
    schema_version: str | None = None,
) -> None:
    if ref.uri != f"artifact://sha256/{ref.sha256}":
        raise ValueError(f"{label} must use its content-addressed CAS URI")
    if media_type is not None and ref.media_type != media_type:
        raise ValueError(f"{label} must use media type {media_type!r}")
    if schema_version is not None and ref.schema_version != schema_version:
        raise ValueError(f"{label} must use schema version {schema_version!r}")


def _duration_ms(frame_count: int, numerator: int, denominator: int) -> int:
    return (frame_count * numerator * 1000 + denominator - 1) // denominator


class VideoDecodedFrameV1(HarnessModel):
    """One decoded raw-frame identity in presentation order."""

    index: int = Field(strict=True, ge=0)
    dts: int = Field(strict=True, ge=0)
    pts: int = Field(strict=True, ge=0)
    duration: PositiveInt
    bytes: PositiveInt
    sha256: Sha256


def decoded_frame_sequence_sha256(
    *,
    time_base_numerator: int,
    time_base_denominator: int,
    frames: tuple[VideoDecodedFrameV1, ...],
) -> str:
    """Hash the ordered frame identity independently of its enclosing manifest."""

    body = {
        "schema_version": _SEQUENCE_IDENTITY_SCHEMA,
        "time_base_numerator": time_base_numerator,
        "time_base_denominator": time_base_denominator,
        "frames": [frame.model_dump(mode="json") for frame in frames],
    }
    payload = (
        json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


class VideoDecodedFrameSequenceV1(HarnessModel):
    """Canonical manifest for every frame decoded from one source container."""

    model_config = public_schema_config(VIDEO_DECODED_FRAME_SEQUENCE_SCHEMA_ID)

    schema_version: Literal[VIDEO_DECODED_FRAME_SEQUENCE_SCHEMA_ID]
    source: ArtifactRef
    detected_format: Literal["MP4"]
    codec_name: Literal["h264"]
    pixel_format: Literal["yuv420p"]
    width: PositiveInt
    height: PositiveInt
    time_base_numerator: PositiveInt
    time_base_denominator: PositiveInt
    fps_numerator: PositiveInt
    fps_denominator: PositiveInt
    duration_ms: PositiveInt
    frames: tuple[VideoDecodedFrameV1, ...] = Field(min_length=1)
    decoded_frame_count: PositiveInt
    unique_frame_count: PositiveInt
    decoded_frame_sequence_sha256: Sha256
    decoder_name: NonEmptyString
    decoder_version: NonEmptyString
    full_decode: Literal[True]

    @model_validator(mode="after")
    def sequence_is_complete_and_self_hashed(self) -> "VideoDecodedFrameSequenceV1":
        _require_cas_ref(self.source, label="source", media_type="video/mp4")
        if self.source.schema_version is not None:
            raise ValueError("source must be raw media without a schema version")
        if self.width % 2 or self.height % 2:
            raise ValueError("yuv420p dimensions must be even")
        if math.gcd(self.time_base_numerator, self.time_base_denominator) != 1:
            raise ValueError("time base must be a reduced positive fraction")
        if math.gcd(self.fps_numerator, self.fps_denominator) != 1:
            raise ValueError("fps must be a reduced positive fraction")
        if (
            self.time_base_numerator * self.fps_numerator
            != self.time_base_denominator * self.fps_denominator
        ):
            raise ValueError("time base and fps must be exact reciprocals")
        expected_bytes = self.width * self.height * 3 // 2
        for index, frame in enumerate(self.frames):
            if (
                frame.index != index
                or frame.dts != index
                or frame.pts != index
                or frame.duration != 1
            ):
                raise ValueError("frames must be complete, ordered, unit-duration CFR evidence")
            if frame.bytes != expected_bytes:
                raise ValueError("frame byte count must match yuv420p dimensions")
        if self.decoded_frame_count != len(self.frames):
            raise ValueError("decoded_frame_count must equal the complete frame sequence")
        if self.unique_frame_count != len({frame.sha256 for frame in self.frames}):
            raise ValueError("unique_frame_count must equal unique decoded frame identities")
        if self.duration_ms != _duration_ms(
            len(self.frames),
            self.time_base_numerator,
            self.time_base_denominator,
        ):
            raise ValueError("duration_ms must equal the complete decoded CFR duration")
        if self.decoded_frame_sequence_sha256 != decoded_frame_sequence_sha256(
            time_base_numerator=self.time_base_numerator,
            time_base_denominator=self.time_base_denominator,
            frames=self.frames,
        ):
            raise ValueError("decoded_frame_sequence_sha256 does not match the ordered frames")
        return self


class VideoMediaProbeV1(HarnessModel):
    """Decoder-derived facts binding a source video to its complete sequence manifest."""

    model_config = public_schema_config(VIDEO_MEDIA_PROBE_SCHEMA_ID)

    schema_version: Literal[VIDEO_MEDIA_PROBE_SCHEMA_ID]
    source: ArtifactRef
    detected_format: Literal["MP4"]
    declared_media_type: Literal["video/mp4"]
    codec_name: Literal["h264"]
    pixel_format: Literal["yuv420p"]
    video_stream_count: Literal[1]
    width: PositiveInt
    height: PositiveInt
    duration_ms: PositiveInt
    time_base_numerator: PositiveInt
    time_base_denominator: PositiveInt
    fps_numerator: PositiveInt
    fps_denominator: PositiveInt
    decoded_frame_count: PositiveInt
    unique_frame_count: PositiveInt
    decoded_frame_sequence_manifest: ArtifactRef
    decoded_frame_sequence_sha256: Sha256
    decoder_name: NonEmptyString
    decoder_version: NonEmptyString
    full_decode: Literal[True]

    @model_validator(mode="after")
    def probe_refs_and_counts_are_bound(self) -> "VideoMediaProbeV1":
        _require_cas_ref(self.source, label="source", media_type=self.declared_media_type)
        if self.source.schema_version is not None:
            raise ValueError("source must be raw media without a schema version")
        if self.width % 2 or self.height % 2:
            raise ValueError("yuv420p dimensions must be even")
        _require_cas_ref(
            self.decoded_frame_sequence_manifest,
            label="decoded_frame_sequence_manifest",
            media_type="application/json",
            schema_version=VIDEO_DECODED_FRAME_SEQUENCE_SCHEMA_ID,
        )
        if self.unique_frame_count > self.decoded_frame_count:
            raise ValueError("unique_frame_count cannot exceed decoded_frame_count")
        if (
            math.gcd(self.time_base_numerator, self.time_base_denominator) != 1
            or math.gcd(self.fps_numerator, self.fps_denominator) != 1
            or self.time_base_numerator * self.fps_numerator
            != self.time_base_denominator * self.fps_denominator
        ):
            raise ValueError("probe timing fractions must be reduced exact reciprocals")
        if self.duration_ms != _duration_ms(
            self.decoded_frame_count,
            self.time_base_numerator,
            self.time_base_denominator,
        ):
            raise ValueError("probe duration must equal its complete decoded sequence")
        return self


class VideoSourceMediaV2(HarnessModel):
    """Public source-video identity backed by a complete decoded sequence."""

    model_config = public_schema_config(VIDEO_SOURCE_MEDIA_V2_SCHEMA_ID)

    schema_version: Literal[VIDEO_SOURCE_MEDIA_V2_SCHEMA_ID]
    source_media_sha256: Sha256
    source: ArtifactRef
    probe: ArtifactRef
    decoded_frame_sequence_manifest: ArtifactRef
    width: PositiveInt
    height: PositiveInt
    duration_ms: PositiveInt
    time_base_numerator: PositiveInt
    time_base_denominator: PositiveInt
    fps_numerator: PositiveInt
    fps_denominator: PositiveInt
    decoded_frame_count: PositiveInt
    unique_frame_count: PositiveInt
    decoded_frame_sequence_sha256: Sha256

    @model_validator(mode="after")
    def identity_is_structurally_verifiable(self) -> "VideoSourceMediaV2":
        _require_cas_ref(self.source, label="source", media_type="video/mp4")
        if self.source.schema_version is not None:
            raise ValueError("source must be raw media without a schema version")
        if self.width % 2 or self.height % 2:
            raise ValueError("yuv420p dimensions must be even")
        _require_cas_ref(
            self.probe,
            label="probe",
            media_type="application/json",
            schema_version=VIDEO_MEDIA_PROBE_SCHEMA_ID,
        )
        _require_cas_ref(
            self.decoded_frame_sequence_manifest,
            label="decoded_frame_sequence_manifest",
            media_type="application/json",
            schema_version=VIDEO_DECODED_FRAME_SEQUENCE_SCHEMA_ID,
        )
        if self.source_media_sha256 != self.source.sha256:
            raise ValueError("source_media_sha256 must equal source.sha256")
        if self.unique_frame_count > self.decoded_frame_count:
            raise ValueError("unique_frame_count cannot exceed decoded_frame_count")
        if (
            math.gcd(self.time_base_numerator, self.time_base_denominator) != 1
            or math.gcd(self.fps_numerator, self.fps_denominator) != 1
            or self.time_base_numerator * self.fps_numerator
            != self.time_base_denominator * self.fps_denominator
        ):
            raise ValueError("source media timing fractions must be reduced exact reciprocals")
        if self.duration_ms != _duration_ms(
            self.decoded_frame_count,
            self.time_base_numerator,
            self.time_base_denominator,
        ):
            raise ValueError("source media duration must equal its complete decoded sequence")
        return self


__all__ = [
    "VIDEO_DECODED_FRAME_SEQUENCE_SCHEMA_ID",
    "VIDEO_MEDIA_PROBE_SCHEMA_ID",
    "VIDEO_SOURCE_MEDIA_V2_SCHEMA_ID",
    "VideoDecodedFrameSequenceV1",
    "VideoDecodedFrameV1",
    "VideoMediaProbeV1",
    "VideoSourceMediaV2",
    "decoded_frame_sequence_sha256",
]
