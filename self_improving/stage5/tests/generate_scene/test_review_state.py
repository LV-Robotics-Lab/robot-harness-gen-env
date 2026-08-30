from __future__ import annotations

import json
import sys
from pathlib import Path

from generate_scene import run_scene_generation_pipeline as pipeline
from generate_scene import run_placement_pipeline as placement_pipeline
from generate_scene.run_placement_pipeline import _pipeline_exit_code as _placement_exit_code
from generate_scene.run_scene_batch import (
    _aggregate_status,
    _batch_exit_code,
    _completed_for_acceptance,
    _scene_counts,
)
from generate_scene.run_scene_generation_pipeline import (
    _mark_final_spec,
    _mark_review_candidate,
    _pipeline_exit_code,
)
from generate_scene.scene_critic import _critic_exit_code


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


def test_pending_review_never_aggregates_to_batch_pass() -> None:
    pending = [{"status": "pending_visual_review"}]
    passed = [{"status": "pass"}]

    assert _aggregate_status(pending, 1, complete=False) == "review_required"
    assert _aggregate_status(pending, 1, complete=True) == "review_required"
    assert _batch_exit_code("review_required") == 2
    assert _scene_counts(pending) == {"publishable_count": 0, "review_required_count": 1}
    assert _aggregate_status(passed, 1, complete=True) == "pass"
    assert _batch_exit_code("pass") == 0
    assert _scene_counts(passed) == {"publishable_count": 1, "review_required_count": 0}
    assert _aggregate_status([], 1, complete=False) == "running"
    assert _aggregate_status([], 1, complete=True) == "partial"
    assert _batch_exit_code("partial") == 1


def test_pending_review_exit_codes_are_review_required() -> None:
    assert _critic_exit_code("pending_visual_review") == 2
    assert _placement_exit_code("pending_visual_review") == 2
    assert _critic_exit_code("pass") == 0
    assert _critic_exit_code("pass_preflight") == 0
    assert _placement_exit_code("pass") == 0
    assert _placement_exit_code("pass_static_only") == 0
    assert _critic_exit_code("repair_required") == 1
    assert _placement_exit_code("fail_smoke") == 1


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


def test_pipeline_static_only_is_an_explicit_nonfinal_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    stage5_root = Path(__file__).resolve().parents[2]
    out_dir = tmp_path / "static-only"
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
            str(tmp_path / "generated"),
        ],
    )

    assert pipeline.main() == 0
    summary = json.loads((out_dir / "scene_generation_summary.json").read_text())
    candidate = json.loads((out_dir / "static_scene_candidate_placement.json").read_text())
    plan = json.loads((out_dir / "validation_plan.json").read_text())
    assert summary["status"] == "pass_static_scene_module"
    assert "static_scene_candidate_placement" in summary["artifacts"]
    assert "final_placement" not in summary["artifacts"]
    assert not (out_dir / "final_placement.json").exists()
    assert candidate["stage"] == "static_scene_candidate"
    assert candidate["orchestrator_decision"]["decision"] == "render_next"
    assert candidate["validation"]["robotwin_load_check"] == "not_run"
    assert candidate["validation"]["render_visibility"] == "not_run"
    assert plan["target_placement"] == "static_scene_candidate_placement.json"


def test_legacy_placement_pipeline_writes_pending_review_as_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    stage5_root = Path(__file__).resolve().parents[2]
    out_dir = tmp_path / "legacy-out"
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

    monkeypatch.setattr(placement_pipeline, "run_robotwin_smoke", fake_smoke)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_placement_pipeline.py",
            "--prompt",
            "an apple and a plate on the table",
            "--asset-catalog",
            str(stage5_root / "asset_catalogs" / "prompt_cases" / "apple_plate.json"),
            "--robotwin-root",
            str(tmp_path / "robotwin"),
            "--out-dir",
            str(out_dir),
            "--run-smoke",
            "--visual-review-report",
            str(visual_report),
        ],
    )

    assert placement_pipeline.main() == 2
    summary = json.loads((out_dir / "pipeline_summary.json").read_text())
    candidate = json.loads((out_dir / "review_candidate_placement.json").read_text())
    plan = json.loads((out_dir / "validation_plan.json").read_text())
    assert summary["status"] == "pending_visual_review"
    assert "review_candidate_placement" in summary["artifacts"]
    assert "final_placement" not in summary["artifacts"]
    assert not (out_dir / "final_placement.json").exists()
    assert not (out_dir / "static_validation_final.json").exists()
    assert candidate["stage"] == "render_review_required"
    assert candidate["orchestrator_decision"]["decision"] == "hold_for_review"
    assert candidate["validation"]["render_visibility"] == "pending_visual_review"
    assert plan["target_placement"] == "review_candidate_placement.json"


