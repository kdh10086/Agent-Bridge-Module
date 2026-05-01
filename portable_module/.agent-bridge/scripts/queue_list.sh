#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/_common.sh"

ab_require_python3
ab_ensure_workspace

AB_QUEUE_FILE="${AB_QUEUE_FILE}" \
AB_QUEUE_DIR="${AB_WORKSPACE}/queue" \
AB_SCRIPT_DIR="${SCRIPT_DIR}" \
python3 - <<'PY'
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ["AB_SCRIPT_DIR"])
from queue_lock import command_prompt_path, portable_queue_lock, read_jsonl

path = Path(os.environ["AB_QUEUE_FILE"])
queue_dir = Path(os.environ["AB_QUEUE_DIR"])
with portable_queue_lock(queue_dir):
    commands = read_jsonl(path)

if not commands:
    print("No pending commands.")
    raise SystemExit(0)

commands.sort(key=lambda c: (-(c.get("priority") or 0), c.get("created_at") or ""))
print("priority  type                 source                  prompt_path")
print("--------  -------------------  ----------------------  ----------------")
for command in commands:
    print(
        f"{command.get('priority', '')!s:<8}  "
        f"{command.get('type', ''):<19}  "
        f"{command.get('source', ''):<22}  "
        f"{command_prompt_path(command)}"
    )
PY
