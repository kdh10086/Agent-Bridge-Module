from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_bridge.gui.macos_apps import (
    CHATGPT_CHROME_APP_PROFILE,
    CHATGPT_MAC_PROFILE,
    ManualStageTarget,
    ensure_chatgpt_chrome_app_target,
    ensure_native_chatgpt_mac_target,
    normalize_pm_target_profile,
    pm_target_for_profile,
)


CANONICAL_PM_VISUAL_SEQUENCE: tuple[str, ...] = (
    "resolve_selected_pm_profile",
    "resolve_target_app_window",
    "activate_target",
    "select_target_window",
    "capture_bounded_screenshot",
    "detect_visual_state",
    "wait_or_retry_state_policy",
    "detect_plus_anchor",
    "compute_composer_click_point",
    "click_focus_composer",
    "set_clipboard_to_pm_prompt",
    "verify_clipboard_readback",
    "paste_with_configured_backend",
    "record_post_paste_diagnostics",
    "submit_pm_prompt",
    "wait_for_response_generation",
    "reactivate_pm_target_before_response_copy",
    "detect_and_click_response_copy",
    "save_pm_response",
    "extract_codex_next_prompt",
    "handoff_to_safetygate_commandqueue_dispatcher_codex",
)


@dataclass(frozen=True)
class PMVisualProfile:
    name: str
    backend: str
    asset_profile: str
    expected_bundle_id: str | None
    uses_chrome_dom_javascript: bool = False


@dataclass(frozen=True)
class PMVisualAssetInventoryItem:
    role: str
    path: str
    exists: bool
    image_size: tuple[int, int] | None = None
    error: str | None = None


@dataclass(frozen=True)
class VisualPMController:
    """Profile-driven PM visual control contract.

    The Chrome/PWA path established the canonical PM visual sequence. Both PM
    profiles use the same PyAutoGUI/window-bounded asset-state-machine stages;
    only target resolution, asset profile, and thresholds differ by profile.
    """

    profile: PMVisualProfile
    target: ManualStageTarget

    @classmethod
    def for_profile(
        cls,
        configured_target: ManualStageTarget,
        profile_name: str | None,
    ) -> "VisualPMController":
        normalized = normalize_pm_target_profile(profile_name)
        target = pm_target_for_profile(configured_target, normalized)
        return cls.for_target(target)

    @classmethod
    def for_target(cls, target: ManualStageTarget) -> "VisualPMController":
        normalized = _visual_pm_profile_name(target)
        if normalized == CHATGPT_MAC_PROFILE:
            prepared = ensure_native_chatgpt_mac_target(target)
            return cls(
                profile=PMVisualProfile(
                    name=CHATGPT_MAC_PROFILE,
                    backend="chatgpt_mac_visual",
                    asset_profile=CHATGPT_MAC_PROFILE,
                    expected_bundle_id="com.openai.chat",
                ),
                target=prepared,
            )
        if normalized == CHATGPT_CHROME_APP_PROFILE:
            prepared = ensure_chatgpt_chrome_app_target(target)
            return cls(
                profile=PMVisualProfile(
                    name=CHATGPT_CHROME_APP_PROFILE,
                    backend="chatgpt_chrome_app_visual",
                    asset_profile=CHATGPT_CHROME_APP_PROFILE,
                    expected_bundle_id=prepared.bundle_id,
                ),
                target=prepared,
            )
        raise ValueError(f"Unsupported visual PM profile: {normalized}")

    @property
    def canonical_sequence(self) -> tuple[str, ...]:
        return CANONICAL_PM_VISUAL_SEQUENCE


def is_visual_pm_target(target: ManualStageTarget | None) -> bool:
    if target is None:
        return False
    try:
        _visual_pm_profile_name(target)
    except ValueError:
        return False
    return True


def normalize_visual_pm_target(target: ManualStageTarget) -> ManualStageTarget:
    return VisualPMController.for_target(target).target


def visual_pm_asset_directory(profile_name: str) -> str:
    normalized = normalize_pm_target_profile(profile_name)
    return f"assets/gui/{normalized}/"


