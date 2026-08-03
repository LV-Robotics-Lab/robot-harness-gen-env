"""Import env-gen resolved_scene.json into the Open-X-Sim IR (first-class importer)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from .importers import EnvironmentImportError, _task_from_contract, _valid_identifier
from .ir import (
    AssetBundle,
    AssetRepresentation,
    EnvSpec,
    EnvironmentPackage,
    Pose,
    SceneObject,
)

_MESH_SUFFIXES = {"glb", "obj", "dae", "stl", "ply", "usd", "usda", "usdc"}


def _is_env_gen(data: dict[str, Any]) -> bool:
    return str(data.get("compiler_version", "")).startswith("scene_gen")


def _representation(
    load_type: str, source_files: list[str], base: Path
) -> AssetRepresentation:
    def _resolve(p: str) -> Path:
        path = Path(p).expanduser()
        return path if path.is_absolute() else (base / path).resolve()

    candidates = list(source_files)
    if load_type == "urdf":
        for f in candidates:
            if f.lower().endswith(".urdf"):
                return AssetRepresentation(
                    format="urdf", uri=str(_resolve(f)), backend="sapien"
                )
    for f in candidates:
        suffix = Path(f).suffix.lower().lstrip(".")
        if suffix in _MESH_SUFFIXES:
            return AssetRepresentation(
                format=suffix, uri=str(_resolve(f)), backend="sapien"
            )
    raise EnvironmentImportError(
        f"no mesh/urdf representation in source_files: {source_files}"
    )


def import_env_gen(path: str | Path) -> EnvironmentPackage:
    source = Path(path).expanduser().resolve()
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EnvironmentImportError(
            f"env-gen scene parse failed for {source}: {exc}"
        ) from exc
    if not _is_env_gen(data):
        raise EnvironmentImportError(
            f"not an env-gen resolved_scene (compiler_version={data.get('compiler_version')!r})"
        )

    ws = data.get("workspace") or {}
    x_lo, x_hi = ws.get("x_bounds_m") or [-1.0, 1.0]
    y_lo, y_hi = ws.get("y_bounds_m") or [-1.0, 1.0]
    table = float(ws.get("table_height_m", 0.0))
    workspace_bounds = (
        float(x_lo),
        float(y_lo),
        table,
        float(x_hi),
        float(y_hi),
        table + 0.5,
    )

    objects: list[SceneObject] = []
    assets: list[AssetBundle] = []
    seen: set[str] = set()
    for index, obj in enumerate(data.get("objects") or []):
        instance_id = _valid_identifier(
            str(obj.get("object_id") or f"object_{index}"), "obj"
        )
        asset_id = _valid_identifier(
            f"{obj.get('asset_id')}_m{obj.get('model_id', 0)}", "asset"
        )
        pose = obj.get("pose") or {}
        objects.append(
            SceneObject(
                instance_id=instance_id,
                asset_id=asset_id,
                pose=Pose(
                    position=tuple(
                        float(v) for v in (pose.get("position_m") or [0.0, 0.0, 0.0])
                    ),
                    orientation_wxyz=tuple(
                        float(v)
                        for v in (pose.get("orientation_wxyz") or [1.0, 0.0, 0.0, 0.0])
                    ),
                ),
                static=bool(obj.get("is_static", False)),
                scale=tuple(
                    float(v) for v in (obj.get("mesh_scale") or [1.0, 1.0, 1.0])
                ),
                metadata={
                    "category": obj.get("category"),
                    "color": obj.get("color"),
                    "material": obj.get("material"),
                    "support_relation": obj.get("support_relation"),
                    "support_target": obj.get("support_target"),
                    "grounding_score": obj.get("grounding_score"),
                },
            )
        )
        if asset_id in seen:
            continue
        seen.add(asset_id)
        joints = list(obj.get("articulation_joint_names") or [])
        assets.append(
            AssetBundle(
                asset_id=asset_id,
                category=str(obj.get("category") or "object"),
                representations=(
                    _representation(
                        str(obj.get("load_type") or "rigid"),
                        list(obj.get("source_files") or []),
                        source.parent,
                    ),
                ),
                physical={
                    "dimensions_m": obj.get("dimensions_m"),
                    "mass_kg": {"status": "unknown"},
                    "inertia": {"status": "unknown"},
                    "friction": {"status": "unknown"},
                },
                articulation=(
                    {
                        "joint_names": joints,
                        "joint_limits": list(
                            obj.get("articulation_joint_limits") or []
                        ),
                        "qpos": list(obj.get("articulation_qpos") or []),
                    }
                    if joints
                    else {}
                ),
                source={
                    "kind": "env_gen",
                    "asset_id": obj.get("asset_id"),
                    "model_id": obj.get("model_id"),
                    "asset_provenance": obj.get("asset_provenance"),
                    "source_files": list(obj.get("source_files") or []),
                },
                tags=tuple(t for t in (obj.get("color"), obj.get("material")) if t),
            )
        )

    task, limitations = _task_from_contract(None, backend="env_gen")
    task = replace(
        task,
        instruction=str(data.get("request") or task.instruction),
        intent="env_gen_scene_import",
    )
    name = _valid_identifier(str(data.get("scene_id") or source.stem), "env_gen")
    package = EnvironmentPackage(
        package_id=name,
        env=EnvSpec(
            name=name,
            objects=tuple(objects),
            gravity_mps2=(0.0, 0.0, -9.81),
            workspace_bounds_m=workspace_bounds,
            metadata={
                "request": data.get("request"),
                "seed": data.get("seed"),
                "compiler_version": data.get("compiler_version"),
                "source_scene_spec_sha256": data.get("source_scene_spec_sha256"),
                "asset_catalog_sha256": data.get("asset_catalog_sha256"),
                "relations": data.get("relations") or [],
            },
        ),
        assets=tuple(assets),
        task=task,
        source={
            "mode": "existing_environment_import",
            "backend": "env_gen",
            "path": str(source),
            "limitations": limitations,
        },
        target_backends=("robotwin",),
    )
    package.validate()
    return package
