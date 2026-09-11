"""Camera-free producer and strict consumer for one Genesis rigid-asset probe."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ValidationError

from .artifacts import ArtifactResolutionError, LocalArtifactStore
from .schemas.common import ArtifactRef
from .schemas.genesis_asset_probe import (
    CONTACT_TRACE_ROWS_SCHEMA_ID,
    GENESIS_ASSET_CONTACT_TRACE_ROW_SCHEMA_ID,
    GENESIS_ASSET_CONTACT_TRACE_SCHEMA_ID,
    GENESIS_ASSET_LOAD_EVIDENCE_SCHEMA_ID,
    GENESIS_ASSET_LOAD_OBSERVATION_SCHEMA_ID,
    GENESIS_ASSET_PROBE_INPUT_SCHEMA_ID,
    GENESIS_ASSET_PROBE_REPORT_SCHEMA_ID,
    GenesisAssetContactTraceRowV1,
    GenesisAssetContactTraceV1,
    GenesisAssetGroundContactV1,
    GenesisAssetLoadEvidenceV1,
    GenesisAssetLoadObservationV1,
    GenesisAssetProbeInputV1,
    GenesisAssetProbeReportV1,
)
from .schemas.x2env_compile_evidence import (
    ASSET_REPRESENTATION_SCHEMA_ID,
    GENESIS_RUNTIME_LOCK_SCHEMA_ID,
    AssetRepresentationV1,
    GenesisRuntimeLockV1,
    canonical_evidence_sha256,
)

_SHA256 = re.compile(r"[0-9a-f]{64}")
_MAX_TRACE_BYTES = 64 * 1024 * 1024


class GenesisAssetProbeError(ValueError):
    """A stable fail-closed error from probe production or evidence verification."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class GenesisAssetGroundContact:
    asset_collision_index: int
    ground_collision_index: int
    position_m: tuple[float, float, float]
    normal_on_asset: tuple[float, float, float]
    force_on_asset_n: tuple[float, float, float]
    force_on_ground_n: tuple[float, float, float]
    penetration_m: float
    normal_force_n: float


@dataclass(frozen=True, slots=True)
class GenesisAssetBackendStep:
    step_index: int
    phase: str
    position_m: tuple[float, float, float]
    orientation_wxyz: tuple[float, float, float, float]
    linear_velocity_m_s: tuple[float, float, float]
    angular_velocity_rad_s: tuple[float, float, float]
    asset_ground_contacts: tuple[GenesisAssetGroundContact, ...]


@dataclass(frozen=True, slots=True)
class GenesisAssetBackendLoad:
    source: str
    root_link_count: int
    joint_count: int
    collision_shape_count: int
    collision_shape_members: tuple[str, ...]
    dynamic: bool
    fixed: bool
    mass_kg: float
    inertia_kg_m2: dict[str, float]
    friction: float
    loaded_collision_min_z_m: float
    test_translation_m: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class GenesisAssetProbeLoadRequest:
    probe_input: GenesisAssetProbeInputV1
    representation: AssetRepresentationV1
    runtime_lock: GenesisRuntimeLockV1
    materialized_root: Path
    root_path: Path


@runtime_checkable
class GenesisAssetProbeBackend(Protocol):
    backend: str
    backend_version: str
    implementation_sha256: str
    loader_contract: str

    def load(self, request: GenesisAssetProbeLoadRequest) -> GenesisAssetBackendLoad: ...

    def observe_initial(self) -> GenesisAssetBackendStep: ...

    def step(self, step_index: int) -> GenesisAssetBackendStep: ...


@dataclass(frozen=True, slots=True)
class GenesisAssetProbeArtifacts:
    probe_input: GenesisAssetProbeInputV1
    probe_input_ref: ArtifactRef
    load_observation: GenesisAssetLoadObservationV1
    load_observation_ref: ArtifactRef
    load_evidence: GenesisAssetLoadEvidenceV1
    load_evidence_ref: ArtifactRef
    rows: tuple[GenesisAssetContactTraceRowV1, ...]
    trace_rows_ref: ArtifactRef
    contact_trace: GenesisAssetContactTraceV1
    contact_trace_ref: ArtifactRef
    probe_report: GenesisAssetProbeReportV1
    probe_report_ref: ArtifactRef


