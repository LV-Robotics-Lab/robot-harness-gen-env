"""Real MCP transport around a small external service fixture, never Genesis."""

import asyncio

import pytest

from self_improving.harness.limited_image_preview_mcp import create_limited_image_preview_mcp_server

WORKFLOW = "123e4567-e89b-42d3-a456-426614174000"


def test_preview_process_inspection_rejects_caller_constructed_record():
    from self_improving.harness.codex_mcp_process import inspect_preview_process_recording

    with pytest.raises(TypeError, match="fixed-runner"):
        inspect_preview_process_recording({"exit_code": 0})


def test_validate_forwards_explicit_target_and_preserves_semantic_failure():
    from mcp import Client

    class Service:
        def validate(self, **arguments):
            assert arguments == {
                "workflow_id": WORKFLOW,
                "idempotency_key": "target",
                "expected_position_x_m": 0.1,
                "expected_position_y_m": 0.2,
                "expected_yaw_rad": 0.3,
            }
            return {
                "workflow_id": WORKFLOW,
                "revision": 5,
                "stage": "validate",
                "status": "failed",
                "failure_code": "semantic_mismatch",
                "failure_message": "requested placement differs from actual scene",
                "resource_links": [
                    {
                        "uri": f"harness://workflows/{WORKFLOW}/failure",
                        "name": "semantic failure",
                        "mime_type": "application/json",
                    }
                ],
            }

    async def exercise():
        async with Client(create_limited_image_preview_mcp_server(Service())) as client:
            result = await client.call_tool(
                "image2env_validate_v3_0_0",
                {
                    "workflow_id": WORKFLOW,
                    "idempotency_key": "target",
                    "expected_position_x_m": 0.1,
                    "expected_position_y_m": 0.2,
                    "expected_yaw_rad": 0.3,
                },
            )
            assert result.is_error
            assert result.structured_content["failure_code"] == "semantic_mismatch"
            assert result.content[1].type == "resource_link"

    asyncio.run(exercise())


def test_public_mcp_lists_only_named_preview_tools_and_delegates_start(tmp_path):
    from mcp import Client, MCPError

    from tests.self_improving.harness.test_golden_control_v2_runtime import native_case

    head = native_case(tmp_path).head
    payload = {
        "workflow_id": str(head.workflow_run_id),
        "status": "active",
        "revision": head.revision,
        "stage": "start",
        "snapshot": head.model_dump(mode="json"),
    }

    class Service:
        def __init__(self):
            self.received = []

        def start(self, **arguments):
            self.received.append(arguments)
            return payload

        def status(self, **arguments):
            assert arguments == {"workflow_id": str(head.workflow_run_id)}
            return {**payload, "stage": "status"}

    service = Service()
    server = create_limited_image_preview_mcp_server(service)

    async def exercise():
        async with Client(server) as client:
            names = {item.name for item in (await client.list_tools()).tools}
            assert names == {
                "image2env_preview_start_v1",
                "image2env_preview_asset_lookup_v1",
                "image2env_compile_v2_0_0",
                "image2env_compile_v3_0_0",
                "image2env_replay_v2_0_0",
                "image2env_replay_v3_0_0",
                "genesis_observe_v3_0_0",
                "image2env_validate_v3_0_0",
                "image2env_preview_publish_v1_0_0",
                "image2env_preview_status_v1",
                "image2env_preview_package_get_v1",
                "harness_advisory_record_v2",
                "harness_prompt_revision_record_v3",
            }
            with pytest.raises(MCPError):
                await client.call_tool("skill.invoke", {})
            result = await client.call_tool(
                "image2env_preview_start_v1",
                {
                    "raw_image_path": "/operator/mouse.png",
                    "user_text": "mouse on table",
                    "idempotency_key": "fresh",
                },
            )
            assert result.structured_content == payload
            result = await client.call_tool(
                "image2env_preview_status_v1",
                {
                    "workflow_id": str(head.workflow_run_id),
                },
            )
            assert result.structured_content == {**payload, "stage": "status"}
            assert service.received == [
                {
                    "raw_image_path": "/operator/mouse.png",
                    "user_text": "mouse on table",
                    "idempotency_key": "fresh",
                }
            ]

    asyncio.run(exercise())


