"""Strict lineage records distinguishing initial and prompt-revision compile attempts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .base import HarnessModel, public_schema_config
from .common import ArtifactRef

INITIAL_COMPILE_ATTEMPT_SCHEMA_ID = "harness.initial_compile_attempt.v1"
PROMPT_REVISION_COMPILE_ATTEMPT_SCHEMA_ID = (
    "harness.prompt_revision_compile_attempt.v1"
)


def _artifact_identity(ref: ArtifactRef) -> tuple[str, str, str]:
    return (ref.sha256, ref.schema_version or "", ref.media_type)


def _require_ref(
    ref: ArtifactRef,
    *,
    label: str,
    schema_version: str,
    media_type: str = "application/json",
) -> None:
    if ref.uri != f"artifact://sha256/{ref.sha256}":
        raise ValueError(f"{label}.uri must be content-addressed by its sha256")
    if ref.schema_version != schema_version or ref.media_type != media_type:
        raise ValueError(
            f"{label} must use schema_version {schema_version!r} and media type {media_type!r}"
        )


class InitialCompileAttemptV1(HarnessModel):
    """An initial compile with no retry or repair lineage."""

    model_config = public_schema_config(INITIAL_COMPILE_ATTEMPT_SCHEMA_ID)

    schema_version: Literal[INITIAL_COMPILE_ATTEMPT_SCHEMA_ID]
    kind: Literal["initial"]


class PromptRevisionCompileAttemptV1(HarnessModel):
    """A compile that must consume one immutable prompt-revision control receipt."""

    model_config = public_schema_config(PROMPT_REVISION_COMPILE_ATTEMPT_SCHEMA_ID)

    schema_version: Literal[PROMPT_REVISION_COMPILE_ATTEMPT_SCHEMA_ID]
    kind: Literal["prompt_revision"]
    prompt_revision: ArtifactRef
    effective_prompt: ArtifactRef
    failure_receipts: tuple[ArtifactRef, ...] = Field(min_length=1)
    predecessor_package: ArtifactRef

    @model_validator(mode="after")
    def lineage_refs_are_canonical(self) -> "PromptRevisionCompileAttemptV1":
        _require_ref(
            self.prompt_revision,
            label="prompt_revision",
            schema_version="harness.prompt_revision.v1",
        )
        _require_ref(
            self.effective_prompt,
            label="effective_prompt",
            schema_version="harness.prompt_text.v1",
            media_type="text/plain",
        )
        _require_ref(
            self.predecessor_package,
            label="predecessor_package",
            schema_version="harness.x2env_environment_package.v1",
        )
        identities = tuple(_artifact_identity(ref) for ref in self.failure_receipts)
        if identities != tuple(sorted(identities)) or len(identities) != len(set(identities)):
            raise ValueError("failure_receipts must be sorted and unique")
        for index, receipt in enumerate(self.failure_receipts):
            _require_ref(
                receipt,
                label=f"failure_receipts[{index}]",
                schema_version="harness.workflow_operation_receipt.v2",
            )
        return self


CompileAttemptV1 = Annotated[
    InitialCompileAttemptV1 | PromptRevisionCompileAttemptV1,
    Field(discriminator="schema_version"),
]


__all__ = [
    "INITIAL_COMPILE_ATTEMPT_SCHEMA_ID",
    "PROMPT_REVISION_COMPILE_ATTEMPT_SCHEMA_ID",
    "CompileAttemptV1",
    "InitialCompileAttemptV1",
    "PromptRevisionCompileAttemptV1",
]
