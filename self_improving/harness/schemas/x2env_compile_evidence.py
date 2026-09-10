"""Typed, locally self-consistent evidence records for x2env compilation.

These schemas replace previously opaque ArtifactRef labels.  Cross-artifact and CAS byte
verification belongs to the exact-reuse closure verifier; the models here reject malformed local
records and bind each aggregate to a domain-separated canonical digest.
"""

from __future__ import annotations

import hashlib
import json
import math
from enum import Enum
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, Field, model_validator

from .asset_repair import LogicalPath
from .base import (
    CanonicalSkillRef,
    HarnessModel,
    NonEmptyString,
    NonNegativeInt,
    PositiveInt,
    Sha256,
    ShortString,
    public_schema_config,
)
from .common import ArtifactRef, Blocker, DependencyRef, ValidationStatus
from .x2env import RequestedGenesisProfile

SCENE_INTERPRETATION_PROPOSAL_SCHEMA_ID = "harness.scene_interpretation_proposal.v1"
PORTABLE_ASSET_CATALOG_SCHEMA_ID = "harness.portable_asset_catalog.v1"
ASSET_REPRESENTATION_SCHEMA_ID = "harness.asset_representation.v1"
ASSET_QUALIFICATION_SCHEMA_ID = "harness.asset_qualification.v1"
X2ENV_ASSET_SELECTION_SCHEMA_ID = "harness.x2env_asset_selection.v1"
SCENE_IR_SCHEMA_ID = "harness.scene_ir.v1"
TASK_IR_SCHEMA_ID = "harness.task_ir.v1"
ASSET_CLOSURE_MANIFEST_SCHEMA_ID = "harness.asset_closure_manifest.v1"
X2ENV_BINDING_MANIFEST_SCHEMA_ID = "harness.x2env_binding_manifest.v1"
GENESIS_RUNTIME_LOCK_SCHEMA_ID = "harness.genesis_runtime_lock.v1"
X2ENV_STATIC_VALIDATION_SCHEMA_ID = "harness.x2env_static_validation.v1"
X2ENV_COMPILE_RECEIPT_SCHEMA_ID = "harness.x2env_compile_receipt.v1"

ObjectId = Annotated[str, Field(strict=True, pattern=r"^[a-z][a-z0-9_]{0,63}$")]
AssetNamespace = Annotated[
    str,
    Field(strict=True, pattern=r"^[a-z][a-z0-9_.-]{0,63}$"),
]
AssetKey = Annotated[
    str,
    Field(strict=True, pattern=r"^[a-z0-9][a-z0-9_.-]{0,127}$"),
]
LedgerAssetId = Annotated[
    str,
    Field(strict=True, pattern=r"^[a-z0-9][a-z0-9_]{0,127}$"),
]
PositiveFiniteFloat = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
FiniteFloat = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Vector3 = tuple[PositiveFiniteFloat, PositiveFiniteFloat, PositiveFiniteFloat]
Position3 = tuple[FiniteFloat, FiniteFloat, FiniteFloat]
QuaternionWxyz = tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]


def _json_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(child) for child in value]
    return value


