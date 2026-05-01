from __future__ import annotations

import time
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from agent_bridge.gui.macos_apps import ManualStageTarget
from agent_bridge.gui.visual_detector import (
    VisualRect,
    plus_search_region,
    point_is_safe,
    safe_search_region,
)
from agent_bridge.gui.visual_thresholds import (
    effective_visual_threshold,
    visual_threshold_cap_applied,
)


class VisualGuiState(str, Enum):
    IDLE = "IDLE"
    COMPOSER_HAS_TEXT = "COMPOSER_HAS_TEXT"
    RUNNING = "RUNNING"
    AMBIGUOUS = "AMBIGUOUS"
    UNKNOWN = "UNKNOWN"


class VisualAssetKind(str, Enum):
    PLUS = "plus"
    SEND_DISABLED = "send_disabled"
    SEND = "send"
    STOP = "stop"


STATE_BY_KIND = {
    VisualAssetKind.SEND_DISABLED: VisualGuiState.IDLE,
    VisualAssetKind.SEND: VisualGuiState.COMPOSER_HAS_TEXT,
    VisualAssetKind.STOP: VisualGuiState.RUNNING,
}
STATE_PRIORITY = {
    VisualGuiState.RUNNING: 0,
    VisualGuiState.COMPOSER_HAS_TEXT: 1,
    VisualGuiState.IDLE: 2,
}
CHATGPT_MAC_STATE_AMBIGUITY_MARGIN = 0.02
CHATGPT_MAC_SEND_BBOX_IOU_THRESHOLD = 0.70
CHATGPT_MAC_APPEARANCE_SCORE_MARGIN = 20.0
STATE_GLYPH_EDGE_MIN_SCORE = 0.70


@dataclass(frozen=True)
class VisualAssetProfile:
    profile_id: str
    plus_templates: tuple[str, ...]
    send_disabled_templates: tuple[str, ...]
    send_templates: tuple[str, ...]
    stop_templates: tuple[str, ...]

    def templates_for_kind(self, kind: VisualAssetKind) -> tuple[str, ...]:
        if kind == VisualAssetKind.PLUS:
            return self.plus_templates
        if kind == VisualAssetKind.SEND_DISABLED:
            return self.send_disabled_templates
        if kind == VisualAssetKind.SEND:
            return self.send_templates
        if kind == VisualAssetKind.STOP:
            return self.stop_templates
        return ()


@dataclass(frozen=True)
class VisualAssetMatch:
    asset_kind: VisualAssetKind
    state: VisualGuiState | None
    template_path: str
    bbox: tuple[int, int, int, int]
    confidence: float
    template_size: tuple[int, int]
    original_template_size: tuple[int, int] | None = None
    selected_scale: float = 1.0
    appearance_score: float | None = None
    edge_score: float | None = None
    glyph_score: float | None = None
    composite_score: float | None = None

    @property
    def center(self) -> tuple[int, int]:
        return VisualRect(*self.bbox).center

    @property
    def visual_score(self) -> float:
        return self.composite_score if self.composite_score is not None else self.confidence


@dataclass(frozen=True)
class VisualAssetTemplateDiagnostic:
    asset_kind: VisualAssetKind
    state: VisualGuiState | None
    template_path: str
    template_exists: bool
    search_region_bounds: tuple[int, int, int, int] | None
    original_template_size: tuple[int, int] | None
    template_size: tuple[int, int] | None
    selected_scale: float | None
    best_match_bbox: tuple[int, int, int, int] | None
    best_match_confidence: float | None
    appearance_score: float | None
    threshold: float
    accepted: bool
    edge_score: float | None = None
    glyph_score: float | None = None
    composite_score: float | None = None
    score_gap_to_next_best: float | None = None
    rejection_reason: str | None = None
    configured_threshold: float | None = None
    effective_threshold: float | None = None
    threshold_cap_applied: bool = False


@dataclass(frozen=True)
class VisualStateDetection:
    selected_app: str
    asset_profile: str
    window_bounds: tuple[int, int, int, int] | None
    safe_region_bounds: tuple[int, int, int, int] | None
    screenshot_captured: bool
    backend_available: bool
    matched_state: VisualGuiState
    plus_search_region_bounds: tuple[int, int, int, int] | None = None
    matched_asset_path: str | None = None
    matched_asset_kind: VisualAssetKind | None = None
    matched_bbox: tuple[int, int, int, int] | None = None
    confidence: float | None = None
    state_ambiguous: bool = False
    state_selection_reason: str | None = None
    plus_anchor_found: bool = False
    plus_anchor_bbox: tuple[int, int, int, int] | None = None
    plus_anchor_confidence: float | None = None
    computed_composer_click_point: tuple[int, int] | None = None
    composer_click_point_safe: bool = False
    matches: tuple[VisualAssetMatch, ...] = ()
    template_diagnostics: tuple[VisualAssetTemplateDiagnostic, ...] = ()
    screenshot_path: str | None = None
    annotated_screenshot_path: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class VisualIdleWaitResult:
    selected_app: str
    asset_profile: str
    final_state: VisualGuiState
    poll_count: int
    elapsed_seconds: float
    timeout_action: str | None
    should_proceed: bool
    should_overwrite: bool
    should_abort: bool
    detection: VisualStateDetection
    error: str | None = None


@dataclass(frozen=True)
class _TemplateMatchBundle:
    matches: tuple[VisualAssetMatch, ...]
    diagnostics: tuple[VisualAssetTemplateDiagnostic, ...]


@dataclass(frozen=True)
class _StateSelection:
    state: VisualGuiState
    match: VisualAssetMatch | None
    ambiguous: bool = False
    reason: str = "unknown"


