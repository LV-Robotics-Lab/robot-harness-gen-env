"""Public-seam RED for deterministic image ingest before image2env compile."""

from __future__ import annotations

import base64
import hashlib
import struct
import zlib
from importlib.metadata import version
from io import BytesIO
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from PIL import Image
from pydantic import ValidationError

from self_improving.harness import schemas as public_schemas
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.schema_catalog import schema_documents, schema_model
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
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoHBwYIDAoMDAsKCwsNDhIQDQ4R"
    "DgsLEBYQERMUFRUVDA8XGBYUGBIUFRT/2wBDAQMEBAUEBQkFBQkUDQsNFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQU"
    "FBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBT/wAARCAABAAIDAREAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAA"
    "AAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAk"
    "M2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKT"
    "lJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QA"
    "HwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdh"
    "cRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hp"
    "anN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk"
    "5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDovBn/ACJ+hf8AXhB/6LWv4tx3+91f8UvzZ/GHij/yXuf/APYZ"
    "if8A09M//9k="
)
_ANIMATED_GIF = base64.b64decode(
    "R0lGODlhAgABAIEAAP8AAAAAAAAAAAAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQACgAAACwAAAAA"
    "AgABAAAIBQABAAgIACH5BAEKAAEALAAAAAACAAEAgQD/AAAAAAAAAAAAAAgFAAEACAgAOw=="
)
_EIGHT_BY_EIGHT_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFElEQVR4nGNkYPjPgA0wYRUdtBIA"
    "y0MBD1YkjLoAAAAASUVORK5CYII="
)


def _png_bytes(mode: str, *, transparency: bool = False) -> bytes:
    image = Image.new(mode, (2, 1))
    if mode == "L":
        image.putdata([0, 255])
    elif mode == "RGBA":
        image.putdata([(255, 0, 0, 255), (0, 255, 0, 0)])
    elif mode == "P":
        image.putpalette([0, 0, 0, 255, 255, 255] + [0, 0, 0] * 254)
        image.putdata([0, 1])
        if transparency:
            image.info["transparency"] = 0
    target = BytesIO()
    image.save(target, format="PNG")
    return target.getvalue()


def _decompression_bomb_png() -> bytes:
    payload = bytearray(_EIGHT_BY_EIGHT_PNG)
    payload[16:24] = struct.pack(">II", 10_000, 10_000)
    payload[29:33] = struct.pack(">I", zlib.crc32(payload[12:29]))
    return bytes(payload)


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
    assert media.canonical_image.uri == (f"artifact://sha256/{media.canonical_image.sha256}")
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
    probe_document = schema_documents()[_PROBE_SCHEMA_ID]
    Draft202012Validator.check_schema(probe_document)
    Draft202012Validator(probe_document).validate(probe.model_dump(mode="json"))
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


@pytest.mark.parametrize(
    ("store", "max_source_bytes", "max_decoded_pixels", "expected"),
    (
        (object(), 1, 1, TypeError),
        (None, True, 1, ValueError),
        (None, 0, 1, ValueError),
        (None, 1, False, ValueError),
        (None, 1, -1, ValueError),
    ),
)
@_requires_image_ingest
def test_image_ingestor_constructor_rejects_non_production_or_unbounded_dependencies(
    tmp_path: Path,
    store: object | None,
    max_source_bytes: object,
    max_decoded_pixels: object,
    expected: type[Exception],
) -> None:
    assert ImageMediaIngestor is not None
    actual_store = LocalArtifactStore(tmp_path / "cas") if store is None else store

    with pytest.raises(expected):
        ImageMediaIngestor(
            artifact_store=actual_store,  # type: ignore[arg-type]
            max_source_bytes=max_source_bytes,  # type: ignore[arg-type]
            max_decoded_pixels=max_decoded_pixels,  # type: ignore[arg-type]
        )


@_requires_image_ingest
def test_image_ingestor_rejects_non_exact_or_typed_source_before_read(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    source = _put_source(
        store,
        tmp_path,
        name="typed-source.jpg",
        payload=_FIXED_JPEG,
        media_type="image/jpeg",
    )
    assert ImageMediaIngestError is not None

    with pytest.raises(TypeError, match="exact ArtifactRef"):
        _ingestor(store).ingest(source.model_dump(mode="python"))  # type: ignore[arg-type]
    with pytest.raises(ImageMediaIngestError) as captured:
        _ingestor(store).ingest(
            source.model_copy(update={"schema_version": "harness.untrusted_image.v1"})
        )

    assert captured.value.code == "HARN_MEDIA_TYPE_MISMATCH"


@pytest.mark.parametrize(
    ("mode", "transparency", "expected_mode"),
    (
        ("L", False, "L"),
        ("RGBA", False, "RGBA"),
        ("P", True, "RGBA"),
    ),
)
@_requires_image_ingest
def test_image_ingest_normalizes_supported_pixel_modes_deterministically(
    tmp_path: Path,
    mode: str,
    transparency: bool,
    expected_mode: str,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    source = _put_source(
        store,
        tmp_path,
        name=f"{mode}.png",
        payload=_png_bytes(mode, transparency=transparency),
        media_type="image/png",
    )

    media = _ingestor(store).ingest(source)

    assert media.color_mode == expected_mode


@_requires_image_ingest
def test_image_ingest_maps_decoder_bomb_warning_to_the_bounded_failure(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    source = _put_source(
        store,
        tmp_path,
        name="bomb.png",
        payload=_decompression_bomb_png(),
        media_type="image/png",
    )
    assert ImageMediaIngestError is not None

    with pytest.raises(ImageMediaIngestError) as captured:
        _ingestor(store, max_decoded_pixels=100_000_000).ingest(source)

    assert captured.value.code == "HARN_IMAGE_LIMIT_EXCEEDED"


@_requires_image_ingest
def test_image_ingest_rejects_a_decodable_but_unsupported_static_format(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    image = Image.new("P", (2, 1))
    target = BytesIO()
    image.save(target, format="GIF")
    source = _put_source(
        store,
        tmp_path,
        name="static.gif",
        payload=target.getvalue(),
        media_type="image/gif",
    )
    assert ImageMediaIngestError is not None

    with pytest.raises(ImageMediaIngestError) as captured:
        _ingestor(store).ingest(source)

    assert captured.value.code == "HARN_MEDIA_TYPE_MISMATCH"


@_requires_image_ingest
def test_image_media_probe_rejects_identity_and_media_drift(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    source = _put_source(
        store,
        tmp_path,
        name="probe-source.jpg",
        payload=_FIXED_JPEG,
        media_type="image/jpeg",
    )
    media = _ingestor(store).ingest(source)
    assert _PROBE_MODEL is not None
    probe = _PROBE_MODEL.model_validate_json(store.resolve(media.probe).path.read_bytes())
    zero_uri = f"artifact://sha256/{'0' * 64}"
    mutations = (
        {"source": source.model_copy(update={"uri": zero_uri})},
        {"source": source.model_copy(update={"schema_version": "harness.fake.v1"})},
        {"canonical_image": media.canonical_image.model_copy(update={"uri": zero_uri})},
        {"canonical_image": media.canonical_image.model_copy(update={"media_type": "image/jpeg"})},
        {
            "canonical_image": media.canonical_image.model_copy(
                update={"schema_version": "harness.fake.v1"}
            )
        },
        {"detected_format": "PNG"},
    )

    for mutation in mutations:
        with pytest.raises(ValidationError):
            _PROBE_MODEL.model_validate({**probe.model_dump(mode="python"), **mutation})
