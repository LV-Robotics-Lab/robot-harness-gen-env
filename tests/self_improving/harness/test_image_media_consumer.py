"""Public-seam RED for strict consumption of image identity evidence."""

from __future__ import annotations

import json
import struct
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, PngImagePlugin
from pydantic import ValidationError

from self_improving.harness import ImageMediaConsumerVerifier as PublicImageMediaConsumerVerifier
from self_improving.harness import image_ingest as image_ingest_module
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.image_ingest import ImageMediaIngestError, ImageMediaIngestor
from self_improving.harness.schemas.common import ArtifactRef
from self_improving.harness.schemas.media_ingest import ImageMediaProbeV1

ImageMediaConsumerVerifier = getattr(
    image_ingest_module,
    "ImageMediaConsumerVerifier",
    None,
)
_requires_consumer = pytest.mark.skipif(
    ImageMediaConsumerVerifier is None,
    reason="waiting for the public read-only image media consumer verifier",
)


def _ref(
    name: str,
    sha256: str,
    *,
    media_type: str,
    schema_version: str | None,
    size: int = 1,
) -> ArtifactRef:
    return ArtifactRef(
        name=name,
        uri=f"artifact://sha256/{sha256}",
        media_type=media_type,
        sha256=sha256,
        bytes=size,
        schema_version=schema_version,
    )


def _put(
    store: LocalArtifactStore,
    root: Path,
    *,
    name: str,
    payload: bytes,
    media_type: str,
    schema_version: str | None = None,
) -> ArtifactRef:
    path = root / name
    path.write_bytes(payload)
    return store.put_file(
        path,
        name=name,
        media_type=media_type,
        schema_version=schema_version,
    )


def _ingestor(store: LocalArtifactStore) -> ImageMediaIngestor:
    return ImageMediaIngestor(
        artifact_store=store,
        max_source_bytes=64 * 1024,
        max_decoded_pixels=1024,
    )


def _consumer(store: LocalArtifactStore):
    assert ImageMediaConsumerVerifier is not None
    return ImageMediaConsumerVerifier(
        artifact_store=store,
        max_source_bytes=64 * 1024,
        max_decoded_pixels=1024,
    )


def _png_source(mode: str, marker: str) -> bytes:
    color: int | tuple[int, ...]
    if mode == "L":
        color = 37
    elif mode == "RGB":
        color = (17, 37, 57)
    else:
        color = (17, 37, 57, 97)
    image = Image.new(mode, (2, 2), color)
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("source-note", marker)
    exif = Image.Exif()
    exif[270] = marker
    target = BytesIO()
    image.save(
        target,
        format="PNG",
        pnginfo=metadata,
        icc_profile=f"fake-profile-{marker}".encode(),
        exif=exif,
    )
    return target.getvalue()


def _png_chunk_names(payload: bytes) -> tuple[bytes, ...]:
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    offset = 8
    names: list[bytes] = []
    while offset < len(payload):
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        name = payload[offset + 4 : offset + 8]
        names.append(name)
        offset += 12 + length
        if name == b"IEND":
            break
    assert offset == len(payload)
    return tuple(names)


def _cas_manifest(store: LocalArtifactStore) -> tuple[tuple[str, int], ...]:
    return tuple(
        (str(path.relative_to(store.root)), path.stat().st_size)
        for path in sorted((store.root / "sha256").glob("*/*"))
    )


def test_image_probe_binds_source_artifact_media_type_to_declared_media_type() -> None:
    source_sha = "1" * 64
    canonical_sha = "2" * 64

    with pytest.raises(ValidationError, match="declared media type"):
        ImageMediaProbeV1(
            schema_version="harness.image_media_probe.v1",
            source=_ref(
                "source.png",
                source_sha,
                media_type="image/png",
                schema_version=None,
            ),
            detected_format="JPEG",
            declared_media_type="image/jpeg",
            width=1,
            height=1,
            color_mode="RGB",
            decoded_frame_count=1,
            unique_frame_count=1,
            canonical_image=_ref(
                "canonical.png",
                canonical_sha,
                media_type="image/png",
                schema_version=None,
            ),
            pixel_sha256="3" * 64,
            decoder_name="Pillow",
            decoder_version="12.0.0",
        )


def test_read_only_image_media_consumer_verifier_is_public() -> None:
    assert ImageMediaConsumerVerifier is not None
    assert PublicImageMediaConsumerVerifier is ImageMediaConsumerVerifier