def test_preview_codex_invocation_attaches_raw_image_and_does_not_need_proposal(tmp_path):
    from PIL import Image

    from self_improving.harness.codex_mcp_acceptance import prepare_limited_preview_codex_invocation

    for name in ("codex", "python", "settings.json"):
        (tmp_path / name).write_bytes(b"fixture")
    Image.new("RGB", (2, 2)).save(tmp_path / "mouse.png")
    invocation = prepare_limited_preview_codex_invocation(
        codex_executable=tmp_path / "codex",
        server_python=tmp_path / "python",
        server_settings=tmp_path / "settings.json",
        server_workdir=tmp_path,
        model="test",
        case="preview_normal",
        objective="mouse on table",
        image_path=tmp_path / "mouse.png",
    )
    assert invocation.argv[invocation.argv.index("-i") + 1] == str(tmp_path / "mouse.png")
    assert "image2env_compile_v3_0_0" in invocation.prompt
    assert "Compile placement arguments must be your explicit tool arguments" in invocation.prompt
    assert invocation.image_sha256 is not None
    assert invocation.server_module == "self_improving.harness.limited_image_preview_mcp"


def test_nested_operation_output_and_public_schema_identity_are_preserved(tmp_path):
    from mcp import Client

    from self_improving.harness.schemas.limited_image_preview_service import PreviewServiceResult
    from self_improving.harness.schemas.workflow import OperationSnapshot
    from tests.self_improving.harness.test_golden_control_v2_runtime import native_case

    head = native_case(tmp_path).head
    original = PreviewServiceResult.model_json_schema()
    operation = OperationSnapshot.model_validate_json(
        __import__("json").dumps(
            {
                "schema_version": "harness.operation_snapshot.v1",
                "operation_id": WORKFLOW,
                "workflow_run_id": str(head.workflow_run_id),
                "principal_id": head.principal_id,
                "idempotency_key": "running",
                "skill_ref": "image2env.replay@2.0.0",
                "command": head.receipt_head.model_copy(
                    update={"schema_version": "harness.run_command_request.v1"}
                ).model_dump(mode="json"),
                "qualified_execution": head.receipt_head.model_copy(
                    update={"schema_version": "harness.qualified_skill_execution.v2"}
                ).model_dump(mode="json"),
                "status": "running",
                "base_revision": head.revision,
                "committed_revision": None,
                "child_run_id": WORKFLOW,
                "tool_result": None,
                "started_at": head.started_at.isoformat(),
                "ended_at": None,
            }
        )
    )

    class Service:
        def replay(self, **arguments):
            return {
                "workflow_id": str(head.workflow_run_id),
                "revision": head.revision,
                "stage": "replay",
                "status": "running",
                "operation": operation.model_dump(mode="json"),
            }

    async def exercise():
        async with Client(create_limited_image_preview_mcp_server(Service())) as client:
            tools = (await client.list_tools()).tools
            control = next(
                item for item in tools if item.name == "harness_prompt_revision_record_v3"
            )
            assert control.output_schema["$id"] == "harness.control_commit_snapshot.v3"
            result = await client.call_tool(
                "image2env_replay_v2_0_0",
                {
                    "workflow_id": str(head.workflow_run_id),
                    "idempotency_key": "running",
                },
            )
            assert result.structured_content["operation"] == operation.model_dump(mode="json")

    asyncio.run(exercise())
    assert PreviewServiceResult.model_json_schema() == original
    assert original["$defs"]["RunSnapshot"]["$id"] == "harness.run_snapshot.v1"


