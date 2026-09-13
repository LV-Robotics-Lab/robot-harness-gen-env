"""Differential test: the inverted index must not change what search returns.

An optimisation that quietly reorders results is worse than no optimisation --
the ranking feeds an identity gate and then a permanent pool entry. So the fast
path is checked against a straightforward full-scan reference over the corpus's
entire vocabulary, not a handful of hand-picked queries.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.a1_providers import (  # noqa: E402
    NON_OBJECT,
    NvidiaAssetServerProvider,
    _tokens,
    _words,
    boundary_hits,
)

KEYS = [
    ("P/Props/YCB/025_mug.usd", 10),
    ("P/Props/YCB/024_bowl.usd", 10),
    ("P/Props/YCB/035_power_drill.usd", 10),
    ("P/Props/Boxes/sm_whitecorrugatedbox_b04_brown_01.usd", 10),
    ("P/Props/Boxes/sm_whitecorrugatedbox_b17_brown_01.usd", 10),
    ("P/Props/Parts/_004_KFLK.usd", 10),
    ("P/Props/Parts/_00_KFPK.usd", 10),
    ("P/Props/Mats/Plastic_Red_A.usd", 10),  # dropped by corpus hygiene
    ("P/Props/YCB/.thumbs/256x256/025_mug.usd.png", 10),  # not a candidate
]


def provider(tmp_path):
    return NvidiaAssetServerProvider(
        ["P/Props"], tmp_path / "i.json", list_keys_fn=lambda *_a, **_k: KEYS
    )


def reference(query, limit=20):
    """The pre-index implementation, kept here as the oracle."""
    toks = _tokens(query)
    out = []
    for key, _size in KEYS:
        base = key.rsplit("/", 1)[-1]
        if ".thumbs" in key or not base.lower().endswith(".usd"):
            continue
        stem = base[:-4]
        if NON_OBJECT.search(stem):
            continue
        hits = boundary_hits(toks, stem)
        if toks and not hits:
            continue
        out.append((float(hits), f"nvidia:{key}"))
    out.sort(key=lambda kv: (-kv[0], kv[1]))
    return out[:limit]


def vocabulary():
    words = set()
    for key, _ in KEYS:
        base = key.rsplit("/", 1)[-1]
        if base.lower().endswith(".usd"):
            words |= _words(base[:-4])
    return sorted(w for w in words if len(w) > 1)


def test_inverted_index_matches_full_scan_over_whole_vocabulary(tmp_path):
    p = provider(tmp_path)
    queries = vocabulary() + [
        "mug",
        "power drill",
        "corrugated brown",
        "b04",
        "00",  # digit-prefix rule: must reach _004 and _00 exactly as before
        "004",
        "mug bowl drill",
    ]
    for q in queries:
        fast = [(c.score, c.candidate_id) for c in p.search(q, limit=20)]
        assert fast == reference(q, limit=20), q


def test_word_posts_under_every_digit_prefix(tmp_path):
    """`_004` is reachable as 004, 00 and 0 -- boundary_hits accepts any prefix
    whose remainder is digits, so the postings must carry all of them. Missing
    this made 32 of 407 differential queries disagree."""
    p = provider(tmp_path)
    assert [c.name for c in p.search("004")] == ["_004_KFLK.usd"]
    assert {c.name for c in p.search("00")} == {"_004_KFLK.usd", "_00_KFPK.usd"}


def test_query_without_usable_tokens_returns_nothing(tmp_path):
    """Deliberate divergence from the old implementation, which returned the
    ENTIRE corpus at score 0 when _tokens dropped every token (single
    characters). Handing hundreds of arbitrary candidates to the identity gate
    is strictly worse than admitting the query was unusable."""
    p = provider(tmp_path)
    assert p.search("0") == []
    assert p.search("a") == []
    assert p.last_stats["matched"] == 0


def test_index_is_parsed_once_not_per_query(tmp_path):
    """The index used to be re-read and re-parsed on every single search --
    a third of query cost spent re-learning something unchanged."""
    calls = []
    prov = NvidiaAssetServerProvider(
        ["P/Props"],
        tmp_path / "i2.json",
        list_keys_fn=lambda *_a, **_k: (calls.append(1), KEYS)[1],
    )
    for _ in range(5):
        prov.search("mug")
    assert len(calls) == 1
