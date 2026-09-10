"""Official MCP 2.2 transport bridge for the transport-neutral S2 adapter."""

from __future__ import annotations

from mcp import MCPError, types
from mcp.server import Server

from .mcp_adapter import (
    HarnessMCPAdapter,
    HarnessMCPAdapterError,
    HarnessMCPProtocolError,
    MCPResourceLink,
    MCPResourceRead,
    MCPTextContent,
    MCPToolCall,
)


def create_harness_mcp_server(adapter: HarnessMCPAdapter) -> Server:
    """Create a protocol server without duplicating Harness business behavior."""

    if type(adapter) is not HarnessMCPAdapter:
        raise TypeError("adapter must be the exact HarnessMCPAdapter")

    async def list_tools(_context, _params) -> types.ListToolsResult:
        projected = adapter.list_tools(adapter._registry_snapshot)
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=tool.name,
                    description=tool.description,
                    input_schema=dict(tool.input_schema),
                    output_schema=dict(tool.output_schema),
                )
                for tool in projected
            ]
        )

    async def call_tool(_context, params) -> types.CallToolResult:
        try:
            result = adapter.call_tool(
                MCPToolCall(name=params.name, arguments=params.arguments or {})
            )
        except HarnessMCPProtocolError as error:
            raise MCPError(-32602, str(error)) from error
        content: list[types.TextContent | types.ResourceLink] = []
        for item in result.content:
            if type(item) is MCPTextContent:
                content.append(types.TextContent(type="text", text=item.text))
            else:
                assert type(item) is MCPResourceLink
                content.append(
                    types.ResourceLink(
                        type="resource_link",
                        name=item.name,
                        uri=item.uri,
                        mime_type=item.media_type,
                    )
                )
        return types.CallToolResult(
            content=content,
            structured_content=dict(result.structured_content),
            is_error=result.is_error,
        )

    async def read_resource(_context, params) -> types.ReadResourceResult:
        try:
            result = adapter.read_resource(MCPResourceRead(uri=str(params.uri)))
        except HarnessMCPProtocolError as error:
            raise MCPError(-32602, str(error)) from error
        except HarnessMCPAdapterError as error:
            raise MCPError(
                -32603, f"{error.code}: Harness resource delivery failed closed"
            ) from error
        if result.text is not None:
            content: types.TextResourceContents | types.BlobResourceContents = (
                types.TextResourceContents(
                    uri=result.uri,
                    mime_type=result.media_type,
                    text=result.text,
                )
            )
        else:
            assert result.blob is not None
            content = types.BlobResourceContents(
                uri=result.uri,
                mime_type=result.media_type,
                blob=result.blob,
            )
        contents: list[types.TextResourceContents | types.BlobResourceContents] = [content]
        if result.access_receipt_text is not None:
            assert result.access_receipt_uri is not None
            contents.append(
                types.TextResourceContents(
                    uri=result.access_receipt_uri,
                    mime_type="application/json",
                    text=result.access_receipt_text,
                )
            )
        return types.ReadResourceResult(contents=contents)

    return Server(
        "robot-harness-golden",
        version="0.1.0",
        instructions=(
            "Use only the exact qualified tools listed by this server. "
            "The Harness is the sole workflow state authority."
        ),
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_read_resource=read_resource,
    )


__all__ = ["create_harness_mcp_server"]
