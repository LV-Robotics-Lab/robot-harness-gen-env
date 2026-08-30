from __future__ import annotations

import json
import os
from dataclasses import FrozenInstanceError

import pytest

import self_improving.harness.runtime_events as runtime_events
from self_improving.harness.runtime_events import (
    RUNTIME_EVENT_SCHEMA,
    RuntimeEventCodec,
    RuntimeEventEmitter,
    RuntimeEventKind,
    RuntimeEventProtocolError,
)

ARTIFACT_PATHS = (
    "media/replay.mp4",
    "media/回放.mp4",
    "evidence/runtime.json",
)


def _codec(
    *,
    max_event_bytes: int = 16_384,
    max_transcript_bytes: int = 1_048_576,
) -> RuntimeEventCodec:
    return RuntimeEventCodec(
        allowed_artifact_paths=ARTIFACT_PATHS,
        max_event_bytes=max_event_bytes,
        max_transcript_bytes=max_transcript_bytes,
    )


def _line(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


def _payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": RUNTIME_EVENT_SCHEMA,
        "seq": 1,
        "kind": RuntimeEventKind.PREFLIGHT_COMPLETED.value,
    }
    payload.update(updates)
    return payload


def test_codec_round_trips_every_event_kind_and_emits_canonical_jsonl() -> None:
    codec = _codec()
    events = (
        codec.event(seq=1, kind=RuntimeEventKind.PREFLIGHT_COMPLETED),
        codec.event(seq=2, kind="scene.loaded"),
        codec.event(seq=3, kind="simulation.started"),
        codec.event(seq=4, kind="simulation.checkpoint", completed_steps=30),
        codec.event(seq=5, kind="simulation.completed", completed_steps=60),
        codec.event(seq=6, kind="media.completed", artifact_paths=("media/replay.mp4",)),
        codec.event(
            seq=7,
            kind="evidence.completed",
            artifact_paths=("evidence/runtime.json",),
        ),
        codec.event(seq=8, kind="worker.completed"),
    )

    transcript = b"".join(codec.encode(event) for event in events)

    assert codec.parse_transcript(transcript) == events
    assert events[0].schema_version == RUNTIME_EVENT_SCHEMA
    assert codec.encode(events[0]) == (
        b'{"kind":"preflight.completed","schema_version":"harness.runtime_event.v1","seq":1}\n'
    )
    with pytest.raises(FrozenInstanceError):
        events[0].seq = 9  # type: ignore[misc]


def test_stream_decoder_handles_record_and_utf8_boundaries() -> None:
    codec = _codec()
    first = codec.event(seq=1, kind="preflight.completed")
    second = codec.event(
        seq=2,
        kind="media.completed",
        artifact_paths=("media/回放.mp4",),
    )
    transcript = codec.encode(first) + codec.encode(second)
    split_at = transcript.index("回".encode()) + 1
    decoder = codec.stream_decoder()

    assert decoder.feed(transcript[:3]) == ()
    assert decoder.feed(transcript[3:split_at]) == (first,)
    assert decoder.feed(transcript[split_at:]) == (second,)
    decoder.finish()

    with pytest.raises(RuntimeEventProtocolError, match="finished"):
        decoder.feed(b"")
    with pytest.raises(RuntimeEventProtocolError, match="finished"):
        decoder.finish()


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"schema_version": "harness.runtime_event.v2"}, "schema"),
        ({"kind": "simulation.paused"}, "kind"),
        ({"seq": True}, "seq"),
        ({"seq": 0}, "seq"),
        ({"seq": "1"}, "seq"),
        ({"unexpected": "field"}, "extra fields"),
        ({"completed_steps": 0}, "completed_steps is not permitted"),
        (
            {"kind": "simulation.checkpoint"},
            "completed_steps is required",
        ),
        (
            {"kind": "simulation.completed", "completed_steps": True},
            "nonnegative integer",
        ),
        (
            {"kind": "simulation.completed", "completed_steps": -1},
            "nonnegative integer",
        ),
        (
            {"kind": "simulation.completed", "completed_steps": 1.0},
            "nonnegative integer",
        ),
        ({"artifact_paths": ["media/replay.mp4"]}, "artifact_paths is not permitted"),
        ({"kind": "media.completed"}, "artifact_paths is required"),
        ({"kind": "media.completed", "artifact_paths": []}, "must not be empty"),
        (
            {"kind": "media.completed", "artifact_paths": "media/replay.mp4"},
            "must be an array",
        ),
        (
            {"kind": "media.completed", "artifact_paths": [7]},
            "must contain strings",
        ),
        (
            {
                "kind": "media.completed",
                "artifact_paths": ["media/replay.mp4", "media/replay.mp4"],
            },
            "duplicates",
        ),
        (
            {"kind": "media.completed", "artifact_paths": ["media/unknown.mp4"]},
            "not allowlisted",
        ),
    ],
)
def test_decoder_rejects_invalid_schema_fields_and_semantics(
    updates: dict[str, object], message: str
) -> None:
    codec = _codec()

    with pytest.raises(RuntimeEventProtocolError, match=message):
        codec.decode_line(_line(_payload(**updates)), expected_seq=1)


