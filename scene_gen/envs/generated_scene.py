"""Load a ResolvedSceneSpec through existing RoboTwin actor utilities."""

from __future__ import annotations

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
        "model_data.json"
        if item.model_id is None
        else f"model_data{item.model_id}.json"
    )
    source_paths = tuple(Path(path).resolve() for path in item.source_files)
    metadata_paths = [path for path in source_paths if path.name == metadata_name]
    if len(metadata_paths) != 1:
        return item.asset_id

    asset_directory = metadata_paths[0].parent
    if asset_directory.name != item.asset_id:
        return item.asset_id

    sources_are_valid = all(
        path.is_file() and path.is_relative_to(asset_directory)
        for path in source_paths
    )
    if not sources_are_valid:
        return item.asset_id
    return str(asset_directory)


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
                candidates = [getattr(shape, "material", None)]
                candidates.extend(
                    getattr(part, "material", None)
                    for part in getattr(shape, "parts", ())
                )
                for material in candidates:
                    if material is None or id(material) in material_ids:
                        continue
                    material.base_color = [*rgb, 1.0]
                    material_ids.add(id(material))
    return len(material_ids)


def load_resolved_scene(task: Any, resolved: ResolvedSceneSpec | dict[str, Any] | str | Path) -> dict[str, Any]:
    """Instantiate only compiler-resolved assets; no user code is executed."""

    import sapien.core as sapien
    from envs.utils import create_actor, create_sapien_urdf_obj

    scene = _coerce_resolved(resolved)
    actors: dict[str, Any] = {}
    for item in scene.objects:
        pose = sapien.Pose(item.pose.position_m, item.pose.orientation_wxyz)
        modelname = (
            item.asset_id
            if item.load_type == "urdf"
            else _runtime_modelname(item)
        )
        if item.load_type == "urdf":
            actor = create_sapien_urdf_obj(
                task,
                pose=pose,
                modelname=modelname,
                modelid=item.model_id,
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
            task.prohibited_area.append([x - width / 2.0, y - depth / 2.0, x + width / 2.0, y + depth / 2.0])
    return actors
