"""Installed-distribution checks for harness runtime dependencies."""

from __future__ import annotations

import hashlib
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
LEDGER_PACKAGE = Path("self_improving/asset_pipeline/active/1_asset_reuse/lib")
LEDGER_MEMBERS = ("__init__.py", "README.md", "conventions.py", "ledger.py")
QUALIFICATION_PACKAGES = (
    Path("self_improving/harness/qualified_skills/text2env.compile/1.0.0"),
    Path("self_improving/harness/qualified_skills/text2env.replay/1.0.0"),
)
QUALIFICATION_MEMBERS = ("manifest.json", "qualification.json", "report.json")
MEDIA_NATIVE_PACKAGE = Path("self_improving/harness/native")
MEDIA_NATIVE_MEMBERS = ("media_sandbox.c",)
QUALIFIED_REPLAY_CLI = Path("self_improving/qualified_replay_cli.py")
QUALIFIED_REPLAY_ENTRY = (
    "robot-harness-run-qualified-replay = self_improving.qualified_replay_cli:main"
)
MEDIA_NATIVE_SHA256 = hashlib.sha256(
    (REPO_ROOT / MEDIA_NATIVE_PACKAGE / MEDIA_NATIVE_MEMBERS[0]).read_bytes()
).hexdigest()


def _copy_build_fixture(destination: Path) -> Path:
    """Copy the smallest real source tree that exercises package discovery."""

    source = destination / "source"
    source.mkdir()
    for relative_path in ("pyproject.toml", "README.md"):
        shutil.copy2(REPO_ROOT / relative_path, source / relative_path)
    for relative_path in (
        Path("self_improving/__init__.py"),
        QUALIFIED_REPLAY_CLI,
        Path("self_improving/registry.py"),
        Path("self_improving/harness/__init__.py"),
        Path("scene_gen/__init__.py"),
        Path("scene_gen/envs/__init__.py"),
        *(HARNESS_PACKAGE / name for name in HARNESS_RESOURCE_MEMBERS),
        *(SCENE_GEN_PACKAGE / name for name in SCENE_GEN_RESOURCE_MEMBERS),
        *(LEDGER_PACKAGE / name for name in LEDGER_MEMBERS),
        *(package / name for package in QUALIFICATION_PACKAGES for name in QUALIFICATION_MEMBERS),
        *(MEDIA_NATIVE_PACKAGE / name for name in MEDIA_NATIVE_MEMBERS),
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


def test_wheel_installs_runtime_source_and_qualification_resources(tmp_path: Path) -> None:
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
    expected_qualifications = {
        (package / name).as_posix()
        for package in QUALIFICATION_PACKAGES
        for name in QUALIFICATION_MEMBERS
    }
    expected_media_native = {
        (MEDIA_NATIVE_PACKAGE / name).as_posix() for name in MEDIA_NATIVE_MEMBERS
    }
    with zipfile.ZipFile(wheel) as archive:
        members = set(archive.namelist())
        assert expected_members <= members
        assert expected_harness_resources <= members
        assert expected_scene_gen_resources <= members
        assert expected_qualifications <= members
        assert expected_media_native <= members
        assert (
            archive.read(QUALIFIED_REPLAY_CLI.as_posix())
            == (REPO_ROOT / QUALIFIED_REPLAY_CLI).read_bytes()
        )
        entry_points_member = next(
            name for name in members if name.endswith(".dist-info/entry_points.txt")
        )
        assert (
            QUALIFIED_REPLAY_ENTRY in archive.read(entry_points_member).decode("utf-8").splitlines()
        )
        for member in expected_qualifications:
            assert archive.read(member) == (REPO_ROOT / member).read_bytes()
        for member in expected_harness_resources:
            assert archive.read(member) == (REPO_ROOT / member).read_bytes()
        for member in expected_scene_gen_resources:
            assert archive.read(member) == (REPO_ROOT / member).read_bytes()
        for member in expected_media_native:
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
    package_name = "self_improving.asset_pipeline.active.1_asset_reuse.lib"
    probe = f"""
import importlib
import importlib.resources
import hashlib
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
distribution_resources = importlib.resources.files("self_improving")
native_source = distribution_resources.joinpath("harness", "native", "media_sandbox.c")
assert native_source.is_file()
assert hashlib.sha256(native_source.read_bytes()).hexdigest() == {MEDIA_NATIVE_SHA256!r}
"""
    _run([sys.executable, "-I", "-c", probe], cwd=installed)
    for member in expected_qualifications:
        assert (installed / member).read_bytes() == (REPO_ROOT / member).read_bytes()
    for member in expected_harness_resources:
        assert (installed / member).read_bytes() == (REPO_ROOT / member).read_bytes()
    for member in expected_scene_gen_resources:
        assert (installed / member).read_bytes() == (REPO_ROOT / member).read_bytes()
    for member in expected_media_native:
        assert (installed / member).read_bytes() == (REPO_ROOT / member).read_bytes()
    assert (installed / QUALIFIED_REPLAY_CLI).read_bytes() == (
        REPO_ROOT / QUALIFIED_REPLAY_CLI
    ).read_bytes()


def test_packaging_declares_qualified_skill_resources() -> None:
    configuration = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())

    package_data = configuration["tool"]["setuptools"]["package-data"]
    assert "AGENTS.md" in package_data["scene_gen"]
    assert "envs/AGENTS.md" in package_data["scene_gen"]
    assert "IMPLEMENTATION_LOG.md" in package_data["self_improving.harness"]
    assert "asset_pipeline/active/1_asset_reuse/lib/README.md" in package_data["self_improving"]
    assert "qualified_skills/**/*.json" in package_data["self_improving.harness"]
    assert "native/*.c" in package_data["self_improving.harness"]
    assert (
        configuration["project"]["scripts"]["robot-harness-run-qualified-replay"]
        == "self_improving.qualified_replay_cli:main"
    )
