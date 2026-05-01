from __future__ import annotations

import hashlib
import importlib.util
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from agent_bridge.core.command_queue import CommandQueue
from agent_bridge.core.event_log import EventLog
from agent_bridge.core.state_store import StateStore
from agent_bridge.gui.asset_state_machine import AssetVisualStateDetector, asset_profile_for_target
from agent_bridge.gui.chatgpt_mac_native import diagnose_chatgpt_app_targets
from agent_bridge.gui.chatgpt_mac_response_capture import diagnose_chatgpt_mac_response_capture
from agent_bridge.gui.codex_ui_detector import CodexUIDetector
from agent_bridge.gui.external_runner_daemon import ExternalGuiRunnerLock, _write_runner_log
from agent_bridge.gui.gui_automation import GuiAutomationAdapter, MacOSSystemEventsGuiAdapter
from agent_bridge.gui.macos_apps import (
    CHATGPT_CHROME_APP_PROFILE,
    CHATGPT_MAC_PROFILE,
    GuiTargets,
    MacOSAppActivator,
    ManualStageTarget,
    ensure_chatgpt_chrome_app_target,
    ensure_native_chatgpt_mac_target,
    pm_target_for_profile,
    replace_manual_stage_target,
)
from agent_bridge.gui.report_roundtrip import (
    ReportRoundtripConfig,
    ReportRoundtripResult,
    run_report_roundtrip,
)
from agent_bridge.gui.visual_pm_controller import VisualPMController


FOREGROUND_TRIGGER_MARKER = "AGENT_BRIDGE_GUI_ROUNDTRIP_TEST"


@dataclass(frozen=True)
class ForegroundBridgeRunnerConfig:
    workspace_dir: Path
    template_dir: Path
    targets: GuiTargets
    pm_target_profile: str
    watch_report_path: Path
    auto_confirm: bool = True
    polling_interval_seconds: float = 3
    debounce_seconds: float = 2
    cooldown_seconds: float = 5
    max_runtime_seconds: int = 0
    max_roundtrips: int = 0
    stale_lock_seconds: float = 1800
    require_trigger_marker: bool = False
    process_existing_trigger: bool = False
    trigger_marker: str = FOREGROUND_TRIGGER_MARKER
    roundtrip_max_runtime_seconds: int = 180
    pm_response_timeout_seconds: int = 45
    stop_after_local_agent_submit: bool = True
    wait_for_artifact_confirmation: bool = False
    debug: bool = False
    debug_state_machine: bool = False
    debug_gui_actions: bool = False
    debug_screenshots: bool = False
    debug_all_template_comparisons: bool = False


@dataclass(frozen=True)
class ForegroundBridgeRunnerResult:
    reason: str
    changes_seen: int = 0
    triggers_accepted: int = 0
    roundtrips_started: int = 0
    roundtrips_completed: int = 0
    roundtrips_failed: int = 0
    roundtrips_interrupted: int = 0
    roundtrips_skipped: int = 0
    failures: tuple[str, ...] = ()
    safety_paused: bool = False
    keyboard_interrupt: bool = False
    max_runtime_reached: bool = False


@dataclass(frozen=True)
class BridgePreflightCheck:
    name: str
    succeeded: bool
    detail: str


@dataclass(frozen=True)
class BridgePreflightResult:
    pm_target_profile: str
    pm_target: ManualStageTarget
    checks: tuple[BridgePreflightCheck, ...]

    @property
    def succeeded(self) -> bool:
        return all(check.succeeded for check in self.checks)


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


