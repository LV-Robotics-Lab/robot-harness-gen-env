"""Explicit bounded generated layout, separate from the historical v1 design contract."""

import hashlib
import json
import math
import subprocess
import time
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .artifacts import artifact_closure
from .assets import AssetRegistry
from .compile import ResolvedAssetSet, StructuralPolicy
from .contracts import BackendProposal, FieldProvenance, InputBundle, Model, SceneIR

Finite = Annotated[float, Field(allow_inf_nan=False)]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class GeneratedLayoutPolicy(Model):
    enabled: bool = False
    mode: Literal["generated_layout"] = "generated_layout"
    structural_defaults_enabled: bool = False
    world_anchor_xy: tuple[Finite, Finite] | None = None
    world_anchor_yaw_degrees: (
        Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)] | None
    ) = None
    support_extent_range_m: tuple[Positive, Positive] = (0.05, 5.0)
    position_abs_max_m: Annotated[float, Field(gt=0, le=1000, allow_inf_nan=False)] = 5.0

    @model_validator(mode="after")
    def bounded_authority(self):
        if self.support_extent_range_m[0] >= self.support_extent_range_m[1]:
            raise ValueError("invalid generated extent range")
        if self.structural_defaults_enabled and (
            self.world_anchor_xy is None or self.world_anchor_yaw_degrees is None
        ):
            raise ValueError("structural defaults require explicit world anchor")
        return self


class GeneratedEntityValues(Model):
    id: str
    dimensions: tuple[Positive, Positive, Positive]
    frame: str
    position: tuple[Finite, Finite, Finite]
    yaw_degrees: Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]


class GroundingValuesV2(Model):
    entities: tuple[GeneratedEntityValues, ...] = Field(min_length=1, max_length=8)


def classify_generated_design(proposal, policy, structural_policy=None):
    """Enumerate authority from real missing fields, never trust advisory critical flags."""
    policy = GeneratedLayoutPolicy.model_validate(policy)
    if not policy.enabled or proposal.scene is None:
        raise ValueError("design_grounding_disabled")
    scene = proposal.scene
    if not 1 <= len(scene.entities) <= 8 or any(
        e.articulation_state is not None for e in scene.entities
    ):
        raise ValueError("unsupported_grounding_scope")
    by_id = {e.id: e for e in scene.entities}
    targets = {}
    for entity in scene.entities:
        if entity.role == "structural_support":
            if (
                entity.category not in {"table", "worktop", "counter"}
                or entity.pose.frame != "world"
            ):
                raise ValueError("unsupported_grounding_support")
        else:
            on = [r for r in scene.relations if r.source == entity.id and r.relation == "on"]
            if len(on) != 1 or on[0].target == entity.id:
                raise ValueError("unsupported_grounding_support")
            target = by_id[on[0].target]
            if target.role != "structural_support":
                raise ValueError("unsupported_dynamic_support_geometry")
            if entity.pose.frame not in ("world", target.id):
                raise ValueError("unsupported_on_coordinate_frame")
            targets[entity.id] = target.id
    if any(r.relation != "on" or by_id[r.source].role != "foreground" for r in scene.relations):
        raise ValueError("unsupported_grounding_relations")
    rules = []
    for entity in scene.entities:
        for kind, values in [
            ("dimensions", entity.dimensions or (None, None, None)),
            ("pose.position", entity.pose.position),
            ("pose.yaw_degrees", (entity.pose.yaw_degrees,)),
        ]:
            for axis, value in enumerate(values):
                path = kind if kind == "pose.yaw_degrees" else f"{kind}[{axis}]"
                basis = "input_fixed"
                if value is None:
                    if kind == "dimensions" and entity.role == "foreground":
                        basis = "asset_geometry"
                    elif (
                        kind == "dimensions"
                        and axis == 2
                        or kind == "pose.position"
                        and axis == 2
                        and entity.role == "structural_support"
                    ):
                        if structural_policy is None:
                            raise ValueError("missing_structural_policy")
                        basis = "deployment_default"
                        value = (
                            structural_policy.thickness_m
                            if kind == "dimensions"
                            else structural_policy.surface_height_m
                        )
                    elif kind == "pose.position" and axis == 2:
                        if entity.pose.frame != targets[entity.id]:
                            raise ValueError("unsupported_on_coordinate_frame")
                        basis = "support_geometry_derived"
                    elif (
                        entity.role == "structural_support"
                        and policy.structural_defaults_enabled
                        and kind.startswith("pose")
                    ):
                        basis = "deployment_default"
                        value = (
                            policy.world_anchor_yaw_degrees
                            if kind == "pose.yaw_degrees"
                            else policy.world_anchor_xy[axis]
                        )
                    else:
                        basis = "simulation_design_choice"
                rules.append({"entity_id": entity.id, "path": path, "basis": basis, "value": value})
    indices = []
    for index, unknown in enumerate(proposal.unknowns):
        if unknown.reason_kind == "conflict":
            raise ValueError("grounding_requires_clarification")
        from .design_plan import canonical_design_field

        field = canonical_design_field(scene, unknown.field)
        selected = []
        for rule in rules:
            prefix = "scene.entities." + rule["entity_id"] + "."
            full = prefix + rule["path"]
            wildcard = (
                prefix + field.split("].", 1)[1]
                if field in {"scene.entities[*].dimensions", "scene.entities[*].pose"}
                else field
            )
            if rule["basis"] != "input_fixed" and (
                full == wildcard
                or (
                    wildcard in {prefix + p for p in ("dimensions", "pose", "pose.position")}
                    and (full.startswith(wildcard + "[") or full.startswith(wildcard + "."))
                )
            ):
                selected.append(rule)
        if not selected:
            if unknown.critical:
                raise ValueError("grounding_unknown_field_not_designable")
            continue
        if unknown.reason_kind not in ("unspecified", "scale_unobservable", "pose_unobservable"):
            raise ValueError("grounding_requires_clarification")
        if unknown.reason_kind == "scale_unobservable" and any(
            not r["path"].startswith("dimensions") for r in selected
        ):
            raise ValueError("grounding_unknown_field_not_designable")
        if unknown.reason_kind == "pose_unobservable" and any(
            not r["path"].startswith("pose") for r in selected
        ):
            raise ValueError("grounding_unknown_field_not_designable")
        indices.append(index)
    return {"rules": rules, "resolved_unknown_indices": indices, "requires_media": False}


