import hashlib
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backfill_ledger_v1.py"


def _mini_pool(tmp_path):
    lib = tmp_path / "asset_library"
    a = lib / "399_widget"
    (a / "visual").mkdir(parents=True)
    (a / "collision").mkdir(parents=True)
    vis0 = a / "visual/base0.glb"
    vis0.write_bytes(b"V")
    col0 = a / "collision/base0.glb"
    col0.write_bytes(b"V")
    (a / "model_data0.json").write_text(json.dumps({"extents": [0.1, 0.2, 0.1]}))
    vis1 = a / "visual/base1.glb"
    vis1.write_bytes(b"V")
    col1 = a / "collision/base1.glb"
    col1.write_bytes(b"V")
    (a / "model_data1.json").write_text(json.dumps({"extents": [0.1, 0.2, 0.1]}))

    src = lib / "_source/acq_399_widget"
    src.mkdir(parents=True)
    (src / "SOURCE_MANIFEST.json").write_text(
        json.dumps({"files": {"w.usd": "ab" * 32}})
    )

    run = tmp_path / "results/20260803_import/bundles"
    run.mkdir(parents=True)
    sha = hashlib.sha256(b"V").hexdigest()

    def _bundle(model, vis, col):
        return {
            "asset_id": f"external_399_widget_m{model}",
            "category": "widget",
            "representations": [
                {
                    "format": "glb",
                    "uri": str(vis),
                    "backend": "sapien",
                    "role": "visual",
                    "sha256": sha,
                    "size_bytes": 1,
                    "metadata": {
                        "derived_from": "w.usd",
                        "rotated_z2y": True,
                        "origin": "bottom-center normalized",
                    },
                },
                {
                    "format": "glb",
                    "uri": str(col),
                    "backend": "sapien",
                    "role": "collision",
                    "sha256": sha,
                    "size_bytes": 1,
                    "metadata": {},
                },
            ],
            "source": {
                "library": "NVIDIA Isaac Assets 5.1",
                "group": "acq_399_widget",
                "file": "w.usd",
                "license": "unknown (test)",
            },
            "physical": {
                "mass_kg": {
                    "value": None,
                    "status": "unknown",
                    "runtime_default_kg": 0.1,
                },
                "mesh_bbox_m": [0.1, 0.2, 0.1],
                "scale_applied": 1.0,
                "size_resolution": {
                    "mode": "match_category",
                    "actual_max_dim_m": 0.2,
                    "scale": 1.0,
                    "reference_max_dim_m": None,
                    "reference_assets": [],
                    "verdict": "no_precedent",
                },
                "conventions": {
                    "is_static": False,
                    "z_policy": "origin_on_table",
                    "footprint_shape": "box",
                    "precedent": None,
                    "note": "no precedent",
                },
                "scale": [1.0, 1.0, 1.0],
                "mesh_up_axis": "Y",
            },
            "articulation": {},
            "tags": ["rigid", "external", "batch"],
        }

    (run / "399_widget_m0.json").write_text(json.dumps(_bundle(0, vis0, col0)))
    (run / "399_widget_m1.json").write_text(json.dumps(_bundle(1, vis1, col1)))

    (run.parent / "import_matrix.json").write_text(
        json.dumps(
            [
                {
                    "asset": "399_widget",
                    "model": 0,
                    "status": "accepted",
                    "settled": True,
                    "no_penetration": True,
                    "tilt_ok": True,
                },
                {
                    "asset": "399_widget",
                    "model": 1,
                    "status": "accepted",
                    "settled": True,
                    "no_penetration": True,
                    "tilt_ok": True,
                },
            ]
        )
    )
    frag = tmp_path / "fragment.yml"
    frag.write_text(
        "  399_widget:\n    category: widget\n    aliases: [widget, gadget]\n"
        '    models:\n      "0":\n        stable_pose_id: upright\n'
        "        stable_orientation_wxyz: [0.7071067811865476, 0.7071067811865476, 0.0, 0.0]\n"
        "        z_policy: origin_on_table\n        footprint_shape: box\n"
        '      "1":\n        stable_pose_id: upright\n'
        "        stable_orientation_wxyz: [0.7071067811865476, 0.7071067811865476, 0.0, 0.0]\n"
        "        z_policy: origin_on_table\n        footprint_shape: box\n"
    )
    return lib, tmp_path / "results", frag


def _run(lib, results, frag, out, apply=False):
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--library-dir",
        str(lib),
        "--results-root",
        str(results),
        "--fragment",
        str(frag),
        "--out",
        str(out),
    ] + (["--apply"] if apply else [])
    return subprocess.run(cmd, capture_output=True, text=True)


def test_apply_aggregates_to_one_ledger(tmp_path):
    lib, results, frag = _mini_pool(tmp_path)  # 造 399_widget m0+m1
    r = _run(lib, results, frag, tmp_path / "rep", apply=True)
    assert r.returncode == 0, r.stderr
    led = json.loads((lib / "399_widget/ledger.json").read_text())
    assert led["schema_version"] == "asset_ledger.v1"
    assert [m["model_id"] for m in led["models"]] == [0, 1]  # 聚合为一份
    assert led["semantics"]["aliases"] == ["widget", "gadget"]  # 资产级一次
    sp = led["models"][0]["physical"]["conventions"]["stable_poses"]
    assert sp[0]["pose_id"] == "upright" and sp[0]["is_default"] is True
    v = led["models"][0]["verification"][0]
    assert v["check"] == "settle" and len(v["verified_digest"]) == 64
    assert "run_id" in v and "T" in v["timestamp"]
    report = json.loads((tmp_path / "rep/backfill_report.json").read_text())
    assert report["violations"] == {}


