"""The in-process authority for versioned Skill registration and invocation."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, TypeAdapter, ValidationError

from .artifacts import ArtifactResolutionError, ArtifactResolver
from .events import EventSink, RunRecorder
from .run_store import (
    ControlledRunIntent,
    ControlledRunLifecycle,
    RunStore,
    RunStoreConflictError,
)
from .schema_catalog import schema_model
from .schemas import (
    ArtifactRef,
    Blocker,
    DependencyRef,
    ExecutionReproducibility,
    Invocation,
    RegisteredSkillDescriptor,
    RunState,
    RunStatus,
    SkillDescriptor,
    SkillDescriptorV2,
    SkillQualification,
)
from .schemas.base import (
    HarnessModel,
    JsonObject,
    PositiveInt,
    SchemaId,
    SkillId,
    StableSemVer,
)


@dataclass(frozen=True)
class HandlerResult:
    """A typed Skill output plus generic supporting artifacts."""

    output: HarnessModel | Mapping[str, Any]
    artifacts: tuple[ArtifactRef, ...] = ()


@dataclass(frozen=True)
class RunContext:
    """The immutable invocation facts and progress surface exposed to a handler."""

    run_id: UUID
    attempt: int
    dependencies: tuple[DependencyRef, ...]
    _recorder: RunRecorder
    _artifact_resolver: ArtifactResolver
    _stage_prefix: str = ""

    def emit(
        self,
        stage: str,
        *,
        artifact_refs: tuple[ArtifactRef, ...] = (),
    ) -> None:
        for artifact in artifact_refs:
            self._artifact_resolver.resolve(artifact)
        self._recorder.progress(
            stage=f"{self._stage_prefix}{stage}",
            artifact_refs=artifact_refs,
        )


SkillHandler = Callable[[HarnessModel, RunContext], HandlerResult]


@dataclass(frozen=True, slots=True)
class _EvidenceInvariantRegistration:
    """One policy result consumed immediately; never an authority token."""

    descriptor: SkillDescriptorV2
    handler: SkillHandler
    dependency_resolver: DependencyResolver
    qualification_bytes: bytes
    report_bytes: bytes


class EvidenceInvariantRegistrationPolicy(Protocol):
    """Configured authority that re-verifies raw evidence and builds live wiring."""

    def verify_and_build(
        self,
        request: object,
        *,
        artifact_resolver: ArtifactResolver,
    ) -> _EvidenceInvariantRegistration: ...


class QualificationCandidate(HarnessModel):
    """Executable Skill identity that deliberately has no qualification receipt."""

    skill_id: SkillId
    version: StableSemVer
    input_schema: SchemaId
    output_schema: SchemaId
    max_attempts: PositiveInt


@dataclass(frozen=True, slots=True)
class QualificationCandidateEvaluation:
    """Auditable production-kernel result for an unregistered candidate."""

    candidate: QualificationCandidate
    invocation: Invocation | None
    state: RunState
    mode: Literal["qualification_candidate"] = "qualification_candidate"


class DependencyResolver(Protocol):
    def resolve(
        self,
        skill_ref: str,
        effective_parameters: HarnessModel,
    ) -> tuple[DependencyRef, ...]: ...


@dataclass(frozen=True)
class StaticDependencyResolver:
    """Explicit immutable dependency records for embedded registries."""

    dependencies: Mapping[str, tuple[DependencyRef, ...]]

    def resolve(
        self,
        skill_ref: str,
        effective_parameters: HarnessModel,
    ) -> tuple[DependencyRef, ...]:
        return tuple(sorted(self.dependencies.get(skill_ref, ()), key=lambda item: item.name))


@dataclass(frozen=True)
class _Registration:
    descriptor: RegisteredSkillDescriptor | QualificationCandidate
    handler: SkillHandler
    input_model: type[HarnessModel]
    output_model: type[HarnessModel]


class RegistryRegistrationError(RuntimeError):
    """A descriptor or qualification cannot enter the immutable registry."""


class RegistryLookupError(KeyError):
    """An exact Skill identity is absent from the Registry."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class DependencyResolutionError(RuntimeError):
    """A declared runtime dependency is unavailable or has the wrong identity."""


class SkillBlocked(RuntimeError):
    """Expected typed domain refusal raised by a Skill handler."""

    def __init__(self, blocker: Blocker) -> None:
        self.blocker = blocker
        super().__init__(blocker.message)


class RunPersistenceError(RuntimeError):
    """A durable run record could not be committed at its required boundary."""

    def __init__(
        self,
        *,
        operation: str,
        run_id: UUID,
        state: RunState | None,
        cause: Exception,
    ) -> None:
        self.operation = operation
        self.run_id = run_id
        self.state = state
        self.cause = cause
        super().__init__(f"failed to persist {operation} for run {run_id}: {cause}")


class ControlledRunIdentityError(RuntimeError):
    """A caller-selected run identity is already bound to different immutable intent."""


