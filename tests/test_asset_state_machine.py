from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from agent_bridge.gui.asset_state_machine import (
    AssetVisualStateDetector,
    VisualAssetKind,
    VisualAssetMatch,
    VisualGuiState,
    _select_state_match,
    default_asset_profile,
    visual_asset_search_region,
    visual_state_search_region,
    wait_for_visual_idle,
)
from agent_bridge.gui.gui_automation import MacOSSystemEventsGuiAdapter
from agent_bridge.gui.macos_apps import ManualStageTarget


WINDOW = (100, 100, 500, 400)


def make_template(path: Path, kind: str) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (24, 24), "white")
    draw = ImageDraw.Draw(image)
    if kind == "plus":
        draw.line((12, 4, 12, 20), fill="black", width=3)
        draw.line((4, 12, 20, 12), fill="black", width=3)
    elif kind == "disabled":
        draw.ellipse((5, 5, 19, 19), outline="black", width=3)
    elif kind == "send":
        draw.polygon((5, 4, 20, 12, 5, 20), outline="black", fill="black")
    elif kind == "stop":
        draw.rectangle((6, 6, 18, 18), outline="black", fill="black")
    else:
        raise AssertionError(kind)
    image.save(path)


def screenshot_with(template_path: Path, *, location: tuple[int, int] = (230, 290)):
    from PIL import Image

    screenshot = Image.new("RGB", (WINDOW[2], WINDOW[3]), "white")
    template = Image.open(template_path).convert("RGB")
    screenshot.paste(template, location)
    return screenshot


def screenshot_with_scaled(
    template_path: Path,
    *,
    scale: float,
    location: tuple[int, int] = (230, 290),
):
    from PIL import Image

    screenshot = Image.new("RGB", (WINDOW[2], WINDOW[3]), "white")
    template = Image.open(template_path).convert("RGB")
    scaled = template.resize(
        (int(template.width * scale), int(template.height * scale)),
        Image.Resampling.NEAREST,
    )
    screenshot.paste(scaled, location)
    return screenshot


def screenshot_with_many(templates: list[tuple[Path, tuple[int, int]]]):
    from PIL import Image

    screenshot = Image.new("RGB", (WINDOW[2], WINDOW[3]), "white")
    for template_path, location in templates:
        template = Image.open(template_path).convert("RGB")
        screenshot.paste(template, location)
    return screenshot


def blank_screenshot():
    from PIL import Image

    return Image.new("RGB", (WINDOW[2], WINDOW[3]), "white")


def patterned_screenshot():
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (WINDOW[2], WINDOW[3]), "white")
    draw = ImageDraw.Draw(image)
    for x in range(0, WINDOW[2], 20):
        draw.line((x, 0, x, WINDOW[3]), fill=(210, 210, 210), width=1)
    for y in range(0, WINDOW[3], 20):
        draw.line((0, y, WINDOW[2], y), fill=(230, 230, 230), width=1)
    return image


def target(**overrides) -> ManualStageTarget:
    data = {
        "app_name": "Codex",
        "visual_asset_profile": "codex",
        "visual_plus_confidence_threshold": 0.80,
        "visual_plus_multiscale_enabled": False,
        "plus_anchor_x_offset": 0,
        "plus_anchor_y_offset": 50,
    }
    data.update(overrides)
    return ManualStageTarget(**data)


def test_chatgpt_mac_asset_profile_loads_correct_files():
    profile = default_asset_profile("chatgpt_mac")

    assert profile.profile_id == "chatgpt_mac"
    assert all("assets/gui/chatgpt_mac/" in path for path in profile.plus_templates)
    assert all("chatgpt_mac_send_disabled" in path for path in profile.send_disabled_templates)
    assert all("chatgpt_mac_send_button" in path for path in profile.send_templates)
    assert all("chatgpt_mac_stop_button" in path for path in profile.stop_templates)


