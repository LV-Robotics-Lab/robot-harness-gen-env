"""Advisory preparation parameters; mesh bounds are not physical measurements."""

import hashlib
import json
import math
import subprocess
import time
from io import BytesIO
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
import trimesh
from pydantic import Field

from .contracts import ArtifactRef, Model, SceneIR

Basis = Literal["source_metadata", "codex_estimate", "deployment_supplied", "input_explicit"]
Positive = Annotated[float, Field(gt=0)]
Unit = Annotated[float, Field(ge=0, le=1)]


class NormalizationParameters(Model):
    """Evidence exactly binds source/fetch/scene and all supplied parameters.

    The deployment authenticates this port. Hash binding alone is not authority.
    advisory_receipt connects managed model logs without a self-referential receipt.
    """

    dimensions_m: tuple[Positive, Positive, Positive]
    up_axis: Literal["Y", "Z"]
    mass_kg: Positive
    friction: float = Field(ge=0)
    base_color: tuple[Unit, Unit, Unit, Unit] | None = None
    declared_color: str | None = None
    dimensions_basis: Basis
    up_axis_basis: Basis
    mass_basis: Basis
    friction_basis: Basis
    color_basis: Basis | None = None
    evidence: ArtifactRef
    advisory_receipt: ArtifactRef | None = None


class PreparationValues(Model):
    """Bounded model output; these are preparation limits, not validation thresholds."""

    dimensions_m: tuple[
        Annotated[float, Field(ge=0.0001, le=100)],
        Annotated[float, Field(ge=0.0001, le=100)],
        Annotated[float, Field(ge=0.0001, le=100)],
    ]
    up_axis: Literal["Y", "Z"]
    mass_kg: float = Field(ge=0.000001, le=100000)
    friction: float = Field(ge=0, le=10)
    base_color: tuple[Unit, Unit, Unit, Unit] | None
    declared_color: str | None


class PreparationResult(Model):
    status: Literal["completed", "failed", "blocked"]
    parameters: NormalizationParameters | None
    receipt: ArtifactRef
    error_code: str | None = None


class GlbPreparationValues(PreparationValues):
    """The source coordinate convention is a format fact, not a model estimate."""

    up_axis: Literal["Y"]


