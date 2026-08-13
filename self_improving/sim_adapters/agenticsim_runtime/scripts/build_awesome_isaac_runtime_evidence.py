#!/usr/bin/env python3
"""Build compact, hash-verified evidence for current Awesome Isaac runtime probes."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "artifacts" / "awesome_isaac" / "5090_runtime_baseline"
DEFAULT_OUTPUT = ROOT / "docs" / "awesome_isaac_runtime_evidence.json"


CANDIDATES = (
    {
        "slug": "enactic/openarm_isaac_lab",
        "head_oid": "bad82e23716e6941c2de78ccb978f57c78b37734",
        "license_findings": {
            "github_detected_spdx": "Apache-2.0",
            "status": "recognized_open_source_license",
        },
        "task": "Isaac-Reach-OpenArm-Play-v0",
        "report": "candidates/openarm_reach_smoke.json",
        "screenshot": "candidates/openarm_reach_smoke.png",
        "runtime_status": "passed_with_repository_root_pythonpath",
        "open_source_runtime_verdict": "usable_open_source_with_conditions",
        "required_asset_license_status": "covered_by_repository_license",
        "source_tree_modified": False,
        "visual_evidence_status": "accepted",
        "conditions": [
            "Install source/openarm as an editable package.",
            "Add the repository root to PYTHONPATH because candidate modules import source.openarm paths.",
        ],
        "remaining_blockers": [
            "Package-only execution is not portable until repository-root imports are removed upstream."
        ],
    },
    {
        "slug": "neuromeka-robotics/nrmk_isaaclab_public",
        "head_oid": "8284fb3cccdeafbf25ba313291333b4cc70b1348",
        "license_findings": {
            "github_detected_spdx": "Apache-2.0",
            "status": "recognized_open_source_license",
        },
        "task": "Indy-Deploy",
        "report": "candidates/nrmk_indy_deploy_smoke.json",
        "screenshot": "candidates/nrmk_indy_deploy_smoke.png",
        "runtime_status": "passed_after_dependency_and_asset_repair",
        "open_source_runtime_verdict": "usable_open_source_with_conditions",
        "required_asset_license_status": "covered_by_repository_license",
        "source_tree_modified": False,
        "visual_evidence_status": "accepted",
        "conditions": [
            "Set PYNPUT_BACKEND=dummy for headless execution.",
            "Install the undeclared runtime dependencies tensordict and rsl-rl-lib==3.0.1.",
            "Materialize the Indy7 Git LFS object before task creation.",
        ],
        "remaining_blockers": [
            "The package metadata does not declare tensordict or rsl-rl-lib.",
            "Other tasks still require their own Git LFS assets and hardcoded-path review.",
        ],
        "failure_artifacts": (
            {
                "label": "missing_tensordict",
                "category": "dependency",
                "path": "candidates/nrmk_indy_deploy_smoke.missing_tensordict.json",
            },
            {
                "label": "missing_rsl_rl",
                "category": "dependency",
                "path": "candidates/nrmk_indy_deploy_smoke.missing_rsl_rl.json",
            },
        ),
        "selected_asset": {
            "path": "isaac_neuromeka/assets/model/usd/indy7/indy7.usd",
            "git_lfs_oid": "sha256:b1c2dfd4003980c07ab02dde2951e6478d55fd771f5e479dc3c241b686c44fae",
            "size_bytes": 12_851_047,
        },
    },
    {
        "slug": "noxrick91/WobbleGo",
        "head_oid": "061fb498c8b4a52a8f21af733b2ebd2447f397b9",
        "license_findings": {
            "github_detected_spdx": "MIT",
            "package_declared_spdx": "Apache-2.0",
            "source_header_spdx": "BSD-3-Clause",
            "status": "recognized_open_source_license_with_inconsistent_metadata",
        },
        "task": "WobbleGo-Direct-v0",
        "report": "candidates/wobblego_smoke.json",
        "screenshot": None,
        "runtime_status": "blocked_external_core_asset_unavailable",
        "open_source_runtime_verdict": "not_runtime_usable_core_asset_unavailable",
        "required_asset_license_status": "unavailable_and_unverified",
        "source_tree_modified": False,
        "visual_evidence_status": "not_captured",
        "conditions": [],
        "remaining_blockers": [
            "The core WobbleGo USD is not present in the repository and the configured remote URL did not resolve in Isaac Sim."
        ],
        "external_core_asset": "https://data.noxcaw.com/downloads/wobble-go/usds/WobbleGo.usd",
    },
    {
        "slug": "fan-ziqi/robot_lab",
        "head_oid": "500399ed75f510aeaff28705a8ce736c514dbec3",
        "license_findings": {
            "github_detected_spdx": "Apache-2.0",
            "bundled_go2_asset_provenance": "unitreerobotics/unitree_ros",
            "status": "recognized_open_source_license_bundled_asset_provenance_not_independently_licensed",
        },
        "task": "RobotLab-Isaac-Velocity-Flat-Unitree-Go2-v0",
        "report": "candidates/robot_lab_go2_smoke.json",
        "screenshot": "candidates/robot_lab_go2_smoke.png",
        "runtime_status": "passed_after_declared_dependency_install",
        "open_source_runtime_verdict": "runtime_usable_repository_license_recognized_asset_provenance_review_remaining",
        "required_asset_license_status": "bundled_under_repository_license_no_separate_asset_notice_detected",
        "source_tree_modified": False,
        "visual_evidence_status": "accepted_robot_fell_under_zero_action",
        "conditions": [
            "Install the declared but unpinned cusrl dependency; the bounded probe used cusrl==1.2.0 without the all extra.",
        ],
        "remaining_blockers": [
            "The bundled Go2 meshes cite unitree_ros provenance but do not carry a separate asset license notice.",
            "The zero-action screenshot proves composition and physics, not a trained locomotion policy.",
        ],
        "failure_artifacts": (
            {
                "label": "missing_cusrl",
                "category": "dependency",
                "path": "candidates/robot_lab_go2_smoke.missing_cusrl.json",
            },
        ),
        "evidence_artifacts": {
            "cusrl_install_report": "candidates/robot_lab_cusrl_core_install_report.json",
            "cusrl_all_dry_run": "candidates/robot_lab_cusrl_dry_run.log",
        },
    },
    {
        "slug": "unitreerobotics/unitree_rl_lab",
        "head_oid": "4960b84732b0c2ec593dccbfe963fda1bcd7b1e3",
        "license_findings": {
            "github_detected_spdx": "Apache-2.0",
            "required_asset_dataset": "unitreerobotics/unitree_model",
            "asset_dataset_head_oid": "323e350252b9c3aee9c40acbfdad84f6ce46a5ac",
            "asset_dataset_license": None,
            "asset_dataset_card_metadata": None,
            "status": "code_open_source_required_asset_license_unverified",
        },
        "task": "Unitree-Go2-Velocity",
        "report": "candidates/unitree_rl_lab_go2_smoke.runtime_framed.json",
        "screenshot": "candidates/unitree_rl_lab_go2_smoke.runtime_framed.png",
        "runtime_status": "passed_with_external_asset_dataset",
        "open_source_runtime_verdict": "runtime_usable_but_required_asset_license_unverified",
        "required_asset_license_status": "no_license_detected",
        "asset_license_gap": True,
        "source_tree_modified": False,
        "visual_evidence_status": "accepted_runtime_root_framed",
        "conditions": [
            "Download the public, non-gated unitreerobotics/unitree_model dataset and configure UNITREE_MODEL_DIR.",
            "Materialize the Go2 Git LFS object before task creation.",
            "Frame the camera from robot.data.root_pos_w because terrain placement moved env_0 about 79 meters from the authored USD origin.",
        ],
        "remaining_blockers": [
            "The required unitree_model dataset exposes neither license metadata nor a license file, so redistribution rights are unverified.",
            "UNITREE_MODEL_DIR remains a literal source placeholder rather than an environment variable or package setting.",
        ],
        "failure_artifacts": (
            {
                "label": "missing_external_asset",
                "category": "asset",
                "path": "candidates/unitree_rl_lab_go2_smoke.missing_external_asset.json",
            },
        ),
        "evidence_artifacts": {
            "go2_asset_hashes": "candidates/unitree_model_go2_sha256.txt",
            "authored_origin_blank_capture": "candidates/unitree_rl_lab_go2_smoke.framed.json",
        },
        "selected_asset": {
            "dataset": "unitreerobotics/unitree_model",
            "dataset_head_oid": "323e350252b9c3aee9c40acbfdad84f6ce46a5ac",
            "path": "Go2/usd/configuration/go2_description_base.usd",
            "git_lfs_oid": "sha256:eeed42fb9e41395c47bc3fac7ff5d58625ac62f44bfa5a09cacebff83229c7a0",
            "size_bytes": 18_806_122,
        },
    },
    {
        "slug": "liorbenhorin/lerobot_so101_teleop",
        "head_oid": "9ea18b2c77df29e05c1c45bb1915dfd95514c470",
        "license_findings": {
            "github_detected_spdx": "MIT",
            "attributed_so101_asset_source": "MuammerBay/so-arm101-ros2-bridge",
            "attributed_asset_source_license": None,
            "attributed_asset_source_license_files": [],
            "status": "code_open_source_attributed_core_asset_license_unverified",
        },
        "task": "Lerobot-So101-Teleop-Base",
        "report": "candidates/lerobot_so101_base_smoke.reframed.json",
        "screenshot": "candidates/lerobot_so101_base_smoke.reframed.png",
        "runtime_status": "passed_after_git_lfs_materialization",
        "open_source_runtime_verdict": "runtime_usable_but_attributed_core_asset_license_unverified",
        "required_asset_license_status": "upstream_no_license_detected",
        "asset_license_gap": True,
        "source_tree_modified": False,
        "visual_evidence_status": "accepted_native_viewer",
        "conditions": [
            "Materialize all 13 Git LFS assets and add the repository source directory to PYTHONPATH.",
            "Use the task-native viewer pose; a generic wide pose did not frame the arm.",
        ],
        "remaining_blockers": [
            "The repository attributes the SO101 USD to MuammerBay/so-arm101-ros2-bridge, which has no detected license or license file.",
        ],
        "evidence_artifacts": {
            "lfs_file_list": "candidates/lerobot_so101_lfs_files.txt",
            "lfs_fsck": "candidates/lerobot_so101_lfs_fsck.log",
        },
    },
    {
        "slug": "lehome-official/lehome-challenge",
        "head_oid": "a805ad2f7ab52a4583066fc4ee5180459a7f9d15",
        "license_findings": {
            "github_detected_spdx": "Apache-2.0",
            "required_asset_dataset": "lehome/asset_challenge",
            "asset_dataset_head_oid": "bea65fd960ad5a1bb3bd3fa77164b28001c08ef9",
            "asset_dataset_license": "Apache-2.0",
            "status": "recognized_open_source_code_and_required_asset_license",
        },
        "task": "LeHome-BiSO101-Direct-Garment-v2",
        "report": "candidates/lehome_biso101_garment_v2_smoke.json",
        "screenshot": "candidates/lehome_biso101_garment_v2_smoke.png",
        "runtime_status": "passed_after_compatibility_patch",
        "open_source_runtime_verdict": "open_source_code_and_assets_runtime_usable_after_source_patch",
        "required_asset_license_status": "apache_2_0",
        "source_tree_modified": True,
        "visual_evidence_status": "accepted",
        "conditions": [
            "Download and materialize the public Apache-2.0 lehome/asset_challenge dataset.",
            "Install plotly==6.5.2, pyserial==3.5, and deepdiff==8.6.1 without the full lerobot dependency set.",
            "Set cfg.garment_name=Top_Long_Unseen_0 before environment creation.",
            "Apply the recorded two-line GarmentObject first-reset compatibility patch.",
        ],
        "remaining_blockers": [
            "Upstream reset calls GarmentObject.reset before initialize on Isaac Sim 5.1; unpatched CUDA execution fails.",
            "Three of four registered task IDs reference Python modules absent from this commit; only the v2 task source is present.",
            "A full lerobot==0.4.3 install would change core NumPy, packaging, and protobuf versions and was deliberately not applied.",
        ],
        "failure_artifacts": (
            {
                "label": "missing_plotly",
                "category": "dependency",
                "path": "candidates/lehome_biso101_garment_v2_smoke.missing_plotly.json",
            },
            {
                "label": "missing_pyserial",
                "category": "dependency",
                "path": "candidates/lehome_biso101_garment_v2_smoke.missing_pyserial.json",
            },
            {
                "label": "missing_deepdiff",
                "category": "dependency",
                "path": "candidates/lehome_biso101_garment_v2_smoke.missing_deepdiff.json",
            },
            {
                "label": "unpatched_cuda_reset",
                "category": "source_compatibility",
                "path": "candidates/lehome_biso101_garment_v2_smoke.unpatched_cuda.json",
            },
            {
                "label": "registered_task_module_missing",
                "category": "registration",
                "path": "candidates/lehome_so101_garment_smoke.json",
            },
        ),
        "evidence_artifacts": {
            "asset_lfs_fsck": "candidates/lehome_assets_lfs_fsck.log",
            "plotly_install_report": "candidates/lehome_plotly_install_report.json",
            "pyserial_install_report": "candidates/lehome_pyserial_install_report.json",
            "deepdiff_install_report": "candidates/lehome_deepdiff_install_report.json",
            "lerobot_dry_run": "candidates/lehome_lerobot_dry_run.log",
        },
        "compatibility_patches": (
            {
                "name": "initialize_garment_before_first_reset",
                "reason": "DirectRLEnv invokes reset before GarmentObject.initialize has populated initial_points_positions.",
                "tracked_path": "docs/awesome_isaac_patches/lehome_garment_initial_reset.patch",
                "runtime_path": "candidates/lehome_initial_reset_patch.diff",
            },
        ),
        "registration_findings": {
            "registered_task_count": 4,
            "task_source_present_count": 1,
            "tested_present_task": "LeHome-BiSO101-Direct-Garment-v2",
        },
    },
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def build_baseline(raw_root: Path) -> dict[str, Any]:
    report_path = raw_root / "isaac_runtime_visual_smoke_continuous.json"
    report = read_json(report_path)
    if report.get("status") != "passed":
        raise RuntimeError(
            f"Isaac runtime baseline did not pass: {report.get('status')}"
        )

    video = report.get("video_evidence") or {}
    sequence = video.get("sequence") or {}
    poses = video.get("poses") or {}
    encode = video.get("encode") or {}
    screenshot_path = raw_root / "isaac_runtime_visual_smoke_continuous.png"
    video_path = raw_root / "isaac_runtime_visual_smoke_continuous.mp4"
    contact_sheet_path = raw_root / "isaac_runtime_visual_smoke_contact_sheet.jpg"
    screenshot = artifact(screenshot_path)
    video_artifact = artifact(video_path)
    expected_artifacts = report.get("artifacts") or {}
    for name, current in (("screenshot", screenshot), ("video", video_artifact)):
        expected = expected_artifacts.get(name) or {}
        if (
            expected.get("sha256") != current["sha256"]
            or expected.get("size_bytes") != current["size_bytes"]
        ):
            raise RuntimeError(
                f"Pulled {name} does not match the remote runtime report"
            )

    frame_count = int(encode.get("frame_count") or 0)
    fps = float(encode.get("fps") or 0.0)
    return {
        "status": "passed",
        "host": report.get("hostname"),
        "gpu": report.get("nvidia_smi"),
        "platform": report.get("platform"),
        "python": report.get("python"),
        "isaac_sim": report.get("isaacsim_version"),
        "torch": report.get("torch"),
        "steps_completed": report.get("steps_completed"),
        "gates": {
            "physics": bool(report.get("physics_passed")),
            "render": bool(report.get("render_passed")),
            "torch": bool(report.get("torch_passed")),
            "video": bool(report.get("video_passed")),
        },
        "video_evidence": {
            "frame_count": frame_count,
            "unique_frame_sha256_count": sequence.get("unique_sha256_count"),
            "pose_movement_transition_count": poses.get("movement_transition_count"),
            "unique_position_count": poses.get("unique_position_count_at_1e_4m"),
            "fps": fps,
            "duration_seconds": round(frame_count / fps, 6) if fps else None,
            "resolution": [
                report.get("image", {}).get("width"),
                report.get("image", {}).get("height"),
            ],
        },
        "artifacts": {
            "report": artifact(report_path),
            "screenshot": screenshot,
            "video": video_artifact,
            "contact_sheet": artifact(contact_sheet_path),
        },
        "finished_at": report.get("finished_at"),
    }


def build_candidate(raw_root: Path, config: dict[str, Any]) -> dict[str, Any]:
    report_path = raw_root / str(config["report"])
    report = read_json(report_path)
    runtime_status = str(config["runtime_status"])
    expected_pass = runtime_status.startswith("passed_")
    captured = report.get("status") == "captured"
    if captured != expected_pass:
        raise RuntimeError(
            f"Runtime result mismatch for {config['slug']}: status={report.get('status')} expected_pass={expected_pass}"
        )

    artifacts: dict[str, Any] = {"report": artifact(report_path)}
    if config.get("screenshot"):
        screenshot_path = raw_root / str(config["screenshot"])
        artifacts["screenshot"] = artifact(screenshot_path)
        if (
            not report.get("screenshot_ready")
            or report.get("screenshot_size_bytes")
            != artifacts["screenshot"]["size_bytes"]
        ):
            raise RuntimeError(f"Screenshot evidence mismatch for {config['slug']}")

    failure_artifacts = []
    for failure_config in config.get("failure_artifacts", ()):
        failure_path = raw_root / failure_config["path"]
        failure = read_json(failure_path)
        failure_artifacts.append(
            {
                "label": failure_config["label"],
                "category": failure_config["category"],
                "error_type": failure.get("error_type"),
                "error_message": failure.get("error_message"),
                "artifact": artifact(failure_path),
            }
        )

    evidence_artifacts = {
        name: artifact(raw_root / relative)
        for name, relative in (config.get("evidence_artifacts") or {}).items()
    }
    compatibility_patches = []
    for patch_config in config.get("compatibility_patches", ()):
        tracked = artifact(ROOT / patch_config["tracked_path"])
        runtime = artifact(raw_root / patch_config["runtime_path"])
        if tracked["sha256"] != runtime["sha256"]:
            raise RuntimeError(
                f"Tracked and runtime patch differ for {config['slug']}: {patch_config['name']}"
            )
        compatibility_patches.append(
            {
                "name": patch_config["name"],
                "reason": patch_config["reason"],
                "tracked_artifact": tracked,
                "runtime_artifact": runtime,
            }
        )

    visual_evidence_status = str(config.get("visual_evidence_status") or "unreviewed")
    visual_evidence_accepted = captured and visual_evidence_status.startswith("accepted")

    return {
        "slug": config["slug"],
        "head_oid": config["head_oid"],
        "license_findings": config["license_findings"],
        "task": config["task"],
        "runtime_status": runtime_status,
        "runtime_passed": captured,
        "open_source_runtime_verdict": config["open_source_runtime_verdict"],
        "required_asset_license_status": config["required_asset_license_status"],
        "asset_license_gap": bool(config.get("asset_license_gap")),
        "source_tree_modified": bool(config.get("source_tree_modified")),
        "reset_passed": captured and "reset_info" in report,
        "steps_requested": report.get("steps_requested"),
        "steps_completed": report.get("steps_completed", 0),
        "render_passed": bool(report.get("screenshot_ready")),
        "visual_evidence_status": visual_evidence_status,
        "visual_evidence_accepted": visual_evidence_accepted,
        "stage_ready": bool((report.get("stage") or {}).get("ready")),
        "stage_match_count": len((report.get("stage") or {}).get("matches") or []),
        "camera_pose": report.get("camera_pose"),
        "framed_prim": report.get("framed_prim"),
        "framed_asset": report.get("framed_asset"),
        "env_cfg_overrides": report.get("env_cfg_overrides") or [],
        "action_space": report.get("action_space"),
        "observation_space": report.get("observation_space"),
        "error": (
            {"type": report.get("error_type"), "message": report.get("error_message")}
            if report.get("status") == "failed"
            else None
        ),
        "conditions": list(config.get("conditions") or []),
        "remaining_blockers": list(config.get("remaining_blockers") or []),
        "failure_artifacts": failure_artifacts,
        "dependency_failures": [
            row for row in failure_artifacts if row["category"] == "dependency"
        ],
        "evidence_artifacts": evidence_artifacts,
        "compatibility_patches": compatibility_patches,
        "registration_findings": config.get("registration_findings"),
        "selected_asset": config.get("selected_asset"),
        "external_core_asset": config.get("external_core_asset"),
        "artifacts": artifacts,
    }


def build_runtime_evidence(raw_root: Path = RAW_ROOT) -> dict[str, Any]:
    baseline = build_baseline(raw_root)
    repositories = [build_candidate(raw_root, config) for config in CANDIDATES]
    status_counts = Counter(row["runtime_status"] for row in repositories)
    return {
        "schema": "agenticsim.awesome_isaac_runtime_evidence.v1",
        "generated_at": baseline["finished_at"],
        "claim_boundary": (
            "The baseline proves Isaac Sim physics, RTX rendering, CUDA compute, and a genuinely multi-frame video on "
            "the recorded host. Candidate rows prove only the named task, exact commit, reset/step count, and visual "
            "capture. Conditions, source patches, and required-asset license gaps remain part of each usability verdict; "
            "a public download is not treated as an open-source license grant."
        ),
        "baseline": baseline,
        "summary": {
            "repository_probe_count": len(repositories),
            "runtime_pass_count": sum(row["runtime_passed"] for row in repositories),
            "runtime_blocked_count": sum(
                not row["runtime_passed"] for row in repositories
            ),
            "direct_unmodified_pass_count": sum(
                row["runtime_passed"]
                and not row["source_tree_modified"]
                and not row["conditions"]
                and not row["asset_license_gap"]
                for row in repositories
            ),
            "source_unmodified_runtime_pass_count": sum(
                row["runtime_passed"] and not row["source_tree_modified"]
                for row in repositories
            ),
            "runtime_pass_after_source_patch_count": sum(
                row["runtime_passed"] and row["source_tree_modified"]
                for row in repositories
            ),
            "runtime_pass_with_asset_license_gap_count": sum(
                row["runtime_passed"] and row["asset_license_gap"]
                for row in repositories
            ),
            "visual_evidence_accepted_count": sum(
                row["visual_evidence_accepted"] for row in repositories
            ),
            "candidate_screenshot_count": sum(
                "screenshot" in row["artifacts"] for row in repositories
            ),
            "runtime_status_counts": dict(sorted(status_counts.items())),
        },
        "repositories": repositories,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = build_runtime_evidence(args.raw_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"status": "written", **report["summary"]}, indent=2, ensure_ascii=False
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
