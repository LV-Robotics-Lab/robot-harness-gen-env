"""Installed-distribution checks for harness runtime dependencies."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
HARNESS_PACKAGE = Path("self_improving/harness")
HARNESS_RESOURCE_MEMBERS = ("IMPLEMENTATION_LOG.md",)
SCENE_GEN_PACKAGE = Path("scene_gen")
SCENE_GEN_RESOURCE_MEMBERS = ("AGENTS.md", "envs/AGENTS.md")
LEDGER_PACKAGE = Path("self_improving/asset_pipeline/active/asset_reuse/lib")
LEDGER_MEMBERS = ("__init__.py", "README.md", "conventions.py", "ledger.py")
FROZEN_ASSERTIONS = Path("self_improving/golden_e2e_progress/physics-assertions-v1.json")
CANONICAL_SCHEMAS = Path("self_improving/harness/x2env/json_schemas")
AGENTICSIM_SOURCE = Path("self_improving/asset_pipeline/active/shared/openxsim/source/agenticsim")
RETIRED_RESOURCE_PREFIXES = (
    "self_improving/harness/qualified_skills/",
    "self_improving/harness/json_schemas/",
    "self_improving/harness/native/",
)
QUALIFIED_REPLAY_CLI = Path("self_improving/qualified_replay_cli.py")
RETIRED_CONSOLES = {"robot-harness-compile", "robot-harness-run-qualified-replay"}


def test_legacy_campaign_and_qwen_entrypoints_are_not_active_commands():
    for name in (
        "run_compile_acceptance.py",
        "run_replay_vlm_assessment.py",
        "run_qualified_replay.py",
    ):
        assert not (REPO_ROOT / "script" / name).exists()
    # Canonical consolidation does not retire the stable core's entrypoints.
    for name in ("generate_scene.py", "run_scene_runtime.py", "run_rendered_critic.py"):
        assert (REPO_ROOT / "script" / name).is_file()


def _copy_build_fixture(destination: Path) -> Path:
    """Copy the smallest real source tree that exercises package discovery."""

    source = destination / "source"
    source.mkdir()
    for relative_path in ("pyproject.toml", "README.md"):
        shutil.copy2(REPO_ROOT / relative_path, source / relative_path)
    for relative_path in (
        Path("self_improving/__init__.py"),
        FROZEN_ASSERTIONS,
        *(p.relative_to(REPO_ROOT) for p in (REPO_ROOT / CANONICAL_SCHEMAS).glob("*.json")),
        CANONICAL_SCHEMAS / "api-fields.md",
        Path("self_improving/registry.py"),
        Path("self_improving/harness/__init__.py"),
        Path("scene_gen/__init__.py"),
        Path("scene_gen/envs/__init__.py"),
        *(HARNESS_PACKAGE / name for name in HARNESS_RESOURCE_MEMBERS),
        *(SCENE_GEN_PACKAGE / name for name in SCENE_GEN_RESOURCE_MEMBERS),
        *(LEDGER_PACKAGE / name for name in LEDGER_MEMBERS),
        *(p.relative_to(REPO_ROOT) for p in (REPO_ROOT / AGENTICSIM_SOURCE).rglob("*.py")),
    ):
        target = source / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative_path, target)
    return source


def _offline_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.update(
        {
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INDEX": "1",
        }
    )
    return environment


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=_offline_environment(),
        check=True,
        capture_output=True,
        text=True,
    )


def test_wheel_installs_runtime_source_without_retired_resources(tmp_path: Path) -> None:
    source = _copy_build_fixture(tmp_path)
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()

    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            str(source),
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheelhouse),
        ],
        cwd=tmp_path,
    )
    wheel = next(wheelhouse.glob("*.whl"))
    expected_members = {(LEDGER_PACKAGE / name).as_posix() for name in LEDGER_MEMBERS}
    expected_harness_resources = {
        (HARNESS_PACKAGE / name).as_posix() for name in HARNESS_RESOURCE_MEMBERS
    }
    expected_scene_gen_resources = {
        (SCENE_GEN_PACKAGE / name).as_posix() for name in SCENE_GEN_RESOURCE_MEMBERS
    }
    with zipfile.ZipFile(wheel) as archive:
        members = set(archive.namelist())
        assert expected_members <= members
        assert expected_harness_resources <= members
        assert expected_scene_gen_resources <= members
        assert not any(name.startswith(RETIRED_RESOURCE_PREFIXES) for name in members)
        assert QUALIFIED_REPLAY_CLI.as_posix() not in members
        entry_points_member = next(
            name for name in members if name.endswith(".dist-info/entry_points.txt")
        )
        entries = {
            line.split("=", 1)[0].strip()
            for line in archive.read(entry_points_member).decode("utf-8").splitlines()
            if "=" in line
        }
        assert not RETIRED_CONSOLES & entries
        assert "x2env" in entries
        for member in expected_harness_resources:
            assert archive.read(member) == (REPO_ROOT / member).read_bytes()
        for member in expected_scene_gen_resources:
            assert archive.read(member) == (REPO_ROOT / member).read_bytes()

    installed = tmp_path / "installed"
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            str(wheel),
            "--no-deps",
            "--no-compile",
            "--target",
            str(installed),
        ],
        cwd=tmp_path,
    )
    package_name = "self_improving.asset_pipeline.active.asset_reuse.lib"
    probe = f"""
import importlib
import importlib.resources
import pathlib
import sys

install_root = pathlib.Path({str(installed)!r}).resolve()
sys.path.insert(0, str(install_root))
package = importlib.import_module({package_name!r})
for module_name in ("conventions", "ledger"):
    module = importlib.import_module(f"{package_name}.{{module_name}}")
    assert pathlib.Path(module.__file__).resolve().is_relative_to(install_root)
resources = importlib.resources.files(package)
for resource_name in {LEDGER_MEMBERS!r}:
    assert resources.joinpath(resource_name).is_file()
"""
    _run([sys.executable, "-I", "-c", probe], cwd=installed)
    for prefix in RETIRED_RESOURCE_PREFIXES:
        assert not (installed / prefix).exists()
    for member in expected_harness_resources:
        assert (installed / member).read_bytes() == (REPO_ROOT / member).read_bytes()
    for member in expected_scene_gen_resources:
        assert (installed / member).read_bytes() == (REPO_ROOT / member).read_bytes()
    assert not (installed / QUALIFIED_REPLAY_CLI).exists()


def test_packaging_declares_retained_resources_only() -> None:
    configuration = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())

    package_data = configuration["tool"]["setuptools"]["package-data"]
    assert "AGENTS.md" in package_data["scene_gen"]
    assert "envs/AGENTS.md" in package_data["scene_gen"]
    assert "IMPLEMENTATION_LOG.md" in package_data["self_improving.harness"]
    assert "asset_pipeline/active/asset_reuse/lib/README.md" in package_data["self_improving"]
    assert "qualified_skills/**/*.json" not in package_data["self_improving.harness"]
    assert "json_schemas/*.json" not in package_data["self_improving.harness"]
    assert "json_schemas/*.json" in package_data["self_improving.harness.x2env"]
    assert "native/*.c" not in package_data["self_improving.harness"]
    assert not RETIRED_CONSOLES & configuration["project"]["scripts"].keys()
