import hashlib
import json
import os
import runpy
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import ledger

IDENTITY = {
    "basis": "manifest_human",
    "evidence": "archive/external_manifest.json",
    "verified": False,
}


def make_model(**over):
    m = {
        "model_id": 0,
        "physical": {
            "mesh_bbox_m": [0.078, 0.051, 0.053],
            "size_resolution": {
                "mode": "match_category",
                "actual_max_dim_m": 0.078,
                "scale": 1.0,
                "reference_max_dim_m": None,
                "reference_assets": [],
                "verdict": "no_precedent",
            },
            "conventions": {
                "is_static": False,
                "z_policy": "origin_on_table",
                "footprint_shape": "box",
                "stable_poses": [
                    {
                        "pose_id": "upright",
                        "orientation_wxyz": ledger.X90_WXYZ,
                        "is_default": True,
                        "measured_against": {
                            "backend": "sapien",
                            "run_id": "fixture-settle-1",
                        },
                    }
                ],
                "inherited_from": None,
            },
            "mass_kg": {"value": None, "status": "unknown"},
            "friction": {"value": None, "status": "unknown"},
        },
        "representations": [
            {
                "format": "glb",
                "uri": "/tmp/x/visual.glb",
                "backend": "sapien",
                "role": "visual",
                "sha256": "0" * 64,
                "files": [
                    {
                        "uri": "/tmp/x/visual.glb",
                        "sha256": "0" * 64,
                        "bytes": 10,
                    }
                ],
                "metadata": {
                    "derived_from": "src.usd",
                    "converter": "omni.kit.asset_converter@isaac-5.1",
                    "conversion_params": {"rotated_z2y": True},
                },
            },
            {
                "format": "png",
                "uri": "/tmp/x/snapshot.png",
                "backend": "sapien",
                "role": "snapshot",
                "sha256": "1" * 64,
                "files": [
                    {
                        "uri": "/tmp/x/snapshot.png",
                        "sha256": "1" * 64,
                        "bytes": 5,
                    }
                ],
                "metadata": {},
            },
        ],
        "articulation": {},
        "source": {
            "kind": "retrieved",
            "library": "NVIDIA Isaac Assets 5.1",
            "group": "acq_315_shears",
            "file": "061_foam_brick.usd",
            "license": {
                "spdx": None,
                "status": "unknown",
                "terms_note": "NVIDIA asset EULA",
            },
            "retrieved_at": "2026-08-08",
            "source_manifest_path": "/tmp/x/SOURCE_MANIFEST.json",
        },
        "verification": [
            {
                "backend": "sapien",
                "check": "settle",
                "verdict": "pass",
                "run_id": "20260808_import",
                "timestamp": "2026-08-08T10:00:00",
                "verified_digest": "d" * 64,
                "report_path": "/tmp/x/import_matrix.json",
            },
        ],
    }
    m.update(over)
    return m


def make_generated_source(**over):
    """source for a model a generator produced. Disjoint from the retrieved
    branch by construction: no url, no retrieval date, no source mirror --
    those describe fetching something that already existed."""
    s = {
        "kind": "generated",
        "generator": {
            "tool": "embodiedgen",
            "tool_version": "v2.1.0",
            "model": "trellis",
            "model_version": None,
            "input": {
                "type": "text",
                "prompt": "a pair of shears",
                "source_media_sha256": None,
            },
            "seed": 42,
            "params": {"n_retry": 2},
            "generated_at": "2026-08-10",
        },
        "license": {
            "spdx": None,
            "status": "unknown",
            "terms_note": "generator output terms unaudited",
        },
    }
    s.update(over)
    return s


def make_valid(**over):
    b = {
        "schema_version": ledger.SCHEMA_VERSION,
        "asset_id": "external_315_shears",
        "category": "shears",
        "kind": "rigid",
        "profile": "sapien_only",
        "external_ids": {"env_gen": "315_shears"},
        "semantics": {
            "aliases": ["shears", "scissors"],
            "colors": [],
            "materials": [],
            "identity": {
                "basis": "manifest_human",
                "evidence": "archive/external_manifest.json",
                "verified": False,
            },
        },
        "models": [make_model()],
    }
    b.update(over)
    return b


def make_cross_backend(**over):
    """A cross_backend asset: isaacsim representation + inertial key present.
    Both are what the profile means, so both live in the one factory."""
    model = make_model()
    model["physical"]["inertial"] = ledger.unknown_inertial("engine_derived")
    model["representations"].append(
        {
            "format": "usd",
            "uri": "/tmp/x/asset.usd",
            "backend": "isaacsim",
            "role": "visual_and_collision",
            "sha256": "2" * 64,
            "files": [
                {
                    "uri": "/tmp/x/asset.usd",
                    "sha256": "2" * 64,
                    "bytes": 20,
                }
            ],
            "collision_meta": {
                "mode": "unknown",
                "unknown_reason": "fixture has no collider provenance",
            },
            "metadata": {},
        }
    )
    b = make_valid(profile="cross_backend", models=[model])
    b.update(over)
    return b


def test_valid_ledger_no_violations():
    assert ledger.validate_ledger(make_valid(), check_files=False) == []


@pytest.mark.parametrize(
    ("collision_meta", "code"),
    [
        (None, "missing"),
        ({}, "missing"),
        ("opaque", "bad_type"),
        ({"mode": ""}, "bad_type"),
        ({"mode": "made_up"}, "bad_enum"),
        ({"mode": "explicit_mesh", "convex": "no"}, "bad_type"),
        ({"mode": "explicit_mesh", "approximation": 7}, "bad_type"),
        ({"mode": "explicit_mesh", "decomposer": []}, "bad_type"),
        ({"mode": "explicit_mesh", "params": []}, "bad_type"),
        ({"mode": "explicit_mesh", "hull_count": -1}, "bad_type"),
        ({"mode": "explicit_mesh", "hull_count": True}, "bad_type"),
        ({"mode": "unknown"}, "missing"),
        ({"mode": "unknown", "unknown_reason": ""}, "bad_type"),
        ({"mode": "unknown", "unknown_reason": 7}, "bad_type"),
    ],
)
def test_collision_bearing_representation_requires_typed_metadata(collision_meta, code):
    document = make_valid()
    representation = document["models"][0]["representations"][0]
    representation["role"] = "collision"
    if collision_meta is not None:
        representation["collision_meta"] = collision_meta

    codes = [violation.code for violation in ledger.validate_ledger(document, check_files=False)]
    assert code in codes


@pytest.mark.parametrize("role", ["visual", "snapshot"])
def test_non_collision_representation_rejects_collision_metadata(role):
    document = make_valid()
    representation = document["models"][0]["representations"][0]
    representation["role"] = role
    representation["collision_meta"] = {"mode": "explicit_mesh", "convex": False}

    codes = [violation.code for violation in ledger.validate_ledger(document, check_files=False)]
    assert "collision_meta_forbidden" in codes


def test_collision_metadata_extensions_are_valid_when_well_typed():
    document = make_valid()
    representation = document["models"][0]["representations"][0]
    representation["role"] = "collision"
    representation["collision_meta"] = {
        "mode": "explicit_mesh",
        "convex": False,
        "approximation": "convex_decomposition",
        "decomposer": "coacd@1.0",
        "params": {"threshold": 0.05},
        "hull_count": 4,
    }

    assert ledger.validate_ledger(document, check_files=False) == []


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda files: files[0].update(source="legacy"),
            "unexpected_field",
        ),
        (
            lambda files: files.append(dict(files[0])),
            "duplicate_file_uri",
        ),
        (
            lambda files: files.insert(
                0,
                {
                    "uri": "/tmp/z-last.glb",
                    "sha256": "f" * 64,
                    "bytes": 1,
                },
            ),
            "files_not_sorted",
        ),
    ],
)
def test_representation_files_are_canonical(mutate, code):
    document = make_valid()
    files = document["models"][0]["representations"][0]["files"]
    mutate(files)

    codes = [violation.code for violation in ledger.validate_ledger(document, check_files=False)]

    assert code in codes


def _del(path):
    def f(b):
        node = b
        parts = path.split(".")
        for p in parts[:-1]:
            node = node[int(p)] if p.isdigit() else node[p]
        last = parts[-1]
        del node[int(last) if last.isdigit() else last]

    return f


def _set(path, value):
    def f(b):
        node = b
        parts = path.split(".")
        for p in parts[:-1]:
            node = node[int(p)] if p.isdigit() else node[p]
        last = parts[-1]
        node[int(last) if last.isdigit() else last] = value

    return f


