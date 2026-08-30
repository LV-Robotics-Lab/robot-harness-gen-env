from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from self_improving.harness import runtime_executor
from self_improving.harness.runtime_capability import RUNTIME_ENVIRONMENT_ALLOWLIST
from self_improving.harness.runtime_executor import (
    RUNTIME_ARTIFACT_PATHS,
    RuntimeExecutionStatus,
    RuntimeExecutorConfigurationError,
    RuntimeFailureCode,
    RuntimeJob,
    SubprocessRoboTwinRuntimeExecutor,
)


def _write_fake_runner(path: Path) -> None:
    path.write_text(
        """\
from __future__ import annotations
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

EVENT_KINDS = [
    "preflight.completed", "scene.loaded", "simulation.started",
    "simulation.checkpoint", "simulation.completed", "media.completed",
    "evidence.completed", "worker.completed",
]
MEDIA = [
    "preview_head.png", "preview_segmentation.png", "preview_world_left.png",
    "preview_world_right.png", "observer_start.png", "observer_mid.png",
    "observer_end.png", "observer_runtime.mp4",
]
EVIDENCE = ["runtime_evidence.json", "runtime_validation_report.json"]
ARTIFACTS = MEDIA + EVIDENCE
PACKAGES = [
    "Pillow", "PyYAML", "gymnasium", "h5py", "imageio", "imageio-ffmpeg",
    "mplib", "nvidia-curobo", "numpy", "open3d", "opencv-python", "sapien",
    "torch", "toppra", "transforms3d", "trimesh",
]
ENVIRONMENT_KEYS = [
    "CONDA_PREFIX", "CUDA_DEVICE_ORDER", "CUDA_HOME", "CUDA_PATH",
    "CUDA_VISIBLE_DEVICES", "DISPLAY", "EGL_DEVICE_ID", "EGL_PLATFORM", "HOME",
    "LANG", "LC_ALL", "LD_LIBRARY_PATH", "LIBGL_DRIVERS_PATH",
    "NVIDIA_DRIVER_CAPABILITIES", "NVIDIA_VISIBLE_DEVICES", "PATH",
    "PYTHONDONTWRITEBYTECODE", "PYTHONPATH", "PYTHONUNBUFFERED", "TMPDIR",
    "TORCH_HOME", "VIRTUAL_ENV", "VK_ICD_FILENAMES", "XAUTHORITY", "XDG_CACHE_HOME",
    "__GLX_VENDOR_LIBRARY_NAME",
]

parser = argparse.ArgumentParser()
parser.add_argument("--robotwin-root", required=True)
parser.add_argument("--resolved-scene")
parser.add_argument("--asset-catalog")
parser.add_argument("--out-dir")
parser.add_argument("--task-config", default="demo_clean")
parser.add_argument("--describe-capabilities")
parser.add_argument("--settle-steps")
parser.add_argument("--settle-converge-max")
parser.add_argument("--precheck-steps")
parser.add_argument("--video-frames")
parser.add_argument("--fps")
parser.add_argument("--min-visible-pixels")
parser.add_argument("--contact-window-steps")
parser.add_argument("--checkpoint-steps")
parser.add_argument("--event-fd", type=int)
parser.add_argument("--event-protocol")
parser.add_argument("--expected-capability-sha256")
parser.add_argument("--runtime-asset-root")
parser.add_argument("--runtime-asset-manifest")
parser.add_argument("--expected-runtime-asset-snapshot-sha256")
parser.add_argument("--evidence-only", action="store_true")
args = parser.parse_args()

root = Path(args.robotwin_root)
task_path = root / "task_config" / f"{args.task_config}.yml"
capability = {
    "schema_version": "harness.robotwin_runtime_capability.v1",
    "protocol_version": 1,
    "backend_id": "robotwin.sapien",
    "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "python": {
        "implementation": "CPython",
        "version": "3.11.0",
        "executable_sha256": hashlib.sha256(
            Path(sys.executable).resolve().read_bytes()
        ).hexdigest(),
    },
    "packages": {
        name: {
            "version": "1.0",
            "record_sha256": "7" * 64,
            "direct_url_sha256": None,
        }
        for name in PACKAGES
    },
    "runtime_binaries": {
        "ffmpeg": {"sha256": "8" * 64, "bytes": 1},
    },
    "accelerator": {
        "probe": {
            "kind": "nvidia-smi.query-gpu.v1",
            "sha256": "9" * 64,
            "bytes": 1,
        },
        "gpus": [
            {"uuid": "GPU-0001", "name": "Fake GPU", "driver_version": "1.0"},
        ],
        "selection_environment": {
            name: {
                "present": name in os.environ,
                "value_sha256": (
                    hashlib.sha256(os.environ[name].encode()).hexdigest()
                    if name in os.environ
                    else None
                ),
            }
            for name in ENVIRONMENT_KEYS
        },
    },
    "bootstrap": {
        "kind": "python.import_base_task.v1",
        "source_sha256": "d" * 64,
        "command_sha256": "e" * 64,
    },
    "robotwin": {
        "commit": "a" * 40, "dirty": False,
        "tracked_diff_sha256": "0" * 64,
        "untracked_manifest_sha256": "1" * 64,
        "tree_state_sha256": "2" * 64,
    },
    "source_modules": {
        "scene_gen": {"tree_sha256": "5" * 64},
        "runtime_events": {"sha256": "6" * 64},
        "runtime_capability": {"sha256": "7" * 64},
        "runtime_assets": {"sha256": "8" * 64},
    },
    "task_config": {
        "path": f"task_config/{args.task_config}.yml",
        "sha256": hashlib.sha256(task_path.read_bytes()).hexdigest(),
        "registry": {
            "path": "env_cfg/task_config/_embodiment_config.yml",
            "sha256": "8" * 64,
        },
        "import_resources": [
            {
                "path": "assets/objects/objaverse/list.json",
                "sha256": "9" * 64,
                "bytes": 1,
            },
            {
                "path": "assets/objects/same.json",
                "sha256": "a" * 64,
                "bytes": 1,
            },
        ],
        "embodiment_closure": {
            "strategy": "selected_embodiment_tree.v1",
            "selection": ["panda"],
            "resources": [
                {
                    "name": "panda",
                    "path": "assets/embodiments/panda",
                    "tree_sha256": "b" * 64,
                    "file_count": 1,
                    "bytes": 1,
                    "required_files": [
                        {"path": "config.yml", "sha256": "c" * 64},
                    ],
                },
            ],
        },
    },
    "supported": {
        "resolved_scene_schemas": ["robotwin.resolved_scene.v1"],
        "asset_catalog_schemas": ["robotwin.asset_catalog.v1"],
        "runtime_evidence_schemas": ["robotwin.scene_runtime_evidence.v2"],
        "validation_report_schemas": ["robotwin.scene_validation.v1"],
        "parameters": {
            "precheck_steps": {"type": "integer", "minimum": 0, "default": 0},
            "settle_steps": {"type": "integer", "minimum": 1, "default": 900},
            "settle_converge_max": {"type": "integer", "minimum": 0, "default": 0},
            "contact_window_steps": {"type": "integer", "minimum": 1, "default": 60},
            "video_frames": {"type": "integer", "minimum": 0, "default": 120},
            "fps": {"type": "integer", "minimum": 1, "default": 12},
            "min_visible_pixels": {"type": "integer", "minimum": 0, "default": 64},
            "checkpoint_steps": {"type": "integer", "minimum": 1, "default": 120},
            "evidence_only": {"type": "boolean", "default": False},
        },
        "event_protocol": {
            "schema_version": "harness.runtime_event.v1",
            "transport": "dedicated_fd_jsonl",
            "kinds": EVENT_KINDS,
            "artifact_paths": ARTIFACTS,
        },
        "asset_snapshot": {
            "manifest_schema": "harness.runtime_asset_snapshot.v1",
            "tree_strategy": "selected_asset_complete_tree.v1",
            "transport": "cas_materialized_read_only_tree",
            "required_with_event_fd": True,
            "verification": "whole_tree_before_first_event_and_after_runtime_close",
            "failure_exit_codes": {"preflight": 86, "postflight_drift": 87},
            "limits": {
                "max_assets": 256,
                "max_directories": 100000,
                "max_files": 100000,
                "max_single_file_bytes": 2147483648,
                "max_total_bytes": 8589934592,
            },
        },
    },
}

mode_path = root / "capability_mode"
capability_mode = mode_path.read_text().strip() if mode_path.exists() else "normal"
if (root / "runtime-drift").exists():
    capability["robotwin"]["dirty"] = True
    capability["robotwin"]["tree_state_sha256"] = "4" * 64
if capability_mode == "bad_schema":
    capability.pop("backend_id")
elif capability_mode == "bad_runner_digest":
    capability["runner_sha256"] = "f" * 64
elif capability_mode == "packages_null":
    capability["packages"]["nvidia-curobo"] = None
elif capability_mode == "bad_runtime_binary":
    capability["runtime_binaries"]["ffmpeg"]["bytes"] = 0
elif capability_mode == "bad_accelerator":
    capability["accelerator"]["gpus"] = []
elif capability_mode == "bad_bootstrap":
    capability["bootstrap"]["kind"] = "wrong"
elif capability_mode == "bad_event_protocol":
    capability["supported"]["event_protocol"].pop("transport")
elif capability_mode == "bad_task_digest":
    capability["task_config"]["sha256"] = "f" * 64
elif capability_mode == "wrong_schema":
    capability["schema_version"] = "wrong"
elif capability_mode == "wrong_protocol":
    capability["protocol_version"] = 2
elif capability_mode == "wrong_backend":
    capability["backend_id"] = "wrong"
elif capability_mode == "bad_python":
    capability["python"]["version"] = ""
elif capability_mode == "bad_python_digest":
    capability["python"]["executable_sha256"] = "f" * 64
elif capability_mode == "bad_packages_shape":
    capability["packages"].pop("nvidia-curobo")
elif capability_mode == "bad_robotwin_shape":
    capability["robotwin"].pop("commit")
elif capability_mode == "bad_commit":
    capability["robotwin"]["commit"] = "ABC"
elif capability_mode == "bad_dirty":
    capability["robotwin"]["dirty"] = 0
elif capability_mode == "bad_tree_digest":
    capability["robotwin"]["tree_state_sha256"] = "ABC"
elif capability_mode == "bad_source_shape":
    capability["source_modules"].pop("runtime_events")
elif capability_mode == "bad_source_digest":
    capability["source_modules"]["scene_gen"]["tree_sha256"] = "ABC"
elif capability_mode == "bad_capability_source":
    capability["source_modules"].pop("runtime_capability")
elif capability_mode == "bad_task_shape":
    capability["task_config"].pop("path")
elif capability_mode == "bad_import_resources":
    capability["task_config"]["import_resources"].pop()
elif capability_mode == "bad_embodiment":
    capability["task_config"]["embodiment_closure"]["resources"] = []
elif capability_mode == "bad_task_path":
    capability["task_config"]["path"] = "task_config/other.yml"
elif capability_mode == "bad_supported_shape":
    capability["supported"].pop("parameters")
elif capability_mode == "bad_supported_schema":
    capability["supported"]["resolved_scene_schemas"] = ["wrong"]
elif capability_mode == "bad_parameters":
    capability["supported"]["parameters"]["fps"]["default"] = 99
elif capability_mode == "bad_event_value":
    capability["supported"]["event_protocol"]["transport"] = "stdout"

canonical = json.dumps(capability, sort_keys=True, separators=(",", ":")) + "\\n"
if args.describe_capabilities:
    if capability_mode == "timeout":
        time.sleep(60)
    if capability_mode == "exit":
        raise SystemExit(9)
    if capability_mode == "huge_stdout":
        print("x" * 5000)
    if capability_mode == "huge_stderr":
        print("x" * 5000, file=sys.stderr)
    if capability_mode == "small_stdout":
        print("capability diagnostic")
    if capability_mode == "escaped_once":
        child_pid_path = root / "capability-child.pid"
        child_code = (
            "import os,time,pathlib;"
            f"pathlib.Path({str(child_pid_path)!r}).write_text(str(os.getpid()));"
            "time.sleep(60)"
        )
        subprocess.Popen([sys.executable, "-c", child_code], start_new_session=True)
        mode_path.write_text("normal\\n")
    destination = Path(args.describe_capabilities)
    if capability_mode == "missing":
        pass
    elif capability_mode == "symlink":
        target = destination.parent / "capability-target.json"
        target.write_text(canonical)
        destination.symlink_to(target)
    elif capability_mode == "noncanonical":
        destination.write_text(json.dumps(capability, indent=2) + "\\n")
    elif capability_mode == "duplicate_key":
        destination.write_text('{"schema_version":"x","schema_version":"y"}\\n')
    elif capability_mode == "empty":
        destination.write_bytes(b"")
    elif capability_mode == "huge_file":
        destination.write_bytes(b"x" * 5000)
    elif capability_mode == "nan":
        destination.write_text('{"value":NaN}\\n')
    elif capability_mode == "directory":
        destination.mkdir()
    elif capability_mode == "nonempty_directory":
        destination.mkdir()
        (destination / "entry").write_text("unsafe")
    else:
        destination.write_text(canonical)
    if capability_mode == "remove_task":
        task_path.unlink()
    if capability_mode == "delete_interpreter":
        Path(sys.executable).unlink()
    raise SystemExit(0)

actual_capability_sha = hashlib.sha256(canonical.encode()).hexdigest()
if args.expected_capability_sha256 != actual_capability_sha:
    raise SystemExit(13)
if not all([
    args.runtime_asset_root,
    args.runtime_asset_manifest,
    args.expected_runtime_asset_snapshot_sha256,
]):
    raise SystemExit(14)
runtime_asset_manifest = Path(args.runtime_asset_manifest)
if (
    not Path(args.runtime_asset_root).is_dir()
    or not runtime_asset_manifest.is_file()
    or hashlib.sha256(runtime_asset_manifest.read_bytes()).hexdigest()
    != args.expected_runtime_asset_snapshot_sha256
):
    raise SystemExit(15)
mode = json.loads(Path(args.resolved_scene).read_text())["mode"]
out = Path(args.out_dir)
if mode == "preflight_crash":
    print("missing runtime dependency", file=sys.stderr)
    raise SystemExit(8)
if mode == "asset_preflight_failure":
    raise SystemExit(86)
if mode == "capability_drift":
    (root / "runtime-drift").write_text("changed\\n")
if mode == "postflight_exit":
    mode_path.write_text("exit\\n")
if mode == "postflight_bad":
    mode_path.write_text("bad_schema\\n")
if mode == "crash_postflight_bad":
    mode_path.write_text("bad_schema\\n")
if mode == "timeout":
    import signal
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(60)
if mode == "timeout_child":
    pid_path = Path(args.resolved_scene).with_suffix(".pid")
    child_code = (
        "import os,signal,time,pathlib;"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()));"
        "time.sleep(60)"
    )
    subprocess.Popen([sys.executable, "-c", child_code])
    time.sleep(60)
if mode == "escaped_child":
    pid_path = Path(args.resolved_scene).with_suffix(".pid")
    child_code = (
        "import os,time,pathlib;"
        f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()));"
        "time.sleep(60)"
    )
    subprocess.Popen(
        [sys.executable, "-c", child_code],
        start_new_session=True,
        pass_fds=(args.event_fd,),
    )
    time.sleep(60)

total_steps = int(args.precheck_steps) + max(int(args.settle_steps), int(args.video_frames))
events = [
    {"schema_version": "harness.runtime_event.v1", "kind": "preflight.completed"},
    {"schema_version": "harness.runtime_event.v1", "kind": "scene.loaded"},
    {"schema_version": "harness.runtime_event.v1", "kind": "simulation.started"},
]
for checkpoint in range(int(args.checkpoint_steps), total_steps + 1, int(args.checkpoint_steps)):
    events.append({
        "schema_version": "harness.runtime_event.v1",
        "kind": "simulation.checkpoint", "completed_steps": checkpoint,
    })
events.append({
    "schema_version": "harness.runtime_event.v1",
    "kind": "simulation.completed", "completed_steps": total_steps,
})
if mode == "repeated_checkpoint":
    events.insert(
        4,
        {
            "schema_version": "harness.runtime_event.v1",
            "kind": "simulation.checkpoint",
            "completed_steps": 2,
        },
    )
if mode == "bad_step_order":
    events[3]["completed_steps"] = total_steps + 1
if mode == "bad_checkpoint_multiple":
    events[3]["completed_steps"] = 1
if mode == "omit_checkpoint":
    events.pop(3)
if mode == "wrong_sim_kind":
    events[3] = {"schema_version": "harness.runtime_event.v1", "kind": "scene.loaded"}
if mode == "short_run":
    next(event for event in events if event["kind"] == "simulation.completed")[
        "completed_steps"
    ] = total_steps - 1
media = MEDIA if int(args.video_frames) > 0 else MEDIA[:4]
for name in media + EVIDENCE:
    (out / name).write_bytes((name + "\\n").encode())
events.extend([
    {
        "schema_version": "harness.runtime_event.v1",
        "kind": "media.completed",
        "artifact_paths": media,
    },
    {
        "schema_version": "harness.runtime_event.v1",
        "kind": "evidence.completed",
        "artifact_paths": EVIDENCE,
    },
    {"schema_version": "harness.runtime_event.v1", "kind": "worker.completed"},
])
for seq, event in enumerate(events, 1):
    event["seq"] = seq
if mode == "bad_seq":
    events[1]["seq"] = 91
elif mode == "bad_kind":
    events[0]["kind"] = "scene.loaded"
elif mode == "bad_path":
    events[-3]["artifact_paths"] = ["../escape"]
elif mode == "bad_media_contract":
    events[-3]["artifact_paths"] = ["preview_head.png"]
elif mode == "bad_evidence_contract":
    events[-2]["artifact_paths"] = ["runtime_evidence.json"]
elif mode == "bad_tail":
    events[-3] = {
        "schema_version": "harness.runtime_event.v1",
        "seq": events[-3]["seq"],
        "kind": "evidence.completed",
        "artifact_paths": EVIDENCE,
    }
elif mode == "event_after_terminal":
    events.append({
        "schema_version": "harness.runtime_event.v1",
        "seq": len(events) + 1,
        "kind": "preflight.completed",
    })
elif mode in {"partial", "crash", "crash_postflight_bad"}:
    events = events[:3]
elif mode == "asset_postflight_drift":
    events = events[:-1]

if mode == "output_symlink":
    (out / "preview_head.png").unlink()
    outside = Path(args.resolved_scene).with_suffix(".png")
    outside.write_bytes(b"outside")
    (out / "preview_head.png").symlink_to(outside)
elif mode == "missing_output":
    (out / "preview_head.png").unlink()
elif mode == "unexpected_output":
    (out / "not-allowed.bin").write_bytes(b"bad")
elif mode == "unexpected_directory":
    (out / "not-allowed").mkdir()
elif mode == "special_output":
    (out / "preview_head.png").unlink()
    os.mkfifo(out / "preview_head.png")

if mode == "stdout_limit":
    print("x" * 5000)
elif mode == "stderr_limit":
    print("x" * 5000, file=sys.stderr)
elif mode == "transcript_limit":
    os.write(args.event_fd, b"x" * 5000)
    time.sleep(1)

for event in events:
    suffix = "" if mode == "unterminated" and event is events[-1] else "\\n"
    record = json.dumps(event, sort_keys=True, separators=(",", ":")) + suffix
    os.write(args.event_fd, record.encode())
print('{"seq":999,"kind":"worker.completed"}')
print("diagnostic only", file=sys.stderr)
if mode == "env":
    print("secret=" + os.environ.get("RUNTIME_EXECUTOR_SECRET", "absent"))
    print("pythonpath=" + os.environ["PYTHONPATH"])
if mode == "asset_postflight_drift":
    raise SystemExit(87)
raise SystemExit(7 if mode in {"crash", "crash_postflight_bad"} else 0)
""",
        encoding="utf-8",
    )


