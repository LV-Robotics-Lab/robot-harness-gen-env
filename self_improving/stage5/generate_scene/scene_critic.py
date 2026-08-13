#!/usr/bin/env python3
"""Unified scene critic for render-in-the-loop tabletop generation."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generate_scene.schemas import read_json, write_json
from generate_scene.tools import get_smoke_artifacts


def _static_issues(static_validation: dict[str, Any]) -> list[dict[str, Any]]:
    issues = []
    for check in static_validation.get("checks", []):
        status = check.get("status")
        if status not in {"fail", "warning"}:
            continue
        issues.append(
            {
                "source": "rule_static_check",
                "severity": "major" if status == "fail" else "minor",
                "target": check.get("name"),
                "message": check.get("message", ""),
            }
        )
    return issues


def _visual_issues(visual_review: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not visual_review:
        return []
    issues = []
    for issue in visual_review.get("issues", []):
        issues.append(
            {
                "source": "visual_render_review",
                "severity": issue.get("severity", "major"),
                "target": issue.get("target", "scene"),
                "message": issue.get("message", ""),
            }
        )
    for check in visual_review.get("checks", []):
        if check.get("status") == "fail":
            issues.append(
                {
                    "source": "visual_render_review",
                    "severity": "major",
                    "target": check.get("name", "scene"),
                    "message": check.get("evidence", ""),
                }
            )
    return issues


def _repair_suggestions(
    *,
    static_validation: dict[str, Any],
    visual_review: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    for issue in _static_issues(static_validation):
        if issue["severity"] == "minor":
            continue
        suggestions.append(
            {
                "source": issue["source"],
                "target": issue["target"],
                "suggestion": f"Repair PlacementSpec before rendering: {issue['message']}",
            }
        )
    if visual_review:
        for suggestion in visual_review.get("repair_suggestions", []):
            suggestions.append(
                {
                    "source": "visual_render_review",
                    "target": suggestion.get("target", "scene"),
                    "suggestion": suggestion.get("suggestion", ""),
                }
            )
    return suggestions


def build_scene_critic_report(
    *,
    prompt: str,
    placement: dict[str, Any],
    static_validation: dict[str, Any],
    smoke_report: dict[str, Any] | None = None,
    visual_review: dict[str, Any] | None = None,
    smoke_dir: Path | None = None,
    attempt: int = 0,
) -> dict[str, Any]:
    """Combine preflight, simulator, and visual evidence into one critic report."""

    static_status = static_validation.get("status", "fail")
    smoke_status = smoke_report.get("status") if smoke_report else "not_run"
    smoke_returncode = smoke_report.get("returncode") if smoke_report else None
    visual_status = visual_review.get("status") if visual_review else "not_run"

    issues = _static_issues(static_validation) + _visual_issues(visual_review)
    suggestions = _repair_suggestions(static_validation=static_validation, visual_review=visual_review)

    if static_status != "pass":
        overall_status = "fail_preflight"
        decision = "repair_spec"
        summary = "Static preflight failed; do not trust render/codegen until the PlacementSpec is repaired."
    elif smoke_report and (smoke_status != "pass" or smoke_returncode not in {0, None}):
        overall_status = "fail_smoke"
        decision = "repair_spec"
        summary = "RoboTwin smoke did not pass; repair placement or asset loading before accepting the scene."
        issues.append(
            {
                "source": "simulator_smoke",
                "severity": "blocker",
                "target": "robotwin_smoke",
                "message": f"Smoke status={smoke_status}, returncode={smoke_returncode}.",
            }
        )
    elif visual_review and visual_status == "pass":
        overall_status = "pass"
        decision = "accept_final"
        summary = "Static preflight, smoke render, and visual review passed."
    elif visual_review and str(visual_status).startswith("pending"):
        overall_status = "pending_visual_review"
        decision = "hold_for_review"
        summary = "Static preflight and smoke ran, but semantic visual review is still pending."
    elif visual_review:
        overall_status = "repair_required"
        decision = "repair_spec"
        summary = "Rendered scene needs repair according to visual review."
    elif smoke_report:
        overall_status = "pending_visual_review"
        decision = "hold_for_review"
        summary = "Static preflight and smoke ran, but visual review is not complete."
    else:
        overall_status = "pass_preflight"
        decision = "render_next"
        summary = "Static preflight passed; render the scene for visual critique."

    return {
        "schema_version": "robotwin.scene_critic.v0",
        "prompt": prompt,
        "attempt": attempt,
        "placement_name": placement.get("placement_name"),
        "overall_status": overall_status,
        "decision": decision,
        "summary": summary,
        "rule_static_check": {
            "status": static_status,
            "fail_count": static_validation.get("fail_count", 0),
            "warning_count": static_validation.get("warning_count", 0),
            "checks": static_validation.get("checks", []),
        },
        "simulator_smoke": {
            "status": smoke_status,
            "returncode": smoke_returncode,
            "artifacts": get_smoke_artifacts(smoke_dir) if smoke_dir else {},
        },
        "visual_render_review": visual_review or {"status": visual_status},
        "issues": issues,
        "repair_suggestions": suggestions,
        "orchestrator_recommendation": {
            "decision": decision,
            "allowed_next_steps": ["accept_final", "repair_spec", "redesign", "hold_for_review"],
        },
        "generated_at": date.today().isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a unified Scene Critic report.")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--placement", required=True)
    parser.add_argument("--static-validation", required=True)
    parser.add_argument("--smoke-report")
    parser.add_argument("--visual-review")
    parser.add_argument("--smoke-dir")
    parser.add_argument("--attempt", type=int, default=0)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    report = build_scene_critic_report(
        prompt=args.prompt,
        placement=read_json(Path(args.placement)),
        static_validation=read_json(Path(args.static_validation)),
        smoke_report=read_json(Path(args.smoke_report)) if args.smoke_report else None,
        visual_review=read_json(Path(args.visual_review)) if args.visual_review else None,
        smoke_dir=Path(args.smoke_dir) if args.smoke_dir else None,
        attempt=args.attempt,
    )
    write_json(Path(args.out), report)
    print(f"{report['overall_status'].upper()} {args.out}")
    return 0 if report["overall_status"] in {"pass", "pass_preflight", "pending_visual_review"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
