from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from self_improving.harness import runtime_capability


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Capability Test",
            "GIT_AUTHOR_EMAIL": "capability@example.invalid",
            "GIT_COMMITTER_NAME": "Capability Test",
            "GIT_COMMITTER_EMAIL": "capability@example.invalid",
        },
    )
    return completed.stdout.strip()


def _runtime_checkout(root: Path) -> Path:
    task_root = root / "env_cfg" / "task_config"
    task_root.mkdir(parents=True)
    (task_root / "demo_clean.yml").write_text(
        "embodiment: [aloha]\n",
        encoding="utf-8",
    )
    (task_root / "_embodiment_config.yml").write_text(
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
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "runtime fixture")
    return root


class _Distribution:
    def __init__(self, name: str) -> None:
        self.version = f"{name}-1.0"
        self._name = name

    def read_text(self, filename: str) -> str | None:
        if filename == "RECORD":
            return f"{self._name}/module.py,sha256=fixture,1\n"
        if filename == "direct_url.json" and self._name == "nvidia-curobo":
            return '{"url":"file:///private/source"}\n'
        return None


def _source_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    source_root = tmp_path / "scene_gen"
    source_root.mkdir()
    (source_root / "schema.py").write_text("SCHEMA = 'v1'\n", encoding="utf-8")
    events_path = tmp_path / "runtime_events.py"
    events_path.write_text("EVENTS = 'v1'\n", encoding="utf-8")
    executable = tmp_path / "python"
    executable.write_bytes(b"python-runtime")
    monkeypatch.setattr(runtime_capability, "SCENE_GEN_SOURCE_ROOT", source_root)
    monkeypatch.setattr(runtime_capability, "RUNTIME_EVENTS_SOURCE_PATH", events_path)
    monkeypatch.setattr(runtime_capability, "PYTHON_EXECUTABLE_PATH", executable)
    monkeypatch.setattr(
        runtime_capability.metadata,
        "distribution",
        lambda name: _Distribution(name),
    )
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_bytes(b"ffmpeg-runtime")
    monkeypatch.setitem(
        sys.modules,
        "imageio_ffmpeg",
        SimpleNamespace(get_ffmpeg_exe=lambda: str(ffmpeg)),
    )
    nvidia_smi = tmp_path / "nvidia-smi"
    nvidia_smi.write_bytes(b"nvidia-smi-runtime")
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
            assert arguments[1:] == (
                "--query-gpu=uuid,name,driver_version",
                "--format=csv,noheader,nounits",
            )
            return runtime_capability._CommandResult(
                stdout=b"GPU-b, GPU B, 560.1\nGPU-a, GPU A, 560.1\n",
                stdout_sha256=hashlib.sha256(
                    b"GPU-b, GPU B, 560.1\nGPU-a, GPU A, 560.1\n"
                ).hexdigest(),
                stdout_bytes=42,
            )
        return original_command(arguments, **kwargs)

    monkeypatch.setattr(runtime_capability, "_bounded_command", bounded_command)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1,0")
    monkeypatch.delenv("EGL_DEVICE_ID", raising=False)
    runner = tmp_path / "runner.py"
    runner.write_text("print('runtime')\n", encoding="utf-8")
    return runner


