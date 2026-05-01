from __future__ import annotations

from agent_bridge.gui.visual_text_recognition import PytesseractTextRecognizer, text_matches


class FakePytesseract:
    def __init__(
        self,
        *,
        text: str = "",
        languages: tuple[str, ...] = ("eng",),
        version_error: Exception | None = None,
        image_error: Exception | None = None,
    ) -> None:
        self.text = text
        self.languages = languages
        self.version_error = version_error
        self.image_error = image_error

    def get_tesseract_version(self):
        if self.version_error:
            raise self.version_error
        return "5.0.0"

    def get_languages(self, config=""):
        _ = config
        return list(self.languages)

    def image_to_string(self, image, lang):
        _ = image
        _ = lang
        if self.image_error:
            raise self.image_error
        return self.text


def test_ocr_backend_unavailable_without_pytesseract_package(monkeypatch):
    def raise_import_error(_name):
        raise ModuleNotFoundError("No module named 'pytesseract'")

    monkeypatch.setattr(
        "agent_bridge.gui.visual_text_recognition.importlib.import_module",
        raise_import_error,
    )
    recognizer = PytesseractTextRecognizer(pytesseract_module=None)

    result = recognizer.detect_text(
        object(),
        target_text="AGENT_BRIDGE_CODEX_PASTE_TEST_DO_NOT_SUBMIT",
        languages=("eng",),
        required_language="eng",
    )

    assert not result.backend_available
    assert result.found is None
    assert "pytesseract" in (result.error or "")


def test_pytesseract_available_but_executable_missing(monkeypatch):
    monkeypatch.setattr("agent_bridge.gui.visual_text_recognition.shutil.which", lambda _name: None)
    recognizer = PytesseractTextRecognizer(
        pytesseract_module=FakePytesseract(text="marker"),
    )

    result = recognizer.detect_text(
        object(),
        target_text="marker",
        languages=("eng",),
        required_language="eng",
    )

    assert result.runtime_status is not None
    assert result.runtime_status.pytesseract_package_available
    assert not result.runtime_status.tesseract_executable_available
    assert not result.backend_available
    assert "tesseract executable" in (result.error or "")


def test_marker_text_detected_with_fuzzy_spacing(monkeypatch):
    monkeypatch.setattr("agent_bridge.gui.visual_text_recognition.shutil.which", lambda _name: "/usr/bin/tesseract")
    recognizer = PytesseractTextRecognizer(
        pytesseract_module=FakePytesseract(
            text="agent bridge codex paste test do not submit",
            languages=("eng",),
        )
    )

    result = recognizer.detect_text(
        object(),
        target_text="AGENT_BRIDGE_CODEX_PASTE_TEST_DO_NOT_SUBMIT",
        languages=("eng",),
        required_language="eng",
    )

    assert result.backend_available
    assert result.found is True
    assert result.confidence is not None


def test_marker_text_absent_returns_no_when_ocr_is_reliable(monkeypatch):
    monkeypatch.setattr("agent_bridge.gui.visual_text_recognition.shutil.which", lambda _name: "/usr/bin/tesseract")
    recognizer = PytesseractTextRecognizer(
        pytesseract_module=FakePytesseract(text="unrelated text", languages=("eng",))
    )

    result = recognizer.detect_text(
        object(),
        target_text="AGENT_BRIDGE_CODEX_PASTE_TEST_DO_NOT_SUBMIT",
        languages=("eng",),
        required_language="eng",
    )

    assert result.backend_available
    assert result.found is False


def test_korean_placeholder_unavailable_without_korean_language(monkeypatch):
    monkeypatch.setattr("agent_bridge.gui.visual_text_recognition.shutil.which", lambda _name: "/usr/bin/tesseract")
    recognizer = PytesseractTextRecognizer(
        pytesseract_module=FakePytesseract(text="후속 변경 사항을 부탁하세요", languages=("eng",))
    )

    result = recognizer.detect_text(
        object(),
        target_text="후속 변경 사항을 부탁하세요",
        languages=("kor", "eng"),
        required_language="kor",
        fuzzy=False,
    )

    assert not result.backend_available
    assert result.found is None
    assert "Korean OCR language data" in (result.error or "")


def test_text_matching_tolerates_case_and_whitespace():
    found, score = text_matches(
        "AGENT_BRIDGE_CODEX_PASTE_TEST_DO_NOT_SUBMIT",
        "agent bridge codex paste test do not submit",
        fuzzy=True,
    )

    assert found
    assert score >= 0.88
