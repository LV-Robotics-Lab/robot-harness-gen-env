"""Explicit native revision contracts; declarations confer no execution authority.

Consumers must authenticate workflow ownership and remeasure the complete footprint.
The bounded XY check here is only an input-domain constraint, not support evidence.
Text/payload hashes prove consumption identity, not natural-language interpretation.
"""

import hashlib
import json
import math
from typing import Annotated, Literal

from pydantic import UUID4, Field, model_validator

from .base import (
    HarnessModel,
    NonEmptyString,
    NonNegativeInt,
    PositiveInt,
    Sha256,
    public_schema_config,
)
from .common import ArtifactRef
from .golden_control import (
    ExactFalse,
    NoPromptFallback,
    ReplacePromptPatch,
    _ControlCommand,
    _ControlReceipt,
    _require_sorted_refs,
)
from .golden_control_v2 import ObservationCitationV2, _citations, _control, _digest
from .workflow import PrincipalId
from .x2env import ImageSourceMedia
from .x2env_compile_evidence import SceneInterpretationProposalV1

Coordinate = Annotated[float, Field(strict=True, allow_inf_nan=False, ge=-10, le=10)]
Extent = Annotated[float, Field(strict=True, allow_inf_nan=False, gt=0.02, le=10)]


def _ref(value, schema, media="application/json"):
    if (
        value.schema_version != schema
        or value.media_type != media
        or value.uri != "artifact://sha256/" + value.sha256
    ):
        raise ValueError("reference must identify the exact canonical CAS type")


def _text(value, text):
    _ref(value, "harness.prompt_text.v1", "text/plain")
    raw = text.encode("utf-8")
    if value.sha256 != hashlib.sha256(raw).hexdigest() or value.bytes != len(raw):
        raise ValueError("effective prompt must consume exact replacement UTF-8 bytes")


class TabletopLayoutRevisionV1(HarnessModel):
    """Absolute world XY and Z-axis yaw for an upright source, in metres/radians.

    A consumer must verify the original source is upright and preserve its qualified
    mass/friction/inertia. Table friction is inherited from the native predecessor.
    Coordinates are bounded to +/-10 m; table extents are (0.02, 10] m. These bounds
    and the centre margin do not replace measured all-vertex footprint validation.
    """

    model_config = public_schema_config("harness.tabletop_layout_revision.v1")
    schema_version: Literal["harness.tabletop_layout_revision.v1"]
    workflow_run_id: UUID4
    principal_id: PrincipalId
    base_revision: NonNegativeInt
    state_sha256: Sha256
    receipt_head_sha256: Sha256
    original_source: ArtifactRef
    source_object_id: NonEmptyString
    replacement_text: NonEmptyString
    effective_prompt: ArtifactRef
    position_xy_m: tuple[Coordinate, Coordinate]
    yaw_rad: Annotated[float, Field(strict=True, allow_inf_nan=False, ge=-math.pi, le=math.pi)]
    table_center_m: tuple[Coordinate, Coordinate, Coordinate]
    table_size_m: tuple[Extent, Extent, Extent]
    relation: Literal["on_table"]
    physical_properties: Literal["preserve_qualified_asset"]

    @model_validator(mode="after")
    def bounded_layout(self):
        _ref(self.original_source, None)
        _text(self.effective_prompt, self.replacement_text)
        if self.table_center_m[2] - self.table_size_m[2] / 2 < 0:
            raise ValueError("finite table bottom must remain above horizontal ground")
        if any(
            abs(self.position_xy_m[i] - self.table_center_m[i]) > self.table_size_m[i] / 2 - 0.01
            for i in (0, 1)
        ):
            raise ValueError("object XY must be inside the finite table input bounds")
        return self


