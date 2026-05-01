#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -n "${CODEX_SANDBOX:-}" ]]; then
  echo "Refusing to run GUI automation from the restricted Codex sandbox."
  echo "Detected hard-block marker: CODEX_SANDBOX"
  echo "Open a normal macOS Terminal, cd to this repository, and run:"
  echo "  bash scripts/run_gui_roundtrip_external.sh"
  exit 1
fi

CONTEXT_MARKERS=()
for key in CODEX_SHELL CODEX_THREAD_ID; do
  if [[ -n "${!key:-}" ]]; then
    CONTEXT_MARKERS+=("$key")
  fi
done

if (( ${#CONTEXT_MARKERS[@]} > 0 )); then
  echo "Warning: Full Access Codex context markers detected: ${CONTEXT_MARKERS[*]}"
  echo "CODEX_SANDBOX is not set; continuing only after preflights pass."
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

PM_VISUAL_PROFILE="$("$PYTHON_BIN" - <<'PY' 2>/dev/null || true
from agent_bridge.gui.macos_apps import load_gui_targets
target = load_gui_targets(__import__("pathlib").Path("config")).pm_assistant
print((target.backend or "") + "|" + (target.visual_asset_profile or ""))
PY
)"

if [[ "$PM_VISUAL_PROFILE" == *"chatgpt_mac_visual"* || "$PM_VISUAL_PROFILE" == *"|chatgpt_mac"* ]]; then
  echo "Running ChatGPT Mac visual activation preflight..."
  "$PYTHON_BIN" -m agent_bridge.cli preflight-gui-apps --pm-app "ChatGPT" --activate

  echo "Running ChatGPT Mac visual state preflight..."
  VISUAL_STATE_OUTPUT="$("$PYTHON_BIN" -m agent_bridge.cli diagnose-visual-state --app chatgpt_mac)"
  echo "$VISUAL_STATE_OUTPUT"
  if [[ "$VISUAL_STATE_OUTPUT" == *"Matched state: AMBIGUOUS"* ]]; then
    echo "ChatGPT Mac visual state is ambiguous; refusing full roundtrip."
    exit 1
  fi

  echo "Running ChatGPT Mac response-capture preflight..."
  RESPONSE_CAPTURE_OUTPUT="$("$PYTHON_BIN" -m agent_bridge.cli diagnose-chatgpt-mac-response-capture)"
  echo "$RESPONSE_CAPTURE_OUTPUT"
  if [[ "$RESPONSE_CAPTURE_OUTPUT" != *"Response capture supported: yes"* ]]; then
    echo "ChatGPT Mac response capture is unsupported; refusing full roundtrip."
    exit 1
  fi
  if [[ "$RESPONSE_CAPTURE_OUTPUT" != *"Copy button found: yes"* ]]; then
    echo "ChatGPT Mac response copy button was not detected; refusing full roundtrip."
    exit 1
  fi
else
  echo "Running PM assistant activation preflight..."
  "$PYTHON_BIN" -m agent_bridge.cli preflight-gui-apps --pm-app "Google Chrome" --activate

  echo "Running PM assistant backend preflight..."
  "$PYTHON_BIN" -m agent_bridge.cli preflight-pm-backend --activate
fi

echo "Running local-agent activation preflight..."
"$PYTHON_BIN" -m agent_bridge.cli preflight-gui-apps --local-agent-app "Codex" --activate

echo "Activation preflights passed. Starting bounded report roundtrip..."
"$PYTHON_BIN" -m agent_bridge.cli dogfood-report-roundtrip \
  --auto-confirm \
  --max-cycles 1 \
  --max-runtime-seconds 180
