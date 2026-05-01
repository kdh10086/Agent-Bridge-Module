from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from agent_bridge.gui.macos_apps import ManualStageTarget
from agent_bridge.gui.visual_text_recognition import (
    OCRRuntimeStatus,
    PytesseractTextRecognizer,
    text_matches,
)
from agent_bridge.gui.visual_thresholds import (
    effective_visual_threshold,
    visual_threshold_cap_applied,
)


@dataclass(frozen=True)
class VisualRect:
    x: int
    y: int
    width: int
    height: int

    @property
    def center(self) -> tuple[int, int]:
        return (int(self.x + self.width / 2), int(self.y + self.height / 2))

    def contains_point(self, point: tuple[int, int]) -> bool:
        return self.x <= point[0] <= self.x + self.width and self.y <= point[1] <= self.y + self.height

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.width, self.height)


@dataclass(frozen=True)
class VisualCandidate:
    name: str
    bbox: VisualRect
    confidence: float


@dataclass(frozen=True)
class VisualDetectionResult:
    backend_available: bool
    screenshot_captured: bool
    window_bounds: tuple[int, int, int, int] | None
    safe_region_bounds: tuple[int, int, int, int] | None
    placeholder_detection_backend_available: bool = False
    placeholder_detection_error: str | None = None
    placeholder_detection_reason: str | None = None
    placeholder_target_text: str | None = None
    placeholder_match_text: str | None = None
    placeholder_ocr_text_path: str | None = None
    placeholder_ocr_confidence: float | None = None
    placeholder_search_region_bounds: tuple[int, int, int, int] | None = None
    ocr_backend: str = "pytesseract"
    pytesseract_package_available: bool | None = None
    tesseract_executable_available: bool | None = None
    ocr_languages: tuple[str, ...] = ()
    english_ocr_available: bool | None = None
    korean_ocr_available: bool | None = None
    plus_button_found: bool = False
    plus_button_bbox: tuple[int, int, int, int] | None = None
    plus_button_confidence: float | None = None
    plus_template_path: str | None = None
    plus_template_size: tuple[int, int] | None = None
    plus_best_match_bbox: tuple[int, int, int, int] | None = None
    plus_best_match_confidence: float | None = None
    plus_confidence_threshold: float | None = None
    plus_configured_confidence_threshold: float | None = None
    plus_effective_confidence_threshold: float | None = None
    plus_threshold_cap_applied: bool | None = None
    plus_multiscale_enabled: bool | None = None
    plus_search_region_bounds: tuple[int, int, int, int] | None = None
    plus_match_error: str | None = None
    placeholder_found: bool = False
    placeholder_bbox: tuple[int, int, int, int] | None = None
    placeholder_confidence: float | None = None
    selected_strategy: str = "none"
    computed_click_point: tuple[int, int] | None = None
    click_point_safe: bool = False
    fallback_would_be_used: bool = False
    debug_image_path: str | None = None
    annotated_image_path: str | None = None
    error: str | None = None


class ScreenshotProvider(Protocol):
    def __call__(self, region: tuple[int, int, int, int]) -> Any:
        raise NotImplementedError


@dataclass(frozen=True)
class PlaceholderDetection:
    backend_available: bool
    candidate: VisualCandidate | None = None
    target_text: str | None = None
    match_text: str | None = None
    extracted_text: str = ""
    ocr_text_path: str | None = None
    confidence: float | None = None
    search_region_bounds: tuple[int, int, int, int] | None = None
    reason: str | None = None
    runtime_status: OCRRuntimeStatus | None = None
    error: str | None = None


@dataclass(frozen=True)
class PlusTemplateMatch:
    candidate: VisualCandidate | None
    best_candidate: VisualCandidate | None = None
    template_path: str | None = None
    template_size: tuple[int, int] | None = None
    confidence_threshold: float | None = None
    configured_confidence_threshold: float | None = None
    effective_confidence_threshold: float | None = None
    threshold_cap_applied: bool = False
    multiscale_enabled: bool | None = None
    search_region_bounds: tuple[int, int, int, int] | None = None
    error: str | None = None


