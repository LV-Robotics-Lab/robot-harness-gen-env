"""Actor-neutral catalog for exact qualified GoldenRun execution bindings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Protocol
from uuid import UUID

from pydantic import BaseModel

from .run_store import ControlledRunLifecycle, SQLiteRunStore
from .schema_catalog import schema_model
from .schemas.common import ArtifactRef, Invocation, RegisteredSkillDescriptor, RunState
from .schemas.registry_snapshot import QualifiedSkillSnapshot
from .schemas.workflow_operation import (
    QualifiedSkillExecutionV2,
    ReceiptDerivedFactClaimV2,
    ToolResultV2,
)
from .trusted_state import TrustedWorldState, WorldFact


class QualifiedApplicationRunPendingError(RuntimeError):
    """The exact application started a child whose terminal state is not recoverable yet."""


@dataclass(frozen=True)
class SkillAuthorizationContext:
    """Current parent authority supplied only by the Golden aggregate."""

    workflow_run_id: UUID
    principal_id: str
    receipt_head: ArtifactRef
    revision: int
    state_ref: ArtifactRef


class SkillEvidencePolicy(Protocol):
    """Skill-owned interpretation of typed input and terminal output evidence."""

    skill_ref: str
    input_model: type[BaseModel]
    evidence_policy_id: str
    evidence_policy_version: str

    @property
    def evidence_policy_implementation_sha256(self) -> str: ...

    def normalize_parameters(self, parameters: Mapping[str, object]) -> BaseModel: ...

    def authorize(self, state: TrustedWorldState, parameters: BaseModel) -> None: ...

    def claims(self, state: RunState) -> tuple[ReceiptDerivedFactClaimV2, ...]: ...

    def diagnostics(self, state: RunState) -> tuple[ArtifactRef, ...]: ...

    def fresh_observations(
        self,
        state: RunState,
        invocation: Invocation | None,
    ) -> tuple[WorldFact, ...]: ...

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


class GoldenExecutionEvidenceHook(Protocol):
    """Optional deeper authority checks for an honestly projected native Skill."""

    def verify_registry_entry(self, entry: QualifiedSkillSnapshot) -> None: ...

    def verify_child_lifecycle(
        self,
        *,
        lifecycle: ControlledRunLifecycle,
        skill_ref: str,
        child_run_id: UUID,
        command_parameters: Mapping[str, object],
        qualified: QualifiedSkillExecutionV2,
    ) -> tuple[ArtifactRef, ...]: ...

    def verify_committed_evidence(
        self,
        *,
        lifecycle: ControlledRunLifecycle,
        skill_ref: str,
        child_run_id: UUID,
        evidence: tuple[ArtifactRef, ...],
    ) -> None: ...

    def resolve_artifact_member(
        self,
        *,
        skill_ref: str,
        child_run_id: UUID,
        evidence: tuple[ArtifactRef, ...],
        sha256: str,
    ) -> ArtifactRef | None: ...


@dataclass(frozen=True)
class _CatalogEntry:
    policy: SkillEvidencePolicy
    application: QualifiedApplicationAdapter | None
    child_authority: SQLiteRunStore | None
    evidence_hook: GoldenExecutionEvidenceHook | None


_CATALOG_TOKEN = object()
_BINDING_TOKEN = object()
_HOOK_TOKEN = object()


class _SealedGoldenExecutionEvidenceHook:
    """Opaque hook accepted only through the Harness-owned hook factory."""

    __slots__ = ("_delegate", "_token")

    def __init__(self, delegate: GoldenExecutionEvidenceHook, *, _token: object) -> None:
        if _token is not _HOOK_TOKEN:
            raise TypeError("execution evidence hooks must be created by their authority factory")
        object.__setattr__(self, "_delegate", delegate)
        object.__setattr__(self, "_token", _token)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("execution evidence hooks are immutable")

    def verify_registry_entry(self, entry: QualifiedSkillSnapshot) -> None:
        self._delegate.verify_registry_entry(entry)

    def verify_child_lifecycle(
        self,
        *,
        lifecycle: ControlledRunLifecycle,
        skill_ref: str,
        child_run_id: UUID,
        command_parameters: Mapping[str, object],
        qualified: QualifiedSkillExecutionV2,
    ) -> tuple[ArtifactRef, ...]:
        return self._delegate.verify_child_lifecycle(
            lifecycle=lifecycle,
            skill_ref=skill_ref,
            child_run_id=child_run_id,
            command_parameters=command_parameters,
            qualified=qualified,
        )

    def verify_committed_evidence(
        self,
        *,
        lifecycle: ControlledRunLifecycle,
        skill_ref: str,
        child_run_id: UUID,
        evidence: tuple[ArtifactRef, ...],
    ) -> None:
        self._delegate.verify_committed_evidence(
            lifecycle=lifecycle,
            skill_ref=skill_ref,
            child_run_id=child_run_id,
            evidence=evidence,
        )

    def resolve_artifact_member(
        self,
        *,
        skill_ref: str,
        child_run_id: UUID,
        evidence: tuple[ArtifactRef, ...],
        sha256: str,
    ) -> ArtifactRef | None:
        return self._delegate.resolve_artifact_member(
            skill_ref=skill_ref,
            child_run_id=child_run_id,
            evidence=evidence,
            sha256=sha256,
        )

    def _is_factory_hook(self) -> bool:
        return self._token is _HOOK_TOKEN


def _create_golden_execution_evidence_hook(
    delegate: GoldenExecutionEvidenceHook,
) -> GoldenExecutionEvidenceHook:
    """Seal one deeper verifier for consumption by GoldenRun."""

    return _SealedGoldenExecutionEvidenceHook(delegate, _token=_HOOK_TOKEN)


class GoldenExecutionBinding(Protocol):
    """Read-only public view of an opaque Skill-owned execution binding."""

    @property
    def skill_ref(self) -> str: ...

    @property
    def policy(self) -> SkillEvidencePolicy: ...

    @property
    def application(self) -> QualifiedApplicationAdapter | None: ...

    @property
    def child_authority(self) -> SQLiteRunStore | None: ...

    @property
    def evidence_hook(self) -> GoldenExecutionEvidenceHook | None: ...


class _SealedGoldenExecutionBinding:
    """Opaque concrete binding accepted only after factory and consumption checks."""

    __slots__ = (
        "_application",
        "_child_authority",
        "_evidence_hook",
        "_policy",
        "_skill_ref",
        "_token",
    )

    def __init__(
        self,
        *,
        _token: object,
        skill_ref: str,
        policy: SkillEvidencePolicy,
        application: QualifiedApplicationAdapter | None,
        child_authority: SQLiteRunStore | None,
        evidence_hook: GoldenExecutionEvidenceHook | None,
    ) -> None:
        if _token is not _BINDING_TOKEN:
            raise TypeError("execution bindings must be created by an exact Skill factory")
        object.__setattr__(self, "_token", _token)
        object.__setattr__(self, "_skill_ref", skill_ref)
        object.__setattr__(self, "_policy", policy)
        object.__setattr__(self, "_application", application)
        object.__setattr__(self, "_child_authority", child_authority)
        object.__setattr__(self, "_evidence_hook", evidence_hook)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("execution bindings are immutable")

    @property
    def skill_ref(self) -> str:
        return self._skill_ref

    @property
    def policy(self) -> SkillEvidencePolicy:
        return self._policy

    @property
    def application(self) -> QualifiedApplicationAdapter | None:
        return self._application

    @property
    def child_authority(self) -> SQLiteRunStore | None:
        return self._child_authority

    @property
    def evidence_hook(self) -> GoldenExecutionEvidenceHook | None:
        return self._evidence_hook

    def _is_factory_binding(self) -> bool:
        return self._token is _BINDING_TOKEN


def _validate_binding_components(
    *,
    skill_ref: str,
    policy: SkillEvidencePolicy,
    application: QualifiedApplicationAdapter | None,
    child_authority: SQLiteRunStore | None,
    evidence_hook: GoldenExecutionEvidenceHook | None,
    require_application_authority: bool,
) -> SQLiteRunStore | None:
    if child_authority is not None and type(child_authority) is not SQLiteRunStore:
        raise TypeError("child_authority must be the exact SQLiteRunStore")
    if skill_ref != policy.skill_ref:
        raise ValueError("binding and evidence policy are not exactly bound")
    if evidence_hook is not None and (
        type(evidence_hook) is not _SealedGoldenExecutionEvidenceHook
        or not evidence_hook._is_factory_hook()
    ):
        raise TypeError("evidence_hook must come from the Harness hook factory")
    input_model = policy.input_model
    if not isinstance(input_model, type) or not issubclass(input_model, BaseModel):
        raise TypeError("evidence policy input model must be a BaseModel type")
    if application is None:
        return child_authority
    if application.skill_ref != skill_ref or application.policy is not policy:
        raise ValueError("application and evidence policy are not exactly bound")
    matching = tuple(
        descriptor
        for descriptor in application.skills
        if f"{descriptor.skill_id}@{descriptor.version}" == skill_ref
    )
    if len(matching) != 1:
        raise ValueError("application does not expose its one exact live descriptor")
    if schema_model(matching[0].input_schema) is not input_model:
        raise ValueError("application live descriptor and policy input model are not exactly bound")
    application_authority = SQLiteRunStore(application.authority_path)
    if child_authority is None:
        if require_application_authority:
            raise ValueError("application and child authority are not exactly bound")
        return application_authority
    if child_authority.path.resolve() != application_authority.path.resolve():
        raise ValueError("application and child authority do not match")
    return application_authority


def _create_golden_execution_binding(
    *,
    policy: SkillEvidencePolicy,
    application: QualifiedApplicationAdapter | None,
    child_authority: SQLiteRunStore | None,
    evidence_hook: GoldenExecutionEvidenceHook | None = None,
) -> GoldenExecutionBinding:
    """Validate one Skill-owned binding without knowing its concrete application type."""

    child_authority = _validate_binding_components(
        skill_ref=policy.skill_ref,
        policy=policy,
        application=application,
        child_authority=child_authority,
        evidence_hook=evidence_hook,
        require_application_authority=False,
    )
    return _SealedGoldenExecutionBinding(
        _token=_BINDING_TOKEN,
        skill_ref=policy.skill_ref,
        policy=policy,
        application=application,
        child_authority=child_authority,
        evidence_hook=evidence_hook,
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

    def evidence_hook_for(self, skill_ref: str) -> GoldenExecutionEvidenceHook | None:
        entry = self._entries.get(skill_ref)
        return None if entry is None else entry.evidence_hook

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
        if type(binding) is not _SealedGoldenExecutionBinding or not binding._is_factory_binding():
            raise TypeError("bindings must come from an exact Skill binding factory")
        _validate_binding_components(
            skill_ref=binding.skill_ref,
            policy=binding.policy,
            application=binding.application,
            child_authority=binding.child_authority,
            evidence_hook=binding.evidence_hook,
            require_application_authority=True,
        )
        if binding.skill_ref in entries:
            raise ValueError(f"duplicate Golden execution binding: {binding.skill_ref}")
        entries[binding.skill_ref] = _CatalogEntry(
            policy=binding.policy,
            application=binding.application,
            child_authority=binding.child_authority,
            evidence_hook=binding.evidence_hook,
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
