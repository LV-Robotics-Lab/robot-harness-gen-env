#!/usr/bin/env python3
"""Verify the Alchedata Self-Improving Agents workspace artifacts."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import jsonschema

from embodied_harness import validate_embodied_harness_package
from openxsim_command_loop import validate_openxsim_package
from selection2env_contract import validate_scene_task_pair, validate_task_program_references
from text2env_literature_review import validate_review_package
try:
    from video_evidence import probe_video
except ModuleNotFoundError:
    from scripts.video_evidence import probe_video


ROOT = Path(__file__).resolve().parents[1]
KNOWN_WORKSPACE_ROOTS = (
    Path("/home/jingxiang/workspace/alchedata-self-improving-agents"),
    Path("/Users/boris/workspace/alchedata-self-improving-agents"),
)

REQUIRED_DOCS = [
    "docs/selection2env_schema.md",
    "docs/sceneagent_reproduction_commands.md",
    "docs/openxsim_command_spec.md",
    "docs/text2env_literature_review.md",
    "docs/embodied_harness_thesis.md",
    "docs/generation2env_blockers.md",
    "docs/robotwin_smoke_evidence.md",
    "docs/dashboard_todo_traceability.md",
    "docs/todo_completion_audit.md",
    "docs/native_act_closed_loop_diagnosis.md",
    "docs/placement_robustness_diagnosis.md",
]

RUN_EXPECTATIONS = {
    "runs/probe_static_apple_plate/pipeline_summary.json": "pass_static_only",
    "runs/probe_static_laptop_knife/pipeline_summary.json": "pass_static_only",
    "runs/probe_static_vegetable_basket/pipeline_summary.json": "pass_static_only",
    "runs/probe_static_drawer_mug_unified/scene_generation_summary.json": "pass_static_scene_module",
}

SMOKE_EXPECTATIONS = {
    "runs/smoke_asset_apple_plate/smoke_report.json": "pass_asset_load_render",
    "runs/smoke_asset_laptop_knife/smoke_report.json": "pass_asset_load_render",
    "runs/smoke_asset_vegetable_basket/smoke_report.json": "pass_asset_load_render",
}

BASETASK_SMOKE_EXPECTATIONS = {
    "runs/smoke_basetask_apple_plate/smoke_report.json": "pass",
    "runs/smoke_basetask_laptop_knife/smoke_report.json": "pass",
    "runs/smoke_basetask_vegetable_basket/smoke_report.json": "pass",
}

COLLECT_DRYRUN_EXPECTATIONS = {
    "runs/collect_dryrun_apple_plate/collect_report.json": "pass_collect_dry_run",
    "runs/collect_dryrun_laptop_knife/collect_report.json": "pass_collect_dry_run",
    "runs/collect_dryrun_vegetable_basket/collect_report.json": "pass_collect_dry_run",
}

FINAL_ACCEPTANCE_COLLECT_EXPECTATIONS = {
    "runs/final_acceptance_20260715/collect_apple_plate/collect_report.json": "task_apple_plate",
    "runs/final_acceptance_20260715/collect_laptop_knife/collect_report.json": "task_laptop_knife",
}

OFFICIAL_ROLLOUT_EXPECTATIONS = {
    "runs/official_rollout_open_laptop/rollout_report.json": "pass_action_rollout",
    "runs/official_rollout_place_container_plate/rollout_report.json": "pass_action_rollout",
    "runs/official_rollout_place_mouse_pad/rollout_report.json": "pass_action_rollout",
}

GENERATED_ROLLOUT_EXPECTATIONS = {
    "runs/generated_rollout_apple_plate_action_repair_pass/rollout_report.json": {
        "status": "pass_generated_action_rollout",
        "task_id": "task_apple_plate",
        "check_success": True,
    },
    "runs/generated_rollout_can_basket_action_repair/rollout_report.json": {
        "status": "pass_generated_action_rollout",
        "task_id": "task_can_basket",
        "check_success": True,
    },
}

GENERATED_COLLECTION_EXPECTATIONS = {
    "runs/generated_collect_apple_plate_action_repair/collection_report.json": {
        "status": "pass_generated_rollout_collection",
        "task_id": "task_apple_plate",
        "episode_count": 3,
    },
    "runs/generated_collect_can_basket_action_repair/collection_report.json": {
        "status": "pass_generated_rollout_collection",
        "task_id": "task_can_basket",
        "episode_count": 3,
    },
}

REQUIRED_DENSE_DIAGNOSIS_CATEGORIES = {
    "wrong_grasp_location",
    "object_knocked_over",
    "arm_jitter",
    "uncontrolled_gripper_open_close",
    "after_contact_failure",
    "visual_material_mismatch",
}

ARTICRAFT_ARCHIVE_PROBES = {
    "runs/articraft_archive_probe_weight_bench/probe_report.json": {
        "asset_id": "rec_adjustable_weight_bench_with_hinged_backrest_008247976e6d499d8bcbd1304f26c972",
        "status": "pass_articraft_archive_sapien_smoke",
        "collision_gate": "pass",
    },
    "runs/articraft_archive_probe_microscope/probe_report.json": {
        "asset_id": "rec_benchtop_monocular_laboratory_microscope_080f397ab8ae4b479a27bb2b62bb1980",
        "status": "pass_articraft_archive_sapien_smoke",
        "collision_gate": "pass",
    },
    "runs/articraft_archive_probe_monitor/probe_report.json": {
        "asset_id": "rec_desktop_monitor_with_tilt_swivel_stand_04c3533227724245bc59022d84d6b041",
        "status": "pass_articraft_archive_sapien_smoke",
        "collision_gate": "pass",
    },
    "runs/articraft_archive_probe_washing_machine/probe_report.json": {
        "asset_id": "rec_frontload_washing_machine_0001",
        "status": "fail_articraft_archive_sapien_smoke",
        "collision_gate": "fail_missing_collision_geometry",
    },
    "runs/articraft_archive_probe_zippo_lighter/probe_report.json": {
        "asset_id": "rec_zippo_lighter_0215496335c84a629660993cbecc92e0",
        "status": "fail_articraft_archive_sapien_smoke",
        "collision_gate": "fail_missing_collision_geometry",
    },
}

POLICY_TRAIN_EVAL_PROBE = "runs/policy_train_eval_entrypoint_probe/probe_report.json"
ACT_HDF5_CONVERSION_REPORT = "runs/act_hdf5_generated_smoke/conversion_report.json"
ACT_HDF5_LOAD_DATA_REPORT = "runs/act_hdf5_generated_smoke/load_data_report.json"
ACT_TRAIN_SMOKE_REPORT = "runs/act_train_smoke_generated/train_smoke_report.json"
ACT_EVAL_SMOKE_REPORT = "runs/act_eval_smoke_generated/evaluate_report.json"
NATIVE_COLLECTION_REPORT = "runs/generated_collect_apple_plate_native_sync/collection_report.json"
NATIVE_ACT_CONVERSION_REPORT = "runs/act_hdf5_native_sync/conversion_report.json"
NATIVE_ACT_LOAD_REPORT = "runs/act_hdf5_native_sync/load_data_report.json"
NATIVE_ACT_REPLAY_REPORT = "runs/act_action_replay_native_sync/replay_report.json"
NATIVE_ACT_TRAIN_REPORT = "runs/act_train_native_sync_rgb_chunk161_1200e/train_smoke_report.json"
NATIVE_ACT_EVAL_REPORT = "runs/act_eval_native_sync_rgb_chunk161_1200e_best/evaluate_report.json"
NATIVE_ACT_DIAGNOSIS = "artifacts/diagnosis/native_act_closed_loop_diagnosis.json"
PLACEMENT_ROBUSTNESS_DIAGNOSIS = "artifacts/diagnosis/placement_robustness_diagnosis.json"
OPENXSIM_BENCHMARK_SCHEMA = "schemas/openxsim_benchmark_bundle.schema.json"
OPENXSIM_BENCHMARK_MANIFEST = "artifacts/openxsim_benchmarks/manifest.json"
OPENXSIM_BENCHMARK_TASKS = {
    "open_laptop": "runs/official_rollout_open_laptop/rollout_report.json",
    "place_mouse_pad": "runs/official_rollout_place_mouse_pad/rollout_report.json",
    "place_container_plate": "runs/official_rollout_place_container_plate/rollout_report.json",
}
GEN_ENV_SCHEMA = "schemas/gen_env.schema.json"
GEN_ENV_CONTRACT_MANIFEST = "artifacts/gen_env_contract/gen_env_contract_manifest.json"
GEN_ENV_CONTRACT_EXAMPLES = {
    "artifacts/gen_env_contract/selection2env_route_sample.json": {
        "route": "selection2env",
        "status": "pass_selection2env_contract",
        "output_key": "selection2env",
    },
    "artifacts/gen_env_contract/forge_fallback_route_blocker.json": {
        "route": "forge_fallback",
        "status": "blocked_forge_fallback",
        "output_key": "forge_fallback",
        "required_blocker": "FORGE_CAPTURE_SOURCE_MISSING",
    },
    "artifacts/gen_env_contract/material_sidecar_route_blocker.json": {
        "route": "material_sidecar",
        "status": "blocked_material_sidecar",
        "output_key": "material_sidecar",
        "required_blocker": "MATERIAL_MULTIVIEW_INPUT_MISSING",
    },
}
GEN_ENV_FALLBACK_SCHEMA = "schemas/gen_env_fallback.schema.json"
GEN_ENV_FALLBACK_PROBES = {
    "artifacts/generation_fallback/blocked_drawer_mug_video2sim_forge.json": {
        "fallback_type": "video2sim_forge",
        "status": "blocked_missing_inputs",
        "required_blocker": "FORGE_CAPTURE_SOURCE_MISSING",
    },
    "artifacts/generation_fallback/blocked_neumatex_material_sidecar.json": {
        "fallback_type": "neumatex_material_sidecar",
        "status": "blocked_missing_inputs",
        "required_blocker": "MATERIAL_MULTIVIEW_INPUT_MISSING",
    },
}
GEN_ENV_FALLBACK_SUMMARY = "artifacts/generation_fallback/fallback_blocker_summary.json"
TASK_PROGRAM_SCHEMA = "schemas/robotwin_task_program_input.schema.json"
CATALOG_SOURCE_AUDIT = "artifacts/adapter_catalog/selection2env_catalog_sources.json"
SCENE_TASK_DECOUPLING_REPORT = "artifacts/scene_task_decoupling/apple_plate_two_tasks.json"
EXTERNAL_SOURCES_LOCK = "external_sources.lock.json"
CORE_CAUSAL_GATES = "artifacts/core_causal_results/gates.json"
SCENEAGENT_ACCEPTANCE_AUDIT = "artifacts/sceneagent_selection2env_acceptance_audit.json"
SCENEAGENT_POLICY_PROMOTION = "artifacts/sceneagent_policy_promotion/pose_conditioned_policy_promotion_v1.json"
TEXT2ENV_EMPIRICAL_AUDIT = "artifacts/text2env_empirics/text2env_empirical_audit_v1.json"
MEMORY_ABLATION = "artifacts/text2env_empirics/memory_ablation_rgb_adapter_v1.json"
FAILURE_SCORE_CORRELATION = "artifacts/text2env_empirics/failure_score_correlation_v1.json"
MATERIAL_ROUNDTRIP = "runs/isaac_material_sidecar_roundtrip_v1/roundtrip_report.json"
ISAAC_COMMAND_BUNDLE = "runs/isaac_openxsim_place_container_plate_v1/bundle_manifest.json"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def artifact_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        for workspace_root in KNOWN_WORKSPACE_ROOTS:
            try:
                return ROOT / path.relative_to(workspace_root)
            except ValueError:
                continue
        return path
    return ROOT / path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def git_output(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def main() -> int:
    for doc in REQUIRED_DOCS:
        path = ROOT / doc
        require(path.exists(), f"missing doc: {doc}")
        require(path.stat().st_size > 500, f"doc too small: {doc}")

    literature_review = validate_review_package()
    openxsim_package = validate_openxsim_package()
    harness_package = validate_embodied_harness_package()

    core_gates = read_json(ROOT / CORE_CAUSAL_GATES)
    require(
        core_gates.get("status") == "pass_all_core_causal_result_gates",
        "core causal result gates are not complete",
    )
    for todo in core_gates.get("todos", []):
        for gate in todo.get("gates", []):
            require(
                str(gate.get("status", "")).startswith("pass"),
                f"core gate is not passing: {todo.get('todo_id')}/{gate.get('gate_id')}",
            )

    sceneagent_acceptance = read_json(ROOT / SCENEAGENT_ACCEPTANCE_AUDIT)
    require(
        sceneagent_acceptance.get("status") == "pass_all_8_sceneagent_acceptance_items",
        "SceneAgent dashboard acceptance audit did not pass",
    )
    require(
        sceneagent_acceptance.get("acceptance_item_count")
        == sceneagent_acceptance.get("pass_count")
        == 8,
        "SceneAgent dashboard acceptance audit is not 8/8",
    )

    sceneagent_promotion = read_json(ROOT / SCENEAGENT_POLICY_PROMOTION)
    require(
        sceneagent_promotion.get("status") == "pass_bounded_pose_conditioned_policy_promotion",
        "bounded SceneAgent policy promotion did not pass",
    )
    sceneagent_gates = sceneagent_promotion.get("gates", {})
    require(
        sceneagent_gates.get("heldout_varied_placement", {}).get("success_count") == 4,
        "SceneAgent held-out placement gate mismatch",
    )
    require(
        sceneagent_gates.get("declared_domain_randomization", {}).get("success_count") == 4,
        "SceneAgent domain-randomization gate mismatch",
    )
    require(
        sceneagent_gates.get("cross_task_learned_policy", {}).get("success_count") == 3,
        "SceneAgent cross-task gate mismatch",
    )

    empirical_audit = read_json(ROOT / TEXT2ENV_EMPIRICAL_AUDIT)
    require(
        empirical_audit.get("status") == "pass_all_text2env_empirical_gates",
        "Text2Env empirical audit did not pass",
    )
    require(
        len(empirical_audit.get("gates", {})) == 5,
        "Text2Env empirical gate count mismatch",
    )
    memory_ablation = read_json(ROOT / MEMORY_ABLATION)
    memory_outcomes = memory_ablation.get("outcomes", {})
    require(memory_ablation.get("status") == "pass_matched_memory_ablation", "memory ablation did not pass")
    require(
        memory_outcomes.get("no_memory_success_count") == 0
        and memory_outcomes.get("memory_success_count") == 3
        and memory_outcomes.get("both_arms_execution_complete") is True,
        "memory ablation outcome mismatch",
    )
    material_roundtrip = read_json(ROOT / MATERIAL_ROUNDTRIP)
    require(
        material_roundtrip.get("status") == "pass_material_sidecar_roundtrip",
        "material sidecar roundtrip did not pass",
    )
    failure_correlation = read_json(ROOT / FAILURE_SCORE_CORRELATION)
    require(
        failure_correlation.get("status") == "pass_predeclared_failure_score_correlation_reported",
        "failure-score correlation result was not retained",
    )
    require(failure_correlation.get("sample_count") == 12, "failure-score sample count mismatch")

    isaac_bundle = read_json(ROOT / ISAAC_COMMAND_BUNDLE)
    require(isaac_bundle.get("status") == "pass_isaac_command_bundle", "Isaac command bundle did not pass")
    require(isaac_bundle.get("file_count") == len(isaac_bundle.get("files", [])) == 34, "Isaac bundle file count mismatch")
    isaac_bundle_root = (ROOT / ISAAC_COMMAND_BUNDLE).parent
    for row in isaac_bundle["files"]:
        path = isaac_bundle_root / row["path"]
        require(path.is_file(), f"Isaac bundle file missing: {path}")
        require(path.stat().st_size == row["bytes"], f"Isaac bundle size mismatch: {path}")
        require(hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"], f"Isaac bundle hash mismatch: {path}")

    external_lock = read_json(ROOT / EXTERNAL_SOURCES_LOCK)["sources"]["robotwin_text2env_demo"]
    patch_path = ROOT / external_lock["patch"]
    require(patch_path.exists(), "robotwin-text2env dependency patch missing")
    patch_sha256 = hashlib.sha256(patch_path.read_bytes()).hexdigest()
    require(patch_sha256 == external_lock["patch_sha256"], "robotwin-text2env dependency patch hash mismatch")
    external_repo = ROOT / "external" / "robotwin-text2env-demo"
    require(external_repo.exists(), "robotwin-text2env checkout missing")
    require(git_output(external_repo, "rev-parse", "HEAD^{tree}") == external_lock["result_tree"], "robotwin-text2env checkout tree mismatch")
    require(
        git_output(external_repo, "remote", "get-url", "origin") == external_lock["url"],
        "robotwin-text2env origin URL mismatch",
    )
    require(
        git_output(external_repo, "merge-base", "--is-ancestor", external_lock["base_commit"], "HEAD") == "",
        "robotwin-text2env base commit is not an ancestor",
    )

    catalog_source_audit = read_json(ROOT / CATALOG_SOURCE_AUDIT)
    require(
        catalog_source_audit.get("status") == "pass_unified_catalog_sources",
        "unified selection2env catalog source gate did not pass",
    )
    catalog_sources = catalog_source_audit.get("sources", {})
    require(catalog_sources.get("robotwin", {}).get("entry_count", 0) >= 100, "RoboTwin discovered catalog too small")
    require(catalog_sources.get("robotwin", {}).get("execution_eligible_count", 0) >= 100, "RoboTwin execution catalog too small")
    require(catalog_sources.get("agenticsim", {}).get("source_commit") == "f34d56a", "unexpected AgenticSim source snapshot")
    require(catalog_sources.get("agenticsim", {}).get("entry_count") == 8, "unexpected AgenticSim alias count")
    require(catalog_sources.get("agenticsim", {}).get("execution_eligible_count") == 8, "AgenticSim aliases do not all resolve to RoboTwin")
    for mapping in catalog_sources.get("agenticsim", {}).get("backend_mappings", []):
        require(mapping.get("selection_eligible") is True, f"AgenticSim backend mapping is blocked: {mapping.get('asset_id')}")
        require(mapping.get("execution_status") == "robotwin_alias_loadable", f"AgenticSim mapping not loadable: {mapping.get('asset_id')}")
    require(catalog_sources.get("articraft10k", {}).get("entry_count", 0) >= 9000, "Articraft catalog source too small")
    require(catalog_sources.get("articraft10k", {}).get("execution_eligible_count") == 0, "Articraft metadata must not bypass import gates")
    for query_path in catalog_source_audit.get("query_artifacts", []):
        query = read_json(artifact_path(query_path))
        require({"robotwin", "agenticsim", "articraft10k"}.issubset(query.get("source_stats", {})), f"catalog query missing sources: {query_path}")

    articraft_probe = read_json(ROOT / "artifacts" / "adapter_catalog" / "articraft10k_probe.json")
    require(articraft_probe.get("status") == "pass_hf_metadata_manifest", "unexpected Articraft probe status")
    require(articraft_probe.get("asset_count", 0) >= 9000, "Articraft probe asset count too low")
    require(articraft_probe.get("license") == "cc-by-4.0", "unexpected Articraft license")
    articraft_manifest = read_json(ROOT / "artifacts" / "adapter_catalog" / "articraft10k_manifest.json")
    require(articraft_manifest.get("status") == "pass_searchable_manifest_from_hf_metadata", "unexpected Articraft manifest status")
    require(articraft_manifest.get("asset_count", 0) >= 9000, "Articraft manifest asset count too low")
    require(len(articraft_manifest.get("entries", [])) == articraft_manifest.get("asset_count"), "Articraft manifest entries/count mismatch")
    require(articraft_manifest.get("asset_format_in_archive") == "URDF", "Articraft asset format must remain URDF")
    for entry in articraft_manifest.get("entries", [])[:25]:
        require(entry.get("source_path", "").endswith(".tar.gz"), "Articraft entry missing archive path")
        require(entry.get("download_url", "").startswith("https://huggingface.co/datasets/"), "Articraft entry missing Hub download URL")
        require(entry.get("semantic_tokens"), "Articraft entry missing semantic tokens")
        require(entry.get("import_status") == "catalog_only_not_imported", "Articraft entry must not claim import")
    articraft_search = read_json(ROOT / "artifacts" / "adapter_catalog" / "articraft10k_search_examples.json")
    require(len(articraft_search.get("queries", [])) >= 5, "Articraft search examples missing")
    for query in articraft_search.get("queries", []):
        require(query.get("matches"), f"Articraft search query has no matches: {query.get('query')}")
    articraft_archive_reports = []
    for report, expected in ARTICRAFT_ARCHIVE_PROBES.items():
        archive_probe = read_json(ROOT / report)
        articraft_archive_reports.append(archive_probe)
        require(archive_probe.get("status") == expected["status"], f"{report}: unexpected Articraft archive probe status")
        selected_asset = archive_probe.get("selected_asset", {})
        require(selected_asset.get("asset_id") == expected["asset_id"], f"{report}: unexpected Articraft archive probe asset")
        require(archive_probe.get("archive", {}).get("member_count", 0) >= 3, f"{report}: unexpected Articraft archive member count")
        require(artifact_path(archive_probe.get("archive", {}).get("path", "")).exists(), f"{report}: missing Articraft downloaded archive")
        urdf = archive_probe.get("urdf", {})
        require(artifact_path(urdf.get("path", "")).exists(), f"{report}: missing Articraft extracted URDF")
        require(urdf.get("link_count", 0) > 0, f"{report}: Articraft URDF missing links")
        require(urdf.get("joint_count", 0) > 0, f"{report}: Articraft URDF missing joints")
        require(not urdf.get("missing_mesh_references"), f"{report}: Articraft URDF has unresolved mesh references")
        checks = archive_probe.get("checks", {})
        for check_name in ("archive_downloaded", "archive_extracted", "urdf_found", "links_present", "joints_present", "mesh_references_resolved"):
            require(checks.get(check_name) is True, f"{report}: Articraft check failed: {check_name}")
        if expected["collision_gate"] == "pass":
            require(urdf.get("collision_geometry_count", 0) > 0, f"{report}: Articraft URDF missing collision geometry")
            require(checks.get("collision_geometry_present") is True, f"{report}: Articraft collision check did not pass")
        else:
            require(urdf.get("collision_geometry_count") == 0, f"{report}: expected collision-geometry blocker")
            require(checks.get("collision_geometry_present") is False, f"{report}: expected failed collision-geometry check")
        sapien_smoke = archive_probe.get("sapien_smoke", {})
        require(sapien_smoke.get("status") == "pass", f"{report}: Articraft SAPIEN smoke did not load/step")
        require(sapien_smoke.get("articulation_type") == "PhysxArticulation", f"{report}: Articraft SAPIEN smoke did not load an articulation")
        require(sapien_smoke.get("link_count") == urdf.get("link_count"), f"{report}: unexpected Articraft SAPIEN link count")
        require(sapien_smoke.get("active_joint_count", 0) > 0, f"{report}: Articraft SAPIEN active joint count too low")
        require(sapien_smoke.get("physics_steps", 0) >= 20, f"{report}: Articraft SAPIEN physics steps too low")
    articraft_archive_passes = sum(1 for report in articraft_archive_reports if report.get("status") == "pass_articraft_archive_sapien_smoke")
    articraft_collision_blockers = sum(
        1
        for report in articraft_archive_reports
        if report.get("status") == "fail_articraft_archive_sapien_smoke"
        and report.get("checks", {}).get("collision_geometry_present") is False
    )
    require(articraft_archive_passes >= 3, "expected at least three passing Articraft archive probes")
    require(articraft_collision_blockers >= 2, "expected at least two Articraft collision-geometry blocker probes")
    runtime = read_json(ROOT / "artifacts" / "runtime" / "robotwin_5090_env.json")
    require(runtime.get("torch_cuda_kernel_test") == "pass", "robotwin-5090 torch CUDA test did not pass")
    require(runtime.get("curobo_install") == "pass", "robotwin-5090 CuRobo install did not pass")
    visual_review = read_json(ROOT / "artifacts" / "visual_review" / "selection2env_visual_review.json")
    require(
        visual_review.get("overall_status") == "pass_initial_scene_support_with_task_success_pending",
        "visual review must distinguish initial-scene support from task success",
    )
    required_visual_dimensions = {
        "collision_or_penetration",
        "visual_plausibility",
        "task_intent_match",
        "occlusion",
        "reachability",
        "support_and_stability",
    }
    require(
        set(visual_review.get("assessment_dimensions", [])) == required_visual_dimensions,
        "visual review does not declare every required semantic dimension",
    )
    require(len(visual_review.get("cases", [])) >= 3, "visual review must cover the three supported tasks")
    for case in visual_review.get("cases", []):
        checks = case.get("checks", {})
        require(checks.get("final_task_relation") == "not_claimed", f"{case.get('task_id')}: visual review must not claim final task success")
        for dimension in required_visual_dimensions:
            require(str(checks.get(dimension, "")).startswith("pass"), f"{case.get('task_id')}: visual review dimension failed: {dimension}")
        for image_name, image_path in case.get("images", {}).items():
            path = artifact_path(image_path)
            require(path.exists(), f"{case.get('task_id')}: missing visual review image {image_path}")
            require(
                hashlib.sha256(path.read_bytes()).hexdigest() == case.get("image_sha256", {}).get(image_name),
                f"{case.get('task_id')}: visual review image hash mismatch: {image_name}",
            )
        runtime_report = read_json(artifact_path(case.get("runtime_corroboration", "")))
        require(
            runtime_report.get("status") in {"pass", "pass_collect_dry_run"},
            f"{case.get('task_id')}: visual review runtime corroboration did not pass",
        )

    final_relation_review = read_json(ROOT / "artifacts" / "visual_review" / "generated_rollout_final_relation_review.json")
    require(
        final_relation_review.get("overall_status") == "pass_scripted_final_relation_support_separate_fixed_scene_policy_eval_passed",
        "generated final-relation review must distinguish visual support from policy eval",
    )
    require(
        artifact_path(final_relation_review.get("contact_sheet", "")).exists(),
        "generated final-relation contact sheet missing",
    )
    reviewed_cases = {case.get("task_id"): case for case in final_relation_review.get("cases", [])}
    require({"task_apple_plate", "task_can_basket"}.issubset(reviewed_cases), "generated final-relation review must cover apple/plate and can/basket")
    for task_id, case in reviewed_cases.items():
        require(case.get("metric_gate", {}).get("status") == "pass", f"{task_id}: final-relation metric gate did not pass")
        require(case.get("metric_gate", {}).get("max_xy_distance_m", 999) < 0.08, f"{task_id}: final-relation xy metric too large")
        require(len(case.get("episodes", [])) >= 3, f"{task_id}: final-relation review must cover three episodes")
        for episode in case.get("episodes", []):
            require(episode.get("check_success") is True, f"{task_id}: reviewed episode did not pass")
            require(episode.get("visual_relation_check") == "pass", f"{task_id}: visual relation check did not pass")
            image_path = artifact_path(episode.get("final_observer_image", ""))
            require(image_path.exists(), f"{task_id}: missing final observer image {image_path}")
            require(image_path.stat().st_size > 0, f"{task_id}: empty final observer image {image_path}")

    action_repair_summary = read_json(ROOT / "artifacts" / "generated_rollout_repair" / "generated_selection2env_action_repair_summary.json")
    require(
        action_repair_summary.get("verdict") == "two_generated_action_rollout_collections_passed",
        "unexpected generated action-repair summary verdict",
    )
    require(len(action_repair_summary.get("passes", [])) >= 2, "generated action-repair summary must include both rollout passes")
    require(len(action_repair_summary.get("collections", [])) >= 2, "generated action-repair summary must include both collections")

    for run, expected_status in RUN_EXPECTATIONS.items():
        summary = read_json(ROOT / run)
        require(summary.get("status") == expected_status, f"{run}: expected {expected_status}, got {summary.get('status')}")

    for run, expected_status in SMOKE_EXPECTATIONS.items():
        report_path = ROOT / run
        report = read_json(report_path)
        require(report.get("status") == expected_status, f"{run}: expected {expected_status}, got {report.get('status')}")
        require(report.get("object_count", 0) >= 2, f"{run}: expected at least two loaded objects")
        for image_path in report.get("images", {}).values():
            require(artifact_path(image_path).exists(), f"{run}: missing smoke image {image_path}")
        pixel_stats = report.get("pixel_stats", {})
        for camera_name in ("observer_camera", "head_camera"):
            require(pixel_stats.get(camera_name, {}).get("std", 0) > 1, f"{run}: {camera_name} appears blank")

    for run, expected_status in BASETASK_SMOKE_EXPECTATIONS.items():
        report_path = ROOT / run
        report = read_json(report_path)
        require(report.get("status") == expected_status, f"{run}: expected {expected_status}, got {report.get('status')}")
        require(len(report.get("initial_poses", {})) >= 2, f"{run}: expected at least two loaded objects")
        for image_path in report.get("images", {}).values():
            require(artifact_path(image_path).exists(), f"{run}: missing Base_Task smoke image {image_path}")

    for run, expected_status in COLLECT_DRYRUN_EXPECTATIONS.items():
        report_path = ROOT / run
        report = read_json(report_path)
        require(report.get("status") == expected_status, f"{run}: expected {expected_status}, got {report.get('status')}")
        require(report.get("episode_count") == 1, f"{run}: expected one dry-run episode")
        require(report.get("object_count", 0) >= 2, f"{run}: expected at least two loaded objects")
        require(report.get("observation_file_count", 0) >= 4, f"{run}: expected saved camera observations")
        require(report.get("policy_execution") == "not_run", f"{run}: must not claim policy execution")
        require(report.get("task_success_claim") == "not_claimed", f"{run}: must not claim task success")
        for key in ("dataset_manifest", "scene_info", "object_states", "observer_video"):
            path = artifact_path(report.get(key, ""))
            require(path.exists(), f"{run}: missing {key}: {path}")
            require(path.stat().st_size > 0, f"{run}: empty {key}: {path}")

    for run, expected_task_id in FINAL_ACCEPTANCE_COLLECT_EXPECTATIONS.items():
        report = read_json(ROOT / run)
        require(report.get("status") == "pass_collect_dry_run", f"{run}: final acceptance /collect did not pass")
        require(report.get("task_id") == expected_task_id, f"{run}: final acceptance task mismatch")
        video_capture = report.get("video_capture", {})
        require(video_capture.get("video_endpoint_only") is False, f"{run}: final acceptance video is endpoint-only")
        require(video_capture.get("simulator_step_stride") == 1, f"{run}: final acceptance video is not per-step")
        require(video_capture.get("frame_count") == 97, f"{run}: final acceptance frame count mismatch")
        encoded_video = probe_video(artifact_path(report.get("observer_video", "")))
        require(encoded_video["frame_count"] == 97, f"{run}: encoded final acceptance frame count mismatch")
        require(abs(encoded_video["fps"] - video_capture["fps"]) < 0.01, f"{run}: final acceptance fps mismatch")

    for run, expected_status in OFFICIAL_ROLLOUT_EXPECTATIONS.items():
        report_path = ROOT / run
        report = read_json(report_path)
        require(report.get("status") == expected_status, f"{run}: expected {expected_status}, got {report.get('status')}")
        require(report.get("probe_type") == "official_robotwin_play_once", f"{run}: wrong probe type")
        require(report.get("check_success") is True, f"{run}: official task check_success did not pass")
        require(report.get("move_event_count", 0) > 0, f"{run}: expected recorded move events")
        require(report.get("left_joint_path_len", 0) + report.get("right_joint_path_len", 0) > 0, f"{run}: expected planned joint paths")
        diagnosis = report.get("failure_diagnosis", {})
        require(diagnosis.get("status") == "no_failure_observed", f"{run}: unexpected diagnosis status")
        checked_categories = set(diagnosis.get("checked_categories", []))
        require(
            REQUIRED_DENSE_DIAGNOSIS_CATEGORIES.issubset(checked_categories),
            f"{run}: required dense diagnosis categories missing",
        )
        require(report.get("next_data_requirement"), f"{run}: missing next data requirement")
        video_capture = report.get("video_capture", {})
        require(video_capture.get("endpoint_only") is False, f"{run}: observer video is endpoint-only")
        require(report.get("video_endpoint_only") is False, f"{run}: endpoint-only compatibility flag is not false")
        require(video_capture.get("frame_count", 0) >= 24, f"{run}: expected at least 24 real frames")
        require(video_capture.get("duration_sec", 0) >= 2, f"{run}: expected at least 2 seconds of video")
        for key in ("events", "move_events", "observer_video"):
            path = artifact_path(report.get(key, ""))
            require(path.exists(), f"{run}: missing {key}: {path}")
            require(path.stat().st_size > 0, f"{run}: empty {key}: {path}")
        encoded_video = probe_video(artifact_path(report["observer_video"]))
        require(encoded_video["frame_count"] == video_capture["frame_count"], f"{run}: encoded/report frame count mismatch")
        require(abs(encoded_video["fps"] - video_capture["fps"]) < 0.01, f"{run}: encoded/report fps mismatch")
        for image_path in report.get("images", {}).values():
            require(artifact_path(image_path).exists(), f"{run}: missing rollout image {image_path}")

    for run, expected in GENERATED_ROLLOUT_EXPECTATIONS.items():
        report_path = ROOT / run
        report = read_json(report_path)
        require(report.get("status") == expected["status"], f"{run}: expected {expected['status']}, got {report.get('status')}")
        require(report.get("probe_type") == "generated_selection2env_play_once", f"{run}: wrong probe type")
        require(report.get("task_id") == expected["task_id"], f"{run}: wrong task id")
        require(report.get("plan_success") is True, f"{run}: generated play_once plan did not succeed")
        require(report.get("check_success") is expected["check_success"], f"{run}: unexpected check_success")
        require(report.get("move_event_count", 0) > 0, f"{run}: expected recorded move events")
        require(report.get("left_joint_path_len", 0) + report.get("right_joint_path_len", 0) > 0, f"{run}: expected planned joint paths")
        checked_categories = set(report.get("failure_diagnosis", {}).get("checked_categories", []))
        require(
            REQUIRED_DENSE_DIAGNOSIS_CATEGORIES.issubset(checked_categories),
            f"{run}: required dense diagnosis categories missing",
        )
        metrics = report.get("relation_metrics", {})
        require(metrics.get("xy_distance_m", 999) < 0.08, f"{run}: generated relation xy distance too large")
        require(metrics.get("left_gripper_open") is True, f"{run}: left gripper should be open after release")
        require(metrics.get("right_gripper_open") is True, f"{run}: right gripper should be open after release")
        require(report.get("next_data_requirement"), f"{run}: missing next data requirement")
        for key in ("events", "move_events", "observer_video"):
            path = artifact_path(report.get(key, ""))
            require(path.exists(), f"{run}: missing {key}: {path}")
            require(path.stat().st_size > 0, f"{run}: empty {key}: {path}")
        for image_path in report.get("images", {}).values():
            require(artifact_path(image_path).exists(), f"{run}: missing generated rollout image {image_path}")

    for run, expected in GENERATED_COLLECTION_EXPECTATIONS.items():
        report = read_json(ROOT / run)
        require(report.get("status") == expected["status"], f"{run}: expected {expected['status']}, got {report.get('status')}")
        require(report.get("probe_type") == "generated_selection2env_multi_episode_play_once_collection", f"{run}: wrong probe type")
        require(report.get("task_id") == expected["task_id"], f"{run}: wrong task id")
        require(report.get("episode_count") == expected["episode_count"], f"{run}: wrong episode count")
        require(report.get("pass_count") == expected["episode_count"], f"{run}: expected every episode to pass")
        require(report.get("fail_count") == 0, f"{run}: expected zero failed episodes")
        require(report.get("policy_execution") == "generated_scripted_play_once", f"{run}: wrong policy execution boundary")
        require(report.get("learned_policy_training") == "not_run", f"{run}: must not claim training")
        require(report.get("learned_policy_evaluation") == "not_run", f"{run}: must not claim evaluation")
        for key in ("dataset_manifest", "events"):
            path = artifact_path(report.get(key, ""))
            require(path.exists(), f"{run}: missing {key}: {path}")
            require(path.stat().st_size > 0, f"{run}: empty {key}: {path}")
        manifest = read_json(artifact_path(report.get("dataset_manifest", "")))
        require(manifest.get("status") == expected["status"], f"{run}: manifest status mismatch")
        require(manifest.get("episode_count") == expected["episode_count"], f"{run}: manifest episode count mismatch")
        for episode in report.get("episodes", []):
            require(episode.get("status") == "pass_generated_action_rollout", f"{run}: episode did not pass")
            require(episode.get("check_success") is True, f"{run}: episode check_success did not pass")
            require(episode.get("planned_joint_paths", 0) > 0, f"{run}: episode missing planned joint path")
            require(episode.get("relation_metrics", {}).get("xy_distance_m", 999) < 0.08, f"{run}: episode relation xy distance too large")
            for key in ("report", "events", "move_events", "policy_trace", "observer_video", "stdout", "stderr"):
                path = artifact_path(episode.get(key, ""))
                require(path.exists(), f"{run}: missing episode {key}: {path}")
                require(path.stat().st_size >= 0, f"{run}: missing episode {key}: {path}")
            for image_path in episode.get("images", {}).values():
                require(artifact_path(image_path).exists(), f"{run}: missing episode image {image_path}")

    act_conversion = read_json(ROOT / ACT_HDF5_CONVERSION_REPORT)
    require(act_conversion.get("status") == "pass_act_hdf5_adapter_smoke", "ACT HDF5 adapter smoke did not pass")
    require(act_conversion.get("pass_count") >= 6, "ACT HDF5 adapter expected at least six converted episodes")
    require(act_conversion.get("fail_count") == 0, "ACT HDF5 adapter should have zero hard conversion failures")
    require(act_conversion.get("act_sim_task_config_json"), "ACT HDF5 adapter missing generated SIM_TASK_CONFIGS path")
    require(artifact_path(act_conversion.get("act_sim_task_config_json", "")).exists(), "ACT HDF5 generated SIM_TASK_CONFIGS missing")
    for episode in act_conversion.get("episodes", []):
        require(episode.get("status") == "pass_act_hdf5_episode", "ACT HDF5 adapter episode did not pass")
        require(episode.get("timesteps", 0) > 1, "ACT HDF5 adapter episode too short")
        require(episode.get("action_dim") == 14, "ACT HDF5 adapter action dimension should be 14")
        path = artifact_path(episode.get("output", ""))
        require(path.exists(), f"missing ACT HDF5 episode: {path}")
        require(path.stat().st_size > 0, f"empty ACT HDF5 episode: {path}")
    act_load = read_json(ROOT / ACT_HDF5_LOAD_DATA_REPORT)
    require(act_load.get("status") == "pass_act_hdf5_loader_smoke", "ACT HDF5 loader smoke did not pass")
    require(act_load.get("num_episodes", 0) >= 6, "ACT HDF5 loader smoke saw too few episodes")
    shapes = act_load.get("batch_item_shapes", {})
    require(shapes.get("qpos") == [14], "ACT HDF5 loader qpos shape mismatch")
    require(shapes.get("image") == [1, 3, 72, 96], "ACT HDF5 loader image shape mismatch")
    require(shapes.get("action", [0, 0])[1] == 14, "ACT HDF5 loader action shape mismatch")
    act_train = read_json(ROOT / ACT_TRAIN_SMOKE_REPORT)
    require(act_train.get("status") == "pass_act_train_smoke", "ACT train smoke did not pass")
    require(act_train.get("returncode") == 0, "ACT train smoke returned nonzero")
    require(act_train.get("best_val_loss") is not None, "ACT train smoke missing validation loss")
    for key in ("policy_best", "dataset_stats", "loss_plot", "l1_plot", "kl_plot"):
        record = act_train.get("files", {}).get(key, {})
        path = artifact_path(record.get("path", ""))
        require(record.get("exists") is True, f"ACT train smoke missing {key}")
        require(path.exists(), f"ACT train smoke file missing: {path}")
        require(path.stat().st_size > 0, f"ACT train smoke file empty: {path}")

    act_eval = read_json(ROOT / ACT_EVAL_SMOKE_REPORT)
    require(act_eval.get("command") == "/evaluate", "generated ACT eval report has wrong command")
    require(
        act_eval.get("status") == "pass_generated_act_evaluate_execution",
        "generated ACT evaluation infrastructure did not complete",
    )
    require(act_eval.get("episode_count", 0) >= 3, "generated ACT eval expected at least three episodes")
    require(
        act_eval.get("execution_count") == act_eval.get("episode_count"),
        "generated ACT eval did not execute every episode",
    )
    require(
        act_eval.get("success_count", 0) + act_eval.get("failure_count", 0) == act_eval.get("episode_count"),
        "generated ACT eval success/failure counts do not cover every episode",
    )
    eval_seeds = set(act_eval.get("held_out_seeds", []))
    source_training_seeds = set(act_eval.get("source_training_seeds", []))
    require(len(eval_seeds) >= 3, "generated ACT eval expected at least three distinct held-out seeds")
    require(eval_seeds.isdisjoint(source_training_seeds), "generated ACT eval seeds overlap source training seeds")
    require(act_eval.get("all_eval_seeds_held_out") is True, "generated ACT eval did not mark all seeds held out")
    require(
        act_eval.get("evaluation_scope", {}).get("domain_randomization") is False,
        "generated ACT eval must preserve its fixed-scene claim boundary",
    )
    require(
        str(act_eval.get("evaluation_scope", {}).get("action_selection", "")).startswith(
            "receding_horizon_first_action"
        ),
        "generated ACT eval must record its action-chunk selection rule",
    )
    require(act_eval.get("next_data_requirement"), "generated ACT eval missing next data requirement")
    model_record = act_eval.get("model", {})
    require(model_record.get("all_keys_matched") is True, "generated ACT eval checkpoint keys did not match")
    require(model_record.get("parameter_count", 0) > 0, "generated ACT eval missing model parameter count")
    require(
        model_record.get("config", {}).get("camera_names") == ["cam_high"],
        "generated ACT eval model camera contract mismatch",
    )
    eval_checkpoint = artifact_path(model_record.get("checkpoint", ""))
    require(eval_checkpoint.exists(), f"generated ACT eval checkpoint missing: {eval_checkpoint}")
    require(eval_checkpoint.stat().st_size > 0, f"generated ACT eval checkpoint empty: {eval_checkpoint}")
    require(
        act_eval.get("camera_adapter", {}).get("runtime_source") == "observer_camera",
        "generated ACT eval must record the observer-camera adapter",
    )
    for key in ("events", "run_state"):
        path = artifact_path(act_eval.get(key, ""))
        require(path.exists(), f"generated ACT eval missing {key}: {path}")
        require(path.stat().st_size > 0, f"generated ACT eval empty {key}: {path}")
    for key in ("stdout", "stderr"):
        path = artifact_path(act_eval.get("process_logs", {}).get(key, ""))
        require(path.exists(), f"generated ACT eval missing process {key}: {path}")
        require(path.stat().st_size > 0, f"generated ACT eval empty process {key}: {path}")
    eval_run_state = read_json(artifact_path(act_eval.get("run_state", "")))
    require(eval_run_state.get("command") == "/evaluate", "generated ACT eval run_state has wrong command")
    require(eval_run_state.get("state") == "completed", "generated ACT eval run_state did not complete")
    require(
        eval_run_state.get("policy_success_rate") == act_eval.get("policy_success_rate"),
        "generated ACT eval run_state success rate mismatch",
    )
    for episode in act_eval.get("episodes", []):
        require(episode.get("command") == "/evaluate", "generated ACT eval episode has wrong command")
        require(episode.get("held_out_seed") is True, "generated ACT eval episode seed was not held out")
        require(episode.get("execution_complete") is True, "generated ACT eval episode did not execute")
        require(episode.get("policy_step_count", 0) >= 1, "generated ACT eval episode has no policy steps")
        require(isinstance(episode.get("policy_success"), bool), "generated ACT eval episode missing policy verdict")
        for key in ("failure_diagnosis", "events", "action_trace", "observer_video"):
            path = artifact_path(episode.get(key, ""))
            require(path.exists(), f"generated ACT eval episode missing {key}: {path}")
            require(path.stat().st_size > 0, f"generated ACT eval episode empty {key}: {path}")
        action_trace = read_json(artifact_path(episode.get("action_trace", "")))
        require(
            action_trace.get("action_count") == episode.get("policy_step_count"),
            "generated ACT eval action trace count mismatch",
        )
        diagnosis = read_json(artifact_path(episode.get("failure_diagnosis", "")))
        checked_categories = set(diagnosis.get("required_categories_checked", []))
        require(
            checked_categories == REQUIRED_DENSE_DIAGNOSIS_CATEGORIES,
            "generated ACT eval diagnosis category contract mismatch",
        )
        diagnosis_categories = set(diagnosis.get("categories", {}))
        require(
            REQUIRED_DENSE_DIAGNOSIS_CATEGORIES.issubset(diagnosis_categories),
            "generated ACT eval diagnosis payload is incomplete",
        )
        require(
            "target_relation_not_satisfied" in diagnosis_categories,
            "generated ACT eval diagnosis missing target relation verdict",
        )
        for image_path in episode.get("images", {}).values():
            path = artifact_path(image_path)
            require(path.exists(), f"generated ACT eval image missing: {path}")
            require(path.stat().st_size > 0, f"generated ACT eval image empty: {path}")

    native_collection = read_json(ROOT / NATIVE_COLLECTION_REPORT)
    require(native_collection.get("episode_count") == 4, "native synchronized collection expected four attempts")
    require(native_collection.get("pass_count") == 3, "native synchronized collection expected three task passes")
    require(native_collection.get("fail_count") == 1, "native synchronized collection expected one retained task failure")
    require(
        native_collection.get("native_synchronized_pass_count") == 4,
        "native synchronized collection did not record every attempt",
    )
    for episode in native_collection.get("episodes", []):
        native = episode.get("native_synchronized_data", {})
        require(native.get("status") == "pass_native_synchronized_recording", "native episode recording did not pass")
        require(native.get("frame_count") == 162, "native episode frame count mismatch")
        for key in ("hdf5", "video"):
            path = artifact_path(native.get(key, ""))
            require(path.exists(), f"native episode missing {key}: {path}")
            require(path.stat().st_size > 0, f"native episode empty {key}: {path}")

    native_conversion = read_json(ROOT / NATIVE_ACT_CONVERSION_REPORT)
    require(native_conversion.get("status") == "pass_native_act_hdf5_adapter", "native ACT conversion did not pass")
    require(native_conversion.get("pass_count") == 3, "native ACT conversion expected three successful episodes")
    require(native_conversion.get("skip_count") == 1, "native ACT conversion expected one skipped failed source")
    require(native_conversion.get("fail_count") == 0, "native ACT conversion had hard failures")
    require(
        native_conversion.get("native_jpeg_color_repair", {}).get("adapter_behavior"),
        "native ACT conversion missing color-repair provenance",
    )
    converted_paths = []
    for episode in native_conversion.get("episodes", []):
        if episode.get("status") != "pass_native_act_hdf5_episode":
            continue
        require(episode.get("act_timestep_count") == 161, "native ACT episode timestep count mismatch")
        require(episode.get("action_dim") == 14, "native ACT episode action dimension mismatch")
        require(episode.get("camera_source") == "observation/head_camera/rgb", "native ACT camera source mismatch")
        require(episode.get("native_jpeg_color_repair"), "native ACT episode missing JPEG repair marker")
        path = artifact_path(episode.get("output", ""))
        converted_paths.append(path)
        require(path.exists(), f"native ACT episode missing: {path}")
        require(path.stat().st_size > 0, f"native ACT episode empty: {path}")
    require(len(converted_paths) == 3, "native ACT conversion path count mismatch")

    native_load = read_json(ROOT / NATIVE_ACT_LOAD_REPORT)
    require(native_load.get("status") == "pass_act_hdf5_loader_smoke", "native ACT loader did not pass")
    require(native_load.get("num_episodes") == 3, "native ACT loader expected three episodes")
    native_shapes = native_load.get("batch_item_shapes", {})
    require(native_shapes.get("image") == [1, 3, 72, 96], "native ACT loader image shape mismatch")
    require(native_shapes.get("qpos") == [14], "native ACT loader qpos shape mismatch")
    require(native_shapes.get("action") == [161, 14], "native ACT loader action shape mismatch")
    require(native_shapes.get("is_pad") == [161], "native ACT loader padding shape mismatch")

    native_replay = read_json(ROOT / NATIVE_ACT_REPLAY_REPORT)
    require(native_replay.get("status") == "pass_act_action_replay_execution", "native ACT action replay did not execute")
    require(native_replay.get("execution_complete") is True, "native ACT action replay did not complete")
    require(native_replay.get("task_success") is True, "native ACT action replay did not satisfy the task verifier")
    require(native_replay.get("executed_action_count") == 161, "native ACT action replay count mismatch")
    require(
        native_replay.get("initial_qpos_match", {}).get("max_abs_error") == 0.0,
        "native ACT action replay initial qpos does not exactly match the source",
    )
    require(native_replay.get("relation_metrics", {}).get("xy_distance_m", 999) < 0.13, "native replay relation failed")
    for key in ("action_trace", "observer_video"):
        path = artifact_path(native_replay.get(key, ""))
        require(path.exists(), f"native ACT replay missing {key}: {path}")
        require(path.stat().st_size > 0, f"native ACT replay empty {key}: {path}")

    native_train = read_json(ROOT / NATIVE_ACT_TRAIN_REPORT)
    require(native_train.get("status") == "pass_act_train_execution", "native ACT training did not pass")
    require(native_train.get("returncode") == 0, "native ACT training returned nonzero")
    require(native_train.get("num_epochs") == 1200, "native ACT training epoch count mismatch")
    require(native_train.get("best_val_loss", 999) < 0.02, "native ACT validation loss is above the recorded gate")
    for key in ("policy_best", "dataset_stats", "loss_plot", "l1_plot", "kl_plot"):
        record = native_train.get("files", {}).get(key, {})
        path = artifact_path(record.get("path", ""))
        require(record.get("exists") is True, f"native ACT training report missing {key}")
        require(path.exists(), f"native ACT training file missing: {path}")
        require(path.stat().st_size > 0, f"native ACT training file empty: {path}")

    native_eval = read_json(ROOT / NATIVE_ACT_EVAL_REPORT)
    require(native_eval.get("status") == "pass_generated_act_evaluate_execution", "native ACT evaluation did not execute")
    require(native_eval.get("episode_count") == 3, "native ACT evaluation expected three episodes")
    require(native_eval.get("execution_count") == 3, "native ACT evaluation did not execute all episodes")
    require(native_eval.get("success_count") == 3, "native ACT evaluation did not pass all fixed-scene episodes")
    require(native_eval.get("failure_count") == 0, "native ACT evaluation recorded unexpected failures")
    require(native_eval.get("policy_success_rate") == 1.0, "native ACT evaluation success rate mismatch")
    require(native_eval.get("all_eval_seeds_held_out") is True, "native ACT eval seeds overlap source seeds")
    require(set(native_eval.get("held_out_seeds", [])) == {4, 5, 6}, "native ACT held-out seed set mismatch")
    require(set(native_eval.get("source_training_seeds", [])) == {0, 1, 2}, "native ACT source seed set mismatch")
    require(native_eval.get("camera_adapter", {}).get("runtime_source") == "head_camera", "native ACT eval camera mismatch")
    require(
        native_eval.get("evaluation_scope", {}).get("action_selection") == "execute_full_161_action_chunk_before_replan",
        "native ACT eval action scheduling mismatch",
    )
    require(native_eval.get("evaluation_scope", {}).get("domain_randomization") is False, "native ACT eval claim boundary changed")
    require(native_eval.get("model", {}).get("all_keys_matched") is True, "native ACT eval checkpoint keys did not match")
    require(native_eval.get("model", {}).get("config", {}).get("chunk_size") == 161, "native ACT model chunk size mismatch")
    native_checkpoint = artifact_path(native_eval.get("model", {}).get("checkpoint", ""))
    require(native_checkpoint.exists(), f"native ACT checkpoint missing: {native_checkpoint}")
    require(native_checkpoint.stat().st_size > 0, f"native ACT checkpoint empty: {native_checkpoint}")
    for key in ("events", "run_state"):
        path = artifact_path(native_eval.get(key, ""))
        require(path.exists(), f"native ACT eval missing {key}: {path}")
        require(path.stat().st_size > 0, f"native ACT eval empty {key}: {path}")
    for key in ("stdout", "stderr"):
        path = artifact_path(native_eval.get("process_logs", {}).get(key, ""))
        require(path.exists(), f"native ACT eval missing process {key}: {path}")
        require(path.stat().st_size > 0, f"native ACT eval empty process {key}: {path}")
    for episode in native_eval.get("episodes", []):
        require(episode.get("execution_complete") is True, "native ACT eval episode did not complete")
        require(episode.get("policy_success") is True, "native ACT eval episode did not pass")
        require(episode.get("policy_step_count") == 130, "native ACT eval episode success step changed")
        diagnosis = read_json(artifact_path(episode.get("failure_diagnosis", "")))
        require(
            set(diagnosis.get("required_categories_checked", [])) == REQUIRED_DENSE_DIAGNOSIS_CATEGORIES,
            "native ACT eval diagnosis category contract mismatch",
        )
    summary_eval = action_repair_summary.get("act_evaluate", {})
    require(summary_eval.get("status") == native_eval.get("status"), "action-repair summary eval status mismatch")
    require(
        summary_eval.get("execution_count") == native_eval.get("execution_count"),
        "action-repair summary eval execution count mismatch",
    )
    require(
        summary_eval.get("success_count") == native_eval.get("success_count"),
        "action-repair summary eval success count mismatch",
    )

    native_diagnosis = read_json(ROOT / NATIVE_ACT_DIAGNOSIS)
    require(
        native_diagnosis.get("status") == "pass_native_act_fixed_scene_closed_loop",
        "native ACT closed-loop diagnosis did not pass",
    )
    require(
        native_diagnosis.get("adapter", {}).get("color_repair", {}).get("repair_is_better") is True,
        "native ACT color repair is not supported by the recorded comparison",
    )
    require(
        native_diagnosis.get("dataset_identity", {}).get("unique_action_trajectory_count") == 1,
        "native ACT diagnosis must retain the one-unique-trajectory claim boundary",
    )
    require(
        native_diagnosis.get("dataset_identity", {}).get("all_converted_episodes_byte_identical") is True,
        "native ACT diagnosis must retain byte-identical dataset evidence",
    )
    require(
        native_diagnosis.get("experiments", {}).get("chunk20", {}).get("evaluate", {}).get("success_count") == 0,
        "native ACT diagnosis missing chunk-20 failure baseline",
    )
    require(
        native_diagnosis.get("experiments", {}).get("chunk161", {}).get("evaluate", {}).get("success_count") == 3,
        "native ACT diagnosis missing chunk-161 success",
    )
    require(
        native_diagnosis.get("conclusion", {}).get("policy_promotion") == "blocked_robustness_not_tested",
        "native ACT diagnosis overstates policy promotion",
    )

    robustness = read_json(ROOT / PLACEMENT_ROBUSTNESS_DIAGNOSIS)
    require(
        robustness.get("status") == "pass_failure_to_data_iteration_promotion_rejected",
        "placement-robustness diagnosis status mismatch",
    )
    initial_iteration = robustness.get("initial_iteration", {})
    initial_dataset = initial_iteration.get("dataset", {})
    require(initial_dataset.get("status") == "pass_act_dataset_diversity", "initial varied dataset gate did not pass")
    require(initial_dataset.get("episode_count") == 12, "initial varied dataset episode count mismatch")
    require(initial_dataset.get("unique_placement_signature_count") == 7, "initial varied placement count mismatch")
    require(initial_dataset.get("unique_action_trajectory_count") == 11, "initial varied action count mismatch")
    require(initial_iteration.get("train", {}).get("status") == "pass_act_train_execution", "initial varied ACT train did not pass")
    initial_eval = initial_iteration.get("heldout_evaluate", {})
    require(initial_eval.get("execution_count") == 4, "initial held-out eval did not execute 4/4")
    require(initial_eval.get("success_count") == 1, "initial held-out eval success count changed")
    require(initial_eval.get("all_eval_placements_held_out") is True, "initial eval placements are not held out")
    horizon_eval = initial_iteration.get("chunk_prefix_40_evaluate", {})
    require(horizon_eval.get("execution_count") == 4, "chunk-prefix eval did not execute 4/4")
    require(horizon_eval.get("success_count") == 1, "chunk-prefix control success count changed")
    require(
        horizon_eval.get("action_selection") == "execute_first_40_of_175_actions_before_replan",
        "chunk-prefix control action schedule mismatch",
    )

    recovery = robustness.get("failure_to_data_iteration", {})
    require(recovery.get("all_failed_placements_recovered_as_expert_data") is True, "failed placements were not recovered")
    require(recovery.get("recovery_collection", {}).get("pass_count") == 3, "recovery collection expected 3/3 passes")
    recovery_dataset = recovery.get("dataset", {})
    require(recovery_dataset.get("status") == "pass_act_dataset_diversity", "recovery dataset gate did not pass")
    require(recovery_dataset.get("episode_count") == 15, "recovery dataset episode count mismatch")
    require(recovery_dataset.get("unique_placement_signature_count") == 10, "recovery placement count mismatch")
    require(recovery_dataset.get("unique_action_trajectory_count") == 14, "recovery action count mismatch")
    require(recovery.get("train", {}).get("status") == "pass_act_train_execution", "recovery ACT train did not pass")
    require(
        recovery.get("new_holdout_feasibility", {}).get("pass_count") == 6,
        "new held-out feasibility scan expected six passing candidates",
    )
    require(
        recovery.get("new_holdout_minimum_pose_vector_distance_to_training_m", 0) > 0.0,
        "new held-out placements are not spatially separated from training",
    )
    final_eval = recovery.get("new_heldout_evaluate", {})
    require(final_eval.get("execution_count") == 4, "new held-out eval did not execute 4/4")
    require(final_eval.get("success_count") == 1, "new held-out eval success count changed")
    require(final_eval.get("all_eval_placements_held_out") is True, "new eval placements are not held out")
    extra_task = robustness.get("additional_task_execution", {})
    require(extra_task.get("pass_count") == 2, "additional task expected 2/2 scripted passes")
    require(extra_task.get("native_synchronized_pass_count") == 2, "additional task native recording expected 2/2")
    gates = robustness.get("promotion_gates", {})
    require(gates.get("new_heldout_eval_all_success") is False, "held-out success gate must remain rejected")
    require(gates.get("additional_task_scripted_execution") is True, "additional task execution gate did not pass")
    require(gates.get("declared_visual_physics_domain_randomization") is False, "domain-randomization gate is overstated")
    require(gates.get("additional_task_learned_policy_evaluation") is False, "cross-task policy gate is overstated")
    require(
        robustness.get("policy_promotion") == "blocked_placement_domain_and_cross_task_robustness",
        "placement-robustness diagnosis overstates policy promotion",
    )

    policy_probe = read_json(ROOT / POLICY_TRAIN_EVAL_PROBE)
    require(policy_probe.get("status") == "blocked_policy_train_eval_not_wired", "policy train/eval probe should remain an explicit blocker")
    require(policy_probe.get("hdf5_file_count", 0) >= 6, "policy train/eval probe should see generated ACT HDF5 episodes")
    require(policy_probe.get("act_hdf5_adapter", {}).get("conversion_status") == "pass_act_hdf5_adapter_smoke", "policy probe missing ACT HDF5 conversion pass")
    require(policy_probe.get("act_hdf5_adapter", {}).get("loader_status") == "pass_act_hdf5_loader_smoke", "policy probe missing ACT HDF5 loader pass")
    require(policy_probe.get("command_failure_count", 0) >= 3, "policy train/eval probe should record failed entrypoint checks")
    blocking_reasons = " ".join(policy_probe.get("blocking_reasons", []))
    require("entrypoint" in blocking_reasons, "policy train/eval probe must record entrypoint failures")
    command_names = {item.get("name") for item in policy_probe.get("commands", [])}
    require({"act_process_data", "act_train_entry", "act_eval_entry"}.issubset(command_names), "policy train/eval probe missing command coverage")
    command_text = "\n".join(
        (item.get("stdout_tail", "") + "\n" + item.get("stderr_tail", ""))
        for item in policy_probe.get("commands", [])
    )
    require("No such file or directory" in command_text, "policy train/eval probe must record missing ACT data path")
    require("sim-task_apple_plate-demo_clean-3" in command_text, "policy train/eval probe must record missing default ACT task config")
    require("No module named 'envs.task_apple_plate'" in command_text, "policy train/eval probe must record eval task-env blocker")
    for item in policy_probe.get("commands", []):
        require(artifact_path(item.get("stdout", "")).exists(), f"policy train/eval probe missing stdout log for {item.get('name')}")
        require(artifact_path(item.get("stderr", "")).exists(), f"policy train/eval probe missing stderr log for {item.get('name')}")

    openxsim_schema = read_json(ROOT / OPENXSIM_BENCHMARK_SCHEMA)
    jsonschema.Draft202012Validator.check_schema(openxsim_schema)
    openxsim_manifest = read_json(ROOT / OPENXSIM_BENCHMARK_MANIFEST)
    require(
        openxsim_manifest.get("status") == "pass_three_openxsim_benchmark_bundles",
        "Open X Sim benchmark manifest did not pass",
    )
    require(openxsim_manifest.get("benchmark_count") == 3, "Open X Sim benchmark manifest must contain three bundles")
    benchmark_rows = openxsim_manifest.get("benchmarks", [])
    require(
        {row.get("task_name") for row in benchmark_rows} == set(OPENXSIM_BENCHMARK_TASKS),
        "Open X Sim benchmark task set mismatch",
    )
    for row in benchmark_rows:
        task_name = row.get("task_name")
        bundle_path = artifact_path(row.get("manifest", ""))
        require(bundle_path.exists(), f"{task_name}: Open X Sim bundle missing: {bundle_path}")
        bundle = read_json(bundle_path)
        jsonschema.validate(bundle, openxsim_schema)
        require(bundle.get("task_name") == task_name, f"{task_name}: bundle task mismatch")
        require(
            set(bundle.get("command_loop", {})) == {"/gen-env", "/collect", "/diagnose", "/evaluate"},
            f"{task_name}: command-loop stage coverage mismatch",
        )
        require(
            bundle.get("command_loop", {}).get("/evaluate", {}).get("learned_policy") is False,
            f"{task_name}: official benchmark must not claim learned-policy evaluation",
        )
        artifacts = bundle.get("artifacts", {})
        for key, value in artifacts.items():
            path = artifact_path(value)
            require(path.exists(), f"{task_name}: missing benchmark artifact {key}: {path}")
            require(path.stat().st_size > 0, f"{task_name}: empty benchmark artifact {key}: {path}")

        source_rollout = read_json(artifact_path(artifacts.get("rollout_report", "")))
        require(source_rollout.get("status") == "pass_action_rollout", f"{task_name}: source rollout did not pass")
        require(source_rollout.get("check_success") is True, f"{task_name}: source success verifier did not pass")
        require(
            artifact_path(OPENXSIM_BENCHMARK_TASKS[task_name]) == artifact_path(artifacts.get("rollout_report", "")),
            f"{task_name}: source rollout path mismatch",
        )

        run_state = read_json(artifact_path(artifacts.get("run_state", "")))
        require(run_state.get("status") == bundle.get("status"), f"{task_name}: run_state status mismatch")
        require(run_state.get("current_stage") == "/evaluate", f"{task_name}: run_state did not reach /evaluate")
        require(run_state.get("learned_policy") is False, f"{task_name}: run_state claim boundary mismatch")
        require(run_state.get("next_data_requirement"), f"{task_name}: run_state missing next data requirement")

        event_rows = [json.loads(line) for line in artifact_path(artifacts.get("events", "")).read_text(encoding="utf-8").splitlines() if line]
        require(len(event_rows) >= 5, f"{task_name}: command-loop events are incomplete")
        require(
            [event.get("sequence") for event in event_rows] == list(range(len(event_rows))),
            f"{task_name}: command-loop event sequence is not contiguous",
        )
        require(
            {event.get("command") for event in event_rows}.issuperset({"/gen-env", "/collect", "/diagnose", "/evaluate"}),
            f"{task_name}: command-loop events miss a required command",
        )

        scene_manifest = read_json(artifact_path(artifacts.get("scene_manifest", "")))
        require(scene_manifest.get("simulator_adapter") == "RoboTwin/SAPIEN", f"{task_name}: scene adapter mismatch")
        require(len(scene_manifest.get("initial_entities", {})) >= 3, f"{task_name}: scene manifest missing entities")
        require(len(scene_manifest.get("final_entities", {})) >= 3, f"{task_name}: final scene state missing entities")
        require(len(scene_manifest.get("camera_artifacts", {})) >= 4, f"{task_name}: scene manifest missing cameras")
        for image_path in scene_manifest.get("camera_artifacts", {}).values():
            path = artifact_path(image_path)
            require(path.exists(), f"{task_name}: scene camera artifact missing: {path}")
            require(path.stat().st_size > 0, f"{task_name}: scene camera artifact empty: {path}")

        task_manifest = read_json(artifact_path(artifacts.get("task_manifest", "")))
        require(task_manifest.get("learned_policy") is False, f"{task_name}: task manifest claim boundary mismatch")
        require(
            task_manifest.get("success_verifier", {}).get("result") is True,
            f"{task_name}: task manifest success verifier did not pass",
        )
        require(task_manifest.get("next_data_requirement"), f"{task_name}: task manifest missing next data requirement")

        diagnosis = read_json(artifact_path(artifacts.get("failure_diagnosis", "")))
        require(diagnosis.get("status") == "no_failure_observed", f"{task_name}: benchmark diagnosis status mismatch")
        require(
            REQUIRED_DENSE_DIAGNOSIS_CATEGORIES.issubset(set(diagnosis.get("checked_categories", []))),
            f"{task_name}: benchmark diagnosis category coverage mismatch",
        )

    gen_env_schema = read_json(ROOT / GEN_ENV_SCHEMA)
    jsonschema.Draft202012Validator.check_schema(gen_env_schema)
    route_enum = set(gen_env_schema.get("properties", {}).get("route", {}).get("enum", []))
    require({"selection2env", "forge_fallback", "material_sidecar"}.issubset(route_enum), "/gen-env schema missing route coverage")
    defs = gen_env_schema.get("$defs", {})
    branch_requirements = {
        "selection2env_outputs": [
            "asset_candidates",
            "selected_assets",
            "placement_regions",
            "support_surface",
            "pose_constraints",
            "camera_observation",
            "robot_constraints",
            "success_verifier",
        ],
        "forge_fallback_outputs": [
            "capture_source",
            "object_prompts",
            "masks",
            "meshes",
            "world_frame_poses",
            "scene_json",
            "urdf",
            "physics_metadata",
            "provenance",
            "import_gate",
        ],
        "material_sidecar_outputs": [
            "textures",
            "materials",
            "albedo",
            "specular_latent",
            "uncertainty",
            "lighting_assumption",
            "renderer_binding",
            "material_provenance",
            "render_gate",
        ],
    }
    for def_name, required_fields in branch_requirements.items():
        definition = defs.get(def_name, {})
        require(set(required_fields).issubset(set(definition.get("required", []))), f"/gen-env schema missing {def_name} required fields")
    gen_env_contract_reports = []
    for report_path, expected in GEN_ENV_CONTRACT_EXAMPLES.items():
        report = read_json(ROOT / report_path)
        gen_env_contract_reports.append(report)
        jsonschema.validate(report, gen_env_schema)
        require(report.get("command") == "/gen-env", f"{report_path}: wrong command")
        require(report.get("route") == expected["route"], f"{report_path}: wrong /gen-env route")
        require(report.get("status") == expected["status"], f"{report_path}: wrong route status")
        require(expected["output_key"] in report.get("outputs", {}), f"{report_path}: missing route output branch")
        require(report.get("claim_boundary"), f"{report_path}: missing claim boundary")
        if expected.get("required_blocker"):
            blocker_codes = {blocker.get("code") for blocker in report.get("blockers", [])}
            require(expected["required_blocker"] in blocker_codes, f"{report_path}: missing required blocker")
    gen_env_contract_manifest = read_json(ROOT / GEN_ENV_CONTRACT_MANIFEST)
    require(gen_env_contract_manifest.get("status") == "pass_gen_env_contract_examples", "unexpected /gen-env contract manifest status")
    require(set(gen_env_contract_manifest.get("examples", [])) == set(GEN_ENV_CONTRACT_EXAMPLES), "/gen-env contract manifest example list mismatch")

    fallback_schema = read_json(ROOT / GEN_ENV_FALLBACK_SCHEMA)
    fallback_reports = []
    for report_path, expected in GEN_ENV_FALLBACK_PROBES.items():
        report = read_json(ROOT / report_path)
        fallback_reports.append(report)
        jsonschema.validate(report, fallback_schema)
        require(report.get("command") == "/gen-env", f"{report_path}: wrong command")
        require(report.get("fallback_type") == expected["fallback_type"], f"{report_path}: wrong fallback type")
        require(report.get("status") == expected["status"], f"{report_path}: wrong fallback status")
        require(report.get("claim_boundary"), f"{report_path}: missing claim boundary")
        require(len(report.get("expected_outputs", [])) >= 5, f"{report_path}: missing expected outputs")
        checks = report.get("checks", [])
        require(any(check.get("status") == "fail" for check in checks), f"{report_path}: expected at least one failed gate")
        import_gates = report.get("import_gate_matrix", {})
        require(
            set(import_gates) == {"import", "scale", "collision", "support", "material", "render", "verifier"},
            f"{report_path}: incomplete fallback import gate matrix",
        )
        require(
            all(gate.get("status", "").startswith("blocked_") for gate in import_gates.values()),
            f"{report_path}: fallback gate matrix must retain blocked statuses",
        )
        blocker_codes = {blocker.get("code") for blocker in report.get("blockers", [])}
        require(expected["required_blocker"] in blocker_codes, f"{report_path}: missing required blocker")
    fallback_summary = read_json(ROOT / GEN_ENV_FALLBACK_SUMMARY)
    require(fallback_summary.get("status") == "blocked_fallback_inputs_missing", "unexpected /gen-env fallback summary status")
    require(set(fallback_summary.get("probes", [])) == set(GEN_ENV_FALLBACK_PROBES), "fallback summary probe list mismatch")
    require(fallback_summary.get("claim_boundary"), "fallback summary missing claim boundary")

    selection_schema = read_json(ROOT / "schemas" / "selection2env.schema.json")
    task_program_schema = read_json(ROOT / TASK_PROGRAM_SCHEMA)
    manifest = read_json(ROOT / "artifacts" / "selection2env_manifest.json")
    artifacts = manifest.get("artifacts", [])
    require(len(artifacts) >= 4, "expected at least four selection2env artifacts")
    for artifact in artifacts:
        jsonschema.validate(artifact, selection_schema)
        require(
            artifact.get("catalog_sources", {}).get("robotwin", {}).get("entry_count", 0) >= 100,
            f"{artifact['task_id']}: full RoboTwin catalog not connected",
        )
        require(
            artifact.get("catalog_sources", {}).get("agenticsim", {}).get("selection_eligible_count") == 8,
            f"{artifact['task_id']}: AgenticSim aliases not connected",
        )
        require(
            artifact.get("catalog_sources", {}).get("articraft10k", {}).get("entry_count", 0) >= 9000,
            f"{artifact['task_id']}: Articraft manifest not connected",
        )
        if artifact["status"] == "pass_sim_smoke":
            smoke = artifact.get("simulator_smoke", {})
            require(smoke.get("asset_load_render", {}).get("status") == "pass_asset_load_render", f"{artifact['task_id']}: missing asset-load render smoke")
            require(smoke.get("basetask_curobo", {}).get("status") == "pass", f"{artifact['task_id']}: missing Base_Task/CuRobo smoke")
            require(smoke.get("collect_dry_run", {}).get("status") == "pass_collect_dry_run", f"{artifact['task_id']}: missing /collect dry-run")
            require(smoke.get("collect_dry_run", {}).get("policy_execution") == "not_run", f"{artifact['task_id']}: collect dry-run must not claim policy rollout")
            require(
                "VISUAL_REVIEW_REQUIRED" not in {blocker.get("code") for blocker in artifact.get("blockers", [])},
                f"{artifact['task_id']}: completed visual review is still marked as a blocker",
            )

    supported = [item for item in artifacts if item["status"] == "pass_sim_smoke"]
    unsupported = [item for item in artifacts if item["status"] == "unsupported_blocker"]
    require(len(supported) >= 3, "expected at least three simulator-smoke-supported tasks")
    require(len(unsupported) >= 1, "expected at least one unsupported blocker task")

    require(
        len({item["scene_id"] for item in artifacts}) == len(artifacts),
        "selection2env artifacts must not fake scene-task decoupling by reusing a nominal scene id across different placements",
    )

    task_program_paths = sorted((ROOT / "artifacts" / "task_program_inputs").glob("*.json"))
    require(len(task_program_paths) >= 4, "expected primary task programs plus same-scene alternate")
    task_programs: dict[str, dict] = {}
    for task_program_path in task_program_paths:
        task_program = read_json(task_program_path)
        jsonschema.validate(task_program, task_program_schema)
        reference_report = validate_task_program_references(task_program)
        require(reference_report["status"] == "pass", f"{task_program_path}: task-program references do not resolve")
        task_programs[task_program["task_id"]] = task_program

    require("task_apple_plate" in task_programs, "primary apple/plate task-program input missing")
    require("task_apple_plate_to_left_front" in task_programs, "same-scene alternate task-program input missing")
    pair_report = validate_scene_task_pair(
        task_programs["task_apple_plate"],
        task_programs["task_apple_plate_to_left_front"],
    )
    require(pair_report["status"] == "pass", "same-scene task-program pair failed strict reference validation")

    decoupling = read_json(ROOT / SCENE_TASK_DECOUPLING_REPORT)
    require(
        decoupling.get("status") == "pass_same_scene_two_executable_tasks",
        "same-scene task decoupling execution evidence did not pass",
    )
    require(decoupling.get("task_count") == 2, "scene-task decoupling must contain exactly two task executions")
    require(
        decoupling.get("schema_version") == "alchedata.scene_task_decoupling_evidence.v1",
        "scene-task decoupling evidence schema is stale",
    )
    require(
        decoupling.get("initial_observer_bytes_identical") is True,
        "scene-task decoupling initial observer bytes differ",
    )
    require(decoupling.get("scene_id") == pair_report.get("scene_id"), "decoupling scene_id mismatch")
    require(decoupling.get("placement_sha256") == pair_report.get("placement_sha256"), "decoupling placement hash mismatch")
    for rollout in decoupling.get("rollouts", []):
        require(rollout.get("status") == "pass", f"decoupling rollout failed: {rollout.get('task_id')}")
        require(all(rollout.get("checks", {}).values()), f"decoupling rollout check failed: {rollout.get('task_id')}")
        video_path = artifact_path(rollout.get("observer_video", ""))
        require(video_path.exists(), f"decoupling video missing: {rollout.get('task_id')}")
        video_capture = rollout.get("video_capture", {})
        require(video_capture.get("endpoint_only") is False, f"decoupling video is endpoint-only: {rollout.get('task_id')}")
        require(video_capture.get("frame_count", 0) >= 500, f"decoupling video is too short: {rollout.get('task_id')}")
        encoded_video = probe_video(video_path)
        require(
            encoded_video["frame_count"] == video_capture["frame_count"],
            f"decoupling encoded frame count mismatch: {rollout.get('task_id')}",
        )
        require(
            abs(encoded_video["fps"] - video_capture["fps"]) < 0.01,
            f"decoupling encoded fps mismatch: {rollout.get('task_id')}",
        )
        for image_path in rollout.get("images", {}).values():
            require(artifact_path(image_path).exists(), f"decoupling image missing: {image_path}")
    initial_observer_paths = [
        artifact_path(rollout["images"]["initial_observer_camera"])
        for rollout in decoupling.get("rollouts", [])
    ]
    initial_observer_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in initial_observer_paths]
    require(len(set(initial_observer_hashes)) == 1, "scene-task decoupling initial observer image hashes differ")
    require(
        initial_observer_hashes[0] == decoupling.get("initial_observer_sha256"),
        "scene-task decoupling initial observer hash record mismatch",
    )

    blocker_artifact = unsupported[0]
    blocker_codes = {blocker.get("code") for blocker in blocker_artifact.get("blockers", [])}
    require(
        blocker_codes == {"ARTICULATED_CONTAINER_TASK_API_UNSUPPORTED"},
        "drawer/mug blocker must reflect the task API boundary, not a false asset miss",
    )
    candidate_backend_ids = {candidate.get("backend_asset_id") for candidate in blocker_artifact.get("asset_candidates", [])}
    require({"036_cabinet", "039_mug"}.issubset(candidate_backend_ids), "drawer/mug existing assets were not discovered")

    print("PASS workspace verification")
    print(
        f"docs={len(REQUIRED_DOCS)} static_runs={len(RUN_EXPECTATIONS)} "
        f"asset_smoke_runs={len(SMOKE_EXPECTATIONS)} basetask_smoke_runs={len(BASETASK_SMOKE_EXPECTATIONS)} "
        f"collect_dry_runs={len(COLLECT_DRYRUN_EXPECTATIONS)} "
        f"fresh_collect_dry_runs={len(FINAL_ACCEPTANCE_COLLECT_EXPECTATIONS)} "
        f"official_rollouts={len(OFFICIAL_ROLLOUT_EXPECTATIONS)} "
        f"generated_rollouts={len(GENERATED_ROLLOUT_EXPECTATIONS)} "
        f"generated_collections={len(GENERATED_COLLECTION_EXPECTATIONS)} "
        f"articraft_assets={articraft_manifest.get('asset_count')} "
        f"articraft_archive_passes={articraft_archive_passes} "
        f"articraft_collision_blockers={articraft_collision_blockers} "
        f"act_hdf5={act_conversion.get('pass_count')} "
        f"act_train={act_train.get('status')} "
        f"act_eval={act_eval.get('status')} "
        f"act_eval_success={act_eval.get('success_count')}/{act_eval.get('episode_count')} "
        f"native_act_data={native_conversion.get('pass_count')} "
        f"native_act_replay={native_replay.get('task_success')} "
        f"native_act_train={native_train.get('status')} "
        f"native_act_eval_success={native_eval.get('success_count')}/{native_eval.get('episode_count')} "
        f"act_placement_eval_success={final_eval.get('success_count')}/{final_eval.get('episode_count')} "
        f"act_placement_promotion={robustness.get('policy_promotion')} "
        f"sceneagent_acceptance={sceneagent_acceptance.get('pass_count')}/8 "
        f"scene_task_videos={'+'.join(str(row['video_capture']['frame_count']) for row in decoupling.get('rollouts', []))} "
        f"sceneagent_policy=4/4+4/4+3/3 "
        f"text2env_empirics={len(empirical_audit.get('gates', {}))}/5 "
        f"memory_ablation={memory_outcomes.get('no_memory_success_count')}/3->{memory_outcomes.get('memory_success_count')}/3 "
        f"isaac_command_bundle={isaac_bundle.get('file_count')}/34 "
        f"core_causal_gates={core_gates.get('status')} "
        f"policy_probe={policy_probe.get('status')} "
        f"openxsim_bundles={openxsim_manifest.get('benchmark_count')} "
        f"gen_env_contracts={len(gen_env_contract_reports)} "
        f"fallback_blockers={len(fallback_reports)} "
        f"literature_sources={literature_review.get('source_count')} "
        f"literature_matrix={literature_review.get('matrix_rows')}x{literature_review.get('matrix_capabilities')} "
        f"openxsim_commands={openxsim_package.get('commands')} "
        f"openxsim_adapters={openxsim_package.get('adapters')} "
        f"openxsim_acceptance={openxsim_package.get('acceptance_items')}/8 "
        f"harness_acceptance={harness_package.get('acceptance_items')}/6 "
        f"harness_priority={harness_package.get('priority_claim')} "
        f"artifacts={len(artifacts)}"
    )
    print("sim_supported=" + ",".join(item["task_id"] for item in supported))
    print("unsupported=" + ",".join(item["task_id"] for item in unsupported))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
