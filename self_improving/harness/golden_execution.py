"""Exact application adapters and per-Skill evidence policies for GoldenRun."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel

from .application import CompileApplication, CompileApplicationRunPendingError
from .run_store import SQLiteRunStore
from .schema_catalog import schema_model
from .schemas.common import (
    ArtifactRef,
    Invocation,
    RegisteredSkillDescriptor,
    RunState,
    RunStatus,
)
from .schemas.text2env import Text2EnvCompileInput, Text2EnvCompileOutput
from .schemas.workflow import RunCommandRequest
from .schemas.workflow_operation import ReceiptDerivedFactClaimV2, ToolResultV2
from .trusted_state import TrustedWorldState


class QualifiedApplicationRunPendingError(RuntimeError):
    """The exact application started a child whose terminal state is not recoverable yet."""


class _GoldenEvidencePolicy(Protocol):
    """Skill-owned interpretation of typed input and terminal output evidence."""

    skill_ref: str
    input_model: type[BaseModel]

    def normalize(
        self,
        request: RunCommandRequest,
    ) -> tuple[RunCommandRequest, BaseModel]: ...

    def claims(self, state: RunState) -> tuple[ReceiptDerivedFactClaimV2, ...]: ...

    def authorize(self, state: TrustedWorldState, parameters: BaseModel) -> None: ...

    def verify_result(
        self,
        *,
        state: RunState,
        claims: tuple[ReceiptDerivedFactClaimV2, ...],
        result: ToolResultV2,
        supporting: tuple[ArtifactRef, ...],
    ) -> None: ...


class _QualifiedApplicationAdapter(Protocol):
    """Actor-neutral surface required from one exact qualified application."""

    skill_ref: str
    policy: _GoldenEvidencePolicy

    @property
    def artifact_root(self) -> Path: ...

    @property
    def authority_path(self) -> Path: ...

    @property
    def skills(self) -> tuple[RegisteredSkillDescriptor, ...]: ...

    def invoke(self, parameters: BaseModel, *, run_id: UUID) -> RunState: ...

    def invocation(self, run_id: UUID) -> Invocation | None: ...

    def run_state(self, run_id: UUID) -> RunState | None: ...


@dataclass(frozen=True)
class _Text2EnvCompileEvidencePolicy:
    """The compile-specific meaning of a successful ToolResult."""

    skill_ref: str = "text2env.compile@1.0.0"
    input_model: type[BaseModel] = Text2EnvCompileInput

    def normalize(
        self,
        request: RunCommandRequest,
    ) -> tuple[RunCommandRequest, BaseModel]:
        if request.skill_ref != self.skill_ref:
            raise ValueError(f"policy does not own {request.skill_ref}")
        typed = Text2EnvCompileInput.model_validate(request.parameters)
        normalized = RunCommandRequest.model_validate(
            {
                **request.model_dump(mode="python"),
                "parameters": typed.model_dump(mode="json"),
            }
        )
        return normalized, typed

    def claims(self, state: RunState) -> tuple[ReceiptDerivedFactClaimV2, ...]:
        if state.status is not RunStatus.SUCCEEDED:
            return ()
        output = Text2EnvCompileOutput.model_validate(state.output)
        return (
            ReceiptDerivedFactClaimV2(
                key="assets.catalog",
                value=output.environment_package.asset_catalog.model_dump(mode="json"),
            ),
            ReceiptDerivedFactClaimV2(
                key="environment.package",
                value=output.environment_package.model_dump(mode="json"),
            ),
        )

    def authorize(self, state: TrustedWorldState, parameters: BaseModel) -> None:
        if type(parameters) is not Text2EnvCompileInput:
            raise TypeError("compile policy requires Text2EnvCompileInput")
        objectives = tuple(fact for fact in state.facts if fact.key == "request.objective")
        if len(objectives) != 1 or type(objectives[0].value) is not str:
            raise ValueError("compile requires one trusted request.objective fact")

    def verify_result(
        self,
        *,
        state: RunState,
        claims: tuple[ReceiptDerivedFactClaimV2, ...],
        result: ToolResultV2,
        supporting: tuple[ArtifactRef, ...],
    ) -> None:
        if result.fresh_observations:
            raise ValueError("compile cannot claim fresh observations")
        expected_diagnostics = () if state.status is RunStatus.SUCCEEDED else supporting
        if result.diagnostics != expected_diagnostics:
            raise ValueError("compile diagnostics do not equal the canonical terminal evidence")
        if claims != self.claims(state):
            raise ValueError("compile claims do not equal the exact typed output")


@dataclass(frozen=True)
class _Text2EnvCompileApplicationAdapter:
    """Exact adapter around the production compile application."""

    application: CompileApplication
    policy: _Text2EnvCompileEvidencePolicy = _Text2EnvCompileEvidencePolicy()
    skill_ref: str = "text2env.compile@1.0.0"

    def __post_init__(self) -> None:
        if type(self.application) is not CompileApplication:
            raise TypeError("application must be the exact CompileApplication")

    @property
    def artifact_root(self) -> Path:
        return self.application.artifact_root

    @property
    def skills(self) -> tuple[RegisteredSkillDescriptor, ...]:
        return self.application.skills

    @property
    def authority_path(self) -> Path:
        return self.application.journal_path

    def invoke(self, parameters: BaseModel, *, run_id: UUID) -> RunState:
        if type(parameters) is not Text2EnvCompileInput:
            raise TypeError("compile adapter requires Text2EnvCompileInput")
        try:
            return self.application.invoke_typed(parameters, run_id=run_id)
        except CompileApplicationRunPendingError as error:
            raise QualifiedApplicationRunPendingError(str(error)) from error

    def invocation(self, run_id: UUID) -> Invocation | None:
        return self.application.invocation(run_id)

    def run_state(self, run_id: UUID) -> RunState | None:
        return self.application.run_state(run_id)


@dataclass(frozen=True)
class _CatalogEntry:
    policy: _GoldenEvidencePolicy
    application: _QualifiedApplicationAdapter | None
    child_authority: SQLiteRunStore | None


_CATALOG_TOKEN = object()


class GoldenExecutionCatalog:
    """Sealed trust root for exact application adapters and evidence policies."""

    def __init__(
        self,
        *,
        _token: object,
        entries: Mapping[str, _CatalogEntry],
    ) -> None:
        if _token is not _CATALOG_TOKEN:
            raise TypeError("GoldenExecutionCatalog must be created by its authority factory")
        self._entries = MappingProxyType(dict(entries))

    @property
    def artifact_roots(self) -> frozenset[Path]:
        return frozenset(
            entry.application.artifact_root
            for entry in self._entries.values()
            if entry.application is not None
        )

    def application_for(self, skill_ref: str) -> _QualifiedApplicationAdapter | None:
        entry = self._entries.get(skill_ref)
        return None if entry is None else entry.application

    def live_descriptors_for(
        self,
        skill_ref: str,
    ) -> tuple[RegisteredSkillDescriptor, ...]:
        application = self.application_for(skill_ref)
        return () if application is None else application.skills

    def invoke(self, skill_ref: str, parameters: BaseModel, *, run_id: UUID) -> RunState:
        application = self.application_for(skill_ref)
        if application is None:
            raise LookupError(f"no live qualified application for {skill_ref}")
        return application.invoke(parameters, run_id=run_id)

    def policy_for(self, skill_ref: str) -> _GoldenEvidencePolicy | None:
        entry = self._entries.get(skill_ref)
        return None if entry is None else entry.policy

    def child_authority_for(self, skill_ref: str) -> SQLiteRunStore | None:
        entry = self._entries.get(skill_ref)
        return None if entry is None else entry.child_authority


def create_golden_execution_catalog(
    compile_application: CompileApplication | None = None,
    *,
    compile_child_authority: SQLiteRunStore | None = None,
) -> GoldenExecutionCatalog:
    """Assemble the P2 catalog; only the exact production compile app is admitted."""

    policy = _Text2EnvCompileEvidencePolicy()
    if (
        compile_child_authority is not None
        and type(compile_child_authority) is not SQLiteRunStore
    ):
        raise TypeError("compile_child_authority must be the exact SQLiteRunStore")
    application: _Text2EnvCompileApplicationAdapter | None = None
    if compile_application is not None:
        if type(compile_application) is not CompileApplication:
            raise TypeError("compile_application must be the exact CompileApplication")
        application = _Text2EnvCompileApplicationAdapter(
            compile_application,
            policy=policy,
        )
        matching = tuple(
            descriptor
            for descriptor in application.skills
            if f"{descriptor.skill_id}@{descriptor.version}" == policy.skill_ref
        )
        if len(matching) != 1:
            raise ValueError("compile application does not expose its one exact Skill")
        descriptor = matching[0]
        if schema_model(descriptor.input_schema) is not policy.input_model:
            raise ValueError("compile application and evidence policy are not exactly bound")
        application_authority = SQLiteRunStore(application.authority_path)
        if compile_child_authority is not None and (
            compile_child_authority.path.resolve() != application_authority.path.resolve()
        ):
            raise ValueError("compile application and child authority do not match")
        compile_child_authority = application_authority
    entries = {
        policy.skill_ref: _CatalogEntry(
            policy=policy,
            application=application,
            child_authority=compile_child_authority,
        )
    }
    return GoldenExecutionCatalog(_token=_CATALOG_TOKEN, entries=entries)


__all__ = [
    "GoldenExecutionCatalog",
    "QualifiedApplicationRunPendingError",
    "create_golden_execution_catalog",
]