def _capability_digest(runner: Path, robotwin_root: Path) -> str:
    destination = robotwin_root / "expected-capability.json"
    environment = {
        key: value for key, value in os.environ.items() if key in RUNTIME_ENVIRONMENT_ALLOWLIST
    }
    environment["PYTHONPATH"] = str(runner.parent)
    environment["PYTHONUNBUFFERED"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    subprocess.run(
        [
            sys.executable,
            str(runner),
            "--robotwin-root",
            str(robotwin_root),
            "--task-config",
            "demo_clean",
            "--describe-capabilities",
            str(destination),
        ],
        check=True,
        env=environment,
    )
    payload = destination.read_bytes()
    destination.unlink()
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class _Case:
    executor: SubprocessRoboTwinRuntimeExecutor
    job: RuntimeJob
    runner: Path
    robotwin: Path
    resolved: Path

    def run(self, observer=lambda event: None):
        return self.executor.execute(self.job, observer)


def _case(
    tmp_path: Path,
    *,
    mode: str = "success",
    video_frames: int = 0,
    timeout_seconds: float = 3,
    max_stdout_bytes: int = 4096,
    max_stderr_bytes: int = 4096,
    max_transcript_bytes: int = 4096,
    capability_timeout_seconds: float = 2,
    max_capability_bytes: int = 16_384,
) -> _Case:
    runner = tmp_path / "fake runtime.py"
    _write_fake_runner(runner)
    robotwin = tmp_path / "robotwin"
    (robotwin / "task_config").mkdir(parents=True)
    (robotwin / "task_config" / "demo_clean.yml").write_text("{}\n", encoding="utf-8")
    resolved = tmp_path / "resolved scene.json"
    resolved.write_text(json.dumps({"mode": mode}) + "\n", encoding="utf-8")
    catalog = tmp_path / "catalog.json"
    catalog.write_text("{}\n", encoding="utf-8")
    runtime_asset_root = tmp_path / "runtime assets"
    runtime_asset_root.mkdir()
    (runtime_asset_root / "fixture").write_bytes(b"asset")
    runtime_asset_manifest = tmp_path / "runtime asset manifest.json"
    runtime_asset_manifest.write_bytes(b'{"fixture":"runtime-assets"}\n')
    expected_runtime_asset_snapshot_sha256 = hashlib.sha256(
        runtime_asset_manifest.read_bytes()
    ).hexdigest()
    expected = _capability_digest(runner, robotwin)
    executor = SubprocessRoboTwinRuntimeExecutor(
        interpreter=Path(sys.executable),
        runner=runner,
        work_root=tmp_path / "runs",
        timeout_seconds=timeout_seconds,
        capability_timeout_seconds=capability_timeout_seconds,
        terminate_grace_seconds=0.1,
        max_stdout_bytes=max_stdout_bytes,
        max_stderr_bytes=max_stderr_bytes,
        max_event_bytes=min(2048, max_transcript_bytes),
        max_transcript_bytes=max_transcript_bytes,
        max_capability_bytes=max_capability_bytes,
    )
    job = RuntimeJob(
        run_id="run-001",
        attempt=1,
        robotwin_root=robotwin,
        resolved_scene=resolved,
        asset_catalog=catalog,
        expected_capability_sha256=expected,
        runtime_asset_root=runtime_asset_root,
        runtime_asset_manifest=runtime_asset_manifest,
        expected_runtime_asset_snapshot_sha256=expected_runtime_asset_snapshot_sha256,
        settle_steps=3,
        video_frames=video_frames,
        checkpoint_steps=2,
    )
    return _Case(executor=executor, job=job, runner=runner, robotwin=robotwin, resolved=resolved)


def _assert_failure(result, code: RuntimeFailureCode) -> None:
    assert result.status is RuntimeExecutionStatus.FAILED
    assert result.failure is not None
    assert result.failure.code is code


def test_execute_uses_only_dedicated_event_fd_and_snapshots_outputs(tmp_path: Path) -> None:
    case = _case(tmp_path)
    seen = []

    result = case.run(seen.append)

    assert result.status is RuntimeExecutionStatus.SUCCEEDED
    assert result.failure is None
    assert [event.seq for event in seen] == list(range(1, 9))
    assert result.stdout == b'{"seq":999,"kind":"worker.completed"}\n'
    assert result.stderr == b"diagnostic only\n"
    assert [item.relative_path for item in result.output_files] == [
        "preview_head.png",
        "preview_segmentation.png",
        "preview_world_left.png",
        "preview_world_right.png",
        "runtime_evidence.json",
        "runtime_validation_report.json",
    ]
    assert all(item.path.is_file() and len(item.sha256) == 64 for item in result.output_files)
    assert result.transcript_complete is True
    assert result.capability is not None
    assert result.capability.sha256 == case.job.expected_capability_sha256
    assert result.postflight_capability_sha256 == case.job.expected_capability_sha256
    environment = result.capability.document["accelerator"]["selection_environment"]
    assert set(environment) == RUNTIME_ENVIRONMENT_ALLOWLIST
    assert (
        environment["PYTHONPATH"]["value_sha256"]
        == hashlib.sha256(str(case.runner.parent).encode()).hexdigest()
    )


def test_execute_rejects_runtime_asset_manifest_drift_before_spawning_worker(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    case.job.runtime_asset_manifest.write_bytes(b"changed-after-job-construction\n")

    with pytest.raises(RuntimeExecutorConfigurationError, match="runtime asset snapshot"):
        case.run()


def test_execute_rejects_runtime_asset_manifest_race_before_creating_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(tmp_path)
    original_fstat = runtime_executor.os.fstat
    calls = 0

    def raced_fstat(descriptor: int):
        nonlocal calls
        value = original_fstat(descriptor)
        try:
            opened = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
        except OSError:
            return value
        if opened == case.job.runtime_asset_manifest:
            calls += 1
            if calls == 2:
                fields = {
                    name: getattr(value, name)
                    for name in (
                        "st_dev",
                        "st_ino",
                        "st_mode",
                        "st_size",
                        "st_mtime_ns",
                        "st_ctime_ns",
                    )
                }
                fields["st_mtime_ns"] += 1
                return SimpleNamespace(**fields)
        return value

    monkeypatch.setattr(runtime_executor.os, "fstat", raced_fstat)

    with pytest.raises(RuntimeExecutorConfigurationError, match="changed while being read"):
        case.run()
    assert not case.executor.work_root.exists()


def test_execute_requires_all_requested_video_artifacts(tmp_path: Path) -> None:
    result = _case(tmp_path, video_frames=2).run()

    assert result.status is RuntimeExecutionStatus.SUCCEEDED
    assert tuple(item.relative_path for item in result.output_files) == RUNTIME_ARTIFACT_PATHS


@pytest.mark.parametrize(
    "mode",
    ["bad_seq", "bad_kind", "bad_path", "unterminated", "event_after_terminal"],
)
def test_event_protocol_attacks_fail_closed(tmp_path: Path, mode: str) -> None:
    result = _case(tmp_path, mode=mode).run()

    _assert_failure(result, RuntimeFailureCode.EVENT_PROTOCOL_ERROR)


@pytest.mark.parametrize("mode", ["bad_media_contract", "bad_evidence_contract"])
def test_artifact_event_contract_is_exact(tmp_path: Path, mode: str) -> None:
    result = _case(tmp_path, mode=mode).run()

    _assert_failure(result, RuntimeFailureCode.EVENT_PROTOCOL_ERROR)


def test_valid_checkpoint_is_forwarded(tmp_path: Path) -> None:
    seen = []
    result = _case(tmp_path, mode="checkpoint").run(seen.append)

    assert result.status is RuntimeExecutionStatus.SUCCEEDED
    assert [event.kind.value for event in seen][3] == "simulation.checkpoint"


def test_checkpoint_beyond_completion_fails(tmp_path: Path) -> None:
    result = _case(tmp_path, mode="bad_step_order").run()

    _assert_failure(result, RuntimeFailureCode.EVENT_PROTOCOL_ERROR)


@pytest.mark.parametrize(
    "mode",
    [
        "repeated_checkpoint",
        "bad_checkpoint_multiple",
        "wrong_sim_kind",
        "short_run",
        "bad_tail",
    ],
)
def test_timeline_contract_rejects_short_or_malformed_runs(tmp_path: Path, mode: str) -> None:
    result = _case(tmp_path, mode=mode).run()

    _assert_failure(result, RuntimeFailureCode.EVENT_PROTOCOL_ERROR)


def test_checkpoint_sequence_includes_a_divisible_fixed_total_step_count(tmp_path: Path) -> None:
    case = _case(tmp_path)
    case = replace(case, job=replace(case.job, settle_steps=4))

    result = case.run()

    assert result.status is RuntimeExecutionStatus.SUCCEEDED
    checkpoints = [
        event.completed_steps
        for event in result.events
        if event.kind.value == "simulation.checkpoint"
    ]
    assert checkpoints == [2, 4]


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("partial", RuntimeFailureCode.INCOMPLETE_TRANSCRIPT),
        ("crash", RuntimeFailureCode.WORKER_CRASH),
        ("timeout", RuntimeFailureCode.WORKER_TIMEOUT),
    ],
)
def test_process_failures_are_typed(tmp_path: Path, mode: str, code: RuntimeFailureCode) -> None:
    result = _case(tmp_path, mode=mode, timeout_seconds=0.2).run()

    _assert_failure(result, code)


