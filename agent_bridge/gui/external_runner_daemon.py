from __future__ import annotations

import json
import os
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from agent_bridge.core.command_queue import CommandQueue
from agent_bridge.core.event_log import EventLog
from agent_bridge.core.state_store import StateStore
from agent_bridge.gui.external_runner import CODEX_SANDBOX_HARD_BLOCK_MARKER, detect_codex_sandbox
from agent_bridge.gui.gui_automation import GuiAutomationAdapter, MacOSSystemEventsGuiAdapter
from agent_bridge.gui.macos_apps import GuiTargets
from agent_bridge.gui.report_roundtrip import (
    ReportRoundtripConfig,
    ReportRoundtripResult,
    run_report_roundtrip,
)


REPORT_TRIGGER = "report_roundtrip.request"
QUEUE_TRIGGER = "queue_dispatch.request"


class ExternalGuiRunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExternalGuiTrigger:
    kind: str
    reason: str
    path: Path | None = None
    content_hash: str | None = None


@dataclass(frozen=True)
class ExternalGuiRunnerConfig:
    workspace_dir: Path
    template_dir: Path
    targets: GuiTargets
    auto_confirm: bool
    watch_reports: bool = False
    watch_queue: bool = False
    polling_interval_seconds: float = 3
    max_runtime_seconds: int = 3600
    debounce_seconds: float = 1
    cooldown_seconds: float = 5
    stale_lock_seconds: float = 1800
    pm_response_timeout_seconds: int = 45
    max_roundtrips: int | None = None
    lock_file_name: str = "external_runner.lock"
    use_report_hash_guard: bool = False
    last_processed_report_hash_path: Path | None = None


@dataclass(frozen=True)
class ExternalGuiRunnerResult:
    reason: str
    triggers_detected: int = 0
    roundtrips_started: int = 0
    roundtrips_completed: int = 0
    failures: list[str] = field(default_factory=list)
    safety_paused: bool = False
    max_runtime_reached: bool = False


RoundtripRunner = Callable[
    [ReportRoundtripConfig, GuiAutomationAdapter, CommandQueue, EventLog, StateStore],
    ReportRoundtripResult,
]


def default_roundtrip_runner(
    config: ReportRoundtripConfig,
    gui: GuiAutomationAdapter,
    queue: CommandQueue,
    event_log: EventLog,
    state_store: StateStore,
) -> ReportRoundtripResult:
    return run_report_roundtrip(
        config=config,
        gui=gui,
        queue=queue,
        event_log=event_log,
        state_store=state_store,
    )


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return None


def _write_runner_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} {message}\n")


class ExternalGuiRunnerLock:
    def __init__(
        self,
        path: Path,
        *,
        stale_lock_seconds: float,
        time_fn: Callable[[], float] = time.time,
    ):
        self.path = path
        self.stale_lock_seconds = stale_lock_seconds
        self.time_fn = time_fn
        self.acquired = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self._is_stale():
            self.path.unlink()
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "created_at_epoch": self.time_fn(),
            }
        )
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        self.acquired = True
        return True

    def release(self) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)
            self.acquired = False

    def _is_stale(self) -> bool:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return True
        created_at = float(data.get("created_at_epoch") or 0)
        return self.time_fn() - created_at > self.stale_lock_seconds


