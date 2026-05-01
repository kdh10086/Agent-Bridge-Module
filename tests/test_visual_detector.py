from __future__ import annotations

from pathlib import Path

from agent_bridge.gui.macos_apps import ManualStageTarget
from agent_bridge.gui.visual_detector import (
    CodexVisualDetector,
    VisualCandidate,
    VisualRect,
    composer_text_search_region,
    compute_plus_anchor_point,
    point_is_safe,
    safe_search_region,
    select_visual_anchor,
)
from agent_bridge.gui.visual_text_recognition import (
    OCRRuntimeStatus,
    OCRTextResult,
    text_matches,
)


class FakeTextRecognizer:
    def __init__(self, text: str):
        self.text = text

    def detect_text(
        self,
        _image,
        *,
        target_text: str,
        languages: tuple[str, ...],
        required_language: str | None = None,
        fuzzy: bool = True,
    ) -> OCRTextResult:
        _ = languages, required_language
        found, confidence = text_matches(target_text, self.text, fuzzy=fuzzy)
        return OCRTextResult(
            backend="pytesseract",
            backend_available=True,
            target_text=target_text,
            found=found,
            confidence=confidence,
            extracted_text=self.text,
            matched_text=target_text if found else None,
            reason="target_text_detected" if found else "target_text_not_detected",
            runtime_status=OCRRuntimeStatus(
                backend="pytesseract",
                pytesseract_package_available=True,
                tesseract_executable_available=True,
                available_languages=("eng", "kor"),
                english_available=True,
                korean_available=True,
            ),
        )


def target(**overrides) -> ManualStageTarget:
    data = {
        "plus_anchor_x_offset": 0,
        "plus_anchor_y_offset": 50,
    }
    data.update(overrides)
    app_name = data.pop("app_name", "Codex")
    return ManualStageTarget(
        app_name=app_name,
        **data,
    )


def make_plus_template(path: Path, *, size: int = 20) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(image)
    center = size // 2
    draw.line((center, 4, center, size - 5), fill="black", width=2)
    draw.line((4, center, size - 5, center), fill="black", width=2)
    image.save(path)


def make_screenshot_with_plus(
    *,
    template_path: Path,
    scale: float = 1.0,
    location: tuple[int, int] = (30, 220),
) -> object:
    from PIL import Image

    screenshot = Image.new("RGB", (400, 300), "white")
    template = Image.open(template_path).convert("RGB")
    if scale != 1.0:
        template = template.resize(
            (int(template.width * scale), int(template.height * scale))
        )
    screenshot.paste(template, location)
    return screenshot


WINDOW = (100, 100, 1000, 800)
PLUS = VisualCandidate("plus", VisualRect(500, 760, 20, 20), 0.93)
PLACEHOLDER = VisualCandidate("placeholder", VisualRect(540, 700, 220, 32), 0.82)


def test_visual_detector_picks_plus_button_over_placeholder():
    result = select_visual_anchor(
        target=target(),
        window_bounds=WINDOW,
        plus_candidate=PLUS,
        placeholder_candidate=PLACEHOLDER,
    )

    assert result.selected_strategy == "visual_plus_anchor"
    assert result.plus_button_found
    assert result.placeholder_found
    assert result.computed_click_point == (510, 720)
    assert result.click_point_safe


def test_plus_button_anchor_works_when_placeholder_absent():
    result = select_visual_anchor(
        target=target(),
        window_bounds=WINDOW,
        plus_candidate=PLUS,
        placeholder_candidate=None,
    )

    assert result.selected_strategy == "visual_plus_anchor"
    assert result.computed_click_point == (510, 720)


def test_placeholder_used_only_when_plus_button_missing():
    result = select_visual_anchor(
        target=target(),
        window_bounds=WINDOW,
        plus_candidate=None,
        placeholder_candidate=PLACEHOLDER,
    )

    assert result.selected_strategy == "visual_placeholder_anchor"
    assert result.computed_click_point == PLACEHOLDER.bbox.center


