#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/_common.sh"

if [[ $# -ne 1 ]]; then
  echo "Usage: bash .agent-bridge/scripts/ingest_review.sh path/to/review_digest.md" >&2
  exit 2
fi

SOURCE_FILE="$1"
if [[ ! -f "${SOURCE_FILE}" ]]; then
  echo "Review digest file not found: ${SOURCE_FILE}" >&2
  exit 1
fi

ab_require_python3
ab_ensure_workspace

CANONICAL_REL=".agent-bridge/workspace/inbox/github_review_digest.md"
CANONICAL_ABS="${AB_WORKSPACE}/inbox/github_review_digest.md"
cp "${SOURCE_FILE}" "${CANONICAL_ABS}"

AB_COMMAND_TYPE="GITHUB_REVIEW_FIX" \
AB_PRIORITY="70" \
AB_SOURCE="portable_ingest_review" \
AB_PAYLOAD_PATH="${CANONICAL_REL}" \
AB_DIGEST_PATH="${CANONICAL_ABS}" \
AB_QUEUE_DIR="${AB_WORKSPACE}/queue" \
python3 - <<'PY'
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

queue_dir = Path(os.environ["AB_QUEUE_DIR"])
queue_dir.mkdir(parents=True, exist_ok=True)
pending_path = queue_dir / "pending_commands.jsonl"
digest_path = Path(os.environ["AB_DIGEST_PATH"])
dedupe_key = "portable_review:" + hashlib.sha256(digest_path.read_bytes()).hexdigest()[:24]

def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows

existing = set()
pending = read_jsonl(pending_path)
for name in ["pending_commands.jsonl", "completed_commands.jsonl", "failed_commands.jsonl"]:
    existing.update(row.get("dedupe_key") for row in read_jsonl(queue_dir / name))

command = {
    "id": f"cmd_{uuid.uuid4().hex[:12]}",
    "type": os.environ["AB_COMMAND_TYPE"],
    "priority": int(os.environ["AB_PRIORITY"]),
    "source": os.environ["AB_SOURCE"],
    "created_at": datetime.now(timezone.utc).isoformat(),
    "task_id": None,
    "pr_number": None,
    "payload_path": os.environ["AB_PAYLOAD_PATH"],
    "requires_user_approval": False,
    "safety_flags": [],
    "dedupe_key": dedupe_key,
    "status": "pending",
    "metadata": {},
}
if dedupe_key in existing:
    print("Duplicate review digest ignored.")
else:
    pending = [
        row for row in pending
        if not (
            row.get("type") == os.environ["AB_COMMAND_TYPE"]
            and row.get("payload_path") == os.environ["AB_PAYLOAD_PATH"]
        )
    ]
    pending.append(command)
    pending_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in pending),
        encoding="utf-8",
    )
    print("Review digest command enqueued.")
print(json.dumps(command, ensure_ascii=False, indent=2))
PY

ab_log_event "portable_review_digest_ingested" "{\"payload_path\":\"${CANONICAL_REL}\"}"
echo "Canonical review digest: ${CANONICAL_REL}"
echo "No dispatch was attempted."