def test_decoder_requires_sequence_to_start_at_one_and_remain_contiguous() -> None:
    codec = _codec()
    first = codec.event(seq=1, kind="preflight.completed")
    third = codec.event(seq=3, kind="scene.loaded")
    decoder = codec.stream_decoder()

    assert decoder.feed(codec.encode(first)) == (first,)
    with pytest.raises(RuntimeEventProtocolError, match="expected seq 2"):
        decoder.feed(codec.encode(third))
    with pytest.raises(RuntimeEventProtocolError, match="failed"):
        decoder.feed(codec.encode(first))
    with pytest.raises(RuntimeEventProtocolError, match="failed"):
        decoder.finish()

    with pytest.raises(RuntimeEventProtocolError, match="expected seq 1"):
        codec.parse_transcript(codec.encode(codec.event(seq=2, kind="preflight.completed")))


@pytest.mark.parametrize(
    ("line", "message"),
    [
        (b"\xff\n", "UTF-8"),
        (b"{not json}\n", "JSON"),
        (
            b'{"schema_version":"harness.runtime_event.v1",\n"seq":1}\n',
            "one physical line",
        ),
        (b"{}\n\n", "one physical line"),
        (b"{}\r\n", "carriage return"),
        (b"\n", "empty"),
        (b"[]\n", "JSON object"),
        (b"NaN\n", "non-standard JSON constant"),
        (
            b'{"schema_version":"harness.runtime_event.v1","seq":1,"seq":1,'
            b'"kind":"preflight.completed"}\n',
            "duplicate JSON key",
        ),
    ],
)
def test_decode_line_rejects_invalid_jsonl_records(line: bytes, message: str) -> None:
    with pytest.raises(RuntimeEventProtocolError, match=message):
        _codec().decode_line(line, expected_seq=1)


def test_decode_line_rejects_wrong_input_type_oversize_and_deep_json() -> None:
    codec = _codec(max_event_bytes=100, max_transcript_bytes=200)
    with pytest.raises(RuntimeEventProtocolError, match="bytes"):
        codec.decode_line("{}\n", expected_seq=1)  # type: ignore[arg-type]
    with pytest.raises(RuntimeEventProtocolError, match="event exceeds"):
        codec.decode_line(b"x" * 100 + b"\n", expected_seq=1)

    deep_codec = _codec()
    deeply_nested = b"[" * 1_100 + b"]" * 1_100 + b"\n"
    with pytest.raises(RuntimeEventProtocolError, match="JSON"):
        deep_codec.decode_line(deeply_nested, expected_seq=1)


