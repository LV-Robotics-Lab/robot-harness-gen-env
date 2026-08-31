from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from self_improving.studies.vlm_fallback_prompt_optimization import amendment

STUDY_ROOT = Path(__file__).resolve().parent
AMENDMENT_ROOT = STUDY_ROOT / "amendments" / "01"


def test_canonical_digest_and_committed_amendment_load_through_one_seam() -> None:
    assert amendment.canonical_sha256({"b": 1, "a": "中"}) == (
        "d8158d9a7acf211407d1309876015fc6e69f13b7dd8126a571e429ddce565911"
    )

    loaded = amendment.load_amendment(AMENDMENT_ROOT)

    assert loaded["contract"]["amendment_id"] == "01"
    assert loaded["summary"]["provider_execution_allowed_now"] is False


def test_normative_contract_is_exact_and_fails_closed_on_unknown_or_missing_fields() -> None:
    loaded = amendment.load_amendment(AMENDMENT_ROOT)

    summary = amendment.validate_amendment(
        loaded["contract"],
        loaded["surface_table"],
        bundle_dir=AMENDMENT_ROOT,
        verify_bindings=False,
    )

    assert summary["common_visible_check_mapping"] == {
        "object_presence": "object_presence",
        "penetration_or_floating": "visible_penetration_or_floating",
        "overall_prompt_match": "overall_prompt_match",
    }
    assert summary["a0_warning_maps_to"] == "abstain"

    extra = copy.deepcopy(loaded["contract"])
    extra["unexpected"] = True
    with pytest.raises(ValueError, match="amendment_contract fields"):
        amendment.validate_amendment(extra, loaded["surface_table"], verify_bindings=False)

    missing = copy.deepcopy(loaded["contract"])
    del missing["experiment_a"]
    with pytest.raises(ValueError, match="amendment_contract fields"):
        amendment.validate_amendment(missing, loaded["surface_table"], verify_bindings=False)


def test_a2_inherits_the_complete_a1_contract_with_only_model_and_resource_differences() -> None:
    loaded = amendment.load_amendment(AMENDMENT_ROOT)
    experiment = loaded["contract"]["experiment_a"]
    arms = experiment["arms"]

    a1 = arms["A1_typed_abstaining_critic_3b"]
    a2 = arms["A2_typed_abstaining_critic_7b"]
    for field in (
        "check_domain",
        "within_arm_only_checks",
        "prompt_contract",
        "input_projection_contract",
        "processor_identity_contract",
    ):
        assert a2[field] == a1[field]
    assert a2["model_role"] != a1["model_role"]
    assert a2["resource_profile"] != a1["resource_profile"]
    assert (
        experiment["status_mapping"]["A2_typed_abstaining_critic_7b"]
        == experiment["status_mapping"]["A1_typed_abstaining_critic_3b"]
    )
    assert experiment["a2_inheritance"] == {
        "base_arm": "A1_typed_abstaining_critic_3b",
        "identical_fields": [
            "check_domain",
            "within_arm_only_checks",
            "status_mapping",
            "prompt_sha256",
            "input_projection_sha256",
            "processor_identity_sha256",
            "sealed_case_roster_sha256",
            "scorer_source_identity_sha256",
        ],
        "allowed_differences": [
            "model_role",
            "model_id",
            "model_revision",
            "model_content_manifest_sha256",
            "model_roster_sha256",
            "resource_reservation",
        ],
        "all_other_differences_forbidden": True,
    }
    assert experiment["cross_arm_comparison"]["paired_contrasts"] == [
        "A1_minus_A0",
        "A2_minus_A0",
        "A2_minus_A1",
    ]

    for mutation in (
        lambda value: value["experiment_a"]["arms"]["A2_typed_abstaining_critic_7b"].update(
            check_domain=list(amendment.A1_CHECKS[:-1])
        ),
        lambda value: value["experiment_a"]["status_mapping"]["A2_typed_abstaining_critic_7b"][
            0
        ].update(target="fail"),
        lambda value: value["experiment_a"]["arms"]["A2_typed_abstaining_critic_7b"].update(
            processor_identity_contract="different_processor"
        ),
        lambda value: value["experiment_a"]["a2_inheritance"]["allowed_differences"].remove(
            "model_id"
        ),
        lambda value: value["experiment_a"]["a2_inheritance"]["allowed_differences"].append(
            "prompt_sha256"
        ),
    ):
        changed = copy.deepcopy(loaded["contract"])
        mutation(changed)
        with pytest.raises(ValueError, match="A2|inherit|eight-check|status|processor"):
            amendment.validate_amendment(
                changed,
                loaded["surface_table"],
                routing_cases=loaded["routing_cases"],
                verify_bindings=False,
            )


def test_future_visible_evaluation_requires_the_exact_42_row_terminal_response_roster() -> None:
    loaded = amendment.load_amendment(AMENDMENT_ROOT)
    contract = loaded["contract"]
    schema = contract["experiment_a"]["schema_validity"]
    visible = contract["authoritative_evaluation_contract"]["future_closure"]["visible_evaluation"]

    assert schema["denominator"] == "sealed_test_terminal_response_roster_per_arm"
    assert schema["sealed_test_case_count_per_arm"] == 14
    assert schema["required_terminal_row_count"] == 42
    assert schema["provider_call_accounting"] == "separate_from_terminal_response_denominator"
    assert visible["arms"] == [
        "A0_current_critic_3b",
        "A1_typed_abstaining_critic_3b",
        "A2_typed_abstaining_critic_7b",
    ]
    assert visible["row_primary_key"] == ["arm", "case_id"]
    assert visible["case_count"] == 14
    assert visible["expected_terminal_row_count"] == 42
    assert len(visible["case_ids"]) == len(set(visible["case_ids"])) == 14
    assert visible["case_binding_fields"] == [
        "case_id",
        "group_id",
        "bundle_sha256",
        "artifacts",
    ]
    assert visible["invalid_terminal_states"] == [
        "missing_response",
        "schema_invalid",
        "provider_exception",
        "provider_timeout",
        "no_response",
    ]
    assert visible["missing_primary_key_policy"] == "reject_authoritative_closure"
    assert visible["row_requires_raw_journal_event_ref"] is True

    for mutation in (
        lambda value: value["experiment_a"]["schema_validity"].update(
            denominator="successful_provider_invocation_subset"
        ),
        lambda value: value["authoritative_evaluation_contract"]["future_closure"][
            "visible_evaluation"
        ].update(expected_terminal_row_count=41),
        lambda value: value["authoritative_evaluation_contract"]["future_closure"][
            "visible_evaluation"
        ]["invalid_terminal_states"].remove("provider_exception"),
    ):
        changed = copy.deepcopy(contract)
        mutation(changed)
        with pytest.raises(ValueError, match="schema|terminal|42|failed response|invalid states"):
            amendment.validate_amendment(
                changed,
                loaded["surface_table"],
                routing_cases=loaded["routing_cases"],
                verify_bindings=False,
            )


def _common_routing_payload(
    arm: str = "B2_typed_route_and_prompt_repair",
) -> dict[str, object]:
    prompt = amendment.load_amendment(AMENDMENT_ROOT)["contract"]["experiment_b"][
        "prompt_contracts"
    ][arm]
    return {
        "original_prompt": "Put an apple inside a basket.",
        "blinded_case_id": "blind-001",
        "invocation_id": "invocation-001",
        "routing_instruction": prompt["routing_instruction_utf8"],
        "prompt_template_version": prompt["prompt_template_version"],
        "seed": 7,
        "attempt": 1,
        "allowed_routes": list(amendment.ROUTE_VOCABULARY),
        "resource_reservation": {"gpu_time_ms": 0},
    }


