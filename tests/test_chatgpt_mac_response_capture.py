from __future__ import annotations

from pathlib import Path

from agent_bridge.gui.chatgpt_mac_response_capture import (
    diagnose_chatgpt_mac_response_capture,
    format_chatgpt_mac_response_capture,
)
from agent_bridge.gui.macos_apps import ManualStageTarget


def target(**overrides) -> ManualStageTarget:
    data = {
        "app_name": "ChatGPT",
        "backend": "chatgpt_mac_visual",
        "visual_asset_profile": "chatgpt_mac",
    }
    data.update(overrides)
    return ManualStageTarget(**data)


def screenshot(_region):
    from PIL import Image

    return Image.new("RGB", (600, 400), "white")


def make_copy_assets(root: Path) -> Path:
    from PIL import Image, ImageDraw

    asset_dir = root / "assets" / "gui" / "chatgpt_mac"
    asset_dir.mkdir(parents=True)
    image = Image.new("RGB", (20, 20), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((5, 5, 15, 15), outline="black", width=2)
    draw.line((8, 8, 17, 8), fill="black", width=2)
    light = asset_dir / "chatgpt_mac_copy_response_button_light.png"
    dark = asset_dir / "chatgpt_mac_copy_response_button_dark.png"
    image.save(light)
    image.save(dark)
    return light


def make_chrome_app_copy_assets(root: Path) -> Path:
    from PIL import Image, ImageDraw

    asset_dir = root / "assets" / "gui" / "chatgpt_chrome_app"
    asset_dir.mkdir(parents=True)
    image = Image.new("RGB", (20, 20), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((5, 5, 15, 15), outline="black", width=2)
    draw.line((8, 8, 17, 8), fill="black", width=2)
    light = asset_dir / "chatgpt_chrome_app_copy_response_button_light.png"
    dark = asset_dir / "chatgpt_chrome_app_copy_response_button_dark.png"
    image.save(light)
    image.save(dark)
    return light


def make_scroll_assets(root: Path) -> Path:
    from PIL import Image, ImageDraw

    asset_dir = root / "assets" / "gui" / "chatgpt_mac"
    asset_dir.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (20, 20), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, 16, 16), outline="black", width=2)
    draw.line((10, 6, 10, 14), fill="black", width=2)
    draw.line((6, 10, 10, 14), fill="black", width=2)
    draw.line((14, 10, 10, 14), fill="black", width=2)
    light = asset_dir / "chatgpt_mac_scroll_down_button_light.png"
    dark = asset_dir / "chatgpt_mac_scroll_down_button_dark.png"
    image.save(light)
    image.save(dark)
    return light


def make_chrome_app_scroll_assets(root: Path) -> Path:
    from PIL import Image, ImageDraw

    asset_dir = root / "assets" / "gui" / "chatgpt_chrome_app"
    asset_dir.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (20, 20), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, 16, 16), outline="black", width=2)
    draw.line((10, 6, 10, 14), fill="black", width=2)
    draw.line((6, 10, 10, 14), fill="black", width=2)
    draw.line((14, 10, 10, 14), fill="black", width=2)
    light = asset_dir / "chatgpt_chrome_app_scroll_down_button_light.png"
    dark = asset_dir / "chatgpt_chrome_app_scroll_down_button_dark.png"
    image.save(light)
    image.save(dark)
    return light


def screenshot_with_copy_button(template_path: Path, calls: list[tuple[int, int, int, int]]):
    def provider(region):
        from PIL import Image

        calls.append(region)
        image = Image.new("RGB", (region[2], region[3]), "white")
        template = Image.open(template_path).convert("RGB")
        image.paste(template, (300, 300))
        return image

    return provider


def screenshot_sequence(templates: list[Path | None], calls: list[tuple[int, int, int, int]]):
    def provider(region):
        from PIL import Image

        calls.append(region)
        image = Image.new("RGB", (region[2], region[3]), "white")
        template_path = templates[min(len(calls) - 1, len(templates) - 1)]
        if template_path is not None:
            template = Image.open(template_path).convert("RGB")
            image.paste(template, (300, 300))
        return image

    return provider


def test_missing_response_copy_assets_report_unsupported(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)

    result = diagnose_chatgpt_mac_response_capture(
        target=target(),
        window_bounds=(10, 20, 600, 400),
        screenshot_provider=screenshot,
    )

    assert result.screenshot_captured
    assert result.backend_available
    assert not result.supported
    assert not result.copy_button_found
    assert "chatgpt_mac_copy_response_button_light.png" in result.missing_copy_assets[0]
    assert "response-copy assets are missing" in (result.error or "")


def test_response_capture_format_reports_missing_assets(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)

    output = format_chatgpt_mac_response_capture(
        diagnose_chatgpt_mac_response_capture(
            target=target(),
            window_bounds=(10, 20, 600, 400),
            screenshot_provider=screenshot,
        )
    )

    assert "Response capture supported: no" in output
    assert "Missing copy assets:" in output


