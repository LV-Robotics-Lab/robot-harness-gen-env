"""Deterministic SceneIR + immutable assets to package-relative Genesis inputs.

Entity frames are geometry centres, except structural supports whose frame is the
upper surface centre. Normalized URDF origins are XY-centred at the bottom plane.
Unknown placement is resolved only by the declared structural/on-support policy.
"""

import hashlib
import json
import math
from pathlib import Path
from typing import Literal

from pydantic import Field

from .contracts import ArtifactRef, Model, SceneIR, Sha256, Source
from .genesis_runtime import RuntimeEntity, RuntimeMember, RuntimeRelation, RuntimeScene


class ResolvedAsset(Model):
    entity_id: str
    version_sha256: Sha256
    acquisition_source: Source
    selection: Literal["exact", "cousin"]


class ResolvedAssetSet(Model):
    scene_ir: ArtifactRef
    assets: tuple[ResolvedAsset, ...]


class StructuralPolicy(Model):
    thickness_m: float = Field(gt=0)
    surface_height_m: float = Field(gt=0)
    friction: float = Field(ge=0)


class CompiledScene(Model):
    runtime_scene: RuntimeScene
    scene_ir: ArtifactRef
    resolved_assets: ResolvedAssetSet
    policy: StructuralPolicy
    defaults_applied: tuple[str, ...]
    receipt: ArtifactRef
    physical_evaluated: Literal[False] = False