def test_chatgpt_chrome_app_asset_profile_loads_correct_files():
    profile = default_asset_profile("chatgpt_chrome_app")

    assert profile.profile_id == "chatgpt_chrome_app"
    assert all("assets/gui/chatgpt_chrome_app/" in path for path in profile.plus_templates)
    assert all("chatgpt_chrome_app_voice_button" in path for path in profile.send_disabled_templates)
    assert all("chatgpt_chrome_app_send_button" in path for path in profile.send_templates)
    assert all("chatgpt_chrome_app_stop_button" in path for path in profile.stop_templates)
    assert not any("chatgpt_mac/" in path for path in profile.plus_templates)
    assert not any("assets/gui/codex/" in path for path in profile.plus_templates)


def test_codex_asset_profile_loads_correct_files():
    profile = default_asset_profile("codex")

    assert profile.profile_id == "codex"
    assert all("assets/gui/codex/" in path for path in profile.plus_templates)
    assert all("codex_send_disabled" in path for path in profile.send_disabled_templates)
    assert all("codex_send_button" in path for path in profile.send_templates)
    assert all("codex_stop_button" in path for path in profile.stop_templates)


def test_send_disabled_maps_to_idle(tmp_path: Path):
    template = tmp_path / "send_disabled.png"
    make_template(template, "disabled")
    detector = AssetVisualStateDetector(screenshot_provider=lambda _region: screenshot_with(template))

    result = detector.detect(
        target=target(visual_send_disabled_templates=(str(template),)),
        window_bounds=WINDOW,
    )

    assert result.matched_state == VisualGuiState.IDLE
    assert result.matched_asset_path == str(template)


def test_chatgpt_chrome_app_voice_asset_maps_to_idle(tmp_path: Path):
    template = tmp_path / "voice.png"
    make_template(template, "disabled")
    detector = AssetVisualStateDetector(screenshot_provider=lambda _region: screenshot_with(template))

    result = detector.detect(
        target=target(
            app_name="ChatGPT",
            visual_asset_profile="chatgpt_chrome_app",
            visual_send_disabled_templates=(str(template),),
        ),
        window_bounds=WINDOW,
    )

    assert result.asset_profile == "chatgpt_chrome_app"
    assert result.matched_state == VisualGuiState.IDLE
    assert result.matched_asset_kind == VisualAssetKind.SEND_DISABLED


def test_send_maps_to_composer_has_text(tmp_path: Path):
    template = tmp_path / "send.png"
    make_template(template, "send")
    detector = AssetVisualStateDetector(screenshot_provider=lambda _region: screenshot_with(template))

    result = detector.detect(target=target(visual_send_templates=(str(template),)), window_bounds=WINDOW)

    assert result.matched_state == VisualGuiState.COMPOSER_HAS_TEXT


def test_stop_maps_to_running(tmp_path: Path):
    template = tmp_path / "stop.png"
    make_template(template, "stop")
    detector = AssetVisualStateDetector(screenshot_provider=lambda _region: screenshot_with(template))

    result = detector.detect(target=target(visual_stop_templates=(str(template),)), window_bounds=WINDOW)

    assert result.matched_state == VisualGuiState.RUNNING


def test_multiple_incompatible_codex_states_are_ambiguous(tmp_path: Path):
    disabled = tmp_path / "disabled.png"
    send = tmp_path / "send.png"
    stop = tmp_path / "stop.png"
    make_template(disabled, "disabled")
    make_template(send, "send")
    make_template(stop, "stop")
    detector = AssetVisualStateDetector(
        screenshot_provider=lambda _region: screenshot_with_many(
            [
                (disabled, (210, 290)),
                (send, (250, 290)),
                (stop, (290, 290)),
            ]
        )
    )

    result = detector.detect(
        target=target(
            visual_send_disabled_templates=(str(disabled),),
            visual_send_templates=(str(send),),
            visual_stop_templates=(str(stop),),
        ),
        window_bounds=WINDOW,
    )

    assert result.matched_state == VisualGuiState.AMBIGUOUS
    assert result.state_ambiguous
    assert result.matched_asset_path in {str(disabled), str(send), str(stop)}


