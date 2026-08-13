from __future__ import annotations

import json
from pathlib import Path

from scene_gen.asset_generator import ensure_assets_for_scene
from scene_gen.catalog import AssetCatalog, scan_robotwin_assets
from scene_gen.parser import parse_rule_based
from scene_gen.solver import solve_scene
from scene_gen.support_geometry import support_footprint_margin


def test_catalog_miss_generates_replayable_robotwin_proxy_with_provenance(tmp_path: Path) -> None:
    objects_root = tmp_path / "RoboTwin" / "assets" / "objects"
    catalog = AssetCatalog(
        robotwin_root=str(tmp_path / "RoboTwin"),
        objects_root=str(objects_root),
        entries=(),
    )
    spec = parse_rule_based("Place a purple hexagonal pedestal on the table.", seed=77)
    effective, report = ensure_assets_for_scene(spec, catalog)
    assert report["status"] == "generated"
    assert report["generated_count"] == 1
    entry = effective.entries[0]
    assert entry.category == "hexagonal_pedestal"
    assert entry.available is True
    assert "procedural_generated" in entry.source_notes
    assert Path(entry.models[0].visual_path).is_file()
    assert Path(entry.models[0].collision_path).is_file()
    provenance_path = Path(entry.asset_path) / "generation_provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    assert provenance["geometry_family"] == "hexagonal_prism"
    assert provenance["geometry_fidelity"].endswith("bounded_proxy")

    resolved = solve_scene(spec, effective)
    generated = resolved.objects[0]
    assert generated.asset_id == entry.asset_id
    assert generated.asset_provenance == "procedural_generated"
    assert generated.generation_metadata_path == str(provenance_path.resolve())

    rescanned, _ = scan_robotwin_assets(tmp_path / "RoboTwin")
    assert rescanned.entries[0].available is True
    assert rescanned.entries[0].category == "hexagonal_pedestal"
    assert "procedural_generated" in rescanned.entries[0].source_notes


def test_generation_is_deterministic_and_reuses_identity(tmp_path: Path) -> None:
    objects_root = tmp_path / "RoboTwin" / "assets" / "objects"
    catalog = AssetCatalog(
        robotwin_root=str(tmp_path / "RoboTwin"),
        objects_root=str(objects_root),
        entries=(),
    )
    spec = parse_rule_based("Place a purple hexagonal pedestal on the table.", seed=4)
    first, first_report = ensure_assets_for_scene(spec, catalog)
    second, second_report = ensure_assets_for_scene(spec, catalog)
    assert first.entries[0].asset_id == second.entries[0].asset_id
    assert first.entries[0].models[0].visual_path == second.entries[0].models[0].visual_path
    assert first_report["generated"][0]["files"] == second_report["generated"][0]["files"]


def _catalog_with_local_block_meshes(tmp_path: Path) -> AssetCatalog:
    catalog_path = Path(__file__).resolve().parents[1] / "fixtures" / "asset_catalog.json"
    catalog = AssetCatalog.model_validate_json(catalog_path.read_text(encoding="utf-8"))
    asset_dir = tmp_path / "RoboTwin" / "assets" / "objects" / "004_fluted-block"
    visual = asset_dir / "visual" / "base0.glb"
    collision = asset_dir / "collision" / "base0.glb"
    visual.parent.mkdir(parents=True)
    collision.parent.mkdir(parents=True)
    visual.write_bytes(b"test visual mesh")
    collision.write_bytes(b"test collision mesh")
    metadata = asset_dir / "model_data0.json"
    metadata.write_text(
        json.dumps(
            {
                "extents": [0.20505566895008087, 0.16323167085647583, 0.20139166712760928],
                "scale": [0.45, 0.4, 0.45],
            }
        ),
        encoding="utf-8",
    )
    entries = []
    for entry in catalog.entries:
        if entry.asset_id != "004_fluted-block":
            entries.append(entry)
            continue
        model = entry.models[0].model_copy(
            update={
                "model_path": str(asset_dir),
                "metadata_path": str(metadata),
                "visual_path": str(visual),
                "collision_path": str(collision),
            }
        )
        entries.append(
            entry.model_copy(
                update={"asset_path": str(asset_dir), "models": (model,)}
            )
        )
    return catalog.model_copy(
        update={
            "robotwin_root": str(tmp_path / "RoboTwin"),
            "objects_root": str(tmp_path / "RoboTwin" / "assets" / "objects"),
            "entries": tuple(entries),
        }
    )


