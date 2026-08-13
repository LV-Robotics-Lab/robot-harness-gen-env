from __future__ import annotations

import ast
import json
import math
from pathlib import Path

from scene_gen.builder import build_scene_package, verify_package
from scene_gen.catalog import load_catalog
from scene_gen.parser import parse_rule_based
from scene_gen.schema import ResolvedSceneSpec
from scene_gen.solver import solve_scene
from scene_gen.validator import validate_resolved_scene

ROOT = Path(__file__).resolve().parents[2]


def solved_case(seed: int = 23):
    catalog = load_catalog(ROOT / "tests" / "fixtures" / "asset_catalog.json")
    spec = parse_rule_based("A red can is left of a plastic basket near the center.", seed=seed)
    return catalog, spec, solve_scene(spec, catalog)


def stacked_case(seed: int = 42):
    catalog = load_catalog(ROOT / "tests" / "fixtures" / "asset_catalog.json")
    spec = parse_rule_based("Place a can on top of a plate.", seed=seed)
    return catalog, spec, solve_scene(spec, catalog)


def contained_case(seed: int = 32):
    catalog = load_catalog(ROOT / "tests" / "fixtures" / "asset_catalog.json")
    spec = parse_rule_based("Put an apple inside a basket.", seed=seed)
    return catalog, spec, solve_scene(spec, catalog)


def test_builder_writes_hash_bound_resolved_only_replay_package(tmp_path: Path) -> None:
    _, spec, resolved = solved_case()
    manifest = build_scene_package(spec, resolved, tmp_path)
    assert manifest["source_scene_spec_sha256"] == spec.digest()
    assert manifest["resolved_scene_sha256"] == resolved.digest()
    assert manifest["resolved_only_entrypoint"] == "scene_gen.envs.generated_scene:load_resolved_scene"
    assert verify_package(tmp_path)["status"] == "pass"

    replayed = ResolvedSceneSpec.model_validate_json((tmp_path / "resolved_scene.json").read_text(encoding="utf-8"))
    assert replayed.digest() == resolved.digest()
    module_source = (tmp_path / "generated_scene.py").read_text(encoding="utf-8")
    tree = ast.parse(module_source)
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"exec", "eval"}
        for node in ast.walk(tree)
    )
    assert "exec(" not in module_source
    assert "eval(" not in module_source


def test_package_verifier_detects_tampering(tmp_path: Path) -> None:
    _, spec, resolved = solved_case()
    build_scene_package(spec, resolved, tmp_path)
    (tmp_path / "request.txt").write_text("tampered\n", encoding="utf-8")
    report = verify_package(tmp_path)
    assert report["status"] == "fail"
    assert any(item["path"] == "request.txt" and not item["pass"] for item in report["checks"])


def test_static_validator_checks_bounds_overlap_relations_and_roundtrip(tmp_path: Path) -> None:
    _, spec, resolved = solved_case()
    build_scene_package(spec, resolved, tmp_path)
    report = validate_resolved_scene(resolved, package_root=tmp_path)
    assert report["status"] == "incomplete"
    assert report["fail_count"] == 0
    assert report["not_run_count"] >= 1
    assert all(
        item["status"] == "pass"
        for item in report["checks"]
        if item["name"].startswith(("workspace_bounds", "no_overlap", "relation", "resolved_only", "package_manifest"))
    )