@dataclass(frozen=True, slots=True)
class VerifiedGenesisAssetProbeEvidence:
    probe_input: GenesisAssetProbeInputV1
    load_observation: GenesisAssetLoadObservationV1
    load_evidence: GenesisAssetLoadEvidenceV1
    rows: tuple[GenesisAssetContactTraceRowV1, ...]
    contact_trace: GenesisAssetContactTraceV1
    probe_report: GenesisAssetProbeReportV1
    representation: AssetRepresentationV1
    runtime_lock: GenesisRuntimeLockV1
    supporting_artifacts: tuple[ArtifactRef, ...]


@dataclass(frozen=True, slots=True)
class GenesisAssetProbeProducer:
    artifact_store: LocalArtifactStore
    backend: GenesisAssetProbeBackend

    def __post_init__(self) -> None:
        if type(self.artifact_store) is not LocalArtifactStore:
            raise TypeError("artifact_store must be the exact LocalArtifactStore")
        if not isinstance(self.backend, GenesisAssetProbeBackend):
            raise TypeError("backend must implement GenesisAssetProbeBackend")

    def produce(
        self,
        *,
        representation_ref: ArtifactRef,
        runtime_lock_ref: ArtifactRef,
    ) -> GenesisAssetProbeArtifacts:
        representation = _read_model(
            self.artifact_store,
            representation_ref,
            AssetRepresentationV1,
            ASSET_REPRESENTATION_SCHEMA_ID,
        )
        runtime_lock = _read_model(
            self.artifact_store,
            runtime_lock_ref,
            GenesisRuntimeLockV1,
            GENESIS_RUNTIME_LOCK_SCHEMA_ID,
        )
        _verify_representation(self.artifact_store, representation_ref, representation)
        _verify_runtime(self.backend, runtime_lock_ref, runtime_lock)

        probe_input = _self_hashed(
            GenesisAssetProbeInputV1,
            "probe_input_sha256",
            {
                "schema_version": GENESIS_ASSET_PROBE_INPUT_SCHEMA_ID,
                "representation": representation_ref,
                "runtime_lock": runtime_lock_ref,
                "requested_profile": "genesis.rigid_scene@1",
                "release_height_m": 0.01,
                "dt_seconds": 0.004,
                "solver": "newton",
                "steps": 1000,
                "cpu_threads": 1,
                "environment_count": 1,
                "seed": 0,
                "collisions_enabled": True,
                "model_execution_allowed": False,
                "network_access_allowed": False,
            },
        )
        probe_input_ref = _publish_model(
            self.artifact_store,
            probe_input,
            "genesis-asset-probe-input.json",
            GENESIS_ASSET_PROBE_INPUT_SCHEMA_ID,
        )

        with tempfile.TemporaryDirectory(prefix="genesis-asset-probe-") as directory:
            materialized_root = Path(directory)
            _materialize_representation(self.artifact_store, representation, materialized_root)
            load = self.backend.load(
                GenesisAssetProbeLoadRequest(
                    probe_input=probe_input,
                    representation=representation,
                    runtime_lock=runtime_lock,
                    materialized_root=materialized_root,
                    root_path=materialized_root / representation.root_logical_path,
                )
            )
            _verify_backend_load(load, representation)
            load_observation = _build_load_observation(
                probe_input_ref=probe_input_ref,
                representation_ref=representation_ref,
                runtime_lock_ref=runtime_lock_ref,
                representation=representation,
                runtime_lock=runtime_lock,
                load=load,
            )
            load_observation_ref = _publish_model(
                self.artifact_store,
                load_observation,
                "genesis-asset-load-observation.json",
                GENESIS_ASSET_LOAD_OBSERVATION_SCHEMA_ID,
            )
            load_evidence = _build_load_evidence(load_observation, load_observation_ref)
            load_evidence_ref = _publish_model(
                self.artifact_store,
                load_evidence,
                "genesis-asset-load-evidence.json",
                GENESIS_ASSET_LOAD_EVIDENCE_SCHEMA_ID,
            )

            backend_rows = [self.backend.observe_initial()]
            backend_rows.extend(self.backend.step(step) for step in range(1, 1001))

        rows = tuple(_row_from_backend(value, index) for index, value in enumerate(backend_rows))
        if any(row.max_penetration_m > 0.001 for row in rows):
            raise GenesisAssetProbeError(
                "HARN_GENESIS_ASSET_PROBE_FAILED",
                "probe penetration exceeded the accepted threshold",
            )
        trace_payload = b"".join(_canonical_json_bytes(row.model_dump(mode="json")) for row in rows)
        trace_rows_ref = _publish_bytes(
            self.artifact_store,
            trace_payload,
            "genesis-asset-contact-trace.ndjson",
            "application/x-ndjson",
            CONTACT_TRACE_ROWS_SCHEMA_ID,
        )
        contact_trace = _build_contact_trace(
            probe_input_ref=probe_input_ref,
            load_evidence_ref=load_evidence_ref,
            representation_ref=representation_ref,
            runtime_lock_ref=runtime_lock_ref,
            trace_rows_ref=trace_rows_ref,
            rows=rows,
        )
        contact_trace_ref = _publish_model(
            self.artifact_store,
            contact_trace,
            "genesis-asset-contact-trace.json",
            GENESIS_ASSET_CONTACT_TRACE_SCHEMA_ID,
        )
        probe_report = _build_report(
            probe_input_ref=probe_input_ref,
            load_observation_ref=load_observation_ref,
            load_evidence_ref=load_evidence_ref,
            trace_rows_ref=trace_rows_ref,
            contact_trace_ref=contact_trace_ref,
            representation_ref=representation_ref,
            runtime_lock_ref=runtime_lock_ref,
        )
        probe_report_ref = _publish_model(
            self.artifact_store,
            probe_report,
            "genesis-asset-probe-report.json",
            GENESIS_ASSET_PROBE_REPORT_SCHEMA_ID,
        )
        artifacts = GenesisAssetProbeArtifacts(
            probe_input=probe_input,
            probe_input_ref=probe_input_ref,
            load_observation=load_observation,
            load_observation_ref=load_observation_ref,
            load_evidence=load_evidence,
            load_evidence_ref=load_evidence_ref,
            rows=rows,
            trace_rows_ref=trace_rows_ref,
            contact_trace=contact_trace,
            contact_trace_ref=contact_trace_ref,
            probe_report=probe_report,
            probe_report_ref=probe_report_ref,
        )
        GenesisAssetProbeEvidenceVerifier(artifact_store=self.artifact_store).verify(
            artifacts,
            representation_ref=representation_ref,
            runtime_lock_ref=runtime_lock_ref,
        )
        return artifacts


