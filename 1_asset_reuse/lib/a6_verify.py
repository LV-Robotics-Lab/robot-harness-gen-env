"""a6: identity verification gate -- the only thing standing between a
confidently wrong retrieval and a permanent wrong entry in the pool.

Why a gate is required rather than a score threshold. Measured 2026-08-11 over
30 present + 6 absent queries, three candidate abstention signals:

    clip_top      present low 0.190 | absent high 0.250   overlapping
    clip_margin   present low 0.047 | absent high 0.078   overlapping
    lex_hits      present low 0     | absent high 3       overlapping

No single threshold separates "the corpus contains this" from "it does not",
and the best zero-false-alarm combination caught only 4 of 6. Meanwhile the
visual channel returns its top-N unconditionally, so after fusion EVERY query
gets confident candidates -- `trash bin` returns cans and beakers.

That matters more than ranking because of what the ledger records. An asset
imported from a search carries `semantics.identity.basis = "requested_by_acquire"`:
the category is what we ASKED for, not what the thing is. A cabinet imported
as a "trash bin" is thereafter, to every downstream consumer, a trash bin.

So the pool's entry condition is a POSITIVE answer about the picture, not the
absence of a negative signal. Verified candidates carry basis "vlm" and
verified=true; everything else simply does not get in.

The model is local and offline (Qwen2.5-VL-3B-Instruct), reusing the upstream
rendered_critic convention -- a remote model that silently changes version
would break the determinism the whole pipeline rests on. `infer` is injectable
so this module is testable without weights, mirroring
rendered_critic.review_rendered_scene(infer=...).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

VERIFY_SCHEMA_VERSION = "identity_verification.v1"
# 7B, not 3B, after a measured perception failure the questioning protocol
# could not fix: shown a fluted planter and asked "is this a trash bin?", the
# 3B said yes at high confidence, and even its OPEN answer for the larger
# planter was "black trash can" -- a sincere misperception. The 7B, same
# prompts, answers "textured cylinder / cylinder with ridges" and fails the
# match honestly (measured 2026-08-11, work/oneoff/gate_hardening_probe.py).
# Latency roughly doubles (still seconds per acquisition, amortised by the
# in-process model cache); the 3B remains selectable via identity_gate.model.
DEFAULT_MODEL = "Qwen/Qwen2.5-VL-7B-Instruct"

MATCH = "match"
MISMATCH = "mismatch"
UNREADABLE = "unreadable"
NO_THUMBNAIL = "no_thumbnail"

OPEN_PROMPT = (
    "Look at this image and answer with one JSON object and nothing else:\n"
    '{"object": "<the single main object you see, 1-3 words>"}'
)


def _name_words(name):
    """Filename -> word set, splitting camelCase, letter/digit boundaries and
    separators: TrafficCamera03.usd -> {traffic, camera, 03, usd}."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(name or ""))
    s = re.sub(r"([a-zA-Z])([0-9])", r"\1 \2", s)
    s = re.sub(r"([0-9])([a-zA-Z])", r"\1 \2", s)
    return {w for w in re.split(r"[^a-zA-Z0-9]+", s.lower()) if w}


def name_check_prompt(name: str, category: str) -> str:
    return (
        f'A 3D asset file is named "{name}".\n'
        f"Judging ONLY this filename (ignore the image), could this file "
        f"plausibly contain a {category}?\n"
        'Generic codes, product or brand names (e.g. "Reginald", "SM_Obj_C1") '
        "could name anything, so they are plausible.\n"
        "A name that clearly denotes a DIFFERENT kind of object is not.\n"
        "Reply with one JSON object and nothing else:\n"
        '{"plausible": true|false, "suggests": "<what the name suggests, 1-3 words>"}'
    )


def _open_agrees(seen, category, aliases):
    """Does the model's OPEN description name the requested thing (or one of
    its aliases)? Word-set overlap plus substring both ways, so "yellow cup"
    agrees with alias "cup" and alias "cycle" agrees with "unicycle"."""
    if not seen:
        return False

    def norm(t):
        # parser categories are slugs ("teddy_bear"); the model answers in
        # words ("teddy bear"). Unnormalised, the two NEVER agreed and the
        # open question vetoed every real teddy bear on two tiers
        # (measured 2026-08-13).
        return str(t).lower().replace("_", " ").replace("-", " ").strip()

    seen = norm(seen)
    seen_words = set(seen.split())
    names = {norm(category), *(norm(a) for a in aliases or [])}
    return any(n in seen or seen in n or (set(n.split()) & seen_words) for n in names)


def build_prompt(category: str, aliases=None) -> str:
    names = ", ".join(dict.fromkeys([category, *(aliases or [])]))
    return (
        "You are screening a 3D asset thumbnail before it enters a robotics "
        "simulation asset pool.\n"
        f"Question: is the object in this image a {category}"
        f"{f' (also called: {names})' if aliases else ''}?\n"
        "Judge only what you can see. A part, a material sample, a machine "
        "component or a different object entirely is NOT a match.\n"
        "Reply with one JSON object and nothing else:\n"
        '{"match": true|false, "object": "<what you actually see, 1-3 words>", '
        '"colors": ["<basic colour words>"], "materials": ["<material words>"], '
        '"confidence": "high"|"medium"|"low"}'
    )


