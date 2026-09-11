"""Gujie deterministic pose/geometry semantics adapted to portable Harness CAS.

Origin: Gujie gen-env eb0b710; exact files and integration limits are recorded in
GUJIE_SCENE_ADAPTER_CONTRACT.md. This produces candidates, never physics authority.
"""

import itertools
import json
import math
import posixpath
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from .schemas.common import ArtifactRef
from .schemas.genesis_scene_replay import GenesisSceneSpecV1
from .schemas.x2env_compile_evidence import AssetRepresentationV1


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _publish(store, value, name, schema=None):
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / name
        path.write_bytes(_canonical(value))
        return store.put_file(path, name=name, media_type="application/json", schema_version=schema)


def _read(store, ref, *, legacy=False):
    payload = store.resolve(ref).path.read_bytes()
    value = json.loads(payload)
    if (
        ref.media_type != "application/json"
        or ref.uri != f"artifact://sha256/{ref.sha256}"
        or payload != _canonical(value) + (b"\n" if legacy else b"")
    ):
        raise ValueError("scene inputs must be canonical CAS JSON")
    return value


def _vector(value, length):
    if (
        not isinstance(value, (list, tuple))
        or len(value) != length
        or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in value
        )
    ):
        raise ValueError("invalid finite numeric vector")
    return tuple(float(v) for v in value)


def _quaternion(value):
    x, y, z, w = _vector(value, 4)
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if abs(norm - 1) > 1e-6:
        raise ValueError("source quaternion must be normalized")
    return (w / norm, x / norm, y / norm, z / norm)


def _source_pose(source, representation_ref):
    if (
        set(source) != {"representation", "metadata", "relation", "scene"}
        or ArtifactRef.model_validate(source["representation"]) != representation_ref
    ):
        raise ValueError("source representation binding differs")
    meta = source["metadata"]
    if set(meta) != {"name", "category", "model"} or any(
        not isinstance(v, str) or not v for v in meta.values()
    ):
        raise ValueError("invalid source identity metadata")
    name = meta["name"]
    if source["relation"] != {"relation": "on_table", "source": name, "target": "table"}:
        raise ValueError("explicit finite-table relation required")
    scene = source["scene"]
    initial = scene["objects_info"]["init_info"]
    states = scene["state"]["registry"]["object_registry"]
    if set(initial) != {name} or set(states) != {name}:
        raise ValueError("single source declaration and state required")
    args = initial[name]["args"]
    if any(args.get(k) != meta[k] for k in meta):
        raise ValueError("source metadata and scene identity differ")
    if args.get("usd_path") or any(
        k in args for k in ("mass", "link_physics_materials", "joint_limits")
    ):
        raise ValueError("source asset or physics override unsupported")
    if (
        _vector(args.get("scale", [1, 1, 1]), 3) != (1.0, 1.0, 1.0)
        or args.get("fixed_base", False) is not False
        or args.get("visual_only", False) is not False
    ):
        raise ValueError("requires unchanged dynamic rigid source")
    state = states[name]
    if state.get("joint_pos", []) or state.get("joint_vel", []):
        raise ValueError("articulated source state unsupported")
    root = state["root_link"]
    if any(
        _vector(root.get(key, [0, 0, 0]), 3) != (0.0, 0.0, 0.0) for key in ("lin_vel", "ang_vel")
    ):
        raise ValueError("nonzero source velocity is unsupported")
    init = scene.get("init_info", {}).get("args", {})
    ground = scene.get("ground_plane_info", {})
    if (
        init.get("use_floor_plane", True) is not True
        or ground.get("visible", init.get("floor_plane_visible", True)) is not True
        or _vector(ground.get("position", [0, 0, 0]), 3) != (0.0, 0.0, 0.0)
        or _quaternion(ground.get("orientation", [0, 0, 0, 1])) != (1.0, 0.0, 0.0, 0.0)
    ):
        raise ValueError("first profile requires the original horizontal ground at zero")
    return _vector(root["pos"], 3), _quaternion(root["ori"])


