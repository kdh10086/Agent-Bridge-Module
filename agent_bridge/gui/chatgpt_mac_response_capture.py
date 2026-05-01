from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agent_bridge.gui.clipboard import Clipboard
from agent_bridge.gui.macos_apps import ManualStageTarget
from agent_bridge.gui.visual_detector import VisualRect, safe_search_region
from agent_bridge.gui.visual_thresholds import (
    effective_visual_threshold,
    visual_threshold_cap_applied,
)


CHATGPT_MAC_COPY_RESPONSE_TEMPLATES = (
    "assets/gui/chatgpt_mac/chatgpt_mac_copy_response_button_light.png",
    "assets/gui/chatgpt_mac/chatgpt_mac_copy_response_button_dark.png",
)
CHATGPT_MAC_SCROLL_DOWN_TEMPLATES = (
    "assets/gui/chatgpt_mac/chatgpt_mac_scroll_down_button_light.png",
    "assets/gui/chatgpt_mac/chatgpt_mac_scroll_down_button_dark.png",
)
CHATGPT_CHROME_APP_COPY_RESPONSE_TEMPLATES = (
    "assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_copy_response_button_light.png",
    "assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_copy_response_button_dark.png",
)
CHATGPT_CHROME_APP_SCROLL_DOWN_TEMPLATES = (
    "assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_scroll_down_button_light.png",
    "assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_scroll_down_button_dark.png",
)


@dataclass(frozen=True)
class ChatGPTMacResponseCaptureResult:
    target_app: str
    asset_profile: str
    window_bounds: tuple[int, int, int, int] | None
    search_region_bounds: tuple[int, int, int, int] | None
    screenshot_captured: bool
    backend_available: bool
    supported: bool
    copy_assets: tuple[str, ...]
    missing_copy_assets: tuple[str, ...]
    scroll_assets: tuple[str, ...] = ()
    missing_scroll_assets: tuple[str, ...] = ()
    copy_button_found: bool = False
    copy_button_bbox: tuple[int, int, int, int] | None = None
    copy_button_click_point: tuple[int, int] | None = None
    copy_button_click_point_safe: bool = False
    copy_button_confidence: float | None = None
    copy_button_original_template_size: tuple[int, int] | None = None
    copy_button_scaled_template_size: tuple[int, int] | None = None
    copy_button_selected_scale: float | None = None
    copy_button_appearance_score: float | None = None
    matched_asset_path: str | None = None
    copy_detection_attempt_count: int = 0
    scroll_button_found: bool = False
    scroll_button_bbox: tuple[int, int, int, int] | None = None
    scroll_button_click_point: tuple[int, int] | None = None
    scroll_button_click_point_safe: bool = False
    scroll_button_confidence: float | None = None
    scroll_button_original_template_size: tuple[int, int] | None = None
    scroll_button_scaled_template_size: tuple[int, int] | None = None
    scroll_button_selected_scale: float | None = None
    scroll_button_appearance_score: float | None = None
    matched_scroll_asset_path: str | None = None
    scroll_attempted: bool = False
    scroll_succeeded: bool = False
    recaptured_after_scroll: bool = False
    confidence_threshold: float | None = None
    configured_confidence_threshold: float | None = None
    effective_confidence_threshold: float | None = None
    threshold_cap_applied: bool = False
    capture_attempted: bool = False
    response_captured: bool = False
    response_length: int = 0
    expected_marker_found: bool | None = None
    screenshot_path: str | None = None
    post_scroll_screenshot_path: str | None = None
    annotated_screenshot_path: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class _TemplateButtonMatch:
    bbox: tuple[int, int, int, int] | None
    confidence: float | None
    asset_path: str | None
    original_template_size: tuple[int, int] | None = None
    scaled_template_size: tuple[int, int] | None = None
    selected_scale: float | None = None
    appearance_score: float | None = None
    accepted: bool = False
    rejection_reason: str | None = None
    configured_threshold: float | None = None
    effective_threshold: float | None = None
    threshold_cap_applied: bool = False


