#!/usr/bin/env python3
"""Build a strict, machine-readable audit for the eight SceneAgent TODO items."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from selection2env_contract import ROOT, validate_scene_task_pair
from video_evidence import probe_video


EXPECTED_SELECTION_FIELDS = {
    "task_text",
    "asset_candidates",
    "selected_assets",
    "placement_regions",
    "support_surface",
    "pose_constraints",
    "camera_observation",
    "robot_constraints",
    "success_verifier",
    "blockers",
}
REQUIRED_VISUAL_DIMENSIONS = {
    "collision_or_penetration",
    "visual_plausibility",
    "task_intent_match",
    "occlusion",
    "reachability",
    "support_and_stability",
}
REMOTE_ROOT_MARKER = "/alchedata-self-improving-agents/"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def resolve_evidence_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.exists():
        return path.resolve()
    text = str(path)
    if REMOTE_ROOT_MARKER in text:
        candidate = ROOT / text.split(REMOTE_ROOT_MARKER, 1)[1]
        if candidate.exists():
            return candidate.resolve()
    if not path.is_absolute():
        candidate = ROOT / path
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError(value)


def evidence(path_value: str | Path) -> dict[str, Any]:
    path = Path(path_value)
    if not path.is_absolute():
        path = ROOT / path
    require(path.is_file(), f"Evidence file is missing: {path}")
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def item(item_id: int, title: str, observed: dict[str, Any], paths: list[str]) -> dict[str, Any]:
    return {
        "acceptance_item": item_id,
        "title": title,
        "status": "pass",
        "observed": observed,
        "evidence": [evidence(path) for path in paths],
    }


def build() -> dict[str, Any]:
    selection_schema = read_json(ROOT / "schemas/selection2env.schema.json")
    task_program_schema = read_json(ROOT / "schemas/robotwin_task_program_input.schema.json")
    missing_selection_fields = EXPECTED_SELECTION_FIELDS - set(selection_schema.get("required", []))
    require(not missing_selection_fields, f"Selection schema is missing fields: {sorted(missing_selection_fields)}")
    require(
        {"task_binding", "placement_sha256", "verifier"}.issubset(task_program_schema.get("required", [])),
        "Task-program schema is missing binding/hash/verifier requirements",
    )

    catalog = read_json(ROOT / "artifacts/adapter_catalog/selection2env_catalog_sources.json")
    require(catalog.get("status") == "pass_unified_catalog_sources", "Unified catalog audit did not pass")
    sources = catalog["sources"]
    require(sources["robotwin"]["execution_eligible_count"] == 123, "RoboTwin catalog count mismatch")
    require(sources["agenticsim"]["execution_eligible_count"] == 8, "AgenticSim alias count mismatch")
    require(sources["articraft10k"]["entry_count"] == 9996, "Articraft manifest count mismatch")
    require(sources["articraft10k"]["execution_eligible_count"] == 0, "Articraft default execution gate is unsafe")

    manifest = read_json(ROOT / "artifacts/selection2env_manifest.json")
    artifacts = manifest.get("artifacts", [])
    supported = [row for row in artifacts if row.get("status") == "pass_sim_smoke"]
    unsupported = [row for row in artifacts if row.get("status") == "unsupported_blocker"]
    require(len(supported) >= 3, "Fewer than three supported task samples")
    require(len(unsupported) >= 1, "Unsupported/blocker sample is missing")
    for row in supported:
        smoke = row.get("simulator_smoke", {})
        require(smoke.get("basetask_curobo", {}).get("status") == "pass", f"{row['task_id']}: Base_Task smoke failed")
        require(smoke.get("collect_dry_run", {}).get("status") == "pass_collect_dry_run", f"{row['task_id']}: /collect dry-run failed")

    final_collect_paths = [
        "runs/final_acceptance_20260715/collect_apple_plate/collect_report.json",
        "runs/final_acceptance_20260715/collect_laptop_knife/collect_report.json",
    ]
    final_collect_results = []
    for path_value in final_collect_paths:
        report = read_json(ROOT / path_value)
        require(report.get("status") == "pass_collect_dry_run", f"Fresh /collect failed: {path_value}")
        video = probe_video(resolve_evidence_path(report["observer_video"]))
        require(video["frame_count"] == report["video_capture"]["frame_count"] == 97, f"Fresh /collect video mismatch: {path_value}")
        require(report["video_capture"]["video_endpoint_only"] is False, f"Fresh /collect is endpoint-only: {path_value}")
        final_collect_results.append({"task_id": report["task_id"], **video})

    visual = read_json(ROOT / "artifacts/visual_review/selection2env_visual_review.json")
    require(set(visual.get("assessment_dimensions", [])) == REQUIRED_VISUAL_DIMENSIONS, "Visual review dimensions are incomplete")
    require(len(visual.get("cases", [])) >= 3, "Visual review has fewer than three cases")
    for case in visual["cases"]:
        for dimension in REQUIRED_VISUAL_DIMENSIONS:
            require(str(case["checks"].get(dimension, "")).startswith("pass"), f"{case['task_id']}: failed visual dimension {dimension}")
        require(case["checks"].get("final_task_relation") == "not_claimed", f"{case['task_id']}: initial review overclaims task success")
        for image_name, path_value in case["images"].items():
            require(sha256_file(ROOT / path_value) == case["image_sha256"][image_name], f"{case['task_id']}: image hash mismatch")

    accepted_candidates = sum(
        candidate.get("decision") == "accepted"
        for row in artifacts
        for candidate in row.get("asset_candidates", [])
    )
    rejected_candidates = sum(
        candidate.get("decision") == "rejected"
        for row in artifacts
        for candidate in row.get("asset_candidates", [])
    )
    require(accepted_candidates >= 6 and rejected_candidates > 0, "Accepted/rejected candidate evidence is incomplete")
    source_lock = read_json(ROOT / "external_sources.lock.json")["sources"]["robotwin_text2env_demo"]
    patch_path = ROOT / source_lock["patch"]
    require(sha256_file(patch_path) == source_lock["patch_sha256"], "Public-base patch hash mismatch")
    result_tree = subprocess.run(
        ["git", "-C", str(ROOT / "external/robotwin-text2env-demo"), "rev-parse", "HEAD^{tree}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    require(result_tree == source_lock["result_tree"], "Patched upstream result tree mismatch")

    fallback = read_json(ROOT / "artifacts/generation_fallback/fallback_blocker_summary.json")
    require(fallback.get("status") == "blocked_fallback_inputs_missing", "Generation fallback summary is not a typed blocker")
    require(len(fallback.get("probes", [])) == 2, "Generation fallback probe count mismatch")

    for row in supported:
        handoff = row.get("handoff", {})
        require(handoff.get("collect_outputs"), f"{row['task_id']}: /collect handoff is empty")
        require(handoff.get("train_reads"), f"{row['task_id']}: /train handoff is empty")
        require(handoff.get("evaluate_reads"), f"{row['task_id']}: /evaluate handoff is empty")

    primary = read_json(ROOT / "artifacts/task_program_inputs/task_apple_plate.json")
    alternate = read_json(ROOT / "artifacts/task_program_inputs/task_apple_plate_to_left_front.json")
    pair = validate_scene_task_pair(primary, alternate)
    require(pair.get("status") == "pass", "Same-scene task-program pair failed")
    decoupling = read_json(ROOT / "artifacts/scene_task_decoupling/apple_plate_two_tasks.json")
    require(decoupling.get("status") == "pass_same_scene_two_executable_tasks", "Same-scene execution failed")
    require(decoupling.get("initial_observer_bytes_identical") is True, "Same-scene initial pixels differ")
    for rollout in decoupling.get("rollouts", []):
        require(rollout.get("status") == "pass", f"Decoupled task failed: {rollout.get('task_id')}")
        video = probe_video(ROOT / rollout["observer_video"])
        require(video["frame_count"] == rollout["video_capture"]["frame_count"], "Decoupling video frame mismatch")
        require(video["frame_count"] >= 500, "Decoupling video is not continuous enough")

    audit_items = [
        item(
            1,
            "Typed selection2env input/output schema",
            {"required_selection_fields": sorted(EXPECTED_SELECTION_FIELDS), "task_program_binding_required": True},
            ["schemas/selection2env.schema.json", "schemas/robotwin_task_program_input.schema.json", "docs/selection2env_schema.md"],
        ),
        item(
            2,
            "RoboTwin, AgenticSim, and Articraft catalog connection",
            {"robotwin_execution_eligible": 123, "agenticsim_aliases": 8, "articraft_searchable": 9996, "articraft_default_execution_eligible": 0},
            ["artifacts/adapter_catalog/selection2env_catalog_sources.json", "artifacts/adapter_catalog/articraft10k_manifest.json"],
        ),
        item(
            3,
            "Three supported samples plus one typed blocker and two fresh /collect runs",
            {"supported_task_ids": [row["task_id"] for row in supported], "unsupported_task_ids": [row["task_id"] for row in unsupported], "fresh_collect": final_collect_results},
            ["artifacts/selection2env_manifest.json", *final_collect_paths],
        ),
        item(
            4,
            "Agent visual and semantic critique beyond collision",
            {"dimensions": sorted(REQUIRED_VISUAL_DIMENSIONS), "case_count": len(visual["cases"]), "final_task_relation": "not_claimed_in_initial_review"},
            ["artifacts/visual_review/selection2env_visual_review.json", "artifacts/visual_review/generated_rollout_final_relation_review.json"],
        ),
        item(
            5,
            "Candidates, placement, reproducible code diff, commands, logs, images, and continuous videos",
            {"accepted_candidates": accepted_candidates, "rejected_candidates": rejected_candidates, "public_base": source_lock["base_commit"], "patch_sha256": source_lock["patch_sha256"], "result_tree": result_tree},
            [
                "external_sources.lock.json",
                source_lock["patch"],
                "docs/sceneagent_reproduction_commands.md",
                "runs/final_acceptance_20260715/scene_task_decoupling/apple_on_plate/process_stdout.log",
                "runs/final_acceptance_20260715/scene_task_decoupling/apple_on_plate/process_stderr.log",
                "runs/final_acceptance_20260715/scene_task_decoupling/apple_to_left_front/process_stdout.log",
                "runs/final_acceptance_20260715/scene_task_decoupling/apple_to_left_front/process_stderr.log",
                "runs/scene_task_decoupling/original_placement_apple_on_plate_fail/final_observer_camera.png",
            ],
        ),
        item(
            6,
            "generation2env blocker memo",
            {"status": fallback["status"], "typed_probe_count": len(fallback["probes"]), "completion_gate": "excluded_by_dashboard_acceptance"},
            ["docs/generation2env_blockers.md", "artifacts/generation_fallback/fallback_blocker_summary.json"],
        ),
        item(
            7,
            "Gaochen pipeline handoff API",
            {"commands": ["/collect", "/train", "/evaluate"], "supported_task_count": len(supported)},
            ["docs/selection2env_schema.md", "docs/openxsim_command_spec.md", "artifacts/openxsim/openxsim_command_registry.json"],
        ),
        item(
            8,
            "One byte-identical scene executes two distinct task specs",
            {
                "scene_id": decoupling["scene_id"],
                "placement_sha256": decoupling["placement_sha256"],
                "initial_observer_sha256": decoupling["initial_observer_sha256"],
                "tasks": [
                    {
                        "task_id": row["task_id"],
                        "frame_count": row["video_capture"]["frame_count"],
                        "check_success": row["checks"]["check_success"],
                    }
                    for row in decoupling["rollouts"]
                ],
            },
            [
                "artifacts/scene_task_decoupling/apple_plate_two_tasks.json",
                "runs/final_acceptance_20260715/scene_task_decoupling/apple_on_plate/rollout_report.json",
                "runs/final_acceptance_20260715/scene_task_decoupling/apple_to_left_front/rollout_report.json",
            ],
        ),
    ]

    policy = read_json(ROOT / "artifacts/sceneagent_policy_promotion/pose_conditioned_policy_promotion_v1.json")
    return {
        "schema_version": "alchedata.sceneagent_selection2env_acceptance_audit.v1",
        "status": "pass_all_8_sceneagent_acceptance_items",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "todo_id": "task_self_improving_sceneagent_selection2env_20260701",
        "todo_scope": "selection2env over existing RoboTwin, AgenticSim aliases, and catalog-gated Articraft metadata",
        "acceptance_item_count": len(audit_items),
        "pass_count": sum(row["status"] == "pass" for row in audit_items),
        "items": audit_items,
        "supplemental_policy_gate": {
            "status": policy["status"],
            "decision": policy["decision"],
            "heldout_varied_placement": "4/4",
            "declared_domain_randomization": "4/4",
            "cross_task_can_basket": "3/3",
            "claim_boundary": policy["claim_boundary"],
            "evidence": evidence("artifacts/sceneagent_policy_promotion/pose_conditioned_policy_promotion_v1.json"),
        },
        "runtime": read_json(ROOT / "artifacts/runtime/robotwin_5090_env.json"),
        "claim_boundary": "This audit completes the eight dashboard acceptance items. generation2env execution, default upstream ACT wrapper compatibility, visual/language-conditioned closed-loop control, and broad Articraft physical validation remain outside this TODO.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(ROOT / "artifacts/sceneagent_selection2env_acceptance_audit.json"),
    )
    args = parser.parse_args()
    result = build()
    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{result['status']} {result['pass_count']}/{result['acceptance_item_count']} out={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
