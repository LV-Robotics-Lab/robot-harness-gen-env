"""a5: query alias expansion and LLM semantic screening for the acquire retrieval layer."""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path

# Tiny embedded common-word set used only to guess a word boundary inside a
# pure-lowercase compound with no other signal (no separator, no case
# transition). Dumb and predictable by design -- not a real dictionary.
_WORDS = {
    "milk",
    "tea",
    "cup",
    "ball",
    "base",
    "top",
    "box",
    "book",
    "shelf",
    "lamp",
    "phone",
    "plant",
    "pot",
    "pan",
    "bottle",
    "water",
    "coffee",
    "wine",
    "glass",
    "paper",
    "towel",
    "tooth",
    "brush",
    "hair",
    "dryer",
    "screw",
    "driver",
    "key",
    "board",
    "mouse",
    "pad",
    "back",
    "pack",
    "hand",
    "bag",
    "foot",
    "rest",
    "head",
    "note",
    "laptop",
    "desk",
    "chair",
    "table",
    "door",
    "knob",
    "light",
    "bulb",
    "trash",
    "can",
    "dust",
    "bin",
    "tool",
    "kit",
    "tape",
    "pen",
    "pencil",
    "ruler",
    "cloth",
}

_CAMEL_RE = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")


def load_alias_cache(path) -> dict:
    p = Path(path)
    if not p.is_file():
        return {}
    return json.loads(p.read_text())


def save_alias_cache(path, cache) -> None:
    """Write atomically: write to a same-directory temp file, then
    os.replace (atomic on POSIX) so concurrent readers/writers never see a
    partial file, and a crash mid-write can't corrupt the cache."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(cache, indent=2, ensure_ascii=False, sort_keys=True))
    os.replace(tmp, p)


def compound_split(term: str) -> list[str]:
    if not term:
        return []
    if "-" in term or "_" in term:
        parts = [p for p in re.split(r"[-_]+", term) if p]
        if len(parts) > 1:
            return [" ".join(p.lower() for p in parts)]
        return []
    if re.search(r"[a-z][A-Z]", term):
        parts = _CAMEL_RE.findall(term)
        if len(parts) > 1:
            return [" ".join(p.lower() for p in parts)]
        return []
    if term.isalpha() and term.islower():
        for i in range(3, len(term) - 2):
            head, tail = term[:i], term[i:]
            if head in _WORDS and tail in _WORDS:
                return [f"{head} {tail}"]
    return []


def _expand_prompt(category: str) -> str:
    return (
        "List up to 5 common alternate search terms or synonyms for the "
        f"object category {category!r}, as used when searching an online "
        "3D asset store. Respond with ONLY a JSON array of lowercase "
        'strings, e.g. ["term one", "term two"]. No extra text.'
    )


def _parse_term_list(raw) -> list[str]:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(t).strip().lower() for t in data if str(t).strip()]


def expand_terms(category, aliases, cfg, llm_fn=None, cache=None) -> dict:
    cache = {} if cache is None else cache
    cfg = cfg or {}
    aliases = list(aliases or [])
    key = category.lower()

    if key in cache and cache[key]:
        extras = list(cache[key])
        source = "cache"
    else:
        fn = llm_fn
        if cfg.get("enabled") and fn is None:
            fn = default_llm_fn(cfg)
        extras = []
        source = "none"
        if cfg.get("enabled") and fn is not None:
            try:
                extras = _parse_term_list(fn(_expand_prompt(category)))
            except Exception:  # noqa: BLE001 -- degrade to split, never raise
                extras = []
            if extras:
                cache[key] = list(extras)
                source = "llm"
        if not extras:
            extras = compound_split(key)
            source = "split" if extras else "none"

    terms = []
    for t in [category, *aliases, *extras]:
        t = str(t).strip().lower()
        if t and t not in terms:
            terms.append(t)
    return {"terms": terms, "added": list(extras), "source": source}


def _screen_prompt(category: str, candidates) -> str:
    items = [{"id": c.candidate_id, "name": c.name} for c in candidates]
    return (
        f"Object category: {category!r}. For each candidate below, decide "
        "whether its name plausibly refers to a real instance of this "
        "category. Respond with ONLY a JSON object mapping each candidate "
        'id to {"ok": true|false, "reason": "..."}. No extra text.\n'
        f"Candidates: {json.dumps(items)}"
    )


def screen_candidates(category, candidates, cfg, llm_fn=None) -> dict:
    cfg = cfg or {}
    if not candidates:
        return {}
    fn = llm_fn
    if cfg.get("enabled") and fn is None:
        fn = default_llm_fn(cfg)
    if not cfg.get("enabled") or fn is None:
        return {}
    try:
        data = json.loads(fn(_screen_prompt(category, candidates)))
    except Exception:  # noqa: BLE001 -- malformed output degrades to "no screening"
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for c in candidates:
        v = data.get(c.candidate_id)
        if isinstance(v, dict) and "ok" in v:
            out[c.candidate_id] = {
                "ok": bool(v["ok"]),
                "reason": str(v.get("reason", "")),
            }
    return out


def default_llm_fn(cfg):
    if not cfg or not cfg.get("enabled") or not cfg.get("api_key_file"):
        return None
    key_path = Path(cfg["api_key_file"]).expanduser()
    if not key_path.is_file():
        return None
    base_url = cfg.get("base_url", "")
    model = cfg.get("model", "")

    def _call(prompt):
        api_key = key_path.read_text().strip()
        req = urllib.request.Request(
            base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(
                {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                }
            ).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read())
        return payload["choices"][0]["message"]["content"]

    return _call