def test_chatgpt_mac_weaker_stop_does_not_override_stronger_send():
    send = VisualAssetMatch(
        asset_kind=VisualAssetKind.SEND,
        state=VisualGuiState.COMPOSER_HAS_TEXT,
        template_path="send.png",
        bbox=(100, 100, 20, 20),
        confidence=1.0,
        template_size=(20, 20),
    )
    stop = VisualAssetMatch(
        asset_kind=VisualAssetKind.STOP,
        state=VisualGuiState.RUNNING,
        template_path="stop.png",
        bbox=(100, 100, 20, 20),
        confidence=0.916,
        template_size=(20, 20),
    )

    selection = _select_state_match([send, stop], profile_id="chatgpt_mac")

    assert selection.state == VisualGuiState.COMPOSER_HAS_TEXT
    assert not selection.ambiguous
    assert selection.match == send
    assert selection.reason.startswith("selected_by_composite_score")


def test_chatgpt_mac_near_equal_states_are_ambiguous():
    idle = VisualAssetMatch(
        asset_kind=VisualAssetKind.SEND_DISABLED,
        state=VisualGuiState.IDLE,
        template_path="disabled.png",
        bbox=(100, 100, 20, 20),
        confidence=1.0,
        template_size=(20, 20),
    )
    send = VisualAssetMatch(
        asset_kind=VisualAssetKind.SEND,
        state=VisualGuiState.COMPOSER_HAS_TEXT,
        template_path="send.png",
        bbox=(100, 100, 20, 20),
        confidence=1.0,
        template_size=(20, 20),
    )

    selection = _select_state_match([idle, send], profile_id="chatgpt_mac")

    assert selection.state == VisualGuiState.AMBIGUOUS
    assert selection.ambiguous


def test_chatgpt_mac_appearance_score_resolves_disabled_send_to_idle():
    idle = VisualAssetMatch(
        asset_kind=VisualAssetKind.SEND_DISABLED,
        state=VisualGuiState.IDLE,
        template_path="disabled.png",
        bbox=(100, 100, 46, 47),
        confidence=1.0,
        template_size=(46, 47),
        appearance_score=0.2,
    )
    send = VisualAssetMatch(
        asset_kind=VisualAssetKind.SEND,
        state=VisualGuiState.COMPOSER_HAS_TEXT,
        template_path="send.png",
        bbox=(102, 101, 42, 45),
        confidence=0.999,
        template_size=(42, 45),
        appearance_score=70.0,
    )

    selection = _select_state_match([idle, send], profile_id="chatgpt_mac")

    assert selection.state == VisualGuiState.IDLE
    assert selection.match == idle
    assert selection.reason.startswith("selected_by_appearance_score")


def test_chatgpt_mac_appearance_score_resolves_enabled_send_to_composer_has_text():
    idle = VisualAssetMatch(
        asset_kind=VisualAssetKind.SEND_DISABLED,
        state=VisualGuiState.IDLE,
        template_path="disabled.png",
        bbox=(100, 100, 46, 47),
        confidence=0.999,
        template_size=(46, 47),
        appearance_score=68.0,
    )
    send = VisualAssetMatch(
        asset_kind=VisualAssetKind.SEND,
        state=VisualGuiState.COMPOSER_HAS_TEXT,
        template_path="send.png",
        bbox=(102, 101, 42, 45),
        confidence=1.0,
        template_size=(42, 45),
        appearance_score=0.4,
    )

    selection = _select_state_match([idle, send], profile_id="chatgpt_mac")

    assert selection.state == VisualGuiState.COMPOSER_HAS_TEXT
    assert selection.match == send
    assert selection.reason.startswith("selected_by_appearance_score")


