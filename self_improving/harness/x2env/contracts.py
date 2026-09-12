"""Canonical typed interfaces. Validate at ingress, not at each internal handoff."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Source = Literal["local", "web", "reconstruction"]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Name = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z][\w-]*$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class ArtifactRef(Model):
    sha256: Sha256
    size_bytes: int = Field(ge=0)
    media_type: str = Field(min_length=1)


class ToolResult(Model):
    operation_id: str = Field(min_length=1)
    status: Literal["succeeded", "failed", "blocked", "cancelled"]
    outputs: tuple[ArtifactRef, ...] = ()
    error_code: str | None = Field(default=None, min_length=1)
    message: str = ""

    @model_validator(mode="after")
    def error_matches_status(self):
        if (self.status == "succeeded") != (self.error_code is None):
            raise ValueError("error code must match tool status")
        return self


class InputMedia(Model):
    path: str = Field(min_length=1)

    @model_validator(mode="after")
    def absolute_path(self):
        if not Path(self.path).is_absolute():
            raise ValueError("media path must be absolute")
        return self


class RequestConstraints(Model):
    allow_cousin: bool = False


class X2EnvRequest(Model):
    text: str | None = Field(default=None, max_length=32768)
    images: tuple[InputMedia, ...] = Field(default=(), max_length=8)
    video: InputMedia | None = None
    seed: int = Field(ge=0, le=2147483647)
    allowed_sources: tuple[Source, ...] = Field(
        default=("local", "web", "reconstruction"), min_length=1, max_length=3
    )
    constraints: RequestConstraints = RequestConstraints()
    idempotency_key: str = Field(min_length=1, max_length=255)
    output_dir: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid_request(self):
        if not ((self.text is not None and self.text.strip()) or self.images or self.video):
            raise ValueError("request must contain text, image or video")
        if not Path(self.output_dir).is_absolute():
            raise ValueError("output_dir must be absolute")
        if len(set(self.allowed_sources)) != len(self.allowed_sources):
            raise ValueError("duplicate source constraint")
        return self


class FieldProvenance(Model):
    source: Literal["text", "image", "video"]
    input_sha256: Sha256
    kind: Literal["explicit", "inferred", "override"]
    media_index: int | None = Field(default=None, ge=0)
    frame_index: int | None = Field(default=None, ge=0)
    note: str = Field(default="", max_length=2048)


class Pose(Model):
    frame: Name
    position: tuple[float, float, float]
    yaw_degrees: float = Field(ge=-180, le=180)


class ArticulationState(Model):
    state: Literal["open", "closed", "specified"]
    joint_positions: dict[str, float] = Field(default_factory=dict)


class SceneEntity(Model):
    id: Name
    category: str = Field(min_length=1, max_length=128)
    role: Literal["foreground", "structural_support"] = "foreground"
    color: str | None
    dimensions: (
        tuple[
            Annotated[float, Field(gt=0)],
            Annotated[float, Field(gt=0)],
            Annotated[float, Field(gt=0)],
        ]
        | None
    )
    material: str | None
    pose: Pose
    articulation_state: ArticulationState | None
    provenance: dict[str, tuple[FieldProvenance, ...]]

    @model_validator(mode="after")
    def field_evidence(self):
        fields = {"category", "color", "dimensions", "material", "pose", "articulation_state"}
        if set(self.provenance) != fields or any(not v for v in self.provenance.values()):
            raise ValueError("every semantic field requires provenance")
        if self.role == "structural_support" and self.category not in {
            "table",
            "worktop",
            "counter",
        }:
            raise ValueError("only table/worktop/counter may use structural geometry")
        return self


class SceneRelation(Model):
    source: Name
    relation: Literal["on", "inside", "left_of", "right_of", "front_of", "behind", "near"]
    target: Name
    distance: Annotated[float, Field(ge=0)] | None = None
    provenance: tuple[FieldProvenance, ...] = Field(min_length=1)


class SceneIR(Model):
    revision: int = Field(ge=0)
    input_sha256: Sha256
    entities: tuple[SceneEntity, ...] = Field(min_length=1, max_length=8)
    relations: tuple[SceneRelation, ...] = ()

    @model_validator(mode="after")
    def entity_graph(self):
        ids = {e.id for e in self.entities}
        if len(ids) != len(self.entities) or "world" in ids:
            raise ValueError("entity IDs must be unique and must not be world")
        edges = {name: set() for name in ids}
        for entity in self.entities:
            frame = entity.pose.frame
            if frame != "world":
                if frame not in ids or frame == entity.id:
                    raise ValueError("invalid pose reference")
                edges[entity.id].add(frame)
        for relation in self.relations:
            if relation.source not in ids or relation.target not in ids:
                raise ValueError("relation references unknown entity")
            if relation.source == relation.target:
                raise ValueError("self relation is invalid")
            if relation.relation in {"on", "inside"}:
                edges[relation.source].add(relation.target)

        def visit(node, ancestors):
            if node in ancestors:
                raise ValueError("cyclic support/pose graph")
            for target in edges[node]:
                visit(target, ancestors | {node})

        for node in ids:
            visit(node, set())
        return self
