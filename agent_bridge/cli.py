from __future__ import annotations

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
from agent_bridge.github.ci_watcher import ingest_ci_fixture
from agent_bridge.github.review_watcher import ingest_review_fixture

app = typer.Typer(help="Agent Bridge CLI")
queue_app = typer.Typer(help="Queue commands")
app.add_typer(queue_app, name="queue")
console = Console()

ROOT = Path.cwd()
WORKSPACE = ROOT / "workspace"
QUEUE_DIR = WORKSPACE / "queue"
STATE_PATH = WORKSPACE / "state" / "state.json"
LOG_PATH = WORKSPACE / "logs" / "bridge.jsonl"
TEMPLATE_DIR = ROOT / "agent_bridge" / "templates"


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
def dispatch_next(dry_run: bool = True) -> None:
    ensure_workspace()
    Dispatcher(
        queue=CommandQueue(QUEUE_DIR),
        prompt_builder=PromptBuilder(TEMPLATE_DIR),
        workspace_dir=WORKSPACE,
        console=console,
    ).dispatch_next(dry_run=dry_run)


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
