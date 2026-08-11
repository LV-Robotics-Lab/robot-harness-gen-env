"""Tier-walk semantics that the 2026-08-11 sandbox run proved wrong in the
original design: phrase-exact tier-0 reuse, and gate-driven fallthrough."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json

from agenticsim.openxsim.assets import AssetCandidate  # noqa: E402

from lib.a1_providers import RoboTwinLocalProvider, Tier, tiered_search  # noqa: E402


def _catalog(tmp_path):
    cat = tmp_path / "cat.json"
    cat.write_text(
        json.dumps(
            {
                "entries": [
                    {"asset_id": "302_can", "category": "can", "semantic_name": "can",
                     "aliases": ["can"], "asset_path": "/x/302_can"},
                    {"asset_id": "021_cup", "category": "cup", "semantic_name": "cup",
                     "aliases": ["cup", "mug"], "asset_path": "/x/021_cup"},
                    {"asset_id": "303_box", "category": "box", "semantic_name": "box",
                     "aliases": ["box", "storage box"], "asset_path": "/x/303_box"},
                ]
            }
        )
    )
    return RoboTwinLocalProvider(cat)


def test_phrase_reuse_does_not_fire_on_shared_tokens(tmp_path):
    """The real incident: category "trash bin" with alias "garbage can" was
    declared reused_local because the TOKEN "can" equals 302_can's name --
    so acquisition was silently skipped for an asset the pool does not have.
    A reuse claim must match a whole requested phrase, not a shared word."""
    p = _catalog(tmp_path)
    assert p.search_phrases(["trash bin", "garbage can"]) == []
    # whole-phrase matches still work, including multi-word aliases
    assert [c.name for c in p.search_phrases(["cup"])] == ["021_cup"]
    assert [c.name for c in p.search_phrases(["storage box"])] == ["303_box"]


class _Stub:
    def __init__(self, name, cands):
        self.name, self._cands, self.last_stats, self.last_errors = name, cands, {}, []

    def search(self, query, limit=20):
        return self._cands[:limit]


def _cand(name, provider):
    return AssetCandidate(
        candidate_id=f"{provider}:{name}", name=name, category="x",
        download_url="https://e/x", source_page="https://e/x", format="usd",
        provider=provider, license="unknown", score=1.0, metadata={},
    )


def test_gate_rejection_walks_down_to_the_next_tier(tmp_path):
    """"Hit and stop" was designed for a precise lexical channel; the visual
    channel answers EVERY query, so a tier can look successful while holding
    only wrong objects. When the gate refutes a whole tier, the walk must
    continue to the less-trusted tier that actually has the thing."""
    t1 = _Stub("nvidia_server", [_cand("wrong_object.usd", "nvidia_server")])
    t2 = _Stub("github_tree", [_cand("Lantern.glb", "github_tree")])
    seen = []

    def accept(tier_no, viable):
        seen.append((tier_no, [c.name for c in viable]))
        return [c for c in viable if c.name == "Lantern.glb"]

    res = tiered_search(
        [Tier(1, t1), Tier(2, t2)], "lantern",
        viable_fn=lambda c: True, accept_fn=accept,
    )
    assert [c.name for c in res["accepted"]] == ["Lantern.glb"]
    assert seen == [(1, ["wrong_object.usd"]), (2, ["Lantern.glb"])]
    assert res["tiers_consulted"] == [1, 2]


def test_no_accept_fn_keeps_the_old_stop_at_first_viable(tmp_path):
    t1 = _Stub("nvidia_server", [_cand("a.usd", "nvidia_server")])
    t2 = _Stub("github_tree", [_cand("b.glb", "github_tree")])
    res = tiered_search([Tier(1, t1), Tier(2, t2)], "q", viable_fn=lambda c: True)
    assert [c.name for c in res["accepted"]] == ["a.usd"]
    assert res["tiers_consulted"] == [1]
