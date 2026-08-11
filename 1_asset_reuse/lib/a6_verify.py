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
DEFAULT_MODEL = "Qwen/Qwen2.5-VL-3B-Instruct"

MATCH = "match"
MISMATCH = "mismatch"
UNREADABLE = "unreadable"
NO_THUMBNAIL = "no_thumbnail"


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


def verify_candidate(
    candidate, category, *, aliases=None, infer=None, model_name=DEFAULT_MODEL
):
    """One candidate -> verdict dict. Never raises on model trouble: an
    inference failure is reported as `unreadable`, which does NOT admit the
    asset."""
    thumb = (candidate.metadata or {}).get("thumbnail")
    base = {
        "schema_version": VERIFY_SCHEMA_VERSION,
        "candidate_id": candidate.candidate_id,
        "name": candidate.name,
        "asked_category": category,
        "model": model_name,
    }
    if not thumb or not Path(thumb).is_file():
        return {**base, "verdict": NO_THUMBNAIL, "thumbnail": thumb}
    prompt = build_prompt(category, aliases)
    run = infer or (lambda p, q: _local_infer(p, q, model_name))
    try:
        raw = run(Path(thumb), prompt)
    except Exception as exc:  # noqa: BLE001 -- reported, never admitted
        return {**base, "verdict": UNREADABLE, "thumbnail": thumb, "error": repr(exc)}
    parsed = _parse(raw)
    if parsed is None:
        return {**base, "verdict": UNREADABLE, "thumbnail": thumb, "raw": raw[:400]}
    return {
        **base,
        "verdict": MATCH if parsed.get("match") is True else MISMATCH,
        "thumbnail": thumb,
        "seen_as": parsed.get("object"),
        "colors": [str(c).lower() for c in (parsed.get("colors") or []) if c],
        "materials": [str(m).lower() for m in (parsed.get("materials") or []) if m],
        "confidence": parsed.get("confidence"),
    }


def verify_candidates(
    candidates,
    category,
    *,
    aliases=None,
    infer=None,
    model_name=DEFAULT_MODEL,
    max_check=3,
):
    """Verify candidates in rank order, stopping at the first match.

    Stops early on purpose: the ranked list is already good (fusion puts the
    right asset in the top 5 on every labelled query), so the gate's job is to
    confirm the leader, not to re-rank the field. `max_check` bounds the cost
    at a few 256x256 images per acquisition."""
    results, accepted = [], None
    for cand in candidates[:max_check]:
        r = verify_candidate(
            cand, category, aliases=aliases, infer=infer, model_name=model_name
        )
        results.append(r)
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
