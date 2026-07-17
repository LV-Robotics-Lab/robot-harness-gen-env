from __future__ import annotations

from pathlib import Path

from scene_gen.catalog import load_catalog
from scene_gen.grounding import ground_object
from scene_gen.parser import parse_rule_based

ROOT = Path(__file__).resolve().parents[2]


def test_grounding_selects_real_usable_models_without_inventing_ids() -> None:
    catalog = load_catalog(ROOT / "tests" / "fixtures" / "asset_catalog.json")
    spec = parse_rule_based("A red can is left of a plastic basket.", seed=41)
    can = ground_object(spec.objects[0], catalog, seed=spec.seed)
    basket = ground_object(spec.objects[1], catalog, seed=spec.seed)

    assert can.entry.asset_id == "071_can"
    assert can.model.model_id == 0
    assert can.model.usable is True
    assert can.model.collision_path
    assert "color metadata unknown; query red preserved" in can.reasons
    assert basket.entry.asset_id == "110_basket"
    assert basket.model.model_id == 1
    assert basket.model.stable_orientation_wxyz == (0.7071067811865476, 0.7071067811865476, 0.0, 0.0)


def test_grounding_is_reproducible_for_fixed_catalog_query_and_seed() -> None:
    catalog = load_catalog(ROOT / "tests" / "fixtures" / "asset_catalog.json")
    query = parse_rule_based("A cup is on the table.", seed=7).objects[0]
    first = ground_object(query, catalog, seed=7)
    second = ground_object(query, catalog, seed=7)
    assert (first.entry.asset_id, first.model.model_id, first.score, first.reasons) == (
        second.entry.asset_id,
        second.model.model_id,
        second.score,
        second.reasons,
    )
