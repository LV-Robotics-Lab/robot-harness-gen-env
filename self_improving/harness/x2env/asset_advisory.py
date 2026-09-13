"""Typed visual evidence, distinct from acquisition and physical validation."""

from typing import Literal

from pydantic import Field

from .contracts import ArtifactRef, Model


class VisualCandidate(Model):
    candidate_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    preview: ArtifactRef
    aliases: tuple[str, ...] = ()
    want_color: str | None = None
    want_material: str | None = None


class VisualAnswer(Model):
    object: str | None
    match: bool | None
    colors: tuple[str, ...]
    materials: tuple[str, ...]
    confidence: float | None = Field(ge=0, le=1)
    same_kind: bool | None
    plausible: bool | None
    suggests: str | None


class VisualVerdict(Model):
    candidate_id: str
    preview: ArtifactRef
    verdict: Literal["match", "mismatch", "unreadable", "no_thumbnail"]
    detail: ArtifactRef


class AssetVisualAssessment(Model):
    status: Literal["completed", "failed", "blocked"]
    verdicts: tuple[VisualVerdict, ...]
    receipt: ArtifactRef
    evidence: tuple[ArtifactRef, ...]
    error_code: str | None = None
    authority: Literal["advisory_only"] = "advisory_only"
    physical_evaluated: Literal[False] = False
