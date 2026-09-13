"""Mesh-only normalization, not a physical qualification or an AssetRegistry.

dimensions_m is the target XYZ AABB after rotation to Z-up. Collision is an
explicit convex-hull proxy: concavities are filled, so this module grants no
container/inside profile. URDF, skins and animation require separate adapters.
Mass/friction are supplied, not measured; inertia uses hull uniform density.
Original source/version ownership stays with the caller and AssetRegistry.
"""

import hashlib
import io
import json
import math
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import trimesh


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _static_source(raw, suffix):
    if suffix == ".glb":
        if len(raw) < 20 or raw[:4] != b"glTF":
            raise ValueError("invalid GLB header")
        version, length, json_length, kind = struct.unpack("<IIII", raw[4:20])
        if version != 2 or length != len(raw) or kind != 0x4E4F534A:
            raise ValueError("invalid GLB length/version/JSON chunk")
        document = json.loads(raw[20 : 20 + json_length])
        if document.get("extensionsUsed") or document.get("extensionsRequired"):
            raise ValueError("unsupported GLB extensions; material/geometry loss is not allowed")
        if any(document.get("samplers", [])):
            raise ValueError("unsupported nondefault texture sampler")
        for material in document.get("materials", []):
            if material.get("occlusionTexture") or material.get("extensions"):
                raise ValueError("unsupported material fields")
            for name in ("normalTexture", "emissiveTexture"):
                texture = material.get(name, {})
                if texture.get("texCoord", 0) != 0 or "scale" in texture:
                    raise ValueError("unsupported material texture coordinates/scale")
            pbr = material.get("pbrMetallicRoughness", {})
            for name in ("baseColorTexture", "metallicRoughnessTexture"):
                if pbr.get(name, {}).get("texCoord", 0) != 0:
                    raise ValueError("unsupported material texture coordinates")
        if document.get("skins") or document.get("animations"):
            raise ValueError("unsupported skinned/animated mesh: articulation cannot be flattened")
        for group in ("buffers", "images"):
            for member in document.get(group, []):
                uri = member.get("uri", "")
                if uri and not uri.startswith("data:"):
                    raise ValueError("unsupported external GLB resource")
        for mesh in document.get("meshes", []):
            if mesh.get("weights") or any(p.get("targets") for p in mesh.get("primitives", [])):
                raise ValueError("unsupported morph targets")
    elif suffix == ".obj":
        if any(
            line.strip().startswith(("mtllib ", "usemtl ")) for line in raw.decode().splitlines()
        ):
            raise ValueError("unsupported OBJ material closure; provide self-contained GLB")


def _number(value, *, positive):
    return (
        type(value) in (int, float)
        and math.isfinite(value)
        and (value > 0 if positive else value >= 0)
    )


