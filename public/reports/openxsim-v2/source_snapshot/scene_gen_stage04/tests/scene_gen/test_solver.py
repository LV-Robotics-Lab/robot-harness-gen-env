from __future__ import annotations

import dataclasses
import math
from pathlib import Path

import pytest

from scene_gen.catalog import load_catalog
from scene_gen.parser import parse_rule_based
from scene_gen.schema import RelationType, SceneSpec
from scene_gen.solver import SceneSolveError, solve_scene

ROOT = Path(__file__).resolve().parents[2]


def _real_catalog():
    return load_catalog(ROOT / "data" / "scene_gen" / "asset_catalog.json")


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
