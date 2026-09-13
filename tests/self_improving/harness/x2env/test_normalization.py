"""Public mesh normalization: fixtures are not asset-generation evidence."""

import hashlib
import json
import struct
import xml.etree.ElementTree as ET

import numpy as np
import pytest
import trimesh
from PIL import Image

from self_improving.harness.x2env.normalization import normalize_mesh


def test_jointed_urdf_is_rejected_without_flattening(tmp_path):
    source = tmp_path / "cabinet.urdf"
    source.write_text(
        '<robot name="cabinet"><link name="body"/><link name="door"/>'
        '<joint name="hinge" type="revolute"/></robot>'
    )
    with pytest.raises(ValueError, match="unsupported"):
        normalize_mesh(
            source, tmp_path / "out", dimensions_m=(1, 1, 1), up_axis="Z", mass_kg=1, friction=0.4
        )
    assert not (tmp_path / "out").exists()


def test_all_scene_instances_and_world_transforms_are_baked(tmp_path):
    box = trimesh.creation.box()
    scene = trimesh.Scene()
    scene.add_geometry(
        box,
        node_name="first",
        geom_name="shared",
        transform=trimesh.transformations.translation_matrix([4, 0, 0]),
    )
    scene.graph.update(
        frame_to="second",
        matrix=trimesh.transformations.translation_matrix([8, 0, 0]),
        geometry="shared",
    )
    source = tmp_path / "instances.glb"
    source.write_bytes(scene.export(file_type="glb"))
    before = source.read_bytes()
    result = normalize_mesh(
        source, tmp_path / "out", dimensions_m=(10, 2, 2), up_axis="Z", mass_kg=2, friction=0.4
    )
    assert result["status"] == "passed"
    assert result["source_geometry_instances"] == 2
    assert source.read_bytes() == before
    visual = trimesh.load(tmp_path / "out/visual.glb", force="scene", process=False)
    parts = visual.dump()
    assert len(parts) == 2
    actual = sorted([piece.bounds.tolist() for piece in parts], key=lambda b: b[0][0])
    np.testing.assert_allclose(actual, [[[-5, -1, 0], [-3, 1, 2]], [[3, -1, 0], [5, 1, 2]]])
    assert result["checks"]["genesis_load_step"] == "not_run"
    assert result["collision"]["basis"] == "convex_hull"


def test_y_to_z_rotation_and_portable_authored_inertia(tmp_path):
    mesh = trimesh.Trimesh(
        vertices=[[0, 0, 0], [1, 0, 0], [0, 2, 0], [0, 0, 3]],
        faces=[[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]],
        process=False,
    )
    source = tmp_path / "tetra.glb"
    source.write_bytes(mesh.export(file_type="glb"))
    result = normalize_mesh(
        source, tmp_path / "out", dimensions_m=(1, 3, 2), up_axis="Y", mass_kg=2, friction=0.7
    )
    loaded = trimesh.load(tmp_path / "out/visual.glb", force="mesh", process=False)
    expected = {(-0.5, 1.5, 0), (0.5, 1.5, 0), (-0.5, 1.5, 2), (-0.5, -1.5, 0)}
    assert set(map(tuple, np.round(loaded.vertices, 6))) == expected
    document = ET.parse(tmp_path / "out/asset.urdf")
    assert len(document.findall("link")) == 1
    assert not document.findall("joint")
    assert {n.get("filename") for n in document.findall(".//mesh")} == {
        "visual.glb",
        "collision.obj",
    }
    physics = json.loads((tmp_path / "out/physics.json").read_text())
    assert physics["friction"] == 0.7
    assert physics["mass_kg"] == 2
    assert physics["parameter_basis"] == "supplied_not_measured"
    assert physics["inertia_basis"] == "convex_hull_uniform_density"
    assert min(np.linalg.eigvalsh(physics["inertia_kg_m2"])) > 0
    assert result["entrypoint"] == "asset.urdf"


def test_embedded_texture_and_multiple_materials_survive(tmp_path):
    scene = trimesh.Scene()
    for index, color in enumerate([(255, 0, 0), (0, 255, 0)]):
        mesh = trimesh.creation.box()
        texture = Image.new("RGB", (2, 2), color)
        mesh.visual = trimesh.visual.TextureVisuals(
            uv=np.zeros((len(mesh.vertices), 2)),
            material=trimesh.visual.material.PBRMaterial(baseColorTexture=texture),
        )
        scene.add_geometry(
            mesh, transform=trimesh.transformations.translation_matrix([index * 2, 0, 0])
        )
    source = tmp_path / "materials.glb"
    source.write_bytes(scene.export(file_type="glb"))
    result = normalize_mesh(
        source, tmp_path / "out", dimensions_m=(3, 1, 1), up_axis="Z", mass_kg=1, friction=0.4
    )
    loaded = trimesh.load(tmp_path / "out/visual.glb", force="scene", process=False)
    assert sorted(
        tuple(m.visual.material.baseColorTexture.getpixel((0, 0))) for m in loaded.geometry.values()
    ) == [(0, 255, 0), (255, 0, 0)]
    assert result["material_policy"] == "preserved_supported_glb_core"
    for member in result["files"]:
        content = (tmp_path / "out" / member["path"]).read_bytes()
        assert member["sha256"] == hashlib.sha256(content).hexdigest()
        assert member["size_bytes"] == len(content)


