"""Deterministic RoboTwin-compatible proxy generation for catalog misses."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from .catalog import AssetCatalog, CatalogEntry, CatalogModel
from .grounding import ground_object
from .schema import SceneObjectSpec, SceneSpec, SceneSpecError

GENERATOR_VERSION = "scene_gen.procedural_proxy.v1"

COLOR_RGB = {
    "black": (0.08, 0.09, 0.10),
    "blue": (0.10, 0.32, 0.78),
    "brown": (0.38, 0.19, 0.08),
    "green": (0.12, 0.55, 0.28),
    "orange": (0.95, 0.38, 0.06),
    "pink": (0.93, 0.35, 0.58),
    "purple": (0.48, 0.18, 0.72),
    "red": (0.82, 0.10, 0.12),
    "white": (0.82, 0.84, 0.86),
    "yellow": (0.92, 0.72, 0.08),
}


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
    return "bounded_box_proxy", 4, (0.08, 0.08, 0.06)


def _prism_obj(
    *,
    sides: int,
    dimensions_m: tuple[float, float, float],
    with_material: bool,
) -> str:
    width, depth, height = dimensions_m
    vertices: list[tuple[float, float, float]] = []
    for z in (0.0, height):
        for index in range(sides):
            angle = 2.0 * math.pi * index / sides + math.pi / 4.0
            vertices.append((0.5 * width * math.cos(angle), 0.5 * depth * math.sin(angle), z))
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


def _write_proxy_asset(query: SceneObjectSpec, objects_root: Path) -> tuple[CatalogEntry, dict[str, Any]]:
    identity = json.dumps(
        {
            "generator": GENERATOR_VERSION,
            "category": query.category,
            "color": query.color,
            "material": query.material,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    asset_id = f"900_gen_{_slug(query.category)}_{digest[:8]}"
    asset_dir = objects_root / asset_id
    visual_dir = asset_dir / "visual"
    collision_dir = asset_dir / "collision"
    visual_dir.mkdir(parents=True, exist_ok=True)
    collision_dir.mkdir(parents=True, exist_ok=True)

    geometry_family, sides, dimensions = _geometry(query)
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
        "generator": GENERATOR_VERSION,
        "asset_id": asset_id,
        "semantic_category": query.category,
        "requested_color": query.color,
        "requested_material": query.material,
        "geometry_family": geometry_family,
        "geometry_fidelity": "semantic_shape_when_recognized_otherwise_bounded_proxy",
        "generated_license": "project_generated_academic_artifact",
        "dimensions_m": list(dimensions),
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
        semantic_name=query.category,
        category=query.category,
        aliases=tuple(sorted({query.category, query.category.replace("_", " ")})),
        colors=(query.color,) if query.color else (),
        materials=(query.material,) if query.material else (),
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
        source_notes=("procedural_generated", GENERATOR_VERSION, "proxy_geometry"),
    )
    return entry, provenance


def ensure_assets_for_scene(
    spec: SceneSpec,
    catalog: AssetCatalog,
    *,
    objects_root: Path | None = None,
) -> tuple[AssetCatalog, dict[str, Any]]:
    """Generate deterministic proxy assets only for queries that cannot be grounded."""

    root = (objects_root or Path(catalog.objects_root)).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    entries = list(catalog.entries)
    generated: list[dict[str, Any]] = []
    effective = catalog
    for query in sorted(spec.objects, key=lambda item: item.object_id):
        try:
            ground_object(query, effective, seed=spec.seed)
            continue
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
    return effective, {
        "schema_version": "robotwin.asset_generation_report.v1",
        "generator": GENERATOR_VERSION,
        "scene_id": spec.scene_id,
        "status": "generated" if generated else "not_needed",
        "generated_count": len(generated),
        "generated": generated,
        "base_catalog_sha256": catalog.digest(),
        "effective_catalog_sha256": effective.digest(),
    }
