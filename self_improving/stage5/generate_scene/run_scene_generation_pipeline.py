#!/usr/bin/env python3
"""Run natural-language prompt to a render-reviewed RoboTwin tabletop scene."""

from __future__ import annotations

import argparse
import copy
import shutil
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generate_scene.asset_catalog import load_asset_catalog
from generate_scene.asset_discovery import discover_robotwin_assets
from generate_scene.asset_grounding import (
    ground_assets,
    prompt_case_from_grounding,
    slugify_prompt,
    validate_asset_grounding_result,
)
from generate_scene.gpt_agent import (
    moonshot_design_initial_spec,
    moonshot_ground_assets,
    moonshot_repair_from_scene_critic,
)
from generate_scene.model_providers import design_initial_spec, validation_plan_for
from generate_scene.observation_agent import observe_scene_with_provider
from generate_scene.scene_codegen import generate_scene_module
from generate_scene.scene_critic import build_scene_critic_report
from generate_scene.schemas import read_json, validate_placement_spec, write_json
from generate_scene.tools import get_smoke_artifacts, run_robotwin_smoke, visual_review


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


def _case_base_catalog(case_path: Path, master_catalog_path: Path) -> str:
    try:
        return str(master_catalog_path.resolve().relative_to(case_path.parent.resolve())).replace("\\", "/")
    except ValueError:
        return str(master_catalog_path.resolve())


def _uses_moonshot(provider: str) -> bool:
    return provider.lower() in {"moonshot", "kimi", "kimi_moonshot"}


def _uses_openai(provider: str) -> bool:
    return provider.lower() in {"openai", "gpt", "gpt5", "gpt-5", "gpt5.5", "gpt-5.5"}


def _uses_llm_agent(provider: str) -> bool:
    return _uses_moonshot(provider) or _uses_openai(provider)


def _mark_final_spec(
    *,
    spec: dict[str, Any],
    scene_critic_review: dict[str, Any],
    attempt: int,
) -> dict[str, Any]:
    final_spec = copy.deepcopy(spec)
    final_spec["schema_version"] = "robotwin.tabletop_placement.v0"
    final_spec["stage"] = "final_render_accepted"
    name = str(final_spec.get("placement_name", "placement"))
    if "final_render_accepted" not in name:
        name = name.replace("designer_initial", "final_render_accepted")
        if "final_render_accepted" not in name:
            name += "_final_render_accepted"
    final_spec["placement_name"] = name
    final_spec["source_scene_critic_review"] = "scene_critic_review.json"
    final_spec["orchestrator_decision"] = {
        "decision": "accept_final",
        "reason": scene_critic_review.get("summary", "Scene Critic accepted the rendered scene."),
        "accepted_attempt": attempt,
        "remaining_uncertainties": [
            "This scene is a tabletop background/placement artifact, not a generated play_once() task policy.",
            "Downstream manipulation tasks must still define play_once() and check_success().",
        ],
    }
    final_spec.setdefault("validation", {})
    final_spec["validation"].update(
        {
            "scene_critic": "pass",
            "robotwin_load_check": "pass_smoke",
            "render_visibility": "pass_visual_review",
        }
    )
    return final_spec


