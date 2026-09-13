"""Immutable authored support surfaces; no actual-loaded or physical authority.

The caller selects among measured planes; this module never chooses the highest one.
Polygon operations preserve holes and reject invalid geometry without repair.
"""

import hashlib
import io
import json
import math
import numbers
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath
from typing import Literal

import numpy as np
import shapely
import trimesh
from scipy.spatial.transform import Rotation
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

from .contracts import Model, Sha256

TOLERANCE_M = 1e-5  # Existing authored/loaded geometric correspondence, not physical margin.


class MeasuredSupportSurface(Model):
    schema_version: Literal["x2env.authored_support_surface.v1"] = (
        "x2env.authored_support_surface.v1"
    )
    basis: Literal["immutable_authored_mesh_geometry"] = "immutable_authored_mesh_geometry"
    version_sha256: Sha256
    plane_z_m: float
    # Each polygon: exterior followed by holes; every ring retains its closing point.
    polygons: tuple[tuple[tuple[tuple[float, float], ...], ...], ...]
    member_bindings: tuple[tuple[str, Sha256], ...]
    face_sources: tuple[tuple[str, str, str, int, int], ...]
    algorithm: Literal["upward_planar_exposed_intersection.v1"] = (
        "upward_planar_exposed_intersection.v1"
    )
    geometry_tolerance_m: float = TOLERANCE_M
    shapely_version: str
    geos_version: str
    actual_loaded_evaluated: Literal[False] = False
    physical_evaluated: Literal[False] = False


def _sequence(value):
    return isinstance(value, (list, tuple)) or isinstance(value, np.ndarray) and value.ndim > 0


def _numeric_vector(value, size, code, *, integer=False):
    if (
        not _sequence(value)
        or len(value) != size
        or any(
            isinstance(v, (bool, np.bool_))
            or not isinstance(v, numbers.Integral if integer else numbers.Real)
            or not math.isfinite(v)
            for v in value
        )
    ):
        raise ValueError(code)
    return value


def _triangles(vertices, faces):
    if not _sequence(vertices) or not _sequence(faces) or not len(vertices) or not len(faces):
        raise ValueError("invalid_support_mesh")
    for row in vertices:
        _numeric_vector(row, 3, "invalid_support_mesh")
    for row in faces:
        _numeric_vector(row, 3, "invalid_support_mesh", integer=True)
    vertices = np.asarray(vertices)
    raw = np.asarray(faces)
    if (
        vertices.ndim != 2
        or vertices.shape[1:] != (3,)
        or not len(vertices)
        or vertices.dtype.kind not in "fiu"
        or not np.isfinite(vertices).all()
        or raw.ndim != 2
        or raw.shape[1:] != (3,)
        or not len(raw)
        or raw.dtype.kind not in "iu"
        or raw.min() < 0
        or raw.max() >= len(vertices)
    ):
        raise ValueError("invalid_support_mesh")
    triangles = vertices[raw]
    cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    if np.any(np.linalg.norm(cross, axis=1) <= 1e-15):
        raise ValueError("invalid_support_mesh: degenerate triangle")
    return triangles


def _domain(triangles):
    polygons = []
    for tri in triangles:
        if not np.isfinite(tri).all():
            raise ValueError("invalid_support_mesh: nonfinite transformed footprint")
        polygon = Polygon(tri[:, :2])
        if polygon.area == 0:  # Vertical projection is a line, not a repaired polygon.
            continue
        # Finite, nonzero-area three-point rings are simple; no repair is needed.
        polygons.append(polygon)
    return unary_union(polygons)


def _urdf_vector(raw):
    tokens = raw.split()
    if len(tokens) != 3:
        raise ValueError("invalid_support_mesh: invalid URDF transform")
    try:
        value = [float(token) for token in tokens]
    except ValueError as exc:
        raise ValueError("invalid_support_mesh: invalid URDF transform") from exc
    return np.asarray(_numeric_vector(value, 3, "invalid_support_mesh: invalid URDF transform"))


