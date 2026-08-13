#!/usr/bin/env python3
"""Reference model-provider layer for the placement harness."""

from __future__ import annotations

import copy
from datetime import date
from typing import Any


DEFAULT_WORKSPACE = {
    "surface": "table",
    "coordinate_convention": "robot_first_person_tabletop; x is robot-left/right across the table, y is robot-front/back on the table, z is height; the RoboTwin adapter maps this frame into the simulator task frame",
    "bounds": {"x": [-0.45, 0.45], "y": [-0.35, 0.25], "z": [0.74, 1.1]},
    "spatial_regions": {
        "left_reachable_area": {
            "x": [-0.24, -0.06],
            "y": [-0.16, 0.08],
            "purpose": "reachable free tabletop area for the first mentioned object",
        },
        "right_reachable_area": {
            "x": [0.08, 0.30],
            "y": [-0.16, 0.08],
            "purpose": "reachable tabletop area for the second mentioned object",
        },
        "center_clearance_area": {
            "x": [-0.05, 0.07],
            "y": [-0.20, 0.12],
            "purpose": "free corridor for downstream manipulation",
        },
    },
}


def _entries_by_prompt_order(prompt: str, catalog: dict[str, Any]) -> list[dict[str, Any]]:
    prompt_lower = prompt.lower()
    matches: list[tuple[int, dict[str, Any]]] = []
    for entry in catalog.get("entries", []):
        aliases = [entry.get("semantic_name", ""), *entry.get("aliases", []), *entry.get("tags", [])]
        positions = [prompt_lower.find(str(alias).lower()) for alias in aliases if str(alias).lower() in prompt_lower]
        if positions:
            matches.append((min(positions), entry))
    return [entry for _pos, entry in sorted(matches, key=lambda item: item[0])]


def _entry_terms(entry: dict[str, Any]) -> list[str]:
    terms = [entry.get("semantic_name", ""), *entry.get("aliases", [])]
    unique = []
    for term in terms:
        term = str(term).lower().strip()
        if term and term not in unique:
            unique.append(term)
    return sorted(unique, key=len, reverse=True)


def _relation_text(text: str) -> str:
    words = [word for word in text.lower().split() if word not in {"a", "an", "the"}]
    return " ".join(words)


