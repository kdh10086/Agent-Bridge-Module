#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/_common.sh"

if [[ $# -lt 1 ]]; then
  echo "Usage: bash .agent-bridge/scripts/write_report.sh \"short summary\"" >&2
  exit 2
fi

ab_ensure_workspace

SUMMARY="$*"
REPORT_PATH="${AB_WORKSPACE}/reports/latest_agent_report.md"
CREATED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

{
  echo "# Agent Report"
  echo
  echo "## Summary"
  echo
  echo "${SUMMARY}"
  echo
  echo "## Updated At"
  echo
  echo "${CREATED_AT}"
  echo
  echo "## Results"
  echo
  echo "Report written by Agent Bridge portable write_report.sh."
} > "${REPORT_PATH}"

ab_log_event "portable_report_written" "{\"report_path\":\".agent-bridge/workspace/reports/latest_agent_report.md\"}"
echo "Report written: .agent-bridge/workspace/reports/latest_agent_report.md"

