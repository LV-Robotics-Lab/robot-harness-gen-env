"""Approved asset patches create immutable children; no runtime success is implied."""

import hashlib
import json
import struct
import xml.etree.ElementTree as ET

import pytest
import trimesh

from self_improving.harness.x2env.assets import AssetLicense, AssetRegistry, AssetSource
from self_improving.harness.x2env.normalization import normalize_mesh
from self_improving.harness.x2env.store import Store


def persisted_attack(store, parent, mutate):
    """Explicit hostile persisted record via public opaque Store, not a Registry mock/DB edit."""
    contents = {m.path: store.read_artifact(m.artifact) for m in parent.files}
    mutate(contents)
    value = parent.model_dump(mode="json")
    value["files"] = [
        {
            "path": path,
            "artifact": store.write_artifact(raw, "application/octet-stream").model_dump(
                mode="json"
            ),
        }
        for path, raw in contents.items()
    ]
    del value["version_sha256"]
    sha = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    value["version_sha256"] = sha
    store.register_asset(sha, parent.asset_id, parent.category, json.dumps(value))
    return sha


def approval_for(store, parent, patch):
    return store.write_artifact(
        json.dumps(
            {
                "authority": "harness_controller",
                "approved": True,
                "parent_version": parent,
                "patch": patch.model_dump(mode="json", exclude_none=True),
            }
        ).encode(),
        "application/json",
    )


@pytest.mark.parametrize(
    "fault",
    [
        "unsafe_path",
        "multi_body",
        "missing_inertia",
        "mass",
        "nonfinite",
        "matrix_shape",
        "matrix_mismatch",
        "visual_format",
        "glb_short",
        "glb_header",
        "color_texture",
    ],
)
def test_persisted_asset_corruption_cannot_be_revised_into_valid_child(tmp_path, fault):
    from copy import deepcopy

    from self_improving.harness.x2env.asset_revision import AssetPatch, AssetRevision

    store, registry, parent = fixture(tmp_path)
    patch = (
        AssetPatch(base_color=(1.0, 0.1, 0.5, 1.0))
        if fault in {"visual_format", "glb_short", "glb_header", "color_texture"}
        else AssetPatch(mass=0.2)
    )

    def mutate(contents):
        if fault == "unsafe_path":
            contents["../bad"] = b"unsafe"
            return
        if fault in {"multi_body", "missing_inertia", "mass", "nonfinite", "visual_format"}:
            tree = ET.fromstring(contents["asset.urdf"])
            if fault == "multi_body":
                tree.find("link").append(deepcopy(tree.find(".//inertial")))
            if fault == "missing_inertia":
                tree.find(".//inertial").remove(tree.find(".//inertia"))
            if fault == "mass":
                tree.find(".//mass").set("value", "2")
            if fault == "nonfinite":
                tree.find(".//inertia").set("ixx", "nan")
            if fault == "visual_format":
                tree.find(".//visual/geometry/mesh").set("filename", "collision.obj")
            contents["asset.urdf"] = ET.tostring(tree)
        elif fault.startswith("matrix"):
            physics = json.loads(contents["physics.json"])
            physics["inertia_kg_m2"] = (
                [] if fault == "matrix_shape" else [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
            )
            contents["physics.json"] = json.dumps(physics).encode()
        elif fault == "glb_short":
            contents["visual.glb"] = b"bad"
        elif fault == "glb_header":
            raw = bytearray(contents["visual.glb"])
            struct.pack_into("<I", raw, 4, 1)
            contents["visual.glb"] = bytes(raw)
        elif fault == "color_texture":
            raw = contents["visual.glb"]
            size = struct.unpack_from("<I", raw, 12)[0]
            doc = json.loads(raw[20 : 20 + size])
            doc["materials"] = [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}]
            encoded = json.dumps(doc).encode()
            encoded += b" " * ((-len(encoded)) % 4)
            tail = raw[20 + size :]
            contents["visual.glb"] = (
                struct.pack(
                    "<4sIIII", b"glTF", 2, 20 + len(encoded) + len(tail), len(encoded), 0x4E4F534A
                )
                + encoded
                + tail
            )

    sha = persisted_attack(store, parent, mutate)
    with pytest.raises(ValueError):
        AssetRevision(registry, store).revise(
            sha, patch, approval=approval_for(store, sha, patch), output_root=tmp_path / "child"
        )
    assert registry.inspect(parent.version_sha256) == parent


