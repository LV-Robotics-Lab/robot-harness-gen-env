from __future__ import annotations

import importlib.util
from pathlib import Path

ACTIVE_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ACTIVE_ROOT / "runtime_config.py"


def _load_config(monkeypatch):
    for name in (
        "ASSET_PIPELINE_ROOT",
        "GEN_ENV_ROOT",
        "ROBOTWIN_ROOT",
        "ROBOTWIN_SHADOW_ROOT",
        "ASSET_CATALOG",
        "ASSET_OVERRIDES",
        "OBJAVERSE_DATA_ROOT",
    ):
        monkeypatch.delenv(name, raising=False)
    spec = importlib.util.spec_from_file_location("asset_pipeline_runtime_config_test", CONFIG_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_paths_default_to_the_current_checkout(monkeypatch):
    config = _load_config(monkeypatch)

    assert config.ACTIVE_ROOT == ACTIVE_ROOT
    assert config.REPO_ROOT == ACTIVE_ROOT.parents[2]
    assert config.GEN_ENV_ROOT == config.REPO_ROOT
    assert config.ASSET_PIPELINE_ROOT == ACTIVE_ROOT
    assert config.ROBOTWIN_SHADOW_ROOT == ACTIVE_ROOT / "data" / "robotwin_shadow"
    assert config.ASSET_OVERRIDES == (
        ACTIVE_ROOT / "data" / "scene_gen_ext" / "asset_overrides_ext.yml"
    )
    assert config.OPENXSIM_SOURCE.is_dir()


def test_active_sources_do_not_embed_personal_home_paths():
    forbidden = ("/home/jingxiang", "/Users/borisguo")
    source_root = ACTIVE_ROOT / "1_asset_reuse"

    for suffix in ("*.py", "*.sh"):
        for path in source_root.rglob(suffix):
            if "tests" in path.parts or "archive" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            assert not any(marker in text for marker in forbidden), path
