from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import subprocess
import sys
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

import self_improving.harness as harness

try:
    import self_improving.harness.genesis_cpu_backend as backend_module
    from self_improving.harness.genesis_cpu_backend import (
        GENESIS_CPU_PROCESS_ENV,
        GENESIS_WORLD_COMMIT,
        GenesisCpuAssetProbeBackend,
        GenesisCpuBackendUnavailableError,
    )

    from self_improving.harness.genesis_asset_probe import GenesisAssetProbeBackend
except ImportError:
    backend_module = None  # type: ignore[assignment]
    GenesisAssetProbeBackend = None  # type: ignore[assignment,misc]
    GENESIS_CPU_PROCESS_ENV = None  # type: ignore[assignment]
    GENESIS_WORLD_COMMIT = None  # type: ignore[assignment]
    GenesisCpuAssetProbeBackend = None  # type: ignore[assignment,misc]
    GenesisCpuBackendUnavailableError = None  # type: ignore[assignment,misc]


_FIXED_GENESIS_ROOT = Path("/home/jingxiang/gujie/gen-env/external/genesis-world")
_FIXED_COMMIT = "0e74bf392781884ccad765c3f344419c86b872ca"
_RUNTIME_IMPLEMENTATION_SHA256 = "3" * 64
_REQUIRED_PROCESS_ENV = {
    "GS_HEADLESS": "1",
    "PYGLET_HEADLESS": "1",
    "GS_PARA_LEVEL": "0",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
}


def _require_api() -> None:
    assert backend_module is not None, "public Genesis CPU backend module is absent"
    assert GenesisAssetProbeBackend is not None
    assert GenesisCpuAssetProbeBackend is not None
    assert GenesisCpuBackendUnavailableError is not None
    assert GENESIS_WORLD_COMMIT == _FIXED_COMMIT
    assert type(GENESIS_CPU_PROCESS_ENV) is MappingProxyType
    assert dict(GENESIS_CPU_PROCESS_ENV) == _REQUIRED_PROCESS_ENV
    for name, value in {
        "GenesisCpuAssetProbeBackend": GenesisCpuAssetProbeBackend,
        "GenesisCpuBackendUnavailableError": GenesisCpuBackendUnavailableError,
    }.items():
        assert getattr(harness, name) is value
        assert name in harness.__all__


def _call_name(node: ast.Call) -> str:
    function = node.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return ""


def _assigned_attribute_names(nodes: list[ast.stmt]) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.Module(body=nodes, type_ignores=[])):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names.update(target.attr for target in targets if isinstance(target, ast.Attribute))
    return names


def _function(tree: ast.Module, class_name: str, function_name: str) -> ast.FunctionDef:
    owner = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return next(
        node
        for node in owner.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )


def test_cpu_backend_is_lazy_public_and_implements_only_the_probe_protocol() -> None:
    _require_api()
    assert GenesisCpuAssetProbeBackend is not None
    assert GenesisAssetProbeBackend is not None
    signature = inspect.signature(GenesisCpuAssetProbeBackend)
    assert tuple(signature.parameters) == (
        "genesis_source_root",
        "runtime_implementation_sha256",
    )
    backend = GenesisCpuAssetProbeBackend(
        genesis_source_root=_FIXED_GENESIS_ROOT,
        runtime_implementation_sha256=_RUNTIME_IMPLEMENTATION_SHA256,
    )
    assert isinstance(backend, GenesisAssetProbeBackend)
    assert backend.backend == "genesis"
    assert backend.implementation_sha256 == _RUNTIME_IMPLEMENTATION_SHA256
    assert backend.loader_contract == "harness.genesis_rigid_loader.v1"

    public_methods = {
        name
        for name, value in inspect.getmembers(GenesisCpuAssetProbeBackend, inspect.isfunction)
        if not name.startswith("_")
    }
    assert public_methods == {"load", "observe_initial", "step"}
    forbidden = {"camera", "render", "image", "video", "media", "record"}
    for method_name in public_methods:
        parameters = inspect.signature(getattr(GenesisCpuAssetProbeBackend, method_name)).parameters
        assert not forbidden.intersection(name.lower() for name in parameters)

    assert importlib.util.find_spec("genesis") is None
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json,sys; "
                "import self_improving.harness.genesis_cpu_backend as candidate; "
                "print(json.dumps({'genesis_loaded': 'genesis' in sys.modules, "
                "'commit': candidate.GENESIS_WORLD_COMMIT}, sort_keys=True))"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == {
        "commit": _FIXED_COMMIT,
        "genesis_loaded": False,
    }


def test_cpu_backend_source_has_no_renderer_camera_or_media_dependency() -> None:
    _require_api()
    assert backend_module is not None
    source_path = Path(backend_module.__file__).resolve()
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])
    assert not {"PIL", "cv2", "imageio"}.intersection(imported_roots)

    forbidden_calls = {
        "Camera",
        "Rasterizer",
        "RayTracer",
        "Renderer",
        "add_camera",
        "render",
        "start_recording",
        "stop_recording",
    }
    call_names = {_call_name(node) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    assert not forbidden_calls.intersection(call_names)
    assert any(
        isinstance(node, ast.ClassDef) and node.name == "_NoopVisualizer" for node in tree.body
    )

    visualizer_guards = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Try)
        and "Visualizer" in _assigned_attribute_names(node.body)
        and "Visualizer" in _assigned_attribute_names(node.finalbody)
    ]
    assert len(visualizer_guards) == 1, (
        "the Genesis Scene Visualizer patch must have one explicit try/finally restoration"
    )

    initial = _function(tree, "GenesisCpuAssetProbeBackend", "observe_initial")
    initial_calls = {_call_name(node) for node in ast.walk(initial) if isinstance(node, ast.Call)}
    assert "detect_collision" in initial_calls
    assert "get_contacts" not in initial_calls
    assert "HARN_GENESIS_INITIAL_COLLISION" in ast.unparse(initial)

    step = _function(tree, "GenesisCpuAssetProbeBackend", "step")
    step_calls = {_call_name(node) for node in ast.walk(step) if isinstance(node, ast.Call)}
    assert {"step", "get_contacts"}.issubset(step_calls)
    scene_step = next(
        node
        for node in ast.walk(step)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "step"
    )
    keyword_values: dict[str, Any] = {
        keyword.arg: ast.literal_eval(keyword.value)
        for keyword in scene_step.keywords
        if keyword.arg is not None
    }
    assert keyword_values["update_visualizer"] is False
    assert keyword_values["refresh_visualizer"] is False


@pytest.mark.skipif(
    importlib.util.find_spec("genesis") is None,
    reason="formal CPU run requires the fixed Genesis environment",
)
def test_formal_runtime_environment_is_fixed_cpu_genesis_checkout() -> None:
    _require_api()
    assert _FIXED_GENESIS_ROOT.is_dir()
    assert (
        subprocess.run(
            ["git", "-C", str(_FIXED_GENESIS_ROOT), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == _FIXED_COMMIT
    )
