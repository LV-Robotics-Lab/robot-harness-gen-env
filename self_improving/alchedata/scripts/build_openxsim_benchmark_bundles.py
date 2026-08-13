#!/usr/bin/env python3
"""Build unified PEARL command-loop bundles from official RoboTwin rollouts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT / "artifacts" / "openxsim_benchmarks"
WORKSPACE_ROOTS = (
    Path("/home/jingxiang/workspace/alchedata-self-improving-agents"),
    Path("/Users/boris/workspace/alchedata-self-improving-agents"),
)
BENCHMARK_RUNS = {
    "open_laptop": ROOT / "runs" / "official_rollout_open_laptop" / "rollout_report.json",
    "place_mouse_pad": ROOT / "runs" / "official_rollout_place_mouse_pad" / "rollout_report.json",
    "place_container_plate": ROOT / "runs" / "official_rollout_place_container_plate" / "rollout_report.json",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def relative_artifact(value: str | Path) -> str:
    path = Path(value)
    if not path.is_absolute():
        return str(path)
    for root in WORKSPACE_ROOTS:
        try:
            return str(path.relative_to(root))
        except ValueError:
            continue
    raise ValueError(f"Artifact is outside known workspace roots: {path}")


def build_bundle(task_name: str, report_path: Path) -> dict[str, Any]:
    report = read_json(report_path)
    if report.get("task_name") != task_name:
        raise ValueError(f"Task mismatch for {report_path}: {report.get('task_name')} != {task_name}")
    if report.get("status") != "pass_action_rollout" or report.get("check_success") is not True:
        raise ValueError(f"Official rollout did not pass for {task_name}")

    benchmark_id = f"robotwin_{task_name}_seed_{report['seed']}"
    out_dir = OUT_ROOT / task_name
    artifact_root = str(out_dir.relative_to(ROOT))
    rollout_report = relative_artifact(report_path)
    rollout_events = relative_artifact(report["events"])
    move_events = relative_artifact(report["move_events"])
    observer_video = relative_artifact(report["observer_video"])
    video_capture = report.get("video_capture", {})
    if (
        video_capture.get("endpoint_only") is not False
        or video_capture.get("frame_count", 0) < 24
        or video_capture.get("duration_sec", 0) < 2
    ):
        raise ValueError(f"Official rollout video is not continuous for {task_name}: {video_capture}")
    failure_diagnosis_path = str((out_dir / "failure_diagnosis.json").relative_to(ROOT))
    run_state_path = str((out_dir / "run_state.json").relative_to(ROOT))
    events_path = str((out_dir / "events.jsonl").relative_to(ROOT))
    scene_manifest_path = str((out_dir / "scene_manifest.json").relative_to(ROOT))
    task_manifest_path = str((out_dir / "task_manifest.json").relative_to(ROOT))

    images = {key: relative_artifact(value) for key, value in report.get("images", {}).items()}
    scene_manifest = {
        "schema_version": "alchedata.openxsim_scene_manifest.v0",
        "benchmark_id": benchmark_id,
        "scene_id": benchmark_id,
        "source": "official_robotwin_task_class",
        "simulator_adapter": "RoboTwin/SAPIEN",
        "task_config": report["task_config"],
        "seed": report["seed"],
        "initial_entities": report.get("initial_entities", {}),
        "final_entities": report.get("final_entities", {}),
        "texture_info": report.get("task_info", {}).get("texture_info", {}),
        "camera_artifacts": images,
    }
    write_json(out_dir / "scene_manifest.json", scene_manifest)

    diagnosis = {
        "schema_version": "alchedata.openxsim_failure_diagnosis.v0",
        "benchmark_id": benchmark_id,
        "task_name": task_name,
        "source_rollout_report": rollout_report,
        **report["failure_diagnosis"],
    }
    write_json(out_dir / "failure_diagnosis.json", diagnosis)

    task_manifest = {
        "schema_version": "alchedata.openxsim_task_manifest.v0",
        "benchmark_id": benchmark_id,
        "task_name": task_name,
        "task_config": report["task_config"],
        "execution_mode": "official_robotwin_scripted_play_once",
        "learned_policy": False,
        "task_info": report.get("task_info", {}).get("info", {}),
        "success_verifier": {
            "type": "official_robotwin_check_success",
            "result": report["check_success"],
            "plan_success": report.get("plan_success"),
        },
        "rollout_report": rollout_report,
        "rollout_events": rollout_events,
        "move_events": move_events,
        "failure_diagnosis": failure_diagnosis_path,
        "next_data_requirement": report["next_data_requirement"],
    }
    write_json(out_dir / "task_manifest.json", task_manifest)

    events = [
        {
            "sequence": 0,
            "command": "/gen-env",
            "event": "official_robotwin_environment_reused",
            "status": "pass",
            "artifact": scene_manifest_path,
        },
        {
            "sequence": 1,
            "command": "/collect",
            "event": "scripted_rollout_completed",
            "status": report["status"],
            "artifact": rollout_report,
        },
        {
            "sequence": 2,
            "command": "/diagnose",
            "event": "dense_diagnosis_recorded",
            "status": diagnosis["status"],
            "artifact": failure_diagnosis_path,
        },
        {
            "sequence": 3,
            "command": "/evaluate",
            "event": "official_success_verifier_completed",
            "status": "pass" if report["check_success"] else "fail",
            "artifact": task_manifest_path,
            "learned_policy": False,
        },
        {
            "sequence": 4,
            "command": "/diagnose",
            "event": "next_data_requirement_emitted",
            "status": "recorded",
            "artifact": task_manifest_path,
            "next_data_requirement": report["next_data_requirement"],
        },
    ]
    write_jsonl(out_dir / "events.jsonl", events)

    run_state = {
        "schema_version": "alchedata.openxsim_run_state.v0",
        "run_id": benchmark_id,
        "benchmark_id": benchmark_id,
        "task_name": task_name,
        "simulator_adapter": "RoboTwin/SAPIEN",
        "status": "pass_openxsim_scripted_benchmark_bundle",
        "current_stage": "/evaluate",
        "execution_mode": "official_robotwin_scripted_play_once",
        "learned_policy": False,
        "artifact_root": artifact_root,
        "events": events_path,
        "source_rollout_report": rollout_report,
        "next_data_requirement": report["next_data_requirement"],
    }
    write_json(out_dir / "run_state.json", run_state)

    bundle = {
        "schema_version": "alchedata.openxsim_benchmark_bundle.v0",
        "benchmark_id": benchmark_id,
        "task_name": task_name,
        "simulator_adapter": "RoboTwin/SAPIEN",
        "status": "pass_openxsim_scripted_benchmark_bundle",
        "video_capture": video_capture,
        "command_loop": {
            "/gen-env": {"status": "pass_reused_official_environment", "artifact": scene_manifest_path},
            "/collect": {"status": report["status"], "artifact": rollout_report},
            "/diagnose": {"status": diagnosis["status"], "artifact": failure_diagnosis_path},
            "/evaluate": {
                "status": "pass_official_success_verifier",
                "artifact": task_manifest_path,
                "learned_policy": False,
            },
        },
        "artifacts": {
            "run_state": run_state_path,
            "events": events_path,
            "scene_manifest": scene_manifest_path,
            "task_manifest": task_manifest_path,
            "rollout_report": rollout_report,
            "rollout_events": rollout_events,
            "move_events": move_events,
            "failure_diagnosis": failure_diagnosis_path,
            "observer_video": observer_video,
        },
        "next_data_requirement": report["next_data_requirement"],
        "claim_boundary": (
            "This bundle unifies an official RoboTwin scripted-expert rollout across PEARL command artifacts. "
            "Its /evaluate stage is the official task success verifier, not learned-policy evaluation."
        ),
    }
    write_json(out_dir / "benchmark_manifest.json", bundle)
    return bundle


def main() -> int:
    bundles = [build_bundle(task_name, path) for task_name, path in BENCHMARK_RUNS.items()]
    manifest = {
        "schema_version": "alchedata.openxsim_benchmark_manifest.v0",
        "status": "pass_three_openxsim_benchmark_bundles",
        "benchmark_count": len(bundles),
        "benchmarks": [
            {
                "benchmark_id": bundle["benchmark_id"],
                "task_name": bundle["task_name"],
                "status": bundle["status"],
                "manifest": f"artifacts/openxsim_benchmarks/{bundle['task_name']}/benchmark_manifest.json",
            }
            for bundle in bundles
        ],
        "claim_boundary": (
            "Three official RoboTwin scripted benchmark bundles with unified command-loop artifacts; "
            "not three learned-policy evaluations."
        ),
    }
    write_json(OUT_ROOT / "manifest.json", manifest)
    print(json.dumps({"status": manifest["status"], "benchmark_count": len(bundles)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
