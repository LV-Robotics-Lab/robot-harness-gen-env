#!/usr/bin/env python3
"""Probe that the default no-smoke Stage 5 path cannot mint final-pass evidence."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[4]
STAGE5_ROOT = REPO_ROOT / "self_improving" / "stage5"
if str(STAGE5_ROOT) not in sys.path:
    sys.path.insert(0, str(STAGE5_ROOT))

from generate_scene import run_scene_generation_pipeline as pipeline  # noqa: E402


def run_probe() -> dict:
    with tempfile.TemporaryDirectory(prefix="aspire-static-only-") as directory:
        root = Path(directory)
        out_dir = root / "out"
        argv = [
            "run_scene_generation_pipeline.py",
            "--prompt",
            "an apple and a plate on the table",
            "--master-catalog",
            str(STAGE5_ROOT / "asset_catalogs" / "robotwin_tabletop_assets_master.json"),
            "--prompt-case",
            str(STAGE5_ROOT / "asset_catalogs" / "prompt_cases" / "apple_plate.json"),
            "--robotwin-root",
            str(root / "robotwin"),
            "--out-dir",
            str(out_dir),
            "--generated-scene-dir",
            str(root / "generated"),
        ]
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
            exit_code = pipeline.main()
        summary = json.loads((out_dir / "scene_generation_summary.json").read_text())
        candidate_path = out_dir / "static_scene_candidate_placement.json"
        legacy_final_path = out_dir / "final_placement.json"
        candidate_exists = candidate_path.exists()
        final_exists = legacy_final_path.exists()
        artifact_path = candidate_path if candidate_path.exists() else legacy_final_path
        artifact = json.loads(artifact_path.read_text())

    checks = {
        "exit_code_is_stage_success": exit_code == 0,
        "status_is_explicit_static_only": summary["status"] == "pass_static_scene_module",
        "no_final_artifact_key": "final_placement" not in summary["artifacts"],
        "no_final_artifact_file": not final_exists,
        "candidate_artifact_exists": candidate_exists,
        "stage_is_nonfinal": artifact.get("stage") == "static_scene_candidate",
        "decision_is_render_next": artifact.get("orchestrator_decision", {}).get("decision")
        == "render_next",
        "smoke_is_not_run": artifact.get("validation", {}).get("robotwin_load_check") == "not_run",
        "visual_is_not_run": artifact.get("validation", {}).get("render_visibility") == "not_run",
    }
    return {
        "schema_version": "aspire.static_only_state_probe.v1",
        "observed": {
            "exit_code": exit_code,
            "summary_status": summary["status"],
            "artifact_keys": sorted(summary["artifacts"]),
            "artifact_filename": artifact_path.name,
            "stage": artifact.get("stage"),
            "decision": artifact.get("orchestrator_decision", {}).get("decision"),
            "robotwin_load_check": artifact.get("validation", {}).get("robotwin_load_check"),
            "render_visibility": artifact.get("validation", {}).get("render_visibility"),
        },
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
