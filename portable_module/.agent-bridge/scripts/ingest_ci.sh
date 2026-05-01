#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/_common.sh"

if [[ $# -ne 1 ]]; then
  echo "Usage: bash .agent-bridge/scripts/ingest_ci.sh path/to/ci_failure_digest.md" >&2
  exit 2
fi

SOURCE_FILE="$1"
if [[ ! -f "${SOURCE_FILE}" ]]; then
  echo "CI failure digest file not found: ${SOURCE_FILE}" >&2
  exit 1
fi

ab_require_python3
ab_ensure_workspace

CANONICAL_REL=".agent-bridge/workspace/inbox/ci_failure_digest.md"
CANONICAL_ABS="${AB_WORKSPACE}/inbox/ci_failure_digest.md"
cp "${SOURCE_FILE}" "${CANONICAL_ABS}"

AB_COMMAND_TYPE="CI_FAILURE_FIX" \
AB_PRIORITY="80" \
AB_SOURCE="portable_ingest_ci" \
AB_PROMPT_PATH="${CANONICAL_REL}" \
AB_DIGEST_PATH="${CANONICAL_ABS}" \
AB_QUEUE_DIR="${AB_WORKSPACE}/queue" \
AB_SCRIPT_DIR="${SCRIPT_DIR}" \
python3 - <<'PY'
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.environ["AB_SCRIPT_DIR"])
from queue_lock import command_prompt_path, portable_queue_lock, read_jsonl, write_jsonl

queue_dir = Path(os.environ["AB_QUEUE_DIR"])
queue_dir.mkdir(parents=True, exist_ok=True)
pending_path = queue_dir / "pending_commands.jsonl"
digest_path = Path(os.environ["AB_DIGEST_PATH"])
dedupe_key = "portable_ci:" + hashlib.sha256(digest_path.read_bytes()).hexdigest()[:24]

existing = set()
command = {
    "id": f"cmd_{uuid.uuid4().hex[:12]}",
    "type": os.environ["AB_COMMAND_TYPE"],
    "priority": int(os.environ["AB_PRIORITY"]),
    "source": os.environ["AB_SOURCE"],
    "created_at": datetime.now(timezone.utc).isoformat(),
    "task_id": None,
    "pr_number": None,
    "prompt_path": os.environ["AB_PROMPT_PATH"],
    "prompt_text": None,
    "requires_user_approval": False,
    "safety_flags": [],
    "dedupe_key": dedupe_key,
    "status": "pending",
    "metadata": {},
}
with portable_queue_lock(queue_dir):
    pending = read_jsonl(pending_path)
    for name in [
        "pending_commands.jsonl",
        "completed_commands.jsonl",
        "failed_commands.jsonl",
        "blocked_commands.jsonl",
    ]:
        existing.update(row.get("dedupe_key") for row in read_jsonl(queue_dir / name))
    in_progress_path = queue_dir / "in_progress.json"
    if in_progress_path.exists():
        existing.add(json.loads(in_progress_path.read_text(encoding="utf-8")).get("dedupe_key"))
    if dedupe_key in existing:
        print("Duplicate CI failure digest ignored.")
    else:
        pending = [
            row for row in pending
            if not (
                row.get("type") == os.environ["AB_COMMAND_TYPE"]
                and command_prompt_path(row) == os.environ["AB_PROMPT_PATH"]
            )
        ]
        pending.append(command)
        write_jsonl(pending_path, pending)
        print("CI failure command enqueued.")
print(json.dumps(command, ensure_ascii=False, indent=2))
PY

ab_log_event "portable_ci_failure_digest_ingested" "{\"prompt_path\":\"${CANONICAL_REL}\"}"
echo "Canonical CI failure digest: ${CANONICAL_REL}"
echo "No dispatch was attempted."
