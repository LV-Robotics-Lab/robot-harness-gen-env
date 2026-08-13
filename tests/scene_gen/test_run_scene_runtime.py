from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "script" / "run_scene_runtime.py"

spec = importlib.util.spec_from_file_location("run_scene_runtime", SCRIPT)
assert spec is not None and spec.loader is not None
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def test_task_config_path_supports_legacy_and_env_cfg_layouts(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "task_config" / "demo_clean.yml"
    modern = tmp_path / "env_cfg" / "task_config" / "demo_clean.yml"

    legacy.parent.mkdir(parents=True)
    modern.parent.mkdir(parents=True)
    legacy.write_text("legacy\n", encoding="utf-8")
    modern.write_text("modern\n", encoding="utf-8")

    assert runtime._robotwin_task_config_path(tmp_path, "demo_clean") == legacy

    legacy.unlink()

    assert runtime._robotwin_task_config_path(tmp_path, "demo_clean") == modern


def test_task_config_path_reports_both_searched_layouts(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError) as error:
        runtime._robotwin_task_config_path(tmp_path, "missing")

    message = str(error.value)
    assert str(tmp_path / "task_config" / "missing.yml") in message
    assert str(tmp_path / "env_cfg" / "task_config" / "missing.yml") in message
