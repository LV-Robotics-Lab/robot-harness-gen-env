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
    create_golden_execution_catalog,
)
from .golden_store import (
    GoldenRunConflictError,
    GoldenRunCorruptionError,
    GoldenRunHeadConflictError,
    SQLiteGoldenRunStore,
)
from .qualification import QualificationReportV1
from .run_store import ControlledRunIntent
from .schema_catalog import schema_model
from .schemas.common import (
    ArtifactRef,
    Invocation,
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
        operation_id_factory: Callable[[], UUID] = uuid4,
        child_run_id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if type(artifact_store) is not LocalArtifactStore:
            raise TypeError("artifact_store must be the exact LocalArtifactStore")
        if type(workflow_store) is not SQLiteGoldenRunStore:
            raise TypeError("workflow_store must be the exact SQLiteGoldenRunStore")
        if execution_catalog is None:
            execution_catalog = create_golden_execution_catalog()
        if type(execution_catalog) is not GoldenExecutionCatalog:
            raise TypeError("execution_catalog must be the exact GoldenExecutionCatalog")
        if execution_catalog.artifact_roots - {artifact_store.root}:
            raise ValueError("qualified applications and GoldenRun must share one CAS root")
        self._artifact_store = artifact_store
        self._workflow_store = workflow_store
        self._execution_catalog = execution_catalog
        self._clock = clock
        self._workflow_id_factory = workflow_id_factory
        self._operation_id_factory = operation_id_factory
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
        if snapshot.revision == 0 and snapshot.active_operation is None:
            self._verify_start_snapshot(
                snapshot,
                request=request,
                request_sha256=request_sha256,
                persisted_idempotency_key=stored.idempotency_key,
                persisted_request_sha256=stored.request_sha256,
            )
        else:
            self._verify_committed_snapshot(
                snapshot,
                request=request,
                persisted_idempotency_key=stored.idempotency_key,
                persisted_request_sha256=stored.request_sha256,
            )
        return snapshot

    def read(self, request: RunReadRequest) -> RunSnapshot:
        """Recover an authorized revision-zero start and reverify its closure."""

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
        if snapshot.revision == 0 and snapshot.active_operation is None:
            self._verify_start_snapshot(
                snapshot,
                request=start_request,
                request_sha256=_request_sha256(start_request),
                persisted_idempotency_key=stored.idempotency_key,
                persisted_request_sha256=stored.request_sha256,
            )
        else:
            self._verify_committed_snapshot(
                snapshot,
                request=start_request,
                persisted_idempotency_key=stored.idempotency_key,
                persisted_request_sha256=stored.request_sha256,
            )
        return snapshot

    def submit(self, request: RunCommandRequest) -> OperationSnapshot:
        """Reserve, execute, and atomically commit one exact qualified Skill."""

        existing = self._workflow_store.read_operation(
            principal_id=request.principal_id,
            workflow_run_id=request.workflow_run_id,
            idempotency_key=request.idempotency_key,
        )
        if existing is not None:
            base_or_head = self.read(
                RunReadRequest(
                    schema_version="harness.run_read_request.v1",
                    principal_id=request.principal_id,
                    workflow_run_id=request.workflow_run_id,
                )
            )
            persisted_command = self._resolve_canonical_model(
                existing.operation.command,
                RunCommandRequest,
            )
            frozen_skill = self._resolve_operation_skill(
                existing.operation,
                registry_ref=base_or_head.registry_snapshot,
            )
            normalized_existing, typed_input = self._normalize_with_frozen_skill(
                request,
                frozen_skill,
            )
            if persisted_command != normalized_existing:
                raise GoldenRunConflictError(
                    "operation idempotency key is already bound to a different command"
                )
            if existing.operation.status is not RunStatus.RUNNING:
                return existing.operation
            return self._resume_operation(
                existing.operation,
                typed_input=typed_input,
                skill=frozen_skill,
            )
        normalized, typed_input, skill = self._normalize_command(request)
        command_payload = _canonical_model_bytes(normalized)
        command_sha256 = hashlib.sha256(command_payload).hexdigest()
        stored = self._workflow_store.reserve_operation(
            principal_id=normalized.principal_id,
            workflow_run_id=normalized.workflow_run_id,
            idempotency_key=normalized.idempotency_key,
            command_sha256=command_sha256,
            expected_revision=normalized.expected_revision,
            expected_state_sha256=normalized.expected_state_sha256,
            expected_receipt_sha256=normalized.expected_receipt_head.sha256,
            factory=lambda snapshot: self._create_running_operation(
                normalized,
                snapshot=snapshot,
                skill=skill,
            ),
        )
        if stored is None:
            raise GoldenRunNotFoundError("golden workflow was not found for this principal")
        operation = stored.operation
        if operation.status.value != "running":
            self.read(
                RunReadRequest(
                    schema_version="harness.run_read_request.v1",
                    principal_id=operation.principal_id,
                    workflow_run_id=operation.workflow_run_id,
                )
            )
            return operation
        authority = self._workflow_store.read_snapshot(
            principal_id=operation.principal_id,
            workflow_run_id=operation.workflow_run_id,
        )
        if authority is None:
            raise GoldenRunNotFoundError("golden workflow was not found for this principal")
        frozen_skill = self._resolve_operation_skill(
            operation,
            registry_ref=authority.snapshot.registry_snapshot,
        )
        if frozen_skill != skill:
            raise GoldenRunConflictError(
                "operation reservation returned a different qualified execution"
            )
        return self._resume_operation(
            operation,
            typed_input=typed_input,
            skill=frozen_skill,
        )

    def _resume_operation(
        self,
        operation: OperationSnapshot,
        *,
        typed_input: BaseModel,
        skill: QualifiedSkillExecutionV2,
    ) -> OperationSnapshot:
        invocation, state, intent = self._read_child_execution(operation.child_run_id)
        if state is not None and intent is None:
            raise GoldenRunCorruptionError("terminal child has no controlled intent")
        if intent is not None:
            self._verify_child_intent(intent, skill=skill, typed_input=typed_input)
        if invocation is not None and state is None:
            return operation
        if state is None:
            if self._execution_catalog.application_for(operation.skill_ref) is None:
                return operation
            stored = self._workflow_store.read_snapshot(
                principal_id=operation.principal_id,
                workflow_run_id=operation.workflow_run_id,
            )
            if stored is None:
                raise GoldenRunNotFoundError("golden workflow was not found for this principal")
            live_skill = self._intersect_live_skill(
                stored.snapshot.registry_snapshot,
                skill_ref=operation.skill_ref,
            )
            if live_skill != skill:
                raise GoldenRunRegistryError(
                    "HARN_DEPENDENCY_DRIFT",
                    "reserved execution no longer equals the live qualified application",
                )
            try:
                state = self._execution_catalog.invoke(
                    operation.skill_ref,
                    typed_input,
                    run_id=operation.child_run_id,
                )
            except QualifiedApplicationRunPendingError:
                return operation
            invocation, persisted_state, intent = self._read_child_execution(operation.child_run_id)
            if persisted_state != state:
                raise GoldenRunCorruptionError(
                    "application result does not equal the durable child authority"
                )
            if intent is None:
                raise GoldenRunCorruptionError("controlled child intent is unavailable")
            self._verify_child_intent(intent, skill=skill, typed_input=typed_input)
        terminal, snapshot = self._build_terminal_operation(
            operation,
            state=state,
            invocation=invocation,
            skill=skill,
        )
        committed = self._workflow_store.commit_operation(
            principal_id=operation.principal_id,
            running=operation,
            terminal=terminal,
            snapshot=snapshot,
        )
        if committed is None:
            raise GoldenRunNotFoundError("golden workflow was not found for this principal")
        verified = self.read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id=operation.principal_id,
                workflow_run_id=operation.workflow_run_id,
            )
        )
        if verified.receipt_head != committed.operation.receipt:
            raise GoldenRunCorruptionError("committed operation is not the workflow head")
        return committed.operation

    def _create_running_operation(
        self,
        request: RunCommandRequest,
        *,
        snapshot: RunSnapshot,
        skill: QualifiedSkillExecutionV2,
    ) -> OperationSnapshot:
        started_at = self._clock()
        if started_at.utcoffset() != timezone.utc.utcoffset(started_at):
            raise ValueError("clock must return a UTC datetime")
        command = self._publish_model(
            request,
            name="run_command_request.json",
            schema_version="harness.run_command_request.v1",
        )
        qualified_execution = self._publish_model(
            skill,
            name="qualified_skill_execution.json",
            schema_version="harness.qualified_skill_execution.v2",
        )
        return OperationSnapshot(
            schema_version="harness.operation_snapshot.v1",
            operation_id=self._operation_id_factory(),
            workflow_run_id=request.workflow_run_id,
            principal_id=request.principal_id,
            idempotency_key=request.idempotency_key,
            command=command,
            qualified_execution=qualified_execution,
            skill_ref=request.skill_ref,
            status="running",
            base_revision=snapshot.revision,
            committed_revision=None,
            child_run_id=self._child_run_id_factory(),
            tool_result=None,
            trusted_receipt=None,
            state_delta=None,
            resulting_state=None,
            resulting_state_sha256=None,
            receipt=None,
            started_at=started_at,
            ended_at=None,
        )

    def _normalize_command(
        self,
        request: RunCommandRequest,
    ) -> tuple[RunCommandRequest, BaseModel, QualifiedSkillExecutionV2]:
        if self._execution_catalog.application_for(request.skill_ref) is None:
            raise GoldenRunRegistryError(
                "HARN_SKILL_UNAVAILABLE",
                f"{request.skill_ref} requires an exact qualified application",
            )
        stored = self._workflow_store.read_snapshot(
            principal_id=request.principal_id,
            workflow_run_id=request.workflow_run_id,
        )
        if stored is None:
            raise GoldenRunNotFoundError("golden workflow was not found for this principal")
        skill = self._intersect_live_skill(
            stored.snapshot.registry_snapshot,
            skill_ref=request.skill_ref,
        )
        normalized, typed_input = self._normalize_with_frozen_skill(request, skill)
        return normalized, typed_input, skill

    def _normalize_with_frozen_skill(
        self,
        request: RunCommandRequest,
        skill: QualifiedSkillExecutionV2,
    ) -> tuple[RunCommandRequest, BaseModel]:
        policy = self._execution_catalog.policy_for(request.skill_ref)
        if policy is None or policy.skill_ref != skill.skill_ref:
            raise GoldenRunRegistryError(
                "HARN_SKILL_UNQUALIFIED",
                f"no exact evidence policy owns {request.skill_ref}",
            )
        try:
            input_model = schema_model(skill.input_schema)
            if input_model is not policy.input_model:
                raise TypeError("qualified descriptor and evidence policy disagree")
            normalized, typed_input = policy.normalize(request)
        except (KeyError, ValidationError, TypeError, ValueError) as error:
            raise GoldenRunRegistryError(
                "HARN_INPUT_SCHEMA_INVALID",
                f"command parameters failed the exact qualified input schema: {error}",
            ) from error
        return normalized, typed_input

    def _intersect_live_skill(
        self,
        registry_ref: ArtifactRef,
        *,
        skill_ref: str,
    ) -> QualifiedSkillExecutionV2:
        frozen = self._verify_registry_snapshot(registry_ref)
        entry = next(
            (
                candidate
                for candidate in frozen.qualified_skills
                if candidate.skill_ref == skill_ref
            ),
            None,
        )
        live = next(
            (
                descriptor
                for descriptor in self._execution_catalog.live_descriptors_for(skill_ref)
                if f"{descriptor.skill_id}@{descriptor.version}" == skill_ref
            ),
            None,
        )
        if entry is None or live is None:
            raise GoldenRunRegistryError(
                "HARN_SKILL_UNQUALIFIED",
                f"Skill is not present in both frozen and live qualified registries: {skill_ref}",
            )
        descriptor_model = (
            SkillDescriptor
            if entry.descriptor_ref.schema_version == "harness.skill_descriptor.v1"
            else SkillDescriptorV2
        )
        stored = self._resolve_canonical_model(entry.descriptor_ref, descriptor_model)
        assert isinstance(stored, (SkillDescriptor, SkillDescriptorV2))
        stored_shape = stored.model_dump(mode="json", exclude={"qualification_artifact"})
        live_shape = live.model_dump(mode="json", exclude={"qualification_artifact"})
        qualification_identities = {
            _artifact_content_identity(entry.qualification_ref),
            _artifact_content_identity(stored.qualification_artifact),
            _artifact_content_identity(live.qualification_artifact),
        }
        if stored_shape != live_shape or len(qualification_identities) != 1:
            raise GoldenRunRegistryError(
                "HARN_DEPENDENCY_DRIFT",
                f"frozen Skill does not equal the live production handler: {skill_ref}",
            )
        report = self._resolve_canonical_model(
            entry.qualification_report_ref,
            QualificationReportV1,
        )
        assert isinstance(report, QualificationReportV1)
        if report.implementation_sha256 != live.implementation_sha256:
            raise GoldenRunRegistryError(
                "HARN_DEPENDENCY_DRIFT",
                f"live implementation no longer matches its qualification report: {skill_ref}",
            )
        return QualifiedSkillExecutionV2(
            schema_version="harness.qualified_skill_execution.v2",
            skill_ref=skill_ref,
            descriptor=entry.descriptor_ref,
            input_schema=live.input_schema,
            output_schema=live.output_schema,
            implementation_sha256=live.implementation_sha256,
            qualification_artifact=entry.qualification_ref,
            qualification_report=entry.qualification_report_ref,
            max_attempts=live.max_attempts,
        )

    def _verify_historical_skill(
        self,
        registry_ref: ArtifactRef,
        skill: QualifiedSkillExecutionV2,
    ) -> QualifiedSkillExecutionV2:
        frozen = self._verify_registry_snapshot(registry_ref)
        entry = next(
            (
                candidate
                for candidate in frozen.qualified_skills
                if candidate.skill_ref == skill.skill_ref
            ),
            None,
        )
        if entry is None:
            raise GoldenRunCorruptionError(
                "trusted receipt Skill is absent from its frozen Registry snapshot"
            )
        descriptor_model = (
            SkillDescriptor
            if entry.descriptor_ref.schema_version == "harness.skill_descriptor.v1"
            else SkillDescriptorV2
        )
        descriptor = self._resolve_canonical_model(entry.descriptor_ref, descriptor_model)
        assert isinstance(descriptor, (SkillDescriptor, SkillDescriptorV2))
        if (
            skill.descriptor != entry.descriptor_ref
            or skill.qualification_artifact != entry.qualification_ref
            or skill.qualification_report != entry.qualification_report_ref
            or skill.input_schema != descriptor.input_schema
            or skill.output_schema != descriptor.output_schema
            or skill.implementation_sha256 != descriptor.implementation_sha256
            or skill.max_attempts != descriptor.max_attempts
            or descriptor.qualification_artifact.sha256 != entry.qualification_ref.sha256
        ):
            raise GoldenRunCorruptionError(
                "trusted receipt Skill does not equal its frozen qualification closure"
            )
        return skill

    def _resolve_operation_skill(
        self,
        operation: OperationSnapshot,
        *,
        registry_ref: ArtifactRef,
    ) -> QualifiedSkillExecutionV2:
        try:
            skill = self._resolve_canonical_model(
                operation.qualified_execution,
                QualifiedSkillExecutionV2,
            )
            assert isinstance(skill, QualifiedSkillExecutionV2)
        except (ArtifactResolutionError, ValidationError, TypeError, ValueError) as error:
            raise GoldenRunCorruptionError(
                f"operation qualified execution failed its CAS closure: {error}"
            ) from error
        if skill.skill_ref != operation.skill_ref:
            raise GoldenRunCorruptionError(
                "operation qualified execution does not match its Skill reference"
            )
        return self._verify_historical_skill(registry_ref, skill)

    def _read_child_execution(
        self,
        run_id: UUID,
    ) -> tuple[Invocation | None, RunState | None, ControlledRunIntent | None]:
        if self._child_run_store is None:
            return None, None, None
        intent = self._child_run_store.read_controlled_intent(run_id)
        invocation = self._child_run_store.read_invocation(run_id)
        state = self._child_run_store.read_run_state(run_id)
        return invocation, state, intent

    def _verify_child_intent(
        self,
        intent: ControlledRunIntent,
        *,
        skill: QualifiedSkillExecutionV2,
        typed_input: BaseModel,
    ) -> None:
        skill_id, version = skill.skill_ref.rsplit("@", 1)
        descriptor_model = (
            SkillDescriptor
            if skill.descriptor.schema_version == "harness.skill_descriptor.v1"
            else SkillDescriptorV2
        )
        descriptor = self._resolve_canonical_model(skill.descriptor, descriptor_model)
        descriptor_sha256 = hashlib.sha256(
            json.dumps(
                descriptor.model_dump(mode="json"),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        input_sha256 = hashlib.sha256(
            json.dumps(
                typed_input.model_dump(mode="json"),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if (
            intent.skill_id != skill_id
            or intent.skill_version != version
            or intent.input_schema != skill.input_schema
            or intent.descriptor_sha256 != descriptor_sha256
            or intent.implementation_sha256 != skill.implementation_sha256
            or intent.qualification_sha256 != skill.qualification_artifact.sha256
            or intent.max_attempts != skill.max_attempts
            or intent.phase != "validated_input"
            or intent.raw_parameters_sha256 != input_sha256
            or intent.effective_parameters_sha256 != input_sha256
        ):
            raise GoldenRunCorruptionError(
                "controlled child intent does not equal its frozen qualified execution"
            )

    def _build_terminal_operation(
        self,
        operation: OperationSnapshot,
        *,
        state: RunState,
        invocation: Invocation | None,
        skill: QualifiedSkillExecutionV2,
    ) -> tuple[OperationSnapshot, RunSnapshot]:
        stored = self._workflow_store.read_snapshot(
            principal_id=operation.principal_id,
            workflow_run_id=operation.workflow_run_id,
        )
        if stored is None:
            raise GoldenRunNotFoundError("golden workflow was not found for this principal")
        base_snapshot = stored.snapshot
        if (
            base_snapshot.active_operation != operation.operation_id
            or base_snapshot.revision != operation.base_revision
            or state.run_id != operation.child_run_id
            or state.ended_at is None
        ):
            raise GoldenRunHeadConflictError(
                "workflow or child-run identity changed before evidence packaging"
            )
        base_state = self._resolve_canonical_model(
            base_snapshot.state_ref,
            TrustedWorldState,
        )
        assert isinstance(base_state, TrustedWorldState)
        command = self._resolve_canonical_model(operation.command, RunCommandRequest)
        assert isinstance(command, RunCommandRequest)
        if command.expected_state_sha256 != base_state.state_sha256:
            raise GoldenRunHeadConflictError("command no longer matches the trusted base state")
        run_state_ref = self._publish_model(
            state,
            name="child_run_state.json",
            schema_version="harness.run_state.v1",
        )
        invocation_ref = (
            None
            if invocation is None
            else self._publish_model(
                invocation,
                name="child_invocation.json",
                schema_version="harness.skill_invocation.v1",
            )
        )
        policy = self._execution_catalog.policy_for(skill.skill_ref)
        if policy is None:
            raise GoldenRunCorruptionError(f"no exact evidence policy owns {skill.skill_ref}")
        try:
            claims = policy.claims(state)
        except (ValidationError, TypeError, ValueError) as error:
            raise GoldenRunCorruptionError(
                f"Skill output failed its exact evidence policy: {error}"
            ) from error
        supporting = tuple(
            sorted(
                state.artifacts,
                key=lambda ref: (ref.sha256, ref.schema_version or "", ref.media_type),
            )
        )
        tool_receipt = TrustedToolReceiptV2(
            schema_version="harness.trusted_tool_receipt.v2",
            workflow_run_id=operation.workflow_run_id,
            operation_id=operation.operation_id,
            principal_id=operation.principal_id,
            command=operation.command,
            base_revision=operation.base_revision,
            base_state_sha256=base_state.state_sha256,
            base_state=base_snapshot.state_ref,
            registry_snapshot=base_snapshot.registry_snapshot,
            skill=skill,
            status=state.status,
            started_at=state.started_at,
            ended_at=state.ended_at,
            child_run_id=state.run_id,
            invocation=invocation_ref,
            run_state=run_state_ref,
            invocation_digest=state.invocation_digest,
            typed_output=state.output,
            blocker=state.blocker,
            supporting_artifacts=supporting,
            derived_fact_claims=claims,
        )
        tool_receipt_ref = self._publish_model(
            tool_receipt,
            name="trusted_tool_receipt.json",
            schema_version="harness.trusted_tool_receipt.v2",
        )
        delta: StateDeltaV2 | None = None
        delta_ref: ArtifactRef | None = None
        resulting_state = base_state
        resulting_state_ref = base_snapshot.state_ref
        if state.status is RunStatus.SUCCEEDED:
            mutations = _mutations_from_claims(
                base_state,
                claims=claims,
                receipt=tool_receipt_ref,
                effective_at=state.ended_at,
            )
            unbound = StateDeltaV2.model_construct(
                schema_version="harness.state_delta.v2",
                base_state_sha256=base_state.state_sha256,
                effective_at=state.ended_at,
                receipt=tool_receipt_ref,
                mutations=mutations,
                delta_sha256="0" * 64,
            )
            delta = StateDeltaV2.model_validate(
                {
                    **unbound.model_dump(mode="python"),
                    "delta_sha256": state_delta_v2_sha256(unbound),
                }
            )
            resulting_state = _apply_state_delta_v2(base_state, delta)
            delta_ref = self._publish_model(
                delta,
                name="state_delta.json",
                schema_version="harness.state_delta.v2",
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
            started_at=state.started_at,
            ended_at=state.ended_at,
            child_run_id=state.run_id,
            invocation=invocation_ref,
            run_state=run_state_ref,
            typed_output=state.output,
            state_delta=delta,
            diagnostics=() if state.status is RunStatus.SUCCEEDED else supporting,
            trusted_receipt=tool_receipt_ref,
            fresh_observations=(),
            blocker=state.blocker,
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
            predecessor_receipt=command.expected_receipt_head,
            tool_receipt=tool_receipt_ref,
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
        terminal = operation.model_copy(
            update={
                "status": state.status,
                "committed_revision": operation.base_revision + 1,
                "tool_result": result_ref,
                "trusted_receipt": tool_receipt_ref,
                "state_delta": delta_ref,
                "resulting_state": resulting_state_ref,
                "resulting_state_sha256": resulting_state.state_sha256,
                "receipt": workflow_receipt_ref,
                "ended_at": state.ended_at,
            }
        )
        terminal = OperationSnapshot.model_validate(terminal.model_dump(mode="python"))
        snapshot = base_snapshot.model_copy(
            update={
                "revision": operation.base_revision + 1,
                "turn_seq": base_snapshot.turn_seq + 1,
                "state_sha256": resulting_state.state_sha256,
                "state_ref": resulting_state_ref,
                "receipt_head": workflow_receipt_ref,
                "active_operation": None,
                "child_runs": (*base_snapshot.child_runs, operation.child_run_id),
                "updated_at": state.ended_at,
            }
        )
        return terminal, RunSnapshot.model_validate(snapshot.model_dump(mode="python"))

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

    def _verify_terminal_operation(
        self,
        operation: OperationSnapshot,
        *,
        expected_registry: ArtifactRef,
        expected_base_state: ArtifactRef,
        expected_base_state_sha256: str,
        expected_predecessor_receipt: ArtifactRef,
        expected_revision: int,
    ) -> tuple[WorkflowOperationReceiptV2, TrustedWorldState]:
        if (
            operation.status is RunStatus.RUNNING
            or operation.tool_result is None
            or operation.trusted_receipt is None
            or operation.receipt is None
        ):
            raise GoldenRunCorruptionError("workflow operation has no terminal evidence")
        try:
            command = self._resolve_canonical_model(operation.command, RunCommandRequest)
            tool_receipt = self._resolve_canonical_model(
                operation.trusted_receipt,
                TrustedToolReceiptV2,
            )
            result = self._resolve_canonical_model(operation.tool_result, ToolResultV2)
            receipt = self._resolve_canonical_model(
                operation.receipt,
                WorkflowOperationReceiptV2,
            )
            assert isinstance(command, RunCommandRequest)
            assert isinstance(tool_receipt, TrustedToolReceiptV2)
            assert isinstance(result, ToolResultV2)
            assert isinstance(receipt, WorkflowOperationReceiptV2)
            run_state = self._resolve_canonical_model(tool_receipt.run_state, RunState)
            base_state = self._resolve_canonical_model(
                expected_base_state,
                TrustedWorldState,
            )
            resulting_state = self._resolve_canonical_model(
                receipt.resulting_state,
                TrustedWorldState,
            )
            assert isinstance(run_state, RunState)
            assert isinstance(base_state, TrustedWorldState)
            assert isinstance(resulting_state, TrustedWorldState)
            invocation = (
                None
                if tool_receipt.invocation is None
                else self._resolve_canonical_model(tool_receipt.invocation, Invocation)
            )
            if invocation is not None:
                assert isinstance(invocation, Invocation)
            for artifact in tool_receipt.supporting_artifacts:
                self._artifact_store.resolve(artifact)
            delta = (
                None
                if operation.state_delta is None
                else self._resolve_canonical_model(operation.state_delta, StateDeltaV2)
            )
            if delta is not None:
                assert isinstance(delta, StateDeltaV2)
        except (
            ArtifactResolutionError,
            ValidationError,
            TypeError,
            ValueError,
        ) as error:
            raise GoldenRunCorruptionError(
                f"workflow operation failed its CAS evidence closure: {error}"
            ) from error
        skill = self._resolve_operation_skill(
            operation,
            registry_ref=expected_registry,
        )
        supporting = tuple(
            sorted(
                run_state.artifacts,
                key=lambda ref: (ref.sha256, ref.schema_version or "", ref.media_type),
            )
        )
        if (
            command.workflow_run_id != operation.workflow_run_id
            or command.principal_id != operation.principal_id
            or command.idempotency_key != operation.idempotency_key
            or command.skill_ref != operation.skill_ref
            or command.expected_revision != expected_revision
            or command.expected_state_sha256 != expected_base_state_sha256
            or command.expected_receipt_head != expected_predecessor_receipt
            or tool_receipt.workflow_run_id != operation.workflow_run_id
            or tool_receipt.operation_id != operation.operation_id
            or tool_receipt.command != operation.command
            or tool_receipt.skill != skill
            or tool_receipt.registry_snapshot != expected_registry
            or tool_receipt.base_revision != expected_revision
            or tool_receipt.base_state_sha256 != expected_base_state_sha256
            or tool_receipt.base_state != expected_base_state
            or tool_receipt.child_run_id != operation.child_run_id
            or tool_receipt.status is not operation.status
            or tool_receipt.run_state != result.run_state
            or tool_receipt.started_at != run_state.started_at
            or tool_receipt.ended_at != run_state.ended_at
            or tool_receipt.invocation_digest != run_state.invocation_digest
            or tool_receipt.typed_output != run_state.output
            or tool_receipt.blocker != run_state.blocker
            or tool_receipt.supporting_artifacts != supporting
            or result.workflow_run_id != operation.workflow_run_id
            or result.operation_id != operation.operation_id
            or result.skill_ref != operation.skill_ref
            or result.status is not operation.status
            or result.child_run_id != operation.child_run_id
            or result.trusted_receipt != operation.trusted_receipt
            or result.started_at != run_state.started_at
            or result.ended_at != run_state.ended_at
            or result.invocation != tool_receipt.invocation
            or result.typed_output != run_state.output
            or result.blocker != run_state.blocker
            or receipt.workflow_run_id != operation.workflow_run_id
            or receipt.operation_id != operation.operation_id
            or receipt.principal_id != operation.principal_id
            or receipt.command != operation.command
            or receipt.tool_receipt != operation.trusted_receipt
            or receipt.tool_result != operation.tool_result
            or receipt.state_delta != operation.state_delta
            or receipt.status is not operation.status
            or receipt.base_revision != expected_revision
            or receipt.committed_revision != operation.committed_revision
            or receipt.predecessor_receipt != expected_predecessor_receipt
            or receipt.base_state_sha256 != expected_base_state_sha256
            or receipt.resulting_state_sha256 != resulting_state.state_sha256
            or receipt.resulting_state != operation.resulting_state
            or receipt.resulting_state_sha256 != operation.resulting_state_sha256
            or receipt.committed_at != operation.ended_at
            or run_state.run_id != operation.child_run_id
            or run_state.status is not operation.status
            or run_state.ended_at != operation.ended_at
            or base_state.state_sha256 != expected_base_state_sha256
        ):
            raise GoldenRunCorruptionError(
                "workflow operation evidence has inconsistent correlation fields"
            )
        if invocation is not None and (
            invocation.run_id != operation.child_run_id
            or invocation.effective_parameters != command.parameters
            or invocation.invocation_digest != run_state.invocation_digest
            or f"{invocation.skill_id}@{invocation.skill_version}" != operation.skill_ref
        ):
            raise GoldenRunCorruptionError(
                "workflow operation invocation does not match its canonical command"
            )
        policy = self._execution_catalog.policy_for(operation.skill_ref)
        if policy is None:
            raise GoldenRunCorruptionError(f"no exact evidence policy owns {operation.skill_ref}")
        try:
            policy.verify_result(
                state=run_state,
                claims=tool_receipt.derived_fact_claims,
                result=result,
                supporting=supporting,
            )
        except (ValidationError, TypeError, ValueError) as error:
            raise GoldenRunCorruptionError(
                f"workflow operation violates its exact evidence policy: {error}"
            ) from error
        if operation.status is RunStatus.SUCCEEDED:
            if invocation is None or delta is None or result.state_delta != delta:
                raise GoldenRunCorruptionError("successful operation evidence is incomplete")
            _verify_delta_claims(delta, tool_receipt, receipt_ref=operation.trusted_receipt)
            rebuilt = _apply_state_delta_v2(base_state, delta)
            if rebuilt != resulting_state:
                raise GoldenRunCorruptionError(
                    "workflow receipt resulting state does not equal its state delta"
                )
        elif (
            (invocation is None) != (run_state.invocation_digest is None)
            or delta is not None
            or resulting_state != base_state
        ):
            raise GoldenRunCorruptionError("blocked or failed operation advanced trusted state")
        return receipt, resulting_state

    def _verify_committed_snapshot(
        self,
        snapshot: RunSnapshot,
        *,
        request: RunStartRequest,
        persisted_idempotency_key: str,
        persisted_request_sha256: str,
    ) -> None:
        if (
            persisted_idempotency_key != request.idempotency_key
            or persisted_request_sha256 != _request_sha256(request)
        ):
            raise GoldenRunCorruptionError(
                "persisted workflow has inconsistent start authority metadata"
            )
        operations = self._workflow_store.read_operations(
            principal_id=snapshot.principal_id,
            workflow_run_id=snapshot.workflow_run_id,
        )
        if not operations:
            raise GoldenRunCorruptionError(
                "persisted workflow start failed its request receipt binding: "
                "advanced workflow has no operation journal"
            )
        try:
            first_command = self._resolve_canonical_model(
                operations[0].operation.command,
                RunCommandRequest,
            )
            assert isinstance(first_command, RunCommandRequest)
            start_receipt = self._resolve_canonical_model(
                first_command.expected_receipt_head,
                WorkflowStartReceipt,
            )
            assert isinstance(start_receipt, WorkflowStartReceipt)
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
                receipt_head=first_command.expected_receipt_head,
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
            expected_revision = 0
            expected_state_sha256 = initial.state_sha256
            expected_state_ref = initial.state_ref
            expected_receipt = initial.receipt_head
            child_runs: tuple[UUID, ...] = ()
            active_operation: UUID | None = None
            for index, stored in enumerate(operations):
                operation = stored.operation
                command = self._resolve_canonical_model(operation.command, RunCommandRequest)
                assert isinstance(command, RunCommandRequest)
                if (
                    command.workflow_run_id != operation.workflow_run_id
                    or command.principal_id != operation.principal_id
                    or command.idempotency_key != operation.idempotency_key
                    or command.skill_ref != operation.skill_ref
                    or operation.base_revision != expected_revision
                    or command.expected_revision != expected_revision
                    or command.expected_state_sha256 != expected_state_sha256
                    or command.expected_receipt_head != expected_receipt
                ):
                    raise GoldenRunCorruptionError(
                        "operation journal does not form one continuous CAS chain"
                    )
                if operation.status is RunStatus.RUNNING:
                    if index != len(operations) - 1:
                        raise GoldenRunCorruptionError(
                            "only the final workflow operation may be running"
                        )
                    active_operation = operation.operation_id
                    self._resolve_operation_skill(
                        operation,
                        registry_ref=snapshot.registry_snapshot,
                    )
                    continue
                receipt, resulting_state = self._verify_terminal_operation(
                    operation,
                    expected_registry=snapshot.registry_snapshot,
                    expected_base_state=expected_state_ref,
                    expected_base_state_sha256=expected_state_sha256,
                    expected_predecessor_receipt=expected_receipt,
                    expected_revision=expected_revision,
                )
                expected_revision += 1
                expected_state_sha256 = resulting_state.state_sha256
                expected_state_ref = receipt.resulting_state
                assert operation.receipt is not None
                expected_receipt = operation.receipt
                child_runs = (*child_runs, operation.child_run_id)
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
                f"advanced workflow failed its operation closure: {error}"
            ) from error
        if (
            snapshot.revision != expected_revision
            or snapshot.turn_seq != expected_revision
            or snapshot.state_sha256 != expected_state_sha256
            or snapshot.state_ref != expected_state_ref
            or snapshot.receipt_head != expected_receipt
            or snapshot.child_runs != child_runs
            or snapshot.active_operation != active_operation
            or snapshot.updated_at < snapshot.started_at
        ):
            raise GoldenRunCorruptionError(
                "persisted workflow head does not equal its operation journal"
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


def _artifact_content_identity(ref: ArtifactRef) -> tuple[str, int, str, str | None]:
    return ref.sha256, ref.bytes, ref.media_type, ref.schema_version


def _mutations_from_claims(
    state: TrustedWorldState,
    *,
    claims: tuple[ReceiptDerivedFactClaimV2, ...],
    receipt: ArtifactRef,
    effective_at: datetime,
) -> tuple[StateMutation, ...]:
    current = {fact.key: fact for fact in state.facts}
    return tuple(
        StateMutation(
            operation=claim.operation,
            key=claim.key,
            expected_fact_sha256=(
                fact_sha256(current[claim.key]) if claim.key in current else None
            ),
            fact=(
                WorldFact(
                    key=claim.key,
                    value=claim.value,
                    source_kind="trusted_receipt",
                    source_artifact=receipt,
                    observed_at=effective_at,
                    valid_until=None,
                )
                if claim.operation == "upsert"
                else None
            ),
        )
        for claim in claims
    )


def _verify_delta_claims(
    delta: StateDeltaV2,
    receipt: TrustedToolReceiptV2,
    *,
    receipt_ref: ArtifactRef,
) -> None:
    claims = {claim.key: claim for claim in receipt.derived_fact_claims}
    if set(claims) != {mutation.key for mutation in delta.mutations}:
        raise GoldenRunCorruptionError("state delta mutations do not equal trusted receipt claims")
    for mutation in delta.mutations:
        claim = claims[mutation.key]
        if claim.operation != mutation.operation:
            raise GoldenRunCorruptionError(
                "state delta operation does not equal trusted receipt claim"
            )
        if mutation.operation == "upsert":
            fact = mutation.fact
            if (
                fact is None
                or fact.source_kind != "trusted_receipt"
                or fact.source_artifact != receipt_ref
                or fact.observed_at != delta.effective_at
                or fact.value != claim.value
            ):
                raise GoldenRunCorruptionError(
                    "state delta fact is not justified by its trusted receipt"
                )


def _apply_state_delta_v2(
    state: TrustedWorldState,
    delta: StateDeltaV2,
) -> TrustedWorldState:
    if delta.base_state_sha256 != state.state_sha256:
        raise GoldenRunCorruptionError("state delta base does not match trusted state")
    try:
        return advance_trusted_world_state(
            state,
            effective_at=delta.effective_at,
            mutations=delta.mutations,
        )
    except ValueError as error:
        raise GoldenRunCorruptionError(f"state delta cannot be applied: {error}") from error


__all__ = [
    "GoldenRunConflictError",
    "GoldenRunCorruptionError",
    "GoldenRunHarness",
    "GoldenRunNotFoundError",
    "GoldenRunRegistryError",
]
