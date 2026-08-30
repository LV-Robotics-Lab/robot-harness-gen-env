"""The in-process authority for versioned Skill registration and invocation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ValidationError

from .artifacts import ArtifactResolutionError, ArtifactResolver
from .events import EventSink, RunRecorder
from .schema_catalog import schema_model
from .schemas import (
    ArtifactRef,
    Blocker,
    DependencyRef,
    Invocation,
    RunState,
    RunStatus,
    SkillDescriptor,
    SkillQualification,
)
from .schemas.base import HarnessModel, JsonObject


@dataclass(frozen=True)
class HandlerResult:
    """A typed Skill output plus generic supporting artifacts."""

    output: HarnessModel | Mapping[str, Any]
    artifacts: tuple[ArtifactRef, ...] = ()


@dataclass(frozen=True)
class RunContext:
    """The only progress callback surface exposed to a Skill handler."""

    run_id: UUID
    attempt: int
    _recorder: RunRecorder

    def emit(
        self,
        stage: str,
        *,
        artifact_refs: tuple[ArtifactRef, ...] = (),
    ) -> None:
        self._recorder.progress(stage=stage, artifact_refs=artifact_refs)


SkillHandler = Callable[[HarnessModel, RunContext], HandlerResult]


class DependencyResolver(Protocol):
    def resolve(self, skill_ref: str) -> tuple[DependencyRef, ...]: ...


@dataclass(frozen=True)
class StaticDependencyResolver:
    """Explicit immutable dependency records for embedded registries."""

    dependencies: Mapping[str, tuple[DependencyRef, ...]]

    def resolve(self, skill_ref: str) -> tuple[DependencyRef, ...]:
        return tuple(sorted(self.dependencies.get(skill_ref, ()), key=lambda item: item.name))


@dataclass(frozen=True)
class _Registration:
    descriptor: SkillDescriptor
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


class SkillRegistry:
    """Own exact versions, validation, content digests, invocation, and audit state."""

    def __init__(
        self,
        *,
        artifact_resolver: ArtifactResolver,
        dependency_resolver: DependencyResolver,
        event_sink: EventSink,
        clock: Callable[[], datetime],
        run_id_factory: Callable[[], UUID],
    ) -> None:
        self._artifact_resolver = artifact_resolver
        self._dependency_resolver = dependency_resolver
        self._event_sink = event_sink
        self._clock = clock
        self._run_id_factory = run_id_factory
        self._registrations: dict[tuple[str, str], _Registration] = {}
        self._invocations: dict[UUID, Invocation] = {}

    def register(self, descriptor: SkillDescriptor, handler: SkillHandler) -> None:
        skill_ref = f"{descriptor.skill_id}@{descriptor.version}"
        qualification_path = self._artifact_resolver.resolve(
            descriptor.qualification_artifact
        ).path
        qualification = SkillQualification.model_validate_json(
            qualification_path.read_text(encoding="utf-8")
        )
        if qualification.skill_ref != skill_ref:
            raise RegistryRegistrationError(
                "qualification skill_ref does not match descriptor identity"
            )
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

    def list(self) -> tuple[SkillDescriptor, ...]:
        return tuple(
            registration.descriptor
            for _, registration in sorted(self._registrations.items())
        )

    def resolve(self, skill_id: str, exact_version: str) -> SkillDescriptor:
        return self._registration(skill_id, exact_version).descriptor

    def invocation(self, run_id: UUID) -> Invocation | None:
        return self._invocations.get(run_id)

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
        descriptor = registration.descriptor
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
            )
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
                )
        skill_ref = f"{skill_id}@{exact_version}"
        try:
            dependencies = tuple(
                sorted(
                    self._dependency_resolver.resolve(skill_ref),
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
            )
        effective_parameters = typed_input.model_dump(mode="json")
        invocation_digest = _invocation_digest(
            skill_id=skill_id,
            skill_version=exact_version,
            effective_parameters=typed_input,
            dependencies=dependencies,
            max_attempts=descriptor.max_attempts,
        )
        run_id = self._run_id_factory()
        invocation = Invocation(
            run_id=run_id,
            skill_id=skill_id,
            skill_version=exact_version,
            effective_parameters=effective_parameters,
            dependencies=dependencies,
            max_attempts=descriptor.max_attempts,
            invocation_digest=invocation_digest,
        )
        self._invocations[run_id] = invocation
        recorder = RunRecorder(
            run_id=run_id,
            skill_id=skill_id,
            skill_version=exact_version,
            clock=self._clock,
            sink=self._event_sink,
        )
        recorder.start(stage="preflight", attempt=1)
        attempt = 1
        collected: tuple[ArtifactRef, ...] = ()
        while True:
            try:
                result = registration.handler(
                    typed_input,
                    RunContext(run_id=run_id, attempt=attempt, _recorder=recorder),
                )
                typed_output = registration.output_model.model_validate(result.output)
                artifacts = _unique_artifacts(
                    (*collected, *_artifact_refs(typed_output), *result.artifacts)
                )
                for artifact in artifacts:
                    self._artifact_resolver.resolve(artifact)
                recorder.finish(
                    status=RunStatus.SUCCEEDED,
                    stage="complete",
                    artifact_refs=artifacts,
                )
                return recorder.build_state(
                    invocation_digest=invocation_digest,
                    max_attempts=descriptor.max_attempts,
                    artifacts=artifacts,
                    output=typed_output.model_dump(mode="json"),
                    blocker=None,
                )
            except SkillBlocked as error:
                blocker = error.blocker
                collected = _unique_artifacts((*collected, *blocker.artifact_refs))
                if blocker.retryable and attempt < descriptor.max_attempts:
                    attempt += 1
                    recorder.retry(
                        stage=f"{blocker.stage}.retry",
                        artifact_refs=blocker.artifact_refs,
                    )
                    continue
                recorder.finish(
                    status=RunStatus.BLOCKED,
                    stage=blocker.stage,
                    artifact_refs=blocker.artifact_refs,
                )
                return recorder.build_state(
                    invocation_digest=invocation_digest,
                    max_attempts=descriptor.max_attempts,
                    artifacts=collected,
                    output=None,
                    blocker=blocker,
                )
            except Exception as error:
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
                    artifact_refs=collected,
                )
                recorder.finish(
                    status=RunStatus.FAILED,
                    stage="invoke",
                    artifact_refs=collected,
                )
                return recorder.build_state(
                    invocation_digest=invocation_digest,
                    max_attempts=descriptor.max_attempts,
                    artifacts=collected,
                    output=None,
                    blocker=blocker,
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
    ) -> RunState:
        run_id = self._run_id_factory()
        recorder = RunRecorder(
            run_id=run_id,
            skill_id=skill_id,
            skill_version=skill_version,
            clock=self._clock,
            sink=self._event_sink,
        )
        recorder.start(stage="preflight", attempt=0)
        blocker = Blocker(
            code=code,
            message=message,
            stage="preflight",
            retryable=False,
            details=details,
            unknowns=(),
            artifact_refs=artifact_refs,
        )
        recorder.finish(
            status=status,
            stage="preflight",
            artifact_refs=artifact_refs,
        )
        return recorder.build_state(
            invocation_digest=None,
            max_attempts=0,
            artifacts=artifact_refs,
            output=None,
            blocker=blocker,
        )


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


def _content_identity(value: Any) -> Any:
    if isinstance(value, ArtifactRef):
        return {
            "media_type": value.media_type,
            "schema_version": value.schema_version,
            "sha256": value.sha256,
        }
    if isinstance(value, BaseModel):
        return {
            name: _content_identity(getattr(value, name))
            for name in value.__class__.model_fields
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
