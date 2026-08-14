import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ledger" / "backfill_ledger_v1.py"


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


def _run(lib, results, frag, out, apply=False, extra_args=()):
    cmd = (
        [
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
        ]
        + (["--apply"] if apply else [])
        + list(extra_args)
    )
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


def test_bundle_alias_articulated_derivations(tmp_path):
    # Reproduces the 314_cabinet-shaped gaps the T8 real-pool backfill run
    # needed mapping fixes for: legacy bundle filed outside the
    # */bundles/<asset>_m<n>.json convention (picked up via --bundle-alias),
    # model marker nested under <asset>/<n>/model_data<n>.json (not the flat
    # rigid-asset layout), source.group/file absent but recoverable from a
    # representation's uri/derived_from, a _source/<group>/ mirror dir
    # present without a SOURCE_MANIFEST.json (generated on --apply), and
    # articulation.joints[].name present but not the required joint_names.
    lib = tmp_path / "asset_library"
    asset_dir = lib / "298_locker"
    (asset_dir / "0").mkdir(parents=True)
    (asset_dir / "0" / "model_data0.json").write_text(
        json.dumps({"extents": [0.2, 0.2, 0.3]})
    )
    urdf_path = asset_dir / "0" / "mobility.urdf"
    urdf_path.write_bytes(b"<robot/>")
    urdf_sha = hashlib.sha256(b"<robot/>").hexdigest()
    vis_path = asset_dir / "0" / "preview.glb"
    vis_path.write_bytes(b"V")
    vis_sha = hashlib.sha256(b"V").hexdigest()

    src_dir = lib / "_source/acq_298_locker"
    src_dir.mkdir(parents=True)
    (src_dir / "locker.usd").write_bytes(b"U")
    src_sha = hashlib.sha256(b"U").hexdigest()

    stray = tmp_path / "misc_run" / "articulated"
    stray.mkdir(parents=True)
    bundle = {
        "asset_id": "external_298_locker_m0",
        "category": "locker",
        "representations": [
            {
                "format": "urdf",
                "uri": str(urdf_path),
                "backend": "sapien",
                "role": "visual_and_collision",
                "sha256": urdf_sha,
                "size_bytes": 1,
                "metadata": {"derived_from": str(src_dir / "locker.usd")},
            },
            {
                "format": "glb",
                "uri": str(vis_path),
                "backend": "sapien",
                "role": "visual",
                "sha256": vis_sha,
                "size_bytes": 1,
                "metadata": {"origin": "bottom-center normalized"},
            },
        ],
        "source": {"library": "Test Lib", "license": "unknown (test)"},
        "physical": {
            "mass_kg": {"value": None, "status": "unknown", "runtime_default_kg": 1.0},
            "mesh_bbox_m": [0.2, 0.2, 0.3],
            "mesh_up_axis": "Z",
            "size_resolution": {
                "mode": "match_category",
                "actual_max_dim_m": 0.3,
                "scale": 1.0,
                "reference_max_dim_m": None,
                "reference_assets": [],
                "verdict": "no_precedent",
            },
            "scale": [1.0, 1.0, 1.0],
        },
        "articulation": {"joints": [{"name": "door_joint"}, {"name": "drawer_joint"}]},
        "tags": ["articulated", "external"],
    }
    (stray / "locker_bundle.json").write_text(json.dumps(bundle))

    frag = tmp_path / "fragment.yml"
    frag.write_text(
        "  298_locker:\n    category: locker\n    aliases: [locker]\n"
        '    models:\n      "0":\n        stable_pose_id: upright\n'
        "        stable_orientation_wxyz: [1.0, 0.0, 0.0, 0.0]\n"
        "        z_policy: origin_on_table\n        footprint_shape: box\n"
    )

    out = tmp_path / "rep"
    r = _run(
        lib,
        tmp_path / "results",
        frag,
        out,
        apply=True,
        extra_args=[f"--bundle-alias=298_locker:0={stray / 'locker_bundle.json'}"],
    )
    assert r.returncode == 0, r.stderr

    led = json.loads((asset_dir / "ledger.json").read_text())
    m = led["models"][0]
    assert m["source"]["group"] == "acq_298_locker"
    assert m["source"]["file"] == "locker.usd"
    assert m["physical"]["scale_applied"] == 1.0
    assert m["articulation"]["joint_names"] == ["door_joint", "drawer_joint"]

    manifest_path = src_dir / "SOURCE_MANIFEST.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["files"]["locker.usd"] == src_sha
    assert m["source"]["source_manifest_path"] == str(manifest_path.resolve())

    report = json.loads((out / "backfill_report.json").read_text())
    assert report["violations"] == {}
    assert "298_locker" not in report["excluded"]
    notes = report["notes"]
    assert "298_locker:m0:acq_298_locker" in notes["group_derived_from_representations"]
    assert "298_locker:m0" in notes["source_manifest_generated"]
    assert "298_locker:m0" in notes["source_manifest_written"]
    assert "298_locker:m0" in notes["file_derived_from_representations"]
    assert "298_locker:m0" in notes["scale_applied_derived_from_scale_vector"]


