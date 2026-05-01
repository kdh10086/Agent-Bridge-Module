#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PID_FILE="workspace/state/external_gui_runner.pid"
LOG_FILE="workspace/logs/external_gui_runner.log"
LOCK_FILE="workspace/state/external_runner.lock"

if [[ -f "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE")"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "External GUI runner: running (PID $pid)"
  else
    echo "External GUI runner: PID file exists, but process is not running"
  fi
else
  echo "External GUI runner: no PID file"
fi

if [[ -f "$LOCK_FILE" ]]; then
  echo "Lock file: $LOCK_FILE"
else
  echo "Lock file: none"
fi

if [[ -f "$LOG_FILE" ]]; then
  echo "Log: $LOG_FILE"
  tail -n 20 "$LOG_FILE"
else
  echo "Log: none"
fi
