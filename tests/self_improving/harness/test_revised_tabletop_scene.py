"""Actual URDF geometry, immutable source and revised finite-table placement."""

import math

import pytest

from self_improving.harness.gujie_scene_adapter import (
    adapt_gujie_tabletop_scene,
    read_gujie_tabletop_scene,
)
from self_improving.harness.revised_tabletop_scene import (
    derive_revised_tabletop_scene,
    verify_revised_tabletop_scene,
)
from self_improving.harness.schemas.native_prompt_revision import TabletopLayoutRevisionV1
from tests.self_improving.harness.test_gujie_scene_adapter import candidate  # noqa: F401
from tests.self_improving.harness.test_native_prompt_revision_contracts import layout


def prepared(asset_fixture):
    store, representation, source, _, _ = asset_fixture
    scene = adapt_gujie_tabletop_scene(
        store,
        source_ref=source,
        representation_ref=representation,
        table_center_m=(0.0, 0.0, 0.4),
        table_size_m=(1.0, 0.6, 0.1),
        table_friction=0.5,
    )
    data = layout()
    data.update(
        original_source=source,
        source_object_id="mouse_001",
        position_xy_m=(-0.2, 0.1),
        yaw_rad=0.0,
        table_center_m=(0.0, 0.0, 0.4),
        table_size_m=(1.0, 0.6, 0.1),
    )
    return store, scene, TabletopLayoutRevisionV1.model_validate(data)


def test_xy_yaw_revision_changes_real_scene_and_preserves_source(candidate):  # noqa: F811
    store, original, change = prepared(candidate)
    before = store.resolve(change.original_source).path.read_bytes()
    derived = derive_revised_tabletop_scene(store, layout=change, predecessor_scene=original)
    actual = verify_revised_tabletop_scene(
        store, layout=change, predecessor_scene=original, derived=derived
    )
    prior = read_gujie_tabletop_scene(store, original)
    assert derived.scene != original
    assert actual.position_m == pytest.approx((-0.2, 0.1, 0.471))
    assert actual.orientation_wxyz == pytest.approx((1.0, 0.0, 0.0, 0.0))
    assert actual.representation == prior.representation
    assert actual.table_friction == prior.table_friction
    assert store.resolve(change.original_source).path.read_bytes() == before


def test_centre_inside_table_is_insufficient_for_complete_collision_footprint(candidate):  # noqa: F811
    store, original, change = prepared(candidate)
    change = TabletopLayoutRevisionV1.model_validate(
        {**change.model_dump(), "position_xy_m": (0.48, 0.0), "yaw_rad": math.pi / 4}
    )
    with pytest.raises(ValueError, match="footprint"):
        derive_revised_tabletop_scene(store, layout=change, predecessor_scene=original)


@pytest.mark.parametrize(
    "variation", ("wrong_object", "wrong_source", "wrong_layout", "missing", "symlink", "alias")
)
def test_derivation_rejects_source_and_locator_drift(candidate, tmp_path, variation):  # noqa: F811
    from self_improving.harness.revised_tabletop_scene import read_revision_artifact

    store, original, change = prepared(candidate)
    if variation == "wrong_layout":
        with pytest.raises(TypeError):
            derive_revised_tabletop_scene(store, layout=object(), predecessor_scene=original)
        return
    if variation == "wrong_object":
        change = change.model_copy(update={"source_object_id": "different"})
    elif variation == "wrong_source":
        change = change.model_copy(
            update={"original_source": original.model_copy(update={"schema_version": None})}
        )
    else:
        path = store.resolve(change.original_source).path
        if variation == "missing":
            path.unlink()
        elif variation == "symlink":
            target = tmp_path / "source-copy.json"
            target.write_bytes(path.read_bytes())
            path.unlink()
            path.symlink_to(target)
        else:
            with pytest.raises(ValueError, match="locators"):
                read_revision_artifact(
                    store, change.original_source.model_copy(update={"uri": path.as_uri()})
                )
            return
    with pytest.raises(ValueError):
        derive_revised_tabletop_scene(store, layout=change, predecessor_scene=original)


def test_tilted_source_is_not_silently_flattened(candidate):  # noqa: F811
    store, representation, _, source, publish = candidate
    source["scene"]["state"]["registry"]["object_registry"]["mouse_001"]["root_link"]["ori"] = [
        math.sin(0.2),
        0.0,
        0.0,
        math.cos(0.2),
    ]
    source_ref = publish(source, "tilted.json")
    original = adapt_gujie_tabletop_scene(
        store,
        source_ref=source_ref,
        representation_ref=representation,
        table_center_m=(0.0, 0.0, 0.4),
        table_size_m=(1.0, 0.6, 0.1),
        table_friction=0.5,
    )
    data = layout()
    data.update(original_source=source_ref, source_object_id="mouse_001")
    with pytest.raises(ValueError, match="upright"):
        derive_revised_tabletop_scene(
            store, layout=TabletopLayoutRevisionV1.model_validate(data), predecessor_scene=original
        )


