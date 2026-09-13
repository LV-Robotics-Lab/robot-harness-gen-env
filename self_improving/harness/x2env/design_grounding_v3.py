"""Versioned measured-DAG layout choices; the managed model never selects height."""

import json
import math
from typing import Literal

from pydantic import Field

from .assets import AssetRegistry
from .contracts import FieldProvenance, Model, SceneIR
from .design_grounding_v2 import _classify_design


class MeasuredLayoutChoice(Model):
    entity_id: str
    path: Literal[
        "dimensions[0]", "dimensions[1]", "pose.position[0]", "pose.position[1]", "pose.yaw_degrees"
    ]
    value: float = Field(allow_inf_nan=False)


class MeasuredLayoutValues(Model):
    choices: tuple[MeasuredLayoutChoice, ...] = Field(max_length=40)


def finalize_measured_scene(base, resolved, bundle):
    """Append derivation provenance outside the geometry solver, retaining original entries."""
    source = "image" if bundle.images else "video" if bundle.video else "text"
    ref = (
        bundle.images[0].source
        if bundle.images
        else bundle.video.source
        if bundle.video
        else bundle.text
    )
    if ref is None:
        raise ValueError("grounding_missing_input")
    entities = []
    for old, new in zip(base.entities, resolved.entities, strict=True):
        if old.id != new.id:
            raise ValueError("grounding_changed_entities")
        updates = {}
        for field in ("dimensions", "pose"):
            if getattr(old, field) == getattr(new, field):
                continue
            note = (
                "Immutable asset geometry dimensions; not real-world metric recovery."
                if field == "dimensions" and new.role == "foreground"
                else (
                    "Explicit generated structural dimensions and deployment thickness; "
                    "not metric recovery."
                )
                if field == "dimensions"
                else (
                    "Authorized generated XY/yaw; Z from measured support geometry or "
                    "explicit structural policy; not metric recovery."
                )
            )
            evidence = FieldProvenance(
                source=source,
                input_sha256=ref.sha256,
                kind="inferred",
                media_index=0 if source == "image" else None,
                frame_index=0 if source == "video" else None,
                note=note,
            )
            updates[field] = (*getattr(old.provenance, field), evidence)
        entities.append(
            new.model_copy(update={"provenance": old.provenance.model_copy(update=updates)})
        )
    return SceneIR.model_validate_json(
        resolved.model_copy(update={"entities": tuple(entities)}).model_dump_json()
    )


def has_measured_support(scene):
    if scene is None:
        return False
    foreground = {e.id for e in scene.entities if e.role == "foreground"}
    return any(r.relation == "on" and r.target in foreground for r in scene.relations)


def ground_measured_scene(
    bundle_ref,
    proposal_ref,
    assets_ref,
    policy,
    *,
    store,
    backend,
    output_root,
    timeout=600,
    structural_policy=None,
):
    from .design_grounding_v2 import _ground_generated_scene

    return _ground_generated_scene(
        bundle_ref,
        proposal_ref,
        assets_ref,
        policy,
        store=store,
        backend=backend,
        output_root=output_root,
        timeout=timeout,
        structural_policy=structural_policy,
        measured=True,
    )


def classify_measured_design(proposal, policy, structural_policy=None):
    if not has_measured_support(proposal.scene):
        raise ValueError("measured grounding requires dynamic support")
    return _classify_design(proposal, policy, structural_policy, dynamic_support=True)


def bind_measured_assets(store, base, assets, plan):
    from .measured_support import read_asset_geometry_from_members

    foreground = {e.id: e for e in base.entities if e.role == "foreground"}
    if len(assets.assets) != len(foreground) or {a.entity_id for a in assets.assets} != set(
        foreground
    ):
        raise ValueError("grounding_asset_set_mismatch")
    bindings, dimensions = [], {}
    for asset in assets.assets:
        version = AssetRegistry(store).inspect(asset.version_sha256)
        refs = {m.path: m.artifact for m in version.files}
        geometry = read_asset_geometry_from_members(
            version, lambda name: store.read_artifact(refs[name])
        )
        low, high = geometry["visual_bounds_m"]
        dims = [b - a for a, b in zip(low, high, strict=True)]
        reported = json.loads(store.read_artifact(version.normalization_report)).get("dimensions_m")
        if (
            not isinstance(reported, list)
            or len(reported) != 3
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in reported)
            or any(
                not math.isclose(a, b, abs_tol=1e-6) for a, b in zip(dims, reported, strict=True)
            )
        ):
            raise ValueError("missing_measured_anchor_dimensions")
        entity = foreground[asset.entity_id]
        if version.category != entity.category:
            raise ValueError("grounding_asset_category_mismatch")
        if any(
            a is not None and not math.isclose(a, b, abs_tol=1e-6)
            for a, b in zip(entity.dimensions or (None,) * 3, dims, strict=True)
        ):
            raise ValueError("grounding_changed_anchor_scale")
        dimensions[entity.id] = dims
        bindings.append(
            dict(
                entity_id=entity.id,
                version_sha256=version.version_sha256,
                normalization_report=version.normalization_report.model_dump(),
                dimensions_m=dims,
                visual_center_m=geometry["visual_center_m"],
                bounds_m=geometry["bounds_m"],
            )
        )
    fixed = {}
    for rule in plan["rules"]:
        value = rule["value"]
        if rule["basis"] == "asset_geometry":
            value = dimensions[rule["entity_id"]][int(rule["path"][-2])]
        if value is not None:
            fixed[rule["entity_id"] + "." + rule["path"]] = value
    return sorted(bindings, key=lambda b: b["entity_id"]), fixed


def build_measured_candidate(base, values, policy, plan, fixed):
    allowed = {
        (r["entity_id"], r["path"])
        for r in plan["rules"]
        if r["basis"] == "simulation_design_choice"
    }
    choices = {(c.entity_id, c.path): c.value for c in values.choices}
    if len(choices) != len(values.choices) or set(choices) != allowed:
        raise ValueError("grounding_changed_authorized_choices")
    for (_, path), value in choices.items():
        if path.startswith("dimensions"):
            low, high = policy.support_extent_range_m
            if not low <= value <= high:
                raise ValueError("generated_extent_out_of_bounds")
        elif path.startswith("pose.position") and abs(value) > policy.position_abs_max_m:
            raise ValueError("generated_position_out_of_bounds")
        elif path == "pose.yaw_degrees" and not -180 <= value <= 180:
            raise ValueError("generated_yaw_out_of_bounds")
    document = base.model_dump(mode="json")
    for entity in document["entities"]:
        entity["dimensions"] = entity["dimensions"] or [None] * 3
        for rule in (r for r in plan["rules"] if r["entity_id"] == entity["id"]):
            path = rule["path"]
            value = choices.get((entity["id"], path), fixed.get(entity["id"] + "." + path))
            if value is None or rule["basis"] == "input_fixed":
                continue
            if path.startswith("dimensions"):
                entity["dimensions"][int(path[-2])] = value
            elif path.startswith("pose.position"):
                entity["pose"]["position"][int(path[-2])] = value
            else:
                entity["pose"]["yaw_degrees"] = value
    return SceneIR.model_validate_json(json.dumps(document))
