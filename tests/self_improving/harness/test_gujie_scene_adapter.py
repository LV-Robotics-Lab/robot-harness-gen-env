"""Portable Gujie scene adaptation preserves finite-table semantics."""

import importlib
import json
import math
import shutil

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.schemas.x2env_compile_evidence import (
    AssetModelSubjectV1,
    AssetRepresentationMemberV1,
    AssetRepresentationV1,
    asset_loader_closure_sha256,
    canonical_evidence_sha256,
)


@pytest.fixture
def candidate(tmp_path):
    store = LocalArtifactStore(tmp_path / "cas")

    def publish(value, name, schema=None, legacy=False):
        path = tmp_path / name
        path.write_bytes(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
            + (b"\n" if legacy else b"")
        )
        return store.put_file(path, name=name, media_type="application/json", schema_version=schema)

    urdf = tmp_path / "object.urdf"
    urdf.write_text(
        '<robot name="object"><link name="body"><inertial><mass value="1"/>'
        '<inertia ixx="0.01" ixy="0" ixz="0" iyy="0.01" iyz="0" izz="0.01"/>'
        '</inertial><visual><geometry><box size="0.10 0.06 0.04"/></geometry></visual>'
        '<collision><geometry><box size="0.10 0.06 0.04"/></geometry></collision></link></robot>'
    )
    member = AssetRepresentationMemberV1(
        logical_path="object.urdf",
        role="urdf",
        artifact=store.put_file(
            urdf, name="object.urdf", media_type="application/xml", schema_version=None
        ),
    )
    subject = AssetModelSubjectV1(
        namespace="gujie.test",
        asset_key="mouse",
        ledger_asset_id="mouse",
        model_id=0,
        backend="genesis",
        source_record_sha256="1" * 64,
    )
    payload = dict(
        schema_version="harness.asset_representation.v1",
        subject=subject.model_dump(mode="json"),
        representation_kind="urdf",
        root_logical_path="object.urdf",
        loader_scale=[1.0, 1.0, 1.0],
        members=[member.model_dump(mode="json")],
        collision_member_paths=["object.urdf"],
        visual_member_paths=["object.urdf"],
        loader_closure_sha256=asset_loader_closure_sha256(subject, "object.urdf", (member,)),
    )
    payload["representation_sha256"] = canonical_evidence_sha256(payload, "representation_sha256")
    representation = AssetRepresentationV1.model_validate(payload)
    ref = publish(
        representation.model_dump(mode="json"),
        "representation.json",
        representation.schema_version,
        True,
    )
    meta = {"name": "mouse_001", "category": "mouse", "model": "rigid"}
    source = {
        "representation": ref.model_dump(mode="json"),
        "metadata": meta,
        "relation": {"relation": "on_table", "source": "mouse_001", "target": "table"},
        "scene": {
            "objects_info": {"init_info": {"mouse_001": {"args": dict(meta)}}},
            "state": {
                "registry": {
                    "object_registry": {
                        "mouse_001": {
                            "root_link": {
                                "pos": [0.1, 0.05, 0.8],
                                "ori": [0.0, 0.0, math.sqrt(0.5), math.sqrt(0.5)],
                            }
                        }
                    }
                }
            },
        },
    }
    source_ref = publish(source, "source.json")
    return store, ref, source_ref, source, publish


def test_source_pose_is_adapted_to_finite_table_and_pure_read_is_portable(candidate, tmp_path):
    adapter = importlib.import_module("self_improving.harness.gujie_scene_adapter")
    store, representation, source, _, _ = candidate
    ref = adapter.adapt_gujie_tabletop_scene(
        store,
        source_ref=source,
        representation_ref=representation,
        table_center_m=(0.0, 0.0, 0.4),
        table_size_m=(1.0, 0.6, 0.1),
        table_friction=0.5,
    )
    scene = adapter.read_gujie_tabletop_scene(store, ref)
    assert scene.position_m == pytest.approx((0.1, 0.05, 0.471))
    assert scene.orientation_wxyz == pytest.approx((math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5)))
    assert scene.table_center_m == (0.0, 0.0, 0.4)
    assert scene.table_size_m == (1.0, 0.6, 0.1)
    assert scene.ground_z_m == 0.0
    assert len(scene.local_vertices_m) == 16
    assert scene.source == source and scene.representation == representation
    relocated = LocalArtifactStore(tmp_path / "relocated-cas")
    shutil.copytree(store.root, relocated.root, dirs_exist_ok=True)
    before = set(relocated.root.rglob("*"))
    assert adapter.read_gujie_tabletop_scene(relocated, ref) == scene
    assert set(relocated.root.rglob("*")) == before


