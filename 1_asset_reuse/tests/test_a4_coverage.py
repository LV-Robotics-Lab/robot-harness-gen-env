import json
from pathlib import Path

from lib import a4_coverage as a4

FIX = Path(__file__).parent / "fixtures" / "mini_catalog.json"


def test_extract_needs_red_mug():
    spec, needs = a4.extract_needs("Place a red mug on the table.", seed=42)
    assert needs[0]["category"] == "cup" and needs[0]["color"] == "red"


def test_coverage_covered_and_gap():
    spec, _ = a4.extract_needs("Place a red mug on the table.", seed=42)
    recs = a4.check_coverage(spec, FIX)
    assert recs[0]["status"] == "covered" and recs[0]["asset_id"]
    spec2, _ = a4.extract_needs("Place a hammer on the table.", seed=42)
    recs2 = a4.check_coverage(spec2, FIX)
    assert recs2[0]["status"] == "gap"


def test_gaps_to_entries_dedup_and_color():
    records = [
        {
            "object_id": "a",
            "category": "bowl",
            "color": None,
            "status": "gap",
            "detail": "x",
        },
        {
            "object_id": "b",
            "category": "bowl",
            "color": None,
            "status": "gap",
            "detail": "x",
        },
        {
            "object_id": "c",
            "category": "cup",
            "color": "blue",
            "status": "gap",
            "detail": "x",
        },
        {
            "object_id": "d",
            "category": "cup",
            "color": None,
            "status": "covered",
            "asset_id": "301_cup",
            "model_id": 0,
            "score": 100.0,
        },
    ]
    entries = a4.gaps_to_entries(records)
    assert entries == [
        {"category": "bowl", "aliases": ["bowl"]},
        {"category": "cup", "aliases": ["cup"], "colors": ["blue"]},
    ]


def test_write_coverage_report(tmp_path):
    a4.write_coverage_report(
        tmp_path / "c.json",
        "p",
        42,
        [
            {
                "object_id": "a",
                "category": "bowl",
                "color": None,
                "status": "gap",
                "detail": "x",
            }
        ],
    )
    d = json.loads((tmp_path / "c.json").read_text())
    assert (
        d["schema"] == "envgen.scene_coverage.v1" and d["objects"][0]["status"] == "gap"
    )
