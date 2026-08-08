import json
import subprocess
import sys
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "b_reverse"
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