@dataclass(frozen=True)
class VisualMarkerPresenceResult:
    marker_text: str
    marker_detection_backend: str
    marker_detection_available: bool
    marker_found: bool | None
    marker_confidence: float | None
    window_bounds: tuple[int, int, int, int] | None
    search_region_bounds: tuple[int, int, int, int] | None
    screenshot_captured: bool
    screenshot_path: str | None = None
    annotated_screenshot_path: str | None = None
    ocr_text_path: str | None = None
    marker_match_text: str | None = None
    detection_reason: str | None = None
    pytesseract_package_available: bool | None = None
    tesseract_executable_available: bool | None = None
    ocr_languages: tuple[str, ...] = ()
    english_ocr_available: bool | None = None
    korean_ocr_available: bool | None = None
    error: str | None = None


def safe_search_region(
    window_bounds: tuple[int, int, int, int],
    *,
    lower_height_ratio: float = 0.35,
    min_x_ratio: float = 0.18,
    max_x_ratio: float = 0.88,
) -> VisualRect:
    x, y, width, height = window_bounds
    safe_x = int(x + width * min_x_ratio)
    safe_width = int(width * (max_x_ratio - min_x_ratio))
    safe_y = int(y + height * (1.0 - lower_height_ratio))
    safe_height = int(height * lower_height_ratio)
    return VisualRect(safe_x, safe_y, safe_width, safe_height)


def plus_search_region(window_bounds: tuple[int, int, int, int]) -> VisualRect:
    return safe_search_region(
        window_bounds,
        lower_height_ratio=0.4,
        min_x_ratio=0.04,
        max_x_ratio=0.92,
    )


def composer_text_search_region(window_bounds: tuple[int, int, int, int]) -> VisualRect:
    return safe_search_region(
        window_bounds,
        lower_height_ratio=0.24,
        min_x_ratio=0.04,
        max_x_ratio=0.92,
    )


def compute_plus_anchor_point(
    plus_bbox: VisualRect,
    target: ManualStageTarget,
) -> tuple[int, int]:
    center_x, center_y = plus_bbox.center
    return (
        int(center_x + target.plus_anchor_x_offset),
        int(center_y - target.plus_anchor_y_offset),
    )


def point_is_safe(
    point: tuple[int, int],
    *,
    window_bounds: tuple[int, int, int, int],
    safe_region: VisualRect,
    avoid_rect: VisualRect | None = None,
) -> bool:
    window = VisualRect(*window_bounds)
    if not window.contains_point(point):
        return False
    if not safe_region.contains_point(point):
        return False
    if avoid_rect and avoid_rect.contains_point(point):
        return False
    return True


