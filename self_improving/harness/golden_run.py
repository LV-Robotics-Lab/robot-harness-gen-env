"""Deep aggregate entry point for one parent Golden Workflow."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError

from .artifacts import ArtifactResolutionError, LocalArtifactStore
from .golden_execution import (
    GoldenExecutionCatalog,
    QualifiedApplicationRunPendingError,
    SkillAuthorizationContext,
    SkillEvidencePolicy,
    create_golden_execution_catalog,
)
from .golden_store import (
    GoldenRunConflictError,
    GoldenRunCorruptionError,
    GoldenRunHeadConflictError,
    SQLiteGoldenRunStore,
)
from .qualification import QualificationReportV1
from .run_store import ControlledRunLifecycle
from .schema_catalog import schema_model
from .schemas.common import (
    ArtifactRef,
    Invocation,
    RegisteredSkillDescriptor,
    RunState,
    RunStatus,
    SkillDescriptor,
    SkillDescriptorV2,
    SkillQualification,
)
from .schemas.genesis_observe import GenesisObservationReceipt
from .schemas.golden_control import (
    ADVISORY_RECORD_COMMAND_SCHEMA_ID,
    CONTROL_COMMIT_SNAPSHOT_SCHEMA_ID,
    EXTERNAL_AGENT_ADVISORY_RECEIPT_SCHEMA_ID,
    PROMPT_REVISION_COMMAND_SCHEMA_ID,
    PROMPT_REVISION_SCHEMA_ID,
    AdvisoryRecordCommand,
    ControlCommitSnapshot,
    ExternalAgentAdvisoryReceipt,
    PromptRevision,
    PromptRevisionCommand,
    VideoFrameToImageFallback,
    external_agent_advisory_receipt_sha256,
    prompt_revision_sha256,
)
from .schemas.registry_snapshot import RegistrySnapshot
from .schemas.resource_access import (
    RESOURCE_ACCESS_RECEIPT_SCHEMA_ID,
    ResourceAccessReceipt,
    resource_access_receipt_sha256,
    resource_access_request_sha256,
)
from .schemas.workflow import (
    OperationSnapshot,
    RunCommandRequest,
    RunReadRequest,
    RunSnapshot,
    RunStartRequest,
    WorkflowStartReceipt,
)
from .schemas.workflow_operation import (
    QualifiedSkillExecutionV2,
    ReceiptDerivedFactClaimV2,
    StateDeltaV2,
    ToolResultV2,
    TrustedToolReceiptV2,
    WorkflowOperationReceiptV2,
    state_delta_v2_sha256,
)
from .trusted_state import (
    StateMutation,
    TrustedWorldState,
    WorldFact,
    WorldFactEvidence,
    advance_trusted_world_state,
    build_world_state,
    fact_sha256,
)


class GoldenRunRegistryError(ValueError):
    """A submitted Registry snapshot is malformed, unavailable, or unqualified."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class GoldenRunNotFoundError(LookupError):
    """No workflow is visible to the requesting principal."""

    code = "HARN_WORKFLOW_NOT_FOUND"


@dataclass(frozen=True, slots=True)
class WorkflowResourceAccess:
    """Verified resource bytes and their separately journaled delivery receipt."""

    resource: ArtifactRef
    payload: bytes
    access_receipt: ArtifactRef
    access_receipt_payload: bytes