class ExternalGuiRunner:
    def __init__(
        self,
        config: ExternalGuiRunnerConfig,
        *,
        env: Mapping[str, str] | None = None,
        roundtrip_runner: RoundtripRunner = default_roundtrip_runner,
        gui_factory: Callable[[], GuiAutomationAdapter] = MacOSSystemEventsGuiAdapter,
        event_log: EventLog | None = None,
        state_store: StateStore | None = None,
        monotonic_fn: Callable[[], float] = time.monotonic,
        time_fn: Callable[[], float] = time.time,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self.env = env if env is not None else os.environ
        self.roundtrip_runner = roundtrip_runner
        self.gui_factory = gui_factory
        self.event_log = event_log or EventLog(config.workspace_dir / "logs" / "bridge.jsonl")
        self.state_store = state_store or StateStore(config.workspace_dir / "state" / "state.json")
        self.monotonic_fn = monotonic_fn
        self.time_fn = time_fn
        self.sleep_fn = sleep_fn
        self.runner_log_path = config.workspace_dir / "logs" / "external_gui_runner.log"
        self.lock_path = config.workspace_dir / "state" / config.lock_file_name
        self.last_processed_report_hash_path = (
            config.last_processed_report_hash_path
            or config.workspace_dir / "state" / "last_processed_report_hash"
        )
        self.triggers_dir = config.workspace_dir / "triggers"
        self.report_path = config.workspace_dir / "reports" / "latest_agent_report.md"
        self.queue_path = config.workspace_dir / "queue" / "pending_commands.jsonl"

    def run(self) -> ExternalGuiRunnerResult:
        if not self.config.auto_confirm:
            raise ExternalGuiRunnerError("run-external-gui-runner requires --auto-confirm.")
        if self.config.max_runtime_seconds <= 0:
            raise ExternalGuiRunnerError("--max-runtime-seconds must be greater than zero.")
        if self.config.polling_interval_seconds <= 0:
            raise ExternalGuiRunnerError("--polling-interval-seconds must be greater than zero.")
        markers = detect_codex_sandbox(self.env)
        if CODEX_SANDBOX_HARD_BLOCK_MARKER in markers:
            self.event_log.append("external_runner_stopped", reason="CODEX_SANDBOX")
            _write_runner_log(self.runner_log_path, "Refused to run: CODEX_SANDBOX is set.")
            raise ExternalGuiRunnerError("Refusing to run external GUI runner with CODEX_SANDBOX set.")

        self._ensure_workspace()
        started_at = self.monotonic_fn()
        baseline_report_mtime = _mtime(self.report_path)
        baseline_report_hash = (
            self._current_report_hash() if self.config.use_report_hash_guard else None
        )
        baseline_queue_mtime = _mtime(self.queue_path)
        trigger_count = 0
        roundtrips_started = 0
        roundtrips_completed = 0
        failures: list[str] = []
        self.event_log.append(
            "external_runner_started",
            watch_reports=self.config.watch_reports,
            watch_queue=self.config.watch_queue,
            max_runtime_seconds=self.config.max_runtime_seconds,
            polling_interval_seconds=self.config.polling_interval_seconds,
            full_access_codex_context=bool(markers),
        )
        _write_runner_log(self.runner_log_path, "External GUI runner started.")

        while True:
            elapsed = self.monotonic_fn() - started_at
            if elapsed >= self.config.max_runtime_seconds:
                self.event_log.append("external_runner_max_runtime_reached", elapsed_seconds=elapsed)
                self.event_log.append("external_runner_stopped", reason="MAX_RUNTIME_REACHED")
                _write_runner_log(self.runner_log_path, "External GUI runner stopped: max runtime reached.")
                return ExternalGuiRunnerResult(
                    reason="MAX_RUNTIME_REACHED",
                    triggers_detected=trigger_count,
                    roundtrips_started=roundtrips_started,
                    roundtrips_completed=roundtrips_completed,
                    failures=failures,
                    max_runtime_reached=True,
                )

            state = self.state_store.load()
            if state.safety_pause:
                self.event_log.append("external_runner_safety_pause")
                self.event_log.append("external_runner_stopped", reason="SAFETY_PAUSE")
                _write_runner_log(self.runner_log_path, "External GUI runner stopped: safety pause.")
                return ExternalGuiRunnerResult(
                    reason="SAFETY_PAUSE",
                    triggers_detected=trigger_count,
                    roundtrips_started=roundtrips_started,
                    roundtrips_completed=roundtrips_completed,
                    failures=failures,
                    safety_paused=True,
                )

            triggers = self._detect_triggers(
                baseline_report_mtime,
                baseline_queue_mtime,
                baseline_report_hash,
            )
            if triggers:
                trigger_count += len(triggers)
                for trigger in triggers:
                    self.event_log.append(
                        "external_runner_trigger_detected",
                        kind=trigger.kind,
                        reason=trigger.reason,
                        path=str(trigger.path) if trigger.path else None,
                    )
                self.sleep_fn(self.config.debounce_seconds)
                outcome = self._run_one_roundtrip(triggers)
                if outcome is None:
                    failures.append("lock unavailable")
                elif outcome:
                    roundtrips_completed += 1
                else:
                    failures.append("roundtrip failed or was blocked")
                if outcome is not None:
                    roundtrips_started += 1
                if outcome is not None:
                    self._consume_trigger_files(triggers)
                    self._mark_report_hash_processed(triggers)
                    baseline_report_mtime = _mtime(self.report_path)
                    baseline_report_hash = (
                        self._current_report_hash()
                        if self.config.use_report_hash_guard
                        else None
                    )
                    baseline_queue_mtime = _mtime(self.queue_path)
                if (
                    self.config.max_roundtrips is not None
                    and roundtrips_started >= self.config.max_roundtrips
                ):
                    self.event_log.append("external_runner_stopped", reason="MAX_ROUNDTRIPS_REACHED")
                    return ExternalGuiRunnerResult(
                        reason="MAX_ROUNDTRIPS_REACHED",
                        triggers_detected=trigger_count,
                        roundtrips_started=roundtrips_started,
                        roundtrips_completed=roundtrips_completed,
                        failures=failures,
                    )
                self.sleep_fn(self.config.cooldown_seconds)
                continue

            self.sleep_fn(self.config.polling_interval_seconds)

    def _ensure_workspace(self) -> None:
        for subdir in ["logs", "state", "queue", "reports", "outbox", "triggers"]:
            (self.config.workspace_dir / subdir).mkdir(parents=True, exist_ok=True)

    def _detect_triggers(
        self,
        baseline_report_mtime: float | None,
        baseline_queue_mtime: float | None,
        baseline_report_hash: str | None = None,
    ) -> list[ExternalGuiTrigger]:
        triggers: list[ExternalGuiTrigger] = []
        report_trigger_path = self.triggers_dir / REPORT_TRIGGER
        queue_trigger_path = self.triggers_dir / QUEUE_TRIGGER
        if report_trigger_path.exists():
            triggers.append(
                ExternalGuiTrigger("report_roundtrip", "trigger_file", report_trigger_path)
            )
        if queue_trigger_path.exists():
            triggers.append(ExternalGuiTrigger("queue_dispatch", "trigger_file", queue_trigger_path))
        if self.config.watch_reports:
            report_mtime = _mtime(self.report_path)
            if self.config.use_report_hash_guard:
                report_hash = self._current_report_hash()
                if report_hash is not None and report_hash != baseline_report_hash:
                    if report_hash != self._last_processed_report_hash():
                        triggers.append(
                            ExternalGuiTrigger(
                                "report_roundtrip",
                                "report_hash_changed",
                                content_hash=report_hash,
                            )
                        )
            elif report_mtime is not None and baseline_report_mtime is not None:
                if report_mtime > baseline_report_mtime:
                    triggers.append(ExternalGuiTrigger("report_roundtrip", "report_mtime"))
        if self.config.watch_queue:
            queue_mtime = _mtime(self.queue_path)
            if queue_mtime is not None and baseline_queue_mtime is not None:
                if queue_mtime > baseline_queue_mtime:
                    triggers.append(ExternalGuiTrigger("queue_dispatch", "queue_mtime"))
        return triggers

    def _run_one_roundtrip(self, triggers: list[ExternalGuiTrigger]) -> bool | None:
        lock = ExternalGuiRunnerLock(
            self.lock_path,
            stale_lock_seconds=self.config.stale_lock_seconds,
            time_fn=self.time_fn,
        )
        if not lock.acquire():
            self.event_log.append("external_runner_roundtrip_failed", reason="lock_unavailable")
            _write_runner_log(self.runner_log_path, "Roundtrip skipped: lock unavailable.")
            return None
        self.event_log.append(
            "external_runner_lock_acquired",
            lock_path=str(self.lock_path),
            trigger_kinds=[trigger.kind for trigger in triggers],
        )
        try:
            self.event_log.append("external_runner_roundtrip_started")
            _write_runner_log(self.runner_log_path, "Roundtrip started.")
            result = self.roundtrip_runner(
                ReportRoundtripConfig(
                    workspace_dir=self.config.workspace_dir,
                    template_dir=self.config.template_dir,
                    targets=self.config.targets,
                    auto_confirm=self.config.auto_confirm,
                    max_cycles=1,
                    max_runtime_seconds=min(180, self.config.max_runtime_seconds),
                    pm_response_timeout_seconds=self.config.pm_response_timeout_seconds,
                    require_pm_backend_preflight=(
                        self.config.targets.pm_assistant.require_backend_preflight
                    ),
                ),
                self.gui_factory(),
                CommandQueue(self.config.workspace_dir / "queue"),
                self.event_log,
                self.state_store,
            )
            if result.safety_paused:
                self.event_log.append("external_runner_safety_pause")
                return False
            if result.completed:
                self.event_log.append("external_runner_roundtrip_completed")
                _write_runner_log(self.runner_log_path, "Roundtrip completed.")
                return True
            self.event_log.append("external_runner_roundtrip_failed", reason=result.reason)
            _write_runner_log(self.runner_log_path, f"Roundtrip failed: {result.reason}")
            return False
        except Exception as error:
            self.event_log.append("external_runner_roundtrip_failed", error=str(error))
            _write_runner_log(self.runner_log_path, f"Roundtrip failed: {error}")
            return False
        finally:
            lock.release()
            self.event_log.append("external_runner_lock_released", lock_path=str(self.lock_path))

    def _consume_trigger_files(self, triggers: list[ExternalGuiTrigger]) -> None:
        for trigger in triggers:
            if trigger.path is None or not trigger.path.exists():
                continue
            consumed_path = trigger.path.with_name(
                f"{trigger.path.name}.consumed.{int(self.time_fn())}"
            )
            try:
                trigger.path.replace(consumed_path)
            except OSError:
                pass

    def _current_report_hash(self) -> str | None:
        try:
            data = self.report_path.read_bytes()
        except OSError:
            return None
        return hashlib.sha256(data).hexdigest()

    def _last_processed_report_hash(self) -> str | None:
        try:
            value = self.last_processed_report_hash_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value or None

    def _mark_report_hash_processed(self, triggers: list[ExternalGuiTrigger]) -> None:
        if not self.config.use_report_hash_guard:
            return
        if not any(trigger.kind == "report_roundtrip" for trigger in triggers):
            return
        report_hash = self._current_report_hash()
        if report_hash is None:
            return
        self.last_processed_report_hash_path.parent.mkdir(parents=True, exist_ok=True)
        self.last_processed_report_hash_path.write_text(f"{report_hash}\n", encoding="utf-8")
        self.event_log.append(
            "external_runner_report_hash_processed",
            hash_path=str(self.last_processed_report_hash_path),
            report_hash=report_hash,
        )
