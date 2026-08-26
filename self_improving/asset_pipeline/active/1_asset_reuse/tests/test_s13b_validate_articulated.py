import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "4_validate"
    / "s13b_validate_articulated.py"
)


def test_non_numeric_instance_dir_fails_fast(tmp_path):
    # T6 fix round 1 (I-2): a non-numeric --instance-dir leaf must not
    # silently default model_id to 0 -- upsert_model's re-import semantics
    # (same model_id = wholesale replace) would let that clobber an
    # existing model 0. The check runs before any SAPIEN/scene work, so
    # this only needs a real `sapien` import (fast, no GPU/display touched)
    # plus a minimal export_report.json -- no real URDF/USD data required.
    inst = tmp_path / "314_cabinet" / "notanumber"
    inst.mkdir(parents=True)
    (inst / "export_report.json").write_text(
        json.dumps({"joints_movable": 1, "bbox_m": [0.6, 0.4, 0.8]})
    )
    lib_dir = tmp_path / "asset_library"
    out = tmp_path / "out"

    r = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--instance-dir",
            str(inst),
            "--source-usd",
            str(tmp_path / "does_not_need_to_exist.usd"),
            "--out",
            str(out),
            "--library-dir",
            str(lib_dir),
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAIL s13b" in r.stdout
    assert "not numeric" in r.stdout
    assert not (lib_dir / "314_cabinet" / "ledger.json").exists()


# Minimal URDF (primitive box geometry, no mesh files) so SAPIEN can load and
# step it without any real cabinet asset data -- one revolute joint (dof=1)
# matching a hand-written export_report.json below.
_MINI_URDF = """<?xml version="1.0"?>
<robot name="mini_cabinet">
  <link name="base">
    <visual><geometry><box size="0.3 0.3 0.5"/></geometry></visual>
    <collision><geometry><box size="0.3 0.3 0.5"/></geometry></collision>
    <inertial><mass value="1.0"/>
      <inertia ixx="0.01" ixy="0" ixz="0" iyy="0.01" iyz="0" izz="0.01"/>
    </inertial>
  </link>
  <link name="door">
    <visual><geometry><box size="0.02 0.2 0.3"/></geometry></visual>
    <collision><geometry><box size="0.02 0.2 0.3"/></geometry></collision>
    <inertial><mass value="0.2"/>
      <inertia ixx="0.001" ixy="0" ixz="0" iyy="0.001" iyz="0" izz="0.001"/>
    </inertial>
  </link>
  <joint name="door_hinge" type="revolute">
    <parent link="base"/>
    <child link="door"/>
    <origin xyz="0.2 0 0" rpy="0 0 0"/>
    <axis xyz="0 0 1"/>
    <limit lower="0" upper="1.57" effort="10" velocity="1"/>
  </joint>
</robot>
"""


def test_violations_block_ledger_write_but_snapshot_stays(tmp_path):
    pytest.importorskip("sapien", reason="articulated runtime validation requires SAPIEN")
    # H3: s13b's gate must align with import_materialize's own gate -- a
    # ledger that fails validate_ledger must NOT become authoritative
    # (formerly: WARN and write anyway). Trigger a genuine violation without
    # touching any script internals: pre-seed the asset's ledger.json with
    # an already-broken sibling model (model_id=0, missing nearly every
    # REQUIRED_MODEL field) before this run registers a second, well-formed
    # model (model_id=1) -- validate_ledger runs over the WHOLE merged
    # ledger (see s13b's `ledger.validate_ledger(led, check_files=False)`),
    # so model 0's pre-existing corruption is what fails the gate here, not
    # anything about this run's own physics.
    asset = "314_cabinet"
    inst = tmp_path / asset / "1"
    inst.mkdir(parents=True)
    (inst / "mobility.urdf").write_text(_MINI_URDF)
    (inst / "export_report.json").write_text(
        json.dumps(
            {
                "joints_movable": 1,
                "bbox_m": [0.3, 0.3, 0.5],
                "source_usd": "dummy_src.usd",
                "links": ["base", "door"],
                "movable": [{"name": "door_hinge", "type": "revolute"}],
            }
        )
    )
    source_usd = tmp_path / "dummy_src.usd"
    source_usd.write_bytes(b"USD")

    lib_dir = tmp_path / "asset_library"
    asset_dir = lib_dir / asset
    asset_dir.mkdir(parents=True)
    lp = asset_dir / "ledger.json"
    # v2-shaped, and matching what s13b itself would write for this asset --
    # the asset-level fields have to agree or upsert_model raises on drift
    # before the run ever reaches the schema gate this test is about.
    seed_ledger = {
        "schema_version": "asset_ledger.v3",
        "asset_id": f"external_{asset}",
        "category": "cabinet",
        "semantic_name": "cabinet",
        "kind": "articulated",
        "profile": "cross_backend",
        "tags": ["articulated", "external", "reverse-import"],
        "semantics": {
            "aliases": ["cabinet"],
            "colors": [],
            "materials": [],
            "identity": {"basis": "unknown", "evidence": None, "verified": False},
        },
        "models": [{"model_id": 0}],  # deliberately broken sibling
    }
    lp.write_text(json.dumps(seed_ledger, indent=2) + "\n")
    before = lp.read_text()

    out = tmp_path / "out"
    r = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--instance-dir",
            str(inst),
            "--source-usd",
            str(source_usd),
            "--out",
            str(out),
            "--library-dir",
            str(lib_dir),
        ],
        capture_output=True,
        text=True,
    )
    # I-3 (review round 1): a ledger violation must fail the run loudly --
    # not just skip the write silently. FAIL headline on stdout (this
    # script's own style), returncode != 0, detail trace on stderr.
    assert r.returncode != 0, r.stdout + r.stderr
    assert "FAIL s13b: schema violations" in r.stdout
    assert "NOT writing authoritative ledger" in r.stderr

    # authoritative ledger untouched -- still just the seeded broken model 0,
    # model 1 never got persisted.
    assert lp.read_text() == before
    on_disk = json.loads(lp.read_text())
    assert [m["model_id"] for m in on_disk["models"]] == [0]

    # run snapshot (pool-layer record, not the authoritative ledger) is
    # still written regardless of the violation.
    bundle = json.loads((out / "cabinet314_bundle.json").read_text())
    assert bundle["asset_id"] == f"external_{asset}_m1"
    assert (out / "cabinet314_validation.json").exists()
