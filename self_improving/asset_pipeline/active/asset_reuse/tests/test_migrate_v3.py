import hashlib
import importlib.util
import json
import runpy
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ledger" / "migrate_v3.py"
sys.path.insert(0, str(ROOT))
from lib import ledger, ledger_writes  # noqa: E402

from tests.trusted_fixtures import qualified_runtime_capability  # noqa: E402


@pytest.fixture(scope="module")
def migration_module():
    name = "asset_ledger_migrate_v3_under_test"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _document(*, collision=False):
    uri = (
        "data/asset_library/901_widget/collision/base0.stl"
        if collision
        else ("data/asset_library/901_widget/visual/base0.stl")
    )
    sha = "a" * 64
    representation = {
        "format": "stl",
        "uri": uri,
        "backend": "sapien",
        "role": "collision" if collision else "visual",
        "sha256": sha,
        "files": [{"uri": uri, "sha256": sha, "bytes": 17}],
        "metadata": {},
    }
    if collision:
        representation["collision_meta"] = {
            "mode": "explicit_mesh",
            "convex": False,
        }
    return {
        "schema_version": ledger.SCHEMA_VERSION,
        "asset_id": "robotwin_901_widget",
        "external_ids": {"env_gen": "901_widget"},
        "category": "widget",
        "kind": "rigid",
        "profile": "sapien_only",
        "semantics": {
            "aliases": ["widget"],
            "identity": {
                "basis": "upstream_catalog",
                "verified": False,
            },
        },
        "models": [
            {
                "model_id": 0,
                "physical": {
                    "mesh_bbox_m": [0.1, 0.1, 0.1],
                    "size_resolution": {
                        "actual_max_dim_m": 0.1,
                        "scale": 1.0,
                    },
                    "conventions": {
                        "is_static": False,
                        "z_policy": "origin_on_table",
                        "footprint_shape": "box",
                        "stable_poses": [
                            {
                                "pose_id": "upright",
                                "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                                "is_default": True,
                                "measured_against": {
                                    "backend": "sapien",
                                    "run_id": "settle-1",
                                },
                            }
                        ],
                        "inherited_from": None,
                    },
                    "mass_kg": {"value": None, "status": "unknown"},
                    "friction": {"value": None, "status": "unknown"},
                },
                "representations": [representation],
                "articulation": {},
                "source": {
                    "kind": "retrieved",
                    "library": "RoboTwin",
                    "group": "deadbeef",
                    "file": "901_widget",
                    "retrieved_at": "2026-08-15",
                    "license": {
                        "spdx": None,
                        "status": "unknown",
                        "terms_note": "fixture",
                    },
                },
                "verification": [],
            }
        ],
    }


def _make_primary_glb(document):
    representation = document["models"][0]["representations"][0]
    uri = representation["uri"].removesuffix(".stl") + ".glb"
    representation["format"] = "glb"
    representation["uri"] = uri
    representation["files"][0]["uri"] = uri
    return representation


def _debt_codes(result):
    """Return debts relevant to each focused migration test.

    The shared legacy fixture intentionally carries only a caller-written pose
    run id.  The dedicated provenance tests assert that debt directly; other
    tests keep their assertions focused on the migration fact under test.
    """

    return [
        issue.code for issue in result.debts if issue.code != "stable_pose_provenance_untrusted"
    ]


def _attach_trusted_pose_receipt(asset_dir, document):
    """Bind the fixture pose to the same deep evidence reader used by production."""

    model = document["models"][0]
    pose = model["physical"]["conventions"]["stable_poses"][0]
    provenance = pose.get("measured_against")
    if not isinstance(provenance, dict):
        return
    proof_model = json.loads(json.dumps(model))
    for representation in proof_model["representations"]:
        representation.pop("size_bytes", None)
        if "files" not in representation:
            primary = Path(representation["uri"])
            representation["files"] = [
                {
                    "uri": str(primary),
                    "sha256": representation["sha256"],
                    "bytes": primary.stat().st_size,
                }
            ]
    asset_key = document.get("external_ids", {}).get("env_gen", "901_widget")
    run_id = "trusted-migration-pose"
    provenance.update(backend="sapien", run_id=run_id)
    proof_model["physical"]["conventions"]["stable_poses"][0]["measured_against"].update(
        backend="sapien", run_id=run_id
    )
    payload = ledger_writes.issue_qualified_verification(
        issuer="asset.settle_repair.v1",
        asset_key=asset_key,
        model_id=proof_model["model_id"],
        run_id=run_id,
        timestamp="2026-08-31T12:00:00",
        reps_digest=ledger.reps_digest(proof_model, "sapien"),
        inputs={
            "fixture": ledger_writes.provenance_file_record(__file__),
            "task": {"asset_key": asset_key, "model_id": proof_model["model_id"]},
        },
        thresholds={
            "max_late_drift_m": 0.002,
            "min_support_z_m": -0.005,
            "max_tilt_deg": 181.0,
        },
        result={
            "schema": "asset_settle_result.v2",
            "finite": True,
            "late_drift_m": 0.0,
            "support_z_m": 0.0,
            "tilt_deg": 0.0,
            "rest_orientation_wxyz": list(pose["orientation_wxyz"]),
            "origin_z_m": 0.0,
            "derived_z_policy": "origin_on_table",
            "details": {},
        },
        model_entry=proof_model,
        execution_snapshot=ledger_writes.publish_execution_snapshot(
            asset_dir / "verification_inputs",
            source_asset_dir=asset_dir,
            asset_key=asset_key,
            model_entry=proof_model,
        ),
        runtime_capability=qualified_runtime_capability(
            asset_dir / "runtime",
            "asset.settle_repair.v1",
            ledger,
            ledger_writes,
        ),
    )
    evidence = ledger_writes.publish_verification_evidence(
        asset_dir / "verification_evidence", payload
    )
    model["verification"] = [ledger_writes.receipt_from_evidence(payload, evidence)]


