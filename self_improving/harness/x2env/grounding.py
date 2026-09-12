"""Explicit asset-anchored simulation design, never recovery of real-world scale."""

import hashlib
import json
import math
import subprocess
import time
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from .artifacts import artifact_closure
from .assets import AssetRegistry
from .compile import ResolvedAssetSet
from .contracts import ArtifactRef, BackendProposal, FieldProvenance, InputBundle, Model, SceneIR


class SceneDesignPolicy(Model):
    enabled: bool = False
    mode: Literal["asset_anchored_simulation"] = "asset_anchored_simulation"


Positive = Annotated[float, Field(gt=0, le=100)]
Coordinate = Annotated[float, Field(ge=-1000, le=1000)]


class GroundedEntity(Model):
    id: str
    dimensions: tuple[Positive, Positive, Positive]
    frame: str
    position: tuple[Coordinate, Coordinate, Coordinate]
    yaw_degrees: float = Field(ge=-180, le=180)


class GroundingValues(Model):
    entities: tuple[GroundedEntity, ...] = Field(min_length=2, max_length=2)


class GroundingResult(Model):
    status: Literal["completed", "blocked", "failed"]
    proposed_scene: SceneIR | None = None
    resolved_unknowns: tuple[int, ...] = ()
    receipt: ArtifactRef
    error_code: str | None = None
    real_world_scale_recovered: Literal[False] = False


