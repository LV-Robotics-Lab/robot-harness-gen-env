#!/usr/bin/env python3
"""Compile a bilingual prompt matrix and optionally replay one or all seeds in RoboTwin."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scene_gen.asset_generator import ensure_assets_for_scene
from scene_gen.builder import build_scene_package
from scene_gen.catalog import load_catalog
from scene_gen.parser import parse_rule_based
from scene_gen.solver import SceneSolveError, solve_scene
from scene_gen.validator import validate_resolved_scene


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_metrics(runtime_dir: Path) -> dict[str, Any]:
    evidence_path = runtime_dir / "runtime_evidence.json"
    validation_path = runtime_dir / "runtime_validation_report.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    return {
        "status": validation.get("status"),
        "fail_count": validation.get("fail_count"),
        "video_frame_count": evidence.get("video_frame_count"),
        "unique_video_frame_count": evidence.get("unique_video_frame_count"),
        "objects": {
            name: {
                key: values.get(key)
                for key in (
                    "translation_drift_m",
                    "rotation_drift_deg",
                    "support_contact_fraction",
                    "support_target",
                    "unexpected_contact_fraction",
                    "support_footprint_margin_m",
                    "inside_contained",
                    "penetration_count",
                    "still_moving",
                    "dropped",
                    "articulation_max_abs_error",
                    "visible_pixels",
                )
            }
            for name, values in (evidence.get("objects") or {}).items()
        },
        "artifacts": {
            name: {
                "path": str(path),
                "sha256": file_sha256(path),
            }
            for name, path in {
                "runtime_evidence": evidence_path,
                "runtime_validation": validation_path,
                "head": runtime_dir / "preview_head.png",
                "world_left": runtime_dir / "preview_world_left.png",
                "world_right": runtime_dir / "preview_world_right.png",
                "observer_start": runtime_dir / "observer_start.png",
                "observer_mid": runtime_dir / "observer_mid.png",
                "observer_end": runtime_dir / "observer_end.png",
                "video": runtime_dir / "observer_runtime.mp4",
            }.items()
            if path.is_file()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--matrix",
        default="tests/fixtures/prompt_matrix.json",
    )
    parser.add_argument("--asset-catalog", required=True)
    parser.add_argument("--generated-objects-root", required=True)
    parser.add_argument("--out-root", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--runtime", action="store_true")
    parser.add_argument("--runtime-all-seeds", action="store_true")
    parser.add_argument("--robotwin-root")
    parser.add_argument("--task-config", default="demo_clean")
    parser.add_argument("--settle-steps", type=int, default=900)
    parser.add_argument("--contact-window-steps", type=int, default=120)
    parser.add_argument("--video-frames", type=int, default=120)
    parser.add_argument("--fps", type=int, default=12)
    args = parser.parse_args()

    if args.runtime and not args.robotwin_root:
        parser.error("--robotwin-root is required with --runtime")

    project_root = Path(__file__).resolve().parents[1]
    matrix_path = Path(args.matrix).expanduser().resolve()
    catalog_path = Path(args.asset_catalog).expanduser().resolve()
    generated_root = Path(args.generated_objects_root).expanduser().resolve()
    out_root = Path(args.out_root).expanduser().resolve()
    report_path = Path(args.report).expanduser().resolve()
    robotwin_root = (
        Path(args.robotwin_root).expanduser().resolve() if args.robotwin_root else None
    )
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    seeds = tuple(int(seed) for seed in matrix["seeds"])
    base_catalog = load_catalog(catalog_path)
    outcomes: list[dict[str, Any]] = []

    for case in matrix["cases"]:
        case_id = str(case["id"])
        prompt = str(case["prompt"])
        expectation = str(case.get("expect", "pass"))
        for seed_index, seed in enumerate(seeds):
            outcome: dict[str, Any] = {
                "case_id": case_id,
                "prompt": prompt,
                "seed": seed,
                "expectation": expectation,
                "status": "fail",
            }
            try:
                spec = parse_rule_based(prompt, seed=seed)
                scene_dir = out_root / case_id / f"seed_{seed:06d}" / spec.scene_id
                scene_dir.mkdir(parents=True, exist_ok=True)
                effective, generation = ensure_assets_for_scene(
                    spec,
                    base_catalog,
                    objects_root=generated_root,
                )
                write_json(scene_dir / "asset_generation_report.json", generation)
                write_json(
                    scene_dir / "effective_asset_catalog.json",
                    effective.canonical_dict(),
                )
                resolved = solve_scene(spec, effective)
                build_scene_package(spec, resolved, scene_dir)
                static = validate_resolved_scene(
                    resolved,
                    catalog=effective,
                    package_root=scene_dir,
                    require_runtime=False,
                )
                write_json(scene_dir / "validation_report.json", static)
                outcome.update(
                    {
                        "scene_id": spec.scene_id,
                        "scene_dir": str(scene_dir),
                        "resolved_scene_sha256": resolved.digest(),
                        "manifest_sha256": file_sha256(
                            scene_dir / "package_manifest.json"
                        ),
                        "static_status": static["status"],
                        "static_fail_count": static["fail_count"],
                        "asset_generation": generation,
                        "objects": [
                            {
                                "object_id": item.object_id,
                                "asset_id": item.asset_id,
                                "asset_provenance": item.asset_provenance,
                                "dimensions_m": item.dimensions_m,
                                "uniform_scale_factor": item.uniform_scale_factor,
                            }
                            for item in resolved.objects
                        ],
                    }
                )
                if static["fail_count"]:
                    outcome["failure_stage"] = "static_validation"
                    outcomes.append(outcome)
                    continue

                run_runtime = expectation == "pass" and args.runtime and (
                    args.runtime_all_seeds or seed_index == 0
                )
                if run_runtime:
                    runtime_dir = scene_dir / "runtime"
                    runtime_dir.mkdir(parents=True, exist_ok=True)
                    command = [
                        sys.executable,
                        str(project_root / "script" / "run_scene_runtime.py"),
                        "--robotwin-root",
                        str(robotwin_root),
                        "--resolved-scene",
                        str(scene_dir / "resolved_scene.json"),
                        "--asset-catalog",
                        str(scene_dir / "effective_asset_catalog.json"),
                        "--out-dir",
                        str(runtime_dir),
                        "--task-config",
                        args.task_config,
                        "--settle-steps",
                        str(args.settle_steps),
                        "--contact-window-steps",
                        str(args.contact_window_steps),
                        "--video-frames",
                        str(args.video_frames),
                        "--fps",
                        str(args.fps),
                    ]
                    environment = os.environ.copy()
                    environment["PYTHONPATH"] = str(project_root)
                    with (runtime_dir / "runtime.log").open(
                        "w",
                        encoding="utf-8",
                    ) as log:
                        completed = subprocess.run(
                            command,
                            cwd=project_root,
                            env=environment,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            check=False,
                        )
                    outcome["runtime_exit_code"] = completed.returncode
                    outcome["runtime"] = runtime_metrics(runtime_dir)
                    if outcome["runtime"]["status"] != "pass":
                        outcome["failure_stage"] = "runtime_validation"
                        outcomes.append(outcome)
                        continue
                else:
                    outcome["runtime"] = {"status": "not_requested"}
                if expectation == "reject":
                    outcome["failure_stage"] = "unexpected_acceptance"
                else:
                    outcome["status"] = "pass"
            except Exception as error:
                expected_rejection = (
                    expectation == "reject" and isinstance(error, SceneSolveError)
                )
                outcome["failure_stage"] = (
                    "expected_rejection" if expected_rejection else "exception"
                )
                outcome["error"] = {
                    "type": type(error).__name__,
                    "message": str(error),
                }
                if expected_rejection:
                    outcome["status"] = "pass"
                    outcome["runtime"] = {"status": "not_requested"}
            outcomes.append(outcome)
            write_json(
                report_path,
                {
                    "schema_version": "robotwin.prompt_matrix_report.v1",
                    "complete": False,
                    "outcomes": outcomes,
                },
            )

    passed = sum(item["status"] == "pass" for item in outcomes)
    runtime_outcomes = [
        item
        for item in outcomes
        if (item.get("runtime") or {}).get("status") in {"pass", "fail"}
    ]
    report = {
        "schema_version": "robotwin.prompt_matrix_report.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "matrix": str(matrix_path),
        "matrix_sha256": file_sha256(matrix_path),
        "asset_catalog": str(catalog_path),
        "asset_catalog_sha256": base_catalog.digest(),
        "runtime_requested": args.runtime,
        "runtime_all_seeds": args.runtime_all_seeds,
        "case_count": len(matrix["cases"]),
        "seed_count_per_case": len(seeds),
        "total": len(outcomes),
        "pass_count": passed,
        "fail_count": len(outcomes) - passed,
        "runtime_count": len(runtime_outcomes),
        "runtime_pass_count": sum(
            item["runtime"]["status"] == "pass" for item in runtime_outcomes
        ),
        "status": "pass" if passed == len(outcomes) else "fail",
        "complete": len(outcomes) == len(matrix["cases"]) * len(seeds),
        "outcomes": outcomes,
    }
    write_json(report_path, report)
    print(
        f"{report['status'].upper()} pass={passed}/{len(outcomes)} "
        f"runtime={report['runtime_pass_count']}/{report['runtime_count']} "
        f"report={report_path}"
    )
    return 0 if report["status"] == "pass" and report["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
