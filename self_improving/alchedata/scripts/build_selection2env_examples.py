#!/usr/bin/env python3
"""Build normalized selection2env artifacts from the RoboTwin static runs."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXTERNAL = ROOT / "external" / "robotwin-text2env-demo"
CATALOG = EXTERNAL / "asset_catalogs" / "robotwin_tabletop_assets_master.json"
ARTIFACT_ROOT = ROOT / "artifacts"
ROBOTWIN_DISCOVERED_CATALOG = ARTIFACT_ROOT / "adapter_catalog" / "robotwin_discovered_catalog.json"
AGENTICSIM_CATALOG = EXTERNAL / "asset_catalogs" / "agenticsim_placement_assets.json"
ARTICRAFT_MANIFEST = ARTIFACT_ROOT / "adapter_catalog" / "articraft10k_manifest.json"
KNOWN_WORKSPACE_ROOTS = (
    Path("/home/jingxiang/workspace/alchedata-self-improving-agents"),
    Path("/Users/boris/workspace/alchedata-self-improving-agents"),
)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(EXTERNAL) not in sys.path:
    sys.path.insert(0, str(EXTERNAL))

from generate_scene.catalog_sources import (  # noqa: E402
    build_candidate_search,
    normalize_agenticsim_catalog,
)
from scripts.selection2env_contract import sha256_file  # noqa: E402

SUPPORTED_CASES = [
    (
        "task_apple_plate",
        "probe_static_apple_plate",
        "smoke_asset_apple_plate",
        "smoke_basetask_apple_plate",
        "collect_dryrun_apple_plate",
        "scene_apple_plate_shared_two_task_v0",
    ),
    (
        "task_laptop_knife",
        "probe_static_laptop_knife",
        "smoke_asset_laptop_knife",
        "smoke_basetask_laptop_knife",
        "collect_dryrun_laptop_knife",
        "scene_laptop_knife_v0",
    ),
    (
        "task_vegetable_basket",
        "probe_static_vegetable_basket",
        "smoke_asset_vegetable_basket",
        "smoke_basetask_vegetable_basket",
        "collect_dryrun_vegetable_basket",
        "scene_vegetable_basket_v0",
    ),
]

UNSUPPORTED_CASE = {
    "task_id": "task_drawer_mug_blocker",
    "task_text": "open the drawer and place the mug inside",
    "scene_id": "drawer_container_scene_missing_assets",
    "run_dir": "probe_static_drawer_mug_unified",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def normalized_artifact_path(value: str) -> str:
    path = Path(value)
    if not path.is_absolute():
        return str(path)
    for workspace_root in KNOWN_WORKSPACE_ROOTS:
        try:
            return str(path.relative_to(workspace_root))
        except ValueError:
            continue
    return str(path)


def load_catalogs() -> dict[str, dict[str, Any]]:
    return {
        "robotwin": read_json(ROBOTWIN_DISCOVERED_CATALOG),
        "agenticsim": read_json(AGENTICSIM_CATALOG),
        "articraft10k": read_json(ARTICRAFT_MANIFEST),
    }


def catalog_search(prompt: str, catalogs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return build_candidate_search(
        prompt=prompt,
        robotwin_catalog=catalogs["robotwin"],
        robotwin_catalog_path=str(ROBOTWIN_DISCOVERED_CATALOG.relative_to(ROOT)),
        agenticsim_catalog=catalogs["agenticsim"],
        agenticsim_catalog_path=str(AGENTICSIM_CATALOG.relative_to(ROOT)),
        articraft_manifest=catalogs["articraft10k"],
        articraft_manifest_path=str(ARTICRAFT_MANIFEST.relative_to(ROOT)),
        limit_per_source=8,
    )


def candidate_rows(spec: dict[str, Any], search: dict[str, Any]) -> list[dict[str, Any]]:
    selected = {obj["asset_id"] for obj in spec.get("objects", [])}
    rows: list[dict[str, Any]] = []
    for entry in search.get("matches", []):
        backend_asset_id = entry.get("backend_asset_id") or entry["asset_id"]
        accepted = bool(entry.get("selection_eligible")) and backend_asset_id in selected
        rows.append(
            {
                "asset_id": entry["asset_id"],
                "semantic_name": entry.get("semantic_name", entry["asset_id"]),
                "catalog_source": entry["catalog_source"],
                "backend_asset_id": backend_asset_id,
                "execution_status": entry["execution_status"],
                "selection_eligible": bool(entry["selection_eligible"]),
                "decision": "accepted" if accepted else "rejected",
                "reason": (
                    "selected by placement spec and execution-eligible"
                    if accepted
                    else "matched task text but was not selected or did not pass the execution gate"
                ),
            }
        )
    return rows


def selected_assets(spec: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for obj in spec.get("objects", []):
        rows.append(
            {
                "object_id": obj["id"],
                "asset_id": obj["asset_id"],
                "model_id": int(obj.get("model_id", 0)),
                "role": obj.get("role", "object"),
                "semantic": obj.get("semantic", obj["asset_id"]),
            }
        )
    return rows


def pose_constraints(spec: dict[str, Any]) -> list[dict[str, Any]]:
    constraints = []
    for obj in spec.get("objects", []):
        pose = obj.get("pose", {})
        constraints.append(
            {
                "object_id": obj["id"],
                "region": pose.get("region"),
                "xyz": pose.get("xyz"),
                "qpos": pose.get("qpos"),
                "z_policy": pose.get("z_policy", "snap_to_tabletop_on_load"),
                "physical": obj.get("physical", {}),
            }
        )
    return constraints


def task_binding_for(task_id: str, spec: dict[str, Any]) -> dict[str, str]:
    by_semantic = {str(obj.get("semantic", "")).lower(): str(obj["id"]) for obj in spec.get("objects", [])}
    if task_id == "task_apple_plate":
        return {
            "template": "place_on",
            "source_object_id": by_semantic["apple"],
            "target_object_id": by_semantic["plate"],
        }
    if task_id == "task_laptop_knife":
        return {
            "template": "place_right_of",
            "source_object_id": by_semantic["laptop"],
            "target_object_id": by_semantic["knife"],
        }
    if task_id == "task_vegetable_basket":
        source_id = by_semantic.get("vegetable") or by_semantic["vagetable"]
        return {
            "template": "place_in",
            "source_object_id": source_id,
            "target_object_id": by_semantic["basket"],
        }
    raise ValueError(f"No task binding for {task_id}")


def verifier_for(binding: dict[str, str]) -> dict[str, Any]:
    relation_by_template = {
        "place_on": "on",
        "place_in": "in",
        "place_in_region": "in_region",
        "place_right_of": "right_of",
    }
    verifier: dict[str, Any] = {
        "type": "simulator_state_and_visual",
        "relation": relation_by_template[binding["template"]],
        "source_object_id": binding["source_object_id"],
        "success_conditions": [
            "generated play_once completes without planner failure",
            "simulator-state relation predicate passes",
            "both grippers are open after release",
            "head and observer camera artifacts are nonblank",
        ],
    }
    if "target_object_id" in binding:
        verifier["target_object_id"] = binding["target_object_id"]
    else:
        verifier["target_region"] = binding["target_region"]
    return verifier


def task_program_input(
    *,
    task_id: str,
    language_prompt: str,
    scene_id: str,
    placement_path: Path,
    binding: dict[str, str],
    variant: str,
    execution_report: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": "alchedata.robotwin_task_program_input.v0",
        "task_id": task_id,
        "variant": variant,
        "language_prompt": language_prompt,
        "scene_id": scene_id,
        "placement_spec": str(placement_path.relative_to(ROOT)),
        "placement_sha256": sha256_file(placement_path),
        "task_binding": binding,
        "robotwin_adapter": {
            "backend": "RoboTwin/SAPIEN",
            "entrypoint": "scripts/run_generated_selection2env_rollout_probe.py",
            "task_config": "demo_clean",
            "python_env": "/home/jingxiang/miniconda3/envs/robotwin-5090",
            "requires_robotwin_root": True,
        },
        "verifier": verifier_for(binding),
    }
    if execution_report:
        result["execution_evidence"] = {"rollout_report": execution_report}
    return result


def smoke_artifacts(smoke_name: str, prefix: str) -> dict[str, str]:
    smoke_dir = ROOT / "runs" / smoke_name
    return {
        f"{prefix}_smoke_report": str((smoke_dir / "smoke_report.json").relative_to(ROOT)),
        f"{prefix}_observer_camera": str((smoke_dir / "observer_camera.png").relative_to(ROOT)),
        f"{prefix}_head_camera": str((smoke_dir / "head_camera.png").relative_to(ROOT)),
    }


def smoke_status(smoke_name: str, scope: str) -> dict[str, Any]:
    report_path = ROOT / "runs" / smoke_name / "smoke_report.json"
    if not report_path.exists():
        return {"status": "missing", "report": str(report_path.relative_to(ROOT))}
    report = read_json(report_path)
    object_count = report.get("object_count")
    if object_count is None:
        object_count = len(report.get("initial_poses", {}))
    return {
        "status": report.get("status", "unknown"),
        "object_count": object_count,
        "pose_delta_norm_m": report.get("pose_delta_norm_m", {}),
        "pixel_stats": report.get("pixel_stats", {}),
        "report": str(report_path.relative_to(ROOT)),
        "scope": scope,
    }


def collect_status(collect_name: str) -> dict[str, Any]:
    report_path = ROOT / "runs" / collect_name / "collect_report.json"
    if not report_path.exists():
        return {"status": "missing", "report": str(report_path.relative_to(ROOT))}
    report = read_json(report_path)
    return {
        "status": report.get("status", "unknown"),
        "episode_count": report.get("episode_count", 0),
        "object_count": report.get("object_count", 0),
        "observation_file_count": report.get("observation_file_count", 0),
        "policy_execution": report.get("policy_execution", "unknown"),
        "task_success_claim": report.get("task_success_claim", "unknown"),
        "dataset_manifest": normalized_artifact_path(report.get("dataset_manifest", ""))
        if report.get("dataset_manifest")
        else "",
        "report": str(report_path.relative_to(ROOT)),
        "scope": "robotwin_base_task_observation_and_object_state_trace",
    }


def collect_artifacts(collect_name: str) -> dict[str, str]:
    collect_dir = ROOT / "runs" / collect_name
    return {
        "collect_report": str((collect_dir / "collect_report.json").relative_to(ROOT)),
        "collect_dataset_manifest": str((collect_dir / "dataset_manifest.json").relative_to(ROOT)),
        "collect_object_states": str((collect_dir / "episode_000" / "object_states.jsonl").relative_to(ROOT)),
        "collect_scene_info": str((collect_dir / "episode_000" / "scene_info.json").relative_to(ROOT)),
    }


def generated_action_artifacts(task_id: str) -> dict[str, str]:
    if task_id != "task_apple_plate":
        return {}
    return {
        "generated_action_repair_placement": "runs/probe_static_apple_plate_action_repair/final_placement.json",
        "generated_action_rollout_report": "runs/generated_rollout_apple_plate_action_repair_pass/rollout_report.json",
        "generated_action_rollout_events": "runs/generated_rollout_apple_plate_action_repair_pass/events.jsonl",
        "generated_action_rollout_move_events": "runs/generated_rollout_apple_plate_action_repair_pass/move_events.jsonl",
        "generated_action_rollout_observer_video": "runs/generated_rollout_apple_plate_action_repair_pass/observer_rollout_probe.mp4",
    }


def generated_action_status(task_id: str) -> dict[str, Any]:
    if task_id != "task_apple_plate":
        return {
            "status": "not_run_for_task",
            "scope": "generated_selection2env_play_once_action_repair",
            "message": "No generated action-repair play_once is attached to this normalized task; apple/plate and can/basket side repairs are tracked in the generated action-repair summary.",
        }
    report = read_json(ROOT / "runs" / "generated_rollout_apple_plate_action_repair_pass" / "rollout_report.json")
    return {
        "status": report.get("status"),
        "probe_type": report.get("probe_type"),
        "check_success": report.get("check_success"),
        "plan_success": report.get("plan_success"),
        "move_event_count": report.get("move_event_count"),
        "relation_metrics": report.get("relation_metrics", {}),
        "report": "runs/generated_rollout_apple_plate_action_repair_pass/rollout_report.json",
        "scope": "generated_selection2env_play_once_action_repair",
    }


def generated_collection_artifacts(task_id: str) -> dict[str, str]:
    if task_id != "task_apple_plate":
        return {}
    return {
        "generated_rollout_collection_report": "runs/generated_collect_apple_plate_action_repair/collection_report.json",
        "generated_rollout_collection_manifest": "runs/generated_collect_apple_plate_action_repair/dataset_manifest.json",
        "generated_rollout_collection_events": "runs/generated_collect_apple_plate_action_repair/events.jsonl",
    }


def generated_collection_status(task_id: str) -> dict[str, Any]:
    if task_id != "task_apple_plate":
        return {
            "status": "not_run_for_task",
            "scope": "generated_selection2env_multi_episode_play_once_collection",
            "message": "No generated rollout collection is attached to this normalized task; apple/plate and can/basket side repairs are tracked in the generated action-repair summary.",
        }
    report = read_json(ROOT / "runs" / "generated_collect_apple_plate_action_repair" / "collection_report.json")
    return {
        "status": report.get("status"),
        "episode_count": report.get("episode_count"),
        "pass_count": report.get("pass_count"),
        "fail_count": report.get("fail_count"),
        "policy_execution": report.get("policy_execution"),
        "learned_policy_training": report.get("learned_policy_training"),
        "learned_policy_evaluation": report.get("learned_policy_evaluation"),
        "dataset_manifest": report.get("dataset_manifest"),
        "report": "runs/generated_collect_apple_plate_action_repair/collection_report.json",
        "scope": "generated_selection2env_multi_episode_play_once_collection",
    }


def generated_eval_artifacts(task_id: str) -> dict[str, str]:
    if task_id != "task_apple_plate":
        return {}
    return {
        "generated_policy_evaluate_report": "runs/act_eval_native_sync_rgb_chunk161_1200e_best/evaluate_report.json",
        "generated_policy_evaluate_run_state": "runs/act_eval_native_sync_rgb_chunk161_1200e_best/run_state.json",
        "generated_policy_evaluate_events": "runs/act_eval_native_sync_rgb_chunk161_1200e_best/events.jsonl",
        "generated_policy_diagnosis": "artifacts/diagnosis/native_act_closed_loop_diagnosis.json",
    }


def generated_eval_status(task_id: str) -> dict[str, Any]:
    if task_id != "task_apple_plate":
        return {
            "status": "not_run_for_task",
            "scope": "generated_selection2env_learned_act_evaluate",
            "message": "The bounded learned-ACT /evaluate adapter currently covers only apple/plate.",
        }
    report = read_json(ROOT / "runs" / "act_eval_native_sync_rgb_chunk161_1200e_best" / "evaluate_report.json")
    return {
        "status": report.get("status"),
        "episode_count": report.get("episode_count"),
        "execution_count": report.get("execution_count"),
        "success_count": report.get("success_count"),
        "policy_success_rate": report.get("policy_success_rate"),
        "held_out_seeds": report.get("held_out_seeds", []),
        "all_eval_seeds_held_out": report.get("all_eval_seeds_held_out"),
        "policy_result": report.get("policy_result"),
        "next_data_requirement": report.get("next_data_requirement"),
        "report": "runs/act_eval_native_sync_rgb_chunk161_1200e_best/evaluate_report.json",
        "scope": "generated_selection2env_learned_act_evaluate",
    }


def policy_gate_blocker(task_id: str) -> dict[str, str]:
    if task_id == "task_apple_plate":
        return {
            "code": "DEFAULT_ACT_PLACEMENT_ROBUSTNESS_FAILED",
            "message": (
                "The retained ACT recovery branch scores 1/4 on varied-placement holdout. A separate privileged "
                "pose-conditioned open-loop policy passes the bounded SceneAgent gate at 4/4 held-out, 4/4 declared "
                "domain randomization, and 3/3 fixed-placement can/basket; this does not repair or promote ACT."
            ),
            "owner": "Zheng Ye / Gaochen",
        }
    return {
        "code": "LEARNED_POLICY_TASK_COVERAGE_BOUNDARY",
        "message": (
            "Generated collection and learned-policy /evaluate coverage are not attached to this normalized task. "
            "This is a post-TODO policy-coverage boundary, not a selection2env completion blocker."
        ),
        "owner": "Zheng Ye / Gaochen",
    }


def build_supported(
    task_id: str,
    run_name: str,
    asset_smoke_name: str,
    basetask_smoke_name: str,
    collect_name: str,
    scene_id: str,
    catalogs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    run_dir = ROOT / "runs" / run_name
    summary = read_json(run_dir / "pipeline_summary.json")
    placement_path = (
        ROOT / "runs" / "scene_task_decoupling" / "shared_apple_plate_scene.json"
        if task_id == "task_apple_plate"
        else run_dir / "final_placement.json"
    )
    static_validation_path = (
        ROOT / "runs" / "scene_task_decoupling" / "shared_scene_static_validation.json"
        if task_id == "task_apple_plate"
        else run_dir / "static_validation_final.json"
    )
    spec = read_json(placement_path)
    search = catalog_search(summary["prompt"], catalogs)
    query_path = ARTIFACT_ROOT / "adapter_catalog" / "selection2env_queries" / f"{task_id}.json"
    write_json(query_path, search)

    task_input_path = ARTIFACT_ROOT / "task_program_inputs" / f"{task_id}.json"
    primary_execution_report = (
        "runs/scene_task_decoupling/apple_on_plate/rollout_report.json"
        if task_id == "task_apple_plate"
        else None
    )
    primary = task_program_input(
        task_id=task_id,
        language_prompt=spec["language_prompt"],
        scene_id=scene_id,
        placement_path=placement_path,
        binding=task_binding_for(task_id, spec),
        variant="primary",
        execution_report=primary_execution_report,
    )
    write_json(task_input_path, primary)

    if task_id == "task_apple_plate":
        alternate_binding = {
            "template": "place_in_region",
            "source_object_id": "apple_1",
            "target_region": "left_front_reachable_area",
        }
        alternate = task_program_input(
            task_id="task_apple_plate_to_left_front",
            language_prompt="move the apple from the left rear pose into the left front reachable area",
            scene_id=scene_id,
            placement_path=placement_path,
            binding=alternate_binding,
            variant="same_scene_alternate_region",
            execution_report="runs/scene_task_decoupling/apple_to_left_front/rollout_report.json",
        )
        alternate_path = ARTIFACT_ROOT / "task_program_inputs" / "task_apple_plate_to_left_front.json"
        write_json(alternate_path, alternate)

    run_state = {
        "schema_version": "alchedata.run_state.v0",
        "run_id": f"{task_id}_static",
        "command": "/gen-env",
        "status": "pass_sim_smoke",
        "artifact_root": str((ARTIFACT_ROOT / "run_state" / task_id).relative_to(ROOT)),
        "source_summary": str((run_dir / "pipeline_summary.json").relative_to(ROOT)),
    }
    write_json(ARTIFACT_ROOT / "run_state" / task_id / "run_state.json", run_state)
    write_jsonl(
        ARTIFACT_ROOT / "run_state" / task_id / "events.jsonl",
        [
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "selection2env.static_pipeline_reused",
                "run_summary": str((run_dir / "pipeline_summary.json").relative_to(ROOT)),
            },
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "selection2env.task_program_input_written",
                "path": str(task_input_path.relative_to(ROOT)),
            },
        ],
    )

    return {
        "schema_version": "alchedata.selection2env.v0",
        "task_id": task_id,
        "task_text": summary["prompt"],
        "status": "pass_sim_smoke",
        "source_pipeline_status": summary["status"],
        "scene_id": scene_id,
        "catalog_sources": search["source_stats"],
        "asset_candidates": candidate_rows(spec, search),
        "selected_assets": selected_assets(spec),
        "placement_regions": spec.get("workspace", {}).get("spatial_regions", {}),
        "support_surface": {
            "type": spec.get("workspace", {}).get("surface", "table"),
            "frame": spec.get("workspace", {}).get("coordinate_convention", "robot_first_person_tabletop"),
            "bounds": spec.get("workspace", {}).get("bounds", {}),
        },
        "pose_constraints": pose_constraints(spec),
        "camera_observation": {
            "required_views": ["head_camera", "observer_camera"],
            "visual_checks": [
                "object visibility",
                "prompt relation match",
                "occlusion",
                "reachability",
                "stability",
                "visual plausibility beyond rigid collision",
            ],
        },
        "robot_constraints": {
            "embodiment": "RoboTwin tabletop manipulator",
            "workspace_reachability": "all objects must be inside named reachable regions before simulator smoke",
            "action_interface": "RoboTwin task-program input; policy data is produced by /collect after smoke",
        },
        "success_verifier": {
            "type": "static_then_simulator_then_visual",
            "conditions": [
                "static schema validation passes",
                "RoboTwin assets exist under robotwin_root",
                "asset-load render smoke exits pass",
                "Base_Task/CuRobo smoke exits pass",
                "/collect dry-run writes dataset manifest, camera samples, and object state traces",
                "visual review confirms placement semantics",
            ],
            "failure_modes": [
                "missing asset",
                "object penetration",
                "floating object",
                "unreachable pose",
                "occlusion",
                "unstable settling",
                "task intent not represented",
            ],
        },
        "artifacts": {
            "pipeline_summary": str((run_dir / "pipeline_summary.json").relative_to(ROOT)),
            "final_placement": str(placement_path.relative_to(ROOT)),
            "static_validation_final": str(static_validation_path.relative_to(ROOT)),
            "task_program_input": str(task_input_path.relative_to(ROOT)),
            "catalog_candidate_search": str(query_path.relative_to(ROOT)),
            **(
                {
                    "alternate_task_program_input": "artifacts/task_program_inputs/task_apple_plate_to_left_front.json",
                    "scene_task_decoupling_report": "artifacts/scene_task_decoupling/apple_plate_two_tasks.json",
                    "scene_task_repair_failure": "runs/scene_task_decoupling/original_placement_apple_on_plate_fail/rollout_report.json",
                }
                if task_id == "task_apple_plate"
                else {}
            ),
            **smoke_artifacts(asset_smoke_name, "asset"),
            **smoke_artifacts(basetask_smoke_name, "basetask"),
            **collect_artifacts(collect_name),
            **generated_action_artifacts(task_id),
            **generated_collection_artifacts(task_id),
            **generated_eval_artifacts(task_id),
        },
        "simulator_smoke": {
            "asset_load_render": smoke_status(asset_smoke_name, "asset_load_render_without_robot_planner"),
            "basetask_curobo": smoke_status(basetask_smoke_name, "robotwin_base_task_with_curobo_initialized"),
            "collect_dry_run": collect_status(collect_name),
            "generated_action_rollout": generated_action_status(task_id),
            "generated_rollout_collection": generated_collection_status(task_id),
            "generated_policy_evaluation": generated_eval_status(task_id),
        },
        "blockers": [policy_gate_blocker(task_id)],
        "handoff": {
            "collect_outputs": ["rollout logs", "scene/task manifest", "camera previews", "object state traces", "failure diagnosis"],
            "train_reads": ["rollout dataset manifest", "policy config", "data requirement spec"],
            "evaluate_reads": ["policy checkpoint", "eval task set", "verifier results", "failure trace clusters"],
        },
    }


def build_unsupported(catalogs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    run_dir = ROOT / "runs" / UNSUPPORTED_CASE["run_dir"]
    summary = read_json(run_dir / "scene_generation_summary.json")
    search = catalog_search(UNSUPPORTED_CASE["task_text"], catalogs)
    query_path = ARTIFACT_ROOT / "adapter_catalog" / "selection2env_queries" / f"{UNSUPPORTED_CASE['task_id']}.json"
    write_json(query_path, search)
    rejected_candidates = candidate_rows({"objects": []}, search)
    for candidate in rejected_candidates:
        if candidate["backend_asset_id"] == "036_cabinet":
            candidate["reason"] = "cabinet/drawer candidate exists, but no verified drawer-open task API is available"
        else:
            candidate["reason"] = "catalog match is not a verified executable drawer-and-mug task binding"
    return {
        "schema_version": "alchedata.selection2env.v0",
        "task_id": UNSUPPORTED_CASE["task_id"],
        "task_text": UNSUPPORTED_CASE["task_text"],
        "status": "unsupported_blocker",
        "scene_id": UNSUPPORTED_CASE["scene_id"],
        "catalog_sources": search["source_stats"],
        "asset_candidates": rejected_candidates,
        "selected_assets": [],
        "placement_regions": {},
        "support_surface": {"type": "unknown", "frame": "robot_first_person_tabletop", "bounds": {}},
        "pose_constraints": [],
        "camera_observation": {"required_views": ["head_camera", "observer_camera"], "visual_checks": ["unsupported task evidence"]},
        "robot_constraints": {
            "embodiment": "RoboTwin tabletop manipulator",
            "workspace_reachability": "not evaluated because the articulated task API is blocked",
            "action_interface": "blocked before executable task-program generation",
        },
        "success_verifier": {
            "type": "blocker_record",
            "conditions": ["candidate assets and API blockers are explicitly recorded"],
            "failure_modes": ["drawer articulation API unsupported", "interior-placement verifier unavailable", "no executable task-program input"],
        },
        "artifacts": {
            "scene_generation_summary": str((run_dir / "scene_generation_summary.json").relative_to(ROOT)),
            "final_placement": str((run_dir / "final_placement.json").relative_to(ROOT)),
            "static_validation": str((run_dir / "attempt_0_static_validation.json").relative_to(ROOT)),
            "catalog_candidate_search": str(query_path.relative_to(ROOT)),
            "source_scene_status": summary.get("status"),
        },
        "blockers": [
            {
                "code": "ARTICULATED_CONTAINER_TASK_API_UNSUPPORTED",
                "message": "AgenticSim maps drawer to RoboTwin 036_cabinet, but drawer opening and interior placement are not verified by the task scaffold/API.",
                "owner": "Zheng Ye / RoboTwin task adapter owner",
            }
        ],
        "handoff": {
            "collect_outputs": [],
            "train_reads": [],
            "evaluate_reads": ["blocker can be used to prioritize asset acquisition or forge fallback"],
        },
    }


def main() -> int:
    catalogs = load_catalogs()
    artifacts = [build_supported(*case, catalogs) for case in SUPPORTED_CASES]
    artifacts.append(build_unsupported(catalogs))

    robotwin_ids = {str(entry["asset_id"]) for entry in catalogs["robotwin"].get("entries", [])}
    agenticsim_mappings = normalize_agenticsim_catalog(
        catalogs["agenticsim"],
        source_path=str(AGENTICSIM_CATALOG.relative_to(ROOT)),
        robotwin_asset_ids=robotwin_ids,
    )
    source_audit = {
        "schema_version": "alchedata.selection2env_catalog_sources.v0",
        "status": (
            "pass_unified_catalog_sources"
            if len(robotwin_ids) >= 100
            and agenticsim_mappings
            and all(item["selection_eligible"] for item in agenticsim_mappings)
            and len(catalogs["articraft10k"].get("entries", [])) >= 9000
            else "fail_catalog_source_gate"
        ),
        "sources": {
            "robotwin": {
                "path": str(ROBOTWIN_DISCOVERED_CATALOG.relative_to(ROOT)),
                "entry_count": len(robotwin_ids),
                "execution_eligible_count": len(robotwin_ids),
            },
            "agenticsim": {
                "path": str(AGENTICSIM_CATALOG.relative_to(ROOT)),
                "source_commit": catalogs["agenticsim"].get("source", {}).get("source_commit"),
                "entry_count": len(agenticsim_mappings),
                "execution_eligible_count": sum(item["selection_eligible"] for item in agenticsim_mappings),
                "backend_mappings": agenticsim_mappings,
            },
            "articraft10k": {
                "path": str(ARTICRAFT_MANIFEST.relative_to(ROOT)),
                "entry_count": len(catalogs["articraft10k"].get("entries", [])),
                "execution_eligible_count": 0,
                "gate": "catalog_only_until_per_asset_import_probe",
            },
        },
        "query_artifacts": [
            f"artifacts/adapter_catalog/selection2env_queries/{task_id}.json"
            for task_id in [*[case[0] for case in SUPPORTED_CASES], UNSUPPORTED_CASE["task_id"]]
        ],
        "claim_boundary": (
            "RoboTwin entries and AgenticSim aliases backed by those entries are selection-eligible. "
            "Articraft metadata is searchable but remains ineligible without per-asset import evidence."
        ),
    }
    write_json(ARTIFACT_ROOT / "adapter_catalog" / "selection2env_catalog_sources.json", source_audit)
    write_json(ARTIFACT_ROOT / "selection2env_manifest.json", {"schema_version": "alchedata.selection2env_manifest.v0", "artifacts": artifacts})
    for artifact in artifacts:
        write_json(ARTIFACT_ROOT / "selection2env" / f"{artifact['task_id']}.json", artifact)
    print(f"wrote {len(artifacts)} selection2env artifacts under {ARTIFACT_ROOT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
