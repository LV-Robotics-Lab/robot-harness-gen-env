"""Transport-neutral S2 adapter for exact qualified Golden Workflow tools."""

from __future__ import annotations

import json
import re
from base64 import b64encode
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Annotated, Literal, Mapping
from uuid import UUID

from pydantic import (
    UUID4,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    create_model,
    model_validator,
)

from .artifacts import ArtifactResolutionError
from .golden_run import (
    GoldenRunConflictError,
    GoldenRunCorruptionError,
    GoldenRunHarness,
    GoldenRunNotFoundError,
    GoldenRunRegistryError,
)
from .schema_catalog import schema_documents, schema_model
from .schemas.base import CanonicalSkillRef, NonNegativeInt, Sha256
from .schemas.common import ArtifactRef
from .schemas.golden_control import (
    AdvisoryRecordCommand,
    ControlCommitSnapshot,
    NoPromptFallback,
    ObservationCitation,
    PromptRevisionCommand,
    ReplacePromptPatch,
    VideoFrameToImageFallback,
)
from .schemas.workflow import (
    CapabilityProfileRef,
    IdempotencyKey,
    RunCommandRequest,
    RunReadRequest,
    RunSnapshot,
    RunStartRequest,
)
from .trusted_state import FactKey, WorldFactEvidence


@dataclass(frozen=True)
class MCPTool:
    """One exact MCP tool projected from a frozen qualified Registry snapshot."""

    name: str
    skill_ref: str | None
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
    text: str | None
    blob: str | None = None


@dataclass(frozen=True)
class MCPWorkflowSkillInputProvider:
    """One server-owned projection from a verified workflow head to Skill input."""

    skill_ref: str
    build: Callable[[RunSnapshot], BaseModel]


class HarnessMCPAdapterError(ValueError):
    """Stable S2 configuration or request failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class HarnessMCPProtocolError(LookupError):
    """Unknown MCP method target that must remain a protocol-level failure."""


class MCPInitialUserFact(BaseModel):
    """One untrusted inline fact that the adapter will freeze into Harness CAS."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    key: FactKey
    value: JsonValue
    observed_at: AwareDatetime
    valid_until: AwareDatetime | None


class MCPWorkflowCreateRequest(BaseModel):
    """Caller-owned portion of a durable workflow start request."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["harness.mcp_workflow_create_request.v1"]
    idempotency_key: IdempotencyKey
    workspace: Literal["ephemeral", "production"]
    requested_profile: CapabilityProfileRef
    initial_user_facts: tuple[MCPInitialUserFact, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def fact_keys_are_unique(self) -> "MCPWorkflowCreateRequest":
        keys = tuple(fact.key for fact in self.initial_user_facts)
        if len(keys) != len(set(keys)):
            raise ValueError("initial_user_facts must use unique keys")
        return self


_WORKFLOW_ARTIFACT_URI_PATTERN = (
    r"^harness://workflows/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
    r"/artifacts/[0-9a-f]{64}$"
)
WorkflowArtifactUri = Annotated[str, Field(pattern=_WORKFLOW_ARTIFACT_URI_PATTERN)]


class MCPObservationCitation(BaseModel):
    """Caller-visible citation using only a workflow-scoped resource URI."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    receipt_uri: WorkflowArtifactUri
    captured_at: AwareDatetime
    valid_until: AwareDatetime
    ttl_seconds: Annotated[int, Field(strict=True, ge=1, le=300)]


