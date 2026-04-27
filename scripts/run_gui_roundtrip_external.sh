#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SANDBOX_MARKERS=()
for key in CODEX_SANDBOX CODEX_SHELL CODEX_THREAD_ID; do
  if [[ -n "${!key:-}" ]]; then
    SANDBOX_MARKERS+=("$key")
  fi
done

if (( ${#SANDBOX_MARKERS[@]} > 0 )); then
  echo "Refusing to run GUI automation from the Codex sandbox."
  echo "Detected environment markers: ${SANDBOX_MARKERS[*]}"
  echo "Open a normal macOS Terminal, cd to this repository, and run:"
  echo "  bash scripts/run_gui_roundtrip_external.sh"
  exit 1
fi

if [[ -n "${PYTHON:-}" ]]; then
  PYTHON_BIN="$PYTHON"
elif [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
else
  PYTHON_BIN="python3"
fi

echo "Running external GUI runner preflight from normal Terminal context..."
"$PYTHON_BIN" -m agent_bridge.cli preflight-external-runner

echo "Running PM assistant activation preflight..."
"$PYTHON_BIN" -m agent_bridge.cli preflight-gui-apps --pm-app "ChatGPT" --activate

echo "Running local-agent activation preflight..."
"$PYTHON_BIN" -m agent_bridge.cli preflight-gui-apps --local-agent-app "Codex" --activate

echo "Activation preflights passed. Starting bounded report roundtrip..."
"$PYTHON_BIN" -m agent_bridge.cli dogfood-report-roundtrip \
  --auto-confirm \
  --max-cycles 1 \
  --max-runtime-seconds 180
