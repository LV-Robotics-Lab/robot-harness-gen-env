#!/usr/bin/env python3
"""Collect multiple generated selection2env play_once rollout episodes."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from placement_manifest_utils import fixed_placement_cases, load_placement_cases


ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def rel(path: str | Path) -> str:
    p = Path(path)
    if p.is_absolute():
        try:
            return str(p.relative_to(ROOT))
        except ValueError:
            return str(p)
    return str(p)


def build_episode_record(
    index: int,
    case: dict[str, Any],
    episode_dir: Path,
    report: dict[str, Any],
    returncode: int,
) -> dict[str, Any]:
    native_data = dict(report.get("native_synchronized_data", {}))
    for key in ("hdf5", "video"):
        if native_data.get(key):
            native_data[key] = rel(native_data[key])
    return {
        "episode_index": index,
        "seed": case["seed"],
        "placement_id": case["placement_id"],
        "candidate_placement_id": case.get("candidate_placement_id"),
        "placement": rel(case["placement_path"]),
        "placement_split": case["split"],
        "pose_signature": case.get("pose_signature"),
        "pose_vector": case.get("pose_vector"),
        "status": report.get("status", "missing_report"),
        "returncode": returncode,
        "check_success": report.get("check_success"),
        "plan_success": report.get("plan_success"),
        "move_event_count": report.get("move_event_count", 0),
        "planned_joint_paths": report.get("left_joint_path_len", 0) + report.get("right_joint_path_len", 0),
        "relation_metrics": report.get("relation_metrics", {}),
        "report": rel(episode_dir / "rollout_report.json"),
        "events": rel(episode_dir / "events.jsonl"),
        "move_events": rel(episode_dir / "move_events.jsonl"),
        "policy_trace": rel(report["policy_trace"]) if report.get("policy_trace") else None,
        "observer_video": rel(episode_dir / "observer_rollout_probe.mp4"),
        "images": {key: rel(value) for key, value in report.get("images", {}).items()},
        "native_synchronized_data": native_data,
        "stdout": rel(episode_dir / "process_stdout.log"),
        "stderr": rel(episode_dir / "process_stderr.log"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generated selection2env multi-episode rollout collection.")
    parser.add_argument("--robotwin-root", required=True)
    placement_group = parser.add_mutually_exclusive_group(required=True)
    placement_group.add_argument("--placement")
    placement_group.add_argument("--placement-manifest")
    parser.add_argument("--placement-split", default="train")
    parser.add_argument(
        "--placement-id",
        action="append",
        help="Select a manifest placement ID. Repeat to collect a reproducible subset in the given order.",
    )
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--scene-module")
    parser.add_argument("--task-config", default="demo_clean")
    parser.add_argument("--episodes", type=int, default=3)
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--seed-list", help="Comma-separated seed list. Overrides --episodes and --seed-start.")
    parser.add_argument("--fps", type=int, default=12)
    parser.add_argument("--arm", choices=["auto", "left", "right"], default="auto")
    parser.add_argument("--motion-mode", choices=["auto", "direct-topdown"], default="auto")
    parser.add_argument("--grasp-z-offset", type=float, default=0.055)
    parser.add_argument("--place-z-offset", type=float, default=0.055)
    parser.add_argument("--record-native-data", action="store_true")
    parser.add_argument("--native-save-freq", type=int, default=15)
    parser.add_argument("--random-background", action="store_true")
    parser.add_argument("--cluttered-table", action="store_true")
    parser.add_argument("--random-light", action="store_true")
    parser.add_argument("--random-table-height", type=float, default=0.0)
    parser.add_argument("--random-head-camera-dis", type=float, default=0.0)
    parser.add_argument("--runner", default=str(ROOT / "scripts" / "run_generated_selection2env_rollout_probe.py"))
    args = parser.parse_args()

    if args.placement_id and not args.placement_manifest:
        parser.error("--placement-id requires --placement-manifest")

    seed_overrides = (
        [int(value.strip()) for value in args.seed_list.split(",") if value.strip()]
        if args.seed_list
        else None
    )
    placement_manifest = None
    if args.placement_manifest:
        placement_manifest, cases = load_placement_cases(
            Path(args.placement_manifest),
            args.placement_split,
            seed_overrides,
            args.placement_id,
        )
    else:
        seed_values = seed_overrides or [args.seed_start + index for index in range(args.episodes)]
        cases = fixed_placement_cases(Path(args.placement), seed_values)

    if len(cases) < 2:
        raise SystemExit("--episodes must be at least 2 for a collection gate")

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    runner = Path(args.runner).expanduser().resolve()
    events: list[dict[str, Any]] = []
    episodes: list[dict[str, Any]] = []
    started = time.time()

    base_report: dict[str, Any] = {
        "schema_version": "alchedata.generated_rollout_collection.v0",
        "command": "/collect",
        "probe_type": "generated_selection2env_multi_episode_play_once_collection",
        "task_id": args.task_id,
        "task_config": args.task_config,
        "placement": str(Path(args.placement).expanduser().resolve()) if args.placement else None,
        "placement_manifest": str(Path(args.placement_manifest).expanduser().resolve()) if args.placement_manifest else None,
        "placement_split": args.placement_split if args.placement_manifest else "fixed",
        "placement_id_filter": args.placement_id,
        "scene_module": str(Path(args.scene_module).expanduser().resolve()) if args.scene_module else None,
        "robotwin_root": str(Path(args.robotwin_root).expanduser().resolve()),
        "out_dir": str(out_dir),
        "episode_count_requested": len(cases),
        "seed_start": args.seed_start if not args.seed_list and not args.placement_manifest else None,
        "seed_list": [case["seed"] for case in cases],
        "placement_manifest_status": placement_manifest.get("status") if placement_manifest else None,
        "status": "started",
        "policy_execution": "generated_scripted_play_once",
        "learned_policy_training": "not_run",
        "learned_policy_evaluation": "not_run",
        "native_synchronized_recording": {
            "requested": args.record_native_data,
            "save_freq": args.native_save_freq,
        },
        "domain_randomization": {
            "random_background": args.random_background,
            "cluttered_table": args.cluttered_table,
            "random_light": args.random_light,
            "random_table_height": args.random_table_height,
            "random_head_camera_dis": args.random_head_camera_dis,
        },
        "limitations": [
            "This collection repeats generated selection2env play_once action-stack episodes.",
            "It is demonstration/evidence collection before policy training, not a learned policy dataset quality claim.",
            "It does not prove held-out robustness, randomization coverage, or /train-/evaluate completion.",
        ],
    }
    write_json(out_dir / "collection_report.json", base_report)
    events.append(
        {
            "event": "collection_start",
            "status": "started",
            "episodes": len(cases),
            "seed_list": [case["seed"] for case in cases],
            "placement_ids": [case["placement_id"] for case in cases],
        }
    )

    for index, case in enumerate(cases):
        seed = case["seed"]
        episode_dir = out_dir / f"episode_{index:03d}"
        episode_dir.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            str(runner),
            "--robotwin-root",
            args.robotwin_root,
            "--placement",
            str(case["placement_path"]),
            "--task-id",
            args.task_id,
            "--out-dir",
            str(episode_dir),
            "--task-config",
            args.task_config,
            "--seed",
            str(seed),
            "--fps",
            str(args.fps),
            "--arm",
            args.arm,
            "--motion-mode",
            args.motion_mode,
            "--grasp-z-offset",
            str(args.grasp_z_offset),
            "--place-z-offset",
            str(args.place_z_offset),
        ]
        if args.scene_module:
            command.extend(["--scene-module", args.scene_module])
        if args.record_native_data:
            command.extend(["--record-native-data", "--native-save-freq", str(args.native_save_freq)])
        if args.random_background:
            command.append("--random-background")
        if args.cluttered_table:
            command.append("--cluttered-table")
        if args.random_light:
            command.append("--random-light")
        if args.random_table_height:
            command.extend(["--random-table-height", str(args.random_table_height)])
        if args.random_head_camera_dis:
            command.extend(["--random-head-camera-dis", str(args.random_head_camera_dis)])

        ep_started = time.time()
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        (episode_dir / "process_stdout.log").write_text(result.stdout, encoding="utf-8")
        (episode_dir / "process_stderr.log").write_text(result.stderr, encoding="utf-8")
        report_path = episode_dir / "rollout_report.json"
        report = read_json(report_path) if report_path.exists() else {"status": "missing_report"}
        episode_record = build_episode_record(index, case, episode_dir, report, result.returncode)
        episode_record["duration_sec"] = round(time.time() - ep_started, 4)
        episodes.append(episode_record)
        events.append(
            {
                "event": "episode_complete",
                "episode_index": index,
                "seed": seed,
                "placement_id": case["placement_id"],
                "pose_signature": case.get("pose_signature"),
                "status": episode_record["status"],
                "returncode": result.returncode,
                "check_success": episode_record["check_success"],
                "duration_sec": episode_record["duration_sec"],
            }
        )

    def episode_passed(episode: dict[str, Any]) -> bool:
        action_passed = episode["status"] == "pass_generated_action_rollout" and episode["check_success"] is True
        if not args.record_native_data:
            return action_passed
        native_status = episode.get("native_synchronized_data", {}).get("status")
        return action_passed and native_status == "pass_native_synchronized_recording"

    pass_count = sum(1 for episode in episodes if episode_passed(episode))
    native_pass_count = sum(
        1
        for episode in episodes
        if episode.get("native_synchronized_data", {}).get("status") == "pass_native_synchronized_recording"
    )
    fail_count = len(episodes) - pass_count
    status = "pass_generated_rollout_collection" if pass_count == len(episodes) else "fail_generated_rollout_collection"
    events.append({"event": "collection_complete", "status": status, "pass_count": pass_count, "fail_count": fail_count})

    dataset_manifest = {
        "schema_version": "alchedata.generated_rollout_dataset_manifest.v0",
        "command": "/collect",
        "probe_type": "generated_selection2env_multi_episode_play_once_collection",
        "task_id": args.task_id,
        "status": status,
        "episode_count": len(episodes),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "native_synchronized_recording_requested": args.record_native_data,
        "native_synchronized_pass_count": native_pass_count,
        "placement_manifest": rel(args.placement_manifest) if args.placement_manifest else None,
        "placement_manifest_status": placement_manifest.get("status") if placement_manifest else None,
        "placement_split": args.placement_split if args.placement_manifest else "fixed",
        "unique_pose_signature_count": len(
            {episode.get("pose_signature") for episode in episodes if episode.get("pose_signature")}
        ),
        "episodes": episodes,
        "claim_boundary": (
            "Generated play_once demonstration collection with explicit per-episode placements and RoboTwin-native "
            "synchronized camera/qpos records; "
            "learned policy /train and /evaluate are not run."
            if args.record_native_data
            else "Generated play_once demonstration collection only; learned policy /train and /evaluate are not run."
        ),
    }
    write_json(out_dir / "dataset_manifest.json", dataset_manifest)
    write_jsonl(out_dir / "events.jsonl", events)

    report = {
        **base_report,
        "status": status,
        "duration_sec": round(time.time() - started, 4),
        "episode_count": len(episodes),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "native_synchronized_pass_count": native_pass_count,
        "placement_manifest": rel(args.placement_manifest) if args.placement_manifest else None,
        "placement_manifest_status": placement_manifest.get("status") if placement_manifest else None,
        "placement_split": args.placement_split if args.placement_manifest else "fixed",
        "unique_pose_signature_count": len(
            {episode.get("pose_signature") for episode in episodes if episode.get("pose_signature")}
        ),
        "dataset_manifest": rel(out_dir / "dataset_manifest.json"),
        "events": rel(out_dir / "events.jsonl"),
        "episodes": episodes,
        "next_data_requirement": (
            "Use the generated demonstration collection as the input contract for policy /train."
            if status == "pass_generated_rollout_collection"
            else "Repair failed generated episodes before promoting this collection to /train."
        ),
    }
    write_json(out_dir / "collection_report.json", report)
    return 0 if status == "pass_generated_rollout_collection" else 1


if __name__ == "__main__":
    raise SystemExit(main())