def test_complete_rotated_footprint_outside_finite_table_is_rejected(candidate):
    adapter = importlib.import_module("self_improving.harness.gujie_scene_adapter")
    store, representation, _, source, publish = candidate
    source["scene"]["state"]["registry"]["object_registry"]["mouse_001"]["root_link"]["pos"][0] = (
        0.48
    )
    ref = publish(source, "edge-source.json")
    before = set(store.root.rglob("*"))
    with pytest.raises(ValueError, match="footprint"):
        adapter.adapt_gujie_tabletop_scene(
            store,
            source_ref=ref,
            representation_ref=representation,
            table_center_m=(0.0, 0.0, 0.4),
            table_size_m=(1.0, 0.6, 0.1),
            table_friction=0.5,
        )
    assert set(store.root.rglob("*")) == before


@pytest.mark.parametrize(
    "change",
    [
        "ground_relation",
        "asset_binding",
        "metadata",
        "scale",
        "fixed",
        "visual_only",
        "physics_override",
        "custom_usd",
        "joints",
        "velocity",
        "tilted_ground",
        "no_ground",
        "bad_quaternion",
        "boolean_position",
        "declaration_mismatch",
        "invalid_metadata",
    ],
)
def test_unsupported_source_semantics_are_rejected_before_writes(candidate, change):
    adapter = importlib.import_module("self_improving.harness.gujie_scene_adapter")
    store, representation, _, source, publish = candidate
    scene = source["scene"]
    args = scene["objects_info"]["init_info"]["mouse_001"]["args"]
    state = scene["state"]["registry"]["object_registry"]["mouse_001"]
    if change == "ground_relation":
        source["relation"]["target"] = "ground"
    elif change == "asset_binding":
        source["representation"]["sha256"] = "2" * 64
    elif change == "metadata":
        args["category"] = "different"
        source["metadata"] = dict(name="mouse_001", category="mouse", model="rigid")
    elif change == "scale":
        args["scale"] = [2, 1, 1]
    elif change == "fixed":
        args["fixed_base"] = True
    elif change == "visual_only":
        args["visual_only"] = True
    elif change == "physics_override":
        args["mass"] = 3
    elif change == "custom_usd":
        args["usd_path"] = "asset.usd"
    elif change == "joints":
        state["joint_pos"] = [0]
    elif change == "velocity":
        state["root_link"]["lin_vel"] = [1, 0, 0]
    elif change == "tilted_ground":
        scene["ground_plane_info"] = {"orientation": [1, 0, 0, 0]}
    elif change == "no_ground":
        scene["init_info"] = {"args": {"use_floor_plane": False}}
    elif change == "bad_quaternion":
        state["root_link"]["ori"] = [0, 0, 0, 2]
    elif change == "boolean_position":
        state["root_link"]["pos"] = [True, 0, 0]
    elif change == "declaration_mismatch":
        scene["objects_info"]["init_info"] = {}
    elif change == "invalid_metadata":
        source["metadata"]["category"] = ""
    ref = publish(source, "invalid-source.json")
    before = set(store.root.rglob("*"))
    with pytest.raises(ValueError):
        adapter.adapt_gujie_tabletop_scene(
            store,
            source_ref=ref,
            representation_ref=representation,
            table_center_m=(0.0, 0.0, 0.4),
            table_size_m=(1.0, 0.6, 0.1),
            table_friction=0.5,
        )
    assert set(store.root.rglob("*")) == before


def replace_representation(candidate, tmp_path, urdf_text, extra=()):
    store, ref, _, source, publish = candidate
    payload = json.loads(store.resolve(ref).path.read_bytes())
    path = tmp_path / "changed.urdf"
    path.write_text(urdf_text)
    root = store.put_file(
        path, name="object.urdf", media_type="application/xml", schema_version=None
    )
    members = [
        AssetRepresentationMemberV1(logical_path="object.urdf", role="urdf", artifact=root),
        *extra,
    ]
    members = tuple(sorted(members, key=lambda m: m.logical_path))
    payload["members"] = [m.model_dump(mode="json") for m in members]
    payload["loader_closure_sha256"] = asset_loader_closure_sha256(
        AssetModelSubjectV1.model_validate(payload["subject"]), "object.urdf", members
    )
    payload["representation_sha256"] = canonical_evidence_sha256(payload, "representation_sha256")
    model = AssetRepresentationV1.model_validate(payload)
    ref = publish(
        model.model_dump(mode="json"), "changed-representation.json", model.schema_version, True
    )
    source["representation"] = ref.model_dump(mode="json")
    return ref, publish(source, "changed-source.json")