def select_visual_anchor(
    *,
    target: ManualStageTarget,
    window_bounds: tuple[int, int, int, int],
    plus_candidate: VisualCandidate | None,
    placeholder_candidate: VisualCandidate | None,
    plus_match: PlusTemplateMatch | None = None,
    placeholder_detection_backend_available: bool = False,
    placeholder_detection_error: str | None = None,
    placeholder_detection_reason: str | None = None,
    placeholder_target_text: str | None = None,
    placeholder_match_text: str | None = None,
    placeholder_ocr_text_path: str | None = None,
    placeholder_ocr_confidence: float | None = None,
    placeholder_search_region_bounds: tuple[int, int, int, int] | None = None,
    ocr_status: OCRRuntimeStatus | None = None,
) -> VisualDetectionResult:
    safe_region = safe_search_region(window_bounds)
    plus_safe_region = plus_search_region(window_bounds)
    plus_point: tuple[int, int] | None = None
    plus_safe = False
    if plus_candidate is not None:
        plus_point = compute_plus_anchor_point(plus_candidate.bbox, target)
        plus_safe = point_is_safe(
            plus_point,
            window_bounds=window_bounds,
            safe_region=plus_safe_region,
            avoid_rect=plus_candidate.bbox,
        )
        if plus_safe:
            return VisualDetectionResult(
                backend_available=True,
                screenshot_captured=True,
                window_bounds=window_bounds,
                safe_region_bounds=plus_safe_region.as_tuple(),
                placeholder_detection_backend_available=placeholder_detection_backend_available,
                placeholder_detection_error=placeholder_detection_error,
                placeholder_detection_reason=placeholder_detection_reason,
                placeholder_target_text=placeholder_target_text,
                placeholder_match_text=placeholder_match_text,
                placeholder_ocr_text_path=placeholder_ocr_text_path,
                placeholder_ocr_confidence=placeholder_ocr_confidence,
                placeholder_search_region_bounds=placeholder_search_region_bounds,
                **_visual_ocr_status_fields(ocr_status),
                plus_button_found=True,
                plus_button_bbox=plus_candidate.bbox.as_tuple(),
                plus_button_confidence=plus_candidate.confidence,
                **_plus_match_fields(plus_match),
                placeholder_found=placeholder_candidate is not None,
                placeholder_bbox=(
                    placeholder_candidate.bbox.as_tuple() if placeholder_candidate else None
                ),
                placeholder_confidence=(
                    placeholder_candidate.confidence if placeholder_candidate else None
                ),
                selected_strategy="visual_plus_anchor",
                computed_click_point=plus_point,
                click_point_safe=True,
            )

    placeholder_point: tuple[int, int] | None = None
    placeholder_safe = False
    if placeholder_candidate is not None:
        placeholder_point = placeholder_candidate.bbox.center
        placeholder_safe = point_is_safe(
            placeholder_point,
            window_bounds=window_bounds,
            safe_region=safe_region,
        )
        if placeholder_safe:
            return VisualDetectionResult(
                backend_available=True,
                screenshot_captured=True,
                window_bounds=window_bounds,
                safe_region_bounds=safe_region.as_tuple(),
                placeholder_detection_backend_available=placeholder_detection_backend_available,
                placeholder_detection_error=placeholder_detection_error,
                placeholder_detection_reason=placeholder_detection_reason,
                placeholder_target_text=placeholder_target_text,
                placeholder_match_text=placeholder_match_text,
                placeholder_ocr_text_path=placeholder_ocr_text_path,
                placeholder_ocr_confidence=placeholder_ocr_confidence,
                placeholder_search_region_bounds=placeholder_search_region_bounds,
                **_visual_ocr_status_fields(ocr_status),
                plus_button_found=plus_candidate is not None,
                plus_button_bbox=plus_candidate.bbox.as_tuple() if plus_candidate else None,
                plus_button_confidence=plus_candidate.confidence if plus_candidate else None,
                **_plus_match_fields(plus_match),
                placeholder_found=True,
                placeholder_bbox=placeholder_candidate.bbox.as_tuple(),
                placeholder_confidence=placeholder_candidate.confidence,
                selected_strategy="visual_placeholder_anchor",
                computed_click_point=placeholder_point,
                click_point_safe=True,
            )

    return VisualDetectionResult(
        backend_available=True,
        screenshot_captured=True,
        window_bounds=window_bounds,
        safe_region_bounds=safe_region.as_tuple(),
        placeholder_detection_backend_available=placeholder_detection_backend_available,
        placeholder_detection_error=placeholder_detection_error,
        placeholder_detection_reason=placeholder_detection_reason,
        placeholder_target_text=placeholder_target_text,
        placeholder_match_text=placeholder_match_text,
        placeholder_ocr_text_path=placeholder_ocr_text_path,
        placeholder_ocr_confidence=placeholder_ocr_confidence,
        placeholder_search_region_bounds=placeholder_search_region_bounds,
        **_visual_ocr_status_fields(ocr_status),
        plus_button_found=plus_candidate is not None,
        plus_button_bbox=plus_candidate.bbox.as_tuple() if plus_candidate else None,
        plus_button_confidence=plus_candidate.confidence if plus_candidate else None,
        **_plus_match_fields(plus_match),
        placeholder_found=placeholder_candidate is not None,
        placeholder_bbox=placeholder_candidate.bbox.as_tuple() if placeholder_candidate else None,
        placeholder_confidence=placeholder_candidate.confidence if placeholder_candidate else None,
        selected_strategy="none",
        computed_click_point=plus_point or placeholder_point,
        click_point_safe=plus_safe or placeholder_safe,
        error=f"No safe visual composer anchor was detected for {target.app_name}.",
    )


