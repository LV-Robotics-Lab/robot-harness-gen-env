from pathlib import Path

import pytest

from scripts.embodied_harness import (
    REQUIRED_COMPARISONS,
    REQUIRED_LOOP_TERMS,
    REQUIRED_ROUTES,
    REQUIRED_SURFACES,
    SPEC,
    read_json,
    validate_embodied_harness_package,
)


def test_embodied_harness_package_passes() -> None:
    root = Path(__file__).resolve().parents[1]
    if not (root / "reports" / "embodied_harness").is_dir():
        pytest.skip("rendered report and run-evidence bundles are intentionally external")
    report = validate_embodied_harness_package()

    assert report["status"] == "pass_embodied_harness_package"
    assert report["acceptance_items"] == 6
    assert report["loop_steps"] == 9
    assert report["surfaces"] == 11
    assert report["comparisons"] == 5
    assert report["routed_commands"] == 5
    assert report["priority_claim"] == "not_established"
    assert report["causal_ablation"]["status"] == "pass_fixed_checkpoint_harness_ablation"
    assert report["causal_ablation"]["success_delta"] == 3
    assert report["causal_ablation"]["promotion_decision"] == "accept"


def test_loop_surfaces_comparisons_and_routes_are_complete() -> None:
    spec = read_json(SPEC)

    assert [row["sketch_term"] for row in spec["loop_steps"]] == REQUIRED_LOOP_TERMS
    assert {row["surface"] for row in spec["embodied_surfaces"]} == REQUIRED_SURFACES
    assert {row["comparison"] for row in spec["novelty_table"]} == REQUIRED_COMPARISONS
    assert {row["command"] for row in spec["command_routing"]} == REQUIRED_ROUTES


def test_priority_and_attribution_boundaries_are_explicit() -> None:
    claim = read_json(SPEC)["paper_claim"]

    assert claim["priority_claim_status"] == "not_established"
    assert claim["prohibited_public_claim"] == "first real embodied harness system"
    assert "checkpoint" in claim["attribution_control"].lower()
    assert "separate" in claim["attribution_control"].lower()


def test_bounded_causal_obligations_are_advanced_without_priority_overclaim() -> None:
    obligations = {row["claim"]: row["status"] for row in read_json(SPEC)["proof_obligations"]}

    assert obligations["implemented embodied harness artifact contract"] == "proven_bounded"
    assert obligations["harness edits improve outcomes independently of policy changes"] == "proven_bounded"
    assert obligations["Open X Sim supports task-semantic reuse across simulators"] == "proven_bounded"
    assert obligations["failure memory can improve a controlled harness decision"] == "proven_bounded"
    assert obligations["a learned policy can pass the bounded SceneAgent promotion contract"] == "proven_bounded"
    assert obligations["real-robot harness evolution"] == "not_run"
    assert obligations["first real embodied harness system"] == "not_established"
