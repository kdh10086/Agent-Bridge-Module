#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON:-python3}"
fi

echo "Starting Agent Bridge foreground runner."
echo "Stop with Ctrl-C."
echo "Default trigger: any post-startup latest_agent_report.md content change."
echo "Pass --require-trigger-marker only for marker-gated compatibility mode."
exec "$PYTHON_BIN" -m agent_bridge.cli run-bridge "$@"
