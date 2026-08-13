#!/usr/bin/env python3
"""Build machine-readable /gen-env fallback blocker probes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "artifacts" / "generation_fallback"


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def common_probe(fallback_id: str, fallback_type: str) -> dict[str, Any]:
    return {
        "schema_version": "alchedata.gen_env_fallback_probe.v0",
        "fallback_id": fallback_id,
        "fallback_type": fallback_type,
        "command": "/gen-env",
        "owner": "Zheng Ye / Boris",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def build_video2sim_probe() -> dict[str, Any]:
    probe = common_probe("blocked_drawer_mug_video2sim_forge", "video2sim_forge")
    probe.update(
        {
            "status": "blocked_missing_inputs",
            "inputs": {
                "source_task": "open the drawer and place the mug inside",
                "blocked_selection2env_artifact": "artifacts/selection2env/task_drawer_mug_blocker.json",
                "capture_source": None,
                "reference_video": None,
                "rgbd_capture": None,
                "asset_catalog_gap": ["drawer", "mug"],
            },
            "expected_outputs": [
                "object_prompts",
                "segmentation_masks",
                "mesh_paths",
                "world_frame_poses",
                "scene_json",
                "urdf_or_articulated_description",
                "approximate_physics_metadata",
                "provenance",
                "sapien_import_smoke_report",
            ],
            "checks": [
                {
                    "name": "selection2env_blocker_exists",
                    "status": "pass",
                    "evidence": "artifacts/selection2env/task_drawer_mug_blocker.json records missing drawer/mug assets",
                },
                {
                    "name": "capture_source_available",
                    "status": "fail",
                    "evidence": "No RGB-D capture, reference video, masks, or object prompt package is present in this workspace",
                },
                {
                    "name": "sapien_import_smoke",
                    "status": "not_run",
                    "evidence": "Forge output is absent, so there is no mesh/URDF to import or smoke-test",
                },
            ],
            "import_gate_matrix": {
                gate: {
                    "status": "blocked_missing_forge_artifact",
                    "evidence": "No generated mesh, URDF, scene, pose, physics, or material artifact exists for this gate",
                }
                for gate in ("import", "scale", "collision", "support", "material", "render", "verifier")
            },
            "blockers": [
                {
                    "code": "FORGE_CAPTURE_SOURCE_MISSING",
                    "message": "video2sim-forge-style dry run needs a reference video or RGB-D capture with object prompts/masks before mesh/URDF generation can be probed",
                    "owner": "Zheng Ye",
                },
                {
                    "code": "FORGE_IMPORT_ARTIFACT_MISSING",
                    "message": "No generated mesh, URDF, world-frame pose, or physics metadata exists for a RoboTwin/SAPIEN import gate",
                    "owner": "Zheng Ye / Boris",
                },
            ],
            "next_step": "Attach one drawer/mug RGB-D or reference-video package, then run forge output through scale, collision, support, material, SAPIEN import, and verifier gates.",
            "claim_boundary": "This is a machine-readable blocker for the missing-asset forge fallback. It does not claim video2sim-forge execution or generated asset validity.",
        }
    )
    return probe


def build_neumatex_probe() -> dict[str, Any]:
    probe = common_probe("blocked_neumatex_material_sidecar", "neumatex_material_sidecar")
    probe.update(
        {
            "status": "blocked_missing_inputs",
            "inputs": {
                "candidate_asset": "generated_or_articraft_asset_pending_material_review",
                "multi_view_images": [],
                "camera_calibration": None,
                "lighting_assumption": None,
                "target_renderer": "SAPIEN/RoboTwin",
            },
            "expected_outputs": [
                "texture_paths",
                "albedo",
                "specular_latent_or_equivalent",
                "uncertainty",
                "lighting_assumption",
                "renderer_binding",
                "material_provenance",
                "render_smoke_report",
            ],
            "checks": [
                {
                    "name": "material_input_views_available",
                    "status": "fail",
                    "evidence": "No calibrated multi-view image set or rendered material capture package is present in this workspace",
                },
                {
                    "name": "geometry_asset_gate",
                    "status": "fail",
                    "evidence": "No accepted forged/generated asset is ready for material-sidecar binding beyond sampled Articraft URDF import probes",
                },
                {
                    "name": "renderer_binding_smoke",
                    "status": "not_run",
                    "evidence": "Material sidecar output is absent, so SAPIEN/RoboTwin render binding cannot be smoke-tested",
                },
            ],
            "import_gate_matrix": {
                "import": {
                    "status": "blocked_missing_geometry_input",
                    "evidence": "No accepted generated geometry is selected for sidecar binding",
                },
                "scale": {
                    "status": "blocked_missing_geometry_input",
                    "evidence": "No accepted generated geometry is selected for scale validation",
                },
                "collision": {
                    "status": "blocked_missing_geometry_input",
                    "evidence": "No accepted generated geometry is selected for collision validation",
                },
                "support": {
                    "status": "blocked_missing_geometry_input",
                    "evidence": "No accepted generated geometry is selected for support validation",
                },
                "material": {
                    "status": "blocked_missing_multiview_input",
                    "evidence": "No calibrated multi-view images or extracted material sidecar exist",
                },
                "render": {
                    "status": "blocked_missing_renderer_binding",
                    "evidence": "No material sidecar is bound to a SAPIEN renderer material",
                },
                "verifier": {
                    "status": "blocked_missing_render_evidence",
                    "evidence": "No rendered output exists for visual-material verification",
                },
            },
            "blockers": [
                {
                    "code": "MATERIAL_MULTIVIEW_INPUT_MISSING",
                    "message": "NeuMaTeX-style extraction needs calibrated multi-view images or rendered captures before material fields can be produced",
                    "owner": "Zheng Ye",
                },
                {
                    "code": "MATERIAL_RENDER_BINDING_MISSING",
                    "message": "No texture/material sidecar has been bound to a RoboTwin/SAPIEN asset for render verification",
                    "owner": "Zheng Ye / Boris",
                },
            ],
            "next_step": "Pick one accepted generated or Articraft asset, provide calibrated multi-view or rendered captures, extract a material sidecar, and run SAPIEN render/visual checks.",
            "claim_boundary": "This is a machine-readable blocker for the material sidecar route. It does not claim NeuMaTeX execution or material fidelity.",
        }
    )
    return probe


def main() -> int:
    probes = [build_video2sim_probe(), build_neumatex_probe()]
    for probe in probes:
        write_json(OUT_DIR / f"{probe['fallback_id']}.json", probe)
    summary = {
        "schema_version": "alchedata.gen_env_fallback_summary.v0",
        "status": "blocked_fallback_inputs_missing",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "probes": [f"artifacts/generation_fallback/{probe['fallback_id']}.json" for probe in probes],
        "claim_boundary": "Records typed /gen-env fallback blockers for Open X Sim acceptance. It does not claim forge or material extraction execution.",
    }
    write_json(OUT_DIR / "fallback_blocker_summary.json", summary)
    print(json.dumps({"status": summary["status"], "probes": len(probes)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
