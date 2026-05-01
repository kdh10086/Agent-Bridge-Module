from __future__ import annotations

import json
from enum import Enum
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import typer
from rich.console import Console
from rich.table import Table

from agent_bridge.codex.dispatcher import Dispatcher
from agent_bridge.codex.output_collector import AgentReportCollector
from agent_bridge.codex.prompt_builder import PromptBuilder
from agent_bridge.core.command_queue import CommandQueue
from agent_bridge.core.event_log import EventLog
from agent_bridge.core.models import BridgeStateName, Command, CommandStatus, CommandType
from agent_bridge.core.safety_gate import SafetyGate
from agent_bridge.core.state_store import StateStore
from agent_bridge.github.ci_watcher import ingest_ci_fixture, watch_ci_failures
from agent_bridge.github.dogfood import dogfood_gh_dry_run
from agent_bridge.github.gh_client import GhClientError
from agent_bridge.github.review_watcher import ingest_review_fixture, watch_review_comments
from agent_bridge.gui.clipboard import ClipboardError, MacOSClipboard
from agent_bridge.gui.asset_state_machine import (
    AssetVisualStateDetector,
    asset_profile_for_target,
    format_visual_state_detection,
)
from agent_bridge.gui.chatgpt_mac_response_capture import (
    diagnose_chatgpt_mac_response_capture as run_chatgpt_mac_response_capture_diagnostic,
    format_chatgpt_mac_response_capture,
)
from agent_bridge.gui.chatgpt_mac_composer import (
    diagnose_chatgpt_mac_composer_text_state as run_chatgpt_mac_composer_text_state_diagnostic,
    format_chatgpt_mac_composer_text_state,
)
from agent_bridge.gui.chatgpt_mac_native import (
    diagnose_chatgpt_app_targets as run_chatgpt_app_target_diagnostic,
    format_app_window_bounds_result,
    format_chatgpt_app_target_diagnostic,
    format_chatgpt_native_preflight,
    preflight_chatgpt_mac_native_target as run_chatgpt_mac_native_preflight,
    set_app_window_bounds as run_set_app_window_bounds,
)
from agent_bridge.gui.app_diagnostics import (
    app_path_for_target,
    diagnose_app_bundle,
    format_app_diagnostic,
)
from agent_bridge.gui.computer_use_trigger import (
    build_computer_use_terminal_trigger,
    write_computer_use_terminal_trigger,
)
from agent_bridge.gui.codex_ui_detector import (
    CodexUIDetector,
    format_codex_focus_target_comparison,
    format_codex_paste_test_result,
    format_codex_input_target_diagnostic,
    format_codex_ui_diagnostic,
    format_codex_ui_tree_dump,
    format_codex_window_selection,
)
from agent_bridge.gui.external_runner import format_external_runner_preflight, preflight_external_runner
from agent_bridge.gui.external_runner_daemon import (
    ExternalGuiRunner,
    ExternalGuiRunnerConfig,
    ExternalGuiRunnerError,
)
from agent_bridge.gui.foreground_bridge_runner import (
    ForegroundBridgeRunner,
    ForegroundBridgeRunnerConfig,
    format_bridge_preflight,
    run_bridge_preflight,
    targets_with_pm_profile_for_startup,
    targets_with_pm_profile,
)
from agent_bridge.gui.gui_automation import MacOSSystemEventsGuiAdapter
from agent_bridge.gui.gui_dogfood import GuiDogfoodConfig, GuiDogfoodError, run_gui_bridge_dogfood
from agent_bridge.gui.iterm_ghost_runner import (
    evaluate_iterm_ghost_runner_context,
    format_iterm_ghost_runner_context,
)
from agent_bridge.gui.local_agent_bridge import (
    dispatch_local_agent_prompt,
    stage_local_agent_prompt as stage_local_agent_prompt_bridge,
)
from agent_bridge.gui.macos_apps import (
    CHATGPT_CHROME_APP_PROFILE,
    CHATGPT_MAC_PROFILE,
    AppActivationError,
    GuiTargets,
    MacOSAppActivator,
    ManualStageTarget,
    discover_gui_apps,
    ensure_chatgpt_chrome_app_target,
    ensure_native_chatgpt_mac_target,
    format_activation_plan,
    format_activation_result,
    format_target_guidance,
    is_chatgpt_mac_visual_target,
    load_gui_targets,
    normalize_pm_target_profile,
    pm_target_for_profile,
    replace_manual_stage_target,
)
from agent_bridge.gui.macos_permissions import (
    diagnose_macos_permissions as run_macos_permission_diagnostic,
    format_macos_permission_diagnostic,
)
from agent_bridge.gui.macos_terminal_confirmation import MacOSTerminalConfirmation
from agent_bridge.gui.manual_confirmation import ManualConfirmation
from agent_bridge.gui.pm_assistant_bridge import stage_pm_prompt as stage_pm_prompt_bridge
from agent_bridge.gui.pm_backend import (
    format_pm_backend_preflight_result,
    merge_pm_target_override,
    preflight_pm_backend,
)
from agent_bridge.gui.pm_visual_sequence_diagnostic import (
    diagnose_pm_visual_sequence as run_pm_visual_sequence_diagnostic,
    format_pm_visual_sequence_diagnostic,
)
from agent_bridge.gui.pm_paste_backend_diagnostic import (
    diagnose_paste_backends as run_pm_paste_backend_diagnostic,
    format_paste_backend_diagnostic,
)
from agent_bridge.gui.report_roundtrip import (
    ReportRoundtripConfig,
    ReportRoundtripError,
    build_report_roundtrip_pm_prompt,
    run_report_roundtrip,
)
from agent_bridge.gui.roundtrip_verifier import format_roundtrip_verification, verify_roundtrip_artifacts
from agent_bridge.orchestrator import RunLoop, RunLoopConfig
from agent_bridge.portable.installer import (
    format_install_plan,
    format_verify_result,
    install_portable as install_portable_module,
    verify_portable as verify_portable_target,
)

app = typer.Typer(help="Agent Bridge CLI")
queue_app = typer.Typer(help="Queue commands")
queue_malformed_app = typer.Typer(help="Inspect quarantined malformed queue records")
app.add_typer(queue_app, name="queue")
queue_app.add_typer(queue_malformed_app, name="malformed")
console = Console()


class ConfirmationMode(str, Enum):
    INLINE = "inline"
    TERMINAL_WINDOW = "terminal-window"


class PMTargetProfile(str, Enum):
    chatgpt_mac = CHATGPT_MAC_PROFILE
    chatgpt_chrome_app = CHATGPT_CHROME_APP_PROFILE


ROOT = Path.cwd()
WORKSPACE = ROOT / "workspace"
QUEUE_DIR = WORKSPACE / "queue"
STATE_PATH = WORKSPACE / "state" / "state.json"
LOG_PATH = WORKSPACE / "logs" / "bridge.jsonl"
TEMPLATE_DIR = ROOT / "agent_bridge" / "templates"
CONFIG_DIR = ROOT / "config"


def ensure_workspace() -> None:
    for sub in ["state", "queue", "inbox", "outbox", "reports", "reviews", "logs", "triggers"]:
        (WORKSPACE / sub).mkdir(parents=True, exist_ok=True)


@app.command()
def init(force: bool = False) -> None:
    ensure_workspace()
    store = StateStore(STATE_PATH)
    if force or not STATE_PATH.exists():
        store.reset()
    if force:
        for path in [
            QUEUE_DIR / "pending_commands.jsonl",
            QUEUE_DIR / "completed_commands.jsonl",
            QUEUE_DIR / "failed_commands.jsonl",
            QUEUE_DIR / "blocked_commands.jsonl",
            QUEUE_DIR / "malformed_commands.jsonl",
            QUEUE_DIR / "in_progress.json",
        ]:
            path.unlink(missing_ok=True)
    report = WORKSPACE / "reports" / "latest_agent_report.md"
    if not report.exists():
        report.write_text("# Agent Report\n\nNo task has been run yet.\n", encoding="utf-8")
    EventLog(LOG_PATH).append("init", force=force)
    console.print("[green]Workspace initialized.[/green]")


@app.command()
def status() -> None:
    ensure_workspace()
    console.print(StateStore(STATE_PATH).load().model_dump_json(indent=2))


@app.command()
def enqueue(
    type: CommandType = typer.Option(..., "--type"),
    payload: Path = typer.Option(..., "--payload"),
    source: str = "manual",
    task_id: str | None = None,
    dedupe_key: str | None = None,
) -> None:
    ensure_workspace()
    command = _build_queue_command(
        command_type=type,
        source=source,
        task_id=task_id,
        prompt_path=str(payload),
        dedupe_key=dedupe_key or f"{type.value}:{payload}:{task_id or ''}",
    )
    added = _enqueue_queue_command(command)
    console.print("[green]Enqueued.[/green]" if added else "[yellow]Duplicate ignored.[/yellow]")


def _build_queue_command(
    *,
    command_type: CommandType,
    source: str,
    task_id: str | None,
    prompt_path: str = "",
    prompt_text: str | None = None,
    priority: int | None = None,
    dedupe_key: str | None = None,
    metadata: dict[str, object] | None = None,
) -> Command:
    return Command(
        id=f"cmd_{uuid4().hex[:12]}",
        type=command_type,
        priority=priority,
        source=source,
        task_id=task_id,
        prompt_path=prompt_path or None,
        prompt_text=prompt_text,
        dedupe_key=dedupe_key
        or f"{command_type.value}:{prompt_path or prompt_text or ''}:{task_id or ''}",
        metadata=metadata or {},
    )


def _enqueue_queue_command(command: Command) -> bool:
    added = CommandQueue(QUEUE_DIR).enqueue(command)
    EventLog(LOG_PATH).append(
        "enqueue",
        added=added,
        command_id=command.id,
        type=command.type.value,
        source=command.source,
    )
    return added


def _parse_queue_status(value: str) -> CommandStatus | None:
    normalized = value.strip().lower()
    if normalized == "all":
        return None
    try:
        return CommandStatus(normalized)
    except ValueError as error:
        allowed = ", ".join([status.value for status in CommandStatus] + ["all"])
        raise typer.BadParameter(f"Unknown queue status {value!r}. Expected one of: {allowed}") from error


def _print_queue_commands(commands: list[Command], *, title: str) -> None:
    table = Table(title=title)
    for col in ["id", "status", "priority", "source", "prompt_source", "created_at", "reason"]:
        table.add_column(col)
    for command in sorted(
        commands,
        key=lambda item: (
            item.status.value,
            -(item.priority or 0),
            item.created_at,
            item.id,
        ),
    ):
        table.add_row(
            command.id,
            command.status.value,
            str(command.priority),
            command.source,
            _queue_prompt_source_type(command),
            command.created_at,
            _queue_reason(command),
        )
    console.print(table)


def _queue_prompt_source_type(command: Command) -> str:
    if command.prompt_text is not None:
        return f"prompt_text len={len(command.prompt_text)}"
    if command.prompt_path:
        return "prompt_path"
    if command.payload_path:
        return "payload_path legacy"
    return "missing"


def _queue_reason(command: Command) -> str:
    if command.status == CommandStatus.FAILED:
        return str(command.metadata.get("failure_reason", ""))
    if command.status == CommandStatus.BLOCKED:
        return str(command.metadata.get("blocked_reason", ""))
    return ""


def _raw_prompt_source_summary(raw_line: str) -> str:
    try:
        record = Command.model_validate_json(raw_line)
    except Exception:
        try:
            raw_record = json.loads(raw_line)
        except Exception:
            return f"invalid-json len={len(raw_line)} sha256={sha256(raw_line.encode()).hexdigest()[:12]}"
        prompt_text = raw_record.get("prompt_text")
        prompt_path = raw_record.get("prompt_path")
        payload_path = raw_record.get("payload_path")
        if prompt_text is not None:
            text = str(prompt_text)
            return f"prompt_text len={len(text)} sha256={sha256(text.encode()).hexdigest()[:12]}"
        if prompt_path:
            return "prompt_path"
        if payload_path:
            return "payload_path legacy"
        return f"missing len={len(raw_line)} sha256={sha256(raw_line.encode()).hexdigest()[:12]}"
    return _queue_prompt_source_type(record)


def _print_malformed_records(records: list[dict[str, object]], *, title: str) -> None:
    table = Table(title=title)
    for col in ["index", "id", "source_path", "line", "prompt_source", "error"]:
        table.add_column(col)
    for record in records:
        raw_line = str(record.get("raw_line", ""))
        table.add_row(
            str(record.get("index", "")),
            str(record.get("id", ""))[:12],
            str(record.get("source_path", "")),
            str(record.get("line_number", "")),
            _raw_prompt_source_summary(raw_line),
            str(record.get("error", ""))[:160],
        )
    console.print(table)


