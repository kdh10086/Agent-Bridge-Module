from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_bridge.codex.prompt_builder import PromptBuilder
from agent_bridge.core.event_log import EventLog
from agent_bridge.core.models import BridgeStateName
from agent_bridge.core.safety_gate import SafetyGate
from agent_bridge.core.state_store import StateStore
from agent_bridge.gui.clipboard import Clipboard
from agent_bridge.gui.manual_confirmation import ManualConfirmation


PM_PROMPT_PATH = "pm_assistant_prompt.md"


@dataclass(frozen=True)
class StageResult:
    prompt_path: Path
    prompt: str
    staged: bool
    copied: bool = False
    blocked: bool = False
    reason: str | None = None


def _block_for_safety(
    *,
    workspace_dir: Path,
    prompt_path: Path,
    prompt: str,
    event_log: EventLog,
    event_type: str,
) -> StageResult:
    decision = SafetyGate().check_text(prompt)
    SafetyGate().write_decision_request(workspace_dir, decision, prompt)
    state_store = StateStore(workspace_dir / "state" / "state.json")
    state = state_store.load()
    state.safety_pause = True
    state.state = BridgeStateName.PAUSED_FOR_USER_DECISION
    state_store.save(state)
    prompt_path.unlink(missing_ok=True)
    event_log.append(
        event_type,
        blocked=True,
        matched_keywords=decision.matched_keywords,
        prompt_path=str(prompt_path),
    )
    return StageResult(
        prompt_path=prompt_path,
        prompt=prompt,
        staged=False,
        blocked=True,
        reason=decision.reason,
    )


def stage_pm_prompt(
    *,
    workspace_dir: Path,
    template_dir: Path,
    dry_run: bool,
    copy_to_clipboard: bool = False,
    clipboard: Clipboard | None = None,
    confirmation: ManualConfirmation | None = None,
    event_log: EventLog | None = None,
) -> StageResult:
    report_path = workspace_dir / "reports" / "latest_agent_report.md"
    outbox_dir = workspace_dir / "outbox"
    prompt_path = outbox_dir / PM_PROMPT_PATH
    log = event_log or EventLog(workspace_dir / "logs" / "bridge.jsonl")

    report = report_path.read_text(encoding="utf-8")
    prompt = PromptBuilder(template_dir).build_pm_report_prompt(report)
    decision = SafetyGate().check_text(prompt)
    if not decision.allowed:
        return _block_for_safety(
            workspace_dir=workspace_dir,
            prompt_path=prompt_path,
            prompt=prompt,
            event_log=log,
            event_type="stage_pm_prompt_blocked",
        )

    outbox_dir.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt, encoding="utf-8")
    copied = False
    if copy_to_clipboard and not dry_run:
        confirmer = confirmation or ManualConfirmation()
        if confirmer.confirm("Copy staged PM assistant prompt to clipboard?"):
            if clipboard is None:
                raise ValueError("clipboard is required when copy is confirmed.")
            clipboard.copy_text(prompt)
            copied = True
        else:
            log.append("stage_pm_prompt_copy_declined", prompt_path=str(prompt_path))

    log.append(
        "stage_pm_prompt",
        prompt_path=str(prompt_path),
        dry_run=dry_run,
        copy_to_clipboard=copy_to_clipboard,
        copied=copied,
    )
    return StageResult(prompt_path=prompt_path, prompt=prompt, staged=True, copied=copied)