def test_empty_patch_is_not_a_revision():
    from self_improving.harness.x2env.asset_revision import AssetPatch

    with pytest.raises(ValueError, match="at least one"):
        AssetPatch()


def test_existing_pbr_material_can_be_revised_without_changing_geometry(tmp_path):
    from self_improving.harness.x2env.asset_revision import AssetPatch, AssetRevision

    store, registry, parent = fixture(tmp_path)
    current = parent
    for index, color in enumerate(((1.0, 0.1, 0.5, 1.0), (0.0, 0.0, 1.0, 0.5))):
        patch = AssetPatch(base_color=color)
        revised = AssetRevision(registry, store).revise(
            current.version_sha256,
            patch,
            approval=approval_for(store, current.version_sha256, patch),
            output_root=tmp_path / f"child-{index}",
        )
        assert revised.parent_version == current.version_sha256
        assert revised.geometry_sha256 == parent.geometry_sha256
        current = revised
    raw = store.read_artifact(next(m.artifact for m in current.files if m.path == "visual.glb"))
    size = struct.unpack_from("<I", raw, 12)[0]
    doc = json.loads(raw[20 : 20 + size])
    assert doc["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"] == [0.0, 0.0, 1.0, 0.5]
    assert doc["materials"][0]["alphaMode"] == "BLEND"
    assert registry.inspect(parent.version_sha256) == parent


def fixture(tmp_path, vertex_color=False, textured=False):
    store = Store(tmp_path / "state")
    registry = AssetRegistry(store)
    source = tmp_path / "source.glb"
    mesh = trimesh.creation.box()
    if vertex_color:
        mesh.visual.vertex_colors = [0, 255, 0, 255]
    if textured:
        import numpy as np
        from PIL import Image

        mesh.visual = trimesh.visual.TextureVisuals(
            uv=np.zeros((len(mesh.vertices), 2)),
            material=trimesh.visual.material.PBRMaterial(
                baseColorTexture=Image.new("RGBA", (2, 2), (0, 255, 0, 255))
            ),
        )
    source.write_bytes(mesh.export(file_type="glb"))
    root = tmp_path / "normalized"
    report = normalize_mesh(
        source,
        root,
        dimensions_m=(0.1, 0.1, 0.1),
        up_axis="Z",
        mass_kg=0.1,
        friction=0.6,
    )
    proof = store.write_artifact(b"fixture", "text/plain")
    version = registry.register(
        "box",
        "box",
        root,
        report["entrypoint"],
        files=tuple(f["path"] for f in report["files"]),
        normalization_report=store.write_artifact(json.dumps(report).encode(), "application/json"),
        license=AssetLicense(
            spdx="CC0-1.0", source_url="https://example.org/fixture", evidence=proof
        ),
        source=AssetSource(kind="web", provider="fixture", source_ref="fixed", evidence=proof),
        receipt=proof,
    )
    return store, registry, version


