from __future__ import annotations

from dataclasses import fields

from self_improving.studies.ASPIRE.experiments.heldout_harness_benchmark import (
    FAMILIES,
    FrozenPolicy,
    PolicyObservation,
    _validate,
    build_clean_controls,
    build_split,
    build_validated_library,
    evaluate_arm,
    make_observation,
    paired_family_stratified_bootstrap,
    run_benchmark,
)


def test_policy_observation_has_no_evaluator_ground_truth() -> None:
    case = build_split("development", per_family=1, seed=17)[0]
    observation = make_observation(
        case.case_id,
        _validate(case.faulty),
        attempt=0,
        remaining_actions=1,
    )
    assert {field.name for field in fields(PolicyObservation)} == {
        "case_id",
        "attempt",
        "remaining_actions",
        "validation_status",
        "failure_triggers",
        "failed_checks",
    }
    rendered = observation.as_dict()
    for forbidden in (
        "family",
        "split",
        "variant",
        "expected",
        "clean",
        "action",
        "heldout",
    ):
        assert forbidden not in rendered
    assert all(family not in observation.case_id for family in FAMILIES)


def test_development_and_heldout_cases_are_disjoint_and_clean_controls_pass() -> None:
    development = build_split("development", per_family=2, seed=23)
    heldout = build_split("heldout", per_family=3, seed=23)
    assert {case.case_id for case in development}.isdisjoint({case.case_id for case in heldout})
    assert {case.clean.resolved.digest() for case in development}.isdisjoint(
        {case.clean.resolved.digest() for case in heldout}
    )
    assert all(
        _validate(control.clean)["status"] == "pass" for control in build_clean_controls(heldout)
    )


def test_all_non_integrity_faults_are_rejected_before_repair() -> None:
    cases = build_split("development", per_family=2, seed=29)
    for case in cases:
        report = _validate(case.faulty)
        if case.family != "integrity_binding":
            assert report["status"] == "fail", (case.family, case.variant, report)
        else:
            # This benchmark remains runnable before and after the production
            # evidence-binding gate.  A pre-fix pass is measured as unsafe
            # publication; a post-fix failure becomes a repairable trace.
            assert report["status"] in {"pass", "fail"}


def test_validated_library_uses_only_development_receipts_and_is_immutable() -> None:
    development = build_split("development", per_family=2, seed=31)
    controls = build_clean_controls(development)
    library, audit = build_validated_library(development, controls)
    assert library == tuple(library)
    assert audit["clean_controls"] == len(controls)
    assert all(skill.clean_regressions == 0 for skill in library)
    rules = {skill.trigger: skill.action for skill in library}
    assert rules["static_relation:on_top_of"] == "recenter_support"
    assert rules["static_relation:inside"] == "recenter_containment"
    assert rules["static_feasibility:workspace"] == "project_workspace"
    assert set(rules) <= {
        "static_relation:on_top_of",
        "static_relation:inside",
        "static_feasibility:workspace",
        "runtime_support:contact",
        "runtime_observation:quality",
        "runtime_binding:scene_identity",
    }


def test_arm_budget_and_clean_regression_metrics_are_enforced() -> None:
    heldout = build_split("heldout", per_family=3, seed=37)
    result = evaluate_arm(FrozenPolicy(), heldout, build_clean_controls(heldout))
    assert result["heldout_fault_cases"] == 18
    assert result["budget_violation_count"] == 0
    assert result["clean_regression_rate"] == 0.0
    assert set(result["family_metrics"]) == set(FAMILIES)
    assert len(result["fault_case_outcomes"]) == 18
    assert set(result["fault_case_outcomes"][0]) == {
        "case_id",
        "family",
        "action",
        "completion",
        "unsafe_publish",
        "budget",
    }
    assert all(
        outcome["budget"]
        == {
            "actions_used": 1,
            "max_actions": 1,
            "validations_used": 2,
            "max_validations": 2,
            "violation": False,
        }
        for outcome in result["fault_case_outcomes"]
    )


def test_family_stratified_paired_bootstrap_is_positive_and_has_no_regressions() -> None:
    result = run_benchmark(
        seed=39,
        development_per_family=2,
        heldout_per_family=5,
        bootstrap_seed=101,
        bootstrap_resamples=1_000,
    )
    comparison = result["comparisons"]["validated_memory_vs_reactive"]
    assert comparison["delta"] >= 0.05
    assert comparison["bootstrap"]["method"] == "family_stratified_paired_percentile"
    assert comparison["bootstrap"]["seed"] == 101
    assert comparison["bootstrap"]["resamples"] == 1_000
    assert comparison["bootstrap"]["ci_lower"] > 0.0
    assert comparison["bootstrap"]["ci_upper"] >= comparison["bootstrap"]["ci_lower"]
    assert comparison["paired_improved"] > 0
    assert comparison["paired_regressed"] == 0
    assert (
        comparison["paired_improved"] + comparison["paired_regressed"] + comparison["paired_tied"]
        == comparison["paired_cases"]
    )
    assert result["keep_rule"]["keep_rule_pass"] is True
    assert result["keep_rule"]["claim_scope"] == "offline_synthetic_contract_mechanism_only"
    assert result["keep_rule"]["paper_scale_aspire_supported"] is False
    assert result["keep_rule"]["simulator_physics_improvement_supported"] is False


def test_bootstrap_rejects_unpaired_outcomes() -> None:
    heldout = build_split("heldout", per_family=1, seed=40)
    controls = build_clean_controls(heldout)
    first = evaluate_arm(FrozenPolicy(), heldout, controls)
    second = evaluate_arm(FrozenPolicy(), heldout, controls)
    second["fault_case_outcomes"] = second["fault_case_outcomes"][:-1]
    try:
        paired_family_stratified_bootstrap(first, second, resamples=10)
    except ValueError as error:
        assert "same fault case IDs" in str(error)
    else:
        raise AssertionError("unpaired outcomes must be rejected")


def test_benchmark_is_deterministic_and_heldout_does_not_edit_memory() -> None:
    first = run_benchmark(
        seed=41,
        development_per_family=2,
        heldout_per_family=3,
        bootstrap_seed=103,
        bootstrap_resamples=1_000,
    )
    second = run_benchmark(
        seed=41,
        development_per_family=2,
        heldout_per_family=3,
        bootstrap_seed=103,
        bootstrap_resamples=1_000,
    )
    larger_heldout = run_benchmark(
        seed=41,
        development_per_family=2,
        heldout_per_family=4,
        bootstrap_seed=103,
        bootstrap_resamples=1_000,
    )
    assert first == second
    assert first["result_sha256"] == second["result_sha256"]
    assert first["split_receipt"]["intersection"] == []
    assert first["promotion"]["library_frozen_before_heldout"] is True
    assert first["promotion"]["library_sha256"] == larger_heldout["promotion"]["library_sha256"]
    assert (
        first["arms"]["validated_trigger_memory"]["macro_heldout_robust_completion"]
        > first["arms"]["reactive_trace_no_memory"]["macro_heldout_robust_completion"]
    )
    comparison = first["comparisons"]["validated_memory_vs_reactive"]
    assert comparison["bootstrap"]["ci_lower"] > 0.0
    assert comparison["paired_regressed"] == 0
    assert first["keep_rule"]["keep_rule_pass"] is True
    assert all(check["pass"] for check in first["keep_rule"]["checks"].values())
    for arm in first["arms"].values():
        assert arm["budget_violation_count"] == 0
        assert arm["clean_regression_rate"] == 0.0
