from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Callable

import numpy as np
import pytest

from scene_gen.catalog import load_catalog
from scene_gen.parser import parse_rule_based
from scene_gen.schema import ResolvedSceneSpec
from scene_gen.solver import solve_scene
from self_improving.harness import runtime_capability
from self_improving.harness.runtime_assets import (
    RUNTIME_ASSET_SNAPSHOT_SCHEMA,
    RuntimeAssetSnapshotError,
    canonical_runtime_asset_manifest_bytes,
)
from self_improving.harness.runtime_events import (
    RuntimeEventCodec,
    RuntimeEventProtocolError,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "script" / "run_scene_runtime.py"

spec = importlib.util.spec_from_file_location("runtime_worker_contract", SCRIPT)
assert spec is not None and spec.loader is not None
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)

EVENT_ARTIFACT_PATHS = (
    "preview_head.png",
    "preview_segmentation.png",
    "preview_world_left.png",
    "preview_world_right.png",
    "observer_start.png",
    "observer_mid.png",
    "observer_end.png",
    "observer_runtime.mp4",
    "runtime_evidence.json",
    "runtime_validation_report.json",
)


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Runtime Contract Test",
            "GIT_AUTHOR_EMAIL": "runtime@example.invalid",
            "GIT_COMMITTER_NAME": "Runtime Contract Test",
            "GIT_COMMITTER_EMAIL": "runtime@example.invalid",
        },
    )
    return completed.stdout.strip()


def _robotwin_checkout(tmp_path: Path) -> Path:
    root = tmp_path / "robotwin"
    config = root / "task_config" / "demo_clean.yml"
    config.parent.mkdir(parents=True)
    config.write_text("embodiment: [aloha]\n", encoding="utf-8")
    _runtime_resource_fixture(root)
    (root / "tracked.txt").write_text("tracked-v1\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "fixture")
    (root / "tracked.txt").write_text("tracked-v2\n", encoding="utf-8")
    (root / "untracked.bin").write_bytes(b"untracked-v1\x00")
    (root / "untracked-link").symlink_to("tracked.txt")
    return root


def _runtime_checkout(root: Path) -> Path:
    config = root / "task_config" / "demo_clean.yml"
    config.parent.mkdir(parents=True)
    config.write_text("embodiment: [aloha]\n", encoding="utf-8")
    _runtime_resource_fixture(root)
    (root / "tracked.txt").write_text("runtime-fixture\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "runtime fixture")
    return root


def _runtime_resource_fixture(root: Path) -> None:
    registry = root / "env_cfg" / "task_config" / "_embodiment_config.yml"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        "aloha:\n  file_path: ./assets/embodiments/aloha\n",
        encoding="utf-8",
    )
    embodiment = root / "assets" / "embodiments" / "aloha"
    (embodiment / "urdf" / "meshes").mkdir(parents=True)
    (embodiment / "srdf").mkdir()
    (embodiment / "config.yml").write_text(
        "urdf_path: ./urdf/robot.urdf\nsrdf_path: ./srdf/robot.srdf\nplanner: curobo\n",
        encoding="utf-8",
    )
    (embodiment / "urdf" / "robot.urdf").write_text(
        '<robot name="fixture"><link name="base"><visual><geometry>'
        '<mesh filename="meshes/base.stl"/></geometry></visual></link></robot>\n',
        encoding="utf-8",
    )
    (embodiment / "urdf" / "meshes" / "base.stl").write_bytes(b"solid fixture\n")
    (embodiment / "srdf" / "robot.srdf").write_text(
        '<robot name="fixture"/>\n',
        encoding="utf-8",
    )
    (embodiment / "curobo_left.yml").write_text("robot: left\n", encoding="utf-8")
    (embodiment / "curobo_right.yml").write_text("robot: right\n", encoding="utf-8")
    objects = root / "assets" / "objects"
    (objects / "objaverse").mkdir(parents=True)
    (objects / "objaverse" / "list.json").write_text(
        '{"item_names":[],"list_of_items":{},"z_max":{},"radius":{},"z_offset":{}}\n',
        encoding="utf-8",
    )
    (objects / "same.json").write_text("{}\n", encoding="utf-8")
    (root / ".gitignore").write_text("assets/*\n", encoding="utf-8")


class _Distribution:
    def __init__(self, name: str, version: str | None = None) -> None:
        self.version = version or f"{name}-test"
        self.name = name

    def read_text(self, filename: str) -> str | None:
        if filename == "RECORD":
            return f"{self.name}/module.py,sha256=fixture,1\n"
        if filename == "direct_url.json" and self.name == "nvidia-curobo":
            return '{"url":"file:///redacted"}\n'
        return None


@pytest.fixture(autouse=True)
def _runtime_capability_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime_capability.metadata,
        "distribution",
        lambda name: _Distribution(name),
    )
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_bytes(b"ffmpeg-test")
    monkeypatch.setitem(
        sys.modules,
        "imageio_ffmpeg",
        SimpleNamespace(get_ffmpeg_exe=lambda: str(ffmpeg)),
    )
    nvidia_smi = tmp_path / "nvidia-smi"
    nvidia_smi.write_bytes(b"nvidia-smi-test")
    monkeypatch.setattr(
        runtime_capability.shutil,
        "which",
        lambda name: str(nvidia_smi) if name == "nvidia-smi" else None,
    )
    original_command = runtime_capability._bounded_command

    def bounded_command(arguments: tuple[str, ...], **kwargs: Any) -> Any:
        if len(arguments) >= 2 and arguments[1] == "-I":
            return runtime_capability._CommandResult(
                stdout=None,
                stdout_sha256=hashlib.sha256(b"").hexdigest(),
                stdout_bytes=0,
            )
        if arguments[0] == str(nvidia_smi):
            payload = b"GPU-test, Test GPU, 560.1\n"
            return runtime_capability._CommandResult(
                stdout=payload,
                stdout_sha256=hashlib.sha256(payload).hexdigest(),
                stdout_bytes=len(payload),
            )
        return original_command(arguments, **kwargs)

    monkeypatch.setattr(runtime_capability, "_bounded_command", bounded_command)