def test_exit_before_first_event_is_typed_as_runtime_preflight_failure(tmp_path: Path) -> None:
    result = _case(tmp_path, mode="preflight_crash").run()

    _assert_failure(result, RuntimeFailureCode.RUNTIME_PREFLIGHT_FAILED)
    assert result.events == ()
    assert b"missing runtime dependency" in result.stderr


def test_runtime_asset_preflight_failure_is_typed_before_any_event(tmp_path: Path) -> None:
    result = _case(tmp_path, mode="asset_preflight_failure").run()

    _assert_failure(result, RuntimeFailureCode.RUNTIME_PREFLIGHT_FAILED)
    assert result.events == ()


def test_runtime_asset_postflight_drift_retains_dedicated_trust_failure(
    tmp_path: Path,
) -> None:
    result = _case(tmp_path, mode="asset_postflight_drift").run()

    _assert_failure(result, RuntimeFailureCode.RUNTIME_ASSET_DRIFT)
    assert result.events
    assert all(event.kind.value != "worker.completed" for event in result.events)


@pytest.mark.parametrize("mode", ["stdout_limit", "stderr_limit", "transcript_limit"])
def test_all_subprocess_streams_are_bounded(tmp_path: Path, mode: str) -> None:
    case = _case(
        tmp_path,
        mode=mode,
        max_stdout_bytes=256,
        max_stderr_bytes=256,
        max_transcript_bytes=256,
    )

    result = case.run()

    _assert_failure(result, RuntimeFailureCode.STREAM_LIMIT_EXCEEDED)
    assert result.stdout_truncated or result.stderr_truncated or result.transcript_truncated


