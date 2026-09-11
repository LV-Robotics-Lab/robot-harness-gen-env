"""Exact native control projection uses real Harness authority and official MCP."""

import pytest

from self_improving.harness.mcp_control_v2 import create_native_harness_mcp_adapter
from tests.self_improving.harness.test_golden_control_v2_runtime import native_case


def advisory_arguments(case):
    command = case.command

    def uri(ref):
        return f"harness://workflows/{command.workflow_run_id}/artifacts/{ref.sha256}"

    return {
        "schema_version": "harness.mcp_advisory_record_request.v2",
        **{
            name: command.model_dump(mode="json")[name]
            for name in (
                "command_id",
                "workflow_run_id",
                "idempotency_key",
                "expected_workflow_revision",
                "expected_state_sha256",
                "expected_receipt_head_sha256",
                "advisory_kind",
                "disposition",
                "summary",
                "recommended_action",
            )
        },
        "source_prompt_uri": uri(command.source_prompt),
        "source_prompt_revision_uri": None,
        "failure_receipt_uris": [uri(ref) for ref in command.failure_receipts],
        "observation_receipt_uris": [uri(c.receipt) for c in command.observation_citations],
        "diagnostic_uris": [],
        "resource_access_receipt_uris": [
            f"harness://workflows/{command.workflow_run_id}/resource-access/{ref.sha256}"
            for ref in command.resource_access_receipts
        ],
    }


def test_native_adapter_projects_v2_controls_and_never_v1(tmp_path):
    case = native_case(tmp_path)
    adapter = create_native_harness_mcp_adapter(
        harness=case.harness,
        principal_id=case.head.principal_id,
        registry_snapshot=case.head.registry_snapshot,
    )
    names = {tool.name for tool in adapter.list_tools(case.head.registry_snapshot)}
    assert {"harness_advisory_record_v2", "harness_prompt_revision_record_v2"} <= names
    assert (
        not {"harness_advisory_record_v1", "harness_prompt_revision_record_v1", "skill.invoke"}
        & names
    )


