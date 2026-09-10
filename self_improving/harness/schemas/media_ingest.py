"""Strict public evidence emitted by deterministic media ingestion."""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from .base import HarnessModel, NonEmptyString, PositiveInt, Sha256, public_schema_config
from .common import ArtifactRef

IMAGE_MEDIA_PROBE_SCHEMA_ID = "harness.image_media_probe.v1"

_FORMAT_MEDIA_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


def _require_cas_ref(
    ref: ArtifactRef,
    *,
    label: str,
    media_type: str | None = None,
) -> None:
    if ref.uri != f"artifact://sha256/{ref.sha256}":
        raise ValueError(f"{label} must use its content-addressed CAS URI")
    if media_type is not None and ref.media_type != media_type:
        raise ValueError(f"{label} must use media type {media_type!r}")


class ImageMediaProbeV1(HarnessModel):
    """Decoder-derived identity binding a source image to its canonical PNG."""

    model_config = public_schema_config(IMAGE_MEDIA_PROBE_SCHEMA_ID)

    schema_version: Literal[IMAGE_MEDIA_PROBE_SCHEMA_ID]
    source: ArtifactRef
    detected_format: Literal["JPEG", "PNG", "WEBP"]
    declared_media_type: Literal["image/jpeg", "image/png", "image/webp"]
    width: PositiveInt
    height: PositiveInt
    color_mode: Literal["RGB", "RGBA", "L"]
    decoded_frame_count: Literal[1]
    unique_frame_count: Literal[1]
    canonical_image: ArtifactRef
    pixel_sha256: Sha256
    decoder_name: Literal["Pillow"]
    decoder_version: NonEmptyString

    @model_validator(mode="after")
    def identities_are_bound(self) -> "ImageMediaProbeV1":
        _require_cas_ref(self.source, label="source")
        if self.source.schema_version is not None:
            raise ValueError("source must be raw media without a schema version")
        _require_cas_ref(
            self.canonical_image,
            label="canonical_image",
            media_type="image/png",
        )
        if self.canonical_image.schema_version is not None:
            raise ValueError("canonical_image must be raw media without a schema version")
        if self.declared_media_type != _FORMAT_MEDIA_TYPES[self.detected_format]:
            raise ValueError("declared media type does not match the detected image format")
        return self


__all__ = ["IMAGE_MEDIA_PROBE_SCHEMA_ID", "ImageMediaProbeV1"]