def test_relabels_are_not_content_migrations(migration_module):
    document = _document()
    _make_primary_glb(document)
    document["semantic_name"] = "widget"
    document["tags"] = ["rigid"]
    del document["external_ids"]
    physical = document["models"][0]["physical"]
    physical["mesh_up_axis"] = "Y"
    physical["origin_convention"] = "bottom-center"
    physical["mass_kg"]["runtime_default_kg"] = 0.1
    physical["mass_kg"]["runtime_default_basis"] = "global_constant"
    pose = physical["conventions"]["stable_poses"][0]
    del pose["measured_against"]
    representation = document["models"][0]["representations"][0]
    del representation["files"]
    representation["size_bytes"] = 17
    original = json.loads(json.dumps(document))

    result = migration_module.migrate_document(document, {}, {}, False)

    assert document == original  # migration never mutates caller-owned evidence
    assert result.document["external_ids"] == {"env_gen": "901_widget"}
    assert "semantic_name" not in result.document
    assert "tags" not in result.document
    repaired_physical = result.document["models"][0]["physical"]
    assert "mesh_up_axis" not in repaired_physical
    assert "origin_convention" not in repaired_physical
    assert repaired_physical["mass_kg"] == {"value": None, "status": "unknown"}
    repaired_representation = result.document["models"][0]["representations"][0]
    assert "size_bytes" not in repaired_representation
    assert repaired_representation["files"] == [
        {
            "uri": "data/asset_library/901_widget/visual/base0.glb",
            "sha256": "a" * 64,
            "bytes": 17,
        }
    ]
    assert [issue.code for issue in result.debts] == [
        "stable_pose_provenance_missing",
        "representation_file_closure_unproven",
    ]
    assert result.blockers == ()
    assert "measured_against" not in (repaired_physical["conventions"]["stable_poses"][0])


def test_migration_is_idempotent_with_untrusted_pose_debt(migration_module):
    document = _document(collision=True)

    first = migration_module.migrate_document(document, {}, {}, False)
    second = migration_module.migrate_document(first.document, {}, {}, False)

    assert first.document == document
    assert first.changes == ()
    assert [issue.code for issue in first.debts] == ["stable_pose_provenance_untrusted"]
    assert first.blockers == ()
    assert second == first


def test_arbitrary_pose_run_id_is_not_treated_as_trusted_measurement(migration_module):
    document = _document(collision=True)
    document["models"][0]["physical"]["conventions"]["stable_poses"][0]["measured_against"][
        "run_id"
    ] = "caller-asserted-but-nonexistent"

    result = migration_module.migrate_document(document, {}, {}, False)

    assert [issue.code for issue in result.debts] == ["stable_pose_provenance_untrusted"]


def test_unknown_evidence_becomes_typed_debt_and_is_not_invented(migration_module):
    document = _document(collision=True)
    pose = document["models"][0]["physical"]["conventions"]["stable_poses"][0]
    del pose["measured_against"]
    representation = document["models"][0]["representations"][0]
    del representation["collision_meta"]
    del representation["files"]

    result = migration_module.migrate_document(document, {}, {}, True)

    assert [(issue.path, issue.code) for issue in result.debts] == [
        (
            "models.0.physical.conventions.stable_poses.0.measured_against",
            "stable_pose_provenance_missing",
        ),
        (
            "models.0.representations.0.collision_meta",
            "collision_provenance_missing",
        ),
        (
            "models.0.representations.0.files",
            "representation_files_evidence_missing",
        ),
    ]
    repaired = result.document["models"][0]
    assert "measured_against" not in repaired["physical"]["conventions"]["stable_poses"][0]
    assert "files" not in repaired["representations"][0]
    assert "collision_meta" not in repaired["representations"][0]


def test_structured_unknown_collision_metadata_remains_typed_debt(migration_module):
    document = _document(collision=True)
    document["models"][0]["representations"][0]["collision_meta"] = {
        "mode": "unknown",
        "unknown_reason": "legacy authoring was not probed",
    }

    result = migration_module.migrate_document(document, {}, {}, False)

    assert _debt_codes(result) == ["collision_provenance_missing"]
    assert result.blockers == ()


def test_invalid_collision_mode_is_a_typed_blocker(migration_module):
    document = _document(collision=True)
    document["models"][0]["representations"][0]["collision_meta"] = {"mode": "made_up"}

    result = migration_module.migrate_document(document, {}, {}, False)

    assert [issue.code for issue in result.blockers] == ["collision_mode_invalid"]


