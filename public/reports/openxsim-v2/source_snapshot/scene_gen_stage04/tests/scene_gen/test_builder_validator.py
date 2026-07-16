from __future__ import annotations

import ast
import json
from pathlib import Path

from scene_gen.builder import build_scene_package, verify_package
from scene_gen.catalog import load_catalog
from scene_gen.parser import parse_rule_based
from scene_gen.schema import ResolvedSceneSpec
from scene_gen.solver import solve_scene
from scene_gen.validator import validate_resolved_scene

ROOT = Path(__file__).resolve().parents[2]


def solved_case(seed: int = 23):
    catalog = load_catalog(ROOT / "data" / "scene_gen" / "asset_catalog.json")
    spec = parse_rule_based("A red can is left of a plastic basket near the center.", seed=seed)
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
                "penetration_count": 0,
                "still_moving": False,
                "support_contact": True,
                "support_mode": "table_contact",
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
            "penetration_count": 0,
            "still_moving": False,
            "support_contact": False,
            "support_mode": "fixed_static_pose" if item.is_static else "table_contact",
            "dropped": False,
            "visible_pixels": 512,
        }
    dynamic = next(item for item in resolved.objects if not item.is_static)
    evidence["objects"][dynamic.object_id]["support_contact"] = True
    report = validate_resolved_scene(resolved, runtime_evidence=evidence, require_runtime=True)
    assert report["status"] == "pass"

    evidence["objects"][dynamic.object_id]["support_contact"] = False
    evidence["objects"][dynamic.object_id]["support_mode"] = "fixed_static_pose"
    failed = validate_resolved_scene(resolved, runtime_evidence=evidence, require_runtime=True)
    assert any(
        item["name"] == f"support_contact:{dynamic.object_id}" and item["status"] == "fail"
        for item in failed["checks"]
    )