def test_chatgpt_mac_appearance_score_keeps_ambiguous_when_not_calibrated():
    idle = VisualAssetMatch(
        asset_kind=VisualAssetKind.SEND_DISABLED,
        state=VisualGuiState.IDLE,
        template_path="disabled.png",
        bbox=(100, 100, 46, 47),
        confidence=1.0,
        template_size=(46, 47),
        appearance_score=12.0,
    )
    send = VisualAssetMatch(
        asset_kind=VisualAssetKind.SEND,
        state=VisualGuiState.COMPOSER_HAS_TEXT,
        template_path="send.png",
        bbox=(102, 101, 42, 45),
        confidence=0.999,
        template_size=(42, 45),
        appearance_score=20.0,
    )

    selection = _select_state_match([idle, send], profile_id="chatgpt_mac")

    assert selection.state == VisualGuiState.AMBIGUOUS
    assert selection.ambiguous


def test_chatgpt_mac_appearance_score_requires_same_button_region():
    idle = VisualAssetMatch(
        asset_kind=VisualAssetKind.SEND_DISABLED,
        state=VisualGuiState.IDLE,
        template_path="disabled.png",
        bbox=(100, 100, 46, 47),
        confidence=1.0,
        template_size=(46, 47),
        appearance_score=0.2,
    )
    send = VisualAssetMatch(
        asset_kind=VisualAssetKind.SEND,
        state=VisualGuiState.COMPOSER_HAS_TEXT,
        template_path="send.png",
        bbox=(300, 100, 42, 45),
        confidence=0.999,
        template_size=(42, 45),
        appearance_score=70.0,
    )

    selection = _select_state_match([idle, send], profile_id="chatgpt_mac")

    assert selection.state == VisualGuiState.AMBIGUOUS
    assert selection.ambiguous


def test_chatgpt_mac_score_gap_selects_stronger_state():
    idle = VisualAssetMatch(
        asset_kind=VisualAssetKind.SEND_DISABLED,
        state=VisualGuiState.IDLE,
        template_path="disabled.png",
        bbox=(100, 100, 20, 20),
        confidence=1.0,
        template_size=(20, 20),
    )
    send = VisualAssetMatch(
        asset_kind=VisualAssetKind.SEND,
        state=VisualGuiState.COMPOSER_HAS_TEXT,
        template_path="send.png",
        bbox=(100, 100, 20, 20),
        confidence=0.90,
        template_size=(20, 20),
    )

    selection = _select_state_match(
        [idle, send],
        profile_id="chatgpt_mac",
        ambiguity_margin=0.03,
    )

    assert selection.state == VisualGuiState.IDLE
    assert not selection.ambiguous


def test_chatgpt_mac_ambiguity_margin_is_configurable():
    idle = VisualAssetMatch(
        asset_kind=VisualAssetKind.SEND_DISABLED,
        state=VisualGuiState.IDLE,
        template_path="disabled.png",
        bbox=(100, 100, 20, 20),
        confidence=0.96,
        template_size=(20, 20),
    )
    send = VisualAssetMatch(
        asset_kind=VisualAssetKind.SEND,
        state=VisualGuiState.COMPOSER_HAS_TEXT,
        template_path="send.png",
        bbox=(100, 100, 20, 20),
        confidence=1.0,
        template_size=(20, 20),
    )

    selection = _select_state_match(
        [idle, send],
        profile_id="chatgpt_mac",
        ambiguity_margin=0.05,
    )

    assert selection.state == VisualGuiState.AMBIGUOUS
    assert selection.ambiguous