def resolve_runtime_pm_target(
    targets: GuiTargets,
    pm_target_profile: str,
) -> ManualStageTarget:
    profile = pm_target_profile.strip().lower().replace("-", "_")
    target = pm_target_for_profile(targets.pm_assistant, profile)
    if profile == CHATGPT_MAC_PROFILE:
        return ensure_native_chatgpt_mac_target(target)
    if profile != CHATGPT_CHROME_APP_PROFILE:
        raise ValueError(f"Unsupported PM target profile: {pm_target_profile}")
    target = ensure_chatgpt_chrome_app_target(target)
    diagnostic = diagnose_chatgpt_app_targets(
        target=target,
        profile=CHATGPT_CHROME_APP_PROFILE,
    )
    selected = next((candidate for candidate in diagnostic.candidates if candidate.selected), None)
    if selected is None or diagnostic.selected_bundle_id is None:
        raise RuntimeError(
            diagnostic.error or "ChatGPT Chrome/PWA app target has no selected visible window."
        )
    return replace_manual_stage_target(
        target,
        app_name=selected.name or target.app_name,
        bundle_id=diagnostic.selected_bundle_id,
        backend=target.backend or "chatgpt_chrome_app_visual",
        profile=CHATGPT_CHROME_APP_PROFILE,
        visual_asset_profile=CHATGPT_CHROME_APP_PROFILE,
    )


def targets_with_pm_profile(targets: GuiTargets, pm_target_profile: str) -> GuiTargets:
    return GuiTargets(
        pm_assistant=resolve_runtime_pm_target(targets, pm_target_profile),
        local_agent=targets.local_agent,
    )


def targets_with_pm_profile_for_startup(
    targets: GuiTargets,
    pm_target_profile: str,
) -> GuiTargets:
    controller = VisualPMController.for_profile(targets.pm_assistant, pm_target_profile)
    return GuiTargets(
        pm_assistant=controller.target,
        local_agent=targets.local_agent,
    )


