"""Load a ResolvedSceneSpec through existing RoboTwin actor utilities."""

from __future__ import annotations

import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..colors import COLOR_RGB
from ..schema import ResolvedSceneSpec


def _coerce_resolved(value: ResolvedSceneSpec | dict[str, Any] | str | Path) -> ResolvedSceneSpec:
    if isinstance(value, ResolvedSceneSpec):
        return value
    if isinstance(value, (str, Path)):
        return ResolvedSceneSpec.model_validate_json(Path(value).read_text(encoding="utf-8"))
    return ResolvedSceneSpec.model_validate(value)


def _runtime_modelname(item: Any) -> str:
    """Use a validated external asset directory when RoboTwin has no native copy."""

    native_directory = Path("assets/objects") / item.asset_id
    if native_directory.is_dir():
        return item.asset_id

    metadata_name = (
        "model_data.json" if item.model_id is None else f"model_data{item.model_id}.json"
    )
    source_paths = tuple(Path(path).resolve() for path in item.source_files)
    metadata_paths = [path for path in source_paths if path.name == metadata_name]
    if len(metadata_paths) != 1:
        return item.asset_id

    asset_directory = metadata_paths[0].parent
    if asset_directory.name != item.asset_id:
        return item.asset_id

    sources_are_valid = all(
        path.is_file() and path.is_relative_to(asset_directory) for path in source_paths
    )
    if not sources_are_valid:
        return item.asset_id
    return str(asset_directory)


def _validated_asset_roots(
    scene: ResolvedSceneSpec,
    asset_roots: Mapping[str, Path] | None,
) -> dict[str, Path] | None:
    if asset_roots is None:
        return None
    if not isinstance(asset_roots, Mapping):
        raise ValueError("asset_roots must be a mapping from object_id to Path")
    expected = {item.object_id for item in scene.objects}
    if set(asset_roots) != expected:
        raise ValueError("asset_roots must exactly cover every resolved object_id")
    result: dict[str, Path] = {}
    for item in scene.objects:
        raw_root = asset_roots[item.object_id]
        if not isinstance(raw_root, Path) or not raw_root.is_absolute():
            raise ValueError("snapshot asset root must be an absolute Path")
        root = raw_root.absolute()
        try:
            root_stat = root.lstat()
        except OSError as error:
            raise ValueError("snapshot asset root is unavailable") from error
        if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
            raise ValueError("snapshot asset root must be a real directory")
        if item.load_type == "urdf":
            try:
                urdf_stat = (root / "mobility.urdf").lstat()
            except OSError as error:
                raise ValueError(
                    "snapshot URDF model root does not contain mobility.urdf"
                ) from error
            if stat.S_ISLNK(urdf_stat.st_mode) or not stat.S_ISREG(urdf_stat.st_mode):
                raise ValueError("snapshot mobility.urdf must be a real regular file")
        elif root.name != item.asset_id:
            raise ValueError("snapshot asset root basename for a rigid object must equal asset_id")
        result[item.object_id] = root
    return result


def _collision_shapes(actor: Any) -> list[Any]:
    raw = getattr(actor, "actor", actor)
    link_getter = getattr(raw, "get_links", None)
    entities = list(link_getter()) if callable(link_getter) else [raw]

    shapes: list[Any] = []
    seen: set[int] = set()
    for entity in entities:
        candidates = [entity]
        component_getter = getattr(entity, "get_components", None)
        if callable(component_getter):
            candidates.extend(component_getter())
        else:
            candidates.extend(getattr(entity, "components", ()))

        for candidate in candidates:
            shape_getter = getattr(candidate, "get_collision_shapes", None)
            if callable(shape_getter):
                candidate_shapes = shape_getter()
            else:
                candidate_shapes = getattr(candidate, "collision_shapes", ())
            for shape in candidate_shapes:
                if id(shape) not in seen:
                    shapes.append(shape)
                    seen.add(id(shape))
    return shapes


def _apply_physical_material(task: Any, actor: Any, item: Any) -> int:
    values = (
        item.static_friction,
        item.dynamic_friction,
        item.restitution,
    )
    if all(value is None for value in values):
        return 0
    if any(value is None for value in values):
        raise RuntimeError(
            f"RoboTwin actor {item.object_id} has incomplete physical material metadata"
        )

    scene = getattr(task, "scene", None)
    material_creator = getattr(scene, "create_physical_material", None)
    if not callable(material_creator):
        raise RuntimeError(f"RoboTwin task cannot create physical material for {item.object_id}")

    physical_material = material_creator(
        float(item.static_friction),
        float(item.dynamic_friction),
        float(item.restitution),
    )
    shapes = _collision_shapes(actor)
    if not shapes:
        raise RuntimeError(f"RoboTwin actor {item.object_id} exposes no collision shapes")

    for shape in shapes:
        setter = getattr(shape, "set_physical_material", None)
        if not callable(setter):
            raise RuntimeError(
                f"RoboTwin collision shape for {item.object_id} "
                f"does not expose set_physical_material"
            )
        setter(physical_material)
    return len(shapes)