@dataclass
class CodexVisualDetector:
    screenshot_provider: ScreenshotProvider | None = None
    template_path: Path | None = None
    marker_ocr_reader: Callable[[Any], str] | None = None
    text_recognizer: PytesseractTextRecognizer | None = None

    def detect(
        self,
        *,
        target: ManualStageTarget,
        window_bounds: tuple[int, int, int, int] | None,
        logs_dir: Path | None = None,
        write_debug: bool = False,
    ) -> VisualDetectionResult:
        if window_bounds is None:
            return VisualDetectionResult(
                backend_available=False,
                screenshot_captured=False,
                window_bounds=None,
                safe_region_bounds=None,
                error="Codex window bounds were unavailable.",
            )
        screenshot = self._capture_screenshot(window_bounds)
        if isinstance(screenshot, str):
            return VisualDetectionResult(
                backend_available=False,
                screenshot_captured=False,
                window_bounds=window_bounds,
                safe_region_bounds=safe_search_region(window_bounds).as_tuple(),
                error=screenshot,
            )

        debug_path: str | None = None
        annotated_path: str | None = None
        window_bounded_annotated_path: Path | None = None
        if write_debug and logs_dir is not None:
            logs_dir.mkdir(parents=True, exist_ok=True)
            debug_path = str(logs_dir / "codex_visual_detection.png")
            annotated_path = str(logs_dir / "codex_visual_detection_annotated.png")
            window_bounded_annotated_path = logs_dir / "codex_window_bounded_detection_annotated.png"
            self._save_image_if_possible(screenshot, Path(debug_path))
            self._save_image_if_possible(screenshot, logs_dir / "codex_window_bounded_detection.png")

        plus_match = self._match_plus_template(screenshot, window_bounds, target)
        plus_candidate = plus_match.candidate
        if target.visual_text_recognition_enabled:
            placeholder = self._detect_placeholder_ocr(
                screenshot,
                window_bounds,
                target.visual_text_recognition_placeholder_text
                or target.composer_placeholder_text,
                logs_dir=logs_dir if write_debug else None,
            )
        else:
            placeholder = PlaceholderDetection(
                backend_available=False,
                target_text=target.visual_text_recognition_placeholder_text
                or target.composer_placeholder_text,
                search_region_bounds=composer_text_search_region(window_bounds).as_tuple(),
                reason="Visual text recognition is disabled in config.",
                error="Visual text recognition is disabled in config.",
            )
        result = select_visual_anchor(
            target=target,
            window_bounds=window_bounds,
            plus_candidate=plus_candidate,
            plus_match=plus_match,
            placeholder_candidate=placeholder.candidate,
            placeholder_detection_backend_available=placeholder.backend_available,
            placeholder_detection_error=placeholder.error,
            placeholder_detection_reason=placeholder.reason,
            placeholder_target_text=placeholder.target_text,
            placeholder_match_text=placeholder.match_text,
            placeholder_ocr_text_path=placeholder.ocr_text_path,
            placeholder_ocr_confidence=placeholder.confidence,
            placeholder_search_region_bounds=placeholder.search_region_bounds,
            ocr_status=placeholder.runtime_status,
        )
        if write_debug and annotated_path is not None:
            self._save_visual_annotation_if_possible(
                screenshot,
                Path(annotated_path),
                window_bounds=window_bounds,
                placeholder_search_region=(
                    VisualRect(*placeholder.search_region_bounds)
                    if placeholder.search_region_bounds
                    else None
                ),
                plus_candidate=plus_candidate or plus_match.best_candidate,
                click_point=result.computed_click_point,
            )
            if window_bounded_annotated_path is not None:
                self._save_visual_annotation_if_possible(
                    screenshot,
                    window_bounded_annotated_path,
                    window_bounds=window_bounds,
                    placeholder_search_region=(
                        VisualRect(*placeholder.search_region_bounds)
                        if placeholder.search_region_bounds
                        else None
                    ),
                    plus_candidate=plus_candidate or plus_match.best_candidate,
                    click_point=result.computed_click_point,
                )
        return VisualDetectionResult(
            **{
                **result.__dict__,
                "debug_image_path": debug_path,
                "annotated_image_path": annotated_path,
                "error": result.error
                or (
                    None
                    if plus_candidate is not None
                    else "Screenshot captured, but no plus-button template match was available."
                ),
            }
        )

    def _capture_screenshot(self, window_bounds: tuple[int, int, int, int]) -> Any | str:
        if self.screenshot_provider is not None:
            try:
                return self.screenshot_provider(window_bounds)
            except Exception as error:  # pragma: no cover - defensive runtime path
                return f"Screenshot provider failed: {error}"
        try:
            import pyautogui  # type: ignore[import-not-found]
        except Exception as error:
            return f"PyAutoGUI screenshot backend unavailable: {error}"
        try:
            return pyautogui.screenshot(region=window_bounds)
        except Exception as error:  # pragma: no cover - depends on host GUI permissions
            return f"Screenshot capture failed: {error}"

    def _match_plus_template(
        self,
        screenshot: Any,
        window_bounds: tuple[int, int, int, int],
        target: ManualStageTarget,
    ) -> PlusTemplateMatch:
        template_paths = tuple(Path(path) for path in target.visual_plus_templates)
        if not template_paths and self.template_path is not None:
            template_paths = (self.template_path,)
        existing_template_paths = tuple(path for path in template_paths if path.exists())
        configured_threshold = float(target.visual_plus_confidence_threshold)
        threshold = effective_visual_threshold(configured_threshold)
        cap_applied = visual_threshold_cap_applied(configured_threshold)
        search_region = plus_search_region(window_bounds)
        if not existing_template_paths:
            return PlusTemplateMatch(
                candidate=None,
                confidence_threshold=threshold,
                configured_confidence_threshold=configured_threshold,
                effective_confidence_threshold=threshold,
                threshold_cap_applied=cap_applied,
                multiscale_enabled=target.visual_plus_multiscale_enabled,
                search_region_bounds=search_region.as_tuple(),
                error=f"No plus-button templates exist for {target.app_name}.",
            )
        try:
            import cv2  # type: ignore[import-not-found]
            import numpy as np  # type: ignore[import-not-found]
            from PIL import Image  # type: ignore[import-not-found]
        except Exception as error:
            return PlusTemplateMatch(
                candidate=None,
                confidence_threshold=threshold,
                configured_confidence_threshold=configured_threshold,
                effective_confidence_threshold=threshold,
                threshold_cap_applied=cap_applied,
                multiscale_enabled=target.visual_plus_multiscale_enabled,
                search_region_bounds=search_region.as_tuple(),
                error=f"OpenCV/Numpy/Pillow template matching unavailable: {error}",
            )

        try:
            image = np.array(screenshot if isinstance(screenshot, Image.Image) else Image.open(screenshot))
            if image.ndim == 3 and image.shape[2] == 4:
                image = cv2.cvtColor(image, cv2.COLOR_RGBA2GRAY)
            elif image.ndim == 3:
                image = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
            wx, wy, _, _ = window_bounds
            sx = search_region.x - wx
            sy = search_region.y - wy
            search = image[sy : sy + search_region.height, sx : sx + search_region.width]
            if search.size == 0:
                return PlusTemplateMatch(
                    candidate=None,
                    confidence_threshold=threshold,
                    configured_confidence_threshold=configured_threshold,
                    effective_confidence_threshold=threshold,
                    threshold_cap_applied=cap_applied,
                    multiscale_enabled=target.visual_plus_multiscale_enabled,
                    search_region_bounds=search_region.as_tuple(),
                    error=f"{target.app_name} plus-button search region was empty.",
                )

            best_candidate: VisualCandidate | None = None
            best_template_path: str | None = None
            best_template_size: tuple[int, int] | None = None
            scales = (
                (0.70, 0.80, 0.90, 1.00, 1.10, 1.20, 1.30)
                if target.visual_plus_multiscale_enabled
                else (1.00,)
            )
            for template_path in existing_template_paths:
                template = cv2.imread(str(template_path), cv2.IMREAD_UNCHANGED)
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
                    candidate = VisualCandidate(
                        name=f"plus_button_template:{template_path}",
                        bbox=VisualRect(
                            search_region.x + max_location[0],
                            search_region.y + max_location[1],
                            width,
                            height,
                        ),
                        confidence=float(max_value),
                    )
                    if best_candidate is None or candidate.confidence > best_candidate.confidence:
                        best_candidate = candidate
                        best_template_path = str(template_path)
                        best_template_size = (width, height)
            accepted = best_candidate if best_candidate and best_candidate.confidence >= threshold else None
            return PlusTemplateMatch(
                candidate=accepted,
                best_candidate=best_candidate,
                template_path=best_template_path,
                template_size=best_template_size,
                confidence_threshold=threshold,
                configured_confidence_threshold=configured_threshold,
                effective_confidence_threshold=threshold,
                threshold_cap_applied=cap_applied,
                multiscale_enabled=target.visual_plus_multiscale_enabled,
                search_region_bounds=search_region.as_tuple(),
            )
        except Exception as error:
            return PlusTemplateMatch(
                candidate=None,
                confidence_threshold=threshold,
                configured_confidence_threshold=configured_threshold,
                effective_confidence_threshold=threshold,
                threshold_cap_applied=cap_applied,
                multiscale_enabled=target.visual_plus_multiscale_enabled,
                search_region_bounds=search_region.as_tuple(),
                error=f"{target.app_name} plus-button template matching failed: {error}",
            )

    def _detect_placeholder_ocr(
        self,
        screenshot: Any,
        window_bounds: tuple[int, int, int, int],
        placeholder_text: str | None,
        logs_dir: Path | None = None,
    ) -> PlaceholderDetection:
        if not placeholder_text:
            return PlaceholderDetection(
                backend_available=False,
                error="No Codex placeholder text is configured.",
            )
        safe_region = composer_text_search_region(window_bounds)
        cropped = self._crop_to_search_region(screenshot, window_bounds, safe_region)
        if isinstance(cropped, str):
            return PlaceholderDetection(
                backend_available=False,
                target_text=placeholder_text,
                search_region_bounds=safe_region.as_tuple(),
                reason=cropped,
                error=cropped,
            )

        recognizer = self.text_recognizer or PytesseractTextRecognizer()
        result = recognizer.detect_text(
            cropped,
            target_text=placeholder_text,
            languages=("kor", "eng"),
            required_language="kor",
            fuzzy=False,
        )
        found = result.found
        confidence = result.confidence
        matched_text = result.matched_text
        reason = result.reason
        if result.backend_available and found is not True and result.extracted_text:
            found, confidence, matched_text, reason = _match_placeholder_text(
                placeholder_text,
                result.extracted_text,
                fallback_confidence=confidence,
                fallback_reason=reason,
            )
        ocr_text_path: str | None = None
        if logs_dir is not None:
            logs_dir.mkdir(parents=True, exist_ok=True)
            ocr_text_path = str(logs_dir / "codex_placeholder_ocr.txt")
            text_payload = result.extracted_text or result.error or result.reason or ""
            Path(ocr_text_path).write_text(text_payload, encoding="utf-8")
        candidate = None
        if found is True:
            candidate = VisualCandidate(
                "placeholder_ocr",
                VisualRect(safe_region.x, safe_region.y, safe_region.width, safe_region.height),
                confidence or 1.0,
            )
        return PlaceholderDetection(
            backend_available=result.backend_available and found is not None,
            candidate=candidate,
            target_text=placeholder_text,
            match_text=matched_text,
            extracted_text=result.extracted_text,
            ocr_text_path=ocr_text_path,
            confidence=confidence,
            search_region_bounds=safe_region.as_tuple(),
            reason=reason,
            runtime_status=result.runtime_status,
            error=result.error,
        )

    def detect_marker_presence(
        self,
        *,
        marker_text: str,
        window_bounds: tuple[int, int, int, int] | None,
        logs_dir: Path | None = None,
        write_debug: bool = False,
    ) -> VisualMarkerPresenceResult:
        if window_bounds is None:
            return VisualMarkerPresenceResult(
                marker_text=marker_text,
                marker_detection_backend="unavailable",
                marker_detection_available=False,
                marker_found=None,
                marker_confidence=None,
                window_bounds=None,
                search_region_bounds=None,
                screenshot_captured=False,
                error="Codex window bounds were unavailable.",
            )
        search_region = composer_text_search_region(window_bounds)
        screenshot = self._capture_screenshot(window_bounds)
        if isinstance(screenshot, str):
            return VisualMarkerPresenceResult(
                marker_text=marker_text,
                marker_detection_backend="unavailable",
                marker_detection_available=False,
                marker_found=None,
                marker_confidence=None,
                window_bounds=window_bounds,
                search_region_bounds=search_region.as_tuple(),
                screenshot_captured=False,
                error=screenshot,
            )

        screenshot_path: str | None = None
        annotated_path: str | None = None
        if write_debug and logs_dir is not None:
            logs_dir.mkdir(parents=True, exist_ok=True)
            screenshot_path = str(logs_dir / "codex_marker_presence.png")
            annotated_path = str(logs_dir / "codex_marker_presence_annotated.png")
            self._save_image_if_possible(screenshot, Path(screenshot_path))
            self._save_marker_annotation_if_possible(
                screenshot,
                Path(annotated_path),
                window_bounds=window_bounds,
                search_region=search_region,
            )

        cropped = self._crop_to_search_region(screenshot, window_bounds, search_region)
        if isinstance(cropped, str):
            return VisualMarkerPresenceResult(
                marker_text=marker_text,
                marker_detection_backend="unavailable",
                marker_detection_available=False,
                marker_found=None,
                marker_confidence=None,
                window_bounds=window_bounds,
                search_region_bounds=search_region.as_tuple(),
                screenshot_captured=True,
                screenshot_path=screenshot_path,
                annotated_screenshot_path=annotated_path,
                error=cropped,
            )

        ocr_text_path: str | None = None
        if self.marker_ocr_reader is not None:
            text = self.marker_ocr_reader(cropped)
            if write_debug and logs_dir is not None:
                ocr_text_path = str(logs_dir / "codex_marker_presence_ocr.txt")
                Path(ocr_text_path).write_text(text, encoding="utf-8")
            found, confidence = text_matches(marker_text, text, fuzzy=True)
            return VisualMarkerPresenceResult(
                marker_text=marker_text,
                marker_detection_backend="ocr",
                marker_detection_available=True,
                marker_found=found,
                marker_confidence=confidence,
                window_bounds=window_bounds,
                search_region_bounds=search_region.as_tuple(),
                screenshot_captured=True,
                screenshot_path=screenshot_path,
                annotated_screenshot_path=annotated_path,
                ocr_text_path=ocr_text_path,
                marker_match_text=marker_text if found else None,
                detection_reason="target_text_detected" if found else "target_text_not_detected",
            )

        recognizer = self.text_recognizer or PytesseractTextRecognizer()
        result = recognizer.detect_text(
            cropped,
            target_text=marker_text,
            languages=("eng",),
            required_language="eng",
            fuzzy=True,
        )
        if write_debug and logs_dir is not None:
            ocr_text_path = str(logs_dir / "codex_marker_presence_ocr.txt")
            text_payload = result.extracted_text or result.error or result.reason or ""
            Path(ocr_text_path).write_text(text_payload, encoding="utf-8")
        status_fields = _marker_ocr_status_fields(result.runtime_status)
        return VisualMarkerPresenceResult(
            marker_text=marker_text,
            marker_detection_backend=result.backend,
            marker_detection_available=result.backend_available and result.found is not None,
            marker_found=result.found,
            marker_confidence=result.confidence,
            window_bounds=window_bounds,
            search_region_bounds=search_region.as_tuple(),
            screenshot_captured=True,
            screenshot_path=screenshot_path,
            annotated_screenshot_path=annotated_path,
            ocr_text_path=ocr_text_path,
            marker_match_text=result.matched_text,
            detection_reason=result.reason,
            error=result.error,
            **status_fields,
        )

    def _crop_to_search_region(
        self,
        screenshot: Any,
        window_bounds: tuple[int, int, int, int],
        search_region: VisualRect,
    ) -> Any | str:
        try:
            from PIL import Image  # type: ignore[import-not-found]
        except Exception as error:
            return f"Pillow unavailable for marker crop: {error}"
        try:
            image = screenshot if isinstance(screenshot, Image.Image) else Image.open(screenshot)
            wx, wy, _, _ = window_bounds
            crop_box = (
                search_region.x - wx,
                search_region.y - wy,
                search_region.x - wx + search_region.width,
                search_region.y - wy + search_region.height,
            )
            return image.crop(crop_box)
        except Exception as error:
            return f"Marker search crop failed: {error}"

    def _save_marker_annotation_if_possible(
        self,
        image: Any,
        path: Path,
        *,
        window_bounds: tuple[int, int, int, int],
        search_region: VisualRect,
    ) -> None:
        try:
            from PIL import Image, ImageDraw  # type: ignore[import-not-found]

            screenshot = image if isinstance(image, Image.Image) else Image.open(image)
            annotated = screenshot.copy()
            draw = ImageDraw.Draw(annotated)
            wx, wy, _, _ = window_bounds
            rect = (
                search_region.x - wx,
                search_region.y - wy,
                search_region.x - wx + search_region.width,
                search_region.y - wy + search_region.height,
            )
            draw.rectangle(rect, outline="red", width=3)
            annotated.save(path)
        except Exception:
            self._save_image_if_possible(image, path)

    def _save_visual_annotation_if_possible(
        self,
        image: Any,
        path: Path,
        *,
        window_bounds: tuple[int, int, int, int],
        placeholder_search_region: VisualRect | None,
        plus_candidate: VisualCandidate | None,
        click_point: tuple[int, int] | None,
    ) -> None:
        try:
            from PIL import Image, ImageDraw  # type: ignore[import-not-found]

            screenshot = image if isinstance(image, Image.Image) else Image.open(image)
            annotated = screenshot.copy()
            draw = ImageDraw.Draw(annotated)
            wx, wy, _, _ = window_bounds
            if placeholder_search_region is not None:
                rect = (
                    placeholder_search_region.x - wx,
                    placeholder_search_region.y - wy,
                    placeholder_search_region.x - wx + placeholder_search_region.width,
                    placeholder_search_region.y - wy + placeholder_search_region.height,
                )
                draw.rectangle(rect, outline="red", width=3)
            if plus_candidate is not None:
                plus = plus_candidate.bbox
                rect = (
                    plus.x - wx,
                    plus.y - wy,
                    plus.x - wx + plus.width,
                    plus.y - wy + plus.height,
                )
                draw.rectangle(rect, outline="blue", width=3)
            if click_point is not None:
                x, y = click_point
                cx = x - wx
                cy = y - wy
                draw.ellipse((cx - 6, cy - 6, cx + 6, cy + 6), outline="green", width=3)
            annotated.save(path)
        except Exception:
            self._save_image_if_possible(image, path)

    def _save_image_if_possible(self, image: Any, path: Path) -> None:
        try:
            image.save(path)
        except Exception:
            path.write_text("Visual screenshot was captured but could not be saved as an image.\n")


