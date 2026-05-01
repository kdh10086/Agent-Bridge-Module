#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PID_FILE="workspace/state/external_gui_runner.pid"
LOG_FILE="workspace/logs/external_gui_runner.log"
mkdir -p "workspace/state" "workspace/logs"

if [[ -f "$PID_FILE" ]]; then
  existing_pid="$(cat "$PID_FILE")"
  if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
    echo "External GUI runner is already running with PID $existing_pid."
    exit 0
  fi
fi

if [[ -n "${PYTHON:-}" ]]; then
  PYTHON_BIN="$PYTHON"
elif [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi

nohup "$PYTHON_BIN" -m agent_bridge.cli run-external-gui-runner \
  --auto-confirm \
  --watch-reports \
  --watch-queue \
  --polling-interval-seconds 3 \
  --max-runtime-seconds 3600 \
  >> "$LOG_FILE" 2>&1 &

echo "$!" > "$PID_FILE"
echo "External GUI runner started with PID $(cat "$PID_FILE")."
echo "Log: $LOG_FILE"
