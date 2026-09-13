import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest
import trimesh

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backfill_upstream.py"
USD_MANUAL_901 = b'#usda 1.0\ndef Xform "Manual901" {}\n'
USD_CONTENT_901 = b'#usda 1.0\ndef Xform "Asset901" {}\n'
USD_CONTENT_902 = b'#usda 1.0\ndef Xform "Asset902" {}\n'

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import ledger, ledger_writes  # noqa: E402
from tests.trusted_fixtures import qualified_runtime_capability  # noqa: E402


@pytest.fixture(scope="module")
def backfill_module():
    name = "asset_backfill_upstream_under_test"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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
                "stable_pose_measured_against": {
                    "backend": "sapien",
                    "run_id": "fixture-settle-901-0",
                },
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
                "stable_pose_measured_against": {
                    "backend": "sapien",
                    "run_id": "fixture-settle-901-1",
                },
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
                "stable_pose_measured_against": {
                    "backend": "sapien",
                    "run_id": "fixture-settle-902-10001",
                },
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


def _run(catalog_path, out_dir, apply=False, extra_args=(), *, seed_receipts=True):
    seeded = (
        _seed_existing_pose_receipts(catalog_path, out_dir, extra_args=extra_args)
        if seed_receipts
        else {}
    )
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
    result = subprocess.run(cmd, capture_output=True, text=True)
    for path, original in seeded.items():
        if path.is_file() and path.read_text() == original:
            path.unlink()
            try:
                path.parent.rmdir()
            except OSError:
                pass
    return result


def _seed_existing_pose_receipts(catalog_path, out_dir, *, extra_args=()):
    """Give success-path fixtures an actual prior settle receipt.

    Production no longer trusts the catalog's run_id.  These tests still use
    that convenient field as fixture input, but turn it into the only shape
    production accepts: an existing ledger pose plus settle/pass receipt bound
    to the current representation-set digest.
    """
    spec = importlib.util.spec_from_file_location("backfill_seed_helper", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    catalog = json.loads(Path(catalog_path).read_text())
    remap_arg = next(
        (
            value.removeprefix("--root-remap=")
            for value in extra_args
            if value.startswith("--root-remap=")
        ),
        None,
    )
    if remap_arg is not None:
        catalog, _ = module._apply_root_remap(catalog, module._parse_root_remap(remap_arg))
    seeded = {}
    report = module._empty_report()
    for entry in catalog["entries"]:
        kind = "articulated" if entry.get("load_type") == "urdf" else "rigid"
        resolved = module._resolve_models(entry, kind, report)
        models = []
        for model, up_axis, origin, _bbox, _scale in resolved:
            provenance = model.get("stable_pose_measured_against")
            if not isinstance(provenance, dict):
                continue
            representations = (
                module._articulated_representations(model)
                if kind == "articulated"
                else module._rigid_representations(model)
            )
            for representation in representations:
                representation["frame"] = {"up_axis": up_axis}
                representation["geometry_state"] = {
                    "origin": origin,
                    "scale_baked": False,
                }
            digest = ledger.reps_digest({"representations": representations}, "sapien")
            path = Path(out_dir) / entry["asset_id"] / "ledger.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            model_entry = {
                "model_id": model["model_id"],
                "physical": {
                    "conventions": {
                        "is_static": model["is_static"],
                        "z_policy": model["z_policy"],
                        "stable_poses": [
                            {
                                "pose_id": model["stable_pose_id"],
                                "orientation_wxyz": model["stable_orientation_wxyz"],
                                "is_default": True,
                                "measured_against": provenance,
                            }
                        ],
                    }
                },
                "representations": representations,
            }
            try:
                snapshot = ledger_writes.publish_execution_snapshot(
                    path.parent / "execution_snapshots",
                    source_asset_dir=entry["asset_path"],
                    asset_key=entry["asset_id"],
                    model_entry=model_entry,
                )
                capability = qualified_runtime_capability(
                    path.parent / "runtime",
                    "asset.settle_repair.v1",
                    ledger,
                    ledger_writes,
                )
                payload = ledger_writes.issue_qualified_verification(
                    issuer="asset.settle_repair.v1",
                    asset_key=entry["asset_id"],
                    model_id=model["model_id"],
                    run_id=provenance["run_id"],
                    timestamp="2026-08-31T00:00:00",
                    reps_digest=digest,
                    inputs={
                        "catalog": ledger_writes.provenance_file_record(catalog_path),
                        "task": {
                            "asset_key": entry["asset_id"],
                            "model_id": model["model_id"],
                        },
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
                        "support_z_m": 0.01,
                        "tilt_deg": 0.0,
                        "rest_orientation_wxyz": model["stable_orientation_wxyz"],
                        "origin_z_m": 0.0,
                        "derived_z_policy": "origin_on_table",
                        "details": {"fixture": "backfill-settle-v1"},
                    },
                    model_entry=model_entry,
                    execution_snapshot=snapshot,
                    runtime_capability=capability,
                )
            except ledger_writes.VerificationEvidenceError:
                # Malformed catalog attack cases must reach the production
                # parser without this positive-path fixture fabricating a
                # receipt for an asset path it cannot snapshot safely.
                continue
            artifact = ledger_writes.publish_verification_evidence(
                path.parent / "verification_evidence", payload
            )
            models.append(
                {
                    "model_id": model["model_id"],
                    "physical": model_entry["physical"],
                    "representations": representations,
                    "verification": [ledger_writes.receipt_from_evidence(payload, artifact)],
                }
            )
        if not models:
            continue
        path = Path(out_dir) / entry["asset_id"] / "ledger.json"
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        original = (
            json.dumps({"external_ids": {"env_gen": entry["asset_id"]}, "models": models}, indent=2)
            + "\n"
        )
        path.write_text(original)
        seeded[path] = original
    return seeded


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