@pytest.mark.parametrize(
    "tool,args",
    [
        ("image2env_preview_start_v1", {"user_text": "mouse", "idempotency_key": "x"}),
        (
            "image2env_preview_start_v1",
            {"raw_image_path": "relative.png", "user_text": "mouse", "idempotency_key": "x"},
        ),
        ("image2env_preview_asset_lookup_v1", {"workflow_id": "w", "query": "cup"}),
        (
            "image2env_compile_v2_0_0",
            {"workflow_id": "w", "asset_id": "mouse", "idempotency_key": "x", "position_x_m": 0.2},
        ),
        (
            "image2env_compile_v3_0_0",
            {"workflow_id": "w", "asset_id": "mouse", "idempotency_key": "x"},
        ),
        (
            "image2env_compile_v3_0_0",
            {
                "workflow_id": "w",
                "asset_id": "mouse",
                "idempotency_key": "x",
                "position_x_m": True,
                "position_y_m": 0.0,
                "yaw_rad": 0.0,
            },
        ),
        (
            "image2env_compile_v3_0_0",
            {
                "workflow_id": "w",
                "asset_id": "mouse",
                "idempotency_key": "x",
                "position_x_m": 11.0,
                "position_y_m": 0.0,
                "yaw_rad": 0.0,
            },
        ),
        (
            "image2env_replay_v2_0_0",
            {"workflow_id": "w", "idempotency_key": "x", "expected_skill_ref": "foreign@9.0.0"},
        ),
        (
            "image2env_validate_v3_0_0",
            {"workflow_id": "w", "idempotency_key": "x", "expected_position_x_m": 0.2},
        ),
    ],
)
def test_out_of_scope_or_invented_wire_arguments_do_not_call_service(tool, args):
    from mcp import Client, MCPError

    class Service:
        def __getattr__(self, name):
            raise AssertionError("invalid protocol must not call service: " + name)

    async def exercise():
        async with Client(create_limited_image_preview_mcp_server(Service())) as client:
            with pytest.raises(MCPError):
                await client.call_tool(tool, args)

    asyncio.run(exercise())


def test_repair_placement_is_forwarded_exactly_and_tool_skill_cannot_be_forged():
    from mcp import Client

    class Service:
        def compile(self, **arguments):
            self.received = arguments
            return {
                "workflow_id": arguments["workflow_id"],
                "status": "succeeded",
                "stage": "compile",
                "revision": 1,
            }

    service = Service()
    args = {
        "workflow_id": WORKFLOW,
        "asset_id": "mouse",
        "idempotency_key": "repair",
        "position_x_m": 0.2,
        "position_y_m": -0.1,
        "yaw_rad": 0.5,
    }

    async def exercise():
        async with Client(create_limited_image_preview_mcp_server(service)) as client:
            result = await client.call_tool("image2env_compile_v3_0_0", args)
            assert result.structured_content == {
                "workflow_id": WORKFLOW,
                "status": "succeeded",
                "stage": "compile",
                "revision": 1,
            }

    asyncio.run(exercise())
    assert service.received == {**args, "expected_skill_ref": "image2env.compile@3.0.0"}


def test_resource_reads_delegate_payload_and_access_receipt_without_own_state():
    from mcp import Client, MCPError

    uri = f"harness://workflows/{WORKFLOW}/artifacts/" + "a" * 64

    class Service:
        def read_resource(self, workflow_id, resource_uri):
            assert workflow_id == WORKFLOW
            assert resource_uri == uri
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mime_type": "application/json",
                        "text": '{"fixture":"actual service bytes"}',
                    },
                    {
                        "uri": f"harness://workflows/{WORKFLOW}/resource-access/" + "b" * 64,
                        "mime_type": "application/json",
                        "text": '{"fixture":"service audit"}',
                    },
                ]
            }

    async def exercise():
        async with Client(create_limited_image_preview_mcp_server(Service())) as client:
            result = await client.read_resource(uri)
            assert result.contents[0].text == '{"fixture":"actual service bytes"}'
            assert "resource-access" in str(result.contents[1].uri)
            with pytest.raises(MCPError):
                await client.read_resource("file:///not-exposed")

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "stage,tool",
    [
        ("observe", "genesis_observe_v3_0_0"),
        ("validate", "image2env_validate_v3_0_0"),
        ("publish", "image2env_preview_publish_v1_0_0"),
        ("package_get", "image2env_preview_package_get_v1"),
    ],
)
def test_evidence_results_are_real_mcp_resource_links_with_strict_output(stage, tool):
    from mcp import Client

    link = {
        "uri": f"harness://workflows/{WORKFLOW}/artifacts/" + "a" * 64,
        "name": "actual-service-artifact",
        "mime_type": "application/json",
    }

    class Service:
        def __getattr__(self, method):
            assert method == stage
            return lambda **args: {
                "workflow_id": args["workflow_id"],
                "revision": 2,
                "stage": stage,
                "status": "succeeded",
                "resource_links": [link],
            }

    async def exercise():
        async with Client(create_limited_image_preview_mcp_server(Service())) as client:
            args = {"workflow_id": WORKFLOW}
            if stage != "package_get":
                args["idempotency_key"] = "request"
            result = await client.call_tool(tool, args)
            assert result.content[1].type == "resource_link"
            assert str(result.content[1].uri) == link["uri"]
            output = next(
                item for item in (await client.list_tools()).tools if item.name == tool
            ).output_schema
            assert output["additionalProperties"] is False

    asyncio.run(exercise())


