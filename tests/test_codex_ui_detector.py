from __future__ import annotations

import builtins
import subprocess
from dataclasses import replace

from agent_bridge.gui.codex_ui_detector import (
    CODEX_COMPOSER_IDLE_EMPTY,
    CODEX_COMPOSER_IDLE_WAIT_TIMEOUT,
    CODEX_PASTE_TEST_MARKER,
    CodexUIDetector,
    LocalAgentFocusResult,
    format_codex_window_selection,
    format_codex_paste_test_result,
    format_codex_input_target_diagnostic,
    format_codex_ui_diagnostic,
)
from agent_bridge.gui.macos_apps import AppActivator, ManualStageTarget
from agent_bridge.gui.visual_detector import VisualDetectionResult, VisualMarkerPresenceResult


class SequenceRunner:
    def __init__(self, outputs: list[tuple[int, str, str]]):
        self.outputs = outputs
        self.commands: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.commands.append(list(command))
        returncode, stdout, stderr = self.outputs.pop(0)
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)


class FakeClipboard:
    def __init__(self) -> None:
        self.text = ""

    def copy_text(self, text: str) -> None:
        self.text = text

    def read_text(self) -> str:
        return self.text


class FakeAppActivator(AppActivator):
    def activate(
        self,
        app_name: str,
        *,
        app_path: str | None = None,
        bundle_id: str | None = None,
    ) -> None:
        del app_name, app_path, bundle_id


class FakeVisualDetector:
    def __init__(
        self,
        result: VisualDetectionResult | list[VisualDetectionResult],
        marker_result: VisualMarkerPresenceResult | list[VisualMarkerPresenceResult] | None = None,
    ) -> None:
        self.results = result if isinstance(result, list) else [result]
        self.marker_results = (
            marker_result
            if isinstance(marker_result, list)
            else ([marker_result] if marker_result is not None else [])
        )
        self.calls: list[dict] = []
        self.marker_calls: list[dict] = []

    def detect(self, **_kwargs) -> VisualDetectionResult:
        self.calls.append(_kwargs)
        if len(self.results) == 1:
            return self.results[0]
        return self.results.pop(0)

    def detect_marker_presence(self, **kwargs) -> VisualMarkerPresenceResult:
        self.marker_calls.append(kwargs)
        if len(self.marker_results) == 1:
            return self.marker_results[0]
        if self.marker_results:
            return self.marker_results.pop(0)
        return VisualMarkerPresenceResult(
            marker_text=kwargs["marker_text"],
            marker_detection_backend="ocr",
            marker_detection_available=False,
            marker_found=None,
            marker_confidence=None,
            window_bounds=kwargs["window_bounds"],
            search_region_bounds=(144, 390, 560, 210),
            screenshot_captured=True,
            screenshot_path="workspace/logs/codex_marker_presence.png",
            annotated_screenshot_path="workspace/logs/codex_marker_presence_annotated.png",
            ocr_text_path="workspace/logs/codex_marker_presence_ocr.txt",
            error="Marker OCR backend unavailable: No module named 'pytesseract'",
        )


def visual_plus_result() -> VisualDetectionResult:
    return VisualDetectionResult(
        backend_available=True,
        screenshot_captured=True,
        window_bounds=(0, 0, 800, 600),
        safe_region_bounds=(144, 390, 560, 210),
        plus_button_found=True,
        plus_button_bbox=(250, 500, 42, 42),
        plus_button_confidence=1.0,
        selected_strategy="visual_plus_anchor",
        computed_click_point=(271, 451),
        click_point_safe=True,
    )


def visual_placeholder_result() -> VisualDetectionResult:
    return VisualDetectionResult(
        backend_available=True,
        screenshot_captured=True,
        window_bounds=(0, 0, 800, 600),
        safe_region_bounds=(144, 390, 560, 210),
        placeholder_detection_backend_available=True,
        placeholder_found=True,
        placeholder_bbox=(260, 520, 250, 40),
        placeholder_confidence=1.0,
        selected_strategy="visual_placeholder_anchor",
        computed_click_point=(385, 540),
        click_point_safe=True,
    )


def visual_busy_result() -> VisualDetectionResult:
    return VisualDetectionResult(
        backend_available=True,
        screenshot_captured=True,
        window_bounds=(0, 0, 800, 600),
        safe_region_bounds=(144, 390, 560, 210),
        placeholder_detection_backend_available=True,
        placeholder_found=False,
        plus_button_found=True,
        plus_button_bbox=(250, 500, 42, 42),
        plus_button_confidence=1.0,
        selected_strategy="visual_plus_anchor",
        computed_click_point=(271, 451),
        click_point_safe=True,
    )


def visual_plus_with_placeholder_result() -> VisualDetectionResult:
    return replace(
        visual_plus_result(),
        placeholder_detection_backend_available=True,
        placeholder_found=True,
        placeholder_bbox=(260, 500, 240, 60),
        placeholder_confidence=1.0,
    )


def marker_result(
    *,
    found: bool | None,
    available: bool = True,
    error: str | None = None,
    ocr_text_path: str = "workspace/logs/codex_marker_presence_ocr.txt",
    match_text: str | None = None,
    reason: str | None = None,
) -> VisualMarkerPresenceResult:
    return VisualMarkerPresenceResult(
        marker_text=CODEX_PASTE_TEST_MARKER,
        marker_detection_backend="ocr",
        marker_detection_available=available,
        marker_found=found,
        marker_confidence=1.0 if found else (0.0 if found is False else None),
        window_bounds=(0, 0, 800, 600),
        search_region_bounds=(144, 360, 560, 240),
        screenshot_captured=True,
        screenshot_path="workspace/logs/codex_marker_presence.png",
        annotated_screenshot_path="workspace/logs/codex_marker_presence_annotated.png",
        ocr_text_path=ocr_text_path,
        marker_match_text=match_text,
        detection_reason=reason,
        error=error,
    )


def ready_paste_visual(
    *,
    marker_result: VisualMarkerPresenceResult | None = None,
    final_visual: VisualDetectionResult | None = None,
) -> FakeVisualDetector:
    return FakeVisualDetector(
        [visual_placeholder_result(), final_visual or visual_plus_result()],
        marker_result=marker_result,
    )


def paste_test_outputs(
    *,
    click_backend: str = "system_events",
    paste_backend: str = "system_events",
    marker_window_bounds: str = "0\n0\n800\n600\n",
    marker_detection_count: int = 1,
    after_paste_snapshot: str = "",
    cleanup: bool = False,
) -> list[tuple[int, str, str]]:
    outputs: list[tuple[int, str, str]] = [
        (0, "", ""),  # activate Codex before composer-ready wait
        (0, "Codex\n", ""),  # frontmost check during composer-ready wait
        (0, "0\n0\n800\n600\n", ""),  # bounded composer-ready screenshot
        (0, "0\n0\n800\n600\n", ""),  # final visual click-target screenshot
        (0, "Codex\n", ""),  # frontmost before click
    ]
    if click_backend == "system_events":
        outputs.append((0, "", ""))  # click at visual anchor
    outputs.extend(
        [
            (0, "Codex\n", ""),  # frontmost after click
        ]
    )
    if paste_backend == "system_events":
        outputs.append((0, "", ""))  # command-v paste
    for _ in range(marker_detection_count):
        outputs.append((0, marker_window_bounds, ""))  # bounded marker OCR screenshot
    outputs.append((0, after_paste_snapshot, ""))  # focused element snapshot after paste
    if cleanup:
        outputs.extend(
            [
                (0, "", ""),  # select all
                (0, "", ""),  # delete
                (0, snapshot(value=""), ""),  # focused element snapshot after cleanup
            ]
        )
    return outputs