def _capability_sha256(module: Any, robotwin_root: Path) -> str:
    capability = module.describe_runtime_capabilities(
        robotwin_root=robotwin_root,
        task_config="demo_clean",
    )
    canonical = (
        json.dumps(
            capability,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def test_describe_capabilities_is_deterministic_attests_dirty_tree_and_never_starts_simulation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    robotwin_root = _robotwin_checkout(tmp_path)
    first_path = tmp_path / "capability-first.json"
    second_path = tmp_path / "capability-second.json"
    versions = {name: f"{name}.test" for name in runtime_capability.REQUIRED_RUNTIME_DISTRIBUTIONS}
    monkeypatch.setattr(
        runtime_capability.metadata,
        "distribution",
        lambda name: _Distribution(name, versions[name]),
    )
    monkeypatch.setattr(
        runtime,
        "load_robotwin_args",
        lambda *_: pytest.fail("describe mode loaded the simulation configuration"),
    )
    monkeypatch.setattr(
        runtime,
        "load_resolved_scene",
        lambda *_: pytest.fail("describe mode loaded scene assets"),
    )

    class ForbiddenBaseTask:
        def __init__(self) -> None:
            pytest.fail("describe mode instantiated the simulator")

    common = [
        "--robotwin-root",
        str(robotwin_root),
        "--task-config",
        "demo_clean",
    ]
    assert (
        runtime.main(
            [*common, "--describe-capabilities", str(first_path)],
            base_task_class=ForbiddenBaseTask,
        )
        == 0
    )
    assert (
        runtime.main(
            [*common, "--describe-capabilities", str(second_path)],
            base_task_class=ForbiddenBaseTask,
        )
        == 0
    )

    first_bytes = first_path.read_bytes()
    assert first_bytes == second_path.read_bytes()
    capability = json.loads(first_bytes)
    runtime_capability.validate_runtime_capability_document(capability)
    assert capability["schema_version"] == "harness.robotwin_runtime_capability.v1"
    assert capability["protocol_version"] == 1
    assert capability["backend_id"] == "robotwin.sapien"
    assert {name: value["version"] for name, value in capability["packages"].items()} == versions
    assert capability["robotwin"]["commit"] == _git(robotwin_root, "rev-parse", "HEAD")
    assert capability["robotwin"]["dirty"] is True
    assert capability["task_config"]["path"] == "task_config/demo_clean.yml"
    assert capability["supported"]["event_protocol"]["artifact_paths"] == list(EVENT_ARTIFACT_PATHS)
    assert re.fullmatch(r"[0-9a-f]{64}", capability["runner_sha256"])
    assert re.fullmatch(r"[0-9a-f]{64}", capability["python"]["executable_sha256"])
    assert re.fullmatch(
        r"[0-9a-f]{64}",
        capability["source_modules"]["scene_gen"]["tree_sha256"],
    )
    assert re.fullmatch(
        r"[0-9a-f]{64}",
        capability["source_modules"]["runtime_events"]["sha256"],
    )
    assert re.fullmatch(r"[0-9a-f]{64}", capability["robotwin"]["tree_state_sha256"])
    serialized = first_bytes.decode("utf-8")
    assert str(tmp_path) not in serialized
    assert "timestamp" not in serialized.lower()

    original_tree_digest = capability["robotwin"]["tree_state_sha256"]
    (robotwin_root / "untracked.bin").write_bytes(b"untracked-v2\x00")
    assert (
        runtime.main(
            [*common, "--describe-capabilities", str(second_path)],
            base_task_class=ForbiddenBaseTask,
        )
        == 0
    )
    changed = json.loads(second_path.read_bytes())
    assert changed["robotwin"]["commit"] == capability["robotwin"]["commit"]
    assert (
        changed["robotwin"]["tracked_diff_sha256"] == capability["robotwin"]["tracked_diff_sha256"]
    )
    assert (
        changed["robotwin"]["untracked_manifest_sha256"]
        != capability["robotwin"]["untracked_manifest_sha256"]
    )
    assert changed["robotwin"]["tree_state_sha256"] != original_tree_digest


def test_source_module_attestation_excludes_bytecode_and_rejects_nonregular_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    scene_gen_root = tmp_path / "sources" / "scene_gen"
    cache = scene_gen_root / "__pycache__"
    cache.mkdir(parents=True)
    (scene_gen_root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (scene_gen_root / "ignored.pyc").write_bytes(b"bytecode-one")
    cached_bytecode = cache / "module.cpython-test.pyc"
    cached_bytecode.write_bytes(b"cached-bytecode-one")
    runtime_events_path = tmp_path / "sources" / "runtime_events.py"
    runtime_events_path.write_text("SCHEMA = 'v1'\n", encoding="utf-8")
    python_executable = tmp_path / "python-runtime"
    python_executable.write_bytes(b"python-runtime-one")
    monkeypatch.setattr(runtime_capability, "SCENE_GEN_SOURCE_ROOT", scene_gen_root)
    monkeypatch.setattr(runtime_capability, "RUNTIME_EVENTS_SOURCE_PATH", runtime_events_path)
    monkeypatch.setattr(runtime_capability, "PYTHON_EXECUTABLE_PATH", python_executable)

    first = runtime.describe_runtime_capabilities(
        robotwin_root=robotwin_root,
        task_config="demo_clean",
    )
    cached_bytecode.write_bytes(b"cached-bytecode-two")
    (scene_gen_root / "ignored.pyc").write_bytes(b"bytecode-two")
    second = runtime.describe_runtime_capabilities(
        robotwin_root=robotwin_root,
        task_config="demo_clean",
    )

    assert second["source_modules"] == first["source_modules"]
    assert first["python"]["executable_sha256"] == hashlib.sha256(b"python-runtime-one").hexdigest()

    source_link = scene_gen_root / "linked.py"
    source_link.symlink_to("module.py")
    with pytest.raises(RuntimeError, match="scene_gen source contains a symlink"):
        runtime.describe_runtime_capabilities(
            robotwin_root=robotwin_root,
            task_config="demo_clean",
        )
    source_link.unlink()

    unsupported = scene_gen_root / "unsupported"
    os.mkfifo(unsupported)
    with pytest.raises(RuntimeError, match="scene_gen source contains a non-regular entry"):
        runtime.describe_runtime_capabilities(
            robotwin_root=robotwin_root,
            task_config="demo_clean",
        )
    unsupported.unlink()

    source_root_link = tmp_path / "scene-gen-link"
    source_root_link.symlink_to(scene_gen_root, target_is_directory=True)
    monkeypatch.setattr(runtime_capability, "SCENE_GEN_SOURCE_ROOT", source_root_link)
    with pytest.raises(RuntimeError, match="scene_gen source root must be a real directory"):
        runtime.describe_runtime_capabilities(
            robotwin_root=robotwin_root,
            task_config="demo_clean",
        )

    monkeypatch.setattr(runtime_capability, "SCENE_GEN_SOURCE_ROOT", scene_gen_root)
    runtime_events_path.unlink()
    runtime_events_path.symlink_to(scene_gen_root / "module.py")
    with pytest.raises(RuntimeError, match="runtime event source must be a real regular file"):
        runtime.describe_runtime_capabilities(
            robotwin_root=robotwin_root,
            task_config="demo_clean",
        )


def test_describe_capabilities_fails_closed_for_missing_package_and_reports_clean_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    robotwin_root = _robotwin_checkout(tmp_path)
    _git(robotwin_root, "add", ".")
    _git(robotwin_root, "commit", "-qm", "clean fixture")

    def missing_package(distribution: str) -> _Distribution:
        if distribution == "nvidia-curobo":
            raise runtime_capability.metadata.PackageNotFoundError(distribution)
        return _Distribution(distribution)

    monkeypatch.setattr(runtime_capability.metadata, "distribution", missing_package)
    with pytest.raises(
        RuntimeError, match="required runtime distribution is missing: nvidia-curobo"
    ):
        runtime.describe_runtime_capabilities(
            robotwin_root=robotwin_root,
            task_config="demo_clean",
        )
    monkeypatch.setattr(
        runtime_capability.metadata,
        "distribution",
        lambda name: _Distribution(name),
    )
    capability = runtime.describe_runtime_capabilities(
        robotwin_root=robotwin_root,
        task_config="demo_clean",
    )

    assert capability["packages"]["nvidia-curobo"]["version"] == "nvidia-curobo-test"
    assert capability["robotwin"]["dirty"] is False
    assert capability["robotwin"]["tracked_diff_sha256"] == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    assert capability["robotwin"]["untracked_manifest_sha256"] == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_describe_capabilities_fails_closed_for_non_git_tree(
    tmp_path: Path,
) -> None:
    non_git_root = tmp_path / "non-git"
    config = non_git_root / "task_config" / "demo_clean.yml"
    config.parent.mkdir(parents=True)
    config.write_text("embodiment: [aloha]\n", encoding="utf-8")
    _runtime_resource_fixture(non_git_root)
    with pytest.raises(RuntimeError, match="cannot attest RoboTwin git state"):
        runtime.describe_runtime_capabilities(
            robotwin_root=non_git_root,
            task_config="demo_clean",
        )


def test_describe_capabilities_rejects_non_commit_git_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    robotwin_root = tmp_path / "robotwin"
    config = robotwin_root / "task_config" / "demo_clean.yml"
    config.parent.mkdir(parents=True)
    config.write_text("embodiment: [aloha]\n", encoding="utf-8")
    _runtime_resource_fixture(robotwin_root)
    monkeypatch.setattr(
        runtime_capability,
        "_git_bytes",
        lambda *_args: b"not-a-commit\n",
    )
    with pytest.raises(RuntimeError, match="not a hexadecimal commit"):
        runtime.describe_runtime_capabilities(
            robotwin_root=robotwin_root,
            task_config="demo_clean",
        )


def test_describe_capabilities_rejects_config_escape_and_non_root_checkout(
    tmp_path: Path,
) -> None:
    robotwin_root = _robotwin_checkout(tmp_path)
    config = robotwin_root / "task_config" / "demo_clean.yml"
    config.unlink()
    outside = tmp_path / "outside.yml"
    outside.write_text("embodiment: [outside]\n", encoding="utf-8")
    config.symlink_to(outside)
    with pytest.raises(RuntimeError, match="task config contains a symlink"):
        runtime.describe_runtime_capabilities(
            robotwin_root=robotwin_root,
            task_config="demo_clean",
        )

    config.unlink()
    config.write_text("embodiment: [aloha]\n", encoding="utf-8")
    nested_root = robotwin_root / "nested"
    nested_config = nested_root / "task_config" / "demo_clean.yml"
    nested_config.parent.mkdir(parents=True)
    nested_config.write_text("embodiment: [nested]\n", encoding="utf-8")
    _runtime_resource_fixture(nested_root)
    (nested_root / "env_cfg" / "task_config" / "_embodiment_config.yml").write_text(
        "nested:\n  file_path: ./assets/embodiments/aloha\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="repository root"):
        runtime.describe_runtime_capabilities(
            robotwin_root=nested_root,
            task_config="demo_clean",
        )


@pytest.mark.parametrize("expected", [None, "z" * 64, "a" * 63])
def test_harness_execution_requires_a_valid_expected_capability_before_any_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    expected: str | None,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    base_task, tasks = _fake_runtime(monkeypatch)
    read_fd, write_fd = os.pipe()
    arguments = [
        "--robotwin-root",
        str(robotwin_root),
        "--resolved-scene",
        str(resolved_path),
        "--out-dir",
        str(tmp_path / "output"),
        "--event-fd",
        str(write_fd),
    ]
    if expected is not None:
        arguments.extend(("--expected-capability-sha256", expected))
    try:
        with pytest.raises(SystemExit) as error:
            runtime.main(arguments, base_task_class=base_task)
        os.close(write_fd)
        write_fd = -1
        transcript = os.read(read_fd, 64 * 1024)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)

    assert error.value.code == 2
    assert transcript == b""
    assert tasks == []


@pytest.mark.parametrize(
    "drift",
    ["robotwin", "task_config", "scene_gen", "runtime_events"],
)
def test_harness_execution_rejects_capability_drift_before_any_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    scene_gen_root = tmp_path / "sources" / "scene_gen"
    scene_gen_root.mkdir(parents=True)
    scene_gen_source = scene_gen_root / "schema.py"
    scene_gen_source.write_text("SCHEMA = 'v1'\n", encoding="utf-8")
    runtime_events_path = tmp_path / "sources" / "runtime_events.py"
    runtime_events_path.write_text("EVENT_SCHEMA = 'v1'\n", encoding="utf-8")
    monkeypatch.setattr(runtime_capability, "SCENE_GEN_SOURCE_ROOT", scene_gen_root)
    monkeypatch.setattr(runtime_capability, "RUNTIME_EVENTS_SOURCE_PATH", runtime_events_path)
    capability_path = tmp_path / "runtime-capability.json"
    assert (
        runtime.main(
            [
                "--robotwin-root",
                str(robotwin_root),
                "--describe-capabilities",
                str(capability_path),
            ]
        )
        == 0
    )
    expected = hashlib.sha256(capability_path.read_bytes()).hexdigest()
    if drift == "robotwin":
        (robotwin_root / "tracked.txt").write_text("drifted runtime\n", encoding="utf-8")
    elif drift == "task_config":
        (robotwin_root / "task_config" / "demo_clean.yml").write_text(
            "embodiment: [aloha]\nepisode_num: 99\n",
            encoding="utf-8",
        )
    elif drift == "scene_gen":
        scene_gen_source.write_text("SCHEMA = 'v2'\n", encoding="utf-8")
    else:
        runtime_events_path.write_text("EVENT_SCHEMA = 'v2'\n", encoding="utf-8")
    base_task, tasks = _fake_runtime(monkeypatch)
    read_fd, write_fd = os.pipe()
    try:
        with pytest.raises(RuntimeError, match="capability digest does not match"):
            runtime.main(
                _runtime_arguments(
                    robotwin_root=robotwin_root,
                    resolved_path=resolved_path,
                    out_dir=tmp_path / "output",
                    event_fd=write_fd,
                    expected_capability_sha256=expected,
                ),
                base_task_class=base_task,
            )
        os.close(write_fd)
        write_fd = -1
        transcript = os.read(read_fd, 64 * 1024)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)

    assert transcript == b""
    assert tasks == []
    assert not (tmp_path / "output").exists()


def test_harness_execution_rejects_runner_drift_before_import_or_event(
    tmp_path: Path,
) -> None:
    copied_script = tmp_path / "run_scene_runtime.py"
    shutil.copy2(SCRIPT, copied_script)
    copied_spec = importlib.util.spec_from_file_location("drifted_runtime_worker", copied_script)
    assert copied_spec is not None and copied_spec.loader is not None
    copied_runtime = importlib.util.module_from_spec(copied_spec)
    copied_spec.loader.exec_module(copied_runtime)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    capability_path = tmp_path / "runtime-capability.json"
    assert (
        copied_runtime.main(
            [
                "--robotwin-root",
                str(robotwin_root),
                "--describe-capabilities",
                str(capability_path),
            ]
        )
        == 0
    )
    expected = hashlib.sha256(capability_path.read_bytes()).hexdigest()
    copied_script.write_text(
        copied_script.read_text(encoding="utf-8") + "\n# runner drift\n",
        encoding="utf-8",
    )

    class ForbiddenBaseTask:
        def __init__(self) -> None:
            pytest.fail("capability mismatch imported or instantiated the simulator")

    read_fd, write_fd = os.pipe()
    try:
        with pytest.raises(RuntimeError, match="capability digest does not match"):
            copied_runtime.main(
                [
                    "--robotwin-root",
                    str(robotwin_root),
                    "--resolved-scene",
                    str(tmp_path / "must-not-be-read.json"),
                    "--out-dir",
                    str(tmp_path / "output"),
                    "--event-fd",
                    str(write_fd),
                    "--expected-capability-sha256",
                    expected,
                    "--runtime-asset-root",
                    str(tmp_path / "must-not-be-read-assets"),
                    "--runtime-asset-manifest",
                    str(tmp_path / "must-not-be-read-manifest.json"),
                    "--expected-runtime-asset-snapshot-sha256",
                    "0" * 64,
                ],
                base_task_class=ForbiddenBaseTask,
            )
        os.close(write_fd)
        write_fd = -1
        transcript = os.read(read_fd, 64 * 1024)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)

    assert transcript == b""
    assert not (tmp_path / "output").exists()


def test_unreachable_adapter_inputs_still_fail_closed(
    tmp_path: Path,
) -> None:
    unsupported = tmp_path / "unsupported"
    unsupported.mkdir()
    with pytest.raises(RuntimeError, match="not a regular file or symlink"):
        runtime_capability._untracked_manifest(tmp_path, b"unsupported\0")
    with pytest.raises(ValueError, match="unsupported runtime event protocol"):
        runtime._runtime_event_emitter(
            event_fd=None,
            event_protocol="harness.runtime_event.v999",
        )


@dataclass
class _Pose:
    p: np.ndarray
    q: np.ndarray


class _Actor:
    def __init__(self, *, identifier: int, position: tuple[float, float, float], scene: Any):
        self.per_scene_id = identifier
        self._position = np.asarray(position, dtype=float)
        self._scene = scene

    def get_pose(self) -> _Pose:
        moved = self._position + np.asarray([self._scene.step_count * 0.002, 0.0, 0.0])
        return _Pose(p=moved, q=np.asarray([1.0, 0.0, 0.0, 0.0]))

    def get_qpos(self) -> np.ndarray:
        return np.asarray([], dtype=float)


class _FakeScene:
    def __init__(self, *, fail_at_step: int | None = None):
        self.step_count = 0
        self.fail_at_step = fail_at_step

    def step(self) -> None:
        self.step_count += 1
        if self.step_count == self.fail_at_step:
            raise RuntimeError("injected acquisition failure")

    def get_contacts(self) -> list[Any]:
        return []

    def update_render(self) -> None:
        return None


class _PictureCamera:
    def __init__(self, *, label: int = 1):
        self._label = label

    def take_picture(self) -> None:
        return None

    def get_picture(self, kind: str) -> np.ndarray:
        if kind == "Segmentation":
            result = np.zeros((4, 4, 2), dtype=np.int64)
            result[..., 1] = self._label
            return result
        if kind == "Color":
            return np.full((4, 4, 4), 0.5, dtype=float)
        raise AssertionError(f"unexpected picture kind {kind}")


class _FakeCameras:
    def __init__(self, scene: _FakeScene):
        self._scene = scene
        self.static_camera_list = [_PictureCamera(label=1)]
        self.static_camera_name = ["head_camera"]
        self.world_camera1 = _PictureCamera()
        self.world_camera2 = _PictureCamera()

    def update_picture(self) -> None:
        return None

    def get_rgb(self) -> dict[str, dict[str, np.ndarray]]:
        return {"head_camera": {"rgb": np.full((4, 4, 3), 64, dtype=np.uint8)}}

    def get_observer_rgb(self) -> np.ndarray:
        return np.full((4, 4, 3), self._scene.step_count % 255, dtype=np.uint8)


def _resolved_scene(path: Path) -> Any:
    catalog = load_catalog(ROOT / "tests" / "fixtures" / "asset_catalog.json")
    scene_spec = parse_rule_based(
        "A red can is left of a plastic basket near the center.",
        seed=23,
    )
    resolved = solve_scene(scene_spec, catalog)
    path.write_text(resolved.model_dump_json(), encoding="utf-8")
    return resolved


def _fake_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_at_step: int | None = None,
    on_close: Callable[[], None] | None = None,
) -> tuple[type[Any], list[Any]]:
    tasks: list[Any] = []

    class FakeBaseTask:
        def __init__(self) -> None:
            self.scene = _FakeScene(fail_at_step=fail_at_step)
            self.cameras = _FakeCameras(self.scene)
            self.closed = False
            tasks.append(self)

        def _init_task_env_(self, **kwargs: Any) -> None:
            del kwargs
            self.load_actors()
            assert self.check_stable() == (True, [])

        def _update_render(self) -> None:
            return None

        def close_env(self, *, clear_cache: bool) -> None:
            assert clear_cache is True
            if on_close is not None:
                on_close()
            self.closed = True

    def load_scene(
        task: Any,
        resolved: Any,
        *,
        asset_roots: dict[str, Path] | None = None,
    ) -> dict[str, _Actor]:
        task.runtime_asset_roots = asset_roots
        return {
            item.object_id: _Actor(
                identifier=index,
                position=item.pose.position_m,
                scene=task.scene,
            )
            for index, item in enumerate(resolved.objects, start=1)
        }

    monkeypatch.setattr(runtime, "load_robotwin_args", lambda *_: {})
    monkeypatch.setattr(runtime, "load_resolved_scene", load_scene)
    monkeypatch.setattr(
        runtime.imageio,
        "mimsave",
        lambda path, *_args, **_kwargs: Path(path).write_bytes(b"fake-mp4"),
    )
    return FakeBaseTask, tasks


