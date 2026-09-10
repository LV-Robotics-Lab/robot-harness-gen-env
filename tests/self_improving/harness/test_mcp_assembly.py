"""Production assembly tests for the restartable compile MCP server."""

from __future__ import annotations

from pathlib import Path

import pytest

from self_improving.harness.application import create_compile_application
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.mcp_adapter import MCPToolCall
from self_improving.harness.mcp_assembly import (
    CompileMCPServerSettings,
    create_compile_mcp_adapter,
)
from self_improving.harness.schemas.text2env import CompileConfig, Text2EnvCompileInput
from tests.self_improving.harness import test_golden_run as base


def test_compile_mcp_assembly_reopens_the_same_durable_authorities(tmp_path: Path) -> None:
    application_settings = base._compile_settings(tmp_path)
    application = create_compile_application(application_settings)
    store = LocalArtifactStore(application.artifact_root)
    registry = base._qualified_registry_for_application(store, tmp_path, application)
    golden_database = tmp_path / "golden.sqlite3"
    catalog_path = application_settings.external_catalog_roots[0] / "empty.json"
    base._empty_catalog(
        catalog_path,
        objects_root=application_settings.allowed_asset_roots[0] / "objects",
    )
    settings = CompileMCPServerSettings(
        schema_version="harness.compile_mcp_server_settings.v1",
        principal_id="operator/system2",
        registry_snapshot=registry,
        compile_state_root=application_settings.state_root,
        external_catalog_roots=application_settings.external_catalog_roots,
        allowed_asset_roots=application_settings.allowed_asset_roots,
        admission_date=application_settings.admission_date,
        asset_library_root=application_settings.asset_library_root,
        golden_database_path=golden_database,
    )
    adapter = create_compile_mcp_adapter(settings)
    start_tool, tool = adapter.list_tools(settings.registry_snapshot)
    start_arguments = {
        "schema_version": "harness.mcp_workflow_create_request.v1",
        "idempotency_key": "start-through-reopened-mcp",
        "workspace": "ephemeral",
        "requested_profile": "legacy.robotwin_validation@2",
        "initial_user_facts": [
            {
                "key": "request.objective",
                "value": base.GENERATED_OBJECTIVE,
                "observed_at": "2026-09-09T00:00:00Z",
                "valid_until": None,
            }
        ],
    }
    started = adapter.call_tool(MCPToolCall(name=start_tool.name, arguments=start_arguments))
    assert started.is_error is False
    arguments = {
        "schema_version": "harness.run_command_request.v1",
        "command_id": "20000000-0000-4000-8000-000000000001",
        "workflow_run_id": started.structured_content["workflow_run_id"],
        "idempotency_key": "compile-through-reopened-mcp",
        "expected_workflow_revision": 0,
        "expected_state_sha256": started.structured_content["state_sha256"],
        "expected_receipt_head_sha256": started.structured_content["receipt_head"][
            "sha256"
        ],
        "parameters": Text2EnvCompileInput(
            request=base.GENERATED_OBJECTIVE,
            seed=77,
            asset_catalog=application.snapshot_asset_catalog(catalog_path),
            config=CompileConfig(generate_missing_assets=True),
        ).model_dump(mode="json"),
    }

    first = adapter.call_tool(MCPToolCall(name=tool.name, arguments=arguments))
    reopened = create_compile_mcp_adapter(settings)
    recovered_start = reopened.call_tool(
        MCPToolCall(name=start_tool.name, arguments=start_arguments)
    )
    second = reopened.call_tool(MCPToolCall(name=tool.name, arguments=arguments))

    assert first == second
    assert recovered_start.structured_content["revision"] == 1
    assert first.structured_content["status"] == "succeeded"
    assert first.structured_content["committed_revision"] == 1

    with pytest.raises(TypeError, match="exact CompileMCPServerSettings"):
        create_compile_mcp_adapter(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-empty exact string"):
        create_compile_mcp_adapter(settings.model_copy(update={"principal_id": ""}))
