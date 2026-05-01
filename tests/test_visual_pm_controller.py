from __future__ import annotations

from agent_bridge.gui.macos_apps import (
    CHATGPT_CHROME_APP_PROFILE,
    CHATGPT_MAC_PROFILE,
    ManualStageTarget,
    default_gui_targets,
)
from agent_bridge.gui.visual_pm_controller import (
    CANONICAL_PM_VISUAL_SEQUENCE,
    VisualPMController,
    collect_visual_pm_asset_inventory,
    expected_visual_pm_asset_paths,
    is_visual_pm_target,
    missing_visual_pm_assets,
)


def test_shared_visual_pm_controller_supports_both_profiles():
    targets = default_gui_targets()

    mac = VisualPMController.for_profile(targets.pm_assistant, CHATGPT_MAC_PROFILE)
    chrome = VisualPMController.for_profile(
        targets.pm_assistant,
        CHATGPT_CHROME_APP_PROFILE,
    )

    assert mac.canonical_sequence == CANONICAL_PM_VISUAL_SEQUENCE
    assert chrome.canonical_sequence == CANONICAL_PM_VISUAL_SEQUENCE
    assert "paste_with_configured_backend" in mac.canonical_sequence
    assert len(mac.canonical_sequence) == 21
    assert mac.profile.name == CHATGPT_MAC_PROFILE
    assert chrome.profile.name == CHATGPT_CHROME_APP_PROFILE


def test_chatgpt_mac_visual_profile_uses_native_target_and_assets():
    target = VisualPMController.for_profile(
        default_gui_targets().pm_assistant,
        CHATGPT_MAC_PROFILE,
    ).target

    assert target.bundle_id == "com.openai.chat"
    assert target.app_path == "/Applications/ChatGPT.app"
    assert target.visual_asset_profile == "chatgpt_mac"
    assert target.paste_backend == "menu_paste_accessibility"
    assert target.paste_backends == (
        "menu_paste_accessibility",
        "system_events_key_code_v_command",
    )
    assert target.plus_anchor_x_offset == 0
    assert target.plus_anchor_y_offset == 40
    assert target.visual_plus_confidence_threshold == 0.58
    assert target.visual_state_confidence_threshold == 0.85
    assert target.visual_appearance_score_threshold == 70.0
    assert all("assets/gui/chatgpt_mac/" in path for path in target.visual_plus_templates)
    assert all("assets/gui/chatgpt_mac/" in path for path in target.visual_send_templates)
    assert is_visual_pm_target(target)


def test_chatgpt_chrome_app_visual_profile_uses_chrome_app_assets_only():
    target = VisualPMController.for_profile(
        default_gui_targets().pm_assistant,
        CHATGPT_CHROME_APP_PROFILE,
    ).target

    assert target.profile == "chatgpt_chrome_app"
    assert target.backend == "chatgpt_chrome_app_visual"
    assert target.visual_asset_profile == "chatgpt_chrome_app"
    assert target.paste_backend == "menu_paste_accessibility"
    assert target.paste_backends == (
        "menu_paste_accessibility",
        "system_events_key_code_v_command",
    )
    assert target.plus_anchor_x_offset == 40
    assert target.plus_anchor_y_offset == 0
    all_templates = (
        *target.visual_plus_templates,
        *target.visual_send_disabled_templates,
        *target.visual_send_templates,
        *target.visual_stop_templates,
    )
    assert all("assets/gui/chatgpt_chrome_app/" in path for path in all_templates)
    assert all("assets/gui/chatgpt_mac/" not in path for path in all_templates)
    assert is_visual_pm_target(target)


def test_visual_pm_controller_prevents_profile_asset_mixing():
    inconsistent = ManualStageTarget(
        app_name="ChatGPT",
        backend="chatgpt_mac_visual",
        profile="chatgpt_chrome_app",
        visual_asset_profile="chatgpt_mac",
    )

    target = VisualPMController.for_profile(
        inconsistent,
        CHATGPT_CHROME_APP_PROFILE,
    ).target

    assert target.backend == "chatgpt_chrome_app_visual"
    assert target.profile == "chatgpt_chrome_app"
    assert target.visual_asset_profile == "chatgpt_chrome_app"
    assert all("assets/gui/chatgpt_chrome_app/" in path for path in target.visual_plus_templates)


def test_chatgpt_mac_asset_inventory_uses_native_asset_directory():
    inventory = collect_visual_pm_asset_inventory(CHATGPT_MAC_PROFILE)

    assert inventory
    assert all(item.path.startswith("assets/gui/chatgpt_mac/") for item in inventory)
    assert {item.role for item in inventory} >= {
        "plus_light",
        "send_disabled_light",
        "send_light",
        "stop_light",
        "copy_response_light",
        "scroll_down_light",
    }
    assert not missing_visual_pm_assets(CHATGPT_MAC_PROFILE)
    assert all(item.image_size for item in inventory)


def test_expected_visual_pm_assets_are_profile_isolated():
    mac_paths = {path for _role, path in expected_visual_pm_asset_paths(CHATGPT_MAC_PROFILE)}
    chrome_paths = {
        path for _role, path in expected_visual_pm_asset_paths(CHATGPT_CHROME_APP_PROFILE)
    }

    assert all("assets/gui/chatgpt_mac/" in path for path in mac_paths)
    assert all("assets/gui/chatgpt_chrome_app/" in path for path in chrome_paths)
    assert mac_paths.isdisjoint(chrome_paths)