def default_asset_profile(profile_id: str) -> VisualAssetProfile:
    normalized = profile_id.strip().lower().replace("-", "_")
    if normalized == "chatgpt_mac":
        return VisualAssetProfile(
            profile_id="chatgpt_mac",
            plus_templates=(
                "assets/gui/chatgpt_mac/chatgpt_mac_plus_button_light.png",
                "assets/gui/chatgpt_mac/chatgpt_mac_plus_button_dark.png",
            ),
            send_disabled_templates=(
                "assets/gui/chatgpt_mac/chatgpt_mac_send_disabled_button_light.png",
                "assets/gui/chatgpt_mac/chatgpt_mac_send_disabled_button_dark.png",
            ),
            send_templates=(
                "assets/gui/chatgpt_mac/chatgpt_mac_send_button_light.png",
                "assets/gui/chatgpt_mac/chatgpt_mac_send_button_dark.png",
            ),
            stop_templates=(
                "assets/gui/chatgpt_mac/chatgpt_mac_stop_button_light.png",
                "assets/gui/chatgpt_mac/chatgpt_mac_stop_button_dark.png",
            ),
        )
    if normalized == "chatgpt_chrome_app":
        return VisualAssetProfile(
            profile_id="chatgpt_chrome_app",
            plus_templates=(
                "assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_plus_button_light.png",
                "assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_plus_button_dark.png",
            ),
            send_disabled_templates=(
                "assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_voice_button_light.png",
                "assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_voice_button_dark.png",
            ),
            send_templates=(
                "assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_send_button_light.png",
                "assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_send_button_dark.png",
            ),
            stop_templates=(
                "assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_stop_button_light.png",
                "assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_stop_button_dark.png",
            ),
        )
    if normalized == "codex":
        return VisualAssetProfile(
            profile_id="codex",
            plus_templates=(
                "assets/gui/codex/codex_plus_button_light.png",
                "assets/gui/codex/codex_plus_button_dark.png",
            ),
            send_disabled_templates=(
                "assets/gui/codex/codex_send_disabled_button_light.png",
                "assets/gui/codex/codex_send_disabled_button_dark.png",
            ),
            send_templates=(
                "assets/gui/codex/codex_send_button_light.png",
                "assets/gui/codex/codex_send_button_dark.png",
            ),
            stop_templates=(
                "assets/gui/codex/codex_stop_button_light.png",
                "assets/gui/codex/codex_stop_button_dark.png",
            ),
        )
    raise ValueError(f"Unsupported visual asset profile: {profile_id}")


def asset_profile_for_target(target: ManualStageTarget) -> VisualAssetProfile:
    profile_id = target.visual_asset_profile
    if not profile_id:
        combined = " ".join(
            part
            for part in (target.app_name, target.window_hint, target.backend)
            if part
        ).lower()
        profile_id = "codex" if "codex" in combined else "chatgpt_mac"
    base = default_asset_profile(profile_id)
    return VisualAssetProfile(
        profile_id=base.profile_id,
        plus_templates=target.visual_plus_templates or base.plus_templates,
        send_disabled_templates=(
            target.visual_send_disabled_templates or base.send_disabled_templates
        ),
        send_templates=target.visual_send_templates or base.send_templates,
        stop_templates=target.visual_stop_templates or base.stop_templates,
    )


def visual_state_search_region(
    window_bounds: tuple[int, int, int, int],
    target: ManualStageTarget,
) -> VisualRect:
    region_name = target.visual_state_search_region.strip().lower().replace("-", "_")
    profile = (target.visual_asset_profile or "").strip().lower().replace("-", "_")
    if region_name == "lower_control_band":
        if profile == "chatgpt_chrome_app":
            return safe_search_region(
                window_bounds,
                lower_height_ratio=0.45,
                min_x_ratio=0.02,
                max_x_ratio=0.98,
            )
        return safe_search_region(
            window_bounds,
            lower_height_ratio=0.4,
            min_x_ratio=0.02,
            max_x_ratio=0.98,
        )
    if region_name == "lower_composer_band":
        return plus_search_region(window_bounds)
    return plus_search_region(window_bounds)


def visual_asset_search_region(
    window_bounds: tuple[int, int, int, int],
    target: ManualStageTarget,
    profile_id: str,
    kind: VisualAssetKind,
    *,
    default_region: VisualRect | None = None,
) -> VisualRect:
    default = default_region or visual_state_search_region(window_bounds, target)
    normalized_profile = profile_id.strip().lower().replace("-", "_")
    if normalized_profile == "chatgpt_chrome_app" and kind == VisualAssetKind.PLUS:
        return safe_search_region(
            window_bounds,
            lower_height_ratio=0.25,
            min_x_ratio=0.04,
            max_x_ratio=0.70,
        )
    if normalized_profile == "chatgpt_mac" and kind == VisualAssetKind.PLUS:
        return safe_search_region(
            window_bounds,
            lower_height_ratio=0.18,
            min_x_ratio=0.02,
            max_x_ratio=0.26,
        )
    if normalized_profile == "chatgpt_mac" and kind in {
        VisualAssetKind.SEND_DISABLED,
        VisualAssetKind.SEND,
        VisualAssetKind.STOP,
    }:
        return safe_search_region(
            window_bounds,
            lower_height_ratio=0.24,
            min_x_ratio=0.78,
            max_x_ratio=0.99,
        )
    return default