@queue_app.command("enqueue")
def queue_enqueue(
    type: CommandType = typer.Option(..., "--type"),
    payload: Path | None = typer.Option(None, "--payload"),
    prompt_text: str | None = typer.Option(None, "--prompt-text"),
    source: str = "manual",
    task_id: str | None = None,
    priority: int | None = None,
    dedupe_key: str | None = None,
) -> None:
    ensure_workspace()
    if payload is None and prompt_text is None:
        raise typer.BadParameter("Provide --payload or --prompt-text.")
    if payload is not None and prompt_text is not None:
        raise typer.BadParameter("Use only one of --payload or --prompt-text.")
    command = _build_queue_command(
        command_type=type,
        source=source,
        task_id=task_id,
        prompt_path=str(payload) if payload is not None else "",
        prompt_text=prompt_text,
        priority=priority,
        dedupe_key=dedupe_key,
    )
    added = _enqueue_queue_command(command)
    console.print("[green]Enqueued.[/green]" if added else "[yellow]Duplicate ignored.[/yellow]")


@queue_app.command("list")
def queue_list(status: str = typer.Option("pending", "--status")) -> None:
    ensure_workspace()
    parsed_status = _parse_queue_status(status)
    commands = CommandQueue(QUEUE_DIR).list_commands(parsed_status)
    _print_queue_commands(commands, title=f"{status.title()} Commands")


@queue_app.command("peek")
def queue_peek() -> None:
    ensure_workspace()
    command = CommandQueue(QUEUE_DIR).peek_next()
    console.print("[yellow]No pending command.[/yellow]" if command is None else command.model_dump_json(indent=2))


@queue_app.command("pop")
def queue_pop() -> None:
    ensure_workspace()
    command = CommandQueue(QUEUE_DIR).pop_next()
    console.print("[yellow]No pending command.[/yellow]" if command is None else command.model_dump_json(indent=2))


@queue_app.command("mark-in-progress")
def queue_mark_in_progress(command_id: str | None = typer.Argument(None)) -> None:
    ensure_workspace()
    queue = CommandQueue(QUEUE_DIR)
    command = queue.mark_in_progress(command_id) if command_id else queue.pop_next()
    console.print("[yellow]No pending command selected.[/yellow]" if command is None else command.model_dump_json(indent=2))


@queue_app.command("mark-completed")
def queue_mark_completed(command_id: str | None = typer.Argument(None)) -> None:
    ensure_workspace()
    command = CommandQueue(QUEUE_DIR).mark_completed(command_id)
    console.print("[yellow]No matching command.[/yellow]" if command is None else command.model_dump_json(indent=2))


@queue_app.command("mark-failed")
def queue_mark_failed(
    command_id: str | None = typer.Argument(None),
    reason: str = typer.Option("manual failure", "--reason"),
) -> None:
    ensure_workspace()
    command = CommandQueue(QUEUE_DIR).mark_failed(reason, command_id)
    console.print("[yellow]No matching command.[/yellow]" if command is None else command.model_dump_json(indent=2))


@queue_app.command("mark-blocked")
def queue_mark_blocked(
    command_id: str | None = typer.Argument(None),
    reason: str = typer.Option("manual block", "--reason"),
) -> None:
    ensure_workspace()
    command = CommandQueue(QUEUE_DIR).mark_blocked(reason, command_id)
    console.print("[yellow]No matching command.[/yellow]" if command is None else command.model_dump_json(indent=2))


@queue_malformed_app.command("list")
def queue_malformed_list() -> None:
    ensure_workspace()
    records = CommandQueue(QUEUE_DIR).list_malformed_records()
    if not records:
        console.print("[green]No malformed queue records.[/green]")
        return
    _print_malformed_records(records, title="Malformed Queue Records")


@queue_malformed_app.command("inspect")
def queue_malformed_inspect(
    index: int = typer.Argument(...),
    show_raw: bool = typer.Option(False, "--show-raw"),
) -> None:
    ensure_workspace()
    records = CommandQueue(QUEUE_DIR).list_malformed_records()
    match = next((record for record in records if int(record.get("index", -1)) == index), None)
    if match is None:
        console.print("[yellow]No malformed queue record with that index.[/yellow]")
        return
    raw_line = str(match.get("raw_line", ""))
    output = {
        "index": match.get("index"),
        "id": match.get("id"),
        "source_path": match.get("source_path"),
        "line_number": match.get("line_number"),
        "error": match.get("error"),
        "raw_line_length": len(raw_line),
        "raw_line_sha256": sha256(raw_line.encode()).hexdigest(),
        "prompt_source": _raw_prompt_source_summary(raw_line),
    }
    if show_raw:
        output["raw_line"] = raw_line
    console.print_json(data=output)


@queue_app.command("repair")
def queue_repair(
    apply: bool = typer.Option(False, "--apply", help="Write schema-valid repaired records back to pending queue."),
) -> None:
    ensure_workspace()
    results = CommandQueue(QUEUE_DIR).repair_malformed_records(apply=apply)
    if not results:
        console.print("[green]No malformed queue records to repair.[/green]")
        return
    table = Table(title="Queue Repair Results" + (" (applied)" if apply else " (dry-run)"))
    for col in ["index", "command_id", "repairable", "applied", "reason"]:
        table.add_column(col)
    for result in results:
        table.add_row(
            str(result.get("index", "")),
            str(result.get("command_id") or ""),
            str(result.get("repairable", False)).lower(),
            str(result.get("applied", False)).lower(),
            str(result.get("reason", ""))[:160],
        )
    console.print(table)