CASES = [
    (_del("schema_version"), "needs_backfill"),
    (_set("schema_version", "asset_ledger.v0"), "bad_schema_version"),
    (_set("kind", "soft"), "bad_enum"),
    (_set("semantics.aliases", []), "empty_aliases"),
    (_set("models", []), "no_models"),
    (_set("models.0.physical.conventions.stable_poses", []), "no_stable_pose"),
    (
        _set(
            "models.0.physical.conventions.stable_poses",
            [
                {
                    "pose_id": "a",
                    "orientation_wxyz": ledger.X90_WXYZ,
                    "is_default": True,
                },
                {
                    "pose_id": "b",
                    "orientation_wxyz": ledger.IDENTITY_WXYZ,
                    "is_default": True,
                },
            ],
        ),
        "multiple_default_poses",
    ),
    (
        _set(
            "models.0.physical.conventions.stable_poses",
            [{"pose_id": "a", "orientation_wxyz": [1, 1, 0, 0], "is_default": True}],
        ),
        "bad_quaternion",
    ),
    (
        _set("models.0.physical.conventions.stable_poses.0.orientation_wxyz", 1),
        "bad_quaternion",
    ),
    (
        _set(
            "models.0.physical.conventions.stable_poses.0.orientation_wxyz",
            ["1", "0", "0", "0"],
        ),
        "bad_quaternion",
    ),
    (
        _set(
            "models.0.physical.mass_kg",
            {
                "value": None,
                "status": "known",
                "runtime_default_kg": 0.1,
                "runtime_default_basis": "global_constant",
            },
        ),
        "unknown_shape",
    ),
    (
        _set(
            "models.0.physical.mass_kg",
            {
                "value": 0.5,
                "status": "estimated",
                "runtime_default_kg": 0.1,
                "runtime_default_basis": "global_constant",
            },
        ),
        "estimator_required",
    ),
    # v3: the runtime_default pair is gone; the enum row above it died with
    # it. What CAN still be malformed is the friction static/dynamic split.
    (
        _set(
            "models.0.physical.friction",
            {"value": None, "status": "unknown", "static": "high"},
        ),
        "unknown_shape",
    ),
    (_del("models.0.physical.friction"), "missing"),
    (_set("models.0.representations", []), "no_sapien_representation"),
    (_set("models.0.source.license", "unknown"), "license_not_structured"),
    (_del("models.0.source.retrieved_at"), "missing"),
    (_set("models.0.verification.0.check", "fly"), "bad_enum"),
    (_del("models.0.verification.0.verified_digest"), "missing"),
    (_del("models.0.verification.0.run_id"), "missing"),
    (_set("usable", True), "derived_field_handwritten"),
    # I-1: only a snapshot representation (even sapien-backed) doesn't count
    # as "has a sapien representation" -- the exclusion applies regardless of
    # which backend the snapshot happens to be tagged with.
    (
        _set(
            "models.0.representations",
            [
                {
                    "format": "png",
                    "uri": "/tmp/x/snapshot.png",
                    "backend": "sapien",
                    "role": "snapshot",
                    "sha256": "1" * 64,
                    "size_bytes": 5,
                }
            ],
        ),
        "no_sapien_representation",
    ),
    # I-5: mass_kg/friction present but not a dict must not silently bypass
    # validation.
    (_set("models.0.physical.mass_kg", "not-a-dict"), "unknown_shape"),
    (_set("models.0.physical.friction", "not-a-dict"), "unknown_shape"),
    # I-6: model_id and verification are required keys on a model entry
    # (an empty verification list is fine; the key itself must exist).
    (_del("models.0.model_id"), "missing"),
    (_del("models.0.verification"), "missing"),
    # M-3: sha256 format is checked even outside check_files=True (out-of-
    # contract code, declared in the report).
    (_set("models.0.representations.0.sha256", "not-a-valid-sha256"), "bad_sha256"),
    # I-4: present-but-null on a non-nullable asset-level field is as
    # unusable as an absent key.
    (_del("asset_id"), "missing"),
    (_set("asset_id", None), "missing"),
    (_set("kind", None), "missing"),
    (_set("models", None), "missing"),
    # I-4: models present but not a list (e.g. an empty dict) must not
    # silently produce zero violations.
    (_set("models", {}), "bad_type"),
    (_set("models.0", "not-a-model"), "bad_type"),
    (_set("models.0.representations.0", "not-a-representation"), "bad_type"),
    # fix-round-2: model_id/verification present-but-null must not silently
    # bypass validation either (the presence-only REQUIRED_MODEL check saw
    # the key exists and stopped there; NOT_NULLABLE_MODEL closes that gap).
    (_set("models.0.model_id", None), "missing"),
    (_set("models.0.verification", None), "missing"),
    # T8 review I-2: a directory name like "batch_v3" naively sliced as if it
    # were a YYYYMMDD date produced this exact garbage timestamp (real
    # incident -- almost shipped into the real pool). Neither
    # verification.timestamp nor source.retrieved_at may hold a string that
    # doesn't parse as an ISO date/datetime.
    (
        _set("models.0.verification.0.timestamp", "batc-h_-v3T00:00:00"),
        "bad_timestamp",
    ),
    (_set("models.0.source.retrieved_at", "batc-h_-v3T00:00:00"), "bad_timestamp"),
    # H1 hardening 5: _is_iso_datetime/_is_iso_date tightened to the
    # canonical T-form / date-form only. Bare fromisoformat() accepts a
    # strictly larger, py3.10-vs-3.11-divergent set (Z suffix, space
    # separator, compact digits, ...) -- these reject exactly that.
    (
        _set("models.0.verification.0.timestamp", "2026-08-08T10:00:00Z"),
        "bad_timestamp",
    ),  # Z suffix
    (
        _set("models.0.verification.0.timestamp", "2026-08-08 10:00:00"),
        "bad_timestamp",
    ),  # space separator instead of T
    (_set("models.0.source.retrieved_at", "20260808"), "bad_timestamp"),  # compact form
    (
        _set("models.0.verification.0.timestamp", "2026-08-08"),
        "bad_timestamp",
    ),  # bare date into timestamp (needs full T-datetime)
    (
        _set("models.0.source.retrieved_at", "2026-08-08T10:00:00"),
        "bad_timestamp",
    ),  # full datetime into retrieved_at (needs bare date)
    # ---- v2 ----------------------------------------------------------------
    # profile is declared, not inferred, so an absent or bogus one is a hard
    # failure: without it the validator cannot know WHICH required table
    # applies, and silently picking the lenient one would let a migration
    # target slip through under-checked.
    (_del("profile"), "missing"),
    (_set("profile", None), "missing"),
    (_set("profile", "isaac_only"), "bad_profile"),
    # identity: the claim "this is a cup" must say where it came from.
    (_del("semantics.identity"), "missing"),
    (_set("semantics.identity.basis", "vibes"), "bad_enum"),
    (_set("semantics.identity.verified", "yes"), "bad_type"),
    (_set("semantics.identity", ["manifest_human"]), "bad_type"),
    # source branching
    (_del("models.0.source.kind"), "missing"),
    (_set("models.0.source.kind", "downloaded"), "bad_source_kind"),
    # retrieved branch still owes its retrieval coordinates
    (_del("models.0.source.library"), "missing"),
    # cross-branch contamination: the realistic version of this is a ledger
    # copy-pasted from a neighbour and only half-edited.
    (
        _set("models.0.source.generator", {"tool": "embodiedgen"}),
        "source_field_mismatch",
    ),
    # the size identity: a converter whose unit assumption drifts produces an
    # asset that is still field-by-field valid and 100x the wrong size.
    (
        _set("models.0.physical.mesh_bbox_m", [7.8, 0.051, 0.053]),
        "size_invariant_mismatch",
    ),
    # extras is typed but never interpreted -- and must actually be a mapping.
    (_set("models.0.extras", ["affordance"]), "bad_type"),
    # v3 is not merely a version label: every newly-added integrity field is
    # mandatory, and every deleted v2 field is forbidden. These mutations
    # model documents that were relabelled v3 without content migration.
    (_del("external_ids.env_gen"), "missing"),
    (_set("external_ids.env_gen", ""), "unsafe_identifier"),
    (_set("external_ids", []), "missing"),
    (
        _del("models.0.physical.conventions.stable_poses.0.measured_against"),
        "measured_against_required",
    ),
    (
        _del("models.0.physical.conventions.stable_poses.0.measured_against.run_id"),
        "measured_against_required",
    ),
    (_del("models.0.representations.0.files"), "missing"),
    (_set("models.0.representations.0.files", []), "missing"),
    (_set("models.0.representations.0.files", {}), "bad_type"),
    (_set("models.0.representations.0.files.0", "not-a-file"), "bad_type"),
    (_set("models.0.representations.0.files.0.bytes", -1), "bad_type"),
    (_set("models.0.representations.0.files.0.bytes", True), "bad_type"),
    (_set("models.0.representations.0.files.0.sha256", "bad"), "bad_sha256"),
    (_set("models.0.representations.0.files.0.uri", ""), "bad_type"),
    (
        _set("models.0.representations.0.files.0.uri", "/tmp/x/other.glb"),
        "primary_file_missing",
    ),
    (
        _set("models.0.physical.conventions.stable_poses.0.measured_against.backend", "bullet"),
        "bad_enum",
    ),
    (
        _set("models.0.physical.conventions.stable_poses.0.measured_against.run_id", ""),
        "measured_against_required",
    ),
    (_set("semantic_name", "shears"), "deleted_field"),
    (_set("tags", ["rigid"]), "deleted_field"),
    (_set("models.0.physical.mesh_up_axis", "Y"), "deleted_field"),
    (_set("models.0.physical.origin_convention", "bottom-center"), "deleted_field"),
    (_set("models.0.physical.mass_kg.runtime_default_kg", 0.1), "deleted_field"),
    (
        _set("models.0.physical.mass_kg.runtime_default_basis", "global_constant"),
        "deleted_field",
    ),
    (_set("models.0.physical.friction.runtime_default", None), "deleted_field"),
    (
        _set("models.0.physical.friction.runtime_default_basis", "none"),
        "deleted_field",
    ),
    (
        _set("models.0.physical.friction.runtime_default_vendor_hint", "legacy"),
        "deleted_field",
    ),
    (_set("models.0.representations.0.size_bytes", 10), "deleted_field"),
    (_set("models.0.physical.conventions.stable_poses", {"pose": 1}), "bad_type"),
    (_set("models.0.physical.conventions.stable_poses", ["opaque"]), "bad_type"),
    (_set("models.0.physical.mass_kg.status", "opaque"), "bad_enum"),
    (_set("models.0.representations", {}), "bad_type"),
    (_set("models.0.representations", None), "no_sapien_representation"),
    (_del("models.0.representations.0.format"), "missing"),
    (_set("models.0.representations.0.role", "physics"), "bad_enum"),
    (_del("models.0.representations.0.backend"), "missing"),
    (_set("models.0.representations.0.backend", "bullet"), "bad_enum"),
    (_set("models.0.source", ["retrieved"]), "unknown_shape"),
    (_set("models.0.source.library", None), "missing"),
    (_del("models.0.source.license.status"), "license_not_structured"),
    (_set("models.0.source.license.status", "unlicensed"), "bad_enum"),
    (_set("models.0.verification.0.verdict", "maybe"), "bad_enum"),
    (_set("models.0.physical.restitution", "springy"), "unknown_shape"),
    (_set("models.0.physical.restitution", {"status": "opaque"}), "bad_enum"),
    (_set("models.0.appearance", "red"), "unknown_shape"),
    (_set("models.0.appearance", {}), "measured_against_required"),
    (
        _set(
            "models.0.appearance",
            {
                "colors_measured": ["red"],
                "measured_against": {"backend": "bullet"},
            },
        ),
        "bad_enum",
    ),
    (_set("models.0.physical.placement", "table"), "unknown_shape"),
]

GENERATED_CASES = [
    # A generated model owes its generation lineage: nothing else can
    # reconstruct which model, which prompt, which seed produced this mesh
    # once the run is over.
    (_del("models.0.source.generator.tool_version"), "missing"),
    (_del("models.0.source.generator.input"), "missing"),
    (_del("models.0.source.generator.params"), "missing"),
    (_del("models.0.source.generator.generated_at"), "missing"),
    (_set("models.0.source.generator.params", ["n_retry"]), "bad_type"),
    (_set("models.0.source.generator.input.type", "audio"), "bad_enum"),
    (
        _set("models.0.source.generator.generated_at", "2026-08-10T10:00:00"),
        "bad_timestamp",
    ),
    # retrieval fields on a generated model: the same half-edited-copy defect
    # seen from the other side.
    (_set("models.0.source.url", "https://example.com/x.usd"), "source_field_mismatch"),
    (_set("models.0.source.retrieved_at", "2026-08-10"), "source_field_mismatch"),
]


@pytest.mark.parametrize("mutate,code", CASES)
def test_violations(mutate, code):
    b = make_valid()
    mutate(b)
    codes = [v.code for v in ledger.validate_ledger(b, check_files=False)]
    assert code in codes, f"expected {code}, got {codes}"


def make_generated_valid(**over):
    model = make_model(source=make_generated_source())
    b = make_valid(models=[model])
    b.update(over)
    return b


def test_generated_source_is_valid():
    assert ledger.validate_ledger(make_generated_valid(), check_files=False) == []


def test_generated_seed_and_model_version_may_be_null():
    """A null seed is a fact, not an omission: it states that this generation
    is not reproducible, which a release gate can act on. An absent key states
    nothing."""
    b = make_generated_valid()
    b["models"][0]["source"]["generator"]["seed"] = None
    b["models"][0]["source"]["generator"]["model_version"] = None
    assert ledger.validate_ledger(b, check_files=False) == []


@pytest.mark.parametrize("mutate,code", GENERATED_CASES)
def test_generated_violations(mutate, code):
    b = make_generated_valid()
    mutate(b)
    codes = [v.code for v in ledger.validate_ledger(b, check_files=False)]
    assert code in codes, f"expected {code}, got {codes}"


def test_cross_backend_profile_is_valid():
    assert ledger.validate_ledger(make_cross_backend(), check_files=False) == []


def test_cross_backend_requires_isaac_representation_and_inertial():
    """The same absence that is correct under sapien_only is a visible debt
    under cross_backend. That asymmetry is the whole point of declaring what
    an asset is for."""
    plain = make_valid()
    assert ledger.validate_ledger(plain, check_files=False) == []

    promoted = make_valid(profile="cross_backend")
    codes = [v.code for v in ledger.validate_ledger(promoted, check_files=False)]
    assert codes.count("profile_requirement_unmet") == 2, codes


def test_profile_does_not_restrict_what_may_be_stored():
    """profile gates what MUST be present, never what MAY be: a sapien_only
    asset carrying inertial data and an isaacsim USD is perfectly legal."""
    b = make_cross_backend(profile="sapien_only")
    assert ledger.validate_ledger(b, check_files=False) == []


def test_extras_is_preserved_and_never_affects_usable():
    b = make_valid()
    b["models"][0]["extras"] = {"affordance": {"graspable_parts": ["handle"]}}
    assert ledger.validate_ledger(b, check_files=False) == []
    ok, missing = ledger.derive_usable(b, 0)
    assert ok is True and missing == []
    # survives the IR round-trip rather than being dropped on the floor
    bundle = ledger.to_ir_bundles(b)[0]
    assert bundle["asset_id"].endswith("_m0")


def test_size_invariant_holds_for_a_rescaled_model():
    """actual_max_dim_m is the PRE-scale reading; the bbox is measured after
    scaling. backfill_upstream used to write the post-scale value under the
    same name, which is exactly what this identity now catches."""
    b = make_valid()
    ph = b["models"][0]["physical"]
    ph["mesh_bbox_m"] = [0.039, 0.0255, 0.0265]
    ph["size_resolution"]["actual_max_dim_m"] = 0.078
    ph["size_resolution"]["scale"] = 0.5
    assert ledger.validate_ledger(b, check_files=False) == []

    ph["size_resolution"]["actual_max_dim_m"] = 0.039  # post-scale, the old bug
    codes = [v.code for v in ledger.validate_ledger(b, check_files=False)]
    assert "size_invariant_mismatch" in codes


def test_derive_usable_is_profile_aware():
    """usable means "meets the contract this asset declared" -- so promoting
    the profile without the evidence must flip it, not silently pass."""
    ok, missing = ledger.derive_usable(make_cross_backend(), 0)
    assert ok is True and missing == []

    ok, missing = ledger.derive_usable(make_valid(profile="cross_backend"), 0)
    assert ok is False
    assert any("inertial" in m for m in missing)
    assert any("backend=isaacsim" in m for m in missing)


