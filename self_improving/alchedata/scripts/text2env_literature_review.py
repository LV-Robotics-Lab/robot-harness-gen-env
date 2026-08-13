#!/usr/bin/env python3
"""Validate the machine-readable Text2Env literature review package."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
SOURCE_REGISTRY = Path("artifacts/literature_review/text2env_primary_sources.json")
METHOD_MATRIX = Path("artifacts/literature_review/text2env_method_matrix.json")
ACCEPTANCE_AUDIT = Path("artifacts/literature_review/text2env_acceptance_audit.json")
SOURCE_SCHEMA = Path("schemas/text2env_literature_review.schema.json")
REVIEW_DOC = Path("docs/text2env_literature_review.md")

REQUIRED_CATEGORIES = {
    "text2task",
    "text2scene",
    "text2asset",
    "selection2env",
    "generation2env",
    "sim_in_loop_repair",
    "domain_randomization_data_generation",
    "policy_eval_data_hook",
}
REQUIRED_CAPABILITIES = {
    "task_generation",
    "asset_retrieval_generation",
    "placement_planning",
    "physics_collision_validation",
    "code_generation_repair",
    "simulator_smoke",
    "data_collection",
    "policy_evaluation",
}
REQUIRED_HANDOFF_FIELDS = {
    "task_text",
    "asset_candidates",
    "selected_assets",
    "placement_regions",
    "support_surface",
    "pose_constraints",
    "camera_observation",
    "robot_constraints",
    "success_verifier",
    "blockers",
}
REQUIRED_INNOVATION_DIMENSIONS = {
    "scene_task_decoupling",
    "open_x_sim_aggregation",
    "any_sim_transfer",
    "mcp_memory_debug_loop",
    "failure_to_data_requirement",
}


def read_json(relative_path: Path) -> dict:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def validate_review_package(*, require_report: bool = True) -> dict:
    schema = read_json(SOURCE_SCHEMA)
    registry = read_json(SOURCE_REGISTRY)
    matrix = read_json(METHOD_MATRIX)
    audit = read_json(ACCEPTANCE_AUDIT)

    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(registry, schema)

    sources = registry["sources"]
    source_ids = [source["source_id"] for source in sources]
    require(len(source_ids) == len(set(source_ids)), "primary-source ids are not unique")
    require(registry["source_count"] == len(sources), "primary-source count does not match rows")
    academic_count = sum(1 for source in sources if source["source_kind"].startswith("paper"))
    require(
        registry["academic_primary_source_count"] == academic_count,
        "academic primary-source count does not match rows",
    )
    require(academic_count >= 8, "fewer than eight academic primary sources")
    require(set(registry["required_scope_ids"]).issubset(source_ids), "required source scope is incomplete")

    categories = {category for source in sources for category in source["categories"]}
    require(REQUIRED_CATEGORIES == categories, "functional taxonomy coverage is incomplete")
    for source in sources:
        links = source["links"]
        if source["source_kind"] != "local_implementation":
            require(any(links.values()), f"{source['source_id']}: no primary link")
        for link in links.values():
            if link is not None:
                require(link.startswith("https://"), f"{source['source_id']}: non-HTTPS source link")
        code_status = source["open_status"]["code_status"]
        if code_status == "released":
            require(links["code"] is not None, f"{source['source_id']}: released code has no link")
        if code_status in {"coming_soon", "not_released", "dataset_only"}:
            require(links["code"] is None, f"{source['source_id']}: code status contradicts code link")
        require(source["reproducibility"]["evidence"], f"{source['source_id']}: missing reproducibility evidence")
        require(source["interface_relation"]["required_gates"], f"{source['source_id']}: missing interface gates")

    require(matrix["status"] == "pass_method_matrix_complete", "method matrix status mismatch")
    require(set(matrix["capabilities"]) == REQUIRED_CAPABILITIES, "method matrix capability set mismatch")
    matrix_ids = [row["source_id"] for row in matrix["rows"]]
    require(matrix_ids == source_ids, "method matrix rows are not aligned with the source registry")
    allowed_levels = set(matrix["levels"])
    for row in matrix["rows"]:
        require(set(row["scores"]) == REQUIRED_CAPABILITIES, f"{row['source_id']}: matrix columns incomplete")
        require(set(row["scores"].values()).issubset(allowed_levels), f"{row['source_id']}: invalid matrix level")

    require(audit["status"] == "pass_text2env_literature_review_acceptance", "acceptance status mismatch")
    items = audit["items"]
    require(audit["acceptance_count"] == 7, "acceptance count must be seven")
    require([item["id"] for item in items] == list(range(1, 8)), "acceptance ids must be 1-7")
    require(all(item["status"] == "pass" for item in items), "not all literature acceptance items pass")
    require(set(audit["taxonomy"]) == REQUIRED_CATEGORIES, "acceptance taxonomy is incomplete")
    require(all(audit["shortlist"].get(tier) for tier in ("P0", "P1", "P2")), "P0/P1/P2 shortlist is incomplete")
    require(
        set(audit["handoff"]["zheng_ye_produces"]["required_fields"]) == REQUIRED_HANDOFF_FIELDS,
        "Zheng Ye handoff field set mismatch",
    )
    require(
        set(audit["handoff"]["gaochen_consumes"]) == {"/collect", "/train", "/evaluate"},
        "Gaochen command handoff is incomplete",
    )
    innovation = audit["innovation_after_aspire_enpire"]
    require(
        set(innovation["distinct_hypothesis_dimensions"]) == REQUIRED_INNOVATION_DIMENSIONS,
        "post-ASPIRE/ENPIRE differentiation dimensions mismatch",
    )
    experiment_statuses = {item["status"] for item in innovation["next_experiments"]}
    require("pass" in experiment_statuses, "innovation audit lacks a passing experiment")
    require("not_run" in experiment_statuses, "innovation audit hides unrun experiments")
    require("executed_failed_promotion" in experiment_statuses, "innovation audit hides failed promotion")

    doc = (ROOT / REVIEW_DOC).read_text(encoding="utf-8")
    for heading in (
        "## Taxonomy and Command Boundary",
        "## Primary Source Registry",
        "## Method Matrix",
        "## P0/P1/P2 Decision",
        "## Zheng Ye to Gaochen Handoff",
        "## After ASPIRE and ENPIRE",
        "## Next Experiments",
        "## Claim Boundary",
    ):
        require(heading in doc, f"review document missing heading: {heading}")
    for stale_claim in (
        "Install/link RoboTwin",
        "adapter target once installed on this host",
        "Code and benchmark released per project page",
    ):
        require(stale_claim not in doc, f"review document retains stale claim: {stale_claim}")

    report_file_count = 0
    screenshot_count = 0
    if require_report:
        report_root = ROOT / "reports" / "text2env_literature_review"
        for relative_path in ("index.html", "text2env_literature_review.md", "assets/source_registry.json", "assets/method_matrix.json"):
            path = report_root / relative_path
            require(path.exists() and path.stat().st_size > 0, f"report artifact missing: {path}")
        screenshots = sorted((report_root / "assets" / "source_pages").glob("*.png"))
        screenshot_count = len(screenshots)
        require(
            screenshot_count == audit["delivery"]["source_page_screenshot_count"],
            "source-page screenshot count mismatch",
        )
        report_file_count = sum(1 for path in report_root.rglob("*") if path.is_file())

    return {
        "status": "pass_text2env_literature_review_package",
        "source_count": len(sources),
        "academic_primary_source_count": academic_count,
        "matrix_rows": len(matrix["rows"]),
        "matrix_capabilities": len(matrix["capabilities"]),
        "acceptance_items": len(items),
        "report_file_count": report_file_count,
        "source_page_screenshots": screenshot_count,
    }


def main() -> int:
    print(json.dumps(validate_review_package(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
