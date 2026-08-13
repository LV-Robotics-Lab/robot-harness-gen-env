#!/usr/bin/env python3
"""Run the prompt-to-preview tabletop placement pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generate_scene.model_providers import (
    critic_review_from_validation,
    design_initial_spec,
    orchestrate_final_spec,
    validation_plan_for,
)
from generate_scene.asset_catalog import load_asset_catalog
from generate_scene.schemas import read_json, validate_placement_spec, write_json
from generate_scene.tools import get_smoke_artifacts, run_robotwin_smoke, visual_review


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a placement prompt to RoboTwin preview.")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--asset-catalog", default="asset_catalogs/prompt_cases/apple_plate.json")
    parser.add_argument("--robotwin-root", default=str(Path.home() / "RoboTwin"))
    parser.add_argument("--model-provider", default="codex_reference")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--run-smoke", action="store_true")
    parser.add_argument("--task-config", default="demo_smoke")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--settle-steps", type=int, default=240)
    parser.add_argument("--video-frames", type=int, default=60)
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument(
        "--visual-review-mode",
        choices=["required", "artifact_only"],
        default="required",
        help="required prevents smoke artifact existence from being treated as semantic visual pass.",
    )
    parser.add_argument(
        "--visual-review-report",
        help="Optional human/VLM/Codex-visual review JSON. status must be pass for final pipeline pass.",
    )
    args = parser.parse_args()

    asset_catalog_path = Path(args.asset_catalog)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    catalog = load_asset_catalog(asset_catalog_path)
    summary: dict[str, Any] = {
        "schema_version": "robotwin.tabletop_placement_pipeline_summary.v0",
        "prompt": args.prompt,
        "asset_catalog": _rel(asset_catalog_path),
        "model_provider": args.model_provider,
        "out_dir": _rel(out_dir),
        "status": "started",
        "artifacts": {},
    }

    try:
        designer_spec = design_initial_spec(
            prompt=args.prompt,
            catalog=catalog,
            asset_catalog_path=_rel(asset_catalog_path),
            model_provider=args.model_provider,
        )
        designer_path = out_dir / "designer_initial_placement.json"
        write_json(designer_path, designer_spec)

        initial_validation = validate_placement_spec(designer_spec, catalog, robotwin_root=args.robotwin_root)
        initial_validation_path = out_dir / "static_validation_initial.json"
        write_json(initial_validation_path, initial_validation)

        critic_review = critic_review_from_validation(
            placement=designer_spec,
            validation_report=initial_validation,
            model_provider=args.model_provider,
        )
        critic_path = out_dir / "critic_review.json"
        write_json(critic_path, critic_review)

        final_spec = orchestrate_final_spec(
            designer_spec=designer_spec,
            critic_review=critic_review,
            model_provider=args.model_provider,
        )
        final_path = out_dir / "final_placement.json"
        write_json(final_path, final_spec)

        final_validation = validate_placement_spec(final_spec, catalog, robotwin_root=args.robotwin_root)
        final_validation_path = out_dir / "static_validation_final.json"
        write_json(final_validation_path, final_validation)

        validation_plan_path = out_dir / "validation_plan.json"
        write_json(validation_plan_path, validation_plan_for(final_spec))

        summary["artifacts"].update(
            {
                "designer_initial_placement": _rel(designer_path),
                "static_validation_initial": _rel(initial_validation_path),
                "critic_review": _rel(critic_path),
                "final_placement": _rel(final_path),
                "static_validation_final": _rel(final_validation_path),
                "validation_plan": _rel(validation_plan_path),
            }
        )

        if final_validation["status"] != "pass":
            summary["status"] = "fail_static_validation"
            write_json(out_dir / "pipeline_summary.json", summary)
            print(f"FAIL {out_dir / 'pipeline_summary.json'}")
            return 1

        if args.run_smoke:
            smoke_dir = out_dir / "smoke"
            smoke_report = run_robotwin_smoke(
                robotwin_root=Path(args.robotwin_root).expanduser(),
                placement=final_path,
                out_dir=smoke_dir,
                task_config=args.task_config,
                seed=args.seed,
                settle_steps=args.settle_steps,
                video_frames=args.video_frames,
                fps=args.fps,
            )
            smoke_report_path = out_dir / "smoke_report_with_command.json"
            write_json(smoke_report_path, smoke_report)

            visual_review_report = visual_review(smoke_dir, args.prompt, mode=args.visual_review_mode)
            if args.visual_review_report:
                visual_review_report = read_json(Path(args.visual_review_report))
            visual_review_path = out_dir / "visual_review.json"
            write_json(visual_review_path, visual_review_report)

            summary["artifacts"].update(
                {
                    "smoke_report": _rel(smoke_dir / "smoke_report.json"),
                    "smoke_report_with_command": _rel(smoke_report_path),
                    "visual_review": _rel(visual_review_path),
                    "preview": get_smoke_artifacts(smoke_dir),
                }
            )
            smoke_passed = smoke_report.get("status") == "pass" and smoke_report.get("returncode") == 0
            visual_status = str(visual_review_report.get("status", ""))
            if not smoke_passed:
                summary["status"] = "fail_smoke"
            elif visual_status == "pass":
                summary["status"] = "pass"
            elif visual_status.startswith("fail"):
                summary["status"] = "fail_visual_review"
            else:
                summary["status"] = "pending_visual_review"
        else:
            summary["status"] = "pass_static_only"

        write_json(out_dir / "pipeline_summary.json", summary)
        if summary["status"] == "pass":
            label = "PASS"
        elif summary["status"] == "pass_static_only":
            label = "PASS_STATIC_ONLY"
        elif summary["status"] == "pending_visual_review":
            label = "REVIEW_REQUIRED"
        else:
            label = "FAIL"
        print(f"{label} {out_dir / 'pipeline_summary.json'}")
        return 0 if summary["status"] in ["pass", "pending_visual_review", "pass_static_only"] else 1
    except Exception as exc:
        summary["status"] = "fail_exception"
        summary["error"] = repr(exc)
        write_json(out_dir / "pipeline_summary.json", summary)
        print(f"FAIL {out_dir / 'pipeline_summary.json'}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