@pytest.mark.parametrize(
    ("mutate", "missing_suffix"),
    [
        (_del("external_ids.env_gen"), "external_ids.env_gen"),
        (
            _del("models.0.physical.conventions.stable_poses.0.measured_against"),
            "stable_poses.0.measured_against",
        ),
        (_del("models.0.representations.0.files"), "representations.0.files"),
    ],
)
def test_derive_usable_requires_v3_integrity_fields(mutate, missing_suffix):
    document = make_valid()
    mutate(document)

    usable, missing = ledger.derive_usable(document, 0)

    assert not usable
    assert any(path.endswith(missing_suffix) for path in missing)


def test_derive_usable_requires_collision_provenance():
    document = make_valid()
    representation = document["models"][0]["representations"][0]
    representation["role"] = "collision"

    usable, missing = ledger.derive_usable(document, 0)

    assert not usable
    assert any(path.endswith("representations.0.collision_meta") for path in missing)


def test_inertial_unknown_is_a_complete_answer():
    """engine_derived + null values is not a placeholder: it positively says
    'no asset-side measurement exists, the engine infers this'."""
    b = make_cross_backend()
    assert ledger.validate_ledger(b, check_files=False) == []
    inertial = b["models"][0]["physical"]["inertial"]
    assert inertial["status"] == "unknown" and inertial["basis"] == "engine_derived"

    inertial["status"] = "estimated"
    codes = [v.code for v in ledger.validate_ledger(b, check_files=False)]
    assert "estimator_required" in codes

    inertial.update({"status": "known", "basis": "measured", "com_m": [0, 0, 1, 2]})
    codes = [v.code for v in ledger.validate_ledger(b, check_files=False)]
    assert "bad_type" in codes


def test_duplicate_model_id():
    b = make_valid(models=[make_model(), make_model()])  # 两个 model_id=0
    codes = [v.code for v in ledger.validate_ledger(b, check_files=False)]
    assert "duplicate_model_id" in codes


def test_missing_model_id_not_falsely_duplicate():
    # I-6: two models that both LACK the model_id key (not "both set to the
    # same id") must each get their own "missing" violation, and must not
    # also be reported as duplicates of each other.
    m0 = make_model()
    del m0["model_id"]
    m1 = make_model()
    del m1["model_id"]
    b = make_valid(models=[m0, m1])
    codes = [v.code for v in ledger.validate_ledger(b, check_files=False)]
    assert "duplicate_model_id" not in codes
    assert codes.count("missing") >= 2


def test_null_model_id_not_falsely_duplicate():
    # fix-round-2: same as test_missing_model_id_not_falsely_duplicate but
    # for model_id explicitly set to None (present-but-null) rather than the
    # key being absent -- this was the actual regression the reviewer found:
    # the round-1 fix's `if mid is not None:` dedup guard combined with
    # _check_required's null-blind presence check let a null model_id
    # through with ZERO violations (worse than the original false-positive
    # duplicate_model_id bug it replaced).
    b = make_valid(models=[make_model(model_id=None), make_model(model_id=None)])
    codes = [v.code for v in ledger.validate_ledger(b, check_files=False)]
    assert "duplicate_model_id" not in codes
    assert codes.count("missing") >= 2


@pytest.mark.parametrize("field", ["asset_id", "external_ids.env_gen"])
@pytest.mark.parametrize(
    "value",
    [
        ".",
        "..",
        "../escape",
        "nested/asset",
        r"nested\asset",
        "bad\x00id",
        "bad\nid",
        "a:b",
        "CON",
        "name.",
        "space name",
        "资产",
    ],
)
def test_asset_identifiers_reject_path_semantics_and_control_characters(field, value):
    document = make_valid()
    node = document
    parts = field.split(".")
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value

    violations = ledger.validate_ledger(document, check_files=False)

    assert any(item.path == field and item.code == "unsafe_identifier" for item in violations)


def test_asset_identifiers_keep_non_path_portable_characters_legal():
    document = make_valid()
    document["asset_id"] = "external.vendor-v2_asset"
    document["external_ids"]["env_gen"] = "301_widget.v2-beta"

    assert ledger.validate_ledger(document, check_files=False) == []


@pytest.mark.parametrize("value", ["a:b", "CON", "name.", "space name", "资产"])
def test_canonical_asset_key_rejects_nonportable_platform_segments(value):
    with pytest.raises(ledger.UnsafeAssetKeyError):
        ledger.canonical_asset_key(value)


def test_articulated_requires_articulation():
    b = make_valid(kind="articulated")
    codes = [v.code for v in ledger.validate_ledger(b, check_files=False)]
    assert "articulation_required" in codes


def test_check_files(tmp_path):
    f = tmp_path / "visual.glb"
    f.write_bytes(b"mesh")
    b = make_valid()
    b["models"][0]["representations"][0]["uri"] = str(f)  # sha 仍 0*64
    b["models"][0]["representations"][0]["files"][0]["uri"] = str(f)
    codes = [v.code for v in ledger.validate_ledger(b, check_files=True)]
    assert "sha256_mismatch" in codes
    b2 = make_valid()
    gone = str(tmp_path / "gone.glb")
    b2["models"][0]["representations"][0]["uri"] = gone
    b2["models"][0]["representations"][0]["files"][0]["uri"] = gone
    assert "file_missing" in [v.code for v in ledger.validate_ledger(b2, check_files=True)]


def test_check_files_verifies_every_representation_member(tmp_path):
    primary = tmp_path / "scene.gltf"
    payload = tmp_path / "payload.bin"
    primary.write_bytes(
        b'{"asset":{"version":"2.0"},"buffers":[{"byteLength":7,"uri":"payload.bin"}]}'
    )
    payload.write_bytes(b"payload")
    primary_sha = hashlib.sha256(primary.read_bytes()).hexdigest()
    payload_sha = hashlib.sha256(payload.read_bytes()).hexdigest()
    document = make_valid()
    representation = document["models"][0]["representations"][0]
    representation.update(
        {
            "format": "gltf",
            "uri": str(primary),
            "sha256": primary_sha,
            "files": sorted(
                [
                    {
                        "uri": str(primary),
                        "sha256": primary_sha,
                        "bytes": primary.stat().st_size,
                    },
                    {"uri": str(payload), "sha256": payload_sha, "bytes": 7},
                ],
                key=lambda member: member["uri"],
            ),
        }
    )
    document["models"][0]["representations"] = [representation]
    assert ledger.validate_ledger(document, check_files=True) == []

    payload_index = next(
        index
        for index, member in enumerate(representation["files"])
        if member["uri"] == str(payload)
    )
    representation["files"][payload_index]["bytes"] = 8
    violations = ledger.validate_ledger(document, check_files=True)
    assert any(
        item.path == f"models.0.representations.0.files.{payload_index}.bytes"
        and item.code == "bytes_mismatch"
        for item in violations
    )
    representation["files"][payload_index]["bytes"] = 7
    payload.write_bytes(b"changed")
    violations = ledger.validate_ledger(document, check_files=True)
    assert any(
        item.path == f"models.0.representations.0.files.{payload_index}.sha256"
        and item.code == "sha256_mismatch"
        for item in violations
    )


def test_check_files_reports_primary_that_is_not_a_declared_member(tmp_path):
    member = tmp_path / "member.stl"
    member.write_bytes(b"mesh")
    member_sha = hashlib.sha256(b"mesh").hexdigest()
    document = make_valid()
    representation = document["models"][0]["representations"][0]
    representation.update(
        {
            "format": "stl",
            "uri": str(tmp_path / "primary.stl"),
            "sha256": member_sha,
            "files": [{"uri": str(member), "sha256": member_sha, "bytes": 4}],
        }
    )
    document["models"][0]["representations"] = [representation]

    violations = ledger.validate_ledger(document, check_files=True)

    assert any(item.code == "primary_file_missing" for item in violations)


@pytest.mark.parametrize(
    ("asset_format", "primary_name", "primary_bytes"),
    [
        (
            "urdf",
            "mobility.urdf",
            b"<robot><link name='base'><visual><geometry>"
            b"<mesh filename='meshes/missing.obj'/></geometry></visual></link></robot>",
        ),
        ("obj", "model.obj", b"mtllib materials/missing.mtl\nv 0 0 0\n"),
    ],
)
def test_check_files_rejects_unlisted_or_missing_dependency_closure(
    tmp_path, asset_format, primary_name, primary_bytes
):
    primary = tmp_path / primary_name
    primary.write_bytes(primary_bytes)
    primary_sha = hashlib.sha256(primary_bytes).hexdigest()
    document = make_valid()
    representation = document["models"][0]["representations"][0]
    representation.update(
        {
            "format": asset_format,
            "uri": str(primary),
            "sha256": primary_sha,
            "files": [{"uri": str(primary), "sha256": primary_sha, "bytes": len(primary_bytes)}],
        }
    )

    violations = ledger.validate_ledger(document, check_files=True)

    assert any(item.code == "representation_file_closure_incomplete" for item in violations)


def test_check_files_rejects_declared_members_outside_exact_loader_closure(tmp_path):
    primary = tmp_path / "model.stl"
    unrelated = tmp_path / "unrelated.bin"
    primary.write_bytes(b"mesh")
    unrelated.write_bytes(b"not loaded")
    primary_sha = hashlib.sha256(primary.read_bytes()).hexdigest()
    unrelated_sha = hashlib.sha256(unrelated.read_bytes()).hexdigest()
    document = make_valid()
    representation = document["models"][0]["representations"][0]
    representation.update(
        {
            "format": "stl",
            "uri": str(primary),
            "sha256": primary_sha,
            "files": sorted(
                [
                    {"uri": str(primary), "sha256": primary_sha, "bytes": 4},
                    {
                        "uri": str(unrelated),
                        "sha256": unrelated_sha,
                        "bytes": len(b"not loaded"),
                    },
                ],
                key=lambda member: member["uri"],
            ),
        }
    )
    document["models"][0]["representations"] = [representation]

    violations = ledger.validate_ledger(document, check_files=True)

    assert any(item.code == "representation_file_closure_extra" for item in violations)


def test_representation_format_must_match_primary_uri_suffix():
    document = make_valid()
    representation = document["models"][0]["representations"][0]
    representation["format"] = "glb"
    representation["uri"] = "/assets/mobility.urdf"
    representation["files"][0]["uri"] = "/assets/mobility.urdf"

    violations = ledger.validate_ledger(document, check_files=False)

    assert any(item.code == "representation_format_mismatch" for item in violations)


def test_representation_format_requires_a_primary_uri_suffix():
    document = make_valid()
    representation = document["models"][0]["representations"][0]
    representation["format"] = "glb"
    representation["uri"] = "/assets/model"
    representation["files"][0]["uri"] = "/assets/model"

    violations = ledger.validate_ledger(document, check_files=False)

    assert any(item.code == "representation_format_mismatch" for item in violations)


def test_representation_format_rejects_malformed_uri_without_crashing():
    document = make_valid()
    representation = document["models"][0]["representations"][0]
    representation["uri"] = "http://["
    representation["files"][0]["uri"] = "http://["

    violations = ledger.validate_ledger(document, check_files=False)

    assert any(item.code == "representation_format_mismatch" for item in violations)


def test_representation_format_and_uri_must_be_non_empty_strings():
    document = make_valid()
    representation = document["models"][0]["representations"][0]
    representation["format"] = 7
    representation["uri"] = []

    violations = ledger.validate_ledger(document, check_files=False)

    assert {item.path for item in violations if item.code == "bad_type"} >= {
        "models.0.representations.0.format",
        "models.0.representations.0.uri",
    }
    assert ledger.validate_ledger(document, check_files=True)


def _glb_json_chunk(document):
    encoded = json.dumps(document, separators=(",", ":")).encode()
    return encoded + b" " * (-len(encoded) % 4)


def _glb_bytes(document, binary=b"\x00\x01\x02\x03"):
    encoded = _glb_json_chunk(document)
    binary += b"\x00" * (-len(binary) % 4)
    chunks = struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
    chunks += struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks


def _raw_glb(*chunks, magic=b"glTF", version=2, declared_size=None, trailer=b""):
    body = (
        b"".join(
            struct.pack("<II", len(payload), chunk_type) + payload for chunk_type, payload in chunks
        )
        + trailer
    )
    actual_size = 12 + len(body)
    return (
        struct.pack(
            "<4sII",
            magic,
            version,
            actual_size if declared_size is None else declared_size,
        )
        + body
    )