def test_observer_failure_terminates_worker_and_is_typed(tmp_path: Path) -> None:
    def broken_observer(event):
        del event
        raise LookupError("observer boom")

    result = _case(tmp_path).run(broken_observer)

    _assert_failure(result, RuntimeFailureCode.OBSERVER_FAILED)
    assert "LookupError" in result.failure.message


@pytest.mark.parametrize(
    "mode",
    [
        "output_symlink",
        "missing_output",
        "unexpected_output",
        "unexpected_directory",
        "special_output",
    ],
)
def test_output_allowlist_is_symlink_safe(tmp_path: Path, mode: str) -> None:
    result = _case(tmp_path, mode=mode).run()

    _assert_failure(result, RuntimeFailureCode.OUTPUT_SECURITY_ERROR)


def test_environment_uses_trusted_module_root_not_inherited_pythonpath(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNTIME_EXECUTOR_SECRET", "must-not-leak")
    monkeypatch.setenv("PYTHONPATH", "/untrusted/injection")
    case = _case(tmp_path, mode="env")

    result = case.run()

    assert result.status is RuntimeExecutionStatus.SUCCEEDED
    assert b"secret=absent" in result.stdout
    assert f"pythonpath={case.runner.parent}".encode() in result.stdout
    assert b"/untrusted/injection" not in result.stdout


def test_allowed_runtime_environment_is_bound_into_the_capability_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(tmp_path)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/qualified/environment/changed")

    result = case.run()

    _assert_failure(result, RuntimeFailureCode.CAPABILITY_MISMATCH)
    assert result.events == ()


def test_capability_digest_drift_after_worker_fails_closed(tmp_path: Path) -> None:
    result = _case(tmp_path, mode="capability_drift").run()

    _assert_failure(result, RuntimeFailureCode.CAPABILITY_DRIFT)
    assert result.postflight_capability_sha256 != result.capability.sha256


@pytest.mark.parametrize("mode", ["postflight_exit", "postflight_bad"])
def test_postflight_capability_failure_overrides_worker_success(tmp_path: Path, mode: str) -> None:
    result = _case(tmp_path, mode=mode).run()

    expected = (
        RuntimeFailureCode.CAPABILITY_PROCESS_FAILED
        if mode == "postflight_exit"
        else RuntimeFailureCode.CAPABILITY_PROTOCOL_ERROR
    )
    _assert_failure(result, expected)
    assert result.transcript_complete is True


def test_postflight_trust_failure_preserves_worker_failure_as_secondary(tmp_path: Path) -> None:
    result = _case(tmp_path, mode="crash_postflight_bad").run()

    _assert_failure(result, RuntimeFailureCode.CAPABILITY_PROTOCOL_ERROR)
    assert [failure.code for failure in result.secondary_failures] == [
        RuntimeFailureCode.WORKER_CRASH
    ]
    assert result.postflight_probe is not None
    assert result.postflight_probe.exit_code == 0


def test_preflight_capability_must_match_qualified_digest(tmp_path: Path) -> None:
    case = _case(tmp_path)
    case = replace(case, job=replace(case.job, expected_capability_sha256="f" * 64))

    result = case.run()

    _assert_failure(result, RuntimeFailureCode.CAPABILITY_MISMATCH)
    assert result.events == ()


@pytest.mark.parametrize(
    "mode",
    [
        "bad_schema",
        "bad_runner_digest",
        "packages_null",
        "bad_runtime_binary",
        "bad_accelerator",
        "bad_bootstrap",
        "bad_event_protocol",
        "bad_task_digest",
        "wrong_schema",
        "wrong_protocol",
        "wrong_backend",
        "bad_python",
        "bad_python_digest",
        "bad_packages_shape",
        "bad_robotwin_shape",
        "bad_commit",
        "bad_dirty",
        "bad_tree_digest",
        "bad_source_shape",
        "bad_source_digest",
        "bad_capability_source",
        "bad_task_shape",
        "bad_import_resources",
        "bad_embodiment",
        "bad_task_path",
        "bad_supported_shape",
        "bad_supported_schema",
        "bad_parameters",
        "bad_event_value",
        "noncanonical",
        "duplicate_key",
        "missing",
        "symlink",
        "empty",
        "nan",
        "remove_task",
        "directory",
        "nonempty_directory",
    ],
)
def test_invalid_capability_documents_fail_before_runtime(tmp_path: Path, mode: str) -> None:
    case = _case(tmp_path)
    (case.robotwin / "capability_mode").write_text(mode + "\n", encoding="utf-8")

    result = case.run()

    _assert_failure(result, RuntimeFailureCode.CAPABILITY_PROTOCOL_ERROR)
    assert result.events == ()


def test_oversized_capability_file_is_rejected(tmp_path: Path) -> None:
    case = _case(tmp_path, max_capability_bytes=256)
    (case.robotwin / "capability_mode").write_text("huge_file\n", encoding="utf-8")

    result = case.run()

    _assert_failure(result, RuntimeFailureCode.CAPABILITY_PROTOCOL_ERROR)
    assert result.preflight_probe is not None
    assert result.preflight_probe.capability_output_truncated is True
    assert len(result.preflight_probe.capability_output) == 256


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("exit", RuntimeFailureCode.CAPABILITY_PROCESS_FAILED),
        ("timeout", RuntimeFailureCode.CAPABILITY_TIMEOUT),
        ("huge_stdout", RuntimeFailureCode.STREAM_LIMIT_EXCEEDED),
        ("huge_stderr", RuntimeFailureCode.STREAM_LIMIT_EXCEEDED),
    ],
)
def test_capability_process_failures_are_typed(
    tmp_path: Path,
    mode: str,
    code: RuntimeFailureCode,
) -> None:
    case = _case(
        tmp_path,
        max_stdout_bytes=256,
        max_stderr_bytes=256,
        capability_timeout_seconds=0.1,
    )
    (case.robotwin / "capability_mode").write_text(mode + "\n", encoding="utf-8")

    result = case.run()

    _assert_failure(result, code)
    assert result.preflight_probe is not None
    if mode in {"huge_stdout", "huge_stderr"}:
        assert result.preflight_probe.stdout_truncated or result.preflight_probe.stderr_truncated
        assert len(result.preflight_probe.stdout) <= 256
        assert len(result.preflight_probe.stderr) <= 256


