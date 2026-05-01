from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from agent_bridge.gui.asset_state_machine import (
    AssetVisualStateDetector,
    VisualGuiState,
    VisualStateDetection,
    asset_profile_for_target,
)
from agent_bridge.gui.macos_apps import ManualStageTarget


@dataclass(frozen=True)
class ChatGPTMacComposerTextStateDiagnosticResult:
    target_app: str
    window_bounds: tuple[int, int, int, int] | None
    marker: str
    initial_state: VisualGuiState | None
    after_type_state: VisualGuiState | None = None
    after_cleanup_state: VisualGuiState | None = None
    click_point: tuple[int, int] | None = None
    click_point_safe: bool = False
    click_attempted: bool = False
    typed_marker_attempted: bool = False
    cleanup_attempted: bool = False
    cleanup_succeeded: bool | None = None
    composer_has_text_detected: bool = False
    idle_after_cleanup_detected: bool = False
    submit_attempted: bool = False
    enter_or_return_pressed: bool = False
    initial_detection: VisualStateDetection | None = None
    after_type_detection: VisualStateDetection | None = None
    after_cleanup_detection: VisualStateDetection | None = None
    error: str | None = None


Clicker = Callable[[int, int], None]
Typer = Callable[[str], None]
KeyPresser = Callable[[str], None]


