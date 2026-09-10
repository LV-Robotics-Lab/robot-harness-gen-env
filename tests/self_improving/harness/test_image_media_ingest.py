"""Public-seam RED for deterministic image ingest before image2env compile."""

from __future__ import annotations

import base64
import hashlib
from importlib.metadata import version
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from self_improving.harness import schemas as public_schemas
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.schema_catalog import schema_model
from self_improving.harness.schemas.common import ArtifactRef

try:
    from self_improving.harness.image_ingest import (
        ImageMediaIngestError,
        ImageMediaIngestor,
    )
except ModuleNotFoundError as error:
    if error.name != "self_improving.harness.image_ingest":
        raise
    ImageMediaIngestError = None  # type: ignore[assignment,misc]
    ImageMediaIngestor = None  # type: ignore[assignment,misc]


_PROBE_SCHEMA_ID = "harness.image_media_probe.v1"
_PROBE_MODEL = getattr(public_schemas, "ImageMediaProbeV1", None)
_PUBLIC_SEAM_READY = (
    ImageMediaIngestError is not None
    and ImageMediaIngestor is not None
    and _PROBE_MODEL is not None
)
_requires_image_ingest = pytest.mark.skipif(
    not _PUBLIC_SEAM_READY,
    reason="waiting for the public deterministic ImageMediaIngestor seam",
)

_FIXED_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAIBAQEBAQIBAQECAgICAgQDAgICAgUEBAMEBgUGBgYF"
    "BgYGBwkIBgcJBwYGCAsICQoKCgoKBggLDAsKDAkKCgr/2wBDAQICAgICAgUDAwUKBwYHCgoKCgoK"
    "CgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgr/wAARCAABAAIDAREAAhEB"
    "AxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9"
    "AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6"
    "Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ip"
    "qrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEB"
    "AQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJB"
    "UQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RV"
    "VldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6"
    "wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDs/gx/yR7w"
    "n/2LVh/6TpX+Yeef8jvFf9fJ/wDpTP8AMj6UX/KTPG//AGN8y/8AU2sf/9k="
)
_ANIMATED_GIF = base64.b64decode(
    "R0lGODlhAgABAIEAAP8AAAAAAAAAAAAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQACgAAACwAAAAA"
    "AgABAAAIBQABAAgIACH5BAEKAAEALAAAAAACAAEAgQD/AAAAAAAAAAAAAAgFAAEACAgAOw=="
)
_EIGHT_BY_EIGHT_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFElEQVR4nGNkYPjPgA0wYRUdtBIA"
    "y0MBD1YkjLoAAAAASUVORK5CYII="
)


def _put_source(
    store: LocalArtifactStore,
    root: Path,
    *,
    name: str,
    payload: bytes,
    media_type: str,
) -> ArtifactRef:
    source = root / name
    source.write_bytes(payload)
    return store.put_file(
        source,
        name=name,
        media_type=media_type,
        schema_version=None,
    )


def _cas_manifest(store: LocalArtifactStore) -> tuple[tuple[str, int], ...]:
    return tuple(
        (str(path.relative_to(store.root)), path.stat().st_size)
        for path in sorted((store.root / "sha256").glob("*/*"))
    )


def _ingestor(
    store: LocalArtifactStore,
    *,
    max_source_bytes: int = 4096,
    max_decoded_pixels: int = 64,
):
    assert ImageMediaIngestor is not None
    return ImageMediaIngestor(
        artifact_store=store,
        max_source_bytes=max_source_bytes,
        max_decoded_pixels=max_decoded_pixels,
    )


def test_image_ingest_public_seam_and_probe_schema_are_available() -> None:
    missing: list[str] = []
    if ImageMediaIngestor is None:
        missing.append("self_improving.harness.image_ingest.ImageMediaIngestor")
    if ImageMediaIngestError is None:
        missing.append("self_improving.harness.image_ingest.ImageMediaIngestError")
    if _PROBE_MODEL is None:
        missing.append("self_improving.harness.schemas.ImageMediaProbeV1")
    try:
        catalog_model = schema_model(_PROBE_SCHEMA_ID)
    except KeyError:
        missing.append(_PROBE_SCHEMA_ID)
    else:
        if catalog_model is not _PROBE_MODEL:
            missing.append(f"{_PROBE_SCHEMA_ID} exact catalog identity")
    assert not missing, "missing accepted X1 image ingest public seam: " + ", ".join(missing)


