"""Exact Skill MCP conversion; the preview service owns all state and decisions."""

import argparse
import asyncio
import contextlib
import copy
import json
import math
import sys
from pathlib import Path
from typing import Annotated, Literal, Protocol
from uuid import UUID

from mcp import MCPError, types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .mcp_control_v2 import MCPAdvisoryRecordRequestV2, MCPPromptRevisionRecordRequestV2
from .schemas.common import ArtifactRef
from .schemas.golden_control_v2 import ControlCommitSnapshotV2
from .schemas.golden_control_v3_snapshot import ControlCommitSnapshotV3
from .schemas.limited_image_preview_service import PreviewServiceResult


class LimitedImagePreviewService(Protocol):
    def start(self, **arguments) -> dict: ...
    def asset_lookup(self, **arguments) -> dict: ...
    def compile(self, **arguments) -> dict: ...
    def replay(self, **arguments) -> dict: ...
    def observe(self, **arguments) -> dict: ...
    def validate(self, **arguments) -> dict: ...
    def publish(self, **arguments) -> dict: ...
    def status(self, **arguments) -> dict: ...
    def package_get(self, **arguments) -> dict: ...
    def submit_control(self, command: BaseModel) -> dict: ...
    def read_resource(self, workflow_id: str, uri: str) -> dict: ...


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class PreviewWorkflowInput(_Input):
    workflow_id: str = Field(min_length=1)


class PreviewOperationInput(PreviewWorkflowInput):
    idempotency_key: str = Field(min_length=1)


class PreviewStartInput(_Input):
    raw_image_ref: ArtifactRef | None = None
    raw_image_path: str | None = None
    user_text: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)

    @model_validator(mode="after")
    def one_raw_image(self):
        if (self.raw_image_ref is None) == (self.raw_image_path is None):
            raise ValueError("provide exactly one raw image path or CAS reference")
        if self.raw_image_path is not None:
            path = Path(self.raw_image_path)
            if not path.is_absolute() or ".." in path.parts:
                raise ValueError("raw image path must be absolute")
        if self.raw_image_ref is not None and (
            self.raw_image_ref.media_type not in {"image/png", "image/jpeg", "image/webp"}
            or self.raw_image_ref.uri != "artifact://sha256/" + self.raw_image_ref.sha256
        ):
            raise ValueError("raw image requires exact image CAS identity")
        return self


class PreviewLookupInput(PreviewWorkflowInput):
    query: Literal["mouse"] = "mouse"


class PreviewCompileV2Input(PreviewOperationInput):
    asset_id: str = Field(min_length=1)


Coordinate = Annotated[float, Field(strict=True, allow_inf_nan=False, ge=-10, le=10)]
Yaw = Annotated[float, Field(strict=True, allow_inf_nan=False, ge=-math.pi, le=math.pi)]
Extent = Annotated[float, Field(strict=True, allow_inf_nan=False, gt=0.02, le=10)]


class PreviewCompileV3Input(PreviewCompileV2Input):
    position_x_m: Coordinate
    position_y_m: Coordinate
    yaw_rad: Yaw


class PreviewValidateInput(PreviewOperationInput):
    expected_position_x_m: Coordinate | None = None
    expected_position_y_m: Coordinate | None = None
    expected_yaw_rad: Yaw | None = None

    @model_validator(mode="after")
    def complete_semantic_target(self):
        supplied = (
            self.expected_position_x_m is not None,
            self.expected_position_y_m is not None,
            self.expected_yaw_rad is not None,
        )
        if any(supplied) and not all(supplied):
            raise ValueError("semantic target requires all expected XY/yaw fields or none")
        return self


class MCPPromptRevisionRecordRequestV3(MCPPromptRevisionRecordRequestV2):
    schema_version: Literal["harness.mcp_prompt_revision_record_request.v3"]
    position_xy_m: tuple[Coordinate, Coordinate]
    yaw_rad: Yaw
    table_center_m: tuple[Coordinate, Coordinate, Coordinate] | None = None
    table_size_m: tuple[Extent, Extent, Extent] | None = None


_TOOLS = {
    "image2env_preview_start_v1": ("start", PreviewStartInput, None),
    "image2env_preview_asset_lookup_v1": ("asset_lookup", PreviewLookupInput, None),
    "image2env_compile_v2_0_0": ("compile", PreviewCompileV2Input, "image2env.compile@2.0.0"),
    "image2env_compile_v3_0_0": ("compile", PreviewCompileV3Input, "image2env.compile@3.0.0"),
    "image2env_replay_v2_0_0": ("replay", PreviewOperationInput, "image2env.replay@2.0.0"),
    "image2env_replay_v3_0_0": ("replay", PreviewOperationInput, "image2env.replay@3.0.0"),
    "genesis_observe_v3_0_0": ("observe", PreviewOperationInput, None),
    "image2env_validate_v3_0_0": ("validate", PreviewValidateInput, None),
    "harness_advisory_record_v2": ("submit_control", MCPAdvisoryRecordRequestV2, None),
    "harness_prompt_revision_record_v3": ("submit_control", MCPPromptRevisionRecordRequestV3, None),
    "image2env_preview_publish_v1_0_0": ("publish", PreviewOperationInput, None),
    "image2env_preview_status_v1": ("status", PreviewWorkflowInput, None),
    "image2env_preview_package_get_v1": ("package_get", PreviewWorkflowInput, None),
}
_CONTROL_OUTPUTS = {
    "harness_advisory_record_v2": ControlCommitSnapshotV2,
    "harness_prompt_revision_record_v3": ControlCommitSnapshotV3,
}


