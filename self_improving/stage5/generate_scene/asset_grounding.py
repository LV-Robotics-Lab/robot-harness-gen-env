#!/usr/bin/env python3
"""Ground natural-language tabletop prompts to RoboTwin asset catalog entries."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generate_scene.asset_catalog import load_asset_catalog
from generate_scene.schemas import read_json, write_json


STOP_TERMS = {"table", "desk", "surface", "side", "left", "right", "front", "back"}


def slugify_prompt(prompt: str, max_words: int = 5) -> str:
    words = re.findall(r"[a-z0-9]+", prompt.lower())
    words = [word for word in words if word not in {"a", "an", "the", "and", "on", "of", "to", "is"}]
    if not words:
        return "scene"
    return "_".join(words[:max_words])


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _entry_terms(entry: dict[str, Any]) -> list[tuple[str, str]]:
    terms: list[tuple[str, str]] = []
    for key, match_type in [
        ("semantic_name", "exact_semantic_name"),
        ("aliases", "exact_alias"),
        ("tags", "exact_tag"),
    ]:
        value = entry.get(key)
        values = value if isinstance(value, list) else [value]
        for item in values:
            term = _norm(str(item or ""))
            if term and term not in STOP_TERMS:
                terms.append((term, match_type))
    dedup: dict[str, str] = {}
    for term, match_type in terms:
        dedup.setdefault(term, match_type)
    return sorted(dedup.items(), key=lambda item: len(item[0]), reverse=True)


def _contains_term(prompt: str, term: str) -> bool:
    pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
    return re.search(pattern, prompt) is not None


def extract_spatial_relations(prompt: str, matched_assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prompt_norm = _norm(re.sub(r"\b(a|an|the)\b", " ", prompt))
    relations: list[dict[str, Any]] = []
    mention_to_asset = {item["mention"]: item["asset_id"] for item in matched_assets}
    mentions = list(mention_to_asset)

    for subject in mentions:
        for reference in [*mentions, "table", "desk"]:
            if subject == reference:
                continue
            patterns = {
                "right_of": [
                    f"{subject} is on right side of {reference}",
                    f"{subject} on right side of {reference}",
                    f"{subject} is to right of {reference}",
                    f"{subject} to right of {reference}",
                ],
                "left_of": [
                    f"{subject} is on left side of {reference}",
                    f"{subject} on left side of {reference}",
                    f"{subject} is to left of {reference}",
                    f"{subject} to left of {reference}",
                ],
                "in_front_of": [
                    f"{subject} is in front of {reference}",
                    f"{subject} in front of {reference}",
                ],
                "behind": [
                    f"{subject} is behind {reference}",
                    f"{subject} behind {reference}",
                ],
                "near": [
                    f"{subject} is near {reference}",
                    f"{subject} near {reference}",
                    f"{subject} next to {reference}",
                ],
                "on_surface": [
                    f"{subject} is on {reference}",
                    f"{subject} on {reference}",
                ],
            }
            for relation, relation_patterns in patterns.items():
                if any(pattern in prompt_norm for pattern in relation_patterns):
                    relations.append(
                        {
                            "subject_mention": subject,
                            "subject_asset_id": mention_to_asset[subject],
                            "relation": relation,
                            "reference": reference,
                            "reference_asset_id": mention_to_asset.get(reference),
                            "direction_frame": "robot_or_dual_arm_first_person",
                        }
                    )

    related_subjects = {relation["subject_asset_id"] for relation in relations}
    for item in matched_assets:
        if not relations or item["asset_id"] not in related_subjects:
            relations.append(
                {
                    "subject_mention": item["mention"],
                    "subject_asset_id": item["asset_id"],
                    "relation": "on_surface",
                    "reference": "table",
                    "reference_asset_id": None,
                    "direction_frame": "robot_or_dual_arm_first_person",
                }
            )
    return relations


def ground_assets(
    *,
    prompt: str,
    master_catalog: dict[str, Any],
    master_catalog_path: str,
    model_provider: str = "deterministic_alias",
) -> dict[str, Any]:
    prompt_norm = _norm(prompt)
    matches: list[dict[str, Any]] = []
    seen_assets: set[str] = set()

    for entry in master_catalog.get("entries", []):
        best: tuple[str, str] | None = None
        for term, match_type in _entry_terms(entry):
            if _contains_term(prompt_norm, term):
                best = (term, match_type)
                break
        if best and entry["asset_id"] not in seen_assets:
            term, match_type = best
            seen_assets.add(entry["asset_id"])
            confidence = 1.0 if match_type in {"exact_semantic_name", "exact_alias"} else 0.75
            matches.append(
                {
                    "mention": term,
                    "asset_id": entry["asset_id"],
                    "semantic_name": entry.get("semantic_name"),
                    "match_type": match_type,
                    "confidence": confidence,
                    "reason": f"Prompt mention {term!r} matched catalog {match_type.replace('_', ' ')} for {entry['asset_id']}.",
                }
            )

    warnings: list[str] = []
    if not matches:
        warnings.append("No catalog asset matched the prompt. Add assets or use an LLM/embedding backend.")

    return {
        "schema_version": "robotwin.tabletop_asset_grounding.v0",
        "prompt": prompt,
        "direction_frame": "robot_or_dual_arm_first_person",
        "master_catalog": master_catalog_path,
        "model_provider": model_provider,
        "generated_at": date.today().isoformat(),
        "matched_assets": matches,
        "selected_asset_ids": [item["asset_id"] for item in matches],
        "unmatched_mentions": [],
        "spatial_relations": extract_spatial_relations(prompt, matches),
        "warnings": warnings,
    }


def validate_asset_grounding_result(result: dict[str, Any], master_catalog: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    entries = {entry["asset_id"]: entry for entry in master_catalog.get("entries", [])}
    if result.get("schema_version") == "robotwin.tabletop_asset_grounding.v0":
        checks.append({"name": "schema_version", "status": "pass", "message": "Schema version is v0."})
    else:
        checks.append({"name": "schema_version", "status": "fail", "message": "Expected robotwin.tabletop_asset_grounding.v0."})

    matched = result.get("matched_assets", [])
    if matched:
        checks.append({"name": "matched_assets_nonempty", "status": "pass", "message": f"Found {len(matched)} matched asset(s)."})
    else:
        checks.append({"name": "matched_assets_nonempty", "status": "fail", "message": "No assets matched."})

    for item in matched:
        asset_id = item.get("asset_id")
        status = "pass" if asset_id in entries else "fail"
        checks.append({"name": f"asset_exists:{asset_id}", "status": status, "message": f"Checked {asset_id} in master catalog."})
        confidence = item.get("confidence")
        if isinstance(confidence, (int, float)) and 0 <= float(confidence) <= 1:
            checks.append({"name": f"confidence_range:{asset_id}", "status": "pass", "message": f"confidence={confidence}"})
        else:
            checks.append({"name": f"confidence_range:{asset_id}", "status": "fail", "message": "confidence must be numeric in [0, 1]."})

    fail_count = sum(1 for check in checks if check["status"] == "fail")
    return {
        "schema_version": "robotwin.tabletop_asset_grounding_validation.v0",
        "status": "pass" if fail_count == 0 else "fail",
        "fail_count": fail_count,
        "checks": checks,
    }


def prompt_case_from_grounding(
    *,
    grounding: dict[str, Any],
    case_name: str,
    base_catalog: str = "../robotwin_tabletop_assets_master.json",
) -> dict[str, Any]:
    return {
        "catalog_version": "robotwin.tabletop_asset_catalog_case.v0",
        "catalog_name": f"{case_name}_prompt_case",
        "updated_at": date.today().isoformat(),
        "purpose": f"Prompt-specific asset selection for: {grounding['prompt']}",
        "base_catalog": base_catalog,
        "mvp_prompt": grounding["prompt"],
        "selected_asset_ids": grounding.get("selected_asset_ids", []),
        "entry_overrides": {},
        "direction_frame": grounding.get("direction_frame", "robot_or_dual_arm_first_person"),
        "asset_grounding": {
            "schema_version": grounding.get("schema_version"),
            "matched_assets": grounding.get("matched_assets", []),
            "spatial_relations": grounding.get("spatial_relations", []),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Ground a tabletop scene prompt to RoboTwin catalog assets.")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--master-catalog", default="asset_catalogs/robotwin_tabletop_assets_master.json")
    parser.add_argument("--out", required=True)
    parser.add_argument("--prompt-case-out")
    parser.add_argument("--case-name")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    master_path = Path(args.master_catalog)
    master = load_asset_catalog(master_path)
    case_name = args.case_name or slugify_prompt(args.prompt)
    result = ground_assets(
        prompt=args.prompt,
        master_catalog=master,
        master_catalog_path=str(master_path),
    )
    validation = validate_asset_grounding_result(result, master)
    result["validation"] = validation
    out_path = Path(args.out)
    write_json(out_path, result)

    if args.prompt_case_out:
        case_path = Path(args.prompt_case_out)
        if case_path.exists() and not args.overwrite:
            existing = read_json(case_path)
            result["prompt_case_path"] = str(case_path)
            result["prompt_case_status"] = "exists_not_overwritten"
            result["prompt_case_selected_asset_ids"] = existing.get("selected_asset_ids", [])
            write_json(out_path, result)
        else:
            prompt_case = prompt_case_from_grounding(grounding=result, case_name=case_name)
            write_json(case_path, prompt_case)
            result["prompt_case_path"] = str(case_path)
            result["prompt_case_status"] = "written"
            write_json(out_path, result)

    print(f"{validation['status'].upper()} {out_path}")
    return 0 if validation["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