def diagnose_chatgpt_mac_composer_text_state(
    *,
    target: ManualStageTarget,
    window_bounds: tuple[int, int, int, int] | None,
    logs_dir: Path | None = None,
    marker: str = "x",
    detector: AssetVisualStateDetector | None = None,
    clicker: Clicker | None = None,
    typer: Typer | None = None,
    key_presser: KeyPresser | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> ChatGPTMacComposerTextStateDiagnosticResult:
    detector = detector or AssetVisualStateDetector()
    if window_bounds is None:
        return ChatGPTMacComposerTextStateDiagnosticResult(
            target_app=target.app_name,
            window_bounds=None,
            marker=marker,
            initial_state=None,
            error=f"{target.app_name} window bounds were unavailable.",
        )
    if len(marker) != 1:
        return ChatGPTMacComposerTextStateDiagnosticResult(
            target_app=target.app_name,
            window_bounds=window_bounds,
            marker=marker,
            initial_state=None,
            error="ChatGPT Mac composer text diagnostic marker must be exactly one character.",
        )

    profile = asset_profile_for_target(target)
    initial = detector.detect(
        target=target,
        window_bounds=window_bounds,
        profile=profile,
        logs_dir=logs_dir,
        write_debug=True,
    )
    if not initial.plus_anchor_found or not initial.composer_click_point_safe:
        return ChatGPTMacComposerTextStateDiagnosticResult(
            target_app=target.app_name,
            window_bounds=window_bounds,
            marker=marker,
            initial_state=initial.matched_state,
            click_point=initial.computed_composer_click_point,
            click_point_safe=initial.composer_click_point_safe,
            initial_detection=initial,
            error="ChatGPT Mac composer anchor was not available or not safe.",
        )

    click_point = initial.computed_composer_click_point
    assert click_point is not None
    try:
        _click(click_point[0], click_point[1], clicker=clicker)
        sleep_fn(0.1)
        _type_marker(marker, typer=typer)
        sleep_fn(0.2)
    except Exception as error:
        return ChatGPTMacComposerTextStateDiagnosticResult(
            target_app=target.app_name,
            window_bounds=window_bounds,
            marker=marker,
            initial_state=initial.matched_state,
            click_point=click_point,
            click_point_safe=True,
            click_attempted=True,
            typed_marker_attempted=True,
            initial_detection=initial,
            error=f"ChatGPT Mac one-character composer diagnostic failed: {error}",
        )

    after_type = detector.detect(
        target=target,
        window_bounds=window_bounds,
        profile=profile,
        logs_dir=logs_dir,
        write_debug=True,
    )
    cleanup_attempted = False
    cleanup_error: str | None = None
    try:
        cleanup_attempted = True
        _press_key("backspace", key_presser=key_presser)
        sleep_fn(0.2)
    except Exception as error:
        cleanup_error = f"Backspace cleanup failed: {error}"

    after_cleanup = detector.detect(
        target=target,
        window_bounds=window_bounds,
        profile=profile,
        logs_dir=logs_dir,
        write_debug=True,
    )
    composer_has_text = after_type.matched_state == VisualGuiState.COMPOSER_HAS_TEXT
    idle_after_cleanup = after_cleanup.matched_state == VisualGuiState.IDLE
    cleanup_succeeded = None if cleanup_error else idle_after_cleanup
    error = cleanup_error
    if not composer_has_text and error is None:
        error = "ChatGPT Mac did not enter COMPOSER_HAS_TEXT after one-character diagnostic."
    return ChatGPTMacComposerTextStateDiagnosticResult(
        target_app=target.app_name,
        window_bounds=window_bounds,
        marker=marker,
        initial_state=initial.matched_state,
        after_type_state=after_type.matched_state,
        after_cleanup_state=after_cleanup.matched_state,
        click_point=click_point,
        click_point_safe=True,
        click_attempted=True,
        typed_marker_attempted=True,
        cleanup_attempted=cleanup_attempted,
        cleanup_succeeded=cleanup_succeeded,
        composer_has_text_detected=composer_has_text,
        idle_after_cleanup_detected=idle_after_cleanup,
        initial_detection=initial,
        after_type_detection=after_type,
        after_cleanup_detection=after_cleanup,
        error=error,
    )


def _click(x: int, y: int, *, clicker: Clicker | None) -> None:
    if clicker is not None:
        clicker(x, y)
        return
    import pyautogui  # type: ignore[import-not-found]

    pyautogui.click(x, y)


def _type_marker(marker: str, *, typer: Typer | None) -> None:
    if typer is not None:
        typer(marker)
        return
    import pyautogui  # type: ignore[import-not-found]

    pyautogui.write(marker, interval=0.001)


def _press_key(key: str, *, key_presser: KeyPresser | None) -> None:
    if key_presser is not None:
        key_presser(key)
        return
    import pyautogui  # type: ignore[import-not-found]

    pyautogui.press(key)


def format_chatgpt_mac_composer_text_state(
    result: ChatGPTMacComposerTextStateDiagnosticResult,
) -> str:
    return "\n".join(
        [
            "# ChatGPT Mac Composer Text-State Diagnostic",
            "",
            f"Target app: {result.target_app}",
            f"Window bounds: {result.window_bounds or 'unavailable'}",
            f"Marker: {result.marker}",
            f"Initial state: {result.initial_state.value if result.initial_state else 'unavailable'}",
            (
                "After type state: "
                + (result.after_type_state.value if result.after_type_state else "unavailable")
            ),
            (
                "After cleanup state: "
                + (
                    result.after_cleanup_state.value
                    if result.after_cleanup_state
                    else "unavailable"
                )
            ),
            f"Click point: {result.click_point or 'unavailable'}",
            f"Click point safe: {'yes' if result.click_point_safe else 'no'}",
            f"Click attempted: {'yes' if result.click_attempted else 'no'}",
            f"Typed marker attempted: {'yes' if result.typed_marker_attempted else 'no'}",
            (
                "COMPOSER_HAS_TEXT detected: "
                + ("yes" if result.composer_has_text_detected else "no")
            ),
            f"Cleanup attempted: {'yes' if result.cleanup_attempted else 'no'}",
            (
                "Cleanup succeeded: "
                + (
                    "unknown"
                    if result.cleanup_succeeded is None
                    else ("yes" if result.cleanup_succeeded else "no")
                )
            ),
            (
                "IDLE after cleanup detected: "
                + ("yes" if result.idle_after_cleanup_detected else "no")
            ),
            f"Submit attempted: {'yes' if result.submit_attempted else 'no'}",
            (
                "Enter/Return pressed: "
                + ("yes" if result.enter_or_return_pressed else "no")
            ),
            f"Error: {result.error or 'none'}",
        ]
    )
