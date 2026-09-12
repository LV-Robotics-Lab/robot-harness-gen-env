"""The public schema exporter detects snapshots drifting from Python contracts."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def test_export_interface_reports_missing_and_changed_files_without_repairing(tmp_path):
    from self_improving.harness.x2env.schema_export import export

    assert "SceneIR.json" in export(tmp_path, check=True)
    assert export(tmp_path) == ()
    assert export(tmp_path, check=True) == ()
    (tmp_path / "SceneIR.json").write_text("{}")
    assert export(tmp_path, check=True) == ("SceneIR.json",)


def test_export_then_check_detects_manual_schema_drift(tmp_path):
    command = [
        sys.executable,
        str(ROOT / "script/export_x2env_schemas.py"),
        "--output",
        str(tmp_path),
    ]
    generated = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert generated.returncode == 0, generated.stderr
    assert (tmp_path / "SceneIR.json").is_file()
    assert (tmp_path / "api-fields.md").is_file()
    checked = subprocess.run(command + ["--check"], capture_output=True, text=True, timeout=30)
    assert checked.returncode == 0, checked.stderr
    (tmp_path / "SceneIR.json").write_text("{}\n")
    drift = subprocess.run(command + ["--check"], capture_output=True, text=True, timeout=30)
    assert drift.returncode == 1
    assert "SceneIR.json" in drift.stdout
    assert (tmp_path / "SceneIR.json").read_text() == "{}\n"
