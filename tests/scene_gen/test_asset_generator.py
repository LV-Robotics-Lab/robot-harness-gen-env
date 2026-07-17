from __future__ import annotations

import json
from pathlib import Path

from scene_gen.asset_generator import ensure_assets_for_scene
from scene_gen.catalog import AssetCatalog, scan_robotwin_assets
from scene_gen.parser import parse_rule_based
from scene_gen.solver import solve_scene


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
