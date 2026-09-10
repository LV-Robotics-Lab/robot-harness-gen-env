"""Fail-closed authority checks for one prompt-revision compile attempt."""

from __future__ import annotations

from pydantic import BaseModel

from .schemas.common import ArtifactRef
from .schemas.compile_attempt import PromptRevisionCompileAttemptV1
from .schemas.golden_control import PromptRevision, VideoFrameToImageFallback
from .schemas.workflow import RunSnapshot
from .schemas.x2env import Image2EnvCompileV2Input, Video2EnvCompileV2Input


def verify_prompt_revision_compile_consumption(
    *,
    prompt_revision_ref: ArtifactRef,
    prompt_revision: PromptRevision,
    snapshot: RunSnapshot,
    skill_ref: str,
    parameters: BaseModel,
) -> None:
    """Require a supported compile input to consume the exact current control receipt."""

    if prompt_revision_ref != snapshot.receipt_head:
        raise ValueError("prompt revision is not the current workflow head")
    if (
        prompt_revision.workflow_run_id != snapshot.workflow_run_id
        or prompt_revision.principal_id != snapshot.principal_id
        or prompt_revision.committed_revision != snapshot.revision
        or prompt_revision.resulting_state != snapshot.state_ref
        or prompt_revision.resulting_state_sha256 != snapshot.state_sha256
    ):
        raise ValueError("prompt revision differs from the current workflow authority")
    if prompt_revision.next_compile_skill_ref != skill_ref:
        raise ValueError("prompt revision targets a different exact compile Skill")
    if type(parameters) not in {Image2EnvCompileV2Input, Video2EnvCompileV2Input}:
        raise ValueError("compile input does not carry supported prompt-revision lineage")

    attempt = parameters.attempt
    if type(attempt) is not PromptRevisionCompileAttemptV1:
        raise ValueError("compile input does not carry prompt-revision lineage")
    if attempt.prompt_revision != prompt_revision_ref:
        raise ValueError("compile attempt cites a different prompt revision")
    if attempt.effective_prompt != prompt_revision.revised_prompt:
        raise ValueError("compile attempt does not use the exact revised prompt")
    if attempt.failure_receipts != prompt_revision.failure_receipts:
        raise ValueError("compile attempt failure lineage differs from the prompt revision")
    if attempt.predecessor_package != prompt_revision.predecessor_package:
        raise ValueError("compile attempt predecessor package differs from the prompt revision")
    if parameters.predecessor_package != attempt.predecessor_package:
        raise ValueError("compile input and attempt disagree on the predecessor package")

    if isinstance(prompt_revision.fallback, VideoFrameToImageFallback):
        if type(parameters) is not Image2EnvCompileV2Input:
            raise ValueError("video-frame fallback must be consumed by image2env compile")
        selected_frame = prompt_revision.fallback.selected_frame
        source_media = parameters.source_media
        if (
            source_media.source != selected_frame
            or source_media.canonical_image != selected_frame
            or source_media.source_media_sha256 != selected_frame.sha256
        ):
            raise ValueError("image compile source is not the selected fallback frame")


__all__ = ["verify_prompt_revision_compile_consumption"]
