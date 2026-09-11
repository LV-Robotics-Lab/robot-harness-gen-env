"""Protocol fixtures test verification only; they never certify a real Codex run."""

import json
from pathlib import Path

import pytest

from self_improving.harness.codex_mcp_acceptance import (
    CodexMCPInvocation,
    inspect_codex_transcript,
    prepare_codex_mcp_invocation,
    verify_control_capture_ttl,
    verify_transcript_authority,
)


def test_invocation_is_ephemeral_exact_mcp_and_does_not_edit_user_config(tmp_path):
    for name in ("codex", "python", "settings.json"):
        (tmp_path / name).write_text("fixture")
    invocation = prepare_codex_mcp_invocation(
        codex_executable=tmp_path / "codex",
        server_python=tmp_path / "python",
        server_settings=tmp_path / "settings.json",
        server_workdir=tmp_path,
        model="test-model",
        case="normal",
        objective="mouse on table",
    )
    assert type(invocation) is CodexMCPInvocation
    assert invocation.argv[:5] == (
        str(tmp_path / "codex"),
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
    )
    assert "--sandbox" in invocation.argv
    assert "read-only" in invocation.argv
    overrides = [invocation.argv[i + 1] for i, value in enumerate(invocation.argv) if value == "-c"]
    assert "features.shell_tool=false" in overrides
    config = next(value for value in overrides if value.startswith("mcp_servers="))
    assert str(tmp_path / "python") in config
    assert str(tmp_path / "settings.json") in config
    assert "skill.invoke" in invocation.prompt
    assert not (tmp_path / ".codex").exists()


def test_relative_server_settings_are_rejected_before_launch(tmp_path):
    with pytest.raises(ValueError, match="absolute"):
        prepare_codex_mcp_invocation(
            codex_executable=Path("codex"),
            server_python=tmp_path / "python",
            server_settings=tmp_path / "settings.json",
            server_workdir=tmp_path,
            model="test",
            case="normal",
            objective="mouse",
        )


def protocol_fixture(case="normal"):
    from self_improving.harness.codex_mcp_acceptance import NORMAL_TOOLS, REVISED_TOOLS

    names = NORMAL_TOOLS if case == "normal" else REVISED_TOOLS
    events = [
        {"type": "thread.started", "thread_id": "private-session-id"},
        {"type": "turn.started"},
    ]
    wire = []
    for index, name in enumerate(names):
        args = {"workflow_run_id": "one-workflow", "expected_workflow_revision": index}
        result = {"content": [], "structuredContent": {"fixture": index}, "isError": False}
        wire.extend(
            [
                {
                    "direction": "request",
                    "message": {
                        "jsonrpc": "2.0",
                        "id": index,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": args},
                    },
                },
                {
                    "direction": "response",
                    "message": {"jsonrpc": "2.0", "id": index, "result": result},
                },
            ]
        )
        item = {
            "id": f"item-{index}",
            "type": "mcp_tool_call",
            "server": "golden",
            "tool": name,
            "arguments": args,
            "status": "in_progress",
        }
        events.append({"type": "item.started", "item": item})
        events.append(
            {
                "type": "item.completed",
                "item": {
                    **item,
                    "status": "completed",
                    "result": {"content": [], "structured_content": {"fixture": index}},
                },
            }
        )
    resource = {
        "jsonrpc": "2.0",
        "id": 100,
        "method": "resources/read",
        "params": {"uri": "harness://workflows/one-workflow/artifacts/" + "a" * 64},
    }
    wire.extend(
        [
            {"direction": "request", "message": resource},
            {
                "direction": "response",
                "message": {
                    "jsonrpc": "2.0",
                    "id": 100,
                    "result": {"contents": [{"uri": resource["params"]["uri"], "text": "{}"}]},
                },
            },
        ]
    )
    events.extend(
        [
            {
                "type": "item.completed",
                "item": {"id": "final", "type": "agent_message", "text": "SUCCESS is not evidence"},
            },
            {"type": "turn.completed", "usage": {"input_tokens": 1}},
        ]
    )
    return events, wire