def _geometry(store, representation):
    if representation.representation_kind != "urdf" or representation.loader_scale != (
        1.0,
        1.0,
        1.0,
    ):
        raise ValueError("requires unchanged rigid URDF representation")
    members = {m.logical_path: m.artifact for m in representation.members}
    for ref in members.values():
        store.resolve(ref)
    text = store.resolve(members[representation.root_logical_path]).path.read_text()
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise ValueError("XML entities forbidden")
    tree = ET.fromstring(text)
    if len(tree.findall("link")) != 1 or tree.findall("joint"):
        raise ValueError("requires one rigid link without joints")
    link = tree.find("link")
    mass = link.find("inertial/mass")
    inertia = link.find("inertial/inertia")
    if mass is None or inertia is None:
        raise ValueError("missing authored inertial fields")
    m = float(mass.get("value"))
    xx, xy, xz, yy, yz, zz = _vector(
        [float(inertia.get(k)) for k in ("ixx", "ixy", "ixz", "iyy", "iyz", "izz")], 6
    )
    determinant = xx * yy * zz + 2 * xy * xz * yz - xx * yz * yz - yy * xz * xz - zz * xy * xy
    half_trace = (xx + yy + zz) / 2
    a, b, c = half_trace - xx, half_trace - yy, half_trace - zz
    triangle_determinant = a * b * c - 2 * xy * xz * yz - a * yz * yz - b * xz * xz - c * xy * xy
    if (
        not math.isfinite(m)
        or m <= 0
        or min(xx, xx * yy - xy * xy, determinant) <= 0
        or min(a, b, c, a * b - xy * xy, a * c - xz * xz, b * c - yz * yz, triangle_determinant)
        < -1e-10
    ):
        raise ValueError("invalid authored mass or inertia")
    vertices = []
    for kind in ("visual", "collision"):
        if not link.findall(kind):
            raise ValueError("missing full visual or collision geometry")
        for node in link.findall(kind):
            mesh = node.find("geometry/mesh")
            if mesh is None:
                box = node.find("geometry/box")
                if box is None:
                    raise ValueError("unsupported authored geometry")
                size = _vector([float(value) for value in box.get("size").split()], 3)
                if min(size) <= 0:
                    raise ValueError("box dimensions must be positive")
                points = list(itertools.product(*[(-value / 2, value / 2) for value in size]))
            else:
                filename = mesh.get("filename", "")
                logical = posixpath.normpath(
                    posixpath.join(posixpath.dirname(representation.root_logical_path), filename)
                )
                if (
                    not filename
                    or filename.startswith("/")
                    or "\\" in filename
                    or ":" in filename
                    or logical.startswith("../")
                    or logical not in members
                    or not logical.endswith(".obj")
                ):
                    raise ValueError("mesh must be a declared relative OBJ member")
                payload = store.resolve(members[logical]).path.read_text()
                scale = _vector([float(v) for v in mesh.get("scale", "1 1 1").split()], 3)
                if min(scale) <= 0:
                    raise ValueError("mesh scale must be positive")
                points = [
                    tuple(
                        v * scale[i]
                        for i, v in enumerate(_vector([float(t) for t in line.split()[1:]], 3))
                    )
                    for line in payload.splitlines()
                    if line.split()[:1] == ["v"]
                ]
                if not 4 <= len(points) <= 1_000_000 or any(
                    max(p[axis] for p in points) - min(p[axis] for p in points) <= 1e-10
                    for axis in range(3)
                ):
                    raise ValueError("mesh geometry must be bounded and nondegenerate")
            origin = node.find("origin")
            attributes = {} if origin is None else origin.attrib
            translation = _vector([float(v) for v in attributes.get("xyz", "0 0 0").split()], 3)
            roll, pitch, yaw = _vector(
                [float(v) for v in attributes.get("rpy", "0 0 0").split()], 3
            )
            cr, sr, cp, sp, cy, sy = (
                math.cos(roll),
                math.sin(roll),
                math.cos(pitch),
                math.sin(pitch),
                math.cos(yaw),
                math.sin(yaw),
            )
            rotation = (
                (cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr),
                (sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr),
                (-sp, cp * sr, cp * cr),
            )
            vertices.extend(
                tuple(
                    sum(row[i] * point[i] for i in range(3)) + translation[j]
                    for j, row in enumerate(rotation)
                )
                for point in points
            )
    return vertices


def _derive(store, source_ref, representation_ref, table_center_m, table_size_m, table_friction):
    source = _read(store, source_ref)
    representation = AssetRepresentationV1.model_validate(
        _read(store, representation_ref, legacy=True)
    )
    original_position, orientation = _source_pose(source, representation_ref)
    w, x, y, z = orientation
    rotation = (
        (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
        (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
        (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)),
    )
    vertices = _geometry(store, representation)
    world_offsets = [
        tuple(sum(row[i] * point[i] for i in range(3)) for row in rotation) for point in vertices
    ]
    position = (
        original_position[0],
        original_position[1],
        table_center_m[2] + table_size_m[2] / 2 + 0.001 - min(point[2] for point in world_offsets),
    )
    if any(
        abs(point[axis] + position[axis] - table_center_m[axis])
        > table_size_m[axis] / 2 - 0.01 + 1e-10
        for point in world_offsets
        for axis in (0, 1)
    ):
        raise ValueError("full footprint lies outside the finite table margin")
    geometry = {
        "source": source_ref.model_dump(mode="json"),
        "representation": representation_ref.model_dump(mode="json"),
        "source_position_m": original_position,
        "position_m": position,
        "orientation_wxyz": orientation,
        "local_vertices_m": vertices,
        "origin_commit": "eb0b710581fd7794bc01b447b0f77cb871c8a711",
        "physics_status": "not_run",
    }
    fields = dict(
        schema_version="harness.genesis_scene_spec.v1",
        source=source_ref,
        representation=representation_ref,
        object_id="object",
        position_m=position,
        orientation_wxyz=orientation,
        local_vertices_m=tuple(vertices),
        table_center_m=table_center_m,
        table_size_m=table_size_m,
        table_friction=table_friction,
        ground_z_m=0.0,
    )
    return geometry, fields


def adapt_gujie_tabletop_scene(
    store, *, source_ref, representation_ref, table_center_m, table_size_m, table_friction
):
    """Compile an explicit finite-table candidate from source pose and full asset geometry."""
    geometry, fields = _derive(
        store, source_ref, representation_ref, table_center_m, table_size_m, table_friction
    )
    GenesisSceneSpecV1(geometry=source_ref, **fields)
    geometry_ref = _publish(store, geometry, "gujie-scene-geometry.json")
    scene = GenesisSceneSpecV1(geometry=geometry_ref, **fields)
    return _publish(
        store, scene.model_dump(mode="json"), "genesis-scene.json", scene.schema_version
    )


def read_gujie_tabletop_scene(store, scene_ref):
    """Recompute a scene candidate from its complete source/asset CAS closure."""
    scene = GenesisSceneSpecV1.model_validate(_read(store, scene_ref))
    geometry, fields = _derive(
        store,
        scene.source,
        scene.representation,
        scene.table_center_m,
        scene.table_size_m,
        scene.table_friction,
    )
    if store.resolve(scene.geometry).path.read_bytes() != _canonical(geometry):
        raise ValueError("scene geometry differs from source representation")
    expected = GenesisSceneSpecV1(geometry=scene.geometry, **fields)
    if expected != scene:
        raise ValueError("scene placement differs from the deterministic candidate")
    return scene
