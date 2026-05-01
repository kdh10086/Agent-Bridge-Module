from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

import agent_bridge.cli as cli_module
from agent_bridge.core.command_queue import CommandQueue
from agent_bridge.core.models import Command, CommandType
from agent_bridge.gui.asset_state_machine import VisualGuiState, VisualStateDetection
from agent_bridge.gui.codex_ui_detector import (
    CodexInputTargetDiagnostic,
    CodexWindowInfo,
    CodexWindowSelectionResult,
    LocalAgentFocusResult,
)
from agent_bridge.gui.chatgpt_mac_native import (
    AppWindowBoundsResult,
    ChatGPTAppCandidate,
    ChatGPTAppTargetDiagnostic,
    ChatGPTNativePreflightResult,
    diagnose_chatgpt_app_targets,
)
from agent_bridge.gui.macos_apps import (
    CHATGPT_CHROME_APP_PROFILE,
    CHATGPT_MAC_PROFILE,
    ActivationAttempt,
    ActivationResult,
    ManualStageTarget,
    automatic_submit_supported,
    load_gui_targets,
    pm_target_for_profile,
)


def write_templates(template_dir: Path) -> None:
    template_dir.mkdir(parents=True)
    (template_dir / "pm_report_prompt.md").write_text("PM prompt:\n{report}", encoding="utf-8")
    (template_dir / "codex_command_wrapper.md").write_text(
        "Type={command_type}\nSource={source}\nPayload={payload}",
        encoding="utf-8",
    )


def write_config(config_dir: Path) -> None:
    config_dir.mkdir(parents=True)
    (config_dir / "default.yaml").write_text(
        """
apps:
  pm_assistant:
    app_name: "ChatGPT"
    app_path: "/Applications/ChatGPT.app"
    bundle_id: "com.openai.chat"
    backend: "chatgpt_mac_visual"
    profile: "chatgpt_mac"
    require_backend_preflight: false
    window_hint: "ChatGPT"
    paste_instruction: "Paste into the ChatGPT composer, then review manually."
    focus_strategy: "visual_plus_anchor"
    visual_asset_profile: "chatgpt_mac"
    click_backend: "pyautogui"
    visual_anchor_click_backend: "pyautogui"
    paste_backend: "pyautogui"
    visual_plus_templates:
      - assets/gui/chatgpt_mac/chatgpt_mac_plus_button_light.png
      - assets/gui/chatgpt_mac/chatgpt_mac_plus_button_dark.png
    visual_send_disabled_templates:
      - assets/gui/chatgpt_mac/chatgpt_mac_send_disabled_button_light.png
      - assets/gui/chatgpt_mac/chatgpt_mac_send_disabled_button_dark.png
    visual_send_templates:
      - assets/gui/chatgpt_mac/chatgpt_mac_send_button_light.png
      - assets/gui/chatgpt_mac/chatgpt_mac_send_button_dark.png
    visual_stop_templates:
      - assets/gui/chatgpt_mac/chatgpt_mac_stop_button_light.png
      - assets/gui/chatgpt_mac/chatgpt_mac_stop_button_dark.png
    visual_plus_confidence_threshold: 0.60
    visual_state_confidence_threshold: 0.95
    visual_state_ambiguity_margin: 0.03
  local_agent:
    app_name: "Codex"
    window_hint: "Agent Bridge"
    paste_instruction: "Paste into Codex input, then review manually."
    focus_strategy: "direct_plus_anchor"
    visual_asset_profile: "codex"
    input_focus_strategy: null
    click_backend: "pyautogui"
    visual_anchor_click_backend: "pyautogui"
    paste_backend: "pyautogui"
    require_prompt_presence_verification: true
    allow_unverified_submit: false
    allow_unverified_submit_for_noop_dogfood: true
    composer_placeholder_text: "후속 변경 사항을 부탁하세요"
    idle_empty_wait_timeout_seconds: 600
    idle_empty_poll_interval_seconds: 10
    dedicated_automation_session: true
    allow_overwrite_after_idle_timeout: true
    stop_on_idle_timeout: false
    plus_anchor_enabled: true
    plus_anchor_x_offset: 0
    plus_anchor_y_offset: 50
    direct_plus_anchor_enabled: true
    direct_plus_anchor_x_offset: 0
    direct_plus_anchor_y_offset: 50
    direct_plus_anchor_y_offset_candidates: [50]
    visual_plus_templates:
      - assets/gui/codex/codex_plus_button_light.png
      - assets/gui/codex/codex_plus_button_dark.png
    visual_send_disabled_templates:
      - assets/gui/codex/codex_send_disabled_button_light.png
      - assets/gui/codex/codex_send_disabled_button_dark.png
    visual_send_templates:
      - assets/gui/codex/codex_send_button_light.png
      - assets/gui/codex/codex_send_button_dark.png
    visual_stop_templates:
      - assets/gui/codex/codex_stop_button_light.png
      - assets/gui/codex/codex_stop_button_dark.png
    visual_state_confidence_threshold: 0.95
    visual_state_search_region: "lower_control_band"
    min_main_window_width: 400
    min_main_window_height: 300
    min_main_window_area: 120000
    window_selection_strategy: "largest_visible_normal"
    composer_policy:
      mode: dedicated_automation_session
      busy_placeholder_wait_timeout_seconds: 600
      busy_placeholder_poll_interval_seconds: 10
      on_busy_timeout: overwrite
""".lstrip(),
        encoding="utf-8",
    )