@dataclass(frozen=True, slots=True)
class GenesisAssetProbeEvidenceVerifier:
    artifact_store: LocalArtifactStore
    max_trace_bytes: int = _MAX_TRACE_BYTES

    def __post_init__(self) -> None:
        if type(self.artifact_store) is not LocalArtifactStore:
            raise TypeError("artifact_store must be the exact LocalArtifactStore")
        if type(self.max_trace_bytes) is not int or self.max_trace_bytes <= 0:
            raise ValueError("max_trace_bytes must be a positive exact integer")

    def verify(
        self,
        artifacts: GenesisAssetProbeArtifacts,
        *,
        representation_ref: ArtifactRef,
        runtime_lock_ref: ArtifactRef,
    ) -> VerifiedGenesisAssetProbeEvidence:
        if type(artifacts) is not GenesisAssetProbeArtifacts:
            raise TypeError("artifacts must be the exact GenesisAssetProbeArtifacts")
        expected_names = (
            (artifacts.probe_input_ref, "genesis-asset-probe-input.json"),
            (artifacts.load_observation_ref, "genesis-asset-load-observation.json"),
            (artifacts.load_evidence_ref, "genesis-asset-load-evidence.json"),
            (artifacts.trace_rows_ref, "genesis-asset-contact-trace.ndjson"),
            (artifacts.contact_trace_ref, "genesis-asset-contact-trace.json"),
            (artifacts.probe_report_ref, "genesis-asset-probe-report.json"),
        )
        if any(ref.name != expected for ref, expected in expected_names):
            _mismatch("probe artifact identity differs from the canonical producer contract")
        representation = _read_model(
            self.artifact_store,
            representation_ref,
            AssetRepresentationV1,
            ASSET_REPRESENTATION_SCHEMA_ID,
        )
        runtime_lock = _read_model(
            self.artifact_store,
            runtime_lock_ref,
            GenesisRuntimeLockV1,
            GENESIS_RUNTIME_LOCK_SCHEMA_ID,
        )
        _verify_representation(self.artifact_store, representation_ref, representation)
        probe_input = self._read_equal(
            artifacts.probe_input_ref,
            artifacts.probe_input,
            GenesisAssetProbeInputV1,
            GENESIS_ASSET_PROBE_INPUT_SCHEMA_ID,
        )
        load_observation = self._read_equal(
            artifacts.load_observation_ref,
            artifacts.load_observation,
            GenesisAssetLoadObservationV1,
            GENESIS_ASSET_LOAD_OBSERVATION_SCHEMA_ID,
        )
        load_evidence = self._read_equal(
            artifacts.load_evidence_ref,
            artifacts.load_evidence,
            GenesisAssetLoadEvidenceV1,
            GENESIS_ASSET_LOAD_EVIDENCE_SCHEMA_ID,
        )
        contact_trace = self._read_equal(
            artifacts.contact_trace_ref,
            artifacts.contact_trace,
            GenesisAssetContactTraceV1,
            GENESIS_ASSET_CONTACT_TRACE_SCHEMA_ID,
        )
        probe_report = self._read_equal(
            artifacts.probe_report_ref,
            artifacts.probe_report,
            GenesisAssetProbeReportV1,
            GENESIS_ASSET_PROBE_REPORT_SCHEMA_ID,
        )
        rows = _read_rows(self.artifact_store, artifacts.trace_rows_ref, self.max_trace_bytes)
        if rows != artifacts.rows:
            _mismatch("trace row identity differs from the published artifacts")
        _verify_cross_layer(
            probe_input=probe_input,
            load_observation=load_observation,
            load_evidence=load_evidence,
            rows=rows,
            contact_trace=contact_trace,
            probe_report=probe_report,
            representation=representation,
            representation_ref=representation_ref,
            runtime_lock=runtime_lock,
            runtime_lock_ref=runtime_lock_ref,
            artifacts=artifacts,
        )
        supporting = _sorted_refs(
            (
                representation_ref,
                runtime_lock_ref,
                *tuple(member.artifact for member in representation.members),
                artifacts.probe_input_ref,
                artifacts.load_observation_ref,
                artifacts.load_evidence_ref,
                artifacts.trace_rows_ref,
                artifacts.contact_trace_ref,
                artifacts.probe_report_ref,
            )
        )
        return VerifiedGenesisAssetProbeEvidence(
            probe_input=probe_input,
            load_observation=load_observation,
            load_evidence=load_evidence,
            rows=rows,
            contact_trace=contact_trace,
            probe_report=probe_report,
            representation=representation,
            runtime_lock=runtime_lock,
            supporting_artifacts=supporting,
        )

    def _read_equal(
        self,
        ref: ArtifactRef,
        expected: BaseModel,
        model: type[BaseModel],
        schema: str,
    ):
        parsed = _read_model(self.artifact_store, ref, model, schema)
        if parsed != expected:
            _mismatch(f"{schema} differs from the supplied artifact graph")
        return parsed


