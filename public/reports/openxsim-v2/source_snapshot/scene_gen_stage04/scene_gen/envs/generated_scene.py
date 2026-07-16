"""Load a ResolvedSceneSpec through existing RoboTwin actor utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..schema import ResolvedSceneSpec


def _coerce_resolved(value: ResolvedSceneSpec | dict[str, Any] | str | Path) -> ResolvedSceneSpec:
    if isinstance(value, ResolvedSceneSpec):
        return value
    if isinstance(value, (str, Path)):
        return ResolvedSceneSpec.model_validate_json(Path(value).read_text(encoding="utf-8"))
    return ResolvedSceneSpec.model_validate(value)


def load_resolved_scene(task: Any, resolved: ResolvedSceneSpec | dict[str, Any] | str | Path) -> dict[str, Any]:
    """Instantiate only compiler-resolved assets; no user code is executed."""

    import sapien.core as sapien
    from envs.utils import create_actor, create_sapien_urdf_obj

    scene = _coerce_resolved(resolved)
    actors: dict[str, Any] = {}
    for item in scene.objects:
        pose = sapien.Pose(item.pose.position_m, item.pose.orientation_wxyz)
        if item.load_type == "urdf":
            actor = create_sapien_urdf_obj(
                task,
                pose=pose,
                modelname=item.asset_id,
                modelid=item.model_id,
                fix_root_link=item.is_static,
            )
        else:
            actor = create_actor(
                task,
                pose=pose,
                modelname=item.asset_id,
                model_id=item.model_id,
                convex=True,
                is_static=item.is_static,
            )
        if actor is None:
            raise RuntimeError(f"RoboTwin failed to load {item.asset_id}/model{item.model_id}")
        actor.set_name(item.object_id)
        actors[item.object_id] = actor
        if hasattr(task, "prohibited_area"):
            width, depth, _ = item.dimensions_m
            x, y, _ = item.pose.position_m
            task.prohibited_area.append([x - width / 2.0, y - depth / 2.0, x + width / 2.0, y + depth / 2.0])
    return actors