def _runtime_arguments(
    *,
    robotwin_root: Path,
    resolved_path: Path,
    out_dir: Path,
    event_fd: int,
    evidence_only: bool = True,
    expected_capability_sha256: str | None = None,
    asset_catalog_path: Path | None = None,
) -> list[str]:
    expected_capability_sha256 = expected_capability_sha256 or _capability_sha256(
        runtime,
        robotwin_root,
    )
    arguments = [
        "--robotwin-root",
        str(robotwin_root),
        "--resolved-scene",
        str(resolved_path),
        "--out-dir",
        str(out_dir),
        "--settle-steps",
        "5",
        "--settle-converge-max",
        "3",
        "--contact-window-steps",
        "2",
        "--video-frames",
        "2",
        "--checkpoint-steps",
        "2",
        "--event-protocol",
        "harness.runtime_event.v1",
        "--event-fd",
        str(event_fd),
        "--expected-capability-sha256",
        expected_capability_sha256,
    ]
    arguments.extend(
        _runtime_asset_arguments(
            resolved_path=resolved_path,
            out_dir=out_dir,
            asset_catalog_path=asset_catalog_path,
        )
    )
    if asset_catalog_path is not None:
        arguments.extend(("--asset-catalog", str(asset_catalog_path)))
    if evidence_only:
        arguments.append("--evidence-only")
    return arguments


