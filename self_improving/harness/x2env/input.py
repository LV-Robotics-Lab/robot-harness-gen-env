"""Bounded raw input ingestion; no model, scene semantics, or workflow authority."""

import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from fractions import Fraction
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from PIL import Image, ImageOps, UnidentifiedImageError
from PIL import __version__ as pillow_version

from .contracts import (
    ArtifactRef,
    ImageInputEvidence,
    InputBundle,
    VideoFrameEvidence,
    VideoInputEvidence,
    X2EnvRequest,
)


class ArtifactStore(Protocol):
    def write_artifact(self, data: bytes, media_type: str) -> ArtifactRef: ...


@dataclass(frozen=True)
class IngestLimits:
    source_bytes: int = 64 * 1024 * 1024
    decoded_pixels: int = 16_000_000
    video_frames: int = 600
    video_seconds: int = 60
    decoded_bytes: int = 1024 * 1024 * 1024
    decoder_seconds: int = 60

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in asdict(self).values()):
            raise ValueError("ingest limits must be positive integers")


class InputIngestError(ValueError):
    def __init__(self, code: str, message: str, artifacts: tuple[ArtifactRef, ...] = ()):
        super().__init__(message)
        self.code = code
        self.artifacts = artifacts


def _source(path, limits):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise InputIngestError("unsafe_input_path", "symbolic input paths are not allowed")
    try:
        if not path.is_file():
            raise InputIngestError("input_unavailable", "input is not a regular file")
        before = path.stat()
        if before.st_size > limits.source_bytes:
            raise InputIngestError("input_limit_exceeded", "source byte limit exceeded")
        with path.open("rb") as stream:
            raw = stream.read(limits.source_bytes + 1)
        after = path.stat()
        if len(raw) > limits.source_bytes:
            raise InputIngestError("input_limit_exceeded", "source byte limit exceeded")
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise InputIngestError("input_changed", "source changed during read")
        return raw
    except OSError as exc:
        raise InputIngestError("input_unavailable", "cannot read source input") from exc


def _image(raw, source, store, limits):
    try:
        with Image.open(BytesIO(raw)) as decoded:
            if (
                decoded.format not in {"PNG", "JPEG", "WEBP"}
                or getattr(decoded, "n_frames", 1) != 1
            ):
                raise InputIngestError("unsupported_image", "single-frame PNG/JPEG/WEBP required")
            if decoded.width * decoded.height > limits.decoded_pixels:
                raise InputIngestError("input_limit_exceeded", "image pixel limit exceeded")
            if decoded.width * decoded.height * 4 > limits.decoded_bytes:
                raise InputIngestError("input_limit_exceeded", "image decoded byte limit exceeded")
            decoded.load()
            oriented = ImageOps.exif_transpose(decoded)
            mode = (
                "RGBA" if "A" in oriented.getbands() or "transparency" in oriented.info else "RGB"
            )
            normalized = oriented.convert(mode)
            clean = Image.frombytes(mode, normalized.size, normalized.tobytes())
            output = BytesIO()
            clean.save(output, format="PNG")
            ref = store.write_artifact(output.getvalue(), "image/png")
            return ImageInputEvidence(
                source=source,
                canonical=ref,
                width=clean.width,
                height=clean.height,
                mode=mode,
                decoder_version=pillow_version,
            )
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise InputIngestError("invalid_image", "image decoding failed") from exc