def _match_placeholder_text(
    placeholder_text: str,
    detected_text: str,
    *,
    fallback_confidence: float | None,
    fallback_reason: str | None,
) -> tuple[bool | None, float | None, str | None, str | None]:
    exact_found, exact_confidence = text_matches(
        placeholder_text,
        detected_text,
        fuzzy=True,
        threshold=0.88,
    )
    if exact_found:
        return True, exact_confidence, placeholder_text, "target_text_detected"
    for phrase in _placeholder_partial_phrases(placeholder_text):
        found, confidence = text_matches(phrase, detected_text, fuzzy=True, threshold=0.76)
        if found:
            return True, confidence, phrase, "partial_placeholder_text_detected"
    return False, fallback_confidence, None, fallback_reason or "target_text_not_detected"


def _placeholder_partial_phrases(placeholder_text: str) -> tuple[str, ...]:
    phrases = ["후속 변경", "부탁하세요"]
    return tuple(phrase for phrase in phrases if phrase and phrase in placeholder_text)


def _visual_ocr_status_fields(status: OCRRuntimeStatus | None) -> dict[str, object]:
    if status is None:
        return {}
    return {
        "ocr_backend": status.backend,
        "pytesseract_package_available": status.pytesseract_package_available,
        "tesseract_executable_available": status.tesseract_executable_available,
        "ocr_languages": status.available_languages,
        "english_ocr_available": status.english_available,
        "korean_ocr_available": status.korean_available,
    }