def test_plus_button_anchor_computes_click_point_from_center_and_offset():
    assert compute_plus_anchor_point(PLUS.bbox, target(plus_anchor_x_offset=5, plus_anchor_y_offset=80)) == (
        515,
        690,
    )


def test_click_point_rejected_when_outside_safe_bounds():
    safe_region = safe_search_region(WINDOW)
    unsafe_point = (120, 700)

    assert not point_is_safe(unsafe_point, window_bounds=WINDOW, safe_region=safe_region)


def test_left_sidebar_unsafe_region_is_rejected():
    left_sidebar_plus = VisualCandidate("plus", VisualRect(120, 760, 20, 20), 0.9)

    result = select_visual_anchor(
        target=target(),
        window_bounds=WINDOW,
        plus_candidate=left_sidebar_plus,
        placeholder_candidate=None,
    )

    assert result.selected_strategy == "none"
    assert not result.click_point_safe


def test_right_panel_unsafe_region_is_rejected():
    right_panel_plus = VisualCandidate("plus", VisualRect(1020, 760, 20, 20), 0.9)

    result = select_visual_anchor(
        target=target(),
        window_bounds=WINDOW,
        plus_candidate=right_panel_plus,
        placeholder_candidate=None,
    )

    assert result.selected_strategy == "none"
    assert not result.click_point_safe


def test_plus_button_itself_is_not_selected_as_click_point():
    result = select_visual_anchor(
        target=target(),
        window_bounds=WINDOW,
        plus_candidate=PLUS,
        placeholder_candidate=None,
    )

    assert result.computed_click_point is not None
    assert not PLUS.bbox.contains_point(result.computed_click_point)


def test_missing_screenshot_backend_is_handled_gracefully():
    detector = CodexVisualDetector(screenshot_provider=lambda _region: (_ for _ in ()).throw(RuntimeError("no screen")))

    result = detector.detect(
        target=target(
            composer_placeholder_text="후속 변경 사항을 부탁하세요",
            visual_text_recognition_enabled=False,
        ),
        window_bounds=WINDOW,
    )

    assert not result.backend_available
    assert not result.screenshot_captured
    assert "no screen" in (result.error or "")


def test_detector_screenshot_is_restricted_to_window_bounds():
    from PIL import Image

    captured_regions: list[tuple[int, int, int, int]] = []

    def provider(region):
        captured_regions.append(region)
        return Image.new("RGB", (region[2], region[3]), "white")

    detector = CodexVisualDetector(screenshot_provider=provider)

    result = detector.detect(
        target=target(
            composer_placeholder_text="후속 변경 사항을 부탁하세요",
            visual_text_recognition_enabled=False,
        ),
        window_bounds=WINDOW,
    )

    assert captured_regions == [WINDOW]
    assert result.screenshot_captured
    assert result.window_bounds == WINDOW
    assert result.placeholder_detection_backend_available is False
    assert result.placeholder_detection_error


def test_marker_presence_screenshot_is_restricted_to_codex_window(tmp_path):
    from PIL import Image

    captured_regions: list[tuple[int, int, int, int]] = []

    def provider(region):
        captured_regions.append(region)
        return Image.new("RGB", (region[2], region[3]), "white")

    detector = CodexVisualDetector(
        screenshot_provider=provider,
        marker_ocr_reader=lambda _image: "",
    )

    result = detector.detect_marker_presence(
        marker_text="AGENT_BRIDGE_CODEX_PASTE_TEST_DO_NOT_SUBMIT",
        window_bounds=WINDOW,
        logs_dir=tmp_path,
        write_debug=True,
    )

    assert captured_regions == [WINDOW]
    assert result.screenshot_captured
    assert result.search_region_bounds == composer_text_search_region(WINDOW).as_tuple()
    assert result.screenshot_path == str(tmp_path / "codex_marker_presence.png")
    assert result.annotated_screenshot_path == str(tmp_path / "codex_marker_presence_annotated.png")
    assert result.ocr_text_path == str(tmp_path / "codex_marker_presence_ocr.txt")
    assert (tmp_path / "codex_marker_presence.png").exists()
    assert (tmp_path / "codex_marker_presence_annotated.png").exists()
    assert (tmp_path / "codex_marker_presence_ocr.txt").exists()