@pytest.mark.parametrize("asset_format", ["glb", "usd", "urdf", "obj"])
def test_primary_file_does_not_prove_multi_file_closure(migration_module, asset_format):
    document = _document()
    representation = document["models"][0]["representations"][0]
    representation["format"] = asset_format
    representation["uri"] = f"data/asset/model.{asset_format}"
    representation["sha256"] = "b" * 64
    del representation["files"]
    representation["size_bytes"] = 23

    result = migration_module.migrate_document(document, {}, {}, False)

    assert result.document["models"][0]["representations"][0]["files"] == [
        {
            "uri": f"data/asset/model.{asset_format}",
            "sha256": "b" * 64,
            "bytes": 23,
        }
    ]
    assert _debt_codes(result) == ["representation_file_closure_unproven"]


@pytest.mark.parametrize("asset_format", ["glb", "usd", "urdf", "obj"])
@pytest.mark.parametrize("member_count", [1, 2])
def test_prefilled_files_cannot_self_attest_dependency_closure(
    migration_module, asset_format, member_count
):
    document = _document()
    representation = document["models"][0]["representations"][0]
    representation["format"] = asset_format
    representation["uri"] = f"data/asset/model.{asset_format}"
    representation["sha256"] = "b" * 64
    representation["files"] = [
        {
            "uri": f"data/asset/model.{asset_format}",
            "sha256": "b" * 64,
            "bytes": 23,
        }
    ]
    if member_count == 2:
        representation["files"].append(
            {
                "uri": "data/asset/dependency.bin",
                "sha256": "c" * 64,
                "bytes": 11,
            }
        )

    first = migration_module.migrate_document(document, {}, {}, False)
    second = migration_module.migrate_document(first.document, {}, {}, False)

    expected_uris = sorted(member["uri"] for member in representation["files"])
    repaired = first.document["models"][0]["representations"][0]
    assert [member["uri"] for member in repaired["files"]] == expected_uris
    assert first.changes == (
        () if member_count == 1 else ("models.0.representations.0.files: sort by uri",)
    )
    assert _debt_codes(first) == ["representation_file_closure_unproven"]
    assert second.document == first.document
    assert second.changes == ()
    assert second.debts == first.debts
    assert second.blockers == first.blockers


def test_prefilled_files_are_canonicalized_by_uri_without_claiming_closure(
    migration_module,
):
    document = _document()
    representation = document["models"][0]["representations"][0]
    representation["format"] = "obj"
    representation["uri"] = "data/asset/model.obj"
    representation["sha256"] = "b" * 64
    representation["files"] = [
        {"uri": "data/asset/texture.png", "sha256": "c" * 64, "bytes": 11},
        {"uri": "data/asset/model.obj", "sha256": "b" * 64, "bytes": 23},
    ]

    result = migration_module.migrate_document(document, {}, {}, False)

    repaired_files = result.document["models"][0]["representations"][0]["files"]
    assert [member["uri"] for member in repaired_files] == sorted(
        member["uri"] for member in repaired_files
    )
    assert _debt_codes(result) == ["representation_file_closure_unproven"]


def test_conflicting_identity_is_a_blocker_not_an_overwrite(migration_module):
    document = _document()
    document["external_ids"]["env_gen"] = "different_asset"

    result = migration_module.migrate_document(document, {}, {}, False)

    assert result.document["external_ids"]["env_gen"] == "different_asset"
    assert [(issue.path, issue.code) for issue in result.blockers] == [
        ("external_ids.env_gen", "external_id_conflict")
    ]


def test_unprefixed_asset_id_is_already_the_catalog_id(migration_module):
    document = _document()
    document["asset_id"] = "901_widget"

    result = migration_module.migrate_document(document, {}, {}, False)

    assert result.document["external_ids"] == {"env_gen": "901_widget"}
    assert result.blockers == ()


def test_empty_external_id_mapping_is_repaired_from_asset_id(migration_module):
    document = _document()
    document["external_ids"] = {}

    result = migration_module.migrate_document(document, {}, {}, False)

    assert result.document["external_ids"] == {"env_gen": "901_widget"}
    assert result.changes == ("external_ids.env_gen",)


def test_generated_asset_prefix_maps_back_to_env_gen_id(migration_module):
    document = _document()
    document["asset_id"] = "generated_901_widget"

    result = migration_module.migrate_document(document, {}, {}, False)

    assert result.document["external_ids"] == {"env_gen": "901_widget"}
    assert result.blockers == ()


def test_proven_multifile_helper_is_empty_for_non_v3(migration_module):
    document = _document()
    document["schema_version"] = "asset_ledger.v2"

    assert migration_module._proven_multifile_representations(document) == set()


@pytest.mark.parametrize("external_ids", [None, {}])
def test_missing_asset_identity_is_a_typed_blocker(migration_module, external_ids):
    document = _document()
    document.pop("asset_id")
    if external_ids is None:
        document.pop("external_ids")
    else:
        document["external_ids"] = external_ids

    result = migration_module.migrate_document(document, {}, {}, False)

    assert [issue.code for issue in result.blockers] == ["external_id_unrecoverable"]


