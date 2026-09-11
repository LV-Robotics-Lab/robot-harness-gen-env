"""Durable, explicitly controlled application for image2env compile candidates.

This module does not install a production qualification or expose an MCP tool.  It
first runs the exact handler as an unregistered candidate.  A caller may then bind
an explicit, CAS-backed controlled qualification before using caller-owned UUIDs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from .artifacts import ArtifactResolutionError, LocalArtifactStore
from .event_journal import SQLiteEventJournal
from .image2env_compile import Image2EnvExactReuseCompileHandler
from .qualification import QualificationReportV1
from .registry import (
    ControlledRunIdentityError,
    ControlledRunPendingError,
    QualificationCandidate,
    QualificationCandidateEvaluation,
    SkillRegistry,
    StaticDependencyResolver,
)
from .run_store import SQLiteRunStore
from .schemas.common import (
    ArtifactRef,
    DependencyRef,
    Invocation,
    RegisteredSkillDescriptor,
    RunState,
    RunStatus,
    SkillDescriptor,
    SkillQualification,
)
from .schemas.x2env import Image2EnvCompileV2Input, Image2EnvCompileV2Output

_SKILL_REF = "image2env.compile@1.0.0"
_INPUT_SCHEMA = "harness.image2env_compile_input.v2"
_OUTPUT_SCHEMA = "harness.image2env_compile_output.v2"
_MCP_TOOL_NAME = "image2env_compile_v1_0_0"
_CONTROLLED_CASE_ID = "controlled-image-exact-reuse-candidate"


@dataclass(frozen=True, slots=True)
class ControlledImage2EnvCompileApplicationSettings:
    """Durable child state and shared evidence CAS roots."""

    state_root: Path
    evidence_artifact_root: Path


class ControlledImage2EnvCompileApplicationRunIdentityError(RuntimeError):
    """A caller-selected child UUID conflicts with durable application state."""


class ControlledImage2EnvCompileApplicationRunPendingError(RuntimeError):
    """A durable child reservation has no safely recoverable terminal state."""


class ControlledImage2EnvCompileApplication:
    """Unqualified candidate facade with an explicit controlled binding gate."""

    durable_run_state = True

    def __init__(
        self,
        *,
        artifact_store: LocalArtifactStore,
        registry: SkillRegistry,
        run_store: SQLiteRunStore,
        handler: Image2EnvExactReuseCompileHandler,
    ) -> None:
        self._artifact_store = artifact_store
        self._registry = registry
        self._run_store = run_store
        self._handler = handler
        self._candidate = QualificationCandidate(
            skill_id="image2env.compile",
            version="1.0.0",
            input_schema=_INPUT_SCHEMA,
            output_schema=_OUTPUT_SCHEMA,
            max_attempts=1,
        )

    @property
    def skills(self) -> tuple[RegisteredSkillDescriptor, ...]:
        return self._registry.list()

    @property
    def artifact_root(self) -> Path:
        return self._artifact_store.root

    @property
    def authority_path(self) -> Path:
        return self._run_store.path.resolve()

    def evaluate(
        self,
        parameters: Image2EnvCompileV2Input,
    ) -> QualificationCandidateEvaluation:
        """Run one unregistered candidate through the shared execution kernel."""

        if self.skills:
            raise RuntimeError("a controlled-qualified application is not a candidate evaluator")
        if type(parameters) is not Image2EnvCompileV2Input:
            raise TypeError("controlled image compile requires exact Image2EnvCompileV2Input")
        trusted = Image2EnvCompileV2Input.model_validate(parameters.model_dump(mode="python"))
        return self._registry.evaluate_candidate(
            self._candidate,
            self._handler,
            trusted.model_dump(mode="json"),
        )

    def bind_controlled_qualification(self, descriptor: SkillDescriptor) -> None:
        """Verify and bind one controlled qualification; never called by production assembly."""

        if type(descriptor) is not SkillDescriptor:
            raise TypeError("controlled qualification must be an exact SkillDescriptor")
        self._verify_controlled_qualification(descriptor)
        current = self.skills
        if current:
            if current != (descriptor,):
                raise ValueError(
                    "controlled image compile is already bound to another qualification"
                )
            return
        self._registry.register(descriptor, self._handler)

    def invoke_typed(self, parameters: Image2EnvCompileV2Input, *, run_id: UUID) -> RunState:
        """Execute once under a caller-owned UUID or recover the exact terminal state."""

        if type(parameters) is not Image2EnvCompileV2Input:
            raise TypeError("controlled image compile requires exact Image2EnvCompileV2Input")
        if type(run_id) is not UUID or run_id.version != 4:
            raise ControlledImage2EnvCompileApplicationRunIdentityError(
                "controlled image compile run_id must be a UUID4"
            )
        if len(self.skills) != 1:
            raise RuntimeError("controlled image compile has no bound qualification")
        try:
            return self._registry.invoke_with_run_id(
                "image2env.compile",
                "1.0.0",
                parameters.model_dump(mode="json"),
                run_id=run_id,
            )
        except ControlledRunIdentityError as error:
            raise ControlledImage2EnvCompileApplicationRunIdentityError(str(error)) from error
        except ControlledRunPendingError as error:
            raise ControlledImage2EnvCompileApplicationRunPendingError(str(error)) from error

    def invocation(self, run_id: UUID) -> Invocation | None:
        return self._run_store.read_invocation(run_id)

    def run_state(self, run_id: UUID) -> RunState | None:
        return self._run_store.read_run_state(run_id)

    def resolve_artifact(self, artifact: ArtifactRef) -> Path:
        return self._artifact_store.resolve(artifact).path

    def _verify_controlled_qualification(self, descriptor: SkillDescriptor) -> None:
        if (
            descriptor.skill_id != "image2env.compile"
            or descriptor.version != "1.0.0"
            or descriptor.input_schema != _INPUT_SCHEMA
            or descriptor.output_schema != _OUTPUT_SCHEMA
            or descriptor.mcp_tool_name != _MCP_TOOL_NAME
            or descriptor.implementation_sha256 != self._handler.expected_implementation_sha256
            or not descriptor.deterministic
            or descriptor.max_attempts != 1
        ):
            raise ValueError(
                "controlled qualification does not describe exact image2env.compile@1.0.0"
            )
        try:
            qualification_path = self._artifact_store.resolve(
                descriptor.qualification_artifact
            ).path
            qualification = SkillQualification.model_validate_json(qualification_path.read_bytes())
            report_path = self._artifact_store.resolve_digest(qualification.report_sha256)
            report = QualificationReportV1.model_validate_json(report_path.read_bytes())
        except (ArtifactResolutionError, OSError, ValueError, ValidationError) as error:
            raise ValueError(
                "controlled qualification evidence is unavailable or invalid"
            ) from error
        if (
            qualification.skill_ref != _SKILL_REF
            or qualification.status != "pass"
            or qualification.deterministic_case_id != _CONTROLLED_CASE_ID
            or qualification.regression_command != report.regression_command
            or report.skill_ref != _SKILL_REF
            or report.status != "pass"
            or report.deterministic_case_id != _CONTROLLED_CASE_ID
            or report.implementation_sha256 != descriptor.implementation_sha256
        ):
            raise ValueError("controlled qualification evidence has the wrong authority")
        checks = {check.name: check for check in report.checks}
        if set(checks) != {"candidate_execution", "output_closure"}:
            raise ValueError("controlled qualification report has the wrong checks")
        execution = checks["candidate_execution"].evidence
        closure = checks["output_closure"].evidence
        try:
            run_id = UUID(str(execution["run_id"]))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("controlled qualification candidate identity is invalid") from error
        invocation = self._run_store.read_invocation(run_id)
        state = self._run_store.read_run_state(run_id)
        if invocation is None or state is None or state.status is not RunStatus.SUCCEEDED:
            raise ValueError("controlled qualification candidate run is unavailable")
        output = Image2EnvCompileV2Output.model_validate(state.output)
        expected_artifacts = sorted(artifact.sha256 for artifact in state.artifacts)
        expected_output_sha256 = hashlib.sha256(_canonical_json_bytes(state.output)).hexdigest()
        if (
            execution.get("controlled") is not True
            or execution.get("mode") != "qualification_candidate"
            or state.skill_id != "image2env.compile"
            or state.skill_version != "1.0.0"
            or not all(event.stage.startswith("qualification_candidate.") for event in state.events)
            or execution.get("invocation_digest") != invocation.invocation_digest
            or execution.get("status") != RunStatus.SUCCEEDED.value
            or execution.get("simulator_executed") is not False
            or execution.get("publishable") is not False
            or closure.get("output_sha256") != expected_output_sha256
            or closure.get("artifact_sha256s") != expected_artifacts
            or closure.get("artifact_count") != len(state.artifacts)
            or closure.get("compile_receipt_sha256") != output.compile_receipt.sha256
        ):
            raise ValueError("controlled qualification report does not bind its candidate run")


def create_controlled_image2env_compile_application(
    settings: ControlledImage2EnvCompileApplicationSettings,
    *,
    handler: Image2EnvExactReuseCompileHandler,
    clock: Callable[[], datetime],
    run_id_factory: Callable[[], UUID],
) -> ControlledImage2EnvCompileApplication:
    """Create an unregistered, durable controlled image compile application."""

    if type(settings) is not ControlledImage2EnvCompileApplicationSettings:
        raise TypeError("settings must be exact ControlledImage2EnvCompileApplicationSettings")
    if type(handler) is not Image2EnvExactReuseCompileHandler:
        raise TypeError("handler must be exact Image2EnvExactReuseCompileHandler")
    if not callable(clock):
        raise TypeError("clock must be callable")
    if not callable(run_id_factory):
        raise TypeError("run_id_factory must be callable")
    state_root = Path(settings.state_root).expanduser().resolve()
    evidence_root = Path(settings.evidence_artifact_root).expanduser().resolve()
    for root, label in ((state_root, "state_root"), (evidence_root, "evidence_artifact_root")):
        if root.exists() and (not root.is_dir() or root.is_symlink()):
            raise ValueError(f"{label} must be a non-symlink directory")
        root.mkdir(parents=True, exist_ok=True)
    if handler.artifact_store.root.resolve() != evidence_root:
        raise ValueError("handler evidence CAS differs from application evidence CAS")
    artifact_store = LocalArtifactStore(evidence_root)
    database = state_root / "harness.sqlite3"
    run_store = SQLiteRunStore(database)
    registry = SkillRegistry(
        artifact_resolver=artifact_store,
        dependency_resolver=StaticDependencyResolver(
            {
                _SKILL_REF: (
                    DependencyRef(
                        name="controlled_exact_reuse",
                        version="1",
                        sha256=handler.expected_implementation_sha256,
                    ),
                )
            }
        ),
        event_sink=SQLiteEventJournal(database),
        clock=clock,
        run_id_factory=run_id_factory,
        run_store=run_store,
    )
    return ControlledImage2EnvCompileApplication(
        artifact_store=artifact_store,
        registry=registry,
        run_store=run_store,
        handler=handler,
    )


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


__all__ = [
    "ControlledImage2EnvCompileApplication",
    "ControlledImage2EnvCompileApplicationRunIdentityError",
    "ControlledImage2EnvCompileApplicationRunPendingError",
    "ControlledImage2EnvCompileApplicationSettings",
    "create_controlled_image2env_compile_application",
]
