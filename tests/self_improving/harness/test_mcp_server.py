"""Official MCP 2.2 in-process client tests for the S2 transport adapter."""

from __future__ import annotations

import json
from importlib.metadata import version
from pathlib import Path
from uuid import UUID

import anyio
import pytest

pytest.importorskip("mcp")
assert tuple(int(part) for part in version("mcp").split(".")[:2]) >= (2, 2)

from mcp import Client, MCPError  # noqa: E402

from self_improving.harness.application import create_compile_application  # noqa: E402
from self_improving.harness.artifacts import LocalArtifactStore  # noqa: E402
from self_improving.harness.golden_compile_execution import (  # noqa: E402
    create_text2env_compile_execution_binding,
)
from self_improving.harness.golden_execution import (  # noqa: E402
    create_golden_execution_catalog,
)
from self_improving.harness.golden_run import GoldenRunHarness  # noqa: E402
from self_improving.harness.golden_store import SQLiteGoldenRunStore  # noqa: E402
from self_improving.harness.mcp_adapter import create_harness_mcp_adapter  # noqa: E402
from self_improving.harness.mcp_server import create_harness_mcp_server  # noqa: E402
from self_improving.harness.schemas.text2env import (  # noqa: E402
    CompileConfig,
    Text2EnvCompileInput,
)
from tests.self_improving.harness import test_golden_run as base  # noqa: E402


def test_official_client_lists_and_calls_the_exact_tool_in_process(tmp_path: Path) -> None:
    settings = base._compile_settings(tmp_path)
    application = create_compile_application(settings)
    artifact_store = LocalArtifactStore(application.artifact_root)
    objective = base._put(
        artifact_store,
        tmp_path,
        name="objective.json",
        payload=base.GENERATED_OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = base._qualified_registry_for_application(artifact_store, tmp_path, application)
    harness = GoldenRunHarness(
        artifact_store=artifact_store,
        workflow_store=SQLiteGoldenRunStore(tmp_path / "golden.sqlite3"),
        execution_catalog=create_golden_execution_catalog(
            bindings=(create_text2env_compile_execution_binding(application=application),)
        ),
        clock=lambda: base.NOW,
        workflow_id_factory=lambda: base.WORKFLOW_ID,
        child_run_id_factory=lambda: UUID("30000000-0000-4000-8000-000000000001"),
    )
    started = harness.start(base._request(objective, registry))
    adapter = create_harness_mcp_adapter(
        harness=harness,
        principal_id=started.principal_id,
        registry_snapshot=started.registry_snapshot,
    )
    server = create_harness_mcp_server(adapter)
    with pytest.raises(TypeError, match="exact HarnessMCPAdapter"):
        create_harness_mcp_server(object())  # type: ignore[arg-type]
    catalog_path = settings.external_catalog_roots[0] / "empty.json"
    base._empty_catalog(
        catalog_path,
        objects_root=settings.allowed_asset_roots[0] / "objects",
    )
    arguments = {
        "schema_version": "harness.run_command_request.v1",
        "command_id": "20000000-0000-4000-8000-000000000001",
        "workflow_run_id": str(started.workflow_run_id),
        "idempotency_key": "compile-through-official-client-001",
        "expected_workflow_revision": started.revision,
        "expected_state_sha256": started.state_sha256,
        "expected_receipt_head_sha256": started.receipt_head.sha256,
        "parameters": Text2EnvCompileInput(
            request=base.GENERATED_OBJECTIVE,
            seed=77,
            asset_catalog=application.snapshot_asset_catalog(catalog_path),
            config=CompileConfig(generate_missing_assets=True),
        ).model_dump(mode="json"),
    }

    async def exercise() -> None:
        async with Client(server, raise_exceptions=True, cache=None) as client:
            listed = await client.list_tools()
            assert tuple(tool.name for tool in listed.tools) == (
                "text2env_compile_v1_0_0",
            )
            assert listed.tools[0].input_schema == adapter.list_tools(
                started.registry_snapshot
            )[0].input_schema
            result = await client.call_tool(listed.tools[0].name, arguments)
            assert result.is_error is False
            assert result.structured_content["status"] == "succeeded"
            assert [content.type for content in result.content] == ["text", "resource_link"]
            resource = await client.read_resource(
                f"harness://workflows/{started.workflow_run_id}"
            )
            assert json.loads(resource.contents[0].text)["revision"] == 1
            with pytest.raises(MCPError, match="unknown exact MCP tool"):
                await client.call_tool("skill.invoke", {})
            with pytest.raises(MCPError, match="unknown Harness resource"):
                await client.read_resource("harness://workflows/not-a-uuid")

    anyio.run(exercise)