class ControlledRunPendingError(RuntimeError):
    """A caller-selected run started but has no terminal record to recover."""


class SkillRegistry:
    """Own exact versions, validation, content digests, invocation, and audit state."""

    def __init__(
        self,
        *,
        artifact_resolver: ArtifactResolver,
        dependency_resolver: DependencyResolver | None,
        event_sink: EventSink,
        clock: Callable[[], datetime],
        run_id_factory: Callable[[], UUID],
        run_store: RunStore | None = None,
        evidence_invariant_policy: EvidenceInvariantRegistrationPolicy | None = None,
    ) -> None:
        self._artifact_resolver = artifact_resolver
        self._dependency_resolver = dependency_resolver
        self._event_sink = event_sink
        self._clock = clock
        self._run_id_factory = run_id_factory
        self._run_store = run_store
        self._evidence_invariant_policy = evidence_invariant_policy
        self._registrations: dict[tuple[str, str], _Registration] = {}
        self._invocations: dict[UUID, Invocation] = {}

    def register(self, descriptor: SkillDescriptor, handler: SkillHandler) -> None:
        if isinstance(descriptor, SkillDescriptorV2):
            raise RegistryRegistrationError(
                "v2 descriptors require a verified evidence-invariant registration"
            )
        if type(descriptor) is not SkillDescriptor:
            raise RegistryRegistrationError("ordinary registration requires a v1 descriptor")
        self._verify_descriptor_qualification(descriptor)
        self._install_registration(descriptor, handler)

    def register_evidence_invariant(self, request: object) -> SkillDescriptorV2:
        """Reverify through the configured policy and atomically install its exact wiring."""

        policy = self._evidence_invariant_policy
        if policy is None:
            raise RegistryRegistrationError("no evidence-invariant registration policy configured")
        try:
            registration = policy.verify_and_build(
                request,
                artifact_resolver=self._artifact_resolver,
            )
        except RegistryRegistrationError:
            raise
        except Exception as error:
            raise RegistryRegistrationError(
                f"evidence-invariant policy verification failed: {error}"
            ) from error
        if type(registration) is not _EvidenceInvariantRegistration:
            raise RegistryRegistrationError("registration policy returned an invalid result")
        descriptor = registration.descriptor
        if (
            type(descriptor) is not SkillDescriptorV2
            or descriptor.reproducibility
            is not ExecutionReproducibility.EVIDENCE_INVARIANT_REPEATABLE
            or not callable(registration.handler)
            or inspect.isfunction(registration.handler)
            or inspect.ismethod(registration.handler)
            or not callable(getattr(registration.dependency_resolver, "resolve", None))
        ):
            raise RegistryRegistrationError("invalid evidence-invariant registration semantics")
        self._verify_policy_qualification(
            descriptor,
            qualification_bytes=registration.qualification_bytes,
            report_bytes=registration.report_bytes,
        )
        resolver = self._dependency_resolver
        if resolver is not None and resolver is not registration.dependency_resolver:
            raise RegistryRegistrationError(
                "registration policy dependency resolver differs from the Registry resolver"
            )
        self._install_registration(descriptor, registration.handler)
        if resolver is None:
            self._dependency_resolver = registration.dependency_resolver
        return descriptor

    @staticmethod
    def _verify_policy_qualification(
        descriptor: SkillDescriptorV2,
        *,
        qualification_bytes: bytes,
        report_bytes: bytes,
    ) -> None:
        if type(qualification_bytes) is not bytes or type(report_bytes) is not bytes:
            raise RegistryRegistrationError("registration policy documents must be exact bytes")
        try:
            qualification = SkillQualification.model_validate_json(qualification_bytes)
        except ValidationError as error:
            raise RegistryRegistrationError(
                "registration policy qualification is invalid"
            ) from error
        skill_ref = f"{descriptor.skill_id}@{descriptor.version}"
        qualification_sha256 = hashlib.sha256(qualification_bytes).hexdigest()
        report_sha256 = hashlib.sha256(report_bytes).hexdigest()
        ref = descriptor.qualification_artifact
        if (
            qualification.skill_ref != skill_ref
            or qualification.report_sha256 != report_sha256
            or ref.uri != f"artifact://sha256/{qualification_sha256}"
            or ref.media_type != "application/json"
            or ref.sha256 != qualification_sha256
            or ref.bytes != len(qualification_bytes)
            or ref.schema_version != "harness.skill_qualification.v1"
        ):
            raise RegistryRegistrationError(
                "registration policy qualification does not bind descriptor and report bytes"
            )

    def _verify_descriptor_qualification(self, descriptor: RegisteredSkillDescriptor) -> None:
        skill_ref = f"{descriptor.skill_id}@{descriptor.version}"
        qualification_path = self._artifact_resolver.resolve(descriptor.qualification_artifact).path
        qualification = SkillQualification.model_validate_json(
            qualification_path.read_text(encoding="utf-8")
        )
        if qualification.skill_ref != skill_ref:
            raise RegistryRegistrationError(
                "qualification skill_ref does not match descriptor identity"
            )
        try:
            self._artifact_resolver.resolve_digest(qualification.report_sha256)
        except ArtifactResolutionError as error:
            raise RegistryRegistrationError(
                f"qualification report is unavailable or corrupt: {error}"
            ) from error

    def _install_registration(
        self,
        descriptor: RegisteredSkillDescriptor,
        handler: SkillHandler,
    ) -> None:
        skill_ref = f"{descriptor.skill_id}@{descriptor.version}"
        input_model = schema_model(descriptor.input_schema)
        output_model = schema_model(descriptor.output_schema)
        key = (descriptor.skill_id, descriptor.version)
        existing = self._registrations.get(key)
        if existing is not None:
            if existing.descriptor == descriptor and existing.handler is handler:
                return
            raise RegistryRegistrationError(f"immutable Skill identity already exists: {skill_ref}")
        self._registrations[key] = _Registration(
            descriptor=descriptor,
            handler=handler,
            input_model=input_model,
            output_model=output_model,
        )

    def list(self) -> tuple[RegisteredSkillDescriptor, ...]:
        return tuple(
            registration.descriptor for _, registration in sorted(self._registrations.items())
        )

    def resolve(self, skill_id: str, exact_version: str) -> RegisteredSkillDescriptor:
        return self._registration(skill_id, exact_version).descriptor

    def invocation(self, run_id: UUID) -> Invocation | None:
        invocation = self._invocations.get(run_id)
        if invocation is not None or self._run_store is None:
            return invocation
        return self._run_store.read_invocation(run_id)

    def evaluate_candidate(
        self,
        candidate: QualificationCandidate,
        handler: SkillHandler,
        parameters: Mapping[str, Any],
    ) -> QualificationCandidateEvaluation:
        """Execute without registration or a qualification receipt.

        The candidate path uses the same typed execution kernel as ``invoke``.
        Its only semantic difference is an explicit event-stage namespace and
        the immutable evaluation envelope returned to the qualification caller.
        """

        registration = _Registration(
            descriptor=candidate,
            handler=handler,
            input_model=schema_model(candidate.input_schema),
            output_model=schema_model(candidate.output_schema),
        )
        state = self._execute(
            registration,
            parameters,
            stage_prefix="qualification_candidate.",
        )
        return QualificationCandidateEvaluation(
            candidate=candidate,
            invocation=self.invocation(state.run_id),
            state=state,
        )

    def invoke(
        self,
        skill_id: str,
        exact_version: str,
        parameters: Mapping[str, Any],
    ) -> RunState:
        try:
            registration = self._registration(skill_id, exact_version)
        except RegistryLookupError as error:
            return self._preflight_terminal(
                skill_id=skill_id,
                skill_version=exact_version,
                status=RunStatus.BLOCKED,
                code=error.code,
                message=str(error.args[0]),
                details={},
            )
        return self._execute(registration, parameters, stage_prefix="")

    def invoke_with_run_id(
        self,
        skill_id: str,
        exact_version: str,
        parameters: Mapping[str, Any],
        *,
        run_id: UUID,
    ) -> RunState:
        """Execute once under a caller-reserved identity or recover its exact terminal state.

        An invocation without a terminal state is deliberately not repeated: the
        Registry cannot prove where the interrupted handler stopped producing
        external side effects.
        """

        registration = self._registration(skill_id, exact_version)
        return self._invoke_controlled(registration, parameters, run_id=run_id)

    def invoke_development_with_run_id(
        self,
        descriptor,
        handler,
        parameters,
        *,
        run_id,
        development_authority,
    ):
        """Execute a deployment-authorized unqualified handler through the durable kernel.

        The zero qualification digest is solely an internal SQLite compatibility sentinel.
        It is never an ArtifactRef, qualification document, or admissible release identity.
        """
        from .development_execution import DevelopmentExecutionAuthority, DevelopmentSkillDescriptor

        if (
            type(development_authority) is not DevelopmentExecutionAuthority
            or development_authority.store is not self._artifact_resolver
        ):
            raise TypeError("development execution requires the exact deployment authority")
        development_authority.verify()
        development_authority.verify_callable(handler)
        if type(descriptor) is not DevelopmentSkillDescriptor:
            raise TypeError("development execution requires an unqualified descriptor")
        if descriptor.implementation_sha256 != development_authority.implementation_sha256:
            raise ValueError("development implementation differs from declared source closure")
        registration = _Registration(
            descriptor=descriptor,
            handler=handler,
            input_model=schema_model(descriptor.input_schema),
            output_model=schema_model(descriptor.output_schema),
        )
        return self._invoke_controlled(registration, parameters, run_id=run_id, development=True)

    def _invoke_controlled(self, registration, parameters, *, run_id, development=False):
        if self._run_store is None:
            raise ControlledRunPendingError("controlled run identity requires a durable RunStore")
        normalized_parameters, raw_parameters_sha256 = _normalize_controlled_parameters(parameters)
        try:
            preview = registration.input_model.model_validate(normalized_parameters)
        except ValidationError as error:
            preview = None
            validation_error = error
        else:
            validation_error = None
        descriptor = registration.descriptor
        effective_parameters_sha256 = (
            None if preview is None else _canonical_json_sha256(preview.model_dump(mode="json"))
        )
        intent_fields = {
            "run_id": run_id,
            "skill_id": descriptor.skill_id,
            "skill_version": descriptor.version,
            "input_schema": descriptor.input_schema,
            "raw_parameters_sha256": raw_parameters_sha256,
            "descriptor_sha256": _canonical_json_sha256(descriptor.model_dump(mode="json")),
            "implementation_sha256": descriptor.implementation_sha256,
            "qualification_sha256": "0" * 64
            if development
            else descriptor.qualification_artifact.sha256,
            "max_attempts": descriptor.max_attempts,
        }
        existing_lifecycle = self._read_controlled_lifecycle(run_id)
        if existing_lifecycle.intent is None and (
            existing_lifecycle.invocation is not None or existing_lifecycle.terminal is not None
        ):
            raise ControlledRunIdentityError(
                f"controlled run identity is already occupied by an ordinary run: {run_id}"
            )
        existing_intent = existing_lifecycle.intent
        if preview is None:
            intent = ControlledRunIntent(
                **intent_fields,
                phase="input_validation",
                effective_parameters_sha256=None,
                dependencies=None,
            )
            if existing_intent is not None and existing_intent != intent:
                raise ControlledRunIdentityError(
                    f"controlled run identity is bound to another intent: {run_id}"
                )
            reserved = self._reserve_controlled_intent(intent)
            if not reserved:
                return self._recover_controlled_preflight(intent)
            assert validation_error is not None
            return self._preflight_terminal(
                skill_id=descriptor.skill_id,
                skill_version=descriptor.version,
                status=RunStatus.BLOCKED,
                code="HARN_INPUT_INVALID",
                message="Skill input failed strict schema validation",
                details={"errors": _json_safe(validation_error.errors())},
                controlled_intent=intent,
            )
        effective_parameters = preview.model_dump(mode="json")
        if existing_intent is not None and existing_intent.phase != "validated_input":
            wanted = ControlledRunIntent(
                **intent_fields,
                phase="preflight",
                effective_parameters_sha256=effective_parameters_sha256,
                dependencies=None,
            )
            if existing_intent != wanted:
                raise ControlledRunIdentityError(
                    f"controlled run identity is bound to another intent: {run_id}"
                )
            return self._recover_controlled_preflight(wanted)
        if existing_intent is not None:
            same_execution_intent = ControlledRunIntent(
                **intent_fields,
                phase="validated_input",
                effective_parameters_sha256=effective_parameters_sha256,
                dependencies=existing_intent.dependencies,
            )
            if existing_intent != same_execution_intent:
                raise ControlledRunIdentityError(
                    f"controlled run identity is bound to another intent: {run_id}"
                )

        artifact_refs = _artifact_refs(preview)
        try:
            for artifact in artifact_refs:
                self._artifact_resolver.resolve(artifact)
            dependencies = tuple(
                sorted(
                    self._dependency_resolver.resolve(
                        f"{descriptor.skill_id}@{descriptor.version}",
                        preview,
                    ),
                    key=lambda item: item.name,
                )
            )
        except ArtifactResolutionError as error:
            if existing_intent is not None:
                raise ControlledRunIdentityError(
                    "controlled run dependencies can no longer be resolved for its exact "
                    f"invocation: {run_id}"
                ) from error
            intent = ControlledRunIntent(
                **intent_fields,
                phase="preflight",
                effective_parameters_sha256=effective_parameters_sha256,
                dependencies=None,
            )
            reserved = self._reserve_controlled_intent(intent)
            if not reserved:
                return self._recover_controlled_preflight(intent)
            return self._preflight_terminal(
                skill_id=descriptor.skill_id,
                skill_version=descriptor.version,
                status=RunStatus.BLOCKED,
                code="HARN_DEPENDENCY_UNAVAILABLE",
                message=str(error),
                details={"reason": error.reason},
                artifact_refs=(artifact,),
                controlled_intent=intent,
            )
        except DependencyResolutionError as error:
            if existing_intent is not None:
                raise ControlledRunIdentityError(
                    "controlled run dependencies can no longer be resolved for its exact "
                    f"invocation: {run_id}"
                ) from error
            return self._controlled_preflight_terminal(
                intent_fields=intent_fields,
                effective_parameters_sha256=effective_parameters_sha256,
                status=RunStatus.BLOCKED,
                code="HARN_DEPENDENCY_UNAVAILABLE",
                message=str(error),
                details={},
            )
        except Exception as error:
            if existing_intent is not None:
                raise ControlledRunIdentityError(
                    "controlled run dependencies can no longer be resolved for its exact "
                    f"invocation: {run_id}"
                ) from error
            return self._controlled_preflight_terminal(
                intent_fields=intent_fields,
                effective_parameters_sha256=effective_parameters_sha256,
                status=RunStatus.FAILED,
                code="HARN_INTERNAL",
                message="dependency resolver failed unexpectedly",
                details={"error_type": type(error).__name__, "error": str(error)},
            )
        if existing_intent is not None:
            wanted = ControlledRunIntent(
                **intent_fields,
                phase="validated_input",
                effective_parameters_sha256=effective_parameters_sha256,
                dependencies=dependencies,
            )
            if existing_intent != wanted:
                raise ControlledRunIdentityError(
                    f"controlled run identity is bound to another invocation: {run_id}"
                )
            intent = existing_intent
            reserved = False
        else:
            intent = ControlledRunIntent(
                **intent_fields,
                phase="validated_input",
                effective_parameters_sha256=effective_parameters_sha256,
                dependencies=dependencies,
            )
            reserved = self._reserve_controlled_intent(intent)
        return self._execute(
            registration,
            effective_parameters,
            stage_prefix="development." if development else "",
            controlled_intent=intent,
            controlled_owner=reserved,
            prepared_input=(preview, dependencies),
        )

    def _execute(
        self,
        registration: _Registration,
        parameters: Mapping[str, Any],
        *,
        stage_prefix: str,
        controlled_intent: ControlledRunIntent | None = None,
        controlled_owner: bool = True,
        prepared_input: tuple[HarnessModel, tuple[DependencyRef, ...]] | None = None,
    ) -> RunState:
        descriptor = registration.descriptor
        skill_id = descriptor.skill_id
        exact_version = descriptor.version
        if prepared_input is None:
            try:
                typed_input = registration.input_model.model_validate(parameters)
            except ValidationError as error:
                return self._preflight_terminal(
                    skill_id=skill_id,
                    skill_version=exact_version,
                    status=RunStatus.BLOCKED,
                    code="HARN_INPUT_INVALID",
                    message="Skill input failed strict schema validation",
                    details={"errors": _json_safe(error.errors())},
                    stage_prefix=stage_prefix,
                    controlled_intent=controlled_intent,
                )
            effective_parameters = typed_input.model_dump(mode="json")
            for artifact in _artifact_refs(typed_input):
                try:
                    self._artifact_resolver.resolve(artifact)
                except ArtifactResolutionError as error:
                    return self._preflight_terminal(
                        skill_id=skill_id,
                        skill_version=exact_version,
                        status=RunStatus.BLOCKED,
                        code="HARN_DEPENDENCY_UNAVAILABLE",
                        message=str(error),
                        details={"reason": error.reason},
                        artifact_refs=(artifact,),
                        stage_prefix=stage_prefix,
                        controlled_intent=controlled_intent,
                    )
            skill_ref = f"{skill_id}@{exact_version}"
            try:
                dependencies = tuple(
                    sorted(
                        self._dependency_resolver.resolve(skill_ref, typed_input),
                        key=lambda item: item.name,
                    )
                )
            except DependencyResolutionError as error:
                return self._preflight_terminal(
                    skill_id=skill_id,
                    skill_version=exact_version,
                    status=RunStatus.BLOCKED,
                    code="HARN_DEPENDENCY_UNAVAILABLE",
                    message=str(error),
                    details={},
                    stage_prefix=stage_prefix,
                    controlled_intent=controlled_intent,
                )
            except Exception as error:
                return self._preflight_terminal(
                    skill_id=skill_id,
                    skill_version=exact_version,
                    status=RunStatus.FAILED,
                    code="HARN_INTERNAL",
                    message="dependency resolver failed unexpectedly",
                    details={
                        "error_type": type(error).__name__,
                        "error": str(error),
                    },
                    stage_prefix=stage_prefix,
                    controlled_intent=controlled_intent,
                )
        else:
            typed_input, dependencies = prepared_input
            effective_parameters = typed_input.model_dump(mode="json")
        invocation_digest = _invocation_digest(
            skill_id=skill_id,
            skill_version=exact_version,
            effective_parameters=typed_input,
            dependencies=dependencies,
            max_attempts=descriptor.max_attempts,
        )
        run_id = (
            controlled_intent.run_id if controlled_intent is not None else self._run_id_factory()
        )
        invocation = Invocation(
            run_id=run_id,
            skill_id=skill_id,
            skill_version=exact_version,
            effective_parameters=effective_parameters,
            dependencies=dependencies,
            max_attempts=descriptor.max_attempts,
            invocation_digest=invocation_digest,
        )
        if controlled_intent is not None:
            recovered = self._recover_controlled_run(invocation, controlled_intent)
            if recovered is not None:
                return recovered
            if not controlled_owner:
                raise ControlledRunPendingError(
                    "controlled run has no durable invocation yet and will not be repeated: "
                    f"{run_id}"
                )
        try:
            self._persist_invocation(invocation)
        except RunPersistenceError:
            if controlled_intent is None:
                raise
            recovered = self._recover_controlled_run(invocation, controlled_intent)
            if recovered is None:  # pragma: no cover - conflicting write guarantees a record
                raise
            return recovered
        self._invocations[run_id] = invocation
        recorder = RunRecorder(
            run_id=run_id,
            skill_id=skill_id,
            skill_version=exact_version,
            clock=self._clock,
            sink=self._event_sink,
        )
        recorder.start(stage=f"{stage_prefix}preflight", attempt=1)
        attempt = 1
        collected: tuple[ArtifactRef, ...] = ()
        while True:
            try:
                result = registration.handler(
                    typed_input,
                    RunContext(
                        run_id=run_id,
                        attempt=attempt,
                        dependencies=dependencies,
                        _recorder=recorder,
                        _artifact_resolver=self._artifact_resolver,
                        _stage_prefix=stage_prefix,
                    ),
                )
                typed_output = registration.output_model.model_validate(result.output)
                candidate_artifacts = (*collected, *_artifact_refs(typed_output), *result.artifacts)
                for artifact in candidate_artifacts:
                    self._artifact_resolver.resolve(artifact)
                artifacts = _unique_artifacts(candidate_artifacts)
                recorder.finish(
                    status=RunStatus.SUCCEEDED,
                    stage=f"{stage_prefix}complete",
                    artifact_refs=artifacts,
                )
                return self._persist_terminal(
                    recorder.build_state(
                        invocation_digest=invocation_digest,
                        max_attempts=descriptor.max_attempts,
                        artifacts=artifacts,
                        output=typed_output.model_dump(mode="json"),
                        blocker=None,
                    )
                )
            except SkillBlocked as error:
                blocker = error.blocker
                try:
                    for artifact in blocker.artifact_refs:
                        self._artifact_resolver.resolve(artifact)
                except Exception as verification_error:
                    return self._internal_failure(
                        recorder=recorder,
                        descriptor=descriptor,
                        invocation_digest=invocation_digest,
                        artifacts=collected,
                        error=verification_error,
                        stage_prefix=stage_prefix,
                    )
                collected = _unique_artifacts((*collected, *blocker.artifact_refs))
                if blocker.retryable and attempt < descriptor.max_attempts:
                    attempt += 1
                    recorder.retry(
                        stage=f"{stage_prefix}{blocker.stage}.retry",
                        artifact_refs=blocker.artifact_refs,
                    )
                    continue
                recorder.finish(
                    status=RunStatus.BLOCKED,
                    stage=f"{stage_prefix}{blocker.stage}",
                    artifact_refs=blocker.artifact_refs,
                )
                return self._persist_terminal(
                    recorder.build_state(
                        invocation_digest=invocation_digest,
                        max_attempts=descriptor.max_attempts,
                        artifacts=collected,
                        output=None,
                        blocker=blocker,
                    )
                )
            except RunPersistenceError:
                raise
            except Exception as error:
                return self._internal_failure(
                    recorder=recorder,
                    descriptor=descriptor,
                    invocation_digest=invocation_digest,
                    artifacts=collected,
                    error=error,
                    stage_prefix=stage_prefix,
                )

    def _reserve_controlled_intent(self, intent: ControlledRunIntent) -> bool:
        assert self._run_store is not None
        try:
            return self._run_store.reserve_controlled_intent(intent)
        except RunStoreConflictError as error:
            raise ControlledRunIdentityError(
                f"controlled run identity is bound to another intent: {intent.run_id}"
            ) from error
        except Exception as error:
            raise RunPersistenceError(
                operation="controlled run intent",
                run_id=intent.run_id,
                state=None,
                cause=error,
            ) from error

    def _controlled_preflight_terminal(
        self,
        *,
        intent_fields: Mapping[str, Any],
        effective_parameters_sha256: str,
        status: RunStatus,
        code: str,
        message: str,
        details: JsonObject,
    ) -> RunState:
        intent = ControlledRunIntent(
            **intent_fields,
            phase="preflight",
            effective_parameters_sha256=effective_parameters_sha256,
            dependencies=None,
        )
        if not self._reserve_controlled_intent(intent):
            return self._recover_controlled_preflight(intent)
        return self._preflight_terminal(
            skill_id=intent.skill_id,
            skill_version=intent.skill_version,
            status=status,
            code=code,
            message=message,
            details=details,
            controlled_intent=intent,
        )

    def _recover_controlled_run(
        self,
        wanted: Invocation,
        intent: ControlledRunIntent,
    ) -> RunState | None:
        assert self._run_store is not None
        lifecycle = self._read_controlled_lifecycle(wanted.run_id)
        existing_intent = lifecycle.intent
        existing = lifecycle.invocation
        terminal = lifecycle.terminal
        if existing_intent != intent:
            raise ControlledRunIdentityError(
                f"controlled run identity is bound to another intent: {wanted.run_id}"
            )
        if existing is None and terminal is None:
            return None
        if existing != wanted:
            raise ControlledRunIdentityError(
                f"controlled run identity is bound to another invocation: {wanted.run_id}"
            )
        if terminal is None:
            raise ControlledRunPendingError(
                f"controlled run has no terminal state and will not be repeated: {wanted.run_id}"
            )
        if (
            terminal.run_id != existing.run_id
            or terminal.skill_id != existing.skill_id
            or terminal.skill_version != existing.skill_version
            or terminal.invocation_digest != existing.invocation_digest
        ):
            raise ControlledRunIdentityError(
                f"controlled run terminal is not bound to its invocation: {wanted.run_id}"
            )
        return terminal

    def _recover_controlled_preflight(self, wanted: ControlledRunIntent) -> RunState:
        assert self._run_store is not None
        lifecycle = self._read_controlled_lifecycle(wanted.run_id)
        existing = lifecycle.intent
        invocation = lifecycle.invocation
        terminal = lifecycle.terminal
        if existing != wanted or invocation is not None:
            raise ControlledRunIdentityError(
                f"controlled run identity is bound to another preflight intent: {wanted.run_id}"
            )
        if terminal is None:
            raise ControlledRunPendingError(
                "controlled preflight has no terminal state and will not be repeated: "
                f"{wanted.run_id}"
            )
        if (
            wanted.phase not in {"input_validation", "preflight"}
            or terminal.run_id != wanted.run_id
            or terminal.skill_id != wanted.skill_id
            or terminal.skill_version != wanted.skill_version
            or terminal.invocation_digest is not None
            or terminal.status not in {RunStatus.BLOCKED, RunStatus.FAILED}
            or terminal.blocker is None
            or terminal.blocker.stage != "preflight"
            or terminal.blocker.retryable
            or terminal.blocker.unknowns
            or terminal.output is not None
            or terminal.attempt != 0
            or terminal.max_attempts != 0
            or terminal.ended_at is None
            or len(terminal.events) != 2
            or terminal.events[0].seq != 1
            or terminal.events[0].timestamp != terminal.started_at
            or terminal.events[0].attempt != 0
            or terminal.events[0].from_status is not None
            or terminal.events[0].to_status is not RunStatus.RUNNING
            or not terminal.events[0].stage.endswith("preflight")
            or terminal.events[0].artifact_refs
            or terminal.events[1].seq != 2
            or terminal.events[1].timestamp != terminal.ended_at
            or terminal.events[1].attempt != 0
            or terminal.events[1].from_status is not RunStatus.RUNNING
            or terminal.events[1].to_status is not terminal.status
            or not terminal.events[1].stage.endswith("preflight")
            or terminal.events[1].artifact_refs != terminal.artifacts
            or terminal.artifacts != terminal.blocker.artifact_refs
        ):
            raise ControlledRunIdentityError(
                f"controlled run terminal is not bound to its preflight intent: {wanted.run_id}"
            )
        return terminal

    def _internal_failure(
        self,
        *,
        recorder: RunRecorder,
        descriptor: RegisteredSkillDescriptor | QualificationCandidate,
        invocation_digest: str,
        artifacts: tuple[ArtifactRef, ...],
        error: Exception,
        stage_prefix: str,
    ) -> RunState:
        blocker = Blocker(
            code="HARN_INTERNAL",
            message="Skill implementation failed unexpectedly",
            stage="invoke",
            retryable=False,
            details={
                "error_type": type(error).__name__,
                "error": str(error),
            },
            unknowns=(),
            artifact_refs=artifacts,
        )
        recorder.finish(
            status=RunStatus.FAILED,
            stage=f"{stage_prefix}invoke",
            artifact_refs=artifacts,
        )
        return self._persist_terminal(
            recorder.build_state(
                invocation_digest=invocation_digest,
                max_attempts=descriptor.max_attempts,
                artifacts=artifacts,
                output=None,
                blocker=blocker,
            )
        )

    def _registration(self, skill_id: str, exact_version: str) -> _Registration:
        registration = self._registrations.get((skill_id, exact_version))
        if registration is not None:
            return registration
        if any(registered_id == skill_id for registered_id, _ in self._registrations):
            raise RegistryLookupError(
                "HARN_VERSION_UNSUPPORTED",
                f"unsupported exact Skill version: {skill_id}@{exact_version}",
            )
        raise RegistryLookupError("HARN_SKILL_NOT_FOUND", f"Skill not found: {skill_id}")

    def _preflight_terminal(
        self,
        *,
        skill_id: str,
        skill_version: str,
        status: RunStatus,
        code: str,
        message: str,
        details: JsonObject,
        artifact_refs: tuple[ArtifactRef, ...] = (),
        stage_prefix: str = "",
        controlled_intent: ControlledRunIntent | None = None,
    ) -> RunState:
        blocker = Blocker(
            code=code,
            message=message,
            stage="preflight",
            retryable=False,
            details=details,
            unknowns=(),
            artifact_refs=artifact_refs,
        )
        run_id = (
            controlled_intent.run_id if controlled_intent is not None else self._run_id_factory()
        )
        if controlled_intent is not None:
            assert self._run_store is not None
            lifecycle = self._read_controlled_lifecycle(run_id)
            if (
                lifecycle.intent != controlled_intent
                or lifecycle.invocation is not None
                or lifecycle.terminal is not None
            ):
                raise ControlledRunIdentityError(
                    f"controlled run identity is bound to another intent: {run_id}"
                )
        recorder = RunRecorder(
            run_id=run_id,
            skill_id=skill_id,
            skill_version=skill_version,
            clock=self._clock,
            sink=self._event_sink,
        )
        recorder.start(stage=f"{stage_prefix}preflight", attempt=0)
        recorder.finish(
            status=status,
            stage=f"{stage_prefix}preflight",
            artifact_refs=artifact_refs,
        )
        return self._persist_terminal(
            recorder.build_state(
                invocation_digest=None,
                max_attempts=0,
                artifacts=artifact_refs,
                output=None,
                blocker=blocker,
            )
        )

    def _persist_invocation(self, invocation: Invocation) -> None:
        if self._run_store is None:
            return
        try:
            self._run_store.put_invocation(invocation)
        except Exception as error:
            raise RunPersistenceError(
                operation="invocation",
                run_id=invocation.run_id,
                state=None,
                cause=error,
            ) from error

    def _read_controlled_lifecycle(self, run_id: UUID) -> ControlledRunLifecycle:
        assert self._run_store is not None
        try:
            return self._run_store.read_controlled_lifecycle(run_id)
        except Exception as error:
            raise RunPersistenceError(
                operation="controlled run lifecycle",
                run_id=run_id,
                state=None,
                cause=error,
            ) from error

    def _persist_terminal(self, state: RunState) -> RunState:
        if self._run_store is None:
            return state
        try:
            self._run_store.put_run_state(state)
        except Exception as error:
            raise RunPersistenceError(
                operation="terminal state",
                run_id=state.run_id,
                state=state,
                cause=error,
            ) from error
        return state


