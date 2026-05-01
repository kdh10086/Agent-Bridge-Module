from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml


NATIVE_CHATGPT_MAC_BUNDLE_ID = "com.openai.chat"
NATIVE_CHATGPT_MAC_APP_PATH = "/Applications/ChatGPT.app"
CHROME_BUNDLE_ID_PREFIXES = ("com.google.Chrome",)
CHROME_APP_BUNDLE_ID_PREFIXES = ("com.google.Chrome.app.",)
CHATGPT_MAC_PROFILE = "chatgpt_mac"
CHATGPT_CHROME_APP_PROFILE = "chatgpt_chrome_app"
SUPPORTED_PM_TARGET_PROFILES = (CHATGPT_MAC_PROFILE, CHATGPT_CHROME_APP_PROFILE)


@dataclass(frozen=True)
class ManualStageTarget:
    app_name: str
    app_path: str | None = None
    bundle_id: str | None = None
    backend: str | None = None
    profile: str | None = None
    require_backend_preflight: bool = False
    window_hint: str | None = None
    paste_instruction: str | None = None
    focus_strategy: str | None = None
    visual_asset_profile: str | None = None
    response_copy_css_selector: str | None = None
    response_copy_xpath: str | None = None
    response_copy_full_xpath: str | None = None
    response_copy_strategy: str | None = None
    idle_empty_timeout_seconds: int | None = None
    input_focus_strategy: str | None = None
    click_backend: str = "system_events"
    visual_anchor_click_backend: str = "system_events"
    paste_backend: str = "system_events"
    paste_backends: tuple[str, ...] = ()
    input_click_x_ratio: float | None = None
    input_click_y_ratio: float | None = None
    require_prompt_presence_verification: bool = True
    allow_unverified_submit: bool = False
    allow_unverified_submit_for_noop_dogfood: bool = False
    composer_placeholder_text: str | None = None
    idle_empty_wait_timeout_seconds: int = 600
    idle_empty_poll_interval_seconds: int = 10
    dedicated_automation_session: bool = True
    allow_overwrite_after_idle_timeout: bool = True
    stop_on_idle_timeout: bool = False
    plus_anchor_enabled: bool = True
    plus_anchor_x_offset: int = 0
    plus_anchor_y_offset: int = 50
    direct_plus_anchor_enabled: bool = False
    direct_plus_anchor_x_offset: int = 0
    direct_plus_anchor_y_offset: int = 50
    direct_plus_anchor_y_offset_candidates: tuple[int, ...] = (50,)
    composer_policy_mode: str = "dedicated_automation_session"
    busy_placeholder_wait_timeout_seconds: int = 600
    busy_placeholder_poll_interval_seconds: int = 10
    on_busy_timeout: str = "overwrite"
    visual_text_recognition_enabled: bool = True
    visual_text_recognition_ocr_backend: str = "pytesseract"
    visual_text_recognition_marker_text: str = "AGENT_BRIDGE_CODEX_PASTE_TEST_DO_NOT_SUBMIT"
    visual_text_recognition_placeholder_text: str | None = "후속 변경 사항을 부탁하세요"
    visual_text_recognition_search_region: str = "lower_composer_band"
    visual_plus_templates: tuple[str, ...] = ()
    visual_send_disabled_templates: tuple[str, ...] = ()
    visual_send_templates: tuple[str, ...] = ()
    visual_stop_templates: tuple[str, ...] = ()
    visual_plus_confidence_threshold: float = 0.80
    visual_state_confidence_threshold: float = 0.80
    visual_response_copy_confidence_threshold: float | None = None
    visual_scroll_down_confidence_threshold: float | None = None
    visual_state_ambiguity_margin: float = 0.02
    visual_state_search_region: str = "lower_control_band"
    visual_plus_multiscale_enabled: bool = True
    visual_scale_min: float = 0.70
    visual_scale_max: float = 1.30
    visual_scale_step: float = 0.10
    visual_appearance_score_threshold: float | None = None
    max_state_machine_attempts: int = 3
    state_machine_retry_delay_seconds: float = 0.5
    max_action_attempts: int = 3
    action_retry_delay_seconds: float = 0.5
    submit_after_paste_max_attempts: int = 100
    owner_reviewed_focus_candidates: tuple[dict[str, Any], ...] = ()
    min_main_window_width: int = 400
    min_main_window_height: int = 300
    min_main_window_area: int = 120000
    window_selection_strategy: str = "largest_visible_normal"


@dataclass(frozen=True)
class GuiTargets:
    pm_assistant: ManualStageTarget
    local_agent: ManualStageTarget


class AppActivationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ActivationAttempt:
    strategy: str
    command: tuple[str, ...]
    succeeded: bool
    output: str = ""


@dataclass(frozen=True)
class ActivationResult:
    app_name: str
    succeeded: bool
    attempts: tuple[ActivationAttempt, ...]

    @property
    def winning_strategy(self) -> str | None:
        for attempt in self.attempts:
            if attempt.succeeded:
                return attempt.strategy
        return None