def test_marker_presence_ocr_unavailable_returns_unknown_without_crashing(monkeypatch):
    from PIL import Image

    monkeypatch.setattr(
        "agent_bridge.gui.visual_text_recognition.shutil.which",
        lambda _name: None,
    )
    detector = CodexVisualDetector(
        screenshot_provider=lambda region: Image.new("RGB", (region[2], region[3]), "white"),
    )

    result = detector.detect_marker_presence(
        marker_text="AGENT_BRIDGE_CODEX_PASTE_TEST_DO_NOT_SUBMIT",
        window_bounds=WINDOW,
    )

    assert result.marker_detection_backend == "pytesseract"
    assert result.marker_detection_available is False
    assert result.marker_found is None
    assert result.error


def test_placeholder_ocr_detects_korean_placeholder_and_writes_artifact(tmp_path):
    from PIL import Image

    placeholder = "후속 변경 사항을 부탁하세요"
    detector = CodexVisualDetector(
        screenshot_provider=lambda region: Image.new("RGB", (region[2], region[3]), "white"),
        text_recognizer=FakeTextRecognizer(placeholder),
    )

    result = detector.detect(
        target=target(
            composer_placeholder_text=placeholder,
            visual_plus_templates=(),
        ),
        window_bounds=WINDOW,
        logs_dir=tmp_path,
        write_debug=True,
    )

    assert result.placeholder_detection_backend_available
    assert result.korean_ocr_available is True
    assert result.placeholder_found
    assert result.placeholder_target_text == placeholder
    assert result.placeholder_match_text == placeholder
    assert result.placeholder_ocr_confidence == 1.0
    assert result.placeholder_search_region_bounds == composer_text_search_region(WINDOW).as_tuple()
    assert result.selected_strategy == "visual_placeholder_anchor"
    assert result.placeholder_ocr_text_path == str(tmp_path / "codex_placeholder_ocr.txt")
    assert (tmp_path / "codex_placeholder_ocr.txt").read_text(encoding="utf-8") == placeholder
    assert (tmp_path / "codex_visual_detection_annotated.png").exists()


def test_placeholder_ocr_matches_whitespace_normalized_korean_text():
    from PIL import Image

    detector = CodexVisualDetector(
        screenshot_provider=lambda region: Image.new("RGB", (region[2], region[3]), "white"),
        text_recognizer=FakeTextRecognizer("후속 변경\n사항을   부탁하세요"),
    )

    result = detector.detect(
        target=target(
            composer_placeholder_text="후속 변경 사항을 부탁하세요",
            visual_plus_templates=(),
        ),
        window_bounds=WINDOW,
    )

    assert result.placeholder_found
    assert result.placeholder_match_text == "후속 변경 사항을 부탁하세요"


def test_placeholder_ocr_partial_korean_phrase_fallback():
    from PIL import Image

    detector = CodexVisualDetector(
        screenshot_provider=lambda region: Image.new("RGB", (region[2], region[3]), "white"),
        text_recognizer=FakeTextRecognizer("후속 변경"),
    )

    result = detector.detect(
        target=target(
            composer_placeholder_text="후속 변경 사항을 부탁하세요",
            visual_plus_templates=(),
        ),
        window_bounds=WINDOW,
    )

    assert result.placeholder_found
    assert result.placeholder_match_text == "후속 변경"
    assert result.placeholder_detection_reason == "partial_placeholder_text_detected"


def test_placeholder_ocr_unrelated_text_reports_absent():
    from PIL import Image

    detector = CodexVisualDetector(
        screenshot_provider=lambda region: Image.new("RGB", (region[2], region[3]), "white"),
        text_recognizer=FakeTextRecognizer("unrelated text"),
    )

    result = detector.detect(
        target=target(
            composer_placeholder_text="후속 변경 사항을 부탁하세요",
            visual_plus_templates=(),
        ),
        window_bounds=WINDOW,
    )

    assert result.placeholder_detection_backend_available
    assert not result.placeholder_found
    assert result.placeholder_detection_reason == "target_text_not_detected"


