#!/usr/bin/env python3
"""Deterministic held-out benchmark for validated, trigger-specific harness memory.

This is a contract-level mechanism benchmark.  It uses committed asset metadata,
``scene_gen`` schemas/solver/validator, and the public Harness validation output
schema.  It does not run a simulator and therefore cannot establish physical
robustness or reproduce ASPIRE's paper-scale results.

The policy-facing observation deliberately excludes the evaluator's family,
fault, clean reference, expected action, and held-out outcome.  Development
cases are used once to promote immutable trigger -> action records; held-out
evaluation never mutates that library.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Protocol

from scene_gen.catalog import load_catalog
from scene_gen.parser import parse_rule_based
from scene_gen.schema import RelationType, ResolvedSceneSpec
from scene_gen.solver import solve_scene
from scene_gen.support_geometry import footprint_2d
from scene_gen.validator import validate_resolved_scene
from self_improving.harness.schemas.common import (
    ArtifactRef,
    Blocker,
    ValidationStatus,
)
from self_improving.harness.schemas.text2env import Text2EnvValidateOutput

SCHEMA_VERSION = "aspire.heldout_harness_benchmark.v1"
BENCHMARK_VERSION = "2026-08-31.v1"
MAX_ACTIONS = 1
MAX_VALIDATIONS = 2
DEFAULT_BOOTSTRAP_RESAMPLES = 10_000
DEFAULT_BOOTSTRAP_SEED = 260_700_272

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CATALOG = ROOT / "tests" / "fixtures" / "asset_catalog.json"

Family = Literal[
    "target_local_support",
    "containment",
    "nested_dynamic_contact",
    "runtime_evidence",
    "integrity_binding",
    "feasibility_proxy",
]
Split = Literal["development", "heldout"]
Action = Literal[
    "retry_unchanged",
    "replay_runtime",
    "recenter_support",
    "recenter_containment",
    "project_workspace",
]

FAMILIES: tuple[Family, ...] = (
    "target_local_support",
    "containment",
    "nested_dynamic_contact",
    "runtime_evidence",
    "integrity_binding",
    "feasibility_proxy",
)

# The order is fixed before any development or held-out result is observed.
ACTION_ORDER: tuple[Action, ...] = (
    "retry_unchanged",
    "replay_runtime",
    "recenter_support",
    "recenter_containment",
    "project_workspace",
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


@dataclass(frozen=True)
class CandidateState:
    """A resolved scene plus its claimed runtime evidence."""

    resolved: ResolvedSceneSpec
    runtime_evidence: dict[str, Any] | None


@dataclass(frozen=True)
class BenchmarkCase:
    """Evaluator-private case; policies never receive this object."""

    case_id: str
    split: Split
    family: Family
    variant: str
    faulty: CandidateState
    clean: CandidateState
    is_control: bool = False


@dataclass(frozen=True)
class PolicyObservation:
    """The complete policy-facing view.

    In particular, this type has no family, split, variant, expected outcome,
    expected action, or clean-reference field.
    """

    case_id: str
    attempt: int
    remaining_actions: int
    validation_status: str
    failure_triggers: tuple[str, ...]
    failed_checks: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "attempt": self.attempt,
            "remaining_actions": self.remaining_actions,
            "validation_status": self.validation_status,
            "failure_triggers": list(self.failure_triggers),
            "failed_checks": list(self.failed_checks),
        }


@dataclass(frozen=True)
class ValidatedSkill:
    trigger: str
    action: Action
    development_case_receipts: tuple[str, ...]
    development_successes: int
    clean_regressions: int
    record_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "trigger": self.trigger,
            "action": self.action,
            "development_case_receipts": list(self.development_case_receipts),
            "development_successes": self.development_successes,
            "clean_regressions": self.clean_regressions,
            "record_sha256": self.record_sha256,
        }


class Policy(Protocol):
    name: str

    def choose(self, observation: PolicyObservation) -> Action: ...


@dataclass(frozen=True)
class FrozenPolicy:
    name: str = "frozen_baseline"

    def choose(self, observation: PolicyObservation) -> Action:
        del observation
        return "retry_unchanged"


@dataclass(frozen=True)
class ReactivePolicy:
    """Uses only the current validation trace and carries no cross-run state."""

    name: str = "reactive_trace_no_memory"

    def choose(self, observation: PolicyObservation) -> Action:
        if any(trigger.startswith("runtime_") for trigger in observation.failure_triggers):
            return "replay_runtime"
        return "retry_unchanged"


@dataclass(frozen=True)
class ValidatedMemoryPolicy:
    """Reactive policy plus an immutable, development-validated skill library."""

    library: tuple[ValidatedSkill, ...]
    name: str = "validated_trigger_memory"

    def choose(self, observation: PolicyObservation) -> Action:
        rules = {skill.trigger: skill.action for skill in self.library}
        candidates = [
            rules[trigger] for trigger in observation.failure_triggers if trigger in rules
        ]
        if candidates:
            counts = {action: candidates.count(action) for action in set(candidates)}
            return min(
                counts,
                key=lambda action: (-counts[action], ACTION_ORDER.index(action)),
            )
        return ReactivePolicy().choose(observation)


def _case_id(split: Split, family: Family, index: int, seed: int, *, control: bool) -> str:
    identity = {
        "benchmark_version": BENCHMARK_VERSION,
        "split": split,
        "family": family,
        "index": index,
        "seed": seed,
        "control": control,
    }
    # The policy sees only this opaque receipt, never the identity payload.
    return _sha256(identity)[:24]


def _solve(request: str, seed: int, catalog_path: Path) -> ResolvedSceneSpec:
    catalog = load_catalog(catalog_path)
    spec = parse_rule_based(request, seed=seed)
    return solve_scene(spec, catalog)


def _clean_runtime_evidence(resolved: ResolvedSceneSpec) -> dict[str, Any]:
    objects: dict[str, Any] = {}
    by_id = {item.object_id: item for item in resolved.objects}
    for item in resolved.objects:
        fixed_table = item.is_static and item.support_relation == RelationType.ON_TABLE
        physical_support = not fixed_table
        object_evidence: dict[str, Any] = {
            "translation_drift_m": 0.0,
            "rotation_drift_deg": 0.0,
            "resolved_translation_error_m": 0.0,
            "resolved_rotation_error_deg": 0.0,
            "penetration_count": 0,
            "still_moving": False,
            "support_contact": physical_support,
            "support_contact_fraction": 1.0 if physical_support else 0.0,
            "unexpected_contact_fraction": 0.0,
            "unexpected_contact_targets": [],
            "support_mode": (
                "fixed_static_pose" if fixed_table else f"{item.support_relation.value}_contact"
            ),
            "support_target": item.support_target if physical_support else None,
            "support_footprint_margin_m": None,
            "inside_contained": None,
            "dropped": False,
            "visible_pixels": 512,
        }
        if item.support_relation == RelationType.ON_TOP_OF:
            object_evidence["support_footprint_margin_m"] = (
                by_id[item.support_target].support_margin_m + 0.01
            )
        if item.support_relation == RelationType.INSIDE:
            object_evidence["inside_contained"] = True
        if item.articulation_qpos:
            object_evidence["articulation_max_abs_error"] = 0.0
        objects[item.object_id] = object_evidence

    relations = {
        f"{relation.relation.value}:{relation.source}:{relation.target}": {"pass": True}
        for relation in resolved.relations
        if relation.target != "table"
        and relation.relation not in {RelationType.ON_TOP_OF, RelationType.INSIDE}
    }
    return {
        "schema_version": "robotwin.scene_runtime_evidence.v2",
        "scene_id": resolved.scene_id,
        "resolved_scene_sha256": resolved.digest(),
        "status": "pass",
        "robot_initial_collision_count": 0,
        "video_frame_count": 120,
        "unique_video_frame_count": 120,
        "objects": objects,
        "relations": relations,
    }


def _replace_object_pose(
    resolved: ResolvedSceneSpec,
    object_id: str,
    *,
    x: float,
    y: float,
) -> ResolvedSceneSpec:
    updated = []
    for item in resolved.objects:
        if item.object_id != object_id:
            updated.append(item)
            continue
        pose = item.pose.model_copy(update={"position_m": (x, y, item.pose.position_m[2])})
        updated.append(item.model_copy(update={"pose": pose}))
    return resolved.model_copy(update={"objects": tuple(updated)})


def _shift_in_target_frame(
    resolved: ResolvedSceneSpec,
    *,
    source_id: str,
    target_id: str,
    local_x: float,
    local_y: float,
) -> ResolvedSceneSpec:
    objects = {item.object_id: item for item in resolved.objects}
    target = objects[target_id]
    cosine = math.cos(target.pose.yaw_rad)
    sine = math.sin(target.pose.yaw_rad)
    world_x = target.pose.position_m[0] + cosine * local_x - sine * local_y
    world_y = target.pose.position_m[1] + sine * local_x + cosine * local_y
    return _replace_object_pose(resolved, source_id, x=world_x, y=world_y)


def _support_case(
    split: Split,
    index: int,
    seed: int,
    catalog_path: Path,
) -> BenchmarkCase:
    clean_resolved = _solve("Place a can on top of a plate.", seed, catalog_path)
    magnitude = 0.022 + 0.004 * (index % 5)
    angle = (index % 8) * math.pi / 4.0
    faulty_resolved = _shift_in_target_frame(
        clean_resolved,
        source_id="can_1",
        target_id="plate_1",
        local_x=magnitude * math.cos(angle),
        local_y=magnitude * math.sin(angle),
    )
    clean = CandidateState(clean_resolved, _clean_runtime_evidence(clean_resolved))
    faulty = CandidateState(faulty_resolved, _clean_runtime_evidence(faulty_resolved))
    return BenchmarkCase(
        case_id=_case_id(split, "target_local_support", index, seed, control=False),
        split=split,
        family="target_local_support",
        variant=f"local_offset_{index % 8}",
        faulty=faulty,
        clean=clean,
    )


def _containment_case(
    split: Split,
    index: int,
    seed: int,
    catalog_path: Path,
) -> BenchmarkCase:
    clean_resolved = _solve("Put an apple inside a basket.", seed, catalog_path)
    if index % 2:
        # The basket is wider on local x than local y, so use a larger x
        # displacement.  Both axes are beyond the complete apple footprint,
        # not merely beyond its center point.
        local_x, local_y = 0.072 + 0.003 * (index % 5), 0.0
    else:
        local_x, local_y = 0.0, 0.038 + 0.003 * (index % 5)
    faulty_resolved = _shift_in_target_frame(
        clean_resolved,
        source_id="apple_1",
        target_id="basket_1",
        local_x=local_x,
        local_y=local_y,
    )
    clean = CandidateState(clean_resolved, _clean_runtime_evidence(clean_resolved))
    faulty = CandidateState(faulty_resolved, _clean_runtime_evidence(faulty_resolved))
    return BenchmarkCase(
        case_id=_case_id(split, "containment", index, seed, control=False),
        split=split,
        family="containment",
        variant=f"target_local_axis_{index % 2}",
        faulty=faulty,
        clean=clean,
    )


def _contact_case(
    split: Split,
    index: int,
    seed: int,
    catalog_path: Path,
) -> BenchmarkCase:
    resolved = _solve("Place a can on top of a plate.", seed, catalog_path)
    clean_evidence = _clean_runtime_evidence(resolved)
    faulty_evidence = copy.deepcopy(clean_evidence)
    source = faulty_evidence["objects"]["can_1"]
    mode = index % 3
    if mode == 0:
        source["support_contact_fraction"] = 0.1 + 0.1 * (index % 6)
        variant = "short_contact_window"
    elif mode == 1:
        source["support_target"] = "table"
        variant = "wrong_support_target"
    else:
        source["unexpected_contact_fraction"] = 1.0
        source["unexpected_contact_targets"] = ["table"]
        variant = "unexpected_table_contact"
    clean = CandidateState(resolved, clean_evidence)
    faulty = CandidateState(resolved, faulty_evidence)
    return BenchmarkCase(
        case_id=_case_id(split, "nested_dynamic_contact", index, seed, control=False),
        split=split,
        family="nested_dynamic_contact",
        variant=variant,
        faulty=faulty,
        clean=clean,
    )


def _runtime_case(
    split: Split,
    index: int,
    seed: int,
    catalog_path: Path,
) -> BenchmarkCase:
    resolved = _solve("Place an apple near the center.", seed, catalog_path)
    clean_evidence = _clean_runtime_evidence(resolved)
    faulty_evidence: dict[str, Any] | None = copy.deepcopy(clean_evidence)
    mode = index % 8
    if mode == 0:
        faulty_evidence = None
        variant = "missing_runtime_evidence"
    else:
        assert faulty_evidence is not None
        object_evidence = faulty_evidence["objects"]["apple_1"]
        if mode == 1:
            faulty_evidence["unique_video_frame_count"] = 2
            variant = "duplicate_video_frames"
        elif mode == 2:
            object_evidence["visible_pixels"] = 1
            variant = "head_visibility"
        elif mode == 3:
            object_evidence["still_moving"] = True
            variant = "still_moving"
        elif mode == 4:
            object_evidence["dropped"] = True
            variant = "dropped"
        elif mode == 5:
            object_evidence["penetration_count"] = 1
            variant = "penetration"
        elif mode == 6:
            faulty_evidence["robot_initial_collision_count"] = 1
            variant = "initial_collision"
        else:
            faulty_evidence["status"] = "fail"
            variant = "runtime_status"
    clean = CandidateState(resolved, clean_evidence)
    faulty = CandidateState(resolved, faulty_evidence)
    return BenchmarkCase(
        case_id=_case_id(split, "runtime_evidence", index, seed, control=False),
        split=split,
        family="runtime_evidence",
        variant=variant,
        faulty=faulty,
        clean=clean,
    )


def _integrity_case(
    split: Split,
    index: int,
    seed: int,
    catalog_path: Path,
) -> BenchmarkCase:
    resolved = _solve("Place an apple near the center.", seed, catalog_path)
    clean_evidence = _clean_runtime_evidence(resolved)
    faulty_evidence = copy.deepcopy(clean_evidence)
    mode = index % 3
    if mode == 0:
        faulty_evidence.pop("resolved_scene_sha256")
        variant = "missing_resolved_digest"
    elif mode == 1:
        faulty_evidence["resolved_scene_sha256"] = "f" * 64
        variant = "wrong_resolved_digest"
    else:
        faulty_evidence["scene_id"] = "unrelated_scene"
        variant = "wrong_scene_id"
    clean = CandidateState(resolved, clean_evidence)
    faulty = CandidateState(resolved, faulty_evidence)
    return BenchmarkCase(
        case_id=_case_id(split, "integrity_binding", index, seed, control=False),
        split=split,
        family="integrity_binding",
        variant=variant,
        faulty=faulty,
        clean=clean,
    )


def _feasibility_case(
    split: Split,
    index: int,
    seed: int,
    catalog_path: Path,
) -> BenchmarkCase:
    clean_resolved = _solve("Place an apple near the center.", seed, catalog_path)
    apple = clean_resolved.objects[0]
    if index % 2:
        x = clean_resolved.workspace.x_bounds_m[1] + 0.1 + 0.01 * (index % 4)
        y = apple.pose.position_m[1]
        variant = "x_out_of_bounds"
    else:
        x = apple.pose.position_m[0]
        y = clean_resolved.workspace.y_bounds_m[1] + 0.1 + 0.01 * (index % 4)
        variant = "y_out_of_bounds"
    faulty_resolved = _replace_object_pose(clean_resolved, "apple_1", x=x, y=y)
    clean = CandidateState(clean_resolved, _clean_runtime_evidence(clean_resolved))
    faulty = CandidateState(faulty_resolved, _clean_runtime_evidence(faulty_resolved))
    return BenchmarkCase(
        case_id=_case_id(split, "feasibility_proxy", index, seed, control=False),
        split=split,
        family="feasibility_proxy",
        variant=variant,
        faulty=faulty,
        clean=clean,
    )


CASE_BUILDERS = {
    "target_local_support": _support_case,
    "containment": _containment_case,
    "nested_dynamic_contact": _contact_case,
    "runtime_evidence": _runtime_case,
    "integrity_binding": _integrity_case,
    "feasibility_proxy": _feasibility_case,
}


def build_split(
    split: Split,
    *,
    per_family: int,
    seed: int,
    catalog_path: Path = DEFAULT_CATALOG,
) -> tuple[BenchmarkCase, ...]:
    if per_family < 1:
        raise ValueError("per_family must be positive")
    split_offset = 1_000 if split == "development" else 1_000_000
    cases: list[BenchmarkCase] = []
    for family_index, family in enumerate(FAMILIES):
        builder = CASE_BUILDERS[family]
        for index in range(per_family):
            case_seed = seed + split_offset + family_index * 10_000 + index
            cases.append(builder(split, index, case_seed, catalog_path))
    return tuple(cases)


def build_clean_controls(cases: Iterable[BenchmarkCase]) -> tuple[BenchmarkCase, ...]:
    controls = []
    for index, case in enumerate(cases):
        controls.append(
            BenchmarkCase(
                case_id=_case_id(
                    case.split,
                    case.family,
                    index,
                    int(case.case_id[:8], 16),
                    control=True,
                ),
                split=case.split,
                family=case.family,
                variant="clean_control",
                faulty=case.clean,
                clean=case.clean,
                is_control=True,
            )
        )
    return tuple(controls)


def _validate(state: CandidateState) -> dict[str, Any]:
    return validate_resolved_scene(
        state.resolved,
        runtime_evidence=copy.deepcopy(state.runtime_evidence),
        require_runtime=True,
    )


def _failed_check_names(report: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        sorted(str(check["name"]) for check in report["checks"] if check["status"] == "fail")
    )


def _trigger_for_check(name: str) -> str:
    if name.startswith("relation:on_top_of:"):
        return "static_relation:on_top_of"
    if name.startswith("relation:inside:"):
        return "static_relation:inside"
    if name.startswith("workspace_bounds:"):
        return "static_feasibility:workspace"
    lowered = name.lower()
    if (
        "binding" in lowered
        or "resolved_scene_sha256" in lowered
        or "scene_id" in lowered
        or "runtime_scene" in lowered
    ):
        return "runtime_binding:scene_identity"
    if name.startswith(
        (
            "support_contact:",
            "no_unexpected_support_contact:",
            "runtime_support_margin:",
            "runtime_inside_containment:",
        )
    ):
        return "runtime_support:contact"
    if name.startswith(
        (
            "runtime_evidence",
            "runtime_status",
            "robot_initial_collision",
            "observer_video_",
            "penetration:",
            "settled:",
            "not_dropped:",
            "head_visibility:",
            "articulation_qpos:",
            "runtime_relation:",
        )
    ):
        return "runtime_observation:quality"
    return f"validator:{name.split(':', 1)[0]}"


def make_observation(
    case_id: str,
    report: dict[str, Any],
    *,
    attempt: int,
    remaining_actions: int,
) -> PolicyObservation:
    failed_checks = _failed_check_names(report)
    triggers = tuple(sorted({_trigger_for_check(name) for name in failed_checks}))
    return PolicyObservation(
        case_id=case_id,
        attempt=attempt,
        remaining_actions=remaining_actions,
        validation_status=str(report["status"]),
        failure_triggers=triggers,
        failed_checks=failed_checks,
    )


def _recenter_relations(
    resolved: ResolvedSceneSpec,
    relation_type: RelationType,
) -> ResolvedSceneSpec:
    updated = resolved
    for relation in resolved.relations:
        if relation.relation != relation_type or relation.target == "table":
            continue
        objects = {item.object_id: item for item in updated.objects}
        target = objects[relation.target]
        updated = _replace_object_pose(
            updated,
            relation.source,
            x=target.pose.position_m[0],
            y=target.pose.position_m[1],
        )
    return updated


def _project_workspace(resolved: ResolvedSceneSpec) -> ResolvedSceneSpec:
    updated = []
    x_low, x_high = resolved.workspace.x_bounds_m
    y_low, y_high = resolved.workspace.y_bounds_m
    for item in resolved.objects:
        footprint = footprint_2d(
            item.dimensions_m,
            item.pose.yaw_rad,
            item.footprint_shape,
        )
        lower_x = x_low + footprint.half_x
        upper_x = x_high - footprint.half_x
        lower_y = y_low + footprint.half_y
        upper_y = y_high - footprint.half_y
        old_x, old_y, old_z = item.pose.position_m
        new_x = min(max(old_x, lower_x), upper_x)
        new_y = min(max(old_y, lower_y), upper_y)
        pose = item.pose.model_copy(update={"position_m": (new_x, new_y, old_z)})
        updated.append(item.model_copy(update={"pose": pose}))
    return resolved.model_copy(update={"objects": tuple(updated)})


def apply_action(state: CandidateState, action: Action) -> CandidateState:
    if action == "retry_unchanged":
        return CandidateState(state.resolved, copy.deepcopy(state.runtime_evidence))
    if action == "replay_runtime":
        return CandidateState(state.resolved, _clean_runtime_evidence(state.resolved))
    if action == "recenter_support":
        repaired = _recenter_relations(state.resolved, RelationType.ON_TOP_OF)
        return CandidateState(repaired, _clean_runtime_evidence(repaired))
    if action == "recenter_containment":
        repaired = _recenter_relations(state.resolved, RelationType.INSIDE)
        return CandidateState(repaired, _clean_runtime_evidence(repaired))
    if action == "project_workspace":
        repaired = _project_workspace(state.resolved)
        return CandidateState(repaired, _clean_runtime_evidence(repaired))
    raise ValueError(f"unknown action: {action}")


def _integrity_bound(state: CandidateState) -> bool:
    evidence = state.runtime_evidence
    return bool(
        isinstance(evidence, dict)
        and evidence.get("resolved_scene_sha256") == state.resolved.digest()
        and evidence.get("scene_id") == state.resolved.scene_id
    )


def _oracle_safe(case: BenchmarkCase, state: CandidateState, report: dict[str, Any]) -> bool:
    if report["status"] != "pass":
        return False
    if case.family == "integrity_binding":
        return _integrity_bound(state)
    return True


def _artifact_for_report(report: dict[str, Any]) -> ArtifactRef:
    payload = _canonical_bytes(report)
    digest = hashlib.sha256(payload).hexdigest()
    return ArtifactRef(
        name="validation_report",
        uri=f"memory://validation/{digest}",
        media_type="application/json",
        sha256=digest,
        bytes=len(payload),
        schema_version="robotwin.scene_validation.v1",
    )


def _harness_output(report: dict[str, Any]) -> Text2EnvValidateOutput:
    artifact = _artifact_for_report(report)
    if report["status"] == "pass":
        return Text2EnvValidateOutput(
            validation_report=artifact,
            validation_status=ValidationStatus.PASS,
            publishable=True,
            blockers=(),
        )
    status = (
        ValidationStatus.INCOMPLETE if report["status"] == "incomplete" else ValidationStatus.FAIL
    )
    blocker = Blocker(
        code="T2E_VALIDATION_FAILED",
        message="authoritative validation did not pass",
        stage="validate",
        retryable=False,
        details={
            "fail_count": int(report["fail_count"]),
            "not_run_count": int(report["not_run_count"]),
        },
        unknowns=(),
        artifact_refs=(artifact,),
    )
    return Text2EnvValidateOutput(
        validation_report=artifact,
        validation_status=status,
        publishable=False,
        blockers=(blocker,),
    )


def _candidate_succeeds(case: BenchmarkCase, state: CandidateState) -> bool:
    report = _validate(state)
    output = _harness_output(report)
    return bool(output.publishable and _oracle_safe(case, state, report))


def build_validated_library(
    development_cases: tuple[BenchmarkCase, ...],
    clean_controls: tuple[BenchmarkCase, ...],
) -> tuple[tuple[ValidatedSkill, ...], dict[str, Any]]:
    trigger_cases: dict[str, list[BenchmarkCase]] = {}
    unobservable: list[str] = []
    for case in development_cases:
        report = _validate(case.faulty)
        observation = make_observation(
            case.case_id,
            report,
            attempt=0,
            remaining_actions=MAX_ACTIONS,
        )
        if not observation.failure_triggers:
            unobservable.append(case.case_id)
        for trigger in observation.failure_triggers:
            trigger_cases.setdefault(trigger, []).append(case)

    skills: list[ValidatedSkill] = []
    rejected: list[dict[str, Any]] = []
    for trigger in sorted(trigger_cases):
        cases = tuple(trigger_cases[trigger])
        selected: Action | None = None
        for action in ACTION_ORDER:
            dev_pass = all(
                _candidate_succeeds(case, apply_action(case.faulty, action)) for case in cases
            )
            if not dev_pass:
                continue
            clean_regressions = sum(
                not _candidate_succeeds(control, apply_action(control.clean, action))
                for control in clean_controls
            )
            if clean_regressions == 0:
                selected = action
                break
        if selected is None:
            rejected.append(
                {
                    "trigger": trigger,
                    "reason": "no action passed all development and clean-regression gates",
                    "development_cases": len(cases),
                }
            )
            continue
        receipts = tuple(sorted(_sha256({"case_id": case.case_id}) for case in cases))
        record = {
            "trigger": trigger,
            "action": selected,
            "development_case_receipts": list(receipts),
            "development_successes": len(cases),
            "clean_regressions": 0,
        }
        skills.append(
            ValidatedSkill(
                trigger=trigger,
                action=selected,
                development_case_receipts=receipts,
                development_successes=len(cases),
                clean_regressions=0,
                record_sha256=_sha256(record),
            )
        )
    frozen = tuple(skills)
    audit = {
        "development_cases": len(development_cases),
        "clean_controls": len(clean_controls),
        "promoted_skills": len(frozen),
        "unobservable_development_cases": sorted(unobservable),
        "rejected_triggers": rejected,
        "library_sha256": _sha256([skill.as_dict() for skill in frozen]),
    }
    return frozen, audit


def _evaluate_case(case: BenchmarkCase, policy: Policy) -> dict[str, Any]:
    initial_state = case.clean if case.is_control else case.faulty
    initial_report = _validate(initial_state)
    validations = 1
    actions = 0
    action: Action | None = None
    final_state = initial_state
    final_report = initial_report
    observation = make_observation(
        case.case_id,
        initial_report,
        attempt=0,
        remaining_actions=MAX_ACTIONS,
    )
    if initial_report["status"] != "pass":
        action = policy.choose(observation)
        actions += 1
        final_state = apply_action(initial_state, action)
        final_report = _validate(final_state)
        validations += 1
    output = _harness_output(final_report)
    oracle_safe = _oracle_safe(case, final_state, final_report)
    safe_publish = bool(output.publishable and oracle_safe)
    unsafe_publish = bool(output.publishable and not oracle_safe)
    return {
        "case_id": case.case_id,
        "family": case.family,
        "is_control": case.is_control,
        "initial_status": initial_report["status"],
        "initial_failed_checks": list(observation.failed_checks),
        "policy_observation_sha256": _sha256(observation.as_dict()),
        "action": action,
        "actions_used": actions,
        "validations_used": validations,
        "final_status": final_report["status"],
        "safe_publish": safe_publish,
        "unsafe_publish": unsafe_publish,
        "completion": safe_publish,
        "budget_violation": actions > MAX_ACTIONS or validations > MAX_VALIDATIONS,
    }


def evaluate_arm(
    policy: Policy,
    heldout_cases: tuple[BenchmarkCase, ...],
    clean_controls: tuple[BenchmarkCase, ...],
) -> dict[str, Any]:
    rows = [_evaluate_case(case, policy) for case in (*heldout_cases, *clean_controls)]
    faults = [row for row in rows if not row["is_control"]]
    controls = [row for row in rows if row["is_control"]]
    family_metrics: dict[str, Any] = {}
    for family in FAMILIES:
        family_rows = [row for row in faults if row["family"] == family]
        completed = sum(bool(row["completion"]) for row in family_rows)
        family_metrics[family] = {
            "completed": completed,
            "total": len(family_rows),
            "rate": completed / len(family_rows),
        }
    rates = [family_metrics[family]["rate"] for family in FAMILIES]
    integrity = [row for row in faults if row["family"] == "integrity_binding"]
    unsafe = sum(bool(row["unsafe_publish"]) for row in faults)
    clean_pass = sum(bool(row["safe_publish"]) for row in controls)
    action_counts = {
        action: sum(row["action"] == action for row in faults) for action in ACTION_ORDER
    }
    action_counts["none"] = sum(row["action"] is None for row in faults)
    fault_case_outcomes = [
        {
            "case_id": row["case_id"],
            "family": row["family"],
            "action": row["action"],
            "completion": row["completion"],
            "unsafe_publish": row["unsafe_publish"],
            "budget": {
                "actions_used": row["actions_used"],
                "max_actions": MAX_ACTIONS,
                "validations_used": row["validations_used"],
                "max_validations": MAX_VALIDATIONS,
                "violation": row["budget_violation"],
            },
        }
        for row in faults
    ]
    return {
        "policy": policy.name,
        "heldout_fault_cases": len(faults),
        "clean_control_cases": len(controls),
        "macro_heldout_robust_completion": sum(rates) / len(rates),
        "worst_family_rate": min(rates),
        "family_metrics": family_metrics,
        "unsafe_publish_count": unsafe,
        "unsafe_publish_rate": unsafe / len(faults),
        "clean_regression_rate": 1.0 - clean_pass / len(controls),
        "clean_preservation_rate": clean_pass / len(controls),
        "hash_binding_accuracy": (
            1.0 - sum(bool(row["unsafe_publish"]) for row in integrity) / len(integrity)
        ),
        "budget_violation_count": sum(bool(row["budget_violation"]) for row in rows),
        "action_counts": action_counts,
        "mean_actions_per_fault": sum(int(row["actions_used"]) for row in faults) / len(faults),
        "mean_validations_per_fault": (
            sum(int(row["validations_used"]) for row in faults) / len(faults)
        ),
        "fault_case_outcomes": fault_case_outcomes,
        "fault_case_outcomes_sha256": _sha256(fault_case_outcomes),
        "case_receipt_sha256": _sha256(rows),
    }


def _percentile(sorted_values: list[float], probability: float) -> float:
    """Return a deterministic linearly interpolated percentile (Hyndman-Fan type 7)."""

    if not sorted_values:
        raise ValueError("percentile requires at least one value")
    if not 0.0 <= probability <= 1.0:
        raise ValueError("probability must be in [0, 1]")
    position = probability * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def paired_family_stratified_bootstrap(
    baseline_arm: dict[str, Any],
    candidate_arm: dict[str, Any],
    *,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
) -> dict[str, Any]:
    """Compare exact paired outcomes with a family-stratified bootstrap.

    Each resample draws paired case deltas with replacement *within* every
    family, computes a mean delta per family, and then computes the same macro
    average used by mHRC.  No outcome is shuffled across families.
    """

    if resamples < 1:
        raise ValueError("resamples must be positive")
    baseline = {row["case_id"]: row for row in baseline_arm["fault_case_outcomes"]}
    candidate = {row["case_id"]: row for row in candidate_arm["fault_case_outcomes"]}
    if set(baseline) != set(candidate):
        raise ValueError("paired arms must contain exactly the same fault case IDs")

    differences: dict[Family, list[int]] = {family: [] for family in FAMILIES}
    improved = 0
    regressed = 0
    tied = 0
    for case_id in sorted(baseline):
        baseline_row = baseline[case_id]
        candidate_row = candidate[case_id]
        if baseline_row["family"] != candidate_row["family"]:
            raise ValueError(f"family mismatch for paired case {case_id}")
        difference = int(candidate_row["completion"]) - int(baseline_row["completion"])
        family = baseline_row["family"]
        differences[family].append(difference)
        if difference > 0:
            improved += 1
        elif difference < 0:
            regressed += 1
        else:
            tied += 1
    if any(not differences[family] for family in FAMILIES):
        raise ValueError("paired bootstrap requires at least one case in every family")

    rng = random.Random(seed)
    bootstrap_deltas: list[float] = []
    for _ in range(resamples):
        family_means = []
        for family in FAMILIES:
            values = differences[family]
            sampled_sum = sum(values[rng.randrange(len(values))] for _ in values)
            family_means.append(sampled_sum / len(values))
        bootstrap_deltas.append(sum(family_means) / len(family_means))
    bootstrap_deltas.sort()

    point_delta = (
        candidate_arm["macro_heldout_robust_completion"]
        - baseline_arm["macro_heldout_robust_completion"]
    )
    return {
        "comparison": f"{candidate_arm['policy']}_minus_{baseline_arm['policy']}",
        "metric": "macro_heldout_robust_completion",
        "delta": point_delta,
        "paired_cases": len(baseline),
        "paired_improved": improved,
        "paired_regressed": regressed,
        "paired_tied": tied,
        "bootstrap": {
            "method": "family_stratified_paired_percentile",
            "confidence": 0.95,
            "seed": seed,
            "resamples": resamples,
            "ci_lower": _percentile(bootstrap_deltas, 0.025),
            "ci_upper": _percentile(bootstrap_deltas, 0.975),
            "distribution_sha256": _sha256(bootstrap_deltas),
        },
    }


def _keep_rule_verdict(
    arms: dict[str, dict[str, Any]],
    comparison: dict[str, Any],
) -> dict[str, Any]:
    baseline = arms["reactive_trace_no_memory"]
    candidate = arms["validated_trigger_memory"]
    checks = {
        "delta_at_least_0_05": {
            "pass": comparison["delta"] >= 0.05,
            "observed": comparison["delta"],
            "threshold": 0.05,
        },
        "paired_bootstrap_ci_above_zero": {
            "pass": comparison["bootstrap"]["ci_lower"] > 0.0,
            "observed_lower": comparison["bootstrap"]["ci_lower"],
            "threshold": 0.0,
        },
        "candidate_mhrc_at_least_0_95": {
            "pass": candidate["macro_heldout_robust_completion"] >= 0.95,
            "observed": candidate["macro_heldout_robust_completion"],
            "threshold": 0.95,
        },
        "zero_unsafe_publications": {
            "pass": candidate["unsafe_publish_count"] == 0,
            "observed": candidate["unsafe_publish_count"],
            "threshold": 0,
        },
        "clean_control_acceptance_100_percent": {
            "pass": candidate["clean_preservation_rate"] == 1.0,
            "observed": candidate["clean_preservation_rate"],
            "threshold": 1.0,
        },
        "identity_binding_100_percent": {
            "pass": candidate["hash_binding_accuracy"] == 1.0,
            "observed": candidate["hash_binding_accuracy"],
            "threshold": 1.0,
        },
        "worst_family_not_worse_than_reactive": {
            "pass": candidate["worst_family_rate"] >= baseline["worst_family_rate"],
            "candidate": candidate["worst_family_rate"],
            "baseline": baseline["worst_family_rate"],
        },
        "zero_paired_case_regressions": {
            "pass": comparison["paired_regressed"] == 0,
            "observed": comparison["paired_regressed"],
            "threshold": 0,
        },
        "matched_budget_respected": {
            "pass": (
                candidate["budget_violation_count"] == 0 and baseline["budget_violation_count"] == 0
            ),
            "candidate_violations": candidate["budget_violation_count"],
            "baseline_violations": baseline["budget_violation_count"],
        },
    }
    passed = all(check["pass"] for check in checks.values())
    return {
        "protocol": "E2",
        "claim_scope": "offline_synthetic_contract_mechanism_only",
        "keep_rule_pass": passed,
        "decision": (
            "keep_synthetic_harness_mechanism_candidate"
            if passed
            else "do_not_keep_synthetic_harness_mechanism_candidate"
        ),
        "checks": checks,
        "paper_scale_aspire_supported": False,
        "simulator_physics_improvement_supported": False,
    }


def run_benchmark(
    *,
    seed: int = 260700272,
    development_per_family: int = 2,
    heldout_per_family: int = 20,
    catalog_path: Path = DEFAULT_CATALOG,
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
    bootstrap_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
) -> dict[str, Any]:
    development = build_split(
        "development",
        per_family=development_per_family,
        seed=seed,
        catalog_path=catalog_path,
    )
    heldout = build_split(
        "heldout",
        per_family=heldout_per_family,
        seed=seed,
        catalog_path=catalog_path,
    )
    development_controls = build_clean_controls(development)
    heldout_controls = build_clean_controls(heldout)
    development_ids = {case.case_id for case in (*development, *development_controls)}
    heldout_ids = {case.case_id for case in (*heldout, *heldout_controls)}
    overlap = sorted(development_ids & heldout_ids)
    if overlap:
        raise RuntimeError(f"development/heldout identity overlap: {overlap}")

    library, promotion_audit = build_validated_library(
        development,
        development_controls,
    )
    library_before = promotion_audit["library_sha256"]
    policies: tuple[Policy, ...] = (
        FrozenPolicy(),
        ReactivePolicy(),
        ValidatedMemoryPolicy(library),
    )
    arms = {policy.name: evaluate_arm(policy, heldout, heldout_controls) for policy in policies}
    comparison = paired_family_stratified_bootstrap(
        arms["reactive_trace_no_memory"],
        arms["validated_trigger_memory"],
        seed=bootstrap_seed,
        resamples=bootstrap_resamples,
    )
    keep_rule = _keep_rule_verdict(arms, comparison)
    library_after = _sha256([skill.as_dict() for skill in library])
    if library_after != library_before:
        raise RuntimeError("held-out evaluation mutated the validated skill library")

    result = {
        "schema_version": SCHEMA_VERSION,
        "benchmark_version": BENCHMARK_VERSION,
        "claim_scope": "offline_contract_mechanism_only",
        "seed": seed,
        "protocol": {
            "development_per_family": development_per_family,
            "heldout_per_family": heldout_per_family,
            "families": list(FAMILIES),
            "max_actions_per_case": MAX_ACTIONS,
            "max_validations_per_case": MAX_VALIDATIONS,
            "bootstrap_seed": bootstrap_seed,
            "bootstrap_resamples": bootstrap_resamples,
            "authoritative_gate": "scene_gen.validator.validate_resolved_scene",
            "publication_contract": "harness.text2env_validate_output.v1",
            "render_used_as_physics_evidence": False,
            "policy_view_excludes": [
                "family",
                "split",
                "variant",
                "clean_reference",
                "expected_outcome",
                "expected_action",
                "heldout_results",
            ],
        },
        "split_receipt": {
            "development_fault_cases": len(development),
            "development_clean_controls": len(development_controls),
            "heldout_fault_cases": len(heldout),
            "heldout_clean_controls": len(heldout_controls),
            "development_ids_sha256": _sha256(sorted(development_ids)),
            "heldout_ids_sha256": _sha256(sorted(heldout_ids)),
            "intersection": overlap,
        },
        "promotion": {
            **promotion_audit,
            "skills": [skill.as_dict() for skill in library],
            "library_frozen_before_heldout": library_before == library_after,
        },
        "arms": arms,
        "comparisons": {
            "validated_memory_vs_reactive": comparison,
        },
        "keep_rule": keep_rule,
        "limitations": [
            "Synthetic evidence exercises contracts; it is not a RoboTwin/SAPIEN rollout.",
            "The deterministic policies are mechanism proxies, not LLM or ASPIRE agents.",
            "Replaying runtime evidence is modeled as a bounded action without simulator cost.",
            (
                "The paired bootstrap conditions on the fixed synthetic families; it does "
                "not estimate simulator or deployment-distribution uncertainty."
            ),
            (
                "No conclusion about real physics, learned policy success, or "
                "paper-scale ASPIRE follows."
            ),
        ],
    }
    result["result_sha256"] = _sha256(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the deterministic ASPIRE-style held-out harness benchmark."
    )
    parser.add_argument("--seed", type=int, default=260700272)
    parser.add_argument("--development-per-family", type=int, default=2)
    parser.add_argument("--heldout-per-family", type=int, default=20)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--bootstrap-seed", type=int, default=DEFAULT_BOOTSTRAP_SEED)
    parser.add_argument(
        "--bootstrap-resamples",
        type=int,
        default=DEFAULT_BOOTSTRAP_RESAMPLES,
    )
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = run_benchmark(
        seed=args.seed,
        development_per_family=args.development_per_family,
        heldout_per_family=args.heldout_per_family,
        catalog_path=args.catalog,
        bootstrap_seed=args.bootstrap_seed,
        bootstrap_resamples=args.bootstrap_resamples,
    )
    rendered = json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
        print(args.out)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