def test_arm_projections_are_exact_and_gold_is_visible_only_to_the_oracle() -> None:
    contract = amendment.load_amendment(AMENDMENT_ROOT)["contract"]
    common = _common_routing_payload()

    assert (
        amendment.validate_arm_projection(
            contract,
            "B0_retry_unchanged",
            {**_common_routing_payload("B0_retry_unchanged"), "failure_code": "SCENE_SOLVE_ERROR"},
        )["failure_code"]
        == "SCENE_SOLVE_ERROR"
    )
    assert (
        amendment.validate_arm_projection(
            contract,
            "B1_generic_prompt_repair",
            {
                **_common_routing_payload("B1_generic_prompt_repair"),
                "untyped_failure_summary": "The bounded solver exhausted.",
            },
        )["untyped_failure_summary"]
        == "The bounded solver exhausted."
    )
    b2 = {
        **common,
        "typed_failure": {"failure_type": "infeasible_geometry"},
        "trusted_state": {"compile_status": "expected_rejection"},
        "asset_availability": {"relevant": False},
        "visible_report": None,
    }
    assert (
        amendment.validate_arm_projection(contract, "B2_typed_route_and_prompt_repair", b2)[
            "visible_report"
        ]
        is None
    )

    with pytest.raises(ValueError, match="fields mismatch"):
        amendment.validate_arm_projection(
            contract,
            "B2_typed_route_and_prompt_repair",
            {**b2, "gold_route": "abstain_infeasible_geometry"},
        )
    nested_gold = copy.deepcopy(b2)
    nested_gold["typed_failure"]["gold_route"] = "abstain_infeasible_geometry"
    with pytest.raises(ValueError, match="gold or annotation leakage"):
        amendment.validate_arm_projection(
            contract,
            "B2_typed_route_and_prompt_repair",
            nested_gold,
        )
    tuple_hidden_gold = copy.deepcopy(b2)
    tuple_hidden_gold["typed_failure"]["nested"] = ({"gold_route": "abstain_infeasible_geometry"},)
    with pytest.raises(ValueError, match="gold or annotation leakage"):
        amendment.validate_arm_projection(
            contract,
            "B2_typed_route_and_prompt_repair",
            tuple_hidden_gold,
        )
    with pytest.raises(ValueError, match="visible_report must remain null"):
        amendment.validate_arm_projection(
            contract,
            "B2_typed_route_and_prompt_repair",
            {**b2, "visible_report": {"status": "fail"}},
        )
    b3 = {
        **b2,
        **{
            key: value
            for key, value in _common_routing_payload("B3_oracle_route_ceiling").items()
            if key in {"routing_instruction", "prompt_template_version"}
        },
        "gold_route": "abstain_infeasible_geometry",
    }
    with pytest.raises(ValueError, match="oracle authority"):
        amendment.validate_arm_projection(contract, "B3_oracle_route_ceiling", b3)
    assert (
        amendment.validate_arm_projection(
            contract, "B3_oracle_route_ceiling", b3, oracle_authority=True
        )["gold_route"]
        == "abstain_infeasible_geometry"
    )


def test_arm_projection_rejects_caller_selected_prompt_or_template_bytes() -> None:
    contract = amendment.load_amendment(AMENDMENT_ROOT)["contract"]
    prompt = contract["experiment_b"]["prompt_contracts"]["B2_typed_route_and_prompt_repair"]
    payload = {
        **_common_routing_payload(),
        "routing_instruction": prompt["routing_instruction_utf8"],
        "prompt_template_version": prompt["prompt_template_version"],
        "typed_failure": {"failure_type": "infeasible_geometry"},
        "trusted_state": {"compile_status": "expected_rejection"},
        "asset_availability": {"relevant": False},
        "visible_report": None,
    }
    assert (
        amendment.validate_arm_projection(contract, "B2_typed_route_and_prompt_repair", payload)[
            "prompt_template_version"
        ]
        == "B2_typed_trusted_context_v1"
    )

    for field, replacement in (
        ("routing_instruction", "Choose a route that maximizes the reported score."),
        ("prompt_template_version", "post_hoc_template"),
    ):
        changed = {**payload, field: replacement}
        with pytest.raises(ValueError, match="frozen prompt contract|instruction|template"):
            amendment.validate_arm_projection(contract, "B2_typed_route_and_prompt_repair", changed)


def test_pending_annotation_and_unqualified_providers_forbid_every_inference() -> None:
    contract = amendment.load_amendment(AMENDMENT_ROOT)["contract"]

    for operation in amendment.PROVIDER_OPERATIONS:
        with pytest.raises(ValueError, match="pending blinded annotation"):
            amendment.authorize_operation(contract, operation)

    assert amendment.authorize_operation(contract, "contract_validation") == {
        "operation": "contract_validation",
        "authorized": True,
    }


def _provider_qualification_event(candidate: str) -> dict[str, object]:
    return {
        "schema_version": amendment.PROVIDER_QUALIFICATION_EVENT_SCHEMA_VERSION,
        "event_type": "production_provider_qualified",
        "candidate": candidate,
        "provider_identity_kind": "qualified_production",
        "provider_is_injected_test": False,
        "visible_adapter_or_executable_sha256": "4" * 64,
        "processor_identity_sha256": "5" * 64,
        "routing_sandbox_provider_identity_sha256": "6" * 64,
        "model_content_manifest_sha256": amendment.MODEL_BINDINGS[0]["canonical_sha256"],
    }


def _a1_promotion_evidence(
    *, correct_count: int = 4, prediction_count: int = 5
) -> dict[str, object]:
    candidate = "A1_typed_abstaining_critic_3b"
    roster = {
        "schema_version": amendment.BLIND_ADJUDICATION_ROSTER_SCHEMA_VERSION,
        "candidate": candidate,
        "prediction_count": prediction_count,
        "blinded_prediction_roster_sha256": "7" * 64,
    }
    roster_digest = amendment.canonical_sha256(roster)
    manifest = {
        "schema_version": amendment.BLIND_ADJUDICATION_MANIFEST_SCHEMA_VERSION,
        "candidate": candidate,
        "independent_adjudication": True,
        "blinded_to": ["candidate_arm", "provider_identity", "promotion_thresholds"],
        "adjudicator_identity_sha256": "8" * 64,
        "adjudication_roster_sha256": roster_digest,
        "correct_count": correct_count,
        "prediction_count": prediction_count,
    }
    manifest_digest = amendment.canonical_sha256(manifest)
    provider_event = _provider_qualification_event(candidate)
    provider_event_digest = amendment.canonical_sha256(provider_event)
    return {
        "A1_minus_A0_common_three_macro_f1_fail": "0.10",
        "A1_minus_A0_common_three_bootstrap_ci_lower": "0.01",
        "A1_common_three_coverage": "0.80",
        "A1_unsafe_visible_pass_count": 0,
        "A1_final_schema_valid_rate": "0.98",
        "A1_typed_correction_correct_count": correct_count,
        "A1_typed_correction_prediction_count": prediction_count,
        "A1_physical_authority_violation_count": 0,
        "A1_base_schema_valid_rate": "0.50",
        "A1_typed_correction_blind_adjudication_manifest_sha256": manifest_digest,
        "A1_typed_correction_adjudication_roster_sha256": roster_digest,
        "provider_qualification_journal_event_sha256": provider_event_digest,
        "provider_identity_kind": "qualified_production",
        "provider_is_injected_test": False,
        "bound_evidence_documents": {
            manifest_digest: manifest,
            roster_digest: roster,
            provider_event_digest: provider_event,
        },
    }


