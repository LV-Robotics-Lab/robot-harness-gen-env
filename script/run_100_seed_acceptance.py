#!/usr/bin/env python3
"""Generate and validate a fixed-seed text-to-RoboTwin acceptance batch."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from scene_gen.acceptance import summarize_acceptance
from scene_gen.builder import build_scene_package
from scene_gen.catalog import load_catalog
from scene_gen.parser import parse_rule_based
from scene_gen.solver import SceneSolveError, solve_scene
from scene_gen.validator import validate_resolved_scene


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_reusable_runtime(path: Path, resolved_sha256: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if report.get("resolved_scene_sha256") != resolved_sha256:
        return None
    return report if report.get("status") == "pass" else None


def git_head(path: Path) -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def seed_scene_dir(out_root: Path, seed: int, scene_id: str) -> Path:
    return out_root / f"seed_{seed:06d}" / scene_id


def evidence_record(scene_dir: Path, out_root: Path, *, runtime_required: bool) -> tuple[dict[str, Any], bool]:
    relative = scene_dir.relative_to(out_root)
    paths = {
        "scene_spec": scene_dir / "scene_spec.json",
        "resolved_scene": scene_dir / "resolved_scene.json",
        "package_manifest": scene_dir / "package_manifest.json",
        "static_validation": scene_dir / "validation_report.json",
    }
    if runtime_required:
        paths.update(
            {
                "runtime_evidence": scene_dir / "runtime" / "runtime_evidence.json",
                "runtime_validation": scene_dir / "runtime" / "runtime_validation_report.json",
            }
        )
    evidence: dict[str, Any] = {}
    for name, path in paths.items():
        evidence[name] = {
            "path": str(relative / path.relative_to(scene_dir)),
            "sha256": file_sha256(path) if path.is_file() else None,
        }
    video = scene_dir / "runtime" / "observer_runtime.mp4"
    if video.is_file():
        evidence["observer_video"] = {
            "path": str(relative / video.relative_to(scene_dir)),
            "sha256": file_sha256(video),
        }
    return evidence, all(item["sha256"] for name, item in evidence.items() if name != "observer_video")


def batch_summary(
    outcomes: list[dict[str, Any]],
    *,
    prompt: str,
    seed_start: int,
    seed_count: int,
    catalog_sha256: str,
    runtime_required: bool,
    minimum_pass_rate: float,
    generator_commit: str | None,
    robotwin_commit: str | None,
    complete: bool,
) -> dict[str, Any]:
    passing = [item for item in outcomes if item.get("status") == "pass"]
    return {
        "schema_version": "robotwin.scene_acceptance.v2",
        "prompt": prompt,
        "seed_start": seed_start,
        "requested_seed_count": seed_count,
        "asset_catalog_sha256": catalog_sha256,
        "runtime_required": runtime_required,
        "minimum_pass_rate": minimum_pass_rate,
        "generator_commit": generator_commit,
        "robotwin_commit": robotwin_commit,
        **summarize_acceptance(outcomes, minimum_pass_rate=minimum_pass_rate),
        "retained_scene_count": len({item.get("scene_dir_relative") for item in passing}),
        "unique_resolved_scene_count": len({item.get("resolved_scene_sha256") for item in passing}),
        "passing_evidence_retained": bool(passing) and all(item.get("evidence_retained") for item in passing),
        "outcomes": outcomes,
        "complete": complete,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--prompt",
        default="Place a red can to the left of a plastic basket near the center.",
    )
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--seed-count", type=int, default=100)
    parser.add_argument("--asset-catalog", required=True)
    parser.add_argument("--out-root", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--minimum-pass-rate", type=float, default=0.95)
    parser.add_argument("--runtime", action="store_true")
    parser.add_argument("--robotwin-root")
    parser.add_argument("--generator-commit")
    parser.add_argument("--task-config", default="demo_clean")
    parser.add_argument("--precheck-steps", type=int, default=0)
    parser.add_argument("--settle-steps", type=int, default=180)
    parser.add_argument("--video-seeds", default="0,17,99")
    parser.add_argument("--video-frames", type=int, default=120)
    parser.add_argument("--min-visible-pixels", type=int, default=64)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()

    if args.seed_count <= 0:
        parser.error("--seed-count must be positive")
    if args.runtime and not args.robotwin_root:
        parser.error("--robotwin-root is required with --runtime")

    project_root = Path(__file__).resolve().parents[1]
    catalog_path = Path(args.asset_catalog).expanduser().resolve()
    catalog = load_catalog(catalog_path)
    out_root = Path(args.out_root).expanduser().resolve()
    report_path = Path(args.report).expanduser().resolve()
    video_seeds = {int(value) for value in args.video_seeds.split(",") if value.strip()}
    outcomes: list[dict[str, Any]] = []
    generator_commit = args.generator_commit or git_head(project_root)
    robotwin_commit = git_head(Path(args.robotwin_root).expanduser().resolve()) if args.robotwin_root else None

    for seed in range(args.seed_start, args.seed_start + args.seed_count):
        spec = parse_rule_based(args.prompt, seed=seed)
        scene_dir = seed_scene_dir(out_root, seed, spec.scene_id)
        outcome: dict[str, Any] = {
            "seed": seed,
            "scene_id": spec.scene_id,
            "scene_dir": str(scene_dir),
            "scene_dir_relative": str(scene_dir.relative_to(out_root)),
            "status": "fail",
        }
        try:
            resolved = solve_scene(spec, catalog)
        except SceneSolveError as error:
            outcome["failure_stage"] = "solver"
            outcome["failure"] = error.report
            outcomes.append(outcome)
            print(f"FAIL seed={seed} stage=solver", flush=True)
            continue

        manifest = build_scene_package(spec, resolved, scene_dir)
        static_report = validate_resolved_scene(
            resolved,
            catalog=catalog,
            package_root=scene_dir,
            require_runtime=False,
        )
        write_json(scene_dir / "validation_report.json", static_report)
        outcome.update(
            {
                "resolved_scene_sha256": resolved.digest(),
                "package_manifest_sha256": file_sha256(scene_dir / "package_manifest.json"),
                "static_status": "pass" if static_report["fail_count"] == 0 else "fail",
                "static_report_status": static_report["status"],
                "static_fail_count": static_report["fail_count"],
            }
        )
        if static_report["fail_count"]:
            outcome["failure_stage"] = "static_validation"
            outcomes.append(outcome)
            print(f"FAIL seed={seed} stage=static_validation", flush=True)
            continue

        if not args.runtime:
            outcome["status"] = "pass"
            outcome["runtime_status"] = "not_requested"
            evidence, retained = evidence_record(scene_dir, out_root, runtime_required=False)
            outcome["evidence"] = evidence
            outcome["evidence_retained"] = retained
            outcomes.append(outcome)
            print(f"PASS seed={seed} static", flush=True)
            continue

        runtime_dir = scene_dir / "runtime"
        runtime_validation_path = runtime_dir / "runtime_validation_report.json"
        reusable = None if args.no_resume else load_reusable_runtime(
            runtime_validation_path, resolved.digest()
        )
        if reusable is None:
            command = [
                sys.executable,
                str(project_root / "script" / "run_scene_runtime.py"),
                "--robotwin-root",
                str(Path(args.robotwin_root).expanduser().resolve()),
                "--resolved-scene",
                str(scene_dir / "resolved_scene.json"),
                "--asset-catalog",
                str(catalog_path),
                "--out-dir",
                str(runtime_dir),
                "--task-config",
                args.task_config,
                "--precheck-steps",
                str(args.precheck_steps),
                "--settle-steps",
                str(args.settle_steps),
                "--video-frames",
                str(args.video_frames if seed in video_seeds else 0),
                "--min-visible-pixels",
                str(args.min_visible_pixels),
            ]
            runtime_dir.mkdir(parents=True, exist_ok=True)
            environment = os.environ.copy()
            environment["PYTHONPATH"] = os.pathsep.join(
                [str(project_root), environment.get("PYTHONPATH", "")]
            ).rstrip(os.pathsep)
            with (runtime_dir / "runtime.log").open("w", encoding="utf-8") as log:
                completed = subprocess.run(
                    command,
                    cwd=project_root,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            outcome["runtime_exit_code"] = completed.returncode
        else:
            outcome["runtime_exit_code"] = 0
            outcome["runtime_reused"] = True

        try:
            runtime_report = json.loads(runtime_validation_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            outcome["runtime_status"] = "missing_or_invalid"
            outcome["failure_stage"] = "runtime_validation"
            outcome["failure"] = repr(error)
        else:
            outcome["runtime_status"] = runtime_report.get("status")
            outcome["final_validation_status"] = runtime_report.get("status")
            outcome["runtime_fail_count"] = runtime_report.get("fail_count")
            if runtime_report.get("status") == "pass":
                outcome["status"] = "pass"
            else:
                outcome["failure_stage"] = "runtime_validation"
        evidence, retained = evidence_record(scene_dir, out_root, runtime_required=True)
        outcome["evidence"] = evidence
        outcome["evidence_retained"] = retained
        outcomes.append(outcome)
        print(
            f"{outcome['status'].upper()} seed={seed} runtime={outcome.get('runtime_status')}",
            flush=True,
        )

        partial = batch_summary(
            outcomes,
            prompt=args.prompt,
            seed_start=args.seed_start,
            seed_count=args.seed_count,
            catalog_sha256=catalog.digest(),
            runtime_required=args.runtime,
            minimum_pass_rate=args.minimum_pass_rate,
            generator_commit=generator_commit,
            robotwin_commit=robotwin_commit,
            complete=False,
        )
        write_json(report_path, partial)

    summary = batch_summary(
        outcomes,
        prompt=args.prompt,
        seed_start=args.seed_start,
        seed_count=args.seed_count,
        catalog_sha256=catalog.digest(),
        runtime_required=args.runtime,
        minimum_pass_rate=args.minimum_pass_rate,
        generator_commit=generator_commit,
        robotwin_commit=robotwin_commit,
        complete=len(outcomes) == args.seed_count,
    )
    write_json(report_path, summary)
    print(
        f"{summary['status'].upper()} pass={summary['pass_count']}/{summary['total']} "
        f"rate={summary['pass_rate']:.3f} report={report_path}",
        flush=True,
    )
    return 0 if summary["status"] == "pass" and summary["complete"] and summary["passing_evidence_retained"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
