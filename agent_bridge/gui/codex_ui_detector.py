from __future__ import annotations

import importlib.util
import json
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from agent_bridge.gui.clipboard import Clipboard
from agent_bridge.gui.macos_apps import ManualStageTarget
from agent_bridge.gui.macos_permissions import accessibility_denied_remediation_message
from agent_bridge.gui.visual_detector import (
    CodexVisualDetector,
    VisualDetectionResult,
    VisualMarkerPresenceResult,
    VisualRect,
    composer_text_search_region,
    plus_search_region,
    safe_search_region,
)


@dataclass(frozen=True)
class LocalAgentPreSubmitCheck:
    active_app: str | None = None
    target_app: str | None = None
    app_frontmost: bool | None = None
    prompt_length: int = 0
    clipboard_length: int = 0
    focused_element_summary: str = "unknown"
    focused_text_length: int | None = None
    input_candidate_count: int | None = None
    selected_input_candidate_summary: str = "unknown"
    input_text_length_before_paste: int | None = None
    input_text_length_after_paste: int | None = None
    prompt_text_present: bool | None = None


@dataclass(frozen=True)
class LocalAgentPostSubmitCheck:
    active_app_before: str | None = None
    active_app_after: str | None = None
    focused_element_summary_after: str = "unknown"
    focused_text_length_before: int | None = None
    focused_text_length_after: int | None = None
    input_cleared: bool | None = None
    new_user_message_detected: bool | None = None
    running_state_detected: bool | None = None
    confirmed: bool | None = None
    confirmation_reason: str = "not_detectable"


@dataclass(frozen=True)
class CodexUIDiagnostic:
    target_app: str
    active_app: str | None
    codex_app_active: bool
    focused_element_summary: str
    input_field_detectable: bool
    focused_text_length: int | None
    conversation_elements_detectable: bool
    running_state_detected: bool | None
    accessibility_available: bool
    input_candidate_count: int | None = None
    selected_input_candidate_summary: str = "unknown"
    limitation: str | None = None


@dataclass(frozen=True)
class LocalAgentFocusResult:
    active_app_before: str | None = None
    active_app_after: str | None = None
    app_frontmost: bool = False
    input_candidate_count: int = 0
    selected_input_candidate_summary: str = "unknown"
    input_text_length_before_paste: int | None = None
    focused_element_summary: str = "unknown"
    window_bounds: tuple[int, int, int, int] | None = None
    fallback_click_point: tuple[int, int] | None = None
    click_backend: str = "system_events"
    pyautogui_available: bool | None = None
    plus_button_bbox: tuple[int, int, int, int] | None = None
    plus_button_center: tuple[int, int] | None = None
    direct_plus_anchor_x_offset: int | None = None
    direct_plus_anchor_y_offset: int | None = None
    succeeded: bool = False
    used_fallback: bool = False
    error: str | None = None


@dataclass(frozen=True)
class CodexUIElement:
    depth: int
    role: str
    subrole: str = ""
    title: str = ""
    description: str = ""
    value: str = ""

    @property
    def summary(self) -> str:
        parts = [self.role]
        if self.subrole:
            parts.append(f"subrole={self.subrole}")
        for label, value in [
            ("title", self.title),
            ("description", self.description),
            ("value", self.value),
        ]:
            if value:
                parts.append(f"{label}={value[:80]}")
        return " ".join(parts)


@dataclass(frozen=True)
class CodexUITreeDump:
    active_app: str | None
    target_app: str
    elements: tuple[CodexUIElement, ...]
    raw_text: str
    accessibility_available: bool
    error: str | None = None


@dataclass(frozen=True)
class CodexWindowInfo:
    index: int
    title: str
    position: tuple[int, int] | None
    size: tuple[int, int] | None
    bounds: tuple[int, int, int, int] | None
    area: int
    visible: bool | None
    minimized: bool | None
    fullscreen: bool | None
    role: str | None
    subrole: str | None
    rejected: bool = False
    rejection_reasons: tuple[str, ...] = ()
    selected: bool = False


@dataclass(frozen=True)
class CodexWindowSelectionResult:
    target_app: str
    strategy: str
    min_width: int
    min_height: int
    min_area: int
    windows: tuple[CodexWindowInfo, ...]
    selected_window: CodexWindowInfo | None
    selected_bounds: tuple[int, int, int, int] | None
    plausible: bool
    error: str | None = None


@dataclass(frozen=True)
class CodexInputTargetDiagnostic:
    target_app: str
    active_app: str | None
    codex_app_active: bool
    window_bounds: tuple[int, int, int, int] | None
    input_candidate_count: int
    best_candidate_summary: str
    fallback_strategy: str | None
    fallback_enabled: bool
    fallback_click_point: tuple[int, int] | None
    prompt_presence_verifiable: bool
    live_submit_allowed: bool
    accessibility_available: bool
    detected_window_count: int = 0
    window_selection_strategy: str = "largest_visible_normal"
    selected_window_title: str | None = None
    rejected_window_summaries: tuple[str, ...] = ()
    window_selection_error: str | None = None
    placeholder_found: bool = False
    placeholder_bbox: tuple[int, int, int, int] | None = None
    plus_button_found: bool = False
    plus_button_bbox: tuple[int, int, int, int] | None = None
    plus_anchor_click_point: tuple[int, int] | None = None
    idle_empty_wait_timeout_seconds: int = 600
    idle_empty_poll_interval_seconds: int = 10
    dedicated_automation_session: bool = True
    allow_overwrite_after_idle_timeout: bool = True
    stop_on_idle_timeout: bool = False
    effective_timeout_policy: str = "overwrite"
    overwrite_allowed: bool = True
    composer_policy_mode: str = "dedicated_automation_session"
    busy_placeholder_wait_timeout_seconds: int = 600
    busy_placeholder_poll_interval_seconds: int = 10
    on_busy_timeout: str = "overwrite"
    visual_detection_backend_available: bool = False
    visual_screenshot_captured: bool = False
    visual_plus_button_found: bool = False
    visual_plus_button_bbox: tuple[int, int, int, int] | None = None
    visual_plus_button_confidence: float | None = None
    visual_plus_template_path: str | None = None
    visual_plus_template_size: tuple[int, int] | None = None
    visual_plus_best_match_bbox: tuple[int, int, int, int] | None = None
    visual_plus_best_match_confidence: float | None = None
    visual_plus_confidence_threshold: float | None = None
    visual_plus_multiscale_enabled: bool | None = None
    visual_plus_search_region_bounds: tuple[int, int, int, int] | None = None
    visual_plus_match_error: str | None = None
    visual_placeholder_found: bool = False
    visual_placeholder_bbox: tuple[int, int, int, int] | None = None
    visual_placeholder_target_text: str | None = None
    visual_placeholder_match_text: str | None = None
    visual_placeholder_ocr_text_path: str | None = None
    visual_placeholder_ocr_confidence: float | None = None
    visual_placeholder_search_region_bounds: tuple[int, int, int, int] | None = None
    visual_placeholder_detection_reason: str | None = None
    visual_selected_strategy: str = "none"
    visual_click_point: tuple[int, int] | None = None
    visual_safe_region_bounds: tuple[int, int, int, int] | None = None
    focus_strategy: str | None = None
    direct_plus_anchor_enabled: bool = False
    direct_plus_anchor_click_point: tuple[int, int] | None = None
    direct_plus_anchor_click_point_safe: bool = False
    direct_plus_anchor_x_offset: int = 0
    direct_plus_anchor_y_offset: int = 24
    direct_plus_anchor_y_offset_candidates: tuple[int, ...] = ()
    visual_placeholder_detection_backend_available: bool = False
    visual_placeholder_detection_error: str | None = None
    visual_ocr_backend: str = "pytesseract"
    visual_pytesseract_package_available: bool | None = None
    visual_tesseract_executable_available: bool | None = None
    visual_ocr_languages: tuple[str, ...] = ()
    visual_english_ocr_available: bool | None = None
    visual_korean_ocr_available: bool | None = None
    visual_click_point_safe: bool = False
    visual_fallback_would_be_used: bool = False
    visual_debug_image_path: str | None = None
    visual_annotated_image_path: str | None = None
    visual_error: str | None = None
    limitation: str | None = None


@dataclass(frozen=True)
class CodexVisualComposerStateResult:
    target_app: str
    codex_frontmost: bool
    codex_window_bounds: tuple[int, int, int, int] | None
    bounded_screenshot_captured: bool
    placeholder_detection_backend_available: bool
    placeholder_visible: bool | None
    placeholder_error: str | None
    plus_anchor_found: bool
    plus_anchor_click_point: tuple[int, int] | None
    plus_anchor_confidence: float | None
    poll_count: int
    elapsed_wait_seconds: float
    busy_timeout_action: str | None
    selected_strategy: str
    should_proceed: bool
    should_overwrite: bool
    should_abort: bool
    error: str | None = None


@dataclass(frozen=True)
class CodexPasteVariantResult:
    variant_name: str
    attempted: bool = False
    paste_error: str | None = None
    marker_found: bool | None = None
    marker_confidence: float | None = None
    literal_v_detected: bool | None = None
    cleanup_attempted: bool = False
    cleanup_success: bool | None = None


@dataclass(frozen=True)
class _PasteBackendRunResult:
    marker_visual: VisualMarkerPresenceResult | None
    paste_attempted: bool
    paste_succeeded: bool
    paste_error: str | None = None
    paste_variant_attempts: tuple[CodexPasteVariantResult, ...] = ()
    paste_variant_attempted: str | None = None
    paste_variant_succeeded: bool | None = None
    literal_v_detected: bool | None = None
    final_paste_strategy: str | None = None
    cleanup_attempted: bool = False
    cleanup_success: bool | None = None


@dataclass(frozen=True)
class CodexFocusTargetCandidate:
    name: str
    family: str
    click_point: tuple[int, int]
    safe: bool
    source: str
    rejection_reason: str | None = None


@dataclass(frozen=True)
class CodexFocusTargetAttemptResult:
    candidate_name: str
    candidate_family: str
    click_point: tuple[int, int]
    click_point_safe: bool
    click_attempted: bool = False
    click_succeeded: bool = False
    typed_marker_attempted: bool = False
    typed_marker_succeeded: bool = False
    marker_found: bool | None = None
    marker_confidence: float | None = None
    marker_match_text: str | None = None
    marker_ocr_text: str = ""
    marker_ocr_text_path: str | None = None
    cleanup_attempted: bool = False
    cleanup_success: bool | None = None
    cleanup_marker_found: bool | None = None
    error: str | None = None


@dataclass(frozen=True)
class CodexFocusTargetComparisonResult:
    target_app: str
    marker_text: str
    click_backend: str
    codex_frontmost: bool
    window_bounds: tuple[int, int, int, int] | None
    visual_backend_available: bool
    visual_screenshot_captured: bool
    placeholder_bbox: tuple[int, int, int, int] | None
    plus_button_bbox: tuple[int, int, int, int] | None
    safe_region_bounds: tuple[int, int, int, int] | None
    candidates: tuple[CodexFocusTargetCandidate, ...]
    attempts: tuple[CodexFocusTargetAttemptResult, ...]
    selected_candidate_name: str | None = None
    selected_click_point: tuple[int, int] | None = None
    comparison_image_path: str | None = None
    comparison_annotated_image_path: str | None = None
    comparison_ocr_text_path: str | None = None
    comparison_json_path: str | None = None
    manual_cleanup_required: bool = False
    stopped_reason: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class CodexPasteTestResult:
    target_app: str
    marker_text: str
    visual_detection_backend_available: bool
    visual_screenshot_captured: bool
    visual_plus_button_found: bool
    visual_plus_button_bbox: tuple[int, int, int, int] | None
    visual_plus_button_confidence: float | None
    visual_selected_strategy: str
    visual_click_point: tuple[int, int] | None
    visual_click_point_safe: bool
    click_backend: str = "system_events"
    pyautogui_available: bool | None = None
    codex_frontmost_before_click: bool | None = None
    codex_frontmost_after_click: bool | None = None
    click_attempted: bool = False
    click_succeeded: bool = False
    paste_backend: str = "system_events"
    paste_attempted: bool = False
    paste_succeeded: bool = False
    paste_variant_attempted: str | None = None
    paste_variant_succeeded: bool | None = None
    paste_variant_attempts: tuple[CodexPasteVariantResult, ...] = ()
    literal_v_detected: bool | None = None
    final_paste_strategy: str | None = None
    clipboard_length: int = 0
    marker_detected: bool | None = None
    marker_presence_detectable: bool = False
    focused_element_summary: str = "unknown"
    focused_text_length_after_paste: int | None = None
    cleanup_attempted: bool = False
    cleanup_success: bool | None = None
    manual_cleanup_required: bool = False
    marker_detection_backend: str = "unknown"
    marker_detection_available: bool = False
    visual_marker_found: bool | None = None
    marker_confidence: float | None = None
    marker_search_region_bounds: tuple[int, int, int, int] | None = None
    marker_screenshot_path: str | None = None
    marker_annotated_screenshot_path: str | None = None
    marker_ocr_text_path: str | None = None
    marker_match_text: str | None = None
    marker_detection_reason: str | None = None
    marker_pytesseract_package_available: bool | None = None
    marker_tesseract_executable_available: bool | None = None
    marker_ocr_languages: tuple[str, ...] = ()
    marker_english_ocr_available: bool | None = None
    marker_korean_ocr_available: bool | None = None
    marker_detection_error: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class _CodexUISnapshot:
    active_app: str | None
    focused_element_summary: str
    focused_text: str | None
    ui_text: str
    button_text: str
    accessibility_available: bool
    error: str | None = None

    @property
    def focused_text_length(self) -> int | None:
        return len(self.focused_text) if self.focused_text is not None else None

    @property
    def all_text(self) -> str:
        return "\n".join(part for part in [self.focused_text or "", self.ui_text, self.button_text] if part)


Runner = Callable[..., subprocess.CompletedProcess[str]]


CODEX_COMPOSER_IDLE_EMPTY = "CODEX_COMPOSER_IDLE_EMPTY"
CODEX_COMPOSER_BUSY_OR_NONEMPTY = "CODEX_COMPOSER_BUSY_OR_NONEMPTY"
CODEX_COMPOSER_IDLE_WAIT_TIMEOUT = "CODEX_COMPOSER_IDLE_WAIT_TIMEOUT"
CODEX_PASTE_TEST_MARKER = "AGENT_BRIDGE_CODEX_PASTE_TEST_DO_NOT_SUBMIT"


@dataclass(frozen=True)
class CodexComposerSnapshot:
    active_app: str | None
    placeholder_found: bool
    placeholder_bbox: tuple[int, int, int, int] | None
    plus_button_found: bool
    plus_button_bbox: tuple[int, int, int, int] | None
    state: str
    error: str | None = None


@dataclass(frozen=True)
class CodexComposerIdleWaitResult:
    state: str
    placeholder_found: bool
    placeholder_bbox: tuple[int, int, int, int] | None
    plus_button_found: bool
    plus_button_bbox: tuple[int, int, int, int] | None
    plus_anchor_click_point: tuple[int, int] | None
    timed_out: bool
    timeout_policy: str
    overwrite_allowed: bool
    should_overwrite: bool
    should_stop: bool
    polls: int
    last_observed_state: str
    message: str | None = None