def _file_backed_document(
    tmp_path,
    primary_name,
    primary_payload,
    *,
    dependencies=None,
    asset_format=None,
):
    payloads = {primary_name: primary_payload, **(dependencies or {})}
    members = []
    for relative_name, payload in payloads.items():
        path = tmp_path / relative_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        members.append(
            {
                "uri": str(path),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
        )
    primary = tmp_path / primary_name
    primary_sha = hashlib.sha256(primary_payload).hexdigest()
    document = make_valid()
    representation = document["models"][0]["representations"][0]
    representation.update(
        {
            "format": asset_format or primary.suffix.lstrip("."),
            "uri": str(primary),
            "sha256": primary_sha,
            "files": sorted(members, key=lambda member: member["uri"]),
        }
    )
    document["models"][0]["representations"] = [representation]
    return document


def test_check_files_parses_glb_json_and_binds_external_images(tmp_path):
    primary = tmp_path / "model.glb"
    texture = tmp_path / "texture.png"
    payload = _glb_bytes(
        {
            "asset": {"version": "2.0"},
            "buffers": [{"byteLength": 4}],
            "images": [{"uri": "texture.png"}],
        }
    )
    primary.write_bytes(payload)
    texture.write_bytes(b"png")
    document = make_valid()
    representation = document["models"][0]["representations"][0]
    representation.update(
        {
            "format": "glb",
            "uri": str(primary),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "files": [
                {
                    "uri": str(primary),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "bytes": len(payload),
                }
            ],
        }
    )
    document["models"][0]["representations"] = [representation]

    incomplete = ledger.validate_ledger(document, check_files=True)
    assert any(item.code == "representation_file_closure_incomplete" for item in incomplete)

    representation["files"].append(
        {
            "uri": str(texture),
            "sha256": hashlib.sha256(b"png").hexdigest(),
            "bytes": 3,
        }
    )
    representation["files"].sort(key=lambda member: member["uri"])
    assert ledger.validate_ledger(document, check_files=True) == []


def test_check_files_accepts_json_only_glb_without_binary_payload(tmp_path):
    payload = _raw_glb((0x4E4F534A, _glb_json_chunk({"asset": {"version": "2.0"}})))
    document = _file_backed_document(tmp_path, "model.glb", payload)

    assert ledger.validate_ledger(document, check_files=True) == []


def test_check_files_rejects_glb_with_truncated_declared_buffer(tmp_path):
    primary = tmp_path / "model.glb"
    payload = _glb_bytes(
        {
            "asset": {"version": "2.0"},
            "buffers": [{"byteLength": 100}],
        },
        binary=b"tiny",
    )
    primary.write_bytes(payload)
    primary_sha = hashlib.sha256(payload).hexdigest()
    document = make_valid()
    representation = document["models"][0]["representations"][0]
    representation.update(
        {
            "format": "glb",
            "uri": str(primary),
            "sha256": primary_sha,
            "files": [{"uri": str(primary), "sha256": primary_sha, "bytes": len(payload)}],
        }
    )
    document["models"][0]["representations"] = [representation]

    violations = ledger.validate_ledger(document, check_files=True)

    assert any(
        item.code == "representation_file_closure_unverifiable"
        and "GLB BIN chunk does not match buffer.byteLength" in item.message
        for item in violations
    )


def test_check_files_rejects_unaligned_glb_chunk(tmp_path):
    primary = tmp_path / "model.glb"
    json_chunk = b"{} "
    chunks = struct.pack("<II", len(json_chunk), 0x4E4F534A) + json_chunk
    payload = struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks
    primary.write_bytes(payload)
    primary_sha = hashlib.sha256(payload).hexdigest()
    document = make_valid()
    representation = document["models"][0]["representations"][0]
    representation.update(
        {
            "format": "glb",
            "uri": str(primary),
            "sha256": primary_sha,
            "files": [{"uri": str(primary), "sha256": primary_sha, "bytes": len(payload)}],
        }
    )
    document["models"][0]["representations"] = [representation]

    violations = ledger.validate_ledger(document, check_files=True)

    assert any(
        item.code == "representation_file_closure_unverifiable"
        and "GLB chunk length is not 4-byte aligned" in item.message
        for item in violations
    )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (_raw_glb((0x4E4F534A, b"{}  "), magic=b"BAD!"), "GLB header is invalid"),
        (_raw_glb((0x4E4F534A, b"{}  "), version=1), "GLB header is invalid"),
        (
            _raw_glb((0x4E4F534A, b"{}  "), declared_size=999),
            "GLB header is invalid",
        ),
        (
            _raw_glb((0x4E4F534A, b"{}  "), trailer=b"tail"),
            "GLB chunk header is truncated",
        ),
        (
            struct.pack("<4sII", b"glTF", 2, 20) + struct.pack("<II", 4, 0x4E4F534A),
            "GLB chunk payload is truncated",
        ),
        (_raw_glb((0x004E4942, b"")), "GLB first chunk is not JSON"),
        (
            _raw_glb((0x4E4F534A, b"{}  "), (0x4E4F534A, b"{}  ")),
            "GLB has multiple JSON chunks",
        ),
        (_raw_glb((0x4E4F534A, b"\xff   ")), "GLB JSON chunk is not UTF-8"),
        (
            _raw_glb(
                (0x4E4F534A, _glb_json_chunk({"buffers": [{"byteLength": 1}]})),
                (0x004E4942, b"x\x00\x00\x00"),
                (0x004E4942, b"x\x00\x00\x00"),
            ),
            "GLB has multiple BIN chunks",
        ),
        (
            _raw_glb((0x4E4F534A, b"{}  "), (0x12345678, b"data")),
            "GLB has unsupported chunk type",
        ),
        (
            _raw_glb((0x4E4F534A, b"{}  "), (0x004E4942, b"data")),
            "GLB BIN chunk has no unique implicit buffer",
        ),
        (
            _raw_glb((0x4E4F534A, _glb_json_chunk({"buffers": [{"byteLength": 1}]}))),
            "glTF buffer has no bound payload",
        ),
        (
            _raw_glb(
                (
                    0x4E4F534A,
                    _glb_json_chunk({"buffers": [{"byteLength": 1}, {"byteLength": 1}]}),
                ),
                (0x004E4942, b"data"),
            ),
            "glTF buffer has no bound payload",
        ),
        (
            _raw_glb((0x4E4F534A, b"{}\x00\x00")),
            "GLB JSON chunk padding must use spaces",
        ),
        (
            _raw_glb(
                (0x4E4F534A, _glb_json_chunk({"buffers": [{"byteLength": 1}]})),
                (0x004E4942, b"xabc"),
            ),
            "GLB BIN chunk padding must use zero bytes",
        ),
    ],
)
def test_check_files_rejects_malformed_glb_structure(tmp_path, payload, message):
    document = _file_backed_document(tmp_path, "model.glb", payload)

    violations = ledger.validate_ledger(document, check_files=True)

    assert any(
        item.code == "representation_file_closure_unverifiable" and message in item.message
        for item in violations
    )


def test_check_files_reports_nul_dependency_uri_without_crashing(tmp_path):
    primary = tmp_path / "model.gltf"
    payload = b'{"asset":{"version":"2.0"},"buffers":[{"uri":"bad\\u0000.bin","byteLength":1}]}'
    primary.write_bytes(payload)
    primary_sha = hashlib.sha256(payload).hexdigest()
    document = make_valid()
    representation = document["models"][0]["representations"][0]
    representation.update(
        {
            "format": "gltf",
            "uri": str(primary),
            "sha256": primary_sha,
            "files": [{"uri": str(primary), "sha256": primary_sha, "bytes": len(payload)}],
        }
    )
    document["models"][0]["representations"] = [representation]

    violations = ledger.validate_ledger(document, check_files=True)

    assert any(
        item.code == "representation_file_closure_incomplete"
        and "non-canonical dependency path" in item.message
        for item in violations
    )


def test_check_files_rejects_dot_segment_dependency_alias(tmp_path):
    primary = tmp_path / "model.gltf"
    texture = tmp_path / "texture.png"
    payload = b'{"asset":{"version":"2.0"},"images":[{"uri":"./texture.png"}]}'
    primary.write_bytes(payload)
    texture.write_bytes(b"png")
    primary_sha = hashlib.sha256(payload).hexdigest()
    texture_sha = hashlib.sha256(b"png").hexdigest()
    document = make_valid()
    representation = document["models"][0]["representations"][0]
    representation.update(
        {
            "format": "gltf",
            "uri": str(primary),
            "sha256": primary_sha,
            "files": sorted(
                [
                    {"uri": str(primary), "sha256": primary_sha, "bytes": len(payload)},
                    {"uri": str(texture), "sha256": texture_sha, "bytes": 3},
                ],
                key=lambda member: member["uri"],
            ),
        }
    )
    document["models"][0]["representations"] = [representation]

    violations = ledger.validate_ledger(document, check_files=True)

    assert any(
        item.code == "representation_file_closure_incomplete"
        and "non-canonical dependency path" in item.message
        for item in violations
    )


@pytest.mark.parametrize(
    ("reference", "message"),
    [
        (" texture.png ", "non-canonical dependency path"),
        ("https://example.com/texture.png", "external or decorated dependency URI"),
        ("http://[", "dependency URI cannot be parsed safely"),
        ("//example.com/texture.png", "external or decorated dependency URI"),
        ("texture.png?version=1", "external or decorated dependency URI"),
        ("texture.png#image", "external or decorated dependency URI"),
        (r"textures\texture.png", "non-canonical dependency path"),
        ("texture%2epng", "non-canonical dependency path"),
        ("/tmp/texture.png", "dependency path escapes its asset tree"),
        ("../texture.png", "dependency path escapes its asset tree"),
        ("textures//texture.png", "non-canonical dependency path"),
    ],
)
def test_check_files_rejects_unsafe_dependency_references(tmp_path, reference, message):
    payload = f"<robot><mesh filename='{reference}'/></robot>".encode()
    document = _file_backed_document(tmp_path, "model.urdf", payload)

    violations = ledger.validate_ledger(document, check_files=True)

    assert any(
        item.code == "representation_file_closure_incomplete" and message in item.message
        for item in violations
    )


def test_check_files_rejects_empty_usda_asset_reference(tmp_path):
    document = _file_backed_document(
        tmp_path,
        "scene.usda",
        b'#usda 1.0\ndef Xform "Root" (references = @ @) {}\n',
    )

    violations = ledger.validate_ledger(document, check_files=True)

    assert any(
        item.code == "representation_file_closure_incomplete"
        and "dependency reference must be a non-empty string" in item.message
        for item in violations
    )


@pytest.mark.parametrize(
    ("primary_name", "primary_payload", "reference", "outside_name", "outside_payload"),
    [
        (
            "model.urdf",
            b"<robot><mesh filename='mesh.obj'/></robot>",
            "mesh.obj",
            "mesh.obj",
            b"v 0 0 0\n",
        ),
        (
            "model.obj",
            b"mtllib material.mtl\n",
            "material.mtl",
            "material.mtl",
            b"newmtl body\n",
        ),
        (
            "model.urdf",
            b"<robot><mesh filename='payload/mesh.obj'/></robot>",
            "payload",
            "mesh.obj",
            b"v 0 0 0\n",
        ),
    ],
)
def test_check_files_rejects_dependency_symlink_escape(
    tmp_path,
    primary_name,
    primary_payload,
    reference,
    outside_name,
    outside_payload,
):
    asset_root = tmp_path / "asset"
    outside_root = tmp_path / "host"
    outside_root.mkdir()
    outside = outside_root / outside_name
    outside.write_bytes(outside_payload)
    document = _file_backed_document(asset_root, primary_name, primary_payload)
    if reference == "payload":
        (asset_root / reference).symlink_to(outside_root, target_is_directory=True)
    else:
        (asset_root / reference).symlink_to(outside)
    representation = document["models"][0]["representations"][0]
    representation["files"].append(
        {
            "uri": str(outside),
            "sha256": hashlib.sha256(outside_payload).hexdigest(),
            "bytes": len(outside_payload),
        }
    )
    representation["files"].sort(key=lambda member: member["uri"])

    violations = ledger.validate_ledger(document, check_files=True)

    assert any(
        item.code == "representation_file_closure_incomplete"
        and "dependency path traverses a symlink" in item.message
        for item in violations
    )


@pytest.mark.parametrize("via_parent", [False, True])
def test_check_files_rejects_symlinked_representation_member(tmp_path, via_parent):
    asset_root = tmp_path / "asset"
    outside_root = tmp_path / "host"
    asset_root.mkdir()
    outside_root.mkdir()
    outside = outside_root / "model.stl"
    outside.write_bytes(b"host mesh")
    if via_parent:
        (asset_root / "payload").symlink_to(outside_root, target_is_directory=True)
        member_path = asset_root / "payload" / "model.stl"
    else:
        member_path = asset_root / "model.stl"
        member_path.symlink_to(outside)
    payload_sha = hashlib.sha256(b"host mesh").hexdigest()
    document = make_valid()
    representation = document["models"][0]["representations"][0]
    representation.update(
        {
            "format": "stl",
            "uri": str(member_path),
            "sha256": payload_sha,
            "files": [
                {
                    "uri": str(member_path),
                    "sha256": payload_sha,
                    "bytes": len(b"host mesh"),
                }
            ],
        }
    )
    document["models"][0]["representations"] = [representation]

    violations = ledger.validate_ledger(document, check_files=True)

    assert any(
        item.path == "models.0.representations.0.files.0.uri"
        and item.code == "representation_file_symlink_forbidden"
        for item in violations
    )