def snapshot(
    *,
    active_app: str = "Codex",
    role: str = "AXTextArea",
    description: str = "Prompt input",
    value: str = "",
    ui_text: str = "",
    button_text: str = "",
) -> str:
    return "\n".join([active_app, role, description, value, ui_text, button_text])


def target() -> ManualStageTarget:
    return ManualStageTarget(app_name="Codex")


def pyautogui_target() -> ManualStageTarget:
    return ManualStageTarget(
        app_name="Codex",
        click_backend="pyautogui",
        visual_anchor_click_backend="pyautogui",
        paste_backend="pyautogui",
    )


def idle_target(**overrides) -> ManualStageTarget:
    return replace(
        ManualStageTarget(
            app_name="Codex",
            composer_placeholder_text="후속 변경 사항을 부탁하세요",
            idle_empty_wait_timeout_seconds=600,
            idle_empty_poll_interval_seconds=10,
            dedicated_automation_session=True,
            allow_overwrite_after_idle_timeout=True,
            stop_on_idle_timeout=False,
            plus_anchor_enabled=True,
            plus_anchor_x_offset=0,
            plus_anchor_y_offset=50,
        ),
        **overrides,
    )


def fallback_target() -> ManualStageTarget:
    return ManualStageTarget(
        app_name="Codex",
        input_focus_strategy="window_relative_click",
        input_click_x_ratio=0.5,
        input_click_y_ratio=0.92,
    )


def composer_state(
    *,
    placeholder: bool = False,
    plus: bool = False,
    active_app: str = "Codex",
) -> str:
    placeholder_line = "PLACEHOLDER\t20\t300\t300\t30" if placeholder else ""
    plus_line = "PLUS\t100\t400\t20\t20" if plus else ""
    return "\n".join([active_app, placeholder_line, plus_line])


def window_rows() -> str:
    return "\n".join(
        [
            "1\tTiny utility\t1762\t1153\t84\t77\ttrue\tfalse\tfalse\tAXWindow\tAXUnknown",
            "2\tAgent Bridge Main\t100\t200\t1000\t700\ttrue\tfalse\tfalse\tAXWindow\tAXStandardWindow",
            "3\tHidden\t50\t50\t900\t500\tfalse\tfalse\tfalse\tAXWindow\tAXStandardWindow",
        ]
    )


def tiny_window_rows() -> str:
    return "1\tTiny utility\t1762\t1153\t84\t77\ttrue\tfalse\tfalse\tAXWindow\tAXUnknown"


def test_select_main_window_rejects_tiny_front_window_and_uses_largest_visible_normal():
    runner = SequenceRunner([(0, window_rows(), "")])
    detector = CodexUIDetector(runner=runner, sleep_fn=lambda _: None)

    result = detector.select_main_window(target())
    output = format_codex_window_selection(result)

    assert result.selected_bounds == (100, 200, 1000, 700)
    assert result.plausible
    assert result.selected_window is not None
    assert result.selected_window.title == "Agent Bridge Main"
    assert result.windows[0].rejected
    assert "width 84 < 400" in result.windows[0].rejection_reasons
    assert "Tiny utility" in output
    assert "status=rejected" in output
    assert "status=selected" in output


def test_window_enumeration_prefers_bundle_id_when_configured():
    runner = SequenceRunner([(0, window_rows(), "")])
    detector = CodexUIDetector(runner=runner, sleep_fn=lambda _: None)

    result = detector.select_main_window(
        ManualStageTarget(app_name="ChatGPT", bundle_id="com.openai.chat")
    )

    assert result.selected_bounds == (100, 200, 1000, 700)
    command = " ".join(runner.commands[0])
    assert 'bundle identifier is "com.openai.chat"' in command
    assert "bestWindowCount" in command
    assert 'application process "ChatGPT"' not in command


def test_window_selection_reports_no_usable_window_clearly():
    runner = SequenceRunner([(0, tiny_window_rows(), "")])
    detector = CodexUIDetector(runner=runner, sleep_fn=lambda _: None)

    result = detector.select_main_window(ManualStageTarget(app_name="ChatGPT"))
    output = format_codex_window_selection(result)

    assert not result.plausible
    assert result.selected_bounds is None
    assert "ChatGPT Window Diagnostic" in output
    assert "status=rejected" in output


def test_visual_detection_uses_selected_main_window_bounds():
    visual = FakeVisualDetector(visual_plus_result())
    runner = SequenceRunner([(0, window_rows(), "")])
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=visual,  # type: ignore[arg-type]
    )

    result = detector.visual_detection_result(target())

    assert visual.calls[0]["window_bounds"] == (100, 200, 1000, 700)
    assert result.window_bounds == (0, 0, 800, 600)


def test_focus_target_test_refuses_when_no_main_window_exists(tmp_path):
    runner = SequenceRunner(
        [
            (0, "", ""),
            (0, "Codex\n", ""),
            (0, tiny_window_rows(), ""),
        ]
    )
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=FakeVisualDetector(visual_plus_result()),  # type: ignore[arg-type]
        pyautogui_clicker=lambda _x, _y: None,
        pyautogui_writer=lambda _text, interval=0.0: None,
        pyautogui_presser=lambda _key: None,
    )

    result = detector.run_focus_target_test(
        pyautogui_target(),
        logs_dir=tmp_path,
        click_backend="pyautogui",
    )

    assert result.window_bounds is None
    assert result.attempts == ()
    assert "No plausible main Codex window" in (result.error or "")


def test_input_cleared_after_submit_confirms_submit():
    prompt = "Run a safe validation task."
    runner = SequenceRunner(
        [
            (0, snapshot(value=prompt), ""),
            (0, snapshot(value=""), ""),
        ]
    )
    detector = CodexUIDetector(runner=runner, sleep_fn=lambda _: None)

    before = detector.inspect_before_submit(
        target=target(),
        prompt=prompt,
        clipboard_text=prompt,
    )
    after = detector.inspect_after_submit(target=target(), prompt=prompt, before=before)

    assert before.prompt_text_present is True
    assert after.input_cleared is True
    assert after.confirmed is True
    assert after.confirmation_reason == "input_cleared"


def test_new_user_message_confirms_submit():
    prompt = "Run a safe validation task."
    runner = SequenceRunner(
        [
            (0, snapshot(value=prompt), ""),
            (0, snapshot(value=prompt, ui_text=f"Earlier message {prompt}"), ""),
        ]
    )
    detector = CodexUIDetector(runner=runner, sleep_fn=lambda _: None)

    before = detector.inspect_before_submit(
        target=target(),
        prompt=prompt,
        clipboard_text=prompt,
    )
    after = detector.inspect_after_submit(target=target(), prompt=prompt, before=before)

    assert after.input_cleared is False
    assert after.new_user_message_detected is True
    assert after.confirmed is True
    assert after.confirmation_reason == "new_user_message"


def test_running_state_confirms_submit():
    prompt = "Run a safe validation task."
    runner = SequenceRunner(
        [
            (0, snapshot(value=prompt), ""),
            (0, snapshot(value=prompt, button_text="Stop generating"), ""),
        ]
    )
    detector = CodexUIDetector(runner=runner, sleep_fn=lambda _: None)

    before = detector.inspect_before_submit(
        target=target(),
        prompt=prompt,
        clipboard_text=prompt,
    )
    after = detector.inspect_after_submit(target=target(), prompt=prompt, before=before)

    assert after.input_cleared is False
    assert after.running_state_detected is True
    assert after.confirmed is True
    assert after.confirmation_reason == "running_state"


