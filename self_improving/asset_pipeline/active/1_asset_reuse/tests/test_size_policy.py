"""方案 C：类目典型尺寸 × 桌面视图三档裁决的行为约定。

核心不变量：账本存"世界的真"（类目典型尺寸），"放不放得上桌"是视图的
裁决——refuse 不是资产的属性，是 tabletop 视图对该类目的属性。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import a4_coverage as a4  # noqa: E402
from lib import conventions  # noqa: E402


def test_small_category_enters_at_real_size():
    policy, d = a4.resolve_size_policy("cup")
    assert policy == "category:0.1"
    assert d["decision"] == "real_size"


def test_moderate_oversize_is_capped_with_marker():
    policy, d = a4.resolve_size_policy("television")
    assert policy == "capped:0.42"
    assert d["decision"] == "capped_to_view"
    assert d["typical_m"] == 0.6


def test_absurd_oversize_is_refused_not_miniaturized():
    policy, d = a4.resolve_size_policy("sofa")
    assert policy is None
    assert d["decision"] == "refuse_oversize_for_view"


def test_unknown_category_keeps_old_default_and_says_so():
    policy, d = a4.resolve_size_policy("frobnicator")
    assert policy == "absolute:0.25"
    assert d["decision"] == "default_unknown"


def test_gap_entry_carries_policy_or_refusal():
    records = [
        {"object_id": "a", "category": "cup", "status": "gap"},
        {"object_id": "b", "category": "sofa", "status": "gap"},
    ]
    entries = a4.gaps_to_entries(records)
    by_cat = {e["category"]: e for e in entries}
    assert by_cat["cup"]["size_policy"] == "category:0.1"
    assert by_cat["sofa"].get("oversize_refusal") is True
    assert "size_policy" not in by_cat["sofa"]


def test_resolve_size_understands_new_prefixes():
    for policy, target in (("category:0.42", 0.42), ("capped:0.42", 0.42)):
        r = conventions.resolve_size("hanger", [43.7, 0.5, 2.0], None, policy)
        assert r["mode"] == policy
        assert abs(r["scale"] - target / 43.7) < 1e-9
        assert r["verdict"] == "scaled"