def bind_generated_assets(store, base, assets, plan):
    foreground = {e.id: e for e in base.entities if e.role == "foreground"}
    if len(assets.assets) != len(foreground) or {a.entity_id for a in assets.assets} != set(
        foreground
    ):
        raise ValueError("grounding_asset_set_mismatch")
    bindings = []
    for asset in assets.assets:
        version = AssetRegistry(store).inspect(asset.version_sha256)
        metrics = json.loads(store.read_artifact(version.normalization_report))
        dims = metrics.get("dimensions_m")
        if (
            not isinstance(dims, list)
            or len(dims) != 3
            or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in dims)
        ):
            raise ValueError("missing_measured_anchor_dimensions")
        if version.category != foreground[asset.entity_id].category:
            raise ValueError("grounding_asset_category_mismatch")
        bindings.append(
            {
                "entity_id": asset.entity_id,
                "version_sha256": version.version_sha256,
                "normalization_report": version.normalization_report.model_dump(),
                "dimensions_m": dims,
            }
        )
    bindings.sort(key=lambda b: b["entity_id"])
    dims = {b["entity_id"]: b["dimensions_m"] for b in bindings}
    fixed = {}
    for rule in plan["rules"]:
        value = rule["value"]
        if rule["basis"] == "asset_geometry":
            value = dims[rule["entity_id"]][int(rule["path"][-2])]
        if rule["basis"] == "support_geometry_derived":
            value = dims[rule["entity_id"]][2] / 2
        if value is not None:
            fixed[rule["entity_id"] + "." + rule["path"]] = value
    for key, entity in foreground.items():
        if any(
            a is not None and not math.isclose(a, b, abs_tol=1e-9, rel_tol=0)
            for a, b in zip(entity.dimensions or (None, None, None), dims[key])
        ):
            raise ValueError("grounding_changed_anchor_scale")
        if (
            entity.pose.frame != "world"
            and entity.pose.position[2] is not None
            and not math.isclose(entity.pose.position[2], dims[key][2] / 2, abs_tol=1e-9, rel_tol=0)
        ):
            raise ValueError("known_height_conflicts_with_on_geometry")
    return bindings, fixed