def test_no_submit_signal_yields_unconfirmed():
    prompt = "Run a safe validation task."
    runner = SequenceRunner(
        [
            (0, snapshot(value=prompt), ""),
            (0, snapshot(value=prompt, ui_text="No matching conversation state"), ""),
        ]
    )
    detector = CodexUIDetector(runner=runner, sleep_fn=lambda _: None)

    before = detector.inspect_before_submit(
        target=target(),
        prompt=prompt,
        clipboard_text=prompt,
    )
    after = detector.inspect_after_submit(target=target(), prompt=prompt, before=before)

    assert after.input_cleared is False
    assert after.new_user_message_detected is False
    assert after.running_state_detected is False
    assert after.confirmed is None
    assert after.confirmation_reason == "not_detectable"


def test_focus_input_reports_selected_candidate():
    runner = SequenceRunner(
        [
            (
                0,
                "\n".join(
                    [
                        "Finder",
                        "Codex",
                        "2",
                        "AXTextArea: Prompt input",
                        "draft text",
                        "AXTextArea: Prompt input",
                    ]
                ),
                "",
            )
        ]
    )
    detector = CodexUIDetector(runner=runner, sleep_fn=lambda _: None)

    result = detector.focus_input(target())

    assert result.succeeded
    assert result.active_app_before == "Finder"
    assert result.active_app_after == "Codex"
    assert result.app_frontmost
    assert result.input_candidate_count == 2
    assert result.selected_input_candidate_summary == "AXTextArea: Prompt input"
    assert result.input_text_length_before_paste == len("draft text")


def test_wait_until_frontmost_succeeds_after_activation_delay():
    runner = SequenceRunner(
        [
            (0, "Finder\n", ""),
            (0, "Codex\n", ""),
        ]
    )
    detector = CodexUIDetector(runner=runner, sleep_fn=lambda _: None)

    assert detector.wait_until_frontmost(target(), timeout_seconds=1)


def test_diagnose_codex_ui_handles_unavailable_accessibility_data():
    runner = SequenceRunner([(1, "", "not authorized")])
    detector = CodexUIDetector(runner=runner, sleep_fn=lambda _: None)

    diagnostic = detector.diagnose(target())
    output = format_codex_ui_diagnostic(diagnostic)

    assert not diagnostic.accessibility_available
    assert not diagnostic.input_field_detectable
    assert "not authorized" in output
    assert "No paste, submit" in output


def test_ui_tree_dump_parses_nested_accessibility_data(tmp_path):
    runner = SequenceRunner(
        [
            (
                0,
                "\n".join(
                    [
                        "Codex",
                        "0\tAXWindow\t\t\tCodex\t",
                        "1\tAXGroup\t\t\tmain\t",
                        "2\tAXWebArea\t\tCodex editor\t\t",
                        "3\tAXTextArea\t\tPrompt input\t\tDraft",
                    ]
                ),
                "",
            )
        ]
    )
    detector = CodexUIDetector(runner=runner, sleep_fn=lambda _: None)

    dump = detector.write_ui_tree_dump(target(), logs_dir=tmp_path)

    assert dump.accessibility_available
    assert len(dump.elements) == 4
    assert dump.elements[-1].role == "AXTextArea"
    assert (tmp_path / "codex_ui_tree.json").exists()
    assert "AXTextArea" in (tmp_path / "codex_ui_tree.txt").read_text(encoding="utf-8")


def test_input_target_diagnostic_reports_no_candidate_without_fallback():
    runner = SequenceRunner(
        [
            (0, "Codex\n", ""),
            (0, "Codex\nCodex\n0\nunknown\n\nunknown\n", ""),
            (0, "10\n20\n800\n600\n", ""),
            (0, "\n", ""),
            (0, "Codex\n", ""),
            (0, composer_state(), ""),
        ]
    )
    detector = CodexUIDetector(runner=runner, sleep_fn=lambda _: None)

    diagnostic = detector.diagnose_input_target(target())
    output = format_codex_input_target_diagnostic(diagnostic)

    assert diagnostic.input_candidate_count == 0
    assert not diagnostic.fallback_enabled
    assert not diagnostic.prompt_presence_verifiable
    assert not diagnostic.live_submit_allowed
    assert "fallback is disabled" in output


def test_composer_placeholder_present_proceeds_immediately():
    events: list[str] = []
    runner = SequenceRunner([(0, composer_state(placeholder=True, plus=True), "")])
    detector = CodexUIDetector(runner=runner, sleep_fn=lambda _: None)

    result = detector.wait_for_composer_idle_empty(
        idle_target(),
        event_callback=lambda event, _metadata: events.append(event),
    )

    assert result.state == CODEX_COMPOSER_IDLE_EMPTY
    assert result.placeholder_found
    assert not result.timed_out
    assert not result.should_overwrite
    assert "local_agent_placeholder_detected" in events


def test_composer_placeholder_absent_waits_and_polls():
    events: list[str] = []
    runner = SequenceRunner(
        [
            (0, composer_state(), ""),
            (0, composer_state(plus=True), ""),
        ]
    )
    times = iter([0.0, 0.0, 2.0])
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        monotonic_fn=lambda: next(times),
    )

    result = detector.wait_for_composer_idle_empty(
        idle_target(idle_empty_wait_timeout_seconds=1, idle_empty_poll_interval_seconds=1),
        event_callback=lambda event, _metadata: events.append(event),
    )

    assert result.state == CODEX_COMPOSER_IDLE_WAIT_TIMEOUT
    assert result.timed_out
    assert result.should_overwrite
    assert "local_agent_pending_text_wait_poll" in events


def test_composer_placeholder_appears_during_wait_proceeds():
    runner = SequenceRunner(
        [
            (0, composer_state(), ""),
            (0, composer_state(placeholder=True), ""),
        ]
    )
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        monotonic_fn=lambda: 0.0,
    )

    result = detector.wait_for_composer_idle_empty(
        idle_target(idle_empty_wait_timeout_seconds=10, idle_empty_poll_interval_seconds=1)
    )

    assert result.state == CODEX_COMPOSER_IDLE_EMPTY
    assert result.polls == 1
    assert result.placeholder_found


def test_default_timeout_policy_uses_controlled_overwrite():
    runner = SequenceRunner([(0, composer_state(plus=True), "")])
    detector = CodexUIDetector(runner=runner, sleep_fn=lambda _: None, monotonic_fn=lambda: 0.0)

    result = detector.wait_for_composer_idle_empty(
        idle_target(idle_empty_wait_timeout_seconds=0)
    )

    assert result.timed_out
    assert result.timeout_policy == "overwrite"
    assert result.should_overwrite
    assert result.overwrite_allowed


def test_conservative_timeout_policy_stops_safely():
    runner = SequenceRunner([(0, composer_state(plus=True), "")])
    detector = CodexUIDetector(runner=runner, sleep_fn=lambda _: None, monotonic_fn=lambda: 0.0)

    result = detector.wait_for_composer_idle_empty(
        idle_target(
            idle_empty_wait_timeout_seconds=0,
            dedicated_automation_session=False,
            allow_overwrite_after_idle_timeout=False,
            stop_on_idle_timeout=True,
        )
    )

    assert result.timed_out
    assert result.timeout_policy == "stop"
    assert result.should_stop
    assert not result.should_overwrite
    assert "stop_on_idle_timeout is enabled" in (result.message or "")


def test_stop_on_idle_timeout_overrides_overwrite_policy():
    runner = SequenceRunner([(0, composer_state(plus=True), "")])
    detector = CodexUIDetector(runner=runner, sleep_fn=lambda _: None, monotonic_fn=lambda: 0.0)

    result = detector.wait_for_composer_idle_empty(
        idle_target(idle_empty_wait_timeout_seconds=0, stop_on_idle_timeout=True)
    )

    assert result.timeout_policy == "stop"
    assert result.should_stop