def _command(command, store, limits, evidence):
    try:
        result = subprocess.run(command, capture_output=True, timeout=limits.decoder_seconds)
    except FileNotFoundError as exc:
        raise InputIngestError("blocked_external_resource", "FFmpeg decoder unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        for output in (exc.stdout, exc.stderr):
            if output:
                evidence.append(store.write_artifact(output, "text/plain"))
        raise InputIngestError("input_decode_timeout", "media decoder exceeded budget") from exc
    evidence.append(store.write_artifact(result.stdout, "text/plain"))
    evidence.append(store.write_artifact(result.stderr, "text/plain"))
    if result.returncode:
        raise InputIngestError("invalid_video", "media decoder rejected source")
    return result.stdout


def _video(raw, source, store, limits, evidence):
    with TemporaryDirectory(prefix="x2env-input-") as temporary:
        path = Path(temporary) / "source.mp4"
        path.write_bytes(raw)
        probe_raw = _command(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            store,
            limits,
            evidence,
        )
        try:
            probe = json.loads(probe_raw)
            streams = [s for s in probe["streams"] if s["codec_type"] == "video"]
            if len(streams) != 1:
                raise ValueError("one video stream required")
            stream = streams[0]
            width, height = int(stream["width"]), int(stream["height"])
            fps = Fraction(stream["avg_frame_rate"])
            duration = float(
                stream["duration"] if "duration" in stream else probe["format"]["duration"]
            )
            if width <= 0 or height <= 0 or fps <= 0 or not 0 < duration <= limits.video_seconds:
                raise InputIngestError(
                    "input_limit_exceeded", "invalid or oversized video duration"
                )
            if width * height > limits.decoded_pixels:
                raise InputIngestError("input_limit_exceeded", "video pixel limit exceeded")
            if width * height * 3 > limits.decoded_bytes:
                raise InputIngestError("input_limit_exceeded", "video decoded byte limit exceeded")
            max_frames = min(limits.video_frames, limits.decoded_bytes // (width * height * 3))
            hashes = _command(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-xerror",
                    "-nostdin",
                    "-i",
                    str(path),
                    "-map",
                    "0:v:0",
                    "-an",
                    "-fps_mode",
                    "passthrough",
                    "-pix_fmt",
                    "rgb24",
                    "-frames:v",
                    str(max_frames + 1),
                    "-f",
                    "framehash",
                    "-hash",
                    "sha256",
                    "-",
                ],
                store,
                limits,
                evidence,
            ).decode()
            time_base = Fraction(re.search(r"#tb 0:\s*(\d+/\d+)", hashes)[1])
            decoder = re.search(r"#software:\s*(.+)", hashes)[1]
            frames = []
            for line in hashes.splitlines():
                if not line or line.startswith("#"):
                    continue
                fields = [f.strip() for f in line.split(",")]
                if len(fields) != 6 or fields[0] != "0":
                    raise ValueError("unexpected decoded frame record")
                frame = VideoFrameEvidence(
                    index=len(frames),
                    pts=int(fields[2]),
                    duration=int(fields[3]),
                    size_bytes=int(fields[4]),
                    sha256=fields[5],
                )
                if frame.size_bytes != width * height * 3:
                    raise ValueError("decoded dimensions mismatch")
                if frames and frame.pts <= frames[-1].pts:
                    raise ValueError("non-monotonic decoded frame timestamps")
                frames.append(frame)
            if len(frames) > max_frames:
                raise InputIngestError(
                    "input_limit_exceeded", "video frame/decoded byte limit exceeded"
                )
            if not frames:
                raise ValueError("no decoded frames")
            if stream.get("nb_frames") and int(stream["nb_frames"]) != len(frames):
                raise ValueError("container frame count differs from complete decode")
            sequence = {
                "schema_version": "x2env.video_frame_sequence.v1",
                "source": source.model_dump(mode="json"),
                "full_decode": True,
                "time_base_num": time_base.numerator,
                "time_base_den": time_base.denominator,
                "pixel_format": "rgb24",
                "frames": [f.model_dump() for f in frames],
                "decoder": decoder,
                "decoder_evidence": [r.model_dump() for r in evidence],
            }
            sequence_ref = store.write_artifact(
                json.dumps(sequence, sort_keys=True, separators=(",", ":")).encode(),
                "application/json",
            )
            evidence.append(sequence_ref)
            probe_ref = store.write_artifact(probe_raw, "application/json")
            evidence.append(probe_ref)
            return VideoInputEvidence(
                source=source,
                probe=probe_ref,
                sequence=sequence_ref,
                width=width,
                height=height,
                codec=stream["codec_name"],
                fps_num=fps.numerator,
                fps_den=fps.denominator,
                time_base_num=time_base.numerator,
                time_base_den=time_base.denominator,
                frame_count=len(frames),
                unique_frame_count=len({f.sha256 for f in frames}),
                decoder_version=decoder,
            )
        except (ValueError, KeyError, TypeError, IndexError, ZeroDivisionError) as exc:
            if isinstance(exc, InputIngestError):
                raise
            raise InputIngestError("invalid_video", "invalid complete video evidence") from exc


def ingest(request: X2EnvRequest, artifact_store: ArtifactStore, *, limits=IngestLimits()):
    """Publish immutable input evidence; preserve partial refs on classified failure."""
    text = None
    artifacts = []
    sources = []
    if request.text is not None:
        text = artifact_store.write_artifact(request.text.encode("utf-8"), "text/plain")
        artifacts.append(text)
        sources.append(text)
    images = []
    video = None
    try:
        for media in request.images:
            raw = _source(media.path, limits)
            source = artifact_store.write_artifact(raw, "application/octet-stream")
            artifacts.append(source)
            sources.append(source)
            images.append(_image(raw, source, artifact_store, limits))
            artifacts.append(images[-1].canonical)
        if request.video is not None:
            raw = _source(request.video.path, limits)
            source = artifact_store.write_artifact(raw, "application/octet-stream")
            artifacts.append(source)
            sources.append(source)
            video = _video(raw, source, artifact_store, limits, artifacts)
    except InputIngestError as exc:
        exc.artifacts = tuple(artifacts) + exc.artifacts
        raise
    identity = hashlib.sha256(
        json.dumps(
            {
                "request": request.model_dump(mode="json"),
                "source_refs": [r.model_dump(mode="json") for r in sources],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    modalities = [
        name
        for name, present in (
            ("text", text is not None),
            ("image", bool(images)),
            ("video", video is not None),
        )
        if present
    ]
    return InputBundle(
        request_sha256=identity,
        text=text,
        images=tuple(images),
        video=video,
        modality="multimodal" if len(modalities) > 1 else modalities[0],
        seed=request.seed,
        resource_limits=asdict(limits),
    )
