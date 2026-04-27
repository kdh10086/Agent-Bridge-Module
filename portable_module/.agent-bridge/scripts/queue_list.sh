#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/_common.sh"

ab_require_python3
ab_ensure_workspace

AB_QUEUE_FILE="${AB_QUEUE_FILE}" python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["AB_QUEUE_FILE"])
commands = []
if path.exists():
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            commands.append(json.loads(line))

if not commands:
    print("No pending commands.")
    raise SystemExit(0)

commands.sort(key=lambda c: (-(c.get("priority") or 0), c.get("created_at") or ""))
print("priority  type                 source                  payload_path")
print("--------  -------------------  ----------------------  ----------------")
for command in commands:
    print(
        f"{command.get('priority', '')!s:<8}  "
        f"{command.get('type', ''):<19}  "
        f"{command.get('source', ''):<22}  "
        f"{command.get('payload_path', '')}"
    )
PY

