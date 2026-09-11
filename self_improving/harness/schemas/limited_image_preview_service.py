"""Strict client-facing projections; all authority remains in Golden snapshots."""

from typing import Literal

from pydantic import UUID4, AwareDatetime

from .base import HarnessModel, NonEmptyString, NonNegativeInt
from .common import ArtifactRef
from .workflow import OperationSnapshot


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
    asset: PreviewAsset | None = None
    observation: PreviewObservedMedia | None = None
    package: ArtifactRef | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    resource_links: tuple[PreviewResourceLink, ...] = ()