def test_promotion_rejects_digest_only_adjudication_and_provider_claims() -> None:
    loaded = amendment.load_amendment(AMENDMENT_ROOT)

    digest_only = _a1_promotion_evidence()
    del digest_only["bound_evidence_documents"]
    with pytest.raises(ValueError, match="bound evidence document"):
        amendment.promotion_decision(
            loaded["contract"],
            "A1_typed_abstaining_critic_3b",
            digest_only,
            routing_cases=loaded["routing_cases"],
        )

    tampered = _a1_promotion_evidence()
    manifest_digest = tampered["A1_typed_correction_blind_adjudication_manifest_sha256"]
    tampered["bound_evidence_documents"][manifest_digest]["correct_count"] = 3
    with pytest.raises(ValueError, match="bound evidence document digest"):
        amendment.promotion_decision(
            loaded["contract"],
            "A1_typed_abstaining_critic_3b",
            tampered,
            routing_cases=loaded["routing_cases"],
        )


def test_promotion_uses_decimal_semantics_and_zero_precision_denominator_fails_closed() -> None:
    loaded = amendment.load_amendment(AMENDMENT_ROOT)
    evidence = _a1_promotion_evidence()

    decision = amendment.promotion_decision(
        loaded["contract"],
        "A1_typed_abstaining_critic_3b",
        evidence,
        routing_cases=loaded["routing_cases"],
    )

    assert decision["metrics"]["A1_typed_correction_precision"] == "0.8"
    assert "A1_base_schema_valid_rate" not in decision["failed_gates"]
    assert decision["eligible"] is False
    assert decision["global_failures"] == [
        "pending_blinded_annotation",
        "production_provider_not_yet_qualified",
        "execution_not_authorized",
        "authoritative_evaluation_evidence_not_bound_by_amendment_01",
    ]

    zero = _a1_promotion_evidence(correct_count=0, prediction_count=0)
    zero_decision = amendment.promotion_decision(
        loaded["contract"],
        "A1_typed_abstaining_critic_3b",
        zero,
        routing_cases=loaded["routing_cases"],
    )
    assert zero_decision["metrics"]["A1_typed_correction_precision"] is None
    assert "A1_typed_correction_precision" in zero_decision["failed_gates"]

    almost_threshold = _a1_promotion_evidence(
        correct_count=8 * 10**29,
        prediction_count=10**30 + 1,
    )
    almost_decision = amendment.promotion_decision(
        loaded["contract"],
        "A1_typed_abstaining_critic_3b",
        almost_threshold,
        routing_cases=loaded["routing_cases"],
    )
    assert "A1_typed_correction_precision" in almost_decision["failed_gates"]

    with pytest.raises(ValueError, match="Decimal string"):
        amendment.promotion_decision(
            loaded["contract"],
            "A1_typed_abstaining_critic_3b",
            {**evidence, "A1_common_three_coverage": 0.80},
            routing_cases=loaded["routing_cases"],
        )
    with pytest.raises(ValueError, match="JSON integer"):
        amendment.promotion_decision(
            loaded["contract"],
            "A1_typed_abstaining_critic_3b",
            {**evidence, "A1_unsafe_visible_pass_count": False},
            routing_cases=loaded["routing_cases"],
        )


def test_reported_metrics_without_the_complete_terminal_roster_are_never_authoritative() -> None:
    loaded = amendment.load_amendment(AMENDMENT_ROOT)
    forged = _a1_promotion_evidence(correct_count=14, prediction_count=14)
    forged.update(
        A1_base_schema_valid_rate="1.0",
        A1_final_schema_valid_rate="1.0",
        A1_minus_A0_common_three_macro_f1_fail="1.0",
        A1_minus_A0_common_three_bootstrap_ci_lower="1.0",
    )

    decision = amendment.promotion_decision(
        loaded["contract"],
        "A1_typed_abstaining_critic_3b",
        forged,
        routing_cases=loaded["routing_cases"],
    )

    assert decision["evaluation_mode"] == "non_authoritative_dry_run"
    assert decision["authoritative"] is False
    assert decision["eligible"] is False
    assert (
        "authoritative_evaluation_evidence_not_bound_by_amendment_01" in decision["global_failures"]
    )


def _repeatability_evidence_documents(
    roster: dict[str, object],
) -> tuple[list[dict[str, object]], str, dict[str, object]]:
    noncontrols = [
        case
        for case in roster["cases"]  # type: ignore[index]
        if case["baseline_class"] == "unrecoverable_failure"
    ]
    receipts: list[dict[str, object]] = []
    documents: dict[str, object] = {}
    event_digests: list[str] = []
    for case_index, case in enumerate(noncontrols, start=1):
        for repeat_index in (1, 2, 3):
            event = {
                "schema_version": amendment.REPEATABILITY_EVENT_SCHEMA_VERSION,
                "event_type": "B2_repeatability_observation",
                "audit_id": "B2_repeatability_audit",
                "case_id": case["case_id"],
                "audit_group_id": case["repeatability_audit_group_id"],
                "repeat_index": repeat_index,
                "typed_input_sha256": amendment.repeatability_typed_input_sha256(
                    roster, case["case_id"]
                ),
                "route": case["gold_route"],
                "revised_prompt_sha256": f"{case_index + 10:064x}",
                "compile_attempt_count": 0,
                "physical_replay_count": 0,
            }
            event_digest = amendment.canonical_sha256(event)
            documents[event_digest] = event
            event_digests.append(event_digest)
            receipts.append({"journal_event_sha256": event_digest})
    manifest = {
        "schema_version": amendment.REPEATABILITY_JOURNAL_MANIFEST_SCHEMA_VERSION,
        "audit_id": "B2_repeatability_audit",
        "event_sha256s": sorted(event_digests),
        "event_count": 18,
        "case_count": 6,
        "repeat_indices": [1, 2, 3],
    }
    manifest_digest = amendment.canonical_sha256(manifest)
    documents[manifest_digest] = manifest
    return receipts, manifest_digest, documents


def _b2_promotion_evidence(roster: dict[str, object]) -> dict[str, object]:
    candidate = "B2_typed_route_and_prompt_repair"
    receipts, repeatability_manifest_digest, documents = _repeatability_evidence_documents(roster)
    provider_event = _provider_qualification_event(candidate)
    provider_event_digest = amendment.canonical_sha256(provider_event)
    documents[provider_event_digest] = provider_event
    return {
        "B2_minus_B1_group_weighted_exact_route_accuracy": "0.15",
        "B2_vs_B1_exact_mcnemar_p": "0.04",
        "conditional_B2_minus_B1_robust_completion": None,
        "conditional_B2_minus_B1_completion_bootstrap_ci_lower": None,
        "B2_clean_control_non_regression_rate": "1.0",
        "B2_safe_abstention_on_unrecoverable_rate": "1.0",
        "B2_intent_preservation_rate": "1.0",
        "B2_unsafe_publication_count": 0,
        "B2_physical_authority_violation_count": 0,
        "B2_repeatability_receipts": receipts,
        "B2_repeatability_journal_manifest_sha256": repeatability_manifest_digest,
        "provider_qualification_journal_event_sha256": provider_event_digest,
        "provider_identity_kind": "qualified_production",
        "provider_is_injected_test": False,
        "bound_evidence_documents": documents,
    }