def test_marker_presence_ocr_reader_finds_marker_with_wrapped_text():
    from PIL import Image

    detector = CodexVisualDetector(
        screenshot_provider=lambda region: Image.new("RGB", (region[2], region[3]), "white"),
        marker_ocr_reader=lambda _image: "AGENT_BRIDGE_CODEX_PASTE_TEST\n_DO_NOT_SUBMIT",
    )

    result = detector.detect_marker_presence(
        marker_text="AGENT_BRIDGE_CODEX_PASTE_TEST_DO_NOT_SUBMIT",
        window_bounds=WINDOW,
    )

    assert result.marker_detection_available
    assert result.marker_found is True
    assert result.marker_confidence == 1.0


def test_marker_presence_ocr_reader_reports_absent_marker():
    from PIL import Image

    detector = CodexVisualDetector(
        screenshot_provider=lambda region: Image.new("RGB", (region[2], region[3]), "white"),
        marker_ocr_reader=lambda _image: "unrelated text",
    )

    result = detector.detect_marker_presence(
        marker_text="AGENT_BRIDGE_CODEX_PASTE_TEST_DO_NOT_SUBMIT",
        window_bounds=WINDOW,
    )

    assert result.marker_detection_available
    assert result.marker_found is False
    assert result.marker_confidence is not None
    assert result.marker_confidence < 0.88


def test_multiscale_matching_finds_scaled_plus_button(tmp_path):
    template_path = tmp_path / "plus.png"
    make_plus_template(template_path)
    screenshot = make_screenshot_with_plus(template_path=template_path, scale=1.25)
    detector = CodexVisualDetector(screenshot_provider=lambda _region: screenshot)

    result = detector.detect(
        target=target(
            plus_anchor_y_offset=20,
            visual_text_recognition_enabled=False,
            visual_plus_templates=(str(template_path),),
            visual_plus_confidence_threshold=0.80,
            visual_plus_multiscale_enabled=True,
        ),
        window_bounds=(0, 0, 400, 300),
    )

    assert result.plus_button_found
    assert result.plus_button_confidence is not None
    assert result.plus_button_confidence >= 0.80
    assert result.plus_template_size == (24, 24) or result.plus_template_size == (26, 26)
    assert result.selected_strategy == "visual_plus_anchor"


def test_grayscale_matching_path_finds_colored_plus_button(tmp_path):
    from PIL import Image, ImageDraw

    template_path = tmp_path / "plus.png"
    make_plus_template(template_path)
    screenshot = Image.new("RGB", (400, 300), "white")
    draw = ImageDraw.Draw(screenshot)
    draw.line((40, 224, 40, 236), fill=(20, 20, 20), width=2)
    draw.line((34, 230, 46, 230), fill=(20, 20, 20), width=2)
    detector = CodexVisualDetector(screenshot_provider=lambda _region: screenshot)

    result = detector.detect(
        target=target(
            plus_anchor_y_offset=20,
            visual_text_recognition_enabled=False,
            visual_plus_templates=(str(template_path),),
            visual_plus_confidence_threshold=0.40,
            visual_plus_multiscale_enabled=True,
        ),
        window_bounds=(0, 0, 400, 300),
    )

    assert result.plus_best_match_confidence is not None
    assert result.plus_best_match_confidence >= 0.40
    assert result.plus_template_path == str(template_path)