def _plus_match_fields(match: PlusTemplateMatch | None) -> dict[str, object]:
    if match is None:
        return {}
    return {
        "plus_template_path": match.template_path,
        "plus_template_size": match.template_size,
        "plus_best_match_bbox": (
            match.best_candidate.bbox.as_tuple() if match.best_candidate else None
        ),
        "plus_best_match_confidence": (
            match.best_candidate.confidence if match.best_candidate else None
        ),
        "plus_confidence_threshold": match.confidence_threshold,
        "plus_configured_confidence_threshold": match.configured_confidence_threshold,
        "plus_effective_confidence_threshold": match.effective_confidence_threshold,
        "plus_threshold_cap_applied": match.threshold_cap_applied,
        "plus_multiscale_enabled": match.multiscale_enabled,
        "plus_search_region_bounds": match.search_region_bounds,
        "plus_match_error": match.error,
    }


def _marker_ocr_status_fields(status: OCRRuntimeStatus | None) -> dict[str, object]:
    if status is None:
        return {}
    return {
        "pytesseract_package_available": status.pytesseract_package_available,
        "tesseract_executable_available": status.tesseract_executable_available,
        "ocr_languages": status.available_languages,
        "english_ocr_available": status.english_available,
        "korean_ocr_available": status.korean_available,
    }