def configure_cli(monkeypatch, tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path
    workspace = root / "workspace"
    template_dir = root / "templates"
    config_dir = root / "config"
    write_templates(template_dir)
    write_config(config_dir)
    (workspace / "reports").mkdir(parents=True)
    (workspace / "reports" / "latest_agent_report.md").write_text("# Report\n\nReady.", encoding="utf-8")
    monkeypatch.setattr(cli_module, "ROOT", root)
    monkeypatch.setattr(cli_module, "WORKSPACE", workspace)
    monkeypatch.setattr(cli_module, "QUEUE_DIR", workspace / "queue")
    monkeypatch.setattr(cli_module, "STATE_PATH", workspace / "state" / "state.json")
    monkeypatch.setattr(cli_module, "LOG_PATH", workspace / "logs" / "bridge.jsonl")
    monkeypatch.setattr(cli_module, "TEMPLATE_DIR", template_dir)
    monkeypatch.setattr(cli_module, "CONFIG_DIR", config_dir)
    return workspace, template_dir, config_dir


def test_target_metadata_loads_from_default_and_local_override(tmp_path: Path):
    config_dir = tmp_path / "config"
    write_config(config_dir)
    (config_dir / "local.yaml").write_text(
        """
apps:
  local_agent:
    app_name: "Codex Nightly"
""".lstrip(),
        encoding="utf-8",
    )

    targets = load_gui_targets(config_dir)

    assert targets.pm_assistant.app_name == "ChatGPT"
    assert targets.pm_assistant.app_path == "/Applications/ChatGPT.app"
    assert targets.pm_assistant.bundle_id == "com.openai.chat"
    assert targets.pm_assistant.backend == "chatgpt_mac_visual"
    assert targets.pm_assistant.profile == "chatgpt_mac"
    assert not targets.pm_assistant.require_backend_preflight
    assert targets.pm_assistant.idle_empty_timeout_seconds == 600
    assert targets.pm_assistant.window_hint == "ChatGPT"
    assert targets.pm_assistant.focus_strategy == "visual_plus_anchor"
    assert targets.pm_assistant.visual_asset_profile == "chatgpt_mac"
    assert targets.pm_assistant.click_backend == "pyautogui"
    assert targets.pm_assistant.visual_plus_templates == (
        "assets/gui/chatgpt_mac/chatgpt_mac_plus_button_light.png",
        "assets/gui/chatgpt_mac/chatgpt_mac_plus_button_dark.png",
    )
    assert targets.pm_assistant.visual_send_disabled_templates == (
        "assets/gui/chatgpt_mac/chatgpt_mac_send_disabled_button_light.png",
        "assets/gui/chatgpt_mac/chatgpt_mac_send_disabled_button_dark.png",
    )
    assert targets.pm_assistant.visual_send_templates == (
        "assets/gui/chatgpt_mac/chatgpt_mac_send_button_light.png",
        "assets/gui/chatgpt_mac/chatgpt_mac_send_button_dark.png",
    )
    assert targets.pm_assistant.visual_stop_templates == (
        "assets/gui/chatgpt_mac/chatgpt_mac_stop_button_light.png",
        "assets/gui/chatgpt_mac/chatgpt_mac_stop_button_dark.png",
    )
    assert targets.pm_assistant.visual_plus_confidence_threshold == 0.60
    assert targets.pm_assistant.visual_state_confidence_threshold == 0.95
    assert targets.pm_assistant.visual_state_ambiguity_margin == 0.03
    assert targets.local_agent.app_name == "Codex Nightly"
    assert targets.local_agent.window_hint == "Agent Bridge"
    assert targets.local_agent.focus_strategy == "direct_plus_anchor"
    assert targets.local_agent.visual_asset_profile == "codex"
    assert targets.local_agent.input_focus_strategy is None
    assert targets.local_agent.click_backend == "pyautogui"
    assert targets.local_agent.visual_anchor_click_backend == "pyautogui"
    assert targets.local_agent.paste_backend == "pyautogui"
    assert targets.local_agent.require_prompt_presence_verification
    assert not targets.local_agent.allow_unverified_submit
    assert targets.local_agent.allow_unverified_submit_for_noop_dogfood
    assert targets.local_agent.composer_placeholder_text == "후속 변경 사항을 부탁하세요"
    assert targets.local_agent.idle_empty_wait_timeout_seconds == 600
    assert targets.local_agent.idle_empty_poll_interval_seconds == 10
    assert targets.local_agent.dedicated_automation_session
    assert targets.local_agent.allow_overwrite_after_idle_timeout
    assert not targets.local_agent.stop_on_idle_timeout
    assert targets.local_agent.plus_anchor_enabled
    assert targets.local_agent.plus_anchor_x_offset == 0
    assert targets.local_agent.plus_anchor_y_offset == 50
    assert targets.local_agent.direct_plus_anchor_enabled
    assert targets.local_agent.direct_plus_anchor_x_offset == 0
    assert targets.local_agent.direct_plus_anchor_y_offset == 50
    assert targets.local_agent.direct_plus_anchor_y_offset_candidates == (
        50,
    )
    assert targets.local_agent.visual_plus_templates == (
        "assets/gui/codex/codex_plus_button_light.png",
        "assets/gui/codex/codex_plus_button_dark.png",
    )
    assert targets.local_agent.visual_send_disabled_templates == (
        "assets/gui/codex/codex_send_disabled_button_light.png",
        "assets/gui/codex/codex_send_disabled_button_dark.png",
    )
    assert targets.local_agent.visual_send_templates == (
        "assets/gui/codex/codex_send_button_light.png",
        "assets/gui/codex/codex_send_button_dark.png",
    )
    assert targets.local_agent.visual_stop_templates == (
        "assets/gui/codex/codex_stop_button_light.png",
        "assets/gui/codex/codex_stop_button_dark.png",
    )
    assert targets.local_agent.visual_state_confidence_threshold == 0.95
    assert targets.local_agent.visual_state_search_region == "lower_control_band"
    assert targets.local_agent.min_main_window_width == 400
    assert targets.local_agent.min_main_window_height == 300
    assert targets.local_agent.min_main_window_area == 120000
    assert targets.local_agent.window_selection_strategy == "largest_visible_normal"
    assert targets.local_agent.composer_policy_mode == "dedicated_automation_session"
    assert targets.local_agent.busy_placeholder_wait_timeout_seconds == 600
    assert targets.local_agent.busy_placeholder_poll_interval_seconds == 10
    assert targets.local_agent.on_busy_timeout == "overwrite"


def test_pm_target_profile_selection_defaults_to_chatgpt_mac(tmp_path: Path):
    config_dir = tmp_path / "config"
    write_config(config_dir)
    targets = load_gui_targets(config_dir)

    selected = pm_target_for_profile(targets.pm_assistant, None)

    assert selected.profile == CHATGPT_MAC_PROFILE
    assert selected.bundle_id == "com.openai.chat"
    assert selected.visual_asset_profile == "chatgpt_mac"


def test_pm_target_profile_selection_can_select_chrome_app(tmp_path: Path):
    config_dir = tmp_path / "config"
    write_config(config_dir)
    targets = load_gui_targets(config_dir)

    selected = pm_target_for_profile(targets.pm_assistant, CHATGPT_CHROME_APP_PROFILE)

    assert selected.profile == CHATGPT_CHROME_APP_PROFILE
    assert selected.backend == "chatgpt_chrome_app_visual"
    assert selected.visual_asset_profile == "chatgpt_chrome_app"
    assert selected.bundle_id is None
    assert all("chatgpt_chrome_app" in path for path in selected.visual_plus_templates)


def test_gui_retry_config_applies_to_visual_targets(tmp_path: Path):
    config_dir = tmp_path / "config"
    write_config(config_dir)
    (config_dir / "local.yaml").write_text(
        """
gui:
  max_state_machine_attempts: 4
  state_machine_retry_delay_seconds: 0.25
  max_action_attempts: 5
  action_retry_delay_seconds: 0.2
""".lstrip(),
        encoding="utf-8",
    )

    targets = load_gui_targets(config_dir)

    assert targets.pm_assistant.max_state_machine_attempts == 4
    assert targets.pm_assistant.state_machine_retry_delay_seconds == 0.25
    assert targets.pm_assistant.max_action_attempts == 5
    assert targets.pm_assistant.action_retry_delay_seconds == 0.2
    assert targets.local_agent.max_state_machine_attempts == 4
    assert targets.local_agent.max_action_attempts == 5


def test_window_relative_fallback_config_loads_from_local_override(tmp_path: Path):
    config_dir = tmp_path / "config"
    write_config(config_dir)
    (config_dir / "local.yaml").write_text(
        """
apps:
  local_agent:
    input_focus_strategy: "window_relative_click"
    input_click_x_ratio: 0.50
    input_click_y_ratio: 0.92
    require_prompt_presence_verification: false
    allow_unverified_submit: true
""".lstrip(),
        encoding="utf-8",
    )

    target = load_gui_targets(config_dir).local_agent

    assert target.input_focus_strategy == "window_relative_click"
    assert target.input_click_x_ratio == 0.50
    assert target.input_click_y_ratio == 0.92
    assert not target.require_prompt_presence_verification
    assert target.allow_unverified_submit


def test_owner_reviewed_focus_candidates_load_from_local_override(tmp_path: Path):
    config_dir = tmp_path / "config"
    write_config(config_dir)
    (config_dir / "local.yaml").write_text(
        """
apps:
  local_agent:
    owner_reviewed_focus_candidates:
      - name: "composer_text_mid"
        basis: "main_window"
        x_ratio: 0.55
        y_ratio: 0.78
      - name: "composer_above_plus_mid"
        basis: "plus_anchor"
        x_offset: 80
        y_offset: 100
""".lstrip(),
        encoding="utf-8",
    )

    target = load_gui_targets(config_dir).local_agent

    assert target.owner_reviewed_focus_candidates == (
        {
            "name": "composer_text_mid",
            "basis": "main_window",
            "x_ratio": 0.55,
            "y_ratio": 0.78,
        },
        {
            "name": "composer_above_plus_mid",
            "basis": "plus_anchor",
            "x_offset": 80,
            "y_offset": 100,
        },
    )


def test_stage_pm_prompt_output_includes_pm_target_guidance(monkeypatch, tmp_path: Path):
    configure_cli(monkeypatch, tmp_path)

    result = CliRunner().invoke(cli_module.app, ["stage-pm-prompt", "--dry-run"])

    assert result.exit_code == 0
    assert "PM assistant target:" in result.output
    assert "ChatGPT" in result.output
    assert "ChatGPT" in result.output


def test_stage_local_agent_prompt_output_includes_local_target_guidance(monkeypatch, tmp_path: Path):
    workspace, _, _ = configure_cli(monkeypatch, tmp_path)
    payload = tmp_path / "payload.md"
    payload.write_text("# Task\n\nReport status.", encoding="utf-8")
    CommandQueue(workspace / "queue").enqueue(
        Command(
            id="cmd_test",
            type=CommandType.REQUEST_STATUS_REPORT,
            source="test",
            payload_path=str(payload),
            dedupe_key="target-test",
        )
    )

    result = CliRunner().invoke(cli_module.app, ["stage-local-agent-prompt", "--dry-run"])

    assert result.exit_code == 0
    assert "Local coding agent target:" in result.output
    assert "Codex" in result.output
    assert "Agent Bridge" in result.output
    assert not (workspace / "queue" / "in_progress.json").exists()


def test_show_gui_targets_prints_metadata_without_activation(monkeypatch, tmp_path: Path):
    configure_cli(monkeypatch, tmp_path)

    result = CliRunner().invoke(cli_module.app, ["show-gui-targets"])

    assert result.exit_code == 0
    assert "PM assistant target:" in result.output
    assert "Local coding agent target:" in result.output
    assert "App activation: manual-confirmation only for local-agent dispatch." in result.output
    assert "Automatic submit/Enter: not supported." in result.output
    assert automatic_submit_supported() is False


def test_diagnose_codex_input_target_does_not_click_without_click_test(monkeypatch, tmp_path: Path):
    configure_cli(monkeypatch, tmp_path)
    calls: list[str] = []

    class FakeDetector:
        def diagnose_input_target(self, target, **kwargs):
            calls.append("diagnose")
            return CodexInputTargetDiagnostic(
                target_app=target.app_name,
                active_app="Codex",
                codex_app_active=True,
                window_bounds=(0, 0, 1000, 800),
                input_candidate_count=0,
                best_candidate_summary="unknown",
                fallback_strategy=None,
                fallback_enabled=False,
                fallback_click_point=None,
                prompt_presence_verifiable=False,
                live_submit_allowed=False,
                accessibility_available=True,
            )

        def click_window_relative_input(self, target):
            calls.append("click")
            return LocalAgentFocusResult()

    class FakeActivator:
        def activate(self, app_name, **kwargs):
            calls.append(f"activate:{app_name}:{kwargs.get('bundle_id')}")

    monkeypatch.setattr(cli_module, "CodexUIDetector", FakeDetector)
    monkeypatch.setattr(cli_module, "MacOSAppActivator", FakeActivator)

    result = CliRunner().invoke(cli_module.app, ["diagnose-codex-input-target"])

    assert result.exit_code == 0
    assert calls == ["activate:Codex:None", "diagnose"]
    assert "Live submit allowed: no" in result.output


def test_diagnose_codex_windows_reports_rejected_tiny_window(monkeypatch, tmp_path: Path):
    configure_cli(monkeypatch, tmp_path)
    calls: list[str] = []

    class FakeDetector:
        def select_main_window(self, target):
            calls.append("select")
            tiny = CodexWindowInfo(
                index=1,
                title="Tiny utility",
                position=(1762, 1153),
                size=(84, 77),
                bounds=(1762, 1153, 84, 77),
                area=6468,
                visible=True,
                minimized=False,
                fullscreen=False,
                role="AXWindow",
                subrole="AXUnknown",
                rejected=True,
                rejection_reasons=("width 84 < 400", "height 77 < 300"),
            )
            main = CodexWindowInfo(
                index=2,
                title="Main",
                position=(100, 200),
                size=(1000, 700),
                bounds=(100, 200, 1000, 700),
                area=700000,
                visible=True,
                minimized=False,
                fullscreen=False,
                role="AXWindow",
                subrole="AXStandardWindow",
                selected=True,
            )
            return CodexWindowSelectionResult(
                target_app=target.app_name,
                strategy="largest_visible_normal",
                min_width=400,
                min_height=300,
                min_area=120000,
                windows=(tiny, main),
                selected_window=main,
                selected_bounds=main.bounds,
                plausible=True,
            )

    class FakeActivator:
        def activate(self, app_name, **kwargs):
            calls.append(f"activate:{app_name}:{kwargs.get('bundle_id')}")

    monkeypatch.setattr(cli_module, "CodexUIDetector", FakeDetector)
    monkeypatch.setattr(cli_module, "MacOSAppActivator", FakeActivator)

    result = CliRunner().invoke(cli_module.app, ["diagnose-codex-windows"])

    assert result.exit_code == 0
    assert calls == ["activate:Codex:None", "select"]
    assert "Tiny utility" in result.output
    assert "status=rejected" in result.output
    assert "Selected bounds: (100, 200, 1000, 700)" in result.output


def test_diagnose_chatgpt_mac_windows_reports_selected_window(monkeypatch, tmp_path: Path):
    configure_cli(monkeypatch, tmp_path)
    calls: list[str] = []

    class FakeDetector:
        def select_main_window(self, target):
            calls.append(f"select:{target.app_name}")
            main = CodexWindowInfo(
                index=1,
                title="ChatGPT",
                position=(59, 409),
                size=(1594, 698),
                bounds=(59, 409, 1594, 698),
                area=1112612,
                visible=True,
                minimized=False,
                fullscreen=False,
                role="AXWindow",
                subrole="AXStandardWindow",
                selected=True,
            )
            return CodexWindowSelectionResult(
                target_app=target.app_name,
                strategy="largest_visible_normal",
                min_width=400,
                min_height=300,
                min_area=120000,
                windows=(main,),
                selected_window=main,
                selected_bounds=main.bounds,
                plausible=True,
            )

    class FakeActivator:
        def activate(self, app_name, **kwargs):
            calls.append(f"activate:{app_name}:{kwargs.get('bundle_id')}")

    monkeypatch.setattr(cli_module, "CodexUIDetector", FakeDetector)
    monkeypatch.setattr(cli_module, "MacOSAppActivator", FakeActivator)

    result = CliRunner().invoke(cli_module.app, ["diagnose-chatgpt-mac-windows"])

    assert result.exit_code == 0
    assert calls == ["activate:ChatGPT:com.openai.chat", "select:ChatGPT"]
    assert "ChatGPT Window Diagnostic" in result.output
    assert "status=selected" in result.output


def test_diagnose_chatgpt_mac_windows_fails_without_usable_window(monkeypatch, tmp_path: Path):
    configure_cli(monkeypatch, tmp_path)

    class FakeDetector:
        def select_main_window(self, target):
            return CodexWindowSelectionResult(
                target_app=target.app_name,
                strategy="largest_visible_normal",
                min_width=400,
                min_height=300,
                min_area=120000,
                windows=(),
                selected_window=None,
                selected_bounds=None,
                plausible=False,
                error="No ChatGPT windows were reported.",
            )

    class FakeActivator:
        def activate(self, *args, **kwargs):
            return None

    monkeypatch.setattr(cli_module, "CodexUIDetector", FakeDetector)
    monkeypatch.setattr(cli_module, "MacOSAppActivator", FakeActivator)

    result = CliRunner().invoke(cli_module.app, ["diagnose-chatgpt-mac-windows"])

    assert result.exit_code == 1
    assert "ChatGPT Mac visible conversation window is unavailable." in result.output


def test_diagnose_chatgpt_app_targets_reports_native_and_rejected_chrome(
    monkeypatch,
    tmp_path: Path,
):
    configure_cli(monkeypatch, tmp_path)

    diagnostic = ChatGPTAppTargetDiagnostic(
        expected_bundle_id="com.openai.chat",
        configured_app_name="ChatGPT",
        configured_bundle_id="com.openai.chat",
        selected_bundle_id="com.openai.chat",
        native_available=True,
        chrome_pwa_candidates_rejected=1,
        candidates=(
            ChatGPTAppCandidate(
                name="ChatGPT",
                bundle_id="com.openai.chat",
                frontmost=True,
                visible=True,
                window_count=1,
                selected=True,
            ),
            ChatGPTAppCandidate(
                name="ChatGPT",
                bundle_id="com.google.Chrome.app.fake",
                frontmost=False,
                visible=True,
                window_count=1,
                rejected=True,
                rejection_reasons=("chrome_or_pwa_bundle_rejected",),
            ),
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "run_chatgpt_app_target_diagnostic",
        lambda target, profile=None: diagnostic,
    )

    result = CliRunner().invoke(cli_module.app, ["diagnose-chatgpt-app-targets"])

    assert result.exit_code == 0
    assert "Selected native bundle id: com.openai.chat" in result.output
    assert "Chrome/PWA candidates rejected: 1" in result.output


def test_diagnose_chatgpt_app_targets_pm_target_override_selects_chrome(
    monkeypatch,
    tmp_path: Path,
):
    configure_cli(monkeypatch, tmp_path)

    diagnostic = ChatGPTAppTargetDiagnostic(
        expected_bundle_id="com.google.Chrome.app.*",
        configured_app_name="ChatGPT",
        configured_bundle_id=None,
        selected_bundle_id="com.google.Chrome.app.fake",
        native_available=True,
        chrome_pwa_candidates_rejected=0,
        candidates=(
            ChatGPTAppCandidate(
                name="ChatGPT",
                bundle_id="com.openai.chat",
                frontmost=False,
                visible=True,
                window_count=1,
                rejected=True,
                rejection_reasons=("native_chatgpt_bundle_rejected",),
            ),
            ChatGPTAppCandidate(
                name="app_mode_loader",
                bundle_id="com.google.Chrome.app.fake",
                frontmost=True,
                visible=True,
                window_count=1,
                window_summaries=("title=ChatGPT,bounds=(10,20,800,600)",),
                selected=True,
            ),
        ),
        selected_profile=CHATGPT_CHROME_APP_PROFILE,
        chrome_app_available=True,
        native_candidates_rejected=1,
    )
    seen_profiles: list[str | None] = []
    monkeypatch.setattr(
        cli_module,
        "run_chatgpt_app_target_diagnostic",
        lambda target, profile=None: seen_profiles.append(profile) or diagnostic,
    )

    result = CliRunner().invoke(
        cli_module.app,
        ["diagnose-chatgpt-app-targets", "--pm-target", "chatgpt_chrome_app"],
    )

    assert result.exit_code == 0
    assert seen_profiles == [CHATGPT_CHROME_APP_PROFILE]
    assert "Selected PM profile: chatgpt_chrome_app" in result.output
    assert "Selected Chrome app bundle id: com.google.Chrome.app.fake" in result.output
    assert "name=app_mode_loader" in result.output
    assert "window_details=title=ChatGPT,bounds=(10,20,800,600)" in result.output
    assert "Native candidates rejected: 1" in result.output


def test_chatgpt_app_targets_selects_chrome_profile_and_rejects_native():
    completed = SimpleNamespace(
        returncode=0,
        stdout=(
            "ChatGPT\tcom.openai.chat\tfalse\ttrue\t1\ttitle=ChatGPT,bounds=(1,2,3,4)\n"
            "Google Chrome\tcom.google.Chrome\tfalse\ttrue\t2\ttitle=Chrome,bounds=(5,6,7,8)\n"
            "app_mode_loader\tcom.google.Chrome.app.fake\ttrue\ttrue\t1\ttitle=ChatGPT,bounds=(10,20,800,600)\n"
        ),
        stderr="",
    )

    result = diagnose_chatgpt_app_targets(
        target=pm_target_for_profile(ManualStageTarget(app_name="ChatGPT"), CHATGPT_CHROME_APP_PROFILE),
        profile=CHATGPT_CHROME_APP_PROFILE,
        runner=lambda *args, **kwargs: completed,
    )

    assert result.selected_profile == CHATGPT_CHROME_APP_PROFILE
    assert result.selected_bundle_id == "com.google.Chrome.app.fake"
    assert result.chrome_app_available
    assert result.native_candidates_rejected == 1
    selected = next(candidate for candidate in result.candidates if candidate.selected)
    assert selected.name == "app_mode_loader"
    assert selected.window_summaries == ("title=ChatGPT,bounds=(10,20,800,600)",)
    native = next(candidate for candidate in result.candidates if candidate.bundle_id == "com.openai.chat")
    assert native.rejected
    assert native.rejection_reasons == ("native_chatgpt_bundle_rejected",)
    browser = next(candidate for candidate in result.candidates if candidate.bundle_id == "com.google.Chrome")
    assert browser.rejected
    assert browser.rejection_reasons == ("chrome_browser_bundle_rejected",)


def test_chatgpt_app_targets_chrome_profile_does_not_select_browser_bundle():
    completed = SimpleNamespace(
        returncode=0,
        stdout=(
            "Google Chrome\tcom.google.Chrome\ttrue\ttrue\t2\n"
            "Google Chrome Helper\tcom.google.Chrome.helper\tfalse\tfalse\t0\n"
        ),
        stderr="",
    )

    result = diagnose_chatgpt_app_targets(
        target=pm_target_for_profile(ManualStageTarget(app_name="ChatGPT"), CHATGPT_CHROME_APP_PROFILE),
        profile=CHATGPT_CHROME_APP_PROFILE,
        runner=lambda *args, **kwargs: completed,
    )

    assert result.selected_profile == CHATGPT_CHROME_APP_PROFILE
    assert result.selected_bundle_id is None
    assert not result.chrome_app_available
    assert result.error == "Chrome/PWA ChatGPT app target was not found."
    assert all(candidate.rejected for candidate in result.candidates)


def test_chatgpt_app_targets_chrome_profile_prefers_windowed_app_candidate():
    completed = SimpleNamespace(
        returncode=0,
        stdout=(
            "app_mode_loader\tcom.google.Chrome.app.empty\tfalse\ttrue\t0\n"
            "app_mode_loader\tcom.google.Chrome.app.windowed\tfalse\ttrue\t1\n"
        ),
        stderr="",
    )

    result = diagnose_chatgpt_app_targets(
        target=pm_target_for_profile(ManualStageTarget(app_name="ChatGPT"), CHATGPT_CHROME_APP_PROFILE),
        profile=CHATGPT_CHROME_APP_PROFILE,
        runner=lambda *args, **kwargs: completed,
    )

    assert result.selected_bundle_id == "com.google.Chrome.app.windowed"
    selected = next(candidate for candidate in result.candidates if candidate.selected)
    assert selected.bundle_id == "com.google.Chrome.app.windowed"
    empty = next(candidate for candidate in result.candidates if candidate.bundle_id == "com.google.Chrome.app.empty")
    assert empty.rejected
    assert empty.rejection_reasons == ("no_visible_chrome_app_window",)


def test_chatgpt_app_targets_chrome_profile_activates_and_reenumerates_windowless_candidate():
    first = SimpleNamespace(
        returncode=0,
        stdout="app_mode_loader\tcom.google.Chrome.app.fake\t123\tfalse\ttrue\t0\n",
        stderr="",
    )
    second = SimpleNamespace(
        returncode=0,
        stdout=(
            "app_mode_loader\tcom.google.Chrome.app.fake\t123\ttrue\ttrue\t1\t"
            "title=ChatGPT,bounds=(10,20,800,600),minimized=false,fullscreen=false\n"
        ),
        stderr="",
    )
    opened = SimpleNamespace(returncode=0, stdout="", stderr="")
    enumerations = [first, second]
    calls: list[tuple[str, ...]] = []

    def runner(args, **kwargs):
        calls.append(tuple(args))
        if args[0] == "open":
            return opened
        return enumerations.pop(0)

    result = diagnose_chatgpt_app_targets(
        target=pm_target_for_profile(
            ManualStageTarget(app_name="ChatGPT"),
            CHATGPT_CHROME_APP_PROFILE,
        ),
        profile=CHATGPT_CHROME_APP_PROFILE,
        runner=runner,
        sleep_fn=lambda _: None,
    )

    assert calls[1] == ("open", "-b", "com.google.Chrome.app.fake")
    assert result.activation_attempted
    assert result.activation_bundle_id == "com.google.Chrome.app.fake"
    assert result.activation_succeeded is True
    assert result.reenumerated_after_activation
    assert result.selected_bundle_id == "com.google.Chrome.app.fake"
    selected = next(candidate for candidate in result.candidates if candidate.selected)
    assert selected.pid == "123"
    assert selected.window_summaries == (
        "title=ChatGPT,bounds=(10,20,800,600),minimized=false,fullscreen=false",
    )


def test_chatgpt_app_targets_chrome_profile_requires_windowed_app_candidate():
    completed = SimpleNamespace(
        returncode=0,
        stdout="app_mode_loader\tcom.google.Chrome.app.empty\ttrue\ttrue\t0\n",
        stderr="",
    )

    result = diagnose_chatgpt_app_targets(
        target=pm_target_for_profile(ManualStageTarget(app_name="ChatGPT"), CHATGPT_CHROME_APP_PROFILE),
        profile=CHATGPT_CHROME_APP_PROFILE,
        runner=lambda *args, **kwargs: completed,
    )

    assert result.selected_bundle_id is None
    assert not result.chrome_app_available
    assert result.error == "Chrome/PWA ChatGPT app target was not found."
    assert result.candidates[0].rejection_reasons == ("no_visible_chrome_app_window",)


def test_diagnose_visual_state_chrome_app_activates_selected_bundle(
    monkeypatch,
    tmp_path: Path,
):
    configure_cli(monkeypatch, tmp_path)
    calls: list[str] = []
    diagnostic = ChatGPTAppTargetDiagnostic(
        expected_bundle_id="com.google.Chrome.app.*",
        configured_app_name="ChatGPT",
        configured_bundle_id=None,
        selected_bundle_id="com.google.Chrome.app.fake",
        native_available=True,
        chrome_pwa_candidates_rejected=0,
        candidates=(
            ChatGPTAppCandidate(
                name="app_mode_loader",
                bundle_id="com.google.Chrome.app.fake",
                frontmost=True,
                visible=True,
                window_count=1,
                window_summaries=("title=ChatGPT,bounds=(10,20,800,600)",),
                selected=True,
            ),
        ),
        selected_profile=CHATGPT_CHROME_APP_PROFILE,
        chrome_app_available=True,
    )
    monkeypatch.setattr(
        cli_module,
        "run_chatgpt_app_target_diagnostic",
        lambda target, profile=None: diagnostic,
    )

    class FakeActivator:
        def activate(self, app_name, **kwargs):
            calls.append(f"activate:{app_name}:{kwargs.get('bundle_id')}")

    class FakeWindowDetector:
        def select_main_window(self, target):
            calls.append(f"select:{target.app_name}:{target.bundle_id}")
            main = CodexWindowInfo(
                index=1,
                title="ChatGPT",
                position=(10, 20),
                size=(800, 600),
                bounds=(10, 20, 800, 600),
                area=480000,
                visible=True,
                minimized=False,
                fullscreen=False,
                role="AXWindow",
                subrole="AXStandardWindow",
                selected=True,
            )
            return CodexWindowSelectionResult(
                target_app=target.app_name,
                strategy="largest_visible_normal",
                min_width=400,
                min_height=300,
                min_area=120000,
                windows=(main,),
                selected_window=main,
                selected_bounds=main.bounds,
                plausible=True,
            )

    class FakeStateDetector:
        def detect(self, *, target, window_bounds, profile, logs_dir, write_debug):
            calls.append(f"detect:{target.app_name}:{target.bundle_id}:{profile.profile_id}:{window_bounds}")
            return VisualStateDetection(
                selected_app=target.app_name,
                asset_profile=profile.profile_id,
                window_bounds=window_bounds,
                safe_region_bounds=(10, 300, 800, 200),
                screenshot_captured=True,
                backend_available=True,
                matched_state=VisualGuiState.UNKNOWN,
            )

    monkeypatch.setattr(cli_module, "MacOSAppActivator", FakeActivator)
    monkeypatch.setattr(cli_module, "CodexUIDetector", FakeWindowDetector)
    monkeypatch.setattr(cli_module, "AssetVisualStateDetector", FakeStateDetector)

    result = CliRunner().invoke(
        cli_module.app,
        ["diagnose-visual-state", "--app", "chatgpt_chrome_app"],
    )

    assert result.exit_code == 0
    assert calls == [
        "activate:app_mode_loader:com.google.Chrome.app.fake",
        "select:app_mode_loader:com.google.Chrome.app.fake",
        "detect:app_mode_loader:com.google.Chrome.app.fake:chatgpt_chrome_app:(10, 20, 800, 600)",
    ]
    assert "Selected asset profile: chatgpt_chrome_app" in result.output


def test_set_app_window_bounds_only_targets_chrome_app(monkeypatch, tmp_path: Path):
    configure_cli(monkeypatch, tmp_path)
    result = CliRunner().invoke(
        cli_module.app,
        ["set-app-window-bounds", "--app", "chatgpt_mac", "--bounds", "100,100,1000,700"],
    )

    assert result.exit_code == 2
    assert "--app must be chatgpt_chrome_app" in result.output


def test_set_app_window_bounds_uses_selected_chrome_app_bundle(monkeypatch, tmp_path: Path):
    configure_cli(monkeypatch, tmp_path)
    calls: list[str] = []
    target = ManualStageTarget(
        app_name="app_mode_loader",
        bundle_id="com.google.Chrome.app.fake",
        profile=CHATGPT_CHROME_APP_PROFILE,
        visual_asset_profile=CHATGPT_CHROME_APP_PROFILE,
        backend="chatgpt_chrome_app_visual",
    )

    monkeypatch.setattr(
        cli_module,
        "_resolve_pm_profile_target",
        lambda profile: calls.append(f"resolve:{profile}") or target,
    )

    class FakeActivator:
        def activate(self, app_name, **kwargs):
            calls.append(f"activate:{app_name}:{kwargs.get('bundle_id')}")

    class FakeDetector:
        def select_main_window(self, selected_target):
            calls.append(f"select:{selected_target.app_name}:{selected_target.bundle_id}")
            main = CodexWindowInfo(
                index=1,
                title="ChatGPT",
                position=(100, 100),
                size=(1000, 700),
                bounds=(100, 100, 1000, 700),
                area=700000,
                visible=True,
                minimized=False,
                fullscreen=False,
                role="AXWindow",
                subrole="AXStandardWindow",
                selected=True,
            )
            return CodexWindowSelectionResult(
                target_app=selected_target.app_name,
                strategy="largest_visible_normal",
                min_width=400,
                min_height=300,
                min_area=120000,
                windows=(main,),
                selected_window=main,
                selected_bounds=main.bounds,
                plausible=True,
            )

    def fake_set_bounds(*, target, bounds):
        calls.append(f"set:{target.app_name}:{target.bundle_id}:{bounds}")
        return AppWindowBoundsResult(
            target_app=target.app_name,
            target_bundle_id=target.bundle_id,
            requested_bounds=bounds,
            before_bounds=(0, 465, 735, 432),
            after_bounds=bounds,
            succeeded=True,
        )

    monkeypatch.setattr(cli_module, "MacOSAppActivator", FakeActivator)
    monkeypatch.setattr(cli_module, "CodexUIDetector", FakeDetector)
    monkeypatch.setattr(cli_module, "run_set_app_window_bounds", fake_set_bounds)

    result = CliRunner().invoke(
        cli_module.app,
        [
            "set-app-window-bounds",
            "--app",
            "chatgpt_chrome_app",
            "--bounds",
            "100,100,1000,700",
        ],
    )

    assert result.exit_code == 0
    assert calls == [
        "resolve:chatgpt_chrome_app",
        "activate:app_mode_loader:com.google.Chrome.app.fake",
        "set:app_mode_loader:com.google.Chrome.app.fake:(100, 100, 1000, 700)",
        "select:app_mode_loader:com.google.Chrome.app.fake",
    ]
    assert "After bounds: (100, 100, 1000, 700)" in result.output


def test_chatgpt_app_targets_rejects_chrome_for_mac_profile():
    completed = SimpleNamespace(
        returncode=0,
        stdout=(
            "ChatGPT\tcom.openai.chat\ttrue\ttrue\t1\n"
            "ChatGPT\tcom.google.Chrome.app.fake\tfalse\ttrue\t1\n"
        ),
        stderr="",
    )

    result = diagnose_chatgpt_app_targets(
        target=pm_target_for_profile(ManualStageTarget(app_name="ChatGPT"), CHATGPT_MAC_PROFILE),
        profile=CHATGPT_MAC_PROFILE,
        runner=lambda *args, **kwargs: completed,
    )

    assert result.selected_profile == CHATGPT_MAC_PROFILE
    assert result.selected_bundle_id == "com.openai.chat"
    chrome = next(
        candidate for candidate in result.candidates if candidate.bundle_id == "com.google.Chrome.app.fake"
    )
    assert chrome.rejected
    assert chrome.rejection_reasons == ("chrome_or_pwa_bundle_rejected",)


def test_preflight_chatgpt_mac_native_target_reports_activation_method(
    monkeypatch,
    tmp_path: Path,
):
    configure_cli(monkeypatch, tmp_path)

    activation = ActivationResult(
        app_name="ChatGPT",
        succeeded=True,
        attempts=(
            ActivationAttempt(
                strategy="osascript-bundle-id",
                command=(
                    "osascript",
                    "-e",
                    'tell application id "com.openai.chat" to activate',
                ),
                succeeded=True,
            ),
        ),
    )
    app_targets = ChatGPTAppTargetDiagnostic(
        expected_bundle_id="com.openai.chat",
        configured_app_name="ChatGPT",
        configured_bundle_id="com.openai.chat",
        selected_bundle_id="com.openai.chat",
        native_available=True,
        chrome_pwa_candidates_rejected=0,
        candidates=(),
    )
    preflight = ChatGPTNativePreflightResult(
        target=load_gui_targets(tmp_path / "config").pm_assistant,
        activation_result=activation,
        app_targets=app_targets,
        selected_native_bundle_id="com.openai.chat",
        activation_method="osascript-bundle-id",
        succeeded=True,
    )
    monkeypatch.setattr(
        cli_module,
        "run_chatgpt_mac_native_preflight",
        lambda target: preflight,
    )

    result = CliRunner().invoke(cli_module.app, ["preflight-chatgpt-mac-native-target"])

    assert result.exit_code == 0
    assert "Activation method: osascript-bundle-id" in result.output
    assert "Selected native bundle id: com.openai.chat" in result.output


def test_diagnose_codex_input_target_click_test_requires_explicit_flag(monkeypatch, tmp_path: Path):
    configure_cli(monkeypatch, tmp_path)
    calls: list[str] = []

    class FakeDetector:
        def click_visual_input(self, target, **kwargs):
            calls.append("visual")
            return LocalAgentFocusResult(error="no visual target")

        def click_direct_plus_anchor(self, target, **kwargs):
            calls.append("direct")
            return LocalAgentFocusResult(error="no direct target")

        def click_window_relative_input(self, target):
            calls.append("click")
            return LocalAgentFocusResult(
                active_app_after="Codex",
                app_frontmost=True,
                window_bounds=(0, 0, 1000, 800),
                fallback_click_point=(500, 736),
                focused_element_summary="AXTextArea",
                succeeded=True,
                used_fallback=True,
            )

    class FakeActivator:
        def activate(self, *args, **kwargs):
            calls.append("activate")

    monkeypatch.setattr(cli_module, "CodexUIDetector", FakeDetector)
    monkeypatch.setattr(cli_module, "MacOSAppActivator", FakeActivator)

    result = CliRunner().invoke(cli_module.app, ["diagnose-codex-input-target", "--click-test"])

    assert result.exit_code == 0
    assert calls == ["activate", "direct"]
    assert "Click-test failed" in result.output


def test_diagnose_codex_input_target_paste_test_passes_paste_backend(
    monkeypatch,
    tmp_path: Path,
):
    configure_cli(monkeypatch, tmp_path)
    calls: list[tuple[str, str | None]] = []

    class FakeDetector:
        def run_paste_test(self, target, **kwargs):
            calls.append(("paste_backend", kwargs.get("paste_backend")))
            return SimpleNamespace(error=None)

    class FakeActivator:
        def activate(self, *args, **kwargs):
            calls.append(("activate", None))

    monkeypatch.setattr(cli_module, "CodexUIDetector", FakeDetector)
    monkeypatch.setattr(cli_module, "MacOSAppActivator", FakeActivator)
    monkeypatch.setattr(cli_module, "format_codex_paste_test_result", lambda _result: "paste ok")

    result = CliRunner().invoke(
        cli_module.app,
        [
            "diagnose-codex-input-target",
            "--paste-test",
            "--click-backend",
            "pyautogui",
            "--paste-backend",
            "pyautogui",
        ],
    )

    assert result.exit_code == 0
    assert calls == [("activate", None), ("paste_backend", "pyautogui")]
    assert "paste ok" in result.output


def test_diagnose_codex_input_target_focus_target_test_passes_click_backend(
    monkeypatch,
    tmp_path: Path,
):
    configure_cli(monkeypatch, tmp_path)
    calls: list[tuple[str, str | None]] = []

    class FakeDetector:
        def run_focus_target_test(self, target, **kwargs):
            calls.append(("click_backend", kwargs.get("click_backend")))
            return SimpleNamespace(error=None)

    class FakeActivator:
        def activate(self, *args, **kwargs):
            calls.append(("activate", None))

    monkeypatch.setattr(cli_module, "CodexUIDetector", FakeDetector)
    monkeypatch.setattr(cli_module, "MacOSAppActivator", FakeActivator)
    monkeypatch.setattr(
        cli_module,
        "format_codex_focus_target_comparison",
        lambda _result: "focus target ok",
    )

    result = CliRunner().invoke(
        cli_module.app,
        [
            "diagnose-codex-input-target",
            "--focus-target-test",
            "--click-backend",
            "pyautogui",
        ],
    )

    assert result.exit_code == 0
    assert calls == [("activate", None), ("click_backend", "pyautogui")]
    assert "focus target ok" in result.output