def _verified_members(asset_record, read_member):
    from .assets import AssetVersion

    version = AssetVersion.model_validate(asset_record)
    digest = hashlib.sha256(
        json.dumps(
            version.model_dump(mode="json", exclude={"version_sha256"}),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    if digest != version.version_sha256:
        raise ValueError("support_asset_identity_mismatch")
    raw_members = {}
    for member in version.files:
        path = PurePosixPath(member.path)
        if (
            not member.path
            or path.is_absolute()
            or ".." in path.parts
            or str(path) != member.path
            or "\\" in member.path
            or ":" in member.path
            or member.path in raw_members
        ):
            raise ValueError("support_asset_identity_mismatch: unsafe member")
        raw = read_member(member.path)
        if (
            not isinstance(raw, bytes)
            or len(raw) != member.artifact.size_bytes
            or hashlib.sha256(raw).hexdigest() != member.artifact.sha256
        ):
            raise ValueError("support_asset_identity_mismatch")
        raw_members[member.path] = raw
    if version.entrypoint not in raw_members:
        raise ValueError("support_asset_identity_mismatch: missing entrypoint")
    return version, raw_members


def _parts(version, read_member):
    members = {m.path: m.artifact for m in version.files}
    document = ET.fromstring(read_member(version.entrypoint))
    if document.findall("joint") or len(document.findall("link")) != 1:
        raise ValueError("unsupported_articulation")
    result = {"visual": [], "collision": []}
    for kind in result:
        for part_index, element in enumerate(document.findall(f"link/{kind}")):
            mesh_element = element.find("geometry/mesh")
            if mesh_element is None:
                raise ValueError("invalid_support_mesh: mesh required")
            name = str(PurePosixPath(version.entrypoint).parent / mesh_element.attrib["filename"])
            if name not in members or PurePosixPath(name).suffix not in {".obj", ".glb"}:
                raise ValueError("support_asset_identity_mismatch")
            scale = _urdf_vector(mesh_element.get("scale", "1 1 1"))
            origin = element.find("origin")
            xyz = _urdf_vector(origin.get("xyz", "0 0 0") if origin is not None else "0 0 0")
            rpy = _urdf_vector(origin.get("rpy", "0 0 0") if origin is not None else "0 0 0")
            if any(
                v.shape != (3,) or not np.isfinite(v).all() for v in (scale, xyz, rpy)
            ) or np.any(scale <= 0):
                raise ValueError("invalid_support_mesh: invalid URDF transform")
            transform = np.eye(4)
            transform[:3, :3] = Rotation.from_euler("xyz", rpy).as_matrix() @ np.diag(scale)
            transform[:3, 3] = xyz
            scene = trimesh.load(
                io.BytesIO(read_member(name)),
                file_type=PurePosixPath(name).suffix[1:],
                force="scene",
                process=False,
            )
            for node in sorted(scene.graph.nodes_geometry):
                matrix, key = scene.graph[node]
                if not np.isfinite(matrix).all() or np.linalg.det(matrix[:3, :3]) <= 0:
                    raise ValueError("invalid_support_mesh: unsupported node transform")
                mesh = scene.geometry[key]
                vertices = trimesh.transform_points(mesh.vertices, transform @ matrix)
                triangles = _triangles(vertices, mesh.faces)
                result[kind].extend(
                    (tri, (kind, name, str(node), part_index, index))
                    for index, tri in enumerate(triangles)
                )
        if not result[kind]:
            raise ValueError("support_surface_not_found")
    return result


def _planes(parts):
    groups = []
    for triangle, source in sorted(parts, key=lambda item: float(item[0][:, 2].mean())):
        normal = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
        if normal[2] <= 0 or np.linalg.norm(normal[:2]) > 1e-6 * np.linalg.norm(normal):
            continue
        z = float(triangle[:, 2].mean())
        if np.ptp(triangle[:, 2]) > TOLERANCE_M:
            continue
        if not groups or z - groups[-1][0] > TOLERANCE_M:
            groups.append((z, []))
        groups[-1][1].append((triangle, source))
    return groups


def _exposed(parts, z, plane):
    above = []
    for triangle, _ in parts:
        if triangle[:, 2].max() > z + TOLERANCE_M:
            if triangle[:, 2].min() < z - TOLERANCE_M and _domain([triangle]).area > 0:
                raise ValueError("unsupported_crossing_surface_plane")
            above.append(triangle)
    return _domain([t for t, _ in plane]).difference(_domain(above))


def measure_support_surfaces(version_sha256, *, registry, store):
    """Return all exposed common horizontal planes from an inspected immutable asset.

    No automatic plane selection, mesh repair, convexification, or runtime execution.
    """
    version = registry.inspect(version_sha256)
    refs = {m.path: m.artifact for m in version.files}
    return measure_support_surfaces_from_members(
        version, lambda name: store.read_artifact(refs[name])
    )


def read_asset_geometry_from_members(asset_record, read_member):
    """Verify an immutable record and return complete URDF-local authored geometry.

    The member reader can be CAS-backed or package-relative; it has no Registry authority.
    Geometry centre is the complete visual bounds centre, not the URDF origin.
    """
    version, members = _verified_members(asset_record, read_member)
    parts = _parts(version, members.__getitem__)
    geometry, bounds = {}, {}
    for kind in ("visual", "collision"):
        triangles = np.asarray([tri for tri, _ in parts[kind]])
        vertices = triangles.reshape(-1, 3)
        bounds[kind] = np.asarray([vertices.min(axis=0), vertices.max(axis=0)])
        geometry[kind] = [
            {
                "local_vertices_m": vertices.tolist(),
                "faces": np.arange(len(vertices)).reshape(-1, 3).tolist(),
            }
        ]
    full = np.concatenate([bounds["visual"], bounds["collision"]])
    return {
        "geometry_parts": geometry,
        "visual_center_m": bounds["visual"].mean(axis=0).tolist(),
        "bounds_m": [full.min(axis=0).tolist(), full.max(axis=0).tolist()],
        "visual_bounds_m": bounds["visual"].tolist(),
        "collision_bounds_m": bounds["collision"].tolist(),
    }


def measure_support_surfaces_from_members(asset_record, read_member):
    """Measure all candidates from verified relative member bytes, without a Registry."""
    version, members = _verified_members(asset_record, read_member)
    parts = _parts(version, members.__getitem__)
    surfaces = []
    for z, visual in _planes(parts["visual"]):
        matches = [
            (other, faces)
            for other, faces in _planes(parts["collision"])
            if abs(other - z) <= TOLERANCE_M
        ]
        if len(matches) > 1:
            raise ValueError("ambiguous_support_surface")
        if not matches:
            continue
        other, collision = matches[0]
        domain = _exposed(parts["visual"], z, visual).intersection(
            _exposed(parts["collision"], other, collision)
        )
        if domain.is_empty or domain.area == 0:
            continue
        if not domain.is_valid or not isinstance(domain, (Polygon, MultiPolygon)):
            raise ValueError("invalid_support_mesh: invalid intersection")
        polygons = [domain] if isinstance(domain, Polygon) else list(domain.geoms)
        rings = tuple(
            tuple(
                tuple((float(x), float(y)) for x, y in ring.coords)
                for ring in (polygon.exterior, *polygon.interiors)
            )
            for polygon in polygons
        )
        surfaces.append(
            MeasuredSupportSurface(
                version_sha256=version.version_sha256,
                plane_z_m=z,
                polygons=rings,
                member_bindings=tuple((m.path, m.artifact.sha256) for m in version.files),
                face_sources=tuple(source for _, source in (*visual, *collision)),
                shapely_version=shapely.__version__,
                geos_version=shapely.geos_version_string,
            )
        )
    if not surfaces:
        raise ValueError("support_surface_not_found")
    return tuple(surfaces)


def _pose(value):
    if not isinstance(value, dict):
        raise ValueError("invalid_support_pose")
    position = np.asarray(_numeric_vector(value.get("position_m"), 3, "invalid_support_pose"))
    q = np.asarray(_numeric_vector(value.get("orientation_wxyz"), 4, "invalid_support_pose"))
    if (
        position.shape != (3,)
        or q.shape != (4,)
        or not np.isfinite(position).all()
        or not np.isfinite(q).all()
        or abs(float(q @ q) - 1) > 1e-5
    ):
        raise ValueError("invalid_support_pose")
    return position, Rotation.from_quat(q[[1, 2, 3, 0]]).as_matrix()


def select_unique_support_surface(surfaces, *, source_geometry, source_pose, target_pose, known_z):
    """Choose only a uniquely feasible authored plane; never highest/first.

    Positions are URDF-origin world coordinates. Unknown Z is the sole computed axis.
    The returned pose is a geometric proposal, not a contact/physics verdict.
    """
    if type(known_z) is not bool:
        raise ValueError("invalid known_z selection constraint")
    src, sr = _pose(source_pose)
    target, tr = _pose(target_pose)
    if not np.allclose(tr[:, 2], [0, 0, 1], atol=1e-6):
        raise ValueError("unsupported_nonhorizontal_surface")
    triangles = np.concatenate(
        [
            _triangles(part["local_vertices_m"], part["faces"])
            for kind in ("visual", "collision")
            for part in source_geometry["geometry_parts"][kind]
        ]
    )
    minimum = float((triangles.reshape(-1, 3) @ sr.T)[:, 2].min())
    candidates, feasible = [], []
    for surface in surfaces:
        raw = surface.model_dump_json().encode()
        sha = hashlib.sha256(raw).hexdigest()
        candidates.append(sha)
        required_z = float(target[2] + surface.plane_z_m - minimum)
        if known_z and abs(float(src[2]) - required_z) > TOLERANCE_M:
            continue
        candidate_pose = {
            "position_m": [float(src[0]), float(src[1]), float(src[2]) if known_z else required_z],
            "orientation_wxyz": list(source_pose["orientation_wxyz"]),
        }
        result = evaluate_support_footprint(
            surface,
            source_geometry_parts=source_geometry["geometry_parts"],
            source_pose=candidate_pose,
            target_pose=target_pose,
        )
        if result["status"] == "passed":
            feasible.append((surface, candidate_pose, sha))
    if len(feasible) > 1:
        raise ValueError("ambiguous_support_surface")
    if not feasible:
        raise ValueError("no_feasible_support_surface: known Z or complete footprint conflict")
    surface, pose, sha = feasible[0]
    return (
        surface,
        pose,
        {
            "candidate_surface_sha256s": candidates,
            "feasible_surface_sha256s": [sha],
            "selected_surface_sha256": sha,
        },
    )


def evaluate_support_footprint(surface, *, source_geometry_parts, source_pose, target_pose):
    """Geometric per-frame report only. Pose/parts authenticity belongs to assess_scene."""
    # Reuse the existing frozen assertion loader; defer import for its future consumer.
    from .assessment import _assertions

    if not isinstance(surface, MeasuredSupportSurface) or not math.isfinite(surface.plane_z_m):
        raise ValueError("invalid_support_surface")
    if any(
        not p or any(len(ring) < 4 or ring[0] != ring[-1] for ring in p) for p in surface.polygons
    ):
        raise ValueError("invalid_support_surface")
    polygons = [Polygon(p[0], p[1:]) for p in surface.polygons]
    if not polygons or any(not p.is_valid or p.area <= 0 for p in polygons):
        raise ValueError("invalid_support_surface")
    domain = MultiPolygon(polygons)
    if not domain.is_valid:
        raise ValueError("invalid_support_surface")
    src, source_rotation = _pose(source_pose)
    target, target_rotation = _pose(target_pose)
    if not np.allclose(target_rotation[:, 2], [0, 0, 1], atol=1e-6):
        raise ValueError("unsupported_nonhorizontal_surface")
    triangles = []
    if not isinstance(source_geometry_parts, dict):
        raise ValueError("invalid_support_mesh: invalid parts")
    for kind in ("visual", "collision"):
        if (
            not isinstance(source_geometry_parts.get(kind), (list, tuple))
            or not source_geometry_parts[kind]
        ):
            raise ValueError("invalid_support_mesh: missing footprint")
        for part in source_geometry_parts[kind]:
            if not isinstance(part, dict):
                raise ValueError("invalid_support_mesh: invalid part")
            authored = _triangles(part.get("local_vertices_m"), part.get("faces"))
            with np.errstate(over="ignore", invalid="ignore"):
                triangles.extend((authored @ source_rotation.T + src - target) @ target_rotation)
    footprint = _domain(triangles)
    if (
        footprint.is_empty
        or not footprint.is_valid
        or not math.isfinite(footprint.area)
        or footprint.area <= 0
    ):
        raise ValueError("invalid_support_mesh: empty footprint")
    covered = domain.covers(footprint)
    margin = float(footprint.distance(domain.boundary)) if covered else None
    required = _assertions()["support"]["target_local_complete_footprint_margin_m_min"]
    passed = covered and margin >= required
    return {
        "status": "passed" if passed else "failed",
        "covered": covered,
        "margin_m": margin,
        "required_margin_m": required,
        "error_code": None
        if passed
        else "support_margin_below_minimum"
        if covered
        else "footprint_outside_support_domain",
        "physical_evaluated": False,
        "actual_loaded_evaluated": False,
        "basis": "geometry_consistency_only",
    }
