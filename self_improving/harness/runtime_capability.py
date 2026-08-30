"""Ephemeral capability negotiation for the RoboTwin replay worker.

The document produced here is a short-lived protocol message.  A trusted
executor must validate it and hash its canonical bytes immediately before a
run; it is not standalone evidence, a receipt, or a durable machine identity.
This module is the single seam shared by the worker adapter and executor for
schema constants, dependency/resource attestation, canonical encoding, and
shape validation.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata as metadata
import json
import os
import platform
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

import yaml

import scene_gen
from scene_gen.catalog import CATALOG_SCHEMA_VERSION
from scene_gen.schema import RESOLVED_SCHEMA_VERSION

from .runtime_events import RUNTIME_EVENT_SCHEMA, RuntimeEventCodec, RuntimeEventKind

RUNTIME_CAPABILITY_SCHEMA = "harness.robotwin_runtime_capability.v1"
RUNTIME_CAPABILITY_PROTOCOL_VERSION = 1
RUNTIME_BACKEND_ID = "robotwin.sapien"
RUNTIME_EVIDENCE_SCHEMA = "robotwin.scene_runtime_evidence.v2"
RUNTIME_VALIDATION_SCHEMA = "robotwin.scene_validation.v1"

REQUIRED_RUNTIME_DISTRIBUTIONS = (
    "Pillow",
    "PyYAML",
    "gymnasium",
    "h5py",
    "imageio",
    "imageio-ffmpeg",
    "mplib",
    "nvidia-curobo",
    "numpy",
    "open3d",
    "opencv-python",
    "sapien",
    "torch",
    "toppra",
    "transforms3d",
    "trimesh",
)

RUNTIME_MEDIA_ARTIFACT_PATHS = (
    "preview_head.png",
    "preview_segmentation.png",
    "preview_world_left.png",
    "preview_world_right.png",
    "observer_start.png",
    "observer_mid.png",
    "observer_end.png",
    "observer_runtime.mp4",
)
RUNTIME_EVIDENCE_ARTIFACT_PATHS = (
    "runtime_evidence.json",
    "runtime_validation_report.json",
)
RUNTIME_ARTIFACT_PATHS = RUNTIME_MEDIA_ARTIFACT_PATHS + RUNTIME_EVIDENCE_ARTIFACT_PATHS

EXPECTED_RUNTIME_PARAMETERS: dict[str, dict[str, Any]] = {
    "precheck_steps": {"type": "integer", "minimum": 0, "default": 0},
    "settle_steps": {"type": "integer", "minimum": 1, "default": 900},
    "settle_converge_max": {"type": "integer", "minimum": 0, "default": 0},
    "contact_window_steps": {"type": "integer", "minimum": 1, "default": 60},
    "video_frames": {"type": "integer", "minimum": 0, "default": 120},
    "fps": {"type": "integer", "minimum": 1, "default": 12},
    "min_visible_pixels": {"type": "integer", "minimum": 0, "default": 64},
    "checkpoint_steps": {"type": "integer", "minimum": 1, "default": 120},
    "evidence_only": {"type": "boolean", "default": False},
}

SCENE_GEN_SOURCE_ROOT = Path(scene_gen.__file__).absolute().parent
RUNTIME_EVENTS_SOURCE_PATH = Path(sys.modules[RuntimeEventCodec.__module__].__file__).absolute()
RUNTIME_CAPABILITY_SOURCE_PATH = Path(__file__).absolute()
PYTHON_EXECUTABLE_PATH = Path(sys.executable).resolve()

RUNTIME_ENVIRONMENT_ALLOWLIST = frozenset(
    {
        "CONDA_PREFIX",
        "CUDA_DEVICE_ORDER",
        "CUDA_HOME",
        "CUDA_PATH",
        "CUDA_VISIBLE_DEVICES",
        "DISPLAY",
        "EGL_DEVICE_ID",
        "EGL_PLATFORM",
        "HOME",
        "LANG",
        "LC_ALL",
        "LD_LIBRARY_PATH",
        "LIBGL_DRIVERS_PATH",
        "NVIDIA_DRIVER_CAPABILITIES",
        "NVIDIA_VISIBLE_DEVICES",
        "PATH",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONPATH",
        "PYTHONUNBUFFERED",
        "TMPDIR",
        "TORCH_HOME",
        "VIRTUAL_ENV",
        "VK_ICD_FILENAMES",
        "XAUTHORITY",
        "XDG_CACHE_HOME",
        "__GLX_VENDOR_LIBRARY_NAME",
    }
)
RUNTIME_ENVIRONMENT_KEYS = tuple(sorted(RUNTIME_ENVIRONMENT_ALLOWLIST))

# Compatibility name retained while the v1 document remains unreleased. The
# identity now covers the complete effective worker environment, not only GPU
# selectors.
ACCELERATOR_ENVIRONMENT_KEYS = RUNTIME_ENVIRONMENT_KEYS

_RUNTIME_IMPORT_RESOURCES = (
    "assets/objects/objaverse/list.json",
    "assets/objects/same.json",
)
_MAX_SMALL_COMMAND_BYTES = 4096
_MAX_COMMAND_STDERR_BYTES = 65_536
_MAX_GIT_DIFF_BYTES = 268_435_456
_MAX_UNTRACKED_LIST_BYTES = 16_777_216
_SMALL_COMMAND_TIMEOUT_SECONDS = 5.0
_LARGE_COMMAND_TIMEOUT_SECONDS = 30.0
_BASE_TASK_BOOTSTRAP_SOURCE = (
    "import os,sys;sys.path.insert(0,os.getcwd());from envs._base_task import Base_Task"
)
_BASE_TASK_BOOTSTRAP_COMMAND = (
    "python",
    "-I",
    "-B",
    "-c",
    _BASE_TASK_BOOTSTRAP_SOURCE,
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TASK_CONFIG_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_CAPABILITY_FIELDS = frozenset(
    {
        "schema_version",
        "protocol_version",
        "backend_id",
        "runner_sha256",
        "python",
        "packages",
        "runtime_binaries",
        "accelerator",
        "bootstrap",
        "source_modules",
        "robotwin",
        "task_config",
        "supported",
    }
)
_PACKAGE_FIELDS = frozenset({"version", "record_sha256", "direct_url_sha256"})
_ROBOTWIN_FIELDS = frozenset(
    {
        "commit",
        "dirty",
        "tracked_diff_sha256",
        "untracked_manifest_sha256",
        "tree_state_sha256",
    }
)


class RuntimeCapabilityError(RuntimeError):
    """Raised when a runtime cannot be attested without guessing."""


@dataclass(frozen=True, slots=True)
class _CommandResult:
    stdout: bytes | None
    stdout_sha256: str
    stdout_bytes: int


def canonical_capability_bytes(value: Any) -> bytes:
    """Encode one capability document as canonical UTF-8 JSONL bytes."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def runtime_capability_sha256(value: Any) -> str:
    """Hash the canonical temporary negotiation document."""

    return _sha256_bytes(canonical_capability_bytes(value))


