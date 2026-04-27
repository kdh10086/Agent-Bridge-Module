from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_bridge.codex.dispatcher import Dispatcher
from agent_bridge.codex.prompt_builder import PromptBuilder
from agent_bridge.core.command_queue import CommandQueue
from agent_bridge.core.event_log import EventLog
from agent_bridge.gui.clipboard import Clipboard
from agent_bridge.gui.macos_apps import AppActivator, ManualStageTarget
from agent_bridge.gui.manual_confirmation import ConfirmationRequest, ManualConfirmation
from agent_bridge.gui.pm_assistant_bridge import StageResult


LOCAL_AGENT_PROMPT_PATH = "next_local_agent_prompt.md"


@dataclass(frozen=True)
class LocalAgentDispatchResult:
    prompt_path: Path
    prompt: str
    staged: bool
    copied: bool = False
    activated: bool = False
    consumed: bool = False
    blocked: bool = False
    reason: str | None = None


def _confirm_request(confirmer: ManualConfirmation, request: ConfirmationRequest) -> bool:
    return confirmer.confirm_request(request)


def _activate_target(app_activator: AppActivator, target: ManualStageTarget) -> None:
    try:
        app_activator.activate(
            target.app_name,
            app_path=target.app_path,
            bundle_id=target.bundle_id,
        )
    except TypeError:
        app_activator.activate(target.app_name)


def stage_local_agent_prompt(
    *,
    workspace_dir: Path,
    template_dir: Path,
    dry_run: bool,
    copy_to_clipboard: bool = False,
    queue: CommandQueue | None = None,
    clipboard: Clipboard | None = None,
    confirmation: ManualConfirmation | None = None,
    event_log: EventLog | None = None,
) -> StageResult:
    command_queue = queue or CommandQueue(workspace_dir / "queue")
    log = event_log or EventLog(workspace_dir / "logs" / "bridge.jsonl")
    dispatch_result = Dispatcher(
        queue=command_queue,
        prompt_builder=PromptBuilder(template_dir),
        workspace_dir=workspace_dir,
        event_log=log,
    ).prepare_next_local_agent_prompt(consume=False, dry_run=dry_run)
    if dispatch_result.command is None:
        log.append("stage_local_agent_prompt_no_pending", prompt_path=str(dispatch_result.prompt_path))
        return StageResult(
            prompt_path=dispatch_result.prompt_path,
            prompt="",
            staged=False,
            reason="No pending commands.",
        )
    if dispatch_result.blocked:
        log.append("stage_local_agent_prompt_blocked", prompt_path=str(dispatch_result.prompt_path))
        return StageResult(
            prompt_path=dispatch_result.prompt_path,
            prompt=dispatch_result.prompt,
            staged=False,
            blocked=True,
            reason=dispatch_result.reason,
        )

    copied = False
    if copy_to_clipboard and not dry_run:
        confirmer = confirmation or ManualConfirmation()
        if confirmer.confirm("Copy staged local-agent prompt to clipboard?"):
            if clipboard is None:
                raise ValueError("clipboard is required when copy is confirmed.")
            clipboard.copy_text(dispatch_result.prompt)
            copied = True
        else:
            log.append("stage_local_agent_prompt_copy_declined", prompt_path=str(dispatch_result.prompt_path))

    log.append(
        "stage_local_agent_prompt",
        command_id=dispatch_result.command.id,
        command_type=dispatch_result.command.type.value,
        prompt_path=str(dispatch_result.prompt_path),
        dry_run=dry_run,
        copy_to_clipboard=copy_to_clipboard,
        copied=copied,
    )
    return StageResult(prompt_path=dispatch_result.prompt_path, prompt=dispatch_result.prompt, staged=True, copied=copied)


