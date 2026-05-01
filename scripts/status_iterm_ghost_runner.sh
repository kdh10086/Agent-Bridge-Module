#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PID_FILE="workspace/state/iterm_ghost_runner.pid"
LOCK_FILE="workspace/state/ghost_runner.lock"
HASH_FILE="workspace/state/last_processed_report_hash"
LOG_FILE="workspace/logs/external_gui_runner.log"

if [[ -f "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE")"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "iTerm ghost runner: running (PID $pid)"
  else
    echo "iTerm ghost runner: PID file exists, but process is not running"
  fi
else
  echo "iTerm ghost runner: no PID file"
fi

if [[ -f "$LOCK_FILE" ]]; then
  echo "Lock file: $LOCK_FILE"
else
  echo "Lock file: none"
fi

if [[ -f "$HASH_FILE" ]]; then
  echo "Last processed report hash: $(cat "$HASH_FILE")"
else
  echo "Last processed report hash: none"
fi

if [[ -f "$LOG_FILE" ]]; then
  echo "Log: $LOG_FILE"
  tail -n 20 "$LOG_FILE"
else
  echo "Log: none"
fi