@pytest.mark.parametrize("variation", ("record", "origin", "source_physics", "other_scene"))
def test_pure_reader_rejects_derived_source_or_scene_replacement(candidate, variation):  # noqa: F811
    import json
    from dataclasses import replace

    store, original, change = prepared(candidate)
    derived = derive_revised_tabletop_scene(store, layout=change, predecessor_scene=original)
    if variation == "record":
        derived = object()
    elif variation == "origin":
        derived = replace(derived, original_source=derived.derived_source)
    elif variation == "source_physics":
        body = json.loads(store.resolve(derived.derived_source).path.read_bytes())
        body["scene"]["objects_info"]["init_info"]["mouse_001"]["args"]["mass"] = 0.2
        derived = replace(derived, derived_source=candidate[4](body, "changed-physics.json"))
    else:
        other = adapt_gujie_tabletop_scene(
            store,
            source_ref=derived.derived_source,
            representation_ref=read_gujie_tabletop_scene(store, original).representation,
            table_center_m=change.table_center_m,
            table_size_m=change.table_size_m,
            table_friction=0.9,
        )
        derived = replace(derived, scene=other)
    before = set(store.root.rglob("*"))
    with pytest.raises((TypeError, ValueError)):
        verify_revised_tabletop_scene(
            store, layout=change, predecessor_scene=original, derived=derived
        )
    assert before == set(store.root.rglob("*"))


def test_read_revision_artifact_requires_real_local_cas(candidate):  # noqa: F811
    from self_improving.harness.revised_tabletop_scene import read_revision_artifact

    with pytest.raises(TypeError):
        read_revision_artifact(object(), candidate[2])


def test_table_geometry_is_consumed_and_revalidated(candidate):  # noqa: F811
    store, original, change = prepared(candidate)
    first = derive_revised_tabletop_scene(store, layout=change, predecessor_scene=original)
    revised = TabletopLayoutRevisionV1.model_validate(
        {**change.model_dump(), "table_center_m": (-0.2, 0.1, 0.7), "table_size_m": (0.4, 0.3, 0.2)}
    )
    second = derive_revised_tabletop_scene(store, layout=revised, predecessor_scene=original)
    actual = verify_revised_tabletop_scene(
        store, layout=revised, predecessor_scene=original, derived=second
    )
    assert second.scene != first.scene
    assert second.derived_source == first.derived_source  # Object-only source has no table fields.
    assert actual.table_center_m == revised.table_center_m
    assert actual.table_size_m == revised.table_size_m
    assert actual.position_m[2] == pytest.approx(0.821)
    too_small = TabletopLayoutRevisionV1.model_validate(
        {**revised.model_dump(), "table_size_m": (0.04, 0.04, 0.2)}
    )
    with pytest.raises(ValueError, match="footprint"):
        derive_revised_tabletop_scene(store, layout=too_small, predecessor_scene=original)


@pytest.mark.parametrize("degrees", (0.99, 1.0, 1.01))
def test_near_upright_boundary_preserves_original_tilt(candidate, degrees):  # noqa: F811
    store, representation, _, source, publish = candidate
    angle = math.radians(degrees)
    source["scene"]["state"]["registry"]["object_registry"]["mouse_001"]["root_link"]["ori"] = [
        math.sin(angle / 2),
        0.0,
        0.0,
        math.cos(angle / 2),
    ]
    source_ref = publish(source, "near-upright.json")
    original = adapt_gujie_tabletop_scene(
        store,
        source_ref=source_ref,
        representation_ref=representation,
        table_center_m=(0.0, 0.0, 0.4),
        table_size_m=(1.0, 0.6, 0.1),
        table_friction=0.5,
    )
    data = layout()
    data.update(original_source=source_ref, source_object_id="mouse_001", yaw_rad=0.8)
    change = TabletopLayoutRevisionV1.model_validate(data)
    if degrees > 1.0:
        with pytest.raises(ValueError, match="upright"):
            derive_revised_tabletop_scene(store, layout=change, predecessor_scene=original)
        return
    derived = derive_revised_tabletop_scene(store, layout=change, predecessor_scene=original)
    scene = verify_revised_tabletop_scene(
        store, layout=change, predecessor_scene=original, derived=derived
    )
    w, x, y, z = scene.orientation_wxyz
    assert 1 - 2 * (x * x + y * y) == pytest.approx(math.cos(angle), abs=1e-12)
    assert math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)) == pytest.approx(0.8)


def test_second_absolute_cas_reloads_derived_geometry_without_source_tree(candidate, tmp_path):  # noqa: F811
    import shutil

    from self_improving.harness.artifacts import LocalArtifactStore

    store, original, change = prepared(candidate)
    derived = derive_revised_tabletop_scene(store, layout=change, predecessor_scene=original)
    relocated = tmp_path / "relocated-cas"
    shutil.copytree(store.root, relocated)
    shutil.rmtree(store.root)
    reader = LocalArtifactStore(relocated)
    before = {p.relative_to(relocated): p.read_bytes() for p in relocated.rglob("*") if p.is_file()}
    scene = verify_revised_tabletop_scene(
        reader, layout=change, predecessor_scene=original, derived=derived
    )
    assert scene.position_m[:2] == change.position_xy_m
    assert before == {
        p.relative_to(relocated): p.read_bytes() for p in relocated.rglob("*") if p.is_file()
    }