def test_runtime_validator_requires_each_object_visibility_and_physics() -> None:
    _, _, resolved = solved_case()
    evidence = {
        "schema_version": "robotwin.scene_runtime_evidence.v1",
        "status": "pass",
        "robot_initial_collision_count": 0,
        "objects": {
            item.object_id: {
                "translation_drift_m": 0.001,
                "rotation_drift_deg": 0.2,
                "resolved_translation_error_m": 0.001,
                "resolved_rotation_error_deg": 0.2,
                "penetration_count": 0,
                "still_moving": False,
                "support_contact": not item.is_static,
                "support_contact_fraction": 1.0 if not item.is_static else 0.0,
                "unexpected_contact_fraction": 0.0,
                "unexpected_contact_targets": [],
                "support_mode": "fixed_static_pose" if item.is_static else "on_table_contact",
                "support_target": None if item.is_static else "table",
                "dropped": False,
                "visible_pixels": 512,
            }
            for item in resolved.objects
        },
    }
    report = validate_resolved_scene(resolved, runtime_evidence=evidence, require_runtime=True)
    assert report["status"] == "pass"

    failed = json.loads(json.dumps(evidence))
    failed["objects"][resolved.objects[0].object_id]["visible_pixels"] = 1
    failed_report = validate_resolved_scene(resolved, runtime_evidence=failed, require_runtime=True)
    assert failed_report["status"] == "fail"
    assert any(
        item["name"] == f"head_visibility:{resolved.objects[0].object_id}" and item["status"] == "fail"
        for item in failed_report["checks"]
    )

    endpoint_only_video = json.loads(json.dumps(evidence))
    endpoint_only_video["video_frame_count"] = 120
    endpoint_only_video["unique_video_frame_count"] = 2
    endpoint_report = validate_resolved_scene(
        resolved,
        runtime_evidence=endpoint_only_video,
        require_runtime=True,
    )
    unique_frames = next(
        item
        for item in endpoint_report["checks"]
        if item["name"] == "observer_video_unique_frames"
    )
    assert unique_frames["status"] == "fail"
    assert unique_frames["evidence"]["minimum"] == 30


def test_runtime_v2_validates_dynamic_relations_instead_of_exact_spawn_pose() -> None:
    _, _, resolved = solved_case()
    relations = {
        f"{relation.relation.value}:{relation.source}:{relation.target}": {
            "pass": True,
        }
        for relation in resolved.relations
        if relation.target != "table"
    }
    evidence = {
        "schema_version": "robotwin.scene_runtime_evidence.v2",
        "status": "pass",
        "robot_initial_collision_count": 0,
        "relations": relations,
        "objects": {},
    }
    for item in resolved.objects:
        dynamic = not item.is_static
        evidence["objects"][item.object_id] = {
            "translation_drift_m": 0.06 if dynamic else 0.0,
            "rotation_drift_deg": 45.0 if dynamic else 0.0,
            "resolved_translation_error_m": 0.06 if dynamic else 0.0,
            "resolved_rotation_error_deg": 45.0 if dynamic else 0.0,
            "penetration_count": 0,
            "still_moving": False,
            "support_contact": dynamic,
            "support_contact_fraction": 1.0 if dynamic else 0.0,
            "unexpected_contact_fraction": 0.0,
            "unexpected_contact_targets": [],
            "support_mode": "on_table_contact" if dynamic else "fixed_static_pose",
            "support_target": "table" if dynamic else None,
            "dropped": False,
            "visible_pixels": 512,
        }

    report = validate_resolved_scene(resolved, runtime_evidence=evidence, require_runtime=True)
    assert report["status"] == "pass"
    dynamic_id = next(item.object_id for item in resolved.objects if not item.is_static)
    assert next(
        item for item in report["checks"] if item["name"] == f"translation_drift:{dynamic_id}"
    )["status"] == "not_applicable"

    failed = json.loads(json.dumps(evidence))
    failed["relations"][next(iter(relations))]["pass"] = False
    failed_report = validate_resolved_scene(
        resolved,
        runtime_evidence=failed,
        require_runtime=True,
    )
    assert failed_report["status"] == "fail"
    assert any(
        item["name"].startswith("runtime_relation:") and item["status"] == "fail"
        for item in failed_report["checks"]
    )