def test_composite_match_scores_select_send_over_stop_background_similarity(tmp_path: Path):
    send = tmp_path / "send.png"
    stop = tmp_path / "stop.png"
    make_template(send, "send")
    make_template(stop, "stop")
    detector = AssetVisualStateDetector(screenshot_provider=lambda _region: screenshot_with(send))

    result = detector.detect(
        target=target(
            visual_send_templates=(str(send),),
            visual_stop_templates=(str(stop),),
        ),
        window_bounds=WINDOW,
    )

    assert result.matched_state == VisualGuiState.COMPOSER_HAS_TEXT
    diagnostics = {diagnostic.template_path: diagnostic for diagnostic in result.template_diagnostics}
    assert diagnostics[str(send)].accepted
    assert diagnostics[str(send)].edge_score is not None
    assert diagnostics[str(send)].glyph_score is not None
    assert diagnostics[str(send)].composite_score is not None
    assert not diagnostics[str(stop)].accepted


def test_composite_match_scores_select_stop_over_send_background_similarity(tmp_path: Path):
    send = tmp_path / "send.png"
    stop = tmp_path / "stop.png"
    make_template(send, "send")
    make_template(stop, "stop")
    detector = AssetVisualStateDetector(screenshot_provider=lambda _region: screenshot_with(stop))

    result = detector.detect(
        target=target(
            visual_send_templates=(str(send),),
            visual_stop_templates=(str(stop),),
        ),
        window_bounds=WINDOW,
    )

    assert result.matched_state == VisualGuiState.RUNNING
    diagnostics = {diagnostic.template_path: diagnostic for diagnostic in result.template_diagnostics}
    assert diagnostics[str(stop)].accepted
    assert not diagnostics[str(send)].accepted


def test_flat_button_background_alone_does_not_pass_composite_threshold(tmp_path: Path):
    from PIL import Image

    flat = tmp_path / "flat.png"
    Image.new("RGB", (24, 24), "white").save(flat)
    detector = AssetVisualStateDetector(screenshot_provider=lambda _region: blank_screenshot())

    result = detector.detect(
        target=target(
            visual_send_templates=(str(flat),),
            visual_state_confidence_threshold=0.70,
        ),
        window_bounds=WINDOW,
    )

    diagnostic = next(
        item for item in result.template_diagnostics if item.template_path == str(flat)
    )
    assert result.matched_state == VisualGuiState.UNKNOWN
    assert diagnostic.best_match_confidence is not None
    assert diagnostic.composite_score is not None
    assert diagnostic.composite_score < diagnostic.threshold
    assert diagnostic.rejection_reason == "glyph_edge_mismatch"


def test_plus_maps_to_composer_anchor_without_mixing_app_assets(tmp_path: Path):
    chatgpt_template = tmp_path / "chatgpt_plus.png"
    codex_template = tmp_path / "codex_plus.png"
    make_template(chatgpt_template, "plus")
    make_template(codex_template, "stop")
    detector = AssetVisualStateDetector(
        screenshot_provider=lambda _region: screenshot_with(chatgpt_template, location=(130, 450))
    )

    chatgpt_result = detector.detect(
        target=target(
                app_name="ChatGPT",
                visual_asset_profile="chatgpt_mac",
                visual_plus_templates=(str(chatgpt_template),),
                visual_plus_confidence_threshold=0.0,
            ),
        window_bounds=WINDOW,
    )
    codex_result = detector.detect(
        target=target(visual_plus_templates=(str(codex_template),)),
        window_bounds=WINDOW,
    )

    assert chatgpt_result.plus_anchor_found
    assert chatgpt_result.matched_state == VisualGuiState.UNKNOWN
    assert not codex_result.plus_anchor_found


def test_chatgpt_mac_plus_search_region_is_bounded_to_lower_left():
    region = visual_asset_search_region(
        WINDOW,
        target(app_name="ChatGPT", visual_asset_profile="chatgpt_mac"),
        "chatgpt_mac",
        VisualAssetKind.PLUS,
        default_region=visual_state_search_region(WINDOW, target()),
    )

    assert region.as_tuple() == (110, 428, 120, 72)


