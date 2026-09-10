"""Public S2 tracer tests for the exact qualified MCP adapter."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator

from self_improving.harness.application import create_compile_application
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.golden_compile_execution import (
    create_text2env_compile_execution_binding,
)
from self_improving.harness.golden_execution import create_golden_execution_catalog
from self_improving.harness.golden_run import GoldenRunHarness
from self_improving.harness.golden_store import SQLiteGoldenRunStore
from self_improving.harness.mcp_adapter import (
    HarnessMCPAdapter,
    HarnessMCPAdapterError,
    HarnessMCPProtocolError,
    MCPResourceRead,
    MCPToolCall,
    MCPWorkflowSkillInputProvider,
    create_harness_mcp_adapter,
)
from self_improving.harness.schemas.common import SkillDescriptor
from self_improving.harness.schemas.registry_snapshot import QualifiedSkillSnapshot
from self_improving.harness.schemas.text2env import CompileConfig, Text2EnvCompileInput
from tests.self_improving.harness import test_golden_run as base


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_adapter_lists_only_the_exact_qualified_live_tool(tmp_path: Path) -> None:
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
    execution_catalog = create_golden_execution_catalog(
        bindings=(create_text2env_compile_execution_binding(application=application),)
    )
    harness = GoldenRunHarness(
        artifact_store=artifact_store,
        workflow_store=SQLiteGoldenRunStore(tmp_path / "golden.sqlite3"),
        execution_catalog=execution_catalog,
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
    tools = adapter.list_tools(started.registry_snapshot)

    assert tuple(tool.name for tool in tools) == (
        "harness_workflow_create_v1",
        "harness_advisory_record_v1",
        "harness_prompt_revision_record_v1",
        "text2env_compile_v1_0_0",
    )
    assert all(tool.name != "skill.invoke" for tool in tools)
    tool = next(item for item in tools if item.name == "text2env_compile_v1_0_0")
    assert tool.skill_ref == "text2env.compile@1.0.0"
    assert tool.input_schema["additionalProperties"] is False
    assert "principal_id" not in tool.input_schema["properties"]
    assert "skill_ref" not in tool.input_schema["properties"]
    assert tool.output_schema["$id"] == "harness.mcp_exact_tool_result.v1"
    snapshot_path = (
        Path(__file__).resolve().parents[3]
        / "self_improving/harness/mcp_tool_snapshots/catalog.v1.json"
    )
    snapshot = json.loads(snapshot_path.read_bytes())
    assert snapshot == {
        "schema_version": "harness.mcp_tool_catalog_snapshot.v1",
        "tools": [
            {
                "name": listed_tool.name,
                "skill_ref": listed_tool.skill_ref,
                "input_schema_sha256": sha256(
                    _canonical_json_bytes(listed_tool.input_schema)
                ).hexdigest(),
                "output_schema_sha256": sha256(
                    _canonical_json_bytes(listed_tool.output_schema)
                ).hexdigest(),
            }
            for listed_tool in tools
        ],
    }
    Draft202012Validator.check_schema(dict(tool.input_schema))
    catalog_path = settings.external_catalog_roots[0] / "empty.json"
    base._empty_catalog(
        catalog_path,
        objects_root=settings.allowed_asset_roots[0] / "objects",
    )
    arguments = {
        "schema_version": "harness.run_command_request.v1",
        "command_id": "20000000-0000-4000-8000-000000000001",
        "workflow_run_id": str(started.workflow_run_id),
        "idempotency_key": "compile-through-mcp-001",
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
    Draft202012Validator(dict(tool.input_schema)).validate(arguments)

    result = adapter.call_tool(MCPToolCall(name=tool.name, arguments=arguments))

    assert result.is_error is False
    assert result.structured_content["status"] == "succeeded"
    assert result.structured_content["skill_ref"] == "text2env.compile@1.0.0"
    assert result.content[0].type == "text"
    assert result.content[1].type == "resource_link"
    assert result.content[1].uri.endswith(str(started.workflow_run_id))
    Draft202012Validator(dict(tool.output_schema)).validate(dict(result.structured_content))

    repeated = adapter.call_tool(MCPToolCall(name=tool.name, arguments=arguments))
    assert repeated == result

    rejected = adapter.call_tool(
        MCPToolCall(name=tool.name, arguments={**arguments, "principal_id": "forged"})
    )
    assert rejected.is_error is True
    assert rejected.structured_content["code"] == "HARN_INPUT_SCHEMA_INVALID"
    Draft202012Validator(dict(tool.output_schema)).validate(dict(rejected.structured_content))

    with pytest.raises(HarnessMCPProtocolError, match="unknown exact MCP tool"):
        adapter.call_tool(MCPToolCall(name="skill.invoke", arguments={}))

    conflict = adapter.call_tool(
        MCPToolCall(
            name=tool.name,
            arguments={
                **arguments,
                "command_id": "20000000-0000-4000-8000-000000000002",
            },
        )
    )
    assert conflict.is_error is True
    assert conflict.structured_content["code"] == "HARN_IDEMPOTENCY_CONFLICT"

    stale = adapter.call_tool(
        MCPToolCall(
            name=tool.name,
            arguments={
                **arguments,
                "command_id": "20000000-0000-4000-8000-000000000003",
                "idempotency_key": "compile-stale-001",
            },
        )
    )
    assert stale.is_error is True
    assert stale.structured_content["code"] == "HARN_WORKFLOW_HEAD_CONFLICT"

    missing = adapter.call_tool(
        MCPToolCall(
            name=tool.name,
            arguments={
                **arguments,
                "command_id": "20000000-0000-4000-8000-000000000004",
                "workflow_run_id": "10000000-0000-4000-8000-000000000099",
                "idempotency_key": "compile-missing-001",
            },
        )
    )
    assert missing.is_error is True
    assert missing.structured_content["code"] == "HARN_WORKFLOW_NOT_FOUND"

    non_json = adapter.call_tool(
        MCPToolCall(name=tool.name, arguments={**arguments, "parameters": object()})
    )
    assert non_json.is_error is True
    assert non_json.structured_content["code"] == "HARN_INPUT_SCHEMA_INVALID"

    resource = adapter.read_resource(MCPResourceRead(uri=result.content[1].uri))
    assert resource.media_type == "application/json"
    assert json.loads(resource.text)["revision"] == 1
    with pytest.raises(HarnessMCPProtocolError, match="unknown Harness resource"):
        adapter.read_resource(MCPResourceRead(uri="harness://workflows/not-a-uuid"))
    with pytest.raises(HarnessMCPProtocolError, match="unknown Harness resource"):
        adapter.read_resource(
            MCPResourceRead(uri="harness://workflows/10000000-0000-4000-8000-000000000099")
        )
    with pytest.raises(HarnessMCPProtocolError, match="unknown Harness resource"):
        adapter.read_resource(
            MCPResourceRead(
                uri=(
                    f"harness://workflows/{started.workflow_run_id}/skill-inputs/"
                    "text2env.validate/2.0.0"
                )
            )
        )

    with pytest.raises(TypeError, match="authority factory"):
        HarnessMCPAdapter(
            _token=object(),
            harness=harness,
            principal_id=started.principal_id,
            registry_snapshot=started.registry_snapshot,
            skill_input_providers={},
        )
    with pytest.raises(TypeError, match="exact GoldenRunHarness"):
        create_harness_mcp_adapter(
            harness=object(),  # type: ignore[arg-type]
            principal_id=started.principal_id,
            registry_snapshot=started.registry_snapshot,
        )
    with pytest.raises(TypeError, match="non-empty exact string"):
        create_harness_mcp_adapter(
            harness=harness,
            principal_id="",
            registry_snapshot=started.registry_snapshot,
        )
    with pytest.raises(TypeError, match="exact provider records"):
        create_harness_mcp_adapter(
            harness=harness,
            principal_id=started.principal_id,
            registry_snapshot=started.registry_snapshot,
            skill_input_providers=(object(),),  # type: ignore[arg-type]
        )
    provider = MCPWorkflowSkillInputProvider(
        skill_ref="text2env.compile@1.0.0",
        build=lambda snapshot: snapshot,
    )
    with pytest.raises(ValueError, match="unique Skill refs"):
        create_harness_mcp_adapter(
            harness=harness,
            principal_id=started.principal_id,
            registry_snapshot=started.registry_snapshot,
            skill_input_providers=(provider, provider),
        )
    with pytest.raises(ValueError, match="target exact live Skills"):
        create_harness_mcp_adapter(
            harness=harness,
            principal_id=started.principal_id,
            registry_snapshot=started.registry_snapshot,
            skill_input_providers=(
                MCPWorkflowSkillInputProvider(
                    skill_ref="text2env.validate@2.0.0",
                    build=lambda snapshot: snapshot,
                ),
            ),
        )

    def rejected_input(_snapshot):
        raise ValueError("input is not available")

    rejecting_adapter = create_harness_mcp_adapter(
        harness=harness,
        principal_id=started.principal_id,
        registry_snapshot=started.registry_snapshot,
        skill_input_providers=(
            MCPWorkflowSkillInputProvider(
                skill_ref="text2env.compile@1.0.0",
                build=rejected_input,
            ),
        ),
    )
    with pytest.raises(HarnessMCPProtocolError, match="unknown Harness resource"):
        rejecting_adapter.read_resource(
            MCPResourceRead(
                uri=(
                    f"harness://workflows/{started.workflow_run_id}/skill-inputs/"
                    "text2env.compile/1.0.0"
                )
            )
        )
    with pytest.raises(HarnessMCPAdapterError, match="not the adapter's frozen authority"):
        adapter.list_tools(objective)

    empty_harness = GoldenRunHarness(
        artifact_store=artifact_store,
        workflow_store=SQLiteGoldenRunStore(tmp_path / "empty-golden.sqlite3"),
        clock=lambda: base.NOW,
        workflow_id_factory=lambda: UUID("10000000-0000-4000-8000-000000000002"),
    )
    empty_started = empty_harness.start(
        base._request(objective, registry, idempotency_key="empty-catalog-start")
    )
    with pytest.raises(HarnessMCPAdapterError) as unqualified:
        create_harness_mcp_adapter(
            harness=empty_harness,
            principal_id=empty_started.principal_id,
            registry_snapshot=empty_started.registry_snapshot,
        )
    assert unqualified.value.code == "HARN_SKILL_UNQUALIFIED"

    snapshot = base._load_registry(artifact_store, registry)
    entry = snapshot.qualified_skills[0]
    descriptor = SkillDescriptor.model_validate_json(
        artifact_store.resolve(entry.descriptor_ref).path.read_bytes()
    )
    drifted_descriptor = descriptor.model_copy(update={"max_attempts": descriptor.max_attempts + 1})
    drifted_descriptor_ref = base._put(
        artifact_store,
        tmp_path,
        name="drifted-compile-descriptor.json",
        payload=base._canonical_bytes(drifted_descriptor),
        schema_version="harness.skill_descriptor.v1",
    )
    drifted_registry = base._publish_registry_entry(
        artifact_store,
        tmp_path,
        QualifiedSkillSnapshot.model_validate(
            entry.model_copy(update={"descriptor_ref": drifted_descriptor_ref}).model_dump(
                mode="python"
            )
        ),
        name="drifted-registry.json",
    )
    drifted_harness = GoldenRunHarness(
        artifact_store=artifact_store,
        workflow_store=SQLiteGoldenRunStore(tmp_path / "drifted-golden.sqlite3"),
        execution_catalog=execution_catalog,
        clock=lambda: base.NOW,
        workflow_id_factory=lambda: UUID("10000000-0000-4000-8000-000000000003"),
    )
    drifted_started = drifted_harness.start(
        base._request(
            objective,
            drifted_registry,
            idempotency_key="drifted-catalog-start",
        )
    )
    with pytest.raises(HarnessMCPAdapterError) as drifted:
        create_harness_mcp_adapter(
            harness=drifted_harness,
            principal_id=drifted_started.principal_id,
            registry_snapshot=drifted_started.registry_snapshot,
        )
    assert drifted.value.code == "HARN_DEPENDENCY_DRIFT"

    registry_path = artifact_store.resolve(registry).path
    registry_path.chmod(0o600)
    registry_path.write_bytes(b"corrupt registry\n")
    corrupt_call = adapter.call_tool(MCPToolCall(name=tool.name, arguments=arguments))
    assert corrupt_call.is_error is True
    assert corrupt_call.structured_content["code"] == "HARN_ARTIFACT_UNAVAILABLE"
    Draft202012Validator(dict(tool.output_schema)).validate(dict(corrupt_call.structured_content))
    with pytest.raises(HarnessMCPAdapterError) as corrupt:
        adapter.list_tools(started.registry_snapshot)
    assert corrupt.value.code == "HARN_ARTIFACT_UNAVAILABLE"
