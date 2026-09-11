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
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": json.dumps(
                    {
                        "workflow_id": args["workflow_id"],
                        "decision": "phase_complete",
                        "reason": "phase done",
                    }
                ),
            },
        },
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
    assert output["terminal_decision"]["decision"] == "phase_complete"
    final = events[-2]["item"]["text"]
    for text in [
        "The task succeeded!",
        json.dumps({"workflow_id": args["workflow_id"], "decision": "success", "reason": "ok"}),
        json.dumps({"workflow_id": "foreign", "decision": "unsupported", "reason": "no"}),
        json.dumps({"workflow_id": args["workflow_id"], "decision": "unsupported", "reason": " "}),
    ]:
        events[-2]["item"]["text"] = text
        with pytest.raises(ValueError):
            inspect_assigned_transcript(lines(events), lines(wire), assignment)
    events[-2]["item"]["text"] = json.dumps(
        {"workflow_id": args["workflow_id"], "decision": "unsupported", "reason": "Only mouse"}
    )
    assert (
        inspect_assigned_transcript(lines(events), lines(wire), assignment)["terminal_decision"][
            "decision"
        ]
        == "unsupported"
    )
    with pytest.raises(ValueError, match="terminal decision"):
        inspect_assigned_transcript(lines(events[:-2] + events[-1:]), lines(wire), assignment)
    events[-2]["item"]["text"] = final
    wire[1]["message"]["result"]["structuredContent"]["snapshot"] = {}
    with pytest.raises(ValueError, match="assigned snapshot"):
        inspect_assigned_transcript(lines(events), lines(wire), assignment)
    item["type"] = "command_execution"
    with pytest.raises(ValueError, match="only assigned"):
        inspect_assigned_transcript(lines(events), lines(wire), assignment)


@pytest.mark.parametrize(
    "variation", ["valid", "foreign_uri", "payload_drift", "wrong_server", "reordered"]
)
def test_codex_01534_resource_call_maps_to_wire_read_without_action_budget(assignment, variation):
    """Small payload preserving observed attempt06's Codex 0.153.4 event shape."""
    from self_improving.harness.codex_backend import inspect_assigned_transcript

    assignment = assignment.model_copy(update={"call_budget": 1})
    workflow = str(assignment.snapshot.workflow_run_id)
    uri = f"harness://workflows/{workflow}/artifacts/" + "a" * 64
    contents = [{"uri": uri, "mimeType": "text/plain", "text": "small public fixture"}]
    status = {
        "id": "status",
        "type": "mcp_tool_call",
        "server": "golden",
        "tool": "image2env_preview_status_v1",
        "arguments": {"workflow_id": workflow},
        "status": "completed",
        "result": {
            "content": [],
            "structured_content": {"snapshot": assignment.snapshot.model_dump(mode="json")},
        },
    }
    resource = {
        "id": "read",
        "type": "mcp_tool_call",
        "server": "golden",
        "tool": "read_mcp_resource",
        "arguments": {"server": "golden", "uri": uri},
        "status": "completed",
        "result": {
            "structured_content": None,
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {"server": "golden", "uri": uri, "contents": contents},
                        separators=(",", ":"),
                    ),
                }
            ],
        },
    }
    if variation == "foreign_uri":
        resource["arguments"]["uri"] = "harness://workflows/foreign/artifacts/a"
    if variation == "wrong_server":
        resource["arguments"]["server"] = "other"
    if variation == "payload_drift":
        resource["result"]["content"][0]["text"] = "{}"
    items = [status, resource] if variation != "reordered" else [resource, status]
    events = [{"type": "thread.started"}, {"type": "turn.started"}]
    for item in items:
        events.extend(
            [{"type": "item.started", "item": item}, {"type": "item.completed", "item": item}]
        )
    events.extend(
        [
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": json.dumps(
                        {
                            "workflow_id": workflow,
                            "decision": "phase_complete",
                            "reason": "phase done",
                        }
                    ),
                },
            },
            {"type": "turn.completed"},
        ]
    )
    messages = [
        (
            "request",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": status["tool"], "arguments": status["arguments"]},
            },
        ),
        (
            "response",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "content": [],
                    "structuredContent": status["result"]["structured_content"],
                },
            },
        ),
        (
            "request",
            {"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": uri}},
        ),
        ("response", {"jsonrpc": "2.0", "id": 2, "result": {"contents": contents}}),
    ]
    frames = [
        {"direction": direction, "monotonic_ns": index * 1000000, "message": message}
        for index, (direction, message) in enumerate(messages)
    ]
    transcript = b"\n".join(json.dumps(item).encode() for item in events)
    wire = b"\n".join(json.dumps(item).encode() for item in frames)
    if variation == "valid":
        result = inspect_assigned_transcript(transcript, wire, assignment)
        assert len(result["calls"]) == 1
        assert result["resource_reads"][0]["uri"] == uri
    else:
        with pytest.raises(ValueError):
            inspect_assigned_transcript(transcript, wire, assignment)


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