def _verify_cross_layer(
    *,
    probe_input: GenesisAssetProbeInputV1,
    load_observation: GenesisAssetLoadObservationV1,
    load_evidence: GenesisAssetLoadEvidenceV1,
    rows: tuple[GenesisAssetContactTraceRowV1, ...],
    contact_trace: GenesisAssetContactTraceV1,
    probe_report: GenesisAssetProbeReportV1,
    representation: AssetRepresentationV1,
    representation_ref: ArtifactRef,
    runtime_lock: GenesisRuntimeLockV1,
    runtime_lock_ref: ArtifactRef,
    artifacts: GenesisAssetProbeArtifacts,
) -> None:
    if not _same_ref(probe_input.representation, representation_ref) or not _same_ref(
        probe_input.runtime_lock,
        runtime_lock_ref,
    ):
        _mismatch("probe input representation or runtime identity differs")
    if probe_input.requested_profile != runtime_lock.requested_profile.value:
        _mismatch("probe input profile differs from the runtime lock")
    if (
        not _same_ref(load_observation.probe_input, artifacts.probe_input_ref)
        or not _same_ref(load_observation.representation, representation_ref)
        or not _same_ref(load_observation.runtime_lock, runtime_lock_ref)
        or load_observation.requested_profile != probe_input.requested_profile
        or load_observation.backend != runtime_lock.backend
        or load_observation.backend_version != runtime_lock.backend_version
        or load_observation.backend_implementation_sha256
        != runtime_lock.runtime_implementation_sha256
        or load_observation.loader_contract != runtime_lock.loader_contract
        or load_observation.subject != representation.subject
        or load_observation.source_record_sha256 != representation.subject.source_record_sha256
        or load_observation.root_logical_path != representation.root_logical_path
        or load_observation.loader_closure_sha256 != representation.loader_closure_sha256
        or load_observation.collision_shape_members != representation.collision_member_paths
    ):
        _mismatch("raw load observation differs from representation or runtime authority")
    expected_load = _build_load_evidence(load_observation, artifacts.load_observation_ref)
    if load_evidence != expected_load:
        _mismatch("load evidence differs from its raw load observation")
    if load_observation.representation_sha256 != representation.representation_sha256:
        _mismatch("load observation representation identity differs")
    if load_observation.members != representation.members:
        _mismatch("load observation member closure differs")
    expected_trace = _build_contact_trace(
        probe_input_ref=artifacts.probe_input_ref,
        load_evidence_ref=artifacts.load_evidence_ref,
        representation_ref=representation_ref,
        runtime_lock_ref=runtime_lock_ref,
        trace_rows_ref=artifacts.trace_rows_ref,
        rows=rows,
    )
    if contact_trace != expected_trace:
        _mismatch("contact trace summary differs from canonical trace rows")
    expected_report = _build_report(
        probe_input_ref=artifacts.probe_input_ref,
        load_observation_ref=artifacts.load_observation_ref,
        load_evidence_ref=artifacts.load_evidence_ref,
        trace_rows_ref=artifacts.trace_rows_ref,
        contact_trace_ref=artifacts.contact_trace_ref,
        representation_ref=representation_ref,
        runtime_lock_ref=runtime_lock_ref,
    )
    if probe_report != expected_report:
        _mismatch("probe report checks or evidence differ from verified inputs")


