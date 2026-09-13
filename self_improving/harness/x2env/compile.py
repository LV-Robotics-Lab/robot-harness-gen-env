"""Deterministic SceneIR + immutable assets to package-relative Genesis inputs.

Entity frames are geometry centres, except structural supports whose frame is the
upper surface centre. Normalized URDF origins are XY-centred at the bottom plane.
Unknown placement is resolved only by the declared structural/on-support policy.
"""

import hashlib
import json
import math
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from .contracts import ArtifactRef, Model, SceneIR, Sha256, Source
from .genesis_runtime import (
    RuntimeEntity,
    RuntimeMember,
    RuntimeRelation,
    RuntimeScene,
    RuntimeSceneV2,
    RuntimeSupportBinding,
)


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
    runtime_scene: Annotated[RuntimeScene | RuntimeSceneV2, Field(discriminator="schema_version")]
    scene_ir: ArtifactRef
    resolved_assets: ResolvedAssetSet
    policy: StructuralPolicy
    defaults_applied: tuple[str, ...]
    receipt: ArtifactRef
    physical_evaluated: Literal[False] = False


def _prepare_scene(scene_ref, assets, *, registry, store, policy, seed):
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
    dynamic_support = any(
        r.relation == "on" and by_id[r.target].role == "foreground" for r in scene.relations
    )
    geometry, bindings = {}, []

    def add_member(name, raw):
        member_data[name] = raw
        members.append(
            RuntimeMember(path=name, sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
        )

    if dynamic_support:
        from .measured_support import (
            measure_support_surfaces_from_members,
            read_asset_geometry_from_members,
            select_unique_support_surface,
        )

        add_member("support/scene-ir.json", store.read_artifact(scene_ref))
        for name, version in versions.items():
            refs = {m.path: m.artifact for m in version.files}
            geometry[name] = read_asset_geometry_from_members(
                version, lambda path, refs=refs: store.read_artifact(refs[path])
            )
            low, high = geometry[name]["visual_bounds_m"]
            measured_dimensions = tuple(hi - lo for lo, hi in zip(low, high, strict=True))
            if any(
                not math.isclose(measured, declared, abs_tol=1e-6)
                for measured, declared in zip(measured_dimensions, dimensions[name], strict=True)
            ):
                raise ValueError("normalized asset dimensions disagree with authored geometry")
            dimensions[name] = measured_dimensions
            add_member(f"support/{name}.asset.json", version.model_dump_json().encode())
        for entity_id in foreground:
            if (
                len([r for r in scene.relations if r.source == entity_id and r.relation == "on"])
                != 1
            ):
                raise ValueError("each dynamic entity needs exactly one support")
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
        measured_target = dynamic_support and on and by_id[on[0].target].role == "foreground"
        if dynamic_support and on:
            resolve(on[0].target)
        position = list(entity.pose.position)
        if any(value is None for value in position[:2]):
            raise ValueError("unresolved foreground XY requires grounding")
        if measured_target and position[2] is None:
            if frame != on[0].target:
                raise ValueError("unresolved dynamic support Z requires target-local frame")
            # Placeholder only for XY transformation; actual Z comes from selected geometry below.
            position[2] = 0.0
        elif any(v is None for v in position):
            if not on or on[0].target != frame or by_id[frame].role != "structural_support":
                raise ValueError("unresolved foreground position")
            measured_height = (
                (geometry[entity_id]["visual_center_m"][2] - geometry[entity_id]["bounds_m"][0][2])
                if dynamic_support
                else dims[2] / 2
            )
            position = [
                v if v is not None else (measured_height if i == 2 else 0.0)
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
        version = versions[entity_id]
        prefix = f"assets/{entity_id}/{version.version_sha256}"
        if f"{prefix}/physics.json" not in member_data:
            raise ValueError("missing physical metadata")
        angle = math.radians(yaw) / 2
        orientation = (math.cos(angle), 0.0, 0.0, math.sin(angle))
        offset = (
            geometry[entity_id]["visual_center_m"] if dynamic_support else (0.0, 0.0, dims[2] / 2)
        )
        radians = math.radians(yaw)
        rotated_offset = (
            math.cos(radians) * offset[0] - math.sin(radians) * offset[1],
            math.sin(radians) * offset[0] + math.cos(radians) * offset[1],
            offset[2],
        )
        urdf_position = tuple(c - o for c, o in zip(centre, rotated_offset, strict=True))
        if not dynamic_support:
            # Preserve historical v1 bytes, including signed zero in known XY coordinates.
            urdf_position = (centre[0], centre[1], centre[2] - dims[2] / 2)
        if dynamic_support:
            target_id = on[0].target
            target = runtime[target_id]
            selection = dict(
                schema_version="x2env.support_selection.v1",
                source_id=entity_id,
                target_id=target_id,
                source_version_sha256=version.version_sha256,
                target_version_sha256=target.version_sha256,
                source_geometry_center_m=list(offset),
                target_geometry_center_m=geometry[target_id]["visual_center_m"]
                if measured_target
                else None,
                known_z=entity.pose.position[2] is not None,
                selection_basis="unique_feasible" if measured_target else "structural_top",
                physical_evaluated=False,
            )
            surface_path, surface_sha = None, None
            if measured_target:
                target_version = versions[target_id]
                refs = {m.path: m.artifact for m in target_version.files}
                surfaces = measure_support_surfaces_from_members(
                    target_version, lambda path: store.read_artifact(refs[path])
                )
                surface, pose, choices = select_unique_support_surface(
                    surfaces,
                    source_geometry=geometry[entity_id],
                    source_pose=dict(position_m=urdf_position, orientation_wxyz=orientation),
                    target_pose=dict(
                        position_m=target.position_m, orientation_wxyz=target.orientation_wxyz
                    ),
                    known_z=selection["known_z"],
                )
                selection.update(choices)
                urdf_position = tuple(pose["position_m"])
                centre = tuple(p + o for p, o in zip(urdf_position, rotated_offset, strict=True))
                surface_path = f"support/{entity_id}.surface.json"
                raw = surface.model_dump_json().encode()
                surface_sha = hashlib.sha256(raw).hexdigest()
                add_member(surface_path, raw)
                if not selection["known_z"]:
                    defaults.append(f"{entity_id}.unknown_z=measured_support_surface")
            else:
                plane = target.position_m[2] + target.size_m[2] / 2
                if abs(urdf_position[2] + geometry[entity_id]["bounds_m"][0][2] - plane) > 1e-5:
                    raise ValueError("known Z conflicts with structural support plane")
            selection.update(
                source_urdf_position_m=list(urdf_position),
                target_urdf_position_m=list(target.position_m),
            )
            selection_path = f"support/{entity_id}.selection.json"
            add_member(selection_path, json.dumps(selection, sort_keys=True).encode())
            bindings.append(
                RuntimeSupportBinding(
                    kind="measured_surface" if measured_target else "structural_top",
                    source_id=entity_id,
                    target_id=target_id,
                    source_version_sha256=version.version_sha256,
                    source_asset_record_path=f"support/{entity_id}.asset.json",
                    target_version_sha256=target.version_sha256,
                    target_asset_record_path=f"support/{target_id}.asset.json"
                    if measured_target
                    else None,
                    surface_path=surface_path,
                    surface_sha256=surface_sha,
                    selection_receipt_path=selection_path,
                )
            )
        frames[entity_id] = (centre, yaw)
        runtime[entity_id] = RuntimeEntity(
            id=entity_id,
            category=entity.category,
            kind="rigid",
            position_m=urdf_position,
            orientation_wxyz=orientation,
            urdf_path=f"{prefix}/{version.entrypoint}",
            physics_path=f"{prefix}/physics.json",
            version_sha256=version.version_sha256,
        )

    for entity in scene.entities:
        resolve(entity.id)
    runtime_scene = (RuntimeSceneV2 if dynamic_support else RuntimeScene)(
        seed=seed,
        scene_ir_sha256=scene_ref.sha256,
        entities=tuple(runtime[e.id] for e in scene.entities),
        members=tuple(members),
        relations=tuple(
            RuntimeRelation(source=r.source, target=r.target, relation=r.relation)
            for r in scene.relations
        ),
        **(
            {"scene_ir_path": "support/scene-ir.json", "support_bindings": tuple(bindings)}
            if dynamic_support
            else {}
        ),
    )
    return scene, runtime_scene, member_data, defaults, frames, dimensions


def resolve_measured_layout(scene_ref, assets, *, registry, store, policy, seed):
    """Read-only dynamic layout resolution; no files, CAS writes or registration.

    The runtime/proofs bind the supplied candidate SceneIR, while ``scene`` only fills
    unknown fields. Callers own the new scene revision, provenance and authorization.
    """
    original, runtime, member_data, _, frames, dimensions = _prepare_scene(
        scene_ref, assets, registry=registry, store=store, policy=policy, seed=seed
    )
    if not isinstance(runtime, RuntimeSceneV2):
        raise ValueError("measured layout requires a dynamic support edge")
    document = original.model_dump(mode="json")
    runtime_by_id = {e.id: e for e in runtime.entities}
    for entity in document["entities"]:
        name, frame = entity["id"], entity["pose"]["frame"]
        centre, _ = frames[name]
        origin, yaw = frames[frame] if frame != "world" else ((0, 0, 0), 0)
        delta = [c - o for c, o in zip(centre, origin, strict=True)]
        theta = math.radians(yaw)
        local = [
            math.cos(theta) * delta[0] + math.sin(theta) * delta[1],
            -math.sin(theta) * delta[0] + math.cos(theta) * delta[1],
            delta[2],
        ]
        entity["pose"]["position"] = [
            actual if old is None else old
            for old, actual in zip(entity["pose"]["position"], local, strict=True)
        ]
        actual_dimensions = dimensions[name] if name in dimensions else runtime_by_id[name].size_m
        entity["dimensions"] = [
            actual if old is None else old
            for old, actual in zip(
                entity["dimensions"] or [None] * 3, actual_dimensions, strict=True
            )
        ]
    return {
        "scene": SceneIR.model_validate_json(json.dumps(document)),
        "runtime_scene": runtime,
        "support_proofs": {
            name: raw for name, raw in member_data.items() if name.startswith("support/")
        },
    }


def compile_scene(scene_ref, assets, *, registry, store, output_root, policy, seed):
    output = Path(output_root)
    if not output.is_absolute() or any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("compile root must be absolute and non-symbolic")
    _, runtime_scene, member_data, defaults, _, _ = _prepare_scene(
        scene_ref, assets, registry=registry, store=store, policy=policy, seed=seed
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
                **(
                    {
                        "support_artifacts": {
                            name: store.write_artifact(raw, "application/json").model_dump()
                            for name, raw in member_data.items()
                            if name.startswith("support/")
                        }
                    }
                    if isinstance(runtime_scene, RuntimeSceneV2)
                    else {}
                ),
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
