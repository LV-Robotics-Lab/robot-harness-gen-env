"""Actor-neutral catalog for exact qualified GoldenRun execution bindings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Protocol
from uuid import UUID

from pydantic import BaseModel

from .run_store import SQLiteRunStore
from .schema_catalog import schema_model
from .schemas.common import ArtifactRef, Invocation, RegisteredSkillDescriptor, RunState
from .schemas.workflow import RunCommandRequest
from .schemas.workflow_operation import ReceiptDerivedFactClaimV2, ToolResultV2
from .trusted_state import TrustedWorldState


class QualifiedApplicationRunPendingError(RuntimeError):
    """The exact application started a child whose terminal state is not recoverable yet."""


class SkillEvidencePolicy(Protocol):
    """Skill-owned interpretation of typed input and terminal output evidence."""

    skill_ref: str
    input_model: type[BaseModel]
    evidence_policy_id: str
    evidence_policy_version: str

    @property
    def evidence_policy_implementation_sha256(self) -> str: ...

    def normalize(self, request: RunCommandRequest) -> tuple[RunCommandRequest, BaseModel]: ...

    def authorize(self, state: TrustedWorldState, parameters: BaseModel) -> None: ...

    def claims(self, state: RunState) -> tuple[ReceiptDerivedFactClaimV2, ...]: ...

    def diagnostics(self, state: RunState) -> tuple[ArtifactRef, ...]: ...

    def verify_result(
        self,
        *,
        state: RunState,
        claims: tuple[ReceiptDerivedFactClaimV2, ...],
        result: ToolResultV2,
        supporting: tuple[ArtifactRef, ...],
    ) -> None: ...


class QualifiedApplicationAdapter(Protocol):
    """Actor-neutral interface required from one exact qualified application."""

    skill_ref: str
    policy: SkillEvidencePolicy

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
class _CatalogEntry:
    policy: SkillEvidencePolicy
    application: QualifiedApplicationAdapter | None
    child_authority: SQLiteRunStore | None


_CATALOG_TOKEN = object()
_BINDING_TOKEN = object()


@dataclass(frozen=True)
class GoldenExecutionBinding:
    """Sealed exact Skill binding assembled by one Skill-owned factory."""

    _token: object
    skill_ref: str
    policy: SkillEvidencePolicy
    application: QualifiedApplicationAdapter | None
    child_authority: SQLiteRunStore | None


def _create_golden_execution_binding(
    *,
    policy: SkillEvidencePolicy,
    application: QualifiedApplicationAdapter | None,
    child_authority: SQLiteRunStore | None,
) -> GoldenExecutionBinding:
    """Validate one Skill-owned binding without knowing its concrete application type."""

    if child_authority is not None and type(child_authority) is not SQLiteRunStore:
        raise TypeError("child_authority must be the exact SQLiteRunStore")
    if application is not None:
        if application.skill_ref != policy.skill_ref or application.policy is not policy:
            raise ValueError("application and evidence policy are not exactly bound")
        matching = tuple(
            descriptor
            for descriptor in application.skills
            if f"{descriptor.skill_id}@{descriptor.version}" == policy.skill_ref
        )
        if len(matching) != 1:
            raise ValueError("application does not expose its one exact Skill")
        if schema_model(matching[0].input_schema) is not policy.input_model:
            raise ValueError("application and evidence policy are not exactly bound")
        application_authority = SQLiteRunStore(application.authority_path)
        if child_authority is not None and (
            child_authority.path.resolve() != application_authority.path.resolve()
        ):
            raise ValueError("application and child authority do not match")
        child_authority = application_authority
    return GoldenExecutionBinding(
        _token=_BINDING_TOKEN,
        skill_ref=policy.skill_ref,
        policy=policy,
        application=application,
        child_authority=child_authority,
    )


class GoldenExecutionCatalog:
    """Sealed trust root for exact application adapters and evidence policies."""

    def __init__(self, *, _token: object, entries: Mapping[str, _CatalogEntry]) -> None:
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

    def application_for(self, skill_ref: str) -> QualifiedApplicationAdapter | None:
        entry = self._entries.get(skill_ref)
        return None if entry is None else entry.application

    def policy_for(self, skill_ref: str) -> SkillEvidencePolicy | None:
        entry = self._entries.get(skill_ref)
        return None if entry is None else entry.policy

    def child_authority_for(self, skill_ref: str) -> SQLiteRunStore | None:
        entry = self._entries.get(skill_ref)
        return None if entry is None else entry.child_authority

    def live_descriptors_for(self, skill_ref: str) -> tuple[RegisteredSkillDescriptor, ...]:
        application = self.application_for(skill_ref)
        return () if application is None else application.skills

    def invoke(self, skill_ref: str, parameters: BaseModel, *, run_id: UUID) -> RunState:
        application = self.application_for(skill_ref)
        if application is None:
            raise LookupError(f"no live qualified application for {skill_ref}")
        return application.invoke(parameters, run_id=run_id)


def create_golden_execution_catalog(
    *,
    bindings: tuple[GoldenExecutionBinding, ...] = (),
) -> GoldenExecutionCatalog:
    """Assemble a sealed catalog from independently owned exact Skill bindings."""

    if type(bindings) is not tuple:
        raise TypeError("bindings must be an exact tuple")
    entries: dict[str, _CatalogEntry] = {}
    for binding in bindings:
        if type(binding) is not GoldenExecutionBinding or binding._token is not _BINDING_TOKEN:
            raise TypeError("bindings must come from an exact Skill binding factory")
        if binding.skill_ref in entries:
            raise ValueError(f"duplicate Golden execution binding: {binding.skill_ref}")
        entries[binding.skill_ref] = _CatalogEntry(
            policy=binding.policy,
            application=binding.application,
            child_authority=binding.child_authority,
        )
    return GoldenExecutionCatalog(_token=_CATALOG_TOKEN, entries=entries)


__all__ = [
    "GoldenExecutionCatalog",
    "GoldenExecutionBinding",
    "QualifiedApplicationAdapter",
    "QualifiedApplicationRunPendingError",
    "SkillEvidencePolicy",
    "create_golden_execution_catalog",
]