def test_routing_roster_and_repeatability_are_derived_not_caller_asserted() -> None:
    loaded = amendment.load_amendment(AMENDMENT_ROOT)
    roster = loaded["routing_cases"]
    evidence = _b2_promotion_evidence(roster)

    decision = amendment.promotion_decision(
        loaded["contract"],
        "B2_typed_route_and_prompt_repair",
        evidence,
        routing_cases=roster,
    )

    assert decision["metrics"]["independent_recoverable_failure_groups"] == 0
    assert decision["metrics"]["conditional_B2_minus_B1_robust_completion"] is None
    assert decision["metrics"]["B2_repeatability_cases_3_of_3"] == 6
    assert "independent_recoverable_failure_groups" in decision["failed_gates"]
    assert "conditional_B2_minus_B1_robust_completion" in decision["failed_gates"]

    leaked_aggregate = {**evidence, "B2_repeatability_cases_3_of_3": 6}
    with pytest.raises(ValueError, match="fields mismatch"):
        amendment.promotion_decision(
            loaded["contract"],
            "B2_typed_route_and_prompt_repair",
            leaked_aggregate,
            routing_cases=roster,
        )

    missing_case = {
        **evidence,
        "B2_repeatability_receipts": evidence["B2_repeatability_receipts"][:-3],
    }
    with pytest.raises(ValueError, match="exact 6-case x 3-repeat roster"):
        amendment.promotion_decision(
            loaded["contract"],
            "B2_typed_route_and_prompt_repair",
            missing_case,
            routing_cases=roster,
        )

    forged_typed_input = copy.deepcopy(evidence)
    documents = forged_typed_input["bound_evidence_documents"]
    for receipt in forged_typed_input["B2_repeatability_receipts"][:3]:
        old_digest = receipt["journal_event_sha256"]
        event = documents.pop(old_digest)
        event["typed_input_sha256"] = "f" * 64
        new_digest = amendment.canonical_sha256(event)
        documents[new_digest] = event
        receipt["journal_event_sha256"] = new_digest
    old_manifest_digest = forged_typed_input["B2_repeatability_journal_manifest_sha256"]
    journal_manifest = documents.pop(old_manifest_digest)
    journal_manifest["event_sha256s"] = sorted(
        receipt["journal_event_sha256"]
        for receipt in forged_typed_input["B2_repeatability_receipts"]
    )
    new_manifest_digest = amendment.canonical_sha256(journal_manifest)
    documents[new_manifest_digest] = journal_manifest
    forged_typed_input["B2_repeatability_journal_manifest_sha256"] = new_manifest_digest
    with pytest.raises(ValueError, match="frozen typed input digest"):
        amendment.promotion_decision(
            loaded["contract"],
            "B2_typed_route_and_prompt_repair",
            forged_typed_input,
            routing_cases=roster,
        )

    forged_failure = copy.deepcopy(roster)
    forged_failure["cases"][0]["baseline_class"] = "recoverable_baseline_failure"
    with pytest.raises(ValueError, match="failure|30/0/6"):
        amendment.validate_routing_cases(forged_failure)


def _typed_intent() -> dict[str, object]:
    return {
        "entity": [
            {"role": "source", "category": "apple"},
            {"role": "target", "category": "basket"},
        ],
        "count": {"source": 1, "target": 1},
        "color": {"source": "red", "target": None},
        "relation": [{"source": "source", "predicate": "inside", "target": "target"}],
        "articulation": {"target": "target", "state": "open"},
        "region": {"target": "source", "name": "back"},
    }


def test_bilingual_surface_terms_canonicalize_but_protected_intent_cannot_change() -> None:
    table = amendment.load_amendment(AMENDMENT_ROOT)["surface_table"]

    assert amendment.canonicalize_surface_term(table, "entity", "zh-Hans", "苹果") == "apple"
    assert amendment.canonicalize_surface_term(table, "color", "en", "  RED  ") == "red"
    assert amendment.canonicalize_surface_term(table, "relation", "zh-Hans", "放进") == "inside"
    with pytest.raises(ValueError, match="unsupported language surface"):
        amendment.canonicalize_surface_term(table, "entity", "en", "pear")

    before = _typed_intent()
    assert amendment.validate_intent_preservation(before, copy.deepcopy(before)) is True
    for dimension in amendment.PROTECTED_DIMENSIONS:
        changed = copy.deepcopy(before)
        if isinstance(changed[dimension], list):
            changed[dimension] = changed[dimension][:-1]
        else:
            changed[dimension] = {}
        with pytest.raises(ValueError, match=dimension):
            amendment.validate_intent_preservation(before, changed)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (
            lambda value: value["experiment_a"]["cross_arm_comparison"]["mapping"][1].update(
                A1="table_contact"
            ),
            "common-three mapping",
        ),
        (
            lambda value: value["experiment_a"]["status_mapping"]["A0_current_critic_3b"][2].update(
                target="fail"
            ),
            "warning mapping",
        ),
        (
            lambda value: value["promotion_contract"]["candidates"][
                "A1_typed_abstaining_critic_3b"
            ]["gates"][0].update(threshold="0.09"),
            "threshold|promotion gate",
        ),
        (
            lambda value: value["promotion_contract"]["candidates"][
                "A1_typed_abstaining_critic_3b"
            ]["gates"][0].update(threshold=0.10),
            "Decimal string",
        ),
        (
            lambda value: value["promotion_contract"]["candidates"][
                "A1_typed_abstaining_critic_3b"
            ]["gates"][3].update(threshold=False),
            "JSON integer",
        ),
        (
            lambda value: value["experiment_a"]["bootstrap"].update(
                per_resample_computation="average precomputed scalar deltas"
            ),
            "recompute both arm macro-F1",
        ),
        (
            lambda value: value["experiment_b"]["repeatability_audit"].update(
                compile_attempt_budget=1
            ),
            "repeatability compile budget",
        ),
        (
            lambda value: value["execution_gate"].update(production_provider_state="qualified"),
            "production provider state",
        ),
        (
            lambda value: value["execution_gate"].update(execution_authorized=True),
            "execution authorization",
        ),
    ],
)
def test_semantic_contract_mutations_are_rejected(mutation, match: str) -> None:
    loaded = amendment.load_amendment(AMENDMENT_ROOT)
    contract = copy.deepcopy(loaded["contract"])
    mutation(contract)

    with pytest.raises(ValueError, match=match):
        amendment.validate_amendment(
            contract,
            loaded["surface_table"],
            routing_cases=loaded["routing_cases"],
            verify_bindings=False,
        )


def test_bound_files_and_model_content_manifests_validate_without_loading_models() -> None:
    loaded = amendment.load_amendment(AMENDMENT_ROOT, verify_bindings=True)

    assert loaded["summary"]["contract_sha256"] == (
        "53e080f8ea03fcd36f88133a0445301a5c144281d5ee5bfbf08351f66ef480b2"
    )
    assert loaded["summary"]["routing_baseline_classes"] == {
        "clean_control": 30,
        "recoverable_baseline_failure": 0,
        "unrecoverable_failure": 6,
    }

    manifest = {
        "schema_version": amendment.MODEL_CONTENT_SCHEMA_VERSION,
        "model_id": "fixture/model",
        "revision": "a" * 40,
        "entries": [
            {
                "path": "config.json",
                "blob_id": "b" * 64,
                "blob_id_algorithm": "sha256",
                "size_bytes": 7,
                "content_sha256": "c" * 64,
            }
        ],
    }
    binding = {
        "model_id": "fixture/model",
        "revision": "a" * 40,
        "canonical_sha256": amendment.canonical_sha256(manifest),
    }
    assert amendment.validate_model_content_manifest(manifest, expected_binding=binding) == {
        "entry_count": 1,
        "total_bytes": 7,
    }
    malformed = copy.deepcopy(manifest)
    malformed["entries"][0]["unexpected"] = True
    with pytest.raises(ValueError, match="fields mismatch"):
        amendment.validate_model_content_manifest(malformed, expected_binding=binding)
    drive_relative = copy.deepcopy(manifest)
    drive_relative["entries"][0]["path"] = "C:config.json"
    drive_binding = {**binding, "canonical_sha256": amendment.canonical_sha256(drive_relative)}
    with pytest.raises(ValueError, match="path is invalid"):
        amendment.validate_model_content_manifest(
            drive_relative,
            expected_binding=drive_binding,
        )


