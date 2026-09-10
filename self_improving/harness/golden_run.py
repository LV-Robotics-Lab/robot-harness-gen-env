"""Deep aggregate entry point for one parent Golden Workflow."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError

from .artifacts import ArtifactResolutionError, LocalArtifactStore
from .golden_execution import (
    GoldenExecutionCatalog,
    QualifiedApplicationRunPendingError,
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
from .schemas.registry_snapshot import RegistrySnapshot
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
        self._verify_start_snapshot(
            snapshot,
            request=request,
            request_sha256=request_sha256,
            persisted_idempotency_key=stored.idempotency_key,
            persisted_request_sha256=stored.request_sha256,
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
        self._authorize(policy, base_state, parameters)
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
    ) -> None:
        try:
            policy.authorize(state, parameters)
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
            self._authorize(policy, base_state, parameters)
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
            fresh_observations=(),
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
        operations = tuple(
            stored.operation
            for stored in self._workflow_store.read_operations(
                principal_id=snapshot.principal_id,
                workflow_run_id=snapshot.workflow_run_id,
            )
        )
        terminal = tuple(op for op in operations if op.status is not RunStatus.RUNNING)
        running = tuple(op for op in operations if op.status is RunStatus.RUNNING)
        expected_active = None if not running else running[0].operation_id
        if len(running) > 1 or _values_differ(
            (
                (len(terminal), snapshot.revision),
                (
                    tuple(op.base_revision for op in operations),
                    tuple(range(len(operations))),
                ),
                (snapshot.turn_seq, snapshot.revision),
                (snapshot.child_runs, tuple(op.child_run_id for op in terminal)),
                (snapshot.active_operation, expected_active),
            )
        ):
            raise GoldenRunCorruptionError("workflow operation journal and head disagree")

        receipts: dict[UUID, tuple[ArtifactRef, WorkflowOperationReceiptV2]] = {}
        predecessor = snapshot.receipt_head
        for operation in reversed(terminal):
            receipt = self._resolve_canonical_model(
                predecessor,
                WorkflowOperationReceiptV2,
            )
            if receipt.operation_id != operation.operation_id:
                raise GoldenRunCorruptionError("workflow receipt chain is not operation ordered")
            receipts[operation.operation_id] = (predecessor, receipt)
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
        for operation in terminal:
            receipt_ref, receipt = receipts[operation.operation_id]
            prior = self._verify_terminal_evidence(
                prior,
                operation,
                receipt_ref=receipt_ref,
                receipt=receipt,
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
    claims: tuple[object, ...],
    *,
    receipt_ref: ArtifactRef,
    observed_at: datetime,
) -> tuple[StateMutation, ...]:
    current = {fact.key: fact for fact in state.facts}
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
            mutation = StateMutation(
                operation="upsert",
                key=key,
                expected_fact_sha256=expected,
                fact=WorldFact(
                    key=key,
                    value=claim.value,
                    source_kind="trusted_receipt",
                    source_artifact=receipt_ref,
                    observed_at=observed_at,
                    valid_until=None,
                ),
            )
        mutations.append(mutation)
    return tuple(sorted(mutations, key=lambda item: item.key))


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
]
