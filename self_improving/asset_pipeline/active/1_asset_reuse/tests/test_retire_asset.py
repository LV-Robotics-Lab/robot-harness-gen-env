import hashlib
import importlib.util
import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ledger" / "retire_asset.py"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import ledger  # noqa: E402


def _retire_module():
    spec = importlib.util.spec_from_file_location("retire_asset_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _model(model_id, vis, col, snap):
    return {
        "model_id": model_id,
        "physical": {
            "mesh_bbox_m": [0.1, 0.1, 0.1],
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
                        "measured_against": {
                            "backend": "sapien",
                            "run_id": f"fixture-settle-{model_id}",
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
                "uri": str(vis),
                "backend": "sapien",
                "role": "visual",
                "sha256": _sha256(vis),
                "files": [{"uri": str(vis), "sha256": _sha256(vis), "bytes": vis.stat().st_size}],
                "metadata": {},
            },
            {
                "format": "glb",
                "uri": str(col),
                "backend": "sapien",
                "role": "collision",
                "sha256": _sha256(col),
                "files": [{"uri": str(col), "sha256": _sha256(col), "bytes": col.stat().st_size}],
                "collision_meta": {"mode": "explicit_mesh"},
                "metadata": {},
            },
            {
                "format": "png",
                "uri": str(snap),
                "backend": "portable",
                "role": "snapshot",
                "sha256": _sha256(snap),
                "files": [
                    {
                        "uri": str(snap),
                        "sha256": _sha256(snap),
                        "bytes": snap.stat().st_size,
                    }
                ],
                "metadata": {},
            },
        ],
        "articulation": {},
        "source": {
            "kind": "retrieved",
            "library": "test",
            "group": "test_group",
            "file": f"base{model_id}.glb",
            "license": {"spdx": None, "status": "unknown", "terms_note": None},
            "retrieved_at": "2026-08-08",
            "source_manifest_path": "/tmp/nonexistent/SOURCE_MANIFEST.json",
        },
        "verification": [],
    }


def _write_asset(lib, asset, model_ids, category="widget"):
    """Rigid asset with one model per id in model_ids, each with real
    visual/collision/model_data/snapshot files -- so a retire test can
    assert the RIGHT model's files disappear and its sibling's survive."""
    adir = lib / asset
    (adir / "visual").mkdir(parents=True)
    (adir / "collision").mkdir(parents=True)
    (adir / "snapshots").mkdir(parents=True)

    models = []
    files = {}
    for n in model_ids:
        vis = adir / "visual" / f"base{n}.glb"
        json_chunk = b"{}  "
        glb = struct.pack("<4sII", b"glTF", 2, 20 + len(json_chunk))
        glb += struct.pack("<II", len(json_chunk), 0x4E4F534A) + json_chunk
        vis.write_bytes(glb)
        col = adir / "collision" / f"base{n}.glb"
        col.write_bytes(glb)
        (adir / f"model_data{n}.json").write_text(json.dumps({"extents": [0.1] * 3}))
        snap = adir / "snapshots" / f"m{n}_default.png"
        snap.write_bytes(f"S{n}".encode())
        models.append(_model(n, vis, col, snap))
        files[n] = {
            "vis": vis,
            "col": col,
            "model_data": adir / f"model_data{n}.json",
            "snap": snap,
        }

    led = {
        "schema_version": "asset_ledger.v3",
        "asset_id": f"external_{asset}",
        "external_ids": {"env_gen": asset},
        "category": category,
        "kind": "rigid",
        "profile": "sapien_only",
        "semantics": {
            "aliases": [category],
            "colors": [],
            "materials": [],
            "identity": {
                "basis": "manifest_human",
                "evidence": "fixture",
                "verified": False,
            },
        },
        "models": models,
    }
    for model in led["models"]:
        model["verification"] = [
            {
                "backend": "sapien",
                "check": "settle",
                "verdict": "pass",
                "run_id": f"fixture-settle-{model['model_id']}",
                "timestamp": "2026-08-31T12:00:00",
                "verified_digest": ledger.reps_digest(model, "sapien"),
            }
        ]
    assert ledger.validate_ledger(led, check_files=True) == []
    (adir / "ledger.json").write_text(json.dumps(led, indent=2) + "\n")
    return adir, files


def _run(lib, asset, model=None, apply=False):
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--library-dir",
        str(lib),
        "--asset",
        asset,
    ]
    if model is not None:
        cmd += ["--model", str(model)]
    if apply:
        cmd += ["--apply"]
    return subprocess.run(cmd, capture_output=True, text=True)