def _invocation_digest(
    *,
    skill_id: str,
    skill_version: str,
    effective_parameters: HarnessModel,
    dependencies: tuple[DependencyRef, ...],
    max_attempts: int,
) -> str:
    payload = {
        "skill_id": skill_id,
        "skill_version": skill_version,
        "effective_parameters": _content_identity(effective_parameters),
        "dependencies": [item.model_dump(mode="json") for item in dependencies],
        "max_attempts": max_attempts,
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_controlled_parameters(value: Mapping[str, Any]) -> tuple[JsonObject, str]:
    try:
        snapshot = dict(value)
        validated = TypeAdapter(JsonObject).validate_python(snapshot, strict=True)
        canonical = json.dumps(
            validated,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        normalized = json.loads(canonical)
        assert isinstance(normalized, dict)
        return normalized, hashlib.sha256(canonical).hexdigest()
    except (TypeError, ValueError, ValidationError) as error:
        raise ControlledRunIdentityError(
            "controlled run parameters must be a finite canonical JSON object"
        ) from error


def _canonical_json_sha256(value: JsonObject) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _content_identity(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, ArtifactRef):
        return {
            "media_type": value.media_type,
            "schema_version": value.schema_version,
            "sha256": value.sha256,
        }
    if isinstance(value, BaseModel):
        return {
            name: _content_identity(getattr(value, name)) for name in value.__class__.model_fields
        }
    if isinstance(value, Mapping):
        return {str(key): _content_identity(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_content_identity(item) for item in value]
    return value


def _artifact_refs(value: Any) -> tuple[ArtifactRef, ...]:
    found: list[ArtifactRef] = []

    def visit(item: Any) -> None:
        if isinstance(item, ArtifactRef):
            found.append(item)
        elif isinstance(item, BaseModel):
            for name in item.__class__.model_fields:
                visit(getattr(item, name))
        elif isinstance(item, Mapping):
            for nested in item.values():
                visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested)

    visit(value)
    return tuple(found)


def _unique_artifacts(artifacts: tuple[ArtifactRef, ...]) -> tuple[ArtifactRef, ...]:
    unique: list[ArtifactRef] = []
    identities: set[tuple[str, str | None, str]] = set()
    for artifact in artifacts:
        identity = (artifact.media_type, artifact.schema_version, artifact.sha256)
        if identity not in identities:
            identities.add(identity)
            unique.append(artifact)
    return tuple(unique)


def _json_safe(value: Any) -> JsonObject | list[Any] | str | int | float | bool | None:
    """Project pydantic diagnostics into the JSON-only Harness contract."""

    return json.loads(json.dumps(value, default=str))
