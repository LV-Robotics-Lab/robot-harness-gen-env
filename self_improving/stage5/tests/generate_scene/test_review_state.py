from __future__ import annotations

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