def test_capability_probe_diagnostics_are_retained_on_success(tmp_path: Path) -> None:
    case = _case(tmp_path)

    result = case.run()

    assert result.preflight_probe is not None
    assert result.preflight_probe.exit_code == 0
    assert result.preflight_probe.stdout == b""
    assert result.preflight_probe.stderr == b""
    assert result.postflight_probe is not None
    assert result.postflight_probe.exit_code == 0


def test_small_capability_stdout_is_retained_without_truncation(tmp_path: Path) -> None:
    case = _case(tmp_path)
    (case.robotwin / "capability_mode").write_text("small_stdout\n", encoding="utf-8")

    result = case.run()

    assert result.status is RuntimeExecutionStatus.SUCCEEDED
    assert result.preflight_probe is not None
    assert result.preflight_probe.stdout == b"capability diagnostic\n"
    assert result.preflight_probe.stdout_truncated is False


def test_capability_probe_closes_pipes_inherited_by_escaped_child(tmp_path: Path) -> None:
    case = _case(tmp_path)
    (case.robotwin / "capability_mode").write_text("escaped_once\n", encoding="utf-8")

    result = case.run()

    assert result.status is RuntimeExecutionStatus.SUCCEEDED
    child_pid = int((case.robotwin / "capability-child.pid").read_text())
    os.kill(child_pid, 9)