def describe_runtime_capability(
    *,
    robotwin_root: Path,
    task_config: str,
    runner_path: Path,
) -> dict[str, Any]:
    """Describe one runnable worker configuration or fail closed."""

    root = Path(robotwin_root).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeCapabilityError("RoboTwin root must be a directory")
    if _TASK_CONFIG_NAME.fullmatch(task_config) is None or ".." in task_config:
        raise RuntimeCapabilityError("task config name is not canonical")
    task = _task_identity(root, task_config)
    # This import executes before distribution metadata is collected so the
    # capability fails on the actual Base_Task import boundary, including any
    # transitive module or import-time resource dependency.
    bootstrap = _bootstrap_identity(root)
    document = {
        "schema_version": RUNTIME_CAPABILITY_SCHEMA,
        "protocol_version": RUNTIME_CAPABILITY_PROTOCOL_VERSION,
        "backend_id": RUNTIME_BACKEND_ID,
        "runner_sha256": _regular_file_sha256(Path(runner_path), label="runtime runner"),
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "executable_sha256": _regular_file_sha256(
                PYTHON_EXECUTABLE_PATH,
                label="Python executable",
            ),
        },
        "packages": _distribution_identities(),
        "runtime_binaries": _runtime_binary_identities(),
        "accelerator": _accelerator_identity(),
        "bootstrap": bootstrap,
        "source_modules": {
            "scene_gen": {
                "tree_sha256": _tree_identity(
                    SCENE_GEN_SOURCE_ROOT,
                    label="scene_gen source",
                    ignore_bytecode=True,
                )["tree_sha256"],
            },
            "runtime_events": {
                "sha256": _regular_file_sha256(
                    RUNTIME_EVENTS_SOURCE_PATH,
                    label="runtime event source",
                ),
            },
            "runtime_capability": {
                "sha256": _regular_file_sha256(
                    RUNTIME_CAPABILITY_SOURCE_PATH,
                    label="runtime capability source",
                ),
            },
        },
        "robotwin": _robotwin_identity(root),
        "task_config": task,
        "supported": {
            "resolved_scene_schemas": [RESOLVED_SCHEMA_VERSION],
            "asset_catalog_schemas": [CATALOG_SCHEMA_VERSION],
            "runtime_evidence_schemas": [RUNTIME_EVIDENCE_SCHEMA],
            "validation_report_schemas": [RUNTIME_VALIDATION_SCHEMA],
            "parameters": EXPECTED_RUNTIME_PARAMETERS,
            "event_protocol": {
                "schema_version": RUNTIME_EVENT_SCHEMA,
                "transport": "dedicated_fd_jsonl",
                "kinds": [kind.value for kind in RuntimeEventKind],
                "artifact_paths": list(RUNTIME_ARTIFACT_PATHS),
            },
        },
    }
    validate_runtime_capability_document(document)
    return document


