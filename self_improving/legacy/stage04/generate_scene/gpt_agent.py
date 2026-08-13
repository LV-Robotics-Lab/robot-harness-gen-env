#!/usr/bin/env python3
"""LLM text agents for RoboTwin tabletop scene generation."""

from __future__ import annotations

import copy
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generate_scene import moonshot_client, openai_client
from generate_scene.schemas import write_json

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


def _load_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


def _uses_openai(provider: str) -> bool:
    return provider.lower() in {"openai", "gpt", "gpt5", "gpt-5", "gpt5.5", "gpt-5.5"}


def _json_chat_for_provider(
    *,
    provider: str,
    kind: str,
    system: str,
    user: str | list[dict[str, Any]],
    model: str | None = None,
) -> dict[str, Any]:
    if _uses_openai(provider):
        return openai_client.json_chat(system=system, user=user, model=model or openai_client.model_from_env(kind))
    return moonshot_client.json_chat(system=system, user=user, model=model or moonshot_client.model_from_env(kind))


def _compact_catalog(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    compact = []
    for entry in catalog.get("entries", []):
        compact.append(
            {
                "asset_id": entry.get("asset_id"),
                "semantic_name": entry.get("semantic_name"),
                "aliases": entry.get("aliases", []),
                "tags": entry.get("tags", []),
                "asset_type": entry.get("asset_type", "rigid"),
                "default_model_id": entry.get("default_model_id", 0),
                "available_model_ids": entry.get("available_model_ids", []),
                "placement_defaults": entry.get("placement_defaults", {}),
                "placement_affordances": entry.get("placement_affordances", {}),
                "model_metadata": entry.get("model_metadata", []),
            }
        )
    return compact


def _as_json_text(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _merge_asset_metadata(spec: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    """Fill object metadata from the catalog so generated specs stay loadable."""

    entries = {entry["asset_id"]: entry for entry in catalog.get("entries", [])}
    result = copy.deepcopy(spec)
    for obj in result.get("objects", []):
        entry = entries.get(obj.get("asset_id"))
        if not entry:
            continue
        model_id = obj.get("model_id", entry.get("default_model_id", 0))
        obj["model_id"] = model_id
        obj.setdefault("semantic", entry.get("semantic_name", obj.get("asset_id")))
        obj.setdefault("role", "scene_object")
        obj.setdefault("physical", {})
        affordances = entry.get("placement_affordances", {})
        defaults = entry.get("placement_defaults", {})
        obj["physical"].setdefault("collision", True)
        obj["physical"].setdefault("stable_on_table", affordances.get("stable_on_table", False))
        obj["physical"].setdefault("is_static", False)
        if "is_static" in defaults:
            obj["physical"]["is_static"] = bool(defaults["is_static"])

        metadata = {}
        for item in entry.get("model_metadata", []):
            if item.get("model_id") == model_id:
                metadata = item
                break
        obj["asset_metadata"] = {
            "approx_scaled_extents_m": metadata.get("approx_scaled_extents_m"),
            "scale": metadata.get("scale"),
            "asset_type": entry.get("asset_type", "rigid"),
            "graspable": affordances.get("graspable", False),
            "support_surface_candidate": affordances.get("support_surface_candidate", False),
            "placement_defaults": defaults,
        }
        pose = obj.setdefault("pose", {})
        if defaults.get("force_qpos"):
            pose["qpos"] = defaults.get("qpos", [1, 0, 0, 0])
        else:
            pose.setdefault("qpos", defaults.get("qpos", [1, 0, 0, 0]))
        pose.setdefault("z_policy", defaults.get("z_policy", "snap_to_tabletop_on_load"))
    return result


def moonshot_ground_assets(
    *,
    prompt: str,
    master_catalog: dict[str, Any],
    master_catalog_path: str,
    model_provider: str = "moonshot",
    model: str | None = None,
) -> dict[str, Any]:
    """Use an external LLM to decompose a prompt and select catalog assets."""

    system = _load_prompt("asset_grounding_agent.md")
    user = {
        "task": "Ground natural-language tabletop scene prompt to RoboTwin catalog assets.",
        "schema": {
            "schema_version": "robotwin.tabletop_asset_grounding.v0",
            "prompt": prompt,
            "direction_frame": "robot_or_dual_arm_first_person",
            "master_catalog": master_catalog_path,
            "model_provider": model_provider,
            "generated_at": date.today().isoformat(),
            "matched_assets": [
                {
                    "mention": "text mention from prompt",
                    "asset_id": "catalog asset_id",
                    "semantic_name": "catalog semantic_name",
                    "match_type": "llm_catalog_selection",
                    "confidence": 0.0,
                    "reason": "short reason",
                }
            ],
            "selected_asset_ids": ["catalog asset ids in prompt order"],
            "unmatched_mentions": [],
            "spatial_relations": [
                {
                    "subject_mention": "asset mention",
                    "subject_asset_id": "catalog asset_id",
                    "relation": "on_surface | left_of | right_of | in_front_of | behind | near | inside",
                    "reference": "table or another mention",
                    "reference_asset_id": None,
                    "direction_frame": "robot_or_dual_arm_first_person",
                }
            ],
            "warnings": [],
        },
        "prompt": prompt,
        "catalog_entries": _compact_catalog(master_catalog),
        "hard_constraints": [
            "Do not invent asset ids.",
            "Select only assets explicitly required by the prompt.",
            "For ordinary tabletop scenes, add on_surface relations to table.",
        ],
    }
    result = _json_chat_for_provider(
        provider=model_provider,
        kind="text",
        system=system,
        user=_as_json_text(user),
        model=model,
    )
    result["schema_version"] = "robotwin.tabletop_asset_grounding.v0"
    result["prompt"] = prompt
    result["direction_frame"] = "robot_or_dual_arm_first_person"
    result["master_catalog"] = master_catalog_path
    result["model_provider"] = model_provider
    result.setdefault("generated_at", date.today().isoformat())
    result.setdefault("warnings", [])
    result.setdefault("unmatched_mentions", [])
    result["selected_asset_ids"] = [item.get("asset_id") for item in result.get("matched_assets", []) if item.get("asset_id")]
    return result


def moonshot_design_initial_spec(
    *,
    prompt: str,
    catalog: dict[str, Any],
    asset_catalog_path: str,
    model_provider: str = "moonshot",
    model: str | None = None,
    variation_index: int | None = None,
    num_variations: int | None = None,
    diversity_context: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Use Moonshot/Kimi as Designer Agent to create TabletopPlacementSpec."""

    system = _load_prompt("designer_agent.md")
    user = {
        "task": "Generate an initial tabletop placement spec.",
        "prompt": prompt,
        "asset_catalog_path": asset_catalog_path,
        "catalog_entries": _compact_catalog(catalog),
        "required_schema": {
            "schema_version": "robotwin.tabletop_placement.v0",
            "placement_name": "short_scene_name_designer_initial_v0",
            "stage": "designer_initial",
            "language_prompt": prompt,
            "asset_catalog_path": asset_catalog_path,
            "generated_by": {
                "agent": "designer",
                "model_backend": model_provider,
                "prompt_template": "generate_scene/gpt_agent.py",
                "generated_at": date.today().isoformat(),
            },
            "workspace": {
                "surface": "table",
                "coordinate_convention": "robot_first_person_tabletop; x negative=robot-left, x positive=robot-right, y positive=front/away, z up",
                "bounds": {"x": [-0.45, 0.45], "y": [-0.35, 0.25], "z": [0.74, 1.1]},
                "spatial_regions": {
                    "left_reachable_area": {"x": [-0.24, -0.06], "y": [-0.16, 0.08]},
                    "right_reachable_area": {"x": [0.08, 0.30], "y": [-0.16, 0.08]},
                    "center_clearance_area": {"x": [-0.05, 0.07], "y": [-0.20, 0.12]},
                },
            },
            "objects": [
                {
                    "id": "semantic_1",
                    "semantic": "catalog semantic_name",
                    "asset_id": "catalog asset_id",
                    "model_id": 0,
                    "role": "scene_object",
                    "pose": {
                        "region": "left_reachable_area",
                        "xyz": [-0.15, -0.04, 0.76],
                        "qpos": [1, 0, 0, 0],
                        "z_policy": "snap_to_tabletop_on_load",
                    },
                    "physical": {"is_static": False, "collision": True, "stable_on_table": True},
                    "affordance_notes": [],
                }
            ],
            "relations": [],
            "constraints": [
                "use_only_catalog_assets",
                "objects_on_table",
                "no_initial_collision",
                "keep_robot_reachable",
                "keep_camera_visible",
                "do_not_generate_task_program",
            ],
            "downstream_task_hints": [],
            "validation": {},
            "designer_notes": [],
        },
        "placement_rules": [
            "Use table z around 0.75 to 0.78; snap_to_tabletop_on_load may correct it.",
            "Keep approximate object centers separated by at least 0.28m unless the prompt asks for contact.",
            "For 'A right of B', A.x must be greater than B.x.",
            "For 'A left of B', A.x must be less than B.x.",
            "Do not output task code or robot actions.",
        ],
        "diversity_request": {
            "variation_index": variation_index,
            "num_variations": num_variations,
            "previous_accepted_placements": diversity_context or [],
            "requirements": [
                "Preserve all requested objects and semantic relations from the prompt.",
                "Make this variation visibly distinct from previous accepted placements by changing valid x/y positions, spacing, and in-plane yaw.",
                "Do not change left/right/front/back truth values; directions use the robot or dual-arm first-person frame.",
                "Keep thin everyday objects flat on the tabletop; only vary in-plane yaw unless the prompt requests upright placement.",
                "Keep the scene reachable, visible, stable, and collision-free.",
            ],
        },
    }
    spec = _json_chat_for_provider(
        provider=model_provider,
        kind="text",
        system=system,
        user=_as_json_text(user),
        model=model,
    )
    spec["schema_version"] = "robotwin.tabletop_placement.v0"
    spec["stage"] = "designer_initial"
    spec["language_prompt"] = prompt
    spec["asset_catalog_path"] = asset_catalog_path
    spec.setdefault("generated_by", {})
    spec["generated_by"].update(
        {
            "agent": "designer",
            "model_backend": model_provider,
            "prompt_template": "generate_scene/gpt_agent.py",
            "generated_at": date.today().isoformat(),
        }
    )
    spec.setdefault("constraints", ["use_only_catalog_assets", "objects_on_table", "no_initial_collision"])
    spec.setdefault("relations", [])
    spec.setdefault("downstream_task_hints", [])
    spec.setdefault("validation", {})
    spec.setdefault("designer_notes", [])
    return _merge_asset_metadata(spec, catalog)


def moonshot_critic_review(
    *,
    placement: dict[str, Any],
    validation_report: dict[str, Any],
    model_provider: str = "moonshot",
    model: str | None = None,
) -> dict[str, Any]:
    system = _load_prompt("static_critic_agent.md")
    user = {
        "task": "Review placement and static validation report.",
        "placement": placement,
        "validation_report": validation_report,
        "required_fields": ["schema_version", "stage", "verdict", "summary", "issues", "repair_suggestions"],
        "verdict_values": ["accept_for_next_stage", "repair_required"],
    }
    review = _json_chat_for_provider(
        provider=model_provider,
        kind="text",
        system=system,
        user=_as_json_text(user),
        model=model,
    )
    review["schema_version"] = "robotwin.tabletop_placement_critic.v0"
    review["stage"] = "critic_static_review"
    review["reviewed_placement"] = placement.get("placement_name")
    review.setdefault("generated_by", {})
    review["generated_by"].update(
        {
            "agent": "critic",
            "model_backend": model_provider,
            "prompt_template": "generate_scene/gpt_agent.py",
            "generated_at": date.today().isoformat(),
        }
    )
    if validation_report.get("status") != "pass":
        review["verdict"] = "repair_required"
    review.setdefault("issues", [])
    review.setdefault("repair_suggestions", [])
    review.setdefault("next_stage_requirements", ["Run RoboTwin smoke render and inspect preview images."])
    return review


def moonshot_orchestrate_final_spec(
    *,
    designer_spec: dict[str, Any],
    critic_review: dict[str, Any],
    validation_report: dict[str, Any],
    catalog: dict[str, Any],
    model_provider: str = "moonshot",
    model: str | None = None,
) -> dict[str, Any]:
    """Use Moonshot/Kimi as Orchestrator Agent to accept or repair the placement."""

    if critic_review.get("verdict") == "accept_for_next_stage" and validation_report.get("status") == "pass":
        final_spec = copy.deepcopy(designer_spec)
    else:
        system = _load_prompt("orchestrator_agent.md")
        user = {
            "task": "Repair the designer placement if needed and output final placement.",
            "designer_spec": designer_spec,
            "critic_review": critic_review,
            "validation_report": validation_report,
            "catalog_entries": _compact_catalog(catalog),
            "hard_constraints": [
                "Use only catalog asset ids and model ids.",
                "Keep objects inside workspace bounds.",
                "Avoid approximate collision unless a relation explicitly requires containment such as inside/contained_in.",
                "Do not generate task code or robot actions.",
            ],
        }
        final_spec = _json_chat_for_provider(
            provider=model_provider,
            kind="text",
            system=system,
            user=_as_json_text(user),
            model=model,
        )
        final_spec = _merge_asset_metadata(final_spec, catalog)

    final_spec["schema_version"] = "robotwin.tabletop_placement.v0"
    final_spec["placement_name"] = str(final_spec.get("placement_name", designer_spec.get("placement_name", "placement"))).replace(
        "designer_initial", "final_static"
    )
    if "final_static" not in final_spec["placement_name"]:
        final_spec["placement_name"] += "_final_static_v0"
    final_spec["stage"] = "final_static_for_smoke"
    final_spec["source_designer_placement"] = "designer_initial_placement.json"
    final_spec["source_critic_review"] = "critic_review.json"
    final_spec.setdefault("generated_by", {})
    final_spec["generated_by"].update(
        {
            "agent": "orchestrator",
            "model_backend": model_provider,
            "prompt_template": "generate_scene/gpt_agent.py",
            "generated_at": date.today().isoformat(),
        }
    )
    final_spec["orchestrator_decision"] = {
        "decision": "accept_for_smoke" if critic_review.get("verdict") == "accept_for_next_stage" else "repair_then_smoke",
        "reason": critic_review.get("summary", "Moonshot orchestrator finalized the placement."),
        "remaining_uncertainties": [
            "Exact simulator contact must be confirmed by RoboTwin smoke.",
            "Semantic visual match must be confirmed by observation_agent.",
        ],
    }
    return final_spec


def moonshot_repair_from_visual_review(
    *,
    final_spec: dict[str, Any],
    visual_review: dict[str, Any],
    catalog: dict[str, Any],
    model_provider: str = "moonshot",
    model: str | None = None,
) -> dict[str, Any]:
    """Repair a final placement using VLM visual feedback."""

    system = _load_prompt("visual_repair_agent.md")
    user = {
        "task": "Repair final placement from visual/VLM feedback.",
        "final_spec": final_spec,
        "visual_review": visual_review,
        "catalog_entries": _compact_catalog(catalog),
        "repair_rules": [
            "If an object floats or penetrates the table, adjust z_policy/qpos or object z.",
            "If objects collide, increase xy separation.",
            "If left/right is wrong, fix x coordinates in robot-first-person frame.",
            "If orientation is wrong, repair qpos using catalog placement_defaults when available.",
            "Keep all object asset_id and model_id values from the catalog.",
        ],
    }
    repaired = _json_chat_for_provider(
        provider=model_provider,
        kind="text",
        system=system,
        user=_as_json_text(user),
        model=model,
    )
    repaired = _merge_asset_metadata(repaired, catalog)
    repaired["schema_version"] = "robotwin.tabletop_placement.v0"
    repaired["stage"] = "final_static_for_smoke"
    repaired["placement_name"] = str(final_spec.get("placement_name", "placement")).replace("_visual_repaired", "")
    repaired["placement_name"] += "_visual_repaired"
    repaired.setdefault("generated_by", {})
    repaired["generated_by"].update(
        {
            "agent": "orchestrator_visual_repair",
            "model_backend": model_provider,
            "prompt_template": "generate_scene/gpt_agent.py",
            "generated_at": date.today().isoformat(),
        }
    )
    repaired["source_visual_review"] = "visual_review.json"
    repaired["orchestrator_decision"] = {
        "decision": "repair_from_visual_review",
        "reason": visual_review.get("summary", "Visual review requested repair."),
        "remaining_uncertainties": [
            "Repaired placement must pass static validation, RoboTwin smoke, and visual review again.",
        ],
    }
    return repaired


def moonshot_repair_from_scene_critic(
    *,
    placement_spec: dict[str, Any],
    scene_critic_review: dict[str, Any],
    catalog: dict[str, Any],
    model_provider: str = "moonshot",
    model: str | None = None,
) -> dict[str, Any]:
    """Repair a placement using the unified Scene Critic report."""

    system = _load_prompt("visual_repair_agent.md")
    user = {
        "task": "Repair tabletop placement from unified Scene Critic feedback.",
        "final_spec": placement_spec,
        "visual_review": scene_critic_review,
        "scene_critic_review": scene_critic_review,
        "catalog_entries": _compact_catalog(catalog),
        "repair_rules": [
            "If rule_static_check failed, repair schema, asset ids, model ids, workspace bounds, or approximate collisions before rendering again.",
            "If simulator_smoke failed, repair placement, qpos, z_policy, loader defaults, or asset physical settings.",
            "If visual_render_review failed, repair object identity, pose.xyz, pose.qpos, spacing, table contact, containment, orientation, or occlusion.",
            "For containment such as 'in' or 'inside', keep the contained object visually inside the container while avoiding visible penetration.",
            "Keep all object asset_id and model_id values from the catalog.",
            "Do not generate play_once(), task code, robot actions, or check_success().",
        ],
    }
    repaired = _json_chat_for_provider(
        provider=model_provider,
        kind="text",
        system=system,
        user=_as_json_text(user),
        model=model,
    )
    repaired = _merge_asset_metadata(repaired, catalog)
    repaired["schema_version"] = "robotwin.tabletop_placement.v0"
    repaired["stage"] = "designer_render_loop_repaired"
    repaired["placement_name"] = str(placement_spec.get("placement_name", "placement")).replace("_scene_critic_repaired", "")
    repaired["placement_name"] += "_scene_critic_repaired"
    repaired.setdefault("generated_by", {})
    repaired["generated_by"].update(
        {
            "agent": "orchestrator_scene_critic_repair",
            "model_backend": model_provider,
            "prompt_template": "generate_scene/gpt_agent.py",
            "generated_at": date.today().isoformat(),
        }
    )
    repaired["source_scene_critic_review"] = "scene_critic_review.json"
    repaired["orchestrator_decision"] = {
        "decision": "repair_from_scene_critic",
        "reason": scene_critic_review.get("summary", "Scene Critic requested repair."),
        "remaining_uncertainties": [
            "Repaired placement must pass preflight, RoboTwin smoke, and Scene Critic again.",
        ],
    }
    return repaired


def save_agent_response(path: Path, data: dict[str, Any]) -> None:
    """Helper for scripts that want to persist raw agent outputs."""

    write_json(path, data)
