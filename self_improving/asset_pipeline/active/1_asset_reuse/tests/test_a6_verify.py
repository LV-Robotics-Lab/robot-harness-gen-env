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
    # a.png: one closed ask (mismatch). b.png: closed ask + the open-question
    # second opinion + the name question ("b.usd" carries no category word,
    # and the reply here parses as neither plausible nor contradiction, which
    # keeps the match). c/d: never reached.
    assert seen == ["a.png", "b.png", "b.png", "b.png"]


def test_second_opinion_vetoes_a_leading_yes(tmp_path):
    """The measured failure mode (2026-08-11, expanded corpus): asked "is
    this a trash bin?" the model said yes AT HIGH CONFIDENCE about a fluted
    flowerpot; asked openly what it saw, it said flowerpot. The open answer
    is the model's real perception -- when the two disagree, the match is
    vetoed."""

    def infer(_p, prompt):
        if "single main object" in prompt:  # the open question
            return '{"object": "flowerpot"}'
        return reply(True, "trash bin")

    out = a6.verify_candidates(
        [cand("Fluted_Medium.usd", png(tmp_path))],
        "trash bin",
        aliases=["garbage can"],
        infer=infer,
    )
    assert out["accepted"] is None
    assert out["results"][0]["second_opinion_veto"] is True
    assert out["results"][0]["open_answer"] == "flowerpot"


def test_second_opinion_accepts_alias_worded_answers(tmp_path):
    """ "yellow cup" must agree with category mug via alias "cup" -- the veto
    is for different OBJECTS, not different WORDINGS, and the alias list is
    exactly where wordings live."""

    def infer(_p, prompt):
        if "single main object" in prompt:
            return '{"object": "yellow cup"}'
        return reply(True, "mug")

    out = a6.verify_candidates(
        [cand("SM_Mug_C1.usd", png(tmp_path))],
        "mug",
        aliases=["cup"],
        infer=infer,
    )
    assert out["accepted"] is not None
    assert out["results"][0]["verdict"] == a6.MATCH


def test_name_contradiction_vetoes_ambiguous_silhouette(tmp_path):
    """The 50k-scale failure (2026-08-12, dsready): TrafficCamera05's
    thumbnail is a Mjolnir-shaped crop, and the model honestly answers
    "hammer" to BOTH the closed and the open question -- pixels cannot save
    this one, and neither could the post-render check (the converted geometry
    looks like a hammer too). The filename is the only dissenting evidence
    channel, so a visual MATCH whose name clearly denotes a different object
    is vetoed."""
    assert a6._name_words("TrafficCamera03.usd") == {"traffic", "camera", "03", "usd"}

    def infer(_p, prompt):
        if "single main object" in prompt:
            return '{"object": "mjolnir hammer"}'
        if "plausible" in prompt:
            return '{"plausible": false, "suggests": "traffic camera"}'
        return reply(True, "hammer")

    out = a6.verify_candidates(
        [cand("TrafficCamera05.usd", png(tmp_path))], "hammer", infer=infer
    )
    assert out["accepted"] is None
    assert out["results"][0]["name_check"] == "contradiction:traffic camera"
    assert out["results"][0]["name_veto"] is True


def test_product_names_stay_admissible(tmp_path):
    """Reginald is a real office chair whose filename is just a product name.
    The name channel must only veto ACTIVE contradictions -- absence of
    support is normal (half the corpus is product codes)."""

    def infer(_p, prompt):
        if "single main object" in prompt:
            return '{"object": "office chair"}'
        if "plausible" in prompt:
            return '{"plausible": true, "suggests": "nothing specific"}'
        return reply(True, "office chair")

    out = a6.verify_candidates(
        [cand("Reginald.usd", png(tmp_path))],
        "office chair",
        aliases=["chair"],
        infer=infer,
    )
    assert out["accepted"] is not None
    assert out["results"][0]["name_check"] == "plausible"