def test_unknown_schema_is_a_blocker_and_is_not_relabelled(migration_module):
    document = _document()
    document["schema_version"] = "asset_ledger.v99"

    result = migration_module.migrate_document(document, {}, {}, False)

    assert result.document == document
    assert result.changes == ()
    assert result.debts == ()
    assert [issue.code for issue in result.blockers] == ["unsupported_schema_version"]


@pytest.mark.parametrize("is_upstream", [False, True])
def test_legacy_sidecars_become_typed_debt_while_geometry_is_repaired(
    migration_module, is_upstream
):
    document = _document()
    _make_primary_glb(document)
    document["schema_version"] = "asset_ledger.v2"
    physical = document["models"][0]["physical"]
    physical["mesh_up_axis"] = "Y"
    physical["origin_convention"] = "bottom-center"
    representation = document["models"][0]["representations"][0]
    del representation["files"]
    representation["size_bytes"] = 17
    document["models"][0]["verification"] = [{"report_path": None}]
    attrs = {"901_widget": {"0": {"colors": ["red"], "fractions": {"red": 1.0}}}}
    survey = {
        "901_widget": {
            "0": {
                "verdict": "hollow",
                "interior": {
                    "dimensions_m": [0.08, 0.08, 0.04],
                    "floor_z_offset_m": 0.01,
                    "floor_basis": "mesh probe",
                },
                "load_tilt_deg": 0.5,
            }
        }
    }

    result = migration_module.migrate_document(document, attrs, survey, is_upstream)

    assert _debt_codes(result) == [
        "measurement_input_unbound",
        "measurement_input_unbound",
        "representation_file_closure_unproven",
    ]
    assert result.blockers == ()
    model = result.document["models"][0]
    repaired_representation = model["representations"][0]
    assert repaired_representation["frame"] == {"up_axis": "Y"}
    assert repaired_representation["geometry_state"] == {
        "scale_baked": not is_upstream,
        "origin": "bottom-center",
    }
    assert "appearance" not in model
    assert "placement" not in model["physical"]
    assert "report_path" not in model["verification"][0]


def test_survey_verdict_without_representation_receipt_is_not_promoted(migration_module):
    document = _document()
    survey = {"901_widget": {"0": {"verdict": "solid"}}}

    result = migration_module.migrate_document(document, {}, survey, False)

    assert "placement" not in result.document["models"][0]["physical"]
    assert _debt_codes(result) == ["measurement_input_unbound"]


def test_reapplying_unbound_sidecar_evidence_is_idempotent_debt(migration_module):
    document = _document()
    attrs = {"901_widget": {"0": {"colors": ["red"], "fractions": {"red": 1.0}}}}
    survey = {"901_widget": {"0": {"verdict": "solid"}}}

    first = migration_module.migrate_document(document, attrs, survey, False)
    second = migration_module.migrate_document(first.document, attrs, survey, False)

    assert first.changes == ()
    assert second.document == first.document
    assert second.changes == ()
    assert second.debts == first.debts
    assert _debt_codes(first) == [
        "measurement_input_unbound",
        "measurement_input_unbound",
    ]


def test_complete_v3_multifile_closure_is_idempotent_except_for_pose_debt(
    tmp_path, migration_module
):
    document = _document()
    asset_root = tmp_path / "asset"
    asset_root.mkdir()
    obj = asset_root / "model.obj"
    mtl = asset_root / "material.mtl"
    obj.write_text("mtllib material.mtl\nv 0 0 0\nf 1 1 1\n", encoding="utf-8")
    mtl.write_text("newmtl mat\n", encoding="utf-8")
    representation = document["models"][0]["representations"][0]
    representation.update(
        {
            "format": "obj",
            "uri": str(obj.resolve()),
            "sha256": hashlib.sha256(obj.read_bytes()).hexdigest(),
            "files": [
                {
                    "uri": str(mtl.resolve()),
                    "sha256": hashlib.sha256(mtl.read_bytes()).hexdigest(),
                    "bytes": mtl.stat().st_size,
                },
                {
                    "uri": str(obj.resolve()),
                    "sha256": hashlib.sha256(obj.read_bytes()).hexdigest(),
                    "bytes": obj.stat().st_size,
                },
            ],
            "metadata": {},
        }
    )

    assert ledger.validate_ledger(document, check_files=True) == []

    first = migration_module.migrate_document(document, {}, {}, False)
    second = migration_module.migrate_document(first.document, {}, {}, False)

    assert first.document == document
    assert first.changes == ()
    assert _debt_codes(first) == []
    assert [issue.code for issue in first.debts] == ["stable_pose_provenance_untrusted"]
    assert first.blockers == ()
    assert second == first


