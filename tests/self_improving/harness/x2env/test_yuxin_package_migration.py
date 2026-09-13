"""Public Python import seam; no provider searches or model/runtime initialization."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def test_yuxin_engine_imports_without_legacy_lib_alias(tmp_path):
    upstream = ROOT / "self_improving/asset_pipeline/active/shared/openxsim/source/agenticsim"
    command = """
import importlib, sys
prefix = 'self_improving.asset_pipeline.active.asset_reuse.lib.'
for name in ('a1_providers', 'a2_selection', 'a3_webfetch', 'a4_coverage',
             'a5_visual', 'a6_verify', 'a7_objaverse', 'ledger', 'ledger_writes'):
    importlib.import_module(prefix + name)
assert 'lib' not in sys.modules
assert 'torch' not in sys.modules
assert 'sapien' not in sys.modules
from self_improving.asset_pipeline.active.asset_reuse.lib.a1_providers import load_providers
tiers, config = load_providers({'providers': {}, 'globals': {'license_gate': True}})
assert tiers == [] and config['license_gate'] is True
"""
    result = subprocess.run(
        [sys.executable, "-c", command],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT), str(upstream)])},
    )
    assert result.returncode == 0, result.stderr
    assert not (ROOT / "self_improving/asset_pipeline/active/1_asset_reuse").exists()