class MCPAdvisoryRecordRequest(BaseModel):
    """Caller-owned portion of one advisory-only workflow control."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["harness.mcp_advisory_record_request.v1"]
    command_id: UUID4
    workflow_run_id: UUID4
    idempotency_key: IdempotencyKey
    expected_workflow_revision: NonNegativeInt
    expected_state_sha256: Sha256
    expected_receipt_head_sha256: Sha256
    advisory_kind: Literal["visual_assessment", "diagnosis", "repair_proposal"]
    disposition: Literal["recommend", "abstain"]
    summary: Annotated[str, Field(min_length=1)]
    recommended_action: Literal[
        "none", "retry", "revise_prompt", "fallback_image", "repair_assets", "stop"
    ]
    source_prompt_uri: WorkflowArtifactUri
    source_prompt_revision_uri: WorkflowArtifactUri | None
    failure_receipt_uris: tuple[WorkflowArtifactUri, ...]
    observation_citations: tuple[MCPObservationCitation, ...]
    diagnostic_uris: tuple[WorkflowArtifactUri, ...]


class MCPNoPromptFallback(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["none"]


class MCPVideoFrameToImageFallback(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["video_frame_to_image"]
    source_video_uri: WorkflowArtifactUri
    selected_frame_uri: WorkflowArtifactUri
    frame_index: NonNegativeInt
    frame_sequence_manifest_uri: WorkflowArtifactUri


MCPPromptFallback = Annotated[
    MCPNoPromptFallback | MCPVideoFrameToImageFallback,
    Field(discriminator="kind"),
]


class MCPPromptRevisionRecordRequest(BaseModel):
    """Caller-owned portion of one explicit prompt-revision workflow control."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["harness.mcp_prompt_revision_record_request.v1"]
    command_id: UUID4
    workflow_run_id: UUID4
    idempotency_key: IdempotencyKey
    expected_workflow_revision: NonNegativeInt
    expected_state_sha256: Sha256
    expected_receipt_head_sha256: Sha256
    source_prompt_uri: WorkflowArtifactUri
    advisory_receipt_uri: WorkflowArtifactUri
    failure_receipt_uris: tuple[WorkflowArtifactUri, ...]
    predecessor_package_uri: WorkflowArtifactUri | None
    patch: ReplacePromptPatch
    fallback: MCPPromptFallback
    next_compile_skill_ref: CanonicalSkillRef


_ADAPTER_TOKEN = object()
_WORKFLOW_CREATE_TOOL = "harness_workflow_create_v1"
_ADVISORY_RECORD_TOOL = "harness_advisory_record_v1"
_PROMPT_REVISION_RECORD_TOOL = "harness_prompt_revision_record_v1"
_WORKFLOW_RESOURCE = re.compile(
    r"^harness://workflows/([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$"
)
_WORKFLOW_ARTIFACT_RESOURCE = re.compile(
    r"^harness://workflows/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})"
    r"/artifacts/([0-9a-f]{64})$"
)
_WORKFLOW_SKILL_INPUT_RESOURCE = re.compile(
    r"^harness://workflows/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})"
    r"/skill-inputs/([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+)/"
    r"((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))$"
)


