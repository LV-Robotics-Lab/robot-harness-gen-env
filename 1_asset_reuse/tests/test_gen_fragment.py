import json
import sys
from pathlib import Path
import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "1_asset_reuse/scripts"))
sys.path.insert(0, str(REPO / "1_asset_reuse"))
from lib import ledger
from tests.test_ledger import make_valid
import gen_fragment


def _write(lib, asset, led):
    for m in led["models"]:  # digest 补真值
        for v in m["verification"]:
            v["verified_digest"] = ledger.reps_digest(m, v["backend"])
    p = lib / asset / "ledger.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(led))
    return led


def test_projection_default_pose(tmp_path):
    _write(tmp_path, "315_shears", make_valid())
    frag, stats = gen_fragment.generate(tmp_path)
    m = frag["315_shears"]["models"]["0"]
    assert m["stable_pose_id"] == "upright"  # 列表→标量投影
    assert m["stable_orientation_wxyz"] == ledger.X90_WXYZ
    assert "is_static" not in m  # False 不输出
    assert stats["unknown_license_models"] == 1  # 警告计数


def test_latest_fail_excluded(tmp_path):
    led = make_valid()
    led["models"][0]["verification"].append(
        {
            "backend": "sapien",
            "check": "settle",
            "verdict": "fail",
            "run_id": "r2",
            "timestamp": "2026-08-08T12:00:00",
            "verified_digest": "补真值占位",
            "report_path": "r.json",
        }
    )
    _write(tmp_path, "315_shears", led)
    frag, _ = gen_fragment.generate(tmp_path)
    assert "315_shears" not in frag  # latest=fail → 出视图（禁 any(pass)）


def test_stale_digest_excluded(tmp_path):
    led = make_valid()
    _write(tmp_path, "315_shears", led)
    p = tmp_path / "315_shears/ledger.json"
    led2 = json.loads(p.read_text())
    led2["models"][0]["verification"][0]["verified_digest"] = "e" * 64
    p.write_text(json.dumps(led2))
    frag, _ = gen_fragment.generate(tmp_path)
    assert "315_shears" not in frag  # digest 失效=未验证


def test_license_gate(tmp_path):
    _write(tmp_path, "315_shears", make_valid())
    frag_off, _ = gen_fragment.generate(tmp_path)
    frag_on, _ = gen_fragment.generate(tmp_path, license_gate=True)
    assert "315_shears" in frag_off and "315_shears" not in frag_on


def test_cli_warns_unknown(tmp_path, capsys):
    _write(tmp_path, "315_shears", make_valid())
    gen_fragment.main(
        ["--library-dir", str(tmp_path), "--out", str(tmp_path / "f.yml")]
    )
    assert "unknown license" in capsys.readouterr().err.lower()  # 无论开关必打警告