class AppActivator:
    def activate(
        self,
        app_name: str,
        *,
        app_path: str | None = None,
        bundle_id: str | None = None,
    ) -> None:
        raise NotImplementedError


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass
class MacOSAppActivator(AppActivator):
    osascript_executable: str = "osascript"
    open_executable: str = "open"
    runner: Runner = subprocess.run

    def _activation_commands(
        self,
        app_name: str,
        *,
        app_path: str | None = None,
        bundle_id: str | None = None,
    ) -> list[tuple[str, tuple[str, ...]]]:
        escaped_app_name = app_name.replace("\\", "\\\\").replace('"', '\\"')
        escaped_bundle_id = (
            bundle_id.replace("\\", "\\\\").replace('"', '\\"') if bundle_id else None
        )
        if escaped_bundle_id:
            commands = [
                (
                    "osascript-bundle-id",
                    (
                        self.osascript_executable,
                        "-e",
                        f'tell application id "{escaped_bundle_id}" to activate',
                    ),
                ),
                ("open-bundle-id", (self.open_executable, "-b", bundle_id)),
            ]
            if app_path:
                commands.append(("open-app-path", (self.open_executable, app_path)))
            commands.append(
                (
                    "osascript-app-name-verified",
                    (
                        self.osascript_executable,
                        "-e",
                        _verified_display_name_activation_script(
                            escaped_app_name,
                            escaped_bundle_id,
                        ),
                    ),
                )
            )
            return commands
        commands = [
            (
                "osascript",
                (
                    self.osascript_executable,
                    "-e",
                    f'tell application "{escaped_app_name}" to activate',
                ),
            ),
            ("open-app-name", (self.open_executable, "-a", app_name)),
        ]
        if app_path:
            commands.append(("open-app-path", (self.open_executable, app_path)))
        return commands

    def activation_plan(
        self,
        app_name: str,
        *,
        app_path: str | None = None,
        bundle_id: str | None = None,
    ) -> list[tuple[str, tuple[str, ...]]]:
        return self._activation_commands(app_name, app_path=app_path, bundle_id=bundle_id)

    def activate_with_result(
        self,
        app_name: str,
        *,
        app_path: str | None = None,
        bundle_id: str | None = None,
    ) -> ActivationResult:
        attempts: list[ActivationAttempt] = []
        for strategy, command in self._activation_commands(
            app_name,
            app_path=app_path,
            bundle_id=bundle_id,
        ):
            completed = self.runner(
                list(command),
                check=False,
                text=True,
                capture_output=True,
            )
            output = (completed.stderr or completed.stdout or "").strip()
            attempt = ActivationAttempt(
                strategy=strategy,
                command=command,
                succeeded=completed.returncode == 0,
                output=output,
            )
            attempts.append(attempt)
            if attempt.succeeded:
                return ActivationResult(app_name=app_name, succeeded=True, attempts=tuple(attempts))
        return ActivationResult(app_name=app_name, succeeded=False, attempts=tuple(attempts))

    def activate(
        self,
        app_name: str,
        *,
        app_path: str | None = None,
        bundle_id: str | None = None,
    ) -> None:
        result = self.activate_with_result(app_name, app_path=app_path, bundle_id=bundle_id)
        if not result.succeeded:
            raise AppActivationError(format_activation_result(result))


PM_ASSISTANT_TARGET = ManualStageTarget(
    app_name="ChatGPT",
    app_path=NATIVE_CHATGPT_MAC_APP_PATH,
    bundle_id=NATIVE_CHATGPT_MAC_BUNDLE_ID,
    backend="chatgpt_mac_visual",
    profile=CHATGPT_MAC_PROFILE,
    require_backend_preflight=False,
    window_hint="ChatGPT",
    paste_instruction="Paste into the ChatGPT composer, then review manually. Do not submit automatically.",
    focus_strategy="visual_plus_anchor",
    visual_asset_profile="chatgpt_mac",
    idle_empty_timeout_seconds=600,
    idle_empty_poll_interval_seconds=10,
    click_backend="pyautogui",
    visual_anchor_click_backend="pyautogui",
    paste_backend="menu_paste_accessibility",
    paste_backends=("menu_paste_accessibility", "system_events_key_code_v_command"),
    plus_anchor_x_offset=0,
    plus_anchor_y_offset=40,
    visual_plus_templates=(
        "assets/gui/chatgpt_mac/chatgpt_mac_plus_button_light.png",
        "assets/gui/chatgpt_mac/chatgpt_mac_plus_button_dark.png",
    ),
    visual_send_disabled_templates=(
        "assets/gui/chatgpt_mac/chatgpt_mac_send_disabled_button_light.png",
        "assets/gui/chatgpt_mac/chatgpt_mac_send_disabled_button_dark.png",
    ),
    visual_send_templates=(
        "assets/gui/chatgpt_mac/chatgpt_mac_send_button_light.png",
        "assets/gui/chatgpt_mac/chatgpt_mac_send_button_dark.png",
    ),
    visual_stop_templates=(
        "assets/gui/chatgpt_mac/chatgpt_mac_stop_button_light.png",
        "assets/gui/chatgpt_mac/chatgpt_mac_stop_button_dark.png",
    ),
    visual_plus_confidence_threshold=0.58,
    visual_state_confidence_threshold=0.85,
    visual_state_ambiguity_margin=0.03,
    visual_appearance_score_threshold=70.0,
)