def test_dry_run_does_not_write(tmp_path):
    lib = tmp_path / "asset_library"
    adir, files = _write_asset(lib, "399_widget", [0, 1])
    before = (adir / "ledger.json").read_text()

    r = _run(lib, "399_widget", model=0, apply=False)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "dry-run" in r.stdout

    assert (adir / "ledger.json").read_text() == before
    for n in (0, 1):
        assert files[n]["vis"].exists()
        assert files[n]["col"].exists()
        assert files[n]["model_data"].exists()
        assert files[n]["snap"].exists()


def test_model_level_retire_prunes_ledger_and_files(tmp_path):
    lib = tmp_path / "asset_library"
    adir, files = _write_asset(lib, "399_widget", [0, 1])

    r = _run(lib, "399_widget", model=0, apply=True)
    assert r.returncode == 0, r.stdout + r.stderr

    # model 0's files gone
    assert not files[0]["vis"].exists()
    assert not files[0]["col"].exists()
    assert not files[0]["model_data"].exists()
    assert not files[0]["snap"].exists()
    # model 1's files (and the asset dir / ledger) untouched
    assert files[1]["vis"].exists()
    assert files[1]["col"].exists()
    assert files[1]["model_data"].exists()
    assert files[1]["snap"].exists()

    led = json.loads((adir / "ledger.json").read_text())
    assert [m["model_id"] for m in led["models"]] == [1]


def test_model_retire_fails_before_deleting_when_surviving_closure_is_invalid(tmp_path):
    lib = tmp_path / "asset_library"
    adir, files = _write_asset(lib, "399_widget", [0, 1])
    before = (adir / "ledger.json").read_bytes()
    files[1]["vis"].write_bytes(b"tampered sibling")

    result = _run(lib, "399_widget", model=0, apply=True)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "mismatch" in result.stderr
    assert (adir / "ledger.json").read_bytes() == before
    assert all(path.exists() for path in files[0].values())