def test_runtime_validator_accepts_explicit_fixed_static_support_only_for_static_objects() -> None:
    _, _, resolved = solved_case()
    evidence = {
        "schema_version": "robotwin.scene_runtime_evidence.v1",
        "status": "pass",
        "robot_initial_collision_count": 0,
        "objects": {},
    }
    for item in resolved.objects:
        evidence["objects"][item.object_id] = {
            "translation_drift_m": 0.0,
            "rotation_drift_deg": 0.0,
            "resolved_translation_error_m": 0.0,
            "resolved_rotation_error_deg": 0.0,
            "penetration_count": 0,
            "still_moving": False,
            "support_contact": False,
            "support_contact_fraction": 0.0,
            "unexpected_contact_fraction": 0.0,
            "unexpected_contact_targets": [],
            "support_mode": "fixed_static_pose" if item.is_static else "table_contact",
            "support_target": None,
            "dropped": False,
            "visible_pixels": 512,
        }
    dynamic = next(item for item in resolved.objects if not item.is_static)
    evidence["objects"][dynamic.object_id]["support_contact"] = True
    evidence["objects"][dynamic.object_id]["support_contact_fraction"] = 1.0
    evidence["objects"][dynamic.object_id]["support_target"] = "table"
    report = validate_resolved_scene(resolved, runtime_evidence=evidence, require_runtime=True)
    assert report["status"] == "pass"

    evidence["objects"][dynamic.object_id]["support_contact"] = False
    evidence["objects"][dynamic.object_id]["support_contact_fraction"] = 0.0
    evidence["objects"][dynamic.object_id]["support_mode"] = "fixed_static_pose"
    failed = validate_resolved_scene(resolved, runtime_evidence=evidence, require_runtime=True)
    assert any(
        item["name"] == f"support_contact:{dynamic.object_id}" and item["status"] == "fail"
        for item in failed["checks"]
    )


def test_static_validator_rejects_edge_placement_even_inside_outer_plate_bounds() -> None:
    _, _, resolved = stacked_case()
    objects = {item.object_id: item for item in resolved.objects}
    can = objects["can_1"]
    plate = objects["plate_1"]
    edge_pose = can.pose.model_copy(
        update={
            "position_m": (
                plate.pose.position_m[0] + 0.04,
                plate.pose.position_m[1],
                can.pose.position_m[2],
            )
        }
    )
    edge_can = can.model_copy(update={"pose": edge_pose})
    edge_scene = resolved.model_copy(
        update={
            "objects": tuple(
                edge_can if item.object_id == can.object_id else item
                for item in resolved.objects
            )
        }
    )
    report = validate_resolved_scene(edge_scene)
    relation = next(
        item
        for item in report["checks"]
        if item["name"] == "relation:on_top_of:can_1:plate_1"
    )
    assert relation["status"] == "fail"
    assert relation["evidence"]["support_footprint_margin_m"] < 0.008


def test_static_validator_rejects_target_local_container_overflow() -> None:
    _, _, resolved = contained_case()
    objects = {item.object_id: item for item in resolved.objects}
    apple = objects["apple_1"]
    basket = objects["basket_1"]
    local_y = 0.04
    world_dx = -math.sin(basket.pose.yaw_rad) * local_y
    world_dy = math.cos(basket.pose.yaw_rad) * local_y
    escaped_pose = apple.pose.model_copy(
        update={
            "position_m": (
                basket.pose.position_m[0] + world_dx,
                basket.pose.position_m[1] + world_dy,
                apple.pose.position_m[2],
            )
        }
    )
    escaped_apple = apple.model_copy(update={"pose": escaped_pose})
    attacked = resolved.model_copy(
        update={
            "objects": tuple(
                escaped_apple if item.object_id == apple.object_id else item
                for item in resolved.objects
            )
        }
    )
    report = validate_resolved_scene(attacked)
    relation = next(
        item
        for item in report["checks"]
        if item["name"] == "relation:inside:apple_1:basket_1"
    )
    assert relation["status"] == "fail"
    assert relation["evidence"]["inside_footprint_margin_m"] < 0.0


