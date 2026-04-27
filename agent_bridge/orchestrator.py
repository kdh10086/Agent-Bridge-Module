from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console

from agent_bridge.codex.dispatcher import Dispatcher
from agent_bridge.codex.prompt_builder import PromptBuilder
from agent_bridge.core.command_queue import CommandQueue
from agent_bridge.core.event_log import EventLog
from agent_bridge.core.models import BridgeStateName, utc_now_iso
from agent_bridge.core.state_store import StateStore
from agent_bridge.github.ci_watcher import watch_ci_failures
from agent_bridge.github.review_watcher import watch_review_comments


Watcher = Callable[..., tuple[bool, Any, Path, Any, str]]


@dataclass(frozen=True)
class RunLoopConfig:
    workspace_dir: Path
    template_dir: Path
    dry_run: bool = True
    max_cycles: int = 5
    max_runtime_seconds: int = 3600
    polling_interval_seconds: float = 30
    watch_reviews: bool = False
    watch_ci: bool = False
    owner: str | None = None
    repo: str | None = None
    pr_number: int | None = None
    dispatch: bool = True
    stop_on_watcher_error: bool = True


@dataclass
class RunLoopResult:
    reason: str
    cycles_completed: int = 0
    dispatched_count: int = 0
    queue_empty_count: int = 0
    safety_paused: bool = False
    errors: list[str] = field(default_factory=list)


