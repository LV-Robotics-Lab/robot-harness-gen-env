#!/usr/bin/env python3
"""Generate multiple diverse render-reviewed scenes for one prompt."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generate_scene.asset_grounding import slugify_prompt
from generate_scene.schemas import read_json, write_json
from generate_scene.tools import get_smoke_artifacts


def _placement_summary(scene_dir: Path) -> dict[str, Any]:
    placement_path = scene_dir / "final_placement.json"
    placement = read_json(placement_path)
    return {
        "scene_dir": str(scene_dir),
        "placement_name": placement.get("placement_name"),
        "objects": [
            {
                "id": obj.get("id"),
                "semantic": obj.get("semantic"),
                "asset_id": obj.get("asset_id"),
                "model_id": obj.get("model_id"),
                "pose": obj.get("pose", {}),
            }
            for obj in placement.get("objects", [])
        ],
        "relations": placement.get("relations", []),
    }


def _accepted(status: str, allow_pending_visual: bool) -> bool:
    return status == "pass" or (allow_pending_visual and status == "pending_visual_review")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a diverse batch of RoboTwin tabletop scenes.")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--batch-name")
    parser.add_argument("--num-scenes", type=int, default=5)
    parser.add_argument("--max-candidates", type=int, default=8)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--master-catalog", default="asset_catalogs/robotwin_tabletop_assets_master.json")
    parser.add_argument("--discover-assets-from-robotwin", action="store_true")
    parser.add_argument("--robotwin-root", default=str(Path.home() / "RoboTwin"))
    parser.add_argument("--model-provider", default="codex_reference")
    parser.add_argument("--generated-scene-dir", default="generated_scenes")
    parser.add_argument("--run-smoke", action="store_true")
    parser.add_argument("--task-config", default="demo_smoke")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--settle-steps", type=int, default=240)
    parser.add_argument("--video-frames", type=int, default=30)
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--smoke-timeout", type=int, default=420)
    parser.add_argument("--python-executable")
    parser.add_argument("--visual-review-mode", choices=["required", "artifact_only", "moonshot", "openai"], default="required")
    parser.add_argument("--visual-repair-attempts", type=int, default=0)
    parser.add_argument("--allow-pending-visual", action="store_true")
    args = parser.parse_args()

    batch_name = args.batch_name or slugify_prompt(args.prompt)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    context_path = out_dir / "diversity_context.json"
    batch_summary_path = out_dir / "batch_summary.json"
    generated_scene_dir = Path(args.generated_scene_dir)

    accepted_scenes: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []

    for candidate_idx in range(args.max_candidates):
        if len(accepted_scenes) >= args.num_scenes:
            break
        write_json(context_path, {"accepted_scenes": accepted_scenes})
        variation_index = len(accepted_scenes)
        case_name = f"{batch_name}_v{variation_index:02d}_c{candidate_idx:02d}"
        scene_dir = out_dir / f"scene_{variation_index:02d}_candidate_{candidate_idx:02d}"
        cmd = [
            sys.executable,
            str(REPO_ROOT / "generate_scene" / "run_scene_generation_pipeline.py"),
            "--prompt",
            args.prompt,
            "--case-name",
            case_name,
            "--master-catalog",
            args.master_catalog,
            "--robotwin-root",
            args.robotwin_root,
            "--model-provider",
            args.model_provider,
            "--out-dir",
            str(scene_dir),
            "--generated-scene-dir",
            str(generated_scene_dir),
            "--seed",
            str(args.seed + candidate_idx),
            "--settle-steps",
            str(args.settle_steps),
            "--video-frames",
            str(args.video_frames),
            "--fps",
            str(args.fps),
            "--smoke-timeout",
            str(args.smoke_timeout),
            "--visual-review-mode",
            args.visual_review_mode,
            "--visual-repair-attempts",
            str(args.visual_repair_attempts),
            "--variation-index",
            str(variation_index),
            "--num-variations",
            str(args.num_scenes),
            "--diversity-context",
            str(context_path),
        ]
        if args.discover_assets_from_robotwin:
            cmd.append("--discover-assets-from-robotwin")
        if args.run_smoke:
            cmd.append("--run-smoke")
        if args.python_executable:
            cmd.extend(["--python-executable", args.python_executable])

        completed = subprocess.run(cmd, cwd=str(REPO_ROOT), text=True, capture_output=True, check=False)
        summary_path = scene_dir / "scene_generation_summary.json"
        scene_summary = read_json(summary_path) if summary_path.exists() else {}
        status = str(scene_summary.get("status", "fail_no_summary"))
        candidate_record = {
            "candidate_index": candidate_idx,
            "variation_index": variation_index,
            "case_name": case_name,
            "scene_dir": str(scene_dir),
            "status": status,
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-2000:],
            "stderr_tail": completed.stderr[-4000:],
            "summary": str(summary_path),
        }
        if summary_path.exists():
            candidate_record["artifacts"] = scene_summary.get("artifacts", {})
        candidates.append(candidate_record)

        if completed.returncode == 0 and _accepted(status, args.allow_pending_visual):
            accepted_record = {
                "accepted_index": len(accepted_scenes),
                "candidate_index": candidate_idx,
                "case_name": case_name,
                "scene_dir": str(scene_dir),
                "status": status,
                "summary": str(summary_path),
                "preview": get_smoke_artifacts(scene_dir / "smoke"),
                "placement_summary": _placement_summary(scene_dir),
            }
            accepted_scenes.append(accepted_record)

        batch_summary = {
            "schema_version": "robotwin.tabletop_scene_batch.v0",
            "prompt": args.prompt,
            "batch_name": batch_name,
            "requested_scenes": args.num_scenes,
            "accepted_count": len(accepted_scenes),
            "candidate_count": len(candidates),
            "status": "pass" if len(accepted_scenes) >= args.num_scenes else "running",
            "accepted_scenes": accepted_scenes,
            "candidates": candidates,
        }
        write_json(batch_summary_path, batch_summary)

    status = "pass" if len(accepted_scenes) >= args.num_scenes else "partial"
    batch_summary = {
        "schema_version": "robotwin.tabletop_scene_batch.v0",
        "prompt": args.prompt,
        "batch_name": batch_name,
        "requested_scenes": args.num_scenes,
        "accepted_count": len(accepted_scenes),
        "candidate_count": len(candidates),
        "status": status,
        "accepted_scenes": accepted_scenes,
        "candidates": candidates,
    }
    write_json(batch_summary_path, batch_summary)
    print(f"{status.upper()} {batch_summary_path}")
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
