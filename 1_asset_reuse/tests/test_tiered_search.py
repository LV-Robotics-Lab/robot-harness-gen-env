from pathlib import Path

from agenticsim.openxsim.assets import AssetCandidate

from lib.a1_providers import Tier, load_providers, tiered_search


def cand(cid, provider="fake", fmt="usd"):
    return AssetCandidate(
        candidate_id=cid,
        name=cid,
        category="x",
        download_url=f"https://x/{cid}",
        source_page="https://x",
        format=fmt,
        provider=provider,
        license="unknown",
        score=1.0,
        metadata={"key": cid, "size_bytes": 1},
    )


class FakeProvider:
    def __init__(self, name, results=None, err=None):
        self.name, self.results, self.err = name, results or [], err

    def search(self, query, limit=20):
        if self.err:
            raise RuntimeError(self.err)
        return self.results


def test_tier0_hit_stops_everything():
    t1 = FakeProvider("t1", [cand("a")])
    res = tiered_search(
        [Tier(0, FakeProvider("t0", [cand("local")])), Tier(1, t1)],
        "cup",
        viable_fn=lambda c: True,
    )
    assert res["tier0_hit"].candidate_id == "local"
    assert res["tiers_consulted"] == [0]


def test_descends_until_viable():
    tiers = [
        Tier(0, FakeProvider("t0", [])),
        Tier(1, FakeProvider("t1", [cand("bad")])),
        Tier(2, FakeProvider("t2", [cand("good")])),
    ]
    res = tiered_search(tiers, "cup", viable_fn=lambda c: c.candidate_id == "good")
    assert res["tiers_consulted"] == [0, 1, 2]
    assert [c.candidate_id for c in res["candidates"]] == ["good"]


def test_provider_error_recorded_and_continues():
    tiers = [
        Tier(0, FakeProvider("t0", [])),
        Tier(1, FakeProvider("boom", err="down")),
        Tier(2, FakeProvider("t2", [cand("good")])),
    ]
    res = tiered_search(tiers, "cup", viable_fn=lambda c: True)
    assert res["candidates"]
    assert any("down" in e["error"] for e in res["provider_errors"])


def test_load_providers_from_config(tmp_path):
    cfg = {
        "globals": {"top_k": 3},
        "providers": {
            "robotwin_local": {
                "enabled": True,
                "tier": 0,
                "catalog": str(
                    Path(__file__).parent / "fixtures" / "mini_catalog.json"
                ),
            },
            "nvidia_server": {
                "enabled": True,
                "tier": 1,
                "prefixes": ["P"],
                "index_path": str(tmp_path / "idx.json"),
            },
            "github_tree": {"enabled": False, "tier": 2, "repositories": []},
            "github_discovery": {"enabled": False, "tier": 3},
        },
    }
    tiers, g = load_providers(cfg)
    assert [t.tier for t in tiers] == [0, 1]
    assert g["top_k"] == 3