def canonical_evidence_sha256(value: object, digest_field: str) -> str:
    """Return the canonical, domain-separated digest for one evidence document."""

    if type(digest_field) is not str or not digest_field.endswith("_sha256"):
        raise TypeError("digest_field must be an exact *_sha256 string")
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json")
    elif isinstance(value, dict):
        payload = _json_value(value)
    else:
        raise TypeError("evidence value must be a model or dict")
    body = dict(payload)
    body.pop(digest_field, None)
    encoded = (
        json.dumps(body, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def asset_loader_closure_sha256(
    subject: "AssetModelSubjectV1 | dict[str, object]",
    root_logical_path: str,
    members: "tuple[AssetRepresentationMemberV1, ...] | list[object]",
) -> str:
    """Bind one model representation to its complete loader member set."""

    payload = {
        "schema_version": "harness.asset_loader_closure_identity.v1",
        "subject": _json_value(subject),
        "root_logical_path": root_logical_path,
        "members": _json_value(members),
    }
    encoded = (
        json.dumps(
            payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_cas_ref(
    ref: ArtifactRef,
    field: str,
    *,
    schema_version: str | None = None,
    media_type: str | None = None,
) -> None:
    if ref.uri != f"artifact://sha256/{ref.sha256}":
        raise ValueError(f"{field} must use its content-addressed artifact URI")
    if schema_version is not None and ref.schema_version != schema_version:
        raise ValueError(f"{field}.schema_version must be {schema_version!r}")
    if media_type is not None and ref.media_type != media_type:
        raise ValueError(f"{field}.media_type must be {media_type!r}")


def _require_json_ref(ref: ArtifactRef, field: str, schema_version: str) -> None:
    _require_cas_ref(ref, field, schema_version=schema_version, media_type="application/json")


def _require_sorted_unique(values: tuple[Any, ...], keys: tuple[Any, ...], field: str) -> None:
    if keys != tuple(sorted(set(keys))):
        raise ValueError(f"{field} must be sorted and unique")


class _SelfHashedEvidence(HarnessModel):
    _digest_field: ClassVar[str]

    @model_validator(mode="after")
    def digest_is_bound(self) -> "_SelfHashedEvidence":
        actual = getattr(self, self._digest_field)
        if actual != canonical_evidence_sha256(self, self._digest_field):
            raise ValueError(f"{self._digest_field} digest is inconsistent")
        return self


class SceneRelationV1(HarnessModel):
    relation: Literal["on_table", "on_top_of", "inside", "near"]
    source: ObjectId
    target: Annotated[str, Field(strict=True, pattern=r"^(table|[a-z][a-z0-9_]{0,63})$")]

    @model_validator(mode="after")
    def endpoints_are_valid(self) -> "SceneRelationV1":
        if self.source == self.target:
            raise ValueError("relation source and target must differ")
        if (self.relation == "on_table") != (self.target == "table"):
            raise ValueError("only on_table may target table")
        return self


class ProposedSceneObjectV1(HarnessModel):
    object_id: ObjectId
    category: ShortString
    role: Literal["dynamic", "support_surface"]
    dimensions_m: Vector3


class SceneInterpretationProposalV1(_SelfHashedEvidence):
    model_config = public_schema_config(SCENE_INTERPRETATION_PROPOSAL_SCHEMA_ID)
    _digest_field = "proposal_sha256"

    schema_version: Literal[SCENE_INTERPRETATION_PROPOSAL_SCHEMA_ID]
    proposal_sha256: Sha256
    route_id: Literal["image2env", "video2env"]
    source_media_sha256: Sha256
    effective_prompt: ArtifactRef
    objects: tuple[ProposedSceneObjectV1, ...] = Field(min_length=1, max_length=64)
    relations: tuple[SceneRelationV1, ...] = Field(min_length=1, max_length=256)
    unknowns: tuple[ShortString, ...] = Field(max_length=64)

    @model_validator(mode="after")
    def proposal_is_canonical(self) -> "SceneInterpretationProposalV1":
        _require_cas_ref(
            self.effective_prompt,
            "effective_prompt",
            schema_version="harness.prompt_text.v1",
            media_type="text/plain",
        )
        object_ids = tuple(item.object_id for item in self.objects)
        _require_sorted_unique(self.objects, object_ids, "objects")
        relation_ids = tuple((item.source, item.target, item.relation) for item in self.relations)
        _require_sorted_unique(self.relations, relation_ids, "relations")
        _require_sorted_unique(self.unknowns, self.unknowns, "unknowns")
        known = set(object_ids)
        for relation in self.relations:
            if relation.source not in known:
                raise ValueError("unknown relation source")
            if relation.target != "table" and relation.target not in known:
                raise ValueError("unknown relation target")
        return self


class GenesisFrameV1(HarnessModel):
    name: Literal["genesis_world"]
    handedness: Literal["right_handed"]
    up_axis: Literal["z"]
    unit: Literal["m"]


class SceneIREntityV1(HarnessModel):
    object_id: ObjectId
    category: ShortString
    dynamic: bool = Field(strict=True)
    collision_required: Literal[True]
    dimensions_m: Vector3


class SceneIRV1(_SelfHashedEvidence):
    model_config = public_schema_config(SCENE_IR_SCHEMA_ID)
    _digest_field = "scene_ir_sha256"

    schema_version: Literal[SCENE_IR_SCHEMA_ID]
    scene_ir_sha256: Sha256
    interpretation_proposal: ArtifactRef
    frame: GenesisFrameV1
    objects: tuple[SceneIREntityV1, ...] = Field(min_length=1, max_length=64)
    relations: tuple[SceneRelationV1, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def scene_is_canonical(self) -> "SceneIRV1":
        _require_json_ref(
            self.interpretation_proposal,
            "interpretation_proposal",
            SCENE_INTERPRETATION_PROPOSAL_SCHEMA_ID,
        )
        object_ids = tuple(item.object_id for item in self.objects)
        _require_sorted_unique(self.objects, object_ids, "objects")
        relation_ids = tuple((item.source, item.target, item.relation) for item in self.relations)
        _require_sorted_unique(self.relations, relation_ids, "relations")
        known = set(object_ids)
        for relation in self.relations:
            if relation.source not in known:
                raise ValueError("unknown relation source")
            if relation.target != "table" and relation.target not in known:
                raise ValueError("unknown relation target")
        return self


class TaskIRV1(_SelfHashedEvidence):
    model_config = public_schema_config(TASK_IR_SCHEMA_ID)
    _digest_field = "task_ir_sha256"

    schema_version: Literal[TASK_IR_SCHEMA_ID]
    task_ir_sha256: Sha256
    scene_ir: ArtifactRef
    requested_profile: RequestedGenesisProfile
    goal_relations: tuple[SceneRelationV1, ...] = Field(min_length=1, max_length=256)
    reset_required: Literal[True]
    action_contract: Literal["none", "robot_control"]
    observation_keys: tuple[ShortString, ...] = Field(min_length=1, max_length=64)
    termination_steps: PositiveInt

    @model_validator(mode="after")
    def task_is_profile_bound(self) -> "TaskIRV1":
        _require_json_ref(self.scene_ir, "scene_ir", SCENE_IR_SCHEMA_ID)
        relation_ids = tuple(
            (item.source, item.target, item.relation) for item in self.goal_relations
        )
        _require_sorted_unique(self.goal_relations, relation_ids, "goal_relations")
        _require_sorted_unique(self.observation_keys, self.observation_keys, "observation_keys")
        expected = (
            "robot_control"
            if self.requested_profile is RequestedGenesisProfile.ROBOT_POLICY
            else "none"
        )
        if self.action_contract != expected:
            raise ValueError("action_contract must match requested_profile")
        return self


class AssetModelSubjectV1(HarnessModel):
    namespace: AssetNamespace
    asset_key: AssetKey
    ledger_asset_id: LedgerAssetId
    model_id: NonNegativeInt
    backend: Literal["genesis"]
    source_record_sha256: Sha256


class QualifiedAssetSubjectV1(HarnessModel):
    model_subject: AssetModelSubjectV1
    representation_sha256: Sha256
    loader_closure_sha256: Sha256


class AssetRepresentationMemberV1(HarnessModel):
    logical_path: LogicalPath
    role: Literal["collision", "metadata", "urdf", "visual"]
    artifact: ArtifactRef

    @model_validator(mode="after")
    def artifact_is_content_addressed(self) -> "AssetRepresentationMemberV1":
        _require_cas_ref(self.artifact, "artifact")
        return self


class AssetRepresentationV1(_SelfHashedEvidence):
    model_config = public_schema_config(ASSET_REPRESENTATION_SCHEMA_ID)
    _digest_field = "representation_sha256"

    schema_version: Literal[ASSET_REPRESENTATION_SCHEMA_ID]
    representation_sha256: Sha256
    subject: AssetModelSubjectV1
    representation_kind: Literal["rigid_mesh", "urdf"]
    root_logical_path: LogicalPath
    loader_scale: Vector3
    loader_closure_sha256: Sha256
    members: tuple[AssetRepresentationMemberV1, ...] = Field(min_length=1, max_length=4096)
    collision_member_paths: tuple[LogicalPath, ...] = Field(min_length=1, max_length=4096)
    visual_member_paths: tuple[LogicalPath, ...] = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def representation_is_complete(self) -> "AssetRepresentationV1":
        paths = tuple(item.logical_path for item in self.members)
        _require_sorted_unique(self.members, paths, "members")
        _require_sorted_unique(
            self.collision_member_paths,
            self.collision_member_paths,
            "collision_member_paths",
        )
        _require_sorted_unique(
            self.visual_member_paths,
            self.visual_member_paths,
            "visual_member_paths",
        )
        known = set(paths)
        if self.root_logical_path not in known:
            raise ValueError("root_logical_path must be a representation member")
        if not set(self.collision_member_paths).issubset(known):
            raise ValueError("collision_member_paths must be representation members")
        if not set(self.visual_member_paths).issubset(known):
            raise ValueError("visual_member_paths must be representation members")
        if self.loader_closure_sha256 != asset_loader_closure_sha256(
            self.subject,
            self.root_logical_path,
            self.members,
        ):
            raise ValueError("loader_closure_sha256 digest is inconsistent")
        return self


class AssetClosureEntryV1(HarnessModel):
    subject: AssetModelSubjectV1
    representation: ArtifactRef
    representation_sha256: Sha256
    loader_closure_sha256: Sha256
    root_logical_path: LogicalPath
    members: tuple[AssetRepresentationMemberV1, ...] = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def entry_is_canonical(self) -> "AssetClosureEntryV1":
        _require_json_ref(
            self.representation,
            "representation",
            ASSET_REPRESENTATION_SCHEMA_ID,
        )
        paths = tuple(item.logical_path for item in self.members)
        _require_sorted_unique(self.members, paths, "members")
        if self.root_logical_path not in set(paths):
            raise ValueError("root_logical_path must be a closure member")
        if self.loader_closure_sha256 != asset_loader_closure_sha256(
            self.subject,
            self.root_logical_path,
            self.members,
        ):
            raise ValueError("loader_closure_sha256 digest is inconsistent")
        return self


class AssetClosureManifestV1(_SelfHashedEvidence):
    model_config = public_schema_config(ASSET_CLOSURE_MANIFEST_SCHEMA_ID)
    _digest_field = "closure_sha256"

    schema_version: Literal[ASSET_CLOSURE_MANIFEST_SCHEMA_ID]
    closure_sha256: Sha256
    assets: tuple[AssetClosureEntryV1, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def assets_are_canonical(self) -> "AssetClosureManifestV1":
        keys = tuple(
            (item.subject.namespace, item.subject.asset_key, item.subject.model_id)
            for item in self.assets
        )
        _require_sorted_unique(self.assets, keys, "assets")
        all_paths = tuple(member.logical_path for asset in self.assets for member in asset.members)
        if len(all_paths) != len(set(all_paths)):
            raise ValueError("asset closure member paths must be globally unique")
        return self


class GenesisRuntimeLockV1(_SelfHashedEvidence):
    model_config = public_schema_config(GENESIS_RUNTIME_LOCK_SCHEMA_ID)
    _digest_field = "runtime_lock_sha256"

    schema_version: Literal[GENESIS_RUNTIME_LOCK_SCHEMA_ID]
    runtime_lock_sha256: Sha256
    backend: Literal["genesis"]
    backend_version: ShortString
    runtime_implementation_sha256: Sha256
    loader_contract: ShortString
    requested_profile: RequestedGenesisProfile
    dependencies: tuple[DependencyRef, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def dependencies_are_canonical(self) -> "GenesisRuntimeLockV1":
        names = tuple(item.name for item in self.dependencies)
        _require_sorted_unique(self.dependencies, names, "dependencies")
        return self


class AssetQualificationCheckV1(HarnessModel):
    code: ShortString
    status: Literal["pass"]
    evidence: tuple[ArtifactRef, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def evidence_is_canonical(self) -> "AssetQualificationCheckV1":
        keys = tuple((item.sha256, item.bytes, item.media_type) for item in self.evidence)
        _require_sorted_unique(self.evidence, keys, "evidence")
        for index, item in enumerate(self.evidence):
            _require_cas_ref(item, f"evidence[{index}]")
        return self


class GenesisAssetQualificationV1(_SelfHashedEvidence):
    model_config = public_schema_config(ASSET_QUALIFICATION_SCHEMA_ID)
    _digest_field = "qualification_sha256"

    schema_version: Literal[ASSET_QUALIFICATION_SCHEMA_ID]
    qualification_sha256: Sha256
    subject: QualifiedAssetSubjectV1
    representation: ArtifactRef
    runtime_lock: ArtifactRef
    requested_profile: RequestedGenesisProfile
    issuer_implementation_sha256: Sha256
    checks: tuple[AssetQualificationCheckV1, ...] = Field(min_length=1, max_length=128)
    status: Literal["pass"]
    backend_load_executed: Literal[True]
    collision_probe_executed: Literal[True]
    qualified: Literal[True]

    @model_validator(mode="after")
    def qualification_is_typed(self) -> "GenesisAssetQualificationV1":
        _require_json_ref(self.representation, "representation", ASSET_REPRESENTATION_SCHEMA_ID)
        _require_json_ref(self.runtime_lock, "runtime_lock", GENESIS_RUNTIME_LOCK_SCHEMA_ID)
        codes = tuple(item.code for item in self.checks)
        _require_sorted_unique(self.checks, codes, "checks")
        return self


class PortableAssetCatalogEntryV1(HarnessModel):
    subject: AssetModelSubjectV1
    semantic_name: ShortString
    aliases: tuple[ShortString, ...] = Field(max_length=64)
    representation: ArtifactRef
    representation_sha256: Sha256
    qualification: ArtifactRef
    qualified_subject: QualifiedAssetSubjectV1
    available: Literal[True]

    @model_validator(mode="after")
    def entry_is_bound(self) -> "PortableAssetCatalogEntryV1":
        _require_json_ref(self.representation, "representation", ASSET_REPRESENTATION_SCHEMA_ID)
        _require_json_ref(self.qualification, "qualification", ASSET_QUALIFICATION_SCHEMA_ID)
        _require_sorted_unique(self.aliases, self.aliases, "aliases")
        if self.qualified_subject.model_subject != self.subject:
            raise ValueError("qualified subject must identify the catalog subject")
        if self.qualified_subject.representation_sha256 != self.representation_sha256:
            raise ValueError("qualified subject must bind representation_sha256")
        return self


class PortableAssetCatalogV1(_SelfHashedEvidence):
    model_config = public_schema_config(PORTABLE_ASSET_CATALOG_SCHEMA_ID)
    _digest_field = "catalog_sha256"

    schema_version: Literal[PORTABLE_ASSET_CATALOG_SCHEMA_ID]
    catalog_sha256: Sha256
    runtime_lock: ArtifactRef
    entries: tuple[PortableAssetCatalogEntryV1, ...] = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def catalog_is_canonical(self) -> "PortableAssetCatalogV1":
        _require_json_ref(self.runtime_lock, "runtime_lock", GENESIS_RUNTIME_LOCK_SCHEMA_ID)
        keys = tuple(
            (item.subject.namespace, item.subject.asset_key, item.subject.model_id)
            for item in self.entries
        )
        _require_sorted_unique(self.entries, keys, "entries")
        return self


class X2EnvAssetSelectionV1(_SelfHashedEvidence):
    model_config = public_schema_config(X2ENV_ASSET_SELECTION_SCHEMA_ID)
    _digest_field = "selection_sha256"

    schema_version: Literal[X2ENV_ASSET_SELECTION_SCHEMA_ID]
    selection_sha256: Sha256
    scene_object_id: ObjectId
    lane: Literal["exact_asset_reuse"]
    asset_catalog: ArtifactRef
    catalog_subject: AssetModelSubjectV1
    qualified_subject: QualifiedAssetSubjectV1
    representation: ArtifactRef
    qualification: ArtifactRef
    asset_closure_manifest: ArtifactRef
    selected: Literal[True]
    decision_reason: NonEmptyString

    @model_validator(mode="after")
    def selection_is_typed(self) -> "X2EnvAssetSelectionV1":
        _require_json_ref(self.asset_catalog, "asset_catalog", PORTABLE_ASSET_CATALOG_SCHEMA_ID)
        _require_json_ref(self.representation, "representation", ASSET_REPRESENTATION_SCHEMA_ID)
        _require_json_ref(self.qualification, "qualification", ASSET_QUALIFICATION_SCHEMA_ID)
        _require_json_ref(
            self.asset_closure_manifest,
            "asset_closure_manifest",
            ASSET_CLOSURE_MANIFEST_SCHEMA_ID,
        )
        if self.qualified_subject.model_subject != self.catalog_subject:
            raise ValueError("qualified subject must identify catalog_subject")
        return self


class X2EnvObjectBindingV1(HarnessModel):
    scene_object_id: ObjectId
    selection: ArtifactRef
    qualified_subject: QualifiedAssetSubjectV1
    representation: ArtifactRef
    qualification: ArtifactRef
    root_logical_path: LogicalPath
    position_m: Position3
    orientation_wxyz: QuaternionWxyz

    @model_validator(mode="after")
    def binding_is_typed(self) -> "X2EnvObjectBindingV1":
        _require_json_ref(self.selection, "selection", X2ENV_ASSET_SELECTION_SCHEMA_ID)
        _require_json_ref(self.representation, "representation", ASSET_REPRESENTATION_SCHEMA_ID)
        _require_json_ref(self.qualification, "qualification", ASSET_QUALIFICATION_SCHEMA_ID)
        norm = math.sqrt(sum(value * value for value in self.orientation_wxyz))
        if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("orientation_wxyz must be a unit quaternion")
        return self


class X2EnvBindingManifestV1(_SelfHashedEvidence):
    model_config = public_schema_config(X2ENV_BINDING_MANIFEST_SCHEMA_ID)
    _digest_field = "binding_sha256"

    schema_version: Literal[X2ENV_BINDING_MANIFEST_SCHEMA_ID]
    binding_sha256: Sha256
    scene_ir: ArtifactRef
    task_ir: ArtifactRef
    asset_catalog: ArtifactRef
    asset_closure_manifest: ArtifactRef
    runtime_lock: ArtifactRef
    bindings: tuple[X2EnvObjectBindingV1, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def manifest_is_typed(self) -> "X2EnvBindingManifestV1":
        for ref, field, schema in (
            (self.scene_ir, "scene_ir", SCENE_IR_SCHEMA_ID),
            (self.task_ir, "task_ir", TASK_IR_SCHEMA_ID),
            (self.asset_catalog, "asset_catalog", PORTABLE_ASSET_CATALOG_SCHEMA_ID),
            (
                self.asset_closure_manifest,
                "asset_closure_manifest",
                ASSET_CLOSURE_MANIFEST_SCHEMA_ID,
            ),
            (self.runtime_lock, "runtime_lock", GENESIS_RUNTIME_LOCK_SCHEMA_ID),
        ):
            _require_json_ref(ref, field, schema)
        keys = tuple(item.scene_object_id for item in self.bindings)
        _require_sorted_unique(self.bindings, keys, "bindings")
        return self


class X2EnvStaticValidationCheckV1(HarnessModel):
    code: ShortString
    status: Literal["pass", "incomplete", "fail"]
    evidence: tuple[ArtifactRef, ...] = Field(max_length=64)

    @model_validator(mode="after")
    def evidence_is_canonical(self) -> "X2EnvStaticValidationCheckV1":
        keys = tuple((item.sha256, item.bytes, item.media_type) for item in self.evidence)
        _require_sorted_unique(self.evidence, keys, "evidence")
        for index, item in enumerate(self.evidence):
            _require_cas_ref(item, f"evidence[{index}]")
        return self


class X2EnvStaticValidationV1(_SelfHashedEvidence):
    model_config = public_schema_config(X2ENV_STATIC_VALIDATION_SCHEMA_ID)
    _digest_field = "validation_sha256"

    schema_version: Literal[X2ENV_STATIC_VALIDATION_SCHEMA_ID]
    validation_sha256: Sha256
    scene_ir: ArtifactRef
    task_ir: ArtifactRef
    asset_catalog: ArtifactRef
    asset_selections: tuple[ArtifactRef, ...] = Field(min_length=1, max_length=64)
    asset_closure_manifest: ArtifactRef
    binding_manifest: ArtifactRef
    runtime_lock: ArtifactRef
    status: ValidationStatus
    checks: tuple[X2EnvStaticValidationCheckV1, ...] = Field(min_length=1, max_length=256)
    blockers: tuple[Blocker, ...]
    simulator_executed: Literal[False]
    physical_validation_claimed: Literal[False]
    publishable: Literal[False]

    @model_validator(mode="after")
    def validation_is_typed(self) -> "X2EnvStaticValidationV1":
        for ref, field, schema in (
            (self.scene_ir, "scene_ir", SCENE_IR_SCHEMA_ID),
            (self.task_ir, "task_ir", TASK_IR_SCHEMA_ID),
            (self.asset_catalog, "asset_catalog", PORTABLE_ASSET_CATALOG_SCHEMA_ID),
            (
                self.asset_closure_manifest,
                "asset_closure_manifest",
                ASSET_CLOSURE_MANIFEST_SCHEMA_ID,
            ),
            (self.binding_manifest, "binding_manifest", X2ENV_BINDING_MANIFEST_SCHEMA_ID),
            (self.runtime_lock, "runtime_lock", GENESIS_RUNTIME_LOCK_SCHEMA_ID),
        ):
            _require_json_ref(ref, field, schema)
        selection_keys = tuple(ref.sha256 for ref in self.asset_selections)
        _require_sorted_unique(self.asset_selections, selection_keys, "asset_selections")
        for index, ref in enumerate(self.asset_selections):
            _require_json_ref(ref, f"asset_selections[{index}]", X2ENV_ASSET_SELECTION_SCHEMA_ID)
        codes = tuple(item.code for item in self.checks)
        _require_sorted_unique(self.checks, codes, "checks")
        statuses = {item.status for item in self.checks}
        if self.status is ValidationStatus.PASS:
            if statuses != {"pass"} or self.blockers:
                raise ValueError("pass static validation requires passing checks and no blockers")
        elif not self.blockers:
            raise ValueError("non-pass static validation requires blockers")
        return self


class X2EnvCompileReceiptV1(_SelfHashedEvidence):
    model_config = public_schema_config(X2ENV_COMPILE_RECEIPT_SCHEMA_ID)
    _digest_field = "receipt_sha256"

    schema_version: Literal[X2ENV_COMPILE_RECEIPT_SCHEMA_ID]
    receipt_sha256: Sha256
    skill_ref: CanonicalSkillRef
    source_media_sha256: Sha256
    interpretation_proposal: ArtifactRef
    asset_catalog: ArtifactRef
    scene_ir: ArtifactRef
    task_ir: ArtifactRef
    asset_selections: tuple[ArtifactRef, ...] = Field(min_length=1, max_length=64)
    asset_closure_manifest: ArtifactRef
    binding_manifest: ArtifactRef
    runtime_lock: ArtifactRef
    static_validation: ArtifactRef
    environment_package: ArtifactRef
    package_id: Sha256
    predecessor_package: ArtifactRef | None
    implementation_sha256: Sha256
    simulator_executed: Literal[False]
    publishable: Literal[False]

    @model_validator(mode="after")
    def receipt_is_typed(self) -> "X2EnvCompileReceiptV1":
        if self.skill_ref not in {
            "image2env.compile@1.0.0",
            "video2env.compile@1.0.0",
        }:
            raise ValueError("skill_ref must identify an exact x2env compile Skill")
        for ref, field, schema in (
            (
                self.interpretation_proposal,
                "interpretation_proposal",
                SCENE_INTERPRETATION_PROPOSAL_SCHEMA_ID,
            ),
            (self.asset_catalog, "asset_catalog", PORTABLE_ASSET_CATALOG_SCHEMA_ID),
            (self.scene_ir, "scene_ir", SCENE_IR_SCHEMA_ID),
            (self.task_ir, "task_ir", TASK_IR_SCHEMA_ID),
            (
                self.asset_closure_manifest,
                "asset_closure_manifest",
                ASSET_CLOSURE_MANIFEST_SCHEMA_ID,
            ),
            (self.binding_manifest, "binding_manifest", X2ENV_BINDING_MANIFEST_SCHEMA_ID),
            (self.runtime_lock, "runtime_lock", GENESIS_RUNTIME_LOCK_SCHEMA_ID),
            (
                self.static_validation,
                "static_validation",
                X2ENV_STATIC_VALIDATION_SCHEMA_ID,
            ),
            (
                self.environment_package,
                "environment_package",
                "harness.x2env_environment_package.v1",
            ),
        ):
            _require_json_ref(ref, field, schema)
        selection_keys = tuple(ref.sha256 for ref in self.asset_selections)
        _require_sorted_unique(self.asset_selections, selection_keys, "asset_selections")
        for index, ref in enumerate(self.asset_selections):
            _require_json_ref(ref, f"asset_selections[{index}]", X2ENV_ASSET_SELECTION_SCHEMA_ID)
        if self.predecessor_package is not None:
            _require_json_ref(
                self.predecessor_package,
                "predecessor_package",
                "harness.x2env_environment_package.v1",
            )
        return self


__all__ = [
    "ASSET_CLOSURE_MANIFEST_SCHEMA_ID",
    "ASSET_QUALIFICATION_SCHEMA_ID",
    "ASSET_REPRESENTATION_SCHEMA_ID",
    "GENESIS_RUNTIME_LOCK_SCHEMA_ID",
    "PORTABLE_ASSET_CATALOG_SCHEMA_ID",
    "SCENE_INTERPRETATION_PROPOSAL_SCHEMA_ID",
    "SCENE_IR_SCHEMA_ID",
    "TASK_IR_SCHEMA_ID",
    "X2ENV_ASSET_SELECTION_SCHEMA_ID",
    "X2ENV_BINDING_MANIFEST_SCHEMA_ID",
    "X2ENV_COMPILE_RECEIPT_SCHEMA_ID",
    "X2ENV_STATIC_VALIDATION_SCHEMA_ID",
    "AssetClosureManifestV1",
    "AssetModelSubjectV1",
    "AssetRepresentationV1",
    "GenesisAssetQualificationV1",
    "GenesisRuntimeLockV1",
    "PortableAssetCatalogV1",
    "QualifiedAssetSubjectV1",
    "SceneIRV1",
    "SceneInterpretationProposalV1",
    "TaskIRV1",
    "X2EnvAssetSelectionV1",
    "X2EnvBindingManifestV1",
    "X2EnvCompileReceiptV1",
    "X2EnvStaticValidationV1",
    "asset_loader_closure_sha256",
    "canonical_evidence_sha256",
]