@_requires_image_ingest
def test_image_ingest_decodes_cas_source_and_publishes_a_strict_canonical_closure(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    source = _put_source(
        store,
        tmp_path,
        name="fixed-two-pixel-source.jpg",
        payload=_FIXED_JPEG,
        media_type="image/jpeg",
    )

    media = _ingestor(store).ingest(source)

    assert type(media) is public_schemas.ImageSourceMedia
    assert media.source == source
    assert media.source_media_sha256 == source.sha256
    assert media.width == 2
    assert media.height == 1
    assert media.color_mode == "RGB"
    assert media.decoded_frame_count == media.unique_frame_count == 1
    assert media.canonical_image.media_type == "image/png"
    assert media.canonical_image.schema_version is None
    assert media.probe.schema_version == _PROBE_SCHEMA_ID
    assert media.probe.media_type == "application/json"
    assert media.canonical_image.uri == (
        f"artifact://sha256/{media.canonical_image.sha256}"
    )
    assert media.probe.uri == f"artifact://sha256/{media.probe.sha256}"

    canonical_bytes = store.resolve(media.canonical_image).path.read_bytes()
    with Image.open(BytesIO(canonical_bytes)) as decoded:
        decoded.load()
        assert decoded.format == "PNG"
        assert decoded.mode == "RGB"
        assert decoded.size == (2, 1)
        assert getattr(decoded, "n_frames", 1) == 1
        pixel_sha256 = hashlib.sha256(decoded.tobytes()).hexdigest()

    assert _PROBE_MODEL is not None
    probe = _PROBE_MODEL.model_validate_json(store.resolve(media.probe).path.read_bytes())
    assert probe.schema_version == _PROBE_SCHEMA_ID
    assert probe.source == source
    assert probe.detected_format == "JPEG"
    assert probe.declared_media_type == "image/jpeg"
    assert (probe.width, probe.height, probe.color_mode) == (2, 1, "RGB")
    assert probe.decoded_frame_count == probe.unique_frame_count == 1
    assert probe.canonical_image == media.canonical_image
    assert probe.pixel_sha256 == pixel_sha256
    assert probe.decoder_name == "Pillow"
    assert probe.decoder_version == version("Pillow")
    with pytest.raises(ValidationError):
        _PROBE_MODEL.model_validate(
            {
                **probe.model_dump(mode="python"),
                "untrusted_extra": True,
            }
        )
    assert store.resolve(source).path.read_bytes() == _FIXED_JPEG
    for artifact in (source, media.canonical_image, media.probe):
        payload = store.resolve(artifact).path.read_bytes()
        assert len(payload) == artifact.bytes
        assert hashlib.sha256(payload).hexdigest() == artifact.sha256


@pytest.mark.parametrize(
    ("case", "expected_code"),
    (
        ("declared_mime", "HARN_MEDIA_TYPE_MISMATCH"),
        ("missing_digest", "HARN_ARTIFACT_UNAVAILABLE"),
        ("animated", "HARN_IMAGE_ANIMATED"),
        ("source_bytes", "HARN_IMAGE_LIMIT_EXCEEDED"),
        ("decoded_pixels", "HARN_IMAGE_LIMIT_EXCEEDED"),
        ("damaged", "HARN_IMAGE_DECODE_FAILED"),
    ),
)
@_requires_image_ingest
def test_image_ingest_rejects_untrusted_or_non_single_image_sources_before_publish(
    tmp_path: Path,
    case: str,
    expected_code: str,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    payload = _FIXED_JPEG
    media_type = "image/jpeg"
    max_source_bytes = 4096
    max_decoded_pixels = 64
    if case == "declared_mime":
        media_type = "image/png"
    elif case == "animated":
        payload = _ANIMATED_GIF
        media_type = "image/gif"
    elif case == "source_bytes":
        max_source_bytes = len(_FIXED_JPEG) - 1
    elif case == "decoded_pixels":
        payload = _EIGHT_BY_EIGHT_PNG
        media_type = "image/png"
        max_decoded_pixels = 63
    elif case == "damaged":
        payload = b"\xff\xd8\xff\xe0truncated-jpeg"
    source = _put_source(
        store,
        tmp_path,
        name=f"{case}.bin",
        payload=payload,
        media_type=media_type,
    )
    if case == "missing_digest":
        digest = "0" * 64
        source = source.model_copy(
            update={
                "sha256": digest,
                "uri": f"artifact://sha256/{digest}",
            }
        )
    before = _cas_manifest(store)
    ingestor = _ingestor(
        store,
        max_source_bytes=max_source_bytes,
        max_decoded_pixels=max_decoded_pixels,
    )
    assert ImageMediaIngestError is not None

    with pytest.raises(ImageMediaIngestError) as captured:
        ingestor.ingest(source)

    assert captured.value.code == expected_code
    assert _cas_manifest(store) == before


@_requires_image_ingest
def test_image_ingest_retry_is_content_identical_and_does_not_grow_cas(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    source = _put_source(
        store,
        tmp_path,
        name="retry-source.jpg",
        payload=_FIXED_JPEG,
        media_type="image/jpeg",
    )
    first = _ingestor(store).ingest(source)
    after_first = _cas_manifest(store)

    recovered = _ingestor(store).ingest(source)

    assert recovered == first
    assert _cas_manifest(store) == after_first
