#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIDGE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKSPACE="${BRIDGE_DIR}/workspace"

mkdir -p "${WORKSPACE}"/{state,queue,inbox,outbox,reports,reviews,logs}

REPORT="${WORKSPACE}/reports/latest_agent_report.md"
PAYLOAD="${WORKSPACE}/inbox/portable_self_test_command.md"
QUEUE_FILE="${WORKSPACE}/queue/pending_commands.jsonl"
PROMPT_OUT="${WORKSPACE}/outbox/portable_self_test_dry_run_prompt.md"
LOG_FILE="${WORKSPACE}/logs/bridge.jsonl"

cat > "${REPORT}" <<'EOF'
# Agent Report

Portable Agent Bridge self-test report.
EOF

cat > "${PAYLOAD}" <<'EOF'
# Portable Self-Test Command

Request a status report for Agent Bridge portable module readiness.
EOF

PAYLOAD_JSON="$(printf '%s' "${PAYLOAD}" | sed 's/\\/\\\\/g; s/"/\\"/g')"
CREATED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

cat > "${QUEUE_FILE}" <<EOF
{"id":"cmd_portable_self_test","type":"REQUEST_STATUS_REPORT","priority":40,"source":"portable_self_test","created_at":"${CREATED_AT}","task_id":null,"pr_number":null,"payload_path":"${PAYLOAD_JSON}","requires_user_approval":false,"safety_flags":[],"dedupe_key":"portable_self_test","status":"pending","metadata":{}}
EOF

cat > "${WORKSPACE}/state/state.json" <<'EOF'
{"state":"IDLE","safety_pause":false,"cycle":0,"max_cycles":5,"max_runtime_seconds":3600}
EOF

printf '{"timestamp":"%s","event_type":"portable_self_test_started","metadata":{}}\n' "${CREATED_AT}" >> "${LOG_FILE}"
printf '{"timestamp":"%s","event_type":"portable_self_test_command_enqueued","metadata":{"command_id":"cmd_portable_self_test"}}\n' "${CREATED_AT}" >> "${LOG_FILE}"

{
  echo "DRY RUN: local-agent prompt"
  echo
  echo "You are the local coding agent working under Agent Bridge."
  echo
  echo "Execute only the command below."
  echo
  echo "[Command Type]"
  echo "REQUEST_STATUS_REPORT"
  echo
  echo "[Command Source]"
  echo "portable_self_test"
  echo
  echo "[Payload]"
  cat "${PAYLOAD}"
} > "${PROMPT_OUT}"

cat "${PROMPT_OUT}"
echo "Portable Agent Bridge self-test completed."
echo "No project source files were modified."