def test_response_copy_assets_are_loaded_and_detected_window_bounded(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.chdir(tmp_path)
    template = make_copy_assets(tmp_path)
    make_scroll_assets(tmp_path)
    captured_regions: list[tuple[int, int, int, int]] = []

    result = diagnose_chatgpt_mac_response_capture(
        target=target(),
        window_bounds=(10, 20, 600, 400),
        screenshot_provider=screenshot_with_copy_button(template, captured_regions),
    )

    assert captured_regions == [(10, 20, 600, 400)]
    assert result.supported
    assert result.copy_button_found
    assert result.missing_copy_assets == ()
    assert result.matched_asset_path is not None
    assert result.copy_button_click_point is not None
    assert result.copy_button_click_point_safe
    assert not result.capture_attempted
    assert not result.scroll_attempted
    assert result.copy_detection_attempt_count == 1


def test_chrome_app_response_copy_assets_are_profile_specific(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    template = make_chrome_app_copy_assets(tmp_path)
    make_chrome_app_scroll_assets(tmp_path)
    captured_regions: list[tuple[int, int, int, int]] = []

    result = diagnose_chatgpt_mac_response_capture(
        target=target(
            backend="chatgpt_chrome_app_visual",
            visual_asset_profile="chatgpt_chrome_app",
        ),
        window_bounds=(10, 20, 600, 400),
        screenshot_provider=screenshot_with_copy_button(template, captured_regions),
    )

    assert captured_regions == [(10, 20, 600, 400)]
    assert result.asset_profile == "chatgpt_chrome_app"
    assert result.supported
    assert result.copy_button_found
    assert all("chatgpt_chrome_app" in path for path in result.copy_assets)
    assert all("chatgpt_chrome_app" in path for path in result.scroll_assets)
    assert not any("chatgpt_mac" in path for path in result.copy_assets)


def test_chrome_app_missing_response_assets_fail_clearly(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)

    result = diagnose_chatgpt_mac_response_capture(
        target=target(
            backend="chatgpt_chrome_app_visual",
            visual_asset_profile="chatgpt_chrome_app",
        ),
        window_bounds=(10, 20, 600, 400),
        screenshot_provider=screenshot,
    )

    assert not result.supported
    assert "chatgpt_chrome_app_copy_response_button_light.png" in result.missing_copy_assets[0]
    assert "chatgpt_chrome_app" in (result.error or "")


def test_response_copy_capture_clicks_only_when_explicitly_requested(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    template = make_copy_assets(tmp_path)
    make_scroll_assets(tmp_path)
    clicks: list[tuple[int, int]] = []

    class Clipboard:
        def __init__(self) -> None:
            self.calls = 0
            self.writes: list[str] = []

        def read_text(self) -> str:
            self.calls += 1
            if self.calls == 1:
                return "before"
            return "CODEX_NEXT_PROMPT captured"

        def copy_text(self, text: str) -> None:
            self.writes.append(text)

    clipboard = Clipboard()
    result = diagnose_chatgpt_mac_response_capture(
        target=target(),
        window_bounds=(10, 20, 600, 400),
        screenshot_provider=screenshot_with_copy_button(template, []),
        attempt_copy=True,
        clipboard=clipboard,  # type: ignore[arg-type]
        expected_marker="CODEX_NEXT_PROMPT",
        clicker=lambda x, y: clicks.append((x, y)),
        sleep_fn=lambda _seconds: None,
    )

    assert result.capture_attempted
    assert result.response_captured
    assert clicks == [result.copy_button_click_point]
    assert clipboard.writes
    assert clipboard.writes[0].startswith("AGENT_BRIDGE_CHATGPT_MAC_RESPONSE_COPY_SENTINEL_")


def test_scroll_down_assets_are_loaded_and_retry_finds_copy(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    copy_template = make_copy_assets(tmp_path)
    scroll_template = make_scroll_assets(tmp_path)
    captured_regions: list[tuple[int, int, int, int]] = []
    clicks: list[tuple[int, int]] = []

    result = diagnose_chatgpt_mac_response_capture(
        target=target(),
        window_bounds=(10, 20, 600, 400),
        screenshot_provider=screenshot_sequence(
            [scroll_template, copy_template],
            captured_regions,
        ),
        clicker=lambda x, y: clicks.append((x, y)),
        sleep_fn=lambda _seconds: None,
    )

    assert captured_regions == [(10, 20, 600, 400), (10, 20, 600, 400)]
    assert result.scroll_button_found
    assert result.scroll_attempted
    assert result.scroll_succeeded
    assert result.recaptured_after_scroll
    assert clicks == [result.scroll_button_click_point]
    assert result.copy_detection_attempt_count == 2
    assert result.copy_button_found
    assert result.supported


def test_scroll_down_is_not_attempted_when_copy_is_visible(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    copy_template = make_copy_assets(tmp_path)
    make_scroll_assets(tmp_path)
    clicks: list[tuple[int, int]] = []

    result = diagnose_chatgpt_mac_response_capture(
        target=target(),
        window_bounds=(10, 20, 600, 400),
        screenshot_provider=screenshot_with_copy_button(copy_template, []),
        clicker=lambda x, y: clicks.append((x, y)),
        sleep_fn=lambda _seconds: None,
    )

    assert result.copy_button_found
    assert result.copy_detection_attempt_count == 1
    assert not result.scroll_attempted
    assert clicks == []


def test_response_copy_not_found_is_reported_after_scroll_retry(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    make_copy_assets(tmp_path)
    scroll_template = make_scroll_assets(tmp_path)
    captured_regions: list[tuple[int, int, int, int]] = []

    result = diagnose_chatgpt_mac_response_capture(
        target=target(),
        window_bounds=(10, 20, 600, 400),
        screenshot_provider=screenshot_sequence([scroll_template, None], captured_regions),
        clicker=lambda _x, _y: None,
        sleep_fn=lambda _seconds: None,
    )

    assert not result.supported
    assert not result.copy_button_found
    assert result.scroll_attempted
    assert result.copy_detection_attempt_count == 2
    assert result.error == "response_copy_button_not_found"