CHATGPT_CHROME_APP_TARGET = ManualStageTarget(
    app_name="ChatGPT",
    app_path=None,
    bundle_id=None,
    backend="chatgpt_chrome_app_visual",
    profile=CHATGPT_CHROME_APP_PROFILE,
    require_backend_preflight=False,
    window_hint="ChatGPT",
    paste_instruction="Paste into the ChatGPT Chrome app composer, then review manually. Do not submit automatically.",
    focus_strategy="visual_plus_anchor",
    visual_asset_profile=CHATGPT_CHROME_APP_PROFILE,
    idle_empty_timeout_seconds=600,
    idle_empty_poll_interval_seconds=10,
    click_backend="pyautogui",
    visual_anchor_click_backend="pyautogui",
    paste_backend="menu_paste_accessibility",
    paste_backends=("menu_paste_accessibility", "system_events_key_code_v_command"),
    plus_anchor_x_offset=40,
    plus_anchor_y_offset=0,
    visual_plus_templates=(
        "assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_plus_button_light.png",
        "assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_plus_button_dark.png",
    ),
    visual_send_disabled_templates=(
        "assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_voice_button_light.png",
        "assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_voice_button_dark.png",
    ),
    visual_send_templates=(
        "assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_send_button_light.png",
        "assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_send_button_dark.png",
    ),
    visual_stop_templates=(
        "assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_stop_button_light.png",
        "assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_stop_button_dark.png",
    ),
    visual_plus_confidence_threshold=0.75,
    visual_state_confidence_threshold=0.85,
    visual_response_copy_confidence_threshold=0.85,
    visual_scroll_down_confidence_threshold=0.85,
    visual_state_ambiguity_margin=0.03,
    visual_scale_min=0.75,
    visual_scale_max=1.35,
    visual_scale_step=0.05,
    visual_appearance_score_threshold=90.0,
)
LOCAL_AGENT_TARGET = ManualStageTarget(
    app_name="Codex",
    app_path=None,
    bundle_id=None,
    backend=None,
    require_backend_preflight=False,
    window_hint="Agent Bridge",
    paste_instruction="Paste into Codex input, then review manually. Do not submit automatically.",
    focus_strategy="direct_plus_anchor",
    visual_asset_profile="codex",
    input_focus_strategy=None,
    click_backend="pyautogui",
    visual_anchor_click_backend="pyautogui",
    paste_backend="pyautogui",
    input_click_x_ratio=None,
    input_click_y_ratio=None,
    require_prompt_presence_verification=True,
    allow_unverified_submit=False,
    allow_unverified_submit_for_noop_dogfood=True,
    composer_placeholder_text="후속 변경 사항을 부탁하세요",
    idle_empty_wait_timeout_seconds=600,
    idle_empty_poll_interval_seconds=10,
    dedicated_automation_session=True,
    allow_overwrite_after_idle_timeout=True,
    stop_on_idle_timeout=False,
    plus_anchor_enabled=True,
    plus_anchor_x_offset=0,
    plus_anchor_y_offset=50,
    direct_plus_anchor_enabled=True,
    direct_plus_anchor_x_offset=0,
    direct_plus_anchor_y_offset=50,
    direct_plus_anchor_y_offset_candidates=(50,),
    composer_policy_mode="dedicated_automation_session",
    busy_placeholder_wait_timeout_seconds=600,
    busy_placeholder_poll_interval_seconds=10,
    on_busy_timeout="overwrite",
    visual_text_recognition_enabled=True,
    visual_text_recognition_ocr_backend="pytesseract",
    visual_text_recognition_marker_text="AGENT_BRIDGE_CODEX_PASTE_TEST_DO_NOT_SUBMIT",
    visual_text_recognition_placeholder_text="후속 변경 사항을 부탁하세요",
    visual_text_recognition_search_region="lower_composer_band",
    visual_plus_templates=(
        "assets/gui/codex/codex_plus_button_light.png",
        "assets/gui/codex/codex_plus_button_dark.png",
    ),
    visual_send_disabled_templates=(
        "assets/gui/codex/codex_send_disabled_button_light.png",
        "assets/gui/codex/codex_send_disabled_button_dark.png",
    ),
    visual_send_templates=(
        "assets/gui/codex/codex_send_button_light.png",
        "assets/gui/codex/codex_send_button_dark.png",
    ),
    visual_stop_templates=(
        "assets/gui/codex/codex_stop_button_light.png",
        "assets/gui/codex/codex_stop_button_dark.png",
    ),
    visual_plus_confidence_threshold=0.80,
    visual_plus_multiscale_enabled=True,
    min_main_window_width=400,
    min_main_window_height=300,
    min_main_window_area=120000,
    window_selection_strategy="largest_visible_normal",
)


def default_gui_targets() -> GuiTargets:
    return GuiTargets(pm_assistant=PM_ASSISTANT_TARGET, local_agent=LOCAL_AGENT_TARGET)


def is_chatgpt_mac_visual_target(target: ManualStageTarget) -> bool:
    backend = (target.backend or "").strip().lower().replace("-", "_")
    profile = (target.visual_asset_profile or "").strip().lower().replace("-", "_")
    target_profile = (target.profile or "").strip().lower().replace("-", "_")
    return (
        backend == "chatgpt_mac_visual"
        or profile == CHATGPT_MAC_PROFILE
        or target_profile == CHATGPT_MAC_PROFILE
    )


def is_chatgpt_chrome_app_visual_target(target: ManualStageTarget) -> bool:
    backend = (target.backend or "").strip().lower().replace("-", "_")
    profile = (target.visual_asset_profile or "").strip().lower().replace("-", "_")
    target_profile = (target.profile or "").strip().lower().replace("-", "_")
    return (
        backend == "chatgpt_chrome_app_visual"
        or profile == CHATGPT_CHROME_APP_PROFILE
        or target_profile == CHATGPT_CHROME_APP_PROFILE
    )


def normalize_pm_target_profile(profile: str | None) -> str:
    normalized = (profile or CHATGPT_MAC_PROFILE).strip().lower().replace("-", "_")
    if normalized not in SUPPORTED_PM_TARGET_PROFILES:
        raise ValueError(
            "Unsupported PM target profile: "
            f"{profile}. Expected one of: {', '.join(SUPPORTED_PM_TARGET_PROFILES)}"
        )
    return normalized


