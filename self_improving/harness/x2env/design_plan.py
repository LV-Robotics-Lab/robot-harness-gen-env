"""Classify bounded design authority without accepting a scene or erasing unknowns."""

from typing import Literal

from .contracts import Model


class DesignRule(Model):
    entity_id: str
    path: str
    basis: Literal[
        "deployment_structural_default", "on_geometry_derived", "asset_anchor", "media_layout"
    ]
    value: float | None = None


class DesignPlan(Model):
    resolved_unknown_indices: tuple[int, ...]
    rules: tuple[DesignRule, ...]
    requires_media: bool


def classify_design_unknowns(proposal, policy, structural_policy=None):
    if not policy.enabled or proposal.scene is None:
        raise ValueError("design_grounding_disabled")
    scene = proposal.scene
    supports = [e for e in scene.entities if e.role == "structural_support"]
    foreground = [e for e in scene.entities if e.role == "foreground"]
    if (
        len(scene.entities) != 2
        or len(supports) != 1
        or len(foreground) != 1
        or any(e.articulation_state is not None for e in scene.entities)
    ):
        raise ValueError("unsupported_grounding_scope")
    support, obj = supports[0], foreground[0]
    on = [r for r in scene.relations if r.source == obj.id and r.relation == "on"]
    if (
        support.category not in {"table", "worktop", "counter"}
        or support.pose.frame != "world"
        or len(on) != 1
        or on[0].target != support.id
        or any(r.relation == "inside" for r in scene.relations)
    ):
        raise ValueError("unsupported_grounding_support")
    defaults = policy.structural_defaults_enabled
    if defaults and structural_policy is None:
        raise ValueError("missing_structural_policy")
    rules = []
    paths = {}
    for entity in scene.entities:
        prefix = "scene.entities." + entity.id
        missing = []
        for i, value in enumerate(entity.dimensions or (None, None, None)):
            if value is not None:
                continue
            basis = "asset_anchor" if entity == obj else "media_layout"
            fixed = None
            if entity == support and i == 2 and defaults:
                basis, fixed = "deployment_structural_default", structural_policy.thickness_m
            missing.append(
                DesignRule(entity_id=entity.id, path=f"dimensions[{i}]", basis=basis, value=fixed)
            )
        for i, value in enumerate(entity.pose.position):
            if value is not None:
                continue
            basis, fixed = "media_layout", None
            if entity == support and defaults:
                basis = "deployment_structural_default"
                fixed = policy.world_anchor_xy[i] if i < 2 else structural_policy.surface_height_m
            elif entity == obj and i == 2 and entity.pose.frame == support.id:
                basis = "on_geometry_derived"
            missing.append(
                DesignRule(
                    entity_id=entity.id, path=f"pose.position[{i}]", basis=basis, value=fixed
                )
            )
        if entity.pose.yaw_degrees is None:
            basis = (
                "deployment_structural_default"
                if entity == support and defaults
                else "media_layout"
            )
            missing.append(
                DesignRule(
                    entity_id=entity.id,
                    path="pose.yaw_degrees",
                    basis=basis,
                    value=policy.world_anchor_yaw_degrees
                    if basis == "deployment_structural_default"
                    else None,
                )
            )
        rules.extend(missing)
        for rule in missing:
            paths[prefix + "." + rule.path] = [rule]
        for field in ("dimensions", "pose", "pose.position", "pose.yaw_degrees"):
            selected = [
                r
                for r in missing
                if r.path == field
                or r.path.startswith(field + "[")
                or r.path.startswith(field + ".")
            ]
            if selected:
                paths[prefix + "." + field] = selected
    indices = []
    for i, unknown in enumerate(proposal.unknowns):
        if not unknown.critical:
            continue
        selected = paths.get(unknown.field)
        # Retain the original bounded wildcard contract; expand only existing entities.
        if unknown.field in {"scene.entities[*].dimensions", "scene.entities[*].pose"}:
            field = unknown.field.split("].")[1]
            selected = [r for r in rules if r.path.startswith(field)]
        if not selected:
            raise ValueError("grounding_unknown_field_not_designable")
        if unknown.reason_kind == "unspecified":
            if any(
                r.basis not in {"deployment_structural_default", "on_geometry_derived"}
                for r in selected
            ):
                raise ValueError("grounding_requires_clarification")
        elif unknown.reason_kind not in {"scale_unobservable", "pose_unobservable"}:
            raise ValueError("grounding_requires_clarification")
        if unknown.reason_kind == "scale_unobservable" and any(
            not r.path.startswith("dimensions") for r in selected
        ):
            raise ValueError("grounding_unknown_field_not_designable")
        if unknown.reason_kind == "pose_unobservable" and any(
            not r.path.startswith("pose") for r in selected
        ):
            raise ValueError("grounding_unknown_field_not_designable")
        if any(
            r.entity_id == obj.id
            and r.path == "pose.position[2]"
            and r.basis != "on_geometry_derived"
            for r in selected
        ):
            # World-frame visual estimates remain supported, not single-axis contact inference.
            if unknown.field.endswith("[2]"):
                raise ValueError("unsupported_on_coordinate_frame")
        indices.append(i)
    if not indices:
        raise ValueError("no_authorized_design_unknowns")
    return DesignPlan(
        resolved_unknown_indices=tuple(indices),
        rules=tuple(rules),
        requires_media=any(r.basis in {"media_layout", "asset_anchor"} for r in rules),
    )