@dataclass
class AssetVisualStateDetector:
    screenshot_provider: Callable[[tuple[int, int, int, int]], Any] | None = None

    def detect(
        self,
        *,
        target: ManualStageTarget,
        window_bounds: tuple[int, int, int, int] | None,
        profile: VisualAssetProfile | None = None,
        logs_dir: Path | None = None,
        write_debug: bool = False,
    ) -> VisualStateDetection:
        profile = profile or asset_profile_for_target(target)
        if window_bounds is None:
            return VisualStateDetection(
                selected_app=target.app_name,
                asset_profile=profile.profile_id,
                window_bounds=None,
                safe_region_bounds=None,
                screenshot_captured=False,
                backend_available=False,
                matched_state=VisualGuiState.UNKNOWN,
                error=f"{target.app_name} window bounds were unavailable.",
            )
        screenshot = self._capture_screenshot(window_bounds)
        safe_region = visual_state_search_region(window_bounds, target)
        plus_region = visual_asset_search_region(
            window_bounds,
            target,
            profile.profile_id,
            VisualAssetKind.PLUS,
            default_region=safe_region,
        )
        if isinstance(screenshot, str):
            return VisualStateDetection(
                selected_app=target.app_name,
                asset_profile=profile.profile_id,
                window_bounds=window_bounds,
                safe_region_bounds=safe_region.as_tuple(),
                screenshot_captured=False,
                backend_available=False,
                matched_state=VisualGuiState.UNKNOWN,
                plus_search_region_bounds=plus_region.as_tuple(),
                error=screenshot,
            )

        screenshot_path: str | None = None
        annotated_path: str | None = None
        if write_debug and logs_dir is not None:
            logs_dir.mkdir(parents=True, exist_ok=True)
            safe_name = profile.profile_id.replace("/", "_")
            screenshot_path = str(logs_dir / f"{safe_name}_visual_state.png")
            annotated_path = str(logs_dir / f"{safe_name}_visual_state_annotated.png")
            _save_image_if_possible(screenshot, Path(screenshot_path))

        match_result = self._match_profile_templates(
            screenshot=screenshot,
            window_bounds=window_bounds,
            search_region=safe_region,
            target=target,
            profile=profile,
        )
        if isinstance(match_result, str):
            return VisualStateDetection(
                selected_app=target.app_name,
                asset_profile=profile.profile_id,
                window_bounds=window_bounds,
                safe_region_bounds=safe_region.as_tuple(),
                screenshot_captured=True,
                backend_available=False,
                matched_state=VisualGuiState.UNKNOWN,
                plus_search_region_bounds=plus_region.as_tuple(),
                screenshot_path=screenshot_path,
                annotated_screenshot_path=annotated_path,
                error=match_result,
            )

        matches = match_result.matches
        template_diagnostics = match_result.diagnostics
        state_matches = [match for match in matches if match.state is not None]
        state_selection = _select_state_match(
            state_matches,
            profile.profile_id,
            ambiguity_margin=target.visual_state_ambiguity_margin,
        )
        state_match = state_selection.match
        matches, template_diagnostics = _apply_state_competition_result(
            matches=matches,
            diagnostics=template_diagnostics,
            state_selection=state_selection,
        )
        plus_match = max(
            (match for match in matches if match.asset_kind == VisualAssetKind.PLUS),
            key=lambda match: match.visual_score,
            default=None,
        )
        click_point = None
        click_safe = False
        if plus_match is not None:
            plus_rect = VisualRect(*plus_match.bbox)
            center_x, center_y = plus_rect.center
            click_point = (
                int(center_x + target.plus_anchor_x_offset),
                int(center_y - target.plus_anchor_y_offset),
            )
            click_safe = point_is_safe(
                click_point,
                window_bounds=window_bounds,
                safe_region=safe_region,
                avoid_rect=plus_rect,
            )
        if write_debug and annotated_path is not None:
            _save_annotation_if_possible(
                screenshot,
                Path(annotated_path),
                search_region=safe_region,
                matches=matches,
                click_point=click_point,
                window_bounds=window_bounds,
            )
        return VisualStateDetection(
            selected_app=target.app_name,
            asset_profile=profile.profile_id,
            window_bounds=window_bounds,
            safe_region_bounds=safe_region.as_tuple(),
            screenshot_captured=True,
            backend_available=True,
            matched_state=state_selection.state,
            plus_search_region_bounds=plus_region.as_tuple(),
            matched_asset_path=state_match.template_path if state_match else None,
            matched_asset_kind=state_match.asset_kind if state_match else None,
            matched_bbox=state_match.bbox if state_match else None,
            confidence=state_match.confidence if state_match else None,
            state_ambiguous=state_selection.ambiguous,
            state_selection_reason=state_selection.reason,
            plus_anchor_found=plus_match is not None,
            plus_anchor_bbox=plus_match.bbox if plus_match else None,
            plus_anchor_confidence=plus_match.confidence if plus_match else None,
            computed_composer_click_point=click_point,
            composer_click_point_safe=click_safe,
            matches=matches,
            template_diagnostics=template_diagnostics,
            screenshot_path=screenshot_path,
            annotated_screenshot_path=annotated_path,
            error=_visual_state_error(state_selection=state_selection, plus_match=plus_match),
        )

    def _capture_screenshot(self, window_bounds: tuple[int, int, int, int]) -> Any | str:
        if self.screenshot_provider is not None:
            try:
                return self.screenshot_provider(window_bounds)
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

    def _match_profile_templates(
        self,
        *,
        screenshot: Any,
        window_bounds: tuple[int, int, int, int],
        search_region: VisualRect,
        target: ManualStageTarget,
        profile: VisualAssetProfile,
    ) -> _TemplateMatchBundle | str:
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
            matches: list[VisualAssetMatch] = []
            diagnostics: list[VisualAssetTemplateDiagnostic] = []
            for kind in VisualAssetKind:
                kind_search_region = visual_asset_search_region(
                    window_bounds,
                    target,
                    profile.profile_id,
                    kind,
                    default_region=search_region,
                )
                sx = kind_search_region.x - wx
                sy = kind_search_region.y - wy
                search = image[
                    sy : sy + kind_search_region.height,
                    sx : sx + kind_search_region.width,
                ]
                search_color = color_image[
                    sy : sy + kind_search_region.height,
                    sx : sx + kind_search_region.width,
                ]
                if search.size == 0:
                    return f"{target.app_name} visual search region was empty for {kind.value}."
                kind_threshold = (
                    target.visual_plus_confidence_threshold
                    if kind == VisualAssetKind.PLUS
                    else target.visual_state_confidence_threshold
                )
                kind_matches, kind_diagnostics = self._template_matches_for_kind(
                    cv2=cv2,
                    np=np,
                    search=search,
                    search_color=search_color,
                    search_region=kind_search_region,
                    template_paths=profile.templates_for_kind(kind),
                    threshold=kind_threshold,
                    scales=_template_scales(target),
                    appearance_score_threshold=target.visual_appearance_score_threshold,
                    state=STATE_BY_KIND.get(kind),
                    kind=kind,
                )
                matches.extend(kind_matches)
                diagnostics.extend(kind_diagnostics)
            return _TemplateMatchBundle(tuple(matches), tuple(diagnostics))
        except Exception as error:
            return f"{target.app_name} visual state template matching failed: {error}"

    def _template_matches_for_kind(
        self,
        *,
        cv2: Any,
        np: Any,
        search: Any,
        search_color: Any,
        search_region: VisualRect,
        template_paths: tuple[str, ...],
        threshold: float,
        scales: tuple[float, ...],
        appearance_score_threshold: float | None,
        state: VisualGuiState | None,
        kind: VisualAssetKind,
    ) -> tuple[tuple[VisualAssetMatch, ...], tuple[VisualAssetTemplateDiagnostic, ...]]:
        accepted: list[VisualAssetMatch] = []
        diagnostics: list[VisualAssetTemplateDiagnostic] = []
        configured_threshold = float(threshold)
        effective_threshold = effective_visual_threshold(configured_threshold)
        cap_applied = visual_threshold_cap_applied(configured_threshold)
        for raw_template_path in template_paths:
            template_path = Path(raw_template_path)
            if not template_path.exists():
                diagnostics.append(
                    VisualAssetTemplateDiagnostic(
                        asset_kind=kind,
                        state=state,
                        template_path=str(template_path),
                        template_exists=False,
                        search_region_bounds=search_region.as_tuple(),
                        original_template_size=None,
                        template_size=None,
                        selected_scale=None,
                        best_match_bbox=None,
                        best_match_confidence=None,
                        appearance_score=None,
                        threshold=effective_threshold,
                        accepted=False,
                        rejection_reason="template_missing",
                        configured_threshold=configured_threshold,
                        effective_threshold=effective_threshold,
                        threshold_cap_applied=cap_applied,
                    )
                )
                continue
            template = cv2.imread(str(template_path), cv2.IMREAD_UNCHANGED)
            if template is None:
                diagnostics.append(
                    VisualAssetTemplateDiagnostic(
                        asset_kind=kind,
                        state=state,
                        template_path=str(template_path),
                        template_exists=True,
                        search_region_bounds=search_region.as_tuple(),
                        original_template_size=None,
                        template_size=None,
                        selected_scale=None,
                        best_match_bbox=None,
                        best_match_confidence=None,
                        appearance_score=None,
                        threshold=effective_threshold,
                        accepted=False,
                        rejection_reason="template_unreadable",
                        configured_threshold=configured_threshold,
                        effective_threshold=effective_threshold,
                        threshold_cap_applied=cap_applied,
                    )
                )
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
            best: VisualAssetMatch | None = None
            scale_candidates: list[VisualAssetMatch] = []
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
                alpha_mask = _resized_alpha_mask(
                    cv2=cv2,
                    template=template,
                    size=(width, height),
                )
                focus_mask = _identity_focus_mask(
                    np=np,
                    shape=(height, width),
                    kind=kind,
                )
                matched = cv2.matchTemplate(search, resized, cv2.TM_CCOEFF_NORMED)
                _, max_value, _, max_location = cv2.minMaxLoc(matched)
                crop_gray = search[
                    max_location[1] : max_location[1] + height,
                    max_location[0] : max_location[0] + width,
                ]
                appearance_score = _template_appearance_score(
                    cv2=cv2,
                    np=np,
                    search_color=search_color,
                    template=template,
                    max_location=max_location,
                    size=(width, height),
                )
                edge_score = _edge_similarity_score(
                    cv2=cv2,
                    np=np,
                    crop_gray=crop_gray,
                    template_gray=resized,
                    alpha_mask=alpha_mask,
                    focus_mask=focus_mask,
                )
                glyph_score = _glyph_similarity_score(
                    cv2=cv2,
                    np=np,
                    crop_gray=crop_gray,
                    template_gray=resized,
                    alpha_mask=alpha_mask,
                    focus_mask=focus_mask,
                )
                composite_score = _composite_visual_score(
                    raw_correlation=float(max_value),
                    edge_score=edge_score,
                    glyph_score=glyph_score,
                    appearance_score=appearance_score,
                    kind=kind,
                )
                candidate = VisualAssetMatch(
                    asset_kind=kind,
                    state=state,
                    template_path=str(template_path),
                    bbox=(
                        search_region.x + max_location[0],
                        search_region.y + max_location[1],
                        width,
                        height,
                    ),
                    confidence=float(max_value),
                    template_size=(width, height),
                    original_template_size=(original_width, original_height),
                    selected_scale=scale,
                    appearance_score=appearance_score,
                    edge_score=edge_score,
                    glyph_score=glyph_score,
                    composite_score=composite_score,
                )
                scale_candidates.append(candidate)
                if best is None or candidate.visual_score > best.visual_score:
                    best = candidate
            if best is None:
                diagnostics.append(
                    VisualAssetTemplateDiagnostic(
                        asset_kind=kind,
                        state=state,
                        template_path=str(template_path),
                        template_exists=True,
                        search_region_bounds=search_region.as_tuple(),
                        original_template_size=(original_width, original_height),
                        template_size=(original_width, original_height),
                        selected_scale=None,
                        best_match_bbox=None,
                        best_match_confidence=None,
                        appearance_score=None,
                        threshold=effective_threshold,
                        accepted=False,
                        rejection_reason="template_larger_than_search_region",
                        configured_threshold=configured_threshold,
                        effective_threshold=effective_threshold,
                        threshold_cap_applied=cap_applied,
                    )
                )
                continue
            ranked_scale_candidates = sorted(
                scale_candidates,
                key=lambda candidate: candidate.visual_score,
                reverse=True,
            )
            score_gap_to_next_best = None
            if len(ranked_scale_candidates) > 1:
                score_gap_to_next_best = (
                    ranked_scale_candidates[0].visual_score
                    - ranked_scale_candidates[1].visual_score
                )
            appearance_rejected = (
                appearance_score_threshold is not None
                and best.appearance_score is not None
                and best.appearance_score > appearance_score_threshold
            )
            identity_rejected = (
                kind != VisualAssetKind.PLUS
                and (
                    best.edge_score is None
                    or best.glyph_score is None
                    or best.edge_score < STATE_GLYPH_EDGE_MIN_SCORE
                    or best.glyph_score < STATE_GLYPH_EDGE_MIN_SCORE
                )
            )
            is_accepted = (
                best.visual_score >= effective_threshold
                and not appearance_rejected
                and not identity_rejected
            )
            rejection_reason = None
            if not is_accepted:
                if appearance_rejected:
                    rejection_reason = "appearance_score_above_threshold"
                elif identity_rejected or best.confidence >= effective_threshold:
                    rejection_reason = "glyph_edge_mismatch"
                else:
                    rejection_reason = "confidence_below_threshold"
            diagnostics.append(
                VisualAssetTemplateDiagnostic(
                    asset_kind=kind,
                    state=state,
                    template_path=str(template_path),
                    template_exists=True,
                    search_region_bounds=search_region.as_tuple(),
                    original_template_size=best.original_template_size,
                    template_size=best.template_size,
                    selected_scale=best.selected_scale,
                    best_match_bbox=best.bbox,
                    best_match_confidence=best.confidence,
                    appearance_score=best.appearance_score,
                    threshold=effective_threshold,
                    accepted=is_accepted,
                    edge_score=best.edge_score,
                    glyph_score=best.glyph_score,
                    composite_score=best.composite_score,
                    score_gap_to_next_best=score_gap_to_next_best,
                    rejection_reason=rejection_reason,
                    configured_threshold=configured_threshold,
                    effective_threshold=effective_threshold,
                    threshold_cap_applied=cap_applied,
                )
            )
            if is_accepted:
                accepted.append(best)
        return tuple(accepted), tuple(diagnostics)