def _find_lateral_relation(prompt: str, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    prompt_lower = _relation_text(prompt)
    for subject in entries:
        for reference in entries:
            if subject["asset_id"] == reference["asset_id"]:
                continue
            for subject_term in _entry_terms(subject):
                for reference_term in _entry_terms(reference):
                    subject_term = _relation_text(subject_term)
                    reference_term = _relation_text(reference_term)
                    right_patterns = [
                        f"{subject_term} is on right side of {reference_term}",
                        f"{subject_term} is to right of {reference_term}",
                        f"{subject_term} on right side of {reference_term}",
                    ]
                    left_patterns = [
                        f"{subject_term} is on left side of {reference_term}",
                        f"{subject_term} is to left of {reference_term}",
                        f"{subject_term} on left side of {reference_term}",
                    ]
                    if any(pattern in prompt_lower for pattern in right_patterns):
                        return {
                            "type": "right_of",
                            "subject_asset_id": subject["asset_id"],
                            "object_asset_id": reference["asset_id"],
                        }
                    if any(pattern in prompt_lower for pattern in left_patterns):
                        return {
                            "type": "left_of",
                            "subject_asset_id": subject["asset_id"],
                            "object_asset_id": reference["asset_id"],
                        }
    return None


def _metadata_for_default_model(entry: dict[str, Any]) -> dict[str, Any]:
    model_id = entry.get("default_model_id", 0)
    for metadata in entry.get("model_metadata", []):
        if metadata.get("model_id") == model_id:
            return metadata
    return {}


def design_initial_spec(
    *,
    prompt: str,
    catalog: dict[str, Any],
    asset_catalog_path: str,
    model_provider: str = "codex_reference",
    variation_index: int | None = None,
    num_variations: int | None = None,
    diversity_context: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a deterministic reference Designer output.

    This provider is intentionally simple: it produces reproducible reference
    artifacts while leaving the same function boundary available for future LLM
    API providers.
    """

    selected = _entries_by_prompt_order(prompt, catalog)
    if not selected:
        raise ValueError(f"No catalog assets matched prompt: {prompt}")

    regions = ["left_reachable_area", "right_reachable_area"]
    variation_index = int(variation_index or 0)
    x_shift = [-0.04, 0.0, 0.04, -0.02, 0.02][variation_index % 5]
    y_shift = [-0.04, -0.01, 0.03, 0.06, -0.07][variation_index % 5]
    poses = [[-0.15 + x_shift, -0.04 + y_shift, 0.76], [0.18 + x_shift, -0.04 - y_shift, 0.755]]
    relation = _find_lateral_relation(prompt, selected)
    relation_slots: dict[str, tuple[str, list[float]]] = {}
    if relation:
        left_pose = [-0.22 + x_shift, -0.04 + y_shift, 0.76]
        right_pose = [0.23 + x_shift, -0.04 - y_shift, 0.755]
        if relation["type"] == "right_of":
            relation_slots[relation["subject_asset_id"]] = ("right_reachable_area", right_pose)
            relation_slots[relation["object_asset_id"]] = ("left_reachable_area", left_pose)
        else:
            relation_slots[relation["subject_asset_id"]] = ("left_reachable_area", left_pose)
            relation_slots[relation["object_asset_id"]] = ("right_reachable_area", right_pose)
    objects = []
    for idx, entry in enumerate(selected):
        model_id = entry.get("default_model_id", 0)
        metadata = _metadata_for_default_model(entry)
        semantic = entry.get("semantic_name", entry["asset_id"])
        role_candidates = entry.get("placement_affordances", {}).get("role_candidates", ["scene_object"])
        region, xyz = relation_slots.get(entry["asset_id"], (regions[min(idx, len(regions) - 1)], poses[min(idx, len(poses) - 1)]))
        placement_defaults = entry.get("placement_defaults", {})
        qpos = placement_defaults.get("qpos", [1, 0, 0, 0])
        if entry["asset_id"] == "003_plate":
            qpos = [0.5, 0.5, 0.5, 0.5]
        affordances = entry.get("placement_affordances", {})
        role = role_candidates[0]
        is_static_background = role in {"container_candidate", "support_or_target_candidate"} or affordances.get("support_surface_candidate", False)
        if "is_static" in placement_defaults:
            is_static_background = bool(placement_defaults["is_static"])
        z_policy = placement_defaults.get("z_policy", "snap_to_tabletop_on_load")
        objects.append(
            {
                "id": f"{semantic}_1",
                "semantic": semantic,
                "asset_id": entry["asset_id"],
                "model_id": model_id,
                "role": role,
                "pose": {"region": region, "xyz": xyz, "qpos": qpos, "z_policy": z_policy},
                "physical": {
                    "is_static": is_static_background,
                    "collision": True,
                    "stable_on_table": affordances.get("stable_on_table", False),
                },
                "asset_metadata": {
                    "approx_scaled_extents_m": metadata.get("approx_scaled_extents_m"),
                    "scale": metadata.get("scale"),
                    "asset_type": entry.get("asset_type", "rigid"),
                    "graspable": affordances.get("graspable", False),
                    "support_surface_candidate": affordances.get("support_surface_candidate", False),
                    "placement_defaults": placement_defaults,
                },
                "affordance_notes": [],
            }
        )

    relations = [
        {"type": "on_surface", "subject": obj["id"], "object": "table", "status": "designed"}
        for obj in objects
    ]
    if len(objects) >= 2:
        relations.append(
            {
                "type": "separated_initially",
                "subject": objects[0]["id"],
                "object": objects[1]["id"],
                "minimum_center_distance_m": 0.28,
                "status": "designed",
            }
        )
    if relation:
        subject_id = next((obj["id"] for obj in objects if obj["asset_id"] == relation["subject_asset_id"]), relation["subject_asset_id"])
        object_id = next((obj["id"] for obj in objects if obj["asset_id"] == relation["object_asset_id"]), relation["object_asset_id"])
        relations.append(
            {
                "type": relation["type"],
                "subject": subject_id,
                "object": object_id,
                "status": "designed",
            }
        )

    workspace = copy.deepcopy(DEFAULT_WORKSPACE)
    return {
        "schema_version": "robotwin.tabletop_placement.v0",
        "placement_name": "_".join([obj["semantic"] for obj in objects]) + "_table_designer_initial_v0",
        "stage": "designer_initial",
        "language_prompt": prompt,
        "asset_catalog_path": asset_catalog_path,
        "generated_by": {
            "agent": "designer",
            "model_backend": model_provider,
            "prompt_template": "skills/design-tabletop-placement/SKILL.md",
            "generated_at": date.today().isoformat(),
        },
        "workspace": workspace,
        "objects": objects,
        "relations": relations,
        "constraints": [
            "use_only_catalog_assets",
            "objects_on_table",
            "no_initial_collision",
            "keep_robot_reachable",
            "keep_camera_visible",
            "do_not_generate_task_program",
        ],
        "downstream_task_hints": ["the placed scene can be consumed by a downstream manipulation task"],
        "validation": {
            "semantic_check": "pending_designer_initial",
            "asset_check": "pending_designer_initial",
            "bounds_check": "pending_designer_initial",
            "collision_check": "pending_designer_initial",
            "stability_check": "pending_designer_initial",
            "robotwin_load_check": "pending_designer_initial",
        },
        "designer_notes": [
            "codex_reference is a deterministic reference provider, not a live LLM call.",
            "Future providers should keep the same output schema and validation gate.",
            "For lateral language relations, left/right/front/back are interpreted from the robot or dual-arm first-person viewpoint unless the prompt explicitly names another frame.",
        ],
    }


def critic_review_from_validation(
    *,
    placement: dict[str, Any],
    validation_report: dict[str, Any],
    model_provider: str = "codex_reference",
) -> dict[str, Any]:
    verdict = "accept_for_next_stage" if validation_report["status"] == "pass" else "repair_required"
    return {
        "schema_version": "robotwin.tabletop_placement_critic.v0",
        "review_name": f"{placement.get('placement_name', 'placement')}_critic_review_v0",
        "stage": "critic_static_review",
        "reviewed_placement": placement.get("placement_name"),
        "generated_by": {
            "agent": "critic",
            "model_backend": model_provider,
            "prompt_template": "skills/critique-tabletop-placement/SKILL.md",
            "generated_at": date.today().isoformat(),
        },
        "verdict": verdict,
        "summary": "Static validation passed; proceed to simulator smoke." if verdict == "accept_for_next_stage" else "Static validation found failures; repair before smoke.",
        "checks": validation_report["checks"],
        "issues": [check for check in validation_report["checks"] if check["status"] in ["fail", "warning"]],
        "repair_suggestions": [],
        "next_stage_requirements": ["Run RoboTwin smoke render and inspect the saved preview image/video."],
    }


def orchestrate_final_spec(
    *,
    designer_spec: dict[str, Any],
    critic_review: dict[str, Any],
    model_provider: str = "codex_reference",
) -> dict[str, Any]:
    final_spec = copy.deepcopy(designer_spec)
    final_spec["placement_name"] = designer_spec["placement_name"].replace("designer_initial", "final_static")
    final_spec["stage"] = "final_static_for_smoke"
    final_spec["source_designer_placement"] = "designer_initial_placement.json"
    final_spec["source_critic_review"] = "critic_review.json"
    final_spec["generated_by"] = {
        "agent": "orchestrator",
        "model_backend": model_provider,
        "prompt_template": "skills/orchestrate-placement-pipeline/SKILL.md",
        "generated_at": date.today().isoformat(),
    }
    accepted = critic_review.get("verdict") == "accept_for_next_stage"
    final_spec["orchestrator_decision"] = {
        "decision": "accept_for_smoke" if accepted else "repair_designer",
        "reason": "Critic accepted the static placement." if accepted else "Critic found static issues.",
        "remaining_uncertainties": [
            "Exact simulator contact must be confirmed by RoboTwin smoke.",
            "Camera visibility must be confirmed by render evidence.",
        ],
    }
    final_spec["validation"] = {
        "semantic_check": "pass_static" if accepted else "repair_required",
        "asset_check": "pass_static" if accepted else "repair_required",
        "bounds_check": "pass_static" if accepted else "repair_required",
        "approx_collision_check": "pass_static" if accepted else "repair_required",
        "stability_metadata_check": "pass_static" if accepted else "repair_required",
        "robotwin_load_check": "pending_simulator_smoke",
        "render_visibility": "pending_render_review",
        "simulator_stability": "pending_simulator_smoke",
    }
    return final_spec


def validation_plan_for(final_spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "robotwin.tabletop_validation_plan.v0",
        "plan_name": f"{final_spec.get('placement_name', 'placement')}_validation_plan",
        "stage": "orchestrator_validation_plan",
        "target_placement": "final_placement.json",
        "language_prompt": final_spec.get("language_prompt"),
        "static_checks": ["schema", "asset_exists", "model_id_exists", "pose_in_workspace_bounds", "approx_no_initial_collision"],
        "simulator_smoke_checks": ["robotwin_scene_load", "settling_stability", "save_head_and_observer_camera"],
        "render_or_visual_checks": ["object_visibility", "visual_prompt_match", "floating_or_penetration_failure_modes"],
        "pass_criteria": [
            "Static validation passes.",
            "RoboTwin smoke exits PASS.",
            "Preview image/video visibly contains the requested tabletop objects.",
        ],
    }
