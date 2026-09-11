from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

import self_improving.harness as harness
import self_improving.harness.schemas as public_schemas
from self_improving.harness.schema_catalog import (
    DEFAULT_SCHEMA_ROOT,
    render_schema_document,
    schema_documents,
    schema_snapshot_name,
)
from self_improving.harness.schemas.common import ArtifactRef
from self_improving.harness.schemas.x2env_compile_evidence import (
    asset_loader_closure_sha256,
    canonical_evidence_sha256,
)

try:
    from self_improving.harness.schemas.genesis_asset_probe import (
        GenesisAssetContactTraceV1,
        GenesisAssetLoadEvidenceV1,
        GenesisAssetProbeInputV1,
        GenesisAssetProbeReportV1,
    )
except ImportError:
    GenesisAssetContactTraceV1 = None  # type: ignore[assignment,misc]
    GenesisAssetLoadEvidenceV1 = None  # type: ignore[assignment,misc]
    GenesisAssetProbeInputV1 = None  # type: ignore[assignment,misc]
    GenesisAssetProbeReportV1 = None  # type: ignore[assignment,misc]

try:
    from self_improving.harness.schemas.genesis_asset_probe import (
        GenesisAssetContactTraceRowV1,
        GenesisAssetLoadObservationV1,
    )
except ImportError:
    GenesisAssetContactTraceRowV1 = None  # type: ignore[assignment,misc]
    GenesisAssetLoadObservationV1 = None  # type: ignore[assignment,misc]


_SCHEMA_MODELS = {
    "harness.genesis_asset_probe_input.v1": GenesisAssetProbeInputV1,
    "harness.genesis_asset_load_evidence.v1": GenesisAssetLoadEvidenceV1,
    "harness.genesis_asset_contact_trace.v1": GenesisAssetContactTraceV1,
    "harness.genesis_asset_probe_report.v1": GenesisAssetProbeReportV1,
}


def _require_api() -> None:
    for schema_id, model in _SCHEMA_MODELS.items():
        assert model is not None, f"{schema_id} is not implemented"
        name = model.__name__
        assert getattr(public_schemas, name) is model
        assert getattr(harness, name) is model
        assert name in public_schemas.__all__
        assert name in harness.__all__
        assert model.model_json_schema()["$id"] == schema_id


def _ref(
    name: str,
    schema_version: str | None,
    *,
    media_type: str = "application/json",
) -> ArtifactRef:
    digest = hashlib.sha256(f"{name}:{schema_version}:{media_type}".encode()).hexdigest()
    return ArtifactRef(
        name=name,
        uri=f"artifact://sha256/{digest}",
        media_type=media_type,
        sha256=digest,
        bytes=97,
        schema_version=schema_version,
    )


def _self_hashed(model: Any, digest_field: str, value: dict[str, object]) -> Any:
    payload = json.loads(
        json.dumps(
            value,
            default=lambda item: item.model_dump(mode="json"),
        )
    )
    payload[digest_field] = canonical_evidence_sha256(payload, digest_field)
    return model.model_validate(payload)


def _members() -> list[dict[str, object]]:
    paths_and_roles = (
        ("physics.json", "metadata"),
        ("urdf/mouse.urdf", "urdf"),
        ("urdf/material/mouse_Kd.png", "visual"),
        ("urdf/material/mouse_Pm.png", "visual"),
        ("urdf/material/mouse_Pr.png", "visual"),
        *((f"urdf/shape/collision/mouse_collision_{index}.obj", "collision") for index in range(8)),
        ("urdf/shape/visual/mouse.mtl", "visual"),
        ("urdf/shape/visual/mouse.obj", "visual"),
    )
    return [
        {
            "logical_path": path,
            "role": role,
            "artifact": _ref(
                f"member-{index:02d}",
                None,
                media_type=(
                    "application/json" if role == "metadata" else "application/octet-stream"
                ),
            ),
        }
        for index, (path, role) in enumerate(sorted(paths_and_roles))
    ]