def test_sidecars_fill_missing_evidence_but_never_replace_newer_measurements(
    migration_module,
):
    document = _document()
    model = document["models"][0]
    model["appearance"] = {
        "colors_measured": ["blue"],
        "method": "human-reviewed calibrated render",
        "measured_against": {"backend": "sapien", "run_id": "attr-20260830"},
    }
    model["physical"]["placement"] = {
        "verdict": "hollow",
        "interior_dims_m": [0.09, 0.09, 0.05],
        "measured_against": {"backend": "sapien", "run_id": "probe-20260830"},
    }
    digest = ledger.reps_digest(model, "sapien")
    model["appearance"]["measured_against"]["verified_digest"] = digest
    model["physical"]["placement"]["measured_against"]["verified_digest"] = digest
    attrs = {"901_widget": {"0": {"colors": ["red"], "fractions": {"red": 1.0}}}}
    survey = {"901_widget": {"0": {"verdict": "solid"}}}

    result = migration_module.migrate_document(document, attrs, survey, False)

    assert result.document["models"][0]["appearance"] == model["appearance"]
    assert result.document["models"][0]["physical"]["placement"] == model["physical"]["placement"]
    assert result.changes == ()
    assert _debt_codes(result) == []


@pytest.mark.parametrize(
    ("field", "run_id"),
    [("appearance", "attr-20260814"), ("placement", "probe-v4-20260815")],
)
def test_old_migration_measurements_remain_bytes_but_are_unbound_debt(
    migration_module, field, run_id
):
    document = _document()
    model = document["models"][0]
    block = {
        "measured_against": {"backend": "sapien", "run_id": run_id},
    }
    if field == "appearance":
        block.update(colors_measured=["red"], method="legacy")
        model["appearance"] = block
    else:
        block.update(verdict="solid")
        model["physical"]["placement"] = block

    first = migration_module.migrate_document(document, {}, {}, False)
    second = migration_module.migrate_document(first.document, {}, {}, False)

    assert first.document == document
    assert second.document == document
    assert _debt_codes(first) == ["measurement_input_unbound"]
    assert second.debts == first.debts


@pytest.mark.parametrize(
    "block",
    [
        None,
        {},
        {"measured_against": []},
        {"measured_against": {"backend": "unknown", "run_id": "run"}},
        {"measured_against": {"backend": "sapien", "run_id": 7}},
        {"measured_against": {"backend": "sapien", "run_id": " "}},
    ],
)
def test_measurement_binding_helper_rejects_malformed_receipts(migration_module, block):
    assert not migration_module._measurement_bound_to_model(_document()["models"][0], block)


def test_legacy_geometry_never_overwrites_existing_or_malformed_evidence(
    migration_module,
):
    document = _document()
    _make_primary_glb(document)
    physical = document["models"][0]["physical"]
    physical["mesh_up_axis"] = "Y"
    physical["origin_convention"] = "bottom-center"
    representation = document["models"][0]["representations"][0]
    representation["frame"] = "opaque-producer-frame"
    representation["geometry_state"] = "opaque-producer-geometry"

    result = migration_module.migrate_document(document, {}, {}, False)

    repaired = result.document["models"][0]["representations"][0]
    assert repaired["frame"] == "opaque-producer-frame"
    assert repaired["geometry_state"] == "opaque-producer-geometry"


def test_null_legacy_deletions_are_recorded_for_auditable_migration(migration_module):
    document = _document()
    physical = document["models"][0]["physical"]
    physical["mesh_up_axis"] = None
    physical["origin_convention"] = None
    representation = document["models"][0]["representations"][0]
    representation["size_bytes"] = None

    result = migration_module.migrate_document(document, {}, {}, False)

    assert "mesh_up_axis" not in result.document["models"][0]["physical"]
    assert "origin_convention" not in result.document["models"][0]["physical"]
    assert "size_bytes" not in result.document["models"][0]["representations"][0]
    assert result.changes == (
        "models.0: del physical.mesh_up_axis",
        "models.0: del physical.origin_convention",
        "models.0.representations.0: del size_bytes",
    )


def test_legacy_geometry_preserves_existing_fields_and_only_fills_gaps(
    migration_module,
):
    document = _document()
    _make_primary_glb(document)
    physical = document["models"][0]["physical"]
    physical["mesh_up_axis"] = "Y"
    physical["origin_convention"] = "bottom-center"
    representation = document["models"][0]["representations"][0]
    representation["frame"] = {"up_axis": "Z"}
    representation["geometry_state"] = {"scale_baked": False, "origin": "center"}

    result = migration_module.migrate_document(document, {}, {}, False)

    repaired = result.document["models"][0]["representations"][0]
    assert repaired["frame"] == {"up_axis": "Z"}
    assert repaired["geometry_state"] == {"scale_baked": False, "origin": "center"}


def test_origin_only_legacy_fact_does_not_create_a_frame(migration_module):
    document = _document()
    _make_primary_glb(document)
    physical = document["models"][0]["physical"]
    physical["origin_convention"] = "bottom-center"

    result = migration_module.migrate_document(document, {}, {}, False)

    representation = result.document["models"][0]["representations"][0]
    assert "frame" not in representation
    assert representation["geometry_state"] == {
        "scale_baked": True,
        "origin": "bottom-center",
    }


def test_non_mapping_envelopes_and_non_list_pose_or_verification_are_left_for_validator(
    migration_module,
):
    document = _document()
    model = document["models"][0]
    model["physical"]["mass_kg"] = None
    model["physical"]["friction"] = "opaque"
    model["physical"]["conventions"]["stable_poses"] = {}
    model["verification"] = {}

    result = migration_module.migrate_document(document, {}, {}, False)

    assert result.document["models"][0]["physical"]["mass_kg"] is None
    assert result.document["models"][0]["physical"]["friction"] == "opaque"


