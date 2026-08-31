"""Pure-stdlib contract seam for preregistration amendment 01.

Loading and validation in this module never loads a model, invokes a provider,
contacts a network service, or grants visible evidence physical authority.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

SCHEMA_VERSION = "vlm_fallback.amendment.v1"
SURFACE_SCHEMA_VERSION = "vlm_fallback.surface_canonicalization.v1"
ROUTING_SCHEMA_VERSION = "vlm_fallback.routing_case_inputs.v1"
MODEL_CONTENT_SCHEMA_VERSION = "vlm_fallback.model_content_manifest.v2"
PROVIDER_QUALIFICATION_EVENT_SCHEMA_VERSION = "vlm_fallback.provider_qualification_journal_event.v1"
BLIND_ADJUDICATION_MANIFEST_SCHEMA_VERSION = (
    "vlm_fallback.blind_typed_correction_adjudication_manifest.v1"
)
BLIND_ADJUDICATION_ROSTER_SCHEMA_VERSION = (
    "vlm_fallback.blind_typed_correction_adjudication_roster.v1"
)
REPEATABILITY_INPUT_SCHEMA_VERSION = "vlm_fallback.B2_repeatability_typed_input.v1"
REPEATABILITY_EVENT_SCHEMA_VERSION = "vlm_fallback.B2_repeatability_journal_event.v1"
REPEATABILITY_JOURNAL_MANIFEST_SCHEMA_VERSION = "vlm_fallback.B2_repeatability_journal_manifest.v1"
AUTHORITATIVE_EVALUATION_SCHEMA_VERSION = "vlm_fallback.authoritative_evaluation_closure.v1"
REPEATABILITY_TYPED_INPUT_PROJECTION = (
    "case_id",
    "group_id",
    "original_prompt",
    "seed",
    "routing_instruction=Choose exactly one frozen route from trusted typed context.",
    "allowed_routes=frozen_route_vocabulary",
    "failure.typed_failure",
    "failure.trusted_state",
    "failure.asset_availability",
    "visible_report=null",
)
STUDY_ID = "vlm-fallback-prompt-optimization-2026-08-31"
AMENDMENT_ID = "01"
DEFAULT_AMENDMENT_ROOT = Path(__file__).with_name("amendments") / AMENDMENT_ID
EXPECTED_CONTRACT_SHA256 = "53e080f8ea03fcd36f88133a0445301a5c144281d5ee5bfbf08351f66ef480b2"
EXPECTED_SURFACE_SHA256 = "fdc8116af6f158253f44f4fda2ba3c6bc624b442addab2bcb97ae1c6077d5ce0"
EXPECTED_ROUTING_SHA256 = "05d7e2fc66a162bbeeebf2e2c08200f7e57d91005c18f6a9a37b08f883811cbf"
ORIGINAL_SPEC_SHA256 = "ad19d38204f20c42d8070785994fe749b8db41364e002e382e938cb92ae5bf6c"
ORIGINAL_LOG_PREFIX_SHA256 = "141bc995ba391a11cea3461180f51936f5829aca0c13ef44e10b48d3ef27f6a4"
ORIGINAL_LOG_PREFIX_LINES = 6

COMMON_VISIBLE_CHECK_MAPPING = {
    "object_presence": "object_presence",
    "penetration_or_floating": "visible_penetration_or_floating",
    "overall_prompt_match": "overall_prompt_match",
}
A0_CHECKS = (
    "object_presence",
    "support_relation",
    "penetration_or_floating",
    "articulation_state",
    "overall_prompt_match",
)
A1_CHECKS = (
    "object_presence",
    "object_identity",
    "object_count",
    "orientation",
    "table_contact",
    "visible_penetration_or_floating",
    "spatial_relation",
    "overall_prompt_match",
)
VISIBLE_TEST_CASE_IDS = (
    "sceneagent_official_open_laptop",
    "sceneagent_official_place_container_plate",
    "sceneagent_official_place_mouse_pad",
    "sceneagent_can_basket_heldout",
    "openxsim_can_left_seed0",
    "openxsim_can_left_seed17",
    "openxsim_can_left_seed99",
    "openxsim_can_left_representative",
    "openxsim_can_left_600_300",
    "openxsim_can_left_upright",
    "usg_fixed_adversarial",
    "usg_released_adversarial",
    "usg_released_corrected_control",
    "usg_fixed_vs_released_comparison",
)
PROTECTED_DIMENSIONS = ("entity", "count", "color", "relation", "articulation", "region")
ROUTE_VOCABULARY = (
    "accept_existing_compile",
    "repair_visible_prompt",
    "canonicalize_language_preserving_intent",
    "reuse_asset",
    "generate_asset_on_catalog_miss",
    "repair_typed_placement_or_asset",
    "replay_or_rematerialize_evidence",
    "abstain_infeasible_geometry",
    "abstain_unsupported_task_api",
    "abstain_missing_capture_inputs",
    "abstain_missing_multiview_and_geometry_inputs",
)
RECURSIVE_FORBIDDEN_PROJECTION_FIELDS = (
    "gold_route",
    "gold_annotation",
    "gold_label",
    "oracle_route",
    "expected_route",
    "annotation_state",
    "artifact_path",
    "repository_path",
)
COMMON_RUNNER_FIELDS = (
    "original_prompt",
    "blinded_case_id",
    "invocation_id",
    "routing_instruction",
    "prompt_template_version",
    "seed",
    "attempt",
    "allowed_routes",
    "resource_reservation",
)
ARM_FAILURE_FIELDS = {
    "B0_retry_unchanged": ("failure_code",),
    "B1_generic_prompt_repair": ("untyped_failure_summary",),
    "B2_typed_route_and_prompt_repair": (
        "typed_failure",
        "trusted_state",
        "asset_availability",
        "visible_report",
    ),
    "B3_oracle_route_ceiling": (
        "typed_failure",
        "trusted_state",
        "asset_availability",
        "visible_report",
        "gold_route",
    ),
}
MODEL_BINDINGS = (
    {
        "role": "primary_local_vlm",
        "path": "model_content_3b.json",
        "schema_version": MODEL_CONTENT_SCHEMA_VERSION,
        "model_id": "Qwen/Qwen2.5-VL-3B-Instruct",
        "revision": "66285546d2b821cf421d4f5eb2576359d3770cd3",
        "canonical_sha256": "dd904e42c13f7a47296e1aff8ce8a78090a86451d24b5ccd0d2807d29e6e4cad",
    },
    {
        "role": "confirmatory_model_size_ceiling",
        "path": "model_content_7b.json",
        "schema_version": MODEL_CONTENT_SCHEMA_VERSION,
        "model_id": "Qwen/Qwen2.5-VL-7B-Instruct",
        "revision": "cc594898137f460bfe9f0759e9844b3ce807cfb5",
        "canonical_sha256": "9686327d73f5f373917d25577fc06244c69b132927af1c586fbcbc23f3301208",
    },
)
PROVIDER_OPERATIONS = frozenset(
    {
        "visible_provider_call",
        "routing_provider_call",
        "oracle_provider_call",
        "format_repair_provider_call",
        "model_inference",
    }
)
NON_EXECUTION_OPERATIONS = frozenset(
    {"contract_validation", "canonical_digest", "binding_verification", "annotation_workflow"}
)
_DECIMAL_RE = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")
_HEX_RE = re.compile(r"^[0-9a-f]+$")


def _require_fields(value: Any, expected: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    observed = set(value)
    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        raise ValueError(f"{label} fields mismatch: missing={missing}, extra={extra}")
    return value


def _require_exact(value: Any, expected: Any, label: str) -> None:
    if value != expected or type(value) is not type(expected):
        raise ValueError(f"{label} must remain exactly frozen")


def _require_lower_hex(value: Any, length: int, label: str) -> str:
    if not isinstance(value, str) or len(value) != length or not _HEX_RE.fullmatch(value):
        raise ValueError(f"{label} must be {length} lowercase hex characters")
    return value


def _require_nonnegative_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a non-negative JSON integer; booleans are forbidden")
    return value


def _require_decimal(value: Any, label: str) -> Decimal:
    if not isinstance(value, str) or not _DECIMAL_RE.fullmatch(value):
        raise ValueError(f"{label} must be a plain base-10 Decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{label} must be a finite Decimal string") from error
    if not parsed.is_finite():
        raise ValueError(f"{label} must be a finite Decimal string")
    return parsed


def canonical_json_bytes(value: Any) -> bytes:
    """Return the amendment's canonical JSON representation."""

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Return SHA-256 over canonical JSON bytes."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _load_json_object(path: Path) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"{path.name} contains duplicate object key: {key}")
            result[key] = item
        return result

    def reject_non_finite(value: str) -> None:
        raise ValueError(f"{path.name} contains non-finite JSON number: {value}")

    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_non_finite,
    )
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _validate_surface_table(surface_table: Mapping[str, Any]) -> None:
    _require_fields(
        surface_table,
        {
            "schema_version",
            "languages",
            "normalization",
            "protected_dimensions",
            "vocabulary",
            "preservation",
        },
        "surface_canonicalization",
    )
    _require_exact(surface_table["schema_version"], SURFACE_SCHEMA_VERSION, "surface schema")
    _require_exact(surface_table["languages"], ["en", "zh-Hans"], "surface languages")
    _require_fields(
        surface_table["normalization"],
        {"unicode_form", "case_handling", "whitespace", "matching"},
        "surface normalization",
    )
    _require_exact(
        surface_table["protected_dimensions"],
        list(PROTECTED_DIMENSIONS),
        "protected dimensions",
    )
    vocabulary = _require_fields(
        surface_table["vocabulary"], set(PROTECTED_DIMENSIONS), "surface vocabulary"
    )
    for dimension in PROTECTED_DIMENSIONS:
        entries = vocabulary[dimension]
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"surface vocabulary {dimension} must be a non-empty list")
        canonical_values: list[str] = []
        seen_surfaces = {"en": set(), "zh-Hans": set()}
        for index, entry in enumerate(entries):
            item = _require_fields(
                entry,
                {"canonical", "en", "zh-Hans"},
                f"surface vocabulary {dimension}[{index}]",
            )
            canonical = item["canonical"]
            if not isinstance(canonical, str) or not canonical:
                raise ValueError(f"surface vocabulary {dimension} canonical must be non-empty")
            canonical_values.append(canonical)
            for language in ("en", "zh-Hans"):
                surfaces = item[language]
                if (
                    not isinstance(surfaces, list)
                    or not surfaces
                    or any(not isinstance(value, str) or not value for value in surfaces)
                    or len(set(surfaces)) != len(surfaces)
                ):
                    raise ValueError(
                        f"surface vocabulary {dimension}.{canonical}.{language} is invalid"
                    )
                overlap = seen_surfaces[language].intersection(surfaces)
                if overlap:
                    raise ValueError(
                        f"surface vocabulary {dimension}.{language} has ambiguous surfaces: "
                        f"{sorted(overlap)}"
                    )
                seen_surfaces[language].update(surfaces)
        if canonical_values != sorted(canonical_values) or len(set(canonical_values)) != len(
            canonical_values
        ):
            raise ValueError(
                f"surface vocabulary {dimension} canonical entries must be unique/sorted"
            )
    preservation = _require_fields(
        surface_table["preservation"],
        {
            "comparison_input",
            "equality",
            "deletion_allowed",
            "insertion_allowed",
            "unknown_surface_policy",
        },
        "surface preservation",
    )
    if preservation["deletion_allowed"] is not False:
        raise ValueError("surface preservation cannot allow protected-field deletion")
    if preservation["insertion_allowed"] is not False:
        raise ValueError("surface preservation cannot allow protected-field insertion")
    _require_exact(
        preservation["unknown_surface_policy"],
        "abstain_unsupported_language_surface",
        "unknown surface policy",
    )
    if canonical_sha256(surface_table) != EXPECTED_SURFACE_SHA256:
        raise ValueError("surface canonicalization mapping digest mismatch")