@pytest.mark.parametrize("layer", ["texture", "vertex"])
def test_uniform_color_replacement_removes_layers_but_preserves_geometry(tmp_path, layer):
    from io import BytesIO

    import numpy as np

    from self_improving.harness.x2env.asset_revision import AssetPatch, AssetRevision

    store, registry, parent = fixture(
        tmp_path, textured=layer == "texture", vertex_color=layer == "vertex"
    )
    original = {m.path: store.read_artifact(m.artifact) for m in parent.files}
    with pytest.raises(ValueError, match="unsupported_asset_patch"):
        default = AssetPatch(base_color=(1.0, 0.0, 0.5, 1.0))
        AssetRevision(registry, store).revise(
            parent.version_sha256,
            default,
            approval=approval_for(store, parent.version_sha256, default),
            output_root=tmp_path / "default",
        )
    patch = AssetPatch(base_color=(1.0, 0.0, 0.5, 1.0), color_mode="uniform_replace")
    child = AssetRevision(registry, store).revise(
        parent.version_sha256,
        patch,
        approval=approval_for(store, parent.version_sha256, patch),
        output_root=tmp_path / "child",
    )
    raw = store.read_artifact(next(m.artifact for m in child.files if m.path == "visual.glb"))
    loaded = trimesh.load(BytesIO(raw), file_type="glb", force="scene", process=False)
    for mesh in loaded.geometry.values():
        colors = np.asarray(mesh.visual.to_color().vertex_colors).reshape(-1, 4)
        np.testing.assert_allclose(colors, np.tile([255, 0, 128, 255], (len(colors), 1)), atol=1)
    assert child.geometry_sha256 == parent.geometry_sha256
    assert registry.inspect(parent.version_sha256) == parent
    current = {m.path: store.read_artifact(m.artifact) for m in child.files}
    for name in ("physics.json", "collision.obj", "asset.urdf"):
        assert current[name] == original[name]

    def bin_chunk(value):
        size = struct.unpack_from("<I", value, 12)[0]
        return value[20 + size :]

    assert bin_chunk(current["visual.glb"]) == bin_chunk(original["visual.glb"])
    receipt = json.loads(store.read_artifact(child.receipt))
    row = receipt["color_replacement"][0]
    assert row["baseColorTexture"] == (1 if layer == "texture" else 0)
    assert row["COLOR_0"] == (1 if layer == "vertex" else 0)


def test_color_mode_requires_explicit_color(tmp_path):
    from self_improving.harness.x2env.asset_revision import AssetPatch

    with pytest.raises(ValueError, match="requires base_color"):
        AssetPatch(color_mode="uniform_replace")


@pytest.mark.parametrize("fault", ["texture", "image", "image_bytes", "uv", "vertex", "extension"])
def test_uniform_replacement_cannot_launder_invalid_parent_color_layers(tmp_path, fault):
    from self_improving.harness.x2env.asset_revision import AssetPatch, AssetRevision

    store, registry, parent = fixture(
        tmp_path, textured=fault != "vertex", vertex_color=fault == "vertex"
    )

    def mutate(contents):
        raw = contents["visual.glb"]
        size = struct.unpack_from("<I", raw, 12)[0]
        doc = json.loads(raw[20 : 20 + size])
        tail = bytearray(raw[20 + size :])
        primitive = doc["meshes"][0]["primitives"][0]
        if fault == "texture":
            doc["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"]["index"] = 999
        elif fault == "image":
            doc["textures"][0]["source"] = 999
        elif fault == "image_bytes":
            view = doc["bufferViews"][doc["images"][0]["bufferView"]]
            start = 8 + view.get("byteOffset", 0)
            tail[start : start + view["byteLength"]] = b"x" * view["byteLength"]
        elif fault == "uv":
            primitive["attributes"]["TEXCOORD_0"] = 999
        elif fault == "vertex":
            primitive["attributes"]["COLOR_0"] = 999
        else:
            doc["materials"][0]["extensions"] = {"KHR_materials_unlit": {}}
        encoded = json.dumps(doc).encode()
        encoded += b" " * ((-len(encoded)) % 4)
        contents["visual.glb"] = (
            struct.pack(
                "<4sIIII", b"glTF", 2, 20 + len(encoded) + len(tail), len(encoded), 0x4E4F534A
            )
            + encoded
            + tail
        )

    parent_ref = persisted_attack(store, parent, mutate)
    patch = AssetPatch(base_color=(1.0, 0.0, 0.5, 1.0), color_mode="uniform_replace")
    with pytest.raises(ValueError):
        AssetRevision(registry, store).revise(
            parent_ref,
            patch,
            approval=approval_for(store, parent_ref, patch),
            output_root=tmp_path / "child",
        )
    assert not (tmp_path / "child").exists()