def test_non_mapping_and_complete_verifications_are_not_rewritten(migration_module):
    document = _document()
    document["models"][0]["verification"] = [
        "opaque",
        {"report_path": "report.json"},
    ]

    result = migration_module.migrate_document(document, {}, {}, False)

    assert result.document["models"][0]["verification"] == [
        "opaque",
        {"report_path": "report.json"},
    ]


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda document: document.update(external_ids=[]), "external_ids_bad_type"),
        (lambda document: document.update(models={}), "models_bad_type"),
        (
            lambda document: document["models"].__setitem__(0, "bad-model"),
            "model_bad_type",
        ),
        (
            lambda document: document["models"][0].update(physical=[]),
            "physical_bad_type",
        ),
        (
            lambda document: document["models"][0].update(representations={}),
            "representations_bad_type",
        ),
        (
            lambda document: document["models"][0]["representations"].__setitem__(
                0, "bad-representation"
            ),
            "representation_bad_type",
        ),
        (
            lambda document: document["models"][0]["representations"][0].update(
                role="collision", collision_meta=[]
            ),
            "collision_meta_bad_type",
        ),
    ],
)
def test_malformed_content_is_a_typed_blocker(migration_module, mutate, code):
    document = _document()
    mutate(document)

    result = migration_module.migrate_document(document, {}, {}, False)

    assert code in [issue.code for issue in result.blockers]


def _run_cli(tmp_path, document, *, dry_run=False, payload_attack=None):
    document = json.loads(json.dumps(document))
    library = tmp_path / "library"
    upstream = tmp_path / "upstream"
    ledger_path = library / "901_widget" / "ledger.json"
    ledger_path.parent.mkdir(parents=True)
    upstream.mkdir()
    payload = library / "901_widget" / "base0.stl"
    payload.write_bytes(b"x" * 17)
    payload_sha = hashlib.sha256(payload.read_bytes()).hexdigest()
    for model in document.get("models", []):
        for representation in model.get("representations", []):
            representation["uri"] = str(payload)
            representation["sha256"] = payload_sha
            for member in representation.get("files", []):
                member.update(uri=str(payload), sha256=payload_sha, bytes=17)
    if isinstance(document.get("models"), list) and document["models"]:
        _attach_trusted_pose_receipt(ledger_path.parent, document)
    original = json.dumps(document, indent=2) + "\n"
    ledger_path.write_text(original)
    if payload_attack == "missing":
        payload.unlink()
    elif payload_attack == "hash_mismatch":
        payload.write_bytes(b"tampered-after-ledger")
    attrs = tmp_path / "attributes.json"
    survey = tmp_path / "survey.json"
    attrs.write_text('{"models": {}}\n')
    survey.write_text('{"models": {}}\n')
    command = [
        sys.executable,
        str(SCRIPT),
        "--library",
        str(library),
        "--upstream",
        str(upstream),
        "--attributes",
        str(attrs),
        "--survey",
        str(survey),
    ]
    if dry_run:
        command.append("--dry-run")
    result = subprocess.run(command, capture_output=True, text=True)
    return result, ledger_path, original


def _run_main_direct(tmp_path, document, migration_module, monkeypatch, *, dry_run=False):
    document = json.loads(json.dumps(document))
    library = tmp_path / "library"
    upstream = tmp_path / "upstream"
    (library / "999_skip").mkdir(parents=True)
    ledger_path = library / "901_widget" / "ledger.json"
    ledger_path.parent.mkdir(parents=True)
    upstream.mkdir()
    payload = library / "901_widget" / "base0.stl"
    payload.write_bytes(b"x" * 17)
    payload_sha = hashlib.sha256(payload.read_bytes()).hexdigest()
    for model in document.get("models", []):
        for representation in model.get("representations", []):
            representation["uri"] = str(payload)
            representation["sha256"] = payload_sha
            for member in representation.get("files", []):
                member.update(uri=str(payload), sha256=payload_sha, bytes=17)
    if isinstance(document.get("models"), list) and document["models"]:
        _attach_trusted_pose_receipt(ledger_path.parent, document)
    original = json.dumps(document, indent=2) + "\n"
    ledger_path.write_text(original)
    attrs = tmp_path / "attributes.json"
    survey = tmp_path / "survey.json"
    attrs.write_text('{"models": {}}\n')
    survey.write_text('{"models": {}}\n')
    argv = [
        "migrate_v3.py",
        "--library",
        str(library),
        "--upstream",
        str(upstream),
        "--attributes",
        str(attrs),
        "--survey",
        str(survey),
    ]
    if dry_run:
        argv.append("--dry-run")
    monkeypatch.setattr(sys, "argv", argv)
    return migration_module.main(), ledger_path, original


def test_cli_refuses_to_write_partial_repair_with_typed_debt(tmp_path):
    document = _document()
    del document["models"][0]["physical"]["conventions"]["stable_poses"][0]["measured_against"]
    result, ledger_path, original = _run_cli(tmp_path, document)

    assert result.returncode == 1
    assert "stable_pose_provenance_missing" in result.stdout
    assert ledger_path.read_text() == original


