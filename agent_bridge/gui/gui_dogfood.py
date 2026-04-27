from __future__ import annotations

import hashlib
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
from agent_bridge.gui.pm_assistant_bridge import stage_pm_prompt


class GuiDogfoodError(RuntimeError):
    pass


@dataclass(frozen=True)
class GuiDogfoodConfig:
    workspace_dir: Path
    template_dir: Path
    targets: GuiTargets
    auto_confirm: bool
    max_cycles: int
    max_runtime_seconds: int
    pm_response_timeout_seconds: int = 30


@dataclass(frozen=True)
class GuiDogfoodResult:
    completed: bool
    reason: str
    cycles_completed: int = 0
    pm_response_path: Path | None = None
    local_agent_prompt_path: Path | None = None
    safety_paused: bool = False


class _RuntimeGuard:
    def __init__(self, *, max_runtime_seconds: int, monotonic_fn: Callable[[], float]):
        self.max_runtime_seconds = max_runtime_seconds
        self.monotonic_fn = monotonic_fn
        self.started_at = monotonic_fn()

    def check(self) -> None:
        if self.monotonic_fn() - self.started_at > self.max_runtime_seconds:
            raise GuiDogfoodError("Max runtime reached.")


def _set_safety_pause(
    *,
    workspace_dir: Path,
    state_store: StateStore,
    event_log: EventLog,
    source_text: str,
    phase: str,
) -> GuiDogfoodResult:
    gate = SafetyGate()
    decision = gate.check_text(source_text)
    gate.write_decision_request(workspace_dir, decision, source_text)
    state = state_store.load()
    state.safety_pause = True
    state.state = BridgeStateName.PAUSED_FOR_USER_DECISION
    state_store.save(state)
    event_log.append(
        "gui_dogfood_safety_blocked",
        phase=phase,
        matched_keywords=decision.matched_keywords,
    )
    return GuiDogfoodResult(completed=False, reason="SAFETY_BLOCKED", safety_paused=True)


def _check_safety_or_pause(
    *,
    workspace_dir: Path,
    state_store: StateStore,
    event_log: EventLog,
    source_text: str,
    phase: str,
) -> GuiDogfoodResult | None:
    decision = SafetyGate().check_text(source_text)
    if decision.allowed:
        return None
    return _set_safety_pause(
        workspace_dir=workspace_dir,
        state_store=state_store,
        event_log=event_log,
        source_text=source_text,
        phase=phase,
    )