def test_stream_rejects_unterminated_oversize_and_non_bytes_input() -> None:
    codec = _codec(max_event_bytes=100, max_transcript_bytes=140)
    decoder = codec.stream_decoder()
    with pytest.raises(RuntimeEventProtocolError, match="bytes"):
        decoder.feed("not bytes")  # type: ignore[arg-type]

    decoder = codec.stream_decoder()
    decoder.feed(b"{")
    with pytest.raises(RuntimeEventProtocolError, match="unterminated"):
        decoder.finish()

    decoder = codec.stream_decoder()
    with pytest.raises(RuntimeEventProtocolError, match="event exceeds"):
        decoder.feed(b"x" * 101)

    decoder = codec.stream_decoder()
    with pytest.raises(RuntimeEventProtocolError, match="event exceeds"):
        decoder.feed(b"x" * 100 + b"\n")

    decoder = codec.stream_decoder()
    first = codec.encode(codec.event(seq=1, kind="preflight.completed"))
    second = codec.encode(codec.event(seq=2, kind="scene.loaded"))
    assert len(first) < 100 and len(second) < 100 and len(first + second) > 140
    assert decoder.feed(first) != ()
    with pytest.raises(RuntimeEventProtocolError, match="transcript exceeds"):
        decoder.feed(second)

    with pytest.raises(RuntimeEventProtocolError, match="bytes"):
        codec.parse_transcript("not bytes")  # type: ignore[arg-type]
    assert codec.parse_transcript(b"") == ()


def test_codec_rejects_oversize_encoded_event() -> None:
    artifact = "media/" + "a" * 100 + ".mp4"
    codec = RuntimeEventCodec(
        allowed_artifact_paths=(artifact,),
        max_event_bytes=100,
        max_transcript_bytes=200,
    )
    event = codec.event(seq=1, kind="media.completed", artifact_paths=(artifact,))

    with pytest.raises(RuntimeEventProtocolError, match="event exceeds"):
        codec.encode(event)


@pytest.mark.parametrize(
    "allowed_paths",
    [
        "media/replay.mp4",
        ("",),
        (".",),
        ("/tmp/replay.mp4",),
        ("../replay.mp4",),
        ("media/../replay.mp4",),
        ("./media/replay.mp4",),
        ("media//replay.mp4",),
        ("media\\replay.mp4",),
        ("C:replay.mp4",),
        ("media/replay.mp4\x00",),
        (7,),
    ],
)
def test_codec_rejects_non_collection_or_unsafe_allowlist_paths(
    allowed_paths: object,
) -> None:
    with pytest.raises(RuntimeEventProtocolError, match="artifact|collection"):
        RuntimeEventCodec(allowed_artifact_paths=allowed_paths)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("max_event_bytes", "max_transcript_bytes"),
    [(0, 100), (True, 100), (100, 0), (100, True), (101, 100)],
)
def test_codec_rejects_invalid_size_limits(max_event_bytes: int, max_transcript_bytes: int) -> None:
    with pytest.raises(RuntimeEventProtocolError, match="byte limit|max_event_bytes"):
        RuntimeEventCodec(
            allowed_artifact_paths=(),
            max_event_bytes=max_event_bytes,
            max_transcript_bytes=max_transcript_bytes,
        )


def test_event_factory_and_encoder_revalidate_typed_values() -> None:
    codec = _codec()
    with pytest.raises(RuntimeEventProtocolError, match="kind"):
        codec.event(seq=1, kind="unknown")
    with pytest.raises(RuntimeEventProtocolError, match="seq"):
        codec.event(seq=False, kind="preflight.completed")
    with pytest.raises(RuntimeEventProtocolError, match="collection"):
        codec.event(
            seq=1,
            kind="media.completed",
            artifact_paths="media/replay.mp4",  # type: ignore[arg-type]
        )

    valid = codec.event(seq=1, kind="preflight.completed")
    object.__setattr__(valid, "kind", "unknown")
    with pytest.raises(RuntimeEventProtocolError, match="kind"):
        codec.encode(valid)
    with pytest.raises(RuntimeEventProtocolError, match="expected_seq"):
        codec.decode_line(
            codec.encode(codec.event(seq=1, kind="preflight.completed")),
            expected_seq=0,
        )

    with pytest.raises(RuntimeEventProtocolError, match="RuntimeEvent"):
        codec.encode(object())  # type: ignore[arg-type]
    tuple_attack = codec.event(
        seq=1,
        kind="media.completed",
        artifact_paths=("media/replay.mp4",),
    )
    object.__setattr__(tuple_attack, "artifact_paths", ["media/replay.mp4"])
    with pytest.raises(RuntimeEventProtocolError, match="tuple"):
        codec.encode(tuple_attack)

    with pytest.raises(RuntimeEventProtocolError, match="kind must be a string"):
        codec.decode_line(_line(_payload(kind=7)), expected_seq=1)