def test_obj_mesh_scale_and_urdf_origin_transform_entire_geometry(candidate, tmp_path):
    adapter = importlib.import_module("self_improving.harness.gujie_scene_adapter")
    store, _, _, _, _ = candidate
    mesh = tmp_path / "shape.obj"
    mesh.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nv 0 0 1\nf 1 2 3\nf 1 2 4\n")
    member = AssetRepresentationMemberV1(
        logical_path="shape.obj",
        role="collision",
        artifact=store.put_file(
            mesh, name="shape.obj", media_type="text/plain", schema_version=None
        ),
    )
    geometry = (
        '<origin xyz="0.02 0 0.01" rpy="0 0 1.5707963267948966"/>'
        '<geometry><mesh filename="shape.obj" scale="0.04 0.02 0.02"/></geometry>'
    )
    urdf = (
        '<robot name="object"><link name="body"><inertial><mass value="1"/>'
        '<inertia ixx="0.01" ixy="0" ixz="0" iyy="0.01" iyz="0" izz="0.01"/>'
        "</inertial><visual>"
        + geometry
        + "</visual><collision>"
        + geometry
        + "</collision></link></robot>"
    )
    representation, source = replace_representation(candidate, tmp_path, urdf, (member,))
    ref = adapter.adapt_gujie_tabletop_scene(
        store,
        source_ref=source,
        representation_ref=representation,
        table_center_m=(0.0, 0.0, 0.4),
        table_size_m=(1.0, 0.6, 0.1),
        table_friction=0.5,
    )
    scene = adapter.read_gujie_tabletop_scene(store, ref)
    assert scene.position_m == pytest.approx((0.1, 0.05, 0.441))
    assert scene.local_vertices_m[1] == pytest.approx((0.02, 0.04, 0.01))
    assert len(scene.local_vertices_m) == 8


@pytest.mark.parametrize(
    "change",
    [
        "joint",
        "second_link",
        "no_inertia",
        "negative_mass",
        "invalid_inertia",
        "no_visual",
        "negative_box",
        "unknown_geometry",
        "entity",
        "resized_representation",
    ],
)
def test_nonrigid_or_invalid_authored_geometry_is_rejected(candidate, tmp_path, change):
    adapter = importlib.import_module("self_improving.harness.gujie_scene_adapter")
    store, ref, _, _, publish = candidate
    representation = json.loads(store.resolve(ref).path.read_bytes())
    from self_improving.harness.schemas.common import ArtifactRef

    text = store.resolve(
        ArtifactRef.model_validate(representation["members"][0]["artifact"])
    ).path.read_text()
    if change == "joint":
        text = text.replace("</robot>", '<joint name="j" type="fixed"/></robot>')
    elif change == "second_link":
        text = text.replace("</robot>", '<link name="other"/></robot>')
    elif change == "no_inertia":
        text = text.replace(
            '<inertia ixx="0.01" ixy="0" ixz="0" iyy="0.01" iyz="0" izz="0.01"/>', ""
        )
    elif change == "negative_mass":
        text = text.replace('mass value="1"', 'mass value="-1"')
    elif change == "invalid_inertia":
        text = text.replace('ixx="0.01"', 'ixx="3"')
    elif change == "no_visual":
        text = text.replace(
            '<visual><geometry><box size="0.10 0.06 0.04"/></geometry></visual>', ""
        )
    elif change == "negative_box":
        text = text.replace('size="0.10', 'size="-0.10')
    elif change == "unknown_geometry":
        text = text.replace('<box size="0.10 0.06 0.04"/>', '<sphere radius="0.1"/>')
    elif change == "entity":
        text = '<!DOCTYPE robot [<!ENTITY x "value">]>' + text
    newref, source = replace_representation(candidate, tmp_path, text)
    if change == "resized_representation":
        payload = json.loads(store.resolve(newref).path.read_bytes())
        payload["loader_scale"] = [2.0, 1.0, 1.0]
        payload["representation_sha256"] = canonical_evidence_sha256(
            payload, "representation_sha256"
        )
        newref = publish(payload, "scaled.json", "harness.asset_representation.v1", True)
        sourcepayload = json.loads(store.resolve(source).path.read_bytes())
        sourcepayload["representation"] = newref.model_dump(mode="json")
        source = publish(sourcepayload, "scaled-source.json")
    before = set(store.root.rglob("*"))
    with pytest.raises(ValueError):
        adapter.adapt_gujie_tabletop_scene(
            store,
            source_ref=source,
            representation_ref=newref,
            table_center_m=(0.0, 0.0, 0.4),
            table_size_m=(1.0, 0.6, 0.1),
            table_friction=0.5,
        )
    assert set(store.root.rglob("*")) == before