def test_model_retire_preserves_file_shared_by_surviving_model(tmp_path):
    lib = tmp_path / "asset_library"
    adir, files = _write_asset(lib, "399_widget", [0, 1])
    ledger_path = adir / "ledger.json"
    document = json.loads(ledger_path.read_text())
    shared = files[0]["vis"]
    shared_digest = _sha256(shared)
    survivor = document["models"][1]
    survivor["representations"][0].update(
        uri=str(shared),
        sha256=shared_digest,
        files=[
            {
                "uri": str(shared),
                "sha256": shared_digest,
                "bytes": shared.stat().st_size,
            }
        ],
    )
    survivor["verification"][0]["verified_digest"] = ledger.reps_digest(survivor, "sapien")
    ledger_path.write_text(json.dumps(document))
    assert ledger.validate_ledger(document, check_files=True) == []

    result = _run(lib, "399_widget", model=0, apply=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert shared.is_file()
    written = json.loads(ledger_path.read_text())
    assert ledger.validate_ledger(written, check_files=True) == []


def test_model_retire_restores_every_file_when_ledger_commit_raises(tmp_path, monkeypatch):
    module = _retire_module()
    lib = tmp_path / "asset_library"
    adir, files = _write_asset(lib, "399_widget", [0, 1])
    ledger_path = adir / "ledger.json"
    before_ledger = ledger_path.read_bytes()
    before_files = {path: path.read_bytes() for path in files[0].values()}
    retirement = module.plan(lib, "399_widget", 0)

    def fail_write(path, document, *, expected=None):
        raise OSError("injected ledger failure")

    monkeypatch.setattr(module.ledger_writes, "write_validated", fail_write)

    with pytest.raises(OSError, match="injected ledger failure"):
        module.execute(adir, retirement)

    assert ledger_path.read_bytes() == before_ledger
    assert {path: path.read_bytes() for path in files[0].values()} == before_files


def test_model_level_retire_last_model_removes_whole_asset(tmp_path):
    lib = tmp_path / "asset_library"
    adir, _files = _write_asset(lib, "399_widget", [0])

    r = _run(lib, "399_widget", model=0, apply=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not adir.exists()


def test_asset_level_retire_removes_everything(tmp_path):
    lib = tmp_path / "asset_library"
    adir, _files = _write_asset(lib, "399_widget", [0, 1])

    r = _run(lib, "399_widget", model=None, apply=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not adir.exists()


def test_whole_asset_retirement_is_one_atomic_quarantine_rename(tmp_path):
    lib = tmp_path / "asset_library"
    adir, _files = _write_asset(lib, "399_widget", [0, 1])
    (adir / "untracked.txt").write_bytes(b"also-retired")
    before = {
        path.relative_to(adir): path.read_bytes() for path in adir.rglob("*") if path.is_file()
    }

    result = _run(lib, "399_widget", apply=True)

    assert result.returncode == 0, result.stdout + result.stderr
    quarantined = list((lib / "_retired").glob("399_widget.*"))
    assert len(quarantined) == 1
    assert {
        path.relative_to(quarantined[0]): path.read_bytes()
        for path in quarantined[0].rglob("*")
        if path.is_file()
    } == before


def test_no_ledger_errors_out(tmp_path):
    lib = tmp_path / "asset_library"
    adir = lib / "399_widget"
    (adir / "visual").mkdir(parents=True)
    vis = adir / "visual" / "base0.glb"
    vis.write_bytes(b"V")

    r = _run(lib, "399_widget", model=None, apply=True)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "no ledger.json" in r.stderr
    assert vis.exists()  # nothing touched


def test_unknown_model_id_errors_out(tmp_path):
    lib = tmp_path / "asset_library"
    adir, files = _write_asset(lib, "399_widget", [0])

    r = _run(lib, "399_widget", model=7, apply=True)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "model_id 7 not found" in r.stderr
    assert files[0]["vis"].exists()  # nothing touched
    assert (adir / "ledger.json").exists()


# --- I-1/I-2 (review round 1): containment guard regression tests -----------
# pathlib's `/` silently discards the left operand when the right one is
# absolute, and --library-dir could be accidentally pointed at a
# symlink-based tree (e.g. a shadow root) whose entries point at the real
# pool. All three must be rejected (exit code 2, distinct from the exit-1
# "known, ordinary" errors above) with the escape target left untouched.


def test_relative_traversal_asset_is_rejected(tmp_path):
    lib = tmp_path / "asset_library"
    lib.mkdir()
    outside = tmp_path / "OUTSIDE"
    outside.mkdir()
    marker = outside / "keepme.txt"
    marker.write_text("sensitive")

    r = _run(lib, "../OUTSIDE", model=None, apply=True)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "escapes" in r.stderr

    assert outside.exists()
    assert marker.read_text() == "sensitive"


def test_absolute_path_asset_is_rejected(tmp_path):
    lib = tmp_path / "asset_library"
    lib.mkdir()
    outside = tmp_path / "OUTSIDE2"
    outside.mkdir()
    marker = outside / "keepme.txt"
    marker.write_text("sensitive")

    r = _run(lib, str(outside), model=None, apply=True)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "escapes" in r.stderr

    assert outside.exists()
    assert marker.read_text() == "sensitive"


def test_symlinked_asset_is_rejected(tmp_path):
    lib = tmp_path / "asset_library"
    lib.mkdir()
    real_target = tmp_path / "real_target"
    real_target.mkdir()
    marker = real_target / "keepme.txt"
    marker.write_text("sensitive")

    linked = lib / "linked_asset"
    linked.symlink_to(real_target, target_is_directory=True)

    r = _run(lib, "linked_asset", model=None, apply=True)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "symlink" in r.stderr

    assert real_target.exists()
    assert marker.read_text() == "sensitive"
    assert linked.is_symlink()  # the symlink itself wasn't touched either


@pytest.mark.parametrize("nested", ["visual", "collision", "snapshots"])
def test_nested_asset_directory_symlink_is_rejected_before_retirement(tmp_path, nested):
    lib = tmp_path / "asset_library"
    adir, files = _write_asset(lib, "399_widget", [0, 1])
    outside = tmp_path / f"outside-{nested}"
    (adir / nested).rename(outside)
    (adir / nested).symlink_to(outside, target_is_directory=True)
    protected_payload = next(path for path in files[0].values() if path.parent == adir / nested)

    result = _run(lib, "399_widget", model=0, apply=True)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "symlink" in result.stderr
    assert protected_payload.exists()
    assert (adir / "ledger.json").exists()
