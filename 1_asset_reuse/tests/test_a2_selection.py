import json

from agenticsim.openxsim.assets import AssetCandidate

from lib import a2_selection as a2


def cand(
    fmt="usd", provider="nvidia_server", key="a/b.usd", size=100, license="unknown x"
):
    return AssetCandidate(
        candidate_id=key,
        name=key,
        category="x",
        download_url="https://x",
        source_page="https://x",
        format=fmt,
        provider=provider,
        license=license,
        score=1.0,
        metadata={"key": key, "size_bytes": size},
    )


G = {"max_size_bytes": 1000, "license_gate": False}


def test_server_candidate_must_be_usd():
    assert a2.gate(cand(fmt="usd"), G) is None
    assert a2.gate(cand(fmt="glb"), G)[0] == a2.REJ_UNSUPPORTED


def test_web_candidate_formats():
    assert a2.gate(cand(fmt="glb", provider="github_tree"), G) is None
    assert a2.gate(cand(fmt="usd", provider="github_tree"), G)[0] == a2.REJ_UNSUPPORTED


def test_thumbs_oversize_license():
    assert a2.gate(cand(key="a/.thumbs/x.usd"), G)[0] == a2.REJ_THUMBS
    assert a2.gate(cand(size=2000), G)[0] == a2.REJ_OVERSIZE
    assert a2.gate(cand(), {**G, "license_gate": True})[0] == a2.REJ_LICENSE


def test_gate_candidates_records_every_rejection():
    recs = a2.gate_candidates([cand(), cand(fmt="glb")], G)
    assert [r["verdict"] for r in recs] == ["viable", "rejected"]
    assert recs[1]["rejection"]["code"] == a2.REJ_UNSUPPORTED


def test_allocate_new_category_gets_next_number(tmp_path):
    lib = tmp_path / "library"
    (lib / "301_cup").mkdir(parents=True)
    (lib / "301_cup" / "model_data0.json").write_text("{}")
    asset, model = a2.allocate_asset("pitcher", lib, tmp_path / "m.json")
    assert (asset, model) == ("302_pitcher", 0)


def test_allocate_same_category_appends_model(tmp_path):
    lib = tmp_path / "library"
    (lib / "301_cup").mkdir(parents=True)
    (lib / "301_cup" / "model_data0.json").write_text("{}")
    asset, model = a2.allocate_asset("cup", lib, tmp_path / "m.json")
    assert (asset, model) == ("301_cup", 1)


def test_allocate_sees_pending_manifest(tmp_path):
    m = tmp_path / "m.json"
    m.write_text(
        json.dumps(
            {
                "groups": [
                    {
                        "name": "g",
                        "prefix": "p",
                        "items": [
                            {
                                "usd": "x.usd",
                                "asset": "301_cup",
                                "model": 0,
                                "category": "cup",
                                "aliases": ["cup"],
                            }
                        ],
                    }
                ]
            }
        )
    )
    assert a2.allocate_asset("bowl", tmp_path / "nolib", m) == ("302_bowl", 0)
    assert a2.allocate_asset("cup", tmp_path / "nolib", m) == ("301_cup", 1)


def test_manifest_group_and_append(tmp_path):
    c = cand(key="Assets/Props/YCB/Axis_Aligned/019_pitcher_base.usd")
    g = a2.build_manifest_group(
        c, "302_pitcher", 0, {"category": "pitcher", "aliases": ["pitcher"]}
    )
    assert g["prefix"] == "Assets/Props/YCB/Axis_Aligned"
    assert g["items"][0] == {
        "usd": "019_pitcher_base.usd",
        "asset": "302_pitcher",
        "model": 0,
        "category": "pitcher",
        "aliases": ["pitcher"],
    }
    p = a2.append_manifest(tmp_path / "acq.json", g)
    a2.append_manifest(p, g)
    assert len(json.loads(p.read_text())["groups"]) == 1


def test_manifest_group_tolerates_github_candidate_without_key():
    c = AssetCandidate(
        candidate_id="github:KhronosGroup/glTF-Sample-Assets:Models/Lantern/glTF-Binary/Lantern.glb",
        name="Lantern.glb",
        category="lantern",
        download_url="https://raw.test/Lantern.glb",
        source_page="https://gh",
        format="glb",
        provider="github_tree",
        license="CC0",
        score=1.0,
        metadata={"path": "Models/Lantern/glTF-Binary/Lantern.glb"},
    )
    g = a2.build_manifest_group(
        c, "303_lantern", 0, {"category": "lantern", "aliases": ["lantern"]}
    )
    assert g["prefix"] == "Models/Lantern/glTF-Binary"
    assert g["items"][0]["usd"] == "Lantern.glb"


def test_write_evidence_schema(tmp_path):
    a2.write_evidence(
        tmp_path / "e.json",
        run_id="r1",
        providers_snapshot={"x": 1},
        categories=[{"query": {"category": "cup"}, "status": "reused_local"}],
    )
    d = json.loads((tmp_path / "e.json").read_text())
    assert d["schema"] == "envgen.asset_selection_evidence.v1"
    assert d["categories"][0]["status"] == "reused_local"