def wait_for_visual_idle(
    *,
    target: ManualStageTarget,
    detect_once: Callable[[], VisualStateDetection],
    timeout_seconds: int = 600,
    poll_interval_seconds: int = 10,
    on_timeout: str = "overwrite",
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
    event_callback: Callable[[str, dict[str, object]], None] | None = None,
) -> VisualIdleWaitResult:
    timeout = max(0, int(timeout_seconds))
    poll_interval = max(1, int(poll_interval_seconds))
    deadline = monotonic_fn() + timeout
    started = monotonic_fn()
    poll_count = 0
    _emit(
        event_callback,
        "asset_visual_idle_wait_started",
        app_name=target.app_name,
        timeout_seconds=timeout,
        poll_interval_seconds=poll_interval,
    )
    while True:
        detection = detect_once()
        elapsed = max(0.0, monotonic_fn() - started)
        _emit(
            event_callback,
            "asset_visual_idle_wait_poll",
            app_name=target.app_name,
            profile=detection.asset_profile,
            observed_state=detection.matched_state.value,
            poll_count=poll_count,
            elapsed_seconds=elapsed,
            remaining_seconds=max(0.0, deadline - monotonic_fn()),
        )
        if detection.matched_state == VisualGuiState.AMBIGUOUS:
            _emit(
                event_callback,
                "asset_visual_state_ambiguous",
                app_name=target.app_name,
                profile=detection.asset_profile,
                reason=detection.state_selection_reason,
            )
            return VisualIdleWaitResult(
                selected_app=target.app_name,
                asset_profile=detection.asset_profile,
                final_state=detection.matched_state,
                poll_count=poll_count,
                elapsed_seconds=elapsed,
                timeout_action=None,
                should_proceed=False,
                should_overwrite=False,
                should_abort=True,
                detection=detection,
                error=(
                    f"{target.app_name} visual state is ambiguous; paste/submit is blocked "
                    "until the state assets are calibrated."
                ),
            )
        if detection.matched_state == VisualGuiState.IDLE:
            _emit(
                event_callback,
                "asset_visual_idle_detected",
                app_name=target.app_name,
                profile=detection.asset_profile,
            )
            return VisualIdleWaitResult(
                selected_app=target.app_name,
                asset_profile=detection.asset_profile,
                final_state=detection.matched_state,
                poll_count=poll_count,
                elapsed_seconds=elapsed,
                timeout_action=None,
                should_proceed=True,
                should_overwrite=False,
                should_abort=False,
                detection=detection,
            )
        if monotonic_fn() >= deadline:
            action = "abort" if on_timeout == "abort" else "overwrite"
            should_overwrite = action == "overwrite" and detection.plus_anchor_found
            should_abort = action == "abort" or not should_overwrite
            _emit(
                event_callback,
                "asset_visual_idle_wait_timeout",
                app_name=target.app_name,
                profile=detection.asset_profile,
                observed_state=detection.matched_state.value,
                action=action,
                plus_anchor_found=detection.plus_anchor_found,
                elapsed_seconds=elapsed,
            )
            return VisualIdleWaitResult(
                selected_app=target.app_name,
                asset_profile=detection.asset_profile,
                final_state=detection.matched_state,
                poll_count=poll_count,
                elapsed_seconds=elapsed,
                timeout_action=action,
                should_proceed=should_overwrite,
                should_overwrite=should_overwrite,
                should_abort=should_abort,
                detection=detection,
                error=None if should_overwrite else f"{target.app_name} visual idle wait timed out.",
            )
        poll_count += 1
        sleep_fn(poll_interval)


