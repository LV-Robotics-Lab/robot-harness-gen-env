from __future__ import annotations

import json
from dataclasses import fields, replace
from pathlib import Path
from typing import Any

import pytest

import self_improving.harness as harness
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.schemas.common import ArtifactRef, DependencyRef
from self_improving.harness.schemas.x2env_compile_evidence import (
    AssetModelSubjectV1,
    AssetRepresentationMemberV1,
    AssetRepresentationV1,
    GenesisRuntimeLockV1,
    asset_loader_closure_sha256,
    canonical_evidence_sha256,
)

try:
    from self_improving.harness.genesis_asset_probe import (
        GenesisAssetBackendLoad,
        GenesisAssetBackendStep,
        GenesisAssetGroundContact,
        GenesisAssetProbeArtifacts,
        GenesisAssetProbeBackend,
        GenesisAssetProbeError,
        GenesisAssetProbeEvidenceVerifier,
        GenesisAssetProbeLoadRequest,
        GenesisAssetProbeProducer,
        VerifiedGenesisAssetProbeEvidence,
    )
except ImportError:
    GenesisAssetBackendLoad = None  # type: ignore[assignment,misc]
    GenesisAssetBackendStep = None  # type: ignore[assignment,misc]
    GenesisAssetGroundContact = None  # type: ignore[assignment,misc]
    GenesisAssetProbeArtifacts = None  # type: ignore[assignment,misc]
    GenesisAssetProbeBackend = None  # type: ignore[assignment,misc]
    GenesisAssetProbeError = None  # type: ignore[assignment,misc]
    GenesisAssetProbeEvidenceVerifier = None  # type: ignore[assignment,misc]
    GenesisAssetProbeLoadRequest = None  # type: ignore[assignment,misc]
    GenesisAssetProbeProducer = None  # type: ignore[assignment,misc]
    VerifiedGenesisAssetProbeEvidence = None  # type: ignore[assignment,misc]

try:
    from self_improving.harness.schemas.genesis_asset_probe import (
        GenesisAssetContactTraceRowV1,
        GenesisAssetLoadObservationV1,
    )
except ImportError:
    GenesisAssetContactTraceRowV1 = None  # type: ignore[assignment,misc]
    GenesisAssetLoadObservationV1 = None  # type: ignore[assignment,misc]


_RUNTIME_IMPLEMENTATION_SHA256 = "3" * 64
_SOURCE_RECORD_SHA256 = "5" * 64
_REPRESENTATION_SCHEMA = "harness.asset_representation.v1"
_RUNTIME_SCHEMA = "harness.genesis_runtime_lock.v1"
_LOAD_OBSERVATION_SCHEMA = "harness.genesis_asset_load_observation.v1"
_ROW_SCHEMA = "harness.genesis_asset_contact_trace_row.v1"
_REPORT_SCHEMA = "harness.genesis_asset_probe_report.v1"
_QUALIFICATION_SCHEMA = "harness.asset_qualification.v1"


def _require_api() -> None:
    expected = {
        "GenesisAssetBackendLoad": GenesisAssetBackendLoad,
        "GenesisAssetBackendStep": GenesisAssetBackendStep,
        "GenesisAssetGroundContact": GenesisAssetGroundContact,
        "GenesisAssetProbeArtifacts": GenesisAssetProbeArtifacts,
        "GenesisAssetProbeBackend": GenesisAssetProbeBackend,
        "GenesisAssetProbeError": GenesisAssetProbeError,
        "GenesisAssetProbeEvidenceVerifier": GenesisAssetProbeEvidenceVerifier,
        "GenesisAssetProbeLoadRequest": GenesisAssetProbeLoadRequest,
        "GenesisAssetProbeProducer": GenesisAssetProbeProducer,
        "VerifiedGenesisAssetProbeEvidence": VerifiedGenesisAssetProbeEvidence,
        "GenesisAssetContactTraceRowV1": GenesisAssetContactTraceRowV1,
        "GenesisAssetLoadObservationV1": GenesisAssetLoadObservationV1,
    }
    for name, value in expected.items():
        assert value is not None, f"public Genesis asset probe seam is absent: {name}"
        assert getattr(harness, name) is value
        assert name in harness.__all__
    assert GenesisAssetContactTraceRowV1.model_json_schema()["$id"] == _ROW_SCHEMA
    assert GenesisAssetLoadObservationV1.model_json_schema()["$id"] == _LOAD_OBSERVATION_SCHEMA


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
        + "\n"
    ).encode()


def _put_bytes(
    store: LocalArtifactStore,
    scratch: Path,
    *,
    name: str,
    payload: bytes,
    media_type: str,
    schema_version: str | None,
) -> ArtifactRef:
    source = scratch / f"source-{len(tuple(scratch.iterdir())):03d}"
    source.write_bytes(payload)
    return store.put_file(
        source,
        name=name,
        media_type=media_type,
        schema_version=schema_version,
    )


