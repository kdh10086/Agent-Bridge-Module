from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

from agent_bridge.core.event_log import EventLog
from agent_bridge.gui.asset_state_machine import VisualGuiState
from agent_bridge.gui.clipboard import Clipboard
from agent_bridge.gui.gui_automation import (
    MacOSSystemEventsGuiAdapter,
    paste_text_matches_expected,
    raw_key_leak_suspected,
)
from agent_bridge.gui.macos_apps import AppActivator, ManualStageTarget
from agent_bridge.gui.visual_pm_controller import VisualPMController


PASTE_DIAGNOSTIC_MARKER = "AGENT_BRIDGE_PASTE_TEST"
PASTE_BACKEND_DIAGNOSTIC_VARIANTS: tuple[tuple[str, str], ...] = (
    ("accessibility_set_focused_value", "accessibility_set_focused_value"),
    ("menu_paste_accessibility", "menu_paste_accessibility"),
    ("system_events_command_v", "system_events_command_v"),
    ("system_events_key_code_v_command", "system_events_key_code_v_command"),
    ("pyautogui_hotkey_cmd_v", "cmd_v_hotkey"),
    ("pyautogui_hotkey_command_v", "command_v_hotkey"),
    ("pyautogui_keydown_command_v", "command_v_keydown"),
    ("pyautogui_keydown_cmd_v", "cmd_v_keydown"),
)
PRODUCTION_RECOMMENDABLE_BACKENDS = frozenset(
    {
        "menu_paste_accessibility",
        "accessibility_set_focused_value",
        "system_events_key_code_v_command",
    }
)


@dataclass(frozen=True)
class PasteBackendDiagnosticAttempt:
    backend_name: str
    paste_variant: str
    action_returned: bool
    active_app_before: str | None
    active_app_after: str | None
    clipboard_before_hash: str | None
    clipboard_after_set_hash: str | None
    marker_reflected_in_composer: bool | None
    state_after_paste: str | None
    raw_v_or_korean_jamo_suspected: bool
    cleanup_success: bool | None
    recommended_for_production: bool
    error: str | None = None


@dataclass(frozen=True)
class PasteBackendDiagnosticResult:
    target: ManualStageTarget
    profile: str
    marker: str
    shared_controller_used: bool
    window_bounds: tuple[int, int, int, int] | None
    initial_state: str | None
    composer_click_point: tuple[int, int] | None
    attempts: tuple[PasteBackendDiagnosticAttempt, ...]
    stable_backends: tuple[str, ...]
    no_submit: bool = True
    error: str | None = None


def diagnose_paste_backends(
    *,
    target: ManualStageTarget,
    clipboard: Clipboard,
    app_activator: AppActivator | None = None,
    event_log: EventLog | None = None,
    logs_dir: Path | None = None,
    marker: str = PASTE_DIAGNOSTIC_MARKER,
) -> PasteBackendDiagnosticResult:
    controller = VisualPMController.for_target(target)
    target = controller.target
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=clipboard,
        app_activator=app_activator,
        event_log=event_log,
        debug_logs_dir=logs_dir,
        sleep_fn=lambda seconds: None if seconds <= 0 else time.sleep(seconds),
    )
    adapter.active_target = target
    try:
        adapter.activate_app(target)
        detection = adapter._detect_asset_state(target)
    except Exception as error:
        return PasteBackendDiagnosticResult(
            target=target,
            profile=controller.profile.name,
            marker=marker,
            shared_controller_used=True,
            window_bounds=None,
            initial_state=None,
            composer_click_point=None,
            attempts=(),
            stable_backends=(),
            error=str(error),
        )
    if not detection.plus_anchor_found or not detection.composer_click_point_safe:
        return PasteBackendDiagnosticResult(
            target=target,
            profile=controller.profile.name,
            marker=marker,
            shared_controller_used=True,
            window_bounds=detection.window_bounds,
            initial_state=detection.matched_state.value,
            composer_click_point=detection.computed_composer_click_point,
            attempts=(),
            stable_backends=(),
            error="composer_click_point_unavailable",
        )

    preexisting_text = ""
    if detection.matched_state == VisualGuiState.COMPOSER_HAS_TEXT:
        try:
            preexisting_text = adapter._copy_pm_composer_text_for_verification(target, marker)
        except Exception:
            preexisting_text = ""
        normalized_preexisting = preexisting_text.strip()
        if normalized_preexisting and normalized_preexisting not in {"v", "V", "ㅍ", marker}:
            return PasteBackendDiagnosticResult(
                target=target,
                profile=controller.profile.name,
                marker=marker,
                shared_controller_used=True,
                window_bounds=detection.window_bounds,
                initial_state=detection.matched_state.value,
                composer_click_point=detection.computed_composer_click_point,
                attempts=(),
                stable_backends=(),
                error="preexisting_composer_text_not_overwritten",
            )
        adapter._cleanup_pm_composer_text(target)

    attempts: list[PasteBackendDiagnosticAttempt] = []
    stable: list[str] = []
    original_clipboard = clipboard.read_text()
    for backend_name, variant_name in PASTE_BACKEND_DIAGNOSTIC_VARIANTS:
        attempt = _run_backend_attempt(
            adapter=adapter,
            target=target,
            clipboard=clipboard,
            marker=marker,
            backend_name=backend_name,
            variant_name=variant_name,
        )
        attempts.append(attempt)
        if attempt.recommended_for_production:
            stable.append(backend_name)
    clipboard.copy_text(original_clipboard)
    if event_log:
        event_log.append(
            "pm_paste_backend_diagnostic_run",
            pm_target=controller.profile.name,
            stable_backends=stable,
            attempt_count=len(attempts),
        )
    return PasteBackendDiagnosticResult(
        target=target,
        profile=controller.profile.name,
        marker=marker,
        shared_controller_used=True,
        window_bounds=detection.window_bounds,
        initial_state=detection.matched_state.value,
        composer_click_point=detection.computed_composer_click_point,
        attempts=tuple(attempts),
        stable_backends=tuple(stable),
    )