def test_description_binds_full_dependency_and_selected_embodiment_closures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _source_inputs(tmp_path, monkeypatch)
    robotwin = _runtime_checkout(tmp_path / "robotwin")

    document = runtime_capability.describe_runtime_capability(
        robotwin_root=robotwin,
        task_config="demo_clean",
        runner_path=runner,
    )

    assert set(document["packages"]) == set(runtime_capability.REQUIRED_RUNTIME_DISTRIBUTIONS)
    assert "imageio-ffmpeg" in document["packages"]
    assert document["packages"]["nvidia-curobo"] == {
        "version": "nvidia-curobo-1.0",
        "record_sha256": hashlib.sha256(b"nvidia-curobo/module.py,sha256=fixture,1\n").hexdigest(),
        "direct_url_sha256": hashlib.sha256(b'{"url":"file:///private/source"}\n').hexdigest(),
    }
    assert document["packages"]["mplib"]["direct_url_sha256"] is None
    task = document["task_config"]
    assert task["path"] == "env_cfg/task_config/demo_clean.yml"
    assert task["registry"]["path"] == "env_cfg/task_config/_embodiment_config.yml"
    assert task["embodiment_closure"]["strategy"] == "selected_embodiment_tree.v1"
    assert task["embodiment_closure"]["selection"] == ["aloha"]
    assert [item["path"] for item in task["import_resources"]] == [
        "assets/objects/objaverse/list.json",
        "assets/objects/same.json",
    ]
    assert task["embodiment_closure"]["resources"] == [
        {
            "name": "aloha",
            "path": "assets/embodiments/aloha",
            "tree_sha256": task["embodiment_closure"]["resources"][0]["tree_sha256"],
            "file_count": 6,
            "bytes": sum(
                path.stat().st_size
                for path in (robotwin / "assets" / "embodiments" / "aloha").rglob("*")
                if path.is_file()
            ),
            "required_files": task["embodiment_closure"]["resources"][0]["required_files"],
        }
    ]
    assert [
        item["path"] for item in task["embodiment_closure"]["resources"][0]["required_files"]
    ] == [
        "config.yml",
        "curobo_left.yml",
        "curobo_right.yml",
        "srdf/robot.srdf",
        "urdf/meshes/base.stl",
        "urdf/robot.urdf",
    ]
    serialized = json.dumps(document, sort_keys=True)
    assert str(tmp_path) not in serialized
    assert document["runtime_binaries"]["ffmpeg"] == {
        "sha256": hashlib.sha256(b"ffmpeg-runtime").hexdigest(),
        "bytes": len(b"ffmpeg-runtime"),
    }
    assert document["accelerator"]["gpus"] == [
        {"uuid": "GPU-a", "name": "GPU A", "driver_version": "560.1"},
        {"uuid": "GPU-b", "name": "GPU B", "driver_version": "560.1"},
    ]
    assert document["accelerator"]["selection_environment"]["CUDA_VISIBLE_DEVICES"] == {
        "present": True,
        "value_sha256": hashlib.sha256(b"1,0").hexdigest(),
    }
    assert document["accelerator"]["selection_environment"]["EGL_DEVICE_ID"] == {
        "present": False,
        "value_sha256": None,
    }
    assert set(document["accelerator"]["selection_environment"]) == set(
        runtime_capability.RUNTIME_ENVIRONMENT_ALLOWLIST
    )
    assert {
        "PATH",
        "LD_LIBRARY_PATH",
        "CONDA_PREFIX",
        "PYTHONPATH",
        "PYTHONUNBUFFERED",
        "PYTHONDONTWRITEBYTECODE",
    }.issubset(document["accelerator"]["selection_environment"])
    assert document["bootstrap"] == {
        "kind": "python.import_base_task.v1",
        "source_sha256": hashlib.sha256(
            runtime_capability._BASE_TASK_BOOTSTRAP_SOURCE.encode("utf-8")
        ).hexdigest(),
        "command_sha256": hashlib.sha256(
            runtime_capability.canonical_capability_bytes(
                list(runtime_capability._BASE_TASK_BOOTSTRAP_COMMAND)
            )
        ).hexdigest(),
    }
    runtime_capability.validate_runtime_capability_document(document)


@pytest.fixture
def capability_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    runner = _source_inputs(tmp_path, monkeypatch)
    robotwin = _runtime_checkout(tmp_path / "robotwin")
    return runtime_capability.describe_runtime_capability(
        robotwin_root=robotwin,
        task_config="demo_clean",
        runner_path=runner,
    )