def test_mass_and_friction_revision_preserves_parent_and_updates_inertia(tmp_path):
    from self_improving.harness.x2env.asset_revision import AssetPatch, AssetRevision

    store, registry, parent = fixture(tmp_path)
    old = {m.path: store.read_artifact(m.artifact) for m in parent.files}
    patch = AssetPatch(mass=0.2, friction=0.4)
    approval = store.write_artifact(
        json.dumps(
            {
                "authority": "harness_controller",
                "approved": True,
                "parent_version": parent.version_sha256,
                "patch": patch.model_dump(exclude_none=True),
            }
        ).encode(),
        "application/json",
    )
    child = AssetRevision(registry, store).revise(
        parent.version_sha256, patch, approval=approval, output_root=tmp_path / "child"
    )
    assert child.parent_version == parent.version_sha256
    assert child.version_sha256 != parent.version_sha256
    assert child.geometry_sha256 == parent.geometry_sha256
    assert child.source == parent.source and child.license == parent.license
    current = {m.path: store.read_artifact(m.artifact) for m in child.files}
    physics = json.loads(current["physics.json"])
    assert physics["mass_kg"] == 0.2 and physics["friction"] == 0.4
    before = ET.fromstring(old[parent.entrypoint]).find(".//inertia")
    after = ET.fromstring(current[child.entrypoint]).find(".//inertia")
    assert float(after.attrib["ixx"]) == 2 * float(before.attrib["ixx"])
    assert {
        m.path: store.read_artifact(m.artifact)
        for m in registry.inspect(parent.version_sha256).files
    } == old


def test_base_color_changes_json_without_touching_geometry_bin(tmp_path):
    from self_improving.harness.x2env.asset_revision import AssetPatch, AssetRevision

    store, registry, parent = fixture(tmp_path)
    patch = AssetPatch(base_color=(1.0, 0.2, 0.5, 1.0))
    approval = store.write_artifact(
        json.dumps(
            {
                "authority": "harness_controller",
                "approved": True,
                "parent_version": parent.version_sha256,
                "patch": patch.model_dump(mode="json", exclude_none=True),
            }
        ).encode(),
        "application/json",
    )
    child = AssetRevision(registry, store).revise(
        parent.version_sha256, patch, approval=approval, output_root=tmp_path / "pink"
    )

    def visual(version):
        return store.read_artifact(
            next(m.artifact for m in version.files if m.path == "visual.glb")
        )

    old, new = visual(parent), visual(child)
    old_size = struct.unpack_from("<I", old, 12)[0]
    new_size = struct.unpack_from("<I", new, 12)[0]
    assert old[20 + old_size :] == new[20 + new_size :]
    document = json.loads(new[20 : 20 + new_size])
    assert document["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"] == [
        1.0,
        0.2,
        0.5,
        1.0,
    ]
    assert child.geometry_sha256 == parent.geometry_sha256


@pytest.mark.parametrize("fault", ["unknown", "unsupported", "approval", "escape"])
def test_unsupported_or_unapproved_changes_cannot_create_a_child(tmp_path, fault):
    from self_improving.harness.x2env.asset_revision import AssetPatch, AssetRevision

    store, registry, parent = fixture(tmp_path)
    with pytest.raises(ValueError):
        patch = AssetPatch(
            **(
                {"mesh_path": "../bad"}
                if fault == "unknown"
                else {"density": 10.0}
                if fault == "unsupported"
                else {"friction": 0.4}
            )
        )
        approval = store.write_artifact(
            json.dumps(
                {
                    "authority": "harness_controller",
                    "approved": fault != "approval",
                    "parent_version": parent.version_sha256,
                    "patch": patch.model_dump(mode="json", exclude_none=True),
                }
            ).encode(),
            "application/json",
        )
        output = tmp_path / "child"
        if fault == "escape":
            output.symlink_to(tmp_path / "outside")
        AssetRevision(registry, store).revise(
            parent.version_sha256, patch, approval=approval, output_root=output
        )
    assert registry.find("box") == (parent,)


