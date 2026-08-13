#!/usr/bin/env python3
"""Run a bounded RoboTwin ACT train smoke on generated HDF5 data."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = ROOT / "runs" / "act_train_smoke_generated"
DEFAULT_CONFIG = ROOT / "runs" / "act_hdf5_generated_smoke" / "SIM_TASK_CONFIGS.generated.json"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def file_record(path: Path) -> dict[str, Any]:
    return {"path": str(path), "exists": path.exists(), "size_bytes": path.stat().st_size if path.exists() else 0}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run bounded ACT training on generated HDF5 data.")
    parser.add_argument("--robotwin-root", default=str(ROOT / "external" / "RoboTwin"))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--task-name", default="sim-generated_selection2env-demo_clean-6")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--gpu-id", default="0")
    parser.add_argument("--hidden-dim", default="64")
    parser.add_argument("--dim-feedforward", default="256")
    parser.add_argument("--chunk-size", default="20")
    parser.add_argument("--batch-size", default="2")
    parser.add_argument("--num-epochs", type=int, default=1)
    parser.add_argument("--learning-rate", default="1e-5")
    parser.add_argument("--save-freq", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    robotwin_root = Path(args.robotwin_root).expanduser().resolve()
    act_root = robotwin_root / "policy" / "ACT"
    config_path = Path(args.config).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    ckpt_dir = out_dir / "act_ckpt"
    report_path = out_dir / "train_smoke_report.json"
    stdout_path = out_dir / "act_train_stdout.log"
    stderr_path = out_dir / "act_train_stderr.log"
    report: dict[str, Any] = {
        "schema_version": "alchedata.act_train_execution.v0",
        "status": "started",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "robotwin_root": str(robotwin_root),
        "act_root": str(act_root),
        "task_name": args.task_name,
        "config": str(config_path),
        "out_dir": str(out_dir),
        "num_epochs": args.num_epochs,
        "claim_boundary": (
            f"Bounded {args.num_epochs}-epoch ACT training on generated HDF5 data. This proves import, data loading, "
            "loss computation, optimization, and checkpoint writing; policy quality requires separate evaluation."
        ),
    }
    write_json(report_path, report)

    generated_config = read_json(config_path)
    act_config_path = act_root / "SIM_TASK_CONFIGS.json"
    backup_path = out_dir / "SIM_TASK_CONFIGS.ACT.before_smoke.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    if ckpt_dir.exists():
        shutil.rmtree(ckpt_dir)
    original_config = act_config_path.read_bytes() if act_config_path.exists() else None
    if original_config is not None:
        backup_path.write_bytes(original_config)
    act_config_path.write_text(json.dumps(generated_config, indent=2) + "\n", encoding="utf-8")

    command = [
        sys.executable,
        "imitate_episodes.py",
        "--task_name",
        args.task_name,
        "--ckpt_dir",
        str(ckpt_dir),
        "--policy_class",
        "ACT",
        "--kl_weight",
        "1",
        "--chunk_size",
        args.chunk_size,
        "--hidden_dim",
        args.hidden_dim,
        "--batch_size",
        args.batch_size,
        "--dim_feedforward",
        args.dim_feedforward,
        "--num_epochs",
        str(args.num_epochs),
        "--lr",
        args.learning_rate,
        "--save_freq",
        str(args.save_freq),
        "--state_dim",
        "14",
        "--seed",
        str(args.seed),
    ]
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = args.gpu_id
    try:
        completed = subprocess.run(
            command,
            cwd=act_root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=args.timeout,
            check=False,
        )
    finally:
        if original_config is None:
            act_config_path.unlink(missing_ok=True)
        else:
            act_config_path.write_bytes(original_config)
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    text = completed.stdout + "\n" + completed.stderr
    match = re.search(r"Best ckpt, val loss ([0-9.]+) @ epoch([0-9]+)", text)
    best_val_loss = float(match.group(1)) if match else None
    best_epoch = int(match.group(2)) if match else None
    files = {
        "policy_best": file_record(ckpt_dir / "policy_best.ckpt"),
        "dataset_stats": file_record(ckpt_dir / "dataset_stats.pkl"),
        "loss_plot": file_record(ckpt_dir / f"train_val_loss_seed_{args.seed}.png"),
        "l1_plot": file_record(ckpt_dir / f"train_val_l1_seed_{args.seed}.png"),
        "kl_plot": file_record(ckpt_dir / f"train_val_kl_seed_{args.seed}.png"),
    }
    if completed.returncode == 0 and files["policy_best"]["exists"]:
        status = "pass_act_train_smoke" if args.num_epochs == 1 else "pass_act_train_execution"
    else:
        status = "blocked_act_train_smoke" if args.num_epochs == 1 else "blocked_act_train_execution"
    report.update(
        {
            "status": status,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "command": command,
            "returncode": completed.returncode,
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
            "best_val_loss": best_val_loss,
            "best_epoch": best_epoch,
            "num_epochs": args.num_epochs,
            "learning_rate": args.learning_rate,
            "files": files,
            "dependency_note": "Requires einops and dm_control in the active ACT environment.",
        }
    )
    write_json(report_path, report)
    print(json.dumps({"status": status, "best_val_loss": best_val_loss, "report": str(report_path)}, ensure_ascii=False))
    return 0 if status.startswith("pass_") else 1


if __name__ == "__main__":
    raise SystemExit(main())
