"""Deterministic RoboTwin-compatible proxy generation for catalog misses."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from .catalog import AssetCatalog, CatalogEntry, CatalogModel
from .colors import COLOR_RGB
from .grounding import GroundedSelection, ground_object
from .schema import RelationType, SceneObjectSpec, SceneSpec, SceneSpecError
from .support_geometry import (
    footprint_2d,
    support_surface_dimensions,
    support_surface_shape,
)

GENERATOR_VERSION = "scene_gen.asset_adaptation.v2"
PROXY_GENERATOR_VERSION = "scene_gen.procedural_proxy.v3"
SCALE_GENERATOR_VERSION = "scene_gen.derived_primitive_scale.v2"
SCALE_HEADROOM = 0.92
MIN_DERIVED_SCALE = 0.35
PRIMITIVE_PROXY_SOURCES = {("004_fluted-block", 0)}


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return normalized[:32] or "object"


def _geometry(query: SceneObjectSpec) -> tuple[str, int, tuple[float, float, float]]:
    category = query.category.lower()
    if "hex" in category:
        return "hexagonal_prism", 6, (0.09, 0.09, 0.075)
    if "oct" in category:
        return "octagonal_prism", 8, (0.09, 0.09, 0.07)
    if "cylinder" in category or "column" in category:
        return "cylindrical_proxy", 24, (0.075, 0.075, 0.09)
    if "pedestal" in category:
        return "rectangular_pedestal", 4, (0.09, 0.09, 0.075)
    if category in {"block", "cube"} or "block" in category or "cube" in category:
        return "bounded_box_proxy", 4, (0.055, 0.055, 0.04)
    return "bounded_box_proxy", 4, (0.08, 0.08, 0.06)


def _prism_obj(
    *,
    sides: int,
    dimensions_m: tuple[float, float, float],
    with_material: bool,
) -> str:
    width, depth, height = dimensions_m
    angles = [
        2.0 * math.pi * index / sides + math.pi / 4.0
        for index in range(sides)
    ]
    max_abs_cos = max(abs(math.cos(angle)) for angle in angles)
    max_abs_sin = max(abs(math.sin(angle)) for angle in angles)
    vertices: list[tuple[float, float, float]] = []
    for z in (0.0, height):
        for angle in angles:
            vertices.append(
                (
                    0.5 * width * math.cos(angle) / max_abs_cos,
                    0.5 * depth * math.sin(angle) / max_abs_sin,
                    z,
                )
            )
    lines = ["# Deterministic procedural proxy", "o generated_proxy"]
    if with_material:
        lines.extend(["mtllib material.mtl", "usemtl generated_material"])
    lines.extend(f"v {x:.9f} {y:.9f} {z:.9f}" for x, y, z in vertices)
    bottom = " ".join(str(index + 1) for index in reversed(range(sides)))
    top = " ".join(str(sides + index + 1) for index in range(sides))
    lines.extend((f"f {bottom}", f"f {top}"))
    for index in range(sides):
        nxt = (index + 1) % sides
        lines.append(f"f {index + 1} {nxt + 1} {sides + nxt + 1} {sides + index + 1}")
    return "\n".join(lines) + "\n"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_proxy_asset(
    query: SceneObjectSpec,
    objects_root: Path,
    *,
    dimensions_m: tuple[float, float, float] | None = None,
    source: GroundedSelection | None = None,
    uniform_scale_factor: float | None = None,
    relation: RelationType | None = None,
    target: GroundedSelection | None = None,
    adaptation_reasons: tuple[str, ...] = (),
) -> tuple[CatalogEntry, dict[str, Any]]:
    derived = source is not None
    if derived and (
        uniform_scale_factor is None
        or relation is None
        or source.model.dimensions_m is None
        or (relation != RelationType.ON_TABLE and target is None)
        or not adaptation_reasons
    ):
        raise SceneSpecError("derived proxy requires complete source and compatibility metadata")
    generation_kind = "derived_scaled_proxy" if derived else "procedural_proxy"
    geometry_family, sides, default_dimensions = _geometry(query)
    dimensions = dimensions_m or default_dimensions
    identity = json.dumps(
        {
            "generator": SCALE_GENERATOR_VERSION if derived else PROXY_GENERATOR_VERSION,
            "generation_kind": generation_kind,
            "category": query.category,
            "color": query.color,
            "material": query.material,
            "dimensions_m": dimensions,
            "source_asset_id": source.entry.asset_id if source else None,
            "source_model_id": source.model.model_id if source else None,
            "uniform_scale_factor": uniform_scale_factor,
            "relation": relation.value if relation else None,
            "target_asset_id": target.entry.asset_id if target else "table",
            "target_model_id": target.model.model_id if target else None,
            "adaptation_reasons": adaptation_reasons,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    prefix = "900_scaled_proxy" if derived else "900_gen"
    asset_id = f"{prefix}_{_slug(query.category)}_{digest[:8]}"
    asset_dir = objects_root / asset_id
    visual_dir = asset_dir / "visual"
    collision_dir = asset_dir / "collision"
    visual_dir.mkdir(parents=True, exist_ok=True)
    collision_dir.mkdir(parents=True, exist_ok=True)

    visual_path = visual_dir / "textured0.obj"
    collision_path = collision_dir / "textured0.obj"
    material_path = visual_dir / "material.mtl"
    metadata_path = asset_dir / "model_data0.json"
    provenance_path = asset_dir / "generation_provenance.json"
    visual_path.write_text(
        _prism_obj(sides=sides, dimensions_m=dimensions, with_material=True),
        encoding="utf-8",
    )
    collision_path.write_text(
        _prism_obj(sides=sides, dimensions_m=dimensions, with_material=False),
        encoding="utf-8",
    )
    red, green, blue = COLOR_RGB.get(query.color or "", (0.28, 0.55, 0.62))
    material_path.write_text(
        "newmtl generated_material\n"
        f"Kd {red:.6f} {green:.6f} {blue:.6f}\n"
        "Ka 0.040000 0.040000 0.040000\n"
        "Ks 0.120000 0.120000 0.120000\n"
        "Ns 32.000000\n",
        encoding="utf-8",
    )
    width, depth, height = dimensions
    metadata_path.write_text(
        json.dumps(
            {
                "scale": [1.0, 1.0, 1.0],
                "extents": [width, height, depth],
                "transform_matrix": [
                    [1.0, 0.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0, 0.0],
                    [0.0, 0.0, 1.0, 0.0],
                    [0.0, 0.0, 0.0, 1.0],
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    provenance = {
        "schema_version": "robotwin.generated_asset_provenance.v1",
        "generator": SCALE_GENERATOR_VERSION if derived else PROXY_GENERATOR_VERSION,
        "generation_kind": generation_kind,
        "asset_id": asset_id,
        "semantic_category": query.category,
        "requested_color": query.color,
        "requested_material": query.material,
        "geometry_family": geometry_family,
        "geometry_fidelity": (
            "primitive_proxy_preserving_uniform_source_dimensions"
            if derived
            else "semantic_shape_when_recognized_otherwise_bounded_proxy"
        ),
        "generated_license": "project_generated_academic_artifact",
        "dimensions_m": list(dimensions),
        **(
            {
                "uniform_scale_factor": uniform_scale_factor,
                "source_asset_id": source.entry.asset_id,
                "source_model_id": source.model.model_id,
                "source_dimensions_m": list(source.model.dimensions_m),
                "adaptation_reasons": list(adaptation_reasons),
                "semantic_name": source.entry.semantic_name,
                "aliases": list(source.entry.aliases),
                "materials": list(source.entry.materials),
                "compatibility": {
                    "relation": relation.value,
                    "target_asset_id": target.entry.asset_id if target else "table",
                    "target_model_id": target.model.model_id if target else None,
                    "headroom_fraction": SCALE_HEADROOM,
                    "source_runtime_probe": (
                        "catalog collision unstable; primitive proxy required"
                        if "source_runtime_instability" in adaptation_reasons
                        else "not_run"
                    ),
                },
            }
            if derived
            else {}
        ),
        "files": {
            "visual": {"path": str(visual_path.resolve()), "sha256": _sha256(visual_path)},
            "collision": {"path": str(collision_path.resolve()), "sha256": _sha256(collision_path)},
            "material": {"path": str(material_path.resolve()), "sha256": _sha256(material_path)},
            "metadata": {"path": str(metadata_path.resolve()), "sha256": _sha256(metadata_path)},
        },
    }
    provenance_path.write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    entry = CatalogEntry(
        asset_id=asset_id,
        semantic_name=source.entry.semantic_name if source else query.category,
        category=source.entry.category if source else query.category,
        aliases=(
            source.entry.aliases
            if source
            else tuple(sorted({query.category, query.category.replace("_", " ")}))
        ),
        colors=(query.color,) if query.color else (),
        materials=(
            (query.material,)
            if query.material
            else source.entry.materials
            if source
            else ()
        ),
        load_type="rigid",
        asset_path=str(asset_dir.resolve()),
        models=(
            CatalogModel(
                model_id=0,
                model_path=str(asset_dir.resolve()),
                metadata_path=str(metadata_path.resolve()),
                visual_path=str(visual_path.resolve()),
                collision_path=str(collision_path.resolve()),
                scale=(1.0, 1.0, 1.0),
                dimensions_m=dimensions,
                stable_pose_id="procedural_flat_base",
                stable_orientation_wxyz=(1.0, 0.0, 0.0, 0.0),
                z_policy="origin_on_table",
                usable=True,
            ),
        ),
        available=True,
        source_notes=(
            (
                "derived_scaled_proxy",
                "geometry_compatible_derived",
                SCALE_GENERATOR_VERSION,
                "proxy_geometry",
            )
            if derived
            else ("procedural_generated", PROXY_GENERATOR_VERSION, "proxy_geometry")
        ),
    )
    return entry, provenance


def _uniform_scale_limit(
    source: CatalogModel,
    target: CatalogModel,
    relation: RelationType,
) -> float | None:
    if source.dimensions_m is None or target.dimensions_m is None:
        return None
    if relation == RelationType.ON_TOP_OF:
        target_dimensions = support_surface_dimensions(
            target.dimensions_m,
            target.support_surface_dimensions_m,
        )
        target_shape = support_surface_shape(
            target.footprint_shape,
            target.support_surface_shape,
        )
        required_margin = target.support_margin_m
        vertical_limit = float("inf")
    elif relation == RelationType.INSIDE:
        if target.interior_dimensions_m is None:
            return None
        target_dimensions = target.interior_dimensions_m[:2]
        target_shape = "box"
        required_margin = 0.005
        vertical_limit = target.interior_dimensions_m[2] / source.dimensions_m[2]
    else:
        return 1.0

    limits: list[float] = []
    for degrees in range(0, 91):
        footprint = footprint_2d(
            source.dimensions_m,
            math.radians(degrees),
            source.footprint_shape,
        )
        if target_shape == "circle":
            available_radius = min(target_dimensions) / 2.0 - required_margin
            horizontal_limit = available_radius / footprint.radius
        else:
            available_x = target_dimensions[0] / 2.0 - required_margin
            available_y = target_dimensions[1] / 2.0 - required_margin
            horizontal_limit = min(
                available_x / footprint.half_x,
                available_y / footprint.half_y,
            )
        limits.append(horizontal_limit)
    return min(vertical_limit, max(limits))


def _scaled_value(value: tuple[float, ...] | None, factor: float) -> tuple[float, ...] | None:
    if value is None:
        return None
    if factor == 1.0:
        return value
    return tuple(round(component * factor, 12) for component in value)


def _support_depth(object_id: str, supports: dict[str, Any]) -> int:
    relation = supports.get(object_id)
    if relation is None or relation.target == "table":
        return 0
    return 1 + _support_depth(relation.target, supports)


def _is_block_category(category: str) -> bool:
    normalized = category.lower()
    return normalized in {"block", "cube"} or "block" in normalized or "cube" in normalized


def ensure_assets_for_scene(
    spec: SceneSpec,
    catalog: AssetCatalog,
    *,
    objects_root: Path | None = None,
) -> tuple[AssetCatalog, dict[str, Any]]:
    """Generate missing assets and derived scales required by nested geometry."""

    root = (objects_root or Path(catalog.objects_root)).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    entries = list(catalog.entries)
    generated: list[dict[str, Any]] = []
    effective = catalog
    supports = {
        relation.source: relation
        for relation in spec.relations
        if relation.relation in {
            RelationType.ON_TABLE,
            RelationType.ON_TOP_OF,
            RelationType.INSIDE,
        }
    }
    queries = {query.object_id: query for query in spec.objects}
    ordered = sorted(
        spec.objects,
        key=lambda item: (_support_depth(item.object_id, supports), item.object_id),
    )
    for query in ordered:
        try:
            selection = ground_object(query, effective, seed=spec.seed)
        except SceneSpecError:
            entry, provenance = _write_proxy_asset(query, root)
            entries.append(entry)
            generated.append(provenance)
            effective = AssetCatalog(
                robotwin_root=catalog.robotwin_root,
                objects_root=str(root),
                source_commit=catalog.source_commit,
                entries=tuple(sorted(entries, key=lambda item: item.asset_id)),
            )
            selection = ground_object(query, effective, seed=spec.seed)

        support = supports.get(query.object_id)
        if support is None:
            raise SceneSpecError(f"missing support relation for {query.object_id}")

        target: GroundedSelection | None = None
        factor = 1.0
        adaptation_reasons: list[str] = []
        source_key = (selection.entry.asset_id, selection.model.model_id)
        if source_key in PRIMITIVE_PROXY_SOURCES:
            adaptation_reasons.append("source_runtime_instability")

        if support.relation in {RelationType.ON_TOP_OF, RelationType.INSIDE}:
            target_query = queries[support.target]
            target = ground_object(target_query, effective, seed=spec.seed)
            scale_limit = _uniform_scale_limit(
                selection.model,
                target.model,
                support.relation,
            )
            if scale_limit is None:
                raise SceneSpecError(
                    f"missing compatibility geometry for {query.object_id} and {support.target}"
                )
            if scale_limit < 1.0:
                factor = math.floor(scale_limit * SCALE_HEADROOM * 1000.0) / 1000.0
                adaptation_reasons.append("nested_geometry_incompatible")

        if not adaptation_reasons:
            continue
        if factor < MIN_DERIVED_SCALE:
            raise SceneSpecError(
                f"required scale {factor:.3f} for {query.object_id} is below "
                f"minimum {MIN_DERIVED_SCALE:.3f}"
            )
        if not _is_block_category(query.category):
            raise SceneSpecError(
                f"automatic derived scaling is not runtime-qualified for {query.category}"
            )
        dimensions = _scaled_value(selection.model.dimensions_m, factor)
        if dimensions is None or len(dimensions) != 3:
            raise SceneSpecError(f"missing source dimensions for {query.object_id}")
        entry, provenance = _write_proxy_asset(
            query,
            root,
            dimensions_m=dimensions,
            source=selection,
            uniform_scale_factor=factor,
            relation=support.relation,
            target=target,
            adaptation_reasons=tuple(adaptation_reasons),
        )
        entries.append(entry)
        generated.append(provenance)
        effective = AssetCatalog(
            robotwin_root=catalog.robotwin_root,
            objects_root=str(root),
            source_commit=catalog.source_commit,
            entries=tuple(sorted(entries, key=lambda item: item.asset_id)),
        )
    return effective, {
        "schema_version": "robotwin.asset_generation_report.v1",
        "generator": GENERATOR_VERSION,
        "scene_id": spec.scene_id,
        "status": "generated" if generated else "not_needed",
        "generated_count": len(generated),
        "procedural_count": sum(
            item.get("generation_kind") == "procedural_proxy" for item in generated
        ),
        "derived_scale_count": sum(
            item.get("generation_kind") == "derived_scaled_proxy" for item in generated
        ),
        "scaled_proxy_count": sum(
            item.get("generation_kind") == "derived_scaled_proxy"
            and item.get("uniform_scale_factor", 1.0) < 1.0
            for item in generated
        ),
        "stabilized_proxy_count": sum(
            item.get("generation_kind") == "derived_scaled_proxy"
            and item.get("uniform_scale_factor") == 1.0
            for item in generated
        ),
        "generated": generated,
        "base_catalog_sha256": catalog.digest(),
        "effective_catalog_sha256": effective.digest(),
    }
