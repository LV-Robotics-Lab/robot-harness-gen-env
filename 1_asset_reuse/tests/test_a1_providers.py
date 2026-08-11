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


def test_nvidia_search_records_last_stats(tmp_path):
    p = make_provider(tmp_path)
    p.search("pitcher")
    # scanned = every entry examined; token_miss = passed format filter but
    # missed the token match (bowl.usd). thumbs + .mdl are filtered by format,
    # not counted as token_miss.
    # Stats describe the INDEX now, not a per-query scan: with an inverted
    # index a query touches only its own posting lists, so "how many rows did
    # this query walk" stopped existing. indexed = searchable entries (the two
    # USDs; the thumbnail and the .mdl are not), matched = entries this query
    # touched, non_object = dropped by corpus hygiene.
    assert p.last_stats == {"indexed": 2, "matched": 1, "non_object": 0}


def test_word_boundary_matching_rejects_substring_false_positives(tmp_path):
    """Substring matching returned a cabinet for "trash bin" and a bearing for
    "teddy bear" on the real listing -- confidently wrong rather than empty,
    which is worse: the ledger then records the identity we asked for."""
    keys = [
        (f"{PREFIX}/sektion_cabinet_instanceable.usd", 1000),
        (f"{PREFIX}/caster_bearing.usd", 1000),
        (f"{PREFIX}/035_power_drill.usd", 1000),
        (f"{PREFIX}/sm_whitecorrugatedbox_b04_brown_01.usd", 1000),
    ]
    prov = NvidiaAssetServerProvider(
        [PREFIX], tmp_path / "idx2.json", list_keys_fn=lambda *_a, **_k: keys
    )
    assert prov.search("trash bin") == []
    assert prov.search("teddy bear") == []
    # and the tightening must not cost real matches
    assert [c.name for c in prov.search("power drill")] == ["035_power_drill.usd"]
    # MEASURED COST of the tightening, asserted rather than hidden: names that
    # glue words together ( splits to
    # {sm, whitecorrugatedbox, b04, brown, 01}) no longer match "box", which
    # substring matching did. The lexical channel gives up that recall on
    # purpose -- recovering it is the visual channel's job, and CLIP does
    # retrieve exactly these files for "a cardboard box" off their thumbnails.
    assert prov.search("corrugated box") == []
    assert prov.search("whitecorrugatedbox") != []


def test_non_object_entries_never_become_candidates(tmp_path):
    """Material shaders and physics proxies are not graspable props; letting
    them in lets "a metal wrench" rank five metal SHADERS above any tool."""
    keys = [
        (f"{PREFIX}/MetalPainted_Yellow_Glossy_A.usd", 1000),
        (f"{PREFIX}/Plastic_Red_A.usd", 1000),
        (f"{PREFIX}/dolly_physics.usd", 1000),
        (f"{PREFIX}/024_bowl.usd", 1000),
    ]
    prov = NvidiaAssetServerProvider(
        [PREFIX], tmp_path / "idx3.json", list_keys_fn=lambda *_a, **_k: keys
    )
    assert prov.search("metal") == []
    assert prov.search("plastic") == []
    assert prov.search("dolly") == []
    assert [c.name for c in prov.search("bowl")] == ["024_bowl.usd"]
    assert prov.last_stats["non_object"] == 3


def test_robotwin_local_search_records_last_stats():
    p = RoboTwinLocalProvider(FIX)
    got = p.search("mug")
    assert got
    data = json.loads(FIX.read_text())
    total = len(data["entries"])
    assert p.last_stats["scanned"] == total
    assert p.last_stats["token_miss"] == total - len(
        [e for e in data["entries"] if "mug" in e.get("aliases", [])]
    )


def test_robotwin_local_search_all_miss_records_stats():
    p = RoboTwinLocalProvider(FIX)
    assert p.search("pitcher") == []
    data = json.loads(FIX.read_text())
    total = len(data["entries"])
    assert p.last_stats == {"scanned": total, "token_miss": total}
