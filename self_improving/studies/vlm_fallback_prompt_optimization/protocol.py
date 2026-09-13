"""Frozen protocol validation and preregistered metrics for the VLM study.

This module is intentionally standard-library-only.  It never imports a model,
contacts a service, runs a simulator, or changes a harness acceptance decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

SPEC_SCHEMA_VERSION = "vlm_fallback.experiment_spec.v1"
EFFECTIVE_SPEC_SCHEMA_VERSION = "vlm_fallback.experiment_spec.v2"
STUDY_ID = "vlm-fallback-prompt-optimization-2026-08-31"
EXPERIMENT_IDS = (
    "A_visible_semantic_correction",
    "B_typed_failure_prompt_fallback",
)
SPLITS = {"train", "dev", "test"}
GOLD_VISIBLE_STATUSES = {"pass", "fail", "not_applicable", "insufficient_view"}
PREDICTED_VISIBLE_STATUSES = GOLD_VISIBLE_STATUSES | {"abstain"}


def canonical_json_bytes(value: Any) -> bytes:
    """Return the study's canonical JSON encoding."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Hash a value using the study's canonical JSON encoding."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_bundle_manifest(repo_root: Path, artifact_paths: Sequence[str]) -> list[dict[str, Any]]:
    """Build the exact manifest whose digest freezes one sample's inputs."""

    root = repo_root.expanduser().resolve(strict=True)
    manifest: list[dict[str, Any]] = []
    for raw_path in sorted(artifact_paths):
        relative = PurePosixPath(raw_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"artifact path escapes repository root: {raw_path}")
        candidate = (root / Path(*relative.parts)).resolve(strict=False)
        if not candidate.is_relative_to(root):
            raise ValueError(f"artifact path escapes repository root: {raw_path}")
        if not candidate.is_file():
            raise ValueError(f"missing artifact: {raw_path}")
        manifest.append(
            {
                "path": relative.as_posix(),
                "sha256": _sha256_file(candidate),
                "size_bytes": candidate.stat().st_size,
            }
        )
    return manifest


def build_hf_snapshot_manifest(snapshot_path: Path) -> list[dict[str, Any]]:
    """Bind a local Hugging Face snapshot to resolved blob IDs and sizes."""

    manifest: list[dict[str, Any]] = []
    for entry in sorted(snapshot_path.iterdir()):
        resolved = entry.resolve(strict=True)
        if not resolved.is_file():
            raise ValueError(f"non-file snapshot entry: {entry}")
        manifest.append(
            {
                "path": entry.name,
                "blob": resolved.name,
                "size_bytes": resolved.stat().st_size,
            }
        )
    return manifest


def _require_hex(value: Any, *, length: int, field: str) -> None:
    if not isinstance(value, str) or len(value) != length:
        raise ValueError(f"{field} must be {length} lowercase hex characters")
    try:
        int(value, 16)
    except ValueError as error:
        raise ValueError(f"{field} must be {length} lowercase hex characters") from error
    if value != value.lower():
        raise ValueError(f"{field} must be {length} lowercase hex characters")