def _build_load_observation(
    *,
    probe_input_ref: ArtifactRef,
    representation_ref: ArtifactRef,
    runtime_lock_ref: ArtifactRef,
    representation: AssetRepresentationV1,
    runtime_lock: GenesisRuntimeLockV1,
    load: GenesisAssetBackendLoad,
) -> GenesisAssetLoadObservationV1:
    return _self_hashed(
        GenesisAssetLoadObservationV1,
        "load_observation_sha256",
        {
            "schema_version": GENESIS_ASSET_LOAD_OBSERVATION_SCHEMA_ID,
            "probe_input": probe_input_ref,
            "representation": representation_ref,
            "runtime_lock": runtime_lock_ref,
            "requested_profile": "genesis.rigid_scene@1",
            "backend": "genesis",
            "backend_version": runtime_lock.backend_version,
            "backend_implementation_sha256": runtime_lock.runtime_implementation_sha256,
            "loader_contract": "harness.genesis_rigid_loader.v1",
            "subject": representation.subject,
            "source_record_sha256": representation.subject.source_record_sha256,
            "representation_sha256": representation.representation_sha256,
            "root_logical_path": representation.root_logical_path,
            "loader_closure_sha256": representation.loader_closure_sha256,
            "members": representation.members,
            "member_count": 15,
            "root_link_count": load.root_link_count,
            "joint_count": load.joint_count,
            "collision_shape_count": load.collision_shape_count,
            "collision_shape_members": load.collision_shape_members,
            "dynamic": load.dynamic,
            "fixed": load.fixed,
            "mass_kg": load.mass_kg,
            "inertia_kg_m2": load.inertia_kg_m2,
            "friction": load.friction,
            "world_up": (0.0, 0.0, 1.0),
            "gravity_m_s2": (0.0, 0.0, -9.81),
            "ground_plane_z_m": 0.0,
            "loaded_collision_min_z_m": load.loaded_collision_min_z_m,
            "test_translation_m": load.test_translation_m,
            "initial_clearance_m": 0.01,
            "release_height_m": 0.01,
            "loaded": True,
        },
    )


def _build_load_evidence(
    observation: GenesisAssetLoadObservationV1,
    observation_ref: ArtifactRef,
) -> GenesisAssetLoadEvidenceV1:
    values = observation.model_dump(mode="json")
    payload = {
        key: value
        for key, value in values.items()
        if key
        not in {
            "schema_version",
            "load_observation_sha256",
            "world_up",
            "gravity_m_s2",
            "ground_plane_z_m",
            "loaded_collision_min_z_m",
            "test_translation_m",
            "initial_clearance_m",
            "release_height_m",
        }
    }
    payload.update(
        schema_version=GENESIS_ASSET_LOAD_EVIDENCE_SCHEMA_ID,
        load_evidence_sha256="0" * 64,
        load_observation=observation_ref,
    )
    return _self_hashed(GenesisAssetLoadEvidenceV1, "load_evidence_sha256", payload)


