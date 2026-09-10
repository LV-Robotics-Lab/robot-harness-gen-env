"""Raw MCP protocol compatibility tests for the external Codex client."""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pytest

pytest.importorskip("mcp")
assert version("mcp") == "2.2.0"

import self_improving.harness as harness_package  # noqa: E402
from self_improving.harness.application import create_compile_application  # noqa: E402
from self_improving.harness.artifacts import LocalArtifactStore  # noqa: E402
from self_improving.harness.mcp_assembly import CompileMCPServerSettings  # noqa: E402
from tests.self_improving.harness import test_golden_run as base  # noqa: E402

PROJECT_ROOT = Path(harness_package.__file__).resolve().parents[2]


def test_codex_legacy_stdio_handshake_lists_exact_object_output_tools(tmp_path: Path) -> None:
    application_settings = base._compile_settings(tmp_path)
    application = create_compile_application(application_settings)
    registry = base._qualified_registry_for_application(
        LocalArtifactStore(application.artifact_root),
        tmp_path,
        application,
    )
    settings = CompileMCPServerSettings(
        schema_version="harness.compile_mcp_server_settings.v1",
        principal_id="tests/codex-protocol-compat",
        registry_snapshot=registry,
        compile_state_root=application_settings.state_root,
        external_catalog_roots=application_settings.external_catalog_roots,
        allowed_asset_roots=application_settings.allowed_asset_roots,
        admission_date=application_settings.admission_date,
        asset_library_root=application_settings.asset_library_root,
        golden_database_path=tmp_path / "golden.sqlite3",
    )
    settings_path = tmp_path / "mcp-settings.json"
    settings_path.write_text(settings.model_dump_json(), encoding="utf-8")
    frames = (
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"elicitation": {"form": {}, "url": {}}},
                "clientInfo": {
                    "name": "codex-mcp-client",
                    "title": "Codex",
                    "version": "0.153.4",
                },
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {"_meta": {"progressToken": 0}},
        },
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "self_improving.harness.mcp_stdio",
            "--settings",
            str(settings_path),
        ],
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(PROJECT_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        },
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None

    def send(frame: dict[str, object]) -> None:
        process.stdin.write(json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n")
        process.stdin.flush()

    def receive() -> dict[str, object]:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=10), "MCP server did not answer the raw Codex frame"
        line = process.stdout.readline()
        assert line, "MCP server closed stdout before answering the raw Codex frame"
        return json.loads(line)

    try:
        send(frames[0])
        initialize_response = receive()
        send(frames[1])
        send(frames[2])
        tools_response = receive()
    finally:
        process.stdin.close()
        try:
            returncode = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise
    responses = (initialize_response, tools_response)

    assert returncode == 0, process.stderr.read()
    assert responses[0]["id"] == 0
    assert responses[0]["result"]["protocolVersion"] == "2025-06-18"
    assert responses[1]["id"] == 1
    assert "error" not in responses[1]
    tools = responses[1]["result"]["tools"]
    assert tuple(tool["name"] for tool in tools) == (
        "harness_workflow_create_v1",
        "harness_advisory_record_v1",
        "harness_prompt_revision_record_v1",
        "text2env_compile_v1_0_0",
    )
    assert {"skill.invoke", "skill_invoke"}.isdisjoint(tool["name"] for tool in tools)
    assert all(tool["outputSchema"]["type"] == "object" for tool in tools)
