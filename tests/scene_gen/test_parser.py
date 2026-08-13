from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scene_gen.parser import parse_provider_payload, parse_rule_based
from scene_gen.schema import RelationType, SceneSpecError

ROOT = Path(__file__).resolve().parents[2]


def test_all_bilingual_golden_prompts_are_stable_and_schema_valid() -> None:
    golden = json.loads(
        (ROOT / "tests" / "fixtures" / "golden_prompts.json").read_text(encoding="utf-8")
    )
    for case in golden["valid"]:
        first = parse_rule_based(case["prompt"], seed=31)
        second = parse_rule_based(case["prompt"], seed=31)
        assert first.language == case["language"]
        assert first.digest() == second.digest()
        assert all(
            item.target == "table"
            for item in first.relations
            if item.relation == RelationType.ON_TABLE
        )
        assert len(
            [
                item
                for item in first.relations
                if item.relation
                in {RelationType.ON_TABLE, RelationType.ON_TOP_OF, RelationType.INSIDE}
            ]
        ) == len(first.objects)


def test_all_invalid_golden_prompts_are_rejected() -> None:
    golden = json.loads(
        (ROOT / "tests" / "fixtures" / "golden_prompts.json").read_text(encoding="utf-8")
    )
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
    distance = next(
        item for item in spec.relations if item.relation == RelationType.DISTANCE_AT_LEAST
    )
    assert distance.min_distance_m == 0.2
    assert spec.frame.y_axis == "front"


def test_rule_parser_preserves_mustard_bottle_semantic_category() -> None:
    spec = parse_rule_based("Place a mustard bottle on the table.", seed=42)

    assert len(spec.objects) == 1
    assert spec.objects[0].object_id == "mustard_bottle_1"
    assert spec.objects[0].category == "mustard_bottle"


def test_rule_parser_preserves_mug_semantic_category() -> None:
    spec = parse_rule_based("Place a mug on the table.", seed=42)

    assert len(spec.objects) == 1
    assert spec.objects[0].object_id == "mug_1"
    assert spec.objects[0].category == "mug"


def test_rule_parser_preserves_coffee_can_semantic_category() -> None:
    spec = parse_rule_based("Place a coffee can on the table.", seed=42)

    assert len(spec.objects) == 1
    assert spec.objects[0].object_id == "coffee_can_1"
    assert spec.objects[0].category == "coffee_can"


def test_rule_parser_preserves_sugar_box_semantic_category() -> None:
    spec = parse_rule_based("Place a sugar box on the table.", seed=42)

    assert len(spec.objects) == 1
    assert spec.objects[0].object_id == "sugar_box_1"
    assert spec.objects[0].category == "sugar_box"


def test_rule_parser_preserves_soft_scrub_semantic_category() -> None:
    for request in (
        "Place a soft scrub bottle on the table.",
        "Place soft scrub on the table.",
    ):
        spec = parse_rule_based(request, seed=42)

        assert len(spec.objects) == 1
        assert spec.objects[0].object_id == "soft_scrub_1"
        assert spec.objects[0].category == "soft_scrub"


def test_rule_parser_preserves_cheez_it_semantic_category() -> None:
    for request in (
        "Place a Cheez-It box on the table.",
        "Place a Cheez It box on the table.",
    ):
        spec = parse_rule_based(request, seed=42)

        assert len(spec.objects) == 1
        assert spec.objects[0].object_id == "cheez_it_1"
        assert spec.objects[0].category == "cheez_it"


def test_rule_parser_preserves_spam_can_semantic_category() -> None:
    spec = parse_rule_based("Place a Spam can on the table.", seed=42)

    assert len(spec.objects) == 1
    assert spec.objects[0].object_id == "spam_can_1"
    assert spec.objects[0].category == "spam_can"


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


def test_parser_emits_stack_inside_and_multi_joint_articulation_semantics() -> None:
    stacked = parse_rule_based("Place a red block on top of a plate.", seed=12)
    assert any(
        item.relation == RelationType.ON_TOP_OF
        and item.source == "block_1"
        and item.target == "plate_1"
        for item in stacked.relations
    )
    assert not any(
        item.relation == RelationType.ON_TABLE and item.source == "block_1"
        for item in stacked.relations
    )

    contained = parse_rule_based("Put an apple inside a basket.", seed=13)
    assert any(
        item.relation == RelationType.INSIDE
        and item.source == "apple_1"
        and item.target == "basket_1"
        for item in contained.relations
    )

    articulated = parse_rule_based("Place a half-open cabinet on the table.", seed=14)
    assert articulated.objects[0].articulation is not None
    assert articulated.objects[0].articulation.state == "partially_open"
    assert articulated.objects[0].articulation.open_fraction == 0.5
    assert articulated.objects[0].articulation.joint_selector == "all_movable"


