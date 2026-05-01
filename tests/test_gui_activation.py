from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

import agent_bridge.cli as cli_module
import agent_bridge.gui.gui_automation as gui_automation
from agent_bridge.core.event_log import EventLog
from agent_bridge.gui.asset_state_machine import (
    VisualAssetKind,
    VisualAssetMatch,
    VisualAssetTemplateDiagnostic,
    VisualGuiState,
    VisualStateDetection,
)
from agent_bridge.gui.chatgpt_mac_response_capture import ChatGPTMacResponseCaptureResult
from agent_bridge.gui.chatgpt_state_machine import ComposerTextVerification
from agent_bridge.gui.clipboard import Clipboard
from agent_bridge.gui.codex_ui_detector import LocalAgentFocusResult
from agent_bridge.gui.gui_automation import (
    GuiAutomationAdapter,
    MacOSSystemEventsGuiAdapter,
    PMSubmitReadyCheck,
    extract_pm_prompt_sentinel,
    paste_text_matches_expected,
    raw_key_leak_suspected,
)
from agent_bridge.gui.macos_apps import (
    CHATGPT_CHROME_APP_TARGET,
    PM_ASSISTANT_TARGET,
    NATIVE_CHATGPT_MAC_BUNDLE_ID,
    MacOSAppActivator,
    ManualStageTarget,
    ensure_native_chatgpt_mac_target,
    load_gui_targets,
)
from agent_bridge.gui.chatgpt_mac_native import diagnose_chatgpt_app_targets
from agent_bridge.gui.report_roundtrip import (
    ReportRoundtripConfig,
    ReportRoundtripError,
    run_report_roundtrip,
)


def completed(command: list[str], returncode: int, stderr: str = ""):
    return subprocess.CompletedProcess(command, returncode, stdout="", stderr=stderr)


class SequenceRunner:
    def __init__(self, returncodes: list[int]):
        self.returncodes = returncodes
        self.commands: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.commands.append(list(command))
        returncode = self.returncodes.pop(0)
        return completed(list(command), returncode, stderr=f"failed:{returncode}")


class FakeClipboard(Clipboard):
    def __init__(self, text: str):
        self.text = text

    def copy_text(self, text: str) -> None:
        self.text = text

    def read_text(self) -> str:
        return self.text


class FakeWindowDetector:
    def __init__(self, active_app: str = "Codex"):
        self.active_app = active_app
        self.window_bounds_calls = 0

    def frontmost_app(self) -> str:
        return self.active_app

    def window_bounds(self, _target: ManualStageTarget) -> tuple[int, int, int, int]:
        self.window_bounds_calls += 1
        return (10, 20, 300, 200)


def verified_paste_content():
    return SimpleNamespace(verified=True, raw_key_leak_suspected=False, failure_reason=None)


def codex_detection(state: VisualGuiState) -> VisualStateDetection:
    matched_asset = {
        VisualGuiState.IDLE: "assets/gui/codex/codex_send_disabled_button_light.png",
        VisualGuiState.COMPOSER_HAS_TEXT: "assets/gui/codex/codex_send_button_light.png",
        VisualGuiState.RUNNING: "assets/gui/codex/codex_stop_button_light.png",
    }.get(state)
    return VisualStateDetection(
        selected_app="Codex",
        asset_profile="codex",
        window_bounds=(10, 20, 800, 600),
        safe_region_bounds=(10, 400, 780, 200),
        screenshot_captured=True,
        backend_available=True,
        matched_state=state,
        matched_asset_path=matched_asset,
        matched_bbox=(700, 520, 38, 39) if matched_asset else None,
        confidence=0.99 if matched_asset else None,
        plus_anchor_found=True,
        plus_anchor_bbox=(100, 520, 27, 25),
        plus_anchor_confidence=0.99,
        computed_composer_click_point=(113, 496),
        composer_click_point_safe=True,
    )


def pm_ambiguous_send_stop_detection(
    *,
    send_confidence: float = 0.963,
    stop_confidence: float = 0.895,
    send_bbox: tuple[int, int, int, int] = (250, 170, 40, 43),
    stop_bbox: tuple[int, int, int, int] = (250, 169, 40, 44),
    window_bounds: tuple[int, int, int, int] = (10, 20, 300, 200),
) -> VisualStateDetection:
    send = VisualAssetMatch(
        asset_kind=VisualAssetKind.SEND,
        state=VisualGuiState.COMPOSER_HAS_TEXT,
        template_path="assets/gui/chatgpt_mac/chatgpt_mac_send_button_light.png",
        bbox=send_bbox,
        confidence=send_confidence,
        template_size=(send_bbox[2], send_bbox[3]),
        appearance_score=10.0,
    )
    stop = VisualAssetMatch(
        asset_kind=VisualAssetKind.STOP,
        state=VisualGuiState.RUNNING,
        template_path="assets/gui/chatgpt_mac/chatgpt_mac_stop_button_light.png",
        bbox=stop_bbox,
        confidence=stop_confidence,
        template_size=(stop_bbox[2], stop_bbox[3]),
        appearance_score=18.0,
    )
    return VisualStateDetection(
        selected_app="ChatGPT",
        asset_profile="chatgpt_mac",
        window_bounds=window_bounds,
        safe_region_bounds=(10, 120, 300, 100),
        screenshot_captured=True,
        backend_available=True,
        matched_state=VisualGuiState.AMBIGUOUS,
        matched_asset_path=send.template_path,
        matched_asset_kind=VisualAssetKind.SEND,
        matched_bbox=send.bbox,
        confidence=send.confidence,
        state_ambiguous=True,
        state_selection_reason="ambiguous_multiple_state_matches:COMPOSER_HAS_TEXT=0.963,RUNNING=0.895",
        plus_anchor_found=True,
        plus_anchor_bbox=(30, 185, 27, 27),
        plus_anchor_confidence=0.95,
        computed_composer_click_point=(43, 158),
        composer_click_point_safe=True,
        matches=(send, stop),
        template_diagnostics=(
            VisualAssetTemplateDiagnostic(
                asset_kind=VisualAssetKind.SEND,
                state=VisualGuiState.COMPOSER_HAS_TEXT,
                template_path=send.template_path,
                template_exists=True,
                search_region_bounds=(220, 130, 80, 80),
                original_template_size=(40, 43),
                template_size=(40, 43),
                selected_scale=1.0,
                best_match_bbox=send.bbox,
                best_match_confidence=send.confidence,
                appearance_score=10.0,
                threshold=0.7,
                accepted=True,
                configured_threshold=0.85,
                effective_threshold=0.7,
                threshold_cap_applied=True,
            ),
            VisualAssetTemplateDiagnostic(
                asset_kind=VisualAssetKind.STOP,
                state=VisualGuiState.RUNNING,
                template_path=stop.template_path,
                template_exists=True,
                search_region_bounds=(220, 130, 80, 80),
                original_template_size=(40, 44),
                template_size=(40, 44),
                selected_scale=1.0,
                best_match_bbox=stop.bbox,
                best_match_confidence=stop.confidence,
                appearance_score=18.0,
                threshold=0.7,
                accepted=True,
                configured_threshold=0.85,
                effective_threshold=0.7,
                threshold_cap_applied=True,
            ),
        ),
        error="Visual state is ambiguous; paste/submit is blocked.",
    )


def pm_submit_control_check(
    *,
    click_point: tuple[int, int] | None = (270, 191),
    click_point_safe: bool = True,
    decision_reason: str = "pm_submit_ready_send_detected",
    confidence: float | None = 0.963,
) -> PMSubmitReadyCheck:
    return PMSubmitReadyCheck(
        ready=click_point is not None and click_point_safe,
        decision_reason=decision_reason,
        matched_asset_path="assets/gui/chatgpt_mac/chatgpt_mac_send_button_light.png"
        if click_point is not None
        else None,
        confidence=confidence,
        configured_threshold=0.85,
        effective_threshold=0.7,
        bbox=(250, 170, 40, 43) if click_point is not None else None,
        click_point=click_point,
        click_point_safe=click_point_safe,
    )


def chatgpt_mac_detection_without_plus() -> VisualStateDetection:
    return VisualStateDetection(
        selected_app="ChatGPT",
        asset_profile="chatgpt_mac",
        window_bounds=(0, 33, 735, 432),
        safe_region_bounds=(14, 292, 705, 172),
        plus_search_region_bounds=(147, 421, 183, 43),
        screenshot_captured=True,
        backend_available=True,
        matched_state=VisualGuiState.UNKNOWN,
        plus_anchor_found=False,
        computed_composer_click_point=None,
        composer_click_point_safe=False,
        template_diagnostics=(
            VisualAssetTemplateDiagnostic(
                asset_kind=VisualAssetKind.PLUS,
                state=None,
                template_path="assets/gui/chatgpt_mac/chatgpt_mac_plus_button_light.png",
                template_exists=True,
                search_region_bounds=(147, 421, 183, 43),
                original_template_size=(30, 31),
                template_size=(21, 21),
                selected_scale=0.7,
                best_match_bbox=(282, 421, 21, 21),
                best_match_confidence=0.57,
                appearance_score=15.8,
                threshold=0.58,
                accepted=False,
                rejection_reason="confidence_below_threshold",
            ),
        ),
    )


def chatgpt_mac_detection_with_accepted_and_rejected_templates() -> VisualStateDetection:
    return VisualStateDetection(
        selected_app="ChatGPT",
        asset_profile="chatgpt_mac",
        window_bounds=(0, 33, 735, 432),
        safe_region_bounds=(14, 292, 705, 172),
        plus_search_region_bounds=(147, 421, 183, 43),
        screenshot_captured=True,
        backend_available=True,
        matched_state=VisualGuiState.COMPOSER_HAS_TEXT,
        matched_asset_path="assets/gui/chatgpt_mac/chatgpt_mac_send_button_light.png",
        matched_bbox=(26, 414, 32, 34),
        confidence=0.91,
        plus_anchor_found=True,
        plus_anchor_bbox=(282, 421, 21, 21),
        plus_anchor_confidence=0.62,
        computed_composer_click_point=(292, 361),
        composer_click_point_safe=True,
        template_diagnostics=(
            VisualAssetTemplateDiagnostic(
                asset_kind=VisualAssetKind.PLUS,
                state=None,
                template_path="assets/gui/chatgpt_mac/chatgpt_mac_plus_button_light.png",
                template_exists=True,
                search_region_bounds=(147, 421, 183, 43),
                original_template_size=(30, 31),
                template_size=(21, 21),
                selected_scale=0.7,
                best_match_bbox=(282, 421, 21, 21),
                best_match_confidence=0.62,
                appearance_score=15.8,
                threshold=0.58,
                accepted=True,
                rejection_reason=None,
            ),
            VisualAssetTemplateDiagnostic(
                asset_kind=VisualAssetKind.SEND,
                state=VisualGuiState.COMPOSER_HAS_TEXT,
                template_path="assets/gui/chatgpt_mac/chatgpt_mac_send_button_light.png",
                template_exists=True,
                search_region_bounds=(14, 292, 705, 172),
                original_template_size=(40, 43),
                template_size=(32, 34),
                selected_scale=0.8,
                best_match_bbox=(26, 414, 32, 34),
                best_match_confidence=0.91,
                appearance_score=20.0,
                threshold=0.85,
                accepted=True,
                rejection_reason=None,
            ),
            VisualAssetTemplateDiagnostic(
                asset_kind=VisualAssetKind.SEND,
                state=VisualGuiState.COMPOSER_HAS_TEXT,
                template_path="assets/gui/chatgpt_mac/chatgpt_mac_send_button_dark.png",
                template_exists=True,
                search_region_bounds=(14, 292, 705, 172),
                original_template_size=(42, 45),
                template_size=(46, 49),
                selected_scale=1.1,
                best_match_bbox=(218, 415, 46, 49),
                best_match_confidence=0.42,
                appearance_score=131.0,
                threshold=0.85,
                accepted=False,
                rejection_reason="confidence_below_threshold",
            ),
        ),
    )


