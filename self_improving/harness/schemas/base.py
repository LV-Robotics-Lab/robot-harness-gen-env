"""Shared primitives for the public Harness MVP schemas."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

SKILL_ID_PATTERN = r"^[a-z][a-z0-9_]{0,31}\.[a-z][a-z0-9_]{0,31}$"
SEMVER_PATTERN = r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$"
SKILL_REF_PATTERN = (
    r"^[a-z][a-z0-9_]{0,31}\.[a-z][a-z0-9_]{0,31}"
    r"@(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$"
)
SCHEMA_ID_PATTERN = (
    r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+\.v(?:0|[1-9][0-9]*)$"
)
SHA256_PATTERN = r"^[0-9a-f]{64}$"
BLOCKER_CODE_PATTERN = r"^[A-Z][A-Z0-9_]{1,63}$"
MCP_TOOL_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,127}$"

NonEmptyString = Annotated[str, Field(strict=True, min_length=1, max_length=4096)]
ShortString = Annotated[str, Field(strict=True, min_length=1, max_length=255)]
SkillId = Annotated[str, Field(strict=True, pattern=SKILL_ID_PATTERN)]
StableSemVer = Annotated[str, Field(strict=True, pattern=SEMVER_PATTERN)]
CanonicalSkillRef = Annotated[str, Field(strict=True, pattern=SKILL_REF_PATTERN)]
SchemaId = Annotated[str, Field(strict=True, pattern=SCHEMA_ID_PATTERN)]
Sha256 = Annotated[str, Field(strict=True, pattern=SHA256_PATTERN)]
BlockerCode = Annotated[str, Field(strict=True, pattern=BLOCKER_CODE_PATTERN)]
McpToolName = Annotated[str, Field(strict=True, pattern=MCP_TOOL_NAME_PATTERN)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]
Seed = Annotated[int, Field(strict=True, ge=0, le=2_147_483_647)]
JsonObject = dict[str, JsonValue]


class HarnessModel(BaseModel):
    """Strict, immutable base for Harness-owned records."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        protected_namespaces=(),
    )


def public_schema_config(schema_id: str) -> ConfigDict:
    """Return the common model configuration for a public schema document."""

    return ConfigDict(
        extra="forbid",
        frozen=True,
        protected_namespaces=(),
        json_schema_extra={"$id": schema_id, "$schema": JSON_SCHEMA_DIALECT},
    )


def derive_mcp_tool_name(skill_id: str, version: str) -> str:
    """Mechanically derive the MCP tool name required by the contract."""

    return f"{skill_id.replace('.', '_')}_v{version.replace('.', '_')}"