def _put_document(
    store: LocalArtifactStore,
    scratch: Path,
    value: Any,
    *,
    name: str,
    schema_version: str,
) -> ArtifactRef:
    return _put_bytes(
        store,
        scratch,
        name=name,
        payload=_canonical_json(value.model_dump(mode="json")),
        media_type="application/json",
        schema_version=schema_version,
    )


def _representation(
    store: LocalArtifactStore,
    scratch: Path,
) -> tuple[AssetRepresentationV1, ArtifactRef]:
    paths_and_roles = [
        ("physics.json", "metadata"),
        ("urdf/mouse.urdf", "urdf"),
        *((f"urdf/shape/collision/mouse_{index}.obj", "collision") for index in range(8)),
        ("urdf/material/mouse_Kd.png", "visual"),
        ("urdf/material/mouse_Pm.png", "visual"),
        ("urdf/material/mouse_Pr.png", "visual"),
        ("urdf/shape/visual/mouse.mtl", "visual"),
        ("urdf/shape/visual/mouse.obj", "visual"),
    ]
    members = tuple(
        AssetRepresentationMemberV1(
            logical_path=path,
            role=role,
            artifact=_put_bytes(
                store,
                scratch,
                name=path,
                payload=f"audited-mouse-member:{path}\n".encode(),
                media_type=(
                    "application/json" if role == "metadata" else "application/octet-stream"
                ),
                schema_version=None,
            ),
        )
        for path, role in sorted(paths_and_roles)
    )
    subject = AssetModelSubjectV1(
        namespace="gujie_genenv_legacy",
        asset_key="simfoundry_pink_mouse_7915827ae4309660",
        ledger_asset_id="legacy_mouse_001",
        model_id=0,
        backend="genesis",
        source_record_sha256=_SOURCE_RECORD_SHA256,
    )
    payload: dict[str, object] = {
        "schema_version": _REPRESENTATION_SCHEMA,
        "representation_sha256": "0" * 64,
        "subject": subject,
        "representation_kind": "urdf",
        "root_logical_path": "urdf/mouse.urdf",
        "loader_scale": (1.0, 1.0, 1.0),
        "loader_closure_sha256": asset_loader_closure_sha256(
            subject,
            "urdf/mouse.urdf",
            members,
        ),
        "members": members,
        "collision_member_paths": tuple(
            member.logical_path for member in members if member.role == "collision"
        ),
        "visual_member_paths": tuple(
            member.logical_path for member in members if member.role == "visual"
        ),
    }
    payload["representation_sha256"] = canonical_evidence_sha256(
        payload,
        "representation_sha256",
    )
    representation = AssetRepresentationV1.model_validate(payload)
    return representation, _put_document(
        store,
        scratch,
        representation,
        name="legacy-mouse-representation.json",
        schema_version=_REPRESENTATION_SCHEMA,
    )


def _runtime_lock(
    store: LocalArtifactStore,
    scratch: Path,
    *,
    requested_profile: str = "genesis.rigid_scene@1",
) -> tuple[GenesisRuntimeLockV1, ArtifactRef]:
    payload: dict[str, object] = {
        "schema_version": _RUNTIME_SCHEMA,
        "runtime_lock_sha256": "0" * 64,
        "backend": "genesis",
        "backend_version": "0.3.3",
        "runtime_implementation_sha256": _RUNTIME_IMPLEMENTATION_SHA256,
        "loader_contract": "harness.genesis_rigid_loader.v1",
        "requested_profile": requested_profile,
        "dependencies": (
            DependencyRef(
                name="genesis-cpu",
                version="0.3.3",
                sha256="4" * 64,
            ),
        ),
    }
    payload["runtime_lock_sha256"] = canonical_evidence_sha256(
        payload,
        "runtime_lock_sha256",
    )
    runtime_lock = GenesisRuntimeLockV1.model_validate(payload)
    return runtime_lock, _put_document(
        store,
        scratch,
        runtime_lock,
        name="genesis-runtime-lock.json",
        schema_version=_RUNTIME_SCHEMA,
    )


