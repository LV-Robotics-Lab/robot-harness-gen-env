"""Exact MCP workflow-create control tool tests."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from jsonschema import Draft202012Validator

from self_improving.harness.application import create_compile_application
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.golden_compile_execution import (
    create_text2env_compile_execution_binding,
)
from self_improving.harness.golden_execution import create_golden_execution_catalog
from self_improving.harness.golden_run import GoldenRunHarness
from self_improving.harness.golden_store import SQLiteGoldenRunStore
from self_improving.harness.mcp_adapter import MCPToolCall, create_harness_mcp_adapter
from tests.self_improving.harness import test_golden_run as base


def test_exact_workflow_create_injects_authority_and_recovers_after_restart(
    tmp_path: Path,
) -> None:
    settings = base._compile_settings(tmp_path)
    application = create_compile_application(settings)
    store = LocalArtifactStore(application.artifact_root)
    registry = base._qualified_registry_for_application(store, tmp_path, application)
    database = tmp_path / "golden.sqlite3"

    def adapter(workflow_id: UUID):
        harness = GoldenRunHarness(
            artifact_store=store,
            workflow_store=SQLiteGoldenRunStore(database),
            execution_catalog=create_golden_execution_catalog(
                bindings=(create_text2env_compile_execution_binding(application=application),)
            ),
            clock=lambda: base.NOW,
            workflow_id_factory=lambda: workflow_id,
        )
        return create_harness_mcp_adapter(
            harness=harness,
            principal_id="operator/system2",
            registry_snapshot=registry,
        )

    first_adapter = adapter(UUID("10000000-0000-4000-8000-000000000010"))
    tool = first_adapter.list_tools(registry)[0]
    assert tool.name == "harness_workflow_create_v1"
    assert tool.skill_ref is None
    assert "principal_id" not in tool.input_schema["properties"]
    assert "registry_snapshot" not in tool.input_schema["properties"]
    assert "initial_fact_evidence" not in tool.input_schema["properties"]
    arguments = {
        "schema_version": "harness.mcp_workflow_create_request.v1",
        "idempotency_key": "mcp-workflow-start-001",
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
    Draft202012Validator(dict(tool.input_schema)).validate(arguments)

    first = first_adapter.call_tool(MCPToolCall(name=tool.name, arguments=arguments))
    restarted = adapter(UUID("10000000-0000-4000-8000-000000000011"))
    second = restarted.call_tool(MCPToolCall(name=tool.name, arguments=arguments))

    assert first == second
    assert first.is_error is False
    assert first.structured_content["revision"] == 0
    assert first.structured_content["principal_id"] == "operator/system2"
    assert first.structured_content["registry_snapshot"] == registry.model_dump(mode="json")
    Draft202012Validator(dict(tool.output_schema)).validate(dict(first.structured_content))

    forged = first_adapter.call_tool(
        MCPToolCall(name=tool.name, arguments={**arguments, "principal_id": "forged"})
    )
    assert forged.is_error is True
    assert forged.structured_content["code"] == "HARN_INPUT_SCHEMA_INVALID"
    duplicate = first_adapter.call_tool(
        MCPToolCall(
            name=tool.name,
            arguments={
                **arguments,
                "idempotency_key": "mcp-workflow-start-duplicate",
                "initial_user_facts": arguments["initial_user_facts"] * 2,
            },
        )
    )
    assert duplicate.is_error is True
    assert duplicate.structured_content["code"] == "HARN_INPUT_SCHEMA_INVALID"
    conflict = first_adapter.call_tool(
        MCPToolCall(name=tool.name, arguments={**arguments, "workspace": "production"})
    )
    assert conflict.is_error is True
    assert conflict.structured_content["code"] == "HARN_IDEMPOTENCY_CONFLICT"
    invalid_fact = first_adapter.call_tool(
        MCPToolCall(
            name=tool.name,
            arguments={
                **arguments,
                "idempotency_key": "mcp-workflow-start-invalid-fact",
                "initial_user_facts": [
                    {
                        **arguments["initial_user_facts"][0],
                        "key": "environment.objective",
                    }
                ],
            },
        )
    )
    assert invalid_fact.is_error is True
    assert invalid_fact.structured_content["code"] == "HARN_INPUT_SCHEMA_INVALID"