class FakeAssetStateDetector:
    def __init__(self, states: list[VisualGuiState]):
        self.detections = [codex_detection(state) for state in states]
        self.calls = 0

    def detect(self, **_kwargs):
        self.calls += 1
        if len(self.detections) > 1:
            return self.detections.pop(0)
        return self.detections[0]


class FakeCodexPasteDetector:
    def __init__(self):
        self.clicks = 0

    def wait_until_frontmost(self, *_args, **_kwargs) -> bool:
        return True

    def frontmost_app(self) -> str:
        return "Codex"

    def window_bounds(self, _target: ManualStageTarget) -> tuple[int, int, int, int]:
        return (10, 20, 800, 600)

    def focus_input(self, _target: ManualStageTarget):
        self.clicks += 1
        return LocalAgentFocusResult(
            succeeded=True,
            app_frontmost=True,
            window_bounds=(10, 20, 800, 600),
            fallback_click_point=(113, 496),
        )

    def click_visual_input(self, _target: ManualStageTarget, **_kwargs):
        return self.focus_input(_target)

    def click_direct_plus_anchor(self, _target: ManualStageTarget, **_kwargs):
        self.clicks += 1
        return LocalAgentFocusResult(
            succeeded=True,
            app_frontmost=True,
            window_bounds=(10, 20, 800, 600),
            fallback_click_point=(113, 496),
            plus_button_bbox=(100, 520, 27, 25),
            plus_button_center=(113, 532),
            click_backend="pyautogui",
        )


class ChangingBoundsCodexDetector(FakeCodexPasteDetector):
    def __init__(self, bounds: list[tuple[int, int, int, int]]):
        super().__init__()
        self.bounds = bounds
        self.index = 0

    def window_bounds(self, _target: ManualStageTarget) -> tuple[int, int, int, int]:
        value = self.bounds[min(self.index, len(self.bounds) - 1)]
        self.index += 1
        return value


class FakeActivator:
    def __init__(self, detector: FakeWindowDetector):
        self.detector = detector
        self.calls: list[tuple[str, str | None]] = []

    def activate(
        self,
        app_name: str,
        *,
        app_path: str | None = None,
        bundle_id: str | None = None,
    ) -> None:
        del app_path
        self.calls.append((app_name, bundle_id))
        self.detector.active_app = app_name


def test_osascript_success():
    runner = SequenceRunner([0])
    activator = MacOSAppActivator(runner=runner)

    result = activator.activate_with_result("ChatGPT")

    assert result.succeeded
    assert result.winning_strategy == "osascript"
    assert len(runner.commands) == 1
    assert runner.commands[0][0] == "osascript"


def test_osascript_failure_then_open_app_success():
    runner = SequenceRunner([1, 0])
    activator = MacOSAppActivator(runner=runner)

    result = activator.activate_with_result("ChatGPT")

    assert result.succeeded
    assert result.winning_strategy == "open-app-name"
    assert [command[0] for command in runner.commands] == ["osascript", "open"]
    assert runner.commands[1] == ["open", "-a", "ChatGPT"]


def test_all_activation_strategies_fail():
    runner = SequenceRunner([1, 1, 1, 1])
    activator = MacOSAppActivator(runner=runner)

    result = activator.activate_with_result(
        "ChatGPT",
        app_path="/Applications/ChatGPT.app",
        bundle_id="com.openai.chat",
    )

    assert not result.succeeded
    assert [attempt.strategy for attempt in result.attempts] == [
        "osascript-bundle-id",
        "open-bundle-id",
        "open-app-path",
        "osascript-app-name-verified",
    ]


def test_app_path_and_bundle_id_config_are_respected(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "default.yaml").write_text(
        """
apps:
  pm_assistant:
    app_name: "ChatGPT"
    app_path: "/Applications/ChatGPT.app"
    bundle_id: "com.openai.chat"
""".lstrip(),
        encoding="utf-8",
    )

    target = load_gui_targets(config_dir).pm_assistant
    plan = MacOSAppActivator().activation_plan(
        target.app_name,
        app_path=target.app_path,
        bundle_id=target.bundle_id,
    )

    assert target.app_path == "/Applications/ChatGPT.app"
    assert target.bundle_id == "com.openai.chat"
    assert plan[0][0] == "osascript-bundle-id"
    assert plan[0][1] == (
        "osascript",
        "-e",
        'tell application id "com.openai.chat" to activate',
    )
    assert plan[1] == ("open-bundle-id", ("open", "-b", "com.openai.chat"))
    assert ("open-app-path", ("open", "/Applications/ChatGPT.app")) in plan
    assert plan[-1][0] == "osascript-app-name-verified"
    assert 'tell application "ChatGPT" to activate' in plan[-1][1][-1]
    assert 'frontBundle is "com.openai.chat"' in plan[-1][1][-1]


def test_asset_response_copy_reactivates_pm_and_redetects_when_codex_frontmost(monkeypatch):
    detector = FakeWindowDetector(active_app="Codex")
    activator = FakeActivator(detector)
    clipboard = FakeClipboard("before")
    target = ManualStageTarget(
        app_name="app_mode_loader",
        bundle_id="com.google.Chrome.app.test",
        backend="chatgpt_chrome_app_visual",
        profile="chatgpt_chrome_app",
        visual_asset_profile="chatgpt_chrome_app",
    )
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=clipboard,
        app_activator=activator,
        codex_ui_detector=detector,
    )
    adapter.active_target = target
    calls: list[tuple[str, object]] = []

    def fake_diagnose(**kwargs):
        calls.append(("diagnose", kwargs["window_bounds"]))
        kwargs["clipboard"].copy_text("```CODEX_NEXT_PROMPT\nnoop\n```")
        return ChatGPTMacResponseCaptureResult(
            target_app=target.app_name,
            asset_profile="chatgpt_chrome_app",
            window_bounds=kwargs["window_bounds"],
            search_region_bounds=(10, 20, 300, 200),
            screenshot_captured=True,
            backend_available=True,
            supported=True,
            copy_assets=("copy.png",),
            missing_copy_assets=(),
            scroll_assets=("scroll.png",),
            copy_button_found=True,
            copy_button_bbox=(20, 30, 16, 16),
            copy_button_click_point=(28, 38),
            copy_button_click_point_safe=True,
            copy_button_confidence=0.99,
            matched_asset_path="copy.png",
            copy_detection_attempt_count=1,
            capture_attempted=True,
            response_captured=True,
            response_length=len("```CODEX_NEXT_PROMPT\nnoop\n```"),
        )

    monkeypatch.setattr(gui_automation, "diagnose_chatgpt_mac_response_capture", fake_diagnose)

    response = adapter.copy_response_text()

    assert response == "```CODEX_NEXT_PROMPT\nnoop\n```"
    assert activator.calls == [("app_mode_loader", "com.google.Chrome.app.test")]
    assert detector.window_bounds_calls == 1
    assert calls == [("diagnose", (10, 20, 300, 200))]
    assert detector.frontmost_app() == "app_mode_loader"


def test_chatgpt_mac_default_target_has_native_bundle_id():
    target = load_gui_targets(Path("missing-config-dir")).pm_assistant
    plan = MacOSAppActivator().activation_plan(
        target.app_name,
        app_path=target.app_path,
        bundle_id=target.bundle_id,
    )

    assert target.bundle_id == NATIVE_CHATGPT_MAC_BUNDLE_ID
    assert plan[0][0] == "osascript-bundle-id"


def test_chatgpt_app_targets_select_native_and_reject_chrome():
    script_output = (
        "ChatGPT\tcom.openai.chat\ttrue\ttrue\t1\n"
        "ChatGPT\tcom.google.Chrome.app.fake\tfalse\ttrue\t1"
    )
    calls: list[list[str]] = []

    def runner(command, **kwargs):
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout=script_output, stderr="")

    result = diagnose_chatgpt_app_targets(
        target=ManualStageTarget(app_name="ChatGPT", backend="chatgpt_mac_visual"),
        runner=runner,
    )

    assert calls[0][0] == "osascript"
    assert result.native_available
    assert result.selected_bundle_id == NATIVE_CHATGPT_MAC_BUNDLE_ID
    assert result.chrome_pwa_candidates_rejected == 1
    assert result.candidates[0].selected
    assert result.candidates[1].rejected


def test_preflight_dry_run_does_not_activate(monkeypatch, tmp_path: Path):
    config_dir = tmp_path / "config"
    workspace = tmp_path / "workspace"
    config_dir.mkdir()
    (config_dir / "default.yaml").write_text("apps: {}\n", encoding="utf-8")
    monkeypatch.setattr(cli_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(cli_module, "WORKSPACE", workspace)

    result = CliRunner().invoke(cli_module.app, ["preflight-gui-apps", "--dry-run"])

    assert result.exit_code == 0
    assert "DRY RUN: activation skipped" in result.output
    assert "No paste, submit, Enter/Return" in result.output


def test_chatgpt_paste_waits_for_idle_empty_before_insertion(monkeypatch):
    calls: list[str] = []

    def fake_wait(*args, timeout_seconds, **kwargs):
        calls.append(f"wait:{timeout_seconds}")

    def fake_insert(*args, **kwargs):
        calls.append("insert")
        return ComposerTextVerification(
            selector="textarea",
            text_length=20,
            contains_expected_marker=True,
            button_state="send-button",
            active_element_summary="textarea#prompt-textarea",
        )

    monkeypatch.setattr(gui_automation, "wait_for_idle_empty_composer", fake_wait)
    monkeypatch.setattr(gui_automation, "insert_text_into_composer", fake_insert)

    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("AGENT_BRIDGE prompt"),
        app_activator=MacOSAppActivator(runner=SequenceRunner([0])),
    )
    adapter.active_target = ManualStageTarget(
        app_name="Google Chrome",
        backend="chrome_js",
        idle_empty_timeout_seconds=600,
    )

    adapter.paste_clipboard()

    assert calls == ["wait:600.0", "insert"]