def test_backfill_writes_complete_v3_identity_and_no_deleted_fields(tmp_path):
    catalog_path, _ = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    result = _run(catalog_path, out, apply=True)
    assert result.returncode == 0, result.stderr

    document = json.loads((out / "901_widget/ledger.json").read_text())
    assert document["external_ids"] == {"env_gen": "901_widget"}
    assert "semantic_name" not in document
    assert "tags" not in document
    for model in document["models"]:
        physical = model["physical"]
        assert "mesh_up_axis" not in physical
        assert "origin_convention" not in physical
        assert not any(
            field.startswith("runtime_default")
            for envelope in (physical["mass_kg"], physical["friction"])
            for field in envelope
        )


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
            "measured_against": {
                "backend": "sapien",
                "run_id": "fixture-settle-901-0",
            },
        }
    ]


def test_missing_pose_measurement_is_reported_not_fabricated(tmp_path):
    catalog_path, _ = _mini_catalog(tmp_path)
    catalog = json.loads(catalog_path.read_text())
    del catalog["entries"][0]["models"][0]["stable_pose_measured_against"]
    catalog_path.write_text(json.dumps(catalog, indent=2))
    out = tmp_path / "out"

    result = _run(catalog_path, out, apply=True)

    assert result.returncode == 1
    report = json.loads((out / "backfill_upstream_report.json").read_text())
    assert "901_widget" not in report["written"]
    assert any(
        violation["code"] == "measured_against_required"
        for violation in report["violations"]["901_widget"]
    )
    assert not (out / "901_widget/ledger.json").exists()


def test_rerun_refuses_legacy_pose_receipt_without_trusted_evidence(tmp_path):
    catalog_path, _ = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    first = _run(catalog_path, out, apply=True)
    assert first.returncode == 0, first.stderr
    ledger_path = out / "901_widget/ledger.json"
    document = json.loads(ledger_path.read_text())
    pose = document["models"][0]["physical"]["conventions"]["stable_poses"][0]
    pose["measured_against"] = {
        "backend": "sapien",
        "run_id": "real-settle-receipt-42",
        "note": "900-step replay",
    }
    document["models"][0]["verification"] = [
        {
            "backend": "sapien",
            "check": "settle",
            "verdict": "pass",
            "run_id": "real-settle-receipt-42",
            "timestamp": "2026-08-31T00:00:00",
            "verified_digest": ledger.reps_digest(document["models"][0], "sapien"),
        }
    ]
    ledger_path.write_text(json.dumps(document, indent=2) + "\n")
    before = ledger_path.read_bytes()

    second = _run(catalog_path, out, apply=True)

    assert second.returncode != 0
    assert ledger_path.read_bytes() == before
    report = json.loads((out / "backfill_upstream_report.json").read_text())
    assert report["violations"]["901_widget"][0]["code"] == "measured_against_required"


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
    src_manifest_sha256 = led["models"][0]["source"]["source_manifest_sha256"]
    assert Path(src_manifest_path).exists()
    assert Path(src_manifest_path).read_text() == manifest_path.read_text()
    assert src_manifest_sha256 == hashlib.sha256(manifest_path.read_bytes()).hexdigest()


def test_manifest_and_ledger_commit_rolls_back_both_on_ledger_failure(
    tmp_path, monkeypatch, backfill_module
):
    catalog_path, _files = _mini_catalog(tmp_path)
    catalog = json.loads(catalog_path.read_text())
    catalog["entries"] = catalog["entries"][:1]
    catalog_path.write_text(json.dumps(catalog, indent=2))
    out = tmp_path / "out"
    result = _run(catalog_path, out, apply=True)
    assert result.returncode == 0, result.stderr

    manifest_path = out / "901_widget/SOURCE_MANIFEST.json"
    ledger_path = out / "901_widget/ledger.json"
    prior_manifest = manifest_path.read_bytes()
    prior_ledger = ledger_path.read_bytes()
    expected = json.loads(prior_ledger)
    next_manifest = b'{"writer":"ordinary-failure"}\n'
    candidate = json.loads(json.dumps(expected))
    candidate["semantics"]["aliases"] = ["ordinary-failure"]
    for model in candidate["models"]:
        model["source"]["source_manifest_sha256"] = hashlib.sha256(next_manifest).hexdigest()

    real_atomic_json = backfill_module.ledger._atomic_write_json

    def fail_candidate_write(path, document, **kwargs):
        if document == candidate:
            raise OSError("injected ledger failure")
        return real_atomic_json(path, document, **kwargs)

    monkeypatch.setattr(backfill_module.ledger, "_atomic_write_json", fail_candidate_write)

    with pytest.raises(OSError, match="injected ledger failure"):
        backfill_module._commit_manifest_and_ledger(
            manifest_path,
            next_manifest,
            ledger_path,
            candidate,
            expected=expected,
        )

    assert manifest_path.read_bytes() == prior_manifest
    assert ledger_path.read_bytes() == prior_ledger
    assert not (manifest_path.parent / backfill_module._PAIR_TRANSACTION_JOURNAL_NAME).exists()