def validate_spec(
    spec: dict[str, Any],
    *,
    repo_root: Path,
    verify_files: bool,
) -> dict[str, Any]:
    """Fail closed unless the frozen study identity and artifact bindings are exact."""

    if spec.get("schema_version") != SPEC_SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SPEC_SCHEMA_VERSION}")
    if spec.get("study_id") != STUDY_ID:
        raise ValueError(f"study_id must be {STUDY_ID}")
    if spec.get("physical_gate_authority") != "deterministic_runtime_and_typed_geometry_only":
        raise ValueError("physical_gate_authority must remain deterministic runtime and geometry")

    models = spec.get("models")
    if not isinstance(models, list) or len(models) != 2:
        raise ValueError("models must freeze exactly the local 3B and 7B candidates")
    for model in models:
        if not isinstance(model, dict) or model.get("local_files_only") is not True:
            raise ValueError("models must be mappings with local_files_only=true")
        _require_hex(model.get("revision"), length=40, field="models.revision")
        _require_hex(
            model.get("snapshot_manifest_sha256"),
            length=64,
            field="models.snapshot_manifest_sha256",
        )

    experiments = spec.get("experiments")
    if not isinstance(experiments, list):
        raise ValueError("experiments must be a list")
    experiment_ids = [item.get("experiment_id") for item in experiments if isinstance(item, dict)]
    if experiment_ids != list(EXPERIMENT_IDS):
        raise ValueError(f"experiment_ids must be {list(EXPERIMENT_IDS)} in order")

    sample_counts: dict[str, dict[str, int]] = {}
    verified_artifact_count = 0
    for experiment in experiments:
        samples = experiment.get("samples")
        if not isinstance(samples, list) or not samples:
            raise ValueError(f"{experiment['experiment_id']}.samples must be non-empty")
        declared = experiment.get("declared_sample_count")
        if declared != len(samples):
            raise ValueError(
                f"{experiment['experiment_id']}.declared_sample_count does not match samples"
            )
        case_ids = [sample.get("case_id") for sample in samples if isinstance(sample, dict)]
        if len(case_ids) != len(samples) or len(set(case_ids)) != len(case_ids):
            raise ValueError(f"{experiment['experiment_id']} has duplicate case_id values")
        counts = {split: 0 for split in ("train", "dev", "test")}
        for sample in samples:
            split = sample.get("split")
            if split not in SPLITS:
                raise ValueError(f"{sample.get('case_id')}.split must be train, dev, or test")
            counts[split] += 1
            if not isinstance(sample.get("group_id"), str) or not sample["group_id"]:
                raise ValueError(f"{sample.get('case_id')}.group_id must be non-empty")
            artifacts = sample.get("artifacts")
            if not isinstance(artifacts, list) or not artifacts:
                raise ValueError(f"{sample.get('case_id')}.artifacts must be non-empty")
            _require_hex(sample.get("bundle_sha256"), length=64, field="bundle_sha256")
            if verify_files:
                observed = build_bundle_manifest(repo_root, artifacts)
                observed_digest = canonical_sha256(observed)
                if observed_digest != sample["bundle_sha256"]:
                    raise ValueError(
                        f"{sample['case_id']}.bundle_sha256 mismatch: "
                        f"expected {sample['bundle_sha256']}, observed {observed_digest}"
                    )
                verified_artifact_count += len(observed)
        sample_counts[experiment["experiment_id"]] = counts

    return {
        "study_id": spec["study_id"],
        "experiment_ids": list(EXPERIMENT_IDS),
        "sample_counts": sample_counts,
        "verified_artifact_count": verified_artifact_count,
    }