class ControlledCpuGenesisBackend:
    backend = "genesis"
    backend_version = "0.3.3"
    implementation_sha256 = _RUNTIME_IMPLEMENTATION_SHA256
    loader_contract = "harness.genesis_rigid_loader.v1"

    def __init__(self, *, failure: str | None = None) -> None:
        self.failure = failure
        self.load_requests: list[Any] = []
        self.initial_observation_count = 0
        self.step_requests: list[int] = []

    def load(self, request: Any) -> Any:
        assert GenesisAssetBackendLoad is not None
        self.load_requests.append(request)
        assert request.root_path == request.materialized_root / "urdf/mouse.urdf"
        assert request.root_path.read_bytes() == b"audited-mouse-member:urdf/mouse.urdf\n"
        return GenesisAssetBackendLoad(
            source="urdf",
            root_link_count=1,
            joint_count=0,
            collision_shape_count=8,
            dynamic=True,
            fixed=False,
            mass_kg=0.07,
            inertia_kg_m2={
                "ixx": 0.00014230592971984656,
                "ixy": 4.0694193088405128e-07,
                "ixz": -4.1828650840502395e-07,
                "iyy": 5.5402188287928484e-05,
                "iyz": 2.4776380627811414e-06,
                "izz": 0.00016461930323691639,
            },
            friction=0.2,
        )

    def observe_initial(self) -> Any:
        self.initial_observation_count += 1
        return self._observation(0, "initial")

    def step(self, step_index: int) -> Any:
        self.step_requests.append(step_index)
        return self._observation(step_index, "post_step")

    def _observation(self, step_index: int, phase: str) -> Any:
        assert GenesisAssetBackendStep is not None
        assert GenesisAssetGroundContact is not None
        returned_step = (
            501 if self.failure == "non_contiguous" and step_index == 500 else step_index
        )
        penetration = 0.002 if self.failure == "penetration" and step_index == 500 else 0.0004
        contacts = (
            ()
            if step_index < 125
            else (
                GenesisAssetGroundContact(
                    asset_collision_index=step_index % 8,
                    penetration_m=penetration,
                    normal_force_n=0.7,
                ),
            )
        )
        return GenesisAssetBackendStep(
            step_index=returned_step,
            phase=phase,
            position_m=(0.0, 0.0, max(0.0, 0.01 - step_index * 0.00008)),
            orientation_wxyz=(1.0, 0.0, 0.0, 0.0),
            linear_velocity_m_s=(0.0, 0.0, 0.0),
            angular_velocity_rad_s=(0.0, 0.0, 0.0),
            asset_ground_contacts=contacts,
        )


def _cas_objects(store: LocalArtifactStore) -> tuple[Path, ...]:
    return tuple(sorted(path for path in store.root.glob("sha256/*/*") if path.is_file()))


def _json_schema_versions(store: LocalArtifactStore) -> tuple[str, ...]:
    versions: list[str] = []
    for path in _cas_objects(store):
        try:
            value = json.loads(path.read_bytes())
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if type(value) is dict and type(value.get("schema_version")) is str:
            versions.append(value["schema_version"])
    return tuple(versions)


def test_cpu_probe_produces_and_deep_verifies_full_canonical_cas_evidence(
    tmp_path: Path,
) -> None:
    _require_api()
    assert GenesisAssetProbeBackend is not None
    assert GenesisAssetProbeProducer is not None
    assert GenesisAssetProbeEvidenceVerifier is not None
    assert GenesisAssetProbeArtifacts is not None
    assert VerifiedGenesisAssetProbeEvidence is not None
    assert GenesisAssetContactTraceRowV1 is not None
    store = LocalArtifactStore(tmp_path / "cas")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    representation, representation_ref = _representation(store, scratch)
    runtime_lock, runtime_lock_ref = _runtime_lock(store, scratch)
    backend = ControlledCpuGenesisBackend()
    assert isinstance(backend, GenesisAssetProbeBackend)
    before = len(_cas_objects(store))

    result = GenesisAssetProbeProducer(artifact_store=store, backend=backend).produce(
        representation_ref=representation_ref,
        runtime_lock_ref=runtime_lock_ref,
    )

    assert type(result) is GenesisAssetProbeArtifacts
    assert len(backend.load_requests) == 1
    assert backend.initial_observation_count == 1
    assert backend.step_requests == list(range(1, 1001))
    request = backend.load_requests[0]
    assert type(request) is GenesisAssetProbeLoadRequest
    assert tuple(field.name for field in fields(request)) == (
        "probe_input",
        "representation",
        "runtime_lock",
        "materialized_root",
        "root_path",
    )
    assert not {"camera", "render", "image", "video", "media"}.intersection(
        field.name for field in fields(request)
    )
    assert request.representation == representation
    assert request.runtime_lock == runtime_lock
    assert request.probe_input.steps == 1000
    assert request.probe_input.dt_seconds == 0.004
    assert request.probe_input.release_height_m == 0.01

    assert result.load_evidence.load_observation == result.load_observation_ref
    assert result.contact_trace.trace_rows == result.trace_rows_ref
    assert result.trace_rows_ref.media_type == "application/x-ndjson"
    rows_payload = store.resolve(result.trace_rows_ref).path.read_bytes()
    assert rows_payload.endswith(b"\n")
    row_lines = rows_payload.splitlines()
    assert len(row_lines) == 1001
    rows = tuple(GenesisAssetContactTraceRowV1.model_validate_json(line) for line in row_lines)
    assert [row.step_index for row in rows] == list(range(1001))
    assert [row.sim_time_us for row in rows] == [step * 4000 for step in range(1001)]
    assert rows[0].phase == "initial"
    assert all(row.phase == "post_step" for row in rows[1:])
    assert all(
        _canonical_json(row.model_dump(mode="json")).rstrip(b"\n") == line
        for row, line in zip(rows, row_lines, strict=True)
    )
    assert result.contact_trace.first_step == 0
    assert result.contact_trace.last_step == 1000
    assert result.contact_trace.sample_count == 1001
    assert result.contact_trace.contact_step_count == 876
    assert result.contact_trace.total_contact_count == 876
    assert result.contact_trace.collision_pair_count == 8
    assert result.contact_trace.max_penetration_m == 0.0004
    assert result.contact_trace.max_normal_force_n == 0.7
    assert result.probe_report.steps_executed == 1000
    assert len(_cas_objects(store)) == before + 6

    verified = GenesisAssetProbeEvidenceVerifier(artifact_store=store).verify(
        result,
        representation_ref=representation_ref,
        runtime_lock_ref=runtime_lock_ref,
    )
    assert type(verified) is VerifiedGenesisAssetProbeEvidence
    assert verified.probe_report == result.probe_report
    assert verified.rows == rows
    assert verified.representation == representation
    assert verified.runtime_lock == runtime_lock

    foreign_rows_ref = replace(
        result.trace_rows_ref,
        name="foreign-trace.ndjson",
    )
    with pytest.raises(GenesisAssetProbeError, match="trace|row|identity"):
        GenesisAssetProbeEvidenceVerifier(artifact_store=store).verify(
            replace(result, trace_rows_ref=foreign_rows_ref),
            representation_ref=representation_ref,
            runtime_lock_ref=runtime_lock_ref,
        )


