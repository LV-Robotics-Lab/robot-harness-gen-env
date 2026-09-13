"""Retired qualified execution graph stays absent; stable lower-level modules stay usable."""

import os
import site
import subprocess
import sys
from pathlib import Path


def test_old_qualified_execution_modules_are_absent(tmp_path):
    root = Path(__file__).resolve().parents[3]
    modules = (
        "self_improving.harness.application",
        "self_improving.harness.compile_cli",
        "self_improving.harness.replay_application",
        "self_improving.harness.qualify_compile",
        "self_improving.harness.qualify_replay",
        "self_improving.harness.replay_qualification",
        "self_improving.harness.run_receipts",
        "self_improving.harness.registry",
        "self_improving.harness.replay_dependencies",
        "self_improving.qualified_replay_cli",
        "self_improving.harness.handlers.text2env_compile",
        "self_improving.harness.handlers.text2env_compile_dependencies",
        "self_improving.harness.handlers.text2env_replay",
        "self_improving.harness.handlers.text2env_validate",
    )
    program = f"""
from importlib.util import find_spec
for name in {modules!r}:
    assert find_spec(name) is None, name
from self_improving.harness import runtime_assets, runtime_capability, runtime_events
from self_improving.harness.schemas import ArtifactRef, SkillDescriptor
from self_improving.harness.x2env.cli import main
from demo.app import create_app
assert callable(main) and callable(create_app)
"""
    result = subprocess.run(
        [sys.executable, "-S", "-c", program],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": os.pathsep.join((str(root), *site.getsitepackages()))},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
