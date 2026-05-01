from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from agent_bridge.core.event_log import EventLog


@dataclass(frozen=True)
class ComputerUseTerminalTrigger:
    command: str
    path: Path
    content: str


def build_external_runner_command(repo_root: Path) -> str:
    quoted_root = shlex.quote(str(repo_root.resolve()))
    return (
        f"cd {quoted_root} && "
        "source .venv/bin/activate && "
        "bash scripts/run_gui_roundtrip_external.sh"
    )


def build_computer_use_terminal_trigger(repo_root: Path, workspace_dir: Path) -> ComputerUseTerminalTrigger:
    command = build_external_runner_command(repo_root)
    path = workspace_dir / "outbox" / "computer_use_terminal_trigger.md"
    content = f"""# Computer Use Terminal Trigger

Computer Use must do only this:

1. Focus an already-open normal macOS Terminal window outside the Codex sandbox.
2. Paste exactly one shell command:

```bash
{command}
```

3. Press Enter once.
4. Stop.

Computer Use must not operate ChatGPT directly.
Computer Use must not operate Codex directly.
Computer Use must not manually copy ChatGPT responses.
Computer Use must not paste into Codex manually.

Agent Bridge Python code remains responsible for:

- reading `workspace/reports/latest_agent_report.md`;
- building the PM prompt;
- pasting/submitting to ChatGPT;
- waiting for ChatGPT response completion;
- copying the full ChatGPT response;
- extracting `CODEX_NEXT_PROMPT`;
- enqueuing/staging the local-agent command;
- pasting/submitting to Codex;
- enforcing SafetyGate;
- enforcing one-cycle and max-runtime bounds;
- logging all events.

Terminal must be outside the restricted Codex sandbox.
Agent Bridge stops after one cycle.
Agent Bridge does not touch GitHub or Gmail in this flow.
"""
    return ComputerUseTerminalTrigger(command=command, path=path, content=content)


def write_computer_use_terminal_trigger(
    *,
    repo_root: Path,
    workspace_dir: Path,
    event_log: EventLog | None = None,
) -> ComputerUseTerminalTrigger:
    trigger = build_computer_use_terminal_trigger(repo_root, workspace_dir)
    trigger.path.parent.mkdir(parents=True, exist_ok=True)
    trigger.path.write_text(trigger.content, encoding="utf-8")
    if event_log:
        event_log.append(
            "computer_use_trigger_prepared",
            trigger_path=str(trigger.path),
            command=trigger.command,
        )
        event_log.append("external_terminal_trigger_expected", trigger_path=str(trigger.path))
    return trigger