def ground_scene(
    bundle_ref, proposal_ref, assets_ref, policy, *, store, backend, output_root, timeout=600
):
    """One bounded managed-model call; all explicit axes and semantic fields immutable."""
    policy = SceneDesignPolicy.model_validate(policy)
    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise ValueError("invalid grounding timeout")
    root = Path(output_root)
    if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("unsafe grounding root")
    root.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    evidence = []
    scene = None
    resolved_indices = ()
    error = None
    status = "blocked"
    changes = []
    original_unknowns = []

    def record(name, raw, media_type="application/json"):
        (root / name).write_bytes(raw)
        ref = store.write_artifact(raw, media_type)
        evidence.append(ref)
        return ref

    try:
        if not policy.enabled:
            raise ValueError("design_grounding_disabled")
        artifact_closure(store, (bundle_ref, proposal_ref, assets_ref))
        bundle = InputBundle.model_validate_json(store.read_artifact(bundle_ref))
        original = BackendProposal.model_validate_json(store.read_artifact(proposal_ref))
        if original.status != "completed" or original.proposal.scene is None:
            raise ValueError("grounding_requires_original_intent")
        proposal = original.proposal
        base = proposal.scene
        original_unknowns = [u.model_dump(mode="json") for u in proposal.unknowns]
        allowed = {"scale_unobservable", "pose_unobservable"}
        if any(u.critical and u.reason_kind not in allowed for u in proposal.unknowns):
            raise ValueError("grounding_requires_clarification")
        resolved_indices = tuple(
            i for i, u in enumerate(proposal.unknowns) if u.reason_kind in allowed
        )
        if not resolved_indices:
            raise ValueError("no_authorized_design_unknowns")
        for index in resolved_indices:
            unknown = proposal.unknowns[index]
            expected = (
                unknown.field.endswith(".dimensions")
                if unknown.reason_kind == "scale_unobservable"
                else unknown.field.endswith((".pose", ".pose.position", ".pose.yaw_degrees"))
            )
            if not expected:
                raise ValueError("grounding_unknown_field_not_designable")
        assets = ResolvedAssetSet.model_validate_json(store.read_artifact(assets_ref))
        if (
            base.input_sha256 != bundle.request_sha256
            or SceneIR.model_validate_json(store.read_artifact(assets.scene_ir)) != base
        ):
            raise ValueError("unbound_grounding_intent")
        rigid = [e for e in base.entities if e.role == "foreground"]
        supports = [e for e in base.entities if e.role == "structural_support"]
        if (
            len(base.entities) != 2
            or len(rigid) != 1
            or len(supports) != 1
            or len(assets.assets) != 1
            or assets.assets[0].entity_id != rigid[0].id
            or any(e.articulation_state is not None for e in base.entities)
        ):
            raise ValueError("unsupported_grounding_scope")
        anchor = rigid[0]
        support = supports[0]
        if (
            support.category not in {"table", "worktop", "counter"}
            or not any(
                r.source == anchor.id and r.target == support.id and r.relation == "on"
                for r in base.relations
            )
            or any(r.relation == "inside" for r in base.relations)
        ):
            raise ValueError("unsupported_grounding_support")
        version = AssetRegistry(store).inspect(assets.assets[0].version_sha256)
        metrics = json.loads(store.read_artifact(version.normalization_report))
        dims = metrics.get("dimensions_m")
        if (
            not isinstance(dims, list)
            or len(dims) != 3
            or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in dims)
        ):
            raise ValueError("missing_measured_anchor_dimensions")
        from .deployment import select_reconstruction_image

        chosen = select_reconstruction_image(
            store,
            bundle,
            assets.scene_ir,
            anchor.id,
            output_root=root / "media-selection",
            timeout=max(1, int(timeout - (time.monotonic() - started))),
        )
        if chosen is None:
            raise ValueError("grounding_requires_media")
        raw = store.read_artifact(chosen.image)
        record("input.png", raw, "image/png")
        context = {
            "bundle_ref": bundle_ref.model_dump(),
            "seed": bundle.seed,
            "original_proposal": proposal.model_dump(mode="json"),
            "asset_version": version.model_dump(mode="json"),
            "anchor_dimensions_m": dims,
            "media_selection": chosen.model_dump(mode="json"),
            "policy": policy.model_dump(),
            "real_world_scale_recovered": False,
        }
        record("context.json", json.dumps(context).encode())
        prompt = (
            "Design a generated simulation scene, NOT a reconstruction of real metric scale. "
            "Use the selected immutable asset dimensions as the design scale anchor exactly. "
            "Using the attached original media relative geometry, propose only unknown dimensions, "
            "coordinates and yaw for the original two entities. Preserve every known numeric axis "
            "exactly. Preserve entity IDs, relations and each declared coordinate frame. "
            "Structural support uses world. Coordinates refer to geometry centres except the "
            "structural support position which is its top-surface centre. "
            "Estimate support footprint and object placement from relative visual layout, "
            "not a fixed "
            "center template. No license, measurement, physical pass, or success claims. "
            "Return GroundingValues JSON only. Context:\n" + json.dumps(context)
        )
        if backend is None:
            raise FileNotFoundError("managed_codex_backend")
        if (
            not backend.executable.is_absolute()
            or hashlib.sha256(backend.executable.read_bytes()).hexdigest() != backend.executable_sha
        ):
            raise ValueError("executable_identity_mismatch")
        raw_values = backend._invoke(
            root,
            prompt,
            [{"path": str(root / "input.png")}],
            GroundingValues,
            record,
            timeout,
            started,
        )
        values = GroundingValues.model_validate_json(raw_values)
        by_id = {e.id: e for e in values.entities}
        if set(by_id) != {e.id for e in base.entities} or len(by_id) != 2:
            raise ValueError("grounding_changed_entities")
        entities = []
        for entity in base.entities:
            value = by_id[entity.id]
            known = entity.dimensions or (None, None, None)
            for old, new in zip(known, value.dimensions, strict=True):
                if old is not None and old != new:
                    raise ValueError("grounding_changed_explicit_axis")
            for old, new in zip(entity.pose.position, value.position, strict=True):
                if old is not None and old != new:
                    raise ValueError("grounding_changed_explicit_axis")
            if entity.pose.yaw_degrees is not None and entity.pose.yaw_degrees != value.yaw_degrees:
                raise ValueError("grounding_changed_explicit_yaw")
            if value.frame != entity.pose.frame:
                raise ValueError("grounding_changed_frame")
            if entity.id == anchor.id and any(
                not math.isclose(a, b, rel_tol=0, abs_tol=1e-9)
                for a, b in zip(value.dimensions, dims, strict=True)
            ):
                raise ValueError("grounding_changed_anchor_scale")
            for field, old, new in [
                ("dimensions", entity.dimensions, value.dimensions),
                (
                    "pose",
                    entity.pose.model_dump(),
                    {
                        "frame": value.frame,
                        "position": value.position,
                        "yaw_degrees": value.yaw_degrees,
                    },
                ),
            ]:
                if old != new:
                    changes.append(
                        {
                            "entity_id": entity.id,
                            "field": field,
                            "changed_axes": [i for i, v in enumerate(known) if v is None]
                            if field == "dimensions"
                            else [str(i) for i, v in enumerate(entity.pose.position) if v is None]
                            + (["yaw_degrees"] if entity.pose.yaw_degrees is None else []),
                            "original": old,
                            "design_value": new,
                            "basis": "selected_asset_simulation_scale"
                            if field == "dimensions" and entity.id == anchor.id
                            else "media_relative_layout_design_estimate",
                            "asset_version": version.version_sha256,
                            "normalization_report": version.normalization_report.model_dump(),
                            "media_selection": chosen.provenance.model_dump(),
                        }
                    )
            provenance = entity.provenance
            source = "image" if bundle.images else "video"
            media_hash = (
                bundle.images[0].source.sha256 if bundle.images else bundle.video.source.sha256
            )
            design = FieldProvenance(
                source=source,
                input_sha256=media_hash,
                kind="inferred",
                media_index=0,
                frame_index=None if bundle.images else 0,
                note="simulation design-choice; not recovered real-world scale; "
                + version.version_sha256,
            )
            provenance = provenance.model_copy(
                update={
                    field: (*getattr(provenance, field), design)
                    for field in {c["field"] for c in changes if c["entity_id"] == entity.id}
                }
            )
            entities.append(
                entity.model_copy(
                    update={
                        "dimensions": value.dimensions,
                        "pose": entity.pose.model_copy(
                            update={
                                "frame": value.frame,
                                "position": value.position,
                                "yaw_degrees": value.yaw_degrees,
                            }
                        ),
                        "provenance": provenance,
                    }
                )
            )
        scene = SceneIR.model_validate_json(
            base.model_copy(update={"entities": tuple(entities)}).model_dump_json()
        )
        status = "completed"
    except FileNotFoundError as exc:
        error = "blocked_external_resource"
        record("error.json", json.dumps({"reason": str(exc)}).encode())
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        error = str(exc)
        scene = None
        resolved_indices = ()
        record("error.json", json.dumps({"reason": error}).encode())
    receipt = store.write_artifact(
        json.dumps(
            {
                "schema_version": "x2env.scene_grounding.v1",
                "status": status,
                "error_code": error,
                "bundle_ref": bundle_ref.model_dump(),
                "proposal_ref": proposal_ref.model_dump(),
                "assets_ref": assets_ref.model_dump(),
                "policy": policy.model_dump(),
                "original_unknowns": original_unknowns,
                "resolved_unknowns": resolved_indices,
                "changes": changes,
                "proposed_scene": scene.model_dump(mode="json") if scene else None,
                "real_world_scale_recovered": False,
                "authority": "advisory_design_only",
                "evidence": [r.model_dump() for r in evidence],
                "wall_seconds": time.monotonic() - started,
            },
            sort_keys=True,
        ).encode(),
        "application/json",
    )
    (root / "receipt.json").write_bytes(store.read_artifact(receipt))
    return GroundingResult(
        status=status,
        proposed_scene=scene,
        resolved_unknowns=resolved_indices,
        receipt=receipt,
        error_code=error,
    )