def test_check_files_rejects_aliases_for_the_same_declared_file(tmp_path):
    payload = b"mtllib missing.mtl\n"
    primary = tmp_path / "model.obj"
    primary.write_bytes(payload)
    primary_sha = hashlib.sha256(payload).hexdigest()
    canonical_uri = str(primary)
    aliased_uri = f"{tmp_path}/./model.obj"
    document = make_valid()
    representation = document["models"][0]["representations"][0]
    representation.update(
        {
            "format": "obj",
            "uri": aliased_uri,
            "sha256": primary_sha,
            "files": sorted(
                [
                    {"uri": aliased_uri, "sha256": primary_sha, "bytes": len(payload)},
                    {"uri": canonical_uri, "sha256": primary_sha, "bytes": len(payload)},
                ],
                key=lambda member: member["uri"],
            ),
        }
    )
    document["models"][0]["representations"] = [representation]

    violations = ledger.validate_ledger(document, check_files=True)

    assert any(item.code == "representation_file_path_alias" for item in violations)


def test_check_files_reports_unreadable_member_without_crashing(tmp_path, monkeypatch):
    document = _file_backed_document(tmp_path, "model.stl", b"mesh")
    primary = tmp_path / "model.stl"
    original = Path.open

    def fail_for_member(path, *args, **kwargs):
        if path == primary:
            raise OSError("read denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_for_member)

    violations = ledger.validate_ledger(document, check_files=True)

    assert any(item.code == "representation_file_unverifiable" for item in violations)


def test_check_files_reports_invalid_member_path_without_crashing():
    document = make_valid()
    representation = document["models"][0]["representations"][0]
    representation["format"] = "stl"
    representation["uri"] = "bad\x00.stl"
    representation["files"] = [{"uri": "bad\x00.stl", "sha256": "0" * 64, "bytes": 1}]

    violations = ledger.validate_ledger(document, check_files=True)

    assert any(
        item.path == "models.0.representations.0.files.0.uri"
        and item.code == "representation_file_path_unverifiable"
        for item in violations
    )


def test_check_files_reports_filesystem_inspection_error_without_crashing(tmp_path, monkeypatch):
    document = _file_backed_document(tmp_path, "model.stl", b"mesh")
    original = Path.is_symlink

    def fail_for_member(path):
        if path.name == "model.stl":
            raise OSError("lstat denied")
        return original(path)

    monkeypatch.setattr(Path, "is_symlink", fail_for_member)

    violations = ledger.validate_ledger(document, check_files=True)

    assert any(
        item.code == "representation_file_path_unverifiable"
        and "cannot inspect file path safely" in item.message
        for item in violations
    )


def test_check_files_reports_dependency_inspection_error_without_crashing(tmp_path, monkeypatch):
    document = _file_backed_document(
        tmp_path,
        "model.urdf",
        b"<robot><mesh filename='dependency.obj'/></robot>",
    )
    original = Path.is_symlink

    def fail_for_dependency(path):
        if path.name == "dependency.obj":
            raise OSError("lstat denied")
        return original(path)

    monkeypatch.setattr(Path, "is_symlink", fail_for_dependency)

    violations = ledger.validate_ledger(document, check_files=True)

    assert any(
        item.code == "representation_file_closure_incomplete"
        and "cannot inspect dependency path safely" in item.message
        for item in violations
    )


def test_check_files_reports_excessively_nested_json_without_crashing(tmp_path):
    primary = tmp_path / "model.gltf"
    nesting = 10_000
    payload = (
        b'{"asset":{"version":"2.0"},"extras":' + b"[" * nesting + b"0" + b"]" * nesting + b"}"
    )
    primary.write_bytes(payload)
    primary_sha = hashlib.sha256(payload).hexdigest()
    document = make_valid()
    representation = document["models"][0]["representations"][0]
    representation.update(
        {
            "format": "gltf",
            "uri": str(primary),
            "sha256": primary_sha,
            "files": [{"uri": str(primary), "sha256": primary_sha, "bytes": len(payload)}],
        }
    )
    document["models"][0]["representations"] = [representation]

    violations = ledger.validate_ledger(document, check_files=True)

    assert any(
        item.code == "representation_file_closure_unverifiable"
        and "JSON document exceeds structural limits" in item.message
        for item in violations
    )


def test_check_files_rejects_gltf_buffer_with_invalid_byte_length(tmp_path):
    payload = b'{"asset":{"version":"2.0"},"buffers":[{"uri":"mesh.bin","byteLength":"four"}]}'
    document = _file_backed_document(
        tmp_path,
        "model.gltf",
        payload,
        dependencies={"mesh.bin": b"mesh"},
    )

    violations = ledger.validate_ledger(document, check_files=True)

    assert any(
        item.code == "representation_file_closure_unverifiable"
        and "glTF buffer.byteLength must be a positive integer" in item.message
        for item in violations
    )


def test_check_files_binds_external_gltf_buffer_byte_length(tmp_path):
    payload = b'{"asset":{"version":"2.0"},"buffers":[{"uri":"mesh.bin","byteLength":5}]}'
    document = _file_backed_document(
        tmp_path,
        "model.gltf",
        payload,
        dependencies={"mesh.bin": b"mesh"},
    )

    violations = ledger.validate_ledger(document, check_files=True)

    assert any(
        item.code == "representation_file_payload_size_mismatch"
        and "mesh.bin declares 5 bytes, file has 4" in item.message
        for item in violations
    )


def test_check_files_reports_dependency_size_race_without_crashing(tmp_path, monkeypatch):
    payload = b'{"asset":{"version":"2.0"},"buffers":[{"uri":"mesh.bin","byteLength":4}]}'
    document = _file_backed_document(
        tmp_path,
        "model.gltf",
        payload,
        dependencies={"mesh.bin": b"mesh"},
    )
    dependency = tmp_path / "mesh.bin"
    original = Path.stat
    followed_calls = 0

    def fail_closure_stat(path, *args, **kwargs):
        nonlocal followed_calls
        if path == dependency and kwargs.get("follow_symlinks", True):
            followed_calls += 1
            if followed_calls == 3:
                raise OSError("stat raced with replacement")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_closure_stat)

    violations = ledger.validate_ledger(document, check_files=True)

    assert any(
        item.code == "representation_file_closure_unverifiable"
        and "cannot inspect dependency payload size" in item.message
        for item in violations
    )


@pytest.mark.parametrize(
    ("primary_name", "payload", "message"),
    [
        ("scene.gltf", b"{", "JSON document is malformed"),
        (
            "scene.gltf",
            b'{"asset":{"version":"2.0"},"buffers":[],"buffers":[]}',
            "duplicate JSON key: buffers",
        ),
        ("scene.gltf", b'{"asset":{"version":"2.0"},"x":NaN}', "non-finite JSON"),
        ("scene.gltf", b"[]", "JSON root must be an object"),
        (
            "scene.gltf",
            b'{"asset":{"version":"2.0"},"buffers":{}}',
            "glTF buffers must be a list",
        ),
        (
            "scene.gltf",
            b'{"asset":{"version":"2.0"},"buffers":[7]}',
            "glTF buffers member must be an object",
        ),
        (
            "scene.gltf",
            b'{"asset":{"version":"2.0"},"buffers":[{"byteLength":4,"uri":7}]}',
            "glTF URI must be a non-empty string",
        ),
        (
            "scene.gltf",
            b'{"asset":{"version":"2.0"},"buffers":[{"byteLength":4}]}',
            "glTF buffer has no bound payload",
        ),
        (
            "scene.gltf",
            b'{"asset":{"version":"2.0"},"images":[{}]}',
            "glTF image has no URI or bufferView",
        ),
        ("scene.gltf", b"\xff", "not UTF-8 text"),
        (
            "model.urdf",
            b"<!DOCTYPE robot [<!ENTITY x 'boom'>]><robot/>",
            "DTD/entity declarations are unsupported",
        ),
        ("model.urdf", b"<robot>", "XML is malformed"),
        (
            "model.urdf",
            b"<robot><mesh/></robot>",
            "URDF mesh must declare a non-empty filename",
        ),
        (
            "model.urdf",
            b"<robot><mesh filename='  '/></robot>",
            "URDF mesh must declare a non-empty filename",
        ),
        ("model.obj", b'mtllib "unterminated\n', "OBJ contains invalid quoting"),
        ("model.obj", b"mtllib\n", "OBJ mtllib command has no path"),
        ("material.mtl", b'map_Kd "unterminated\n', "MTL contains invalid quoting"),
        ("material.mtl", b"map_Kd\n", "MTL texture command has no path"),
        ("scene.usd", b"PXR-USDC\x00", "USD is not auditable USDA text"),
        ("scene.usdc", b"PXR-USDC\x00", "binary USDC dependency closure"),
        ("scene.opaque", b"opaque", "unsupported dependency semantics"),
    ],
)
def test_check_files_rejects_malformed_dependency_documents(
    tmp_path, primary_name, payload, message
):
    document = _file_backed_document(tmp_path, primary_name, payload)

    violations = ledger.validate_ledger(document, check_files=True)

    assert any(
        item.code == "representation_file_closure_unverifiable" and message in item.message
        for item in violations
    )


@pytest.mark.parametrize("primary_name", ["scene.gltf", "scene.glb"])
def test_check_files_rejects_oversized_dependency_documents(tmp_path, primary_name):
    payload = b"x" * (16 * 1024 * 1024 + 1)
    document = _file_backed_document(tmp_path, primary_name, payload)

    violations = ledger.validate_ledger(document, check_files=True)

    assert any(
        item.code == "representation_file_closure_unverifiable"
        and "dependency-bearing document exceeds 16777216 bytes" in item.message
        for item in violations
    )


def test_check_files_rejects_recursive_usda_dependency_cycle(tmp_path):
    document = _file_backed_document(
        tmp_path,
        "root.usda",
        b'#usda 1.0\ndef Xform "Root" (references = @child.usda@</Child>) {}\n',
        dependencies={
            "child.usda": (b'#usda 1.0\ndef Xform "Child" (references = @root.usda@</Root>) {}\n')
        },
    )

    violations = ledger.validate_ledger(document, check_files=True)

    assert any(
        item.code == "representation_file_dependency_cycle"
        and "root.usda -> child.usda -> root.usda" in item.message
        for item in violations
    )


@pytest.mark.parametrize(
    ("primary_name", "primary_payload", "dependencies"),
    [
        (
            "model.urdf",
            (
                b"<robot><link name='base'><visual><geometry>"
                b"<mesh filename='model.obj'/></geometry>"
                b"<material><texture filename='albedo.png'/></material>"
                b"</visual></link></robot>"
            ),
            {
                "model.obj": b"mtllib material.mtl\nv 0 0 0\n",
                "material.mtl": b"newmtl body\nmap_Kd -s 1 1 1 albedo.png\n",
                "albedo.png": b"png",
            },
        ),
        (
            "scene.gltf",
            (
                b'{"asset":{"version":"2.0"},'
                b'"buffers":[{"uri":"mesh.bin","byteLength":4}],'
                b'"images":[{"uri":"DATA:image/png;base64,cG5n"},'
                b'{"bufferView":0},{"uri":"albedo.png"}]}'
            ),
            {"mesh.bin": b"mesh", "albedo.png": b"png"},
        ),
        (
            "scene.dae",
            (
                b"<COLLADA><library_images><image><init_from>albedo.png</init_from>"
                b"</image></library_images><instance_node url='child.dae#Child'/></COLLADA>"
            ),
            {
                "child.dae": b"<COLLADA><node id='Child'/></COLLADA>",
                "albedo.png": b"png",
            },
        ),
        (
            "scene.usda",
            b'#usda 1.0\ndef Xform "Root" (references = @child.usda@</Child>) {}\n',
            {
                "child.usda": (
                    b'#usda 1.0\ndef Xform "Child" { asset inputs:file = @albedo.png@ }\n'
                ),
                "albedo.png": b"png",
            },
        ),
    ],
)
def test_check_files_accepts_complete_recursive_descriptor_closure(
    tmp_path, primary_name, primary_payload, dependencies
):
    document = _file_backed_document(
        tmp_path,
        primary_name,
        primary_payload,
        dependencies=dependencies,
    )

    assert ledger.validate_ledger(document, check_files=True) == []


def test_ledger_path(tmp_path):
    library = tmp_path / "library"
    assert ledger.ledger_path(library, "315_shears") == library / "315_shears/ledger.json"