def _runtime_loader_payload(asset_id: str, relative: str) -> bytes:
    suffix = Path(relative).suffix.lower()
    if suffix == ".glb":
        document = b'{"asset":{"version":"2.0"}}'
        document += b" " * (-len(document) % 4)
        json_chunk = len(document).to_bytes(4, "little") + b"JSON" + document
        return (
            b"glTF"
            + (2).to_bytes(4, "little")
            + (12 + len(json_chunk)).to_bytes(4, "little")
            + json_chunk
        )
    if suffix == ".gltf":
        return b'{"asset":{"version":"2.0"}}\n'
    if suffix == ".urdf":
        return b'<robot name="fixture"/>\n'
    if suffix == ".obj":
        return b"o fixture\n"
    if suffix == ".mtl":
        return b"newmtl fixture\n"
    if suffix == ".dae":
        return b'<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema"/>\n'
    return f"loader-input:{asset_id}:{relative}\n".encode()


def _runtime_asset_arguments(
    *,
    resolved_path: Path,
    out_dir: Path,
    asset_catalog_path: Path | None = None,
) -> list[str]:
    resolved = ResolvedSceneSpec.model_validate_json(resolved_path.read_text(encoding="utf-8"))
    staging = out_dir.parent / f".{out_dir.name}-runtime-assets"
    root = staging / "objects"
    root.mkdir(parents=True)
    assets = []
    selected: dict[str, set[int]] = {}
    for item in resolved.objects:
        selected.setdefault(item.asset_id, set()).add(item.model_id)
    catalog = load_catalog(asset_catalog_path) if asset_catalog_path is not None else None
    for asset_id, model_ids in sorted(selected.items()):
        asset_root = root / asset_id
        asset_root.mkdir()
        required_files = ["loader.bin"]
        if catalog is not None:
            entry = next(value for value in catalog.entries if value.asset_id == asset_id)
            required_files = sorted(
                {
                    Path(source).relative_to(entry.asset_path).as_posix()
                    for model in entry.models
                    if model.model_id in model_ids
                    for source in (
                        model.metadata_path,
                        model.visual_path,
                        model.collision_path,
                        model.urdf_path,
                    )
                    if source is not None
                }
            )
        files = []
        for relative in required_files:
            payload = _runtime_loader_payload(asset_id, relative)
            destination = asset_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
            files.append(
                {
                    "path": relative,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "bytes": len(payload),
                }
            )
        directories = ["."] + sorted(
            {
                parent.as_posix()
                for relative in required_files
                for parent in Path(relative).parents
                if parent.as_posix() != "."
            }
        )
        files.sort(key=lambda value: value["path"])
        assets.append(
            {
                "asset_id": asset_id,
                "selected_model_ids": sorted(model_ids),
                "tree_sha256": hashlib.sha256(
                    canonical_runtime_asset_manifest_bytes(
                        {
                            "directories": directories,
                            "files": files,
                        }
                    )
                ).hexdigest(),
                "file_count": len(files),
                "bytes": sum(value["bytes"] for value in files),
                "directories": directories,
                "required_files": required_files,
                "files": files,
            }
        )
    catalog_sha256 = catalog.digest() if catalog is not None else resolved.asset_catalog_sha256
    manifest = {
        "schema_version": RUNTIME_ASSET_SNAPSHOT_SCHEMA,
        "resolved_scene_sha256": resolved.digest(),
        "asset_catalog_sha256": catalog_sha256,
        "assets": assets,
    }
    manifest_path = staging / "runtime_asset_snapshot.json"
    manifest_path.write_bytes(canonical_runtime_asset_manifest_bytes(manifest))
    return [
        "--runtime-asset-root",
        str(root),
        "--runtime-asset-manifest",
        str(manifest_path),
        "--expected-runtime-asset-snapshot-sha256",
        hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    ]


def test_worker_events_follow_real_boundaries_counts_and_exact_artifact_allowlist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    resolved = _resolved_scene(resolved_path)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    out_dir = tmp_path / "output"
    capability_path = tmp_path / "runtime-capability.json"
    assert (
        runtime.main(
            [
                "--robotwin-root",
                str(robotwin_root),
                "--describe-capabilities",
                str(capability_path),
            ]
        )
        == 0
    )
    expected_capability_sha256 = hashlib.sha256(capability_path.read_bytes()).hexdigest()
    read_fd, write_fd = os.pipe()
    before_close: list[bytes] = []

    def assert_terminal_not_yet_emitted() -> None:
        before_close.append(os.read(read_fd, 64 * 1024))
        events = RuntimeEventCodec(allowed_artifact_paths=EVENT_ARTIFACT_PATHS).parse_transcript(
            before_close[-1]
        )
        assert events[-1].kind.value == "evidence.completed"
        assert all(event.kind.value != "worker.completed" for event in events)

    base_task, tasks = _fake_runtime(
        monkeypatch,
        on_close=assert_terminal_not_yet_emitted,
    )
    verification_states: list[bool | None] = []
    original_verify = runtime.verify_runtime_asset_snapshot

    def trace_verification(**kwargs: Any) -> Any:
        verified = original_verify(**kwargs)
        verification_states.append(tasks[-1].closed if tasks else None)
        return verified

    monkeypatch.setattr(runtime, "verify_runtime_asset_snapshot", trace_verification)
    try:
        exit_code = runtime.main(
            _runtime_arguments(
                robotwin_root=robotwin_root,
                resolved_path=resolved_path,
                out_dir=out_dir,
                event_fd=write_fd,
                expected_capability_sha256=expected_capability_sha256,
            ),
            base_task_class=base_task,
        )
        os.close(write_fd)
        write_fd = -1
        transcript = b"".join(before_close) + os.read(read_fd, 64 * 1024)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)

    codec = RuntimeEventCodec(allowed_artifact_paths=EVENT_ARTIFACT_PATHS)
    events = codec.parse_transcript(transcript)
    assert exit_code == 0
    assert [event.kind.value for event in events] == [
        "preflight.completed",
        "scene.loaded",
        "simulation.started",
        "simulation.checkpoint",
        "simulation.checkpoint",
        "simulation.checkpoint",
        "simulation.checkpoint",
        "simulation.completed",
        "media.completed",
        "evidence.completed",
        "worker.completed",
    ]
    assert [event.completed_steps for event in events if event.completed_steps is not None] == [
        2,
        4,
        6,
        8,
        8,
    ]
    assert events[-3].artifact_paths == (
        "preview_head.png",
        "preview_segmentation.png",
        "preview_world_left.png",
        "preview_world_right.png",
        "observer_start.png",
        "observer_mid.png",
        "observer_end.png",
        "observer_runtime.mp4",
    )
    assert events[-2].artifact_paths == (
        "runtime_evidence.json",
        "runtime_validation_report.json",
    )
    assert all(path in EVENT_ARTIFACT_PATHS for event in events for path in event.artifact_paths)
    assert len(tasks) == 1 and tasks[0].closed is True
    assert verification_states == [None, True]
    assert tasks[0].scene.step_count == 8
    evidence = json.loads((out_dir / "runtime_evidence.json").read_bytes())
    assert evidence["resolved_scene_sha256"] == resolved.digest()
    manifest_path = tmp_path / ".output-runtime-assets" / "runtime_asset_snapshot.json"
    assert (
        evidence["runtime_asset_snapshot_sha256"]
        == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    )
    assert tasks[0].runtime_asset_roots == {
        item.object_id: tmp_path / ".output-runtime-assets" / "objects" / item.asset_id
        for item in resolved.objects
    }
    assert evidence["simulation_step_count"] == 8
    assert evidence["settle_extra_steps"] == 3
    assert json.loads((out_dir / "runtime_validation_report.json").read_bytes())["status"] == (
        "fail"
    )