def test_plus_button_anchor_computes_click_point_without_clicking_plus():
    runner = SequenceRunner(
        [
            (0, composer_state(plus=True), ""),
            (0, "0\n0\n800\n600\n", ""),
        ]
    )
    detector = CodexUIDetector(runner=runner, sleep_fn=lambda _: None)

    result = detector.plus_anchor_click_preview(idle_target())

    assert result.fallback_click_point == (110, 360)
    assert result.fallback_click_point != (110, 410)
    assert result.succeeded is False
    assert result.error is None


def test_direct_plus_anchor_uses_same_x_and_default_y_offset():
    detector = CodexUIDetector(
        runner=SequenceRunner([(0, "Codex\n", ""), (0, "Codex\n", "")]),
        sleep_fn=lambda _: None,
        visual_detector=FakeVisualDetector(visual_plus_result()),  # type: ignore[arg-type]
    )
    direct_target = replace(
        pyautogui_target(),
        direct_plus_anchor_enabled=True,
        direct_plus_anchor_y_offset=24,
    )

    result = detector.direct_plus_anchor_preview(direct_target)

    assert result.plus_button_center == (271, 521)
    assert result.fallback_click_point == (271, 497)
    assert result.fallback_click_point[0] == result.plus_button_center[0]
    assert result.direct_plus_anchor_y_offset == 24
    assert result.error is None


def test_direct_plus_anchor_respects_configured_y_offset():
    detector = CodexUIDetector(
        runner=SequenceRunner([(0, "Codex\n", ""), (0, "Codex\n", "")]),
        sleep_fn=lambda _: None,
        visual_detector=FakeVisualDetector(visual_plus_result()),  # type: ignore[arg-type]
    )
    direct_target = replace(
        pyautogui_target(),
        direct_plus_anchor_enabled=True,
        direct_plus_anchor_y_offset=40,
    )

    result = detector.direct_plus_anchor_preview(direct_target)

    assert result.fallback_click_point == (271, 481)


def test_direct_plus_anchor_rejects_point_inside_plus_button():
    detector = CodexUIDetector(
        runner=SequenceRunner([(0, "Codex\n", ""), (0, "Codex\n", "")]),
        sleep_fn=lambda _: None,
        visual_detector=FakeVisualDetector(visual_plus_result()),  # type: ignore[arg-type]
    )
    direct_target = replace(
        pyautogui_target(),
        direct_plus_anchor_enabled=True,
        direct_plus_anchor_y_offset=0,
    )

    result = detector.direct_plus_anchor_preview(direct_target)

    assert result.fallback_click_point is None
    assert "Direct plus-anchor" in (result.error or "")


def test_direct_plus_anchor_rejects_point_outside_composer_band():
    detector = CodexUIDetector(
        runner=SequenceRunner([(0, "Codex\n", ""), (0, "Codex\n", "")]),
        sleep_fn=lambda _: None,
        visual_detector=FakeVisualDetector(visual_plus_result()),  # type: ignore[arg-type]
    )
    direct_target = replace(
        pyautogui_target(),
        direct_plus_anchor_enabled=True,
        direct_plus_anchor_y_offset=200,
    )

    result = detector.direct_plus_anchor_preview(direct_target)

    assert result.fallback_click_point is None


def test_click_test_uses_direct_plus_anchor_when_configured():
    clicks: list[tuple[int, int]] = []
    runner = SequenceRunner(
        [
            (0, "Codex\n", ""),
            (0, "Codex\n", ""),
            (0, snapshot(role="AXTextArea", description="Prompt input"), ""),
        ]
    )
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=FakeVisualDetector(visual_plus_result()),  # type: ignore[arg-type]
        pyautogui_clicker=lambda x, y: clicks.append((x, y)),
    )
    direct_target = replace(
        pyautogui_target(),
        direct_plus_anchor_enabled=True,
        direct_plus_anchor_y_offset=24,
    )

    result = detector.click_direct_plus_anchor(direct_target)

    assert result.succeeded
    assert result.selected_input_candidate_summary == "direct_plus_anchor"
    assert clicks == [(271, 497)]


def test_plus_button_missing_after_timeout_fails_safely():
    runner = SequenceRunner(
        [
            (0, composer_state(), ""),
            (0, "0\n0\n800\n600\n", ""),
            (0, "Codex\n", ""),
        ]
    )
    detector = CodexUIDetector(runner=runner, sleep_fn=lambda _: None)

    result = detector.plus_anchor_click_preview(idle_target())

    assert result.fallback_click_point is None
    assert "Plus-button anchor" in (result.error or "")


def test_window_relative_fallback_computes_click_point():
    runner = SequenceRunner(
        [
            (0, "100\n200\n1000\n500\n", ""),
            (0, "Codex\n", ""),
        ]
    )
    detector = CodexUIDetector(runner=runner, sleep_fn=lambda _: None)

    result = detector.window_relative_click_preview(fallback_target())

    assert result.window_bounds == (100, 200, 1000, 500)
    assert result.fallback_click_point == (600, 660)
    assert result.used_fallback


def test_click_test_uses_explicit_window_relative_point():
    runner = SequenceRunner(
        [
            (0, "100\n200\n1000\n500\n", ""),
            (0, "Codex\n", ""),
            (0, "", ""),
            (0, snapshot(role="AXTextArea", description="Prompt input"), ""),
        ]
    )
    detector = CodexUIDetector(runner=runner, sleep_fn=lambda _: None)

    result = detector.click_window_relative_input(fallback_target())

    assert result.succeeded
    assert result.used_fallback
    assert result.fallback_click_point == (600, 660)
    assert any("click at {600, 660}" in " ".join(command) for command in runner.commands)


def test_paste_test_uses_visual_plus_anchor_click_target():
    marker = CODEX_PASTE_TEST_MARKER
    runner = SequenceRunner(paste_test_outputs(after_paste_snapshot=snapshot(value=marker), cleanup=True))
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=ready_paste_visual(),  # type: ignore[arg-type]
    )
    clipboard = FakeClipboard()

    result = detector.run_paste_test(target(), clipboard=clipboard)

    assert result.click_attempted
    assert result.click_succeeded
    assert result.visual_click_point == (271, 451)
    assert any("click at {271, 451}" in " ".join(command) for command in runner.commands)


def test_paste_test_prefers_placeholder_click_when_idle_empty():
    visual = ready_paste_visual(final_visual=visual_plus_with_placeholder_result())
    runner = SequenceRunner(
        paste_test_outputs(after_paste_snapshot="Codex\nERROR\nnot authorized\n\n\n\n")
    )
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=visual,  # type: ignore[arg-type]
    )

    result = detector.run_paste_test(target(), clipboard=FakeClipboard())
    command_text = "\n".join(" ".join(command) for command in runner.commands)

    assert result.visual_selected_strategy == "visual_placeholder_anchor"
    assert result.visual_click_point == (380, 530)
    assert "click at {380, 530}" in command_text


def test_paste_test_copies_harmless_marker_and_pastes_without_submit():
    marker = CODEX_PASTE_TEST_MARKER
    runner = SequenceRunner(paste_test_outputs(after_paste_snapshot=snapshot(value=marker), cleanup=True))
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=ready_paste_visual(),  # type: ignore[arg-type]
    )
    clipboard = FakeClipboard()

    result = detector.run_paste_test(target(), clipboard=clipboard)
    command_text = "\n".join(" ".join(command) for command in runner.commands)

    assert clipboard.text == marker
    assert result.clipboard_length == len(marker)
    assert result.paste_attempted
    assert result.paste_succeeded
    assert 'keystroke "v" using command down' in command_text
    assert "key code 36" not in command_text