def compile_scene(scene_ref, assets, *, registry, store, output_root, policy, seed):
    scene = SceneIR.model_validate_json(store.read_artifact(scene_ref))
    if any(entity.pose.yaw_degrees is None for entity in scene.entities):
        raise ValueError("unresolved scene yaw requires grounding")
    if assets.scene_ir != scene_ref:
        raise ValueError("resolved assets belong to another scene")
    foreground = {e.id for e in scene.entities if e.role == "foreground"}
    if {a.entity_id for a in assets.assets} != foreground or len(assets.assets) != len(foreground):
        raise ValueError("resolved asset set must cover each foreground entity exactly once")
    if any(e.articulation_state is not None for e in scene.entities):
        raise ValueError("unsupported articulation profile")
    if any(r.relation == "inside" for r in scene.relations):
        raise ValueError("unsupported inside profile")
    output = Path(output_root)
    if not output.is_absolute() or any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("compile root must be absolute and non-symbolic")
    versions, dimensions, member_data, members = {}, {}, {}, []
    for item in assets.assets:
        version = registry.inspect(item.version_sha256)
        report = json.loads(store.read_artifact(version.normalization_report))
        dimensions[item.entity_id] = tuple(report["dimensions_m"])
        if len(dimensions[item.entity_id]) != 3 or any(
            type(v) not in (float, int) or not math.isfinite(v) or v <= 0
            for v in dimensions[item.entity_id]
        ):
            raise ValueError("invalid normalized asset dimensions")
        versions[item.entity_id] = version
        prefix = f"assets/{item.entity_id}/{version.version_sha256}"
        for member in version.files:
            name = f"{prefix}/{member.path}"
            # Registry owns reference validation; no path is reconstructed from model strings.
            raw = store.read_artifact(member.artifact)
            member_data[name] = raw
            members.append(
                RuntimeMember(
                    path=name, sha256=member.artifact.sha256, size_bytes=member.artifact.size_bytes
                )
            )
    by_id = {e.id: e for e in scene.entities}
    defaults, runtime, frames = [], {}, {}

    def resolve(entity_id):
        if entity_id in runtime:
            return
        entity = by_id[entity_id]
        if entity.role == "structural_support":
            if entity.pose.frame != "world":
                raise ValueError("structural support requires world frame")
            dims = entity.dimensions
            if dims is None or dims[0] is None or dims[1] is None:
                raise ValueError("structural width and length must be explicit")
            thickness = dims[2] if dims[2] is not None else policy.thickness_m
            pos = tuple(
                v if v is not None else (policy.surface_height_m if i == 2 else 0.0)
                for i, v in enumerate(entity.pose.position)
            )
            if dims[2] is None:
                defaults.append(f"{entity_id}.thickness=deployment.structural_policy")
            if any(v is None for v in entity.pose.position):
                defaults.append(f"{entity_id}.world_surface_origin=deployment.structural_policy")
            frames[entity_id] = (pos, entity.pose.yaw_degrees)
            angle = math.radians(entity.pose.yaw_degrees) / 2
            runtime[entity_id] = RuntimeEntity(
                id=entity_id,
                category=entity.category,
                kind="structural_box",
                position_m=(pos[0], pos[1], pos[2] - thickness / 2),
                orientation_wxyz=(math.cos(angle), 0.0, 0.0, math.sin(angle)),
                size_m=(dims[0], dims[1], thickness),
                friction=policy.friction,
            )
            return
        dims = dimensions[entity_id]
        if entity.dimensions is not None and any(
            wanted is not None and not math.isclose(wanted, actual, abs_tol=1e-6)
            for wanted, actual in zip(entity.dimensions, dims, strict=True)
        ):
            raise ValueError("asset dimensions conflict with explicit scene intent")
        frame = entity.pose.frame
        if frame != "world":
            resolve(frame)
        origin, parent_yaw = frames[frame] if frame != "world" else ((0.0, 0.0, 0.0), 0.0)
        on = [r for r in scene.relations if r.source == entity_id and r.relation == "on"]
        if len(on) > 1:
            raise ValueError("multiple support targets")
        position = list(entity.pose.position)
        if any(value is None for value in position[:2]):
            raise ValueError("unresolved foreground XY requires grounding")
        if any(v is None for v in position):
            if not on or on[0].target != frame or by_id[frame].role != "structural_support":
                raise ValueError("unresolved foreground position")
            position = [
                v if v is not None else (dims[2] / 2 if i == 2 else 0.0)
                for i, v in enumerate(position)
            ]
            defaults.append(
                f"{entity_id}.unknown_position=on_support_centre_and_measured_half_height"
            )
        theta = math.radians(parent_yaw)
        centre = (
            origin[0] + math.cos(theta) * position[0] - math.sin(theta) * position[1],
            origin[1] + math.sin(theta) * position[0] + math.cos(theta) * position[1],
            origin[2] + position[2],
        )
        yaw = parent_yaw + entity.pose.yaw_degrees
        frames[entity_id] = (centre, yaw)
        version = versions[entity_id]
        prefix = f"assets/{entity_id}/{version.version_sha256}"
        if f"{prefix}/physics.json" not in member_data:
            raise ValueError("missing physical metadata")
        angle = math.radians(yaw) / 2
        runtime[entity_id] = RuntimeEntity(
            id=entity_id,
            category=entity.category,
            kind="rigid",
            position_m=(centre[0], centre[1], centre[2] - dims[2] / 2),
            orientation_wxyz=(math.cos(angle), 0.0, 0.0, math.sin(angle)),
            urdf_path=f"{prefix}/{version.entrypoint}",
            physics_path=f"{prefix}/physics.json",
            version_sha256=version.version_sha256,
        )

    for entity in scene.entities:
        resolve(entity.id)
    runtime_scene = RuntimeScene(
        seed=seed,
        scene_ir_sha256=scene_ref.sha256,
        entities=tuple(runtime[e.id] for e in scene.entities),
        members=tuple(members),
        relations=tuple(
            RuntimeRelation(source=r.source, target=r.target, relation=r.relation)
            for r in scene.relations
        ),
    )
    output.mkdir(parents=True, exist_ok=False)
    for name, raw in member_data.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    raw_scene = runtime_scene.model_dump_json().encode()
    (output / "scene.json").write_bytes(raw_scene)
    receipt = store.write_artifact(
        json.dumps(
            {
                "source": "harness_structural_geometry_and_immutable_assets",
                "scene_ir": scene_ref.model_dump(),
                "scene_sha256": hashlib.sha256(raw_scene).hexdigest(),
                "assets": assets.model_dump(mode="json"),
                "policy": policy.model_dump(),
                "defaults_applied": defaults,
                "physical_evaluated": False,
            },
            sort_keys=True,
        ).encode(),
        "application/json",
    )
    return CompiledScene(
        runtime_scene=runtime_scene,
        scene_ir=scene_ref,
        resolved_assets=assets,
        policy=policy,
        defaults_applied=tuple(defaults),
        receipt=receipt,
    )