def test_event_mode_requires_complete_runtime_asset_snapshot_before_any_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    base_task, tasks = _fake_runtime(monkeypatch)
    read_fd, write_fd = os.pipe()
    arguments = _runtime_arguments(
        robotwin_root=robotwin_root,
        resolved_path=resolved_path,
        out_dir=tmp_path / "output",
        event_fd=write_fd,
    )
    index = arguments.index("--runtime-asset-manifest")
    del arguments[index : index + 2]
    try:
        with pytest.raises(SystemExit) as error:
            runtime.main(arguments, base_task_class=base_task)
        os.close(write_fd)
        write_fd = -1
        transcript = os.read(read_fd, 64 * 1024)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)

    assert error.value.code == 2
    assert transcript == b""
    assert tasks == []


def test_event_mode_rejects_legacy_invocation_without_any_asset_snapshot_argument(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    base_task, tasks = _fake_runtime(monkeypatch)
    read_fd, write_fd = os.pipe()
    arguments = _runtime_arguments(
        robotwin_root=robotwin_root,
        resolved_path=resolved_path,
        out_dir=tmp_path / "output",
        event_fd=write_fd,
    )
    for flag in (
        "--runtime-asset-root",
        "--runtime-asset-manifest",
        "--expected-runtime-asset-snapshot-sha256",
    ):
        index = arguments.index(flag)
        del arguments[index : index + 2]
    try:
        with pytest.raises(SystemExit) as error:
            runtime.main(arguments, base_task_class=base_task)
        os.close(write_fd)
        write_fd = -1
        transcript = os.read(read_fd, 64 * 1024)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)

    assert error.value.code == 2
    assert transcript == b""
    assert tasks == []


def test_runtime_asset_manifest_tamper_fails_before_any_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    out_dir = tmp_path / "output"
    base_task, tasks = _fake_runtime(monkeypatch)
    read_fd, write_fd = os.pipe()
    arguments = _runtime_arguments(
        robotwin_root=robotwin_root,
        resolved_path=resolved_path,
        out_dir=out_dir,
        event_fd=write_fd,
    )
    manifest = tmp_path / ".output-runtime-assets" / "runtime_asset_snapshot.json"
    manifest.write_bytes(b'{"tampered":true}\n')
    try:
        with pytest.raises(RuntimeError, match="manifest"):
            runtime.main(arguments, base_task_class=base_task)
        os.close(write_fd)
        write_fd = -1
        transcript = os.read(read_fd, 64 * 1024)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)

    assert transcript == b""
    assert tasks == []


def test_runtime_asset_drift_during_close_prevents_terminal_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    out_dir = tmp_path / "output"
    read_fd, write_fd = os.pipe()

    def drift_snapshot() -> None:
        file_path = next((tmp_path / ".output-runtime-assets" / "objects").rglob("loader.bin"))
        file_path.write_bytes(b"drift-after-evidence\n")

    base_task, tasks = _fake_runtime(monkeypatch, on_close=drift_snapshot)
    try:
        with pytest.raises(RuntimeError, match="tree differs"):
            runtime.main(
                _runtime_arguments(
                    robotwin_root=robotwin_root,
                    resolved_path=resolved_path,
                    out_dir=out_dir,
                    event_fd=write_fd,
                ),
                base_task_class=base_task,
            )
        os.close(write_fd)
        write_fd = -1
        transcript = os.read(read_fd, 64 * 1024)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)

    events = RuntimeEventCodec(allowed_artifact_paths=EVENT_ARTIFACT_PATHS).parse_transcript(
        transcript
    )
    assert events[-1].kind.value == "evidence.completed"
    assert all(event.kind.value != "worker.completed" for event in events)
    assert len(tasks) == 1 and tasks[0].closed is True


def test_runtime_asset_drift_is_primary_even_when_simulation_also_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    out_dir = tmp_path / "output"
    read_fd, write_fd = os.pipe()

    def drift_snapshot() -> None:
        file_path = next((tmp_path / ".output-runtime-assets" / "objects").rglob("loader.bin"))
        file_path.write_bytes(b"drift-plus-runtime-failure\n")

    base_task, tasks = _fake_runtime(
        monkeypatch,
        fail_at_step=1,
        on_close=drift_snapshot,
    )
    try:
        with pytest.raises(RuntimeAssetSnapshotError) as captured:
            runtime.main(
                _runtime_arguments(
                    robotwin_root=robotwin_root,
                    resolved_path=resolved_path,
                    out_dir=out_dir,
                    event_fd=write_fd,
                ),
                base_task_class=base_task,
            )
        os.close(write_fd)
        write_fd = -1
        transcript = os.read(read_fd, 64 * 1024)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)

    assert captured.value.reason == "runtime_asset_tree_drift"
    assert isinstance(captured.value.__cause__, RuntimeError)
    assert "injected acquisition failure" in str(captured.value.__cause__)
    events = RuntimeEventCodec(allowed_artifact_paths=EVENT_ARTIFACT_PATHS).parse_transcript(
        transcript
    )
    assert [event.kind.value for event in events] == [
        "preflight.completed",
        "scene.loaded",
        "simulation.started",
    ]
    assert len(tasks) == 1 and tasks[0].closed is True


def test_runtime_asset_drift_dominates_close_failure_and_retains_it_as_cause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    out_dir = tmp_path / "output"
    read_fd, write_fd = os.pipe()

    def drift_and_fail_close() -> None:
        file_path = next((tmp_path / ".output-runtime-assets" / "objects").rglob("loader.bin"))
        file_path.write_bytes(b"drift-plus-close-failure\n")
        raise RuntimeError("injected close failure after drift")

    base_task, tasks = _fake_runtime(monkeypatch, on_close=drift_and_fail_close)
    try:
        with pytest.raises(RuntimeAssetSnapshotError) as captured:
            runtime.main(
                _runtime_arguments(
                    robotwin_root=robotwin_root,
                    resolved_path=resolved_path,
                    out_dir=out_dir,
                    event_fd=write_fd,
                ),
                base_task_class=base_task,
            )
        os.close(write_fd)
        write_fd = -1
        transcript = os.read(read_fd, 64 * 1024)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)

    assert captured.value.reason == "runtime_asset_tree_drift"
    assert isinstance(captured.value.__cause__, RuntimeError)
    assert "injected close failure after drift" in str(captured.value.__cause__)
    assert any("runtime close also failed" in note for note in captured.value.__notes__)
    events = RuntimeEventCodec(allowed_artifact_paths=EVENT_ARTIFACT_PATHS).parse_transcript(
        transcript
    )
    assert events[-1].kind.value == "evidence.completed"
    assert all(event.kind.value != "worker.completed" for event in events)
    assert len(tasks) == 1 and tasks[0].closed is False