def test_diagnosis_prompt_requires_new_head_reads_after_advisory(assignment, tmp_path):
    import hashlib
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    from self_improving.harness.codex_backend import CodexBackend

    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("external ELF fixture requires C compiler")
    source = tmp_path / "prompt-fixture.c"
    source.write_text(
        "#include <stdio.h>\n"
        'int main(void){FILE *f=fopen("prompt.txt","wb"); int c; '
        "while((c=fgetc(stdin))!=EOF) fputc(c,f); fclose(f); return 17;}"
    )
    executable = tmp_path / "prompt-fixture-elf"
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
    diagnosis = assignment.model_copy(
        update={
            "phase": "diagnosis",
            "allowed_actions": (
                "image2env_preview_status_v1",
                "harness_advisory_record_v2",
                "harness_prompt_revision_record_v3",
            ),
            "call_budget": 8,
        }
    )

    result = backend.run(diagnosis, output_root=tmp_path / "record")

    assert result.status == "exit_failure"
    prompt = (tmp_path / "record/prompt.txt").read_text()
    required_in_order = (
        "Diagnosis is a two-head sequence.",
        "At the pre-advisory current head, first call image2env_preview_status_v1, then read "
        "the current effective prompt, preserved validation failure, fresh observation, "
        "diagnostics, and initial replay evidence before calling harness_advisory_record_v2.",
        "The advisory commit advances the Golden head.",
        "After harness_advisory_record_v2 commits, call image2env_preview_status_v1 again and "
        "use its post-advisory current head.",
        "At that post-advisory head, re-read every artifact "
        "harness_prompt_revision_record_v3 will reference: the current source prompt, new "
        "advisory receipt, preserved failure receipts, and predecessor environment.",
        "Use only the new post-advisory resource-access receipts from those reads.",
        "Pre-advisory resource-access receipts are invalid for "
        "harness_prompt_revision_record_v3; never reuse them or rely on session memory.",
        "Only after those reads call harness_prompt_revision_record_v3.",
    )
    positions = [prompt.index(fragment) for fragment in required_in_order]
    assert positions == sorted(positions)


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
    source.write_text(
        "#include <unistd.h>\n#include <stdio.h>\nint main(int argc,char **argv){"
        'FILE *f=fopen("argv.txt","w"); for(int i=0;i<argc;i++) fprintf(f,"%s\\n",argv[i]);'
        "fclose(f);" + body + "}"
    )
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
    argv = (tmp_path / "record/argv.txt").read_text().splitlines()
    schema_path = Path(argv[argv.index("--output-schema") + 1])
    schema = json.loads(schema_path.read_bytes())
    assert schema_path == tmp_path / "record/terminal-decision.schema.json"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["decision"]["enum"] == ["phase_complete", "unsupported"]
    assert set(schema["required"]) == {"workflow_id", "decision", "reason"}