def test_official_client_derives_citation_then_records_native_prompt_revision(tmp_path):
    import asyncio
    import json
    from uuid import uuid4

    pytest.importorskip("mcp")
    from mcp import Client, MCPError

    from self_improving.harness.mcp_server import create_harness_mcp_server

    case = native_case(tmp_path)
    adapter = create_native_harness_mcp_adapter(
        harness=case.harness,
        principal_id=case.head.principal_id,
        registry_snapshot=case.head.registry_snapshot,
    )
    with pytest.raises(TypeError, match="exact HarnessMCPAdapter"):
        create_harness_mcp_server(object())
    server = create_harness_mcp_server(adapter)
    args = advisory_arguments(case)

    async def exercise():
        async with Client(server, raise_exceptions=True, cache=None) as client:
            with pytest.raises(MCPError):
                await client.call_tool("skill.invoke", {})
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert "harness_advisory_record_v2" in names
            accesses = []
            for uri in (
                args["source_prompt_uri"],
                *args["failure_receipt_uris"],
                *args["observation_receipt_uris"],
            ):
                read = await client.read_resource(uri)
                accesses.append(str(read.contents[1].uri))
            args["resource_access_receipt_uris"] = sorted(accesses)
            result = await client.call_tool("harness_advisory_record_v2", args)
            assert not result.is_error, result
            control = result.structured_content
            assert control["schema_version"] == "harness.control_commit_snapshot.v2"
            receipt_uri = f"harness://workflows/{case.head.workflow_run_id}/artifacts/{control['receipt']['sha256']}"
            read = await client.read_resource(receipt_uri)
            receipt = json.loads(read.contents[0].text)
            assert receipt["observation_citations"][0] == case.command.observation_citations[
                0
            ].model_dump(mode="json")
            head = case.harness.read(case.read)
            environment = (
                f"harness://workflows/{head.workflow_run_id}/artifacts/{case.native.sha256}"
            )
            accesses = []
            for uri in (
                args["source_prompt_uri"],
                receipt_uri,
                *args["failure_receipt_uris"],
                environment,
            ):
                read = await client.read_resource(uri)
                accesses.append(str(read.contents[1].uri))
            revision = {
                "schema_version": "harness.mcp_prompt_revision_record_request.v2",
                "command_id": str(uuid4()),
                "workflow_run_id": str(head.workflow_run_id),
                "idempotency_key": "native-revision",
                "expected_workflow_revision": head.revision,
                "expected_state_sha256": head.state_sha256,
                "expected_receipt_head_sha256": head.receipt_head.sha256,
                "source_prompt_uri": args["source_prompt_uri"],
                "failure_receipt_uris": args["failure_receipt_uris"],
                "resource_access_receipt_uris": sorted(accesses),
                "advisory_receipt_uri": receipt_uri,
                "predecessor_environment_uri": environment,
                "replacement_text": "mouse centered on table",
                "reason": "measured placement",
            }
            result = await client.call_tool("harness_prompt_revision_record_v2", revision)
            assert not result.is_error, result
            assert result.structured_content["control_kind"] == "prompt_revision"

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "mode",
    [
        "payload",
        "ttl",
        "unread",
        "duplicate_access",
        "foreign",
        "foreign_access",
        "stale_head",
        "stale_capture",
        "missing_access",
        "missing_artifact",
        "extra_access",
    ],
)
def test_v2_controls_reject_unbound_sources_without_advancing(tmp_path, mode):
    from datetime import timedelta
    from uuid import uuid4

    from self_improving.harness.mcp_adapter import MCPResourceRead, MCPToolCall

    case = native_case(tmp_path)
    adapter = create_native_harness_mcp_adapter(
        harness=case.harness,
        principal_id=case.head.principal_id,
        registry_snapshot=case.head.registry_snapshot,
    )
    args = advisory_arguments(case)
    if mode == "payload":
        args["observation"] = case.command.observation_citations[0].observation.model_dump(
            mode="json"
        )
    elif mode == "ttl":
        args["ttl_seconds"] = 300
    elif mode == "unread":
        args["resource_access_receipt_uris"] = []
    elif mode == "duplicate_access":
        args["resource_access_receipt_uris"] *= 2
    elif mode == "foreign":
        args["source_prompt_uri"] = args["source_prompt_uri"].replace(
            str(case.head.workflow_run_id), str(uuid4())
        )
    elif mode == "foreign_access":
        args["resource_access_receipt_uris"][0] = args["resource_access_receipt_uris"][0].replace(
            str(case.head.workflow_run_id), str(uuid4())
        )
    elif mode == "stale_head":
        args["expected_workflow_revision"] += 1
    elif mode == "stale_capture":
        case.clock.value += timedelta(days=1)
    elif mode == "missing_access":
        args["resource_access_receipt_uris"][0] = (
            args["resource_access_receipt_uris"][0].rsplit("/", 1)[0] + "/" + "f" * 64
        )
    elif mode == "missing_artifact":
        case.store.resolve(case.command.source_prompt).path.unlink()
    else:
        uri = f"harness://workflows/{case.head.workflow_run_id}/artifacts/{case.native.sha256}"
        delivered = adapter.read_resource(MCPResourceRead(uri))
        args["resource_access_receipt_uris"].append(delivered.access_receipt_uri)
    result = adapter.call_tool(MCPToolCall("harness_advisory_record_v2", args))
    assert result.is_error
    assert result.structured_content["schema_version"] == "harness.mcp_error.v1"
    assert (
        case.workflow.read_snapshot(
            principal_id=case.head.principal_id, workflow_run_id=case.head.workflow_run_id
        ).snapshot.revision
        == case.head.revision
    )


def test_native_factory_and_protocol_do_not_accept_forged_or_generic_controls(tmp_path):
    from self_improving.harness.mcp_adapter import HarnessMCPProtocolError, MCPToolCall
    from self_improving.harness.mcp_control_v2 import NativeHarnessMCPAdapter

    with pytest.raises(TypeError, match="sealed factory"):
        NativeHarnessMCPAdapter(_token=object(), base=object())
    case = native_case(tmp_path)
    adapter = create_native_harness_mcp_adapter(
        harness=case.harness,
        principal_id=case.head.principal_id,
        registry_snapshot=case.head.registry_snapshot,
    )
    for name in ("skill.invoke", "harness_advisory_record_v1", "harness_prompt_revision_record_v1"):
        with pytest.raises(HarnessMCPProtocolError):
            adapter.call_tool(MCPToolCall(name, {}))
