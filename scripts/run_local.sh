#!/usr/bin/env bash
set -euo pipefail

# One-command local Offline Gemma 4 Desktop launcher for RollMetrics.
# Zero cloud API keys required.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

PORT="${ROLLMETRICS_PORT:-8090}"
FOLDER="${1:-}"

export ROLLMETRICS_ENVIRONMENT="local"
export ROLLMETRICS_PROFILE="local"
export ROLLMETRICS_ALLOW_LOCAL_DEV_BYPASS="true"

if [[ -x "${REPO_ROOT}/.venv/bin/rollmetrics" ]]; then
  BIN="${REPO_ROOT}/.venv/bin/rollmetrics"
else
  BIN="rollmetrics"
fi

if [[ -n "${FOLDER}" ]]; then
  exec "${BIN}" desktop --folder "${FOLDER}" --port "${PORT}"
else
  exec "${BIN}" desktop --port "${PORT}"
fi