def normalize_mesh(
    source_path, new_output_dir, *, dimensions_m, up_axis, mass_kg, friction, color_rgba=None
):
    """Return local resource refs and a hash-bound measured geometry report."""
    source, output = Path(source_path), Path(new_output_dir)
    suffix = source.suffix.lower()
    if suffix not in {".glb", ".obj", ".stl", ".ply"}:
        raise ValueError("unsupported source: only static single-file mesh formats")
    if (
        len(dimensions_m) != 3
        or not all(_number(v, positive=True) for v in dimensions_m)
        or up_axis not in {"Y", "Z"}
        or not _number(mass_kg, positive=True)
        or not _number(friction, positive=False)
    ):
        raise ValueError("invalid dimensions/axis/supplied physical parameters")
    if color_rgba is not None and (
        len(color_rgba) != 4 or not all(_number(v, positive=False) and v <= 1 for v in color_rgba)
    ):
        raise ValueError("color override must be four floats in [0, 1]")
    if source.is_symlink() or not source.is_file():
        raise ValueError("source must be a regular non-symlink file")
    raw = source.read_bytes()
    _static_source(raw, suffix)
    scene = trimesh.load(io.BytesIO(raw), file_type=suffix[1:], force="scene", process=False)
    pieces, node_records = [], []
    for node in sorted(scene.graph.nodes_geometry):
        world, key = scene.graph[node]
        mesh = scene.geometry[key]
        if not isinstance(mesh, trimesh.Trimesh) or not len(mesh.faces):
            raise ValueError("unsupported non-triangle or empty geometry")
        if not np.isfinite(world).all() or abs(np.linalg.det(world[:3, :3])) < 1e-12:
            raise ValueError("invalid node transform")
        copy = mesh.copy()
        copy.apply_transform(world)
        if not np.isfinite(copy.vertices).all():
            raise ValueError("nonfinite source vertices")
        pieces.append(copy)
        node_records.append({"node": node, "geometry": key, "world_transform": world.tolist()})
    if not pieces:
        raise ValueError("empty source scene")
    rotation = np.eye(4)
    if up_axis == "Y":
        rotation[:3, :3] = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]
    points = np.concatenate([m.vertices for m in pieces])
    rotated = trimesh.transform_points(points, rotation)
    bounds = np.array([rotated.min(axis=0), rotated.max(axis=0)])
    extents = bounds[1] - bounds[0]
    if np.any(extents <= 1e-12):
        raise ValueError("degenerate mesh has zero extent")
    scaling = np.eye(4)
    scaling[:3, :3] = np.diag(np.asarray(dimensions_m) / extents)
    scaled_bounds = bounds * np.diag(scaling)[:3]
    translation = np.eye(4)
    translation[:3, 3] = [
        -scaled_bounds[:, 0].mean(),
        -scaled_bounds[:, 1].mean(),
        -scaled_bounds[0, 2],
    ]
    transform = translation @ scaling @ rotation
    visual = trimesh.Scene()
    for index, piece in enumerate(pieces):
        piece.apply_transform(transform)
        if color_rgba is not None:
            # The fixed Genesis GLB importer consumes PBR factors, not COLOR_0.
            piece.visual = trimesh.visual.TextureVisuals(
                material=trimesh.visual.material.PBRMaterial(
                    baseColorFactor=np.rint(np.array(color_rgba) * 255).astype(np.uint8),
                    metallicFactor=0.0,
                    roughnessFactor=1.0,
                ),
            )
        visual.add_geometry(piece, geom_name=f"part_{index}", node_name=f"part_{index}")
    hull = trimesh.util.concatenate(pieces).convex_hull
    if not hull.is_volume or not np.isfinite(hull.volume) or hull.volume <= 0:
        raise ValueError("cannot build finite solid convex hull")
    hull.density = mass_kg / hull.volume
    inertia = hull.moment_inertia
    if not np.isfinite(inertia).all() or np.any(np.linalg.eigvalsh(inertia) <= 0):
        raise ValueError("invalid uniform-hull inertia")
    output.mkdir(parents=True, exist_ok=False)
    visual.export(output / "visual.glb")
    hull.export(output / "collision.obj")
    robot = ET.Element("robot", name="normalized_mesh")
    link = ET.SubElement(robot, "link", name="body")
    inertial = ET.SubElement(link, "inertial")
    ET.SubElement(inertial, "origin", xyz=" ".join(map(str, hull.center_mass)), rpy="0 0 0")
    ET.SubElement(inertial, "mass", value=str(mass_kg))
    ET.SubElement(
        inertial,
        "inertia",
        **{
            name: str(inertia[i, j])
            for name, i, j in [
                ("ixx", 0, 0),
                ("ixy", 0, 1),
                ("ixz", 0, 2),
                ("iyy", 1, 1),
                ("iyz", 1, 2),
                ("izz", 2, 2),
            ]
        },
    )
    for kind, filename in [("visual", "visual.glb"), ("collision", "collision.obj")]:
        member = ET.SubElement(link, kind)
        ET.SubElement(member, "origin", xyz="0 0 0", rpy="0 0 0")
        geometry = ET.SubElement(member, "geometry")
        ET.SubElement(geometry, "mesh", filename=filename, scale="1 1 1")
    ET.ElementTree(robot).write(output / "asset.urdf", encoding="utf-8", xml_declaration=True)
    physics = {
        "mass_kg": mass_kg,
        "friction": friction,
        "parameter_basis": "supplied_not_measured",
        "inertia_basis": "convex_hull_uniform_density",
        "inertia_kg_m2": inertia.tolist(),
        "center_of_mass_m": hull.center_mass.tolist(),
        "friction_application": "requires_runtime_physics_json_consumer",
    }
    (output / "physics.json").write_text(json.dumps(physics, indent=2) + "\n")
    result = {
        "schema_version": "x2env.normalization.v1",
        "status": "passed",
        "entrypoint": "asset.urdf",
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_geometry_instances": len(pieces),
        "source_nodes": node_records,
        "source_up_axis": up_axis,
        "target_up_axis": "Z",
        "world_to_normalized": transform.tolist(),
        "dimensions_m": list(dimensions_m),
        "measured_visual_bounds_m": visual.bounds.tolist(),
        "collision": {
            "basis": "convex_hull",
            "fills_concavities": True,
            "bounds_m": hull.bounds.tolist(),
            "volume_m3": float(hull.volume),
        },
        "checks": {
            "geometry_normalization": "passed",
            "genesis_load_step": "not_run",
            "physical_profile": "not_run",
            "container_inside_profile": "not_run",
        },
        "material_policy": (
            "explicit_color_override" if color_rgba is not None else "preserved_supported_glb_core"
        ),
        "color_override_rgba": color_rgba,
    }
    result["files"] = [
        {"path": name, "sha256": _sha(output / name), "size_bytes": (output / name).stat().st_size}
        for name in ["asset.urdf", "visual.glb", "collision.obj", "physics.json"]
    ]
    (output / "normalization.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