def test_missing_required_checkpoint_fails_closed(tmp_path: Path) -> None:
    result = _case(tmp_path, mode="omit_checkpoint").run()

    _assert_failure(result, RuntimeFailureCode.EVENT_PROTOCOL_ERROR)


def test_precheck_steps_are_included_in_expected_runtime_total(tmp_path: Path) -> None:
    case = _case(tmp_path)
    case = replace(case, job=replace(case.job, precheck_steps=2))

    result = case.run()

    assert result.status is RuntimeExecutionStatus.SUCCEEDED
    completed = next(event for event in result.events if event.kind.value == "simulation.completed")
    assert completed.completed_steps == 5


def test_timeout_kills_descendants_in_the_worker_process_group(tmp_path: Path) -> None:
    case = _case(tmp_path, mode="timeout_child", timeout_seconds=0.3)

    result = case.run()

    _assert_failure(result, RuntimeFailureCode.WORKER_TIMEOUT)
    pid_path = case.resolved.with_suffix(".pid")
    child_pid = int(pid_path.read_text())
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("worker descendant survived process-group cleanup")


def test_drain_deadline_closes_fds_inherited_by_an_escaped_child(tmp_path: Path) -> None:
    case = _case(tmp_path, mode="escaped_child", timeout_seconds=0.2)

    result = case.run()

    _assert_failure(result, RuntimeFailureCode.WORKER_TIMEOUT)
    child_pid = int(case.resolved.with_suffix(".pid").read_text())
    os.kill(child_pid, 9)