def test_preview_transcript_keeps_exact_skills_and_actual_mcp_duration():
    from self_improving.harness.codex_mcp_acceptance import inspect_codex_transcript
    from tests.self_improving.harness.test_codex_mcp_acceptance import lines, protocol_fixture

    events, wire = protocol_fixture()
    names = [
        "image2env_preview_start_v1",
        "image2env_preview_asset_lookup_v1",
        "image2env_compile_v2_0_0",
        "image2env_replay_v2_0_0",
        "genesis_observe_v3_0_0",
        "image2env_validate_v3_0_0",
        "image2env_preview_publish_v1_0_0",
        "image2env_preview_package_get_v1",
    ]
    import copy

    new_events, new_wire = events[:2], []
    for index, name in enumerate(names):
        start = copy.deepcopy(events[2])
        end = copy.deepcopy(events[3])
        for event in (start, end):
            event["item"].update(id=f"new-{index}", tool=name)
        request, response = copy.deepcopy(wire[0]), copy.deepcopy(wire[1])
        request["message"]["id"] = response["message"]["id"] = index
        request["message"]["params"]["name"] = name
        request["monotonic_ns"] = index * 10_000_000_000
        response["monotonic_ns"] = request["monotonic_ns"] + 2_000_000_000
        new_events.extend((start, end))
        new_wire.extend((request, response))
    new_events.extend(events[-2:])
    new_wire.extend(wire[-2:])
    result = inspect_codex_transcript(lines(new_events), lines(new_wire), case="preview_normal")
    assert result.mcp_durations_seconds == (2.0,) * 8
    assert tuple(call["tool"] for call in result.calls) == tuple(names)


def test_preview_ttl_margin_uses_actually_delivered_capture_and_control_commit(tmp_path):
    import json

    from self_improving.harness.codex_mcp_acceptance import (
        CodexTranscriptInspection,
        preview_observation_consumption_margins,
    )
    from tests.self_improving.harness.test_golden_control_v2_runtime import native_case

    case = native_case(tmp_path)
    control = case.harness.submit_control(case.command)
    citation = case.command.observation_citations[0]
    capture = citation.observation
    observe = {
        "workflow_id": str(case.head.workflow_run_id),
        "revision": 1,
        "stage": "observe",
        "status": "succeeded",
        "observation": {
            "receipt": citation.receipt.model_dump(mode="json"),
            "image": capture.image.model_dump(mode="json"),
            "captured_at": capture.captured_at.isoformat(),
            "expires_at": capture.expires_at.isoformat(),
            "authority": capture.authority,
        },
    }
    calls = (
        {
            "tool": "genesis_observe_v3_0_0",
            "arguments": {},
            "result": {"structured_content": observe},
        },
        {
            "tool": "harness_advisory_record_v2",
            "arguments": {
                "observation_receipt_uris": [
                    f"harness://workflows/{case.head.workflow_run_id}/artifacts/{citation.receipt.sha256}"
                ]
            },
            "result": {"structured_content": control.model_dump(mode="json")},
        },
    )
    for ref in case.command.resource_access_receipts:
        _, audit = case.harness.read_resource_access_receipt(case.read, sha256=ref.sha256)
        delivery = json.loads(audit)
        if delivery["resource"]["sha256"] == citation.receipt.sha256:
            _, raw = case.harness.read_workflow_artifact(case.read, sha256=citation.receipt.sha256)
            reads = (
                {
                    "uri": delivery["resource_uri"],
                    "after_calls": 1,
                    "result": {"contents": [{"text": raw.decode()}, {"text": audit.decode()}]},
                },
            )
            break
    inspection = CodexTranscriptInspection(
        calls, reads, "a" * 64, "b" * 64, mcp_durations_seconds=(2.0, 1.0)
    )
    result = preview_observation_consumption_margins(inspection)
    margin = result["control_ttl_margins"][0]
    assert (
        margin["ttl_remaining_seconds"]
        == (capture.expires_at - control.committed_at).total_seconds()
    )
    assert margin["captured_at"] == capture.captured_at.isoformat()
    from dataclasses import replace

    with pytest.raises(ValueError, match="never read"):
        preview_observation_consumption_margins(replace(inspection, resource_reads=()))