def test_chatgpt_mac_state_search_region_is_bounded_to_lower_right_controls():
    region = visual_asset_search_region(
        WINDOW,
        target(app_name="ChatGPT", visual_asset_profile="chatgpt_mac"),
        "chatgpt_mac",
        VisualAssetKind.SEND_DISABLED,
        default_region=visual_state_search_region(WINDOW, target()),
    )

    assert region.as_tuple() == (490, 404, 104, 96)


def test_chatgpt_chrome_app_search_regions_are_window_relative():
    chrome_target = target(app_name="ChatGPT", visual_asset_profile="chatgpt_chrome_app")
    state_region = visual_state_search_region(WINDOW, chrome_target)
    plus_region = visual_asset_search_region(
        WINDOW,
        chrome_target,
        "chatgpt_chrome_app",
        VisualAssetKind.PLUS,
        default_region=state_region,
    )

    assert state_region.as_tuple() == (110, 320, 480, 180)
    assert plus_region.as_tuple() == (120, 400, 329, 100)


def test_chatgpt_mac_missing_plus_asset_reports_template_missing(tmp_path: Path):
    missing = tmp_path / "missing_plus.png"
    detector = AssetVisualStateDetector(screenshot_provider=lambda _region: screenshot_with_many([]))

    result = detector.detect(
        target=target(
            app_name="ChatGPT",
            visual_asset_profile="chatgpt_mac",
            visual_plus_templates=(str(missing),),
        ),
        window_bounds=WINDOW,
    )

    diagnostics = {diagnostic.template_path: diagnostic for diagnostic in result.template_diagnostics}
    assert diagnostics[str(missing)].asset_kind == VisualAssetKind.PLUS
    assert diagnostics[str(missing)].rejection_reason == "template_missing"


def test_plus_asset_is_not_used_as_state_signal(tmp_path: Path):
    plus = tmp_path / "plus.png"
    make_template(plus, "plus")
    detector = AssetVisualStateDetector(screenshot_provider=lambda _region: screenshot_with(plus))

    result = detector.detect(target=target(visual_plus_templates=(str(plus),)), window_bounds=WINDOW)

    assert result.plus_anchor_found
    assert result.matched_state == VisualGuiState.UNKNOWN
    assert result.matched_asset_path is None


def test_per_template_diagnostics_report_low_confidence_and_missing_assets(tmp_path: Path):
    template = tmp_path / "send.png"
    missing = tmp_path / "missing.png"
    make_template(template, "send")
    detector = AssetVisualStateDetector(screenshot_provider=lambda _region: patterned_screenshot())

    result = detector.detect(
        target=target(
            visual_state_confidence_threshold=1.01,
            visual_send_templates=(str(template), str(missing)),
        ),
        window_bounds=WINDOW,
    )

    assert result.matched_state == VisualGuiState.UNKNOWN
    diagnostics = {diagnostic.template_path: diagnostic for diagnostic in result.template_diagnostics}
    assert diagnostics[str(template)].best_match_confidence is not None
    assert diagnostics[str(template)].appearance_score is not None
    assert diagnostics[str(template)].rejection_reason in {
        "confidence_below_threshold",
        "glyph_edge_mismatch",
    }
    assert diagnostics[str(missing)].rejection_reason == "template_missing"


def test_visual_state_threshold_cap_is_reported_and_used(tmp_path: Path):
    template = tmp_path / "send.png"
    make_template(template, "send")
    detector = AssetVisualStateDetector(screenshot_provider=lambda _region: screenshot_with(template))

    result = detector.detect(
        target=target(
            visual_state_confidence_threshold=0.85,
            visual_send_templates=(str(template),),
        ),
        window_bounds=WINDOW,
    )

    diagnostic = next(
        item for item in result.template_diagnostics if item.template_path == str(template)
    )
    assert diagnostic.configured_threshold == 0.85
    assert diagnostic.effective_threshold == 0.70
    assert diagnostic.threshold == 0.70
    assert diagnostic.threshold_cap_applied
    assert diagnostic.accepted