def test_codex_local_agent_paste_uses_pyautogui_backend():
    hotkeys: list[tuple[str, ...]] = []
    window_detector = FakeWindowDetector()

    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("AGENT_BRIDGE_CODEX_PASTE_TEST_DO_NOT_SUBMIT"),
        app_activator=FakeActivator(window_detector),
        codex_ui_detector=FakeCodexPasteDetector(),  # type: ignore[arg-type]
        pyautogui_hotkeyer=lambda *keys: hotkeys.append(tuple(keys)),
    )
    adapter.active_target = ManualStageTarget(
        app_name="Codex",
        paste_backend="pyautogui",
    )

    adapter.paste_clipboard()

    assert hotkeys == [("command", "v")]


def test_codex_local_agent_paste_retry_stops_when_send_ready_detected():
    hotkeys: list[tuple[str, ...]] = []
    detector = FakeCodexPasteDetector()
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("LOCAL AGENT PROMPT"),
        app_activator=FakeActivator(FakeWindowDetector()),
        codex_ui_detector=detector,  # type: ignore[arg-type]
        asset_state_detector=FakeAssetStateDetector(
            [VisualGuiState.IDLE, VisualGuiState.COMPOSER_HAS_TEXT]
        ),  # type: ignore[arg-type]
        pyautogui_hotkeyer=lambda *keys: hotkeys.append(tuple(keys)),
        sleep_fn=lambda _seconds: None,
    )
    adapter.active_target = ManualStageTarget(
        app_name="Codex",
        paste_backend="pyautogui",
        visual_asset_profile="codex",
        focus_strategy="direct_plus_anchor",
        direct_plus_anchor_enabled=True,
    )

    assert adapter.paste_clipboard() is True

    assert hotkeys == [("command", "v")]
    assert detector.clicks == 1
    assert adapter.last_local_agent_paste_send_ready is None
    assert adapter.last_local_agent_paste_state_before == "IDLE"
    assert adapter.last_local_agent_paste_state_after is None


def test_codex_local_agent_paste_retry_tries_variants_in_order():
    hotkeys: list[tuple[str, ...]] = []
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("LOCAL AGENT PROMPT"),
        app_activator=FakeActivator(FakeWindowDetector()),
        codex_ui_detector=FakeCodexPasteDetector(),  # type: ignore[arg-type]
        asset_state_detector=FakeAssetStateDetector(
            [
                VisualGuiState.IDLE,
                VisualGuiState.IDLE,
                VisualGuiState.COMPOSER_HAS_TEXT,
            ]
        ),  # type: ignore[arg-type]
        pyautogui_hotkeyer=lambda *keys: hotkeys.append(tuple(keys)),
        sleep_fn=lambda _seconds: None,
    )
    adapter.active_target = ManualStageTarget(
        app_name="Codex",
        paste_backend="pyautogui",
        visual_asset_profile="codex",
        focus_strategy="direct_plus_anchor",
        direct_plus_anchor_enabled=True,
    )

    adapter.paste_clipboard()

    assert hotkeys == [("command", "v")]
    assert adapter.last_local_agent_paste_backend_success is True
    assert adapter.last_local_agent_paste_send_ready is None


def test_codex_local_agent_paste_retry_fails_when_state_remains_idle():
    hotkeys: list[tuple[str, ...]] = []
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("LOCAL AGENT PROMPT"),
        app_activator=FakeActivator(FakeWindowDetector()),
        codex_ui_detector=FakeCodexPasteDetector(),  # type: ignore[arg-type]
        asset_state_detector=FakeAssetStateDetector([VisualGuiState.IDLE]),  # type: ignore[arg-type]
        pyautogui_hotkeyer=lambda *keys: hotkeys.append(tuple(keys)),
        sleep_fn=lambda _seconds: None,
        local_agent_max_paste_attempts=1,
    )
    adapter.active_target = ManualStageTarget(
        app_name="Codex",
        paste_backend="pyautogui",
        visual_asset_profile="codex",
        focus_strategy="direct_plus_anchor",
        direct_plus_anchor_enabled=True,
    )

    assert adapter.paste_clipboard() is True

    assert hotkeys == [("command", "v")]
    assert all("typewrite" not in call for keys in hotkeys for call in keys)
    assert adapter.last_local_agent_paste_send_ready is None
    assert adapter.last_local_agent_paste_backend_success is True


def test_codex_local_agent_paste_debug_logs_attempt_index(tmp_path: Path):
    debug_log = tmp_path / "gui_actions_debug.jsonl"
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("LOCAL AGENT PROMPT"),
        app_activator=FakeActivator(FakeWindowDetector()),
        codex_ui_detector=FakeCodexPasteDetector(),  # type: ignore[arg-type]
        asset_state_detector=FakeAssetStateDetector(
            [VisualGuiState.IDLE, VisualGuiState.COMPOSER_HAS_TEXT]
        ),  # type: ignore[arg-type]
        pyautogui_hotkeyer=lambda *_keys: None,
        sleep_fn=lambda _seconds: None,
        debug_gui_actions_log=EventLog(debug_log),
        bridge_attempt_id="bridge_debug_test",
    )
    adapter.active_target = ManualStageTarget(
        app_name="Codex",
        paste_backend="pyautogui",
        visual_asset_profile="codex",
        focus_strategy="direct_plus_anchor",
        direct_plus_anchor_enabled=True,
    )

    adapter.paste_clipboard()

    records = [json.loads(line) for line in debug_log.read_text(encoding="utf-8").splitlines()]
    paste_attempt = next(
        record
        for record in records
        if record["metadata"]["action"] == "local_agent_paste_attempt_started"
    )
    action_names = [record["metadata"]["action"] for record in records]
    assert paste_attempt["metadata"]["bridge_attempt_id"] == "bridge_debug_test"
    assert paste_attempt["metadata"]["attempt_index"] == 1
    assert "local_agent_codex_focus_succeeded" in action_names
    assert "local_agent_paste_variant_succeeded" in action_names
    assert "local_agent_paste_attempt_completed" in action_names
    assert action_names.index("local_agent_codex_focus_succeeded") < action_names.index(
        "local_agent_paste_variant_attempted"
    )


def test_codex_local_agent_paste_logs_focus_succeeded_when_backend_fails(tmp_path: Path):
    debug_log = tmp_path / "gui_actions_debug.jsonl"

    def failing_hotkey(*_keys):
        raise RuntimeError("paste backend failed")

    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("LOCAL AGENT PROMPT"),
        app_activator=FakeActivator(FakeWindowDetector()),
        codex_ui_detector=FakeCodexPasteDetector(),  # type: ignore[arg-type]
        asset_state_detector=FakeAssetStateDetector([VisualGuiState.IDLE]),  # type: ignore[arg-type]
        pyautogui_hotkeyer=failing_hotkey,
        sleep_fn=lambda _seconds: None,
        debug_gui_actions_log=EventLog(debug_log),
        bridge_attempt_id="bridge_backend_fail",
        local_agent_max_paste_attempts=1,
    )
    adapter.active_target = ManualStageTarget(
        app_name="Codex",
        paste_backend="pyautogui",
        visual_asset_profile="codex",
        focus_strategy="direct_plus_anchor",
        direct_plus_anchor_enabled=True,
    )

    try:
        adapter.paste_clipboard()
    except Exception as error:
        assert "local_agent_paste_backend_failed" in str(error)
    else:
        raise AssertionError("Expected paste backend failure.")

    records = [json.loads(line) for line in debug_log.read_text(encoding="utf-8").splitlines()]
    action_names = [record["metadata"]["action"] for record in records]
    assert "local_agent_codex_focus_succeeded" in action_names
    assert "local_agent_focus_succeeded_but_paste_missing" in action_names
    assert action_names.index("local_agent_codex_focus_succeeded") < action_names.index(
        "local_agent_focus_succeeded_but_paste_missing"
    )


def test_debug_state_machine_logs_detection_transition_fields(tmp_path: Path):
    state_log = tmp_path / "gui_state_machine_debug.jsonl"
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("LOCAL AGENT PROMPT"),
        app_activator=FakeActivator(FakeWindowDetector()),
        codex_ui_detector=FakeCodexPasteDetector(),  # type: ignore[arg-type]
        asset_state_detector=FakeAssetStateDetector([VisualGuiState.IDLE]),  # type: ignore[arg-type]
        debug_state_machine_log=EventLog(state_log),
        bridge_attempt_id="bridge_state_debug",
    )
    target = ManualStageTarget(
        app_name="Codex",
        visual_asset_profile="codex",
    )

    adapter._detect_asset_state(target)

    records = [json.loads(line) for line in state_log.read_text(encoding="utf-8").splitlines()]
    detection = next(record for record in records if record["event_type"] == "gui_state_detection")
    metadata = detection["metadata"]
    assert metadata["bridge_attempt_id"] == "bridge_state_debug"
    assert metadata["detected_state"] == "IDLE"
    assert metadata["selected_state"] == "IDLE"
    assert metadata["transition"] == "START->IDLE"
    assert metadata["decision"] == "selected"
    assert metadata["selected_window_bounds"] == [10, 20, 800, 600]


def test_debug_state_machine_logs_window_bounds_refresh(tmp_path: Path):
    state_log = tmp_path / "gui_state_machine_debug.jsonl"
    detector = ChangingBoundsCodexDetector(
        [(10, 20, 800, 600), (30, 40, 900, 650)]
    )
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("LOCAL AGENT PROMPT"),
        app_activator=FakeActivator(FakeWindowDetector()),
        codex_ui_detector=detector,  # type: ignore[arg-type]
        asset_state_detector=FakeAssetStateDetector(
            [VisualGuiState.IDLE, VisualGuiState.COMPOSER_HAS_TEXT]
        ),  # type: ignore[arg-type]
        debug_state_machine_log=EventLog(state_log),
        bridge_attempt_id="bridge_window_refresh",
    )
    target = ManualStageTarget(app_name="Codex", visual_asset_profile="codex")

    adapter._detect_asset_state(target)
    adapter._detect_asset_state(target)

    records = [
        json.loads(line)
        for line in state_log.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["event_type"] == "gui_window_bounds_checked"
    ]
    assert len(records) == 2
    assert records[0]["metadata"]["window_bounds_changed"] is True
    assert records[0]["metadata"]["old_bounds"] is None
    assert records[0]["metadata"]["new_bounds"] == [10, 20, 800, 600]
    assert records[1]["metadata"]["window_bounds_changed"] is True
    assert records[1]["metadata"]["old_bounds"] == [10, 20, 800, 600]
    assert records[1]["metadata"]["new_bounds"] == [30, 40, 900, 650]
    assert records[1]["metadata"]["stale_coordinate_reused"] is False