def test_attempt_root_is_immutable_per_attempt(tmp_path: Path) -> None:
    case = _case(tmp_path)
    assert case.run().status is RuntimeExecutionStatus.SUCCEEDED

    with pytest.raises(RuntimeExecutorConfigurationError, match="already exists"):
        case.run()


def test_optional_asset_catalog_is_not_added_to_worker_argv(tmp_path: Path) -> None:
    case = _case(tmp_path)
    case = replace(case, job=replace(case.job, asset_catalog=None))

    result = case.run()

    assert result.status is RuntimeExecutionStatus.SUCCEEDED


def test_observer_must_be_callable(tmp_path: Path) -> None:
    case = _case(tmp_path)

    with pytest.raises(RuntimeExecutorConfigurationError, match="observer"):
        case.executor.execute(case.job, None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"run_id": "bad/path"}, "run_id"),
        ({"run_id": "a..b"}, "run_id"),
        ({"attempt": 0}, "attempt"),
        ({"attempt": True}, "attempt"),
        ({"task_config": "bad/path"}, "task_config"),
        ({"task_config": "a..b"}, "task_config"),
        ({"expected_capability_sha256": "ABC"}, "expected_capability"),
        ({"expected_capability_sha256": None}, "expected_capability"),
        (
            {"expected_runtime_asset_snapshot_sha256": "ABC"},
            "expected_runtime_asset_snapshot",
        ),
        (
            {"expected_runtime_asset_snapshot_sha256": None},
            "expected_runtime_asset_snapshot",
        ),
        ({"precheck_steps": -1}, "precheck_steps"),
        ({"video_frames": True}, "video_frames"),
        ({"min_visible_pixels": -1}, "min_visible_pixels"),
        ({"settle_steps": 0}, "settle_steps"),
        ({"contact_window_steps": True}, "contact_window_steps"),
        ({"fps": 0}, "fps"),
        ({"checkpoint_steps": 0}, "checkpoint_steps"),
    ],
)
def test_invalid_jobs_are_rejected_before_creating_an_attempt(
    tmp_path: Path,
    changes: dict[str, object],
    message: str,
) -> None:
    case = _case(tmp_path)
    job = replace(case.job, **changes)

    with pytest.raises(RuntimeExecutorConfigurationError, match=message):
        case.executor.execute(job, lambda event: None)
    assert not case.executor.work_root.exists()


def test_execute_requires_runtime_job_instance(tmp_path: Path) -> None:
    case = _case(tmp_path)

    with pytest.raises(RuntimeExecutorConfigurationError, match="RuntimeJob"):
        case.executor.execute(object(), lambda event: None)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["robotwin_root", "resolved_scene", "asset_catalog"])
def test_input_paths_reject_symlinks(tmp_path: Path, field: str) -> None:
    case = _case(tmp_path)
    original = getattr(case.job, field)
    assert original is not None
    link = tmp_path / f"{field}-link"
    link.symlink_to(original, target_is_directory=field == "robotwin_root")
    job = replace(case.job, **{field: link})

    with pytest.raises(RuntimeExecutorConfigurationError, match=field):
        case.executor.execute(job, lambda event: None)


def test_filesystem_inputs_require_path_objects(tmp_path: Path) -> None:
    case = _case(tmp_path)
    job = replace(case.job, resolved_scene=str(case.resolved))

    with pytest.raises(RuntimeExecutorConfigurationError, match="pathlib.Path"):
        case.executor.execute(job, lambda event: None)


def test_task_config_cannot_escape_through_parent_symlink(tmp_path: Path) -> None:
    case = _case(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "demo_clean.yml").write_text("{}\n", encoding="utf-8")
    task_directory = case.robotwin / "task_config"
    for item in task_directory.iterdir():
        item.unlink()
    task_directory.rmdir()
    task_directory.symlink_to(outside, target_is_directory=True)

    result = case.run()

    _assert_failure(result, RuntimeFailureCode.CAPABILITY_PROTOCOL_ERROR)


