"""Prompt normalization + coverage hints. The parser itself is upstream and
read-only; these tests pin OUR layer's contract with it."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import a4_coverage as a4  # noqa: E402


def test_vocab_colliding_compounds_are_joined_seamlessly():
    """The measured failure (web run 2026-08-12): "tissue-box" term-matched
    the vocabulary word "box" across the hyphen boundary and a plain storage
    box was staged. The upstream boundary class is `(?<![a-z0-9])`, so '-'
    AND '_' both leak -- only seamless concatenation survives the scan, with
    the readable forms kept as aliases."""
    p, al = a4.normalize_prompt_ex("Place a tissue-box near the bowl.")
    assert p == "Place a tissuebox near the bowl."
    assert al == {"tissuebox": ["tissue box", "tissue-box"]}
    # underscore form leaks the same way and is joined too
    assert a4.normalize_prompt("grab the tissue_box") == "grab the tissuebox"


def test_non_colliding_compounds_stay_untouched():
    """ "dumbbell-rack" contains no vocabulary word: the upstream free-capture
    already turns it into category dumbbell_rack, so rewriting it would
    BREAK the working path."""
    p, al = a4.normalize_prompt_ex("Place a dumbbell-rack on the table")
    assert p == "Place a dumbbell-rack on the table"
    assert al == {}


def test_relation_wording_is_never_rewritten():
    assert a4.normalize_prompt("Put an apple on-top-of the box.") == (
        "Put an apple on-top-of the box."
    )
    assert a4.normalize_prompt("next-to the plate") == "next-to the plate"


def test_numeric_ranges_untouched():
    assert a4.normalize_prompt("seed 42-45 run") == "seed 42-45 run"


def test_parse_after_normalization_yields_compound_category():
    """End-to-end with the real upstream parser: the compound must arrive as
    its own category (honest gap candidate), not collapse to "box"."""
    spec, needs = a4.extract_needs("Place a tissue-box near the bowl.", seed=42)
    cats = sorted(n["category"] for n in needs)
    assert cats == ["bowl", "tissuebox"]


def test_compound_aliases_reach_acquisition_entries():
    records = [
        {
            "object_id": "tissuebox_1",
            "category": "tissuebox",
            "color": None,
            "status": "gap",
            "detail": "x",
        },
    ]
    _p, al = a4.normalize_prompt_ex("Place a tissue-box near the bowl.")
    entries = a4.gaps_to_entries(records, extra_aliases=al)
    assert entries[0]["aliases"] == ["tissuebox", "tissue box", "tissue-box"]


def test_known_compounds_keep_working():
    spec, needs = a4.extract_needs("Place a dumbbell-rack on the table", seed=42)
    assert [n["category"] for n in needs] == ["dumbbell_rack"]


def test_unusable_same_category_hint():
    """ "no usable candidate" must say when the library HAS the asset and it
    merely awaits validation -- retrieval and validation are different next
    steps (the dumbbell-rack confusion, 2026-08-12)."""
    catalog = {
        "entries": [
            {
                "asset_id": "013_dumbbell-rack",
                "category": "dumbbell_rack",
                "models": [
                    {"model_id": 0, "usable": False, "missing": ["stable_pose"]},
                    {"model_id": 1, "usable": False, "missing": ["stable_pose"]},
                ],
            },
            {
                "asset_id": "020_hammer",
                "category": "hammer",
                "models": [{"model_id": 0, "usable": True, "missing": []}],
            },
        ]
    }
    hints = a4._unusable_same_category(catalog, "dumbbell_rack")
    assert hints == ["013_dumbbell-rack: 0/2 models usable (missing: stable_pose)"]
    assert a4._unusable_same_category(catalog, "hammer") == []
    assert a4._unusable_same_category(catalog, "snowman") == []
