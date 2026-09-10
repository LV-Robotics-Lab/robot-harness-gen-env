"""Controlled Golden binding for receipt-bound ``genesis.observe@1.0.0`` evidence.

This module deliberately does not enter production assembly.  It lets a sealed,
test-controlled qualification exercise the same caller-owned child UUID,
durability, evidence policy, and Golden aggregate path used by production Skills.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel

from .artifacts import LocalArtifactStore
from .genesis_observe import (
    ControlledGenesisObserveApplication,
    ControlledObserveApplicationRunPendingError,
)
from .golden_execution import (
    GoldenExecutionBinding,
    QualifiedApplicationRunPendingError,
    _create_golden_execution_binding,
)
from .golden_observe_policy import ControlledGenesisObserveEvidencePolicy
from .run_store import SQLiteRunStore
from .schemas.common import (
    Invocation,
    RegisteredSkillDescriptor,
    RunState,
    SkillDescriptor,
)
from .schemas.genesis_observe import GenesisObserveInput


@dataclass(frozen=True)
class _ControlledGenesisObserveApplicationAdapter:
    application: ControlledGenesisObserveApplication
    policy: ControlledGenesisObserveEvidencePolicy
    skill_ref: str = "genesis.observe@1.0.0"

    @property
    def artifact_root(self) -> Path:
        return self.application.artifact_root

    @property
    def authority_path(self) -> Path:
        return self.application.authority_path

    @property
    def skills(self) -> tuple[RegisteredSkillDescriptor, ...]:
        return self.application.skills

    def invoke(self, parameters: BaseModel, *, run_id: UUID) -> RunState:
        if type(parameters) is not GenesisObserveInput:
            raise TypeError("observe adapter requires GenesisObserveInput")
        try:
            return self.application.invoke_typed(parameters, run_id=run_id)
        except ControlledObserveApplicationRunPendingError as error:
            raise QualifiedApplicationRunPendingError(str(error)) from error

    def invocation(self, run_id: UUID) -> Invocation | None:
        return self.application.invocation(run_id)

    def run_state(self, run_id: UUID) -> RunState | None:
        return self.application.run_state(run_id)


def create_controlled_genesis_observe_execution_binding(
    *,
    application: ControlledGenesisObserveApplication | None,
    qualified_descriptor: SkillDescriptor | None = None,
    child_authority: SQLiteRunStore | None = None,
    artifact_store: LocalArtifactStore | None = None,
) -> GoldenExecutionBinding:
    """Bind controlled observation to Golden without claiming production qualification."""

    if application is not None and type(application) is not ControlledGenesisObserveApplication:
        raise TypeError("application must be the exact ControlledGenesisObserveApplication")
    if qualified_descriptor is not None and type(qualified_descriptor) is not SkillDescriptor:
        raise TypeError("qualified_descriptor must be the exact SkillDescriptor")
    if child_authority is not None and type(child_authority) is not SQLiteRunStore:
        raise TypeError("child_authority must be the exact SQLiteRunStore")
    if artifact_store is not None and type(artifact_store) is not LocalArtifactStore:
        raise TypeError("artifact_store must be the exact LocalArtifactStore")
    if application is None:
        if qualified_descriptor is not None:
            raise ValueError("restart binding cannot install a live qualification")
        if artifact_store is None:
            raise ValueError("restart binding requires the controlled evidence artifact_store")
    else:
        live_store = LocalArtifactStore(application.artifact_root)
        if artifact_store is not None and artifact_store.root != live_store.root:
            raise ValueError("artifact_store differs from controlled observe application CAS")
        if qualified_descriptor is None:
            raise ValueError("live binding requires one controlled qualified_descriptor")
        application._bind_controlled_descriptor(qualified_descriptor)
        artifact_store = live_store
    assert artifact_store is not None
    policy = ControlledGenesisObserveEvidencePolicy(artifact_store=artifact_store)
    adapter = (
        None
        if application is None
        else _ControlledGenesisObserveApplicationAdapter(application=application, policy=policy)
    )
    return _create_golden_execution_binding(
        policy=policy,
        application=adapter,
        child_authority=child_authority,
    )


__all__ = ["create_controlled_genesis_observe_execution_binding"]
