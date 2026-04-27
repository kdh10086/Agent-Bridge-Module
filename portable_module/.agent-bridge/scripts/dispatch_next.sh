#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/_common.sh"

if [[ $# -ne 1 || "$1" != "--dry-run" ]]; then
  echo "Portable real GUI dispatch is not implemented in the MVP." >&2
  echo "Usage: bash .agent-bridge/scripts/dispatch_next.sh --dry-run" >&2
  exit 2
fi

ab_require_python3
ab_ensure_workspace

AB_PROJECT_ROOT="${AB_PROJECT_ROOT}" \
AB_WORKSPACE="${AB_WORKSPACE}" \
AB_QUEUE_FILE="${AB_QUEUE_FILE}" \
AB_LOG_FILE="${AB_LOG_FILE}" \
python3 - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

project_root = Path(os.environ["AB_PROJECT_ROOT"])
workspace = Path(os.environ["AB_WORKSPACE"])
queue_file = Path(os.environ["AB_QUEUE_FILE"])
log_file = Path(os.environ["AB_LOG_FILE"])
prompt_path = workspace / "outbox" / "next_local_agent_prompt.md"
template_path = workspace.parent / "templates" / "local_agent_command_wrapper.md"
hard_stops = [
    "NEEDS_USER_DECISION",
    "APPROVAL_REQUIRED",
    "RISK_HIGH",
    "PAID_API",
    "LICENSE_UNKNOWN",
    "PRIVACY_RISK",
    "MAIN_MERGE",
    "DATA_MIGRATION",
    "ARCHITECTURE_CHANGE",
    "DELETE_OR_REWRITE_LARGE_SCOPE",
    "CI_FAILED_REPEATEDLY",
    "MAX_CYCLE_REACHED",
]

def log(event_type: str, **metadata: object) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "metadata": metadata,
    }
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

def read_commands() -> list[dict]:
    if not queue_file.exists():
        return []
    rows = []
    for line in queue_file.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows

def resolve_payload(command: dict) -> Path:
    payload_path = Path(command["payload_path"])
    if payload_path.is_absolute():
        return payload_path
    return project_root / payload_path

commands = read_commands()
if not commands:
    print("No pending commands.")
    log("portable_dispatch_no_pending")
    raise SystemExit(0)

for command in commands:
    payload_path = resolve_payload(command)
    payload = payload_path.read_text(encoding="utf-8") if payload_path.exists() else ""
    upper_payload = payload.upper()
    matched = [keyword for keyword in hard_stops if keyword in upper_payload]
    if command.get("requires_user_approval") and "APPROVAL_REQUIRED" not in matched:
        matched.append("APPROVAL_REQUIRED")
    if matched:
        inbox = workspace / "inbox"
        outbox = workspace / "outbox"
        state_dir = workspace / "state"
        inbox.mkdir(parents=True, exist_ok=True)
        outbox.mkdir(parents=True, exist_ok=True)
        state_dir.mkdir(parents=True, exist_ok=True)
        if prompt_path.exists():
            prompt_path.unlink()
        (inbox / "user_decision_request.md").write_text(
            "# User Decision Required\n\n"
            "## Reason\n\nHard-stop keyword detected in a pending command payload.\n\n"
            f"## Command\n\n{command.get('type')} from {command.get('source')}\n\n"
            f"## Matched Keywords\n\n{', '.join(matched)}\n\n"
            f"## Payload Path\n\n{command.get('payload_path')}\n\n",
            encoding="utf-8",
        )
        (outbox / "owner_decision_email.md").write_text(
            "Subject: [Agent Bridge Decision Required] Portable Automation Paused\n\n"
            "## Summary\n\nAgent Bridge portable dispatch paused because a safety gate was triggered.\n\n"
            f"## Matched Keywords\n\n{', '.join(matched)}\n\n"
            f"## Payload Path\n\n{command.get('payload_path')}\n",
            encoding="utf-8",
        )
        (state_dir / "state.json").write_text(
            json.dumps(
                {
                    "state": "PAUSED_FOR_USER_DECISION",
                    "safety_pause": True,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "blocked_command_id": command.get("id"),
                    "matched_keywords": matched,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print("Safety pause triggered. No local-agent prompt was produced.")
        print(f"Matched keywords: {', '.join(matched)}")
        log("portable_dispatch_blocked", command_id=command.get("id"), matched_keywords=matched)
        raise SystemExit(0)

commands.sort(key=lambda c: (-(c.get("priority") or 0), c.get("created_at") or ""))
command = commands[0]
payload_path = resolve_payload(command)
if not payload_path.exists():
    raise SystemExit(f"Payload file not found: {command.get('payload_path')}")
payload = payload_path.read_text(encoding="utf-8")

template = (
    template_path.read_text(encoding="utf-8")
    if template_path.exists()
    else "You are the local coding agent working under Agent Bridge.\n\n[Command Type]\n{command_type}\n\n[Command Source]\n{source}\n\n[Payload]\n{payload}\n"
)
prompt = template.format(
    command_type=command.get("type", ""),
    source=command.get("source", ""),
    payload=payload,
)
prompt_path.parent.mkdir(parents=True, exist_ok=True)
prompt_path.write_text(prompt, encoding="utf-8")
print(prompt)
log("portable_dispatch_dry_run_prompt_built", command_id=command.get("id"), command_type=command.get("type"))
PY

