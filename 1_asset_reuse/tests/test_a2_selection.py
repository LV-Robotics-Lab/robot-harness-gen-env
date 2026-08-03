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