def test_chatgpt_asset_paste_overwrites_after_idle_timeout(monkeypatch):
    actions: list[str] = []
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector()),
        sleep_fn=lambda _seconds: None,
    )
    adapter.active_target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_chrome_app_visual",
        visual_asset_profile="chatgpt_chrome_app",
        paste_backend="pyautogui",
    )

    monkeypatch.setattr(
        adapter,
        "_wait_for_asset_idle",
        lambda _target: SimpleNamespace(
            should_overwrite=True,
            asset_profile="chatgpt_chrome_app",
            final_state=VisualGuiState.COMPOSER_HAS_TEXT,
            poll_count=2,
            detection=codex_detection(VisualGuiState.IDLE),
        ),
    )
    monkeypatch.setattr(
        adapter,
        "_click_pm_asset_composer_for_paste",
        lambda *_args, **_kwargs: actions.append("click")
        or codex_detection(VisualGuiState.IDLE),
    )
    monkeypatch.setattr(
        adapter,
        "_select_all_local_agent_text",
        lambda _target: actions.append("select_all"),
    )
    monkeypatch.setattr(adapter, "_set_and_verify_pm_clipboard", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(adapter, "_paste_variants", lambda _target: ("command_v_hotkey",))
    monkeypatch.setattr(adapter, "_paste_variant", lambda *_args: actions.append("paste"))
    monkeypatch.setattr(
        adapter,
        "_verify_pm_paste_content",
        lambda *_args, **_kwargs: verified_paste_content(),
    )
    monkeypatch.setattr(
        adapter,
        "_detect_asset_state",
        lambda _target: codex_detection(VisualGuiState.COMPOSER_HAS_TEXT),
    )

    adapter.paste_clipboard()

    assert actions == ["click", "select_all", "paste"]


def test_chatgpt_asset_paste_succeeds_when_action_returns_even_if_state_remains_idle(
    monkeypatch,
):
    hotkeys: list[str] = []
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector()),
        sleep_fn=lambda _seconds: None,
    )
    adapter.active_target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_chrome_app_visual",
        visual_asset_profile="chatgpt_chrome_app",
        paste_backend="pyautogui",
        max_action_attempts=1,
    )
    monkeypatch.setattr(
        adapter,
        "_wait_for_asset_idle",
        lambda _target: SimpleNamespace(
            should_overwrite=False,
            asset_profile="chatgpt_chrome_app",
            final_state=VisualGuiState.IDLE,
            poll_count=1,
            detection=codex_detection(VisualGuiState.IDLE),
        ),
    )
    monkeypatch.setattr(
        adapter,
        "_click_pm_asset_composer_for_paste",
        lambda *_args, **_kwargs: codex_detection(VisualGuiState.IDLE),
    )
    monkeypatch.setattr(adapter, "_set_and_verify_pm_clipboard", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(adapter, "_paste_variants", lambda _target: ("command_v_hotkey",))
    monkeypatch.setattr(adapter, "_paste_variant", lambda *_args: hotkeys.append("paste"))
    monkeypatch.setattr(
        adapter,
        "_detect_asset_state",
        lambda _target: codex_detection(VisualGuiState.IDLE),
    )

    assert adapter.paste_clipboard() is True

    assert hotkeys == ["paste"]
    assert adapter.last_pm_paste_send_ready is None
    assert adapter.last_pm_paste_backend_success is True


def test_visual_pm_uses_menu_paste_accessibility_backend_by_default():
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector()),
    )
    target = ensure_native_chatgpt_mac_target(PM_ASSISTANT_TARGET)

    assert target.paste_backend == "menu_paste_accessibility"
    assert adapter._paste_variants(target) == (
        "menu_paste_accessibility",
        "system_events_key_code_v_command",
    )
    assert CHATGPT_CHROME_APP_TARGET.paste_backend == "menu_paste_accessibility"
    assert adapter._paste_variants(CHATGPT_CHROME_APP_TARGET) == (
        "menu_paste_accessibility",
        "system_events_key_code_v_command",
    )


def test_chatgpt_mac_paste_backend_is_profile_config_driven():
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector()),
    )
    target = ensure_native_chatgpt_mac_target(
        ManualStageTarget(
            app_name="ChatGPT",
            backend="chatgpt_mac_visual",
            profile="chatgpt_mac",
            visual_asset_profile="chatgpt_mac",
            paste_backend="pyautogui",
        )
    )

    assert target.paste_backend == "pyautogui"
    assert adapter._paste_variants(target) == ()


def test_chatgpt_asset_paste_retry_reuses_successful_plus_focus(monkeypatch):
    actions: list[str] = []
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector()),
        sleep_fn=lambda _seconds: None,
    )
    adapter.active_target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
        paste_backend="accessibility_set_focused_value",
        max_action_attempts=3,
    )
    monkeypatch.setattr(
        adapter,
        "_wait_for_asset_idle",
        lambda _target: SimpleNamespace(
            should_overwrite=False,
            asset_profile="chatgpt_mac",
            final_state=VisualGuiState.IDLE,
            poll_count=1,
            detection=codex_detection(VisualGuiState.IDLE),
        ),
    )
    monkeypatch.setattr(
        adapter,
        "_click_pm_asset_composer_for_paste",
        lambda *_args, **_kwargs: actions.append("focus")
        or codex_detection(VisualGuiState.IDLE),
    )
    monkeypatch.setattr(adapter, "_set_and_verify_pm_clipboard", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(adapter, "_paste_variant", lambda *_args: actions.append("paste"))
    monkeypatch.setattr(
        adapter,
        "_verify_pm_paste_content",
        lambda *_args, **_kwargs: verified_paste_content(),
    )
    monkeypatch.setattr(adapter, "_detect_asset_state", lambda _target: codex_detection(VisualGuiState.IDLE))

    assert adapter.paste_clipboard() is True

    assert actions == ["focus", "paste"]
    assert adapter.last_pm_paste_send_ready is None
    assert adapter.last_pm_paste_backend_success is True


def test_chatgpt_asset_paste_retry_stops_after_first_success(monkeypatch):
    actions: list[str] = []
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector()),
        sleep_fn=lambda _seconds: None,
    )
    adapter.active_target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
        paste_backend="accessibility_set_focused_value",
        max_action_attempts=3,
    )
    monkeypatch.setattr(
        adapter,
        "_wait_for_asset_idle",
        lambda _target: SimpleNamespace(
            should_overwrite=False,
            asset_profile="chatgpt_mac",
            final_state=VisualGuiState.IDLE,
            poll_count=1,
            detection=codex_detection(VisualGuiState.IDLE),
        ),
    )
    monkeypatch.setattr(
        adapter,
        "_click_pm_asset_composer_for_paste",
        lambda *_args, **_kwargs: actions.append("focus")
        or codex_detection(VisualGuiState.IDLE),
    )
    monkeypatch.setattr(adapter, "_set_and_verify_pm_clipboard", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(adapter, "_paste_variant", lambda *_args: actions.append("paste"))
    monkeypatch.setattr(
        adapter,
        "_verify_pm_paste_content",
        lambda *_args, **_kwargs: verified_paste_content(),
    )
    monkeypatch.setattr(
        adapter,
        "_detect_asset_state",
        lambda _target: codex_detection(VisualGuiState.COMPOSER_HAS_TEXT),
    )

    assert adapter.paste_clipboard() is True

    assert actions == ["focus", "paste"]
    assert adapter.last_pm_paste_send_ready is None
    assert adapter.last_pm_paste_backend_success is True


def test_pm_initial_composer_with_current_sentinel_submits_without_repaste(
    tmp_path: Path,
    monkeypatch,
):
    prompt = "PM prompt\nAGENT_BRIDGE_PM_PROMPT_SENTINEL: bridge_preexisting"
    action_log = tmp_path / "gui_actions_debug.jsonl"
    actions: list[str] = []
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard(prompt),
        app_activator=FakeActivator(FakeWindowDetector(active_app="ChatGPT")),
        sleep_fn=lambda _seconds: None,
        debug_gui_actions_log=EventLog(action_log),
        bridge_attempt_id="bridge_preexisting",
    )
    adapter.active_target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
        paste_backend="menu_paste_accessibility",
        max_action_attempts=1,
    )
    monkeypatch.setattr(
        adapter,
        "_wait_for_asset_idle",
        lambda *_args, **_kwargs: SimpleNamespace(
            should_overwrite=False,
            asset_profile="chatgpt_mac",
            final_state=VisualGuiState.COMPOSER_HAS_TEXT,
            poll_count=1,
            detection=codex_detection(VisualGuiState.COMPOSER_HAS_TEXT),
        ),
    )
    monkeypatch.setattr(
        adapter,
        "_click_pm_asset_composer_for_paste",
        lambda *_args, **_kwargs: actions.append("focus")
        or codex_detection(VisualGuiState.COMPOSER_HAS_TEXT),
    )
    monkeypatch.setattr(
        adapter,
        "_copy_pm_composer_text_for_verification",
        lambda *_args, **_kwargs: prompt,
    )
    monkeypatch.setattr(adapter, "_paste_variant", lambda *_args: actions.append("paste"))
    monkeypatch.setattr(
        adapter,
        "_detect_asset_state",
        lambda _target: codex_detection(VisualGuiState.COMPOSER_HAS_TEXT),
    )

    assert adapter.paste_clipboard() is True

    assert actions == ["focus"]
    assert adapter.last_pm_paste_backend_success is True
    assert adapter.last_pm_paste_content_verified is True
    assert adapter.last_pm_prompt_sentinel_found is True
    assert adapter.last_pm_paste_send_ready is None
    records = [json.loads(line) for line in action_log.read_text(encoding="utf-8").splitlines()]
    actions_logged = [record["metadata"].get("action") for record in records]
    assert "pm_initial_composer_state" in actions_logged
    assert "pm_preexisting_text_check_started" in actions_logged
    assert "pm_preexisting_text_check_result" in actions_logged
    assert "pm_prompt_already_present_in_composer" in actions_logged
    assert "send_ready_check_skipped_by_policy" in actions_logged


