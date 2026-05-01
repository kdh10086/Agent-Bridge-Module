from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_bridge.gui.asset_state_machine import VisualGuiState, VisualStateDetection
from agent_bridge.gui.chatgpt_mac_composer import (
    diagnose_chatgpt_mac_composer_text_state,
)
from agent_bridge.gui.macos_apps import ManualStageTarget


WINDOW = (59, 37, 1594, 1070)


def target() -> ManualStageTarget:
    return ManualStageTarget(
        app_name="ChatGPT",
        app_path="/Applications/ChatGPT.app",
        bundle_id="com.openai.chat",
        backend="chatgpt_mac_visual",
        visual_asset_profile="chatgpt_mac",
        focus_strategy="visual_plus_anchor",
    )


def detection(
    state: VisualGuiState,
    *,
    plus_found: bool = True,
    click_safe: bool = True,
) -> VisualStateDetection:
    return VisualStateDetection(
        selected_app="ChatGPT",
        asset_profile="chatgpt_mac",
        window_bounds=WINDOW,
        safe_region_bounds=(90, 679, 1530, 428),
        screenshot_captured=True,
        backend_available=True,
        matched_state=state,
        plus_search_region_bounds=(377, 1000, 398, 107),
        plus_anchor_found=plus_found,
        plus_anchor_bbox=(509, 1059, 28, 27) if plus_found else None,
        plus_anchor_confidence=1.0 if plus_found else None,
        computed_composer_click_point=(523, 1002) if plus_found else None,
        composer_click_point_safe=click_safe,
    )


def detector_with(sequence: list[VisualStateDetection]):
    def detect(**_kwargs):
        return sequence.pop(0)

    return SimpleNamespace(detect=detect)


def test_chatgpt_mac_composer_text_diagnostic_types_one_character_and_cleans_up():
    clicks: list[tuple[int, int]] = []
    typed: list[str] = []
    keys: list[str] = []

    result = diagnose_chatgpt_mac_composer_text_state(
        target=target(),
        window_bounds=WINDOW,
        logs_dir=Path("/tmp"),
        detector=detector_with(
            [
                detection(VisualGuiState.IDLE),
                detection(VisualGuiState.COMPOSER_HAS_TEXT),
                detection(VisualGuiState.IDLE),
            ]
        ),
        clicker=lambda x, y: clicks.append((x, y)),
        typer=lambda text: typed.append(text),
        key_presser=lambda key: keys.append(key),
        sleep_fn=lambda _seconds: None,
    )

    assert result.composer_has_text_detected
    assert result.idle_after_cleanup_detected
    assert result.submit_attempted is False
    assert result.enter_or_return_pressed is False
    assert clicks == [(523, 1002)]
    assert typed == ["x"]
    assert keys == ["backspace"]


def test_chatgpt_mac_composer_text_diagnostic_refuses_without_safe_plus_anchor():
    clicks: list[tuple[int, int]] = []
    typed: list[str] = []
    keys: list[str] = []

    result = diagnose_chatgpt_mac_composer_text_state(
        target=target(),
        window_bounds=WINDOW,
        detector=detector_with([detection(VisualGuiState.IDLE, plus_found=False)]),
        clicker=lambda x, y: clicks.append((x, y)),
        typer=lambda text: typed.append(text),
        key_presser=lambda key: keys.append(key),
        sleep_fn=lambda _seconds: None,
    )

    assert not result.click_attempted
    assert not result.typed_marker_attempted
    assert not result.cleanup_attempted
    assert result.error == "ChatGPT Mac composer anchor was not available or not safe."
    assert clicks == []
    assert typed == []
    assert keys == []
