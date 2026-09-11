"""Strict client-facing projections; all authority remains in Golden snapshots."""

from typing import Literal

from pydantic import UUID4, AwareDatetime, Field, model_validator

from .base import HarnessModel, NonEmptyString, NonNegativeInt
from .common import ArtifactRef
from .workflow import OperationSnapshot, RunSnapshot


class PreviewResourceLink(HarnessModel):
    uri: NonEmptyString
    name: NonEmptyString
    mime_type: NonEmptyString


class PreviewAsset(HarnessModel):
    asset_id: NonEmptyString
    label: Literal["mouse"]
    representation: ArtifactRef
    qualification: ArtifactRef


class PreviewObservedMedia(HarnessModel):
    receipt: ArtifactRef
    image: ArtifactRef
    captured_at: AwareDatetime
    expires_at: AwareDatetime
    authority: Literal["observed_restored_final_pose", "observed_replay_final_frame"]


class PreviewServiceResult(HarnessModel):
    workflow_id: UUID4
    revision: NonNegativeInt
    stage: Literal[
        "start",
        "asset_lookup",
        "compile",
        "replay",
        "observe",
        "validate",
        "publish",
        "status",
        "package_get",
    ]
    status: Literal["active", "running", "succeeded", "failed", "waiting_input", "published"]
    operation: OperationSnapshot | None = None
    snapshot: RunSnapshot | None = None
    source_prompt: ArtifactRef | None = None
    failure_receipts: tuple[ArtifactRef, ...] = ()
    asset: PreviewAsset | None = None
    observation: PreviewObservedMedia | None = None
    package: ArtifactRef | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    resource_links: tuple[PreviewResourceLink, ...] = ()
    failure_code: NonEmptyString | None = None
    failure_message: NonEmptyString | None = None
    elapsed_seconds: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    ttl_remaining_seconds: float | None = Field(default=None, allow_inf_nan=False)
    library_publish_ns: NonNegativeInt | None = None

    @model_validator(mode="after")
    def failure_is_visible(self):
        if self.stage in {"start", "status"} and self.snapshot is None:
            raise ValueError("start and status must expose the actual Golden snapshot")
        if self.snapshot is not None and (
            self.snapshot.workflow_run_id != self.workflow_id
            or self.snapshot.revision != self.revision
        ):
            raise ValueError("projection differs from its actual workflow snapshot")
        if (self.failure_code is None) != (self.failure_message is None):
            raise ValueError("failure code and message must be paired")
        if self.status == "failed" and self.failure_code is None:
            raise ValueError("failed result must expose its concrete cause")
        if self.failure_code is not None and self.status not in {"failed", "waiting_input"}:
            raise ValueError("successful result cannot carry failure metadata")
        return self