class _RevisionBodyV3(HarnessModel):
    source_prompt: ArtifactRef
    advisory_receipt: ArtifactRef
    failure_receipts: tuple[ArtifactRef, ...] = Field(min_length=1)
    predecessor_environment: ArtifactRef
    patch: ReplacePromptPatch
    fallback: NoPromptFallback
    next_compile_skill_ref: Literal["image2env.compile@3.0.0"]
    external_agent_executed: ExactFalse
    layout_proposal: TabletopLayoutRevisionV1

    @model_validator(mode="after")
    def revision_inputs(self):
        _ref(self.source_prompt, "harness.prompt_text.v1", "text/plain")
        _ref(self.advisory_receipt, "harness.external_agent_advisory_receipt.v2")
        if self.predecessor_environment.schema_version not in {
            "harness.image2env_genesis_compile_receipt.v1",
            "harness.image2env_genesis_compile_receipt.v2",
            "harness.genesis_scene_execution_receipt.v2",
        }:
            raise ValueError("revision requires a native environment predecessor")
        _ref(self.predecessor_environment, self.predecessor_environment.schema_version)
        _require_sorted_refs(
            self.failure_receipts,
            label="failure_receipts",
            schema="harness.workflow_operation_receipt.v2",
        )
        if (
            self.patch.base_text_sha256 != self.source_prompt.sha256
            or self.patch.replacement_text != self.layout_proposal.replacement_text
        ):
            raise ValueError("layout proposal must consume the exact prompt patch")
        return self


def _context(layout, workflow, principal, revision, state, head):
    if (
        layout.workflow_run_id,
        layout.principal_id,
        layout.base_revision,
        layout.state_sha256,
        layout.receipt_head_sha256,
    ) != (workflow, principal, revision, state, head):
        raise ValueError("layout proposal differs from declared workflow context")


class PromptRevisionCommandV3(_RevisionBodyV3, _ControlCommand):
    model_config = public_schema_config("harness.prompt_revision_command.v3")
    schema_version: Literal["harness.prompt_revision_command.v3"]

    @model_validator(mode="after")
    def command_context(self):
        _context(
            self.layout_proposal,
            self.workflow_run_id,
            self.principal_id,
            self.expected_workflow_revision,
            self.expected_state_sha256,
            self.expected_receipt_head_sha256,
        )
        if self.expected_receipt_head_sha256 != self.advisory_receipt.sha256:
            raise ValueError("revision must immediately follow the declared advisory")
        return self


def prompt_revision_v3_sha256(value):
    return _digest(value, "revision_sha256", "harness.prompt_revision.identity.v3", "revision")


class PromptRevisionV3(_RevisionBodyV3, _ControlReceipt):
    model_config = public_schema_config("harness.prompt_revision.v3")
    schema_version: Literal["harness.prompt_revision.v3"]
    prompt_revision_number: PositiveInt
    predecessor_prompt_revision: ArtifactRef | None
    observation_citations: tuple[ObservationCitationV2, ...]
    revised_prompt: ArtifactRef
    authority: Literal["advisory_only"]
    grants_physical_validity: ExactFalse
    grants_publishability: ExactFalse
    revision_sha256: Sha256

    @model_validator(mode="after")
    def revision_context(self):
        _control(self, "harness.prompt_revision_command.v3")
        _context(
            self.layout_proposal,
            self.workflow_run_id,
            self.principal_id,
            self.base_revision,
            self.base_state_sha256,
            self.predecessor_receipt.sha256,
        )
        _text(self.revised_prompt, self.patch.replacement_text)
        if self.predecessor_receipt != self.advisory_receipt:
            raise ValueError("revision must immediately follow its advisory")
        if (self.prompt_revision_number == 1) != (self.predecessor_prompt_revision is None):
            raise ValueError("revision number requires the matching predecessor presence")
        if self.predecessor_prompt_revision is not None:
            _ref(self.predecessor_prompt_revision, "harness.prompt_revision.v3")
        _citations(self.observation_citations, self.committed_at)
        if self.revision_sha256 != prompt_revision_v3_sha256(self):
            raise ValueError("revision identity mismatch")
        return self


