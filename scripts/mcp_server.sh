#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)

export PYTHONPATH="$REPO_ROOT"
export ANAMNESIS_DATA_DIR="${ANAMNESIS_DATA_DIR:-$REPO_ROOT/.anamnesis-data}"
export ANAMNESIS_PROJECT_PREFIXES="${ANAMNESIS_PROJECT_PREFIXES:-}"
export ANAMNESIS_CC_ROOT="${ANAMNESIS_CC_ROOT:-$HOME/.claude/projects}"
export ANAMNESIS_CODEX_ROOT="${ANAMNESIS_CODEX_ROOT:-$HOME/.codex/sessions}"

exec "$REPO_ROOT/.venv/bin/python" -m anamnesis.daemon.mcp_server "$@"