def _set_path(document: dict[str, Any], path: str, value: Any) -> None:
    current: Any = document
    parts = path.split(".")
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    if isinstance(current, list):
        current[int(parts[-1])] = value
    else:
        current[parts[-1]] = value


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("schema_version", "wrong"),
        ("protocol_version", 2),
        ("backend_id", "wrong"),
        ("runner_sha256", "A" * 64),
        ("python", {}),
        ("python.implementation", 1),
        ("python.version", ""),
        ("python.executable_sha256", "bad"),
        ("packages", {}),
        ("packages.Pillow", {}),
        ("packages.Pillow.version", 1),
        ("packages.Pillow.version", ""),
        ("packages.Pillow.record_sha256", "bad"),
        ("packages.nvidia-curobo.direct_url_sha256", "bad"),
        ("runtime_binaries", {}),
        ("runtime_binaries.ffmpeg", {}),
        ("runtime_binaries.ffmpeg.sha256", "bad"),
        ("runtime_binaries.ffmpeg.bytes", True),
        ("runtime_binaries.ffmpeg.bytes", 0),
        ("accelerator", {}),
        ("accelerator.probe", {}),
        ("accelerator.probe.kind", "wrong"),
        ("accelerator.probe.sha256", "bad"),
        ("accelerator.probe.bytes", 0),
        ("accelerator.gpus", {}),
        ("accelerator.gpus", []),
        ("accelerator.gpus.0", {}),
        ("accelerator.gpus.0.uuid", 1),
        ("accelerator.gpus.0.name", ""),
        ("accelerator.selection_environment", {}),
        ("accelerator.selection_environment.PATH", {}),
        ("accelerator.selection_environment.PATH.present", 1),
        ("accelerator.selection_environment.PATH.value_sha256", "bad"),
        ("bootstrap", {}),
        ("bootstrap.kind", "wrong"),
        ("bootstrap.source_sha256", "bad"),
        ("bootstrap.command_sha256", "bad"),
        ("source_modules", {}),
        ("source_modules.scene_gen", {}),
        ("source_modules.runtime_events", {}),
        ("source_modules.runtime_capability", {}),
        ("source_modules.scene_gen.tree_sha256", "bad"),
        ("source_modules.runtime_events.sha256", "bad"),
        ("source_modules.runtime_capability.sha256", "bad"),
        ("robotwin", {}),
        ("robotwin.commit", 1),
        ("robotwin.commit", "abc"),
        ("robotwin.dirty", 1),
        ("robotwin.tracked_diff_sha256", "bad"),
        ("task_config", {}),
        ("task_config.path", 1),
        ("task_config.path", "/absolute"),
        ("task_config.sha256", "bad"),
        ("task_config.registry", {}),
        ("task_config.registry.path", "../escape"),
        ("task_config.registry.sha256", "bad"),
        ("task_config.import_resources", {}),
        ("task_config.import_resources", []),
        ("task_config.import_resources.0", {}),
        ("task_config.import_resources.0.path", "wrong.json"),
        ("task_config.import_resources.0.sha256", "bad"),
        ("task_config.import_resources.0.bytes", True),
        ("task_config.import_resources.0.bytes", 0),
        ("task_config.embodiment_closure", {}),
        ("task_config.embodiment_closure.strategy", "wrong"),
        ("task_config.embodiment_closure.selection", {}),
        ("task_config.embodiment_closure.selection", []),
        ("task_config.embodiment_closure.selection", [""]),
        ("task_config.embodiment_closure.resources", {}),
        ("task_config.embodiment_closure.resources", []),
        ("task_config.embodiment_closure.resources.0", {}),
        ("task_config.embodiment_closure.resources.0.name", 1),
        ("task_config.embodiment_closure.resources.0.name", "other"),
        ("task_config.embodiment_closure.resources.0.path", "/absolute"),
        ("task_config.embodiment_closure.resources.0.tree_sha256", "bad"),
        ("task_config.embodiment_closure.resources.0.file_count", True),
        ("task_config.embodiment_closure.resources.0.file_count", 0),
        ("task_config.embodiment_closure.resources.0.bytes", 0),
        ("task_config.embodiment_closure.resources.0.required_files", {}),
        ("task_config.embodiment_closure.resources.0.required_files", []),
        ("task_config.embodiment_closure.resources.0.required_files.0", {}),
        (
            "task_config.embodiment_closure.resources.0.required_files.0.path",
            "../escape",
        ),
        (
            "task_config.embodiment_closure.resources.0.required_files.0.sha256",
            "bad",
        ),
        ("supported", {}),
        ("supported.resolved_scene_schemas", []),
        ("supported.asset_catalog_schemas", []),
        ("supported.runtime_evidence_schemas", []),
        ("supported.validation_report_schemas", []),
        ("supported.parameters", {}),
        ("supported.event_protocol", {}),
    ],
)
def test_validator_rejects_closed_shape_and_identity_attacks(
    capability_document: dict[str, Any],
    path: str,
    value: Any,
) -> None:
    attacked = copy.deepcopy(capability_document)
    _set_path(attacked, path, value)

    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="capability"):
        runtime_capability.validate_runtime_capability_document(attacked)


