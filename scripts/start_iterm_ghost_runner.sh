#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PID_FILE="workspace/state/iterm_ghost_runner.pid"
LOG_FILE="workspace/logs/external_gui_runner.log"
REPORT_PATH="workspace/reports/latest_agent_report.md"
mkdir -p "workspace/state" "workspace/logs"

if [[ -n "${CODEX_SANDBOX:-}" ]]; then
  echo "Refusing to start iTerm ghost runner: CODEX_SANDBOX is set."
  exit 1
fi

if [[ -n "${PYTHON:-}" ]]; then
  PYTHON_BIN="$PYTHON"
elif [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi

echo "Agent Bridge iTerm ghost runner"
echo "Repo: $ROOT_DIR"
echo "Watched report: $ROOT_DIR/$REPORT_PATH"
echo "Max runtime: ${MAX_RUNTIME_SECONDS:-3600}s"
echo "Max roundtrips: ${MAX_ROUNDTRIPS:-1}"
echo "Stop foreground mode with Ctrl-C."
echo ""

if [[ "${1:-}" == "--background" ]]; then
  if [[ -f "$PID_FILE" ]]; then
    existing_pid="$(cat "$PID_FILE")"
    if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
      echo "iTerm ghost runner is already running with PID $existing_pid."
      exit 0
    fi
  fi
  nohup "$PYTHON_BIN" -m agent_bridge.cli run-iterm-ghost-runner \
    --auto-confirm \
    --watch-report \
    --polling-interval-seconds "${POLLING_INTERVAL_SECONDS:-3}" \
    --max-runtime-seconds "${MAX_RUNTIME_SECONDS:-3600}" \
    --max-roundtrips "${MAX_ROUNDTRIPS:-1}" \
    >> "$LOG_FILE" 2>&1 &
  echo "$!" > "$PID_FILE"
  echo "iTerm ghost runner started with PID $(cat "$PID_FILE")."
  echo "Log: $LOG_FILE"
  exit 0
fi

exec "$PYTHON_BIN" -m agent_bridge.cli run-iterm-ghost-runner \
  --auto-confirm \
  --watch-report \
  --polling-interval-seconds "${POLLING_INTERVAL_SECONDS:-3}" \
  --max-runtime-seconds "${MAX_RUNTIME_SECONDS:-3600}" \
  --max-roundtrips "${MAX_ROUNDTRIPS:-1}"
