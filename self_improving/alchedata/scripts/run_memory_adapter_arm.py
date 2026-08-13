#!/usr/bin/env python3
"""Run one matched memory/no-memory RGB adapter-selection arm."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pose_conditioned_trajectory_policy import read_json, sha256_file, write_json


ROOT = Path(__file__).resolve().parents[1]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def select_adapter(
    mode: str,
    memory_path: Path | None,
    task_id: str,
    checkpoint_sha256: str,
    default_adapter: str,
) -> tuple[str, dict[str, Any]]:
    if mode == "no-memory":
        if memory_path is not None:
            raise ValueError("no-memory mode must not receive a memory path")
        return default_adapter, {
            "memory_available": False,
            "selection_rule": "declared_default_adapter",
            "memory": None,
        }
    if memory_path is None:
        raise ValueError("read-memory mode requires --memory")
    memory = read_json(memory_path)
    if memory["status"] != "active_failure_memory":
        raise ValueError("Failure memory is not active")
    if memory["applicability"]["task_id"] != task_id:
        raise ValueError("Failure memory task does not match this arm")
    if memory["applicability"]["checkpoint_sha256"] != checkpoint_sha256:
        raise ValueError("Failure memory checkpoint does not match this arm")
    selected = str(memory["recommendation"]["runtime_color_adapter"])
    return selected, {
        "memory_available": True,
        "selection_rule": "matching_active_failure_memory_recommendation",
        "memory": str(memory_path),
        "memory_sha256": sha256_file(memory_path),
        "memory_id": memory["memory_id"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["no-memory", "read-memory"], required=True)
    parser.add_argument("--memory")
    parser.add_argument("--robotwin-root", required=True)
    parser.add_argument("--placement", required=True)
    parser.add_argument("--checkpoint-dir", required=True)
    parser.add_argument("--training-collection-report", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--task-id", default="task_apple_plate")
    parser.add_argument("--default-adapter", default="swap_red_blue")
    parser.add_argument("--seed", action="append", type=int, dest="seeds")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_dir = Path(args.checkpoint_dir).expanduser().resolve()
    checkpoint_path = checkpoint_dir / "policy_best.ckpt"
    stats_path = checkpoint_dir / "dataset_stats.pkl"
    checkpoint_sha256 = sha256_file(checkpoint_path)
    stats_sha256 = sha256_file(stats_path)
    seeds = args.seeds or [4, 5, 6]
    memory_path = Path(args.memory).expanduser().resolve() if args.memory else None
    adapter, selection = select_adapter(
        args.mode,
        memory_path,
        args.task_id,
        checkpoint_sha256,
        args.default_adapter,
    )
    evaluator_dir = out_dir / "evaluation"
    controller_path = out_dir / "memory_controller.json"
    fixed_protocol = {
        "task_id": args.task_id,
        "task_config": "demo_clean",
        "placement": str(Path(args.placement).expanduser().resolve()),
        "placement_sha256": sha256_file(Path(args.placement).expanduser().resolve()),
        "seeds": seeds,
        "checkpoint": str(checkpoint_path),
        "checkpoint_sha256": checkpoint_sha256,
        "dataset_stats": str(stats_path),
        "dataset_stats_sha256": stats_sha256,
        "runtime_camera_source": "head_camera",
        "chunk_size": 161,
        "action_execution": "full-chunk",
        "max_steps": 200,
        "evaluator": "scripts/run_generated_act_eval_smoke.py",
    }
    controller: dict[str, Any] = {
        "schema_version": "alchedata.memory_adapter_controller.v0",
        "status": "started",
        "started_at": utc_now(),
        "mode": args.mode,
        "default_adapter": args.default_adapter,
        "selected_adapter": adapter,
        "selection": selection,
        "fixed_protocol": fixed_protocol,
        "claim_boundary": (
            "This controller tests whether a matching structured failure memory changes one harness decision. "
            "It does not change model weights, infer a new policy, or establish general memory benefit."
        ),
    }
    write_json(controller_path, controller)

    command = [
        sys.executable,
        str(ROOT / "scripts" / "run_generated_act_eval_smoke.py"),
        "--robotwin-root",
        str(Path(args.robotwin_root).expanduser().resolve()),
        "--placement",
        str(Path(args.placement).expanduser().resolve()),
        "--task-id",
        args.task_id,
        "--checkpoint-dir",
        str(checkpoint_dir),
        "--checkpoint-name",
        "policy_best.ckpt",
        "--out-dir",
        str(evaluator_dir),
        "--task-config",
        "demo_clean",
        "--max-steps",
        "200",
        "--fps",
        "8",
        "--camera-width",
        "96",
        "--camera-height",
        "72",
        "--device",
        "cuda:0",
        "--hidden-dim",
        "64",
        "--dim-feedforward",
        "256",
        "--chunk-size",
        "161",
        "--action-execution",
        "full-chunk",
        "--runtime-camera-source",
        "head_camera",
        "--runtime-color-adapter",
        adapter,
        "--training-collection-report",
        str(Path(args.training_collection_report).expanduser().resolve()),
    ]
    for seed in seeds:
        command.extend(["--seed", str(seed)])
    stdout_path = out_dir / "evaluator_stdout.log"
    stderr_path = out_dir / "evaluator_stderr.log"
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        result = subprocess.run(command, cwd=ROOT, stdout=stdout, stderr=stderr, check=False)

    report_path = evaluator_dir / "evaluate_report.json"
    report = read_json(report_path) if report_path.is_file() else None
    controller.update(
        {
            "status": "completed" if result.returncode == 0 and report else "blocked",
            "finished_at": utc_now(),
            "evaluator_returncode": result.returncode,
            "evaluator_command": command,
            "evaluator_report": str(report_path),
            "evaluator_report_sha256": sha256_file(report_path) if report_path.is_file() else None,
            "execution_count": report.get("execution_count") if report else None,
            "success_count": report.get("success_count") if report else None,
            "episode_count": report.get("episode_count") if report else None,
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
        }
    )
    write_json(controller_path, controller)
    print(
        json.dumps(
            {
                "status": controller["status"],
                "mode": args.mode,
                "selected_adapter": adapter,
                "success_count": controller["success_count"],
                "episode_count": controller["episode_count"],
            }
        )
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