def test_invalid_table_is_rejected_without_orphan_geometry(candidate):
    adapter = importlib.import_module("self_improving.harness.gujie_scene_adapter")
    store, representation, source, _, _ = candidate
    before = set(store.root.rglob("*"))
    with pytest.raises(ValueError):
        adapter.adapt_gujie_tabletop_scene(
            store,
            source_ref=source,
            representation_ref=representation,
            table_center_m=(0.0, 0.0, -0.4),
            table_size_m=(1.0, 0.6, 0.1),
            table_friction=0.5,
        )
    assert set(store.root.rglob("*")) == before


@pytest.mark.parametrize("change", ["scene_pose", "geometry", "noncanonical_scene", "source_bytes"])
def test_pure_reader_recomputes_content_not_declared_geometry(candidate, tmp_path, change):
    adapter = importlib.import_module("self_improving.harness.gujie_scene_adapter")
    store, representation, source, _, publish = candidate
    ref = adapter.adapt_gujie_tabletop_scene(
        store,
        source_ref=source,
        representation_ref=representation,
        table_center_m=(0.0, 0.0, 0.4),
        table_size_m=(1.0, 0.6, 0.1),
        table_friction=0.5,
    )
    payload = json.loads(store.resolve(ref).path.read_bytes())
    if change == "scene_pose":
        payload["position_m"][2] += 0.05
    elif change == "geometry":
        payload["geometry"] = publish({"fake": "measurement"}, "fake-geometry.json").model_dump(
            mode="json"
        )
    elif change == "source_bytes":
        store.resolve(source).path.write_bytes(b"changed source")
    if change in {"scene_pose", "geometry"}:
        ref = publish(payload, "changed-scene.json", ref.schema_version)
    elif change == "noncanonical_scene":
        path = tmp_path / "spaced.json"
        path.write_text(json.dumps(payload, indent=2))
        ref = store.put_file(
            path,
            name="spaced.json",
            media_type="application/json",
            schema_version=ref.schema_version,
        )
    before = set(store.root.rglob("*"))
    with pytest.raises(ValueError):
        adapter.read_gujie_tabletop_scene(store, ref)
    assert set(store.root.rglob("*")) == before


@pytest.mark.parametrize(
    "change",
    [
        "parent_escape",
        "absolute_path",
        "missing_mesh",
        "negative_scale",
        "nonfinite_vertex",
        "empty_mesh",
        "flat_mesh",
    ],
)
def test_mesh_input_closure_fails_closed(candidate, tmp_path, change):
    adapter = importlib.import_module("self_improving.harness.gujie_scene_adapter")
    store, _, _, _, _ = candidate
    filename = {
        "parent_escape": "../shape.obj",
        "absolute_path": "/shape.obj",
        "missing_mesh": "missing.obj",
    }.get(change, "shape.obj")
    meshtext = "v 0 0 0\nv 1 0 0\nv 0 1 0\nv 0 0 1\nf 1 2 3\n"
    if change == "nonfinite_vertex":
        meshtext = meshtext.replace("v 0 0 1", "v 0 0 nan")
    elif change == "empty_mesh":
        meshtext = "# no geometry\n"
    elif change == "flat_mesh":
        meshtext = meshtext.replace("v 0 0 1", "v 1 1 0")
    mesh = tmp_path / "mesh.obj"
    mesh.write_text(meshtext)
    member = AssetRepresentationMemberV1(
        logical_path="shape.obj",
        role="collision",
        artifact=store.put_file(
            mesh, name="shape.obj", media_type="text/plain", schema_version=None
        ),
    )
    scale = "-0.02 0.02 0.02" if change == "negative_scale" else "0.02 0.02 0.02"
    geom = f'<geometry><mesh filename="{filename}" scale="{scale}"/></geometry>'
    text = (
        '<robot name="object"><link name="body"><inertial><mass value="1"/>'
        '<inertia ixx="0.01" ixy="0" ixz="0" iyy="0.01" iyz="0" izz="0.01"/>'
        "</inertial><visual>" + geom + "</visual><collision>" + geom + "</collision></link></robot>"
    )
    representation, source = replace_representation(candidate, tmp_path, text, (member,))
    before = set(store.root.rglob("*"))
    with pytest.raises(ValueError):
        adapter.adapt_gujie_tabletop_scene(
            store,
            source_ref=source,
            representation_ref=representation,
            table_center_m=(0.0, 0.0, 0.4),
            table_size_m=(1.0, 0.6, 0.1),
            table_friction=0.5,
        )
    assert set(store.root.rglob("*")) == before
