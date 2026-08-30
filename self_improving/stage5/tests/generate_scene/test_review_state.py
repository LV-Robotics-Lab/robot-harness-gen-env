from __future__ import annotations

import json
import sys
from pathlib import Path

from generate_scene import run_scene_generation_pipeline as pipeline
from generate_scene.run_scene_batch import _completed_for_acceptance
from generate_scene.run_scene_generation_pipeline import (
    _mark_final_spec,
    _mark_review_candidate,
    _pipeline_exit_code,
)


def _spec() -> dict:
    return {
        "schema_version": "robotwin.tabletop_placement.v0",
        "placement_name": "designer_initial_test",
        "stage": "designer_initial",
        "validation": {},
    }


def test_pending_review_remains_a_nonfinal_candidate() -> None:
    candidate = _mark_review_candidate(
        spec=_spec(),
        scene_critic_review={
            "overall_status": "pending_visual_review",
            "decision": "hold_for_review",
            "summary": "Visual review is pending.",
        },
        attempt=2,
    )

    assert candidate["stage"] == "render_review_required"
    assert candidate["orchestrator_decision"]["decision"] == "hold_for_review"
    assert candidate["orchestrator_decision"]["review_attempt"] == 2
    assert candidate["validation"]["scene_critic"] == "pending_visual_review"
    assert candidate["validation"]["render_visibility"] == "pending_visual_review"
    assert _pipeline_exit_code("pending_visual_review") == 2


def test_pass_control_retains_final_acceptance() -> None:
    final_spec = _mark_final_spec(
        spec=_spec(),
        scene_critic_review={
            "overall_status": "pass",
            "decision": "accept_final",
            "summary": "All configured checks passed.",
        },
        attempt=1,
    )

    assert final_spec["stage"] == "final_render_accepted"
    assert final_spec["orchestrator_decision"]["decision"] == "accept_final"
    assert final_spec["validation"]["scene_critic"] == "pass"
    assert final_spec["validation"]["render_visibility"] == "pass_visual_review"
    assert _pipeline_exit_code("pass") == 0


def test_batch_requires_explicit_pending_review_policy() -> None:
    assert not _completed_for_acceptance(
        returncode=2,
        status="pending_visual_review",
        allow_pending_visual=False,
    )
    assert _completed_for_acceptance(
        returncode=2,
        status="pending_visual_review",
        allow_pending_visual=True,
    )
    assert not _completed_for_acceptance(
        returncode=0,
        status="pending_visual_review",
        allow_pending_visual=True,
    )
    assert _completed_for_acceptance(
        returncode=0,
        status="pass",
        allow_pending_visual=False,
    )


def test_pipeline_writes_pending_review_as_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    stage5_root = Path(__file__).resolve().parents[2]
    out_dir = tmp_path / "out"
    generated_dir = tmp_path / "generated"
    visual_report = tmp_path / "pending_visual_review.json"
    visual_report.write_text(
        json.dumps(
            {
                "schema_version": "robotwin.visual_review.v0",
                "status": "pending_semantic_review",
                "checks": [],
                "issues": [],
                "repair_suggestions": [],
            }
        ),
        encoding="utf-8",
    )

    def fake_smoke(**kwargs):
        smoke_dir = kwargs["out_dir"]
        smoke_dir.mkdir(parents=True, exist_ok=True)
        report = {"status": "pass", "returncode": 0}
        (smoke_dir / "smoke_report.json").write_text(
            json.dumps(report),
            encoding="utf-8",
        )
        return report

    monkeypatch.setattr(pipeline, "run_robotwin_smoke", fake_smoke)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_scene_generation_pipeline.py",
            "--prompt",
            "an apple and a plate on the table",
            "--master-catalog",
            str(stage5_root / "asset_catalogs" / "robotwin_tabletop_assets_master.json"),
            "--prompt-case",
            str(stage5_root / "asset_catalogs" / "prompt_cases" / "apple_plate.json"),
            "--robotwin-root",
            str(tmp_path / "robotwin"),
            "--out-dir",
            str(out_dir),
            "--generated-scene-dir",
            str(generated_dir),
            "--run-smoke",
            "--visual-review-report",
            str(visual_report),
        ],
    )

    assert pipeline.main() == 2
    summary = json.loads((out_dir / "scene_generation_summary.json").read_text())
    candidate = json.loads((out_dir / "review_candidate_placement.json").read_text())
    assert summary["status"] == "pending_visual_review"
    assert "review_candidate_placement" in summary["artifacts"]
    assert "final_placement" not in summary["artifacts"]
    assert not (out_dir / "final_placement.json").exists()
    assert candidate["stage"] == "render_review_required"
    assert candidate["orchestrator_decision"]["decision"] == "hold_for_review"