def test_incompatible_block_is_derived_at_a_uniform_scale_and_fits_plate(
    tmp_path: Path,
) -> None:
    catalog = _catalog_with_local_block_meshes(tmp_path)
    spec = parse_rule_based("Place a red block on top of a plate.", seed=31)
    effective, report = ensure_assets_for_scene(spec, catalog)

    assert report["status"] == "generated"
    assert report["derived_scale_count"] == 1
    assert report["procedural_count"] == 0
    provenance = report["generated"][0]
    assert provenance["generation_kind"] == "derived_scaled_proxy"
    assert provenance["source_asset_id"] == "004_fluted-block"
    assert 0.58 <= provenance["uniform_scale_factor"] <= 0.61
    assert provenance["requested_color"] == "red"

    resolved = solve_scene(spec, effective)
    objects = {item.object_id: item for item in resolved.objects}
    block = objects["block_1"]
    plate = objects["plate_1"]
    assert block.asset_provenance == "derived_scaled_proxy"
    assert block.derived_from_asset_id == "004_fluted-block"
    assert block.derived_from_model_id == 0
    assert block.uniform_scale_factor == provenance["uniform_scale_factor"]
    assert block.mesh_scale == (1.0, 1.0, 1.0)
    assert block.dimensions_m == tuple(provenance["dimensions_m"])
    margin = support_footprint_margin(
        source_dimensions_m=block.dimensions_m,
        source_yaw=block.pose.yaw_rad,
        source_shape=block.footprint_shape,
        target_surface_dimensions_m=plate.support_surface_dimensions_m,
        target_yaw=plate.pose.yaw_rad,
        target_surface_shape=plate.support_surface_shape,
        dx=block.pose.position_m[0] - plate.pose.position_m[0],
        dy=block.pose.position_m[1] - plate.pose.position_m[1],
    )
    assert margin >= plate.support_margin_m


def test_runtime_unstable_block_uses_dimension_preserving_proxy_on_table(
    tmp_path: Path,
) -> None:
    catalog = _catalog_with_local_block_meshes(tmp_path)
    spec = parse_rule_based(
        "A blue cup is in front of a wooden block and both objects are near each other.",
        seed=7,
    )
    effective, report = ensure_assets_for_scene(spec, catalog)

    assert report["derived_scale_count"] == 1
    assert report["scaled_proxy_count"] == 0
    assert report["stabilized_proxy_count"] == 1
    provenance = report["generated"][0]
    assert provenance["source_asset_id"] == "004_fluted-block"
    assert provenance["uniform_scale_factor"] == 1.0
    assert provenance["dimensions_m"] == list(
        next(
            entry for entry in catalog.entries if entry.asset_id == "004_fluted-block"
        ).models[0].dimensions_m
    )
    assert provenance["adaptation_reasons"] == ["source_runtime_instability"]

    resolved = solve_scene(spec, effective)
    block = next(item for item in resolved.objects if item.object_id == "block_1")
    assert block.asset_provenance == "derived_scaled_proxy"
    assert block.uniform_scale_factor == 1.0


def test_proxy_mesh_bounds_match_declared_dimensions(tmp_path: Path) -> None:
    catalog = _catalog_with_local_block_meshes(tmp_path)
    spec = parse_rule_based("Place a red block on top of a plate.", seed=31)
    _, report = ensure_assets_for_scene(spec, catalog)
    provenance = report["generated"][0]
    visual_path = Path(provenance["files"]["visual"]["path"])
    vertices = [
        tuple(float(value) for value in line.split()[1:])
        for line in visual_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("v ")
    ]
    bounds = tuple(
        max(vertex[axis] for vertex in vertices)
        - min(vertex[axis] for vertex in vertices)
        for axis in range(3)
    )
    expected = tuple(provenance["dimensions_m"])
    assert all(abs(actual - declared) < 1e-8 for actual, declared in zip(bounds, expected))
