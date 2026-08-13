from __future__ import annotations

import dataclasses
import math
from pathlib import Path

import pytest

from scene_gen.catalog import load_catalog
from scene_gen.parser import parse_rule_based
from scene_gen.schema import RelationType, SceneSpec
from scene_gen.solver import SceneSolveError, solve_scene
from scene_gen.support_geometry import support_footprint_margin

ROOT = Path(__file__).resolve().parents[2]


def _real_catalog():
    return load_catalog(ROOT / "tests" / "fixtures" / "asset_catalog.json")


def test_solver_is_deterministic_and_preserves_real_asset_paths() -> None:
    catalog = _real_catalog()
    spec = parse_rule_based("A red can is left of a plastic basket near the center.", seed=17)
    first = solve_scene(spec, catalog)
    second = solve_scene(spec, catalog)
    assert first.digest() == second.digest()
    assert first.source_scene_spec_sha256 == spec.digest()
    assert first.asset_catalog_sha256 == catalog.digest()
    assert [item.asset_id for item in first.objects] == ["110_basket", "071_can"]
    assert all(Path(path).is_absolute() for item in first.objects for path in item.source_files)
    basket = next(item for item in first.objects if item.asset_id == "110_basket")
    assert basket.stable_pose_id == "robotwin_basket_upright"
    assert basket.pose.orientation_wxyz != (
        math.cos(basket.pose.yaw_rad / 2.0),
        0.0,
        0.0,
        math.sin(basket.pose.yaw_rad / 2.0),
    )


def test_solver_meets_all_geometric_relations() -> None:
    catalog = _real_catalog()
    spec = parse_rule_based("A cup is in front of a wooden block and at least 0.20 m away.", seed=19)
    resolved = solve_scene(spec, catalog)
    objects = {item.object_id: item for item in resolved.objects}
    cup = objects["cup_1"]
    block = objects["block_1"]
    assert cup.pose.position_m[1] > block.pose.position_m[1]
    assert math.dist(cup.pose.position_m[:2], block.pose.position_m[:2]) >= 0.20
    assert any(item.relation == RelationType.FRONT_OF for item in resolved.relations)
    assert resolved.solver_trace.status == "pass"
    assert resolved.solver_trace.total_attempts <= 2 * resolved.solver_trace.max_attempts_per_object * 49


def test_fixed_100_seed_gate_passes_for_the_declared_can_basket_case() -> None:
    catalog = _real_catalog()
    digests: set[str] = set()
    failures: list[int] = []
    for seed in range(100):
        spec = parse_rule_based(
            "Place a red can to the left of a plastic basket near the center.",
            seed=seed,
        )
        try:
            resolved = solve_scene(spec, catalog)
        except SceneSolveError:
            failures.append(seed)
            continue
        digests.add(resolved.digest())
    assert len(failures) <= 5, failures
    assert len(digests) >= 95


def test_impossible_workspace_fails_with_bounded_machine_readable_trace() -> None:
    catalog = _real_catalog()
    spec = parse_rule_based("A plate is left of a basket near the center.", seed=3)
    payload = spec.canonical_dict()
    payload["workspace"]["x_bounds_m"] = [-0.05, 0.05]
    payload["workspace"]["y_bounds_m"] = [-0.02, 0.02]
    payload["workspace"]["robot_keepout_x_m"] = [-0.01, 0.01]
    payload["workspace"]["robot_keepout_y_m"] = [-0.02, -0.01]
    impossible = SceneSpec.model_validate(payload)
    with pytest.raises(SceneSolveError) as raised:
        solve_scene(impossible, catalog, max_attempts_per_object=4, max_backtracks=2)
    assert raised.value.report["status"] == "fail"
    assert raised.value.report["blocker"] == "bounded solver exhausted"
    assert raised.value.report["total_attempts"] <= 8
    assert raised.value.report["attempts"]