def test_manifest_commit_preserves_receipt_waiting_on_the_pair_transaction(
    tmp_path, monkeypatch, backfill_module
):
    catalog_path, _files = _mini_catalog(tmp_path)
    catalog = json.loads(catalog_path.read_text())
    catalog["entries"] = catalog["entries"][:1]
    catalog_path.write_text(json.dumps(catalog, indent=2))
    out = tmp_path / "out"
    result = _run(catalog_path, out, apply=True)
    assert result.returncode == 0, result.stderr

    manifest_path = out / "901_widget/SOURCE_MANIFEST.json"
    ledger_path = out / "901_widget/ledger.json"
    expected = json.loads(ledger_path.read_text())
    next_manifest = b'{"writer":"pair-before-receipt"}\n'
    candidate = json.loads(json.dumps(expected))
    for model in candidate["models"]:
        model["source"]["source_manifest_sha256"] = hashlib.sha256(next_manifest).hexdigest()
    receipt = {
        "backend": "sapien",
        "check": "runtime_load",
        "verdict": "pass",
        "run_id": "concurrent-runtime-check",
        "timestamp": "2026-08-31T12:00:00",
        "verified_digest": ledger.reps_digest(expected["models"][0], "sapien"),
    }
    real_atomic_write = backfill_module._atomic_write_bytes
    append_attempted = threading.Event()
    append_finished = threading.Event()
    worker = None

    def append_receipt():
        append_attempted.set()
        try:
            ledger.append_verification(ledger_path, 0, receipt)
        finally:
            append_finished.set()

    def start_receipt_waiter_after_manifest(path, payload, *, locked=None):
        nonlocal worker
        real_atomic_write(path, payload, locked=locked)
        if Path(path) == manifest_path and payload == next_manifest and worker is None:
            worker = threading.Thread(target=append_receipt)
            worker.start()
            assert append_attempted.wait(timeout=5)

    monkeypatch.setattr(
        backfill_module,
        "_atomic_write_bytes",
        start_receipt_waiter_after_manifest,
    )

    backfill_module._commit_manifest_and_ledger(
        manifest_path,
        next_manifest,
        ledger_path,
        candidate,
        expected=expected,
    )
    assert worker is not None
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert append_finished.is_set()

    current = json.loads(ledger_path.read_text())
    assert current["models"][0]["verification"][-1] == receipt
    assert manifest_path.read_bytes() == next_manifest


def test_manifest_and_ledger_commit_keeps_the_same_concurrent_winner(
    tmp_path, monkeypatch, backfill_module
):
    catalog_path, _files = _mini_catalog(tmp_path)
    catalog = json.loads(catalog_path.read_text())
    catalog["entries"] = catalog["entries"][:1]
    catalog_path.write_text(json.dumps(catalog, indent=2))
    out = tmp_path / "out"
    result = _run(catalog_path, out, apply=True)
    assert result.returncode == 0, result.stderr

    manifest_path = out / "901_widget/SOURCE_MANIFEST.json"
    ledger_path = out / "901_widget/ledger.json"
    assert manifest_path.is_file()
    assert ledger_path.is_file()
    expected = json.loads(ledger_path.read_text())
    original_manifest = json.loads(manifest_path.read_text())

    manifests = {}
    candidates = {}
    for writer in ("outer", "concurrent"):
        manifest = {**original_manifest, "writer": writer}
        payload = (json.dumps(manifest, indent=2) + "\n").encode()
        candidate = json.loads(json.dumps(expected))
        candidate["semantics"]["aliases"] = [writer]
        for model in candidate["models"]:
            model["source"]["source_manifest_sha256"] = hashlib.sha256(payload).hexdigest()
        manifests[writer] = payload
        candidates[writer] = candidate

    outer_ident = threading.get_ident()
    outer_holds_lock = threading.Event()
    concurrent_attempted_lock = threading.Event()
    concurrent_holds_lock = threading.Event()
    concurrent_finished = threading.Event()
    outcomes = {}
    worker = None
    real_locked_ledger = backfill_module.ledger._locked_ledger
    real_atomic_write = backfill_module._atomic_write_bytes

    @contextmanager
    def observe_real_lock(path):
        is_outer = threading.get_ident() == outer_ident
        if not is_outer:
            concurrent_attempted_lock.set()
        with real_locked_ledger(path) as locked:
            if is_outer:
                outer_holds_lock.set()
            else:
                concurrent_holds_lock.set()
            yield locked

    monkeypatch.setattr(backfill_module.ledger, "_locked_ledger", observe_real_lock)

    def commit(writer):
        try:
            backfill_module._commit_manifest_and_ledger(
                manifest_path,
                manifests[writer],
                ledger_path,
                candidates[writer],
                expected=expected,
            )
        except BaseException as exc:  # assertions below verify the typed losing outcome
            outcomes[writer] = exc
        else:
            outcomes[writer] = None
        finally:
            if writer == "concurrent":
                concurrent_finished.set()

    def start_competitor_after_outer_manifest(path, payload, *, locked=None):
        nonlocal worker
        real_atomic_write(path, payload, locked=locked)
        if (
            threading.get_ident() != outer_ident
            or Path(path) != manifest_path
            or payload != manifests["outer"]
            or worker is not None
        ):
            return
        worker = threading.Thread(target=commit, args=("concurrent",))
        worker.start()
        assert concurrent_attempted_lock.wait(timeout=5)
        if not outer_holds_lock.is_set():
            assert concurrent_holds_lock.wait(timeout=5)
            assert concurrent_finished.wait(timeout=5)

    monkeypatch.setattr(
        backfill_module,
        "_atomic_write_bytes",
        start_competitor_after_outer_manifest,
    )

    commit("outer")
    assert worker is not None
    worker.join(timeout=5)
    assert not worker.is_alive()

    failures = [outcome for outcome in outcomes.values() if outcome is not None]
    assert len(failures) == 1
    assert isinstance(failures[0], ledger_writes.ConcurrentLedgerUpdateError)

    published_manifest = json.loads(manifest_path.read_text())
    winner = published_manifest.get("writer")
    assert winner in candidates
    published_ledger = json.loads(ledger_path.read_text())
    assert published_ledger == candidates[winner]
    assert (
        published_ledger["models"][0]["source"]["source_manifest_sha256"]
        == hashlib.sha256(manifests[winner]).hexdigest()
    )