def test_new_model_entry_and_upsert():
    m = ledger.new_model_entry(
        model=0,
        representations=make_model()["representations"],
        mesh_bbox_m=[0.078, 0.051, 0.053],
        mesh_up_axis="Y",
        origin_convention="bottom-center",
        size_resolution=make_model()["physical"]["size_resolution"],
        conventions=make_model()["physical"]["conventions"],
        source=make_model()["source"],
        verification=make_model()["verification"],
    )
    led = ledger.upsert_model(
        None,
        asset="315_shears",
        category="shears",
        kind="rigid",
        profile="sapien_only",
        identity=IDENTITY,
        aliases=["shears"],
        colors=[],
        materials=[],
        tags=["rigid", "external"],
        model_entry=m,
    )
    assert led["asset_id"] == "external_315_shears"  # 缺省前缀规则，可传 asset_id_prefix 覆盖
    assert ledger.validate_ledger(led, check_files=False) == []
    m1 = dict(m, model_id=1)
    led2 = ledger.upsert_model(
        led,
        asset="315_shears",
        category="shears",
        kind="rigid",
        profile="sapien_only",
        identity=IDENTITY,
        aliases=["shears"],
        colors=[],
        materials=[],
        tags=["rigid", "external"],
        model_entry=m1,
    )
    assert [x["model_id"] for x in led2["models"]] == [0, 1]

    # I-3: re-upserting an EXISTING model_id must replace that entry wholesale
    # in place, not append a duplicate.
    m0_updated = dict(
        m,
        physical=dict(
            m["physical"],
            size_resolution=dict(m["physical"]["size_resolution"], scale=2.0),
        ),
    )
    led3 = ledger.upsert_model(
        led2,
        asset="315_shears",
        category="shears",
        kind="rigid",
        profile="sapien_only",
        identity=IDENTITY,
        aliases=["shears"],
        colors=[],
        materials=[],
        tags=["rigid", "external"],
        model_entry=m0_updated,
    )
    assert len(led3["models"]) == 2  # still 2 -- replaced, not appended
    assert (
        led3["models"][0]["physical"]["size_resolution"]["scale"] == 2.0
    )  # content actually replaced
    assert "scale_applied" not in led3["models"][0]["physical"]  # v2: gone for good

    with pytest.raises(ValueError):  # 资产级漂移写时即抓
        ledger.upsert_model(
            led2,
            asset="315_shears",
            category="shears",
            kind="rigid",
            profile="sapien_only",
            identity=IDENTITY,
            aliases=["tin"],
            colors=[],
            materials=[],
            tags=["rigid", "external"],
            model_entry=dict(m, model_id=2),
        )

    identity_drift = json.loads(json.dumps(led2))
    identity_drift["external_ids"]["env_gen"] = "wrong_asset"
    with pytest.raises(ValueError, match="external_ids.env_gen"):
        ledger.upsert_model(
            identity_drift,
            asset="315_shears",
            category="shears",
            kind="rigid",
            profile="sapien_only",
            identity=IDENTITY,
            aliases=["shears"],
            colors=[],
            materials=[],
            tags=[],
            model_entry=dict(m, model_id=2),
        )


def test_upsert_model_discards_legacy_fields_and_detects_asset_drift():
    # v2 history: the drift rule once guarded semantic_name; v3 deletes the
    # field outright (79/79 ledgers had it == category, zero readers), so the
    # test now asserts the DISCARD plus the surviving asset_id cross-check.
    m = ledger.new_model_entry(
        model=0,
        representations=make_model()["representations"],
        mesh_bbox_m=[0.078, 0.051, 0.053],
        mesh_up_axis="Y",
        origin_convention="bottom-center",
        size_resolution=make_model()["physical"]["size_resolution"],
        conventions=make_model()["physical"]["conventions"],
        source=make_model()["source"],
        verification=make_model()["verification"],
    )
    led = ledger.upsert_model(
        None,
        asset="315_shears",
        category="shears",
        kind="rigid",
        profile="sapien_only",
        identity=IDENTITY,
        aliases=["shears"],
        colors=[],
        materials=[],
        tags=["rigid", "external"],
        model_entry=m,
    )
    # v3: semantic_name/tags are accepted and DISCARDED -- no drift to
    # detect, and the built ledger must not carry either field.
    led2 = ledger.upsert_model(
        led,
        asset="315_shears",
        category="shears",
        kind="rigid",
        profile="sapien_only",
        identity=IDENTITY,
        aliases=["shears"],
        colors=[],
        materials=[],
        tags=["rigid", "external"],
        semantic_name="not_shears",
        model_entry=dict(m, model_id=1),
    )
    assert "semantic_name" not in led2 and "tags" not in led2
    with pytest.raises(ValueError):  # wrong asset name, caught via asset_id suffix
        ledger.upsert_model(
            led,
            asset="999_wrong",
            category="shears",
            kind="rigid",
            profile="sapien_only",
            identity=IDENTITY,
            aliases=["shears"],
            colors=[],
            materials=[],
            tags=["rigid", "external"],
            model_entry=dict(m, model_id=1),
        )


def test_append_and_latest(tmp_path):
    p = tmp_path / "ledger.json"
    led = make_valid()
    dig = ledger.reps_digest(led["models"][0], "sapien")
    led["models"][0]["verification"][0]["verified_digest"] = dig
    p.write_text(json.dumps(led))
    fail = {
        "backend": "sapien",
        "check": "settle",
        "verdict": "fail",
        "run_id": "r2",
        "timestamp": "2026-08-08T12:00:00",
        "verified_digest": dig,
        "report_path": "r2.json",
    }
    out = ledger.append_verification(p, 0, fail)
    assert len(out["models"][0]["verification"]) == 2  # append-only
    assert oct(p.stat().st_mode)[-3:] == "644"  # M-1: not mkstemp's default 0600
    latest = ledger.latest_verification(out["models"][0], "sapien", "settle")
    assert latest["verdict"] == "fail"  # 新 fail 压过旧 pass —— 禁 any(pass)
    assert (
        ledger.append_verification(p, 0, fail)["models"][0]["verification"]
        == out["models"][0]["verification"]
    )  # 同 (backend,check,run_id,digest) 去重

    # I-2: appended LAST but timestamped EARLIER than the current latest --
    # must not become "latest". Pins timestamp-max semantics against a
    # candidates[-1]-style (list-order) regression. Digest still matches, so
    # this isn't the staleness path.
    early_pass = dict(fail, run_id="r1_early", timestamp="2026-08-08T08:00:00", verdict="pass")
    out_early = ledger.append_verification(p, 0, early_pass)
    assert len(out_early["models"][0]["verification"]) == 3
    latest_after_early = ledger.latest_verification(out_early["models"][0], "sapien", "settle")
    assert latest_after_early["run_id"] == "r2"  # still the 12:00 fail

    stale = dict(
        fail,
        run_id="r3",
        timestamp="2026-08-08T13:00:00",
        verified_digest="e" * 64,
        verdict="pass",
    )
    with pytest.raises(ledger.VerificationDigestError):
        ledger.append_verification(p, 0, stale)
    assert ledger.latest_verification(out_early["models"][0], "sapien", "settle") is not None


def test_append_verification_rejects_same_identity_with_different_fact(tmp_path):
    path = tmp_path / "ledger.json"
    document = make_valid()
    digest = ledger.reps_digest(document["models"][0], "sapien")
    original = {
        "backend": "sapien",
        "check": "settle",
        "verdict": "pass",
        "run_id": "immutable-run",
        "timestamp": "2026-08-31T12:00:00",
        "verified_digest": digest,
    }
    document["models"][0]["verification"] = [original]
    path.write_text(json.dumps(document))
    before = path.read_bytes()

    # Mapping order is not fact identity: the complete canonical entry is an
    # idempotent retry, while changing even verdict under the same producer
    # identity is contradictory evidence and must never be swallowed.
    reordered = {key: original[key] for key in reversed(original)}
    assert ledger.append_verification(path, 0, reordered) == document
    with pytest.raises(ledger.VerificationConflictError):
        ledger.append_verification(path, 0, {**original, "verdict": "fail"})

    assert path.read_bytes() == before


def test_atomic_write_json_fsyncs(tmp_path, monkeypatch):
    # H1 顺手: crash-durability for the contract store -- flush() + fsync()
    # before the atomic os.replace() so a completed write is actually on
    # disk, not sitting in an OS buffer that a crash right after could still
    # lose. Spies on ledger.os.fsync (the same os module _atomic_write_json
    # uses) rather than importing os separately here.
    calls = []
    orig_fsync = ledger.os.fsync

    def spy_fsync(fd):
        calls.append(fd)
        return orig_fsync(fd)

    monkeypatch.setattr(ledger.os, "fsync", spy_fsync)
    p = tmp_path / "ledger.json"
    ledger._atomic_write_json(p, {"a": 1})
    assert calls, "os.fsync was not called during atomic write"
    assert json.loads(p.read_text()) == {"a": 1}


def test_write_ledger_atomic(tmp_path):
    # T5 fix I-1: whole-ledger writer (import_materialize's upsert path)
    # must go through the same lock+atomic-replace machinery as
    # append_verification, not a bare path.write_text().
    p = tmp_path / "ledger.json"
    led = make_valid()
    ledger.write_ledger(p, led)
    assert json.loads(p.read_text()) == led
    assert oct(p.stat().st_mode)[-3:] == "644"  # not mkstemp's default 0600
    led2 = dict(led, category="changed")
    ledger.write_ledger(p, led2)  # overwrite in place (read-upsert-write pattern)
    assert json.loads(p.read_text())["category"] == "changed"


def test_write_ledger_at_uses_pinned_directory_without_consuming_caller_fd(tmp_path):
    directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        document = make_valid()
        ledger.write_ledger_at(directory_fd, document)

        assert json.loads((tmp_path / "ledger.json").read_text()) == document
        assert oct((tmp_path / "ledger.json").stat().st_mode)[-3:] == "644"
        os.fstat(directory_fd)
    finally:
        os.close(directory_fd)


def test_write_ledger_at_rejects_a_non_directory_fd(tmp_path):
    ordinary = tmp_path / "ordinary.txt"
    ordinary.write_text("not a directory")
    descriptor = os.open(ordinary, os.O_RDONLY)
    try:
        with pytest.raises(NotADirectoryError, match="not a directory"):
            ledger.write_ledger_at(descriptor, make_valid())
    finally:
        os.close(descriptor)


def test_write_ledger_at_closes_lock_fd_when_stream_wrapping_fails(tmp_path, monkeypatch):
    directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    opened = []
    real_open = os.open

    def recording_open(*args, **kwargs):
        descriptor = real_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def fail_fdopen(*_args, **_kwargs):
        raise OSError("wrap failed")

    monkeypatch.setattr(os, "open", recording_open)
    monkeypatch.setattr(os, "fdopen", fail_fdopen)
    try:
        with pytest.raises(OSError, match="wrap failed"):
            ledger.write_ledger_at(directory_fd, make_valid())

        assert len(opened) == 1
        with pytest.raises(OSError):
            os.fstat(opened[0])
        os.fstat(directory_fd)
    finally:
        os.close(directory_fd)


def test_write_ledger_at_rejects_lock_symlink_without_touching_target(tmp_path):
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("do-not-truncate")
    (tmp_path / "ledger.lock").symlink_to(sentinel)
    directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with pytest.raises(OSError):
            ledger.write_ledger_at(directory_fd, make_valid())
    finally:
        os.close(directory_fd)

    assert sentinel.read_text() == "do-not-truncate"
    assert not (tmp_path / "ledger.json").exists()


@pytest.mark.parametrize("operation", ["write", "append"])
def test_ledger_lock_symlink_fails_closed_without_truncating_target(tmp_path, operation):
    path = tmp_path / "ledger.json"
    document = make_valid()
    digest = ledger.reps_digest(document["models"][0], "sapien")
    path.write_text(json.dumps(document))
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("do-not-truncate")
    path.with_suffix(".lock").symlink_to(sentinel)

    with pytest.raises(OSError):
        if operation == "write":
            ledger.write_ledger(path, document)
        else:
            ledger.append_verification(
                path,
                0,
                {
                    "backend": "sapien",
                    "check": "runtime_load",
                    "verdict": "pass",
                    "run_id": "lock-attack",
                    "timestamp": "2026-08-31T12:00:00",
                    "verified_digest": digest,
                },
            )

    assert sentinel.read_text() == "do-not-truncate"


def test_ledger_lock_fails_closed_without_o_nofollow(tmp_path, monkeypatch):
    path = tmp_path / "ledger.json"
    path.write_text("{}")
    monkeypatch.delattr(os, "O_NOFOLLOW")

    with pytest.raises(OSError, match="requires O_NOFOLLOW"):
        ledger.write_ledger(path, {})


