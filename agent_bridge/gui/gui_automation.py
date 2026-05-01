from __future__ import annotations

import hashlib
import subprocess
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from agent_bridge.core.event_log import EventLog
from agent_bridge.gui.chatgpt_state_machine import (
    ChatGPTStateMachineError,
    MacOSChromeJavaScriptDomClient,
    ResponseCopySelectors,
    copy_response_with_strategies,
    insert_text_into_composer,
    wait_for_idle_empty_composer,
    wait_for_response_copy_ready,
    wait_for_send_ready,
    click_send_button,
)
from agent_bridge.gui.chatgpt_mac_response_capture import (
    diagnose_chatgpt_mac_response_capture,
)
from agent_bridge.gui.clipboard import Clipboard, MacOSClipboard
from agent_bridge.gui.asset_state_machine import (
    AssetVisualStateDetector,
    VisualAssetKind,
    VisualAssetMatch,
    VisualGuiState,
    VisualIdleWaitResult,
    VisualStateDetection,
    asset_profile_for_target,
    wait_for_visual_idle,
)
from agent_bridge.gui.codex_ui_detector import (
    CodexUIDetector,
    LocalAgentFocusResult,
    LocalAgentPostSubmitCheck,
    LocalAgentPreSubmitCheck,
)
from agent_bridge.gui.macos_apps import (
    AppActivator,
    MacOSAppActivator,
    ManualStageTarget,
)
from agent_bridge.gui.visual_pm_controller import (
    collect_visual_pm_asset_inventory,
    is_visual_pm_target,
    normalize_visual_pm_target,
    visual_pm_asset_directory,
)


class GuiAutomationError(RuntimeError):
    pass


@dataclass(frozen=True)
class PMPasteContentVerification:
    verified: bool
    method: str
    copied_text_length: int | None
    copied_text_hash: str | None
    raw_key_leak_suspected: bool
    sentinel_id: str | None = None
    sentinel_hash: str | None = None
    sentinel_found: bool | None = None
    failure_reason: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class PMSubmitReadyCheck:
    ready: bool
    decision_reason: str
    global_state: str | None = None
    global_state_ambiguous: bool = False
    matched_asset_path: str | None = None
    confidence: float | None = None
    configured_threshold: float | None = None
    effective_threshold: float | None = None
    bbox: tuple[int, int, int, int] | None = None
    click_point: tuple[int, int] | None = None
    click_point_safe: bool = False
    stop_candidate_asset_path: str | None = None
    stop_candidate_confidence: float | None = None
    stop_candidate_bbox: tuple[int, int, int, int] | None = None


class GuiAutomationAdapter:
    def activate_app(self, target: ManualStageTarget) -> None:
        raise NotImplementedError

    def copy_text_to_clipboard(self, text: str) -> None:
        raise NotImplementedError

    def paste_clipboard(self) -> bool | None:
        raise NotImplementedError

    def submit(self) -> None:
        raise NotImplementedError

    def wait_for_response(self, timeout_seconds: int) -> None:
        raise NotImplementedError

    def copy_response_text(self) -> str:
        raise NotImplementedError

    def read_clipboard_text(self) -> str | None:
        return None

    def expect_response_contains(self, marker: str | None) -> None:
        return None

    def set_local_agent_queue_handoff_mode(self, enabled: bool) -> None:
        return None

    def inspect_local_agent_before_submit(
        self,
        target: ManualStageTarget,
        prompt: str,
    ) -> LocalAgentPreSubmitCheck:
        return LocalAgentPreSubmitCheck(
            target_app=target.app_name,
            prompt_length=len(prompt),
            prompt_text_present=None,
        )

    def inspect_local_agent_after_submit(
        self,
        target: ManualStageTarget,
        prompt: str,
        before: LocalAgentPreSubmitCheck,
    ) -> LocalAgentPostSubmitCheck:
        return LocalAgentPostSubmitCheck(
            active_app_before=before.active_app,
            focused_text_length_before=before.focused_text_length,
            confirmed=None,
        )