def validate_effective_spec_v2(
    spec: dict[str, Any],
    *,
    repo_root: Path,
    verify_files: bool,
) -> dict[str, Any]:
    """Validate the full amendment-01 spec while preserving the v1 sample contract.

    The fixed amendment-bundle loader owns byte authority.  This validator owns
    the execution-facing shape: the complete v1 experiment roster must still be
    valid and each model must carry a content manifest that is required in
    invocation receipts.
    """

    if not isinstance(spec, dict) or spec.get("schema_version") != (EFFECTIVE_SPEC_SCHEMA_VERSION):
        raise ValueError(f"schema_version must be {EFFECTIVE_SPEC_SCHEMA_VERSION}")
    if spec.get("status") != "preregistered_pre_execution":
        raise ValueError("effective spec status must remain preregistered_pre_execution")
    amendment_value = spec.get("amendment")
    if not isinstance(amendment_value, dict) or amendment_value.get("amendment_id") != "01":
        raise ValueError("effective spec must bind amendment 01")

    content_definition = spec.get("content_manifest_digest_definition")
    if (
        not isinstance(content_definition, dict)
        or content_definition.get("schema_version") != "vlm_fallback.model_content_manifest.v2"
        or content_definition.get("authority")
        != "required normative model identity for v2 execution and receipts"
    ):
        raise ValueError("effective spec model content digest definition mismatch")

    models = spec.get("models")
    if not isinstance(models, list) or len(models) != 2:
        raise ValueError("models must freeze exactly the local 3B and 7B candidates")
    model_roles: set[str] = set()
    for model in models:
        if not isinstance(model, dict):
            raise ValueError("models must be mappings")
        role = model.get("role")
        if not isinstance(role, str) or not role or role in model_roles:
            raise ValueError("models must use unique non-empty roles")
        model_roles.add(role)
        content_manifest = model.get("content_manifest")
        if (
            not isinstance(content_manifest, dict)
            or set(content_manifest) != {"path", "schema_version", "canonical_sha256"}
            or content_manifest.get("schema_version") != "vlm_fallback.model_content_manifest.v2"
        ):
            raise ValueError(f"model content manifest binding mismatch for role: {role}")
        path = content_manifest.get("path")
        if (
            not isinstance(path, str)
            or PurePosixPath(path).is_absolute()
            or ".." in PurePosixPath(path).parts
        ):
            raise ValueError(f"model content manifest path is unsafe for role: {role}")
        _require_hex(
            content_manifest.get("canonical_sha256"),
            length=64,
            field=f"models.{role}.content_manifest.canonical_sha256",
        )

    logging_contract = spec.get("logging_contract")
    receipt_fields = (
        logging_contract.get("invocation_receipt_fields")
        if isinstance(logging_contract, dict)
        else None
    )
    if (
        not isinstance(receipt_fields, list)
        or receipt_fields.count("model_content_manifest_sha256") != 1
        or receipt_fields.count("model_roster_sha256") != 1
    ):
        raise ValueError("effective spec receipts must bind model content manifest and roster")

    # Reuse the established v1 roster/artifact validator on a detached
    # projection.  Amendment-only fields cannot weaken those checks.
    legacy = json.loads(canonical_json_bytes(spec))
    legacy["schema_version"] = SPEC_SCHEMA_VERSION
    legacy.pop("amendment", None)
    legacy.pop("content_manifest_digest_definition", None)
    for model in legacy["models"]:
        model.pop("content_manifest", None)
    summary = validate_spec(legacy, repo_root=repo_root, verify_files=verify_files)
    return {
        **summary,
        "amendment_id": "01",
        "model_content_manifest_count": len(models),
    }


