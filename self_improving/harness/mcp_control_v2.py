"""Native MCP controls derive citations from audited workflow resource deliveries."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import UUID4, BaseModel, ConfigDict, Field, ValidationError

from .artifacts import ArtifactResolutionError
from .golden_run import (
    GoldenRunConflictError,
    GoldenRunCorruptionError,
    GoldenRunHarness,
    GoldenRunNotFoundError,
    GoldenRunRegistryError,
)
from .mcp_adapter import (
    HarnessMCPAdapterError,
    HarnessMCPProtocolError,
    MCPTool,
    MCPToolCall,
    MCPWorkflowSkillInputProvider,
    WorkflowArtifactUri,
    WorkflowResourceAccessUri,
    _control_request_schema,
    _control_result,
    _domain_error_code,
    _error_result,
    _parse_exact_request,
    _tool_result_schema,
    create_harness_mcp_adapter,
)
from .schemas.base import NonNegativeInt, Sha256
from .schemas.common import ArtifactRef
from .schemas.genesis_fresh_capture import GenesisFreshCaptureReceiptV1
from .schemas.golden_control_v2 import (
    AdvisoryRecordCommandV2,
    ControlCommitSnapshotV2,
    ObservationCitationV2,
    PromptRevisionCommandV2,
)
from .schemas.resource_access import ResourceAccessReceipt
from .schemas.workflow import IdempotencyKey, RunReadRequest


class _ControlWireV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    command_id: UUID4
    workflow_run_id: UUID4
    idempotency_key: IdempotencyKey
    expected_workflow_revision: NonNegativeInt
    expected_state_sha256: Sha256
    expected_receipt_head_sha256: Sha256
    source_prompt_uri: WorkflowArtifactUri
    failure_receipt_uris: tuple[WorkflowArtifactUri, ...]
    resource_access_receipt_uris: tuple[WorkflowResourceAccessUri, ...]


class MCPAdvisoryRecordRequestV2(_ControlWireV2):
    schema_version: Literal["harness.mcp_advisory_record_request.v2"]
    advisory_kind: Literal["visual_assessment", "diagnosis", "repair_proposal"]
    disposition: Literal["recommend", "abstain"]
    summary: Annotated[str, Field(min_length=1)]
    recommended_action: Literal[
        "none", "retry", "revise_prompt", "fallback_image", "repair_assets", "stop"
    ]
    source_prompt_revision_uri: WorkflowArtifactUri | None
    observation_receipt_uris: tuple[WorkflowArtifactUri, ...]
    diagnostic_uris: tuple[WorkflowArtifactUri, ...]


class MCPPromptRevisionRecordRequestV2(_ControlWireV2):
    schema_version: Literal["harness.mcp_prompt_revision_record_request.v2"]
    advisory_receipt_uri: WorkflowArtifactUri
    predecessor_environment_uri: WorkflowArtifactUri
    replacement_text: Annotated[str, Field(min_length=1)]
    reason: Annotated[str, Field(min_length=1)]


_TOKEN = object()
_MODELS = {
    "harness_advisory_record_v2": MCPAdvisoryRecordRequestV2,
    "harness_prompt_revision_record_v2": MCPPromptRevisionRecordRequestV2,
}
_OLD = frozenset({"harness_advisory_record_v1", "harness_prompt_revision_record_v1"})


class NativeHarnessMCPAdapter:
    """One sealed v2 control mode; execution and storage remain owned by Harness."""

    __slots__ = ("_base", "_registry_snapshot")

    def __init__(self, *, _token, base):
        if _token is not _TOKEN:
            raise TypeError("native adapter requires its sealed factory")
        self._base = base
        self._registry_snapshot = base._registry_snapshot

    def list_tools(self, snapshot):
        existing = tuple(tool for tool in self._base.list_tools(snapshot) if tool.name not in _OLD)
        controls = tuple(
            MCPTool(
                name=name,
                skill_ref=None,
                input_schema_id=model.model_fields["schema_version"].annotation.__args__[0],
                description="Record advisory-only native control from delivered resources.",
                input_schema=_control_request_schema(model),
                output_schema=_tool_result_schema(ControlCommitSnapshotV2.model_json_schema()),
            )
            for name, model in _MODELS.items()
        )
        return existing + controls

    def read_resource(self, request):
        return self._base.read_resource(request)

    def call_tool(self, request: MCPToolCall):
        if request.name in _OLD:
            raise HarnessMCPProtocolError("legacy controls are absent from native v2 mode")
        if request.name not in _MODELS:
            return self._base.call_tool(request)
        try:
            envelope = _parse_exact_request(request.arguments, _MODELS[request.name])
            command = self._command(envelope)
            return _control_result(self._base._harness.submit_control(command))
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
        except (ValidationError, ValueError) as error:
            return _error_result("HARN_INPUT_SCHEMA_INVALID", str(error))

    def _command(self, envelope):
        harness = self._base._harness
        read = RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=self._base._principal_id,
            workflow_run_id=envelope.workflow_run_id,
        )
        deliveries = {}
        access_refs = []
        for uri in envelope.resource_access_receipt_uris:
            ref = self._base._resolve_resource_access_uri(uri, workflow_id=envelope.workflow_run_id)
            _, payload = harness.read_resource_access_receipt(read, sha256=ref.sha256)
            delivery = ResourceAccessReceipt.model_validate_json(payload)
            if delivery.resource_uri in deliveries:
                raise ValueError("resource deliveries must be unique")
            snapshot = delivery.observed_snapshot
            if (snapshot.revision, snapshot.state_sha256, snapshot.receipt_head.sha256) != (
                envelope.expected_workflow_revision,
                envelope.expected_state_sha256,
                envelope.expected_receipt_head_sha256,
            ):
                raise ValueError("resource delivery differs from expected workflow head")
            deliveries[delivery.resource_uri] = delivery
            access_refs.append(ref)

        used = set()

        def resolve(uri):
            ref = self._base._resolve_workflow_artifact_uri(
                uri, workflow_id=envelope.workflow_run_id
            )
            delivery = deliveries.get(uri)
            if delivery is None or delivery.resource != ref:
                raise ValueError("resource was not previously delivered under this workflow head")
            used.add(uri)
            return ref

        def finish(command):
            if used != set(deliveries):
                raise ValueError("resource accesses do not exactly cover control sources")
            return command

        common = {
            name: getattr(envelope, name)
            for name in (
                "command_id",
                "workflow_run_id",
                "idempotency_key",
                "expected_workflow_revision",
                "expected_state_sha256",
                "expected_receipt_head_sha256",
            )
        }
        common.update(
            principal_id=self._base._principal_id,
            source_prompt=resolve(envelope.source_prompt_uri),
            failure_receipts=tuple(resolve(uri) for uri in envelope.failure_receipt_uris),
            external_agent_executed=False,
        )
        if type(envelope) is MCPPromptRevisionRecordRequestV2:
            return finish(
                PromptRevisionCommandV2(
                    schema_version="harness.prompt_revision_command.v2",
                    **common,
                    advisory_receipt=resolve(envelope.advisory_receipt_uri),
                    predecessor_environment=resolve(envelope.predecessor_environment_uri),
                    patch=dict(
                        kind="full_replace",
                        base_text_sha256=common["source_prompt"].sha256,
                        replacement_text=envelope.replacement_text,
                        reason=envelope.reason,
                    ),
                    fallback=dict(kind="none"),
                    next_compile_skill_ref="image2env.compile@2.0.0",
                )
            )
        citations = []
        for uri in envelope.observation_receipt_uris:
            ref = resolve(uri)
            _, payload = harness.read_workflow_artifact(read, sha256=ref.sha256)
            observed = GenesisFreshCaptureReceiptV1.model_validate_json(payload)
            citations.append(
                ObservationCitationV2(
                    schema_version="harness.observation_citation.v2",
                    receipt=ref,
                    observation=observed,
                    captured_at=observed.captured_at,
                    expires_at=observed.expires_at,
                    ttl_seconds=observed.ttl_seconds,
                    authority=observed.authority,
                    process_continuation=observed.process_continuation,
                )
            )
        return finish(
            AdvisoryRecordCommandV2(
                schema_version="harness.advisory_record_command.v2",
                **common,
                **{
                    name: getattr(envelope, name)
                    for name in ("advisory_kind", "disposition", "summary", "recommended_action")
                },
                source_prompt_revision=None
                if envelope.source_prompt_revision_uri is None
                else resolve(envelope.source_prompt_revision_uri),
                observation_citations=tuple(citations),
                diagnostic_refs=tuple(resolve(uri) for uri in envelope.diagnostic_uris),
                resource_access_receipts=tuple(sorted(access_refs, key=lambda ref: ref.sha256)),
            )
        )


def create_native_harness_mcp_adapter(
    *,
    harness: GoldenRunHarness,
    principal_id: str,
    registry_snapshot: ArtifactRef,
    skill_input_providers: tuple[MCPWorkflowSkillInputProvider, ...] = (),
) -> NativeHarnessMCPAdapter:
    """Opt into v2 controls while retaining exact qualified Skill/resource projection."""
    return NativeHarnessMCPAdapter(
        _token=_TOKEN,
        base=create_harness_mcp_adapter(
            harness=harness,
            principal_id=principal_id,
            registry_snapshot=registry_snapshot,
            skill_input_providers=skill_input_providers,
        ),
    )
