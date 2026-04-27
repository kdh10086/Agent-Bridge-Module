from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import uuid4

from agent_bridge.codex.dispatcher import Dispatcher
from agent_bridge.codex.prompt_builder import PromptBuilder
from agent_bridge.core.command_queue import CommandQueue
from agent_bridge.core.event_log import EventLog
from agent_bridge.core.models import BridgeStateName, Command, CommandType
from agent_bridge.core.safety_gate import SafetyGate
from agent_bridge.core.state_store import StateStore
from agent_bridge.gui.gui_automation import GuiAutomationAdapter
from agent_bridge.gui.macos_apps import GuiTargets


class ReportRoundtripError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReportRoundtripConfig:
    workspace_dir: Path
    template_dir: Path
    targets: GuiTargets
    auto_confirm: bool
    max_cycles: int = 1
    max_runtime_seconds: int = 180
    pm_response_timeout_seconds: int = 45


@dataclass(frozen=True)
class ReportRoundtripResult:
    completed: bool
    reason: str
    cycles_completed: int = 0
    pm_prompt_path: Path | None = None
    pm_response_path: Path | None = None
    extracted_prompt_path: Path | None = None
    local_agent_prompt_path: Path | None = None
    safety_paused: bool = False


class _RuntimeGuard:
    def __init__(self, *, max_runtime_seconds: int, monotonic_fn: Callable[[], float]):
        self.max_runtime_seconds = max_runtime_seconds
        self.monotonic_fn = monotonic_fn
        self.started_at = monotonic_fn()

    def check(self) -> None:
        if self.monotonic_fn() - self.started_at > self.max_runtime_seconds:
            raise ReportRoundtripError("Max runtime reached.")


def build_report_roundtrip_pm_prompt(report: str) -> str:
    return (
        "You are the PM assistant for Agent Bridge.\n\n"
        "Read the full latest agent report below and return the next Codex instruction.\n\n"
        "Response contract:\n"
        "- Return exactly one fenced Markdown code block.\n"
        "- The fence info string must start with CODEX_NEXT_PROMPT.\n"
        "- Do not include prose before or after the block.\n"
        "- Put only the next Codex instruction inside that block.\n"
        "- Keep the instruction generic to Agent Bridge and do not add downstream project assumptions.\n\n"
        "If the report contains AGENT_BRIDGE_GUI_ROUNDTRIP_TEST, the returned Codex prompt must be a "
        "safe no-op validation prompt that only tells Codex to confirm receipt, write a short success note "
        "to workspace/reports/latest_agent_report.md, and avoid code changes.\n\n"
        "Latest agent report:\n\n"
        f"{report}"
    )


def extract_codex_next_prompt(response: str) -> str:
    matches: list[str] = []
    lines = response.splitlines(keepends=True)
    opening_pattern = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})(?P<info>[^\r\n]*)$")
    index = 0
    while index < len(lines):
        line_without_newline = lines[index].rstrip("\r\n")
        opening_match = opening_pattern.match(line_without_newline)
        if not opening_match:
            index += 1
            continue

        info_string = opening_match.group("info").strip()
        if not info_string.startswith("CODEX_NEXT_PROMPT"):
            index += 1
            continue

        fence = opening_match.group("fence")
        fence_char = fence[0]
        closing_pattern = re.compile(
            rf"^[ \t]{{0,3}}{re.escape(fence_char)}{{{len(fence)},}}[ \t]*$"
        )
        content_lines: list[str] = []
        index += 1
        found_closing_fence = False
        while index < len(lines):
            candidate = lines[index].rstrip("\r\n")
            if closing_pattern.match(candidate):
                found_closing_fence = True
                break
            content_lines.append(lines[index])
            index += 1

        if found_closing_fence:
            matches.append("".join(content_lines))
            index += 1
            continue

        break

    if len(matches) != 1:
        raise ReportRoundtripError("Expected exactly one CODEX_NEXT_PROMPT fenced block.")
    prompt = matches[0]
    if not prompt.strip():
        raise ReportRoundtripError("CODEX_NEXT_PROMPT block was empty.")
    return prompt


