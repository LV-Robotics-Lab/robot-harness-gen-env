from __future__ import annotations

import json
from pathlib import Path

from scene_gen.catalog import scan_robotwin_assets


def write_model(asset: Path, *, model_id: int = 0) -> None:
    (asset / "visual").mkdir(parents=True)
    (asset / "collision").mkdir(parents=True)
    (asset / "visual" / f"base{model_id}.glb").write_bytes(b"visual")
    (asset / "collision" / f"base{model_id}.glb").write_bytes(b"collision")
    (asset / f"model_data{model_id}.json").write_text(
        json.dumps({"scale": [0.05, 0.05, 0.05], "extents": [1.0, 2.0, 1.0]}),
        encoding="utf-8",
    )


def test_catalog_scans_real_paths_and_is_reproducible(tmp_path: Path) -> None:
    root = tmp_path / "RoboTwin"
    can = root / "assets" / "objects" / "071_can"
    write_model(can)
    missing = root / "assets" / "objects" / "999_missing"
    missing.mkdir(parents=True)
    overrides = tmp_path / "overrides.yml"
    overrides.write_text(
        """schema_version: robotwin.asset_overrides.v1
assets:
  071_can:
    category: can
    aliases: [can, cola can]
    models:
      \"0\":
        stable_pose_id: robotwin_can_upright
        stable_orientation_wxyz: [0.70710678, 0.70710678, 0.0, 0.0]
        z_policy: origin_on_table
""",
        encoding="utf-8",
    )

    first, first_missing = scan_robotwin_assets(root, overrides_path=overrides, source_commit="abc123")
    second, second_missing = scan_robotwin_assets(root, overrides_path=overrides, source_commit="abc123")

    assert first.digest() == second.digest()
    assert first_missing == second_missing
    assert [entry.asset_id for entry in first.entries] == ["071_can", "999_missing"]
    entry = first.entries[0]
    assert entry.available is True
    assert entry.load_type == "rigid"
    assert Path(entry.asset_path).is_absolute()
    assert Path(entry.models[0].visual_path).is_file()
    assert Path(entry.models[0].collision_path).is_file()
    assert entry.models[0].dimensions_m == (0.05, 0.05, 0.1)
    assert entry.models[0].stable_pose_id == "robotwin_can_upright"
    assert entry.models[0].stable_orientation_wxyz is not None
    assert abs(entry.models[0].stable_orientation_wxyz[0] - 2**-0.5) < 1e-7
    assert abs(entry.models[0].stable_orientation_wxyz[1] - 2**-0.5) < 1e-7

    missing_entry = first.entries[1]
    assert missing_entry.available is False
    assert "supported_loader" in missing_entry.availability_reasons
    assert first_missing["entry_count"] == 2
    assert first_missing["available_entry_count"] == 1


def test_catalog_requires_collision_dimensions_and_stable_pose(tmp_path: Path) -> None:
    root = tmp_path / "RoboTwin"
    asset = root / "assets" / "objects" / "001_bottle"
    (asset / "visual").mkdir(parents=True)
    (asset / "visual" / "base0.glb").write_bytes(b"visual")
    (asset / "model_data0.json").write_text("{}", encoding="utf-8")
    catalog, report = scan_robotwin_assets(root)
    entry = catalog.entries[0]
    assert entry.available is False
    assert set(entry.availability_reasons) >= {"collision_mesh", "dimensions_m", "scale", "stable_pose"}
    assert report["entries"][0]["asset_id"] == "001_bottle"
