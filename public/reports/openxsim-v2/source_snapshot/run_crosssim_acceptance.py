#!/usr/bin/env python3
"""Validate rigid, articulated, and camera-sensitive MuJoCo-to-SAPIEN transfer."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from PIL import Image

from _bootstrap import bootstrap_repo_source

REPO_ROOT = bootstrap_repo_source()

from agenticsim.openxsim.backends import CompileResult, compile_package  # noqa: E402
from agenticsim.openxsim.conformance import evaluate_conformance  # noqa: E402
from agenticsim.openxsim.importers import import_environment  # noqa: E402
from agenticsim.openxsim.ir import (  # noqa: E402
    AssetBundle,
    AssetRepresentation,
    EnvironmentPackage,
    EnvSpec,
    Pose,
    SceneObject,
    TaskSpec,
)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def task(instruction: str) -> TaskSpec:
    return TaskSpec(
        instruction=instruction,
        intent="cross_sim_zero_action_replay",
        reset={"object_poses": "from_env_spec"},
        action={"interface": "zero_action", "operations": []},
        observation={"state": ["object_pose", "contact"]},
        plan=(),
        success=({"type": "settled"},),
        termination=({"type": "timeout", "steps": 120},),
    )


def primitive_asset(asset_id: str, color: tuple[float, float, float], half_size: tuple[float, float, float]) -> AssetBundle:
    return AssetBundle(
        asset_id=asset_id,
        category="box",
        representations=(
            AssetRepresentation(
                format="primitive_box",
                uri="primitive://box",
                metadata={"half_size_m": list(half_size), "color_rgb": list(color)},
            ),
        ),
        source={"kind": "procedural_common_subset"},
        physical={"mass_kg": 0.2},
    )


def build_packages() -> list[tuple[str, EnvironmentPackage]]:
    rigid_asset = primitive_asset("rigid_box_asset", (0.9, 0.15, 0.12), (0.05, 0.05, 0.05))
    rigid = EnvironmentPackage(
        package_id="crosssim_rigid_drop",
        env=EnvSpec(
            name="crosssim_rigid_drop",
            objects=(
                SceneObject(
                    instance_id="rigid_box",
                    asset_id=rigid_asset.asset_id,
                    pose=Pose(position=(0.0, 0.0, 0.35)),
                    static=False,
                ),
            ),
        ),
        assets=(rigid_asset,),
        task=task("Let the rigid box settle under gravity."),
        source={"mode": "existing_mujoco_environment_fixture", "case": "rigid"},
        target_backends=("mujoco", "sapien"),
    )

    articulation = {
        "schema": "agenticsim.articulation_tree.v1",
        "root_link": "cabinet_base",
        "links": [
            {
                "id": "cabinet_base",
                "pose": {"position": [0.0, 0.0, 0.0], "orientation_wxyz": [1.0, 0.0, 0.0, 0.0]},
                "geometry": {"type": "box", "half_size_m": [0.18, 0.08, 0.2], "color_rgb": [0.42, 0.48, 0.55]},
                "geometry_origin": {"position": [0.0, 0.0, 0.0], "orientation_wxyz": [1.0, 0.0, 0.0, 0.0]},
                "mass_kg": 1.0,
            },
            {
                "id": "door",
                "pose": {"position": [0.0, -0.095, 0.0], "orientation_wxyz": [1.0, 0.0, 0.0, 0.0]},
                "geometry": {"type": "box", "half_size_m": [0.18, 0.015, 0.2], "color_rgb": [0.82, 0.24, 0.18]},
                "geometry_origin": {"position": [0.0, 0.0, 0.0], "orientation_wxyz": [1.0, 0.0, 0.0, 0.0]},
                "mass_kg": 0.3,
            },
        ],
        "joints": [
            {
                "id": "door_hinge",
                "type": "revolute",
                "parent": "cabinet_base",
                "child": "door",
                "axis": [0.0, 0.0, 1.0],
                "range": [-1.2, 0.0],
                "damping": 0.1,
            }
        ],
        "movable_joint_count": 1,
        "source": "embedded_acceptance_fixture",
    }
    articulated_asset = AssetBundle(
        asset_id="hinged_cabinet_asset",
        category="articulated_cabinet",
        representations=(
            AssetRepresentation(format="articulation_tree", uri="embedded://hinged_cabinet", role="source"),
        ),
        source={"kind": "procedural_articulation_common_subset"},
        articulation=articulation,
        tags=("articulated", "one_dof"),
    )
    articulated = EnvironmentPackage(
        package_id="crosssim_articulated_hinge",
        env=EnvSpec(
            name="crosssim_articulated_hinge",
            objects=(
                SceneObject(
                    instance_id="cabinet",
                    asset_id=articulated_asset.asset_id,
                    pose=Pose(position=(0.0, 0.0, 0.2)),
                    static=True,
                ),
            ),
        ),
        assets=(articulated_asset,),
        task=task("Keep the hinged cabinet stable while preserving its door joint."),
        source={"mode": "existing_mujoco_environment_fixture", "case": "articulated"},
        target_backends=("mujoco", "sapien"),
    )

    red = primitive_asset("camera_red_asset", (0.92, 0.08, 0.07), (0.07, 0.07, 0.07))
    blue = primitive_asset("camera_blue_asset", (0.05, 0.24, 0.9), (0.08, 0.08, 0.08))
    camera = EnvironmentPackage(
        package_id="crosssim_camera_alignment",
        env=EnvSpec(
            name="crosssim_camera_alignment",
            objects=(
                SceneObject("red_marker", red.asset_id, Pose(position=(-0.18, 0.0, 0.07)), static=True),
                SceneObject("blue_marker", blue.asset_id, Pose(position=(0.18, 0.0, 0.08)), static=True),
            ),
            sensors=(
                {
                    "id": "inspection_camera",
                    "type": "camera",
                    "width": 640,
                    "height": 480,
                    "fov_y_deg": 55.0,
                    "near_m": 0.01,
                    "far_m": 10.0,
                    "position": [0.0, -1.0, 0.72],
                    "look_at": [0.0, 0.0, 0.08],
                    "up": [0.0, 0.0, 1.0],
                    "convention": "look_at_world_z_up",
                },
            ),
        ),
        assets=(red, blue),
        task=task("Observe the red and blue markers from the inspection camera."),
        source={"mode": "existing_mujoco_environment_fixture", "case": "camera_sensitive"},
        target_backends=("mujoco", "sapien"),
    )
    for package in (rigid, articulated, camera):
        package.validate()
    return [("rigid", rigid), ("articulated", articulated), ("camera_sensitive", camera)]


def run_runtime(result: CompileResult, *, steps: int, log_path: Path) -> dict[str, Any]:
    command = [sys.executable, *result.runtime_command[1:], "--steps", str(steps)]
    environment = os.environ.copy()
    environment.setdefault("MUJOCO_GL", "cgl")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(command, cwd=REPO_ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT, check=False)
    runtime_path = Path(result.manifest_path).parent / "runtime_evidence.json"
    if completed.returncode != 0 or not runtime_path.is_file():
        raise RuntimeError(f"runtime failed exit={completed.returncode}; see {log_path}")
    return json.loads(runtime_path.read_text(encoding="utf-8"))


def run_command(command: list[str], *, cwd: Path = REPO_ROOT) -> None:
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if completed.returncode:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}\n{completed.stdout}\n{completed.stderr}")


def image_metrics(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        pixels = list(rgb.getdata())
    red_points = [(index % width, index // width) for index, (r, g, b) in enumerate(pixels) if r > 120 and r > 1.4 * g and r > 1.4 * b]
    blue_points = [(index % width, index // width) for index, (r, g, b) in enumerate(pixels) if b > 110 and b > 1.3 * r and b > 1.3 * g]
    def centroid(points: list[tuple[int, int]]) -> list[float] | None:
        if not points:
            return None
        return [sum(item[0] for item in points) / len(points) / width, sum(item[1] for item in points) / len(points) / height]
    values = [channel for pixel in pixels for channel in pixel]
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return {
        "path": str(path),
        "width": width,
        "height": height,
        "pixel_std": math.sqrt(variance),
        "red_pixel_count": len(red_points),
        "blue_pixel_count": len(blue_points),
        "red_centroid_normalized": centroid(red_points),
        "blue_centroid_normalized": centroid(blue_points),
    }


def centroid_error(first: list[float] | None, second: list[float] | None) -> float | None:
    if first is None or second is None:
        return None
    return math.dist(first, second)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--remote-host", default="jingxiang@100.64.0.6")
    parser.add_argument("--remote-project", default="/home/jingxiang/workspace/AgenticSim-openxsim-acceptance")
    parser.add_argument("--remote-output", default="/home/jingxiang/workspace/openxsim-crosssim-acceptance")
    parser.add_argument("--remote-python", default="/home/jingxiang/miniconda3/envs/robotwin-5090/bin/python")
    parser.add_argument("--steps", type=int, default=120)
    args = parser.parse_args()
    output = Path(args.output).expanduser().resolve()
    prepared = output / "prepared_packages"
    prepared.mkdir(parents=True, exist_ok=True)

    cases: list[dict[str, Any]] = []
    local_results: dict[str, tuple[EnvironmentPackage, CompileResult, dict[str, Any]]] = {}
    for case_type, package in build_packages():
        scene_dir = output / "scenes" / package.package_id
        if scene_dir.exists():
            shutil.rmtree(scene_dir)
        package.write_json(scene_dir / "canonical_package.json")
        source_result = compile_package(package, scene_dir / "source", ("mujoco",), strict=True)["mujoco"]
        imported = import_environment(source_result.artifact_path, source_backend="mujoco")
        imported.write_json(prepared / f"{package.package_id}.json")
        target_result = compile_package(imported, scene_dir / "target", ("sapien",), strict=True)["sapien"]
        source_runtime = run_runtime(source_result, steps=args.steps, log_path=scene_dir / "source_runtime.log")
        local_results[package.package_id] = (imported, target_result, source_runtime)
        cases.append(
            {
                "case": case_type,
                "package_id": package.package_id,
                "canonical_digest": package.digest(),
                "imported_digest": imported.digest(),
                "source_compile": source_result.to_dict(),
                "target_compile": target_result.to_dict(),
                "source_runtime_path": str(Path(source_result.manifest_path).parent / "runtime_evidence.json"),
            }
        )

    remote_project = args.remote_project.rstrip("/")
    remote_output = args.remote_output.rstrip("/")
    run_command(
        [
            "ssh",
            args.remote_host,
            "mkdir",
            "-p",
            f"{remote_project}/source/agenticsim",
            f"{remote_project}/scripts",
            f"{remote_output}/prepared_packages",
            f"{remote_output}/targets",
        ]
    )
    run_command(
        [
            "rsync",
            "-az",
            "--delete",
            str(REPO_ROOT / "source/agenticsim") + "/",
            f"{args.remote_host}:{remote_project}/source/agenticsim/",
        ]
    )
    run_command(
        [
            "rsync",
            "-az",
            str(REPO_ROOT / "scripts/_bootstrap.py"),
            str(REPO_ROOT / "scripts/run_crosssim_sapien_targets.py"),
            f"{args.remote_host}:{remote_project}/scripts/",
        ]
    )
    run_command(["rsync", "-az", "--delete", str(prepared) + "/", f"{args.remote_host}:{remote_output}/prepared_packages/"])
    remote_command = (
        f"env PYTHONPATH={remote_project}/source/agenticsim {args.remote_python} "
        f"{remote_project}/scripts/run_crosssim_sapien_targets.py "
        f"--input-dir {remote_output}/prepared_packages --output-dir {remote_output}/targets --steps {args.steps}"
    )
    run_command(["ssh", args.remote_host, remote_command])
    pulled = output / "remote_targets"
    pulled.mkdir(parents=True, exist_ok=True)
    run_command(["rsync", "-az", f"{args.remote_host}:{remote_output}/targets/", str(pulled) + "/"])

    final_cases: list[dict[str, Any]] = []
    failure_gallery: list[dict[str, Any]] = []
    for record in cases:
        package_id = record["package_id"]
        imported, target_result, source_runtime = local_results[package_id]
        target_runtime_path = pulled / package_id / "compiled/sapien/runtime_evidence.json"
        target_runtime = json.loads(target_runtime_path.read_text(encoding="utf-8"))
        conformance = evaluate_conformance(
            imported,
            target_result,
            source_backend="mujoco",
            source_runtime=source_runtime,
            target_runtime=target_runtime,
            state_tolerance_m=0.04,
            contact_debounce_steps=5,
        )
        conformance_path = output / "scenes" / package_id / "conformance.json"
        conformance.write_json(conformance_path)
        record.update(
            {
                "target_runtime_path": str(target_runtime_path),
                "conformance": conformance.to_dict(),
                "conformance_path": str(conformance_path),
            }
        )
        if record["case"] == "articulated":
            source_joints = source_runtime.get("joint_trajectory") or []
            target_joints = target_runtime.get("joint_trajectory") or []
            names_match = bool(source_joints and target_joints) and set(source_joints[0].get("joints") or {}) == set(target_joints[0].get("joints") or {}) == {"door_hinge"}
            errors = []
            if names_match and len(source_joints) == len(target_joints):
                errors = [
                    abs(float(left["joints"]["door_hinge"]) - float(right["joints"]["door_hinge"]))
                    for left, right in zip(source_joints, target_joints)
                ]
            record["articulation_fidelity"] = {
                "joint_names_match": names_match,
                "trajectory_steps_match": len(source_joints) == len(target_joints),
                "max_joint_error_rad": max(errors) if errors else None,
                "threshold_rad": 0.05,
                "status": "pass" if errors and max(errors) <= 0.05 else "fail",
            }
        if record["case"] == "camera_sensitive":
            source_camera = source_runtime.get("camera_evidence") or []
            target_camera = target_runtime.get("camera_evidence") or []
            source_image = Path(source_camera[0]["rgb_path"]) if source_camera else Path("missing")
            target_image = target_runtime_path.parent / "inspection_camera_rgb.png"
            source_metrics = image_metrics(source_image)
            target_metrics = image_metrics(target_image)
            red_error = centroid_error(source_metrics["red_centroid_normalized"], target_metrics["red_centroid_normalized"])
            blue_error = centroid_error(source_metrics["blue_centroid_normalized"], target_metrics["blue_centroid_normalized"])
            passed = (
                source_metrics["red_pixel_count"] >= 20
                and source_metrics["blue_pixel_count"] >= 20
                and target_metrics["red_pixel_count"] >= 20
                and target_metrics["blue_pixel_count"] >= 20
                and red_error is not None
                and blue_error is not None
                and red_error <= 0.15
                and blue_error <= 0.15
                and source_metrics["width"] == target_metrics["width"] == 640
                and source_metrics["height"] == target_metrics["height"] == 480
            )
            record["camera_fidelity"] = {
                "status": "pass" if passed else "fail",
                "source": source_metrics,
                "target": target_metrics,
                "red_centroid_error_normalized": red_error,
                "blue_centroid_error_normalized": blue_error,
                "centroid_threshold_normalized": 0.15,
                "fov_y_deg_source": source_camera[0].get("fov_y_deg") if source_camera else None,
                "fov_y_deg_target": target_camera[0].get("fov_y_deg") if target_camera else None,
            }
        special_pass = record.get("articulation_fidelity", record.get("camera_fidelity", {"status": "pass"}))["status"] == "pass"
        record["status"] = "pass" if conformance.highest_consecutive_level == "L3" and special_pass else "fail"
        if record["status"] != "pass":
            failure_gallery.append(
                {
                    "package_id": package_id,
                    "case": record["case"],
                    "highest_level": conformance.highest_consecutive_level,
                    "failed_checks": [asdict(item) for item in conformance.checks if item.status == "fail"],
                    "special_fidelity": record.get("articulation_fidelity") or record.get("camera_fidelity"),
                }
            )
        final_cases.append(record)

    # MetaSim stays a compiler/import adapter until its declared runtime dependencies are installed.
    metasim_decision = {
        "decision": "adapter_only",
        "included_in_runtime_claim": False,
        "reason": "No MetaSim runtime dependency is installed on the acceptance host; static compilation is covered, runtime parity is not claimed.",
        "revisit_when": "MetaSim runtime and matching robot/controller assets are available in the same validation environment.",
    }
    pass_count = sum(item["status"] == "pass" for item in final_cases)
    status = "pass" if len(final_cases) == 3 and pass_count == 3 else "fail"
    report = {
        "schema": "agenticsim.crosssim_acceptance.v1",
        "status": status,
        "source_backend": "mujoco",
        "target_backend": "sapien",
        "case_count": len(final_cases),
        "pass_count": pass_count,
        "cases": final_cases,
        "failure_gallery": failure_gallery,
        "metasim_decision": metasim_decision,
        "complete": True,
    }
    write_json(output / "crosssim_acceptance.json", report)
    print(f"{status.upper()} pass={pass_count}/{len(final_cases)} report={output / 'crosssim_acceptance.json'}")
    return 0 if status == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