def _validate_experiment_a(experiment: Any) -> None:
    value = _require_fields(
        experiment,
        {
            "arms",
            "a2_inheritance",
            "cross_arm_comparison",
            "status_mapping",
            "schema_validity",
            "typed_correction_precision",
            "bootstrap",
        },
        "experiment_a",
    )
    arms = _require_fields(
        value["arms"],
        {
            "A0_current_critic_3b",
            "A1_typed_abstaining_critic_3b",
            "A2_typed_abstaining_critic_7b",
        },
        "experiment_a.arms",
    )
    a0 = _require_fields(
        arms["A0_current_critic_3b"],
        {"check_domain", "within_arm_only_checks"},
        "A0 arm",
    )
    a1 = _require_fields(
        arms["A1_typed_abstaining_critic_3b"],
        {
            "check_domain",
            "within_arm_only_checks",
            "prompt_contract",
            "input_projection_contract",
            "processor_identity_contract",
            "model_role",
            "resource_profile",
        },
        "A1 arm",
    )
    a2 = _require_fields(
        arms["A2_typed_abstaining_critic_7b"],
        {
            "check_domain",
            "within_arm_only_checks",
            "prompt_contract",
            "input_projection_contract",
            "processor_identity_contract",
            "model_role",
            "resource_profile",
        },
        "A2 arm",
    )
    _require_exact(a0["check_domain"], list(A0_CHECKS), "A0 five-check domain")
    _require_exact(a1["check_domain"], list(A1_CHECKS), "A1 eight-check domain")
    _require_exact(a2["check_domain"], list(A1_CHECKS), "A2 inherited eight-check domain")
    _require_exact(
        a0["within_arm_only_checks"],
        ["support_relation", "articulation_state"],
        "A0 within-arm-only checks",
    )
    _require_exact(
        a1["within_arm_only_checks"],
        ["object_identity", "object_count", "orientation", "table_contact", "spatial_relation"],
        "A1 within-arm-only checks",
    )
    _require_exact(
        a2["within_arm_only_checks"],
        ["object_identity", "object_count", "orientation", "table_contact", "spatial_relation"],
        "A2 inherited within-arm-only checks",
    )
    shared_a1_a2 = {
        "prompt_contract": "selected_A1_prompt_bytes_bound_by_authoritative_evaluation_closure",
        "input_projection_contract": (
            "sealed_case_input_projection_bound_by_authoritative_evaluation_closure"
        ),
        "processor_identity_contract": (
            "processor_identity_bound_by_authoritative_evaluation_closure"
        ),
    }
    for field, expected in shared_a1_a2.items():
        _require_exact(a1[field], expected, f"A1 {field}")
        _require_exact(a2[field], a1[field], f"A2 inherited {field}")
    _require_exact(a1["model_role"], "primary_local_vlm", "A1 model role")
    _require_exact(a2["model_role"], "confirmatory_model_size_ceiling", "A2 model role")
    _require_exact(a1["resource_profile"], "primary_3b", "A1 resource profile")
    _require_exact(a2["resource_profile"], "confirmatory_7b", "A2 resource profile")
    inheritance = _require_fields(
        value["a2_inheritance"],
        {"base_arm", "identical_fields", "allowed_differences", "all_other_differences_forbidden"},
        "A2 inheritance",
    )
    _require_exact(
        dict(inheritance),
        {
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
        },
        "A2 inheritance semantics",
    )
    comparison = _require_fields(
        value["cross_arm_comparison"],
        {
            "scope",
            "mapping",
            "mapping_cardinality",
            "paired_contrasts",
            "unmapped_policy",
            "missing_mapped_value_policy",
        },
        "A cross-arm comparison",
    )
    expected_mapping = [
        {"A0": left, "A1": right, "A2": right}
        for left, right in COMMON_VISIBLE_CHECK_MAPPING.items()
    ]
    _require_exact(comparison["mapping"], expected_mapping, "A common-three mapping")
    _require_exact(comparison["mapping_cardinality"], 3, "A mapping cardinality")
    _require_exact(
        comparison["paired_contrasts"],
        ["A1_minus_A0", "A2_minus_A0", "A2_minus_A1"],
        "A common-three paired contrasts",
    )
    _require_exact(comparison["scope"], "mapped_intersection_only", "A comparison scope")
    _require_exact(
        comparison["unmapped_policy"],
        "arm_internal_reporting_only_never_impute",
        "A unmapped policy",
    )
    _require_exact(
        comparison["missing_mapped_value_policy"],
        "mark_pair_not_comparable_never_impute",
        "A missing-value policy",
    )
    mappings = _require_fields(
        value["status_mapping"],
        {
            "A0_current_critic_3b",
            "A1_typed_abstaining_critic_3b",
            "A2_typed_abstaining_critic_7b",
        },
        "A status mapping",
    )
    expected_a0_status = [
        {"source": "pass", "target": "pass"},
        {"source": "fail", "target": "fail"},
        {"source": "warning", "target": "abstain"},
        {"source": "not_applicable", "target": "not_applicable"},
    ]
    _require_exact(mappings["A0_current_critic_3b"], expected_a0_status, "A0 warning mapping")
    expected_a1_status = [
        {"source": "pass", "target": "pass"},
        {"source": "fail", "target": "fail"},
        {"source": "abstain", "target": "abstain"},
        {"source": "not_applicable", "target": "not_applicable"},
    ]
    _require_exact(mappings["A1_typed_abstaining_critic_3b"], expected_a1_status, "A1 mapping")
    _require_exact(
        mappings["A2_typed_abstaining_critic_7b"],
        expected_a1_status,
        "A2 inherited status mapping",
    )
    schema = _require_fields(
        value["schema_validity"],
        {
            "denominator",
            "sealed_test_case_count_per_arm",
            "arm_count",
            "required_terminal_row_count",
            "missing_or_failed_response_policy",
            "provider_call_accounting",
            "base_valid_rate",
            "final_valid_rate",
            "repair_may_change_visible_facts",
            "promotion_rate",
        },
        "A schema validity",
    )
    _require_exact(
        schema["denominator"],
        "sealed_test_terminal_response_roster_per_arm",
        "schema denominator",
    )
    _require_exact(schema["sealed_test_case_count_per_arm"], 14, "schema sealed-test count")
    _require_exact(schema["arm_count"], 3, "schema arm count")
    _require_exact(schema["required_terminal_row_count"], 42, "schema terminal row count")
    _require_exact(
        schema["missing_or_failed_response_policy"],
        "retain_terminal_row_and_count_schema_invalid",
        "schema missing or failed response policy",
    )
    _require_exact(
        schema["provider_call_accounting"],
        "separate_from_terminal_response_denominator",
        "schema provider-call accounting",
    )
    _require_fields(
        schema["base_valid_rate"],
        {"numerator", "report_required", "promotion_gate"},
        "base-valid rate",
    )
    final_rate = _require_fields(
        schema["final_valid_rate"],
        {
            "numerator",
            "max_format_only_repairs_per_base_invocation",
            "report_required",
            "promotion_gate",
        },
        "final-valid rate",
    )
    _require_exact(schema["base_valid_rate"]["report_required"], True, "base-valid disclosure")
    _require_exact(schema["base_valid_rate"]["promotion_gate"], False, "base-valid gate")
    _require_exact(final_rate["report_required"], True, "final-valid disclosure")
    _require_exact(final_rate["promotion_gate"], True, "final-valid gate")
    _require_exact(final_rate["max_format_only_repairs_per_base_invocation"], 1, "repair count")
    _require_exact(schema["promotion_rate"], "final_valid_rate", "schema promotion rate")
    _require_exact(schema["repair_may_change_visible_facts"], False, "format-repair fact policy")
    precision = _require_fields(
        value["typed_correction_precision"],
        {"numerator", "denominator", "arithmetic", "zero_denominator"},
        "typed-correction precision",
    )
    zero = _require_fields(
        precision["zero_denominator"],
        {"metric_value", "promotion_gate"},
        "typed-correction zero denominator",
    )
    _require_exact(
        precision["numerator"],
        "blindly_adjudicated_correct_typed_correction_count",
        "typed-correction numerator",
    )
    _require_exact(
        precision["denominator"],
        "blindly_adjudicated_typed_correction_prediction_count",
        "typed-correction denominator",
    )
    _require_exact(
        precision["arithmetic"],
        "exact rational numerator/denominator; compare to Decimal threshold by integer "
        "cross-multiplication without rounded quotient authority",
        "typed-correction arithmetic",
    )
    if zero["metric_value"] is not None or zero["promotion_gate"] != "fail":
        raise ValueError("typed-correction zero denominator must be null and fail promotion")
    bootstrap = _require_fields(
        value["bootstrap"],
        {
            "method",
            "resampling_unit",
            "resamples",
            "seed",
            "confidence",
            "per_resample_computation",
            "forbidden_shortcut",
        },
        "A bootstrap",
    )
    _require_exact(bootstrap["resampling_unit"], "group_id", "A bootstrap unit")
    _require_exact(bootstrap["resamples"], 10000, "A bootstrap resamples")
    _require_exact(bootstrap["seed"], 20260831, "A bootstrap seed")
    _require_decimal(bootstrap["confidence"], "A bootstrap confidence")
    computation = bootstrap["per_resample_computation"]
    if not all(
        phrase in computation
        for phrase in (
            "A1-A0, A2-A0, and A2-A1",
            "reconstruct both arms' raw mapped common-three-check rows",
            "recompute both arm group-weighted failure macro-F1 values",
        )
    ):
        raise ValueError(
            "A bootstrap must recompute both arm macro-F1 values from raw common-three rows for "
            "A1-A0, A2-A0, and A2-A1"
        )
    if "precomputed scalar" not in bootstrap["forbidden_shortcut"]:
        raise ValueError("A bootstrap must forbid generic scalar mean-delta shortcuts")


