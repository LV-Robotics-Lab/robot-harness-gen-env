#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
PYTHON_BIN="${PYTHON_BIN:-python}"

cd "$REPO_ROOT"
if [ "$#" -eq 0 ]; then
  set -- all
fi
exec "$PYTHON_BIN" script/x2env_test_groups.py "$@"