def pm_target_for_profile(target: ManualStageTarget, profile: str | None) -> ManualStageTarget:
    normalized = normalize_pm_target_profile(profile)
    if normalized == CHATGPT_MAC_PROFILE:
        return ensure_native_chatgpt_mac_target(target)
    if is_chatgpt_chrome_app_visual_target(target):
        return ensure_chatgpt_chrome_app_target(target)
    return CHATGPT_CHROME_APP_TARGET


def ensure_native_chatgpt_mac_target(target: ManualStageTarget) -> ManualStageTarget:
    if not is_chatgpt_mac_visual_target(target):
        return target
    return replace_manual_stage_target(
        target,
        app_name="ChatGPT",
        app_path=target.app_path or NATIVE_CHATGPT_MAC_APP_PATH,
        bundle_id=NATIVE_CHATGPT_MAC_BUNDLE_ID,
        backend="chatgpt_mac_visual",
        profile=CHATGPT_MAC_PROFILE,
        visual_asset_profile=CHATGPT_MAC_PROFILE,
        visual_plus_templates=_profile_templates(
            target.visual_plus_templates,
            PM_ASSISTANT_TARGET.visual_plus_templates,
            "assets/gui/chatgpt_mac/",
        ),
        visual_send_disabled_templates=_profile_templates(
            target.visual_send_disabled_templates,
            PM_ASSISTANT_TARGET.visual_send_disabled_templates,
            "assets/gui/chatgpt_mac/",
        ),
        visual_send_templates=_profile_templates(
            target.visual_send_templates,
            PM_ASSISTANT_TARGET.visual_send_templates,
            "assets/gui/chatgpt_mac/",
        ),
        visual_stop_templates=_profile_templates(
            target.visual_stop_templates,
            PM_ASSISTANT_TARGET.visual_stop_templates,
            "assets/gui/chatgpt_mac/",
        ),
    )


def ensure_chatgpt_chrome_app_target(target: ManualStageTarget) -> ManualStageTarget:
    if not is_chatgpt_chrome_app_visual_target(target):
        return target
    return replace_manual_stage_target(
        target,
        backend="chatgpt_chrome_app_visual",
        profile=CHATGPT_CHROME_APP_PROFILE,
        visual_asset_profile=CHATGPT_CHROME_APP_PROFILE,
        app_path=target.app_path,
        bundle_id=target.bundle_id,
        visual_plus_templates=_profile_templates(
            target.visual_plus_templates,
            CHATGPT_CHROME_APP_TARGET.visual_plus_templates,
            "assets/gui/chatgpt_chrome_app/",
        ),
        visual_send_disabled_templates=_profile_templates(
            target.visual_send_disabled_templates,
            CHATGPT_CHROME_APP_TARGET.visual_send_disabled_templates,
            "assets/gui/chatgpt_chrome_app/",
        ),
        visual_send_templates=_profile_templates(
            target.visual_send_templates,
            CHATGPT_CHROME_APP_TARGET.visual_send_templates,
            "assets/gui/chatgpt_chrome_app/",
        ),
        visual_stop_templates=_profile_templates(
            target.visual_stop_templates,
            CHATGPT_CHROME_APP_TARGET.visual_stop_templates,
            "assets/gui/chatgpt_chrome_app/",
        ),
    )


def _profile_templates(
    current: tuple[str, ...],
    fallback: tuple[str, ...],
    expected_path_fragment: str,
) -> tuple[str, ...]:
    if current and all(expected_path_fragment in path for path in current):
        return current
    return fallback


def is_rejected_chatgpt_candidate_bundle(bundle_id: str | None) -> bool:
    if not bundle_id:
        return False
    return any(bundle_id.startswith(prefix) for prefix in CHROME_BUNDLE_ID_PREFIXES)


def is_chatgpt_chrome_app_candidate_bundle(bundle_id: str | None) -> bool:
    if not bundle_id:
        return False
    return any(bundle_id.startswith(prefix) for prefix in CHROME_APP_BUNDLE_ID_PREFIXES)


def replace_manual_stage_target(
    target: ManualStageTarget,
    **updates: Any,
) -> ManualStageTarget:
    data = target.__dict__.copy()
    data.update(updates)
    return ManualStageTarget(**data)


def _verified_display_name_activation_script(app_name: str, bundle_id: str) -> str:
    return f"""
tell application "{app_name}" to activate
delay 0.2
tell application "System Events"
  set frontProcess to first application process whose frontmost is true
  set frontBundle to bundle identifier of frontProcess
  if frontBundle is "{bundle_id}" then
    return frontBundle
  end if
  error "Display-name activation resolved to " & frontBundle & " instead of {bundle_id}"
end tell
""".strip()


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in config file: {path}")
    return data


