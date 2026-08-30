#!/usr/bin/env python3
"""Probe whether a pending visual review is mislabeled as final acceptance."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
STAGE5 = ROOT / "self_improving" / "stage5"
if str(STAGE5) not in sys.path:
    sys.path.insert(0, str(STAGE5))

from generate_scene import run_scene_generation_pipeline as pipeline  # noqa: E402


def _pending_result() -> dict[str, Any]:
    spec = {
        "schema_version": "robotwin.tabletop_placement.v0",
        "placement_name": "designer_initial_probe",
        "stage": "designer_initial",
        "validation": {},
    }
    review = {
        "overall_status": "pending_visual_review",
        "decision": "hold_for_review",
        "summary": "Semantic visual review is pending.",
    }
    if hasattr(pipeline, "_mark_review_candidate"):
        artifact = pipeline._mark_review_candidate(
            spec=spec,
            scene_critic_review=review,
            attempt=0,
        )
    else:
        artifact = pipeline._mark_final_spec(
            spec=spec,
            scene_critic_review=review,
            attempt=0,
        )
    exit_code = (
        pipeline._pipeline_exit_code("pending_visual_review")
        if hasattr(pipeline, "_pipeline_exit_code")
        else 0
    )
    checks = {
        "not_final_stage": artifact.get("stage") != "final_render_accepted",
        "not_accept_final": artifact.get("orchestrator_decision", {}).get("decision")
        != "accept_final",
        "critic_not_pass": artifact.get("validation", {}).get("scene_critic") != "pass",
        "visual_not_pass": artifact.get("validation", {}).get("render_visibility")
        != "pass_visual_review",
        "nonzero_review_exit": exit_code != 0,
    }
    return {
        "artifact": artifact,
        "exit_code": exit_code,
        "checks": checks,
        "correct_decisions": sum(checks.values()),
        "decision_count": len(checks),
        "accuracy": sum(checks.values()) / len(checks),
    }


def _pass_control() -> dict[str, Any]:
    spec = {
        "schema_version": "robotwin.tabletop_placement.v0",
        "placement_name": "designer_initial_probe",
        "stage": "designer_initial",
        "validation": {},
    }
    review = {
        "overall_status": "pass",
        "decision": "accept_final",
        "summary": "All configured checks passed.",
    }
    artifact = pipeline._mark_final_spec(
        spec=spec,
        scene_critic_review=review,
        attempt=0,
    )
    exit_code = pipeline._pipeline_exit_code("pass") if hasattr(pipeline, "_pipeline_exit_code") else 0
    checks = {
        "final_stage": artifact.get("stage") == "final_render_accepted",
        "accept_final": artifact.get("orchestrator_decision", {}).get("decision")
        == "accept_final",
        "critic_pass": artifact.get("validation", {}).get("scene_critic") == "pass",
        "visual_pass": artifact.get("validation", {}).get("render_visibility")
        == "pass_visual_review",
        "zero_exit": exit_code == 0,
    }
    return {
        "exit_code": exit_code,
        "checks": checks,
        "correct_decisions": sum(checks.values()),
        "decision_count": len(checks),
        "accuracy": sum(checks.values()) / len(checks),
    }


def run_probe() -> dict[str, Any]:
    return {
        "schema_version": "robot_harness.aspire_pending_review_probe.v1",
        "pending": _pending_result(),
        "pass_control": _pass_control(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = run_probe()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "pending_accuracy": result["pending"]["accuracy"],
                "pass_control_accuracy": result["pass_control"]["accuracy"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