def _select_state_match(
    matches: list[VisualAssetMatch],
    profile_id: str = "",
    *,
    ambiguity_margin: float = CHATGPT_MAC_STATE_AMBIGUITY_MARGIN,
) -> _StateSelection:
    if not matches:
        return _StateSelection(
            state=VisualGuiState.UNKNOWN,
            match=None,
            reason="no_state_match",
        )
    return _select_shared_state_match(
        matches,
        ambiguity_margin=ambiguity_margin,
    )


def _apply_state_competition_result(
    *,
    matches: tuple[VisualAssetMatch, ...],
    diagnostics: tuple[VisualAssetTemplateDiagnostic, ...],
    state_selection: _StateSelection,
) -> tuple[tuple[VisualAssetMatch, ...], tuple[VisualAssetTemplateDiagnostic, ...]]:
    selected_state = state_selection.state
    if (
        state_selection.ambiguous
        or selected_state in {VisualGuiState.UNKNOWN, VisualGuiState.AMBIGUOUS}
        or state_selection.match is None
    ):
        return matches, diagnostics
    filtered_matches = tuple(
        match
        for match in matches
        if match.state is None or match.state == selected_state
    )
    filtered_diagnostics = tuple(
        replace(
            diagnostic,
            accepted=False,
            rejection_reason="outcompeted_by_composite_score",
        )
        if diagnostic.accepted
        and diagnostic.state is not None
        and diagnostic.state != selected_state
        else diagnostic
        for diagnostic in diagnostics
    )
    return filtered_matches, filtered_diagnostics


def _rgb_array_from_screenshot(image: Any, cv2: Any) -> Any:
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    if image.ndim == 3 and image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_RGBA2RGB)
    if image.ndim == 3:
        return image[:, :, :3]
    return image


