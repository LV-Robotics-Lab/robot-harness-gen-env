"""Opt-in smoke for the operator-pinned Codex ELF and an exact read-only MCP tool."""

import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image

import self_improving.harness.codex_backend as codex_backend
from tests.self_improving.harness.test_golden_control_v2_runtime import native_case

_CODEX = Path("/home/jingxiang/.local/libexec/codex-real")
_CODEX_SHA256 = "56ef98ab4032d317ab26e9b5e5a175650717351edb16ed9cde0cb6d1734d62da"

pytestmark = pytest.mark.skipif(
    os.environ.get("HARNESS_RUN_REAL_CODEX_SMOKE") != "1",
    reason="requires explicit operator opt-in for a real Codex call",
)


def test_pinned_codex_calls_assigned_exact_status_without_business_state_write(
    tmp_path, monkeypatch
):
    assert _CODEX.is_file()
    assert hashlib.sha256(_CODEX.read_bytes()).hexdigest() == _CODEX_SHA256
    image_path = tmp_path / "mouse.png"
    Image.new("RGB", (2, 2), "white").save(image_path)
    snapshot = native_case(tmp_path / "fixture").head
    assignment = codex_backend.CodexAssignment(
        snapshot=snapshot,
        image_path=image_path,
        objective="Read the assigned status only and end this phase without business execution.",
        phase="normal",
        allowed_actions=(codex_backend.STATUS,),
        call_budget=1,
        deadline=datetime.now(UTC) + timedelta(minutes=5),
    )
    project_root = Path(__file__).resolve().parents[3]
    settings = tmp_path / "project-root.txt"
    settings.write_text(str(project_root), encoding="utf-8")
    (tmp_path / "approval_smoke_relay.py").write_text(_RELAY, encoding="utf-8")
    (tmp_path / "approval_smoke_server.py").write_text(_SERVER, encoding="utf-8")
    monkeypatch.setattr(codex_backend, "__name__", "approval_smoke_relay")
    backend = codex_backend.CodexBackend(
        executable=_CODEX,
        approved_executable_sha256=_CODEX_SHA256,
        server_python=Path(sys.executable),
        server_settings=settings,
        server_workdir=tmp_path,
        model="gpt-6-astra",
    )

    result = backend.run(assignment, output_root=tmp_path / "evidence")

    assert result.status == "completed", (
        result,
        (result.evidence_root / "codex.stderr").read_text(),
    )
    assert result.exit_code == 0
    assert [call["tool"] for call in result.decision_evidence["calls"]] == [codex_backend.STATUS]
    assert result.decision_evidence["terminal_decision"]["decision"] == "phase_complete"
    assert json.loads((result.evidence_root / "status-called.json").read_bytes()) == {
        "tool": codex_backend.STATUS,
        "workflow_id": str(snapshot.workflow_run_id),
    }


_RELAY = r"""
import os
import sys
from pathlib import Path

if len(sys.argv) != 7 or sys.argv[1] != "--relay":
    raise SystemExit(2)
assignment_path, python, settings, workdir, output = map(Path, sys.argv[2:])
sys.path.insert(0, settings.read_text(encoding="utf-8").strip())
from self_improving.harness.codex_backend import AssignedMCPInput, CodexAssignment
from self_improving.harness.codex_mcp_process import record_stdio_transport

assignment = CodexAssignment.model_validate_json(assignment_path.read_bytes())
os.chdir(workdir)
with (
    (output / "transport.jsonl").open("xb") as audit,
    (output / "server.stderr").open("xb") as errors,
):
    raise SystemExit(
        record_stdio_transport(
            [
                str(python),
                "-B",
                str(workdir / "approval_smoke_server.py"),
                str(assignment_path),
                str(output / "status-called.json"),
            ],
            source=AssignedMCPInput(sys.stdin.buffer, assignment),
            destination=sys.stdout.buffer,
            audit=audit,
            errors=errors,
        )
    )
"""


_SERVER = r"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import MCPError, types
from mcp.server import Server
from mcp.server.stdio import stdio_server

assignment = json.loads(Path(sys.argv[1]).read_bytes())
snapshot = assignment["snapshot"]
workflow_id = snapshot["workflow_run_id"]
marker = Path(sys.argv[2])
tool_name = "image2env_preview_status_v1"

async def list_tools(_context, _params):
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name=tool_name,
                description="Read the assigned workflow status without changing business state.",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"workflow_id": {"type": "string", "const": workflow_id}},
                    "required": ["workflow_id"],
                },
            )
        ]
    )

async def call_tool(_context, params):
    if params.name != tool_name or (params.arguments or {}) != {"workflow_id": workflow_id}:
        raise MCPError(-32602, "foreign status request")
    marker.write_text(
        json.dumps(
            {"tool": tool_name, "workflow_id": workflow_id},
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    result = {"snapshot": snapshot}
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text=json.dumps(result, sort_keys=True, separators=(",", ":"))
            )
        ],
        structured_content=result,
        is_error=False,
    )

async def main():
    server = Server("approval-smoke", on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

asyncio.run(main())
"""