def prepare_asset(backend, scene_ir, entity, candidate, fetched, *, output_root, timeout):
    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise ValueError("deadline must be an integer within 1..600 seconds")
    root = Path(output_root)
    if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("attempt root must be absolute non-symbolic new directory")
    root.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    evidence, values, source_sha = [], None, None
    status, error = "failed", None
    axis_basis = []

    def record(name, data, media_type="application/json"):
        (root / name).write_bytes(data)
        ref = backend.store.write_artifact(data, media_type)
        evidence.append(ref)
        return ref

    try:
        if (
            not backend.executable.is_absolute()
            or hashlib.sha256(backend.executable.read_bytes()).hexdigest() != backend.executable_sha
        ):
            raise ValueError("executable_identity_mismatch")
        scene = SceneIR.model_validate_json(backend.store.read_artifact(scene_ir))
        if entity not in scene.entities or entity.role != "foreground":
            raise ValueError("unbound_preparation_entity")
        if candidate.entity_id != entity.id or fetched.status != "succeeded":
            raise ValueError("unbound_preparation_source")
        for ref in (candidate.engine_record, fetched.receipt):
            backend.store.read_artifact(ref)
        path = Path(fetched.source_path)
        if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
            raise ValueError("unsafe_preparation_source")
        raw = path.read_bytes()
        source_sha = hashlib.sha256(raw).hexdigest()
        source_ref = record("source" + path.suffix, raw, "application/octet-stream")
        if path.suffix.lower() not in {".glb", ".obj", ".ply", ".stl"}:
            raise ValueError("unsupported_preparation_format")
        # No filesystem resolver: a supplied mesh cannot load undeclared sidecar files.
        mesh = trimesh.load(
            BytesIO(raw), file_type=path.suffix[1:].lower(), force="scene", process=False
        )
        bounds = np.asarray(mesh.bounds, dtype=float)
        if (
            bounds.shape != (2, 3)
            or not np.isfinite(bounds).all()
            or np.any(bounds[1] <= bounds[0])
        ):
            raise ValueError("invalid_source_geometry")
        context = {
            "scene_ir": scene_ir.model_dump(mode="json"),
            "entity": entity.model_dump(mode="json"),
            "candidate": candidate.model_dump(mode="json"),
            "fetch": fetched.model_dump(mode="json"),
            "source": source_ref.model_dump(mode="json"),
            "source_format": path.suffix.lower(),
            "measured_mesh_bounds": bounds.tolist(),
            "bounds_basis": "source_mesh_coordinates_not_measured_real_object",
            "format_units": "meters by glTF convention; not physical object calibration"
            if path.suffix.lower() == ".glb"
            else "unspecified",
            "gltf_axis_fact": "Y-up; glTF 2.0 coordinate-system convention"
            if path.suffix.lower() == ".glb"
            else None,
        }
        record("context.json", json.dumps(context).encode())
        prompt = (
            "You are the Harness advisory asset preparation backend. Use no tools. "
            "Return only supplied PreparationValues schema. Preserve every known SceneIR dimension "
            "axis exactly. Dimensions are target normalized Z-up X/Y/Z meters; infer only unknown "
            "axes, finite plausible mass_kg and friction, as estimates not measurements. Preserve "
            "geometry aspect ratio; do not make arbitrary non-uniform scaling. Source mesh bounds "
            "are source mesh coordinates, not measured real-world size. "
            "up_axis describes the SOURCE mesh, not the target dimensions or Genesis. "
            "For GLB it MUST be Y by glTF format, even though target dimensions are Z-up. "
            "For an explicit requested color provide RGBA and exactly its color name; otherwise "
            "leave base_color and declared_color null. No receipt, authority, license, "
            "Skill success "
            "or physical validation claims. Do not change thresholds. Context:\n"
            + json.dumps(context)
        )
        response_schema = GlbPreparationValues if path.suffix.lower() == ".glb" else PreparationValues
        raw_response = backend._invoke(root, prompt, [], response_schema, record, timeout, start)
        values = PreparationValues.model_validate_json(raw_response)
        known = entity.dimensions or (None, None, None)
        for wanted, actual in zip(known, values.dimensions_m, strict=True):
            if wanted is not None and not math.isclose(wanted, actual, rel_tol=0, abs_tol=1e-9):
                raise ValueError("preparation_changed_explicit_dimensions")
            axis_basis.append("input_explicit" if wanted is not None else "codex_estimate")
        if path.suffix.lower() == ".glb" and values.up_axis != "Y":
            raise ValueError("preparation_changed_format_axis")
        if entity.color:
            if values.base_color is None or values.declared_color != entity.color:
                raise ValueError("preparation_changed_explicit_color")
        elif values.base_color is not None or values.declared_color is not None:
            raise ValueError("preparation_added_unrequested_color")
        status = "completed"
    except FileNotFoundError as exc:
        status, error, values = "blocked", "blocked_external_resource", None
        record("error.json", json.dumps({"error": str(exc)}).encode())
    except (ValueError, OSError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        error, values = "invalid_preparation_evidence", None
        record("error.json", json.dumps({"reason": str(exc)}).encode())
    receipt = record(
        "result.json",
        json.dumps(
            {
                "status": status,
                "error_code": error,
                "model": backend.model,
                "executable_sha256": backend.executable_sha,
                "external_agent_executed": (root / "process.json").is_file(),
                "scene_ir": scene_ir.model_dump(mode="json"),
                "source_sha256": source_sha,
                "dimensions_axis_basis": axis_basis,
                "authority": "advisory_only",
                "requested_color_basis": "input_explicit" if entity.color else None,
                "rgba_conversion_basis": "codex_estimate" if entity.color else None,
                "evidence": [r.model_dump(mode="json") for r in evidence],
                "wall_seconds": time.monotonic() - start,
            }
        ).encode(),
    )
    params = None
    if values is not None:
        fields = dict(
            **values.model_dump(mode="json"),
            dimensions_basis="input_explicit"
            if all(b == "input_explicit" for b in axis_basis)
            else "codex_estimate",
            up_axis_basis="source_metadata" if path.suffix.lower() == ".glb" else "codex_estimate",
            mass_basis="codex_estimate",
            friction_basis="codex_estimate",
            color_basis="codex_estimate" if entity.color else None,
            advisory_receipt=receipt.model_dump(mode="json"),
        )
        binding = record(
            "preparation.json",
            json.dumps(
                {
                    "scene_ir": scene_ir.model_dump(mode="json"),
                    "entity_id": entity.id,
                    "candidate_id": candidate.candidate_id,
                    "fetch_receipt": fetched.receipt.model_dump(mode="json"),
                    "source_sha256": source_sha,
                    "parameters": fields,
                }
            ).encode(),
        )
        params = NormalizationParameters.model_validate_json(
            json.dumps({**fields, "evidence": binding.model_dump(mode="json")})
        )
    return PreparationResult(status=status, parameters=params, receipt=receipt, error_code=error)