def test_ledger_lock_closes_fd_when_stream_wrapping_fails(tmp_path, monkeypatch):
    path = tmp_path / "ledger.json"
    path.write_text("{}")
    opened_fd = None
    real_open = os.open

    def recording_open(*args, **kwargs):
        nonlocal opened_fd
        opened_fd = real_open(*args, **kwargs)
        return opened_fd

    def fail_fdopen(*_args, **_kwargs):
        raise OSError("wrap failed")

    monkeypatch.setattr(os, "open", recording_open)
    monkeypatch.setattr(os, "fdopen", fail_fdopen)

    with pytest.raises(OSError, match="wrap failed"):
        ledger.write_ledger(path, {})

    assert opened_fd is not None
    with pytest.raises(OSError):
        os.fstat(opened_fd)


def test_append_verification_runtime_load_backfill(tmp_path):
    # T7: s11 backfills a runtime_load entry the same way after its sweep --
    # pin the round trip for this check specifically (the other tests here
    # only exercise "settle"), and confirm it coexists with the model's
    # existing settle entry rather than clobbering it.
    p = tmp_path / "ledger.json"
    led = make_valid()
    dig = ledger.reps_digest(led["models"][0], "sapien")
    led["models"][0]["verification"][0]["verified_digest"] = dig
    p.write_text(json.dumps(led))

    entry = {
        "backend": "sapien",
        "check": "runtime_load",
        "verdict": "pass",
        "run_id": "sweep_20260808",
        "timestamp": "2026-08-08T14:00:00",
        "verified_digest": dig,
        "report_path": "/tmp/sweep_20260808.json",
    }
    out = ledger.append_verification(p, 0, entry)
    model = out["models"][0]
    assert len(model["verification"]) == 2  # fixture's settle + new runtime_load

    latest = ledger.latest_verification(model, "sapien", "runtime_load")
    assert latest is not None
    assert latest["verdict"] == "pass"
    assert latest["run_id"] == "sweep_20260808"

    settle = ledger.latest_verification(model, "sapien", "settle")
    assert settle is not None and settle["run_id"] == "20260808_import"  # untouched

    assert ledger.validate_ledger(out, check_files=False) == []


def test_to_ir_bundles_roundtrip():
    sys.path.insert(
        0,
        str(Path(__file__).resolve().parents[2] / "shared/openxsim/source/agenticsim"),
    )
    from agenticsim.openxsim.ir import AssetBundle

    flat = ledger.to_ir_bundles(make_valid())
    assert len(flat) == 1 and flat[0]["asset_id"] == "external_315_shears_m0"
    ab = AssetBundle.from_dict(flat[0])  # 旧读者形状兼容
    ab.validate()
    assert ab.representation_for("sapien") is not None
    assert all(r["role"] != "snapshot" for r in flat[0]["representations"])


def test_to_ir_bundles_derives_legacy_kind_and_source_tags_from_v3_fields():
    sys.path.insert(
        0,
        str(Path(__file__).resolve().parents[2] / "shared/openxsim/source/agenticsim"),
    )
    from agenticsim.openxsim.ir import AssetBundle

    articulated = make_valid(kind="articulated")
    articulated["models"][0]["articulation"] = {"joint_names": ["door_hinge"]}
    articulated_bundle = ledger.to_ir_bundles(articulated)[0]
    assert articulated_bundle["tags"] == ["articulated", "external"]
    assert AssetBundle.from_dict(articulated_bundle).tags == ("articulated", "external")

    generated_bundle = ledger.to_ir_bundles(make_generated_valid())[0]
    assert generated_bundle["tags"] == ["rigid", "generated"]
    assert AssetBundle.from_dict(generated_bundle).tags == ("rigid", "generated")

    unknown = make_valid(kind="opaque")
    unknown["models"][0]["source"]["kind"] = "opaque"
    assert ledger.to_ir_bundles(unknown)[0]["tags"] == []


def test_derive_usable():
    # I-7: positive case -- a fully valid ledger's only model is usable with
    # no missing paths.
    b = make_valid()
    ok, missing = ledger.derive_usable(b, 0)
    assert ok is True
    assert missing == []

    # negative case -- a required field absent on the model shows up in
    # missing_paths with the model[model_id=N]-prefixed dotted-path format.
    b2 = make_valid()
    del b2["models"][0]["physical"]["mesh_bbox_m"]
    ok2, missing2 = ledger.derive_usable(b2, 0)
    assert ok2 is False
    assert "models[model_id=0].physical.mesh_bbox_m" in missing2

    # negative case -- unknown model_id.
    ok3, missing3 = ledger.derive_usable(b, 99)
    assert ok3 is False
    assert missing3 == ["models[model_id=99]"]


def test_reps_digest_literal():
    # Pin the versioned canonical representation-set contract against a literal
    # rather than re-deriving the same json/hash implementation in the test.
    model = {
        "representations": [
            {"backend": "sapien", "role": "visual", "sha256": "2" * 64},
            {"backend": "sapien", "role": "collision", "sha256": "1" * 64},
            {"backend": "sapien", "role": "snapshot", "sha256": "9" * 64},  # excluded
            {"backend": "isaacsim", "role": "visual", "sha256": "3" * 64},  # excluded
        ]
    }
    assert (
        ledger.reps_digest(model, "sapien")
        == "12ef410127bb07ba2c1968b3aa4ea549b57281b3e71098a8973e46d9c2f2c59f"
    )
    reordered = {"representations": list(reversed(model["representations"]))}
    assert ledger.reps_digest(reordered, "sapien") == ledger.reps_digest(model, "sapien")


@pytest.mark.parametrize(
    "model",
    [
        {"representations": []},
        {"representations": [{"backend": "sapien", "role": "snapshot", "sha256": "1" * 64}]},
        {"representations": [{"backend": "isaacsim", "role": "visual", "sha256": "2" * 64}]},
    ],
)
def test_reps_digest_rejects_backend_without_loader_representation(model):
    with pytest.raises(ledger.RepresentationDigestError, match="no loader-visible"):
        ledger.reps_digest(model, "sapien")


def test_reps_digest_invalidates_receipt_when_closure_or_loader_metadata_changes():
    model = make_model()
    digest = ledger.reps_digest(model, "sapien")
    assert ledger.REPS_DIGEST_VERSION == "asset-representation-set.v2"
    assert digest != "60e05bd1b195af2f94112fa7197a5c88289058840ce7c6df9693756bc6250f55"
    model["verification"][0]["verified_digest"] = digest
    assert ledger.latest_verification(model, "sapien", "settle") is not None

    legacy_receipt = json.loads(json.dumps(model))
    legacy_receipt["verification"][0]["verified_digest"] = (
        "60e05bd1b195af2f94112fa7197a5c88289058840ce7c6df9693756bc6250f55"
    )
    assert ledger.latest_verification(legacy_receipt, "sapien", "settle") is None

    closure_changed = json.loads(json.dumps(model))
    closure_changed["representations"][0]["files"][0]["sha256"] = "e" * 64
    assert ledger.reps_digest(closure_changed, "sapien") != digest
    assert ledger.latest_verification(closure_changed, "sapien", "settle") is None

    metadata_changed = json.loads(json.dumps(model))
    metadata_changed["representations"][0]["metadata"]["conversion_params"]["rotated_z2y"] = False
    assert ledger.reps_digest(metadata_changed, "sapien") != digest
    assert ledger.latest_verification(metadata_changed, "sapien", "settle") is None

    locator_changed = json.loads(json.dumps(model))
    locator_changed["representations"][0]["uri"] = "/mounted-elsewhere/visual.glb"
    assert ledger.reps_digest(locator_changed, "sapien") != digest
    assert ledger.latest_verification(locator_changed, "sapien", "settle") is None

    snapshot_changed = json.loads(json.dumps(model))
    snapshot_changed["representations"][1]["files"][0]["sha256"] = "f" * 64
    assert ledger.reps_digest(snapshot_changed, "sapien") == digest


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("backend", "bullet", "bad_enum"),
        ("run_id", "", "bad_type"),
        ("run_id", 7, "bad_type"),
        ("verified_digest", "signed-by-claim", "bad_sha256"),
        ("report_path", "", "bad_type"),
        ("report_path", 7, "bad_type"),
    ],
)
def test_validate_verification_rejects_untrusted_core_shapes(field, value, code):
    document = make_valid()
    document["models"][0]["verification"][0][field] = value

    violations = ledger.validate_ledger(document, check_files=False)

    assert any(
        item.path == f"models.0.verification.0.{field}" and item.code == code for item in violations
    )


@pytest.mark.parametrize("verification", [{}, ["opaque"]])
def test_validate_verification_rejects_malformed_collection_or_member(verification):
    document = make_valid()
    document["models"][0]["verification"] = verification

    violations = ledger.validate_ledger(document, check_files=False)

    assert any(
        item.path.startswith("models.0.verification") and item.code == "bad_type"
        for item in violations
    )


def test_latest_verification_rejects_structurally_untrusted_receipt():
    model = make_model()
    model["verification"][0]["verified_digest"] = ledger.reps_digest(model, "sapien")
    model["verification"][0]["run_id"] = ""

    assert ledger.latest_verification(model, "sapien", "settle") is None

    model["verification"] = ["opaque"]
    assert ledger.latest_verification(model, "sapien", "settle") is None


def test_latest_verification_rejects_ambiguous_latest_timestamp():
    model = make_model()
    digest = ledger.reps_digest(model, "sapien")
    first = dict(model["verification"][0], verified_digest=digest, verdict="pass")
    conflicting = dict(first, run_id="same-second-failure", verdict="fail")
    model["verification"] = [first, conflicting]

    assert ledger.latest_verification(model, "sapien", "settle") is None


def test_new_model_entry_articulated_full():
    # T6: articulated builder happy path -- joint_names/types/limits/qpos +
    # balance_gate in articulation, mass basis=urdf_inertial override,
    # URDF Z-up stable pose -- exercised end to end through upsert_model +
    # validate_ledger (0 violations), matching what s13b now assembles.
    art = {
        "joint_names": ["drawer_0"],
        "joint_types": ["prismatic"],
        "limits": [[0.0, 0.3]],
        "closed_qpos": [0.0],
        "open_qpos": [0.3],
        "balance_gate": {"free_joints_allowed": False, "measured_equilibrium": None},
    }
    conv = dict(make_model()["physical"]["conventions"])
    conv["stable_poses"] = [
        {
            "pose_id": "upright",
            "orientation_wxyz": ledger.IDENTITY_WXYZ,  # URDF Z-up -> identity
            "is_default": True,
            "measured_against": {
                "backend": "sapien",
                "run_id": "fixture-articulated-settle-1",
            },
        }
    ]
    m = ledger.new_model_entry(
        model=0,
        representations=make_model()["representations"],
        mesh_bbox_m=[0.6, 0.4, 0.8],
        mesh_up_axis="Z",
        origin_convention="base-at-floor",
        # actual_max_dim_m is the PRE-scale reading, so it has to agree with
        # this model's own bbox (0.8 at scale 1.0) -- reusing the rigid
        # fixture's 0.078 would trip size_invariant_mismatch, which is the
        # check working, not a fixture inconvenience.
        size_resolution=dict(make_model()["physical"]["size_resolution"], actual_max_dim_m=0.8),
        conventions=conv,
        source=make_model()["source"],
        verification=[],
        articulation=art,
        mass_override={
            "value": None,
            "status": "unknown",
            "runtime_default_kg": 10.0,
            "runtime_default_basis": "urdf_inertial",
        },
    )
    led = ledger.upsert_model(
        None,
        asset="314_cabinet",
        category="cabinet",
        kind="articulated",
        profile="sapien_only",
        identity=IDENTITY,
        aliases=["cabinet"],
        colors=[],
        materials=[],
        tags=["articulated", "external"],
        model_entry=m,
    )
    assert ledger.validate_ledger(led, check_files=False) == []
    assert led["kind"] == "articulated"
    assert led["models"][0]["articulation"]["balance_gate"]["free_joints_allowed"] is False
    assert "runtime_default_basis" not in led["models"][0]["physical"]["mass_kg"]


