import hashlib
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backfill_upstream.py"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import ledger  # noqa: E402


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _mini_catalog(tmp_path):
    """One rigid asset (901_widget: model 0 usable, model 1 usable:false,
    aliases empty) + one articulated asset (902_gadget: single usable
    model, non-zero model_id, one revolute joint)."""
    rt = tmp_path / "RoboTwin"
    objects = rt / "assets/objects"

    a1 = objects / "901_widget"
    vis0, col0 = a1 / "visual/base0.glb", a1 / "collision/base0.glb"
    _write(vis0, b"VISUAL-901-0")
    _write(col0, b"COLLISION-901-0")
    (a1 / "model_data0.json").parent.mkdir(parents=True, exist_ok=True)
    (a1 / "model_data0.json").write_text("{}")
    vis1, col1 = a1 / "visual/base1.glb", a1 / "collision/base1.glb"
    _write(vis1, b"VISUAL-901-1")
    _write(col1, b"COLLISION-901-1")

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
                "scale": [0.1, 0.1, 0.1],
                "dimensions_m": [0.05, 0.06, 0.12],
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
                "z_policy": "origin_on_table",
                "is_static": False,
                "articulation_joints": [],
                "articulation_closed_qpos": [],
                "articulation_open_qpos": [],
                "usable": False,
                "missing": ["stable_pose"],
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
    return catalog_path, {"vis0": vis0, "col0": col0, "urdf": urdf}


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
    assert manifest["files"]["visual/base0.glb"] == _sha(b"VISUAL-901-0")
    assert manifest["files"]["collision/base0.glb"] == _sha(b"COLLISION-901-0")

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
    assert reps["visual"]["sha256"] == _sha(b"VISUAL-901-0")
    assert reps["visual"]["size_bytes"] == len(b"VISUAL-901-0")
    assert reps["collision"]["sha256"] == _sha(b"COLLISION-901-0")
    assert reps["collision"]["size_bytes"] == len(b"COLLISION-901-0")
    assert reps["visual"]["backend"] == "sapien"


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
    catalog_path, _ = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    r1 = _run(catalog_path, out, apply=True)
    assert r1.returncode == 0, r1.stderr
    before1 = (out / "901_widget/ledger.json").read_text()
    before2 = (out / "902_gadget/ledger.json").read_text()

    r2 = _run(catalog_path, out, apply=True)
    assert r2.returncode == 0, r2.stderr
    after1 = (out / "901_widget/ledger.json").read_text()
    after2 = (out / "902_gadget/ledger.json").read_text()

    assert before1 == after1
    assert before2 == after2


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


def test_articulated_up_axis_identity_orientation_is_zup(tmp_path):
    # 902_gadget in the shared fixture carries stable_orientation_wxyz ==
    # IDENTITY -- per conventions.py's causal rule (URDF Z-up -> identity),
    # that means its raw frame is already Z-up.
    catalog_path, _ = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    r = _run(catalog_path, out, apply=True)
    assert r.returncode == 0, r.stderr
    physical = json.loads((out / "902_gadget/ledger.json").read_text())["models"][0][
        "physical"
    ]
    assert physical["mesh_up_axis"] == "Z"
    assert physical["origin_convention"] == "base-at-floor"


def _mini_catalog_x90_articulated(tmp_path):
    """A second, standalone articulated asset whose stable_orientation_wxyz
    is X90 (not IDENTITY) -- real-catalog evidence (036_cabinet) that
    articulated up-axis is NOT uniformly Z, so it must be derived per model,
    not hardcoded."""
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
    return catalog_path


def test_articulated_up_axis_x90_orientation_is_yup(tmp_path):
    catalog_path = _mini_catalog_x90_articulated(tmp_path)
    out = tmp_path / "out"
    r = _run(catalog_path, out, apply=True)
    assert r.returncode == 0, r.stderr
    led = json.loads((out / "804_hinge/ledger.json").read_text())
    physical = led["models"][0]["physical"]
    assert physical["mesh_up_axis"] == "Y"
    assert physical["origin_convention"] == "bottom-center"
    # sanity: validator still 0 violations for this shape too
    assert ledger.validate_ledger(led, check_files=True) == []


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
    _write(vis0, b"VISUAL-801-0")
    _write(col0, b"COLLISION-801-0")

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
                "scale": [0.1, 0.1, 0.1],
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


def test_root_remap_absent_by_default(tmp_path):
    catalog_path, _ = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    r = _run(catalog_path, out, apply=True)
    assert r.returncode == 0, r.stderr
    report = json.loads((out / "backfill_upstream_report.json").read_text())
    assert report["notes"]["root_remap"] is None