def lines(values):
    return b"".join(json.dumps(value).encode() + b"\n" for value in values)


@pytest.mark.parametrize("case", ["normal", "revised"])
def test_protocol_fixture_requires_assistant_calls_and_matching_transport(case):
    events, wire = protocol_fixture(case)
    report = inspect_codex_transcript(lines(events), lines(wire), case=case)
    assert report.authority == "transcript_consistency_only"
    assert report.resource_reads[0]["uri"].endswith("a" * 64)
    assert "private-session-id" not in repr(report)


@pytest.mark.parametrize(
    "mode",
    [
        "client",
        "shell",
        "foreign",
        "missing",
        "forged_final",
        "payload",
        "read_missing",
        "duplicate",
        "unfinished",
        "error",
    ],
)
def test_protocol_counterexamples_cannot_be_claimed_as_codex_acceptance(mode):
    events, wire = protocol_fixture()
    if mode == "client":
        events = []
    elif mode == "shell":
        events.insert(
            2,
            {
                "type": "item.started",
                "item": {"id": "bad", "type": "command_execution", "command": "python harness.py"},
            },
        )
    elif mode == "foreign":
        events[2]["item"]["server"] = "foreign"
    elif mode == "missing":
        events.pop(3)
    elif mode == "forged_final":
        events = events[:2] + events[-2:]
    elif mode == "payload":
        events[3]["item"]["result"]["structured_content"] = {"fixture": 99}
    elif mode == "read_missing":
        wire = wire[:-2]
    elif mode == "duplicate":
        events.insert(4, events[3])
    elif mode == "unfinished":
        events.pop()
    elif mode == "error":
        wire[1]["message"]["result"]["isError"] = True
    with pytest.raises(ValueError):
        inspect_codex_transcript(lines(events), lines(wire), case="normal")


def test_control_ttl_comes_from_trusted_sqlite_cas_not_model_text(tmp_path):
    from tests.self_improving.harness.test_golden_control_v2_runtime import native_case

    case = native_case(tmp_path)
    committed = case.harness.submit_control(case.command)
    result = verify_control_capture_ttl(
        harness=case.harness, request=case.read, control_ref=committed.receipt
    )
    assert len(result) == 1
    assert result[0]["ttl_seconds"] == 60
    assert 0 <= result[0]["commit_margin_seconds"] <= 60
    assert result[0]["authority"] == "observed_restored_final_pose"
    with pytest.raises(ValueError, match="control"):
        verify_control_capture_ttl(harness=case.harness, request=case.read, control_ref=case.native)


def test_real_harness_rejects_foreign_transcript_start(tmp_path):
    from tests.self_improving.harness.test_golden_control_v2_runtime import native_case

    case = native_case(tmp_path)
    events, wire = protocol_fixture()
    inspection = inspect_codex_transcript(lines(events), lines(wire), case="normal")
    with pytest.raises(ValueError):
        verify_transcript_authority(inspection, harness=case.harness, request=case.read)


