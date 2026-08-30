"""Isolated subprocess execution for the RoboTwin replay worker.

The module's public seam is deliberately small: callers provide one immutable
``RuntimeJob`` and receive one complete ``RuntimeExecution``.  Worker stdout and
stderr are retained only as bounded diagnostics.  Progress is accepted solely
from the dedicated, strictly decoded event file descriptor.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

from .runtime_assets import RUNTIME_ASSET_DRIFT_EXIT_CODE, RUNTIME_ASSET_PREFLIGHT_EXIT_CODE
from .runtime_capability import (
    RUNTIME_ARTIFACT_PATHS,
    RUNTIME_ENVIRONMENT_ALLOWLIST,
    RUNTIME_EVIDENCE_ARTIFACT_PATHS,
    RUNTIME_MEDIA_ARTIFACT_PATHS,
    RuntimeCapabilityError,
    canonical_capability_bytes,
    validate_runtime_capability_document,
)
from .runtime_events import (
    DEFAULT_MAX_EVENT_BYTES,
    DEFAULT_MAX_TRANSCRIPT_BYTES,
    RUNTIME_EVENT_SCHEMA,
    RuntimeEvent,
    RuntimeEventCodec,
    RuntimeEventKind,
    RuntimeEventProtocolError,
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_TASK_CONFIG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


class RuntimeExecutionStatus(str, Enum):
    """High-level outcome of an executor call."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RuntimeFailureCode(str, Enum):
    """Stable failure taxonomy for replay recovery and clustering."""

    CAPABILITY_TIMEOUT = "capability_timeout"
    CAPABILITY_PROCESS_FAILED = "capability_process_failed"
    CAPABILITY_PROTOCOL_ERROR = "capability_protocol_error"
    CAPABILITY_MISMATCH = "capability_mismatch"
    CAPABILITY_DRIFT = "capability_drift"
    EVENT_PROTOCOL_ERROR = "event_protocol_error"
    INCOMPLETE_TRANSCRIPT = "incomplete_transcript"
    WORKER_TIMEOUT = "worker_timeout"
    RUNTIME_PREFLIGHT_FAILED = "runtime_preflight_failed"
    RUNTIME_ASSET_DRIFT = "runtime_asset_drift"
    WORKER_CRASH = "worker_crash"
    STREAM_LIMIT_EXCEEDED = "stream_limit_exceeded"
    OBSERVER_FAILED = "observer_failed"
    OUTPUT_SECURITY_ERROR = "output_security_error"


@dataclass(frozen=True, slots=True)
class RuntimeFailure:
    code: RuntimeFailureCode
    message: str