class HarnessMCPAdapter:
    """Expose a small transport-neutral interface over the authoritative Harness."""

    __slots__ = (
        "_harness",
        "_principal_id",
        "_registry_snapshot",
        "_skill_input_providers",
        "_token",
    )

    def __init__(
        self,
        *,
        _token: object,
        harness: GoldenRunHarness,
        principal_id: str,
        registry_snapshot: ArtifactRef,
        skill_input_providers: Mapping[str, Callable[[RunSnapshot], BaseModel]],
    ) -> None:
        if _token is not _ADAPTER_TOKEN:
            raise TypeError("HarnessMCPAdapter must be created by its authority factory")
        self._token = _token
        self._harness = harness
        self._principal_id = principal_id
        self._registry_snapshot = registry_snapshot
        self._skill_input_providers = dict(skill_input_providers)

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
            tools: list[MCPTool] = [
                MCPTool(
                    name=_WORKFLOW_CREATE_TOOL,
                    skill_ref=None,
                    input_schema_id="harness.run_start_request.v1",
                    description=(
                        "Create one durable Golden Workflow under this server's frozen authority."
                    ),
                    input_schema=_workflow_start_schema(),
                    output_schema=_tool_result_schema(documents["harness.run_snapshot.v1"]),
                ),
                MCPTool(
                    name=_ADVISORY_RECORD_TOOL,
                    skill_ref=None,
                    input_schema_id="harness.mcp_advisory_record_request.v1",
                    description=(
                        "Record one advisory-only diagnosis under the current Golden Workflow head."
                    ),
                    input_schema=_control_request_schema(MCPAdvisoryRecordRequest),
                    output_schema=_tool_result_schema(
                        documents["harness.control_commit_snapshot.v1"]
                    ),
                ),
                MCPTool(
                    name=_PROMPT_REVISION_RECORD_TOOL,
                    skill_ref=None,
                    input_schema_id="harness.mcp_prompt_revision_record_request.v1",
                    description=(
                        "Record one explicit prompt revision under the current "
                        "Golden Workflow head."
                    ),
                    input_schema=_control_request_schema(MCPPromptRevisionRecordRequest),
                    output_schema=_tool_result_schema(
                        documents["harness.control_commit_snapshot.v1"]
                    ),
                ),
            ]
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
        if tool.name == _WORKFLOW_CREATE_TOOL:
            return self._call_workflow_create(request)
        if tool.name == _ADVISORY_RECORD_TOOL:
            return self._call_advisory_record(request)
        if tool.name == _PROMPT_REVISION_RECORD_TOOL:
            return self._call_prompt_revision_record(request)
        assert tool.skill_ref is not None
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

    def _call_workflow_create(self, request: MCPToolCall) -> MCPToolResult:
        try:
            wire_payload = json.dumps(
                dict(request.arguments),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            envelope = _workflow_start_model().model_validate_json(wire_payload)
            evidence = tuple(
                self._harness._publish_model(
                    WorldFactEvidence(
                        source_kind="user_input",
                        key=fact.key,
                        value=fact.value,
                        observed_at=fact.observed_at,
                        valid_until=fact.valid_until,
                    ),
                    name="mcp_user_fact.json",
                    schema_version="harness.world_fact_evidence.v1",
                )
                for fact in envelope.initial_user_facts
            )
            start = RunStartRequest(
                schema_version="harness.run_start_request.v1",
                principal_id=self._principal_id,
                idempotency_key=envelope.idempotency_key,
                workspace=envelope.workspace,
                requested_profile=envelope.requested_profile,
                initial_fact_evidence=tuple(
                    sorted(
                        evidence,
                        key=lambda artifact: (
                            artifact.sha256,
                            artifact.bytes,
                            artifact.media_type,
                            artifact.schema_version or "",
                        ),
                    )
                ),
                registry_snapshot=self._registry_snapshot,
            )
        except (TypeError, ValueError) as error:
            return _error_result("HARN_INPUT_SCHEMA_INVALID", str(error))
        try:
            snapshot = self._harness.start(start)
        except (
            ArtifactResolutionError,
            GoldenRunConflictError,
            GoldenRunCorruptionError,
            GoldenRunRegistryError,
            ValueError,
        ) as error:
            code = getattr(
                error,
                "code",
                {
                    ArtifactResolutionError: "HARN_ARTIFACT_UNAVAILABLE",
                    ValueError: "HARN_INPUT_SCHEMA_INVALID",
                }.get(type(error), "HARN_INTERNAL"),
            )
            return _error_result(code, str(error))
        structured = snapshot.model_dump(mode="json")
        workflow_uri = f"harness://workflows/{snapshot.workflow_run_id}"
        return MCPToolResult(
            content=(
                MCPTextContent(text=f"Golden Workflow {snapshot.workflow_run_id} is revision 0."),
                MCPResourceLink(
                    name=f"workflow-{snapshot.workflow_run_id}",
                    uri=workflow_uri,
                    media_type="application/json",
                ),
            ),
            structured_content=structured,
            is_error=False,
        )

    def _call_advisory_record(self, request: MCPToolCall) -> MCPToolResult:
        try:
            envelope = _parse_exact_request(request.arguments, MCPAdvisoryRecordRequest)
            workflow_id = envelope.workflow_run_id
            command = AdvisoryRecordCommand(
                schema_version="harness.advisory_record_command.v1",
                command_id=envelope.command_id,
                principal_id=self._principal_id,
                workflow_run_id=workflow_id,
                idempotency_key=envelope.idempotency_key,
                expected_workflow_revision=envelope.expected_workflow_revision,
                expected_state_sha256=envelope.expected_state_sha256,
                expected_receipt_head_sha256=envelope.expected_receipt_head_sha256,
                advisory_kind=envelope.advisory_kind,
                disposition=envelope.disposition,
                summary=envelope.summary,
                recommended_action=envelope.recommended_action,
                source_prompt=self._resolve_workflow_artifact_uri(
                    envelope.source_prompt_uri,
                    workflow_id=workflow_id,
                ),
                source_prompt_revision=(
                    None
                    if envelope.source_prompt_revision_uri is None
                    else self._resolve_workflow_artifact_uri(
                        envelope.source_prompt_revision_uri,
                        workflow_id=workflow_id,
                    )
                ),
                failure_receipts=tuple(
                    self._resolve_workflow_artifact_uri(uri, workflow_id=workflow_id)
                    for uri in envelope.failure_receipt_uris
                ),
                observation_citations=tuple(
                    ObservationCitation(
                        receipt=self._resolve_workflow_artifact_uri(
                            citation.receipt_uri,
                            workflow_id=workflow_id,
                        ),
                        captured_at=citation.captured_at,
                        valid_until=citation.valid_until,
                        ttl_seconds=citation.ttl_seconds,
                    )
                    for citation in envelope.observation_citations
                ),
                diagnostic_refs=tuple(
                    self._resolve_workflow_artifact_uri(uri, workflow_id=workflow_id)
                    for uri in envelope.diagnostic_uris
                ),
                external_agent_executed=False,
            )
            control = self._harness.submit_control(command)
        except ValidationError as error:
            return _error_result("HARN_INPUT_SCHEMA_INVALID", str(error))
        except HarnessMCPAdapterError as error:
            return _error_result(error.code, str(error))
        except (
            ArtifactResolutionError,
            GoldenRunConflictError,
            GoldenRunCorruptionError,
            GoldenRunNotFoundError,
            GoldenRunRegistryError,
        ) as error:
            return _error_result(_domain_error_code(error), str(error))
        return _control_result(control)

    def _call_prompt_revision_record(self, request: MCPToolCall) -> MCPToolResult:
        try:
            envelope = _parse_exact_request(request.arguments, MCPPromptRevisionRecordRequest)
            workflow_id = envelope.workflow_run_id
            if type(envelope.fallback) is MCPNoPromptFallback:
                fallback: NoPromptFallback | VideoFrameToImageFallback = NoPromptFallback(
                    kind="none"
                )
            else:
                assert type(envelope.fallback) is MCPVideoFrameToImageFallback
                fallback = VideoFrameToImageFallback(
                    kind="video_frame_to_image",
                    source_video=self._resolve_workflow_artifact_uri(
                        envelope.fallback.source_video_uri,
                        workflow_id=workflow_id,
                    ),
                    selected_frame=self._resolve_workflow_artifact_uri(
                        envelope.fallback.selected_frame_uri,
                        workflow_id=workflow_id,
                    ),
                    frame_index=envelope.fallback.frame_index,
                    frame_sequence_manifest=self._resolve_workflow_artifact_uri(
                        envelope.fallback.frame_sequence_manifest_uri,
                        workflow_id=workflow_id,
                    ),
                )
            command = PromptRevisionCommand(
                schema_version="harness.prompt_revision_command.v1",
                command_id=envelope.command_id,
                principal_id=self._principal_id,
                workflow_run_id=workflow_id,
                idempotency_key=envelope.idempotency_key,
                expected_workflow_revision=envelope.expected_workflow_revision,
                expected_state_sha256=envelope.expected_state_sha256,
                expected_receipt_head_sha256=envelope.expected_receipt_head_sha256,
                source_prompt=self._resolve_workflow_artifact_uri(
                    envelope.source_prompt_uri,
                    workflow_id=workflow_id,
                ),
                advisory_receipt=self._resolve_workflow_artifact_uri(
                    envelope.advisory_receipt_uri,
                    workflow_id=workflow_id,
                ),
                failure_receipts=tuple(
                    self._resolve_workflow_artifact_uri(uri, workflow_id=workflow_id)
                    for uri in envelope.failure_receipt_uris
                ),
                predecessor_package=(
                    None
                    if envelope.predecessor_package_uri is None
                    else self._resolve_workflow_artifact_uri(
                        envelope.predecessor_package_uri,
                        workflow_id=workflow_id,
                    )
                ),
                patch=envelope.patch,
                fallback=fallback,
                next_compile_skill_ref=envelope.next_compile_skill_ref,
                external_agent_executed=False,
            )
            control = self._harness.submit_control(command)
        except ValidationError as error:
            return _error_result("HARN_INPUT_SCHEMA_INVALID", str(error))
        except HarnessMCPAdapterError as error:
            return _error_result(error.code, str(error))
        except (
            ArtifactResolutionError,
            GoldenRunConflictError,
            GoldenRunCorruptionError,
            GoldenRunNotFoundError,
            GoldenRunRegistryError,
        ) as error:
            return _error_result(_domain_error_code(error), str(error))
        return _control_result(control)

    def _resolve_workflow_artifact_uri(
        self,
        uri: str,
        *,
        workflow_id: UUID,
    ) -> ArtifactRef:
        match = _WORKFLOW_ARTIFACT_RESOURCE.fullmatch(uri)
        if match is None or UUID(match.group(1)) != workflow_id:
            raise HarnessMCPAdapterError(
                "HARN_INPUT_SCHEMA_INVALID",
                "resource URI is outside the command workflow",
            )
        ref, _payload = self._harness.read_workflow_artifact(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id=self._principal_id,
                workflow_run_id=workflow_id,
            ),
            sha256=match.group(2),
        )
        return ref

    def read_resource(self, request: MCPResourceRead) -> MCPResourceResult:
        """Read a workflow only through the authorized S1 interface."""

        match = _WORKFLOW_RESOURCE.fullmatch(request.uri)
        artifact_match = _WORKFLOW_ARTIFACT_RESOURCE.fullmatch(request.uri)
        skill_input_match = _WORKFLOW_SKILL_INPUT_RESOURCE.fullmatch(request.uri)
        if artifact_match is not None:
            return self._read_workflow_artifact_resource(request, artifact_match.groups())
        if skill_input_match is not None:
            return self._read_skill_input_resource(request, skill_input_match.groups())
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
        text = (
            json.dumps(
                snapshot.model_dump(mode="json"),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        return MCPResourceResult(uri=request.uri, media_type="application/json", text=text)

    def _read_skill_input_resource(
        self,
        request: MCPResourceRead,
        identity: tuple[str, str, str],
    ) -> MCPResourceResult:
        workflow_id, skill_id, version = identity
        provider = self._skill_input_providers.get(f"{skill_id}@{version}")
        if provider is None:
            raise HarnessMCPProtocolError(f"unknown Harness resource: {request.uri}")
        try:
            snapshot = self._harness.read(
                RunReadRequest(
                    schema_version="harness.run_read_request.v1",
                    principal_id=self._principal_id,
                    workflow_run_id=UUID(workflow_id),
                )
            )
            value = provider(snapshot)
        except (GoldenRunNotFoundError, TypeError, ValueError) as error:
            raise HarnessMCPProtocolError(f"unknown Harness resource: {request.uri}") from error
        text = (
            json.dumps(
                value.model_dump(mode="json"),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        return MCPResourceResult(uri=request.uri, media_type="application/json", text=text)

    def _read_workflow_artifact_resource(
        self,
        request: MCPResourceRead,
        identity: tuple[str, str],
    ) -> MCPResourceResult:
        workflow_id, digest = identity
        read_request = RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=self._principal_id,
            workflow_run_id=UUID(workflow_id),
        )
        try:
            ref, payload = self._harness.read_workflow_artifact(
                read_request,
                sha256=digest,
            )
        except GoldenRunNotFoundError as error:
            raise HarnessMCPProtocolError(f"unknown Harness resource: {request.uri}") from error
        if ref.media_type in {"application/json", "application/x-ndjson"}:
            text = payload.decode("utf-8")
            return MCPResourceResult(uri=request.uri, media_type=ref.media_type, text=text)
        return MCPResourceResult(
            uri=request.uri,
            media_type=ref.media_type,
            text=None,
            blob=b64encode(payload).decode("ascii"),
        )


def create_harness_mcp_adapter(
    *,
    harness: GoldenRunHarness,
    principal_id: str,
    registry_snapshot: ArtifactRef,
    skill_input_providers: tuple[MCPWorkflowSkillInputProvider, ...] = (),
) -> HarnessMCPAdapter:
    """Bind S2 to one exact S1 authority and frozen Registry snapshot."""

    if type(harness) is not GoldenRunHarness:
        raise TypeError("harness must be the exact GoldenRunHarness")
    if type(principal_id) is not str or not principal_id:
        raise TypeError("principal_id must be a non-empty exact string")
    if any(type(item) is not MCPWorkflowSkillInputProvider for item in skill_input_providers):
        raise TypeError("skill_input_providers must contain exact provider records")
    provider_refs = tuple(item.skill_ref for item in skill_input_providers)
    if len(provider_refs) != len(set(provider_refs)):
        raise ValueError("skill_input_providers must use unique Skill refs")
    adapter = HarnessMCPAdapter(
        _token=_ADAPTER_TOKEN,
        harness=harness,
        principal_id=principal_id,
        registry_snapshot=registry_snapshot,
        skill_input_providers={item.skill_ref: item.build for item in skill_input_providers},
    )
    live_refs = {
        tool.skill_ref
        for tool in adapter.list_tools(registry_snapshot)
        if tool.skill_ref is not None
    }
    if not set(provider_refs).issubset(live_refs):
        raise ValueError("skill_input_providers must target exact live Skills")
    return adapter


def _command_envelope_schema(
    tool_name: str,
    input_model: type[BaseModel],
) -> dict[str, object]:
    model = _command_envelope_model(tool_name, input_model)
    document = model.model_json_schema(mode="validation")
    _strip_nested_resource_identifiers(document)
    return document


def _workflow_start_schema() -> dict[str, object]:
    document = _workflow_start_model().model_json_schema(mode="validation")
    _strip_nested_resource_identifiers(document)
    return document


def _control_request_schema(model: type[BaseModel]) -> dict[str, object]:
    document = model.model_json_schema(mode="validation")
    _strip_nested_resource_identifiers(document)
    return document


def _parse_exact_request(
    arguments: Mapping[str, object],
    model: type[BaseModel],
) -> MCPAdvisoryRecordRequest | MCPPromptRevisionRecordRequest:
    try:
        wire_payload = json.dumps(
            dict(arguments),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise HarnessMCPAdapterError("HARN_INPUT_SCHEMA_INVALID", str(error)) from error
    value = model.model_validate_json(wire_payload)
    return value


def _domain_error_code(error: Exception) -> str:
    return getattr(
        error,
        "code",
        {ArtifactResolutionError: "HARN_ARTIFACT_UNAVAILABLE"}.get(type(error), "HARN_INTERNAL"),
    )


def _control_result(control: ControlCommitSnapshot) -> MCPToolResult:
    workflow_uri = f"harness://workflows/{control.workflow_run_id}"
    return MCPToolResult(
        content=(
            MCPTextContent(
                text=(
                    f"{control.control_kind} control {control.command_id} committed revision "
                    f"{control.committed_revision}."
                )
            ),
            MCPResourceLink(
                name=f"workflow-{control.workflow_run_id}",
                uri=workflow_uri,
                media_type="application/json",
            ),
        ),
        structured_content=control.model_dump(mode="json"),
        is_error=False,
    )


def _workflow_start_model() -> type[BaseModel]:
    return MCPWorkflowCreateRequest


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
    "MCPAdvisoryRecordRequest",
    "MCPObservationCitation",
    "MCPPromptRevisionRecordRequest",
    "MCPWorkflowCreateRequest",
    "MCPTextContent",
    "MCPTool",
    "MCPToolCall",
    "MCPToolResult",
    "create_harness_mcp_adapter",
]
