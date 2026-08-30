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
LEDGER_PACKAGE = Path("self_improving/asset_pipeline/active/1_asset_reuse/lib")
LEDGER_MEMBERS = ("__init__.py", "conventions.py", "ledger.py")


def _copy_build_fixture(destination: Path) -> Path:
    """Copy the smallest real source tree that exercises package discovery."""

    source = destination / "source"
    source.mkdir()
    for relative_path in ("pyproject.toml", "README.md"):
        shutil.copy2(REPO_ROOT / relative_path, source / relative_path)
    for relative_path in (
        Path("self_improving/__init__.py"),
        Path("self_improving/registry.py"),
        *(LEDGER_PACKAGE / name for name in LEDGER_MEMBERS),
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


def test_wheel_installs_asset_ledger_contract(tmp_path: Path) -> None:
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
    with zipfile.ZipFile(wheel) as archive:
        assert expected_members <= set(archive.namelist())

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
    package_name = "self_improving.asset_pipeline.active.1_asset_reuse.lib"
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


def test_packaging_declares_future_qualified_skill_resources() -> None:
    configuration = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())

    package_data = configuration["tool"]["setuptools"]["package-data"]
    assert "qualified_skills/**/*.json" in package_data["self_improving.harness"]