def validate_runtime_capability_document(value: object) -> Mapping[str, Any]:
    """Validate the closed, path-free capability shape and return it unchanged."""

    document = _exact_mapping(value, _CAPABILITY_FIELDS, label="document")
    if document["schema_version"] != RUNTIME_CAPABILITY_SCHEMA:
        raise RuntimeCapabilityError("capability schema_version is unsupported")
    if document["protocol_version"] != RUNTIME_CAPABILITY_PROTOCOL_VERSION:
        raise RuntimeCapabilityError("capability protocol_version is unsupported")
    if document["backend_id"] != RUNTIME_BACKEND_ID:
        raise RuntimeCapabilityError("capability backend_id is unsupported")
    _digest(document["runner_sha256"], label="runner_sha256")

    python = _exact_mapping(
        document["python"],
        frozenset({"implementation", "version", "executable_sha256"}),
        label="python",
    )
    for field in ("implementation", "version"):
        if not isinstance(python[field], str) or not python[field]:
            raise RuntimeCapabilityError(f"capability python.{field} is invalid")
    _digest(python["executable_sha256"], label="python.executable_sha256")

    packages = _exact_mapping(
        document["packages"],
        frozenset(REQUIRED_RUNTIME_DISTRIBUTIONS),
        label="packages",
    )
    for name in REQUIRED_RUNTIME_DISTRIBUTIONS:
        package = _exact_mapping(packages[name], _PACKAGE_FIELDS, label=f"packages.{name}")
        if not isinstance(package["version"], str) or not package["version"]:
            raise RuntimeCapabilityError(f"capability packages.{name}.version is invalid")
        _digest(package["record_sha256"], label=f"packages.{name}.record_sha256")
        direct = package["direct_url_sha256"]
        if direct is not None:
            _digest(direct, label=f"packages.{name}.direct_url_sha256")

    binaries = _exact_mapping(
        document["runtime_binaries"],
        frozenset({"ffmpeg"}),
        label="runtime_binaries",
    )
    _validate_binary_identity(binaries["ffmpeg"], label="runtime_binaries.ffmpeg")
    _validate_accelerator(document["accelerator"])
    bootstrap = _exact_mapping(
        document["bootstrap"],
        frozenset({"kind", "source_sha256", "command_sha256"}),
        label="bootstrap",
    )
    if bootstrap["kind"] != "python.import_base_task.v1":
        raise RuntimeCapabilityError("capability bootstrap kind is unsupported")
    _digest(bootstrap["source_sha256"], label="bootstrap.source_sha256")
    _digest(bootstrap["command_sha256"], label="bootstrap.command_sha256")

    source_modules = _exact_mapping(
        document["source_modules"],
        frozenset({"scene_gen", "runtime_events", "runtime_capability"}),
        label="source_modules",
    )
    scene_gen_source = _exact_mapping(
        source_modules["scene_gen"],
        frozenset({"tree_sha256"}),
        label="source_modules.scene_gen",
    )
    event_source = _exact_mapping(
        source_modules["runtime_events"],
        frozenset({"sha256"}),
        label="source_modules.runtime_events",
    )
    capability_source = _exact_mapping(
        source_modules["runtime_capability"],
        frozenset({"sha256"}),
        label="source_modules.runtime_capability",
    )
    _digest(scene_gen_source["tree_sha256"], label="scene_gen.tree_sha256")
    _digest(event_source["sha256"], label="runtime_events.sha256")
    _digest(capability_source["sha256"], label="runtime_capability.sha256")

    robotwin = _exact_mapping(document["robotwin"], _ROBOTWIN_FIELDS, label="robotwin")
    if (
        not isinstance(robotwin["commit"], str)
        or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", robotwin["commit"]) is None
    ):
        raise RuntimeCapabilityError("capability robotwin.commit is invalid")
    if type(robotwin["dirty"]) is not bool:
        raise RuntimeCapabilityError("capability robotwin.dirty is invalid")
    for field in (
        "tracked_diff_sha256",
        "untracked_manifest_sha256",
        "tree_state_sha256",
    ):
        _digest(robotwin[field], label=f"robotwin.{field}")

    _validate_task_identity(document["task_config"])
    _validate_supported(document["supported"])
    return document


def _runtime_binary_identities() -> dict[str, dict[str, Any]]:
    try:
        imageio_ffmpeg = importlib.import_module("imageio_ffmpeg")
        raw_path = imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, AttributeError, OSError) as error:
        raise RuntimeCapabilityError("imageio-ffmpeg executable cannot be resolved") from error
    if not isinstance(raw_path, str) or not raw_path:
        raise RuntimeCapabilityError("imageio-ffmpeg returned an invalid executable path")
    return {"ffmpeg": _binary_identity(Path(raw_path), label="FFmpeg executable")}


def _accelerator_identity() -> dict[str, Any]:
    raw_executable = shutil.which("nvidia-smi")
    if not isinstance(raw_executable, str) or not raw_executable:
        raise RuntimeCapabilityError("nvidia-smi executable is missing")
    executable = Path(raw_executable)
    result = _bounded_command(
        (
            str(executable),
            "--query-gpu=uuid,name,driver_version",
            "--format=csv,noheader,nounits",
        ),
        cwd=None,
        timeout_seconds=_SMALL_COMMAND_TIMEOUT_SECONDS,
        max_stdout_bytes=65_536,
        max_stderr_bytes=_MAX_COMMAND_STDERR_BYTES,
        capture_stdout=True,
    )
    if result.stdout is None:
        raise RuntimeCapabilityError("nvidia-smi query did not return captured output")
    try:
        text = result.stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise RuntimeCapabilityError("nvidia-smi query is not UTF-8") from error
    gpus: list[dict[str, str]] = []
    for line in text.splitlines():
        fields = [field.strip() for field in line.split(",", 2)]
        if len(fields) != 3 or any(not field or len(field) > 256 for field in fields):
            raise RuntimeCapabilityError("nvidia-smi query returned an invalid GPU row")
        if any(any(ord(character) < 32 for character in field) for field in fields):
            raise RuntimeCapabilityError("nvidia-smi query returned a control character")
        gpus.append(
            {
                "uuid": fields[0],
                "name": fields[1],
                "driver_version": fields[2],
            }
        )
    if not gpus or len({gpu["uuid"] for gpu in gpus}) != len(gpus):
        raise RuntimeCapabilityError("nvidia-smi query returned no unique GPUs")
    gpus.sort(key=lambda gpu: (gpu["uuid"], gpu["name"], gpu["driver_version"]))
    environment: dict[str, dict[str, Any]] = {}
    for name in ACCELERATOR_ENVIRONMENT_KEYS:
        present = name in os.environ
        environment[name] = {
            "present": present,
            "value_sha256": (_sha256_bytes(os.environ[name].encode("utf-8")) if present else None),
        }
    return {
        "probe": {
            "kind": "nvidia-smi.query-gpu.v1",
            **_binary_identity(executable, label="nvidia-smi executable"),
        },
        "gpus": gpus,
        "selection_environment": environment,
    }