def test_runtime_validator_rejects_static_contact_free_nested_support() -> None:
    _, _, resolved = stacked_case()
    objects = {item.object_id: item for item in resolved.objects}
    can = objects["can_1"]
    static_can = can.model_copy(update={"is_static": True})
    attacked = resolved.model_copy(
        update={
            "objects": tuple(
                static_can if item.object_id == can.object_id else item
                for item in resolved.objects
            )
        }
    )
    evidence = {
        "status": "pass",
        "robot_initial_collision_count": 0,
        "objects": {},
    }
    for item in attacked.objects:
        nested = item.object_id == "can_1"
        evidence["objects"][item.object_id] = {
            "translation_drift_m": 0.0,
            "rotation_drift_deg": 0.0,
            "resolved_translation_error_m": 0.0,
            "resolved_rotation_error_deg": 0.0,
            "penetration_count": 0,
            "still_moving": False,
            "support_contact": False if nested else True,
            "support_contact_fraction": 0.0 if nested else 1.0,
            "unexpected_contact_fraction": 0.0,
            "unexpected_contact_targets": [],
            "support_mode": "fixed_static_pose" if nested else "on_table_contact",
            "support_target": None if nested else "table",
            "support_footprint_margin_m": 0.008 if nested else None,
            "inside_contained": None,
            "dropped": False,
            "visible_pixels": 512,
        }
    report = validate_resolved_scene(attacked, runtime_evidence=evidence, require_runtime=True)
    support = next(item for item in report["checks"] if item["name"] == "support_contact:can_1")
    assert support["status"] == "fail"


def test_runtime_validator_rejects_intermittent_nested_contact() -> None:
    _, _, resolved = stacked_case()
    evidence = {
        "status": "pass",
        "robot_initial_collision_count": 0,
        "objects": {},
    }
    for item in resolved.objects:
        nested = item.object_id == "can_1"
        evidence["objects"][item.object_id] = {
            "translation_drift_m": 0.0,
            "rotation_drift_deg": 0.0,
            "resolved_translation_error_m": 0.0,
            "resolved_rotation_error_deg": 0.0,
            "penetration_count": 0,
            "still_moving": False,
            "support_contact": True,
            "support_contact_fraction": 0.25 if nested else 1.0,
            "unexpected_contact_fraction": 0.0,
            "unexpected_contact_targets": [],
            "support_mode": "on_top_of_contact" if nested else "on_table_contact",
            "support_target": "plate_1" if nested else "table",
            "support_footprint_margin_m": 0.008 if nested else None,
            "inside_contained": None,
            "dropped": False,
            "visible_pixels": 512,
        }
    report = validate_resolved_scene(resolved, runtime_evidence=evidence, require_runtime=True)
    support = next(item for item in report["checks"] if item["name"] == "support_contact:can_1")
    assert support["status"] == "fail"


def test_runtime_validator_rejects_nested_source_contacting_table() -> None:
    _, _, resolved = stacked_case()
    evidence = {
        "status": "pass",
        "robot_initial_collision_count": 0,
        "objects": {},
    }
    for item in resolved.objects:
        nested = item.object_id == "can_1"
        evidence["objects"][item.object_id] = {
            "translation_drift_m": 0.0,
            "rotation_drift_deg": 0.0,
            "resolved_translation_error_m": 0.0,
            "resolved_rotation_error_deg": 0.0,
            "penetration_count": 0,
            "still_moving": False,
            "support_contact": True,
            "support_contact_fraction": 1.0,
            "unexpected_contact_fraction": 1.0 if nested else 0.0,
            "unexpected_contact_targets": ["table"] if nested else [],
            "support_mode": "on_top_of_contact" if nested else "on_table_contact",
            "support_target": "plate_1" if nested else "table",
            "support_footprint_margin_m": 0.008 if nested else None,
            "inside_contained": None,
            "dropped": False,
            "visible_pixels": 512,
        }
    report = validate_resolved_scene(resolved, runtime_evidence=evidence, require_runtime=True)
    check = next(
        item
        for item in report["checks"]
        if item["name"] == "no_unexpected_support_contact:can_1"
    )
    assert check["status"] == "fail"