def test_pm_initial_composer_without_current_sentinel_overwrites(monkeypatch):
    prompt = "PM prompt\nAGENT_BRIDGE_PM_PROMPT_SENTINEL: bridge_overwrite"
    copied_texts = iter(["stale unrelated text", prompt])
    actions: list[str] = []
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard(prompt),
        app_activator=FakeActivator(FakeWindowDetector(active_app="ChatGPT")),
        sleep_fn=lambda _seconds: None,
    )
    adapter.active_target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
        paste_backend="menu_paste_accessibility",
        max_action_attempts=1,
    )
    monkeypatch.setattr(
        adapter,
        "_wait_for_asset_idle",
        lambda *_args, **_kwargs: SimpleNamespace(
            should_overwrite=False,
            asset_profile="chatgpt_mac",
            final_state=VisualGuiState.COMPOSER_HAS_TEXT,
            poll_count=1,
            detection=codex_detection(VisualGuiState.COMPOSER_HAS_TEXT),
        ),
    )
    monkeypatch.setattr(
        adapter,
        "_click_pm_asset_composer_for_paste",
        lambda *_args, **_kwargs: actions.append("focus")
        or codex_detection(VisualGuiState.COMPOSER_HAS_TEXT),
    )
    monkeypatch.setattr(
        adapter,
        "_copy_pm_composer_text_for_verification",
        lambda *_args, **_kwargs: next(copied_texts),
    )
    monkeypatch.setattr(
        adapter,
        "_select_all_local_agent_text",
        lambda _target: actions.append("select_all"),
    )
    monkeypatch.setattr(adapter, "_paste_variant", lambda *_args: actions.append("paste"))
    monkeypatch.setattr(
        adapter,
        "_detect_asset_state",
        lambda _target: codex_detection(VisualGuiState.COMPOSER_HAS_TEXT),
    )

    assert adapter.paste_clipboard() is True

    assert actions == ["focus", "select_all", "paste"]
    assert adapter.last_pm_paste_backend_success is True
    assert adapter.last_pm_paste_content_verified is None
    assert adapter.last_pm_prompt_sentinel_found is None


def test_pm_initial_composer_overwrite_proceeds_after_paste_action_returns(monkeypatch):
    prompt = "PM prompt\nAGENT_BRIDGE_PM_PROMPT_SENTINEL: bridge_overwrite_fail"
    actions: list[str] = []
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard(prompt),
        app_activator=FakeActivator(FakeWindowDetector(active_app="ChatGPT")),
        sleep_fn=lambda _seconds: None,
    )
    adapter.active_target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
        paste_backend="menu_paste_accessibility",
        max_action_attempts=1,
    )
    monkeypatch.setattr(
        adapter,
        "_wait_for_asset_idle",
        lambda *_args, **_kwargs: SimpleNamespace(
            should_overwrite=False,
            asset_profile="chatgpt_mac",
            final_state=VisualGuiState.COMPOSER_HAS_TEXT,
            poll_count=1,
            detection=codex_detection(VisualGuiState.COMPOSER_HAS_TEXT),
        ),
    )
    monkeypatch.setattr(
        adapter,
        "_click_pm_asset_composer_for_paste",
        lambda *_args, **_kwargs: codex_detection(VisualGuiState.COMPOSER_HAS_TEXT),
    )
    monkeypatch.setattr(
        adapter,
        "_copy_pm_composer_text_for_verification",
        lambda *_args, **_kwargs: "stale unrelated text",
    )
    monkeypatch.setattr(
        adapter,
        "_select_all_local_agent_text",
        lambda _target: actions.append("select_all"),
    )
    monkeypatch.setattr(adapter, "_paste_variant", lambda *_args: actions.append("paste"))
    monkeypatch.setattr(
        adapter,
        "_detect_asset_state",
        lambda _target: codex_detection(VisualGuiState.COMPOSER_HAS_TEXT),
    )

    assert adapter.paste_clipboard() is True

    assert actions == ["select_all", "paste"]
    assert adapter.last_pm_paste_backend_success is True
    assert adapter.last_pm_paste_content_verified is None


def test_pm_initial_composer_copyback_wrong_scope_blocks_submit(monkeypatch):
    prompt = "PM prompt\nAGENT_BRIDGE_PM_PROMPT_SENTINEL: bridge_wrong_scope"
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard(prompt),
        app_activator=FakeActivator(FakeWindowDetector(active_app="ChatGPT")),
        sleep_fn=lambda _seconds: None,
    )
    adapter.active_target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
        paste_backend="menu_paste_accessibility",
        max_action_attempts=1,
    )
    monkeypatch.setattr(
        adapter,
        "_wait_for_asset_idle",
        lambda *_args, **_kwargs: SimpleNamespace(
            should_overwrite=False,
            asset_profile="chatgpt_mac",
            final_state=VisualGuiState.COMPOSER_HAS_TEXT,
            poll_count=1,
            detection=codex_detection(VisualGuiState.COMPOSER_HAS_TEXT),
        ),
    )
    monkeypatch.setattr(
        adapter,
        "_click_pm_asset_composer_for_paste",
        lambda *_args, **_kwargs: codex_detection(VisualGuiState.COMPOSER_HAS_TEXT),
    )
    monkeypatch.setattr(
        adapter,
        "_copy_pm_composer_text_for_verification",
        lambda *_args, **_kwargs: "x" * 21000,
    )
    monkeypatch.setattr(adapter, "_paste_variant", lambda *_args: None)

    try:
        adapter.paste_clipboard()
    except Exception as error:
        assert "pm_composer_copyback_not_composer" in str(error)
    else:
        raise AssertionError("Expected wrong-scope copy-back failure.")


def test_pm_ambiguous_after_paste_allows_submit_ready_with_content_verified(
    tmp_path: Path,
    monkeypatch,
):
    action_log = tmp_path / "gui_actions_debug.jsonl"
    event_log = tmp_path / "bridge.jsonl"
    actions: list[str] = []
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt\nAGENT_BRIDGE_PM_PROMPT_SENTINEL: bridge_test"),
        app_activator=FakeActivator(FakeWindowDetector(active_app="ChatGPT")),
        sleep_fn=lambda _seconds: None,
        event_log=EventLog(event_log),
        debug_gui_actions_log=EventLog(action_log),
        bridge_attempt_id="bridge_submit_ready",
    )
    adapter.active_target = ManualStageTarget(
        app_name="ChatGPT",
        bundle_id=NATIVE_CHATGPT_MAC_BUNDLE_ID,
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
        paste_backend="menu_paste_accessibility",
        paste_backends=("menu_paste_accessibility",),
        max_action_attempts=1,
    )
    monkeypatch.setattr(
        adapter,
        "_wait_for_asset_idle",
        lambda _target: SimpleNamespace(
            should_overwrite=False,
            asset_profile="chatgpt_mac",
            final_state=VisualGuiState.IDLE,
            poll_count=1,
            detection=codex_detection(VisualGuiState.IDLE),
        ),
    )
    monkeypatch.setattr(
        adapter,
        "_click_pm_asset_composer_for_paste",
        lambda *_args, **_kwargs: codex_detection(VisualGuiState.IDLE),
    )
    monkeypatch.setattr(adapter, "_set_and_verify_pm_clipboard", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(adapter, "_paste_variant", lambda *_args: actions.append("paste"))
    monkeypatch.setattr(
        adapter,
        "_detect_asset_state",
        lambda _target: pm_ambiguous_send_stop_detection(),
    )
    monkeypatch.setattr(
        adapter,
        "_verify_pm_paste_content",
        lambda *_args, **_kwargs: verified_paste_content(),
    )

    assert adapter.paste_clipboard() is True

    assert actions == ["paste"]
    assert adapter.last_pm_paste_state_after is None
    assert adapter.last_pm_paste_send_ready is None
    assert adapter.last_pm_submit_ready_check is None
    records = [json.loads(line) for line in action_log.read_text(encoding="utf-8").splitlines()]
    actions_logged = [record["metadata"].get("action") for record in records]
    assert "paste_checkpoint_passed" in actions_logged
    assert "submit_after_paste_policy_used" in actions_logged
    assert "send_ready_check_skipped_by_policy" in actions_logged


def test_pm_submit_ready_detector_blocks_when_send_missing():
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector(active_app="ChatGPT")),
        sleep_fn=lambda _seconds: None,
    )
    target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
    )
    detection = codex_detection(VisualGuiState.RUNNING)

    result = adapter._detect_pm_submit_ready(target, detection)

    assert result.ready is False
    assert result.decision_reason == "pm_app_running_before_submit"


def test_pm_submit_ready_detector_blocks_unsafe_send_click_point():
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector(active_app="ChatGPT")),
        sleep_fn=lambda _seconds: None,
    )
    target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
    )
    detection = pm_ambiguous_send_stop_detection(
        send_bbox=(500, 500, 40, 43),
        stop_confidence=0.40,
    )

    result = adapter._detect_pm_submit_ready(target, detection)

    assert result.ready is False
    assert result.decision_reason == "pm_submit_ready_click_unsafe"


def test_pm_submit_ready_detector_blocks_dominant_running_candidate():
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector(active_app="ChatGPT")),
        sleep_fn=lambda _seconds: None,
    )
    target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
        visual_state_ambiguity_margin=0.03,
    )
    detection = pm_ambiguous_send_stop_detection(send_confidence=0.80, stop_confidence=0.90)

    result = adapter._detect_pm_submit_ready(target, detection)

    assert result.ready is False
    assert result.decision_reason == "pm_app_running_before_submit"


def test_pm_submit_guard_allows_ambiguous_state_after_submit_ready(monkeypatch):
    clicks: list[tuple[int, int]] = []
    submit_check = pm_submit_control_check()
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt\nAGENT_BRIDGE_PM_PROMPT_SENTINEL: bridge_test"),
        app_activator=FakeActivator(FakeWindowDetector(active_app="ChatGPT")),
        sleep_fn=lambda _seconds: None,
    )
    adapter.active_target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
        max_action_attempts=1,
    )
    adapter.last_pm_clipboard_set_attempted = True
    adapter.last_pm_clipboard_set_succeeded = True
    adapter.last_pm_clipboard_readback_matches_prompt_hash = True
    adapter.last_pm_paste_attempted = True
    adapter.last_pm_paste_backend_success = True
    adapter.last_pm_paste_send_ready = True
    adapter.last_pm_paste_state_after = VisualGuiState.AMBIGUOUS.value
    adapter.last_pm_paste_content_verified = True
    adapter.last_pm_prompt_sentinel_found = True
    monkeypatch.setattr(
        adapter,
        "_detect_pm_submit_ready",
        lambda *_args, **_kwargs: submit_check,
    )
    monkeypatch.setattr(adapter, "_detect_asset_state", lambda _target: codex_detection(VisualGuiState.RUNNING))
    monkeypatch.setattr(adapter, "_click_point", lambda point: clicks.append(point))

    adapter.submit()

    assert clicks == [submit_check.click_point]