def test_cli_stages_all_ledgers_before_writing_when_later_ledger_fails(tmp_path):
    library = tmp_path / "library"
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    originals = {}
    for asset, broken in (("901_widget", False), ("902_widget", True)):
        document = _document()
        document["schema_version"] = "asset_ledger.v2"
        document["semantic_name"] = "legacy"
        if broken:
            del document["models"][0]["physical"]["conventions"]["stable_poses"][0][
                "measured_against"
            ]
        asset_dir = library / asset
        asset_dir.mkdir(parents=True)
        payload = asset_dir / "base0.stl"
        payload.write_bytes(b"x" * 17)
        payload_sha = hashlib.sha256(payload.read_bytes()).hexdigest()
        for representation in document["models"][0]["representations"]:
            representation.update(uri=str(payload), sha256=payload_sha)
            for member in representation.get("files", []):
                member.update(uri=str(payload), sha256=payload_sha, bytes=17)
        ledger_path = asset_dir / "ledger.json"
        original = json.dumps(document, indent=2) + "\n"
        ledger_path.write_text(original)
        originals[ledger_path] = original
    attrs = tmp_path / "attributes.json"
    survey = tmp_path / "survey.json"
    attrs.write_text('{"models": {}}\n')
    survey.write_text('{"models": {}}\n')

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--library",
            str(library),
            "--upstream",
            str(upstream),
            "--attributes",
            str(attrs),
            "--survey",
            str(survey),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "stable_pose_provenance_missing" in result.stdout
    assert all(path.read_text() == original for path, original in originals.items())


def test_batch_commit_blocks_before_injected_second_ledger_failure(
    tmp_path, monkeypatch, migration_module
):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text('{"old": 1}\n')
    second.write_text('{"old": 2}\n')
    staged = [
        (first, first.read_bytes(), {"new": 1}),
        (second, second.read_bytes(), {"new": 2}),
    ]
    real_write = migration_module._write_staged_document
    calls = 0

    def fail_second(path, document):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second-ledger failure")
        real_write(path, document)

    monkeypatch.setattr(migration_module, "_write_staged_document", fail_second)

    with pytest.raises(migration_module.UnsafeBatchMigrationError, match="crash-atomic"):
        migration_module._commit_batch(staged)

    assert calls == 0
    assert first.read_bytes() == b'{"old": 1}\n'
    assert second.read_bytes() == b'{"old": 2}\n'


def test_multi_ledger_apply_fails_closed_before_first_publication(
    tmp_path, monkeypatch, migration_module
):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text('{"old": 1}\n')
    second.write_text('{"old": 2}\n')
    staged = [
        (first, first.read_bytes(), {"new": 1}),
        (second, second.read_bytes(), {"new": 2}),
    ]
    writes = []
    monkeypatch.setattr(
        migration_module,
        "_write_staged_document",
        lambda path, document: writes.append((path, document)),
    )

    with pytest.raises(migration_module.UnsafeBatchMigrationError, match="crash-atomic"):
        migration_module._commit_batch(staged)

    assert writes == []
    assert first.read_bytes() == b'{"old": 1}\n'
    assert second.read_bytes() == b'{"old": 2}\n'


def test_single_ledger_commit_revalidates_file_closure_under_lock(
    tmp_path, monkeypatch, migration_module
):
    active_root = tmp_path / "active"
    monkeypatch.setattr(migration_module.L, "ACTIVE_ROOT", active_root)
    asset_dir = active_root / "data" / "asset_library" / "901_widget"
    payload = asset_dir / "visual" / "base0.stl"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"x" * 17)
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()

    original_document = _document()
    representation = original_document["models"][0]["representations"][0]
    representation.update(sha256=digest)
    representation["files"][0].update(sha256=digest, bytes=17)
    candidate = json.loads(json.dumps(original_document))
    candidate["semantics"]["aliases"] = ["renamed widget"]
    ledger_path = asset_dir / "ledger.json"
    ledger_path.write_text(json.dumps(original_document, indent=2) + "\n")
    original = ledger_path.read_bytes()

    assert migration_module.L.validate_ledger(candidate, check_files=True) == []
    payload.write_bytes(b"y" * 17)

    with pytest.raises(migration_module.ConcurrentMigrationError, match="closure changed"):
        migration_module._commit_batch([(ledger_path, original, candidate)])

    assert ledger_path.read_bytes() == original


def test_duplicate_migration_path_is_rejected_before_locking(
    tmp_path, monkeypatch, migration_module
):
    path = tmp_path / "ledger.json"
    path.write_text('{"old": 1}\n')
    lock_attempts = []

    @contextmanager
    def recording_lock(candidate):
        lock_attempts.append(candidate)
        yield

    monkeypatch.setattr(migration_module.L, "_locked_ledger", recording_lock)
    staged = [
        (path, path.read_bytes(), {"new": 1}),
        (path, path.read_bytes(), {"new": 2}),
    ]

    with pytest.raises(migration_module.ConcurrentMigrationError, match="duplicate"):
        migration_module._commit_batch(staged)

    assert lock_attempts == []


def test_cli_treats_profile_requirements_as_hard_and_never_writes(tmp_path):
    document = _document()
    document["schema_version"] = "asset_ledger.v2"
    document["profile"] = "cross_backend"
    original = json.dumps(document, indent=2) + "\n"

    result, ledger_path, original = _run_cli(tmp_path, document)

    assert result.returncode == 1
    assert "profile_requirement_unmet" in result.stdout
    assert ledger_path.read_text() == original