@dataclass
class MacOSSystemEventsGuiAdapter(GuiAutomationAdapter):
    clipboard: Clipboard | None = None
    app_activator: AppActivator | None = None
    codex_ui_detector: CodexUIDetector | None = None
    asset_state_detector: AssetVisualStateDetector | None = None
    osascript_executable: str = "osascript"
    sleep_fn: Callable[[float], None] = time.sleep
    event_log: EventLog | None = None
    debug_state_machine_log: EventLog | None = None
    debug_gui_actions_log: EventLog | None = None
    debug_output_fn: Callable[[str], None] | None = None
    debug_all_template_comparisons: bool = False
    bridge_attempt_id: str | None = None
    debug_screenshots: bool = False
    debug_logs_dir: object | None = None
    active_target: ManualStageTarget | None = None
    response_expected_marker: str | None = None
    state_timeout_seconds: float = 10
    local_agent_frontmost_timeout_seconds: float = 5
    last_local_agent_focus_result: LocalAgentFocusResult | None = None
    pyautogui_hotkeyer: Callable[..., None] | None = None
    local_agent_queue_handoff_mode: bool = False
    local_agent_max_paste_attempts: int = 3
    local_agent_paste_retry_delay_seconds: float = 0.5
    last_local_agent_paste_send_ready: bool | None = None
    last_local_agent_paste_state_before: str | None = None
    last_local_agent_paste_state_after: str | None = None
    last_local_agent_paste_state_after_confidence: float | None = None
    last_local_agent_paste_state_after_asset: str | None = None
    last_local_agent_paste_attempted: bool = False
    last_local_agent_paste_backend_success: bool = False
    last_local_agent_paste_failure_reason: str | None = None
    last_local_agent_clipboard_readback_matches_prompt_hash: bool | None = None
    last_pm_paste_send_ready: bool | None = None
    last_pm_paste_state_before: str | None = None
    last_pm_paste_state_after: str | None = None
    last_pm_paste_attempted: bool = False
    last_pm_paste_backend_success: bool = False
    last_pm_paste_failure_reason: str | None = None
    last_pm_submit_ready_check: PMSubmitReadyCheck | None = None
    last_pm_raw_v_failure_detected: bool = False
    last_pm_paste_content_verified: bool | None = None
    last_pm_paste_content_verification_method: str | None = None
    last_pm_paste_copied_back_length: int | None = None
    last_pm_paste_copied_back_hash: str | None = None
    last_pm_prompt_sentinel_id: str | None = None
    last_pm_prompt_sentinel_hash: str | None = None
    last_pm_prompt_sentinel_found: bool | None = None
    last_pm_clipboard_set_attempted: bool = False
    last_pm_clipboard_set_succeeded: bool = False
    last_pm_clipboard_readback_matches_prompt_hash: bool | None = None
    state_machine_previous_states: dict[str, str] = field(default_factory=dict)
    visual_pm_asset_inventory_logged: set[str] = field(default_factory=set)
    last_visual_window_bounds: dict[str, tuple[int, int, int, int] | None] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        if self.clipboard is None:
            self.clipboard = MacOSClipboard()
        if self.app_activator is None:
            self.app_activator = MacOSAppActivator()
        if self.codex_ui_detector is None:
            self.codex_ui_detector = CodexUIDetector(
                osascript_executable=self.osascript_executable,
                sleep_fn=self.sleep_fn,
            )
        if self.asset_state_detector is None:
            self.asset_state_detector = AssetVisualStateDetector()

    def _run_system_events(self, script: str) -> None:
        try:
            completed = subprocess.run(
                [self.osascript_executable, "-e", script],
                check=False,
                text=True,
                capture_output=True,
                timeout=5,
            )
        except subprocess.TimeoutExpired as error:
            raise GuiAutomationError("macOS GUI automation timed out.") from error
        if completed.returncode != 0:
            raise GuiAutomationError(completed.stderr.strip() or "macOS GUI automation failed.")

    def _run_system_events_capture(self, script: str) -> str:
        try:
            completed = subprocess.run(
                [self.osascript_executable, "-e", script],
                check=False,
                text=True,
                capture_output=True,
                timeout=5,
            )
        except subprocess.TimeoutExpired as error:
            raise GuiAutomationError("macOS GUI automation timed out.") from error
        if completed.returncode != 0:
            raise GuiAutomationError(completed.stderr.strip() or "macOS GUI automation failed.")
        return completed.stdout.strip()

    def _click_accessibility_menu_item(
        self,
        target: ManualStageTarget,
        *,
        item_names: tuple[str, ...],
        menu_names: tuple[str, ...] = ("Edit", "편집", "수정"),
    ) -> str:
        process_selector = _system_events_process_selector(target)
        script = f"""
tell application "System Events"
  set targetProcess to {process_selector}
  tell targetProcess
    set frontmost to true
    repeat with menuName in {_applescript_list(menu_names)}
      repeat with itemName in {_applescript_list(item_names)}
        try
          click menu item (itemName as text) of menu (menuName as text) of menu bar 1
          return (menuName as text) & tab & (itemName as text)
        end try
      end repeat
    end repeat
  end tell
end tell
error "menu item unavailable"
""".strip()
        return self._run_system_events_capture(script)

    def _append_event(self, event_type: str, metadata: dict[str, object]) -> None:
        if self.event_log:
            self.event_log.append(event_type, **metadata)
        if self.debug_state_machine_log:
            debug_metadata = dict(metadata)
            state_value = (
                debug_metadata.get("observed_state")
                or debug_metadata.get("final_state")
                or debug_metadata.get("matched_state")
            )
            if state_value is not None:
                detected_state = str(state_value)
                app_name = str(debug_metadata.get("app_name") or debug_metadata.get("app") or "unknown")
                profile = str(debug_metadata.get("profile") or "unknown")
                state_key = f"event:{app_name}:{profile}:{event_type}"
                previous_state = self.state_machine_previous_states.get(state_key)
                self.state_machine_previous_states[state_key] = detected_state
                debug_metadata.setdefault("previous_state", previous_state)
                debug_metadata.setdefault("detected_state", detected_state)
                debug_metadata.setdefault("selected_state", detected_state)
                debug_metadata.setdefault(
                    "transition",
                    f"{previous_state or 'START'}->{detected_state}",
                )
                if event_type == "asset_visual_idle_wait_poll":
                    debug_metadata.setdefault(
                        "decision",
                        "proceed" if detected_state == VisualGuiState.IDLE.value else "wait",
                    )
                elif event_type == "asset_visual_idle_wait_timeout":
                    debug_metadata.setdefault("decision", debug_metadata.get("action") or "timeout")
                elif event_type == "asset_visual_idle_detected":
                    debug_metadata.setdefault("decision", "proceed")
            self.debug_state_machine_log.append(
                event_type,
                bridge_attempt_id=self.bridge_attempt_id,
                phase="visual_state_machine",
                result="observed",
                **debug_metadata,
            )

    def _append_action_debug(
        self,
        *,
        phase: str,
        action: str,
        result: str,
        **metadata: object,
    ) -> None:
        if self.debug_gui_actions_log:
            self.debug_gui_actions_log.append(
                "gui_action",
                bridge_attempt_id=self.bridge_attempt_id,
                phase=phase,
                action=action,
                result=result,
                **metadata,
            )

    def _emit_terminal_debug(self, target: ManualStageTarget, message: str) -> None:
        if self.debug_output_fn is None:
            return
        profile = target.profile or target.visual_asset_profile or "unknown"
        bridge_attempt_id = self.bridge_attempt_id or "bridge_unknown"
        self.debug_output_fn(f"[{bridge_attempt_id}] PM {profile}: {message}")

    def _visual_window_key(self, target: ManualStageTarget) -> str:
        return ":".join(
            part
            for part in (
                target.profile or target.visual_asset_profile or "unknown",
                target.bundle_id or target.app_name,
            )
            if part
        )

    def _log_window_bounds_refresh(
        self,
        *,
        target: ManualStageTarget,
        operation: str,
        old_bounds: tuple[int, int, int, int] | None,
        new_bounds: tuple[int, int, int, int] | None,
    ) -> None:
        changed = old_bounds != new_bounds
        if self.debug_output_fn is not None:
            self._emit_terminal_debug(
                target,
                (
                    f"window_bounds_checked operation={operation} "
                    f"changed={'yes' if changed else 'no'} "
                    f"old={old_bounds or 'unavailable'} "
                    f"new={new_bounds or 'unavailable'} "
                    "screenshot_recaptured=yes search_regions_recomputed=yes "
                    "stale_coordinate_reused=no"
                ),
            )
        if self.debug_state_machine_log:
            self.debug_state_machine_log.append(
                "gui_window_bounds_checked",
                bridge_attempt_id=self.bridge_attempt_id,
                phase="window_refresh",
                app=target.app_name,
                profile=target.profile or target.visual_asset_profile,
                bundle_id=target.bundle_id,
                operation=operation,
                result="changed" if changed else "unchanged",
                window_bounds_checked=True,
                window_bounds_changed=changed,
                old_bounds=old_bounds,
                new_bounds=new_bounds,
                screenshot_recaptured=True,
                search_regions_recomputed=True,
                stale_coordinate_reused=False,
            )

    def _visual_debug_enabled(self) -> bool:
        return bool(
            self.debug_state_machine_log
            or self.debug_gui_actions_log
            or self.debug_output_fn
        )

    def _log_visual_pm_asset_inventory_once(self, target: ManualStageTarget) -> None:
        if not is_visual_pm_target(target) or not self._visual_debug_enabled():
            return
        profile = target.visual_asset_profile or target.profile or ""
        key = f"{profile}:{target.bundle_id or target.app_name}"
        if key in self.visual_pm_asset_inventory_logged:
            return
        self.visual_pm_asset_inventory_logged.add(key)
        asset_dir = visual_pm_asset_directory(profile)
        inventory = collect_visual_pm_asset_inventory(profile)
        missing = [item.path for item in inventory if not item.exists]
        inventory_payload = [
            {
                "role": item.role,
                "path": item.path,
                "exists": item.exists,
                "image_size": item.image_size,
                "error": item.error,
            }
            for item in inventory
        ]
        result = "failed" if missing else "succeeded"
        self._append_action_debug(
            phase="pm_visual_assets",
            action="pm_visual_asset_inventory",
            result=result,
            app=target.app_name,
            profile=profile,
            backend=target.backend,
            bundle_id=target.bundle_id,
            asset_directory=asset_dir,
            assets=inventory_payload,
            missing_assets=missing,
        )
        if self.event_log:
            self.event_log.append(
                "pm_visual_asset_inventory",
                app=target.app_name,
                profile=profile,
                backend=target.backend,
                bundle_id=target.bundle_id,
                asset_directory=asset_dir,
                missing_assets=missing,
            )
        self._emit_terminal_debug(
            target,
            f"asset inventory profile={profile} dir={asset_dir} missing={len(missing)}",
        )
        for item in inventory:
            size = f"{item.image_size[0]}x{item.image_size[1]}" if item.image_size else "unknown"
            status = "ok" if item.exists and not item.error else "missing" if not item.exists else "error"
            self._emit_terminal_debug(
                target,
                f"asset {item.role} {status} path={item.path} size={size}",
            )
        if profile == "chatgpt_mac" and missing:
            raise GuiAutomationError(f"chatgpt_mac_asset_missing: {', '.join(missing)}")

    def _append_template_detection_debug(
        self,
        *,
        target: ManualStageTarget,
        detection,
        attempt_index: int,
        max_attempts: int,
    ) -> None:
        if not self.debug_state_machine_log and self.debug_output_fn is None:
            return
        if self.debug_output_fn is not None:
            self._emit_terminal_template_detection_debug(
                target=target,
                detection=detection,
            )
        for diagnostic in detection.template_diagnostics:
            state_name = (
                diagnostic.state.value if diagnostic.state else "COMPOSER_ANCHOR"
            )
            operation = (
                "detect_plus_anchor"
                if diagnostic.asset_kind.value == "plus"
                else "detect_visual_state"
            )
            if self.debug_state_machine_log:
                self._append_state_debug(
                    phase="visual_detect",
                    app=target.app_name,
                    profile=detection.asset_profile,
                    bundle_id=target.bundle_id,
                    state_name=state_name,
                    result="accepted" if diagnostic.accepted else "rejected",
                    operation=operation,
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                    selected_window_bounds=detection.window_bounds,
                    screenshot_captured=detection.screenshot_captured,
                    screenshot_path=detection.screenshot_path,
                    search_region_bounds=diagnostic.search_region_bounds,
                    template_role=diagnostic.asset_kind.value,
                    template_path=diagnostic.template_path,
                    template_exists=diagnostic.template_exists,
                    original_template_size=diagnostic.original_template_size,
                    scaled_template_size=diagnostic.template_size,
                    selected_scale=diagnostic.selected_scale,
                    best_match_bbox=diagnostic.best_match_bbox,
                    confidence=diagnostic.best_match_confidence,
                    configured_threshold=(
                        diagnostic.configured_threshold
                        if diagnostic.configured_threshold is not None
                        else diagnostic.threshold
                    ),
                    effective_threshold=(
                        diagnostic.effective_threshold
                        if diagnostic.effective_threshold is not None
                        else diagnostic.threshold
                    ),
                    threshold_cap_applied=diagnostic.threshold_cap_applied,
                    appearance_score=diagnostic.appearance_score,
                    edge_score=getattr(diagnostic, "edge_score", None),
                    glyph_score=getattr(diagnostic, "glyph_score", None),
                    composite_score=getattr(diagnostic, "composite_score", None),
                    score_gap_to_next_best=getattr(
                        diagnostic, "score_gap_to_next_best", None
                    ),
                    accepted=diagnostic.accepted,
                    rejection_reason=diagnostic.rejection_reason,
                    selected_state=detection.matched_state.value,
                    plus_anchor_found=detection.plus_anchor_found,
                    error=detection.error,
                )

    def _emit_terminal_template_detection_debug(
        self,
        *,
        target: ManualStageTarget,
        detection,
    ) -> None:
        diagnostics = tuple(detection.template_diagnostics)
        if not diagnostics:
            return
        if self.debug_all_template_comparisons:
            for diagnostic in diagnostics:
                self._emit_terminal_debug(
                    target,
                    self._terminal_template_compare_line(diagnostic),
                )
            return

        for diagnostic in diagnostics:
            if diagnostic.accepted:
                self._emit_terminal_debug(
                    target,
                    self._terminal_template_accepted_line(diagnostic),
                )

        groups = (
            ("plus", [item for item in diagnostics if item.asset_kind.value == "plus"]),
            ("state", [item for item in diagnostics if item.asset_kind.value != "plus"]),
        )
        for label, group in groups:
            if not group or any(item.accepted for item in group):
                continue
            best = max(
                group,
                key=lambda item: (
                    getattr(item, "composite_score", None)
                    if getattr(item, "composite_score", None) is not None
                    else item.best_match_confidence
                    if item.best_match_confidence is not None
                    else -1.0
                ),
            )
            self._emit_terminal_debug(
                target,
                self._terminal_template_no_candidate_line(label, best),
            )

    def _terminal_template_accepted_line(self, diagnostic) -> str:
        state_text = (
            f" state={diagnostic.state.value}"
            if diagnostic.state is not None
            else ""
        )
        return (
            f"accepted {diagnostic.asset_kind.value} "
            f"{Path(diagnostic.template_path).name} "
            f"raw={self._debug_float(diagnostic.best_match_confidence)} "
            f"edge={self._debug_float(getattr(diagnostic, 'edge_score', None))} "
            f"glyph={self._debug_float(getattr(diagnostic, 'glyph_score', None))} "
            f"appearance={self._debug_float(diagnostic.appearance_score)} "
            f"composite={self._debug_float(getattr(diagnostic, 'composite_score', None))} "
            f"configured={self._debug_float(getattr(diagnostic, 'configured_threshold', None) or diagnostic.threshold)} "
            f"effective={self._debug_float(getattr(diagnostic, 'effective_threshold', None) or diagnostic.threshold)} "
            f"threshold={self._debug_float(diagnostic.threshold)}"
            f"{state_text}"
        )

    def _terminal_template_no_candidate_line(self, label: str, diagnostic) -> str:
        reason = diagnostic.rejection_reason or "not_accepted"
        message = (
            f"no {label} candidate passed threshold; "
            f"best={diagnostic.asset_kind.value} {Path(diagnostic.template_path).name} "
            f"raw={self._debug_float(diagnostic.best_match_confidence)} "
            f"edge={self._debug_float(getattr(diagnostic, 'edge_score', None))} "
            f"glyph={self._debug_float(getattr(diagnostic, 'glyph_score', None))} "
            f"appearance={self._debug_float(diagnostic.appearance_score)} "
            f"composite={self._debug_float(getattr(diagnostic, 'composite_score', None))} "
            f"configured={self._debug_float(getattr(diagnostic, 'configured_threshold', None) or diagnostic.threshold)} "
            f"effective={self._debug_float(getattr(diagnostic, 'effective_threshold', None) or diagnostic.threshold)} "
            f"threshold={self._debug_float(diagnostic.threshold)} "
            f"reason={reason}"
        )
        if label == "plus":
            message += "; plus asset may be stale or search region may be wrong"
        return message

    def _terminal_template_compare_line(self, diagnostic) -> str:
        return (
            f"compare {diagnostic.asset_kind.value} "
            f"{Path(diagnostic.template_path).name} "
            f"raw={self._debug_float(diagnostic.best_match_confidence)} "
            f"edge={self._debug_float(getattr(diagnostic, 'edge_score', None))} "
            f"glyph={self._debug_float(getattr(diagnostic, 'glyph_score', None))} "
            f"appearance={self._debug_float(diagnostic.appearance_score)} "
            f"composite={self._debug_float(getattr(diagnostic, 'composite_score', None))} "
            f"configured={self._debug_float(getattr(diagnostic, 'configured_threshold', None) or diagnostic.threshold)} "
            f"effective={self._debug_float(getattr(diagnostic, 'effective_threshold', None) or diagnostic.threshold)} "
            f"threshold={self._debug_float(diagnostic.threshold)} "
            f"accepted={'yes' if diagnostic.accepted else 'no'}"
            + (f" reason={diagnostic.rejection_reason}" if diagnostic.rejection_reason else "")
        )

    @staticmethod
    def _debug_float(value: float | None) -> str:
        return f"{value:.3f}" if value is not None else "unavailable"

    def _append_state_debug(
        self,
        *,
        phase: str,
        state_name: str,
        result: str,
        **metadata: object,
    ) -> None:
        if self.debug_state_machine_log:
            self.debug_state_machine_log.append(
                "gui_state_detection",
                bridge_attempt_id=self.bridge_attempt_id,
                phase=phase,
                state_name=state_name,
                result=result,
                **metadata,
            )

    def _action_max_attempts(self, target: ManualStageTarget) -> int:
        return max(1, int(getattr(target, "max_action_attempts", 3) or 3))

    def _action_retry_delay_seconds(self, target: ManualStageTarget) -> float:
        return max(0.0, float(getattr(target, "action_retry_delay_seconds", 0.5) or 0.0))

    def _submit_after_paste_max_attempts(self, target: ManualStageTarget) -> int:
        return max(1, int(getattr(target, "submit_after_paste_max_attempts", 100) or 100))

    def _state_machine_max_attempts(self, target: ManualStageTarget) -> int:
        return max(1, int(getattr(target, "max_state_machine_attempts", 3) or 3))

    def _state_machine_retry_delay_seconds(self, target: ManualStageTarget) -> float:
        return max(
            0.0,
            float(getattr(target, "state_machine_retry_delay_seconds", 0.5) or 0.0),
        )

    def activate_app(self, target: ManualStageTarget) -> None:
        if is_visual_pm_target(target):
            target = normalize_visual_pm_target(target)
        if self.app_activator is None:
            raise GuiAutomationError("App activator is not configured.")
        max_attempts = self._action_max_attempts(target)
        last_error: Exception | None = None
        for attempt_index in range(1, max_attempts + 1):
            self._append_action_debug(
                phase="activate_app",
                action="activate_app",
                result="attempted",
                app=target.app_name,
                profile=target.profile,
                bundle_id=target.bundle_id,
                attempt_index=attempt_index,
                max_attempts=max_attempts,
            )
            try:
                self.app_activator.activate(
                    target.app_name,
                    app_path=target.app_path,
                    bundle_id=target.bundle_id,
                )
                self.active_target = target
                if self._is_codex_target(target):
                    if self.codex_ui_detector is None:
                        raise GuiAutomationError("Codex UI detector is not configured.")
                    if not self.codex_ui_detector.wait_until_frontmost(
                        target,
                        timeout_seconds=self.local_agent_frontmost_timeout_seconds,
                    ):
                        active_app = self.codex_ui_detector.frontmost_app() or "unknown"
                        raise GuiAutomationError(
                            "Codex app did not become frontmost after activation; "
                            f"active app is {active_app}."
                        )
            except Exception as error:
                last_error = error
                self._append_action_debug(
                    phase="activate_app",
                    action="activate_app",
                    result="failed",
                    app=target.app_name,
                    profile=target.profile,
                    bundle_id=target.bundle_id,
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                    error=str(error),
                )
                if attempt_index >= max_attempts:
                    raise
                self.sleep_fn(self._action_retry_delay_seconds(target))
                continue
            self._append_action_debug(
                phase="activate_app",
                action="activate_app",
                result="succeeded",
                app=target.app_name,
                profile=target.profile,
                bundle_id=target.bundle_id,
                attempt_index=attempt_index,
                max_attempts=max_attempts,
            )
            return
        if last_error is not None:
            raise GuiAutomationError(str(last_error))

    def copy_text_to_clipboard(self, text: str) -> None:
        if self.clipboard is None:
            raise GuiAutomationError("Clipboard is not configured.")
        self._append_action_debug(
            phase="clipboard",
            action="set_clipboard",
            result="attempted",
            prompt_length=len(text),
        )
        self.clipboard.copy_text(text)
        self._append_action_debug(
            phase="clipboard",
            action="set_clipboard",
            result="succeeded",
            prompt_length=len(text),
        )

    def read_clipboard_text(self) -> str | None:
        if self.clipboard is None:
            return None
        return self.clipboard.read_text()

    def paste_clipboard(self) -> bool | None:
        if self._is_chatgpt_asset_target():
            if self.clipboard is None:
                raise GuiAutomationError("Clipboard is not configured.")
            if self.active_target is None:
                raise GuiAutomationError("No active visual PM target is configured.")
            return self._paste_pm_asset_clipboard_with_retry(self.active_target)
        if self._is_chatgpt_target():
            if self.clipboard is None:
                raise GuiAutomationError("Clipboard is not configured.")
            text = self.clipboard.read_text()
            marker = self._expected_composer_marker(text)
            try:
                wait_for_idle_empty_composer(
                    self._chatgpt_dom_client(),
                    timeout_seconds=self._idle_empty_timeout_seconds(),
                    poll_interval_seconds=self._idle_empty_poll_interval_seconds(),
                    sleep_fn=self.sleep_fn,
                    event_log=self.event_log,
                )
                verification = insert_text_into_composer(
                    self._chatgpt_dom_client(),
                    text,
                    expected_marker=marker,
                    event_log=self.event_log,
                )
            except ChatGPTStateMachineError as error:
                raise GuiAutomationError(str(error)) from error
            if self.event_log:
                self.event_log.append(
                    "pm_prompt_inserted",
                    text_length=verification.text_length,
                    selector=verification.selector,
                    button_state=verification.button_state,
                )
            return True
        if self.active_target is not None and self._is_codex_target(self.active_target):
            if self.codex_ui_detector is None:
                raise GuiAutomationError("Codex UI detector is not configured.")
            target = self.active_target
            if self.event_log:
                self.event_log.append("codex_input_discovery_started", app_name=target.app_name)
            return self._paste_local_agent_clipboard_with_retry(target)
        self._run_system_events('tell application "System Events" to keystroke "v" using command down')
        return True

    def _paste_with_backend(self, target: ManualStageTarget) -> None:
        backend = _normalize_backend(target.paste_backend)
        if backend in {"pyautogui", "pyautogui_hotkey_command_v", "command_v_hotkey"}:
            try:
                if self.pyautogui_hotkeyer is not None:
                    self.pyautogui_hotkeyer("command", "v")
                else:
                    import pyautogui

                    pyautogui.hotkey("command", "v", interval=0.1)
            except ModuleNotFoundError as error:
                raise GuiAutomationError(
                    "PyAutoGUI paste backend is unavailable: pyautogui is not installed."
                ) from error
            except Exception as error:
                raise GuiAutomationError(f"PyAutoGUI paste failed: {error}") from error
            return
        if backend in {"pyautogui_hotkey_cmd_v", "cmd_v_hotkey"}:
            try:
                if self.pyautogui_hotkeyer is not None:
                    self.pyautogui_hotkeyer("cmd", "v")
                else:
                    import pyautogui

                    pyautogui.hotkey("cmd", "v", interval=0.1)
            except ModuleNotFoundError as error:
                raise GuiAutomationError(
                    "PyAutoGUI paste backend is unavailable: pyautogui is not installed."
                ) from error
            except Exception as error:
                raise GuiAutomationError(f"PyAutoGUI paste failed: {error}") from error
            return
        if backend in {"system_events", "system_events_command_v"}:
            self._run_system_events('tell application "System Events" to keystroke "v" using command down')
            return
        if backend in {"system_events_key_code_v_command", "system_events_keycode_command_v"}:
            self._run_system_events('tell application "System Events" to key code 9 using command down')
            return
        if backend in {"menu_paste_accessibility", "accessibility_menu_paste", "menu_paste"}:
            self._click_accessibility_menu_item(target, item_names=("Paste", "붙이기"))
            return
        if backend in {"accessibility_set_focused_value", "accessibility_set_value"}:
            self._set_focused_accessibility_value_from_clipboard(target)
            return
        raise GuiAutomationError(f"Unsupported paste backend: {backend}")

    def _paste_pm_asset_clipboard_with_retry(self, target: ManualStageTarget) -> bool:
        if self.clipboard is None:
            raise GuiAutomationError("Clipboard is not configured.")
        prompt = self.clipboard.read_text()
        prompt_length = len(prompt)
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if prompt_length <= 0:
            self.last_pm_paste_failure_reason = "pm_prompt_empty"
            raise GuiAutomationError("pm_prompt_empty")

        self.last_pm_paste_send_ready = None
        self.last_pm_paste_state_before = None
        self.last_pm_paste_state_after = None
        self.last_pm_paste_attempted = False
        self.last_pm_paste_backend_success = False
        self.last_pm_paste_failure_reason = None
        self.last_pm_submit_ready_check = None
        self.last_pm_clipboard_set_attempted = False
        self.last_pm_clipboard_set_succeeded = False
        self.last_pm_clipboard_readback_matches_prompt_hash = None
        self.last_pm_raw_v_failure_detected = False
        self.last_pm_paste_content_verified = None
        self.last_pm_paste_content_verification_method = None
        self.last_pm_paste_copied_back_length = None
        self.last_pm_paste_copied_back_hash = None
        self.last_pm_prompt_sentinel_id = None
        self.last_pm_prompt_sentinel_hash = None
        self.last_pm_prompt_sentinel_found = None

        max_attempts = self._action_max_attempts(target)
        retry_delay = self._action_retry_delay_seconds(target)
        backend_chain = pm_paste_backends_for_target(target)
        self._append_action_debug(
            phase="pm_paste",
            app=target.app_name,
            profile=target.profile,
            action="pm_prompt_loaded",
            result="succeeded",
            prompt_length=prompt_length,
            prompt_hash=prompt_hash,
        )
        self._append_action_debug(
            phase="pm_paste",
            app=target.app_name,
            profile=target.profile,
            action="pm_prompt_hash_computed",
            result="succeeded",
            prompt_length=prompt_length,
            prompt_hash=prompt_hash,
        )
        self._append_action_debug(
            phase="pm_paste",
            app=target.app_name,
            profile=target.profile,
            action="pm_paste_retry_started",
            result="started",
            max_attempts=max_attempts,
            paste_backend_chain=backend_chain,
            prompt_length=prompt_length,
            prompt_hash=prompt_hash,
        )
        if self.event_log:
            self.event_log.append(
                "pm_paste_retry_started",
                max_attempts=max_attempts,
                paste_backend_chain=backend_chain,
                pm_prompt_length=prompt_length,
                pm_prompt_hash=prompt_hash,
            )

        final_state: VisualGuiState | None = None
        final_reason = "pm_paste_not_reflected_in_pm_state"
        focus_detection = None
        detection_before = None
        asset_wait = None
        preexisting_overwrite_started = False
        for attempt_index in range(1, max_attempts + 1):
            self.last_pm_paste_attempted = True
            self._append_action_debug(
                phase="pm_paste",
                app=target.app_name,
                profile=target.profile,
                action="pm_paste_attempt_started",
                result="attempted",
                attempt_index=attempt_index,
                max_attempts=max_attempts,
                prompt_length=prompt_length,
                prompt_hash=prompt_hash,
            )
            if self.event_log:
                self.event_log.append(
                    "pm_paste_attempt_started",
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                    pm_prompt_length=prompt_length,
                    pm_prompt_hash=prompt_hash,
                )

            if focus_detection is None:
                self.activate_app(target)
                try:
                    try:
                        asset_wait = self._wait_for_asset_idle(
                            target,
                            allow_preexisting_text=True,
                        )
                    except TypeError as error:
                        if "allow_preexisting_text" not in str(error):
                            raise
                        asset_wait = self._wait_for_asset_idle(target)
                except GuiAutomationError as error:
                    final_reason = "pm_paste_state_unverified"
                    self._append_action_debug(
                        phase="pm_paste",
                        app=target.app_name,
                        profile=target.profile,
                        action="pm_state_check_before_paste",
                        result="failed",
                        attempt_index=attempt_index,
                        max_attempts=max_attempts,
                        error=str(error),
                        prompt_length=prompt_length,
                        prompt_hash=prompt_hash,
                    )
                    if attempt_index >= max_attempts:
                        self.last_pm_paste_failure_reason = final_reason
                        raise GuiAutomationError(final_reason) from error
                    self.sleep_fn(retry_delay)
                    continue
                if getattr(asset_wait, "should_abort", False) or not getattr(
                    asset_wait,
                    "should_proceed",
                    True,
                ):
                    final_reason = asset_wait.error or "pm_paste_state_unverified"
                    self._append_action_debug(
                        phase="pm_paste",
                        app=target.app_name,
                        profile=target.profile,
                        action="pm_state_check_before_paste",
                        result="failed",
                        attempt_index=attempt_index,
                        max_attempts=max_attempts,
                        error=final_reason,
                        prompt_length=prompt_length,
                        prompt_hash=prompt_hash,
                    )
                    if attempt_index >= max_attempts:
                        self.last_pm_paste_failure_reason = final_reason
                        raise GuiAutomationError(final_reason)
                    self.sleep_fn(retry_delay)
                    continue
                detection_before = asset_wait.detection
                self.last_pm_paste_state_before = detection_before.matched_state.value
                self._append_action_debug(
                    phase="pm_paste",
                    app=target.app_name,
                    profile=target.profile,
                    action="pm_initial_composer_state",
                    result="observed",
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                    pm_state=detection_before.matched_state.value,
                    pm_state_confidence=detection_before.confidence,
                    pm_state_asset=detection_before.matched_asset_path,
                    window_bounds=detection_before.window_bounds,
                    prompt_length=prompt_length,
                    prompt_hash=prompt_hash,
                )
                self._emit_terminal_debug(
                    target,
                    f"initial state={detection_before.matched_state.value}",
                )
                self._append_action_debug(
                    phase="pm_paste",
                    app=target.app_name,
                    profile=target.profile,
                    action="pm_state_check_before_paste",
                    result="observed",
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                    pm_state=detection_before.matched_state.value,
                    pm_state_confidence=detection_before.confidence,
                    pm_state_asset=detection_before.matched_asset_path,
                    window_bounds=detection_before.window_bounds,
                    prompt_length=prompt_length,
                    prompt_hash=prompt_hash,
                )

                try:
                    focus_detection = self._click_pm_asset_composer_for_paste(
                        target,
                        attempt_index=attempt_index,
                        max_attempts=max_attempts,
                        prompt_length=prompt_length,
                        prompt_hash=prompt_hash,
                    )
                except GuiAutomationError as error:
                    final_reason = str(error)
                    if "pm_composer_click_unsafe" in final_reason:
                        self.last_pm_paste_failure_reason = final_reason
                        raise
                    if attempt_index >= max_attempts:
                        self.last_pm_paste_failure_reason = final_reason
                        raise
                    self.sleep_fn(retry_delay)
                    continue
                if asset_wait.should_overwrite:
                    if self.event_log:
                        self.event_log.append(
                            "pm_existing_composer_overwrite_attempted",
                            app_name=target.app_name,
                            profile=asset_wait.asset_profile,
                            final_state=asset_wait.final_state.value,
                            poll_count=asset_wait.poll_count,
                            attempt_index=attempt_index,
                        )
                    self._select_all_local_agent_text(target)
                elif asset_wait.final_state == VisualGuiState.COMPOSER_HAS_TEXT:
                    self._emit_terminal_debug(
                        target,
                        "checking existing composer content for current prompt sentinel",
                    )
                    preexisting_result = self._check_pm_preexisting_composer_content(
                        target,
                        expected_text=prompt,
                        paste_variant="preexisting_composer",
                        paste_backend="composer_copyback",
                        attempt_index=attempt_index,
                        max_attempts=max_attempts,
                        prompt_hash=prompt_hash,
                        window_bounds=focus_detection.window_bounds,
                    )
                    if preexisting_result.verified:
                        self._record_pm_paste_content_verification(
                            target,
                            preexisting_result,
                            paste_variant="preexisting_composer",
                            paste_backend="composer_copyback",
                            attempt_index=attempt_index,
                            max_attempts=max_attempts,
                            prompt_length=prompt_length,
                            prompt_hash=prompt_hash,
                            window_bounds=focus_detection.window_bounds,
                        )
                        try:
                            self._set_and_verify_pm_clipboard(
                                target,
                                prompt,
                                prompt_hash,
                                attempt_index=attempt_index,
                                max_attempts=max_attempts,
                            )
                        except GuiAutomationError as error:
                            final_reason = str(error)
                            if attempt_index >= max_attempts:
                                self.last_pm_paste_failure_reason = final_reason
                                raise
                            self.sleep_fn(retry_delay)
                            continue
                        self.last_pm_paste_backend_success = True
                        self.last_pm_paste_state_after = asset_wait.final_state.value
                        self.last_pm_paste_send_ready = None
                        self._append_action_debug(
                            phase="pm_paste",
                            app=target.app_name,
                            profile=target.profile,
                            action="pm_prompt_already_present_in_composer",
                            result="succeeded",
                            attempt_index=attempt_index,
                            max_attempts=max_attempts,
                            prompt_length=prompt_length,
                            prompt_hash=prompt_hash,
                            sentinel_id=preexisting_result.sentinel_id,
                            sentinel_hash=preexisting_result.sentinel_hash,
                            copyback_length=preexisting_result.copied_text_length,
                            copyback_hash=preexisting_result.copied_text_hash,
                            pm_state_after=asset_wait.final_state.value,
                            window_bounds=focus_detection.window_bounds,
                        )
                        self._append_action_debug(
                            phase="pm_paste",
                            app=target.app_name,
                            profile=target.profile,
                            action="send_ready_check_skipped_by_policy",
                            result="skipped",
                            diagnostic_only=True,
                            attempt_index=attempt_index,
                            max_attempts=max_attempts,
                            prompt_length=prompt_length,
                            prompt_hash=prompt_hash,
                            window_bounds=focus_detection.window_bounds,
                        )
                        if self.event_log:
                            self.event_log.append(
                                "pm_prompt_already_present_in_composer",
                                attempt_index=attempt_index,
                                pm_prompt_length=prompt_length,
                                pm_prompt_hash=prompt_hash,
                                sentinel_id=preexisting_result.sentinel_id,
                                sentinel_hash=preexisting_result.sentinel_hash,
                            )
                            self.event_log.append(
                                "send_ready_check_skipped_by_policy",
                                attempt_index=attempt_index,
                                phase="pm",
                                diagnostic_only=True,
                            )
                        self._emit_terminal_debug(
                            target,
                            "sentinel_found=yes; current PM prompt already present",
                        )
                        self._emit_terminal_debug(
                            target,
                            "send-ready check skipped by policy; submit will locate control",
                        )
                        return True
                    if preexisting_result.copied_text_hash is None:
                        final_reason = (
                            preexisting_result.failure_reason
                            or "pm_prompt_content_verification_failed"
                        )
                        self.last_pm_paste_failure_reason = final_reason
                        raise GuiAutomationError(final_reason)
                    if preexisting_result.failure_reason == "pm_composer_copyback_not_composer":
                        final_reason = "pm_composer_copyback_not_composer"
                        self.last_pm_paste_failure_reason = final_reason
                        raise GuiAutomationError(final_reason)
                    preexisting_overwrite_started = True
                    self._append_action_debug(
                        phase="pm_paste",
                        app=target.app_name,
                        profile=target.profile,
                        action="pm_preexisting_text_without_current_sentinel",
                        result="observed",
                        attempt_index=attempt_index,
                        max_attempts=max_attempts,
                        prompt_length=prompt_length,
                        prompt_hash=prompt_hash,
                        sentinel_id=preexisting_result.sentinel_id,
                        sentinel_hash=preexisting_result.sentinel_hash,
                        copyback_length=preexisting_result.copied_text_length,
                        copyback_hash=preexisting_result.copied_text_hash,
                        raw_key_leak_suspected=preexisting_result.raw_key_leak_suspected,
                        window_bounds=focus_detection.window_bounds,
                    )
                    self._append_action_debug(
                        phase="pm_paste",
                        app=target.app_name,
                        profile=target.profile,
                        action="pm_composer_overwrite_started",
                        result="attempted",
                        attempt_index=attempt_index,
                        max_attempts=max_attempts,
                        prompt_length=prompt_length,
                        prompt_hash=prompt_hash,
                        window_bounds=focus_detection.window_bounds,
                    )
                    if self.event_log:
                        self.event_log.append(
                            "pm_preexisting_text_without_current_sentinel",
                            attempt_index=attempt_index,
                            pm_prompt_length=prompt_length,
                            pm_prompt_hash=prompt_hash,
                            sentinel_id=preexisting_result.sentinel_id,
                            sentinel_hash=preexisting_result.sentinel_hash,
                        )
                        self.event_log.append(
                            "pm_composer_overwrite_started",
                            attempt_index=attempt_index,
                        )
                    self._emit_terminal_debug(
                        target,
                        "sentinel_found=no; overwriting composer with current PM prompt",
                    )
                    try:
                        self._select_all_local_agent_text(target)
                    except GuiAutomationError:
                        if not self._cleanup_pm_composer_text(target):
                            final_reason = "pm_composer_overwrite_failed"
                            self.last_pm_paste_failure_reason = final_reason
                            self._append_action_debug(
                                phase="pm_paste",
                                app=target.app_name,
                                profile=target.profile,
                                action="pm_composer_overwrite_failed",
                                result="failed",
                                attempt_index=attempt_index,
                                max_attempts=max_attempts,
                                failure_reason=final_reason,
                                prompt_length=prompt_length,
                                prompt_hash=prompt_hash,
                                window_bounds=focus_detection.window_bounds,
                            )
                            raise GuiAutomationError(final_reason)
            else:
                self._append_action_debug(
                    phase="pm_paste",
                    app=target.app_name,
                    profile=target.profile,
                    action="pm_focus_attempt_skipped",
                    result="succeeded",
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                    reason="previous_focus_success",
                    prompt_length=prompt_length,
                    prompt_hash=prompt_hash,
                )
                self._emit_terminal_debug(
                    target,
                    (
                        f"detect plus anchor previously succeeded; "
                        f"skipping focus retry attempt {attempt_index}/{max_attempts}"
                    ),
                )

            try:
                self._set_and_verify_pm_clipboard(
                    target,
                    prompt,
                    prompt_hash,
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                )
            except GuiAutomationError as error:
                final_reason = str(error)
                if attempt_index >= max_attempts:
                    self.last_pm_paste_failure_reason = final_reason
                    raise
                self.sleep_fn(retry_delay)
                continue

            backend_variant_succeeded = False
            variant_action_returned = False
            for variant_name in self._paste_variants(target):
                variant_backend = pm_paste_backend_for_variant(variant_name) or backend_chain[0]
                self._append_action_debug(
                    phase="pm_paste",
                    app=target.app_name,
                    profile=target.profile,
                    action="pm_paste_variant_attempted",
                    result="attempted",
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                    paste_variant=variant_name,
                    prompt_length=prompt_length,
                    prompt_hash=prompt_hash,
                    window_bounds=focus_detection.window_bounds,
                    pm_state_before=detection_before.matched_state.value
                    if detection_before is not None
                    else None,
                    paste_backend=variant_backend,
                )
                self._emit_terminal_debug(
                    target,
                    (
                        f"paste variant {variant_name} attempt {attempt_index}/{max_attempts} "
                        f"backend={variant_backend}"
                    ),
                )
                try:
                    self._paste_variant(target, variant_name)
                except GuiAutomationError as error:
                    if "pm_paste_raw_v_typed_instead_of" in str(error):
                        self.last_pm_raw_v_failure_detected = True
                        final_reason = "pm_paste_raw_v_typed_instead_of_prompt"
                    self._append_action_debug(
                        phase="pm_paste",
                        app=target.app_name,
                        profile=target.profile,
                        action="pm_paste_variant_result",
                        result="failed",
                        attempt_index=attempt_index,
                        max_attempts=max_attempts,
                        paste_variant=variant_name,
                        error=str(error),
                        paste_backend=variant_backend,
                        prompt_length=prompt_length,
                        prompt_hash=prompt_hash,
                    )
                    continue

                variant_action_returned = True
                self._append_action_debug(
                    phase="pm_paste",
                    app=target.app_name,
                    profile=target.profile,
                    action="pm_paste_variant_action_returned",
                    result="action_returned",
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                    paste_variant=variant_name,
                    paste_backend=variant_backend,
                    prompt_length=prompt_length,
                    prompt_hash=prompt_hash,
                    window_bounds=focus_detection.window_bounds,
                )
                self._emit_terminal_debug(
                    target,
                    f"paste variant {variant_name} action returned",
                )
                if self.event_log:
                    self.event_log.append(
                        "pm_paste_variant_action_returned",
                        attempt_index=attempt_index,
                        paste_variant=variant_name,
                    )
                backend_variant_succeeded = True
                self.last_pm_paste_backend_success = True
                self._append_action_debug(
                    phase="pm_paste",
                    app=target.app_name,
                    profile=target.profile,
                    action="paste_checkpoint_passed",
                    result="succeeded",
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                    paste_variant=variant_name,
                    paste_backend=variant_backend,
                    prompt_length=prompt_length,
                    prompt_hash=prompt_hash,
                    clipboard_readback_matches_prompt_hash=(
                        self.last_pm_clipboard_readback_matches_prompt_hash
                    ),
                )
                self._append_action_debug(
                    phase="pm_paste",
                    app=target.app_name,
                    profile=target.profile,
                    action="pm_paste_attempt_completed",
                    result="succeeded",
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                    paste_variant=variant_name,
                    paste_backend=variant_backend,
                    prompt_length=prompt_length,
                    prompt_hash=prompt_hash,
                    diagnostic_only=False,
                )
                self._append_action_debug(
                    phase="pm_paste",
                    app=target.app_name,
                    profile=target.profile,
                    action="submit_after_paste_policy_used",
                    result="policy_selected",
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                    paste_variant=variant_name,
                    paste_backend=variant_backend,
                    prompt_length=prompt_length,
                    prompt_hash=prompt_hash,
                )
                self._append_action_debug(
                    phase="pm_paste",
                    app=target.app_name,
                    profile=target.profile,
                    action="pm_submit_after_verified_paste",
                    result="policy_selected",
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                    paste_variant=variant_name,
                    paste_backend=variant_backend,
                    prompt_length=prompt_length,
                    prompt_hash=prompt_hash,
                )
                self._append_action_debug(
                    phase="pm_paste",
                    app=target.app_name,
                    profile=target.profile,
                    action="send_ready_check_skipped_by_policy",
                    result="skipped",
                    diagnostic_only=True,
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                    paste_variant=variant_name,
                    paste_backend=variant_backend,
                )
                self._append_action_debug(
                    phase="pm_paste",
                    app=target.app_name,
                    profile=target.profile,
                    action="prompt_presence_check_skipped_by_policy",
                    result="skipped",
                    diagnostic_only=True,
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                    paste_variant=variant_name,
                    paste_backend=variant_backend,
                )
                self._append_action_debug(
                    phase="pm_paste",
                    app=target.app_name,
                    profile=target.profile,
                    action="attachment_verification_skipped_by_policy",
                    result="skipped",
                    diagnostic_only=True,
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                    paste_variant=variant_name,
                    paste_backend=variant_backend,
                )
                if self.event_log:
                    self.event_log.append(
                        "paste_checkpoint_passed",
                        phase="pm",
                        attempt_index=attempt_index,
                        paste_variant=variant_name,
                        paste_backend=variant_backend,
                    )
                    self.event_log.append(
                        "pm_submit_after_verified_paste",
                        attempt_index=attempt_index,
                        paste_variant=variant_name,
                        paste_backend=variant_backend,
                    )
                    self.event_log.append(
                        "submit_after_paste_policy_used",
                        phase="pm",
                        attempt_index=attempt_index,
                        paste_variant=variant_name,
                        paste_backend=variant_backend,
                    )
                    self.event_log.append(
                        "pm_paste_attempt_completed",
                        attempt_index=attempt_index,
                        paste_variant=variant_name,
                        paste_backend=variant_backend,
                    )
                    self.event_log.append(
                        "send_ready_check_skipped_by_policy",
                        phase="pm",
                        diagnostic_only=True,
                    )
                    self.event_log.append(
                        "prompt_presence_check_skipped_by_policy",
                        phase="pm",
                        diagnostic_only=True,
                    )
                    self.event_log.append(
                        "attachment_verification_skipped_by_policy",
                        phase="pm",
                        diagnostic_only=True,
                    )
                self._emit_terminal_debug(
                    target,
                    "paste checkpoint passed; submit will proceed without visible-text gate",
                )
                return True

            if (
                not backend_variant_succeeded
                and not variant_action_returned
                and not self.last_pm_raw_v_failure_detected
            ):
                final_reason = "pm_paste_backend_failed"
            if self.last_pm_raw_v_failure_detected:
                self.last_pm_paste_failure_reason = final_reason
                raise GuiAutomationError(final_reason)
            if attempt_index < max_attempts:
                self.sleep_fn(retry_delay)

        if final_state in {VisualGuiState.UNKNOWN, VisualGuiState.AMBIGUOUS}:
            final_reason = "pm_paste_state_unverified"
        self.last_pm_paste_failure_reason = final_reason
        if preexisting_overwrite_started:
            self._append_action_debug(
                phase="pm_paste",
                app=target.app_name,
                profile=target.profile,
                action="pm_composer_overwrite_failed",
                result="failed",
                max_attempts=max_attempts,
                failure_reason=final_reason,
                prompt_length=prompt_length,
                prompt_hash=prompt_hash,
            )
            if self.event_log:
                self.event_log.append(
                    "pm_composer_overwrite_failed",
                    max_attempts=max_attempts,
                    failure_reason=final_reason,
                )
        self._append_action_debug(
            phase="pm_paste",
            app=target.app_name,
            profile=target.profile,
            action="pm_paste_retry_exhausted",
            result="failed",
            max_attempts=max_attempts,
            pm_state_after=final_state.value if final_state else None,
            failure_reason=final_reason,
            prompt_length=prompt_length,
            prompt_hash=prompt_hash,
        )
        if self.event_log:
            self.event_log.append(
                "pm_paste_retry_exhausted",
                max_attempts=max_attempts,
                pm_state_after=final_state.value if final_state else None,
                failure_reason=final_reason,
            )
        raise GuiAutomationError(final_reason)

    def _click_pm_asset_composer_for_paste(
        self,
        target: ManualStageTarget,
        *,
        attempt_index: int,
        max_attempts: int,
        prompt_length: int,
        prompt_hash: str,
    ):
        self._append_action_debug(
            phase="pm_paste",
            app=target.app_name,
            profile=target.profile,
            action="pm_focus_attempt_started",
            result="attempted",
            attempt_index=attempt_index,
            max_attempts=max_attempts,
            prompt_length=prompt_length,
            prompt_hash=prompt_hash,
        )
        self._emit_terminal_debug(
            target,
            f"detect plus anchor attempt {attempt_index}/{max_attempts}",
        )
        detection = self._detect_asset_state(target)
        if not detection.plus_anchor_found:
            best_plus = max(
                (
                    diagnostic.best_match_confidence
                    for diagnostic in detection.template_diagnostics
                    if diagnostic.asset_kind.value == "plus"
                    and diagnostic.best_match_confidence is not None
                ),
                default=None,
            )
            best_text = f"{best_plus:.3f}" if best_plus is not None else "unavailable"
            self._emit_terminal_debug(
                target,
                (
                    f"detect plus anchor attempt {attempt_index}/{max_attempts} failed: "
                    f"plus anchor not found; best confidence={best_text}"
                ),
            )
            self._append_action_debug(
                phase="pm_paste",
                app=target.app_name,
                profile=target.profile,
                action="pm_focus_attempt_failed",
                result="failed",
                attempt_index=attempt_index,
                max_attempts=max_attempts,
                failure_reason="pm_plus_anchor_not_found",
                window_bounds=detection.window_bounds,
                prompt_length=prompt_length,
                prompt_hash=prompt_hash,
            )
            raise GuiAutomationError("pm_plus_anchor_not_found")
        if not detection.composer_click_point_safe:
            self._append_action_debug(
                phase="pm_paste",
                app=target.app_name,
                profile=target.profile,
                action="pm_focus_attempt_failed",
                result="failed",
                attempt_index=attempt_index,
                max_attempts=max_attempts,
                failure_reason="pm_composer_click_unsafe",
                click_point=detection.computed_composer_click_point,
                window_bounds=detection.window_bounds,
                prompt_length=prompt_length,
                prompt_hash=prompt_hash,
            )
            raise GuiAutomationError("pm_composer_click_unsafe")
        if detection.computed_composer_click_point is None:
            raise GuiAutomationError("pm_composer_click_point_unavailable")
        plus_confidence = (
            f"{detection.plus_anchor_confidence:.3f}"
            if detection.plus_anchor_confidence is not None
            else "unavailable"
        )
        self._emit_terminal_debug(
            target,
            (
                f"detect plus anchor attempt {attempt_index}/{max_attempts} succeeded: "
                f"confidence={plus_confidence} "
                f"click_point={detection.computed_composer_click_point}; stopping retries"
            ),
        )
        self._click_point(detection.computed_composer_click_point)
        self._append_action_debug(
            phase="pm_paste",
            app=target.app_name,
            profile=target.profile,
            action="pm_focus_attempt_succeeded",
            result="succeeded",
            attempt_index=attempt_index,
            max_attempts=max_attempts,
            click_point=detection.computed_composer_click_point,
            plus_anchor_bbox=detection.plus_anchor_bbox,
            plus_anchor_confidence=detection.plus_anchor_confidence,
            window_bounds=detection.window_bounds,
            backend=target.click_backend,
            prompt_length=prompt_length,
            prompt_hash=prompt_hash,
        )
        if self.event_log:
            self.event_log.append(
                "pm_focus_attempt_succeeded",
                attempt_index=attempt_index,
                click_point=detection.computed_composer_click_point,
                plus_anchor_bbox=detection.plus_anchor_bbox,
            )
        return detection

    def _set_and_verify_pm_clipboard(
        self,
        target: ManualStageTarget,
        prompt: str,
        prompt_hash: str,
        *,
        attempt_index: int,
        max_attempts: int,
    ) -> None:
        prompt_length = len(prompt)
        self.last_pm_clipboard_set_attempted = True
        self._append_action_debug(
            phase="pm_paste",
            app=target.app_name,
            profile=target.profile,
            action="pm_clipboard_set_attempted",
            result="attempted",
            attempt_index=attempt_index,
            max_attempts=max_attempts,
            prompt_length=prompt_length,
            prompt_hash=prompt_hash,
        )
        if self.event_log:
            self.event_log.append(
                "pm_clipboard_set_attempted",
                attempt_index=attempt_index,
                pm_prompt_length=prompt_length,
                pm_prompt_hash=prompt_hash,
            )
        try:
            self.copy_text_to_clipboard(prompt)
        except Exception as error:
            self.last_pm_clipboard_set_succeeded = False
            self.last_pm_paste_failure_reason = "pm_clipboard_set_failed"
            self._append_action_debug(
                phase="pm_paste",
                app=target.app_name,
                profile=target.profile,
                action="pm_clipboard_set_succeeded",
                result="failed",
                attempt_index=attempt_index,
                max_attempts=max_attempts,
                error=str(error),
                prompt_length=prompt_length,
                prompt_hash=prompt_hash,
            )
            raise GuiAutomationError("pm_clipboard_set_failed") from error
        self.last_pm_clipboard_set_succeeded = True
        self._append_action_debug(
            phase="pm_paste",
            app=target.app_name,
            profile=target.profile,
            action="pm_clipboard_set_succeeded",
            result="succeeded",
            attempt_index=attempt_index,
            max_attempts=max_attempts,
            prompt_length=prompt_length,
            prompt_hash=prompt_hash,
        )
        if self.event_log:
            self.event_log.append(
                "pm_clipboard_set_succeeded",
                attempt_index=attempt_index,
                pm_prompt_length=prompt_length,
                pm_prompt_hash=prompt_hash,
            )
        readback = self.clipboard.read_text() if self.clipboard else None
        if readback is None:
            self.last_pm_clipboard_readback_matches_prompt_hash = None
            return
        readback_hash = hashlib.sha256(readback.encode("utf-8")).hexdigest()
        readback_matches = readback_hash == prompt_hash
        self.last_pm_clipboard_readback_matches_prompt_hash = readback_matches
        self._append_action_debug(
            phase="pm_paste",
            app=target.app_name,
            profile=target.profile,
            action="pm_clipboard_readback_verified",
            result="succeeded" if readback_matches else "failed",
            attempt_index=attempt_index,
            max_attempts=max_attempts,
            clipboard_length=len(readback),
            clipboard_hash=readback_hash,
            prompt_length=prompt_length,
            prompt_hash=prompt_hash,
        )
        if self.event_log:
            self.event_log.append(
                "pm_clipboard_readback_verified",
                attempt_index=attempt_index,
                clipboard_length=len(readback),
                clipboard_hash=readback_hash,
                clipboard_readback_matches_pm_prompt_hash=readback_matches,
            )
        if not readback_matches:
            self.last_pm_paste_failure_reason = "pm_clipboard_readback_mismatch"
            raise GuiAutomationError("pm_clipboard_readback_mismatch")

    def _check_pm_preexisting_composer_content(
        self,
        target: ManualStageTarget,
        *,
        expected_text: str,
        paste_variant: str,
        paste_backend: str,
        attempt_index: int,
        max_attempts: int,
        prompt_hash: str,
        window_bounds: tuple[int, int, int, int] | None,
    ) -> "PMPasteContentVerification":
        sentinel_id = extract_pm_prompt_sentinel(expected_text)
        sentinel_hash = (
            hashlib.sha256(sentinel_id.encode("utf-8")).hexdigest() if sentinel_id else None
        )
        self._append_action_debug(
            phase="pm_paste",
            app=target.app_name,
            profile=target.profile,
            action="pm_preexisting_text_check_started",
            result="attempted",
            attempt_index=attempt_index,
            max_attempts=max_attempts,
            paste_variant=paste_variant,
            paste_backend=paste_backend,
            prompt_length=len(expected_text),
            prompt_hash=prompt_hash,
            sentinel_id=sentinel_id,
            sentinel_hash=sentinel_hash,
            window_bounds=window_bounds,
        )
        if self.event_log:
            self.event_log.append(
                "pm_preexisting_text_check_started",
                attempt_index=attempt_index,
                pm_prompt_length=len(expected_text),
                pm_prompt_hash=prompt_hash,
                sentinel_id=sentinel_id,
                sentinel_hash=sentinel_hash,
            )
        try:
            copied_text = self._copy_pm_composer_text_for_verification(target, expected_text)
        except GuiAutomationError as error:
            result = PMPasteContentVerification(
                verified=False,
                method="menu_select_all_copy",
                copied_text_length=None,
                copied_text_hash=None,
                raw_key_leak_suspected=False,
                sentinel_id=sentinel_id,
                sentinel_hash=sentinel_hash,
                sentinel_found=False if sentinel_id else None,
                failure_reason="pm_prompt_content_verification_failed",
                error=str(error),
            )
            self._append_action_debug(
                phase="pm_paste",
                app=target.app_name,
                profile=target.profile,
                action="pm_preexisting_text_check_result",
                result="failed",
                attempt_index=attempt_index,
                max_attempts=max_attempts,
                prompt_length=len(expected_text),
                prompt_hash=prompt_hash,
                sentinel_id=sentinel_id,
                sentinel_hash=sentinel_hash,
                sentinel_found=result.sentinel_found,
                copyback_length=None,
                copyback_hash=None,
                failure_reason=result.failure_reason,
                error=result.error,
                window_bounds=window_bounds,
            )
            return result

        copied_hash = hashlib.sha256(copied_text.encode("utf-8")).hexdigest()
        raw_leak = raw_key_leak_suspected(copied_text, expected_text)
        sentinel_found = (
            normalize_paste_text_for_verification(sentinel_id)
            in normalize_paste_text_for_verification(copied_text)
            if sentinel_id
            else None
        )
        exact_text_matches = paste_text_matches_expected(copied_text, expected_text)
        verified = ((sentinel_found is True) if sentinel_id else exact_text_matches) and not raw_leak
        wrong_scope = (
            not verified
            and len(copied_text) > max(len(expected_text) * 2, 20000)
        )
        failure_reason = None
        if wrong_scope:
            failure_reason = "pm_composer_copyback_not_composer"
        elif not verified:
            failure_reason = "pm_prompt_content_verification_failed"
        result = PMPasteContentVerification(
            verified=verified,
            method="menu_select_all_copy",
            copied_text_length=len(copied_text),
            copied_text_hash=copied_hash,
            raw_key_leak_suspected=raw_leak,
            sentinel_id=sentinel_id,
            sentinel_hash=sentinel_hash,
            sentinel_found=sentinel_found,
            failure_reason=failure_reason,
            error=None,
        )
        self._append_action_debug(
            phase="pm_paste",
            app=target.app_name,
            profile=target.profile,
            action="pm_preexisting_text_check_result",
            result="succeeded" if result.verified else "stale_text",
            attempt_index=attempt_index,
            max_attempts=max_attempts,
            prompt_length=len(expected_text),
            prompt_hash=prompt_hash,
            sentinel_id=sentinel_id,
            sentinel_hash=sentinel_hash,
            sentinel_found=result.sentinel_found,
            copyback_length=result.copied_text_length,
            copyback_hash=result.copied_text_hash,
            raw_short_artifact_detected=result.raw_key_leak_suspected,
            failure_reason=result.failure_reason,
            window_bounds=window_bounds,
        )
        if self.event_log:
            self.event_log.append(
                "pm_preexisting_text_check_result",
                attempt_index=attempt_index,
                content_verified=result.verified,
                sentinel_id=sentinel_id,
                sentinel_hash=sentinel_hash,
                sentinel_found=result.sentinel_found,
                copied_text_length=result.copied_text_length,
                copied_text_hash=result.copied_text_hash,
                raw_short_artifact_detected=result.raw_key_leak_suspected,
                failure_reason=result.failure_reason,
            )
        self._emit_terminal_debug(
            target,
            (
                "existing composer copy-back "
                f"length={result.copied_text_length} "
                f"hash={result.copied_text_hash} "
                f"sentinel_found={result.sentinel_found} "
                f"raw_short_artifact_detected={result.raw_key_leak_suspected}"
            ),
        )
        return result

    def _verify_pm_paste_content(
        self,
        target: ManualStageTarget,
        *,
        expected_text: str,
        paste_variant: str,
        paste_backend: str,
        attempt_index: int,
        max_attempts: int,
        prompt_hash: str,
        window_bounds: tuple[int, int, int, int] | None,
    ) -> "PMPasteContentVerification":
        sentinel_id = extract_pm_prompt_sentinel(expected_text)
        sentinel_hash = (
            hashlib.sha256(sentinel_id.encode("utf-8")).hexdigest() if sentinel_id else None
        )
        self._append_action_debug(
            phase="pm_paste",
            app=target.app_name,
            profile=target.profile,
            action="pm_paste_content_verification_started",
            result="attempted",
            attempt_index=attempt_index,
            max_attempts=max_attempts,
            paste_variant=paste_variant,
            paste_backend=paste_backend,
            prompt_length=len(expected_text),
            prompt_hash=prompt_hash,
            sentinel_id=sentinel_id,
            sentinel_hash=sentinel_hash,
            window_bounds=window_bounds,
        )
        self._append_action_debug(
            phase="pm_paste",
            app=target.app_name,
            profile=target.profile,
            action="pm_composer_copyback_started",
            result="attempted",
            attempt_index=attempt_index,
            max_attempts=max_attempts,
            paste_variant=paste_variant,
            paste_backend=paste_backend,
            prompt_length=len(expected_text),
            prompt_hash=prompt_hash,
            sentinel_id=sentinel_id,
            sentinel_hash=sentinel_hash,
            window_bounds=window_bounds,
        )
        self._emit_terminal_debug(
            target,
            f"composer copy-back attempted paste_variant={paste_variant}",
        )
        try:
            copied_text = self._copy_pm_composer_text_for_verification(target, expected_text)
        except GuiAutomationError as error:
            result = PMPasteContentVerification(
                verified=False,
                method="menu_select_all_copy",
                copied_text_length=None,
                copied_text_hash=None,
                raw_key_leak_suspected=False,
                sentinel_id=sentinel_id,
                sentinel_hash=sentinel_hash,
                sentinel_found=False if sentinel_id else None,
                failure_reason="pm_prompt_content_verification_failed",
                error=str(error),
            )
            self._record_pm_paste_content_verification(
                target,
                result,
                paste_variant=paste_variant,
                paste_backend=paste_backend,
                attempt_index=attempt_index,
                max_attempts=max_attempts,
                prompt_length=len(expected_text),
                prompt_hash=prompt_hash,
                window_bounds=window_bounds,
            )
            return result

        copied_hash = hashlib.sha256(copied_text.encode("utf-8")).hexdigest()
        raw_leak = raw_key_leak_suspected(copied_text, expected_text)
        sentinel_found = (
            normalize_paste_text_for_verification(sentinel_id)
            in normalize_paste_text_for_verification(copied_text)
            if sentinel_id
            else None
        )
        exact_text_matches = paste_text_matches_expected(copied_text, expected_text)
        verified = ((sentinel_found is True) if sentinel_id else exact_text_matches) and not raw_leak
        failure_reason = None
        if raw_leak:
            failure_reason = "pm_paste_raw_v_typed_instead_of_prompt"
        elif not verified:
            failure_reason = "pm_prompt_content_verification_failed"
        result = PMPasteContentVerification(
            verified=verified,
            method="menu_select_all_copy",
            copied_text_length=len(copied_text),
            copied_text_hash=copied_hash,
            raw_key_leak_suspected=raw_leak,
            sentinel_id=sentinel_id,
            sentinel_hash=sentinel_hash,
            sentinel_found=sentinel_found,
            failure_reason=failure_reason,
            error=None,
        )
        self._record_pm_paste_content_verification(
            target,
            result,
            paste_variant=paste_variant,
            paste_backend=paste_backend,
            attempt_index=attempt_index,
            max_attempts=max_attempts,
            prompt_length=len(expected_text),
            prompt_hash=prompt_hash,
            window_bounds=window_bounds,
        )
        return result

    def _record_pm_paste_content_verification(
        self,
        target: ManualStageTarget,
        result: "PMPasteContentVerification",
        *,
        paste_variant: str,
        paste_backend: str,
        attempt_index: int,
        max_attempts: int,
        prompt_length: int,
        prompt_hash: str,
        window_bounds: tuple[int, int, int, int] | None,
    ) -> None:
        self.last_pm_paste_content_verified = result.verified
        self.last_pm_paste_content_verification_method = result.method
        self.last_pm_paste_copied_back_length = result.copied_text_length
        self.last_pm_paste_copied_back_hash = result.copied_text_hash
        self.last_pm_prompt_sentinel_id = result.sentinel_id
        self.last_pm_prompt_sentinel_hash = result.sentinel_hash
        self.last_pm_prompt_sentinel_found = result.sentinel_found
        if result.raw_key_leak_suspected:
            self.last_pm_raw_v_failure_detected = True
        self._append_action_debug(
            phase="pm_paste",
            app=target.app_name,
            profile=target.profile,
            action="pm_composer_copyback_completed",
            result="succeeded" if result.copied_text_hash is not None else "failed",
            attempt_index=attempt_index,
            max_attempts=max_attempts,
            paste_variant=paste_variant,
            paste_backend=paste_backend,
            prompt_length=prompt_length,
            prompt_hash=prompt_hash,
            sentinel_id=result.sentinel_id,
            sentinel_hash=result.sentinel_hash,
            copyback_length=result.copied_text_length,
            copyback_hash=result.copied_text_hash,
            raw_key_leak_suspected=result.raw_key_leak_suspected,
            error=result.error,
            window_bounds=window_bounds,
        )
        self._append_action_debug(
            phase="pm_paste",
            app=target.app_name,
            profile=target.profile,
            action="pm_paste_content_verification_result",
            result="succeeded" if result.verified else "failed",
            attempt_index=attempt_index,
            max_attempts=max_attempts,
            paste_variant=paste_variant,
            paste_backend=paste_backend,
            prompt_length=prompt_length,
            prompt_hash=prompt_hash,
            sentinel_id=result.sentinel_id,
            sentinel_hash=result.sentinel_hash,
            sentinel_found=result.sentinel_found,
            copied_text_length=result.copied_text_length,
            copied_text_hash=result.copied_text_hash,
            content_verified=result.verified,
            raw_key_leak_suspected=result.raw_key_leak_suspected,
            verification_method=result.method,
            failure_reason=result.failure_reason,
            error=result.error,
            window_bounds=window_bounds,
        )
        self._append_action_debug(
            phase="pm_paste",
            app=target.app_name,
            profile=target.profile,
            action="pm_prompt_sentinel_found",
            result="succeeded" if result.sentinel_found else "failed",
            attempt_index=attempt_index,
            max_attempts=max_attempts,
            paste_variant=paste_variant,
            paste_backend=paste_backend,
            prompt_length=prompt_length,
            prompt_hash=prompt_hash,
            sentinel_id=result.sentinel_id,
            sentinel_hash=result.sentinel_hash,
            copyback_length=result.copied_text_length,
            copyback_hash=result.copied_text_hash,
        )
        if result.raw_key_leak_suspected:
            self._append_action_debug(
                phase="pm_paste",
                app=target.app_name,
                profile=target.profile,
                action="pm_paste_raw_v_typed_instead_of_prompt",
                result="failed",
                attempt_index=attempt_index,
                max_attempts=max_attempts,
                paste_variant=paste_variant,
                paste_backend=paste_backend,
                prompt_length=prompt_length,
                prompt_hash=prompt_hash,
                sentinel_id=result.sentinel_id,
                sentinel_hash=result.sentinel_hash,
                copyback_length=result.copied_text_length,
                copyback_hash=result.copied_text_hash,
                failure_reason=result.failure_reason,
            )
        self._append_action_debug(
            phase="pm_paste",
            app=target.app_name,
            profile=target.profile,
            action=(
                "pm_prompt_content_verified"
                if result.verified
                else "pm_prompt_content_verification_failed"
            ),
            result="succeeded" if result.verified else "failed",
            attempt_index=attempt_index,
            max_attempts=max_attempts,
            paste_variant=paste_variant,
            paste_backend=paste_backend,
            prompt_length=prompt_length,
            prompt_hash=prompt_hash,
            sentinel_id=result.sentinel_id,
            sentinel_hash=result.sentinel_hash,
            sentinel_found=result.sentinel_found,
            copyback_length=result.copied_text_length,
            copyback_hash=result.copied_text_hash,
            failure_reason=result.failure_reason,
            error=result.error,
        )
        self._emit_terminal_debug(
            target,
            (
                "composer copy-back "
                f"length={result.copied_text_length} "
                f"hash={result.copied_text_hash} "
                f"sentinel_found={result.sentinel_found} "
                f"raw_key_leak_suspected={result.raw_key_leak_suspected}"
            ),
        )
        self._emit_terminal_debug(
            target,
            (
                "paste verified=yes"
                if result.verified
                else f"paste verified=no reason={result.failure_reason}"
            ),
        )
        if self.event_log:
            self.event_log.append(
                "pm_paste_content_verification_result",
                attempt_index=attempt_index,
                paste_variant=paste_variant,
                paste_backend=paste_backend,
                content_verified=result.verified,
                sentinel_id=result.sentinel_id,
                sentinel_hash=result.sentinel_hash,
                sentinel_found=result.sentinel_found,
                raw_key_leak_suspected=result.raw_key_leak_suspected,
                copied_text_length=result.copied_text_length,
                copied_text_hash=result.copied_text_hash,
                failure_reason=result.failure_reason,
            )

    def _copy_pm_composer_text_for_verification(
        self,
        target: ManualStageTarget,
        restore_clipboard_text: str,
    ) -> str:
        if self.clipboard is None:
            raise GuiAutomationError("Clipboard is not configured.")
        sentinel = f"AGENT_BRIDGE_COPYBACK_SENTINEL_{hashlib.sha256(restore_clipboard_text.encode('utf-8')).hexdigest()[:12]}"
        self.clipboard.copy_text(sentinel)
        try:
            self._run_system_events('tell application "System Events" to key code 0 using command down')
            self.sleep_fn(0.05)
            self._run_system_events('tell application "System Events" to key code 8 using command down')
            self.sleep_fn(0.05)
            copied_text = self.clipboard.read_text()
            self.clipboard.copy_text(restore_clipboard_text)
            if copied_text != sentinel:
                return copied_text
        except GuiAutomationError:
            self.clipboard.copy_text(restore_clipboard_text)
        try:
            accessibility_text = self._read_focused_accessibility_value(target)
        except GuiAutomationError:
            accessibility_text = ""
        if accessibility_text:
            return accessibility_text
        self.clipboard.copy_text(sentinel)
        self._click_accessibility_menu_item(
            target,
            item_names=("Select All", "모두 선택"),
        )
        self.sleep_fn(0.05)
        self._click_accessibility_menu_item(
            target,
            item_names=("Copy", "복사"),
        )
        self.sleep_fn(0.05)
        copied_text = self.clipboard.read_text()
        self.clipboard.copy_text(restore_clipboard_text)
        if copied_text == sentinel:
            raise GuiAutomationError("pm_paste_content_copyback_unavailable")
        return copied_text

    def _cleanup_pm_composer_text(self, target: ManualStageTarget) -> bool:
        try:
            self._run_system_events('tell application "System Events" to key code 0 using command down')
            self.sleep_fn(0.05)
            self._run_system_events('tell application "System Events" to key code 51')
            self.sleep_fn(0.1)
            return True
        except GuiAutomationError:
            pass
        try:
            self._clear_focused_accessibility_value(target)
            self.sleep_fn(0.1)
            return True
        except GuiAutomationError:
            pass
        try:
            self._click_accessibility_menu_item(
                target,
                item_names=("Select All", "모두 선택"),
            )
            self.sleep_fn(0.05)
            self._run_system_events('tell application "System Events" to key code 51')
            self.sleep_fn(0.1)
            return True
        except GuiAutomationError:
            return False

    def _paste_local_agent_clipboard(self, target: ManualStageTarget) -> None:
        backend = _normalize_backend(target.paste_backend)
        if self.event_log:
            self.event_log.append("local_agent_paste_backend_selected", paste_backend=backend)
        self._append_action_debug(
            phase="local_agent_paste",
            app=target.app_name,
            profile=target.profile,
            action="paste",
            result="attempted",
            backend=backend,
        )
        if backend == "pyautogui":
            if self.clipboard is not None:
                try:
                    clipboard_length = len(self.clipboard.read_text())
                except Exception:
                    clipboard_length = 0
            else:
                clipboard_length = 0
            if self.event_log:
                self.event_log.append(
                    "local_agent_pyautogui_paste_attempted",
                    clipboard_length=clipboard_length,
                )
            self._paste_with_backend(target)
            if self.event_log:
                self.event_log.append("local_agent_pyautogui_paste_completed")
            self._append_action_debug(
                phase="local_agent_paste",
                app=target.app_name,
                profile=target.profile,
                action="paste",
                result="succeeded",
                backend=backend,
                clipboard_length=clipboard_length,
            )
            return
        if backend == "system_events":
            if self.event_log:
                self.event_log.append("local_agent_system_events_paste_attempted")
            self._run_system_events('tell application "System Events" to keystroke "v" using command down')
            self._append_action_debug(
                phase="local_agent_paste",
                app=target.app_name,
                profile=target.profile,
                action="paste",
                result="succeeded",
                backend=backend,
            )
            return
        raise GuiAutomationError(f"Unsupported paste backend: {backend}")

    def _paste_local_agent_clipboard_with_retry(self, target: ManualStageTarget) -> bool:
        if self.clipboard is None:
            raise GuiAutomationError("Clipboard is not configured.")
        prompt = self.clipboard.read_text()
        prompt_length = len(prompt)
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if prompt_length <= 0:
            self.last_local_agent_paste_failure_reason = "local_agent_prompt_empty"
            raise GuiAutomationError("local_agent_prompt_empty")

        self.last_local_agent_paste_attempted = False
        self.last_local_agent_paste_backend_success = False
        self.last_local_agent_paste_send_ready = None
        self.last_local_agent_paste_state_before = None
        self.last_local_agent_paste_state_after = None
        self.last_local_agent_paste_state_after_confidence = None
        self.last_local_agent_paste_state_after_asset = None
        self.last_local_agent_paste_failure_reason = None
        self.last_local_agent_clipboard_readback_matches_prompt_hash = None

        max_attempts = max(
            1,
            int(getattr(target, "max_action_attempts", 0) or self.local_agent_max_paste_attempts),
        )
        retry_delay = self._action_retry_delay_seconds(target)
        backend = _normalize_backend(target.paste_backend)
        self._append_action_debug(
            phase="local_agent_paste",
            app=target.app_name,
            profile=target.profile,
            action="local_agent_paste_retry_started",
            result="started",
            max_attempts=max_attempts,
            paste_backend=backend,
            prompt_length=prompt_length,
            prompt_hash=prompt_hash,
        )
        if self.event_log:
            self.event_log.append(
                "local_agent_paste_retry_started",
                max_attempts=max_attempts,
                paste_backend=backend,
                prompt_length=prompt_length,
                prompt_hash=prompt_hash,
            )

        for attempt_index in range(1, max_attempts + 1):
            self.last_local_agent_paste_attempted = True
            self._append_action_debug(
                phase="local_agent_paste",
                app=target.app_name,
                profile=target.profile,
                action="local_agent_paste_attempt_started",
                result="attempted",
                attempt_index=attempt_index,
                prompt_length=prompt_length,
                prompt_hash=prompt_hash,
            )
            if self.event_log:
                self.event_log.append(
                    "local_agent_paste_attempt_started",
                    attempt_index=attempt_index,
                    prompt_length=prompt_length,
                    prompt_hash=prompt_hash,
                )

            self.activate_app(target)
            state_before = self._detect_local_agent_state_for_paste(
                target,
                phase="local_agent_state_check_before_paste",
                attempt_index=attempt_index,
                prompt_length=prompt_length,
                prompt_hash=prompt_hash,
            )
            if state_before is not None:
                self.last_local_agent_paste_state_before = state_before.matched_state.value

            focus_result = self._click_local_agent_composer_for_paste(
                target,
                attempt_index=attempt_index,
                prompt_length=prompt_length,
                prompt_hash=prompt_hash,
            )
            self.last_local_agent_focus_result = focus_result
            if not focus_result.succeeded:
                failure_reason = "local_agent_composer_click_failed"
                self._append_action_debug(
                    phase="local_agent_paste",
                    app=target.app_name,
                    profile=target.profile,
                    action="click_composer",
                    result="failed",
                    attempt_index=attempt_index,
                    error=focus_result.error,
                    prompt_length=prompt_length,
                    prompt_hash=prompt_hash,
                )
                if attempt_index >= max_attempts:
                    self.last_local_agent_paste_failure_reason = failure_reason
                    raise GuiAutomationError(failure_reason)
                self.sleep_fn(retry_delay)
                continue

            self._set_and_verify_local_agent_clipboard(
                target,
                prompt,
                prompt_hash,
                attempt_index=attempt_index,
            )

            backend_variant_succeeded = False
            for variant_name in self._local_agent_paste_variants(target):
                self._append_action_debug(
                    phase="local_agent_paste",
                    app=target.app_name,
                    profile=target.profile,
                    action="local_agent_paste_variant_attempted",
                    result="attempted",
                    attempt_index=attempt_index,
                    paste_variant=variant_name,
                    prompt_length=prompt_length,
                    prompt_hash=prompt_hash,
                    codex_window_bounds=focus_result.window_bounds,
                    codex_state_before=(
                        state_before.matched_state.value if state_before is not None else None
                    ),
                )
                try:
                    self._paste_local_agent_variant(target, variant_name)
                except GuiAutomationError as error:
                    self._append_action_debug(
                        phase="local_agent_paste",
                        app=target.app_name,
                        profile=target.profile,
                        action="local_agent_paste_variant_result",
                        result="failed",
                        attempt_index=attempt_index,
                        paste_variant=variant_name,
                        error=str(error),
                        prompt_length=prompt_length,
                        prompt_hash=prompt_hash,
                    )
                    continue

                backend_variant_succeeded = True
                self.last_local_agent_paste_backend_success = True
                self._append_action_debug(
                    phase="local_agent_paste",
                    app=target.app_name,
                    profile=target.profile,
                    action="local_agent_paste_variant_result",
                    result="succeeded",
                    attempt_index=attempt_index,
                    paste_variant=variant_name,
                    prompt_length=prompt_length,
                    prompt_hash=prompt_hash,
                )
                self._append_action_debug(
                    phase="local_agent_paste",
                    app=target.app_name,
                    profile=target.profile,
                    action="local_agent_paste_variant_succeeded",
                    result="succeeded",
                    attempt_index=attempt_index,
                    paste_variant=variant_name,
                    prompt_length=prompt_length,
                    prompt_hash=prompt_hash,
                    codex_window_bounds=focus_result.window_bounds,
                )
                if self.event_log:
                    self.event_log.append(
                        "local_agent_paste_variant_result",
                        attempt_index=attempt_index,
                        paste_variant=variant_name,
                        paste_variant_succeeded=True,
                    )
                    self.event_log.append(
                        "local_agent_paste_variant_succeeded",
                        attempt_index=attempt_index,
                        paste_variant=variant_name,
                    )
                self._append_action_debug(
                    phase="local_agent_paste",
                    app=target.app_name,
                    profile=target.profile,
                    action="paste_checkpoint_passed",
                    result="succeeded",
                    attempt_index=attempt_index,
                    paste_variant=variant_name,
                    prompt_length=prompt_length,
                    prompt_hash=prompt_hash,
                    clipboard_readback_matches_prompt_hash=(
                        self.last_local_agent_clipboard_readback_matches_prompt_hash
                    ),
                )
                self._append_action_debug(
                    phase="local_agent_paste",
                    app=target.app_name,
                    profile=target.profile,
                    action="local_agent_paste_attempt_completed",
                    result="succeeded",
                    attempt_index=attempt_index,
                    paste_variant=variant_name,
                    prompt_length=prompt_length,
                    prompt_hash=prompt_hash,
                    codex_window_bounds=focus_result.window_bounds,
                    codex_state_after=None,
                    codex_send_ready_after_paste=None,
                    diagnostic_only=False,
                )
                self._append_action_debug(
                    phase="local_agent_paste",
                    app=target.app_name,
                    profile=target.profile,
                    action="submit_after_paste_policy_used",
                    result="policy_selected",
                    attempt_index=attempt_index,
                    paste_variant=variant_name,
                    prompt_length=prompt_length,
                    prompt_hash=prompt_hash,
                )
                self._append_action_debug(
                    phase="local_agent_paste",
                    app=target.app_name,
                    profile=target.profile,
                    action="local_agent_submit_after_verified_paste",
                    result="policy_selected",
                    attempt_index=attempt_index,
                    paste_variant=variant_name,
                    prompt_length=prompt_length,
                    prompt_hash=prompt_hash,
                )
                self._append_action_debug(
                    phase="local_agent_paste",
                    app=target.app_name,
                    profile=target.profile,
                    action="send_ready_check_skipped_by_policy",
                    result="skipped",
                    diagnostic_only=True,
                    attempt_index=attempt_index,
                    paste_variant=variant_name,
                )
                self._append_action_debug(
                    phase="local_agent_paste",
                    app=target.app_name,
                    profile=target.profile,
                    action="prompt_presence_check_skipped_by_policy",
                    result="skipped",
                    diagnostic_only=True,
                    attempt_index=attempt_index,
                    paste_variant=variant_name,
                )
                self._append_action_debug(
                    phase="local_agent_paste",
                    app=target.app_name,
                    profile=target.profile,
                    action="attachment_verification_skipped_by_policy",
                    result="skipped",
                    diagnostic_only=True,
                    attempt_index=attempt_index,
                    paste_variant=variant_name,
                )
                if self.event_log:
                    self.event_log.append(
                        "paste_checkpoint_passed",
                        phase="local_agent",
                        attempt_index=attempt_index,
                        paste_variant=variant_name,
                    )
                    self.event_log.append(
                        "local_agent_submit_after_verified_paste",
                        attempt_index=attempt_index,
                        paste_variant=variant_name,
                    )
                    self.event_log.append(
                        "submit_after_paste_policy_used",
                        phase="local_agent",
                        attempt_index=attempt_index,
                        paste_variant=variant_name,
                    )
                    self.event_log.append(
                        "local_agent_paste_attempt_completed",
                        attempt_index=attempt_index,
                        paste_variant=variant_name,
                        codex_state_after=None,
                        codex_send_ready_after_paste=None,
                    )
                    self.event_log.append(
                        "send_ready_check_skipped_by_policy",
                        phase="local_agent",
                        diagnostic_only=True,
                    )
                    self.event_log.append(
                        "prompt_presence_check_skipped_by_policy",
                        phase="local_agent",
                        diagnostic_only=True,
                    )
                    self.event_log.append(
                        "attachment_verification_skipped_by_policy",
                        phase="local_agent",
                        diagnostic_only=True,
                    )
                return True

            if not backend_variant_succeeded:
                self.last_local_agent_paste_failure_reason = "local_agent_paste_backend_failed"
                self._append_action_debug(
                    phase="local_agent_paste",
                    app=target.app_name,
                    profile=target.profile,
                    action="local_agent_focus_succeeded_but_paste_missing",
                    result="failed",
                    attempt_index=attempt_index,
                    prompt_length=prompt_length,
                    prompt_hash=prompt_hash,
                    codex_window_bounds=focus_result.window_bounds,
                )
                if self.event_log:
                    self.event_log.append(
                        "local_agent_focus_succeeded_but_paste_missing",
                        attempt_index=attempt_index,
                        codex_window_bounds=focus_result.window_bounds,
                    )
                raise GuiAutomationError("local_agent_paste_backend_failed")
            self.sleep_fn(retry_delay)

        self.last_local_agent_paste_failure_reason = (
            "local_agent_paste_not_reflected_in_codex_state"
        )
        self._append_action_debug(
            phase="local_agent_paste",
            app=target.app_name,
            profile=target.profile,
            action="local_agent_paste_retry_exhausted",
            result="failed",
            max_attempts=max_attempts,
            prompt_length=prompt_length,
            prompt_hash=prompt_hash,
            codex_state_after=self.last_local_agent_paste_state_after,
        )
        if self.event_log:
            self.event_log.append(
                "local_agent_paste_retry_exhausted",
                max_attempts=max_attempts,
                codex_state_after=self.last_local_agent_paste_state_after,
            )
        raise GuiAutomationError("local_agent_paste_not_reflected_in_codex_state")

    def _detect_local_agent_state_for_paste(
        self,
        target: ManualStageTarget,
        *,
        phase: str,
        attempt_index: int,
        prompt_length: int,
        prompt_hash: str,
        paste_variant: str | None = None,
    ):
        if not self._uses_asset_state_target(target):
            return None
        detection = self._detect_asset_state(target)
        self._append_action_debug(
            phase="local_agent_paste",
            app=target.app_name,
            profile=target.profile,
            action=phase,
            result="observed",
            attempt_index=attempt_index,
            paste_variant=paste_variant,
            prompt_length=prompt_length,
            prompt_hash=prompt_hash,
            codex_window_bounds=detection.window_bounds,
            codex_state=detection.matched_state.value,
            codex_state_confidence=detection.confidence,
            codex_state_asset=detection.matched_asset_path,
        )
        if self.event_log:
            self.event_log.append(
                phase,
                attempt_index=attempt_index,
                paste_variant=paste_variant,
                codex_window_bounds=detection.window_bounds,
                codex_state=detection.matched_state.value,
                codex_state_confidence=detection.confidence,
                codex_state_asset=detection.matched_asset_path,
            )
        return detection

    def _click_local_agent_composer_for_paste(
        self,
        target: ManualStageTarget,
        *,
        attempt_index: int,
        prompt_length: int,
        prompt_hash: str,
    ) -> LocalAgentFocusResult:
        if self.codex_ui_detector is None:
            raise GuiAutomationError("Codex UI detector is not configured.")
        click_backend = target.visual_anchor_click_backend or target.click_backend
        logs_dir = Path(self.debug_logs_dir) if self.debug_logs_dir else None
        self._append_action_debug(
            phase="local_agent_paste",
            app=target.app_name,
            profile=target.profile,
            action="local_agent_codex_focus_started",
            result="attempted",
            attempt_index=attempt_index,
            click_backend=click_backend,
            focus_strategy=target.focus_strategy,
            prompt_length=prompt_length,
            prompt_hash=prompt_hash,
        )
        self._append_action_debug(
            phase="local_agent_paste",
            app=target.app_name,
            profile=target.profile,
            action="click_composer",
            result="attempted",
            attempt_index=attempt_index,
            click_backend=click_backend,
            focus_strategy=target.focus_strategy,
            prompt_length=prompt_length,
            prompt_hash=prompt_hash,
        )
        if self.event_log:
            self.event_log.append(
                "local_agent_composer_click_attempted",
                attempt_index=attempt_index,
                focus_strategy=target.focus_strategy,
                click_backend=click_backend,
            )
        if target.focus_strategy == "direct_plus_anchor" and target.direct_plus_anchor_enabled:
            result = self.codex_ui_detector.click_direct_plus_anchor(
                target,
                logs_dir=logs_dir,
                visual_debug=self.debug_screenshots,
                click_backend=click_backend,
            )
        elif target.plus_anchor_enabled:
            result = self.codex_ui_detector.click_visual_input(
                target,
                logs_dir=logs_dir,
                visual_debug=self.debug_screenshots,
                require_plus=True,
                click_backend=click_backend,
            )
        else:
            result = self.codex_ui_detector.focus_input(target)
        self._append_action_debug(
            phase="local_agent_paste",
            app=target.app_name,
            profile=target.profile,
            action=(
                "local_agent_codex_focus_succeeded"
                if result.succeeded
                else "local_agent_codex_focus_failed"
            ),
            result="succeeded" if result.succeeded else "failed",
            attempt_index=attempt_index,
            click_point=result.fallback_click_point,
            plus_anchor_bbox=result.plus_button_bbox,
            plus_anchor_center=result.plus_button_center,
            codex_window_bounds=result.window_bounds,
            app_frontmost=result.app_frontmost,
            error=result.error,
            prompt_length=prompt_length,
            prompt_hash=prompt_hash,
        )
        self._append_action_debug(
            phase="local_agent_paste",
            app=target.app_name,
            profile=target.profile,
            action="click_composer",
            result="succeeded" if result.succeeded else "failed",
            attempt_index=attempt_index,
            click_point=result.fallback_click_point,
            plus_anchor_bbox=result.plus_button_bbox,
            plus_anchor_center=result.plus_button_center,
            codex_window_bounds=result.window_bounds,
            app_frontmost=result.app_frontmost,
            error=result.error,
            prompt_length=prompt_length,
            prompt_hash=prompt_hash,
        )
        if self.event_log:
            self.event_log.append(
                "local_agent_codex_focus_succeeded"
                if result.succeeded
                else "local_agent_codex_focus_failed",
                attempt_index=attempt_index,
                click_point=result.fallback_click_point,
                plus_anchor_bbox=result.plus_button_bbox,
                codex_window_bounds=result.window_bounds,
                app_frontmost=result.app_frontmost,
                error=result.error,
            )
            self.event_log.append(
                "local_agent_composer_click_succeeded"
                if result.succeeded
                else "local_agent_composer_click_failed",
                attempt_index=attempt_index,
                click_point=result.fallback_click_point,
                plus_anchor_bbox=result.plus_button_bbox,
                codex_window_bounds=result.window_bounds,
                app_frontmost=result.app_frontmost,
                error=result.error,
            )
        return result

    def _set_and_verify_local_agent_clipboard(
        self,
        target: ManualStageTarget,
        prompt: str,
        prompt_hash: str,
        *,
        attempt_index: int,
    ) -> None:
        prompt_length = len(prompt)
        self._append_action_debug(
            phase="local_agent_paste",
            app=target.app_name,
            profile=target.profile,
            action="local_agent_clipboard_set_attempted",
            result="attempted",
            attempt_index=attempt_index,
            prompt_length=prompt_length,
            prompt_hash=prompt_hash,
        )
        if self.event_log:
            self.event_log.append(
                "local_agent_clipboard_set_attempted",
                attempt_index=attempt_index,
                local_agent_prompt_length=prompt_length,
                local_agent_prompt_hash=prompt_hash,
            )
        try:
            self.copy_text_to_clipboard(prompt)
        except Exception as error:
            self.last_local_agent_paste_failure_reason = "local_agent_clipboard_set_failed"
            self._append_action_debug(
                phase="local_agent_paste",
                app=target.app_name,
                profile=target.profile,
                action="local_agent_clipboard_set_succeeded",
                result="failed",
                attempt_index=attempt_index,
                error=str(error),
                prompt_length=prompt_length,
                prompt_hash=prompt_hash,
            )
            raise GuiAutomationError("local_agent_clipboard_set_failed") from error
        self._append_action_debug(
            phase="local_agent_paste",
            app=target.app_name,
            profile=target.profile,
            action="local_agent_clipboard_set_succeeded",
            result="succeeded",
            attempt_index=attempt_index,
            prompt_length=prompt_length,
            prompt_hash=prompt_hash,
        )
        readback = self.clipboard.read_text() if self.clipboard else None
        if readback is None:
            self.last_local_agent_clipboard_readback_matches_prompt_hash = None
            return
        readback_hash = hashlib.sha256(readback.encode("utf-8")).hexdigest()
        readback_matches = readback_hash == prompt_hash
        self.last_local_agent_clipboard_readback_matches_prompt_hash = readback_matches
        self._append_action_debug(
            phase="local_agent_paste",
            app=target.app_name,
            profile=target.profile,
            action="local_agent_clipboard_readback_verified",
            result="succeeded" if readback_matches else "failed",
            attempt_index=attempt_index,
            clipboard_length=len(readback),
            clipboard_hash=readback_hash,
            prompt_length=prompt_length,
            prompt_hash=prompt_hash,
        )
        if self.event_log:
            self.event_log.append(
                "local_agent_clipboard_readback_verified",
                attempt_index=attempt_index,
                clipboard_length=len(readback),
                clipboard_hash=readback_hash,
                clipboard_readback_matches_prompt_hash=readback_matches,
            )
        if not readback_matches:
            self.last_local_agent_paste_failure_reason = "local_agent_clipboard_readback_mismatch"
            raise GuiAutomationError("local_agent_clipboard_readback_mismatch")

    def _local_agent_paste_variants(self, target: ManualStageTarget) -> tuple[str, ...]:
        return self._paste_variants_for_backend(
            target.paste_backend,
            allow_keydown_variants=True,
        )

    def _paste_variants(self, target: ManualStageTarget) -> tuple[str, ...]:
        return production_pm_paste_variants_for_target(target)

    def _paste_variants_for_backend(
        self,
        paste_backend: str | None,
        *,
        allow_keydown_variants: bool,
    ) -> tuple[str, ...]:
        return paste_variants_for_backend(
            paste_backend,
            allow_keydown_variants=allow_keydown_variants,
        )

    def _paste_variant(self, target: ManualStageTarget, variant_name: str) -> None:
        if is_visual_pm_target(target) and variant_name in RAW_V_PRONE_PM_PASTE_VARIANTS:
            raise GuiAutomationError("pm_paste_raw_v_typed_instead_of_prompt")
        self._paste_local_agent_variant(target, variant_name)

    def _paste_local_agent_variant(
        self,
        target: ManualStageTarget,
        variant_name: str,
    ) -> None:
        if variant_name == "accessibility_set_focused_value":
            self._set_focused_accessibility_value_from_clipboard(target)
            return
        if variant_name == "menu_paste_accessibility":
            self._click_accessibility_menu_item(
                target,
                item_names=("Paste", "붙여넣기", "붙이기"),
            )
            return
        if variant_name == "system_events_command_v":
            self._run_system_events(
                'tell application "System Events" to keystroke "v" using command down'
            )
            return
        if variant_name == "system_events_key_code_v_command":
            self._run_system_events(
                'tell application "System Events" to key code 9 using command down'
            )
            return
        try:
            if self.pyautogui_hotkeyer is not None:
                if variant_name == "command_v_hotkey":
                    self.pyautogui_hotkeyer("command", "v")
                    return
                if variant_name == "cmd_v_hotkey":
                    self.pyautogui_hotkeyer("cmd", "v")
                    return
                if variant_name == "command_v_keydown":
                    self.pyautogui_hotkeyer("keyDown", "command")
                    self.pyautogui_hotkeyer("press", "v")
                    self.pyautogui_hotkeyer("keyUp", "command")
                    return
                if variant_name == "cmd_v_keydown":
                    self.pyautogui_hotkeyer("keyDown", "cmd")
                    self.pyautogui_hotkeyer("press", "v")
                    self.pyautogui_hotkeyer("keyUp", "cmd")
                    return
            else:
                import pyautogui

                if variant_name == "command_v_hotkey":
                    pyautogui.hotkey("command", "v", interval=0.1)
                    return
                if variant_name == "cmd_v_hotkey":
                    pyautogui.hotkey("cmd", "v", interval=0.1)
                    return
                if variant_name == "command_v_keydown":
                    pyautogui.keyDown("command")
                    try:
                        pyautogui.press("v")
                    finally:
                        pyautogui.keyUp("command")
                    return
                if variant_name == "cmd_v_keydown":
                    pyautogui.keyDown("cmd")
                    try:
                        pyautogui.press("v")
                    finally:
                        pyautogui.keyUp("cmd")
                    return
        except ModuleNotFoundError as error:
            raise GuiAutomationError(
                "PyAutoGUI paste backend is unavailable: pyautogui is not installed."
            ) from error
        except Exception as error:
            raise GuiAutomationError(f"PyAutoGUI paste failed: {error}") from error
        raise GuiAutomationError(f"Unsupported paste variant: {variant_name}")

    def _set_focused_accessibility_value_from_clipboard(
        self,
        target: ManualStageTarget,
    ) -> None:
        process_selector = _system_events_process_selector(target)
        script = f"""
tell application "System Events"
  set targetProcess to {process_selector}
  set focusedElement to value of attribute "AXFocusedUIElement" of targetProcess
  set clipboardText to do shell script "pbpaste"
  set value of focusedElement to clipboardText
end tell
""".strip()
        self._run_system_events(script)

    def _read_focused_accessibility_value(self, target: ManualStageTarget) -> str:
        process_selector = _system_events_process_selector(target)
        script = f"""
tell application "System Events"
  set targetProcess to {process_selector}
  set focusedElement to value of attribute "AXFocusedUIElement" of targetProcess
  try
    set focusedValue to value of focusedElement
    if focusedValue is missing value then return ""
    return focusedValue as text
  on error
    return ""
  end try
end tell
""".strip()
        return self._run_system_events_capture(script)

    def _clear_focused_accessibility_value(self, target: ManualStageTarget) -> None:
        process_selector = _system_events_process_selector(target)
        script = f"""
tell application "System Events"
  set targetProcess to {process_selector}
  set focusedElement to value of attribute "AXFocusedUIElement" of targetProcess
  set value of focusedElement to ""
end tell
""".strip()
        self._run_system_events(script)

    def _select_all_local_agent_text(self, target: ManualStageTarget) -> None:
        backend = (target.paste_backend or "system_events").strip().lower().replace("-", "_")
        if backend == "pyautogui":
            try:
                if self.pyautogui_hotkeyer is not None:
                    self.pyautogui_hotkeyer("command", "a")
                else:
                    import pyautogui

                    pyautogui.hotkey("command", "a", interval=0.1)
            except ModuleNotFoundError as error:
                raise GuiAutomationError(
                    "PyAutoGUI select-all backend is unavailable: pyautogui is not installed."
                ) from error
            except Exception as error:
                raise GuiAutomationError(f"PyAutoGUI select-all failed: {error}") from error
            return
        self._run_system_events('tell application "System Events" to keystroke "a" using command down')

    def _pm_submit_block_reason(self) -> str | None:
        if self.clipboard is None:
            return "pm_clipboard_set_failed"
        prompt = self.clipboard.read_text()
        if not prompt:
            return "pm_prompt_empty"
        if not self.last_pm_clipboard_set_attempted:
            return "pm_clipboard_set_not_attempted"
        if not self.last_pm_clipboard_set_succeeded:
            return "pm_clipboard_set_failed"
        if self.last_pm_clipboard_readback_matches_prompt_hash is False:
            return "pm_clipboard_readback_mismatch"
        if not self.last_pm_paste_attempted:
            return "pm_paste_not_attempted"
        if not self.last_pm_paste_backend_success:
            return "pm_paste_backend_failed"
        return None

    def submit(self) -> None:
        if self._is_chatgpt_asset_target():
            target = self.active_target
            prompt = self.clipboard.read_text() if self.clipboard is not None else ""
            prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else None
            submit_block_reason = self._pm_submit_block_reason()
            self._append_action_debug(
                phase="pm_submit_guard",
                app=target.app_name if target is not None else None,
                profile=target.profile if target is not None else None,
                action="pm_submit_guard_checked",
                result="succeeded" if submit_block_reason is None else "failed",
                failure_reason=submit_block_reason,
                prompt_length=len(prompt),
                prompt_hash=prompt_hash,
                pm_clipboard_set_attempted=self.last_pm_clipboard_set_attempted,
                pm_clipboard_set_succeeded=self.last_pm_clipboard_set_succeeded,
                pm_clipboard_readback_matches_prompt_hash=(
                    self.last_pm_clipboard_readback_matches_prompt_hash
                ),
                pm_paste_attempted=self.last_pm_paste_attempted,
                pm_paste_backend_success=self.last_pm_paste_backend_success,
                pm_raw_v_failure_detected=self.last_pm_raw_v_failure_detected,
                pm_paste_content_verified=self.last_pm_paste_content_verified,
                pm_prompt_sentinel_id=self.last_pm_prompt_sentinel_id,
                pm_prompt_sentinel_hash=self.last_pm_prompt_sentinel_hash,
                pm_prompt_sentinel_found=self.last_pm_prompt_sentinel_found,
                pm_paste_content_verification_method=(
                    self.last_pm_paste_content_verification_method
                ),
                pm_paste_copied_back_length=self.last_pm_paste_copied_back_length,
                pm_paste_copied_back_hash=self.last_pm_paste_copied_back_hash,
                pm_state_after_paste=self.last_pm_paste_state_after,
                pm_send_ready_after_paste=self.last_pm_paste_send_ready,
                pm_submit_ready_decision=(
                    self.last_pm_submit_ready_check.decision_reason
                    if self.last_pm_submit_ready_check
                    else None
                ),
                pm_submit_ready_asset=(
                    self.last_pm_submit_ready_check.matched_asset_path
                    if self.last_pm_submit_ready_check
                    else None
                ),
                pm_submit_ready_confidence=(
                    self.last_pm_submit_ready_check.confidence
                    if self.last_pm_submit_ready_check
                    else None
                ),
                pm_submit_ready_bbox=(
                    self.last_pm_submit_ready_check.bbox
                    if self.last_pm_submit_ready_check
                    else None
                ),
                pm_competing_stop_confidence=(
                    self.last_pm_submit_ready_check.stop_candidate_confidence
                    if self.last_pm_submit_ready_check
                    else None
                ),
            )
            if self.event_log:
                self.event_log.append(
                    "pm_submit_guard_checked",
                    prompt_length=len(prompt),
                    prompt_hash=prompt_hash,
                    pm_clipboard_set_attempted=self.last_pm_clipboard_set_attempted,
                    pm_clipboard_set_succeeded=self.last_pm_clipboard_set_succeeded,
                    pm_clipboard_readback_matches_prompt_hash=(
                        self.last_pm_clipboard_readback_matches_prompt_hash
                    ),
                    pm_paste_attempted=self.last_pm_paste_attempted,
                    pm_paste_backend_success=self.last_pm_paste_backend_success,
                    pm_raw_v_failure_detected=self.last_pm_raw_v_failure_detected,
                    pm_paste_content_verified=self.last_pm_paste_content_verified,
                    pm_prompt_sentinel_id=self.last_pm_prompt_sentinel_id,
                    pm_prompt_sentinel_hash=self.last_pm_prompt_sentinel_hash,
                    pm_prompt_sentinel_found=self.last_pm_prompt_sentinel_found,
                    pm_paste_content_verification_method=(
                        self.last_pm_paste_content_verification_method
                    ),
                    pm_paste_copied_back_length=self.last_pm_paste_copied_back_length,
                    pm_paste_copied_back_hash=self.last_pm_paste_copied_back_hash,
                    pm_state_after_paste=self.last_pm_paste_state_after,
                    pm_send_ready_after_paste=self.last_pm_paste_send_ready,
                    pm_submit_ready_decision=(
                        self.last_pm_submit_ready_check.decision_reason
                        if self.last_pm_submit_ready_check
                        else None
                    ),
                    pm_submit_ready_confidence=(
                        self.last_pm_submit_ready_check.confidence
                        if self.last_pm_submit_ready_check
                        else None
                    ),
                    block_reason=submit_block_reason,
                )
            if submit_block_reason is not None:
                blocked_action = "pm_submit_blocked_missing_paste"
                if submit_block_reason == "pm_prompt_content_verification_failed":
                    blocked_action = "pm_submit_guard_blocked_content_unverified"
                elif submit_block_reason in {
                    "pm_submit_ready_not_detected",
                    "pm_submit_ready_click_unsafe",
                    "pm_app_running_before_submit",
                    "pm_paste_state_unverified",
                }:
                    blocked_action = "pm_submit_guard_blocked_no_submit_ready"
                self._append_action_debug(
                    phase="pm_submit_guard",
                    app=target.app_name if target is not None else None,
                    profile=target.profile if target is not None else None,
                    action=blocked_action,
                    result="blocked",
                    failure_reason=submit_block_reason,
                    prompt_length=len(prompt),
                    prompt_hash=prompt_hash,
                    pm_prompt_sentinel_id=self.last_pm_prompt_sentinel_id,
                    pm_prompt_sentinel_hash=self.last_pm_prompt_sentinel_hash,
                    pm_prompt_sentinel_found=self.last_pm_prompt_sentinel_found,
                    pm_submit_ready_decision=(
                        self.last_pm_submit_ready_check.decision_reason
                        if self.last_pm_submit_ready_check
                        else None
                    ),
                    pm_submit_ready_confidence=(
                        self.last_pm_submit_ready_check.confidence
                        if self.last_pm_submit_ready_check
                        else None
                    ),
                )
                if self.event_log:
                    self.event_log.append(
                        blocked_action,
                        failure_reason=submit_block_reason,
                    )
                raise GuiAutomationError(submit_block_reason)
            self._append_action_debug(
                phase="pm_submit_guard",
                app=target.app_name if target is not None else None,
                profile=target.profile if target is not None else None,
                action="pm_submit_guard_allowed_by_submit_ready",
                result="succeeded",
                prompt_length=len(prompt),
                prompt_hash=prompt_hash,
                pm_submit_ready_decision=(
                    self.last_pm_submit_ready_check.decision_reason
                    if self.last_pm_submit_ready_check
                    else None
                ),
                pm_submit_ready_confidence=(
                    self.last_pm_submit_ready_check.confidence
                    if self.last_pm_submit_ready_check
                    else None
                ),
                pm_paste_content_verified=self.last_pm_paste_content_verified,
            )
            if self.event_log:
                self.event_log.append(
                    "pm_submit_guard_allowed_by_submit_ready",
                    pm_submit_ready_decision=(
                        self.last_pm_submit_ready_check.decision_reason
                        if self.last_pm_submit_ready_check
                        else None
                    ),
                    pm_submit_ready_confidence=(
                        self.last_pm_submit_ready_check.confidence
                        if self.last_pm_submit_ready_check
                        else None
                    ),
                    pm_paste_content_verified=self.last_pm_paste_content_verified,
                )
            self._append_action_debug(
                phase="pm_submit_guard",
                app=target.app_name if target is not None else None,
                profile=target.profile if target is not None else None,
                action="submit_after_paste_policy_used",
                result="succeeded",
                prompt_length=len(prompt),
                prompt_hash=prompt_hash,
                pm_paste_attempted=self.last_pm_paste_attempted,
                pm_paste_backend_success=self.last_pm_paste_backend_success,
            )
            self._append_action_debug(
                phase="pm_submit_guard",
                app=target.app_name if target is not None else None,
                profile=target.profile if target is not None else None,
                action="pm_submit_after_verified_paste",
                result="succeeded",
                prompt_length=len(prompt),
                prompt_hash=prompt_hash,
                pm_paste_attempted=self.last_pm_paste_attempted,
                pm_paste_backend_success=self.last_pm_paste_backend_success,
            )
            if self.event_log:
                self.event_log.append(
                    "submit_after_paste_policy_used",
                    phase="pm",
                    prompt_length=len(prompt),
                    prompt_hash=prompt_hash,
                )
                self.event_log.append(
                    "pm_submit_after_verified_paste",
                    prompt_length=len(prompt),
                    prompt_hash=prompt_hash,
                )
            if target is None:
                raise GuiAutomationError("pm_submit_control_not_found_after_paste")
            self._submit_pm_after_verified_paste(
                target=target,
                prompt_length=len(prompt),
                prompt_hash=prompt_hash,
            )
            return
        if self._is_chatgpt_target():
            dom = self._chatgpt_dom_client()
            wait_for_send_ready(
                dom,
                timeout_seconds=self.state_timeout_seconds,
                sleep_fn=self.sleep_fn,
                event_log=self.event_log,
            )
            click_send_button(dom)
            return
        if self.active_target is not None and self._is_codex_target(self.active_target):
            backend = (
                self.active_target.visual_anchor_click_backend
                or self.active_target.click_backend
                or "system_events"
            ).strip().lower().replace("-", "_")
            self._append_action_debug(
                phase="local_agent_submit",
                app=self.active_target.app_name,
                profile=self.active_target.profile,
                action="submit",
                result="attempted",
                backend=backend,
            )
            if backend == "pyautogui":
                try:
                    if self.pyautogui_hotkeyer is not None:
                        # Tests use this hook for keyboard interactions; pyautogui.press
                        # is intentionally not exposed on the adapter.
                        self.pyautogui_hotkeyer("enter")
                    else:
                        import pyautogui

                        pyautogui.press("enter")
                except ModuleNotFoundError as error:
                    raise GuiAutomationError(
                        "PyAutoGUI submit backend is unavailable: pyautogui is not installed."
                    ) from error
                except Exception as error:
                    raise GuiAutomationError(f"PyAutoGUI submit failed: {error}") from error
                if self.event_log:
                    self.event_log.append("local_agent_pyautogui_submit_attempted")
                self._append_action_debug(
                    phase="local_agent_submit",
                    app=self.active_target.app_name,
                    profile=self.active_target.profile,
                    action="submit",
                    result="succeeded",
                    backend=backend,
                )
                return
        self._run_system_events('tell application "System Events" to key code 36')
        if self.active_target is not None:
            self._append_action_debug(
                phase="submit",
                app=self.active_target.app_name,
                profile=self.active_target.profile,
                action="submit",
                result="succeeded",
                backend="system_events",
            )

    def _submit_pm_after_verified_paste(
        self,
        *,
        target: ManualStageTarget,
        prompt_length: int,
        prompt_hash: str | None,
    ) -> None:
        max_attempts = self._submit_after_paste_max_attempts(target)
        retry_delay = self._action_retry_delay_seconds(target)
        self._emit_terminal_debug(target, "paste checkpoint passed")
        self._emit_terminal_debug(target, "visible-text gates skipped by policy")
        last_control_check: PMSubmitReadyCheck | None = None
        last_detection: VisualStateDetection | None = None
        submit_clicked = False

        for attempt_index in range(1, max_attempts + 1):
            self._emit_terminal_debug(target, "locating submit control")
            self._append_action_debug(
                phase="pm_submit",
                app=target.app_name,
                profile=target.profile,
                action="pm_submit_control_locator_started",
                result="attempted",
                attempt_index=attempt_index,
                max_attempts=max_attempts,
                prompt_length=prompt_length,
                prompt_hash=prompt_hash,
            )
            if self.event_log:
                self.event_log.append(
                    "pm_submit_control_locator_started",
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                )

            self.activate_app(target)
            control_check = self._detect_pm_submit_ready(
                target,
                attempt_index=attempt_index,
                max_attempts=max_attempts,
            )
            last_control_check = control_check
            if control_check.click_point is None:
                self._append_action_debug(
                    phase="pm_submit",
                    app=target.app_name,
                    profile=target.profile,
                    action="pm_submit_control_not_found_after_paste",
                    result="failed" if attempt_index >= max_attempts else "retry",
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                    failure_reason=control_check.decision_reason,
                    prompt_length=prompt_length,
                    prompt_hash=prompt_hash,
                    submit_ready_decision=control_check.decision_reason,
                    send_candidate_confidence=control_check.confidence,
                    stop_candidate_confidence=control_check.stop_candidate_confidence,
                )
                self._emit_terminal_debug(
                    target,
                    "submit control not found after verified paste",
                )
                if attempt_index < max_attempts:
                    self.sleep_fn(retry_delay)
                    continue
                raise GuiAutomationError("pm_submit_control_not_found_after_paste")
            if not control_check.click_point_safe:
                self._append_action_debug(
                    phase="pm_submit",
                    app=target.app_name,
                    profile=target.profile,
                    action="pm_submit_click_point_unsafe",
                    result="failed",
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                    prompt_length=prompt_length,
                    prompt_hash=prompt_hash,
                    matched_asset_path=control_check.matched_asset_path,
                    confidence=control_check.confidence,
                    bbox=control_check.bbox,
                    click_point=control_check.click_point,
                    click_point_safe=control_check.click_point_safe,
                    submit_ready_decision=control_check.decision_reason,
                )
                self._emit_terminal_debug(target, "submit click point unsafe")
                raise GuiAutomationError("pm_submit_click_point_unsafe")

            asset_label = (
                Path(control_check.matched_asset_path).stem
                if control_check.matched_asset_path
                else "unknown"
            )
            self._append_action_debug(
                phase="pm_submit",
                app=target.app_name,
                profile=target.profile,
                action="pm_submit_control_located",
                result="succeeded",
                attempt_index=attempt_index,
                max_attempts=max_attempts,
                prompt_length=prompt_length,
                prompt_hash=prompt_hash,
                matched_asset_path=control_check.matched_asset_path,
                confidence=control_check.confidence,
                bbox=control_check.bbox,
                click_point=control_check.click_point,
                click_point_safe=control_check.click_point_safe,
                submit_ready_decision=control_check.decision_reason,
                competing_stop_confidence=control_check.stop_candidate_confidence,
                competing_stop_bbox=control_check.stop_candidate_bbox,
            )
            if self.event_log:
                self.event_log.append(
                    "pm_submit_control_located",
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                    matched_asset_path=control_check.matched_asset_path,
                    confidence=control_check.confidence,
                    click_point=control_check.click_point,
                    submit_ready_decision=control_check.decision_reason,
                )
            self._emit_terminal_debug(
                target,
                (
                    f"submit control found asset={asset_label} "
                    f"click_point={control_check.click_point} safe=yes"
                ),
            )
            self._append_action_debug(
                phase="pm_submit",
                app=target.app_name,
                profile=target.profile,
                action="pm_submit_attempted",
                result="attempted",
                attempt_index=attempt_index,
                max_attempts=max_attempts,
                prompt_length=prompt_length,
                prompt_hash=prompt_hash,
                matched_asset_path=control_check.matched_asset_path,
                confidence=control_check.confidence,
                click_point=control_check.click_point,
                submit_ready_decision=control_check.decision_reason,
                competing_stop_confidence=control_check.stop_candidate_confidence,
            )
            if self.event_log:
                self.event_log.append(
                    "pm_submit_attempted",
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                    matched_asset_path=control_check.matched_asset_path,
                    confidence=control_check.confidence,
                    submit_ready_decision=control_check.decision_reason,
                )
            self._emit_terminal_debug(
                target,
                f"submit attempt {attempt_index}/{max_attempts}",
            )
            self._click_point(control_check.click_point)
            submit_clicked = True
            self._append_action_debug(
                phase="pm_submit",
                app=target.app_name,
                profile=target.profile,
                action="pm_submit_click_completed",
                result="succeeded",
                attempt_index=attempt_index,
                max_attempts=max_attempts,
                prompt_length=prompt_length,
                prompt_hash=prompt_hash,
                matched_asset_path=control_check.matched_asset_path,
                confidence=control_check.confidence,
                click_point=control_check.click_point,
            )
            if self.event_log:
                self.event_log.append(
                    "pm_submit_click_completed",
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                    matched_asset_path=control_check.matched_asset_path,
                    confidence=control_check.confidence,
                    click_point=control_check.click_point,
                )
                self.event_log.append(
                    "pm_prompt_submitted_via_asset_state_machine",
                    matched_asset_path=control_check.matched_asset_path,
                    confidence=control_check.confidence,
                    submit_ready_decision=control_check.decision_reason,
                )
            self._emit_terminal_debug(target, "submit click completed")
            self.sleep_fn(retry_delay)

            try:
                last_detection = self._detect_asset_state(target)
            except GuiAutomationError as error:
                self._append_action_debug(
                    phase="pm_submit",
                    app=target.app_name,
                    profile=target.profile,
                    action="pm_submit_post_click_state_check_failed",
                    result="failed" if attempt_index >= max_attempts else "retry",
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                    prompt_length=prompt_length,
                    prompt_hash=prompt_hash,
                    error=str(error),
                )
                if attempt_index < max_attempts:
                    continue
                raise GuiAutomationError("pm_submit_not_reflected_after_click") from error

            self._append_action_debug(
                phase="pm_submit",
                app=target.app_name,
                profile=target.profile,
                action="pm_submit_post_click_state_observed",
                result="observed",
                attempt_index=attempt_index,
                max_attempts=max_attempts,
                prompt_length=prompt_length,
                prompt_hash=prompt_hash,
                pm_state_after_submit=last_detection.matched_state.value,
                matched_asset_path=last_detection.matched_asset_path,
                confidence=last_detection.confidence,
                window_bounds=last_detection.window_bounds,
            )
            self._emit_terminal_debug(
                target,
                f"state after submit={last_detection.matched_state.value}",
            )
            reflection_reason = self._pm_submit_reflection_reason(last_detection)
            if reflection_reason is not None:
                self._append_action_debug(
                    phase="pm_submit",
                    app=target.app_name,
                    profile=target.profile,
                    action="pm_submit_reflected_after_click",
                    result="succeeded",
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                    prompt_length=prompt_length,
                    prompt_hash=prompt_hash,
                    pm_state_after_submit=last_detection.matched_state.value,
                    reflection_reason=reflection_reason,
                )
                if self.event_log:
                    self.event_log.append(
                        "pm_submit_reflected_after_click",
                        attempt_index=attempt_index,
                        max_attempts=max_attempts,
                        pm_state_after_submit=last_detection.matched_state.value,
                        reflection_reason=reflection_reason,
                    )
                return

        failure_reason = (
            "pm_submit_not_reflected_after_click"
            if submit_clicked
            else "pm_submit_control_not_found_after_paste"
        )
        self._append_action_debug(
            phase="pm_submit",
            app=target.app_name,
            profile=target.profile,
            action=failure_reason,
            result="failed",
            max_attempts=max_attempts,
            prompt_length=prompt_length,
            prompt_hash=prompt_hash,
            submit_ready_decision=(
                last_control_check.decision_reason if last_control_check else None
            ),
            last_pm_state_after_submit=(
                last_detection.matched_state.value if last_detection else None
            ),
        )
        if self.event_log:
            self.event_log.append(
                failure_reason,
                max_attempts=max_attempts,
                submit_ready_decision=(
                    last_control_check.decision_reason if last_control_check else None
                ),
                last_pm_state_after_submit=(
                    last_detection.matched_state.value if last_detection else None
                ),
            )
        raise GuiAutomationError(failure_reason)

    def _pm_submit_reflection_reason(
        self,
        detection: VisualStateDetection,
    ) -> str | None:
        if detection.matched_state == VisualGuiState.RUNNING:
            return "running_detected"
        if self._best_visual_match_for_kind(detection, VisualAssetKind.STOP) is not None:
            return "stop_button_detected"
        if self._best_visual_match_for_kind(detection, VisualAssetKind.SEND) is None:
            return "send_button_disappeared"
        return None

    def inspect_local_agent_before_submit(
        self,
        target: ManualStageTarget,
        prompt: str,
    ) -> LocalAgentPreSubmitCheck:
        if self.clipboard is None:
            raise GuiAutomationError("Clipboard is not configured.")
        clipboard_text = self.clipboard.read_text()
        if self.codex_ui_detector is None:
            raise GuiAutomationError("Codex UI detector is not configured.")
        result = self.codex_ui_detector.inspect_before_submit(
            target=target,
            prompt=prompt,
            clipboard_text=clipboard_text,
        )
        focus_result = self.last_local_agent_focus_result
        if focus_result is None:
            return result
        return replace(
            result,
            app_frontmost=focus_result.app_frontmost,
            input_candidate_count=focus_result.input_candidate_count,
            selected_input_candidate_summary=focus_result.selected_input_candidate_summary,
            input_text_length_before_paste=focus_result.input_text_length_before_paste,
            input_text_length_after_paste=result.focused_text_length,
        )

    def inspect_local_agent_after_submit(
        self,
        target: ManualStageTarget,
        prompt: str,
        before: LocalAgentPreSubmitCheck,
    ) -> LocalAgentPostSubmitCheck:
        if self.codex_ui_detector is None:
            raise GuiAutomationError("Codex UI detector is not configured.")
        return self.codex_ui_detector.inspect_after_submit(
            target=target,
            prompt=prompt,
            before=before,
        )

    def wait_for_response(self, timeout_seconds: int) -> None:
        if self._is_chatgpt_asset_target():
            self._wait_for_asset_state(
                self.active_target,
                expected_state=VisualGuiState.RUNNING,
                timeout_seconds=min(timeout_seconds, self.state_timeout_seconds),
                allow_timeout=True,
            )
            self._wait_for_asset_state(
                self.active_target,
                expected_state=VisualGuiState.IDLE,
                timeout_seconds=timeout_seconds,
            )
            return
        if self._is_chatgpt_target():
            wait_for_response_copy_ready(
                self._chatgpt_dom_client(),
                timeout_seconds=timeout_seconds,
                sleep_fn=self.sleep_fn,
                event_log=self.event_log,
            )
            return
        self.sleep_fn(timeout_seconds)

    def copy_response_text(self) -> str:
        if self.clipboard is None:
            raise GuiAutomationError("Clipboard is not configured.")
        if self._is_chatgpt_asset_target():
            target = self.active_target
            if target is None:
                raise GuiAutomationError("No active ChatGPT Mac target is configured.")
            if self.codex_ui_detector is None:
                raise GuiAutomationError("Window detector is not configured.")
            active_before = self._frontmost_app_safe()
            self._append_action_debug(
                phase="pm_response_copy",
                app=target.app_name,
                profile=target.profile,
                action="read_active_app",
                result="succeeded" if active_before else "unknown",
                active_app=active_before,
            )
            if self._looks_like_codex_app(active_before):
                self._append_action_debug(
                    phase="pm_response_copy",
                    app=target.app_name,
                    profile=target.profile,
                    action="focus_guard",
                    result="codex_frontmost_before_copy",
                    active_app_before=active_before,
                )
            self._append_action_debug(
                phase="pm_response_copy",
                app=target.app_name,
                profile=target.profile,
                bundle_id=target.bundle_id,
                action="activate_app",
                result="attempted",
                reason="reactivate_before_response_copy_click",
                active_app_before=active_before,
            )
            self.activate_app(target)
            active_after = self._frontmost_app_safe()
            if self.event_log:
                self.event_log.append(
                    "pm_target_reactivated_before_copy",
                    app_name=target.app_name,
                    profile=target.profile,
                    bundle_id=target.bundle_id,
                    active_app_before=active_before,
                    active_app_after=active_after,
                )
            self._append_action_debug(
                phase="pm_response_copy",
                app=target.app_name,
                profile=target.profile,
                bundle_id=target.bundle_id,
                action="activate_app",
                result="succeeded",
                reason="reactivate_before_response_copy_click",
                active_app_before=active_before,
                active_app_after=active_after,
                pm_target_frontmost_verified=active_after == target.app_name,
            )
            if self._looks_like_codex_app(active_after):
                if self.event_log:
                    self.event_log.append(
                        "pm_target_not_frontmost_before_copy",
                        app_name=target.app_name,
                        profile=target.profile,
                        active_app_before=active_before,
                        active_app_after=active_after,
                    )
                raise GuiAutomationError("pm_target_not_frontmost_before_copy")
            bounds = self.codex_ui_detector.window_bounds(target)
            bounds_key = self._visual_window_key(target)
            old_bounds = self.last_visual_window_bounds.get(bounds_key)
            self._log_window_bounds_refresh(
                target=target,
                operation="pm_response_copy_detection",
                old_bounds=old_bounds,
                new_bounds=bounds,
            )
            self.last_visual_window_bounds[bounds_key] = bounds
            result = diagnose_chatgpt_mac_response_capture(
                target=target,
                window_bounds=bounds,
                attempt_copy=True,
                clipboard=self.clipboard,
                expected_marker=self.response_expected_marker,
                sleep_fn=self.sleep_fn,
            )
            if self.event_log:
                self.event_log.append(
                    "pm_response_copy_button_redetected",
                    app_name=target.app_name,
                    profile=result.asset_profile,
                    window_bounds=result.window_bounds,
                    copy_button_found=result.copy_button_found,
                    copy_button_bbox=result.copy_button_bbox,
                    copy_button_click_point=result.copy_button_click_point,
                    copy_button_click_point_safe=result.copy_button_click_point_safe,
                    matched_asset_path=result.matched_asset_path,
                    confidence=result.copy_button_confidence,
                    configured_threshold=result.configured_confidence_threshold,
                    effective_threshold=result.effective_confidence_threshold
                    or result.confidence_threshold,
                    threshold_cap_applied=result.threshold_cap_applied,
                    selected_scale=result.copy_button_selected_scale,
                    active_app_before=active_before,
                    active_app_after_reactivation=active_after,
                )
            self._append_action_debug(
                phase="pm_response_copy",
                app=target.app_name,
                profile=result.asset_profile,
                action="redetect_copy_button",
                result="succeeded" if result.copy_button_found else "failed",
                window_bounds=result.window_bounds,
                copy_button_bbox=result.copy_button_bbox,
                copy_button_click_point=result.copy_button_click_point,
                copy_button_click_point_safe=result.copy_button_click_point_safe,
                matched_asset_path=result.matched_asset_path,
                confidence=result.copy_button_confidence,
                configured_threshold=result.configured_confidence_threshold,
                effective_threshold=result.effective_confidence_threshold
                or result.confidence_threshold,
                threshold_cap_applied=result.threshold_cap_applied,
                selected_scale=result.copy_button_selected_scale,
                error=result.error,
            )
            if self.event_log:
                self.event_log.append(
                    "chatgpt_mac_response_capture_attempted",
                    window_bounds=result.window_bounds,
                    supported=result.supported,
                    copy_button_found=result.copy_button_found,
                    matched_asset_path=result.matched_asset_path,
                    confidence=result.copy_button_confidence,
                    configured_threshold=result.configured_confidence_threshold,
                    effective_threshold=result.effective_confidence_threshold
                    or result.confidence_threshold,
                    threshold_cap_applied=result.threshold_cap_applied,
                    copy_detection_attempt_count=result.copy_detection_attempt_count,
                    scroll_button_found=result.scroll_button_found,
                    scroll_attempted=result.scroll_attempted,
                    scroll_succeeded=result.scroll_succeeded,
                    response_captured=result.response_captured,
                    response_length=result.response_length,
                    error=result.error,
                )
            if not result.copy_button_found:
                raise GuiAutomationError(
                    "pm_response_copy_button_occluded_or_unavailable"
                    + (f": {result.error}" if result.error else "")
                )
            if not result.copy_button_click_point_safe:
                raise GuiAutomationError("pm_response_copy_button_occluded_or_unavailable")
            if not result.response_captured:
                raise GuiAutomationError(
                    result.error
                    or "ChatGPT Mac visual response capture did not produce a response."
                )
            if self.event_log:
                self.event_log.append(
                    "pm_response_copy_clicked",
                    app_name=target.app_name,
                    profile=result.asset_profile,
                    copy_button_click_point=result.copy_button_click_point,
                    matched_asset_path=result.matched_asset_path,
                    confidence=result.copy_button_confidence,
                    response_length=result.response_length,
                )
            self._append_action_debug(
                phase="pm_response_copy",
                app=target.app_name,
                profile=result.asset_profile,
                action="copy_response",
                result="succeeded",
                copy_button_click_point=result.copy_button_click_point,
                matched_asset_path=result.matched_asset_path,
                confidence=result.copy_button_confidence,
                response_length=result.response_length,
            )
            text = self.clipboard.read_text()
            if not text.strip():
                raise GuiAutomationError("Copied ChatGPT Mac PM response was empty.")
            return text
        if self._is_chatgpt_target():
            try:
                selectors = self._response_copy_selectors()
                result = copy_response_with_strategies(
                    dom=self._chatgpt_dom_client(),
                    clipboard=self.clipboard,
                    selectors=selectors,
                    expected_marker=self.response_expected_marker,
                    generic_copy_fallback=lambda: self._run_system_events(
                        'tell application "System Events" to keystroke "c" using command down'
                    ),
                    event_log=self.event_log,
                )
                return result.text
            except ChatGPTStateMachineError as error:
                raise GuiAutomationError(str(error)) from error
        self._run_system_events('tell application "System Events" to keystroke "c" using command down')
        text = self.clipboard.read_text()
        if not text.strip():
            raise GuiAutomationError("Copied PM response was empty.")
        return text

    def expect_response_contains(self, marker: str | None) -> None:
        self.response_expected_marker = marker

    def set_local_agent_queue_handoff_mode(self, enabled: bool) -> None:
        self.local_agent_queue_handoff_mode = enabled

    def _wait_for_asset_idle(
        self,
        target: ManualStageTarget | None,
        *,
        allow_preexisting_text: bool = False,
    ):
        if target is None:
            raise GuiAutomationError("No active visual GUI target is configured.")
        if allow_preexisting_text and is_visual_pm_target(target):
            started = time.monotonic()
            max_attempts = self._state_machine_max_attempts(target)
            retry_delay = self._state_machine_retry_delay_seconds(target)
            last_detection: VisualStateDetection | None = None
            for attempt_index in range(1, max_attempts + 1):
                detection = self._detect_asset_state(target)
                last_detection = detection
                elapsed = max(0.0, time.monotonic() - started)
                self._append_event(
                    "asset_visual_idle_wait_poll",
                    {
                        "app_name": target.app_name,
                        "profile": detection.asset_profile,
                        "observed_state": detection.matched_state.value,
                        "poll_count": attempt_index - 1,
                        "elapsed_seconds": elapsed,
                        "remaining_seconds": None,
                        "decision": (
                            "proceed"
                            if detection.matched_state
                            in {VisualGuiState.IDLE, VisualGuiState.COMPOSER_HAS_TEXT}
                            else "retry"
                            if attempt_index < max_attempts
                            else "fail"
                        ),
                    },
                )
                if detection.matched_state == VisualGuiState.IDLE:
                    self._append_event(
                        "asset_visual_idle_detected",
                        {
                            "app_name": target.app_name,
                            "profile": detection.asset_profile,
                            "decision": "proceed",
                        },
                    )
                    return VisualIdleWaitResult(
                        selected_app=target.app_name,
                        asset_profile=detection.asset_profile,
                        final_state=detection.matched_state,
                        poll_count=attempt_index - 1,
                        elapsed_seconds=elapsed,
                        timeout_action=None,
                        should_proceed=True,
                        should_overwrite=False,
                        should_abort=False,
                        detection=detection,
                    )
                if detection.matched_state == VisualGuiState.COMPOSER_HAS_TEXT:
                    self._append_event(
                        "asset_visual_preexisting_text_detected",
                        {
                            "app_name": target.app_name,
                            "profile": detection.asset_profile,
                            "decision": "verify_or_overwrite",
                        },
                    )
                    return VisualIdleWaitResult(
                        selected_app=target.app_name,
                        asset_profile=detection.asset_profile,
                        final_state=detection.matched_state,
                        poll_count=attempt_index - 1,
                        elapsed_seconds=elapsed,
                        timeout_action=None,
                        should_proceed=True,
                        should_overwrite=False,
                        should_abort=False,
                        detection=detection,
                    )
                if detection.matched_state == VisualGuiState.AMBIGUOUS:
                    return VisualIdleWaitResult(
                        selected_app=target.app_name,
                        asset_profile=detection.asset_profile,
                        final_state=detection.matched_state,
                        poll_count=attempt_index - 1,
                        elapsed_seconds=elapsed,
                        timeout_action=None,
                        should_proceed=False,
                        should_overwrite=False,
                        should_abort=True,
                        detection=detection,
                        error=(
                            f"{target.app_name} visual state is ambiguous; paste/submit is "
                            "blocked until the state assets are calibrated."
                        ),
                    )
                if detection.matched_state == VisualGuiState.RUNNING:
                    break
                if attempt_index < max_attempts:
                    self.sleep_fn(retry_delay)
            if (
                last_detection is not None
                and last_detection.matched_state == VisualGuiState.UNKNOWN
            ):
                return VisualIdleWaitResult(
                    selected_app=target.app_name,
                    asset_profile=last_detection.asset_profile,
                    final_state=last_detection.matched_state,
                    poll_count=max_attempts,
                    elapsed_seconds=max(0.0, time.monotonic() - started),
                    timeout_action=None,
                    should_proceed=False,
                    should_overwrite=False,
                    should_abort=True,
                    detection=last_detection,
                    error="pm_paste_state_unverified",
                )
        result = wait_for_visual_idle(
            target=target,
            detect_once=lambda: self._detect_asset_state(target),
            timeout_seconds=target.idle_empty_timeout_seconds
            or target.busy_placeholder_wait_timeout_seconds,
            poll_interval_seconds=target.idle_empty_poll_interval_seconds
            or target.busy_placeholder_poll_interval_seconds,
            on_timeout=target.on_busy_timeout,
            sleep_fn=self.sleep_fn,
            event_callback=self._append_event,
        )
        if result.should_abort:
            raise GuiAutomationError(result.error or f"{target.app_name} visual idle wait aborted.")
        if result.should_overwrite and self.event_log:
            self.event_log.append(
                "asset_visual_timeout_policy_overwrite",
                app_name=target.app_name,
                profile=result.asset_profile,
                poll_count=result.poll_count,
            )
        return result

    def _wait_for_asset_state(
        self,
        target: ManualStageTarget | None,
        *,
        expected_state: VisualGuiState,
        timeout_seconds: float,
        allow_timeout: bool = False,
    ):
        if target is None:
            raise GuiAutomationError("No active visual GUI target is configured.")
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        last_detection = self._detect_asset_state(target)
        while True:
            if last_detection.matched_state == expected_state:
                return last_detection
            if time.monotonic() >= deadline:
                if allow_timeout:
                    return last_detection
                raise GuiAutomationError(
                    f"{target.app_name} did not enter visual state {expected_state.value}; "
                    f"last_state={last_detection.matched_state.value}."
                )
            self.sleep_fn(1.0)
            last_detection = self._detect_asset_state(target)

    def _detect_asset_state(self, target: ManualStageTarget):
        if is_visual_pm_target(target):
            target = normalize_visual_pm_target(target)
            self._log_visual_pm_asset_inventory_once(target)
        if self.asset_state_detector is None:
            raise GuiAutomationError("Asset visual state detector is not configured.")
        if self.codex_ui_detector is None:
            raise GuiAutomationError("Window detector is not configured.")
        max_attempts = self._state_machine_max_attempts(target)
        retry_delay = self._state_machine_retry_delay_seconds(target)
        last_error: Exception | None = None
        result = None
        attempt_index = 0
        for attempt_index in range(1, max_attempts + 1):
            try:
                self._emit_terminal_debug(
                    target,
                    f"detect state attempt {attempt_index}/{max_attempts}",
                )
                if self.app_activator is not None:
                    self.app_activator.activate(
                        target.app_name,
                        app_path=target.app_path,
                        bundle_id=target.bundle_id,
                    )
                bounds = self.codex_ui_detector.window_bounds(target)
                bounds_key = self._visual_window_key(target)
                old_bounds = self.last_visual_window_bounds.get(bounds_key)
                self._log_window_bounds_refresh(
                    target=target,
                    operation="detect_visual_state",
                    old_bounds=old_bounds,
                    new_bounds=bounds,
                )
                self.last_visual_window_bounds[bounds_key] = bounds
                result = self.asset_state_detector.detect(
                    target=target,
                    window_bounds=bounds,
                    profile=asset_profile_for_target(target),
                    logs_dir=Path(self.debug_logs_dir) if self.debug_logs_dir else None,
                    write_debug=self.debug_screenshots,
                )
                break
            except Exception as error:
                last_error = error
                self._emit_terminal_debug(
                    target,
                    (
                        f"detect state attempt {attempt_index}/{max_attempts} failed: "
                        f"{error}"
                    ),
                )
                self._append_state_debug(
                    phase="visual_detect",
                    app=target.app_name,
                    profile=target.profile,
                    bundle_id=target.bundle_id,
                    state_name=VisualGuiState.UNKNOWN.value,
                    result="failed",
                    operation="detect_visual_state",
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                    decision="retry" if attempt_index < max_attempts else "fail",
                    error=str(error),
                )
                if attempt_index >= max_attempts:
                    raise GuiAutomationError(
                        f"{target.app_name} visual state detection failed: {error}"
                    ) from error
                self.sleep_fn(retry_delay)
        if result is None:
            raise GuiAutomationError(
                f"{target.app_name} visual state detection failed: {last_error}"
            )
        if self.event_log:
            self.event_log.append(
                "asset_visual_state_detected",
                app_name=target.app_name,
                profile=result.asset_profile,
                matched_state=result.matched_state.value,
                matched_asset_path=result.matched_asset_path,
                confidence=result.confidence,
                state_ambiguous=result.state_ambiguous,
                state_selection_reason=result.state_selection_reason,
                plus_anchor_found=result.plus_anchor_found,
                window_bounds=result.window_bounds,
            )
        confidence_text = f"{result.confidence:.3f}" if result.confidence is not None else "unavailable"
        self._emit_terminal_debug(
            target,
            (
                f"state={result.matched_state.value} confidence={confidence_text} "
                f"plus_anchor_found={'yes' if result.plus_anchor_found else 'no'}"
            ),
        )
        self._emit_terminal_debug(target, f"selected state={result.matched_state.value}")
        state_key = f"detect:{target.app_name}:{result.asset_profile}"
        previous_state = self.state_machine_previous_states.get(state_key)
        selected_state = result.matched_state.value
        self.state_machine_previous_states[state_key] = selected_state
        self._append_state_debug(
            phase="visual_detect",
            app=target.app_name,
            profile=result.asset_profile,
            bundle_id=target.bundle_id,
            state_name=result.matched_state.value,
            result="succeeded" if result.backend_available else "failed",
            operation="detect_visual_state",
            attempt_index=attempt_index,
            max_attempts=max_attempts,
            previous_state=previous_state,
            detected_state=selected_state,
            selected_state=selected_state,
            transition=f"{previous_state or 'START'}->{selected_state}",
            decision="selected",
            ambiguity_reason=result.state_selection_reason if result.state_ambiguous else None,
            selected_window_bounds=result.window_bounds,
            screenshot_captured=result.screenshot_captured,
            screenshot_path=result.screenshot_path,
            annotated_screenshot_path=result.annotated_screenshot_path,
            safe_region_bounds=result.safe_region_bounds,
            plus_search_region_bounds=result.plus_search_region_bounds,
            matched_asset_path=result.matched_asset_path,
            matched_bbox=result.matched_bbox,
            confidence=result.confidence,
            state_ambiguous=result.state_ambiguous,
            state_selection_reason=result.state_selection_reason,
            plus_anchor_found=result.plus_anchor_found,
            plus_anchor_bbox=result.plus_anchor_bbox,
            plus_anchor_confidence=result.plus_anchor_confidence,
            computed_composer_click_point=result.computed_composer_click_point,
            composer_click_point_safe=result.composer_click_point_safe,
            template_diagnostics=[
                {
                    "asset_kind": diagnostic.asset_kind.value,
                    "state": diagnostic.state.value if diagnostic.state else None,
                    "template_path": diagnostic.template_path,
                    "template_exists": diagnostic.template_exists,
                    "search_region_bounds": diagnostic.search_region_bounds,
                    "original_template_size": diagnostic.original_template_size,
                    "template_size": diagnostic.template_size,
                    "selected_scale": diagnostic.selected_scale,
                    "best_match_bbox": diagnostic.best_match_bbox,
                    "best_match_confidence": diagnostic.best_match_confidence,
                    "appearance_score": diagnostic.appearance_score,
                    "edge_score": getattr(diagnostic, "edge_score", None),
                    "glyph_score": getattr(diagnostic, "glyph_score", None),
                    "composite_score": getattr(diagnostic, "composite_score", None),
                    "score_gap_to_next_best": getattr(
                        diagnostic, "score_gap_to_next_best", None
                    ),
                    "threshold": diagnostic.threshold,
                    "configured_threshold": (
                        diagnostic.configured_threshold
                        if diagnostic.configured_threshold is not None
                        else diagnostic.threshold
                    ),
                    "effective_threshold": (
                        diagnostic.effective_threshold
                        if diagnostic.effective_threshold is not None
                        else diagnostic.threshold
                    ),
                    "threshold_cap_applied": diagnostic.threshold_cap_applied,
                    "accepted": diagnostic.accepted,
                    "rejection_reason": diagnostic.rejection_reason,
                }
                for diagnostic in result.template_diagnostics
            ],
            error=result.error,
        )
        self._append_template_detection_debug(
            target=target,
            detection=result,
            attempt_index=attempt_index,
            max_attempts=max_attempts,
        )
        return result

    def _detect_pm_submit_ready(
        self,
        target: ManualStageTarget,
        detection: VisualStateDetection | None = None,
        *,
        attempt_index: int | None = None,
        max_attempts: int | None = None,
        paste_variant: str | None = None,
        paste_backend: str | None = None,
    ) -> PMSubmitReadyCheck:
        self._append_action_debug(
            phase="pm_submit_ready",
            app=target.app_name,
            profile=target.profile,
            action="pm_submit_ready_check_started",
            result="attempted",
            attempt_index=attempt_index,
            max_attempts=max_attempts,
            paste_variant=paste_variant,
            paste_backend=paste_backend,
        )
        if self.event_log:
            self.event_log.append(
                "pm_submit_ready_check_started",
                attempt_index=attempt_index,
                paste_variant=paste_variant,
                paste_backend=paste_backend,
            )
        if detection is None:
            detection = self._detect_asset_state(target)

        if detection.state_ambiguous:
            self._append_action_debug(
                phase="pm_submit_ready",
                app=target.app_name,
                profile=target.profile,
                action="pm_global_state_ambiguous_after_paste",
                result="observed",
                attempt_index=attempt_index,
                max_attempts=max_attempts,
                global_selected_state=detection.matched_state.value,
                ambiguity_reason=detection.state_selection_reason,
                selected_window_bounds=detection.window_bounds,
                paste_variant=paste_variant,
                paste_backend=paste_backend,
            )
            if self.event_log:
                self.event_log.append(
                    "pm_global_state_ambiguous_after_paste",
                    global_selected_state=detection.matched_state.value,
                    ambiguity_reason=detection.state_selection_reason,
                )

        send_match = self._best_visual_match_for_kind(detection, VisualAssetKind.SEND)
        stop_match = self._best_visual_match_for_kind(detection, VisualAssetKind.STOP)
        send_click_point = _rect_center(send_match.bbox) if send_match is not None else None
        click_safe = (
            _point_inside_bounds(send_click_point, detection.window_bounds)
            if send_click_point is not None
            else False
        )
        send_thresholds = self._template_thresholds_for_kind(detection, VisualAssetKind.SEND)
        stop_dominant = False
        if stop_match is not None and send_match is not None:
            dominance_margin = max(0.05, float(target.visual_state_ambiguity_margin))
            stop_dominant = stop_match.confidence > send_match.confidence + dominance_margin
        elif stop_match is not None and send_match is None:
            stop_dominant = True

        if send_match is None:
            ready = False
            reason = (
                "pm_app_running_before_submit"
                if stop_dominant
                else "pm_submit_ready_send_not_detected"
            )
        elif not click_safe:
            ready = False
            reason = "pm_submit_ready_click_unsafe"
        elif stop_dominant:
            ready = False
            reason = "pm_app_running_before_submit"
        else:
            ready = True
            reason = (
                "pm_submit_ready_with_global_state_ambiguous"
                if detection.state_ambiguous
                else "pm_submit_ready_send_detected"
            )

        check = PMSubmitReadyCheck(
            ready=ready,
            decision_reason=reason,
            global_state=detection.matched_state.value,
            global_state_ambiguous=detection.state_ambiguous,
            matched_asset_path=send_match.template_path if send_match else None,
            confidence=send_match.confidence if send_match else None,
            configured_threshold=send_thresholds[0],
            effective_threshold=send_thresholds[1],
            bbox=send_match.bbox if send_match else None,
            click_point=send_click_point,
            click_point_safe=click_safe,
            stop_candidate_asset_path=stop_match.template_path if stop_match else None,
            stop_candidate_confidence=stop_match.confidence if stop_match else None,
            stop_candidate_bbox=stop_match.bbox if stop_match else None,
        )
        self.last_pm_submit_ready_check = check
        if check.ready and detection.state_ambiguous:
            self._append_action_debug(
                phase="pm_submit_ready",
                app=target.app_name,
                profile=target.profile,
                action="pm_submit_ready_with_global_state_ambiguous",
                result="succeeded",
                attempt_index=attempt_index,
                max_attempts=max_attempts,
                selected_window_bounds=detection.window_bounds,
                send_candidate_confidence=check.confidence,
                send_candidate_bbox=check.bbox,
                stop_candidate_confidence=check.stop_candidate_confidence,
                stop_candidate_bbox=check.stop_candidate_bbox,
                global_selected_state=check.global_state,
                submit_ready_decision=check.decision_reason,
                paste_variant=paste_variant,
                paste_backend=paste_backend,
            )
            if self.event_log:
                self.event_log.append(
                    "pm_submit_ready_with_global_state_ambiguous",
                    send_candidate_confidence=check.confidence,
                    stop_candidate_confidence=check.stop_candidate_confidence,
                    submit_ready_decision=check.decision_reason,
                )
        self._append_action_debug(
            phase="pm_submit_ready",
            app=target.app_name,
            profile=target.profile,
            action="pm_submit_ready_check_result",
            result="succeeded" if check.ready else "failed",
            attempt_index=attempt_index,
            max_attempts=max_attempts,
            selected_window_bounds=detection.window_bounds,
            send_candidate_confidence=check.confidence,
            send_candidate_bbox=check.bbox,
            matched_submit_asset=check.matched_asset_path,
            configured_threshold=check.configured_threshold,
            effective_threshold=check.effective_threshold,
            click_point=check.click_point,
            click_point_safe=check.click_point_safe,
            stop_candidate_confidence=check.stop_candidate_confidence,
            stop_candidate_bbox=check.stop_candidate_bbox,
            competing_stop_asset=check.stop_candidate_asset_path,
            global_selected_state=check.global_state,
            global_state_ambiguous=check.global_state_ambiguous,
            submit_ready_decision=check.decision_reason,
            paste_variant=paste_variant,
            paste_backend=paste_backend,
        )
        if self.event_log:
            self.event_log.append(
                "pm_submit_ready_check_result",
                submit_ready=check.ready,
                send_candidate_confidence=check.confidence,
                send_candidate_bbox=check.bbox,
                stop_candidate_confidence=check.stop_candidate_confidence,
                stop_candidate_bbox=check.stop_candidate_bbox,
                global_selected_state=check.global_state,
                global_state_ambiguous=check.global_state_ambiguous,
                decision_reason=check.decision_reason,
            )
        send_conf = f"{check.confidence:.3f}" if check.confidence is not None else "unavailable"
        stop_conf = (
            f"{check.stop_candidate_confidence:.3f}"
            if check.stop_candidate_confidence is not None
            else "unavailable"
        )
        self._emit_terminal_debug(
            target,
            (
                "submit-ready check: "
                f"send confidence={send_conf} "
                f"threshold={check.effective_threshold if check.effective_threshold is not None else 'unavailable'} "
                f"accepted={'yes' if check.ready else 'no'} "
                f"stop_confidence={stop_conf} "
                f"reason={check.decision_reason}"
            ),
        )
        if check.ready and detection.state_ambiguous:
            self._emit_terminal_debug(
                target,
                "global state ambiguous, but submit-ready is positive after paste",
            )
        return check

    def _wait_for_pm_submit_ready(
        self,
        target: ManualStageTarget,
        *,
        timeout_seconds: float,
    ) -> PMSubmitReadyCheck:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        last_check = self._detect_pm_submit_ready(target)
        while True:
            if last_check.ready:
                return last_check
            if last_check.decision_reason == "pm_app_running_before_submit":
                raise GuiAutomationError("pm_app_running_before_submit")
            if time.monotonic() >= deadline:
                raise GuiAutomationError(last_check.decision_reason)
            self.sleep_fn(1.0)
            last_check = self._detect_pm_submit_ready(target)

    def _best_visual_match_for_kind(
        self,
        detection: VisualStateDetection,
        kind: VisualAssetKind,
    ) -> VisualAssetMatch | None:
        match = max(
            (candidate for candidate in detection.matches if candidate.asset_kind == kind),
            key=lambda candidate: candidate.confidence,
            default=None,
        )
        if match is not None:
            return match
        if (
            kind == VisualAssetKind.SEND
            and detection.matched_state == VisualGuiState.COMPOSER_HAS_TEXT
            and detection.matched_bbox is not None
        ):
            return VisualAssetMatch(
                asset_kind=VisualAssetKind.SEND,
                state=VisualGuiState.COMPOSER_HAS_TEXT,
                template_path=detection.matched_asset_path or "",
                bbox=detection.matched_bbox,
                confidence=detection.confidence or 0.0,
                template_size=(detection.matched_bbox[2], detection.matched_bbox[3]),
            )
        if (
            kind == VisualAssetKind.STOP
            and detection.matched_state == VisualGuiState.RUNNING
            and detection.matched_bbox is not None
        ):
            return VisualAssetMatch(
                asset_kind=VisualAssetKind.STOP,
                state=VisualGuiState.RUNNING,
                template_path=detection.matched_asset_path or "",
                bbox=detection.matched_bbox,
                confidence=detection.confidence or 0.0,
                template_size=(detection.matched_bbox[2], detection.matched_bbox[3]),
            )
        return None

    def _template_thresholds_for_kind(
        self,
        detection: VisualStateDetection,
        kind: VisualAssetKind,
    ) -> tuple[float | None, float | None]:
        for diagnostic in detection.template_diagnostics:
            if diagnostic.asset_kind == kind:
                configured = (
                    diagnostic.configured_threshold
                    if diagnostic.configured_threshold is not None
                    else diagnostic.threshold
                )
                effective = (
                    diagnostic.effective_threshold
                    if diagnostic.effective_threshold is not None
                    else diagnostic.threshold
                )
                return configured, effective
        return None, None

    def _click_asset_composer_anchor(self, target: ManualStageTarget | None) -> None:
        if target is None:
            raise GuiAutomationError("No active visual GUI target is configured.")
        detection = self._detect_asset_state(target)
        if not detection.plus_anchor_found or not detection.composer_click_point_safe:
            raise GuiAutomationError(
                f"{target.app_name} plus anchor was not safely detected for composer focus."
            )
        if detection.computed_composer_click_point is None:
            raise GuiAutomationError(f"{target.app_name} composer click point was unavailable.")
        self._click_point(detection.computed_composer_click_point)
        self._append_action_debug(
            phase="composer_focus",
            app=target.app_name,
            profile=detection.asset_profile,
            action="click_composer",
            result="succeeded",
            click_point=detection.computed_composer_click_point,
            plus_anchor_bbox=detection.plus_anchor_bbox,
            plus_anchor_confidence=detection.plus_anchor_confidence,
            backend=target.click_backend,
        )
        if self.event_log:
            self.event_log.append(
                "asset_visual_composer_anchor_clicked",
                app_name=target.app_name,
                profile=detection.asset_profile,
                click_point=detection.computed_composer_click_point,
                plus_anchor_bbox=detection.plus_anchor_bbox,
                plus_anchor_confidence=detection.plus_anchor_confidence,
            )

    def _click_point(self, point: tuple[int, int]) -> None:
        try:
            import pyautogui

            pyautogui.click(point[0], point[1])
        except ModuleNotFoundError as error:
            raise GuiAutomationError(
                "PyAutoGUI click backend is unavailable: pyautogui is not installed."
            ) from error
        except Exception as error:
            raise GuiAutomationError(f"PyAutoGUI click failed: {error}") from error

    def _frontmost_app_safe(self) -> str | None:
        if self.codex_ui_detector is None:
            return None
        try:
            return self.codex_ui_detector.frontmost_app()
        except Exception:
            return None

    def _looks_like_codex_app(self, app_name: str | None) -> bool:
        return "codex" in (app_name or "").lower()

    def _is_chatgpt_target(self) -> bool:
        if self.active_target is None:
            return False
        # Legacy DOM backends are kept out of the production visual PM profiles.
        backend = (self.active_target.backend or "").lower()
        if backend in {"chrome_js", "browser_apple_events"}:
            return True
        if backend in {"chatgpt_pwa_js", "accessibility_fallback", "unsupported"}:
            return False
        combined = " ".join(
            part
            for part in [self.active_target.app_name, self.active_target.window_hint]
            if part
        ).lower()
        return "chatgpt" in combined or "chrome" in combined

    def _is_chatgpt_asset_target(self) -> bool:
        return is_visual_pm_target(self.active_target)

    def _uses_asset_state_target(self, target: ManualStageTarget) -> bool:
        profile = (target.visual_asset_profile or "").lower().replace("-", "_")
        backend = (target.backend or "").lower().replace("-", "_")
        return profile in {"chatgpt_mac", "chatgpt_chrome_app", "codex"} or backend in {
            "chatgpt_mac_visual",
            "chatgpt_chrome_app_visual",
            "asset_visual",
        }

    def _is_codex_target(self, target: ManualStageTarget) -> bool:
        combined = " ".join(
            part
            for part in [target.app_name, target.bundle_id, target.window_hint]
            if part
        ).lower()
        return "codex" in combined

    def _chatgpt_dom_client(self) -> MacOSChromeJavaScriptDomClient:
        if self.active_target is None:
            raise GuiAutomationError("No active ChatGPT target is configured.")
        return MacOSChromeJavaScriptDomClient(
            app_name=self.active_target.app_name,
            osascript_executable=self.osascript_executable,
        )

    def _response_copy_selectors(self) -> ResponseCopySelectors:
        if self.active_target is None:
            return ResponseCopySelectors()
        defaults = ResponseCopySelectors()
        return ResponseCopySelectors(
            css_selector=self.active_target.response_copy_css_selector or defaults.css_selector,
            xpath=self.active_target.response_copy_xpath or defaults.xpath,
            full_xpath=self.active_target.response_copy_full_xpath or defaults.full_xpath,
        )

    def _expected_composer_marker(self, text: str) -> str | None:
        for marker in ("AGENT_BRIDGE", "CODEX_NEXT_PROMPT"):
            if marker in text:
                return marker
        return None

    def _idle_empty_timeout_seconds(self) -> float:
        if self.active_target and self.active_target.idle_empty_timeout_seconds is not None:
            return float(self.active_target.idle_empty_timeout_seconds)
        return 600.0

    def _idle_empty_poll_interval_seconds(self) -> float:
        if self.active_target:
            return float(self.active_target.idle_empty_poll_interval_seconds)
        return 10.0