def test_visual_pm_raw_v_variant_is_blocked_for_full_prompt_paste():
    hotkeys: list[tuple[str, ...]] = []
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector()),
        pyautogui_hotkeyer=lambda *keys: hotkeys.append(tuple(keys)),
    )
    target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
        paste_backend="pyautogui",
    )

    for variant in ("command_v_hotkey", "cmd_v_hotkey", "command_v_keydown", "cmd_v_keydown"):
        try:
            adapter._paste_variant(target, variant)
        except Exception as error:
            assert "pm_paste_raw_v_typed_instead_of_prompt" in str(error)
        else:
            raise AssertionError("Expected raw-v paste variant to be blocked.")

    assert hotkeys == []


def test_visual_pm_pyautogui_backend_has_no_production_variants():
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector()),
    )
    target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_chrome_app_visual",
        profile="chatgpt_chrome_app",
        visual_asset_profile="chatgpt_chrome_app",
        paste_backend="pyautogui",
    )

    assert adapter._paste_variants(target) == ()


def test_visual_pm_default_production_chain_excludes_raw_v_prone_variants():
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector()),
    )

    for target in (PM_ASSISTANT_TARGET, CHATGPT_CHROME_APP_TARGET):
        variants = adapter._paste_variants(target)
        assert variants == ("menu_paste_accessibility", "system_events_key_code_v_command")
        assert "system_events_command_v" not in variants
        assert "accessibility_set_focused_value" not in variants
        assert "cmd_v_hotkey" not in variants
        assert "command_v_hotkey" not in variants
        assert "command_v_keydown" not in variants
        assert "cmd_v_keydown" not in variants


def test_pm_paste_action_returned_counts_as_verified_paste_checkpoint(
    tmp_path: Path,
    monkeypatch,
):
    action_log = tmp_path / "gui_actions_debug.jsonl"
    actions: list[str] = []
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector()),
        sleep_fn=lambda _seconds: None,
        debug_gui_actions_log=EventLog(action_log),
        bridge_attempt_id="bridge_paste_semantics",
    )
    adapter.active_target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_chrome_app_visual",
        profile="chatgpt_chrome_app",
        visual_asset_profile="chatgpt_chrome_app",
        paste_backend="system_events_command_v",
        paste_backends=("system_events_command_v", "accessibility_set_focused_value"),
        max_action_attempts=1,
    )
    monkeypatch.setattr(
        adapter,
        "_wait_for_asset_idle",
        lambda _target: SimpleNamespace(
            should_overwrite=False,
            asset_profile="chatgpt_chrome_app",
            final_state=VisualGuiState.IDLE,
            poll_count=1,
            detection=codex_detection(VisualGuiState.IDLE),
        ),
    )
    monkeypatch.setattr(
        adapter,
        "_click_pm_asset_composer_for_paste",
        lambda *_args, **_kwargs: codex_detection(VisualGuiState.IDLE),
    )
    monkeypatch.setattr(
        adapter,
        "_paste_variants",
        lambda _target: ("system_events_command_v", "accessibility_set_focused_value"),
    )
    monkeypatch.setattr(adapter, "_set_and_verify_pm_clipboard", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(adapter, "_paste_variant", lambda _target, variant: actions.append(variant))
    monkeypatch.setattr(
        adapter,
        "_verify_pm_paste_content",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("content verification should be diagnostic-only")
        ),
    )
    monkeypatch.setattr(
        adapter,
        "_detect_asset_state",
        lambda _target: codex_detection(VisualGuiState.IDLE),
    )

    assert adapter.paste_clipboard() is True

    assert actions == ["system_events_command_v"]
    assert adapter.last_pm_paste_backend_success is True
    records = [json.loads(line) for line in action_log.read_text(encoding="utf-8").splitlines()]
    action_names = [record["metadata"].get("action") for record in records]
    assert "paste_checkpoint_passed" in action_names
    assert "submit_after_paste_policy_used" in action_names
    assert "pm_paste_variant_not_reflected" not in action_names


def test_chatgpt_mac_system_events_command_v_variant_runs_osascript(monkeypatch):
    scripts: list[str] = []
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector()),
    )
    monkeypatch.setattr(adapter, "_run_system_events", lambda script: scripts.append(script))
    target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
        paste_backend="system_events_command_v",
    )

    adapter._paste_variant(target, "system_events_command_v")

    assert scripts == ['tell application "System Events" to keystroke "v" using command down']


def test_chatgpt_mac_system_events_key_code_variant_runs_osascript(monkeypatch):
    scripts: list[str] = []
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector()),
    )
    monkeypatch.setattr(adapter, "_run_system_events", lambda script: scripts.append(script))
    target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
        paste_backend="system_events_key_code_v_command",
    )

    adapter._paste_variant(target, "system_events_key_code_v_command")

    assert scripts == ['tell application "System Events" to key code 9 using command down']


def test_visual_pm_menu_paste_accessibility_variant_uses_menu(monkeypatch):
    calls: list[tuple[str, ...]] = []
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector()),
    )
    monkeypatch.setattr(
        adapter,
        "_click_accessibility_menu_item",
        lambda _target, *, item_names, menu_names=("Edit", "편집", "수정"): calls.append(
            item_names
        ),
    )
    target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
        paste_backend="menu_paste_accessibility",
    )

    adapter._paste_variant(target, "menu_paste_accessibility")

    assert calls == [("Paste", "붙여넣기", "붙이기")]


def test_visual_pm_accessibility_set_value_variant_sets_focused_value(monkeypatch):
    scripts: list[str] = []
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector()),
    )
    monkeypatch.setattr(adapter, "_run_system_events", lambda script: scripts.append(script))
    target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
        paste_backend="accessibility_set_focused_value",
    )

    adapter._paste_variant(target, "accessibility_set_focused_value")

    assert scripts
    assert "AXFocusedUIElement" in scripts[0]
    assert "pbpaste" in scripts[0]
    assert "set value of focusedElement to clipboardText" in scripts[0]


def test_raw_v_and_korean_jamo_are_detected_as_paste_leakage():
    assert raw_key_leak_suspected("v", "AGENT_BRIDGE_PASTE_TEST")
    assert raw_key_leak_suspected("ㅍ", "AGENT_BRIDGE_PASTE_TEST")
    assert raw_key_leak_suspected("ㅍㅍㅍ", "AGENT_BRIDGE_PASTE_TEST")
    assert raw_key_leak_suspected("x", "AGENT_BRIDGE_PASTE_TEST")
    assert not raw_key_leak_suspected("AGENT_BRIDGE_PASTE_TEST", "AGENT_BRIDGE_PASTE_TEST")


def test_pm_prompt_sentinel_extracts_bridge_attempt_line():
    prompt = "Header\nAGENT_BRIDGE_PM_PROMPT_SENTINEL: bridge_test\nBody"

    assert extract_pm_prompt_sentinel(prompt) == "AGENT_BRIDGE_PM_PROMPT_SENTINEL: bridge_test"


def test_paste_text_match_requires_full_expected_content():
    assert paste_text_matches_expected("AGENT_BRIDGE_PASTE_TEST", "AGENT_BRIDGE_PASTE_TEST")
    assert not paste_text_matches_expected("ㅍ", "AGENT_BRIDGE_PASTE_TEST")
    assert not paste_text_matches_expected("prefix AGENT_BRIDGE_PASTE_TEST", "AGENT_BRIDGE_PASTE_TEST")


def test_pm_paste_copyback_with_sentinel_passes_verification(monkeypatch):
    prompt = (
        "You are the PM assistant.\n"
        "AGENT_BRIDGE_PM_PROMPT_SENTINEL: bridge_test\n"
        "Do the task."
    )
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard(prompt),
        app_activator=FakeActivator(FakeWindowDetector()),
        sleep_fn=lambda _seconds: None,
    )
    target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
        paste_backend="menu_paste_accessibility",
    )
    monkeypatch.setattr(
        adapter,
        "_copy_pm_composer_text_for_verification",
        lambda *_args, **_kwargs: "ㅍ\nAGENT_BRIDGE_PM_PROMPT_SENTINEL: bridge_test\npartial",
    )

    result = adapter._verify_pm_paste_content(
        target,
        expected_text=prompt,
        paste_variant="menu_paste_accessibility",
        paste_backend="menu_paste_accessibility",
        attempt_index=1,
        max_attempts=3,
        prompt_hash=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        window_bounds=(0, 0, 100, 100),
    )

    assert result.verified is True
    assert result.sentinel_found is True


def test_pm_paste_copyback_missing_sentinel_blocks_verification(monkeypatch):
    prompt = (
        "You are the PM assistant.\n"
        "AGENT_BRIDGE_PM_PROMPT_SENTINEL: bridge_test\n"
        "Do the task."
    )
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard(prompt),
        app_activator=FakeActivator(FakeWindowDetector()),
        sleep_fn=lambda _seconds: None,
    )
    target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
        paste_backend="menu_paste_accessibility",
    )
    monkeypatch.setattr(
        adapter,
        "_copy_pm_composer_text_for_verification",
        lambda *_args, **_kwargs: "partial prompt without sentinel",
    )

    result = adapter._verify_pm_paste_content(
        target,
        expected_text=prompt,
        paste_variant="menu_paste_accessibility",
        paste_backend="menu_paste_accessibility",
        attempt_index=1,
        max_attempts=3,
        prompt_hash=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        window_bounds=(0, 0, 100, 100),
    )

    assert result.verified is False
    assert result.sentinel_found is False
    assert result.failure_reason == "pm_prompt_content_verification_failed"


def test_pm_paste_copyback_raw_korean_jamo_blocks_verification(monkeypatch):
    prompt = (
        "You are the PM assistant.\n"
        "AGENT_BRIDGE_PM_PROMPT_SENTINEL: bridge_test\n"
        "Do the task."
    )
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard(prompt),
        app_activator=FakeActivator(FakeWindowDetector()),
        sleep_fn=lambda _seconds: None,
    )
    target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
        paste_backend="menu_paste_accessibility",
    )
    monkeypatch.setattr(
        adapter,
        "_copy_pm_composer_text_for_verification",
        lambda *_args, **_kwargs: "ㅍ",
    )

    result = adapter._verify_pm_paste_content(
        target,
        expected_text=prompt,
        paste_variant="menu_paste_accessibility",
        paste_backend="menu_paste_accessibility",
        attempt_index=1,
        max_attempts=3,
        prompt_hash=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        window_bounds=(0, 0, 100, 100),
    )

    assert result.verified is False
    assert result.raw_key_leak_suspected is True
    assert result.failure_reason == "pm_paste_raw_v_typed_instead_of_prompt"