def _parse(raw: str) -> dict | None:
    """Pull the first JSON object out of the reply. A model that answers in
    prose is treated as unreadable rather than guessed at -- the whole point of
    this gate is to not invent an identity."""
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) and "match" in obj else None


def _local_infer(image_path: Path, prompt: str, model_name: str) -> str:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    import torch
    from qwen_vl_utils import process_vision_info
    from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

    global _MODEL_CACHE
    if _MODEL_CACHE.get("name") != model_name:
        model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            model_name,
            torch_dtype="auto",
            device_map="auto",
            low_cpu_mem_usage=True,
            local_files_only=True,
        )
        processor = AutoProcessor.from_pretrained(
            model_name,
            min_pixels=256 * 28 * 28,
            max_pixels=1024 * 28 * 28,
            local_files_only=True,
        )
        _MODEL_CACHE = {"name": model_name, "model": model, "processor": processor}
    model = _MODEL_CACHE["model"]
    processor = _MODEL_CACHE["processor"]

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": f"file://{Path(image_path).resolve()}"},
                {"type": "text", "text": prompt},
            ],
        }
    ]
    text = processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    image_inputs, video_inputs = process_vision_info(messages)
    inputs = processor(
        text=[text],
        images=image_inputs,
        videos=video_inputs,
        padding=True,
        return_tensors="pt",
    ).to(model.device)
    with torch.inference_mode():
        # greedy: same thumbnail must always yield the same verdict, or the
        # pool's contents would depend on sampling luck
        generated = model.generate(**inputs, max_new_tokens=160, do_sample=False)
    trimmed = [out[len(src) :] for src, out in zip(inputs.input_ids, generated)]
    return processor.batch_decode(trimmed, skip_special_tokens=True)[0]


_MODEL_CACHE: dict = {}


def verify_image(
    image_path,
    category,
    *,
    aliases=None,
    infer=None,
    model_name=DEFAULT_MODEL,
    second_opinion=True,
):
    """One image -> verdict dict. The primitive both call sites share:

    - pre-download, on the SOURCE's thumbnail (NVIDIA publishes one per prop);
    - post-render, on OUR OWN settle snapshot -- which is how web-sourced
      assets get verified at all. A GitHub candidate has no picture before
      download, but the materialize step renders one before anything reaches
      the ledger, and looking at that render closes the loophole where
      "nothing to look at" simply admitted the asset. Measured in vivo
      2026-08-11: token matching handed "trash bin" an AnimatedColorsCube.glb,
      and only the physics gate happened to stop it.

    Never raises on model trouble: an inference failure is reported as
    `unreadable`, which does NOT admit the asset."""
    base = {
        "schema_version": VERIFY_SCHEMA_VERSION,
        "asked_category": category,
        "model": model_name,
        "image": str(image_path) if image_path else None,
    }
    if not image_path or not Path(image_path).is_file():
        return {**base, "verdict": NO_THUMBNAIL}
    prompt = build_prompt(category, aliases)
    run = infer or (lambda p, q: _local_infer(p, q, model_name))
    try:
        raw = run(Path(image_path), prompt)
    except Exception as exc:  # noqa: BLE001 -- reported, never admitted
        return {**base, "verdict": UNREADABLE, "error": repr(exc)}
    parsed = _parse(raw)
    if parsed is None:
        return {**base, "verdict": UNREADABLE, "raw": raw[:400]}
    result = {
        **base,
        "verdict": MATCH if parsed.get("match") is True else MISMATCH,
        "seen_as": parsed.get("object"),
        "colors": [str(c).lower() for c in (parsed.get("colors") or []) if c],
        "materials": [str(m).lower() for m in (parsed.get("materials") or []) if m],
        "confidence": parsed.get("confidence"),
    }
    if result["verdict"] == MATCH and second_opinion:
        # Second opinion, measured 2026-08-11 on the expanded corpus (16
        # queries): the yes/no question alone accepted a fluted planter as a
        # "trash bin" AT HIGH CONFIDENCE -- confidence gating caught nothing,
        # and an image-anchored forced choice flipped back to the wrong
        # answer too. The only reliable signal was ASKING WITHOUT THE LABEL:
        # shown the same image open-endedly, the model said "flowerpot".
        # A leading question plants its own answer; the open question is the
        # model's actual perception. Accept only when the two agree.
        #
        # Measured cost: adjacent-name misses (open says "ceramic cup" of a
        # beaker) reject a true positive when the alias net is thin. That is
        # the right side of the asymmetry -- a false reject is recoverable
        # (walk continues; honest "not found"; richer aliases fix it), a
        # false accept is a permanently mislabeled pool asset -- but it is a
        # real cost, so entry authors should list common synonyms.
        try:
            open_raw = run(Path(image_path), OPEN_PROMPT)
        except Exception as exc:  # noqa: BLE001
            return {**result, "verdict": UNREADABLE, "error": repr(exc)}
        m = re.search(r'"object"\s*:\s*"([^"]+)"', open_raw)
        open_seen = m.group(1).lower() if m else None
        result["open_answer"] = open_seen
        if not _open_agrees(open_seen, category, aliases):
            result["verdict"] = MISMATCH
            result["second_opinion_veto"] = True
    return result


