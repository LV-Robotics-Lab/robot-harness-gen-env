"""Transport-neutral S2 adapter for exact qualified Golden Workflow tools."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal, Mapping
from uuid import UUID

from pydantic import BaseModel, ConfigDict, create_model

from .golden_run import (
    GoldenRunConflictError,
    GoldenRunCorruptionError,
    GoldenRunHarness,
    GoldenRunNotFoundError,
    GoldenRunRegistryError,
)
from .schema_catalog import schema_documents, schema_model
from .schemas.common import ArtifactRef
from .schemas.workflow import RunCommandRequest, RunReadRequest


@dataclass(frozen=True)
class MCPTool:
    """One exact MCP tool projected from a frozen qualified Registry snapshot."""

    name: str
    skill_ref: str
    input_schema_id: str
    description: str
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]


@dataclass(frozen=True)
class MCPToolCall:
    """One transport-neutral call to an exact projected tool."""

    name: str
    arguments: Mapping[str, object]


@dataclass(frozen=True)
class MCPTextContent:
    """Short human-readable projection of a structured tool result."""

    text: str
    type: Literal["text"] = "text"


@dataclass(frozen=True)
class MCPResourceLink:
    """Link to an authority-backed Harness resource."""

    name: str
    uri: str
    media_type: str
    type: Literal["resource_link"] = "resource_link"


@dataclass(frozen=True)
class MCPToolResult:
    """Structured result returned to an MCP transport."""

    content: tuple[MCPTextContent | MCPResourceLink, ...]
    structured_content: Mapping[str, object]
    is_error: bool


@dataclass(frozen=True)
class MCPResourceRead:
    """Read one authority-backed Harness resource."""

    uri: str


@dataclass(frozen=True)
class MCPResourceResult:
    """Canonical text returned for a Harness resource."""

    uri: str
    media_type: str
    text: str


class HarnessMCPAdapterError(ValueError):
    """Stable S2 configuration or request failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class HarnessMCPProtocolError(LookupError):
    """Unknown MCP method target that must remain a protocol-level failure."""


_ADAPTER_TOKEN = object()
_WORKFLOW_RESOURCE = re.compile(
    r"^harness://workflows/([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$"
)


