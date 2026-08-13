"""Relocatable paths for the active asset and simulator pipeline.

Repository-owned paths default to this checkout. External runtimes and the
RoboTwin asset tree can be injected without editing source files.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _path_from_env(name: str, default: Path) -> Path:
    return Path(os.environ.get(name, default)).expanduser().resolve()


ACTIVE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = ACTIVE_ROOT.parents[2]
ASSET_PIPELINE_ROOT = _path_from_env("ASSET_PIPELINE_ROOT", ACTIVE_ROOT)
GEN_ENV_ROOT = _path_from_env("GEN_ENV_ROOT", REPO_ROOT)
ROBOTWIN_ROOT = _path_from_env("ROBOTWIN_ROOT", REPO_ROOT / "external" / "RoboTwin")
ROBOTWIN_SHADOW_ROOT = _path_from_env(
    "ROBOTWIN_SHADOW_ROOT",
    ASSET_PIPELINE_ROOT / "data" / "robotwin_shadow",
)
ASSET_CATALOG = _path_from_env(
    "ASSET_CATALOG",
    ASSET_PIPELINE_ROOT / "data" / "scene_gen_ext" / "asset_catalog.json",
)
ASSET_OVERRIDES = _path_from_env(
    "ASSET_OVERRIDES",
    ASSET_PIPELINE_ROOT / "data" / "scene_gen_ext" / "asset_overrides_ext.yml",
)
OBJAVERSE_DATA_ROOT = _path_from_env(
    "OBJAVERSE_DATA_ROOT",
    ASSET_PIPELINE_ROOT / "data" / "asset_index" / "objaverse",
)
OPENXSIM_SOURCE = ACTIVE_ROOT / "shared" / "openxsim" / "source" / "agenticsim"

SAPIEN_PYTHON = os.environ.get("SAPIEN_PYTHON", sys.executable)
ISAAC_PYTHON = os.environ.get("ISAAC_PYTHON", sys.executable)
