"""Exact public-schema closure required by ``text2env.replay@1.0.0``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .schema_catalog import _make_schema_document_standalone, render_schema_document
from .schemas.base import HarnessModel
from .schemas.common import (
    ArtifactRef,
    Blocker,
    Event,
    Invocation,
    RunState,
    SkillDescriptor,
    SkillDescriptorV2,
    SkillQualification,
)
from .schemas.qualification_report import PublicQualificationReportV1
from .schemas.text2env import (
    EnvironmentPackage,
    Text2EnvCompileInput,
    Text2EnvCompileOutput,
    Text2EnvReplayInput,
    Text2EnvReplayOutput,
)


@dataclass(frozen=True, slots=True)
class ReplaySchemaBinding:
    schema_id: str
    model: type[HarnessModel]

    @property
    def snapshot_path(self) -> str:
        return f"self_improving/harness/json_schemas/{self.schema_id}.schema.json"


REPLAY_SCHEMA_BINDINGS: tuple[ReplaySchemaBinding, ...] = (
    ReplaySchemaBinding("harness.artifact_ref.v1", ArtifactRef),
    ReplaySchemaBinding("harness.blocker.v1", Blocker),
    ReplaySchemaBinding("harness.environment_package.v1", EnvironmentPackage),
    ReplaySchemaBinding("harness.event.v1", Event),
    ReplaySchemaBinding("harness.run_state.v1", RunState),
    ReplaySchemaBinding("harness.skill_descriptor.v1", SkillDescriptor),
    ReplaySchemaBinding("harness.skill_descriptor.v2", SkillDescriptorV2),
    ReplaySchemaBinding("harness.skill_invocation.v1", Invocation),
    ReplaySchemaBinding("harness.skill_qualification.v1", SkillQualification),
    ReplaySchemaBinding(
        "harness.skill_qualification_report.v1",
        PublicQualificationReportV1,
    ),
    ReplaySchemaBinding("harness.text2env_compile_input.v1", Text2EnvCompileInput),
    ReplaySchemaBinding("harness.text2env_compile_output.v1", Text2EnvCompileOutput),
    ReplaySchemaBinding("harness.text2env_replay_input.v1", Text2EnvReplayInput),
    ReplaySchemaBinding("harness.text2env_replay_output.v1", Text2EnvReplayOutput),
)
REPLAY_SCHEMA_SNAPSHOT_PATHS = tuple(binding.snapshot_path for binding in REPLAY_SCHEMA_BINDINGS)


class ReplaySchemaContractError(RuntimeError):
    """A replay-bound model and its committed public schema are not exact peers."""


def verify_replay_schema_contract(implementation_root: Path) -> None:
    """Require exact model-to-snapshot bytes for every replay-bound public schema."""

    root = Path(implementation_root)
    for binding in REPLAY_SCHEMA_BINDINGS:
        expected = render_schema_document(
            _make_schema_document_standalone(
                binding.model.model_json_schema(mode="validation")
            )
        ).encode()
        path = root / binding.snapshot_path
        try:
            actual = path.read_bytes()
        except OSError as error:
            raise ReplaySchemaContractError(
                f"replay schema snapshot is unavailable: {binding.schema_id}"
            ) from error
        if actual != expected:
            raise ReplaySchemaContractError(
                f"replay schema snapshot differs from its model: {binding.schema_id}"
            )