def test_runtime_failure_dominates_close_failure_when_snapshot_remains_trusted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    out_dir = tmp_path / "output"
    read_fd, write_fd = os.pipe()

    def fail_close() -> None:
        raise RuntimeError("injected secondary close failure")

    base_task, tasks = _fake_runtime(monkeypatch, fail_at_step=1, on_close=fail_close)
    try:
        with pytest.raises(RuntimeError, match="injected acquisition failure") as captured:
            runtime.main(
                _runtime_arguments(
                    robotwin_root=robotwin_root,
                    resolved_path=resolved_path,
                    out_dir=out_dir,
                    event_fd=write_fd,
                ),
                base_task_class=base_task,
            )
        os.close(write_fd)
        write_fd = -1
        transcript = os.read(read_fd, 64 * 1024)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)

    assert isinstance(captured.value.__cause__, RuntimeError)
    assert "injected secondary close failure" in str(captured.value.__cause__)
    events = RuntimeEventCodec(allowed_artifact_paths=EVENT_ARTIFACT_PATHS).parse_transcript(
        transcript
    )
    assert [event.kind.value for event in events] == [
        "preflight.completed",
        "scene.loaded",
        "simulation.started",
    ]
    assert len(tasks) == 1 and tasks[0].closed is False


def test_failure_evidence_write_error_still_closes_and_postverifies_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    out_dir = tmp_path / "output"
    read_fd, write_fd = os.pipe()
    base_task, tasks = _fake_runtime(monkeypatch, fail_at_step=1)
    verification_states: list[bool | None] = []
    original_verify = runtime.verify_runtime_asset_snapshot

    def trace_verification(**kwargs: Any) -> Any:
        verified = original_verify(**kwargs)
        verification_states.append(tasks[-1].closed if tasks else None)
        return verified

    def fail_evidence_write(_path: Path, _value: Any) -> None:
        raise RuntimeError("injected failure-evidence write failure")

    monkeypatch.setattr(runtime, "verify_runtime_asset_snapshot", trace_verification)
    monkeypatch.setattr(runtime, "write_json", fail_evidence_write)
    try:
        with pytest.raises(RuntimeError, match="failure-evidence write failure") as captured:
            runtime.main(
                _runtime_arguments(
                    robotwin_root=robotwin_root,
                    resolved_path=resolved_path,
                    out_dir=out_dir,
                    event_fd=write_fd,
                ),
                base_task_class=base_task,
            )
        os.close(write_fd)
        write_fd = -1
        transcript = os.read(read_fd, 64 * 1024)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)

    assert any("original runtime failure" in note for note in captured.value.__notes__)
    assert verification_states == [None, True]
    events = RuntimeEventCodec(allowed_artifact_paths=EVENT_ARTIFACT_PATHS).parse_transcript(
        transcript
    )
    assert [event.kind.value for event in events] == [
        "preflight.completed",
        "scene.loaded",
        "simulation.started",
    ]
    assert len(tasks) == 1 and tasks[0].closed is True


def test_base_exception_during_close_still_postverifies_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    out_dir = tmp_path / "output"
    read_fd, write_fd = os.pipe()

    def interrupt_close() -> None:
        raise KeyboardInterrupt("injected close interrupt")

    base_task, tasks = _fake_runtime(monkeypatch, on_close=interrupt_close)
    verification_states: list[bool | None] = []
    original_verify = runtime.verify_runtime_asset_snapshot

    def trace_verification(**kwargs: Any) -> Any:
        verified = original_verify(**kwargs)
        verification_states.append(tasks[-1].closed if tasks else None)
        return verified

    monkeypatch.setattr(runtime, "verify_runtime_asset_snapshot", trace_verification)
    try:
        with pytest.raises(KeyboardInterrupt, match="injected close interrupt"):
            runtime.main(
                _runtime_arguments(
                    robotwin_root=robotwin_root,
                    resolved_path=resolved_path,
                    out_dir=out_dir,
                    event_fd=write_fd,
                ),
                base_task_class=base_task,
            )
        os.close(write_fd)
        write_fd = -1
        transcript = os.read(read_fd, 64 * 1024)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)

    assert verification_states == [None, False]
    events = RuntimeEventCodec(allowed_artifact_paths=EVENT_ARTIFACT_PATHS).parse_transcript(
        transcript
    )
    assert events[-1].kind.value == "evidence.completed"
    assert all(event.kind.value != "worker.completed" for event in events)
    assert len(tasks) == 1 and tasks[0].closed is False


def test_precheck_steps_are_visible_physics_and_final_checkpoint_is_not_hidden(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    out_dir = tmp_path / "output"
    base_task, tasks = _fake_runtime(monkeypatch)
    read_fd, write_fd = os.pipe()
    arguments = _runtime_arguments(
        robotwin_root=robotwin_root,
        resolved_path=resolved_path,
        out_dir=out_dir,
        event_fd=write_fd,
    )
    arguments.extend(("--precheck-steps", "3", "--settle-converge-max", "0"))
    try:
        exit_code = runtime.main(arguments, base_task_class=base_task)
        os.close(write_fd)
        write_fd = -1
        transcript = os.read(read_fd, 64 * 1024)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)

    events = RuntimeEventCodec(allowed_artifact_paths=EVENT_ARTIFACT_PATHS).parse_transcript(
        transcript
    )
    simulation_events = [event for event in events if event.kind.value.startswith("simulation.")]
    assert [(event.kind.value, event.completed_steps) for event in simulation_events] == [
        ("simulation.started", None),
        ("simulation.checkpoint", 2),
        ("simulation.checkpoint", 4),
        ("simulation.checkpoint", 6),
        ("simulation.checkpoint", 8),
        ("simulation.completed", 8),
    ]
    assert exit_code == 0
    assert tasks[0].scene.step_count == 8
    evidence = json.loads((out_dir / "runtime_evidence.json").read_bytes())
    assert evidence["precheck_steps"] == 3
    assert evidence["base_simulation_step_count"] == 5
    assert evidence["simulation_step_count"] == 5
    assert evidence["total_physics_step_count"] == 8
    assert evidence["settle_extra_steps"] == 0


def test_output_root_must_be_new_or_empty_before_any_runtime_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    base_task, tasks = _fake_runtime(monkeypatch)
    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "foreign.txt").write_text("foreign\n", encoding="utf-8")
    regular_file = tmp_path / "regular-file"
    regular_file.write_text("foreign\n", encoding="utf-8")
    empty_target = tmp_path / "empty-target"
    empty_target.mkdir()
    symlink = tmp_path / "output-link"
    symlink.symlink_to(empty_target, target_is_directory=True)

    for out_dir in (nonempty, regular_file, symlink):
        read_fd, write_fd = os.pipe()
        try:
            with pytest.raises(RuntimeError, match="output root"):
                runtime.main(
                    _runtime_arguments(
                        robotwin_root=robotwin_root,
                        resolved_path=resolved_path,
                        out_dir=out_dir,
                        event_fd=write_fd,
                    ),
                    base_task_class=base_task,
                )
            os.close(write_fd)
            write_fd = -1
            assert os.read(read_fd, 64 * 1024) == b""
        finally:
            os.close(read_fd)
            if write_fd >= 0:
                os.close(write_fd)

    assert tasks == []
    assert (nonempty / "foreign.txt").read_text(encoding="utf-8") == "foreign\n"
    assert regular_file.read_text(encoding="utf-8") == "foreign\n"
    assert symlink.is_symlink()
    assert list(empty_target.iterdir()) == []


def test_empty_output_root_is_accepted_and_artifact_allowlist_rejects_nonregular_entries(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    runtime._prepare_output_root(empty)
    assert list(empty.iterdir()) == []

    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    expected = artifact_root / "expected.bin"
    expected.write_bytes(b"expected")
    link = artifact_root / "linked.bin"
    link.symlink_to(expected)
    with pytest.raises(RuntimeError, match="non-regular artifact"):
        runtime._require_exact_artifacts(artifact_root, ("expected.bin", "linked.bin"))
    link.unlink()
    directory = artifact_root / "directory.bin"
    directory.mkdir()
    with pytest.raises(RuntimeError, match="non-regular artifact"):
        runtime._require_exact_artifacts(artifact_root, ("expected.bin", "directory.bin"))
    directory.rmdir()
    empty_file = artifact_root / "empty.bin"
    empty_file.write_bytes(b"")
    with pytest.raises(RuntimeError, match="non-regular artifact"):
        runtime._require_exact_artifacts(artifact_root, ("expected.bin", "empty.bin"))


def test_undeclared_worker_output_is_rejected_before_media_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    out_dir = tmp_path / "output"
    base_task, tasks = _fake_runtime(monkeypatch)
    original_save_rgb = runtime.save_rgb
    injected = False

    def save_rgb_with_undeclared_output(path: Path, image: np.ndarray) -> None:
        nonlocal injected
        original_save_rgb(path, image)
        if not injected:
            injected = True
            (Path(path).parent / "undeclared.bin").write_bytes(b"foreign")

    monkeypatch.setattr(runtime, "save_rgb", save_rgb_with_undeclared_output)
    read_fd, write_fd = os.pipe()
    try:
        with pytest.raises(RuntimeError, match="artifacts do not match the allowlist"):
            runtime.main(
                _runtime_arguments(
                    robotwin_root=robotwin_root,
                    resolved_path=resolved_path,
                    out_dir=out_dir,
                    event_fd=write_fd,
                ),
                base_task_class=base_task,
            )
        os.close(write_fd)
        write_fd = -1
        transcript = os.read(read_fd, 64 * 1024)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)

    events = RuntimeEventCodec(allowed_artifact_paths=EVENT_ARTIFACT_PATHS).parse_transcript(
        transcript
    )
    assert events[-1].kind.value == "simulation.completed"
    assert all(
        event.kind.value not in {"media.completed", "evidence.completed", "worker.completed"}
        for event in events
    )
    assert (out_dir / "undeclared.bin").read_bytes() == b"foreign"
    assert json.loads((out_dir / "runtime_evidence.json").read_bytes())["status"] == "fail"
    assert tasks[0].closed is True