def test_name_support_skips_the_name_question(tmp_path):
    """constr_mov_jack_hammer_01 contains the category word: asking a model
    whether "hammer" might be a hammer is a wasted inference."""
    calls = []

    def infer(_p, prompt):
        calls.append(prompt)
        if "single main object" in prompt:
            return '{"object": "hammer"}'
        return reply(True, "hammer")

    out = a6.verify_candidates(
        [cand("constr_mov_jack_hammer_01.usd", png(tmp_path))], "hammer", infer=infer
    )
    assert out["accepted"] is not None
    assert out["results"][0]["name_check"] == "supports"
    assert len(calls) == 2  # closed + open only, no name question


def test_synonym_check_rescues_true_synonyms(tmp_path):
    """ "dustbin" vs open answer "trash can": word-level disagreement is not
    object disagreement -- three real dustbins were vetoed across two tiers
    (measured 2026-08-13). The lexical question rescues explicit synonyms."""

    def infer(_p, prompt):
        if "single main object" in prompt:
            return '{"object": "trash can"}'
        if "same_kind" in prompt:
            return '{"same_kind": true}'
        return reply(True, "dustbin")

    out = a6.verify_candidates(
        [cand("obs_dustbin_02.usd", png(tmp_path))], "dustbin", infer=infer
    )
    assert out["accepted"] is not None
    assert out["results"][0]["synonym_check"] is True


def test_synonym_check_false_keeps_the_veto(tmp_path):
    """The flowerpot class must stay dead: open answer names a DIFFERENT
    object and the lexical question confirms they are different kinds."""

    def infer(_p, prompt):
        if "single main object" in prompt:
            return '{"object": "flowerpot"}'
        if "same_kind" in prompt:
            return '{"same_kind": false}'
        return reply(True, "trash bin")

    out = a6.verify_candidates(
        [cand("Fluted_Medium.usd", png(tmp_path))], "trash bin", infer=infer
    )
    assert out["accepted"] is None
    assert out["results"][0]["second_opinion_veto"] is True
    assert out["results"][0]["synonym_check"] is False


def test_open_agreement_normalizes_slug_categories(tmp_path):
    """Parser categories are slugs ("teddy_bear"); the model answers in words
    ("teddy bear"). Unnormalised these NEVER agreed, and the open question
    vetoed every real teddy bear on two tiers (measured 2026-08-13)."""

    def infer(_p, prompt):
        if "single main object" in prompt:
            return '{"object": "teddy bear"}'
        return reply(True, "teddy bear")

    out = a6.verify_candidates(
        [cand("teddy_bear.usd", png(tmp_path))],
        "teddy_bear",
        aliases=["teddy_bear"],
        infer=infer,
    )
    assert out["accepted"] is not None
    assert out["results"][0]["verdict"] == a6.MATCH


def test_dead_thumbnails_do_not_consume_the_window(tmp_path):
    """Community thumbnails rot: with max_check=3, two dead links pushed the
    real teddy bears at ranks 6-8 out of view entirely (measured 2026-08-13).
    NO_THUMBNAIL rows are recorded but only LOOKED-at candidates count."""
    cands = [
        cand("dead1.glb"),
        cand("dead2.glb"),
        cand("wrong.usd", png(tmp_path, "w.png")),
        cand("dead3.glb"),
        cand("right.usd", png(tmp_path, "r.png")),
    ]

    def infer(p, prompt):
        if "single main object" in prompt:
            return '{"object": "mug"}'
        return reply(p.name == "r.png", "mug" if p.name == "r.png" else "vase")

    out = a6.verify_candidates(cands, "mug", infer=infer, max_check=3)
    assert out["accepted"] is not None
    assert out["accepted"].name == "right.usd"


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


def test_merge_observed_attributes_matrix():
    # 声明优先、观察只填空；basis: manifest / vlm / mixed / None
    assert a6.merge_observed_attributes(["red"], []) == (["red"], "manifest")
    assert a6.merge_observed_attributes([], ["yellow"]) == (["yellow"], "vlm")
    merged, basis = a6.merge_observed_attributes(["red"], ["white", "red"])
    assert merged == ["red", "white"] and basis == "mixed"
    assert a6.merge_observed_attributes([], []) == ([], None)
    merged, basis = a6.merge_observed_attributes(["red"], ["red"])
    assert merged == ["red"] and basis == "manifest"