def verify_candidate(
    candidate,
    category,
    *,
    aliases=None,
    infer=None,
    model_name=DEFAULT_MODEL,
    second_opinion=True,
):
    """One candidate -> verdict dict, keyed off its source thumbnail.

    A visual MATCH additionally has to survive a NAME check, because pixels
    alone have a measured failure mode at industrial-corpus scale (2026-08-12,
    dsready): TrafficCamera05's 256px thumbnail is a Mjolnir-shaped crop, and
    the model honestly answers "hammer" to both the closed and the open
    question -- and the settle render of the converted geometry looks like a
    hammer too, so the post-render check agreed as well. The filename is the
    one evidence channel that dissents. Policy:

      name contains a category/alias word  -> supports, no question asked
      name is a code or product name       -> plausible (Reginald IS a chair)
      name clearly denotes another object  -> veto the match

    Only an ACTIVE contradiction vetoes; absence of support never does, and a
    name-question failure keeps the visual verdict (auxiliary signal, never an
    infra veto)."""
    thumb = (candidate.metadata or {}).get("thumbnail")
    r = verify_image(
        thumb,
        category,
        aliases=aliases,
        infer=infer,
        model_name=model_name,
        second_opinion=second_opinion,
    )
    r = {
        **r,
        "candidate_id": candidate.candidate_id,
        "name": candidate.name,
        "thumbnail": thumb,
    }
    if r["verdict"] == MATCH:
        tokens = set()
        for n in (category, *(aliases or [])):
            tokens |= set(str(n).lower().split())
        if tokens & _name_words(candidate.name):
            r["name_check"] = "supports"
        else:
            run = infer or (lambda p, q: _local_infer(p, q, model_name))
            try:
                raw = run(Path(thumb), name_check_prompt(candidate.name, category))
                m = re.search(r'"plausible"\s*:\s*(true|false)', raw)
                sm = re.search(r'"suggests"\s*:\s*"([^"]+)"', raw)
                suggests = sm.group(1).lower() if sm else None
                if m is None:
                    r["name_check"] = "unreadable"
                elif m.group(1) == "true":
                    r["name_check"] = "plausible"
                else:
                    r["name_check"] = f"contradiction:{suggests}"
                    r["verdict"] = MISMATCH
                    r["name_veto"] = True
            except Exception as exc:  # noqa: BLE001 -- auxiliary, never blocks
                r["name_check"] = "unreadable"
                r["name_check_error"] = repr(exc)
    return r


def verify_candidates(
    candidates,
    category,
    *,
    aliases=None,
    infer=None,
    model_name=DEFAULT_MODEL,
    max_check=3,
    second_opinion=True,
):
    """Verify candidates in rank order, stopping at the first match.

    Stops early on purpose: the ranked list is already good (fusion puts the
    right asset in the top 5 on every labelled query), so the gate's job is to
    confirm the leader, not to re-rank the field. `max_check` bounds the cost
    at a few 256x256 images per acquisition."""
    results, accepted = [], None
    looked_n = 0
    # NO_THUMBNAIL must not consume the inspection window: community
    # thumbnails rot, and with max_check=3 two dead links pushed the real
    # teddy bears at ranks 6-8 out of view entirely (measured 2026-08-13).
    # The window counts candidates we could actually LOOK at; a hard cap of
    # 4x still bounds the walk over a rotten list.
    for cand in candidates[: 4 * max_check]:
        if looked_n >= max_check:
            break
        r = verify_candidate(
            cand,
            category,
            aliases=aliases,
            infer=infer,
            model_name=model_name,
            second_opinion=second_opinion,
        )
        results.append(r)
        if r["verdict"] != NO_THUMBNAIL:
            looked_n += 1
        if r["verdict"] == MATCH:
            accepted = cand
            break

    # "We looked and it was wrong" and "there was nothing to look at" are
    # different facts and must not collapse into one refusal. Only the NVIDIA
    # corpus publishes thumbnails; GitHub-sourced candidates have no picture
    # before download, so blocking on absence would close an entire asset
    # source rather than protect it.
    #
    # decided:  a readable verdict existed and none matched -> keep it out
    # unverifiable: nothing could be looked at -> admit as before, but the
    #               ledger records identity basis requested_by_acquire with
    #               verified=false, so the weaker claim stays visible instead
    #               of being laundered into a verified one.
    looked = [r for r in results if r["verdict"] in (MATCH, MISMATCH)]
    outcome = "verified" if accepted else ("rejected" if looked else "unverifiable")
    return {"accepted": accepted, "outcome": outcome, "results": results}