def test_validator_rejects_duplicate_and_unsorted_accelerators(
    capability_document: dict[str, Any],
) -> None:
    first = capability_document["accelerator"]["gpus"][0]
    duplicate = copy.deepcopy(capability_document)
    duplicate["accelerator"]["gpus"].append(copy.deepcopy(first))
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="unique and sorted"):
        runtime_capability.validate_runtime_capability_document(duplicate)

    unsorted = copy.deepcopy(capability_document)
    unsorted["accelerator"]["gpus"] = [
        {"uuid": "GPU-z", "name": "GPU Z", "driver_version": "1"},
        {"uuid": "GPU-a", "name": "GPU A", "driver_version": "1"},
    ]
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="unique and sorted"):
        runtime_capability.validate_runtime_capability_document(unsorted)


def test_validator_rejects_absent_environment_digest_and_duplicate_resource(
    capability_document: dict[str, Any],
) -> None:
    environment = copy.deepcopy(capability_document)
    environment["accelerator"]["selection_environment"]["EGL_DEVICE_ID"] = {
        "present": False,
        "value_sha256": "0" * 64,
    }
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="absent accelerator"):
        runtime_capability.validate_runtime_capability_document(environment)

    duplicate = copy.deepcopy(capability_document)
    files = duplicate["task_config"]["embodiment_closure"]["resources"][0]["required_files"]
    files.append(copy.deepcopy(files[0]))
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="duplicated"):
        runtime_capability.validate_runtime_capability_document(duplicate)


def test_bootstrap_probe_is_read_only_and_binds_logical_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "robotwin"
    package = root / "envs"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "_base_task.py").write_text("class Base_Task:\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(
        runtime_capability, "PYTHON_EXECUTABLE_PATH", Path(sys.executable).resolve()
    )
    before = {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }

    identity = runtime_capability._bootstrap_identity(root)

    after = {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert not list(root.rglob("__pycache__"))
    assert identity["kind"] == "python.import_base_task.v1"
    assert (
        identity["command_sha256"]
        == hashlib.sha256(
            runtime_capability.canonical_capability_bytes(
                list(runtime_capability._BASE_TASK_BOOTSTRAP_COMMAND)
            )
        ).hexdigest()
    )


def test_describe_rejects_missing_root_and_noncanonical_task_names(tmp_path: Path) -> None:
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="must be a directory"):
        runtime_capability.describe_runtime_capability(
            robotwin_root=tmp_path / "missing",
            task_config="demo_clean",
            runner_path=tmp_path / "runner",
        )

    root = tmp_path / "root"
    root.mkdir()
    for name in ("/absolute", "a..b"):
        with pytest.raises(runtime_capability.RuntimeCapabilityError, match="not canonical"):
            runtime_capability.describe_runtime_capability(
                robotwin_root=root,
                task_config=name,
                runner_path=tmp_path / "runner",
            )


def test_runtime_binary_identity_fails_closed_for_import_path_and_file_attacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime_capability.importlib,
        "import_module",
        lambda _name: (_ for _ in ()).throw(ImportError("missing")),
    )
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="cannot be resolved"):
        runtime_capability._runtime_binary_identities()

    monkeypatch.setattr(
        runtime_capability.importlib,
        "import_module",
        lambda _name: SimpleNamespace(get_ffmpeg_exe=lambda: None),
    )
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="invalid executable path"):
        runtime_capability._runtime_binary_identities()

    missing = tmp_path / "missing"
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="regular file"):
        runtime_capability._binary_identity(missing, label="binary")
    target = tmp_path / "target"
    target.write_bytes(b"target")
    symlink = tmp_path / "link"
    symlink.symlink_to(target)
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="regular file"):
        runtime_capability._binary_identity(symlink, label="binary")
    empty = tmp_path / "empty"
    empty.write_bytes(b"")
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="must not be empty"):
        runtime_capability._binary_identity(empty, label="binary")


def _mock_accelerator_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: bytes | None,
) -> None:
    executable = tmp_path / "nvidia-smi"
    executable.write_bytes(b"nvidia-smi")
    monkeypatch.setattr(runtime_capability.shutil, "which", lambda _name: str(executable))
    monkeypatch.setattr(
        runtime_capability,
        "_bounded_command",
        lambda *_args, **_kwargs: runtime_capability._CommandResult(
            stdout=stdout,
            stdout_sha256=hashlib.sha256(stdout or b"").hexdigest(),
            stdout_bytes=len(stdout or b""),
        ),
    )


