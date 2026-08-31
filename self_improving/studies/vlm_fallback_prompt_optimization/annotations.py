"""Blinded annotation packaging and sealing for the frozen VLM study.

The public packs expose only task intent and image bytes.  Case identifiers,
source paths, split names, runtime evidence, and rater answers remain in
authenticated private mappings or rating files supplied explicitly by the
operator.  This module never runs a model or opens sealed test gold.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from self_improving.studies.vlm_fallback_prompt_optimization import protocol

ASSIGNMENT_SCHEMA = "vlm_fallback.blinded_assignment.v1"
RATING_SCHEMA = "vlm_fallback.blinded_rating.v1"
PRIVATE_MAP_SCHEMA = "vlm_fallback.private_assignment_map.v1"
ADJUDICATION_MAP_SCHEMA = "vlm_fallback.private_adjudication_map.v1"
TRAIN_GOLD_SCHEMA = "vlm_fallback.visible_train_gold.v1"
DEV_GOLD_SCHEMA = "vlm_fallback.visible_dev_gold.v1"
TEST_PAYLOAD_SCHEMA = "vlm_fallback.test_annotation_payload.v1"
GOLD_SEAL_SCHEMA = "vlm_fallback.visible_gold_seal.v1"
SEALED_MANIFEST_SCHEMA = "vlm_fallback.sealed_test_annotation_manifest.v2"
FROZEN_SPEC_SHA256 = "ad19d38204f20c42d8070785994fe749b8db41364e002e382e938cb92ae5bf6c"
STUDY_ID = "vlm-fallback-prompt-optimization-2026-08-31"
RATER_SLOTS = ("rater_1", "rater_2")
IMAGE_SUFFIXES = frozenset({".bmp", ".jpeg", ".jpg", ".png", ".webp"})


class AnnotationIntegrityError(RuntimeError):
    """Raised when blinding, response completeness, or binding is invalid."""


@dataclass(frozen=True)
class ExportReceipt:
    case_count: int
    private_mapping_sha256: str
    assignment_sha256: Mapping[str, str]


@dataclass(frozen=True)
class AdjudicationReceipt:
    disagreement_count: int
    disputed_case_count: int
    assignment_sha256: str


@dataclass(frozen=True)
class SealReceipt:
    disagreement_count: int
    dev_gold_manifest_sha256: str
    test_annotation_payload_sha256: str
    annotation_manifest_sha256: str
    gold_seal_sha256: str


def canonical_json_bytes(value: Any) -> bytes:
    return protocol.canonical_json_bytes(value)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _tool_source_sha256() -> str:
    try:
        return sha256_bytes(Path(__file__).resolve(strict=True).read_bytes())
    except OSError as error:
        raise AnnotationIntegrityError("annotation tool source cannot be hashed") from error


def _require_blind_key(blind_key: bytes) -> None:
    if not isinstance(blind_key, bytes) or len(blind_key) < 32:
        raise AnnotationIntegrityError("blind key must contain at least 32 bytes")


def _blind_digest(blind_key: bytes, *parts: str) -> str:
    payload = "\0".join(parts).encode("utf-8")
    return hmac.new(blind_key, payload, hashlib.sha256).hexdigest()


def _canonical_file(path: Path, value: Any, *, private: bool = False) -> str:
    raw = canonical_json_bytes(value) + b"\n"
    path.write_bytes(raw)
    if private:
        path.chmod(0o600)
    return sha256_bytes(raw)


def _load_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except OSError as error:
        raise AnnotationIntegrityError(f"{label} cannot be read") from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnnotationIntegrityError(f"{label} is not valid JSON") from error
    if not isinstance(value, dict):
        raise AnnotationIntegrityError(f"{label} must be an object")
    return value, raw


def _annotation_contract_digests(spec: Mapping[str, Any]) -> dict[str, str]:
    experiment = _visible_experiment(spec)
    gold = experiment["gold_annotation"]
    return {
        "label_contract_sha256": protocol.canonical_sha256(
            {
                "visible_checks": experiment["visible_checks"],
                "statuses": gold["statuses"],
                "insufficient_view_maps_to": gold["insufficient_view_maps_to"],
            }
        ),
        "rater_contract_sha256": protocol.canonical_sha256(
            {"raters": gold["raters"], "blinded_to": gold["blinded_to"]}
        ),
        "adjudication_contract_sha256": protocol.canonical_sha256(
            {
                "adjudication": gold["adjudication"],
                "agreement_report": gold["agreement_report"],
            }
        ),
    }


def _visible_experiment(spec: Mapping[str, Any]) -> Mapping[str, Any]:
    experiments = spec.get("experiments")
    if not isinstance(experiments, list):
        raise AnnotationIntegrityError("spec experiments are missing")
    matches = [
        item
        for item in experiments
        if isinstance(item, Mapping)
        and item.get("experiment_id") == "A_visible_semantic_correction"
    ]
    if len(matches) != 1:
        raise AnnotationIntegrityError("visible annotation experiment must be unique")
    return matches[0]


def _load_verified_spec(spec_path: Path, repo_root: Path) -> tuple[dict[str, Any], str]:
    try:
        root = repo_root.expanduser().resolve(strict=True)
        configured = spec_path.expanduser().resolve(strict=True)
    except OSError as error:
        raise AnnotationIntegrityError(
            "frozen spec or repository root cannot be resolved"
        ) from error
    expected = (
        root / "self_improving/studies/vlm_fallback_prompt_optimization/experiment_spec.json"
    ).resolve(strict=False)
    if configured != expected:
        raise AnnotationIntegrityError("annotation export requires the checked-in frozen spec")
    raw = configured.read_bytes()
    observed_sha = sha256_bytes(raw)
    if observed_sha != FROZEN_SPEC_SHA256:
        raise AnnotationIntegrityError("frozen annotation spec digest mismatch")
    spec = protocol.load_spec(configured)
    protocol.validate_spec(spec, repo_root=root, verify_files=True)
    return spec, observed_sha


def _safe_source(root: Path, relative_path: str) -> Path:
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise AnnotationIntegrityError("annotation artifact path is unsafe")
    try:
        candidate = (root / Path(*relative.parts)).resolve(strict=True)
    except OSError as error:
        raise AnnotationIntegrityError("annotation image artifact is missing") from error
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise AnnotationIntegrityError("annotation artifact escapes repository")
    return candidate


def _ensure_new_outputs(public_root: Path, private_map_path: Path) -> None:
    public = public_root.expanduser().resolve(strict=False)
    private = private_map_path.expanduser().resolve(strict=False)
    if private.is_relative_to(public):
        raise AnnotationIntegrityError(
            "private mapping cannot be inside the public assignment tree"
        )
    if public_root.exists():
        raise AnnotationIntegrityError("public assignment root already exists")
    if private_map_path.exists():
        raise AnnotationIntegrityError("private mapping already exists")


def _assignment_directory(slot: str) -> str:
    return slot.replace("_", "-")


def _authenticated_mapping(mapping: dict[str, Any], blind_key: bytes) -> dict[str, Any]:
    value = dict(mapping)
    value["mapping_hmac_sha256"] = hmac.new(
        blind_key,
        canonical_json_bytes(mapping),
        hashlib.sha256,
    ).hexdigest()
    return value


def _load_authenticated_mapping(
    path: Path,
    blind_key: bytes,
    *,
    schema: str,
) -> tuple[dict[str, Any], bytes]:
    _require_blind_key(blind_key)
    mapping, raw = _load_json(path, label="private mapping")
    if mapping.get("schema_version") != schema:
        raise AnnotationIntegrityError("private mapping schema mismatch")
    if mapping.get("blind_key_sha256") != sha256_bytes(blind_key):
        raise AnnotationIntegrityError("private mapping blind key mismatch")
    if mapping.get("tool_source_sha256") != _tool_source_sha256():
        raise AnnotationIntegrityError("annotation tool source digest mismatch")
    supplied = mapping.get("mapping_hmac_sha256")
    unsigned = dict(mapping)
    unsigned.pop("mapping_hmac_sha256", None)
    expected = hmac.new(
        blind_key,
        canonical_json_bytes(unsigned),
        hashlib.sha256,
    ).hexdigest()
    if not isinstance(supplied, str) or not hmac.compare_digest(supplied, expected):
        raise AnnotationIntegrityError("private mapping authentication failed")
    return mapping, raw


def export_blinded_assignments(
    *,
    spec_path: Path,
    repo_root: Path,
    public_root: Path,
    private_map_path: Path,
    blind_key: bytes,
) -> ExportReceipt:
    """Export two independently pseudonymized packs for all visible samples."""

    _require_blind_key(blind_key)
    _ensure_new_outputs(public_root, private_map_path)
    spec, spec_sha256 = _load_verified_spec(spec_path, repo_root)
    experiment = _visible_experiment(spec)
    checks = tuple(experiment.get("visible_checks", ()))
    statuses = tuple(experiment.get("gold_annotation", {}).get("statuses", ()))
    if len(checks) != 8 or len(set(checks)) != len(checks):
        raise AnnotationIntegrityError("visible annotation check contract is invalid")
    if set(statuses) != {"pass", "fail", "not_applicable", "insufficient_view"}:
        raise AnnotationIntegrityError("visible annotation status contract is invalid")
    root = repo_root.expanduser().resolve(strict=True)
    planned: list[dict[str, Any]] = []
    for sample in experiment.get("samples", []):
        if not isinstance(sample, Mapping):
            raise AnnotationIntegrityError("visible annotation sample is invalid")
        case_id = sample.get("case_id")
        split = sample.get("split")
        task_context = sample.get("task_context")
        if (
            not isinstance(case_id, str)
            or split not in {"train", "dev", "test"}
            or not isinstance(task_context, str)
            or not task_context
        ):
            raise AnnotationIntegrityError("visible annotation sample identity is invalid")
        sources = []
        for raw_path in sample.get("artifacts", []):
            if not isinstance(raw_path, str) or Path(raw_path).suffix.lower() not in IMAGE_SUFFIXES:
                continue
            path = _safe_source(root, raw_path)
            data = path.read_bytes()
            sources.append({"path": raw_path, "suffix": path.suffix.lower(), "data": data})
        if not sources:
            raise AnnotationIntegrityError(f"annotation sample has no image views: {case_id}")
        planned.append(
            {
                "case_id": case_id,
                "split": split,
                "task_context": task_context,
                "sources": sources,
            }
        )
    if len({item["case_id"] for item in planned}) != len(planned):
        raise AnnotationIntegrityError("visible annotation case ids must be unique")

    public_root.mkdir(parents=True, mode=0o755)
    contracts = _annotation_contract_digests(spec)
    tool_source_sha256 = _tool_source_sha256()
    assignment_digests: dict[str, str] = {}
    mapping_entries: list[dict[str, Any]] = []
    by_case = {item["case_id"]: item for item in planned}
    item_ids_by_slot: dict[str, dict[str, str]] = {}
    for slot in RATER_SLOTS:
        item_ids_by_slot[slot] = {
            case_id: f"item-{_blind_digest(blind_key, STUDY_ID, slot, case_id)[:24]}"
            for case_id in by_case
        }
        slot_root = public_root / _assignment_directory(slot)
        media_root = slot_root / "media"
        media_root.mkdir(parents=True)
        items = []
        for case_id, item_id in sorted(item_ids_by_slot[slot].items(), key=lambda pair: pair[1]):
            sample = by_case[case_id]
            views = []
            target_root = media_root / item_id
            target_root.mkdir()
            for index, source in enumerate(sample["sources"], start=1):
                relative = f"media/{item_id}/view-{index:02d}{source['suffix']}"
                target = slot_root / relative
                target.write_bytes(source["data"])
                views.append(
                    {
                        "path": relative,
                        "sha256": sha256_bytes(source["data"]),
                        "size_bytes": len(source["data"]),
                    }
                )
            items.append(
                {
                    "item_id": item_id,
                    "task_context": sample["task_context"],
                    "checks": list(checks),
                    "views": views,
                }
            )
        assignment_id = _blind_digest(blind_key, STUDY_ID, slot, "assignment")
        assignment = {
            "schema_version": ASSIGNMENT_SCHEMA,
            "study_id": spec["study_id"],
            "spec_sha256": spec_sha256,
            "tool_source_sha256": tool_source_sha256,
            "assignment_id": assignment_id,
            "rater_slot": slot,
            "label_contract_sha256": contracts["label_contract_sha256"],
            "allowed_statuses": list(statuses),
            "blinded_to": list(experiment["gold_annotation"]["blinded_to"]),
            "instructions": (
                "Judge only what is visible in the supplied images and task intent. "
                "Use insufficient_view whenever a check cannot be observed."
            ),
            "items": items,
        }
        assignment_digests[slot] = _canonical_file(slot_root / "assignment.json", assignment)
        template = {
            "schema_version": RATING_SCHEMA,
            "assignment_sha256": assignment_digests[slot],
            "rater_slot": slot,
            "rows": [
                {
                    "item_id": item["item_id"],
                    "labels": {check: None for check in item["checks"]},
                }
                for item in items
            ],
        }
        _canonical_file(slot_root / "ratings.template.json", template)

    for sample in sorted(planned, key=lambda item: item["case_id"]):
        mapping_entries.append(
            {
                "case_id": sample["case_id"],
                "split": sample["split"],
                "task_context": sample["task_context"],
                "item_ids": {
                    slot: item_ids_by_slot[slot][sample["case_id"]] for slot in RATER_SLOTS
                },
                "source_views": [
                    {
                        "path": source["path"],
                        "sha256": sha256_bytes(source["data"]),
                        "size_bytes": len(source["data"]),
                    }
                    for source in sample["sources"]
                ],
            }
        )
    unsigned_mapping = {
        "schema_version": PRIVATE_MAP_SCHEMA,
        "study_id": spec["study_id"],
        "spec_sha256": spec_sha256,
        "blind_key_sha256": sha256_bytes(blind_key),
        "tool_source_sha256": tool_source_sha256,
        **contracts,
        "checks": list(checks),
        "statuses": list(statuses),
        "public_assignments": assignment_digests,
        "entries": mapping_entries,
    }
    mapping = _authenticated_mapping(unsigned_mapping, blind_key)
    private_map_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    mapping_sha = _canonical_file(private_map_path, mapping, private=True)
    return ExportReceipt(len(planned), mapping_sha, assignment_digests)


def _load_assignment(
    *, public_root: Path, slot: str, expected_sha256: str
) -> tuple[dict[str, Any], bytes]:
    assignment_path = public_root / _assignment_directory(slot) / "assignment.json"
    assignment, raw = _load_json(assignment_path, label=f"{slot} assignment")
    if sha256_bytes(raw) != expected_sha256:
        raise AnnotationIntegrityError("assignment digest mismatch")
    if (
        assignment.get("schema_version") != ASSIGNMENT_SCHEMA
        or assignment.get("rater_slot") != slot
    ):
        raise AnnotationIntegrityError("assignment contract mismatch")
    return assignment, raw


def _validate_rating(
    rating: Mapping[str, Any],
    *,
    slot: str,
    assignment_sha256: str,
    expected_checks: Mapping[str, Sequence[str]],
    statuses: set[str],
) -> dict[str, dict[str, str]]:
    if set(rating) != {"schema_version", "assignment_sha256", "rater_slot", "rows"}:
        raise AnnotationIntegrityError("rating top-level contract mismatch")
    if rating.get("schema_version") != RATING_SCHEMA:
        raise AnnotationIntegrityError("rating schema mismatch")
    if rating.get("rater_slot") != slot:
        raise AnnotationIntegrityError("rating rater slot mismatch")
    if rating.get("assignment_sha256") != assignment_sha256:
        raise AnnotationIntegrityError("rating assignment digest mismatch")
    rows = rating.get("rows")
    if not isinstance(rows, list):
        raise AnnotationIntegrityError("rating rows must be complete")
    item_ids = [row.get("item_id") for row in rows if isinstance(row, Mapping)]
    if len(item_ids) != len(rows) or len(set(item_ids)) != len(item_ids):
        raise AnnotationIntegrityError("rating contains a duplicate item")
    if set(item_ids) != set(expected_checks) or len(rows) != len(expected_checks):
        raise AnnotationIntegrityError("rating rows are not complete")
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        if set(row) != {"item_id", "labels"} or not isinstance(row["labels"], Mapping):
            raise AnnotationIntegrityError("rating row contract mismatch")
        item_id = row["item_id"]
        labels = row["labels"]
        if set(labels) != set(expected_checks[item_id]):
            raise AnnotationIntegrityError("rating checks are not complete")
        if any(type(value) is not str or value not in statuses for value in labels.values()):
            raise AnnotationIntegrityError("rating status is invalid")
        result[item_id] = dict(labels)
    return result


def _load_bound_rating_record(
    *,
    private_map_path: Path,
    public_root: Path,
    rating_path: Path,
    rater_slot: str,
    blind_key: bytes,
) -> tuple[dict[str, dict[str, str]], str]:
    if rater_slot not in RATER_SLOTS:
        raise AnnotationIntegrityError("rating rater slot is invalid")
    mapping, _ = _load_authenticated_mapping(private_map_path, blind_key, schema=PRIVATE_MAP_SCHEMA)
    assignment_sha = mapping.get("public_assignments", {}).get(rater_slot)
    if not isinstance(assignment_sha, str):
        raise AnnotationIntegrityError("private mapping lacks assignment digest")
    assignment, _ = _load_assignment(
        public_root=public_root,
        slot=rater_slot,
        expected_sha256=assignment_sha,
    )
    expected_checks = {
        item["item_id"]: tuple(item["checks"])
        for item in assignment.get("items", [])
        if isinstance(item, Mapping)
    }
    rating, rating_raw = _load_json(rating_path, label=f"{rater_slot} ratings")
    by_item = _validate_rating(
        rating,
        slot=rater_slot,
        assignment_sha256=assignment_sha,
        expected_checks=expected_checks,
        statuses=set(mapping["statuses"]),
    )
    item_to_case = {entry["item_ids"][rater_slot]: entry["case_id"] for entry in mapping["entries"]}
    if set(by_item) != set(item_to_case):
        raise AnnotationIntegrityError("rating item mapping is incomplete")
    return (
        {item_to_case[item_id]: labels for item_id, labels in by_item.items()},
        sha256_bytes(rating_raw),
    )


def load_bound_ratings(
    *,
    private_map_path: Path,
    public_root: Path,
    rating_path: Path,
    rater_slot: str,
    blind_key: bytes,
) -> dict[str, dict[str, str]]:
    """Validate one completed rating file and return labels keyed by case id."""

    labels, _ = _load_bound_rating_record(
        private_map_path=private_map_path,
        public_root=public_root,
        rating_path=rating_path,
        rater_slot=rater_slot,
        blind_key=blind_key,
    )
    return labels


def _case_disagreements(
    first: Mapping[str, Mapping[str, str]],
    second: Mapping[str, Mapping[str, str]],
    checks: Sequence[str],
) -> dict[str, list[str]]:
    if set(first) != set(second):
        raise AnnotationIntegrityError("rater case sets differ")
    return {
        case_id: [check for check in checks if first[case_id][check] != second[case_id][check]]
        for case_id in first
        if any(first[case_id][check] != second[case_id][check] for check in checks)
    }


def _copy_bound_views(
    *, source_root: Path, target_root: Path, views: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    copied = []
    for index, view in enumerate(views, start=1):
        raw_path = view.get("path")
        if not isinstance(raw_path, str):
            raise AnnotationIntegrityError("assignment view path is invalid")
        relative = PurePosixPath(raw_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise AnnotationIntegrityError("assignment view path is unsafe")
        source = (source_root / Path(*relative.parts)).resolve(strict=True)
        root = source_root.resolve(strict=True)
        if not source.is_relative_to(root) or not source.is_file():
            raise AnnotationIntegrityError("assignment view escapes public pack")
        data = source.read_bytes()
        if sha256_bytes(data) != view.get("sha256") or len(data) != view.get("size_bytes"):
            raise AnnotationIntegrityError("assignment view digest mismatch")
        suffix = source.suffix.lower()
        relative_target = f"media/view-{index:02d}{suffix}"
        target = target_root / relative_target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        copied.append(
            {
                "path": relative_target,
                "sha256": sha256_bytes(data),
                "size_bytes": len(data),
            }
        )
    return copied


def prepare_adjudication(
    *,
    private_map_path: Path,
    public_root: Path,
    rater_1_rating_path: Path,
    rater_2_rating_path: Path,
    adjudication_root: Path,
    adjudication_map_path: Path,
    blind_key: bytes,
) -> AdjudicationReceipt:
    """Export a third-rater pack containing only disputed checks, never answers."""

    _ensure_new_outputs(adjudication_root, adjudication_map_path)
    mapping, mapping_raw = _load_authenticated_mapping(
        private_map_path, blind_key, schema=PRIVATE_MAP_SCHEMA
    )
    first, first_rating_sha = _load_bound_rating_record(
        private_map_path=private_map_path,
        public_root=public_root,
        rating_path=rater_1_rating_path,
        rater_slot="rater_1",
        blind_key=blind_key,
    )
    second, second_rating_sha = _load_bound_rating_record(
        private_map_path=private_map_path,
        public_root=public_root,
        rating_path=rater_2_rating_path,
        rater_slot="rater_2",
        blind_key=blind_key,
    )
    checks = tuple(mapping["checks"])
    disagreements = _case_disagreements(first, second, checks)
    rater_1_assignment, _ = _load_assignment(
        public_root=public_root,
        slot="rater_1",
        expected_sha256=mapping["public_assignments"]["rater_1"],
    )
    item_by_id = {item["item_id"]: item for item in rater_1_assignment["items"]}
    entry_by_case = {entry["case_id"]: entry for entry in mapping["entries"]}
    adjudication_root.mkdir(parents=True, mode=0o755)
    items = []
    adj_entries = []
    for case_id, disputed_checks in sorted(disagreements.items()):
        entry = entry_by_case[case_id]
        source_item = item_by_id[entry["item_ids"]["rater_1"]]
        item_id = f"item-{_blind_digest(blind_key, STUDY_ID, 'adjudicator', case_id)[:24]}"
        item_root = adjudication_root / item_id
        views = _copy_bound_views(
            source_root=public_root / "rater-1",
            target_root=item_root,
            views=source_item["views"],
        )
        # Paths in the public manifest are relative to adjudication_root.
        views = [dict(view, path=f"{item_id}/{view['path']}") for view in views]
        items.append(
            {
                "item_id": item_id,
                "task_context": entry["task_context"],
                "checks": disputed_checks,
                "views": views,
            }
        )
        adj_entries.append({"item_id": item_id, "case_id": case_id, "checks": disputed_checks})
    assignment = {
        "schema_version": ASSIGNMENT_SCHEMA,
        "study_id": mapping["study_id"],
        "spec_sha256": mapping["spec_sha256"],
        "tool_source_sha256": _tool_source_sha256(),
        "assignment_id": _blind_digest(blind_key, STUDY_ID, "adjudicator", "assignment"),
        "rater_slot": "adjudicator",
        "label_contract_sha256": mapping["label_contract_sha256"],
        "allowed_statuses": mapping["statuses"],
        "blinded_to": [
            "arm",
            "model output",
            "runtime pass/fail",
            "fallback result",
            "other rater labels",
        ],
        "instructions": "Independently label only the listed disputed checks from visible images.",
        "items": sorted(items, key=lambda item: item["item_id"]),
    }
    assignment_sha = _canonical_file(adjudication_root / "assignment.json", assignment)
    template = {
        "schema_version": RATING_SCHEMA,
        "assignment_sha256": assignment_sha,
        "rater_slot": "adjudicator",
        "rows": [
            {
                "item_id": item["item_id"],
                "labels": {check: None for check in item["checks"]},
            }
            for item in assignment["items"]
        ],
    }
    _canonical_file(adjudication_root / "ratings.template.json", template)
    unsigned = {
        "schema_version": ADJUDICATION_MAP_SCHEMA,
        "study_id": mapping["study_id"],
        "spec_sha256": mapping["spec_sha256"],
        "blind_key_sha256": sha256_bytes(blind_key),
        "tool_source_sha256": _tool_source_sha256(),
        "parent_mapping_sha256": sha256_bytes(mapping_raw),
        "assignment_sha256": assignment_sha,
        "rater_1_rating_sha256": first_rating_sha,
        "rater_2_rating_sha256": second_rating_sha,
        "statuses": mapping["statuses"],
        "entries": sorted(adj_entries, key=lambda item: item["item_id"]),
    }
    adjudication_map_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _canonical_file(
        adjudication_map_path,
        _authenticated_mapping(unsigned, blind_key),
        private=True,
    )
    return AdjudicationReceipt(
        disagreement_count=sum(len(value) for value in disagreements.values()),
        disputed_case_count=len(disagreements),
        assignment_sha256=assignment_sha,
    )


def _load_adjudication_ratings(
    *,
    mapping_raw: bytes,
    adjudication_map_path: Path,
    adjudication_root: Path,
    rating_path: Path,
    blind_key: bytes,
    rater_1_rating_sha256: str,
    rater_2_rating_sha256: str,
) -> tuple[dict[str, dict[str, str]], str]:
    adj_map, _ = _load_authenticated_mapping(
        adjudication_map_path, blind_key, schema=ADJUDICATION_MAP_SCHEMA
    )
    if adj_map.get("parent_mapping_sha256") != sha256_bytes(mapping_raw):
        raise AnnotationIntegrityError("adjudication mapping parent digest mismatch")
    if (
        adj_map.get("rater_1_rating_sha256") != rater_1_rating_sha256
        or adj_map.get("rater_2_rating_sha256") != rater_2_rating_sha256
    ):
        raise AnnotationIntegrityError("adjudication rater response digest mismatch")
    assignment, _ = _load_json(
        adjudication_root / "assignment.json", label="adjudication assignment"
    )
    assignment_raw = (adjudication_root / "assignment.json").read_bytes()
    if sha256_bytes(assignment_raw) != adj_map.get("assignment_sha256"):
        raise AnnotationIntegrityError("adjudication assignment digest mismatch")
    expected = {
        item["item_id"]: tuple(item["checks"])
        for item in assignment.get("items", [])
        if isinstance(item, Mapping)
    }
    rating, rating_raw = _load_json(rating_path, label="adjudication ratings")
    by_item = _validate_rating(
        rating,
        slot="adjudicator",
        assignment_sha256=adj_map["assignment_sha256"],
        expected_checks=expected,
        statuses=set(adj_map["statuses"]),
    )
    item_to_case = {entry["item_id"]: entry["case_id"] for entry in adj_map["entries"]}
    return (
        {item_to_case[item_id]: labels for item_id, labels in by_item.items()},
        sha256_bytes(rating_raw),
    )


def _decimal_fraction(value: Fraction) -> str:
    with localcontext() as context:
        context.prec = 32
        decimal = Decimal(value.numerator) / Decimal(value.denominator)
        return str(decimal.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def _agreement_report(
    first: Mapping[str, Mapping[str, str]],
    second: Mapping[str, Mapping[str, str]],
    checks: Sequence[str],
    statuses: Sequence[str],
) -> dict[str, Any]:
    cases = sorted(first)
    total = len(cases) * len(checks)
    matching = sum(
        first[case_id][check] == second[case_id][check] for case_id in cases for check in checks
    )
    kappas = {}
    for check in checks:
        agreement = sum(first[case][check] == second[case][check] for case in cases)
        observed = Fraction(agreement, len(cases))
        expected = sum(
            Fraction(
                sum(first[case][check] == status for case in cases)
                * sum(second[case][check] == status for case in cases),
                len(cases) ** 2,
            )
            for status in statuses
        )
        if expected == 1:
            kappa = Fraction(1 if observed == 1 else 0, 1)
        else:
            kappa = (observed - expected) / (1 - expected)
        kappas[check] = _decimal_fraction(kappa)
    return {
        "raw_agreement": _decimal_fraction(Fraction(matching, total)),
        "disagreement_count": total - matching,
        "cohen_kappa_per_check": kappas,
    }


def _gold_manifest(
    *,
    schema: str,
    mapping: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": schema,
        "study_id": mapping["study_id"],
        "spec_sha256": mapping["spec_sha256"],
        "label_contract_sha256": mapping["label_contract_sha256"],
        "rater_contract_sha256": mapping["rater_contract_sha256"],
        "adjudication_contract_sha256": mapping["adjudication_contract_sha256"],
        "rows": list(rows),
    }


def seal_annotations(
    *,
    private_map_path: Path,
    public_root: Path,
    rater_1_rating_path: Path,
    rater_2_rating_path: Path,
    output_root: Path,
    blind_key: bytes,
    adjudication_map_path: Path | None = None,
    adjudication_root: Path | None = None,
    adjudication_rating_path: Path | None = None,
) -> SealReceipt:
    """Resolve all labels and emit digest-bound train/dev/test artifacts.

    The test payload is permission-restricted but not encrypted.  Operators must
    keep the output root outside model-visible inputs until prompt freeze.
    """

    if output_root.exists():
        raise AnnotationIntegrityError("sealed annotation output already exists")
    mapping, mapping_raw = _load_authenticated_mapping(
        private_map_path, blind_key, schema=PRIVATE_MAP_SCHEMA
    )
    first, first_rating_sha = _load_bound_rating_record(
        private_map_path=private_map_path,
        public_root=public_root,
        rating_path=rater_1_rating_path,
        rater_slot="rater_1",
        blind_key=blind_key,
    )
    second, second_rating_sha = _load_bound_rating_record(
        private_map_path=private_map_path,
        public_root=public_root,
        rating_path=rater_2_rating_path,
        rater_slot="rater_2",
        blind_key=blind_key,
    )
    checks = tuple(mapping["checks"])
    disagreements = _case_disagreements(first, second, checks)
    adjudicated: dict[str, dict[str, str]] = {}
    adjudication_rating_sha: str | None = None
    if disagreements:
        if not all(
            path is not None
            for path in (
                adjudication_map_path,
                adjudication_root,
                adjudication_rating_path,
            )
        ):
            raise AnnotationIntegrityError("adjudication is required for every disagreement")
        adjudicated, adjudication_rating_sha = _load_adjudication_ratings(
            mapping_raw=mapping_raw,
            adjudication_map_path=adjudication_map_path,
            adjudication_root=adjudication_root,
            rating_path=adjudication_rating_path,
            blind_key=blind_key,
            rater_1_rating_sha256=first_rating_sha,
            rater_2_rating_sha256=second_rating_sha,
        )
        if set(adjudicated) != set(disagreements) or any(
            set(adjudicated[case_id]) != set(disagreements[case_id]) for case_id in disagreements
        ):
            raise AnnotationIntegrityError("adjudication response is not exact for disputes")
    elif any(
        path is not None
        for path in (adjudication_map_path, adjudication_root, adjudication_rating_path)
    ):
        raise AnnotationIntegrityError("adjudication inputs are forbidden without disagreements")

    entry_by_case = {entry["case_id"]: entry for entry in mapping["entries"]}
    final_rows = []
    for case_id in sorted(first):
        gold = {}
        for check in checks:
            if first[case_id][check] == second[case_id][check]:
                gold[check] = first[case_id][check]
            else:
                gold[check] = adjudicated[case_id][check]
        final_rows.append({"case_id": case_id, "gold": gold})
    rows_by_split = {
        split: [row for row in final_rows if entry_by_case[row["case_id"]]["split"] == split]
        for split in ("train", "dev", "test")
    }
    agreement = _agreement_report(first, second, checks, mapping["statuses"])
    agreement.update(
        {
            "rater_1_rating_sha256": first_rating_sha,
            "rater_2_rating_sha256": second_rating_sha,
            "adjudication_rating_sha256": adjudication_rating_sha,
        }
    )
    output_root.mkdir(parents=True, mode=0o700)
    output_root.chmod(0o700)
    train = _gold_manifest(schema=TRAIN_GOLD_SCHEMA, mapping=mapping, rows=rows_by_split["train"])
    dev = _gold_manifest(schema=DEV_GOLD_SCHEMA, mapping=mapping, rows=rows_by_split["dev"])
    test_payload = {
        **_gold_manifest(schema=TEST_PAYLOAD_SCHEMA, mapping=mapping, rows=rows_by_split["test"]),
        "tool_source_sha256": mapping["tool_source_sha256"],
        "agreement": agreement,
    }
    _canonical_file(output_root / "train_gold_manifest.json", train, private=True)
    dev_sha = _canonical_file(output_root / "dev_gold_manifest.json", dev, private=True)
    test_sha = _canonical_file(
        output_root / "test_annotation_payload.json", test_payload, private=True
    )
    candidate = {
        "adjudication_contract_sha256": mapping["adjudication_contract_sha256"],
        "case_ids": sorted(row["case_id"] for row in rows_by_split["test"]),
        "dev_gold_manifest_sha256": dev_sha,
        "label_contract_sha256": mapping["label_contract_sha256"],
        "rater_contract_sha256": mapping["rater_contract_sha256"],
        "schema_version": SEALED_MANIFEST_SCHEMA,
        "spec_sha256": mapping["spec_sha256"],
        "state": "sealed",
        "study_id": mapping["study_id"],
        "test_annotation_payload_sha256": test_sha,
    }
    candidate_path = output_root / "sealed_test_annotation_manifest.candidate.json"
    candidate_sha = _canonical_file(candidate_path, candidate, private=True)
    gold_seal = {
        "schema_version": GOLD_SEAL_SCHEMA,
        "study_id": mapping["study_id"],
        "spec_sha256": mapping["spec_sha256"],
        "state": "sealed",
        "test_gold_opened": False,
        "annotation_manifest_sha256": candidate_sha,
    }
    gold_seal_sha = _canonical_file(output_root / "visible_gold_seal.json", gold_seal, private=True)
    return SealReceipt(
        disagreement_count=agreement["disagreement_count"],
        dev_gold_manifest_sha256=dev_sha,
        test_annotation_payload_sha256=test_sha,
        annotation_manifest_sha256=candidate_sha,
        gold_seal_sha256=gold_seal_sha,
    )


def _read_private_key(path: Path) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise AnnotationIntegrityError("blind key must be a regular non-symlink file")
        mode = path.stat().st_mode
        if mode & 0o077:
            raise AnnotationIntegrityError("blind key file permissions must be owner-only")
        value = path.read_bytes()
    except OSError as error:
        raise AnnotationIntegrityError("blind key file cannot be read") from error
    _require_blind_key(value)
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export, adjudicate, and seal blinded visible-semantic annotations. "
            "This command never invokes a VLM or simulator."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export = subparsers.add_parser("export", help="create two blinded rater packs")
    export.add_argument("--spec", type=Path, required=True)
    export.add_argument("--repo-root", type=Path, required=True)
    export.add_argument("--public-root", type=Path, required=True)
    export.add_argument("--private-map", type=Path, required=True)
    export.add_argument("--blind-key-file", type=Path, required=True)

    adjudicate = subparsers.add_parser(
        "adjudicate", help="create a third-rater pack for disagreements only"
    )
    adjudicate.add_argument("--private-map", type=Path, required=True)
    adjudicate.add_argument("--public-root", type=Path, required=True)
    adjudicate.add_argument("--rater-1-ratings", type=Path, required=True)
    adjudicate.add_argument("--rater-2-ratings", type=Path, required=True)
    adjudicate.add_argument("--adjudication-root", type=Path, required=True)
    adjudicate.add_argument("--adjudication-map", type=Path, required=True)
    adjudicate.add_argument("--blind-key-file", type=Path, required=True)

    seal = subparsers.add_parser("seal", help="emit split-bound gold and digest commitments")
    seal.add_argument("--private-map", type=Path, required=True)
    seal.add_argument("--public-root", type=Path, required=True)
    seal.add_argument("--rater-1-ratings", type=Path, required=True)
    seal.add_argument("--rater-2-ratings", type=Path, required=True)
    seal.add_argument("--output-root", type=Path, required=True)
    seal.add_argument("--blind-key-file", type=Path, required=True)
    seal.add_argument("--adjudication-root", type=Path)
    seal.add_argument("--adjudication-map", type=Path)
    seal.add_argument("--adjudication-ratings", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    blind_key = _read_private_key(args.blind_key_file)
    if args.command == "export":
        receipt = export_blinded_assignments(
            spec_path=args.spec,
            repo_root=args.repo_root,
            public_root=args.public_root,
            private_map_path=args.private_map,
            blind_key=blind_key,
        )
    elif args.command == "adjudicate":
        receipt = prepare_adjudication(
            private_map_path=args.private_map,
            public_root=args.public_root,
            rater_1_rating_path=args.rater_1_ratings,
            rater_2_rating_path=args.rater_2_ratings,
            adjudication_root=args.adjudication_root,
            adjudication_map_path=args.adjudication_map,
            blind_key=blind_key,
        )
    else:
        receipt = seal_annotations(
            private_map_path=args.private_map,
            public_root=args.public_root,
            rater_1_rating_path=args.rater_1_ratings,
            rater_2_rating_path=args.rater_2_ratings,
            output_root=args.output_root,
            blind_key=blind_key,
            adjudication_map_path=args.adjudication_map,
            adjudication_root=args.adjudication_root,
            adjudication_rating_path=args.adjudication_ratings,
        )
    print(canonical_json_bytes(asdict(receipt)).decode("utf-8"))
    return 0


__all__ = [
    "AdjudicationReceipt",
    "AnnotationIntegrityError",
    "ExportReceipt",
    "SealReceipt",
    "canonical_json_bytes",
    "export_blinded_assignments",
    "load_bound_ratings",
    "main",
    "prepare_adjudication",
    "seal_annotations",
    "sha256_bytes",
]


if __name__ == "__main__":
    raise SystemExit(main())