def _row_from_backend(
    value: GenesisAssetBackendStep,
    expected_step: int,
) -> GenesisAssetContactTraceRowV1:
    if type(value) is not GenesisAssetBackendStep:
        _mismatch("backend step has the wrong type")
    expected_phase = "initial" if expected_step == 0 else "post_step"
    if value.step_index != expected_step or value.phase != expected_phase:
        _mismatch("backend step sequence is not contiguous")
    contacts = tuple(
        GenesisAssetGroundContactV1(
            pair="asset:ground",
            asset_collision_index=contact.asset_collision_index,
            ground_collision_index=contact.ground_collision_index,
            position_m=contact.position_m,
            normal_on_asset=contact.normal_on_asset,
            force_on_asset_n=contact.force_on_asset_n,
            force_on_ground_n=contact.force_on_ground_n,
            penetration_m=contact.penetration_m,
            normal_force_n=contact.normal_force_n,
        )
        for contact in value.asset_ground_contacts
    )
    try:
        return GenesisAssetContactTraceRowV1(
            schema_version=GENESIS_ASSET_CONTACT_TRACE_ROW_SCHEMA_ID,
            step_index=value.step_index,
            phase=value.phase,
            sim_time_us=value.step_index * 4_000,
            position_m=value.position_m,
            orientation_wxyz=value.orientation_wxyz,
            linear_velocity_m_s=value.linear_velocity_m_s,
            angular_velocity_rad_s=value.angular_velocity_rad_s,
            contacts=contacts,
            contact_count=len(contacts),
            total_normal_force_n=sum(contact.normal_force_n for contact in contacts),
            max_penetration_m=max((contact.penetration_m for contact in contacts), default=0.0),
        )
    except ValidationError as error:
        raise GenesisAssetProbeError(
            "HARN_GENESIS_ASSET_PROBE_EVIDENCE_MISMATCH",
            "backend step evidence is invalid",
        ) from error


def _build_contact_trace(
    *,
    probe_input_ref: ArtifactRef,
    load_evidence_ref: ArtifactRef,
    representation_ref: ArtifactRef,
    runtime_lock_ref: ArtifactRef,
    trace_rows_ref: ArtifactRef,
    rows: tuple[GenesisAssetContactTraceRowV1, ...],
) -> GenesisAssetContactTraceV1:
    if tuple(row.step_index for row in rows) != tuple(range(1001)):
        _mismatch("trace steps are not contiguous from zero through one thousand")
    contact_rows = tuple(row for row in rows[1:] if row.contacts)
    contacts = tuple(contact for row in rows[1:] for contact in row.contacts)
    if not contacts:
        raise GenesisAssetProbeError(
            "HARN_GENESIS_ASSET_PROBE_FAILED",
            "probe observed no candidate-ground contact",
        )
    max_penetration = max(contact.penetration_m for contact in contacts)
    if max_penetration > 0.001:
        raise GenesisAssetProbeError(
            "HARN_GENESIS_ASSET_PROBE_FAILED",
            "probe penetration exceeded the accepted threshold",
        )
    forces = tuple(contact.normal_force_n for contact in contacts)
    pairs = {contact.asset_collision_index for contact in contacts}
    return _self_hashed(
        GenesisAssetContactTraceV1,
        "contact_trace_sha256",
        {
            "schema_version": GENESIS_ASSET_CONTACT_TRACE_SCHEMA_ID,
            "probe_input": probe_input_ref,
            "load_evidence": load_evidence_ref,
            "representation": representation_ref,
            "runtime_lock": runtime_lock_ref,
            "trace_rows": trace_rows_ref,
            "first_step": 0,
            "last_step": 1000,
            "sample_count": 1001,
            "steps_contiguous": True,
            "contact_observed": True,
            "contact_step_count": len(contact_rows),
            "first_contact_step": contact_rows[0].step_index,
            "last_contact_step": contact_rows[-1].step_index,
            "total_contact_count": len(contacts),
            "collision_pair_count": len(pairs),
            "penetration_threshold_m": 0.001,
            "max_penetration_m": max_penetration,
            "penetration_violation_count": 0,
            "max_normal_force_n": max(forces),
            "mean_contact_normal_force_n": sum(forces) / len(forces),
            "total_normal_impulse_ns": sum(row.total_normal_force_n for row in rows[1:]) * 0.004,
            "collisions_enabled": True,
            "simulator_executed": True,
        },
    )


