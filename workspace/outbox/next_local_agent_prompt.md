You are the local coding agent working under Agent Bridge.

Execute only the command below.

Rules:
- Read referenced files first.
- Do not start unrelated work.
- Keep scope minimal.
- Add or update tests when applicable.
- Run relevant tests.
- Write or update `workspace/reports/latest_agent_report.md` when done.
- Stop and report if owner approval is required.

[Command Type]
CHATGPT_PM_NEXT_TASK

[Command Source]
pm_assistant

[Payload]
# PM Instruction

Implement the next smallest step: add command queue persistence and tests.

## Scope

- JSONL pending queue
- completed queue
- failed queue
- dedupe by key
- priority ordering

## Out of Scope

- GUI automation
- GitHub watcher
- email sending

