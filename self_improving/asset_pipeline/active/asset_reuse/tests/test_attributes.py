"""属性检索（颜色/材质）的行为约定。

上游语义决定了这里的每一条：entry.colors 一旦非空，颜色不符的资产会被
grounding 直接拒收；所以"标错"比"不标"贵得多，测试盯的就是这条边界。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import a4_coverage as a4  # noqa: E402
from lib import a6_verify as a6  # noqa: E402


class _Cand:
    def __init__(self, name="thing.usd", thumb=None):
        self.candidate_id = "src/" + name
        self.name = name
        self.metadata = {"thumbnail": str(thumb) if thumb else None}


def png(tmp_path, name="t.png"):
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\n\x1a\n")  # only existence is checked here
    return p


def _infer_factory(match=True, obj="cup", colors=(), materials=()):
    def infer(_path, prompt):
        if "two names for the same kind" in prompt:
            return '{"same_kind": true}'
        if "plausible" in prompt:
            return '{"plausible": true, "suggests": null}'
        if "Question: is the object" in prompt:
            return json.dumps(
                {
                    "match": match,
                    "object": obj,
                    "colors": list(colors),
                    "materials": list(materials),
                    "confidence": "high",
                }
            )
        return json.dumps({"object": obj})  # open second opinion

    return infer


def test_requested_colour_rejects_a_differently_coloured_match(tmp_path):
    r = a6.verify_candidate(
        _Cand("cup.usd", png(tmp_path)),
        "cup",
        infer=_infer_factory(colors=("blue",), obj="cup"),
        want_color="red",
    )
    assert r["verdict"] == a6.MISMATCH
    assert r["attribute_veto"] == a6.ATTR_MISMATCH
    assert r["attribute_check"]["color"] == "mismatch"


def test_requested_colour_accepts_a_neighbouring_shade(tmp_path):
    r = a6.verify_candidate(
        _Cand("cup.usd", png(tmp_path)),
        "cup",
        infer=_infer_factory(colors=("crimson",), obj="cup"),
        want_color="red",
    )
    assert r["verdict"] == a6.MATCH
    assert r["attribute_check"]["color"] == "ok"


def test_unknown_colour_is_not_taken_as_agreement(tmp_path):
    """模型没报颜色 != 颜色符合。放过它就等于"随便给一个"。"""
    r = a6.verify_candidate(
        _Cand("cup.usd", png(tmp_path)), "cup", infer=_infer_factory(colors=()), want_color="red"
    )
    assert r["verdict"] == a6.MISMATCH
    assert r["attribute_check"]["color"] == "unknown"


def test_material_synonyms_agree(tmp_path):
    r = a6.verify_candidate(
        _Cand("bowl.usd", png(tmp_path)),
        "bowl",
        infer=_infer_factory(obj="bowl", colors=("brown",), materials=("wooden",)),
        want_color="brown",
        want_material="wood",
    )
    assert r["verdict"] == a6.MATCH
    assert r["attribute_check"] == {"color": "ok", "material": "ok"}


def test_no_attribute_requested_leaves_verdict_untouched(tmp_path):
    r = a6.verify_candidate(
        _Cand("cup.usd", png(tmp_path)), "cup", infer=_infer_factory(colors=("blue",))
    )
    assert r["verdict"] == a6.MATCH
    assert "attribute_check" not in r


class _Obj:
    def __init__(self, oid, category, color=None, material=None):
        self.object_id = oid
        self.category = category
        self.color = color
        self.material = material


def test_gap_entry_carries_colour_and_material_to_acquisition():
    records = [
        {
            "object_id": "bowl_1",
            "category": "bowl",
            "color": "red",
            "material": "wood",
            "status": "gap",
        }
    ]
    entries = a4.gaps_to_entries(records)
    assert entries[0]["colors"] == ["red"]
    assert entries[0]["materials"] == ["wood"]


def test_same_category_different_attributes_are_separate_acquisitions():
    """红碗和木碗是两次采购，不能被去重合并成一次。"""
    records = [
        {"object_id": "b1", "category": "bowl", "color": "red", "status": "gap"},
        {"object_id": "b2", "category": "bowl", "material": "wood", "status": "gap"},
    ]
    assert len(a4.gaps_to_entries(records)) == 2
