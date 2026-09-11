"""Actual URDF geometry, immutable source and revised finite-table placement."""

import math

import pytest

from self_improving.harness.gujie_scene_adapter import adapt_gujie_tabletop_scene, read_gujie_tabletop_scene
from self_improving.harness.revised_tabletop_scene import derive_revised_tabletop_scene, verify_revised_tabletop_scene
from self_improving.harness.schemas.native_prompt_revision import TabletopLayoutRevisionV1
from tests.self_improving.harness.test_gujie_scene_adapter import candidate  # noqa: F401
from tests.self_improving.harness.test_native_prompt_revision_contracts import layout


def prepared(candidate):
    store, representation, source, _, _ = candidate
    scene=adapt_gujie_tabletop_scene(store, source_ref=source,representation_ref=representation,
        table_center_m=(0.0,0.0,0.4),table_size_m=(1.0,0.6,0.1),table_friction=0.5)
    data=layout()
    data.update(original_source=source,source_object_id="mouse_001",position_xy_m=(-0.2,0.1),yaw_rad=0.0,
                table_center_m=(0.0,0.0,0.4),table_size_m=(1.0,0.6,0.1))
    return store,scene,TabletopLayoutRevisionV1.model_validate(data)


def test_xy_yaw_revision_changes_real_scene_and_preserves_source(candidate):  # noqa: F811
    store,original,change=prepared(candidate)
    before=store.resolve(change.original_source).path.read_bytes()
    derived=derive_revised_tabletop_scene(store,layout=change,predecessor_scene=original)
    actual=verify_revised_tabletop_scene(store,layout=change,predecessor_scene=original,derived=derived)
    prior=read_gujie_tabletop_scene(store,original)
    assert derived.scene != original
    assert actual.position_m==pytest.approx((-0.2,0.1,0.471))
    assert actual.orientation_wxyz==pytest.approx((1.0,0.0,0.0,0.0))
    assert actual.representation==prior.representation
    assert actual.table_friction==prior.table_friction
    assert store.resolve(change.original_source).path.read_bytes()==before


def test_centre_inside_table_is_insufficient_for_complete_collision_footprint(candidate):  # noqa: F811
    store,original,change=prepared(candidate)
    change=TabletopLayoutRevisionV1.model_validate({**change.model_dump(),"position_xy_m":(0.48,0.0),"yaw_rad":math.pi/4})
    with pytest.raises(ValueError,match="footprint"):
        derive_revised_tabletop_scene(store,layout=change,predecessor_scene=original)