class RunLoop:
    def __init__(
        self,
        config: RunLoopConfig,
        *,
        queue: CommandQueue | None = None,
        event_log: EventLog | None = None,
        state_store: StateStore | None = None,
        console: Console | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
        review_watcher: Watcher = watch_review_comments,
        ci_watcher: Watcher = watch_ci_failures,
    ):
        self.config = config
        self.workspace_dir = config.workspace_dir
        self.queue = queue or CommandQueue(self.workspace_dir / "queue")
        self.event_log = event_log or EventLog(self.workspace_dir / "logs" / "bridge.jsonl")
        self.state_store = state_store or StateStore(self.workspace_dir / "state" / "state.json")
        self.console = console or Console()
        self.sleep_fn = sleep_fn
        self.monotonic_fn = monotonic_fn
        self.review_watcher = review_watcher
        self.ci_watcher = ci_watcher

    def run(self) -> RunLoopResult:
        self._ensure_workspace()
        self._validate()
        start_time = self.monotonic_fn()
        result = RunLoopResult(reason="LOOP_COMPLETE")
        self._start_state()
        self._event(
            "run_loop_start",
            dry_run=self.config.dry_run,
            max_cycles=self.config.max_cycles,
            max_runtime_seconds=self.config.max_runtime_seconds,
            polling_interval_seconds=self.config.polling_interval_seconds,
            watch_reviews=self.config.watch_reviews,
            watch_ci=self.config.watch_ci,
            dispatch=self.config.dispatch,
        )

        try:
            while True:
                state = self.state_store.load()
                if state.safety_pause:
                    result.reason = "SAFETY_PAUSED"
                    result.safety_paused = True
                    self._event("run_loop_safety_pause", cycle=result.cycles_completed)
                    return self._complete(result)

                if self._runtime_exceeded(start_time):
                    result.reason = "MAX_RUNTIME_REACHED"
                    self._event("run_loop_max_runtime_reached", cycle=result.cycles_completed)
                    return self._complete(result)

                if result.cycles_completed >= self.config.max_cycles:
                    result.reason = "MAX_CYCLES_REACHED"
                    self._event("run_loop_max_cycles_reached", cycle=result.cycles_completed)
                    return self._complete(result)

                cycle = result.cycles_completed + 1
                self._cycle_state(cycle)
                self._event("run_loop_cycle_start", cycle=cycle)

                self._poll_watchers(cycle)
                pending = self.queue.list_pending()
                if not pending:
                    result.queue_empty_count += 1
                    self._event("run_loop_queue_empty", cycle=cycle)
                elif self.config.dispatch:
                    prompt = self._dispatch_dry_run(cycle)
                    if prompt is not None:
                        result.dispatched_count += 1
                        self._event("run_loop_command_dispatched_dry_run", cycle=cycle)
                    state = self.state_store.load()
                    if state.safety_pause:
                        result.reason = "SAFETY_PAUSED"
                        result.safety_paused = True
                        self._event("run_loop_safety_pause", cycle=cycle)
                        result.cycles_completed = cycle
                        return self._complete(result)
                else:
                    self._event("run_loop_dispatch_skipped", cycle=cycle, pending_commands=len(pending))

                result.cycles_completed = cycle
                if result.cycles_completed >= self.config.max_cycles:
                    result.reason = "MAX_CYCLES_REACHED"
                    self._event("run_loop_max_cycles_reached", cycle=result.cycles_completed)
                    return self._complete(result)
                if self._runtime_exceeded(start_time):
                    result.reason = "MAX_RUNTIME_REACHED"
                    self._event("run_loop_max_runtime_reached", cycle=result.cycles_completed)
                    return self._complete(result)

                self._wait_interval(cycle)
        except Exception as error:
            message = str(error)
            state = self.state_store.load()
            state.last_error = message
            state.state = BridgeStateName.ERROR_RECOVERY
            self._save_state(state, "LOOP_ERROR")
            result.reason = "LOOP_ERROR"
            result.errors.append(message)
            self._event("run_loop_error", error=message, cycle=result.cycles_completed)
            return self._complete(result)

    def _ensure_workspace(self) -> None:
        for subdir in ["state", "queue", "inbox", "outbox", "reports", "reviews", "logs"]:
            (self.workspace_dir / subdir).mkdir(parents=True, exist_ok=True)

    def _validate(self) -> None:
        if self.config.max_cycles < 0:
            raise ValueError("max_cycles must be zero or greater.")
        if self.config.max_runtime_seconds < 0:
            raise ValueError("max_runtime_seconds must be zero or greater.")
        if self.config.polling_interval_seconds < 0:
            raise ValueError("polling_interval_seconds must be zero or greater.")
        if (self.config.watch_reviews or self.config.watch_ci) and (
            not self.config.owner or not self.config.repo or self.config.pr_number is None
        ):
            raise ValueError("--owner, --repo, and --pr are required when watcher polling is enabled.")

    def _start_state(self) -> None:
        state = self.state_store.load()
        state.cycle = 0
        state.max_cycles = self.config.max_cycles
        state.max_runtime_seconds = self.config.max_runtime_seconds
        state.loop_started_at = utc_now_iso()
        state.last_error = None
        state.state = BridgeStateName.IDLE
        self._save_state(state, "LOOP_START")

    def _cycle_state(self, cycle: int) -> None:
        state = self.state_store.load()
        state.cycle = cycle
        state.state = BridgeStateName.QUEUE_READY
        self._save_state(state, "INSPECT_QUEUE")

    def _save_state(self, state, event_name: str) -> None:
        state.last_loop_event = event_name
        self.state_store.save(state)

    def _event(self, event_type: str, **metadata: Any) -> None:
        self.event_log.append(event_type, **metadata)

    def _runtime_exceeded(self, start_time: float) -> bool:
        return (self.monotonic_fn() - start_time) >= self.config.max_runtime_seconds

    def _poll_watchers(self, cycle: int) -> None:
        if not self.config.watch_reviews and not self.config.watch_ci:
            return
        self._event("run_loop_poll_watchers", cycle=cycle)
        watcher_dry_run = self.config.dry_run
        if self.config.watch_reviews:
            self.review_watcher(
                owner=self.config.owner,
                repo=self.config.repo,
                pr_number=self.config.pr_number,
                workspace_dir=self.workspace_dir,
                dry_run=watcher_dry_run,
                queue=self.queue,
                event_log=self.event_log,
            )
        if self.config.watch_ci:
            self.ci_watcher(
                owner=self.config.owner,
                repo=self.config.repo,
                pr_number=self.config.pr_number,
                workspace_dir=self.workspace_dir,
                dry_run=watcher_dry_run,
                queue=self.queue,
                event_log=self.event_log,
            )

    def _dispatch_dry_run(self, cycle: int) -> str | None:
        state = self.state_store.load()
        state.state = BridgeStateName.DISPATCHING
        self._save_state(state, "DISPATCH_DRY_RUN")
        self._event("run_loop_dispatch_dry_run_start", cycle=cycle)
        dispatcher = Dispatcher(
            queue=self.queue,
            prompt_builder=PromptBuilder(self.config.template_dir),
            workspace_dir=self.workspace_dir,
            console=self.console,
            event_log=self.event_log,
            state_store=self.state_store,
        )
        return dispatcher.dispatch_next(dry_run=True)

    def _wait_interval(self, cycle: int) -> None:
        if self.config.polling_interval_seconds <= 0:
            return
        self._event("run_loop_wait_interval", cycle=cycle, seconds=self.config.polling_interval_seconds)
        self.sleep_fn(self.config.polling_interval_seconds)

    def _complete(self, result: RunLoopResult) -> RunLoopResult:
        state = self.state_store.load()
        if not state.safety_pause and result.reason != "LOOP_ERROR":
            state.state = BridgeStateName.IDLE
        self._save_state(state, result.reason)
        self._event(
            "run_loop_complete",
            reason=result.reason,
            cycles_completed=result.cycles_completed,
            dispatched_count=result.dispatched_count,
            queue_empty_count=result.queue_empty_count,
            safety_paused=result.safety_paused,
            errors=result.errors,
        )
        return result