def test_probe_preflight_rejects_representation_runtime_and_profile_before_backend(
    tmp_path: Path,
) -> None:
    _require_api()
    assert GenesisAssetProbeProducer is not None
    assert GenesisAssetProbeError is not None
    store = LocalArtifactStore(tmp_path / "cas")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _, representation_ref = _representation(store, scratch)
    _, runtime_lock_ref = _runtime_lock(store, scratch)
    _, policy_runtime_ref = _runtime_lock(
        store,
        scratch,
        requested_profile="genesis.robot_policy@1",
    )

    cases = (
        (
            replace(representation_ref, schema_version="harness.other_representation.v1"),
            runtime_lock_ref,
            _RUNTIME_IMPLEMENTATION_SHA256,
        ),
        (representation_ref, policy_runtime_ref, _RUNTIME_IMPLEMENTATION_SHA256),
        (representation_ref, runtime_lock_ref, "f" * 64),
    )
    for candidate_representation, candidate_runtime, backend_sha256 in cases:
        backend = ControlledCpuGenesisBackend()
        backend.implementation_sha256 = backend_sha256
        producer = GenesisAssetProbeProducer(artifact_store=store, backend=backend)
        before = len(_cas_objects(store))
        with pytest.raises(GenesisAssetProbeError, match="representation|runtime|profile|identity"):
            producer.produce(
                representation_ref=candidate_representation,
                runtime_lock_ref=candidate_runtime,
            )
        assert backend.load_requests == []
        assert backend.initial_observation_count == 0
        assert backend.step_requests == []
        assert len(_cas_objects(store)) == before


def test_probe_failure_returns_no_report_or_qualification(
    tmp_path: Path,
) -> None:
    _require_api()
    assert GenesisAssetProbeProducer is not None
    assert GenesisAssetProbeError is not None
    for failure in ("non_contiguous", "penetration"):
        case_root = tmp_path / failure
        store = LocalArtifactStore(case_root / "cas")
        scratch = case_root / "scratch"
        scratch.mkdir(parents=True)
        _, representation_ref = _representation(store, scratch)
        _, runtime_lock_ref = _runtime_lock(store, scratch)
        backend = ControlledCpuGenesisBackend(failure=failure)

        with pytest.raises(GenesisAssetProbeError, match="step|contiguous|penetration"):
            GenesisAssetProbeProducer(artifact_store=store, backend=backend).produce(
                representation_ref=representation_ref,
                runtime_lock_ref=runtime_lock_ref,
            )

        assert len(backend.load_requests) == 1
        assert _REPORT_SCHEMA not in _json_schema_versions(store)
        assert _QUALIFICATION_SCHEMA not in _json_schema_versions(store)
