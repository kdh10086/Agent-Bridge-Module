#!/usr/bin/env bash

AB_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB_BRIDGE_DIR="$(cd "${AB_SCRIPT_DIR}/.." && pwd)"
AB_PROJECT_ROOT="$(cd "${AB_BRIDGE_DIR}/.." && pwd)"
AB_WORKSPACE="${AB_BRIDGE_DIR}/workspace"
AB_QUEUE_FILE="${AB_WORKSPACE}/queue/pending_commands.jsonl"
AB_LOG_FILE="${AB_WORKSPACE}/logs/bridge.jsonl"

AB_HARD_STOP_KEYWORDS=(
  "NEEDS_USER_DECISION"
  "APPROVAL_REQUIRED"
  "RISK_HIGH"
  "PAID_API"
  "LICENSE_UNKNOWN"
  "PRIVACY_RISK"
  "MAIN_MERGE"
  "DATA_MIGRATION"
  "ARCHITECTURE_CHANGE"
  "DELETE_OR_REWRITE_LARGE_SCOPE"
  "CI_FAILED_REPEATEDLY"
  "MAX_CYCLE_REACHED"
)

ab_require_python3() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "Agent Bridge portable scripts require python3 for JSONL queue handling." >&2
    exit 1
  fi
}

ab_ensure_workspace() {
  mkdir -p \
    "${AB_WORKSPACE}/state" \
    "${AB_WORKSPACE}/queue" \
    "${AB_WORKSPACE}/inbox" \
    "${AB_WORKSPACE}/outbox" \
    "${AB_WORKSPACE}/reports" \
    "${AB_WORKSPACE}/reviews" \
    "${AB_WORKSPACE}/logs"
}

ab_log_event() {
  local event_type="$1"
  local metadata_json="${2:-{}}"
  ab_require_python3
  AB_LOG_EVENT="${event_type}" AB_LOG_METADATA="${metadata_json}" AB_LOG_FILE="${AB_LOG_FILE}" python3 - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

path = Path(os.environ["AB_LOG_FILE"])
path.parent.mkdir(parents=True, exist_ok=True)
try:
    metadata = json.loads(os.environ.get("AB_LOG_METADATA", "{}"))
except json.JSONDecodeError:
    metadata = {"raw": os.environ.get("AB_LOG_METADATA", "")}
record = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "event_type": os.environ["AB_LOG_EVENT"],
    "metadata": metadata,
}
with path.open("a", encoding="utf-8") as f:
    f.write(json.dumps(record, ensure_ascii=False) + "\n")
PY
}

ab_payload_actual_path() {
  local payload_path="$1"
  if [[ "${payload_path}" = /* ]]; then
    printf '%s\n' "${payload_path}"
  else
    printf '%s/%s\n' "${AB_PROJECT_ROOT}" "${payload_path}"
  fi
}

