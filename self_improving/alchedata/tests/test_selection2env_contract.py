from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.selection2env_contract import validate_scene_task_pair, validate_task_program_references


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def task_program(task_id: str, placement: Path, digest: str, binding: dict) -> dict:
    verifier = {
        "type": "simulator_state_and_visual",
        "relation": "in_region" if binding["template"] == "place_in_region" else "on",
        "source_object_id": binding["source_object_id"],
        "success_conditions": ["a", "b", "c"],
    }
    if "target_object_id" in binding:
        verifier["target_object_id"] = binding["target_object_id"]
    else:
        verifier["target_region"] = binding["target_region"]
    return {
        "task_id": task_id,
        "scene_id": "shared_scene",
        "placement_spec": str(placement),
        "placement_sha256": digest,
        "task_binding": binding,
        "verifier": verifier,
    }


def test_same_scene_pair_requires_real_shared_placement(tmp_path: Path) -> None:
    placement = tmp_path / "placement.json"
    write_json(
        placement,
        {
            "objects": [{"id": "apple"}, {"id": "plate"}],
            "workspace": {"spatial_regions": {"center": {"x": [-0.1, 0.1], "y": [-0.1, 0.1]}}},
        },
    )
    digest = hashlib.sha256(placement.read_bytes()).hexdigest()
    primary = task_program(
        "apple_on_plate",
        placement,
        digest,
        {"template": "place_on", "source_object_id": "apple", "target_object_id": "plate"},
    )
    alternate = task_program(
        "apple_to_center",
        placement,
        digest,
        {"template": "place_in_region", "source_object_id": "apple", "target_region": "center"},
    )

    report = validate_scene_task_pair(primary, alternate, root=tmp_path)

    assert report["status"] == "pass"
    assert report["placement_sha256"] == digest
    assert all(check["status"] == "pass" for check in report["checks"])


def test_missing_placement_fails_reference_validation(tmp_path: Path) -> None:
    program = {
        "placement_spec": "missing.json",
        "placement_sha256": "0" * 64,
        "task_binding": {
            "template": "place_on",
            "source_object_id": "apple",
            "target_object_id": "plate",
        },
        "verifier": {"source_object_id": "apple"},
    }

    report = validate_task_program_references(program, root=tmp_path)

    assert report["status"] == "fail"
    assert report["checks"][0]["name"] == "placement_spec_exists"


def test_verifier_must_match_binding_relation_and_target(tmp_path: Path) -> None:
    placement = tmp_path / "placement.json"
    write_json(
        placement,
        {
            "objects": [{"id": "apple"}, {"id": "plate"}],
            "workspace": {"spatial_regions": {}},
        },
    )
    digest = hashlib.sha256(placement.read_bytes()).hexdigest()
    program = task_program(
        "apple_on_plate",
        placement,
        digest,
        {"template": "place_on", "source_object_id": "apple", "target_object_id": "plate"},
    )
    program["verifier"]["relation"] = "in"
    program["verifier"]["target_object_id"] = "apple"

    report = validate_task_program_references(program, root=tmp_path)

    checks = {check["name"]: check["status"] for check in report["checks"]}
    assert report["status"] == "fail"
    assert checks["verifier_relation_matches_binding"] == "fail"
    assert checks["verifier_target_matches_binding"] == "fail"
