#!/usr/bin/env python3
"""Reference and binding checks for selection2env task-program inputs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def workspace_path(value: str, *, root: Path = ROOT) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_task_binding(
    task_program: dict[str, Any],
    placement: dict[str, Any],
) -> dict[str, Any]:
    binding = task_program.get("task_binding")
    if not isinstance(binding, dict):
        raise ValueError("task_binding must be an object")

    object_ids = {str(obj["id"]) for obj in placement.get("objects", [])}
    region_ids = set(placement.get("workspace", {}).get("spatial_regions", {}))
    template = str(binding.get("template", ""))
    source_id = str(binding.get("source_object_id", ""))
    if source_id not in object_ids:
        raise ValueError(f"task source_object_id is not in placement: {source_id}")

    normalized: dict[str, Any] = {
        "template": template,
        "source_id": source_id,
    }
    target_object_id = binding.get("target_object_id")
    target_region = binding.get("target_region")
    if target_object_id:
        target_id = str(target_object_id)
        if target_id not in object_ids:
            raise ValueError(f"task target_object_id is not in placement: {target_id}")
        normalized.update({"target_kind": "object", "target_id": target_id})
    elif target_region:
        region_id = str(target_region)
        if region_id not in region_ids:
            raise ValueError(f"task target_region is not in placement: {region_id}")
        normalized.update({"target_kind": "region", "target_region": region_id})
    else:
        raise ValueError("task binding needs target_object_id or target_region")

    if template not in {"place_on", "place_in", "place_in_region", "place_right_of"}:
        raise ValueError(f"unsupported task binding template: {template}")
    if template == "place_in_region" and normalized["target_kind"] != "region":
        raise ValueError("place_in_region requires target_region")
    if template != "place_in_region" and normalized["target_kind"] != "object":
        raise ValueError(f"{template} requires target_object_id")
    return normalized


def validate_task_program_references(
    task_program: dict[str, Any],
    *,
    root: Path = ROOT,
) -> dict[str, Any]:
    placement_path = workspace_path(str(task_program.get("placement_spec", "")), root=root)
    checks: list[dict[str, Any]] = []
    if not placement_path.exists():
        checks.append(
            {
                "name": "placement_spec_exists",
                "status": "fail",
                "message": str(placement_path),
            }
        )
        return {"status": "fail", "checks": checks, "placement_path": str(placement_path)}

    checks.append(
        {
            "name": "placement_spec_exists",
            "status": "pass",
            "message": str(placement_path),
        }
    )
    placement_sha256 = sha256_file(placement_path)
    declared_sha256 = task_program.get("placement_sha256")
    checks.append(
        {
            "name": "placement_sha256_matches",
            "status": "pass" if declared_sha256 == placement_sha256 else "fail",
            "message": f"declared={declared_sha256} actual={placement_sha256}",
        }
    )
    placement = read_json(placement_path)
    try:
        binding = normalize_task_binding(task_program, placement)
    except ValueError as exc:
        checks.append({"name": "task_binding_resolves", "status": "fail", "message": str(exc)})
        binding = None
    else:
        checks.append(
            {
                "name": "task_binding_resolves",
                "status": "pass",
                "message": json.dumps(binding, sort_keys=True),
            }
        )

    verifier = task_program.get("verifier", {})
    verifier_source = verifier.get("source_object_id")
    checks.append(
        {
            "name": "verifier_source_matches_binding",
            "status": (
                "pass"
                if binding and verifier_source == binding["source_id"]
                else "fail"
            ),
            "message": f"verifier_source={verifier_source}",
        }
    )
    fail_count = sum(check["status"] == "fail" for check in checks)
    return {
        "status": "pass" if fail_count == 0 else "fail",
        "fail_count": fail_count,
        "placement_path": str(placement_path),
        "placement_sha256": placement_sha256,
        "binding": binding,
        "checks": checks,
    }


def validate_scene_task_pair(
    primary: dict[str, Any],
    alternate: dict[str, Any],
    *,
    root: Path = ROOT,
) -> dict[str, Any]:
    primary_validation = validate_task_program_references(primary, root=root)
    alternate_validation = validate_task_program_references(alternate, root=root)
    checks = [
        {
            "name": "task_ids_are_distinct",
            "status": "pass" if primary.get("task_id") != alternate.get("task_id") else "fail",
            "message": f"{primary.get('task_id')} != {alternate.get('task_id')}",
        },
        {
            "name": "scene_id_is_shared",
            "status": "pass" if primary.get("scene_id") == alternate.get("scene_id") else "fail",
            "message": str(primary.get("scene_id")),
        },
        {
            "name": "placement_path_is_shared",
            "status": "pass" if primary.get("placement_spec") == alternate.get("placement_spec") else "fail",
            "message": str(primary.get("placement_spec")),
        },
        {
            "name": "placement_bytes_are_shared",
            "status": (
                "pass"
                if primary_validation.get("placement_sha256")
                == alternate_validation.get("placement_sha256")
                else "fail"
            ),
            "message": str(primary_validation.get("placement_sha256")),
        },
        {
            "name": "bindings_are_distinct",
            "status": (
                "pass"
                if primary_validation.get("binding") != alternate_validation.get("binding")
                else "fail"
            ),
            "message": (
                f"primary={primary_validation.get('binding')} "
                f"alternate={alternate_validation.get('binding')}"
            ),
        },
        {
            "name": "primary_references_resolve",
            "status": primary_validation["status"],
            "message": f"fail_count={primary_validation.get('fail_count', 1)}",
        },
        {
            "name": "alternate_references_resolve",
            "status": alternate_validation["status"],
            "message": f"fail_count={alternate_validation.get('fail_count', 1)}",
        },
    ]
    fail_count = sum(check["status"] == "fail" for check in checks)
    return {
        "status": "pass" if fail_count == 0 else "fail",
        "fail_count": fail_count,
        "scene_id": primary.get("scene_id"),
        "placement_spec": primary.get("placement_spec"),
        "placement_sha256": primary_validation.get("placement_sha256"),
        "checks": checks,
        "primary_validation": primary_validation,
        "alternate_validation": alternate_validation,
    }