def _run_backend_attempt(
    *,
    adapter: MacOSSystemEventsGuiAdapter,
    target: ManualStageTarget,
    clipboard: Clipboard,
    marker: str,
    backend_name: str,
    variant_name: str,
) -> PasteBackendDiagnosticAttempt:
    active_before = _frontmost_app(adapter)
    before_text = clipboard.read_text()
    before_hash = _hash_text(before_text)
    clipboard.copy_text(marker)
    after_set_hash = _hash_text(clipboard.read_text())
    action_returned = False
    marker_reflected: bool | None = None
    raw_leak = False
    cleanup_success: bool | None = None
    state_after: str | None = None
    error: str | None = None
    try:
        adapter._click_pm_asset_composer_for_paste(
            target,
            attempt_index=1,
            max_attempts=1,
            prompt_length=len(marker),
            prompt_hash=_hash_text(marker),
        )
        adapter._paste_local_agent_variant(target, variant_name)
        action_returned = True
        adapter.sleep_fn(0.2)
        state_detection = adapter._detect_asset_state(target)
        state_after = state_detection.matched_state.value
        if state_detection.matched_state == VisualGuiState.COMPOSER_HAS_TEXT:
            copied = adapter._copy_pm_composer_text_for_verification(target, marker)
            marker_reflected = paste_text_matches_expected(copied, marker)
            raw_leak = raw_key_leak_suspected(copied, marker)
        else:
            try:
                copied = adapter._copy_pm_composer_text_for_verification(target, marker)
            except Exception:
                copied = ""
            marker_reflected = paste_text_matches_expected(copied, marker)
            raw_leak = raw_key_leak_suspected(copied, marker)
        if marker_reflected or raw_leak or state_after == VisualGuiState.COMPOSER_HAS_TEXT.value:
            cleanup_success = adapter._cleanup_pm_composer_text(target)
        else:
            cleanup_success = True
    except Exception as exc:
        error = str(exc)
        cleanup_success = adapter._cleanup_pm_composer_text(target)
    active_after = _frontmost_app(adapter)
    recommended = (
        action_returned
        and marker_reflected is True
        and not raw_leak
        and backend_name in PRODUCTION_RECOMMENDABLE_BACKENDS
    )
    return PasteBackendDiagnosticAttempt(
        backend_name=backend_name,
        paste_variant=variant_name,
        action_returned=action_returned,
        active_app_before=active_before,
        active_app_after=active_after,
        clipboard_before_hash=before_hash,
        clipboard_after_set_hash=after_set_hash,
        marker_reflected_in_composer=marker_reflected,
        state_after_paste=state_after,
        raw_v_or_korean_jamo_suspected=raw_leak,
        cleanup_success=cleanup_success,
        recommended_for_production=recommended,
        error=error,
    )


def format_paste_backend_diagnostic(result: PasteBackendDiagnosticResult) -> str:
    lines = [
        "# PM Paste Backend Diagnostic",
        "",
        f"Profile: {result.profile}",
        f"App: {result.target.app_name}",
        f"Bundle id: {result.target.bundle_id or 'unavailable'}",
        f"Shared controller used: {_yes_no(result.shared_controller_used)}",
        f"Marker: {result.marker}",
        f"Window bounds: {result.window_bounds or 'unavailable'}",
        f"Initial state: {result.initial_state or 'unavailable'}",
        f"Composer click point: {result.composer_click_point or 'unavailable'}",
        "No submit attempted: yes",
        f"Stable production backends: {', '.join(result.stable_backends) or 'none'}",
        f"Error: {result.error or 'none'}",
        "",
        "## Backend Matrix",
    ]
    if not result.attempts:
        lines.append("- not run")
    for attempt in result.attempts:
        lines.append(
            "- "
            f"{attempt.backend_name}: "
            f"action_returned={_yes_no(attempt.action_returned)} "
            f"state_after={attempt.state_after_paste or 'unavailable'} "
            f"marker_reflected={_yes_no_unknown(attempt.marker_reflected_in_composer)} "
            f"raw_v_or_korean_jamo_suspected={_yes_no(attempt.raw_v_or_korean_jamo_suspected)} "
            f"cleanup_success={_yes_no_unknown(attempt.cleanup_success)} "
            f"recommended_for_production={_yes_no(attempt.recommended_for_production)} "
            f"error={attempt.error or 'none'}"
        )
    return "\n".join(lines)


def _frontmost_app(adapter: MacOSSystemEventsGuiAdapter) -> str | None:
    detector = adapter.codex_ui_detector
    if detector is None:
        return None
    try:
        return detector.frontmost_app()
    except Exception:
        return None


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _yes_no_unknown(value: bool | None) -> str:
    if value is None:
        return "unknown"
    return _yes_no(value)