def _normalize_backend(value: str | None) -> str:
    normalized = (value or "system_events").strip().lower().replace("-", "_")
    if normalized in {"system_events", "systemevents", "osascript"}:
        return "system_events"
    if normalized == "pyautogui":
        return "pyautogui"
    return normalized


RAW_V_PRONE_PM_PASTE_VARIANTS = frozenset(
    {
        "command_v_hotkey",
        "cmd_v_hotkey",
        "command_v_keydown",
        "cmd_v_keydown",
    }
)


DIAGNOSTIC_ONLY_PM_PASTE_VARIANTS = (
    "system_events_command_v",
    "cmd_v_hotkey",
    "command_v_hotkey",
    "command_v_keydown",
    "cmd_v_keydown",
)


def pm_paste_backends_for_target(target: ManualStageTarget) -> tuple[str, ...]:
    raw_backends = tuple(
        _normalize_backend(backend)
        for backend in getattr(target, "paste_backends", ())
        if str(backend).strip()
    )
    if raw_backends:
        return raw_backends
    return (_normalize_backend(target.paste_backend),)


def pm_paste_backend_for_variant(variant_name: str) -> str | None:
    if variant_name == "accessibility_set_focused_value":
        return "accessibility_set_focused_value"
    if variant_name == "menu_paste_accessibility":
        return "menu_paste_accessibility"
    if variant_name == "system_events_command_v":
        return "system_events_command_v"
    if variant_name == "system_events_key_code_v_command":
        return "system_events_key_code_v_command"
    if variant_name == "cmd_v_hotkey":
        return "pyautogui_hotkey_cmd_v"
    if variant_name == "command_v_hotkey":
        return "pyautogui_hotkey_command_v"
    if variant_name == "command_v_keydown":
        return "pyautogui_keydown_command_v"
    if variant_name == "cmd_v_keydown":
        return "pyautogui_keydown_cmd_v"
    return None