def test_multiple_plus_templates_are_tried_in_order_until_match(tmp_path):
    bad_template = tmp_path / "bad.png"
    good_template = tmp_path / "good.png"
    make_plus_template(good_template)
    from PIL import Image, ImageDraw

    bad = Image.new("RGB", (20, 20), "white")
    draw = ImageDraw.Draw(bad)
    draw.ellipse((4, 4, 16, 16), outline="black", width=2)
    bad.save(bad_template)
    screenshot = make_screenshot_with_plus(template_path=good_template)
    detector = CodexVisualDetector(screenshot_provider=lambda _region: screenshot)

    result = detector.detect(
        target=target(
            plus_anchor_y_offset=20,
            visual_text_recognition_enabled=False,
            visual_plus_templates=(str(bad_template), str(good_template)),
            visual_plus_confidence_threshold=0.80,
        ),
        window_bounds=(0, 0, 400, 300),
    )

    assert result.plus_button_found
    assert result.plus_template_path == str(good_template)


def test_app_specific_plus_templates_are_respected(tmp_path):
    codex_template = tmp_path / "codex_plus.png"
    chatgpt_template = tmp_path / "chatgpt_plus.png"
    make_plus_template(codex_template)
    from PIL import Image, ImageDraw

    chatgpt = Image.new("RGB", (20, 20), "white")
    draw = ImageDraw.Draw(chatgpt)
    draw.rectangle((4, 4, 16, 16), outline="black", width=2)
    chatgpt.save(chatgpt_template)

    screenshot = make_screenshot_with_plus(template_path=codex_template)
    detector = CodexVisualDetector(screenshot_provider=lambda _region: screenshot)

    codex_result = detector.detect(
        target=target(
            app_name="Codex",
            plus_anchor_y_offset=20,
            visual_text_recognition_enabled=False,
            visual_plus_templates=(str(codex_template),),
            visual_plus_confidence_threshold=0.80,
        ),
        window_bounds=(0, 0, 400, 300),
    )
    chatgpt_result = detector.detect(
        target=target(
            app_name="Google Chrome",
            plus_anchor_y_offset=20,
            visual_text_recognition_enabled=False,
            visual_plus_templates=(str(chatgpt_template),),
            visual_plus_confidence_threshold=0.95,
        ),
        window_bounds=(0, 0, 400, 300),
    )

    assert codex_result.plus_button_found
    assert codex_result.plus_template_path == str(codex_template)
    assert not chatgpt_result.plus_button_found
    assert chatgpt_result.plus_template_path == str(chatgpt_template)


def test_no_target_templates_does_not_fall_back_to_codex_asset(tmp_path):
    template_path = tmp_path / "codex_plus.png"
    make_plus_template(template_path)
    screenshot = make_screenshot_with_plus(template_path=template_path)
    detector = CodexVisualDetector(screenshot_provider=lambda _region: screenshot)

    result = detector.detect(
        target=target(
            visual_text_recognition_enabled=False,
            visual_plus_templates=(),
        ),
        window_bounds=(0, 0, 400, 300),
    )

    assert not result.plus_button_found
    assert result.plus_match_error == "No plus-button templates exist for Codex."


def test_low_confidence_plus_match_is_rejected(tmp_path):
    from PIL import Image

    template_path = tmp_path / "plus.png"
    make_plus_template(template_path)
    screenshot = Image.new("RGB", (400, 300), "white")
    detector = CodexVisualDetector(screenshot_provider=lambda _region: screenshot)

    result = detector.detect(
        target=target(
            visual_text_recognition_enabled=False,
            visual_plus_templates=(str(template_path),),
            visual_plus_confidence_threshold=0.95,
        ),
        window_bounds=(0, 0, 400, 300),
    )

    assert not result.plus_button_found
    assert result.plus_best_match_confidence is not None
    assert result.plus_best_match_confidence < 0.95


def test_plus_detection_search_is_bounded_to_codex_lower_band(tmp_path):
    template_path = tmp_path / "plus.png"
    make_plus_template(template_path)
    screenshot = make_screenshot_with_plus(template_path=template_path)
    detector = CodexVisualDetector(screenshot_provider=lambda _region: screenshot)

    result = detector.detect(
        target=target(
            plus_anchor_y_offset=20,
            visual_text_recognition_enabled=False,
            visual_plus_templates=(str(template_path),),
        ),
        window_bounds=(0, 0, 400, 300),
    )

    assert result.plus_search_region_bounds == (16, 180, 352, 120)