class GoldenRunHarness:
    """Create and recover auditable revision-zero workflows through the S1 seam."""

    def __init__(
        self,
        *,
        artifact_store: LocalArtifactStore,
        workflow_store: SQLiteGoldenRunStore,
        execution_catalog: GoldenExecutionCatalog | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        workflow_id_factory: Callable[[], UUID] = uuid4,
        child_run_id_factory: Callable[[], UUID] = uuid4,
        resource_access_id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if type(artifact_store) is not LocalArtifactStore:
            raise TypeError("artifact_store must be the exact LocalArtifactStore")
        if type(workflow_store) is not SQLiteGoldenRunStore:
            raise TypeError("workflow_store must be the exact SQLiteGoldenRunStore")
        self._artifact_store = artifact_store
        self._workflow_store = workflow_store
        if execution_catalog is None:
            execution_catalog = create_golden_execution_catalog()
        if type(execution_catalog) is not GoldenExecutionCatalog:
            raise TypeError("execution_catalog must be the exact GoldenExecutionCatalog")
        if execution_catalog.artifact_roots and execution_catalog.artifact_roots != {
            artifact_store.root
        }:
            raise ValueError("execution catalog and GoldenRun must share one exact CAS")
        self._execution_catalog = execution_catalog
        self._clock = clock
        self._workflow_id_factory = workflow_id_factory
        self._child_run_id_factory = child_run_id_factory
        self._resource_access_id_factory = resource_access_id_factory

    def start(self, request: RunStartRequest) -> RunSnapshot:
        """Build the first trusted state and its actor-neutral receipt head."""

        request_sha256 = _request_sha256(request)
        stored = self._workflow_store.get_or_create_start(
            principal_id=request.principal_id,
            idempotency_key=request.idempotency_key,
            request_sha256=request_sha256,
            start_request_sha256=hashlib.sha256(_canonical_model_bytes(request)).hexdigest(),
            factory=lambda: self._create_start(request, request_sha256=request_sha256),
        )
        snapshot = stored.snapshot
        if snapshot.revision == 0 and snapshot.active_operation is None:
            self._verify_start_snapshot(
                snapshot,
                request=request,
                request_sha256=request_sha256,
                persisted_idempotency_key=stored.idempotency_key,
                persisted_request_sha256=stored.request_sha256,
            )
        else:
            snapshot = self.read(
                RunReadRequest(
                    schema_version="harness.run_read_request.v1",
                    principal_id=request.principal_id,
                    workflow_run_id=snapshot.workflow_run_id,
                )
            )
        return snapshot

    def read(self, request: RunReadRequest) -> RunSnapshot:
        """Recover an authorized workflow and reverify its current CAS closure."""

        stored = self._workflow_store.read_snapshot(
            principal_id=request.principal_id,
            workflow_run_id=request.workflow_run_id,
        )
        if stored is None:
            raise GoldenRunNotFoundError("golden workflow was not found for this principal")
        snapshot = stored.snapshot
        try:
            start_request = self._resolve_canonical_model(
                snapshot.start_request_ref,
                RunStartRequest,
            )
        except (ArtifactResolutionError, ValidationError, TypeError, ValueError) as error:
            raise GoldenRunCorruptionError(
                f"persisted workflow start failed its CAS closure: {error}"
            ) from error
        try:
            if snapshot.revision == 0 and snapshot.active_operation is None:
                self._verify_start_snapshot(
                    snapshot,
                    request=start_request,
                    request_sha256=_request_sha256(start_request),
                    persisted_idempotency_key=stored.idempotency_key,
                    persisted_request_sha256=stored.request_sha256,
                )
            else:
                self._verify_advanced_snapshot(
                    snapshot,
                    request=start_request,
                    persisted_idempotency_key=stored.idempotency_key,
                    persisted_request_sha256=stored.request_sha256,
                )
        except GoldenRunCorruptionError:
            raise
        except (
            ArtifactResolutionError,
            GoldenRunRegistryError,
            ValidationError,
            TypeError,
            ValueError,
        ) as error:
            raise GoldenRunCorruptionError(
                f"persisted workflow failed its CAS closure: {error}"
            ) from error
        return snapshot

    def read_workflow_artifact(
        self,
        request: RunReadRequest,
        *,
        sha256: str,
    ) -> tuple[ArtifactRef, bytes]:
        """Read one artifact explicitly reachable from a verified workflow closure."""

        snapshot = self.read(request)
        reachable = self._reachable_workflow_artifacts(snapshot)
        ref = next((item for item in reachable if item.sha256 == sha256), None)
        if ref is None:
            raise GoldenRunNotFoundError("workflow artifact was not found for this principal")
        return ref, self._artifact_store.resolve(ref).path.read_bytes()

    def read_workflow_artifact_audited(
        self,
        request: RunReadRequest,
        *,
        sha256: str,
        resource_uri: str,
    ) -> WorkflowResourceAccess:
        """Deliver one reachable artifact and append a non-mutating read receipt."""

        snapshot = self.read(request)
        reachable = self._reachable_workflow_artifacts(snapshot)
        resource = next((item for item in reachable if item.sha256 == sha256), None)
        if resource is None:
            raise GoldenRunNotFoundError("workflow artifact was not found for this principal")
        expected_uri = f"harness://workflows/{snapshot.workflow_run_id}/artifacts/{resource.sha256}"
        if resource_uri != expected_uri:
            raise ValueError("resource_uri does not name the requested workflow artifact")
        payload = self._artifact_store.resolve(resource).path.read_bytes()
        snapshot_payload = _canonical_model_bytes(snapshot)
        snapshot_sha256 = hashlib.sha256(snapshot_payload).hexdigest()
        access_identity = resource_access_request_sha256(
            principal_id=request.principal_id,
            workflow_run_id=request.workflow_run_id,
            resource_uri=resource_uri,
            resource=resource,
            observed_snapshot_sha256=snapshot_sha256,
        )
        existing = self._workflow_store.read_resource_access(
            principal_id=request.principal_id,
            workflow_run_id=request.workflow_run_id,
            access_identity_sha256=access_identity,
        )
        if existing is not None:
            return self._materialize_resource_access(existing.receipt, payload=payload)

        served_at = self._clock()
        _require_utc_clock(served_at)
        unhashed = ResourceAccessReceipt.model_construct(
            schema_version=RESOURCE_ACCESS_RECEIPT_SCHEMA_ID,
            access_id=self._resource_access_id_factory(),
            principal_id=request.principal_id,
            workflow_run_id=request.workflow_run_id,
            resource_uri=resource_uri,
            resource=resource,
            delivered_sha256=resource.sha256,
            delivered_bytes=len(payload),
            representation=(
                "text_utf8"
                if resource.media_type in {"application/json", "application/x-ndjson"}
                else "blob_base64"
            ),
            observed_snapshot=snapshot,
            observed_snapshot_sha256=snapshot_sha256,
            served_at=served_at,
            delivery_scope="server_response_payload",
            transport_acknowledged=False,
            model_understanding_claimed=False,
            receipt_sha256="0" * 64,
        )
        receipt = ResourceAccessReceipt(
            **unhashed.model_dump(mode="python", exclude={"receipt_sha256"}),
            receipt_sha256=resource_access_receipt_sha256(unhashed),
        )
        receipt_ref = self._publish_model(
            receipt,
            name="resource_access_receipt.json",
            schema_version=RESOURCE_ACCESS_RECEIPT_SCHEMA_ID,
        )
        persisted = self._workflow_store.commit_resource_access(
            principal_id=request.principal_id,
            access_identity_sha256=access_identity,
            expected_snapshot_sha256=snapshot_sha256,
            receipt=receipt,
        )
        if persisted is None:
            raise GoldenRunNotFoundError("golden workflow was not found for this principal")
        result = self._materialize_resource_access(persisted.receipt, payload=payload)
        if persisted.receipt == receipt and result.access_receipt != receipt_ref:
            raise GoldenRunCorruptionError(
                "resource access receipt CAS identity changed during durable commit"
            )
        return result

    def read_resource_access_receipt(
        self,
        request: RunReadRequest,
        *,
        sha256: str,
    ) -> tuple[ArtifactRef, bytes]:
        """Read one journaled access receipt without recursively recording another read."""

        self.read(request)
        stored = self._workflow_store.read_resource_access(
            principal_id=request.principal_id,
            workflow_run_id=request.workflow_run_id,
            receipt_payload_sha256=sha256,
        )
        if stored is None:
            raise GoldenRunNotFoundError(
                "workflow resource access receipt was not found for this principal"
            )
        result = self._materialize_resource_access(stored.receipt, payload=b"")
        return result.access_receipt, result.access_receipt_payload

    def _materialize_resource_access(
        self,
        receipt: ResourceAccessReceipt,
        *,
        payload: bytes,
    ) -> WorkflowResourceAccess:
        receipt_payload = _canonical_model_bytes(receipt)
        digest = hashlib.sha256(receipt_payload).hexdigest()
        receipt_ref = ArtifactRef(
            name="resource_access_receipt.json",
            uri=f"artifact://sha256/{digest}",
            sha256=digest,
            bytes=len(receipt_payload),
            media_type="application/json",
            schema_version=RESOURCE_ACCESS_RECEIPT_SCHEMA_ID,
        )
        resolved = self._artifact_store.resolve(receipt_ref).path.read_bytes()
        if resolved != receipt_payload:
            raise GoldenRunCorruptionError("resource access receipt CAS bytes changed")
        return WorkflowResourceAccess(
            resource=receipt.resource,
            payload=payload,
            access_receipt=receipt_ref,
            access_receipt_payload=receipt_payload,
        )

    def _reachable_workflow_artifacts(
        self,
        snapshot: RunSnapshot,
    ) -> tuple[ArtifactRef, ...]:
        refs: list[ArtifactRef] = [
            snapshot.state_ref,
            snapshot.start_request_ref,
            snapshot.registry_snapshot,
            snapshot.receipt_head,
        ]
        start = self._resolve_canonical_model(snapshot.start_request_ref, RunStartRequest)
        refs.extend(start.initial_fact_evidence)
        registry = self._resolve_canonical_model(snapshot.registry_snapshot, RegistrySnapshot)
        for entry in registry.qualified_skills:
            refs.extend(
                (
                    entry.descriptor_ref,
                    entry.qualification_ref,
                    entry.qualification_report_ref,
                )
            )
        state = self._resolve_canonical_model(snapshot.state_ref, TrustedWorldState)
        refs.extend(fact.source_artifact for fact in state.facts)

        entries = tuple(
            entry
            for entry in self._workflow_store.read_entries(
                principal_id=snapshot.principal_id,
                workflow_run_id=snapshot.workflow_run_id,
            )
            if entry.base_revision < snapshot.revision
            or (
                entry.operation is not None
                and entry.operation.operation_id == snapshot.active_operation
            )
        )
        operations = tuple(
            cast(OperationSnapshot, entry.operation)
            for entry in entries
            if entry.operation is not None
        )
        receipt_ref = snapshot.receipt_head
        terminal_entries = tuple(
            entry
            for entry in entries
            if entry.control is not None
            or (entry.operation is not None and entry.operation.status is not RunStatus.RUNNING)
        )
        for entry in reversed(terminal_entries):
            if entry.operation is not None:
                receipt = cast(
                    WorkflowOperationReceiptV2,
                    self._resolve_canonical_model(
                        receipt_ref,
                        WorkflowOperationReceiptV2,
                    ),
                )
                refs.extend(
                    (
                        receipt_ref,
                        receipt.predecessor_receipt,
                        receipt.tool_receipt,
                        receipt.tool_result,
                        receipt.resulting_state,
                    )
                )
                refs.extend(filter(None, (receipt.state_delta,)))
                receipt_ref = receipt.predecessor_receipt
            else:
                control = cast(ControlCommitSnapshot, entry.control)
                if receipt_ref != control.receipt:
                    raise GoldenRunCorruptionError(
                        "control receipt chain differs from the durable turn ledger"
                    )
                control_receipt = self._resolve_control_receipt(receipt_ref)
                refs.extend(self._control_artifact_refs(control, control_receipt))
                receipt_ref = control_receipt.predecessor_receipt
        refs.append(receipt_ref)

        for operation in operations:
            refs.extend((operation.command, operation.qualified_execution))
            qualified = self._resolve_canonical_model(
                operation.qualified_execution,
                QualifiedSkillExecutionV2,
            )
            refs.extend(
                (
                    qualified.descriptor,
                    qualified.qualification_artifact,
                    qualified.qualification_report,
                )
            )
        terminal_operations = tuple(
            item for item in operations if item.status is not RunStatus.RUNNING
        )
        for operation in terminal_operations:
            result_ref = cast(ArtifactRef, operation.tool_result)
            refs.append(result_ref)
            result = self._resolve_canonical_model(result_ref, ToolResultV2)
            refs.extend((result.run_state, result.trusted_receipt))
            refs.extend(observation.source_artifact for observation in result.fresh_observations)
            refs.extend(filter(None, (result.invocation, result.state_delta)))
            tool_receipt = self._resolve_canonical_model(
                result.trusted_receipt,
                TrustedToolReceiptV2,
            )
            refs.extend(
                (
                    tool_receipt.command,
                    tool_receipt.base_state,
                    tool_receipt.registry_snapshot,
                    tool_receipt.skill.descriptor,
                    tool_receipt.skill.qualification_artifact,
                    tool_receipt.skill.qualification_report,
                    tool_receipt.run_state,
                    *tool_receipt.supporting_artifacts,
                )
            )
            refs.extend(filter(None, (tool_receipt.invocation,)))
            child_state = self._resolve_canonical_model(result.run_state, RunState)
            refs.extend(child_state.artifacts)
        return tuple(dict.fromkeys(refs))

    def _resolve_control_receipt(
        self,
        artifact: ArtifactRef,
    ) -> ExternalAgentAdvisoryReceipt | PromptRevision:
        if artifact.schema_version == EXTERNAL_AGENT_ADVISORY_RECEIPT_SCHEMA_ID:
            model: type[ExternalAgentAdvisoryReceipt | PromptRevision] = (
                ExternalAgentAdvisoryReceipt
            )
        elif artifact.schema_version == PROMPT_REVISION_SCHEMA_ID:
            model = PromptRevision
        else:
            raise GoldenRunCorruptionError("control receipt has an unsupported schema")
        return cast(
            ExternalAgentAdvisoryReceipt | PromptRevision,
            self._resolve_canonical_model(artifact, model),
        )

    @staticmethod
    def _control_artifact_refs(
        control: ControlCommitSnapshot,
        receipt: ExternalAgentAdvisoryReceipt | PromptRevision,
    ) -> tuple[ArtifactRef, ...]:
        refs = [
            control.command,
            control.payload,
            control.receipt,
            control.receipt_head,
            receipt.command,
            receipt.predecessor_receipt,
            receipt.base_state,
            receipt.resulting_state,
            receipt.source_prompt,
            *receipt.failure_receipts,
            *(citation.receipt for citation in receipt.observation_citations),
        ]
        if type(receipt) is ExternalAgentAdvisoryReceipt:
            refs.extend(receipt.diagnostic_refs)
            refs.extend(receipt.resource_access_receipts)
            refs.extend(filter(None, (receipt.source_prompt_revision,)))
        else:
            refs.extend(
                (
                    receipt.advisory_receipt,
                    receipt.predecessor_package,
                    receipt.revised_prompt,
                )
            )
            refs.extend(filter(None, (receipt.predecessor_prompt_revision,)))
            if isinstance(receipt.fallback, VideoFrameToImageFallback):
                refs.extend(
                    (
                        receipt.fallback.source_video,
                        receipt.fallback.selected_frame,
                        receipt.fallback.frame_sequence_manifest,
                    )
                )
        return tuple(refs)

    def submit(self, request: RunCommandRequest) -> OperationSnapshot:
        """Reserve, execute, and durably commit one exact qualified Skill command."""

        existing = self._workflow_store.read_operation(
            principal_id=request.principal_id,
            workflow_run_id=request.workflow_run_id,
            skill_ref=request.skill_ref,
            idempotency_key=request.idempotency_key,
        )
        if existing is not None:
            policy, _ = self._resolve_frozen_execution(existing.operation)
            command = self._resolve_canonical_model(existing.operation.command, RunCommandRequest)
            normalized, _ = self._normalize_command(policy, request)
            if command != normalized:
                raise GoldenRunConflictError(
                    "operation idempotency key is already bound to a different command"
                )
            if existing.operation.status is not RunStatus.RUNNING:
                self.read(
                    RunReadRequest(
                        schema_version="harness.run_read_request.v1",
                        principal_id=request.principal_id,
                        workflow_run_id=request.workflow_run_id,
                    )
                )
                return existing.operation
            return self._resume_operation(existing.operation, request)

        current = self.read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id=request.principal_id,
                workflow_run_id=request.workflow_run_id,
            )
        )
        policy, qualified = self._resolve_live_execution(
            current.registry_snapshot,
            request.skill_ref,
        )
        normalized, parameters = self._normalize_command(policy, request)
        base_state = self._resolve_canonical_model(current.state_ref, TrustedWorldState)
        self._authorize(policy, base_state, parameters, snapshot=current)
        command_ref = self._publish_model(
            normalized,
            name="run_command_request.json",
            schema_version="harness.run_command_request.v1",
        )
        qualified_ref = self._publish_model(
            qualified,
            name="qualified_skill_execution.json",
            schema_version="harness.qualified_skill_execution.v2",
        )
        started_at = self._clock()
        _require_utc_clock(started_at)
        operation = OperationSnapshot(
            schema_version="harness.operation_snapshot.v1",
            operation_id=normalized.command_id,
            workflow_run_id=normalized.workflow_run_id,
            principal_id=normalized.principal_id,
            idempotency_key=normalized.idempotency_key,
            command=command_ref,
            qualified_execution=qualified_ref,
            skill_ref=normalized.skill_ref,
            status=RunStatus.RUNNING,
            base_revision=normalized.expected_workflow_revision,
            committed_revision=None,
            child_run_id=self._child_run_id_factory(),
            tool_result=None,
            started_at=started_at,
            ended_at=None,
        )
        reserved = self._workflow_store.reserve_operation(
            principal_id=normalized.principal_id,
            workflow_run_id=normalized.workflow_run_id,
            skill_ref=normalized.skill_ref,
            idempotency_key=normalized.idempotency_key,
            command_sha256=command_ref.sha256,
            expected_workflow_revision=normalized.expected_workflow_revision,
            expected_state_sha256=normalized.expected_state_sha256,
            expected_receipt_head_sha256=normalized.expected_receipt_head_sha256,
            operation=operation,
        )
        if reserved is None:
            raise GoldenRunNotFoundError("golden workflow was not found for this principal")
        if reserved.operation != operation:
            persisted_command = self._resolve_canonical_model(
                reserved.operation.command,
                RunCommandRequest,
            )
            if persisted_command != normalized:
                raise GoldenRunConflictError(
                    "operation identity is already bound to another command"
                )
            operation = reserved.operation
        return self._resume_operation(operation, normalized)

    def submit_control(
        self,
        request: AdvisoryRecordCommand | PromptRevisionCommand,
    ) -> ControlCommitSnapshot:
        """Commit one advisory-only record without changing trusted world state."""

        if type(request) not in {AdvisoryRecordCommand, PromptRevisionCommand}:
            raise TypeError("request must be an exact Golden control command")
        control_kind = "advisory" if type(request) is AdvisoryRecordCommand else "prompt_revision"
        existing = self._workflow_store.read_control_record(
            principal_id=request.principal_id,
            workflow_run_id=request.workflow_run_id,
            control_kind=control_kind,
            idempotency_key=request.idempotency_key,
        )
        if existing is not None:
            persisted = self._resolve_control_command(existing.control.command)
            if persisted != request:
                raise GoldenRunConflictError(
                    "control idempotency key is already bound to a different command"
                )
            self.read(
                RunReadRequest(
                    schema_version="harness.run_read_request.v1",
                    principal_id=request.principal_id,
                    workflow_run_id=request.workflow_run_id,
                )
            )
            return existing.control

        current = self.read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id=request.principal_id,
                workflow_run_id=request.workflow_run_id,
            )
        )
        if (
            request.expected_workflow_revision != current.revision
            or request.expected_state_sha256 != current.state_sha256
            or request.expected_receipt_head_sha256 != current.receipt_head.sha256
            or current.active_operation is not None
        ):
            raise GoldenRunHeadConflictError(
                "workflow head changed before the control record could commit"
            )
        committed_at = self._clock()
        _require_utc_clock(committed_at)
        reachable = self._reachable_workflow_artifacts(current)
        reachable_identities = {_artifact_identity(ref) for ref in reachable}
        if type(request) is AdvisoryRecordCommand:
            self._verify_advisory_lineage(
                request,
                current=current,
                at=committed_at,
                reachable_identities=reachable_identities,
            )
        else:
            self._verify_prompt_lineage(
                request,
                current=current,
                at=committed_at,
                reachable_identities=reachable_identities,
            )
        command_ref = self._publish_model(
            request,
            name=f"{control_kind}_command.json",
            schema_version=(
                ADVISORY_RECORD_COMMAND_SCHEMA_ID
                if type(request) is AdvisoryRecordCommand
                else PROMPT_REVISION_COMMAND_SCHEMA_ID
            ),
        )
        if type(request) is AdvisoryRecordCommand:
            receipt = self._build_advisory_receipt(
                request,
                command_ref=command_ref,
                current=current,
                committed_at=committed_at,
                reachable_identities=reachable_identities,
            )
            receipt_schema = EXTERNAL_AGENT_ADVISORY_RECEIPT_SCHEMA_ID
            receipt_name = "external_agent_advisory_receipt.json"
        else:
            receipt = self._build_prompt_revision(
                request,
                command_ref=command_ref,
                current=current,
                committed_at=committed_at,
                reachable_identities=reachable_identities,
            )
            receipt_schema = PROMPT_REVISION_SCHEMA_ID
            receipt_name = "prompt_revision.json"
        receipt_ref = self._publish_model(
            receipt,
            name=receipt_name,
            schema_version=receipt_schema,
        )
        control = ControlCommitSnapshot(
            schema_version=CONTROL_COMMIT_SNAPSHOT_SCHEMA_ID,
            control_kind=control_kind,
            idempotency_key=request.idempotency_key,
            command_id=request.command_id,
            workflow_run_id=request.workflow_run_id,
            principal_id=request.principal_id,
            command=command_ref,
            predecessor_receipt=current.receipt_head,
            base_revision=current.revision,
            committed_revision=current.revision + 1,
            base_turn_seq=current.turn_seq,
            committed_turn_seq=current.turn_seq + 1,
            base_state_sha256=current.state_sha256,
            base_state=current.state_ref,
            resulting_state_sha256=current.state_sha256,
            resulting_state=current.state_ref,
            committed_at=committed_at,
            payload=receipt_ref,
            receipt=receipt_ref,
            receipt_head=receipt_ref,
        )
        next_snapshot = RunSnapshot.model_validate(
            current.model_copy(
                update={
                    "revision": current.revision + 1,
                    "turn_seq": current.turn_seq + 1,
                    "receipt_head": receipt_ref,
                    "updated_at": committed_at,
                }
            ).model_dump(mode="python")
        )
        persisted = self._workflow_store.commit_control_record(
            principal_id=request.principal_id,
            expected_workflow_revision=request.expected_workflow_revision,
            expected_state_sha256=request.expected_state_sha256,
            expected_receipt_head_sha256=request.expected_receipt_head_sha256,
            control=control,
            snapshot=next_snapshot,
        )
        if persisted is None:
            raise GoldenRunNotFoundError("golden workflow was not found for this principal")
        if persisted.control != control:
            persisted_command = self._resolve_control_command(persisted.control.command)
            if persisted_command != request:
                raise GoldenRunConflictError("control identity is already bound to another command")
        self.read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id=request.principal_id,
                workflow_run_id=request.workflow_run_id,
            )
        )
        return persisted.control

    def _build_advisory_receipt(
        self,
        command: AdvisoryRecordCommand,
        *,
        command_ref: ArtifactRef,
        current: RunSnapshot,
        committed_at: datetime,
        reachable_identities: set[tuple[str, str, str]],
    ) -> ExternalAgentAdvisoryReceipt:
        self._verify_advisory_lineage(
            command,
            current=current,
            at=committed_at,
            reachable_identities=reachable_identities,
        )
        unhashed = ExternalAgentAdvisoryReceipt.model_construct(
            schema_version=EXTERNAL_AGENT_ADVISORY_RECEIPT_SCHEMA_ID,
            command_id=command.command_id,
            workflow_run_id=command.workflow_run_id,
            principal_id=command.principal_id,
            command=command_ref,
            predecessor_receipt=current.receipt_head,
            base_revision=current.revision,
            committed_revision=current.revision + 1,
            base_turn_seq=current.turn_seq,
            committed_turn_seq=current.turn_seq + 1,
            base_state_sha256=current.state_sha256,
            base_state=current.state_ref,
            resulting_state_sha256=current.state_sha256,
            resulting_state=current.state_ref,
            committed_at=committed_at,
            advisory_kind=command.advisory_kind,
            disposition=command.disposition,
            summary=command.summary,
            recommended_action=command.recommended_action,
            source_prompt=command.source_prompt,
            source_prompt_revision=command.source_prompt_revision,
            failure_receipts=command.failure_receipts,
            observation_citations=command.observation_citations,
            diagnostic_refs=command.diagnostic_refs,
            resource_access_receipts=command.resource_access_receipts,
            authority="advisory_only",
            external_agent_executed=False,
            grants_physical_validity=False,
            grants_publishability=False,
            receipt_sha256="0" * 64,
        )
        return ExternalAgentAdvisoryReceipt(
            **unhashed.model_dump(mode="python", exclude={"receipt_sha256"}),
            receipt_sha256=external_agent_advisory_receipt_sha256(unhashed),
        )

    def _verify_advisory_lineage(
        self,
        command: AdvisoryRecordCommand,
        *,
        current: RunSnapshot,
        at: datetime,
        reachable_identities: set[tuple[str, str, str]],
    ) -> None:
        self._require_reachable(
            command.source_prompt,
            label="source prompt",
            reachable_identities=reachable_identities,
        )
        if command.source_prompt_revision is not None:
            self._require_reachable(
                command.source_prompt_revision,
                label="source prompt revision",
                reachable_identities=reachable_identities,
            )
            self._resolve_canonical_model(command.source_prompt_revision, PromptRevision)
        failure_artifacts = self._verify_control_failures(
            command.failure_receipts,
            current=current,
            reachable_identities=reachable_identities,
        )
        for diagnostic in command.diagnostic_refs:
            self._require_reachable(
                diagnostic,
                label="diagnostic history",
                reachable_identities=reachable_identities,
            )
            if _artifact_identity(diagnostic) not in failure_artifacts:
                raise GoldenRunNotFoundError(
                    "diagnostic is not reachable from the cited failure history"
                )
        for citation in command.observation_citations:
            self._verify_observation_citation(
                citation.receipt,
                captured_at=citation.captured_at,
                valid_until=citation.valid_until,
                at=at,
                current=current,
                reachable_identities=reachable_identities,
            )
        accessed_identities: set[tuple[str, str, str]] = set()
        for access_ref in command.resource_access_receipts:
            access = self._resolve_canonical_model(access_ref, ResourceAccessReceipt)
            stored = self._workflow_store.read_resource_access(
                principal_id=current.principal_id,
                workflow_run_id=current.workflow_run_id,
                receipt_payload_sha256=access_ref.sha256,
            )
            if (
                stored is None
                or stored.receipt != access
                or access.principal_id != current.principal_id
                or access.workflow_run_id != current.workflow_run_id
                or access.served_at > at
            ):
                raise GoldenRunNotFoundError(
                    "resource access receipt is not bound to this workflow history"
                )
            observed_reachable = {
                _artifact_identity(ref)
                for ref in self._reachable_workflow_artifacts(access.observed_snapshot)
            }
            if _artifact_identity(access.resource) not in observed_reachable:
                raise GoldenRunNotFoundError(
                    "resource access receipt targets an unreachable workflow artifact"
                )
            accessed_identities.add(_artifact_identity(access.resource))
        required_access_identities = {
            _artifact_identity(command.source_prompt),
            *(_artifact_identity(ref) for ref in command.failure_receipts),
            *(_artifact_identity(citation.receipt) for citation in command.observation_citations),
            *(_artifact_identity(ref) for ref in command.diagnostic_refs),
        }
        if command.source_prompt_revision is not None:
            required_access_identities.add(_artifact_identity(command.source_prompt_revision))
        if accessed_identities and accessed_identities != required_access_identities:
            raise GoldenRunNotFoundError(
                "resource access receipts do not exactly cover the advisory sources"
            )

    def _build_prompt_revision(
        self,
        command: PromptRevisionCommand,
        *,
        command_ref: ArtifactRef,
        current: RunSnapshot,
        committed_at: datetime,
        reachable_identities: set[tuple[str, str, str]],
    ) -> PromptRevision:
        advisory, predecessor_prompt, prompt_revision_number, _ = self._verify_prompt_lineage(
            command,
            current=current,
            at=committed_at,
            reachable_identities=reachable_identities,
        )
        revised_prompt = self._publish_bytes(
            command.patch.replacement_text.encode("utf-8"),
            name="revised_prompt.txt",
            media_type="text/plain",
            schema_version="harness.prompt_text.v1",
        )
        unhashed = PromptRevision.model_construct(
            schema_version=PROMPT_REVISION_SCHEMA_ID,
            command_id=command.command_id,
            workflow_run_id=command.workflow_run_id,
            principal_id=command.principal_id,
            command=command_ref,
            predecessor_receipt=current.receipt_head,
            base_revision=current.revision,
            committed_revision=current.revision + 1,
            base_turn_seq=current.turn_seq,
            committed_turn_seq=current.turn_seq + 1,
            base_state_sha256=current.state_sha256,
            base_state=current.state_ref,
            resulting_state_sha256=current.state_sha256,
            resulting_state=current.state_ref,
            committed_at=committed_at,
            prompt_revision_number=prompt_revision_number,
            source_prompt=command.source_prompt,
            predecessor_prompt_revision=predecessor_prompt,
            advisory_receipt=command.advisory_receipt,
            failure_receipts=command.failure_receipts,
            observation_citations=advisory.observation_citations,
            predecessor_package=command.predecessor_package,
            patch=command.patch,
            revised_prompt=revised_prompt,
            fallback=command.fallback,
            next_compile_skill_ref=command.next_compile_skill_ref,
            external_agent_executed=False,
            revision_sha256="0" * 64,
        )
        return PromptRevision(
            **unhashed.model_dump(mode="python", exclude={"revision_sha256"}),
            revision_sha256=prompt_revision_sha256(unhashed),
        )

    def _verify_prompt_lineage(
        self,
        command: PromptRevisionCommand,
        *,
        current: RunSnapshot,
        at: datetime,
        reachable_identities: set[tuple[str, str, str]],
    ) -> tuple[
        ExternalAgentAdvisoryReceipt,
        ArtifactRef | None,
        int,
        set[tuple[str, str, str]],
    ]:
        if command.advisory_receipt != current.receipt_head:
            raise GoldenRunRegistryError(
                "HARN_HISTORY_MISMATCH",
                "prompt revision must immediately follow its advisory receipt",
            )
        self._require_reachable(
            command.advisory_receipt,
            label="advisory history",
            reachable_identities=reachable_identities,
        )
        advisory = cast(
            ExternalAgentAdvisoryReceipt,
            self._resolve_canonical_model(
                command.advisory_receipt,
                ExternalAgentAdvisoryReceipt,
            ),
        )
        if (
            command.source_prompt != advisory.source_prompt
            or command.failure_receipts != advisory.failure_receipts
        ):
            raise GoldenRunRegistryError(
                "HARN_HISTORY_MISMATCH",
                "prompt revision must preserve advisory prompt and failure lineage",
            )
        failure_artifacts = self._verify_control_failures(
            command.failure_receipts,
            current=current,
            reachable_identities=reachable_identities,
        )
        if _artifact_identity(command.predecessor_package) not in failure_artifacts:
            raise GoldenRunNotFoundError(
                "predecessor package is not reachable from the cited failure history"
            )
        for citation in advisory.observation_citations:
            self._verify_observation_citation(
                citation.receipt,
                captured_at=citation.captured_at,
                valid_until=citation.valid_until,
                at=at,
                current=current,
                reachable_identities=reachable_identities,
            )
        if advisory.recommended_action == "fallback_image" and not isinstance(
            command.fallback,
            VideoFrameToImageFallback,
        ):
            raise GoldenRunRegistryError(
                "HARN_HISTORY_MISMATCH",
                "advisory requires an explicit video-to-image fallback",
            )
        if isinstance(command.fallback, VideoFrameToImageFallback):
            self._verify_video_frame_fallback(
                command.fallback,
                allowed_artifacts=failure_artifacts,
                reachable_identities=reachable_identities,
            )
        predecessor_prompt = advisory.source_prompt_revision
        prompt_revision_number = 1
        if predecessor_prompt is not None:
            prior = cast(
                PromptRevision,
                self._resolve_canonical_model(predecessor_prompt, PromptRevision),
            )
            if command.source_prompt != prior.revised_prompt:
                raise GoldenRunRegistryError(
                    "HARN_HISTORY_MISMATCH",
                    "prompt revision does not continue the prior revised prompt",
                )
            prompt_revision_number = prior.prompt_revision_number + 1
        return advisory, predecessor_prompt, prompt_revision_number, failure_artifacts

    def _verify_control_failures(
        self,
        receipts: tuple[ArtifactRef, ...],
        *,
        current: RunSnapshot,
        reachable_identities: set[tuple[str, str, str]],
    ) -> set[tuple[str, str, str]]:
        artifacts: set[tuple[str, str, str]] = set()
        for ref in receipts:
            self._require_reachable(
                ref,
                label="failure history",
                reachable_identities=reachable_identities,
            )
            receipt = cast(
                WorkflowOperationReceiptV2,
                self._resolve_canonical_model(ref, WorkflowOperationReceiptV2),
            )
            if (
                receipt.workflow_run_id != current.workflow_run_id
                or receipt.principal_id != current.principal_id
                or receipt.status not in {RunStatus.BLOCKED, RunStatus.FAILED}
            ):
                raise GoldenRunRegistryError(
                    "HARN_HISTORY_MISMATCH",
                    "cited failure receipt is not a blocked or failed workflow operation",
                )
            result = cast(
                ToolResultV2,
                self._resolve_canonical_model(receipt.tool_result, ToolResultV2),
            )
            tool_receipt = cast(
                TrustedToolReceiptV2,
                self._resolve_canonical_model(receipt.tool_receipt, TrustedToolReceiptV2),
            )
            child_state = cast(
                RunState,
                self._resolve_canonical_model(result.run_state, RunState),
            )
            for artifact in (
                ref,
                receipt.tool_result,
                receipt.tool_receipt,
                *result.diagnostics,
                *tool_receipt.supporting_artifacts,
                *child_state.artifacts,
            ):
                artifacts.add(_artifact_identity(artifact))
        return artifacts

    def _verify_observation_citation(
        self,
        receipt_ref: ArtifactRef,
        *,
        captured_at: datetime,
        valid_until: datetime,
        at: datetime,
        current: RunSnapshot,
        reachable_identities: set[tuple[str, str, str]],
    ) -> None:
        self._require_reachable(
            receipt_ref,
            label="observation history",
            reachable_identities=reachable_identities,
        )
        receipt = cast(
            GenesisObservationReceipt,
            self._resolve_canonical_model(receipt_ref, GenesisObservationReceipt),
        )
        if receipt.captured_at != captured_at or receipt.valid_until != valid_until:
            raise GoldenRunRegistryError(
                "HARN_HISTORY_MISMATCH",
                "observation citation window differs from its receipt",
            )
        if not captured_at <= at <= valid_until:
            raise GoldenRunRegistryError(
                "HARN_OBSERVATION_EXPIRED",
                "observation citation expired before the control commit",
            )
        state = cast(
            TrustedWorldState,
            self._resolve_canonical_model(current.state_ref, TrustedWorldState),
        )
        state_facts = {fact.key: fact for fact in state.facts}
        for observation in receipt.observations:
            fact = state_facts.get(observation.key)
            if (
                fact is None
                or fact.value != observation.value
                or fact.observed_at != observation.captured_at
                or fact.valid_until != observation.valid_until
            ):
                raise GoldenRunRegistryError(
                    "HARN_HISTORY_MISMATCH",
                    "observation citation is not present in the current trusted state",
                )

    def _verify_video_frame_fallback(
        self,
        fallback: VideoFrameToImageFallback,
        *,
        allowed_artifacts: set[tuple[str, str, str]],
        reachable_identities: set[tuple[str, str, str]],
    ) -> None:
        for label, ref in (
            ("source video", fallback.source_video),
            ("selected frame", fallback.selected_frame),
            ("frame lineage", fallback.frame_sequence_manifest),
        ):
            self._require_reachable(
                ref,
                label=label,
                reachable_identities=reachable_identities,
            )
            if _artifact_identity(ref) not in allowed_artifacts:
                raise GoldenRunNotFoundError(
                    f"{label} is not reachable from the cited failure lineage"
                )
        payload = self._artifact_store.resolve(fallback.frame_sequence_manifest).path.read_bytes()
        try:
            manifest = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GoldenRunRegistryError(
                "HARN_INPUT_SCHEMA_INVALID",
                "frame lineage manifest is not canonical JSON",
            ) from error
        if (
            type(manifest) is not dict
            or payload != _canonical_json_bytes(manifest)
            or set(manifest) != {"schema_version", "source_video", "frames"}
            or manifest.get("schema_version") != "harness.video_frame_sequence.v1"
        ):
            raise GoldenRunRegistryError(
                "HARN_INPUT_SCHEMA_INVALID",
                "frame lineage manifest is not strict canonical JSON",
            )
        try:
            source_video = ArtifactRef.model_validate(manifest["source_video"])
            frames = manifest["frames"]
            matches = tuple(
                ArtifactRef.model_validate(item["artifact"])
                for item in frames
                if type(item) is dict
                and set(item) == {"index", "artifact"}
                and item["index"] == fallback.frame_index
            )
        except (KeyError, TypeError, ValidationError, ValueError) as error:
            raise GoldenRunRegistryError(
                "HARN_INPUT_SCHEMA_INVALID",
                "frame lineage manifest has invalid typed entries",
            ) from error
        if source_video != fallback.source_video or matches != (fallback.selected_frame,):
            raise GoldenRunRegistryError(
                "HARN_HISTORY_MISMATCH",
                "selected frame is not bound to the source video frame lineage",
            )

    def _require_reachable(
        self,
        ref: ArtifactRef,
        *,
        label: str,
        reachable_identities: set[tuple[str, str, str]],
    ) -> None:
        if _artifact_identity(ref) not in reachable_identities:
            raise GoldenRunNotFoundError(f"{label} is not reachable from workflow history")
        self._artifact_store.resolve(ref)

    def _resolve_control_command(
        self,
        artifact: ArtifactRef,
    ) -> AdvisoryRecordCommand | PromptRevisionCommand:
        if artifact.schema_version == ADVISORY_RECORD_COMMAND_SCHEMA_ID:
            model: type[AdvisoryRecordCommand | PromptRevisionCommand] = AdvisoryRecordCommand
        elif artifact.schema_version == PROMPT_REVISION_COMMAND_SCHEMA_ID:
            model = PromptRevisionCommand
        else:
            raise GoldenRunCorruptionError("control command has an unsupported schema")
        return cast(
            AdvisoryRecordCommand | PromptRevisionCommand,
            self._resolve_canonical_model(artifact, model),
        )

    def _resolve_live_execution(
        self,
        registry_ref: ArtifactRef,
        skill_ref: str,
    ) -> tuple[SkillEvidencePolicy, QualifiedSkillExecutionV2]:
        registry = self._verify_registry_snapshot(registry_ref)
        entries = tuple(
            entry for entry in registry.qualified_skills if entry.skill_ref == skill_ref
        )
        if len(entries) != 1:
            raise GoldenRunRegistryError(
                "HARN_SKILL_UNQUALIFIED",
                f"registry snapshot does not admit one exact Skill: {skill_ref}",
            )
        policy = self._execution_catalog.policy_for(skill_ref)
        live = self._execution_catalog.live_descriptors_for(skill_ref)
        if policy is None or len(live) != 1:
            raise GoldenRunRegistryError(
                "HARN_SKILL_UNQUALIFIED",
                f"no live qualified application intersects {skill_ref}",
            )
        entry = entries[0]
        frozen = self._resolve_descriptor(entry.descriptor_ref)
        descriptor = live[0]
        if _values_differ(
            (
                (type(frozen), type(descriptor)),
                (frozen.model_dump(mode="json"), descriptor.model_dump(mode="json")),
            )
        ):
            raise GoldenRunRegistryError(
                "HARN_DEPENDENCY_DRIFT",
                f"live descriptor differs from the frozen registry: {skill_ref}",
            )
        qualified = QualifiedSkillExecutionV2(
            schema_version="harness.qualified_skill_execution.v2",
            skill_ref=skill_ref,
            descriptor=entry.descriptor_ref,
            input_schema=descriptor.input_schema,
            output_schema=descriptor.output_schema,
            implementation_sha256=descriptor.implementation_sha256,
            qualification_artifact=entry.qualification_ref,
            qualification_report=entry.qualification_report_ref,
            max_attempts=descriptor.max_attempts,
            evidence_policy_id=policy.evidence_policy_id,
            evidence_policy_version=policy.evidence_policy_version,
            evidence_policy_implementation_sha256=(policy.evidence_policy_implementation_sha256),
        )
        return policy, qualified

    def _resolve_frozen_execution(
        self,
        operation: OperationSnapshot,
    ) -> tuple[SkillEvidencePolicy, QualifiedSkillExecutionV2]:
        qualified = self._resolve_canonical_model(
            operation.qualified_execution,
            QualifiedSkillExecutionV2,
        )
        registry = self._verify_registry_snapshot(
            self._read_visible_snapshot(operation).registry_snapshot
        )
        entries = tuple(
            entry for entry in registry.qualified_skills if entry.skill_ref == operation.skill_ref
        )
        policy = self._execution_catalog.policy_for(operation.skill_ref)
        if len(entries) != 1 or policy is None:
            raise GoldenRunCorruptionError("operation no longer has one frozen qualified Skill")
        entry = entries[0]
        descriptor = self._resolve_descriptor(entry.descriptor_ref)
        if _values_differ(
            (
                (qualified.skill_ref, operation.skill_ref),
                (qualified.descriptor, entry.descriptor_ref),
                (qualified.input_schema, descriptor.input_schema),
                (qualified.output_schema, descriptor.output_schema),
                (qualified.implementation_sha256, descriptor.implementation_sha256),
                (qualified.qualification_artifact, entry.qualification_ref),
                (qualified.qualification_report, entry.qualification_report_ref),
                (qualified.max_attempts, descriptor.max_attempts),
                (qualified.evidence_policy_id, policy.evidence_policy_id),
                (qualified.evidence_policy_version, policy.evidence_policy_version),
                (
                    qualified.evidence_policy_implementation_sha256,
                    policy.evidence_policy_implementation_sha256,
                ),
            )
        ):
            raise GoldenRunCorruptionError("operation frozen execution binding is inconsistent")
        return policy, qualified

    def _read_visible_snapshot(self, operation: OperationSnapshot) -> RunSnapshot:
        stored = self._workflow_store.read_snapshot(
            principal_id=operation.principal_id,
            workflow_run_id=operation.workflow_run_id,
        )
        if stored is None:
            raise GoldenRunNotFoundError("golden workflow was not found for this principal")
        return stored.snapshot

    def _normalize_command(
        self,
        policy: SkillEvidencePolicy,
        request: RunCommandRequest,
    ) -> tuple[RunCommandRequest, BaseModel]:
        try:
            parameters = policy.normalize_parameters(request.parameters)
            if type(parameters) is not policy.input_model:
                raise TypeError("evidence policy returned the wrong input model")
            normalized = RunCommandRequest.model_validate(
                {
                    **request.model_dump(mode="python"),
                    "parameters": parameters.model_dump(mode="json"),
                }
            )
            return normalized, parameters
        except (TypeError, ValueError, ValidationError) as error:
            raise GoldenRunRegistryError(
                "HARN_INPUT_SCHEMA_INVALID",
                f"command parameters are invalid for {request.skill_ref}: {error}",
            ) from error

    @staticmethod
    def _authorize(
        policy: SkillEvidencePolicy,
        state: TrustedWorldState,
        parameters: BaseModel,
        *,
        snapshot: RunSnapshot,
    ) -> None:
        try:
            policy.authorize(state, parameters)
            workflow_authorizer = getattr(policy, "authorize_workflow", None)
            if workflow_authorizer is not None:
                workflow_authorizer(
                    state,
                    parameters,
                    SkillAuthorizationContext(
                        workflow_run_id=snapshot.workflow_run_id,
                        principal_id=snapshot.principal_id,
                        receipt_head=snapshot.receipt_head,
                        revision=snapshot.revision,
                        state_ref=snapshot.state_ref,
                    ),
                )
        except (TypeError, ValueError) as error:
            raise GoldenRunRegistryError(
                "HARN_INPUT_SCHEMA_INVALID",
                f"command is not authorized by trusted world state: {error}",
            ) from error

    def _resume_operation(
        self,
        operation: OperationSnapshot,
        request: RunCommandRequest,
    ) -> OperationSnapshot:
        policy, qualified = self._resolve_frozen_execution(operation)
        command = self._resolve_canonical_model(operation.command, RunCommandRequest)
        normalized, parameters = self._normalize_command(policy, request)
        if command != normalized or command.command_id != operation.operation_id:
            raise GoldenRunConflictError("reserved operation command identity differs")
        child_authority = self._execution_catalog.child_authority_for(operation.skill_ref)
        if child_authority is None:
            return operation
        lifecycle = child_authority.read_controlled_lifecycle(operation.child_run_id)
        if lifecycle.terminal is None:
            if lifecycle.invocation is not None:
                return operation
            application = self._execution_catalog.application_for(operation.skill_ref)
            if application is None:
                return operation
            snapshot = self._read_visible_snapshot(operation)
            base_state = self._resolve_canonical_model(snapshot.state_ref, TrustedWorldState)
            if snapshot.active_operation != operation.operation_id:
                raise GoldenRunHeadConflictError(
                    "reserved operation is not the active workflow head"
                )
            live_policy, live_qualified = self._resolve_live_execution(
                snapshot.registry_snapshot,
                operation.skill_ref,
            )
            if live_qualified != qualified or live_policy is not policy:
                raise GoldenRunRegistryError(
                    "HARN_DEPENDENCY_DRIFT",
                    "live qualified execution changed after reservation",
                )
            self._authorize(policy, base_state, parameters, snapshot=snapshot)
            try:
                self._execution_catalog.invoke(
                    operation.skill_ref,
                    parameters,
                    run_id=operation.child_run_id,
                )
            except QualifiedApplicationRunPendingError:
                pass
            lifecycle = child_authority.read_controlled_lifecycle(operation.child_run_id)
            if lifecycle.terminal is None:
                return operation
        self._verify_child_lifecycle(lifecycle, operation, command, qualified)
        assert lifecycle.terminal is not None
        return self._commit_terminal_operation(
            operation,
            command,
            policy,
            qualified,
            lifecycle,
        )

    def _verify_child_lifecycle(
        self,
        lifecycle: ControlledRunLifecycle,
        operation: OperationSnapshot,
        command: RunCommandRequest,
        qualified: QualifiedSkillExecutionV2,
    ) -> None:
        intent = lifecycle.intent
        state = lifecycle.terminal
        if intent is None or state is None:
            raise GoldenRunCorruptionError("terminal child lacks its controlled execution intent")
        skill_id, version = operation.skill_ref.split("@", maxsplit=1)
        parameters_digest = _canonical_json_sha256(command.parameters)
        descriptor = self._resolve_descriptor(qualified.descriptor)
        if _values_differ(
            (
                (intent.run_id, operation.child_run_id),
                (intent.skill_id, skill_id),
                (intent.skill_version, version),
                (intent.input_schema, qualified.input_schema),
                (intent.raw_parameters_sha256, parameters_digest),
                (intent.effective_parameters_sha256, parameters_digest),
                (
                    intent.descriptor_sha256,
                    _canonical_json_sha256(descriptor.model_dump(mode="json")),
                ),
                (intent.implementation_sha256, qualified.implementation_sha256),
                (intent.qualification_sha256, qualified.qualification_artifact.sha256),
                (intent.max_attempts, qualified.max_attempts),
                (state.run_id, operation.child_run_id),
                (state.skill_id, skill_id),
                (state.skill_version, version),
                (state.max_attempts, qualified.max_attempts),
            )
        ):
            raise GoldenRunCorruptionError("terminal child differs from its frozen execution")
        invocation = lifecycle.invocation
        if invocation is not None and _values_differ(
            (
                (invocation.effective_parameters, command.parameters),
                (invocation.dependencies, intent.dependencies),
            )
        ):
            raise GoldenRunCorruptionError("child invocation differs from its frozen command")

    def _commit_terminal_operation(
        self,
        operation: OperationSnapshot,
        command: RunCommandRequest,
        policy: SkillEvidencePolicy,
        qualified: QualifiedSkillExecutionV2,
        lifecycle: ControlledRunLifecycle,
    ) -> OperationSnapshot:
        state = lifecycle.terminal
        assert state is not None and state.ended_at is not None
        current = self._read_visible_snapshot(operation)
        if (
            current.active_operation != operation.operation_id
            or current.revision != command.expected_workflow_revision
            or current.state_sha256 != command.expected_state_sha256
            or current.receipt_head.sha256 != command.expected_receipt_head_sha256
        ):
            raise GoldenRunHeadConflictError("workflow head changed before terminal commit")
        base_state = self._resolve_canonical_model(current.state_ref, TrustedWorldState)
        invocation_ref = (
            None
            if lifecycle.invocation is None
            else self._publish_model(
                lifecycle.invocation,
                name="skill_invocation.json",
                schema_version="harness.skill_invocation.v1",
            )
        )
        run_state_ref = self._publish_model(
            state,
            name="run_state.json",
            schema_version="harness.run_state.v1",
        )
        supporting = _canonical_artifacts(state.artifacts)
        for artifact in supporting:
            self._artifact_store.resolve(artifact)
        claims = policy.claims(state)
        fresh_observations = policy.fresh_observations(state, lifecycle.invocation)
        _verify_observation_projection(
            state=state,
            claims=claims,
            observations=fresh_observations,
            supporting=supporting,
        )
        receipt = TrustedToolReceiptV2(
            schema_version="harness.trusted_tool_receipt.v2",
            workflow_run_id=operation.workflow_run_id,
            operation_id=operation.operation_id,
            principal_id=operation.principal_id,
            command=operation.command,
            base_revision=operation.base_revision,
            base_state_sha256=base_state.state_sha256,
            base_state=current.state_ref,
            registry_snapshot=current.registry_snapshot,
            skill=qualified,
            status=state.status,
            started_at=operation.started_at,
            ended_at=state.ended_at,
            child_run_id=operation.child_run_id,
            invocation=invocation_ref,
            run_state=run_state_ref,
            invocation_digest=state.invocation_digest,
            typed_output=state.output,
            blocker=state.blocker,
            supporting_artifacts=supporting,
            derived_fact_claims=claims,
        )
        receipt_ref = self._publish_model(
            receipt,
            name="trusted_tool_receipt.json",
            schema_version="harness.trusted_tool_receipt.v2",
        )
        delta: StateDeltaV2 | None = None
        delta_ref: ArtifactRef | None = None
        resulting_state = base_state
        resulting_state_ref = current.state_ref
        if state.status is RunStatus.SUCCEEDED:
            mutations = _mutations_from_claims(
                base_state,
                claims,
                receipt_ref=receipt_ref,
                observed_at=state.ended_at,
                observations=fresh_observations,
            )
            unhashed = StateDeltaV2.model_construct(
                schema_version="harness.state_delta.v2",
                base_state_sha256=base_state.state_sha256,
                effective_at=state.ended_at,
                receipt=receipt_ref,
                mutations=mutations,
                delta_sha256="0" * 64,
            )
            delta = StateDeltaV2(
                **unhashed.model_dump(mode="python", exclude={"delta_sha256"}),
                delta_sha256=state_delta_v2_sha256(unhashed),
            )
            delta_ref = self._publish_model(
                delta,
                name="state_delta.json",
                schema_version="harness.state_delta.v2",
            )
            resulting_state = advance_trusted_world_state(
                base_state,
                effective_at=delta.effective_at,
                mutations=delta.mutations,
            )
            resulting_state_ref = self._publish_model(
                resulting_state,
                name="trusted_world_state.json",
                schema_version="harness.trusted_world_state.v1",
            )
        result = ToolResultV2(
            schema_version="harness.tool_result.v2",
            workflow_run_id=operation.workflow_run_id,
            operation_id=operation.operation_id,
            skill_ref=operation.skill_ref,
            status=state.status,
            started_at=operation.started_at,
            ended_at=state.ended_at,
            child_run_id=operation.child_run_id,
            invocation=invocation_ref,
            run_state=run_state_ref,
            typed_output=state.output,
            state_delta=delta_ref,
            diagnostics=policy.diagnostics(state),
            trusted_receipt=receipt_ref,
            fresh_observations=fresh_observations,
            blocker=state.blocker,
        )
        policy.verify_result(
            state=state,
            claims=claims,
            result=result,
            supporting=supporting,
        )
        result_ref = self._publish_model(
            result,
            name="tool_result.json",
            schema_version="harness.tool_result.v2",
        )
        workflow_receipt = WorkflowOperationReceiptV2(
            schema_version="harness.workflow_operation_receipt.v2",
            workflow_run_id=operation.workflow_run_id,
            operation_id=operation.operation_id,
            principal_id=operation.principal_id,
            command=operation.command,
            predecessor_receipt=current.receipt_head,
            tool_receipt=receipt_ref,
            tool_result=result_ref,
            state_delta=delta_ref,
            base_revision=operation.base_revision,
            committed_revision=operation.base_revision + 1,
            base_state_sha256=base_state.state_sha256,
            resulting_state_sha256=resulting_state.state_sha256,
            resulting_state=resulting_state_ref,
            status=state.status,
            committed_at=state.ended_at,
        )
        workflow_receipt_ref = self._publish_model(
            workflow_receipt,
            name="workflow_operation_receipt.json",
            schema_version="harness.workflow_operation_receipt.v2",
        )
        terminal = OperationSnapshot.model_validate(
            operation.model_copy(
                update={
                    "status": state.status,
                    "committed_revision": operation.base_revision + 1,
                    "tool_result": result_ref,
                    "ended_at": state.ended_at,
                }
            ).model_dump(mode="python")
        )
        next_snapshot = RunSnapshot.model_validate(
            current.model_copy(
                update={
                    "revision": operation.base_revision + 1,
                    "turn_seq": current.turn_seq + 1,
                    "state_sha256": resulting_state.state_sha256,
                    "state_ref": resulting_state_ref,
                    "receipt_head": workflow_receipt_ref,
                    "active_operation": None,
                    "child_runs": (*current.child_runs, operation.child_run_id),
                    "updated_at": state.ended_at,
                }
            ).model_dump(mode="python")
        )
        persisted = self._workflow_store.commit_operation(
            principal_id=operation.principal_id,
            running=operation,
            terminal=terminal,
            snapshot=next_snapshot,
        )
        if persisted is None:
            raise GoldenRunNotFoundError("golden workflow was not found for this principal")
        return persisted.operation

    def _verify_advanced_snapshot(
        self,
        snapshot: RunSnapshot,
        *,
        request: RunStartRequest,
        persisted_idempotency_key: str,
        persisted_request_sha256: str,
    ) -> None:
        entries = self._workflow_store.read_entries(
            principal_id=snapshot.principal_id,
            workflow_run_id=snapshot.workflow_run_id,
        )
        terminal_entries = tuple(
            entry
            for entry in entries
            if entry.control is not None
            or (entry.operation is not None and entry.operation.status is not RunStatus.RUNNING)
        )
        running = tuple(
            cast(OperationSnapshot, entry.operation)
            for entry in entries
            if entry.operation is not None and entry.operation.status is RunStatus.RUNNING
        )
        terminal_operations = tuple(
            cast(OperationSnapshot, entry.operation)
            for entry in terminal_entries
            if entry.operation is not None
        )
        expected_active = None if not running else running[0].operation_id
        if len(running) > 1 or _values_differ(
            (
                (len(terminal_entries), snapshot.revision),
                (
                    tuple(entry.base_revision for entry in entries),
                    tuple(range(len(entries))),
                ),
                (snapshot.turn_seq, snapshot.revision),
                (
                    snapshot.child_runs,
                    tuple(op.child_run_id for op in terminal_operations),
                ),
                (snapshot.active_operation, expected_active),
            )
        ):
            raise GoldenRunCorruptionError("workflow turn journal and head disagree")

        receipts: dict[UUID, tuple[ArtifactRef, BaseModel]] = {}
        predecessor = snapshot.receipt_head
        for entry in reversed(terminal_entries):
            if entry.operation is not None:
                operation = entry.operation
                receipt = cast(
                    WorkflowOperationReceiptV2,
                    self._resolve_canonical_model(
                        predecessor,
                        WorkflowOperationReceiptV2,
                    ),
                )
                if receipt.operation_id != operation.operation_id:
                    raise GoldenRunCorruptionError("workflow receipt chain is not turn ordered")
                receipts[entry.entry_id] = (predecessor, receipt)
                predecessor = receipt.predecessor_receipt
            else:
                control = cast(ControlCommitSnapshot, entry.control)
                if predecessor != control.receipt:
                    raise GoldenRunCorruptionError(
                        "workflow control receipt chain is not turn ordered"
                    )
                receipt = self._resolve_control_receipt(predecessor)
                receipts[entry.entry_id] = (predecessor, receipt)
                predecessor = receipt.predecessor_receipt
        start_receipt = self._resolve_canonical_model(predecessor, WorkflowStartReceipt)
        initial = RunSnapshot(
            schema_version="harness.run_snapshot.v1",
            workflow_run_id=snapshot.workflow_run_id,
            principal_id=snapshot.principal_id,
            workspace=snapshot.workspace,
            requested_profile=snapshot.requested_profile,
            revision=0,
            status="active",
            turn_seq=0,
            state_sha256=start_receipt.state_sha256,
            state_ref=start_receipt.state_ref,
            start_request_ref=snapshot.start_request_ref,
            registry_snapshot=snapshot.registry_snapshot,
            receipt_head=predecessor,
            active_operation=None,
            child_runs=(),
            started_at=snapshot.started_at,
            updated_at=snapshot.started_at,
        )
        self._verify_start_snapshot(
            initial,
            request=request,
            request_sha256=_request_sha256(request),
            persisted_idempotency_key=persisted_idempotency_key,
            persisted_request_sha256=persisted_request_sha256,
        )
        prior = initial
        for entry in terminal_entries:
            receipt_ref, receipt = receipts[entry.entry_id]
            if entry.operation is not None:
                if type(receipt) is not WorkflowOperationReceiptV2:
                    raise GoldenRunCorruptionError(
                        "skill turn does not have a workflow operation receipt"
                    )
                prior = self._verify_terminal_evidence(
                    prior,
                    entry.operation,
                    receipt_ref=receipt_ref,
                    receipt=receipt,
                )
            else:
                control = cast(ControlCommitSnapshot, entry.control)
                if type(receipt) not in {ExternalAgentAdvisoryReceipt, PromptRevision}:
                    raise GoldenRunCorruptionError("control turn does not have a control receipt")
                prior = self._verify_control_evidence(
                    prior,
                    control,
                    receipt_ref=receipt_ref,
                    receipt=cast(
                        ExternalAgentAdvisoryReceipt | PromptRevision,
                        receipt,
                    ),
                )
        if running:
            self._verify_running_operation(prior, running[0])
            prior = RunSnapshot.model_validate(
                prior.model_copy(
                    update={
                        "active_operation": running[0].operation_id,
                        "updated_at": running[0].started_at,
                    }
                ).model_dump(mode="python")
            )
        if prior != snapshot:
            raise GoldenRunCorruptionError("workflow head cannot be reconstructed from evidence")

    def _verify_running_operation(
        self,
        prior: RunSnapshot,
        operation: OperationSnapshot,
    ) -> None:
        command = self._resolve_canonical_model(operation.command, RunCommandRequest)
        self._resolve_frozen_execution(operation)
        if (
            _values_differ(
                (
                    (command.command_id, operation.operation_id),
                    (command.workflow_run_id, prior.workflow_run_id),
                    (command.principal_id, prior.principal_id),
                    (command.idempotency_key, operation.idempotency_key),
                    (command.skill_ref, operation.skill_ref),
                    (command.expected_workflow_revision, prior.revision),
                    (command.expected_state_sha256, prior.state_sha256),
                    (command.expected_receipt_head_sha256, prior.receipt_head.sha256),
                    (operation.base_revision, prior.revision),
                )
            )
            or operation.started_at < prior.updated_at
        ):
            raise GoldenRunCorruptionError("running operation differs from its reserved head")

    def _verify_control_evidence(
        self,
        prior: RunSnapshot,
        control: ControlCommitSnapshot,
        *,
        receipt_ref: ArtifactRef,
        receipt: ExternalAgentAdvisoryReceipt | PromptRevision,
    ) -> RunSnapshot:
        command = self._resolve_control_command(control.command)
        common_invalid = _values_differ(
            (
                (control.command_id, command.command_id),
                (control.workflow_run_id, prior.workflow_run_id),
                (control.principal_id, prior.principal_id),
                (control.idempotency_key, command.idempotency_key),
                (control.predecessor_receipt, prior.receipt_head),
                (control.base_revision, prior.revision),
                (control.committed_revision, prior.revision + 1),
                (control.base_turn_seq, prior.turn_seq),
                (control.committed_turn_seq, prior.turn_seq + 1),
                (control.base_state_sha256, prior.state_sha256),
                (control.base_state, prior.state_ref),
                (control.resulting_state_sha256, prior.state_sha256),
                (control.resulting_state, prior.state_ref),
                (control.payload, receipt_ref),
                (control.receipt, receipt_ref),
                (control.receipt_head, receipt_ref),
                (command.workflow_run_id, prior.workflow_run_id),
                (command.principal_id, prior.principal_id),
                (command.expected_workflow_revision, prior.revision),
                (command.expected_state_sha256, prior.state_sha256),
                (command.expected_receipt_head_sha256, prior.receipt_head.sha256),
                (receipt.command_id, command.command_id),
                (receipt.workflow_run_id, prior.workflow_run_id),
                (receipt.principal_id, prior.principal_id),
                (receipt.command, control.command),
                (receipt.predecessor_receipt, prior.receipt_head),
                (receipt.base_revision, prior.revision),
                (receipt.committed_revision, prior.revision + 1),
                (receipt.base_turn_seq, prior.turn_seq),
                (receipt.committed_turn_seq, prior.turn_seq + 1),
                (receipt.base_state_sha256, prior.state_sha256),
                (receipt.base_state, prior.state_ref),
                (receipt.resulting_state_sha256, prior.state_sha256),
                (receipt.resulting_state, prior.state_ref),
                (receipt.committed_at, control.committed_at),
            )
        )
        if common_invalid or control.committed_at < prior.updated_at:
            raise GoldenRunCorruptionError("workflow control receipt is inconsistently bound")

        reachable_identities = {
            _artifact_identity(ref) for ref in self._reachable_workflow_artifacts(prior)
        }
        if control.control_kind == "advisory":
            if (
                type(command) is not AdvisoryRecordCommand
                or type(receipt) is not ExternalAgentAdvisoryReceipt
            ):
                raise GoldenRunCorruptionError("advisory turn has the wrong typed evidence")
            advisory_invalid = _values_differ(
                (
                    (receipt.advisory_kind, command.advisory_kind),
                    (receipt.disposition, command.disposition),
                    (receipt.summary, command.summary),
                    (receipt.recommended_action, command.recommended_action),
                    (receipt.source_prompt, command.source_prompt),
                    (receipt.source_prompt_revision, command.source_prompt_revision),
                    (receipt.failure_receipts, command.failure_receipts),
                    (receipt.observation_citations, command.observation_citations),
                    (receipt.diagnostic_refs, command.diagnostic_refs),
                    (receipt.external_agent_executed, command.external_agent_executed),
                )
            )
            if advisory_invalid:
                raise GoldenRunCorruptionError("advisory receipt differs from its exact command")
            self._verify_advisory_lineage(
                command,
                current=prior,
                at=control.committed_at,
                reachable_identities=reachable_identities,
            )
        else:
            if type(command) is not PromptRevisionCommand or type(receipt) is not PromptRevision:
                raise GoldenRunCorruptionError("prompt revision turn has the wrong typed evidence")
            advisory, predecessor_prompt, revision_number, _ = self._verify_prompt_lineage(
                command,
                current=prior,
                at=control.committed_at,
                reachable_identities=reachable_identities,
            )
            revision_invalid = _values_differ(
                (
                    (receipt.prompt_revision_number, revision_number),
                    (receipt.source_prompt, command.source_prompt),
                    (receipt.predecessor_prompt_revision, predecessor_prompt),
                    (receipt.advisory_receipt, command.advisory_receipt),
                    (receipt.failure_receipts, command.failure_receipts),
                    (receipt.observation_citations, advisory.observation_citations),
                    (receipt.predecessor_package, command.predecessor_package),
                    (receipt.patch, command.patch),
                    (receipt.fallback, command.fallback),
                    (receipt.next_compile_skill_ref, command.next_compile_skill_ref),
                    (receipt.external_agent_executed, command.external_agent_executed),
                )
            )
            if revision_invalid:
                raise GoldenRunCorruptionError(
                    "prompt revision differs from its exact command and advisory lineage"
                )
            revised_payload = self._artifact_store.resolve(receipt.revised_prompt).path.read_bytes()
            if revised_payload != command.patch.replacement_text.encode("utf-8"):
                raise GoldenRunCorruptionError(
                    "revised prompt bytes differ from the full-replacement patch"
                )
        return RunSnapshot.model_validate(
            prior.model_copy(
                update={
                    "revision": control.committed_revision,
                    "turn_seq": control.committed_turn_seq,
                    "receipt_head": receipt_ref,
                    "updated_at": control.committed_at,
                }
            ).model_dump(mode="python")
        )

    def _verify_terminal_evidence(
        self,
        prior: RunSnapshot,
        operation: OperationSnapshot,
        *,
        receipt_ref: ArtifactRef,
        receipt: WorkflowOperationReceiptV2,
    ) -> RunSnapshot:
        self._verify_running_operation(
            prior,
            OperationSnapshot.model_validate(
                operation.model_copy(
                    update={
                        "status": RunStatus.RUNNING,
                        "committed_revision": None,
                        "tool_result": None,
                        "ended_at": None,
                    }
                ).model_dump(mode="python")
            ),
        )
        policy, qualified = self._resolve_frozen_execution(operation)
        assert operation.tool_result is not None and operation.ended_at is not None
        result = self._resolve_canonical_model(operation.tool_result, ToolResultV2)
        tool_receipt = self._resolve_canonical_model(
            result.trusted_receipt,
            TrustedToolReceiptV2,
        )
        child_state = self._resolve_canonical_model(result.run_state, RunState)
        invocation = (
            None
            if result.invocation is None
            else self._resolve_canonical_model(result.invocation, Invocation)
        )
        supporting = _canonical_artifacts(child_state.artifacts)
        for artifact in supporting:
            self._artifact_store.resolve(artifact)
        claims = policy.claims(child_state)
        fresh_observations = policy.fresh_observations(child_state, invocation)
        _verify_observation_projection(
            state=child_state,
            claims=claims,
            observations=fresh_observations,
            supporting=supporting,
        )
        policy.verify_result(
            state=child_state,
            claims=claims,
            result=result,
            supporting=supporting,
        )
        common_invalid = _values_differ(
            (
                (result.workflow_run_id, operation.workflow_run_id),
                (result.operation_id, operation.operation_id),
                (result.skill_ref, operation.skill_ref),
                (result.status, operation.status),
                (result.started_at, operation.started_at),
                (result.ended_at, operation.ended_at),
                (result.child_run_id, operation.child_run_id),
                (result.typed_output, child_state.output),
                (result.blocker, child_state.blocker),
                (child_state.run_id, operation.child_run_id),
                (result.invocation, tool_receipt.invocation),
                (result.run_state, tool_receipt.run_state),
                (tool_receipt.workflow_run_id, operation.workflow_run_id),
                (tool_receipt.operation_id, operation.operation_id),
                (tool_receipt.principal_id, operation.principal_id),
                (tool_receipt.command, operation.command),
                (tool_receipt.base_revision, operation.base_revision),
                (tool_receipt.base_state_sha256, prior.state_sha256),
                (tool_receipt.base_state, prior.state_ref),
                (tool_receipt.registry_snapshot, prior.registry_snapshot),
                (tool_receipt.skill, qualified),
                (tool_receipt.status, operation.status),
                (tool_receipt.started_at, operation.started_at),
                (tool_receipt.ended_at, operation.ended_at),
                (tool_receipt.child_run_id, operation.child_run_id),
                (tool_receipt.invocation_digest, child_state.invocation_digest),
                (tool_receipt.typed_output, child_state.output),
                (tool_receipt.blocker, child_state.blocker),
                (tool_receipt.supporting_artifacts, supporting),
                (tool_receipt.derived_fact_claims, claims),
                (result.fresh_observations, fresh_observations),
                ((invocation is None), (child_state.invocation_digest is None)),
                (
                    None if invocation is None else invocation.invocation_digest,
                    child_state.invocation_digest,
                ),
            )
        )
        if common_invalid:
            raise GoldenRunCorruptionError("terminal ToolResult closure is inconsistently bound")

        resulting_state = prior.state_ref
        resulting_sha = prior.state_sha256
        delta_ref: ArtifactRef | None = None
        if operation.status is RunStatus.SUCCEEDED:
            assert result.state_delta is not None
            if receipt.state_delta != result.state_delta:
                raise GoldenRunCorruptionError("state delta differs across result evidence")
            delta = self._resolve_canonical_model(result.state_delta, StateDeltaV2)
            if (
                delta.delta_sha256 != state_delta_v2_sha256(delta)
                or delta.base_state_sha256 != prior.state_sha256
                or delta.receipt != result.trusted_receipt
                or not result.started_at <= delta.effective_at <= result.ended_at
            ):
                raise GoldenRunCorruptionError("state delta differs across result evidence")
            base_state = self._resolve_canonical_model(prior.state_ref, TrustedWorldState)
            expected_mutations = _mutations_from_claims(
                base_state,
                claims,
                receipt_ref=result.trusted_receipt,
                observed_at=operation.ended_at,
                observations=fresh_observations,
            )
            if delta.mutations != expected_mutations:
                raise GoldenRunCorruptionError("state delta does not implement receipt claims")
            advanced = advance_trusted_world_state(
                base_state,
                effective_at=delta.effective_at,
                mutations=delta.mutations,
            )
            published = self._resolve_canonical_model(receipt.resulting_state, TrustedWorldState)
            if advanced != published:
                raise GoldenRunCorruptionError("resulting state does not apply its exact delta")
            for fact in published.facts:
                self._artifact_store.resolve(fact.source_artifact)
            resulting_state = receipt.resulting_state
            resulting_sha = published.state_sha256
            delta_ref = result.state_delta
        elif result.state_delta is not None or receipt.state_delta is not None:
            raise GoldenRunCorruptionError("non-successful operation cannot advance state")

        if _values_differ(
            (
                (receipt_ref.schema_version, "harness.workflow_operation_receipt.v2"),
                (receipt.workflow_run_id, operation.workflow_run_id),
                (receipt.operation_id, operation.operation_id),
                (receipt.principal_id, operation.principal_id),
                (receipt.command, operation.command),
                (receipt.predecessor_receipt, prior.receipt_head),
                (receipt.tool_receipt, result.trusted_receipt),
                (receipt.tool_result, operation.tool_result),
                (receipt.state_delta, delta_ref),
                (receipt.base_revision, operation.base_revision),
                (receipt.committed_revision, operation.committed_revision),
                (receipt.base_state_sha256, prior.state_sha256),
                (receipt.resulting_state_sha256, resulting_sha),
                (receipt.resulting_state, resulting_state),
                (receipt.status, operation.status),
                (receipt.committed_at, operation.ended_at),
            )
        ):
            raise GoldenRunCorruptionError("workflow operation receipt is inconsistently bound")
        return RunSnapshot.model_validate(
            prior.model_copy(
                update={
                    "revision": operation.committed_revision,
                    "turn_seq": prior.turn_seq + 1,
                    "state_sha256": resulting_sha,
                    "state_ref": resulting_state,
                    "receipt_head": receipt_ref,
                    "child_runs": (*prior.child_runs, operation.child_run_id),
                    "updated_at": operation.ended_at,
                }
            ).model_dump(mode="python")
        )

    def _create_start(self, request: RunStartRequest, *, request_sha256: str) -> RunSnapshot:
        started_at = self._clock()
        if started_at.utcoffset() != timezone.utc.utcoffset(started_at):
            raise ValueError("clock must return a UTC datetime")
        workflow_run_id = self._workflow_id_factory()
        self._verify_registry_snapshot(request.registry_snapshot)

        facts = tuple(
            self._fact_from_user_evidence(artifact) for artifact in request.initial_fact_evidence
        )
        state = build_world_state(facts, as_of=started_at)
        state_ref = self._publish_model(
            state,
            name="trusted_world_state.json",
            schema_version="harness.trusted_world_state.v1",
        )
        request_ref = self._publish_model(
            request,
            name="run_start_request.json",
            schema_version="harness.run_start_request.v1",
        )
        receipt = WorkflowStartReceipt(
            schema_version="harness.workflow_start_receipt.v1",
            workflow_run_id=workflow_run_id,
            principal_id=request.principal_id,
            workspace=request.workspace,
            requested_profile=request.requested_profile,
            request_sha256=request_sha256,
            request_ref=request_ref,
            state_sha256=state.state_sha256,
            state_ref=state_ref,
            registry_snapshot=request.registry_snapshot,
            started_at=started_at,
        )
        receipt_ref = self._publish_model(
            receipt,
            name="workflow_start_receipt.json",
            schema_version="harness.workflow_start_receipt.v1",
        )
        return RunSnapshot(
            schema_version="harness.run_snapshot.v1",
            workflow_run_id=workflow_run_id,
            principal_id=request.principal_id,
            workspace=request.workspace,
            requested_profile=request.requested_profile,
            revision=0,
            status="active",
            turn_seq=0,
            state_sha256=state.state_sha256,
            state_ref=state_ref,
            start_request_ref=request_ref,
            registry_snapshot=request.registry_snapshot,
            receipt_head=receipt_ref,
            active_operation=None,
            child_runs=(),
            started_at=started_at,
            updated_at=started_at,
        )

    def _fact_from_user_evidence(self, artifact: ArtifactRef) -> WorldFact:
        payload = self._artifact_store.resolve(artifact).path.read_bytes()
        evidence = WorldFactEvidence.model_validate_json(payload)
        if payload != _canonical_model_bytes(evidence):
            raise ValueError("initial fact evidence must use canonical JSON bytes")
        if evidence.source_kind != "user_input":
            raise ValueError("initial fact evidence must declare user_input provenance")
        if not evidence.key.startswith("request."):
            raise ValueError("initial fact evidence key must use the request namespace")
        return WorldFact(
            key=evidence.key,
            value=evidence.value,
            source_kind="user_input",
            source_artifact=artifact,
            observed_at=evidence.observed_at,
            valid_until=evidence.valid_until,
        )

    def _verify_start_snapshot(
        self,
        snapshot: RunSnapshot,
        *,
        request: RunStartRequest,
        request_sha256: str,
        persisted_idempotency_key: str,
        persisted_request_sha256: str,
    ) -> None:
        if persisted_idempotency_key != request.idempotency_key:
            raise GoldenRunCorruptionError(
                "persisted workflow has inconsistent idempotency metadata"
            )
        if persisted_request_sha256 != request_sha256:
            raise GoldenRunCorruptionError(
                "persisted workflow has inconsistent logical request identity"
            )
        if (
            snapshot.principal_id != request.principal_id
            or snapshot.workspace != request.workspace
            or snapshot.requested_profile != request.requested_profile
            or snapshot.registry_snapshot != request.registry_snapshot
            or snapshot.revision != 0
            or snapshot.status.value != "active"
            or snapshot.turn_seq != 0
            or snapshot.active_operation is not None
            or snapshot.child_runs
            or snapshot.updated_at != snapshot.started_at
        ):
            raise GoldenRunCorruptionError(
                "persisted workflow start failed its request receipt binding"
            )
        try:
            self._verify_registry_snapshot(snapshot.registry_snapshot)
            state = self._resolve_canonical_model(
                snapshot.state_ref,
                TrustedWorldState,
            )
            receipt = self._resolve_canonical_model(
                snapshot.receipt_head,
                WorkflowStartReceipt,
            )
            persisted_request = self._resolve_canonical_model(
                snapshot.start_request_ref,
                RunStartRequest,
            )
            for fact in state.facts:
                self._artifact_store.resolve(fact.source_artifact)
        except (
            ArtifactResolutionError,
            GoldenRunRegistryError,
            ValidationError,
            TypeError,
            ValueError,
        ) as error:
            raise GoldenRunCorruptionError(
                f"persisted workflow start failed its CAS closure: {error}"
            ) from error
        if (
            state.state_sha256 != snapshot.state_sha256
            or snapshot.state_ref.sha256
            != hashlib.sha256(_canonical_model_bytes(state)).hexdigest()
            or tuple(fact.source_artifact for fact in state.facts) != request.initial_fact_evidence
            or persisted_request != request
            or receipt.workflow_run_id != snapshot.workflow_run_id
            or receipt.principal_id != snapshot.principal_id
            or receipt.workspace != snapshot.workspace
            or receipt.requested_profile != snapshot.requested_profile
            or receipt.request_sha256 != request_sha256
            or receipt.request_ref != snapshot.start_request_ref
            or receipt.state_sha256 != snapshot.state_sha256
            or receipt.state_ref != snapshot.state_ref
            or receipt.registry_snapshot != snapshot.registry_snapshot
            or receipt.started_at != snapshot.started_at
        ):
            raise GoldenRunCorruptionError(
                "persisted workflow start failed its request receipt binding"
            )

    def _verify_registry_snapshot(
        self,
        artifact: ArtifactRef,
    ) -> RegistrySnapshot:
        try:
            snapshot = self._resolve_canonical_model(artifact, RegistrySnapshot)
        except ArtifactResolutionError as error:
            raise GoldenRunRegistryError(
                "HARN_ARTIFACT_UNAVAILABLE",
                f"registry snapshot is unavailable: {error}",
            ) from error
        except (ValidationError, TypeError, ValueError) as error:
            raise GoldenRunRegistryError(
                "HARN_INPUT_SCHEMA_INVALID",
                f"registry snapshot is not strict canonical JSON: {error}",
            ) from error
        for entry in snapshot.qualified_skills:
            try:
                descriptor_model = (
                    SkillDescriptor
                    if entry.descriptor_ref.schema_version == "harness.skill_descriptor.v1"
                    else SkillDescriptorV2
                )
                descriptor = self._resolve_canonical_model(
                    entry.descriptor_ref,
                    descriptor_model,
                )
                qualification = self._resolve_canonical_model(
                    entry.qualification_ref,
                    SkillQualification,
                )
                report = self._resolve_canonical_model(
                    entry.qualification_report_ref,
                    QualificationReportV1,
                )
            except ArtifactResolutionError as error:
                raise GoldenRunRegistryError(
                    "HARN_ARTIFACT_UNAVAILABLE",
                    f"registry qualification artifact is unavailable: {entry.skill_ref}",
                ) from error
            except (ValidationError, TypeError, ValueError) as error:
                raise GoldenRunRegistryError(
                    "HARN_INPUT_SCHEMA_INVALID",
                    f"registry qualification artifact is invalid: {entry.skill_ref}: {error}",
                ) from error
            descriptor_skill_ref = f"{descriptor.skill_id}@{descriptor.version}"
            if (
                descriptor_skill_ref != entry.skill_ref
                or descriptor.mcp_tool_name != entry.mcp_tool_name
                or descriptor.qualification_artifact != entry.qualification_ref
                or qualification.skill_ref != entry.skill_ref
                or qualification.report_sha256 != entry.qualification_report_ref.sha256
                or report.skill_ref != entry.skill_ref
                or report.status != qualification.status
                or report.deterministic_case_id != qualification.deterministic_case_id
                or report.regression_command != qualification.regression_command
                or report.implementation_sha256 != descriptor.implementation_sha256
            ):
                raise GoldenRunRegistryError(
                    "HARN_SKILL_UNQUALIFIED",
                    f"registry snapshot has an unbound qualification closure: {entry.skill_ref}",
                )
            try:
                schema_model(descriptor.input_schema)
                schema_model(descriptor.output_schema)
            except KeyError as error:
                raise GoldenRunRegistryError(
                    "HARN_DEPENDENCY_DRIFT",
                    f"registry descriptor references an unavailable schema: {entry.skill_ref}",
                ) from error
        return snapshot

    def _resolve_descriptor(self, artifact: ArtifactRef) -> RegisteredSkillDescriptor:
        model = (
            SkillDescriptor
            if artifact.schema_version == "harness.skill_descriptor.v1"
            else SkillDescriptorV2
        )
        return self._resolve_canonical_model(artifact, model)

    def _resolve_canonical_model(
        self,
        artifact: ArtifactRef,
        model: type[BaseModel],
    ) -> BaseModel:
        payload = self._artifact_store.resolve(artifact).path.read_bytes()
        parsed = model.model_validate_json(payload)
        if payload != _canonical_model_bytes(parsed):
            raise ValueError("artifact does not use canonical JSON bytes")
        return parsed

    def _publish_model(
        self,
        model: BaseModel,
        *,
        name: str,
        schema_version: str,
    ) -> ArtifactRef:
        payload = _canonical_model_bytes(model)
        with tempfile.TemporaryDirectory(prefix="harness-golden-run-") as temporary_root:
            source = Path(temporary_root) / name
            source.write_bytes(payload)
            return self._artifact_store.put_file(
                source,
                name=name,
                media_type="application/json",
                schema_version=schema_version,
            )

    def _publish_bytes(
        self,
        payload: bytes,
        *,
        name: str,
        media_type: str,
        schema_version: str | None,
    ) -> ArtifactRef:
        with tempfile.TemporaryDirectory(prefix="harness-golden-control-") as temporary_root:
            source = Path(temporary_root) / name
            source.write_bytes(payload)
            return self._artifact_store.put_file(
                source,
                name=name,
                media_type=media_type,
                schema_version=schema_version,
            )