def _render_entities(actor: Any) -> list[Any]:
    raw = getattr(actor, "actor", actor)
    link_getter = getattr(raw, "get_links", None)
    if callable(link_getter):
        return list(link_getter())
    return [raw]


def _apply_color_override(actor: Any, color: str) -> int:
    rgb = COLOR_RGB.get(color)
    if rgb is None:
        raise RuntimeError(f"unsupported runtime color override: {color}")
    material_ids: set[int] = set()
    for entity in _render_entities(actor):
        components = getattr(entity, "components", ())
        for component in components:
            for shape in getattr(component, "render_shapes", ()):
                # A multi-material triangle mesh RAISES on the aggregate
                # .material getter (SAPIEN C++ side throws RuntimeError, so
                # getattr's default never applies) -- a hanger with mixed
                # materials killed the whole render on a colour-tint request
                # (2026-08-15). Its materials are reachable via .parts below.
                try:
                    candidates = [shape.material]
                except (RuntimeError, AttributeError):
                    candidates = []
                candidates.extend(
                    getattr(part, "material", None) for part in getattr(shape, "parts", ())
                )
                for material in candidates:
                    if material is None or id(material) in material_ids:
                        continue
                    material.base_color = [*rgb, 1.0]
                    material_ids.add(id(material))
    return len(material_ids)


def load_resolved_scene(
    task: Any,
    resolved: ResolvedSceneSpec | dict[str, Any] | str | Path,
    *,
    asset_roots: Mapping[str, Path] | None = None,
) -> dict[str, Any]:
    """Instantiate only compiler-resolved assets; no user code is executed."""

    scene = _coerce_resolved(resolved)
    snapshot_roots = _validated_asset_roots(scene, asset_roots)
    import sapien.core as sapien
    from envs.utils import create_actor, create_sapien_urdf_obj

    actors: dict[str, Any] = {}
    for item in scene.objects:
        pose = sapien.Pose(item.pose.position_m, item.pose.orientation_wxyz)
        modelname = (
            str(snapshot_roots[item.object_id])
            if snapshot_roots is not None
            else item.asset_id
            if item.load_type == "urdf"
            else _runtime_modelname(item)
        )
        if item.load_type == "urdf":
            actor = create_sapien_urdf_obj(
                task,
                pose=pose,
                modelname=modelname,
                # The snapshot mapping already names the catalog-selected model
                # directory. Passing the catalog model id would make RoboTwin
                # reinterpret it as a positional index among child directories.
                modelid=None if snapshot_roots is not None else item.model_id,
                fix_root_link=item.is_static,
            )
        else:
            actor = create_actor(
                task,
                pose=pose,
                modelname=modelname,
                model_id=item.model_id,
                convex=True,
                is_static=item.is_static,
            )
        if actor is None:
            raise RuntimeError(f"RoboTwin failed to load {item.asset_id}/model{item.model_id}")
        if item.mass_kg is not None:
            mass_setter = getattr(actor, "set_mass", None)
            if not callable(mass_setter):
                raise RuntimeError(f"RoboTwin actor {item.object_id} does not expose set_mass")
            mass_setter(float(item.mass_kg))
        _apply_physical_material(task, actor, item)
        if item.color and _apply_color_override(actor, item.color) == 0:
            raise RuntimeError(
                f"RoboTwin loaded {item.object_id} without a tintable render material"
            )
        if item.articulation_qpos:
            setter = getattr(actor, "set_qpos", None)
            if not callable(setter):
                raise RuntimeError(
                    f"RoboTwin actor {item.object_id} does not expose set_qpos for articulation"
                )
            setter(list(item.articulation_qpos))
            raw_articulation = getattr(actor, "actor", None)
            joint_getter = getattr(raw_articulation, "get_active_joints", None)
            if callable(joint_getter):
                for joint, target in zip(joint_getter(), item.articulation_qpos):
                    drive_properties = getattr(joint, "set_drive_properties", None)
                    drive_target = getattr(joint, "set_drive_target", None)
                    if callable(drive_properties):
                        drive_properties(stiffness=10000.0, damping=400.0, force_limit=5000.0)
                    if callable(drive_target):
                        drive_target(float(target))
        actor.set_name(item.object_id)
        actors[item.object_id] = actor
        if hasattr(task, "prohibited_area"):
            width, depth, _ = item.dimensions_m
            x, y, _ = item.pose.position_m
            task.prohibited_area.append(
                [x - width / 2.0, y - depth / 2.0, x + width / 2.0, y + depth / 2.0]
            )
    return actors
