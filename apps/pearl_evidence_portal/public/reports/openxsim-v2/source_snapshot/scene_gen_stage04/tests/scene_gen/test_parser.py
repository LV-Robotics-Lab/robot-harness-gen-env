from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scene_gen.parser import parse_provider_payload, parse_rule_based
from scene_gen.schema import RelationType, SceneSpecError

ROOT = Path(__file__).resolve().parents[2]


def test_all_bilingual_golden_prompts_are_stable_and_schema_valid() -> None:
    golden = json.loads((ROOT / "data" / "scene_gen" / "golden_prompts.json").read_text(encoding="utf-8"))
    for case in golden["valid"]:
        first = parse_rule_based(case["prompt"], seed=31)
        second = parse_rule_based(case["prompt"], seed=31)
        assert first.language == case["language"]
        assert first.digest() == second.digest()
        assert all(item.target == "table" for item in first.relations if item.relation == RelationType.ON_TABLE)
        assert len([item for item in first.relations if item.relation == RelationType.ON_TABLE]) == len(first.objects)


def test_all_invalid_golden_prompts_are_rejected() -> None:
    golden = json.loads((ROOT / "data" / "scene_gen" / "golden_prompts.json").read_text(encoding="utf-8"))
    for case in golden["invalid"]:
        with pytest.raises((SceneSpecError, ValidationError)):
            parse_rule_based(case["prompt"], seed=31)


def test_direction_and_distance_semantics_match_the_fixed_frame() -> None:
    spec = parse_rule_based(
        "A metal hammer is behind a plastic calculator and at least 0.20 m away.",
        seed=9,
    )
    hammer, calculator = spec.objects
    assert hammer.material == "metal"
    assert calculator.material == "plastic"
    assert any(item.relation == RelationType.BEHIND for item in spec.relations)
    distance = next(item for item in spec.relations if item.relation == RelationType.DISTANCE_AT_LEAST)
    assert distance.min_distance_m == 0.2
    assert spec.frame.y_axis == "front"


def test_provider_payload_cannot_smuggle_backend_fields_or_change_request() -> None:
    spec = parse_rule_based("A can is left of a basket.", seed=4)
    payload = spec.canonical_dict()
    payload["objects"][0]["asset_id"] = "071_can"
    with pytest.raises((SceneSpecError, ValidationError), match="cannot contain"):
        parse_provider_payload(payload, request=spec.request, seed=4)

    changed = spec.canonical_dict()
    changed["request"] = "changed"
    with pytest.raises(SceneSpecError, match="changed the user request"):
        parse_provider_payload(changed, request=spec.request, seed=4)