def test_upsert_model_attribute_basis_optional_and_preserved():
    m = ledger.new_model_entry(
        model=0,
        representations=make_model()["representations"],
        mesh_bbox_m=[0.078, 0.051, 0.053],
        mesh_up_axis="Y",
        origin_convention="bottom-center",
        size_resolution=make_model()["physical"]["size_resolution"],
        conventions=make_model()["physical"]["conventions"],
        source=make_model()["source"],
        verification=make_model()["verification"],
    )
    led = ledger.upsert_model(
        None,
        asset="320_ball",
        category="ball",
        kind="rigid",
        profile="sapien_only",
        identity=IDENTITY,
        aliases=["ball"],
        colors=["yellow"],
        materials=["plastic"],
        tags=[],
        model_entry=m,
        attribute_basis={"colors": "vlm", "materials": "vlm"},
    )
    assert led["semantics"]["attribute_basis"] == {"colors": "vlm", "materials": "vlm"}
    assert ledger.validate_ledger(led, check_files=False) == []
    # 已有账本再 upsert：basis 保留、不参与漂移检查
    led2 = ledger.upsert_model(
        led,
        asset="320_ball",
        category="ball",
        kind="rigid",
        profile="sapien_only",
        identity=IDENTITY,
        aliases=["ball"],
        colors=["yellow"],
        materials=["plastic"],
        tags=[],
        model_entry=dict(m, model_id=1),
        attribute_basis=None,
    )
    assert led2["semantics"]["attribute_basis"] == {"colors": "vlm", "materials": "vlm"}


def test_direct_module_entry_uses_absolute_import_fallback():
    namespace = runpy.run_path(ledger.__file__)

    assert namespace["SCHEMA_VERSION"] == ledger.SCHEMA_VERSION


def test_strict_dates_and_numeric_dotted_paths_cover_fail_closed_edges():
    assert ledger._is_iso_date("2026-02-30") is False
    assert ledger._is_iso_datetime("2026-02-30T10:00:00") is False
    node = {"models": [{"physical": {"value": 1}}]}
    assert ledger._get(node, "models.0.physical.value") == 1
    assert ledger._get(node, "models.4") is ledger._MISSING
    assert ledger._get(node, "models.0.1") is ledger._MISSING


def test_asset_layout_rejects_duplicates_and_ignores_internal_entries(tmp_path):
    duplicate_root = tmp_path / "duplicates"
    (duplicate_root / "provider-a" / "901_widget").mkdir(parents=True)
    (duplicate_root / "provider-b" / "901_widget").mkdir(parents=True)
    with pytest.raises(ValueError, match="resolves to 2 directories"):
        ledger.asset_dir(duplicate_root, "901_widget")
    with pytest.raises(ValueError, match="exists twice"):
        list(ledger.iter_assets(duplicate_root))

    library = tmp_path / "library"
    (library / "001_flat").mkdir(parents=True)
    (library / "provider" / "cube").mkdir(parents=True)
    (library / "provider" / "_internal").mkdir()
    (library / "empty-provider").mkdir()
    (library / "_source").mkdir()
    (library / "README.txt").write_text("not an asset")
    (library / "provider" / "note.txt").write_text("not an asset")

    assert [path.name for path in ledger.iter_assets(library)] == ["001_flat", "cube"]


def test_public_path_helpers_cover_flat_missing_and_portable_layouts(tmp_path, monkeypatch):
    active_root = tmp_path / "active"
    flat_asset = active_root / "library" / "001_flat"
    flat_asset.mkdir(parents=True)
    inside = flat_asset / "model.stl"
    inside.write_bytes(b"mesh")
    outside = tmp_path / "outside.stl"
    outside.write_bytes(b"mesh")
    monkeypatch.setattr(ledger, "ACTIVE_ROOT", active_root)

    assert ledger.to_portable_uri(inside) == "library/001_flat/model.stl"
    assert ledger.to_portable_uri(outside) == str(outside)
    assert ledger.resolve_uri("library/001_flat/model.stl") == inside
    assert ledger.resolve_uri(outside) == outside
    assert ledger.asset_dir(active_root / "library", "001_flat") == flat_asset
    assert list(ledger.iter_assets(tmp_path / "missing-library")) == []


def test_latest_verification_returns_none_when_no_check_matches():
    assert ledger.latest_verification(make_model(), "mujoco", "e2e") is None


def test_latest_verification_rejects_receipt_for_backend_without_representation():
    model = make_model()
    model["verification"] = [
        {
            "backend": "isaacsim",
            "check": "settle",
            "verdict": "pass",
            "run_id": "unbound-empty-backend",
            "timestamp": "2026-08-31T12:00:00",
            "verified_digest": "a" * 64,
        }
    ]

    assert ledger.latest_verification(model, "isaacsim", "settle") is None


def test_measured_blocks_cover_shape_provenance_and_required_values():
    violations = []
    ledger._validate_measured_block("opaque", "appearance", ("colors_measured",), violations)
    ledger._validate_measured_block({}, "appearance", ("colors_measured",), violations)
    ledger._validate_measured_block(
        {"measured_against": {"backend": "bullet"}},
        "appearance",
        ("colors_measured",),
        violations,
    )
    ledger._validate_measured_block(
        {
            "colors_measured": ["red"],
            "measured_against": {"backend": "sapien"},
        },
        "appearance",
        ("colors_measured",),
        violations,
    )
    ledger._validate_measured_block(
        {"measured_against": {"backend": "sapien"}},
        "placement",
        (),
        violations,
    )

    assert {violation.code for violation in violations} == {
        "unknown_shape",
        "measured_against_required",
        "bad_enum",
        "missing",
    }


def test_inertial_validator_covers_every_structural_branch():
    violations = []
    ledger._validate_inertial("opaque", "inertial.0", violations)
    ledger._validate_inertial(
        {
            "status": "opaque",
            "basis": "opaque",
            "com_m": [0, 0, 0],
            "inertia_diagonal_kgm2": [1, 1, 1],
        },
        "inertial.1",
        violations,
    )
    ledger._validate_inertial(
        {
            "status": "known",
            "basis": "measured",
            "com_m": None,
            "inertia_diagonal_kgm2": None,
        },
        "inertial.2",
        violations,
    )
    ledger._validate_inertial(
        {
            "status": "known",
            "basis": "measured",
            "com_m": [0, 0],
            "inertia_diagonal_kgm2": [1, 1, 1],
        },
        "inertial.3",
        violations,
    )

    assert {violation.code for violation in violations} == {
        "unknown_shape",
        "bad_enum",
        "bad_type",
    }


def test_model_optional_and_early_return_branches_are_fail_closed():
    documents = []

    no_conventions = make_valid()
    no_conventions["models"][0]["physical"]["conventions"] = "opaque"
    documents.append(no_conventions)

    null_poses = make_valid()
    null_poses["models"][0]["physical"]["conventions"]["stable_poses"] = None
    documents.append(null_poses)

    no_mass = make_valid()
    del no_mass["models"][0]["physical"]["mass_kg"]
    documents.append(no_mass)

    no_license = make_valid()
    del no_license["models"][0]["source"]["license"]
    documents.append(no_license)

    no_source = make_valid()
    no_source["models"][0]["source"] = None
    documents.append(no_source)

    no_verification = make_valid()
    no_verification["models"][0]["verification"] = None
    documents.append(no_verification)

    for document in documents:
        assert ledger.validate_ledger(document, check_files=False)

    early_return_shapes = [
        {"physical": {"mesh_bbox_m": None, "size_resolution": {}}},
        {"physical": {"mesh_bbox_m": [1], "size_resolution": None}},
        {
            "physical": {
                "mesh_bbox_m": [1],
                "size_resolution": {"actual_max_dim_m": None, "scale": 1},
            }
        },
        {
            "physical": {
                "mesh_bbox_m": ["one"],
                "size_resolution": {"actual_max_dim_m": 1, "scale": 1},
            }
        },
    ]
    for model in early_return_shapes:
        violations = []
        ledger._check_size_invariant(model, "model", violations)
        assert violations == []


def test_check_files_skips_malformed_nodes_without_crashing(tmp_path):
    existing = tmp_path / "member.bin"
    existing.write_bytes(b"member")
    representation = {
        "format": "glb",
        "uri": str(existing),
        "backend": "sapien",
        "role": "visual",
        "sha256": "bad",
        "files": [
            "opaque-member",
            {"uri": "", "sha256": "0" * 64, "bytes": 0},
            {"uri": str(existing), "sha256": "bad", "bytes": 6},
        ],
        "metadata": {},
    }
    document = make_valid(
        models=[
            make_model(representations=["opaque-representation", representation]),
            "opaque-model",
        ]
    )

    assert ledger.validate_ledger(document, check_files=True)

    non_list_representations = make_valid()
    non_list_representations["models"][0]["representations"] = {}
    assert ledger.validate_ledger(non_list_representations, check_files=True)

    non_list_files = make_valid()
    non_list_files["models"][0]["representations"][0]["files"] = {}
    assert ledger.validate_ledger(non_list_files, check_files=True)


def test_derive_usable_is_total_over_malformed_and_articulated_documents():
    assert ledger.derive_usable(make_valid(models=None), 0) == (False, ["models"])

    missing_source = make_valid()
    del missing_source["models"][0]["source"]["library"]
    ok, missing = ledger.derive_usable(missing_source, 0)
    assert not ok and any(path.endswith("source.library") for path in missing)

    empty_poses = make_valid()
    empty_poses["models"][0]["physical"]["conventions"]["stable_poses"] = []
    assert not ledger.derive_usable(empty_poses, 0)[0]

    malformed_poses = make_valid()
    malformed_poses["models"][0]["physical"]["conventions"]["stable_poses"] = {"pose": 1}
    assert not ledger.derive_usable(malformed_poses, 0)[0]

    malformed_representations = make_valid()
    malformed_representations["models"][0]["representations"] = ["opaque"]
    ok, missing = ledger.derive_usable(malformed_representations, 0)
    assert not ok
    assert any(path.endswith("representations.0") for path in missing)
    assert any("backend=sapien" in path for path in missing)

    non_list_representations = make_valid()
    non_list_representations["models"][0]["representations"] = {}
    assert not ledger.derive_usable(non_list_representations, 0)[0]

    unknown_collision = make_valid()
    rep = unknown_collision["models"][0]["representations"][0]
    rep["role"] = "collision"
    rep["collision_meta"] = {
        "mode": "unknown",
        "unknown_reason": "loader authoring was not probed",
    }
    assert ledger.derive_usable(unknown_collision, 0) == (True, [])
    del rep["collision_meta"]["unknown_reason"]
    assert not ledger.derive_usable(unknown_collision, 0)[0]
    rep["collision_meta"] = {"mode": "made_up"}
    assert not ledger.derive_usable(unknown_collision, 0)[0]

    articulated = make_valid(kind="articulated")
    articulated["models"][0]["articulation"] = "opaque"
    assert not ledger.derive_usable(articulated, 0)[0]
    articulated["models"][0]["articulation"] = {"joint_names": []}
    assert ledger.derive_usable(articulated, 0) == (True, [])


def test_new_model_entry_preserves_every_v3_optional_block():
    fixture = make_model()
    entry = ledger.new_model_entry(
        model=0,
        representations=fixture["representations"],
        mesh_bbox_m=fixture["physical"]["mesh_bbox_m"],
        size_resolution=fixture["physical"]["size_resolution"],
        conventions=fixture["physical"]["conventions"],
        source=fixture["source"],
        verification=fixture["verification"],
        inertial=ledger.unknown_inertial(),
        placement={"measured_against": {"backend": "sapien"}},
        restitution={"value": 0.1, "status": "known"},
        appearance={
            "colors_measured": ["red"],
            "measured_against": {"backend": "sapien"},
        },
        extras={"affordance": "graspable"},
    )

    assert entry["physical"]["placement"]
    assert entry["physical"]["restitution"]
    assert entry["appearance"]
    assert entry["extras"]


@pytest.mark.parametrize("delete_before_raise", [False, True])
def test_atomic_write_removes_or_tolerates_missing_temp_on_failure(
    tmp_path, monkeypatch, delete_before_raise
):
    target = tmp_path / "ledger.json"

    def failing_replace(source, destination, *, src_dir_fd, dst_dir_fd):
        del destination, dst_dir_fd
        if delete_before_raise:
            os.unlink(source, dir_fd=src_dir_fd)
        raise RuntimeError("replace failed")

    monkeypatch.setattr(ledger.os, "replace", failing_replace)
    with pytest.raises(RuntimeError, match="replace failed"):
        ledger._atomic_write_json(target, {"schema_version": ledger.SCHEMA_VERSION})

    assert list(tmp_path.glob("ledger.json.*.tmp")) == []


def test_append_verification_searches_multiple_models_and_rejects_unknown_id(tmp_path):
    document = make_valid(models=[make_model(model_id=1), make_model(model_id=2)])
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(document))
    entry = {
        "backend": "sapien",
        "check": "runtime_load",
        "verdict": "pass",
        "run_id": "edge-run",
        "timestamp": "2026-08-31T10:00:00",
        "verified_digest": ledger.reps_digest(document["models"][1], "sapien"),
    }

    updated = ledger.append_verification(path, 2, entry)
    assert updated["models"][1]["verification"][-1] == entry

    with pytest.raises(ValueError, match="no model with model_id=99"):
        ledger.append_verification(path, 99, entry)