def _target_from_config(data: dict[str, Any], fallback: ManualStageTarget) -> ManualStageTarget:
    composer_policy = data.get("composer_policy") if isinstance(data.get("composer_policy"), dict) else {}
    visual_text = (
        data.get("visual_text_recognition")
        if isinstance(data.get("visual_text_recognition"), dict)
        else {}
    )

    def _string_tuple(key: str, fallback_value: tuple[str, ...]) -> tuple[str, ...]:
        raw_values = data.get(key)
        if raw_values is None:
            return fallback_value
        if isinstance(raw_values, list):
            return tuple(str(value) for value in raw_values)
        if isinstance(raw_values, tuple):
            return tuple(str(value) for value in raw_values)
        return (str(raw_values),)

    plus_templates = _string_tuple("visual_plus_templates", fallback.visual_plus_templates)
    send_disabled_templates = _string_tuple(
        "visual_send_disabled_templates",
        fallback.visual_send_disabled_templates,
    )
    send_templates = _string_tuple("visual_send_templates", fallback.visual_send_templates)
    stop_templates = _string_tuple("visual_stop_templates", fallback.visual_stop_templates)
    paste_backends_fallback = (
        ()
        if data.get("paste_backend") is not None and data.get("paste_backends") is None
        else fallback.paste_backends
    )
    paste_backends = _string_tuple("paste_backends", paste_backends_fallback)
    raw_focus_candidates = data.get("owner_reviewed_focus_candidates")
    if raw_focus_candidates is None:
        owner_focus_candidates = fallback.owner_reviewed_focus_candidates
    elif isinstance(raw_focus_candidates, list):
        owner_focus_candidates = tuple(
            dict(value) for value in raw_focus_candidates if isinstance(value, dict)
        )
    else:
        owner_focus_candidates = ()
    return ManualStageTarget(
        app_name=str(data.get("app_name") or fallback.app_name),
        app_path=data.get("app_path") if data.get("app_path") is not None else fallback.app_path,
        bundle_id=data.get("bundle_id") if data.get("bundle_id") is not None else fallback.bundle_id,
        backend=data.get("backend") if data.get("backend") is not None else fallback.backend,
        profile=(
            str(data.get("profile"))
            if data.get("profile") is not None
            else fallback.profile
        ),
        require_backend_preflight=bool(
            data.get("require_backend_preflight")
            if data.get("require_backend_preflight") is not None
            else fallback.require_backend_preflight
        ),
        window_hint=data.get("window_hint") if data.get("window_hint") is not None else fallback.window_hint,
        paste_instruction=(
            data.get("paste_instruction")
            if data.get("paste_instruction") is not None
            else fallback.paste_instruction
        ),
        focus_strategy=(
            data.get("focus_strategy")
            if data.get("focus_strategy") is not None
            else fallback.focus_strategy
        ),
        visual_asset_profile=(
            str(data.get("visual_asset_profile"))
            if data.get("visual_asset_profile") is not None
            else fallback.visual_asset_profile
        ),
        response_copy_css_selector=(
            data.get("response_copy_css_selector")
            if data.get("response_copy_css_selector") is not None
            else fallback.response_copy_css_selector
        ),
        response_copy_xpath=(
            data.get("response_copy_xpath")
            if data.get("response_copy_xpath") is not None
            else fallback.response_copy_xpath
        ),
        response_copy_full_xpath=(
            data.get("response_copy_full_xpath")
            if data.get("response_copy_full_xpath") is not None
            else fallback.response_copy_full_xpath
        ),
        response_copy_strategy=(
            data.get("response_copy_strategy")
            if data.get("response_copy_strategy") is not None
            else fallback.response_copy_strategy
        ),
        idle_empty_timeout_seconds=(
            int(data.get("idle_empty_timeout_seconds"))
            if data.get("idle_empty_timeout_seconds") is not None
            else fallback.idle_empty_timeout_seconds
        ),
        input_focus_strategy=(
            data.get("input_focus_strategy")
            if data.get("input_focus_strategy") is not None
            else fallback.input_focus_strategy
        ),
        click_backend=(
            str(data.get("click_backend"))
            if data.get("click_backend") is not None
            else fallback.click_backend
        ),
        visual_anchor_click_backend=(
            str(data.get("visual_anchor_click_backend"))
            if data.get("visual_anchor_click_backend") is not None
            else fallback.visual_anchor_click_backend
        ),
        paste_backend=(
            str(data.get("paste_backend"))
            if data.get("paste_backend") is not None
            else fallback.paste_backend
        ),
        paste_backends=paste_backends,
        input_click_x_ratio=(
            float(data.get("input_click_x_ratio"))
            if data.get("input_click_x_ratio") is not None
            else fallback.input_click_x_ratio
        ),
        input_click_y_ratio=(
            float(data.get("input_click_y_ratio"))
            if data.get("input_click_y_ratio") is not None
            else fallback.input_click_y_ratio
        ),
        require_prompt_presence_verification=bool(
            data.get("require_prompt_presence_verification")
            if data.get("require_prompt_presence_verification") is not None
            else fallback.require_prompt_presence_verification
        ),
        allow_unverified_submit=bool(
            data.get("allow_unverified_submit")
            if data.get("allow_unverified_submit") is not None
            else fallback.allow_unverified_submit
        ),
        allow_unverified_submit_for_noop_dogfood=bool(
            data.get("allow_unverified_submit_for_noop_dogfood")
            if data.get("allow_unverified_submit_for_noop_dogfood") is not None
            else fallback.allow_unverified_submit_for_noop_dogfood
        ),
        composer_placeholder_text=(
            data.get("composer_placeholder_text")
            if data.get("composer_placeholder_text") is not None
            else fallback.composer_placeholder_text
        ),
        idle_empty_wait_timeout_seconds=(
            int(data.get("idle_empty_wait_timeout_seconds"))
            if data.get("idle_empty_wait_timeout_seconds") is not None
            else fallback.idle_empty_wait_timeout_seconds
        ),
        idle_empty_poll_interval_seconds=(
            int(data.get("idle_empty_poll_interval_seconds"))
            if data.get("idle_empty_poll_interval_seconds") is not None
            else fallback.idle_empty_poll_interval_seconds
        ),
        dedicated_automation_session=bool(
            data.get("dedicated_automation_session")
            if data.get("dedicated_automation_session") is not None
            else fallback.dedicated_automation_session
        ),
        allow_overwrite_after_idle_timeout=bool(
            data.get("allow_overwrite_after_idle_timeout")
            if data.get("allow_overwrite_after_idle_timeout") is not None
            else fallback.allow_overwrite_after_idle_timeout
        ),
        stop_on_idle_timeout=bool(
            data.get("stop_on_idle_timeout")
            if data.get("stop_on_idle_timeout") is not None
            else fallback.stop_on_idle_timeout
        ),
        plus_anchor_enabled=bool(
            data.get("plus_anchor_enabled")
            if data.get("plus_anchor_enabled") is not None
            else fallback.plus_anchor_enabled
        ),
        plus_anchor_x_offset=(
            int(data.get("plus_anchor_x_offset"))
            if data.get("plus_anchor_x_offset") is not None
            else fallback.plus_anchor_x_offset
        ),
        plus_anchor_y_offset=(
            int(data.get("plus_anchor_y_offset"))
            if data.get("plus_anchor_y_offset") is not None
            else fallback.plus_anchor_y_offset
        ),
        direct_plus_anchor_enabled=bool(
            data.get("direct_plus_anchor_enabled")
            if data.get("direct_plus_anchor_enabled") is not None
            else fallback.direct_plus_anchor_enabled
        ),
        direct_plus_anchor_x_offset=(
            int(data.get("direct_plus_anchor_x_offset"))
            if data.get("direct_plus_anchor_x_offset") is not None
            else fallback.direct_plus_anchor_x_offset
        ),
        direct_plus_anchor_y_offset=(
            int(data.get("direct_plus_anchor_y_offset"))
            if data.get("direct_plus_anchor_y_offset") is not None
            else fallback.direct_plus_anchor_y_offset
        ),
        direct_plus_anchor_y_offset_candidates=(
            tuple(int(value) for value in data.get("direct_plus_anchor_y_offset_candidates"))
            if isinstance(data.get("direct_plus_anchor_y_offset_candidates"), list)
            else fallback.direct_plus_anchor_y_offset_candidates
        ),
        composer_policy_mode=(
            str(composer_policy.get("mode"))
            if composer_policy.get("mode") is not None
            else fallback.composer_policy_mode
        ),
        busy_placeholder_wait_timeout_seconds=(
            int(composer_policy.get("busy_placeholder_wait_timeout_seconds"))
            if composer_policy.get("busy_placeholder_wait_timeout_seconds") is not None
            else fallback.busy_placeholder_wait_timeout_seconds
        ),
        busy_placeholder_poll_interval_seconds=(
            int(composer_policy.get("busy_placeholder_poll_interval_seconds"))
            if composer_policy.get("busy_placeholder_poll_interval_seconds") is not None
            else fallback.busy_placeholder_poll_interval_seconds
        ),
        on_busy_timeout=(
            str(composer_policy.get("on_busy_timeout"))
            if composer_policy.get("on_busy_timeout") is not None
            else fallback.on_busy_timeout
        ),
        visual_text_recognition_enabled=bool(
            visual_text.get("enabled")
            if visual_text.get("enabled") is not None
            else fallback.visual_text_recognition_enabled
        ),
        visual_text_recognition_ocr_backend=(
            str(visual_text.get("ocr_backend"))
            if visual_text.get("ocr_backend") is not None
            else fallback.visual_text_recognition_ocr_backend
        ),
        visual_text_recognition_marker_text=(
            str(visual_text.get("marker_text"))
            if visual_text.get("marker_text") is not None
            else fallback.visual_text_recognition_marker_text
        ),
        visual_text_recognition_placeholder_text=(
            str(visual_text.get("placeholder_text"))
            if visual_text.get("placeholder_text") is not None
            else fallback.visual_text_recognition_placeholder_text
        ),
        visual_text_recognition_search_region=(
            str(visual_text.get("search_region"))
            if visual_text.get("search_region") is not None
            else fallback.visual_text_recognition_search_region
        ),
        visual_plus_templates=plus_templates,
        visual_send_disabled_templates=send_disabled_templates,
        visual_send_templates=send_templates,
        visual_stop_templates=stop_templates,
        visual_plus_confidence_threshold=(
            float(data.get("visual_plus_confidence_threshold"))
            if data.get("visual_plus_confidence_threshold") is not None
            else fallback.visual_plus_confidence_threshold
        ),
        visual_state_confidence_threshold=(
            float(data.get("visual_state_confidence_threshold"))
            if data.get("visual_state_confidence_threshold") is not None
            else fallback.visual_state_confidence_threshold
        ),
        visual_response_copy_confidence_threshold=(
            float(data.get("visual_response_copy_confidence_threshold"))
            if data.get("visual_response_copy_confidence_threshold") is not None
            else fallback.visual_response_copy_confidence_threshold
        ),
        visual_scroll_down_confidence_threshold=(
            float(data.get("visual_scroll_down_confidence_threshold"))
            if data.get("visual_scroll_down_confidence_threshold") is not None
            else fallback.visual_scroll_down_confidence_threshold
        ),
        visual_state_ambiguity_margin=(
            float(data.get("visual_state_ambiguity_margin"))
            if data.get("visual_state_ambiguity_margin") is not None
            else fallback.visual_state_ambiguity_margin
        ),
        visual_state_search_region=(
            str(data.get("visual_state_search_region"))
            if data.get("visual_state_search_region") is not None
            else fallback.visual_state_search_region
        ),
        visual_plus_multiscale_enabled=bool(
            data.get("visual_plus_multiscale_enabled")
            if data.get("visual_plus_multiscale_enabled") is not None
            else fallback.visual_plus_multiscale_enabled
        ),
        visual_scale_min=(
            float(data.get("visual_scale_min"))
            if data.get("visual_scale_min") is not None
            else fallback.visual_scale_min
        ),
        visual_scale_max=(
            float(data.get("visual_scale_max"))
            if data.get("visual_scale_max") is not None
            else fallback.visual_scale_max
        ),
        visual_scale_step=(
            float(data.get("visual_scale_step"))
            if data.get("visual_scale_step") is not None
            else fallback.visual_scale_step
        ),
        visual_appearance_score_threshold=(
            float(data.get("visual_appearance_score_threshold"))
            if data.get("visual_appearance_score_threshold") is not None
            else fallback.visual_appearance_score_threshold
        ),
        max_state_machine_attempts=(
            int(data.get("max_state_machine_attempts"))
            if data.get("max_state_machine_attempts") is not None
            else fallback.max_state_machine_attempts
        ),
        state_machine_retry_delay_seconds=(
            float(data.get("state_machine_retry_delay_seconds"))
            if data.get("state_machine_retry_delay_seconds") is not None
            else fallback.state_machine_retry_delay_seconds
        ),
        max_action_attempts=(
            int(data.get("max_action_attempts"))
            if data.get("max_action_attempts") is not None
            else fallback.max_action_attempts
        ),
        action_retry_delay_seconds=(
            float(data.get("action_retry_delay_seconds"))
            if data.get("action_retry_delay_seconds") is not None
            else fallback.action_retry_delay_seconds
        ),
        submit_after_paste_max_attempts=(
            int(data.get("submit_after_paste_max_attempts"))
            if data.get("submit_after_paste_max_attempts") is not None
            else fallback.submit_after_paste_max_attempts
        ),
        owner_reviewed_focus_candidates=owner_focus_candidates,
        min_main_window_width=(
            int(data.get("min_main_window_width"))
            if data.get("min_main_window_width") is not None
            else fallback.min_main_window_width
        ),
        min_main_window_height=(
            int(data.get("min_main_window_height"))
            if data.get("min_main_window_height") is not None
            else fallback.min_main_window_height
        ),
        min_main_window_area=(
            int(data.get("min_main_window_area"))
            if data.get("min_main_window_area") is not None
            else fallback.min_main_window_area
        ),
        window_selection_strategy=(
            str(data.get("window_selection_strategy"))
            if data.get("window_selection_strategy") is not None
            else fallback.window_selection_strategy
        ),
    )


