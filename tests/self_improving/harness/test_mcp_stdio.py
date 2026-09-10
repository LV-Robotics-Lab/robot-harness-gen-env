"""Official MCP 2.2 stdio restart tracer for the production transport boundary."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path

import anyio
import pytest

pytest.importorskip("mcp")
assert version("mcp") == "2.2.0"

from mcp import Client, StdioServerParameters, types  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from self_improving.harness.application import create_compile_application  # noqa: E402
from self_improving.harness.artifacts import LocalArtifactStore  # noqa: E402
from self_improving.harness.golden_store import SQLiteGoldenRunStore  # noqa: E402
from self_improving.harness.schemas.text2env import (  # noqa: E402
    CompileConfig,
    Text2EnvCompileInput,
)
from tests.self_improving.harness import test_golden_run as base  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ASSEMBLY_SENTINEL = "MCP_STDIO_ASSEMBLY_PRINT_SENTINEL"
HANDLER_SENTINEL = "MCP_STDIO_HANDLER_PRINT_SENTINEL"


def _database_rows(path: Path) -> tuple[tuple[str, int], ...]:
    with closing(sqlite3.connect(path)) as connection:
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
        return tuple(
            (table, connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in tables
        )


def _cas_manifest(root: Path) -> tuple[tuple[str, int, str], ...]:
    return tuple(
        (str(path.relative_to(root)), path.stat().st_size, sha256(path.read_bytes()).hexdigest())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def _server_parameters(*, command: list[str]) -> StdioServerParameters:
    return StdioServerParameters(
        command=command[0],
        args=command[1:],
        cwd=str(PROJECT_ROOT),
        env={
            **os.environ,
            "PYTHONPATH": str(PROJECT_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        },
    )


async def _roundtrip(
    server: StdioServerParameters,
    *,
    tool_name: str,
    start_arguments: dict[str, object],
    compile_parameters: dict[str, object],
    command_arguments: dict[str, object] | None = None,
) -> tuple[dict[str, object], dict[str, object], str, dict[str, object]]:
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errlog:
        try:
            async with Client(
                stdio_client(server, errlog=errlog),
                raise_exceptions=True,
                cache=None,
            ) as client:
                assert client.protocol_version == "2026-07-28"
                listed = await client.list_tools()
                assert tuple(tool.name for tool in listed.tools) == (
                    "harness_workflow_create_v1",
                    tool_name,
                )
                assert listed.tools[1].input_schema["additionalProperties"] is False
                created = await client.call_tool(listed.tools[0].name, start_arguments)
                assert created.is_error is False
                assert created.structured_content is not None
                workflow_uri = (
                    "harness://workflows/"
                    + str(created.structured_content["workflow_run_id"])
                )
                if command_arguments is None:
                    assert created.structured_content["revision"] == 0
                    arguments = {
                        "schema_version": "harness.run_command_request.v1",
                        "command_id": "20000000-0000-4000-8000-000000000001",
                        "workflow_run_id": created.structured_content["workflow_run_id"],
                        "idempotency_key": "compile-through-stdio-restart-001",
                        "expected_workflow_revision": created.structured_content["revision"],
                        "expected_state_sha256": created.structured_content["state_sha256"],
                        "expected_receipt_head_sha256": created.structured_content[
                            "receipt_head"
                        ]["sha256"],
                        "parameters": compile_parameters,
                    }
                else:
                    assert created.structured_content["revision"] == 1
                    arguments = command_arguments
                result = await client.call_tool(tool_name, arguments)
                assert result.is_error is False
                assert result.structured_content is not None
                assert result.structured_content["status"] == "succeeded"
                assert [content.type for content in result.content] == ["text", "resource_link"]
                assert isinstance(result.content[0], types.TextContent)
                assert isinstance(result.content[1], types.ResourceLink)
                assert str(result.content[1].uri) == workflow_uri
                resource = await client.read_resource(workflow_uri)
                assert len(resource.contents) == 1
                assert isinstance(resource.contents[0], types.TextResourceContents)
                snapshot = json.loads(resource.contents[0].text)
                assert snapshot["revision"] == 1
        except BaseException as error:
            errlog.flush()
            errlog.seek(0)
            raise AssertionError(f"stdio child failed:\n{errlog.read()}") from error
        errlog.seek(0)
        stderr = errlog.read()
    return (
        result.model_dump(mode="json", by_alias=True, exclude_none=True),
        resource.model_dump(mode="json", by_alias=True, exclude_none=True),
        stderr,
        arguments,
    )


def test_official_stdio_client_reuses_one_operation_after_server_restart(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A/B processes must preserve authority and keep ordinary prints off stdout."""

    settings = base._compile_settings(tmp_path)
    application = create_compile_application(settings)
    artifact_store = LocalArtifactStore(application.artifact_root)
    registry = base._qualified_registry_for_application(
        artifact_store,
        tmp_path,
        application,
    )
    golden_database = tmp_path / "golden.sqlite3"
    SQLiteGoldenRunStore(golden_database)
    catalog_path = settings.external_catalog_roots[0] / "empty.json"
    base._empty_catalog(
        catalog_path,
        objects_root=settings.allowed_asset_roots[0] / "objects",
    )
    tool_name = "text2env_compile_v1_0_0"
    compile_parameters = Text2EnvCompileInput(
        request=base.GENERATED_OBJECTIVE,
        seed=77,
        asset_catalog=application.snapshot_asset_catalog(catalog_path),
        config=CompileConfig(generate_missing_assets=True),
    ).model_dump(mode="json")
    start_arguments: dict[str, object] = {
        "schema_version": "harness.mcp_workflow_create_request.v1",
        "idempotency_key": "start-through-stdio-restart-001",
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
    config_path = tmp_path / "mcp-stdio-settings.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": "harness.compile_mcp_server_settings.v1",
                "principal_id": "tests/golden-e2e",
                "registry_snapshot": registry.model_dump(mode="json"),
                "compile_state_root": str(settings.state_root),
                "external_catalog_roots": [str(path) for path in settings.external_catalog_roots],
                "allowed_asset_roots": [str(path) for path in settings.allowed_asset_roots],
                "admission_date": settings.admission_date.isoformat(),
                "asset_library_root": str(settings.asset_library_root),
                "golden_database_path": str(golden_database),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    database_before = (
        _database_rows(golden_database),
        _database_rows(application.journal_path),
    )
    cas_before = _cas_manifest(application.artifact_root)
    caplog.set_level(logging.ERROR)

    async def exercise() -> tuple[
        tuple[dict[str, object], dict[str, object], str],
        tuple[dict[str, object], dict[str, object], str],
        tuple[tuple[tuple[str, int], ...], tuple[tuple[str, int], ...]],
        tuple[tuple[tuple[str, int], ...], tuple[tuple[str, int], ...]],
        tuple[tuple[str, int, str], ...],
        tuple[tuple[str, int, str], ...],
    ]:
        with anyio.fail_after(30):
            first = await _roundtrip(
                _server_parameters(
                    command=[
                        sys.executable,
                        "-m",
                        "tests.self_improving.harness.test_mcp_stdio",
                        "--serve-with-print-sentinels",
                        str(config_path),
                    ]
                ),
                tool_name=tool_name,
                start_arguments=start_arguments,
                compile_parameters=compile_parameters,
            )
        database_after_first = (
            _database_rows(golden_database),
            _database_rows(application.journal_path),
        )
        cas_after_first = _cas_manifest(application.artifact_root)
        with anyio.fail_after(30):
            second = await _roundtrip(
                _server_parameters(
                    command=[
                        sys.executable,
                        "-m",
                        "self_improving.harness.mcp_stdio",
                        "--settings",
                        str(config_path),
                    ]
                ),
                tool_name=tool_name,
                start_arguments=start_arguments,
                compile_parameters=compile_parameters,
                command_arguments=first[3],
            )
        database_after_second = (
            _database_rows(golden_database),
            _database_rows(application.journal_path),
        )
        cas_after_second = _cas_manifest(application.artifact_root)
        return (
            first,
            second,
            database_after_first,
            database_after_second,
            cas_after_first,
            cas_after_second,
        )

    (
        first,
        second,
        database_after_first,
        database_after_second,
        cas_after_first,
        cas_after_second,
    ) = anyio.run(exercise)

    assert first[:2] == second[:2]
    assert first[3] == second[3]
    assert first[0]["structuredContent"]["operation_id"] == (
        "20000000-0000-4000-8000-000000000001"
    )
    assert database_before[0] != database_after_first[0]
    assert database_after_first == database_after_second
    assert cas_after_first == cas_after_second
    assert cas_before != cas_after_first
    golden_counts = dict(database_after_second[0])
    child_counts = dict(database_after_second[1])
    assert golden_counts["golden_workflow_starts"] == 1
    assert golden_counts["golden_workflow_operations"] == 1
    assert child_counts["harness_controlled_run_intents"] == 1
    assert child_counts["harness_invocations"] == 1
    assert child_counts["harness_run_states"] == 1
    assert first[2].count(ASSEMBLY_SENTINEL) == 1
    assert first[2].count(HANDLER_SENTINEL) == 2
    assert ASSEMBLY_SENTINEL not in second[2]
    assert HANDLER_SENTINEL not in second[2]
    client_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "Failed to parse JSONRPC message" not in client_logs
    assert ASSEMBLY_SENTINEL not in client_logs
    assert HANDLER_SENTINEL not in client_logs


async def _serve_with_print_sentinels(config_path: Path) -> None:
    """Fault-inject ordinary prints while preserving the real production adapter/server."""

    from self_improving.harness.mcp_adapter import HarnessMCPAdapter
    from self_improving.harness.mcp_stdio import (
        CompileMCPServerSettings,
        _serve_stdio,
        create_compile_mcp_adapter,
    )

    settings = CompileMCPServerSettings.model_validate_json(config_path.read_bytes())
    original_call_tool = HarnessMCPAdapter.call_tool

    def noisy_call_tool(self, request):
        print(HANDLER_SENTINEL)
        return original_call_tool(self, request)

    HarnessMCPAdapter.call_tool = noisy_call_tool
    try:

        def adapter_factory():
            print(ASSEMBLY_SENTINEL)
            return create_compile_mcp_adapter(settings)

        await _serve_stdio(adapter_factory)
    finally:
        HarnessMCPAdapter.call_tool = original_call_tool


def _main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] != "--serve-with-print-sentinels":
        raise SystemExit("expected --serve-with-print-sentinels SETTINGS.json")
    anyio.run(_serve_with_print_sentinels, Path(sys.argv[2]))


if __name__ == "__main__":
    _main()
