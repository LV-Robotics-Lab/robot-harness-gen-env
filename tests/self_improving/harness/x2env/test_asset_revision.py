"""Approved asset patches create immutable children; no runtime success is implied."""

import json
import struct
import xml.etree.ElementTree as ET

import pytest
import trimesh

from self_improving.harness.x2env.assets import AssetLicense, AssetRegistry, AssetSource
from self_improving.harness.x2env.normalization import normalize_mesh
from self_improving.harness.x2env.store import Store


def fixture(tmp_path, vertex_color=False):
    store = Store(tmp_path / "state")
    registry = AssetRegistry(store)
    source = tmp_path / "source.glb"
    source.write_bytes(trimesh.creation.box().export(file_type="glb"))
    root = tmp_path / "normalized"
    report = normalize_mesh(
        source,
        root,
        dimensions_m=(0.1, 0.1, 0.1),
        up_axis="Z",
        mass_kg=0.1,
        friction=0.6,
        color_rgba=(0.0, 1.0, 0.0, 1.0) if vertex_color else None,
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