def _rgb_array_from_cv2_template(template: Any, cv2: Any) -> tuple[Any, Any | None]:
    if template.ndim == 2:
        return cv2.cvtColor(template, cv2.COLOR_GRAY2RGB), None
    if template.ndim == 3 and template.shape[2] == 4:
        return cv2.cvtColor(template[:, :, :3], cv2.COLOR_BGR2RGB), template[:, :, 3]
    if template.ndim == 3:
        return cv2.cvtColor(template[:, :, :3], cv2.COLOR_BGR2RGB), None
    return template, None


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
    if width <= 0 or height <= 0:
        return None
    crop = search_color[y : y + height, x : x + width]
    if crop.size == 0 or crop.shape[0] != height or crop.shape[1] != width:
        return None
    template_rgb, alpha = _rgb_array_from_cv2_template(template, cv2)
    resized = cv2.resize(
        template_rgb,
        (width, height),
        interpolation=cv2.INTER_AREA
        if width < template_rgb.shape[1] or height < template_rgb.shape[0]
        else cv2.INTER_CUBIC,
    )
    crop_float = crop.astype("float32")
    template_float = resized.astype("float32")
    if alpha is not None:
        alpha_resized = cv2.resize(alpha, (width, height), interpolation=cv2.INTER_AREA)
        mask = alpha_resized.astype("float32") / 255.0
        mask_weight = float(mask.sum())
        if mask_weight > 0.0:
            diff = np.abs(crop_float - template_float) * mask[:, :, None]
            return float(diff.sum() / (mask_weight * 3.0))
    return float(np.mean(np.abs(crop_float - template_float)))


def _resized_alpha_mask(*, cv2: Any, template: Any, size: tuple[int, int]) -> Any | None:
    if template.ndim != 3 or template.shape[2] != 4:
        return None
    alpha = template[:, :, 3]
    resized = cv2.resize(alpha, size, interpolation=cv2.INTER_AREA)
    if resized.size == 0:
        return None
    if float((resized > 8).sum()) / float(resized.size) > 0.98:
        return None
    return (resized > 8).astype("uint8") * 255


def _foreground_mask(
    *,
    cv2: Any,
    np: Any,
    gray: Any,
    alpha_mask: Any | None = None,
) -> Any:
    if alpha_mask is not None:
        alpha = alpha_mask > 0
    else:
        alpha = np.ones(gray.shape[:2], dtype=bool)
    if gray.size == 0:
        return np.zeros(gray.shape[:2], dtype=bool)
    border_parts = [
        gray[0, :],
        gray[-1, :],
        gray[:, 0],
        gray[:, -1],
    ]
    border = np.concatenate([part.reshape(-1) for part in border_parts])
    background = float(np.median(border))
    diff = np.abs(gray.astype("float32") - background)
    threshold = max(10.0, float(np.std(border)) * 1.5)
    mask = (diff >= threshold) & alpha
    min_pixels = max(4, int(gray.size * 0.01))
    if int(mask.sum()) < min_pixels:
        edges = cv2.Canny(gray.astype("uint8"), 40, 120) > 0
        mask = (mask | edges) & alpha
    return mask


def _identity_focus_mask(
    *,
    np: Any,
    shape: tuple[int, int],
    kind: VisualAssetKind,
) -> Any | None:
    if kind == VisualAssetKind.PLUS:
        return None
    height, width = shape
    if height < 8 or width < 8:
        return None
    margin_x = max(2, int(round(width * 0.22)))
    margin_y = max(2, int(round(height * 0.22)))
    if margin_x * 2 >= width or margin_y * 2 >= height:
        return None
    mask = np.zeros((height, width), dtype=bool)
    mask[margin_y : height - margin_y, margin_x : width - margin_x] = True
    return mask


def _edge_similarity_score(
    *,
    cv2: Any,
    np: Any,
    crop_gray: Any,
    template_gray: Any,
    alpha_mask: Any | None = None,
    focus_mask: Any | None = None,
) -> float:
    if crop_gray.shape[:2] != template_gray.shape[:2] or crop_gray.size == 0:
        return 0.0
    template_edges = cv2.Canny(template_gray.astype("uint8"), 40, 120) > 0
    crop_edges = cv2.Canny(crop_gray.astype("uint8"), 40, 120) > 0
    if alpha_mask is not None:
        alpha = alpha_mask > 0
        template_edges &= alpha
        crop_edges &= alpha
    if focus_mask is not None:
        template_edges &= focus_mask
        crop_edges &= focus_mask
    template_count = int(template_edges.sum())
    crop_count = int(crop_edges.sum())
    if template_count <= 0:
        return 0.0
    kernel = np.ones((3, 3), dtype="uint8")
    crop_dilated = cv2.dilate(crop_edges.astype("uint8"), kernel) > 0
    template_dilated = cv2.dilate(template_edges.astype("uint8"), kernel) > 0
    recall = float((template_edges & crop_dilated).sum()) / float(template_count)
    precision = (
        float((crop_edges & template_dilated).sum()) / float(crop_count)
        if crop_count > 0
        else 0.0
    )
    if recall <= 0.0 and precision <= 0.0:
        return 0.0
    return max(0.0, min(1.0, (2.0 * recall * precision) / (recall + precision)))


def _glyph_similarity_score(
    *,
    cv2: Any,
    np: Any,
    crop_gray: Any,
    template_gray: Any,
    alpha_mask: Any | None = None,
    focus_mask: Any | None = None,
) -> float:
    if crop_gray.shape[:2] != template_gray.shape[:2] or crop_gray.size == 0:
        return 0.0
    template_mask = _foreground_mask(
        cv2=cv2,
        np=np,
        gray=template_gray.astype("uint8"),
        alpha_mask=alpha_mask,
    )
    crop_mask = _foreground_mask(
        cv2=cv2,
        np=np,
        gray=crop_gray.astype("uint8"),
        alpha_mask=alpha_mask,
    )
    if focus_mask is not None:
        template_mask &= focus_mask
        crop_mask &= focus_mask
    template_count = int(template_mask.sum())
    if template_count <= 0:
        return 0.0
    kernel = np.ones((3, 3), dtype="uint8")
    template_dilated = cv2.dilate(template_mask.astype("uint8"), kernel) > 0
    crop_dilated = cv2.dilate(crop_mask.astype("uint8"), kernel) > 0
    intersection = int((template_dilated & crop_dilated).sum())
    union = int((template_dilated | crop_dilated).sum())
    mask_iou = float(intersection) / float(union) if union > 0 else 0.0
    diff = np.abs(crop_gray.astype("float32") - template_gray.astype("float32"))
    masked_similarity = 1.0 - min(1.0, float(diff[template_mask].mean()) / 255.0)
    return max(0.0, min(1.0, (0.60 * mask_iou) + (0.40 * masked_similarity)))


