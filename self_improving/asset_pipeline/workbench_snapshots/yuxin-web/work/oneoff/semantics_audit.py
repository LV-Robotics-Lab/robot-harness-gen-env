#!/usr/bin/env python3
"""One-off (work/oneoff/): cross-ledger semantics audit.

Answers four questions the per-ledger validator structurally cannot, because
they are properties of the WHOLE library rather than of one ledger:

  1. alias collisions  -- which alias words are claimed by more than one asset
     (the direct root cause of "搜不准": grounding scores collisions
     identically, so the winner is decided by a seed-hashed tie-break)
  2. match tiers       -- for a corpus of query categories, how many assets
     land on each rung of upstream's scoring ladder, and how many TIE on the
     top rung (that tie count IS the ambiguity, per category)
  3. coverage gaps     -- query categories no asset matches at all ("搜不到")
  4. identity origin   -- where each asset's category/aliases came from, plus
     the degeneracy/emptiness stats for semantic_name / colors / materials

Fidelity: the ladder below mirrors upstream scene_gen.grounding._semantic_score
EXACTLY -- strict equality, the same lower()+replace(" ","_") normalisation,
and the same rung values (100 category / 95 semantic_name / 90 alias). It is
not a re-interpretation; if upstream changes, this script is wrong and must be
re-read against it.

Read-only by construction: opens ledgers, manifests and the prompt corpus, and
writes nothing except its own report.
"""

import argparse
import json
import sys
from pathlib import Path

TIER_CATEGORY = 100.0
TIER_SEMANTIC = 95.0
TIER_ALIAS = 90.0

# Assets in the upstream 900_ block are procedurally generated proxies and
# derived rescales (scene_gen.asset_generator), not real library assets --
# a collision between a real asset and a proxy is a different (worse) problem
# than a collision between two real assets, so they are labelled separately.
PROXY_BLOCK = "900_"
ID_PREFIXES = ("robotwin_", "external_")


def _norm(value):
    """Upstream's normalisation for aliases and semantic_name."""
    return (value or "").lower().replace(" ", "_")


def _dir_key(asset_id):
    """Ledger asset_id -> library directory name (external_301_cup -> 301_cup)."""
    for prefix in ID_PREFIXES:
        if asset_id.startswith(prefix):
            return asset_id[len(prefix) :]
    return asset_id


def _is_proxy(asset_id):
    return _dir_key(asset_id).startswith(PROXY_BLOCK)


def load_catalog(catalog_path):
    """Entries from a built asset_catalog.json -- THE population grounding
    actually scores against. Ledgers are a different (much smaller) population:
    only 31 of 141 catalog entries have one, so collision/tie numbers computed
    over ledgers alone are simply about a different set of objects."""
    data = json.loads(Path(catalog_path).read_text())
    entries = []
    for e in data.get("entries", []):
        asset_id = e.get("asset_id", "")
        entries.append(
            {
                "asset_id": asset_id,
                "dir_key": _dir_key(asset_id),
                "category": e.get("category"),
                "semantic_name": e.get("semantic_name"),
                "aliases": list(e.get("aliases") or []),
                "colors": list(e.get("colors") or []),
                "materials": list(e.get("materials") or []),
                "proxy": _is_proxy(asset_id),
                "origin_dir": "catalog",
                "models": len(e.get("models") or []),
            }
        )
    return entries


def load_ledgers(ledger_dirs):
    """[{asset_id, dir_key, category, semantic_name, aliases, colors,
    materials, proxy, origin_dir}] over every ledger.json under each dir."""
    entries = []
    for d in ledger_dirs:
        root = Path(d)
        if not root.is_dir():
            raise SystemExit(f"not a directory: {root}")
        for asset_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            lp = asset_dir / "ledger.json"
            if not lp.exists():
                continue
            led = json.loads(lp.read_text())
            sem = led.get("semantics") or {}
            asset_id = led.get("asset_id", asset_dir.name)
            entries.append(
                {
                    "asset_id": asset_id,
                    "dir_key": _dir_key(asset_id),
                    "category": led.get("category"),
                    "semantic_name": led.get("semantic_name"),
                    "aliases": list(sem.get("aliases") or []),
                    "colors": list(sem.get("colors") or []),
                    "materials": list(sem.get("materials") or []),
                    "proxy": _is_proxy(asset_id),
                    "origin_dir": root.name,
                    "models": len(led.get("models") or []),
                }
            )
    return entries