def run_gui_bridge_dogfood(
    *,
    config: GuiDogfoodConfig,
    gui: GuiAutomationAdapter,
    queue: CommandQueue | None = None,
    event_log: EventLog | None = None,
    state_store: StateStore | None = None,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> GuiDogfoodResult:
    if not config.auto_confirm:
        raise GuiDogfoodError("dogfood-gui-bridge requires --auto-confirm.")
    if config.max_cycles < 1:
        raise GuiDogfoodError("--max-cycles must be at least 1.")
    if config.max_runtime_seconds <= 0:
        raise GuiDogfoodError("--max-runtime-seconds must be greater than zero.")
    if config.pm_response_timeout_seconds <= 0:
        raise GuiDogfoodError("--pm-response-timeout-seconds must be greater than zero.")

    workspace_dir = config.workspace_dir
    command_queue = queue or CommandQueue(workspace_dir / "queue")
    log = event_log or EventLog(workspace_dir / "logs" / "bridge.jsonl")
    store = state_store or StateStore(workspace_dir / "state" / "state.json")
    guard = _RuntimeGuard(
        max_runtime_seconds=config.max_runtime_seconds,
        monotonic_fn=monotonic_fn,
    )
    cycles_completed = 0
    pm_response_path: Path | None = None
    local_prompt_path: Path | None = None

    log.append(
        "gui_dogfood_started",
        max_cycles=config.max_cycles,
        max_runtime_seconds=config.max_runtime_seconds,
        pm_response_timeout_seconds=config.pm_response_timeout_seconds,
    )
    log.append("gui_auto_confirm_enabled")

    try:
        for cycle in range(config.max_cycles):
            guard.check()
            pm_stage = stage_pm_prompt(
                workspace_dir=workspace_dir,
                template_dir=config.template_dir,
                dry_run=True,
                event_log=log,
            )
            if pm_stage.blocked:
                log.append("gui_dogfood_safety_blocked", phase="pm_prompt_stage")
                return GuiDogfoodResult(
                    completed=False,
                    reason="SAFETY_BLOCKED",
                    cycles_completed=cycles_completed,
                    safety_paused=True,
                )
            log.append("pm_prompt_staged", cycle=cycle + 1, prompt_path=str(pm_stage.prompt_path))

            blocked = _check_safety_or_pause(
                workspace_dir=workspace_dir,
                state_store=store,
                event_log=log,
                source_text=pm_stage.prompt,
                phase="pm_prompt_submit",
            )
            if blocked:
                return blocked

            guard.check()
            gui.activate_app(config.targets.pm_assistant)
            log.append("pm_app_activated", app_name=config.targets.pm_assistant.app_name)
            guard.check()
            gui.copy_text_to_clipboard(pm_stage.prompt)
            gui.paste_clipboard()
            log.append("pm_prompt_pasted", prompt_path=str(pm_stage.prompt_path))
            guard.check()
            gui.submit()
            log.append("pm_prompt_submitted")

            guard.check()
            log.append("pm_response_wait_started", timeout_seconds=config.pm_response_timeout_seconds)
            gui.wait_for_response(config.pm_response_timeout_seconds)
            guard.check()
            pm_response = gui.copy_response_text()
            log.append("pm_response_copied", length=len(pm_response))
            pm_response_path = workspace_dir / "outbox" / "pm_response.md"
            pm_response_path.parent.mkdir(parents=True, exist_ok=True)
            pm_response_path.write_text(pm_response, encoding="utf-8")
            log.append("pm_response_saved", response_path=str(pm_response_path))

            blocked = _check_safety_or_pause(
                workspace_dir=workspace_dir,
                state_store=store,
                event_log=log,
                source_text=pm_response,
                phase="pm_response_enqueue",
            )
            if blocked:
                return blocked

            command = Command(
                id=f"cmd_{uuid4().hex[:12]}",
                type=CommandType.CHATGPT_PM_NEXT_TASK,
                source="pm_assistant_gui_dogfood",
                payload_path=str(pm_response_path),
                dedupe_key=f"pm_gui_response:{hashlib.sha256(pm_response.encode()).hexdigest()}",
            )
            added = command_queue.enqueue(command)
            log.append("pm_command_enqueued", command_id=command.id, added=added)

            guard.check()
            dispatch_result = Dispatcher(
                queue=command_queue,
                prompt_builder=PromptBuilder(config.template_dir),
                workspace_dir=workspace_dir,
                event_log=log,
            ).prepare_next_local_agent_prompt(consume=False, dry_run=False)
            if dispatch_result.blocked:
                log.append("gui_dogfood_safety_blocked", phase="local_agent_prompt_stage")
                return GuiDogfoodResult(
                    completed=False,
                    reason="SAFETY_BLOCKED",
                    cycles_completed=cycles_completed,
                    safety_paused=True,
                )
            if dispatch_result.command is None:
                raise GuiDogfoodError("No local-agent command was available after PM response enqueue.")
            local_prompt_path = dispatch_result.prompt_path
            log.append(
                "local_agent_prompt_staged",
                command_id=dispatch_result.command.id,
                prompt_path=str(local_prompt_path),
            )

            blocked = _check_safety_or_pause(
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
            log.append("local_agent_prompt_pasted", prompt_path=str(local_prompt_path))
            guard.check()
            gui.submit()
            log.append("local_agent_prompt_submitted", command_id=dispatch_result.command.id)
            command_queue.pop_by_id(dispatch_result.command.id)
            cycles_completed += 1

        log.append("gui_dogfood_completed", cycles_completed=cycles_completed)
        return GuiDogfoodResult(
            completed=True,
            reason="MAX_CYCLES_REACHED",
            cycles_completed=cycles_completed,
            pm_response_path=pm_response_path,
            local_agent_prompt_path=local_prompt_path,
        )
    except Exception as error:
        log.append("gui_dogfood_failed", error=str(error), cycles_completed=cycles_completed)
        if isinstance(error, GuiDogfoodError):
            raise
        raise GuiDogfoodError(str(error)) from error