def test_dry_run_does_not_write(tmp_path):
    lib, results, frag = _mini_pool(tmp_path)
    r = _run(lib, results, frag, tmp_path / "rep", apply=False)
    assert r.returncode == 0, r.stderr
    assert not (lib / "399_widget/ledger.json").exists()
    report = json.loads((tmp_path / "rep/backfill_report.json").read_text())
    assert report["violations"] == {}
    assert report["written"] == 0


def test_apply_is_idempotent(tmp_path):
    lib, results, frag = _mini_pool(tmp_path)
    r1 = _run(lib, results, frag, tmp_path / "rep1", apply=True)
    assert r1.returncode == 0, r1.stderr
    before = (lib / "399_widget/ledger.json").read_text()

    r2 = _run(lib, results, frag, tmp_path / "rep2", apply=True)
    assert r2.returncode == 0, r2.stderr
    after = (lib / "399_widget/ledger.json").read_text()
    assert before == after

    report2 = json.loads((tmp_path / "rep2/backfill_report.json").read_text())
    assert report2["written"] == 0
    assert report2["skipped"] == ["399_widget"]


def test_origin_convention_prefers_sapien_backend(tmp_path):
    # review fix-round-1: a non-sapien representation with role=="visual" must
    # not be picked for physical.origin_convention just because it's first in
    # the list -- only the sapien-backed visual rep's metadata.origin counts.
    lib = tmp_path / "asset_library"
    a = lib / "397_probe"
    (a / "visual").mkdir(parents=True)
    (a / "collision").mkdir(parents=True)
    vis_sapien = a / "visual/base0.glb"
    vis_sapien.write_bytes(b"V")
    col_sapien = a / "collision/base0.glb"
    col_sapien.write_bytes(b"V")
    vis_isaac = a / "visual/base0.usd"
    vis_isaac.write_bytes(b"V")
    (a / "model_data0.json").write_text(json.dumps({"extents": [0.1, 0.2, 0.1]}))

    src = lib / "_source/acq_397_probe"
    src.mkdir(parents=True)
    (src / "SOURCE_MANIFEST.json").write_text(
        json.dumps({"files": {"p.usd": "cd" * 32}})
    )

    run = tmp_path / "results/20260803_import/bundles"
    run.mkdir(parents=True)
    sha = hashlib.sha256(b"V").hexdigest()

    bundle = {
        "asset_id": "external_397_probe_m0",
        "category": "probe",
        "representations": [
            {
                "format": "usd",
                "uri": str(vis_isaac),
                "backend": "isaacsim",
                "role": "visual",
                "sha256": sha,
                "size_bytes": 1,
                "metadata": {"origin": "wrong-value normalized"},
            },
            {
                "format": "glb",
                "uri": str(vis_sapien),
                "backend": "sapien",
                "role": "visual",
                "sha256": sha,
                "size_bytes": 1,
                "metadata": {
                    "derived_from": "p.usd",
                    "rotated_z2y": True,
                    "origin": "bottom-center normalized",
                },
            },
            {
                "format": "glb",
                "uri": str(col_sapien),
                "backend": "sapien",
                "role": "collision",
                "sha256": sha,
                "size_bytes": 1,
                "metadata": {},
            },
        ],
        "source": {
            "library": "NVIDIA Isaac Assets 5.1",
            "group": "acq_397_probe",
            "file": "p.usd",
            "license": "unknown (test)",
        },
        "physical": {
            "mass_kg": {"value": None, "status": "unknown", "runtime_default_kg": 0.1},
            "mesh_bbox_m": [0.1, 0.2, 0.1],
            "scale_applied": 1.0,
            "size_resolution": {
                "mode": "match_category",
                "actual_max_dim_m": 0.2,
                "scale": 1.0,
                "reference_max_dim_m": None,
                "reference_assets": [],
                "verdict": "no_precedent",
            },
            "conventions": {
                "is_static": False,
                "z_policy": "origin_on_table",
                "footprint_shape": "box",
                "precedent": None,
                "note": "no precedent",
            },
            "scale": [1.0, 1.0, 1.0],
            "mesh_up_axis": "Y",
        },
        "articulation": {},
        "tags": ["rigid", "external", "batch"],
    }
    (run / "397_probe_m0.json").write_text(json.dumps(bundle))
    (run.parent / "import_matrix.json").write_text(
        json.dumps(
            [
                {
                    "asset": "397_probe",
                    "model": 0,
                    "status": "accepted",
                    "settled": True,
                    "no_penetration": True,
                    "tilt_ok": True,
                }
            ]
        )
    )
    frag = tmp_path / "fragment.yml"
    frag.write_text(
        "  397_probe:\n    category: probe\n    aliases: [probe]\n"
        '    models:\n      "0":\n        stable_pose_id: upright\n'
        "        stable_orientation_wxyz: [0.7071067811865476, 0.7071067811865476, 0.0, 0.0]\n"
        "        z_policy: origin_on_table\n        footprint_shape: box\n"
    )

    r = _run(lib, tmp_path / "results", frag, tmp_path / "rep", apply=True)
    assert r.returncode == 0, r.stderr
    led = json.loads((lib / "397_probe/ledger.json").read_text())
    assert led["models"][0]["physical"]["origin_convention"] == "bottom-center"