@dataclass(frozen=True, slots=True)
class RuntimeProbeDiagnostics:
    """Bounded diagnostics from one capability probe subprocess."""

    exit_code: int | None
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool
    stderr_truncated: bool
    capability_output: bytes = b""
    capability_output_truncated: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeCapabilitySnapshot:
    """Strictly validated canonical capability document."""

    sha256: str
    bytes: int
    document: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class RuntimeExecutorIdentity:
    """Path-free identity of the host supervisor bytes and bounded policy."""

    interpreter_sha256: str
    interpreter_bytes: int
    runner_sha256: str
    runner_bytes: int
    implementation_sha256: str
    implementation_bytes: int
    timeout_seconds: float
    capability_timeout_seconds: float
    terminate_grace_seconds: float
    max_stdout_bytes: int
    max_stderr_bytes: int
    max_event_bytes: int
    max_transcript_bytes: int
    max_capability_bytes: int
    schema_version: str = "harness.runtime_executor_identity.v1"

    @property
    def canonical_bytes(self) -> bytes:
        """Return the canonical dependency document used by Invocation receipts."""

        document = {
            "schema_version": self.schema_version,
            "interpreter": {
                "sha256": self.interpreter_sha256,
                "bytes": self.interpreter_bytes,
            },
            "runner": {
                "sha256": self.runner_sha256,
                "bytes": self.runner_bytes,
            },
            "implementation": {
                "sha256": self.implementation_sha256,
                "bytes": self.implementation_bytes,
            },
            "timeouts": {
                "worker_seconds": self.timeout_seconds,
                "capability_seconds": self.capability_timeout_seconds,
                "terminate_grace_seconds": self.terminate_grace_seconds,
            },
            "limits": {
                "max_stdout_bytes": self.max_stdout_bytes,
                "max_stderr_bytes": self.max_stderr_bytes,
                "max_event_bytes": self.max_event_bytes,
                "max_transcript_bytes": self.max_transcript_bytes,
                "max_capability_bytes": self.max_capability_bytes,
            },
            "event_protocol": {
                "schema_version": RUNTIME_EVENT_SCHEMA,
                "artifact_paths": sorted(RUNTIME_ARTIFACT_PATHS),
            },
            "environment_allowlist": sorted(RUNTIME_ENVIRONMENT_ALLOWLIST),
        }
        return (
            json.dumps(
                document,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("ascii")

    @property
    def sha256(self) -> str:
        """Return the canonical executor dependency digest."""

        return hashlib.sha256(self.canonical_bytes).hexdigest()


@dataclass(frozen=True, slots=True)
class RuntimeOutputFile:
    """Symlink-safe, content-bound output retained for later CAS publication."""

    relative_path: str
    path: Path
    sha256: str
    bytes: int


@dataclass(frozen=True, slots=True)
class RuntimeJob:
    """One immutable replay attempt; adaptive settle is fixed to zero in V1."""

    run_id: str
    attempt: int
    robotwin_root: Path
    resolved_scene: Path
    expected_capability_sha256: str
    runtime_asset_root: Path
    runtime_asset_manifest: Path
    expected_runtime_asset_snapshot_sha256: str
    asset_catalog: Path | None = None
    task_config: str = "demo_clean"
    precheck_steps: int = 0
    settle_steps: int = 900
    contact_window_steps: int = 120
    video_frames: int = 120
    fps: int = 12
    min_visible_pixels: int = 64
    checkpoint_steps: int = 120


@dataclass(frozen=True, slots=True)
class RuntimeExecution:
    """Complete bounded record returned for both success and failure."""

    status: RuntimeExecutionStatus
    failure: RuntimeFailure | None
    exit_code: int | None
    attempt_root: Path
    capability: RuntimeCapabilitySnapshot | None
    postflight_capability_sha256: str | None
    events: tuple[RuntimeEvent, ...]
    transcript: bytes
    stdout: bytes
    stderr: bytes
    transcript_complete: bool
    transcript_truncated: bool
    stdout_truncated: bool
    stderr_truncated: bool
    output_files: tuple[RuntimeOutputFile, ...]
    preflight_probe: RuntimeProbeDiagnostics | None = None
    postflight_probe: RuntimeProbeDiagnostics | None = None
    secondary_failures: tuple[RuntimeFailure, ...] = ()


class RuntimeExecutor(Protocol):
    """Replay execution seam used by harness handlers."""

    @property
    def identity(self) -> RuntimeExecutorIdentity: ...

    def execute(
        self,
        job: RuntimeJob,
        observer: Callable[[RuntimeEvent], None],
    ) -> RuntimeExecution: ...


class RuntimeExecutorConfigurationError(ValueError):
    """Raised before any subprocess runs when trusted executor input is invalid."""


@dataclass(frozen=True, slots=True)
class _ProcessCapture:
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    events: tuple[RuntimeEvent, ...]
    transcript: bytes
    failure: RuntimeFailure | None
    transcript_complete: bool
    transcript_truncated: bool
    stdout_truncated: bool
    stderr_truncated: bool


@dataclass(frozen=True, slots=True)
class _CapabilityProbe:
    capability: RuntimeCapabilitySnapshot | None
    failure: RuntimeFailure | None
    diagnostics: RuntimeProbeDiagnostics


@dataclass(slots=True)
class _BoundedBuffer:
    limit: int
    value: bytearray
    truncated: bool = False

    @classmethod
    def create(cls, limit: int) -> "_BoundedBuffer":
        return cls(limit=limit, value=bytearray())

    def append(self, chunk: bytes) -> bool:
        remaining = self.limit - len(self.value)
        self.value.extend(chunk[:remaining])
        if len(chunk) > remaining:
            self.truncated = True
        return self.truncated


class _EventLifecycle:
    """Validate semantic event ordering beyond JSONL syntax."""

    def __init__(
        self,
        *,
        precheck_steps: int,
        settle_steps: int,
        video_frames: int,
        checkpoint_steps: int,
    ) -> None:
        self._phase = 0
        self._expected_total_steps = precheck_steps + max(settle_steps, video_frames)
        self._expected_checkpoints = tuple(
            range(checkpoint_steps, self._expected_total_steps + 1, checkpoint_steps)
        )
        self._checkpoint_index = 0
        self._expected_media_paths = RUNTIME_MEDIA_ARTIFACT_PATHS[:4]
        if video_frames > 0:
            self._expected_media_paths = RUNTIME_MEDIA_ARTIFACT_PATHS

    def accept(self, event: RuntimeEvent) -> None:
        fixed = (
            RuntimeEventKind.PREFLIGHT_COMPLETED,
            RuntimeEventKind.SCENE_LOADED,
            RuntimeEventKind.SIMULATION_STARTED,
        )
        if self._phase < len(fixed):
            if event.kind is not fixed[self._phase]:
                raise RuntimeEventProtocolError(
                    f"expected {fixed[self._phase].value}, received {event.kind.value}"
                )
            self._phase += 1
            return
        if self._phase == 3:
            if event.kind is RuntimeEventKind.SIMULATION_CHECKPOINT:
                assert event.completed_steps is not None
                if (
                    self._checkpoint_index >= len(self._expected_checkpoints)
                    or event.completed_steps != self._expected_checkpoints[self._checkpoint_index]
                ):
                    raise RuntimeEventProtocolError(
                        "simulation checkpoint sequence does not match the requested timeline"
                    )
                self._checkpoint_index += 1
                return
            if event.kind is not RuntimeEventKind.SIMULATION_COMPLETED:
                raise RuntimeEventProtocolError(
                    "simulation checkpoints must end with simulation.completed"
                )
            assert event.completed_steps is not None
            if self._checkpoint_index != len(self._expected_checkpoints):
                raise RuntimeEventProtocolError(
                    "simulation completed before every required checkpoint was observed"
                )
            if event.completed_steps != self._expected_total_steps:
                raise RuntimeEventProtocolError(
                    "simulation completed_steps does not match the requested timeline"
                )
            self._phase = 4
            return
        tail = (
            RuntimeEventKind.MEDIA_COMPLETED,
            RuntimeEventKind.EVIDENCE_COMPLETED,
            RuntimeEventKind.WORKER_COMPLETED,
        )
        tail_index = self._phase - 4
        if event.kind is not tail[tail_index]:
            expected = tail[tail_index].value
            raise RuntimeEventProtocolError(f"expected {expected}, received {event.kind.value}")
        if event.kind is RuntimeEventKind.MEDIA_COMPLETED:
            if event.artifact_paths != self._expected_media_paths:
                raise RuntimeEventProtocolError(
                    "media.completed paths do not match the requested video configuration"
                )
        elif event.kind is RuntimeEventKind.EVIDENCE_COMPLETED:
            if event.artifact_paths != RUNTIME_EVIDENCE_ARTIFACT_PATHS:
                raise RuntimeEventProtocolError(
                    "evidence.completed paths must identify both fixed evidence files"
                )
        self._phase += 1

    @property
    def complete(self) -> bool:
        return self._phase == 7


class SubprocessRoboTwinRuntimeExecutor:
    """Run one fixed Python worker in an isolated process group."""

    def __init__(
        self,
        *,
        interpreter: Path,
        runner: Path,
        work_root: Path,
        timeout_seconds: float,
        capability_timeout_seconds: float = 15.0,
        terminate_grace_seconds: float = 0.5,
        max_stdout_bytes: int = 1_048_576,
        max_stderr_bytes: int = 1_048_576,
        max_event_bytes: int = DEFAULT_MAX_EVENT_BYTES,
        max_transcript_bytes: int = DEFAULT_MAX_TRANSCRIPT_BYTES,
        max_capability_bytes: int = 262_144,
        module_root: Path | None = None,
    ) -> None:
        self.interpreter = _fixed_file(interpreter, label="interpreter", executable=True)
        self.runner = _fixed_file(runner, label="runner", executable=False)
        self.interpreter_sha256 = _sha256_file(self.interpreter)
        self.runner_sha256 = _sha256_file(self.runner)
        self.work_root = _absolute_path(work_root)
        inferred_module_root = (
            self.runner.parent.parent if self.runner.parent.name == "script" else self.runner.parent
        )
        self.module_root = _safe_directory(
            module_root if module_root is not None else inferred_module_root,
            label="module_root",
        )
        self.timeout_seconds = _positive_number(timeout_seconds, "timeout_seconds")
        self.capability_timeout_seconds = _positive_number(
            capability_timeout_seconds,
            "capability_timeout_seconds",
        )
        self.terminate_grace_seconds = _positive_number(
            terminate_grace_seconds,
            "terminate_grace_seconds",
        )
        self.max_stdout_bytes = _positive_integer(max_stdout_bytes, "max_stdout_bytes")
        self.max_stderr_bytes = _positive_integer(max_stderr_bytes, "max_stderr_bytes")
        self.max_capability_bytes = _positive_integer(
            max_capability_bytes,
            "max_capability_bytes",
        )
        self.codec = RuntimeEventCodec(
            allowed_artifact_paths=RUNTIME_ARTIFACT_PATHS,
            max_event_bytes=max_event_bytes,
            max_transcript_bytes=max_transcript_bytes,
        )
        implementation = _fixed_file(
            Path(__file__).resolve(), label="implementation", executable=False
        )
        self._identity = RuntimeExecutorIdentity(
            interpreter_sha256=self.interpreter_sha256,
            interpreter_bytes=self.interpreter.stat().st_size,
            runner_sha256=self.runner_sha256,
            runner_bytes=self.runner.stat().st_size,
            implementation_sha256=_sha256_file(implementation),
            implementation_bytes=implementation.stat().st_size,
            timeout_seconds=self.timeout_seconds,
            capability_timeout_seconds=self.capability_timeout_seconds,
            terminate_grace_seconds=self.terminate_grace_seconds,
            max_stdout_bytes=self.max_stdout_bytes,
            max_stderr_bytes=self.max_stderr_bytes,
            max_event_bytes=self.codec.max_event_bytes,
            max_transcript_bytes=self.codec.max_transcript_bytes,
            max_capability_bytes=self.max_capability_bytes,
        )

    @property
    def identity(self) -> RuntimeExecutorIdentity:
        """Return the immutable supervisor policy identity for dependencies."""

        return self._identity

    def execute(
        self,
        job: RuntimeJob,
        observer: Callable[[RuntimeEvent], None],
    ) -> RuntimeExecution:
        normalized = _validate_job(job)
        if not callable(observer):
            raise RuntimeExecutorConfigurationError("observer must be callable")
        attempt_root = _create_attempt_root(self.work_root, normalized.run_id, normalized.attempt)
        capability: RuntimeCapabilitySnapshot | None = None
        postflight_digest: str | None = None
        preflight_diagnostics: RuntimeProbeDiagnostics | None = None
        postflight_diagnostics: RuntimeProbeDiagnostics | None = None
        capture = _empty_capture()
        failure: RuntimeFailure | None = None
        secondary_failures: list[RuntimeFailure] = []

        preflight = self._describe_capability(
            normalized,
            attempt_root / ".capability-preflight.json",
        )
        capability = preflight.capability
        failure = preflight.failure
        preflight_diagnostics = preflight.diagnostics
        if failure is None and capability is not None:
            if capability.sha256 != normalized.expected_capability_sha256:
                failure = RuntimeFailure(
                    RuntimeFailureCode.CAPABILITY_MISMATCH,
                    "preflight capability digest does not match the qualified digest",
                )

        if failure is None:
            capture = self._run_worker(normalized, attempt_root, observer)
            worker_failure = capture.failure
            postflight = self._describe_capability(
                normalized,
                attempt_root / ".capability-postflight.json",
            )
            postflight_diagnostics = postflight.diagnostics
            if postflight.capability is not None:
                postflight_digest = postflight.capability.sha256
            trust_failure = postflight.failure
            if trust_failure is None and postflight_digest != normalized.expected_capability_sha256:
                trust_failure = RuntimeFailure(
                    RuntimeFailureCode.CAPABILITY_DRIFT,
                    "runtime capability changed during execution",
                )
            if trust_failure is not None:
                failure = trust_failure
                if worker_failure is not None:
                    secondary_failures.append(worker_failure)
            else:
                failure = worker_failure

        output_files, output_failure = _snapshot_output_files(
            attempt_root,
            declared_paths=_declared_artifact_paths(capture.events),
            require_exact=failure is None,
        )
        if output_failure is not None and failure is None:
            failure = output_failure
        elif output_failure is not None:
            secondary_failures.append(output_failure)
        status = (
            RuntimeExecutionStatus.SUCCEEDED if failure is None else RuntimeExecutionStatus.FAILED
        )
        return RuntimeExecution(
            status=status,
            failure=failure,
            exit_code=capture.exit_code,
            attempt_root=attempt_root,
            capability=capability,
            postflight_capability_sha256=postflight_digest,
            events=capture.events,
            transcript=capture.transcript,
            stdout=capture.stdout,
            stderr=capture.stderr,
            transcript_complete=capture.transcript_complete,
            transcript_truncated=capture.transcript_truncated,
            stdout_truncated=capture.stdout_truncated,
            stderr_truncated=capture.stderr_truncated,
            output_files=output_files,
            preflight_probe=preflight_diagnostics,
            postflight_probe=postflight_diagnostics,
            secondary_failures=tuple(secondary_failures),
        )

    def _describe_capability(
        self,
        job: RuntimeJob,
        destination: Path,
    ) -> _CapabilityProbe:
        argv = [
            str(self.interpreter),
            str(self.runner),
            "--robotwin-root",
            str(job.robotwin_root),
            "--task-config",
            job.task_config,
            "--describe-capabilities",
            str(destination),
        ]
        try:
            process = subprocess.Popen(
                argv,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_sanitized_environment(self.module_root),
                start_new_session=True,
            )
        except OSError as error:
            return _CapabilityProbe(
                capability=None,
                failure=RuntimeFailure(
                    RuntimeFailureCode.CAPABILITY_PROCESS_FAILED,
                    f"capability process could not start: {type(error).__name__}",
                ),
                diagnostics=_empty_probe_diagnostics(),
            )
        try:
            diagnostics, process_failure = _capture_capability_process(
                process,
                timeout_seconds=self.capability_timeout_seconds,
                terminate_grace_seconds=self.terminate_grace_seconds,
                max_stdout_bytes=self.max_stdout_bytes,
                max_stderr_bytes=self.max_stderr_bytes,
            )
            if process_failure is not None:
                return _CapabilityProbe(None, process_failure, diagnostics)
            try:
                capability_output, capability_truncated = _read_capability_output(
                    destination,
                    max_bytes=self.max_capability_bytes,
                )
                diagnostics = replace(
                    diagnostics,
                    capability_output=capability_output,
                    capability_output_truncated=capability_truncated,
                )
                if not capability_output or capability_truncated:
                    raise ValueError("capability output has an invalid byte length")
                snapshot = _load_capability(
                    capability_output,
                    job=job,
                    interpreter_sha256=self.interpreter_sha256,
                    runner_sha256=self.runner_sha256,
                )
            except (OSError, ValueError, json.JSONDecodeError, RuntimeCapabilityError) as error:
                return _CapabilityProbe(
                    capability=None,
                    failure=RuntimeFailure(
                        RuntimeFailureCode.CAPABILITY_PROTOCOL_ERROR,
                        f"invalid capability document: {error}",
                    ),
                    diagnostics=diagnostics,
                )
        finally:
            _remove_capability_output(destination)
        return _CapabilityProbe(snapshot, None, diagnostics)

    def _run_worker(
        self,
        job: RuntimeJob,
        attempt_root: Path,
        observer: Callable[[RuntimeEvent], None],
    ) -> _ProcessCapture:
        read_fd, write_fd = os.pipe()
        argv = self._worker_argv(job, attempt_root, write_fd)
        try:
            process = subprocess.Popen(
                argv,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_sanitized_environment(self.module_root),
                start_new_session=True,
                pass_fds=(write_fd,),
            )
        except OSError as error:
            os.close(read_fd)
            os.close(write_fd)
            return _empty_capture(
                RuntimeFailure(
                    RuntimeFailureCode.WORKER_CRASH,
                    f"runtime worker could not start: {type(error).__name__}",
                )
            )
        os.close(write_fd)
        assert process.stdout is not None
        assert process.stderr is not None
        return _capture_worker(
            process,
            event_fd=read_fd,
            codec=self.codec,
            observer=observer,
            timeout_seconds=self.timeout_seconds,
            terminate_grace_seconds=self.terminate_grace_seconds,
            max_stdout_bytes=self.max_stdout_bytes,
            max_stderr_bytes=self.max_stderr_bytes,
            precheck_steps=job.precheck_steps,
            settle_steps=job.settle_steps,
            video_frames=job.video_frames,
            checkpoint_steps=job.checkpoint_steps,
        )

    def _worker_argv(self, job: RuntimeJob, attempt_root: Path, event_fd: int) -> list[str]:
        argv = [
            str(self.interpreter),
            str(self.runner),
            "--robotwin-root",
            str(job.robotwin_root),
            "--resolved-scene",
            str(job.resolved_scene),
            "--out-dir",
            str(attempt_root),
            "--task-config",
            job.task_config,
            "--precheck-steps",
            str(job.precheck_steps),
            "--settle-steps",
            str(job.settle_steps),
            "--settle-converge-max",
            "0",
            "--contact-window-steps",
            str(job.contact_window_steps),
            "--video-frames",
            str(job.video_frames),
            "--fps",
            str(job.fps),
            "--min-visible-pixels",
            str(job.min_visible_pixels),
            "--checkpoint-steps",
            str(job.checkpoint_steps),
            "--event-fd",
            str(event_fd),
            "--event-protocol",
            RUNTIME_EVENT_SCHEMA,
            "--expected-capability-sha256",
            job.expected_capability_sha256,
            "--runtime-asset-root",
            str(job.runtime_asset_root),
            "--runtime-asset-manifest",
            str(job.runtime_asset_manifest),
            "--expected-runtime-asset-snapshot-sha256",
            job.expected_runtime_asset_snapshot_sha256,
            "--evidence-only",
        ]
        if job.asset_catalog is not None:
            argv.extend(("--asset-catalog", str(job.asset_catalog)))
        return argv


def _capture_capability_process(
    process: subprocess.Popen[bytes],
    *,
    timeout_seconds: float,
    terminate_grace_seconds: float,
    max_stdout_bytes: int,
    max_stderr_bytes: int,
) -> tuple[RuntimeProbeDiagnostics, RuntimeFailure | None]:
    stdout = _BoundedBuffer.create(max_stdout_bytes)
    stderr = _BoundedBuffer.create(max_stderr_bytes)
    failure: RuntimeFailure | None = None
    terminated = False
    selector = selectors.DefaultSelector()
    assert process.stdout is not None
    assert process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    deadline = time.monotonic() + timeout_seconds
    drain_deadline: float | None = None
    try:
        while selector.get_map():
            now = time.monotonic()
            if failure is None and now >= deadline:
                failure = RuntimeFailure(
                    RuntimeFailureCode.CAPABILITY_TIMEOUT,
                    "capability description timed out",
                )
            if failure is not None and not terminated:
                _terminate_process_group(process, terminate_grace_seconds)
                terminated = True
                drain_deadline = time.monotonic() + terminate_grace_seconds
            if drain_deadline is not None and now >= drain_deadline:
                break
            wait_until = drain_deadline if drain_deadline is not None else deadline
            for key, _ in selector.select(timeout=max(0.0, min(0.05, wait_until - now))):
                chunk = os.read(key.fd, 65_536)
                if not chunk:
                    _close_selector_key(selector, key)
                    continue
                buffer = stdout if key.data == "stdout" else stderr
                if buffer.append(chunk) and failure is None:
                    failure = RuntimeFailure(
                        RuntimeFailureCode.STREAM_LIMIT_EXCEEDED,
                        f"capability {key.data} exceeded its configured byte limit",
                    )
            if process.poll() is not None and drain_deadline is None:
                drain_deadline = time.monotonic() + terminate_grace_seconds
    finally:
        for key in tuple(selector.get_map().values()):
            _close_selector_key(selector, key)
        selector.close()
        _terminate_process_group(process, terminate_grace_seconds)
    if failure is None and process.returncode != 0:
        failure = RuntimeFailure(
            RuntimeFailureCode.CAPABILITY_PROCESS_FAILED,
            f"capability process exited with code {process.returncode}",
        )
    return (
        RuntimeProbeDiagnostics(
            exit_code=process.returncode,
            stdout=bytes(stdout.value),
            stderr=bytes(stderr.value),
            stdout_truncated=stdout.truncated,
            stderr_truncated=stderr.truncated,
        ),
        failure,
    )


def _capture_worker(
    process: subprocess.Popen[bytes],
    *,
    event_fd: int,
    codec: RuntimeEventCodec,
    observer: Callable[[RuntimeEvent], None],
    timeout_seconds: float,
    terminate_grace_seconds: float,
    max_stdout_bytes: int,
    max_stderr_bytes: int,
    precheck_steps: int,
    settle_steps: int,
    video_frames: int,
    checkpoint_steps: int,
) -> _ProcessCapture:
    stdout = _BoundedBuffer.create(max_stdout_bytes)
    stderr = _BoundedBuffer.create(max_stderr_bytes)
    transcript = _BoundedBuffer.create(codec.max_transcript_bytes)
    decoder = codec.stream_decoder()
    lifecycle = _EventLifecycle(
        precheck_steps=precheck_steps,
        settle_steps=settle_steps,
        video_frames=video_frames,
        checkpoint_steps=checkpoint_steps,
    )
    events: list[RuntimeEvent] = []
    failure: RuntimeFailure | None = None
    terminated = False
    selector = selectors.DefaultSelector()
    assert process.stdout is not None
    assert process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    selector.register(event_fd, selectors.EVENT_READ, "event")
    deadline = time.monotonic() + timeout_seconds
    drain_deadline: float | None = None
    try:
        while selector.get_map():
            now = time.monotonic()
            if failure is None and now >= deadline:
                failure = RuntimeFailure(
                    RuntimeFailureCode.WORKER_TIMEOUT,
                    "runtime worker exceeded its deadline",
                )
            if failure is not None and not terminated:
                _terminate_process_group(process, terminate_grace_seconds)
                terminated = True
                drain_deadline = time.monotonic() + terminate_grace_seconds
            if drain_deadline is not None and now >= drain_deadline:
                break
            wait_until = drain_deadline if drain_deadline is not None else deadline
            ready = selector.select(timeout=max(0.0, min(0.05, wait_until - now)))
            for key, _ in ready:
                chunk = os.read(key.fd, 65_536)
                if not chunk:
                    _close_selector_key(selector, key)
                    continue
                if key.data == "stdout":
                    exceeded = stdout.append(chunk)
                elif key.data == "stderr":
                    exceeded = stderr.append(chunk)
                else:
                    exceeded = transcript.append(chunk)
                    if exceeded and failure is None:
                        failure = RuntimeFailure(
                            RuntimeFailureCode.STREAM_LIMIT_EXCEEDED,
                            "runtime event stream exceeded its configured byte limit",
                        )
                    elif failure is None:
                        try:
                            decoded = decoder.feed(chunk)
                            for event in decoded:
                                lifecycle.accept(event)
                                observer(event)
                                events.append(event)
                        except RuntimeEventProtocolError as error:
                            failure = RuntimeFailure(
                                RuntimeFailureCode.EVENT_PROTOCOL_ERROR,
                                str(error),
                            )
                        except Exception as error:  # observer is an untrusted callback
                            failure = RuntimeFailure(
                                RuntimeFailureCode.OBSERVER_FAILED,
                                f"runtime observer failed: {type(error).__name__}: {error}",
                            )
                if exceeded and failure is None:
                    failure = RuntimeFailure(
                        RuntimeFailureCode.STREAM_LIMIT_EXCEEDED,
                        f"runtime {key.data} stream exceeded its configured byte limit",
                    )
            if process.poll() is not None and drain_deadline is None:
                drain_deadline = time.monotonic() + terminate_grace_seconds
        if failure is None:
            try:
                decoder.finish()
            except RuntimeEventProtocolError as error:
                failure = RuntimeFailure(RuntimeFailureCode.EVENT_PROTOCOL_ERROR, str(error))
    finally:
        for key in tuple(selector.get_map().values()):
            _close_selector_key(selector, key)
        selector.close()
        _terminate_process_group(process, terminate_grace_seconds)

    transcript_complete = lifecycle.complete and failure is None
    if failure is None and process.returncode != 0:
        if process.returncode == RUNTIME_ASSET_PREFLIGHT_EXIT_CODE:
            failure_code = (
                RuntimeFailureCode.EVENT_PROTOCOL_ERROR
                if events
                else RuntimeFailureCode.RUNTIME_PREFLIGHT_FAILED
            )
        elif process.returncode == RUNTIME_ASSET_DRIFT_EXIT_CODE:
            failure_code = (
                RuntimeFailureCode.RUNTIME_ASSET_DRIFT
                if events
                else RuntimeFailureCode.EVENT_PROTOCOL_ERROR
            )
        else:
            failure_code = (
                RuntimeFailureCode.WORKER_CRASH
                if events
                else RuntimeFailureCode.RUNTIME_PREFLIGHT_FAILED
            )
        failure = RuntimeFailure(
            failure_code,
            f"runtime worker exited with code {process.returncode}",
        )
    elif failure is None and not lifecycle.complete:
        failure = RuntimeFailure(
            RuntimeFailureCode.INCOMPLETE_TRANSCRIPT,
            "runtime worker exited without a complete terminal transcript",
        )
    return _ProcessCapture(
        exit_code=process.returncode,
        stdout=bytes(stdout.value),
        stderr=bytes(stderr.value),
        events=tuple(events),
        transcript=bytes(transcript.value),
        failure=failure,
        transcript_complete=transcript_complete,
        transcript_truncated=transcript.truncated,
        stdout_truncated=stdout.truncated,
        stderr_truncated=stderr.truncated,
    )


def _close_selector_key(selector: selectors.BaseSelector, key: selectors.SelectorKey) -> None:
    selector.unregister(key.fd)
    if hasattr(key.fileobj, "close"):
        key.fileobj.close()
    else:
        os.close(key.fd)


def _read_capability_output(path: Path, *, max_bytes: int) -> tuple[bytes, bool]:
    path_stat = path.lstat()
    if stat.S_ISLNK(path_stat.st_mode):
        raise ValueError("capability output must be a regular non-symlink file")
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    output = bytearray()
    truncated = path_stat.st_size > max_bytes
    try:
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise ValueError("capability output changed type while being opened")
        truncated = truncated or opened_stat.st_size > max_bytes
        while len(output) <= max_bytes:
            chunk = os.read(descriptor, min(65_536, max_bytes + 1 - len(output)))
            if not chunk:
                break
            output.extend(chunk)
    finally:
        os.close(descriptor)
    if len(output) > max_bytes:
        del output[max_bytes:]
        truncated = True
    return bytes(output), truncated


def _remove_capability_output(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except IsADirectoryError:
        try:
            path.rmdir()
        except OSError:
            pass


def _load_capability(
    payload: bytes,
    *,
    job: RuntimeJob,
    interpreter_sha256: str,
    runner_sha256: str,
) -> RuntimeCapabilitySnapshot:
    value = json.loads(
        payload.decode("utf-8", errors="strict"),
        object_pairs_hook=_unique_json_object,
        parse_constant=_reject_json_constant,
    )
    _validate_capability_document(
        value,
        job=job,
        interpreter_sha256=interpreter_sha256,
        runner_sha256=runner_sha256,
    )
    canonical = canonical_capability_bytes(value)
    if payload != canonical:
        raise ValueError("capability output is not canonical JSON")
    return RuntimeCapabilitySnapshot(
        sha256=hashlib.sha256(payload).hexdigest(),
        bytes=len(payload),
        document=_freeze_json(value),
    )


def _validate_capability_document(
    value: object,
    *,
    job: RuntimeJob,
    interpreter_sha256: str,
    runner_sha256: str,
) -> None:
    document = validate_runtime_capability_document(value)
    if document["runner_sha256"] != runner_sha256:
        raise ValueError("capability runner digest does not match the fixed runner")
    python = document["python"]
    assert isinstance(python, Mapping)
    if python["executable_sha256"] != interpreter_sha256:
        raise ValueError("capability Python executable does not match the fixed interpreter")
    task = document["task_config"]
    assert isinstance(task, Mapping)
    expected_paths = {
        f"task_config/{job.task_config}.yml",
        f"env_cfg/task_config/{job.task_config}.yml",
    }
    if task["path"] not in expected_paths:
        raise ValueError("capability task_config.path does not identify the requested config")
    task_path = job.robotwin_root / str(task["path"])
    if _safe_file_sha256(task_path, root=job.robotwin_root) != task["sha256"]:
        raise ValueError("capability task_config.sha256 does not match the config file")


def _snapshot_output_files(
    root: Path,
    *,
    declared_paths: frozenset[str],
    require_exact: bool,
) -> tuple[tuple[RuntimeOutputFile, ...], RuntimeFailure | None]:
    snapshots: list[RuntimeOutputFile] = []
    try:
        actual_paths: set[str] = set()
        for candidate in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
            relative = candidate.relative_to(root).as_posix()
            file_stat = candidate.lstat()
            if stat.S_ISLNK(file_stat.st_mode):
                raise ValueError(f"runtime output is a symlink: {relative}")
            if stat.S_ISDIR(file_stat.st_mode):
                raise ValueError(f"runtime output directory is not allowlisted: {relative}")
            if relative not in RUNTIME_ARTIFACT_PATHS:
                raise ValueError(f"runtime output is not allowlisted: {relative}")
            sha256, size = _open_file_snapshot(candidate)
            snapshots.append(
                RuntimeOutputFile(
                    relative_path=relative,
                    path=candidate,
                    sha256=sha256,
                    bytes=size,
                )
            )
            actual_paths.add(relative)
        if require_exact and actual_paths != declared_paths:
            raise ValueError("runtime output files do not exactly match declared event artifacts")
    except (OSError, ValueError) as error:
        snapshots.sort(key=lambda item: RUNTIME_ARTIFACT_PATHS.index(item.relative_path))
        return tuple(snapshots), RuntimeFailure(
            RuntimeFailureCode.OUTPUT_SECURITY_ERROR,
            str(error),
        )
    snapshots.sort(key=lambda item: RUNTIME_ARTIFACT_PATHS.index(item.relative_path))
    return tuple(snapshots), None


def _declared_artifact_paths(events: tuple[RuntimeEvent, ...]) -> frozenset[str]:
    return frozenset(path for event in events for path in event.artifact_paths)


def _validate_job(job: RuntimeJob) -> RuntimeJob:
    if not isinstance(job, RuntimeJob):
        raise RuntimeExecutorConfigurationError("job must be a RuntimeJob")
    if not _RUN_ID.fullmatch(job.run_id) or ".." in job.run_id:
        raise RuntimeExecutorConfigurationError("run_id is not canonical")
    if type(job.attempt) is not int or job.attempt < 1:
        raise RuntimeExecutorConfigurationError("attempt must be a positive integer")
    if not _TASK_CONFIG.fullmatch(job.task_config) or ".." in job.task_config:
        raise RuntimeExecutorConfigurationError("task_config is not canonical")
    if not isinstance(job.expected_capability_sha256, str) or not _SHA256.fullmatch(
        job.expected_capability_sha256
    ):
        raise RuntimeExecutorConfigurationError(
            "expected_capability_sha256 must be 64 lowercase hexadecimal characters"
        )
    if not isinstance(job.expected_runtime_asset_snapshot_sha256, str) or not _SHA256.fullmatch(
        job.expected_runtime_asset_snapshot_sha256
    ):
        raise RuntimeExecutorConfigurationError(
            "expected_runtime_asset_snapshot_sha256 must be 64 lowercase hexadecimal characters"
        )
    robotwin_root = _safe_directory(job.robotwin_root, label="robotwin_root")
    resolved_scene = _safe_input_file(job.resolved_scene, label="resolved_scene")
    runtime_asset_root = _safe_directory(job.runtime_asset_root, label="runtime_asset_root")
    runtime_asset_manifest = _safe_input_file(
        job.runtime_asset_manifest,
        label="runtime_asset_manifest",
    )
    try:
        manifest_sha256, _ = _open_file_snapshot(runtime_asset_manifest)
    except (OSError, ValueError) as error:
        raise RuntimeExecutorConfigurationError(
            f"runtime asset snapshot manifest {error}"
        ) from error
    if manifest_sha256 != job.expected_runtime_asset_snapshot_sha256:
        raise RuntimeExecutorConfigurationError(
            "runtime asset snapshot manifest does not match its expected digest"
        )
    asset_catalog = (
        _safe_input_file(job.asset_catalog, label="asset_catalog")
        if job.asset_catalog is not None
        else None
    )
    for name in ("precheck_steps", "video_frames", "min_visible_pixels"):
        value = getattr(job, name)
        if type(value) is not int or value < 0:
            raise RuntimeExecutorConfigurationError(f"{name} must be a nonnegative integer")
    for name in ("settle_steps", "contact_window_steps", "fps", "checkpoint_steps"):
        value = getattr(job, name)
        if type(value) is not int or value < 1:
            raise RuntimeExecutorConfigurationError(f"{name} must be a positive integer")
    return RuntimeJob(
        run_id=job.run_id,
        attempt=job.attempt,
        robotwin_root=robotwin_root,
        resolved_scene=resolved_scene,
        expected_capability_sha256=job.expected_capability_sha256,
        runtime_asset_root=runtime_asset_root,
        runtime_asset_manifest=runtime_asset_manifest,
        expected_runtime_asset_snapshot_sha256=job.expected_runtime_asset_snapshot_sha256,
        asset_catalog=asset_catalog,
        task_config=job.task_config,
        precheck_steps=job.precheck_steps,
        settle_steps=job.settle_steps,
        contact_window_steps=job.contact_window_steps,
        video_frames=job.video_frames,
        fps=job.fps,
        min_visible_pixels=job.min_visible_pixels,
        checkpoint_steps=job.checkpoint_steps,
    )


def _create_attempt_root(work_root: Path, run_id: str, attempt: int) -> Path:
    if work_root.exists() or work_root.is_symlink():
        if work_root.is_symlink() or not work_root.is_dir():
            raise RuntimeExecutorConfigurationError("work_root must be a non-symlink directory")
    else:
        work_root.mkdir(parents=True)
    run_root = work_root / run_id
    if run_root.exists() or run_root.is_symlink():
        if run_root.is_symlink() or not run_root.is_dir():
            raise RuntimeExecutorConfigurationError("run output root is unsafe")
    else:
        run_root.mkdir()
    attempt_root = run_root / f"attempt-{attempt}"
    try:
        attempt_root.mkdir()
    except FileExistsError as error:
        raise RuntimeExecutorConfigurationError("attempt output root already exists") from error
    return attempt_root


def _empty_capture(failure: RuntimeFailure | None = None) -> _ProcessCapture:
    return _ProcessCapture(
        exit_code=None,
        stdout=b"",
        stderr=b"",
        events=(),
        transcript=b"",
        failure=failure,
        transcript_complete=False,
        transcript_truncated=False,
        stdout_truncated=False,
        stderr_truncated=False,
    )


def _empty_probe_diagnostics() -> RuntimeProbeDiagnostics:
    return RuntimeProbeDiagnostics(
        exit_code=None,
        stdout=b"",
        stderr=b"",
        stdout_truncated=False,
        stderr_truncated=False,
    )


def _terminate_process_group(process: subprocess.Popen[bytes], grace_seconds: float) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait()
        return
    if process.poll() is None:
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if process.poll() is None:
        process.wait()


def _sanitized_environment(module_root: Path) -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if key in RUNTIME_ENVIRONMENT_ALLOWLIST
    }
    environment["PYTHONPATH"] = str(module_root)
    environment["PYTHONUNBUFFERED"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def _fixed_file(path: Path, *, label: str, executable: bool) -> Path:
    try:
        resolved = _absolute_path(path).resolve(strict=True)
    except OSError as error:
        raise RuntimeExecutorConfigurationError(f"{label} does not exist") from error
    if not resolved.is_file():
        raise RuntimeExecutorConfigurationError(f"{label} must be a regular file")
    if executable and not os.access(resolved, os.X_OK):
        raise RuntimeExecutorConfigurationError(f"{label} must be executable")
    return resolved


def _safe_directory(path: Path, *, label: str) -> Path:
    candidate = _absolute_path(path)
    if candidate.is_symlink() or not candidate.is_dir():
        raise RuntimeExecutorConfigurationError(f"{label} must be a non-symlink directory")
    return candidate.resolve(strict=True)


def _safe_input_file(path: Path, *, label: str) -> Path:
    candidate = _absolute_path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise RuntimeExecutorConfigurationError(f"{label} must be a non-symlink regular file")
    return candidate.resolve(strict=True)


def _absolute_path(path: Path) -> Path:
    if not isinstance(path, Path):
        raise RuntimeExecutorConfigurationError("filesystem paths must be pathlib.Path values")
    return Path(os.path.abspath(path.expanduser()))


def _positive_number(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise RuntimeExecutorConfigurationError(f"{name} must be a positive number")
    return float(value)


def _positive_integer(value: int, name: str) -> int:
    if type(value) is not int or value < 1:
        raise RuntimeExecutorConfigurationError(f"{name} must be a positive integer")
    return value


def _safe_file_sha256(path: Path, *, root: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("capability-bound file is missing or unsafe")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("capability-bound file escapes its declared root")
    return _sha256_file(resolved)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _open_file_snapshot(path: Path) -> tuple[str, int]:
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    digest = hashlib.sha256()
    size = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("runtime output changed type while being read")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if _snapshot_file_identity(before) != _snapshot_file_identity(after) or size != before.st_size:
        raise ValueError("changed while being read")
    return digest.hexdigest(), size


def _snapshot_file_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"capability document has duplicate key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"capability document contains non-standard constant {value}")


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value