def test_paste_test_reports_marker_presence_when_detectable():
    marker = CODEX_PASTE_TEST_MARKER
    events: list[str] = []
    runner = SequenceRunner(paste_test_outputs(after_paste_snapshot=snapshot(value=marker), cleanup=True))
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=ready_paste_visual(),  # type: ignore[arg-type]
    )

    result = detector.run_paste_test(
        target(),
        clipboard=FakeClipboard(),
        event_callback=lambda event, _metadata: events.append(event),
    )

    assert result.marker_detected is True
    assert result.marker_presence_detectable is True
    assert result.cleanup_attempted
    assert result.cleanup_success is True
    assert "codex_paste_test_marker_detected" in events
    assert "codex_paste_test_completed" in events


def test_paste_test_handles_undetectable_marker_state():
    runner = SequenceRunner(
        paste_test_outputs(after_paste_snapshot="Codex\nERROR\nnot authorized\n\n\n\n")
    )
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=ready_paste_visual(),  # type: ignore[arg-type]
    )

    result = detector.run_paste_test(target(), clipboard=FakeClipboard())
    output = format_codex_paste_test_result(result)

    assert result.paste_attempted
    assert result.marker_detected is None
    assert result.marker_presence_detectable is False
    assert not result.cleanup_attempted
    assert result.manual_cleanup_required
    assert "Please clear the Codex composer manually if the marker is visible." in output


def test_paste_test_marker_search_is_scoped_to_codex_window():
    marker = CODEX_PASTE_TEST_MARKER
    visual = ready_paste_visual(marker_result=marker_result(found=True))
    runner = SequenceRunner(
        paste_test_outputs(
            marker_window_bounds="20\n30\n900\n700\n",
            after_paste_snapshot="Codex\nERROR\nnot authorized\n\n\n\n",
        )
    )
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=visual,  # type: ignore[arg-type]
    )

    result = detector.run_paste_test(target(), clipboard=FakeClipboard())

    assert visual.marker_calls[0]["window_bounds"] == (20, 30, 900, 700)
    assert visual.marker_calls[0]["marker_text"] == marker
    assert result.marker_search_region_bounds == (144, 360, 560, 240)


def test_paste_test_ocr_unavailable_reports_unknown_without_crashing():
    visual = ready_paste_visual(
        marker_result=marker_result(
            found=None,
            available=False,
            error="Marker OCR backend unavailable: No module named 'pytesseract'",
        ),
    )
    runner = SequenceRunner(
        paste_test_outputs(after_paste_snapshot="Codex\nERROR\nnot authorized\n\n\n\n")
    )
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=visual,  # type: ignore[arg-type]
    )

    result = detector.run_paste_test(target(), clipboard=FakeClipboard())

    assert result.visual_marker_found is None
    assert result.marker_detection_available is False
    assert result.marker_detected is None
    assert "pytesseract" in (result.marker_detection_error or "")


def test_paste_test_visual_marker_found_reports_yes():
    marker = CODEX_PASTE_TEST_MARKER
    events: list[str] = []
    visual = ready_paste_visual(
        marker_result=marker_result(found=True),
    )
    runner = SequenceRunner(
        paste_test_outputs(after_paste_snapshot="Codex\nERROR\nnot authorized\n\n\n\n")
    )
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=visual,  # type: ignore[arg-type]
    )

    result = detector.run_paste_test(
        target(),
        clipboard=FakeClipboard(),
        event_callback=lambda event, _metadata: events.append(event),
    )

    assert result.visual_marker_found is True
    assert result.marker_detection_available is True
    assert result.marker_detected is True
    assert result.marker_text == marker
    assert "local_agent_marker_present_after_paste" in events


def test_paste_test_visual_marker_absent_reports_no_when_backend_proves_absence():
    events: list[str] = []
    visual = ready_paste_visual(
        marker_result=marker_result(found=False),
    )
    runner = SequenceRunner(
        paste_test_outputs(after_paste_snapshot="Codex\nERROR\nnot authorized\n\n\n\n")
    )
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=visual,  # type: ignore[arg-type]
    )

    result = detector.run_paste_test(
        target(),
        clipboard=FakeClipboard(),
        event_callback=lambda event, _metadata: events.append(event),
    )
    output = format_codex_paste_test_result(result)

    assert result.visual_marker_found is False
    assert result.marker_detection_available is True
    assert result.marker_confidence == 0.0
    assert "Visual marker found: no" in output
    assert "local_agent_marker_absent_after_paste" in events


def test_paste_test_reports_marker_debug_artifact_paths():
    visual = ready_paste_visual(marker_result=marker_result(found=True))
    runner = SequenceRunner(
        paste_test_outputs(after_paste_snapshot="Codex\nERROR\nnot authorized\n\n\n\n")
    )
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=visual,  # type: ignore[arg-type]
    )

    result = detector.run_paste_test(target(), clipboard=FakeClipboard())
    output = format_codex_paste_test_result(result)

    assert result.marker_screenshot_path == "workspace/logs/codex_marker_presence.png"
    assert (
        result.marker_annotated_screenshot_path
        == "workspace/logs/codex_marker_presence_annotated.png"
    )
    assert result.marker_ocr_text_path == "workspace/logs/codex_marker_presence_ocr.txt"
    assert "Marker screenshot: workspace/logs/codex_marker_presence.png" in output
    assert "Marker annotated screenshot: workspace/logs/codex_marker_presence_annotated.png" in output
    assert "Marker OCR text: workspace/logs/codex_marker_presence_ocr.txt" in output


def test_detect_codex_prompt_presence_returns_structured_result():
    marker = CODEX_PASTE_TEST_MARKER
    visual = FakeVisualDetector(
        visual_plus_result(),
        marker_result=marker_result(found=True),
    )
    runner = SequenceRunner([(0, "20\n30\n900\n700\n", "")])
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=visual,  # type: ignore[arg-type]
    )

    result = detector.detect_codex_prompt_presence(
        target(),
        expected_text=marker,
        logs_dir=None,
        write_debug=True,
    )

    assert result.marker_found is True
    assert result.marker_detection_available is True
    assert result.search_region_bounds == (144, 360, 560, 240)
    assert visual.marker_calls[0]["window_bounds"] == (20, 30, 900, 700)
    assert visual.marker_calls[0]["marker_text"] == marker


def test_paste_test_blocks_when_visual_composer_is_not_ready():
    visual = FakeVisualDetector(visual_busy_result())
    runner = SequenceRunner(
        [
            (0, "", ""),
            (0, "Codex\n", ""),
            (0, "0\n0\n800\n600\n", ""),
        ]
    )
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        monotonic_fn=lambda: 0.0,
        visual_detector=visual,  # type: ignore[arg-type]
    )

    result = detector.run_paste_test(
        idle_target(busy_placeholder_wait_timeout_seconds=0, on_busy_timeout="abort"),
        clipboard=FakeClipboard(),
    )
    command_text = "\n".join(" ".join(command) for command in runner.commands)

    assert not result.click_attempted
    assert not result.paste_attempted
    assert "keystroke" not in command_text
    assert "busy timeout" in (result.error or "")


def test_paste_test_blocks_when_visual_plus_anchor_missing():
    runner = SequenceRunner(
        [
            (0, "", ""),
            (0, "Codex\n", ""),
            (0, "0\n0\n800\n600\n", ""),
            (0, "0\n0\n800\n600\n", ""),
            (0, "Codex\n", ""),
        ]
    )
    visual = VisualDetectionResult(
        backend_available=True,
        screenshot_captured=True,
        window_bounds=(0, 0, 800, 600),
        safe_region_bounds=(144, 390, 560, 210),
        selected_strategy="none",
        computed_click_point=None,
        click_point_safe=False,
        error="No safe visual Codex composer anchor was detected.",
    )
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=ready_paste_visual(final_visual=visual),  # type: ignore[arg-type]
    )

    result = detector.run_paste_test(target(), clipboard=FakeClipboard())

    assert not result.click_attempted
    assert not result.paste_attempted
    assert "No safe visual" in (result.error or "")


