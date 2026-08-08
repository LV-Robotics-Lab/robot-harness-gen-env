import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import ledger


def make_model(**over):
    m = {
        "model_id": 0,
        "physical": {
            "mesh_bbox_m": [0.078, 0.051, 0.053],
            "mesh_up_axis": "Y",
            "origin_convention": "bottom-center",
            "scale_applied": 1.0,
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
                    }
                ],
                "inherited_from": None,
            },
            "mass_kg": {
                "value": None,
                "status": "unknown",
                "runtime_default_kg": 0.1,
                "runtime_default_basis": "global_constant",
            },
            "friction": {
                "value": None,
                "status": "unknown",
                "runtime_default": None,
                "runtime_default_basis": "none",
            },
        },
        "representations": [
            {
                "format": "glb",
                "uri": "/tmp/x/visual.glb",
                "backend": "sapien",
                "role": "visual",
                "sha256": "0" * 64,
                "size_bytes": 10,
                "metadata": {
                    "derived_from": "src.usd",
                    "converter": "omni.kit.asset_converter@isaac-5.1",
                    "conversion_params": {"rotated_z2y": True},
                },
            },
        ],
        "articulation": {},
        "source": {
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


def make_valid(**over):
    b = {
        "schema_version": "asset_ledger.v1",
        "asset_id": "external_315_shears",
        "category": "shears",
        "semantic_name": "shears",
        "kind": "rigid",
        "tags": ["rigid", "external", "batch"],
        "semantics": {"aliases": ["shears", "scissors"], "colors": [], "materials": []},
        "models": [make_model()],
    }
    b.update(over)
    return b


def test_valid_ledger_no_violations():
    assert ledger.validate_ledger(make_valid(), check_files=False) == []


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
    (_del("semantic_name"), "missing"),
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
    (
        _set(
            "models.0.physical.mass_kg",
            {
                "value": None,
                "status": "unknown",
                "runtime_default_kg": 0.1,
                "runtime_default_basis": "vibes",
            },
        ),
        "bad_enum",
    ),
    (_del("models.0.physical.friction"), "missing"),
    (_set("models.0.representations", []), "no_sapien_representation"),
    (_set("models.0.source.license", "unknown"), "license_not_structured"),
    (_del("models.0.source.retrieved_at"), "missing"),
    (_set("models.0.verification.0.check", "fly"), "bad_enum"),
    (_del("models.0.verification.0.verified_digest"), "missing"),
    (_del("models.0.verification.0.run_id"), "missing"),
    (_set("usable", True), "derived_field_handwritten"),
]


@pytest.mark.parametrize("mutate,code", CASES)
def test_violations(mutate, code):
    b = make_valid()
    mutate(b)
    codes = [v.code for v in ledger.validate_ledger(b, check_files=False)]
    assert code in codes, f"expected {code}, got {codes}"


def test_duplicate_model_id():
    b = make_valid(models=[make_model(), make_model()])  # 两个 model_id=0
    codes = [v.code for v in ledger.validate_ledger(b, check_files=False)]
    assert "duplicate_model_id" in codes


def test_articulated_requires_articulation():
    b = make_valid(kind="articulated")
    codes = [v.code for v in ledger.validate_ledger(b, check_files=False)]
    assert "articulation_required" in codes


def test_check_files(tmp_path):
    f = tmp_path / "visual.glb"
    f.write_bytes(b"mesh")
    b = make_valid()
    b["models"][0]["representations"][0]["uri"] = str(f)  # sha 仍 0*64
    codes = [v.code for v in ledger.validate_ledger(b, check_files=True)]
    assert "sha256_mismatch" in codes
    b2 = make_valid()
    b2["models"][0]["representations"][0]["uri"] = str(tmp_path / "gone.glb")
    assert "file_missing" in [
        v.code for v in ledger.validate_ledger(b2, check_files=True)
    ]


def test_ledger_path():
    assert (
        str(ledger.ledger_path("/lib", "315_shears")) == "/lib/315_shears/ledger.json"
    )


def test_new_model_entry_and_upsert():
    m = ledger.new_model_entry(
        model=0,
        representations=make_model()["representations"],
        mesh_bbox_m=[0.078, 0.051, 0.053],
        mesh_up_axis="Y",
        origin_convention="bottom-center",
        scale_applied=1.0,
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
        aliases=["shears"],
        colors=[],
        materials=[],
        tags=["rigid", "external"],
        model_entry=m,
    )
    assert (
        led["asset_id"] == "external_315_shears"
    )  # 缺省前缀规则，可传 asset_id_prefix 覆盖
    assert ledger.validate_ledger(led, check_files=False) == []
    m1 = dict(m, model_id=1)
    led2 = ledger.upsert_model(
        led,
        asset="315_shears",
        category="shears",
        kind="rigid",
        aliases=["shears"],
        colors=[],
        materials=[],
        tags=["rigid", "external"],
        model_entry=m1,
    )
    assert [x["model_id"] for x in led2["models"]] == [0, 1]
    with pytest.raises(ValueError):  # 资产级漂移写时即抓
        ledger.upsert_model(
            led2,
            asset="315_shears",
            category="shears",
            kind="rigid",
            aliases=["tin"],
            colors=[],
            materials=[],
            tags=["rigid", "external"],
            model_entry=dict(m, model_id=2),
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
    latest = ledger.latest_verification(out["models"][0], "sapien", "settle")
    assert latest["verdict"] == "fail"  # 新 fail 压过旧 pass —— 禁 any(pass)
    assert (
        ledger.append_verification(p, 0, fail)["models"][0]["verification"]
        == out["models"][0]["verification"]
    )  # 同 (backend,check,run_id,digest) 去重
    stale = dict(
        fail,
        run_id="r3",
        timestamp="2026-08-08T13:00:00",
        verified_digest="e" * 64,
        verdict="pass",
    )
    out3 = ledger.append_verification(p, 0, stale)
    assert ledger.latest_verification(out3["models"][0], "sapien", "settle") is None
    # ↑ 最新条 digest 与当前 reps 不符 → 失效返回 None（如实报未验证）


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