def _composite_visual_score(
    *,
    raw_correlation: float,
    edge_score: float,
    glyph_score: float,
    appearance_score: float | None,
    kind: VisualAssetKind,
) -> float:
    appearance_similarity = 0.50
    if appearance_score is not None:
        appearance_similarity = 1.0 - min(1.0, max(0.0, appearance_score) / 80.0)
    if kind == VisualAssetKind.PLUS:
        weights = (0.45, 0.25, 0.25, 0.05)
    else:
        weights = (0.35, 0.30, 0.30, 0.05)
    raw_weight, edge_weight, glyph_weight, appearance_weight = weights
    score = (
        raw_weight * max(0.0, min(1.0, raw_correlation))
        + edge_weight * max(0.0, min(1.0, edge_score))
        + glyph_weight * max(0.0, min(1.0, glyph_score))
        + appearance_weight * max(0.0, min(1.0, appearance_similarity))
    )
    return max(0.0, min(1.0, score))


def _select_shared_state_match(
    matches: list[VisualAssetMatch],
    *,
    ambiguity_margin: float = CHATGPT_MAC_STATE_AMBIGUITY_MARGIN,
) -> _StateSelection:
    best_by_state: dict[VisualGuiState, VisualAssetMatch] = {}
    for match in matches:
        if match.state is None:
            continue
        current = best_by_state.get(match.state)
        if current is None or match.visual_score > current.visual_score:
            best_by_state[match.state] = match
    ranked = sorted(best_by_state.values(), key=lambda match: match.visual_score, reverse=True)
    if not ranked:
        return _StateSelection(
            state=VisualGuiState.UNKNOWN,
            match=None,
            reason="no_state_match",
        )
    if len(ranked) == 1:
        selected = ranked[0]
        return _StateSelection(
            state=selected.state or VisualGuiState.UNKNOWN,
            match=selected,
            reason="selected_single_state_match",
        )
    top = ranked[0]
    second = ranked[1]
    score_gap = top.visual_score - second.visual_score
    if score_gap <= max(0.0, ambiguity_margin):
        appearance_selection = _select_send_state_by_appearance(top, second)
        if appearance_selection is not None:
            return appearance_selection
        return _StateSelection(
            state=VisualGuiState.AMBIGUOUS,
            match=top,
            ambiguous=True,
            reason=(
                "ambiguous_near_equal_state_matches:"
                f"{top.state.value if top.state else 'unknown'}={top.visual_score:.3f},"
                f"{second.state.value if second.state else 'unknown'}={second.visual_score:.3f}"
            ),
        )
    return _StateSelection(
        state=top.state or VisualGuiState.UNKNOWN,
        match=top,
        reason=(
            "selected_by_composite_score:"
            f"{top.state.value if top.state else 'unknown'}={top.visual_score:.3f},"
            f"{second.state.value if second.state else 'unknown'}={second.visual_score:.3f},"
            f"gap={score_gap:.3f}"
        ),
    )


def _select_send_state_by_appearance(
    first: VisualAssetMatch,
    second: VisualAssetMatch,
) -> _StateSelection | None:
    states = {first.state, second.state}
    if states != {VisualGuiState.IDLE, VisualGuiState.COMPOSER_HAS_TEXT}:
        return None
    if first.appearance_score is None or second.appearance_score is None:
        return None
    if _bbox_iou(first.bbox, second.bbox) < CHATGPT_MAC_SEND_BBOX_IOU_THRESHOLD:
        return None
    score_gap = abs(first.appearance_score - second.appearance_score)
    if score_gap < CHATGPT_MAC_APPEARANCE_SCORE_MARGIN:
        return None
    selected = first if first.appearance_score < second.appearance_score else second
    other = second if selected is first else first
    return _StateSelection(
        state=selected.state or VisualGuiState.UNKNOWN,
        match=selected,
        reason=(
            "selected_by_appearance_score:"
            f"{selected.state.value if selected.state else 'unknown'}="
            f"{selected.appearance_score:.3f},"
            f"{other.state.value if other.state else 'unknown'}="
            f"{other.appearance_score:.3f}"
        ),
    )