def test_click_visual_input_uses_pyautogui_backend():
    clicks: list[tuple[int, int]] = []
    runner = SequenceRunner(
        [
            (0, "Codex\n", ""),
            (0, "Codex\n", ""),
            (0, snapshot(role="AXTextArea", description="Prompt input"), ""),
        ]
    )
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=FakeVisualDetector(visual_plus_result()),  # type: ignore[arg-type]
        pyautogui_clicker=lambda x, y: clicks.append((x, y)),
    )

    result = detector.click_visual_input(pyautogui_target())
    command_text = "\n".join(" ".join(command) for command in runner.commands)

    assert result.succeeded
    assert result.click_backend == "pyautogui"
    assert result.pyautogui_available is True
    assert clicks == [(271, 451)]
    assert "click at" not in command_text


def test_paste_test_uses_pyautogui_backend_without_submitting():
    clicks: list[tuple[int, int]] = []
    hotkeys: list[tuple[str, ...]] = []
    events: list[str] = []
    visual = ready_paste_visual(marker_result=marker_result(found=True))
    runner = SequenceRunner(
        paste_test_outputs(
            click_backend="pyautogui",
            paste_backend="pyautogui",
            after_paste_snapshot="Codex\nERROR\nnot authorized\n\n\n\n",
        )
    )
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=visual,  # type: ignore[arg-type]
        pyautogui_clicker=lambda x, y: clicks.append((x, y)),
        pyautogui_hotkeyer=lambda *keys: hotkeys.append(tuple(keys)),
    )

    result = detector.run_paste_test(
        pyautogui_target(),
        clipboard=FakeClipboard(),
        event_callback=lambda event, _metadata: events.append(event),
    )
    command_text = "\n".join(" ".join(command) for command in runner.commands)

    assert result.click_backend == "pyautogui"
    assert result.paste_backend == "pyautogui"
    assert result.click_attempted
    assert result.click_succeeded
    assert result.paste_attempted
    assert result.paste_succeeded
    assert clicks == [(271, 451), (271, 451)]
    assert hotkeys == [("command", "v")]
    assert result.paste_variant_attempted == "command_v_hotkey"
    assert result.paste_variant_succeeded is True
    assert result.final_paste_strategy == "command_v_hotkey"
    assert "local_agent_paste_backend_selected" in events
    assert "local_agent_pyautogui_paste_attempted" in events
    assert "local_agent_pyautogui_paste_completed" in events
    assert "local_agent_marker_ocr_after_pyautogui_paste" in events
    assert "click at" not in command_text
    assert 'keystroke "v" using command down' not in command_text
    assert "key code 36" not in command_text


def test_pyautogui_paste_tries_cmd_v_when_command_v_marker_not_found():
    hotkeys: list[tuple[str, ...]] = []
    visual = ready_paste_visual(
        marker_result=[
            marker_result(found=False),
            marker_result(found=True),
        ],
    )
    runner = SequenceRunner(
        paste_test_outputs(
            click_backend="pyautogui",
            paste_backend="pyautogui",
            marker_detection_count=2,
            after_paste_snapshot="Codex\nERROR\nnot authorized\n\n\n\n",
        )
    )
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=visual,  # type: ignore[arg-type]
        pyautogui_clicker=lambda _x, _y: None,
        pyautogui_hotkeyer=lambda *keys: hotkeys.append(tuple(keys)),
    )

    result = detector.run_paste_test(pyautogui_target(), clipboard=FakeClipboard())

    assert hotkeys == [("command", "v"), ("cmd", "v")]
    assert [attempt.variant_name for attempt in result.paste_variant_attempts] == [
        "command_v_hotkey",
        "cmd_v_hotkey",
    ]
    assert result.final_paste_strategy == "cmd_v_hotkey"
    assert result.paste_variant_succeeded is True


def test_pyautogui_paste_tries_explicit_keydown_variant():
    hotkeys: list[tuple[str, ...]] = []
    key_events: list[tuple[str, str]] = []
    visual = ready_paste_visual(
        marker_result=[
            marker_result(found=False),
            marker_result(found=False),
            marker_result(found=True),
        ],
    )
    runner = SequenceRunner(
        paste_test_outputs(
            click_backend="pyautogui",
            paste_backend="pyautogui",
            marker_detection_count=3,
            after_paste_snapshot="Codex\nERROR\nnot authorized\n\n\n\n",
        )
    )
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=visual,  # type: ignore[arg-type]
        pyautogui_clicker=lambda _x, _y: None,
        pyautogui_hotkeyer=lambda *keys: hotkeys.append(tuple(keys)),
        pyautogui_key_downer=lambda key: key_events.append(("down", key)),
        pyautogui_presser=lambda key: key_events.append(("press", key)),
        pyautogui_key_upper=lambda key: key_events.append(("up", key)),
    )

    result = detector.run_paste_test(pyautogui_target(), clipboard=FakeClipboard())

    assert hotkeys == [("command", "v"), ("cmd", "v")]
    assert key_events == [("down", "command"), ("press", "v"), ("up", "command")]
    assert result.final_paste_strategy == "command_v_keydown"


def test_ascii_typewrite_fallback_is_diagnostic_only():
    writes: list[tuple[str, float]] = []
    visual = ready_paste_visual(
        marker_result=[
            marker_result(found=False),
            marker_result(found=False),
            marker_result(found=False),
            marker_result(found=False),
            marker_result(found=True),
        ],
    )
    runner = SequenceRunner(
        paste_test_outputs(
            click_backend="pyautogui",
            paste_backend="pyautogui",
            marker_detection_count=5,
            after_paste_snapshot="Codex\nERROR\nnot authorized\n\n\n\n",
        )
    )
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=visual,  # type: ignore[arg-type]
        pyautogui_clicker=lambda _x, _y: None,
        pyautogui_hotkeyer=lambda *_keys: None,
        pyautogui_key_downer=lambda _key: None,
        pyautogui_presser=lambda _key: None,
        pyautogui_key_upper=lambda _key: None,
        pyautogui_writer=lambda text, interval=0.0: writes.append((text, interval)),
    )

    result = detector.run_paste_test(pyautogui_target(), clipboard=FakeClipboard())

    assert writes == [(CODEX_PASTE_TEST_MARKER, 0.001)]
    assert result.final_paste_strategy == "ascii_typewrite_marker"
    assert result.paste_variant_succeeded is True


def test_typewrite_fallback_is_not_used_for_full_local_agent_prompt():
    from agent_bridge.gui.gui_automation import MacOSSystemEventsGuiAdapter

    writes: list[str] = []
    hotkeys: list[tuple[str, ...]] = []

    class FakeCodexDetector:
        def wait_until_frontmost(self, *_args, **_kwargs):
            return True

        def frontmost_app(self):
            return "Codex"

        def wait_for_visual_composer_ready(self, *args, **kwargs):
            return type("State", (), {"should_abort": False, "should_overwrite": False})()

        def focus_input(self, target):
            return LocalAgentFocusResult(succeeded=True, app_frontmost=True)

        def click_visual_input(self, target, **kwargs):
            return LocalAgentFocusResult(succeeded=True, app_frontmost=True)

    clipboard = FakeClipboard()
    clipboard.copy_text("LOCAL AGENT PROMPT")

    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=clipboard,
        app_activator=FakeAppActivator(),
        codex_ui_detector=FakeCodexDetector(),  # type: ignore[arg-type]
        pyautogui_hotkeyer=lambda *keys: hotkeys.append(tuple(keys)),
    )
    adapter.active_target = ManualStageTarget(app_name="Codex", paste_backend="pyautogui")

    adapter.paste_clipboard()

    assert hotkeys == [("command", "v")]
    assert not hasattr(adapter, "pyautogui_writer")
    assert writes == []