class HarnessMCPAdapter:
    """Expose a small transport-neutral interface over the authoritative Harness."""

    __slots__ = ("_harness", "_principal_id", "_registry_snapshot", "_token")

    def __init__(
        self,
        *,
        _token: object,
        harness: GoldenRunHarness,
        principal_id: str,
        registry_snapshot: ArtifactRef,
    ) -> None:
        if _token is not _ADAPTER_TOKEN:
            raise TypeError("HarnessMCPAdapter must be created by its authority factory")
        self._token = _token
        self._harness = harness
        self._principal_id = principal_id
        self._registry_snapshot = registry_snapshot

    def list_tools(self, snapshot: ArtifactRef) -> tuple[MCPTool, ...]:
        """Return exact, qualified and live tools in canonical MCP-name order."""

        if snapshot != self._registry_snapshot:
            raise HarnessMCPAdapterError(
                "HARN_REGISTRY_SNAPSHOT_MISMATCH",
                "requested Registry snapshot is not the adapter's frozen authority",
            )
        try:
            registry = self._harness._verify_registry_snapshot(snapshot)
            documents = schema_documents()
            tools: list[MCPTool] = []
            for entry in registry.qualified_skills:
                live = self._harness._execution_catalog.live_descriptors_for(entry.skill_ref)
                if len(live) != 1:
                    raise HarnessMCPAdapterError(
                        "HARN_SKILL_UNQUALIFIED",
                        "Registry Skill does not have one exact live application: "
                        f"{entry.skill_ref}",
                    )
                descriptor = self._harness._resolve_descriptor(entry.descriptor_ref)
                if live[0] != descriptor:
                    raise HarnessMCPAdapterError(
                        "HARN_DEPENDENCY_DRIFT",
                        f"Registry and live descriptor differ: {entry.skill_ref}",
                    )
                input_model = schema_model(descriptor.input_schema)
                tools.append(
                    MCPTool(
                        name=entry.mcp_tool_name,
                        skill_ref=entry.skill_ref,
                        input_schema_id=descriptor.input_schema,
                        description=(
                            f"Submit exact qualified {entry.skill_ref} "
                            "to the durable Golden Workflow."
                        ),
                        input_schema=_command_envelope_schema(entry.mcp_tool_name, input_model),
                        output_schema=_tool_result_schema(
                            documents["harness.operation_snapshot.v1"]
                        ),
                    )
                )
        except GoldenRunRegistryError as error:
            raise HarnessMCPAdapterError(error.code, str(error)) from error
        return tuple(tools)

    def call_tool(self, request: MCPToolCall) -> MCPToolResult:
        """Validate one exact command and delegate all mutation to S1."""

        try:
            tools = {tool.name: tool for tool in self.list_tools(self._registry_snapshot)}
        except HarnessMCPAdapterError as error:
            return _error_result(error.code, str(error))
        tool = tools.get(request.name)
        if tool is None:
            raise HarnessMCPProtocolError(f"unknown exact MCP tool: {request.name}")
        envelope_model = _command_envelope_model(
            tool.name,
            schema_model(tool.input_schema_id),
        )
        try:
            wire_payload = json.dumps(
                dict(request.arguments),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            envelope = envelope_model.model_validate_json(wire_payload)
            command = RunCommandRequest.model_validate(
                {
                    **envelope.model_dump(mode="json"),
                    "principal_id": self._principal_id,
                    "skill_ref": tool.skill_ref,
                }
            )
        except (TypeError, ValueError) as error:
            return _error_result("HARN_INPUT_SCHEMA_INVALID", str(error))

        try:
            operation = self._harness.submit(command)
        except (
            GoldenRunConflictError,
            GoldenRunCorruptionError,
            GoldenRunNotFoundError,
            GoldenRunRegistryError,
        ) as error:
            return _error_result(error.code, str(error))
        structured = operation.model_dump(mode="json")
        operation_uri = f"harness://workflows/{operation.workflow_run_id}"
        return MCPToolResult(
            content=(
                MCPTextContent(
                    text=(
                        f"{operation.skill_ref} operation {operation.operation_id} "
                        f"is {operation.status.value}."
                    )
                ),
                MCPResourceLink(
                    name=f"operation-{operation.operation_id}",
                    uri=operation_uri,
                    media_type="application/json",
                ),
            ),
            structured_content=structured,
            is_error=operation.status.value in {"blocked", "failed"},
        )

    def read_resource(self, request: MCPResourceRead) -> MCPResourceResult:
        """Read a workflow only through the authorized S1 interface."""

        match = _WORKFLOW_RESOURCE.fullmatch(request.uri)
        if match is None:
            raise HarnessMCPProtocolError(f"unknown Harness resource: {request.uri}")
        try:
            snapshot = self._harness.read(
                RunReadRequest(
                    schema_version="harness.run_read_request.v1",
                    principal_id=self._principal_id,
                    workflow_run_id=UUID(match.group(1)),
                )
            )
        except (GoldenRunNotFoundError, ValueError) as error:
            raise HarnessMCPProtocolError(f"unknown Harness resource: {request.uri}") from error
        text = json.dumps(
            snapshot.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
        return MCPResourceResult(uri=request.uri, media_type="application/json", text=text)


def create_harness_mcp_adapter(
    *,
    harness: GoldenRunHarness,
    principal_id: str,
    registry_snapshot: ArtifactRef,
) -> HarnessMCPAdapter:
    """Bind S2 to one exact S1 authority and frozen Registry snapshot."""

    if type(harness) is not GoldenRunHarness:
        raise TypeError("harness must be the exact GoldenRunHarness")
    if type(principal_id) is not str or not principal_id:
        raise TypeError("principal_id must be a non-empty exact string")
    adapter = HarnessMCPAdapter(
        _token=_ADAPTER_TOKEN,
        harness=harness,
        principal_id=principal_id,
        registry_snapshot=registry_snapshot,
    )
    adapter.list_tools(registry_snapshot)
    return adapter


def _command_envelope_schema(
    tool_name: str,
    input_model: type[BaseModel],
) -> dict[str, object]:
    model = _command_envelope_model(tool_name, input_model)
    document = model.model_json_schema(mode="validation")
    _strip_nested_resource_identifiers(document)
    return document


def _command_envelope_model(
    tool_name: str,
    input_model: type[BaseModel],
) -> type[BaseModel]:
    fields: dict[str, tuple[object, object]] = {}
    for name, field in RunCommandRequest.model_fields.items():
        if name in {"principal_id", "skill_ref"}:
            continue
        fields[name] = (field.annotation, deepcopy(field))
    parameters = deepcopy(RunCommandRequest.model_fields["parameters"])
    fields["parameters"] = (input_model, parameters)
    return create_model(
        "_" + tool_name + "_command",
        __config__=ConfigDict(extra="forbid", frozen=True, strict=True),
        **fields,
    )


def _error_result(code: str, message: str) -> MCPToolResult:
    structured = {
        "schema_version": "harness.mcp_error.v1",
        "code": code,
        "message": message,
    }
    return MCPToolResult(
        content=(MCPTextContent(text=f"{code}: {message}"),),
        structured_content=structured,
        is_error=True,
    )


def _tool_result_schema(operation_schema: Mapping[str, object]) -> dict[str, object]:
    return {
        "$id": "harness.mcp_exact_tool_result.v1",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "oneOf": [
            deepcopy(dict(operation_schema)),
            {
                "additionalProperties": False,
                "properties": {
                    "schema_version": {"const": "harness.mcp_error.v1", "type": "string"},
                    "code": {"minLength": 1, "type": "string"},
                    "message": {"minLength": 1, "type": "string"},
                },
                "required": ["schema_version", "code", "message"],
                "type": "object",
            },
        ],
    }


def _strip_nested_resource_identifiers(document: dict[str, object]) -> None:
    definitions = document.get("$defs")
    pending = list(definitions.values()) if isinstance(definitions, dict) else []
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            value.pop("$id", None)
            value.pop("$schema", None)
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


__all__ = [
    "HarnessMCPAdapter",
    "HarnessMCPAdapterError",
    "HarnessMCPProtocolError",
    "MCPResourceLink",
    "MCPResourceRead",
    "MCPResourceResult",
    "MCPTextContent",
    "MCPTool",
    "MCPToolCall",
    "MCPToolResult",
    "create_harness_mcp_adapter",
]