def _validate_contract_mutation(mutation, match: str) -> None:
    loaded = amendment.load_amendment(AMENDMENT_ROOT)
    contract = copy.deepcopy(loaded["contract"])
    mutation(contract)
    with pytest.raises(ValueError, match=match):
        amendment.validate_amendment(
            contract,
            loaded["surface_table"],
            routing_cases=loaded["routing_cases"],
            verify_bindings=False,
        )


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (
            lambda value: value["experiment_a"]["typed_correction_precision"][
                "zero_denominator"
            ].update(metric_value="0"),
            "zero denominator",
        ),
        (
            lambda value: value["experiment_a"]["bootstrap"].update(
                forbidden_shortcut="average group deltas"
            ),
            "scalar mean-delta",
        ),
        (
            lambda value: value["experiment_b"]["arm_projections"]["B0_retry_unchanged"].update(
                visible_report_effective_value={}
            ),
            "visible report must remain null",
        ),
        (
            lambda value: value["experiment_b"]["visible_report_unlock"].update(
                B2_and_B3_current_value={}
            ),
            "visible report must be null",
        ),
        (
            lambda value: value["experiment_b"]["routing_roster"].update(
                clean_control_B0_B1_B2_action="accept_existing_compile"
            ),
            "runner no-op accepts",
        ),
        (
            lambda value: value["experiment_b"]["routing_roster"][
                "zero_completion_denominator"
            ].update(metric_value="0"),
            "zero recoverable-failure denominator",
        ),
        (
            lambda value: value["promotion_contract"]["candidates"][
                "A1_typed_abstaining_critic_3b"
            ].update(gates=[]),
            "promotion gates are incomplete",
        ),
        (
            lambda value: value["promotion_contract"]["candidates"][
                "A1_typed_abstaining_critic_3b"
            ]["gates"].__setitem__(0, "invalid"),
            "must be an object",
        ),
        (
            lambda value: value["promotion_contract"]["candidates"][
                "A1_typed_abstaining_critic_3b"
            ]["gates"][0].update(gate_type="unknown"),
            "gate_type is invalid",
        ),
        (
            lambda value: value["promotion_contract"]["candidates"][
                "A1_typed_abstaining_critic_3b"
            ]["gates"][0].update(value_type="boolean"),
            "value_type must be decimal or integer",
        ),
        (
            lambda value: value["promotion_contract"]["candidates"][
                "A1_typed_abstaining_critic_3b"
            ]["gates"][0].update(operator="ne"),
            "operator is invalid",
        ),
        (
            lambda value: value["promotion_contract"]["candidates"][
                "A1_typed_abstaining_critic_3b"
            ]["gates"][5].update(numerator_metric="A1_unbound_count"),
            "bound count evidence",
        ),
        (
            lambda value: value["promotion_contract"]["production_evidence"].update(
                injected_test_provider_counts_as_production=True
            ),
            "journal-bound and not self-reported",
        ),
        (
            lambda value: value["execution_gate"].update(
                self_reported_provider_identity_is_authority=True
            ),
            "cannot authorize production evidence",
        ),
        (
            lambda value: value["execution_gate"].update(future_unlock_requires=[]),
            "unlock requirements are incomplete",
        ),
        (
            lambda value: value["execution_gate"]["future_unlock_requires"].__setitem__(
                1, "replacement requirement"
            ),
            "exact visible provider and processor",
        ),
        (
            lambda value: value["execution_gate"]["future_unlock_requires"].__setitem__(
                2, "replacement requirement"
            ),
            "exact routing sandbox provider",
        ),
        (
            lambda value: value["canonicalization_contract"].update(
                preserve_multiplicity_and_roles=False
            ),
            "preserve protected intent",
        ),
    ],
)
def test_every_compound_frozen_contract_guard_rejects_semantic_drift(mutation, match: str) -> None:
    _validate_contract_mutation(mutation, match)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda value: value["vocabulary"].update(entity=[]), "non-empty list"),
        (
            lambda value: value["vocabulary"]["entity"][0].update(canonical=""),
            "canonical must be non-empty",
        ),
        (
            lambda value: value["vocabulary"]["entity"][0].update(en=[]),
            "entity.*en is invalid",
        ),
        (
            lambda value: value["vocabulary"]["entity"][1]["en"].__setitem__(
                0, value["vocabulary"]["entity"][0]["en"][0]
            ),
            "ambiguous surfaces",
        ),
        (
            lambda value: value["vocabulary"]["entity"].reverse(),
            "canonical entries must be unique/sorted",
        ),
        (
            lambda value: value["preservation"].update(deletion_allowed=True),
            "cannot allow protected-field deletion",
        ),
        (
            lambda value: value["preservation"].update(insertion_allowed=True),
            "cannot allow protected-field insertion",
        ),
        (
            lambda value: value["normalization"].update(unicode_form="NFC"),
            "mapping digest mismatch",
        ),
    ],
)
def test_surface_table_rejects_ambiguous_or_intent_losing_mutations(mutation, match: str) -> None:
    loaded = amendment.load_amendment(AMENDMENT_ROOT)
    table = copy.deepcopy(loaded["surface_table"])
    mutation(table)

    with pytest.raises(ValueError, match=match):
        amendment.validate_amendment(
            loaded["contract"],
            table,
            routing_cases=loaded["routing_cases"],
            verify_bindings=False,
        )


def _noncontrol_index(roster: dict[str, object]) -> int:
    return next(
        index
        for index, case in enumerate(roster["cases"])  # type: ignore[index]
        if case["baseline_class"] == "unrecoverable_failure"
    )


def _collapse_one_singleton_audit_group(roster: dict[str, object]) -> None:
    noncontrols = [
        case
        for case in roster["cases"]  # type: ignore[index]
        if case["baseline_class"] == "unrecoverable_failure"
    ]
    noncontrols[-1]["repeatability_audit_group_id"] = noncontrols[0]["repeatability_audit_group_id"]


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (
            lambda value: value["class_contract"]["clean_control"].update(retry_allowed=True),
            "no-op",
        ),
        (
            lambda value: value["class_contract"]["recoverable_baseline_failure"].update(
                counts_in_completion_denominator=False
            ),
            "only completion denominator",
        ),
        (
            lambda value: value["class_contract"]["unrecoverable_failure"].update(
                counts_in_completion_denominator=True
            ),
            "cannot enter the completion denominator",
        ),
        (lambda value: value.update(cases=[]), "exactly 36 cases"),
        (lambda value: value["cases"][0].update(case_id=""), "must be non-empty text"),
        (lambda value: value["cases"][0].update(split="holdout"), "split is invalid"),
        (lambda value: value["cases"][0].update(seed=False), "JSON integer"),
        (
            lambda value: value["cases"][0].update(baseline_class="unknown"),
            "baseline_class is invalid",
        ),
        (
            lambda value: value["cases"][0].update(failure={}),
            "clean controls must have null failure",
        ),
        (
            lambda value: value["cases"][_noncontrol_index(value)]["failure"].update(
                failure_code=""
            ),
            "failure.failure_code is invalid",
        ),
        (
            lambda value: value["cases"][_noncontrol_index(value)]["failure"].update(
                typed_failure={}
            ),
            "failure.typed_failure is invalid",
        ),
        (
            lambda value: value["cases"][_noncontrol_index(value)].update(
                gold_route="accept_existing_compile"
            ),
            "typed abstention gold",
        ),
        (
            lambda value: value["cases"][_noncontrol_index(value)].update(
                repeatability_audit_group_id=None
            ),
            "must enter the repeatability audit",
        ),
        (
            lambda value: value["cases"][1].update(case_id=value["cases"][0]["case_id"]),
            "case_id values must be unique",
        ),
        (
            _collapse_one_singleton_audit_group,
            "four audit groups",
        ),
        (
            lambda value: value["cases"][0].update(original_prompt="A changed prompt."),
            "canonical digest mismatch",
        ),
    ],
)
def test_routing_roster_rejects_every_classification_and_audit_boundary(
    mutation, match: str
) -> None:
    roster = copy.deepcopy(amendment.load_amendment(AMENDMENT_ROOT)["routing_cases"])
    mutation(roster)
    with pytest.raises(ValueError, match=match):
        amendment.validate_routing_cases(roster)