def _build_report(
    *,
    probe_input_ref: ArtifactRef,
    load_observation_ref: ArtifactRef,
    load_evidence_ref: ArtifactRef,
    trace_rows_ref: ArtifactRef,
    contact_trace_ref: ArtifactRef,
    representation_ref: ArtifactRef,
    runtime_lock_ref: ArtifactRef,
) -> GenesisAssetProbeReportV1:
    evidence = {
        "backend_load": (load_observation_ref, load_evidence_ref),
        "closure_identity": (representation_ref, runtime_lock_ref, load_evidence_ref),
        "collision_shapes": (load_observation_ref, load_evidence_ref),
        "contact_observed": (trace_rows_ref, contact_trace_ref),
        "dynamic_release": (probe_input_ref, load_observation_ref, trace_rows_ref),
        "penetration_bounded": (trace_rows_ref, contact_trace_ref),
    }
    return _self_hashed(
        GenesisAssetProbeReportV1,
        "probe_report_sha256",
        {
            "schema_version": GENESIS_ASSET_PROBE_REPORT_SCHEMA_ID,
            "probe_input": probe_input_ref,
            "load_evidence": load_evidence_ref,
            "contact_trace": contact_trace_ref,
            "representation": representation_ref,
            "runtime_lock": runtime_lock_ref,
            "requested_profile": "genesis.rigid_scene@1",
            "status": "pass",
            "checks": tuple(
                {
                    "code": code,
                    "status": "pass",
                    "evidence": _sorted_refs(refs),
                }
                for code, refs in sorted(evidence.items())
            ),
            "member_closure_verified": True,
            "load_executed": True,
            "contact_probe_executed": True,
            "steps_executed": 1000,
            "collisions_enabled": True,
            "simulator_executed": True,
            "model_call_count": 0,
            "network_call_count": 0,
        },
    )


def _verify_runtime(
    backend: GenesisAssetProbeBackend,
    runtime_ref: ArtifactRef,
    runtime: GenesisRuntimeLockV1,
) -> None:
    if (
        runtime.requested_profile.value != "genesis.rigid_scene@1"
        or runtime.backend != "genesis"
        or runtime.loader_contract != "harness.genesis_rigid_loader.v1"
        or backend.backend != runtime.backend
        or backend.backend_version != runtime.backend_version
        or backend.implementation_sha256 != runtime.runtime_implementation_sha256
        or backend.loader_contract != runtime.loader_contract
    ):
        raise GenesisAssetProbeError(
            "HARN_DEPENDENCY_DRIFT",
            "runtime profile or backend identity differs from the sealed runtime lock",
        )
    _require_ref(runtime_ref, GENESIS_RUNTIME_LOCK_SCHEMA_ID)


def _verify_backend_load(
    load: GenesisAssetBackendLoad,
    representation: AssetRepresentationV1,
) -> None:
    if type(load) is not GenesisAssetBackendLoad:
        _mismatch("backend load observation has the wrong type")
    expected_collisions = representation.collision_member_paths
    if (
        load.source != "urdf"
        or load.root_link_count != 1
        or load.joint_count != 0
        or load.collision_shape_count != 8
        or load.collision_shape_members != expected_collisions
        or load.dynamic is not True
        or load.fixed is not False
    ):
        _mismatch("backend load observation differs from the rigid loader contract")


def _verify_representation(
    store: LocalArtifactStore,
    representation_ref: ArtifactRef,
    representation: AssetRepresentationV1,
) -> None:
    _require_ref(representation_ref, ASSET_REPRESENTATION_SCHEMA_ID)
    if len(representation.members) != 15 or len(representation.collision_member_paths) != 8:
        _mismatch("representation is not the bounded fifteen-member rigid asset")
    for member in representation.members:
        _require_cas_ref(member.artifact)
        _resolve_bytes(store, member.artifact)


def _materialize_representation(
    store: LocalArtifactStore,
    representation: AssetRepresentationV1,
    root: Path,
) -> None:
    for member in representation.members:
        logical = PurePosixPath(member.logical_path)
        if logical.is_absolute() or ".." in logical.parts or not logical.parts:
            _mismatch("representation member has an unsafe logical path")
        target = root.joinpath(*logical.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_resolve_bytes(store, member.artifact))


def _read_rows(
    store: LocalArtifactStore,
    ref: ArtifactRef,
    max_bytes: int,
) -> tuple[GenesisAssetContactTraceRowV1, ...]:
    _require_ref(ref, CONTACT_TRACE_ROWS_SCHEMA_ID, media_type="application/x-ndjson")
    payload = _resolve_bytes(store, ref)
    if not payload or len(payload) > max_bytes or not payload.endswith(b"\n") or b"\r" in payload:
        _mismatch("trace rows are absent, oversized, or noncanonical")
    lines = payload.splitlines()
    if len(lines) != 1001 or any(not line for line in lines):
        _mismatch("trace row count is not exactly one thousand and one")
    rows: list[GenesisAssetContactTraceRowV1] = []
    for line in lines:
        try:
            row = GenesisAssetContactTraceRowV1.model_validate_json(line)
        except ValidationError as error:
            raise GenesisAssetProbeError(
                "HARN_GENESIS_ASSET_PROBE_EVIDENCE_MISMATCH",
                "trace row failed its strict schema",
            ) from error
        if _canonical_json_bytes(row.model_dump(mode="json")).rstrip(b"\n") != line:
            _mismatch("trace row is not canonical JSON")
        rows.append(row)
    return tuple(rows)