def _merge_app_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return merged


def load_gui_targets(config_dir: Path) -> GuiTargets:
    defaults = default_gui_targets()
    default_config = _load_yaml(config_dir / "default.yaml")
    local_config = _load_yaml(config_dir / "local.yaml")
    apps = _merge_app_config(
        (default_config.get("apps") or {}) if isinstance(default_config.get("apps") or {}, dict) else {},
        (local_config.get("apps") or {}) if isinstance(local_config.get("apps") or {}, dict) else {},
    )
    gui_retry = _merge_app_config(
        (default_config.get("gui") or {}) if isinstance(default_config.get("gui") or {}, dict) else {},
        (local_config.get("gui") or {}) if isinstance(local_config.get("gui") or {}, dict) else {},
    )
    pm_config = apps.get("pm_assistant") if isinstance(apps.get("pm_assistant"), dict) else {}
    local_config_data = apps.get("local_agent") if isinstance(apps.get("local_agent"), dict) else {}
    return GuiTargets(
        pm_assistant=_target_from_config(
            _merge_app_config(gui_retry, pm_config),
            defaults.pm_assistant,
        ),
        local_agent=_target_from_config(
            _merge_app_config(gui_retry, local_config_data),
            defaults.local_agent,
        ),
    )


def format_target_guidance(label: str, target: ManualStageTarget) -> str:
    lines = [
        f"{label} target:",
        f"  App: {target.app_name}",
        f"  App path: {target.app_path or 'unspecified'}",
        f"  Bundle id: {target.bundle_id or 'unspecified'}",
        f"  Backend: {target.backend or 'unspecified'}",
        f"  Profile: {target.profile or 'unspecified'}",
        f"  Require backend preflight: {'yes' if target.require_backend_preflight else 'no'}",
            f"  Window hint: {target.window_hint or 'unspecified'}",
            f"  Paste instruction: {target.paste_instruction or 'Paste manually, then review before submitting.'}",
            f"  Focus strategy: {target.focus_strategy or 'default'}",
            f"  Visual asset profile: {target.visual_asset_profile or 'auto'}",
    ]
    if target.idle_empty_timeout_seconds is not None:
        lines.append(f"  Idle-empty timeout: {target.idle_empty_timeout_seconds}s")
    lines.extend(
        [
            f"  Input focus strategy: {target.input_focus_strategy or 'accessibility_only'}",
            f"  Click backend: {target.click_backend}",
            f"  Visual anchor click backend: {target.visual_anchor_click_backend}",
            f"  Paste backend: {target.paste_backend}",
            f"  Paste backend chain: {', '.join(target.paste_backends) or 'derived from paste backend'}",
            (
                "  Input click ratio: "
                + (
                    f"{target.input_click_x_ratio:.2f}, {target.input_click_y_ratio:.2f}"
                    if target.input_click_x_ratio is not None
                    and target.input_click_y_ratio is not None
                    else "unspecified"
                )
            ),
            (
                "  Require prompt presence verification: "
                + ("yes" if target.require_prompt_presence_verification else "no")
            ),
            f"  Allow unverified submit: {'yes' if target.allow_unverified_submit else 'no'}",
            (
                "  Allow no-op dogfood unverified submit: "
                + ("yes" if target.allow_unverified_submit_for_noop_dogfood else "no")
            ),
            f"  Composer placeholder text: {target.composer_placeholder_text or 'unspecified'}",
            f"  Local idle-empty wait timeout: {target.idle_empty_wait_timeout_seconds}s",
            f"  Local idle-empty poll interval: {target.idle_empty_poll_interval_seconds}s",
            f"  Dedicated automation session: {'yes' if target.dedicated_automation_session else 'no'}",
            (
                "  Allow overwrite after idle timeout: "
                + ("yes" if target.allow_overwrite_after_idle_timeout else "no")
            ),
            f"  Stop on idle timeout: {'yes' if target.stop_on_idle_timeout else 'no'}",
            f"  Plus anchor enabled: {'yes' if target.plus_anchor_enabled else 'no'}",
            (
                "  Plus anchor offset: "
                f"{target.plus_anchor_x_offset}, -{target.plus_anchor_y_offset}"
            ),
            (
                "  Direct plus-anchor: "
                + ("enabled" if target.direct_plus_anchor_enabled else "disabled")
                + f", offset {target.direct_plus_anchor_x_offset}, -{target.direct_plus_anchor_y_offset}"
            ),
            f"  Composer policy mode: {target.composer_policy_mode}",
            (
                "  Busy placeholder wait timeout: "
                f"{target.busy_placeholder_wait_timeout_seconds}s"
            ),
            (
                "  Busy placeholder poll interval: "
                f"{target.busy_placeholder_poll_interval_seconds}s"
            ),
            f"  On busy timeout: {target.on_busy_timeout}",
            (
                "  Visual text recognition enabled: "
                + ("yes" if target.visual_text_recognition_enabled else "no")
            ),
            f"  Visual text OCR backend: {target.visual_text_recognition_ocr_backend}",
            f"  Visual text search region: {target.visual_text_recognition_search_region}",
            f"  Visual plus templates: {', '.join(target.visual_plus_templates)}",
            (
                "  Visual send-disabled templates: "
                f"{', '.join(target.visual_send_disabled_templates)}"
            ),
            f"  Visual send templates: {', '.join(target.visual_send_templates)}",
            f"  Visual stop templates: {', '.join(target.visual_stop_templates)}",
            f"  Visual plus threshold: {target.visual_plus_confidence_threshold:.2f}",
            f"  Visual state threshold: {target.visual_state_confidence_threshold:.2f}",
            (
                "  Visual response-copy threshold: "
                f"{target.visual_response_copy_confidence_threshold if target.visual_response_copy_confidence_threshold is not None else 'state default'}"
            ),
            (
                "  Visual scroll-down threshold: "
                f"{target.visual_scroll_down_confidence_threshold if target.visual_scroll_down_confidence_threshold is not None else 'state default'}"
            ),
            f"  Max state-machine attempts: {target.max_state_machine_attempts}",
            (
                "  State-machine retry delay: "
                f"{target.state_machine_retry_delay_seconds:.2f}s"
            ),
            f"  Max GUI action attempts: {target.max_action_attempts}",
            f"  GUI action retry delay: {target.action_retry_delay_seconds:.2f}s",
            f"  Submit-after-paste max attempts: {target.submit_after_paste_max_attempts}",
            f"  Visual state ambiguity margin: {target.visual_state_ambiguity_margin:.2f}",
            f"  Visual state search region: {target.visual_state_search_region}",
            (
                "  Visual plus multiscale: "
                + ("yes" if target.visual_plus_multiscale_enabled else "no")
            ),
            (
                "  Visual scale range: "
                f"{target.visual_scale_min:.2f}-{target.visual_scale_max:.2f} "
                f"step {target.visual_scale_step:.2f}"
            ),
            (
                "  Visual appearance score threshold: "
                f"{target.visual_appearance_score_threshold if target.visual_appearance_score_threshold is not None else 'disabled'}"
            ),
            (
                "  Main window minimum: "
                f"{target.min_main_window_width}x{target.min_main_window_height}, "
                f"area {target.min_main_window_area}"
            ),
            f"  Window selection strategy: {target.window_selection_strategy}",
        ]
    )
    return "\n".join(lines)


