"""Text-only compilation into the shared Open-X-Sim IR."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agenticsim.generation.placement_agent import PlacementResult, plan_from_instruction

from .ir import (
    AssetBundle,
    AssetRepresentation,
    EnvironmentPackage,
    EnvSpec,
    Pose,
    SceneObject,
    TaskSpec,
)


def _midpoint(values: list[float] | tuple[float, float], default: float = 0.0) -> float:
    if len(values) != 2:
        return default
    return (float(values[0]) + float(values[1])) / 2.0


def _pose_from_placement(obj: dict[str, Any]) -> tuple[Pose, dict[str, Any]]:
    initial = obj.get("initial_pose") or {}
    quaternion = initial.get("qpos") or [1.0, 0.0, 0.0, 0.0]
    if initial.get("mode") == "random_uniform":
        position = (
            _midpoint(initial.get("xlim") or [0.0, 0.0]),
            _midpoint(initial.get("ylim") or [0.0, 0.0]),
            _midpoint(initial.get("zlim") or [0.0, 0.0]),
        )
        return Pose(position=position, orientation_wxyz=tuple(float(value) for value in quaternion)), dict(initial)
    xyz = initial.get("xyz") or [0.0, 0.0, 0.0]
    return (
        Pose(
            position=tuple(float(value) for value in xyz),
            orientation_wxyz=tuple(float(value) for value in quaternion),
        ),
        {},
    )


def _asset_check_by_object(result: PlacementResult) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("object_id")): item
        for item in result.manifest.get("asset_checks", [])
        if item.get("object_id")
    }


def _asset_from_object(obj: dict[str, Any], check: dict[str, Any] | None) -> AssetBundle:
    object_id = str(obj["id"])
    physical = dict(obj.get("physical") or {})
    if obj.get("kind") == "box":
        geometry = dict(obj.get("geometry") or {})
        half_size = geometry.get("half_size") or [0.025, 0.025, 0.025]
        representation = AssetRepresentation(
            format="primitive_box",
            uri="primitive://box",
            backend="portable",
            metadata={
                "half_size_m": [float(value) for value in half_size],
                "color_rgb": list(geometry.get("color") or [0.8, 0.8, 0.8]),
            },
        )
        return AssetBundle(
            asset_id=f"{object_id}_asset",
            category=str(obj.get("category") or "box"),
            representations=(representation,),
            source={"kind": "procedural_primitive", "generator": "RoboTwin.create_box"},
            physical=physical,
            tags=("primitive", "tabletop"),
        )

    asset = dict(obj.get("asset") or {})
    modelname = str(asset.get("modelname") or object_id)
    resolved_path = str((check or {}).get("path") or "")
    uri = resolved_path or f"robotwin://objects/{modelname}"
    representation = AssetRepresentation(
        format="robotwin_model",
        uri=uri,
        backend="sapien",
        metadata={
            "modelname": modelname,
            "model_id": int(asset.get("model_id", 0)),
            "resolved": bool((check or {}).get("resolved", False)),
        },
    )
    return AssetBundle(
        asset_id=f"{object_id}_asset",
        category=str(obj.get("category") or modelname),
        representations=(representation,),
        source={
            "kind": "robotwin_asset",
            "modelname": modelname,
            "source_root": str((check or {}).get("source") or "robotwin_asset_roots"),
        },
        physical=physical,
        tags=("robotwin", str(obj.get("role") or "object")),
    )


def placement_result_to_package(
    result: PlacementResult,
    *,
    target_backends: tuple[str, ...] = ("sapien",),
) -> EnvironmentPackage:
    """Translate the existing RoboTwin placement schema into EnvironmentPackage v1."""

    spec = result.spec
    checks = _asset_check_by_object(result)
    assets: list[AssetBundle] = []
    instances: list[SceneObject] = []
    for obj in spec.get("objects", []):
        asset = _asset_from_object(obj, checks.get(str(obj.get("id"))))
        assets.append(asset)
        pose, randomization = _pose_from_placement(obj)
        instances.append(
            SceneObject(
                instance_id=str(obj["id"]),
                asset_id=asset.asset_id,
                pose=pose,
                role=str(obj.get("role") or "object"),
                static=bool((obj.get("physical") or {}).get("is_static", False)),
                randomization=randomization,
                metadata={
                    "description_key": obj.get("description_key", ""),
                    "protected_region": obj.get("protected_region") or {},
                },
            )
        )

    workspace = spec.get("workspace") or {}
    raw_bounds = workspace.get("bounds") or {}
    bounds = (
        float((raw_bounds.get("x") or [-1.0, 1.0])[0]),
        float((raw_bounds.get("y") or [-1.0, 1.0])[0]),
        float((raw_bounds.get("z") or [0.0, 2.0])[0]),
        float((raw_bounds.get("x") or [-1.0, 1.0])[1]),
        float((raw_bounds.get("y") or [-1.0, 1.0])[1]),
        float((raw_bounds.get("z") or [0.0, 2.0])[1]),
    )
    env = EnvSpec(
        name=str(spec["task_name"]),
        objects=tuple(instances),
        workspace_bounds_m=bounds,
        robots=(
            {
                "id": "robot",
                "setup": str(workspace.get("robot_setup") or "dual_arm"),
                "source": "robotwin",
            },
        ),
        sensors=(
            {"id": "head_camera", "type": "rgb", "required": True},
            {"id": "left_camera", "type": "rgb", "required": False},
            {"id": "right_camera", "type": "rgb", "required": False},
        ),
        regions=tuple(dict(region) for region in spec.get("regions", [])),
        randomization=dict(spec.get("randomization") or {}),
        metadata={
            "workspace_type": workspace.get("type", "tabletop"),
            "surface": workspace.get("surface", "table"),
            "table": workspace.get("table") or {},
        },
    )
    task = TaskSpec(
        instruction=str(spec["language_instruction"]),
        intent=str(spec.get("intent") or "placement"),
        reset={
            "object_poses": "from_env_spec",
            "randomization": dict(spec.get("randomization") or {}),
            "constraints": list(spec.get("validation_constraints") or []),
        },
        action={
            "interface": "robotwin_primitive_plan",
            "arm_policy": dict(spec.get("arm_policy") or {}),
            "operations": sorted({str(step.get("op")) for step in spec.get("plan", [])}),
        },
        observation={
            "state": ["object_pose", "robot_joint_position", "contact"],
            "cameras": ["head_camera", "left_camera", "right_camera"],
        },
        plan=tuple(dict(step) for step in spec.get("plan", [])),
        success=tuple(dict(condition) for condition in spec.get("success", [])),
        termination=({"type": "timeout", "steps": 1000},),
        metadata={
            "source_schema": spec.get("schema_version"),
            "source_status": spec.get("status"),
            "language": spec.get("language") or {},
        },
    )
    package = EnvironmentPackage(
        package_id=str(spec["task_name"]),
        env=env,
        assets=tuple(assets),
        task=task,
        anchors=(),
        source={
            "mode": "text_only",
            "compiler": "agenticsim.generation.placement_agent",
            "schema": spec.get("schema_version"),
            "network_used": False,
            "asset_generation_used": bool(result.manifest.get("asset_generation_used", False)),
            "blockers": list(result.manifest.get("blockers") or []),
        },
        target_backends=target_backends,
        metadata={"placement_manifest": result.manifest},
    )
    package.validate()
    return package


def compile_text(
    instruction: str,
    *,
    repo_root: str | Path | None = None,
    target_backends: tuple[str, ...] = ("sapien",),
) -> EnvironmentPackage:
    """Compile text only; this function has no anchor or network entry point."""

    result = plan_from_instruction(instruction, repo_root=repo_root)
    return placement_result_to_package(result, target_backends=target_backends)