def _bootstrap_identity(root: Path) -> dict[str, str]:
    _bounded_command(
        (
            str(PYTHON_EXECUTABLE_PATH),
            "-I",
            "-B",
            "-c",
            _BASE_TASK_BOOTSTRAP_SOURCE,
        ),
        cwd=root,
        timeout_seconds=_LARGE_COMMAND_TIMEOUT_SECONDS,
        max_stdout_bytes=65_536,
        max_stderr_bytes=_MAX_COMMAND_STDERR_BYTES,
        capture_stdout=False,
    )
    return {
        "kind": "python.import_base_task.v1",
        "source_sha256": _sha256_bytes(_BASE_TASK_BOOTSTRAP_SOURCE.encode("utf-8")),
        "command_sha256": _sha256_bytes(
            canonical_capability_bytes(list(_BASE_TASK_BOOTSTRAP_COMMAND))
        ),
    }


def _distribution_identities() -> dict[str, dict[str, str | None]]:
    result: dict[str, dict[str, str | None]] = {}
    for name in REQUIRED_RUNTIME_DISTRIBUTIONS:
        try:
            distribution = metadata.distribution(name)
        except metadata.PackageNotFoundError as error:
            raise RuntimeCapabilityError(
                f"required runtime distribution is missing: {name}"
            ) from error
        version = distribution.version
        if not isinstance(version, str) or not version:
            raise RuntimeCapabilityError(f"required runtime distribution has no version: {name}")
        record = distribution.read_text("RECORD")
        if not isinstance(record, str) or not record:
            raise RuntimeCapabilityError(f"required runtime distribution has no RECORD: {name}")
        direct_url = distribution.read_text("direct_url.json")
        if direct_url is not None and (not isinstance(direct_url, str) or not direct_url):
            raise RuntimeCapabilityError(
                f"runtime distribution has an invalid direct_url.json: {name}"
            )
        result[name] = {
            "version": version,
            "record_sha256": _sha256_bytes(record.encode("utf-8")),
            "direct_url_sha256": (
                _sha256_bytes(direct_url.encode("utf-8")) if direct_url is not None else None
            ),
        }
    return result


def _binary_identity(path: Path, *, label: str) -> dict[str, Any]:
    path = Path(path).absolute()
    if path.is_symlink() or not path.is_file():
        raise RuntimeCapabilityError(f"{label} must be a real regular file")
    size = path.stat().st_size
    if size < 1:
        raise RuntimeCapabilityError(f"{label} must not be empty")
    return {"sha256": _sha256_file(path), "bytes": size}


def _validate_binary_identity(value: object, *, label: str) -> None:
    identity = _exact_mapping(
        value,
        frozenset({"sha256", "bytes"}),
        label=label,
    )
    _digest(identity["sha256"], label=f"{label}.sha256")
    if type(identity["bytes"]) is not int or identity["bytes"] < 1:
        raise RuntimeCapabilityError(f"capability {label}.bytes is invalid")


def _validate_accelerator(value: object) -> None:
    accelerator = _exact_mapping(
        value,
        frozenset({"probe", "gpus", "selection_environment"}),
        label="accelerator",
    )
    probe = _exact_mapping(
        accelerator["probe"],
        frozenset({"kind", "sha256", "bytes"}),
        label="accelerator.probe",
    )
    if probe["kind"] != "nvidia-smi.query-gpu.v1":
        raise RuntimeCapabilityError("capability accelerator probe is unsupported")
    _validate_binary_identity(
        {"sha256": probe["sha256"], "bytes": probe["bytes"]},
        label="accelerator.probe",
    )
    gpus = accelerator["gpus"]
    if not isinstance(gpus, list) or not gpus:
        raise RuntimeCapabilityError("capability accelerator GPUs are invalid")
    previous: tuple[str, str, str] | None = None
    seen: set[str] = set()
    for gpu_value in gpus:
        gpu = _exact_mapping(
            gpu_value,
            frozenset({"uuid", "name", "driver_version"}),
            label="accelerator GPU",
        )
        fields = tuple(gpu[field] for field in ("uuid", "name", "driver_version"))
        if any(not isinstance(field, str) or not field for field in fields):
            raise RuntimeCapabilityError("capability accelerator GPU field is invalid")
        if fields[0] in seen or (previous is not None and fields < previous):
            raise RuntimeCapabilityError("capability accelerator GPUs are not unique and sorted")
        seen.add(fields[0])
        previous = fields
    environment = _exact_mapping(
        accelerator["selection_environment"],
        frozenset(ACCELERATOR_ENVIRONMENT_KEYS),
        label="accelerator.selection_environment",
    )
    for name in ACCELERATOR_ENVIRONMENT_KEYS:
        item = _exact_mapping(
            environment[name],
            frozenset({"present", "value_sha256"}),
            label=f"accelerator.selection_environment.{name}",
        )
        if type(item["present"]) is not bool:
            raise RuntimeCapabilityError("capability accelerator environment presence is invalid")
        if item["present"]:
            _digest(item["value_sha256"], label=f"accelerator environment {name}")
        elif item["value_sha256"] is not None:
            raise RuntimeCapabilityError("capability absent accelerator environment has a digest")