def test_literal_v_cleanup_path_is_handled():
    runner = SequenceRunner(
        [
            (0, "", ""),
            (0, "Codex\n", ""),
            (0, "0\n0\n800\n600\n", ""),
            (0, "0\n0\n800\n600\n", ""),
            (0, "Codex\n", ""),
            (0, "Codex\n", ""),
            (0, "0\n0\n800\n600\n", ""),
            (0, "", ""),
            (0, "", ""),
            (0, "0\n0\n800\n600\n", ""),
            (0, "Codex\nERROR\nnot authorized\n\n\n\n", ""),
        ]
    )
    visual = ready_paste_visual(
        marker_result=[
            marker_result(found=False, reason="literal_v_detected"),
            marker_result(found=True),
        ],
    )
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=visual,  # type: ignore[arg-type]
        pyautogui_clicker=lambda _x, _y: None,
        pyautogui_hotkeyer=lambda *_keys: None,
    )

    result = detector.run_paste_test(pyautogui_target(), clipboard=FakeClipboard())
    command_text = "\n".join(" ".join(command) for command in runner.commands)

    assert result.literal_v_detected is True
    assert result.cleanup_attempted
    assert result.cleanup_success is True
    assert 'keystroke "a" using command down' in command_text
    assert "key code 51" in command_text
    assert "key code 36" not in command_text


def focus_visual_result() -> VisualDetectionResult:
    return VisualDetectionResult(
        backend_available=True,
        screenshot_captured=True,
        window_bounds=(0, 0, 800, 600),
        safe_region_bounds=(144, 390, 560, 210),
        placeholder_detection_backend_available=True,
        placeholder_found=True,
        placeholder_bbox=(200, 460, 300, 80),
        placeholder_confidence=1.0,
        plus_button_found=True,
        plus_button_bbox=(80, 520, 40, 40),
        plus_button_confidence=1.0,
        selected_strategy="visual_placeholder_anchor",
        computed_click_point=(350, 500),
        click_point_safe=True,
    )


def test_focus_target_candidates_include_placeholder_plus_and_composer_band():
    detector = CodexUIDetector(runner=SequenceRunner([]), sleep_fn=lambda _: None)

    candidates = detector.build_focus_target_candidates(pyautogui_target(), focus_visual_result())
    names = [candidate.name for candidate in candidates]

    assert "placeholder_center" in names
    assert "placeholder_center_left" in names
    assert "placeholder_center_right" in names
    assert "plus_anchor_y_offset_50" in names
    assert "plus_anchor_y_offset_70" not in names
    assert "plus_anchor_y_offset_90" not in names
    assert "plus_anchor_y_offset_110" not in names
    assert "composer_band_center_left" in names
    assert "composer_band_placeholder_area" in names
    assert "composer_band_above_plus" in names


def test_focus_target_owner_reviewed_unsafe_candidate_is_rejected():
    detector = CodexUIDetector(runner=SequenceRunner([]), sleep_fn=lambda _: None)
    owner_target = replace(
        pyautogui_target(),
        owner_reviewed_focus_candidates=({"name": "bad", "x_ratio": 0.99, "y_ratio": 0.01},),
    )

    candidates = detector.build_focus_target_candidates(owner_target, focus_visual_result())
    owner = [candidate for candidate in candidates if candidate.family == "owner_reviewed"]

    assert owner
    assert owner[0].name == "owner_reviewed:bad"
    assert owner[0].safe is False
    assert owner[0].rejection_reason == "outside safe composer band"


def test_focus_target_owner_reviewed_main_window_basis_is_bounded():
    detector = CodexUIDetector(runner=SequenceRunner([]), sleep_fn=lambda _: None)
    owner_target = replace(
        pyautogui_target(),
        owner_reviewed_focus_candidates=(
            {
                "name": "composer_text_mid",
                "basis": "main_window",
                "x_ratio": 0.55,
                "y_ratio": 0.78,
            },
        ),
    )

    candidates = detector.build_focus_target_candidates(owner_target, focus_visual_result())
    owner = [candidate for candidate in candidates if candidate.name == "owner_reviewed:composer_text_mid"]

    assert owner
    assert owner[0].click_point == (440, 468)
    assert owner[0].safe
    assert owner[0].source == "owner_reviewed_focus_candidates:main_window"


def test_focus_target_owner_reviewed_plus_anchor_basis_rejects_plus_overlap():
    detector = CodexUIDetector(runner=SequenceRunner([]), sleep_fn=lambda _: None)
    owner_target = replace(
        pyautogui_target(),
        owner_reviewed_focus_candidates=(
            {
                "name": "bad_plus_center",
                "basis": "plus_anchor",
                "x_offset": 0,
                "y_offset": 0,
            },
        ),
    )

    candidates = detector.build_focus_target_candidates(owner_target, focus_visual_result())
    owner = [candidate for candidate in candidates if candidate.name == "owner_reviewed:bad_plus_center"]

    assert owner
    assert owner[0].click_point == (100, 540)
    assert owner[0].safe is False
    assert owner[0].rejection_reason == "inside plus button bbox"


def test_focus_target_test_types_one_character_and_cleans_with_backspace(tmp_path):
    writes: list[str] = []
    presses: list[str] = []
    visual = FakeVisualDetector(
        VisualDetectionResult(
            backend_available=True,
            screenshot_captured=True,
            window_bounds=(0, 0, 800, 600),
            safe_region_bounds=(144, 390, 560, 210),
            selected_strategy="none",
            computed_click_point=None,
            click_point_safe=False,
        ),
        marker_result=[
            marker_result(found=True),
            marker_result(found=False),
            marker_result(found=False),
            marker_result(found=False),
            marker_result(found=False),
            marker_result(found=False),
        ],
    )
    runner = SequenceRunner(
        [
            (0, "", ""),
            (0, "Codex\n", ""),
            (0, "0\n0\n800\n600\n", ""),
            (0, "0\n0\n800\n600\n", ""),
            (0, "0\n0\n800\n600\n", ""),
            (0, "0\n0\n800\n600\n", ""),
            (0, "0\n0\n800\n600\n", ""),
            (0, "0\n0\n800\n600\n", ""),
            (0, "0\n0\n800\n600\n", ""),
            (0, "0\n0\n800\n600\n", ""),
            (0, "0\n0\n800\n600\n", ""),
        ]
    )
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=visual,  # type: ignore[arg-type]
        pyautogui_clicker=lambda _x, _y: None,
        pyautogui_writer=lambda text, interval=0.0: writes.append(text),
        pyautogui_presser=lambda key: presses.append(key),
    )

    result = detector.run_focus_target_test(
        pyautogui_target(),
        logs_dir=tmp_path,
        click_backend="pyautogui",
    )

    assert result.selected_candidate_name == "composer_band_center_left"
    assert writes == ["x", "x", "x"]
    assert presses == ["backspace", "backspace", "backspace"]
    assert "enter" not in presses
    assert result.comparison_json_path is not None
    assert result.comparison_ocr_text_path is not None