@pytest.mark.parametrize("kind", ["work_file", "work_symlink", "run_file", "run_symlink"])
def test_attempt_roots_reject_files_and_symlinks(tmp_path: Path, kind: str) -> None:
    case = _case(tmp_path)
    work_root = case.executor.work_root
    target = tmp_path / "target"
    target.mkdir()
    if kind == "work_file":
        work_root.parent.mkdir(parents=True, exist_ok=True)
        work_root.write_text("unsafe")
    elif kind == "work_symlink":
        work_root.parent.mkdir(parents=True, exist_ok=True)
        work_root.symlink_to(target, target_is_directory=True)
    else:
        work_root.mkdir(parents=True)
        run_root = work_root / case.job.run_id
        if kind == "run_file":
            run_root.write_text("unsafe")
        else:
            run_root.symlink_to(target, target_is_directory=True)

    with pytest.raises(RuntimeExecutorConfigurationError, match="root"):
        case.run()


def test_constructor_rejects_missing_or_nonregular_fixed_files(tmp_path: Path) -> None:
    runner = tmp_path / "runner.py"
    _write_fake_runner(runner)
    module_root = tmp_path / "module"
    module_root.mkdir()
    common = {
        "runner": runner,
        "work_root": tmp_path / "runs",
        "timeout_seconds": 1,
        "module_root": module_root,
    }

    with pytest.raises(RuntimeExecutorConfigurationError, match="interpreter does not exist"):
        SubprocessRoboTwinRuntimeExecutor(interpreter=tmp_path / "missing", **common)
    with pytest.raises(RuntimeExecutorConfigurationError, match="runner must be a regular file"):
        SubprocessRoboTwinRuntimeExecutor(
            interpreter=Path(sys.executable),
            **{**common, "runner": module_root},
        )
    nonexecutable = tmp_path / "not-executable"
    nonexecutable.write_text("x")
    with pytest.raises(RuntimeExecutorConfigurationError, match="interpreter must be executable"):
        SubprocessRoboTwinRuntimeExecutor(interpreter=nonexecutable, **common)


def test_executor_identity_binds_policy_but_not_work_locator(tmp_path: Path) -> None:
    case = _case(tmp_path)
    identity = case.executor.identity
    document = json.loads(identity.canonical_bytes)

    assert identity.schema_version == "harness.runtime_executor_identity.v1"
    assert identity.sha256 == hashlib.sha256(identity.canonical_bytes).hexdigest()
    assert document["event_protocol"]["schema_version"] == "harness.runtime_event.v1"
    assert document["limits"]["max_event_bytes"] == case.executor.codec.max_event_bytes
    assert str(tmp_path).encode() not in identity.canonical_bytes

    relocated = SubprocessRoboTwinRuntimeExecutor(
        interpreter=Path(sys.executable),
        runner=case.runner,
        module_root=tmp_path,
        work_root=tmp_path / "relocated-runs",
        timeout_seconds=3,
        capability_timeout_seconds=2,
        terminate_grace_seconds=0.1,
        max_stdout_bytes=4096,
        max_stderr_bytes=4096,
        max_event_bytes=2048,
        max_transcript_bytes=4096,
        max_capability_bytes=16_384,
    )
    changed_policy = SubprocessRoboTwinRuntimeExecutor(
        interpreter=Path(sys.executable),
        runner=case.runner,
        module_root=tmp_path,
        work_root=tmp_path / "changed-policy-runs",
        timeout_seconds=4,
        capability_timeout_seconds=2,
        terminate_grace_seconds=0.1,
        max_stdout_bytes=4096,
        max_stderr_bytes=4096,
        max_event_bytes=2048,
        max_transcript_bytes=4096,
        max_capability_bytes=16_384,
    )

    assert relocated.identity == identity
    assert changed_policy.identity.sha256 != identity.sha256


def test_capability_spawn_failure_is_returned_without_running_worker(tmp_path: Path) -> None:
    case = _case(tmp_path)
    temporary_interpreter = tmp_path / "temporary-python"
    os.link(Path(sys.executable).resolve(), temporary_interpreter)
    executor = SubprocessRoboTwinRuntimeExecutor(
        interpreter=temporary_interpreter,
        runner=case.runner,
        work_root=tmp_path / "other-runs",
        timeout_seconds=1,
    )
    temporary_interpreter.unlink()

    result = executor.execute(case.job, lambda event: None)

    _assert_failure(result, RuntimeFailureCode.CAPABILITY_PROCESS_FAILED)


def test_worker_spawn_failure_is_captured_after_valid_preflight(tmp_path: Path) -> None:
    case = _case(tmp_path)
    temporary_interpreter = tmp_path / "temporary-python"
    os.link(Path(sys.executable).resolve(), temporary_interpreter)
    executor = SubprocessRoboTwinRuntimeExecutor(
        interpreter=temporary_interpreter,
        runner=case.runner,
        work_root=tmp_path / "other-runs",
        timeout_seconds=1,
    )
    (case.robotwin / "capability_mode").write_text(
        "delete_interpreter\n",
        encoding="utf-8",
    )

    result = executor.execute(case.job, lambda event: None)

    _assert_failure(result, RuntimeFailureCode.CAPABILITY_PROCESS_FAILED)
    assert result.events == ()


@pytest.mark.parametrize(
    "changes",
    [
        {"timeout_seconds": 0},
        {"capability_timeout_seconds": True},
        {"terminate_grace_seconds": "bad"},
        {"max_stdout_bytes": 0},
        {"max_stderr_bytes": True},
        {"max_capability_bytes": 0},
    ],
)
def test_constructor_limits_are_positive_and_strict(
    tmp_path: Path,
    changes: dict[str, object],
) -> None:
    runner = tmp_path / "runner.py"
    _write_fake_runner(runner)
    arguments = {
        "interpreter": Path(sys.executable),
        "runner": runner,
        "work_root": tmp_path / "runs",
        "timeout_seconds": 1,
        **changes,
    }

    with pytest.raises(RuntimeExecutorConfigurationError, match="positive"):
        SubprocessRoboTwinRuntimeExecutor(**arguments)
