from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import self_improving.harness.media_verifier as media_verifier_module
from self_improving.harness.media_sandbox import (
    NativeCgroupSandbox,
    NativeSandboxIdentity,
    SandboxCapture,
    SandboxError,
    SandboxMetrics,
    SandboxPolicy,
    SandboxReason,
)
from self_improving.harness.media_verifier import (
    MediaVerificationError,
    MediaVerificationReason,
    ReplayMediaVerifier,
    SubprocessReplayMediaVerifier,
)

_PREVIEWS = (
    "preview_head.png",
    "preview_segmentation.png",
    "preview_world_left.png",
    "preview_world_right.png",
)
_OBSERVERS = ("observer_start.png", "observer_mid.png", "observer_end.png")
_ROOT = Path(__file__).parents[3]
_LAUNCHER_SOURCE = _ROOT / "self_improving/harness/native/media_sandbox.c"
_STATIC_FFMPEG = Path(
    os.environ.get(
        "MEDIA_SANDBOX_STATIC_FFMPEG",
        "/tmp/media-ffmpeg-qualified-v7.0.2/ffmpeg",
    )
)
_HISTORICAL_REPLAY = (
    _ROOT / "self_improving/studies/ASPIRE/artifacts/real_replay_can_on_plate_seed7"
)
_QUALIFIED_FFMPEG_SHA256 = "fe08d0f51873874056abe5be5eb1d1047a1cc3eb3da0c41907047be5581ef02e"


@pytest.fixture(scope="session")
def production_launcher(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("production-media-launcher") / "media-sandbox"
    subprocess.run(
        [
            "cc",
            "-std=c17",
            "-O2",
            "-static",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(_LAUNCHER_SOURCE),
            "-o",
            str(output),
        ],
        check=True,
        capture_output=True,
    )
    return output


def _sandbox_identity() -> NativeSandboxIdentity:
    return NativeSandboxIdentity(
        launcher_sha256="1" * 64,
        launcher_bytes=10,
        launcher_source_sha256="2" * 64,
        launcher_source_bytes=20,
        ffmpeg_sha256="3" * 64,
        ffmpeg_bytes=30,
        implementation_sha256="4" * 64,
        implementation_bytes=40,
        landlock_abi=8,
        kernel_architecture="x86_64",
        kernel_release="7.0.0-test",
        policy=SandboxPolicy(),
    )


def _metrics() -> SandboxMetrics:
    return SandboxMetrics(
        memory_peak_bytes=4096,
        memory_events=(("low", 0), ("oom", 0), ("oom_kill", 0)),
        pids_peak=1,
        pids_events=(("max", 0),),
        cpu_stats=(("usage_usec", 1),),
    )


def _manifest(
    *,
    width: int,
    height: int,
    fps: int,
    hashes: tuple[str, ...],
    rgba: bool,
    sar: str = "0/1",
) -> bytes:
    frame_bytes = width * height * (4 if rgba else 3) // (1 if rgba else 2)
    lines = [
        "#format: frame checksums",
        "#version: 2",
        "#hash: SHA256",
        "#software: Lavf61.1.100",
        f"#tb 0: 1/{fps}",
        "#media_type 0: video",
        "#codec_id 0: rawvideo",
        f"#dimensions 0: {width}x{height}",
        f"#sar 0: {sar}",
        "#stream#, dts, pts, duration, size, hash",
    ]
    lines.extend(
        f"0, {index}, {index}, 1, {frame_bytes}, {digest}" for index, digest in enumerate(hashes)
    )
    return ("\n".join(lines) + "\n").encode("ascii")


@dataclass
class FakeSandbox:
    identity: NativeSandboxIdentity = field(default_factory=_sandbox_identity)
    video_manifest: bytes | None = None
    exit_code: int = 0
    stderr: bytes = b""
    failure: SandboxError | None = None
    mutate_path: Path | None = None
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def run(self, media_fd: int, arguments: tuple[str, ...]) -> SandboxCapture:
        self.calls.append(arguments)
        if self.failure is not None:
            raise self.failure
        header = os.pread(media_fd, 24, 0)
        if header.startswith(b"\x89PNG"):
            width = int.from_bytes(header[16:20], "big")
            height = int.from_bytes(header[20:24], "big")
            stdout = _manifest(
                width=width,
                height=height,
                fps=25,
                hashes=(hashlib.sha256(header).hexdigest(),),
                rgba=True,
            )
        else:
            assert self.video_manifest is not None
            stdout = self.video_manifest
        if self.mutate_path is not None:
            payload = self.mutate_path.read_bytes()
            self.mutate_path.write_bytes(bytes((payload[0] ^ 1,)) + payload[1:])
        return SandboxCapture(
            exit_code=self.exit_code,
            stdout=stdout,
            stderr=self.stderr,
            metrics=_metrics(),
        )


def _write_png_header(path: Path, *, width: int, height: int) -> None:
    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes((8, 6, 0, 0, 0))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + ihdr + b"\0" * 4)


def _write_mp4_boxes(path: Path) -> None:
    path.write_bytes(
        (24).to_bytes(4, "big")
        + b"ftyp"
        + b"isom"
        + (0).to_bytes(4, "big")
        + b"isomavc1"
        + (8).to_bytes(4, "big")
        + b"moov"
        + (8).to_bytes(4, "big")
        + b"mdat"
    )


def _preview_media(tmp_path: Path) -> dict[str, Path]:
    media: dict[str, Path] = {}
    for index, relative in enumerate(_PREVIEWS):
        path = tmp_path / relative
        _write_png_header(path, width=8 + index, height=6 + index)
        media[relative] = path
    return media


def _video_media(tmp_path: Path) -> dict[str, Path]:
    media = _preview_media(tmp_path)
    for relative in _OBSERVERS:
        path = tmp_path / relative
        _write_png_header(path, width=16, height=12)
        media[relative] = path
    video = tmp_path / "observer_runtime.mp4"
    _write_mp4_boxes(video)
    media[video.name] = video
    return media