def test_vertex_color_modulation_is_not_falsely_claimed_recolored(tmp_path):
    from self_improving.harness.x2env.asset_revision import AssetPatch, AssetRevision

    store, registry, parent = fixture(tmp_path, vertex_color=True)
    patch = AssetPatch(base_color=(1.0, 0.2, 0.5, 1.0))
    approval = store.write_artifact(
        json.dumps(
            {
                "authority": "harness_controller",
                "approved": True,
                "parent_version": parent.version_sha256,
                "patch": patch.model_dump(mode="json", exclude_none=True),
            }
        ).encode(),
        "application/json",
    )
    with pytest.raises(ValueError, match="unsupported_asset_patch_vertex_color"):
        AssetRevision(registry, store).revise(
            parent.version_sha256, patch, approval=approval, output_root=tmp_path / "unsupported"
        )
    assert not (tmp_path / "unsupported").exists()


@pytest.mark.parametrize(
    "fields", [{"mass": 0.1}, {"friction": 0.6}, {"mass": 0.1, "friction": 0.6}]
)
def test_equal_physics_patch_cannot_manufacture_a_repair_version(tmp_path, fields):
    from self_improving.harness.x2env.asset_revision import AssetPatch, AssetRevision

    store, registry, parent = fixture(tmp_path)
    patch = AssetPatch(**fields)
    approval = store.write_artifact(
        json.dumps(
            {
                "authority": "harness_controller",
                "approved": True,
                "parent_version": parent.version_sha256,
                "patch": patch.model_dump(mode="json", exclude_none=True),
            }
        ).encode(),
        "application/json",
    )
    with pytest.raises(ValueError, match="no_effect_asset_patch"):
        AssetRevision(registry, store).revise(
            parent.version_sha256, patch, approval=approval, output_root=tmp_path / "no-effect"
        )
    assert not (tmp_path / "no-effect").exists()
    assert registry.find("box") == (parent,)


def test_repeating_existing_base_color_is_no_effect(tmp_path):
    from self_improving.harness.x2env.asset_revision import AssetPatch, AssetRevision

    store, registry, parent = fixture(tmp_path)
    patch = AssetPatch(base_color=(1.0, 0.2, 0.5, 1.0))

    def approved(version):
        return store.write_artifact(
            json.dumps(
                {
                    "authority": "harness_controller",
                    "approved": True,
                    "parent_version": version.version_sha256,
                    "patch": patch.model_dump(mode="json", exclude_none=True),
                }
            ).encode(),
            "application/json",
        )

    revision = AssetRevision(registry, store)
    child = revision.revise(
        parent.version_sha256, patch, approval=approved(parent), output_root=tmp_path / "pink"
    )
    with pytest.raises(ValueError, match="no_effect_asset_patch"):
        revision.revise(
            child.version_sha256,
            patch,
            approval=approved(child),
            output_root=tmp_path / "same-pink",
        )
    assert not (tmp_path / "same-pink").exists()
    assert len(registry.find("box")) == 2


def test_one_real_change_among_equal_fields_still_creates_a_child(tmp_path):
    from self_improving.harness.x2env.asset_revision import AssetPatch, AssetRevision

    store, registry, parent = fixture(tmp_path)
    patch = AssetPatch(mass=0.1, friction=0.4)
    approval = store.write_artifact(
        json.dumps(
            {
                "authority": "harness_controller",
                "approved": True,
                "parent_version": parent.version_sha256,
                "patch": patch.model_dump(mode="json", exclude_none=True),
            }
        ).encode(),
        "application/json",
    )
    child = AssetRevision(registry, store).revise(
        parent.version_sha256, patch, approval=approval, output_root=tmp_path / "friction-only"
    )
    old_urdf = next(m.artifact for m in parent.files if m.path == parent.entrypoint)
    new_urdf = next(m.artifact for m in child.files if m.path == child.entrypoint)
    assert old_urdf == new_urdf
    assert child.version_sha256 != parent.version_sha256