@_requires_consumer
def test_image_media_consumer_revalidates_probe_canonical_bytes_and_all_mirrored_fields(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    source = _put(
        store,
        tmp_path,
        name="source.png",
        payload=_png_source("RGB", "trusted"),
        media_type="image/png",
    )
    media = _ingestor(store).ingest(source)
    verifier = _consumer(store)
    expected_probe = ImageMediaProbeV1.model_validate_json(
        store.resolve(media.probe).path.read_bytes()
    )
    before = _cas_manifest(store)

    assert verifier.verify(media) == expected_probe
    assert _cas_manifest(store) == before

    alternate_source = _put(
        store,
        tmp_path,
        name="alternate.png",
        payload=_png_source("RGBA", "alternate"),
        media_type="image/png",
    )
    alternate = _ingestor(store).ingest(alternate_source)
    mutations = (
        {"source_media_sha256": "0" * 64},
        {
            "source": alternate.source,
            "source_media_sha256": alternate.source.sha256,
        },
        {"canonical_image": alternate.canonical_image},
        {"probe": alternate.probe},
        {"width": media.width + 1},
        {"height": media.height + 1},
        {"color_mode": "RGBA"},
    )
    before_rejections = _cas_manifest(store)
    for mutation in mutations:
        drifted = media.model_copy(update=mutation)
        with pytest.raises(ImageMediaIngestError) as captured:
            verifier.verify(drifted)
        assert captured.value.code == "HARN_IMAGE_EVIDENCE_MISMATCH"
        assert _cas_manifest(store) == before_rejections


@_requires_consumer
def test_image_media_consumer_requires_canonical_probe_json_with_one_terminal_newline(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    source = _put(
        store,
        tmp_path,
        name="source.png",
        payload=_png_source("RGB", "canonical-json"),
        media_type="image/png",
    )
    media = _ingestor(store).ingest(source)
    probe_payload = store.resolve(media.probe).path.read_bytes()
    assert probe_payload.endswith(b"\n")
    assert not probe_payload.endswith(b"\n\n")
    probe = ImageMediaProbeV1.model_validate_json(probe_payload)
    noncanonical_payload = json.dumps(
        probe.model_dump(mode="json"),
        indent=2,
        sort_keys=False,
    ).encode("utf-8")
    noncanonical_ref = _put(
        store,
        tmp_path,
        name="noncanonical-probe.json",
        payload=noncanonical_payload,
        media_type="application/json",
        schema_version="harness.image_media_probe.v1",
    )
    drifted = media.model_copy(update={"probe": noncanonical_ref})
    before = _cas_manifest(store)

    with pytest.raises(ImageMediaIngestError) as captured:
        _consumer(store).verify(drifted)

    assert captured.value.code == "HARN_IMAGE_EVIDENCE_MISMATCH"
    assert _cas_manifest(store) == before


@_requires_consumer
def test_image_media_consumer_rejects_invalid_typed_probe_and_unavailable_evidence(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    source = _put(
        store,
        tmp_path,
        name="source.png",
        payload=_png_source("RGB", "invalid-probe"),
        media_type="image/png",
    )
    media = _ingestor(store).ingest(source)
    invalid_probe = _put(
        store,
        tmp_path,
        name="invalid-probe.json",
        payload=b"{}\n",
        media_type="application/json",
        schema_version="harness.image_media_probe.v1",
    )

    with pytest.raises(ImageMediaIngestError) as invalid:
        _consumer(store).verify(media.model_copy(update={"probe": invalid_probe}))
    missing_sha = "0" * 64
    with pytest.raises(ImageMediaIngestError) as unavailable:
        _consumer(store).verify(
            media.model_copy(
                update={
                    "canonical_image": media.canonical_image.model_copy(
                        update={
                            "sha256": missing_sha,
                            "uri": f"artifact://sha256/{missing_sha}",
                        }
                    )
                }
            )
        )

    assert invalid.value.code == "HARN_IMAGE_EVIDENCE_MISMATCH"
    assert unavailable.value.code == "HARN_ARTIFACT_UNAVAILABLE"


@_requires_consumer
def test_image_media_consumer_requires_exact_media_and_its_own_source_bound(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    source = _put(
        store,
        tmp_path,
        name="source.png",
        payload=_png_source("RGB", "bounds"),
        media_type="image/png",
    )
    media = _ingestor(store).ingest(source)

    with pytest.raises(TypeError, match="exact ImageSourceMedia"):
        _consumer(store).verify(media.model_dump(mode="python"))  # type: ignore[arg-type]
    with pytest.raises(ImageMediaIngestError) as captured:
        ImageMediaConsumerVerifier(
            artifact_store=store,
            max_source_bytes=source.bytes - 1,
            max_decoded_pixels=1024,
        ).verify(media)

    assert captured.value.code == "HARN_IMAGE_LIMIT_EXCEEDED"


@pytest.mark.parametrize("mode", ("L", "RGB", "RGBA"))
def test_canonical_png_identity_excludes_icc_text_and_exif_metadata(
    tmp_path: Path,
    mode: str,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    first_source = _put(
        store,
        tmp_path,
        name=f"{mode}-first.png",
        payload=_png_source(mode, "first"),
        media_type="image/png",
    )
    second_source = _put(
        store,
        tmp_path,
        name=f"{mode}-second.png",
        payload=_png_source(mode, "second"),
        media_type="image/png",
    )

    first = _ingestor(store).ingest(first_source)
    second = _ingestor(store).ingest(second_source)
    first_probe = ImageMediaProbeV1.model_validate_json(
        store.resolve(first.probe).path.read_bytes()
    )
    second_probe = ImageMediaProbeV1.model_validate_json(
        store.resolve(second.probe).path.read_bytes()
    )

    assert first.source != second.source
    assert first.probe != second.probe
    assert first_probe.pixel_sha256 == second_probe.pixel_sha256
    assert first.canonical_image.sha256 == second.canonical_image.sha256
    canonical_payload = store.resolve(first.canonical_image).path.read_bytes()
    assert set(_png_chunk_names(canonical_payload)).isdisjoint(
        {b"iCCP", b"tEXt", b"zTXt", b"iTXt", b"eXIf"}
    )