def test_routing_roster_fails_closed_against_unavailable_or_divergent_base_spec() -> None:
    loaded = amendment.load_amendment(AMENDMENT_ROOT)
    roster = loaded["routing_cases"]
    base_spec = json.loads((STUDY_ROOT / "experiment_spec.json").read_text(encoding="utf-8"))

    with pytest.raises(ValueError, match="experiments are unavailable"):
        amendment.validate_routing_cases(roster, base_spec={})
    with pytest.raises(ValueError, match="B samples are unavailable"):
        amendment.validate_routing_cases(roster, base_spec={"experiments": []})

    no_samples = copy.deepcopy(base_spec)
    experiment = next(
        item
        for item in no_samples["experiments"]
        if item["experiment_id"] == "B_typed_failure_prompt_fallback"
    )
    experiment["samples"] = []
    with pytest.raises(ValueError, match="must contain 36 samples"):
        amendment.validate_routing_cases(roster, base_spec=no_samples)

    divergent = copy.deepcopy(base_spec)
    experiment = next(
        item
        for item in divergent["experiments"]
        if item["experiment_id"] == "B_typed_failure_prompt_fallback"
    )
    experiment["samples"][0]["task_context"] = "A divergent prompt."
    with pytest.raises(ValueError, match="diverges from base spec"):
        amendment.validate_routing_cases(roster, base_spec=divergent)


def _fixture_model_manifest() -> tuple[dict[str, object], dict[str, object]]:
    manifest: dict[str, object] = {
        "schema_version": amendment.MODEL_CONTENT_SCHEMA_VERSION,
        "model_id": "fixture/model",
        "revision": "a" * 40,
        "entries": [
            {
                "path": "config.json",
                "blob_id": "b" * 64,
                "blob_id_algorithm": "sha256",
                "size_bytes": 7,
                "content_sha256": "c" * 64,
            }
        ],
    }
    binding: dict[str, object] = {
        "model_id": "fixture/model",
        "revision": "a" * 40,
        "canonical_sha256": amendment.canonical_sha256(manifest),
    }
    return manifest, binding


def test_model_manifest_accepts_both_blob_algorithms_and_rejects_bad_rosters() -> None:
    manifest, _ = _fixture_model_manifest()
    manifest["entries"].append(  # type: ignore[union-attr]
        {
            "path": "weights.bin",
            "blob_id": "d" * 40,
            "blob_id_algorithm": "git_blob_sha1",
            "size_bytes": 11,
            "content_sha256": "e" * 64,
        }
    )
    binding = {
        "model_id": "fixture/model",
        "revision": "a" * 40,
        "canonical_sha256": amendment.canonical_sha256(manifest),
    }
    assert amendment.validate_model_content_manifest(manifest, expected_binding=binding) == {
        "entry_count": 2,
        "total_bytes": 18,
    }

    for mutate, match in (
        (lambda value: value.update(entries=[]), "entries must be non-empty"),
        (
            lambda value: value["entries"][0].update(blob_id_algorithm="md5"),
            "blob_id_algorithm is invalid",
        ),
        (
            lambda value: value["entries"][0].update(blob_id="A" * 64),
            "lowercase hex",
        ),
        (
            lambda value: value["entries"].append(copy.deepcopy(value["entries"][0])),
            "unique and sorted",
        ),
    ):
        malformed, _ = _fixture_model_manifest()
        mutate(malformed)
        malformed_binding = {
            "model_id": "fixture/model",
            "revision": "a" * 40,
            "canonical_sha256": amendment.canonical_sha256(malformed),
        }
        with pytest.raises(ValueError, match=match):
            amendment.validate_model_content_manifest(
                malformed,
                expected_binding=malformed_binding,
            )


def _copy_file_backed_bundle(tmp_path: Path) -> tuple[Path, Path]:
    study = tmp_path / "study"
    bundle = study / "amendments" / "01"
    shutil.copytree(AMENDMENT_ROOT, bundle)
    shutil.copy2(STUDY_ROOT / "experiment_spec.json", study / "experiment_spec.json")
    shutil.copy2(STUDY_ROOT / "run_log.jsonl", study / "run_log.jsonl")
    return study, bundle