def test_composer_has_text_alone_does_not_satisfy_pm_paste(monkeypatch):
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector()),
        sleep_fn=lambda _seconds: None,
    )
    adapter.active_target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
        paste_backend="menu_paste_accessibility",
        max_action_attempts=1,
    )
    monkeypatch.setattr(
        adapter,
        "_wait_for_asset_idle",
        lambda _target: SimpleNamespace(
            should_overwrite=False,
            asset_profile="chatgpt_mac",
            final_state=VisualGuiState.IDLE,
            poll_count=1,
            detection=codex_detection(VisualGuiState.IDLE),
        ),
    )
    monkeypatch.setattr(
        adapter,
        "_click_pm_asset_composer_for_paste",
        lambda *_args, **_kwargs: codex_detection(VisualGuiState.IDLE),
    )
    monkeypatch.setattr(adapter, "_set_and_verify_pm_clipboard", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(adapter, "_paste_variant", lambda *_args: None)
    monkeypatch.setattr(
        adapter,
        "_detect_asset_state",
        lambda _target: codex_detection(VisualGuiState.COMPOSER_HAS_TEXT),
    )
    monkeypatch.setattr(
        adapter,
        "_verify_pm_paste_content",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("content verification should be diagnostic-only")
        ),
    )

    assert adapter.paste_clipboard() is True
    assert adapter.last_pm_paste_backend_success is True
    assert adapter.last_pm_paste_content_verified is None


def test_chatgpt_asset_submit_guard_ignores_raw_v_diagnostic_after_verified_paste(monkeypatch):
    clicks: list[tuple[int, int]] = []
    submit_check = pm_submit_control_check(click_point=(20, 30))
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector()),
        sleep_fn=lambda _seconds: None,
    )
    adapter.active_target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
        paste_backend="system_events_command_v",
        max_action_attempts=1,
    )
    adapter.last_pm_clipboard_set_attempted = True
    adapter.last_pm_clipboard_set_succeeded = True
    adapter.last_pm_clipboard_readback_matches_prompt_hash = True
    adapter.last_pm_paste_attempted = True
    adapter.last_pm_paste_backend_success = True
    adapter.last_pm_paste_send_ready = True
    adapter.last_pm_paste_state_after = VisualGuiState.COMPOSER_HAS_TEXT.value
    adapter.last_pm_raw_v_failure_detected = True
    monkeypatch.setattr(adapter, "_detect_pm_submit_ready", lambda *_args, **_kwargs: submit_check)
    monkeypatch.setattr(adapter, "_detect_asset_state", lambda _target: codex_detection(VisualGuiState.RUNNING))
    monkeypatch.setattr(adapter, "_click_point", lambda point: clicks.append(point))

    adapter.submit()

    assert clicks == [(20, 30)]


def test_chatgpt_asset_submit_guard_ignores_missing_content_verification_after_verified_paste(
    monkeypatch,
):
    clicks: list[tuple[int, int]] = []
    submit_check = pm_submit_control_check(click_point=(20, 30))
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector()),
        sleep_fn=lambda _seconds: None,
    )
    adapter.active_target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
        paste_backend="menu_paste_accessibility",
        max_action_attempts=1,
    )
    adapter.last_pm_clipboard_set_attempted = True
    adapter.last_pm_clipboard_set_succeeded = True
    adapter.last_pm_clipboard_readback_matches_prompt_hash = True
    adapter.last_pm_paste_attempted = True
    adapter.last_pm_paste_backend_success = True
    adapter.last_pm_paste_send_ready = True
    adapter.last_pm_paste_state_after = VisualGuiState.COMPOSER_HAS_TEXT.value
    adapter.last_pm_paste_content_verified = False
    monkeypatch.setattr(adapter, "_detect_pm_submit_ready", lambda *_args, **_kwargs: submit_check)
    monkeypatch.setattr(adapter, "_detect_asset_state", lambda _target: codex_detection(VisualGuiState.RUNNING))
    monkeypatch.setattr(adapter, "_click_point", lambda point: clicks.append(point))

    adapter.submit()

    assert clicks == [(20, 30)]


def test_pm_submit_after_paste_checkpoint_clicks_without_visible_text_gate(monkeypatch):
    for profile, backend in (
        ("chatgpt_chrome_app", "chatgpt_chrome_app_visual"),
        ("chatgpt_mac", "chatgpt_mac_visual"),
    ):
        clicks: list[tuple[int, int]] = []
        terminal_lines: list[str] = []
        submit_check = pm_submit_control_check(click_point=(20, 30))
        adapter = MacOSSystemEventsGuiAdapter(
            clipboard=FakeClipboard("PM prompt"),
            app_activator=FakeActivator(FakeWindowDetector()),
            sleep_fn=lambda _seconds: None,
            debug_output_fn=terminal_lines.append,
        )
        adapter.active_target = ManualStageTarget(
            app_name="ChatGPT",
            backend=backend,
            profile=profile,
            visual_asset_profile=profile,
            paste_backend="menu_paste_accessibility",
            max_action_attempts=1,
        )
        adapter.last_pm_clipboard_set_attempted = True
        adapter.last_pm_clipboard_set_succeeded = True
        adapter.last_pm_clipboard_readback_matches_prompt_hash = True
        adapter.last_pm_paste_attempted = True
        adapter.last_pm_paste_backend_success = True
        adapter.last_pm_paste_send_ready = False
        adapter.last_pm_paste_content_verified = False
        monkeypatch.setattr(
            adapter,
            "_detect_pm_submit_ready",
            lambda *_args, **_kwargs: submit_check,
        )
        monkeypatch.setattr(
            adapter,
            "_detect_asset_state",
            lambda _target: codex_detection(VisualGuiState.RUNNING),
        )
        monkeypatch.setattr(
            adapter,
            "_wait_for_pm_submit_ready",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("send-ready wait must not gate submit")
            ),
        )
        monkeypatch.setattr(adapter, "_click_point", lambda point: clicks.append(point))

        adapter.submit()

        assert clicks == [(20, 30)]
        joined = "\n".join(terminal_lines)
        assert "visible-text gates skipped by policy" in joined
        assert "submit click completed" in joined


def test_pm_submit_control_missing_after_verified_paste_fails_clearly(monkeypatch):
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector()),
        sleep_fn=lambda _seconds: None,
    )
    adapter.active_target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_chrome_app_visual",
        profile="chatgpt_chrome_app",
        visual_asset_profile="chatgpt_chrome_app",
        paste_backend="menu_paste_accessibility",
        max_action_attempts=1,
    )
    adapter.last_pm_clipboard_set_attempted = True
    adapter.last_pm_clipboard_set_succeeded = True
    adapter.last_pm_clipboard_readback_matches_prompt_hash = True
    adapter.last_pm_paste_attempted = True
    adapter.last_pm_paste_backend_success = True
    monkeypatch.setattr(
        adapter,
        "_detect_pm_submit_ready",
        lambda *_args, **_kwargs: pm_submit_control_check(
            click_point=None,
            decision_reason="pm_submit_ready_send_not_detected",
            confidence=None,
        ),
    )

    try:
        adapter.submit()
    except Exception as error:
        assert "pm_submit_control_not_found_after_paste" in str(error)
    else:
        raise AssertionError("Expected missing submit control failure.")


def test_pm_submit_unsafe_click_point_after_verified_paste_fails_clearly(monkeypatch):
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector()),
        sleep_fn=lambda _seconds: None,
    )
    adapter.active_target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_chrome_app_visual",
        profile="chatgpt_chrome_app",
        visual_asset_profile="chatgpt_chrome_app",
        paste_backend="menu_paste_accessibility",
        max_action_attempts=1,
    )
    adapter.last_pm_clipboard_set_attempted = True
    adapter.last_pm_clipboard_set_succeeded = True
    adapter.last_pm_clipboard_readback_matches_prompt_hash = True
    adapter.last_pm_paste_attempted = True
    adapter.last_pm_paste_backend_success = True
    monkeypatch.setattr(
        adapter,
        "_detect_pm_submit_ready",
        lambda *_args, **_kwargs: pm_submit_control_check(
            click_point=(999, 999),
            click_point_safe=False,
            decision_reason="pm_submit_ready_click_unsafe",
        ),
    )

    try:
        adapter.submit()
    except Exception as error:
        assert "pm_submit_click_point_unsafe" in str(error)
    else:
        raise AssertionError("Expected unsafe submit click failure.")


def test_pm_submit_retry_stops_when_running_is_detected(monkeypatch):
    clicks: list[tuple[int, int]] = []
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector()),
        sleep_fn=lambda _seconds: None,
    )
    adapter.active_target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_chrome_app_visual",
        profile="chatgpt_chrome_app",
        visual_asset_profile="chatgpt_chrome_app",
        paste_backend="menu_paste_accessibility",
        max_action_attempts=3,
    )
    adapter.last_pm_clipboard_set_attempted = True
    adapter.last_pm_clipboard_set_succeeded = True
    adapter.last_pm_clipboard_readback_matches_prompt_hash = True
    adapter.last_pm_paste_attempted = True
    adapter.last_pm_paste_backend_success = True
    monkeypatch.setattr(
        adapter,
        "_detect_pm_submit_ready",
        lambda *_args, **_kwargs: pm_submit_control_check(click_point=(20, 30)),
    )
    monkeypatch.setattr(
        adapter,
        "_detect_asset_state",
        lambda _target: codex_detection(VisualGuiState.RUNNING),
    )
    monkeypatch.setattr(adapter, "_click_point", lambda point: clicks.append(point))

    adapter.submit()

    assert clicks == [(20, 30)]


def test_pm_submit_after_paste_tolerates_upload_wait_past_three_attempts(monkeypatch):
    clicks: list[tuple[int, int]] = []
    checks = {"count": 0}
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector()),
        sleep_fn=lambda _seconds: None,
    )
    adapter.active_target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_chrome_app_visual",
        profile="chatgpt_chrome_app",
        visual_asset_profile="chatgpt_chrome_app",
        paste_backend="menu_paste_accessibility",
        max_action_attempts=3,
    )
    adapter.last_pm_clipboard_set_attempted = True
    adapter.last_pm_clipboard_set_succeeded = True
    adapter.last_pm_clipboard_readback_matches_prompt_hash = True
    adapter.last_pm_paste_attempted = True
    adapter.last_pm_paste_backend_success = True

    def detect_submit_ready(*_args, **_kwargs):
        checks["count"] += 1
        if checks["count"] < 5:
            return pm_submit_control_check(
                click_point=None,
                decision_reason="pm_submit_ready_upload_in_progress",
                confidence=None,
            )
        return pm_submit_control_check(click_point=(20, 30))

    monkeypatch.setattr(adapter, "_detect_pm_submit_ready", detect_submit_ready)
    monkeypatch.setattr(
        adapter,
        "_detect_asset_state",
        lambda _target: codex_detection(VisualGuiState.RUNNING),
    )
    monkeypatch.setattr(adapter, "_click_point", lambda point: clicks.append(point))

    adapter.submit()

    assert checks["count"] == 5
    assert clicks == [(20, 30)]