def paste_variants_for_backend(
    paste_backend: str | None,
    *,
    allow_keydown_variants: bool,
) -> tuple[str, ...]:
    backend = _normalize_backend(paste_backend)
    if backend == "pyautogui":
        if allow_keydown_variants:
            return (
                "command_v_hotkey",
                "cmd_v_hotkey",
                "command_v_keydown",
                "cmd_v_keydown",
            )
        return ("cmd_v_hotkey",)
    if backend in {"pyautogui_hotkey_command_v", "command_v_hotkey"}:
        return ("command_v_hotkey",)
    if backend in {"pyautogui_hotkey_cmd_v", "cmd_v_hotkey"}:
        return ("cmd_v_hotkey",)
    if backend in {"pyautogui_keydown_command_v", "command_v_keydown"}:
        return ("command_v_keydown",)
    if backend in {"pyautogui_keydown_cmd_v", "cmd_v_keydown"}:
        return ("cmd_v_keydown",)
    if backend in {"system_events", "system_events_command_v"}:
        return ("system_events_command_v",)
    if backend in {"system_events_key_code_v_command", "system_events_keycode_command_v"}:
        return ("system_events_key_code_v_command",)
    if backend in {"menu_paste_accessibility", "accessibility_menu_paste", "menu_paste"}:
        return ("menu_paste_accessibility",)
    if backend in {"accessibility_set_focused_value", "accessibility_set_value"}:
        return ("accessibility_set_focused_value",)
    raise GuiAutomationError(f"Unsupported paste backend: {backend}")