def expected_visual_pm_asset_paths(profile_name: str) -> tuple[tuple[str, str], ...]:
    normalized = normalize_pm_target_profile(profile_name)
    if normalized == CHATGPT_MAC_PROFILE:
        prefix = "assets/gui/chatgpt_mac"
        return (
            ("plus_light", f"{prefix}/chatgpt_mac_plus_button_light.png"),
            ("plus_dark", f"{prefix}/chatgpt_mac_plus_button_dark.png"),
            ("send_disabled_light", f"{prefix}/chatgpt_mac_send_disabled_button_light.png"),
            ("send_disabled_dark", f"{prefix}/chatgpt_mac_send_disabled_button_dark.png"),
            ("send_light", f"{prefix}/chatgpt_mac_send_button_light.png"),
            ("send_dark", f"{prefix}/chatgpt_mac_send_button_dark.png"),
            ("stop_light", f"{prefix}/chatgpt_mac_stop_button_light.png"),
            ("stop_dark", f"{prefix}/chatgpt_mac_stop_button_dark.png"),
            ("copy_response_light", f"{prefix}/chatgpt_mac_copy_response_button_light.png"),
            ("copy_response_dark", f"{prefix}/chatgpt_mac_copy_response_button_dark.png"),
            ("scroll_down_light", f"{prefix}/chatgpt_mac_scroll_down_button_light.png"),
            ("scroll_down_dark", f"{prefix}/chatgpt_mac_scroll_down_button_dark.png"),
        )
    if normalized == CHATGPT_CHROME_APP_PROFILE:
        prefix = "assets/gui/chatgpt_chrome_app"
        return (
            ("plus_light", f"{prefix}/chatgpt_chrome_app_plus_button_light.png"),
            ("plus_dark", f"{prefix}/chatgpt_chrome_app_plus_button_dark.png"),
            ("voice_light", f"{prefix}/chatgpt_chrome_app_voice_button_light.png"),
            ("voice_dark", f"{prefix}/chatgpt_chrome_app_voice_button_dark.png"),
            ("send_light", f"{prefix}/chatgpt_chrome_app_send_button_light.png"),
            ("send_dark", f"{prefix}/chatgpt_chrome_app_send_button_dark.png"),
            ("stop_light", f"{prefix}/chatgpt_chrome_app_stop_button_light.png"),
            ("stop_dark", f"{prefix}/chatgpt_chrome_app_stop_button_dark.png"),
            (
                "copy_response_light",
                f"{prefix}/chatgpt_chrome_app_copy_response_button_light.png",
            ),
            (
                "copy_response_dark",
                f"{prefix}/chatgpt_chrome_app_copy_response_button_dark.png",
            ),
            ("scroll_down_light", f"{prefix}/chatgpt_chrome_app_scroll_down_button_light.png"),
            ("scroll_down_dark", f"{prefix}/chatgpt_chrome_app_scroll_down_button_dark.png"),
        )
    raise ValueError(f"Unsupported visual PM profile: {profile_name}")


def collect_visual_pm_asset_inventory(
    profile_name: str,
    *,
    root: Path | None = None,
) -> tuple[PMVisualAssetInventoryItem, ...]:
    base = root or Path.cwd()
    items: list[PMVisualAssetInventoryItem] = []
    for role, path in expected_visual_pm_asset_paths(profile_name):
        full_path = base / path
        exists = full_path.exists()
        image_size = None
        error = None
        if exists:
            try:
                image_size = _png_size(full_path)
            except Exception as exc:  # pragma: no cover - defensive diagnostics.
                error = str(exc)
        items.append(
            PMVisualAssetInventoryItem(
                role=role,
                path=path,
                exists=exists,
                image_size=image_size,
                error=error,
            )
        )
    return tuple(items)


def missing_visual_pm_assets(
    profile_name: str,
    *,
    root: Path | None = None,
) -> tuple[PMVisualAssetInventoryItem, ...]:
    return tuple(item for item in collect_visual_pm_asset_inventory(profile_name, root=root) if not item.exists)


def _visual_pm_profile_name(target: ManualStageTarget) -> str:
    backend = (target.backend or "").strip().lower().replace("-", "_")
    asset_profile = (target.visual_asset_profile or "").strip().lower().replace("-", "_")
    target_profile = (target.profile or "").strip().lower().replace("-", "_")
    if (
        backend == "chatgpt_mac_visual"
        or asset_profile == CHATGPT_MAC_PROFILE
        or target_profile == CHATGPT_MAC_PROFILE
    ):
        return CHATGPT_MAC_PROFILE
    if (
        backend == "chatgpt_chrome_app_visual"
        or asset_profile == CHATGPT_CHROME_APP_PROFILE
        or target_profile == CHATGPT_CHROME_APP_PROFILE
    ):
        return CHATGPT_CHROME_APP_PROFILE
    raise ValueError(
        "Target is not a supported visual PM profile "
        f"(profile={target.profile!r}, asset_profile={target.visual_asset_profile!r}, "
        f"backend={target.backend!r})."
    )


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    png_signature = b"\x89PNG\r\n\x1a\n"
    if len(data) < 24 or not data.startswith(png_signature):
        raise ValueError("not a PNG file")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return (width, height)