def test_pm_submit_retries_and_fails_when_click_is_not_reflected(monkeypatch):
    clicks: list[tuple[int, int]] = []
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector()),
        sleep_fn=lambda _seconds: None,
    )
    adapter.active_target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_chrome_app_visual",
        profile="chatgpt_chrome_app",
        visual_asset_profile="chatgpt_chrome_app",
        paste_backend="menu_paste_accessibility",
        max_action_attempts=3,
        submit_after_paste_max_attempts=3,
    )
    adapter.last_pm_clipboard_set_attempted = True
    adapter.last_pm_clipboard_set_succeeded = True
    adapter.last_pm_clipboard_readback_matches_prompt_hash = True
    adapter.last_pm_paste_attempted = True
    adapter.last_pm_paste_backend_success = True
    monkeypatch.setattr(
        adapter,
        "_detect_pm_submit_ready",
        lambda *_args, **_kwargs: pm_submit_control_check(click_point=(20, 30)),
    )
    monkeypatch.setattr(
        adapter,
        "_detect_asset_state",
        lambda _target: codex_detection(VisualGuiState.COMPOSER_HAS_TEXT),
    )
    monkeypatch.setattr(adapter, "_click_point", lambda point: clicks.append(point))

    try:
        adapter.submit()
    except Exception as error:
        assert "pm_submit_not_reflected_after_click" in str(error)
    else:
        raise AssertionError("Expected unreflected submit click failure.")

    assert clicks == [(20, 30), (20, 30), (20, 30)]


def test_chatgpt_mac_plus_anchor_failure_prints_terminal_attempt_log():
    terminal_lines: list[str] = []
    detector = FakeWindowDetector(active_app="ChatGPT")
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(detector),
        codex_ui_detector=detector,  # type: ignore[arg-type]
        asset_state_detector=FakeAssetStateDetector([]),  # type: ignore[arg-type]
        debug_output_fn=terminal_lines.append,
        sleep_fn=lambda _seconds: None,
    )
    adapter.asset_state_detector.detect = lambda **_kwargs: chatgpt_mac_detection_without_plus()  # type: ignore[method-assign]
    target = ManualStageTarget(
        app_name="ChatGPT",
        app_path="/Applications/ChatGPT.app",
        bundle_id=NATIVE_CHATGPT_MAC_BUNDLE_ID,
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
        focus_strategy="visual_plus_anchor",
    )

    try:
        adapter._click_pm_asset_composer_for_paste(
            target,
            attempt_index=1,
            max_attempts=3,
            prompt_length=9,
            prompt_hash="abc123",
        )
    except Exception as error:
        assert "pm_plus_anchor_not_found" in str(error)
    else:
        raise AssertionError("Expected plus-anchor failure.")

    joined = "\n".join(terminal_lines)
    assert "PM chatgpt_mac: detect plus anchor attempt 1/3" in joined
    assert "failed: plus anchor not found" in joined
    assert "best confidence=0.570" in joined


def test_visual_pm_terminal_debug_logs_no_candidate_summary_for_rejections():
    terminal_lines: list[str] = []
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector(active_app="ChatGPT")),
        codex_ui_detector=FakeCodexPasteDetector(),  # type: ignore[arg-type]
        asset_state_detector=FakeAssetStateDetector([]),  # type: ignore[arg-type]
        debug_output_fn=terminal_lines.append,
        sleep_fn=lambda _seconds: None,
    )
    adapter.asset_state_detector.detect = lambda **_kwargs: chatgpt_mac_detection_without_plus()  # type: ignore[method-assign]
    target = ManualStageTarget(
        app_name="ChatGPT",
        app_path="/Applications/ChatGPT.app",
        bundle_id=NATIVE_CHATGPT_MAC_BUNDLE_ID,
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
        focus_strategy="visual_plus_anchor",
    )

    adapter._detect_asset_state(target)

    joined = "\n".join(terminal_lines)
    assert "detect state attempt 1/3" in joined
    assert "no plus candidate passed threshold" in joined
    assert "best=plus chatgpt_mac_plus_button_light.png" in joined
    assert "raw=0.570" in joined
    assert "threshold=0.580" in joined
    assert "reason=confidence_below_threshold" in joined
    assert "compare plus chatgpt_mac_plus_button_light.png" not in joined
    assert "selected state=UNKNOWN" in joined


def test_visual_pm_terminal_debug_logs_accepted_candidates_and_suppresses_rejected_by_default(
    tmp_path: Path,
):
    terminal_lines: list[str] = []
    state_log = tmp_path / "gui_state_machine_debug.jsonl"
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector(active_app="ChatGPT")),
        codex_ui_detector=FakeCodexPasteDetector(),  # type: ignore[arg-type]
        asset_state_detector=FakeAssetStateDetector([]),  # type: ignore[arg-type]
        debug_output_fn=terminal_lines.append,
        debug_state_machine_log=EventLog(state_log),
        sleep_fn=lambda _seconds: None,
    )
    adapter.asset_state_detector.detect = (  # type: ignore[method-assign]
        lambda **_kwargs: chatgpt_mac_detection_with_accepted_and_rejected_templates()
    )
    target = ManualStageTarget(
        app_name="ChatGPT",
        app_path="/Applications/ChatGPT.app",
        bundle_id=NATIVE_CHATGPT_MAC_BUNDLE_ID,
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
        focus_strategy="visual_plus_anchor",
    )

    adapter._detect_asset_state(target)

    joined = "\n".join(terminal_lines)
    comparison_lines = "\n".join(
        line
        for line in terminal_lines
        if "accepted " in line or "compare " in line or "candidate" in line
    )
    assert "accepted plus chatgpt_mac_plus_button_light.png" in comparison_lines
    assert "accepted send chatgpt_mac_send_button_light.png" in comparison_lines
    assert "raw=0.910" in comparison_lines
    assert "composite=unavailable" in comparison_lines
    assert "chatgpt_mac_send_button_dark.png" not in comparison_lines
    assert "selected state=COMPOSER_HAS_TEXT" in joined
    records = [json.loads(line) for line in state_log.read_text(encoding="utf-8").splitlines()]
    rejected = [
        record
        for record in records
        if record["metadata"].get("template_path", "").endswith("chatgpt_mac_send_button_dark.png")
    ]
    assert rejected
    assert rejected[0]["metadata"]["accepted"] is False
    assert "edge_score" in rejected[0]["metadata"]
    assert "glyph_score" in rejected[0]["metadata"]
    assert "composite_score" in rejected[0]["metadata"]


def test_visual_pm_terminal_debug_all_template_comparisons_prints_rejections():
    terminal_lines: list[str] = []
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector(active_app="ChatGPT")),
        codex_ui_detector=FakeCodexPasteDetector(),  # type: ignore[arg-type]
        asset_state_detector=FakeAssetStateDetector([]),  # type: ignore[arg-type]
        debug_output_fn=terminal_lines.append,
        debug_all_template_comparisons=True,
        sleep_fn=lambda _seconds: None,
    )
    adapter.asset_state_detector.detect = lambda **_kwargs: chatgpt_mac_detection_without_plus()  # type: ignore[method-assign]
    target = ManualStageTarget(
        app_name="ChatGPT",
        app_path="/Applications/ChatGPT.app",
        bundle_id=NATIVE_CHATGPT_MAC_BUNDLE_ID,
        backend="chatgpt_mac_visual",
        profile="chatgpt_mac",
        visual_asset_profile="chatgpt_mac",
        focus_strategy="visual_plus_anchor",
    )

    adapter._detect_asset_state(target)

    joined = "\n".join(terminal_lines)
    assert "compare plus chatgpt_mac_plus_button_light.png" in joined
    assert "accepted=no" in joined


def test_chatgpt_asset_submit_guard_blocks_without_paste():
    adapter = MacOSSystemEventsGuiAdapter(
        clipboard=FakeClipboard("PM prompt"),
        app_activator=FakeActivator(FakeWindowDetector()),
        sleep_fn=lambda _seconds: None,
    )
    adapter.active_target = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_chrome_app_visual",
        visual_asset_profile="chatgpt_chrome_app",
        paste_backend="pyautogui",
    )

    try:
        adapter.submit()
    except Exception as error:
        assert "pm_clipboard_set_not_attempted" in str(error)
    else:
        raise AssertionError("Expected PM submit guard failure.")


class FailingActivationGui(GuiAutomationAdapter):
    def __init__(self):
        self.actions: list[str] = []

    def activate_app(self, target: ManualStageTarget) -> None:
        self.actions.append(f"activate:{target.app_name}")
        raise RuntimeError("activation preflight failed")

    def copy_text_to_clipboard(self, text: str) -> None:
        self.actions.append("copy_text")

    def paste_clipboard(self) -> None:
        self.actions.append("paste")

    def submit(self) -> None:
        self.actions.append("submit")

    def wait_for_response(self, timeout_seconds: int) -> None:
        self.actions.append("wait")

    def copy_response_text(self) -> str:
        self.actions.append("copy_response")
        return "```CODEX_NEXT_PROMPT\nnoop\n```"


def test_roundtrip_aborts_before_paste_if_pm_activation_preflight_fails(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    (workspace / "reports").mkdir(parents=True)
    (workspace / "reports" / "latest_agent_report.md").write_text("# Report\n\nReady.", encoding="utf-8")
    template_dir.mkdir()
    (template_dir / "codex_command_wrapper.md").write_text(
        "Type={command_type}\nSource={source}\nPayload={payload}",
        encoding="utf-8",
    )
    gui = FailingActivationGui()

    try:
        run_report_roundtrip(
            config=ReportRoundtripConfig(
                workspace_dir=workspace,
                template_dir=template_dir,
                targets=load_gui_targets(tmp_path / "missing-config"),
                auto_confirm=True,
                max_cycles=1,
                max_runtime_seconds=180,
                require_pm_backend_preflight=False,
            ),
            gui=gui,
        )
    except ReportRoundtripError as error:
        assert "activation preflight failed" in str(error)
    else:
        raise AssertionError("Expected activation preflight failure.")

    assert gui.actions == ["activate:ChatGPT"]
