"""Identity gate. Tested with an injected `infer` so no weights are needed --
same seam upstream's rendered_critic uses."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agenticsim.openxsim.assets import AssetCandidate  # noqa: E402

from lib import a6_verify as a6  # noqa: E402


def cand(name, thumb=None, cid=None):
    return AssetCandidate(
        candidate_id=cid or f"nvidia:{name}",
        name=name,
        category="props",
        download_url="https://example/x.usd",
        source_page="https://example/x.usd",
        format="usd",
        provider="nvidia_visual",
        license="unknown",
        score=1.0,
        metadata={"thumbnail": str(thumb) if thumb else None},
    )


def png(tmp_path, name="t.png"):
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\n\x1a\n")  # only existence is checked here
    return p


def reply(match, obj="mug", colors=("red",)):
    cols = ", ".join(f'"{c}"' for c in colors)
    return (
        f'Sure! {{"match": {str(match).lower()}, "object": "{obj}", '
        f'"colors": [{cols}], "materials": ["ceramic"], "confidence": "high"}}'
    )


def test_match_admits_and_harvests_appearance(tmp_path):
    """A pass is not just a yes/no: the same look at the picture is what fills
    colors/materials, which is what separates same-category ties downstream."""
    out = a6.verify_candidates(
        [cand("025_mug.usd", png(tmp_path))],
        "mug",
        infer=lambda _p, _q: reply(True, "mug", ("red", "white")),
    )
    assert out["outcome"] == "verified"
    assert out["accepted"].name == "025_mug.usd"
    assert out["results"][0]["colors"] == ["red", "white"]
    assert out["results"][0]["seen_as"] == "mug"


def test_mismatch_keeps_the_asset_out(tmp_path):
    """The failure this gate exists for: retrieval is confident, the picture
    disagrees, and without the gate the pool would gain a cabinet named
    'trash bin'."""
    out = a6.verify_candidates(
        [cand("sektion_cabinet.usd", png(tmp_path))],
        "trash bin",
        infer=lambda _p, _q: reply(False, "cabinet"),
    )
    assert out["accepted"] is None
    assert out["outcome"] == "rejected"


def test_stops_at_first_match_and_bounds_cost(tmp_path):
    seen = []

    def infer(p, _q):
        seen.append(p.name)
        return reply(p.name == "b.png", "mug")

    cands = [
        cand("a.usd", png(tmp_path, "a.png")),
        cand("b.usd", png(tmp_path, "b.png")),
        cand("c.usd", png(tmp_path, "c.png")),
        cand("d.usd", png(tmp_path, "d.png")),
    ]
    out = a6.verify_candidates(cands, "mug", infer=infer, max_check=3)
    assert out["accepted"].name == "b.usd"
    assert seen == ["a.png", "b.png"]  # stopped at the match, never reached c/d


def test_no_thumbnail_is_unverifiable_not_rejected():
    """'We looked and it was wrong' and 'there was nothing to look at' must not
    collapse: only the NVIDIA corpus ships thumbnails, so treating absence as
    rejection would close the GitHub sources entirely."""
    out = a6.verify_candidates(
        [cand("web_asset.glb")], "mug", infer=lambda *_: reply(True)
    )
    assert out["accepted"] is None
    assert out["outcome"] == "unverifiable"
    assert out["results"][0]["verdict"] == a6.NO_THUMBNAIL


def test_unparseable_reply_never_admits(tmp_path):
    """A model that answers in prose gets no benefit of the doubt -- inventing
    an identity is exactly what this gate exists to prevent."""
    out = a6.verify_candidates(
        [cand("x.usd", png(tmp_path))], "mug", infer=lambda *_: "Yes, that is a mug!"
    )
    assert out["accepted"] is None
    assert out["outcome"] == "unverifiable"
    assert out["results"][0]["verdict"] == a6.UNREADABLE


def test_inference_failure_is_reported_not_swallowed(tmp_path):
    def boom(*_a):
        raise RuntimeError("CUDA out of memory")

    out = a6.verify_candidates([cand("x.usd", png(tmp_path))], "mug", infer=boom)
    assert out["accepted"] is None
    assert out["results"][0]["verdict"] == a6.UNREADABLE
    assert "CUDA out of memory" in out["results"][0]["error"]


def test_prompt_names_the_category_and_forbids_guessing():
    p = a6.build_prompt("mug", ["cup"])
    assert "mug" in p and "cup" in p
    assert "Judge only what you can see" in p
