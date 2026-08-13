#!/usr/bin/env python3
"""Run SAPIEN import, settling, contact, and render checks for AssetScout outputs."""

from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
import sapien


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 1e-12:
        raise ValueError("camera vector cannot be zero")
    return [value / norm for value in vector]


def cross(first: list[float], second: list[float]) -> list[float]:
    return [
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    ]


def camera_pose(position: list[float], target: list[float]) -> sapien.Pose:
    forward = normalize([target[index] - position[index] for index in range(3)])
    left = normalize(cross([0.0, 0.0, 1.0], forward))
    camera_up = normalize(cross(forward, left))
    matrix = [
        [forward[0], left[0], camera_up[0]],
        [forward[1], left[1], camera_up[1]],
        [forward[2], left[2], camera_up[2]],
    ]
    trace = matrix[0][0] + matrix[1][1] + matrix[2][2]
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        quaternion = [0.25 * scale, (matrix[2][1] - matrix[1][2]) / scale, (matrix[0][2] - matrix[2][0]) / scale, (matrix[1][0] - matrix[0][1]) / scale]
    elif matrix[0][0] > matrix[1][1] and matrix[0][0] > matrix[2][2]:
        scale = math.sqrt(1.0 + matrix[0][0] - matrix[1][1] - matrix[2][2]) * 2.0
        quaternion = [(matrix[2][1] - matrix[1][2]) / scale, 0.25 * scale, (matrix[0][1] + matrix[1][0]) / scale, (matrix[0][2] + matrix[2][0]) / scale]
    elif matrix[1][1] > matrix[2][2]:
        scale = math.sqrt(1.0 + matrix[1][1] - matrix[0][0] - matrix[2][2]) * 2.0
        quaternion = [(matrix[0][2] - matrix[2][0]) / scale, (matrix[0][1] + matrix[1][0]) / scale, 0.25 * scale, (matrix[1][2] + matrix[2][1]) / scale]
    else:
        scale = math.sqrt(1.0 + matrix[2][2] - matrix[0][0] - matrix[1][1]) * 2.0
        quaternion = [(matrix[1][0] - matrix[0][1]) / scale, (matrix[0][2] + matrix[2][0]) / scale, (matrix[1][2] + matrix[2][1]) / scale, 0.25 * scale]
    return sapien.Pose(position, quaternion)


def body_name(body: Any) -> str | None:
    name = getattr(body, "name", None)
    if not name:
        getter = getattr(body, "get_name", None)
        name = getter() if callable(getter) else None
    if not name:
        name = getattr(getattr(body, "entity", None), "name", None)
    return str(name) if name else None


def select_representation(record: dict[str, Any], *, role: str, format_name: str | None = None) -> dict[str, Any]:
    for representation in record.get("representations") or []:
        if representation.get("role") != role:
            continue
        if format_name is not None and representation.get("format") != format_name:
            continue
        return representation
    raise KeyError(f"missing {role} representation")