@pytest.mark.parametrize(
    ("invalid_json", "match"),
    [
        ('{"a":1,"a":2}', "duplicate object key"),
        ('{"value":NaN}', "non-finite JSON number"),
        ("[]", "must contain a JSON object"),
    ],
)
def test_load_amendment_rejects_noncanonical_json_documents(
    tmp_path: Path, invalid_json: str, match: str
) -> None:
    _, bundle = _copy_file_backed_bundle(tmp_path)
    (bundle / "amendment_contract.json").write_text(invalid_json, encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        amendment.load_amendment(bundle)


def test_file_backed_binding_rejects_escape_nonfile_and_digest_drift(tmp_path: Path) -> None:
    study, bundle = _copy_file_backed_bundle(tmp_path / "escape")
    outside = tmp_path / "outside-spec.json"
    shutil.copy2(STUDY_ROOT / "experiment_spec.json", outside)
    (study / "experiment_spec.json").unlink()
    (study / "experiment_spec.json").symlink_to(outside)
    with pytest.raises(ValueError, match="escapes its allowed root"):
        amendment.load_amendment(bundle, verify_bindings=True)

    _, bundle = _copy_file_backed_bundle(tmp_path / "nonfile")
    (bundle / "model_content_3b.json").unlink()
    (bundle / "model_content_3b.json").mkdir()
    with pytest.raises(ValueError, match="bound path is not a file"):
        amendment.load_amendment(bundle, verify_bindings=True)

    study, bundle = _copy_file_backed_bundle(tmp_path / "spec-drift")
    with (study / "experiment_spec.json").open("a", encoding="utf-8") as stream:
        stream.write("\n")
    with pytest.raises(ValueError, match="original spec file digest mismatch"):
        amendment.load_amendment(bundle, verify_bindings=True)

    study, bundle = _copy_file_backed_bundle(tmp_path / "short-log")
    (study / "run_log.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="six-line prefix"):
        amendment.load_amendment(bundle, verify_bindings=True)

    study, bundle = _copy_file_backed_bundle(tmp_path / "log-drift")
    (study / "run_log.jsonl").write_text("{}\n" * 6, encoding="utf-8")
    with pytest.raises(ValueError, match="run-log prefix digest mismatch"):
        amendment.load_amendment(bundle, verify_bindings=True)


def test_roster_rejects_a_well_formed_case_that_changes_the_frozen_class_split() -> None:
    roster = copy.deepcopy(amendment.load_amendment(AMENDMENT_ROOT)["routing_cases"])
    source = roster["cases"][_noncontrol_index(roster)]
    roster["cases"][0].update(
        baseline_class="unrecoverable_failure",
        failure=copy.deepcopy(source["failure"]),
        gold_route=source["gold_route"],
        repeatability_audit_group_id=source["repeatability_audit_group_id"],
    )
    with pytest.raises(ValueError, match="frozen 30/0/6 split"):
        amendment.validate_routing_cases(roster)


def test_contract_digest_guard_catches_semantically_uninspected_text() -> None:
    loaded = amendment.load_amendment(AMENDMENT_ROOT)
    contract = copy.deepcopy(loaded["contract"])
    contract["integration_contract"]["reason_spec_v2_not_bound"] = "changed prose"

    with pytest.raises(ValueError, match="contract canonical digest mismatch|binding reason"):
        amendment.validate_amendment(
            contract,
            loaded["surface_table"],
            routing_cases=loaded["routing_cases"],
        )


def _b2_projection_payload() -> dict[str, object]:
    return {
        **_common_routing_payload(),
        "typed_failure": {"failure_type": "infeasible_geometry"},
        "trusted_state": {"compile_status": "expected_rejection"},
        "asset_availability": {"relevant": False},
        "visible_report": None,
    }


def test_arm_projection_guards_all_public_input_boundaries() -> None:
    contract = amendment.load_amendment(AMENDMENT_ROOT)["contract"]

    changed_contract = copy.deepcopy(contract)
    changed_contract["integration_contract"]["reason_spec_v2_not_bound"] = "changed"
    with pytest.raises(ValueError, match="exact amendment 01 contract"):
        amendment.validate_arm_projection(
            changed_contract,
            "B2_typed_route_and_prompt_repair",
            _b2_projection_payload(),
        )
    with pytest.raises(ValueError, match="unknown B arm"):
        amendment.validate_arm_projection(contract, "B9_unknown", _common_routing_payload())

    unserializable = _b2_projection_payload()
    unserializable["resource_reservation"] = {"value": object()}
    with pytest.raises(ValueError, match="canonical-JSON serializable"):
        amendment.validate_arm_projection(
            contract,
            "B2_typed_route_and_prompt_repair",
            unserializable,
        )

    empty_text = _b2_projection_payload()
    empty_text["original_prompt"] = ""
    with pytest.raises(ValueError, match="original_prompt must be non-empty text"):
        amendment.validate_arm_projection(
            contract,
            "B2_typed_route_and_prompt_repair",
            empty_text,
        )

    null_seed = _b2_projection_payload()
    null_seed["seed"] = None
    assert (
        amendment.validate_arm_projection(
            contract,
            "B2_typed_route_and_prompt_repair",
            null_seed,
        )["seed"]
        is None
    )

    bad_reservation = _b2_projection_payload()
    bad_reservation["resource_reservation"] = []
    with pytest.raises(ValueError, match="resource_reservation must be an object"):
        amendment.validate_arm_projection(
            contract,
            "B2_typed_route_and_prompt_repair",
            bad_reservation,
        )

    with pytest.raises(ValueError, match="B0 failure_code must be non-empty text"):
        amendment.validate_arm_projection(
            contract,
            "B0_retry_unchanged",
            {**_common_routing_payload("B0_retry_unchanged"), "failure_code": ""},
        )
    with pytest.raises(ValueError, match="B1 untyped_failure_summary must be non-empty text"):
        amendment.validate_arm_projection(
            contract,
            "B1_generic_prompt_repair",
            {**_common_routing_payload("B1_generic_prompt_repair"), "untyped_failure_summary": ""},
        )

    empty_typed = _b2_projection_payload()
    empty_typed["typed_failure"] = {}
    with pytest.raises(ValueError, match="typed_failure must be a non-empty object"):
        amendment.validate_arm_projection(
            contract,
            "B2_typed_route_and_prompt_repair",
            empty_typed,
        )

    invalid_gold = {
        **_b2_projection_payload(),
        **{
            key: value
            for key, value in _common_routing_payload("B3_oracle_route_ceiling").items()
            if key in {"routing_instruction", "prompt_template_version"}
        },
        "gold_route": "invented_route",
    }
    with pytest.raises(ValueError, match="gold_route must be in the frozen route vocabulary"):
        amendment.validate_arm_projection(
            contract,
            "B3_oracle_route_ceiling",
            invalid_gold,
            oracle_authority=True,
        )
    with pytest.raises(ValueError, match="oracle authority is valid only for B3"):
        amendment.validate_arm_projection(
            contract,
            "B2_typed_route_and_prompt_repair",
            _b2_projection_payload(),
            oracle_authority=True,
        )


def test_operation_authorization_fails_closed_for_bad_contract_and_unknown_names() -> None:
    contract = amendment.load_amendment(AMENDMENT_ROOT)["contract"]
    changed_contract = copy.deepcopy(contract)
    changed_contract["integration_contract"]["reason_spec_v2_not_bound"] = "changed"
    with pytest.raises(ValueError, match="exact amendment 01 contract"):
        amendment.authorize_operation(changed_contract, "contract_validation")
    with pytest.raises(ValueError, match="operation must be non-empty text"):
        amendment.authorize_operation(contract, "")
    with pytest.raises(ValueError, match="unknown operation is forbidden"):
        amendment.authorize_operation(contract, "unregistered_operation")


def test_surface_and_intent_helpers_reject_bad_public_inputs() -> None:
    table = amendment.load_amendment(AMENDMENT_ROOT)["surface_table"]
    for dimension, language, surface, match in (
        ("unknown", "en", "apple", "unknown protected dimension"),
        ("entity", "fr", "apple", "unsupported canonicalization language"),
        ("entity", "en", "", "surface must be non-empty text"),
    ):
        with pytest.raises(ValueError, match=match):
            amendment.canonicalize_surface_term(table, dimension, language, surface)

    intent = _typed_intent()
    malformed = copy.deepcopy(intent)
    malformed["entity"] = {"not", "json"}
    with pytest.raises(ValueError, match="entity is not canonical JSON"):
        amendment.validate_intent_preservation(intent, malformed)


def _a2_promotion_evidence() -> dict[str, object]:
    candidate = "A2_typed_abstaining_critic_7b"
    roster = {
        "schema_version": amendment.BLIND_ADJUDICATION_ROSTER_SCHEMA_VERSION,
        "candidate": candidate,
        "prediction_count": 5,
        "blinded_prediction_roster_sha256": "7" * 64,
    }
    roster_digest = amendment.canonical_sha256(roster)
    manifest = {
        "schema_version": amendment.BLIND_ADJUDICATION_MANIFEST_SCHEMA_VERSION,
        "candidate": candidate,
        "independent_adjudication": True,
        "blinded_to": ["candidate_arm", "provider_identity", "promotion_thresholds"],
        "adjudicator_identity_sha256": "8" * 64,
        "adjudication_roster_sha256": roster_digest,
        "correct_count": 4,
        "prediction_count": 5,
    }
    manifest_digest = amendment.canonical_sha256(manifest)
    provider_event = _provider_qualification_event(candidate)
    provider_event["model_content_manifest_sha256"] = amendment.MODEL_BINDINGS[1][
        "canonical_sha256"
    ]
    provider_event_digest = amendment.canonical_sha256(provider_event)
    return {
        "A2_minus_A0_common_three_macro_f1_fail": "0.10",
        "A2_minus_A0_common_three_bootstrap_ci_lower": "0.01",
        "A2_common_three_coverage": "0.80",
        "A2_unsafe_visible_pass_count": 0,
        "A2_final_schema_valid_rate": "0.98",
        "A2_typed_correction_correct_count": 4,
        "A2_typed_correction_prediction_count": 5,
        "A2_physical_authority_violation_count": 0,
        "A2_minus_A1_common_three_macro_f1_fail": "0.05",
        "A2_mean_latency_ratio_over_A1": "2.00",
        "A2_base_schema_valid_rate": "0.50",
        "A2_typed_correction_blind_adjudication_manifest_sha256": manifest_digest,
        "A2_typed_correction_adjudication_roster_sha256": roster_digest,
        "provider_qualification_journal_event_sha256": provider_event_digest,
        "provider_identity_kind": "qualified_production",
        "provider_is_injected_test": False,
        "bound_evidence_documents": {
            manifest_digest: manifest,
            roster_digest: roster,
            provider_event_digest: provider_event,
        },
    }


def test_a2_and_fraction_rendering_cover_all_frozen_numeric_operators() -> None:
    loaded = amendment.load_amendment(AMENDMENT_ROOT)
    a2 = amendment.promotion_decision(
        loaded["contract"],
        "A2_typed_abstaining_critic_7b",
        _a2_promotion_evidence(),
        routing_cases=loaded["routing_cases"],
    )
    assert a2["metrics"]["A2_mean_latency_ratio_over_A1"] == "2.00"
    assert a2["metrics"]["A2_typed_correction_precision"] == "0.8"

    one_eighth = amendment.promotion_decision(
        loaded["contract"],
        "A1_typed_abstaining_critic_3b",
        _a1_promotion_evidence(correct_count=1, prediction_count=8),
        routing_cases=loaded["routing_cases"],
    )
    assert one_eighth["metrics"]["A1_typed_correction_precision"] == "0.125"
    whole = amendment.promotion_decision(
        loaded["contract"],
        "A1_typed_abstaining_critic_3b",
        _a1_promotion_evidence(correct_count=1, prediction_count=1),
        routing_cases=loaded["routing_cases"],
    )
    assert whole["metrics"]["A1_typed_correction_precision"] == "1"


def _rebind_repeatability_event(evidence: dict[str, object], receipt_index: int, mutation) -> None:
    receipts = evidence["B2_repeatability_receipts"]
    documents = evidence["bound_evidence_documents"]
    receipt = receipts[receipt_index]
    old_event_digest = receipt["journal_event_sha256"]
    event = documents.pop(old_event_digest)
    mutation(event)
    new_event_digest = amendment.canonical_sha256(event)
    documents[new_event_digest] = event
    receipt["journal_event_sha256"] = new_event_digest

    old_manifest_digest = evidence["B2_repeatability_journal_manifest_sha256"]
    manifest = documents.pop(old_manifest_digest)
    manifest["event_sha256s"] = sorted(item["journal_event_sha256"] for item in receipts)
    new_manifest_digest = amendment.canonical_sha256(manifest)
    documents[new_manifest_digest] = manifest
    evidence["B2_repeatability_journal_manifest_sha256"] = new_manifest_digest


def test_repeatability_receipts_reject_each_independent_integrity_failure() -> None:
    loaded = amendment.load_amendment(AMENDMENT_ROOT)
    roster = loaded["routing_cases"]

    duplicate_digest = _b2_promotion_evidence(roster)
    duplicate_digest["B2_repeatability_receipts"][1] = copy.deepcopy(
        duplicate_digest["B2_repeatability_receipts"][0]
    )
    with pytest.raises(ValueError, match="event digests must be unique"):
        amendment.promotion_decision(
            loaded["contract"],
            "B2_typed_route_and_prompt_repair",
            duplicate_digest,
            routing_cases=roster,
        )

    cases = (
        (lambda event: event.update(case_id="outside-case"), "outside the frozen six-case roster"),
        (lambda event: event.update(repeat_index=4), "repeat_index must be exactly"),
        (
            lambda event: event.update(repeat_index=1, revised_prompt_sha256="a" * 64),
            "duplicate case/repeat pair",
        ),
        (lambda event: event.update(route="invented_route"), "outside the frozen vocabulary"),
    )
    for mutation, match in cases:
        invalid = _b2_promotion_evidence(roster)
        _rebind_repeatability_event(invalid, 1, mutation)
        with pytest.raises(ValueError, match=match):
            amendment.promotion_decision(
                loaded["contract"],
                "B2_typed_route_and_prompt_repair",
                invalid,
                routing_cases=roster,
            )

    nonrepeatable = _b2_promotion_evidence(roster)
    _rebind_repeatability_event(
        nonrepeatable,
        1,
        lambda event: event.update(revised_prompt_sha256="f" * 64),
    )
    decision = amendment.promotion_decision(
        loaded["contract"],
        "B2_typed_route_and_prompt_repair",
        nonrepeatable,
        routing_cases=roster,
    )
    assert decision["metrics"]["B2_repeatability_cases_3_of_3"] == 5


def test_promotion_rejects_bad_candidates_documents_and_impossible_counts() -> None:
    loaded = amendment.load_amendment(AMENDMENT_ROOT)
    contract = loaded["contract"]
    roster = loaded["routing_cases"]

    changed_contract = copy.deepcopy(contract)
    changed_contract["integration_contract"]["reason_spec_v2_not_bound"] = "changed"
    with pytest.raises(ValueError, match="exact amendment 01 contract"):
        amendment.promotion_decision(
            changed_contract,
            "A1_typed_abstaining_critic_3b",
            _a1_promotion_evidence(),
        )
    with pytest.raises(ValueError, match="not a promotion candidate"):
        amendment.promotion_decision(contract, "B0_retry_unchanged", {})

    nonmap = _a1_promotion_evidence()
    nonmap["bound_evidence_documents"] = []
    with pytest.raises(ValueError, match="requires bound evidence documents"):
        amendment.promotion_decision(
            contract,
            "A1_typed_abstaining_critic_3b",
            nonmap,
            routing_cases=roster,
        )

    missing_provider = _a1_promotion_evidence()
    provider_digest = missing_provider["provider_qualification_journal_event_sha256"]
    del missing_provider["bound_evidence_documents"][provider_digest]
    with pytest.raises(ValueError, match="requires a bound evidence document"):
        amendment.promotion_decision(
            contract,
            "A1_typed_abstaining_critic_3b",
            missing_provider,
            routing_cases=roster,
        )

    nonobject_provider = _a1_promotion_evidence()
    provider_digest = nonobject_provider["provider_qualification_journal_event_sha256"]
    nonobject_provider["bound_evidence_documents"][provider_digest] = []
    with pytest.raises(ValueError, match="must be an object"):
        amendment.promotion_decision(
            contract,
            "A1_typed_abstaining_critic_3b",
            nonobject_provider,
            routing_cases=roster,
        )

    too_many_correct = _a1_promotion_evidence(correct_count=6, prediction_count=5)
    with pytest.raises(ValueError, match="numerator cannot exceed its denominator"):
        amendment.promotion_decision(
            contract,
            "A1_typed_abstaining_critic_3b",
            too_many_correct,
            routing_cases=roster,
        )

    extra_document = _a1_promotion_evidence()
    extra_document["bound_evidence_documents"]["f" * 64] = {"extra": True}
    with pytest.raises(ValueError, match="exactly the declared promotion bindings"):
        amendment.promotion_decision(
            contract,
            "A1_typed_abstaining_critic_3b",
            extra_document,
            routing_cases=roster,
        )


def test_promotion_default_roster_and_zero_denominator_rules_are_enforced() -> None:
    contract = amendment.load_amendment(AMENDMENT_ROOT)["contract"]
    decision = amendment.promotion_decision(
        contract,
        "A1_typed_abstaining_critic_3b",
        _a1_promotion_evidence(),
    )
    assert decision["candidate"] == "A1_typed_abstaining_critic_3b"

    roster = amendment.load_amendment(AMENDMENT_ROOT)["routing_cases"]
    nonnull_conditional = _b2_promotion_evidence(roster)
    nonnull_conditional["conditional_B2_minus_B1_robust_completion"] = "0.10"
    with pytest.raises(ValueError, match="must be null when its completion denominator is zero"):
        amendment.promotion_decision(
            contract,
            "B2_typed_route_and_prompt_repair",
            nonnull_conditional,
            routing_cases=roster,
        )


def test_repeatability_digest_requires_one_frozen_noncontrol_case() -> None:
    roster = amendment.load_amendment(AMENDMENT_ROOT)["routing_cases"]
    control_id = next(
        case["case_id"] for case in roster["cases"] if case["baseline_class"] == "clean_control"
    )
    with pytest.raises(ValueError, match="one frozen non-control"):
        amendment.repeatability_typed_input_sha256(roster, control_id)
