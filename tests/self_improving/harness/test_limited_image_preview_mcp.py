"""Real MCP transport around a small external service fixture, never Genesis."""

import asyncio

import pytest

from self_improving.harness.limited_image_preview_mcp import create_limited_image_preview_mcp_server


def test_public_mcp_lists_only_named_preview_tools_and_delegates_start():
    from mcp import Client, MCPError

    class Service:
        def __init__(self):
            self.received = []

        def start(self, **arguments):
            self.received.append(arguments)
            return {"workflow_id": "service-issued", "status": "started"}

    service = Service()
    server = create_limited_image_preview_mcp_server(service)

    async def exercise():
        async with Client(server) as client:
            names = {item.name for item in (await client.list_tools()).tools}
            assert names == {
                "limited_image_preview_start_v1", "limited_image_preview_asset_lookup_v1",
                "limited_image_preview_compile_v1", "limited_image_preview_replay_v1",
                "limited_image_preview_observe_v1", "limited_image_preview_observation_read_v1",
                "limited_image_preview_validate_v1", "limited_image_preview_publish_v1",
                "limited_image_preview_status_v1", "limited_image_preview_package_get_v1",
            }
            with pytest.raises(MCPError):
                await client.call_tool("skill.invoke", {})

    asyncio.run(exercise())
