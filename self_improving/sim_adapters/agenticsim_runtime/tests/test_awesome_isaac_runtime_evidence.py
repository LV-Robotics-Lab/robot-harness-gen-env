from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    module_path = ROOT / "scripts" / "build_awesome_isaac_runtime_evidence.py"
    spec = importlib.util.spec_from_file_location(
        "build_awesome_isaac_runtime_evidence", module_path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runtime_evidence_rebuild_matches_checked_summary() -> None:
    module = load_module()
    rebuilt = module.build_runtime_evidence()
    checked = json.loads(
        (ROOT / "docs" / "awesome_isaac_runtime_evidence.json").read_text(
            encoding="utf-8"
        )
    )

    assert rebuilt == checked
    assert rebuilt["baseline"]["video_evidence"]["frame_count"] == 32
    assert rebuilt["baseline"]["video_evidence"]["unique_frame_sha256_count"] == 32
    assert rebuilt["summary"]["repository_probe_count"] == 7
    assert rebuilt["summary"]["runtime_pass_count"] == 6
    assert rebuilt["summary"]["runtime_blocked_count"] == 1
    assert rebuilt["summary"]["source_unmodified_runtime_pass_count"] == 5
    assert rebuilt["summary"]["runtime_pass_after_source_patch_count"] == 1
    assert rebuilt["summary"]["runtime_pass_with_asset_license_gap_count"] == 2
    assert rebuilt["summary"]["visual_evidence_accepted_count"] == 6


def test_runtime_passes_keep_workarounds_and_blockers_visible() -> None:
    module = load_module()
    report = module.build_runtime_evidence()
    by_slug = {row["slug"]: row for row in report["repositories"]}

    assert by_slug["enactic/openarm_isaac_lab"]["conditions"]
    assert by_slug["neuromeka-robotics/nrmk_isaaclab_public"]["dependency_failures"]
    assert by_slug["noxrick91/WobbleGo"]["runtime_passed"] is False
    assert "data.noxcaw.com" in by_slug["noxrick91/WobbleGo"]["error"]["message"]
    assert by_slug["unitreerobotics/unitree_rl_lab"]["asset_license_gap"] is True
    assert by_slug["liorbenhorin/lerobot_so101_teleop"]["asset_license_gap"] is True
    assert by_slug["lehome-official/lehome-challenge"]["source_tree_modified"] is True
    assert by_slug["lehome-official/lehome-challenge"]["compatibility_patches"]
    assert by_slug["lehome-official/lehome-challenge"]["env_cfg_overrides"] == [
        {"path": "garment_name", "previous": None, "value": "Top_Long_Unseen_0"}
    ]
