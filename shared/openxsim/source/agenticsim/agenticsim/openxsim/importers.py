"""Import compiled and existing simulator environments into EnvironmentPackage."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .backends import CompileResult
from .ir import (
    AssetBundle,
    AssetRepresentation,
    EnvironmentPackage,
    EnvSpec,
    Pose,
    SceneObject,
    TaskSpec,
)


class EnvironmentImportError(RuntimeError):
    """Raised when an existing environment cannot be represented honestly."""


def import_compile_manifest(path: str | Path) -> EnvironmentPackage:
    """Recover the exact canonical package preserved beside a backend artifact."""

    result = CompileResult.read(path)
    package_path = Path(result.package_path)
    if not package_path.is_file():
        raise EnvironmentImportError(f"compile manifest package snapshot is missing: {package_path}")
    package = EnvironmentPackage.read_json(package_path)
    if package.digest() != result.package_digest:
        raise EnvironmentImportError("compile manifest package digest does not match its snapshot")
    return package


def _floats(value: str | None, length: int, default: tuple[float, ...]) -> tuple[float, ...]:
    if not value:
        return default
    result = tuple(float(item) for item in value.split())
    if len(result) != length:
        raise EnvironmentImportError(f"expected {length} numeric values, got {value!r}")
    return result


def _valid_identifier(value: str, prefix: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.:-]+", "_", value).strip("_")
    if not value or not value[0].isalpha():
        value = f"{prefix}_{value}"
    return value


def _task_from_contract(contract: dict[str, Any] | None, *, backend: str) -> tuple[TaskSpec, list[str]]:
    if contract:
        return (
            TaskSpec(
                instruction=f"Imported {backend} task contract.",
                intent="imported_task",
                reset=dict(contract.get("reset") or {}),
                action=dict(contract.get("action") or {}),
                observation=dict(contract.get("observation") or {}),
                plan=(),
                success=tuple(dict(item) for item in contract.get("success", [])) or ({"type": "unbound"},),
                termination=tuple(dict(item) for item in contract.get("termination", [])),
                metadata={"task_contract_embedded": True, "source_backend": backend},
            ),
            [],
        )
    return (
        TaskSpec(
            instruction=f"Bind task semantics for the imported {backend} environment.",
            intent="environment_import_requires_task_binding",
            reset={"source": f"{backend}_defaults"},
            action={"status": "unbound"},
            observation={"state": ["object_pose"]},
            plan=(),
            success=({"type": "unbound", "requires_binding": True},),
            metadata={"task_contract_embedded": False, "source_backend": backend},
        ),
        ["task_semantics_unbound"],
    )


def _native_package(
    *,
    package_id: str,
    backend: str,
    source: Path,
    objects: list[SceneObject],
    assets: list[AssetBundle],
    gravity: tuple[float, float, float],
    contract: dict[str, Any] | None,
    regions: tuple[dict[str, Any], ...] = (),
    metadata: dict[str, Any] | None = None,
) -> EnvironmentPackage:
    task, limitations = _task_from_contract(contract, backend=backend)
    package = EnvironmentPackage(
        package_id=_valid_identifier(package_id, backend),
        env=EnvSpec(
            name=_valid_identifier(package_id, backend),
            objects=tuple(objects),
            gravity_mps2=gravity,
            regions=regions,
            metadata=dict(metadata or {}),
        ),
        assets=tuple(assets),
        task=task,
        source={
            "mode": "existing_environment_import",
            "backend": backend,
            "path": str(source),
            "limitations": limitations,
        },
        target_backends=(backend,),
    )
    package.validate()
    return package


def import_mjcf(path: str | Path, *, package_id: str | None = None) -> EnvironmentPackage:
    """Import MJCF structure; absent task semantics remain explicitly unbound."""

    source = Path(path).expanduser().resolve()
    try:
        tree = ET.parse(source)
    except (OSError, ET.ParseError) as exc:
        raise EnvironmentImportError(f"MJCF parse failed for {source}: {exc}") from exc
    root = tree.getroot()
    compiler = root.find("compiler")
    angle = compiler.get("angle", "degree") if compiler is not None else "degree"
    option = root.find("option")
    gravity = _floats(option.get("gravity") if option is not None else None, 3, (0.0, 0.0, -9.81))
    mesh_paths: dict[str, Path] = {}
    for mesh in root.findall("./asset/mesh"):
        name = mesh.get("name")
        filename = mesh.get("file")
        if name and filename:
            mesh_paths[name] = (source.parent / filename).resolve()

    assets: list[AssetBundle] = []
    objects: list[SceneObject] = []
    seen_assets: set[str] = set()
    worldbody = root.find("worldbody")
    if worldbody is not None:
        for index, body in enumerate(worldbody.iter("body")):
            name = _valid_identifier(body.get("name") or f"body_{index}", "body")
            geom = body.find("geom")
            if geom is None:
                continue
            asset_id = _valid_identifier(f"{name}_asset", "asset")
            geom_type = geom.get("type", "sphere")
            representations: list[AssetRepresentation] = []
            if geom_type == "box":
                half = list(_floats(geom.get("size"), 3, (0.05, 0.05, 0.05)))
                rgba = list(_floats(geom.get("rgba"), 4, (0.8, 0.8, 0.8, 1.0)))
                representations.append(
                    AssetRepresentation(
                        format="primitive_box",
                        uri="primitive://box",
                        metadata={"half_size_m": half, "color_rgb": rgba[:3]},
                    )
                )
            elif geom_type == "mesh" and geom.get("mesh") in mesh_paths:
                mesh_path = mesh_paths[str(geom.get("mesh"))]
                representations.append(
                    AssetRepresentation(
                        format=mesh_path.suffix.lower().lstrip("."),
                        uri=str(mesh_path),
                        backend="mujoco",
                    )
                )
            else:
                representations.append(
                    AssetRepresentation(
                        format=f"mujoco_{geom_type}",
                        uri=f"mujoco://geom/{geom_type}",
                        backend="mujoco",
                        metadata={"size": geom.get("size", "")},
                    )
                )
            if asset_id not in seen_assets:
                assets.append(
                    AssetBundle(
                        asset_id=asset_id,
                        category=geom_type,
                        representations=tuple(representations),
                        source={"kind": "mjcf", "path": str(source), "geom": geom.get("name", "")},
                    )
                )
                seen_assets.add(asset_id)
            objects.append(
                SceneObject(
                    instance_id=name,
                    asset_id=asset_id,
                    pose=Pose(
                        position=_floats(body.get("pos"), 3, (0.0, 0.0, 0.0)),
                        orientation_wxyz=_floats(body.get("quat"), 4, (1.0, 0.0, 0.0, 0.0)),
                    ),
                    static=body.find("freejoint") is None and body.find("joint") is None,
                    metadata={"source_body": body.get("name", "")},
                )
            )

    custom = root.find("./custom/text[@name='agenticsim_task_contract']")
    contract: dict[str, Any] | None = None
    if custom is not None and custom.get("data"):
        try:
            contract = json.loads(custom.get("data", "{}"))
        except json.JSONDecodeError:
            contract = None
    if contract:
        task = TaskSpec(
            instruction="Imported AgenticSim MJCF task",
            intent="imported_task",
            reset=dict(contract.get("reset") or {}),
            action=dict(contract.get("action") or {}),
            observation=dict(contract.get("observation") or {}),
            plan=(),
            success=tuple(dict(item) for item in contract.get("success", [])) or ({"type": "unbound"},),
            termination=tuple(dict(item) for item in contract.get("termination", [])),
            metadata={"task_contract_embedded": True},
        )
    else:
        task = TaskSpec(
            instruction="Bind task semantics for the imported MJCF environment.",
            intent="environment_import_requires_task_binding",
            reset={"source": "mjcf_defaults"},
            action={"status": "unbound"},
            observation={"state": ["qpos", "qvel"]},
            plan=(),
            success=({"type": "unbound", "requires_binding": True},),
            metadata={"task_contract_embedded": False},
        )
    package_name = _valid_identifier(package_id or root.get("model") or source.stem, "mjcf")
    package = EnvironmentPackage(
        package_id=package_name,
        env=EnvSpec(
            name=package_name,
            objects=tuple(objects),
            gravity_mps2=gravity,
            metadata={"mjcf_angle": angle},
        ),
        assets=tuple(assets),
        task=task,
        source={
            "mode": "existing_environment_import",
            "backend": "mujoco",
            "path": str(source),
            "limitations": [] if contract else ["task_semantics_unbound"],
        },
        target_backends=("mujoco",),
    )
    package.validate()
    return package


def import_sapien_scene(path: str | Path) -> EnvironmentPackage:
    """Import the structured scene format emitted for SAPIEN execution."""

    source = Path(path).expanduser().resolve()
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EnvironmentImportError(f"SAPIEN scene parse failed for {source}: {exc}") from exc
    if data.get("schema") != "agenticsim.sapien_scene.v1":
        raise EnvironmentImportError(f"unsupported SAPIEN scene schema: {data.get('schema')!r}")
    assets: list[AssetBundle] = []
    objects: list[SceneObject] = []
    for index, item in enumerate(data.get("objects") or []):
        if item.get("kind") == "missing":
            raise EnvironmentImportError(f"SAPIEN source contains unresolved object: {item.get('name')}")
        name = _valid_identifier(str(item.get("name") or f"object_{index}"), "sapien")
        asset_id = _valid_identifier(f"{name}_asset", "asset")
        kind = str(item.get("kind") or "")
        if kind == "box":
            representation = AssetRepresentation(
                format="primitive_box",
                uri="primitive://box",
                metadata={
                    "half_size_m": [float(value) for value in item.get("half_size_m") or [0.05, 0.05, 0.05]],
                    "color_rgb": [float(value) for value in item.get("color_rgb") or [0.8, 0.8, 0.8]],
                },
            )
        else:
            asset_path = Path(str(item.get("path") or "")).expanduser()
            if not asset_path.is_absolute():
                asset_path = (source.parent / asset_path).resolve()
            if not asset_path.exists():
                raise EnvironmentImportError(f"SAPIEN asset is missing for {name}: {asset_path}")
            representation = AssetRepresentation(format=kind, uri=str(asset_path), backend="sapien")
        assets.append(
            AssetBundle(
                asset_id=asset_id,
                category=kind or "object",
                representations=(representation,),
                source={"kind": "sapien_scene", "path": str(source)},
            )
        )
        objects.append(
            SceneObject(
                instance_id=name,
                asset_id=asset_id,
                pose=Pose(
                    position=tuple(float(value) for value in item.get("position") or [0.0, 0.0, 0.0]),
                    orientation_wxyz=tuple(
                        float(value) for value in item.get("orientation_wxyz") or [1.0, 0.0, 0.0, 0.0]
                    ),
                ),
                static=bool(item.get("static", False)),
                scale=tuple(float(value) for value in item.get("scale") or [1.0, 1.0, 1.0]),
            )
        )
    return _native_package(
        package_id=str(data.get("package_id") or source.stem),
        backend="sapien",
        source=source,
        objects=objects,
        assets=assets,
        gravity=tuple(float(value) for value in data.get("gravity_mps2") or [0.0, 0.0, -9.81]),
        contract=dict(data.get("task_contract") or {}) or None,
        regions=tuple(dict(item) for item in data.get("regions") or []),
        metadata={"source_schema": data["schema"]},
    )


def import_metasim_scenario(path: str | Path) -> EnvironmentPackage:
    """Import a serialized MetaSim ScenarioCfg without executing Python."""

    source = Path(path).expanduser().resolve()
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EnvironmentImportError(f"MetaSim scenario parse failed for {source}: {exc}") from exc
    if data.get("schema") != "agenticsim.metasim_scenario.v1":
        raise EnvironmentImportError(f"unsupported MetaSim scenario schema: {data.get('schema')!r}")
    assets: list[AssetBundle] = []
    objects: list[SceneObject] = []
    for index, item in enumerate(data.get("objects") or []):
        name = _valid_identifier(str(item.get("name") or f"object_{index}"), "metasim")
        asset_id = _valid_identifier(f"{name}_asset", "asset")
        class_name = str(item.get("class") or "")
        if class_name == "PrimitiveCubeCfg":
            size = [float(value) for value in item.get("size") or [0.1, 0.1, 0.1]]
            representation = AssetRepresentation(
                format="primitive_box",
                uri="primitive://box",
                metadata={
                    "half_size_m": [value / 2.0 for value in size],
                    "color_rgb": [float(value) for value in item.get("color") or [0.8, 0.8, 0.8]],
                },
            )
        elif class_name == "RigidObjCfg":
            path_fields = ("usd_path", "urdf_path", "mjcf_path", "mesh_path")
            selected = next(((field, item.get(field)) for field in path_fields if item.get(field)), None)
            if selected is None:
                raise EnvironmentImportError(f"MetaSim rigid object has no asset path: {name}")
            field, value = selected
            asset_path = Path(str(value)).expanduser()
            if not asset_path.is_absolute():
                asset_path = (source.parent / asset_path).resolve()
            if not asset_path.is_file():
                raise EnvironmentImportError(f"MetaSim asset is missing for {name}: {asset_path}")
            fmt = {"usd_path": asset_path.suffix.lstrip("."), "urdf_path": "urdf", "mjcf_path": "mjcf"}.get(
                field, asset_path.suffix.lstrip(".")
            )
            representation = AssetRepresentation(format=fmt, uri=str(asset_path), backend="metasim")
        else:
            raise EnvironmentImportError(f"unsupported MetaSim object class: {class_name!r}")
        assets.append(
            AssetBundle(
                asset_id=asset_id,
                category=class_name or "object",
                representations=(representation,),
                source={"kind": "metasim_scenario", "path": str(source)},
                physical={"mass_kg": float(item.get("mass", 0.1))},
            )
        )
        objects.append(
            SceneObject(
                instance_id=name,
                asset_id=asset_id,
                pose=Pose(
                    position=tuple(float(value) for value in item.get("default_position") or [0.0, 0.0, 0.0]),
                    orientation_wxyz=tuple(
                        float(value) for value in item.get("default_orientation") or [1.0, 0.0, 0.0, 0.0]
                    ),
                ),
                static=bool(item.get("fix_base_link", False)),
                scale=tuple(float(value) for value in item.get("scale") or [1.0, 1.0, 1.0]),
            )
        )
    return _native_package(
        package_id=str(data.get("package_id") or source.stem),
        backend="metasim",
        source=source,
        objects=objects,
        assets=assets,
        gravity=tuple(float(value) for value in data.get("gravity") or [0.0, 0.0, -9.81]),
        contract=dict(data.get("task_contract") or {}) or None,
        metadata={"source_schema": data["schema"], "simulator": data.get("simulator")},
    )


def _usda_scope(text: str, name: str) -> str | None:
    match = re.search(rf'\bdef\s+Scope\s+"{re.escape(name)}"\s*\{{', text)
    if not match:
        return None
    depth = 1
    index = match.end()
    while index < len(text) and depth:
        depth += 1 if text[index] == "{" else -1 if text[index] == "}" else 0
        index += 1
    return text[match.end() : index - 1] if depth == 0 else None


def _usda_child_blocks(scope: str) -> list[tuple[str, str, str]]:
    results: list[tuple[str, str, str]] = []
    pattern = re.compile(r'\bdef\s+(Cube|Xform)\s+"([^"]+)"(?:\s*\([^)]*\))?\s*\{')
    for match in pattern.finditer(scope):
        depth = 1
        index = match.end()
        while index < len(scope) and depth:
            depth += 1 if scope[index] == "{" else -1 if scope[index] == "}" else 0
            index += 1
        if depth == 0:
            results.append((match.group(1), match.group(2), scope[match.end() : index - 1]))
    return results


def _usda_vector(block: str, attribute: str, length: int, default: tuple[float, ...]) -> tuple[float, ...]:
    match = re.search(rf'\b{re.escape(attribute)}\s*=\s*\(([^)]+)\)', block)
    return _floats(match.group(1).replace(",", " ") if match else None, length, default)


def import_isaac_usda(path: str | Path) -> EnvironmentPackage:
    """Import the explicit Cube/Xform subset used by Open-X-Sim USDA scenes."""

    source = Path(path).expanduser().resolve()
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise EnvironmentImportError(f"Isaac USDA read failed for {source}: {exc}") from exc
    if not text.startswith("#usda 1.0"):
        raise EnvironmentImportError("Isaac native importer currently requires ASCII USDA")
    scope = _usda_scope(text, "Objects")
    if scope is None:
        raise EnvironmentImportError("Isaac USDA has no /World/Objects scope")
    assets: list[AssetBundle] = []
    objects: list[SceneObject] = []
    for prim_type, raw_name, block in _usda_child_blocks(scope):
        name = _valid_identifier(raw_name, "isaac")
        asset_id = _valid_identifier(f"{name}_asset", "asset")
        position = _usda_vector(block, "double3 xformOp:translate", 3, (0.0, 0.0, 0.0))
        scale = _usda_vector(block, "double3 xformOp:scale", 3, (1.0, 1.0, 1.0))
        orientation_match = re.search(r"quatd xformOp:orient\s*=\s*\(([^,]+),\s*\(([^)]+)\)\)", block)
        orientation = (
            (float(orientation_match.group(1)),) + _floats(orientation_match.group(2).replace(",", " "), 3, (0, 0, 0))
            if orientation_match
            else (1.0, 0.0, 0.0, 0.0)
        )
        if prim_type == "Cube":
            color_match = re.search(r"primvars:displayColor\s*=\s*\[\(([^)]+)\)\]", block)
            color = _floats(color_match.group(1).replace(",", " ") if color_match else None, 3, (0.8, 0.8, 0.8))
            representation = AssetRepresentation(
                format="primitive_box",
                uri="primitive://box",
                metadata={"half_size_m": [value / 2.0 for value in scale], "color_rgb": list(color)},
            )
            object_scale = (1.0, 1.0, 1.0)
        else:
            reference = re.search(r"@([^@]+)@", block)
            if reference is None:
                raise EnvironmentImportError(f"Isaac Xform has no external reference: {raw_name}")
            asset_path = (source.parent / reference.group(1)).resolve()
            if not asset_path.is_file():
                raise EnvironmentImportError(f"Isaac referenced asset is missing: {asset_path}")
            representation = AssetRepresentation(
                format=asset_path.suffix.lstrip("."),
                uri=str(asset_path),
                backend="isaacsim",
            )
            object_scale = scale
        assets.append(
            AssetBundle(
                asset_id=asset_id,
                category=prim_type.lower(),
                representations=(representation,),
                source={"kind": "isaac_usda", "path": str(source)},
            )
        )
        objects.append(
            SceneObject(
                instance_id=name,
                asset_id=asset_id,
                pose=Pose(position=position, orientation_wxyz=orientation),
                scale=object_scale,
            )
        )
    if not objects:
        raise EnvironmentImportError("Isaac USDA Objects scope contains no supported prims")
    return _native_package(
        package_id=source.stem,
        backend="isaacsim",
        source=source,
        objects=objects,
        assets=assets,
        gravity=(0.0, 0.0, -9.81),
        contract=None,
        metadata={"source_schema": "usda_subset", "imported_prim_types": [type_ for type_, _, _ in _usda_child_blocks(scope)]},
    )


def import_environment(path: str | Path, *, source_backend: str | None = None) -> EnvironmentPackage:
    """Dispatch a compile manifest or a supported native environment."""

    source = Path(path).expanduser().resolve()
    if source.name == "compile_manifest.json":
        return import_compile_manifest(source)
    sibling_manifest = source.parent / "compile_manifest.json"
    if source_backend is None and sibling_manifest.is_file():
        try:
            result = CompileResult.read(sibling_manifest)
            if Path(result.artifact_path).resolve() == source:
                return import_compile_manifest(sibling_manifest)
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            pass
    backend = (source_backend or "").lower()
    if backend in {"mujoco", "mjcf"} or source.suffix.lower() == ".xml":
        return import_mjcf(source)
    if backend in {"isaac", "isaacsim", "usd"} or source.suffix.lower() == ".usda":
        return import_isaac_usda(source)
    if source.suffix.lower() == ".json":
        data = json.loads(source.read_text(encoding="utf-8"))
        if data.get("schema") == "agenticsim.sapien_scene.v1" or backend in {"sapien", "sapien3"}:
            return import_sapien_scene(source)
        if data.get("schema") == "agenticsim.metasim_scenario.v1" or backend == "metasim":
            return import_metasim_scenario(source)
        package_path = data.get("package_path")
        if package_path:
            return EnvironmentPackage.read_json(package_path)
        if data.get("schema_version"):
            return EnvironmentPackage.from_dict(data)
    if backend == "metasim" and source.suffix.lower() == ".py":
        scenario_json = source.with_name("scenario.json")
        if scenario_json.is_file():
            return import_metasim_scenario(scenario_json)
    raise EnvironmentImportError(f"unsupported environment import: {source}")
