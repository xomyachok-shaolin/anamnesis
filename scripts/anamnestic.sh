#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)

export PYTHONPATH="$REPO_ROOT"
export ANAMNESTIC_DATA_DIR="${ANAMNESTIC_DATA_DIR:-$REPO_ROOT/.anamnestic-data}"
export ANAMNESTIC_PROJECT_PREFIXES="${ANAMNESTIC_PROJECT_PREFIXES:-}"
export ANAMNESTIC_CC_ROOT="${ANAMNESTIC_CC_ROOT:-$HOME/.claude/projects}"
export ANAMNESTIC_CODEX_ROOT="${ANAMNESTIC_CODEX_ROOT:-$HOME/.codex/sessions}"

exec "$REPO_ROOT/.venv/bin/python" -m anamnestic.cli "$@"
