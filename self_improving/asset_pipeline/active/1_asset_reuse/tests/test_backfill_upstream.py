import hashlib
import json
import subprocess
import sys
from pathlib import Path

import trimesh

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backfill_upstream.py"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import ledger  # noqa: E402


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _write_box_mesh(path, extents=(0.1, 0.1, 0.1), translate=(0.0, 0.0, 0.0)):
    """Write a real, trimesh-loadable box mesh to path (format inferred
    from the suffix -- .glb/.obj both supported) with known extents,
    translated so a caller can control which axis (if any) touches zero.
    E.g. translate=(0, extents[1] / 2, 0) makes the box's Y-minimum sit at
    0 -- "rests on the floor along Y", exactly what
    backfill_upstream.py's _measure_rigid_geometry looks for. Round 1-3's
    fixtures used opaque placeholder bytes for rigid visual/collision
    files; that stopped working once round 4 started actually loading
    rigid visual files via trimesh to measure mesh_up_axis. Returns the
    sha256 of the exported file (trimesh's exact byte output isn't
    something a caller should hardcode/predict)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mesh = trimesh.creation.box(extents=extents)
    mesh.apply_translation(translate)
    mesh.export(str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mini_catalog(tmp_path):
    """One rigid asset (901_widget: model 0 usable with real geometry
    resting on the floor along Y -- min_y == 0 after translation -- so it
    measures to mesh_up_axis "Y"; model 1 usable:false but OTHERWISE
    complete, including a legitimate stable pose (review fix I2.2), so
    usable-filtering tests exercise the usable:false flag itself and not
    some other missing-field side effect -- model 1's mesh files stay
    opaque placeholder bytes since usable:false models are filtered out
    before _resolve_models ever tries to load them) + one articulated
    asset (902_gadget: single usable model, non-zero model_id, one
    revolute joint; articulated mesh_up_axis is a fixed constant
    regardless of stable_orientation_wxyz -- see backfill_upstream.py's
    module docstring -- so this fixture's IDENTITY orientation only
    exercises stable_poses, never axis resolution)."""
    rt = tmp_path / "RoboTwin"
    objects = rt / "assets/objects"

    a1 = objects / "901_widget"
    vis0, col0 = a1 / "visual/base0.glb", a1 / "collision/base0.glb"
    vis0_sha = _write_box_mesh(vis0, extents=(0.05, 0.08, 0.05), translate=(0, 0.04, 0))
    col0_sha = _write_box_mesh(col0, extents=(0.05, 0.08, 0.05), translate=(0, 0.04, 0))
    (a1 / "model_data0.json").parent.mkdir(parents=True, exist_ok=True)
    (a1 / "model_data0.json").write_text("{}")
    vis1, col1 = a1 / "visual/base1.glb", a1 / "collision/base1.glb"
    # I2.2 (review fix-round-2): model 1 must have REAL, loadable, non-
    # ambiguous geometry, not opaque placeholder bytes. With placeholder
    # bytes, trimesh.load() throws on model 1 regardless of the usable
    # filter, so it would ALSO land in up_axis_ambiguous if the usable
    # filter were ever broken/removed -- making usable-filtering tests
    # vacuously true (they'd still pass via a completely different
    # exclusion path, not because usable:false was actually enforced).
    # Real, unambiguously-Y-up geometry here means a broken usable filter
    # would let model 1 resolve to a concrete axis and actually get
    # ingested, so "model 1 not in the ledger" genuinely depends on the
    # usable:false gate. Confirmed by mutation testing (see u2-report.md).
    _write_box_mesh(vis1, extents=(0.04, 0.07, 0.04), translate=(0, 0.035, 0))
    _write_box_mesh(col1, extents=(0.04, 0.07, 0.04), translate=(0, 0.035, 0))

    rigid_entry = {
        "asset_id": "901_widget",
        "semantic_name": "widget",
        "category": "widget",
        "aliases": [],
        "colors": ["red"],
        "materials": ["plastic"],
        "load_type": "rigid",
        "asset_path": str(a1),
        "models": [
            {
                "model_id": 0,
                "model_path": str(a1),
                "metadata_path": str(a1 / "model_data0.json"),
                "visual_path": str(vis0),
                "collision_path": str(col0),
                "scale": [1.0, 1.0, 1.0],
                "dimensions_m": [0.05, 0.06, 0.12],  # unused for rigid mesh_bbox_m
                "footprint_shape": "circle",
                "support_margin_m": 0.005,
                "support_spawn_clearance_m": 0.003,
                "stable_pose_id": "upright",
                "stable_orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                "z_policy": "origin_on_table",
                "is_static": False,
                "articulation_joints": [],
                "articulation_closed_qpos": [],
                "articulation_open_qpos": [],
                "usable": True,
                "missing": [],
            },
            {
                "model_id": 1,
                "model_path": str(a1),
                "metadata_path": str(a1 / "model_data1.json"),
                "visual_path": str(vis1),
                "collision_path": str(col1),
                "scale": [0.1, 0.1, 0.1],
                "dimensions_m": [0.05, 0.06, 0.12],
                "footprint_shape": "box",
                "support_margin_m": 0.005,
                "support_spawn_clearance_m": 0.003,
                # I2.2: a legitimate stable pose even though usable:false --
                # this model must be excluded ONLY because usable is False,
                # not incidentally because some other field is missing.
                "stable_pose_id": "upright",
                "stable_orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                "z_policy": "origin_on_table",
                "is_static": False,
                "articulation_joints": [],
                "articulation_closed_qpos": [],
                "articulation_open_qpos": [],
                "usable": False,
                "missing": ["quality_review_pending"],
            },
        ],
    }

    a2 = objects / "902_gadget/10001"
    urdf = a2 / "mobility.urdf"
    _write(urdf, b"<robot name='g'></robot>")
    (a2 / "model_data.json").write_text("{}")

    articulated_entry = {
        "asset_id": "902_gadget",
        "semantic_name": "gadget",
        "category": "gadget",
        "aliases": ["gadget"],
        "colors": [],
        "materials": [],
        "load_type": "urdf",
        "asset_path": str(objects / "902_gadget"),
        "models": [
            {
                "model_id": 10001,
                "model_path": str(a2),
                "metadata_path": str(a2 / "model_data.json"),
                "visual_path": str(urdf),
                "collision_path": str(urdf),
                "urdf_path": str(urdf),
                "scale": [0.2, 0.2, 0.2],
                "dimensions_m": [0.3, 0.2, 0.25],
                "footprint_shape": "box",
                "support_margin_m": 0.005,
                "support_spawn_clearance_m": 0.003,
                "stable_pose_id": "flat",
                "stable_orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                "z_policy": "origin_on_table",
                "is_static": True,
                "articulation_joints": [
                    {
                        "name": "joint_0",
                        "joint_type": "revolute",
                        "lower": -1.0,
                        "upper": 1.0,
                    }
                ],
                "articulation_closed_qpos": [0.0],
                "articulation_open_qpos": [1.0],
                "usable": True,
                "missing": [],
            }
        ],
    }

    catalog = {
        "schema_version": 1,
        "robotwin_root": str(rt),
        "objects_root": str(objects),
        "source_commit": "deadbeef123",
        "entries": [rigid_entry, articulated_entry],
    }
    catalog_path = tmp_path / "asset_catalog.json"
    catalog_path.write_text(json.dumps(catalog, indent=2))
    return catalog_path, {
        "vis0": vis0,
        "col0": col0,
        "vis0_sha": vis0_sha,
        "col0_sha": col0_sha,
        "urdf": urdf,
    }


def _run(catalog_path, out_dir, apply=False, extra_args=()):
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--catalog",
        str(catalog_path),
        "--out",
        str(out_dir),
    ]
    if apply:
        cmd.append("--apply")
    cmd += list(extra_args)
    return subprocess.run(cmd, capture_output=True, text=True)


def test_apply_writes_expected_ledgers_and_report(tmp_path):
    catalog_path, files = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    r = _run(catalog_path, out, apply=True)
    assert r.returncode == 0, r.stderr

    report = json.loads((out / "backfill_upstream_report.json").read_text())
    assert sorted(report["written"]) == ["901_widget", "902_gadget"]
    assert report["skipped_unusable"] == ["901_widget:m1"]
    assert report["aliases_defaulted"] == ["901_widget"]
    assert report["violations"] == {}

    led = json.loads((out / "901_widget/ledger.json").read_text())
    assert [m["model_id"] for m in led["models"]] == [0]  # model 1 (unusable) excluded


def test_asset_id_has_robotwin_prefix(tmp_path):
    catalog_path, _ = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    r = _run(catalog_path, out, apply=True)
    assert r.returncode == 0, r.stderr
    led = json.loads((out / "901_widget/ledger.json").read_text())
    assert led["asset_id"] == "robotwin_901_widget"
    led2 = json.loads((out / "902_gadget/ledger.json").read_text())
    assert led2["asset_id"] == "robotwin_902_gadget"


def test_aliases_default_to_category_when_empty(tmp_path):
    catalog_path, _ = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    r = _run(catalog_path, out, apply=True)
    assert r.returncode == 0, r.stderr
    led = json.loads((out / "901_widget/ledger.json").read_text())
    assert led["semantics"]["aliases"] == ["widget"]  # == [category]


def test_stable_pose_shape(tmp_path):
    catalog_path, _ = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    r = _run(catalog_path, out, apply=True)
    assert r.returncode == 0, r.stderr
    led = json.loads((out / "901_widget/ledger.json").read_text())
    poses = led["models"][0]["physical"]["conventions"]["stable_poses"]
    assert poses == [
        {
            "pose_id": "upright",
            "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
            "is_default": True,
        }
    ]


def test_source_manifest_generated_and_referenced(tmp_path):
    catalog_path, files = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    r = _run(catalog_path, out, apply=True)
    assert r.returncode == 0, r.stderr

    manifest_path = out / "901_widget/SOURCE_MANIFEST.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["files"]["visual/base0.glb"] == files["vis0_sha"]
    assert manifest["files"]["collision/base0.glb"] == files["col0_sha"]

    led = json.loads((out / "901_widget/ledger.json").read_text())
    src_manifest_path = led["models"][0]["source"]["source_manifest_path"]
    assert Path(src_manifest_path).exists()
    assert Path(src_manifest_path).read_text() == manifest_path.read_text()


def test_representation_sha256_and_size_are_real(tmp_path):
    catalog_path, files = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    r = _run(catalog_path, out, apply=True)
    assert r.returncode == 0, r.stderr
    led = json.loads((out / "901_widget/ledger.json").read_text())
    reps = {rep["role"]: rep for rep in led["models"][0]["representations"]}
    assert reps["visual"]["sha256"] == files["vis0_sha"]
    assert reps["visual"]["size_bytes"] == files["vis0"].stat().st_size
    assert reps["collision"]["sha256"] == files["col0_sha"]
    assert reps["collision"]["size_bytes"] == files["col0"].stat().st_size
    assert reps["visual"]["backend"] == "sapien"
    assert reps["visual"]["format"] == "glb"


def test_articulated_representation_is_single_urdf_entry(tmp_path):
    catalog_path, files = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    r = _run(catalog_path, out, apply=True)
    assert r.returncode == 0, r.stderr
    led = json.loads((out / "902_gadget/ledger.json").read_text())
    reps = led["models"][0]["representations"]
    sapien_reps = [rp for rp in reps if rp["backend"] == "sapien"]
    assert len(sapien_reps) == 1
    assert sapien_reps[0]["role"] == "visual_and_collision"
    assert sapien_reps[0]["format"] == "urdf"
    assert sapien_reps[0]["sha256"] == _sha(b"<robot name='g'></robot>")


def test_articulation_mapping(tmp_path):
    catalog_path, _ = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    r = _run(catalog_path, out, apply=True)
    assert r.returncode == 0, r.stderr
    led = json.loads((out / "902_gadget/ledger.json").read_text())
    art = led["models"][0]["articulation"]
    assert art["joint_names"] == ["joint_0"]
    assert art["closed_qpos"] == [0.0]
    assert art["open_qpos"] == [1.0]
    assert led["kind"] == "articulated"


def test_usable_false_model_not_ingested(tmp_path):
    catalog_path, _ = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    r = _run(catalog_path, out, apply=True)
    assert r.returncode == 0, r.stderr
    led = json.loads((out / "901_widget/ledger.json").read_text())
    model_ids = [m["model_id"] for m in led["models"]]
    assert 1 not in model_ids


def test_validator_zero_violations(tmp_path):
    catalog_path, _ = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    r = _run(catalog_path, out, apply=True)
    assert r.returncode == 0, r.stderr
    for asset in ("901_widget", "902_gadget"):
        led = json.loads((out / asset / "ledger.json").read_text())
        assert ledger.validate_ledger(led, check_files=True) == []


def test_dry_run_does_not_write_ledger_files(tmp_path):
    catalog_path, _ = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    r = _run(catalog_path, out, apply=False)
    assert r.returncode == 0, r.stderr
    assert not (out / "901_widget/ledger.json").exists()
    assert not (out / "901_widget/SOURCE_MANIFEST.json").exists()
    assert not (out / "902_gadget/ledger.json").exists()
    report = json.loads((out / "backfill_upstream_report.json").read_text())
    assert sorted(report["written"]) == ["901_widget", "902_gadget"]


def test_apply_is_idempotent(tmp_path):
    # I2.3: byte comparison extended to SOURCE_MANIFEST.json, not just
    # ledger.json -- it's regenerated from a fresh disk scan on every
    # --apply run, and should be just as stable across reruns.
    catalog_path, _ = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    r1 = _run(catalog_path, out, apply=True)
    assert r1.returncode == 0, r1.stderr
    before_ledger1 = (out / "901_widget/ledger.json").read_text()
    before_ledger2 = (out / "902_gadget/ledger.json").read_text()
    before_manifest1 = (out / "901_widget/SOURCE_MANIFEST.json").read_text()
    before_manifest2 = (out / "902_gadget/SOURCE_MANIFEST.json").read_text()

    r2 = _run(catalog_path, out, apply=True)
    assert r2.returncode == 0, r2.stderr
    after_ledger1 = (out / "901_widget/ledger.json").read_text()
    after_ledger2 = (out / "902_gadget/ledger.json").read_text()
    after_manifest1 = (out / "901_widget/SOURCE_MANIFEST.json").read_text()
    after_manifest2 = (out / "902_gadget/SOURCE_MANIFEST.json").read_text()

    assert before_ledger1 == after_ledger1
    assert before_ledger2 == after_ledger2
    assert before_manifest1 == after_manifest1
    assert before_manifest2 == after_manifest2


def test_incremental_layer_preserved_across_rerun(tmp_path):
    catalog_path, _ = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    r1 = _run(catalog_path, out, apply=True)
    assert r1.returncode == 0, r1.stderr

    lp = out / "901_widget/ledger.json"
    led = json.loads(lp.read_text())
    model0 = led["models"][0]
    fake_usd = tmp_path / "manual_isaac/901_widget.usd"
    _write(fake_usd, b"USD-MANUAL-901")
    fake_isaac_rep = {
        "format": "usd",
        "uri": str(fake_usd),
        "backend": "isaacsim",
        "role": "visual_and_collision",
        "sha256": _sha(b"USD-MANUAL-901"),
        "size_bytes": len(b"USD-MANUAL-901"),
        "metadata": {
            "derived_from": model0["representations"][0]["uri"],
            "converter": "manual-test-injection",
            "conversion_params": {},
        },
    }
    model0["representations"].append(fake_isaac_rep)
    model0["source"]["license"] = {
        "spdx": "MIT",
        "status": "declared",
        "terms_note": "hand-audited for this test",
    }
    model0["verification"].append(
        {
            "backend": "sapien",
            "check": "settle",
            "verdict": "pass",
            "run_id": "manual_test_run",
            "timestamp": "2026-08-08T10:00:00",
            "verified_digest": ledger.reps_digest(model0, "sapien"),
            "report_path": "/tmp/fake/report.json",
        }
    )
    lp.write_text(json.dumps(led, indent=2))

    r2 = _run(catalog_path, out, apply=True)
    assert r2.returncode == 0, r2.stderr

    led2 = json.loads(lp.read_text())
    model0_2 = led2["models"][0]
    isaac_reps = [
        rp for rp in model0_2["representations"] if rp["backend"] == "isaacsim"
    ]
    assert len(isaac_reps) == 1
    assert isaac_reps[0]["sha256"] == _sha(b"USD-MANUAL-901")
    assert model0_2["source"]["license"]["status"] == "declared"
    assert model0_2["source"]["license"]["spdx"] == "MIT"
    assert len(model0_2["verification"]) == 1
    assert model0_2["verification"][0]["run_id"] == "manual_test_run"

    # derived core still refreshed: sapien reps still present & correct
    sapien_reps = [
        rp for rp in model0_2["representations"] if rp["backend"] == "sapien"
    ]
    assert len(sapien_reps) == 2


def test_isaac_usd_registration(tmp_path):
    catalog_path, _ = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    usd_path = tmp_path / "901_widget.usd"
    usd_path.write_bytes(b"USD-CONTENT-901")

    r = _run(
        catalog_path,
        out,
        apply=True,
        extra_args=[f"--isaac-usd=901_widget={usd_path}"],
    )
    assert r.returncode == 0, r.stderr

    led = json.loads((out / "901_widget/ledger.json").read_text())
    model0 = led["models"][0]
    isaac_reps = [rp for rp in model0["representations"] if rp["backend"] == "isaacsim"]
    assert len(isaac_reps) == 1
    rep = isaac_reps[0]
    assert rep["role"] == "visual_and_collision"
    assert rep["format"] == "usd"
    assert rep["sha256"] == _sha(b"USD-CONTENT-901")
    assert rep["size_bytes"] == len(b"USD-CONTENT-901")
    assert rep["metadata"]["derived_from"] == model0["representations"][0]["uri"]
    assert rep["metadata"]["converter"]

    # re-running with the SAME --isaac-usd path must not duplicate the entry
    r2 = _run(
        catalog_path,
        out,
        apply=True,
        extra_args=[f"--isaac-usd=901_widget={usd_path}"],
    )
    assert r2.returncode == 0, r2.stderr
    led2 = json.loads((out / "901_widget/ledger.json").read_text())
    isaac_reps2 = [
        rp for rp in led2["models"][0]["representations"] if rp["backend"] == "isaacsim"
    ]
    assert len(isaac_reps2) == 1


def test_isaac_usd_missing_file_errors_out(tmp_path):
    catalog_path, _ = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    missing = tmp_path / "does_not_exist.usd"
    r = _run(
        catalog_path,
        out,
        apply=True,
        extra_args=[f"--isaac-usd=901_widget={missing}"],
    )
    assert r.returncode != 0
    assert not (out / "901_widget/ledger.json").exists()


def test_isaac_usd_registration_articulated_first_usable_model(tmp_path):
    # 902_gadget's only usable model has model_id=10001 (a PartNet-Mobility
    # id, never literally 0) -- "model 0" in the spec is interpreted as
    # "catalog order's first usable model", not literal model_id==0, since
    # the latter is never true for any articulated asset in the real
    # catalog and would make --isaac-usd unusable for that whole kind.
    catalog_path, _ = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    usd_path = tmp_path / "902_gadget.usd"
    usd_path.write_bytes(b"USD-CONTENT-902")

    r = _run(
        catalog_path,
        out,
        apply=True,
        extra_args=[f"--isaac-usd=902_gadget={usd_path}"],
    )
    assert r.returncode == 0, r.stderr

    led = json.loads((out / "902_gadget/ledger.json").read_text())
    model0 = led["models"][0]
    assert model0["model_id"] == 10001
    isaac_reps = [rp for rp in model0["representations"] if rp["backend"] == "isaacsim"]
    assert len(isaac_reps) == 1
    rep = isaac_reps[0]
    assert rep["role"] == "visual_and_collision"
    assert rep["sha256"] == _sha(b"USD-CONTENT-902")
    assert rep["size_bytes"] == len(b"USD-CONTENT-902")
    assert rep["metadata"]["derived_from"] == model0["representations"][0]["uri"]


# --- round 4 (review fix-round-1 C1+C2): mesh_up_axis measured off the
# asset's own files (rigid) or fixed by a verified upstream convention
# (articulated) -- stable_orientation_wxyz no longer has any bearing on it.


def test_rigid_up_axis_measured_y_up(tmp_path):
    # 901_widget's shared-fixture geometry rests on the floor along Y
    # (min_y == 0 after translation) -- measured, not inferred from
    # stable_orientation_wxyz (which is IDENTITY here and is irrelevant to
    # axis resolution under round 4).
    catalog_path, _ = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    r = _run(catalog_path, out, apply=True)
    assert r.returncode == 0, r.stderr
    led = json.loads((out / "901_widget/ledger.json").read_text())
    physical = led["models"][0]["physical"]
    rep0 = led["models"][0]["representations"][0]
    assert rep0["frame"]["up_axis"] == "Y"
    assert rep0["geometry_state"]["origin"] == "bottom-center"
    assert rep0["geometry_state"]["scale_baked"] is False
    # mesh_bbox_m comes from the SAME trimesh measurement x scale_applied
    # (1.0 in this fixture), not catalog dimensions_m ([0.05, 0.06, 0.12],
    # deliberately different in this fixture to prove it's unused). GLB
    # round-trips vertices through float32, so exact equality is too
    # strict -- compare with a tight tolerance instead.
    for actual, expected in zip(physical["mesh_bbox_m"], [0.05, 0.08, 0.05]):
        assert abs(actual - expected) < 1e-5
    assert abs(physical["size_resolution"]["actual_max_dim_m"] - 0.08) < 1e-5


def _mini_catalog_rigid_zup(tmp_path):
    """A standalone rigid asset whose real geometry rests on the floor
    along Z (min_z == 0) -- exercises the other measured axis."""
    rt = tmp_path / "RoboTwinZup"
    objects = rt / "assets/objects"
    a = objects / "806_zblock"
    vis0, col0 = a / "visual/base0.glb", a / "collision/base0.glb"
    _write_box_mesh(vis0, extents=(0.05, 0.05, 0.06), translate=(0, 0, 0.03))
    _write_box_mesh(col0, extents=(0.05, 0.05, 0.06), translate=(0, 0, 0.03))

    entry = {
        "asset_id": "806_zblock",
        "semantic_name": "zblock",
        "category": "zblock",
        "aliases": ["zblock"],
        "colors": [],
        "materials": [],
        "load_type": "rigid",
        "asset_path": str(a),
        "models": [
            {
                "model_id": 0,
                "model_path": str(a),
                "metadata_path": str(a / "model_data0.json"),
                "visual_path": str(vis0),
                "collision_path": str(col0),
                "scale": [1.0, 1.0, 1.0],
                "dimensions_m": [0.05, 0.05, 0.05],
                "footprint_shape": "box",
                "support_margin_m": 0.005,
                "support_spawn_clearance_m": 0.003,
                "stable_pose_id": "upright",
                "stable_orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                "z_policy": "origin_on_table",
                "is_static": False,
                "articulation_joints": [],
                "articulation_closed_qpos": [],
                "articulation_open_qpos": [],
                "usable": True,
                "missing": [],
            }
        ],
    }
    catalog = {
        "schema_version": 1,
        "robotwin_root": str(rt),
        "objects_root": str(objects),
        "source_commit": "zblockcommit",
        "entries": [entry],
    }
    catalog_path = tmp_path / "zblock_catalog.json"
    catalog_path.write_text(json.dumps(catalog, indent=2))
    return catalog_path


def test_rigid_up_axis_measured_z_up(tmp_path):
    catalog_path = _mini_catalog_rigid_zup(tmp_path)
    out = tmp_path / "out"
    r = _run(catalog_path, out, apply=True)
    assert r.returncode == 0, r.stderr
    led = json.loads((out / "806_zblock/ledger.json").read_text())
    physical = led["models"][0]["physical"]
    rep0 = led["models"][0]["representations"][0]
    assert rep0["frame"]["up_axis"] == "Z"
    assert rep0["geometry_state"]["origin"] == "base-at-floor"
    assert ledger.validate_ledger(led, check_files=True) == []


def _mini_catalog_ambiguous(tmp_path):
    """A rigid asset whose real geometry is a box CENTERED at its own
    local origin (bounds symmetric about zero on all three axes) --
    matches the actual measured shape of the real catalog's 020_hammer /
    034_knife (confirmed by direct trimesh measurement: their
    min/(extent/2) ratio is ~1.0 on every axis, not a near-miss on any
    one). No axis touches zero, so up_axis genuinely can't be determined
    by this method."""
    rt = tmp_path / "RoboTwinAmbiguous"
    objects = rt / "assets/objects"
    a = objects / "805_odd"
    vis0, col0 = a / "visual/base0.glb", a / "collision/base0.glb"
    _write_box_mesh(vis0, extents=(0.05, 0.05, 0.05))  # no translation -> centered
    _write_box_mesh(col0, extents=(0.05, 0.05, 0.05))

    entry = {
        "asset_id": "805_odd",
        "semantic_name": "odd",
        "category": "odd",
        "aliases": ["odd"],
        "colors": [],
        "materials": [],
        "load_type": "rigid",
        "asset_path": str(a),
        "models": [
            {
                "model_id": 0,
                "model_path": str(a),
                "metadata_path": str(a / "model_data0.json"),
                "visual_path": str(vis0),
                "collision_path": str(col0),
                "scale": [1.0, 1.0, 1.0],
                "dimensions_m": [0.05, 0.05, 0.05],
                "footprint_shape": "box",
                "support_margin_m": 0.005,
                "support_spawn_clearance_m": 0.003,
                "stable_pose_id": "odd_pose",
                "stable_orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                "z_policy": "origin_on_table",
                "is_static": False,
                "articulation_joints": [],
                "articulation_closed_qpos": [],
                "articulation_open_qpos": [],
                "usable": True,
                "missing": [],
            }
        ],
    }
    catalog = {
        "schema_version": 1,
        "robotwin_root": str(rt),
        "objects_root": str(objects),
        "source_commit": "ambiguouscommit",
        "entries": [entry],
    }
    catalog_path = tmp_path / "ambiguous_catalog.json"
    catalog_path.write_text(json.dumps(catalog, indent=2))
    return catalog_path


def test_up_axis_ambiguous_model_is_excluded(tmp_path):
    catalog_path = _mini_catalog_ambiguous(tmp_path)
    out = tmp_path / "out"
    r = _run(catalog_path, out, apply=True)
    assert r.returncode == 0, r.stderr  # not a validation failure -- an honest skip

    assert not (out / "805_odd/ledger.json").exists()

    report = json.loads((out / "backfill_upstream_report.json").read_text())
    assert report["notes"]["up_axis_ambiguous"] == ["805_odd:m0"]
    assert "805_odd" not in report["written"]


def test_articulated_axis_fixed_regardless_of_stable_orientation(tmp_path):
    # Decoupling demo (C1+C2's core claim): an articulated asset carrying
    # an X90 stable_orientation_wxyz (like the real 036_cabinet) still
    # measures mesh_up_axis "Z" -- articulated axis is a fixed constant,
    # never derived from stable_orientation_wxyz -- while that same X90
    # value is still preserved verbatim in stable_poses (pose data stays
    # catalog-authored; geometry stays file-measured; the two never
    # contradict each other because they no longer answer the same
    # question).
    rt = tmp_path / "RoboTwinX90"
    objects = rt / "assets/objects"
    a = objects / "804_hinge/500"
    urdf = a / "mobility.urdf"
    _write(urdf, b"<robot name='h'></robot>")

    entry = {
        "asset_id": "804_hinge",
        "semantic_name": "hinge",
        "category": "hinge",
        "aliases": ["hinge"],
        "colors": [],
        "materials": [],
        "load_type": "urdf",
        "asset_path": str(objects / "804_hinge"),
        "models": [
            {
                "model_id": 500,
                "model_path": str(a),
                "metadata_path": str(a / "model_data.json"),
                "visual_path": str(urdf),
                "collision_path": str(urdf),
                "urdf_path": str(urdf),
                "scale": [0.2, 0.2, 0.2],
                "dimensions_m": [0.1, 0.1, 0.1],
                "footprint_shape": "box",
                "support_margin_m": 0.005,
                "support_spawn_clearance_m": 0.003,
                "stable_pose_id": "upright",
                "stable_orientation_wxyz": list(ledger.X90_WXYZ),
                "z_policy": "origin_on_table",
                "is_static": False,
                "articulation_joints": [
                    {
                        "name": "j0",
                        "joint_type": "revolute",
                        "lower": -0.5,
                        "upper": 0.5,
                    }
                ],
                "articulation_closed_qpos": [0.0],
                "articulation_open_qpos": [0.5],
                "usable": True,
                "missing": [],
            }
        ],
    }
    catalog = {
        "schema_version": 1,
        "robotwin_root": str(rt),
        "objects_root": str(objects),
        "source_commit": "x90commit",
        "entries": [entry],
    }
    catalog_path = tmp_path / "x90_catalog.json"
    catalog_path.write_text(json.dumps(catalog, indent=2))

    out = tmp_path / "out"
    r = _run(catalog_path, out, apply=True)
    assert r.returncode == 0, r.stderr
    led = json.loads((out / "804_hinge/ledger.json").read_text())
    physical = led["models"][0]["physical"]
    rep0 = led["models"][0]["representations"][0]
    assert rep0["frame"]["up_axis"] == "Z"
    assert rep0["geometry_state"]["origin"] == "base-at-floor"
    # the X90 stable-pose data itself is preserved verbatim, unaffected.
    assert physical["conventions"]["stable_poses"][0]["orientation_wxyz"] == list(
        ledger.X90_WXYZ
    )
    assert ledger.validate_ledger(led, check_files=True) == []


def test_articulated_up_axis_identity_orientation_is_zup(tmp_path):
    # 902_gadget in the shared fixture carries stable_orientation_wxyz ==
    # IDENTITY, but that's incidental now: articulated mesh_up_axis is a
    # fixed constant (see backfill_upstream.py's module docstring, and
    # test_articulated_axis_fixed_regardless_of_stable_orientation above
    # for the same asset with a non-Z-suggesting orientation).
    catalog_path, _ = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    r = _run(catalog_path, out, apply=True)
    assert r.returncode == 0, r.stderr
    led = json.loads((out / "902_gadget/ledger.json").read_text())
    rep0 = led["models"][0]["representations"][0]
    assert rep0["frame"]["up_axis"] == "Z"
    assert rep0["geometry_state"]["origin"] == "base-at-floor"


def test_format_derived_from_uri_suffix(tmp_path):
    # C3: representations[].format comes from the file's own suffix, not a
    # hardcoded "glb" -- real-catalog regression: the four 900_* series
    # assets use .obj visual/collision files, which round 1-3 silently
    # mislabeled "glb".
    rt = tmp_path / "RoboTwinObj"
    objects = rt / "assets/objects"
    a = objects / "900_gen_testblock"
    vis0, col0 = a / "visual/textured0.obj", a / "collision/textured0.obj"
    _write_box_mesh(vis0, extents=(0.04, 0.04, 0.04), translate=(0, 0, 0.02))
    _write_box_mesh(col0, extents=(0.04, 0.04, 0.04), translate=(0, 0, 0.02))

    entry = {
        "asset_id": "900_gen_testblock",
        "semantic_name": "testblock",
        "category": "testblock",
        "aliases": ["testblock"],
        "colors": [],
        "materials": [],
        "load_type": "rigid",
        "asset_path": str(a),
        "models": [
            {
                "model_id": 0,
                "model_path": str(a),
                "metadata_path": str(a / "model_data0.json"),
                "visual_path": str(vis0),
                "collision_path": str(col0),
                "scale": [1.0, 1.0, 1.0],
                "dimensions_m": [0.04, 0.04, 0.04],
                "footprint_shape": "box",
                "support_margin_m": 0.005,
                "support_spawn_clearance_m": 0.003,
                "stable_pose_id": "procedural_flat_base",
                "stable_orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                "z_policy": "origin_on_table",
                "is_static": False,
                "articulation_joints": [],
                "articulation_closed_qpos": [],
                "articulation_open_qpos": [],
                "usable": True,
                "missing": [],
            }
        ],
    }
    catalog = {
        "schema_version": 1,
        "robotwin_root": str(rt),
        "objects_root": str(objects),
        "source_commit": "objcommit",
        "entries": [entry],
    }
    catalog_path = tmp_path / "obj_catalog.json"
    catalog_path.write_text(json.dumps(catalog, indent=2))

    out = tmp_path / "out"
    r = _run(catalog_path, out, apply=True)
    assert r.returncode == 0, r.stderr
    led = json.loads((out / "900_gen_testblock/ledger.json").read_text())
    reps = {rp["role"]: rp for rp in led["models"][0]["representations"]}
    assert reps["visual"]["format"] == "obj"
    assert reps["collision"]["format"] == "obj"
    assert led["models"][0]["representations"][0]["frame"]["up_axis"] == "Z"


def test_isaac_usd_no_ingestible_model_errors_out(tmp_path):
    # I1: --isaac-usd targeting an asset that IS in the catalog but has
    # zero ingestible models after resolution (here: its only usable model
    # is up_axis-ambiguous) errors out the same way an unknown asset does.
    catalog_path = _mini_catalog_ambiguous(tmp_path)
    out = tmp_path / "out"
    usd_path = tmp_path / "805_odd.usd"
    usd_path.write_bytes(b"USD-CONTENT-805")

    r = _run(
        catalog_path,
        out,
        apply=True,
        extra_args=[f"--isaac-usd=805_odd={usd_path}"],
    )
    assert r.returncode == 2
    assert not (out / "805_odd/ledger.json").exists()
    assert not (out / "backfill_upstream_report.json").exists()


def _mini_catalog_for_remap(tmp_path):
    """A rigid asset whose catalog paths all carry a fake OLD prefix that
    never exists on disk; the real files live under a separate NEW prefix
    directory. Exercises --root-remap OLD=NEW rewriting every absolute path
    field before any file-existence check / sha256 / uri write happens."""
    old_root = "/old/fake/RoboTwin"
    old_objects = f"{old_root}/assets/objects"
    new_root = tmp_path / "new_root"
    new_objects = new_root / "assets/objects"

    a1 = new_objects / "801_gizmo"
    vis0, col0 = a1 / "visual/base0.glb", a1 / "collision/base0.glb"
    _write_box_mesh(vis0, extents=(0.05, 0.05, 0.05), translate=(0, 0.025, 0))
    _write_box_mesh(col0, extents=(0.05, 0.05, 0.05), translate=(0, 0.025, 0))

    old_a1 = f"{old_objects}/801_gizmo"
    entry = {
        "asset_id": "801_gizmo",
        "semantic_name": "gizmo",
        "category": "gizmo",
        "aliases": ["gizmo"],
        "colors": [],
        "materials": [],
        "load_type": "rigid",
        "asset_path": old_a1,
        "models": [
            {
                "model_id": 0,
                "model_path": old_a1,
                "metadata_path": f"{old_a1}/model_data0.json",
                "visual_path": f"{old_a1}/visual/base0.glb",
                "collision_path": f"{old_a1}/collision/base0.glb",
                "scale": [1.0, 1.0, 1.0],
                "dimensions_m": [0.05, 0.05, 0.05],
                "footprint_shape": "box",
                "support_margin_m": 0.005,
                "support_spawn_clearance_m": 0.003,
                "stable_pose_id": "upright",
                "stable_orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                "z_policy": "origin_on_table",
                "is_static": False,
                "articulation_joints": [],
                "articulation_closed_qpos": [],
                "articulation_open_qpos": [],
                "usable": True,
                "missing": [],
            }
        ],
    }
    catalog = {
        "schema_version": 1,
        "robotwin_root": old_root,
        "objects_root": old_objects,
        "source_commit": "cafebabe",
        "entries": [entry],
    }
    catalog_path = tmp_path / "remap_catalog.json"
    catalog_path.write_text(json.dumps(catalog, indent=2))
    return catalog_path, old_objects, str(new_objects)


def test_root_remap_rewrites_paths_before_file_checks(tmp_path):
    catalog_path, old_prefix, new_prefix = _mini_catalog_for_remap(tmp_path)
    out = tmp_path / "out"
    r = _run(
        catalog_path,
        out,
        apply=True,
        extra_args=[f"--root-remap={old_prefix}={new_prefix}"],
    )
    assert r.returncode == 0, r.stderr

    led = json.loads((out / "801_gizmo/ledger.json").read_text())
    model0 = led["models"][0]
    for rep in model0["representations"]:
        assert rep["uri"].startswith(new_prefix)
        assert not rep["uri"].startswith(old_prefix)

    # source.file stays a clean relative path (relative to the new prefix),
    # not a raw absolute path -- robotwin_root itself never moved, only the
    # objects/ subtree did, so the relative-path base must follow the remap.
    assert model0["source"]["file"] == "801_gizmo"

    report = json.loads((out / "backfill_upstream_report.json").read_text())
    rr = report["notes"]["root_remap"]
    assert rr == {"old_prefix": old_prefix, "new_prefix": new_prefix, "hits": 5}


def _mini_catalog_for_remap_articulated(tmp_path):
    """I2.1: an articulated (urdf) asset whose OLD-prefix urdf_path is
    DISTINCT text from the OLD-prefix visual_path/collision_path (all three
    still point at the same real file once correctly remapped, but the
    strings differ before remap) -- _articulated_representations prefers
    urdf_path when present, so if urdf_path were ever dropped from
    _REMAP_FIELDS_MODEL, the resulting representation would keep pointing
    at the stale OLD (nonexistent) path even though visual_path/
    collision_path got remapped correctly. That would surface as a
    file_missing violation here, not a silent pass."""
    old_root = "/old/fake/RoboTwinArt"
    old_objects = f"{old_root}/assets/objects"
    new_root = tmp_path / "new_root_art"
    new_objects = new_root / "assets/objects"

    a = new_objects / "803_hingebox/700"
    urdf = a / "mobility.urdf"
    _write(urdf, b"<robot name='hb'></robot>")

    old_a = f"{old_objects}/803_hingebox/700"
    old_urdf_path = f"{old_a}/mobility.urdf"  # same string as visual/collision here,
    # but still exercises the field: if urdf_path were dropped from the
    # remap field list, this exact string would survive unrewritten and
    # the representation would 404 against check_files=True.
    entry = {
        "asset_id": "803_hingebox",
        "semantic_name": "hingebox",
        "category": "hingebox",
        "aliases": ["hingebox"],
        "colors": [],
        "materials": [],
        "load_type": "urdf",
        "asset_path": old_a,
        "models": [
            {
                "model_id": 700,
                "model_path": old_a,
                "metadata_path": f"{old_a}/model_data.json",
                "visual_path": old_urdf_path,
                "collision_path": old_urdf_path,
                "urdf_path": old_urdf_path,
                "scale": [1.0, 1.0, 1.0],
                "dimensions_m": [0.1, 0.1, 0.1],
                "footprint_shape": "box",
                "support_margin_m": 0.005,
                "support_spawn_clearance_m": 0.003,
                "stable_pose_id": "upright",
                "stable_orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                "z_policy": "origin_on_table",
                "is_static": False,
                "articulation_joints": [
                    {
                        "name": "j0",
                        "joint_type": "revolute",
                        "lower": -0.3,
                        "upper": 0.3,
                    }
                ],
                "articulation_closed_qpos": [0.0],
                "articulation_open_qpos": [0.3],
                "usable": True,
                "missing": [],
            }
        ],
    }
    catalog = {
        "schema_version": 1,
        "robotwin_root": old_root,
        "objects_root": old_objects,
        "source_commit": "artcafebabe",
        "entries": [entry],
    }
    catalog_path = tmp_path / "remap_articulated_catalog.json"
    catalog_path.write_text(json.dumps(catalog, indent=2))
    return catalog_path, old_objects, str(new_objects)


def test_root_remap_covers_articulated_urdf_path(tmp_path):
    catalog_path, old_prefix, new_prefix = _mini_catalog_for_remap_articulated(tmp_path)
    out = tmp_path / "out"
    r = _run(
        catalog_path,
        out,
        apply=True,
        extra_args=[f"--root-remap={old_prefix}={new_prefix}"],
    )
    assert r.returncode == 0, r.stderr

    report = json.loads((out / "backfill_upstream_report.json").read_text())
    assert report["violations"] == {}
    rr = report["notes"]["root_remap"]
    # asset_path + model_path + visual_path + collision_path + metadata_path
    # + urdf_path == 6 hits (one more than the rigid remap test's 5, because
    # urdf_path exists on this entry).
    assert rr == {"old_prefix": old_prefix, "new_prefix": new_prefix, "hits": 6}

    led = json.loads((out / "803_hingebox/ledger.json").read_text())
    rep = led["models"][0]["representations"][0]
    assert rep["backend"] == "sapien"
    assert rep["uri"].startswith(new_prefix)
    assert not rep["uri"].startswith(old_prefix)
    assert ledger.validate_ledger(led, check_files=True) == []


def test_root_remap_absent_by_default(tmp_path):
    catalog_path, _ = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    r = _run(catalog_path, out, apply=True)
    assert r.returncode == 0, r.stderr
    report = json.loads((out / "backfill_upstream_report.json").read_text())
    assert report["notes"]["root_remap"] is None
