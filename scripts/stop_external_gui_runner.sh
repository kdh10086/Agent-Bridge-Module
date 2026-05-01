#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PID_FILE="workspace/state/external_gui_runner.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "External GUI runner PID file not found."
  exit 0
fi

pid="$(cat "$PID_FILE")"
if [[ -z "$pid" ]]; then
  rm -f "$PID_FILE"
  echo "External GUI runner PID file was empty and has been removed."
  exit 0
fi

if kill -0 "$pid" 2>/dev/null; then
  kill "$pid"
  echo "Sent stop signal to external GUI runner PID $pid."
else
  echo "External GUI runner PID $pid is not running."
fi

rm -f "$PID_FILE"