def _validate_experiment_b(experiment: Any) -> None:
    value = _require_fields(
        experiment,
        {
            "route_vocabulary",
            "common_runner_owned_fields",
            "forbidden_common_fields",
            "recursive_forbidden_projection_fields",
            "prompt_contracts",
            "routing_output_schema",
            "arm_projections",
            "visible_report_unlock",
            "routing_roster",
            "repeatability_audit",
        },
        "experiment_b",
    )
    _require_exact(value["route_vocabulary"], list(ROUTE_VOCABULARY), "B route vocabulary")
    _require_exact(
        value["common_runner_owned_fields"],
        list(COMMON_RUNNER_FIELDS),
        "B common runner-owned fields",
    )
    expected_forbidden = [
        "gold_route",
        "gold_annotation",
        "annotation_state",
        "artifact_path",
        "repository_path",
    ]
    _require_exact(
        value["forbidden_common_fields"], expected_forbidden, "B forbidden common fields"
    )
    _require_exact(
        value["recursive_forbidden_projection_fields"],
        list(RECURSIVE_FORBIDDEN_PROJECTION_FIELDS),
        "B recursive leakage fields",
    )
    prompt_contracts = _require_fields(
        value["prompt_contracts"], set(ARM_FAILURE_FIELDS), "B prompt contracts"
    )
    expected_prompt_identity = {
        "B0_retry_unchanged": (
            None,
            None,
            "vlm_fallback.runner_owned_no_provider_control.v1",
            (),
        ),
        "B1_generic_prompt_repair": (
            "B1_generic_preserve_intent_v1",
            "Preserve the original task intent. Using only the supplied untyped failure summary, "
            "choose exactly one route from allowed_routes and return one canonical JSON object "
            "matching the frozen output schema. Do not infer or expose gold labels.",
            "vlm_fallback.B1_generic_routing_input.v1",
            (
                "invocation_id",
                "case_id",
                "arm",
                "original_prompt",
                "routing_instruction",
                "prompt_template_version",
                "untyped_failure_summary",
                "seed",
                "attempt",
                "allowed_routes",
                "resource_reservation",
            ),
        ),
        "B2_typed_route_and_prompt_repair": (
            "B2_typed_trusted_context_v1",
            "Choose exactly one frozen route from trusted typed context. Preserve the original "
            "task intent and return one canonical JSON object matching the frozen output schema. "
            "Do not infer or expose gold labels.",
            "vlm_fallback.B2_typed_routing_input.v1",
            (
                "invocation_id",
                "case_id",
                "arm",
                "original_prompt",
                "routing_instruction",
                "prompt_template_version",
                "typed_failure",
                "trusted_state",
                "asset_availability",
                "visible_report",
                "seed",
                "attempt",
                "allowed_routes",
                "resource_reservation",
            ),
        ),
        "B3_oracle_route_ceiling": (
            "B3_disjoint_oracle_ceiling_v1",
            "Oracle ceiling only. Using the supplied gold_route and typed context, return that "
            "route and at most one intent-preserving revised prompt as one canonical JSON object "
            "matching the frozen output schema.",
            "vlm_fallback.B3_oracle_routing_input.v1",
            (
                "invocation_id",
                "case_id",
                "arm",
                "original_prompt",
                "routing_instruction",
                "prompt_template_version",
                "typed_failure",
                "trusted_state",
                "asset_availability",
                "visible_report",
                "gold_route",
                "seed",
                "attempt",
                "allowed_routes",
                "resource_reservation",
            ),
        ),
    }
    decoding = {
        "do_sample": False,
        "temperature": "0",
        "top_p": "1",
        "max_output_tokens": 512,
    }
    for arm, (
        template,
        instruction,
        input_schema,
        ordered_fields,
    ) in expected_prompt_identity.items():
        prompt = _require_fields(
            prompt_contracts[arm],
            {
                "prompt_template_version",
                "routing_instruction_utf8",
                "input_serialization_schema",
                "ordered_routes",
                "output_schema_ref",
                "decoding_parameters",
                "provider_allowed",
            },
            f"{arm} prompt contract",
        )
        _require_exact(prompt["prompt_template_version"], template, f"{arm} prompt template")
        _require_exact(prompt["routing_instruction_utf8"], instruction, f"{arm} instruction")
        input_contract = _require_fields(
            prompt["input_serialization_schema"],
            {"schema_version", "ordered_fields", "field_contracts", "additional_properties"},
            f"{arm} input serialization schema",
        )
        _require_exact(input_contract["schema_version"], input_schema, f"{arm} input schema")
        _require_exact(
            input_contract["ordered_fields"], list(ordered_fields), f"{arm} input fields"
        )
        _require_fields(
            input_contract["field_contracts"], set(ordered_fields), f"{arm} input types"
        )
        _require_exact(input_contract["additional_properties"], False, f"{arm} extra input fields")
        is_control = arm == "B0_retry_unchanged"
        _require_exact(
            prompt["ordered_routes"], [] if is_control else list(ROUTE_VOCABULARY), f"{arm} routes"
        )
        _require_exact(
            prompt["output_schema_ref"],
            None if is_control else "experiment_b.routing_output_schema",
            f"{arm} output schema",
        )
        _require_exact(
            prompt["decoding_parameters"], None if is_control else decoding, f"{arm} decoding"
        )
        _require_exact(prompt["provider_allowed"], False, f"{arm} current provider authority")
    output_schema = _require_fields(
        value["routing_output_schema"],
        {
            "schema_version",
            "top_level_type",
            "ordered_fields",
            "field_contracts",
            "additional_properties",
        },
        "B routing output schema",
    )
    _require_exact(
        output_schema["schema_version"],
        "vlm_fallback.sandboxed_routing_output.v1",
        "B routing output schema version",
    )
    _require_exact(output_schema["top_level_type"], "object", "B routing output type")
    output_fields = ["route", "revised_prompt", "intent_before", "intent_after"]
    _require_exact(output_schema["ordered_fields"], output_fields, "B routing output fields")
    _require_fields(output_schema["field_contracts"], set(output_fields), "B routing output types")
    _require_exact(output_schema["additional_properties"], False, "B routing output extras")
    arms = _require_fields(value["arm_projections"], set(ARM_FAILURE_FIELDS), "B arm projections")
    expected_modes = {
        "B0_retry_unchanged": "deterministic_unchanged_retry",
        "B1_generic_prompt_repair": "provider_generic_preserve_intent_repair",
        "B2_typed_route_and_prompt_repair": "provider_typed_route",
        "B3_oracle_route_ceiling": "disjoint_oracle_provider",
    }
    for arm, fields in ARM_FAILURE_FIELDS.items():
        projection = _require_fields(
            arms[arm],
            {
                "failure_context_fields",
                "visible_report_effective_value",
                "execution_mode",
                "gold_route_access",
                "oracle_only",
                "deployable",
                "promotion_eligible",
            },
            f"{arm} projection",
        )
        _require_exact(projection["failure_context_fields"], list(fields), f"{arm} visible fields")
        if projection["visible_report_effective_value"] is not None:
            raise ValueError(f"{arm} visible report must remain null in amendment 01")
        _require_exact(projection["execution_mode"], expected_modes[arm], f"{arm} mode")
        gold_expected = "oracle_only" if arm == "B3_oracle_route_ceiling" else "forbidden"
        _require_exact(projection["gold_route_access"], gold_expected, f"{arm} gold access")
        is_oracle = arm == "B3_oracle_route_ceiling"
        _require_exact(projection["oracle_only"], is_oracle, f"{arm} oracle status")
        _require_exact(projection["deployable"], not is_oracle, f"{arm} deployability")
        _require_exact(
            projection["promotion_eligible"],
            arm == "B2_typed_route_and_prompt_repair",
            f"{arm} promotion eligibility",
        )
    gold_arms = [arm for arm, fields in ARM_FAILURE_FIELDS.items() if "gold_route" in fields]
    if gold_arms != ["B3_oracle_route_ceiling"]:
        raise ValueError("gold_route may be exposed only to the disjoint B3 oracle")
    unlock = _require_fields(
        value["visible_report_unlock"],
        {
            "non_null_allowed_by_this_amendment",
            "requires_future_amendment",
            "requires_journal_bound_A1_evidence",
            "B0_and_B1_exposure",
            "B2_and_B3_current_value",
        },
        "B visible report unlock",
    )
    _require_exact(unlock["non_null_allowed_by_this_amendment"], False, "visible report authority")
    _require_exact(unlock["requires_future_amendment"], True, "visible report future amendment")
    _require_exact(
        unlock["requires_journal_bound_A1_evidence"],
        True,
        "visible report journal binding",
    )
    if unlock["B2_and_B3_current_value"] is not None:
        raise ValueError("B2/B3 visible report must be null before a later amendment")
    roster = _require_fields(
        value["routing_roster"],
        {
            "source",
            "caller_supplied_cases_allowed",
            "clean_control_count",
            "recoverable_baseline_failure_count",
            "unrecoverable_failure_count",
            "clean_control_B0_B1_B2_action",
            "completion_denominator_class",
            "zero_completion_denominator",
        },
        "B routing roster",
    )
    _require_exact(roster["source"], "routing_case_inputs.json", "B roster source")
    _require_exact(roster["caller_supplied_cases_allowed"], False, "caller case authority")
    _require_exact(roster["clean_control_count"], 30, "clean-control count")
    _require_exact(roster["recoverable_baseline_failure_count"], 0, "recoverable failure count")
    _require_exact(roster["unrecoverable_failure_count"], 6, "unrecoverable failure count")
    if "without_provider_or_retry" not in roster["clean_control_B0_B1_B2_action"]:
        raise ValueError("clean controls must be runner no-op accepts for B0/B1/B2")
    _require_exact(
        roster["completion_denominator_class"],
        "recoverable_baseline_failure",
        "B completion denominator class",
    )
    zero = _require_fields(
        roster["zero_completion_denominator"],
        {"metric_value", "reason", "completion_superiority_claim_allowed", "promotion_gate"},
        "B zero completion denominator",
    )
    if (
        zero["metric_value"] is not None
        or zero["reason"] != "insufficient_recoverable_failure_groups"
        or zero["completion_superiority_claim_allowed"] is not False
        or zero["promotion_gate"] != "fail"
    ):
        raise ValueError(
            "zero recoverable-failure denominator must null the metric and fail promotion"
        )
    audit = _require_fields(
        value["repeatability_audit"],
        {
            "audit_id",
            "source_roster",
            "case_count",
            "audit_group_count",
            "repeat_indices",
            "same_typed_input_required",
            "typed_input_digest_projection",
            "required_equal_outputs",
            "journal_event_schema",
            "journal_manifest_schema",
            "route_evaluation_budget",
            "compile_attempt_budget",
            "physical_replay_budget",
            "counts_against_regular_recovery_attempt_budget",
        },
        "B repeatability audit",
    )
    _require_exact(audit["audit_id"], "B2_repeatability_audit", "repeatability audit id")
    _require_exact(audit["case_count"], 6, "repeatability case count")
    _require_exact(audit["audit_group_count"], 4, "repeatability group count")
    _require_exact(audit["repeat_indices"], [1, 2, 3], "repeatability indices")
    _require_exact(audit["same_typed_input_required"], True, "repeatability input identity")
    _require_exact(
        audit["typed_input_digest_projection"],
        list(REPEATABILITY_TYPED_INPUT_PROJECTION),
        "repeatability typed-input projection",
    )
    _require_exact(
        audit["journal_event_schema"],
        REPEATABILITY_EVENT_SCHEMA_VERSION,
        "repeatability journal event schema",
    )
    _require_exact(
        audit["journal_manifest_schema"],
        REPEATABILITY_JOURNAL_MANIFEST_SCHEMA_VERSION,
        "repeatability journal manifest schema",
    )
    _require_exact(audit["route_evaluation_budget"], 18, "repeatability route budget")
    _require_exact(audit["compile_attempt_budget"], 0, "repeatability compile budget")
    _require_exact(audit["physical_replay_budget"], 0, "repeatability replay budget")
    _require_exact(
        audit["counts_against_regular_recovery_attempt_budget"],
        False,
        "repeatability regular-budget isolation",
    )


def _validate_authoritative_evaluation_contract(
    evaluation: Any, promotion: Mapping[str, Any]
) -> None:
    value = _require_fields(
        evaluation,
        {
            "current_state",
            "current_result_artifacts",
            "current_promotion_authority",
            "future_closure",
        },
        "authoritative evaluation contract",
    )
    _require_exact(value["current_state"], "not_bound_by_amendment_01", "evaluation current state")
    _require_exact(value["current_result_artifacts"], [], "evaluation current result artifacts")
    _require_exact(value["current_promotion_authority"], False, "evaluation current authority")
    future = _require_fields(
        value["future_closure"],
        {
            "schema_version",
            "binding_direction",
            "reverse_bindings",
            "visible_evaluation",
            "routing_evaluation",
            "metric_authority",
            "gate_derivations",
        },
        "authoritative evaluation future closure",
    )
    _require_exact(
        future["schema_version"],
        AUTHORITATIVE_EVALUATION_SCHEMA_VERSION,
        "authoritative evaluation schema",
    )
    direction = _require_fields(
        future["binding_direction"],
        {
            "result_closure_references_prior_inputs",
            "prior_amendment_root_references_result_closure",
            "journal_event_references_completed_result_closure",
            "cycles_allowed",
        },
        "evaluation binding direction",
    )
    _require_exact(direction["result_closure_references_prior_inputs"], True, "result direction")
    _require_exact(
        direction["prior_amendment_root_references_result_closure"],
        False,
        "amendment-root cycle direction",
    )
    _require_exact(
        direction["journal_event_references_completed_result_closure"],
        True,
        "journal result binding direction",
    )
    _require_exact(direction["cycles_allowed"], False, "evaluation digest cycles")
    reverse = _require_fields(
        future["reverse_bindings"],
        {"required_sha256_fields", "model_content_manifest_sha256_by_role"},
        "evaluation reverse bindings",
    )
    _require_exact(
        reverse["required_sha256_fields"],
        [
            "amendment_01_root_manifest_sha256",
            "effective_experiment_spec_v2_sha256",
            "amendment_01_contract_canonical_sha256",
            "runner_source_manifest_v2_sha256",
            "sealed_annotation_manifest_v3_sha256",
            "provider_qualification_journal_manifest_sha256",
            "selected_A1_prompt_selection_manifest_sha256",
        ],
        "evaluation reverse-binding fields",
    )
    _require_exact(
        reverse["model_content_manifest_sha256_by_role"],
        ["primary_local_vlm", "confirmatory_model_size_ceiling"],
        "evaluation model-content bindings",
    )
    visible = _require_fields(
        future["visible_evaluation"],
        {
            "sealed_split",
            "case_roster_source",
            "case_count",
            "case_ids",
            "case_binding_fields",
            "arms",
            "row_primary_key",
            "expected_terminal_row_count",
            "terminal_response_states",
            "invalid_terminal_states",
            "missing_primary_key_policy",
            "per_arm_schema_validity_denominator",
            "provider_invocation_subset_denominator_allowed",
            "provider_call_accounting",
            "row_requires_raw_journal_event_ref",
            "exact_row_fields",
            "exact_row_schema",
            "A1_A2_identity_requirements",
        },
        "visible evaluation closure",
    )
    _require_exact(visible["sealed_split"], "test", "visible sealed split")
    _require_exact(
        visible["case_roster_source"],
        "full_effective_experiment_spec_v2.A_visible_semantic_correction.samples",
        "visible case roster source",
    )
    _require_exact(visible["case_count"], 14, "visible case count")
    _require_exact(visible["case_ids"], list(VISIBLE_TEST_CASE_IDS), "visible case roster")
    _require_exact(
        visible["case_binding_fields"],
        ["case_id", "group_id", "bundle_sha256", "artifacts"],
        "visible case bindings",
    )
    _require_exact(
        visible["arms"],
        [
            "A0_current_critic_3b",
            "A1_typed_abstaining_critic_3b",
            "A2_typed_abstaining_critic_7b",
        ],
        "visible evaluation arms",
    )
    _require_exact(visible["row_primary_key"], ["arm", "case_id"], "visible row key")
    _require_exact(visible["expected_terminal_row_count"], 42, "visible terminal row count")
    invalid_states = [
        "missing_response",
        "schema_invalid",
        "provider_exception",
        "provider_timeout",
        "no_response",
    ]
    _require_exact(
        visible["terminal_response_states"],
        ["success", *invalid_states],
        "visible terminal states",
    )
    _require_exact(visible["invalid_terminal_states"], invalid_states, "visible invalid states")
    _require_exact(
        visible["missing_primary_key_policy"],
        "reject_authoritative_closure",
        "visible missing terminal row policy",
    )
    _require_exact(visible["per_arm_schema_validity_denominator"], 14, "visible denominator")
    _require_exact(
        visible["provider_invocation_subset_denominator_allowed"],
        False,
        "visible successful-call subset policy",
    )
    _require_exact(
        visible["provider_call_accounting"],
        "separate_bound_event_roster_including_base_calls_and_format_only_repairs",
        "visible provider-call accounting",
    )
    _require_exact(visible["row_requires_raw_journal_event_ref"], True, "visible event refs")
    visible_row_fields = [
        "arm",
        "case_id",
        "group_id",
        "bundle_sha256",
        "artifacts",
        "terminal_response_state",
        "base_response_schema_valid",
        "final_response_schema_valid",
        "format_only_repair_count",
        "raw_response_sha256",
        "parsed_response_sha256",
        "latency_ms",
        "common_three_check_rows",
        "within_arm_check_rows",
        "typed_correction_predictions",
        "physical_authority_violation_count",
        "provider_invocation_event_sha256s",
        "terminal_journal_event_sha256",
    ]
    _require_exact(visible["exact_row_fields"], visible_row_fields, "visible exact row fields")
    visible_schema = _require_fields(
        visible["exact_row_schema"],
        {"additional_properties", "field_contracts", "nested_schemas"},
        "visible exact row schema",
    )
    _require_exact(visible_schema["additional_properties"], False, "visible row extra fields")
    _require_fields(visible_schema["field_contracts"], set(visible_row_fields), "visible row types")
    nested = _require_fields(
        visible_schema["nested_schemas"],
        {"common_three_check_row", "within_arm_check_row", "typed_correction_prediction"},
        "visible nested row schemas",
    )
    for name, fields in {
        "common_three_check_row": [
            "check_name",
            "gold_status",
            "predicted_status",
            "scorable",
            "abstained",
        ],
        "within_arm_check_row": [
            "check_name",
            "gold_status",
            "predicted_status",
            "scorable",
            "abstained",
        ],
        "typed_correction_prediction": [
            "prediction_id",
            "check_name",
            "proposed_status",
            "prediction_event_sha256",
            "blind_adjudication_row_sha256",
        ],
    }.items():
        schema = _require_fields(
            nested[name],
            {"ordered_fields", "field_contracts", "additional_properties"},
            f"visible {name} schema",
        )
        _require_exact(schema["ordered_fields"], fields, f"visible {name} fields")
        _require_fields(schema["field_contracts"], set(fields), f"visible {name} types")
        _require_exact(schema["additional_properties"], False, f"visible {name} extras")
    identity = _require_fields(
        visible["A1_A2_identity_requirements"],
        {
            "selected_prompt_sha256_equal",
            "case_input_projection_sha256_equal",
            "processor_identity_sha256_equal",
            "sealed_case_roster_sha256_equal",
            "scorer_source_identity_sha256_equal",
            "selection_manifest_schema",
            "selection_manifest_must_be_journal_bound_before_test_unseal",
        },
        "A1/A2 evaluation identity",
    )
    for field in (
        "selected_prompt_sha256_equal",
        "case_input_projection_sha256_equal",
        "processor_identity_sha256_equal",
        "sealed_case_roster_sha256_equal",
        "scorer_source_identity_sha256_equal",
        "selection_manifest_must_be_journal_bound_before_test_unseal",
    ):
        _require_exact(identity[field], True, f"A1/A2 {field}")
    _require_exact(
        identity["selection_manifest_schema"],
        "vlm_fallback.A1_prompt_selection_manifest.v1",
        "A1 prompt selection schema",
    )
    routing = _require_fields(
        future["routing_evaluation"],
        {
            "case_roster_source",
            "case_count",
            "class_counts",
            "arms",
            "row_primary_key",
            "expected_terminal_row_count",
            "clean_control_no_provider_arms",
            "clean_control_action",
            "B3_clean_control_policy",
            "future_manifest_required_sha256_bindings",
            "future_manifest_binding_derivations",
            "row_requires_raw_journal_event_ref",
            "exact_row_fields",
            "exact_row_schema",
        },
        "routing evaluation closure",
    )
    _require_exact(routing["case_roster_source"], "routing_case_inputs.json", "routing source")
    _require_exact(routing["case_count"], 36, "routing evaluation case count")
    _require_exact(
        routing["class_counts"],
        {"clean_control": 30, "recoverable_baseline_failure": 0, "unrecoverable_failure": 6},
        "routing evaluation classes",
    )
    routing_arms = list(ARM_FAILURE_FIELDS)
    _require_exact(routing["arms"], routing_arms, "routing evaluation arms")
    _require_exact(routing["row_primary_key"], ["arm", "case_id"], "routing row key")
    _require_exact(routing["expected_terminal_row_count"], 144, "routing terminal row count")
    _require_exact(
        routing["clean_control_no_provider_arms"], routing_arms[:3], "routing control arms"
    )
    _require_exact(
        routing["clean_control_action"],
        "runner_noop_accept_existing_compile_without_provider_or_retry",
        "routing control action",
    )
    _require_exact(
        routing["B3_clean_control_policy"],
        "disjoint_oracle_ceiling_result_never_promotion_eligible",
        "routing B3 control policy",
    )
    manifest_fields = [
        "prompt_template_sha256_by_provider_arm",
        "ordered_route_vocabulary_sha256",
        "output_schema_sha256_by_provider_arm",
        "decoding_parameters_sha256_by_provider_arm",
    ]
    _require_exact(
        routing["future_manifest_required_sha256_bindings"],
        manifest_fields,
        "routing prompt manifest bindings",
    )
    _require_fields(
        routing["future_manifest_binding_derivations"],
        set(manifest_fields),
        "routing prompt manifest derivations",
    )
    _require_exact(routing["row_requires_raw_journal_event_ref"], True, "routing event refs")
    routing_row_fields = [
        "arm",
        "case_id",
        "group_id",
        "baseline_class",
        "bundle_sha256",
        "artifacts",
        "terminal_response_state",
        "predicted_route",
        "gold_route_ref_sha256",
        "revised_prompt_sha256",
        "compile_outcome_ref_sha256",
        "physical_replay_outcome_ref_sha256",
        "intent_preserved",
        "unsafe_publication",
        "provider_invocation_count",
        "provider_invocation_event_sha256s",
        "terminal_journal_event_sha256",
    ]
    _require_exact(routing["exact_row_fields"], routing_row_fields, "routing exact row fields")
    routing_schema = _require_fields(
        routing["exact_row_schema"],
        {
            "additional_properties",
            "field_contracts",
            "nested_schemas",
            "additional_properties_for_nested_values",
        },
        "routing exact row schema",
    )
    _require_exact(routing_schema["additional_properties"], False, "routing row extra fields")
    _require_fields(routing_schema["field_contracts"], set(routing_row_fields), "routing row types")
    _require_exact(routing_schema["nested_schemas"], {}, "routing inline nested schemas")
    metric_authority = _require_fields(
        future["metric_authority"],
        {
            "caller_supplied_core_decimal_or_integer_authoritative",
            "all_core_metrics_recomputed_from_bound_rows",
            "required_source_identity_sha256_fields",
            "required_bootstrap_receipt_sha256_fields",
            "row_and_event_references_required",
        },
        "evaluation metric authority",
    )
    _require_exact(
        metric_authority["caller_supplied_core_decimal_or_integer_authoritative"],
        False,
        "caller metric authority",
    )
    _require_exact(
        metric_authority["all_core_metrics_recomputed_from_bound_rows"],
        True,
        "metric row recomputation",
    )
    _require_exact(
        metric_authority["required_source_identity_sha256_fields"],
        ["scorer_executable_sha256", "scorer_source_manifest_sha256"],
        "scorer source identity",
    )
    _require_exact(
        metric_authority["required_bootstrap_receipt_sha256_fields"],
        [
            "visible_pairwise_group_cluster_bootstrap_receipt_sha256",
            "routing_group_cluster_bootstrap_receipt_sha256",
        ],
        "bootstrap receipts",
    )
    _require_exact(
        metric_authority["row_and_event_references_required"], True, "metric row references"
    )
    expected_metrics = {
        gate["metric"]
        for candidate in promotion["candidates"].values()
        for gate in candidate["gates"]
    }
    derivations = _require_fields(
        future["gate_derivations"], expected_metrics, "promotion gate derivations"
    )
    for metric, derivation in derivations.items():
        item = _require_fields(
            derivation,
            {"source_rows", "source_fields", "recompute_function"},
            f"{metric} derivation",
        )
        if not isinstance(item["source_rows"], str) or not item["source_rows"]:
            raise ValueError(f"{metric} source rows must be explicit")
        fields = item["source_fields"]
        if (
            not isinstance(fields, list)
            or not fields
            or any(not isinstance(field, str) or not field for field in fields)
            or len(fields) != len(set(fields))
        ):
            raise ValueError(f"{metric} source fields must be explicit and unique")
        function = item["recompute_function"]
        if not isinstance(function, str) or not function or "caller" in function.casefold():
            raise ValueError(f"{metric} recompute function must be frozen and row-derived")


