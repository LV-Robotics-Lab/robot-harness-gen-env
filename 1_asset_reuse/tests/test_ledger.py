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
            {
                "format": "png",
                "uri": "/tmp/x/snapshot.png",
                "backend": "sapien",
                "role": "snapshot",
                "sha256": "1" * 64,
                "size_bytes": 5,
                "metadata": {},
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
    (_set("asset_id", None), "missing"),
    (_set("kind", None), "missing"),
    (_set("tags", None), "missing"),
    (_set("models", None), "missing"),
    # I-4: models present but not a list (e.g. an empty dict) must not
    # silently produce zero violations.
    (_set("models", {}), "bad_type"),
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

    # I-3: re-upserting an EXISTING model_id must replace that entry wholesale
    # in place, not append a duplicate.
    m0_updated = dict(m, physical=dict(m["physical"], scale_applied=2.0))
    led3 = ledger.upsert_model(
        led2,
        asset="315_shears",
        category="shears",
        kind="rigid",
        aliases=["shears"],
        colors=[],
        materials=[],
        tags=["rigid", "external"],
        model_entry=m0_updated,
    )
    assert len(led3["models"]) == 2  # still 2 -- replaced, not appended
    assert (
        led3["models"][0]["physical"]["scale_applied"] == 2.0
    )  # content actually replaced

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


def test_upsert_model_detects_semantic_name_and_asset_drift():
    # C-1: the "asset-level fields must match on re-upsert" rule had missed
    # semantic_name entirely (silently kept the old value), and never
    # cross-checked the `asset` param itself against the existing asset_id.
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
    with pytest.raises(ValueError):  # semantic_name drift
        ledger.upsert_model(
            led,
            asset="315_shears",
            category="shears",
            kind="rigid",
            aliases=["shears"],
            colors=[],
            materials=[],
            tags=["rigid", "external"],
            semantic_name="not_shears",
            model_entry=dict(m, model_id=1),
        )
    with pytest.raises(ValueError):  # wrong asset name, caught via asset_id suffix
        ledger.upsert_model(
            led,
            asset="999_wrong",
            category="shears",
            kind="rigid",
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
    early_pass = dict(
        fail, run_id="r1_early", timestamp="2026-08-08T08:00:00", verdict="pass"
    )
    out_early = ledger.append_verification(p, 0, early_pass)
    assert len(out_early["models"][0]["verification"]) == 3
    latest_after_early = ledger.latest_verification(
        out_early["models"][0], "sapien", "settle"
    )
    assert latest_after_early["run_id"] == "r2"  # still the 12:00 fail

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
    # I-7: pin reps_digest's exact contract (sorted, comma-joined, snapshot
    # excluded, wrong-backend excluded) against a hand-computed hex value
    # rather than re-deriving the same hashlib call inside the test.
    model = {
        "representations": [
            {"backend": "sapien", "role": "visual", "sha256": "2" * 64},
            {"backend": "sapien", "role": "collision", "sha256": "1" * 64},
            {"backend": "sapien", "role": "snapshot", "sha256": "9" * 64},  # excluded
            {"backend": "isaacsim", "role": "visual", "sha256": "3" * 64},  # excluded
        ]
    }
    # hand-computed offline:
    #   sha256(",".join(sorted(["1"*64, "2"*64])).encode()).hexdigest()
    assert (
        ledger.reps_digest(model, "sapien")
        == "d24a4134be711dad6027de088dda210b4d2fb13a7528e0d87e5918c837150e2f"
    )


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
        }
    ]
    m = ledger.new_model_entry(
        model=0,
        representations=make_model()["representations"],
        mesh_bbox_m=[0.6, 0.4, 0.8],
        mesh_up_axis="Z",
        origin_convention="base-at-floor",
        scale_applied=1.0,
        size_resolution=make_model()["physical"]["size_resolution"],
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
        aliases=["cabinet"],
        colors=[],
        materials=[],
        tags=["articulated", "external"],
        model_entry=m,
    )
    assert ledger.validate_ledger(led, check_files=False) == []
    assert led["kind"] == "articulated"
    assert (
        led["models"][0]["articulation"]["balance_gate"]["free_joints_allowed"] is False
    )
    assert (
        led["models"][0]["physical"]["mass_kg"]["runtime_default_basis"]
        == "urdf_inertial"
    )