def _canonical_model_bytes(model: BaseModel) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _canonical_json_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _values_differ(pairs: tuple[tuple[object, object], ...]) -> bool:
    return any(actual != expected for actual, expected in pairs)


def _require_utc_clock(value: datetime) -> None:
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError("clock must return a UTC datetime")


def _artifact_identity(ref: ArtifactRef) -> tuple[str, str, str]:
    return (ref.sha256, ref.schema_version or "", ref.media_type)


def _canonical_artifacts(artifacts: tuple[ArtifactRef, ...]) -> tuple[ArtifactRef, ...]:
    ordered = tuple(sorted(artifacts, key=_artifact_identity))
    identities = tuple(_artifact_identity(ref) for ref in ordered)
    if len(identities) != len(set(identities)):
        raise GoldenRunCorruptionError("terminal child repeats supporting artifact identity")
    return ordered


def _mutations_from_claims(
    state: TrustedWorldState,
    claims: tuple[ReceiptDerivedFactClaimV2, ...],
    *,
    receipt_ref: ArtifactRef,
    observed_at: datetime,
    observations: tuple[WorldFact, ...] = (),
) -> tuple[StateMutation, ...]:
    current = {fact.key: fact for fact in state.facts}
    observation_by_key = {observation.key: observation for observation in observations}
    mutations = []
    for claim in claims:
        key = claim.key
        existing = current.get(key)
        expected = None if existing is None else fact_sha256(existing)
        if claim.operation == "retract":
            if existing is None:
                raise GoldenRunCorruptionError(f"cannot retract absent trusted fact: {key}")
            mutation = StateMutation(
                operation="retract",
                key=key,
                expected_fact_sha256=expected,
                fact=None,
            )
        else:
            observation = observation_by_key.get(key)
            mutation = StateMutation(
                operation="upsert",
                key=key,
                expected_fact_sha256=expected,
                fact=WorldFact(
                    key=key,
                    value=claim.value,
                    source_kind="trusted_receipt",
                    source_artifact=receipt_ref,
                    observed_at=(observed_at if observation is None else observation.observed_at),
                    valid_until=None if observation is None else observation.valid_until,
                ),
            )
        mutations.append(mutation)
    return tuple(sorted(mutations, key=lambda item: item.key))


