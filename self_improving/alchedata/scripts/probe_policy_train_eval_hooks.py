#!/usr/bin/env python3
"""Probe RoboTwin policy train/eval entrypoints against current generated collections."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = ROOT / "runs" / "policy_train_eval_entrypoint_probe"
GENERATED_COLLECTIONS = [
    ROOT / "runs" / "generated_collect_apple_plate_action_repair" / "collection_report.json",
    ROOT / "runs" / "generated_collect_can_basket_action_repair" / "collection_report.json",
]
DEFAULT_ACT_HDF5_DIR = ROOT / "runs" / "act_hdf5_generated_smoke"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_command(name: str, command: list[str], cwd: Path, out_dir: Path, timeout: int) -> dict[str, Any]:
    stdout_path = out_dir / f"{name}_stdout.log"
    stderr_path = out_dir / f"{name}_stderr.log"
    started_at = datetime.now(timezone.utc).isoformat()
    env = os.environ.copy()
    python_bin_dir = str(Path(sys.executable).resolve().parent)
    env["PATH"] = python_bin_dir + os.pathsep + env.get("PATH", "")
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=env,
        )
        timed_out = False
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = None
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", errors="replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", errors="replace")
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    return {
        "name": name,
        "command": command,
        "cwd": str(cwd),
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "timeout_s": timeout,
        "timed_out": timed_out,
        "returncode": returncode,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "stdout_tail": stdout[-2000:],
        "stderr_tail": stderr[-2000:],
    }


def collection_summary(path: Path) -> dict[str, Any]:
    report = read_json(path)
    run_dir = path.parent
    hdf5_files = sorted(str(item) for item in run_dir.rglob("*.hdf5"))
    return {
        "path": str(path),
        "status": report.get("status"),
        "task_id": report.get("task_id"),
        "episode_count": report.get("episode_count"),
        "policy_execution": report.get("policy_execution"),
        "learned_policy_training": report.get("learned_policy_training"),
        "learned_policy_evaluation": report.get("learned_policy_evaluation"),
        "dataset_manifest": report.get("dataset_manifest"),
        "hdf5_file_count": len(hdf5_files),
        "hdf5_files": hdf5_files[:10],
    }


def act_hdf5_summary(path: Path) -> dict[str, Any]:
    conversion_report = path / "conversion_report.json"
    load_data_report = path / "load_data_report.json"
    hdf5_files = sorted(str(item) for item in (path / "data").glob("*.hdf5"))
    summary: dict[str, Any] = {
        "path": str(path),
        "conversion_report": str(conversion_report),
        "load_data_report": str(load_data_report),
        "hdf5_file_count": len(hdf5_files),
        "hdf5_files": hdf5_files[:10],
    }
    if conversion_report.exists():
        report = read_json(conversion_report)
        summary.update(
            {
                "conversion_status": report.get("status"),
                "pass_count": report.get("pass_count"),
                "fail_count": report.get("fail_count"),
                "skip_count": report.get("skip_count"),
                "act_sim_task_name": report.get("act_sim_task_name"),
                "act_sim_task_config_json": report.get("act_sim_task_config_json"),
            }
        )
    else:
        summary["conversion_status"] = "missing_conversion_report"
    if load_data_report.exists():
        report = read_json(load_data_report)
        summary.update(
            {
                "loader_status": report.get("status"),
                "loader_num_episodes": report.get("num_episodes"),
                "loader_batch_item_shapes": report.get("batch_item_shapes"),
            }
        )
    else:
        summary["loader_status"] = "missing_load_data_report"
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe policy train/eval wiring against generated collection artifacts.")
    parser.add_argument("--robotwin-root", default=str(ROOT / "external" / "RoboTwin"))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--task-name", default="task_apple_plate")
    parser.add_argument("--task-config", default="demo_clean")
    parser.add_argument("--expert-data-num", default="3")
    parser.add_argument("--act-hdf5-dir", default=str(DEFAULT_ACT_HDF5_DIR))
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    robotwin_root = Path(args.robotwin_root).expanduser().resolve()
    act_root = robotwin_root / "policy" / "ACT"
    report: dict[str, Any] = {
        "schema_version": "alchedata.policy_train_eval_probe.v0",
        "status": "started",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "robotwin_root": str(robotwin_root),
        "out_dir": str(out_dir),
        "task_name": args.task_name,
        "task_config": args.task_config,
        "expert_data_num": args.expert_data_num,
        "entrypoints": {
            "act_process_data": str(act_root / "process_data.sh"),
            "act_train": str(act_root / "train.sh"),
            "act_eval": str(act_root / "eval.sh"),
            "robotwin_eval_policy": str(robotwin_root / "script" / "eval_policy.py"),
        },
        "generated_collections": [collection_summary(path) for path in GENERATED_COLLECTIONS if path.exists()],
        "act_hdf5_adapter": act_hdf5_summary(Path(args.act_hdf5_dir).expanduser().resolve()),
        "commands": [],
        "runtime": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "cwd": os.getcwd(),
        },
        "claim_boundary": (
            "This probes whether current generated collection artifacts are wired to RoboTwin policy train/eval entrypoints. "
            "The ACT HDF5 adapter and loader smoke are checked separately; full train/eval is expected to fail until ACT "
            "task config, dependencies, env registration, and checkpoint wiring exist."
        ),
    }
    write_json(out_dir / "probe_report.json", report)

    missing_entrypoints = [name for name, value in report["entrypoints"].items() if not Path(value).exists()]
    if missing_entrypoints:
        report.update(
            {
                "status": "blocked_policy_entrypoints_missing",
                "blocking_reasons": [f"missing entrypoint: {name}" for name in missing_entrypoints],
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        write_json(out_dir / "probe_report.json", report)
        print(json.dumps({"status": report["status"], "report": str(out_dir / "probe_report.json")}, ensure_ascii=False))
        return 0

    process_cmd = ["bash", "process_data.sh", args.task_name, args.task_config, args.expert_data_num]
    report["commands"].append(run_command("act_process_data", process_cmd, act_root, out_dir, args.timeout))

    train_cmd = [
        sys.executable,
        "imitate_episodes.py",
        "--task_name",
        f"sim-{args.task_name}-{args.task_config}-{args.expert_data_num}",
        "--ckpt_dir",
        str(out_dir / "act_ckpt_probe"),
        "--policy_class",
        "ACT",
        "--kl_weight",
        "10",
        "--chunk_size",
        "50",
        "--hidden_dim",
        "512",
        "--batch_size",
        "2",
        "--dim_feedforward",
        "3200",
        "--num_epochs",
        "1",
        "--lr",
        "1e-5",
        "--save_freq",
        "1",
        "--state_dim",
        "14",
        "--seed",
        "0",
    ]
    report["commands"].append(run_command("act_train_entry", train_cmd, act_root, out_dir, args.timeout))

    eval_cmd = [
        "bash",
        "eval.sh",
        args.task_name,
        args.task_config,
        args.task_config,
        args.expert_data_num,
        "0",
        "0",
    ]
    report["commands"].append(run_command("act_eval_entry", eval_cmd, act_root, out_dir, args.timeout))

    hdf5_count = report["act_hdf5_adapter"].get("hdf5_file_count", 0)
    command_failures = [item for item in report["commands"] if item.get("returncode") not in (0, None) or item.get("timed_out")]
    blocking_reasons = []
    if hdf5_count == 0:
        blocking_reasons.append("generated ACT HDF5 adapter contains no HDF5 episodes")
    if report["act_hdf5_adapter"].get("conversion_status") != "pass_act_hdf5_adapter_smoke":
        blocking_reasons.append("generated ACT HDF5 adapter conversion did not pass")
    if report["act_hdf5_adapter"].get("loader_status") != "pass_act_hdf5_loader_smoke":
        blocking_reasons.append("generated ACT HDF5 loader smoke did not pass")
    if command_failures:
        blocking_reasons.append("RoboTwin ACT process/train/eval entrypoint probes did not complete successfully")
    if any("No Task" in (item.get("stdout_tail", "") + item.get("stderr_tail", "")) for item in report["commands"]):
        blocking_reasons.append("generated task name is not registered as a RoboTwin env module for eval")
    if any("Dataset does not exist" in (item.get("stdout_tail", "") + item.get("stderr_tail", "")) for item in report["commands"]):
        blocking_reasons.append("ACT process_data cannot find source RoboTwin HDF5 data")
    if any("sim-task_apple_plate-demo_clean-3" in (item.get("stdout_tail", "") + item.get("stderr_tail", "")) for item in report["commands"]):
        blocking_reasons.append("default ACT train task name is not registered in SIM_TASK_CONFIGS")

    report.update(
        {
            "status": "blocked_policy_train_eval_not_wired" if blocking_reasons else "pass_policy_train_eval_entrypoints",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "blocking_reasons": blocking_reasons,
            "hdf5_file_count": hdf5_count,
            "command_failure_count": len(command_failures),
        }
    )
    write_json(out_dir / "probe_report.json", report)
    print(json.dumps({"status": report["status"], "report": str(out_dir / "probe_report.json")}, ensure_ascii=False))
    return 0 if report["status"].startswith("blocked_") or report["status"].startswith("pass_") else 1


if __name__ == "__main__":
    raise SystemExit(main())
