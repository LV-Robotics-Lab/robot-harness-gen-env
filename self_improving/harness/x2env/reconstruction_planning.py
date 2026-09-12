"""Advisory reconstruction planning, never image generation or asset authorization."""

import hashlib
import json
import math
import subprocess
import time
from io import BytesIO
from pathlib import Path
from typing import Annotated, Literal

from PIL import Image
from pydantic import Field

from .adapters.reconstruction import SegmentationProposal
from .contracts import ArtifactRef, Model, SceneIR

Dimension = Annotated[float, Field(ge=0.0001, le=100)]


class ReconstructionValues(Model):
    """Only proposed pixels and bounded estimates; no licenses or provenance authored by model."""

    box_xyxy: tuple[float, float, float, float] | None
    point_coords: tuple[tuple[float, float], ...] = Field(max_length=32)
    point_labels: tuple[Literal[0, 1], ...] = Field(max_length=32)
    dimensions_m: tuple[Dimension, Dimension, Dimension]
    mass_kg: float = Field(ge=0.000001, le=100000)
    friction: float = Field(ge=0, le=10)
    reason: str = Field(min_length=1, max_length=4000)


class ReconstructionParameters(Model):
    dimensions_m: tuple[Dimension, Dimension, Dimension]
    dimensions_axis_basis: tuple[
        Literal["input_explicit", "codex_estimate"],
        Literal["input_explicit", "codex_estimate"],
        Literal["input_explicit", "codex_estimate"],
    ]
    mass_kg: float = Field(gt=0)
    friction: float = Field(ge=0)
    mass_basis: Literal["codex_estimate"] = "codex_estimate"
    friction_basis: Literal["codex_estimate"] = "codex_estimate"
    evidence: ArtifactRef


class ReconstructionPlanResult(Model):
    status: Literal["completed", "failed", "blocked"]
    segmentation: SegmentationProposal | None
    parameters: ReconstructionParameters | None = None
    receipt: ArtifactRef
    error_code: str | None = None