def _verify_observation_projection(
    *,
    state: RunState,
    claims: tuple[ReceiptDerivedFactClaimV2, ...],
    observations: tuple[WorldFact, ...],
    supporting: tuple[ArtifactRef, ...],
) -> None:
    if not observations:
        return
    if state.status is not RunStatus.SUCCEEDED:
        raise GoldenRunCorruptionError("non-successful child cannot project fresh observations")
    supporting_identities = {_artifact_identity(ref) for ref in supporting}
    claims_by_key = {claim.key: claim for claim in claims}
    for observation in observations:
        claim = claims_by_key.get(observation.key)
        if (
            claim is None
            or claim.operation != "upsert"
            or claim.value != observation.value
            or observation.valid_until is None
            or _artifact_identity(observation.source_artifact) not in supporting_identities
        ):
            raise GoldenRunCorruptionError(
                "fresh observation does not equal a receipt claim with supporting evidence"
            )


def _request_sha256(request: RunStartRequest) -> str:
    payload = {
        "domain": "harness.run_start_request.identity.v1",
        "request": request.model_dump(mode="json"),
    }
    return hashlib.sha256(
        (
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "GoldenRunConflictError",
    "GoldenRunCorruptionError",
    "GoldenRunHeadConflictError",
    "GoldenRunHarness",
    "GoldenRunNotFoundError",
    "GoldenRunRegistryError",
    "WorkflowResourceAccess",
]
