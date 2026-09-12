"""Canonical typed interfaces. Validate at ingress, not at each internal handoff."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Source = Literal["local", "web", "reconstruction"]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Name = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z][\w-]*$")]
Vector3 = Annotated[tuple[float | None, ...], Field(min_length=3, max_length=3)]
PositiveVector3 = Annotated[
    tuple[Annotated[float, Field(gt=0)] | None, ...], Field(min_length=3, max_length=3)
]


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


class WorkflowHandle(Model):
    workflow_id: str = Field(min_length=1)


class OperationRecord(Model):
    operation_id: str
    capability: str
    version: str
    status: Literal["running", "succeeded", "failed", "blocked", "cancelled"]
    started_at: str
    ended_at: str | None = None
    result: ToolResult | None = None


class WorkflowSnapshot(WorkflowHandle):
    status: Literal["active", "succeeded", "failed", "blocked", "cancelled"]
    revision: int = Field(ge=0)
    request: X2EnvRequest
    operations: tuple[OperationRecord, ...] = ()
    owner: str | None = None
    stop_reason: str | None = None
    required_resources: tuple[str, ...] = ()
    input_bundle: ArtifactRef | None = None
    proposal: ArtifactRef | None = None
    scene_ir: ArtifactRef | None = None
    pending_scene_ir: ArtifactRef | None = None
    grounding: ArtifactRef | None = None
    asset_resolution: ArtifactRef | None = None
    resolved_assets: ArtifactRef | None = None
    compiled_scene: ArtifactRef | None = None
    replay_result: ArtifactRef | None = None
    observation: ArtifactRef | None = None
    diagnosis: ArtifactRef | None = None
    validation: ArtifactRef | None = None
    environment_package: ArtifactRef | None = None
    revisions: tuple[ArtifactRef, ...] = ()


class FieldProvenance(Model):
    source: Literal["text", "image", "video"]
    input_sha256: Sha256
    kind: Literal["explicit", "inferred", "override"]
    media_index: int | None = Field(default=None, ge=0)
    frame_index: int | None = Field(default=None, ge=0)
    note: str = Field(default="", max_length=2048)


class Pose(Model):
    frame: Name = Field(
        description="Exactly 'world' or another entity's id; never invent frame aliases. "
        "Structural supports use world."
    )
    position: Vector3 = Field(
        description="Intent x/y/z in metres in the declared frame. Use null for each unknown axis, "
        "including unresolved support height; do not invent coordinates."
    )
    yaw_degrees: float | None = Field(ge=-180, le=180)


class JointPosition(Model):
    name: str = Field(min_length=1)
    value: float


class ArticulationState(Model):
    state: Literal["open", "closed", "specified"]
    joint_positions: tuple[JointPosition, ...] = ()


EvidenceList = Annotated[tuple[FieldProvenance, ...], Field(min_length=1)]


class EntityProvenance(Model):
    category: EvidenceList
    color: EvidenceList
    dimensions: EvidenceList
    material: EvidenceList
    pose: EvidenceList
    articulation_state: EvidenceList

    def records(self) -> tuple[FieldProvenance, ...]:
        return tuple(record for name in type(self).model_fields for record in getattr(self, name))


class SceneEntity(Model):
    id: Name
    category: str = Field(
        min_length=1,
        max_length=128,
        description="Foreground object category. For structural_support, exactly table, worktop "
        "or counter; tabletop is not a structural category.",
    )
    role: Literal["foreground", "structural_support"] = "foreground"
    color: str | None
    dimensions: PositiveVector3 | None = Field(
        description="Intent x/y/z dimensions in metres. Preserve known axes and use null "
        "for unknown "
        "axes (including unknown support thickness). Entirely unknown dimensions may be null. "
        "Asset resolution must resolve dimensions before compilation."
    )
    material: str | None
    pose: Pose
    articulation_state: ArticulationState | None
    provenance: EntityProvenance

    @model_validator(mode="after")
    def field_evidence(self):
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


class ImageInputEvidence(Model):
    source: ArtifactRef
    canonical: ArtifactRef
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    mode: Literal["RGB", "RGBA"]
    decoder_version: str = Field(min_length=1)


class VideoFrameEvidence(Model):
    index: int = Field(ge=0)
    pts: int
    duration: int = Field(gt=0)
    sha256: Sha256
    size_bytes: int = Field(gt=0)


class VideoInputEvidence(Model):
    source: ArtifactRef
    probe: ArtifactRef
    sequence: ArtifactRef
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    codec: str = Field(min_length=1)
    pixel_format: Literal["rgb24"] = "rgb24"
    fps_num: int = Field(gt=0)
    fps_den: int = Field(gt=0)
    time_base_num: int = Field(gt=0)
    time_base_den: int = Field(gt=0)
    frame_count: int = Field(gt=0)
    unique_frame_count: int = Field(gt=0)
    decoder_version: str = Field(min_length=1)


class InputBundle(Model):
    schema_version: Literal["x2env.input_bundle.v1"] = "x2env.input_bundle.v1"
    request_sha256: Sha256
    text: ArtifactRef | None
    images: tuple[ImageInputEvidence, ...] = Field(default=(), max_length=8)
    video: VideoInputEvidence | None = None
    modality: Literal["text", "image", "video", "multimodal"]
    seed: int = Field(ge=0)
    override_policy: Literal["explicit_text_over_media_with_provenance"] = (
        "explicit_text_over_media_with_provenance"
    )
    resource_limits: dict[str, int]


class UnknownField(Model):
    field: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=2048)
    critical: bool
    reason_kind: Literal[
        "scale_unobservable",
        "pose_unobservable",
        "conflict",
        "unsupported",
        "missing_required_semantics",
        "unspecified",
    ] = "unspecified"
    provenance: tuple[FieldProvenance, ...] = Field(min_length=1)


class SceneIntentProposal(Model):
    schema_version: Literal["x2env.scene_intent_proposal.v2"] = "x2env.scene_intent_proposal.v2"
    scene: SceneIR | None
    unknowns: tuple[UnknownField, ...] = Field(max_length=64)

    @model_validator(mode="after")
    def meaningful_proposal(self):
        if self.scene is None and not self.unknowns:
            raise ValueError("proposal requires a scene or explicit unknowns")
        return self


class BackendProposal(Model):
    status: Literal["completed", "failed", "blocked"]
    proposal: SceneIntentProposal | None
    evidence: tuple[ArtifactRef, ...]
    error_code: str | None
    elapsed_seconds: float = Field(ge=0)
    authority: Literal["advisory_only"] = "advisory_only"

    @model_validator(mode="after")
    def result_matches_status(self):
        if self.status == "completed":
            if self.proposal is None or self.error_code is not None:
                raise ValueError("completed advisory requires a proposal without error")
        elif self.proposal is not None or not self.error_code:
            raise ValueError("unsuccessful advisory requires an error without proposal")
        return self
