"""Exact production binding for legacy text2env validate Golden execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel

from .artifacts import LocalArtifactStore
from .golden_execution import (
    GoldenExecutionBinding,
    QualifiedApplicationRunPendingError,
    _create_golden_execution_binding,
)
from .golden_validate_policy import Text2EnvValidateV2EvidencePolicy
from .run_store import SQLiteRunStore
from .schemas.common import Invocation, RegisteredSkillDescriptor, RunState
from .schemas.text2env_validate_v2 import Text2EnvValidateV2Input
from .validate_application import (
    ValidateApplication,
    ValidateApplicationRunPendingError,
)


@dataclass(frozen=True)
class _Text2EnvValidateApplicationAdapter:
    application: ValidateApplication
    policy: Text2EnvValidateV2EvidencePolicy
    skill_ref: str = "text2env.validate@2.0.0"

    def __post_init__(self) -> None:
        if type(self.application) is not ValidateApplication:
            raise TypeError("application must be the exact ValidateApplication")
        self.application._assert_golden_execution_ready()

    @property
    def artifact_root(self) -> Path:
        self.application._assert_golden_execution_ready()
        return self.application.artifact_root

    @property
    def authority_path(self) -> Path:
        self.application._assert_golden_execution_ready()
        return self.application.journal_path

    @property
    def skills(self) -> tuple[RegisteredSkillDescriptor, ...]:
        self.application._assert_golden_execution_ready()
        return self.application.skills

    def invoke(self, parameters: BaseModel, *, run_id: UUID) -> RunState:
        self.application._assert_golden_execution_ready()
        if type(parameters) is not Text2EnvValidateV2Input:
            raise TypeError("validate adapter requires Text2EnvValidateV2Input")
        try:
            return self.application.invoke_typed(parameters, run_id=run_id)
        except ValidateApplicationRunPendingError as error:
            raise QualifiedApplicationRunPendingError(str(error)) from error

    def invocation(self, run_id: UUID) -> Invocation | None:
        return self.application.invocation(run_id)

    def run_state(self, run_id: UUID) -> RunState | None:
        return self.application.run_state(run_id)


def create_text2env_validate_execution_binding(
    *,
    application: ValidateApplication | None = None,
    child_authority: SQLiteRunStore | None = None,
    artifact_store: LocalArtifactStore | None = None,
) -> GoldenExecutionBinding:
    """Bind validate's store-aware policy to its application or restart authority."""

    if application is not None and type(application) is not ValidateApplication:
        raise TypeError("application must be the exact ValidateApplication")
    if child_authority is not None and type(child_authority) is not SQLiteRunStore:
        raise TypeError("child_authority must be the exact SQLiteRunStore")
    if artifact_store is not None and type(artifact_store) is not LocalArtifactStore:
        raise TypeError("artifact_store must be the exact LocalArtifactStore")
    if application is not None:
        live_store = LocalArtifactStore(application.artifact_root)
        if artifact_store is not None and artifact_store.root != live_store.root:
            raise ValueError("artifact_store differs from validate application CAS")
        artifact_store = live_store
    if artifact_store is None:
        raise ValueError("validate binding requires its evidence artifact_store")
    policy = Text2EnvValidateV2EvidencePolicy(artifact_store=artifact_store)
    adapter = (
        None
        if application is None
        else _Text2EnvValidateApplicationAdapter(application=application, policy=policy)
    )
    return _create_golden_execution_binding(
        policy=policy,
        application=adapter,
        child_authority=child_authority,
    )


__all__ = ["create_text2env_validate_execution_binding"]