class ForegroundBridgeRunner:
    def __init__(
        self,
        config: ForegroundBridgeRunnerConfig,
        *,
        roundtrip_runner: RoundtripRunner = default_roundtrip_runner,
        gui_factory: Callable[[], GuiAutomationAdapter] = MacOSSystemEventsGuiAdapter,
        event_log: EventLog | None = None,
        state_store: StateStore | None = None,
        monotonic_fn: Callable[[], float] = time.monotonic,
        time_fn: Callable[[], float] = time.time,
        sleep_fn: Callable[[float], None] = time.sleep,
        output_fn: Callable[[str], None] = print,
    ):
        self.config = config
        self.roundtrip_runner = roundtrip_runner
        self.gui_factory = gui_factory
        self.event_log = event_log or EventLog(config.workspace_dir / "logs" / "bridge.jsonl")
        self.state_store = state_store or StateStore(config.workspace_dir / "state" / "state.json")
        self.monotonic_fn = monotonic_fn
        self.time_fn = time_fn
        self.sleep_fn = sleep_fn
        self.output_fn = output_fn
        self.lock_path = config.workspace_dir / "state" / "foreground_bridge_runner.lock"
        self.runner_log_path = config.workspace_dir / "logs" / "foreground_bridge_runner.log"
        self.last_seen_hash_path = config.workspace_dir / "state" / "last_seen_report_hash"
        self.last_processed_hash_path = config.workspace_dir / "state" / "last_processed_report_hash"

    def run(self) -> ForegroundBridgeRunnerResult:
        self._ensure_workspace()
        started_at = self.monotonic_fn()
        runner_start_time = self.time_fn()
        failures: list[str] = []
        changes_seen = 0
        triggers_accepted = 0
        roundtrips_started = 0
        roundtrips_completed = 0
        roundtrips_failed = 0
        roundtrips_interrupted = 0
        roundtrips_skipped = 0
        active_bridge_attempt_id: str | None = None
        startup_baseline_hash = self._current_report_hash()
        last_seen_hash = startup_baseline_hash
        if startup_baseline_hash is not None:
            self._write_hash(self.last_seen_hash_path, startup_baseline_hash)
        baseline_recorded_time = self.time_fn()
        ready_to_watch_time = self.time_fn()
        startup_elapsed_seconds = self.monotonic_fn() - started_at
        self._emit(
            "foreground_bridge_runner_started",
            selected_pm_target=self.config.pm_target_profile,
            watch_report=str(self.config.watch_report_path),
            startup_baseline_hash=startup_baseline_hash,
            runner_start_time=runner_start_time,
            baseline_recorded_time=baseline_recorded_time,
            ready_to_watch_time=ready_to_watch_time,
            startup_elapsed_seconds=startup_elapsed_seconds,
            process_existing_trigger=self.config.process_existing_trigger,
            debug=self.config.debug,
            debug_state_machine=self._debug_state_machine_enabled,
            debug_gui_actions=self._debug_gui_actions_enabled,
            debug_screenshots=self.config.debug_screenshots,
            debug_all_template_comparisons=self.config.debug_all_template_comparisons,
        )
        self._print(f"Runner started. PM target: {self.config.pm_target_profile}")
        self._print(f"Watching report: {self.config.watch_report_path}")
        self._print(f"Ready to watch. Startup elapsed: {startup_elapsed_seconds:.3f}s")
        self._emit(
            "foreground_bridge_initial_baseline_recorded",
            report_hash=startup_baseline_hash,
            ignored=not self.config.process_existing_trigger,
            runner_start_time=runner_start_time,
            baseline_recorded_time=baseline_recorded_time,
            ready_to_watch_time=ready_to_watch_time,
            startup_elapsed_seconds=startup_elapsed_seconds,
        )
        self._print("Initial report baseline recorded. Waiting for changes after startup.")
        if self.config.debug:
            self._print("Debug logging enabled.")

        if self.config.process_existing_trigger and startup_baseline_hash is not None:
            processed = self._last_processed_report_hash()
            if startup_baseline_hash == processed:
                self._emit(
                    "foreground_bridge_report_hash_ignored",
                    reason="already_processed",
                    report_hash=startup_baseline_hash,
                    startup=True,
                )
                self._print("Report hash already processed; waiting for next change.")
            else:
                report_text = self._read_report_text()
                marker_present = self.config.trigger_marker in report_text
                self._emit(
                    "foreground_bridge_trigger_marker_checked",
                    marker=self.config.trigger_marker,
                    marker_present=marker_present,
                    require_trigger_marker=self.config.require_trigger_marker,
                    startup=True,
                )
                if self.config.require_trigger_marker and not marker_present:
                    self._emit(
                        "foreground_bridge_report_ignored",
                        reason="trigger_marker_absent",
                        report_hash=startup_baseline_hash,
                        startup=True,
                    )
                    self._print("Existing report trigger marker absent; waiting for changes.")
                else:
                    triggers_accepted += 1
                    active_bridge_attempt_id = self._new_bridge_attempt_id()
                    self._emit(
                        "foreground_bridge_existing_trigger_detected",
                        report_hash=startup_baseline_hash,
                        bridge_attempt_id=active_bridge_attempt_id,
                        trigger_marker_present=marker_present,
                        require_trigger_marker=self.config.require_trigger_marker,
                    )
                    self._print("Existing startup report accepted by explicit opt-in.")
                    roundtrips_started += 1
                    outcome = self._run_one_roundtrip(active_bridge_attempt_id)
                    active_bridge_attempt_id = None
                    if outcome is None:
                        roundtrips_started -= 1
                        roundtrips_skipped += 1
                        failures.append("lock unavailable")
                    else:
                        if outcome.completed and not outcome.safety_paused:
                            roundtrips_completed += 1
                            self._write_hash(
                                self.last_processed_hash_path,
                                startup_baseline_hash,
                            )
                        else:
                            roundtrips_failed += 1
                            failures.append(outcome.reason)
                        if outcome.safety_paused:
                            self._emit(
                                "foreground_bridge_runner_stopped",
                                reason="SAFETY_PAUSE",
                            )
                            return ForegroundBridgeRunnerResult(
                                reason="SAFETY_PAUSE",
                                changes_seen=changes_seen,
                                triggers_accepted=triggers_accepted,
                                roundtrips_started=roundtrips_started,
                                roundtrips_completed=roundtrips_completed,
                                roundtrips_failed=roundtrips_failed,
                                roundtrips_interrupted=roundtrips_interrupted,
                                roundtrips_skipped=roundtrips_skipped,
                                failures=tuple(failures),
                                safety_paused=True,
                            )
                    if (
                        self.config.max_roundtrips > 0
                        and roundtrips_started >= self.config.max_roundtrips
                    ):
                        self._emit(
                            "foreground_bridge_runner_stopped",
                            reason="MAX_ROUNDTRIPS_REACHED",
                        )
                        self._print("Runner stopped: max roundtrips reached.")
                        return ForegroundBridgeRunnerResult(
                            reason="MAX_ROUNDTRIPS_REACHED",
                            changes_seen=changes_seen,
                            triggers_accepted=triggers_accepted,
                            roundtrips_started=roundtrips_started,
                            roundtrips_completed=roundtrips_completed,
                            roundtrips_failed=roundtrips_failed,
                            roundtrips_interrupted=roundtrips_interrupted,
                            roundtrips_skipped=roundtrips_skipped,
                            failures=tuple(failures),
                        )
                    self.sleep_fn(self.config.cooldown_seconds)
                    self._print("Waiting for next report change.")

        try:
            while True:
                elapsed = self.monotonic_fn() - started_at
                if self.config.max_runtime_seconds > 0 and elapsed >= self.config.max_runtime_seconds:
                    self._emit("foreground_bridge_runner_stopped", reason="MAX_RUNTIME_REACHED")
                    self._print("Runner stopped: max runtime reached.")
                    return ForegroundBridgeRunnerResult(
                        reason="MAX_RUNTIME_REACHED",
                        changes_seen=changes_seen,
                        triggers_accepted=triggers_accepted,
                        roundtrips_started=roundtrips_started,
                        roundtrips_completed=roundtrips_completed,
                        roundtrips_failed=roundtrips_failed,
                        roundtrips_interrupted=roundtrips_interrupted,
                        roundtrips_skipped=roundtrips_skipped,
                        failures=tuple(failures),
                        max_runtime_reached=True,
                    )

                if self.state_store.load().safety_pause:
                    self._emit("foreground_bridge_runner_stopped", reason="SAFETY_PAUSE")
                    self._print("Runner stopped: safety pause is active.")
                    return ForegroundBridgeRunnerResult(
                        reason="SAFETY_PAUSE",
                        changes_seen=changes_seen,
                        triggers_accepted=triggers_accepted,
                        roundtrips_started=roundtrips_started,
                        roundtrips_completed=roundtrips_completed,
                        roundtrips_failed=roundtrips_failed,
                        roundtrips_interrupted=roundtrips_interrupted,
                        roundtrips_skipped=roundtrips_skipped,
                        failures=tuple(failures),
                        safety_paused=True,
                    )

                report_hash = self._current_report_hash()
                if report_hash is None or report_hash == last_seen_hash:
                    self.sleep_fn(self.config.polling_interval_seconds)
                    continue

                changes_seen += 1
                previous_hash = last_seen_hash
                self.sleep_fn(self.config.debounce_seconds)
                stable_hash = self._current_report_hash()
                if stable_hash is None:
                    last_seen_hash = None
                    continue
                if stable_hash != report_hash:
                    self._emit(
                        "foreground_bridge_report_change_debounced",
                        report_hash=stable_hash,
                        previous_hash=report_hash,
                    )
                    report_hash = stable_hash
                last_seen_hash = report_hash
                self._write_hash(self.last_seen_hash_path, report_hash)

                if report_hash == self._last_processed_report_hash():
                    self._emit(
                        "foreground_bridge_report_hash_ignored",
                        reason="already_processed",
                        report_hash=report_hash,
                    )
                    self._print("Report hash already processed; waiting for next change.")
                    continue

                report_text = self._read_report_text()
                marker_present = self.config.trigger_marker in report_text
                self._emit(
                    "foreground_bridge_trigger_marker_checked",
                    marker=self.config.trigger_marker,
                    marker_present=marker_present,
                    require_trigger_marker=self.config.require_trigger_marker,
                )
                if self.config.require_trigger_marker and not marker_present:
                    self._print(
                        "Report changed but trigger marker absent; "
                        "change ignored because --require-trigger-marker is enabled."
                    )
                    self._emit(
                        "foreground_bridge_report_ignored",
                        reason="trigger_marker_absent",
                        report_hash=report_hash,
                        startup_baseline_hash=startup_baseline_hash,
                        last_seen_report_hash=last_seen_hash,
                        last_processed_report_hash=self._last_processed_report_hash(),
                        report_title=self._report_title(),
                        report_length=len(report_text),
                        require_trigger_marker=self.config.require_trigger_marker,
                    )
                    continue

                active_bridge_attempt_id = self._new_bridge_attempt_id()
                self._emit(
                    "foreground_bridge_report_change_detected",
                    bridge_attempt_id=active_bridge_attempt_id,
                    report_hash=report_hash,
                    previous_hash=previous_hash,
                    startup_baseline_hash=startup_baseline_hash,
                    last_seen_report_hash=last_seen_hash,
                    last_processed_report_hash=self._last_processed_report_hash(),
                    report_title=self._report_title(),
                    report_length=len(report_text),
                    debounce_seconds=self.config.debounce_seconds,
                    cooldown_seconds=self.config.cooldown_seconds,
                    bridge_started=True,
                    trigger_marker_present=marker_present,
                    require_trigger_marker=self.config.require_trigger_marker,
                )
                if self.config.require_trigger_marker:
                    self._print("Report change detected with trigger marker; starting bridge.")
                else:
                    self._print(
                        "Report change detected; trigger marker not required; starting bridge."
                    )
                triggers_accepted += 1
                roundtrips_started += 1
                outcome = self._run_one_roundtrip(active_bridge_attempt_id)
                active_bridge_attempt_id = None
                if outcome is None:
                    roundtrips_started -= 1
                    roundtrips_skipped += 1
                    failures.append("lock unavailable")
                else:
                    if outcome.completed and not outcome.safety_paused:
                        roundtrips_completed += 1
                        self._write_hash(self.last_processed_hash_path, report_hash)
                    else:
                        roundtrips_failed += 1
                        failures.append(outcome.reason)
                    if outcome.safety_paused:
                        self._emit("foreground_bridge_runner_stopped", reason="SAFETY_PAUSE")
                        return ForegroundBridgeRunnerResult(
                            reason="SAFETY_PAUSE",
                            changes_seen=changes_seen,
                            triggers_accepted=triggers_accepted,
                            roundtrips_started=roundtrips_started,
                            roundtrips_completed=roundtrips_completed,
                            roundtrips_failed=roundtrips_failed,
                            roundtrips_interrupted=roundtrips_interrupted,
                            roundtrips_skipped=roundtrips_skipped,
                            failures=tuple(failures),
                            safety_paused=True,
                        )
                if self.config.max_roundtrips > 0 and roundtrips_started >= self.config.max_roundtrips:
                    self._emit("foreground_bridge_runner_stopped", reason="MAX_ROUNDTRIPS_REACHED")
                    self._print("Runner stopped: max roundtrips reached.")
                    return ForegroundBridgeRunnerResult(
                        reason="MAX_ROUNDTRIPS_REACHED",
                        changes_seen=changes_seen,
                        triggers_accepted=triggers_accepted,
                        roundtrips_started=roundtrips_started,
                        roundtrips_completed=roundtrips_completed,
                        roundtrips_failed=roundtrips_failed,
                        roundtrips_interrupted=roundtrips_interrupted,
                        roundtrips_skipped=roundtrips_skipped,
                        failures=tuple(failures),
                    )
                self.sleep_fn(self.config.cooldown_seconds)
                self._print("Waiting for next report change.")
        except KeyboardInterrupt:
            if active_bridge_attempt_id is not None:
                roundtrips_interrupted += 1
                self._emit(
                    "foreground_bridge_roundtrip_interrupted",
                    bridge_attempt_id=active_bridge_attempt_id,
                    reason="KEYBOARD_INTERRUPT",
                )
            self._emit(
                "foreground_bridge_runner_stopped",
                reason="KEYBOARD_INTERRUPT",
                active_bridge_attempt_id=active_bridge_attempt_id,
            )
            self._print("Runner stopped by KeyboardInterrupt.")
            return ForegroundBridgeRunnerResult(
                reason="KEYBOARD_INTERRUPT",
                changes_seen=changes_seen,
                triggers_accepted=triggers_accepted,
                roundtrips_started=roundtrips_started,
                roundtrips_completed=roundtrips_completed,
                roundtrips_failed=roundtrips_failed,
                roundtrips_interrupted=roundtrips_interrupted,
                roundtrips_skipped=roundtrips_skipped,
                failures=tuple(failures),
                keyboard_interrupt=True,
            )

    @property
    def _debug_state_machine_enabled(self) -> bool:
        return self.config.debug or self.config.debug_state_machine

    @property
    def _debug_gui_actions_enabled(self) -> bool:
        return self.config.debug or self.config.debug_gui_actions

    def _new_bridge_attempt_id(self) -> str:
        return f"bridge_{self.time_fn():.6f}".replace(".", "_")

    def _run_one_roundtrip(self, bridge_attempt_id: str) -> ReportRoundtripResult | None:
        lock = ExternalGuiRunnerLock(
            self.lock_path,
            stale_lock_seconds=self.config.stale_lock_seconds,
            time_fn=self.time_fn,
        )
        if not lock.acquire():
            self._emit(
                "foreground_bridge_roundtrip_skipped",
                bridge_attempt_id=bridge_attempt_id,
                reason="lock_unavailable",
            )
            self._print("Bridge skipped: lock unavailable.")
            return None
        self._emit(
            "foreground_bridge_lock_acquired",
            bridge_attempt_id=bridge_attempt_id,
            lock_path=str(self.lock_path),
        )
        try:
            try:
                runtime_targets = targets_with_pm_profile(
                    self.config.targets,
                    self.config.pm_target_profile,
                )
            except Exception as error:
                self._emit(
                    "foreground_bridge_pm_target_resolution_failed",
                    bridge_attempt_id=bridge_attempt_id,
                    pm_target=self.config.pm_target_profile,
                    error=str(error),
                )
                self._print(f"Bridge failed: PM target resolution failed: {error}")
                return ReportRoundtripResult(
                    completed=False,
                    reason=f"PM_TARGET_RESOLUTION_FAILED: {error}",
                )
            self._emit(
                "foreground_bridge_roundtrip_started",
                bridge_attempt_id=bridge_attempt_id,
                pm_target=self.config.pm_target_profile,
                pm_target_app=runtime_targets.pm_assistant.app_name,
                pm_target_bundle_id=runtime_targets.pm_assistant.bundle_id,
            )
            self._print(f"Bridge started with PM target: {self.config.pm_target_profile}")
            result = self.roundtrip_runner(
                ReportRoundtripConfig(
                    workspace_dir=self.config.workspace_dir,
                    template_dir=self.config.template_dir,
                    targets=runtime_targets,
                    auto_confirm=self.config.auto_confirm,
                    max_cycles=1,
                    max_runtime_seconds=self.config.roundtrip_max_runtime_seconds,
                    pm_response_timeout_seconds=self.config.pm_response_timeout_seconds,
                    require_pm_backend_preflight=(
                        runtime_targets.pm_assistant.require_backend_preflight
                    ),
                    submit_local_agent=True,
                    stop_after_local_agent_submit=self.config.stop_after_local_agent_submit,
                    wait_for_artifact_confirmation=self.config.wait_for_artifact_confirmation,
                    bridge_attempt_id=bridge_attempt_id,
                    debug_state_machine=self._debug_state_machine_enabled,
                    debug_gui_actions=self._debug_gui_actions_enabled,
                    debug_screenshots=self.config.debug_screenshots,
                    debug_all_template_comparisons=self.config.debug_all_template_comparisons,
                    debug_logs_dir=self.config.workspace_dir / "logs",
                    debug_output_fn=(
                        self._print
                        if self._debug_state_machine_enabled
                        or self._debug_gui_actions_enabled
                        else None
                    ),
                ),
                self.gui_factory(),
                CommandQueue(self.config.workspace_dir / "queue"),
                self.event_log,
                self.state_store,
            )
            if result.completed and not result.safety_paused:
                self._emit(
                    "foreground_bridge_roundtrip_completed",
                    bridge_attempt_id=bridge_attempt_id,
                    reason=result.reason,
                )
                self._print(f"Bridge completed: {result.reason}")
            else:
                self._emit(
                    "foreground_bridge_roundtrip_failed",
                    bridge_attempt_id=bridge_attempt_id,
                    reason=result.reason,
                )
                self._print(f"Bridge failed/stopped: {result.reason}")
            return result
        except Exception as error:
            self._emit(
                "foreground_bridge_roundtrip_failed",
                bridge_attempt_id=bridge_attempt_id,
                error=str(error),
            )
            self._print(f"Bridge failed: {error}")
            return ReportRoundtripResult(completed=False, reason=str(error))
        finally:
            lock.release()
            self._emit(
                "foreground_bridge_lock_released",
                bridge_attempt_id=bridge_attempt_id,
                lock_path=str(self.lock_path),
            )

    def _ensure_workspace(self) -> None:
        for subdir in ["logs", "state", "queue", "reports", "outbox"]:
            (self.config.workspace_dir / subdir).mkdir(parents=True, exist_ok=True)

    def _current_report_hash(self) -> str | None:
        try:
            return hashlib.sha256(self.config.watch_report_path.read_bytes()).hexdigest()
        except OSError:
            return None

    def _read_report_text(self) -> str:
        try:
            return self.config.watch_report_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _report_title(self) -> str | None:
        for line in self._read_report_text().splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return None

    def _last_seen_report_hash(self) -> str | None:
        return self._read_hash(self.last_seen_hash_path)

    def _last_processed_report_hash(self) -> str | None:
        return self._read_hash(self.last_processed_hash_path)

    def _read_hash(self, path: Path) -> str | None:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value or None

    def _write_hash(self, path: Path, value: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{value}\n", encoding="utf-8")

    def _emit(self, event_type: str, **metadata: object) -> None:
        self.event_log.append(event_type, **metadata)
        _write_runner_log(
            self.runner_log_path,
            f"{event_type} {metadata if metadata else ''}".rstrip(),
        )

    def _print(self, message: str) -> None:
        self.output_fn(message)


def run_bridge_preflight(
    *,
    workspace_dir: Path,
    targets: GuiTargets,
    pm_target_profile: str,
    config_dir: Path | None = None,
) -> BridgePreflightResult:
    checks: list[BridgePreflightCheck] = []
    try:
        runtime_pm_target = resolve_runtime_pm_target(targets, pm_target_profile)
        checks.append(
            BridgePreflightCheck(
                "pm_target_selection",
                True,
                (
                    f"Selected {runtime_pm_target.app_name} "
                    f"({runtime_pm_target.bundle_id or 'no bundle id'})."
                ),
            )
        )
    except Exception as error:
        fallback = pm_target_for_profile(targets.pm_assistant, pm_target_profile)
        checks.append(BridgePreflightCheck("pm_target_selection", False, str(error)))
        return BridgePreflightResult(pm_target_profile, fallback, tuple(checks))

    checks.append(
        BridgePreflightCheck(
            "clipboard_tools",
            bool(shutil.which("pbcopy") and shutil.which("pbpaste")),
            "pbcopy/pbpaste available."
            if shutil.which("pbcopy") and shutil.which("pbpaste")
            else "pbcopy or pbpaste missing.",
        )
    )
    checks.append(
        BridgePreflightCheck(
            "pyautogui",
            importlib.util.find_spec("pyautogui") is not None,
            "pyautogui import spec found."
            if importlib.util.find_spec("pyautogui") is not None
            else "pyautogui is unavailable.",
        )
    )
    queue_dir = workspace_dir / "queue"
    checks.append(
        BridgePreflightCheck(
            "command_queue_directories",
            queue_dir.exists(),
            f"Queue directory exists: {queue_dir}" if queue_dir.exists() else f"Missing: {queue_dir}",
        )
    )
    if config_dir is not None:
        config_path = config_dir / "default.yaml"
        checks.append(
            BridgePreflightCheck(
                "config_readable",
                config_path.exists(),
                f"Readable config: {config_path}" if config_path.exists() else f"Missing: {config_path}",
            )
        )

    activator = MacOSAppActivator()
    window_detector = CodexUIDetector()
    visual_detector = AssetVisualStateDetector()
    try:
        activator.activate(
            runtime_pm_target.app_name,
            app_path=runtime_pm_target.app_path,
            bundle_id=runtime_pm_target.bundle_id,
        )
        pm_window = window_detector.select_main_window(runtime_pm_target)
        pm_state = visual_detector.detect(
            target=runtime_pm_target,
            window_bounds=pm_window.selected_bounds,
            profile=asset_profile_for_target(runtime_pm_target),
        )
        checks.append(
            BridgePreflightCheck(
                "pm_visual_state",
                pm_state.screenshot_captured and pm_state.backend_available,
                f"state={pm_state.matched_state.value}, bounds={pm_state.window_bounds}",
            )
        )
        response_capture = diagnose_chatgpt_mac_response_capture(
            target=runtime_pm_target,
            window_bounds=pm_window.selected_bounds,
            logs_dir=workspace_dir / "logs",
            write_debug=True,
            attempt_copy=False,
        )
        checks.append(
            BridgePreflightCheck(
                "pm_response_copy",
                (
                    response_capture.screenshot_captured
                    and response_capture.backend_available
                    and not response_capture.missing_copy_assets
                ),
                (
                    f"copy_button_found={response_capture.copy_button_found}, "
                    f"supported={response_capture.supported}, error={response_capture.error or 'none'}"
                ),
            )
        )
    except Exception as error:
        checks.append(BridgePreflightCheck("pm_visual_preflight", False, str(error)))

    try:
        local_target = targets.local_agent
        activator.activate(
            local_target.app_name,
            app_path=local_target.app_path,
            bundle_id=local_target.bundle_id,
        )
        codex_window = window_detector.select_main_window(local_target)
        codex_state = visual_detector.detect(
            target=local_target,
            window_bounds=codex_window.selected_bounds,
            profile=asset_profile_for_target(local_target),
        )
        checks.append(
            BridgePreflightCheck(
                "codex_visual_state",
                codex_state.screenshot_captured and codex_state.backend_available,
                f"state={codex_state.matched_state.value}, bounds={codex_state.window_bounds}",
            )
        )
    except Exception as error:
        checks.append(BridgePreflightCheck("codex_visual_state", False, str(error)))

    return BridgePreflightResult(pm_target_profile, runtime_pm_target, tuple(checks))


def format_bridge_preflight(result: BridgePreflightResult) -> str:
    lines = [
        "# Foreground Bridge Runner Preflight",
        "",
        f"PM target profile: {result.pm_target_profile}",
        f"PM target app: {result.pm_target.app_name}",
        f"PM target bundle id: {result.pm_target.bundle_id or 'unspecified'}",
        f"Result: {'passed' if result.succeeded else 'failed'}",
        "",
        "## Checks",
    ]
    for check in result.checks:
        status = "ok" if check.succeeded else "failed"
        lines.append(f"- {check.name}: {status}")
        lines.append(f"  {check.detail}")
    lines.append("")
    lines.append("No PM prompt, Codex prompt, GitHub, or Gmail action was submitted.")
    return "\n".join(lines)