def _payload(ref, model):
    _ref(ref, model.schema_version)
    raw = json.dumps(
        model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    if not any(
        ref.sha256 == hashlib.sha256(data).hexdigest() and ref.bytes == len(data)
        for data in (raw, raw + b"\n")
    ):
        raise ValueError("typed payload must equal its exact canonical CAS bytes")


class PromptRevisionCompileAttemptV2(HarnessModel):
    model_config = public_schema_config("harness.prompt_revision_compile_attempt.v2")
    schema_version: Literal["harness.prompt_revision_compile_attempt.v2"]
    kind: Literal["prompt_revision"]
    prompt_revision: ArtifactRef
    revision: PromptRevisionV3
    effective_prompt: ArtifactRef
    failure_receipts: tuple[ArtifactRef, ...] = Field(min_length=1)
    predecessor_environment: ArtifactRef

    @model_validator(mode="after")
    def consumes_revision(self):
        _payload(self.prompt_revision, self.revision)
        if (self.effective_prompt, self.failure_receipts, self.predecessor_environment) != (
            self.revision.revised_prompt,
            self.revision.failure_receipts,
            self.revision.predecessor_environment,
        ):
            raise ValueError("attempt must consume exact revision prompt, failures and predecessor")
        return self


class Image2EnvCompileV3Input(HarnessModel):
    model_config = public_schema_config("harness.image2env_compile_input.v3")
    schema_version: Literal["harness.image2env_compile_input.v3"]
    attempt: PromptRevisionCompileAttemptV2
    source_media: ImageSourceMedia
    interpretation_proposal: ArtifactRef
    proposal: SceneInterpretationProposalV1
    asset_catalog: ArtifactRef
    seed: Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]
    requested_profile: Literal["genesis.rigid_scene@1"]
    acquisition_policy: Literal["exact_only"]
    model_execution_allowed: ExactFalse

    @model_validator(mode="after")
    def consumes_proposal(self):
        _payload(self.interpretation_proposal, self.proposal)
        _ref(self.asset_catalog, "harness.portable_asset_catalog.v1")
        layout = self.attempt.revision.layout_proposal
        if (
            self.proposal.effective_prompt != self.attempt.effective_prompt
            or self.proposal.source_media_sha256 != self.source_media.source_media_sha256
            or self.proposal.route_id != "image2env"
            or self.proposal.unknowns
            or len(self.proposal.objects) != 1
            or self.proposal.objects[0].object_id != layout.source_object_id
            or self.proposal.objects[0].role != "dynamic"
            or tuple((r.relation, r.source, r.target) for r in self.proposal.relations)
            != (("on_table", layout.source_object_id, "table"),)
        ):
            raise ValueError("proposal must consume revised prompt, source and single-table object")
        return self


class Image2EnvGenesisCompileRequestV2(HarnessModel):
    model_config = public_schema_config("harness.image2env_genesis_compile_request.v2")
    schema_version: Literal["harness.image2env_genesis_compile_request.v2"]
    workflow_run_id: UUID4
    principal_id: PrincipalId
    expected_workflow_revision: NonNegativeInt
    expected_state_sha256: Sha256
    expected_receipt_head_sha256: Sha256
    compile_input: Image2EnvCompileV3Input
    qualification_run_id: UUID4

    @model_validator(mode="after")
    def current_revision(self):
        attempt = self.compile_input.attempt
        revision = attempt.revision
        if (
            self.workflow_run_id,
            self.principal_id,
            self.expected_workflow_revision,
            self.expected_state_sha256,
            self.expected_receipt_head_sha256,
        ) != (
            revision.workflow_run_id,
            revision.principal_id,
            revision.committed_revision,
            revision.resulting_state_sha256,
            attempt.prompt_revision.sha256,
        ):
            raise ValueError("recompile request must consume the declared current revision head")
        return self


class Image2EnvGenesisCompileReceiptV2(HarnessModel):
    model_config = public_schema_config("harness.image2env_genesis_compile_receipt.v2")
    schema_version: Literal["harness.image2env_genesis_compile_receipt.v2"]
    request: ArtifactRef
    scene: ArtifactRef
    qualification: ArtifactRef
    layout_proposal: ArtifactRef
    original_source: ArtifactRef
    derived_source_scene: ArtifactRef
    source_object_id: NonEmptyString
    proposal_object_id: NonEmptyString
    scene_object_id: Literal["object"]
    implementation_sha256: Sha256
    implementation: tuple[ArtifactRef, ...] = Field(min_length=1)
    scope: Literal["static_scene_candidate"]
    simulator_executed: ExactFalse

    @model_validator(mode="after")
    def static_derivation(self):
        for value, schema in (
            (self.request, "harness.image2env_genesis_compile_request.v2"),
            (self.scene, "harness.genesis_scene_spec.v1"),
            (self.qualification, "harness.asset_qualification.v1"),
            (self.layout_proposal, "harness.tabletop_layout_revision.v1"),
            (self.original_source, None),
            (self.derived_source_scene, None),
        ):
            _ref(value, schema)
        if self.source_object_id != self.proposal_object_id:
            raise ValueError("native static candidate requires the exact source object")
        names = tuple(r.name for r in self.implementation)
        if names != tuple(sorted(set(names))):
            raise ValueError("implementation source names must be sorted and unique")
        for value in self.implementation:
            _ref(value, None, "text/x-python")
        return self
