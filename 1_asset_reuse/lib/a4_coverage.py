"""a4: scene-need extraction and coverage check via upstream grounding (read-only imports)."""

from __future__ import annotations

import json
from pathlib import Path

from scene_gen.catalog import load_catalog
from scene_gen.grounding import ground_object
from scene_gen.parser import parse_rule_based
from scene_gen.schema import SceneSpecError


def extract_needs(prompt, seed=0):
    spec = parse_rule_based(prompt, seed=seed)
    needs = [
        {"object_id": o.object_id, "category": o.category, "color": o.color}
        for o in spec.objects
    ]
    return spec, needs


def check_coverage(spec, catalog_path):
    catalog = load_catalog(Path(catalog_path))
    records = []
    for obj in spec.objects:
        base = {
            "object_id": obj.object_id,
            "category": obj.category,
            "color": obj.color,
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
            records.append({**base, "status": "gap", "detail": str(exc)})
    return records


def gaps_to_entries(records):
    seen, entries = set(), []
    for r in records:
        if r["status"] != "gap":
            continue
        key = (r["category"], r.get("color"))
        if key in seen:
            continue
        seen.add(key)
        entry = {"category": r["category"], "aliases": [r["category"]]}
        if r.get("color"):
            entry["colors"] = [r["color"]]
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
