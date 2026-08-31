"""Decode replay media behind a fail-closed native sandbox before promotion.

The module never imports Pillow and never trusts runtime-evidence claims.  PNG
and MP4 bytes are opened once, content-bound, and completely decoded by the
configured static FFmpeg adapter.  The only production adapter runs behind the
native Landlock/seccomp/cgroup seam in :mod:`media_sandbox`; an unavailable
delegated ``supervisor``/``jobs`` topology is a stable failure, not a fallback.

FFmpeg receives a fresh inherited regular descriptor through its ``fd:``
protocol for every invocation.  It receives no media pathname.  PNG output is
explicitly converted to RGBA.  MP4 output is not converted: strict framemd5
frame sizes prove 8-bit 4:2:0 output and therefore reject 4:2:2, 4:4:4, and
high-bit-depth streams.  MP4 sample aspect ratio must be ``1/1`` or the FFmpeg
``0/1`` sentinel, which is retained as an observed "undeclared, square by
default" fact rather than rewritten.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from pathlib import Path
from typing import Protocol

from .media_sandbox import (
    MediaSandbox,
    NativeSandboxIdentity,
    SandboxCapture,
    SandboxError,
    SandboxMetrics,
    SandboxReason,
)

_PREVIEW_PNG_PATHS = (
    "preview_head.png",
    "preview_segmentation.png",
    "preview_world_left.png",
    "preview_world_right.png",
)
_OBSERVER_PNG_PATHS = (
    "observer_start.png",
    "observer_mid.png",
    "observer_end.png",
)
_VIDEO_PATH = "observer_runtime.mp4"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_IHDR_BYTES = 13
_HASH_CHUNK_BYTES = 1024 * 1024
_MAX_TOP_LEVEL_BOXES = 1024
_MAX_FTYP_BYTES = 4096
_ALLOWED_MP4_BRANDS = {
    b"avc1",
    b"iso2",
    b"iso3",
    b"iso4",
    b"iso5",
    b"iso6",
    b"isom",
    b"mp41",
    b"mp42",
}
_FRAME_LINE = re.compile(rb"0,\s*(-?\d+),\s*(-?\d+),\s*(-?\d+),\s*(-?\d+),\s*([0-9a-f]{64})\Z")
_DIMENSIONS_HEADER = re.compile(rb"#dimensions 0:\s*(\d+)x(\d+)\Z")
_TIME_BASE_HEADER = re.compile(rb"#tb 0:\s*(-?\d+)/(-?\d+)\Z")
_SAR_HEADER = re.compile(rb"#sar 0:\s*(-?\d+)/(-?\d+)\Z")
_SOFTWARE_HEADER = re.compile(rb"#software:\s*[\x21-\x7e](?:[\x20-\x7e]*[\x21-\x7e])?\Z")
_STREAM_HEADER = re.compile(rb"#stream#,\s*dts,\s*pts,\s*duration,\s*size,\s*hash\Z")
_COMMON_FFMPEG_ARGUMENTS = (
    "-nostdin",
    "-cpuflags",
    "0",
    "-threads",
    "1",
    "-filter_threads",
    "1",
    "-filter_complex_threads",
    "1",
    "-v",
    "error",
    "-xerror",
)


class MediaVerificationReason(str, Enum):
    """Stable path-free reasons suitable for typed harness failures."""

    CONFIGURATION_INVALID = "configuration_invalid"
    BINARY_INVALID = "binary_invalid"
    BINARY_DRIFT = "binary_drift"
    DEPENDENCY_INVALID = "dependency_invalid"
    DEPENDENCY_DRIFT = "dependency_drift"
    REQUEST_INVALID = "request_invalid"
    MEDIA_SET_INVALID = "media_set_invalid"
    MEDIA_UNSAFE = "media_unsafe"
    MEDIA_TOO_LARGE = "media_too_large"
    MEDIA_CHANGED = "media_changed"
    PNG_DECODE_FAILED = "png_decode_failed"
    VIDEO_CONTAINER_INVALID = "video_container_invalid"
    VIDEO_STREAM_INVALID = "video_stream_invalid"
    VIDEO_CODEC_UNSUPPORTED = "video_codec_unsupported"
    VIDEO_PIXEL_FORMAT_UNSUPPORTED = "video_pixel_format_unsupported"
    VIDEO_PIXELS_EXCEEDED = "video_pixels_exceeded"
    VIDEO_ASPECT_RATIO_INVALID = "video_aspect_ratio_invalid"
    VIDEO_FRAME_COUNT_MISMATCH = "video_frame_count_mismatch"
    VIDEO_FRAMES_NOT_UNIQUE = "video_frames_not_unique"
    VIDEO_FPS_MISMATCH = "video_fps_mismatch"
    OBSERVER_DIMENSIONS_MISMATCH = "observer_dimensions_mismatch"
    SANDBOX_UNAVAILABLE = "sandbox_unavailable"
    SANDBOX_FAILED = "sandbox_failed"
    SANDBOX_VIOLATION = "sandbox_violation"
    DECODE_TIMEOUT = "decode_timeout"
    DECODE_OUTPUT_LIMIT = "decode_output_limit"
    DECODE_MEMORY_LIMIT = "decode_memory_limit"
    DECODE_PROCESS_LIMIT = "decode_process_limit"
    DECODE_CPU_LIMIT = "decode_cpu_limit"
    DECODE_FAILED = "decode_failed"
    DECODE_INVALID = "decode_invalid"


_REASON_MESSAGES = {reason: reason.value.replace("_", " ") for reason in MediaVerificationReason}


class MediaVerificationError(RuntimeError):
    """A verification failure that exposes no decoder output or host path."""

    def __init__(
        self,
        reason: MediaVerificationReason,
        *,
        sandbox_metrics: SandboxMetrics | None = None,
    ) -> None:
        self.reason = reason
        self.sandbox_metrics = sandbox_metrics
        super().__init__(_REASON_MESSAGES[reason])


@dataclass(frozen=True, slots=True)
class ReplayMediaToolchainIdentity:
    """Content identity of the held static executable and its launcher."""

    ffmpeg_sha256: str
    ffmpeg_bytes: int
    launcher_sha256: str
    launcher_bytes: int


@dataclass(frozen=True, slots=True)
class ReplayMediaVerifierIdentity:
    """Path-free identity of the complete decoder, isolation, and flag policy."""

    sandbox_identity: NativeSandboxIdentity
    implementation_sha256: str
    implementation_bytes: int
    max_media_bytes: int
    max_png_pixels: int
    max_video_pixels: int
    max_alloc_bytes: int
    probesize_bytes: int
    analyzeduration_microseconds: int
    common_ffmpeg_arguments: tuple[str, ...]
    png_ffmpeg_arguments: tuple[str, ...]
    video_ffmpeg_arguments: tuple[str, ...]
    input_transport: str = "fd"
    schema_version: str = "harness.replay_media_verifier_identity.v3"

    @property
    def canonical_bytes(self) -> bytes:
        document = {
            "schema_version": self.schema_version,
            "sandbox": {
                "sha256": self.sandbox_identity.sha256,
                "document": json.loads(self.sandbox_identity.canonical_bytes),
            },
            "implementation": {
                "sha256": self.implementation_sha256,
                "bytes": self.implementation_bytes,
            },
            "input_transport": self.input_transport,
            "max_media_bytes": self.max_media_bytes,
            "max_png_pixels": self.max_png_pixels,
            "max_video_pixels": self.max_video_pixels,
            "ffmpeg": {
                "max_alloc_bytes": self.max_alloc_bytes,
                "probesize_bytes": self.probesize_bytes,
                "analyzeduration_microseconds": self.analyzeduration_microseconds,
                "max_streams": 1,
                "common_arguments": list(self.common_ffmpeg_arguments),
                "png_arguments": list(self.png_ffmpeg_arguments),
                "video_arguments": list(self.video_ffmpeg_arguments),
                "png_input_format": "png_pipe",
                "png_decoder": "png",
                "png_output_format": "rgba",
                "video_input_format": "mov",
                "video_decoder": "h264",
                "video_output_format": "unconverted-strict-8bit-420-sample-count",
            },
            "mp4_byte_policy": {
                "max_top_level_boxes": _MAX_TOP_LEVEL_BOXES,
                "max_ftyp_bytes": _MAX_FTYP_BYTES,
                "allowed_brands": sorted(brand.decode("ascii") for brand in _ALLOWED_MP4_BRANDS),
            },
        }
        return (
            json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        ).encode("ascii")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()


@dataclass(frozen=True, slots=True)
class PngMediaVerification:
    """Facts from one fully decoded PNG and its isolated decoder run."""

    relative_path: str
    sha256: str
    bytes: int
    width: int
    height: int
    mode: str
    sandbox_metrics: SandboxMetrics
    format: str = "PNG"


@dataclass(frozen=True, slots=True)
class VideoMediaVerification:
    """Facts measured from the only fully decoded MP4 video stream."""

    relative_path: str
    sha256: str
    bytes: int
    frame_count: int
    unique_frame_count: int
    fps_numerator: int
    fps_denominator: int
    width: int
    height: int
    format_name: str
    codec_name: str
    pixel_format: str
    sample_aspect_ratio: str
    square_sample_aspect_ratio_defaulted: bool
    sandbox_metrics: SandboxMetrics

    @property
    def fps(self) -> float:
        return self.fps_numerator / self.fps_denominator


@dataclass(frozen=True, slots=True)
class ReplayMediaVerification:
    """Immutable success record for the entire fixed replay media set."""

    pngs: tuple[PngMediaVerification, ...]
    video: VideoMediaVerification | None
    toolchain: ReplayMediaToolchainIdentity
    verifier_identity: ReplayMediaVerifierIdentity


class ReplayMediaVerifier(Protocol):
    """Production seam consumed by the replay handler."""

    @property
    def identity(self) -> ReplayMediaVerifierIdentity: ...

    def verify(
        self,
        media: Mapping[str, Path],
        *,
        expected_video_frames: int,
        expected_fps: int,
    ) -> ReplayMediaVerification: ...


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    path: Path
    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int
    changed_ns: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _OpenMedia:
    identity: _FileIdentity
    file_descriptor: int


@dataclass(frozen=True, slots=True)
class _ImplementationIdentity:
    sha256: str
    bytes: int


@dataclass(frozen=True, slots=True)
class _DecodedManifest:
    width: int
    height: int
    time_base: Fraction
    sample_aspect_ratio: Fraction
    hashes: tuple[bytes, ...]


class SubprocessReplayMediaVerifier:
    """Verify fixed replay media through an injected isolated decoder adapter."""

    def __init__(
        self,
        *,
        sandbox: MediaSandbox,
        max_media_bytes: int = 512 * 1024 * 1024,
        max_png_pixels: int = 16_777_216,
        max_video_pixels: int = 16_777_216,
        max_alloc_bytes: int = 512 * 1024 * 1024,
        probesize_bytes: int = 8 * 1024 * 1024,
        analyzeduration_microseconds: int = 5_000_000,
    ) -> None:
        limits = (
            max_media_bytes,
            max_png_pixels,
            max_video_pixels,
            max_alloc_bytes,
            probesize_bytes,
            analyzeduration_microseconds,
        )
        if any(type(limit) is not int or limit <= 0 for limit in limits):
            raise MediaVerificationError(MediaVerificationReason.CONFIGURATION_INVALID)
        try:
            sandbox_identity = sandbox.identity
            _validate_sandbox_identity(sandbox_identity)
        except (AttributeError, TypeError, ValueError):
            raise MediaVerificationError(MediaVerificationReason.DEPENDENCY_INVALID) from None
        self._sandbox = sandbox
        self._sandbox_identity = sandbox_identity
        self._max_media_bytes = max_media_bytes
        self._max_png_pixels = max_png_pixels
        self._max_video_pixels = max_video_pixels
        self._max_alloc_bytes = max_alloc_bytes
        self._probesize_bytes = probesize_bytes
        self._analyzeduration_microseconds = analyzeduration_microseconds
        self._implementation_identity = _measure_implementation()
        input_arguments = (
            "-max_alloc",
            str(max_alloc_bytes),
            "-probesize",
            str(probesize_bytes),
            "-analyzeduration",
            str(analyzeduration_microseconds),
            "-max_streams",
            "1",
            "-protocol_whitelist",
            "fd,pipe",
        )
        self._png_arguments = (
            *_COMMON_FFMPEG_ARGUMENTS,
            *input_arguments,
            "-f",
            "png_pipe",
            "-c:v",
            "png",
            "-fd",
            "4",
            "-i",
            "fd:",
            "-map",
            "0",
            "-vf",
            "format=rgba",
            "-f",
            "framemd5",
            "-hash",
            "sha256",
            "pipe:1",
        )
        self._video_arguments = (
            *_COMMON_FFMPEG_ARGUMENTS,
            *input_arguments,
            "-f",
            "mov",
            "-c:v",
            "h264",
            "-fd",
            "4",
            "-i",
            "fd:",
            "-map",
            "0",
            "-f",
            "framemd5",
            "-hash",
            "sha256",
            "pipe:1",
        )
        self._identity = ReplayMediaVerifierIdentity(
            sandbox_identity=sandbox_identity,
            implementation_sha256=self._implementation_identity.sha256,
            implementation_bytes=self._implementation_identity.bytes,
            max_media_bytes=max_media_bytes,
            max_png_pixels=max_png_pixels,
            max_video_pixels=max_video_pixels,
            max_alloc_bytes=max_alloc_bytes,
            probesize_bytes=probesize_bytes,
            analyzeduration_microseconds=analyzeduration_microseconds,
            common_ffmpeg_arguments=_COMMON_FFMPEG_ARGUMENTS,
            png_ffmpeg_arguments=self._png_arguments,
            video_ffmpeg_arguments=self._video_arguments,
        )
        self._toolchain = ReplayMediaToolchainIdentity(
            ffmpeg_sha256=sandbox_identity.ffmpeg_sha256,
            ffmpeg_bytes=sandbox_identity.ffmpeg_bytes,
            launcher_sha256=sandbox_identity.launcher_sha256,
            launcher_bytes=sandbox_identity.launcher_bytes,
        )

    @property
    def identity(self) -> ReplayMediaVerifierIdentity:
        return self._identity

    def verify(
        self,
        media: Mapping[str, Path],
        *,
        expected_video_frames: int,
        expected_fps: int,
    ) -> ReplayMediaVerification:
        self._require_dependencies_unchanged()
        if (
            type(expected_video_frames) is not int
            or expected_video_frames < 0
            or type(expected_fps) is not int
            or expected_fps <= 0
        ):
            raise MediaVerificationError(MediaVerificationReason.REQUEST_INVALID)
        expected_paths = _PREVIEW_PNG_PATHS
        if expected_video_frames > 0:
            expected_paths += _OBSERVER_PNG_PATHS + (_VIDEO_PATH,)
        paths = _validate_media_mapping(media, expected_paths)
        with ExitStack() as stack:
            opened: dict[str, _OpenMedia] = {}
            total_bytes = 0
            for relative_path in expected_paths:
                item = _open_media(paths[relative_path], max_bytes=self._max_media_bytes)
                stack.callback(os.close, item.file_descriptor)
                total_bytes += item.identity.size
                if total_bytes > self._max_media_bytes:
                    raise MediaVerificationError(MediaVerificationReason.MEDIA_TOO_LARGE)
                opened[relative_path] = item
            try:
                pngs = tuple(
                    self._decode_png(relative_path, opened[relative_path])
                    for relative_path in expected_paths
                    if relative_path.endswith(".png")
                )
                video = None
                if expected_video_frames > 0:
                    video = self._decode_video(
                        opened[_VIDEO_PATH],
                        expected_video_frames=expected_video_frames,
                        expected_fps=expected_fps,
                    )
                    observer_dimensions = {
                        item.relative_path: (item.width, item.height)
                        for item in pngs
                        if item.relative_path in _OBSERVER_PNG_PATHS
                    }
                    if any(
                        observer_dimensions[path] != (video.width, video.height)
                        for path in _OBSERVER_PNG_PATHS
                    ):
                        raise MediaVerificationError(
                            MediaVerificationReason.OBSERVER_DIMENSIONS_MISMATCH
                        )
            except MediaVerificationError:
                _require_media_unchanged(opened.values())
                self._require_dependencies_unchanged()
                raise
            _require_media_unchanged(opened.values())
            self._require_dependencies_unchanged()
            return ReplayMediaVerification(
                pngs=pngs,
                video=video,
                toolchain=self._toolchain,
                verifier_identity=self._identity,
            )

    def _require_dependencies_unchanged(self) -> None:
        try:
            current_identity = self._sandbox.identity
            _validate_sandbox_identity(current_identity)
            current_implementation = _measure_implementation()
        except (AttributeError, TypeError, ValueError, MediaVerificationError):
            raise MediaVerificationError(MediaVerificationReason.DEPENDENCY_DRIFT) from None
        if (
            current_identity != self._sandbox_identity
            or current_implementation != self._implementation_identity
        ):
            raise MediaVerificationError(MediaVerificationReason.DEPENDENCY_DRIFT)

    def _decode_png(self, relative_path: str, opened: _OpenMedia) -> PngMediaVerification:
        width, height = _parse_png_header(
            opened.file_descriptor,
            opened.identity.size,
            max_pixels=self._max_png_pixels,
        )
        capture = _run_sandbox(self._sandbox, opened.file_descriptor, self._png_arguments)
        if capture.exit_code != 0 or capture.stderr:
            raise MediaVerificationError(
                MediaVerificationReason.PNG_DECODE_FAILED,
                sandbox_metrics=capture.metrics,
            )
        try:
            decoded = _parse_framemd5(
                capture.stdout,
                expected_dimensions=(width, height),
                expected_time_base=Fraction(1, 25),
                allowed_sample_aspect_ratios=(Fraction(0, 1),),
                expected_frame_bytes=4 * width * height,
            )
        except MediaVerificationError:
            raise MediaVerificationError(
                MediaVerificationReason.PNG_DECODE_FAILED,
                sandbox_metrics=capture.metrics,
            ) from None
        if len(decoded.hashes) != 1:
            raise MediaVerificationError(
                MediaVerificationReason.PNG_DECODE_FAILED,
                sandbox_metrics=capture.metrics,
            )
        return PngMediaVerification(
            relative_path=relative_path,
            sha256=opened.identity.sha256,
            bytes=opened.identity.size,
            width=width,
            height=height,
            mode="RGBA",
            sandbox_metrics=capture.metrics,
        )

    def _decode_video(
        self,
        opened: _OpenMedia,
        *,
        expected_video_frames: int,
        expected_fps: int,
    ) -> VideoMediaVerification:
        _validate_mp4_boxes(opened.file_descriptor, opened.identity.size)
        capture = _run_sandbox(self._sandbox, opened.file_descriptor, self._video_arguments)
        if capture.exit_code != 0 or capture.stderr:
            raise MediaVerificationError(
                MediaVerificationReason.DECODE_FAILED,
                sandbox_metrics=capture.metrics,
            )
        try:
            decoded = _parse_video_framemd5(
                capture.stdout,
                expected_fps=expected_fps,
                max_video_pixels=self._max_video_pixels,
            )
        except MediaVerificationError as caught:
            raise MediaVerificationError(
                caught.reason,
                sandbox_metrics=capture.metrics,
            ) from None
        if len(decoded.hashes) != expected_video_frames:
            raise MediaVerificationError(
                MediaVerificationReason.VIDEO_FRAME_COUNT_MISMATCH,
                sandbox_metrics=capture.metrics,
            )
        unique_frames = len(set(decoded.hashes))
        if expected_video_frames > 1 and unique_frames < 2:
            raise MediaVerificationError(
                MediaVerificationReason.VIDEO_FRAMES_NOT_UNIQUE,
                sandbox_metrics=capture.metrics,
            )
        observed_sar = (
            f"{decoded.sample_aspect_ratio.numerator}/{decoded.sample_aspect_ratio.denominator}"
        )
        return VideoMediaVerification(
            relative_path=_VIDEO_PATH,
            sha256=opened.identity.sha256,
            bytes=opened.identity.size,
            frame_count=len(decoded.hashes),
            unique_frame_count=unique_frames,
            fps_numerator=expected_fps,
            fps_denominator=1,
            width=decoded.width,
            height=decoded.height,
            format_name="iso-bmff/mp4",
            codec_name="h264",
            pixel_format="8bit-420",
            sample_aspect_ratio=observed_sar,
            square_sample_aspect_ratio_defaulted=decoded.sample_aspect_ratio == 0,
            sandbox_metrics=capture.metrics,
        )


def _validate_sandbox_identity(identity: NativeSandboxIdentity) -> None:
    if not isinstance(identity, NativeSandboxIdentity):
        raise TypeError("invalid sandbox identity")
    canonical = identity.canonical_bytes
    if not isinstance(canonical, bytes) or hashlib.sha256(canonical).hexdigest() != identity.sha256:
        raise ValueError("invalid sandbox identity digest")
    document = json.loads(canonical)
    if not isinstance(document, dict):
        raise ValueError("invalid sandbox identity document")


def _measure_implementation() -> _ImplementationIdentity:
    try:
        payload = Path(__file__).read_bytes()
    except OSError:
        raise MediaVerificationError(MediaVerificationReason.DEPENDENCY_INVALID) from None
    return _ImplementationIdentity(sha256=hashlib.sha256(payload).hexdigest(), bytes=len(payload))


def _validate_media_mapping(
    media: Mapping[str, Path], expected_paths: tuple[str, ...]
) -> dict[str, Path]:
    if not isinstance(media, Mapping):
        raise MediaVerificationError(MediaVerificationReason.MEDIA_SET_INVALID)
    try:
        materialized = dict(media.items())
    except Exception:
        raise MediaVerificationError(MediaVerificationReason.MEDIA_SET_INVALID) from None
    if set(materialized) != set(expected_paths):
        raise MediaVerificationError(MediaVerificationReason.MEDIA_SET_INVALID)
    ordered = {relative: materialized[relative] for relative in expected_paths}
    if any(not isinstance(path, Path) for path in ordered.values()):
        raise MediaVerificationError(MediaVerificationReason.MEDIA_UNSAFE)
    if any(path.name != relative for relative, path in ordered.items()):
        raise MediaVerificationError(MediaVerificationReason.MEDIA_UNSAFE)
    if len({path.parent for path in ordered.values()}) != 1:
        raise MediaVerificationError(MediaVerificationReason.MEDIA_UNSAFE)
    return ordered


def _open_media(path: Path, *, max_bytes: int) -> _OpenMedia:
    if not path.is_absolute():
        raise MediaVerificationError(MediaVerificationReason.MEDIA_UNSAFE)
    try:
        if path.resolve(strict=True) != path:
            raise OSError("path traverses a symlink")
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError:
        raise MediaVerificationError(MediaVerificationReason.MEDIA_UNSAFE) from None
    try:
        before = os.fstat(descriptor)
        path_info = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or (before.st_dev, before.st_ino) != (
            path_info.st_dev,
            path_info.st_ino,
        ):
            raise MediaVerificationError(MediaVerificationReason.MEDIA_UNSAFE)
        if before.st_size > max_bytes:
            raise MediaVerificationError(MediaVerificationReason.MEDIA_TOO_LARGE)
        digest = _hash_descriptor(descriptor, before.st_size)
        after = os.fstat(descriptor)
        if _stat_key(before) != _stat_key(after):
            raise MediaVerificationError(MediaVerificationReason.MEDIA_UNSAFE)
        identity = _FileIdentity(
            path=path,
            device=after.st_dev,
            inode=after.st_ino,
            mode=after.st_mode,
            size=after.st_size,
            modified_ns=after.st_mtime_ns,
            changed_ns=after.st_ctime_ns,
            sha256=digest,
        )
        opened = _OpenMedia(identity=identity, file_descriptor=descriptor)
        _require_media_file_unchanged(opened)
        return opened
    except MediaVerificationError:
        os.close(descriptor)
        raise
    except OSError:
        os.close(descriptor)
        raise MediaVerificationError(MediaVerificationReason.MEDIA_UNSAFE) from None


def _hash_descriptor(descriptor: int, size: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while offset < size:
        chunk = os.pread(descriptor, min(_HASH_CHUNK_BYTES, size - offset), offset)
        if not chunk:
            raise OSError("file changed while hashing")
        digest.update(chunk)
        offset += len(chunk)
    return digest.hexdigest()


def _stat_key(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _require_media_file_unchanged(opened: _OpenMedia) -> None:
    expected = opened.identity
    try:
        current = os.fstat(opened.file_descriptor)
        path_info = os.stat(expected.path, follow_symlinks=False)
        if expected.path.resolve(strict=True) != expected.path:
            raise OSError("path changed")
        if (
            _stat_key(current)
            != (
                expected.device,
                expected.inode,
                expected.mode,
                expected.size,
                expected.modified_ns,
                expected.changed_ns,
            )
            or (current.st_dev, current.st_ino) != (path_info.st_dev, path_info.st_ino)
            or _hash_descriptor(opened.file_descriptor, current.st_size) != expected.sha256
        ):
            raise OSError("media changed")
    except OSError:
        raise MediaVerificationError(MediaVerificationReason.MEDIA_CHANGED) from None


def _require_media_unchanged(opened: object) -> None:
    for item in opened:
        _require_media_file_unchanged(item)


def _parse_png_header(descriptor: int, size: int, *, max_pixels: int) -> tuple[int, int]:
    try:
        header = os.pread(descriptor, 33, 0)
    except OSError:
        raise MediaVerificationError(MediaVerificationReason.PNG_DECODE_FAILED) from None
    if (
        size < 33
        or len(header) != 33
        or header[:8] != _PNG_SIGNATURE
        or int.from_bytes(header[8:12], "big") != _PNG_IHDR_BYTES
        or header[12:16] != b"IHDR"
    ):
        raise MediaVerificationError(MediaVerificationReason.PNG_DECODE_FAILED)
    width = int.from_bytes(header[16:20], "big")
    height = int.from_bytes(header[20:24], "big")
    if (
        width <= 0
        or height <= 0
        or width * height > max_pixels
        or header[26] != 0
        or header[27] != 0
        or header[28] not in {0, 1}
    ):
        raise MediaVerificationError(MediaVerificationReason.PNG_DECODE_FAILED)
    return width, height


def _validate_mp4_boxes(descriptor: int, size: int) -> None:
    try:
        _scan_mp4_boxes(descriptor, size)
    except OSError:
        raise MediaVerificationError(MediaVerificationReason.VIDEO_CONTAINER_INVALID) from None


def _scan_mp4_boxes(descriptor: int, size: int) -> None:
    offset = 0
    counts: dict[bytes, int] = {}
    brands: set[bytes] = set()
    boxes = 0
    while offset < size:
        boxes += 1
        if boxes > _MAX_TOP_LEVEL_BOXES:
            raise MediaVerificationError(MediaVerificationReason.VIDEO_CONTAINER_INVALID)
        header = os.pread(descriptor, 16, offset)
        if len(header) < 8:
            raise MediaVerificationError(MediaVerificationReason.VIDEO_CONTAINER_INVALID)
        box_size = int.from_bytes(header[:4], "big")
        box_type = header[4:8]
        header_bytes = 8
        if box_size == 1:
            if len(header) < 16:
                raise MediaVerificationError(MediaVerificationReason.VIDEO_CONTAINER_INVALID)
            box_size = int.from_bytes(header[8:16], "big")
            header_bytes = 16
        if (
            box_size == 0
            or box_size < header_bytes
            or box_size > size - offset
            or re.fullmatch(rb"[A-Za-z0-9 ]{4}", box_type) is None
        ):
            raise MediaVerificationError(MediaVerificationReason.VIDEO_CONTAINER_INVALID)
        counts[box_type] = counts.get(box_type, 0) + 1
        if offset == 0:
            if box_type != b"ftyp" or box_size > _MAX_FTYP_BYTES or header_bytes != 8:
                raise MediaVerificationError(MediaVerificationReason.VIDEO_CONTAINER_INVALID)
            payload = os.pread(descriptor, box_size - header_bytes, offset + header_bytes)
            if len(payload) != box_size - header_bytes or len(payload) < 8 or len(payload) % 4:
                raise MediaVerificationError(MediaVerificationReason.VIDEO_CONTAINER_INVALID)
            brands = {
                payload[:4],
                *(payload[index : index + 4] for index in range(8, len(payload), 4)),
            }
        offset += box_size
    if (
        offset != size
        or counts.get(b"ftyp") != 1
        or counts.get(b"moov") != 1
        or counts.get(b"mdat", 0) < 1
        or not brands & _ALLOWED_MP4_BRANDS
    ):
        raise MediaVerificationError(MediaVerificationReason.VIDEO_CONTAINER_INVALID)


def _run_sandbox(
    sandbox: MediaSandbox,
    media_fd: int,
    arguments: tuple[str, ...],
) -> SandboxCapture:
    try:
        capture = sandbox.run(media_fd, arguments)
    except SandboxError as error:
        mapping = {
            SandboxReason.TOOL_DRIFT: MediaVerificationReason.DEPENDENCY_DRIFT,
            SandboxReason.MEDIA_DRIFT: MediaVerificationReason.MEDIA_CHANGED,
            SandboxReason.CGROUP_UNAVAILABLE: MediaVerificationReason.SANDBOX_UNAVAILABLE,
            SandboxReason.SANDBOX_UNAVAILABLE: MediaVerificationReason.SANDBOX_UNAVAILABLE,
            SandboxReason.TIMEOUT: MediaVerificationReason.DECODE_TIMEOUT,
            SandboxReason.OUTPUT_LIMIT: MediaVerificationReason.DECODE_OUTPUT_LIMIT,
            SandboxReason.RESOURCE_MEMORY: MediaVerificationReason.DECODE_MEMORY_LIMIT,
            SandboxReason.RESOURCE_PIDS: MediaVerificationReason.DECODE_PROCESS_LIMIT,
            SandboxReason.RESOURCE_CPU: MediaVerificationReason.DECODE_CPU_LIMIT,
            SandboxReason.SECCOMP_VIOLATION: MediaVerificationReason.SANDBOX_VIOLATION,
        }
        raise MediaVerificationError(
            mapping.get(error.reason, MediaVerificationReason.SANDBOX_FAILED),
            sandbox_metrics=error.metrics,
        ) from None
    if not isinstance(capture, SandboxCapture):
        raise MediaVerificationError(MediaVerificationReason.SANDBOX_FAILED)
    return capture


def _parse_video_framemd5(
    payload: bytes,
    *,
    expected_fps: int,
    max_video_pixels: int,
) -> _DecodedManifest:
    header = _parse_framemd5_headers(payload)
    width, height = header[0]
    if width <= 0 or height <= 0 or width % 2 or height % 2:
        raise MediaVerificationError(MediaVerificationReason.VIDEO_PIXEL_FORMAT_UNSUPPORTED)
    if width * height > max_video_pixels:
        raise MediaVerificationError(MediaVerificationReason.VIDEO_PIXELS_EXCEEDED)
    return _parse_framemd5(
        payload,
        expected_dimensions=(width, height),
        expected_time_base=Fraction(1, expected_fps),
        allowed_sample_aspect_ratios=(Fraction(0, 1), Fraction(1, 1)),
        expected_frame_bytes=width * height * 3 // 2,
    )


def _parse_framemd5_headers(payload: bytes) -> tuple[tuple[int, int], Fraction, Fraction]:
    lines = _canonical_manifest_lines(payload)
    if len(lines) < 10:
        raise MediaVerificationError(MediaVerificationReason.DECODE_INVALID)
    dimensions = _DIMENSIONS_HEADER.fullmatch(lines[7])
    time_base = _TIME_BASE_HEADER.fullmatch(lines[4])
    sample_aspect_ratio = _SAR_HEADER.fullmatch(lines[8])
    if (
        lines[0] != b"#format: frame checksums"
        or lines[1] != b"#version: 2"
        or lines[2] != b"#hash: SHA256"
        or _SOFTWARE_HEADER.fullmatch(lines[3]) is None
        or time_base is None
        or lines[5] != b"#media_type 0: video"
        or lines[6] != b"#codec_id 0: rawvideo"
        or dimensions is None
        or sample_aspect_ratio is None
        or _STREAM_HEADER.fullmatch(lines[9]) is None
    ):
        raise MediaVerificationError(MediaVerificationReason.DECODE_INVALID)
    try:
        parsed_time_base = Fraction(int(time_base.group(1)), int(time_base.group(2)))
        parsed_sar = Fraction(int(sample_aspect_ratio.group(1)), int(sample_aspect_ratio.group(2)))
    except ZeroDivisionError:
        raise MediaVerificationError(MediaVerificationReason.DECODE_INVALID) from None
    return (
        (int(dimensions.group(1)), int(dimensions.group(2))),
        parsed_time_base,
        parsed_sar,
    )


def _canonical_manifest_lines(payload: bytes) -> tuple[bytes, ...]:
    if not isinstance(payload, bytes) or not payload.endswith(b"\n") or b"\r" in payload:
        raise MediaVerificationError(MediaVerificationReason.DECODE_INVALID)
    lines = tuple(payload[:-1].split(b"\n"))
    if any(not line or line != line.strip() for line in lines):
        raise MediaVerificationError(MediaVerificationReason.DECODE_INVALID)
    return lines


def _parse_framemd5(
    payload: bytes,
    *,
    expected_dimensions: tuple[int, int],
    expected_time_base: Fraction,
    allowed_sample_aspect_ratios: tuple[Fraction, ...],
    expected_frame_bytes: int,
) -> _DecodedManifest:
    lines = _canonical_manifest_lines(payload)
    dimensions, time_base, sample_aspect_ratio = _parse_framemd5_headers(payload)
    if dimensions != expected_dimensions:
        raise MediaVerificationError(MediaVerificationReason.DECODE_INVALID)
    if time_base != expected_time_base:
        raise MediaVerificationError(MediaVerificationReason.VIDEO_FPS_MISMATCH)
    expected_time_base_line = f"#tb 0: 1/{expected_time_base.denominator}".encode("ascii")
    if expected_time_base.numerator != 1 or lines[4] != expected_time_base_line:
        raise MediaVerificationError(MediaVerificationReason.DECODE_INVALID)
    if sample_aspect_ratio not in allowed_sample_aspect_ratios:
        raise MediaVerificationError(MediaVerificationReason.VIDEO_ASPECT_RATIO_INVALID)
    hashes: list[bytes] = []
    for expected_timestamp, line in enumerate(lines[10:]):
        frame = _FRAME_LINE.fullmatch(line)
        if frame is None:
            raise MediaVerificationError(MediaVerificationReason.DECODE_INVALID)
        dts = int(frame.group(1))
        pts = int(frame.group(2))
        duration = int(frame.group(3))
        frame_bytes = int(frame.group(4))
        if (
            dts != expected_timestamp
            or pts != expected_timestamp
            or duration != 1
            or frame_bytes != expected_frame_bytes
        ):
            if frame_bytes != expected_frame_bytes:
                raise MediaVerificationError(MediaVerificationReason.VIDEO_PIXEL_FORMAT_UNSUPPORTED)
            raise MediaVerificationError(MediaVerificationReason.DECODE_INVALID)
        hashes.append(frame.group(5))
    return _DecodedManifest(
        width=dimensions[0],
        height=dimensions[1],
        time_base=time_base,
        sample_aspect_ratio=sample_aspect_ratio,
        hashes=tuple(hashes),
    )
