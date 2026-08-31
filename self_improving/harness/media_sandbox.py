"""Fail-closed native process sandbox for untrusted replay-media decoding.

Production use requires a preconfigured delegated cgroup-v2 root with sibling
``supervisor`` and ``jobs`` children.  The caller must already run below the
``supervisor`` leaf; this module never asks systemd for delegation and never
falls back to an unsandboxed decoder.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import secrets
import select
import selectors
import signal
import stat
import struct
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol


class SandboxReason(str, Enum):
    """Stable, path-free sandbox failure reasons."""

    CONFIGURATION_INVALID = "configuration_invalid"
    TOOL_INVALID = "tool_invalid"
    TOOL_NOT_STATIC = "tool_not_static"
    TOOL_DRIFT = "tool_drift"
    MEDIA_DRIFT = "media_drift"
    CGROUP_UNAVAILABLE = "cgroup_unavailable"
    CGROUP_SETUP_FAILED = "cgroup_setup_failed"
    CGROUP_MIGRATION_FAILED = "cgroup_migration_failed"
    LAUNCHER_START_FAILED = "launcher_start_failed"
    SANDBOX_UNAVAILABLE = "sandbox_unavailable"
    LANDLOCK_SETUP_FAILED = "landlock_setup_failed"
    SECCOMP_SETUP_FAILED = "seccomp_setup_failed"
    TIMEOUT = "timeout"
    OUTPUT_LIMIT = "output_limit"
    RESOURCE_MEMORY = "resource_memory"
    RESOURCE_PIDS = "resource_pids"
    RESOURCE_CPU = "resource_cpu"
    SECCOMP_VIOLATION = "seccomp_violation"
    CLEANUP_FAILED = "cleanup_failed"


_MESSAGES = {reason: reason.value.replace("_", " ") for reason in SandboxReason}
_MESSAGES[SandboxReason.CGROUP_UNAVAILABLE] = (
    "preconfigured delegated cgroup supervisor/jobs topology is unavailable"
)
_MIN_LANDLOCK_ABI = 6


class SandboxError(RuntimeError):
    """A failure that exposes neither host paths nor decoder output."""

    def __init__(
        self,
        reason: SandboxReason,
        *,
        metrics: SandboxMetrics | None = None,
    ) -> None:
        self.reason = reason
        self.metrics = metrics
        super().__init__(_MESSAGES[reason])


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    """Every process and kernel resource limit enforced by the adapter."""

    timeout_seconds: float = 30.0
    max_output_bytes: int = 16 * 1024 * 1024
    address_space_bytes: int = 1024 * 1024 * 1024
    cpu_seconds: int = 30
    open_files: int = 64
    # RLIMIT_NPROC is per real UID, not per sandbox.  Keep it above the host
    # user's normal process population; the actual per-run bound is pids.max.
    processes: int = 4096
    file_bytes: int = 16 * 1024 * 1024
    core_bytes: int = 0
    memory_bytes: int = 1024 * 1024 * 1024
    swap_bytes: int = 0
    pids: int = 32
    cpu_quota_us: int = 100_000
    cpu_period_us: int = 100_000
    memory_oom_group: bool = True
    max_depth: int = 0
    max_descendants: int = 0
    schema_version: str = "harness.media_sandbox_policy.v1"

    def __post_init__(self) -> None:
        positive_integers = (
            self.max_output_bytes,
            self.address_space_bytes,
            self.cpu_seconds,
            self.open_files,
            self.processes,
            self.file_bytes,
            self.memory_bytes,
            self.pids,
            self.cpu_quota_us,
            self.cpu_period_us,
        )
        if (
            type(self.timeout_seconds) not in {int, float}
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
            or any(type(value) is not int or value <= 0 for value in positive_integers)
            or type(self.core_bytes) is not int
            or self.core_bytes != 0
            or type(self.swap_bytes) is not int
            or self.swap_bytes != 0
            or self.open_files < 16
            or self.memory_oom_group is not True
            or type(self.max_depth) is not int
            or self.max_depth != 0
            or type(self.max_descendants) is not int
            or self.max_descendants != 0
        ):
            raise SandboxError(SandboxReason.CONFIGURATION_INVALID)

    @property
    def canonical_bytes(self) -> bytes:
        document = {
            "schema_version": self.schema_version,
            "process": {
                "timeout_seconds": float(self.timeout_seconds),
                "max_output_bytes": self.max_output_bytes,
            },
            "rlimit": {
                "address_space_bytes": self.address_space_bytes,
                "cpu_seconds": self.cpu_seconds,
                "open_files": self.open_files,
                "processes": self.processes,
                "file_bytes": self.file_bytes,
                "core_bytes": self.core_bytes,
            },
            "cgroup": {
                "memory_bytes": self.memory_bytes,
                "swap_bytes": self.swap_bytes,
                "pids": self.pids,
                "cpu_quota_us": self.cpu_quota_us,
                "cpu_period_us": self.cpu_period_us,
                "memory_oom_group": self.memory_oom_group,
                "max_depth": self.max_depth,
                "max_descendants": self.max_descendants,
            },
        }
        return (
            json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        ).encode("ascii")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()


@dataclass(frozen=True, slots=True)
class _BoundFile:
    path: Path
    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int
    changed_ns: int
    sha256: str


@dataclass(frozen=True, slots=True)
class NativeSandboxIdentity:
    """Path-free identity of tools, policy, implementation, and host ABI."""

    launcher_sha256: str
    launcher_bytes: int
    launcher_source_sha256: str
    launcher_source_bytes: int
    ffmpeg_sha256: str
    ffmpeg_bytes: int
    implementation_sha256: str
    implementation_bytes: int
    landlock_abi: int
    kernel_architecture: str
    kernel_release: str
    policy: SandboxPolicy
    schema_version: str = "harness.native_media_sandbox_identity.v3"

    def __post_init__(self) -> None:
        digests = (
            self.launcher_sha256,
            self.launcher_source_sha256,
            self.ffmpeg_sha256,
            self.implementation_sha256,
        )
        byte_counts = (
            self.launcher_bytes,
            self.launcher_source_bytes,
            self.ffmpeg_bytes,
            self.implementation_bytes,
        )
        if (
            any(
                not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in digests
            )
            or any(type(value) is not int or value <= 0 for value in byte_counts)
            or type(self.landlock_abi) is not int
            or self.landlock_abi < _MIN_LANDLOCK_ABI
            or not isinstance(self.kernel_architecture, str)
            or not self.kernel_architecture
            or not isinstance(self.kernel_release, str)
            or not self.kernel_release
            or not isinstance(self.policy, SandboxPolicy)
            or self.schema_version != "harness.native_media_sandbox_identity.v3"
        ):
            raise SandboxError(SandboxReason.CONFIGURATION_INVALID)

    @property
    def canonical_bytes(self) -> bytes:
        document = {
            "schema_version": self.schema_version,
            "launcher": {"sha256": self.launcher_sha256, "bytes": self.launcher_bytes},
            "launcher_source": {
                "sha256": self.launcher_source_sha256,
                "bytes": self.launcher_source_bytes,
            },
            "ffmpeg": {"sha256": self.ffmpeg_sha256, "bytes": self.ffmpeg_bytes},
            "implementation": {
                "sha256": self.implementation_sha256,
                "bytes": self.implementation_bytes,
            },
            "landlock_abi": self.landlock_abi,
            "kernel": {
                "architecture": self.kernel_architecture,
                "release": self.kernel_release,
            },
            "policy_sha256": self.policy.sha256,
            "policy": json.loads(self.policy.canonical_bytes),
        }
        return (
            json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        ).encode("ascii")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()


@dataclass(frozen=True, slots=True)
class SandboxMetrics:
    """Bounded cgroup-v2 resource facts captured after process exit."""

    memory_peak_bytes: int
    memory_events: tuple[tuple[str, int], ...]
    pids_peak: int
    pids_events: tuple[tuple[str, int], ...]
    cpu_stats: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class SandboxCapture:
    """Decoder exit and bounded output plus cgroup resource evidence."""

    exit_code: int
    stdout: bytes
    stderr: bytes
    metrics: SandboxMetrics


class MediaSandbox(Protocol):
    """Execution seam used by the replay-media verifier and its test adapter."""

    @property
    def identity(self) -> NativeSandboxIdentity: ...

    def run(self, media_fd: int, arguments: tuple[str, ...]) -> SandboxCapture: ...


class NativeCgroupSandbox:
    """Adapter that will run one held static decoder inside a delegated cgroup."""

    def __init__(
        self,
        *,
        launcher: Path,
        launcher_source: Path,
        ffmpeg: Path,
        delegated_cgroup_root: Path,
        policy: SandboxPolicy | None = None,
    ) -> None:
        self._policy = policy if policy is not None else SandboxPolicy()
        self._launcher = _bind_file(launcher, executable=True, require_static_elf=True)
        self._ffmpeg = _bind_file(ffmpeg, executable=True, require_static_elf=True)
        self._launcher_source = _bind_file(
            launcher_source,
            executable=False,
            require_static_elf=False,
        )
        self._delegated_cgroup_root = delegated_cgroup_root
        self._implementation = _bind_file(
            Path(__file__).resolve(),
            executable=False,
            require_static_elf=False,
        )
        landlock_abi = _probe_landlock_abi(self._launcher)
        if landlock_abi < _MIN_LANDLOCK_ABI:
            raise SandboxError(SandboxReason.SANDBOX_UNAVAILABLE)
        self._identity = NativeSandboxIdentity(
            launcher_sha256=self._launcher.sha256,
            launcher_bytes=self._launcher.size,
            launcher_source_sha256=self._launcher_source.sha256,
            launcher_source_bytes=self._launcher_source.size,
            ffmpeg_sha256=self._ffmpeg.sha256,
            ffmpeg_bytes=self._ffmpeg.size,
            implementation_sha256=self._implementation.sha256,
            implementation_bytes=self._implementation.size,
            landlock_abi=landlock_abi,
            kernel_architecture=platform.machine(),
            kernel_release=platform.release(),
            policy=self._policy,
        )

    @property
    def identity(self) -> NativeSandboxIdentity:
        """Return the immutable sandbox dependency identity."""

        return self._identity

    def run(self, media_fd: int, arguments: tuple[str, ...]) -> SandboxCapture:
        """Run one decoder invocation under all configured kernel limits."""

        if (
            type(media_fd) is not int
            or media_fd < 0
            or not isinstance(arguments, tuple)
            or any(not isinstance(argument, str) or "\x00" in argument for argument in arguments)
        ):
            raise SandboxError(SandboxReason.CONFIGURATION_INVALID)
        try:
            media_info = os.fstat(media_fd)
        except OSError:
            raise SandboxError(SandboxReason.CONFIGURATION_INVALID) from None
        if not stat.S_ISREG(media_info.st_mode):
            raise SandboxError(SandboxReason.CONFIGURATION_INVALID)
        _validate_delegated_topology(self._delegated_cgroup_root)
        job = _create_job(self._delegated_cgroup_root / "jobs", self._policy)
        process: subprocess.Popen[bytes] | None = None
        descriptors: set[int] = set()
        primary_failure: SandboxError | None = None
        capture: tuple[int, bytes, bytes] | None = None
        metrics: SandboxMetrics | None = None
        try:
            launcher_fd = _open_bound_file(self._launcher)
            descriptors.add(launcher_fd)
            ffmpeg_fd = _open_bound_file(self._ffmpeg)
            descriptors.add(ffmpeg_fd)
            invocation_media_fd = _reopen_media(media_fd, media_info)
            descriptors.add(invocation_media_fd)
            status_read, status_write = os.pipe2(os.O_CLOEXEC)
            gate_read, gate_write = os.pipe2(os.O_CLOEXEC)
            descriptors.update((status_read, status_write, gate_read, gate_write))
            command = (
                f"/proc/self/fd/{launcher_fd}",
                "--run",
                str(status_write),
                str(gate_read),
                str(ffmpeg_fd),
                str(invocation_media_fd),
                str(self._policy.address_space_bytes),
                str(self._policy.cpu_seconds),
                str(self._policy.open_files),
                str(self._policy.processes),
                str(self._policy.file_bytes),
                str(self._policy.core_bytes),
                "--",
                "media-decoder",
                *arguments,
            )
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=False,
                    start_new_session=True,
                    close_fds=True,
                    pass_fds=(
                        launcher_fd,
                        ffmpeg_fd,
                        invocation_media_fd,
                        status_write,
                        gate_read,
                    ),
                    env={"LANG": "C", "LC_ALL": "C", "TZ": "UTC"},
                )
            except OSError:
                raise SandboxError(SandboxReason.LAUNCHER_START_FAILED) from None
            _close_descriptor(status_write, descriptors)
            _close_descriptor(gate_read, descriptors)
            deadline = time.monotonic() + float(self._policy.timeout_seconds)
            _await_ready(process, status_read, deadline)
            _attach_process(job, process.pid)
            try:
                if os.write(gate_write, b"G") != 1:
                    raise OSError("short gate write")
            except OSError:
                raise SandboxError(SandboxReason.LAUNCHER_START_FAILED) from None
            _close_descriptor(gate_write, descriptors)
            capture = _capture_process(
                process,
                status_read=status_read,
                deadline=deadline,
                max_output_bytes=self._policy.max_output_bytes,
            )
            _close_descriptor(status_read, descriptors)
        except SandboxError as caught:
            primary_failure = caught
        except Exception:
            primary_failure = SandboxError(SandboxReason.LAUNCHER_START_FAILED)
        finally:
            if process is not None and (primary_failure is not None or process.poll() is None):
                _kill_job(job)
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass
            if process is not None:
                try:
                    process.wait(timeout=1.0)
                except (OSError, subprocess.TimeoutExpired):
                    primary_failure = SandboxError(SandboxReason.CLEANUP_FAILED)
            for descriptor in tuple(descriptors):
                _close_descriptor(descriptor, descriptors)
            try:
                _wait_unpopulated(job, timeout_seconds=1.0)
            except (OSError, ValueError, SandboxError):
                _kill_job(job)
                try:
                    _wait_unpopulated(job, timeout_seconds=1.0)
                except (OSError, ValueError, SandboxError):
                    pass
                primary_failure = SandboxError(SandboxReason.CLEANUP_FAILED)
            try:
                metrics = _read_metrics(job)
                job.rmdir()
            except (OSError, ValueError, SandboxError):
                primary_failure = SandboxError(SandboxReason.CLEANUP_FAILED)
        if primary_failure is not None:
            if metrics is not None:
                raise SandboxError(primary_failure.reason, metrics=metrics)
            raise primary_failure
        if capture is None or metrics is None:
            raise SandboxError(SandboxReason.CLEANUP_FAILED)
        exit_code, stdout, stderr = capture
        try:
            _raise_resource_failure(exit_code, metrics)
        except SandboxError as caught:
            raise SandboxError(caught.reason, metrics=metrics) from None
        _require_bound_path_unchanged(self._launcher)
        _require_bound_path_unchanged(self._ffmpeg)
        _require_bound_path_unchanged(self._launcher_source)
        _require_bound_path_unchanged(self._implementation)
        try:
            final_media_info = os.fstat(media_fd)
        except OSError:
            raise SandboxError(SandboxReason.MEDIA_DRIFT) from None
        if _stat_key(media_info) != _stat_key(final_media_info):
            raise SandboxError(SandboxReason.MEDIA_DRIFT)
        return SandboxCapture(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            metrics=metrics,
        )


def _create_job(jobs: Path, policy: SandboxPolicy) -> Path:
    job = jobs / f"media-{os.getpid()}-{secrets.token_hex(12)}"
    try:
        job.mkdir(mode=0o700)
        values = {
            "memory.max": str(policy.memory_bytes),
            "memory.swap.max": str(policy.swap_bytes),
            "memory.oom.group": "1" if policy.memory_oom_group else "0",
            "pids.max": str(policy.pids),
            "cpu.max": f"{policy.cpu_quota_us} {policy.cpu_period_us}",
            "cgroup.max.depth": str(policy.max_depth),
            "cgroup.max.descendants": str(policy.max_descendants),
        }
        for name, value in values.items():
            target = job / name
            target.write_text(value + "\n", encoding="ascii")
            if target.read_text(encoding="ascii").strip() != value:
                raise OSError("cgroup policy readback mismatch")
        return job
    except OSError:
        try:
            job.rmdir()
        except OSError:
            pass
        raise SandboxError(SandboxReason.CGROUP_SETUP_FAILED) from None


def _open_bound_file(expected: _BoundFile) -> int:
    try:
        descriptor = os.open(
            expected.path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        info = os.fstat(descriptor)
        actual = _identity_from_descriptor(expected.path, descriptor, info)
        if actual != expected:
            raise OSError("tool identity changed")
        return descriptor
    except (OSError, SandboxError):
        if "descriptor" in locals():
            os.close(descriptor)
        raise SandboxError(SandboxReason.TOOL_DRIFT) from None


def _reopen_media(master_fd: int, expected: os.stat_result) -> int:
    try:
        descriptor = os.open(f"/proc/self/fd/{master_fd}", os.O_RDONLY | os.O_CLOEXEC)
        actual = os.fstat(descriptor)
        if not stat.S_ISREG(actual.st_mode) or (
            actual.st_dev,
            actual.st_ino,
            actual.st_mode,
            actual.st_size,
        ) != (expected.st_dev, expected.st_ino, expected.st_mode, expected.st_size):
            raise OSError("media identity changed")
        if os.lseek(descriptor, 0, os.SEEK_SET) != 0:
            raise OSError("media descriptor did not rewind")
        return descriptor
    except OSError:
        if "descriptor" in locals():
            os.close(descriptor)
        raise SandboxError(SandboxReason.MEDIA_DRIFT) from None


def _await_ready(
    process: subprocess.Popen[bytes],
    status_read: int,
    deadline: float,
) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SandboxError(SandboxReason.TIMEOUT)
    try:
        ready, _, _ = select.select((status_read,), (), (), remaining)
    except (OSError, ValueError):
        raise SandboxError(SandboxReason.LAUNCHER_START_FAILED) from None
    if ready != [status_read] or os.read(status_read, 1) != b"R" or process.poll() is not None:
        raise SandboxError(SandboxReason.LAUNCHER_START_FAILED)


def _attach_process(job: Path, pid: int) -> None:
    try:
        (job / "cgroup.procs").write_text(f"{pid}\n", encoding="ascii")
        attached = {
            int(record) for record in (job / "cgroup.procs").read_text(encoding="ascii").split()
        }
        events = dict(_read_key_values(job / "cgroup.events"))
        if pid not in attached or events.get("populated") != 1:
            raise OSError("cgroup migration verification failed")
    except (OSError, ValueError):
        raise SandboxError(SandboxReason.CGROUP_MIGRATION_FAILED) from None


def _capture_process(
    process: subprocess.Popen[bytes],
    *,
    status_read: int,
    deadline: float,
    max_output_bytes: int,
) -> tuple[int, bytes, bytes]:
    if process.stdout is None or process.stderr is None:
        raise SandboxError(SandboxReason.LAUNCHER_START_FAILED)
    stdout = bytearray()
    stderr = bytearray()
    outputs = {
        process.stdout.fileno(): stdout,
        process.stderr.fileno(): stderr,
    }
    total = 0
    selector = selectors.DefaultSelector()
    try:
        for descriptor in (*outputs, status_read):
            selector.register(descriptor, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SandboxError(SandboxReason.TIMEOUT)
            for key, _ in selector.select(min(remaining, 0.05)):
                try:
                    chunk = os.read(key.fd, 65_536)
                except OSError:
                    raise SandboxError(SandboxReason.LAUNCHER_START_FAILED) from None
                if not chunk:
                    selector.unregister(key.fd)
                    continue
                if key.fd == status_read:
                    raise SandboxError(SandboxReason.LAUNCHER_START_FAILED)
                remaining_output = max_output_bytes - total
                outputs[key.fd].extend(chunk[: max(0, remaining_output)])
                total += len(chunk)
                if total > max_output_bytes:
                    raise SandboxError(SandboxReason.OUTPUT_LIMIT)
        try:
            exit_code = process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise SandboxError(SandboxReason.TIMEOUT) from None
        return exit_code, bytes(stdout), bytes(stderr)
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()


def _kill_job(job: Path) -> None:
    try:
        (job / "cgroup.kill").write_text("1\n", encoding="ascii")
    except OSError:
        pass


def _wait_unpopulated(job: Path, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        events = dict(_read_key_values(job / "cgroup.events"))
        if events.get("populated") == 0:
            return
        if time.monotonic() >= deadline:
            raise SandboxError(SandboxReason.CLEANUP_FAILED)
        time.sleep(0.01)


def _read_metrics(job: Path) -> SandboxMetrics:
    try:
        memory_peak = _read_nonnegative_integer(job / "memory.peak")
        pids_peak = _read_nonnegative_integer(job / "pids.peak")
        memory_events = _read_key_values(job / "memory.events.local")
        pids_events = _read_key_values(job / "pids.events.local")
        cpu_stats = _read_key_values(job / "cpu.stat")
    except (OSError, ValueError):
        raise SandboxError(SandboxReason.CLEANUP_FAILED) from None
    return SandboxMetrics(
        memory_peak_bytes=memory_peak,
        memory_events=memory_events,
        pids_peak=pids_peak,
        pids_events=pids_events,
        cpu_stats=cpu_stats,
    )


def _read_nonnegative_integer(path: Path) -> int:
    text = path.read_text(encoding="ascii").strip()
    if not text.isdecimal():
        raise ValueError("invalid cgroup integer")
    return int(text)


def _read_key_values(path: Path) -> tuple[tuple[str, int], ...]:
    records: list[tuple[str, int]] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="ascii").splitlines():
        parts = line.split()
        if len(parts) != 2 or parts[0] in seen or not parts[1].isdecimal():
            raise ValueError("invalid cgroup metrics")
        seen.add(parts[0])
        records.append((parts[0], int(parts[1])))
    if not records:
        raise ValueError("empty cgroup metrics")
    return tuple(records)


def _raise_resource_failure(exit_code: int, metrics: SandboxMetrics) -> None:
    memory = dict(metrics.memory_events)
    pids = dict(metrics.pids_events)
    if memory.get("oom_kill", 0) > 0 or memory.get("oom", 0) > 0:
        raise SandboxError(SandboxReason.RESOURCE_MEMORY)
    if pids.get("max", 0) > 0:
        raise SandboxError(SandboxReason.RESOURCE_PIDS)
    if exit_code == -signal.SIGXCPU:
        raise SandboxError(SandboxReason.RESOURCE_CPU)
    if exit_code == -signal.SIGSYS:
        raise SandboxError(SandboxReason.SECCOMP_VIOLATION)
    if exit_code == 77:
        raise SandboxError(SandboxReason.LANDLOCK_SETUP_FAILED)
    if exit_code == 78:
        raise SandboxError(SandboxReason.SECCOMP_SETUP_FAILED)
    if exit_code in {64, 72, 73, 74, 75, 76, 79}:
        raise SandboxError(SandboxReason.LAUNCHER_START_FAILED)


def _close_descriptor(descriptor: int, descriptors: set[int]) -> None:
    if descriptor in descriptors:
        descriptors.remove(descriptor)
        try:
            os.close(descriptor)
        except OSError:
            pass


def _bind_file(path: Path, *, executable: bool, require_static_elf: bool) -> _BoundFile:
    if not isinstance(path, Path) or not path.is_absolute():
        raise SandboxError(SandboxReason.TOOL_INVALID)
    try:
        if path.resolve(strict=True) != path:
            raise SandboxError(SandboxReason.TOOL_INVALID)
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError:
        raise SandboxError(SandboxReason.TOOL_INVALID) from None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or (executable and before.st_mode & 0o111 == 0):
            raise SandboxError(SandboxReason.TOOL_INVALID)
        payload = bytearray()
        offset = 0
        while offset < before.st_size:
            chunk = os.pread(descriptor, min(1024 * 1024, before.st_size - offset), offset)
            if not chunk:
                raise SandboxError(SandboxReason.TOOL_INVALID)
            payload.extend(chunk)
            offset += len(chunk)
        after = os.fstat(descriptor)
        if _stat_key(before) != _stat_key(after):
            raise SandboxError(SandboxReason.TOOL_INVALID)
        if require_static_elf and not _is_static_elf(payload):
            raise SandboxError(SandboxReason.TOOL_NOT_STATIC)
        return _BoundFile(
            path=path,
            device=after.st_dev,
            inode=after.st_ino,
            mode=after.st_mode,
            size=after.st_size,
            modified_ns=after.st_mtime_ns,
            changed_ns=after.st_ctime_ns,
            sha256=hashlib.sha256(payload).hexdigest(),
        )
    finally:
        os.close(descriptor)


def _identity_from_descriptor(
    path: Path,
    descriptor: int,
    before: os.stat_result | None = None,
) -> _BoundFile:
    if before is None:
        before = os.fstat(descriptor)
    path_info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or (before.st_dev, before.st_ino) != (
        path_info.st_dev,
        path_info.st_ino,
    ):
        raise OSError("not the bound regular file")
    digest = hashlib.sha256()
    offset = 0
    while offset < before.st_size:
        chunk = os.pread(descriptor, min(1024 * 1024, before.st_size - offset), offset)
        if not chunk:
            raise OSError("file changed while hashing")
        digest.update(chunk)
        offset += len(chunk)
    after = os.fstat(descriptor)
    if _stat_key(before) != _stat_key(after):
        raise OSError("file changed while hashing")
    return _BoundFile(
        path=path,
        device=after.st_dev,
        inode=after.st_ino,
        mode=after.st_mode,
        size=after.st_size,
        modified_ns=after.st_mtime_ns,
        changed_ns=after.st_ctime_ns,
        sha256=digest.hexdigest(),
    )


def _require_bound_path_unchanged(expected: _BoundFile) -> None:
    try:
        actual = _bind_file(
            expected.path,
            executable=expected.mode & 0o111 != 0,
            require_static_elf=False,
        )
    except SandboxError:
        raise SandboxError(SandboxReason.TOOL_DRIFT) from None
    if actual != expected:
        raise SandboxError(SandboxReason.TOOL_DRIFT)


def _stat_key(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _is_static_elf(payload: bytes) -> bool:
    if len(payload) < 64 or payload[:6] != b"\x7fELF\x02\x01":
        return False
    if struct.unpack_from("<H", payload, 18)[0] != 62:  # EM_X86_64
        return False
    program_offset = struct.unpack_from("<Q", payload, 32)[0]
    program_entry_bytes = struct.unpack_from("<H", payload, 54)[0]
    program_count = struct.unpack_from("<H", payload, 56)[0]
    if program_entry_bytes < 56 or program_count == 0:
        return False
    end = program_offset + program_entry_bytes * program_count
    if end > len(payload):
        return False
    for index in range(program_count):
        header_offset = program_offset + index * program_entry_bytes
        program_type = struct.unpack_from("<I", payload, header_offset)[0]
        if program_type in {2, 3}:  # PT_DYNAMIC or PT_INTERP
            return False
    return True


def _probe_landlock_abi(launcher: _BoundFile) -> int:
    descriptor = _open_bound_file(launcher)
    try:
        capture = subprocess.run(
            (f"/proc/self/fd/{descriptor}", "--probe-landlock-abi"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=2.0,
            check=False,
            close_fds=True,
            pass_fds=(descriptor,),
            env={"LANG": "C", "LC_ALL": "C", "TZ": "UTC"},
        )
    except (OSError, subprocess.SubprocessError):
        raise SandboxError(SandboxReason.SANDBOX_UNAVAILABLE) from None
    finally:
        os.close(descriptor)
    if capture.returncode != 0 or capture.stderr or len(capture.stdout) > 16:
        raise SandboxError(SandboxReason.SANDBOX_UNAVAILABLE)
    try:
        text = capture.stdout.decode("ascii")
        if not text.endswith("\n") or not text[:-1].isdigit():
            raise ValueError
        return int(text[:-1])
    except (UnicodeDecodeError, ValueError):
        raise SandboxError(SandboxReason.SANDBOX_UNAVAILABLE) from None


def _current_cgroup() -> Path:
    current_record = Path("/proc/self/cgroup").read_text(encoding="ascii").strip()
    hierarchy, separator, relative = current_record.partition("::")
    if hierarchy != "0" or separator != "::" or not relative.startswith("/"):
        raise ValueError("not unified cgroup v2")
    return Path("/sys/fs/cgroup") / relative[1:]


def _validate_delegated_topology(root: Path, *, current: Path | None = None) -> None:
    required = {"cpu", "memory", "pids"}
    try:
        if (
            not isinstance(root, Path)
            or not root.is_absolute()
            or root.resolve(strict=True) != root
        ):
            raise OSError("invalid root")
        supervisor = root / "supervisor"
        jobs = root / "jobs"
        if supervisor.resolve(strict=True) != supervisor or jobs.resolve(strict=True) != jobs:
            raise OSError("missing topology")
        if current is None:
            current = _current_cgroup()
        current.relative_to(supervisor)
        if (root / "cgroup.procs").read_text(encoding="ascii").strip():
            raise OSError("delegated root contains processes")
        if (jobs / "cgroup.procs").read_text(encoding="ascii").strip():
            raise OSError("jobs inner node contains processes")
        controllers = set((root / "cgroup.controllers").read_text(encoding="ascii").split())
        root_enabled = set((root / "cgroup.subtree_control").read_text(encoding="ascii").split())
        jobs_enabled = set((jobs / "cgroup.subtree_control").read_text(encoding="ascii").split())
        if (
            not required <= controllers
            or not required <= root_enabled
            or not required <= jobs_enabled
        ):
            raise OSError("controllers not delegated")
        if not (
            os.access(root / "cgroup.procs", os.W_OK)
            and os.access(jobs, os.W_OK | os.X_OK)
            and os.access(jobs / "cgroup.procs", os.W_OK)
        ):
            raise OSError("delegation is not writable")
    except (OSError, ValueError):
        raise SandboxError(SandboxReason.CGROUP_UNAVAILABLE) from None
