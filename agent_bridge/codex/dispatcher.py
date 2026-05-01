from dataclasses import dataclass
from pathlib import Path
from rich.console import Console
from agent_bridge.codex.prompt_builder import PromptBuilder
from agent_bridge.core.command_queue import CommandQueue
from agent_bridge.core.event_log import EventLog
from agent_bridge.core.models import BridgeStateName, Command, CommandStatus, SafetyDecision
from agent_bridge.core.safety_gate import SafetyGate
from agent_bridge.core.state_store import StateStore


LOCAL_AGENT_PROMPT_PATH = "next_local_agent_prompt.md"


@dataclass(frozen=True)
class DispatchPromptResult:
    prompt_path: Path
    prompt: str
    command: Command | None
    staged: bool
    consumed: bool = False
    blocked: bool = False
    reason: str | None = None
    command_status: str | None = None


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

    def _next_pending_command(self) -> Command | None:
        pending = sorted(self.queue.list_pending(), key=lambda c: (-(c.priority or 0), c.created_at))
        return pending[0] if pending else None

    def _resolve_prompt_path(self, prompt_path: str) -> Path:
        path = Path(prompt_path)
        if path.is_absolute():
            return path
        return self.workspace_dir.parent / path

    def _resolve_command_payload(self, command: Command) -> str:
        if command.prompt_text is not None:
            return command.prompt_text
        if command.prompt_path:
            return self._resolve_prompt_path(command.prompt_path).read_text(encoding="utf-8")
        if command.payload_path:
            return self._resolve_prompt_path(command.payload_path).read_text(encoding="utf-8")
        raise ValueError(f"Command {command.id} has no prompt_text, prompt_path, or payload_path.")

    def stage_command_by_id(self, command_id: str, *, dry_run: bool = False) -> DispatchPromptResult:
        return self.prepare_next_local_agent_prompt(
            consume=False,
            dry_run=dry_run,
            command_id=command_id,
        )

    def dispatch_command_by_id(self, command_id: str, *, dry_run: bool = True) -> DispatchPromptResult:
        return self.prepare_next_local_agent_prompt(
            consume=True,
            dry_run=dry_run,
            command_id=command_id,
        )

    def prepare_next_local_agent_prompt(
        self,
        *,
        consume: bool,
        dry_run: bool = True,
        command_id: str | None = None,
    ) -> DispatchPromptResult:
        prompt_path = self.workspace_dir / "outbox" / LOCAL_AGENT_PROMPT_PATH
        if command_id:
            self.event_log.append(
                "local_agent_dispatch_by_id_started",
                command_id=command_id,
                consume=consume,
                dry_run=dry_run,
            )
        if command_id:
            if consume:
                command = self.queue.pop_by_id(command_id)
                if command is None:
                    existing = self.queue.get_by_id(command_id)
                    if existing is None:
                        reason = "command_id_not_found_after_enqueue"
                        command_status = None
                    else:
                        reason = "command_not_dispatchable_after_enqueue"
                        command_status = existing.status.value
                    self.event_log.append(
                        "local_agent_dispatch_by_id_result",
                        command_id=command_id,
                        result="failed",
                        reason=reason,
                        command_status=command_status,
                        consume=consume,
                    )
                    return DispatchPromptResult(
                        prompt_path=prompt_path,
                        prompt="",
                        command=None,
                        staged=False,
                        consumed=False,
                        reason=reason,
                        command_status=command_status,
                    )
            else:
                command = self.queue.get_by_id(command_id)
                if command is None:
                    reason = "command_id_not_found_after_enqueue"
                    self.event_log.append(
                        "local_agent_dispatch_by_id_result",
                        command_id=command_id,
                        result="failed",
                        reason=reason,
                        consume=consume,
                    )
                    return DispatchPromptResult(
                        prompt_path=prompt_path,
                        prompt="",
                        command=None,
                        staged=False,
                        consumed=False,
                        reason=reason,
                    )
                if command.status not in {CommandStatus.PENDING, CommandStatus.IN_PROGRESS}:
                    reason = "command_not_dispatchable_after_enqueue"
                    self.event_log.append(
                        "local_agent_dispatch_by_id_result",
                        command_id=command_id,
                        result="failed",
                        reason=reason,
                        command_status=command.status.value,
                        consume=consume,
                    )
                    return DispatchPromptResult(
                        prompt_path=prompt_path,
                        prompt="",
                        command=None,
                        staged=False,
                        consumed=False,
                        reason=reason,
                        command_status=command.status.value,
                    )
        else:
            command = self.queue.pop_next() if consume else self._next_pending_command()
        if command is None:
            self.event_log.append(
                "dispatch_no_pending" if consume else "local_agent_prompt_stage_no_pending",
                command_id=command_id,
            )
            return DispatchPromptResult(
                prompt_path=prompt_path,
                prompt="",
                command=None,
                staged=False,
                consumed=False,
                reason="No pending commands.",
            )

        if consume:
            self.event_log.append(
                "dispatch_started",
                command_id=command.id,
                command_type=command.type.value,
                dry_run=dry_run,
            )

        payload = self._resolve_command_payload(command)
        prompt = self.prompt_builder.build_local_agent_command(command, payload)
        decision = self.safety_gate.check_text(prompt)
        if command.requires_user_approval and decision.allowed:
            decision = SafetyDecision(
                allowed=False,
                reason="Command requires user approval.",
                matched_keywords=["APPROVAL_REQUIRED"],
            )

        if not decision.allowed:
            self.safety_gate.write_decision_request(self.workspace_dir, decision, prompt)
            prompt_path.unlink(missing_ok=True)
            state = self.state_store.load()
            state.safety_pause = True
            state.state = BridgeStateName.PAUSED_FOR_USER_DECISION
            self.state_store.save(state)
            if consume:
                self.queue.block_in_progress("Blocked by safety gate.")
                self.event_log.append(
                    "dispatch_blocked",
                    command_id=command.id,
                    matched_keywords=decision.matched_keywords,
                )
            self.event_log.append(
                "local_agent_dispatch_blocked_by_safety",
                command_id=command.id,
                matched_keywords=decision.matched_keywords,
                prompt_path=str(prompt_path),
                consumed=consume,
            )
            return DispatchPromptResult(
                prompt_path=prompt_path,
                prompt=prompt,
                command=command,
                staged=False,
                consumed=consume,
                blocked=True,
                reason=decision.reason,
            )

        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")
        self.event_log.append(
            "local_agent_prompt_staged",
            command_id=command.id,
            command_type=command.type.value,
            prompt_path=str(prompt_path),
            consumed=consume,
            dry_run=dry_run,
        )
        if command_id:
            self.event_log.append(
                "local_agent_prompt_staged_from_command_id",
                command_id=command.id,
                command_status=command.status.value,
                prompt_path=str(prompt_path),
                consumed=consume,
                dry_run=dry_run,
            )
            self.event_log.append(
                "local_agent_dispatch_by_id_result",
                command_id=command.id,
                command_status=command.status.value,
                result="succeeded",
                prompt_path=str(prompt_path),
                consumed=consume,
            )
        return DispatchPromptResult(
            prompt_path=prompt_path,
            prompt=prompt,
            command=command,
            staged=True,
            consumed=consume,
            command_status=command.status.value,
        )

    def dispatch_next(self, dry_run: bool = True) -> str | None:
        self.event_log.append("local_agent_dispatch_requested", dry_run=dry_run, consume=True)
        result = self.prepare_next_local_agent_prompt(consume=True, dry_run=dry_run)
        if result.command is None:
            self.console.print("[yellow]No pending commands.[/yellow]")
            return None
        if result.blocked:
            self.console.print("[red]Command blocked by safety gate.[/red]")
            return None
        if dry_run:
            self.event_log.append("dispatch_dry_run_prompt_built", command_id=result.command.id)
            self.console.print("[bold cyan]DRY RUN: local-agent prompt[/bold cyan]")
            self.console.print(result.prompt)
            return result.prompt
        raise NotImplementedError("Real GUI dispatch is intentionally not implemented in MVP.")