@app.command("dispatch-next")
def dispatch_next(
    dry_run: bool | None = typer.Option(None, "--dry-run/--no-dry-run"),
    stage_only: bool = typer.Option(False, "--stage-only"),
    copy_to_clipboard: bool = typer.Option(False, "--copy-to-clipboard"),
    activate_app: bool = typer.Option(False, "--activate-app"),
    confirmation_mode: ConfirmationMode = typer.Option(
        ConfirmationMode.TERMINAL_WINDOW,
        "--confirmation-mode",
    ),
    confirmation_timeout_seconds: int = typer.Option(120, "--confirmation-timeout-seconds"),
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    ensure_workspace()
    targets = load_gui_targets(CONFIG_DIR)
    effective_dry_run = dry_run if dry_run is not None else not (copy_to_clipboard or activate_app)

    if stage_only and (copy_to_clipboard or activate_app):
        console.print("[red]--stage-only cannot be combined with clipboard copy or app activation.[/red]")
        raise typer.Exit(1)

    if stage_only:
        result = stage_local_agent_prompt_bridge(
            workspace_dir=WORKSPACE,
            template_dir=TEMPLATE_DIR,
            dry_run=True,
            queue=CommandQueue(QUEUE_DIR),
            event_log=EventLog(LOG_PATH),
        )
        if result.blocked:
            console.print("[red]Dispatch blocked by safety gate.[/red]")
            raise typer.Exit(1)
        if not result.staged:
            console.print(f"[yellow]{result.reason}[/yellow]")
            return
        console.print(f"[green]Local-agent prompt staged:[/green] {result.prompt_path}")
        console.print(format_target_guidance("Local coding agent", targets.local_agent))
        console.print("[bold cyan]STAGE ONLY: queue was not consumed.[/bold cyan]")
        return

    if effective_dry_run:
        Dispatcher(
            queue=CommandQueue(QUEUE_DIR),
            prompt_builder=PromptBuilder(TEMPLATE_DIR),
            workspace_dir=WORKSPACE,
            console=console,
        ).dispatch_next(dry_run=True)
        console.print(format_target_guidance("Local coding agent", targets.local_agent))
        return

    if not copy_to_clipboard and not activate_app:
        console.print("[red]Real dispatch requires --copy-to-clipboard or --activate-app.[/red]")
        console.print("Automatic paste and automatic submit are not implemented.")
        raise typer.Exit(1)

    if confirmation_timeout_seconds <= 0:
        console.print("[red]--confirmation-timeout-seconds must be greater than zero.[/red]")
        raise typer.Exit(1)

    confirmation: ManualConfirmation | MacOSTerminalConfirmation
    if confirmation_mode == ConfirmationMode.INLINE:
        confirmation = ManualConfirmation(lambda message: typer.confirm(message, default=False))
    else:
        confirmation = MacOSTerminalConfirmation(
            workspace_dir=WORKSPACE,
            timeout_seconds=confirmation_timeout_seconds,
            event_log=EventLog(LOG_PATH),
        )

    try:
        result = dispatch_local_agent_prompt(
            workspace_dir=WORKSPACE,
            template_dir=TEMPLATE_DIR,
            copy_to_clipboard=copy_to_clipboard,
            activate_app=activate_app,
            local_agent_target=targets.local_agent,
            yes=yes,
            queue=CommandQueue(QUEUE_DIR),
            clipboard=MacOSClipboard(),
            confirmation=confirmation,
            app_activator=MacOSAppActivator(),
            event_log=EventLog(LOG_PATH),
        )
    except (AppActivationError, ClipboardError, ValueError) as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    if result.blocked:
        console.print("[red]Dispatch blocked by safety gate.[/red]")
        raise typer.Exit(1)
    if not result.staged:
        console.print(f"[yellow]{result.reason}[/yellow]")
        return

    console.print(f"[green]Local-agent prompt staged:[/green] {result.prompt_path}")
    console.print(format_target_guidance("Local coding agent", targets.local_agent))
    if result.copied:
        console.print("[green]Copied staged prompt to clipboard after confirmation.[/green]")
    elif copy_to_clipboard:
        console.print("[yellow]Clipboard copy cancelled; queue was not consumed by that step.[/yellow]")
    if result.activated:
        console.print("[green]Local coding agent app activated after confirmation.[/green]")
    elif activate_app:
        console.print("[yellow]App activation cancelled; queue was not consumed by that step.[/yellow]")
    console.print(
        "[green]Command moved to in-progress.[/green]"
        if result.consumed
        else "[yellow]Command remains pending.[/yellow]"
    )


@app.command("collect-agent-report")
def collect_agent_report() -> None:
    ensure_workspace()
    console.print(AgentReportCollector(WORKSPACE / "reports" / "latest_agent_report.md").collect())


@app.command("send-report-to-pm")
def send_report_to_pm(dry_run: bool = True) -> None:
    ensure_workspace()
    report = AgentReportCollector(WORKSPACE / "reports" / "latest_agent_report.md").collect()
    prompt = PromptBuilder(TEMPLATE_DIR).build_pm_report_prompt(report)
    (WORKSPACE / "outbox" / "pm_report_prompt.md").write_text(prompt, encoding="utf-8")
    if dry_run:
        console.print("[bold cyan]DRY RUN: PM prompt[/bold cyan]")
        console.print(prompt)
    else:
        raise NotImplementedError("Real PM assistant GUI bridge is not implemented in MVP.")


@app.command("show-gui-targets")
def show_gui_targets() -> None:
    targets = load_gui_targets(CONFIG_DIR)
    console.print(format_target_guidance("PM assistant", targets.pm_assistant))
    console.print(format_target_guidance("Local coding agent", targets.local_agent))
    console.print("App activation: manual-confirmation only for local-agent dispatch.")
    console.print("Automatic submit/Enter: not supported.")


def _override_target(target: ManualStageTarget, app_name: str | None) -> ManualStageTarget:
    if not app_name:
        return target
    return ManualStageTarget(
        app_name=app_name,
        app_path=target.app_path,
        bundle_id=target.bundle_id,
        backend=target.backend,
        profile=target.profile,
        require_backend_preflight=target.require_backend_preflight,
        window_hint=target.window_hint,
        paste_instruction=target.paste_instruction,
        focus_strategy=target.focus_strategy,
        visual_asset_profile=target.visual_asset_profile,
        response_copy_css_selector=target.response_copy_css_selector,
        response_copy_xpath=target.response_copy_xpath,
        response_copy_full_xpath=target.response_copy_full_xpath,
        response_copy_strategy=target.response_copy_strategy,
        idle_empty_timeout_seconds=target.idle_empty_timeout_seconds,
        input_focus_strategy=target.input_focus_strategy,
        click_backend=target.click_backend,
        visual_anchor_click_backend=target.visual_anchor_click_backend,
        paste_backend=target.paste_backend,
        input_click_x_ratio=target.input_click_x_ratio,
        input_click_y_ratio=target.input_click_y_ratio,
        require_prompt_presence_verification=target.require_prompt_presence_verification,
        allow_unverified_submit=target.allow_unverified_submit,
        allow_unverified_submit_for_noop_dogfood=target.allow_unverified_submit_for_noop_dogfood,
        composer_placeholder_text=target.composer_placeholder_text,
        idle_empty_wait_timeout_seconds=target.idle_empty_wait_timeout_seconds,
        idle_empty_poll_interval_seconds=target.idle_empty_poll_interval_seconds,
        dedicated_automation_session=target.dedicated_automation_session,
        allow_overwrite_after_idle_timeout=target.allow_overwrite_after_idle_timeout,
        stop_on_idle_timeout=target.stop_on_idle_timeout,
        plus_anchor_enabled=target.plus_anchor_enabled,
        plus_anchor_x_offset=target.plus_anchor_x_offset,
        plus_anchor_y_offset=target.plus_anchor_y_offset,
        direct_plus_anchor_enabled=target.direct_plus_anchor_enabled,
        direct_plus_anchor_x_offset=target.direct_plus_anchor_x_offset,
        direct_plus_anchor_y_offset=target.direct_plus_anchor_y_offset,
        direct_plus_anchor_y_offset_candidates=target.direct_plus_anchor_y_offset_candidates,
        composer_policy_mode=target.composer_policy_mode,
        busy_placeholder_wait_timeout_seconds=target.busy_placeholder_wait_timeout_seconds,
        busy_placeholder_poll_interval_seconds=target.busy_placeholder_poll_interval_seconds,
        on_busy_timeout=target.on_busy_timeout,
        visual_text_recognition_enabled=target.visual_text_recognition_enabled,
        visual_text_recognition_ocr_backend=target.visual_text_recognition_ocr_backend,
        visual_text_recognition_marker_text=target.visual_text_recognition_marker_text,
        visual_text_recognition_placeholder_text=target.visual_text_recognition_placeholder_text,
        visual_text_recognition_search_region=target.visual_text_recognition_search_region,
        visual_plus_templates=target.visual_plus_templates,
        visual_send_disabled_templates=target.visual_send_disabled_templates,
        visual_send_templates=target.visual_send_templates,
        visual_stop_templates=target.visual_stop_templates,
        visual_plus_confidence_threshold=target.visual_plus_confidence_threshold,
        visual_state_confidence_threshold=target.visual_state_confidence_threshold,
        visual_state_ambiguity_margin=target.visual_state_ambiguity_margin,
        visual_state_search_region=target.visual_state_search_region,
        visual_plus_multiscale_enabled=target.visual_plus_multiscale_enabled,
        owner_reviewed_focus_candidates=target.owner_reviewed_focus_candidates,
        min_main_window_width=target.min_main_window_width,
        min_main_window_height=target.min_main_window_height,
        min_main_window_area=target.min_main_window_area,
        window_selection_strategy=target.window_selection_strategy,
    )


def _configured_pm_profile(target: ManualStageTarget, override: PMTargetProfile | None = None) -> str:
    if override is not None:
        return normalize_pm_target_profile(override.value)
    return normalize_pm_target_profile(
        target.profile or target.visual_asset_profile or CHATGPT_MAC_PROFILE
    )


def _resolve_watch_report_path(value: Path) -> Path:
    if value.is_absolute():
        return value
    return ROOT / value


def _targets_for_pm_target_override(
    targets: GuiTargets,
    override: PMTargetProfile | None,
) -> tuple[str, GuiTargets]:
    profile = _configured_pm_profile(targets.pm_assistant, override)
    return profile, targets_with_pm_profile(targets, profile)


def _targets_for_pm_target_override_startup(
    targets: GuiTargets,
    override: PMTargetProfile | None,
) -> tuple[str, GuiTargets]:
    profile = _configured_pm_profile(targets.pm_assistant, override)
    return profile, targets_with_pm_profile_for_startup(targets, profile)


def _selected_candidate_for_bundle(candidates, bundle_id: str | None):
    for candidate in candidates:
        if candidate.selected and (bundle_id is None or candidate.bundle_id == bundle_id):
            return candidate
    return None


def _resolve_pm_profile_target(profile: str) -> ManualStageTarget:
    targets = load_gui_targets(CONFIG_DIR)
    target = pm_target_for_profile(targets.pm_assistant, profile)
    if profile == CHATGPT_MAC_PROFILE:
        return ensure_native_chatgpt_mac_target(target)
    target = ensure_chatgpt_chrome_app_target(target)
    diagnostic = run_chatgpt_app_target_diagnostic(
        target=target,
        profile=CHATGPT_CHROME_APP_PROFILE,
    )
    candidate = _selected_candidate_for_bundle(diagnostic.candidates, diagnostic.selected_bundle_id)
    if candidate is None:
        console.print(format_chatgpt_app_target_diagnostic(diagnostic))
        console.print("[red]ChatGPT Chrome app target is unavailable.[/red]")
        raise typer.Exit(1)
    return replace_manual_stage_target(
        target,
        app_name=candidate.name or target.app_name,
        bundle_id=candidate.bundle_id,
        backend=target.backend or "chatgpt_chrome_app_visual",
        profile=CHATGPT_CHROME_APP_PROFILE,
        visual_asset_profile=CHATGPT_CHROME_APP_PROFILE,
    )


def _resolve_visual_state_target(app_name: str) -> ManualStageTarget:
    normalized = app_name.strip().lower().replace("-", "_")
    if normalized == CHATGPT_MAC_PROFILE:
        return _resolve_pm_profile_target(CHATGPT_MAC_PROFILE)
    if normalized == CHATGPT_CHROME_APP_PROFILE:
        return _resolve_pm_profile_target(CHATGPT_CHROME_APP_PROFILE)
    if normalized == "codex":
        return load_gui_targets(CONFIG_DIR).local_agent
    console.print("[red]--app must be chatgpt_mac, chatgpt_chrome_app, or codex.[/red]")
    raise typer.Exit(2)


def _parse_bounds_option(value: str) -> tuple[int, int, int, int]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 4:
        console.print("[red]--bounds must be formatted as x,y,width,height.[/red]")
        raise typer.Exit(2)
    try:
        x, y, width, height = (int(part) for part in parts)
    except ValueError:
        console.print("[red]--bounds values must be integers.[/red]")
        raise typer.Exit(2)
    if width <= 0 or height <= 0:
        console.print("[red]--bounds width and height must be positive.[/red]")
        raise typer.Exit(2)
    return (x, y, width, height)


def _run_response_capture_diagnostic(
    *,
    app_name: str,
    attempt_copy: bool,
    expected_marker: str | None,
) -> None:
    ensure_workspace()
    normalized = app_name.strip().lower().replace("-", "_")
    if normalized not in {CHATGPT_MAC_PROFILE, CHATGPT_CHROME_APP_PROFILE}:
        console.print("[red]--app must be chatgpt_mac or chatgpt_chrome_app.[/red]")
        raise typer.Exit(2)
    target = _resolve_pm_profile_target(normalized)
    log = EventLog(LOG_PATH)
    try:
        MacOSAppActivator().activate(
            target.app_name,
            app_path=target.app_path,
            bundle_id=target.bundle_id,
        )
    except AppActivationError as error:
        console.print(f"[red]Activation failed:[/red] {error}")
        raise typer.Exit(1) from error
    window_detector = CodexUIDetector()
    window_selection = window_detector.select_main_window(target)
    clipboard = MacOSClipboard() if attempt_copy else None
    result = run_chatgpt_mac_response_capture_diagnostic(
        target=target,
        window_bounds=window_selection.selected_bounds,
        logs_dir=WORKSPACE / "logs",
        write_debug=True,
        attempt_copy=attempt_copy,
        clipboard=clipboard,
        expected_marker=expected_marker,
    )
    if attempt_copy and result.response_captured and clipboard is not None:
        response_text = clipboard.read_text()
        out_path = WORKSPACE / "outbox" / f"{normalized}_response_capture.md"
        out_path.write_text(response_text, encoding="utf-8")
    log.append(
        f"{normalized}_response_capture_diagnostic_run",
        window_bounds=result.window_bounds,
        screenshot_captured=result.screenshot_captured,
        supported=result.supported,
        copy_button_found=result.copy_button_found,
        matched_asset_path=result.matched_asset_path,
        confidence=result.copy_button_confidence,
        copy_detection_attempt_count=result.copy_detection_attempt_count,
        scroll_button_found=result.scroll_button_found,
        scroll_attempted=result.scroll_attempted,
        scroll_succeeded=result.scroll_succeeded,
        matched_scroll_asset_path=result.matched_scroll_asset_path,
        scroll_confidence=result.scroll_button_confidence,
        capture_attempted=result.capture_attempted,
        response_captured=result.response_captured,
        response_length=result.response_length,
        missing_copy_assets=result.missing_copy_assets,
        missing_scroll_assets=result.missing_scroll_assets,
        error=result.error,
    )
    console.print(format_chatgpt_mac_response_capture(result))
    if not result.screenshot_captured or not result.backend_available:
        raise typer.Exit(1)
    if attempt_copy and not result.response_captured:
        raise typer.Exit(1)


@app.command("preflight-gui-apps")
def preflight_gui_apps(
    dry_run: bool = typer.Option(False, "--dry-run"),
    pm_app: str | None = typer.Option(None, "--pm-app"),
    local_agent_app: str | None = typer.Option(None, "--local-agent-app"),
    activate: bool = typer.Option(False, "--activate"),
) -> None:
    targets = load_gui_targets(CONFIG_DIR)
    selected: list[tuple[str, ManualStageTarget]] = []
    if pm_app or not local_agent_app:
        selected.append(("PM assistant", _override_target(targets.pm_assistant, pm_app)))
    if local_agent_app or not pm_app:
        selected.append(("Local coding agent", _override_target(targets.local_agent, local_agent_app)))

    activator = MacOSAppActivator()
    any_failed = False
    for label, target in selected:
        console.print(format_target_guidance(label, target))
        console.print(format_activation_plan(label, target))
        if dry_run or not activate:
            console.print("[bold cyan]DRY RUN: activation skipped.[/bold cyan]")
            continue
        result = activator.activate_with_result(
            target.app_name,
            app_path=target.app_path,
            bundle_id=target.bundle_id,
        )
        console.print(format_activation_result(result))
        any_failed = any_failed or not result.succeeded

    console.print("No paste, submit, Enter/Return, GitHub, or Gmail action was attempted.")
    if any_failed:
        raise typer.Exit(1)


@app.command("preflight-pm-backend")
def preflight_pm_backend_command(
    app_name: str | None = typer.Option(None, "--app-name"),
    bundle_id: str | None = typer.Option(None, "--bundle-id"),
    app_path: Path | None = typer.Option(None, "--app-path"),
    backend: str | None = typer.Option(None, "--backend"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    activate: bool = typer.Option(False, "--activate"),
) -> None:
    ensure_workspace()
    targets = load_gui_targets(CONFIG_DIR)
    target = merge_pm_target_override(
        targets.pm_assistant,
        app_name=app_name,
        app_path=app_path,
        bundle_id=bundle_id,
        backend=backend,
    )
    result = preflight_pm_backend(
        target=target,
        dry_run=dry_run,
        activate=activate,
        event_log=EventLog(LOG_PATH),
    )
    console.print(format_pm_backend_preflight_result(result))
    if not result.succeeded:
        raise typer.Exit(1)


@app.command("list-gui-apps")
def list_gui_apps() -> None:
    apps = discover_gui_apps()
    table = Table(title="GUI Apps")
    table.add_column("name")
    table.add_column("path")
    for path in apps:
        table.add_row(path.stem, str(path))
    console.print(table)
    console.print("Scanned /Applications and ~/Applications only.")


@app.command("diagnose-gui-app")
def diagnose_gui_app(
    app_path: Path = typer.Option(..., "--app-path"),
    activate: bool = typer.Option(False, "--activate"),
) -> None:
    diagnostic = diagnose_app_bundle(app_path, activate=activate)
    console.print(format_app_diagnostic(diagnostic))
    console.print("No paste, submit, Enter/Return, GitHub, or Gmail action was attempted.")
    if activate and diagnostic.activation_result and not diagnostic.activation_result.succeeded:
        raise typer.Exit(1)


@app.command("diagnose-gui-apps")
def diagnose_gui_apps(activate: bool = typer.Option(False, "--activate")) -> None:
    targets = load_gui_targets(CONFIG_DIR)
    discovered_apps = discover_gui_apps()
    selected = [
        ("PM assistant", targets.pm_assistant),
        ("Local coding agent", targets.local_agent),
    ]
    any_failed = False
    for label, target in selected:
        console.rule(label)
        target_app_path = app_path_for_target(target, discovered_apps)
        if target_app_path is None:
            console.print(f"[yellow]No app path found for configured app: {target.app_name}[/yellow]")
            continue
        diagnostic = diagnose_app_bundle(target_app_path, activate=activate)
        console.print(format_app_diagnostic(diagnostic))
        any_failed = any_failed or bool(
            activate and diagnostic.activation_result and not diagnostic.activation_result.succeeded
        )
    console.print("No paste, submit, Enter/Return, GitHub, or Gmail action was attempted.")
    if any_failed:
        raise typer.Exit(1)


@app.command("diagnose-codex-ui")
def diagnose_codex_ui() -> None:
    ensure_workspace()
    targets = load_gui_targets(CONFIG_DIR)
    diagnostic = CodexUIDetector().diagnose(targets.local_agent)
    console.print(format_codex_ui_diagnostic(diagnostic))
    EventLog(LOG_PATH).append(
        "codex_ui_diagnostic_run",
        active_app=diagnostic.active_app,
        codex_app_active=diagnostic.codex_app_active,
        input_field_detectable=diagnostic.input_field_detectable,
        input_candidate_count=diagnostic.input_candidate_count,
        selected_input_candidate_summary=diagnostic.selected_input_candidate_summary,
        conversation_elements_detectable=diagnostic.conversation_elements_detectable,
        running_state_detected=diagnostic.running_state_detected,
        accessibility_available=diagnostic.accessibility_available,
    )


@app.command("dump-codex-ui-tree")
def dump_codex_ui_tree() -> None:
    ensure_workspace()
    targets = load_gui_targets(CONFIG_DIR)
    detector = CodexUIDetector()
    dump = detector.write_ui_tree_dump(targets.local_agent, logs_dir=WORKSPACE / "logs")
    EventLog(LOG_PATH).append(
        "codex_ui_tree_dumped",
        json_path=str(WORKSPACE / "logs" / "codex_ui_tree.json"),
        text_path=str(WORKSPACE / "logs" / "codex_ui_tree.txt"),
        element_count=len(dump.elements),
        accessibility_available=dump.accessibility_available,
    )
    console.print(format_codex_ui_tree_dump(dump))
    console.print("")
    console.print(f"JSON written: {WORKSPACE / 'logs' / 'codex_ui_tree.json'}")
    console.print(f"Text written: {WORKSPACE / 'logs' / 'codex_ui_tree.txt'}")
    console.print("No paste, submit, Enter/Return, GitHub, or Gmail action was attempted.")


@app.command("diagnose-codex-windows")
def diagnose_codex_windows() -> None:
    ensure_workspace()
    targets = load_gui_targets(CONFIG_DIR)
    MacOSAppActivator().activate(
        targets.local_agent.app_name,
        app_path=targets.local_agent.app_path,
        bundle_id=targets.local_agent.bundle_id,
    )
    detector = CodexUIDetector()
    result = detector.select_main_window(targets.local_agent)
    EventLog(LOG_PATH).append(
        "codex_window_selection_diagnostic_run",
        target_app=result.target_app,
        window_count=len(result.windows),
        selected_bounds=result.selected_bounds,
        plausible=result.plausible,
        error=result.error,
        rejected_windows=[
            {
                "index": window.index,
                "title": window.title,
                "bounds": window.bounds,
                "rejection_reasons": window.rejection_reasons,
            }
            for window in result.windows
            if window.rejected
        ],
    )
    console.print(format_codex_window_selection(result))
    if not result.plausible:
        raise typer.Exit(1)


@app.command("diagnose-chatgpt-mac-windows")
def diagnose_chatgpt_mac_windows() -> None:
    ensure_workspace()
    targets = load_gui_targets(CONFIG_DIR)
    target = ensure_native_chatgpt_mac_target(targets.pm_assistant)
    if not is_chatgpt_mac_visual_target(target):
        console.print("[red]Configured PM assistant is not chatgpt_mac_visual.[/red]")
        raise typer.Exit(1)
    MacOSAppActivator().activate(
        target.app_name,
        app_path=target.app_path,
        bundle_id=target.bundle_id,
    )
    detector = CodexUIDetector()
    result = detector.select_main_window(target)
    EventLog(LOG_PATH).append(
        "chatgpt_mac_window_selection_diagnostic_run",
        target_app=result.target_app,
        window_count=len(result.windows),
        selected_bounds=result.selected_bounds,
        plausible=result.plausible,
        error=result.error,
        rejected_windows=[
            {
                "index": window.index,
                "title": window.title,
                "bounds": window.bounds,
                "rejection_reasons": window.rejection_reasons,
            }
            for window in result.windows
            if window.rejected
        ],
    )
    console.print(format_codex_window_selection(result))
    if not result.plausible:
        console.print("[red]ChatGPT Mac visible conversation window is unavailable.[/red]")
        raise typer.Exit(1)


@app.command("diagnose-chatgpt-chrome-app-windows")
def diagnose_chatgpt_chrome_app_windows() -> None:
    ensure_workspace()
    target = _resolve_pm_profile_target(CHATGPT_CHROME_APP_PROFILE)
    try:
        MacOSAppActivator().activate(
            target.app_name,
            app_path=target.app_path,
            bundle_id=target.bundle_id,
        )
    except AppActivationError as error:
        console.print(f"[red]Activation failed:[/red] {error}")
        raise typer.Exit(1) from error
    detector = CodexUIDetector()
    result = detector.select_main_window(target)
    EventLog(LOG_PATH).append(
        "chatgpt_chrome_app_window_selection_diagnostic_run",
        target_app=result.target_app,
        target_bundle_id=target.bundle_id,
        window_count=len(result.windows),
        selected_bounds=result.selected_bounds,
        plausible=result.plausible,
        error=result.error,
        rejected_windows=[
            {
                "index": window.index,
                "title": window.title,
                "bounds": window.bounds,
                "rejection_reasons": window.rejection_reasons,
            }
            for window in result.windows
            if window.rejected
        ],
    )
    console.print(format_codex_window_selection(result))
    if not result.plausible:
        console.print("[red]ChatGPT Chrome app visible conversation window is unavailable.[/red]")
        raise typer.Exit(1)


@app.command("diagnose-chatgpt-app-targets")
def diagnose_chatgpt_app_targets(
    pm_target: PMTargetProfile | None = typer.Option(
        None,
        "--pm-target",
        help="PM target profile: chatgpt_mac or chatgpt_chrome_app.",
    ),
) -> None:
    ensure_workspace()
    configured = load_gui_targets(CONFIG_DIR).pm_assistant
    profile = _configured_pm_profile(configured, pm_target)
    target = pm_target_for_profile(configured, profile)
    result = run_chatgpt_app_target_diagnostic(target=target, profile=profile)
    EventLog(LOG_PATH).append(
        "chatgpt_app_target_diagnostic_run",
        selected_profile=result.selected_profile,
        expected_bundle_id=result.expected_bundle_id,
        configured_bundle_id=result.configured_bundle_id,
        selected_bundle_id=result.selected_bundle_id,
        native_available=result.native_available,
        chrome_app_available=result.chrome_app_available,
        chrome_pwa_candidates_rejected=result.chrome_pwa_candidates_rejected,
        native_candidates_rejected=result.native_candidates_rejected,
        candidate_count=len(result.candidates),
        error=result.error,
    )
    console.print(format_chatgpt_app_target_diagnostic(result))
    if profile == CHATGPT_MAC_PROFILE and result.selected_bundle_id != "com.openai.chat":
        raise typer.Exit(1)
    if profile == CHATGPT_CHROME_APP_PROFILE and not result.chrome_app_available:
        raise typer.Exit(1)


@app.command("set-app-window-bounds")
def set_app_window_bounds(
    app_name: str = typer.Option(..., "--app", help="Only chatgpt_chrome_app is supported."),
    bounds: str = typer.Option(..., "--bounds", help="Window bounds as x,y,width,height."),
) -> None:
    ensure_workspace()
    normalized = app_name.strip().lower().replace("-", "_")
    if normalized != CHATGPT_CHROME_APP_PROFILE:
        console.print("[red]--app must be chatgpt_chrome_app for this bounded helper.[/red]")
        raise typer.Exit(2)
    requested_bounds = _parse_bounds_option(bounds)
    target = _resolve_pm_profile_target(CHATGPT_CHROME_APP_PROFILE)
    try:
        MacOSAppActivator().activate(
            target.app_name,
            app_path=target.app_path,
            bundle_id=target.bundle_id,
        )
    except AppActivationError as error:
        console.print(f"[red]Activation failed:[/red] {error}")
        raise typer.Exit(1) from error
    result = run_set_app_window_bounds(target=target, bounds=requested_bounds)
    window_selection = CodexUIDetector().select_main_window(target)
    EventLog(LOG_PATH).append(
        "app_window_bounds_set",
        app=normalized,
        target_app=target.app_name,
        target_bundle_id=target.bundle_id,
        requested_bounds=requested_bounds,
        before_bounds=result.before_bounds,
        after_bounds=result.after_bounds,
        reenumerated_bounds=window_selection.selected_bounds,
        succeeded=result.succeeded,
        error=result.error,
    )
    console.print(format_app_window_bounds_result(result))
    console.print("")
    console.print("## Re-enumerated Window Selection")
    console.print(format_codex_window_selection(window_selection))
    if not result.succeeded:
        raise typer.Exit(1)


@app.command("resize-chatgpt-chrome-app-window")
def resize_chatgpt_chrome_app_window(
    bounds: str = typer.Option(..., "--bounds", help="Window bounds as x,y,width,height."),
) -> None:
    set_app_window_bounds(app_name=CHATGPT_CHROME_APP_PROFILE, bounds=bounds)


@app.command("preflight-chatgpt-mac-native-target")
def preflight_chatgpt_mac_native_target() -> None:
    ensure_workspace()
    target = ensure_native_chatgpt_mac_target(load_gui_targets(CONFIG_DIR).pm_assistant)
    if not is_chatgpt_mac_visual_target(target):
        console.print("[red]Configured PM assistant is not chatgpt_mac_visual.[/red]")
        raise typer.Exit(1)
    result = run_chatgpt_mac_native_preflight(target=target)
    EventLog(LOG_PATH).append(
        "chatgpt_mac_native_target_preflight_run",
        target_app=result.target.app_name,
        target_bundle_id=result.target.bundle_id,
        activation_method=result.activation_method,
        selected_bundle_id=result.selected_native_bundle_id,
        succeeded=result.succeeded,
        error=result.error,
    )
    console.print(format_chatgpt_native_preflight(result))
    if not result.succeeded:
        raise typer.Exit(1)


@app.command("diagnose-visual-state")
def diagnose_visual_state(
    app_name: str = typer.Option(..., "--app", help="Visual asset profile/app: chatgpt_mac, chatgpt_chrome_app, or codex."),
) -> None:
    ensure_workspace()
    target = _resolve_visual_state_target(app_name)

    log = EventLog(LOG_PATH)
    try:
        MacOSAppActivator().activate(
            target.app_name,
            app_path=target.app_path,
            bundle_id=target.bundle_id,
        )
    except AppActivationError as error:
        console.print(f"[red]Activation failed:[/red] {error}")
        raise typer.Exit(1) from error
    window_detector = CodexUIDetector()
    window_selection = window_detector.select_main_window(target)
    detector = AssetVisualStateDetector()
    profile = asset_profile_for_target(target)
    result = detector.detect(
        target=target,
        window_bounds=window_selection.selected_bounds,
        profile=profile,
        logs_dir=WORKSPACE / "logs",
        write_debug=True,
    )
    log.append(
        "asset_visual_state_diagnostic_run",
        selected_app=target.app_name,
        asset_profile=result.asset_profile,
        window_bounds=result.window_bounds,
        screenshot_captured=result.screenshot_captured,
        matched_state=result.matched_state.value,
        matched_asset_path=result.matched_asset_path,
        confidence=result.confidence,
        plus_anchor_found=result.plus_anchor_found,
        composer_click_point=result.computed_composer_click_point,
        composer_click_point_safe=result.composer_click_point_safe,
        error=result.error,
    )
    console.print(format_visual_state_detection(result))
    if not result.screenshot_captured or not result.backend_available:
        raise typer.Exit(1)


@app.command("diagnose-pm-visual-sequence")
def diagnose_pm_visual_sequence(
    pm_target: PMTargetProfile = typer.Option(
        PMTargetProfile.chatgpt_mac,
        "--pm-target",
        help="PM target profile: chatgpt_mac or chatgpt_chrome_app.",
    ),
    click_test: bool = typer.Option(
        False,
        "--click-test",
        help="Click the computed composer point if it is safe. Never pastes or submits.",
    ),
) -> None:
    ensure_workspace()
    profile, targets = _targets_for_pm_target_override(load_gui_targets(CONFIG_DIR), pm_target)
    result = run_pm_visual_sequence_diagnostic(
        target=targets.pm_assistant,
        logs_dir=WORKSPACE / "logs",
        click_test=click_test,
    )
    EventLog(LOG_PATH).append(
        "pm_visual_sequence_diagnostic_run",
        pm_target=profile,
        selected_app=result.target.app_name,
        selected_bundle_id=result.target.bundle_id,
        backend=result.backend,
        asset_profile=result.asset_profile,
        asset_directory=result.asset_directory,
        window_bounds=result.window_bounds,
        screenshot_captured=(
            result.visual_state_result.screenshot_captured
            if result.visual_state_result
            else False
        ),
        state=(
            result.visual_state_result.matched_state.value
            if result.visual_state_result
            else None
        ),
        plus_anchor_found=(
            result.visual_state_result.plus_anchor_found
            if result.visual_state_result
            else False
        ),
        composer_click_point=result.composer_click_point,
        composer_click_point_safe=result.composer_click_point_safe,
        click_test_attempted=result.click_test_attempted,
        failure_step=result.failure_step,
        error=result.error,
    )
    console.print(format_pm_visual_sequence_diagnostic(result))


@app.command("diagnose-paste-backends")
def diagnose_paste_backends(
    pm_target: PMTargetProfile = typer.Option(
        PMTargetProfile.chatgpt_mac,
        "--pm-target",
        help="PM target profile: chatgpt_mac or chatgpt_chrome_app.",
    ),
) -> None:
    ensure_workspace()
    profile, targets = _targets_for_pm_target_override(load_gui_targets(CONFIG_DIR), pm_target)
    try:
        result = run_pm_paste_backend_diagnostic(
            target=targets.pm_assistant,
            clipboard=MacOSClipboard(),
            app_activator=MacOSAppActivator(),
            event_log=EventLog(LOG_PATH),
            logs_dir=WORKSPACE / "logs",
        )
    except ClipboardError as error:
        console.print(f"[red]Paste backend diagnostic failed:[/red] {error}")
        raise typer.Exit(1) from error
    EventLog(LOG_PATH).append(
        "pm_paste_backend_diagnostic_cli_run",
        pm_target=profile,
        selected_app=result.target.app_name,
        selected_bundle_id=result.target.bundle_id,
        stable_backends=result.stable_backends,
        attempt_count=len(result.attempts),
        error=result.error,
    )
    console.print(format_paste_backend_diagnostic(result))
    if result.error:
        raise typer.Exit(1)


@app.command("diagnose-chatgpt-mac-response-capture")
def diagnose_chatgpt_mac_response_capture(
    attempt_copy: bool = typer.Option(False, "--attempt-copy"),
    expected_marker: str | None = typer.Option(None, "--expected-marker"),
) -> None:
    _run_response_capture_diagnostic(
        app_name=CHATGPT_MAC_PROFILE,
        attempt_copy=attempt_copy,
        expected_marker=expected_marker,
    )


@app.command("diagnose-chatgpt-chrome-app-response-capture")
def diagnose_chatgpt_chrome_app_response_capture(
    attempt_copy: bool = typer.Option(False, "--attempt-copy"),
    expected_marker: str | None = typer.Option(None, "--expected-marker"),
) -> None:
    _run_response_capture_diagnostic(
        app_name=CHATGPT_CHROME_APP_PROFILE,
        attempt_copy=attempt_copy,
        expected_marker=expected_marker,
    )


@app.command("diagnose-response-capture")
def diagnose_response_capture(
    app_name: str = typer.Option(..., "--app", help="Response capture app/profile: chatgpt_mac or chatgpt_chrome_app."),
    attempt_copy: bool = typer.Option(False, "--attempt-copy"),
    expected_marker: str | None = typer.Option(None, "--expected-marker"),
) -> None:
    _run_response_capture_diagnostic(
        app_name=app_name,
        attempt_copy=attempt_copy,
        expected_marker=expected_marker,
    )


@app.command("diagnose-chatgpt-mac-composer-text-state")
def diagnose_chatgpt_mac_composer_text_state() -> None:
    ensure_workspace()
    targets = load_gui_targets(CONFIG_DIR)
    target = ensure_native_chatgpt_mac_target(targets.pm_assistant)
    if not is_chatgpt_mac_visual_target(target):
        console.print("[red]Configured PM assistant is not chatgpt_mac_visual.[/red]")
        raise typer.Exit(1)
    log = EventLog(LOG_PATH)
    try:
        MacOSAppActivator().activate(
            target.app_name,
            app_path=target.app_path,
            bundle_id=target.bundle_id,
        )
    except AppActivationError as error:
        console.print(f"[red]Activation failed:[/red] {error}")
        raise typer.Exit(1) from error
    window_detector = CodexUIDetector()
    window_selection = window_detector.select_main_window(target)
    result = run_chatgpt_mac_composer_text_state_diagnostic(
        target=target,
        window_bounds=window_selection.selected_bounds,
        logs_dir=WORKSPACE / "logs",
    )
    log.append(
        "chatgpt_mac_composer_text_state_diagnostic_run",
        window_bounds=result.window_bounds,
        initial_state=result.initial_state.value if result.initial_state else None,
        after_type_state=result.after_type_state.value if result.after_type_state else None,
        after_cleanup_state=(
            result.after_cleanup_state.value if result.after_cleanup_state else None
        ),
        click_point=result.click_point,
        click_point_safe=result.click_point_safe,
        click_attempted=result.click_attempted,
        typed_marker_attempted=result.typed_marker_attempted,
        cleanup_attempted=result.cleanup_attempted,
        cleanup_succeeded=result.cleanup_succeeded,
        composer_has_text_detected=result.composer_has_text_detected,
        submit_attempted=result.submit_attempted,
        enter_or_return_pressed=result.enter_or_return_pressed,
        error=result.error,
    )
    console.print(format_chatgpt_mac_composer_text_state(result))
    if result.error:
        raise typer.Exit(1)


@app.command("diagnose-codex-input-target")
def diagnose_codex_input_target(
    show_click_target: bool = typer.Option(False, "--show-click-target"),
    visual_debug: bool = typer.Option(False, "--visual-debug"),
    direct_plus_anchor_preview: bool = typer.Option(False, "--direct-plus-anchor-preview"),
    click_test: bool = typer.Option(False, "--click-test"),
    paste_test: bool = typer.Option(False, "--paste-test"),
    focus_target_test: bool = typer.Option(False, "--focus-target-test"),
    click_backend: str | None = typer.Option(None, "--click-backend"),
    paste_backend: str | None = typer.Option(None, "--paste-backend"),
) -> None:
    ensure_workspace()
    targets = load_gui_targets(CONFIG_DIR)
    detector = CodexUIDetector()
    log = EventLog(LOG_PATH)
    selected_live_diagnostics = sum(1 for value in (click_test, paste_test, focus_target_test) if value)
    if selected_live_diagnostics > 1:
        console.print("[red]Choose only one of --click-test, --paste-test, or --focus-target-test.[/red]")
        raise typer.Exit(1)
    if focus_target_test:
        MacOSAppActivator().activate(
            targets.local_agent.app_name,
            app_path=targets.local_agent.app_path,
            bundle_id=targets.local_agent.bundle_id,
        )
        result = detector.run_focus_target_test(
            targets.local_agent,
            logs_dir=WORKSPACE / "logs",
            visual_debug=True,
            click_backend=click_backend,
            event_callback=lambda event, metadata: log.append(event, **metadata),
        )
        console.print(format_codex_focus_target_comparison(result))
        if result.error:
            raise typer.Exit(1)
        return
    if paste_test:
        MacOSAppActivator().activate(
            targets.local_agent.app_name,
            app_path=targets.local_agent.app_path,
            bundle_id=targets.local_agent.bundle_id,
        )
        try:
            result = detector.run_paste_test(
                targets.local_agent,
                clipboard=MacOSClipboard(),
                logs_dir=WORKSPACE / "logs",
                visual_debug=True,
                click_backend=click_backend,
                paste_backend=paste_backend,
                event_callback=lambda event, metadata: log.append(event, **metadata),
            )
        except ClipboardError as error:
            console.print(f"[red]Paste-test failed:[/red] {error}")
            raise typer.Exit(1) from error
        console.print(format_codex_paste_test_result(result))
        if result.error:
            raise typer.Exit(1)
        return
    if click_test:
        MacOSAppActivator().activate(
            targets.local_agent.app_name,
            app_path=targets.local_agent.app_path,
            bundle_id=targets.local_agent.bundle_id,
        )
        if (
            targets.local_agent.focus_strategy == "direct_plus_anchor"
            and targets.local_agent.direct_plus_anchor_enabled
        ):
            visual_result = detector.click_direct_plus_anchor(
                targets.local_agent,
                logs_dir=WORKSPACE / "logs",
                visual_debug=True,
                click_backend=click_backend,
            )
        else:
            visual_result = detector.click_visual_input(
                targets.local_agent,
                logs_dir=WORKSPACE / "logs",
                visual_debug=True,
                click_backend=click_backend,
            )
        result = (
            visual_result
            if visual_result.fallback_click_point
            or targets.local_agent.input_focus_strategy != "window_relative_click"
            else detector.click_window_relative_input(targets.local_agent)
        )
        log.append("codex_input_fallback_click_attempted", **result.__dict__)
        if result.error:
            console.print(f"[red]Click-test failed:[/red] {result.error}")
        else:
            console.print("[green]Click-test attempted.[/green]")
        console.print(f"Window bounds: {result.window_bounds or 'unknown'}")
        console.print(f"Click point: {result.fallback_click_point or 'unavailable'}")
        console.print(f"Click backend: {result.click_backend}")
        console.print(
            "PyAutoGUI available: "
            + (
                "yes"
                if result.pyautogui_available is True
                else ("no" if result.pyautogui_available is False else "unknown")
            )
        )
        console.print(f"Codex frontmost after click: {'yes' if result.app_frontmost else 'no'}")
        console.print(f"Focused element after click: {result.focused_element_summary}")
    else:
        MacOSAppActivator().activate(
            targets.local_agent.app_name,
            app_path=targets.local_agent.app_path,
            bundle_id=targets.local_agent.bundle_id,
        )
        diagnostic = detector.diagnose_input_target(
            targets.local_agent,
            logs_dir=WORKSPACE / "logs",
            visual_debug=visual_debug,
        )
        if show_click_target or visual_debug or direct_plus_anchor_preview:
            log.append(
                "codex_input_fallback_click_previewed",
                window_bounds=diagnostic.window_bounds,
                fallback_click_point=diagnostic.visual_click_point
                or diagnostic.fallback_click_point,
                fallback_strategy=diagnostic.visual_selected_strategy
                or diagnostic.fallback_strategy,
                visual_debug=visual_debug,
                visual_plus_button_found=diagnostic.visual_plus_button_found,
                visual_placeholder_found=diagnostic.visual_placeholder_found,
                visual_click_point_safe=diagnostic.visual_click_point_safe,
                direct_plus_anchor_click_point=diagnostic.direct_plus_anchor_click_point,
                direct_plus_anchor_click_point_safe=(
                    diagnostic.direct_plus_anchor_click_point_safe
                ),
            )
        console.print(format_codex_input_target_diagnostic(diagnostic))
        if show_click_target or visual_debug or direct_plus_anchor_preview:
            console.print("")
            console.print("Click preview only. No click, paste, submit, Enter/Return, GitHub, or Gmail action was attempted.")


@app.command("diagnose-macos-permissions")
def diagnose_macos_permissions() -> None:
    ensure_workspace()
    diagnostic = run_macos_permission_diagnostic()
    console.print(format_macos_permission_diagnostic(diagnostic))
    EventLog(LOG_PATH).append(
        "macos_permissions_diagnostic_run",
        running_under_codex_context=diagnostic.running_under_codex_context,
        running_under_terminal_context=diagnostic.running_under_terminal_context,
        terminal_context_process=diagnostic.terminal_context_process,
        osascript_path=diagnostic.osascript_path,
        system_events_name_probe_succeeded=diagnostic.system_events_name_probe.succeeded,
        frontmost_process_probe_succeeded=diagnostic.frontmost_process_probe.succeeded,
        non_click_ui_probe_succeeded=diagnostic.non_click_ui_probe.succeeded,
        accessibility_denied=(
            diagnostic.system_events_name_probe.accessibility_denied
            or diagnostic.frontmost_process_probe.accessibility_denied
            or diagnostic.non_click_ui_probe.accessibility_denied
        ),
        likely_permission_target=diagnostic.likely_permission_target,
    )


@app.command("preflight-external-runner")
def preflight_external_gui_runner() -> None:
    targets = load_gui_targets(CONFIG_DIR)
    preflight = preflight_external_runner(
        pm_target=targets.pm_assistant,
        local_agent_target=targets.local_agent,
    )
    console.print(format_external_runner_preflight(preflight))


@app.command("preflight-iterm-ghost-runner")
def preflight_iterm_ghost_runner() -> None:
    ensure_workspace()
    targets = load_gui_targets(CONFIG_DIR)
    permission_diagnostic = run_macos_permission_diagnostic()
    context = evaluate_iterm_ghost_runner_context(permission_diagnostic)
    console.print(format_macos_permission_diagnostic(permission_diagnostic))
    console.print("")
    console.print(format_iterm_ghost_runner_context(context))
    EventLog(LOG_PATH).append(
        "iterm_ghost_runner_preflight_context_checked",
        allowed=context.allowed,
        reason=context.reason,
        warning=context.warning,
        running_under_codex_context=permission_diagnostic.running_under_codex_context,
        running_under_terminal_context=permission_diagnostic.running_under_terminal_context,
        terminal_context_process=permission_diagnostic.terminal_context_process,
    )
    if not context.allowed:
        console.print("[red]This preflight must be run from iTerm/Terminal for the ghost runner path.[/red]")
        raise typer.Exit(1)

    preflight = preflight_external_runner(
        pm_target=targets.pm_assistant,
        local_agent_target=targets.local_agent,
    )
    console.print("")
    console.print(format_external_runner_preflight(preflight))
    if preflight.restricted_codex_sandbox:
        raise typer.Exit(1)
    if not preflight.clipboard_tools_available or not preflight.apps_resolve:
        raise typer.Exit(1)

    pm_backend_result = preflight_pm_backend(
        target=targets.pm_assistant,
        activate=True,
        event_log=EventLog(LOG_PATH),
    )
    console.print("")
    console.print(format_pm_backend_preflight_result(pm_backend_result))
    if not pm_backend_result.succeeded:
        raise typer.Exit(1)

    activator = MacOSAppActivator()
    local_activation = activator.activate_with_result(
        targets.local_agent.app_name,
        app_path=targets.local_agent.app_path,
        bundle_id=targets.local_agent.bundle_id,
    )
    console.print("")
    console.print(format_activation_result(local_activation))
    if not local_activation.succeeded:
        raise typer.Exit(1)

    codex_diagnostic = CodexUIDetector().diagnose_input_target(
        targets.local_agent,
        logs_dir=WORKSPACE / "logs",
        visual_debug=False,
    )
    console.print("")
    console.print(format_codex_input_target_diagnostic(codex_diagnostic))
    if not codex_diagnostic.visual_plus_button_found:
        raise typer.Exit(1)

    console.print("[green]iTerm ghost runner preflight passed.[/green]")
    console.print("No paste, submit, Enter/Return, GitHub, or Gmail action was attempted.")


@app.command("preflight-report-roundtrip")
def preflight_report_roundtrip() -> None:
    ensure_workspace()
    targets = load_gui_targets(CONFIG_DIR)
    preflight = preflight_external_runner(
        pm_target=targets.pm_assistant,
        local_agent_target=targets.local_agent,
    )
    console.print(format_external_runner_preflight(preflight))
    if preflight.restricted_codex_sandbox:
        console.print("[red]Restricted Codex sandbox detected; GUI roundtrip is blocked.[/red]")
        raise typer.Exit(1)
    if not preflight.clipboard_tools_available:
        console.print("[red]Clipboard tools are missing; GUI roundtrip is blocked.[/red]")
        raise typer.Exit(1)

    if targets.pm_assistant.require_backend_preflight:
        pm_backend_result = preflight_pm_backend(
            target=targets.pm_assistant,
            activate=True,
            event_log=EventLog(LOG_PATH),
        )
        console.print(format_pm_backend_preflight_result(pm_backend_result))
        if not pm_backend_result.succeeded:
            raise typer.Exit(1)
    else:
        console.print(
            "[yellow]PM backend DOM preflight skipped; visual asset diagnostics are used for this target.[/yellow]"
        )

    activator = MacOSAppActivator()
    local_activation = activator.activate_with_result(
        targets.local_agent.app_name,
        app_path=targets.local_agent.app_path,
        bundle_id=targets.local_agent.bundle_id,
    )
    console.print(format_activation_result(local_activation))
    if not local_activation.succeeded:
        raise typer.Exit(1)

    report_path = WORKSPACE / "reports" / "latest_agent_report.md"
    if not report_path.exists():
        console.print(f"[red]Missing latest report: {report_path}[/red]")
        raise typer.Exit(1)
    pm_prompt = build_report_roundtrip_pm_prompt(report_path.read_text(encoding="utf-8"))
    safety_decision = SafetyGate().check_text(pm_prompt)
    if not safety_decision.allowed:
        console.print("[red]SafetyGate blocked the PM prompt; GUI roundtrip is blocked.[/red]")
        console.print(f"Matched keywords: {', '.join(safety_decision.matched_keywords)}")
        raise typer.Exit(1)

    console.print("[green]Full Access report roundtrip preflight passed.[/green]")
    console.print("No paste, submit, Enter/Return, GitHub, or Gmail action was attempted.")


@app.command("prepare-computer-use-terminal-trigger")
def prepare_computer_use_terminal_trigger() -> None:
    ensure_workspace()
    trigger = write_computer_use_terminal_trigger(
        repo_root=ROOT,
        workspace_dir=WORKSPACE,
        event_log=EventLog(LOG_PATH),
    )
    console.print(f"[green]Computer Use terminal trigger written:[/green] {trigger.path}")
    console.print(trigger.command)


@app.command("show-computer-use-terminal-trigger")
def show_computer_use_terminal_trigger() -> None:
    ensure_workspace()
    trigger_path = WORKSPACE / "outbox" / "computer_use_terminal_trigger.md"
    if trigger_path.exists():
        content = trigger_path.read_text(encoding="utf-8")
    else:
        content = build_computer_use_terminal_trigger(ROOT, WORKSPACE).content
    EventLog(LOG_PATH).append("computer_use_trigger_previewed", trigger_path=str(trigger_path))
    console.print(content)


@app.command("verify-roundtrip-result")
def verify_roundtrip_result() -> None:
    ensure_workspace()
    log = EventLog(LOG_PATH)
    log.append("roundtrip_verification_started")
    result = verify_roundtrip_artifacts(WORKSPACE)
    console.print(format_roundtrip_verification(result))
    if result.success:
        log.append("roundtrip_verification_succeeded")
    else:
        log.append("roundtrip_verification_failed", failure_point=result.failure_point)


@app.command("run-external-gui-runner")
def run_external_gui_runner(
    auto_confirm: bool = typer.Option(False, "--auto-confirm"),
    watch_reports: bool = typer.Option(False, "--watch-reports"),
    watch_queue: bool = typer.Option(False, "--watch-queue"),
    polling_interval_seconds: float = typer.Option(3, "--polling-interval-seconds"),
    max_runtime_seconds: int = typer.Option(3600, "--max-runtime-seconds"),
    debounce_seconds: float = typer.Option(1, "--debounce-seconds"),
    cooldown_seconds: float = typer.Option(5, "--cooldown-seconds"),
    stale_lock_seconds: float = typer.Option(1800, "--stale-lock-seconds"),
    pm_response_timeout_seconds: int = typer.Option(45, "--pm-response-timeout-seconds"),
) -> None:
    ensure_workspace()
    if not auto_confirm:
        console.print("[red]run-external-gui-runner requires --auto-confirm.[/red]")
        console.print("Default GUI handoff remains manual-confirmation based.")
        raise typer.Exit(1)
    targets = load_gui_targets(CONFIG_DIR)
    preflight = preflight_external_runner(
        pm_target=targets.pm_assistant,
        local_agent_target=targets.local_agent,
    )
    console.print(format_external_runner_preflight(preflight))
    if preflight.restricted_codex_sandbox:
        console.print("[red]Restricted Codex sandbox detected; external GUI runner is blocked.[/red]")
        raise typer.Exit(1)
    if not preflight.clipboard_tools_available:
        console.print("[red]Clipboard tools are missing; external GUI runner is blocked.[/red]")
        raise typer.Exit(1)
    if not preflight.apps_resolve:
        console.print("[red]Configured GUI apps did not resolve; external GUI runner is blocked.[/red]")
        raise typer.Exit(1)

    try:
        result = ExternalGuiRunner(
            ExternalGuiRunnerConfig(
                workspace_dir=WORKSPACE,
                template_dir=TEMPLATE_DIR,
                targets=targets,
                auto_confirm=auto_confirm,
                watch_reports=watch_reports,
                watch_queue=watch_queue,
                polling_interval_seconds=polling_interval_seconds,
                max_runtime_seconds=max_runtime_seconds,
                debounce_seconds=debounce_seconds,
                cooldown_seconds=cooldown_seconds,
                stale_lock_seconds=stale_lock_seconds,
                pm_response_timeout_seconds=pm_response_timeout_seconds,
            ),
            event_log=EventLog(LOG_PATH),
            state_store=StateStore(STATE_PATH),
        ).run()
    except ExternalGuiRunnerError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    console.print(f"[green]External GUI runner stopped:[/green] {result.reason}")
    console.print(f"Triggers detected: {result.triggers_detected}")
    console.print(f"Roundtrips started: {result.roundtrips_started}")
    console.print(f"Roundtrips completed: {result.roundtrips_completed}")
    if result.safety_paused:
        console.print("[red]Safety pause is active.[/red]")
    if result.failures:
        console.print(f"[yellow]Failures: {len(result.failures)}[/yellow]")


@app.command("run-iterm-ghost-runner")
def run_iterm_ghost_runner(
    auto_confirm: bool = typer.Option(False, "--auto-confirm"),
    watch_report: bool = typer.Option(False, "--watch-report"),
    polling_interval_seconds: float = typer.Option(3, "--polling-interval-seconds"),
    max_runtime_seconds: int = typer.Option(3600, "--max-runtime-seconds"),
    max_roundtrips: int = typer.Option(1, "--max-roundtrips"),
    debounce_seconds: float = typer.Option(1, "--debounce-seconds"),
    cooldown_seconds: float = typer.Option(5, "--cooldown-seconds"),
    stale_lock_seconds: float = typer.Option(1800, "--stale-lock-seconds"),
    pm_response_timeout_seconds: int = typer.Option(45, "--pm-response-timeout-seconds"),
) -> None:
    ensure_workspace()
    if not auto_confirm:
        console.print("[red]run-iterm-ghost-runner requires --auto-confirm.[/red]")
        console.print("Default GUI handoff remains manual-confirmation based.")
        raise typer.Exit(1)
    if max_roundtrips <= 0:
        console.print("[red]--max-roundtrips must be greater than zero.[/red]")
        raise typer.Exit(1)
    targets = load_gui_targets(CONFIG_DIR)
    permission_diagnostic = run_macos_permission_diagnostic()
    context = evaluate_iterm_ghost_runner_context(permission_diagnostic)
    console.print(format_iterm_ghost_runner_context(context))
    console.print("")
    console.print("Parent process chain:")
    for process in permission_diagnostic.parent_process_chain:
        console.print(
            f"- pid={process.pid} ppid={process.ppid if process.ppid is not None else 'unknown'} command={process.command}"
        )
    EventLog(LOG_PATH).append(
        "iterm_ghost_runner_context_checked",
        allowed=context.allowed,
        reason=context.reason,
        warning=context.warning,
        running_under_codex_context=permission_diagnostic.running_under_codex_context,
        running_under_terminal_context=permission_diagnostic.running_under_terminal_context,
        terminal_context_process=permission_diagnostic.terminal_context_process,
    )
    if not context.allowed:
        console.print("[red]This runner must be launched from iTerm/Terminal.[/red]")
        raise typer.Exit(1)

    preflight = preflight_external_runner(
        pm_target=targets.pm_assistant,
        local_agent_target=targets.local_agent,
    )
    console.print("")
    console.print(format_external_runner_preflight(preflight))
    if preflight.restricted_codex_sandbox:
        console.print("[red]Restricted Codex sandbox detected; ghost runner is blocked.[/red]")
        raise typer.Exit(1)
    if not preflight.clipboard_tools_available:
        console.print("[red]Clipboard tools are missing; ghost runner is blocked.[/red]")
        raise typer.Exit(1)
    if not preflight.apps_resolve:
        console.print("[red]Configured GUI apps did not resolve; ghost runner is blocked.[/red]")
        raise typer.Exit(1)

    console.print("")
    console.print(f"Watching report: {WORKSPACE / 'reports' / 'latest_agent_report.md'}")
    console.print(f"Max runtime seconds: {max_runtime_seconds}")
    console.print(f"Max roundtrips: {max_roundtrips}")
    try:
        result = ExternalGuiRunner(
            ExternalGuiRunnerConfig(
                workspace_dir=WORKSPACE,
                template_dir=TEMPLATE_DIR,
                targets=targets,
                auto_confirm=auto_confirm,
                watch_reports=watch_report,
                watch_queue=False,
                polling_interval_seconds=polling_interval_seconds,
                max_runtime_seconds=max_runtime_seconds,
                debounce_seconds=debounce_seconds,
                cooldown_seconds=cooldown_seconds,
                stale_lock_seconds=stale_lock_seconds,
                pm_response_timeout_seconds=pm_response_timeout_seconds,
                max_roundtrips=max_roundtrips,
                lock_file_name="ghost_runner.lock",
                use_report_hash_guard=True,
                last_processed_report_hash_path=WORKSPACE
                / "state"
                / "last_processed_report_hash",
            ),
            event_log=EventLog(LOG_PATH),
            state_store=StateStore(STATE_PATH),
        ).run()
    except ExternalGuiRunnerError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    console.print(f"[green]iTerm ghost runner stopped:[/green] {result.reason}")
    console.print(f"Triggers detected: {result.triggers_detected}")
    console.print(f"Roundtrips started: {result.roundtrips_started}")
    console.print(f"Roundtrips completed: {result.roundtrips_completed}")
    if result.safety_paused:
        console.print("[red]Safety pause is active.[/red]")
    if result.failures:
        console.print(f"[yellow]Failures: {len(result.failures)}[/yellow]")


@app.command("stage-pm-prompt")
def stage_pm_prompt(
    dry_run: bool = typer.Option(False, "--dry-run"),
    copy_to_clipboard: bool = typer.Option(False, "--copy-to-clipboard"),
) -> None:
    ensure_workspace()
    targets = load_gui_targets(CONFIG_DIR)
    try:
        result = stage_pm_prompt_bridge(
            workspace_dir=WORKSPACE,
            template_dir=TEMPLATE_DIR,
            dry_run=dry_run,
            copy_to_clipboard=copy_to_clipboard,
            clipboard=None if dry_run else MacOSClipboard(),
            confirmation=ManualConfirmation(lambda message: typer.confirm(message, default=False)),
            event_log=EventLog(LOG_PATH),
        )
    except (ClipboardError, ValueError) as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    if result.blocked:
        console.print("[red]Staging blocked by safety gate.[/red]")
        raise typer.Exit(1)
    console.print(f"[green]PM prompt staged:[/green] {result.prompt_path}")
    console.print(format_target_guidance("PM assistant", targets.pm_assistant))
    if dry_run:
        console.print("[bold cyan]DRY RUN: clipboard copy skipped.[/bold cyan]")
    elif copy_to_clipboard:
        console.print("[green]Copied to clipboard.[/green]" if result.copied else "[yellow]Clipboard copy skipped.[/yellow]")


@app.command("stage-local-agent-prompt")
def stage_local_agent_prompt(
    dry_run: bool = typer.Option(False, "--dry-run"),
    copy_to_clipboard: bool = typer.Option(False, "--copy-to-clipboard"),
) -> None:
    ensure_workspace()
    targets = load_gui_targets(CONFIG_DIR)
    try:
        result = stage_local_agent_prompt_bridge(
            workspace_dir=WORKSPACE,
            template_dir=TEMPLATE_DIR,
            dry_run=dry_run,
            copy_to_clipboard=copy_to_clipboard,
            queue=CommandQueue(QUEUE_DIR),
            clipboard=None if dry_run else MacOSClipboard(),
            confirmation=ManualConfirmation(lambda message: typer.confirm(message, default=False)),
            event_log=EventLog(LOG_PATH),
        )
    except (ClipboardError, ValueError) as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    if result.blocked:
        console.print("[red]Staging blocked by safety gate.[/red]")
        raise typer.Exit(1)
    if not result.staged:
        console.print(f"[yellow]{result.reason}[/yellow]")
        return
    console.print(f"[green]Local-agent prompt staged:[/green] {result.prompt_path}")
    console.print(format_target_guidance("Local coding agent", targets.local_agent))
    if dry_run:
        console.print("[bold cyan]DRY RUN: clipboard copy skipped.[/bold cyan]")
    elif copy_to_clipboard:
        console.print("[green]Copied to clipboard.[/green]" if result.copied else "[yellow]Clipboard copy skipped.[/yellow]")


@app.command("run-once")
def run_once(
    dry_run: bool = True,
    fixture: Path | None = None,
    fake_pm_response: Path = Path("fixtures/fake_pm_response.md"),
) -> None:
    ensure_workspace()
    log = EventLog(LOG_PATH)
    if fixture:
        target = WORKSPACE / "reports" / "latest_agent_report.md"
        target.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
        log.append("fixture_report_loaded", fixture=str(fixture))

    report = AgentReportCollector(WORKSPACE / "reports" / "latest_agent_report.md").collect()
    pm_prompt = PromptBuilder(TEMPLATE_DIR).build_pm_report_prompt(report)
    (WORKSPACE / "outbox" / "pm_report_prompt.md").write_text(pm_prompt, encoding="utf-8")
    log.append("pm_prompt_built")

    response = fake_pm_response.read_text(encoding="utf-8") if fake_pm_response.exists() else "# Next Command\n\nRequest status report."
    response_path = WORKSPACE / "outbox" / "pm_instruction.md"
    response_path.write_text(response, encoding="utf-8")

    decision = SafetyGate().check_text(response)
    if not decision.allowed:
        SafetyGate().write_decision_request(WORKSPACE, decision, response)
        state = StateStore(STATE_PATH).load()
        state.safety_pause = True
        state.state = BridgeStateName.PAUSED_FOR_USER_DECISION
        StateStore(STATE_PATH).save(state)
        log.append("safety_pause_triggered", keywords=decision.matched_keywords)
        console.print("[red]Safety pause triggered.[/red]")
        return

    command = Command(
        id=f"cmd_{uuid4().hex[:12]}",
        type=CommandType.CHATGPT_PM_NEXT_TASK,
        source="pm_assistant",
        prompt_path=str(response_path),
        dedupe_key=f"pm_response:{hash(response)}",
    )
    CommandQueue(QUEUE_DIR).enqueue(command)
    log.append("pm_command_enqueued", command_id=command.id)
    console.print("[green]Run-once completed up to queue enqueue.[/green]")
    if dry_run:
        console.print("Run `python -m agent_bridge.cli dispatch-next --dry-run` next.")


@app.command("run-loop")
def run_loop(
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
    max_cycles: int = typer.Option(5, "--max-cycles"),
    max_runtime_seconds: int = typer.Option(3600, "--max-runtime-seconds"),
    polling_interval_seconds: float = typer.Option(30, "--polling-interval-seconds"),
    watch_reviews: bool = typer.Option(False, "--watch-reviews"),
    watch_ci: bool = typer.Option(False, "--watch-ci"),
    owner: str | None = typer.Option(None, "--owner"),
    repo: str | None = typer.Option(None, "--repo"),
    pr: int | None = typer.Option(None, "--pr"),
    dispatch: bool = typer.Option(True, "--dispatch/--no-dispatch"),
) -> None:
    ensure_workspace()
    if not dry_run:
        console.print("[yellow]Real GUI dispatch is not implemented; dispatcher remains dry-run only.[/yellow]")
    try:
        result = RunLoop(
            RunLoopConfig(
                workspace_dir=WORKSPACE,
                template_dir=TEMPLATE_DIR,
                dry_run=dry_run,
                max_cycles=max_cycles,
                max_runtime_seconds=max_runtime_seconds,
                polling_interval_seconds=polling_interval_seconds,
                watch_reviews=watch_reviews,
                watch_ci=watch_ci,
                owner=owner,
                repo=repo,
                pr_number=pr,
                dispatch=dispatch,
            ),
            queue=CommandQueue(QUEUE_DIR),
            event_log=EventLog(LOG_PATH),
            state_store=StateStore(STATE_PATH),
            console=console,
        ).run()
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    console.print(f"[green]Run-loop stopped:[/green] {result.reason}")
    console.print(f"Cycles completed: {result.cycles_completed}")
    console.print(f"Dry-run dispatches: {result.dispatched_count}")
    if result.safety_paused:
        console.print("[red]Safety pause is active.[/red]")


@app.command("ingest-review")
def ingest_review(
    fixture: Path = typer.Option(..., "--fixture"),
    dry_run: bool = False,
) -> None:
    ensure_workspace()
    added, command, digest_path = ingest_review_fixture(
        fixture=fixture,
        workspace_dir=WORKSPACE,
        queue=CommandQueue(QUEUE_DIR),
        event_log=EventLog(LOG_PATH),
    )
    console.print(f"[green]Review digest written:[/green] {digest_path}")
    console.print("[green]Review command enqueued.[/green]" if added else "[yellow]Duplicate ignored.[/yellow]")
    if dry_run:
        console.print("[bold cyan]DRY RUN: producer only[/bold cyan]")
        console.print("No dispatch was attempted.")
    console.print(command.model_dump_json(indent=2))


@app.command("ingest-ci")
def ingest_ci(
    fixture: Path = typer.Option(..., "--fixture"),
    dry_run: bool = False,
) -> None:
    ensure_workspace()
    added, command, digest_path = ingest_ci_fixture(
        fixture=fixture,
        workspace_dir=WORKSPACE,
        queue=CommandQueue(QUEUE_DIR),
        event_log=EventLog(LOG_PATH),
    )
    console.print(f"[green]CI digest written:[/green] {digest_path}")
    console.print("[green]CI command enqueued.[/green]" if added else "[yellow]Duplicate ignored.[/yellow]")
    if dry_run:
        console.print("[bold cyan]DRY RUN: producer only[/bold cyan]")
        console.print("No dispatch was attempted.")
    console.print(command.model_dump_json(indent=2))


@app.command("watch-reviews")
def watch_reviews(
    owner: str = typer.Option(..., "--owner"),
    repo: str = typer.Option(..., "--repo"),
    pr: int = typer.Option(..., "--pr"),
    dry_run: bool = False,
) -> None:
    ensure_workspace()
    try:
        added, command, digest_path, digest, markdown = watch_review_comments(
            owner=owner,
            repo=repo,
            pr_number=pr,
            workspace_dir=WORKSPACE,
            dry_run=dry_run,
            queue=CommandQueue(QUEUE_DIR),
            event_log=EventLog(LOG_PATH),
        )
    except GhClientError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    if dry_run:
        console.print("[bold cyan]DRY RUN: GitHub review watcher[/bold cyan]")
        console.print("No digest file was written and no queue entry was created.")
        console.print(markdown)
        return

    console.print(f"[green]Review digest written:[/green] {digest_path}")
    if command is None:
        console.print("[yellow]No likely automated review comments found; no command enqueued.[/yellow]")
    else:
        console.print("[green]Review command enqueued.[/green]" if added else "[yellow]Duplicate ignored.[/yellow]")
        console.print(command.model_dump_json(indent=2))
    console.print(f"Action items: {len(digest.action_items)}")


@app.command("watch-ci")
def watch_ci(
    owner: str = typer.Option(..., "--owner"),
    repo: str = typer.Option(..., "--repo"),
    pr: int = typer.Option(..., "--pr"),
    dry_run: bool = False,
) -> None:
    ensure_workspace()
    try:
        added, command, digest_path, digest, markdown = watch_ci_failures(
            owner=owner,
            repo=repo,
            pr_number=pr,
            workspace_dir=WORKSPACE,
            dry_run=dry_run,
            queue=CommandQueue(QUEUE_DIR),
            event_log=EventLog(LOG_PATH),
        )
    except GhClientError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    if dry_run:
        console.print("[bold cyan]DRY RUN: GitHub CI watcher[/bold cyan]")
        console.print("No digest file was written and no queue entry was created.")
        console.print(markdown)
        return

    console.print(f"[green]CI digest written:[/green] {digest_path}")
    if command is None:
        console.print("[yellow]No failed or cancelled CI checks found; no command enqueued.[/yellow]")
    else:
        console.print("[green]CI command enqueued.[/green]" if added else "[yellow]Duplicate ignored.[/yellow]")
        console.print(command.model_dump_json(indent=2))
    console.print(f"Failures: {len(digest.failures)}")


@app.command("dogfood-gh")
def dogfood_gh(
    owner: str = typer.Option(..., "--owner"),
    repo: str = typer.Option(..., "--repo"),
    pr: int = typer.Option(..., "--pr"),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
) -> None:
    ensure_workspace()
    if not dry_run:
        console.print("[red]dogfood-gh only supports --dry-run in this milestone.[/red]")
        console.print("Use watch-reviews/watch-ci directly for explicit non-dry-run queue ingestion.")
        raise typer.Exit(1)
    try:
        result = dogfood_gh_dry_run(
            owner=owner,
            repo=repo,
            pr_number=pr,
            workspace_dir=WORKSPACE,
            queue=CommandQueue(QUEUE_DIR),
            event_log=EventLog(LOG_PATH),
        )
    except (GhClientError, ValueError) as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    console.print("[bold cyan]DRY RUN: GitHub safe-PR dogfood[/bold cyan]")
    console.print("No digest file was written, no queue entry was created, and no dispatch was attempted.")
    console.print(
        f"Pending queue entries before/after: {result.queue_pending_before}/{result.queue_pending_after}"
    )
    console.print(f"Review action items: {result.review_action_items}")
    console.print(result.review_markdown)
    console.print(f"CI failures: {result.ci_failures}")
    console.print(result.ci_markdown)


@app.command("dogfood-gui-bridge")
def dogfood_gui_bridge(
    auto_confirm: bool = typer.Option(False, "--auto-confirm"),
    max_cycles: int = typer.Option(..., "--max-cycles"),
    max_runtime_seconds: int = typer.Option(..., "--max-runtime-seconds"),
    pm_response_timeout_seconds: int = typer.Option(30, "--pm-response-timeout-seconds"),
) -> None:
    ensure_workspace()
    if not auto_confirm:
        console.print("[red]dogfood-gui-bridge requires --auto-confirm for unattended GUI side effects.[/red]")
        console.print("Default local-agent handoff remains manual-confirmation based.")
        raise typer.Exit(1)
    try:
        result = run_gui_bridge_dogfood(
            config=GuiDogfoodConfig(
                workspace_dir=WORKSPACE,
                template_dir=TEMPLATE_DIR,
                targets=load_gui_targets(CONFIG_DIR),
                auto_confirm=auto_confirm,
                max_cycles=max_cycles,
                max_runtime_seconds=max_runtime_seconds,
                pm_response_timeout_seconds=pm_response_timeout_seconds,
            ),
            gui=MacOSSystemEventsGuiAdapter(),
            queue=CommandQueue(QUEUE_DIR),
            event_log=EventLog(LOG_PATH),
            state_store=StateStore(STATE_PATH),
        )
    except GuiDogfoodError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    if result.safety_paused:
        console.print("[red]GUI dogfood stopped on safety pause.[/red]")
        raise typer.Exit(1)
    console.print(f"[green]GUI dogfood stopped:[/green] {result.reason}")
    console.print(f"Cycles completed: {result.cycles_completed}")
    if result.pm_response_path:
        console.print(f"PM response saved: {result.pm_response_path}")
    if result.local_agent_prompt_path:
        console.print(f"Local-agent prompt staged: {result.local_agent_prompt_path}")


@app.command("dogfood-report-roundtrip")
def dogfood_report_roundtrip(
    auto_confirm: bool = typer.Option(False, "--auto-confirm"),
    max_cycles: int = typer.Option(1, "--max-cycles"),
    max_runtime_seconds: int = typer.Option(180, "--max-runtime-seconds"),
    pm_response_timeout_seconds: int = typer.Option(45, "--pm-response-timeout-seconds"),
    pm_target: PMTargetProfile | None = typer.Option(
        None,
        "--pm-target",
        help="PM target profile override: chatgpt_mac or chatgpt_chrome_app.",
    ),
    submit_local_agent: bool = typer.Option(
        True,
        "--submit-local-agent/--no-submit-local-agent",
        help="Submit the staged local-agent prompt after PM response extraction.",
    ),
    stop_after_local_agent_submit: bool = typer.Option(
        False,
        "--stop-after-local-agent-submit",
        help="Stop immediately after attempting the local-agent submit/queue handoff.",
    ),
    wait_for_artifact_confirmation: bool = typer.Option(
        True,
        "--artifact-confirmation-wait/--no-artifact-confirmation-wait",
        help="Wait for the no-op success report artifact after local-agent submit.",
    ),
) -> None:
    ensure_workspace()
    if not auto_confirm:
        console.print("[red]dogfood-report-roundtrip requires --auto-confirm.[/red]")
        console.print("Default local-agent handoff remains manual-confirmation based.")
        raise typer.Exit(1)
    targets = load_gui_targets(CONFIG_DIR)
    try:
        _, targets = _targets_for_pm_target_override(targets, pm_target)
    except Exception as error:
        console.print(f"[red]PM target selection failed:[/red] {error}")
        raise typer.Exit(1) from error
    try:
        result = run_report_roundtrip(
            config=ReportRoundtripConfig(
                workspace_dir=WORKSPACE,
                template_dir=TEMPLATE_DIR,
                targets=targets,
                auto_confirm=auto_confirm,
                max_cycles=max_cycles,
                max_runtime_seconds=max_runtime_seconds,
                pm_response_timeout_seconds=pm_response_timeout_seconds,
                require_pm_backend_preflight=targets.pm_assistant.require_backend_preflight,
                submit_local_agent=submit_local_agent,
                stop_after_local_agent_submit=stop_after_local_agent_submit,
                wait_for_artifact_confirmation=wait_for_artifact_confirmation,
            ),
            gui=MacOSSystemEventsGuiAdapter(),
            queue=CommandQueue(QUEUE_DIR),
            event_log=EventLog(LOG_PATH),
            state_store=StateStore(STATE_PATH),
        )
    except ReportRoundtripError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

    if result.safety_paused:
        console.print("[red]Report roundtrip stopped on safety pause.[/red]")
        raise typer.Exit(1)
    console.print(f"[green]Report roundtrip stopped:[/green] {result.reason}")
    console.print(f"Cycles completed: {result.cycles_completed}")
    if result.pm_response_path:
        console.print(f"PM response saved: {result.pm_response_path}")
    if result.extracted_prompt_path:
        console.print(f"Extracted Codex prompt saved: {result.extracted_prompt_path}")
    if result.local_agent_prompt_path:
        console.print(f"Local-agent prompt staged: {result.local_agent_prompt_path}")


@app.command("preflight-run-bridge")
def preflight_run_bridge(
    pm_target: PMTargetProfile | None = typer.Option(
        None,
        "--pm-target",
        help="PM target profile: chatgpt_mac or chatgpt_chrome_app.",
    ),
) -> None:
    ensure_workspace()
    targets = load_gui_targets(CONFIG_DIR)
    profile = _configured_pm_profile(targets.pm_assistant, pm_target)
    result = run_bridge_preflight(
        workspace_dir=WORKSPACE,
        targets=targets,
        pm_target_profile=profile,
        config_dir=CONFIG_DIR,
    )
    EventLog(LOG_PATH).append(
        "foreground_bridge_preflight_run",
        pm_target_profile=profile,
        succeeded=result.succeeded,
        pm_target_app=result.pm_target.app_name,
        pm_target_bundle_id=result.pm_target.bundle_id,
    )
    console.print(format_bridge_preflight(result))
    if not result.succeeded:
        raise typer.Exit(1)


@app.command("run-bridge")
def run_bridge(
    pm_target: PMTargetProfile | None = typer.Option(
        None,
        "--pm-target",
        help="PM target profile: chatgpt_mac or chatgpt_chrome_app.",
    ),
    watch_report: Path = typer.Option(
        Path("workspace/reports/latest_agent_report.md"),
        "--watch-report",
        help="Report file to watch for Agent Bridge content changes.",
    ),
    polling_interval_seconds: float = typer.Option(3, "--polling-interval-seconds"),
    debounce_seconds: float = typer.Option(2, "--debounce-seconds"),
    cooldown_seconds: float = typer.Option(5, "--cooldown-seconds"),
    max_runtime_seconds: int = typer.Option(
        0,
        "--max-runtime-seconds",
        help="0 means run until Ctrl-C, max roundtrips, or safety pause.",
    ),
    max_roundtrips: int = typer.Option(
        0,
        "--max-roundtrips",
        help="0 means unlimited until Ctrl-C, max runtime, or safety pause.",
    ),
    require_trigger_marker: bool = typer.Option(
        False,
        "--require-trigger-marker/--no-require-trigger-marker",
        help=(
            "Require AGENT_BRIDGE_GUI_ROUNDTRIP_TEST before triggering. "
            "By default any post-startup report content change triggers."
        ),
    ),
    process_existing_trigger: bool = typer.Option(
        False,
        "--process-existing-trigger",
        help=(
            "Process the report that already exists at runner startup if it is eligible "
            "under the current trigger policy. By default startup content is only "
            "recorded as a baseline."
        ),
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable state-machine and GUI action debug logs."),
    debug_state_machine: bool = typer.Option(
        False,
        "--debug-state-machine",
        help="Write detailed visual state-machine debug events.",
    ),
    debug_gui_actions: bool = typer.Option(
        False,
        "--debug-gui-actions",
        help="Write detailed GUI action debug events.",
    ),
    debug_screenshots: bool = typer.Option(
        False,
        "--debug-screenshots",
        help="Write bounded debug screenshots during major visual checks.",
    ),
    debug_all_template_comparisons: bool = typer.Option(
        False,
        "--debug-all-template-comparisons",
        help=(
            "Print every template comparison to the terminal. By default debug terminal "
            "output shows accepted candidates plus concise no-candidate summaries; JSONL "
            "logs still keep all comparisons."
        ),
    ),
    roundtrip_max_runtime_seconds: int = typer.Option(180, "--roundtrip-max-runtime-seconds"),
    pm_response_timeout_seconds: int = typer.Option(45, "--pm-response-timeout-seconds"),
) -> None:
    """Run the foreground bridge.

    Default trigger policy: any post-startup report content change. The startup
    baseline is recorded and ignored. Live visual diagnostics are deferred until
    a bridge attempt starts; use preflight-run-bridge for heavier no-submit checks.
    """
    ensure_workspace()
    targets = load_gui_targets(CONFIG_DIR)
    try:
        profile, startup_targets = _targets_for_pm_target_override_startup(targets, pm_target)
    except Exception as error:
        console.print(f"[red]PM target selection failed:[/red] {error}")
        raise typer.Exit(1) from error
    resolved_watch_report = _resolve_watch_report_path(watch_report)
    console.print("[bold]Agent Bridge foreground runner[/bold]")
    console.print("Stop with Ctrl-C.")
    runner = ForegroundBridgeRunner(
        ForegroundBridgeRunnerConfig(
            workspace_dir=WORKSPACE,
            template_dir=TEMPLATE_DIR,
            targets=startup_targets,
            pm_target_profile=profile,
            watch_report_path=resolved_watch_report,
            polling_interval_seconds=polling_interval_seconds,
            debounce_seconds=debounce_seconds,
            cooldown_seconds=cooldown_seconds,
            max_runtime_seconds=max_runtime_seconds,
            max_roundtrips=max_roundtrips,
            require_trigger_marker=require_trigger_marker,
            process_existing_trigger=process_existing_trigger,
            roundtrip_max_runtime_seconds=roundtrip_max_runtime_seconds,
            pm_response_timeout_seconds=pm_response_timeout_seconds,
            stop_after_local_agent_submit=True,
            wait_for_artifact_confirmation=False,
            debug=debug,
            debug_state_machine=debug_state_machine,
            debug_gui_actions=debug_gui_actions,
            debug_screenshots=debug_screenshots,
            debug_all_template_comparisons=debug_all_template_comparisons,
        ),
        output_fn=console.print,
    )
    result = runner.run()
    console.print(f"Runner stopped: {result.reason}")
    console.print(f"Roundtrips started: {result.roundtrips_started}")
    console.print(f"Roundtrips completed: {result.roundtrips_completed}")
    console.print(f"Roundtrips failed: {getattr(result, 'roundtrips_failed', 0)}")
    console.print(f"Roundtrips interrupted: {getattr(result, 'roundtrips_interrupted', 0)}")
    console.print(f"Roundtrips skipped: {getattr(result, 'roundtrips_skipped', 0)}")


@app.command("install-portable")
def install_portable(
    target: Path = typer.Option(..., "--target"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    force: bool = False,
    include_agents_snippet: bool = typer.Option(
        True,
        "--include-agents-snippet/--no-include-agents-snippet",
    ),
) -> None:
    plan = install_portable_module(
        target=target,
        dry_run=dry_run,
        force=force,
        include_agents_snippet=include_agents_snippet,
    )
    console.print(format_install_plan(plan, dry_run=dry_run))
    if plan.blocked:
        raise typer.Exit(1)


@app.command("verify-portable")
def verify_portable(target: Path = typer.Option(..., "--target")) -> None:
    result = verify_portable_target(target)
    console.print(format_verify_result(result))
    if not result.ok:
        raise typer.Exit(1)


@app.command("simulate-dogfood")
def simulate_dogfood() -> None:
    ensure_workspace()
    fake_report = Path("fixtures/fake_agent_report.md")
    if fake_report.exists():
        (WORKSPACE / "reports" / "latest_agent_report.md").write_text(fake_report.read_text(encoding="utf-8"), encoding="utf-8")
    run_once(dry_run=True, fixture=None)
    review_payload = Path("fixtures/fake_review_digest.md")
    if review_payload.exists():
        command = Command(
            id=f"cmd_{uuid4().hex[:12]}",
            type=CommandType.GITHUB_REVIEW_FIX,
            source="github_review_watcher",
            prompt_path=str(review_payload),
            dedupe_key="fake_review_digest",
        )
        CommandQueue(QUEUE_DIR).enqueue(command)
    console.print("[green]Fake review command enqueued.[/green]")
    queue_list("pending")
    console.print("[cyan]Next dispatch should prioritize review-fix over PM next-task.[/cyan]")


@app.command("pause")
def pause() -> None:
    ensure_workspace()
    store = StateStore(STATE_PATH)
    state = store.load()
    state.safety_pause = True
    store.save(state)
    console.print("[yellow]Paused.[/yellow]")


@app.command("resume")
def resume() -> None:
    ensure_workspace()
    store = StateStore(STATE_PATH)
    state = store.load()
    state.safety_pause = False
    store.save(state)
    console.print("[green]Resumed.[/green]")


@app.command("reset-state")
def reset_state() -> None:
    ensure_workspace()
    StateStore(STATE_PATH).reset()
    console.print("[green]State reset.[/green]")


if __name__ == "__main__":
    app()
