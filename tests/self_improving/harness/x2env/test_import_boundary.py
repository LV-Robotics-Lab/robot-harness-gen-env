"""Fresh-process import boundary; never substitute sys.modules or legacy adapters."""

import os
import site
import subprocess
import sys
from pathlib import Path


def test_superseded_workflows_are_not_importable_in_fresh_process(tmp_path):
    root = Path(__file__).resolve().parents[4]
    program = """
from importlib.util import find_spec
for name in (
    'self_improving.harness.system2.planner',
    'self_improving.harness.system2.dispatcher',
    'self_improving.harness.system2.context',
    'self_improving.harness.system2.domain',
    'self_improving.harness.system2.history',
    'self_improving.harness.system2.qwen_local',
    'self_improving.harness.golden_run',
    'self_improving.system2_compile_turn',
    'self_improving.system2_replay_handoff',
    'self_improving.system2_replay_input_promotion',
    'self_improving.system2_replay_evidence_acquisition',
    'self_improving.system2_replay_snapshot_assessment',
    'self_improving.system2_replay_snapshot_validation',
    'self_improving.harness.qwen_replay_vlm_provider',
    'self_improving.harness.replay_vlm_worker',
    'self_improving.harness.replay_vlm_assessment',
    'demo.harness_replay_assessment',
):
    try:
        spec = find_spec(name)
    except ModuleNotFoundError:
        spec = None
    assert spec is None, name
from self_improving.harness.x2env.cli import main
from self_improving.harness import runtime_assets, runtime_capability, runtime_events
from demo.app import create_app
assert callable(main) and callable(create_app)
"""
    result = subprocess.run(
        [sys.executable, "-S", "-c", program],
        cwd=tmp_path,
        # Load real dependencies without unrelated editable-install .pth finders.
        env={**os.environ, "PYTHONPATH": os.pathsep.join([str(root), *site.getsitepackages()])},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_canonical_import_and_cli_do_not_initialize_legacy_graph(tmp_path):
    root = Path(__file__).resolve().parents[4]
    program = """
import sys
import self_improving.harness.x2env
from self_improving.harness.x2env.cli import main
for name in (
    'self_improving.harness.application',
    'self_improving.harness.qualification',
    'self_improving.harness.golden_run',
    'self_improving.harness.registry',
    'self_improving.harness.system2.planner',
    'self_improving.harness.qwen_replay_vlm_provider',
):
    assert name not in sys.modules, name
assert callable(main)
assert main(['status', '--workflow-id', 'missing']) == 1
"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert '"error_code": "invalid_request"' in result.stdout