def dispatch_local_agent_prompt(
    *,
    workspace_dir: Path,
    template_dir: Path,
    copy_to_clipboard: bool,
    activate_app: bool,
    local_agent_target: ManualStageTarget,
    yes: bool = False,
    queue: CommandQueue | None = None,
    clipboard: Clipboard | None = None,
    confirmation: ManualConfirmation | None = None,
    app_activator: AppActivator | None = None,
    event_log: EventLog | None = None,
) -> LocalAgentDispatchResult:
    command_queue = queue or CommandQueue(workspace_dir / "queue")
    log = event_log or EventLog(workspace_dir / "logs" / "bridge.jsonl")
    confirmer = confirmation or ManualConfirmation()
    log.append(
        "local_agent_dispatch_requested",
        dry_run=False,
        consume_on_confirm=True,
        copy_to_clipboard=copy_to_clipboard,
        activate_app=activate_app,
    )

    dispatch_result = Dispatcher(
        queue=command_queue,
        prompt_builder=PromptBuilder(template_dir),
        workspace_dir=workspace_dir,
        event_log=log,
    ).prepare_next_local_agent_prompt(consume=False, dry_run=False)
    if dispatch_result.command is None:
        return LocalAgentDispatchResult(
            prompt_path=dispatch_result.prompt_path,
            prompt="",
            staged=False,
            reason="No pending commands.",
        )
    if dispatch_result.blocked:
        return LocalAgentDispatchResult(
            prompt_path=dispatch_result.prompt_path,
            prompt=dispatch_result.prompt,
            staged=False,
            blocked=True,
            reason=dispatch_result.reason,
        )

    copied = False
    activated = False
    if copy_to_clipboard:
        copy_request = ConfirmationRequest(
            action_summary="Copy staged local-agent prompt to clipboard",
            target_app_name=local_agent_target.app_name,
            target_window_hint=local_agent_target.window_hint,
            prompt_path=dispatch_result.prompt_path,
            will_do=(
                "Copy the staged local-agent prompt text to the system clipboard.",
                "Leave the prompt file available in workspace/outbox.",
            ),
        )
        confirmed = yes or _confirm_request(confirmer, copy_request)
        if confirmed:
            if clipboard is None:
                raise ValueError("clipboard is required when copy is confirmed.")
            clipboard.copy_text(dispatch_result.prompt)
            copied = True
            log.append(
                "local_agent_clipboard_copy_confirmed",
                command_id=dispatch_result.command.id,
                prompt_path=str(dispatch_result.prompt_path),
                yes=yes,
            )
        else:
            log.append(
                "local_agent_clipboard_copy_cancelled",
                command_id=dispatch_result.command.id,
                prompt_path=str(dispatch_result.prompt_path),
            )

    if activate_app:
        activation_request = ConfirmationRequest(
            action_summary=f"Activate local coding agent app '{local_agent_target.app_name}'",
            target_app_name=local_agent_target.app_name,
            target_window_hint=local_agent_target.window_hint,
            prompt_path=dispatch_result.prompt_path,
            will_do=(
                f"Ask macOS to focus the configured app: {local_agent_target.app_name}.",
                "Print the manual paste instructions in the current Agent Bridge CLI output.",
            ),
        )
        confirmed = yes or _confirm_request(confirmer, activation_request)
        if confirmed:
            if app_activator is None:
                raise ValueError("app_activator is required when activation is confirmed.")
            _activate_target(app_activator, local_agent_target)
            activated = True
            log.append(
                "local_agent_activation_confirmed",
                command_id=dispatch_result.command.id,
                app_name=local_agent_target.app_name,
                yes=yes,
            )
        else:
            log.append(
                "local_agent_activation_cancelled",
                command_id=dispatch_result.command.id,
                app_name=local_agent_target.app_name,
            )

    consumed = False
    if copied or activated:
        consumed = command_queue.pop_by_id(dispatch_result.command.id) is not None

    return LocalAgentDispatchResult(
        prompt_path=dispatch_result.prompt_path,
        prompt=dispatch_result.prompt,
        staged=True,
        copied=copied,
        activated=activated,
        consumed=consumed,
    )