def test_manifest_pair_commit_never_reopens_a_replaced_asset_directory(
    tmp_path, monkeypatch, backfill_module
):
    catalog_path, _files = _mini_catalog(tmp_path)
    catalog = json.loads(catalog_path.read_text())
    catalog["entries"] = catalog["entries"][:1]
    catalog_path.write_text(json.dumps(catalog, indent=2))
    out = tmp_path / "out"
    result = _run(catalog_path, out, apply=True)
    assert result.returncode == 0, result.stderr

    asset_dir = out / "901_widget"
    manifest_path = asset_dir / "SOURCE_MANIFEST.json"
    ledger_path = asset_dir / "ledger.json"
    expected = json.loads(ledger_path.read_text())
    next_manifest = b'{"writer":"pinned"}\n'
    candidate = json.loads(json.dumps(expected))
    candidate["semantics"]["aliases"] = ["pinned"]
    for model in candidate["models"]:
        model["source"]["source_manifest_sha256"] = hashlib.sha256(next_manifest).hexdigest()

    detached = out / "901_widget-detached"
    replacement_manifest = b'{"writer":"replacement"}\n'
    replacement_ledger = (json.dumps(expected, indent=2) + "\n").encode()
    real_locked_ledger = backfill_module.ledger._locked_ledger
    replaced = False

    @contextmanager
    def replace_directory_after_lock(path):
        nonlocal replaced
        with real_locked_ledger(path) as locked:
            if not replaced and Path(path) == manifest_path:
                replaced = True
                os.rename(asset_dir, detached)
                asset_dir.mkdir()
                manifest_path.write_bytes(replacement_manifest)
                ledger_path.write_bytes(replacement_ledger)
            yield locked

    monkeypatch.setattr(backfill_module.ledger, "_locked_ledger", replace_directory_after_lock)

    with pytest.raises(OSError, match="asset directory changed"):
        backfill_module._commit_manifest_and_ledger(
            manifest_path,
            next_manifest,
            ledger_path,
            candidate,
            expected=expected,
        )

    assert manifest_path.read_bytes() == replacement_manifest
    assert ledger_path.read_bytes() == replacement_ledger
    assert (detached / "SOURCE_MANIFEST.json").read_bytes() != next_manifest
    assert json.loads((detached / "ledger.json").read_text()) == expected


def test_manifest_pair_commit_recovers_a_crash_before_publishing_the_ledger(
    tmp_path, monkeypatch, backfill_module
):
    catalog_path, _files = _mini_catalog(tmp_path)
    catalog = json.loads(catalog_path.read_text())
    catalog["entries"] = catalog["entries"][:1]
    catalog_path.write_text(json.dumps(catalog, indent=2))
    out = tmp_path / "out"
    result = _run(catalog_path, out, apply=True)
    assert result.returncode == 0, result.stderr

    asset_dir = out / "901_widget"
    manifest_path = asset_dir / "SOURCE_MANIFEST.json"
    ledger_path = asset_dir / "ledger.json"
    expected = json.loads(ledger_path.read_text())
    next_manifest = b'{"writer":"after-recovery"}\n'
    candidate = json.loads(json.dumps(expected))
    candidate["semantics"]["aliases"] = ["after-recovery"]
    for model in candidate["models"]:
        model["source"]["source_manifest_sha256"] = hashlib.sha256(next_manifest).hexdigest()

    real_atomic_write = backfill_module._atomic_write_bytes

    def crash_after_manifest_replace(path, payload, *, locked=None):
        real_atomic_write(path, payload, locked=locked)
        if Path(path) == manifest_path and payload == next_manifest:
            raise SystemExit("simulated process death after manifest replace")

    monkeypatch.setattr(backfill_module, "_atomic_write_bytes", crash_after_manifest_replace)

    with pytest.raises(SystemExit, match="simulated process death"):
        backfill_module._commit_manifest_and_ledger(
            manifest_path,
            next_manifest,
            ledger_path,
            candidate,
            expected=expected,
        )

    journal_path = asset_dir / backfill_module._PAIR_TRANSACTION_JOURNAL_NAME
    assert journal_path.is_file()
    with pytest.raises(ledger.LedgerIdentityError):
        ledger.load_asset_ledger(out, "901_widget")

    monkeypatch.setattr(backfill_module, "_atomic_write_bytes", real_atomic_write)
    backfill_module._commit_manifest_and_ledger(
        manifest_path,
        next_manifest,
        ledger_path,
        candidate,
        expected=expected,
    )

    assert not journal_path.exists()
    assert manifest_path.read_bytes() == next_manifest
    assert json.loads(ledger_path.read_text()) == candidate
    assert ledger.validate_ledger(candidate, check_files=True) == []


