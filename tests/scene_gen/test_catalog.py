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
        footprint_shape: circle
        support_surface_shape: circle
        support_surface_dimensions_m: [0.04, 0.04]
        support_surface_z_offset_m: 0.08
        support_margin_m: 0.004
        support_spawn_clearance_m: 0.002
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
    assert entry.models[0].footprint_shape == "circle"
    assert entry.models[0].support_surface_shape == "circle"
    assert entry.models[0].support_surface_dimensions_m == (0.04, 0.04)
    assert entry.models[0].support_surface_z_offset_m == 0.08
    assert entry.models[0].support_margin_m == 0.004
    assert entry.models[0].support_spawn_clearance_m == 0.002

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


def test_catalog_scans_nested_articulated_models_and_joint_limits(tmp_path: Path) -> None:
    root = tmp_path / "RoboTwin"
    model = root / "assets" / "objects" / "036_cabinet" / "46653"
    model.mkdir(parents=True)
    (model / "mobility.urdf").write_text(
        """<robot name="cabinet">
  <link name="base"/><link name="drawer_a"/><link name="drawer_b"/>
  <joint name="drawer_1" type="prismatic"><parent link="base"/><child link="drawer_a"/><limit lower="0" upper="0.66" effort="1" velocity="1"/></joint>
  <joint name="drawer_2" type="prismatic"><parent link="base"/><child link="drawer_b"/><limit lower="0" upper="0.50" effort="1" velocity="1"/></joint>
</robot>""",
        encoding="utf-8",
    )
    (model / "model_data.json").write_text(
        json.dumps({"scale": 0.27, "init_qpos": [0.0, 0.0]}), encoding="utf-8"
    )
    (model / "bounding_box.json").write_text(
        json.dumps({"min": [-0.4, -0.8, -0.45], "max": [0.4, 0.8, 0.45]}),
        encoding="utf-8",
    )
    overrides = tmp_path / "overrides.yml"
    overrides.write_text(
        """schema_version: robotwin.asset_overrides.v1
assets:
  036_cabinet:
    category: cabinet
    aliases: [cabinet]
    models:
      "46653":
        stable_pose_id: upright
        z_policy: origin_on_table
        articulation_closed_qpos: [0.0, 0.0]
        articulation_open_qpos: [0.5, 0.4]
""",
        encoding="utf-8",
    )
    catalog, _ = scan_robotwin_assets(root, overrides_path=overrides)
    entry = catalog.entries[0]
    assert entry.load_type == "urdf"
    assert entry.available is True
    assert [item.model_id for item in entry.models] == [46653]
    scanned = entry.models[0]
    assert Path(scanned.urdf_path).is_file()
    assert scanned.dimensions_m == (0.21600000000000003, 0.24300000000000002, 0.43200000000000005)
    assert [joint.name for joint in scanned.articulation_joints] == ["drawer_1", "drawer_2"]
    assert scanned.articulation_closed_qpos == (0.0, 0.0)
    assert scanned.articulation_open_qpos == (0.135, 0.10800000000000001)
    assert scanned.articulation_joints[0].upper == 0.17820000000000003