def test_accelerator_identity_fails_closed_for_missing_or_unreadable_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_capability.shutil, "which", lambda _name: None)
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="is missing"):
        runtime_capability._accelerator_identity()

    _mock_accelerator_query(tmp_path, monkeypatch, None)
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="captured output"):
        runtime_capability._accelerator_identity()

    _mock_accelerator_query(tmp_path, monkeypatch, b"\xff")
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="not UTF-8"):
        runtime_capability._accelerator_identity()


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"bad-row\n", "invalid GPU row"),
        (b"GPU-a, name, driver\x01\n", "control character"),
        (b"", "no unique GPUs"),
        (b"GPU-a, A, 1\nGPU-a, B, 1\n", "no unique GPUs"),
    ],
)
def test_accelerator_identity_rejects_ambiguous_gpu_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
    message: str,
) -> None:
    _mock_accelerator_query(tmp_path, monkeypatch, payload)
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match=message):
        runtime_capability._accelerator_identity()


class _BrokenDistribution:
    def __init__(self, *, version: Any = "1", record: Any = "record", direct: Any = None):
        self.version = version
        self.record = record
        self.direct = direct

    def read_text(self, filename: str) -> Any:
        if filename == "RECORD":
            return self.record
        if filename == "direct_url.json":
            return self.direct
        raise AssertionError(filename)


@pytest.mark.parametrize(
    ("distribution", "message"),
    [
        (_BrokenDistribution(version=None), "no version"),
        (_BrokenDistribution(version=""), "no version"),
        (_BrokenDistribution(record=None), "no RECORD"),
        (_BrokenDistribution(record=""), "no RECORD"),
        (_BrokenDistribution(direct=""), "invalid direct_url"),
        (_BrokenDistribution(direct=1), "invalid direct_url"),
    ],
)
def test_distribution_identity_rejects_incomplete_metadata(
    monkeypatch: pytest.MonkeyPatch,
    distribution: _BrokenDistribution,
    message: str,
) -> None:
    monkeypatch.setattr(runtime_capability.metadata, "distribution", lambda _name: distribution)
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match=message):
        runtime_capability._distribution_identities()


def test_distribution_identity_rejects_missing_distribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(name: str) -> Any:
        raise runtime_capability.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(runtime_capability.metadata, "distribution", missing)
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="distribution is missing"):
        runtime_capability._distribution_identities()


def test_task_and_import_resource_identity_fail_closed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="task config is missing"):
        runtime_capability._task_config_path(root, "missing")

    resources = root / "resources"
    resources.mkdir()
    empty = resources / "empty.json"
    empty.write_bytes(b"")
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="byte length"):
        runtime_capability._json_resource_identity(root, "resources/empty.json")
    too_large = resources / "large.json"
    too_large.write_bytes(b" " * 4_194_305)
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="byte length"):
        runtime_capability._json_resource_identity(root, "resources/large.json")
    invalid = resources / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="valid UTF-8 JSON"):
        runtime_capability._json_resource_identity(root, "resources/invalid.json")
    sequence = resources / "sequence.json"
    sequence.write_text("[]\n", encoding="utf-8")
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="JSON object"):
        runtime_capability._json_resource_identity(root, "resources/sequence.json")
    objaverse = resources / "list.json"
    objaverse.write_text("{}\n", encoding="utf-8")
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="invalid import shape"):
        runtime_capability._json_resource_identity(root, "resources/list.json")


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"embodiment": "aloha"},
        {"embodiment": ["a", "b"]},
        {"embodiment": [1]},
        {"embodiment": [""]},
        {"embodiment": ["a/b"]},
        {"embodiment": ["a\\b"]},
        {"embodiment": ["a", "b", True]},
        {"embodiment": ["a", "b", "1"]},
    ],
)
def test_embodiment_selection_rejects_ambiguous_forms(document: dict[str, Any]) -> None:
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="task embodiment"):
        runtime_capability._embodiment_selection(document)