def run_asset(record: dict[str, Any], assets_root: Path, output: Path, *, steps: int) -> dict[str, Any]:
    asset_id = str(record["asset_id"])
    asset_dir = assets_root / "compiled" / asset_id
    visual_representation = select_representation(record, role="visual", format_name="obj")
    collision_representation = select_representation(record, role="collision")
    visual_path = asset_dir / Path(str(visual_representation["uri"])).name
    collision_path = asset_dir / Path(str(collision_representation["uri"])).name
    if not visual_path.is_file() or not collision_path.is_file():
        raise FileNotFoundError(f"missing transferred visual/collision files for {asset_id}")

    normalization = dict((visual_representation.get("metadata") or {}).get("normalization") or {})
    bounds = dict(normalization.get("normalized_bounds") or {})
    minimum = [float(value) for value in bounds.get("minimum") or [-0.5, -0.5, -0.5]]
    maximum = [float(value) for value in bounds.get("maximum") or [0.5, 0.5, 0.5]]
    initial_z = -minimum[2] + 0.08

    engine = sapien.Engine()
    renderer = sapien.SapienRenderer()
    engine.set_renderer(renderer)
    scene = engine.create_scene()
    scene.set_timestep(0.01)
    scene.add_ground(0.0)
    scene.set_ambient_light([0.35, 0.35, 0.35])
    scene.add_directional_light([0.2, 0.3, -1.0], [1.2, 1.2, 1.2], shadow=False)

    builder = scene.create_actor_builder()
    builder.add_visual_from_file(str(visual_path))
    builder.add_multiple_convex_collisions_from_file(str(collision_path))
    actor = builder.build(name=asset_id)
    actor.set_pose(sapien.Pose([0.0, 0.0, initial_z]))

    camera = scene.add_camera(f"{asset_id}_camera", 512, 512, math.radians(55.0), 0.01, 10.0)
    extent = max(maximum[index] - minimum[index] for index in range(3))
    distance = max(1.5, extent * 2.6)
    camera.set_pose(camera_pose([distance, -distance, max(1.1, initial_z + extent)], [0.0, 0.0, max(0.2, initial_z * 0.6)]))

    trajectory: list[list[float]] = [[float(value) for value in actor.get_pose().p]]
    contacts: list[list[str]] = []
    for _ in range(steps):
        scene.step()
        trajectory.append([float(value) for value in actor.get_pose().p])
        current_contacts: list[str] = []
        for contact in scene.get_contacts():
            names = sorted(filter(None, (body_name(body) for body in contact.bodies)))
            if names:
                current_contacts.append(":".join(names))
        contacts.append(sorted(set(current_contacts)))

    scene.update_render()
    camera.take_picture()
    color = camera.get_picture("Color")[..., :3]
    pixels = (np.clip(color, 0.0, 1.0) * 255).astype(np.uint8)
    preview_path = output / asset_id / "sapien_import.png"
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(pixels).save(preview_path)

    tail = trajectory[-min(20, len(trajectory)) :]
    tail_motion = max(math.dist(left, right) for left, right in zip(tail, tail[1:])) if len(tail) > 1 else 0.0
    ground_contact = any(
        any("ground" in contact and asset_id in contact for contact in step_contacts)
        for step_contacts in contacts[-min(20, len(contacts)) :]
    )
    preview_std = float(pixels.std())
    final_position = trajectory[-1]
    finite = all(math.isfinite(value) for pose in trajectory for value in pose)
    passed = finite and final_position[2] >= -0.05 and ground_contact and tail_motion <= 0.005 and preview_std >= 3.0
    return {
        "asset_id": asset_id,
        "category": record.get("category"),
        "status": "pass" if passed else "fail",
        "visual_path": str(visual_path),
        "collision_path": str(collision_path),
        "initial_position_m": trajectory[0],
        "final_position_m": final_position,
        "tail_max_step_motion_m": tail_motion,
        "tail_motion_threshold_m": 0.005,
        "ground_contact_last_20_steps": ground_contact,
        "positions_finite": finite,
        "preview_path": str(preview_path),
        "preview_pixel_std": preview_std,
        "preview_nonempty_threshold": 3.0,
        "steps": steps,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets-root", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--steps", type=int, default=120)
    args = parser.parse_args()
    assets_root = Path(args.assets_root).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    if output.exists():
        shutil.rmtree(output)
    acceptance = json.loads(Path(args.report).expanduser().resolve().read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []
    for source_record in acceptance.get("assets") or []:
        try:
            record = run_asset(source_record, assets_root, output, steps=args.steps)
        except Exception as exc:
            record = {
                "asset_id": source_record.get("asset_id"),
                "category": source_record.get("category"),
                "status": "fail",
                "failure": repr(exc),
            }
        records.append(record)
        write_json(output / "sapien_asset_acceptance.partial.json", {"assets": records, "complete": False})
        print(f"{record['status'].upper()} {record['asset_id']}", flush=True)
    pass_count = sum(record["status"] == "pass" for record in records)
    report = {
        "schema": "agenticsim.asset_scout_sapien_acceptance.v1",
        "status": "pass" if records and pass_count == len(records) else "fail",
        "sapien_version": getattr(sapien, "__version__", "unknown"),
        "asset_count": len(records),
        "pass_count": pass_count,
        "assets": records,
        "complete": True,
    }
    write_json(output / "sapien_asset_acceptance.json", report)
    print(f"{report['status'].upper()} pass={pass_count}/{len(records)}", flush=True)
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
