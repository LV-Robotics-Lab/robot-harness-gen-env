#!/usr/bin/env python3
"""MCP-lite command line tools for the tabletop placement harness."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generate_scene.observation_agent import observe_scene_with_provider
from generate_scene.schemas import read_json, validate_placement_spec, write_json
from generate_scene.asset_catalog import load_asset_catalog


def list_assets(catalog_path: Path) -> dict[str, Any]:
    catalog = load_asset_catalog(catalog_path)
    return {
        "assets": [
            {
                "asset_id": entry["asset_id"],
                "semantic_name": entry.get("semantic_name"),
                "aliases": entry.get("aliases", []),
                "default_model_id": entry.get("default_model_id", 0),
                "available_model_ids": entry.get("available_model_ids", []),
            }
            for entry in catalog.get("entries", [])
        ]
    }


def get_asset_metadata(catalog_path: Path, asset_id: str) -> dict[str, Any]:
    catalog = load_asset_catalog(catalog_path)
    for entry in catalog.get("entries", []):
        if entry.get("asset_id") == asset_id:
            return entry
    raise KeyError(f"Unknown asset_id: {asset_id}")


def run_robotwin_smoke(
    *,
    robotwin_root: Path,
    placement: Path,
    out_dir: Path,
    task_config: str,
    seed: int,
    settle_steps: int,
    video_frames: int,
    fps: int,
    scene_module: Path | None = None,
    python_executable: str | None = None,
    timeout_sec: int = 420,
) -> dict[str, Any]:
    runner_python = python_executable or os.environ.get("ROBOTWIN_PYTHON") or sys.executable
    cmd = [
        runner_python,
        str(REPO_ROOT / "generate_scene" / "run_robotwin_placement_smoke.py"),
        "--robotwin-root",
        str(robotwin_root),
        "--placement",
        str(placement),
        "--out-dir",
        str(out_dir),
        "--task-config",
        task_config,
        "--seed",
        str(seed),
        "--settle-steps",
        str(settle_steps),
        "--video-frames",
        str(video_frames),
        "--fps",
        str(fps),
    ]
    if scene_module is not None:
        cmd.extend(["--scene-module", str(scene_module)])
    env = os.environ.copy()
    python_bin = str(Path(runner_python).expanduser().resolve().parent)
    env["PATH"] = python_bin + os.pathsep + env.get("PATH", "")
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            check=False,
            text=True,
            capture_output=True,
            env=env,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        report_path = out_dir / "smoke_report.json"
        report = read_json(report_path) if report_path.exists() else {}
        report.update(
            {
                "status": "fail_timeout",
                "stage": "robotwin_smoke",
                "command": cmd,
                "returncode": None,
                "timeout_sec": timeout_sec,
                "stdout": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
                "stderr_tail": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
            }
        )
        return report
    report_path = out_dir / "smoke_report.json"
    report = read_json(report_path) if report_path.exists() else {}
    report["command"] = cmd
    report["returncode"] = completed.returncode
    report["stdout"] = completed.stdout
    report["stderr_tail"] = completed.stderr[-4000:]
    if completed.returncode != 0:
        report.setdefault("status", "fail")
    return report


def get_smoke_artifacts(smoke_dir: Path) -> dict[str, Any]:
    return {
        "smoke_dir": str(smoke_dir),
        "report": str(smoke_dir / "smoke_report.json"),
        "head_camera": str(smoke_dir / "head_camera.png"),
        "observer_camera": str(smoke_dir / "observer_camera.png"),
        "observer_video": str(smoke_dir / "observer_camera.mp4"),
        "exists": {
            "report": (smoke_dir / "smoke_report.json").exists(),
            "head_camera": (smoke_dir / "head_camera.png").exists(),
            "observer_camera": (smoke_dir / "observer_camera.png").exists(),
            "observer_video": (smoke_dir / "observer_camera.mp4").exists(),
        },
    }


def visual_review(smoke_dir: Path, prompt: str, mode: str = "required") -> dict[str, Any]:
    if mode in {"moonshot", "openai"}:
        return observe_scene_with_provider(smoke_dir=smoke_dir, prompt=prompt, model_provider=mode)

    artifacts = get_smoke_artifacts(smoke_dir)
    missing = [name for name, exists in artifacts["exists"].items() if name != "observer_video" and not exists]
    if missing:
        status = "fail_missing_artifacts"
    elif mode == "artifact_only":
        status = "pending_visual_critic_artifacts_only"
    else:
        status = "pending_human_or_vlm_visual_review"
    return {
        "schema_version": "robotwin.tabletop_visual_review.v0",
        "prompt": prompt,
        "status": status,
        "review_mode": mode,
        "artifacts": artifacts,
        "missing_required_artifacts": missing,
        "notes": [
            "Artifact existence is not a semantic visual review.",
            "A human, Codex visual reference review, or external VLM must check object identity, orientation, table contact, penetration, occlusion, and prompt match before the scene is accepted.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP-lite tools for RoboTwin tabletop placement.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_assets = sub.add_parser("list-assets")
    p_assets.add_argument("--asset-catalog", required=True)

    p_asset = sub.add_parser("get-asset-metadata")
    p_asset.add_argument("--asset-catalog", required=True)
    p_asset.add_argument("--asset-id", required=True)

    p_validate = sub.add_parser("validate-placement")
    p_validate.add_argument("--placement", required=True)
    p_validate.add_argument("--asset-catalog", required=True)
    p_validate.add_argument("--robotwin-root")
    p_validate.add_argument("--out")

    p_smoke = sub.add_parser("run-smoke")
    p_smoke.add_argument("--robotwin-root", required=True)
    p_smoke.add_argument("--placement", required=True)
    p_smoke.add_argument("--scene-module")
    p_smoke.add_argument("--out-dir", required=True)
    p_smoke.add_argument("--task-config", default="demo_smoke")
    p_smoke.add_argument("--seed", type=int, default=0)
    p_smoke.add_argument("--settle-steps", type=int, default=240)
    p_smoke.add_argument("--video-frames", type=int, default=60)
    p_smoke.add_argument("--fps", type=int, default=15)
    p_smoke.add_argument("--python-executable")
    p_smoke.add_argument("--timeout-sec", type=int, default=420)

    p_artifacts = sub.add_parser("get-smoke-artifacts")
    p_artifacts.add_argument("--smoke-dir", required=True)

    p_visual = sub.add_parser("visual-review")
    p_visual.add_argument("--smoke-dir", required=True)
    p_visual.add_argument("--prompt", required=True)
    p_visual.add_argument("--mode", choices=["required", "artifact_only", "moonshot", "openai"], default="required")

    args = parser.parse_args()

    if args.command == "list-assets":
        result = list_assets(Path(args.asset_catalog))
    elif args.command == "get-asset-metadata":
        result = get_asset_metadata(Path(args.asset_catalog), args.asset_id)
    elif args.command == "validate-placement":
        result = validate_placement_spec(read_json(Path(args.placement)), load_asset_catalog(Path(args.asset_catalog)), robotwin_root=args.robotwin_root)
        if args.out:
            write_json(Path(args.out), result)
    elif args.command == "run-smoke":
        result = run_robotwin_smoke(
            robotwin_root=Path(args.robotwin_root).expanduser(),
            placement=Path(args.placement),
            out_dir=Path(args.out_dir),
            task_config=args.task_config,
            seed=args.seed,
            settle_steps=args.settle_steps,
            video_frames=args.video_frames,
            fps=args.fps,
            scene_module=Path(args.scene_module) if args.scene_module else None,
            python_executable=args.python_executable,
            timeout_sec=args.timeout_sec,
        )
    elif args.command == "get-smoke-artifacts":
        result = get_smoke_artifacts(Path(args.smoke_dir))
    elif args.command == "visual-review":
        result = visual_review(Path(args.smoke_dir), args.prompt, mode=args.mode)
    else:
        raise AssertionError(args.command)

    print(json.dumps(result, indent=2))
    return 0 if not str(result.get("status", "")).startswith("fail") else 1


if __name__ == "__main__":
    raise SystemExit(main())
