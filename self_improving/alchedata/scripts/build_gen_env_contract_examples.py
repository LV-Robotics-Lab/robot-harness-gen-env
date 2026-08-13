#!/usr/bin/env python3
"""Build sample /gen-env route-contract artifacts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "artifacts" / "gen_env_contract"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def common(task_text: str, route: str, status: str) -> dict[str, Any]:
    return {
        "schema_version": "alchedata.gen_env.v0",
        "command": "/gen-env",
        "route": route,
        "status": status,
        "task_text": task_text,
        "owner": "Zheng Ye / Boris",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "task_text": task_text,
            "reference_image": None,
            "rgbd_capture": None,
            "reference_video": None,
            "capture_source": None,
            "multi_view_images": [],
            "camera_calibration": None,
            "lighting_assumption": None,
            "asset_catalog_roots": [
                "external/robotwin-text2env-demo/asset_catalogs/robotwin_tabletop_assets_master.json",
                "artifacts/adapter_catalog/articraft10k_manifest.json",
            ],
            "allowed_simulator_adapters": ["RoboTwin/SAPIEN"],
            "robot_constraints": {
                "embodiment": "RoboTwin dual-arm",
                "workspace_reachability": "tabletop",
                "action_interface": "Base_Task/CuRobo or generated play_once",
            },
        },
    }


def build_selection_route() -> dict[str, Any]:
    task = read_json(ROOT / "artifacts" / "selection2env" / "task_apple_plate.json")
    artifact_paths = {
        "selection2env_artifact": "artifacts/selection2env/task_apple_plate.json",
        "task_program_input": "artifacts/task_program_inputs/task_apple_plate.json",
        "alternate_task_program_input": "artifacts/task_program_inputs/task_apple_plate_to_left_front.json",
        "collect_report": "runs/collect_dryrun_apple_plate/collect_report.json",
        "generated_collection": "runs/generated_collect_apple_plate_action_repair/collection_report.json",
    }
    record = common(task["task_text"], "selection2env", "pass_selection2env_contract")
    record.update(
        {
            "outputs": {
                "selection2env": {
                    "asset_candidates": task["asset_candidates"],
                    "selected_assets": task["selected_assets"],
                    "placement_regions": task["placement_regions"],
                    "support_surface": task["support_surface"],
                    "pose_constraints": task["pose_constraints"],
                    "camera_observation": task["camera_observation"],
                    "robot_constraints": task["robot_constraints"],
                    "success_verifier": task["success_verifier"],
                    "scene_task_decoupling": {
                        "scene_id": task["scene_id"],
                        "alternate_task_artifact": artifact_paths["alternate_task_program_input"],
                        "claim": "scene_apple_plate_shared_two_task_v0 passes two task specs over one placement SHA",
                    },
                    "artifact_paths": artifact_paths,
                },
                "handoff": task["handoff"],
            },
            "blockers": [],
            "claim_boundary": "This sample validates the selection2env branch of the unified /gen-env contract. It reuses existing pass_static_only and collection evidence and does not claim policy evaluation.",
        }
    )
    return record


def build_forge_route() -> dict[str, Any]:
    blocker = read_json(ROOT / "artifacts" / "generation_fallback" / "blocked_drawer_mug_video2sim_forge.json")
    record = common(blocker["inputs"]["source_task"], "forge_fallback", "blocked_forge_fallback")
    record["inputs"].update(
        {
            "reference_video": blocker["inputs"].get("reference_video"),
            "rgbd_capture": blocker["inputs"].get("rgbd_capture"),
            "capture_source": blocker["inputs"].get("capture_source"),
        }
    )
    record.update(
        {
            "outputs": {
                "forge_fallback": {
                    "capture_source": None,
                    "object_prompts": [],
                    "masks": [],
                    "meshes": [],
                    "world_frame_poses": [],
                    "scene_json": None,
                    "urdf": None,
                    "physics_metadata": None,
                    "provenance": {
                        "blocker_artifact": "artifacts/generation_fallback/blocked_drawer_mug_video2sim_forge.json",
                        "blocked_selection2env_artifact": blocker["inputs"]["blocked_selection2env_artifact"],
                    },
                    "import_gate": {
                        "status": "not_run",
                        "reason": "forge output is absent, so no RoboTwin/SAPIEN import, scale, collision, support, material, or verifier gate can run",
                    },
                },
                "handoff": {
                    "collect_outputs": ["sapien_import_smoke_report", "scene_json", "object_state_trace"],
                    "train_reads": ["accepted_rollout_dataset_manifest"],
                    "evaluate_reads": ["verifier gate outputs", "failure diagnosis"],
                },
            },
            "blockers": blocker["blockers"],
            "claim_boundary": "This sample validates the forge_fallback branch of the unified /gen-env contract. It is a blocker record and does not claim video2sim-forge execution.",
        }
    )
    return record


def build_material_route() -> dict[str, Any]:
    blocker = read_json(ROOT / "artifacts" / "generation_fallback" / "blocked_neumatex_material_sidecar.json")
    record = common("extract a relightable material sidecar for a generated or imported asset", "material_sidecar", "blocked_material_sidecar")
    record["inputs"].update(
        {
            "multi_view_images": blocker["inputs"].get("multi_view_images", []),
            "camera_calibration": blocker["inputs"].get("camera_calibration"),
            "lighting_assumption": blocker["inputs"].get("lighting_assumption"),
        }
    )
    record.update(
        {
            "outputs": {
                "material_sidecar": {
                    "textures": [],
                    "materials": [],
                    "albedo": None,
                    "specular_latent": None,
                    "uncertainty": None,
                    "lighting_assumption": None,
                    "renderer_binding": None,
                    "material_provenance": {
                        "blocker_artifact": "artifacts/generation_fallback/blocked_neumatex_material_sidecar.json",
                        "candidate_asset": blocker["inputs"]["candidate_asset"],
                    },
                    "render_gate": {
                        "status": "not_run",
                        "reason": "material sidecar output is absent, so renderer binding and visual-material checks cannot run",
                    },
                },
                "handoff": {
                    "collect_outputs": ["render_smoke_report", "visual_material_check"],
                    "train_reads": ["accepted rollout dataset manifest"],
                    "evaluate_reads": ["render gate outputs", "visual-material mismatch diagnosis"],
                },
            },
            "blockers": blocker["blockers"],
            "claim_boundary": "This sample validates the material_sidecar branch of the unified /gen-env contract. It is a blocker record and does not claim NeuMaTeX execution.",
        }
    )
    return record


def main() -> int:
    examples = [
        ("selection2env_route_sample.json", build_selection_route()),
        ("forge_fallback_route_blocker.json", build_forge_route()),
        ("material_sidecar_route_blocker.json", build_material_route()),
    ]
    for filename, data in examples:
        write_json(OUT_DIR / filename, data)
    manifest = {
        "schema_version": "alchedata.gen_env_contract_manifest.v0",
        "status": "pass_gen_env_contract_examples",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema": "schemas/gen_env.schema.json",
        "examples": [f"artifacts/gen_env_contract/{filename}" for filename, _ in examples],
        "claim_boundary": "Validates the unified /gen-env route contract over selection2env, forge fallback blocker, and material sidecar blocker examples.",
    }
    write_json(OUT_DIR / "gen_env_contract_manifest.json", manifest)
    print(json.dumps({"status": manifest["status"], "examples": len(examples)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
