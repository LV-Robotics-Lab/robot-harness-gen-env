"""Assigned backend protocol fixtures do not certify real model execution."""

import io
import json
from datetime import UTC, datetime, timedelta

import pytest

from self_improving.harness.codex_backend import AssignedMCPInput, CodexAssignment
from tests.self_improving.harness.test_golden_control_v2_runtime import native_case


def test_assigned_relay_rejects_start_and_foreign_workflow_before_forwarding(tmp_path):
    case = native_case(tmp_path)
    assignment = CodexAssignment(
        snapshot=case.head,
        image_path=tmp_path / "image.png",
        objective="mouse on table",
        phase="normal",
        allowed_actions=("image2env_preview_status_v1",),
        call_budget=2,
        deadline=datetime.now(UTC) + timedelta(minutes=1),
    )
    for name, workflow in [
        ("image2env_preview_start_v1", str(case.head.workflow_run_id)),
        ("image2env_preview_status_v1", "foreign"),
    ]:
        message = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": {"workflow_id": workflow}},
        }
        stream = AssignedMCPInput(io.BytesIO(json.dumps(message).encode() + b"\n"), assignment)
        with pytest.raises(ValueError):
            stream.readline()


@pytest.fixture
def assignment(tmp_path):
    from PIL import Image

    case = native_case(tmp_path)
    path = tmp_path / "mouse.png"
    Image.new("RGB", (2, 2)).save(path)
    return CodexAssignment(
        snapshot=case.head,
        image_path=path,
        objective="mouse on table",
        phase="normal",
        allowed_actions=("image2env_preview_status_v1", "image2env_preview_asset_lookup_v1"),
        call_budget=2,
        deadline=datetime.now(UTC) + timedelta(minutes=1),
    )


def test_assigned_guard_budget_resources_and_bytes(assignment):
    workflow = str(assignment.snapshot.workflow_run_id)

    def line(method, params):
        return (
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
            + b"\n"
        )

    status = line(
        "tools/call",
        {"name": "image2env_preview_status_v1", "arguments": {"workflow_id": workflow}},
    )
    stream = AssignedMCPInput(io.BytesIO(status * 3), assignment)
    assert stream.readline() == status
    assert stream.readline() == status
    with pytest.raises(ValueError, match="budget"):
        stream.readline()
    for method, params in [
        ("resources/read", {"uri": "harness://workflows/foreign/artifacts/a"}),
        ("unknown", {}),
    ]:
        with pytest.raises(ValueError):
            AssignedMCPInput(io.BytesIO(line(method, params)), assignment).readline()
    good = line("resources/read", {"uri": f"harness://workflows/{workflow}"})
    assert AssignedMCPInput(io.BytesIO(good), assignment).readline() == good
    expired = assignment.model_copy(update={"deadline": datetime.now(UTC) - timedelta(seconds=1)})
    with pytest.raises(ValueError, match="deadline"):
        AssignedMCPInput(io.BytesIO(status), expired).readline()


def test_assigned_transcript_matches_actual_wire_and_rejects_shell_or_foreign(assignment):
    from self_improving.harness.codex_backend import inspect_assigned_transcript

    args = {"workflow_id": str(assignment.snapshot.workflow_run_id)}
    result = {
        "content": [],
        "structured_content": {"snapshot": assignment.snapshot.model_dump(mode="json")},
    }
    item = {
        "id": "call",
        "type": "mcp_tool_call",
        "server": "golden",
        "tool": "image2env_preview_status_v1",
        "arguments": args,
        "status": "completed",
        "result": result,
    }
    events = [
        {"type": "thread.started"},
        {"type": "turn.started"},
        {"type": "item.started", "item": item},
        {"type": "item.completed", "item": item},
        {"type": "turn.completed"},
    ]
    wire = [
        {
            "direction": "request",
            "monotonic_ns": 0,
            "message": {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": item["tool"], "arguments": args},
            },
        },
        {
            "direction": "response",
            "monotonic_ns": 1000000000,
            "message": {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"content": [], "structuredContent": result["structured_content"]},
            },
        },
    ]

    def lines(values):
        return b"\n".join(json.dumps(value).encode() for value in values)

    output = inspect_assigned_transcript(lines(events), lines(wire), assignment)
    assert output["calls"][0]["elapsed_seconds"] == 1.0
    assert output["business_state_authority"] is False
    wire[1]["message"]["result"]["structuredContent"]["snapshot"] = {}
    with pytest.raises(ValueError, match="assigned snapshot"):
        inspect_assigned_transcript(lines(events), lines(wire), assignment)
    item["type"] = "command_execution"
    with pytest.raises(ValueError, match="only assigned"):
        inspect_assigned_transcript(lines(events), lines(wire), assignment)


@pytest.mark.parametrize(
    "field,value",
    [
        ("allowed_actions", ("image2env_preview_start_v1",)),
        ("allowed_actions", ("image2env_preview_status_v1", "skill.invoke")),
        ("allowed_actions", ("image2env_preview_status_v1",) * 2),
        ("image_path", "relative.png"),
        ("deadline", "2026-09-11T12:00:00"),
        ("call_budget", True),
    ],
)
def test_public_assignment_rejects_ambiguous_scope(assignment, field, value):
    data = assignment.model_dump(mode="json")
    data[field] = value
    with pytest.raises(ValueError):
        CodexAssignment.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    "mode,expected",
    [("exit", "exit_failure"), ("invalid", "invalid_transcript"), ("timeout", "timeout")],
)
def test_real_external_fixture_process_failure_is_structured(assignment, tmp_path, mode, expected):
    import hashlib
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    from self_improving.harness.codex_backend import CodexBackend

    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("external ELF fixture requires C compiler")
    source = tmp_path / "fixture.c"
    body = {"exit": "return 17;", "invalid": "return 0;", "timeout": "sleep(10); return 0;"}[mode]
    source.write_text("#include <unistd.h>\nint main(){" + body + "}")
    executable = tmp_path / "fixture-elf"
    subprocess.run([compiler, str(source), "-o", str(executable)], check=True)
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    backend = CodexBackend(
        executable=executable,
        approved_executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        server_python=Path(sys.executable),
        server_settings=settings,
        server_workdir=tmp_path,
        model="fixture-not-codex",
    )
    if mode == "timeout":
        assignment = assignment.model_copy(
            update={"deadline": datetime.now(UTC) + timedelta(seconds=0.1)}
        )
    outcome = backend.run(assignment, output_root=tmp_path / "record")
    assert outcome.status == expected
    assert outcome.decision_evidence is None
    assert outcome.transcript_sha256