def test_profile_requirement_is_a_unit_level_migration_failure(migration_module):
    result = migration_module.migrate_document(_document(), {}, {}, False)
    violation = ledger.Violation(
        "models.0.representations",
        "profile_requirement_unmet",
        "missing target backend",
    )

    failures = migration_module._migration_failures(result, [violation])

    assert [failure.code for failure in failures] == [
        "stable_pose_provenance_untrusted",
        "profile_requirement_unmet",
    ]


def test_cli_repairs_v2_atomically_and_dry_run_is_read_only(tmp_path):
    document = _document()
    document["schema_version"] = "asset_ledger.v2"
    del document["external_ids"]
    representation = document["models"][0]["representations"][0]
    del representation["files"]
    representation["size_bytes"] = 17

    dry_result, dry_path, _ = _run_cli(tmp_path / "dry", document, dry_run=True)
    assert dry_result.returncode == 0
    assert json.loads(dry_path.read_text())["schema_version"] == "asset_ledger.v2"

    result, ledger_path, _ = _run_cli(tmp_path / "apply", document)
    assert result.returncode == 0, result.stdout + result.stderr
    repaired = json.loads(ledger_path.read_text())
    assert repaired["schema_version"] == ledger.SCHEMA_VERSION
    assert repaired["external_ids"] == {"env_gen": "901_widget"}
    repaired_representation = repaired["models"][0]["representations"][0]
    assert repaired_representation["uri"] == repaired_representation["files"][0]["uri"]


@pytest.mark.parametrize(
    ("payload_attack", "violation_code"),
    [("missing", "file_missing"), ("hash_mismatch", "sha256_mismatch")],
)
def test_cli_full_file_gate_rejects_missing_or_changed_payload_without_write(
    tmp_path, payload_attack, violation_code
):
    document = _document()
    document["schema_version"] = "asset_ledger.v2"

    result, ledger_path, original = _run_cli(
        tmp_path,
        document,
        payload_attack=payload_attack,
    )

    assert result.returncode == 1
    assert violation_code in result.stdout
    assert ledger_path.read_text() == original


def test_cli_skips_asset_directories_without_a_ledger(tmp_path):
    library = tmp_path / "library"
    upstream = tmp_path / "upstream"
    (library / "901_widget").mkdir(parents=True)
    upstream.mkdir()
    attrs = tmp_path / "attributes.json"
    survey = tmp_path / "survey.json"
    attrs.write_text('{"models": {}}\n')
    survey.write_text('{"models": {}}\n')

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--library",
            str(library),
            "--upstream",
            str(upstream),
            "--attributes",
            str(attrs),
            "--survey",
            str(survey),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "migrated=0 failed=0" in result.stdout


def test_main_direct_writes_success_and_skips_non_ledger_dirs(
    tmp_path, migration_module, monkeypatch, capsys
):
    code, ledger_path, _ = _run_main_direct(tmp_path, _document(), migration_module, monkeypatch)

    captured = capsys.readouterr()
    assert code == 0
    assert "ok   901_widget: already_complete_v3" in captured.out
    assert "migrated=1 failed=0" in captured.out
    assert json.loads(ledger_path.read_text())["schema_version"] == ledger.SCHEMA_VERSION


def test_main_direct_dry_run_is_read_only(tmp_path, migration_module, monkeypatch, capsys):
    document = _document()
    document["schema_version"] = "asset_ledger.v2"
    del document["external_ids"]
    del document["models"][0]["representations"][0]["files"]
    document["models"][0]["representations"][0]["size_bytes"] = 17

    code, ledger_path, original = _run_main_direct(
        tmp_path, document, migration_module, monkeypatch, dry_run=True
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "DRYRUN migrated=1 failed=0" in captured.out
    assert ledger_path.read_text() == original


def test_main_direct_returns_failure_without_writing(
    tmp_path, migration_module, monkeypatch, capsys
):
    document = _document()
    del document["models"][0]["physical"]["conventions"]["stable_poses"][0]["measured_against"]

    code, ledger_path, original = _run_main_direct(
        tmp_path, document, migration_module, monkeypatch
    )

    captured = capsys.readouterr()
    assert code == 1
    assert "stable_pose_provenance_missing" in captured.out
    assert ledger_path.read_text() == original


def test_main_entrypoint_raises_system_exit_with_status(tmp_path, monkeypatch):
    library = tmp_path / "library"
    upstream = tmp_path / "upstream"
    library.mkdir()
    upstream.mkdir()
    attrs = tmp_path / "attributes.json"
    survey = tmp_path / "survey.json"
    attrs.write_text('{"models": {}}\n')
    survey.write_text('{"models": {}}\n')
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "migrate_v3.py",
            "--library",
            str(library),
            "--upstream",
            str(upstream),
            "--attributes",
            str(attrs),
            "--survey",
            str(survey),
        ],
    )

    with pytest.raises(SystemExit) as captured:
        runpy.run_path(str(SCRIPT), run_name="__main__")

    assert captured.value.code == 0
