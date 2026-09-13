"""Strict JSONL progress protocol for the replay worker's dedicated event FD.

The protocol deliberately carries only bounded progress facts.  Simulator stdout and stderr are
diagnostic streams and must never be parsed as events.  Artifact paths are accepted only when they
exactly match a caller-provided allowlist; this module enforces lexical path safety but cannot prove
that a path on disk does not traverse a symlink.

``simulation.checkpoint`` and ``simulation.completed`` require ``completed_steps``.  Only
``media.completed`` and ``evidence.completed`` accept (and require) ``artifact_paths``.  The other
kinds accept neither field.  A worker may stop before its terminal event when its process fails, but
if ``worker.completed`` is present it must be the final record.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from threading import Lock
from typing import Any

RUNTIME_EVENT_SCHEMA = "harness.runtime_event.v1"
DEFAULT_MAX_EVENT_BYTES = 16_384
DEFAULT_MAX_TRANSCRIPT_BYTES = 1_048_576


class RuntimeEventProtocolError(ValueError):
    """Raised when an event stream violates the replay worker protocol."""


class RuntimeEventKind(str, Enum):
    """Closed set of progress facts a replay worker may publish."""

    PREFLIGHT_COMPLETED = "preflight.completed"
    SCENE_LOADED = "scene.loaded"
    SIMULATION_STARTED = "simulation.started"
    SIMULATION_CHECKPOINT = "simulation.checkpoint"
    SIMULATION_COMPLETED = "simulation.completed"
    MEDIA_COMPLETED = "media.completed"
    EVIDENCE_COMPLETED = "evidence.completed"
    WORKER_COMPLETED = "worker.completed"


RUNTIME_EVENT_KINDS = frozenset(kind.value for kind in RuntimeEventKind)
_STEP_KINDS = frozenset(
    {
        RuntimeEventKind.SIMULATION_CHECKPOINT,
        RuntimeEventKind.SIMULATION_COMPLETED,
    }
)
_ARTIFACT_KINDS = frozenset(
    {
        RuntimeEventKind.MEDIA_COMPLETED,
        RuntimeEventKind.EVIDENCE_COMPLETED,
    }
)
_BASE_FIELDS = frozenset({"schema_version", "seq", "kind"})
_OPTIONAL_FIELDS = frozenset({"completed_steps", "artifact_paths"})


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """Immutable, validated replay-worker event."""

    seq: int
    kind: RuntimeEventKind
    completed_steps: int | None = None
    artifact_paths: tuple[str, ...] = ()

    @property
    def schema_version(self) -> str:
        """Return the immutable wire-schema identity."""

        return RUNTIME_EVENT_SCHEMA


class RuntimeEventCodec:
    """Construct, encode, and decode events under one fixed artifact allowlist."""

    def __init__(
        self,
        *,
        allowed_artifact_paths: Iterable[str],
        max_event_bytes: int = DEFAULT_MAX_EVENT_BYTES,
        max_transcript_bytes: int = DEFAULT_MAX_TRANSCRIPT_BYTES,
    ) -> None:
        self.max_event_bytes = _positive_limit(max_event_bytes, name="max_event_bytes")
        self.max_transcript_bytes = _positive_limit(
            max_transcript_bytes,
            name="max_transcript_bytes",
        )
        if self.max_event_bytes > self.max_transcript_bytes:
            raise RuntimeEventProtocolError("max_event_bytes cannot exceed max_transcript_bytes")
        if isinstance(allowed_artifact_paths, (str, bytes)) or not isinstance(
            allowed_artifact_paths, Iterable
        ):
            raise RuntimeEventProtocolError("allowed_artifact_paths must be a collection of paths")
        paths: set[str] = set()
        for path in allowed_artifact_paths:
            _validate_safe_artifact_path(path)
            paths.add(path)
        self.allowed_artifact_paths = frozenset(paths)

    def event(
        self,
        *,
        seq: int,
        kind: RuntimeEventKind | str,
        completed_steps: int | None = None,
        artifact_paths: Iterable[str] = (),
    ) -> RuntimeEvent:
        """Build an event and apply exactly the same checks used by the decoder."""

        if isinstance(artifact_paths, (str, bytes)) or not isinstance(artifact_paths, Iterable):
            raise RuntimeEventProtocolError("artifact_paths must be a collection of paths")
        path_values = tuple(artifact_paths)
        payload: dict[str, Any] = {
            "schema_version": RUNTIME_EVENT_SCHEMA,
            "seq": seq,
            "kind": kind.value if isinstance(kind, RuntimeEventKind) else kind,
        }
        if completed_steps is not None:
            payload["completed_steps"] = completed_steps
        if path_values:
            payload["artifact_paths"] = list(path_values)
        return self._validate_payload(payload, expected_seq=None)

    def encode(self, event: RuntimeEvent) -> bytes:
        """Return one canonical UTF-8 JSONL record, including its final LF."""

        validated = self._validate_event_object(event)
        payload: dict[str, Any] = {
            "schema_version": RUNTIME_EVENT_SCHEMA,
            "seq": validated.seq,
            "kind": validated.kind.value,
        }
        if validated.completed_steps is not None:
            payload["completed_steps"] = validated.completed_steps
        if validated.artifact_paths:
            payload["artifact_paths"] = list(validated.artifact_paths)
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        if len(encoded) > self.max_event_bytes:
            raise RuntimeEventProtocolError(
                f"runtime event exceeds {self.max_event_bytes}-byte limit"
            )
        return encoded

    def decode_line(self, line: bytes, *, expected_seq: int) -> RuntimeEvent:
        """Decode exactly one LF-terminated physical JSONL record."""

        _validate_expected_seq(expected_seq)
        if not isinstance(line, bytes):
            raise RuntimeEventProtocolError("runtime event record must be bytes")
        if len(line) > self.max_event_bytes:
            raise RuntimeEventProtocolError(
                f"runtime event exceeds {self.max_event_bytes}-byte limit"
            )
        if line.count(b"\n") != 1 or not line.endswith(b"\n"):
            raise RuntimeEventProtocolError(
                "runtime event must occupy one physical line ending in LF"
            )
        if b"\r" in line:
            raise RuntimeEventProtocolError("runtime event must not contain a carriage return")
        body = line[:-1]
        if not body:
            raise RuntimeEventProtocolError("runtime event line must not be empty")
        try:
            text = body.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise RuntimeEventProtocolError("runtime event is not valid UTF-8") from exc
        try:
            payload = json.loads(
                text,
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
        except RuntimeEventProtocolError:
            raise
        except (json.JSONDecodeError, RecursionError) as exc:
            raise RuntimeEventProtocolError("runtime event is not valid JSON") from exc
        return self._validate_payload(payload, expected_seq=expected_seq)

    def stream_decoder(self) -> RuntimeEventStreamDecoder:
        """Create a stateful decoder for arbitrary byte chunks read from a dedicated FD."""

        return RuntimeEventStreamDecoder(self)

    def parse_transcript(self, transcript: bytes) -> tuple[RuntimeEvent, ...]:
        """Parse a complete JSONL transcript with the streaming rules."""

        if not isinstance(transcript, bytes):
            raise RuntimeEventProtocolError("runtime event transcript must be bytes")
        decoder = self.stream_decoder()
        events = decoder.feed(transcript)
        decoder.finish()
        return events

    def _validate_event_object(self, event: RuntimeEvent) -> RuntimeEvent:
        if not isinstance(event, RuntimeEvent):
            raise RuntimeEventProtocolError("encode requires a RuntimeEvent")
        if not isinstance(event.kind, RuntimeEventKind):
            raise RuntimeEventProtocolError("runtime event kind is invalid")
        if not isinstance(event.artifact_paths, tuple):
            raise RuntimeEventProtocolError("RuntimeEvent artifact_paths must be a tuple")
        payload: dict[str, Any] = {
            "schema_version": RUNTIME_EVENT_SCHEMA,
            "seq": event.seq,
            "kind": event.kind.value,
        }
        if event.completed_steps is not None:
            payload["completed_steps"] = event.completed_steps
        if event.artifact_paths:
            payload["artifact_paths"] = list(event.artifact_paths)
        return self._validate_payload(payload, expected_seq=None)

    def _validate_payload(
        self,
        payload: object,
        *,
        expected_seq: int | None,
    ) -> RuntimeEvent:
        if not isinstance(payload, dict):
            raise RuntimeEventProtocolError("runtime event must be a JSON object")
        extra_fields = set(payload) - _BASE_FIELDS - _OPTIONAL_FIELDS
        if extra_fields:
            raise RuntimeEventProtocolError(
                f"runtime event has extra fields: {sorted(extra_fields)!r}"
            )
        if payload.get("schema_version") != RUNTIME_EVENT_SCHEMA:
            raise RuntimeEventProtocolError(
                f"runtime event schema must be {RUNTIME_EVENT_SCHEMA!r}"
            )
        seq = payload.get("seq")
        if type(seq) is not int or seq < 1:
            raise RuntimeEventProtocolError("runtime event seq must be a positive integer")
        if expected_seq is not None and seq != expected_seq:
            raise RuntimeEventProtocolError(
                f"runtime event expected seq {expected_seq}, received {seq}"
            )
        raw_kind = payload.get("kind")
        if not isinstance(raw_kind, str):
            raise RuntimeEventProtocolError("runtime event kind must be a string")
        try:
            kind = RuntimeEventKind(raw_kind)
        except ValueError as exc:
            raise RuntimeEventProtocolError(
                f"runtime event kind is not allowed: {raw_kind!r}"
            ) from exc

        completed_present = "completed_steps" in payload
        if kind in _STEP_KINDS:
            if not completed_present:
                raise RuntimeEventProtocolError(f"completed_steps is required for {kind.value}")
            completed_steps = payload["completed_steps"]
            if type(completed_steps) is not int or completed_steps < 0:
                raise RuntimeEventProtocolError("completed_steps must be a nonnegative integer")
        else:
            if completed_present:
                raise RuntimeEventProtocolError(
                    f"completed_steps is not permitted for {kind.value}"
                )
            completed_steps = None

        artifact_present = "artifact_paths" in payload
        if kind in _ARTIFACT_KINDS:
            if not artifact_present:
                raise RuntimeEventProtocolError(f"artifact_paths is required for {kind.value}")
            artifact_values = payload["artifact_paths"]
            if not isinstance(artifact_values, list):
                raise RuntimeEventProtocolError("artifact_paths must be an array")
            if not artifact_values:
                raise RuntimeEventProtocolError("artifact_paths must not be empty")
            if not all(isinstance(path, str) for path in artifact_values):
                raise RuntimeEventProtocolError("artifact_paths must contain strings")
            if len(set(artifact_values)) != len(artifact_values):
                raise RuntimeEventProtocolError("artifact_paths must not contain duplicates")
            for path in artifact_values:
                _validate_safe_artifact_path(path)
                if path not in self.allowed_artifact_paths:
                    raise RuntimeEventProtocolError(
                        f"runtime event artifact path is not allowlisted: {path!r}"
                    )
            artifact_paths = tuple(artifact_values)
        else:
            if artifact_present:
                raise RuntimeEventProtocolError(f"artifact_paths is not permitted for {kind.value}")
            artifact_paths = ()

        return RuntimeEvent(
            seq=seq,
            kind=kind,
            completed_steps=completed_steps,
            artifact_paths=artifact_paths,
        )


class RuntimeEventStreamDecoder:
    """Incrementally decode arbitrary byte chunks while enforcing contiguous sequencing."""

    def __init__(self, codec: RuntimeEventCodec) -> None:
        self._codec = codec
        self._buffer = bytearray()
        self._total_bytes = 0
        self._expected_seq = 1
        self._terminal_seen = False
        self._failed = False
        self._finished = False

    def feed(self, chunk: bytes) -> tuple[RuntimeEvent, ...]:
        """Consume bytes and return all complete events decoded from this chunk."""

        self._ensure_open()
        try:
            if not isinstance(chunk, bytes):
                raise RuntimeEventProtocolError("runtime event stream chunks must be bytes")
            new_total = self._total_bytes + len(chunk)
            if new_total > self._codec.max_transcript_bytes:
                raise RuntimeEventProtocolError(
                    "runtime event transcript exceeds "
                    f"{self._codec.max_transcript_bytes}-byte limit"
                )
            self._total_bytes = new_total
            self._buffer.extend(chunk)
            events: list[RuntimeEvent] = []
            while True:
                newline_index = self._buffer.find(b"\n")
                if newline_index < 0:
                    break
                record_size = newline_index + 1
                if record_size > self._codec.max_event_bytes:
                    raise RuntimeEventProtocolError(
                        f"runtime event exceeds {self._codec.max_event_bytes}-byte limit"
                    )
                record = bytes(self._buffer[:record_size])
                del self._buffer[:record_size]
                if self._terminal_seen:
                    raise RuntimeEventProtocolError("worker.completed must be the final event")
                event = self._codec.decode_line(record, expected_seq=self._expected_seq)
                events.append(event)
                self._expected_seq += 1
                self._terminal_seen = event.kind is RuntimeEventKind.WORKER_COMPLETED
            if len(self._buffer) > self._codec.max_event_bytes:
                raise RuntimeEventProtocolError(
                    f"runtime event exceeds {self._codec.max_event_bytes}-byte limit"
                )
            return tuple(events)
        except RuntimeEventProtocolError:
            self._failed = True
            raise

    def finish(self) -> None:
        """Close the stream, rejecting any unterminated final record."""

        self._ensure_open()
        if self._buffer:
            self._failed = True
            raise RuntimeEventProtocolError(
                "runtime event stream ended with an unterminated record"
            )
        self._finished = True

    def _ensure_open(self) -> None:
        if self._failed:
            raise RuntimeEventProtocolError("runtime event decoder is in a failed state")
        if self._finished:
            raise RuntimeEventProtocolError("runtime event decoder is already finished")


class RuntimeEventEmitter:
    """Write sequenced events to an owned dedicated FD without touching stdout/stderr."""

    def __init__(self, *, fd: int, codec: RuntimeEventCodec) -> None:
        if type(fd) is not int or fd <= 2:
            raise RuntimeEventProtocolError(
                "runtime events require a dedicated file descriptor greater than 2"
            )
        if not isinstance(codec, RuntimeEventCodec):
            raise RuntimeEventProtocolError("runtime event emitter requires a RuntimeEventCodec")
        self._fd = fd
        self._codec = codec
        self._next_seq = 1
        self._terminal_seen = False
        self._failed = False
        self._lock = Lock()

    def emit(
        self,
        *,
        kind: RuntimeEventKind | str,
        completed_steps: int | None = None,
        artifact_paths: Iterable[str] = (),
    ) -> RuntimeEvent:
        """Validate and synchronously write one event; sequence advances only after success."""

        with self._lock:
            if self._failed:
                raise RuntimeEventProtocolError("runtime event emitter is in a failed state")
            if self._terminal_seen:
                raise RuntimeEventProtocolError("worker.completed has already been emitted")
            event = self._codec.event(
                seq=self._next_seq,
                kind=kind,
                completed_steps=completed_steps,
                artifact_paths=artifact_paths,
            )
            encoded = self._codec.encode(event)
            try:
                _write_all(self._fd, encoded)
            except RuntimeEventProtocolError:
                self._failed = True
                raise
            self._next_seq += 1
            self._terminal_seen = event.kind is RuntimeEventKind.WORKER_COMPLETED
            return event


def _positive_limit(value: int, *, name: str) -> int:
    if type(value) is not int or value < 1:
        raise RuntimeEventProtocolError(f"{name} byte limit must be a positive integer")
    return value


def _validate_expected_seq(value: int) -> None:
    if type(value) is not int or value < 1:
        raise RuntimeEventProtocolError("expected_seq must be a positive integer")


def _validate_safe_artifact_path(path: object) -> None:
    if not isinstance(path, str) or not path:
        raise RuntimeEventProtocolError("artifact path must be a nonempty string")
    if any(ord(character) < 32 or ord(character) == 127 for character in path):
        raise RuntimeEventProtocolError("artifact path must not contain control characters")
    if "\\" in path or ":" in path:
        raise RuntimeEventProtocolError("artifact path must use portable POSIX relative syntax")
    pure_path = PurePosixPath(path)
    if pure_path.is_absolute() or ".." in pure_path.parts:
        raise RuntimeEventProtocolError("artifact path must be relative and must not contain '..'")
    if path == "." or pure_path.as_posix() != path or "." in pure_path.parts:
        raise RuntimeEventProtocolError("artifact path must use canonical relative syntax")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeEventProtocolError(f"runtime event has duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise RuntimeEventProtocolError(f"runtime event has non-standard JSON constant: {value}")


def _write_all(fd: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        try:
            written = os.write(fd, payload[offset:])
        except InterruptedError:
            continue
        except OSError as exc:
            raise RuntimeEventProtocolError("runtime event write failed") from exc
        if type(written) is not int or written <= 0 or written > len(payload) - offset:
            raise RuntimeEventProtocolError("runtime event write returned an invalid byte count")
        offset += written
