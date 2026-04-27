#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/_common.sh"

ab_require_python3
ab_ensure_workspace

rm -f \
  "${AB_WORKSPACE}/queue/pending_commands.jsonl" \
  "${AB_WORKSPACE}/queue/in_progress.json" \
  "${AB_WORKSPACE}/inbox/github_review_digest.md" \
  "${AB_WORKSPACE}/inbox/ci_failure_digest.md" \
  "${AB_WORKSPACE}/inbox/user_decision_request.md" \
  "${AB_WORKSPACE}/outbox/owner_decision_email.md" \
  "${AB_WORKSPACE}/outbox/next_local_agent_prompt.md"

bash "${SCRIPT_DIR}/write_report.sh" "Portable Agent Bridge self-test report."
bash "${SCRIPT_DIR}/ingest_review.sh" "${AB_BRIDGE_DIR}/fixtures/fake_review_digest.md" >/dev/null
bash "${SCRIPT_DIR}/ingest_ci.sh" "${AB_BRIDGE_DIR}/fixtures/fake_ci_failure_digest.md" >/dev/null

QUEUE_OUTPUT="$(bash "${SCRIPT_DIR}/queue_list.sh")"
echo "${QUEUE_OUTPUT}"

if ! grep -q "GITHUB_REVIEW_FIX" <<<"${QUEUE_OUTPUT}"; then
  echo "Self-test failed: review command missing from queue." >&2
  exit 1
fi
if ! grep -q "CI_FAILURE_FIX" <<<"${QUEUE_OUTPUT}"; then
  echo "Self-test failed: CI command missing from queue." >&2
  exit 1
fi

PROMPT_OUTPUT="$(bash "${SCRIPT_DIR}/dispatch_next.sh" --dry-run)"
echo "${PROMPT_OUTPUT}"

PROMPT_PATH="${AB_WORKSPACE}/outbox/next_local_agent_prompt.md"
if [[ ! -f "${PROMPT_PATH}" ]]; then
  echo "Self-test failed: dry-run prompt was not written." >&2
  exit 1
fi
if ! grep -q "CI_FAILURE_FIX" "${PROMPT_PATH}"; then
  echo "Self-test failed: CI command did not outrank review command." >&2
  exit 1
fi

echo "Portable Agent Bridge self-test completed."
echo "No project source files were modified."
