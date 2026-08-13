#!/usr/bin/env python3
"""Validate the strict PEARL Open X Sim command-loop package."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jsonschema

try:
    from video_evidence import probe_video
except ModuleNotFoundError:
    from scripts.video_evidence import probe_video


ROOT = Path(__file__).resolve().parents[1]
COMMAND_REGISTRY = Path("artifacts/openxsim/openxsim_command_registry.json")
ADAPTER_MATRIX = Path("artifacts/openxsim/openxsim_adapter_matrix.json")
ACCEPTANCE_AUDIT = Path("artifacts/openxsim/openxsim_acceptance_audit.json")
AGENTICSIM_ISAAC_SNAPSHOT = Path(
    "artifacts/openxsim/agenticsim_awesome_isaac_snapshot.json"
)
VIDEO_FRAME_UNIQUENESS = Path("artifacts/openxsim/openxsim_video_frame_uniqueness.json")
COMMAND_SCHEMA = Path("schemas/openxsim_command_registry.schema.json")
GEN_ENV_SCHEMA = Path("schemas/gen_env.schema.json")
BENCHMARK_MANIFEST = Path("artifacts/openxsim_benchmarks/manifest.json")
FALLBACK_PROBES = (
    Path("artifacts/generation_fallback/blocked_drawer_mug_video2sim_forge.json"),
    Path("artifacts/generation_fallback/blocked_neumatex_material_sidecar.json"),
)
COMMAND_DOC = Path("docs/openxsim_command_spec.md")
REPORT_ROOT = Path("reports/openxsim_command_loop")
ISAAC_COMMAND_BUNDLE = Path("runs/isaac_openxsim_place_container_plate_v1")
CROSS_SIM_TASK_CONTRACT = Path("artifacts/openxsim_cross_sim/place_container_plate_task_contract.json")

REQUIRED_COMMANDS = {
    "/gen-env",
    "/collect",
    "/train",
    "/evaluate",
    "/diagnose",
    "/transfer",
}
REQUIRED_ADAPTER_COLUMNS = {
    "source",
    "engine_renderer",
    "asset_format",
    "material_system",
    "reset_step_verifier_api",
    "license_access",
    "migration_difficulty",
    "current_status",
}
REQUIRED_DENSE_CATEGORIES = {
    "wrong_grasp_location",
    "object_knocked_over",
    "arm_jitter",
    "uncontrolled_gripper_open_close",
    "after_contact_failure",
    "visual_material_mismatch",
}
REQUIRED_IMPORT_GATES = {
    "import",
    "scale",
    "collision",
    "support",
    "material",
    "render",
    "verifier",
}
REQUIRED_BENCHMARKS = {
    "open_laptop",
    "place_mouse_pad",
    "place_container_plate",
}


def read_json(relative_path: Path | str) -> dict:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def resolve_evidence(expression: str) -> list[Path]:
    clean = expression.split("#", 1)[0].rstrip("/")
    if "*" in clean:
        return sorted(ROOT.glob(clean))
    return [ROOT / clean]


def require_evidence(expression: str, context: str) -> None:
    paths = resolve_evidence(expression)
    require(paths, f"{context}: evidence glob matched nothing: {expression}")
    for path in paths:
        require(path.exists(), f"{context}: evidence is missing: {path}")
        if path.is_file():
            require(path.stat().st_size > 0, f"{context}: evidence is empty: {path}")


def validate_manifest(report_root: Path) -> int:
    manifest_path = report_root / "report_manifest.json"
    require(manifest_path.exists(), "Open X Sim report manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(
        manifest.get("status") == "pass_report_bundle",
        "Open X Sim report manifest status mismatch",
    )
    rows = manifest.get("files", [])
    require(
        manifest.get("file_count") == len(rows),
        "Open X Sim report manifest file count mismatch",
    )
    for row in rows:
        path = report_root / row["path"]
        require(path.exists(), f"Open X Sim report manifest path missing: {path}")
        require(
            path.stat().st_size == row["bytes"],
            f"Open X Sim report size mismatch: {path}",
        )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        require(digest == row["sha256"], f"Open X Sim report hash mismatch: {path}")
    return len(rows)


def validate_isaac_command_bundle() -> dict:
    bundle_root = ROOT / ISAAC_COMMAND_BUNDLE
    contract = read_json(CROSS_SIM_TASK_CONTRACT)
    require(contract.get("status") == "pass_normalized_cross_sim_task_contract", "cross-sim task contract failed")
    require(contract.get("task_id") == "openxsim_place_container_plate_v1", "cross-sim task id mismatch")

    manifest_path = bundle_root / "bundle_manifest.json"
    require(manifest_path.is_file(), "Isaac command bundle manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("status") == "pass_isaac_command_bundle", "Isaac command bundle status mismatch")
    rows = manifest.get("files", [])
    require(manifest.get("file_count") == len(rows) == 34, "Isaac command bundle file count mismatch")
    declared = {row["path"]: row for row in rows}
    require(len(declared) == len(rows), "Isaac command bundle contains duplicate paths")
    observed = {
        path.relative_to(bundle_root).as_posix(): path
        for path in bundle_root.rglob("*")
        if path.is_file() and path.name != "bundle_manifest.json"
    }
    require(set(declared) == set(observed), "Isaac command bundle file set mismatch")
    for relative, row in declared.items():
        path = observed[relative]
        require(path.stat().st_size == row["bytes"], f"Isaac bundle size mismatch: {relative}")
        require(hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"], f"Isaac bundle hash mismatch: {relative}")

    run_state = json.loads((bundle_root / "run_state.json").read_text(encoding="utf-8"))
    expected_commands = {
        "/gen-env": "pass_isaac_gen_env",
        "/collect": "pass_isaac_scripted_collection",
        "/evaluate": "pass_isaac_task_verifier",
        "/diagnose": "no_failure_observed",
        "/transfer": "pass_task_semantic_transfer_with_declared_losses",
    }
    require(run_state.get("state") == "completed", "Isaac command bundle did not complete")
    require(run_state.get("commands") == expected_commands, "Isaac command sequence mismatch")
    require(run_state.get("task_success") is True, "Isaac task verifier did not pass")
    require(run_state.get("same_task_transfer") is True, "Isaac same-task transfer flag is false")

    gen_env = json.loads((bundle_root / "gen_env.json").read_text(encoding="utf-8"))
    require(gen_env.get("status") == expected_commands["/gen-env"], "Isaac /gen-env failed")
    require(all(gen_env.get("gates", {}).values()), "Isaac /gen-env gate failed")
    require((bundle_root / gen_env["scene_stage"]["path"]).is_file(), "Isaac USD stage is missing")
    require(gen_env["task_contract"]["sha256"] == hashlib.sha256((ROOT / CROSS_SIM_TASK_CONTRACT).read_bytes()).hexdigest(), "Isaac task contract hash mismatch")

    collect = json.loads((bundle_root / "collect.json").read_text(encoding="utf-8"))
    require(collect.get("status") == expected_commands["/collect"], "Isaac /collect failed")
    require(collect.get("learned_policy") is False, "Isaac scripted collection overclaims learned policy")
    require(collect.get("step_count") == 120, "Isaac trace step count mismatch")
    video = collect.get("video_evidence", {})
    require(video.get("endpoint_only") is False, "Isaac command video is endpoint-only")
    require(video.get("frame_count") == 24, "Isaac command video frame count mismatch")
    require(video.get("unique_frame_sha256_count") == 24, "Isaac command video frames are not unique")
    video_probe = probe_video(bundle_root / collect["video"]["path"])
    require(video_probe["frame_count"] == 24, "Isaac encoded video frame count mismatch")
    require(video_probe["duration_sec"] >= 2, "Isaac encoded video is shorter than two seconds")

    evaluate = json.loads((bundle_root / "evaluate.json").read_text(encoding="utf-8"))
    require(evaluate.get("status") == expected_commands["/evaluate"], "Isaac /evaluate failed")
    require(evaluate.get("execution_complete") is True and evaluate.get("task_success") is True, "Isaac target task failed")
    require(all(evaluate.get("metrics", {}).get("checks", {}).values()), "Isaac target verifier check failed")
    require(evaluate.get("learned_policy") is False, "Isaac target evaluation overclaims learned policy")

    diagnose = json.loads((bundle_root / "diagnose.json").read_text(encoding="utf-8"))
    require(diagnose.get("status") == expected_commands["/diagnose"], "Isaac /diagnose failed")
    require(REQUIRED_DENSE_CATEGORIES == set(diagnose.get("categories", {})), "Isaac diagnosis category set mismatch")

    transfer = json.loads((bundle_root / "transfer.json").read_text(encoding="utf-8"))
    require(transfer.get("status") == expected_commands["/transfer"], "Isaac /transfer failed")
    require(transfer.get("same_normalized_task_contract") is True, "Isaac transfer does not bind one task contract")
    require(transfer.get("target_backend", {}).get("target_verifier_success") is True, "Isaac transfer target verifier failed")
    fidelities = {row["field"]: row["fidelity"] for row in transfer.get("mappings", [])}
    require(fidelities.get("task relation") == "exact_relation", "Isaac task relation transfer mismatch")
    for field in ("action interface", "materials", "robot embodiment"):
        require(fidelities.get(field) == "not_transferred", f"Isaac transfer hides an unmapped surface: {field}")

    source_report = read_json(Path(contract["source_backend"]["evidence"]))
    require(source_report.get("check_success") is True, "source RoboTwin verifier did not pass")
    return {
        "status": "pass_isaac_full_command_bundle",
        "task_id": contract["task_id"],
        "command_count": len(expected_commands),
        "task_success": evaluate["task_success"],
        "trace_steps": collect["step_count"],
        "video_frames": video["frame_count"],
        "unique_video_frames": video["unique_frame_sha256_count"],
        "transfer_status": transfer["status"],
    }


def validate_openxsim_package(*, require_report: bool = True) -> dict:
    schema = read_json(COMMAND_SCHEMA)
    registry = read_json(COMMAND_REGISTRY)
    matrix = read_json(ADAPTER_MATRIX)
    audit = read_json(ACCEPTANCE_AUDIT)
    agenticsim_isaac = read_json(AGENTICSIM_ISAAC_SNAPSHOT)
    video_uniqueness = read_json(VIDEO_FRAME_UNIQUENESS)
    isaac_command_bundle = validate_isaac_command_bundle()

    require(
        video_uniqueness["status"] == "pass_all_decoded_frames_unique",
        "Open X Sim video uniqueness status mismatch",
    )
    video_uniqueness_rows = video_uniqueness["videos"]
    require(
        len(video_uniqueness_rows) == 4,
        "Open X Sim video uniqueness row count mismatch",
    )
    require(
        all(
            row["all_decoded_frames_unique"]
            and row["decoded_frame_count"] == row["unique_decoded_frame_hash_count"]
            and row["decoded_frame_count"] > 2
            for row in video_uniqueness_rows
        ),
        "Open X Sim report contains repeated endpoint-only video evidence",
    )

    require(
        agenticsim_isaac["status"] == "pass_agenticsim_awesome_isaac_snapshot",
        "AgenticSim Awesome Isaac snapshot status mismatch",
    )
    require(
        len(agenticsim_isaac["source_commit"]) == 40,
        "AgenticSim source commit is not pinned",
    )
    require(
        agenticsim_isaac["catalog_summary"]["repository_count"] == 745,
        "Awesome Isaac catalog count mismatch",
    )
    require(
        agenticsim_isaac["catalog_summary"]["verified_open_source_count"] == 308,
        "Awesome Isaac open-source count mismatch",
    )
    require(
        agenticsim_isaac["runtime_summary"]["repository_probe_count"] == 12,
        "Isaac candidate probe count mismatch",
    )
    require(
        agenticsim_isaac["runtime_summary"]["runtime_pass_count"] == 11,
        "Isaac candidate pass count mismatch",
    )
    require(
        agenticsim_isaac["runtime_summary"]["runtime_blocked_count"] == 1,
        "Isaac candidate block count mismatch",
    )
    require(
        agenticsim_isaac["runtime_summary"]["strict_open_source_runtime_pass_count"]
        == 6,
        "Isaac strict open-source runtime closure count mismatch",
    )
    require(
        agenticsim_isaac["runtime_summary"][
            "runtime_pass_without_open_source_closure_count"
        ]
        == 5,
        "Isaac strict open-source provenance gap count mismatch",
    )
    require(
        agenticsim_isaac["usage_policy"]["policy_id"]
        == "noncommercial_academic_local_use",
        "Isaac academic-use policy mismatch",
    )
    require(
        agenticsim_isaac["runtime_summary"]["academic_use_runtime_accepted_count"]
        == 11,
        "Isaac academic-use admission count mismatch",
    )
    require(
        agenticsim_isaac["runtime_summary"]["academic_use_runtime_blocked_count"] == 1,
        "Isaac academic-use runtime blocker count mismatch",
    )
    require(
        agenticsim_isaac["runtime_summary"]["academic_use_license_advisory_count"] == 5,
        "Isaac academic-use license advisory count mismatch",
    )
    baseline = agenticsim_isaac["runtime_baseline"]
    require(all(baseline["gates"].values()), "RTX Isaac baseline gate failed")
    require(
        baseline["video_evidence"]["frame_count"] == 32,
        "RTX Isaac baseline frame count mismatch",
    )
    require(
        baseline["video_evidence"]["unique_frame_sha256_count"] == 32,
        "RTX Isaac baseline video is not frame-unique",
    )

    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(registry, schema)
    commands = registry["commands"]
    require(
        {row["command"] for row in commands} == REQUIRED_COMMANDS,
        "Open X Sim command set mismatch",
    )
    for command in commands:
        for evidence in command["current_evidence"]:
            require_evidence(evidence, command["command"])

    doc = (ROOT / COMMAND_DOC).read_text(encoding="utf-8")
    for command in REQUIRED_COMMANDS:
        require(f"## {command}" in doc, f"command document missing section: {command}")
    require("Failure codes:" in doc, "command document has no failure-code sections")
    for code in next(row for row in commands if row["command"] == "/diagnose")[
        "failure_codes"
    ]:
        require(
            f"`{code}`" in doc,
            f"command document missing /diagnose failure code: {code}",
        )
    for code in next(row for row in commands if row["command"] == "/transfer")[
        "failure_codes"
    ]:
        require(
            f"`{code}`" in doc,
            f"command document missing /transfer failure code: {code}",
        )

    require(
        matrix["status"] == "pass_adapter_matrix_complete",
        "adapter matrix status mismatch",
    )
    require(
        set(matrix["required_columns"]) == REQUIRED_ADAPTER_COLUMNS,
        "adapter matrix declared columns mismatch",
    )
    adapters = matrix["adapters"]
    require(
        matrix["adapter_count"] == len(adapters) == 10,
        "adapter matrix must contain ten rows",
    )
    require(
        len({row["adapter_id"] for row in adapters}) == len(adapters),
        "adapter ids are not unique",
    )
    for adapter in adapters:
        require(
            REQUIRED_ADAPTER_COLUMNS.issubset(adapter),
            f"{adapter['adapter_id']}: adapter columns incomplete",
        )
        require(
            all(adapter[column] for column in REQUIRED_ADAPTER_COLUMNS),
            f"{adapter['adapter_id']}: blank adapter field",
        )
        if adapter["source_url"] is not None:
            require(
                adapter["source_url"].startswith("https://"),
                f"{adapter['adapter_id']}: non-HTTPS source URL",
            )
        for evidence in adapter["evidence"]:
            require_evidence(evidence, adapter["adapter_id"])
    statuses = {row["current_status"] for row in adapters}
    require(
        any(status.startswith("P0") for status in statuses),
        "adapter matrix lacks a P0 row",
    )
    require(
        any(status.startswith("P1") for status in statuses),
        "adapter matrix lacks a P1 row",
    )
    require(
        any(status.startswith("P2") for status in statuses),
        "adapter matrix lacks a P2 row",
    )

    require(
        audit["status"] == "pass_openxsim_command_loop_acceptance",
        "Open X Sim acceptance status mismatch",
    )
    items = audit["items"]
    require(
        audit["acceptance_count"] == len(items) == 8,
        "Open X Sim acceptance count must be eight",
    )
    require(
        [item["id"] for item in items] == list(range(1, 9)),
        "Open X Sim acceptance ids must be 1-8",
    )
    require(
        all(item["status"] == "pass" for item in items),
        "not all Open X Sim acceptance items pass",
    )
    for item in items:
        for evidence in item["evidence"]:
            require_evidence(evidence, f"acceptance {item['id']}")
    require(
        set(audit["dense_diagnosis_categories"]) == REQUIRED_DENSE_CATEGORIES,
        "dense diagnosis list mismatch",
    )

    gen_env = read_json(GEN_ENV_SCHEMA)
    jsonschema.Draft202012Validator.check_schema(gen_env)
    defs = gen_env["$defs"]
    required_branch_fields = {
        "selection2env_outputs": {
            "asset_candidates",
            "selected_assets",
            "placement_regions",
            "support_surface",
            "pose_constraints",
            "camera_observation",
            "robot_constraints",
            "success_verifier",
        },
        "forge_fallback_outputs": {
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
        },
        "material_sidecar_outputs": {
            "textures",
            "materials",
            "albedo",
            "specular_latent",
            "uncertainty",
            "lighting_assumption",
            "renderer_binding",
            "material_provenance",
            "render_gate",
        },
    }
    for name, fields in required_branch_fields.items():
        require(
            fields.issubset(defs[name]["required"]),
            f"/gen-env schema branch incomplete: {name}",
        )

    for probe_path in FALLBACK_PROBES:
        probe = read_json(probe_path)
        require(
            probe["status"].startswith("blocked_"),
            f"fallback probe must remain blocked: {probe_path}",
        )
        require(
            set(probe["import_gate_matrix"]) == REQUIRED_IMPORT_GATES,
            f"fallback gate matrix incomplete: {probe_path}",
        )
        require(
            all(
                row["status"].startswith("blocked_")
                for row in probe["import_gate_matrix"].values()
            ),
            f"fallback probe hides an unexecuted gate: {probe_path}",
        )

    benchmark_manifest = read_json(BENCHMARK_MANIFEST)
    require(
        benchmark_manifest["benchmark_count"] == 3,
        "Open X Sim benchmark count mismatch",
    )
    require(
        {row["task_name"] for row in benchmark_manifest["benchmarks"]}
        == REQUIRED_BENCHMARKS,
        "benchmark task set mismatch",
    )
    for row in benchmark_manifest["benchmarks"]:
        bundle = read_json(Path(row["manifest"]))
        require(
            bundle["status"] == "pass_openxsim_scripted_benchmark_bundle",
            f"{row['task_name']}: benchmark did not pass",
        )
        require(
            bundle["command_loop"]["/evaluate"]["learned_policy"] is False,
            f"{row['task_name']}: benchmark claim boundary mismatch",
        )
        video_capture = bundle.get("video_capture", {})
        require(
            video_capture.get("endpoint_only") is False,
            f"{row['task_name']}: video is endpoint-only",
        )
        require(
            video_capture.get("frame_count", 0) >= 24,
            f"{row['task_name']}: video has fewer than 24 frames",
        )
        require(
            video_capture.get("duration_sec", 0) >= 2,
            f"{row['task_name']}: video is shorter than 2 seconds",
        )
        for artifact in bundle["artifacts"].values():
            require_evidence(artifact, row["task_name"])
        run_state = read_json(Path(bundle["artifacts"]["run_state"]))
        require(
            run_state["current_stage"] == "/evaluate",
            f"{row['task_name']}: run_state is incomplete",
        )
        require(
            run_state["next_data_requirement"],
            f"{row['task_name']}: next data requirement missing",
        )
        diagnosis = read_json(Path(bundle["artifacts"]["failure_diagnosis"]))
        require(
            REQUIRED_DENSE_CATEGORIES.issubset(diagnosis["checked_categories"]),
            f"{row['task_name']}: dense diagnosis incomplete",
        )
        encoded_video = probe_video(ROOT / bundle["artifacts"]["observer_video"])
        require(
            encoded_video["frame_count"] == video_capture["frame_count"],
            f"{row['task_name']}: encoded/report frame count mismatch",
        )
        require(
            encoded_video["duration_sec"] >= 2,
            f"{row['task_name']}: encoded video is shorter than 2 seconds",
        )

    report_file_count = 0
    if require_report:
        report_root = ROOT / REPORT_ROOT
        for relative in (
            "index.html",
            "openxsim_command_loop.md",
            "assets/command_registry.json",
            "assets/adapter_matrix.json",
        ):
            require_evidence(str(REPORT_ROOT / relative), "Open X Sim report")
        delivery = audit["delivery"]
        benchmark_images = sorted(
            (report_root / "assets" / "benchmark_frames").glob("*.png")
        )
        benchmark_videos = sorted(
            (report_root / "assets" / "benchmark_videos").glob("*.mp4")
        )
        source_pages = sorted((report_root / "assets" / "source_pages").glob("*.png"))
        isaac_intake = report_root / "assets" / "isaac_intake"
        isaac_candidate_evidence = isaac_intake / "candidate_evidence"
        isaac_runtime_rows = agenticsim_isaac["current_runtime_rows"]
        isaac_candidate_images = [
            isaac_candidate_evidence / Path(row["artifacts"]["screenshot"]["path"]).name
            for row in isaac_runtime_rows
            if row["runtime_passed"]
        ]
        isaac_baseline_images = sorted(isaac_intake.glob("baseline_*.*g"))
        isaac_baseline_video = isaac_intake / "baseline_motion.mp4"
        require(
            len(benchmark_images) == delivery["benchmark_screenshot_count"],
            "benchmark screenshot count mismatch",
        )
        require(
            len(benchmark_videos) == delivery["benchmark_video_count"],
            "benchmark video count mismatch",
        )
        for video in benchmark_videos:
            encoded_video = probe_video(video)
            require(
                encoded_video["frame_count"] >= 24,
                f"report video has fewer than 24 frames: {video}",
            )
            require(
                encoded_video["duration_sec"] >= 2,
                f"report video is shorter than 2 seconds: {video}",
            )
        for row in video_uniqueness_rows:
            video_path = report_root / row["report_path"]
            require(video_path.is_file(), f"uniqueness video is missing: {video_path}")
            require(
                video_path.stat().st_size == row["bytes"],
                f"uniqueness video size mismatch: {video_path}",
            )
            require(
                hashlib.sha256(video_path.read_bytes()).hexdigest() == row["sha256"],
                f"uniqueness video hash mismatch: {video_path}",
            )
            encoded_video = probe_video(video_path)
            require(
                encoded_video["frame_count"] == row["decoded_frame_count"],
                f"uniqueness video frame count mismatch: {video_path}",
            )
        require(
            len(source_pages) == delivery["reference_page_screenshot_count"],
            "reference screenshot count mismatch",
        )
        require(
            len(isaac_candidate_images) == delivery["isaac_candidate_screenshot_count"],
            "Isaac candidate screenshot count mismatch",
        )
        require(
            all(path.is_file() for path in isaac_candidate_images),
            "Isaac candidate screenshot is missing",
        )
        require(
            len(isaac_baseline_images) == delivery["isaac_baseline_image_count"],
            "Isaac baseline image count mismatch",
        )
        require(
            isaac_baseline_video.is_file(), "Isaac baseline report video is missing"
        )
        isaac_video_probe = probe_video(isaac_baseline_video)
        require(
            isaac_video_probe["frame_count"] == 32,
            "Isaac baseline report video frame count mismatch",
        )
        require(
            isaac_video_probe["duration_sec"] >= 2,
            "Isaac baseline report video is too short",
        )
        report_runtime = json.loads(
            (isaac_intake / "runtime_evidence.json").read_text(encoding="utf-8")
        )
        require(
            report_runtime["summary"] == agenticsim_isaac["runtime_summary"],
            "Bundled runtime evidence drifted",
        )
        report_file_count = validate_manifest(report_root)

    return {
        "status": "pass_openxsim_command_loop_package",
        "acceptance_items": len(items),
        "commands": len(commands),
        "adapters": len(adapters),
        "benchmarks": benchmark_manifest["benchmark_count"],
        "dense_categories": len(REQUIRED_DENSE_CATEGORIES),
        "fallback_gate_rows": len(FALLBACK_PROBES) * len(REQUIRED_IMPORT_GATES),
        "isaac_runtime_passes": agenticsim_isaac["runtime_summary"][
            "runtime_pass_count"
        ],
        "isaac_runtime_blocked": agenticsim_isaac["runtime_summary"][
            "runtime_blocked_count"
        ],
        "isaac_academic_use_accepted": agenticsim_isaac["runtime_summary"][
            "academic_use_runtime_accepted_count"
        ],
        "isaac_license_advisories": agenticsim_isaac["runtime_summary"][
            "academic_use_license_advisory_count"
        ],
        "video_unique_decoded_frames": sum(
            row["unique_decoded_frame_hash_count"] for row in video_uniqueness_rows
        ),
        "report_file_count": report_file_count,
        "isaac_command_bundle": isaac_command_bundle,
    }


def main() -> int:
    print(json.dumps(validate_openxsim_package(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