def test_visual_plus_threshold_below_cap_is_preserved(tmp_path: Path):
    template = tmp_path / "plus.png"
    make_template(template, "plus")
    detector = AssetVisualStateDetector(screenshot_provider=lambda _region: screenshot_with(template))

    result = detector.detect(
        target=target(
            visual_plus_confidence_threshold=0.58,
            visual_plus_templates=(str(template),),
        ),
        window_bounds=WINDOW,
    )

    diagnostic = next(
        item for item in result.template_diagnostics if item.template_path == str(template)
    )
    assert diagnostic.configured_threshold == 0.58
    assert diagnostic.effective_threshold == 0.58
    assert diagnostic.threshold == 0.58
    assert not diagnostic.threshold_cap_applied


def test_multiscale_visual_state_reports_selected_scale(tmp_path: Path):
    template = tmp_path / "send.png"
    make_template(template, "send")
    detector = AssetVisualStateDetector(
        screenshot_provider=lambda _region: screenshot_with_scaled(template, scale=1.25)
    )

    result = detector.detect(
        target=target(
            visual_send_templates=(str(template),),
            visual_plus_multiscale_enabled=True,
            visual_scale_min=0.75,
            visual_scale_max=1.35,
            visual_scale_step=0.05,
        ),
        window_bounds=WINDOW,
    )

    assert result.matched_state == VisualGuiState.COMPOSER_HAS_TEXT
    diagnostics = {diagnostic.template_path: diagnostic for diagnostic in result.template_diagnostics}
    assert diagnostics[str(template)].selected_scale is not None
    assert diagnostics[str(template)].selected_scale != 1.0
    assert diagnostics[str(template)].original_template_size == (24, 24)
    assert diagnostics[str(template)].template_size != diagnostics[str(template)].original_template_size


def test_appearance_score_threshold_rejects_visual_false_positive(tmp_path: Path):
    template = tmp_path / "send.png"
    make_template(template, "send")
    detector = AssetVisualStateDetector(screenshot_provider=lambda _region: screenshot_with(template))

    result = detector.detect(
        target=target(
            visual_send_templates=(str(template),),
            visual_appearance_score_threshold=-1.0,
        ),
        window_bounds=WINDOW,
    )

    assert result.matched_state == VisualGuiState.UNKNOWN
    diagnostics = {diagnostic.template_path: diagnostic for diagnostic in result.template_diagnostics}
    assert diagnostics[str(template)].rejection_reason == "appearance_score_above_threshold"


def test_visual_state_screenshot_is_window_bounded(tmp_path: Path):
    template = tmp_path / "send.png"
    make_template(template, "send")
    captured: list[tuple[int, int, int, int]] = []

    def provider(region):
        captured.append(region)
        return screenshot_with(template)

    detector = AssetVisualStateDetector(screenshot_provider=provider)

    result = detector.detect(target=target(visual_send_templates=(str(template),)), window_bounds=WINDOW)

    assert captured == [WINDOW]
    assert result.safe_region_bounds is not None
    assert result.matched_state == VisualGuiState.COMPOSER_HAS_TEXT


def test_lower_control_band_includes_right_side_state_controls(tmp_path: Path):
    template = tmp_path / "stop.png"
    make_template(template, "stop")
    detector = AssetVisualStateDetector(
        screenshot_provider=lambda _region: screenshot_with(template, location=(465, 290))
    )

    result = detector.detect(target=target(visual_stop_templates=(str(template),)), window_bounds=WINDOW)

    assert result.safe_region_bounds == (110, 340, 480, 160)
    assert result.matched_state == VisualGuiState.RUNNING


def test_lower_composer_band_uses_plus_anchor_region():
    region = visual_state_search_region(
        WINDOW,
        target(visual_state_search_region="lower_composer_band"),
    )

    assert region.as_tuple() == (120, 340, 440, 160)