def test_manifest_replace_is_parent_fsynced_before_the_ledger_is_published(
    tmp_path, monkeypatch, backfill_module
):
    catalog_path, _files = _mini_catalog(tmp_path)
    catalog = json.loads(catalog_path.read_text())
    catalog["entries"] = catalog["entries"][:1]
    catalog_path.write_text(json.dumps(catalog, indent=2))
    out = tmp_path / "out"
    result = _run(catalog_path, out, apply=True)
    assert result.returncode == 0, result.stderr

    manifest_path = out / "901_widget/SOURCE_MANIFEST.json"
    ledger_path = out / "901_widget/ledger.json"
    expected = json.loads(ledger_path.read_text())
    next_manifest = b'{"writer":"durable"}\n'
    candidate = json.loads(json.dumps(expected))
    candidate["semantics"]["aliases"] = ["durable"]
    for model in candidate["models"]:
        model["source"]["source_manifest_sha256"] = hashlib.sha256(next_manifest).hexdigest()

    real_replace = backfill_module.os.replace
    real_fsync = backfill_module.os.fsync
    real_atomic_json = backfill_module.ledger._atomic_write_json
    awaiting_parent_sync = False
    saw_manifest_replace = False
    saw_manifest_parent_sync = False

    def observe_replace(source, destination, *args, **kwargs):
        nonlocal awaiting_parent_sync, saw_manifest_replace
        result = real_replace(source, destination, *args, **kwargs)
        if destination == manifest_path.name:
            saw_manifest_replace = True
            awaiting_parent_sync = True
        return result

    def observe_fsync(fd):
        nonlocal awaiting_parent_sync, saw_manifest_parent_sync
        if awaiting_parent_sync and stat.S_ISDIR(os.fstat(fd).st_mode):
            awaiting_parent_sync = False
            saw_manifest_parent_sync = True
        return real_fsync(fd)

    def require_manifest_durability_before_ledger(path, document, **kwargs):
        if document == candidate:
            assert not awaiting_parent_sync
            assert saw_manifest_parent_sync
        return real_atomic_json(path, document, **kwargs)

    monkeypatch.setattr(backfill_module.os, "replace", observe_replace)
    monkeypatch.setattr(backfill_module.os, "fsync", observe_fsync)
    monkeypatch.setattr(
        backfill_module.ledger,
        "_atomic_write_json",
        require_manifest_durability_before_ledger,
    )

    backfill_module._commit_manifest_and_ledger(
        manifest_path,
        next_manifest,
        ledger_path,
        candidate,
        expected=expected,
    )

    assert saw_manifest_replace
    assert saw_manifest_parent_sync