def test_legacy_placement_pipeline_never_exposes_transient_final_before_review(
    tmp_path: Path,
    monkeypatch,
) -> None:
    stage5_root = Path(__file__).resolve().parents[2]
    out_dir = tmp_path / "legacy-crash"

    def fake_smoke(**kwargs):
        smoke_dir = kwargs["out_dir"]
        smoke_dir.mkdir(parents=True, exist_ok=True)
        report = {"status": "pass", "returncode": 0}
        (smoke_dir / "smoke_report.json").write_text(json.dumps(report), encoding="utf-8")
        return report

    def crash_before_review(*_args, **_kwargs):
        raise RuntimeError("probe crash before visual decision")

    monkeypatch.setattr(placement_pipeline, "run_robotwin_smoke", fake_smoke)
    monkeypatch.setattr(placement_pipeline, "visual_review", crash_before_review)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_placement_pipeline.py",
            "--prompt",
            "an apple and a plate on the table",
            "--asset-catalog",
            str(stage5_root / "asset_catalogs" / "prompt_cases" / "apple_plate.json"),
            "--robotwin-root",
            str(tmp_path / "robotwin"),
            "--out-dir",
            str(out_dir),
            "--run-smoke",
        ],
    )

    try:
        placement_pipeline.main()
    except RuntimeError as error:
        assert str(error) == "probe crash before visual decision"
    else:
        raise AssertionError("expected visual-review crash")

    summary = json.loads((out_dir / "pipeline_summary.json").read_text())
    assert summary["status"] == "fail_exception"
    assert "final_placement" not in summary["artifacts"]
    assert (out_dir / "candidate_placement.json").is_file()
    assert not (out_dir / "final_placement.json").exists()


def test_legacy_placement_pipeline_atomically_promotes_only_visual_pass(
    tmp_path: Path,
    monkeypatch,
) -> None:
    stage5_root = Path(__file__).resolve().parents[2]
    out_dir = tmp_path / "legacy-pass"
    visual_report = tmp_path / "pass_visual_review.json"
    visual_report.write_text(
        json.dumps({"status": "pass", "issues": [], "summary": "Visual review passed."}),
        encoding="utf-8",
    )

    def fake_smoke(**kwargs):
        smoke_dir = kwargs["out_dir"]
        smoke_dir.mkdir(parents=True, exist_ok=True)
        report = {"status": "pass", "returncode": 0}
        (smoke_dir / "smoke_report.json").write_text(json.dumps(report), encoding="utf-8")
        return report

    monkeypatch.setattr(placement_pipeline, "run_robotwin_smoke", fake_smoke)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_placement_pipeline.py",
            "--prompt",
            "an apple and a plate on the table",
            "--asset-catalog",
            str(stage5_root / "asset_catalogs" / "prompt_cases" / "apple_plate.json"),
            "--robotwin-root",
            str(tmp_path / "robotwin"),
            "--out-dir",
            str(out_dir),
            "--run-smoke",
            "--visual-review-report",
            str(visual_report),
        ],
    )

    assert placement_pipeline.main() == 0
    summary = json.loads((out_dir / "pipeline_summary.json").read_text())
    final_spec = json.loads((out_dir / "final_placement.json").read_text())
    assert summary["status"] == "pass"
    assert "final_placement" in summary["artifacts"]
    assert "candidate_placement" not in summary["artifacts"]
    assert not (out_dir / "candidate_placement.json").exists()
    assert final_spec["stage"] == "final_render_accepted"
    assert final_spec["orchestrator_decision"]["decision"] == "accept_final"