def _task_identity(root: Path, task_config: str) -> dict[str, Any]:
    task_path = _task_config_path(root, task_config)
    registry_path = _real_file_inside(
        root,
        root / "env_cfg" / "task_config" / "_embodiment_config.yml",
        label="embodiment registry",
    )
    task_document = _yaml_mapping(task_path, label="task config")
    registry = _yaml_mapping(registry_path, label="embodiment registry")
    selection = _embodiment_selection(task_document)
    closure = _embodiment_closure(root, registry, selection)
    import_resources = [
        _json_resource_identity(root, relative_path) for relative_path in _RUNTIME_IMPORT_RESOURCES
    ]
    return {
        "path": task_path.relative_to(root).as_posix(),
        "sha256": _sha256_file(task_path),
        "registry": {
            "path": registry_path.relative_to(root).as_posix(),
            "sha256": _sha256_file(registry_path),
        },
        "embodiment_closure": closure,
        "import_resources": import_resources,
    }


def _task_config_path(root: Path, task_config: str) -> Path:
    candidates = (
        root / "task_config" / f"{task_config}.yml",
        root / "env_cfg" / "task_config" / f"{task_config}.yml",
    )
    for candidate in candidates:
        if candidate.exists():
            return _real_file_inside(root, candidate, label="task config")
    raise RuntimeCapabilityError(f"RoboTwin task config is missing: {task_config}")


def _json_resource_identity(root: Path, relative_path: str) -> dict[str, Any]:
    path = _real_file_inside(
        root,
        root / _safe_relative_parts(relative_path, label="runtime import resource"),
        label="runtime import resource",
    )
    size = path.stat().st_size
    if size < 1 or size > 4_194_304:
        raise RuntimeCapabilityError("runtime import resource has an invalid byte length")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeCapabilityError("runtime import resource is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise RuntimeCapabilityError("runtime import resource must be a JSON object")
    if relative_path.endswith("/list.json"):
        expected = {
            "item_names": list,
            "list_of_items": dict,
            "z_max": dict,
            "radius": dict,
            "z_offset": dict,
        }
        if any(
            not isinstance(value.get(name), expected_type)
            for name, expected_type in expected.items()
        ):
            raise RuntimeCapabilityError("objaverse list.json has an invalid import shape")
    return {"path": relative_path, "sha256": _sha256_file(path), "bytes": size}


def _embodiment_selection(task_document: Mapping[str, Any]) -> tuple[str, ...]:
    raw = task_document.get("embodiment")
    if not isinstance(raw, list) or len(raw) not in {1, 3}:
        raise RuntimeCapabilityError(
            "task embodiment must contain one name or two names and distance"
        )
    names = raw[:1] if len(raw) == 1 else raw[:2]
    if any(not isinstance(name, str) or not name or "/" in name or "\\" in name for name in names):
        raise RuntimeCapabilityError("task embodiment name is invalid")
    if len(raw) == 3 and type(raw[2]) not in {int, float}:
        raise RuntimeCapabilityError("task embodiment distance is invalid")
    return tuple(names)


def _embodiment_closure(
    root: Path,
    registry: Mapping[str, Any],
    selection: tuple[str, ...],
) -> dict[str, Any]:
    selected_roots: list[Path] = []
    resource_names: list[str] = []
    for name in selection:
        entry = registry.get(name)
        if not isinstance(entry, dict) or set(entry) != {"file_path"}:
            raise RuntimeCapabilityError(f"embodiment registry entry is invalid: {name}")
        file_path = entry["file_path"]
        if not isinstance(file_path, str) or not file_path:
            raise RuntimeCapabilityError(f"embodiment file_path is invalid: {name}")
        resource_root = _real_directory_inside(root, file_path, label=f"embodiment {name}")
        selected_roots.append(resource_root)
        resource_names.append(name)

    unique: list[tuple[str, Path]] = []
    for name, resource_root in zip(resource_names, selected_roots):
        if resource_root not in [path for _, path in unique]:
            unique.append((name, resource_root))

    resources: list[dict[str, Any]] = []
    same_root = len(selected_roots) == 1 or selected_roots[0] == selected_roots[-1]
    for name, resource_root in unique:
        config_path = _real_file_inside(
            resource_root,
            resource_root / "config.yml",
            label=f"embodiment {name} config",
        )
        config = _yaml_mapping(config_path, label=f"embodiment {name} config")
        required = [config_path]
        for key in ("urdf_path", "srdf_path"):
            raw = config.get(key)
            if not isinstance(raw, str) or not raw:
                raise RuntimeCapabilityError(f"embodiment {name} has no {key}")
            required.append(
                _real_file_inside(
                    resource_root,
                    resource_root / _safe_relative_parts(raw, label=f"embodiment {name} {key}"),
                    label=f"embodiment {name} {key}",
                )
            )
        required.extend(_urdf_mesh_files(resource_root, required[1], name=name))
        if config.get("planner", "mplib_RRT") == "curobo":
            curobo_names = ("curobo_left.yml", "curobo_right.yml") if same_root else ("curobo.yml",)
            for filename in curobo_names:
                required.append(
                    _real_file_inside(
                        resource_root,
                        resource_root / filename,
                        label=f"embodiment {name} {filename}",
                    )
                )
        identity = _tree_identity(resource_root, label=f"embodiment {name}")
        required_files = sorted(
            (
                {
                    "path": path.relative_to(resource_root).as_posix(),
                    "sha256": _sha256_file(path),
                }
                for path in set(required)
            ),
            key=lambda item: item["path"],
        )
        resources.append(
            {
                "name": name,
                "path": resource_root.relative_to(root).as_posix(),
                **identity,
                "required_files": required_files,
            }
        )
    return {
        "strategy": "selected_embodiment_tree.v1",
        "selection": list(selection),
        "resources": resources,
    }


def _urdf_mesh_files(resource_root: Path, urdf_path: Path, *, name: str) -> list[Path]:
    try:
        document = ElementTree.parse(urdf_path)
    except (ElementTree.ParseError, OSError) as error:
        raise RuntimeCapabilityError(f"embodiment {name} URDF is not parseable") from error
    result: list[Path] = []
    for element in document.iter():
        if element.tag.rsplit("}", 1)[-1] != "mesh":
            continue
        filename = element.attrib.get("filename")
        if not isinstance(filename, str) or not filename or filename.startswith("$("):
            raise RuntimeCapabilityError(f"embodiment {name} URDF mesh reference is invalid")
        if filename.startswith("package://"):
            filename = filename.removeprefix("package://")
        mesh_path = urdf_path.parent / _safe_relative_parts(
            filename,
            label=f"embodiment {name} URDF mesh",
            allow_parent=True,
        )
        result.append(
            _real_file_inside(resource_root, mesh_path, label=f"embodiment {name} URDF mesh")
        )
    return result


def _tree_identity(
    root: Path,
    *,
    label: str,
    ignore_bytecode: bool = False,
) -> dict[str, Any]:
    root = Path(root).absolute()
    if root.is_symlink() or not root.is_dir():
        raise RuntimeCapabilityError(f"{label} root must be a real directory")
    digest = hashlib.sha256()
    file_count = 0
    byte_count = 0
    for candidate in sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix()):
        relative = candidate.relative_to(root)
        if candidate.is_symlink():
            raise RuntimeCapabilityError(f"{label} contains a symlink")
        if ignore_bytecode and ("__pycache__" in relative.parts or candidate.suffix == ".pyc"):
            continue
        if candidate.is_dir():
            continue
        if not candidate.is_file():
            raise RuntimeCapabilityError(f"{label} contains a non-regular entry")
        encoded_path = relative.as_posix().encode("utf-8", errors="strict")
        size = candidate.stat().st_size
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(bytes.fromhex(_sha256_file(candidate)))
        file_count += 1
        byte_count += size
    if file_count == 0:
        raise RuntimeCapabilityError(f"{label} tree must not be empty")
    return {"tree_sha256": digest.hexdigest(), "file_count": file_count, "bytes": byte_count}