def test_representation_sha256_and_size_are_real(tmp_path):
    catalog_path, files = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    r = _run(catalog_path, out, apply=True)
    assert r.returncode == 0, r.stderr
    led = json.loads((out / "901_widget/ledger.json").read_text())
    reps = {rep["role"]: rep for rep in led["models"][0]["representations"]}
    assert reps["visual"]["sha256"] == files["vis0_sha"]
    assert reps["collision"]["sha256"] == files["col0_sha"]
    assert reps["visual"]["files"] == [
        {
            "uri": ledger.to_portable_uri(files["vis0"]),
            "sha256": files["vis0_sha"],
            "bytes": files["vis0"].stat().st_size,
        }
    ]
    assert reps["collision"]["files"] == [
        {
            "uri": ledger.to_portable_uri(files["col0"]),
            "sha256": files["col0_sha"],
            "bytes": files["col0"].stat().st_size,
        }
    ]
    assert reps["collision"]["collision_meta"] == {
        "mode": "explicit_mesh",
    }
    assert all("size_bytes" not in representation for representation in reps.values())
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
    assert sapien_reps[0]["collision_meta"]["mode"] == "unknown"
    assert sapien_reps[0]["collision_meta"]["unknown_reason"]
    assert {Path(item["uri"]).name for item in sapien_reps[0]["files"]} == {"mobility.urdf"}


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
    _write(fake_usd, USD_MANUAL_901)
    fake_isaac_rep = {
        "format": "usd",
        "uri": str(fake_usd),
        "backend": "isaacsim",
        "role": "visual_and_collision",
        "sha256": _sha(USD_MANUAL_901),
        "size_bytes": len(USD_MANUAL_901),
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
    model0["physical"]["conventions"]["stable_poses"][0]["measured_against"] = {
        "backend": "sapien",
        "run_id": "manual_test_run",
    }
    digest = ledger.reps_digest(model0, "sapien")
    snapshot = ledger_writes.publish_execution_snapshot(
        lp.parent / "execution_snapshots",
        source_asset_dir=json.loads(catalog_path.read_text())["entries"][0]["asset_path"],
        asset_key="901_widget",
        model_entry=model0,
    )
    capability = qualified_runtime_capability(
        lp.parent / "runtime",
        "asset.settle_repair.v1",
        ledger,
        ledger_writes,
    )
    payload = ledger_writes.issue_qualified_verification(
        issuer="asset.settle_repair.v1",
        asset_key="901_widget",
        model_id=model0["model_id"],
        run_id="manual_test_run",
        timestamp="2026-08-08T10:00:00",
        reps_digest=digest,
        inputs={
            "fixture": ledger_writes.provenance_file_record(__file__),
            "task": {"asset_key": "901_widget", "model_id": 0},
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
            "support_z_m": 0.01,
            "tilt_deg": 0.0,
            "rest_orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
            "origin_z_m": 0.0,
            "derived_z_policy": "origin_on_table",
            "details": {"fixture": "incremental-settle-v1"},
        },
        model_entry=model0,
        execution_snapshot=snapshot,
        runtime_capability=capability,
    )
    artifact = ledger_writes.publish_verification_evidence(
        lp.parent / "verification_evidence", payload
    )
    model0["verification"] = [ledger_writes.receipt_from_evidence(payload, artifact)]
    lp.write_text(json.dumps(led, indent=2))

    r2 = _run(catalog_path, out, apply=True)
    assert r2.returncode == 0, r2.stderr

    led2 = json.loads(lp.read_text())
    model0_2 = led2["models"][0]
    isaac_reps = [rp for rp in model0_2["representations"] if rp["backend"] == "isaacsim"]
    assert len(isaac_reps) == 1
    assert isaac_reps[0]["sha256"] == _sha(USD_MANUAL_901)
    assert model0_2["source"]["license"]["status"] == "declared"
    assert model0_2["source"]["license"]["spdx"] == "MIT"
    assert len(model0_2["verification"]) == 1
    assert model0_2["verification"][0]["run_id"] == "manual_test_run"

    # derived core still refreshed: sapien reps still present & correct
    sapien_reps = [rp for rp in model0_2["representations"] if rp["backend"] == "sapien"]
    assert len(sapien_reps) == 2


def test_isaac_usd_registration(tmp_path):
    catalog_path, _ = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    usd_path = tmp_path / "901_widget.usd"
    usd_path.write_bytes(USD_CONTENT_901)

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
    assert rep["sha256"] == _sha(USD_CONTENT_901)
    assert rep["files"] == [
        {
            "uri": ledger.to_portable_uri(usd_path),
            "sha256": _sha(USD_CONTENT_901),
            "bytes": len(USD_CONTENT_901),
        }
    ]
    assert rep["collision_meta"]["mode"] == "unknown"
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
    isaac_reps2 = [rp for rp in led2["models"][0]["representations"] if rp["backend"] == "isaacsim"]
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
    usd_path.write_bytes(USD_CONTENT_902)

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
    assert rep["sha256"] == _sha(USD_CONTENT_902)
    assert rep["files"] == [
        {
            "uri": ledger.to_portable_uri(usd_path),
            "sha256": _sha(USD_CONTENT_902),
            "bytes": len(USD_CONTENT_902),
        }
    ]
    assert rep["collision_meta"]["mode"] == "unknown"
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
                "stable_pose_measured_against": {
                    "backend": "sapien",
                    "run_id": "fixture-settle-801-0",
                },
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
                "stable_pose_measured_against": {
                    "backend": "sapien",
                    "run_id": "fixture-settle-804-500",
                },
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
    assert physical["conventions"]["stable_poses"][0]["orientation_wxyz"] == list(ledger.X90_WXYZ)
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
                "stable_pose_measured_against": {
                    "backend": "sapien",
                    "run_id": "fixture-settle-900-0",
                },
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
                "stable_pose_measured_against": {
                    "backend": "sapien",
                    "run_id": "fixture-settle-802-0",
                },
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
                "stable_pose_measured_against": {
                    "backend": "sapien",
                    "run_id": "fixture-settle-803-7",
                },
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


def test_helper_fail_closed_edges_are_typed_and_deterministic(tmp_path, backfill_module):
    report = backfill_module._empty_report()
    missing_mesh = tmp_path / "missing.glb"
    assert backfill_module._measure_rigid_geometry(missing_mesh, report, "901_widget:m0") is None
    assert report["notes"]["up_axis_ambiguous"] == ["901_widget:m0"]

    empty = tmp_path / "empty"
    empty.mkdir()
    assert backfill_module._latest_file_mtime_date(empty) is None

    outside = tmp_path / "outside" / "asset.obj"
    sidecar = outside.parent / "payload.mtl"
    _write(outside, b"mtllib payload.mtl\nv 0 0 0\n")
    _write(sidecar, b"newmtl fixture\n")
    assert backfill_module._relative_to_root(outside, empty) == ledger.to_portable_uri(outside)

    assert backfill_module._derive_scale_applied([], report, "empty-scale") is None
    assert backfill_module._derive_scale_applied([1.0, 2.0, 1.0], report, "non-uniform") == 1.0
    assert report["notes"]["non_uniform_scale"] == ["non-uniform"]

    records = backfill_module._representation_files(outside)
    assert [record["uri"] for record in records] == sorted(
        [ledger.to_portable_uri(outside), ledger.to_portable_uri(sidecar)]
    )

    assert backfill_module._existing_model(None, 0) is None
    assert (
        backfill_module._existing_model({"models": [{"model_id": 1}, {"model_id": 2}]}, 3) is None
    )


def test_catalog_pose_run_id_is_not_measurement_evidence(backfill_module):
    model = {
        "stable_pose_id": "upright",
        "stable_orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
        "stable_pose_measured_against": {"backend": "sapien", "run_id": "catalog-claim"},
    }

    pose = backfill_module._stable_poses(model, None, [], "901_widget")

    assert "measured_against" not in pose[0]


def test_pose_measurement_helper_only_reuses_matching_trusted_pose(tmp_path, backfill_module):
    model = {
        "stable_pose_id": "upright",
        "stable_orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
    }
    provenance = {"backend": "sapien", "run_id": "real-settle-42"}
    primary = tmp_path / "asset" / "model.stl"
    _write(primary, b"model")
    representations = [
        {
            "format": "stl",
            "uri": str(primary),
            "backend": "sapien",
            "role": "visual",
            "sha256": _sha(b"model"),
            "files": [{"uri": str(primary), "sha256": _sha(b"model"), "bytes": 5}],
        }
    ]
    digest = ledger.reps_digest({"representations": representations}, "sapien")
    existing = {
        "model_id": 0,
        "representations": representations,
        "physical": {
            "conventions": {
                "z_policy": "origin_on_table",
                "stable_poses": [
                    "opaque",
                    {
                        "pose_id": "flat",
                        "orientation_wxyz": model["stable_orientation_wxyz"],
                        "measured_against": provenance,
                    },
                    {
                        "pose_id": "upright",
                        "orientation_wxyz": [0.0, 1.0, 0.0, 0.0],
                        "measured_against": provenance,
                    },
                    {
                        "pose_id": "upright",
                        "orientation_wxyz": model["stable_orientation_wxyz"],
                        "measured_against": {
                            "backend": "sapien",
                            "run_id": "upstream-catalog-old",
                        },
                    },
                    {
                        "pose_id": "upright",
                        "orientation_wxyz": model["stable_orientation_wxyz"],
                        "measured_against": None,
                    },
                    {
                        "pose_id": "upright",
                        "orientation_wxyz": model["stable_orientation_wxyz"],
                        "is_default": True,
                        "measured_against": provenance,
                    },
                ],
            }
        },
        "verification": [],
    }
    snapshot = ledger_writes.publish_execution_snapshot(
        tmp_path / "execution-snapshots",
        source_asset_dir=primary.parent,
        asset_key="901_widget",
        model_entry=existing,
    )
    capability = qualified_runtime_capability(
        tmp_path / "runtime",
        "asset.settle_repair.v1",
        ledger,
        ledger_writes,
    )
    payload = ledger_writes.issue_qualified_verification(
        issuer="asset.settle_repair.v1",
        asset_key="901_widget",
        model_id=0,
        run_id="real-settle-42",
        timestamp="2026-08-31T00:00:00",
        reps_digest=digest,
        inputs={
            "fixture": ledger_writes.provenance_file_record(__file__),
            "task": {"asset_key": "901_widget", "model_id": 0},
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
            "support_z_m": 0.01,
            "tilt_deg": 0.0,
            "rest_orientation_wxyz": model["stable_orientation_wxyz"],
            "origin_z_m": 0.0,
            "derived_z_policy": "origin_on_table",
            "details": {"fixture": "pose-reuse-settle-v1"},
        },
        model_entry=existing,
        execution_snapshot=snapshot,
        runtime_capability=capability,
    )
    artifact = ledger_writes.publish_verification_evidence(tmp_path / "evidence", payload)
    existing["verification"] = [ledger_writes.receipt_from_evidence(payload, artifact)]

    reused = backfill_module._stable_poses(model, existing, representations, "901_widget")

    assert reused[0]["measured_against"] == provenance
    provenance["run_id"] = "mutated-after-call"
    assert reused[0]["measured_against"]["run_id"] == "real-settle-42"
    assert (
        backfill_module._existing_pose_measurement(
            model,
            {**existing, "verification": {}},
            representations,
            "901_widget",
        )
        is None
    )
    assert (
        backfill_module._existing_pose_measurement(
            model,
            {**existing, "verification": [{}]},
            representations,
            "901_widget",
        )
        is None
    )


def test_preserved_representation_sorting_never_invents_missing_files(tmp_path, backfill_module):
    primary = tmp_path / "primary.usd"
    primary.write_bytes(b"primary")
    primary_sha = _sha(b"primary")
    representation = {
        "uri": str(primary),
        "sha256": primary_sha,
        "role": "visual_and_collision",
        "files": [
            {"uri": "z.usd", "sha256": "a" * 64, "bytes": 1},
            {"uri": "a.usd", "sha256": "b" * 64, "bytes": 1},
        ],
        "collision_meta": {"mode": "explicit_mesh", "convex": False},
    }

    normalized = backfill_module._normalize_preserved_representation(representation)

    assert [member["uri"] for member in normalized["files"]] == ["a.usd", "z.usd"]
    assert normalized["collision_meta"] == representation["collision_meta"]

    wrong_digest = {
        "uri": str(primary),
        "sha256": "0" * 64,
        "role": "visual",
    }
    normalized_wrong = backfill_module._normalize_preserved_representation(wrong_digest)
    assert "files" not in normalized_wrong
    assert "collision_meta" not in normalized_wrong

    missing = {
        "uri": str(tmp_path / "missing.usd"),
        "sha256": "0" * 64,
        "role": "collision",
    }
    normalized_missing = backfill_module._normalize_preserved_representation(missing)
    assert "files" not in normalized_missing
    assert normalized_missing["collision_meta"]["mode"] == "unknown"


@pytest.mark.parametrize("raw", ["asset", "=path", "asset="])
def test_invalid_cli_mapping_syntax_is_rejected(backfill_module, raw):
    with pytest.raises(ValueError):
        backfill_module._parse_isaac_usd([raw])
    with pytest.raises(ValueError):
        backfill_module._parse_root_remap(raw)


def test_root_remap_ignores_nonmatching_and_non_string_fields(backfill_module):
    catalog = {
        "entries": [
            {
                "asset_path": 7,
                "models": [
                    {
                        "model_path": "/other/model",
                        "visual_path": None,
                    }
                ],
            },
            {"asset_path": "/old/asset", "models": []},
        ]
    }

    remapped, hits = backfill_module._apply_root_remap(catalog, ("/old", "/new"))

    assert hits == 1
    assert remapped["entries"][1]["asset_path"] == "/new/asset"
    assert catalog["entries"][1]["asset_path"] == "/old/asset"


@pytest.mark.parametrize(
    "attack",
    [
        "catalog_shape",
        "entries_shape",
        "entry_shape",
        "unsafe_asset",
        "duplicate_asset",
        "models_shape",
        "model_shape",
        "asset_escape",
        "asset_symlink",
        "model_escape",
    ],
)
def test_catalog_index_helper_rejects_malformed_or_escaping_content(
    tmp_path, backfill_module, attack
):
    objects = tmp_path / "objects"
    asset = objects / "901_widget"
    asset.mkdir(parents=True)
    catalog = {
        "objects_root": str(objects),
        "entries": [
            {
                "asset_id": "901_widget",
                "asset_path": str(asset),
                "models": [{"model_id": 0, "model_path": str(asset)}],
            }
        ],
    }
    if attack == "catalog_shape":
        catalog = []
    elif attack == "entries_shape":
        catalog["entries"] = {}
    elif attack == "entry_shape":
        catalog["entries"] = ["bad"]
    elif attack == "unsafe_asset":
        catalog["entries"][0]["asset_id"] = "../escape"
    elif attack == "duplicate_asset":
        catalog["entries"].append(json.loads(json.dumps(catalog["entries"][0])))
    elif attack == "models_shape":
        catalog["entries"][0]["models"] = {}
    elif attack == "model_shape":
        catalog["entries"][0]["models"] = ["bad"]
    elif attack == "asset_escape":
        catalog["entries"][0]["asset_path"] = str(tmp_path / "outside")
    elif attack == "asset_symlink":
        relocated = objects / "nested" / "901_widget"
        relocated.parent.mkdir()
        asset.rename(relocated)
        asset.symlink_to(relocated, target_is_directory=True)
    elif attack == "model_escape":
        catalog["entries"][0]["models"][0]["model_path"] = str(tmp_path / "outside")

    with pytest.raises(ValueError):
        backfill_module._entries_by_asset(catalog)


def test_catalog_index_helper_accepts_contained_safe_asset(tmp_path, backfill_module):
    objects = tmp_path / "objects"
    asset = objects / "901_widget"
    asset.mkdir(parents=True)
    entry = {
        "asset_id": "901_widget",
        "asset_path": str(asset),
        "models": [{"model_id": 0, "model_path": str(asset)}],
    }

    assert backfill_module._entries_by_asset(
        {"objects_root": str(objects), "entries": [entry]}
    ) == {"901_widget": entry}


def test_unknown_isaac_asset_fails_before_writing(tmp_path):
    catalog_path, _ = _mini_catalog(tmp_path)
    usd_path = tmp_path / "unknown.usd"
    usd_path.write_bytes(b"usd")

    result = _run(
        catalog_path,
        tmp_path / "out",
        apply=True,
        extra_args=[f"--isaac-usd=999_unknown={usd_path}"],
    )

    assert result.returncode == 2
    assert "not found in catalog" in result.stderr
    assert not (tmp_path / "out" / "backfill_upstream_report.json").exists()


def test_catalog_asset_path_cannot_escape_its_asset_key(tmp_path):
    catalog_path, _ = _mini_catalog(tmp_path)
    catalog = json.loads(catalog_path.read_text())
    empty_asset_dir = tmp_path / "empty-articulated-asset"
    empty_asset_dir.mkdir()
    catalog["entries"][1]["asset_path"] = str(empty_asset_dir)
    catalog_path.write_text(json.dumps(catalog, indent=2))

    result = _run(catalog_path, tmp_path / "out", apply=True)

    assert result.returncode == 2
    assert "asset_path escapes objects_root asset key" in result.stderr
    assert not (tmp_path / "out" / "backfill_upstream_report.json").exists()


def test_main_records_validator_violations_and_exits_nonzero(
    tmp_path, monkeypatch, backfill_module
):
    catalog_path, _ = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    monkeypatch.setattr(
        backfill_module.ledger,
        "validate_ledger",
        lambda document, check_files: [
            ledger.Violation("models.0", "injected_failure", "coverage fixture")
        ],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--catalog",
            str(catalog_path),
            "--out",
            str(out),
            "--apply",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        backfill_module.main()

    assert exc_info.value.code == 1
    report = json.loads((out / "backfill_upstream_report.json").read_text())
    assert report["violations"]["901_widget"][0]["code"] == "injected_failure"
    assert report["written"] == []
    assert not (out / "901_widget" / "ledger.json").exists()


@pytest.mark.parametrize("asset_id", ["../escape", "bad/name", "bad\\name", "bad\x00id", "x" * 129])
def test_catalog_asset_keys_must_be_safe_bounded_basenames(tmp_path, asset_id):
    catalog_path, _ = _mini_catalog(tmp_path)
    catalog = json.loads(catalog_path.read_text())
    catalog["entries"][0]["asset_id"] = asset_id
    catalog_path.write_text(json.dumps(catalog))

    result = _run(catalog_path, tmp_path / "out", apply=True, seed_receipts=False)

    assert result.returncode == 2
    assert "unsafe asset_id" in result.stderr
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("attack", ["duplicate_asset", "duplicate_model"])
def test_catalog_duplicate_identities_are_rejected_before_output(tmp_path, attack):
    catalog_path, _ = _mini_catalog(tmp_path)
    catalog = json.loads(catalog_path.read_text())
    if attack == "duplicate_asset":
        catalog["entries"].append(json.loads(json.dumps(catalog["entries"][0])))
    else:
        catalog["entries"][0]["models"].append(
            json.loads(json.dumps(catalog["entries"][0]["models"][0]))
        )
    catalog_path.write_text(json.dumps(catalog))

    result = _run(catalog_path, tmp_path / "out", apply=True, seed_receipts=False)

    assert result.returncode == 2
    assert "duplicate" in result.stderr
    assert not (tmp_path / "out").exists()


def test_two_model_asset_uses_one_asset_level_profile_without_upsert_crash(tmp_path):
    catalog_path, _ = _mini_catalog(tmp_path)
    catalog = json.loads(catalog_path.read_text())
    catalog["entries"][0]["models"][1]["usable"] = True
    catalog["entries"][0]["models"][1]["missing"] = []
    catalog_path.write_text(json.dumps(catalog, indent=2))
    usd = tmp_path / "first-model.usd"
    usd.write_bytes(USD_CONTENT_901)

    result = _run(
        catalog_path,
        tmp_path / "out",
        apply=True,
        extra_args=[f"--isaac-usd=901_widget={usd}"],
    )

    assert result.returncode == 0, result.stderr
    document = json.loads((tmp_path / "out" / "901_widget" / "ledger.json").read_text())
    assert [model["model_id"] for model in document["models"]] == [0, 1]
    assert document["profile"] == "sapien_only"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("verified_digest", "0" * 64),
        ("check", "runtime_load"),
        ("verdict", "fail"),
        ("backend", "isaacsim"),
        ("run_id", "different-run"),
    ],
)
def test_stale_or_nonsettle_pose_receipt_is_not_resigned_or_written(tmp_path, field, value):
    catalog_path, _ = _mini_catalog(tmp_path)
    out = tmp_path / "out"
    assert _run(catalog_path, out, apply=True).returncode == 0
    ledger_path = out / "901_widget" / "ledger.json"
    document = json.loads(ledger_path.read_text())
    document["models"][0]["verification"][0][field] = value
    attacked = json.dumps(document, indent=2) + "\n"
    ledger_path.write_text(attacked)

    result = _run(catalog_path, out, apply=True)

    assert result.returncode == 1
    assert ledger_path.read_text() == attacked
    report = json.loads((out / "backfill_upstream_report.json").read_text())
    codes = {item["code"] for item in report["violations"]["901_widget"]}
    assert "measured_against_required" in codes
