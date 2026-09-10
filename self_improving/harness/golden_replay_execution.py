"""Exact production application binding for text2env replay Golden execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel

from .golden_execution import (
    GoldenExecutionBinding,
    QualifiedApplicationRunPendingError,
    _create_golden_execution_binding,
)
from .golden_replay_policy import Text2EnvReplayEvidencePolicy
from .replay_application import ReplayApplication, ReplayApplicationRunPendingError
from .run_store import SQLiteRunStore
from .schemas.common import Invocation, RegisteredSkillDescriptor, RunState
from .schemas.text2env import Text2EnvReplayInput


@dataclass(frozen=True)
class _Text2EnvReplayApplicationAdapter:
    application: ReplayApplication
    policy: Text2EnvReplayEvidencePolicy = Text2EnvReplayEvidencePolicy()
    skill_ref: str = "text2env.replay@1.0.0"

    def __post_init__(self) -> None:
        if type(self.application) is not ReplayApplication:
            raise TypeError("application must be the exact ReplayApplication")
        self.application._assert_golden_execution_ready()

    def _assert_ready(self) -> None:
        self.application._assert_golden_execution_ready()

    @property
    def artifact_root(self) -> Path:
        self._assert_ready()
        return self.application.artifact_root

    @property
    def authority_path(self) -> Path:
        self._assert_ready()
        return self.application.journal_path

    @property
    def skills(self) -> tuple[RegisteredSkillDescriptor, ...]:
        self._assert_ready()
        return self.application.skills

    def invoke(self, parameters: BaseModel, *, run_id: UUID) -> RunState:
        self._assert_ready()
        if type(parameters) is not Text2EnvReplayInput:
            raise TypeError("replay adapter requires Text2EnvReplayInput")
        try:
            return self.application.invoke_typed(parameters, run_id=run_id)
        except ReplayApplicationRunPendingError as error:
            raise QualifiedApplicationRunPendingError(str(error)) from error

    def invocation(self, run_id: UUID) -> Invocation | None:
        self._assert_ready()
        return self.application.invocation(run_id)

    def run_state(self, run_id: UUID) -> RunState | None:
        self._assert_ready()
        return self.application.run_state(run_id)


def create_text2env_replay_execution_binding(
    *,
    application: ReplayApplication | None = None,
    child_authority: SQLiteRunStore | None = None,
) -> GoldenExecutionBinding:
    """Bind the exact replay policy to its production application and child authority."""

    if application is not None and type(application) is not ReplayApplication:
        raise TypeError("application must be the exact ReplayApplication")
    if child_authority is not None and type(child_authority) is not SQLiteRunStore:
        raise TypeError("child_authority must be the exact SQLiteRunStore")
    policy = Text2EnvReplayEvidencePolicy()
    adapter = (
        None
        if application is None
        else _Text2EnvReplayApplicationAdapter(application=application, policy=policy)
    )
    return _create_golden_execution_binding(
        policy=policy,
        application=adapter,
        child_authority=child_authority,
    )


__all__ = ["create_text2env_replay_execution_binding"]