def _bounded_command(
    arguments: tuple[str, ...],
    *,
    cwd: Path | None,
    timeout_seconds: float,
    max_stdout_bytes: int,
    max_stderr_bytes: int,
    capture_stdout: bool,
) -> _CommandResult:
    if not arguments or any(not isinstance(value, str) or not value for value in arguments):
        raise RuntimeCapabilityError("capability command arguments are invalid")
    try:
        process = subprocess.Popen(
            arguments,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        raise RuntimeCapabilityError("capability command could not start") from error
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    stdout_digest = hashlib.sha256()
    stdout_parts: list[bytes] = []
    stdout_count = 0
    stderr_count = 0
    deadline = time.monotonic() + timeout_seconds
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeCapabilityError("capability command timed out")
            ready = selector.select(remaining)
            if not ready:
                raise RuntimeCapabilityError("capability command timed out")
            for key, _ in ready:
                chunk = os.read(key.fileobj.fileno(), 65_536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if key.data == "stdout":
                    stdout_count += len(chunk)
                    if stdout_count > max_stdout_bytes:
                        raise RuntimeCapabilityError("capability command stdout exceeded its limit")
                    stdout_digest.update(chunk)
                    if capture_stdout:
                        stdout_parts.append(chunk)
                else:
                    stderr_count += len(chunk)
                    if stderr_count > max_stderr_bytes:
                        raise RuntimeCapabilityError("capability command stderr exceeded its limit")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeCapabilityError("capability command timed out")
        return_code = process.wait(timeout=remaining)
        if return_code != 0:
            raise RuntimeCapabilityError(f"capability command exited with code {return_code}")
    except (OSError, subprocess.TimeoutExpired):
        _terminate_process_group(process)
        raise RuntimeCapabilityError("capability command failed while reading output") from None
    except RuntimeCapabilityError:
        _terminate_process_group(process)
        raise
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
    return _CommandResult(
        stdout=b"".join(stdout_parts) if capture_stdout else None,
        stdout_sha256=stdout_digest.hexdigest(),
        stdout_bytes=stdout_count,
    )


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        # SIGKILL has already been issued. Never let cleanup defeat the
        # generator's own bounded-execution contract.
        return


def _robotwin_identity(root: Path) -> dict[str, Any]:
    commit = _git_bytes(root, "rev-parse", "HEAD").decode("ascii", errors="strict").strip()
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit) is None:
        raise RuntimeCapabilityError("RoboTwin HEAD is not a hexadecimal commit identity")
    repository_root = Path(
        os.fsdecode(_git_bytes(root, "rev-parse", "--show-toplevel").rstrip(b"\n"))
    ).resolve()
    if repository_root != root:
        raise RuntimeCapabilityError("RoboTwin path must be the git repository root")
    tracked_diff = _bounded_command(
        ("git", "diff", "--binary", "--no-ext-diff", "HEAD", "--"),
        cwd=root,
        timeout_seconds=_LARGE_COMMAND_TIMEOUT_SECONDS,
        max_stdout_bytes=_MAX_GIT_DIFF_BYTES,
        max_stderr_bytes=_MAX_COMMAND_STDERR_BYTES,
        capture_stdout=False,
    )
    untracked = _bounded_command(
        ("git", "ls-files", "--others", "--exclude-standard", "-z"),
        cwd=root,
        timeout_seconds=_LARGE_COMMAND_TIMEOUT_SECONDS,
        max_stdout_bytes=_MAX_UNTRACKED_LIST_BYTES,
        max_stderr_bytes=_MAX_COMMAND_STDERR_BYTES,
        capture_stdout=True,
    )
    if untracked.stdout is None:
        raise RuntimeCapabilityError("git untracked query did not return captured output")
    tracked_digest = tracked_diff.stdout_sha256
    untracked_digest = _untracked_manifest(root, untracked.stdout)
    tree_state_digest = _sha256_bytes(
        json.dumps(
            {
                "commit": commit,
                "tracked_diff_sha256": tracked_digest,
                "untracked_manifest_sha256": untracked_digest,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    )
    return {
        "commit": commit,
        "dirty": bool(tracked_diff.stdout_bytes or untracked.stdout_bytes),
        "tracked_diff_sha256": tracked_digest,
        "untracked_manifest_sha256": untracked_digest,
        "tree_state_sha256": tree_state_digest,
    }


def _untracked_manifest(root: Path, paths: bytes) -> str:
    digest = hashlib.sha256()
    for encoded_path in sorted(path for path in paths.split(b"\0") if path):
        relative_path = os.fsdecode(encoded_path)
        candidate = root / relative_path
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        if candidate.is_symlink():
            target = os.fsencode(os.readlink(candidate))
            digest.update(b"L")
            digest.update(len(target).to_bytes(8, "big"))
            digest.update(target)
        elif candidate.is_file():
            digest.update(b"F")
            digest.update(bytes.fromhex(_sha256_file(candidate)))
        else:
            raise RuntimeCapabilityError(
                "RoboTwin untracked entry is not a regular file or symlink"
            )
    return digest.hexdigest()


def _git_bytes(root: Path, *arguments: str) -> bytes:
    try:
        result = _bounded_command(
            ("git", *arguments),
            cwd=root,
            timeout_seconds=_SMALL_COMMAND_TIMEOUT_SECONDS,
            max_stdout_bytes=_MAX_SMALL_COMMAND_BYTES,
            max_stderr_bytes=_MAX_COMMAND_STDERR_BYTES,
            capture_stdout=True,
        )
    except RuntimeCapabilityError as error:
        command = " ".join(("git", *arguments))
        raise RuntimeCapabilityError(f"cannot attest RoboTwin git state with {command}") from error
    if result.stdout is None:
        raise RuntimeCapabilityError("git identity query did not return captured output")
    return result.stdout


def _validate_task_identity(value: object) -> None:
    task = _exact_mapping(
        value,
        frozenset({"path", "sha256", "registry", "embodiment_closure", "import_resources"}),
        label="task_config",
    )
    _logical_path(task["path"], label="task_config.path")
    _digest(task["sha256"], label="task_config.sha256")
    registry = _exact_mapping(
        task["registry"],
        frozenset({"path", "sha256"}),
        label="task_config.registry",
    )
    _logical_path(registry["path"], label="task_config.registry.path")
    _digest(registry["sha256"], label="task_config.registry.sha256")
    import_resources = task["import_resources"]
    if not isinstance(import_resources, list) or len(import_resources) != len(
        _RUNTIME_IMPORT_RESOURCES
    ):
        raise RuntimeCapabilityError("capability runtime import resources are invalid")
    for expected_path, resource_value in zip(_RUNTIME_IMPORT_RESOURCES, import_resources):
        resource = _exact_mapping(
            resource_value,
            frozenset({"path", "sha256", "bytes"}),
            label="runtime import resource",
        )
        if resource["path"] != expected_path:
            raise RuntimeCapabilityError("capability runtime import resource path is invalid")
        _digest(resource["sha256"], label="runtime import resource sha256")
        if type(resource["bytes"]) is not int or resource["bytes"] < 1:
            raise RuntimeCapabilityError("capability runtime import resource bytes is invalid")
    closure = _exact_mapping(
        task["embodiment_closure"],
        frozenset({"strategy", "selection", "resources"}),
        label="task_config.embodiment_closure",
    )
    if closure["strategy"] != "selected_embodiment_tree.v1":
        raise RuntimeCapabilityError("capability embodiment closure strategy is unsupported")
    selection = closure["selection"]
    resources = closure["resources"]
    if (
        not isinstance(selection, list)
        or not selection
        or not all(isinstance(name, str) and name for name in selection)
    ):
        raise RuntimeCapabilityError("capability embodiment selection is invalid")
    if not isinstance(resources, list) or not resources:
        raise RuntimeCapabilityError("capability embodiment resources are invalid")
    for index, resource_value in enumerate(resources):
        resource = _exact_mapping(
            resource_value,
            frozenset({"name", "path", "tree_sha256", "file_count", "bytes", "required_files"}),
            label=f"task_config.embodiment.resources[{index}]",
        )
        if not isinstance(resource["name"], str) or resource["name"] not in selection:
            raise RuntimeCapabilityError("capability embodiment resource name is invalid")
        _logical_path(resource["path"], label="embodiment resource path")
        _digest(resource["tree_sha256"], label="embodiment tree_sha256")
        for field in ("file_count", "bytes"):
            if type(resource[field]) is not int or resource[field] < 1:
                raise RuntimeCapabilityError(f"capability embodiment {field} is invalid")
        files = resource["required_files"]
        if not isinstance(files, list) or not files:
            raise RuntimeCapabilityError("capability embodiment required_files are invalid")
        seen: set[str] = set()
        for file_value in files:
            file = _exact_mapping(
                file_value,
                frozenset({"path", "sha256"}),
                label="embodiment required file",
            )
            path = _logical_path(file["path"], label="embodiment required file path")
            if path in seen:
                raise RuntimeCapabilityError("capability embodiment required file is duplicated")
            seen.add(path)
            _digest(file["sha256"], label="embodiment required file sha256")


def _validate_supported(value: object) -> None:
    supported = _exact_mapping(
        value,
        frozenset(
            {
                "resolved_scene_schemas",
                "asset_catalog_schemas",
                "runtime_evidence_schemas",
                "validation_report_schemas",
                "parameters",
                "event_protocol",
            }
        ),
        label="supported",
    )
    expected_lists = {
        "resolved_scene_schemas": [RESOLVED_SCHEMA_VERSION],
        "asset_catalog_schemas": [CATALOG_SCHEMA_VERSION],
        "runtime_evidence_schemas": [RUNTIME_EVIDENCE_SCHEMA],
        "validation_report_schemas": [RUNTIME_VALIDATION_SCHEMA],
    }
    for field, expected in expected_lists.items():
        if supported[field] != expected:
            raise RuntimeCapabilityError(f"capability {field} is unsupported")
    if supported["parameters"] != EXPECTED_RUNTIME_PARAMETERS:
        raise RuntimeCapabilityError("capability runtime parameters are unsupported")
    event_protocol = supported["event_protocol"]
    expected_event_protocol = {
        "schema_version": RUNTIME_EVENT_SCHEMA,
        "transport": "dedicated_fd_jsonl",
        "kinds": [kind.value for kind in RuntimeEventKind],
        "artifact_paths": list(RUNTIME_ARTIFACT_PATHS),
    }
    if event_protocol != expected_event_protocol:
        raise RuntimeCapabilityError("capability runtime event protocol is unsupported")


def _yaml_mapping(path: Path, *, label: str) -> Mapping[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise RuntimeCapabilityError(f"{label} is not valid UTF-8 YAML") from error
    if not isinstance(value, dict):
        raise RuntimeCapabilityError(f"{label} must be a mapping")
    return value


def _safe_relative_parts(
    value: str,
    *,
    label: str,
    allow_parent: bool = False,
) -> Path:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise RuntimeCapabilityError(f"{label} path is invalid")
    pure = PurePosixPath(value)
    if pure.is_absolute() or (not allow_parent and ".." in pure.parts):
        raise RuntimeCapabilityError(f"{label} path is not contained")
    return Path(*pure.parts)


def _real_directory_inside(root: Path, value: str, *, label: str) -> Path:
    relative = _safe_relative_parts(value, label=label)
    candidate = root / relative
    _reject_symlink_components(root, candidate, label=label)
    if not candidate.is_dir():
        raise RuntimeCapabilityError(f"{label} directory is missing")
    return candidate


def _real_file_inside(root: Path, candidate: Path, *, label: str) -> Path:
    candidate = Path(candidate).absolute()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise RuntimeCapabilityError(f"{label} escapes its declared root") from error
    _reject_symlink_components(root, candidate, label=label)
    if not candidate.is_file():
        raise RuntimeCapabilityError(f"{label} file is missing")
    return candidate


def _reject_symlink_components(root: Path, candidate: Path, *, label: str) -> None:
    relative = candidate.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise RuntimeCapabilityError(f"{label} contains a symlink")


def _regular_file_sha256(path: Path, *, label: str) -> str:
    path = Path(path).absolute()
    if path.is_symlink() or not path.is_file():
        raise RuntimeCapabilityError(f"{label} must be a real regular file")
    return _sha256_file(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _exact_mapping(value: object, fields: frozenset[str], *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise RuntimeCapabilityError(f"capability {label} has an invalid shape")
    return value


def _digest(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise RuntimeCapabilityError(f"capability {label} is not a lowercase SHA-256")
    return value


def _logical_path(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise RuntimeCapabilityError(f"capability {label} is invalid")
    pure = PurePosixPath(value)
    if (
        not value
        or pure.is_absolute()
        or ".." in pure.parts
        or "\\" in value
        or pure.as_posix() != value
    ):
        raise RuntimeCapabilityError(f"capability {label} is invalid")
    return value
