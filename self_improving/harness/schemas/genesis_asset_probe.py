"""Strict evidence contracts for one bounded Genesis rigid-asset probe."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import Field, model_validator

from .base import HarnessModel, Sha256, ShortString, public_schema_config
from .common import ArtifactRef
from .x2env_compile_evidence import (
    AssetModelSubjectV1,
    AssetRepresentationMemberV1,
    asset_loader_closure_sha256,
    canonical_evidence_sha256,
)

GENESIS_ASSET_PROBE_INPUT_SCHEMA_ID = "harness.genesis_asset_probe_input.v1"
GENESIS_ASSET_LOAD_EVIDENCE_SCHEMA_ID = "harness.genesis_asset_load_evidence.v1"
GENESIS_ASSET_CONTACT_TRACE_SCHEMA_ID = "harness.genesis_asset_contact_trace.v1"
GENESIS_ASSET_PROBE_REPORT_SCHEMA_ID = "harness.genesis_asset_probe_report.v1"

ASSET_REPRESENTATION_SCHEMA_ID = "harness.asset_representation.v1"
GENESIS_RUNTIME_LOCK_SCHEMA_ID = "harness.genesis_runtime_lock.v1"
CONTACT_TRACE_ROWS_SCHEMA_ID = "harness.genesis_asset_contact_trace_rows.v1"

CasUri = Annotated[
    str,
    Field(strict=True, pattern=r"^artifact://sha256/[0-9a-f]{64}$"),
]
NonNegativeFiniteFloat = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
PositiveFiniteFloat = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
ContactStep = Annotated[int, Field(strict=True, ge=0, le=999)]


class _CasArtifactRef(ArtifactRef):
    uri: CasUri

    @model_validator(mode="after")
    def uri_is_content_addressed(self) -> "_CasArtifactRef":
        if self.uri != f"artifact://sha256/{self.sha256}":
            raise ValueError("artifact uri must be content-addressed by sha256")
        return self


class _RepresentationRef(_CasArtifactRef):
    media_type: Literal["application/json"]
    schema_version: Literal[ASSET_REPRESENTATION_SCHEMA_ID]


class _RuntimeLockRef(_CasArtifactRef):
    media_type: Literal["application/json"]
    schema_version: Literal[GENESIS_RUNTIME_LOCK_SCHEMA_ID]


class _ProbeInputRef(_CasArtifactRef):
    media_type: Literal["application/json"]
    schema_version: Literal[GENESIS_ASSET_PROBE_INPUT_SCHEMA_ID]


class _LoadEvidenceRef(_CasArtifactRef):
    media_type: Literal["application/json"]
    schema_version: Literal[GENESIS_ASSET_LOAD_EVIDENCE_SCHEMA_ID]


class _ContactTraceRef(_CasArtifactRef):
    media_type: Literal["application/json"]
    schema_version: Literal[GENESIS_ASSET_CONTACT_TRACE_SCHEMA_ID]


class _ContactTraceRowsRef(_CasArtifactRef):
    media_type: Literal["application/x-ndjson"]
    schema_version: Literal[CONTACT_TRACE_ROWS_SCHEMA_ID]


class _SelfHashedProbeEvidence(HarnessModel):
    _digest_field: ClassVar[str]

    @model_validator(mode="after")
    def digest_is_bound(self) -> "_SelfHashedProbeEvidence":
        if getattr(self, self._digest_field) != canonical_evidence_sha256(
            self,
            self._digest_field,
        ):
            raise ValueError(f"{self._digest_field} digest is inconsistent")
        return self


class GenesisAssetProbeInputV1(_SelfHashedProbeEvidence):
    """Sealed CPU/Newton parameters and source identities for a candidate probe."""

    model_config = public_schema_config(GENESIS_ASSET_PROBE_INPUT_SCHEMA_ID)
    _digest_field = "probe_input_sha256"

    schema_version: Literal[GENESIS_ASSET_PROBE_INPUT_SCHEMA_ID]
    probe_input_sha256: Sha256
    representation: _RepresentationRef
    runtime_lock: _RuntimeLockRef
    requested_profile: Literal["genesis.rigid_scene@1"]
    release_height_m: Literal[0.01]
    dt_seconds: Literal[0.004]
    solver: Literal["newton"]
    steps: Literal[1000]
    cpu_threads: Literal[1]
    environment_count: Literal[1]
    seed: Literal[0]
    collisions_enabled: Literal[True]
    model_execution_allowed: Literal[False]
    network_access_allowed: Literal[False]


class GenesisInertiaTensorV1(HarnessModel):
    """URDF inertia tensor copied from the loaded dynamic link."""

    ixx: PositiveFiniteFloat
    ixy: Annotated[float, Field(strict=True, allow_inf_nan=False)]
    ixz: Annotated[float, Field(strict=True, allow_inf_nan=False)]
    iyy: PositiveFiniteFloat
    iyz: Annotated[float, Field(strict=True, allow_inf_nan=False)]
    izz: PositiveFiniteFloat


class GenesisAssetLoadEvidenceV1(_SelfHashedProbeEvidence):
    """Exact loader closure and physical properties observed after Genesis load."""

    model_config = public_schema_config(GENESIS_ASSET_LOAD_EVIDENCE_SCHEMA_ID)
    _digest_field = "load_evidence_sha256"

    schema_version: Literal[GENESIS_ASSET_LOAD_EVIDENCE_SCHEMA_ID]
    load_evidence_sha256: Sha256
    probe_input: _ProbeInputRef
    representation: _RepresentationRef
    runtime_lock: _RuntimeLockRef
    requested_profile: Literal["genesis.rigid_scene@1"]
    backend: Literal["genesis"]
    backend_version: ShortString
    backend_implementation_sha256: Sha256
    loader_contract: Literal["harness.genesis_rigid_loader.v1"]
    subject: AssetModelSubjectV1
    source_record_sha256: Sha256
    representation_sha256: Sha256
    root_logical_path: ShortString
    loader_closure_sha256: Sha256
    members: tuple[AssetRepresentationMemberV1, ...] = Field(min_length=15, max_length=15)
    member_count: Literal[15]
    root_link_count: Literal[1]
    joint_count: Literal[0]
    collision_shape_count: Literal[8]
    dynamic: Literal[True]
    fixed: Literal[False]
    mass_kg: PositiveFiniteFloat
    inertia_kg_m2: GenesisInertiaTensorV1
    friction: NonNegativeFiniteFloat
    loaded: Literal[True]

    @model_validator(mode="after")
    def load_identity_is_complete(self) -> "GenesisAssetLoadEvidenceV1":
        paths = tuple(member.logical_path for member in self.members)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("members must be canonically sorted and unique")
        if self.root_logical_path not in paths:
            raise ValueError("root_logical_path must be a member")
        if sum(member.role == "collision" for member in self.members) != 8:
            raise ValueError("collision_shape_count must match collision members")
        if self.source_record_sha256 != self.subject.source_record_sha256:
            raise ValueError("source_record_sha256 must match subject source_record_sha256")
        if self.loader_closure_sha256 != asset_loader_closure_sha256(
            self.subject,
            self.root_logical_path,
            self.members,
        ):
            raise ValueError("loader_closure_sha256 digest is inconsistent")
        return self


class GenesisAssetContactTraceV1(_SelfHashedProbeEvidence):
    """CAS-bound step/contact summary for the fixed one-thousand-step probe."""

    model_config = public_schema_config(GENESIS_ASSET_CONTACT_TRACE_SCHEMA_ID)
    _digest_field = "contact_trace_sha256"

    schema_version: Literal[GENESIS_ASSET_CONTACT_TRACE_SCHEMA_ID]
    contact_trace_sha256: Sha256
    probe_input: _ProbeInputRef
    load_evidence: _LoadEvidenceRef
    representation: _RepresentationRef
    runtime_lock: _RuntimeLockRef
    trace_rows: _ContactTraceRowsRef
    first_step: Literal[0]
    last_step: Literal[999]
    sample_count: Literal[1000]
    steps_contiguous: Literal[True]
    contact_observed: Literal[True]
    contact_step_count: Annotated[int, Field(strict=True, ge=1, le=1000)]
    first_contact_step: ContactStep
    last_contact_step: ContactStep
    total_contact_count: Annotated[int, Field(strict=True, ge=1)]
    collision_pair_count: Annotated[int, Field(strict=True, ge=1)]
    penetration_threshold_m: Literal[0.001]
    max_penetration_m: Annotated[float, Field(strict=True, ge=0, le=0.001, allow_inf_nan=False)]
    penetration_violation_count: Literal[0]
    max_normal_force_n: NonNegativeFiniteFloat
    mean_contact_normal_force_n: NonNegativeFiniteFloat
    total_normal_impulse_ns: NonNegativeFiniteFloat
    collisions_enabled: Literal[True]
    simulator_executed: Literal[True]

    @model_validator(mode="after")
    def contact_summary_is_coherent(self) -> "GenesisAssetContactTraceV1":
        if self.first_contact_step > self.last_contact_step:
            raise ValueError("contact step range must be ordered")
        if self.total_contact_count < self.contact_step_count:
            raise ValueError("total contact count must cover contact step count")
        return self


class GenesisAssetProbeCheckV1(HarnessModel):
    """One non-qualification probe check and its exact evidence references."""

    code: Literal[
        "backend_load",
        "closure_identity",
        "collision_shapes",
        "contact_observed",
        "dynamic_release",
        "penetration_bounded",
    ]
    status: Literal["pass"]
    evidence: tuple[_CasArtifactRef, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def evidence_is_canonical(self) -> "GenesisAssetProbeCheckV1":
        keys = tuple((ref.sha256, ref.bytes, ref.media_type) for ref in self.evidence)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("check evidence must be sorted and unique")
        return self


class GenesisAssetProbeReportV1(_SelfHashedProbeEvidence):
    """Closed candidate probe report; it intentionally makes no qualification claim."""

    model_config = public_schema_config(GENESIS_ASSET_PROBE_REPORT_SCHEMA_ID)
    _digest_field = "probe_report_sha256"

    schema_version: Literal[GENESIS_ASSET_PROBE_REPORT_SCHEMA_ID]
    probe_report_sha256: Sha256
    probe_input: _ProbeInputRef
    load_evidence: _LoadEvidenceRef
    contact_trace: _ContactTraceRef
    representation: _RepresentationRef
    runtime_lock: _RuntimeLockRef
    requested_profile: Literal["genesis.rigid_scene@1"]
    status: Literal["pass"]
    checks: tuple[GenesisAssetProbeCheckV1, ...] = Field(min_length=6, max_length=6)
    member_closure_verified: Literal[True]
    load_executed: Literal[True]
    contact_probe_executed: Literal[True]
    steps_executed: Literal[1000]
    collisions_enabled: Literal[True]
    simulator_executed: Literal[True]
    model_call_count: Literal[0]
    network_call_count: Literal[0]

    @model_validator(mode="after")
    def checks_are_exact_and_canonical(self) -> "GenesisAssetProbeReportV1":
        expected = (
            "backend_load",
            "closure_identity",
            "collision_shapes",
            "contact_observed",
            "dynamic_release",
            "penetration_bounded",
        )
        if tuple(check.code for check in self.checks) != expected:
            raise ValueError("checks must be the exact canonically sorted probe checks")
        return self


__all__ = [
    "GENESIS_ASSET_CONTACT_TRACE_SCHEMA_ID",
    "GENESIS_ASSET_LOAD_EVIDENCE_SCHEMA_ID",
    "GENESIS_ASSET_PROBE_INPUT_SCHEMA_ID",
    "GENESIS_ASSET_PROBE_REPORT_SCHEMA_ID",
    "GenesisAssetContactTraceV1",
    "GenesisAssetLoadEvidenceV1",
    "GenesisAssetProbeInputV1",
    "GenesisAssetProbeReportV1",
]