@pytest.mark.parametrize("dimensions", [(None, 1, 1), (0, 1, 1), (float("nan"), 1, 1)])
def test_incomplete_or_invalid_dimensions_rejected(tmp_path, dimensions):
    with pytest.raises(ValueError, match="invalid dimensions"):
        normalize_mesh(
            tmp_path / "missing.glb",
            tmp_path / "out",
            dimensions_m=dimensions,
            up_axis="Y",
            mass_kg=1,
            friction=0.4,
        )
    assert not (tmp_path / "out").exists()


def test_existing_output_and_source_symlinks_refused(tmp_path):
    source = tmp_path / "mesh.glb"
    source.write_bytes(trimesh.creation.box().export(file_type="glb"))
    symlink = tmp_path / "link.glb"
    symlink.symlink_to(source)
    with pytest.raises(ValueError, match="non-symlink"):
        normalize_mesh(
            symlink, tmp_path / "out", dimensions_m=(1, 1, 1), up_axis="Z", mass_kg=1, friction=0.4
        )
    output = tmp_path / "out"
    output.mkdir()
    sentinel = output / "old"
    sentinel.write_text("preserve")
    with pytest.raises(FileExistsError):
        normalize_mesh(source, output, dimensions_m=(1, 1, 1), up_axis="Z", mass_kg=1, friction=0.4)
    assert sentinel.read_text() == "preserve"
    assert list(output.iterdir()) == [sentinel]