@pytest.fixture
def documents() -> dict[str, Any]:
    _require_api()
    assert GenesisAssetProbeInputV1 is not None
    assert GenesisAssetLoadEvidenceV1 is not None
    assert GenesisAssetContactTraceV1 is not None
    assert GenesisAssetProbeReportV1 is not None
    representation_ref = _ref("representation", "harness.asset_representation.v1")
    runtime_lock_ref = _ref("runtime-lock", "harness.genesis_runtime_lock.v1")
    probe_input = _self_hashed(
        GenesisAssetProbeInputV1,
        "probe_input_sha256",
        {
            "schema_version": "harness.genesis_asset_probe_input.v1",
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
    probe_input_ref = _ref("probe-input", "harness.genesis_asset_probe_input.v1")
    subject = {
        "namespace": "gujie_genenv_legacy",
        "asset_key": "simfoundry_pink_mouse_7915827ae4309660",
        "ledger_asset_id": "legacy_mouse_001",
        "model_id": 0,
        "backend": "genesis",
        "source_record_sha256": "5" * 64,
    }
    members = _members()
    loader_closure_sha256 = asset_loader_closure_sha256(
        subject,
        "urdf/mouse.urdf",
        members,
    )
    load = _self_hashed(
        GenesisAssetLoadEvidenceV1,
        "load_evidence_sha256",
        {
            "schema_version": "harness.genesis_asset_load_evidence.v1",
            "probe_input": probe_input_ref,
            "load_observation": _ref(
                "load-observation",
                "harness.genesis_asset_load_observation.v1",
            ),
            "representation": representation_ref,
            "runtime_lock": runtime_lock_ref,
            "requested_profile": "genesis.rigid_scene@1",
            "backend": "genesis",
            "backend_version": "0.3.3",
            "backend_implementation_sha256": "3" * 64,
            "loader_contract": "harness.genesis_rigid_loader.v1",
            "subject": subject,
            "source_record_sha256": subject["source_record_sha256"],
            "representation_sha256": "6" * 64,
            "root_logical_path": "urdf/mouse.urdf",
            "loader_closure_sha256": loader_closure_sha256,
            "members": members,
            "member_count": 15,
            "source_link_count": 1,
            "source_joint_count": 0,
            "loaded_link_count": 1,
            "loaded_joint_count": 1,
            "loaded_dof_count": 6,
            "source_collision_member_count": 8,
            "source_collision_members": tuple(
                item["logical_path"] for item in members if item["role"] == "collision"
            ),
            "loaded_collision_shape_count": 1,
            "loaded_collision_shapes_merged": True,
            "loaded_collision_vertex_count": 502,
            "loaded_collision_face_count": 1000,
            "dynamic": True,
            "fixed": False,
            "mass_kg": 0.07,
            "inertia_kg_m2": {
                "ixx": 0.00014230592971984656,
                "ixy": 4.0694193088405128e-07,
                "ixz": -4.1828650840502395e-07,
                "iyy": 5.5402188287928484e-05,
                "iyz": 2.4776380627811414e-06,
                "izz": 0.00016461930323691639,
            },
            "friction": 0.2,
            "loaded": True,
        },
    )
    load_ref = _ref("load-evidence", "harness.genesis_asset_load_evidence.v1")
    trace_rows = _ref(
        "contact-trace",
        "harness.genesis_asset_contact_trace_row.v1",
        media_type="application/x-ndjson",
    )
    trace = _self_hashed(
        GenesisAssetContactTraceV1,
        "contact_trace_sha256",
        {
            "schema_version": "harness.genesis_asset_contact_trace.v1",
            "probe_input": probe_input_ref,
            "load_evidence": load_ref,
            "representation": representation_ref,
            "runtime_lock": runtime_lock_ref,
            "trace_rows": trace_rows,
            "first_step": 0,
            "last_step": 1000,
            "sample_count": 1001,
            "steps_contiguous": True,
            "contact_observed": True,
            "contact_step_count": 875,
            "first_contact_step": 125,
            "last_contact_step": 999,
            "total_contact_count": 880,
            "collision_pair_count": 1,
            "penetration_threshold_m": 0.001,
            "max_penetration_m": 0.0004,
            "penetration_violation_count": 0,
            "max_normal_force_n": 2.1,
            "mean_contact_normal_force_n": 0.687,
            "total_normal_impulse_ns": 5.49,
            "final_linear_speed_m_s": 0.0001,
            "final_angular_speed_rad_s": 0.0002,
            "settled_linear_speed_threshold_m_s": 0.01,
            "settled_angular_speed_threshold_rad_s": 0.05,
            "settled": True,
            "collisions_enabled": True,
            "simulator_executed": True,
        },
    )
    trace_ref = _ref("trace-evidence", "harness.genesis_asset_contact_trace.v1")
    check_evidence = {
        "backend_load": (load_ref,),
        "closure_identity": tuple(
            sorted((representation_ref, load_ref), key=lambda item: item.sha256)
        ),
        "collision_shapes": (load_ref,),
        "contact_observed": (trace_ref,),
        "dynamic_release": tuple(
            sorted((probe_input_ref, trace_ref), key=lambda item: item.sha256)
        ),
        "penetration_bounded": (trace_ref,),
        "settled": (trace_ref,),
    }
    report = _self_hashed(
        GenesisAssetProbeReportV1,
        "probe_report_sha256",
        {
            "schema_version": "harness.genesis_asset_probe_report.v1",
            "probe_input": probe_input_ref,
            "load_evidence": load_ref,
            "contact_trace": trace_ref,
            "representation": representation_ref,
            "runtime_lock": runtime_lock_ref,
            "requested_profile": "genesis.rigid_scene@1",
            "status": "pass",
            "checks": [
                {"code": code, "status": "pass", "evidence": refs}
                for code, refs in sorted(check_evidence.items())
            ],
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
    return {
        "input": probe_input,
        "load": load,
        "trace": trace,
        "report": report,
    }


def _rehash(model: Any, value: dict[str, Any]) -> Any:
    digest_field = next(name for name in value if name.endswith("_sha256"))
    value[digest_field] = canonical_evidence_sha256(value, digest_field)
    return model.model_validate(value)


def test_asset_probe_models_are_strict_self_hashed_public_documents(
    documents: dict[str, Any],
) -> None:
    assert len(documents) == 4
    for value in documents.values():
        dumped = value.model_dump(mode="json")
        assert type(value).model_validate_json(json.dumps(dumped)) == value
        with pytest.raises(ValidationError, match="extra"):
            type(value).model_validate({**dumped, "extra": True})
        digest_field = next(name for name in dumped if name.endswith("_sha256"))
        with pytest.raises(ValidationError, match="digest is inconsistent"):
            type(value).model_validate({**dumped, digest_field: "f" * 64})


def test_raw_load_and_trace_row_schemas_remove_probe_recomputation_ambiguity() -> None:
    assert GenesisAssetLoadObservationV1 is not None
    assert GenesisAssetContactTraceRowV1 is not None
    assert getattr(public_schemas, "GenesisAssetLoadObservationV1") is (
        GenesisAssetLoadObservationV1
    )
    assert getattr(public_schemas, "GenesisAssetContactTraceRowV1") is (
        GenesisAssetContactTraceRowV1
    )
    assert getattr(harness, "GenesisAssetLoadObservationV1") is GenesisAssetLoadObservationV1
    assert getattr(harness, "GenesisAssetContactTraceRowV1") is GenesisAssetContactTraceRowV1

    initial = GenesisAssetContactTraceRowV1(
        schema_version="harness.genesis_asset_contact_trace_row.v1",
        step_index=0,
        phase="initial",
        sim_time_us=0,
        position_m=(0.0, 0.0, 0.01),
        orientation_wxyz=(1.0, 0.0, 0.0, 0.0),
        linear_velocity_m_s=(0.0, 0.0, 0.0),
        angular_velocity_rad_s=(0.0, 0.0, 0.0),
        contacts=(),
        contact_count=0,
        total_normal_force_n=0.0,
        max_penetration_m=0.0,
    )
    contact = {
        "pair": "asset:ground",
        "asset_collision_index": 3,
        "ground_collision_index": 0,
        "position_m": (0.0, 0.0, 0.0),
        "normal_on_asset": (0.0, 0.0, 1.0),
        "force_on_asset_n": (0.0, 0.0, 0.7),
        "force_on_ground_n": (0.0, 0.0, -0.7),
        "normal_force_n": 0.7,
        "penetration_m": 0.0002,
    }
    post = GenesisAssetContactTraceRowV1(
        schema_version="harness.genesis_asset_contact_trace_row.v1",
        step_index=1,
        phase="post_step",
        sim_time_us=4000,
        position_m=(0.0, 0.0, 0.0098),
        orientation_wxyz=(1.0, 0.0, 0.0, 0.0),
        linear_velocity_m_s=(0.0, 0.0, -0.0392),
        angular_velocity_rad_s=(0.0, 0.0, 0.0),
        contacts=(contact,),
        contact_count=1,
        total_normal_force_n=0.7,
        max_penetration_m=0.0002,
    )
    assert initial.step_index == 0 and post.step_index == 1
    assert post.contacts[0].asset_collision_index == 3
    for field, replacement, message in (
        ("phase", "initial", "phase"),
        ("sim_time_us", 0, "sim_time"),
        ("contact_count", 0, "contact_count"),
        ("total_normal_force_n", 0.6, "normal force"),
        ("max_penetration_m", 0.0001, "penetration"),
    ):
        changed = post.model_dump(mode="json")
        changed[field] = replacement
        with pytest.raises(ValidationError, match=message):
            GenesisAssetContactTraceRowV1.model_validate(changed)

    assert GenesisAssetLoadObservationV1.model_json_schema()["$id"] == (
        "harness.genesis_asset_load_observation.v1"
    )
    assert GenesisAssetContactTraceRowV1.model_json_schema()["$id"] == (
        "harness.genesis_asset_contact_trace_row.v1"
    )


def test_probe_input_freezes_the_sealed_cpu_newton_execution_profile(
    documents: dict[str, Any],
) -> None:
    probe_input = documents["input"]
    values = probe_input.model_dump(mode="json")
    assert values["release_height_m"] == 0.01
    assert values["dt_seconds"] == 0.004
    assert values["solver"] == "newton"
    assert values["steps"] == 1000
    assert values["cpu_threads"] == values["environment_count"] == 1
    assert values["collisions_enabled"] is True
    assert values["model_execution_allowed"] is False
    assert values["network_access_allowed"] is False
    for field, replacement in (
        ("release_height_m", 0.02),
        ("dt_seconds", 0.002),
        ("solver", "cg"),
        ("steps", 999),
        ("cpu_threads", 2),
        ("environment_count", 2),
        ("collisions_enabled", False),
        ("model_execution_allowed", True),
        ("network_access_allowed", True),
    ):
        changed = json.loads(json.dumps(values))
        changed[field] = replacement
        with pytest.raises(ValidationError):
            _rehash(type(probe_input), changed)
    for field, schema_version in (
        ("representation", "harness.other_representation.v1"),
        ("runtime_lock", "harness.other_runtime.v1"),
    ):
        changed = json.loads(json.dumps(values))
        changed[field]["schema_version"] = schema_version
        with pytest.raises(ValidationError, match="schema_version"):
            _rehash(type(probe_input), changed)
    changed = json.loads(json.dumps(values))
    changed["representation"]["uri"] = f"artifact://sha256/{'f' * 64}"
    with pytest.raises(ValidationError, match="content-addressed"):
        _rehash(type(probe_input), changed)


def test_load_evidence_binds_complete_mouse_loader_and_physical_properties(
    documents: dict[str, Any],
) -> None:
    load = documents["load"]
    values = load.model_dump(mode="json")
    assert values["member_count"] == len(values["members"]) == 15
    assert values["source_link_count"] == 1
    assert values["source_joint_count"] == 0
    assert values["loaded_link_count"] == 1
    assert values["loaded_joint_count"] == 1
    assert values["loaded_dof_count"] == 6
    assert values["source_collision_member_count"] == 8
    assert values["loaded_collision_shape_count"] == 1
    assert values["loaded_collision_shapes_merged"] is True
    assert sum(item["role"] == "collision" for item in values["members"]) == 8
    assert values["dynamic"] is True and values["fixed"] is False
    assert values["mass_kg"] == 0.07
    assert values["friction"] == 0.2
    assert values["backend"] == values["subject"]["backend"] == "genesis"
    assert values["source_record_sha256"] == values["subject"]["source_record_sha256"]
    assert values["loader_closure_sha256"] == asset_loader_closure_sha256(
        values["subject"], values["root_logical_path"], values["members"]
    )

    for field, replacement, message in (
        ("member_count", 14, "member_count"),
        ("source_collision_member_count", 7, "source_collision_member_count"),
        ("dynamic", False, "dynamic"),
        ("fixed", True, "fixed"),
        ("source_record_sha256", "f" * 64, "source_record"),
        ("backend", "other", "backend"),
        ("loader_closure_sha256", "f" * 64, "loader_closure"),
    ):
        changed = json.loads(json.dumps(values))
        changed[field] = replacement
        with pytest.raises(ValidationError, match=message):
            _rehash(type(load), changed)
    for field, replacement in (
        ("loaded_link_count", -1),
        ("loaded_joint_count", 0),
        ("mass_kg", 0.0),
        ("friction", -0.1),
    ):
        changed = json.loads(json.dumps(values))
        changed[field] = replacement
        with pytest.raises(ValidationError):
            _rehash(type(load), changed)
    changed = json.loads(json.dumps(values))
    changed["members"] = list(reversed(changed["members"]))
    with pytest.raises(ValidationError, match="sorted"):
        _rehash(type(load), changed)
    changed = json.loads(json.dumps(values))
    changed["root_logical_path"] = "urdf/missing.urdf"
    with pytest.raises(ValidationError, match="root_logical_path"):
        _rehash(type(load), changed)
    changed = json.loads(json.dumps(values))
    collision = next(item for item in changed["members"] if item["role"] == "collision")
    collision["role"] = "visual"
    with pytest.raises(ValidationError, match="source collision member count"):
        _rehash(type(load), changed)
    changed = json.loads(json.dumps(values))
    changed["inertia_kg_m2"]["ixx"] = -1.0
    with pytest.raises(ValidationError, match="inertia"):
        _rehash(type(load), changed)


def test_contact_trace_requires_contiguous_steps_and_consistent_summaries(
    documents: dict[str, Any],
) -> None:
    trace = documents["trace"]
    values = trace.model_dump(mode="json")
    assert values["sample_count"] == values["last_step"] - values["first_step"] + 1
    assert values["trace_rows"]["media_type"] == "application/x-ndjson"
    assert values["trace_rows"]["schema_version"] == ("harness.genesis_asset_contact_trace_row.v1")
    assert values["contact_observed"] is True
    assert 0 < values["contact_step_count"] <= values["total_contact_count"]
    assert values["first_step"] <= values["first_contact_step"]
    assert values["first_contact_step"] <= values["last_contact_step"]
    assert values["last_contact_step"] <= values["last_step"]
    assert values["max_penetration_m"] <= values["penetration_threshold_m"]
    assert values["penetration_violation_count"] == 0

    for field, replacement, message in (
        ("sample_count", 999, "sample_count"),
        ("steps_contiguous", False, "contiguous"),
        ("first_contact_step", 1000, "contact"),
        ("total_contact_count", 1, "contact"),
        ("penetration_violation_count", 1, "penetration"),
        ("collisions_enabled", False, "collisions"),
        ("simulator_executed", False, "simulator"),
    ):
        changed = json.loads(json.dumps(values))
        changed[field] = replacement
        with pytest.raises(ValidationError, match=message):
            _rehash(type(trace), changed)
    changed = json.loads(json.dumps(values))
    changed.update(
        contact_observed=False,
        contact_step_count=875,
        first_contact_step=None,
        last_contact_step=None,
    )
    with pytest.raises(ValidationError, match="contact"):
        _rehash(type(trace), changed)
    changed = json.loads(json.dumps(values))
    changed["max_normal_force_n"] = -0.1
    with pytest.raises(ValidationError):
        _rehash(type(trace), changed)
    changed = json.loads(json.dumps(values))
    changed["first_contact_step"] = 999
    changed["last_contact_step"] = 998
    with pytest.raises(ValidationError, match="contact"):
        _rehash(type(trace), changed)


def test_probe_report_binds_all_layers_without_claiming_qualification(
    documents: dict[str, Any],
) -> None:
    report = documents["report"]
    values = report.model_dump(mode="json")
    assert [check["code"] for check in values["checks"]] == [
        "backend_load",
        "closure_identity",
        "collision_shapes",
        "contact_observed",
        "dynamic_release",
        "penetration_bounded",
        "settled",
    ]
    assert values["status"] == "pass"
    assert values["member_closure_verified"] is True
    assert values["load_executed"] is True
    assert values["contact_probe_executed"] is True
    assert values["steps_executed"] == 1000
    assert values["collisions_enabled"] is True
    assert values["simulator_executed"] is True
    assert values["model_call_count"] == values["network_call_count"] == 0
    assert not {
        "qualified",
        "qualification",
        "qualification_sha256",
        "publishable",
        "promotion_executed",
    }.intersection(values)

    for field, replacement, message in (
        ("member_closure_verified", False, "closure"),
        ("load_executed", False, "load"),
        ("contact_probe_executed", False, "contact"),
        ("steps_executed", 999, "steps"),
        ("collisions_enabled", False, "collisions"),
        ("simulator_executed", False, "simulator"),
        ("model_call_count", 1, "model_call"),
        ("network_call_count", 1, "network_call"),
    ):
        changed = json.loads(json.dumps(values))
        changed[field] = replacement
        with pytest.raises(ValidationError, match=message):
            _rehash(type(report), changed)
    changed = json.loads(json.dumps(values))
    changed["checks"] = list(reversed(changed["checks"]))
    with pytest.raises(ValidationError, match="sorted"):
        _rehash(type(report), changed)
    changed = json.loads(json.dumps(values))
    changed["checks"][0]["status"] = "fail"
    with pytest.raises(ValidationError, match="status|checks"):
        _rehash(type(report), changed)
    changed = json.loads(json.dumps(values))
    changed["checks"][1]["evidence"] = list(reversed(changed["checks"][1]["evidence"]))
    with pytest.raises(ValidationError, match="evidence.*sorted"):
        _rehash(type(report), changed)


def test_probe_schemas_are_standalone_draft202012_snapshots(
    documents: dict[str, Any],
) -> None:
    catalog = schema_documents()
    assert len(catalog) == 132
    for value in documents.values():
        schema_id = value.model_json_schema()["$id"]
        document = catalog[schema_id]
        assert document["$id"] == schema_id
        assert document["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert document["additionalProperties"] is False
        Draft202012Validator.check_schema(document)
        Draft202012Validator(document).validate(value.model_dump(mode="json"))
        snapshot = DEFAULT_SCHEMA_ROOT / schema_snapshot_name(schema_id)
        assert snapshot.read_text(encoding="utf-8") == render_schema_document(document)
