#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
PYTHON_BIN="${PYTHON_BIN:-python}"

cd "$REPO_ROOT"
"$PYTHON_BIN" -m self_improving --json
"$PYTHON_BIN" -m pytest -q \
  --cov=self_improving.harness \
  --cov-branch \
  --cov-report=term-missing \
  --cov-fail-under=100
PYTHONPATH=.:self_improving/stage5 \
  "$PYTHON_BIN" -m pytest -q self_improving/stage5/tests
PYTHONPATH=self_improving/alchedata:self_improving/alchedata/scripts \
  "$PYTHON_BIN" -m pytest -q self_improving/alchedata/tests
PYTHONPATH=self_improving/sim_adapters/agenticsim_runtime \
  "$PYTHON_BIN" -m pytest -q self_improving/sim_adapters/agenticsim_runtime/tests

cd "$REPO_ROOT/self_improving/asset_pipeline/active/1_asset_reuse"
PYTHONPATH=.:scripts:../shared/openxsim/source/agenticsim:../../../.. \
  "$PYTHON_BIN" -m pytest -q tests

cd "$REPO_ROOT"
"$PYTHON_BIN" -m pytest -q self_improving/asset_pipeline/active/web/tests

cd "$REPO_ROOT/self_improving/asset_pipeline/active/shared/openxsim"
PYTHONPATH=source/agenticsim "$PYTHON_BIN" -m pytest -q tests
