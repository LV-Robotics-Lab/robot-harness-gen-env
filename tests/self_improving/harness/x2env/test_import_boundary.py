"""Fresh-process import boundary; never substitute sys.modules or legacy adapters."""

import os
import subprocess
import sys
from pathlib import Path


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