def test_embodiment_closure_handles_shared_split_and_non_curobo_resources(
    tmp_path: Path,
) -> None:
    root = _runtime_checkout(tmp_path / "robotwin")
    aloha = root / "assets" / "embodiments" / "aloha"
    shared = runtime_capability._embodiment_closure(
        root,
        {
            "left": {"file_path": "assets/embodiments/aloha"},
            "right": {"file_path": "assets/embodiments/aloha"},
        },
        ("left", "right"),
    )
    assert len(shared["resources"]) == 1

    split_root = root / "assets" / "embodiments" / "split"
    shutil.copytree(aloha, split_root)
    (aloha / "curobo.yml").write_text("robot: left\n", encoding="utf-8")
    (split_root / "curobo.yml").write_text("robot: right\n", encoding="utf-8")
    split = runtime_capability._embodiment_closure(
        root,
        {
            "left": {"file_path": "assets/embodiments/aloha"},
            "right": {"file_path": "assets/embodiments/split"},
        },
        ("left", "right"),
    )
    assert len(split["resources"]) == 2
    assert all(
        "curobo.yml" in {item["path"] for item in resource["required_files"]}
        for resource in split["resources"]
    )

    (aloha / "config.yml").write_text(
        "urdf_path: ./urdf/robot.urdf\nsrdf_path: ./srdf/robot.srdf\nplanner: mplib_RRT\n",
        encoding="utf-8",
    )
    mplib = runtime_capability._embodiment_closure(
        root,
        {"aloha": {"file_path": "assets/embodiments/aloha"}},
        ("aloha",),
    )
    assert not any(
        item["path"].startswith("curobo") for item in mplib["resources"][0]["required_files"]
    )


def test_embodiment_closure_rejects_registry_config_and_directory_attacks(
    tmp_path: Path,
) -> None:
    root = _runtime_checkout(tmp_path / "robotwin")
    for entry in (None, {}, {"file_path": "x", "extra": 1}):
        with pytest.raises(runtime_capability.RuntimeCapabilityError, match="registry entry"):
            runtime_capability._embodiment_closure(root, {"aloha": entry}, ("aloha",))
    for file_path in (None, ""):
        with pytest.raises(runtime_capability.RuntimeCapabilityError, match="file_path"):
            runtime_capability._embodiment_closure(
                root,
                {"aloha": {"file_path": file_path}},
                ("aloha",),
            )
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="directory is missing"):
        runtime_capability._embodiment_closure(
            root,
            {"aloha": {"file_path": "assets/embodiments/missing"}},
            ("aloha",),
        )

    config = root / "assets" / "embodiments" / "aloha" / "config.yml"
    config.write_text("srdf_path: ./srdf/robot.srdf\n", encoding="utf-8")
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="has no urdf_path"):
        runtime_capability._embodiment_closure(
            root,
            {"aloha": {"file_path": "assets/embodiments/aloha"}},
            ("aloha",),
        )


def test_urdf_resource_parser_rejects_malformed_and_dynamic_references(
    tmp_path: Path,
) -> None:
    root = tmp_path / "embodiment"
    meshes = root / "urdf" / "meshes"
    meshes.mkdir(parents=True)
    (meshes / "base.stl").write_bytes(b"mesh")
    urdf = root / "urdf" / "robot.urdf"
    urdf.write_text("<robot>", encoding="utf-8")
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="not parseable"):
        runtime_capability._urdf_mesh_files(root, urdf, name="robot")

    for filename in ("", "$(find package)/mesh.stl"):
        urdf.write_text(f'<robot><mesh filename="{filename}"/></robot>', encoding="utf-8")
        with pytest.raises(runtime_capability.RuntimeCapabilityError, match="reference is invalid"):
            runtime_capability._urdf_mesh_files(root, urdf, name="robot")

    urdf.write_text(
        '<robot xmlns="urn:test"><mesh filename="package://meshes/base.stl"/></robot>',
        encoding="utf-8",
    )
    assert runtime_capability._urdf_mesh_files(root, urdf, name="robot") == [meshes / "base.stl"]