def _safety_pause(
    *,
    workspace_dir: Path,
    state_store: StateStore,
    event_log: EventLog,
    source_text: str,
    phase: str,
) -> ReportRoundtripResult:
    gate = SafetyGate()
    decision = gate.check_text(source_text)
    gate.write_decision_request(workspace_dir, decision, source_text)
    state = state_store.load()
    state.safety_pause = True
    state.state = BridgeStateName.PAUSED_FOR_USER_DECISION
    state_store.save(state)
    event_log.append(
        "report_roundtrip_safety_blocked",
        phase=phase,
        matched_keywords=decision.matched_keywords,
    )
    return ReportRoundtripResult(completed=False, reason="SAFETY_BLOCKED", safety_paused=True)


def _check_safety(
    *,
    workspace_dir: Path,
    state_store: StateStore,
    event_log: EventLog,
    source_text: str,
    phase: str,
) -> ReportRoundtripResult | None:
    decision = SafetyGate().check_text(source_text)
    if decision.allowed:
        return None
    return _safety_pause(
        workspace_dir=workspace_dir,
        state_store=state_store,
        event_log=event_log,
        source_text=source_text,
        phase=phase,
    )


def run_report_roundtrip(
    *,
    config: ReportRoundtripConfig,
    gui: GuiAutomationAdapter,
    queue: CommandQueue | None = None,
    event_log: EventLog | None = None,
    state_store: StateStore | None = None,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> ReportRoundtripResult:
    if not config.auto_confirm:
        raise ReportRoundtripError("dogfood-report-roundtrip requires --auto-confirm.")
    if config.max_cycles != 1:
        raise ReportRoundtripError("dogfood-report-roundtrip is limited to exactly one cycle.")
    if config.max_runtime_seconds <= 0:
        raise ReportRoundtripError("--max-runtime-seconds must be greater than zero.")
    if config.pm_response_timeout_seconds <= 0:
        raise ReportRoundtripError("--pm-response-timeout-seconds must be greater than zero.")

    workspace_dir = config.workspace_dir
    outbox_dir = workspace_dir / "outbox"
    report_path = workspace_dir / "reports" / "latest_agent_report.md"
    pm_prompt_path = outbox_dir / "pm_assistant_prompt.md"
    pm_response_path = outbox_dir / "pm_response.md"
    extracted_prompt_path = outbox_dir / "extracted_codex_next_prompt.md"
    command_queue = queue or CommandQueue(workspace_dir / "queue")
    log = event_log or EventLog(workspace_dir / "logs" / "bridge.jsonl")
    store = state_store or StateStore(workspace_dir / "state" / "state.json")
    guard = _RuntimeGuard(
        max_runtime_seconds=config.max_runtime_seconds,
        monotonic_fn=monotonic_fn,
    )

    log.append(
        "report_roundtrip_started",
        max_cycles=config.max_cycles,
        max_runtime_seconds=config.max_runtime_seconds,
        pm_response_timeout_seconds=config.pm_response_timeout_seconds,
    )
    log.append("report_roundtrip_auto_confirm_enabled")

    try:
        guard.check()
        report = report_path.read_text(encoding="utf-8")
        pm_prompt = build_report_roundtrip_pm_prompt(report)
        blocked = _check_safety(
            workspace_dir=workspace_dir,
            state_store=store,
            event_log=log,
            source_text=pm_prompt,
            phase="pm_prompt_submit",
        )
        if blocked:
            return blocked

        outbox_dir.mkdir(parents=True, exist_ok=True)
        pm_prompt_path.write_text(pm_prompt, encoding="utf-8")
        log.append("pm_prompt_staged", prompt_path=str(pm_prompt_path), mode="report_roundtrip")

        guard.check()
        gui.activate_app(config.targets.pm_assistant)
        log.append("pm_app_activated", app_name=config.targets.pm_assistant.app_name)
        guard.check()
        gui.copy_text_to_clipboard(pm_prompt)
        gui.paste_clipboard()
        log.append("pm_prompt_pasted", prompt_path=str(pm_prompt_path))
        guard.check()
        gui.submit()
        log.append("pm_prompt_submitted", mode="report_roundtrip")

        guard.check()
        log.append("pm_response_wait_started", timeout_seconds=config.pm_response_timeout_seconds)
        gui.wait_for_response(config.pm_response_timeout_seconds)
        guard.check()
        pm_response = gui.copy_response_text()
        log.append("pm_response_copied", length=len(pm_response), mode="report_roundtrip")
        pm_response_path.write_text(pm_response, encoding="utf-8")
        log.append("pm_response_saved", response_path=str(pm_response_path))

        extracted_prompt = extract_codex_next_prompt(pm_response)
        extracted_prompt_path.write_text(extracted_prompt, encoding="utf-8")
        log.append("codex_next_prompt_extracted", prompt_path=str(extracted_prompt_path))

        command = Command(
            id=f"cmd_{uuid4().hex[:12]}",
            type=CommandType.USER_MANUAL_COMMAND,
            source="pm_assistant_report_roundtrip",
            payload_path=str(extracted_prompt_path),
            dedupe_key=f"report_roundtrip:{hashlib.sha256(extracted_prompt.encode()).hexdigest()}",
        )
        added = command_queue.enqueue(command)
        log.append("pm_command_enqueued", command_id=command.id, added=added, mode="report_roundtrip")

        guard.check()
        dispatch_result = Dispatcher(
            queue=command_queue,
            prompt_builder=PromptBuilder(config.template_dir),
            workspace_dir=workspace_dir,
            event_log=log,
        ).prepare_next_local_agent_prompt(consume=False, dry_run=False)
        if dispatch_result.blocked:
            log.append("report_roundtrip_safety_blocked", phase="local_agent_prompt_stage")
            return ReportRoundtripResult(completed=False, reason="SAFETY_BLOCKED", safety_paused=True)
        if dispatch_result.command is None:
            raise ReportRoundtripError("No local-agent command was available after enqueue.")

        log.append(
            "local_agent_prompt_staged",
            command_id=dispatch_result.command.id,
            prompt_path=str(dispatch_result.prompt_path),
            mode="report_roundtrip",
        )
        blocked = _check_safety(
            workspace_dir=workspace_dir,
            state_store=store,
            event_log=log,
            source_text=dispatch_result.prompt,
            phase="local_agent_prompt_submit",
        )
        if blocked:
            return blocked

        guard.check()
        gui.activate_app(config.targets.local_agent)
        log.append("local_agent_app_activated", app_name=config.targets.local_agent.app_name)
        guard.check()
        gui.copy_text_to_clipboard(dispatch_result.prompt)
        gui.paste_clipboard()
        log.append("local_agent_prompt_pasted", prompt_path=str(dispatch_result.prompt_path))
        guard.check()
        gui.submit()
        log.append("local_agent_prompt_submitted", command_id=dispatch_result.command.id)
        command_queue.pop_by_id(dispatch_result.command.id)

        log.append("report_roundtrip_completed", cycles_completed=1)
        return ReportRoundtripResult(
            completed=True,
            reason="ONE_CYCLE_COMPLETE",
            cycles_completed=1,
            pm_prompt_path=pm_prompt_path,
            pm_response_path=pm_response_path,
            extracted_prompt_path=extracted_prompt_path,
            local_agent_prompt_path=dispatch_result.prompt_path,
        )
    except Exception as error:
        log.append("report_roundtrip_failed", error=str(error))
        if isinstance(error, ReportRoundtripError):
            raise
        raise ReportRoundtripError(str(error)) from error