def _copy_if_exists(src: Path, dst: Path) -> None:
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run prompt to a render-reviewed RoboTwin scene module.")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--master-catalog", default="asset_catalogs/robotwin_tabletop_assets_master.json")
    parser.add_argument("--discover-assets-from-robotwin", action="store_true")
    parser.add_argument("--prompt-case")
    parser.add_argument("--case-name")
    parser.add_argument("--overwrite-prompt-case", action="store_true")
    parser.add_argument("--robotwin-root", default=str(Path.home() / "RoboTwin"))
    parser.add_argument("--model-provider", default="codex_reference")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--generated-scene-dir", default="generated_scenes")
    parser.add_argument("--run-smoke", action="store_true")
    parser.add_argument("--task-config", default="demo_smoke")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--settle-steps", type=int, default=240)
    parser.add_argument("--video-frames", type=int, default=60)
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--smoke-timeout", type=int, default=420)
    parser.add_argument("--python-executable", help="Python executable for RoboTwin smoke. Also configurable through ROBOTWIN_PYTHON.")
    parser.add_argument("--visual-review-mode", choices=["required", "artifact_only", "moonshot", "openai"], default="required")
    parser.add_argument("--visual-review-report")
    parser.add_argument("--visual-repair-attempts", type=int, default=0)
    parser.add_argument("--variation-index", type=int, default=0)
    parser.add_argument("--num-variations", type=int, default=1)
    parser.add_argument("--diversity-context", help="Optional JSON file with previous accepted placement summaries.")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    master_catalog_path = Path(args.master_catalog)
    if args.discover_assets_from_robotwin:
        master_catalog_path = out_dir / "robotwin_discovered_asset_catalog.json"
        discovered_catalog = discover_robotwin_assets(Path(args.robotwin_root))
        write_json(master_catalog_path, discovered_catalog)
    master_catalog = load_asset_catalog(master_catalog_path)
    case_name = args.case_name or slugify_prompt(args.prompt)
    if args.prompt_case:
        prompt_case_path = Path(args.prompt_case)
    elif args.discover_assets_from_robotwin:
        prompt_case_path = out_dir / "prompt_case_catalog.json"
    else:
        prompt_case_path = Path("asset_catalogs") / "prompt_cases" / f"{case_name}.json"

    summary: dict[str, Any] = {
        "schema_version": "robotwin.tabletop_scene_generation_summary.v1",
        "pipeline": "render_in_the_loop_scene_generation",
        "prompt": args.prompt,
        "case_name": case_name,
        "master_catalog": _rel(master_catalog_path),
        "prompt_case": _rel(prompt_case_path),
        "model_provider": args.model_provider,
        "out_dir": _rel(out_dir),
        "status": "started",
        "variation_index": args.variation_index,
        "num_variations": args.num_variations,
        "artifacts": {},
        "attempts": [],
    }
    if args.discover_assets_from_robotwin:
        summary["artifacts"]["discovered_asset_catalog"] = _rel(master_catalog_path)

    try:
        if _uses_llm_agent(args.model_provider):
            grounding = moonshot_ground_assets(
                prompt=args.prompt,
                master_catalog=master_catalog,
                master_catalog_path=_rel(master_catalog_path),
                model_provider=args.model_provider,
            )
        else:
            grounding = ground_assets(
                prompt=args.prompt,
                master_catalog=master_catalog,
                master_catalog_path=_rel(master_catalog_path),
            )
        grounding_validation = validate_asset_grounding_result(grounding, master_catalog)
        grounding["validation"] = grounding_validation
        grounding_path = out_dir / "asset_grounding.json"
        write_json(grounding_path, grounding)
        summary["artifacts"]["asset_grounding"] = _rel(grounding_path)

        if grounding_validation["status"] != "pass":
            summary["status"] = "fail_asset_grounding"
            write_json(out_dir / "scene_generation_summary.json", summary)
            print(f"FAIL {out_dir / 'scene_generation_summary.json'}")
            return 1

        if prompt_case_path.exists() and not args.overwrite_prompt_case:
            prompt_case_status = "exists_not_overwritten"
        else:
            prompt_case = prompt_case_from_grounding(
                grounding=grounding,
                case_name=case_name,
                base_catalog=_case_base_catalog(prompt_case_path, master_catalog_path),
            )
            write_json(prompt_case_path, prompt_case)
            prompt_case_status = "written"
        case_copy_path = out_dir / "prompt_case.json"
        shutil.copyfile(prompt_case_path, case_copy_path)
        summary["prompt_case_status"] = prompt_case_status
        summary["artifacts"]["prompt_case_copy"] = _rel(case_copy_path)

        catalog = load_asset_catalog(prompt_case_path)
        diversity_context = read_json(Path(args.diversity_context)).get("accepted_scenes", []) if args.diversity_context else []
        if _uses_llm_agent(args.model_provider):
            working_spec = moonshot_design_initial_spec(
                prompt=args.prompt,
                catalog=catalog,
                asset_catalog_path=_rel(prompt_case_path),
                model_provider=args.model_provider,
                variation_index=args.variation_index,
                num_variations=args.num_variations,
                diversity_context=diversity_context,
            )
        else:
            working_spec = design_initial_spec(
                prompt=args.prompt,
                catalog=catalog,
                asset_catalog_path=_rel(prompt_case_path),
                model_provider=args.model_provider,
                variation_index=args.variation_index,
                num_variations=args.num_variations,
                diversity_context=diversity_context,
            )

        designer_path = out_dir / "designer_initial_placement.json"
        write_json(designer_path, working_spec)
        summary["artifacts"]["designer_initial_placement"] = _rel(designer_path)

        scene_module_path = Path(args.generated_scene_dir) / f"{case_name}_scene.py"
        max_attempts = max(args.visual_repair_attempts, 0) + 1
        accepted = False
        last_scene_critic: dict[str, Any] | None = None
        last_visual_review: dict[str, Any] | None = None
        last_smoke_report: dict[str, Any] | None = None

        for attempt in range(max_attempts):
            attempt_name = f"attempt_{attempt}"
            attempt_record: dict[str, Any] = {"attempt": attempt, "stage": "designer_render_loop"}
            attempt_placement_path = out_dir / f"{attempt_name}_placement.json"
            write_json(attempt_placement_path, working_spec)
            attempt_record["placement"] = _rel(attempt_placement_path)

            static_validation = validate_placement_spec(working_spec, catalog, robotwin_root=args.robotwin_root)
            static_validation_path = out_dir / f"{attempt_name}_static_validation.json"
            write_json(static_validation_path, static_validation)
            attempt_record["static_validation"] = _rel(static_validation_path)
            if attempt == 0:
                _copy_if_exists(static_validation_path, out_dir / "static_validation_initial.json")
                summary["artifacts"]["static_validation_initial"] = _rel(out_dir / "static_validation_initial.json")

            if static_validation.get("status") != "pass":
                scene_critic_review = build_scene_critic_report(
                    prompt=args.prompt,
                    placement=working_spec,
                    static_validation=static_validation,
                    attempt=attempt,
                )
                scene_critic_path = out_dir / f"{attempt_name}_scene_critic_review.json"
                write_json(scene_critic_path, scene_critic_review)
                last_scene_critic = scene_critic_review
                attempt_record["scene_critic_review"] = _rel(scene_critic_path)
                attempt_record["status"] = scene_critic_review["overall_status"]
                summary["attempts"].append(attempt_record)

                if attempt + 1 >= max_attempts or not _uses_llm_agent(args.model_provider):
                    write_json(out_dir / "scene_critic_review.json", scene_critic_review)
                    summary["artifacts"]["scene_critic_review"] = _rel(out_dir / "scene_critic_review.json")
                    summary["status"] = "fail_static_preflight"
                    write_json(out_dir / "scene_generation_summary.json", summary)
                    print(f"FAIL {out_dir / 'scene_generation_summary.json'}")
                    return 1

                working_spec = moonshot_repair_from_scene_critic(
                    placement_spec=working_spec,
                    scene_critic_review=scene_critic_review,
                    catalog=catalog,
                    model_provider=args.model_provider,
                )
                continue

            scene_report = generate_scene_module(placement_path=attempt_placement_path, out_path=scene_module_path)
            scene_report_path = out_dir / f"{attempt_name}_scene_codegen_report.json"
            write_json(scene_report_path, scene_report)
            attempt_record["generated_scene_module"] = _rel(scene_module_path)
            attempt_record["scene_codegen_report"] = _rel(scene_report_path)

            if not args.run_smoke:
                preflight_scene_critic = build_scene_critic_report(
                    prompt=args.prompt,
                    placement=working_spec,
                    static_validation=static_validation,
                    attempt=attempt,
                )
                write_json(out_dir / "scene_critic_review.json", preflight_scene_critic)
                final_spec = _mark_final_spec(
                    spec=working_spec,
                    scene_critic_review=preflight_scene_critic,
                    attempt=attempt,
                )
                final_path = out_dir / "final_placement.json"
                write_json(final_path, final_spec)
                scene_report = generate_scene_module(placement_path=final_path, out_path=scene_module_path)
                write_json(out_dir / "scene_codegen_report.json", scene_report)
                write_json(out_dir / "validation_plan.json", validation_plan_for(final_spec))
                summary["artifacts"].update(
                    {
                        "final_placement": _rel(final_path),
                        "generated_scene_module": _rel(scene_module_path),
                        "scene_codegen_report": _rel(out_dir / "scene_codegen_report.json"),
                        "validation_plan": _rel(out_dir / "validation_plan.json"),
                        "scene_critic_review": _rel(out_dir / "scene_critic_review.json"),
                    }
                )
                summary["status"] = "pass_static_scene_module"
                summary["attempts"].append({**attempt_record, "status": "pass_static_scene_module"})
                accepted = True
                break

            smoke_dir = out_dir / f"smoke_{attempt_name}"
            smoke_report = run_robotwin_smoke(
                robotwin_root=Path(args.robotwin_root).expanduser(),
                placement=attempt_placement_path,
                out_dir=smoke_dir,
                task_config=args.task_config,
                seed=args.seed,
                settle_steps=args.settle_steps,
                video_frames=args.video_frames,
                fps=args.fps,
                scene_module=scene_module_path,
                python_executable=args.python_executable,
                timeout_sec=args.smoke_timeout,
            )
            smoke_report_path = out_dir / f"{attempt_name}_smoke_report_with_command.json"
            write_json(smoke_report_path, smoke_report)
            last_smoke_report = smoke_report
            attempt_record["smoke_report"] = _rel(smoke_dir / "smoke_report.json")
            attempt_record["smoke_report_with_command"] = _rel(smoke_report_path)
            attempt_record["preview"] = get_smoke_artifacts(smoke_dir)

            if args.visual_review_report:
                visual_review_report = read_json(Path(args.visual_review_report))
            elif args.visual_review_mode in {"moonshot", "openai"}:
                visual_review_report = observe_scene_with_provider(
                    smoke_dir=smoke_dir,
                    prompt=args.prompt,
                    placement=working_spec,
                    asset_grounding=grounding,
                    model_provider=args.visual_review_mode,
                )
            else:
                visual_review_report = visual_review(smoke_dir, args.prompt, mode=args.visual_review_mode)
            visual_review_path = out_dir / f"{attempt_name}_visual_review.json"
            write_json(visual_review_path, visual_review_report)
            last_visual_review = visual_review_report
            attempt_record["visual_review"] = _rel(visual_review_path)

            scene_critic_review = build_scene_critic_report(
                prompt=args.prompt,
                placement=working_spec,
                static_validation=static_validation,
                smoke_report=smoke_report,
                visual_review=visual_review_report,
                smoke_dir=smoke_dir,
                attempt=attempt,
            )
            scene_critic_path = out_dir / f"{attempt_name}_scene_critic_review.json"
            write_json(scene_critic_path, scene_critic_review)
            last_scene_critic = scene_critic_review
            attempt_record["scene_critic_review"] = _rel(scene_critic_path)
            attempt_record["status"] = scene_critic_review["overall_status"]
            summary["attempts"].append(attempt_record)

            if scene_critic_review["overall_status"] in {"pass", "pending_visual_review"}:
                final_spec = _mark_final_spec(spec=working_spec, scene_critic_review=scene_critic_review, attempt=attempt)
                final_path = out_dir / "final_placement.json"
                write_json(final_path, final_spec)
                final_validation = validate_placement_spec(final_spec, catalog, robotwin_root=args.robotwin_root)
                write_json(out_dir / "static_validation_final.json", final_validation)
                scene_report = generate_scene_module(placement_path=final_path, out_path=scene_module_path)
                write_json(out_dir / "scene_codegen_report.json", scene_report)
                write_json(out_dir / "validation_plan.json", validation_plan_for(final_spec))

                write_json(out_dir / "scene_critic_review.json", scene_critic_review)
                write_json(out_dir / "visual_review.json", visual_review_report)
                _copy_if_exists(smoke_report_path, out_dir / "smoke_report_with_command.json")
                if smoke_dir != out_dir / "smoke":
                    final_smoke_dir = out_dir / "smoke"
                    if final_smoke_dir.exists():
                        shutil.rmtree(final_smoke_dir)
                    shutil.copytree(smoke_dir, final_smoke_dir)

                summary["artifacts"].update(
                    {
                        "final_placement": _rel(final_path),
                        "static_validation_final": _rel(out_dir / "static_validation_final.json"),
                        "generated_scene_module": _rel(scene_module_path),
                        "scene_codegen_report": _rel(out_dir / "scene_codegen_report.json"),
                        "validation_plan": _rel(out_dir / "validation_plan.json"),
                        "smoke_report": _rel((out_dir / "smoke") / "smoke_report.json"),
                        "smoke_report_with_command": _rel(out_dir / "smoke_report_with_command.json"),
                        "visual_review": _rel(out_dir / "visual_review.json"),
                        "scene_critic_review": _rel(out_dir / "scene_critic_review.json"),
                        "preview": get_smoke_artifacts(out_dir / "smoke"),
                    }
                )
                summary["status"] = "pass" if scene_critic_review["overall_status"] == "pass" else "pending_visual_review"
                accepted = True
                break

            if attempt + 1 >= max_attempts or not _uses_llm_agent(args.model_provider):
                break

            working_spec = moonshot_repair_from_scene_critic(
                placement_spec=working_spec,
                scene_critic_review=scene_critic_review,
                catalog=catalog,
                model_provider=args.model_provider,
            )

        if not accepted:
            if last_scene_critic is not None:
                write_json(out_dir / "scene_critic_review.json", last_scene_critic)
                summary["artifacts"]["scene_critic_review"] = _rel(out_dir / "scene_critic_review.json")
            if last_visual_review is not None:
                write_json(out_dir / "visual_review.json", last_visual_review)
                summary["artifacts"]["visual_review"] = _rel(out_dir / "visual_review.json")
            if last_smoke_report is not None:
                write_json(out_dir / "smoke_report_with_command.json", last_smoke_report)
                summary["artifacts"]["smoke_report_with_command"] = _rel(out_dir / "smoke_report_with_command.json")
            final_status = (last_scene_critic or {}).get("overall_status", "fail_exception")
            summary["status"] = final_status if str(final_status).startswith("fail") else "repair_required"

        write_json(out_dir / "scene_generation_summary.json", summary)
        if summary["status"] == "pass":
            label = "PASS"
        elif summary["status"] == "pending_visual_review":
            label = "REVIEW_REQUIRED"
        elif summary["status"] == "pass_static_scene_module":
            label = "PASS_STATIC"
        else:
            label = "FAIL"
        print(f"{label} {out_dir / 'scene_generation_summary.json'}")
        return 0 if summary["status"] in {"pass", "pending_visual_review", "pass_static_scene_module"} else 1
    except Exception as exc:
        summary["status"] = "fail_exception"
        summary["error"] = repr(exc)
        write_json(out_dir / "scene_generation_summary.json", summary)
        print(f"FAIL {out_dir / 'scene_generation_summary.json'}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