def test_tree_identity_rejects_empty_symlink_and_nonregular_entries(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="real directory"):
        runtime_capability._tree_identity(missing, label="tree")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="must not be empty"):
        runtime_capability._tree_identity(empty, label="tree")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "file").write_bytes(b"data")
    (tree / "link").symlink_to("file")
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="contains a symlink"):
        runtime_capability._tree_identity(tree, label="tree")
    (tree / "link").unlink()
    fifo = tree / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="non-regular"):
        runtime_capability._tree_identity(tree, label="tree")
    fifo.unlink()
    cache = tree / "__pycache__"
    cache.mkdir()
    (cache / "ignored.py").write_text("ignored\n", encoding="utf-8")
    (tree / "ignored.pyc").write_bytes(b"ignored")
    assert (
        runtime_capability._tree_identity(
            tree,
            label="tree",
            ignore_bytecode=True,
        )["file_count"]
        == 1
    )


def _bounded_command(
    arguments: tuple[str, ...],
    *,
    timeout_seconds: float = 1.0,
    max_stdout_bytes: int = 1024,
    max_stderr_bytes: int = 1024,
    capture_stdout: bool = True,
) -> Any:
    return runtime_capability._bounded_command(
        arguments,
        cwd=None,
        timeout_seconds=timeout_seconds,
        max_stdout_bytes=max_stdout_bytes,
        max_stderr_bytes=max_stderr_bytes,
        capture_stdout=capture_stdout,
    )


def test_bounded_command_captures_or_stream_hashes_output() -> None:
    payload = b"bounded-output"
    command = (sys.executable, "-c", f"import sys;sys.stdout.buffer.write({payload!r})")
    captured = _bounded_command(command)
    streamed = _bounded_command(command, capture_stdout=False)

    assert captured.stdout == payload
    assert captured.stdout_sha256 == hashlib.sha256(payload).hexdigest()
    assert captured.stdout_bytes == len(payload)
    assert streamed.stdout is None
    assert streamed.stdout_sha256 == captured.stdout_sha256
    assert streamed.stdout_bytes == captured.stdout_bytes


@pytest.mark.parametrize("arguments", [(), ("",), (sys.executable, 1)])
def test_bounded_command_rejects_invalid_arguments(arguments: tuple[Any, ...]) -> None:
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="arguments are invalid"):
        _bounded_command(arguments)  # type: ignore[arg-type]


def test_bounded_command_rejects_start_output_exit_and_read_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_popen = runtime_capability.subprocess.Popen
    monkeypatch.setattr(
        runtime_capability.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("cannot start")),
    )
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="could not start"):
        _bounded_command((sys.executable, "-c", "pass"))
    monkeypatch.setattr(runtime_capability.subprocess, "Popen", original_popen)

    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="stdout exceeded"):
        _bounded_command(
            (sys.executable, "-c", "print('too much output')"),
            max_stdout_bytes=1,
        )
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="stderr exceeded"):
        _bounded_command(
            (sys.executable, "-c", "import sys;sys.stderr.write('too much')"),
            max_stderr_bytes=1,
        )
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="exited with code 7"):
        _bounded_command((sys.executable, "-c", "raise SystemExit(7)"))

    monkeypatch.setattr(
        runtime_capability.selectors,
        "DefaultSelector",
        lambda: _SyntheticSelector(has_map=True, error=OSError("read failed")),
    )
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="failed while reading"):
        _bounded_command((sys.executable, "-c", "import time;time.sleep(1)"))


class _SyntheticSelector:
    def __init__(self, *, has_map: bool, ready: Any = None, error: Exception | None = None):
        self.has_map = has_map
        self.ready = ready
        self.error = error

    def register(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def get_map(self) -> dict[int, int]:
        return {1: 1} if self.has_map else {}

    def select(self, _timeout: float) -> Any:
        if self.error is not None:
            raise self.error
        return self.ready

    def close(self) -> None:
        return None


def test_bounded_command_enforces_each_deadline_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime_capability.selectors,
        "DefaultSelector",
        lambda: _SyntheticSelector(has_map=True, ready=[]),
    )
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="timed out"):
        _bounded_command(
            (sys.executable, "-c", "import time;time.sleep(1)"),
            timeout_seconds=0.01,
        )

    times = iter((0.0, 2.0))
    monkeypatch.setattr(runtime_capability.time, "monotonic", lambda: next(times, 2.0))
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="timed out"):
        _bounded_command((sys.executable, "-c", "import time;time.sleep(1)"))

    monkeypatch.setattr(
        runtime_capability.selectors,
        "DefaultSelector",
        lambda: _SyntheticSelector(has_map=False),
    )
    times = iter((0.0, 2.0))
    monkeypatch.setattr(runtime_capability.time, "monotonic", lambda: next(times, 2.0))
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="timed out"):
        _bounded_command((sys.executable, "-c", "import time;time.sleep(1)"))


