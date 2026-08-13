#!/usr/bin/env python3
"""Execute an Isaac Sim /gen-env -> /collect -> /evaluate -> /diagnose -> /transfer bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import shutil
import socket
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "artifacts/openxsim_cross_sim/place_container_plate_task_contract.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_evidence(path: Path, root: Path | None = None) -> dict[str, Any]:
    resolved = path.resolve()
    relative = resolved
    if root is not None:
        try:
            relative = resolved.relative_to(root.resolve())
        except ValueError:
            pass
    return {
        "path": relative.as_posix(),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def relation_metrics(
    source_position: list[float],
    target_position: list[float],
    source_size: list[float],
    target_size: list[float],
    source_speed_mps: float,
    verifier: dict[str, float],
) -> dict[str, Any]:
    horizontal_distance = math.dist(source_position[:2], target_position[:2])
    source_bottom = source_position[2] - source_size[2] / 2.0
    target_top = target_position[2] + target_size[2] / 2.0
    vertical_gap = abs(source_bottom - target_top)
    checks = {
        "horizontal_center_distance": horizontal_distance <= verifier["horizontal_center_distance_max_m"],
        "vertical_support_gap": vertical_gap <= verifier["source_bottom_to_target_top_abs_max_m"],
        "source_speed": source_speed_mps <= verifier["source_speed_max_mps"],
    }
    return {
        "horizontal_center_distance_m": horizontal_distance,
        "source_bottom_z_m": source_bottom,
        "target_top_z_m": target_top,
        "source_bottom_to_target_top_abs_m": vertical_gap,
        "source_speed_mps": source_speed_mps,
        "checks": checks,
        "success": all(checks.values()),
    }


def build_transfer_record(
    contract_path: Path,
    contract: dict[str, Any],
    source_report_path: Path,
    target_evaluate_path: Path,
    target_success: bool,
) -> dict[str, Any]:
    return {
        "schema_version": "alchedata.openxsim.isaac_transfer.v0",
        "command": "/transfer",
        "status": "pass_task_semantic_transfer_with_declared_losses" if target_success else "fail_target_verifier",
        "task_id": contract["task_id"],
        "same_normalized_task_contract": True,
        "task_contract": file_evidence(contract_path, ROOT),
        "source_backend": {
            "adapter": contract["source_backend"]["adapter"],
            "execution_type": contract["source_backend"]["execution_type"],
            "report": file_evidence(source_report_path, ROOT),
        },
        "target_backend": {
            "adapter": contract["target_backend"]["adapter"],
            "execution_type": contract["target_backend"]["execution_type"],
            "evaluate_report": file_evidence(target_evaluate_path, target_evaluate_path.parent),
            "target_verifier_success": target_success,
        },
        "mappings": [
            {"field": "task relation", "source": "place_on(container, plate)", "target": "place_on(container_proxy, plate_proxy)", "fidelity": "exact_relation"},
            {"field": "units and up axis", "source": "meters, Z-up", "target": "meters, Z-up", "fidelity": "exact"},
            {"field": "container asset", "source": "RoboTwin rigid object", "target": "Isaac DynamicCuboid", "fidelity": "primitive_proxy"},
            {"field": "plate asset", "source": "RoboTwin plate", "target": "Isaac FixedCuboid", "fidelity": "primitive_proxy"},
            {"field": "success verifier", "source": "RoboTwin task check_success", "target": "geometric support and speed checks", "fidelity": "relation_equivalent_not_code_identical"},
            {"field": "action interface", "source": "dual-arm scripted robot expert", "target": "scripted object-space trajectory", "fidelity": "not_transferred"},
            {"field": "materials", "source": "RoboTwin task materials", "target": "constant primitive colors", "fidelity": "not_transferred"},
            {"field": "robot embodiment", "source": "dual-arm robot", "target": "none", "fidelity": "not_transferred"}
        ],
        "declared_losses": [
            "asset geometry is represented by primitive proxies",
            "the robot embodiment and joint-action policy are not transferred",
            "materials and lighting are not matched",
            "verifier semantics are matched at relation level but implementation code differs"
        ],
        "claim_boundary": "This is an executed task-semantic transfer with a target verifier, not policy transfer, asset identity, visual parity, or evidence that transfer improves learned-policy reuse."
    }


def write_bundle_manifest(out_dir: Path) -> dict[str, Any]:
    rows = [
        file_evidence(path, out_dir)
        for path in sorted(out_dir.rglob("*"))
        if path.is_file() and path.name != "bundle_manifest.json"
    ]
    manifest = {
        "schema_version": "alchedata.openxsim.isaac_command_bundle_manifest.v0",
        "status": "pass_isaac_command_bundle",
        "file_count": len(rows),
        "files": rows,
    }
    write_json(out_dir / "bundle_manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--agenticsim-runtime", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--trajectory-steps", type=int, default=72)
    parser.add_argument("--settle-steps", type=int, default=48)
    parser.add_argument("--video-frames", type=int, default=24)
    parser.add_argument("--video-fps", type=float, default=12.0)
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=540)
    args = parser.parse_args()

    contract_path = args.task_contract.expanduser().resolve()
    runtime = args.agenticsim_runtime.expanduser().resolve()
    out_dir = args.out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    contract = read_json(contract_path)
    source_report_path = ROOT / contract["source_backend"]["evidence"]
    source_report = read_json(source_report_path)
    if source_report.get("check_success") is not True:
        raise AssertionError("Source RoboTwin verifier did not pass")

    os.environ.setdefault("OMNI_KIT_ACCEPT_EULA", "YES")
    nvidia_icd = Path("/usr/share/vulkan/icd.d/nvidia_icd.json")
    if nvidia_icd.is_file():
        os.environ.setdefault("VK_ICD_FILENAMES", str(nvidia_icd))
    sys.path.insert(0, str(runtime / "scripts"))
    from _isaac_gui import capture_active_viewport, encode_png_sequence_to_mp4
    from isaacsim import SimulationApp

    app = SimulationApp(
        {
            "headless": True,
            "hide_ui": True,
            "renderer": "RaytracedLighting",
            "width": max(args.width, 64),
            "height": max(args.height, 64),
            "fast_shutdown": True,
            "active_gpu": 0,
            "physics_gpu": 0,
            "multi_gpu": False,
            "max_gpu_count": 1,
        }
    )
    events: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []
    run_state: dict[str, Any] = {
        "schema_version": "alchedata.pearl_run_state.v0",
        "state": "started",
        "task_id": contract["task_id"],
        "backend": "Isaac Sim 5.1",
        "started_at": utc_now(),
    }
    write_json(out_dir / "run_state.json", run_state)
    try:
        import numpy as np
        import omni.timeline
        from isaacsim.core.api import World
        from isaacsim.core.api.objects import DynamicCuboid, FixedCuboid
        from isaacsim.core.utils.viewports import set_camera_view
        from pxr import Gf, UsdLux

        world = World(stage_units_in_meters=1.0)
        world.scene.add_default_ground_plane()
        source_size = [0.20, 0.20, 0.20]
        target_size = [0.65, 0.65, 0.08]
        source_start = [-0.65, -0.15, 0.11]
        target_position = [0.10, 0.0, 0.04]
        source_goal = [target_position[0], target_position[1], 0.19]
        target = world.scene.add(
            FixedCuboid(
                prim_path="/World/PlateProxy",
                name="plate_proxy",
                position=np.asarray(target_position),
                scale=np.asarray(target_size),
                color=np.asarray([0.88, 0.88, 0.92]),
            )
        )
        source = world.scene.add(
            DynamicCuboid(
                prim_path="/World/ContainerProxy",
                name="container_proxy",
                position=np.asarray(source_start),
                scale=np.asarray(source_size),
                color=np.asarray([0.10, 0.45, 0.90]),
                mass=0.5,
            )
        )
        dome = UsdLux.DomeLight.Define(world.stage, "/World/OpenXSimDome")
        dome.CreateIntensityAttr(550.0)
        dome.CreateColorAttr(Gf.Vec3f(1.0, 0.97, 0.92))
        key = UsdLux.DistantLight.Define(world.stage, "/World/OpenXSimKey")
        key.CreateIntensityAttr(2400.0)
        key.CreateAngleAttr(0.6)
        set_camera_view([2.5, 2.4, 1.7], [0.0, 0.0, 0.22], camera_prim_path="/OmniverseKit_Persp")
        world.reset()
        for _ in range(8):
            world.step(render=True)

        stage_path = out_dir / "scene.usda"
        world.stage.GetRootLayer().Export(str(stage_path))
        gen_env = {
            "schema_version": "alchedata.openxsim.isaac_gen_env.v0",
            "command": "/gen-env",
            "status": "pass_isaac_gen_env",
            "task_id": contract["task_id"],
            "task_contract": file_evidence(contract_path, ROOT),
            "adapter": "Isaac Sim 5.1",
            "scene_stage": file_evidence(stage_path, out_dir),
            "asset_bindings": contract["target_backend"]["asset_binding"],
            "placement": {
                "container_start_m": source_start,
                "container_goal_m": source_goal,
                "plate_position_m": target_position,
                "container_size_m": source_size,
                "plate_size_m": target_size,
            },
            "gates": {"stage_export": True, "reset": True, "physics": True, "renderer": True},
            "claim_boundary": "The target scene uses Isaac primitives and proves executable scene construction, not source-asset identity or material parity."
        }
        write_json(out_dir / "gen_env.json", gen_env)
        events.append({"at": utc_now(), "command": "/gen-env", "status": gen_env["status"]})

        frame_dir = out_dir / "frames"
        if frame_dir.exists():
            shutil.rmtree(frame_dir)
        frame_dir.mkdir(parents=True)
        timeline = omni.timeline.get_timeline_interface()
        total_steps = args.trajectory_steps + args.settle_steps
        sample_steps = {
            round(index * total_steps / (max(args.video_frames, 2) - 1))
            for index in range(max(args.video_frames, 2))
        }
        frame_paths: list[Path] = []
        frame_records: list[dict[str, Any]] = []

        def capture(step: int) -> None:
            frame_path = frame_dir / f"frame_{len(frame_paths):05d}.png"
            position = [float(value) for value in source.get_world_pose()[0]]
            was_playing = timeline.is_playing()
            timeline.pause()
            try:
                app.run_coroutine(
                    capture_active_viewport(
                        frame_path,
                        warmup_frames=8 if not frame_paths else 0,
                        completion_frames=4 if not frame_paths else 2,
                        timeout_s=120,
                        prefer_new_viewport=True,
                        width=args.width,
                        height=args.height,
                        camera_eye=(2.5, 2.4, 1.7),
                        camera_lookat=(0.0, 0.0, 0.22),
                    )
                )
            finally:
                if was_playing:
                    timeline.play()
            frame_paths.append(frame_path)
            frame_records.append({"frame_index": len(frame_paths) - 1, "simulation_step": step, "source_position_m": position})

        if 0 in sample_steps:
            capture(0)
        for step in range(1, total_steps + 1):
            if step <= args.trajectory_steps:
                fraction = step / args.trajectory_steps
                position = [
                    source_start[0] + (source_goal[0] - source_start[0]) * fraction,
                    source_start[1] + (source_goal[1] - source_start[1]) * fraction,
                    source_start[2] + (source_goal[2] - source_start[2]) * fraction + 0.35 * math.sin(math.pi * fraction),
                ]
                source.set_world_pose(position=np.asarray(position, dtype=np.float32))
                source.set_linear_velocity(np.zeros(3, dtype=np.float32))
                action = {"type": "set_object_pose", "position_m": position}
                phase = "scripted_transfer"
            else:
                action = {"type": "physics_settle"}
                phase = "settle"
            world.step(render=True)
            position = [float(value) for value in source.get_world_pose()[0]]
            velocity = [float(value) for value in source.get_linear_velocity()]
            traces.append({
                "step": step,
                "phase": phase,
                "action": action,
                "source_position_m": position,
                "source_linear_velocity_mps": velocity,
                "target_position_m": [float(value) for value in target.get_world_pose()[0]],
            })
            if step in sample_steps:
                capture(step)

        trace_path = out_dir / "state_action_trace.jsonl"
        write_jsonl(trace_path, traces)
        video_path = out_dir / "isaac_place_on_rollout.mp4"
        encode = encode_png_sequence_to_mp4(frame_dir, video_path, fps=max(args.video_fps, 1.0))
        initial_image = frame_paths[0]
        final_image = frame_paths[-1]
        source_position = [float(value) for value in source.get_world_pose()[0]]
        target_pose = [float(value) for value in target.get_world_pose()[0]]
        source_speed = float(np.linalg.norm(source.get_linear_velocity()))
        metrics = relation_metrics(
            source_position,
            target_pose,
            source_size,
            target_size,
            source_speed,
            contract["relation"]["success_verifier"],
        )
        frame_hashes = [sha256_file(path) for path in frame_paths]
        collect = {
            "schema_version": "alchedata.openxsim.isaac_collect.v0",
            "command": "/collect",
            "status": "pass_isaac_scripted_collection" if encode.get("ready") else "fail_video_encode",
            "task_id": contract["task_id"],
            "execution_type": "scripted_object_space_expert",
            "learned_policy": False,
            "step_count": len(traces),
            "trajectory_steps": args.trajectory_steps,
            "settle_steps": args.settle_steps,
            "trace": file_evidence(trace_path, out_dir),
            "video": file_evidence(video_path, out_dir),
            "initial_image": file_evidence(initial_image, out_dir),
            "final_image": file_evidence(final_image, out_dir),
            "video_evidence": {
                "frame_count": len(frame_paths),
                "unique_frame_sha256_count": len(set(frame_hashes)),
                "endpoint_only": False,
                "captures": frame_records,
                "encode": encode,
            },
            "claim_boundary": "This is a backend-native Isaac collection with a scripted object-space expert, not robot control or learned-policy evidence."
        }
        write_json(out_dir / "collect.json", collect)
        events.append({"at": utc_now(), "command": "/collect", "status": collect["status"]})

        evaluate = {
            "schema_version": "alchedata.openxsim.isaac_evaluate.v0",
            "command": "/evaluate",
            "status": "pass_isaac_task_verifier" if metrics["success"] else "fail_isaac_task_verifier",
            "task_id": contract["task_id"],
            "learned_policy": False,
            "execution_complete": True,
            "task_success": metrics["success"],
            "verifier": contract["relation"]["success_verifier"],
            "metrics": metrics,
            "claim_boundary": "The result verifies the normalized place_on relation for this scripted Isaac execution; it is not learned-policy quality."
        }
        evaluate_path = out_dir / "evaluate.json"
        write_json(evaluate_path, evaluate)
        events.append({"at": utc_now(), "command": "/evaluate", "status": evaluate["status"]})

        diagnose = {
            "schema_version": "alchedata.openxsim.isaac_diagnose.v0",
            "command": "/diagnose",
            "status": "no_failure_observed" if metrics["success"] else "task_relation_failure",
            "task_id": contract["task_id"],
            "trace": file_evidence(trace_path, out_dir),
            "categories": {
                "wrong_grasp_location": {"status": "not_applicable", "reason": "No gripper is present in the target execution."},
                "object_knocked_over": {"status": "not_observed", "evidence": {"final_position_m": source_position}},
                "arm_jitter": {"status": "not_applicable", "reason": "No robot arm is present in the target execution."},
                "uncontrolled_gripper_open_close": {"status": "not_applicable", "reason": "No gripper is present in the target execution."},
                "after_contact_failure": {"status": "not_observed" if metrics["success"] else "observed", "evidence": metrics},
                "visual_material_mismatch": {"status": "known_transfer_loss", "evidence": "Primitive colors replace source materials."}
            },
            "root_cause_hypothesis": None if metrics["success"] else "The final place_on relation failed a geometric or speed threshold.",
            "claim_boundary": "Not-applicable categories are retained rather than reported as absent failure modes."
        }
        write_json(out_dir / "diagnose.json", diagnose)
        events.append({"at": utc_now(), "command": "/diagnose", "status": diagnose["status"]})

        transfer = build_transfer_record(contract_path, contract, source_report_path, evaluate_path, metrics["success"])
        write_json(out_dir / "transfer.json", transfer)
        events.append({"at": utc_now(), "command": "/transfer", "status": transfer["status"]})
        write_jsonl(out_dir / "events.jsonl", events)

        command_loop = {
            "/gen-env": gen_env["status"],
            "/collect": collect["status"],
            "/evaluate": evaluate["status"],
            "/diagnose": diagnose["status"],
            "/transfer": transfer["status"],
        }
        passed = all(status.startswith("pass_") or status == "no_failure_observed" for status in command_loop.values())
        run_state.update({
            "state": "completed" if passed else "failed",
            "finished_at": utc_now(),
            "commands": command_loop,
            "task_success": metrics["success"],
            "same_task_transfer": transfer["same_normalized_task_contract"],
            "runtime": {
                "hostname": socket.gethostname(),
                "platform": platform.platform(),
                "python": platform.python_version(),
            },
        })
        write_json(out_dir / "run_state.json", run_state)
        manifest = write_bundle_manifest(out_dir)
        summary = {
            "status": manifest["status"] if passed else "fail_isaac_command_bundle",
            "task_success": metrics["success"],
            "command_loop": command_loop,
            "file_count": manifest["file_count"],
            "out_dir": str(out_dir),
        }
    except Exception as exc:
        run_state.update({
            "state": "failed",
            "finished_at": utc_now(),
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        })
        write_json(out_dir / "run_state.json", run_state)
        summary = {"status": "fail_isaac_command_bundle", "error": run_state["error"], "out_dir": str(out_dir)}
    finally:
        app.close()

    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["status"] == "pass_isaac_command_bundle" else 1


if __name__ == "__main__":
    raise SystemExit(main())
