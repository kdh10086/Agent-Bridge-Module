from pathlib import Path
from rich.console import Console
from agent_bridge.codex.prompt_builder import PromptBuilder
from agent_bridge.core.command_queue import CommandQueue
from agent_bridge.core.event_log import EventLog
from agent_bridge.core.models import BridgeStateName, SafetyDecision
from agent_bridge.core.safety_gate import SafetyGate
from agent_bridge.core.state_store import StateStore


class Dispatcher:
    def __init__(
        self,
        queue: CommandQueue,
        prompt_builder: PromptBuilder,
        workspace_dir: Path,
        console: Console | None = None,
        event_log: EventLog | None = None,
        state_store: StateStore | None = None,
    ):
        self.queue = queue
        self.prompt_builder = prompt_builder
        self.workspace_dir = workspace_dir
        self.console = console or Console()
        self.safety_gate = SafetyGate()
        self.event_log = event_log or EventLog(workspace_dir / "logs" / "bridge.jsonl")
        self.state_store = state_store or StateStore(workspace_dir / "state" / "state.json")

    def dispatch_next(self, dry_run: bool = True) -> str | None:
        command = self.queue.pop_next()
        if command is None:
            self.event_log.append("dispatch_no_pending")
            self.console.print("[yellow]No pending commands.[/yellow]")
            return None

        self.event_log.append(
            "dispatch_started",
            command_id=command.id,
            command_type=command.type.value,
            dry_run=dry_run,
        )
        payload_path = Path(command.payload_path)
        if not payload_path.is_absolute():
            payload_path = self.workspace_dir.parent / payload_path
        payload = payload_path.read_text(encoding="utf-8")

        decision = self.safety_gate.check_text(payload)
        if command.requires_user_approval and decision.allowed:
            decision = SafetyDecision(
                allowed=False,
                reason="Command requires user approval.",
                matched_keywords=["APPROVAL_REQUIRED"],
            )

        if not decision.allowed:
            self.safety_gate.write_decision_request(self.workspace_dir, decision, payload)
            state = self.state_store.load()
            state.safety_pause = True
            state.state = BridgeStateName.PAUSED_FOR_USER_DECISION
            self.state_store.save(state)
            self.queue.fail_in_progress("Blocked by safety gate.")
            self.event_log.append(
                "dispatch_blocked",
                command_id=command.id,
                matched_keywords=decision.matched_keywords,
            )
            self.console.print("[red]Command blocked by safety gate.[/red]")
            return None

        prompt = self.prompt_builder.build_local_agent_command(command, payload)
        if dry_run:
            self.event_log.append("dispatch_dry_run_prompt_built", command_id=command.id)
            self.console.print("[bold cyan]DRY RUN: local-agent prompt[/bold cyan]")
            self.console.print(prompt)
            return prompt
        raise NotImplementedError("Real GUI dispatch is intentionally not implemented in MVP.")