def match_tier(query_category, entry):
    """Mirror of upstream _semantic_score's ladder (category-only; the colour
    and material gates are reported separately by colour_gate_effects)."""
    cat = query_category.lower()
    if entry["category"] == cat:
        return TIER_CATEGORY, "exact category match"
    if _norm(entry["semantic_name"]) == cat:
        return TIER_SEMANTIC, "exact semantic_name match"
    if cat in {_norm(a) for a in entry["aliases"]}:
        return TIER_ALIAS, "exact alias match"
    return None, None


def alias_collisions(entries):
    """normalised alias -> DISTINCT claimant assets, for aliases claimed by
    more than one asset. Claimants are deduped per asset: an entry listing
    "cup with liquid" and "cup_with_liquid" collides with ITSELF after
    normalisation, which is a data-quality defect (see redundant_aliases),
    not the grounding ambiguity this table is about."""
    index = {}
    for e in entries:
        for alias in {_norm(a) for a in e["aliases"]}:
            index.setdefault(alias, {})[e["asset_id"]] = e
    out = []
    for alias, claimants in sorted(index.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(claimants) < 2:
            continue
        rows = list(claimants.values())
        out.append(
            {
                "alias": alias,
                "count": len(rows),
                "assets": [c["asset_id"] for c in rows],
                "proxies": [c["asset_id"] for c in rows if c["proxy"]],
                "real": [c["asset_id"] for c in rows if not c["proxy"]],
            }
        )
    return out


def redundant_aliases(entries):
    """Aliases that can never add a match: duplicates of each other after
    normalisation, or equal to the entry's own category (which already scores
    100 on the rung above). Dead weight -- and a candidate validator check."""
    out = []
    for e in entries:
        seen, dupes = set(), []
        for alias in e["aliases"]:
            n = _norm(alias)
            if n in seen:
                dupes.append(alias)
            seen.add(n)
        same_as_category = [a for a in e["aliases"] if _norm(a) == _norm(e["category"])]
        if dupes or same_as_category:
            out.append(
                {
                    "asset_id": e["asset_id"],
                    "aliases": e["aliases"],
                    "normalisation_duplicates": dupes,
                    "same_as_category": same_as_category,
                }
            )
    return out


def tier_report(entries, queries):
    """Per query category: claimants by rung + the top-rung tie count."""
    rows = []
    for q in queries:
        rungs = {TIER_CATEGORY: [], TIER_SEMANTIC: [], TIER_ALIAS: []}
        for e in entries:
            tier, _ = match_tier(q, e)
            if tier is not None:
                rungs[tier].append(e)
        top = next(
            (t for t in (TIER_CATEGORY, TIER_SEMANTIC, TIER_ALIAS) if rungs[t]), None
        )
        winners = rungs[top] if top else []
        rows.append(
            {
                "query": q,
                "total_matches": sum(len(v) for v in rungs.values()),
                "top_tier": top,
                # The number that matters: assets tied on the top rung. >1 means
                # the pick is decided by grounding's seed-hashed tie-break, i.e.
                # the same prompt returns different assets for different seeds.
                "top_tier_tie": len(winners),
                "top_tier_assets": [w["asset_id"] for w in winners],
                "top_tier_proxies": [w["asset_id"] for w in winners if w["proxy"]],
                "by_rung": {
                    str(int(k)): [e["asset_id"] for e in v] for k, v in rungs.items()
                },
            }
        )
    return rows


def colour_gate_effects(entries, coloured_queries):
    """Upstream rejects a candidate outright when the query names a colour and
    the entry's colours are non-empty but lack it; an EMPTY colours list is
    permissive. Filled-in colours therefore narrow reach -- worth seeing."""
    out = []
    for cat, colour in coloured_queries:
        rejected, permitted, matched = [], [], []
        for e in entries:
            tier, _ = match_tier(cat, e)
            if tier is None:
                continue
            if colour in [c.lower() for c in e["colors"]]:
                matched.append(e["asset_id"])
            elif e["colors"]:
                rejected.append(e["asset_id"])
            else:
                permitted.append(e["asset_id"])
        out.append(
            {
                "query": f"{colour} {cat}",
                "colour_matched": matched,
                "rejected_by_colour": rejected,
                "permitted_colour_unknown": permitted,
            }
        )
    return out


def identity_origin(entries, manifests, upstream_dirname):
    """Infer where each asset's identity claim came from. Anything not
    positively attributable is reported as 'unknown' -- never guessed."""
    hand, acquired = set(), set()
    for kind, path in manifests:
        p = Path(path)
        if not p.exists():
            continue
        data = json.loads(p.read_text())
        target = hand if kind == "manifest_human" else acquired
        for group in data.get("groups") or []:
            for item in group.get("items") or []:
                if item.get("asset"):
                    target.add(item["asset"])

    rows, counts = [], {}
    for e in entries:
        if e["origin_dir"] == upstream_dirname:
            basis = "upstream_catalog"
        elif e["dir_key"] in acquired:
            basis = "requested_by_acquire"
        elif e["dir_key"] in hand:
            basis = "manifest_human"
        else:
            basis = "unknown"
        counts[basis] = counts.get(basis, 0) + 1
        rows.append({"asset_id": e["asset_id"], "basis": basis})
    return {"counts": counts, "rows": rows}


def field_health(entries):
    return {
        "ledgers": len(entries),
        "semantic_name_equals_category": sum(
            1 for e in entries if e["semantic_name"] == e["category"]
        ),
        "semantic_name_distinct": [
            {
                "asset_id": e["asset_id"],
                "category": e["category"],
                "semantic_name": e["semantic_name"],
            }
            for e in entries
            if e["semantic_name"] != e["category"]
        ],
        "colors_empty": sum(1 for e in entries if not e["colors"]),
        "materials_empty": sum(1 for e in entries if not e["materials"]),
        "category_not_lowercase": [
            e["asset_id"]
            for e in entries
            if e["category"] != (e["category"] or "").lower()
        ],
        "distinct_aliases": len({_norm(a) for e in entries for a in e["aliases"]}),
    }


def extract_queries(upstream, prompt_files, extra_prompts):
    """Query categories via the UPSTREAM parser -- never a hand-rolled one.
    Returns (categories, coloured_queries, mode, note)."""
    prompts = list(extra_prompts)
    for pf in prompt_files:
        data = json.loads(Path(pf).read_text())
        for case in data.get("cases", []):
            if case.get("prompt"):
                prompts.append(case["prompt"])

    sys.path.insert(0, str(upstream))
    try:
        from scene_gen.parser import parse_rule_based
    except Exception as exc:  # noqa: BLE001 -- report, never silently degrade
        return [], [], "unavailable", f"{type(exc).__name__}: {exc}"

    cats, coloured, rejected = [], [], []
    for prompt in prompts:
        try:
            spec = parse_rule_based(prompt)
        except Exception as exc:  # noqa: BLE001 -- negative cases are expected
            rejected.append({"prompt": prompt, "error": f"{type(exc).__name__}: {exc}"})
            continue
        for obj in spec.objects:
            cats.append(obj.category)
            if obj.color:
                coloured.append((obj.category, obj.color))
    note = f"{len(prompts)} prompt(s); {len(rejected)} rejected by parser"
    return sorted(set(cats)), sorted(set(coloured)), "upstream_parser", note


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--catalog",
        required=True,
        help="built asset_catalog.json -- THE population grounding scores "
        "against; collisions/ties/coverage are computed over this",
    )
    ap.add_argument(
        "--ledger-dir",
        action="append",
        required=True,
        help="repeatable; each holds <asset>/ledger.json. Used for identity "
        "provenance + ledger field health only, NOT for collisions",
    )
    ap.add_argument(
        "--upstream-dirname",
        default="upstream_ledgers",
        help="which --ledger-dir basename counts as upstream-derived",
    )
    ap.add_argument(
        "--upstream", required=True, help="env-gen checkout (for its parser)"
    )
    ap.add_argument(
        "--prompt-file",
        action="append",
        default=[],
        help="upstream prompt_matrix.json style corpus; repeatable",
    )
    ap.add_argument(
        "--prompt", action="append", default=[], help="extra literal prompt"
    )
    ap.add_argument("--manifest-human", action="append", default=[])
    ap.add_argument("--manifest-acquired", action="append", default=[])
    ap.add_argument("--out")
    args = ap.parse_args()

    # Two populations, deliberately kept apart: `catalog` is what grounding
    # scores (so collisions/ties/coverage are computed there), `ledgers` is
    # what we own and can attribute (so identity/field-health are computed
    # there). Mixing them silently -- e.g. counting collisions over ledgers --
    # answers a question nobody asked, since most catalog entries have no
    # ledger and some ledgers (upstream 900_ proxies) are not in our catalog.
    catalog_entries = load_catalog(args.catalog)
    ledger_entries = load_ledgers(args.ledger_dir)
    queries, coloured, mode, note = extract_queries(
        Path(args.upstream), args.prompt_file, args.prompt
    )

    manifests = [("manifest_human", p) for p in args.manifest_human]
    manifests += [("manifest_acquired", p) for p in args.manifest_acquired]

    ledger_ids = {e["asset_id"] for e in ledger_entries}
    ledger_dirs = {e["dir_key"] for e in ledger_entries}

    report = {
        "corpus": {"mode": mode, "note": note, "queries": queries},
        "populations": {
            "catalog_entries": len(catalog_entries),
            "ledgers": len(ledger_entries),
            "catalog_entries_without_ledger": [
                e["asset_id"]
                for e in catalog_entries
                if e["dir_key"] not in ledger_dirs
            ],
            "ledgers_not_in_catalog": [
                e["asset_id"]
                for e in ledger_entries
                if e["dir_key"] not in {c["dir_key"] for c in catalog_entries}
            ],
        },
        "field_health": field_health(ledger_entries),
        "alias_collisions": alias_collisions(catalog_entries),
        "redundant_aliases": redundant_aliases(catalog_entries),
        "tiers": tier_report(catalog_entries, queries) if queries else [],
        "colour_gate": (
            colour_gate_effects(catalog_entries, coloured) if queries else []
        ),
        "identity_origin": identity_origin(
            ledger_entries, manifests, args.upstream_dirname
        ),
    }
    fh = report["field_health"]
    pop = report["populations"]
    print(
        f"catalog entries: {pop['catalog_entries']}  (scored by grounding)\n"
        f"ledgers:         {pop['ledgers']}  "
        f"({len(pop['catalog_entries_without_ledger'])} catalog entries have no ledger, "
        f"{len(pop['ledgers_not_in_catalog'])} ledgers are not in this catalog)"
    )
    print(
        f"ledger field health -- semantic_name == category: "
        f"{fh['semantic_name_equals_category']}/{fh['ledgers']}, "
        f"colors empty: {fh['colors_empty']}, materials empty: {fh['materials_empty']}"
    )
    print(f"corpus: mode={mode} ({note}) -> {len(queries)} query categor(ies)")

    ra = report["redundant_aliases"]
    print(
        f"\n--- redundant aliases [catalog]: {len(ra)} entr(ies) "
        f"({sum(len(r['normalisation_duplicates']) for r in ra)} normalisation dupes, "
        f"{sum(len(r['same_as_category']) for r in ra)} same-as-category) ---"
    )
    for row in ra[:12]:
        print(f"  {row['asset_id']:<26} {row['aliases']}")
    if len(ra) > 12:
        print(f"  ... {len(ra) - 12} more (full list in --out)")

    print(f"\n--- alias collisions [catalog]: {len(report['alias_collisions'])} ---")
    for row in report["alias_collisions"]:
        tag = f"  [{len(row['proxies'])} proxy]" if row["proxies"] else ""
        print(f"  {row['alias']:<16} x{row['count']}{tag}  {', '.join(row['assets'])}")

    if queries:
        ambiguous = [r for r in report["tiers"] if r["top_tier_tie"] > 1]
        gaps = [r for r in report["tiers"] if r["total_matches"] == 0]
        print(f"\n--- top-rung ties (seed decides the winner): {len(ambiguous)} ---")
        for row in ambiguous:
            px = (
                f"  [{len(row['top_tier_proxies'])} proxy]"
                if row["top_tier_proxies"]
                else ""
            )
            print(
                f"  {row['query']:<16} tier={int(row['top_tier'])} tie={row['top_tier_tie']}{px}"
                f"  {', '.join(row['top_tier_assets'])}"
            )
        print(f"\n--- coverage gaps (no asset matches): {len(gaps)} ---")
        for row in gaps:
            print(f"  {row['query']}")
        narrowed = [c for c in report["colour_gate"] if c["rejected_by_colour"]]
        print(f"\n--- colour gate rejections: {len(narrowed)} ---")
        for row in narrowed:
            print(
                f"  {row['query']:<20} rejected: {', '.join(row['rejected_by_colour'])}"
            )

    print("\n--- identity origin ---")
    for basis, n in sorted(report["identity_origin"]["counts"].items()):
        print(f"  {basis:<22} {n}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print(f"\nreport -> {out}")


if __name__ == "__main__":
    main()
