#!/usr/bin/env python3
"""Validate the PEARL embodied-harness paper-framing package."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
SPEC = Path("artifacts/embodied_harness/embodied_harness_spec.json")
AUDIT = Path("artifacts/embodied_harness/embodied_harness_acceptance_audit.json")
SCHEMA = Path("schemas/embodied_harness_spec.schema.json")
DOC = Path("docs/embodied_harness_thesis.md")
COMMAND_REGISTRY = Path("artifacts/openxsim/openxsim_command_registry.json")
REPORT_ROOT = Path("reports/embodied_harness")
CAUSAL_ABLATION = Path("artifacts/embodied_harness/fixed_checkpoint_rgb_ablation_v1.json")

REQUIRED_LOOP_TERMS = [
    "current harness h_t",
    "embodied task execution",
    "weakness mining",
    "clustered failure patterns",
    "harness proposal",
    "proposed edits",
    "regression test",
    "promotion decision",
    "updated harness h_{t+1}",
]
REQUIRED_SURFACES = {
    "reset",
    "observations",
    "actions",
    "tool_calls",
    "simulator_adapters",
    "safety_limits",
    "real_sim_traces",
    "validation_rules",
    "data_requirements",
    "memory",
    "rollback",
}
REQUIRED_COMPARISONS = {
    "LLM coding harnesses",
    "ASPIRE",
    "ENPIRE",
    "RoboTwin/Text2Env",
    "standard VLA evaluation",
}
REQUIRED_ROUTES = {"/gen-env", "/collect", "/evaluate", "/diagnose", "/transfer"}


def read_json(relative_path: Path | str) -> dict:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def resolve_evidence(expression: str) -> list[Path]:
    clean = expression.split("#", 1)[0].rstrip("/")
    if "*" in clean:
        return sorted(ROOT.glob(clean))
    return [ROOT / clean]


def require_evidence(expression: str, context: str) -> None:
    paths = resolve_evidence(expression)
    require(paths, f"{context}: evidence glob matched nothing: {expression}")
    for path in paths:
        require(path.exists(), f"{context}: evidence missing: {path}")
        if path.is_file():
            require(path.stat().st_size > 0, f"{context}: evidence empty: {path}")


def validate_manifest(report_root: Path) -> int:
    manifest_path = report_root / "report_manifest.json"
    require(manifest_path.exists(), "embodied-harness report manifest missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("status") == "pass_report_bundle", "embodied-harness report manifest status mismatch")
    rows = manifest.get("files", [])
    require(manifest.get("file_count") == len(rows), "embodied-harness report manifest count mismatch")
    for row in rows:
        path = report_root / row["path"]
        require(path.exists(), f"embodied-harness report file missing: {path}")
        require(path.stat().st_size == row["bytes"], f"embodied-harness report size mismatch: {path}")
        require(hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"], f"embodied-harness report hash mismatch: {path}")
    return len(rows)


def validate_causal_ablation() -> dict:
    ablation = read_json(CAUSAL_ABLATION)
    require(
        ablation.get("status") == "pass_matched_harness_ablation_candidate_promoted",
        "fixed-checkpoint harness ablation did not promote the candidate",
    )
    intervention = ablation.get("experiment", {}).get("intervention", {})
    require(intervention.get("harness_surface") == "observations.runtime_color_adapter", "harness intervention mismatch")
    require(intervention.get("baseline") == "swap_red_blue", "harness baseline adapter mismatch")
    require(intervention.get("candidate") == "identity", "harness candidate adapter mismatch")
    require(intervention.get("only_declared_difference") is True, "harness ablation has undeclared differences")
    fixed = ablation.get("experiment", {}).get("fixed_variables", {})
    require(len(fixed.get("checkpoint_sha256", "")) == 64, "harness checkpoint hash is missing")
    require(len(fixed.get("dataset_stats_sha256", "")) == 64, "harness dataset stats hash is missing")
    require(fixed.get("held_out_seeds") == [4, 5, 6], "harness ablation seed set mismatch")
    outcomes = ablation.get("outcomes", {})
    require(outcomes.get("baseline_success_count") == 0, "harness baseline outcome mismatch")
    require(outcomes.get("candidate_success_count") == 3, "harness candidate outcome mismatch")
    require(outcomes.get("episode_count_per_arm") == 3, "harness episode count mismatch")
    require(outcomes.get("success_delta") == 3, "harness success delta mismatch")
    require(outcomes.get("both_arms_execution_complete") is True, "harness arms did not both execute")
    promotion = ablation.get("promotion", {})
    require(promotion.get("decision") == "accept", "harness candidate was not accepted")
    require(all(promotion.get("gates", {}).values()), "harness promotion gate failed")
    require(promotion.get("rollback_harness_id"), "harness rollback pointer is missing")
    for key in ("baseline", "candidate"):
        report_key = f"{key}_report"
        hash_key = f"{key}_report_sha256"
        report_path = ROOT / ablation["evidence"][report_key]
        require(report_path.is_file(), f"harness {key} report is missing")
        require(
            hashlib.sha256(report_path.read_bytes()).hexdigest() == ablation["evidence"][hash_key],
            f"harness {key} report hash mismatch",
        )
    return {
        "status": "pass_fixed_checkpoint_harness_ablation",
        "baseline_success": outcomes["baseline_success_count"],
        "candidate_success": outcomes["candidate_success_count"],
        "episode_count_per_arm": outcomes["episode_count_per_arm"],
        "success_delta": outcomes["success_delta"],
        "promotion_decision": promotion["decision"],
    }


def validate_embodied_harness_package(*, require_report: bool = True) -> dict:
    schema = read_json(SCHEMA)
    spec = read_json(SPEC)
    audit = read_json(AUDIT)
    causal_ablation = validate_causal_ablation()

    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(spec, schema)
    claim = spec["paper_claim"]
    require(claim["priority_claim_status"] == "not_established", "priority claim must remain unestablished")
    require(claim["prohibited_public_claim"] == "first real embodied harness system", "prohibited priority claim changed")
    require("first" not in claim["working_claim"].lower(), "working paper claim silently asserts priority")
    require("checkpoint" in claim["attribution_control"].lower(), "attribution control does not fix the checkpoint")
    require("separate" in claim["attribution_control"].lower(), "policy retraining is not separated from harness edits")

    steps = spec["loop_steps"]
    require([row["order"] for row in steps] == list(range(1, 10)), "harness loop order must be 1-9")
    require([row["sketch_term"] for row in steps] == REQUIRED_LOOP_TERMS, "harness loop term mapping mismatch")
    for step in steps:
        for evidence in step["evidence"]:
            require_evidence(evidence, step["sketch_term"])

    surfaces = spec["embodied_surfaces"]
    require({row["surface"] for row in surfaces} == REQUIRED_SURFACES, "embodied surface set mismatch")
    require(len({row["surface"] for row in surfaces}) == len(surfaces), "embodied surfaces are duplicated")

    comparisons = spec["novelty_table"]
    require({row["comparison"] for row in comparisons} == REQUIRED_COMPARISONS, "novelty comparison set mismatch")
    require(all(row["missing_evidence"] for row in comparisons), "novelty table hides missing evidence")

    routes = spec["command_routing"]
    require({row["command"] for row in routes} == REQUIRED_ROUTES, "implementation routing command set mismatch")
    command_registry = read_json(COMMAND_REGISTRY)
    registered = {row["command"] for row in command_registry["commands"]}
    require(REQUIRED_ROUTES.issubset(registered), "routed command is missing from Open X Sim registry")
    for route in routes:
        require_evidence(route["evidence"], route["command"])
        require(set(route["harness_surfaces"]).issubset(REQUIRED_SURFACES), f"{route['command']}: unknown harness surface")

    proof_statuses = {row["status"] for row in spec["proof_obligations"]}
    require({"proven_bounded", "not_run", "not_established"}.issubset(proof_statuses), "proof obligations hide claim classes")
    bounded_rows = {
        row["claim"]: row
        for row in spec["proof_obligations"]
        if row["status"] == "proven_bounded"
    }
    require(
        {
            "implemented embodied harness artifact contract",
            "harness edits improve outcomes independently of policy changes",
            "Open X Sim supports task-semantic reuse across simulators",
            "failure memory can improve a controlled harness decision",
            "a learned policy can pass the bounded SceneAgent promotion contract",
        }.issubset(bounded_rows),
        "bounded harness proof obligations were not advanced",
    )
    real_robot_rows = [row for row in spec["proof_obligations"] if row["claim"] == "real-robot harness evolution"]
    require(len(real_robot_rows) == 1 and real_robot_rows[0]["status"] == "not_run", "real-robot proof obligation mismatch")
    priority_rows = [row for row in spec["proof_obligations"] if row["claim"] == "first real embodied harness system"]
    require(len(priority_rows) == 1 and priority_rows[0]["status"] == "not_established", "priority proof obligation mismatch")

    require(audit["status"] == "pass_embodied_harness_acceptance", "embodied-harness acceptance status mismatch")
    items = audit["items"]
    require(audit["acceptance_count"] == len(items) == 6, "embodied-harness acceptance count must be six")
    require([item["id"] for item in items] == list(range(1, 7)), "embodied-harness acceptance ids must be 1-6")
    require(all(item["status"] == "pass" for item in items), "not all embodied-harness acceptance items pass")
    for item in items:
        for evidence in item["evidence"]:
            if evidence.startswith("reports/") and not require_report:
                continue
            require_evidence(evidence, f"acceptance {item['id']}")

    doc = (ROOT / DOC).read_text(encoding="utf-8")
    for heading in (
        "## One-Page Thesis",
        "## Loop Mapping",
        "## Embodied-Specific Harness Surfaces",
        "## Novelty Table",
        "## Figure Caption And Brief",
        "## Implementation Routing",
        "## Proof Obligations",
        "## Claim Boundary",
    ):
        require(heading in doc, f"embodied-harness document missing heading: {heading}")
    require(
        "must not currently be" in doc and "first real embodied harness system" in doc,
        "document does not reject unsupported priority claim",
    )

    report_file_count = 0
    image_count = 0
    if require_report:
        report_root = ROOT / REPORT_ROOT
        for relative in ("index.html", "embodied_harness_thesis.md", "assets/embodied_harness_spec.json", "assets/embodied_harness_loop.png"):
            require_evidence(str(REPORT_ROOT / relative), "embodied-harness report")
        image_count = len(list((report_root / "assets").rglob("*.png")))
        require(image_count == audit["delivery"]["bundled_image_count"], "embodied-harness bundled image count mismatch")
        report_file_count = validate_manifest(report_root)

    return {
        "status": "pass_embodied_harness_package",
        "acceptance_items": len(items),
        "loop_steps": len(steps),
        "surfaces": len(surfaces),
        "comparisons": len(comparisons),
        "routed_commands": len(routes),
        "proof_obligations": len(spec["proof_obligations"]),
        "bundled_images": image_count,
        "report_file_count": report_file_count,
        "priority_claim": claim["priority_claim_status"],
        "causal_ablation": causal_ablation,
    }


def main() -> int:
    print(json.dumps(validate_embodied_harness_package(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