def apply_generated_values(base, values, policy, plan, bindings, fixed, bundle):
    by_id = {e.id: e for e in values.entities}
    if len(by_id) != len(values.entities) or set(by_id) != {e.id for e in base.entities}:
        raise ValueError("grounding_changed_entities")
    choices = []
    entities = []
    for entity in base.entities:
        value = by_id[entity.id]
        if value.frame != entity.pose.frame:
            raise ValueError("grounding_changed_frame")
        numbers = {
            **{f"dimensions[{i}]": v for i, v in enumerate(value.dimensions)},
            **{f"pose.position[{i}]": v for i, v in enumerate(value.position)},
            "pose.yaw_degrees": value.yaw_degrees,
        }
        for rule in (r for r in plan["rules"] if r["entity_id"] == entity.id):
            key = entity.id + "." + rule["path"]
            number = numbers[rule["path"]]
            if key in fixed and number != fixed[key]:
                raise ValueError("grounding_changed_authoritative_design_value")
            if rule["basis"] == "simulation_design_choice":
                if rule["path"].startswith("dimensions"):
                    low, high = policy.support_extent_range_m
                    if not low <= number <= high:
                        raise ValueError("generated_extent_out_of_bounds")
                elif (
                    rule["path"].startswith("pose.position")
                    and abs(number) > policy.position_abs_max_m
                ):
                    raise ValueError("generated_position_out_of_bounds")
                choices.append({**rule, "value": number})
        binding = next((b for b in bindings if b["entity_id"] == entity.id), None)
        if binding and tuple(binding["dimensions_m"]) != value.dimensions:
            raise ValueError("grounding_changed_anchor_scale")
        changed = []
        if entity.dimensions != value.dimensions:
            changed.append("dimensions")
        if entity.pose.position != value.position or entity.pose.yaw_degrees != value.yaw_degrees:
            changed.append("pose")
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
        provenance = FieldProvenance(
            source=source,
            input_sha256=ref.sha256,
            kind="inferred",
            media_index=0 if source == "image" else None,
            frame_index=0 if source == "video" else None,
            note="Explicit generated simulation design; not real-world scale recovery.",
        )
        entities.append(
            entity.model_copy(
                update={
                    "dimensions": value.dimensions,
                    "pose": entity.pose.model_copy(
                        update={"position": value.position, "yaw_degrees": value.yaw_degrees}
                    ),
                    "provenance": entity.provenance.model_copy(
                        update={k: (*getattr(entity.provenance, k), provenance) for k in changed}
                    ),
                }
            )
        )
    return SceneIR.model_validate_json(
        base.model_copy(update={"entities": tuple(entities)}).model_dump_json()
    ), choices


