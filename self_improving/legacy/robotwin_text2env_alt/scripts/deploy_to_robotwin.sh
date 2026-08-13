#!/usr/bin/env bash
set -euo pipefail

ROBOTWIN_ROOT="${1:-${HOME}/RoboTwin}"
TASK_NAME="${2:-move_object_between_zones}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TASK_ROOT="${REPO_ROOT}/generated/robotwin_tasks/${TASK_NAME}"

if [[ ! -d "${ROBOTWIN_ROOT}" ]]; then
  echo "ERROR: RoboTwin root not found: ${ROBOTWIN_ROOT}" >&2
  exit 1
fi

if [[ ! -f "${TASK_ROOT}/envs/${TASK_NAME}.py" ]]; then
  echo "ERROR: generated task file not found: ${TASK_ROOT}/envs/${TASK_NAME}.py" >&2
  exit 1
fi

if [[ ! -f "${TASK_ROOT}/description/task_instruction/${TASK_NAME}.json" ]]; then
  echo "ERROR: generated instruction file not found: ${TASK_ROOT}/description/task_instruction/${TASK_NAME}.json" >&2
  exit 1
fi

install -D "${TASK_ROOT}/envs/${TASK_NAME}.py" "${ROBOTWIN_ROOT}/envs/${TASK_NAME}.py"
install -D "${TASK_ROOT}/description/task_instruction/${TASK_NAME}.json" \
  "${ROBOTWIN_ROOT}/description/task_instruction/${TASK_NAME}.json"

echo "Deployed ${TASK_NAME} to ${ROBOTWIN_ROOT}"