def test_parser_supports_chinese_stack_inside_and_articulation() -> None:
    stacked = parse_rule_based("把红色方块叠在盘子上。", seed=21)
    assert any(item.relation == RelationType.ON_TOP_OF for item in stacked.relations)
    contained = parse_rule_based("把苹果放进篮子里。", seed=22)
    assert any(item.relation == RelationType.INSIDE for item in contained.relations)
    articulated = parse_rule_based("把柜子的所有抽屉打开一半并放在桌上。", seed=23)
    assert articulated.objects[0].articulation is not None
    assert articulated.objects[0].articulation.open_fraction == 0.5


@pytest.mark.parametrize(
    ("category", "prompt"),
    [
        ("can", "Place two cans on top of a plate."),
        ("bottle", "Place two bottles on top of a plate."),
    ],
)
def test_parser_expands_plural_sources_and_copies_support_relation(
    category: str, prompt: str
) -> None:
    spec = parse_rule_based(prompt, seed=31)
    source_ids = {f"{category}_1", f"{category}_2"}
    assert [(item.object_id, item.category) for item in spec.objects] == [
        (f"{category}_1", category),
        (f"{category}_2", category),
        ("plate_1", "plate"),
    ]
    assert {
        (item.source, item.target)
        for item in spec.relations
        if item.relation == RelationType.ON_TOP_OF
    } == {(source, "plate_1") for source in source_ids}
    assert {
        (item.source, item.target)
        for item in spec.relations
        if item.relation == RelationType.ON_TABLE
    } == {("plate_1", "table")}


def test_parser_copies_attributes_to_every_instance_in_a_quantity_group() -> None:
    spec = parse_rule_based("Place two red cans on top of a plate.", seed=32)
    objects = {item.object_id: item for item in spec.objects}
    assert objects["can_1"].color == "red"
    assert objects["can_2"].color == "red"
    assert objects["plate_1"].color is None


def test_parser_expands_chinese_quantity_group_and_inside_relation() -> None:
    spec = parse_rule_based("把两个杯子放进篮子里。", seed=33)
    assert [item.object_id for item in spec.objects] == ["cup_1", "cup_2", "basket_1"]
    assert {
        (item.source, item.target)
        for item in spec.relations
        if item.relation == RelationType.INSIDE
    } == {("cup_1", "basket_1"), ("cup_2", "basket_1")}
    assert {item.source for item in spec.relations if item.relation == RelationType.ON_TABLE} == {
        "basket_1"
    }


@pytest.mark.parametrize(
    ("prompt", "expected_ids"),
    [
        ("Place one bottle on top of a plate.", ["bottle_1", "plate_1"]),
        (
            "Place three apples inside a basket.",
            ["apple_1", "apple_2", "apple_3", "basket_1"],
        ),
        ("Place two boxes on the table.", ["box_1", "box_2"]),
        ("Place two coffee mugs on the table.", ["mug_1", "mug_2"]),
        ("Place two knives on top of a plate.", ["knife_1", "knife_2", "plate_1"]),
    ],
)
def test_parser_supports_controlled_quantities_and_plural_forms(
    prompt: str, expected_ids: list[str]
) -> None:
    spec = parse_rule_based(prompt, seed=34)
    assert [item.object_id for item in spec.objects] == expected_ids


def test_quantity_expansion_digest_is_deterministic() -> None:
    prompt = "Place two plastic bottles on top of a plate."
    first = parse_rule_based(prompt, seed=35)
    second = parse_rule_based(prompt, seed=35)
    assert first.digest() == second.digest()
    assert [item.material for item in first.objects[:2]] == ["plastic", "plastic"]


@pytest.mark.parametrize(
    "prompt",
    [
        "Place cans on the table.",
        "Place four cans on the table.",
        "Place 2 cans on the table.",
        "Place two can on the table.",
        "Place one cans on the table.",
        "Place two on top of a plate.",
        "Place two widgets on top of a plate.",
        "Place two cans on top of two plates.",
        "Place two bottles respectively on top of two plates.",
        "把四个杯子放进篮子里。",
        "把两个杯子分别放进两个篮子里。",
        (
            "Place three cans, three bottles, three apples, three cups, "
            "and three plates on the table."
        ),
    ],
)
def test_unsupported_or_unbound_quantities_fail_closed(prompt: str) -> None:
    with pytest.raises(SceneSpecError):
        parse_rule_based(prompt, seed=36)