def _mcp_schema(model):
    """Keep generated root-local refs rooted at the tool's schema document."""
    schema = copy.deepcopy(model.model_json_schema())

    def visit(value, root=False):
        if isinstance(value, dict):
            if not root:
                value.pop("$id", None)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema, root=True)
    return schema


def create_limited_image_preview_mcp_server(service: LimitedImagePreviewService) -> Server:
    """Convert strict JSON to explicit service functions without filling any proposal."""

    async def list_tools(_context, _params):
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=name,
                    description="Limited mouse-only exact-reuse preview: " + (skill or method),
                    input_schema=_mcp_schema(model),
                    output_schema=_mcp_schema(_CONTROL_OUTPUTS.get(name, PreviewServiceResult)),
                )
                for name, (method, model, skill) in _TOOLS.items()
            ]
        )

    async def call_tool(_context, params):
        if params.name not in _TOOLS:
            raise MCPError(-32602, "unknown exact preview tool")
        method, model, skill = _TOOLS[params.name]
        try:
            request = model.model_validate_json(json.dumps(params.arguments or {}, allow_nan=False))
        except (ValueError, TypeError) as error:
            raise MCPError(-32602, str(error)) from error
        if method == "submit_control":
            result = service.submit_control(request)
        else:
            arguments = request.model_dump(mode="python", exclude_none=True)
            if skill is not None:
                arguments["expected_skill_ref"] = skill
            result = getattr(service, method)(**arguments)
        if type(result) is not dict:
            raise MCPError(-32603, "preview service must return a JSON object")
        try:
            output = _CONTROL_OUTPUTS.get(params.name, PreviewServiceResult).model_validate_json(
                json.dumps(result)
            )
        except (ValueError, TypeError) as error:
            raise MCPError(-32603, "service output does not match exact version") from error
        try:
            raw = json.dumps(result, allow_nan=False, sort_keys=True, separators=(",", ":"))
        except (ValueError, TypeError) as error:
            raise MCPError(-32603, "preview service returned non-JSON data") from error
        content = [types.TextContent(type="text", text=raw)]
        if isinstance(output, PreviewServiceResult):
            if (
                method in {"observe", "validate", "publish", "package_get"}
                and not output.resource_links
            ):
                raise MCPError(-32603, "service evidence result requires actual resource links")
            content.extend(
                types.ResourceLink(
                    type="resource_link", name=link.name, uri=link.uri, mime_type=link.mime_type
                )
                for link in output.resource_links
            )
        return types.CallToolResult(
            content=content,
            structured_content=result,
            is_error=result.get("status") in {"failed", "blocked", "unsupported"},
        )

    async def read_resource(_context, params):
        uri = str(params.uri)
        if not uri.startswith("harness://workflows/"):
            raise MCPError(-32602, "only workflow-owned resources are exposed")
        try:
            workflow_id = uri.split("/")[3]
            if str(UUID(workflow_id)) != workflow_id:
                raise ValueError("noncanonical workflow identity")
        except (IndexError, ValueError) as error:
            raise MCPError(-32602, "resource requires canonical workflow identity") from error
        result = service.read_resource(workflow_id, uri)
        return types.ReadResourceResult.model_validate(result)

    return Server(
        "limited-image-preview",
        instructions=(
            "Use exact named Skills. Service owns workflow state and publication. "
            "Scope is one dynamic mouse on a finite table, bounded XY/yaw, exact asset reuse. "
            "Never substitute an unsupported request. No robot-policy qualification is claimed."
        ),
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_read_resource=read_resource,
    )


async def serve_limited_image_preview_stdio(service: LimitedImagePreviewService):
    async with stdio_server() as (read_stream, write_stream):
        with contextlib.redirect_stdout(sys.stderr):
            server = create_limited_image_preview_mcp_server(service)
            await server.run(read_stream, write_stream, server.create_initialization_options())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.settings.is_absolute():
        parser.error("settings must be an absolute path")
    # Fixed assembly only; never import a caller-selected module.
    from .limited_image_preview_service import create_limited_image_preview_service

    with contextlib.redirect_stdout(sys.stderr):
        service = create_limited_image_preview_service(args.settings)
    asyncio.run(serve_limited_image_preview_stdio(service))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