def test_solver_places_stack_and_container_contents_in_three_dimensions() -> None:
    catalog = _real_catalog()
    stacked = solve_scene(parse_rule_based("Place a can on top of a plate.", seed=31), catalog)
    stack_objects = {item.object_id: item for item in stacked.objects}
    can = stack_objects["can_1"]
    plate = stack_objects["plate_1"]
    assert can.support_relation == RelationType.ON_TOP_OF
    assert can.support_target == "plate_1"
    assert can.pose.position_m[2] > plate.pose.position_m[2]
    assert can.is_static is False
    assert plate.is_static is False
    center_distance = math.dist(can.pose.position_m[:2], plate.pose.position_m[:2])
    assert center_distance <= 0.0065
    margin = support_footprint_margin(
        source_dimensions_m=can.dimensions_m,
        source_yaw=can.pose.yaw_rad,
        source_shape=can.footprint_shape,
        target_surface_dimensions_m=plate.support_surface_dimensions_m,
        target_yaw=plate.pose.yaw_rad,
        target_surface_shape=plate.support_surface_shape,
        dx=can.pose.position_m[0] - plate.pose.position_m[0],
        dy=can.pose.position_m[1] - plate.pose.position_m[1],
    )
    assert margin >= plate.support_margin_m

    contained = solve_scene(parse_rule_based("Put an apple inside a basket.", seed=32), catalog)
    inside_objects = {item.object_id: item for item in contained.objects}
    apple = inside_objects["apple_1"]
    basket = inside_objects["basket_1"]
    assert apple.support_relation == RelationType.INSIDE
    assert apple.support_target == "basket_1"
    assert apple.is_static is False
    assert basket.is_static is True
    assert basket.interior_floor_z_offset_m == pytest.approx(0.012)
    assert apple.pose.position_m[2] == pytest.approx(
        basket.pose.position_m[2]
        + basket.interior_floor_z_offset_m
        + basket.support_spawn_clearance_m
    )
    assert abs(apple.pose.position_m[0] - basket.pose.position_m[0]) < basket.dimensions_m[0] / 2
    assert abs(apple.pose.position_m[1] - basket.pose.position_m[1]) < basket.dimensions_m[1] / 2


def test_solver_rejects_a_source_that_cannot_fit_the_stable_support_surface() -> None:
    catalog = _real_catalog()
    with pytest.raises(SceneSolveError) as raised:
        solve_scene(
            parse_rule_based("Place a red block on top of a plate.", seed=31),
            catalog,
            max_attempts_per_object=4,
        )
    reasons = {
        reason
        for attempt in raised.value.report["attempts"]
        for reason in attempt["reasons"]
    }
    assert "object footprint does not fit stable support surface" in reasons


def test_can_plate_100_seed_gate_preserves_stability_margin() -> None:
    catalog = _real_catalog()
    margins: list[float] = []
    center_distances: list[float] = []
    for seed in range(100):
        resolved = solve_scene(
            parse_rule_based("Place a can on top of a plate.", seed=seed),
            catalog,
        )
        objects = {item.object_id: item for item in resolved.objects}
        can = objects["can_1"]
        plate = objects["plate_1"]
        center_distances.append(math.dist(can.pose.position_m[:2], plate.pose.position_m[:2]))
        margins.append(
            support_footprint_margin(
                source_dimensions_m=can.dimensions_m,
                source_yaw=can.pose.yaw_rad,
                source_shape=can.footprint_shape,
                target_surface_dimensions_m=plate.support_surface_dimensions_m,
                target_yaw=plate.pose.yaw_rad,
                target_surface_shape=plate.support_surface_shape,
                dx=can.pose.position_m[0] - plate.pose.position_m[0],
                dy=can.pose.position_m[1] - plate.pose.position_m[1],
            )
        )
    assert min(margins) >= 0.008 - 1e-9
    assert max(center_distances) <= 0.0065


def test_solver_maps_semantic_articulation_to_all_movable_joint_qpos() -> None:
    from scene_gen.catalog import AssetCatalog, CatalogEntry, CatalogJoint, CatalogModel

    catalog = AssetCatalog(
        robotwin_root="/tmp/RoboTwin",
        objects_root="/tmp/RoboTwin/assets/objects",
        entries=(
            CatalogEntry(
                asset_id="036_cabinet",
                semantic_name="cabinet",
                category="cabinet",
                aliases=("cabinet",),
                load_type="urdf",
                asset_path="/tmp/RoboTwin/assets/objects/036_cabinet",
                available=True,
                models=(
                    CatalogModel(
                        model_id=46653,
                        urdf_path="/tmp/RoboTwin/assets/objects/036_cabinet/46653/mobility.urdf",
                        scale=(0.27, 0.27, 0.27),
                        dimensions_m=(0.23, 0.24, 0.43),
                        stable_pose_id="upright",
                        stable_orientation_wxyz=(1.0, 0.0, 0.0, 0.0),
                        articulation_joints=(
                            CatalogJoint(name="drawer_1", joint_type="prismatic", lower=0.0, upper=0.66),
                            CatalogJoint(name="drawer_2", joint_type="prismatic", lower=0.0, upper=0.66),
                            CatalogJoint(name="drawer_3", joint_type="prismatic", lower=0.0, upper=0.66),
                        ),
                        articulation_closed_qpos=(0.0, 0.0, 0.0),
                        articulation_open_qpos=(0.6, 0.5, 0.4),
                        usable=True,
                    ),
                ),
            ),
        ),
    )
    resolved = solve_scene(
        parse_rule_based("Place a half-open cabinet on the table.", seed=33),
        catalog,
    )
    cabinet = resolved.objects[0]
    assert cabinet.articulation_joint_names == ("drawer_1", "drawer_2", "drawer_3")
    assert cabinet.articulation_qpos == pytest.approx((0.3, 0.25, 0.2))
