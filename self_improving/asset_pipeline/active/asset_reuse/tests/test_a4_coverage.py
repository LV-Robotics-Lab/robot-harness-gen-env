import json
from pathlib import Path

from lib import a4_coverage as a4

FIX = Path(__file__).parent / "fixtures" / "mini_catalog.json"


def test_extract_needs_red_mug():
    spec, needs = a4.extract_needs("Place a red mug on the table.", seed=42)
    assert needs[0]["category"] == "mug" and needs[0]["color"] == "red"


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
    # 方案 C（2026-08-15）：已知类目按典型真实尺寸入库（category:<m>），
    # 桌面适配是视图裁决；absolute:0.25 只剩未知类目的诚实缺省。
    assert entries == [
        {
            "category": "bowl",
            "aliases": ["bowl"],
            "size_decision": {
                "typical_m": 0.16,
                "confidence": "high",
                "decision": "real_size",
            },
            "size_policy": "category:0.16",
        },
        {
            "category": "cup",
            "aliases": ["cup"],
            "size_decision": {
                "typical_m": 0.1,
                "confidence": "high",
                "decision": "real_size",
            },
            "size_policy": "category:0.1",
            "colors": ["blue"],
        },
    ]


def test_mark_acquired_gap_to_covered_becomes_acquired():
    before = [{"object_id": "a", "category": "hammer", "color": None, "status": "gap"}]
    after = [
        {
            "object_id": "a",
            "category": "hammer",
            "color": None,
            "status": "covered",
            "asset_id": "301_hammer",
            "model_id": 0,
            "score": 100.0,
        }
    ]
    result = a4.mark_acquired(before, after)
    assert result[0]["status"] == "acquired"
    assert result[0]["asset_id"] == "301_hammer"
    assert result[0]["model_id"] == 0
    assert result[0]["score"] == 100.0


def test_mark_acquired_always_covered_stays_covered():
    before = [
        {
            "object_id": "a",
            "category": "cup",
            "color": None,
            "status": "covered",
            "asset_id": "301_cup",
            "model_id": 0,
            "score": 100.0,
        }
    ]
    after = [dict(before[0])]
    result = a4.mark_acquired(before, after)
    assert result[0]["status"] == "covered"


def test_mark_acquired_still_gap_stays_gap():
    before = [{"object_id": "a", "category": "hammer", "color": None, "status": "gap"}]
    after = [
        {
            "object_id": "a",
            "category": "hammer",
            "color": None,
            "status": "gap",
            "detail": "still missing",
        }
    ]
    result = a4.mark_acquired(before, after)
    assert result[0]["status"] == "gap"


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