def production_pm_paste_variants_for_target(target: ManualStageTarget) -> tuple[str, ...]:
    variants: list[str] = []
    for backend in pm_paste_backends_for_target(target):
        for variant in paste_variants_for_backend(backend, allow_keydown_variants=False):
            if (
                variant in RAW_V_PRONE_PM_PASTE_VARIANTS
                or variant in DIAGNOSTIC_ONLY_PM_PASTE_VARIANTS
            ):
                continue
            variants.append(variant)
    return tuple(dict.fromkeys(variants))


RAW_KEY_LEAK_TEXTS = frozenset({"v", "V", "ㅍ"})
PM_PROMPT_SENTINEL_PREFIX = "AGENT_BRIDGE_PM_PROMPT_SENTINEL:"


def extract_pm_prompt_sentinel(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(PM_PROMPT_SENTINEL_PREFIX):
            return stripped
    return None


def normalize_paste_text_for_verification(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def raw_key_leak_suspected(copied_text: str, expected_text: str) -> bool:
    copied = normalize_paste_text_for_verification(copied_text)
    expected = normalize_paste_text_for_verification(expected_text)
    if len(expected) <= 1:
        return False
    if copied in RAW_KEY_LEAK_TEXTS:
        return True
    if copied and set(copied).issubset(RAW_KEY_LEAK_TEXTS):
        return True
    return len(copied) == 1 and copied != expected


def paste_text_matches_expected(copied_text: str, expected_text: str) -> bool:
    copied = normalize_paste_text_for_verification(copied_text)
    expected = normalize_paste_text_for_verification(expected_text)
    return bool(expected) and copied == expected


def _applescript_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _applescript_list(values: tuple[str, ...]) -> str:
    return "{" + ", ".join(_applescript_string(value) for value in values) + "}"


def _system_events_process_selector(target: ManualStageTarget) -> str:
    if target.bundle_id:
        return (
            "first application process whose bundle identifier is "
            f"{_applescript_string(target.bundle_id)}"
        )
    return f"first application process whose name is {_applescript_string(target.app_name)}"


def _rect_center(rect: tuple[int, int, int, int]) -> tuple[int, int]:
    return (int(rect[0] + rect[2] / 2), int(rect[1] + rect[3] / 2))


def _point_inside_bounds(
    point: tuple[int, int] | None,
    bounds: tuple[int, int, int, int] | None,
) -> bool:
    if point is None or bounds is None:
        return False
    px, py = point
    x, y, width, height = bounds
    return x <= px <= x + width and y <= py <= y + height