def test_preview_pngs_are_fully_decoded_through_the_sandbox_seam(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    verifier: ReplayMediaVerifier = SubprocessReplayMediaVerifier(sandbox=sandbox)

    result = verifier.verify(
        _preview_media(tmp_path),
        expected_video_frames=0,
        expected_fps=12,
    )

    assert [(item.relative_path, item.width, item.height, item.mode) for item in result.pngs] == [
        ("preview_head.png", 8, 6, "RGBA"),
        ("preview_segmentation.png", 9, 7, "RGBA"),
        ("preview_world_left.png", 10, 8, "RGBA"),
        ("preview_world_right.png", 11, 9, "RGBA"),
    ]
    assert result.video is None
    assert all("png_pipe" in call and "fd:" in call for call in sandbox.calls)
    assert all("-fd" in call and "4" in call for call in sandbox.calls)
    assert dataclasses.is_dataclass(result) and result.__dataclass_params__.frozen is True
    assert "PIL" not in media_verifier_module.__dict__


def test_video_result_preserves_undeclared_square_sar_and_decoded_uniqueness(
    tmp_path: Path,
) -> None:
    sandbox = FakeSandbox(
        video_manifest=_manifest(
            width=16,
            height=12,
            fps=3,
            hashes=("a" * 64, "b" * 64, "a" * 64),
            rgba=False,
            sar="0/1",
        )
    )
    verifier = SubprocessReplayMediaVerifier(sandbox=sandbox)

    result = verifier.verify(
        _video_media(tmp_path),
        expected_video_frames=3,
        expected_fps=3,
    )

    assert result.video is not None
    assert result.video.frame_count == 3
    assert result.video.unique_frame_count == 2
    assert result.video.fps == 3.0
    assert result.video.format_name == "iso-bmff/mp4"
    assert result.video.codec_name == "h264"
    assert result.video.pixel_format == "8bit-420"
    assert result.video.sample_aspect_ratio == "0/1"
    assert result.video.square_sample_aspect_ratio_defaulted is True
    video_call = sandbox.calls[-1]
    assert "mov" in video_call and "h264" in video_call
    assert "format=yuv420p" not in video_call
    assert "setsar=1/1" not in video_call
    assert video_call.count("-map") == 1 and "0" in video_call


def test_identity_v3_is_path_free_and_binds_sandbox_policy_and_exact_flags(
    tmp_path: Path,
) -> None:
    sandbox = FakeSandbox()
    verifier = SubprocessReplayMediaVerifier(sandbox=sandbox)
    document = json.loads(verifier.identity.canonical_bytes)

    assert document["schema_version"] == "harness.replay_media_verifier_identity.v3"
    assert document["sandbox"]["sha256"] == sandbox.identity.sha256
    assert document["input_transport"] == "fd"
    assert document["ffmpeg"]["max_streams"] == 1
    assert document["ffmpeg"]["video_input_format"] == "mov"
    assert document["ffmpeg"]["video_decoder"] == "h264"
    assert document["ffmpeg"]["png_output_format"] == "rgba"
    assert str(tmp_path).encode() not in verifier.identity.canonical_bytes
    assert verifier.identity.sha256 == hashlib.sha256(verifier.identity.canonical_bytes).hexdigest()


@pytest.mark.parametrize(
    ("frames", "fps"),
    ((-1, 12), (True, 12), (0, 0), (0, True)),
)
def test_request_contract_is_typed(frames: object, fps: object, tmp_path: Path) -> None:
    verifier = SubprocessReplayMediaVerifier(sandbox=FakeSandbox())
    with pytest.raises(MediaVerificationError) as caught:
        verifier.verify(
            _preview_media(tmp_path),
            expected_video_frames=frames,  # type: ignore[arg-type]
            expected_fps=fps,  # type: ignore[arg-type]
        )

    assert caught.value.reason is MediaVerificationReason.REQUEST_INVALID


def _verify_failure(
    verifier: SubprocessReplayMediaVerifier,
    media: Mapping[str, Path],
    *,
    frames: int,
    fps: int,
) -> MediaVerificationReason:
    with pytest.raises(MediaVerificationError) as caught:
        verifier.verify(media, expected_video_frames=frames, expected_fps=fps)
    return caught.value.reason


@pytest.mark.parametrize("case", ("signature", "pixels", "truncated"))
def test_png_header_attacks_are_rejected_before_decoder(
    case: str,
    tmp_path: Path,
) -> None:
    media = _preview_media(tmp_path)
    attacked = media["preview_head.png"]
    if case == "signature":
        attacked.write_bytes(b"not a png")
    elif case == "pixels":
        _write_png_header(attacked, width=101, height=1)
    else:
        attacked.write_bytes(b"\x89PNG\r\n\x1a\n")
    sandbox = FakeSandbox()
    verifier = SubprocessReplayMediaVerifier(sandbox=sandbox, max_png_pixels=100)

    assert (
        _verify_failure(verifier, media, frames=0, fps=12)
        is MediaVerificationReason.PNG_DECODE_FAILED
    )
    assert sandbox.calls == []


def test_png_requires_one_complete_rgba_frame_from_decoder(tmp_path: Path) -> None:
    malformed = _manifest(
        width=8,
        height=6,
        fps=25,
        hashes=("a" * 64,),
        rgba=True,
    ).replace(b", 192,", b", 144,")
    sandbox = FakeSandbox(video_manifest=None)

    def bad_run(media_fd: int, arguments: tuple[str, ...]) -> SandboxCapture:
        del media_fd, arguments
        return SandboxCapture(0, malformed, b"", _metrics())

    sandbox.run = bad_run  # type: ignore[method-assign]
    verifier = SubprocessReplayMediaVerifier(sandbox=sandbox)

    assert (
        _verify_failure(verifier, _preview_media(tmp_path), frames=0, fps=12)
        is MediaVerificationReason.PNG_DECODE_FAILED
    )


@pytest.mark.parametrize("case", ("extra", "symlink", "different_parent", "wrong_name"))
def test_media_allowlist_and_paths_fail_closed(case: str, tmp_path: Path) -> None:
    media = _preview_media(tmp_path)
    if case == "extra":
        extra = tmp_path / "observer_start.png"
        _write_png_header(extra, width=8, height=6)
        media[extra.name] = extra
        expected = MediaVerificationReason.MEDIA_SET_INVALID
    elif case == "symlink":
        original = media["preview_head.png"]
        linked = tmp_path / "linked" / original.name
        linked.parent.mkdir()
        linked.symlink_to(original)
        media[original.name] = linked
        expected = MediaVerificationReason.MEDIA_UNSAFE
    elif case == "different_parent":
        original = media["preview_head.png"]
        moved = tmp_path / "other" / original.name
        moved.parent.mkdir()
        moved.write_bytes(original.read_bytes())
        media[original.name] = moved
        expected = MediaVerificationReason.MEDIA_UNSAFE
    else:
        original = media["preview_head.png"]
        renamed = tmp_path / "wrong.png"
        renamed.write_bytes(original.read_bytes())
        media[original.name] = renamed
        expected = MediaVerificationReason.MEDIA_UNSAFE

    assert (
        _verify_failure(
            SubprocessReplayMediaVerifier(sandbox=FakeSandbox()), media, frames=0, fps=12
        )
        is expected
    )


@pytest.mark.parametrize("case", ("avi", "trailing", "missing_moov", "brand", "zero_box"))
def test_mp4_byte_envelope_rejects_noncanonical_or_renamed_containers(
    case: str,
    tmp_path: Path,
) -> None:
    media = _video_media(tmp_path)
    video = media["observer_runtime.mp4"]
    payload = video.read_bytes()
    if case == "avi":
        payload = b"RIFF" + b"\0" * 36
    elif case == "trailing":
        payload += b"garbage"
    elif case == "missing_moov":
        payload = payload.replace(b"moov", b"free")
    elif case == "brand":
        payload = payload.replace(b"isom", b"evil").replace(b"avc1", b"fake")
    else:
        payload = b"\0\0\0\0ftyp" + payload[8:]
    video.write_bytes(payload)
    verifier = SubprocessReplayMediaVerifier(
        sandbox=FakeSandbox(
            video_manifest=_manifest(
                width=16,
                height=12,
                fps=3,
                hashes=("a" * 64, "b" * 64, "c" * 64),
                rgba=False,
            )
        )
    )

    assert (
        _verify_failure(verifier, media, frames=3, fps=3)
        is MediaVerificationReason.VIDEO_CONTAINER_INVALID
    )
    assert len(verifier._sandbox.calls) == 7  # type: ignore[attr-defined]


def _video_verifier(manifest: bytes) -> SubprocessReplayMediaVerifier:
    return SubprocessReplayMediaVerifier(sandbox=FakeSandbox(video_manifest=manifest))


@pytest.mark.parametrize(
    ("manifest", "reason"),
    (
        (
            _manifest(
                width=16,
                height=12,
                fps=4,
                hashes=("a" * 64, "b" * 64, "c" * 64),
                rgba=False,
            ),
            MediaVerificationReason.VIDEO_FPS_MISMATCH,
        ),
        (
            _manifest(
                width=16,
                height=12,
                fps=3,
                hashes=("a" * 64, "b" * 64),
                rgba=False,
            ),
            MediaVerificationReason.VIDEO_FRAME_COUNT_MISMATCH,
        ),
        (
            _manifest(
                width=16,
                height=12,
                fps=3,
                hashes=("a" * 64, "a" * 64, "a" * 64),
                rgba=False,
            ),
            MediaVerificationReason.VIDEO_FRAMES_NOT_UNIQUE,
        ),
        (
            _manifest(
                width=16,
                height=12,
                fps=3,
                hashes=("a" * 64, "b" * 64, "c" * 64),
                rgba=False,
                sar="2/1",
            ),
            MediaVerificationReason.VIDEO_ASPECT_RATIO_INVALID,
        ),
        (
            _manifest(
                width=15,
                height=12,
                fps=3,
                hashes=("a" * 64, "b" * 64, "c" * 64),
                rgba=False,
            ),
            MediaVerificationReason.VIDEO_PIXEL_FORMAT_UNSUPPORTED,
        ),
        (
            _manifest(
                width=16,
                height=12,
                fps=3,
                hashes=("a" * 64, "b" * 64, "c" * 64),
                rgba=False,
            ).replace(b", 288,", b", 576,"),
            MediaVerificationReason.VIDEO_PIXEL_FORMAT_UNSUPPORTED,
        ),
        (
            _manifest(
                width=16,
                height=12,
                fps=3,
                hashes=("a" * 64, "b" * 64, "c" * 64),
                rgba=False,
            ).replace(b"0, 1, 1, 1,", b"0, 7, 1, 1,"),
            MediaVerificationReason.DECODE_INVALID,
        ),
    ),
)
def test_video_manifest_attacks_have_stable_reasons(
    manifest: bytes,
    reason: MediaVerificationReason,
    tmp_path: Path,
) -> None:
    assert (
        _verify_failure(_video_verifier(manifest), _video_media(tmp_path), frames=3, fps=3)
        is reason
    )


@pytest.mark.parametrize("exit_code,stderr", ((1, b""), (0, b"host/path leaked")))
def test_decoder_nonzero_or_any_stderr_is_rejected_without_leakage(
    exit_code: int,
    stderr: bytes,
    tmp_path: Path,
) -> None:
    sandbox = FakeSandbox(
        video_manifest=_manifest(
            width=16,
            height=12,
            fps=3,
            hashes=("a" * 64, "b" * 64, "c" * 64),
            rgba=False,
        ),
        exit_code=exit_code,
        stderr=stderr,
    )
    verifier = SubprocessReplayMediaVerifier(sandbox=sandbox)
    with pytest.raises(MediaVerificationError) as caught:
        verifier.verify(_preview_media(tmp_path), expected_video_frames=0, expected_fps=12)

    assert caught.value.reason is MediaVerificationReason.PNG_DECODE_FAILED
    assert "host/path" not in str(caught.value)


def test_observer_png_dimensions_must_match_video(tmp_path: Path) -> None:
    media = _video_media(tmp_path)
    _write_png_header(media["observer_mid.png"], width=18, height=12)
    verifier = _video_verifier(
        _manifest(
            width=16,
            height=12,
            fps=3,
            hashes=("a" * 64, "b" * 64, "c" * 64),
            rgba=False,
        )
    )

    assert (
        _verify_failure(verifier, media, frames=3, fps=3)
        is MediaVerificationReason.OBSERVER_DIMENSIONS_MISMATCH
    )


@pytest.mark.parametrize(
    ("sandbox_reason", "media_reason"),
    (
        (SandboxReason.CGROUP_UNAVAILABLE, MediaVerificationReason.SANDBOX_UNAVAILABLE),
        (SandboxReason.TIMEOUT, MediaVerificationReason.DECODE_TIMEOUT),
        (SandboxReason.OUTPUT_LIMIT, MediaVerificationReason.DECODE_OUTPUT_LIMIT),
        (SandboxReason.RESOURCE_MEMORY, MediaVerificationReason.DECODE_MEMORY_LIMIT),
        (SandboxReason.RESOURCE_PIDS, MediaVerificationReason.DECODE_PROCESS_LIMIT),
        (SandboxReason.RESOURCE_CPU, MediaVerificationReason.DECODE_CPU_LIMIT),
        (SandboxReason.SECCOMP_VIOLATION, MediaVerificationReason.SANDBOX_VIOLATION),
        (SandboxReason.TOOL_DRIFT, MediaVerificationReason.DEPENDENCY_DRIFT),
        (SandboxReason.MEDIA_DRIFT, MediaVerificationReason.MEDIA_CHANGED),
        (SandboxReason.LANDLOCK_SETUP_FAILED, MediaVerificationReason.SANDBOX_FAILED),
    ),
)
def test_sandbox_failures_map_to_stable_media_reasons(
    sandbox_reason: SandboxReason,
    media_reason: MediaVerificationReason,
    tmp_path: Path,
) -> None:
    verifier = SubprocessReplayMediaVerifier(
        sandbox=FakeSandbox(failure=SandboxError(sandbox_reason))
    )
    assert _verify_failure(verifier, _preview_media(tmp_path), frames=0, fps=12) is media_reason


def test_sandbox_resource_metrics_survive_failure_mapping(tmp_path: Path) -> None:
    metrics = _metrics()
    verifier = SubprocessReplayMediaVerifier(
        sandbox=FakeSandbox(failure=SandboxError(SandboxReason.RESOURCE_MEMORY, metrics=metrics))
    )
    with pytest.raises(MediaVerificationError) as caught:
        verifier.verify(_preview_media(tmp_path), expected_video_frames=0, expected_fps=12)
    assert caught.value.reason is MediaVerificationReason.DECODE_MEMORY_LIMIT
    assert caught.value.sandbox_metrics == metrics


def test_media_mutation_during_decode_is_detected(tmp_path: Path) -> None:
    media = _preview_media(tmp_path)
    sandbox = FakeSandbox(mutate_path=media["preview_head.png"])

    assert (
        _verify_failure(
            SubprocessReplayMediaVerifier(sandbox=sandbox),
            media,
            frames=0,
            fps=12,
        )
        is MediaVerificationReason.MEDIA_CHANGED
    )


def test_total_media_size_limit_is_enforced_before_decode(tmp_path: Path) -> None:
    media = _preview_media(tmp_path)
    sandbox = FakeSandbox()
    verifier = SubprocessReplayMediaVerifier(sandbox=sandbox, max_media_bytes=100)

    assert (
        _verify_failure(verifier, media, frames=0, fps=12)
        is MediaVerificationReason.MEDIA_TOO_LARGE
    )
    assert sandbox.calls == []


def test_identity_digest_changes_for_sandbox_and_each_verifier_policy() -> None:
    base_sandbox = FakeSandbox()
    base = SubprocessReplayMediaVerifier(sandbox=base_sandbox).identity.sha256
    changed_sandbox = dataclasses.replace(base_sandbox.identity, ffmpeg_sha256="f" * 64)
    variants = {
        SubprocessReplayMediaVerifier(
            sandbox=FakeSandbox(identity=changed_sandbox)
        ).identity.sha256,
        SubprocessReplayMediaVerifier(sandbox=FakeSandbox(), max_media_bytes=1024).identity.sha256,
        SubprocessReplayMediaVerifier(sandbox=FakeSandbox(), max_png_pixels=1024).identity.sha256,
        SubprocessReplayMediaVerifier(sandbox=FakeSandbox(), max_video_pixels=1024).identity.sha256,
        SubprocessReplayMediaVerifier(sandbox=FakeSandbox(), max_alloc_bytes=1024).identity.sha256,
        SubprocessReplayMediaVerifier(sandbox=FakeSandbox(), probesize_bytes=1024).identity.sha256,
        SubprocessReplayMediaVerifier(
            sandbox=FakeSandbox(), analyzeduration_microseconds=1024
        ).identity.sha256,
    }

    assert len(variants) == 7
    assert base not in variants


def test_real_static_decoder_accepts_historical_120_frame_replay_in_delegated_scope(
    production_launcher: Path,
) -> None:
    delegated = os.environ.get("MEDIA_SANDBOX_DELEGATED_ROOT")
    if not delegated or not _STATIC_FFMPEG.is_file():
        pytest.skip("requires delegated cgroup topology and pinned static ffmpeg")
    media = {
        relative: _HISTORICAL_REPLAY / relative
        for relative in (*_PREVIEWS, *_OBSERVERS, "observer_runtime.mp4")
    }
    verifier = SubprocessReplayMediaVerifier(
        sandbox=NativeCgroupSandbox(
            launcher=production_launcher,
            launcher_source=_LAUNCHER_SOURCE,
            ffmpeg=_STATIC_FFMPEG,
            delegated_cgroup_root=Path(delegated),
        )
    )

    result = verifier.verify(media, expected_video_frames=120, expected_fps=12)

    assert result.video is not None
    assert result.video.frame_count == 120
    assert result.video.unique_frame_count == 114
    assert (result.video.width, result.video.height) == (320, 240)
    assert result.video.sample_aspect_ratio == "0/1"
    assert result.video.square_sample_aspect_ratio_defaulted is True
    assert all(item.sandbox_metrics.memory_peak_bytes > 0 for item in result.pngs)
    assert result.video.sandbox_metrics.memory_peak_bytes > 0


def test_qualified_ffmpeg_has_only_the_audited_media_capabilities() -> None:
    if not _STATIC_FFMPEG.is_file():
        pytest.skip("qualified static ffmpeg is not installed")
    build = subprocess.run(
        [_STATIC_FFMPEG, "-hide_banner", "-buildconf"],
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8")
    protocols = subprocess.run(
        [_STATIC_FFMPEG, "-hide_banner", "-protocols"],
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8")
    formats = subprocess.run(
        [_STATIC_FFMPEG, "-hide_banner", "-formats"],
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8")
    codecs = subprocess.run(
        [_STATIC_FFMPEG, "-hide_banner", "-codecs"],
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8")

    assert hashlib.sha256(_STATIC_FFMPEG.read_bytes()).hexdigest() == _QUALIFIED_FFMPEG_SHA256
    for flag in (
        "--disable-everything",
        "--disable-autodetect",
        "--disable-network",
        "--disable-doc",
        "--disable-debug",
        "--disable-x86asm",
        "--disable-ffprobe",
        "--disable-avdevice",
        "--disable-swresample",
        "--enable-ffmpeg",
        "--enable-protocol=fd",
        "--enable-protocol=pipe",
        "--enable-demuxer=mov",
        "--enable-demuxer=image_png_pipe",
        "--enable-decoder=h264",
        "--enable-decoder=png",
        "--enable-encoder=rawvideo",
        "--enable-muxer=framemd5",
        "--enable-parser=h264",
        "--enable-filter=format",
        "--enable-filter=scale",
        "--enable-zlib",
        "--pkg-config-flags=--static",
        "--extra-ldflags=-static",
    ):
        assert flag in build
    assert {line.strip() for line in protocols.splitlines() if line.startswith("  ")} == {
        "fd",
        "pipe",
    }
    assert "framemd5" in formats and "png_pipe" in formats and "mov,mp4" in formats
    assert " h264 " in codecs and " png " in codecs and " rawvideo " in codecs


def _historical_media_copy(tmp_path: Path) -> dict[str, Path]:
    media: dict[str, Path] = {}
    for relative in (*_PREVIEWS, *_OBSERVERS):
        target = tmp_path / relative
        shutil.copyfile(_HISTORICAL_REPLAY / relative, target)
        media[relative] = target
    media["observer_runtime.mp4"] = tmp_path / "observer_runtime.mp4"
    return media


def _generate_attack_video(path: Path, case: str) -> None:
    command = [
        "/usr/bin/ffmpeg",
        "-nostdin",
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=320x240:rate=12",
    ]
    if case == "extra_stream":
        command.extend(("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=8000"))
    if case == "sar":
        command.extend(("-vf", "setsar=2/1"))
    elif case == "full_range":
        command.extend(("-vf", "scale=out_range=full"))
    command.extend(("-frames:v", "3"))
    if case == "mpeg4":
        command.extend(("-c:v", "mpeg4", "-pix_fmt", "yuv420p"))
    else:
        command.extend(
            (
                "-c:v",
                "libx264",
                "-pix_fmt",
                (
                    "yuv444p"
                    if case == "yuv444"
                    else "yuvj420p"
                    if case == "full_range"
                    else "yuv420p"
                ),
            )
        )
        if case == "full_range":
            command.extend(("-color_range", "pc"))
    if case == "extra_stream":
        command.extend(("-c:a", "aac", "-shortest"))
    if case == "avi":
        command.extend(("-f", "avi", str(path)))
    else:
        command.extend(("-movflags", "+faststart", str(path)))
    subprocess.run(command, check=True, capture_output=True)
    if case == "bitflip":
        payload = bytearray(path.read_bytes())
        offset = 0
        while offset < len(payload):
            box_size = int.from_bytes(payload[offset : offset + 4], "big")
            if payload[offset + 4 : offset + 8] == b"mdat":
                payload[offset + 8] ^= 0x80
                path.write_bytes(payload)
                break
            offset += box_size
        else:
            raise AssertionError("generated MP4 omitted mdat")


@pytest.mark.parametrize(
    ("case", "reason"),
    (
        ("mpeg4", MediaVerificationReason.DECODE_FAILED),
        ("yuv444", MediaVerificationReason.VIDEO_PIXEL_FORMAT_UNSUPPORTED),
        ("sar", MediaVerificationReason.VIDEO_ASPECT_RATIO_INVALID),
        ("extra_stream", MediaVerificationReason.DECODE_FAILED),
        ("bitflip", MediaVerificationReason.DECODE_FAILED),
        ("avi", MediaVerificationReason.VIDEO_CONTAINER_INVALID),
    ),
)
def test_real_decoder_rejects_codec_pixel_sar_and_extra_stream_attacks(
    case: str,
    reason: MediaVerificationReason,
    tmp_path: Path,
    production_launcher: Path,
) -> None:
    delegated = os.environ.get("MEDIA_SANDBOX_DELEGATED_ROOT")
    if not delegated or not _STATIC_FFMPEG.is_file():
        pytest.skip("requires delegated cgroup topology and qualified static ffmpeg")
    media = _historical_media_copy(tmp_path)
    _generate_attack_video(media["observer_runtime.mp4"], case)
    verifier = SubprocessReplayMediaVerifier(
        sandbox=NativeCgroupSandbox(
            launcher=production_launcher,
            launcher_source=_LAUNCHER_SOURCE,
            ffmpeg=_STATIC_FFMPEG,
            delegated_cgroup_root=Path(delegated),
        )
    )

    assert _verify_failure(verifier, media, frames=3, fps=12) is reason


def test_full_range_h264_is_reported_only_as_proven_8bit_420(
    tmp_path: Path,
    production_launcher: Path,
) -> None:
    delegated = os.environ.get("MEDIA_SANDBOX_DELEGATED_ROOT")
    if not delegated or not _STATIC_FFMPEG.is_file():
        pytest.skip("requires delegated cgroup topology and qualified static ffmpeg")
    media = _historical_media_copy(tmp_path)
    _generate_attack_video(media["observer_runtime.mp4"], "full_range")
    verifier = SubprocessReplayMediaVerifier(
        sandbox=NativeCgroupSandbox(
            launcher=production_launcher,
            launcher_source=_LAUNCHER_SOURCE,
            ffmpeg=_STATIC_FFMPEG,
            delegated_cgroup_root=Path(delegated),
        )
    )

    result = verifier.verify(media, expected_video_frames=3, expected_fps=12)

    assert result.video is not None
    assert result.video.pixel_format == "8bit-420"
    assert result.video.frame_count == 3


@pytest.mark.parametrize(
    "value",
    (0, -1, True, 1.5, "1"),
)
def test_each_verifier_resource_limit_is_a_positive_integer(value: object) -> None:
    arguments = {
        "max_media_bytes": 1,
        "max_png_pixels": 1,
        "max_video_pixels": 1,
        "max_alloc_bytes": 1,
        "probesize_bytes": 1,
        "analyzeduration_microseconds": 1,
    }
    for name in tuple(arguments):
        invalid = dict(arguments)
        invalid[name] = value  # type: ignore[assignment]
        with pytest.raises(MediaVerificationError) as caught:
            SubprocessReplayMediaVerifier(sandbox=FakeSandbox(), **invalid)
        assert caught.value.reason is MediaVerificationReason.CONFIGURATION_INVALID


class _ExplodingMapping(Mapping[str, Path]):
    def __getitem__(self, key: str) -> Path:
        raise AssertionError(key)

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(_PREVIEWS)

    def __len__(self) -> int:
        return len(_PREVIEWS)

    def items(self):  # type: ignore[no-untyped-def]
        raise RuntimeError("untrusted mapping")


@pytest.mark.parametrize("case", ("not_mapping", "exploding", "not_path", "relative"))
def test_malformed_media_mappings_have_stable_reasons(case: str, tmp_path: Path) -> None:
    media: object
    if case == "not_mapping":
        media = []
        expected = MediaVerificationReason.MEDIA_SET_INVALID
    elif case == "exploding":
        media = _ExplodingMapping()
        expected = MediaVerificationReason.MEDIA_SET_INVALID
    elif case == "not_path":
        media = {relative: str(tmp_path / relative) for relative in _PREVIEWS}
        expected = MediaVerificationReason.MEDIA_UNSAFE
    else:
        media = {relative: Path(relative) for relative in _PREVIEWS}
        expected = MediaVerificationReason.MEDIA_UNSAFE

    assert (
        _verify_failure(
            SubprocessReplayMediaVerifier(sandbox=FakeSandbox()),
            media,  # type: ignore[arg-type]
            frames=0,
            fps=12,
        )
        is expected
    )


@pytest.mark.parametrize("case", ("same_parent_symlink", "missing", "directory", "single_large"))
def test_open_media_attacks_fail_before_decode(case: str, tmp_path: Path) -> None:
    media = _preview_media(tmp_path)
    target = media["preview_head.png"]
    if case == "same_parent_symlink":
        backing = tmp_path / "backing.png"
        backing.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(backing)
        expected = MediaVerificationReason.MEDIA_UNSAFE
        limit = 512 * 1024 * 1024
    elif case == "missing":
        target.unlink()
        expected = MediaVerificationReason.MEDIA_UNSAFE
        limit = 512 * 1024 * 1024
    elif case == "directory":
        target.unlink()
        target.mkdir()
        expected = MediaVerificationReason.MEDIA_UNSAFE
        limit = 512 * 1024 * 1024
    else:
        expected = MediaVerificationReason.MEDIA_TOO_LARGE
        limit = target.stat().st_size - 1
    sandbox = FakeSandbox()

    assert (
        _verify_failure(
            SubprocessReplayMediaVerifier(sandbox=sandbox, max_media_bytes=limit),
            media,
            frames=0,
            fps=12,
        )
        is expected
    )
    assert sandbox.calls == []


def test_media_stat_or_hash_change_while_opening_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = _preview_media(tmp_path)
    target = media["preview_head.png"]
    original_hash = media_verifier_module._hash_descriptor
    changed = False

    def changing_hash(descriptor: int, size: int) -> str:
        nonlocal changed
        digest = original_hash(descriptor, size)
        if not changed:
            changed = True
            os.utime(target, ns=(target.stat().st_atime_ns, target.stat().st_mtime_ns + 1))
        return digest

    monkeypatch.setattr(media_verifier_module, "_hash_descriptor", changing_hash)

    assert (
        _verify_failure(
            SubprocessReplayMediaVerifier(sandbox=FakeSandbox()),
            media,
            frames=0,
            fps=12,
        )
        is MediaVerificationReason.MEDIA_UNSAFE
    )


def test_short_read_while_hashing_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = _preview_media(tmp_path)
    original_pread = media_verifier_module.os.pread
    first = True

    def short_read(descriptor: int, amount: int, offset: int) -> bytes:
        nonlocal first
        if first:
            first = False
            return b""
        return original_pread(descriptor, amount, offset)

    monkeypatch.setattr(media_verifier_module.os, "pread", short_read)

    assert (
        _verify_failure(
            SubprocessReplayMediaVerifier(sandbox=FakeSandbox()),
            media,
            frames=0,
            fps=12,
        )
        is MediaVerificationReason.MEDIA_UNSAFE
    )


def test_path_replacement_during_decode_is_media_changed(tmp_path: Path) -> None:
    media = _preview_media(tmp_path)
    attacked = media["preview_head.png"]
    backing = tmp_path / "replacement.png"
    backing.write_bytes(attacked.read_bytes())
    sandbox = FakeSandbox()
    original_run = sandbox.run
    replaced = False

    def replacing_run(media_fd: int, arguments: tuple[str, ...]) -> SandboxCapture:
        nonlocal replaced
        capture = original_run(media_fd, arguments)
        if not replaced:
            replaced = True
            attacked.unlink()
            attacked.symlink_to(backing)
        return capture

    sandbox.run = replacing_run  # type: ignore[method-assign]

    assert (
        _verify_failure(
            SubprocessReplayMediaVerifier(sandbox=sandbox),
            media,
            frames=0,
            fps=12,
        )
        is MediaVerificationReason.MEDIA_CHANGED
    )


def test_png_and_mp4_read_errors_are_stably_mapped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = _preview_media(tmp_path)
    for path in media.values():
        path.write_bytes(path.read_bytes() + b"x")
    original_pread = media_verifier_module.os.pread

    def reject_png_header(descriptor: int, amount: int, offset: int) -> bytes:
        if amount == 33 and offset == 0:
            raise OSError("read failed")
        return original_pread(descriptor, amount, offset)

    monkeypatch.setattr(media_verifier_module.os, "pread", reject_png_header)
    assert (
        _verify_failure(
            SubprocessReplayMediaVerifier(sandbox=FakeSandbox()),
            media,
            frames=0,
            fps=12,
        )
        is MediaVerificationReason.PNG_DECODE_FAILED
    )

    monkeypatch.setattr(media_verifier_module.os, "pread", original_pread)
    video_media = _video_media(tmp_path)

    def reject_box_header(descriptor: int, amount: int, offset: int) -> bytes:
        if amount == 16:
            raise OSError("read failed")
        return original_pread(descriptor, amount, offset)

    monkeypatch.setattr(media_verifier_module.os, "pread", reject_box_header)
    verifier = _video_verifier(
        _manifest(
            width=16,
            height=12,
            fps=3,
            hashes=("a" * 64, "b" * 64, "c" * 64),
            rgba=False,
        )
    )
    assert (
        _verify_failure(verifier, video_media, frames=3, fps=3)
        is MediaVerificationReason.VIDEO_CONTAINER_INVALID
    )


class _InvalidDigestIdentity(NativeSandboxIdentity):
    @property
    def sha256(self) -> str:
        return "0" * 64


class _NondocumentIdentity(NativeSandboxIdentity):
    @property
    def canonical_bytes(self) -> bytes:
        return b"[]\n"


def _identity_variant(kind: type[NativeSandboxIdentity]) -> NativeSandboxIdentity:
    base = _sandbox_identity()
    return kind(
        launcher_sha256=base.launcher_sha256,
        launcher_bytes=base.launcher_bytes,
        launcher_source_sha256=base.launcher_source_sha256,
        launcher_source_bytes=base.launcher_source_bytes,
        ffmpeg_sha256=base.ffmpeg_sha256,
        ffmpeg_bytes=base.ffmpeg_bytes,
        implementation_sha256=base.implementation_sha256,
        implementation_bytes=base.implementation_bytes,
        landlock_abi=base.landlock_abi,
        kernel_architecture=base.kernel_architecture,
        kernel_release=base.kernel_release,
        policy=base.policy,
    )


@pytest.mark.parametrize(
    "identity",
    (object(), _identity_variant(_InvalidDigestIdentity), _identity_variant(_NondocumentIdentity)),
)
def test_invalid_sandbox_identities_are_rejected(identity: object) -> None:
    with pytest.raises(MediaVerificationError) as caught:
        SubprocessReplayMediaVerifier(sandbox=FakeSandbox(identity=identity))  # type: ignore[arg-type]
    assert caught.value.reason is MediaVerificationReason.DEPENDENCY_INVALID


def test_unreadable_verifier_implementation_is_dependency_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unreadable(path: Path) -> bytes:
        del path
        raise OSError("unreadable")

    monkeypatch.setattr(media_verifier_module.Path, "read_bytes", unreadable)
    with pytest.raises(MediaVerificationError) as caught:
        SubprocessReplayMediaVerifier(sandbox=FakeSandbox())
    assert caught.value.reason is MediaVerificationReason.DEPENDENCY_INVALID


def test_dependency_identity_and_implementation_drift_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = FakeSandbox()
    verifier = SubprocessReplayMediaVerifier(sandbox=sandbox)
    sandbox.identity = dataclasses.replace(sandbox.identity, ffmpeg_sha256="f" * 64)
    assert (
        _verify_failure(verifier, _preview_media(tmp_path), frames=0, fps=12)
        is MediaVerificationReason.DEPENDENCY_DRIFT
    )

    sandbox = FakeSandbox()
    verifier = SubprocessReplayMediaVerifier(sandbox=sandbox)

    def unreadable() -> object:
        raise MediaVerificationError(MediaVerificationReason.DEPENDENCY_INVALID)

    monkeypatch.setattr(media_verifier_module, "_measure_implementation", unreadable)
    assert (
        _verify_failure(verifier, _preview_media(tmp_path), frames=0, fps=12)
        is MediaVerificationReason.DEPENDENCY_DRIFT
    )


def test_sandbox_must_return_a_typed_capture(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    sandbox.run = lambda media_fd, arguments: object()  # type: ignore[method-assign,return-value]
    assert (
        _verify_failure(
            SubprocessReplayMediaVerifier(sandbox=sandbox),
            _preview_media(tmp_path),
            frames=0,
            fps=12,
        )
        is MediaVerificationReason.SANDBOX_FAILED
    )


def _replace_video_payload(media: dict[str, Path], payload: bytes) -> None:
    media["observer_runtime.mp4"].write_bytes(payload)


@pytest.mark.parametrize(
    "payload",
    (
        (24).to_bytes(4, "big") + b"ftyp" + b"isom" + b"\0\0\0\0" + b"isomavc1" + b"\0\0\0\1moov",
        (18).to_bytes(4, "big")
        + b"ftyp"
        + b"isom"
        + b"\0\0\0\0xx"
        + (8).to_bytes(4, "big")
        + b"moov"
        + (8).to_bytes(4, "big")
        + b"mdat",
        (1).to_bytes(4, "big") + b"ftyp" + (24).to_bytes(8, "big") + b"isom" + b"\0\0\0\0",
    ),
)
def test_truncated_or_noncanonical_mp4_headers_are_rejected(
    payload: bytes,
    tmp_path: Path,
) -> None:
    media = _video_media(tmp_path)
    _replace_video_payload(media, payload)
    assert (
        _verify_failure(
            _video_verifier(
                _manifest(
                    width=16,
                    height=12,
                    fps=3,
                    hashes=("a" * 64, "b" * 64, "c" * 64),
                    rgba=False,
                )
            ),
            media,
            frames=3,
            fps=3,
        )
        is MediaVerificationReason.VIDEO_CONTAINER_INVALID
    )


def test_mp4_box_count_is_bounded_before_decode(tmp_path: Path) -> None:
    media = _video_media(tmp_path)
    ftyp = media["observer_runtime.mp4"].read_bytes()[:24]
    free = (8).to_bytes(4, "big") + b"free"
    _replace_video_payload(media, ftyp + free * 1024 + (8).to_bytes(4, "big") + b"moov")
    assert (
        _verify_failure(
            _video_verifier(b"unused"),
            media,
            frames=3,
            fps=3,
        )
        is MediaVerificationReason.VIDEO_CONTAINER_INVALID
    )


def test_extended_non_ftyp_boxes_are_accepted(tmp_path: Path) -> None:
    media = _video_media(tmp_path)
    ftyp = media["observer_runtime.mp4"].read_bytes()[:24]
    extended_moov = (1).to_bytes(4, "big") + b"moov" + (16).to_bytes(8, "big")
    _replace_video_payload(media, ftyp + extended_moov + (8).to_bytes(4, "big") + b"mdat")
    result = _video_verifier(
        _manifest(
            width=16,
            height=12,
            fps=1,
            hashes=("a" * 64,),
            rgba=False,
            sar="1/1",
        )
    ).verify(media, expected_video_frames=1, expected_fps=1)
    assert result.video is not None
    assert result.video.unique_frame_count == 1
    assert result.video.sample_aspect_ratio == "1/1"
    assert result.video.square_sample_aspect_ratio_defaulted is False


@pytest.mark.parametrize(
    ("manifest", "reason"),
    (
        (b"x\n", MediaVerificationReason.DECODE_INVALID),
        (
            _manifest(width=16, height=12, fps=3, hashes=("a" * 64,), rgba=False).replace(
                b"#version: 2", b"#version: 3"
            ),
            MediaVerificationReason.DECODE_INVALID,
        ),
        (
            _manifest(width=16, height=12, fps=3, hashes=("a" * 64,), rgba=False).replace(
                b"#tb 0: 1/3", b"#tb 0: 1/0"
            ),
            MediaVerificationReason.DECODE_INVALID,
        ),
        (
            _manifest(width=16, height=12, fps=3, hashes=("a" * 64,), rgba=False).replace(
                b"a" * 64, b"A" * 64
            ),
            MediaVerificationReason.DECODE_INVALID,
        ),
        (
            _manifest(width=0, height=12, fps=3, hashes=("a" * 64,), rgba=False),
            MediaVerificationReason.VIDEO_PIXEL_FORMAT_UNSUPPORTED,
        ),
        (
            _manifest(width=22, height=20, fps=3, hashes=("a" * 64,), rgba=False),
            MediaVerificationReason.VIDEO_PIXELS_EXCEEDED,
        ),
    ),
)
def test_manifest_header_attacks_are_strictly_rejected(
    manifest: bytes,
    reason: MediaVerificationReason,
    tmp_path: Path,
) -> None:
    verifier = SubprocessReplayMediaVerifier(
        sandbox=FakeSandbox(video_manifest=manifest),
        max_video_pixels=400,
    )
    assert _verify_failure(verifier, _video_media(tmp_path), frames=1, fps=3) is reason


@pytest.mark.parametrize("mutation", ("blank", "whitespace", "missing_newline", "noncanonical_tb"))
def test_framemd5_text_envelope_is_canonical(mutation: str, tmp_path: Path) -> None:
    manifest = _manifest(width=16, height=12, fps=3, hashes=("a" * 64,), rgba=False)
    if mutation == "blank":
        manifest = manifest.replace(b"#version: 2\n", b"#version: 2\n\n")
    elif mutation == "whitespace":
        manifest = b" " + manifest
    elif mutation == "missing_newline":
        manifest = manifest.rstrip(b"\n")
    else:
        manifest = manifest.replace(b"#tb 0: 1/3", b"#tb 0: 2/6")
    assert (
        _verify_failure(_video_verifier(manifest), _video_media(tmp_path), frames=1, fps=3)
        is MediaVerificationReason.DECODE_INVALID
    )


def test_png_rejects_zero_or_multiple_decoded_frames(tmp_path: Path) -> None:
    for hashes in ((), ("a" * 64, "b" * 64)):
        malformed = _manifest(width=8, height=6, fps=25, hashes=hashes, rgba=True)
        sandbox = FakeSandbox()
        sandbox.run = lambda media_fd, arguments, value=malformed: SandboxCapture(  # type: ignore[method-assign]
            0, value, b"", _metrics()
        )
        assert (
            _verify_failure(
                SubprocessReplayMediaVerifier(sandbox=sandbox),
                _preview_media(tmp_path),
                frames=0,
                fps=12,
            )
            is MediaVerificationReason.PNG_DECODE_FAILED
        )


def test_png_manifest_dimensions_must_match_ihdr(tmp_path: Path) -> None:
    malformed = _manifest(width=10, height=6, fps=25, hashes=("a" * 64,), rgba=True)
    sandbox = FakeSandbox()
    sandbox.run = lambda media_fd, arguments: SandboxCapture(  # type: ignore[method-assign]
        0, malformed, b"", _metrics()
    )
    assert (
        _verify_failure(
            SubprocessReplayMediaVerifier(sandbox=sandbox),
            _preview_media(tmp_path),
            frames=0,
            fps=12,
        )
        is MediaVerificationReason.PNG_DECODE_FAILED
    )


@pytest.mark.parametrize("exit_code,stderr", ((1, b""), (0, b"decoder warning")))
def test_video_decoder_nonzero_or_stderr_is_rejected(
    exit_code: int,
    stderr: bytes,
    tmp_path: Path,
) -> None:
    sandbox = FakeSandbox(
        video_manifest=_manifest(
            width=16,
            height=12,
            fps=3,
            hashes=("a" * 64, "b" * 64, "c" * 64),
            rgba=False,
        )
    )
    original_run = sandbox.run

    def video_failure(media_fd: int, arguments: tuple[str, ...]) -> SandboxCapture:
        capture = original_run(media_fd, arguments)
        if os.pread(media_fd, 8, 4) == b"ftypisom":
            return dataclasses.replace(capture, exit_code=exit_code, stderr=stderr)
        return capture

    sandbox.run = video_failure  # type: ignore[method-assign]
    assert (
        _verify_failure(
            SubprocessReplayMediaVerifier(sandbox=sandbox),
            _video_media(tmp_path),
            frames=3,
            fps=3,
        )
        is MediaVerificationReason.DECODE_FAILED
    )