def _validate_gate(gate: Any, label: str) -> None:
    if not isinstance(gate, Mapping):
        raise ValueError(f"{label} must be an object")
    gate_type = gate.get("gate_type")
    common = {"gate_type", "metric", "value_type", "operator", "threshold"}
    if gate_type == "threshold":
        _require_fields(gate, common, label)
    elif gate_type == "ratio":
        _require_fields(
            gate,
            common | {"numerator_metric", "denominator_metric", "zero_denominator"},
            label,
        )
        _require_exact(
            gate["zero_denominator"],
            "fail_promotion_with_null_metric",
            f"{label} zero denominator",
        )
    elif gate_type == "conditional_threshold":
        _require_fields(
            gate,
            common
            | {
                "condition_metric",
                "condition_value_type",
                "condition_operator",
                "condition_threshold",
                "condition_false",
            },
            label,
        )
        _require_exact(gate["condition_value_type"], "integer", f"{label} condition type")
        _require_nonnegative_int(gate["condition_threshold"], f"{label} condition threshold")
        _require_exact(
            gate["condition_false"],
            "fail_promotion_and_forbid_superiority_claim",
            f"{label} false condition",
        )
    else:
        raise ValueError(f"{label}.gate_type is invalid")
    if gate["value_type"] == "decimal":
        _require_decimal(gate["threshold"], f"{label} threshold")
    elif gate["value_type"] == "integer":
        _require_nonnegative_int(gate["threshold"], f"{label} threshold")
    else:
        raise ValueError(f"{label}.value_type must be decimal or integer")
    if gate["operator"] not in {"eq", "gt", "gte", "lt", "lte"}:
        raise ValueError(f"{label}.operator is invalid")


