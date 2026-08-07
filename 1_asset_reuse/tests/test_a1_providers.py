import json
from pathlib import Path

from lib.a1_providers import BUCKET, NvidiaAssetServerProvider, RoboTwinLocalProvider

FAKE_KEYS = [
    ("Assets/Isaac/5.1/Isaac/Props/YCB/Axis_Aligned/019_pitcher_base.usd", 4200000),
    ("Assets/Isaac/5.1/Isaac/Props/YCB/Axis_Aligned/024_bowl.usd", 3100000),
    (
        "Assets/Isaac/5.1/Isaac/Props/YCB/Axis_Aligned/.thumbs/256x256/024_bowl.usd.png",
        9000,
    ),
    ("Assets/Isaac/5.1/Isaac/Props/YCB/Axis_Aligned/Materials/wood.mdl", 12000),
]
PREFIX = "Assets/Isaac/5.1/Isaac/Props/YCB/Axis_Aligned"

FIX = Path(__file__).parent / "fixtures" / "mini_catalog.json"


def make_provider(tmp_path, calls=None):
    def fake_list(prefix, bucket=BUCKET, timeout_s=120):
        if calls is not None:
            calls.append(prefix)
        return FAKE_KEYS

    return NvidiaAssetServerProvider(
        [PREFIX], tmp_path / "idx.json", list_keys_fn=fake_list
    )


def test_search_matches_usd_by_token(tmp_path):
    p = make_provider(tmp_path)
    got = p.search("pitcher")
    assert len(got) == 1
    c = got[0]
    assert c.candidate_id.endswith("019_pitcher_base.usd")
    assert c.format == "usd"
    assert c.provider == "nvidia_server"
    assert c.metadata["key"].endswith("019_pitcher_base.usd")
    assert c.metadata["size_bytes"] == 4200000
    assert c.download_url.startswith(BUCKET)


def test_search_skips_thumbs_and_non_usd(tmp_path):
    p = make_provider(tmp_path)
    got = p.search("bowl")
    assert len(got) == 1 and ".thumbs" not in got[0].metadata["key"]
    assert p.search("wood") == []


def test_index_cached_after_first_build(tmp_path):
    calls = []
    p = make_provider(tmp_path, calls)
    p.search("bowl")
    p.search("pitcher")
    assert calls == [PREFIX]
    assert json.loads((tmp_path / "idx.json").read_text())[PREFIX]


def test_local_provider_hits_alias():
    got = RoboTwinLocalProvider(FIX).search("mug")
    assert got and got[0].format == "catalog_entry"
    assert got[0].metadata["asset_id"]
    assert got[0].provider == "robotwin_local"


def test_local_provider_miss_returns_empty():
    assert RoboTwinLocalProvider(FIX).search("pitcher") == []