def _bbox_iou(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> float:
    ax1, ay1, aw, ah = first
    bx1, by1, bw, bh = second
    ax2 = ax1 + aw
    ay2 = ay1 + ah
    bx2 = bx1 + bw
    by2 = by1 + bh
    intersection_width = max(0, min(ax2, bx2) - max(ax1, bx1))
    intersection_height = max(0, min(ay2, by2) - max(ay1, by1))
    intersection = intersection_width * intersection_height
    union = aw * ah + bw * bh - intersection
    if union <= 0:
        return 0.0
    return intersection / union


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


def _visual_state_error(
    *,
    state_selection: _StateSelection,
    plus_match: VisualAssetMatch | None,
) -> str | None:
    if state_selection.ambiguous:
        return "Visual state is ambiguous; paste/submit is blocked."
    if state_selection.match or plus_match:
        return None
    return "No visual state assets matched."


def format_visual_state_detection(result: VisualStateDetection) -> str:
    lines = [
        "# Visual State Diagnostic",
        "",
        f"Selected app: {result.selected_app}",
        f"Selected asset profile: {result.asset_profile}",
        f"Selected window bounds: {result.window_bounds or 'unavailable'}",
        f"Safe region bounds: {result.safe_region_bounds or 'unavailable'}",
        f"Plus search region bounds: {result.plus_search_region_bounds or 'unavailable'}",
        f"Screenshot captured: {'yes' if result.screenshot_captured else 'no'}",
        f"Backend available: {'yes' if result.backend_available else 'no'}",
        f"Matched state: {result.matched_state.value}",
        f"State ambiguous: {'yes' if result.state_ambiguous else 'no'}",
        f"State selection reason: {result.state_selection_reason or 'unavailable'}",
        f"Matched asset path: {result.matched_asset_path or 'unavailable'}",
        f"Matched asset kind: {result.matched_asset_kind.value if result.matched_asset_kind else 'unavailable'}",
        (
            "Confidence: "
            + (f"{result.confidence:.3f}" if result.confidence is not None else "unavailable")
        ),
        f"Plus anchor found: {'yes' if result.plus_anchor_found else 'no'}",
        f"Plus anchor bbox: {result.plus_anchor_bbox or 'unavailable'}",
        (
            "Plus anchor confidence: "
            + (
                f"{result.plus_anchor_confidence:.3f}"
                if result.plus_anchor_confidence is not None
                else "unavailable"
            )
        ),
        "Composer offset dx/dy: unavailable"
        if result.plus_anchor_bbox is None
        else (
            "Composer offset dx/dy: "
            f"({result.computed_composer_click_point[0] - VisualRect(*result.plus_anchor_bbox).center[0]}, "
            f"{result.computed_composer_click_point[1] - VisualRect(*result.plus_anchor_bbox).center[1]})"
            if result.computed_composer_click_point is not None
            else "Composer offset dx/dy: unavailable"
        ),
        f"Computed composer click point: {result.computed_composer_click_point or 'unavailable'}",
        f"Composer click point safe: {'yes' if result.composer_click_point_safe else 'no'}",
        f"Screenshot path: {result.screenshot_path or 'not written'}",
        f"Annotated screenshot path: {result.annotated_screenshot_path or 'not written'}",
        f"Error: {result.error or 'none'}",
    ]
    if result.template_diagnostics:
        lines.extend(["", "## Per-Template Diagnostics"])
        for diagnostic in result.template_diagnostics:
            lines.extend(
                [
                    f"- Template: {diagnostic.template_path}",
                    f"  kind: {diagnostic.asset_kind.value}",
                    f"  state: {diagnostic.state.value if diagnostic.state else 'COMPOSER_ANCHOR'}",
                    f"  exists: {'yes' if diagnostic.template_exists else 'no'}",
                    f"  search region: {diagnostic.search_region_bounds or 'unavailable'}",
                    f"  original template size: {diagnostic.original_template_size or 'unavailable'}",
                    (
                        "  selected scale: "
                        + (
                            f"{diagnostic.selected_scale:.3f}"
                            if diagnostic.selected_scale is not None
                            else "unavailable"
                        )
                    ),
                    f"  scaled template size: {diagnostic.template_size or 'unavailable'}",
                    f"  best match bbox: {diagnostic.best_match_bbox or 'unavailable'}",
                    (
                        "  best match confidence: "
                        + (
                            f"{diagnostic.best_match_confidence:.3f}"
                            if diagnostic.best_match_confidence is not None
                            else "unavailable"
                        )
                    ),
                    (
                        "  appearance score: "
                        + (
                            f"{diagnostic.appearance_score:.3f}"
                            if diagnostic.appearance_score is not None
                            else "unavailable"
                        )
                    ),
                    (
                        "  edge score: "
                        + (
                            f"{diagnostic.edge_score:.3f}"
                            if diagnostic.edge_score is not None
                            else "unavailable"
                        )
                    ),
                    (
                        "  glyph score: "
                        + (
                            f"{diagnostic.glyph_score:.3f}"
                            if diagnostic.glyph_score is not None
                            else "unavailable"
                        )
                    ),
                    (
                        "  composite score: "
                        + (
                            f"{diagnostic.composite_score:.3f}"
                            if diagnostic.composite_score is not None
                            else "unavailable"
                        )
                    ),
                    (
                        "  score gap to next best: "
                        + (
                            f"{diagnostic.score_gap_to_next_best:.3f}"
                            if diagnostic.score_gap_to_next_best is not None
                            else "unavailable"
                        )
                    ),
                    f"  configured threshold: {diagnostic.configured_threshold if diagnostic.configured_threshold is not None else diagnostic.threshold:.3f}",
                    f"  effective threshold: {diagnostic.effective_threshold if diagnostic.effective_threshold is not None else diagnostic.threshold:.3f}",
                    f"  threshold cap applied: {'yes' if diagnostic.threshold_cap_applied else 'no'}",
                    f"  accepted: {'yes' if diagnostic.accepted else 'no'}",
                    f"  rejection reason: {diagnostic.rejection_reason or 'none'}",
                ]
            )
    plus_diagnostics = [
        item for item in result.template_diagnostics if item.asset_kind == VisualAssetKind.PLUS
    ]
    if plus_diagnostics and not result.plus_anchor_found:
        best_plus = max(
            plus_diagnostics,
            key=lambda item: (
                item.composite_score
                if item.composite_score is not None
                else item.best_match_confidence
                if item.best_match_confidence is not None
                else -1.0
            ),
        )
        lines.extend(
            [
                "",
                "## Plus-Anchor Recommendation",
                (
                    "No plus-anchor candidate passed the effective threshold; "
                    f"best={Path(best_plus.template_path).name} "
                    f"raw={best_plus.best_match_confidence if best_plus.best_match_confidence is not None else 'unavailable'} "
                    f"composite={best_plus.composite_score if best_plus.composite_score is not None else 'unavailable'} "
                    f"configured={best_plus.configured_threshold if best_plus.configured_threshold is not None else best_plus.threshold} "
                    f"effective={best_plus.effective_threshold if best_plus.effective_threshold is not None else best_plus.threshold}. "
                    "Refresh the plus asset or adjust the profile search region if the UI button is visibly present."
                ),
            ]
        )
    return "\n".join(lines)


def _emit(
    callback: Callable[[str, dict[str, object]], None] | None,
    event_type: str,
    **metadata: object,
) -> None:
    if callback is not None:
        callback(event_type, metadata)


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
    matches: tuple[VisualAssetMatch, ...],
    click_point: tuple[int, int] | None,
    window_bounds: tuple[int, int, int, int],
) -> None:
    try:
        from PIL import ImageDraw

        annotated = image.copy()
        draw = ImageDraw.Draw(annotated)
        wx, wy, _, _ = window_bounds

        def local_rect(rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
            return (rect[0] - wx, rect[1] - wy, rect[0] - wx + rect[2], rect[1] - wy + rect[3])

        draw.rectangle(local_rect(search_region.as_tuple()), outline="yellow", width=3)
        for match in matches:
            color = {
                VisualAssetKind.PLUS: "cyan",
                VisualAssetKind.SEND_DISABLED: "green",
                VisualAssetKind.SEND: "blue",
                VisualAssetKind.STOP: "red",
            }[match.asset_kind]
            draw.rectangle(local_rect(match.bbox), outline=color, width=3)
        if click_point is not None:
            x = click_point[0] - wx
            y = click_point[1] - wy
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), outline="magenta", width=3)
        annotated.save(path)
    except Exception:
        return
