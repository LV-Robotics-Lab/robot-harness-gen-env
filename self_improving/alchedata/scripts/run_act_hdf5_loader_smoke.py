#!/usr/bin/env python3
"""Smoke-test generated ACT HDF5 data with RoboTwin ACT dataset utilities."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_act_utils(robotwin_root: Path):
    act_dir = robotwin_root / "policy" / "ACT"
    utils_path = act_dir / "utils.py"
    if not utils_path.exists():
        raise RuntimeError(f"Missing RoboTwin ACT utils.py: {utils_path}")
    sys.path.insert(0, str(act_dir))
    spec = importlib.util.spec_from_file_location("robotwin_act_utils", utils_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import RoboTwin ACT utils.py: {utils_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description="Run RoboTwin ACT HDF5 loader smoke.")
    parser.add_argument("--robotwin-root", default=str(ROOT / "external" / "RoboTwin"))
    parser.add_argument("--config", default=str(ROOT / "runs" / "act_hdf5_generated_smoke" / "SIM_TASK_CONFIGS.generated.json"))
    parser.add_argument("--task-name")
    parser.add_argument("--out", default=str(ROOT / "runs" / "act_hdf5_generated_smoke" / "load_data_report.json"))
    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve()
    robotwin_root = Path(args.robotwin_root).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve()
    report: dict[str, Any] = {
        "schema_version": "alchedata.act_hdf5_loader_smoke.v0",
        "status": "started",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": str(config_path),
        "robotwin_root": str(robotwin_root),
        "claim_boundary": (
            "RoboTwin ACT utility smoke only: imports ACT utils, computes norm stats, and reads one dataset item. "
            "It does not run ACT train.py, checkpointing, or evaluation."
        ),
    }
    write_json(out_path, report)

    try:
        config = read_json(config_path)
        task_name = args.task_name or next(iter(config))
        task_config = config[task_name]
        utils = load_act_utils(robotwin_root)
        norm_stats, max_action_len = utils.get_norm_stats(task_config["dataset_dir"], task_config["num_episodes"])
        dataset = utils.EpisodicDataset([0], task_config["dataset_dir"], task_config["camera_names"], norm_stats, max_action_len)
        image_data, qpos_data, action_data, is_pad = dataset[0]
        report.update(
            {
                "status": "pass_act_hdf5_loader_smoke",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "task_name": task_name,
                "dataset_dir": task_config["dataset_dir"],
                "num_episodes": int(task_config["num_episodes"]),
                "camera_names": task_config["camera_names"],
                "max_action_len": int(max_action_len),
                "batch_item_shapes": {
                    "image": list(image_data.shape),
                    "qpos": list(qpos_data.shape),
                    "action": list(action_data.shape),
                    "is_pad": list(is_pad.shape),
                },
                "norm_stats_shapes": {key: list(value.shape) for key, value in norm_stats.items() if hasattr(value, "shape")},
            }
        )
    except Exception as exc:  # noqa: BLE001
        report.update(
            {
                "status": "blocked_act_hdf5_loader_smoke",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "exception": repr(exc),
            }
        )
    write_json(out_path, report)
    print(json.dumps({"status": report["status"], "report": str(out_path)}, ensure_ascii=False))
    return 0 if report["status"].startswith("pass_") else 1


if __name__ == "__main__":
    raise SystemExit(main())
