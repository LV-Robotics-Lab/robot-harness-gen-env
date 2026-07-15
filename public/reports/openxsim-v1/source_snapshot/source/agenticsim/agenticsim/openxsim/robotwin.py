"""EnvironmentPackage to RoboTwin selection2env placement/task adapters."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .ir import AssetBundle, EnvironmentPackage, SceneObject


ROBOTWIN_SCALE_DEFAULTS: dict[str, list[float]] = {
    "002_bowl": [0.08, 0.08, 0.08],
    "021_cup": [0.08, 0.08, 0.08],
    "036_cabinet": [0.15, 0.15, 0.15],
    "071_can": [0.05, 0.05, 0.05],
    "110_basket": [0.12, 0.12, 0.12],
    "114_bottle": [0.06, 0.06, 0.06],
}


class RoboTwinExportError(RuntimeError):
    """Raised when a package cannot be represented by the RoboTwin adapter."""


class RoboTwinRuntimeEvidenceError(RuntimeError):
    """Raised when a RoboTwin rollout is not bound to the compiled package."""


def _asset_map(package: EnvironmentPackage) -> dict[str, AssetBundle]:
    return {asset.asset_id: asset for asset in package.assets}


def _robotwin_representation(asset: AssetBundle):
    return next((item for item in asset.representations if item.format == "robotwin_model"), None)


def _semantic(obj: SceneObject, asset: AssetBundle) -> str:
    category = asset.category.lower().strip()
    aliases = {"cola_can": "can", "cola bottle": "bottle"}
    return aliases.get(category, category.replace("cola_", ""))


def _role(obj: SceneObject) -> str:
    return {
        "manipulated": "manipuland_candidate",
        "container": "container_candidate",
        "fixture": "support_or_target_candidate",
        "distractor": "scene_object",
    }.get(obj.role, obj.role or "scene_object")


def _binding(package: EnvironmentPackage) -> dict[str, Any]:
    source = next((obj for obj in package.env.objects if obj.role == "manipulated"), None)
    if source is None:
        raise RoboTwinExportError("RoboTwin task needs one object with role=manipulated")
    for condition in package.task.success:
        condition_type = str(condition.get("type") or "")
        if condition_type in {"near", "contact"} and condition.get("target_object"):
            template = "place_in" if package.task.intent == "place_in_container" else "place_on"
            return {
                "template": template,
                "source_object_id": source.instance_id,
                "target_object_id": str(condition["target_object"]),
            }
        if condition_type == "in_region" and condition.get("region"):
            return {
                "template": "place_in_region",
                "source_object_id": source.instance_id,
                "target_region": str(condition["region"]),
            }
    raise RoboTwinExportError("RoboTwin adapter could not bind a supported success relation")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: str | Path) -> tuple[Path, dict[str, Any]]:
    resolved = Path(path).expanduser().resolve()
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RoboTwinRuntimeEvidenceError(f"could not read JSON evidence {resolved}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RoboTwinRuntimeEvidenceError(f"JSON evidence must be an object: {resolved}")
    return resolved, payload


def _evidence_path(report_path: Path, value: Any) -> Path:
    candidate = Path(str(value)).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    relocated = report_path.parent / candidate.name
    if relocated.is_file():
        return relocated.resolve()
    raise RoboTwinRuntimeEvidenceError(f"rollout evidence file is missing: {value}")


def _normalized_report_binding(value: dict[str, Any]) -> dict[str, Any]:
    result = {
        "template": value.get("template"),
        "source_object_id": value.get("source_object_id") or value.get("source_id"),
    }
    target_object = value.get("target_object_id") or value.get("target_id")
    if target_object:
        result["target_object_id"] = target_object
    target_region = value.get("target_region")
    if target_region:
        result["target_region"] = target_region
    return result


def runtime_evidence_from_rollout(
    package: EnvironmentPackage,
    task_program_path: str | Path,
    rollout_report_path: str | Path,
    *,
    minimum_video_frames: int = 3,
) -> dict[str, Any]:
    """Validate and normalize a generated RoboTwin rollout for conformance checks."""

    package.validate()
    task_path, task_program = _read_json(task_program_path)
    report_path, report = _read_json(rollout_report_path)
    package_ref = dict(task_program.get("environment_package") or {})
    failures: list[str] = []
    if package_ref.get("schema") != package.schema_version:
        failures.append("task-program package schema differs")
    if package_ref.get("digest") != package.digest():
        failures.append("task-program package digest differs")
    if task_program.get("task_id") != package.package_id:
        failures.append("task-program task id differs")
    expected_binding = _binding(package)
    if dict(task_program.get("task_binding") or {}) != expected_binding:
        failures.append("task-program task binding differs")
    if _normalized_report_binding(dict(report.get("task_binding") or {})) != expected_binding:
        failures.append("rollout task binding differs")

    verifier = dict(task_program.get("verifier") or {})
    conditions = verifier.get("conditions")
    if verifier.get("type") != "conjunction" or conditions != [dict(item) for item in package.task.success]:
        failures.append("task-program verifier is not an exact TaskSpec success-condition binding")

    placement_value = task_program.get("placement_spec")
    if not placement_value:
        failures.append("task-program placement path is absent")
        placement_path = task_path.parent / "placement.json"
    else:
        candidate = Path(str(placement_value)).expanduser()
        placement_path = candidate if candidate.is_file() else task_path.parent / candidate.name
    declared_placement_sha = str(task_program.get("placement_sha256") or "")
    if not placement_path.is_file() or _sha256(placement_path) != declared_placement_sha:
        failures.append("task-program placement SHA-256 does not match placement bytes")
    if report.get("placement_sha256") != declared_placement_sha:
        failures.append("rollout placement SHA-256 differs")

    semantic = dict(report.get("semantic_verification") or {})
    if semantic.get("schema") != "agenticsim.robotwin_success_verification.v1":
        failures.append("rollout semantic verification is absent")
    if semantic.get("conditions") != [dict(item) for item in package.task.success]:
        failures.append("rollout semantic conditions differ from TaskSpec")
    if semantic.get("all_passed") is not True:
        failures.append("rollout did not pass every TaskSpec success condition")

    video_capture = dict(report.get("video_capture") or {})
    frame_count = int(video_capture.get("frame_count") or 0)
    if video_capture.get("endpoint_only") is not False or frame_count < minimum_video_frames:
        failures.append(
            f"continuous video requirement failed: endpoint_only={video_capture.get('endpoint_only')} "
            f"frames={frame_count} minimum={minimum_video_frames}"
        )
    try:
        video_path = _evidence_path(report_path, report.get("observer_video"))
    except RoboTwinRuntimeEvidenceError as exc:
        failures.append(str(exc))
        video_path = report_path.parent / "observer_rollout_probe.mp4"

    object_ids = {obj.instance_id for obj in package.env.objects}
    initial_objects = dict(report.get("initial_objects") or {})
    final_objects = dict(report.get("final_objects") or {})
    reset_ok = set(initial_objects) == object_ids
    step_ok = set(final_objects) == object_ids and int(report.get("move_event_count") or 0) > 0
    action_bound = (
        report.get("status") == "pass_generated_action_rollout"
        and report.get("plan_success") is True
        and step_ok
        and int(report.get("left_joint_path_len") or 0) + int(report.get("right_joint_path_len") or 0) > 0
    )
    success_bound = report.get("check_success") is True and semantic.get("all_passed") is True
    if not reset_ok:
        failures.append("rollout initial object set differs from EnvSpec")
    if not step_ok:
        failures.append("rollout did not execute a state-changing action sequence")
    if not action_bound:
        failures.append("RoboTwin action interface was not executed successfully")
    if not success_bound:
        failures.append("RoboTwin success evaluator was not bound successfully")

    observation_keys = sorted(str(value) for value in package.task.observation.get("state", []))
    if failures:
        raise RoboTwinRuntimeEvidenceError("; ".join(failures))

    semantic_contract = package.task.semantic_contract()
    contract_hash = hashlib.sha256(
        json.dumps(semantic_contract, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "schema": "agenticsim.runtime_evidence.v1",
        "backend": "robotwin",
        "package_id": package.package_id,
        "package_digest": package.digest(),
        "task_contract_hash": contract_hash,
        "reset_ok": reset_ok,
        "step_ok": step_ok,
        "action_interface": package.task.action.get("interface"),
        "action_interface_bound": action_bound,
        "success_evaluator": verifier,
        "success_evaluator_bound": success_bound,
        "observation_keys": observation_keys,
        "task_program": str(task_path),
        "task_program_sha256": _sha256(task_path),
        "placement": str(placement_path.resolve()),
        "placement_sha256": declared_placement_sha,
        "rollout_report": str(report_path),
        "rollout_report_sha256": _sha256(report_path),
        "video": {
            **video_capture,
            "path": str(video_path),
            "sha256": _sha256(video_path),
            "size_bytes": video_path.stat().st_size,
        },
        "endpoint_states": {
            "initial_objects": initial_objects,
            "final_objects": final_objects,
        },
        "semantic_verification": semantic,
        "limitations": [
            "Endpoint states are not promoted to an L3 trajectory replay.",
            "One generated action rollout is not an L4 policy evaluation.",
        ],
    }


def build_placement(package: EnvironmentPackage) -> dict[str, Any]:
    """Build the placement schema consumed by the generated RoboTwin action probe."""

    package.validate()
    assets = _asset_map(package)
    objects: list[dict[str, Any]] = []
    for obj in package.env.objects:
        asset = assets[obj.asset_id]
        representation = _robotwin_representation(asset)
        if representation is None:
            raise RoboTwinExportError(f"{obj.instance_id} has no robotwin_model representation")
        modelname = str(representation.metadata.get("modelname") or asset.source.get("modelname") or "")
        if not modelname:
            raise RoboTwinExportError(f"{obj.instance_id} robotwin_model has no modelname")
        resolved = Path(representation.uri).is_dir()
        if not resolved and not representation.uri.startswith("robotwin://"):
            raise RoboTwinExportError(f"{obj.instance_id} RoboTwin asset path is missing: {representation.uri}")
        objects.append(
            {
                "id": obj.instance_id,
                "semantic": _semantic(obj, asset),
                "asset_id": modelname,
                "model_id": int(representation.metadata.get("model_id", 0)),
                "role": _role(obj),
                "pose": {
                    "xyz": list(obj.pose.position),
                    "qpos": list(obj.pose.orientation_wxyz),
                    "z_policy": "snap_to_tabletop_on_load",
                },
                "physical": {
                    "is_static": obj.static or not bool(asset.physical.get("graspable", True)),
                    "collision": True,
                    "stable_on_table": True,
                },
                "asset_metadata": {
                    "scale": list(
                        representation.metadata.get("scale")
                        or ROBOTWIN_SCALE_DEFAULTS.get(modelname, [1.0, 1.0, 1.0])
                    ),
                    "asset_type": "rigid",
                    "graspable": bool(asset.physical.get("graspable", obj.role == "manipulated")),
                    "support_surface_candidate": obj.role in {"container", "fixture"},
                    "placement_defaults": {
                        "loader": "create_actor",
                        "qpos": list(obj.pose.orientation_wxyz),
                        "z_policy": "snap_to_tabletop_on_load",
                    },
                },
                "source": {
                    "environment_package_asset_id": asset.asset_id,
                    "representation_uri": representation.uri,
                    "resolved": resolved,
                },
            }
        )
    bounds = package.env.workspace_bounds_m
    spatial_regions: dict[str, Any] = {}
    for region in package.env.regions:
        center = region.get("center") or [0.0, 0.0, 0.0]
        size = region.get("size") or [0.1, 0.1, 0.0]
        spatial_regions[str(region["id"])] = {
            "x": [float(center[0]) - float(size[0]) / 2.0, float(center[0]) + float(size[0]) / 2.0],
            "y": [float(center[1]) - float(size[1]) / 2.0, float(center[1]) + float(size[1]) / 2.0],
            "success_tolerance_m": float(region.get("success_tolerance_m", 0.0)),
        }
    return {
        "schema_version": "robotwin.tabletop_placement.v0",
        "placement_name": package.package_id,
        "stage": "compiled_from_environment_package",
        "language_prompt": package.task.instruction,
        "generated_by": {
            "agent": "agenticsim.openxsim.robotwin",
            "source_schema": package.schema_version,
            "source_package_digest": package.digest(),
        },
        "workspace": {
            "surface": package.env.metadata.get("surface", "table"),
            "coordinate_convention": "robot_first_person_tabletop; z up; metres",
            "bounds": {
                "x": [bounds[0], bounds[3]],
                "y": [bounds[1], bounds[4]],
                "z": [bounds[2], bounds[5]],
            },
            "spatial_regions": spatial_regions,
        },
        "objects": objects,
        "constraints": list(package.task.reset.get("constraints") or []),
        "downstream_task_hints": [_binding(package)["template"], "generated_selection2env_play_once"],
    }


def write_robotwin_bundle(package: EnvironmentPackage, output_dir: str | Path) -> tuple[Path, Path]:
    """Write a placement plus hash-bound task-program input."""

    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    placement_path = output / "placement.json"
    placement = build_placement(package)
    placement_path.write_text(json.dumps(placement, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    placement_sha = hashlib.sha256(placement_path.read_bytes()).hexdigest()
    binding = _binding(package)
    verifier: dict[str, Any] = {
        "source_object_id": binding["source_object_id"],
        "relation": {"place_in": "in", "place_on": "on", "place_in_region": "in_region"}[binding["template"]],
    }
    if binding.get("target_object_id"):
        verifier["target_object_id"] = binding["target_object_id"]
    else:
        verifier["target_region"] = binding["target_region"]
    task_program = {
        "schema_version": "alchedata.selection2env_task_program.v0",
        "task_id": package.package_id,
        "scene_id": f"{package.package_id}_scene",
        "placement_spec": str(placement_path),
        "placement_sha256": placement_sha,
        "task_binding": binding,
        "verifier": {
            **verifier,
            "type": "conjunction",
            "conditions": [dict(item) for item in package.task.success],
        },
        "environment_package": {
            "schema": package.schema_version,
            "digest": package.digest(),
        },
    }
    task_program_path = output / "task_program.json"
    task_program_path.write_text(
        json.dumps(task_program, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return placement_path, task_program_path