class _SyntheticProcess:
    def __init__(self, *, return_code: int | None, wait_error: Exception | None = None):
        self.pid = 12345
        self.return_code = return_code
        self.wait_error = wait_error
        self.waited = False

    def poll(self) -> int | None:
        return self.return_code

    def wait(self, *, timeout: float) -> int:
        self.waited = True
        if self.wait_error is not None:
            raise self.wait_error
        return 0


def test_process_group_cleanup_is_itself_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    finished = _SyntheticProcess(return_code=0)
    runtime_capability._terminate_process_group(finished)  # type: ignore[arg-type]
    assert finished.waited is False

    missing = _SyntheticProcess(return_code=None)
    monkeypatch.setattr(
        runtime_capability.os,
        "killpg",
        lambda *_args: (_ for _ in ()).throw(ProcessLookupError()),
    )
    runtime_capability._terminate_process_group(missing)  # type: ignore[arg-type]
    assert missing.waited is False

    killed = _SyntheticProcess(return_code=None)
    monkeypatch.setattr(runtime_capability.os, "killpg", lambda *_args: None)
    runtime_capability._terminate_process_group(killed)  # type: ignore[arg-type]
    assert killed.waited is True

    timed_out = _SyntheticProcess(
        return_code=None,
        wait_error=subprocess.TimeoutExpired("worker", 1.0),
    )
    runtime_capability._terminate_process_group(timed_out)  # type: ignore[arg-type]
    assert timed_out.waited is True


def test_git_helpers_fail_closed_when_stream_capture_is_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty_result = runtime_capability._CommandResult(
        stdout=None,
        stdout_sha256=hashlib.sha256(b"").hexdigest(),
        stdout_bytes=0,
    )
    monkeypatch.setattr(
        runtime_capability, "_bounded_command", lambda *_args, **_kwargs: empty_result
    )
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="did not return"):
        runtime_capability._git_bytes(tmp_path, "rev-parse", "HEAD")

    root = tmp_path.resolve()

    def git_bytes(_root: Path, *arguments: str) -> bytes:
        if arguments == ("rev-parse", "HEAD"):
            return b"0" * 40 + b"\n"
        return os.fsencode(root) + b"\n"

    monkeypatch.setattr(runtime_capability, "_git_bytes", git_bytes)
    monkeypatch.setattr(
        runtime_capability, "_bounded_command", lambda *_args, **_kwargs: empty_result
    )
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="untracked query"):
        runtime_capability._robotwin_identity(root)


def test_yaml_and_contained_path_helpers_reject_unsafe_inputs(
    tmp_path: Path,
) -> None:
    invalid_yaml = tmp_path / "invalid.yml"
    invalid_yaml.write_bytes(b"\xff")
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="valid UTF-8 YAML"):
        runtime_capability._yaml_mapping(invalid_yaml, label="yaml")
    sequence_yaml = tmp_path / "sequence.yml"
    sequence_yaml.write_text("[]\n", encoding="utf-8")
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="must be a mapping"):
        runtime_capability._yaml_mapping(sequence_yaml, label="yaml")

    for value in (None, "", "bad\\path", "bad\x01path"):
        with pytest.raises(runtime_capability.RuntimeCapabilityError, match="path is invalid"):
            runtime_capability._safe_relative_parts(value, label="path")  # type: ignore[arg-type]
    for value in ("/absolute", "../escape"):
        with pytest.raises(runtime_capability.RuntimeCapabilityError, match="not contained"):
            runtime_capability._safe_relative_parts(value, label="path")
    assert runtime_capability._safe_relative_parts(
        "../mesh.stl",
        label="path",
        allow_parent=True,
    ) == Path("../mesh.stl")

    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="directory is missing"):
        runtime_capability._real_directory_inside(root, "missing", label="directory")
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="escapes"):
        runtime_capability._real_file_inside(root, tmp_path / "outside", label="file")
    with pytest.raises(runtime_capability.RuntimeCapabilityError, match="file is missing"):
        runtime_capability._real_file_inside(root, root / "missing", label="file")
