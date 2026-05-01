from __future__ import annotations

import importlib
import shutil
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any


@dataclass(frozen=True)
class OCRRuntimeStatus:
    backend: str
    pytesseract_package_available: bool
    tesseract_executable_available: bool
    available_languages: tuple[str, ...] = ()
    english_available: bool | None = None
    korean_available: bool | None = None
    error: str | None = None

    @property
    def backend_available(self) -> bool:
        return self.pytesseract_package_available and self.tesseract_executable_available


@dataclass(frozen=True)
class OCRTextResult:
    backend: str
    backend_available: bool
    target_text: str
    found: bool | None
    confidence: float | None
    extracted_text: str = ""
    matched_text: str | None = None
    reason: str | None = None
    error: str | None = None
    runtime_status: OCRRuntimeStatus | None = None


@dataclass
class PytesseractTextRecognizer:
    pytesseract_module: Any | None = None
    executable_path: str | None = None
    fuzzy_threshold: float = 0.88

    def runtime_status(self) -> OCRRuntimeStatus:
        module = self._load_pytesseract()
        if isinstance(module, str):
            return OCRRuntimeStatus(
                backend="pytesseract",
                pytesseract_package_available=False,
                tesseract_executable_available=False,
                error=module,
            )

        executable_available = bool(self.executable_path or shutil.which("tesseract"))
        version_error: str | None = None
        if executable_available:
            try:
                module.get_tesseract_version()
            except Exception as error:  # pragma: no cover - depends on host OCR install
                executable_available = False
                version_error = str(error)

        languages: tuple[str, ...] = ()
        language_error: str | None = None
        english_available: bool | None = None
        korean_available: bool | None = None
        if executable_available:
            try:
                languages = tuple(str(language) for language in module.get_languages(config=""))
                english_available = "eng" in languages
                korean_available = "kor" in languages
            except Exception as error:  # pragma: no cover - depends on host OCR install
                language_error = str(error)

        return OCRRuntimeStatus(
            backend="pytesseract",
            pytesseract_package_available=True,
            tesseract_executable_available=executable_available,
            available_languages=languages,
            english_available=english_available,
            korean_available=korean_available,
            error=version_error or language_error,
        )

    def detect_text(
        self,
        image: Any,
        *,
        target_text: str,
        languages: tuple[str, ...],
        required_language: str | None = None,
        fuzzy: bool = True,
    ) -> OCRTextResult:
        status = self.runtime_status()
        if not status.pytesseract_package_available:
            return self._unavailable(target_text, status, status.error or "pytesseract is not installed.")
        if not status.tesseract_executable_available:
            return self._unavailable(
                target_text,
                status,
                status.error or "The tesseract executable is not available.",
            )
        if required_language == "kor" and status.korean_available is False:
            return self._unavailable(target_text, status, "Korean OCR language data is unavailable.")
        if required_language == "eng" and status.english_available is False:
            return self._unavailable(target_text, status, "English OCR language data is unavailable.")

        module = self._load_pytesseract()
        if isinstance(module, str):
            return self._unavailable(target_text, status, module)

        lang = "+".join(languages)
        try:
            text = module.image_to_string(image, lang=lang)
        except Exception as error:
            return OCRTextResult(
                backend="pytesseract",
                backend_available=False,
                target_text=target_text,
                found=None,
                confidence=None,
                reason="OCR failed.",
                error=str(error),
                runtime_status=status,
            )

        found, score = text_matches(target_text, text, fuzzy=fuzzy, threshold=self.fuzzy_threshold)
        return OCRTextResult(
            backend="pytesseract",
            backend_available=True,
            target_text=target_text,
            found=found,
            confidence=score,
            extracted_text=text,
            matched_text=target_text if found else None,
            reason="target_text_detected" if found else "target_text_not_detected",
            runtime_status=status,
        )

    def _load_pytesseract(self) -> Any | str:
        if self.pytesseract_module is not None:
            return self.pytesseract_module
        try:
            module = importlib.import_module("pytesseract")
        except Exception as error:
            return f"pytesseract package unavailable: {error}"
        if self.executable_path:
            module.pytesseract.tesseract_cmd = self.executable_path
        return module

    @staticmethod
    def _unavailable(
        target_text: str,
        status: OCRRuntimeStatus,
        message: str,
    ) -> OCRTextResult:
        return OCRTextResult(
            backend="pytesseract",
            backend_available=False,
            target_text=target_text,
            found=None,
            confidence=None,
            reason=message,
            error=message,
            runtime_status=status,
        )


def text_matches(
    target_text: str,
    detected_text: str,
    *,
    fuzzy: bool,
    threshold: float = 0.88,
) -> tuple[bool, float]:
    normalized_target = normalize_ocr_text(target_text)
    normalized_detected = normalize_ocr_text(detected_text)
    if not normalized_target or not normalized_detected:
        return False, 0.0
    if normalized_target in normalized_detected:
        return True, 1.0
    if not fuzzy:
        return False, 0.0
    score = SequenceMatcher(None, normalized_target, normalized_detected).ratio()
    return score >= threshold, score


def normalize_ocr_text(text: str) -> str:
    return "".join(str(text).lower().split())