def test_stdio_witness_records_actual_child_transport(tmp_path):
    import io
    import sys

    from self_improving.harness.codex_mcp_process import record_stdio_transport

    source = io.BytesIO(b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
    destination, audit = io.BytesIO(), io.BytesIO()
    with (tmp_path / "errors").open("wb") as errors:
        code = record_stdio_transport(
            [
                sys.executable,
                "-B",
                "-c",
                "import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())",
            ],
            source=source,
            destination=destination,
            audit=audit,
            errors=errors,
        )
    assert code == 0
    assert destination.getvalue() == source.getvalue()
    frames = [json.loads(line) for line in audit.getvalue().splitlines()]
    assert [frame["direction"] for frame in frames] == ["request", "response"]
    assert [frame["sequence"] for frame in frames] == [0, 1]


def test_runner_rejects_wrapper_without_executing_codex(tmp_path):
    from self_improving.harness.codex_mcp_process import run_codex_mcp_invocation

    for name in ("codex", "python", "settings.json"):
        (tmp_path / name).write_text("#!/bin/sh\nexit 1\n")
    kwargs = dict(
        server_python=tmp_path / "python",
        server_settings=tmp_path / "settings.json",
        server_workdir=tmp_path,
        objective="mouse",
    )
    invocation = prepare_codex_mcp_invocation(
        codex_executable=tmp_path / "codex", model="test", case="normal", **kwargs
    )
    with pytest.raises(ValueError, match="ELF"):
        run_codex_mcp_invocation(
            invocation, output_root=tmp_path / "run", approved_codex_sha256="a" * 64, **kwargs
        )
    assert not (tmp_path / "run").exists()


def test_actual_controlled_sqlite_history_and_resource_deliveries_crosscheck(tmp_path):
    """This smaller external-handler workflow is not a complete normal acceptance case."""
    import base64

    from self_improving.harness.codex_mcp_acceptance import CodexTranscriptInspection
    from self_improving.harness.schemas.workflow import RunCommandRequest
    from tests.self_improving.harness.test_golden_control_v2_runtime import native_case

    case = native_case(tmp_path)
    control = case.harness.submit_control(case.command)
    head = case.harness.read(case.read)
    history = []
    cursor = case.head.receipt_head
    while cursor.schema_version == "harness.workflow_operation_receipt.v2":
        _, raw = case.harness.read_workflow_artifact(case.read, sha256=cursor.sha256)
        receipt = json.loads(raw)
        history.append(receipt)
        from self_improving.harness.schemas.common import ArtifactRef

        cursor = ArtifactRef.model_validate(receipt["predecessor_receipt"])
    history.reverse()
    initial = head.model_dump(mode="json")
    _, start_raw = case.harness.read_workflow_artifact(case.read, sha256=cursor.sha256)
    start = json.loads(start_raw)
    initial.update({key: start[key] for key in ("state_ref", "state_sha256", "started_at")})
    initial.update(
        revision=0, turn_seq=0, receipt_head=cursor.model_dump(mode="json"), child_runs=[]
    )
    calls = [
        {
            "tool": "harness_workflow_create_v1",
            "arguments": {},
            "result": {"structured_content": initial},
        }
    ]
    for receipt in history:
        _, raw = case.harness.read_workflow_artifact(case.read, sha256=receipt["command"]["sha256"])
        command = RunCommandRequest.model_validate_json(raw)
        operation = case.harness.submit(command)
        calls.append(
            {
                "tool": "genesis_observe_v3_0_0",
                "arguments": command.model_dump(mode="json"),
                "result": {"structured_content": operation.model_dump(mode="json")},
            }
        )
    calls.append(
        {
            "tool": "harness_advisory_record_v2",
            "arguments": case.command.model_dump(mode="json"),
            "result": {"structured_content": control.model_dump(mode="json")},
        }
    )
    reads = []
    for access_ref in case.command.resource_access_receipts:
        _, audit = case.harness.read_resource_access_receipt(case.read, sha256=access_ref.sha256)
        delivery = json.loads(audit)
        _, raw = case.harness.read_workflow_artifact(
            case.read, sha256=delivery["resource"]["sha256"]
        )
        resource = {"uri": delivery["resource_uri"]}
        resource.update(
            {"text": raw.decode()}
            if delivery["representation"] == "text_utf8"
            else {"blob": base64.b64encode(raw).decode()}
        )
        reads.append(
            {
                "uri": resource["uri"],
                "after_calls": 3,
                "result": {
                    "contents": [
                        resource,
                        {
                            "uri": f"harness://workflows/{head.workflow_run_id}/resource-access/{access_ref.sha256}",
                            "text": audit.decode(),
                        },
                    ]
                },
            }
        )
    inspection = CodexTranscriptInspection(tuple(calls), tuple(reads), "a" * 64, "b" * 64)
    result = verify_transcript_authority(inspection, harness=case.harness, request=case.read)
    assert result["actual_codex_execution_certified"] is False
    assert len(result["ttl_margins"]) == 1
    assert result["workflow_revision"] == 3
    reads[0]["after_calls"] = 4
    with pytest.raises(ValueError, match="not read before"):
        verify_transcript_authority(inspection, harness=case.harness, request=case.read)


@pytest.mark.parametrize(
    "mode",
    [
        "invalid_json",
        "non_object",
        "case",
        "turn",
        "item",
        "tool",
        "rpc",
        "method",
        "notification",
        "request_duplicate",
        "unpaired",
        "rpc_error",
        "direction",
        "extra_client",
    ],
)
def test_ambiguous_or_unrelated_wire_evidence_is_rejected(mode):
    events, wire = protocol_fixture()
    case = "normal"
    if mode == "invalid_json":
        with pytest.raises(ValueError):
            inspect_codex_transcript(b"{", lines(wire), case=case)
        return
    if mode == "non_object":
        events = [1]
    elif mode == "case":
        case = "other"
    elif mode == "turn":
        events.insert(2, {"type": "turn.failed"})
    elif mode == "item":
        events[2]["item"]["type"] = "file_change"
    elif mode == "tool":
        events[2]["item"]["tool"] = "skill.invoke"
    elif mode == "rpc":
        wire[0]["message"]["jsonrpc"] = "1.0"
    elif mode == "method":
        wire[0]["message"]["method"] = "generic/invoke"
    elif mode == "notification":
        wire[0]["message"].pop("id")
    elif mode == "request_duplicate":
        wire.insert(1, wire[0])
    elif mode == "unpaired":
        wire[1]["message"]["id"] = 987
    elif mode == "rpc_error":
        wire[1]["message"]["error"] = {"code": -1}
    elif mode == "direction":
        wire[0]["direction"] = "unknown"
    elif mode == "extra_client":
        wire.extend(
            [
                {
                    "direction": "request",
                    "message": {
                        "jsonrpc": "2.0",
                        "id": 99,
                        "method": "tools/call",
                        "params": {"name": "skill.invoke"},
                    },
                },
                {"direction": "response", "message": {"jsonrpc": "2.0", "id": 99, "result": {}}},
            ]
        )
    with pytest.raises(ValueError):
        inspect_codex_transcript(lines(events), lines(wire), case=case)


def test_revised_case_retains_the_actual_failed_validation_before_diagnosis():
    events, wire = protocol_fixture("revised")
    events[11]["item"]["status"] = "failed"
    wire[9]["message"]["result"]["isError"] = True
    inspection = inspect_codex_transcript(lines(events), lines(wire), case="revised")
    assert inspection.calls[4]["status"] == "failed"


def test_arbitrary_recording_cannot_certify_a_programmatic_client():
    from self_improving.harness.codex_mcp_process import verify_codex_process_recording

    with pytest.raises(TypeError, match="fixed-runner"):
        verify_codex_process_recording(object(), harness=None, request=None)


@pytest.mark.parametrize("mode", ["identity", "not_codex", "settings_drift"])
def test_unapproved_or_non_codex_executable_cannot_launch_acceptance(tmp_path, mode):
    import hashlib

    from self_improving.harness.codex_mcp_process import run_codex_mcp_invocation

    executable = Path("/usr/bin/true")
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    kwargs = dict(
        server_python=executable,
        server_settings=settings,
        server_workdir=tmp_path,
        objective="mouse",
    )
    invocation = prepare_codex_mcp_invocation(
        codex_executable=executable, model="test", case="normal", **kwargs
    )
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    if mode == "settings_drift":
        settings.write_text('{"changed":true}')
    with pytest.raises(ValueError):
        run_codex_mcp_invocation(
            invocation,
            output_root=tmp_path / "run",
            approved_codex_sha256="a" * 64 if mode == "identity" else digest,
            **kwargs,
        )
    assert not (tmp_path / "run").exists()
