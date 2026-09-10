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
        if type(self.artifact_store) is not LocalArtifactStore:
            raise TypeError("artifact_store must be the exact LocalArtifactStore")
        for value, label in (
            (self.max_source_bytes, "max_source_bytes"),
            (self.max_decoded_pixels, "max_decoded_pixels"),
        ):
            if type(value) is not int or value <= 0:
                raise ValueError(f"{label} must be a positive exact integer")

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
        payload = self._read_source(source)
        detected_format, normalized = self._decode(source, payload)
        canonical_payload = self._canonical_png(normalized)
        pixel_sha256 = hashlib.sha256(normalized.tobytes()).hexdigest()

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
            probe = ImageMediaProbeV1(
                schema_version="harness.image_media_probe.v1",
                source=source,
                detected_format=detected_format,
                declared_media_type=source.media_type,
                width=normalized.width,
                height=normalized.height,
                color_mode=normalized.mode,
                decoded_frame_count=1,
                unique_frame_count=1,
                canonical_image=canonical_ref,
                pixel_sha256=pixel_sha256,
                decoder_name="Pillow",
                decoder_version=version("Pillow"),
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

    def _read_source(self, source: ArtifactRef) -> bytes:
        try:
            resolved = self.artifact_store.resolve(source)
            payload = resolved.path.read_bytes()
        except (ArtifactResolutionError, OSError) as error:
            raise ImageMediaIngestError(
                "HARN_ARTIFACT_UNAVAILABLE",
                "image source is unavailable or failed content verification",
            ) from error
        # ``resolve`` already verifies this immutable CAS object. This second check closes the
        # concurrent-tamper window between resolution and read; deterministically reaching it
        # would require replacing the exact production store or racing the filesystem.
        if (  # pragma: no cover - concurrent external CAS tamper defense
            len(payload) != source.bytes or hashlib.sha256(payload).hexdigest() != source.sha256
        ):
            raise ImageMediaIngestError(
                "HARN_ARTIFACT_UNAVAILABLE",
                "image source changed during verified ingestion",
            )
        return payload

    def _decode(self, source: ArtifactRef, payload: bytes) -> tuple[str, Image.Image]:
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
                    if opened.width * opened.height > self.max_decoded_pixels:
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

    @staticmethod
    def _canonical_png(image: Image.Image) -> bytes:
        target = BytesIO()
        image.save(target, format="PNG", optimize=False, compress_level=9)
        return target.getvalue()


def _normalize_image(image: Image.Image) -> Image.Image:
    if image.mode == "L":
        return image.copy()
    if "A" in image.getbands() or "transparency" in image.info:
        return image.convert("RGBA")
    return image.convert("RGB")


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


__all__ = ["ImageMediaIngestError", "ImageMediaIngestor"]
