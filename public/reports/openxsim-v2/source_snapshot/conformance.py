"""L0-L4 cross-simulator conformance evaluation."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .backends import CompileResult
from .importers import import_compile_manifest
from .ir import EnvironmentPackage


PASS = "pass"
FAIL = "fail"
NOT_EVALUATED = "not_evaluated"


@dataclass(frozen=True)
class ConformanceCheck:
    level: str
    name: str
    status: str
    details: str
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConformanceReport:
    source_backend: str
    target_backend: str
    package_id: str
    highest_consecutive_level: str | None
    checks: tuple[ConformanceCheck, ...]
    schema: str = "agenticsim.conformance_report.v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write_json(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path


def _load_mapping(value: str | Path | Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    return json.loads(Path(value).read_text(encoding="utf-8"))


def _artifact_check(result: CompileResult) -> ConformanceCheck:
    artifact = Path(result.artifact_path)
    if result.blockers:
        return ConformanceCheck(
            "L0",
            "asset_import",
            FAIL,
            "backend compiler reported unresolved asset blockers",
            {"blockers": list(result.blockers)},
        )
    if not artifact.is_file() or artifact.stat().st_size == 0:
        return ConformanceCheck("L0", "asset_import", FAIL, "compiled artifact is missing or empty")
    try:
        if result.backend == "mujoco":
            ET.parse(artifact)
        elif result.backend == "metasim":
            ast.parse(artifact.read_text(encoding="utf-8"))
        elif result.backend == "sapien":
            json.loads(artifact.read_text(encoding="utf-8"))
        elif result.backend == "isaacsim":
            text = artifact.read_text(encoding="utf-8")
            if not text.startswith("#usda 1.0") or text.count("{") != text.count("}"):
                raise ValueError("invalid USDA header or unbalanced blocks")
        elif result.backend == "robotwin":
            task_program = json.loads(artifact.read_text(encoding="utf-8"))
            placement = Path(str(result.metadata.get("placement_path") or ""))
            if task_program.get("schema_version") != "alchedata.selection2env_task_program.v0":
                raise ValueError("unexpected RoboTwin task-program schema")
            if not placement.is_file():
                raise ValueError("RoboTwin placement is missing")
            actual_sha = hashlib.sha256(placement.read_bytes()).hexdigest()
            if task_program.get("placement_sha256") != actual_sha:
                raise ValueError("RoboTwin placement SHA-256 mismatch")
            verifier = task_program.get("verifier") or {}
            if verifier.get("type") != "conjunction" or not verifier.get("conditions"):
                raise ValueError("RoboTwin success verifier is not bound")
    except Exception as exc:
        return ConformanceCheck("L0", "asset_import", FAIL, f"compiled artifact validation failed: {exc}")
    return ConformanceCheck(
        "L0",
        "asset_import",
        PASS,
        "artifact exists and passed backend-specific static validation",
        {"artifact": str(artifact), "size_bytes": artifact.stat().st_size},
    )


def _structural_check(
    source: EnvironmentPackage,
    target: EnvironmentPackage,
    *,
    pose_tolerance_m: float,
) -> ConformanceCheck:
    failures: list[str] = []
    source_objects = {obj.instance_id: obj for obj in source.env.objects}
    target_objects = {obj.instance_id: obj for obj in target.env.objects}
    if set(source_objects) != set(target_objects):
        failures.append("object ids differ")
    if {asset.asset_id for asset in source.assets} != {asset.asset_id for asset in target.assets}:
        failures.append("asset ids differ")
    if source.env.units != target.env.units:
        failures.append("world units differ")
    if source.env.up_axis != target.env.up_axis:
        failures.append("up axis differs")
    max_pose_error = 0.0
    for object_id in set(source_objects) & set(target_objects):
        lhs = source_objects[object_id]
        rhs = target_objects[object_id]
        error = math.sqrt(sum((lhs.pose.position[index] - rhs.pose.position[index]) ** 2 for index in range(3)))
        max_pose_error = max(max_pose_error, error)
        if error > pose_tolerance_m:
            failures.append(f"{object_id} pose error {error:.6g}m exceeds tolerance")
        if lhs.static != rhs.static:
            failures.append(f"{object_id} static flag differs")
    source_regions = {str(item.get("id")) for item in source.env.regions}
    target_regions = {str(item.get("id")) for item in target.env.regions}
    if source_regions != target_regions:
        failures.append("region ids differ")
    return ConformanceCheck(
        "L1",
        "scene_structure",
        FAIL if failures else PASS,
        "; ".join(failures) if failures else "units, axes, objects, assets, regions, and nominal poses agree",
        {"max_pose_error_m": max_pose_error, "pose_tolerance_m": pose_tolerance_m},
    )


def _runtime_semantics_check(
    source: EnvironmentPackage,
    target: EnvironmentPackage,
    source_runtime: dict[str, Any] | None,
    target_runtime: dict[str, Any] | None,
) -> ConformanceCheck:
    if source.task.semantic_contract() != target.task.semantic_contract():
        return ConformanceCheck("L2", "task_semantics", FAIL, "serialized task contracts differ")
    if source_runtime is None or target_runtime is None:
        return ConformanceCheck(
            "L2",
            "task_semantics",
            NOT_EVALUATED,
            "task contracts match statically, but both source and target runtime evidence are required",
        )
    expected_hash = hashlib.sha256(
        json.dumps(source.task.semantic_contract(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    binding_failures = [
        f"{side}.{field}"
        for side, evidence in (("source", source_runtime), ("target", target_runtime))
        for field in ("action_interface_bound", "success_evaluator_bound")
        if evidence.get(field) is not True
    ]
    hash_failures = [
        side
        for side, evidence in (("source", source_runtime), ("target", target_runtime))
        if evidence.get("task_contract_hash") != expected_hash
    ]
    if binding_failures or hash_failures:
        return ConformanceCheck(
            "L2",
            "task_semantics",
            FAIL,
            "runtime action/success binding or task-contract hash failed",
            {"binding_failures": binding_failures, "contract_hash_failures": hash_failures},
        )
    required = ("reset_ok", "step_ok")
    failed = [
        f"{side}.{field}"
        for side, evidence in (("source", source_runtime), ("target", target_runtime))
        for field in required
        if evidence.get(field) is not True
    ]
    if failed:
        return ConformanceCheck("L2", "task_semantics", FAIL, f"runtime evidence failed: {failed}")
    source_obs = set(source_runtime.get("observation_keys") or [])
    target_obs = set(target_runtime.get("observation_keys") or [])
    expected_obs = set(source.task.observation.get("state") or [])
    observation_failures = {
        side: sorted(expected_obs - set(evidence.get("observation_keys") or []))
        for side, evidence in (("source", source_runtime), ("target", target_runtime))
        if expected_obs - set(evidence.get("observation_keys") or [])
    }
    if observation_failures:
        return ConformanceCheck(
            "L2",
            "task_semantics",
            FAIL,
            "runtime evidence is missing TaskSpec observation keys",
            {"missing_observation_keys": observation_failures},
        )
    if source_obs and target_obs and source_obs != target_obs:
        return ConformanceCheck(
            "L2",
            "task_semantics",
            FAIL,
            "runtime observation keys differ",
            {"source": sorted(source_obs), "target": sorted(target_obs)},
        )
    return ConformanceCheck(
        "L2",
        "task_semantics",
        PASS,
        "task contracts match and both runtimes reset and step successfully",
        {"observation_keys": sorted(source_obs or target_obs)},
    )


def _trajectory_check(
    source_runtime: dict[str, Any] | None,
    target_runtime: dict[str, Any] | None,
    *,
    state_tolerance_m: float,
    contact_debounce_steps: int,
) -> ConformanceCheck:
    source_trajectory = (source_runtime or {}).get("trajectory")
    target_trajectory = (target_runtime or {}).get("trajectory")
    if not source_trajectory or not target_trajectory:
        return ConformanceCheck(
            "L3",
            "trajectory_replay",
            NOT_EVALUATED,
            "both runtimes must provide non-empty trajectory arrays",
        )
    if len(source_trajectory) != len(target_trajectory):
        return ConformanceCheck(
            "L3",
            "trajectory_replay",
            FAIL,
            "trajectory lengths differ",
            {"source_steps": len(source_trajectory), "target_steps": len(target_trajectory)},
        )
    max_error = 0.0
    missing_objects: set[str] = set()
    for source_step, target_step in zip(source_trajectory, target_trajectory):
        source_objects = source_step.get("objects") or {}
        target_objects = target_step.get("objects") or {}
        if set(source_objects) != set(target_objects):
            missing_objects.update(set(source_objects) ^ set(target_objects))
            continue
        for object_id in source_objects:
            lhs = source_objects[object_id]
            rhs = target_objects[object_id]
            if len(lhs) < 3 or len(rhs) < 3:
                missing_objects.add(object_id)
                continue
            error = math.sqrt(sum((float(lhs[index]) - float(rhs[index])) ** 2 for index in range(3)))
            max_error = max(max_error, error)
    source_contacts = [set(step.get("contacts") or []) for step in source_trajectory]
    target_contacts = [set(step.get("contacts") or []) for step in target_trajectory]
    raw_contact_mismatches = sum(left != right for left, right in zip(source_contacts, target_contacts))

    def close_short_gaps(trace: list[set[str]]) -> list[set[str]]:
        closed = [set(values) for values in trace]
        if contact_debounce_steps <= 0:
            return closed
        contact_ids = set().union(*closed) if closed else set()
        for contact_id in contact_ids:
            index = 0
            while index < len(closed):
                if contact_id in closed[index]:
                    index += 1
                    continue
                gap_start = index
                while index < len(closed) and contact_id not in closed[index]:
                    index += 1
                if (
                    gap_start > 0
                    and index < len(closed)
                    and index - gap_start <= contact_debounce_steps
                ):
                    for gap_index in range(gap_start, index):
                        closed[gap_index].add(contact_id)
        return closed

    debounced_source = close_short_gaps(source_contacts)
    debounced_target = close_short_gaps(target_contacts)
    contact_mismatches = sum(left != right for left, right in zip(debounced_source, debounced_target))
    failures: list[str] = []
    if missing_objects:
        failures.append(f"trajectory object mismatch: {sorted(missing_objects)}")
    if max_error > state_tolerance_m:
        failures.append(f"maximum state error {max_error:.6g}m exceeds tolerance")
    if contact_mismatches:
        failures.append(
            f"contact sets differ on {contact_mismatches} steps after "
            f"{contact_debounce_steps}-step debounce"
        )
    pass_details = "trajectory state and contact traces agree within tolerance"
    if raw_contact_mismatches and not contact_mismatches:
        pass_details += f" after closing contact gaps up to {contact_debounce_steps} steps"
    return ConformanceCheck(
        "L3",
        "trajectory_replay",
        FAIL if failures else PASS,
        "; ".join(failures) if failures else pass_details,
        {
            "steps": len(source_trajectory),
            "max_state_error_m": max_error,
            "state_tolerance_m": state_tolerance_m,
            "raw_contact_mismatch_steps": raw_contact_mismatches,
            "contact_mismatch_steps": contact_mismatches,
            "contact_debounce_steps": contact_debounce_steps,
        },
    )


def _policy_check(
    source_policy: dict[str, Any] | None,
    target_policy: dict[str, Any] | None,
    *,
    success_rate_tolerance: float,
    minimum_episodes: int,
) -> ConformanceCheck:
    if source_policy is None or target_policy is None:
        return ConformanceCheck(
            "L4",
            "policy_behavior",
            NOT_EVALUATED,
            "source and target policy evaluation evidence are both required",
        )
    source_episodes = int(source_policy.get("episodes", 0))
    target_episodes = int(target_policy.get("episodes", 0))
    if source_episodes < minimum_episodes or target_episodes < minimum_episodes:
        return ConformanceCheck(
            "L4",
            "policy_behavior",
            FAIL,
            "policy evaluation has too few episodes",
            {"minimum_episodes": minimum_episodes, "source": source_episodes, "target": target_episodes},
        )
    difference = abs(float(source_policy.get("success_rate", 0.0)) - float(target_policy.get("success_rate", 0.0)))
    return ConformanceCheck(
        "L4",
        "policy_behavior",
        PASS if difference <= success_rate_tolerance else FAIL,
        "policy success-rate difference is within tolerance"
        if difference <= success_rate_tolerance
        else "policy success-rate difference exceeds tolerance",
        {"success_rate_difference": difference, "tolerance": success_rate_tolerance},
    )


def evaluate_conformance(
    source_package: EnvironmentPackage,
    target_compile: CompileResult | str | Path,
    *,
    source_backend: str,
    source_runtime: str | Path | Mapping[str, Any] | None = None,
    target_runtime: str | Path | Mapping[str, Any] | None = None,
    source_policy: str | Path | Mapping[str, Any] | None = None,
    target_policy: str | Path | Mapping[str, Any] | None = None,
    pose_tolerance_m: float = 1e-6,
    state_tolerance_m: float = 0.01,
    contact_debounce_steps: int = 0,
    success_rate_tolerance: float = 0.1,
    minimum_policy_episodes: int = 20,
) -> ConformanceReport:
    """Evaluate every level without promoting missing evidence to a pass."""

    result = target_compile if isinstance(target_compile, CompileResult) else CompileResult.read(target_compile)
    target_package = import_compile_manifest(result.manifest_path)
    checks = (
        _artifact_check(result),
        _structural_check(source_package, target_package, pose_tolerance_m=pose_tolerance_m),
        _runtime_semantics_check(
            source_package,
            target_package,
            _load_mapping(source_runtime),
            _load_mapping(target_runtime),
        ),
        _trajectory_check(
            _load_mapping(source_runtime),
            _load_mapping(target_runtime),
            state_tolerance_m=state_tolerance_m,
            contact_debounce_steps=contact_debounce_steps,
        ),
        _policy_check(
            _load_mapping(source_policy),
            _load_mapping(target_policy),
            success_rate_tolerance=success_rate_tolerance,
            minimum_episodes=minimum_policy_episodes,
        ),
    )
    highest: str | None = None
    for check in checks:
        if check.status != PASS:
            break
        highest = check.level
    return ConformanceReport(
        source_backend=source_backend,
        target_backend=result.backend,
        package_id=source_package.package_id,
        highest_consecutive_level=highest,
        checks=checks,
    )