def ground_generated_scene(
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
    from .grounding import GroundingResult

    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise ValueError("invalid grounding timeout")
    policy = GeneratedLayoutPolicy.model_validate(policy)
    root = Path(output_root)
    if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("unsafe grounding root")
    root.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    evidence = []
    named_evidence = {}
    scene = None
    error = None
    status = "blocked"
    plan = None
    fixed = {}
    bindings = []
    choices = []
    chosen = None
    unknowns = []

    def record(name, raw, media_type="application/json"):
        (root / name).write_bytes(raw)
        ref = store.write_artifact(raw, media_type)
        evidence.append(ref)
        named_evidence[name] = ref.model_dump()
        return ref

    try:
        artifact_closure(store, (bundle_ref, proposal_ref, assets_ref))
        bundle = InputBundle.model_validate_json(store.read_artifact(bundle_ref))
        original = BackendProposal.model_validate_json(store.read_artifact(proposal_ref))
        if original.status != "completed" or original.proposal.scene is None:
            raise ValueError("grounding_requires_original_intent")
        base = original.proposal.scene
        unknowns = [u.model_dump(mode="json") for u in original.proposal.unknowns]
        if structural_policy is not None:
            structural_policy = StructuralPolicy.model_validate(structural_policy)
        plan = classify_generated_design(original.proposal, policy, structural_policy)
        assets = ResolvedAssetSet.model_validate_json(store.read_artifact(assets_ref))
        if (
            base.input_sha256 != bundle.request_sha256
            or SceneIR.model_validate_json(store.read_artifact(assets.scene_ir)) != base
        ):
            raise ValueError("unbound_grounding_intent")
        bindings, fixed = bind_generated_assets(store, base, assets, plan)
        if bundle.images or bundle.video:
            from .deployment import select_reconstruction_image

            remaining = int(timeout - (time.monotonic() - started))
            if remaining < 1:
                raise ValueError("model_timeout")
            chosen = select_reconstruction_image(
                store,
                bundle,
                assets.scene_ir,
                base.entities[0].id,
                output_root=root / "media-selection",
                timeout=remaining,
            )
            record("input.png", store.read_artifact(chosen.image), "image/png")
        elif bundle.text is None:
            raise ValueError("grounding_missing_input")
        context = {
            "schema_version": "x2env.generated_layout_context.v2",
            "bundle_ref": bundle_ref.model_dump(),
            "seed": bundle.seed,
            "original_proposal": original.proposal.model_dump(mode="json"),
            "policy": policy.model_dump(mode="json"),
            "structural_policy": structural_policy.model_dump(mode="json")
            if structural_policy
            else None,
            "asset_bindings": bindings,
            "design_plan": plan,
            "fixed_values": fixed,
            "media_selection": chosen.model_dump(mode="json") if chosen else None,
            "real_world_scale_recovered": False,
        }
        record("context.json", json.dumps(context).encode())
        prompt = (
            "Choose only authorized missing numeric values for a generated simulation layout. "
            "Do not recover real-world metric scale. Preserve every entity, ID, category, "
            "frame, relation and known numeric axis. Use each entity's own immutable asset "
            "dimensions and every fixed_values entry exactly. "
            "Only simulation_design_choice fields may be chosen: support XY extents within "
            "support_extent_range_m, unknown XY within position_abs_max_m, yaw within [-180,180]. "
            "Do not invent Z or asset dimensions. When original media is attached preserve its "
            "intended relative layout and original declared relations; generated_layout does not "
            "authorize ignoring the input. Without media choose a feasible layout "
            "under the same rules. "
            "Geometry centres are used except structural position is the top-surface centre. "
            "No physical, license, qualification or success claims.\n" + json.dumps(context)
        )
        if (
            not backend.executable.is_absolute()
            or hashlib.sha256(backend.executable.read_bytes()).hexdigest() != backend.executable_sha
        ):
            raise ValueError("executable_identity_mismatch")
        raw = backend._invoke(
            root,
            prompt,
            [{"path": str(root / "input.png")}] if chosen else [],
            GroundingValuesV2,
            record,
            timeout,
            started,
        )
        values = GroundingValuesV2.model_validate_json(raw)
        scene, choices = apply_generated_values(base, values, policy, plan, bindings, fixed, bundle)
        status = "completed"
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        error = str(exc)
        scene = None
        record("error.json", json.dumps({"reason": error}).encode())
    body = {
        "schema_version": "x2env.scene_grounding.v2",
        "status": status,
        "error_code": error,
        "bundle_ref": bundle_ref.model_dump(),
        "proposal_ref": proposal_ref.model_dump(),
        "assets_ref": assets_ref.model_dump(),
        "policy": policy.model_dump(mode="json"),
        "structural_policy": structural_policy.model_dump(mode="json")
        if structural_policy
        else None,
        "asset_bindings": bindings,
        "design_plan": plan,
        "fixed_values": fixed,
        "design_choices": choices,
        "media_selection": chosen.model_dump(mode="json") if chosen else None,
        "original_unknowns": unknowns,
        "resolved_unknowns": plan["resolved_unknown_indices"] if plan and scene else [],
        "proposed_scene": scene.model_dump(mode="json") if scene else None,
        "real_world_scale_recovered": False,
        "authority": "advisory_design_only",
        "evidence": [ref.model_dump() for ref in evidence],
        "transport": {
            name: named_evidence[name]
            for name in (
                "prompt.txt",
                "proposal.schema.json",
                "invocation.json",
                "process.json",
                "process-terminal.json",
                "codex.jsonl",
                "codex.stderr",
                "proposal.json",
            )
            if name in named_evidence
        },
        "wall_seconds": time.monotonic() - started,
    }
    receipt = record("receipt.json", json.dumps(body, sort_keys=True).encode())
    return GroundingResult(
        status=status,
        proposed_scene=scene,
        resolved_unknowns=tuple(body["resolved_unknowns"]),
        receipt=receipt,
        error_code=error,
    )
