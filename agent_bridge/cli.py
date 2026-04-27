from __future__ import annotations

from enum import Enum
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
from agent_bridge.core.models import BridgeStateName, Command, CommandType
from agent_bridge.core.safety_gate import SafetyGate
from agent_bridge.core.state_store import StateStore
from agent_bridge.github.ci_watcher import ingest_ci_fixture, watch_ci_failures
from agent_bridge.github.dogfood import dogfood_gh_dry_run
from agent_bridge.github.gh_client import GhClientError
from agent_bridge.github.review_watcher import ingest_review_fixture, watch_review_comments
from agent_bridge.gui.clipboard import ClipboardError, MacOSClipboard
from agent_bridge.gui.app_diagnostics import (
    app_path_for_target,
    diagnose_app_bundle,
    format_app_diagnostic,
)
from agent_bridge.gui.external_runner import format_external_runner_preflight, preflight_external_runner
from agent_bridge.gui.gui_automation import MacOSSystemEventsGuiAdapter
from agent_bridge.gui.gui_dogfood import GuiDogfoodConfig, GuiDogfoodError, run_gui_bridge_dogfood
from agent_bridge.gui.local_agent_bridge import (
    dispatch_local_agent_prompt,
    stage_local_agent_prompt as stage_local_agent_prompt_bridge,
)
from agent_bridge.gui.macos_apps import (
    AppActivationError,
    MacOSAppActivator,
    ManualStageTarget,
    discover_gui_apps,
    format_activation_plan,
    format_activation_result,
    format_target_guidance,
    load_gui_targets,
)
from agent_bridge.gui.macos_terminal_confirmation import MacOSTerminalConfirmation
from agent_bridge.gui.manual_confirmation import ManualConfirmation
from agent_bridge.gui.pm_assistant_bridge import stage_pm_prompt as stage_pm_prompt_bridge
from agent_bridge.gui.report_roundtrip import (
    ReportRoundtripConfig,
    ReportRoundtripError,
    run_report_roundtrip,
)
from agent_bridge.orchestrator import RunLoop, RunLoopConfig
from agent_bridge.portable.installer import (
    format_install_plan,
    format_verify_result,
    install_portable as install_portable_module,
    verify_portable as verify_portable_target,
)

app = typer.Typer(help="Agent Bridge CLI")
queue_app = typer.Typer(help="Queue commands")
app.add_typer(queue_app, name="queue")
console = Console()


class ConfirmationMode(str, Enum):
    INLINE = "inline"
    TERMINAL_WINDOW = "terminal-window"


ROOT = Path.cwd()
WORKSPACE = ROOT / "workspace"
QUEUE_DIR = WORKSPACE / "queue"
STATE_PATH = WORKSPACE / "state" / "state.json"
LOG_PATH = WORKSPACE / "logs" / "bridge.jsonl"
TEMPLATE_DIR = ROOT / "agent_bridge" / "templates"
CONFIG_DIR = ROOT / "config"


def ensure_workspace() -> None:
    for sub in ["state", "queue", "inbox", "outbox", "reports", "reviews", "logs"]:
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
    command = Command(
        id=f"cmd_{uuid4().hex[:12]}",
        type=type,
        source=source,
        task_id=task_id,
        payload_path=str(payload),
        dedupe_key=dedupe_key or f"{type.value}:{payload}:{task_id or ''}",
    )
    added = CommandQueue(QUEUE_DIR).enqueue(command)
    EventLog(LOG_PATH).append("enqueue", added=added, command_id=command.id, type=type.value)
    console.print("[green]Enqueued.[/green]" if added else "[yellow]Duplicate ignored.[/yellow]")


@queue_app.command("list")
def queue_list() -> None:
    ensure_workspace()
    commands = CommandQueue(QUEUE_DIR).list_pending()
    table = Table(title="Pending Commands")
    for col in ["priority", "type", "id", "source", "payload_path", "dedupe_key"]:
        table.add_column(col)
    for c in sorted(commands, key=lambda x: (-(x.priority or 0), x.created_at)):
        table.add_row(str(c.priority), c.type.value, c.id, c.source, c.payload_path, c.dedupe_key)
    console.print(table)


@queue_app.command("pop")
def queue_pop() -> None:
    ensure_workspace()
    command = CommandQueue(QUEUE_DIR).pop_next()
    console.print("[yellow]No pending command.[/yellow]" if command is None else command.model_dump_json(indent=2))


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
        window_hint=target.window_hint,
        paste_instruction=target.paste_instruction,
    )


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


@app.command("preflight-external-runner")
def preflight_external_gui_runner() -> None:
    targets = load_gui_targets(CONFIG_DIR)
    preflight = preflight_external_runner(
        pm_target=targets.pm_assistant,
        local_agent_target=targets.local_agent,
    )
    console.print(format_external_runner_preflight(preflight))


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
        payload_path=str(response_path),
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
) -> None:
    ensure_workspace()
    if not auto_confirm:
        console.print("[red]dogfood-report-roundtrip requires --auto-confirm.[/red]")
        console.print("Default local-agent handoff remains manual-confirmation based.")
        raise typer.Exit(1)
    try:
        result = run_report_roundtrip(
            config=ReportRoundtripConfig(
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
            payload_path=str(review_payload),
            dedupe_key="fake_review_digest",
        )
        CommandQueue(QUEUE_DIR).enqueue(command)
    console.print("[green]Fake review command enqueued.[/green]")
    queue_list()
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