def test_runtime_evidence_is_path_free_and_identical_across_output_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    base_task, tasks = _fake_runtime(monkeypatch)
    evidence_payloads: list[bytes] = []

    for name in ("first-output", "second-output"):
        out_dir = tmp_path / name
        read_fd, write_fd = os.pipe()
        try:
            assert (
                runtime.main(
                    _runtime_arguments(
                        robotwin_root=robotwin_root,
                        resolved_path=resolved_path,
                        out_dir=out_dir,
                        event_fd=write_fd,
                    ),
                    base_task_class=base_task,
                )
                == 0
            )
            os.close(write_fd)
            write_fd = -1
            RuntimeEventCodec(allowed_artifact_paths=EVENT_ARTIFACT_PATHS).parse_transcript(
                os.read(read_fd, 64 * 1024)
            )
        finally:
            os.close(read_fd)
            if write_fd >= 0:
                os.close(write_fd)
        evidence_payloads.append((out_dir / "runtime_evidence.json").read_bytes())

    assert len(tasks) == 2 and all(task.closed for task in tasks)
    assert evidence_payloads[0] == evidence_payloads[1]
    assert str(tmp_path).encode() not in evidence_payloads[0]
    evidence = json.loads(evidence_payloads[0])
    assert evidence["images"] == {
        "head": "preview_head.png",
        "observer_end": "observer_end.png",
        "observer_mid": "observer_mid.png",
        "observer_start": "observer_start.png",
        "segmentation": "preview_segmentation.png",
        "world_left": "preview_world_left.png",
        "world_right": "preview_world_right.png",
    }
    assert evidence["video"] == "observer_runtime.mp4"


def test_physical_validation_failure_keeps_legacy_nonzero_exit_without_evidence_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    out_dir = tmp_path / "output"
    base_task, tasks = _fake_runtime(monkeypatch)
    read_fd, write_fd = os.pipe()
    try:
        exit_code = runtime.main(
            _runtime_arguments(
                robotwin_root=robotwin_root,
                resolved_path=resolved_path,
                out_dir=out_dir,
                event_fd=write_fd,
                evidence_only=False,
            ),
            base_task_class=base_task,
        )
        os.close(write_fd)
        write_fd = -1
        transcript = os.read(read_fd, 64 * 1024)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)

    events = RuntimeEventCodec(allowed_artifact_paths=EVENT_ARTIFACT_PATHS).parse_transcript(
        transcript
    )
    assert exit_code == 2
    assert json.loads((out_dir / "runtime_validation_report.json").read_bytes())["status"] == (
        "fail"
    )
    assert events[-1].kind.value == "worker.completed"
    assert tasks[0].closed is True


def test_acquisition_failure_is_nonzero_closes_runtime_and_never_emits_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    out_dir = tmp_path / "output"
    base_task, tasks = _fake_runtime(monkeypatch, fail_at_step=3)
    read_fd, write_fd = os.pipe()
    try:
        with pytest.raises(RuntimeError, match="injected acquisition failure"):
            runtime.main(
                _runtime_arguments(
                    robotwin_root=robotwin_root,
                    resolved_path=resolved_path,
                    out_dir=out_dir,
                    event_fd=write_fd,
                ),
                base_task_class=base_task,
            )
        os.close(write_fd)
        write_fd = -1
        transcript = os.read(read_fd, 64 * 1024)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)

    events = RuntimeEventCodec(allowed_artifact_paths=EVENT_ARTIFACT_PATHS).parse_transcript(
        transcript
    )
    assert [event.kind.value for event in events] == [
        "preflight.completed",
        "scene.loaded",
        "simulation.started",
        "simulation.checkpoint",
    ]
    assert events[-1].completed_steps == 2
    assert tasks[0].closed is True
    failure_evidence = json.loads((out_dir / "runtime_evidence.json").read_bytes())
    assert failure_evidence["status"] == "fail"
    assert "injected acquisition failure" in failure_evidence["error"]
    assert not (out_dir / "runtime_validation_report.json").exists()


def test_preflight_event_requires_successful_runtime_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    out_dir = tmp_path / "output"
    base_task, tasks = _fake_runtime(monkeypatch)
    monkeypatch.setattr(
        runtime,
        "load_robotwin_args",
        lambda *_: (_ for _ in ()).throw(RuntimeError("invalid runtime config")),
    )
    original_cwd = Path.cwd()
    original_sys_path = sys.path.copy()
    read_fd, write_fd = os.pipe()
    try:
        with pytest.raises(RuntimeError, match="invalid runtime config"):
            runtime.main(
                _runtime_arguments(
                    robotwin_root=robotwin_root,
                    resolved_path=resolved_path,
                    out_dir=out_dir,
                    event_fd=write_fd,
                ),
                base_task_class=base_task,
            )
        os.close(write_fd)
        write_fd = -1
        transcript = os.read(read_fd, 64 * 1024)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)

    assert transcript == b""
    assert tasks == []
    evidence = json.loads((out_dir / "runtime_evidence.json").read_bytes())
    assert evidence["status"] == "fail"
    assert "invalid runtime config" in evidence["error"]
    assert Path.cwd() == original_cwd
    assert sys.path == original_sys_path


def test_scene_loaded_event_requires_successful_asset_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    out_dir = tmp_path / "output"
    base_task, tasks = _fake_runtime(monkeypatch)
    monkeypatch.setattr(
        runtime,
        "load_resolved_scene",
        lambda *_, **__: (_ for _ in ()).throw(RuntimeError("asset setup failed")),
    )
    read_fd, write_fd = os.pipe()
    try:
        with pytest.raises(RuntimeError, match="asset setup failed"):
            runtime.main(
                _runtime_arguments(
                    robotwin_root=robotwin_root,
                    resolved_path=resolved_path,
                    out_dir=out_dir,
                    event_fd=write_fd,
                ),
                base_task_class=base_task,
            )
        os.close(write_fd)
        write_fd = -1
        transcript = os.read(read_fd, 64 * 1024)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)

    events = RuntimeEventCodec(allowed_artifact_paths=EVENT_ARTIFACT_PATHS).parse_transcript(
        transcript
    )
    assert [event.kind.value for event in events] == ["preflight.completed"]
    assert len(tasks) == 1 and tasks[0].closed is True
    evidence = json.loads((out_dir / "runtime_evidence.json").read_bytes())
    assert evidence["status"] == "fail"
    assert "asset setup failed" in evidence["error"]


def test_close_failure_never_claims_worker_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    out_dir = tmp_path / "output"

    def fail_close() -> None:
        raise RuntimeError("injected close failure")

    base_task, tasks = _fake_runtime(monkeypatch, on_close=fail_close)
    read_fd, write_fd = os.pipe()
    try:
        with pytest.raises(RuntimeError, match="injected close failure"):
            runtime.main(
                _runtime_arguments(
                    robotwin_root=robotwin_root,
                    resolved_path=resolved_path,
                    out_dir=out_dir,
                    event_fd=write_fd,
                ),
                base_task_class=base_task,
            )
        os.close(write_fd)
        write_fd = -1
        transcript = os.read(read_fd, 64 * 1024)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)

    events = RuntimeEventCodec(allowed_artifact_paths=EVENT_ARTIFACT_PATHS).parse_transcript(
        transcript
    )
    assert events[-1].kind.value == "evidence.completed"
    assert all(event.kind.value != "worker.completed" for event in events)
    assert tasks[0].closed is False
    assert (out_dir / "runtime_evidence.json").is_file()
    assert (out_dir / "runtime_validation_report.json").is_file()