def test_emitter_writes_only_to_a_dedicated_fd_and_owns_sequence() -> None:
    codec = _codec()
    read_fd, write_fd = os.pipe()
    try:
        emitter = RuntimeEventEmitter(fd=write_fd, codec=codec)
        first = emitter.emit(kind="preflight.completed")
        second = emitter.emit(kind="simulation.checkpoint", completed_steps=10)
        os.close(write_fd)
        write_fd = -1
        transcript = os.read(read_fd, 64 * 1024)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)

    assert [first.seq, second.seq] == [1, 2]
    assert codec.parse_transcript(transcript) == (first, second)

    for fd in (0, 1, 2, -1, True):
        with pytest.raises(RuntimeEventProtocolError, match="dedicated file descriptor"):
            RuntimeEventEmitter(fd=fd, codec=codec)

    with pytest.raises(RuntimeEventProtocolError, match="RuntimeEventCodec"):
        RuntimeEventEmitter(fd=9, codec=object())  # type: ignore[arg-type]


def test_emitter_retries_interruption_and_handles_partial_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codec = _codec()
    written = bytearray()
    calls = 0

    def partial_write(fd: int, data: bytes) -> int:
        nonlocal calls
        assert fd == 9
        calls += 1
        if calls == 1:
            raise InterruptedError
        count = max(1, len(data) // 2)
        written.extend(data[:count])
        return count

    monkeypatch.setattr(runtime_events.os, "write", partial_write)
    event = RuntimeEventEmitter(fd=9, codec=codec).emit(kind="preflight.completed")

    assert bytes(written) == codec.encode(event)
    assert calls >= 3


@pytest.mark.parametrize("failure", [OSError("closed"), 0])
def test_emitter_poisoned_after_ambiguous_write_failure(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException | int
) -> None:
    def fail_write(fd: int, data: bytes) -> int:
        del fd, data
        if isinstance(failure, BaseException):
            raise failure
        return failure

    monkeypatch.setattr(runtime_events.os, "write", fail_write)
    emitter = RuntimeEventEmitter(fd=9, codec=_codec())
    with pytest.raises(RuntimeEventProtocolError, match="write"):
        emitter.emit(kind="preflight.completed")
    with pytest.raises(RuntimeEventProtocolError, match="failed"):
        emitter.emit(kind="preflight.completed")


def test_emitter_validation_failure_does_not_consume_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written = bytearray()

    def collect(fd: int, data: bytes) -> int:
        assert fd == 9
        written.extend(data)
        return len(data)

    monkeypatch.setattr(runtime_events.os, "write", collect)
    emitter = RuntimeEventEmitter(fd=9, codec=_codec())
    with pytest.raises(RuntimeEventProtocolError, match="artifact_paths is required"):
        emitter.emit(kind="media.completed")

    event = emitter.emit(kind="preflight.completed")
    assert event.seq == 1
    assert bytes(written) == _codec().encode(event)


def test_worker_completed_is_terminal_for_decoder_and_emitter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codec = _codec()
    terminal = codec.event(seq=1, kind="worker.completed")
    after_terminal = codec.event(seq=2, kind="preflight.completed")
    with pytest.raises(RuntimeEventProtocolError, match="final event"):
        codec.parse_transcript(codec.encode(terminal) + codec.encode(after_terminal))

    monkeypatch.setattr(runtime_events.os, "write", lambda fd, data: len(data))
    emitter = RuntimeEventEmitter(fd=9, codec=codec)
    emitter.emit(kind="worker.completed")
    with pytest.raises(RuntimeEventProtocolError, match="already been emitted"):
        emitter.emit(kind="preflight.completed")
