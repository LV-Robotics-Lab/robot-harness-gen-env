"""Exact production application binding for text2env compile Golden execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel

from .application import CompileApplication, CompileApplicationRunPendingError
from .golden_compile_policy import Text2EnvCompileEvidencePolicy
from .golden_execution import (
    GoldenExecutionBinding,
    QualifiedApplicationRunPendingError,
    _create_golden_execution_binding,
)
from .run_store import SQLiteRunStore
from .schemas.common import Invocation, RegisteredSkillDescriptor, RunState
from .schemas.text2env import Text2EnvCompileInput


@dataclass(frozen=True)
class _Text2EnvCompileApplicationAdapter:
    application: CompileApplication
    policy: Text2EnvCompileEvidencePolicy = Text2EnvCompileEvidencePolicy()
    skill_ref: str = "text2env.compile@1.0.0"

    def __post_init__(self) -> None:
        if type(self.application) is not CompileApplication:
            raise TypeError("application must be the exact CompileApplication")

    @property
    def artifact_root(self) -> Path:
        return self.application.artifact_root

    @property
    def authority_path(self) -> Path:
        return self.application.journal_path

    @property
    def skills(self) -> tuple[RegisteredSkillDescriptor, ...]:
        return self.application.skills

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


def create_text2env_compile_execution_binding(
    *,
    application: CompileApplication | None = None,
    child_authority: SQLiteRunStore | None = None,
) -> GoldenExecutionBinding:
    """Bind the exact compile policy to its production application and child authority."""

    if application is not None and type(application) is not CompileApplication:
        raise TypeError("application must be the exact CompileApplication")
    if child_authority is not None and type(child_authority) is not SQLiteRunStore:
        raise TypeError("child_authority must be the exact SQLiteRunStore")
    policy = Text2EnvCompileEvidencePolicy()
    adapter = (
        None
        if application is None
        else _Text2EnvCompileApplicationAdapter(application=application, policy=policy)
    )
    return _create_golden_execution_binding(
        policy=policy,
        application=adapter,
        child_authority=child_authority,
    )


__all__ = ["create_text2env_compile_execution_binding"]