def diagnose_chatgpt_mac_response_capture(
    *,
    target: ManualStageTarget,
    window_bounds: tuple[int, int, int, int] | None,
    logs_dir: Path | None = None,
    write_debug: bool = False,
    attempt_copy: bool = False,
    clipboard: Clipboard | None = None,
    expected_marker: str | None = None,
    screenshot_provider: Callable[[tuple[int, int, int, int]], Any] | None = None,
    clicker: Callable[[int, int], None] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> ChatGPTMacResponseCaptureResult:
    profile = target.visual_asset_profile or "chatgpt_mac"
    copy_assets = response_copy_templates_for_profile(profile)
    scroll_assets = scroll_down_templates_for_profile(profile)
    safe_profile = profile.strip().lower().replace("-", "_").replace("/", "_")
    if window_bounds is None:
        return ChatGPTMacResponseCaptureResult(
            target_app=target.app_name,
            asset_profile=profile,
            window_bounds=None,
            search_region_bounds=None,
            screenshot_captured=False,
            backend_available=False,
            supported=False,
            copy_assets=copy_assets,
            missing_copy_assets=_missing_assets(copy_assets),
            scroll_assets=scroll_assets,
            missing_scroll_assets=_missing_assets(scroll_assets),
            error=f"{target.app_name} window bounds were unavailable.",
        )

    search_region = safe_search_region(
        window_bounds,
        lower_height_ratio=0.9,
        min_x_ratio=0.04,
        max_x_ratio=0.96,
    )
    screenshot = _capture_screenshot(window_bounds, screenshot_provider=screenshot_provider)
    screenshot_path: str | None = None
    annotated_path: str | None = None
    if isinstance(screenshot, str):
        return ChatGPTMacResponseCaptureResult(
            target_app=target.app_name,
            asset_profile=profile,
            window_bounds=window_bounds,
            search_region_bounds=search_region.as_tuple(),
            screenshot_captured=False,
            backend_available=False,
            supported=False,
            copy_assets=copy_assets,
            missing_copy_assets=_missing_assets(copy_assets),
            scroll_assets=scroll_assets,
            missing_scroll_assets=_missing_assets(scroll_assets),
            error=screenshot,
        )
    if write_debug and logs_dir is not None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = str(logs_dir / f"{safe_profile}_response_capture.png")
        annotated_path = str(logs_dir / f"{safe_profile}_response_capture_annotated.png")
        _save_image_if_possible(screenshot, Path(screenshot_path))

    missing_assets = _missing_assets(copy_assets)
    missing_scroll_assets = _missing_assets(scroll_assets)
    if missing_assets:
        if write_debug and annotated_path is not None:
            _save_annotation_if_possible(
                screenshot,
                Path(annotated_path),
                search_region=search_region,
                window_bounds=window_bounds,
                copy_bbox=None,
                scroll_bbox=None,
            )
        return ChatGPTMacResponseCaptureResult(
            target_app=target.app_name,
            asset_profile=profile,
            window_bounds=window_bounds,
            search_region_bounds=search_region.as_tuple(),
            screenshot_captured=True,
            backend_available=True,
            supported=False,
            copy_assets=copy_assets,
            missing_copy_assets=missing_assets,
            scroll_assets=scroll_assets,
            missing_scroll_assets=missing_scroll_assets,
            screenshot_path=screenshot_path,
            annotated_screenshot_path=annotated_path,
            error=(
                f"{profile} response-copy assets are missing: "
                + ", ".join(missing_assets)
            ),
        )

    copy_detection_attempt_count = 1
    configured_copy_threshold = (
        target.visual_response_copy_confidence_threshold
        if target.visual_response_copy_confidence_threshold is not None
        else target.visual_state_confidence_threshold
    )
    copy_threshold = effective_visual_threshold(configured_copy_threshold)
    copy_cap_applied = visual_threshold_cap_applied(configured_copy_threshold)
    configured_scroll_threshold = (
        target.visual_scroll_down_confidence_threshold
        if target.visual_scroll_down_confidence_threshold is not None
        else target.visual_state_confidence_threshold
    )
    scroll_threshold = effective_visual_threshold(configured_scroll_threshold)
    match_result = _find_template_button(
        screenshot=screenshot,
        window_bounds=window_bounds,
        search_region=search_region,
        template_paths=copy_assets,
        threshold=copy_threshold,
        scales=_template_scales(target),
        appearance_score_threshold=target.visual_appearance_score_threshold,
        label="response-copy",
    )
    if isinstance(match_result, str):
        return ChatGPTMacResponseCaptureResult(
            target_app=target.app_name,
            asset_profile=profile,
            window_bounds=window_bounds,
            search_region_bounds=search_region.as_tuple(),
            screenshot_captured=True,
            backend_available=False,
            supported=False,
            copy_assets=copy_assets,
            missing_copy_assets=(),
            scroll_assets=scroll_assets,
            missing_scroll_assets=missing_scroll_assets,
            copy_detection_attempt_count=copy_detection_attempt_count,
            confidence_threshold=copy_threshold,
            configured_confidence_threshold=float(configured_copy_threshold),
            effective_confidence_threshold=copy_threshold,
            threshold_cap_applied=copy_cap_applied,
            screenshot_path=screenshot_path,
            annotated_screenshot_path=annotated_path,
            error=match_result,
        )
    copy_match = match_result
    bbox = copy_match.bbox if copy_match.accepted else None
    confidence = copy_match.confidence
    asset_path = copy_match.asset_path
    scroll_bbox: tuple[int, int, int, int] | None = None
    scroll_confidence: float | None = None
    scroll_asset_path: str | None = None
    scroll_click_point: tuple[int, int] | None = None
    scroll_click_safe = False
    scroll_attempted = False
    scroll_succeeded = False
    recaptured_after_scroll = False
    post_scroll_screenshot_path: str | None = None
    scroll_match: _TemplateButtonMatch | None = None

    if bbox is None:
        if missing_scroll_assets:
            if write_debug and annotated_path is not None:
                _save_annotation_if_possible(
                    screenshot,
                    Path(annotated_path),
                    search_region=search_region,
                    window_bounds=window_bounds,
                    copy_bbox=None,
                    scroll_bbox=None,
                )
            return ChatGPTMacResponseCaptureResult(
                target_app=target.app_name,
                asset_profile=profile,
                window_bounds=window_bounds,
                search_region_bounds=search_region.as_tuple(),
                screenshot_captured=True,
                backend_available=True,
                supported=False,
                copy_assets=copy_assets,
                missing_copy_assets=(),
                scroll_assets=scroll_assets,
                missing_scroll_assets=missing_scroll_assets,
                copy_detection_attempt_count=copy_detection_attempt_count,
                confidence_threshold=copy_threshold,
                screenshot_path=screenshot_path,
                annotated_screenshot_path=annotated_path,
                error=(
                    "response_copy_button_not_found; scroll-down assets are missing: "
                    + ", ".join(missing_scroll_assets)
                ),
            )
        scroll_match_result = _find_template_button(
            screenshot=screenshot,
            window_bounds=window_bounds,
            search_region=search_region,
            template_paths=scroll_assets,
            threshold=scroll_threshold,
            scales=_template_scales(target),
            appearance_score_threshold=target.visual_appearance_score_threshold,
            label="scroll-down",
        )
        if isinstance(scroll_match_result, str):
            return ChatGPTMacResponseCaptureResult(
                target_app=target.app_name,
                asset_profile=profile,
                window_bounds=window_bounds,
                search_region_bounds=search_region.as_tuple(),
                screenshot_captured=True,
                backend_available=False,
                supported=False,
                copy_assets=copy_assets,
                missing_copy_assets=(),
                scroll_assets=scroll_assets,
                missing_scroll_assets=missing_scroll_assets,
                copy_detection_attempt_count=copy_detection_attempt_count,
                confidence_threshold=scroll_threshold,
                screenshot_path=screenshot_path,
                annotated_screenshot_path=annotated_path,
                error=scroll_match_result,
            )
        scroll_match = scroll_match_result
        scroll_bbox = scroll_match.bbox if scroll_match.accepted else None
        scroll_confidence = scroll_match.confidence
        scroll_asset_path = scroll_match.asset_path
        if scroll_bbox is not None:
            scroll_click_point = VisualRect(*scroll_bbox).center
            scroll_click_safe = _point_inside_region(
                scroll_click_point,
                window_bounds=window_bounds,
                search_region=search_region,
            )
            if scroll_click_safe:
                scroll_attempted = True
                try:
                    if clicker is not None:
                        clicker(scroll_click_point[0], scroll_click_point[1])
                    else:
                        import pyautogui  # type: ignore[import-not-found]

                        pyautogui.click(scroll_click_point[0], scroll_click_point[1])
                    sleep_fn(0.5)
                    scroll_succeeded = True
                    recaptured = _capture_screenshot(
                        window_bounds,
                        screenshot_provider=screenshot_provider,
                    )
                    if isinstance(recaptured, str):
                        return ChatGPTMacResponseCaptureResult(
                            target_app=target.app_name,
                            asset_profile=profile,
                            window_bounds=window_bounds,
                            search_region_bounds=search_region.as_tuple(),
                            screenshot_captured=True,
                            backend_available=False,
                            supported=False,
                            copy_assets=copy_assets,
                            missing_copy_assets=(),
                            scroll_assets=scroll_assets,
                            missing_scroll_assets=missing_scroll_assets,
                            copy_detection_attempt_count=copy_detection_attempt_count,
                            scroll_button_found=True,
                            scroll_button_bbox=scroll_bbox,
                            scroll_button_click_point=scroll_click_point,
                            scroll_button_click_point_safe=scroll_click_safe,
                            scroll_button_confidence=scroll_confidence,
                            matched_scroll_asset_path=scroll_asset_path,
                            scroll_attempted=scroll_attempted,
                            scroll_succeeded=scroll_succeeded,
                            confidence_threshold=scroll_threshold,
                            screenshot_path=screenshot_path,
                            annotated_screenshot_path=annotated_path,
                            error=recaptured,
                        )
                    screenshot = recaptured
                    recaptured_after_scroll = True
                    if write_debug and logs_dir is not None:
                        post_scroll_screenshot_path = str(
                            logs_dir / "chatgpt_mac_response_capture_after_scroll.png"
                            if safe_profile == "chatgpt_mac"
                            else logs_dir / f"{safe_profile}_response_capture_after_scroll.png"
                        )
                        _save_image_if_possible(screenshot, Path(post_scroll_screenshot_path))
                    copy_detection_attempt_count += 1
                    retry_result = _find_template_button(
                        screenshot=screenshot,
                        window_bounds=window_bounds,
                        search_region=search_region,
                        template_paths=copy_assets,
                        threshold=copy_threshold,
                        scales=_template_scales(target),
                        appearance_score_threshold=target.visual_appearance_score_threshold,
                        label="response-copy",
                    )
                    if isinstance(retry_result, str):
                        return ChatGPTMacResponseCaptureResult(
                            target_app=target.app_name,
                            asset_profile=profile,
                            window_bounds=window_bounds,
                            search_region_bounds=search_region.as_tuple(),
                            screenshot_captured=True,
                            backend_available=False,
                            supported=False,
                            copy_assets=copy_assets,
                            missing_copy_assets=(),
                            scroll_assets=scroll_assets,
                            missing_scroll_assets=missing_scroll_assets,
                            copy_detection_attempt_count=copy_detection_attempt_count,
                            scroll_button_found=True,
                            scroll_button_bbox=scroll_bbox,
                            scroll_button_click_point=scroll_click_point,
                            scroll_button_click_point_safe=scroll_click_safe,
                            scroll_button_confidence=scroll_confidence,
                            matched_scroll_asset_path=scroll_asset_path,
                            scroll_attempted=scroll_attempted,
                            scroll_succeeded=scroll_succeeded,
                            recaptured_after_scroll=recaptured_after_scroll,
                            confidence_threshold=copy_threshold,
                            screenshot_path=screenshot_path,
                            post_scroll_screenshot_path=post_scroll_screenshot_path,
                            annotated_screenshot_path=annotated_path,
                            error=retry_result,
                        )
                    retry_match = retry_result
                    copy_match = retry_match
                    bbox = retry_match.bbox if retry_match.accepted else None
                    confidence = retry_match.confidence
                    asset_path = retry_match.asset_path
                except Exception as error:
                    return ChatGPTMacResponseCaptureResult(
                        target_app=target.app_name,
                        asset_profile=profile,
                        window_bounds=window_bounds,
                        search_region_bounds=search_region.as_tuple(),
                        screenshot_captured=True,
                        backend_available=True,
                        supported=False,
                        copy_assets=copy_assets,
                        missing_copy_assets=(),
                        scroll_assets=scroll_assets,
                        missing_scroll_assets=missing_scroll_assets,
                        copy_detection_attempt_count=copy_detection_attempt_count,
                        scroll_button_found=True,
                        scroll_button_bbox=scroll_bbox,
                        scroll_button_click_point=scroll_click_point,
                        scroll_button_click_point_safe=scroll_click_safe,
                        scroll_button_confidence=scroll_confidence,
                        matched_scroll_asset_path=scroll_asset_path,
                        scroll_attempted=scroll_attempted,
                        scroll_succeeded=False,
                        confidence_threshold=scroll_threshold,
                        screenshot_path=screenshot_path,
                        annotated_screenshot_path=annotated_path,
                        error=f"{profile} scroll-down click failed: {error}",
                    )
    if write_debug and annotated_path is not None:
        _save_annotation_if_possible(
            screenshot,
            Path(annotated_path),
            search_region=search_region,
            window_bounds=window_bounds,
            copy_bbox=bbox,
            scroll_bbox=scroll_bbox,
        )
    if bbox is None:
        return ChatGPTMacResponseCaptureResult(
            target_app=target.app_name,
            asset_profile=profile,
            window_bounds=window_bounds,
            search_region_bounds=search_region.as_tuple(),
            screenshot_captured=True,
            backend_available=True,
            supported=False,
            copy_assets=copy_assets,
            missing_copy_assets=(),
            scroll_assets=scroll_assets,
            missing_scroll_assets=missing_scroll_assets,
            copy_detection_attempt_count=copy_detection_attempt_count,
            copy_button_original_template_size=copy_match.original_template_size,
            copy_button_scaled_template_size=copy_match.scaled_template_size,
            copy_button_selected_scale=copy_match.selected_scale,
            copy_button_appearance_score=copy_match.appearance_score,
            scroll_button_found=scroll_bbox is not None,
            scroll_button_bbox=scroll_bbox,
            scroll_button_click_point=scroll_click_point,
            scroll_button_click_point_safe=scroll_click_safe,
            scroll_button_confidence=scroll_confidence,
            scroll_button_original_template_size=(
                scroll_match.original_template_size if scroll_match else None
            ),
            scroll_button_scaled_template_size=(
                scroll_match.scaled_template_size if scroll_match else None
            ),
            scroll_button_selected_scale=scroll_match.selected_scale if scroll_match else None,
            scroll_button_appearance_score=scroll_match.appearance_score if scroll_match else None,
            matched_scroll_asset_path=scroll_asset_path,
            scroll_attempted=scroll_attempted,
            scroll_succeeded=scroll_succeeded,
            recaptured_after_scroll=recaptured_after_scroll,
            confidence_threshold=copy_threshold,
            screenshot_path=screenshot_path,
            post_scroll_screenshot_path=post_scroll_screenshot_path,
            annotated_screenshot_path=annotated_path,
            error="response_copy_button_not_found",
        )
    click_point = VisualRect(*bbox).center
    click_safe = _point_inside_region(
        click_point,
        window_bounds=window_bounds,
        search_region=search_region,
    )
    if not attempt_copy:
        return ChatGPTMacResponseCaptureResult(
            target_app=target.app_name,
            asset_profile=profile,
            window_bounds=window_bounds,
            search_region_bounds=search_region.as_tuple(),
            screenshot_captured=True,
            backend_available=True,
            supported=True,
            copy_assets=copy_assets,
            missing_copy_assets=(),
            scroll_assets=scroll_assets,
            missing_scroll_assets=missing_scroll_assets,
            copy_button_found=True,
            copy_button_bbox=bbox,
            copy_button_click_point=click_point,
            copy_button_click_point_safe=click_safe,
            copy_button_confidence=confidence,
            copy_button_original_template_size=copy_match.original_template_size,
            copy_button_scaled_template_size=copy_match.scaled_template_size,
            copy_button_selected_scale=copy_match.selected_scale,
            copy_button_appearance_score=copy_match.appearance_score,
            matched_asset_path=asset_path,
            copy_detection_attempt_count=copy_detection_attempt_count,
            scroll_button_found=scroll_bbox is not None,
            scroll_button_bbox=scroll_bbox,
            scroll_button_click_point=scroll_click_point,
            scroll_button_click_point_safe=scroll_click_safe,
            scroll_button_confidence=scroll_confidence,
            scroll_button_original_template_size=(
                scroll_match.original_template_size if scroll_match else None
            ),
            scroll_button_scaled_template_size=(
                scroll_match.scaled_template_size if scroll_match else None
            ),
            scroll_button_selected_scale=scroll_match.selected_scale if scroll_match else None,
            scroll_button_appearance_score=scroll_match.appearance_score if scroll_match else None,
            matched_scroll_asset_path=scroll_asset_path,
            scroll_attempted=scroll_attempted,
            scroll_succeeded=scroll_succeeded,
            recaptured_after_scroll=recaptured_after_scroll,
            confidence_threshold=copy_threshold,
            screenshot_path=screenshot_path,
            post_scroll_screenshot_path=post_scroll_screenshot_path,
            annotated_screenshot_path=annotated_path,
        )
    if clipboard is None:
        return ChatGPTMacResponseCaptureResult(
            target_app=target.app_name,
            asset_profile=profile,
            window_bounds=window_bounds,
            search_region_bounds=search_region.as_tuple(),
            screenshot_captured=True,
            backend_available=True,
            supported=True,
            copy_assets=copy_assets,
            missing_copy_assets=(),
            scroll_assets=scroll_assets,
            missing_scroll_assets=missing_scroll_assets,
            copy_button_found=True,
            copy_button_bbox=bbox,
            copy_button_click_point=click_point,
            copy_button_click_point_safe=click_safe,
            copy_button_confidence=confidence,
            matched_asset_path=asset_path,
            copy_detection_attempt_count=copy_detection_attempt_count,
            scroll_button_found=scroll_bbox is not None,
            scroll_button_bbox=scroll_bbox,
            scroll_button_click_point=scroll_click_point,
            scroll_button_click_point_safe=scroll_click_safe,
            scroll_button_confidence=scroll_confidence,
            matched_scroll_asset_path=scroll_asset_path,
            scroll_attempted=scroll_attempted,
            scroll_succeeded=scroll_succeeded,
            recaptured_after_scroll=recaptured_after_scroll,
            confidence_threshold=copy_threshold,
            capture_attempted=False,
            screenshot_path=screenshot_path,
            post_scroll_screenshot_path=post_scroll_screenshot_path,
            annotated_screenshot_path=annotated_path,
            error=f"Clipboard is required to capture a {profile} response.",
        )
    before_text = clipboard.read_text()
    sentinel = f"AGENT_BRIDGE_CHATGPT_MAC_RESPONSE_COPY_SENTINEL_{time.monotonic_ns()}"
    try:
        clipboard.copy_text(sentinel)
    except Exception as error:
        return ChatGPTMacResponseCaptureResult(
            target_app=target.app_name,
            asset_profile=profile,
            window_bounds=window_bounds,
            search_region_bounds=search_region.as_tuple(),
            screenshot_captured=True,
            backend_available=True,
            supported=True,
            copy_assets=copy_assets,
            missing_copy_assets=(),
            scroll_assets=scroll_assets,
            missing_scroll_assets=missing_scroll_assets,
            copy_button_found=True,
            copy_button_bbox=bbox,
            copy_button_click_point=click_point,
            copy_button_click_point_safe=click_safe,
            copy_button_confidence=confidence,
            matched_asset_path=asset_path,
            copy_detection_attempt_count=copy_detection_attempt_count,
            scroll_button_found=scroll_bbox is not None,
            scroll_button_bbox=scroll_bbox,
            scroll_button_click_point=scroll_click_point,
            scroll_button_click_point_safe=scroll_click_safe,
            scroll_button_confidence=scroll_confidence,
            matched_scroll_asset_path=scroll_asset_path,
            scroll_attempted=scroll_attempted,
            scroll_succeeded=scroll_succeeded,
            recaptured_after_scroll=recaptured_after_scroll,
            confidence_threshold=copy_threshold,
            capture_attempted=False,
            screenshot_path=screenshot_path,
            post_scroll_screenshot_path=post_scroll_screenshot_path,
            annotated_screenshot_path=annotated_path,
            error=f"Clipboard sentinel setup failed before {profile} response copy: {error}",
        )
    if not click_safe:
        return ChatGPTMacResponseCaptureResult(
            target_app=target.app_name,
            asset_profile=profile,
            window_bounds=window_bounds,
            search_region_bounds=search_region.as_tuple(),
            screenshot_captured=True,
            backend_available=True,
            supported=True,
            copy_assets=copy_assets,
            missing_copy_assets=(),
            scroll_assets=scroll_assets,
            missing_scroll_assets=missing_scroll_assets,
            copy_button_found=True,
            copy_button_bbox=bbox,
            copy_button_click_point=click_point,
            copy_button_click_point_safe=False,
            copy_button_confidence=confidence,
            matched_asset_path=asset_path,
            copy_detection_attempt_count=copy_detection_attempt_count,
            scroll_button_found=scroll_bbox is not None,
            scroll_button_bbox=scroll_bbox,
            scroll_button_click_point=scroll_click_point,
            scroll_button_click_point_safe=scroll_click_safe,
            scroll_button_confidence=scroll_confidence,
            matched_scroll_asset_path=scroll_asset_path,
            scroll_attempted=scroll_attempted,
            scroll_succeeded=scroll_succeeded,
            recaptured_after_scroll=recaptured_after_scroll,
            confidence_threshold=copy_threshold,
            capture_attempted=False,
            screenshot_path=screenshot_path,
            post_scroll_screenshot_path=post_scroll_screenshot_path,
            annotated_screenshot_path=annotated_path,
            error=f"{profile} response copy button click point was outside the safe bounded region.",
        )
    try:
        if clicker is not None:
            clicker(click_point[0], click_point[1])
        else:
            import pyautogui  # type: ignore[import-not-found]

            pyautogui.click(click_point[0], click_point[1])
    except Exception as error:
        return ChatGPTMacResponseCaptureResult(
            target_app=target.app_name,
            asset_profile=profile,
            window_bounds=window_bounds,
            search_region_bounds=search_region.as_tuple(),
            screenshot_captured=True,
            backend_available=True,
            supported=True,
            copy_assets=copy_assets,
            missing_copy_assets=(),
            scroll_assets=scroll_assets,
            missing_scroll_assets=missing_scroll_assets,
            copy_button_found=True,
            copy_button_bbox=bbox,
            copy_button_click_point=click_point,
            copy_button_click_point_safe=click_safe,
            copy_button_confidence=confidence,
            matched_asset_path=asset_path,
            copy_detection_attempt_count=copy_detection_attempt_count,
            scroll_button_found=scroll_bbox is not None,
            scroll_button_bbox=scroll_bbox,
            scroll_button_click_point=scroll_click_point,
            scroll_button_click_point_safe=scroll_click_safe,
            scroll_button_confidence=scroll_confidence,
            matched_scroll_asset_path=scroll_asset_path,
            scroll_attempted=scroll_attempted,
            scroll_succeeded=scroll_succeeded,
            recaptured_after_scroll=recaptured_after_scroll,
            confidence_threshold=copy_threshold,
            capture_attempted=True,
            screenshot_path=screenshot_path,
            post_scroll_screenshot_path=post_scroll_screenshot_path,
            annotated_screenshot_path=annotated_path,
            error=f"{profile} response copy click failed: {error}",
        )
    sleep_fn(0.5)
    after_text = clipboard.read_text()
    response_text = after_text if after_text != sentinel else ""
    expected_found = expected_marker in response_text if expected_marker else None
    if not response_text.strip():
        try:
            clipboard.copy_text(before_text)
        except Exception:
            pass
        error = "Clipboard did not change after ChatGPT Mac response copy attempt."
    elif expected_marker and not expected_found:
        error = f"Copied {profile} response did not contain {expected_marker}."
    else:
        error = None
    return ChatGPTMacResponseCaptureResult(
        target_app=target.app_name,
        asset_profile=profile,
        window_bounds=window_bounds,
        search_region_bounds=search_region.as_tuple(),
        screenshot_captured=True,
        backend_available=True,
        supported=True,
        copy_assets=copy_assets,
        missing_copy_assets=(),
        scroll_assets=scroll_assets,
        missing_scroll_assets=missing_scroll_assets,
        copy_button_found=True,
        copy_button_bbox=bbox,
        copy_button_click_point=click_point,
        copy_button_click_point_safe=click_safe,
        copy_button_confidence=confidence,
        matched_asset_path=asset_path,
        copy_detection_attempt_count=copy_detection_attempt_count,
        scroll_button_found=scroll_bbox is not None,
        scroll_button_bbox=scroll_bbox,
        scroll_button_click_point=scroll_click_point,
        scroll_button_click_point_safe=scroll_click_safe,
        scroll_button_confidence=scroll_confidence,
        matched_scroll_asset_path=scroll_asset_path,
        scroll_attempted=scroll_attempted,
        scroll_succeeded=scroll_succeeded,
        recaptured_after_scroll=recaptured_after_scroll,
        confidence_threshold=copy_threshold,
        configured_confidence_threshold=float(configured_copy_threshold),
        effective_confidence_threshold=copy_threshold,
        threshold_cap_applied=copy_cap_applied,
        capture_attempted=True,
        response_captured=error is None,
        response_length=len(response_text),
        expected_marker_found=expected_found,
        screenshot_path=screenshot_path,
        post_scroll_screenshot_path=post_scroll_screenshot_path,
        annotated_screenshot_path=annotated_path,
        error=error,
    )


def format_chatgpt_mac_response_capture(result: ChatGPTMacResponseCaptureResult) -> str:
    confidence = (
        f"{result.copy_button_confidence:.3f}"
        if result.copy_button_confidence is not None
        else "unavailable"
    )
    scroll_confidence = (
        f"{result.scroll_button_confidence:.3f}"
        if result.scroll_button_confidence is not None
        else "unavailable"
    )
    copy_scale = (
        f"{result.copy_button_selected_scale:.3f}"
        if result.copy_button_selected_scale is not None
        else "unavailable"
    )
    copy_appearance = (
        f"{result.copy_button_appearance_score:.3f}"
        if result.copy_button_appearance_score is not None
        else "unavailable"
    )
    scroll_scale = (
        f"{result.scroll_button_selected_scale:.3f}"
        if result.scroll_button_selected_scale is not None
        else "unavailable"
    )
    scroll_appearance = (
        f"{result.scroll_button_appearance_score:.3f}"
        if result.scroll_button_appearance_score is not None
        else "unavailable"
    )
    threshold = (
        f"{result.confidence_threshold:.3f}"
        if result.confidence_threshold is not None
        else "unavailable"
    )
    configured_threshold = (
        f"{result.configured_confidence_threshold:.3f}"
        if result.configured_confidence_threshold is not None
        else threshold
    )
    effective_threshold = (
        f"{result.effective_confidence_threshold:.3f}"
        if result.effective_confidence_threshold is not None
        else threshold
    )
    marker_found = (
        "unknown"
        if result.expected_marker_found is None
        else ("yes" if result.expected_marker_found else "no")
    )
    lines = [
        "# ChatGPT Response Capture Diagnostic",
        "",
        f"Target app: {result.target_app}",
        f"Asset profile: {result.asset_profile}",
        f"Selected window bounds: {result.window_bounds or 'unavailable'}",
        f"Search region bounds: {result.search_region_bounds or 'unavailable'}",
        f"Screenshot captured: {'yes' if result.screenshot_captured else 'no'}",
        f"Backend available: {'yes' if result.backend_available else 'no'}",
        f"Response capture supported: {'yes' if result.supported else 'no'}",
        f"Copy assets: {', '.join(result.copy_assets)}",
        (
            "Missing copy assets: "
            + (", ".join(result.missing_copy_assets) if result.missing_copy_assets else "none")
        ),
        f"Scroll-down assets: {', '.join(result.scroll_assets)}",
        (
            "Missing scroll-down assets: "
            + (
                ", ".join(result.missing_scroll_assets)
                if result.missing_scroll_assets
                else "none"
            )
        ),
        f"Copy button found: {'yes' if result.copy_button_found else 'no'}",
        f"Copy button bbox: {result.copy_button_bbox or 'unavailable'}",
        f"Copy button click point: {result.copy_button_click_point or 'unavailable'}",
        f"Copy button click point safe: {'yes' if result.copy_button_click_point_safe else 'no'}",
        f"Copy button confidence: {confidence}",
        f"Copy button original template size: {result.copy_button_original_template_size or 'unavailable'}",
        f"Copy button selected scale: {copy_scale}",
        f"Copy button scaled template size: {result.copy_button_scaled_template_size or 'unavailable'}",
        f"Copy button appearance score: {copy_appearance}",
        f"Copy detection attempts: {result.copy_detection_attempt_count}",
        f"Scroll-down button found: {'yes' if result.scroll_button_found else 'no'}",
        f"Scroll-down button bbox: {result.scroll_button_bbox or 'unavailable'}",
        f"Scroll-down click point: {result.scroll_button_click_point or 'unavailable'}",
        f"Scroll-down click point safe: {'yes' if result.scroll_button_click_point_safe else 'no'}",
        f"Scroll-down confidence: {scroll_confidence}",
        f"Scroll-down original template size: {result.scroll_button_original_template_size or 'unavailable'}",
        f"Scroll-down selected scale: {scroll_scale}",
        f"Scroll-down scaled template size: {result.scroll_button_scaled_template_size or 'unavailable'}",
        f"Scroll-down appearance score: {scroll_appearance}",
        f"Matched scroll-down asset path: {result.matched_scroll_asset_path or 'unavailable'}",
        f"Scroll-down attempted: {'yes' if result.scroll_attempted else 'no'}",
        f"Scroll-down succeeded: {'yes' if result.scroll_succeeded else 'no'}",
        f"Recaptured after scroll-down: {'yes' if result.recaptured_after_scroll else 'no'}",
        f"Confidence threshold: {threshold}",
        f"Configured confidence threshold: {configured_threshold}",
        f"Effective confidence threshold: {effective_threshold}",
        f"Threshold cap applied: {'yes' if result.threshold_cap_applied else 'no'}",
        f"Matched asset path: {result.matched_asset_path or 'unavailable'}",
        f"Capture attempted: {'yes' if result.capture_attempted else 'no'}",
        f"Response captured: {'yes' if result.response_captured else 'no'}",
        f"Response length: {result.response_length}",
        f"Expected marker found: {marker_found}",
        f"Screenshot path: {result.screenshot_path or 'not written'}",
        f"Post-scroll screenshot path: {result.post_scroll_screenshot_path or 'not written'}",
        f"Annotated screenshot path: {result.annotated_screenshot_path or 'not written'}",
        f"Error: {result.error or 'none'}",
    ]
    return "\n".join(lines)


def _missing_assets(paths: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(path for path in paths if not Path(path).exists())


def response_copy_templates_for_profile(profile: str | None) -> tuple[str, ...]:
    normalized = (profile or "chatgpt_mac").strip().lower().replace("-", "_")
    if normalized == "chatgpt_chrome_app":
        return CHATGPT_CHROME_APP_COPY_RESPONSE_TEMPLATES
    return CHATGPT_MAC_COPY_RESPONSE_TEMPLATES


def scroll_down_templates_for_profile(profile: str | None) -> tuple[str, ...]:
    normalized = (profile or "chatgpt_mac").strip().lower().replace("-", "_")
    if normalized == "chatgpt_chrome_app":
        return CHATGPT_CHROME_APP_SCROLL_DOWN_TEMPLATES
    return CHATGPT_MAC_SCROLL_DOWN_TEMPLATES


def _point_inside_region(
    point: tuple[int, int],
    *,
    window_bounds: tuple[int, int, int, int],
    search_region: VisualRect,
) -> bool:
    return VisualRect(*window_bounds).contains_point(point) and search_region.contains_point(point)


def _capture_screenshot(
    window_bounds: tuple[int, int, int, int],
    *,
    screenshot_provider: Callable[[tuple[int, int, int, int]], Any] | None,
) -> Any | str:
    if screenshot_provider is not None:
        try:
            return screenshot_provider(window_bounds)
        except Exception as error:
            return f"Screenshot provider failed: {error}"
    try:
        import pyautogui  # type: ignore[import-not-found]
    except Exception as error:
        return f"PyAutoGUI screenshot backend unavailable: {error}"
    try:
        return pyautogui.screenshot(region=window_bounds)
    except Exception as error:
        return f"Screenshot capture failed: {error}"


def _template_scales(target: ManualStageTarget) -> tuple[float, ...]:
    if not target.visual_plus_multiscale_enabled:
        return (1.0,)
    scale_min = max(0.05, float(target.visual_scale_min))
    scale_max = max(scale_min, float(target.visual_scale_max))
    step = max(0.01, float(target.visual_scale_step))
    values: list[float] = []
    current = scale_min
    while current <= scale_max + 1e-9:
        values.append(round(current, 4))
        current += step
    if not any(abs(value - 1.0) < 1e-9 for value in values) and scale_min <= 1.0 <= scale_max:
        values.append(1.0)
        values.sort()
    return tuple(values or [1.0])


def _find_template_button(
    *,
    screenshot: Any,
    window_bounds: tuple[int, int, int, int],
    search_region: VisualRect,
    template_paths: tuple[str, ...],
    threshold: float,
    scales: tuple[float, ...],
    appearance_score_threshold: float | None,
    label: str,
) -> _TemplateButtonMatch | str:
    try:
        import cv2  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
        from PIL import Image  # type: ignore[import-not-found]
    except Exception as error:
        return f"OpenCV/Numpy/Pillow template matching unavailable: {error}"
    try:
        raw_image = np.array(
            screenshot if isinstance(screenshot, Image.Image) else Image.open(screenshot)
        )
        color_image = _rgb_array_from_screenshot(raw_image, cv2)
        if raw_image.ndim == 3 and raw_image.shape[2] == 4:
            image = cv2.cvtColor(raw_image, cv2.COLOR_RGBA2GRAY)
        elif raw_image.ndim == 3:
            image = cv2.cvtColor(raw_image, cv2.COLOR_RGB2GRAY)
        else:
            image = raw_image
        wx, wy, _, _ = window_bounds
        sx = search_region.x - wx
        sy = search_region.y - wy
        search = image[sy : sy + search_region.height, sx : sx + search_region.width]
        search_color = color_image[
            sy : sy + search_region.height,
            sx : sx + search_region.width,
        ]
        if search.size == 0:
            return f"ChatGPT {label} search region was empty."
        best: _TemplateButtonMatch | None = None
        for template_path in template_paths:
            template = cv2.imread(template_path, cv2.IMREAD_UNCHANGED)
            if template is None:
                continue
            if template.ndim == 3 and template.shape[2] == 4:
                alpha = template[:, :, 3]
                template_gray = cv2.cvtColor(template[:, :, :3], cv2.COLOR_BGR2GRAY)
                template_gray = cv2.bitwise_and(template_gray, template_gray, mask=alpha)
            elif template.ndim == 3:
                template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
            else:
                template_gray = template
            original_height, original_width = template_gray.shape[:2]
            for scale in scales:
                width = max(1, int(original_width * scale))
                height = max(1, int(original_height * scale))
                if width > search.shape[1] or height > search.shape[0]:
                    continue
                resized = cv2.resize(
                    template_gray,
                    (width, height),
                    interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC,
                )
                matched = cv2.matchTemplate(search, resized, cv2.TM_CCOEFF_NORMED)
                _, max_value, _, max_location = cv2.minMaxLoc(matched)
                appearance_score = _template_appearance_score(
                    cv2=cv2,
                    np=np,
                    search_color=search_color,
                    template=template,
                    max_location=max_location,
                    size=(width, height),
                )
                candidate = _TemplateButtonMatch(
                    bbox=(
                        search_region.x + max_location[0],
                        search_region.y + max_location[1],
                        width,
                        height,
                    ),
                    confidence=float(max_value),
                    asset_path=template_path,
                    original_template_size=(original_width, original_height),
                    scaled_template_size=(width, height),
                    selected_scale=scale,
                    appearance_score=appearance_score,
                )
                if best is None or (candidate.confidence or 0.0) > (best.confidence or 0.0):
                    best = candidate
        if best is None:
            return _TemplateButtonMatch(
                bbox=None,
                confidence=None,
                asset_path=None,
                rejection_reason="template_larger_than_search_region",
            )
        appearance_rejected = (
            appearance_score_threshold is not None
            and best.appearance_score is not None
            and best.appearance_score > appearance_score_threshold
        )
        accepted = (best.confidence or 0.0) >= threshold and not appearance_rejected
        return _TemplateButtonMatch(
            bbox=best.bbox if accepted else None,
            confidence=best.confidence,
            asset_path=best.asset_path,
            original_template_size=best.original_template_size,
            scaled_template_size=best.scaled_template_size,
            selected_scale=best.selected_scale,
            appearance_score=best.appearance_score,
            accepted=accepted,
            rejection_reason=(
                None
                if accepted
                else (
                    "appearance_score_above_threshold"
                    if appearance_rejected
                    else "confidence_below_threshold"
                )
            ),
        )
    except Exception as error:
        return f"ChatGPT {label} template matching failed: {error}"


def _rgb_array_from_screenshot(raw_image: Any, cv2: Any) -> Any:
    if raw_image.ndim == 3 and raw_image.shape[2] == 4:
        return cv2.cvtColor(raw_image, cv2.COLOR_RGBA2RGB)
    if raw_image.ndim == 3:
        return raw_image[:, :, :3]
    return cv2.cvtColor(raw_image, cv2.COLOR_GRAY2RGB)


def _template_appearance_score(
    *,
    cv2: Any,
    np: Any,
    search_color: Any,
    template: Any,
    max_location: tuple[int, int],
    size: tuple[int, int],
) -> float | None:
    width, height = size
    x, y = max_location
    candidate_region = search_color[y : y + height, x : x + width]
    if candidate_region.size == 0:
        return None
    if template.ndim == 3 and template.shape[2] == 4:
        template_rgb = cv2.cvtColor(template[:, :, :3], cv2.COLOR_BGR2RGB)
    elif template.ndim == 3:
        template_rgb = cv2.cvtColor(template, cv2.COLOR_BGR2RGB)
    else:
        template_rgb = cv2.cvtColor(template, cv2.COLOR_GRAY2RGB)
    template_rgb = cv2.resize(
        template_rgb,
        (width, height),
        interpolation=cv2.INTER_AREA,
    )
    diff = np.abs(candidate_region.astype("float32") - template_rgb.astype("float32"))
    return float(diff.mean())


def _save_image_if_possible(image: Any, path: Path) -> None:
    try:
        image.save(path)
    except Exception:
        return


def _save_annotation_if_possible(
    image: Any,
    path: Path,
    *,
    search_region: VisualRect,
    window_bounds: tuple[int, int, int, int],
    copy_bbox: tuple[int, int, int, int] | None,
    scroll_bbox: tuple[int, int, int, int] | None = None,
) -> None:
    try:
        from PIL import ImageDraw

        annotated = image.copy()
        draw = ImageDraw.Draw(annotated)
        wx, wy, _, _ = window_bounds

        def local_rect(rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
            return (
                rect[0] - wx,
                rect[1] - wy,
                rect[0] - wx + rect[2],
                rect[1] - wy + rect[3],
            )

        draw.rectangle(local_rect(search_region.as_tuple()), outline="yellow", width=3)
        if copy_bbox is not None:
            draw.rectangle(local_rect(copy_bbox), outline="cyan", width=3)
        if scroll_bbox is not None:
            draw.rectangle(local_rect(scroll_bbox), outline="orange", width=3)
        annotated.save(path)
    except Exception:
        return
