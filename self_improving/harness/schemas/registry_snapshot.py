"""Portable, immutable snapshot of exact qualified Skills."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import model_validator

from .base import CanonicalSkillRef, HarnessModel, McpToolName, public_schema_config
from .common import ArtifactRef

REGISTRY_SNAPSHOT_SCHEMA_ID = "harness.registry_snapshot.v1"

_CAS_URI = re.compile(r"^artifact://sha256/([0-9a-f]{64})$")
_DESCRIPTOR_SCHEMAS = frozenset(
    {
        "harness.skill_descriptor.v1",
        "harness.skill_descriptor.v2",
    }
)


class QualifiedSkillSnapshot(HarnessModel):
    """Exact portable evidence closure for one Registry entry."""

    skill_ref: CanonicalSkillRef
    mcp_tool_name: McpToolName
    descriptor_ref: ArtifactRef
    qualification_ref: ArtifactRef
    qualification_report_ref: ArtifactRef

    @model_validator(mode="after")
    def refs_are_content_addressed_and_typed(self) -> "QualifiedSkillSnapshot":
        _require_json_cas(self.descriptor_ref, label="descriptor_ref")
        if self.descriptor_ref.schema_version not in _DESCRIPTOR_SCHEMAS:
            raise ValueError("descriptor_ref must identify a supported Skill descriptor schema")
        _require_json_cas(
            self.qualification_ref,
            label="qualification_ref",
            schema_version="harness.skill_qualification.v1",
        )
        _require_json_cas(
            self.qualification_report_ref,
            label="qualification_report_ref",
            schema_version="harness.skill_qualification_report.v1",
        )
        return self


class RegistrySnapshot(HarnessModel):
    """Frozen exact Skill catalog consumed by a Golden Workflow and MCP."""

    model_config = public_schema_config(REGISTRY_SNAPSHOT_SCHEMA_ID)

    schema_version: Literal["harness.registry_snapshot.v1"]
    qualified_skills: tuple[QualifiedSkillSnapshot, ...]

    @model_validator(mode="after")
    def snapshot_is_canonical(self) -> "RegistrySnapshot":
        tool_names = [entry.mcp_tool_name for entry in self.qualified_skills]
        if tool_names != sorted(tool_names) or len(tool_names) != len(set(tool_names)):
            raise ValueError("qualified skills must be sorted and unique by mcp_tool_name")
        skill_refs = [entry.skill_ref for entry in self.qualified_skills]
        if len(skill_refs) != len(set(skill_refs)):
            raise ValueError("qualified skill_ref values must be unique")
        return self


def _require_json_cas(
    artifact: ArtifactRef,
    *,
    label: str,
    schema_version: str | None = None,
) -> None:
    match = _CAS_URI.fullmatch(artifact.uri)
    if match is None or match.group(1) != artifact.sha256:
        raise ValueError(f"{label} must use its content-addressed artifact URI")
    if artifact.media_type != "application/json":
        raise ValueError(f"{label} must use media_type='application/json'")
    if schema_version is not None and artifact.schema_version != schema_version:
        raise ValueError(f"{label} must have schema_version={schema_version}")


__all__ = [
    "REGISTRY_SNAPSHOT_SCHEMA_ID",
    "QualifiedSkillSnapshot",
    "RegistrySnapshot",
]