def test_missing_media_file_is_an_acquisition_failure_and_is_never_published(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    out_dir = tmp_path / "output"
    base_task, tasks = _fake_runtime(monkeypatch)
    monkeypatch.setattr(runtime.imageio, "mimsave", lambda *_args, **_kwargs: None)
    read_fd, write_fd = os.pipe()
    try:
        with pytest.raises(RuntimeError, match="artifacts do not match the allowlist"):
            runtime.main(
                _runtime_arguments(
                    robotwin_root=robotwin_root,
                    resolved_path=resolved_path,
                    out_dir=out_dir,
                    event_fd=write_fd,
                ),
                base_task_class=base_task,
            )
        os.close(write_fd)
        write_fd = -1
        transcript = os.read(read_fd, 64 * 1024)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)

    events = RuntimeEventCodec(allowed_artifact_paths=EVENT_ARTIFACT_PATHS).parse_transcript(
        transcript
    )
    assert events[-1].kind.value == "simulation.completed"
    assert all(
        event.kind.value not in {"media.completed", "evidence.completed", "worker.completed"}
        for event in events
    )
    assert tasks[0].closed is True
    assert json.loads((out_dir / "runtime_evidence.json").read_bytes())["status"] == "fail"


def test_video_disabled_publishes_only_files_that_exist_and_keeps_real_step_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    out_dir = tmp_path / "output"
    base_task, tasks = _fake_runtime(monkeypatch)
    read_fd, write_fd = os.pipe()
    try:
        arguments = _runtime_arguments(
            robotwin_root=robotwin_root,
            resolved_path=resolved_path,
            out_dir=out_dir,
            event_fd=write_fd,
            asset_catalog_path=ROOT / "tests" / "fixtures" / "asset_catalog.json",
        )
        arguments.extend(
            (
                "--settle-steps",
                "2",
                "--settle-converge-max",
                "0",
                "--video-frames",
                "0",
                "--checkpoint-steps",
                "120",
            )
        )
        exit_code = runtime.main(arguments, base_task_class=base_task)
        os.close(write_fd)
        write_fd = -1
        transcript = os.read(read_fd, 64 * 1024)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)

    events = RuntimeEventCodec(allowed_artifact_paths=EVENT_ARTIFACT_PATHS).parse_transcript(
        transcript
    )
    assert exit_code == 0
    assert [event.kind.value for event in events] == [
        "preflight.completed",
        "scene.loaded",
        "simulation.started",
        "simulation.completed",
        "media.completed",
        "evidence.completed",
        "worker.completed",
    ]
    assert events[3].completed_steps == 2
    assert events[4].artifact_paths == (
        "preview_head.png",
        "preview_segmentation.png",
        "preview_world_left.png",
        "preview_world_right.png",
    )
    evidence = json.loads((out_dir / "runtime_evidence.json").read_bytes())
    assert evidence["simulation_step_count"] == 2
    assert evidence["video_frame_count"] == 0
    assert evidence["video_sample_step_indices"] == []
    assert not (out_dir / "observer_runtime.mp4").exists()
    assert tasks[0].closed is True


def test_legacy_cli_without_event_fd_still_returns_zero_for_a_passing_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = tmp_path / "robotwin"
    robotwin_root.mkdir()
    out_dir = tmp_path / "output"
    base_task, tasks = _fake_runtime(monkeypatch)
    envs_package = ModuleType("envs")
    envs_package.__path__ = []  # type: ignore[attr-defined]
    base_task_module = ModuleType("envs._base_task")
    base_task_module.Base_Task = base_task  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "envs", envs_package)
    monkeypatch.setitem(sys.modules, "envs._base_task", base_task_module)
    monkeypatch.setattr(
        runtime,
        "validate_resolved_scene",
        lambda *_args, **_kwargs: {
            "schema_version": "robotwin.scene_validation.v1",
            "status": "pass",
            "fail_count": 0,
        },
    )
    original_cwd = Path.cwd()
    original_sys_path = sys.path.copy()

    exit_code = runtime.main(
        [
            "--robotwin-root",
            str(robotwin_root),
            "--resolved-scene",
            str(resolved_path),
            "--out-dir",
            str(out_dir),
            "--settle-steps",
            "1",
            "--video-frames",
            "0",
        ],
    )

    assert exit_code == 0
    assert tasks[0].closed is True
    assert json.loads((out_dir / "runtime_validation_report.json").read_bytes())["status"] == (
        "pass"
    )
    assert Path.cwd() == original_cwd
    assert sys.path == original_sys_path


@pytest.mark.parametrize(
    ("event_fd", "event_protocol", "error_type"),
    [
        (2, "harness.runtime_event.v1", RuntimeEventProtocolError),
        (999_999, "harness.runtime_event.v1", ValueError),
        (None, "harness.runtime_event.v999", SystemExit),
    ],
)
def test_invalid_event_transport_fails_closed_before_simulation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    event_fd: int | None,
    event_protocol: str,
    error_type: type[BaseException],
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    base_task, tasks = _fake_runtime(monkeypatch)
    arguments = [
        "--robotwin-root",
        str(robotwin_root),
        "--resolved-scene",
        str(resolved_path),
        "--out-dir",
        str(tmp_path / "output"),
        "--event-protocol",
        event_protocol,
        "--expected-capability-sha256",
        _capability_sha256(runtime, robotwin_root),
    ]
    if event_fd is not None:
        arguments.extend(("--event-fd", str(event_fd)))
        arguments.extend(
            _runtime_asset_arguments(
                resolved_path=resolved_path,
                out_dir=tmp_path / "output",
            )
        )

    with pytest.raises(error_type):
        runtime.main(arguments, base_task_class=base_task)

    assert tasks == []


def test_read_only_event_fd_fails_on_preflight_write_before_simulation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = _runtime_checkout(tmp_path / "robotwin")
    out_dir = tmp_path / "output"
    event_file = tmp_path / "events.jsonl"
    event_file.write_bytes(b"")
    base_task, tasks = _fake_runtime(monkeypatch)

    with event_file.open("rb") as stream:
        with pytest.raises(RuntimeEventProtocolError, match="write failed"):
            runtime.main(
                _runtime_arguments(
                    robotwin_root=robotwin_root,
                    resolved_path=resolved_path,
                    out_dir=out_dir,
                    event_fd=stream.fileno(),
                ),
                base_task_class=base_task,
            )

    assert tasks == []
    assert event_file.read_bytes() == b""
    evidence = json.loads((out_dir / "runtime_evidence.json").read_bytes())
    assert evidence["status"] == "fail"
    assert "runtime event write failed" in evidence["error"]


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--settle-steps", "0"),
        ("--settle-steps", "not-an-integer"),
        ("--settle-converge-max", "-1"),
        ("--precheck-steps", "-1"),
        ("--video-frames", "-1"),
        ("--fps", "0"),
        ("--min-visible-pixels", "-1"),
        ("--contact-window-steps", "0"),
        ("--checkpoint-steps", "0"),
        ("--task-config", "../outside"),
    ],
)
def test_invalid_runtime_parameters_fail_preflight_before_simulation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flag: str,
    value: str,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = tmp_path / "robotwin"
    robotwin_root.mkdir()
    base_task, tasks = _fake_runtime(monkeypatch)

    with pytest.raises(SystemExit) as error:
        runtime.main(
            [
                "--robotwin-root",
                str(robotwin_root),
                "--resolved-scene",
                str(resolved_path),
                "--out-dir",
                str(tmp_path / "output"),
                flag,
                value,
            ],
            base_task_class=base_task,
        )

    assert error.value.code == 2
    assert tasks == []


@pytest.mark.parametrize("omitted_flag", ["--resolved-scene", "--out-dir"])
def test_runtime_mode_requires_scene_and_output_while_describe_does_not(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    omitted_flag: str,
) -> None:
    resolved_path = tmp_path / "resolved.json"
    _resolved_scene(resolved_path)
    robotwin_root = tmp_path / "robotwin"
    robotwin_root.mkdir()
    base_task, tasks = _fake_runtime(monkeypatch)
    values = {
        "--resolved-scene": str(resolved_path),
        "--out-dir": str(tmp_path / "output"),
    }
    arguments = ["--robotwin-root", str(robotwin_root)]
    for flag, value in values.items():
        if flag != omitted_flag:
            arguments.extend((flag, value))

    with pytest.raises(SystemExit) as error:
        runtime.main(arguments, base_task_class=base_task)

    assert error.value.code == 2
    assert tasks == []
