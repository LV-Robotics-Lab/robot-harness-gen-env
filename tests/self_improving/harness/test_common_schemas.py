from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from self_improving.harness.schemas.base import derive_mcp_tool_name
from self_improving.harness.schemas.common import (
    ArtifactRef,
    Blocker,
    Event,
    Invocation,
    RunState,
    RunStatus,
    SkillDescriptor,
    SkillQualification,
)

SHA_A = "a" * 64
SHA_B = "b" * 64


def test_artifact_ref_is_strict_frozen_and_keeps_required_null(artifact_payload) -> None:
    artifact = ArtifactRef.model_validate(artifact_payload(None))

    assert artifact.model_dump()["schema_version"] is None
    with pytest.raises(ValidationError, match="frozen"):
        artifact.name = "changed"  # type: ignore[misc]

    unknown = artifact_payload(None)
    unknown["unexpected"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ArtifactRef.model_validate(unknown)

    for invalid_bytes in ("123", True, -1):
        invalid = artifact_payload(None)
        invalid["bytes"] = invalid_bytes
        with pytest.raises(ValidationError):
            ArtifactRef.model_validate(invalid)

    missing_nullable = artifact_payload(None)
    del missing_nullable["schema_version"]
    with pytest.raises(ValidationError, match="Field required"):
        ArtifactRef.model_validate(missing_nullable)


def test_blocker_preserves_unknown_codes_but_unknown_records_are_strict(
    blocker_payload,
) -> None:
    payload = blocker_payload(code="FUTURE_GATE_REJECTED")
    blocker = Blocker.model_validate(payload)
    assert blocker.code == "FUTURE_GATE_REJECTED"
    assert blocker.unknowns[0].field == "contact"

    bad_code = deepcopy(payload)
    bad_code["code"] = "future-code"
    with pytest.raises(ValidationError):
        Blocker.model_validate(bad_code)

    bad_unknown = deepcopy(payload)
    bad_unknown["unknowns"][0]["extra"] = "not allowed"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Blocker.model_validate(bad_unknown)

    bad_retry = deepcopy(payload)
    bad_retry["retryable"] = 1
    with pytest.raises(ValidationError):
        Blocker.model_validate(bad_retry)


def test_event_requires_explicit_nullable_status_and_aware_timestamp(
    artifact_payload,
) -> None:
    payload = {
        "seq": 1,
        "timestamp": "2026-08-17T10:00:00Z",
        "stage": "preflight",
        "attempt": 0,
        "from_status": None,
        "to_status": "running",
        "artifact_refs": [artifact_payload(None)],
    }
    event = Event.model_validate(payload)
    assert event.from_status is None
    assert event.to_status == RunStatus.RUNNING

    missing = deepcopy(payload)
    del missing["from_status"]
    with pytest.raises(ValidationError, match="Field required"):
        Event.model_validate(missing)

    naive = deepcopy(payload)
    naive["timestamp"] = "2026-08-17T10:00:00"
    with pytest.raises(ValidationError, match="timezone"):
        Event.model_validate(naive)


def test_skill_qualification_and_descriptor_enforce_exact_identity(
    artifact_payload,
) -> None:
    qualification = SkillQualification(
        skill_ref="text2env.compile@1.0.0",
        status="pass",
        deterministic_case_id="compile-fixture-42",
        regression_command="pytest -q tests/self_improving/harness",
        report_sha256=SHA_A,
    )
    assert qualification.status == "pass"

    with pytest.raises(ValidationError):
        SkillQualification.model_validate(
            {**qualification.model_dump(mode="json"), "status": "fail"}
        )
    with pytest.raises(ValidationError):
        SkillQualification.model_validate(
            {**qualification.model_dump(mode="json"), "skill_ref": "text2env.compile@latest"}
        )

    descriptor_payload = {
        "skill_id": "text2env.compile",
        "version": "1.0.0",
        "mcp_tool_name": "text2env_compile_v1_0_0",
        "input_schema": "harness.text2env_compile_input.v1",
        "output_schema": "harness.text2env_compile_output.v1",
        "implementation_name": "text2env_compile_handler",
        "implementation_version": "scene_gen.stage5_solver.v3",
        "implementation_sha256": SHA_B,
        "deterministic": True,
        "max_attempts": 1,
        "qualification_artifact": artifact_payload(
            "harness.skill_qualification.v1",
            name="qualification",
        ),
    }
    descriptor = SkillDescriptor.model_validate(descriptor_payload)
    assert descriptor.mcp_tool_name == derive_mcp_tool_name(
        descriptor.skill_id,
        descriptor.version,
    )

    wrong_name = deepcopy(descriptor_payload)
    wrong_name["mcp_tool_name"] = "text2env_compile_v1_0_1"
    with pytest.raises(ValidationError, match="mcp_tool_name must be"):
        SkillDescriptor.model_validate(wrong_name)

    wrong_qualification = deepcopy(descriptor_payload)
    wrong_qualification["qualification_artifact"]["schema_version"] = "harness.other.v1"
    with pytest.raises(ValidationError, match="qualification_artifact.schema_version"):
        SkillDescriptor.model_validate(wrong_qualification)

    for invalid_version in ("01.0.0", "1.0", "1.0.0-rc1", "1.0.0+build"):
        invalid = deepcopy(descriptor_payload)
        invalid["version"] = invalid_version
        with pytest.raises(ValidationError):
            SkillDescriptor.model_validate(invalid)

    for field, value in (
        ("skill_id", "Text2Env.compile"),
        ("deterministic", False),
        ("max_attempts", 0),
        ("input_schema", "not-a-schema"),
    ):
        invalid = deepcopy(descriptor_payload)
        invalid[field] = value
        with pytest.raises(ValidationError):
            SkillDescriptor.model_validate(invalid)


def test_invocation_requires_sorted_unique_dependencies(run_id) -> None:
    payload = {
        "run_id": str(run_id),
        "skill_id": "text2env.compile",
        "skill_version": "1.0.0",
        "effective_parameters": {"request": "Place a can on a table.", "seed": 42},
        "dependencies": [
            {"name": "pydantic", "version": "2.12.4", "sha256": SHA_A},
            {"name": "scene_gen", "version": "stage5.v3", "sha256": SHA_B},
        ],
        "max_attempts": 1,
        "invocation_digest": SHA_A,
    }
    invocation = Invocation.model_validate(payload)
    assert [item.name for item in invocation.dependencies] == ["pydantic", "scene_gen"]

    unsorted = deepcopy(payload)
    unsorted["dependencies"].reverse()
    with pytest.raises(ValidationError, match="sorted by name"):
        Invocation.model_validate(unsorted)

    duplicate = deepcopy(payload)
    duplicate["dependencies"][1]["name"] = "pydantic"
    with pytest.raises(ValidationError, match="must be unique"):
        Invocation.model_validate(duplicate)

    wrong_uuid_version = deepcopy(payload)
    wrong_uuid_version["run_id"] = "12345678-1234-1234-9234-123456789abc"
    with pytest.raises(ValidationError, match="UUID version 4"):
        Invocation.model_validate(wrong_uuid_version)


def _event(
    seq: int,
    timestamp: datetime,
    attempt: int,
    from_status: str | None,
    to_status: str,
) -> dict[str, Any]:
    return {
        "seq": seq,
        "timestamp": timestamp,
        "stage": "invoke" if attempt else "preflight",
        "attempt": attempt,
        "from_status": from_status,
        "to_status": to_status,
        "artifact_refs": [],
    }


def _run_state_payload(
    run_id,
    started_at: datetime,
    ended_at: datetime,
    blocker_payload,
    *,
    status: str = "succeeded",
) -> dict[str, Any]:
    terminal_blocker = blocker_payload() if status in {"blocked", "failed"} else None
    terminal_output = {"result": "typed"} if status == "succeeded" else None
    attempt = 0 if status == "blocked" else 1
    max_attempts = 0 if attempt == 0 else 1
    digest = None if attempt == 0 else SHA_A
    events = [
        _event(1, started_at, attempt, None, "running"),
        _event(2, ended_at, attempt, "running", status),
    ]
    return {
        "run_id": str(run_id),
        "invocation_digest": digest,
        "skill_id": "text2env.compile",
        "skill_version": "1.0.0",
        "status": status,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "started_at": started_at,
        "ended_at": ended_at,
        "events": events,
        "artifacts": [],
        "output": terminal_output,
        "blocker": terminal_blocker,
    }


def test_run_state_accepts_running_success_blocked_failed_and_retry(
    run_id,
    started_at,
    ended_at,
    blocker_payload,
) -> None:
    running_payload = _run_state_payload(
        run_id,
        started_at,
        ended_at,
        blocker_payload,
    )
    running_payload.update(
        {
            "status": "running",
            "ended_at": None,
            "events": [_event(1, started_at, 1, None, "running")],
            "output": None,
        }
    )
    assert RunState.model_validate(running_payload).status == RunStatus.RUNNING

    assert RunState.model_validate(
        _run_state_payload(run_id, started_at, ended_at, blocker_payload)
    ).status == RunStatus.SUCCEEDED
    assert RunState.model_validate(
        _run_state_payload(
            run_id,
            started_at,
            ended_at,
            blocker_payload,
            status="blocked",
        )
    ).attempt == 0
    assert RunState.model_validate(
        _run_state_payload(
            run_id,
            started_at,
            ended_at,
            blocker_payload,
            status="failed",
        )
    ).blocker is not None

    retry = _run_state_payload(run_id, started_at, ended_at, blocker_payload)
    retry["attempt"] = 2
    retry["max_attempts"] = 2
    retry["events"] = [
        _event(1, started_at, 1, None, "running"),
        _event(2, started_at + timedelta(seconds=10), 1, "running", "running"),
        _event(3, started_at + timedelta(seconds=20), 2, "running", "running"),
        _event(4, ended_at, 2, "running", "succeeded"),
    ]
    assert RunState.model_validate(retry).attempt == 2


def test_run_state_rejects_broken_event_history(
    run_id,
    started_at,
    ended_at,
    blocker_payload,
) -> None:
    base = _run_state_payload(run_id, started_at, ended_at, blocker_payload)

    cases: list[tuple[str, Any, str]] = [
        ("events.1.seq", 3, "seq values"),
        ("events.0.from_status", "running", "null -> running"),
        ("events.0.to_status", "blocked", "null -> running"),
        ("started_at", started_at - timedelta(seconds=1), "started_at"),
        ("events.1.from_status", "blocked", "continuous chain"),
        ("events.1.timestamp", started_at - timedelta(seconds=1), "nondecreasing"),
        ("events.1.attempt", 3, "increment by at most 1"),
        ("status", "failed", "final event status"),
        ("attempt", 2, "final event attempt"),
        ("max_attempts", 0, "preflight attempt 0"),
        ("invocation_digest", None, "execution attempts require"),
        ("ended_at", None, "terminal state requires"),
        ("ended_at", started_at - timedelta(seconds=1), "cannot precede"),
        ("ended_at", ended_at + timedelta(seconds=1), "final event timestamp"),
        ("blocker", blocker_payload(), "succeeded state requires"),
        ("output", None, "succeeded state requires"),
    ]
    for path, value, message in cases:
        candidate = deepcopy(base)
        target: Any = candidate
        parts = path.split(".")
        for part in parts[:-1]:
            target = target[int(part)] if part.isdigit() else target[part]
        target[parts[-1]] = value
        with pytest.raises(ValidationError, match=message):
            RunState.model_validate(candidate)

    after_terminal = deepcopy(base)
    after_terminal["events"].insert(
        1,
        _event(2, started_at + timedelta(seconds=1), 1, "running", "blocked"),
    )
    after_terminal["events"][2]["seq"] = 3
    after_terminal["events"][2]["from_status"] = "blocked"
    with pytest.raises(ValidationError, match="cannot follow"):
        RunState.model_validate(after_terminal)

    decreasing = deepcopy(base)
    decreasing["attempt"] = 1
    decreasing["max_attempts"] = 2
    decreasing["events"] = [
        _event(1, started_at, 2, None, "running"),
        _event(2, ended_at, 1, "running", "succeeded"),
    ]
    with pytest.raises(ValidationError, match="nondecreasing"):
        RunState.model_validate(decreasing)


def test_run_state_rejects_invalid_attempt_and_terminal_payloads(
    run_id,
    started_at,
    ended_at,
    blocker_payload,
) -> None:
    preflight = _run_state_payload(
        run_id,
        started_at,
        ended_at,
        blocker_payload,
        status="blocked",
    )
    preflight["invocation_digest"] = SHA_A
    with pytest.raises(ValidationError, match="attempt 0 cannot have"):
        RunState.model_validate(preflight)

    over_limit = _run_state_payload(run_id, started_at, ended_at, blocker_payload)
    over_limit["attempt"] = 2
    over_limit["events"][0]["attempt"] = 2
    over_limit["events"][1]["attempt"] = 2
    with pytest.raises(ValidationError, match="cannot exceed"):
        RunState.model_validate(over_limit)

    running = _run_state_payload(run_id, started_at, ended_at, blocker_payload)
    running.update(
        {
            "status": "running",
            "events": [_event(1, started_at, 1, None, "running")],
            "ended_at": ended_at,
            "output": None,
        }
    )
    with pytest.raises(ValidationError, match="running state requires"):
        RunState.model_validate(running)

    for status, field, value in (
        ("blocked", "blocker", None),
        ("failed", "output", {"unexpected": True}),
    ):
        candidate = _run_state_payload(
            run_id,
            started_at,
            ended_at,
            blocker_payload,
            status=status,
        )
        candidate[field] = value
        with pytest.raises(ValidationError, match="require a blocker and null output"):
            RunState.model_validate(candidate)
