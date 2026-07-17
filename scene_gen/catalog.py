"""Deterministic scanner for the real RoboTwin object asset tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field

from .schema import StrictModel

CATALOG_SCHEMA_VERSION = "robotwin.asset_catalog.v1"


class CatalogJoint(StrictModel):
    name: str
    joint_type: str
    lower: float
    upper: float


class CatalogModel(StrictModel):
    model_id: int = Field(ge=0)
    model_path: str | None = None
    metadata_path: str | None = None
    visual_path: str | None = None
    collision_path: str | None = None
    urdf_path: str | None = None
    scale: tuple[float, float, float] | None = None
    dimensions_m: tuple[float, float, float] | None = None
    interior_dimensions_m: tuple[float, float, float] | None = None
    interior_floor_z_offset_m: float | None = Field(default=None, ge=0.0)
    footprint_shape: Literal["box", "circle"] = "box"
    support_surface_shape: Literal["box", "circle"] | None = None
    support_surface_dimensions_m: tuple[float, float] | None = None
    support_surface_z_offset_m: float | None = Field(default=None, ge=0.0)
    support_margin_m: float = Field(default=0.005, ge=0.0)
    support_spawn_clearance_m: float = Field(default=0.003, ge=0.0, le=0.02)
    stable_pose_id: str | None = None
    stable_orientation_wxyz: tuple[float, float, float, float] | None = None
    z_policy: Literal["origin_on_table", "center_on_table"] = "origin_on_table"
    is_static: bool = False
    articulation_joints: tuple[CatalogJoint, ...] = ()
    articulation_closed_qpos: tuple[float, ...] = ()
    articulation_open_qpos: tuple[float, ...] = ()
    usable: bool
    missing: tuple[str, ...] = ()


class CatalogEntry(StrictModel):
    asset_id: str
    semantic_name: str
    category: str
    aliases: tuple[str, ...]
    colors: tuple[str, ...] = ()
    materials: tuple[str, ...] = ()
    load_type: Literal["rigid", "urdf", "unsupported"]
    asset_path: str
    models: tuple[CatalogModel, ...]
    available: bool
    availability_reasons: tuple[str, ...] = ()
    source_notes: tuple[str, ...] = ()


class AssetCatalog(StrictModel):
    schema_version: Literal[CATALOG_SCHEMA_VERSION] = CATALOG_SCHEMA_VERSION
    robotwin_root: str
    objects_root: str
    source_commit: str | None = None
    entries: tuple[CatalogEntry, ...]

    def canonical_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)

    def digest(self) -> str:
        payload = json.dumps(
            self.canonical_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _triplet(value: Any) -> tuple[float, float, float] | None:
    if isinstance(value, (int, float)):
        number = float(value)
        return (number, number, number)
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    if not all(isinstance(item, (int, float)) for item in value):
        return None
    return tuple(float(item) for item in value)


def _pair(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if not all(isinstance(item, (int, float)) for item in value):
        return None
    return tuple(float(item) for item in value)


def _scaled_dimensions(metadata: dict[str, Any]) -> tuple[float, float, float] | None:
    extents = _triplet(metadata.get("extents"))
    scale = _triplet(metadata.get("scale"))
    if extents is None or scale is None:
        return None
    source = tuple(abs(extent * factor) for extent, factor in zip(extents, scale))
    # RoboTwin metadata is authored in mesh x/y-up/z coordinates. SAPIEN's
    # GLB import maps it to world x/y/z-up, so depth and height swap here.
    return (source[0], source[2], source[1])


def _bounding_box_dimensions(model_dir: Path, metadata: dict[str, Any]) -> tuple[float, float, float] | None:
    bounds = _read_json(model_dir / "bounding_box.json")
    lower = _triplet(bounds.get("min"))
    upper = _triplet(bounds.get("max"))
    scale = _triplet(metadata.get("scale"))
    if lower is None or upper is None or scale is None:
        return None
    extents = tuple(abs(high - low) * factor for low, high, factor in zip(lower, upper, scale))
    return (extents[0], extents[2], extents[1])


def _float_tuple(value: Any) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    if not all(isinstance(item, (int, float)) for item in value):
        return ()
    return tuple(float(item) for item in value)


def _parse_articulation_joints(urdf_path: Path | None) -> tuple[CatalogJoint, ...]:
    if urdf_path is None:
        return ()
    try:
        root = ET.parse(urdf_path).getroot()
    except (ET.ParseError, OSError):
        return ()
    joints: list[CatalogJoint] = []
    for joint in root.findall("joint"):
        joint_type = str(joint.get("type") or "")
        if joint_type == "fixed":
            continue
        limit = joint.find("limit")
        if joint_type == "continuous":
            lower, upper = -3.141592653589793, 3.141592653589793
        elif limit is not None and limit.get("lower") is not None and limit.get("upper") is not None:
            try:
                lower, upper = float(limit.get("lower")), float(limit.get("upper"))
            except (TypeError, ValueError):
                continue
        else:
            continue
        if lower > upper:
            lower, upper = upper, lower
        joints.append(
            CatalogJoint(
                name=str(joint.get("name") or f"joint_{len(joints)}"),
                joint_type=joint_type,
                lower=lower,
                upper=upper,
            )
        )
    return tuple(joints)


def _model_id(path: Path) -> int:
    match = re.search(r"(?:model_data|base|textured)(\d+)", path.stem)
    return int(match.group(1)) if match else 0


def _first_existing(paths: list[Path]) -> Path | None:
    return next((path.resolve() for path in paths if path.is_file()), None)


def _mesh_candidates(asset_dir: Path, kind: str, model_id: int) -> list[Path]:
    suffixes = [str(model_id)]
    if model_id == 0:
        suffixes.append("")
    names = [
        name
        for suffix in suffixes
        for name in (f"base{suffix}.glb", f"base{suffix}.obj", f"textured{suffix}.obj")
    ]
    directories = [asset_dir / kind, asset_dir] if kind in {"visual", "collision"} else [asset_dir]
    return [directory / name for directory in directories for name in names]


def _normalized_semantic(asset_id: str) -> str:
    semantic = re.sub(r"^\d+[_-]?", "", asset_id).lower().replace("-", "_")
    return re.sub(r"_+", "_", semantic).strip("_") or asset_id.lower()


def _override_for(overrides: dict[str, Any], asset_id: str, model_id: int) -> dict[str, Any]:
    asset_override = (overrides.get("assets") or {}).get(asset_id) or {}
    model_override = (asset_override.get("models") or {}).get(str(model_id)) or {}
    merged = {key: value for key, value in asset_override.items() if key != "models"}
    merged.update(model_override)
    return merged


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    values = value if isinstance(value, list) else [value]
    return tuple(sorted({str(item).strip().lower() for item in values if str(item).strip()}))


def _scan_model(
    asset_dir: Path,
    model_dir: Path,
    load_type: Literal["rigid", "urdf", "unsupported"],
    model_id: int,
    override: dict[str, Any],
) -> CatalogModel:
    metadata_candidates = [
        model_dir / "model_data.json",
        model_dir / (f"model_data{model_id}.json" if model_id else "model_data0.json"),
        asset_dir / (f"model_data{model_id}.json" if model_id else "model_data0.json"),
        asset_dir / "model_data.json",
    ]
    metadata_path = _first_existing(metadata_candidates)
    metadata = _read_json(metadata_path) if metadata_path else {}
    urdf_path = _first_existing([model_dir / "mobility.urdf", model_dir / "mobility_vhacd.urdf"])
    visual_path = _first_existing(_mesh_candidates(model_dir, "visual", model_id))
    collision_path = _first_existing(_mesh_candidates(model_dir, "collision", model_id))
    if model_dir != asset_dir and load_type != "urdf":
        visual_path = visual_path or _first_existing(_mesh_candidates(asset_dir, "visual", model_id))
        collision_path = collision_path or _first_existing(_mesh_candidates(asset_dir, "collision", model_id))
    if load_type == "urdf":
        visual_path = visual_path or urdf_path
        collision_path = collision_path or urdf_path

    missing: list[str] = []
    if metadata_path is None:
        missing.append("model_metadata")
    if load_type == "rigid":
        if visual_path is None:
            missing.append("visual_mesh")
        if collision_path is None:
            missing.append("collision_mesh")
    elif load_type == "urdf" and urdf_path is None:
        missing.append("mobility_urdf")
    else:
        if load_type == "unsupported":
            missing.append("supported_loader")

    dimensions = (
        _triplet(override.get("dimensions_m"))
        or _scaled_dimensions(metadata)
        or _bounding_box_dimensions(model_dir, metadata)
    )
    if dimensions is None:
        missing.append("dimensions_m")
    footprint_shape = str(override.get("footprint_shape", "box"))
    if footprint_shape not in {"box", "circle"}:
        raise ValueError(f"invalid footprint_shape: {asset_dir.name}/{model_id}")
    support_surface_dimensions = _pair(override.get("support_surface_dimensions_m"))
    support_surface_shape = override.get("support_surface_shape")
    support_surface_z_offset = override.get("support_surface_z_offset_m")
    interior_dimensions = _triplet(override.get("interior_dimensions_m"))
    interior_floor_z_offset = override.get("interior_floor_z_offset_m")
    if interior_floor_z_offset is not None:
        if interior_dimensions is None:
            raise ValueError(
                f"interior_floor_z_offset_m requires interior dimensions: {asset_dir.name}/{model_id}"
            )
        if not isinstance(interior_floor_z_offset, (int, float)):
            raise ValueError(
                f"interior_floor_z_offset_m must be numeric: {asset_dir.name}/{model_id}"
            )
        if dimensions is not None and (
            float(interior_floor_z_offset) + interior_dimensions[2] > dimensions[2] + 1e-9
        ):
            raise ValueError(f"interior exceeds object height: {asset_dir.name}/{model_id}")
    if support_surface_dimensions is not None:
        if support_surface_shape not in {"box", "circle"}:
            raise ValueError(
                f"support_surface_shape is required with support dimensions: {asset_dir.name}/{model_id}"
            )
        if not all(value > 0.0 for value in support_surface_dimensions):
            raise ValueError(f"support surface dimensions must be positive: {asset_dir.name}/{model_id}")
        if not isinstance(support_surface_z_offset, (int, float)):
            raise ValueError(
                f"support_surface_z_offset_m is required with support dimensions: {asset_dir.name}/{model_id}"
            )
        if dimensions is not None and (
            support_surface_dimensions[0] > dimensions[0]
            or support_surface_dimensions[1] > dimensions[1]
            or float(support_surface_z_offset) > dimensions[2]
        ):
            raise ValueError(f"support surface exceeds object bounds: {asset_dir.name}/{model_id}")
    elif support_surface_shape is not None or support_surface_z_offset is not None:
        raise ValueError(
            f"support surface metadata must include dimensions, shape, and z offset: {asset_dir.name}/{model_id}"
        )
    scale = _triplet(override.get("scale")) or _triplet(metadata.get("scale"))
    if scale is None:
        missing.append("scale")
    stable_pose_id = override.get("stable_pose_id")
    if not stable_pose_id:
        missing.append("stable_pose")
    raw_orientation = override.get("stable_orientation_wxyz", [1.0, 0.0, 0.0, 0.0])
    if not isinstance(raw_orientation, (list, tuple)) or len(raw_orientation) != 4:
        raise ValueError(f"stable_orientation_wxyz must have four values: {asset_dir.name}/{model_id}")
    stable_orientation_wxyz = tuple(float(value) for value in raw_orientation)
    orientation_norm = sum(value * value for value in stable_orientation_wxyz) ** 0.5
    if orientation_norm <= 0:
        raise ValueError(f"stable orientation cannot be zero: {asset_dir.name}/{model_id}")
    stable_orientation_wxyz = tuple(value / orientation_norm for value in stable_orientation_wxyz)
    articulation_joints = _parse_articulation_joints(urdf_path)
    closed_qpos = _float_tuple(override.get("articulation_closed_qpos"))
    if not closed_qpos:
        closed_qpos = _float_tuple(metadata.get("init_qpos"))
    if articulation_joints and len(closed_qpos) != len(articulation_joints):
        closed_qpos = tuple(joint.lower for joint in articulation_joints)
    open_qpos = _float_tuple(override.get("articulation_open_qpos"))
    if articulation_joints and len(open_qpos) != len(articulation_joints):
        open_qpos = tuple(joint.upper for joint in articulation_joints)
    if articulation_joints:
        closed_qpos = tuple(
            min(joint.upper, max(joint.lower, value))
            for joint, value in zip(articulation_joints, closed_qpos)
        )
        open_qpos = tuple(
            min(joint.upper, max(joint.lower, value))
            for joint, value in zip(articulation_joints, open_qpos)
        )
        scale_factor = scale[0] if scale is not None else 1.0
        closed_qpos = tuple(
            value * scale_factor if joint.joint_type == "prismatic" else value
            for joint, value in zip(articulation_joints, closed_qpos)
        )
        open_qpos = tuple(
            value * scale_factor if joint.joint_type == "prismatic" else value
            for joint, value in zip(articulation_joints, open_qpos)
        )
        articulation_joints = tuple(
            CatalogJoint(
                name=joint.name,
                joint_type=joint.joint_type,
                lower=joint.lower * scale_factor if joint.joint_type == "prismatic" else joint.lower,
                upper=joint.upper * scale_factor if joint.joint_type == "prismatic" else joint.upper,
            )
            for joint in articulation_joints
        )
    usable = not any(
        reason in missing
        for reason in ("visual_mesh", "collision_mesh", "mobility_urdf", "supported_loader", "dimensions_m", "stable_pose")
    )
    return CatalogModel(
        model_id=model_id,
        model_path=str(model_dir.resolve()),
        metadata_path=str(metadata_path) if metadata_path else None,
        visual_path=str(visual_path) if visual_path else None,
        collision_path=str(collision_path) if collision_path else None,
        urdf_path=str(urdf_path) if urdf_path else None,
        scale=scale,
        dimensions_m=dimensions,
        interior_dimensions_m=interior_dimensions,
        interior_floor_z_offset_m=(
            float(interior_floor_z_offset) if interior_floor_z_offset is not None else None
        ),
        footprint_shape=footprint_shape,
        support_surface_shape=support_surface_shape,
        support_surface_dimensions_m=support_surface_dimensions,
        support_surface_z_offset_m=(
            float(support_surface_z_offset) if support_surface_z_offset is not None else None
        ),
        support_margin_m=float(override.get("support_margin_m", 0.005)),
        support_spawn_clearance_m=float(override.get("support_spawn_clearance_m", 0.003)),
        stable_pose_id=str(stable_pose_id) if stable_pose_id else None,
        stable_orientation_wxyz=stable_orientation_wxyz if stable_pose_id else None,
        z_policy=override.get("z_policy", "origin_on_table"),
        is_static=bool(override.get("is_static", False)),
        articulation_joints=articulation_joints,
        articulation_closed_qpos=closed_qpos,
        articulation_open_qpos=open_qpos,
        usable=usable,
        missing=tuple(sorted(set(missing))),
    )


def load_overrides(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    with path.open("r", encoding="utf-8") as stream:
        value = yaml.safe_load(stream) or {}
    if not isinstance(value, dict):
        raise ValueError(f"asset overrides must be a mapping: {path}")
    return value


def scan_robotwin_assets(
    robotwin_root: Path,
    *,
    overrides_path: Path | None = None,
    source_commit: str | None = None,
) -> tuple[AssetCatalog, dict[str, Any]]:
    root = robotwin_root.expanduser().resolve()
    objects_root = root / "assets" / "objects"
    if not objects_root.is_dir():
        raise FileNotFoundError(f"RoboTwin objects root does not exist: {objects_root}")
    overrides = load_overrides(overrides_path)
    entries: list[CatalogEntry] = []
    for asset_dir in sorted((path for path in objects_root.iterdir() if path.is_dir()), key=lambda path: path.name):
        generated_provenance = _read_json(asset_dir / "generation_provenance.json")
        direct_urdf = (asset_dir / "mobility.urdf").is_file() or (asset_dir / "mobility_vhacd.urdf").is_file()
        nested_urdf_dirs = sorted(
            (
                path
                for path in asset_dir.iterdir()
                if path.is_dir()
                and path.name.isdigit()
                and ((path / "mobility.urdf").is_file() or (path / "mobility_vhacd.urdf").is_file())
            ),
            key=lambda path: int(path.name),
        )
        urdf_exists = direct_urdf or bool(nested_urdf_dirs)
        mesh_exists = any(asset_dir.glob("**/*.glb")) or any(asset_dir.glob("**/*.obj"))
        load_type: Literal["rigid", "urdf", "unsupported"]
        load_type = "urdf" if urdf_exists else "rigid" if mesh_exists else "unsupported"
        model_dirs: dict[int, Path] = {}
        if nested_urdf_dirs:
            model_dirs = {int(path.name): path for path in nested_urdf_dirs}
        elif direct_urdf:
            model_dirs = {0: asset_dir}
        else:
            metadata_files = sorted(asset_dir.glob("model_data*.json"), key=lambda path: (_model_id(path), path.name))
            model_ids = {_model_id(path) for path in metadata_files}
            for mesh in asset_dir.glob("**/*"):
                if mesh.is_file() and mesh.suffix.lower() in {".glb", ".obj"}:
                    model_ids.add(_model_id(mesh))
            if not model_ids:
                model_ids.add(0)
            model_dirs = {model_id: asset_dir for model_id in model_ids}
        models = tuple(
            _scan_model(
                asset_dir,
                model_dirs[model_id],
                load_type,
                model_id,
                {
                    **(
                        {
                            "dimensions_m": generated_provenance.get("dimensions_m"),
                            "stable_pose_id": "procedural_flat_base",
                            "z_policy": "origin_on_table",
                        }
                        if generated_provenance and model_id == 0
                        else {}
                    ),
                    **_override_for(overrides, asset_dir.name, model_id),
                },
            )
            for model_id in sorted(model_dirs)
        )
        asset_override = (overrides.get("assets") or {}).get(asset_dir.name) or {}
        generated_category = generated_provenance.get("semantic_category")
        semantic_name = str(
            asset_override.get("semantic_name")
            or generated_category
            or _normalized_semantic(asset_dir.name)
        )
        category = str(asset_override.get("category") or generated_category or semantic_name).lower().replace(" ", "_")
        aliases = _string_tuple(
            [semantic_name, semantic_name.replace("_", " "), *(asset_override.get("aliases") or [])]
        )
        available = any(model.usable for model in models)
        reasons = sorted({reason for model in models for reason in model.missing})
        entries.append(
            CatalogEntry(
                asset_id=asset_dir.name,
                semantic_name=semantic_name,
                category=category,
                aliases=aliases,
                colors=_string_tuple(
                    asset_override.get("colors")
                    or generated_provenance.get("requested_color")
                ),
                materials=_string_tuple(
                    asset_override.get("materials")
                    or generated_provenance.get("requested_material")
                ),
                load_type=load_type,
                asset_path=str(asset_dir.resolve()),
                models=models,
                available=available,
                availability_reasons=tuple(reasons),
                source_notes=_string_tuple(
                    [
                        *_string_tuple(asset_override.get("source_notes")),
                        *(
                            ["procedural_generated", generated_provenance.get("generator"), "proxy_geometry"]
                            if generated_provenance
                            else []
                        ),
                    ]
                ),
            )
        )
    catalog = AssetCatalog(
        robotwin_root=str(root),
        objects_root=str(objects_root.resolve()),
        source_commit=source_commit,
        entries=tuple(entries),
    )
    missing_report = {
        "schema_version": "robotwin.asset_catalog_missing.v1",
        "asset_catalog_sha256": catalog.digest(),
        "entry_count": len(entries),
        "available_entry_count": sum(entry.available for entry in entries),
        "unavailable_entry_count": sum(not entry.available for entry in entries),
        "entries": [
            {
                "asset_id": entry.asset_id,
                "available": entry.available,
                "load_type": entry.load_type,
                "reasons": list(entry.availability_reasons),
                "models": [
                    {
                        "model_id": model.model_id,
                        "usable": model.usable,
                        "missing": list(model.missing),
                    }
                    for model in entry.models
                ],
            }
            for entry in entries
            if entry.availability_reasons or not entry.available
        ],
    }
    return catalog, missing_report


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def load_catalog(path: Path) -> AssetCatalog:
    return AssetCatalog.model_validate_json(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan a real RoboTwin assets/objects tree.")
    parser.add_argument("--robotwin-root", required=True)
    parser.add_argument("--overrides", default=str(Path(__file__).with_name("asset_overrides.yml")))
    parser.add_argument("--source-commit")
    parser.add_argument("--out", required=True)
    parser.add_argument("--missing-out", required=True)
    args = parser.parse_args()
    catalog, missing = scan_robotwin_assets(
        Path(args.robotwin_root),
        overrides_path=Path(args.overrides),
        source_commit=args.source_commit,
    )
    write_json(Path(args.out), catalog.canonical_dict())
    write_json(Path(args.missing_out), missing)
    print(
        f"PASS entries={len(catalog.entries)} available={sum(entry.available for entry in catalog.entries)} "
        f"sha256={catalog.digest()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
