#!/usr/bin/env python3
"""Probe pending-review exit and aggregate state across active Stage 5 CLIs."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[4]
STAGE5_ROOT = REPO_ROOT / "self_improving" / "stage5"
if str(STAGE5_ROOT) not in sys.path:
    sys.path.insert(0, str(STAGE5_ROOT))

from generate_scene import run_scene_batch, scene_critic  # noqa: E402


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _batch_result(root: Path, *, status: str) -> dict:
    output = root / f"batch-{status}"

    def fake_run(cmd, **_kwargs):
        scene_dir = Path(cmd[cmd.index("--out-dir") + 1])
        _write_json(
            scene_dir / "scene_generation_summary.json",
            {"status": status, "artifacts": {}},
        )
        filename = (
            "review_candidate_placement.json"
            if status == "pending_visual_review"
            else "final_placement.json"
        )
        _write_json(
            scene_dir / filename,
            {"placement_name": status, "objects": [], "relations": []},
        )
        return SimpleNamespace(
            returncode=2 if status == "pending_visual_review" else 0, stdout="", stderr=""
        )

    argv = [
        "run_scene_batch.py",
        "--prompt",
        "probe",
        "--num-scenes",
        "1",
        "--max-candidates",
        "1",
        "--out-dir",
        str(output),
    ]
    if status == "pending_visual_review":
        argv.append("--allow-pending-visual")
    with (
        patch.object(run_scene_batch.subprocess, "run", fake_run),
        patch.object(sys, "argv", argv),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        exit_code = run_scene_batch.main()
    summary = json.loads((output / "batch_summary.json").read_text(encoding="utf-8"))
    return {"exit_code": exit_code, "status": summary["status"]}


def _critic_result(root: Path, *, visual_status: str) -> dict:
    case = root / f"critic-{visual_status}"
    paths = {
        "placement": case / "placement.json",
        "static": case / "static.json",
        "smoke": case / "smoke.json",
        "visual": case / "visual.json",
        "out": case / "critic.json",
    }
    _write_json(paths["placement"], {"placement_name": "probe"})
    _write_json(
        paths["static"], {"status": "pass", "checks": [], "fail_count": 0, "warning_count": 0}
    )
    _write_json(paths["smoke"], {"status": "pass", "returncode": 0})
    _write_json(paths["visual"], {"status": visual_status, "issues": []})
    argv = [
        "scene_critic.py",
        "--prompt",
        "probe",
        "--placement",
        str(paths["placement"]),
        "--static-validation",
        str(paths["static"]),
        "--smoke-report",
        str(paths["smoke"]),
        "--visual-review",
        str(paths["visual"]),
        "--out",
        str(paths["out"]),
    ]
    with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
        exit_code = scene_critic.main()
    report = json.loads(paths["out"].read_text(encoding="utf-8"))
    return {"exit_code": exit_code, "status": report["overall_status"]}


def run_probe() -> dict:
    with tempfile.TemporaryDirectory(prefix="aspire-pending-entrypoints-") as directory:
        root = Path(directory)
        observed = {
            "batch_pending": _batch_result(root, status="pending_visual_review"),
            "batch_pass": _batch_result(root, status="pass"),
            "critic_pending": _critic_result(root, visual_status="pending_semantic_review"),
            "critic_pass": _critic_result(root, visual_status="pass"),
        }
    expected = {
        "batch_pending": {"exit_code": 2, "status": "review_required"},
        "batch_pass": {"exit_code": 0, "status": "pass"},
        "critic_pending": {"exit_code": 2, "status": "pending_visual_review"},
        "critic_pass": {"exit_code": 0, "status": "pass"},
    }
    checks = {
        f"{case}.{field}": observed[case][field] == value
        for case, fields in expected.items()
        for field, value in fields.items()
    }
    return {
        "schema_version": "aspire.pending_entrypoints_probe.v1",
        "observed": observed,
        "expected": expected,
        "checks": checks,
        "accuracy": sum(checks.values()) / len(checks),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = run_probe()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"accuracy": result["accuracy"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
