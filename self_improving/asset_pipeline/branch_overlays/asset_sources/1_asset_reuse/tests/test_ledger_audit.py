import hashlib
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ledger" / "ledger_audit.py"


def _write_clean_asset(lib, asset, category="widget"):
    """A minimal but genuinely valid v1 ledger, with a real on-disk file
    (correct sha256/size_bytes) so validate_ledger(check_files=True) passes
    cleanly -- same field shape as tests/test_ledger.py's make_model/
    make_valid, just pointed at real tmp files instead of /tmp/x/ stubs."""
    adir = lib / asset
    (adir / "visual").mkdir(parents=True)
    vis = adir / "visual" / "base0.glb"
    vis.write_bytes(b"VISUAL_BYTES")
    sha = hashlib.sha256(b"VISUAL_BYTES").hexdigest()

    model = {
        "model_id": 0,
        "physical": {
            "mesh_bbox_m": [0.1, 0.1, 0.1],
            "mesh_up_axis": "Y",
            "origin_convention": "bottom-center",
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
                "stable_poses": [
                    {
                        "pose_id": "upright",
                        "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
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
                "uri": str(vis),
                "backend": "sapien",
                "role": "visual",
                "sha256": sha,
                "size_bytes": vis.stat().st_size,
                "metadata": {},
            }
        ],
        "articulation": {},
        "source": {
            "library": "test",
            "group": "test_group",
            "file": "base0.glb",
            "license": {"spdx": None, "status": "unknown", "terms_note": None},
            "retrieved_at": "2026-08-08",
            "source_manifest_path": str(adir / "SOURCE_MANIFEST.json"),
        },
        "verification": [],
    }
    led = {
        "schema_version": "asset_ledger.v1",
        "asset_id": f"external_{asset}",
        "category": category,
        "semantic_name": category,
        "kind": "rigid",
        "tags": ["rigid", "external", "batch"],
        "semantics": {"aliases": [category], "colors": [], "materials": []},
        "models": [model],
    }
    (adir / "ledger.json").write_text(json.dumps(led, indent=2) + "\n")
    return adir, vis


def _run(lib, out=None):
    cmd = [sys.executable, str(SCRIPT), "--library-dir", str(lib)]
    if out is not None:
        cmd += ["--out", str(out)]
    return subprocess.run(cmd, capture_output=True, text=True)


def test_clean_pool_exits_zero(tmp_path):
    lib = tmp_path / "asset_library"
    _write_clean_asset(lib, "399_widget")
    out = tmp_path / "report.json"

    r = _run(lib, out)
    assert r.returncode == 0, r.stdout + r.stderr

    report = json.loads(out.read_text())
    assert report["audited"] == 1
    assert report["clean"] == ["399_widget"]
    assert report["violations"] == {}
    assert report["no_ledger"] == []


def test_tampered_file_triggers_sha256_mismatch_exit_1(tmp_path):
    lib = tmp_path / "asset_library"
    _adir, vis = _write_clean_asset(lib, "399_widget")
    vis.write_bytes(b"TAMPERED")  # bytes changed, ledger.json's sha256 stale
    out = tmp_path / "report.json"

    r = _run(lib, out)
    assert r.returncode == 1, r.stdout + r.stderr

    report = json.loads(out.read_text())
    assert report["clean"] == []
    codes = [v["code"] for v in report["violations"]["399_widget"]]
    assert "sha256_mismatch" in codes


def test_no_ledger_asset_recorded_and_exit_stays_zero(tmp_path):
    lib = tmp_path / "asset_library"
    _write_clean_asset(lib, "399_widget")  # exit-0 baseline

    unmigrated = lib / "205_legacy"
    unmigrated.mkdir(parents=True)
    (unmigrated / "model_data0.json").write_text(json.dumps({"extents": [0.1] * 3}))

    out = tmp_path / "report.json"
    r = _run(lib, out)
    assert r.returncode == 0, r.stdout + r.stderr

    report = json.loads(out.read_text())
    assert report["no_ledger"] == ["205_legacy"]
    assert report["clean"] == ["399_widget"]
    assert report["audited"] == 1  # 205_legacy has no ledger -> not counted
    assert report["violations"] == {}


def test_no_out_prints_stdout_summary(tmp_path):
    lib = tmp_path / "asset_library"
    _write_clean_asset(lib, "399_widget")

    r = _run(lib)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "audited: 1" in r.stdout
    assert "clean: 1" in r.stdout


def test_empty_sweep_errors_out(tmp_path):
    # M-4 (review round 1): audited==0 and no_ledger==0 -- e.g. an empty dir,
    # or --library-dir pointed at the wrong place entirely -- must not read
    # as a silent "all clean" (exit 0). exit 2, distinct from the exit-1
    # "found violations" case.
    lib = tmp_path / "asset_library"
    lib.mkdir()

    r = _run(lib)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "nothing to audit" in r.stderr
