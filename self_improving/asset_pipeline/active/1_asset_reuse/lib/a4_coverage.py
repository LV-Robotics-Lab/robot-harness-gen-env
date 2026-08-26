"""a4: scene-need extraction and coverage check via upstream grounding (read-only imports)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from scene_gen.catalog import load_catalog
from scene_gen.grounding import ground_object
from scene_gen.parser import parse_rule_based
from scene_gen.schema import SceneSpecError

# Words that appear inside hyphenated RELATION phrases ("on-top-of"); a hyphen
# touching one of these is left alone so we never rewrite spatial wording.
_RELATION_WORDS = {
    "on",
    "top",
    "of",
    "in",
    "front",
    "next",
    "to",
    "left",
    "right",
    "behind",
    "near",
    "inside",
    "into",
    "the",
}


def _vocab_words():
    """Single-word terms of the upstream vocabulary ("box", "cup", "remote",
    ...). Only these can collide inside a hyphenated compound."""
    from scene_gen.parser import OBJECT_TERMS

    return {
        t.lower()
        for terms in OBJECT_TERMS.values()
        for t in terms
        if " " not in t and t.isascii()
    }


def normalize_prompt_ex(prompt: str) -> tuple[str, dict[str, list[str]]]:
    """Defuse hyphen/underscore compounds whose parts collide with the
    upstream vocabulary, returning (rewritten_prompt, {joined: aliases}).

    The measured failure (web run 2026-08-12): "tissue-box" -- the upstream
    term scan's boundary class is `(?<![a-z0-9])`, so both '-' and '_' count
    as boundaries and the vocabulary word "box" matched inside the compound;
    the free capture that would have kept the whole compound is skipped for
    any span overlapping a term hit, and a plain storage box was staged for a
    tissue-box request. The only prompt-level rewrite the upstream parser
    cannot mis-split is seamless concatenation: tissue-box -> "tissuebox",
    which free-captures as one honest category. The readable word forms are
    returned as aliases so acquisition still searches for "tissue box".

    Compounds with NO vocabulary collision ("dumbbell-rack") are left alone --
    they already free-capture correctly -- and hyphens touching relation
    words ("on-top-of") are never rewritten."""
    # Unicode whitespace first: a full-width space (U+3000, the CJK IME
    # default) or NBSP that a later sanitizer DELETES instead of splitting
    # fuses neighbouring words -- "Place a TV<U+3000>on" became "TVon" and
    # bought a sofa under category tvon_the_table (2026-08-15). Every
    # whitespace rune becomes an ASCII space before anything else looks.
    prompt = "".join(" " if ch.isspace() else ch for ch in prompt)
    vocab = _vocab_words()
    aliases: dict[str, list[str]] = {}

    def _fix(m: re.Match) -> str:
        parts = re.split(r"[-_]", m.group(0))
        low = [p.lower() for p in parts]
        if any(p in _RELATION_WORDS for p in low):
            return m.group(0)
        if not any(p in vocab for p in low):
            return m.group(0)
        joined = "".join(low)
        aliases[joined] = [" ".join(low), "-".join(low)]
        return joined

    rewritten = re.sub(r"[A-Za-z]+(?:[-_][A-Za-z]+)+", _fix, prompt)
    # The upstream relation lookahead knows "inside/into" but NOT bare "in":
    # "Place a tea in the cup" silently drops the tea mention (measured
    # 2026-08-13). Rewrite genuinely containment-flavoured " in the/a " to
    # " inside the/a ", leaving "in front of" untouched.
    rewritten = re.sub(
        r"\bin\s+(the|a|an)\s+(?!front\b)", r"inside \1 ", rewritten, flags=re.I
    )
    return rewritten, aliases


def normalize_prompt(prompt: str) -> str:
    return normalize_prompt_ex(prompt)[0]


def extract_needs(prompt, seed=0):
    spec = parse_rule_based(normalize_prompt(prompt), seed=seed)
    needs = [
        {"object_id": o.object_id, "category": o.category, "color": o.color}
        for o in spec.objects
    ]
    return spec, needs


def _get(o, key, default=None):
    if isinstance(o, dict):
        return o.get(key, default)
    return getattr(o, key, default)


def _unusable_same_category(catalog, category):
    """Assets of the requested category that exist but have zero usable
    models. "the library has no X" and "the library has an X nobody has
    validated yet" demand different responses (acquire vs validate), and the
    generic gap detail collapsed them -- a dumbbell-rack request 2026-08-12
    walked all the way to an external-search blocker while 013_dumbbell-rack
    sat in the catalog with 4 unvalidated models."""
    hints = []
    for entry in _get(catalog, "entries", []) or []:
        if _get(entry, "category") != category:
            continue
        models = _get(entry, "models", []) or []
        if models and not any(_get(m, "usable") for m in models):
            missing = sorted(
                {str(r) for m in models for r in (_get(m, "missing") or [])}
            )
            hints.append(
                f"{_get(entry, 'asset_id')}: 0/{len(models)} models usable"
                f" (missing: {', '.join(missing) or 'unknown'})"
            )
    return hints


def check_coverage(spec, catalog_path):
    catalog = load_catalog(Path(catalog_path))
    records = []
    for obj in spec.objects:
        base = {
            "object_id": obj.object_id,
            "category": obj.category,
            "color": obj.color,
            # material rides along with colour: upstream grounding rejects a
            # material mismatch exactly like a colour mismatch, so a gap for
            # "a wooden bowl" has to reach acquisition as one (2026-08-14)
            "material": obj.material,
        }
        try:
            sel = ground_object(obj, catalog, seed=spec.seed)
            records.append(
                {
                    **base,
                    "status": "covered",
                    "asset_id": sel.entry.asset_id,
                    "model_id": sel.model.model_id,
                    "score": sel.score,
                }
            )
        except SceneSpecError as exc:
            rec = {**base, "status": "gap", "detail": str(exc)}
            hints = _unusable_same_category(catalog, obj.category)
            if hints:
                rec["unusable_candidates"] = hints
                rec["detail"] += (
                    "; catalog HAS same-category assets awaiting validation: "
                    + "; ".join(hints)
                )
            records.append(rec)
    return records


_SIZE_TABLE = None


def _category_sizes():
    """configs/category_sizes.yml, cached. Returns (sizes, views, default)."""
    global _SIZE_TABLE
    if _SIZE_TABLE is None:
        import yaml

        p = Path(__file__).resolve().parents[1] / "configs" / "category_sizes.yml"
        d = yaml.safe_load(p.read_text()) if p.is_file() else {}
        _SIZE_TABLE = (
            d.get("sizes") or {},
            (d.get("views") or {}).get("tabletop") or {},
            float(d.get("default_unknown_m") or 0.25),
        )
    return _SIZE_TABLE


def resolve_size_policy(category):
    """Category -> (size_policy string, decision dict) under the tabletop
    view. The TABLE records the environment-neutral truth (typical real-world
    max dim); whether that fits a 0.70x0.50 m table is THIS view's ruling:

      fits    typical <= fit_max   -> "category:<typical>"  (real size)
      capped  typical <= refuse    -> "capped:<cap_to>"     (marked shrink)
      refuse  typical >  refuse    -> None + refusal record (honest stop;
              the asset keeps its real size for future non-tabletop views)

    Unknown categories keep the old absolute default -- and the decision
    says so, because a default is a decision too. Born from the "everything
    looks basket-sized" report (2026-08-15): absolute:0.25 flattened a
    0.73 m television and a 43.75 m mis-authored hanger to the same 25 cm."""
    sizes, view, default = _category_sizes()
    fit = float(view.get("fit_max_m", 0.42))
    cap = float(view.get("cap_to_m", 0.42))
    refuse = float(view.get("refuse_over_m", 0.84))
    row = sizes.get(category)
    if not row:
        return f"absolute:{default}", {
            "decision": "default_unknown",
            "note": "category not in size table",
        }
    t = float(row["size_m"])
    base = {"typical_m": t, "confidence": row.get("confidence")}
    if t <= fit:
        return f"category:{t}", {**base, "decision": "real_size"}
    if t <= refuse:
        return f"capped:{cap}", {**base, "decision": "capped_to_view"}
    return None, {**base, "decision": "refuse_oversize_for_view"}


def gaps_to_entries(records, extra_aliases=None):
    seen, entries = set(), []
    for r in records:
        if r["status"] != "gap":
            continue
        key = (r["category"], r.get("color"), r.get("material"))
        if key in seen:
            continue
        seen.add(key)
        # Web sources ship at arbitrary author scales (a 13.6 m tissue box,
        # measured 2026-08-13); without a size policy the import gate rightly
        # rejects them as implausible. Every gap-driven acquisition therefore
        # carries a tabletop default; per-request policies can override later.
        policy, size_decision = resolve_size_policy(r["category"])
        entry = {
            "category": r["category"],
            "aliases": [r["category"]],
            "size_decision": size_decision,
        }
        if policy is None:
            entry["oversize_refusal"] = True
        else:
            entry["size_policy"] = policy
        # compound categories arrive concatenated ("tissuebox", see
        # normalize_prompt_ex); the readable word forms ride along as search
        # wideners so retrieval still looks for "tissue box"
        for alias in (extra_aliases or {}).get(r["category"], []):
            if alias not in entry["aliases"]:
                entry["aliases"].append(alias)
        if r.get("color"):
            entry["colors"] = [r["color"]]
        if r.get("material"):
            entry["materials"] = [r["material"]]
        entries.append(entry)
    return entries


def mark_acquired(before, after):
    """Rewrite status "covered" -> "acquired" for objects that were a "gap"
    in `before` and are now "covered" in `after` (gap-driven acquire succeeded).
    Everything else passes through unchanged."""
    before_by_id = {r["object_id"]: r for r in before}
    result = []
    for r in after:
        b = before_by_id.get(r["object_id"])
        if b and b.get("status") == "gap" and r.get("status") == "covered":
            r = {**r, "status": "acquired"}
        result.append(r)
    return result


def write_coverage_report(path, prompt, seed, records):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(
            {
                "schema": "envgen.scene_coverage.v1",
                "prompt": prompt,
                "seed": seed,
                "objects": records,
            },
            indent=1,
            ensure_ascii=False,
        )
    )
