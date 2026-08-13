#!/usr/bin/env python3
"""Validate the executed Text2Env empirical gate bundle."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from pose_conditioned_trajectory_policy import read_json, sha256_file, write_json
except ModuleNotFoundError:
    from scripts.pose_conditioned_trajectory_policy import read_json, sha256_file, write_json


ROOT = Path(__file__).resolve().parents[1]
REMOTE_ROOT_MARKER = "/alchedata-self-improving-agents/"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def resolve_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.exists():
        return path.resolve()
    text = str(path)
    if REMOTE_ROOT_MARKER in text:
        candidate = ROOT / text.split(REMOTE_ROOT_MARKER, 1)[1]
        if candidate.exists():
            return candidate.resolve()
    if not path.is_absolute() and (ROOT / path).exists():
        return (ROOT / path).resolve()
    raise FileNotFoundError(f"Evidence path is unavailable: {value}")


def validate_cross_sim_bundle() -> dict[str, Any]:
    contract_path = ROOT / "artifacts/openxsim_cross_sim/place_container_plate_task_contract.json"
    bundle_root = ROOT / "runs/isaac_openxsim_place_container_plate_v1"
    contract = read_json(contract_path)
    require(contract["status"] == "pass_normalized_cross_sim_task_contract", "Cross-sim task contract failed")
    manifest = read_json(bundle_root / "bundle_manifest.json")
    rows = manifest["files"]
    declared = {row["path"]: row for row in rows}
    observed = {
        path.relative_to(bundle_root).as_posix(): path
        for path in bundle_root.rglob("*")
        if path.is_file() and path.name != "bundle_manifest.json"
    }
    require(manifest["status"] == "pass_isaac_command_bundle", "Isaac command bundle failed")
    require(manifest["file_count"] == len(rows) == len(declared) == len(observed) == 34, "Isaac bundle count mismatch")
    require(set(declared) == set(observed), "Isaac bundle file set mismatch")
    for relative, path in observed.items():
        row = declared[relative]
        require(path.stat().st_size == row["bytes"], f"Isaac bundle size mismatch: {relative}")
        require(sha256_file(path) == row["sha256"], f"Isaac bundle hash mismatch: {relative}")

    expected_commands = {
        "/gen-env": "pass_isaac_gen_env",
        "/collect": "pass_isaac_scripted_collection",
        "/evaluate": "pass_isaac_task_verifier",
        "/diagnose": "no_failure_observed",
        "/transfer": "pass_task_semantic_transfer_with_declared_losses",
    }
    run_state = read_json(bundle_root / "run_state.json")
    collect = read_json(bundle_root / "collect.json")
    evaluate = read_json(bundle_root / "evaluate.json")
    transfer = read_json(bundle_root / "transfer.json")
    require(run_state["state"] == "completed" and run_state["commands"] == expected_commands, "Isaac commands mismatch")
    require(run_state["task_success"] is True and run_state["same_task_transfer"] is True, "Isaac task or transfer failed")
    require(collect["step_count"] == 120 and collect["learned_policy"] is False, "Isaac trace contract failed")
    video = collect["video_evidence"]
    require(video["frame_count"] == video["unique_frame_sha256_count"] == 24, "Isaac video evidence failed")
    require(video["endpoint_only"] is False, "Isaac video is endpoint-only")
    require(evaluate["execution_complete"] is True and evaluate["task_success"] is True, "Isaac verifier failed")
    require(all(evaluate["metrics"]["checks"].values()), "Isaac verifier check failed")
    require(transfer["same_normalized_task_contract"] is True, "Cross-sim task contract was not retained")
    require(transfer["target_backend"]["target_verifier_success"] is True, "Transferred target verifier failed")
    source_report = read_json(ROOT / contract["source_backend"]["evidence"])
    require(source_report["check_success"] is True, "RoboTwin source verifier failed")
    return {
        "status": "pass",
        "task_id": contract["task_id"],
        "command_count": len(expected_commands),
        "task_success": True,
        "trace_steps": collect["step_count"],
        "video_frames": video["frame_count"],
        "unique_video_frames": video["unique_frame_sha256_count"],
        "transfer_status": transfer["status"],
    }


def validate_material_bundle(report_path: Path) -> dict[str, Any]:
    report = read_json(report_path)
    bundle_root = report_path.parent
    require(report["status"] == "pass_material_sidecar_roundtrip", "Material roundtrip failed")
    sidecar_path = bundle_root / report["material_sidecar"]["path"]
    import_path = bundle_root / report["isaac_import"]["path"]
    render_path = bundle_root / report["isaac_render"]["path"]
    comparison_path = bundle_root / report["comparison"]["path"]
    for path, row in (
        (sidecar_path, report["material_sidecar"]),
        (import_path, report["isaac_import"]),
        (render_path, report["isaac_render"]),
        (comparison_path, report["comparison"]),
    ):
        require(path.is_file(), f"Missing material evidence: {path}")
        require(path.stat().st_size == row["bytes"], f"Material evidence size mismatch: {path}")
        require(sha256_file(path) == row["sha256"], f"Material evidence hash mismatch: {path}")
    sidecar = read_json(sidecar_path)
    imported = read_json(import_path)
    comparison = read_json(comparison_path)
    require(sidecar["status"] == "pass_observation_material_extraction", "Sidecar extraction failed")
    require(sidecar["foreground_pixel_count"] >= 24, "Source material mask is too small")
    require(imported["status"] == "pass_usd_preview_surface_binding", "Isaac material import failed")
    require(imported["bound"] is True and imported["shader_id"] == "UsdPreviewSurface", "Native material is not bound")
    require(comparison["status"] == "pass_material_roundtrip_comparison", "Material comparison failed")
    require(comparison["acceptance"]["finite_metrics"] is True, "Material metrics are not finite")

    manifest = read_json(bundle_root / "bundle_manifest.json")
    declared = {row["path"]: row for row in manifest["files"]}
    observed = {
        path.relative_to(bundle_root).as_posix(): path
        for path in bundle_root.rglob("*")
        if path.is_file() and path.name != "bundle_manifest.json"
    }
    require(manifest["status"] == "pass_material_roundtrip_bundle", "Material bundle manifest failed")
    require(manifest["file_count"] == len(declared) == len(observed), "Material bundle file count mismatch")
    require(set(declared) == set(observed), "Material bundle file set mismatch")
    for relative, path in observed.items():
        row = declared[relative]
        require(path.stat().st_size == row["bytes"], f"Material manifest size mismatch: {relative}")
        require(sha256_file(path) == row["sha256"], f"Material manifest hash mismatch: {relative}")
    return {
        "status": "pass_material_extraction_import_render_compare",
        "report": str(report_path.relative_to(ROOT)),
        "report_sha256": sha256_file(report_path),
        "source_foreground_pixels": sidecar["foreground_pixel_count"],
        "rendered_foreground_pixels": comparison["metrics"]["rendered_foreground_pixel_count"],
        "rgb_mae": comparison["metrics"]["rgb_mean_absolute_error"],
        "cie76_delta_e": comparison["metrics"]["cie76_delta_e"],
        "shader_id": imported["shader_id"],
    }


def build_audit(
    memory_path: Path,
    material_path: Path,
    correlation_path: Path,
    promotion_path: Path,
    out_path: Path,
) -> dict[str, Any]:
    cross_sim = validate_cross_sim_bundle()
    memory = read_json(memory_path)
    correlation = read_json(correlation_path)
    promotion = read_json(promotion_path)
    material = validate_material_bundle(material_path)

    require(memory["status"] == "pass_matched_memory_ablation", "Memory ablation failed")
    require(memory["experiment"]["only_declared_controller_difference"] is True, "Memory ablation is not matched")
    require(memory["outcomes"]["both_arms_execution_complete"] is True, "Memory ablation did not execute")
    require(correlation["status"] == "pass_predeclared_failure_score_correlation_reported", "Failure-score audit failed")
    require(correlation["sample_count"] == correlation["unique_pose_signature_count"] == 12, "Failure-score sample gate failed")
    require(correlation["all_samples_retained"] is True, "Failure-score samples were filtered")
    require(promotion["status"] == "pass_bounded_pose_conditioned_policy_promotion", "Robust policy promotion failed")
    require(all(gate["status"] == "pass" for gate in promotion["gates"].values()), "SceneAgent promotion gate failed")

    audit = {
        "schema_version": "alchedata.text2env_empirical_audit.v0",
        "status": "pass_all_text2env_empirical_gates",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gates": {
            "same_task_cross_sim_execution": {
                **cross_sim,
                "status": "pass",
                "task_contract": "artifacts/openxsim_cross_sim/place_container_plate_task_contract.json",
                "isaac_bundle": "runs/isaac_openxsim_place_container_plate_v1",
            },
            "memory_ablation": {
                "status": "pass",
                "report": str(memory_path.relative_to(ROOT)),
                "report_sha256": sha256_file(memory_path),
                **memory["outcomes"],
            },
            "material_extraction": {"status": "pass", **material},
            "failure_score_correlation": {
                "status": "pass_negative_result_retained",
                "report": str(correlation_path.relative_to(ROOT)),
                "report_sha256": sha256_file(correlation_path),
                "sample_count": correlation["sample_count"],
                "success_count": correlation["success_count"],
                "failure_count": correlation["failure_count"],
                "metrics": correlation["metrics"],
                "interpretation": correlation["interpretation"],
            },
            "robust_policy_result": {
                "status": "pass_bounded_privileged_policy",
                "report": str(promotion_path.relative_to(ROOT)),
                "report_sha256": sha256_file(promotion_path),
                "decision": promotion["decision"],
                "claim_boundary": promotion["claim_boundary"],
            },
        },
        "claim_boundary": (
            "These gates add executed empirical evidence to the literature review. They do not reproduce the cited "
            "methods, establish state of the art, validate a visual language policy, or turn the negative failure-score "
            "correlation into a usable prioritization signal."
        ),
    }
    write_json(out_path, audit)
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory", required=True)
    parser.add_argument("--material", required=True)
    parser.add_argument("--correlation", required=True)
    parser.add_argument("--promotion", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    audit = build_audit(
        resolve_path(args.memory),
        resolve_path(args.material),
        resolve_path(args.correlation),
        resolve_path(args.promotion),
        Path(args.out).expanduser().resolve(),
    )
    print(json.dumps({"status": audit["status"], "gates": list(audit["gates"]), "out": args.out}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