def test_pending_manifest_not_written_when_asset_excluded(tmp_path):
    # T8 review I-1: a _source/<group>/ mirror dir missing only its
    # SOURCE_MANIFEST.json must not get one synthesized on disk unless the
    # asset it's attached to actually clears validate_ledger and gets
    # promoted -- otherwise a synthetic manifest is orphaned in the pool for
    # an asset that was never actually backfilled (the real incident: 314
    # _cabinet stayed excluded but sektion_cabinet/SOURCE_MANIFEST.json got
    # written anyway, wrong prefix shape and all).
    lib = tmp_path / "asset_library"
    a = lib / "394_orphan"
    (a / "visual").mkdir(parents=True)
    (a / "collision").mkdir(parents=True)
    vis = a / "visual/base0.glb"
    vis.write_bytes(b"V")
    col = a / "collision/base0.glb"
    col.write_bytes(b"V")
    (a / "model_data0.json").write_text(json.dumps({"extents": [0.1, 0.1, 0.1]}))

    src_dir = lib / "_source/acq_394_orphan"
    src_dir.mkdir(parents=True)
    (src_dir / "o.usd").write_bytes(b"U")

    run = tmp_path / "results/20260803_import/bundles"
    run.mkdir(parents=True)
    sha = hashlib.sha256(b"V").hexdigest()
    bundle = {
        "asset_id": "external_394_orphan_m0",
        "category": "orphan",
        "representations": [
            {
                "format": "glb",
                "uri": str(vis),
                "backend": "sapien",
                "role": "visual",
                "sha256": sha,
                "size_bytes": 1,
                "metadata": {"origin": "bottom-center normalized"},
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
            "group": "acq_394_orphan",
            "file": "o.usd",
            "license": "unknown (test)",
        },
        "physical": {
            "mass_kg": {"value": None, "status": "unknown", "runtime_default_kg": 0.1},
            "mesh_bbox_m": [0.1, 0.1, 0.1],
            # mesh_up_axis / size_resolution deliberately absent -- an
            # irreducible gap, this asset must end up excluded regardless of
            # the manifest-pending mechanics being tested here.
            "scale": [1.0, 1.0, 1.0],
            "conventions": {
                "is_static": False,
                "z_policy": "origin_on_table",
                "footprint_shape": "box",
                "precedent": None,
            },
        },
        "articulation": {},
        "tags": ["rigid", "external"],
    }
    (run / "394_orphan_m0.json").write_text(json.dumps(bundle))
    frag = tmp_path / "fragment.yml"
    frag.write_text(
        "  394_orphan:\n    category: orphan\n    aliases: [orphan]\n"
        '    models:\n      "0":\n        stable_pose_id: upright\n'
        "        stable_orientation_wxyz: [1.0, 0.0, 0.0, 0.0]\n"
        "        z_policy: origin_on_table\n        footprint_shape: box\n"
    )
    out = tmp_path / "rep"
    r = _run(lib, tmp_path / "results", frag, out, apply=True)
    assert r.returncode == 1  # violations present (mesh_up_axis etc.)

    manifest_path = src_dir / "SOURCE_MANIFEST.json"
    assert not manifest_path.exists()  # core assertion: no orphaned synthetic manifest
    assert not (a / "ledger.json").exists()

    report = json.loads((out / "backfill_report.json").read_text())
    assert "394_orphan" in report["excluded"]
    notes = report["notes"]
    # the build-time preview still fires (useful for triage: "this asset
    # WOULD get a manifest generated if it cleared validation") ...
    assert "394_orphan:m0" in notes["source_manifest_generated"]
    # ... but nothing was actually written to disk for it.
    assert "394_orphan:m0" not in notes["source_manifest_written"]


def test_violations_block_apply_write(tmp_path):
    # A legacy bundle that predates mesh_up_axis/size_resolution and whose
    # _source/<group>/ mirror dir was never kept: a real, irreducible data
    # gap (not something mapping logic can derive without fabricating a
    # value). The pool is only-append -- backfill must not write a ledger
    # that fails its own validator, and must say so in the report.
    lib = tmp_path / "asset_library"
    a = lib / "396_gap"
    (a / "visual").mkdir(parents=True)
    (a / "collision").mkdir(parents=True)
    vis = a / "visual/base0.glb"
    vis.write_bytes(b"V")
    col = a / "collision/base0.glb"
    col.write_bytes(b"V")
    (a / "model_data0.json").write_text(json.dumps({"extents": [0.1, 0.1, 0.1]}))

    run = tmp_path / "results/20260803_import/bundles"
    run.mkdir(parents=True)
    sha = hashlib.sha256(b"V").hexdigest()
    bundle = {
        "asset_id": "external_396_gap_m0",
        "category": "gap",
        "representations": [
            {
                "format": "glb",
                "uri": str(vis),
                "backend": "sapien",
                "role": "visual",
                "sha256": sha,
                "size_bytes": 1,
                "metadata": {"origin": "bottom-center normalized"},
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
            "group": "acq_396_gap",
            "file": "g.usd",
            "license": "unknown (test)",
        },
        "physical": {
            "mass_kg": {"value": None, "status": "unknown", "runtime_default_kg": 0.1},
            "mesh_bbox_m": [0.1, 0.1, 0.1],
            # mesh_up_axis / size_resolution deliberately absent, and
            # source.group's mirror dir is never created below -- nothing
            # here is legitimately derivable.
            "scale": [
                1.0,
                2.0,
                1.0,
            ],  # non-uniform: scale_applied stays undeducible too
            "conventions": {
                "is_static": False,
                "z_policy": "origin_on_table",
                "footprint_shape": "box",
                "precedent": None,
            },
        },
        "articulation": {},
        "tags": ["rigid", "external"],
    }
    (run / "396_gap_m0.json").write_text(json.dumps(bundle))
    frag = tmp_path / "fragment.yml"
    frag.write_text(
        "  396_gap:\n    category: gap\n    aliases: [gap]\n"
        '    models:\n      "0":\n        stable_pose_id: upright\n'
        "        stable_orientation_wxyz: [1.0, 0.0, 0.0, 0.0]\n"
        "        z_policy: origin_on_table\n        footprint_shape: box\n"
    )
    out = tmp_path / "rep"
    r = _run(lib, tmp_path / "results", frag, out, apply=True)
    assert r.returncode == 1  # violations present -> non-zero exit, contract unchanged
    assert not (a / "ledger.json").exists()
    report = json.loads((out / "backfill_report.json").read_text())
    assert report["excluded"] == ["396_gap"]
    assert report["written"] == 0
    codes = {v["code"] for v in report["violations"]["396_gap"]}
    assert "missing" in codes


def test_uri_rebased_to_current_library_dir(tmp_path):
    # The recorded uri lives under a repo root that's since been renamed
    # (env-gen-dev-asset -> env-gen-dev is the real-world case this fixes);
    # the file is real, just findable only by re-anchoring on the
    # data/asset_library/ segment still present in the stale path.
    lib = tmp_path / "asset_library"
    a = lib / "395_relic"
    (a / "visual").mkdir(parents=True)
    (a / "collision").mkdir(parents=True)
    vis = a / "visual/base0.glb"
    vis.write_bytes(b"V")
    col = a / "collision/base0.glb"
    col.write_bytes(b"V")
    (a / "model_data0.json").write_text(json.dumps({"extents": [0.1, 0.1, 0.1]}))

    src = lib / "_source/acq_395_relic"
    src.mkdir(parents=True)
    (src / "SOURCE_MANIFEST.json").write_text(
        json.dumps({"files": {"r.usd": "ab" * 32}})
    )

    run = tmp_path / "results/20260803_import/bundles"
    run.mkdir(parents=True)
    sha = hashlib.sha256(b"V").hexdigest()
    stale_vis_uri = "/stale-old-root/data/asset_library/395_relic/visual/base0.glb"
    stale_col_uri = "/stale-old-root/data/asset_library/395_relic/collision/base0.glb"
    bundle = {
        "asset_id": "external_395_relic_m0",
        "category": "relic",
        "representations": [
            {
                "format": "glb",
                "uri": stale_vis_uri,
                "backend": "sapien",
                "role": "visual",
                "sha256": sha,
                "size_bytes": 1,
                "metadata": {"origin": "bottom-center normalized"},
            },
            {
                "format": "glb",
                "uri": stale_col_uri,
                "backend": "sapien",
                "role": "collision",
                "sha256": sha,
                "size_bytes": 1,
                "metadata": {},
            },
        ],
        "source": {
            "library": "NVIDIA Isaac Assets 5.1",
            "group": "acq_395_relic",
            "file": "r.usd",
            "license": "unknown (test)",
        },
        "physical": {
            "mass_kg": {"value": None, "status": "unknown", "runtime_default_kg": 0.1},
            "mesh_bbox_m": [0.1, 0.1, 0.1],
            "mesh_up_axis": "Y",
            "scale_applied": 1.0,
            "size_resolution": {
                "mode": "match_category",
                "actual_max_dim_m": 0.1,
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
            },
        },
        "articulation": {},
        "tags": ["rigid", "external"],
    }
    (run / "395_relic_m0.json").write_text(json.dumps(bundle))
    frag = tmp_path / "fragment.yml"
    frag.write_text(
        "  395_relic:\n    category: relic\n    aliases: [relic]\n"
        '    models:\n      "0":\n        stable_pose_id: upright\n'
        "        stable_orientation_wxyz: [1.0, 0.0, 0.0, 0.0]\n"
        "        z_policy: origin_on_table\n        footprint_shape: box\n"
    )
    out = tmp_path / "rep"
    r = _run(lib, tmp_path / "results", frag, out, apply=True)
    assert r.returncode == 0, r.stderr
    led = json.loads((a / "ledger.json").read_text())
    reps = led["models"][0]["representations"]
    assert reps[0]["uri"] == str(vis)
    assert reps[1]["uri"] == str(col)
    report = json.loads((out / "backfill_report.json").read_text())
    assert "395_relic:m0" in report["notes"]["uri_rebased"]


def test_web_runs_bundle_not_trusted(tmp_path):
    # T8 review I-3: results/web_runs/ is the OTHER concurrent session's live
    # output area (Pipeline Studio) -- it uses the same YYYYMMDD_ naming
    # convention as historical import/acquire runs, so only a name-based
    # denylist (not a date-pattern check) can keep backfill from reading its
    # output. The decoy is given a strictly newer mtime than the legitimate
    # bundle so this test actually exercises the trust filter, not just the
    # "latest mtime wins" tie-break.
    lib, results, frag = _mini_pool(tmp_path)  # writes 399_widget m0+m1

    decoy_dir = (
        results / "web_runs" / "20260808_195234_place_a_brick_on_the_table" / "bundles"
    )
    decoy_dir.mkdir(parents=True)
    real_path = results / "20260803_import" / "bundles" / "399_widget_m0.json"
    decoy = json.loads(real_path.read_text())
    decoy["physical"]["mass_kg"]["runtime_default_kg"] = 999.0  # decoy marker
    decoy_path = decoy_dir / "399_widget_m0.json"
    decoy_path.write_text(json.dumps(decoy))

    now = time.time()
    os.utime(real_path, (now - 100, now - 100))
    os.utime(decoy_path, (now, now))  # decoy strictly newer than the legitimate bundle

    r = _run(lib, results, frag, tmp_path / "rep", apply=True)
    assert r.returncode == 0, r.stderr
    led = json.loads((lib / "399_widget/ledger.json").read_text())
    assert led["models"][0]["physical"]["mass_kg"]["runtime_default_kg"] == 0.1

    # H1 顺手: a rejected-as-untrusted candidate must not just silently
    # vanish from the discovery step -- it's derived (filtered) behavior,
    # and T8's own incident was exactly a silent derived-behavior change.
    # The report must show what was found and rejected, not just what won.
    report = json.loads((tmp_path / "rep/backfill_report.json").read_text())
    rejected = report["notes"].get("bundle_rejected_untrusted", [])
    assert any("399_widget:m0" in r and "web_runs" in r for r in rejected), rejected


def test_generated_manifest_retrieved_at_uses_latest_file_mtime(tmp_path):
    # H1 hardening 4: a _source/<group>/ mirror dir's OWN mtime is polluted
    # by any later file add/delete inside it (real incident: a mirror dir's
    # mtime read days after every actual file still inside it, because a
    # file had since been deleted from the dir). retrieved_at for the
    # generated_from_mirror_dir basis must come from the newest FILE inside
    # the dir, not the dir's own mtime.
    lib = tmp_path / "asset_library"
    a = lib / "393_relic2"
    (a / "visual").mkdir(parents=True)
    (a / "collision").mkdir(parents=True)
    vis = a / "visual/base0.glb"
    vis.write_bytes(b"V")
    col = a / "collision/base0.glb"
    col.write_bytes(b"V")
    (a / "model_data0.json").write_text(json.dumps({"extents": [0.1, 0.1, 0.1]}))

    src_dir = lib / "_source/acq_393_relic2"
    src_dir.mkdir(parents=True)
    old_file = src_dir / "old.usd"
    old_file.write_bytes(b"U1")
    new_file = src_dir / "new.usd"
    new_file.write_bytes(b"U2")

    now = time.time()
    old_ts = now - 6 * 86400
    new_ts = now - 4 * 86400
    os.utime(old_file, (old_ts, old_ts))
    os.utime(new_file, (new_ts, new_ts))
    os.utime(src_dir, (now, now))  # dir mtime is the NEWEST of all -- must not win

    run = tmp_path / "results/20260803_import/bundles"
    run.mkdir(parents=True)
    sha = hashlib.sha256(b"V").hexdigest()
    bundle = {
        "asset_id": "external_393_relic2_m0",
        "category": "relic2",
        "representations": [
            {
                "format": "glb",
                "uri": str(vis),
                "backend": "sapien",
                "role": "visual",
                "sha256": sha,
                "size_bytes": 1,
                "metadata": {"origin": "bottom-center normalized"},
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
            "group": "acq_393_relic2",
            "file": "new.usd",
            "license": "unknown (test)",
        },
        "physical": {
            "mass_kg": {"value": None, "status": "unknown", "runtime_default_kg": 0.1},
            "mesh_bbox_m": [0.1, 0.1, 0.1],
            "mesh_up_axis": "Y",
            "scale_applied": 1.0,
            "size_resolution": {
                "mode": "match_category",
                "actual_max_dim_m": 0.1,
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
            },
        },
        "articulation": {},
        "tags": ["rigid", "external"],
    }
    (run / "393_relic2_m0.json").write_text(json.dumps(bundle))
    frag = tmp_path / "fragment.yml"
    frag.write_text(
        "  393_relic2:\n    category: relic2\n    aliases: [relic2]\n"
        '    models:\n      "0":\n        stable_pose_id: upright\n'
        "        stable_orientation_wxyz: [1.0, 0.0, 0.0, 0.0]\n"
        "        z_policy: origin_on_table\n        footprint_shape: box\n"
    )
    out = tmp_path / "rep"
    r = _run(lib, tmp_path / "results", frag, out, apply=True)
    assert r.returncode == 0, r.stderr
    led = json.loads((a / "ledger.json").read_text())
    retrieved_at = led["models"][0]["source"]["retrieved_at"]

    expected = time.strftime("%Y-%m-%d", time.localtime(new_ts))
    dir_mtime_date = time.strftime("%Y-%m-%d", time.localtime(now))
    assert retrieved_at == expected
    assert retrieved_at != dir_mtime_date


def test_generated_manifest_retrieved_at_falls_back_to_dir_mtime_when_empty(tmp_path):
    # H1 hardening 4 edge case (review fix round 1: this branch had zero
    # test coverage): a mirror dir that exists but contains no files has no
    # file mtime to derive retrieved_at from -- must fall back to the dir's
    # own mtime (the prior, pre-hardening semantics) and say so explicitly
    # via notes.retrieved_at_mirror_dir_empty rather than silently.
    lib = tmp_path / "asset_library"
    a = lib / "392_hollow"
    (a / "visual").mkdir(parents=True)
    (a / "collision").mkdir(parents=True)
    vis = a / "visual/base0.glb"
    vis.write_bytes(b"V")
    col = a / "collision/base0.glb"
    col.write_bytes(b"V")
    (a / "model_data0.json").write_text(json.dumps({"extents": [0.1, 0.1, 0.1]}))

    src_dir = lib / "_source/acq_392_hollow"
    src_dir.mkdir(parents=True)  # exists but empty -- no files inside

    dir_ts = time.time() - 3 * 86400
    os.utime(src_dir, (dir_ts, dir_ts))

    run = tmp_path / "results/20260803_import/bundles"
    run.mkdir(parents=True)
    sha = hashlib.sha256(b"V").hexdigest()
    bundle = {
        "asset_id": "external_392_hollow_m0",
        "category": "hollow",
        "representations": [
            {
                "format": "glb",
                "uri": str(vis),
                "backend": "sapien",
                "role": "visual",
                "sha256": sha,
                "size_bytes": 1,
                "metadata": {"origin": "bottom-center normalized"},
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
            "group": "acq_392_hollow",
            "file": "h.usd",
            "license": "unknown (test)",
        },
        "physical": {
            "mass_kg": {"value": None, "status": "unknown", "runtime_default_kg": 0.1},
            "mesh_bbox_m": [0.1, 0.1, 0.1],
            "mesh_up_axis": "Y",
            "scale_applied": 1.0,
            "size_resolution": {
                "mode": "match_category",
                "actual_max_dim_m": 0.1,
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
            },
        },
        "articulation": {},
        "tags": ["rigid", "external"],
    }
    (run / "392_hollow_m0.json").write_text(json.dumps(bundle))
    frag = tmp_path / "fragment.yml"
    frag.write_text(
        "  392_hollow:\n    category: hollow\n    aliases: [hollow]\n"
        '    models:\n      "0":\n        stable_pose_id: upright\n'
        "        stable_orientation_wxyz: [1.0, 0.0, 0.0, 0.0]\n"
        "        z_policy: origin_on_table\n        footprint_shape: box\n"
    )
    out = tmp_path / "rep"
    r = _run(lib, tmp_path / "results", frag, out, apply=True)
    assert r.returncode == 0, r.stderr
    led = json.loads((a / "ledger.json").read_text())
    retrieved_at = led["models"][0]["source"]["retrieved_at"]

    expected = time.strftime("%Y-%m-%d", time.localtime(dir_ts))
    assert retrieved_at == expected  # fell back to dir mtime, not today

    report = json.loads((out / "backfill_report.json").read_text())
    assert "392_hollow:m0" in report["notes"]["retrieved_at_mirror_dir_empty"]


def test_generate_source_manifest_atomic_write(tmp_path, monkeypatch):
    # H1 顺手: SOURCE_MANIFEST.json lives in the shared _source/ pool where
    # other backfill/import runs may read concurrently -- writing it must go
    # through the same tempfile+os.replace atomicity as lib.ledger's writes,
    # not a bare path.write_text() that a concurrent reader could observe
    # half-written.
    sys.path.insert(0, str(SCRIPT.parent))
    import backfill_ledger_v1 as backfill_mod

    calls = []
    orig_replace = os.replace

    def spy_replace(src, dst):
        assert os.path.exists(src)  # tempfile exists and is fully written pre-replace
        calls.append((src, dst))
        return orig_replace(src, dst)

    monkeypatch.setattr(backfill_mod.os, "replace", spy_replace)
    target = tmp_path / "SOURCE_MANIFEST.json"
    backfill_mod._atomic_write_text(target, '{"a": 1}\n')
    assert calls, "os.replace was not used for the manifest write"
    assert target.read_text() == '{"a": 1}\n'
    assert list(tmp_path.glob("*.tmp")) == []  # no leftover tempfile
    # review fix round 1: mkstemp defaults to 0600 and os.replace doesn't
    # change mode bits -- without an explicit chmod the manifest would land
    # 0600, blocking the very "shared pool concurrent read" scenario this
    # atomic-write hardening exists for (see lib.ledger._atomic_write_json's
    # identical chmod).
    assert oct(target.stat().st_mode)[-3:] == "644"