@dataclass
class CodexUIDetector:
    osascript_executable: str = "osascript"
    runner: Runner = subprocess.run
    sleep_fn: Callable[[float], None] = time.sleep
    monotonic_fn: Callable[[], float] = time.monotonic
    post_submit_delay_seconds: float = 1.0
    visual_detector: CodexVisualDetector | None = None
    pyautogui_clicker: Callable[[int, int], None] | None = None
    pyautogui_hotkeyer: Callable[..., None] | None = None
    pyautogui_key_downer: Callable[[str], None] | None = None
    pyautogui_key_upper: Callable[[str], None] | None = None
    pyautogui_presser: Callable[[str], None] | None = None
    pyautogui_writer: Callable[..., None] | None = None

    def __post_init__(self) -> None:
        if self.visual_detector is None:
            self.visual_detector = CodexVisualDetector()

    def wait_until_frontmost(
        self,
        target: ManualStageTarget,
        *,
        timeout_seconds: float = 5.0,
        polling_interval_seconds: float = 0.2,
    ) -> bool:
        deadline = self.monotonic_fn() + timeout_seconds
        while True:
            if self.frontmost_app() == target.app_name:
                return True
            if self.monotonic_fn() >= deadline:
                return False
            self.sleep_fn(polling_interval_seconds)

    def frontmost_app(self) -> str | None:
        script = 'tell application "System Events" to name of first application process whose frontmost is true'
        completed = self.runner(
            [self.osascript_executable, "-e", script],
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            return None
        return completed.stdout.strip() or None

    def focus_input(self, target: ManualStageTarget) -> LocalAgentFocusResult:
        result = self._inspect_input_candidate(target, focus=True)
        if result.succeeded or target.input_focus_strategy != "window_relative_click":
            return result
        fallback = self.window_relative_click_preview(target)
        if fallback.fallback_click_point is None:
            return result
        return LocalAgentFocusResult(
            active_app_before=fallback.active_app_before,
            active_app_after=fallback.active_app_after,
            app_frontmost=fallback.app_frontmost,
            input_candidate_count=result.input_candidate_count,
            selected_input_candidate_summary=result.selected_input_candidate_summary,
            input_text_length_before_paste=result.input_text_length_before_paste,
            focused_element_summary=result.focused_element_summary,
            window_bounds=fallback.window_bounds,
            fallback_click_point=fallback.fallback_click_point,
            succeeded=False,
            used_fallback=True,
            error="window_relative_click fallback is configured but real paste requires explicit click-test or future guarded handoff support.",
        )

    def inspect_composer_state(self, target: ManualStageTarget) -> CodexComposerSnapshot:
        placeholder = (target.composer_placeholder_text or "").replace("\\", "\\\\").replace('"', '\\"')
        app_name = target.app_name.replace("\\", "\\\\").replace('"', '\\"')
        script = f"""
tell application "System Events"
  set frontApp to "unknown"
  try
    set frontApp to name of first application process whose frontmost is true
  end try
  set placeholderLine to ""
  set plusLine to ""
  try
    tell application process "{app_name}"
      set elementItems to entire contents of front window
      repeat with elementItem in elementItems
        set roleValue to ""
        set titleValue to ""
        set descriptionValue to ""
        set valueValue to ""
        try
          set roleValue to value of attribute "AXRole" of elementItem as text
        end try
        try
          set titleValue to value of attribute "AXTitle" of elementItem as text
        end try
        try
          set descriptionValue to value of attribute "AXDescription" of elementItem as text
        end try
        try
          set valueValue to value of attribute "AXValue" of elementItem as text
        end try
        set combinedValue to titleValue & " " & descriptionValue & " " & valueValue
        set bboxValue to ""
        try
          set posValue to position of elementItem
          set sizeValue to size of elementItem
          set bboxValue to ((item 1 of posValue) as text) & tab & ((item 2 of posValue) as text) & tab & ((item 1 of sizeValue) as text) & tab & ((item 2 of sizeValue) as text)
        end try
        if placeholderLine is "" and "{placeholder}" is not "" and combinedValue contains "{placeholder}" then
          set placeholderLine to "PLACEHOLDER" & tab & bboxValue
        end if
        if plusLine is "" and roleValue is "AXButton" then
          ignoring case
            if titleValue is "+" or descriptionValue is "+" or valueValue is "+" or combinedValue contains "plus" or combinedValue contains "add" or combinedValue contains "첨부" or combinedValue contains "추가" then
              set plusLine to "PLUS" & tab & bboxValue
            end if
          end ignoring
        end if
      end repeat
      return frontApp & linefeed & placeholderLine & linefeed & plusLine
    end tell
  on error errorMessage
    return frontApp & linefeed & "ERROR" & tab & errorMessage & linefeed & plusLine
  end try
end tell
""".strip()
        completed = self.runner(
            [self.osascript_executable, "-e", script],
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            output = (completed.stderr or completed.stdout or "").strip()
            return CodexComposerSnapshot(
                active_app=None,
                placeholder_found=False,
                placeholder_bbox=None,
                plus_button_found=False,
                plus_button_bbox=None,
                state=CODEX_COMPOSER_BUSY_OR_NONEMPTY,
                error=output or "osascript failed",
            )
        return _parse_composer_snapshot(completed.stdout)

    def wait_for_composer_idle_empty(
        self,
        target: ManualStageTarget,
        *,
        event_callback: Callable[[str, dict[str, object]], None] | None = None,
    ) -> CodexComposerIdleWaitResult:
        timeout = max(0, int(target.idle_empty_wait_timeout_seconds))
        poll_interval = max(1, int(target.idle_empty_poll_interval_seconds))
        timeout_policy = _timeout_policy(target)
        overwrite_allowed = timeout_policy == "overwrite"
        _emit(event_callback, "local_agent_idle_empty_wait_started", timeout_seconds=timeout)
        if not target.composer_placeholder_text:
            return CodexComposerIdleWaitResult(
                state=CODEX_COMPOSER_IDLE_EMPTY,
                placeholder_found=False,
                placeholder_bbox=None,
                plus_button_found=False,
                plus_button_bbox=None,
                plus_anchor_click_point=None,
                timed_out=False,
                timeout_policy=timeout_policy,
                overwrite_allowed=overwrite_allowed,
                should_overwrite=False,
                should_stop=False,
                polls=0,
                last_observed_state=CODEX_COMPOSER_IDLE_EMPTY,
                message="Codex composer placeholder text is not configured; idle wait skipped.",
            )
        deadline = self.monotonic_fn() + timeout
        polls = 0
        while True:
            snapshot = self.inspect_composer_state(target)
            if snapshot.placeholder_found:
                _emit(
                    event_callback,
                    "local_agent_placeholder_detected",
                    placeholder_bbox=snapshot.placeholder_bbox,
                )
                return _idle_wait_result(
                    target=target,
                    snapshot=snapshot,
                    state=CODEX_COMPOSER_IDLE_EMPTY,
                    timed_out=False,
                    timeout_policy=timeout_policy,
                    overwrite_allowed=overwrite_allowed,
                    should_overwrite=False,
                    should_stop=False,
                    polls=polls,
                )
            _emit(
                event_callback,
                "local_agent_placeholder_absent",
                active_app=snapshot.active_app,
                error=snapshot.error,
            )
            if self.monotonic_fn() >= deadline:
                _emit(
                    event_callback,
                    "local_agent_idle_empty_wait_timeout",
                    timeout_seconds=timeout,
                    last_observed_state=CODEX_COMPOSER_BUSY_OR_NONEMPTY,
                )
                should_overwrite = overwrite_allowed
                should_stop = not should_overwrite
                _emit(
                    event_callback,
                    "local_agent_timeout_policy_overwrite"
                    if should_overwrite
                    else "local_agent_timeout_policy_stop",
                    timeout_seconds=timeout,
                )
                return _idle_wait_result(
                    target=target,
                    snapshot=snapshot,
                    state=CODEX_COMPOSER_IDLE_WAIT_TIMEOUT,
                    timed_out=True,
                    timeout_policy=timeout_policy,
                    overwrite_allowed=overwrite_allowed,
                    should_overwrite=should_overwrite,
                    should_stop=should_stop,
                    polls=polls,
                    message=(
                        None
                        if should_overwrite
                        else (
                            "Codex composer did not become idle-empty within "
                            f"{timeout} seconds and stop_on_idle_timeout is enabled."
                        )
                    ),
                )
            polls += 1
            _emit(
                event_callback,
                "local_agent_pending_text_wait_poll",
                poll=polls,
                poll_interval_seconds=poll_interval,
            )
            self.sleep_fn(poll_interval)

    def plus_anchor_click_preview(self, target: ManualStageTarget) -> LocalAgentFocusResult:
        snapshot = self.inspect_composer_state(target)
        bounds = self.window_bounds(target)
        point = _plus_anchor_click_point(snapshot.plus_button_bbox, target, bounds)
        active = snapshot.active_app or self.frontmost_app()
        if point is None:
            return LocalAgentFocusResult(
                active_app_after=active,
                app_frontmost=active == target.app_name,
                window_bounds=bounds,
                used_fallback=True,
                error="Plus-button anchor was not detected or produced an unsafe click point.",
            )
        return LocalAgentFocusResult(
            active_app_after=active,
            app_frontmost=active == target.app_name,
            window_bounds=bounds,
            fallback_click_point=point,
            used_fallback=True,
            selected_input_candidate_summary="plus-button anchor",
        )

    def click_plus_anchor(
        self,
        target: ManualStageTarget,
        *,
        click_backend: str | None = None,
    ) -> LocalAgentFocusResult:
        backend = _resolve_click_backend(target, click_backend)
        preview = self.plus_anchor_click_preview(target)
        if preview.fallback_click_point is None:
            visual_preview = self.visual_click_preview(target, require_plus=True)
            if visual_preview.fallback_click_point is not None:
                preview = visual_preview
            else:
                return _with_click_backend(preview, backend, self._pyautogui_available())
        x, y = preview.fallback_click_point
        click_error = self._click_point((x, y), backend=backend)
        if click_error:
            return LocalAgentFocusResult(
                active_app_after=preview.active_app_after,
                app_frontmost=preview.app_frontmost,
                window_bounds=preview.window_bounds,
                fallback_click_point=preview.fallback_click_point,
                selected_input_candidate_summary=preview.selected_input_candidate_summary,
                click_backend=backend,
                pyautogui_available=self._pyautogui_available(),
                used_fallback=True,
                error=click_error,
            )
        snapshot = self._snapshot(target)
        return LocalAgentFocusResult(
            active_app_after=snapshot.active_app,
            app_frontmost=snapshot.active_app == target.app_name,
            selected_input_candidate_summary="plus-button anchor",
            focused_element_summary=snapshot.focused_element_summary,
            window_bounds=preview.window_bounds,
            fallback_click_point=preview.fallback_click_point,
            click_backend=backend,
            pyautogui_available=self._pyautogui_available(),
            succeeded=True,
            used_fallback=True,
        )

    def click_placeholder_anchor(
        self,
        target: ManualStageTarget,
        placeholder_bbox: tuple[int, int, int, int],
        *,
        click_backend: str | None = None,
    ) -> LocalAgentFocusResult:
        backend = _resolve_click_backend(target, click_backend)
        x, y, width, height = placeholder_bbox
        point = (int(x + width / 2), int(y + height / 2))
        click_error = self._click_point(point, backend=backend)
        if click_error:
            return LocalAgentFocusResult(
                active_app_after=self.frontmost_app(),
                fallback_click_point=point,
                click_backend=backend,
                pyautogui_available=self._pyautogui_available(),
                used_fallback=True,
                error=click_error,
            )
        snapshot = self._snapshot(target)
        return LocalAgentFocusResult(
            active_app_after=snapshot.active_app,
            app_frontmost=snapshot.active_app == target.app_name,
            selected_input_candidate_summary="placeholder anchor",
            focused_element_summary=snapshot.focused_element_summary,
            fallback_click_point=point,
            click_backend=backend,
            pyautogui_available=self._pyautogui_available(),
            succeeded=True,
            used_fallback=True,
        )

    def dump_ui_tree(self, target: ManualStageTarget, *, max_depth: int = 8) -> CodexUITreeDump:
        app_name = target.app_name.replace("\\", "\\\\").replace('"', '\\"')
        _ = max_depth
        script = f"""
tell application "System Events"
  set frontApp to "unknown"
  try
    set frontApp to name of first application process whose frontmost is true
  end try
  try
    tell application process "{app_name}"
      set outputValue to frontApp & linefeed
      set windowRole to ""
      set windowTitle to ""
      try
        set windowRole to value of attribute "AXRole" of front window as text
      end try
      try
        set windowTitle to value of attribute "AXTitle" of front window as text
      end try
      set outputValue to outputValue & "0" & tab & windowRole & tab & "" & tab & windowTitle & tab & "" & tab & "" & linefeed
      set elementItems to entire contents of front window
      repeat with elementItem in elementItems
        set roleValue to ""
        set subroleValue to ""
        set titleValue to ""
        set descriptionValue to ""
        set valueValue to ""
        try
          set roleValue to value of attribute "AXRole" of elementItem as text
        end try
        try
          set subroleValue to value of attribute "AXSubrole" of elementItem as text
        end try
        try
          set titleValue to value of attribute "AXTitle" of elementItem as text
        end try
        try
          set descriptionValue to value of attribute "AXDescription" of elementItem as text
        end try
        try
          set valueValue to value of attribute "AXValue" of elementItem as text
        end try
        set outputValue to outputValue & "1" & tab & roleValue & tab & subroleValue & tab & titleValue & tab & descriptionValue & tab & valueValue & linefeed
      end repeat
      return outputValue
    end tell
  on error errorMessage
    return frontApp & linefeed & "ERROR" & tab & errorMessage
  end try
end tell
""".strip()
        completed = self.runner(
            [self.osascript_executable, "-e", script],
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            output = (completed.stderr or completed.stdout or "").strip()
            return CodexUITreeDump(
                active_app=None,
                target_app=target.app_name,
                elements=(),
                raw_text=output,
                accessibility_available=False,
                error=output or "osascript failed",
            )
        return _parse_ui_tree_output(completed.stdout, target_app=target.app_name)

    def write_ui_tree_dump(
        self,
        target: ManualStageTarget,
        *,
        logs_dir: Path,
        max_depth: int = 8,
    ) -> CodexUITreeDump:
        dump = self.dump_ui_tree(target, max_depth=max_depth)
        logs_dir.mkdir(parents=True, exist_ok=True)
        json_path = logs_dir / "codex_ui_tree.json"
        txt_path = logs_dir / "codex_ui_tree.txt"
        json_path.write_text(
            json.dumps(
                {
                    "active_app": dump.active_app,
                    "target_app": dump.target_app,
                    "accessibility_available": dump.accessibility_available,
                    "error": dump.error,
                    "elements": [element.__dict__ for element in dump.elements],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        txt_path.write_text(format_codex_ui_tree_dump(dump), encoding="utf-8")
        return dump

    def diagnose_input_target(
        self,
        target: ManualStageTarget,
        *,
        logs_dir: Path | None = None,
        visual_debug: bool = False,
    ) -> CodexInputTargetDiagnostic:
        active_app = self.frontmost_app()
        candidate = self._inspect_input_candidate(target, focus=False)
        window_selection = self.select_main_window(target)
        window_bounds = window_selection.selected_bounds
        fallback_preview = self.window_relative_click_preview(
            target,
            bounds=window_bounds,
            active_app=active_app,
        )
        composer_snapshot = self.inspect_composer_state(target)
        visual = self.visual_detection_result(
            target,
            window_bounds=window_bounds,
            logs_dir=logs_dir,
            visual_debug=visual_debug,
        )
        direct_plus_point = _direct_plus_anchor_click_point(
            visual.plus_button_bbox,
            target,
            window_bounds,
        )
        plus_anchor_point = _plus_anchor_click_point(
            composer_snapshot.plus_button_bbox,
            target,
            window_bounds,
        )
        candidate_found = candidate.input_candidate_count > 0
        fallback_enabled = target.input_focus_strategy == "window_relative_click"
        prompt_presence_verifiable = candidate_found
        live_submit_allowed = (
            prompt_presence_verifiable
            or (
                not target.require_prompt_presence_verification
                and target.allow_unverified_submit
                and fallback_enabled
            )
        )
        limitation = None
        if not candidate_found and not fallback_enabled:
            limitation = "No Accessibility input candidate was found and fallback is disabled."
        elif not candidate_found:
            limitation = "Fallback is configured, but prompt presence remains unverifiable."
        return CodexInputTargetDiagnostic(
            target_app=target.app_name,
            active_app=active_app,
            codex_app_active=active_app == target.app_name,
            window_bounds=window_bounds,
            detected_window_count=len(window_selection.windows),
            window_selection_strategy=window_selection.strategy,
            selected_window_title=(
                window_selection.selected_window.title
                if window_selection.selected_window is not None
                else None
            ),
            rejected_window_summaries=tuple(
                _window_rejection_summary(window)
                for window in window_selection.windows
                if window.rejected
            ),
            window_selection_error=window_selection.error,
            input_candidate_count=candidate.input_candidate_count,
            best_candidate_summary=candidate.selected_input_candidate_summary,
            fallback_strategy=target.input_focus_strategy,
            fallback_enabled=fallback_enabled,
            fallback_click_point=fallback_preview.fallback_click_point,
            prompt_presence_verifiable=prompt_presence_verifiable,
            live_submit_allowed=live_submit_allowed,
            accessibility_available=candidate.error is None,
            placeholder_found=composer_snapshot.placeholder_found,
            placeholder_bbox=composer_snapshot.placeholder_bbox,
            plus_button_found=composer_snapshot.plus_button_found,
            plus_button_bbox=composer_snapshot.plus_button_bbox,
            plus_anchor_click_point=plus_anchor_point,
            idle_empty_wait_timeout_seconds=target.idle_empty_wait_timeout_seconds,
            idle_empty_poll_interval_seconds=target.idle_empty_poll_interval_seconds,
            dedicated_automation_session=target.dedicated_automation_session,
            allow_overwrite_after_idle_timeout=target.allow_overwrite_after_idle_timeout,
            stop_on_idle_timeout=target.stop_on_idle_timeout,
            effective_timeout_policy=_timeout_policy(target),
            overwrite_allowed=_timeout_policy(target) == "overwrite",
            composer_policy_mode=target.composer_policy_mode,
            busy_placeholder_wait_timeout_seconds=target.busy_placeholder_wait_timeout_seconds,
            busy_placeholder_poll_interval_seconds=target.busy_placeholder_poll_interval_seconds,
            on_busy_timeout=target.on_busy_timeout,
            visual_detection_backend_available=visual.backend_available,
            visual_screenshot_captured=visual.screenshot_captured,
            visual_plus_button_found=visual.plus_button_found,
            visual_plus_button_bbox=visual.plus_button_bbox,
            visual_plus_button_confidence=visual.plus_button_confidence,
            visual_plus_template_path=visual.plus_template_path,
            visual_plus_template_size=visual.plus_template_size,
            visual_plus_best_match_bbox=visual.plus_best_match_bbox,
            visual_plus_best_match_confidence=visual.plus_best_match_confidence,
            visual_plus_confidence_threshold=visual.plus_confidence_threshold,
            visual_plus_multiscale_enabled=visual.plus_multiscale_enabled,
            visual_plus_search_region_bounds=visual.plus_search_region_bounds,
            visual_plus_match_error=visual.plus_match_error,
            visual_placeholder_found=visual.placeholder_found,
            visual_placeholder_bbox=visual.placeholder_bbox,
            visual_placeholder_target_text=visual.placeholder_target_text,
            visual_placeholder_match_text=visual.placeholder_match_text,
            visual_placeholder_ocr_text_path=visual.placeholder_ocr_text_path,
            visual_placeholder_ocr_confidence=visual.placeholder_ocr_confidence,
            visual_placeholder_search_region_bounds=visual.placeholder_search_region_bounds,
            visual_placeholder_detection_reason=visual.placeholder_detection_reason,
            visual_placeholder_detection_backend_available=(
                visual.placeholder_detection_backend_available
            ),
            visual_placeholder_detection_error=visual.placeholder_detection_error,
            visual_ocr_backend=visual.ocr_backend,
            visual_pytesseract_package_available=visual.pytesseract_package_available,
            visual_tesseract_executable_available=visual.tesseract_executable_available,
            visual_ocr_languages=visual.ocr_languages,
            visual_english_ocr_available=visual.english_ocr_available,
            visual_korean_ocr_available=visual.korean_ocr_available,
            visual_selected_strategy=visual.selected_strategy,
            visual_click_point=visual.computed_click_point,
            visual_safe_region_bounds=visual.safe_region_bounds,
            focus_strategy=target.focus_strategy,
            direct_plus_anchor_enabled=target.direct_plus_anchor_enabled,
            direct_plus_anchor_click_point=direct_plus_point,
            direct_plus_anchor_click_point_safe=direct_plus_point is not None,
            direct_plus_anchor_x_offset=target.direct_plus_anchor_x_offset,
            direct_plus_anchor_y_offset=target.direct_plus_anchor_y_offset,
            direct_plus_anchor_y_offset_candidates=(
                target.direct_plus_anchor_y_offset_candidates
            ),
            visual_click_point_safe=visual.click_point_safe,
            visual_fallback_would_be_used=visual.fallback_would_be_used,
            visual_debug_image_path=visual.debug_image_path,
            visual_annotated_image_path=visual.annotated_image_path,
            visual_error=visual.error,
            limitation=limitation,
        )

    def wait_for_visual_composer_ready(
        self,
        target: ManualStageTarget,
        *,
        logs_dir: Path | None = None,
        visual_debug: bool = False,
        event_callback: Callable[[str, dict[str, object]], None] | None = None,
    ) -> CodexVisualComposerStateResult:
        timeout = max(0, int(target.busy_placeholder_wait_timeout_seconds))
        poll_interval = max(1, int(target.busy_placeholder_poll_interval_seconds))
        deadline = self.monotonic_fn() + timeout
        started_at = self.monotonic_fn()
        polls = 0
        last_result: CodexVisualComposerStateResult | None = None
        _emit(
            event_callback,
            "local_agent_visual_composer_wait_started",
            timeout_seconds=timeout,
            poll_interval_seconds=poll_interval,
        )
        while True:
            activation_error = self._activate_target_by_name(target)
            active_app = self.frontmost_app()
            frontmost = active_app == target.app_name
            bounds = self.window_bounds(target) if frontmost else None
            visual = self.visual_detection_result(
                target,
                window_bounds=bounds,
                logs_dir=logs_dir,
                visual_debug=visual_debug,
            )
            placeholder_visible = (
                True
                if visual.placeholder_found
                else (False if visual.placeholder_detection_backend_available else None)
            )
            elapsed = max(0.0, self.monotonic_fn() - started_at)
            last_result = CodexVisualComposerStateResult(
                target_app=target.app_name,
                codex_frontmost=frontmost,
                codex_window_bounds=bounds,
                bounded_screenshot_captured=visual.screenshot_captured,
                placeholder_detection_backend_available=(
                    visual.placeholder_detection_backend_available
                ),
                placeholder_visible=placeholder_visible,
                placeholder_error=visual.placeholder_detection_error,
                plus_anchor_found=visual.plus_button_found,
                plus_anchor_click_point=visual.computed_click_point,
                plus_anchor_confidence=visual.plus_button_confidence,
                poll_count=polls,
                elapsed_wait_seconds=elapsed,
                busy_timeout_action=None,
                selected_strategy="visual_placeholder_immediate"
                if placeholder_visible is True
                else "visual_busy_wait_poll",
                should_proceed=placeholder_visible is True,
                should_overwrite=False,
                should_abort=False,
                error=activation_error or visual.error,
            )
            _emit(
                event_callback,
                "local_agent_visual_composer_poll",
                poll_count=polls,
                codex_frontmost=frontmost,
                window_bounds=bounds,
                placeholder_visible=placeholder_visible,
                plus_anchor_found=visual.plus_button_found,
                elapsed_wait_seconds=elapsed,
            )
            if placeholder_visible is True:
                return last_result
            if self.monotonic_fn() >= deadline:
                action = _visual_busy_timeout_action(target)
                should_overwrite = action == "overwrite" and visual.plus_button_found
                should_abort = action == "abort" or not should_overwrite
                selected_strategy = (
                    "visual_plus_anchor_overwrite"
                    if should_overwrite
                    else "visual_busy_timeout_abort"
                )
                _emit(
                    event_callback,
                    "local_agent_visual_composer_busy_timeout",
                    action=action,
                    plus_anchor_found=visual.plus_button_found,
                    elapsed_wait_seconds=elapsed,
                )
                return CodexVisualComposerStateResult(
                    **{
                        **last_result.__dict__,
                        "busy_timeout_action": action,
                        "selected_strategy": selected_strategy,
                        "should_proceed": should_overwrite,
                        "should_overwrite": should_overwrite,
                        "should_abort": should_abort,
                        "error": (
                            None
                            if should_overwrite
                            else "Codex composer busy timeout reached; abort selected."
                        ),
                    }
                )
            polls += 1
            self.sleep_fn(poll_interval)

    def visual_detection_result(
        self,
        target: ManualStageTarget,
        *,
        window_bounds: tuple[int, int, int, int] | None = None,
        logs_dir: Path | None = None,
        visual_debug: bool = False,
    ) -> VisualDetectionResult:
        if self.visual_detector is None:
            return VisualDetectionResult(
                backend_available=False,
                screenshot_captured=False,
                window_bounds=window_bounds,
                safe_region_bounds=None,
                error="Visual detector is not configured.",
            )
        return self.visual_detector.detect(
            target=target,
            window_bounds=window_bounds if window_bounds is not None else self.window_bounds(target),
            logs_dir=logs_dir,
            write_debug=visual_debug,
        )

    def _activate_target_by_name(self, target: ManualStageTarget) -> str | None:
        app_name = target.app_name.replace("\\", "\\\\").replace('"', '\\"')
        script = f'tell application "{app_name}" to activate'
        completed = self.runner(
            [self.osascript_executable, "-e", script],
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            return (completed.stderr or completed.stdout or "").strip() or "Codex activation failed."
        return None

    def visual_click_preview(
        self,
        target: ManualStageTarget,
        *,
        logs_dir: Path | None = None,
        visual_debug: bool = False,
        require_plus: bool = False,
    ) -> LocalAgentFocusResult:
        visual = self.visual_detection_result(
            target,
            logs_dir=logs_dir,
            visual_debug=visual_debug,
        )
        if require_plus and visual.selected_strategy != "visual_plus_anchor":
            return LocalAgentFocusResult(
                window_bounds=visual.window_bounds,
                fallback_click_point=visual.computed_click_point,
                selected_input_candidate_summary=visual.selected_strategy,
                used_fallback=True,
                error="Visual plus-button anchor was not detected.",
            )
        if not visual.click_point_safe or visual.computed_click_point is None:
            return LocalAgentFocusResult(
                window_bounds=visual.window_bounds,
                fallback_click_point=visual.computed_click_point,
                selected_input_candidate_summary=visual.selected_strategy,
                used_fallback=True,
                error=visual.error or "No safe visual click target was detected.",
            )
        active_app = self.frontmost_app()
        return LocalAgentFocusResult(
            active_app_after=active_app,
            app_frontmost=active_app == target.app_name,
            window_bounds=visual.window_bounds,
            fallback_click_point=visual.computed_click_point,
            selected_input_candidate_summary=visual.selected_strategy,
            used_fallback=True,
        )

    def direct_plus_anchor_preview(
        self,
        target: ManualStageTarget,
        *,
        logs_dir: Path | None = None,
        visual_debug: bool = False,
    ) -> LocalAgentFocusResult:
        visual = self.visual_detection_result(
            target,
            logs_dir=logs_dir,
            visual_debug=visual_debug,
        )
        point = _direct_plus_anchor_click_point(
            visual.plus_button_bbox,
            target,
            visual.window_bounds,
        )
        plus_center = _rect_center(visual.plus_button_bbox)
        active_app = self.frontmost_app()
        if point is None:
            return LocalAgentFocusResult(
                active_app_after=active_app,
                app_frontmost=active_app == target.app_name,
                window_bounds=visual.window_bounds,
                fallback_click_point=None,
                plus_button_bbox=visual.plus_button_bbox,
                plus_button_center=plus_center,
                direct_plus_anchor_x_offset=target.direct_plus_anchor_x_offset,
                direct_plus_anchor_y_offset=target.direct_plus_anchor_y_offset,
                selected_input_candidate_summary="direct_plus_anchor",
                click_backend=_resolve_click_backend(target),
                pyautogui_available=self._pyautogui_available(),
                used_fallback=True,
                error=visual.error
                or "Direct plus-anchor could not produce a safe Codex composer click point.",
            )
        return LocalAgentFocusResult(
            active_app_after=active_app,
            app_frontmost=active_app == target.app_name,
            window_bounds=visual.window_bounds,
            fallback_click_point=point,
            plus_button_bbox=visual.plus_button_bbox,
            plus_button_center=plus_center,
            direct_plus_anchor_x_offset=target.direct_plus_anchor_x_offset,
            direct_plus_anchor_y_offset=target.direct_plus_anchor_y_offset,
            selected_input_candidate_summary="direct_plus_anchor",
            click_backend=_resolve_click_backend(target),
            pyautogui_available=self._pyautogui_available(),
            used_fallback=True,
        )

    def click_direct_plus_anchor(
        self,
        target: ManualStageTarget,
        *,
        logs_dir: Path | None = None,
        visual_debug: bool = False,
        click_backend: str | None = None,
    ) -> LocalAgentFocusResult:
        backend = _resolve_click_backend(target, click_backend)
        preview = self.direct_plus_anchor_preview(
            target,
            logs_dir=logs_dir,
            visual_debug=visual_debug,
        )
        if preview.fallback_click_point is None:
            return _with_click_backend(preview, backend, self._pyautogui_available())
        click_error = self._click_point(preview.fallback_click_point, backend=backend)
        if click_error:
            return LocalAgentFocusResult(
                active_app_before=preview.active_app_after,
                active_app_after=self.frontmost_app(),
                app_frontmost=preview.app_frontmost,
                window_bounds=preview.window_bounds,
                fallback_click_point=preview.fallback_click_point,
                plus_button_bbox=preview.plus_button_bbox,
                plus_button_center=preview.plus_button_center,
                direct_plus_anchor_x_offset=preview.direct_plus_anchor_x_offset,
                direct_plus_anchor_y_offset=preview.direct_plus_anchor_y_offset,
                selected_input_candidate_summary=preview.selected_input_candidate_summary,
                click_backend=backend,
                pyautogui_available=self._pyautogui_available(),
                used_fallback=True,
                error=click_error,
            )
        snapshot = self._snapshot(target)
        return LocalAgentFocusResult(
            active_app_before=preview.active_app_after,
            active_app_after=snapshot.active_app,
            app_frontmost=snapshot.active_app == target.app_name,
            selected_input_candidate_summary="direct_plus_anchor",
            focused_element_summary=snapshot.focused_element_summary,
            window_bounds=preview.window_bounds,
            fallback_click_point=preview.fallback_click_point,
            plus_button_bbox=preview.plus_button_bbox,
            plus_button_center=preview.plus_button_center,
            direct_plus_anchor_x_offset=preview.direct_plus_anchor_x_offset,
            direct_plus_anchor_y_offset=preview.direct_plus_anchor_y_offset,
            click_backend=backend,
            pyautogui_available=self._pyautogui_available(),
            succeeded=True,
            used_fallback=True,
        )

    def click_visual_input(
        self,
        target: ManualStageTarget,
        *,
        logs_dir: Path | None = None,
        visual_debug: bool = False,
        require_plus: bool = False,
        click_backend: str | None = None,
    ) -> LocalAgentFocusResult:
        backend = _resolve_click_backend(target, click_backend)
        preview = self.visual_click_preview(
            target,
            logs_dir=logs_dir,
            visual_debug=visual_debug,
            require_plus=require_plus,
        )
        if preview.fallback_click_point is None:
            return _with_click_backend(preview, backend, self._pyautogui_available())
        click_error = self._click_point(preview.fallback_click_point, backend=backend)
        if click_error:
            return LocalAgentFocusResult(
                active_app_before=preview.active_app_after,
                active_app_after=self.frontmost_app(),
                app_frontmost=preview.app_frontmost,
                window_bounds=preview.window_bounds,
                fallback_click_point=preview.fallback_click_point,
                selected_input_candidate_summary=preview.selected_input_candidate_summary,
                click_backend=backend,
                pyautogui_available=self._pyautogui_available(),
                used_fallback=True,
                error=click_error,
            )
        snapshot = self._snapshot(target)
        return LocalAgentFocusResult(
            active_app_before=preview.active_app_after,
            active_app_after=snapshot.active_app,
            app_frontmost=snapshot.active_app == target.app_name,
            selected_input_candidate_summary=preview.selected_input_candidate_summary,
            focused_element_summary=snapshot.focused_element_summary,
            window_bounds=preview.window_bounds,
            fallback_click_point=preview.fallback_click_point,
            click_backend=backend,
            pyautogui_available=self._pyautogui_available(),
            succeeded=True,
            used_fallback=True,
        )

    def run_paste_test(
        self,
        target: ManualStageTarget,
        *,
        clipboard: Clipboard,
        marker_text: str = CODEX_PASTE_TEST_MARKER,
        logs_dir: Path | None = None,
        visual_debug: bool = True,
        click_backend: str | None = None,
        paste_backend: str | None = None,
        event_callback: Callable[[str, dict[str, object]], None] | None = None,
    ) -> CodexPasteTestResult:
        backend = _resolve_click_backend(target, click_backend)
        resolved_paste_backend = _resolve_paste_backend(target, paste_backend)
        if marker_text == CODEX_PASTE_TEST_MARKER:
            marker_text = target.visual_text_recognition_marker_text or marker_text
        _emit(
            event_callback,
            "codex_paste_test_started",
            target_app=target.app_name,
            click_backend=backend,
            paste_backend=resolved_paste_backend,
        )
        _emit(
            event_callback,
            "local_agent_paste_backend_selected",
            paste_backend=resolved_paste_backend,
        )
        composer_state = self.wait_for_visual_composer_ready(
            target,
            logs_dir=logs_dir,
            visual_debug=visual_debug,
            event_callback=event_callback,
        )
        if not composer_state.should_proceed:
            visual = _visual_detection_from_composer_state(composer_state)
            error = composer_state.error or (
                "Codex composer was not ready for paste-test."
            )
            _emit(
                event_callback,
                "codex_paste_test_marker_not_detectable",
                reason="composer_not_ready",
                selected_strategy=composer_state.selected_strategy,
                placeholder_visible=composer_state.placeholder_visible,
                error=error,
            )
            return _paste_test_result(
                target=target,
                marker_text=marker_text,
                visual=visual,
                click_backend=backend,
                paste_backend=resolved_paste_backend,
                pyautogui_available=self._pyautogui_available(),
                codex_frontmost_before_click=composer_state.codex_frontmost,
                error=error,
            )
        visual = self.visual_detection_result(
            target,
            logs_dir=logs_dir,
            visual_debug=visual_debug,
        )
        visual = _prefer_placeholder_click_when_idle(visual, composer_state)
        frontmost_before_click = self.frontmost_app() == target.app_name
        if (
            visual.selected_strategy not in {"visual_plus_anchor", "visual_placeholder_anchor"}
            or visual.computed_click_point is None
            or not visual.click_point_safe
        ):
            _emit(
                event_callback,
                "codex_paste_test_marker_not_detectable",
                reason="no_safe_visual_plus_anchor",
            )
            return _paste_test_result(
                target=target,
                marker_text=marker_text,
                visual=visual,
                click_backend=backend,
                paste_backend=resolved_paste_backend,
                pyautogui_available=self._pyautogui_available(),
                codex_frontmost_before_click=frontmost_before_click,
                error=visual.error or "No safe visual plus-anchor click target was detected.",
            )

        _emit(
            event_callback,
            "codex_paste_test_click_target_selected",
            click_point=visual.computed_click_point,
            plus_button_confidence=visual.plus_button_confidence,
            click_backend=backend,
        )
        click_error = self._click_point(visual.computed_click_point, backend=backend)
        frontmost_after_click = self.frontmost_app() == target.app_name
        if click_error:
            return _paste_test_result(
                target=target,
                marker_text=marker_text,
                visual=visual,
                click_backend=backend,
                paste_backend=resolved_paste_backend,
                pyautogui_available=self._pyautogui_available(),
                codex_frontmost_before_click=frontmost_before_click,
                codex_frontmost_after_click=frontmost_after_click,
                click_attempted=True,
                error=click_error,
            )

        if resolved_paste_backend == "pyautogui":
            paste_run = self._run_pyautogui_paste_test_variants(
                target,
                clipboard=clipboard,
                marker_text=marker_text,
                visual=visual,
                click_backend=backend,
                logs_dir=logs_dir,
                event_callback=event_callback,
            )
        else:
            paste_run = self._run_single_paste_test_attempt(
                target,
                clipboard=clipboard,
                marker_text=marker_text,
                paste_backend=resolved_paste_backend,
                logs_dir=logs_dir,
                event_callback=event_callback,
            )
        if paste_run.paste_error and not paste_run.paste_succeeded:
            return _paste_test_result(
                target=target,
                marker_text=marker_text,
                visual=visual,
                click_backend=backend,
                paste_backend=resolved_paste_backend,
                pyautogui_available=self._pyautogui_available(),
                codex_frontmost_before_click=frontmost_before_click,
                codex_frontmost_after_click=frontmost_after_click,
                click_attempted=True,
                click_succeeded=True,
                paste_attempted=paste_run.paste_attempted,
                clipboard_length=len(marker_text),
                error=paste_run.paste_error,
                paste_variant_attempts=paste_run.paste_variant_attempts,
                paste_variant_attempted=paste_run.paste_variant_attempted,
                paste_variant_succeeded=paste_run.paste_variant_succeeded,
                literal_v_detected=paste_run.literal_v_detected,
                final_paste_strategy=paste_run.final_paste_strategy,
            )

        marker_visual = paste_run.marker_visual
        snapshot = self._snapshot(target)
        marker_detected = marker_visual.marker_found if marker_visual else None
        if marker_detected is None:
            marker_detected = _prompt_present_in_text(marker_text, snapshot.focused_text)
            if snapshot.focused_element_summary == "unknown":
                marker_detected = None
        if marker_visual and marker_visual.marker_found is True:
            marker_detected = True
        marker_presence_detectable = marker_detected is not None
        if marker_detected:
            _emit(event_callback, "codex_paste_test_marker_detected")
        else:
            _emit(
                event_callback,
                "codex_paste_test_marker_not_detectable",
                marker_presence_detectable=marker_presence_detectable,
            )

        cleanup_attempted = paste_run.cleanup_attempted
        cleanup_success: bool | None = paste_run.cleanup_success
        safe_cleanup_possible = (
            marker_detected is True
            and snapshot.focused_text is not None
            and _prompt_present_in_text(marker_text, snapshot.focused_text) is True
            and not cleanup_attempted
        )
        if safe_cleanup_possible:
            cleanup_attempted = True
            _emit(event_callback, "codex_paste_test_cleanup_attempted")
            cleanup_error = self._clear_focused_text()
            if cleanup_error:
                cleanup_success = False
            else:
                self.sleep_fn(0.1)
                cleanup_snapshot = self._snapshot(target)
                marker_after_cleanup = _prompt_present_in_text(
                    marker_text,
                    cleanup_snapshot.focused_text,
                )
                cleanup_success = False if marker_after_cleanup else True

        result = _paste_test_result(
            target=target,
            marker_text=marker_text,
            visual=visual,
            click_backend=backend,
            paste_backend=resolved_paste_backend,
            pyautogui_available=self._pyautogui_available(),
            codex_frontmost_before_click=frontmost_before_click,
            codex_frontmost_after_click=frontmost_after_click,
            click_attempted=True,
            click_succeeded=True,
            paste_attempted=paste_run.paste_attempted,
            paste_succeeded=paste_run.paste_succeeded,
            paste_variant_attempts=paste_run.paste_variant_attempts,
            paste_variant_attempted=paste_run.paste_variant_attempted,
            paste_variant_succeeded=paste_run.paste_variant_succeeded,
            literal_v_detected=paste_run.literal_v_detected,
            final_paste_strategy=paste_run.final_paste_strategy,
            clipboard_length=len(marker_text),
            marker_detected=marker_detected,
            marker_presence_detectable=marker_presence_detectable,
            focused_element_summary=snapshot.focused_element_summary,
            focused_text_length_after_paste=snapshot.focused_text_length,
            cleanup_attempted=cleanup_attempted,
            cleanup_success=cleanup_success,
            manual_cleanup_required=cleanup_success is not True,
            marker_visual=marker_visual,
            error=paste_run.paste_error if not marker_detected else None,
        )
        _emit(
            event_callback,
            "codex_paste_test_completed",
            paste_attempted=result.paste_attempted,
            marker_detected=result.marker_detected,
            cleanup_success=result.cleanup_success,
        )
        return result

    def run_focus_target_test(
        self,
        target: ManualStageTarget,
        *,
        marker_text: str = "x",
        logs_dir: Path | None = None,
        visual_debug: bool = True,
        click_backend: str | None = None,
        event_callback: Callable[[str, dict[str, object]], None] | None = None,
    ) -> CodexFocusTargetComparisonResult:
        backend = _resolve_click_backend(target, click_backend)
        _emit(
            event_callback,
            "codex_focus_target_comparison_started",
            target_app=target.app_name,
            click_backend=backend,
            marker_text=marker_text,
        )
        activation_error = self._activate_target_by_name(target)
        active_app = self.frontmost_app()
        codex_frontmost = active_app == target.app_name
        window_bounds = self.window_bounds(target) if codex_frontmost else None
        visual = (
            self.visual_detection_result(
                target,
                window_bounds=window_bounds,
                logs_dir=logs_dir,
                visual_debug=visual_debug,
            )
            if window_bounds is not None
            else VisualDetectionResult(
                backend_available=self.visual_detector is not None,
                screenshot_captured=False,
                window_bounds=None,
                safe_region_bounds=None,
                error="No plausible main Codex window was selected.",
            )
        )
        focus_safe_region_bounds = (
            composer_text_search_region(window_bounds).as_tuple()
            if window_bounds is not None
            else visual.safe_region_bounds
        )
        candidates = self.build_focus_target_candidates(target, visual)
        attempts: list[CodexFocusTargetAttemptResult] = []
        selected_candidate: CodexFocusTargetCandidate | None = None
        manual_cleanup_required = False
        stopped_reason: str | None = None
        comparison_ocr_lines: list[str] = []

        if activation_error or not codex_frontmost or window_bounds is None:
            if activation_error:
                error = activation_error
            elif not codex_frontmost:
                error = "Codex was not frontmost for focus-target test."
            else:
                error = "No plausible main Codex window was selected for focus-target test."
            result = CodexFocusTargetComparisonResult(
                target_app=target.app_name,
                marker_text=marker_text,
                click_backend=backend,
                codex_frontmost=codex_frontmost,
                window_bounds=window_bounds,
                visual_backend_available=visual.backend_available,
                visual_screenshot_captured=visual.screenshot_captured,
                placeholder_bbox=visual.placeholder_bbox,
                plus_button_bbox=visual.plus_button_bbox,
                safe_region_bounds=focus_safe_region_bounds,
                candidates=candidates,
                attempts=(),
                error=error,
            )
            _emit(
                event_callback,
                "codex_focus_target_comparison_completed",
                selected_candidate=None,
                stopped_reason=error,
            )
            return self._write_focus_target_artifacts(
                result,
                visual=visual,
                logs_dir=logs_dir,
                ocr_lines=comparison_ocr_lines,
            )
        if not candidates:
            result = CodexFocusTargetComparisonResult(
                target_app=target.app_name,
                marker_text=marker_text,
                click_backend=backend,
                codex_frontmost=codex_frontmost,
                window_bounds=window_bounds,
                visual_backend_available=visual.backend_available,
                visual_screenshot_captured=visual.screenshot_captured,
                placeholder_bbox=visual.placeholder_bbox,
                plus_button_bbox=visual.plus_button_bbox,
                safe_region_bounds=focus_safe_region_bounds,
                candidates=(),
                attempts=(),
                stopped_reason="No bounded Codex composer click candidates were available.",
            )
            _emit(
                event_callback,
                "codex_focus_target_comparison_completed",
                selected_candidate=None,
                stopped_reason=result.stopped_reason,
            )
            return self._write_focus_target_artifacts(
                result,
                visual=visual,
                logs_dir=logs_dir,
                ocr_lines=comparison_ocr_lines,
            )

        for candidate in candidates:
            if not candidate.safe:
                attempts.append(
                    CodexFocusTargetAttemptResult(
                        candidate_name=candidate.name,
                        candidate_family=candidate.family,
                        click_point=candidate.click_point,
                        click_point_safe=False,
                        error=candidate.rejection_reason
                        or "Candidate click point is outside the bounded safe region.",
                    )
                )
                continue
            _emit(
                event_callback,
                "codex_focus_target_candidate_attempted",
                candidate_name=candidate.name,
                candidate_family=candidate.family,
                click_point=candidate.click_point,
            )
            click_error = self._click_point(candidate.click_point, backend=backend)
            if click_error:
                attempts.append(
                    CodexFocusTargetAttemptResult(
                        candidate_name=candidate.name,
                        candidate_family=candidate.family,
                        click_point=candidate.click_point,
                        click_point_safe=True,
                        click_attempted=True,
                        error=click_error,
                    )
                )
                continue
            type_error = self._pyautogui_write(marker_text, interval=0.001)
            if type_error:
                attempts.append(
                    CodexFocusTargetAttemptResult(
                        candidate_name=candidate.name,
                        candidate_family=candidate.family,
                        click_point=candidate.click_point,
                        click_point_safe=True,
                        click_attempted=True,
                        click_succeeded=True,
                        typed_marker_attempted=True,
                        error=type_error,
                    )
                )
                continue
            self.sleep_fn(0.2)
            marker_visual = self.detect_codex_prompt_presence(
                target,
                expected_text=marker_text,
                logs_dir=logs_dir,
                write_debug=True,
            )
            marker_ocr_text = _read_marker_ocr_text(marker_visual)
            comparison_ocr_lines.append(
                f"## {candidate.name}\n"
                f"marker_found={marker_visual.marker_found}\n"
                f"confidence={marker_visual.marker_confidence}\n"
                f"{marker_ocr_text}\n"
            )
            cleanup_error = self._pyautogui_press("backspace")
            cleanup_attempted = True
            cleanup_marker_found: bool | None = None
            cleanup_success: bool | None = None
            if cleanup_error is None:
                self.sleep_fn(0.1)
                cleanup_visual = self.detect_codex_prompt_presence(
                    target,
                    expected_text=marker_text,
                    logs_dir=logs_dir,
                    write_debug=False,
                )
                cleanup_marker_found = cleanup_visual.marker_found
                if cleanup_marker_found is False:
                    cleanup_success = True
                elif cleanup_marker_found is True:
                    cleanup_success = False
                else:
                    cleanup_success = None
            attempt = CodexFocusTargetAttemptResult(
                candidate_name=candidate.name,
                candidate_family=candidate.family,
                click_point=candidate.click_point,
                click_point_safe=True,
                click_attempted=True,
                click_succeeded=True,
                typed_marker_attempted=True,
                typed_marker_succeeded=True,
                marker_found=marker_visual.marker_found,
                marker_confidence=marker_visual.marker_confidence,
                marker_match_text=marker_visual.marker_match_text,
                marker_ocr_text=marker_ocr_text,
                marker_ocr_text_path=marker_visual.ocr_text_path,
                cleanup_attempted=cleanup_attempted,
                cleanup_success=cleanup_success,
                cleanup_marker_found=cleanup_marker_found,
                error=cleanup_error,
            )
            attempts.append(attempt)
            _emit(
                event_callback,
                "codex_focus_target_candidate_completed",
                candidate_name=candidate.name,
                marker_found=marker_visual.marker_found,
                cleanup_success=cleanup_success,
                error=cleanup_error,
            )
            if marker_visual.marker_found is True and selected_candidate is None:
                selected_candidate = candidate
                _emit(
                    event_callback,
                    "codex_focus_target_candidate_selected",
                    candidate_name=candidate.name,
                    click_point=candidate.click_point,
                )
            if cleanup_success is None or cleanup_success is False:
                manual_cleanup_required = True
                stopped_reason = (
                    "Cleanup could not be verified after typing the one-character marker."
                    if cleanup_success is None
                    else "Cleanup failed after typing the one-character marker."
                )
                break

        result = CodexFocusTargetComparisonResult(
            target_app=target.app_name,
            marker_text=marker_text,
            click_backend=backend,
            codex_frontmost=codex_frontmost,
            window_bounds=window_bounds,
            visual_backend_available=visual.backend_available,
            visual_screenshot_captured=visual.screenshot_captured,
            placeholder_bbox=visual.placeholder_bbox,
            plus_button_bbox=visual.plus_button_bbox,
            safe_region_bounds=focus_safe_region_bounds,
            candidates=candidates,
            attempts=tuple(attempts),
            selected_candidate_name=selected_candidate.name if selected_candidate else None,
            selected_click_point=selected_candidate.click_point if selected_candidate else None,
            manual_cleanup_required=manual_cleanup_required,
            stopped_reason=stopped_reason,
        )
        result = self._write_focus_target_artifacts(
            result,
            visual=visual,
            logs_dir=logs_dir,
            ocr_lines=comparison_ocr_lines,
        )
        _emit(
            event_callback,
            "codex_focus_target_comparison_completed",
            selected_candidate=result.selected_candidate_name,
            stopped_reason=result.stopped_reason,
        )
        return result

    def build_focus_target_candidates(
        self,
        target: ManualStageTarget,
        visual: VisualDetectionResult,
    ) -> tuple[CodexFocusTargetCandidate, ...]:
        if visual.window_bounds is None:
            return ()
        window_bounds = visual.window_bounds
        _, _, window_width, window_height = window_bounds
        if window_width < 300 or window_height < 200:
            return ()
        candidates: list[CodexFocusTargetCandidate] = []
        if visual.placeholder_bbox is not None:
            px, py, pwidth, pheight = visual.placeholder_bbox
            placeholder_points = (
                ("placeholder_center", (int(px + pwidth / 2), int(py + pheight / 2))),
                ("placeholder_center_left", (int(px + pwidth * 0.35), int(py + pheight / 2))),
                ("placeholder_center_right", (int(px + pwidth * 0.65), int(py + pheight / 2))),
            )
            for name, point in placeholder_points:
                candidates.append(
                    _focus_candidate(
                        name=name,
                        family="placeholder",
                        point=point,
                        source="placeholder_bbox",
                        window_bounds=window_bounds,
                        safe_region=safe_search_region(window_bounds),
                    )
                )
        if visual.plus_button_bbox is not None:
            px, py, pwidth, pheight = visual.plus_button_bbox
            plus_center_x = int(px + pwidth / 2)
            plus_center_y = int(py + pheight / 2)
            for offset in tuple(dict.fromkeys((target.plus_anchor_y_offset, 50))):
                candidates.append(
                    _focus_candidate(
                        name=f"plus_anchor_y_offset_{offset}",
                        family="plus_anchor",
                        point=(
                            int(plus_center_x + target.plus_anchor_x_offset),
                            int(plus_center_y - offset),
                        ),
                        source="plus_button_bbox",
                        window_bounds=window_bounds,
                        safe_region=plus_search_region(window_bounds),
                        avoid_rect=VisualRect(px, py, pwidth, pheight),
                    )
                )
        text_region = composer_text_search_region(window_bounds)
        composer_points = (
            ("composer_band_center_left", (int(text_region.x + text_region.width * 0.35), text_region.center[1])),
            ("composer_band_placeholder_area", (int(text_region.x + text_region.width * 0.50), text_region.center[1])),
            ("composer_band_above_plus", (int(text_region.x + text_region.width * 0.04), int(text_region.y + text_region.height * 0.25))),
        )
        for name, point in composer_points:
            candidates.append(
                _focus_candidate(
                    name=name,
                    family="composer_band",
                    point=point,
                    source="composer_text_search_region",
                    window_bounds=window_bounds,
                    safe_region=text_region,
                )
            )
        for raw in target.owner_reviewed_focus_candidates:
            candidate = _owner_reviewed_focus_candidate(
                raw,
                window_bounds,
                text_region,
                visual.plus_button_bbox,
            )
            if candidate is not None:
                candidates.append(candidate)
        return tuple(_dedupe_focus_candidates(candidates))

    def detect_marker_presence(
        self,
        target: ManualStageTarget,
        *,
        marker_text: str,
        logs_dir: Path | None = None,
        write_debug: bool = False,
    ) -> VisualMarkerPresenceResult:
        if not target.visual_text_recognition_enabled:
            return VisualMarkerPresenceResult(
                marker_text=marker_text,
                marker_detection_backend=target.visual_text_recognition_ocr_backend,
                marker_detection_available=False,
                marker_found=None,
                marker_confidence=None,
                window_bounds=self.window_bounds(target),
                search_region_bounds=None,
                screenshot_captured=False,
                detection_reason="Visual text recognition is disabled in config.",
                error="Visual text recognition is disabled in config.",
            )
        if self.visual_detector is None:
            return VisualMarkerPresenceResult(
                marker_text=marker_text,
                marker_detection_backend="unavailable",
                marker_detection_available=False,
                marker_found=None,
                marker_confidence=None,
                window_bounds=None,
                search_region_bounds=None,
                screenshot_captured=False,
                error="Visual detector is not configured.",
            )
        return self.visual_detector.detect_marker_presence(
            marker_text=marker_text,
            window_bounds=self.window_bounds(target),
            logs_dir=logs_dir,
            write_debug=write_debug,
        )

    def detect_codex_prompt_presence(
        self,
        target: ManualStageTarget,
        *,
        expected_text: str,
        logs_dir: Path | None = None,
        write_debug: bool = False,
    ) -> VisualMarkerPresenceResult:
        """Detect expected prompt text in the bounded Codex composer region.

        This is intentionally OCR/window-bounded and does not paste, submit, or press
        any keys. The local-agent handoff path can later require this result before
        submit without changing the no-submit diagnostic behavior here.
        """
        return self.detect_marker_presence(
            target,
            marker_text=expected_text,
            logs_dir=logs_dir,
            write_debug=write_debug,
        )

    def _run_single_paste_test_attempt(
        self,
        target: ManualStageTarget,
        *,
        clipboard: Clipboard,
        marker_text: str,
        paste_backend: str,
        logs_dir: Path | None,
        event_callback: Callable[[str, dict[str, object]], None] | None,
    ) -> _PasteBackendRunResult:
        clipboard.copy_text(marker_text)
        _emit(
            event_callback,
            "codex_paste_test_marker_copied",
            clipboard_length=len(marker_text),
            paste_backend=paste_backend,
        )
        _emit(
            event_callback,
            "local_agent_system_events_paste_attempted",
            clipboard_length=len(marker_text),
        )
        paste_error = self._paste_clipboard(backend=paste_backend)
        _emit(event_callback, "codex_paste_test_paste_attempted", error=paste_error)
        if paste_error:
            return _PasteBackendRunResult(
                marker_visual=None,
                paste_attempted=True,
                paste_succeeded=False,
                paste_error=paste_error,
            )
        self.sleep_fn(0.2)
        marker_visual = self._detect_marker_after_paste(
            target,
            marker_text=marker_text,
            logs_dir=logs_dir,
            event_callback=event_callback,
            paste_backend=paste_backend,
        )
        return _PasteBackendRunResult(
            marker_visual=marker_visual,
            paste_attempted=True,
            paste_succeeded=True,
        )

    def _run_pyautogui_paste_test_variants(
        self,
        target: ManualStageTarget,
        *,
        clipboard: Clipboard,
        marker_text: str,
        visual: VisualDetectionResult,
        click_backend: str,
        logs_dir: Path | None,
        event_callback: Callable[[str, dict[str, object]], None] | None,
    ) -> _PasteBackendRunResult:
        if visual.computed_click_point is None:
            return _PasteBackendRunResult(
                marker_visual=None,
                paste_attempted=False,
                paste_succeeded=False,
                paste_error="No visual click point is available for PyAutoGUI paste-test.",
            )
        variants = list(_pyautogui_paste_variants(marker_text))
        attempts: list[CodexPasteVariantResult] = []
        last_marker_visual: VisualMarkerPresenceResult | None = None
        last_error: str | None = None
        any_literal_v = False
        cleanup_attempted_any = False
        cleanup_success_final: bool | None = None
        _emit(
            event_callback,
            "local_agent_pyautogui_paste_attempted",
            clipboard_length=len(marker_text),
            variant_count=len(variants),
        )
        for variant_name, variant_kind in variants:
            click_error = self._click_point(visual.computed_click_point, backend=click_backend)
            if click_error:
                attempt = CodexPasteVariantResult(
                    variant_name=variant_name,
                    attempted=False,
                    paste_error=click_error,
                )
                attempts.append(attempt)
                last_error = click_error
                break
            clipboard.copy_text(marker_text)
            _emit(
                event_callback,
                "codex_paste_test_marker_copied",
                clipboard_length=len(marker_text),
                paste_backend="pyautogui",
                paste_variant=variant_name,
            )
            _emit(
                event_callback,
                "local_agent_pyautogui_paste_variant_attempted",
                variant=variant_name,
            )
            paste_error = self._perform_pyautogui_paste_variant(
                variant_kind,
                marker_text=marker_text,
            )
            _emit(
                event_callback,
                "codex_paste_test_paste_attempted",
                error=paste_error,
                paste_variant=variant_name,
            )
            if paste_error:
                attempts.append(
                    CodexPasteVariantResult(
                        variant_name=variant_name,
                        attempted=True,
                        paste_error=paste_error,
                    )
                )
                last_error = paste_error
                continue
            self.sleep_fn(0.2)
            marker_visual = self._detect_marker_after_paste(
                target,
                marker_text=marker_text,
                logs_dir=logs_dir,
                event_callback=event_callback,
                paste_variant=variant_name,
            )
            last_marker_visual = marker_visual
            literal_v = _literal_v_detected(marker_visual)
            any_literal_v = any_literal_v or literal_v
            cleanup_attempted = False
            cleanup_success: bool | None = None
            marker_found = marker_visual.marker_found
            if marker_found:
                attempts.append(
                    CodexPasteVariantResult(
                        variant_name=variant_name,
                        attempted=True,
                        marker_found=True,
                        marker_confidence=marker_visual.marker_confidence,
                        literal_v_detected=literal_v,
                    )
                )
                _emit(
                    event_callback,
                    "local_agent_pyautogui_paste_completed",
                    variant=variant_name,
                )
                return _PasteBackendRunResult(
                    marker_visual=marker_visual,
                    paste_attempted=True,
                    paste_succeeded=True,
                    paste_variant_attempts=tuple(attempts),
                    paste_variant_attempted=variant_name,
                    paste_variant_succeeded=True,
                    literal_v_detected=any_literal_v,
                    final_paste_strategy=variant_name,
                    cleanup_attempted=cleanup_attempted_any,
                    cleanup_success=cleanup_success_final,
                )
            if literal_v or _partial_marker_text_detected(marker_text, marker_visual):
                cleanup_attempted = True
                cleanup_attempted_any = True
                _emit(
                    event_callback,
                    "codex_paste_test_cleanup_attempted",
                    paste_variant=variant_name,
                    reason="literal_v_or_partial_marker",
                )
                cleanup_error = self._clear_focused_text()
                cleanup_success = cleanup_error is None
                cleanup_success_final = cleanup_success
                if cleanup_success:
                    self.sleep_fn(0.1)
            attempts.append(
                CodexPasteVariantResult(
                    variant_name=variant_name,
                    attempted=True,
                    marker_found=marker_found,
                    marker_confidence=marker_visual.marker_confidence,
                    literal_v_detected=literal_v,
                    cleanup_attempted=cleanup_attempted,
                    cleanup_success=cleanup_success,
                )
            )
        final_attempt = attempts[-1].variant_name if attempts else None
        succeeded = any(attempt.marker_found is True for attempt in attempts)
        return _PasteBackendRunResult(
            marker_visual=last_marker_visual,
            paste_attempted=bool(attempts),
            paste_succeeded=last_error is None or last_marker_visual is not None,
            paste_error=None if last_marker_visual is not None else last_error,
            paste_variant_attempts=tuple(attempts),
            paste_variant_attempted=final_attempt,
            paste_variant_succeeded=succeeded,
            literal_v_detected=any_literal_v,
            final_paste_strategy=next(
                (attempt.variant_name for attempt in attempts if attempt.marker_found is True),
                final_attempt,
            ),
            cleanup_attempted=cleanup_attempted_any,
            cleanup_success=cleanup_success_final,
        )

    def _detect_marker_after_paste(
        self,
        target: ManualStageTarget,
        *,
        marker_text: str,
        logs_dir: Path | None,
        event_callback: Callable[[str, dict[str, object]], None] | None,
        paste_variant: str | None = None,
        paste_backend: str = "pyautogui",
    ) -> VisualMarkerPresenceResult:
        _emit(
            event_callback,
            "codex_marker_presence_detection_started",
            paste_variant=paste_variant,
        )
        if paste_backend == "pyautogui":
            _emit(
                event_callback,
                "local_agent_marker_ocr_after_pyautogui_paste",
                paste_variant=paste_variant,
            )
        marker_visual = self.detect_codex_prompt_presence(
            target,
            expected_text=marker_text,
            logs_dir=logs_dir,
            write_debug=True,
        )
        if marker_visual.screenshot_captured:
            _emit(
                event_callback,
                "codex_marker_presence_screenshot_captured",
                screenshot_path=marker_visual.screenshot_path,
                search_region_bounds=marker_visual.search_region_bounds,
                paste_variant=paste_variant,
            )
        if marker_visual.screenshot_path or marker_visual.annotated_screenshot_path:
            _emit(
                event_callback,
                "codex_marker_presence_debug_artifact_written",
                screenshot_path=marker_visual.screenshot_path,
                annotated_screenshot_path=marker_visual.annotated_screenshot_path,
                paste_variant=paste_variant,
            )
        if not marker_visual.marker_detection_available:
            _emit(
                event_callback,
                "codex_marker_presence_ocr_unavailable",
                error=marker_visual.error,
                paste_variant=paste_variant,
            )
            _emit(
                event_callback,
                "codex_marker_presence_unknown",
                error=marker_visual.error,
                paste_variant=paste_variant,
            )
        elif marker_visual.marker_found:
            _emit(
                event_callback,
                "codex_marker_presence_detected",
                confidence=marker_visual.marker_confidence,
                paste_variant=paste_variant,
            )
            _emit(
                event_callback,
                "local_agent_marker_present_after_paste",
                confidence=marker_visual.marker_confidence,
                paste_variant=paste_variant,
            )
        else:
            _emit(
                event_callback,
                "codex_marker_presence_unknown",
                marker_found=False,
                paste_variant=paste_variant,
            )
            _emit(
                event_callback,
                "local_agent_marker_absent_after_paste",
                confidence=marker_visual.marker_confidence,
                paste_variant=paste_variant,
            )
        return marker_visual

    def _click_point(self, point: tuple[int, int], *, backend: str = "system_events") -> str | None:
        if backend == "pyautogui":
            return self._click_point_pyautogui(point)
        if backend != "system_events":
            return f"Unsupported click backend: {backend}"
        x, y = point
        script = f"""
tell application "System Events"
  click at {{{x}, {y}}}
end tell
""".strip()
        completed = self.runner(
            [self.osascript_executable, "-e", script],
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            return (completed.stderr or completed.stdout or "").strip() or "visual anchor click failed"
        return None

    def _click_point_pyautogui(self, point: tuple[int, int]) -> str | None:
        x, y = point
        try:
            if self.pyautogui_clicker is not None:
                self.pyautogui_clicker(x, y)
            else:
                import pyautogui

                pyautogui.click(x, y)
        except ModuleNotFoundError:
            return "PyAutoGUI click backend is unavailable: pyautogui is not installed."
        except Exception as error:
            return f"PyAutoGUI click failed: {error}"
        return None

    def _pyautogui_available(self) -> bool:
        return (
            self.pyautogui_clicker is not None
            or self.pyautogui_hotkeyer is not None
            or self.pyautogui_key_downer is not None
            or self.pyautogui_key_upper is not None
            or self.pyautogui_presser is not None
            or self.pyautogui_writer is not None
            or importlib.util.find_spec("pyautogui") is not None
        )

    def _paste_clipboard(self, *, backend: str = "system_events") -> str | None:
        if backend == "pyautogui":
            return self._paste_clipboard_pyautogui()
        if backend != "system_events":
            return f"Unsupported paste backend: {backend}"
        script = 'tell application "System Events" to keystroke "v" using command down'
        completed = self.runner(
            [self.osascript_executable, "-e", script],
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            return (completed.stderr or completed.stdout or "").strip() or "clipboard paste failed"
        return None

    def _paste_clipboard_pyautogui(self) -> str | None:
        return self._pyautogui_hotkey("command", "v")

    def _perform_pyautogui_paste_variant(
        self,
        variant_kind: str,
        *,
        marker_text: str,
    ) -> str | None:
        if variant_kind == "hotkey_command":
            return self._pyautogui_hotkey("command", "v")
        if variant_kind == "hotkey_cmd":
            return self._pyautogui_hotkey("cmd", "v")
        if variant_kind == "keydown_command":
            return self._pyautogui_key_chord("command", "v")
        if variant_kind == "keydown_cmd":
            return self._pyautogui_key_chord("cmd", "v")
        if variant_kind == "ascii_typewrite_marker":
            if not _diagnostic_typewrite_allowed(marker_text):
                return "Diagnostic ASCII typewrite fallback is not allowed for this marker."
            return self._pyautogui_write(marker_text, interval=0.001)
        return f"Unsupported PyAutoGUI paste variant: {variant_kind}"

    def _pyautogui_hotkey(self, *keys: str) -> str | None:
        try:
            if self.pyautogui_hotkeyer is not None:
                self.pyautogui_hotkeyer(*keys)
            else:
                import pyautogui

                pyautogui.hotkey(*keys, interval=0.1)
        except ModuleNotFoundError:
            return "PyAutoGUI paste backend is unavailable: pyautogui is not installed."
        except Exception as error:
            return f"PyAutoGUI paste failed: {error}"
        return None

    def _pyautogui_key_chord(self, modifier_key: str, key: str) -> str | None:
        key_down_succeeded = False
        try:
            if (
                self.pyautogui_key_downer is not None
                and self.pyautogui_presser is not None
                and self.pyautogui_key_upper is not None
            ):
                self.pyautogui_key_downer(modifier_key)
                key_down_succeeded = True
                self.pyautogui_presser(key)
                self.pyautogui_key_upper(modifier_key)
            else:
                import pyautogui

                pyautogui.keyDown(modifier_key)
                key_down_succeeded = True
                pyautogui.press(key)
                pyautogui.keyUp(modifier_key)
        except ModuleNotFoundError:
            return "PyAutoGUI paste backend is unavailable: pyautogui is not installed."
        except Exception as error:
            if key_down_succeeded:
                self._pyautogui_key_up_best_effort(modifier_key)
            return f"PyAutoGUI paste failed: {error}"
        return None

    def _pyautogui_key_up_best_effort(self, key: str) -> None:
        try:
            if self.pyautogui_key_upper is not None:
                self.pyautogui_key_upper(key)
            else:
                import pyautogui

                pyautogui.keyUp(key)
        except Exception:
            return

    def _pyautogui_write(self, text: str, *, interval: float) -> str | None:
        try:
            if self.pyautogui_writer is not None:
                self.pyautogui_writer(text, interval=interval)
            else:
                import pyautogui

                pyautogui.write(text, interval=interval)
        except ModuleNotFoundError:
            return "PyAutoGUI paste backend is unavailable: pyautogui is not installed."
        except Exception as error:
            return f"PyAutoGUI diagnostic typewrite failed: {error}"
        return None

    def _pyautogui_press(self, key: str) -> str | None:
        try:
            if self.pyautogui_presser is not None:
                self.pyautogui_presser(key)
            else:
                import pyautogui

                pyautogui.press(key)
        except ModuleNotFoundError:
            return "PyAutoGUI key press backend is unavailable: pyautogui is not installed."
        except Exception as error:
            return f"PyAutoGUI key press failed: {error}"
        return None

    def _write_focus_target_artifacts(
        self,
        result: CodexFocusTargetComparisonResult,
        *,
        visual: VisualDetectionResult,
        logs_dir: Path | None,
        ocr_lines: list[str],
    ) -> CodexFocusTargetComparisonResult:
        if logs_dir is None:
            return result
        logs_dir.mkdir(parents=True, exist_ok=True)
        image_path = logs_dir / "codex_focus_target_comparison.png"
        annotated_path = logs_dir / "codex_focus_target_comparison_annotated.png"
        ocr_path = logs_dir / "codex_focus_target_comparison_ocr.txt"
        json_path = logs_dir / "codex_focus_target_comparison.json"
        ocr_path.write_text("\n".join(ocr_lines), encoding="utf-8")
        json_path.write_text(json.dumps(_focus_target_result_payload(result), indent=2), encoding="utf-8")
        self._write_focus_target_images(
            result,
            visual=visual,
            image_path=image_path,
            annotated_path=annotated_path,
        )
        return replace(
            result,
            comparison_image_path=str(image_path),
            comparison_annotated_image_path=str(annotated_path),
            comparison_ocr_text_path=str(ocr_path),
            comparison_json_path=str(json_path),
        )

    def _write_focus_target_images(
        self,
        result: CodexFocusTargetComparisonResult,
        *,
        visual: VisualDetectionResult,
        image_path: Path,
        annotated_path: Path,
    ) -> None:
        if not visual.debug_image_path:
            image_path.write_text("No focus-target screenshot was available.\n", encoding="utf-8")
            annotated_path.write_text("No focus-target screenshot was available.\n", encoding="utf-8")
            return
        try:
            from PIL import Image, ImageDraw  # type: ignore[import-not-found]

            screenshot = Image.open(visual.debug_image_path)
            screenshot.save(image_path)
            annotated = screenshot.copy()
            draw = ImageDraw.Draw(annotated)
            if result.window_bounds is None:
                annotated.save(annotated_path)
                return
            wx, wy, _, _ = result.window_bounds
            if result.safe_region_bounds is not None:
                sx, sy, sw, sh = result.safe_region_bounds
                draw.rectangle((sx - wx, sy - wy, sx - wx + sw, sy - wy + sh), outline="red", width=3)
            if result.placeholder_bbox is not None:
                px, py, pw, ph = result.placeholder_bbox
                draw.rectangle((px - wx, py - wy, px - wx + pw, py - wy + ph), outline="orange", width=3)
            if result.plus_button_bbox is not None:
                px, py, pw, ph = result.plus_button_bbox
                draw.rectangle((px - wx, py - wy, px - wx + pw, py - wy + ph), outline="blue", width=3)
            for candidate in result.candidates:
                x, y = candidate.click_point
                cx = x - wx
                cy = y - wy
                if candidate.name == result.selected_candidate_name:
                    color = "green"
                elif not candidate.safe:
                    color = "red"
                else:
                    color = "purple"
                draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), outline=color, width=2)
            annotated.save(annotated_path)
        except Exception as error:
            image_path.write_text(f"Focus target image artifact unavailable: {error}\n", encoding="utf-8")
            annotated_path.write_text(
                f"Focus target annotated artifact unavailable: {error}\n",
                encoding="utf-8",
            )

    def _clear_focused_text(self) -> str | None:
        for script in [
            'tell application "System Events" to keystroke "a" using command down',
            'tell application "System Events" to key code 51',
        ]:
            completed = self.runner(
                [self.osascript_executable, "-e", script],
                check=False,
                text=True,
                capture_output=True,
            )
            if completed.returncode != 0:
                return (completed.stderr or completed.stdout or "").strip() or "cleanup failed"
        return None

    def enumerate_windows(self, target: ManualStageTarget) -> tuple[CodexWindowInfo, ...]:
        windows, _error = self._enumerate_windows(target)
        return windows

    def select_main_window(self, target: ManualStageTarget) -> CodexWindowSelectionResult:
        windows, error = self._enumerate_windows(target)
        evaluated = tuple(_evaluate_window_for_selection(window, target) for window in windows)
        candidates = [window for window in evaluated if not window.rejected]
        selected: CodexWindowInfo | None = None
        if candidates:
            selected = max(
                candidates,
                key=lambda window: (
                    window.visible is True,
                    (window.subrole or "") == "AXStandardWindow",
                    window.area,
                ),
            )
        if selected is not None:
            evaluated = tuple(
                replace(window, selected=window.index == selected.index)
                for window in evaluated
            )
            selected = next(window for window in evaluated if window.selected)
        return CodexWindowSelectionResult(
            target_app=target.app_name,
            strategy=target.window_selection_strategy,
            min_width=target.min_main_window_width,
            min_height=target.min_main_window_height,
            min_area=target.min_main_window_area,
            windows=evaluated,
            selected_window=selected,
            selected_bounds=selected.bounds if selected else None,
            plausible=selected is not None,
            error=error if selected is None else None,
        )

    def _enumerate_windows(
        self,
        target: ManualStageTarget,
    ) -> tuple[tuple[CodexWindowInfo, ...], str | None]:
        app_name = target.app_name.replace("\\", "\\\\").replace('"', '\\"')
        bundle_id = (
            target.bundle_id.replace("\\", "\\\\").replace('"', '\\"')
            if target.bundle_id
            else ""
        )
        if bundle_id:
            process_lookup = f"""
      set matchingProcesses to every application process whose bundle identifier is "{bundle_id}"
      if (count of matchingProcesses) is 0 then
        return "ERROR" & tab & "No running process found for bundle id {bundle_id}."
      end if
      set targetProcess to item 1 of matchingProcesses
      set bestWindowCount to -1
      repeat with candidateProcess in matchingProcesses
        set candidateProcessRef to contents of candidateProcess
        set candidateWindowCount to 0
        try
          set candidateWindowCount to count of (windows of candidateProcessRef)
        end try
        if candidateWindowCount is greater than bestWindowCount then
          set targetProcess to candidateProcessRef
          set bestWindowCount to candidateWindowCount
        end if
      end repeat
""".rstrip()
        else:
            process_lookup = f"""
      set targetProcess to application process "{app_name}"
""".rstrip()
        script = f"""
tell application "System Events"
  try
{process_lookup}
    tell targetProcess
      set outputValue to ""
      set windowCount to count of windows
      repeat with i from 1 to windowCount
        set targetWindow to window i
        set titleValue to ""
        set xValue to ""
        set yValue to ""
        set widthValue to ""
        set heightValue to ""
        set visibleValue to "unknown"
        set minimizedValue to "unknown"
        set fullscreenValue to "unknown"
        set roleValue to ""
        set subroleValue to ""
        try
          set titleValue to name of targetWindow as text
        end try
        try
          set posValue to position of targetWindow
          set xValue to item 1 of posValue as text
          set yValue to item 2 of posValue as text
        end try
        try
          set sizeValue to size of targetWindow
          set widthValue to item 1 of sizeValue as text
          set heightValue to item 2 of sizeValue as text
        end try
        try
          set visibleValue to visible of targetWindow as text
        end try
        try
          set minimizedValue to value of attribute "AXMinimized" of targetWindow as text
        end try
        try
          set fullscreenValue to value of attribute "AXFullScreen" of targetWindow as text
        end try
        try
          set roleValue to value of attribute "AXRole" of targetWindow as text
        end try
        try
          set subroleValue to value of attribute "AXSubrole" of targetWindow as text
        end try
        set outputValue to outputValue & (i as text) & tab & titleValue & tab & xValue & tab & yValue & tab & widthValue & tab & heightValue & tab & visibleValue & tab & minimizedValue & tab & fullscreenValue & tab & roleValue & tab & subroleValue
        if i is less than windowCount then
          set outputValue to outputValue & linefeed
        end if
      end repeat
      return outputValue
    end tell
  on error errorMessage
    return "ERROR" & tab & errorMessage
  end try
end tell
""".strip()
        completed = self.runner(
            [self.osascript_executable, "-e", script],
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            output = (completed.stderr or completed.stdout or "").strip()
            return (), output or "Codex window enumeration failed."
        return _parse_window_enumeration_output(completed.stdout)

    def window_bounds(self, target: ManualStageTarget) -> tuple[int, int, int, int] | None:
        return self.select_main_window(target).selected_bounds

    def window_relative_click_preview(
        self,
        target: ManualStageTarget,
        *,
        bounds: tuple[int, int, int, int] | None = None,
        active_app: str | None = None,
    ) -> LocalAgentFocusResult:
        bounds = self.window_bounds(target) if bounds is None else bounds
        active = self.frontmost_app() if active_app is None else active_app
        if (
            bounds is None
            or target.input_click_x_ratio is None
            or target.input_click_y_ratio is None
        ):
            return LocalAgentFocusResult(
                active_app_after=active,
                app_frontmost=active == target.app_name,
                window_bounds=bounds,
                used_fallback=True,
                error="Window-relative click fallback is not fully configured.",
            )
        x, y, width, height = bounds
        point = (
            int(x + width * target.input_click_x_ratio),
            int(y + height * target.input_click_y_ratio),
        )
        return LocalAgentFocusResult(
            active_app_after=active,
            app_frontmost=active == target.app_name,
            window_bounds=bounds,
            fallback_click_point=point,
            used_fallback=True,
        )

    def click_window_relative_input(self, target: ManualStageTarget) -> LocalAgentFocusResult:
        preview = self.window_relative_click_preview(target)
        if preview.fallback_click_point is None:
            return preview
        x, y = preview.fallback_click_point
        script = f"""
tell application "System Events"
  click at {{{x}, {y}}}
end tell
""".strip()
        completed = self.runner(
            [self.osascript_executable, "-e", script],
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            output = (completed.stderr or completed.stdout or "").strip()
            return LocalAgentFocusResult(
                active_app_after=preview.active_app_after,
                app_frontmost=preview.app_frontmost,
                window_bounds=preview.window_bounds,
                fallback_click_point=preview.fallback_click_point,
                used_fallback=True,
                error=output or "window-relative click failed",
            )
        snapshot = self._snapshot(target)
        return LocalAgentFocusResult(
            active_app_after=snapshot.active_app,
            app_frontmost=snapshot.active_app == target.app_name,
            input_candidate_count=preview.input_candidate_count,
            selected_input_candidate_summary=preview.selected_input_candidate_summary,
            focused_element_summary=snapshot.focused_element_summary,
            window_bounds=preview.window_bounds,
            fallback_click_point=preview.fallback_click_point,
            succeeded=True,
            used_fallback=True,
        )

    def inspect_before_submit(
        self,
        *,
        target: ManualStageTarget,
        prompt: str,
        clipboard_text: str,
    ) -> LocalAgentPreSubmitCheck:
        snapshot = self._snapshot(target)
        prompt_present = _prompt_present_in_text(prompt, snapshot.focused_text)
        return LocalAgentPreSubmitCheck(
            active_app=snapshot.active_app,
            target_app=target.app_name,
            app_frontmost=snapshot.active_app == target.app_name,
            prompt_length=len(prompt),
            clipboard_length=len(clipboard_text),
            focused_element_summary=snapshot.focused_element_summary,
            focused_text_length=snapshot.focused_text_length,
            input_text_length_after_paste=snapshot.focused_text_length,
            prompt_text_present=prompt_present,
        )

    def inspect_after_submit(
        self,
        *,
        target: ManualStageTarget,
        prompt: str,
        before: LocalAgentPreSubmitCheck,
    ) -> LocalAgentPostSubmitCheck:
        self.sleep_fn(self.post_submit_delay_seconds)
        snapshot = self._snapshot(target)
        prompt_present_after = _prompt_present_in_text(prompt, snapshot.focused_text)
        input_cleared: bool | None = None
        if before.prompt_text_present is True and prompt_present_after is not None:
            input_cleared = not prompt_present_after

        new_user_message_detected = _prompt_present_in_text(prompt, snapshot.ui_text)
        running_state_detected = _running_state_detected(snapshot.all_text)

        confirmed = bool(input_cleared or new_user_message_detected or running_state_detected)
        if input_cleared:
            reason = "input_cleared"
        elif new_user_message_detected:
            reason = "new_user_message"
        elif running_state_detected:
            reason = "running_state"
        else:
            reason = "not_detectable"
        return LocalAgentPostSubmitCheck(
            active_app_before=before.active_app,
            active_app_after=snapshot.active_app,
            focused_element_summary_after=snapshot.focused_element_summary,
            focused_text_length_before=before.focused_text_length,
            focused_text_length_after=snapshot.focused_text_length,
            input_cleared=input_cleared,
            new_user_message_detected=new_user_message_detected,
            running_state_detected=running_state_detected,
            confirmed=True if confirmed else None,
            confirmation_reason=reason,
        )

    def diagnose(self, target: ManualStageTarget) -> CodexUIDiagnostic:
        snapshot = self._snapshot(target)
        input_detectable = snapshot.focused_text is not None
        conversation_detectable = bool(snapshot.ui_text.strip())
        running = _running_state_detected(snapshot.all_text) if snapshot.accessibility_available else None
        limitation = None
        if not snapshot.accessibility_available:
            limitation = snapshot.error or "macOS Accessibility data was unavailable."
        elif not input_detectable:
            limitation = "Focused Codex input text was not detectable."
        candidate_result = None
        if snapshot.accessibility_available:
            candidate_result = self._inspect_input_candidate(target, focus=False)
        return CodexUIDiagnostic(
            target_app=target.app_name,
            active_app=snapshot.active_app,
            codex_app_active=snapshot.active_app == target.app_name,
            focused_element_summary=snapshot.focused_element_summary,
            input_field_detectable=input_detectable,
            focused_text_length=snapshot.focused_text_length,
            conversation_elements_detectable=conversation_detectable,
            running_state_detected=running,
            accessibility_available=snapshot.accessibility_available,
            input_candidate_count=(
                candidate_result.input_candidate_count if candidate_result is not None else None
            ),
            selected_input_candidate_summary=(
                candidate_result.selected_input_candidate_summary
                if candidate_result is not None
                else "unknown"
            ),
            limitation=limitation,
        )

    def _inspect_input_candidate(
        self,
        target: ManualStageTarget,
        *,
        focus: bool,
    ) -> LocalAgentFocusResult:
        app_name = target.app_name.replace("\\", "\\\\").replace('"', '\\"')
        focus_script = ""
        frontmost_script = ""
        if focus:
            frontmost_script = "      set frontmost to true"
            focus_script = """
      try
        perform action "AXRaise" of selectedInput
      end try
      try
        set focused of selectedInput to true
      end try
      delay 0.1
""".rstrip()
        script = f"""
tell application "System Events"
  set frontBefore to "unknown"
  set frontAfter to "unknown"
  try
    set frontBefore to name of first application process whose frontmost is true
  end try
  try
    tell application process "{app_name}"
{frontmost_script}
      set candidateItems to {{}}
      set elementItems to entire contents of front window
      repeat with childItem in elementItems
        set roleValue to ""
        set titleValue to ""
        set descriptionValue to ""
        set valueValue to ""
        set subroleValue to ""
        try
          set roleValue to value of attribute "AXRole" of childItem as text
        end try
        try
          set titleValue to value of attribute "AXTitle" of childItem as text
        end try
        try
          set descriptionValue to value of attribute "AXDescription" of childItem as text
        end try
        try
          set valueValue to value of attribute "AXValue" of childItem as text
        end try
        try
          set subroleValue to value of attribute "AXSubrole" of childItem as text
        end try
        set combinedValue to roleValue & " " & subroleValue & " " & titleValue & " " & descriptionValue & " " & valueValue
        ignoring case
          set looksEditable to roleValue is "AXTextArea" or roleValue is "AXTextField" or roleValue is "AXComboBox" or roleValue is "AXWebArea" or subroleValue contains "Text" or combinedValue contains "prompt" or combinedValue contains "composer" or combinedValue contains "input" or combinedValue contains "message" or combinedValue contains "editor" or combinedValue contains "ask"
        end ignoring
        if looksEditable then
          set end of candidateItems to childItem
        end if
      end repeat
      set candidateCount to count of candidateItems
      if candidateCount is 0 then
        try
          set frontAfter to name of first application process whose frontmost is true
        end try
        return frontBefore & linefeed & frontAfter & linefeed & "0" & linefeed & "unknown" & linefeed & "" & linefeed & "unknown"
      end if
      set selectedInput to item -1 of candidateItems
      set roleValue to "unknown"
      set descriptionValue to ""
      set valueValue to ""
      try
        set roleValue to value of attribute "AXRole" of selectedInput as text
      end try
      try
        set descriptionValue to value of attribute "AXDescription" of selectedInput as text
      end try
      try
        set valueValue to value of attribute "AXValue" of selectedInput as text
      end try
{focus_script}
      set focusedSummary to "unknown"
      try
        set focusedElement to value of attribute "AXFocusedUIElement"
        set focusedRole to value of attribute "AXRole" of focusedElement as text
        set focusedDescription to ""
        try
          set focusedDescription to value of attribute "AXDescription" of focusedElement as text
        end try
        if focusedDescription is "" then
          set focusedSummary to focusedRole
        else
          set focusedSummary to focusedRole & ": " & focusedDescription
        end if
      end try
      try
        set frontAfter to name of first application process whose frontmost is true
      end try
      if descriptionValue is "" then
        set selectedSummary to roleValue
      else
        set selectedSummary to roleValue & ": " & descriptionValue
      end if
      return frontBefore & linefeed & frontAfter & linefeed & (candidateCount as text) & linefeed & selectedSummary & linefeed & valueValue & linefeed & focusedSummary
    end tell
  on error errorMessage
    return frontBefore & linefeed & frontAfter & linefeed & "0" & linefeed & "unknown" & linefeed & "" & linefeed & "unknown" & linefeed & errorMessage
  end try
end tell
""".strip()
        completed = self.runner(
            [self.osascript_executable, "-e", script],
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            output = (completed.stderr or completed.stdout or "").strip()
            return LocalAgentFocusResult(error=output or "osascript failed")
        lines = completed.stdout.splitlines()
        active_before = lines[0].strip() if lines else None
        active_after = lines[1].strip() if len(lines) > 1 else None
        try:
            candidate_count = int(lines[2].strip()) if len(lines) > 2 else 0
        except ValueError:
            candidate_count = 0
        selected_summary = lines[3].strip() if len(lines) > 3 and lines[3].strip() else "unknown"
        selected_value = lines[4] if len(lines) > 4 else ""
        focused_summary = lines[5].strip() if len(lines) > 5 and lines[5].strip() else "unknown"
        error = lines[6].strip() if len(lines) > 6 and lines[6].strip() else None
        succeeded = candidate_count > 0 and active_after == target.app_name and error is None
        return LocalAgentFocusResult(
            active_app_before=active_before,
            active_app_after=active_after,
            app_frontmost=active_after == target.app_name,
            input_candidate_count=candidate_count,
            selected_input_candidate_summary=selected_summary,
            input_text_length_before_paste=len(selected_value),
            focused_element_summary=focused_summary,
            succeeded=succeeded,
            error=error,
        )

    def _snapshot(self, target: ManualStageTarget) -> _CodexUISnapshot:
        app_name = target.app_name.replace("\\", "\\\\").replace('"', '\\"')
        script = f"""
tell application "System Events"
  set frontApp to "unknown"
  try
    set frontApp to name of first application process whose frontmost is true
  end try
  set roleValue to "unknown"
  set descriptionValue to ""
  set valueValue to ""
  set uiTextValue to ""
  set buttonTextValue to ""
  try
    tell application process "{app_name}"
      set focusedElement to value of attribute "AXFocusedUIElement"
      try
        set roleValue to value of attribute "AXRole" of focusedElement as text
      end try
      try
        set descriptionValue to value of attribute "AXDescription" of focusedElement as text
      end try
      try
        set valueValue to value of attribute "AXValue" of focusedElement as text
      end try
      try
        set uiTextValue to value of static texts of front window as text
      end try
      try
        set buttonTextValue to description of buttons of front window as text
      end try
    end tell
  on error errorMessage
    return frontApp & linefeed & "ERROR" & linefeed & errorMessage & linefeed & "" & linefeed & "" & linefeed & ""
  end try
  return frontApp & linefeed & roleValue & linefeed & descriptionValue & linefeed & valueValue & linefeed & uiTextValue & linefeed & buttonTextValue
end tell
""".strip()
        completed = self.runner(
            [self.osascript_executable, "-e", script],
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            output = (completed.stderr or completed.stdout or "").strip()
            return _CodexUISnapshot(
                active_app=None,
                focused_element_summary=f"unavailable: {output}",
                focused_text=None,
                ui_text="",
                button_text="",
                accessibility_available=False,
                error=output or "osascript failed",
            )
        lines = completed.stdout.splitlines()
        active_app = lines[0].strip() if lines else None
        role = lines[1].strip() if len(lines) > 1 else "unknown"
        description = lines[2].strip() if len(lines) > 2 else ""
        if role == "ERROR":
            error = description or "Codex Accessibility data was unavailable."
            return _CodexUISnapshot(
                active_app=active_app,
                focused_element_summary=f"unavailable: {error}",
                focused_text=None,
                ui_text="",
                button_text="",
                accessibility_available=False,
                error=error,
            )
        focused_text = lines[3] if len(lines) > 3 else ""
        ui_text = lines[4] if len(lines) > 4 else ""
        button_text = "\n".join(lines[5:]) if len(lines) > 5 else ""
        summary = role if not description else f"{role}: {description}"
        return _CodexUISnapshot(
            active_app=active_app,
            focused_element_summary=summary,
            focused_text=focused_text,
            ui_text=ui_text,
            button_text=button_text,
            accessibility_available=True,
        )


def _prompt_present_in_text(prompt: str, text: str | None) -> bool | None:
    if text is None:
        return None
    if not text:
        return False
    normalized_prompt = " ".join(prompt.split())
    normalized_text = " ".join(text.split())
    if not normalized_prompt:
        return False
    if normalized_prompt in normalized_text:
        return True
    prefix = normalized_prompt[: min(120, len(normalized_prompt))]
    return bool(prefix and prefix in normalized_text)


def _emit(
    callback: Callable[[str, dict[str, object]], None] | None,
    event_type: str,
    **metadata: object,
) -> None:
    if callback is not None:
        callback(event_type, metadata)


def _timeout_policy(target: ManualStageTarget) -> str:
    if target.stop_on_idle_timeout:
        return "stop"
    if not target.dedicated_automation_session or not target.allow_overwrite_after_idle_timeout:
        return "stop"
    if target.on_busy_timeout == "abort":
        return "stop"
    if (
        target.on_busy_timeout == "overwrite"
        and target.composer_policy_mode == "dedicated_automation_session"
    ):
        return "overwrite"
    if target.dedicated_automation_session and target.allow_overwrite_after_idle_timeout:
        return "overwrite"
    return "stop"


def _visual_busy_timeout_action(target: ManualStageTarget) -> str:
    if target.stop_on_idle_timeout:
        return "abort"
    if not target.dedicated_automation_session or not target.allow_overwrite_after_idle_timeout:
        return "abort"
    if target.on_busy_timeout == "abort":
        return "abort"
    if (
        target.on_busy_timeout == "overwrite"
        and target.composer_policy_mode == "dedicated_automation_session"
    ):
        return "overwrite"
    return "abort"


def _parse_bbox(parts: list[str]) -> tuple[int, int, int, int] | None:
    if len(parts) < 5:
        return None
    try:
        values = [int(float(value)) for value in parts[1:5]]
    except ValueError:
        return None
    return (values[0], values[1], values[2], values[3])


def _parse_window_enumeration_output(
    output: str,
) -> tuple[tuple[CodexWindowInfo, ...], str | None]:
    stripped = output.strip()
    if not stripped:
        return (), "No windows were reported by System Events."
    lines = [line for line in stripped.splitlines() if line.strip()]
    if lines and lines[0].startswith("ERROR\t"):
        parts = lines[0].split("\t", 1)
        return (), parts[1] if len(parts) > 1 else "Codex window enumeration failed."
    legacy = _parse_legacy_front_window_bounds(lines)
    if legacy is not None:
        x, y, width, height = legacy
        return (
            (
                CodexWindowInfo(
                    index=1,
                    title="front window",
                    position=(x, y),
                    size=(width, height),
                    bounds=legacy,
                    area=width * height,
                    visible=True,
                    minimized=False,
                    fullscreen=None,
                    role=None,
                    subrole=None,
                ),
            ),
            None,
        )

    windows: list[CodexWindowInfo] = []
    for line in lines:
        parts = line.split("\t")
        if len(parts) < 11:
            continue
        index = _parse_int(parts[0]) or (len(windows) + 1)
        title = parts[1].strip()
        x = _parse_int(parts[2])
        y = _parse_int(parts[3])
        width = _parse_int(parts[4])
        height = _parse_int(parts[5])
        position = (x, y) if x is not None and y is not None else None
        size = (width, height) if width is not None and height is not None else None
        bounds = (
            (x, y, width, height)
            if x is not None and y is not None and width is not None and height is not None
            else None
        )
        area = width * height if width is not None and height is not None else 0
        windows.append(
            CodexWindowInfo(
                index=index,
                title=title,
                position=position,
                size=size,
                bounds=bounds,
                area=area,
                visible=_parse_bool(parts[6]),
                minimized=_parse_bool(parts[7]),
                fullscreen=_parse_bool(parts[8]),
                role=parts[9].strip() or None,
                subrole=parts[10].strip() or None,
            )
        )
    if not windows:
        return (), "Codex window enumeration output could not be parsed."
    return tuple(windows), None


def _parse_legacy_front_window_bounds(lines: list[str]) -> tuple[int, int, int, int] | None:
    if len(lines) != 4:
        return None
    values: list[int] = []
    for line in lines:
        value = _parse_int(line)
        if value is None:
            return None
        values.append(value)
    return (values[0], values[1], values[2], values[3])


def _parse_int(value: str) -> int | None:
    try:
        return int(float(value.strip()))
    except (TypeError, ValueError):
        return None


def _parse_bool(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized in {"true", "yes", "1"}:
        return True
    if normalized in {"false", "no", "0"}:
        return False
    return None


def _evaluate_window_for_selection(
    window: CodexWindowInfo,
    target: ManualStageTarget,
) -> CodexWindowInfo:
    reasons: list[str] = []
    if window.bounds is None or window.size is None:
        reasons.append("missing bounds")
    else:
        width, height = window.size
        if width < target.min_main_window_width:
            reasons.append(f"width {width} < {target.min_main_window_width}")
        if height < target.min_main_window_height:
            reasons.append(f"height {height} < {target.min_main_window_height}")
        if window.area < target.min_main_window_area:
            reasons.append(f"area {window.area} < {target.min_main_window_area}")
    if window.visible is False:
        reasons.append("not visible")
    if window.minimized is True:
        reasons.append("minimized")
    return replace(
        window,
        rejected=bool(reasons),
        rejection_reasons=tuple(reasons),
    )


def _window_rejection_summary(window: CodexWindowInfo) -> str:
    title = window.title or "(untitled)"
    return (
        f"#{window.index} {title} bounds={window.bounds or 'unknown'}: "
        + ", ".join(window.rejection_reasons)
    )


def _parse_composer_snapshot(output: str) -> CodexComposerSnapshot:
    lines = output.splitlines()
    active_app = lines[0].strip() if lines else None
    placeholder_found = False
    plus_found = False
    placeholder_bbox: tuple[int, int, int, int] | None = None
    plus_bbox: tuple[int, int, int, int] | None = None
    error: str | None = None
    for line in lines[1:]:
        parts = line.split("\t")
        if not parts or not parts[0]:
            continue
        if parts[0] == "ERROR":
            error = parts[1] if len(parts) > 1 else "Codex composer state unavailable."
        elif parts[0] == "PLACEHOLDER":
            placeholder_found = True
            placeholder_bbox = _parse_bbox(parts)
        elif parts[0] == "PLUS":
            plus_found = True
            plus_bbox = _parse_bbox(parts)
    return CodexComposerSnapshot(
        active_app=active_app,
        placeholder_found=placeholder_found,
        placeholder_bbox=placeholder_bbox,
        plus_button_found=plus_found,
        plus_button_bbox=plus_bbox,
        state=CODEX_COMPOSER_IDLE_EMPTY
        if placeholder_found
        else CODEX_COMPOSER_BUSY_OR_NONEMPTY,
        error=error,
    )


def _plus_anchor_click_point(
    plus_bbox: tuple[int, int, int, int] | None,
    target: ManualStageTarget,
    window_bounds: tuple[int, int, int, int] | None,
) -> tuple[int, int] | None:
    if plus_bbox is None or not target.plus_anchor_enabled:
        return None
    x, y, width, height = plus_bbox
    point = (
        int(x + width / 2 + target.plus_anchor_x_offset),
        int(y + height / 2 - target.plus_anchor_y_offset),
    )
    if x <= point[0] <= x + width and y <= point[1] <= y + height:
        return None
    if window_bounds is not None:
        wx, wy, w_width, w_height = window_bounds
        if not (wx <= point[0] <= wx + w_width and wy <= point[1] <= wy + w_height):
            return None
    return point


def _rect_center(rect: tuple[int, int, int, int] | None) -> tuple[int, int] | None:
    if rect is None:
        return None
    x, y, width, height = rect
    return (int(x + width / 2), int(y + height / 2))


def _direct_plus_anchor_click_point(
    plus_bbox: tuple[int, int, int, int] | None,
    target: ManualStageTarget,
    window_bounds: tuple[int, int, int, int] | None,
) -> tuple[int, int] | None:
    if plus_bbox is None or not target.direct_plus_anchor_enabled:
        return None
    plus_rect = VisualRect(*plus_bbox)
    plus_center_x, plus_center_y = plus_rect.center
    point = (
        int(plus_center_x + target.direct_plus_anchor_x_offset),
        int(plus_center_y - target.direct_plus_anchor_y_offset),
    )
    if plus_rect.contains_point(point):
        return None
    if window_bounds is None:
        return None
    rejection = _focus_point_rejection_reason(
        point,
        window_bounds=window_bounds,
        safe_region=composer_text_search_region(window_bounds),
        avoid_rect=plus_rect,
    )
    if rejection is not None:
        return None
    return point


def _idle_wait_result(
    *,
    target: ManualStageTarget,
    snapshot: CodexComposerSnapshot,
    state: str,
    timed_out: bool,
    timeout_policy: str,
    overwrite_allowed: bool,
    should_overwrite: bool,
    should_stop: bool,
    polls: int,
    message: str | None = None,
) -> CodexComposerIdleWaitResult:
    point = _plus_anchor_click_point(snapshot.plus_button_bbox, target, None)
    return CodexComposerIdleWaitResult(
        state=state,
        placeholder_found=snapshot.placeholder_found,
        placeholder_bbox=snapshot.placeholder_bbox,
        plus_button_found=snapshot.plus_button_found,
        plus_button_bbox=snapshot.plus_button_bbox,
        plus_anchor_click_point=point,
        timed_out=timed_out,
        timeout_policy=timeout_policy,
        overwrite_allowed=overwrite_allowed,
        should_overwrite=should_overwrite,
        should_stop=should_stop,
        polls=polls,
        last_observed_state=snapshot.state,
        message=message,
    )


def _pyautogui_paste_variants(marker_text: str) -> tuple[tuple[str, str], ...]:
    variants: list[tuple[str, str]] = [
        ("command_v_hotkey", "hotkey_command"),
        ("cmd_v_hotkey", "hotkey_cmd"),
        ("command_v_keydown", "keydown_command"),
        ("cmd_v_keydown", "keydown_cmd"),
    ]
    if _diagnostic_typewrite_allowed(marker_text):
        variants.append(("ascii_typewrite_marker", "ascii_typewrite_marker"))
    return tuple(variants)


def _diagnostic_typewrite_allowed(text: str) -> bool:
    return bool(text) and len(text) <= 128 and "\n" not in text and all(ord(char) < 128 for char in text)


def _read_marker_ocr_text(marker_visual: VisualMarkerPresenceResult) -> str:
    parts: list[str] = []
    if marker_visual.marker_match_text:
        parts.append(marker_visual.marker_match_text)
    if marker_visual.ocr_text_path:
        path = Path(marker_visual.ocr_text_path)
        try:
            if path.exists():
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return "\n".join(part for part in parts if part)


def _literal_v_detected(marker_visual: VisualMarkerPresenceResult) -> bool:
    if marker_visual.detection_reason == "literal_v_detected":
        return True
    text = _read_marker_ocr_text(marker_visual).strip().lower()
    return text == "v"


def _partial_marker_text_detected(
    marker_text: str,
    marker_visual: VisualMarkerPresenceResult,
) -> bool:
    text = _read_marker_ocr_text(marker_visual).lower()
    if not text:
        return False
    normalized = "".join(text.split())
    normalized_marker = "".join(marker_text.lower().split())
    if normalized_marker and normalized_marker in normalized:
        return True
    return any(fragment in normalized for fragment in ("agent_bridge", "codex_paste", "do_not_submit"))


def _focus_candidate(
    *,
    name: str,
    family: str,
    point: tuple[int, int],
    source: str,
    window_bounds: tuple[int, int, int, int],
    safe_region: VisualRect,
    avoid_rect: VisualRect | None = None,
) -> CodexFocusTargetCandidate:
    rejection_reason = _focus_point_rejection_reason(
        point,
        window_bounds=window_bounds,
        safe_region=safe_region,
        avoid_rect=avoid_rect,
    )
    return CodexFocusTargetCandidate(
        name=name,
        family=family,
        click_point=point,
        safe=rejection_reason is None,
        source=source,
        rejection_reason=rejection_reason,
    )


def _focus_point_rejection_reason(
    point: tuple[int, int],
    *,
    window_bounds: tuple[int, int, int, int],
    safe_region: VisualRect,
    avoid_rect: VisualRect | None = None,
) -> str | None:
    window = VisualRect(*window_bounds)
    if not window.contains_point(point):
        return "outside selected main Codex window bounds"
    if not safe_region.contains_point(point):
        return "outside safe composer band"
    if avoid_rect is not None and avoid_rect.contains_point(point):
        return "inside plus button bbox"
    return None


def _owner_reviewed_focus_candidate(
    raw: dict[str, object],
    window_bounds: tuple[int, int, int, int],
    safe_region: VisualRect,
    plus_bbox: tuple[int, int, int, int] | None = None,
) -> CodexFocusTargetCandidate | None:
    name = str(raw.get("name") or "owner_reviewed_focus_candidate")
    basis = str(raw.get("basis") or "main_window")
    avoid_rect = VisualRect(*plus_bbox) if plus_bbox is not None else None
    try:
        if basis == "plus_anchor":
            if plus_bbox is None:
                return None
            px, py, width, height = plus_bbox
            point = (
                int(px + width / 2 + float(raw.get("x_offset") or 0)),
                int(py + height / 2 - float(raw.get("y_offset") or 0)),
            )
        elif basis in {"composer_band", "safe_region"} and "x_ratio" in raw and "y_ratio" in raw:
            point = (
                int(safe_region.x + safe_region.width * float(raw["x_ratio"])),
                int(safe_region.y + safe_region.height * float(raw["y_ratio"])),
            )
        elif "x_ratio" in raw and "y_ratio" in raw:
            wx, wy, width, height = window_bounds
            point = (
                int(wx + width * float(raw["x_ratio"])),
                int(wy + height * float(raw["y_ratio"])),
            )
        elif "x" in raw and "y" in raw:
            point = (int(float(raw["x"])), int(float(raw["y"])))
        else:
            return None
    except (TypeError, ValueError):
        return None
    return _focus_candidate(
        name=f"owner_reviewed:{name}",
        family="owner_reviewed",
        point=point,
        source=f"owner_reviewed_focus_candidates:{basis}",
        window_bounds=window_bounds,
        safe_region=safe_region,
        avoid_rect=avoid_rect,
    )


def _dedupe_focus_candidates(
    candidates: list[CodexFocusTargetCandidate],
) -> list[CodexFocusTargetCandidate]:
    seen: set[tuple[int, int, str]] = set()
    deduped: list[CodexFocusTargetCandidate] = []
    for candidate in candidates:
        key = (candidate.click_point[0], candidate.click_point[1], candidate.family)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _focus_target_result_payload(result: CodexFocusTargetComparisonResult) -> dict[str, object]:
    return {
        "target_app": result.target_app,
        "marker_text": result.marker_text,
        "click_backend": result.click_backend,
        "codex_frontmost": result.codex_frontmost,
        "window_bounds": result.window_bounds,
        "placeholder_bbox": result.placeholder_bbox,
        "plus_button_bbox": result.plus_button_bbox,
        "safe_region_bounds": result.safe_region_bounds,
        "selected_candidate_name": result.selected_candidate_name,
        "selected_click_point": result.selected_click_point,
        "manual_cleanup_required": result.manual_cleanup_required,
        "stopped_reason": result.stopped_reason,
        "error": result.error,
        "candidates": [candidate.__dict__ for candidate in result.candidates],
        "attempts": [attempt.__dict__ for attempt in result.attempts],
    }


def _paste_test_result(
    *,
    target: ManualStageTarget,
    marker_text: str,
    visual: VisualDetectionResult,
    click_attempted: bool = False,
    click_succeeded: bool = False,
    paste_attempted: bool = False,
    paste_succeeded: bool = False,
    paste_variant_attempted: str | None = None,
    paste_variant_succeeded: bool | None = None,
    paste_variant_attempts: tuple[CodexPasteVariantResult, ...] = (),
    literal_v_detected: bool | None = None,
    final_paste_strategy: str | None = None,
    clipboard_length: int = 0,
    marker_detected: bool | None = None,
    marker_presence_detectable: bool = False,
    focused_element_summary: str = "unknown",
    focused_text_length_after_paste: int | None = None,
    cleanup_attempted: bool = False,
    cleanup_success: bool | None = None,
    manual_cleanup_required: bool = False,
    marker_visual: VisualMarkerPresenceResult | None = None,
    error: str | None = None,
    click_backend: str = "system_events",
    paste_backend: str = "system_events",
    pyautogui_available: bool | None = None,
    codex_frontmost_before_click: bool | None = None,
    codex_frontmost_after_click: bool | None = None,
) -> CodexPasteTestResult:
    return CodexPasteTestResult(
        target_app=target.app_name,
        marker_text=marker_text,
        visual_detection_backend_available=visual.backend_available,
        visual_screenshot_captured=visual.screenshot_captured,
        visual_plus_button_found=visual.plus_button_found,
        visual_plus_button_bbox=visual.plus_button_bbox,
        visual_plus_button_confidence=visual.plus_button_confidence,
        visual_selected_strategy=visual.selected_strategy,
        visual_click_point=visual.computed_click_point,
        visual_click_point_safe=visual.click_point_safe,
        click_backend=click_backend,
        pyautogui_available=pyautogui_available,
        codex_frontmost_before_click=codex_frontmost_before_click,
        codex_frontmost_after_click=codex_frontmost_after_click,
        click_attempted=click_attempted,
        click_succeeded=click_succeeded,
        paste_backend=paste_backend,
        paste_attempted=paste_attempted,
        paste_succeeded=paste_succeeded,
        paste_variant_attempted=paste_variant_attempted,
        paste_variant_succeeded=paste_variant_succeeded,
        paste_variant_attempts=paste_variant_attempts,
        literal_v_detected=literal_v_detected,
        final_paste_strategy=final_paste_strategy,
        clipboard_length=clipboard_length,
        marker_detected=marker_detected,
        marker_presence_detectable=marker_presence_detectable,
        focused_element_summary=focused_element_summary,
        focused_text_length_after_paste=focused_text_length_after_paste,
        cleanup_attempted=cleanup_attempted,
        cleanup_success=cleanup_success,
        manual_cleanup_required=manual_cleanup_required,
        marker_detection_backend=(
            marker_visual.marker_detection_backend if marker_visual else "unknown"
        ),
        marker_detection_available=(
            marker_visual.marker_detection_available if marker_visual else False
        ),
        visual_marker_found=marker_visual.marker_found if marker_visual else None,
        marker_confidence=marker_visual.marker_confidence if marker_visual else None,
        marker_search_region_bounds=(
            marker_visual.search_region_bounds if marker_visual else None
        ),
        marker_screenshot_path=marker_visual.screenshot_path if marker_visual else None,
        marker_annotated_screenshot_path=(
            marker_visual.annotated_screenshot_path if marker_visual else None
        ),
        marker_ocr_text_path=marker_visual.ocr_text_path if marker_visual else None,
        marker_match_text=marker_visual.marker_match_text if marker_visual else None,
        marker_detection_reason=marker_visual.detection_reason if marker_visual else None,
        marker_pytesseract_package_available=(
            marker_visual.pytesseract_package_available if marker_visual else None
        ),
        marker_tesseract_executable_available=(
            marker_visual.tesseract_executable_available if marker_visual else None
        ),
        marker_ocr_languages=marker_visual.ocr_languages if marker_visual else (),
        marker_english_ocr_available=marker_visual.english_ocr_available if marker_visual else None,
        marker_korean_ocr_available=marker_visual.korean_ocr_available if marker_visual else None,
        marker_detection_error=marker_visual.error if marker_visual else None,
        error=error,
    )


def _visual_detection_from_composer_state(
    state: CodexVisualComposerStateResult,
) -> VisualDetectionResult:
    return VisualDetectionResult(
        backend_available=state.placeholder_detection_backend_available,
        screenshot_captured=state.bounded_screenshot_captured,
        window_bounds=state.codex_window_bounds,
        safe_region_bounds=None,
        placeholder_detection_backend_available=state.placeholder_detection_backend_available,
        placeholder_detection_error=state.placeholder_error,
        placeholder_found=state.placeholder_visible is True,
        plus_button_found=state.plus_anchor_found,
        plus_button_confidence=state.plus_anchor_confidence,
        selected_strategy=state.selected_strategy,
        computed_click_point=state.plus_anchor_click_point,
        click_point_safe=state.plus_anchor_click_point is not None,
        error=state.error,
    )


def _prefer_placeholder_click_when_idle(
    visual: VisualDetectionResult,
    composer_state: CodexVisualComposerStateResult,
) -> VisualDetectionResult:
    if (
        composer_state.selected_strategy != "visual_placeholder_immediate"
        or not visual.placeholder_found
        or visual.placeholder_bbox is None
        or visual.window_bounds is None
    ):
        return visual
    x, y, width, height = visual.placeholder_bbox
    point = (int(x + width / 2), int(y + height / 2))
    if not _point_in_bounds(point, visual.window_bounds):
        return visual
    if visual.safe_region_bounds is not None and not _point_in_bounds(
        point,
        visual.safe_region_bounds,
    ):
        return visual
    return replace(
        visual,
        selected_strategy="visual_placeholder_anchor",
        computed_click_point=point,
        click_point_safe=True,
    )


def _point_in_bounds(
    point: tuple[int, int],
    bounds: tuple[int, int, int, int],
) -> bool:
    x, y, width, height = bounds
    return x <= point[0] <= x + width and y <= point[1] <= y + height


def _resolve_click_backend(target: ManualStageTarget, override: str | None = None) -> str:
    backend = override or target.visual_anchor_click_backend or target.click_backend
    normalized = backend.strip().lower().replace("-", "_")
    if normalized in {"system_events", "systemevents", "osascript"}:
        return "system_events"
    if normalized == "pyautogui":
        return "pyautogui"
    return normalized


def _resolve_paste_backend(target: ManualStageTarget, override: str | None = None) -> str:
    backend = override or target.paste_backend
    normalized = backend.strip().lower().replace("-", "_")
    if normalized in {"system_events", "systemevents", "osascript"}:
        return "system_events"
    if normalized == "pyautogui":
        return "pyautogui"
    return normalized


def _with_click_backend(
    result: LocalAgentFocusResult,
    click_backend: str,
    pyautogui_available: bool | None,
) -> LocalAgentFocusResult:
    return LocalAgentFocusResult(
        active_app_before=result.active_app_before,
        active_app_after=result.active_app_after,
        app_frontmost=result.app_frontmost,
        input_candidate_count=result.input_candidate_count,
        selected_input_candidate_summary=result.selected_input_candidate_summary,
        input_text_length_before_paste=result.input_text_length_before_paste,
        focused_element_summary=result.focused_element_summary,
        window_bounds=result.window_bounds,
        fallback_click_point=result.fallback_click_point,
        plus_button_bbox=result.plus_button_bbox,
        plus_button_center=result.plus_button_center,
        direct_plus_anchor_x_offset=result.direct_plus_anchor_x_offset,
        direct_plus_anchor_y_offset=result.direct_plus_anchor_y_offset,
        click_backend=click_backend,
        pyautogui_available=pyautogui_available,
        succeeded=result.succeeded,
        used_fallback=result.used_fallback,
        error=result.error,
    )


def _parse_ui_tree_output(output: str, *, target_app: str) -> CodexUITreeDump:
    lines = output.splitlines()
    active_app = lines[0].strip() if lines else None
    if len(lines) > 1 and lines[1].startswith("ERROR\t"):
        return CodexUITreeDump(
            active_app=active_app,
            target_app=target_app,
            elements=(),
            raw_text=output,
            accessibility_available=False,
            error=lines[1].split("\t", 1)[1],
        )
    elements: list[CodexUIElement] = []
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        try:
            depth = int(parts[0])
        except ValueError:
            continue
        padded = parts + [""] * (6 - len(parts))
        elements.append(
            CodexUIElement(
                depth=depth,
                role=padded[1],
                subrole=padded[2],
                title=padded[3],
                description=padded[4],
                value=padded[5][:500],
            )
        )
    return CodexUITreeDump(
        active_app=active_app,
        target_app=target_app,
        elements=tuple(elements),
        raw_text=output,
        accessibility_available=True,
    )


def format_codex_ui_tree_dump(dump: CodexUITreeDump) -> str:
    lines = [
        "# Codex UI Tree Dump",
        "",
        f"Target app: {dump.target_app}",
        f"Active app: {dump.active_app or 'unknown'}",
        f"Accessibility available: {'yes' if dump.accessibility_available else 'no'}",
        f"Element count: {len(dump.elements)}",
    ]
    if dump.error:
        lines.extend(["", f"Error: {dump.error}"])
    if dump.elements:
        lines.extend(["", "## Elements"])
        for element in dump.elements:
            indent = "  " * element.depth
            lines.append(f"{indent}- {element.summary}")
    return "\n".join(lines)


def _yes_no_unknown(value: bool | None) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def format_codex_input_target_diagnostic(diagnostic: CodexInputTargetDiagnostic) -> str:
    lines = [
        "# Codex Input Target Diagnostic",
        "",
        f"Target app: {diagnostic.target_app}",
        f"Active app: {diagnostic.active_app or 'unknown'}",
        f"Codex app active: {'yes' if diagnostic.codex_app_active else 'no'}",
        f"Accessibility available: {'yes' if diagnostic.accessibility_available else 'no'}",
        f"Detected Codex windows: {diagnostic.detected_window_count}",
        f"Window selection strategy: {diagnostic.window_selection_strategy}",
        f"Selected window title: {diagnostic.selected_window_title or 'unavailable'}",
        f"Window bounds: {diagnostic.window_bounds or 'unknown'}",
        f"Window selection error: {diagnostic.window_selection_error or 'none'}",
        f"Accessibility candidate count: {diagnostic.input_candidate_count}",
        f"Best candidate: {diagnostic.best_candidate_summary}",
        f"Fallback strategy: {diagnostic.fallback_strategy or 'disabled'}",
        f"Fallback enabled: {'yes' if diagnostic.fallback_enabled else 'no'}",
        f"Fallback click point: {diagnostic.fallback_click_point or 'unavailable'}",
        f"Placeholder found: {'yes' if diagnostic.placeholder_found else 'no'}",
        f"Placeholder bbox: {diagnostic.placeholder_bbox or 'unavailable'}",
        f"Plus button found: {'yes' if diagnostic.plus_button_found else 'no'}",
        f"Plus button bbox: {diagnostic.plus_button_bbox or 'unavailable'}",
        f"Plus anchor click point: {diagnostic.plus_anchor_click_point or 'unavailable'}",
        (
            "Visual detection backend available: "
            + ("yes" if diagnostic.visual_detection_backend_available else "no")
        ),
        f"Visual screenshot captured: {'yes' if diagnostic.visual_screenshot_captured else 'no'}",
        f"Visual plus button found: {'yes' if diagnostic.visual_plus_button_found else 'no'}",
        f"Visual plus button bbox: {diagnostic.visual_plus_button_bbox or 'unavailable'}",
        (
            "Visual plus button confidence: "
            + (
                f"{diagnostic.visual_plus_button_confidence:.3f}"
                if diagnostic.visual_plus_button_confidence is not None
                else "unavailable"
            )
        ),
        f"Visual plus template path: {diagnostic.visual_plus_template_path or 'unavailable'}",
        f"Visual plus template size: {diagnostic.visual_plus_template_size or 'unavailable'}",
        (
            "Visual plus best match bbox: "
            f"{diagnostic.visual_plus_best_match_bbox or 'unavailable'}"
        ),
        (
            "Visual plus best match confidence: "
            + (
                f"{diagnostic.visual_plus_best_match_confidence:.3f}"
                if diagnostic.visual_plus_best_match_confidence is not None
                else "unavailable"
            )
        ),
        (
            "Visual plus confidence threshold: "
            + (
                f"{diagnostic.visual_plus_confidence_threshold:.3f}"
                if diagnostic.visual_plus_confidence_threshold is not None
                else "unavailable"
            )
        ),
        (
            "Visual plus multiscale enabled: "
            + _yes_no_unknown(diagnostic.visual_plus_multiscale_enabled)
        ),
        (
            "Visual plus search region bounds: "
            f"{diagnostic.visual_plus_search_region_bounds or 'unavailable'}"
        ),
        f"Visual plus match error: {diagnostic.visual_plus_match_error or 'none'}",
        f"Visual placeholder bbox: {diagnostic.visual_placeholder_bbox or 'unavailable'}",
        f"Visual placeholder target text: {diagnostic.visual_placeholder_target_text or 'unavailable'}",
        (
            "Visual placeholder backend available: "
            + ("yes" if diagnostic.visual_placeholder_detection_backend_available else "no")
        ),
        (
            "Visual placeholder OCR available: "
            + ("yes" if diagnostic.visual_placeholder_detection_backend_available else "no")
        ),
        (
            "Visual placeholder found: "
            + _yes_no_unknown(
                True
                if diagnostic.visual_placeholder_found
                else (
                    False
                    if diagnostic.visual_placeholder_detection_backend_available
                    else None
                )
            )
        ),
        f"Visual placeholder match text: {diagnostic.visual_placeholder_match_text or 'unavailable'}",
        (
            "Visual placeholder OCR confidence: "
            + (
                f"{diagnostic.visual_placeholder_ocr_confidence:.3f}"
                if diagnostic.visual_placeholder_ocr_confidence is not None
                else "unavailable"
            )
        ),
        f"Visual placeholder OCR text: {diagnostic.visual_placeholder_ocr_text_path or 'not written'}",
        (
            "Visual placeholder search region: "
            f"{diagnostic.visual_placeholder_search_region_bounds or 'unavailable'}"
        ),
        (
            "Visual placeholder detection reason: "
            + (diagnostic.visual_placeholder_detection_reason or "none")
        ),
        (
            "Visual placeholder backend error: "
            + (diagnostic.visual_placeholder_detection_error or "none")
        ),
        f"Visual OCR backend: {diagnostic.visual_ocr_backend}",
        (
            "pytesseract Python package available: "
            + _yes_no_unknown(diagnostic.visual_pytesseract_package_available)
        ),
        (
            "tesseract executable available: "
            + _yes_no_unknown(diagnostic.visual_tesseract_executable_available)
        ),
        (
            "OCR languages: "
            + (", ".join(diagnostic.visual_ocr_languages) if diagnostic.visual_ocr_languages else "unknown")
        ),
        (
            "English OCR support: "
            + _yes_no_unknown(diagnostic.visual_english_ocr_available)
        ),
        (
            "Korean OCR support: "
            + _yes_no_unknown(diagnostic.visual_korean_ocr_available)
        ),
        f"Visual selected strategy: {diagnostic.visual_selected_strategy}",
        f"Visual click point: {diagnostic.visual_click_point or 'unavailable'}",
        f"Visual safe region bounds: {diagnostic.visual_safe_region_bounds or 'unavailable'}",
        f"Visual click point safe: {'yes' if diagnostic.visual_click_point_safe else 'no'}",
        f"Focus strategy: {diagnostic.focus_strategy or 'default'}",
        (
            "Direct plus-anchor enabled: "
            + ("yes" if diagnostic.direct_plus_anchor_enabled else "no")
        ),
        f"Direct plus-anchor click point: {diagnostic.direct_plus_anchor_click_point or 'unavailable'}",
        (
            "Direct plus-anchor click point safe: "
            + ("yes" if diagnostic.direct_plus_anchor_click_point_safe else "no")
        ),
        (
            "Direct plus-anchor offset: "
            f"{diagnostic.direct_plus_anchor_x_offset}, "
            f"-{diagnostic.direct_plus_anchor_y_offset}"
        ),
        (
            "Direct plus-anchor y offset candidates: "
            + (
                ", ".join(str(value) for value in diagnostic.direct_plus_anchor_y_offset_candidates)
                if diagnostic.direct_plus_anchor_y_offset_candidates
                else "none"
            )
        ),
        f"Visual fallback would be used: {'yes' if diagnostic.visual_fallback_would_be_used else 'no'}",
        f"Visual debug image: {diagnostic.visual_debug_image_path or 'not written'}",
        f"Visual annotated image: {diagnostic.visual_annotated_image_path or 'not written'}",
        f"Visual error: {diagnostic.visual_error or 'none'}",
        f"Idle-empty wait timeout: {diagnostic.idle_empty_wait_timeout_seconds}s",
        f"Idle-empty poll interval: {diagnostic.idle_empty_poll_interval_seconds}s",
        (
            "Dedicated automation session: "
            + ("yes" if diagnostic.dedicated_automation_session else "no")
        ),
        (
            "Allow overwrite after idle timeout: "
            + ("yes" if diagnostic.allow_overwrite_after_idle_timeout else "no")
        ),
        f"Stop on idle timeout: {'yes' if diagnostic.stop_on_idle_timeout else 'no'}",
        f"Effective timeout policy: {diagnostic.effective_timeout_policy}",
        f"Overwrite would be allowed: {'yes' if diagnostic.overwrite_allowed else 'no'}",
        f"Composer policy mode: {diagnostic.composer_policy_mode}",
        (
            "Busy placeholder wait timeout: "
            f"{diagnostic.busy_placeholder_wait_timeout_seconds}s"
        ),
        (
            "Busy placeholder poll interval: "
            f"{diagnostic.busy_placeholder_poll_interval_seconds}s"
        ),
        f"On busy timeout: {diagnostic.on_busy_timeout}",
        (
            "Prompt presence verification possible: "
            + ("yes" if diagnostic.prompt_presence_verifiable else "no")
        ),
        f"Live submit allowed: {'yes' if diagnostic.live_submit_allowed else 'no'}",
    ]
    if diagnostic.limitation:
        lines.extend(["", "## Limitation", diagnostic.limitation])
    if diagnostic.rejected_window_summaries:
        lines.extend(["", "## Rejected Windows"])
        lines.extend(f"- {summary}" for summary in diagnostic.rejected_window_summaries)
    lines.extend(["", "No paste, submit, Enter/Return, GitHub, or Gmail action was attempted."])
    return "\n".join(lines)


def format_codex_window_selection(result: CodexWindowSelectionResult) -> str:
    lines = [
        f"# {result.target_app} Window Diagnostic",
        "",
        f"Target app: {result.target_app}",
        f"Selection strategy: {result.strategy}",
        (
            "Minimum main window: "
            f"width>={result.min_width}, height>={result.min_height}, area>={result.min_area}"
        ),
        f"Window count: {len(result.windows)}",
        f"Selected bounds: {result.selected_bounds or 'unavailable'}",
        f"Plausible composer window: {'yes' if result.plausible else 'no'}",
        f"Error: {result.error or 'none'}",
        "",
        "## Windows",
    ]
    if not result.windows:
        lines.append(f"No {result.target_app} windows were reported.")
    for window in result.windows:
        if window.selected:
            status = "selected"
        elif window.rejected:
            status = "rejected"
        else:
            status = "candidate"
        reasons = ", ".join(window.rejection_reasons) if window.rejection_reasons else "none"
        lines.append(
            "- "
            f"#{window.index}: status={status}, title={window.title or '(untitled)'}, "
            f"bounds={window.bounds or 'unknown'}, area={window.area}, "
            f"visible={_yes_no_unknown(window.visible)}, "
            f"minimized={_yes_no_unknown(window.minimized)}, "
            f"fullscreen={_yes_no_unknown(window.fullscreen)}, "
            f"role={window.role or 'unknown'}, subrole={window.subrole or 'unknown'}, "
            f"rejection_reasons={reasons}"
        )
    lines.extend(["", "No click, paste, submit, Enter/Return, GitHub, or Gmail action was attempted."])
    return "\n".join(lines)


def format_codex_paste_test_result(result: CodexPasteTestResult) -> str:
    marker_state = "unknown"
    if result.marker_detected is True:
        marker_state = "yes"
    elif result.marker_detected is False:
        marker_state = "no"
    cleanup_state = "unknown"
    if result.cleanup_success is True:
        cleanup_state = "yes"
    elif result.cleanup_success is False:
        cleanup_state = "no"
    lines = [
        "# Codex Paste-Test Diagnostic",
        "",
        f"Target app: {result.target_app}",
        f"Visual backend available: {'yes' if result.visual_detection_backend_available else 'no'}",
        f"Screenshot captured: {'yes' if result.visual_screenshot_captured else 'no'}",
        f"Plus button found: {'yes' if result.visual_plus_button_found else 'no'}",
        f"Plus button bbox: {result.visual_plus_button_bbox or 'unavailable'}",
        (
            "Plus button confidence: "
            + (
                f"{result.visual_plus_button_confidence:.3f}"
                if result.visual_plus_button_confidence is not None
                else "unavailable"
            )
        ),
        f"Selected strategy: {result.visual_selected_strategy}",
        f"Computed click point: {result.visual_click_point or 'unavailable'}",
        f"Click point safe: {'yes' if result.visual_click_point_safe else 'no'}",
        f"Click backend: {result.click_backend}",
        "PyAutoGUI available: " + _yes_no_unknown(result.pyautogui_available),
        (
            "Codex frontmost before click: "
            + _yes_no_unknown(result.codex_frontmost_before_click)
        ),
        (
            "Codex frontmost after click: "
            + _yes_no_unknown(result.codex_frontmost_after_click)
        ),
        f"Click attempted: {'yes' if result.click_attempted else 'no'}",
        f"Click succeeded: {'yes' if result.click_succeeded else 'no'}",
        f"Paste backend: {result.paste_backend}",
        f"Paste attempted: {'yes' if result.paste_attempted else 'no'}",
        f"Paste succeeded: {'yes' if result.paste_succeeded else 'no'}",
        f"Paste variant attempted: {result.paste_variant_attempted or 'none'}",
        (
            "Paste variant succeeded: "
            + _yes_no_unknown(result.paste_variant_succeeded)
        ),
        "Literal-v appeared: " + _yes_no_unknown(result.literal_v_detected),
        f"Final paste strategy: {result.final_paste_strategy or 'none'}",
        f"Clipboard length: {result.clipboard_length}",
        f"Marker text used: {result.marker_text}",
        f"Marker detection backend: {result.marker_detection_backend}",
        (
            "Marker detection available: "
            + ("yes" if result.marker_detection_available else "no")
        ),
        (
            "Visual marker found: "
            + (
                "yes"
                if result.visual_marker_found is True
                else ("no" if result.visual_marker_found is False else "unknown")
            )
        ),
        (
            "Marker confidence: "
            + (
                f"{result.marker_confidence:.3f}"
                if result.marker_confidence is not None
                else "unavailable"
            )
        ),
        f"Marker search region bounds: {result.marker_search_region_bounds or 'unavailable'}",
        f"Marker screenshot: {result.marker_screenshot_path or 'not written'}",
        (
            "Marker annotated screenshot: "
            + (result.marker_annotated_screenshot_path or "not written")
        ),
        f"Marker OCR text: {result.marker_ocr_text_path or 'not written'}",
        f"Marker match text: {result.marker_match_text or 'unavailable'}",
        f"Marker detection reason: {result.marker_detection_reason or 'none'}",
        (
            "Marker pytesseract Python package available: "
            + _yes_no_unknown(result.marker_pytesseract_package_available)
        ),
        (
            "Marker tesseract executable available: "
            + _yes_no_unknown(result.marker_tesseract_executable_available)
        ),
        (
            "Marker OCR languages: "
            + (", ".join(result.marker_ocr_languages) if result.marker_ocr_languages else "unknown")
        ),
        (
            "Marker English OCR support: "
            + _yes_no_unknown(result.marker_english_ocr_available)
        ),
        (
            "Marker Korean OCR support: "
            + _yes_no_unknown(result.marker_korean_ocr_available)
        ),
        f"Marker detection error: {result.marker_detection_error or 'none'}",
        f"Marker presence detectable: {'yes' if result.marker_presence_detectable else 'no'}",
        f"Marker detected: {marker_state}",
        f"Focused element after paste: {result.focused_element_summary}",
        (
            "Focused text length after paste: "
            + (
                str(result.focused_text_length_after_paste)
                if result.focused_text_length_after_paste is not None
                else "unknown"
            )
        ),
        f"Cleanup attempted: {'yes' if result.cleanup_attempted else 'no'}",
        f"Cleanup success: {cleanup_state}",
    ]
    if result.paste_variant_attempts:
        lines.extend(["", "## Paste Variant Attempts"])
        for attempt in result.paste_variant_attempts:
            marker_found = (
                "yes"
                if attempt.marker_found is True
                else ("no" if attempt.marker_found is False else "unknown")
            )
            lines.append(
                "- "
                f"{attempt.variant_name}: "
                f"attempted={'yes' if attempt.attempted else 'no'}, "
                f"marker_found={marker_found}, "
                f"literal_v={_yes_no_unknown(attempt.literal_v_detected)}, "
                f"cleanup_attempted={'yes' if attempt.cleanup_attempted else 'no'}, "
                f"error={attempt.paste_error or 'none'}"
            )
    if result.error:
        lines.extend(["", f"Error: {result.error}"])
        if remediation := accessibility_denied_remediation_message(result.error):
            lines.extend(["", remediation])
    if result.manual_cleanup_required:
        lines.extend(
            [
                "",
                "Please clear the Codex composer manually if the marker is visible.",
            ]
        )
    lines.extend(["", "No submit, Enter/Return, local-agent command, GitHub, or Gmail action was attempted."])
    return "\n".join(lines)


def format_codex_focus_target_comparison(result: CodexFocusTargetComparisonResult) -> str:
    lines = [
        "# Codex Focus Target Comparison",
        "",
        f"Target app: {result.target_app}",
        f"Marker text: {result.marker_text}",
        f"Click backend: {result.click_backend}",
        f"Codex frontmost: {'yes' if result.codex_frontmost else 'no'}",
        f"Window bounds: {result.window_bounds or 'unavailable'}",
        f"Visual backend available: {'yes' if result.visual_backend_available else 'no'}",
        f"Screenshot captured: {'yes' if result.visual_screenshot_captured else 'no'}",
        f"Placeholder bbox: {result.placeholder_bbox or 'unavailable'}",
        f"Plus button bbox: {result.plus_button_bbox or 'unavailable'}",
        f"Safe region bounds: {result.safe_region_bounds or 'unavailable'}",
        f"Selected candidate: {result.selected_candidate_name or 'none'}",
        f"Selected click point: {result.selected_click_point or 'unavailable'}",
        f"Comparison screenshot: {result.comparison_image_path or 'not written'}",
        (
            "Comparison annotated screenshot: "
            + (result.comparison_annotated_image_path or "not written")
        ),
        f"Comparison OCR text: {result.comparison_ocr_text_path or 'not written'}",
        f"Comparison JSON: {result.comparison_json_path or 'not written'}",
    ]
    if result.error:
        lines.extend(["", f"Error: {result.error}"])
    lines.extend(["", "## Candidate Targets"])
    for candidate in result.candidates:
        lines.append(
            "- "
            f"{candidate.name}: family={candidate.family}, "
            f"point={candidate.click_point}, "
            f"safe={'yes' if candidate.safe else 'no'}, "
            f"source={candidate.source}, "
            f"rejection={candidate.rejection_reason or 'none'}"
        )
    lines.extend(["", "## Candidate Attempts"])
    if not result.attempts:
        lines.append("No candidate was attempted.")
    for attempt in result.attempts:
        found = (
            "yes"
            if attempt.marker_found is True
            else ("no" if attempt.marker_found is False else "unknown")
        )
        cleanup = (
            "yes"
            if attempt.cleanup_success is True
            else ("no" if attempt.cleanup_success is False else "unknown")
        )
        lines.append(
            "- "
            f"{attempt.candidate_name}: "
            f"point={attempt.click_point}, "
            f"click_attempted={'yes' if attempt.click_attempted else 'no'}, "
            f"typed_marker_attempted={'yes' if attempt.typed_marker_attempted else 'no'}, "
            f"ocr_marker_found={found}, "
            f"confidence={attempt.marker_confidence if attempt.marker_confidence is not None else 'unavailable'}, "
            f"cleanup_attempted={'yes' if attempt.cleanup_attempted else 'no'}, "
            f"cleanup={cleanup}, "
            f"error={attempt.error or 'none'}"
        )
    if result.stopped_reason:
        lines.extend(["", f"Stopped reason: {result.stopped_reason}"])
    if result.manual_cleanup_required:
        lines.extend(["", "Please clear the Codex composer manually if the marker is visible."])
    lines.extend(["", "No submit, Enter/Return, local-agent command, GitHub, or Gmail action was attempted."])
    return "\n".join(lines)


def _running_state_detected(text: str) -> bool:
    normalized = text.lower()
    markers = (
        "stop",
        "cancel",
        "running",
        "responding",
        "thinking",
        "working",
        "중지",
        "취소",
        "실행",
        "응답",
        "생각",
    )
    return any(marker in normalized for marker in markers)


def format_codex_ui_diagnostic(diagnostic: CodexUIDiagnostic) -> str:
    lines = [
        "# Codex UI Diagnostic",
        "",
        f"Target app: {diagnostic.target_app}",
        f"Active app: {diagnostic.active_app or 'unknown'}",
        f"Codex app active: {'yes' if diagnostic.codex_app_active else 'no'}",
        f"Accessibility available: {'yes' if diagnostic.accessibility_available else 'no'}",
        f"Focused element: {diagnostic.focused_element_summary}",
        f"Input field detectable: {'yes' if diagnostic.input_field_detectable else 'no'}",
        (
            "Input candidate count: "
            + (
                str(diagnostic.input_candidate_count)
                if diagnostic.input_candidate_count is not None
                else "unknown"
            )
        ),
        f"Selected input candidate: {diagnostic.selected_input_candidate_summary}",
        f"Focused text length: {diagnostic.focused_text_length if diagnostic.focused_text_length is not None else 'unknown'}",
        f"Conversation elements detectable: {'yes' if diagnostic.conversation_elements_detectable else 'no'}",
        (
            "Running/responding indicator detectable: "
            + (
                "unknown"
                if diagnostic.running_state_detected is None
                else ("yes" if diagnostic.running_state_detected else "no")
            )
        ),
    ]
    if diagnostic.limitation:
        lines.extend(["", "## Limitation", diagnostic.limitation])
    lines.extend(["", "No paste, submit, Enter/Return, GitHub, or Gmail action was attempted."])
    return "\n".join(lines)