def format_activation_plan(label: str, target: ManualStageTarget) -> str:
    activator = MacOSAppActivator()
    lines = [f"{label} activation plan:"]
    for strategy, command in activator.activation_plan(
        target.app_name,
        app_path=target.app_path,
        bundle_id=target.bundle_id,
    ):
        lines.append(f"  - {strategy}: {' '.join(command)}")
    return "\n".join(lines)


def format_activation_result(result: ActivationResult) -> str:
    lines = [
        f"Activation {'succeeded' if result.succeeded else 'failed'} for app: {result.app_name}",
    ]
    if result.winning_strategy:
        lines.append(f"Winning strategy: {result.winning_strategy}")
    for attempt in result.attempts:
        status = "ok" if attempt.succeeded else "failed"
        lines.append(f"- {attempt.strategy}: {status}")
        lines.append(f"  command: {' '.join(attempt.command)}")
        if attempt.output:
            lines.append(f"  output: {attempt.output}")
    return "\n".join(lines)


def discover_gui_apps(search_roots: list[Path] | None = None) -> list[Path]:
    roots = search_roots or [Path("/Applications"), Path.home() / "Applications"]
    apps: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        apps.extend(sorted(root.glob("*.app")))
        apps.extend(sorted(root.glob("*/*.app")))
    return apps


def automatic_submit_supported() -> bool:
    return False