def _validate_promotion_contract(promotion: Any) -> None:
    value = _require_fields(
        promotion,
        {
            "numeric_semantics",
            "evidence_binding_validation",
            "candidates",
            "excluded_arms",
            "production_evidence",
        },
        "promotion_contract",
    )
    numeric = _require_fields(
        value["numeric_semantics"], {"decimal", "integer", "comparison"}, "numeric semantics"
    )
    decimal = _require_fields(
        numeric["decimal"],
        {"json_type", "parser", "grammar", "finite_only", "binary_float_conversion_allowed"},
        "Decimal semantics",
    )
    _require_exact(decimal["json_type"], "string", "Decimal JSON type")
    _require_exact(decimal["parser"], "decimal.Decimal", "Decimal parser")
    _require_exact(decimal["grammar"], r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$", "Decimal grammar")
    _require_exact(decimal["finite_only"], True, "Decimal finite policy")
    _require_exact(decimal["binary_float_conversion_allowed"], False, "binary float policy")
    integer = _require_fields(
        numeric["integer"], {"json_type", "booleans_allowed", "minimum"}, "integer semantics"
    )
    _require_exact(integer["json_type"], "integer", "integer JSON type")
    _require_exact(integer["booleans_allowed"], False, "integer boolean policy")
    _require_exact(integer["minimum"], 0, "integer minimum")
    _require_exact(
        numeric["comparison"],
        "Decimal thresholds and integer metrics are exact; ratio gates use exact rational "
        "comparison and no rounded quotient controls a gate",
        "promotion comparison semantics",
    )
    evidence_binding = _require_fields(
        value["evidence_binding_validation"],
        {
            "digest_algorithm",
            "bound_documents_field",
            "digest_only_claims_allowed",
            "extra_bound_documents_allowed",
            "provider_qualification_event_schema",
            "blind_adjudication_manifest_schema",
            "blind_adjudication_roster_schema",
            "repeatability_event_schema",
            "repeatability_journal_manifest_schema",
            "current_authority",
        },
        "promotion evidence binding validation",
    )
    expected_binding_values = {
        "digest_algorithm": "sha256",
        "bound_documents_field": "bound_evidence_documents",
        "digest_only_claims_allowed": False,
        "extra_bound_documents_allowed": False,
        "provider_qualification_event_schema": PROVIDER_QUALIFICATION_EVENT_SCHEMA_VERSION,
        "blind_adjudication_manifest_schema": BLIND_ADJUDICATION_MANIFEST_SCHEMA_VERSION,
        "blind_adjudication_roster_schema": BLIND_ADJUDICATION_ROSTER_SCHEMA_VERSION,
        "repeatability_event_schema": REPEATABILITY_EVENT_SCHEMA_VERSION,
        "repeatability_journal_manifest_schema": (REPEATABILITY_JOURNAL_MANIFEST_SCHEMA_VERSION),
        "current_authority": (
            "content_integrity_only; execution and production evidence authority remain "
            "forbidden until a later amendment and root-bound journal qualify them"
        ),
    }
    _require_exact(
        dict(evidence_binding), expected_binding_values, "promotion evidence binding semantics"
    )
    candidates = _require_fields(
        value["candidates"],
        {
            "A1_typed_abstaining_critic_3b",
            "A2_typed_abstaining_critic_7b",
            "B2_typed_route_and_prompt_repair",
        },
        "promotion candidates",
    )
    expected = {
        "A1_typed_abstaining_critic_3b": [
            ("threshold", "A1_minus_A0_common_three_macro_f1_fail", "decimal", "gte", "0.10"),
            (
                "threshold",
                "A1_minus_A0_common_three_bootstrap_ci_lower",
                "decimal",
                "gt",
                "0",
            ),
            ("threshold", "A1_common_three_coverage", "decimal", "gte", "0.80"),
            ("threshold", "A1_unsafe_visible_pass_count", "integer", "eq", 0),
            ("threshold", "A1_final_schema_valid_rate", "decimal", "gte", "0.98"),
            ("ratio", "A1_typed_correction_precision", "decimal", "gte", "0.80"),
            ("threshold", "A1_physical_authority_violation_count", "integer", "eq", 0),
        ],
        "A2_typed_abstaining_critic_7b": [
            ("threshold", "A2_minus_A0_common_three_macro_f1_fail", "decimal", "gte", "0.10"),
            (
                "threshold",
                "A2_minus_A0_common_three_bootstrap_ci_lower",
                "decimal",
                "gt",
                "0",
            ),
            ("threshold", "A2_common_three_coverage", "decimal", "gte", "0.80"),
            ("threshold", "A2_unsafe_visible_pass_count", "integer", "eq", 0),
            ("threshold", "A2_final_schema_valid_rate", "decimal", "gte", "0.98"),
            ("ratio", "A2_typed_correction_precision", "decimal", "gte", "0.80"),
            ("threshold", "A2_physical_authority_violation_count", "integer", "eq", 0),
            ("threshold", "A2_minus_A1_common_three_macro_f1_fail", "decimal", "gte", "0.05"),
            ("threshold", "A2_mean_latency_ratio_over_A1", "decimal", "lte", "2.00"),
        ],
        "B2_typed_route_and_prompt_repair": [
            (
                "threshold",
                "B2_minus_B1_group_weighted_exact_route_accuracy",
                "decimal",
                "gte",
                "0.15",
            ),
            ("threshold", "B2_vs_B1_exact_mcnemar_p", "decimal", "lt", "0.05"),
            ("threshold", "independent_recoverable_failure_groups", "integer", "gte", 8),
            (
                "conditional_threshold",
                "conditional_B2_minus_B1_robust_completion",
                "decimal",
                "gte",
                "0.10",
            ),
            (
                "conditional_threshold",
                "conditional_B2_minus_B1_completion_bootstrap_ci_lower",
                "decimal",
                "gt",
                "0",
            ),
            ("threshold", "B2_clean_control_non_regression_rate", "decimal", "eq", "1.0"),
            (
                "threshold",
                "B2_safe_abstention_on_unrecoverable_rate",
                "decimal",
                "eq",
                "1.0",
            ),
            ("threshold", "B2_intent_preservation_rate", "decimal", "eq", "1.0"),
            ("threshold", "B2_unsafe_publication_count", "integer", "eq", 0),
            ("threshold", "B2_repeatability_cases_3_of_3", "integer", "eq", 6),
            ("threshold", "B2_repeatability_required_cases", "integer", "eq", 6),
            ("threshold", "B2_physical_authority_violation_count", "integer", "eq", 0),
        ],
    }
    for candidate, expected_gates in expected.items():
        expected_fields = {"all_gates_required", "gates"}
        expected_fields.add(
            "required_disclosures" if candidate.startswith("A") else "current_preregistered_outcome"
        )
        expected_fields.add(
            "required_evidence_bindings" if candidate.startswith("A") else "derived_evidence"
        )
        item = _require_fields(candidates[candidate], expected_fields, f"{candidate} promotion")
        _require_exact(item["all_gates_required"], True, f"{candidate} all-gates rule")
        gates = item["gates"]
        if not isinstance(gates, list) or len(gates) != len(expected_gates):
            raise ValueError(f"{candidate} promotion gates are incomplete")
        observed_specs: list[tuple[Any, ...]] = []
        for index, gate in enumerate(gates):
            _validate_gate(gate, f"{candidate}.gates[{index}]")
            observed_specs.append(
                (
                    gate["gate_type"],
                    gate["metric"],
                    gate["value_type"],
                    gate["operator"],
                    gate["threshold"],
                )
            )
            if gate["gate_type"] == "conditional_threshold":
                _require_exact(
                    gate["condition_metric"],
                    "independent_recoverable_failure_groups",
                    "B conditional gate source",
                )
                _require_exact(gate["condition_threshold"], 8, "B conditional group threshold")
            if gate["gate_type"] == "ratio" and not (
                gate["numerator_metric"].endswith("typed_correction_correct_count")
                and gate["denominator_metric"].endswith("typed_correction_prediction_count")
            ):
                raise ValueError("typed-correction precision must use bound count evidence")
        if observed_specs != expected_gates:
            raise ValueError(f"{candidate} threshold or promotion gate changed")
        if candidate.startswith("A"):
            prefix = candidate[:2]
            _require_exact(
                item["required_evidence_bindings"],
                [
                    f"{prefix}_typed_correction_blind_adjudication_manifest_sha256",
                    f"{prefix}_typed_correction_adjudication_roster_sha256",
                ],
                f"{candidate} blind adjudication binding",
            )
        else:
            derived = _require_fields(
                item["derived_evidence"],
                {
                    "B2_repeatability_cases_3_of_3",
                    "caller_supplied_repeatability_aggregate_allowed",
                    "journal_bound_receipts_required",
                    "typed_input_bound_to_frozen_roster",
                },
                "B2 repeatability evidence",
            )
            _require_exact(
                derived["caller_supplied_repeatability_aggregate_allowed"],
                False,
                "repeatability aggregate authority",
            )
            _require_exact(
                derived["journal_bound_receipts_required"],
                True,
                "repeatability journal binding",
            )
            _require_exact(
                derived["typed_input_bound_to_frozen_roster"],
                True,
                "repeatability typed-input binding",
            )
    _require_exact(
        value["excluded_arms"],
        {
            "B0_retry_unchanged": "baseline_only",
            "B1_generic_prompt_repair": "baseline_only",
            "B3_oracle_route_ceiling": "oracle_only_never_deployable_or_promotion_eligible",
        },
        "excluded promotion arms",
    )
    production = _require_fields(
        value["production_evidence"],
        {
            "injected_test_provider_counts_as_production",
            "self_reported_ProviderIdentity_production_eligible_is_sufficient",
            "journal_bound_qualified_provider_identity_required",
        },
        "promotion production evidence",
    )
    if (
        production["injected_test_provider_counts_as_production"] is not False
        or production["self_reported_ProviderIdentity_production_eligible_is_sufficient"]
        is not False
        or production["journal_bound_qualified_provider_identity_required"] is not True
    ):
        raise ValueError(
            "production promotion evidence must be journal-bound and not self-reported"
        )


def _validate_execution_gate(gate: Any) -> None:
    value = _require_fields(
        gate,
        {
            "annotation_manifest",
            "current_annotation_state",
            "production_provider_state",
            "provider_execution_allowed_now",
            "execution_authorized",
            "provider_or_inference_operations",
            "pending_annotation_policy",
            "self_reported_provider_identity_is_authority",
            "future_unlock_requires",
            "test_provider_may_supply_production_evidence",
            "unknown_state_policy",
        },
        "execution_gate",
    )
    _require_exact(
        value["current_annotation_state"],
        "pending_blinded_annotation",
        "current annotation state",
    )
    _require_exact(
        value["production_provider_state"], "not_yet_qualified", "production provider state"
    )
    _require_exact(
        value["provider_execution_allowed_now"], False, "provider execution availability"
    )
    _require_exact(value["execution_authorized"], False, "execution authorization")
    _require_exact(
        value["provider_or_inference_operations"],
        [
            "visible_provider_call",
            "routing_provider_call",
            "oracle_provider_call",
            "format_repair_provider_call",
            "model_inference",
        ],
        "provider operation vocabulary",
    )
    _require_exact(
        value["pending_annotation_policy"],
        "forbid_every_provider_and_inference_operation",
        "pending provider policy",
    )
    if (
        value["self_reported_provider_identity_is_authority"] is not False
        or value["test_provider_may_supply_production_evidence"] is not False
    ):
        raise ValueError("self-reported or test providers cannot authorize production evidence")
    future = value["future_unlock_requires"]
    if not isinstance(future, list) or len(future) != 6:
        raise ValueError("future execution unlock requirements are incomplete")
    if not any("visible provider adapter or executable and processor" in item for item in future):
        raise ValueError("future unlock must bind the exact visible provider and processor")
    if not any("routing sandbox provider identity" in item for item in future):
        raise ValueError("future unlock must bind the exact routing sandbox provider")
    if not any("experiment B prompt contract manifest" in item for item in future):
        raise ValueError("future unlock must bind the exact experiment B prompt contract")
    if not any("complete authoritative evaluation closure" in item for item in future):
        raise ValueError("future unlock must bind the authoritative evaluation closure")


def _validate_canonicalization_contract(contract: Any) -> None:
    value = _require_fields(
        contract,
        {
            "surface_table",
            "supported_languages",
            "protected_dimensions",
            "allowed_rewrite",
            "comparison",
            "preserve_multiplicity_and_roles",
            "deletion_allowed",
            "insertion_allowed",
            "unknown_surface_policy",
        },
        "canonicalization_contract",
    )
    _require_exact(value["surface_table"], "surface_canonicalization.json", "surface table path")
    _require_exact(value["supported_languages"], ["en", "zh-Hans"], "canonical languages")
    _require_exact(value["protected_dimensions"], list(PROTECTED_DIMENSIONS), "protected intent")
    if (
        value["preserve_multiplicity_and_roles"] is not True
        or value["deletion_allowed"] is not False
        or value["insertion_allowed"] is not False
    ):
        raise ValueError("canonical rewrites must exactly preserve protected intent and roles")
    _require_exact(
        value["unknown_surface_policy"],
        "abstain_unsupported_language_surface",
        "canonical unknown-surface policy",
    )


def validate_routing_cases(
    roster: Mapping[str, Any], *, base_spec: Mapping[str, Any] | None = None
) -> dict[str, int]:
    """Validate the caller-independent 36-case routing roster."""

    value = _require_fields(
        roster,
        {
            "schema_version",
            "study_id",
            "base_spec_sha256",
            "counts",
            "class_contract",
            "repeatability_audit",
            "cases",
        },
        "routing_case_inputs",
    )
    _require_exact(value["schema_version"], ROUTING_SCHEMA_VERSION, "routing schema")
    _require_exact(value["study_id"], STUDY_ID, "routing study id")
    _require_exact(value["base_spec_sha256"], ORIGINAL_SPEC_SHA256, "routing base spec")
    expected_counts = {
        "total": 36,
        "clean_control": 30,
        "recoverable_baseline_failure": 0,
        "unrecoverable_failure": 6,
        "analysis_groups": 13,
        "repeatability_audit_cases": 6,
        "repeatability_audit_groups": 4,
        "repeatability_route_evaluations": 18,
    }
    _require_exact(value["counts"], expected_counts, "routing roster counts")
    classes = _require_fields(
        value["class_contract"],
        {"clean_control", "recoverable_baseline_failure", "unrecoverable_failure"},
        "routing class contract",
    )
    _require_fields(
        classes["clean_control"],
        {
            "failure_must_be_null",
            "deployable_arm_action",
            "provider_call_allowed",
            "retry_allowed",
            "counts_as_recoverable_baseline_failure",
        },
        "clean-control class",
    )
    if (
        classes["clean_control"]["failure_must_be_null"] is not True
        or classes["clean_control"]["provider_call_allowed"] is not False
        or classes["clean_control"]["retry_allowed"] is not False
        or classes["clean_control"]["counts_as_recoverable_baseline_failure"] is not False
    ):
        raise ValueError("clean controls must be no-op accepts, not recovery attempts")
    _require_fields(
        classes["recoverable_baseline_failure"],
        {
            "failure_must_be_non_null",
            "deployable_arm_action",
            "provider_call_allowed_after_execution_unlock",
            "counts_in_completion_denominator",
        },
        "recoverable-failure class",
    )
    _require_fields(
        classes["unrecoverable_failure"],
        {
            "failure_must_be_non_null",
            "deployable_arm_action",
            "provider_call_allowed_after_execution_unlock",
            "counts_in_completion_denominator",
        },
        "unrecoverable-failure class",
    )
    if classes["recoverable_baseline_failure"]["counts_in_completion_denominator"] is not True:
        raise ValueError("recoverable failures must be the only completion denominator")
    if classes["unrecoverable_failure"]["counts_in_completion_denominator"] is not False:
        raise ValueError("unrecoverable failures cannot enter the completion denominator")
    audit = _require_fields(
        value["repeatability_audit"],
        {
            "audit_id",
            "arms",
            "case_selection",
            "case_count",
            "audit_group_count",
            "repeat_indices",
            "same_typed_input_required",
            "typed_input_digest_projection",
            "required_equal_outputs",
            "journal_event_schema",
            "journal_manifest_schema",
            "route_evaluation_budget",
            "compile_attempt_budget",
            "physical_replay_budget",
            "counts_against_regular_recovery_attempt_budget",
            "provider_execution_requires_global_unlock",
        },
        "routing repeatability audit",
    )
    _require_exact(audit["audit_id"], "B2_repeatability_audit", "routing repeatability id")
    _require_exact(audit["arms"], ["B2_typed_route_and_prompt_repair"], "repeatability arm")
    _require_exact(audit["case_count"], 6, "routing repeatability cases")
    _require_exact(audit["audit_group_count"], 4, "routing repeatability groups")
    _require_exact(audit["repeat_indices"], [1, 2, 3], "routing repeat indices")
    _require_exact(audit["same_typed_input_required"], True, "routing repeat input")
    _require_exact(
        audit["typed_input_digest_projection"],
        list(REPEATABILITY_TYPED_INPUT_PROJECTION),
        "routing repeat typed-input projection",
    )
    _require_exact(
        audit["journal_event_schema"],
        REPEATABILITY_EVENT_SCHEMA_VERSION,
        "routing repeat event schema",
    )
    _require_exact(
        audit["journal_manifest_schema"],
        REPEATABILITY_JOURNAL_MANIFEST_SCHEMA_VERSION,
        "routing repeat manifest schema",
    )
    _require_exact(audit["route_evaluation_budget"], 18, "routing repeat route budget")
    _require_exact(audit["compile_attempt_budget"], 0, "routing repeat compile budget")
    _require_exact(audit["physical_replay_budget"], 0, "routing repeat replay budget")
    _require_exact(
        audit["counts_against_regular_recovery_attempt_budget"],
        False,
        "routing repeat regular-budget isolation",
    )
    cases = value["cases"]
    if not isinstance(cases, list) or len(cases) != 36:
        raise ValueError("routing roster must contain exactly 36 cases")
    case_fields = {
        "case_id",
        "split",
        "group_id",
        "seed",
        "original_prompt",
        "baseline_class",
        "failure",
        "gold_route",
        "repeatability_audit_group_id",
    }
    case_ids: list[str] = []
    observed_counts = {key: 0 for key in classes}
    audit_groups: set[str] = set()
    audit_cases = 0
    for index, case in enumerate(cases):
        item = _require_fields(case, case_fields, f"routing case[{index}]")
        for key in ("case_id", "split", "group_id", "original_prompt", "gold_route"):
            if not isinstance(item[key], str) or not item[key]:
                raise ValueError(f"routing case[{index}].{key} must be non-empty text")
        if item["split"] not in {"train", "dev", "test"}:
            raise ValueError(f"routing case[{index}].split is invalid")
        if item["seed"] is not None:
            _require_nonnegative_int(item["seed"], f"routing case[{index}].seed")
        baseline_class = item["baseline_class"]
        if baseline_class not in observed_counts:
            raise ValueError(f"routing case[{index}].baseline_class is invalid")
        observed_counts[baseline_class] += 1
        case_ids.append(item["case_id"])
        if baseline_class == "clean_control":
            if (
                item["failure"] is not None
                or item["gold_route"] != "accept_existing_compile"
                or item["repeatability_audit_group_id"] is not None
            ):
                raise ValueError(
                    "clean controls must have null failure and runner no-op acceptance"
                )
        else:
            failure = _require_fields(
                item["failure"],
                {
                    "failure_code",
                    "untyped_failure_summary",
                    "typed_failure",
                    "trusted_state",
                    "asset_availability",
                },
                f"routing case[{index}].failure",
            )
            for field in ("failure_code", "untyped_failure_summary"):
                if not isinstance(failure[field], str) or not failure[field]:
                    raise ValueError(f"routing case[{index}].failure.{field} is invalid")
            for field in ("typed_failure", "trusted_state", "asset_availability"):
                if not isinstance(failure[field], Mapping) or not failure[field]:
                    raise ValueError(f"routing case[{index}].failure.{field} is invalid")
            if not item["gold_route"].startswith("abstain_"):
                raise ValueError("unrecoverable routing cases require exact typed abstention gold")
            audit_group = item["repeatability_audit_group_id"]
            if not isinstance(audit_group, str) or not audit_group:
                raise ValueError("all six non-control cases must enter the repeatability audit")
            audit_groups.add(audit_group)
            audit_cases += 1
    if len(set(case_ids)) != 36:
        raise ValueError("routing roster case_id values must be unique")
    if observed_counts != {
        "clean_control": 30,
        "recoverable_baseline_failure": 0,
        "unrecoverable_failure": 6,
    }:
        raise ValueError("routing baseline classes do not match the frozen 30/0/6 split")
    if audit_cases != 6 or len(audit_groups) != 4:
        raise ValueError("repeatability audit must cover all six non-controls in four audit groups")
    if base_spec is not None:
        experiments = base_spec.get("experiments")
        if not isinstance(experiments, list):
            raise ValueError("base spec experiments are unavailable for routing binding")
        matches = [
            experiment
            for experiment in experiments
            if isinstance(experiment, Mapping)
            and experiment.get("experiment_id") == "B_typed_failure_prompt_fallback"
        ]
        if len(matches) != 1 or not isinstance(matches[0].get("samples"), list):
            raise ValueError("base spec B samples are unavailable for routing binding")
        samples = matches[0]["samples"]
        if len(samples) != 36:
            raise ValueError("base spec B roster must contain 36 samples")
        for case, sample in zip(cases, samples):
            expected_seed = (
                sample.get("source_record", {}).get("seed")
                if isinstance(sample.get("source_record"), Mapping)
                else None
            )
            expected_class = (
                "clean_control" if sample.get("clean_control") is True else "unrecoverable_failure"
            )
            expected_binding = (
                sample.get("case_id"),
                sample.get("split"),
                sample.get("group_id"),
                expected_seed,
                sample.get("task_context"),
                expected_class,
                sample.get("gold_route"),
            )
            observed_binding = (
                case["case_id"],
                case["split"],
                case["group_id"],
                case["seed"],
                case["original_prompt"],
                case["baseline_class"],
                case["gold_route"],
            )
            if observed_binding != expected_binding:
                raise ValueError(f"routing roster diverges from base spec at {case['case_id']}")
    if canonical_sha256(roster) != EXPECTED_ROUTING_SHA256:
        raise ValueError("routing case roster canonical digest mismatch")
    return observed_counts


def validate_model_content_manifest(
    manifest: Mapping[str, Any], *, expected_binding: Mapping[str, Any]
) -> dict[str, int]:
    """Strictly parse one separately produced model-content manifest."""

    value = _require_fields(
        manifest, {"schema_version", "model_id", "revision", "entries"}, "model content manifest"
    )
    _require_exact(value["schema_version"], MODEL_CONTENT_SCHEMA_VERSION, "model content schema")
    _require_exact(value["model_id"], expected_binding["model_id"], "model content model_id")
    _require_exact(value["revision"], expected_binding["revision"], "model content revision")
    entries = value["entries"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("model content entries must be non-empty")
    paths: list[str] = []
    total_bytes = 0
    for index, entry in enumerate(entries):
        item = _require_fields(
            entry,
            {"path", "blob_id", "blob_id_algorithm", "size_bytes", "content_sha256"},
            f"model content entry[{index}]",
        )
        path = item["path"]
        relative = PurePosixPath(path) if isinstance(path, str) else None
        if (
            not isinstance(path, str)
            or not path
            or relative is None
            or relative.is_absolute()
            or ".." in relative.parts
            or "." in relative.parts
            or "\\" in path
            or re.match(r"^[A-Za-z]:", path) is not None
            or relative.as_posix() != path
        ):
            raise ValueError(f"model content entry[{index}].path is invalid")
        paths.append(path)
        algorithm = item["blob_id_algorithm"]
        if algorithm not in {"sha256", "git_blob_sha1"}:
            raise ValueError(f"model content entry[{index}].blob_id_algorithm is invalid")
        _require_lower_hex(
            item["blob_id"], 64 if algorithm == "sha256" else 40, f"entry[{index}].blob_id"
        )
        _require_lower_hex(item["content_sha256"], 64, f"entry[{index}].content_sha256")
        total_bytes += _require_nonnegative_int(item["size_bytes"], f"entry[{index}].size_bytes")
    if paths != sorted(paths) or len(set(paths)) != len(paths):
        raise ValueError("model content entry paths must be unique and sorted")
    observed_digest = canonical_sha256(manifest)
    _require_exact(
        observed_digest,
        expected_binding["canonical_sha256"],
        "model content canonical digest",
    )
    return {"entry_count": len(entries), "total_bytes": total_bytes}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _resolve_binding(bundle_dir: Path, relative_path: str, *, within: Path) -> Path:
    if Path(relative_path).is_absolute():
        raise ValueError(f"bound path must be relative: {relative_path}")
    candidate = (bundle_dir / relative_path).resolve(strict=True)
    allowed_root = within.resolve(strict=True)
    if not candidate.is_relative_to(allowed_root):
        raise ValueError(f"bound path escapes its allowed root: {relative_path}")
    if not candidate.is_file():
        raise ValueError(f"bound path is not a file: {relative_path}")
    return candidate


def _validate_source_bindings(
    bindings: Any,
    *,
    bundle_dir: Path,
    surface_table: Mapping[str, Any],
    routing_cases: Mapping[str, Any],
    verify_files: bool,
) -> Mapping[str, Any]:
    value = _require_fields(
        bindings,
        {
            "digest_algorithm",
            "canonical_json",
            "original_spec",
            "original_run_log_prefix",
            "model_content_manifests",
            "surface_canonicalization",
            "routing_case_inputs",
            "digest_exclusions",
        },
        "source_bindings",
    )
    _require_exact(value["digest_algorithm"], "sha256", "digest algorithm")
    _require_exact(
        value["canonical_json"],
        {
            "encoding": "UTF-8",
            "ensure_ascii": False,
            "sort_keys": True,
            "separators": [",", ":"],
        },
        "canonical JSON contract",
    )
    spec = _require_fields(value["original_spec"], {"path", "sha256"}, "original spec binding")
    _require_exact(spec["path"], "../../experiment_spec.json", "original spec path")
    _require_exact(spec["sha256"], ORIGINAL_SPEC_SHA256, "original spec digest")
    log = _require_fields(
        value["original_run_log_prefix"],
        {"path", "line_count", "sha256"},
        "original log-prefix binding",
    )
    _require_exact(log["path"], "../../run_log.jsonl", "original log path")
    _require_exact(log["line_count"], ORIGINAL_LOG_PREFIX_LINES, "original log line count")
    _require_exact(log["sha256"], ORIGINAL_LOG_PREFIX_SHA256, "original log-prefix digest")
    models = value["model_content_manifests"]
    _require_exact(models, list(MODEL_BINDINGS), "model content bindings")
    surface = _require_fields(
        value["surface_canonicalization"], {"path", "canonical_sha256"}, "surface binding"
    )
    _require_exact(surface["path"], "surface_canonicalization.json", "surface filename")
    _require_exact(surface["canonical_sha256"], EXPECTED_SURFACE_SHA256, "surface binding digest")
    _require_exact(
        canonical_sha256(surface_table), EXPECTED_SURFACE_SHA256, "surface content digest"
    )
    routing = _require_fields(
        value["routing_case_inputs"],
        {"path", "canonical_sha256", "case_count"},
        "routing binding",
    )
    _require_exact(routing["path"], "routing_case_inputs.json", "routing filename")
    _require_exact(routing["canonical_sha256"], EXPECTED_ROUTING_SHA256, "routing binding digest")
    _require_exact(routing["case_count"], 36, "routing binding case count")
    _require_exact(
        canonical_sha256(routing_cases), EXPECTED_ROUTING_SHA256, "routing content digest"
    )
    expected_exclusions = [
        "amendment_contract.json self digest",
        "amendment root manifest",
        "implementation source",
        "test source",
        "derived full effective experiment spec v2",
        "append-only run log bytes after the frozen six-line prefix",
    ]
    _require_exact(value["digest_exclusions"], expected_exclusions, "digest-cycle exclusions")
    if verify_files:
        study_root = bundle_dir.parents[1]
        spec_path = _resolve_binding(bundle_dir, spec["path"], within=study_root)
        if _sha256_file(spec_path) != ORIGINAL_SPEC_SHA256:
            raise ValueError("original spec file digest mismatch")
        log_path = _resolve_binding(bundle_dir, log["path"], within=study_root)
        lines = log_path.read_bytes().splitlines(keepends=True)
        if len(lines) < ORIGINAL_LOG_PREFIX_LINES:
            raise ValueError("run log no longer contains the frozen six-line prefix")
        prefix = b"".join(lines[:ORIGINAL_LOG_PREFIX_LINES])
        if hashlib.sha256(prefix).hexdigest() != ORIGINAL_LOG_PREFIX_SHA256:
            raise ValueError("original six-line run-log prefix digest mismatch")
        for binding in MODEL_BINDINGS:
            manifest_path = _resolve_binding(bundle_dir, binding["path"], within=bundle_dir)
            manifest = _load_json_object(manifest_path)
            validate_model_content_manifest(manifest, expected_binding=binding)
    return value


def _validate_claim_boundary(boundary: Any) -> None:
    value = _require_fields(
        boundary,
        {
            "phase",
            "results_generated",
            "model_loaded",
            "provider_or_inference_invoked",
            "study_execution_network_calls",
            "simulator_invoked",
            "physical_gate_authority",
        },
        "claim_boundary",
    )
    _require_exact(value["phase"], "pre_execution_protocol_amendment", "amendment phase")
    for field in (
        "results_generated",
        "model_loaded",
        "provider_or_inference_invoked",
        "simulator_invoked",
    ):
        _require_exact(value[field], False, f"claim boundary {field}")
    _require_exact(value["study_execution_network_calls"], 0, "claim boundary study network calls")
    _require_exact(
        value["physical_gate_authority"],
        "deterministic_runtime_and_typed_geometry_only",
        "physical gate authority",
    )


def _validate_integration_contract(integration: Any) -> None:
    value = _require_fields(
        integration,
        {
            "full_effective_spec_v2_required",
            "overlay_spec_allowed",
            "v2_must_preserve_all_v1_cases_and_artifact_bindings",
            "v2_must_bind_model_content_surface_and_routing_manifests",
            "v2_must_include_all_amended_A_B_execution_and_promotion_semantics",
            "this_contract_binds_spec_v2_digest",
            "reason_spec_v2_not_bound",
        },
        "integration_contract",
    )
    required_true = (
        "full_effective_spec_v2_required",
        "v2_must_preserve_all_v1_cases_and_artifact_bindings",
        "v2_must_bind_model_content_surface_and_routing_manifests",
        "v2_must_include_all_amended_A_B_execution_and_promotion_semantics",
    )
    for field in required_true:
        _require_exact(value[field], True, f"integration {field}")
    _require_exact(value["overlay_spec_allowed"], False, "overlay spec policy")
    _require_exact(value["this_contract_binds_spec_v2_digest"], False, "spec-v2 cycle policy")
    _require_exact(
        value["reason_spec_v2_not_bound"],
        "the current amendment root manifest binds the full effective spec v2 without creating a "
        "digest cycle",
        "current amendment-root spec-v2 binding reason",
    )


def validate_amendment(
    contract: Mapping[str, Any],
    surface_table: Mapping[str, Any],
    *,
    bundle_dir: Path | None = None,
    routing_cases: Mapping[str, Any] | None = None,
    verify_bindings: bool = False,
) -> dict[str, Any]:
    """Fail closed unless amendment 01 and every normative dependency are exact."""

    value = _require_fields(
        contract,
        {
            "schema_version",
            "amendment_id",
            "study_id",
            "status",
            "claim_boundary",
            "source_bindings",
            "experiment_a",
            "experiment_b",
            "authoritative_evaluation_contract",
            "canonicalization_contract",
            "promotion_contract",
            "execution_gate",
            "integration_contract",
        },
        "amendment_contract",
    )
    _require_exact(value["schema_version"], SCHEMA_VERSION, "amendment schema")
    _require_exact(value["amendment_id"], AMENDMENT_ID, "amendment id")
    _require_exact(value["study_id"], STUDY_ID, "amendment study id")
    _require_exact(value["status"], "preregistered_pre_execution", "amendment status")
    _validate_claim_boundary(value["claim_boundary"])
    _validate_experiment_a(value["experiment_a"])
    _validate_experiment_b(value["experiment_b"])
    _validate_surface_table(surface_table)
    _validate_canonicalization_contract(value["canonicalization_contract"])
    _validate_promotion_contract(value["promotion_contract"])
    _validate_authoritative_evaluation_contract(
        value["authoritative_evaluation_contract"], value["promotion_contract"]
    )
    _validate_execution_gate(value["execution_gate"])
    _validate_integration_contract(value["integration_contract"])
    root = (bundle_dir or DEFAULT_AMENDMENT_ROOT).expanduser().resolve(strict=True)
    roster = (
        routing_cases
        if routing_cases is not None
        else _load_json_object(root / "routing_case_inputs.json")
    )
    base_spec: Mapping[str, Any] | None = None
    if verify_bindings:
        base_spec = _load_json_object(root / "../../experiment_spec.json")
    counts = validate_routing_cases(roster, base_spec=base_spec)
    _validate_source_bindings(
        value["source_bindings"],
        bundle_dir=root,
        surface_table=surface_table,
        routing_cases=roster,
        verify_files=verify_bindings,
    )
    observed_contract_digest = canonical_sha256(contract)
    if observed_contract_digest != EXPECTED_CONTRACT_SHA256:
        raise ValueError("amendment contract canonical digest mismatch")
    return {
        "study_id": STUDY_ID,
        "amendment_id": AMENDMENT_ID,
        "contract_sha256": observed_contract_digest,
        "surface_table_sha256": canonical_sha256(surface_table),
        "routing_case_inputs_sha256": canonical_sha256(roster),
        "common_visible_check_mapping": dict(COMMON_VISIBLE_CHECK_MAPPING),
        "a0_warning_maps_to": "abstain",
        "routing_baseline_classes": counts,
        "provider_execution_allowed_now": False,
        "production_provider_state": "not_yet_qualified",
    }


def load_amendment(
    bundle_dir: Path = DEFAULT_AMENDMENT_ROOT, *, verify_bindings: bool = False
) -> dict[str, Any]:
    """Load and validate amendment 01 through one deep, side-effect-free seam."""

    root = bundle_dir.expanduser().resolve(strict=True)
    contract = _load_json_object(root / "amendment_contract.json")
    surface_table = _load_json_object(root / "surface_canonicalization.json")
    routing_cases = _load_json_object(root / "routing_case_inputs.json")
    summary = validate_amendment(
        contract,
        surface_table,
        bundle_dir=root,
        routing_cases=routing_cases,
        verify_bindings=verify_bindings,
    )
    return {
        "contract": contract,
        "surface_table": surface_table,
        "routing_cases": routing_cases,
        "contract_sha256": canonical_sha256(contract),
        "surface_table_sha256": canonical_sha256(surface_table),
        "routing_case_inputs_sha256": canonical_sha256(routing_cases),
        "summary": summary,
    }


def _reject_projection_leakage(value: Any, *, allow_top_level_gold: bool, depth: int = 0) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in RECURSIVE_FORBIDDEN_PROJECTION_FIELDS and not (
                allow_top_level_gold and depth == 0 and key == "gold_route"
            ):
                raise ValueError(f"gold or annotation leakage through projection field: {key}")
            _reject_projection_leakage(item, allow_top_level_gold=False, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            _reject_projection_leakage(item, allow_top_level_gold=False, depth=depth + 1)


def validate_arm_projection(
    contract: Mapping[str, Any],
    arm: str,
    payload: Mapping[str, Any],
    *,
    oracle_authority: bool = False,
) -> dict[str, Any]:
    """Validate one exact runner-owned plus arm-specific routing projection."""

    if canonical_sha256(contract) != EXPECTED_CONTRACT_SHA256:
        raise ValueError("arm projection requires the exact amendment 01 contract")
    _validate_experiment_b(contract.get("experiment_b"))
    if arm not in ARM_FAILURE_FIELDS:
        raise ValueError(f"unknown B arm: {arm}")
    try:
        normalized_payload = json.loads(canonical_json_bytes(payload))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{arm} payload must be canonical-JSON serializable") from error
    expected_fields = set(COMMON_RUNNER_FIELDS) | set(ARM_FAILURE_FIELDS[arm])
    value = _require_fields(normalized_payload, expected_fields, f"{arm} payload")
    _reject_projection_leakage(value, allow_top_level_gold=arm == "B3_oracle_route_ceiling")
    for field in ("original_prompt", "blinded_case_id", "invocation_id"):
        if not isinstance(value[field], str) or not value[field]:
            raise ValueError(f"{arm}.{field} must be non-empty text")
    prompt_contract = contract["experiment_b"]["prompt_contracts"][arm]
    _require_exact(
        value["routing_instruction"],
        prompt_contract["routing_instruction_utf8"],
        f"{arm} frozen prompt contract instruction",
    )
    _require_exact(
        value["prompt_template_version"],
        prompt_contract["prompt_template_version"],
        f"{arm} frozen prompt contract template",
    )
    if value["seed"] is not None:
        _require_nonnegative_int(value["seed"], f"{arm}.seed")
    _require_nonnegative_int(value["attempt"], f"{arm}.attempt")
    _require_exact(value["allowed_routes"], list(ROUTE_VOCABULARY), f"{arm}.allowed_routes")
    if not isinstance(value["resource_reservation"], Mapping):
        raise ValueError(f"{arm}.resource_reservation must be an object")
    if arm == "B0_retry_unchanged":
        if not isinstance(value["failure_code"], str) or not value["failure_code"]:
            raise ValueError("B0 failure_code must be non-empty text")
    elif arm == "B1_generic_prompt_repair":
        if (
            not isinstance(value["untyped_failure_summary"], str)
            or not value["untyped_failure_summary"]
        ):
            raise ValueError("B1 untyped_failure_summary must be non-empty text")
    else:
        for field in ("typed_failure", "trusted_state", "asset_availability"):
            if not isinstance(value[field], Mapping) or not value[field]:
                raise ValueError(f"{arm}.{field} must be a non-empty object")
        if value["visible_report"] is not None:
            raise ValueError("visible_report must remain null under amendment 01")
    if arm == "B3_oracle_route_ceiling":
        if oracle_authority is not True:
            raise ValueError("B3 requires explicit disjoint oracle authority")
        if value["gold_route"] not in ROUTE_VOCABULARY:
            raise ValueError("B3 gold_route must be in the frozen route vocabulary")
    elif oracle_authority:
        raise ValueError("oracle authority is valid only for B3")
    return dict(value)


def authorize_operation(contract: Mapping[str, Any], operation: str) -> dict[str, Any]:
    """Authorize only non-execution work while annotation and providers are unqualified."""

    if canonical_sha256(contract) != EXPECTED_CONTRACT_SHA256:
        raise ValueError("operation authorization requires the exact amendment 01 contract")
    if not isinstance(operation, str) or not operation:
        raise ValueError("operation must be non-empty text")
    gate = contract["execution_gate"]
    _validate_execution_gate(gate)
    if operation in PROVIDER_OPERATIONS:
        if gate["current_annotation_state"] == "pending_blinded_annotation":
            raise ValueError(
                "pending blinded annotation forbids every provider and inference operation"
            )
        if gate["production_provider_state"] != "qualified":
            raise ValueError("production provider is not qualified")
        if gate["execution_authorized"] is not True:
            raise ValueError("provider execution is not authorized")
    elif operation not in NON_EXECUTION_OPERATIONS:
        raise ValueError("unknown operation is forbidden by the fail-closed execution gate")
    return {"operation": operation, "authorized": True}


def _normalize_surface(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def canonicalize_surface_term(
    surface_table: Mapping[str, Any], dimension: str, language: str, surface: str
) -> str:
    """Map one registered English or Chinese surface to its frozen canonical term."""

    _validate_surface_table(surface_table)
    if dimension not in PROTECTED_DIMENSIONS:
        raise ValueError(f"unknown protected dimension: {dimension}")
    if language not in {"en", "zh-Hans"}:
        raise ValueError(f"unsupported canonicalization language: {language}")
    if not isinstance(surface, str) or not surface.strip():
        raise ValueError("surface must be non-empty text")
    needle = _normalize_surface(surface)
    matches = [
        entry["canonical"]
        for entry in surface_table["vocabulary"][dimension]
        if needle in {_normalize_surface(candidate) for candidate in entry[language]}
    ]
    if len(matches) != 1:
        raise ValueError(
            f"unsupported language surface for {dimension}/{language}; typed abstention is required"
        )
    return matches[0]


def validate_intent_preservation(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    """Require exact trusted typed-intent equality for all six protected dimensions."""

    left = _require_fields(before, set(PROTECTED_DIMENSIONS), "original typed intent")
    right = _require_fields(after, set(PROTECTED_DIMENSIONS), "revised typed intent")
    for dimension in PROTECTED_DIMENSIONS:
        try:
            left_bytes = canonical_json_bytes(left[dimension])
            right_bytes = canonical_json_bytes(right[dimension])
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"protected intent dimension {dimension} is not canonical JSON"
            ) from error
        if left_bytes != right_bytes:
            raise ValueError(f"protected intent dimension {dimension} changed")
    return True


def _comparison_passes(
    left: Decimal | Fraction | int, operator: str, right: Decimal | Fraction | int
) -> bool:
    if operator == "eq":
        return left == right
    if operator == "gt":
        return left > right
    if operator == "gte":
        return left >= right
    if operator == "lt":
        return left < right
    if operator == "lte":
        return left <= right
    raise ValueError(f"unsupported comparison operator: {operator}")


def _serialized_fraction(value: Fraction) -> str:
    """Render a finite ratio as Decimal text, otherwise as an exact fraction."""

    denominator = value.denominator
    twos = 0
    fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        return f"{value.numerator}/{value.denominator}"
    places = max(twos, fives)
    if places == 0:
        return str(value.numerator)
    scaled = value.numerator * 2 ** (places - twos) * 5 ** (places - fives)
    sign = "-" if scaled < 0 else ""
    digits = str(abs(scaled)).zfill(places + 1)
    whole = digits[:-places]
    fractional = digits[-places:].rstrip("0")
    return f"{sign}{whole}.{fractional}" if fractional else f"{sign}{whole}"


def _serialized_metric(value: Decimal | Fraction | int | None) -> str | int | None:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Fraction):
        return _serialized_fraction(value)
    return value


def _bound_evidence_document(
    documents: Mapping[str, Any], digest: Any, label: str
) -> Mapping[str, Any]:
    key = _require_lower_hex(digest, 64, f"{label} digest")
    if key not in documents:
        raise ValueError(f"{label} requires a bound evidence document")
    document = documents[key]
    if not isinstance(document, Mapping):
        raise ValueError(f"{label} bound evidence document must be an object")
    _require_exact(canonical_sha256(document), key, f"{label} bound evidence document digest")
    return document


def _validate_provider_qualification_document(
    document: Mapping[str, Any],
    *,
    candidate: str,
    identity_kind: Any,
    is_injected_test: Any,
) -> None:
    value = _require_fields(
        document,
        {
            "schema_version",
            "event_type",
            "candidate",
            "provider_identity_kind",
            "provider_is_injected_test",
            "visible_adapter_or_executable_sha256",
            "processor_identity_sha256",
            "routing_sandbox_provider_identity_sha256",
            "model_content_manifest_sha256",
        },
        "provider qualification journal event",
    )
    _require_exact(
        value["schema_version"],
        PROVIDER_QUALIFICATION_EVENT_SCHEMA_VERSION,
        "provider qualification event schema",
    )
    _require_exact(
        value["event_type"],
        "production_provider_qualified",
        "provider qualification event type",
    )
    _require_exact(value["candidate"], candidate, "provider qualification candidate")
    _require_exact(value["provider_identity_kind"], identity_kind, "bound provider identity kind")
    _require_exact(
        value["provider_is_injected_test"],
        is_injected_test,
        "bound injected-provider status",
    )
    for field in (
        "visible_adapter_or_executable_sha256",
        "processor_identity_sha256",
        "routing_sandbox_provider_identity_sha256",
    ):
        _require_lower_hex(value[field], 64, field)
    expected_model_digest = MODEL_BINDINGS[1 if candidate.startswith("A2_") else 0][
        "canonical_sha256"
    ]
    _require_exact(
        value["model_content_manifest_sha256"],
        expected_model_digest,
        "qualified provider model-content binding",
    )


def _validate_blind_adjudication_documents(
    manifest: Mapping[str, Any],
    roster: Mapping[str, Any],
    *,
    candidate: str,
    roster_digest: str,
    correct_count: int,
    prediction_count: int,
) -> None:
    roster_value = _require_fields(
        roster,
        {
            "schema_version",
            "candidate",
            "prediction_count",
            "blinded_prediction_roster_sha256",
        },
        "blind typed-correction adjudication roster",
    )
    _require_exact(
        roster_value["schema_version"],
        BLIND_ADJUDICATION_ROSTER_SCHEMA_VERSION,
        "blind adjudication roster schema",
    )
    _require_exact(roster_value["candidate"], candidate, "blind adjudication roster candidate")
    _require_exact(
        roster_value["prediction_count"], prediction_count, "blind adjudication roster count"
    )
    _require_lower_hex(
        roster_value["blinded_prediction_roster_sha256"],
        64,
        "blinded prediction roster commitment",
    )
    manifest_value = _require_fields(
        manifest,
        {
            "schema_version",
            "candidate",
            "independent_adjudication",
            "blinded_to",
            "adjudicator_identity_sha256",
            "adjudication_roster_sha256",
            "correct_count",
            "prediction_count",
        },
        "blind typed-correction adjudication manifest",
    )
    _require_exact(
        manifest_value["schema_version"],
        BLIND_ADJUDICATION_MANIFEST_SCHEMA_VERSION,
        "blind adjudication manifest schema",
    )
    _require_exact(manifest_value["candidate"], candidate, "blind adjudication candidate")
    _require_exact(
        manifest_value["independent_adjudication"], True, "independent adjudication status"
    )
    _require_exact(
        manifest_value["blinded_to"],
        ["candidate_arm", "provider_identity", "promotion_thresholds"],
        "blind adjudication masking",
    )
    _require_lower_hex(manifest_value["adjudicator_identity_sha256"], 64, "adjudicator identity")
    _require_exact(
        manifest_value["adjudication_roster_sha256"],
        roster_digest,
        "blind adjudication roster binding",
    )
    _require_exact(
        manifest_value["correct_count"], correct_count, "blind adjudication correct count"
    )
    _require_exact(
        manifest_value["prediction_count"],
        prediction_count,
        "blind adjudication prediction count",
    )


def _repeatability_typed_input(case: Mapping[str, Any]) -> dict[str, Any]:
    failure = case["failure"]
    return {
        "schema_version": REPEATABILITY_INPUT_SCHEMA_VERSION,
        "case_id": case["case_id"],
        "group_id": case["group_id"],
        "original_prompt": case["original_prompt"],
        "seed": case["seed"],
        "routing_instruction": "Choose exactly one frozen route from trusted typed context.",
        "allowed_routes": list(ROUTE_VOCABULARY),
        "typed_failure": failure["typed_failure"],
        "trusted_state": failure["trusted_state"],
        "asset_availability": failure["asset_availability"],
        "visible_report": None,
    }


def repeatability_typed_input_sha256(roster: Mapping[str, Any], case_id: str) -> str:
    """Digest the frozen B2 audit input for one of the six non-control cases."""

    validate_routing_cases(roster)
    matches = [case for case in roster["cases"] if case["case_id"] == case_id]
    if len(matches) != 1 or matches[0]["baseline_class"] != "unrecoverable_failure":
        raise ValueError("repeatability case must be one frozen non-control roster entry")
    return canonical_sha256(_repeatability_typed_input(matches[0]))


def _validated_repeatability_pass_count(
    roster: Mapping[str, Any], receipts: Any, documents: Mapping[str, Any], manifest_digest: Any
) -> tuple[int, set[str]]:
    cases = {
        case["case_id"]: case
        for case in roster["cases"]
        if case["baseline_class"] == "unrecoverable_failure"
    }
    if not isinstance(receipts, list) or len(receipts) != len(cases) * 3:
        raise ValueError("repeatability receipts must cover the exact 6-case x 3-repeat roster")
    expected_pairs = {(case_id, repeat) for case_id in cases for repeat in (1, 2, 3)}
    observed_pairs: set[tuple[str, int]] = set()
    event_digests: set[str] = set()
    by_case: dict[str, list[Mapping[str, Any]]] = {case_id: [] for case_id in cases}
    for index, receipt in enumerate(receipts):
        envelope = _require_fields(
            receipt, {"journal_event_sha256"}, f"repeatability receipt[{index}]"
        )
        journal_digest = _require_lower_hex(
            envelope["journal_event_sha256"], 64, "repeatability journal event digest"
        )
        if journal_digest in event_digests:
            raise ValueError("repeatability journal event digests must be unique")
        event_digests.add(journal_digest)
        item = _require_fields(
            _bound_evidence_document(
                documents, journal_digest, f"repeatability receipt[{index}] journal event"
            ),
            {
                "schema_version",
                "event_type",
                "audit_id",
                "case_id",
                "audit_group_id",
                "repeat_index",
                "typed_input_sha256",
                "route",
                "revised_prompt_sha256",
                "compile_attempt_count",
                "physical_replay_count",
            },
            f"repeatability journal event[{index}]",
        )
        _require_exact(
            item["schema_version"],
            REPEATABILITY_EVENT_SCHEMA_VERSION,
            f"repeatability event[{index}] schema",
        )
        _require_exact(
            item["event_type"],
            "B2_repeatability_observation",
            f"repeatability event[{index}] type",
        )
        _require_exact(
            item["audit_id"], "B2_repeatability_audit", f"repeatability event[{index}] audit"
        )
        case_id = item["case_id"]
        if case_id not in cases:
            raise ValueError(
                "repeatability receipt includes a case outside the frozen six-case roster"
            )
        _require_exact(
            item["audit_group_id"],
            cases[case_id]["repeatability_audit_group_id"],
            f"repeatability receipt[{index}] audit group",
        )
        repeat_index = item["repeat_index"]
        if type(repeat_index) is not int or repeat_index not in {1, 2, 3}:
            raise ValueError("repeatability repeat_index must be exactly 1, 2, or 3")
        pair = (case_id, repeat_index)
        if pair in observed_pairs:
            raise ValueError("repeatability receipts contain a duplicate case/repeat pair")
        observed_pairs.add(pair)
        _require_exact(
            item["typed_input_sha256"],
            canonical_sha256(_repeatability_typed_input(cases[case_id])),
            f"repeatability event[{index}] frozen typed input digest",
        )
        if item["route"] not in ROUTE_VOCABULARY:
            raise ValueError("repeatability route is outside the frozen vocabulary")
        _require_lower_hex(item["revised_prompt_sha256"], 64, "repeatability revised prompt digest")
        _require_exact(
            item["compile_attempt_count"], 0, f"repeatability event[{index}] compile count"
        )
        _require_exact(
            item["physical_replay_count"], 0, f"repeatability event[{index}] replay count"
        )
        by_case[case_id].append(item)
    if observed_pairs != expected_pairs:
        raise ValueError("repeatability receipts must cover the exact 6-case x 3-repeat roster")
    passed = 0
    for case_id, case_receipts in by_case.items():
        ordered = sorted(case_receipts, key=lambda item: item["repeat_index"])
        if len({item["typed_input_sha256"] for item in ordered}) != 1:
            raise ValueError(f"repeatability typed input changed across repeats for {case_id}")
        outputs = {(item["route"], item["revised_prompt_sha256"]) for item in ordered}
        passed += int(len(outputs) == 1)
    manifest = _require_fields(
        _bound_evidence_document(documents, manifest_digest, "B2 repeatability journal manifest"),
        {
            "schema_version",
            "audit_id",
            "event_sha256s",
            "event_count",
            "case_count",
            "repeat_indices",
        },
        "B2 repeatability journal manifest",
    )
    _require_exact(
        manifest["schema_version"],
        REPEATABILITY_JOURNAL_MANIFEST_SCHEMA_VERSION,
        "repeatability journal manifest schema",
    )
    _require_exact(manifest["audit_id"], "B2_repeatability_audit", "repeatability manifest audit")
    _require_exact(
        manifest["event_sha256s"], sorted(event_digests), "repeatability manifest event binding"
    )
    _require_exact(manifest["event_count"], 18, "repeatability manifest event count")
    _require_exact(manifest["case_count"], 6, "repeatability manifest case count")
    _require_exact(manifest["repeat_indices"], [1, 2, 3], "repeatability manifest indices")
    return passed, event_digests | {manifest_digest}


def promotion_decision(
    contract: Mapping[str, Any],
    candidate: str,
    evidence: Mapping[str, Any],
    *,
    routing_cases: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Dry-run frozen gates; amendment 01 never returns authoritative promotion evidence."""

    if canonical_sha256(contract) != EXPECTED_CONTRACT_SHA256:
        raise ValueError("promotion requires the exact amendment 01 contract")
    promotion = contract["promotion_contract"]
    _validate_promotion_contract(promotion)
    candidates = promotion["candidates"]
    if candidate not in candidates:
        raise ValueError(f"arm is not a promotion candidate: {candidate}")
    config = candidates[candidate]
    if "bound_evidence_documents" not in evidence:
        raise ValueError("promotion evidence requires a bound evidence document map")
    common_evidence = {
        "provider_qualification_journal_event_sha256",
        "provider_identity_kind",
        "provider_is_injected_test",
        "bound_evidence_documents",
    }
    expected_fields = set(common_evidence)
    for gate in config["gates"]:
        if gate["gate_type"] == "ratio":
            expected_fields.update({gate["numerator_metric"], gate["denominator_metric"]})
        elif gate["metric"] not in {
            "independent_recoverable_failure_groups",
            "B2_repeatability_cases_3_of_3",
            "B2_repeatability_required_cases",
        }:
            expected_fields.add(gate["metric"])
    if candidate.startswith("A"):
        expected_fields.update(config["required_disclosures"])
        expected_fields.update(config["required_evidence_bindings"])
    else:
        expected_fields.update(
            {"B2_repeatability_receipts", "B2_repeatability_journal_manifest_sha256"}
        )
    value = _require_fields(evidence, expected_fields, f"{candidate} promotion evidence")
    documents = value["bound_evidence_documents"]
    if not isinstance(documents, Mapping):
        raise ValueError("promotion evidence requires bound evidence documents")
    provider_event_digest = _require_lower_hex(
        value["provider_qualification_journal_event_sha256"],
        64,
        "provider qualification journal event",
    )
    _require_exact(
        value["provider_identity_kind"], "qualified_production", "provider identity kind"
    )
    _require_exact(value["provider_is_injected_test"], False, "injected test provider evidence")
    _validate_provider_qualification_document(
        _bound_evidence_document(
            documents, provider_event_digest, "provider qualification journal event"
        ),
        candidate=candidate,
        identity_kind=value["provider_identity_kind"],
        is_injected_test=value["provider_is_injected_test"],
    )
    used_document_digests = {provider_event_digest}
    if candidate.startswith("A"):
        for binding in config["required_evidence_bindings"]:
            _require_lower_hex(value[binding], 64, binding)
        prefix = candidate[:2]
        _require_decimal(value[f"{prefix}_base_schema_valid_rate"], "base schema-valid rate")
        correct_count = _require_nonnegative_int(
            value[f"{prefix}_typed_correction_correct_count"],
            f"{prefix} typed-correction correct count",
        )
        prediction_count = _require_nonnegative_int(
            value[f"{prefix}_typed_correction_prediction_count"],
            f"{prefix} typed-correction prediction count",
        )
        manifest_digest = value[f"{prefix}_typed_correction_blind_adjudication_manifest_sha256"]
        roster_digest = value[f"{prefix}_typed_correction_adjudication_roster_sha256"]
        _validate_blind_adjudication_documents(
            _bound_evidence_document(
                documents, manifest_digest, f"{prefix} blind adjudication manifest"
            ),
            _bound_evidence_document(
                documents, roster_digest, f"{prefix} blind adjudication roster"
            ),
            candidate=candidate,
            roster_digest=roster_digest,
            correct_count=correct_count,
            prediction_count=prediction_count,
        )
        used_document_digests.update({manifest_digest, roster_digest})
    roster = routing_cases
    if roster is None:
        roster = _load_json_object(DEFAULT_AMENDMENT_ROOT / "routing_case_inputs.json")
    validate_routing_cases(roster)
    derived: dict[str, Decimal | Fraction | int | None] = {}
    if candidate == "B2_typed_route_and_prompt_repair":
        recoverable = [
            case
            for case in roster["cases"]
            if case["baseline_class"] == "recoverable_baseline_failure"
        ]
        derived["independent_recoverable_failure_groups"] = len(
            {case["group_id"] for case in recoverable}
        )
        repeat_passes, repeat_document_digests = _validated_repeatability_pass_count(
            roster,
            value["B2_repeatability_receipts"],
            documents,
            value["B2_repeatability_journal_manifest_sha256"],
        )
        used_document_digests.update(repeat_document_digests)
        derived["B2_repeatability_cases_3_of_3"] = repeat_passes
        derived["B2_repeatability_required_cases"] = 6
        if not recoverable:
            for metric in (
                "conditional_B2_minus_B1_robust_completion",
                "conditional_B2_minus_B1_completion_bootstrap_ci_lower",
            ):
                if value[metric] is not None:
                    raise ValueError(
                        f"{metric} must be null when its completion denominator is zero"
                    )
                derived[metric] = None
    if set(documents) != used_document_digests:
        raise ValueError(
            "bound evidence documents must resolve exactly the declared promotion bindings"
        )
    metrics: dict[str, Decimal | Fraction | int | None] = dict(derived)
    failed_gates: list[str] = []
    gate_results: list[dict[str, Any]] = []
    for gate in config["gates"]:
        metric = gate["metric"]
        gate_type = gate["gate_type"]
        if gate_type == "conditional_threshold":
            condition_value = derived.get(gate["condition_metric"])
            condition_threshold = _require_nonnegative_int(
                gate["condition_threshold"], f"{metric} condition threshold"
            )
            if condition_value is None or not _comparison_passes(
                condition_value, gate["condition_operator"], condition_threshold
            ):
                metrics[metric] = None
                failed_gates.append(metric)
                gate_results.append(
                    {"metric": metric, "passed": False, "reason": gate["condition_false"]}
                )
                continue
        if gate_type == "ratio":
            numerator = _require_nonnegative_int(
                value[gate["numerator_metric"]], gate["numerator_metric"]
            )
            denominator = _require_nonnegative_int(
                value[gate["denominator_metric"]], gate["denominator_metric"]
            )
            if numerator > denominator:
                raise ValueError(f"{metric} numerator cannot exceed its denominator")
            if denominator == 0:
                observed: Decimal | Fraction | int | None = None
                passed = False
                reason = gate["zero_denominator"]
            else:
                observed = Fraction(numerator, denominator)
                threshold = _require_decimal(gate["threshold"], f"{metric} threshold")
                passed = _comparison_passes(
                    observed,
                    gate["operator"],
                    Fraction(threshold),
                )
                reason = None if passed else "threshold_not_met"
        else:
            if metric in derived:
                observed = derived[metric]
            else:
                raw = value[metric]
                observed = (
                    _require_decimal(raw, metric)
                    if gate["value_type"] == "decimal"
                    else _require_nonnegative_int(raw, metric)
                )
            if observed is None:
                passed = False
                reason = "metric_is_null"
            else:
                threshold = (
                    _require_decimal(gate["threshold"], f"{metric} threshold")
                    if gate["value_type"] == "decimal"
                    else _require_nonnegative_int(gate["threshold"], f"{metric} threshold")
                )
                passed = _comparison_passes(observed, gate["operator"], threshold)
                reason = None if passed else "threshold_not_met"
        metrics[metric] = observed
        if not passed:
            failed_gates.append(metric)
        gate_results.append({"metric": metric, "passed": passed, "reason": reason})
    execution_gate = contract["execution_gate"]
    global_failures: list[str] = []
    if execution_gate["current_annotation_state"] == "pending_blinded_annotation":
        global_failures.append("pending_blinded_annotation")
    if execution_gate["production_provider_state"] != "qualified":
        global_failures.append("production_provider_not_yet_qualified")
    if execution_gate["execution_authorized"] is not True:
        global_failures.append("execution_not_authorized")
    global_failures.append("authoritative_evaluation_evidence_not_bound_by_amendment_01")
    return {
        "candidate": candidate,
        "evaluation_mode": "non_authoritative_dry_run",
        "authoritative": False,
        "eligible": False,
        "metrics": {key: _serialized_metric(item) for key, item in metrics.items()},
        "gate_results": gate_results,
        "failed_gates": failed_gates,
        "global_failures": global_failures,
    }