def test_idle_wait_polls_every_10_seconds_until_idle():
    detections = [
        SimpleNamespace(
            selected_app="Codex",
            asset_profile="codex",
            matched_state=VisualGuiState.RUNNING,
            plus_anchor_found=True,
        ),
        SimpleNamespace(
            selected_app="Codex",
            asset_profile="codex",
            matched_state=VisualGuiState.COMPOSER_HAS_TEXT,
            plus_anchor_found=True,
        ),
        SimpleNamespace(
            selected_app="Codex",
            asset_profile="codex",
            matched_state=VisualGuiState.IDLE,
            plus_anchor_found=True,
        ),
    ]
    current_time = 0.0
    sleeps: list[float] = []

    def monotonic() -> float:
        return current_time

    def sleep(seconds: float) -> None:
        nonlocal current_time
        sleeps.append(seconds)
        current_time += seconds

    def detect_once():
        return detections.pop(0)

    result = wait_for_visual_idle(
        target=target(),
        detect_once=detect_once,
        timeout_seconds=600,
        poll_interval_seconds=10,
        sleep_fn=sleep,
        monotonic_fn=monotonic,
    )

    assert result.should_proceed
    assert result.poll_count == 2
    assert sleeps == [10, 10]


def test_ambiguous_visual_state_blocks_without_timeout():
    detections = [
        SimpleNamespace(
            selected_app="ChatGPT",
            asset_profile="chatgpt_mac",
            matched_state=VisualGuiState.AMBIGUOUS,
            state_selection_reason="ambiguous_near_equal_state_matches",
            plus_anchor_found=True,
        )
    ]
    slept: list[float] = []

    result = wait_for_visual_idle(
        target=target(app_name="ChatGPT", visual_asset_profile="chatgpt_mac"),
        detect_once=lambda: detections.pop(0),
        timeout_seconds=600,
        poll_interval_seconds=10,
        sleep_fn=lambda seconds: slept.append(seconds),
        monotonic_fn=lambda: 0.0,
    )

    assert result.should_abort
    assert not result.should_proceed
    assert slept == []


def test_idle_wait_timeout_overwrite_requires_plus_anchor():
    current_time = 0.0

    def monotonic() -> float:
        return current_time

    def sleep(seconds: float) -> None:
        nonlocal current_time
        current_time += seconds

    def detect_once():
        return SimpleNamespace(
            selected_app="Codex",
            asset_profile="codex",
            matched_state=VisualGuiState.COMPOSER_HAS_TEXT,
            plus_anchor_found=True,
        )

    result = wait_for_visual_idle(
        target=target(),
        detect_once=detect_once,
        timeout_seconds=20,
        poll_interval_seconds=10,
        on_timeout="overwrite",
        sleep_fn=sleep,
        monotonic_fn=monotonic,
    )

    assert result.should_overwrite
    assert not result.should_abort


def test_idle_wait_timeout_abort_policy():
    current_time = 0.0

    def monotonic() -> float:
        return current_time

    def sleep(seconds: float) -> None:
        nonlocal current_time
        current_time += seconds

    def detect_once():
        return SimpleNamespace(
            selected_app="Codex",
            asset_profile="codex",
            matched_state=VisualGuiState.UNKNOWN,
            plus_anchor_found=True,
        )

    result = wait_for_visual_idle(
        target=target(),
        detect_once=detect_once,
        timeout_seconds=10,
        poll_interval_seconds=10,
        on_timeout="abort",
        sleep_fn=sleep,
        monotonic_fn=monotonic,
    )

    assert result.should_abort
    assert not result.should_overwrite


def test_pyautogui_click_path_is_used(monkeypatch):
    clicks: list[tuple[int, int]] = []
    monkeypatch.setitem(
        sys.modules,
        "pyautogui",
        SimpleNamespace(click=lambda x, y: clicks.append((x, y))),
    )

    adapter = MacOSSystemEventsGuiAdapter()
    adapter._click_point((123, 456))

    assert clicks == [(123, 456)]
