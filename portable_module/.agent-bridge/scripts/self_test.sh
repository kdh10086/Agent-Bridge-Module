#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/_common.sh"

ab_require_python3
ab_ensure_workspace

AB_WORKSPACE="${AB_WORKSPACE}" AB_SCRIPT_DIR="${SCRIPT_DIR}" python3 - <<'PY'
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ["AB_SCRIPT_DIR"])
from queue_lock import portable_queue_lock

workspace = Path(os.environ["AB_WORKSPACE"])
queue_dir = workspace / "queue"
with portable_queue_lock(queue_dir):
    for path in [
        queue_dir / "pending_commands.jsonl",
        queue_dir / "in_progress.json",
        workspace / "inbox" / "github_review_digest.md",
        workspace / "inbox" / "ci_failure_digest.md",
        workspace / "inbox" / "user_decision_request.md",
        workspace / "outbox" / "owner_decision_email.md",
        workspace / "outbox" / "next_local_agent_prompt.md",
    ]:
        path.unlink(missing_ok=True)
PY

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