def _read_model(store: LocalArtifactStore, ref: ArtifactRef, model: type[BaseModel], schema: str):
    _require_ref(ref, schema)
    payload = _resolve_bytes(store, ref)
    try:
        parsed = model.model_validate_json(payload)
    except ValidationError as error:
        raise GenesisAssetProbeError(
            "HARN_GENESIS_ASSET_PROBE_EVIDENCE_MISMATCH",
            f"{schema} failed its strict schema",
        ) from error
    if _canonical_json_bytes(parsed.model_dump(mode="json")) != payload:
        _mismatch(f"{schema} is not canonical JSON")
    return parsed


def _resolve_bytes(store: LocalArtifactStore, ref: ArtifactRef) -> bytes:
    _require_cas_ref(ref)
    try:
        payload = store.resolve(ref).path.read_bytes()
    except (ArtifactResolutionError, OSError) as error:
        raise GenesisAssetProbeError(
            "HARN_ARTIFACT_UNAVAILABLE",
            "probe artifact is unavailable or corrupt",
        ) from error
    if len(payload) != ref.bytes or hashlib.sha256(payload).hexdigest() != ref.sha256:
        raise GenesisAssetProbeError(
            "HARN_ARTIFACT_UNAVAILABLE",
            "probe artifact bytes differ from their declared identity",
        )
    return payload


def _require_ref(ref: ArtifactRef, schema: str, *, media_type: str = "application/json") -> None:
    if type(ref) is not ArtifactRef:
        raise TypeError("probe evidence references must be exact ArtifactRef values")
    _require_cas_ref(ref)
    if ref.media_type != media_type or ref.schema_version != schema:
        _mismatch("probe artifact media type or schema identity differs")


def _require_cas_ref(ref: ArtifactRef) -> None:
    if ref.uri != f"artifact://sha256/{ref.sha256}":
        _mismatch("probe artifact identity is not content-addressed")


def _self_hashed(model: type[BaseModel], digest_field: str, payload: dict[str, object]):
    value = json.loads(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            default=lambda item: item.model_dump(mode="json"),
        )
    )
    value[digest_field] = "0" * 64
    value[digest_field] = canonical_evidence_sha256(value, digest_field)
    try:
        return model.model_validate(value)
    except ValidationError as error:
        raise GenesisAssetProbeError(
            "HARN_GENESIS_ASSET_PROBE_EVIDENCE_MISMATCH",
            f"failed to construct {model.__name__}",
        ) from error


def _publish_model(
    store: LocalArtifactStore,
    value: BaseModel,
    name: str,
    schema: str,
) -> ArtifactRef:
    return _publish_bytes(
        store,
        _canonical_json_bytes(value.model_dump(mode="json")),
        name,
        "application/json",
        schema,
    )


def _publish_bytes(
    store: LocalArtifactStore,
    payload: bytes,
    name: str,
    media_type: str,
    schema: str,
) -> ArtifactRef:
    with tempfile.TemporaryDirectory(prefix="genesis-asset-probe-publish-") as directory:
        path = Path(directory) / "payload"
        path.write_bytes(payload)
        return store.put_file(
            path,
            name=name,
            media_type=media_type,
            schema_version=schema,
        )


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _sorted_refs(refs: tuple[ArtifactRef, ...]) -> tuple[ArtifactRef, ...]:
    return tuple(sorted(refs, key=lambda ref: (ref.sha256, ref.bytes, ref.media_type)))


def _same_ref(left: ArtifactRef, right: ArtifactRef) -> bool:
    return left.model_dump(mode="json") == right.model_dump(mode="json")


def _mismatch(message: str) -> None:
    raise GenesisAssetProbeError("HARN_GENESIS_ASSET_PROBE_EVIDENCE_MISMATCH", message)


__all__ = [
    "GenesisAssetBackendLoad",
    "GenesisAssetBackendStep",
    "GenesisAssetGroundContact",
    "GenesisAssetProbeArtifacts",
    "GenesisAssetProbeBackend",
    "GenesisAssetProbeError",
    "GenesisAssetProbeEvidenceVerifier",
    "GenesisAssetProbeLoadRequest",
    "GenesisAssetProbeProducer",
    "VerifiedGenesisAssetProbeEvidence",
]
