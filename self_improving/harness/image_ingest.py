"""Deterministic, bounded ingestion of one CAS image into canonical evidence."""

from __future__ import annotations

import hashlib
import json
import warnings
from dataclasses import dataclass
from importlib.metadata import version
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError

from .artifacts import ArtifactResolutionError, LocalArtifactStore
from .schemas.common import ArtifactRef
from .schemas.media_ingest import ImageMediaProbeV1
from .schemas.x2env import ImageSourceMedia

_FORMAT_MEDIA_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


class ImageMediaIngestError(ValueError):
    """Stable image-ingest failure without leaking local CAS paths."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ImageMediaIngestor:
    """Decode one immutable source, publish a canonical PNG, and bind a strict probe."""

    artifact_store: LocalArtifactStore
    max_source_bytes: int
    max_decoded_pixels: int

    def __post_init__(self) -> None:
        _validate_configuration(
            self.artifact_store,
            max_source_bytes=self.max_source_bytes,
            max_decoded_pixels=self.max_decoded_pixels,
        )

    def ingest(self, source: ArtifactRef) -> ImageSourceMedia:
        """Return content-identical evidence for repeated ingestion of the same source."""

        if type(source) is not ArtifactRef:
            raise TypeError("source must be the exact ArtifactRef")
        if source.schema_version is not None:
            raise ImageMediaIngestError(
                "HARN_MEDIA_TYPE_MISMATCH",
                "image source must be raw media without a schema version",
            )
        if source.bytes > self.max_source_bytes:
            raise ImageMediaIngestError(
                "HARN_IMAGE_LIMIT_EXCEEDED",
                "image source exceeds the configured byte limit",
            )
        payload = _read_verified_artifact(self.artifact_store, source)
        detected_format, normalized = _decode_image(
            source,
            payload,
            max_decoded_pixels=self.max_decoded_pixels,
        )
        canonical_payload = _canonical_png(normalized)

        with TemporaryDirectory(prefix="harness-image-ingest-") as temporary_root:
            root = Path(temporary_root)
            canonical_path = root / "canonical.png"
            canonical_path.write_bytes(canonical_payload)
            canonical_ref = self.artifact_store.put_file(
                canonical_path,
                name=f"{source.sha256}.canonical.png",
                media_type="image/png",
                schema_version=None,
            )
            probe = _image_probe(
                source=source,
                detected_format=detected_format,
                canonical_image=canonical_ref,
                normalized=normalized,
            )
            probe_path = root / "probe.json"
            probe_path.write_bytes(_canonical_json_bytes(probe.model_dump(mode="json")))
            probe_ref = self.artifact_store.put_file(
                probe_path,
                name=f"{source.sha256}.image-probe.json",
                media_type="application/json",
                schema_version="harness.image_media_probe.v1",
            )

        return ImageSourceMedia(
            schema_version="harness.image_source_media.v1",
            source_media_sha256=source.sha256,
            source=source,
            canonical_image=canonical_ref,
            probe=probe_ref,
            width=normalized.width,
            height=normalized.height,
            color_mode=normalized.mode,
            decoded_frame_count=1,
            unique_frame_count=1,
        )


@dataclass(frozen=True, slots=True)
class ImageMediaConsumerVerifier:
    """Pure-read consumer verification for a complete ``ImageSourceMedia`` closure."""

    artifact_store: LocalArtifactStore
    max_source_bytes: int
    max_decoded_pixels: int

    def __post_init__(self) -> None:
        _validate_configuration(
            self.artifact_store,
            max_source_bytes=self.max_source_bytes,
            max_decoded_pixels=self.max_decoded_pixels,
        )

    def verify(self, media: ImageSourceMedia) -> ImageMediaProbeV1:
        if type(media) is not ImageSourceMedia:
            raise TypeError("media must be the exact ImageSourceMedia")
        if media.source.bytes > self.max_source_bytes:
            raise ImageMediaIngestError(
                "HARN_IMAGE_LIMIT_EXCEEDED",
                "image source exceeds the configured byte limit",
            )
        source_payload = _read_verified_artifact(self.artifact_store, media.source)
        detected_format, normalized = _decode_image(
            media.source,
            source_payload,
            max_decoded_pixels=self.max_decoded_pixels,
        )
        canonical_payload = _canonical_png(normalized)
        expected_canonical = _artifact_ref_for_payload(
            canonical_payload,
            name=f"{media.source.sha256}.canonical.png",
            media_type="image/png",
            schema_version=None,
        )
        _read_verified_artifact(self.artifact_store, media.canonical_image)
        if media.canonical_image != expected_canonical:
            _evidence_mismatch("canonical image identity does not match decoded source pixels")

        expected_probe = _image_probe(
            source=media.source,
            detected_format=detected_format,
            canonical_image=expected_canonical,
            normalized=normalized,
        )
        expected_probe_payload = _canonical_json_bytes(expected_probe.model_dump(mode="json"))
        expected_probe_ref = _artifact_ref_for_payload(
            expected_probe_payload,
            name=f"{media.source.sha256}.image-probe.json",
            media_type="application/json",
            schema_version="harness.image_media_probe.v1",
        )
        probe_payload = _read_verified_artifact(self.artifact_store, media.probe)
        try:
            probe = ImageMediaProbeV1.model_validate_json(probe_payload)
        except ValidationError:
            _evidence_mismatch("probe is not strict typed image evidence")
        if (
            media.probe != expected_probe_ref
            or probe_payload != expected_probe_payload
            or probe != expected_probe
        ):
            _evidence_mismatch("probe bytes do not canonically bind decoded source evidence")

        expected_media = ImageSourceMedia(
            schema_version="harness.image_source_media.v1",
            source_media_sha256=media.source.sha256,
            source=media.source,
            canonical_image=expected_canonical,
            probe=expected_probe_ref,
            width=normalized.width,
            height=normalized.height,
            color_mode=normalized.mode,
            decoded_frame_count=1,
            unique_frame_count=1,
        )
        if media != expected_media:
            _evidence_mismatch("image media fields do not match decoded source evidence")
        return probe


def _validate_configuration(
    artifact_store: LocalArtifactStore,
    *,
    max_source_bytes: int,
    max_decoded_pixels: int,
) -> None:
    if type(artifact_store) is not LocalArtifactStore:
        raise TypeError("artifact_store must be the exact LocalArtifactStore")
    for value, label in (
        (max_source_bytes, "max_source_bytes"),
        (max_decoded_pixels, "max_decoded_pixels"),
    ):
        if type(value) is not int or value <= 0:
            raise ValueError(f"{label} must be a positive exact integer")


def _read_verified_artifact(store: LocalArtifactStore, ref: ArtifactRef) -> bytes:
    try:
        resolved = store.resolve(ref)
        payload = resolved.path.read_bytes()
    except (ArtifactResolutionError, OSError) as error:
        raise ImageMediaIngestError(
            "HARN_ARTIFACT_UNAVAILABLE",
            "image evidence is unavailable or failed content verification",
        ) from error
    # ``resolve`` already verifies this immutable CAS object. This second check closes the
    # concurrent-tamper window between resolution and read; deterministically reaching it
    # would require replacing the exact production store or racing the filesystem.
    if (  # pragma: no cover - concurrent external CAS tamper defense
        len(payload) != ref.bytes or hashlib.sha256(payload).hexdigest() != ref.sha256
    ):
        raise ImageMediaIngestError(
            "HARN_ARTIFACT_UNAVAILABLE",
            "image evidence changed during verified consumption",
        )
    return payload


def _decode_image(
    source: ArtifactRef,
    payload: bytes,
    *,
    max_decoded_pixels: int,
) -> tuple[str, Image.Image]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(payload)) as opened:
                detected_format = opened.format
                if getattr(opened, "is_animated", False) or getattr(opened, "n_frames", 1) != 1:
                    raise ImageMediaIngestError(
                        "HARN_IMAGE_ANIMATED",
                        "animated images cannot enter the single-image ingest seam",
                    )
                if opened.width * opened.height > max_decoded_pixels:
                    raise ImageMediaIngestError(
                        "HARN_IMAGE_LIMIT_EXCEEDED",
                        "decoded image exceeds the configured pixel limit",
                    )
                opened.load()
                normalized = _normalize_image(ImageOps.exif_transpose(opened))
    except ImageMediaIngestError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ImageMediaIngestError(
            "HARN_IMAGE_LIMIT_EXCEEDED",
            "image decoder rejected an oversized image",
        ) from error
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
        raise ImageMediaIngestError(
            "HARN_IMAGE_DECODE_FAILED",
            "image bytes could not be completely decoded",
        ) from error

    expected_media_type = _FORMAT_MEDIA_TYPES.get(str(detected_format))
    if expected_media_type is None or source.media_type != expected_media_type:
        raise ImageMediaIngestError(
            "HARN_MEDIA_TYPE_MISMATCH",
            "declared media type does not match decoded image bytes",
        )
    return str(detected_format), normalized


def _image_probe(
    *,
    source: ArtifactRef,
    detected_format: str,
    canonical_image: ArtifactRef,
    normalized: Image.Image,
) -> ImageMediaProbeV1:
    return ImageMediaProbeV1(
        schema_version="harness.image_media_probe.v1",
        source=source,
        detected_format=detected_format,
        declared_media_type=source.media_type,
        width=normalized.width,
        height=normalized.height,
        color_mode=normalized.mode,
        decoded_frame_count=1,
        unique_frame_count=1,
        canonical_image=canonical_image,
        pixel_sha256=hashlib.sha256(normalized.tobytes()).hexdigest(),
        decoder_name="Pillow",
        decoder_version=version("Pillow"),
    )


def _artifact_ref_for_payload(
    payload: bytes,
    *,
    name: str,
    media_type: str,
    schema_version: str | None,
) -> ArtifactRef:
    sha256 = hashlib.sha256(payload).hexdigest()
    return ArtifactRef(
        name=name,
        uri=f"artifact://sha256/{sha256}",
        media_type=media_type,
        sha256=sha256,
        bytes=len(payload),
        schema_version=schema_version,
    )


def _evidence_mismatch(message: str) -> None:
    raise ImageMediaIngestError("HARN_IMAGE_EVIDENCE_MISMATCH", message)


def _canonical_png(image: Image.Image) -> bytes:
    target = BytesIO()
    image.save(target, format="PNG", optimize=False, compress_level=9)
    return target.getvalue()


def _normalize_image(image: Image.Image) -> Image.Image:
    if image.mode == "L":
        mode = "L"
    elif "A" in image.getbands() or "transparency" in image.info:
        mode = "RGBA"
    else:
        mode = "RGB"
    converted = image if image.mode == mode else image.convert(mode)
    return Image.frombytes(mode, converted.size, converted.tobytes())


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


__all__ = ["ImageMediaConsumerVerifier", "ImageMediaIngestError", "ImageMediaIngestor"]