def test_focus_target_test_no_success_means_no_selected_target(tmp_path):
    visual = FakeVisualDetector(
        VisualDetectionResult(
            backend_available=True,
            screenshot_captured=True,
            window_bounds=(0, 0, 800, 600),
            safe_region_bounds=(144, 390, 560, 210),
            selected_strategy="none",
            computed_click_point=None,
            click_point_safe=False,
        ),
        marker_result=[
            marker_result(found=False),
            marker_result(found=False),
            marker_result(found=False),
            marker_result(found=False),
            marker_result(found=False),
            marker_result(found=False),
        ],
    )
    runner = SequenceRunner(
        [
            (0, "", ""),
            (0, "Codex\n", ""),
            (0, "0\n0\n800\n600\n", ""),
            (0, "0\n0\n800\n600\n", ""),
            (0, "0\n0\n800\n600\n", ""),
            (0, "0\n0\n800\n600\n", ""),
            (0, "0\n0\n800\n600\n", ""),
            (0, "0\n0\n800\n600\n", ""),
            (0, "0\n0\n800\n600\n", ""),
            (0, "0\n0\n800\n600\n", ""),
            (0, "0\n0\n800\n600\n", ""),
        ]
    )
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=visual,  # type: ignore[arg-type]
        pyautogui_clicker=lambda _x, _y: None,
        pyautogui_writer=lambda _text, interval=0.0: None,
        pyautogui_presser=lambda _key: None,
    )

    result = detector.run_focus_target_test(
        pyautogui_target(),
        logs_dir=tmp_path,
        click_backend="pyautogui",
    )

    assert result.selected_candidate_name is None
    assert all(attempt.marker_found is False for attempt in result.attempts)


def test_system_events_paste_remains_available_when_explicitly_selected():
    clicks: list[tuple[int, int]] = []
    hotkeys: list[tuple[str, ...]] = []
    visual = ready_paste_visual(
        marker_result=marker_result(found=None, available=False, error="OCR unavailable"),
    )
    runner = SequenceRunner(
        paste_test_outputs(
            click_backend="pyautogui",
            paste_backend="system_events",
            after_paste_snapshot="Codex\nERROR\nnot authorized\n\n\n\n",
        )
    )
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=visual,  # type: ignore[arg-type]
        pyautogui_clicker=lambda x, y: clicks.append((x, y)),
        pyautogui_hotkeyer=lambda *keys: hotkeys.append(tuple(keys)),
    )

    result = detector.run_paste_test(
        pyautogui_target(),
        clipboard=FakeClipboard(),
        paste_backend="system_events",
    )
    command_text = "\n".join(" ".join(command) for command in runner.commands)

    assert result.paste_backend == "system_events"
    assert clicks == [(271, 451)]
    assert hotkeys == []
    assert 'keystroke "v" using command down' in command_text
    assert "key code 36" not in command_text


def test_system_events_click_requires_explicit_backend_selection():
    runner = SequenceRunner(
        [
            (0, "Codex\n", ""),
            (0, "Codex\n", ""),
            (0, "", ""),
            (0, snapshot(role="AXTextArea", description="Prompt input"), ""),
        ]
    )
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=FakeVisualDetector(visual_plus_result()),  # type: ignore[arg-type]
        pyautogui_clicker=lambda _x, _y: None,
    )

    result = detector.click_visual_input(pyautogui_target(), click_backend="system_events")
    command_text = "\n".join(" ".join(command) for command in runner.commands)

    assert result.succeeded
    assert result.click_backend == "system_events"
    assert "click at {271, 451}" in command_text


def test_missing_pyautogui_reports_clear_error(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pyautogui":
            raise ModuleNotFoundError("No module named 'pyautogui'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(
        "agent_bridge.gui.codex_ui_detector.importlib.util.find_spec",
        lambda name: None if name == "pyautogui" else object(),
    )
    runner = SequenceRunner(
        [
            (0, "Codex\n", ""),
            (0, "Codex\n", ""),
            (0, "Codex\n", ""),
        ]
    )
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        visual_detector=FakeVisualDetector(visual_plus_result()),  # type: ignore[arg-type]
    )

    result = detector.click_visual_input(pyautogui_target())

    assert not result.succeeded
    assert result.click_backend == "pyautogui"
    assert result.pyautogui_available is False
    assert "pyautogui is not installed" in (result.error or "")


def test_window_bounded_visual_state_placeholder_proceeds_immediately():
    runner = SequenceRunner(
        [
            (0, "", ""),
            (0, "Codex\n", ""),
            (0, "100\n200\n800\n600\n", ""),
        ]
    )
    visual = FakeVisualDetector(visual_placeholder_result())
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        monotonic_fn=lambda: 0.0,
        visual_detector=visual,  # type: ignore[arg-type]
    )

    result = detector.wait_for_visual_composer_ready(idle_target())

    assert result.should_proceed
    assert not result.should_overwrite
    assert result.selected_strategy == "visual_placeholder_immediate"
    assert result.placeholder_visible is True
    assert visual.calls[0]["window_bounds"] == (100, 200, 800, 600)


def test_window_bounded_visual_state_polls_every_configured_interval():
    runner = SequenceRunner(
        [
            (0, "", ""),
            (0, "Codex\n", ""),
            (0, "100\n200\n800\n600\n", ""),
            (0, "", ""),
            (0, "Codex\n", ""),
            (0, "100\n200\n800\n600\n", ""),
        ]
    )
    sleep_intervals: list[float] = []
    times = iter([0.0, 0.0, 0.0, 0.0, 10.0])
    visual = FakeVisualDetector([visual_busy_result(), visual_placeholder_result()])
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda value: sleep_intervals.append(value),
        monotonic_fn=lambda: next(times),
        visual_detector=visual,  # type: ignore[arg-type]
    )

    result = detector.wait_for_visual_composer_ready(
        idle_target(
            busy_placeholder_wait_timeout_seconds=30,
            busy_placeholder_poll_interval_seconds=10,
        )
    )

    assert result.should_proceed
    assert result.poll_count == 1
    assert sleep_intervals == [10]
    assert len(visual.calls) == 2
    assert all(call["window_bounds"] == (100, 200, 800, 600) for call in visual.calls)


def test_visual_state_timeout_overwrite_selects_plus_anchor_fallback():
    runner = SequenceRunner(
        [
            (0, "", ""),
            (0, "Codex\n", ""),
            (0, "100\n200\n800\n600\n", ""),
        ]
    )
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        monotonic_fn=lambda: 0.0,
        visual_detector=FakeVisualDetector(visual_busy_result()),  # type: ignore[arg-type]
    )

    result = detector.wait_for_visual_composer_ready(
        idle_target(busy_placeholder_wait_timeout_seconds=0, on_busy_timeout="overwrite")
    )

    assert result.busy_timeout_action == "overwrite"
    assert result.should_proceed
    assert result.should_overwrite
    assert result.plus_anchor_found
    assert result.selected_strategy == "visual_plus_anchor_overwrite"


def test_visual_state_timeout_abort_stops_safely():
    runner = SequenceRunner(
        [
            (0, "", ""),
            (0, "Codex\n", ""),
            (0, "100\n200\n800\n600\n", ""),
        ]
    )
    detector = CodexUIDetector(
        runner=runner,
        sleep_fn=lambda _: None,
        monotonic_fn=lambda: 0.0,
        visual_detector=FakeVisualDetector(visual_busy_result()),  # type: ignore[arg-type]
    )

    result = detector.wait_for_visual_composer_ready(
        idle_target(busy_placeholder_wait_timeout_seconds=0, on_busy_timeout="abort")
    )

    assert result.busy_timeout_action == "abort"
    assert result.should_abort
    assert not result.should_proceed
    assert result.selected_strategy == "visual_busy_timeout_abort"