def load_spec(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("experiment spec must be a JSON object")
    return value


def visible_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Score visible checks; unobservable gold requires abstention and is not scorable."""

    if not rows:
        raise ValueError("visible metrics require at least one row")
    case_ids: list[str] = []
    group_ids: list[str] = []
    for row in rows:
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("visible metric rows require a non-empty case_id")
        case_ids.append(case_id)
        group_id = row.get("group_id")
        if not isinstance(group_id, str) or not group_id:
            raise ValueError("visible metric rows require a non-empty group_id")
        group_ids.append(group_id)
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("visible metric rows require a unique case_id per row")
    group_sizes: dict[str, int] = defaultdict(int)
    for group_id in group_ids:
        group_sizes[group_id] += 1
    counters: dict[str, dict[str, float]] = defaultdict(
        lambda: {"tp": 0, "fp": 0, "fn": 0, "eligible": 0}
    )
    eligible = covered = correct = unsafe_passes = 0.0
    insufficient = insufficient_abstains = insufficient_non_abstains = 0.0
    insufficient_overclaims = 0.0
    for row, group_id in zip(rows, group_ids):
        weight = 1.0 / group_sizes[group_id]
        gold = row["gold"]
        prediction = row["prediction"]
        if set(gold) != set(prediction):
            raise ValueError(f"{row.get('case_id')} gold and prediction must have the same checks")
        for check_name, gold_status in gold.items():
            predicted_status = prediction[check_name]
            if gold_status not in GOLD_VISIBLE_STATUSES:
                raise ValueError(f"invalid gold status for {check_name}: {gold_status}")
            if predicted_status not in PREDICTED_VISIBLE_STATUSES:
                raise ValueError(f"invalid prediction status for {check_name}: {predicted_status}")
            if gold_status == "insufficient_view":
                insufficient += weight
                insufficient_abstains += weight * int(predicted_status == "abstain")
                insufficient_non_abstains += weight * int(predicted_status != "abstain")
                insufficient_overclaims += weight * int(predicted_status == "pass")
                unsafe_passes += weight * int(predicted_status == "pass")
                continue
            if gold_status == "not_applicable":
                continue
            eligible += weight
            counters[check_name]["eligible"] += weight
            predicted_decision = predicted_status in {"pass", "fail"}
            if predicted_decision:
                covered += weight
                correct += weight * int(predicted_status == gold_status)
            unsafe_passes += weight * int(gold_status == "fail" and predicted_status == "pass")
            counters[check_name]["tp"] += weight * int(
                gold_status == "fail" and predicted_status == "fail"
            )
            counters[check_name]["fp"] += weight * int(
                gold_status == "pass" and predicted_status == "fail"
            )
            counters[check_name]["fn"] += weight * int(
                gold_status == "fail" and predicted_status != "fail"
            )

    per_check: dict[str, dict[str, float | int]] = {}
    f1_values: list[float] = []
    for check_name in sorted(counters):
        counts = counters[check_name]
        precision_denominator = counts["tp"] + counts["fp"]
        recall_denominator = counts["tp"] + counts["fn"]
        precision = counts["tp"] / precision_denominator if precision_denominator else 0.0
        recall = counts["tp"] / recall_denominator if recall_denominator else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_check[check_name] = {
            "eligible": counts["eligible"],
            "precision_fail": precision,
            "recall_fail": recall,
            "f1_fail": f1,
        }
        if recall_denominator:
            f1_values.append(f1)
    return {
        "group_count": len(group_sizes),
        "eligible_decisions": eligible,
        "covered_decisions": covered,
        "coverage": covered / eligible if eligible else 0.0,
        "selective_accuracy": correct / covered if covered else 0.0,
        "unsafe_visible_pass_count": unsafe_passes,
        "insufficient_view_count": insufficient,
        "insufficient_view_abstain_count": insufficient_abstains,
        "insufficient_view_non_abstain_count": insufficient_non_abstains,
        "insufficient_view_overclaim_count": insufficient_overclaims,
        "macro_f1_fail": sum(f1_values) / len(f1_values) if f1_values else 0.0,
        "per_check": per_check,
    }


def routing_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Keep robust completion, correct blockers, and unsafe publication distinct."""

    if not rows:
        raise ValueError("routing metrics require at least one row")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    case_ids: list[str] = []
    for row in rows:
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("routing metric rows require a non-empty case_id")
        case_ids.append(case_id)
        group_id = row.get("group_id")
        if not isinstance(group_id, str) or not group_id:
            raise ValueError("routing metric rows require a non-empty group_id")
        grouped[group_id].append(row)
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("routing metric rows require a unique case_id per row")
    recoverable = [row for row in rows if row["recoverable"]]
    unrecoverable = [row for row in rows if not row["recoverable"]]
    group_route_accuracy = [
        sum(row["gold_route"] == row["predicted_route"] for row in group_rows) / len(group_rows)
        for group_rows in grouped.values()
    ]
    safe_abstentions = sum(
        row["gold_route"] == row["predicted_route"]
        and str(row["predicted_route"]).startswith("abstain_")
        for row in unrecoverable
    )
    return {
        "group_count": len(grouped),
        "case_count": len(rows),
        "route_accuracy": sum(group_route_accuracy) / len(group_route_accuracy),
        "recoverable_case_count": len(recoverable),
        "robust_completion_rate": (
            sum(bool(row["robust_completion"]) for row in recoverable) / len(recoverable)
            if recoverable
            else 0.0
        ),
        "unrecoverable_case_count": len(unrecoverable),
        "safe_abstention_rate": (safe_abstentions / len(unrecoverable) if unrecoverable else 0.0),
        "unsafe_publication_count": sum(bool(row["unsafe_publication"]) for row in rows),
    }


def _percentile(sorted_values: Sequence[float], probability: float) -> float:
    index = probability * (len(sorted_values) - 1)
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return sorted_values[lower]
    weight = index - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def paired_cluster_bootstrap_delta(
    rows: Sequence[dict[str, Any]],
    *,
    resamples: int,
    seed: int,
) -> dict[str, Any]:
    """Paired percentile bootstrap over groups, not correlated seed rows."""

    if not rows:
        raise ValueError("paired cluster bootstrap requires at least one row")
    if resamples < 1:
        raise ValueError("resamples must be at least one")
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        grouped[str(row["group_id"])].append(float(row["candidate"]) - float(row["baseline"]))
    group_deltas = [sum(values) / len(values) for _, values in sorted(grouped.items())]
    point_delta = sum(group_deltas) / len(group_deltas)
    generator = random.Random(seed)
    distribution = sorted(
        sum(generator.choice(group_deltas) for _ in group_deltas) / len(group_deltas)
        for _ in range(resamples)
    )
    return {
        "groups": len(group_deltas),
        "resamples": resamples,
        "seed": seed,
        "point_delta": point_delta,
        "ci_lower": _percentile(distribution, 0.025),
        "ci_upper": _percentile(distribution, 0.975),
        "distribution_sha256": canonical_sha256(distribution),
    }


def exact_mcnemar(
    baseline_correct: Sequence[bool],
    candidate_correct: Sequence[bool],
) -> dict[str, Any]:
    """Return the exact two-sided McNemar test for paired binary decisions."""

    if not baseline_correct or len(baseline_correct) != len(candidate_correct):
        raise ValueError("McNemar inputs must have the same non-zero length")
    baseline_only = sum(
        bool(baseline) and not bool(candidate)
        for baseline, candidate in zip(baseline_correct, candidate_correct)
    )
    candidate_only = sum(
        not bool(baseline) and bool(candidate)
        for baseline, candidate in zip(baseline_correct, candidate_correct)
    )
    discordant = baseline_only + candidate_only
    if discordant:
        tail = sum(
            math.comb(discordant, index) for index in range(min(baseline_only, candidate_only) + 1)
        ) / (2**discordant)
        p_value = min(1.0, 2.0 * tail)
    else:
        p_value = 1.0
    return {
        "baseline_only_correct": baseline_only,
        "candidate_only_correct": candidate_only,
        "discordant_pairs": discordant,
        "two_sided_exact_p": p_value,
    }


def inventory_summary(spec: dict[str, Any]) -> dict[str, Any]:
    model_receipts = []
    for model in spec["models"]:
        snapshot_path = Path(model["local_snapshot_path"]).expanduser()
        available = snapshot_path.is_dir()
        observed_digest = (
            canonical_sha256(build_hf_snapshot_manifest(snapshot_path)) if available else None
        )
        model_receipts.append(
            {
                "model_id": model["model_id"],
                "revision": model["revision"],
                "snapshot_manifest_sha256": model["snapshot_manifest_sha256"],
                "observed_snapshot_manifest_sha256": observed_digest,
                "available": available,
                "identity_match": available
                and observed_digest == model["snapshot_manifest_sha256"],
            }
        )
    return {
        "study_id": spec["study_id"],
        "expensive_execution_started": False,
        "models": model_receipts,
        "experiments": [
            {
                "experiment_id": experiment["experiment_id"],
                "sample_count": len(experiment["samples"]),
            }
            for experiment in spec["experiments"]
        ],
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("spec", type=Path)
    validate_parser.add_argument("--repo-root", type=Path, required=True)
    inventory_parser = subparsers.add_parser("inventory")
    inventory_parser.add_argument("spec", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)

    spec = load_spec(args.spec)
    if args.command == "validate":
        result = validate_spec(spec, repo_root=args.repo_root, verify_files=True)
    else:
        result = inventory_summary(spec)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