@pytest.mark.parametrize(
    "change",
    [
        {"skins": [{}]},
        {"animations": [{}]},
        {"extensionsUsed": ["KHR_texture_transform"]},
        {"images": [{"uri": "../outside.png"}]},
        {"buffers": [{"uri": "/tmp/outside.bin"}]},
        {"meshes": [{"primitives": [{"targets": [{"POSITION": 0}]}]}]},
        {"samplers": [{"wrapS": 33071}]},
        {"materials": [{"occlusionTexture": {"index": 0}}]},
        {"materials": [{"normalTexture": {"index": 0, "scale": 0.5}}]},
        {"materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"texCoord": 1}}}]},
    ],
)
def test_unsupported_glb_features_fail_before_output(tmp_path, change):
    raw = trimesh.creation.box().export(file_type="glb")
    old_size = struct.unpack("<I", raw[12:16])[0]
    document = json.loads(raw[20 : 20 + old_size])
    document.update(change)
    updated = json.dumps(document).encode()
    updated += b" " * (-len(updated) % 4)
    body = struct.pack("<II", len(updated), 0x4E4F534A) + updated + raw[20 + old_size :]
    source = tmp_path / "unsupported.glb"
    source.write_bytes(b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body)
    with pytest.raises(ValueError, match="unsupported"):
        normalize_mesh(
            source, tmp_path / "out", dimensions_m=(1, 1, 1), up_axis="Z", mass_kg=1, friction=0.4
        )
    assert not (tmp_path / "out").exists()


def test_color_override_is_explicit_and_collision_bounds_measured(tmp_path):
    source = tmp_path / "mesh.glb"
    source.write_bytes(trimesh.creation.icosphere(subdivisions=1).export(file_type="glb"))
    result = normalize_mesh(
        source,
        tmp_path / "out",
        dimensions_m=(0.1, 0.2, 0.3),
        up_axis="Z",
        mass_kg=0.2,
        friction=0.8,
        color_rgba=(1, 0, 0, 1),
    )
    assert result["material_policy"] == "explicit_color_override"
    loaded = trimesh.load(tmp_path / "out/visual.glb", force="mesh", process=False)
    raw = (tmp_path / "out/visual.glb").read_bytes()
    size = struct.unpack("<I", raw[12:16])[0]
    document = json.loads(raw[20 : 20 + size])
    for mesh in document["meshes"]:
        for primitive in mesh["primitives"]:
            assert "COLOR_0" not in primitive["attributes"]
            pbr = document["materials"][primitive["material"]]["pbrMetallicRoughness"]
            assert pbr["baseColorFactor"] == [1.0, 0.0, 0.0, 1.0]
            assert "baseColorTexture" not in pbr
    np.testing.assert_array_equal(loaded.visual.material.baseColorFactor, [255, 0, 0, 255])
    hull = trimesh.load(tmp_path / "out/collision.obj", process=False)
    np.testing.assert_allclose(hull.bounds, result["collision"]["bounds_m"], atol=1e-8)
    assert hull.volume == pytest.approx(result["collision"]["volume_m3"])
    assert result["collision"]["fills_concavities"] is True
    assert result["checks"]["container_inside_profile"] == "not_run"


@pytest.mark.parametrize("original_appearance", ["texture", "vertex_color"])
def test_uniform_override_replaces_appearance_without_changing_geometry_or_old_files(
    tmp_path, original_appearance
):
    mesh = trimesh.creation.box()
    if original_appearance == "texture":
        mesh.visual = trimesh.visual.TextureVisuals(
            uv=np.zeros((len(mesh.vertices), 2)),
            material=trimesh.visual.material.PBRMaterial(
                baseColorTexture=Image.new("RGB", (2, 2), (0, 255, 0))
            ),
        )
    else:
        mesh.visual.vertex_colors = [0, 255, 0, 255]
    source = tmp_path / "source.glb"
    source.write_bytes(mesh.export(file_type="glb"))
    source_bytes = source.read_bytes()
    kwargs = dict(dimensions_m=(0.1, 0.2, 0.3), up_axis="Y", mass_kg=0.2, friction=0.5)
    normalize_mesh(source, tmp_path / "original", **kwargs)
    old_files = {p.name: p.read_bytes() for p in (tmp_path / "original").iterdir()}
    normalize_mesh(source, tmp_path / "recolored", color_rgba=(0.0, 0.0, 1.0, 1.0), **kwargs)
    preserved = trimesh.load(tmp_path / "original/visual.glb", force="mesh", process=False)
    recolored = trimesh.load(tmp_path / "recolored/visual.glb", force="mesh", process=False)
    if original_appearance == "texture":
        assert preserved.visual.material.baseColorTexture.getpixel((0, 0)) == (0, 255, 0)
    else:
        assert np.all(preserved.visual.vertex_colors == [0, 255, 0, 255])
    np.testing.assert_array_equal(recolored.visual.material.baseColorFactor, [0, 0, 255, 255])
    assert recolored.visual.material.baseColorTexture is None
    np.testing.assert_array_equal(recolored.vertices, preserved.vertices)
    np.testing.assert_array_equal(recolored.faces, preserved.faces)
    for name in ["collision.obj", "physics.json", "asset.urdf"]:
        assert (tmp_path / "recolored" / name).read_bytes() == old_files[name]
    assert source.read_bytes() == source_bytes
    assert {p.name: p.read_bytes() for p in (tmp_path / "original").iterdir()} == old_files


@pytest.mark.parametrize("raw", [b"invalid", b"glTF" + struct.pack("<IIII", 1, 20, 0, 0)])
def test_corrupt_glb_header_rejected(tmp_path, raw):
    source = tmp_path / "bad.glb"
    source.write_bytes(raw)
    with pytest.raises(ValueError, match="invalid GLB"):
        normalize_mesh(
            source, tmp_path / "out", dimensions_m=(1, 1, 1), up_axis="Z", mass_kg=1, friction=0.5
        )


def test_plain_obj_supported_but_external_material_refused(tmp_path):
    source = tmp_path / "plain.obj"
    source.write_text(trimesh.creation.icosphere(subdivisions=0).export(file_type="obj"))
    result = normalize_mesh(
        source, tmp_path / "out", dimensions_m=(1, 1, 1), up_axis="Z", mass_kg=1, friction=0.5
    )
    assert result["status"] == "passed"
    source.write_text("mtllib ../../outside.mtl\n" + source.read_text())
    with pytest.raises(ValueError, match="material closure"):
        normalize_mesh(
            source,
            tmp_path / "rejected",
            dimensions_m=(1, 1, 1),
            up_axis="Z",
            mass_kg=1,
            friction=0.5,
        )


@pytest.mark.parametrize("color", [(1, 0, 0), (1, 0, 0, 2), (1, 0, 0, float("nan"))])
def test_invalid_color_rejected(tmp_path, color):
    with pytest.raises(ValueError, match="color override"):
        normalize_mesh(
            tmp_path / "missing.glb",
            tmp_path / "out",
            dimensions_m=(1, 1, 1),
            up_axis="Z",
            mass_kg=1,
            friction=0.5,
            color_rgba=color,
        )


def test_degenerate_mesh_rejected(tmp_path):
    mesh = trimesh.Trimesh(vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces=[[0, 1, 2]])
    source = tmp_path / "flat.glb"
    source.write_bytes(mesh.export(file_type="glb"))
    with pytest.raises(ValueError, match="degenerate mesh"):
        normalize_mesh(
            source, tmp_path / "out", dimensions_m=(1, 1, 1), up_axis="Z", mass_kg=1, friction=0.5
        )
    assert not (tmp_path / "out").exists()