def plan_reconstruction(backend, scene_ir, entity, image_ref, *, output_root, timeout=600):
    root = Path(output_root)
    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise ValueError("invalid planning timeout")
    if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("planning root must be absolute new non-symbolic directory")
    root.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    evidence = []
    values = None
    bases = []
    status, error = "failed", None

    def record(name, data, media_type="application/json"):
        (root / name).write_bytes(data)
        ref = backend.store.write_artifact(data, media_type)
        evidence.append(ref)
        return ref

    try:
        scene = SceneIR.model_validate_json(backend.store.read_artifact(scene_ir))
        if entity not in scene.entities or entity.role != "foreground":
            raise ValueError("unbound_reconstruction_entity")
        if image_ref is None:
            status, error = "blocked", "missing_reconstruction_image"
        else:
            raw = backend.store.read_artifact(image_ref)
            with Image.open(BytesIO(raw)) as image:
                width, height = image.size
                if image.getexif().get(274, 1) != 1:
                    raise ValueError("reconstruction_image_requires_canonical_orientation")
                if image.format not in {"PNG", "JPEG", "WEBP"} or width * height > 40000000:
                    raise ValueError("unsupported_reconstruction_image")
                suffix = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}[image.format]
                image.load()
            name = f"input.{suffix}"
            record(name, raw, image_ref.media_type)
            if (
                not backend.executable.is_absolute()
                or hashlib.sha256(backend.executable.read_bytes()).hexdigest()
                != backend.executable_sha
            ):
                raise ValueError("executable_identity_mismatch")
            context = {
                "scene_ir": scene_ir.model_dump(mode="json"),
                "entity": entity.model_dump(mode="json"),
                "image_ref": image_ref.model_dump(mode="json"),
                "width": width,
                "height": height,
                "media_selection_authority": "Harness caller; no implicit frame selection",
            }
            record("context.json", json.dumps(context).encode())
            prompt = (
                "You are the Harness advisory reconstruction planner. Use no tools. Locate only "
                "the requested SceneIR entity in the attached actual image. Return a SAM2 box in "
                "original image pixel xyxy coordinates and/or labelled pixel points (1 foreground, "
                "0 background). Do not invent another image, object or fixed full-frame box. "
                "Preserve all known SceneIR dimension axes exactly; estimate only unknown target "
                "dimensions in normalized Z-up X/Y/Z meters and plausible mass_kg/friction. These "
                "are estimates, not measurements. No license, receipt, qualification, "
                "Skill success "
                "or execution authority. Return only the static schema. Context:\n"
                + json.dumps(context)
            )
            response = backend._invoke(
                root,
                prompt,
                [{"path": str(root / name), "input_sha256": image_ref.sha256}],
                ReconstructionValues,
                record,
                timeout,
                start,
            )
            values = ReconstructionValues.model_validate_json(response)
            box = values.box_xyxy
            if box and not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height):
                raise ValueError("invalid_segmentation_box")
            if (
                len(values.point_coords) != len(values.point_labels)
                or (box is None and not values.point_coords)
                or (box is None and 1 not in values.point_labels)
                or any(not 0 <= x < width or not 0 <= y < height for x, y in values.point_coords)
            ):
                raise ValueError("invalid_segmentation_points")
            for wanted, actual in zip(
                entity.dimensions or (None, None, None), values.dimensions_m, strict=True
            ):
                if wanted is not None and not math.isclose(wanted, actual, rel_tol=0, abs_tol=1e-9):
                    raise ValueError("changed_explicit_dimensions")
                bases.append("input_explicit" if wanted is not None else "codex_estimate")
            status = "completed"
    except FileNotFoundError as exc:
        status, error, values = "blocked", "blocked_external_resource", None
        record("error.json", json.dumps({"reason": str(exc)}).encode())
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        known = {
            "unbound_reconstruction_entity",
            "reconstruction_image_requires_canonical_orientation",
            "unsupported_reconstruction_image",
            "executable_identity_mismatch",
            "invalid_segmentation_box",
            "invalid_segmentation_points",
            "changed_explicit_dimensions",
            "model_timeout",
            "model_interrupted",
            "advisory_tool_violation",
        }
        error, values = str(exc) if str(exc) in known else "invalid_reconstruction_plan", None
        record("error.json", json.dumps({"reason": str(exc)}).encode())
    receipt = record(
        "result.json",
        json.dumps(
            {
                "status": status,
                "error_code": error,
                "scene_ir": scene_ir.model_dump(mode="json"),
                "entity_id": entity.id,
                "image_ref": image_ref.model_dump(mode="json") if image_ref else None,
                "model": backend.model,
                "executable_sha256": backend.executable_sha,
                "external_agent_executed": (root / "process.json").is_file(),
                "dimensions_axis_basis": bases,
                "mass_basis": "codex_estimate",
                "friction_basis": "codex_estimate",
                "proposal": values.model_dump(mode="json") if values else None,
                "evidence": [ref.model_dump(mode="json") for ref in evidence],
                "registration_authorized": False,
                "source_image_selection_trust": "delegated_to_harness",
                "authority": "advisory_only",
                "wall_seconds": time.monotonic() - start,
            }
        ).encode(),
    )
    segmentation, parameters = None, None
    if values:
        segmentation = SegmentationProposal(
            input_sha256=image_ref.sha256,
            proposal_receipt=receipt,
            box_xyxy=values.box_xyxy,
            point_coords=values.point_coords,
            point_labels=values.point_labels,
        )
        parameters = ReconstructionParameters(
            dimensions_m=values.dimensions_m,
            dimensions_axis_basis=tuple(bases),
            mass_kg=values.mass_kg,
            friction=values.friction,
            evidence=receipt,
        )
    return ReconstructionPlanResult(
        status=status,
        segmentation=segmentation,
        parameters=parameters,
        receipt=receipt,
        error_code=error,
    )
