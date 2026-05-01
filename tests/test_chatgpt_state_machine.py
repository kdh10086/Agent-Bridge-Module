from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_bridge.core.event_log import EventLog
from agent_bridge.gui.chatgpt_state_machine import (
    ChatGPTDomState,
    ChatGPTStateMachineError,
    DEFAULT_CHATGPT_IDLE_EMPTY_POLL_INTERVAL_SECONDS,
    DEFAULT_CHATGPT_IDLE_EMPTY_TIMEOUT_SECONDS,
    ResponseCopyResult,
    MacOSChromeJavaScriptDomClient,
    click_send_button,
    copy_response_with_strategies,
    detect_state_from_html,
    focus_composer,
    insert_text_into_composer,
    query_dom_state,
    wait_for_idle_empty_composer,
    wait_for_response_copy_ready,
    wait_for_send_ready,
)
from agent_bridge.gui.clipboard import Clipboard


class StaticDom:
    def __init__(self, state: ChatGPTDomState):
        self.state = state
        self.scripts: list[str] = []

    def evaluate_javascript(self, script: str) -> str:
        self.scripts.append(script)
        return json.dumps(
            {
                "composer_empty": self.state.composer_empty,
                "send_ready": self.state.send_ready,
                "streaming": self.state.streaming,
                "response_copy_ready": self.state.response_copy_ready,
                "copy_button_count": self.state.copy_button_count,
                "composer_text_length": self.state.composer_text_length,
                "button_state": self.state.button_state,
                "composer_selector": self.state.composer_selector,
                "active_element_summary": self.state.active_element_summary,
            }
        )


class SequenceDom:
    def __init__(self, states: list[ChatGPTDomState]):
        self.states = states
        self.index = 0

    def evaluate_javascript(self, script: str) -> str:
        state = self.states[min(self.index, len(self.states) - 1)]
        self.index += 1
        return json.dumps(
            {
                "composer_empty": state.composer_empty,
                "send_ready": state.send_ready,
                "streaming": state.streaming,
                "response_copy_ready": state.response_copy_ready,
                "copy_button_count": state.copy_button_count,
                "composer_text_length": state.composer_text_length,
                "button_state": state.button_state,
                "composer_selector": state.composer_selector,
                "active_element_summary": state.active_element_summary,
            }
        )


class FakeClipboard(Clipboard):
    def __init__(self, text: str = "before"):
        self.text = text

    def copy_text(self, text: str) -> None:
        self.text = text

    def read_text(self) -> str:
        return self.text


class CopyStrategyDom:
    def __init__(
        self,
        clipboard: FakeClipboard,
        *,
        success_strategy: str | None,
        copied_text: str = "```CODEX_NEXT_PROMPT\nok\n```",
    ):
        self.clipboard = clipboard
        self.success_strategy = success_strategy
        self.copied_text = copied_text
        self.attempted: list[str] = []

    def evaluate_javascript(self, script: str) -> str:
        strategy = self._strategy_name(script)
        self.attempted.append(strategy)
        if strategy == self.success_strategy:
            self.clipboard.text = self.copied_text
            return "clicked"
        return "missing"

    def _strategy_name(self, script: str) -> str:
        if "latestButton" in script:
            return "latest_copy_button"
        if "document.querySelector" in script:
            return "owner_css_selector"
        if "/html/body" in script:
            return "owner_full_xpath"
        if "section[154]" in script:
            return "owner_xpath"
        return "unknown"


class RichResponseCopyDom:
    def __init__(
        self,
        clipboard: FakeClipboard,
        *,
        click_changes_clipboard: bool,
        fallback_text: str = "```CODEX_NEXT_PROMPT\nfallback\n```",
        fallback_found: bool = True,
    ):
        self.clipboard = clipboard
        self.click_changes_clipboard = click_changes_clipboard
        self.fallback_text = fallback_text
        self.fallback_found = fallback_found
        self.scripts: list[str] = []

    def evaluate_javascript(self, script: str) -> str:
        self.scripts.append(script)
        if "extract_latest_assistant_response_text" in script:
            return json.dumps(
                {
                    "found": self.fallback_found,
                    "text": self.fallback_text,
                    "text_length": len(self.fallback_text),
                    "copy_button_count": 3,
                    "container_summary": 'section[role=assistant] text="latest assistant"',
                    "error": None if self.fallback_found else "missing latest assistant response container",
                }
            )
        if "selected_copy_button_index" in script:
            if self.click_changes_clipboard:
                self.clipboard.text = "```CODEX_NEXT_PROMPT\nbutton copy\n```"
            return json.dumps(
                {
                    "clicked": True,
                    "copy_button_count": 3,
                    "selected_copy_button_index": 2,
                    "container_summary": 'section[role=assistant] text="latest assistant"',
                    "selected_button_summary": 'button[data-testid=copy-turn-action-button]',
                    "error": None,
                }
            )
        return "missing"


class ScriptedDom:
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.scripts: list[str] = []

    def evaluate_javascript(self, script: str) -> str:
        self.scripts.append(script)
        if not self.responses:
            raise AssertionError("No scripted DOM response available.")
        return self.responses.pop(0)


def composer_response(
    *,
    found: bool = True,
    selector: str | None = "textarea",
    active_inside: bool = True,
    active_element_summary: str = "textarea#prompt-textarea",
    text_length: int = 0,
    text: str = "",
    button_state: str = "speech-button",
) -> str:
    return json.dumps(
        {
            "found": found,
            "selector": selector,
            "active_inside": active_inside,
            "active_element_summary": active_element_summary,
            "text_length": text_length,
            "text": text,
            "button_state": button_state,
        }
    )


def test_empty_composer_detected_from_html():
    state = detect_state_from_html('<button data-testid="composer-speech-button" aria-label="Voice 시작">')

    assert state.composer_empty
    assert not state.send_ready


def test_send_ready_detected_from_html():
    state = detect_state_from_html(
        '<button id="composer-submit-button" data-testid="send-button" aria-label="프롬프트 보내기">'
    )

    assert state.send_ready


def test_streaming_detected_from_html():
    state = detect_state_from_html(
        '<button id="composer-submit-button" data-testid="stop-button" aria-label="스트리밍 중지">'
    )

    assert state.streaming


def test_response_copy_ready_detected_from_html():
    state = detect_state_from_html('<button data-testid="copy-turn-action-button" aria-label="응답 복사">')

    assert state.response_copy_ready
    assert state.copy_button_count == 1


def test_query_dom_state_maps_json_response():
    state = query_dom_state(StaticDom(ChatGPTDomState(send_ready=True, copy_button_count=2)))

    assert state.send_ready
    assert state.copy_button_count == 2


def test_submit_blocked_when_only_voice_button_is_present():
    with pytest.raises(ChatGPTStateMachineError, match="did not enter send-ready"):
        wait_for_send_ready(
            StaticDom(ChatGPTDomState(composer_empty=True)),
            timeout_seconds=0.001,
            sleep_fn=lambda _: None,
        )


def test_send_ready_wait_succeeds_when_send_button_appears():
    state = wait_for_send_ready(
        SequenceDom(
            [
                ChatGPTDomState(composer_empty=True),
                ChatGPTDomState(send_ready=True),
            ]
        ),
        timeout_seconds=1,
        sleep_fn=lambda _: None,
    )

    assert state.send_ready


def test_default_idle_empty_timeout_is_600_seconds():
    assert DEFAULT_CHATGPT_IDLE_EMPTY_TIMEOUT_SECONDS == 600


def test_default_idle_empty_poll_interval_is_10_seconds():
    assert DEFAULT_CHATGPT_IDLE_EMPTY_POLL_INTERVAL_SECONDS == 10


def test_idle_empty_wait_proceeds_only_when_speech_button_state_appears(tmp_path: Path):
    log = EventLog(tmp_path / "bridge.jsonl")
    dom = SequenceDom(
        [
            ChatGPTDomState(streaming=True, button_state="stop-button"),
            ChatGPTDomState(send_ready=True, button_state="send-button"),
            ChatGPTDomState(composer_empty=True, button_state="speech-button"),
        ]
    )

    state = wait_for_idle_empty_composer(
        dom,
        timeout_seconds=600,
        sleep_fn=lambda _: None,
        monotonic_fn=lambda: 0.0,
        event_log=log,
    )

    assert state.composer_empty
    log_text = log.path.read_text(encoding="utf-8")
    assert "pm_idle_empty_wait_started" in log_text
    assert "pm_idle_empty_poll" in log_text
    assert "pm_streaming_state_observed_before_paste" in log_text
    assert "pm_user_pending_message_observed_before_paste" in log_text
    assert "pm_idle_empty_detected" in log_text


def test_idle_empty_wait_rechecks_every_10_seconds_before_timeout(tmp_path: Path):
    sleeps: list[float] = []
    times = iter([0.0, 0.0, 10.0, 20.0, 601.0])
    log = EventLog(tmp_path / "bridge.jsonl")
    dom = SequenceDom(
        [
            ChatGPTDomState(streaming=True, button_state="stop-button"),
            ChatGPTDomState(streaming=True, button_state="stop-button"),
            ChatGPTDomState(send_ready=True, button_state="send-button"),
        ]
    )

    with pytest.raises(ChatGPTStateMachineError):
        wait_for_idle_empty_composer(
            dom,
            timeout_seconds=600,
            sleep_fn=lambda seconds: sleeps.append(seconds),
            monotonic_fn=lambda: next(times),
            event_log=log,
        )

    assert sleeps == [10, 10, 10]
    assert dom.index == 3
    log_text = log.path.read_text(encoding="utf-8")
    assert '"elapsed_seconds": 20.0' in log_text
    assert '"remaining_seconds": 580.0' in log_text


def test_idle_empty_wait_uses_custom_poll_interval():
    sleeps: list[float] = []
    times = iter([0.0, 0.0, 5.0, 601.0])

    with pytest.raises(ChatGPTStateMachineError):
        wait_for_idle_empty_composer(
            SequenceDom(
                [
                    ChatGPTDomState(send_ready=True, button_state="send-button"),
                    ChatGPTDomState(send_ready=True, button_state="send-button"),
                ]
            ),
            timeout_seconds=600,
            poll_interval_seconds=5,
            sleep_fn=lambda seconds: sleeps.append(seconds),
            monotonic_fn=lambda: next(times),
        )

    assert sleeps == [5, 5]


def test_idle_empty_wait_proceeds_when_speech_button_appears_on_later_poll():
    sleeps: list[float] = []
    times = iter([0.0, 0.0, 10.0, 20.0])
    dom = SequenceDom(
        [
            ChatGPTDomState(streaming=True, button_state="stop-button"),
            ChatGPTDomState(send_ready=True, button_state="send-button"),
            ChatGPTDomState(composer_empty=True, button_state="speech-button"),
        ]
    )

    state = wait_for_idle_empty_composer(
        dom,
        timeout_seconds=600,
        sleep_fn=lambda seconds: sleeps.append(seconds),
        monotonic_fn=lambda: next(times),
    )

    assert state.composer_empty
    assert sleeps == [10, 10]
    assert dom.index == 3


def test_idle_empty_wait_timeout_reports_600_seconds_and_last_state(tmp_path: Path):
    times = iter([0.0, 0.0, 601.0])
    log = EventLog(tmp_path / "bridge.jsonl")

    with pytest.raises(ChatGPTStateMachineError) as error:
        wait_for_idle_empty_composer(
            StaticDom(ChatGPTDomState(send_ready=True, button_state="send-button")),
            timeout_seconds=600,
            sleep_fn=lambda _: None,
            monotonic_fn=lambda: next(times),
            event_log=log,
        )

    message = str(error.value)
    assert "within 600 seconds" in message
    assert "last_observed_state=user_pending_message" in message
    assert "pm_idle_empty_wait_timeout" in log.path.read_text(encoding="utf-8")


def test_idle_empty_wait_timeout_reports_streaming_state():
    times = iter([0.0, 0.0, 601.0])

    with pytest.raises(ChatGPTStateMachineError) as error:
        wait_for_idle_empty_composer(
            StaticDom(ChatGPTDomState(streaming=True, button_state="stop-button")),
            timeout_seconds=600,
            sleep_fn=lambda _: None,
            monotonic_fn=lambda: next(times),
        )

    assert "last_observed_state=streaming" in str(error.value)


def test_wait_for_send_ready_failure_includes_diagnostics():
    with pytest.raises(ChatGPTStateMachineError) as error:
        wait_for_send_ready(
            StaticDom(
                ChatGPTDomState(
                    composer_empty=True,
                    composer_text_length=42,
                    button_state="speech-button",
                    composer_selector="textarea",
                    active_element_summary="textarea#prompt-textarea",
                )
            ),
            timeout_seconds=0.001,
            sleep_fn=lambda _: None,
        )

    message = str(error.value)
    assert "composer_text_length=42" in message
    assert "button_state=speech-button" in message


def test_copy_wait_blocks_while_streaming_then_allows_copy_ready():
    state = wait_for_response_copy_ready(
        SequenceDom(
            [
                ChatGPTDomState(streaming=True),
                ChatGPTDomState(response_copy_ready=True, copy_button_count=3),
            ]
        ),
        timeout_seconds=1,
        sleep_fn=lambda _: None,
    )

    assert state.response_copy_ready
    assert state.copy_button_count == 3


def test_composer_focus_succeeds_for_textarea():
    dom = ScriptedDom([composer_response(selector="textarea")])

    result = focus_composer(dom)

    assert result.selector == "textarea"
    assert result.active_inside
    assert "textarea" in dom.scripts[0]


def test_composer_focus_succeeds_for_contenteditable():
    dom = ScriptedDom([composer_response(selector='div[contenteditable="true"]')])

    result = focus_composer(dom)

    assert result.selector == 'div[contenteditable="true"]'
    assert result.active_inside
    assert 'contenteditable="true"' in dom.scripts[0]


def test_missing_composer_fails_clearly():
    dom = ScriptedDom(
        [
            composer_response(
                found=False,
                selector=None,
                active_element_summary="body",
                button_state="unknown",
            )
        ]
    )

    with pytest.raises(ChatGPTStateMachineError, match="composer was not found"):
        focus_composer(dom)


def test_dom_insertion_dispatches_input_event_and_verifies_marker():
    dom = ScriptedDom(
        [
            composer_response(selector="textarea"),
            composer_response(
                selector="textarea",
                text="AGENT_BRIDGE test prompt",
                text_length=len("AGENT_BRIDGE test prompt"),
                button_state="send-button",
            ),
        ]
    )

    result = insert_text_into_composer(
        dom,
        "AGENT_BRIDGE test prompt",
        expected_marker="AGENT_BRIDGE",
    )

    assert result.text_length == len("AGENT_BRIDGE test prompt")
    assert result.contains_expected_marker
    assert "new InputEvent('input'" in dom.scripts[1]
    assert "dispatchEvent" in dom.scripts[1]


def test_paste_verification_detects_missing_marker():
    dom = ScriptedDom(
        [
            composer_response(selector="textarea"),
            composer_response(
                selector="textarea",
                text="different text",
                text_length=len("different text"),
                button_state="send-button",
            ),
        ]
    )

    with pytest.raises(ChatGPTStateMachineError, match="expected marker was missing"):
        insert_text_into_composer(dom, "AGENT_BRIDGE test prompt", expected_marker="AGENT_BRIDGE")


def test_send_ready_wait_succeeds_after_text_insertion_state():
    state = wait_for_send_ready(
        SequenceDom(
            [
                ChatGPTDomState(
                    send_ready=False,
                    composer_text_length=20,
                    button_state="speech-button",
                ),
                ChatGPTDomState(
                    send_ready=True,
                    composer_text_length=20,
                    button_state="send-button",
                ),
            ]
        ),
        timeout_seconds=1,
        sleep_fn=lambda _: None,
    )

    assert state.send_ready


def test_click_send_button_blocks_speech_button():
    dom = ScriptedDom(["speech-button"])

    with pytest.raises(ChatGPTStateMachineError, match="send button"):
        click_send_button(dom)


def test_click_send_button_allows_send_button():
    dom = ScriptedDom(["clicked"])

    click_send_button(dom)


def assert_strategy_result(result: ResponseCopyResult, strategy: str) -> None:
    assert result.strategy == strategy
    assert "CODEX_NEXT_PROMPT" in result.text


def test_latest_copy_button_strategy_succeeds(tmp_path: Path):
    clipboard = FakeClipboard()
    log = EventLog(tmp_path / "bridge.jsonl")

    result = copy_response_with_strategies(
        dom=CopyStrategyDom(clipboard, success_strategy="latest_copy_button"),
        clipboard=clipboard,
        expected_marker="CODEX_NEXT_PROMPT",
        event_log=log,
    )

    assert_strategy_result(result, "latest_copy_button")
    assert "pm_response_copy_strategy_succeeded" in log.path.read_text(encoding="utf-8")


def test_latest_copy_button_uses_latest_assistant_container_diagnostics(tmp_path: Path):
    clipboard = FakeClipboard()
    dom = RichResponseCopyDom(clipboard, click_changes_clipboard=True)
    log = EventLog(tmp_path / "bridge.jsonl")

    result = copy_response_with_strategies(
        dom=dom,
        clipboard=clipboard,
        expected_marker="CODEX_NEXT_PROMPT",
        event_log=log,
    )

    assert_strategy_result(result, "latest_copy_button")
    assert any('[data-message-author-role="assistant"]' in script for script in dom.scripts)
    log_text = log.path.read_text(encoding="utf-8")
    assert '"selected_copy_button_index": 2' in log_text
    assert "latest assistant" in log_text


def test_copy_button_no_change_triggers_dom_text_fallback(tmp_path: Path):
    clipboard = FakeClipboard("before")
    dom = RichResponseCopyDom(clipboard, click_changes_clipboard=False)
    log = EventLog(tmp_path / "bridge.jsonl")

    result = copy_response_with_strategies(
        dom=dom,
        clipboard=clipboard,
        expected_marker="CODEX_NEXT_PROMPT",
        event_log=log,
    )

    assert_strategy_result(result, "dom_text_fallback")
    assert clipboard.read_text() == "```CODEX_NEXT_PROMPT\nfallback\n```"
    assert any("extract_latest_assistant_response_text" in script for script in dom.scripts)
    log_text = log.path.read_text(encoding="utf-8")
    assert "Clipboard did not change after response copy attempt" in log_text
    assert '"strategy": "dom_text_fallback"' in log_text
    assert '"extracted_text_length": 33' in log_text


def test_dom_text_fallback_reconstructs_rendered_codex_next_prompt_fence():
    clipboard = FakeClipboard("before")
    dom = RichResponseCopyDom(
        clipboard,
        click_changes_clipboard=False,
        fallback_text="CODEX_NEXT_PROMPTTask ID: AB-ROUNDTRIP-NOOP\nConfirm receipt only.",
    )

    result = copy_response_with_strategies(
        dom=dom,
        clipboard=clipboard,
        expected_marker="CODEX_NEXT_PROMPT",
    )

    assert result.strategy == "dom_text_fallback"
    assert result.text.startswith("```CODEX_NEXT_PROMPT\n")
    assert result.text.endswith("\n```")
    assert "Task ID: AB-ROUNDTRIP-NOOP" in result.text
    assert "\nCODEX_NEXT_PROMPT\n" not in result.text


def test_dom_text_fallback_removes_duplicate_label_inside_reconstructed_fence():
    clipboard = FakeClipboard("before")
    dom = RichResponseCopyDom(
        clipboard,
        click_changes_clipboard=False,
        fallback_text=(
            "```CODEX_NEXT_PROMPT\n"
            "CODEX_NEXT_PROMPT\n"
            "Task ID: AB-035\n"
            "Confirm receipt only.\n"
            "```"
        ),
    )

    result = copy_response_with_strategies(
        dom=dom,
        clipboard=clipboard,
        expected_marker="CODEX_NEXT_PROMPT",
    )

    assert result.strategy == "dom_text_fallback"
    assert result.text == (
        "```CODEX_NEXT_PROMPT\n"
        "Task ID: AB-035\n"
        "Confirm receipt only.\n"
        "```"
    )


def test_dom_text_fallback_fails_when_extracted_text_is_empty():
    clipboard = FakeClipboard("before")
    dom = RichResponseCopyDom(clipboard, click_changes_clipboard=False, fallback_text="")

    with pytest.raises(ChatGPTStateMachineError, match="DOM text fallback extracted empty"):
        copy_response_with_strategies(dom=dom, clipboard=clipboard)


def test_dom_text_fallback_fails_when_expected_marker_is_missing():
    clipboard = FakeClipboard("before")
    dom = RichResponseCopyDom(
        clipboard,
        click_changes_clipboard=False,
        fallback_text="assistant response without fenced prompt",
    )

    with pytest.raises(ChatGPTStateMachineError, match="did not contain CODEX_NEXT_PROMPT"):
        copy_response_with_strategies(
            dom=dom,
            clipboard=clipboard,
            expected_marker="CODEX_NEXT_PROMPT",
        )


def test_owner_css_selector_fallback_succeeds():
    clipboard = FakeClipboard()

    result = copy_response_with_strategies(
        dom=CopyStrategyDom(clipboard, success_strategy="owner_css_selector"),
        clipboard=clipboard,
        expected_marker="CODEX_NEXT_PROMPT",
    )

    assert_strategy_result(result, "owner_css_selector")


def test_owner_xpath_fallback_succeeds():
    clipboard = FakeClipboard()

    result = copy_response_with_strategies(
        dom=CopyStrategyDom(clipboard, success_strategy="owner_xpath"),
        clipboard=clipboard,
        expected_marker="CODEX_NEXT_PROMPT",
    )

    assert_strategy_result(result, "owner_xpath")


def test_owner_full_xpath_fallback_succeeds():
    clipboard = FakeClipboard()

    result = copy_response_with_strategies(
        dom=CopyStrategyDom(clipboard, success_strategy="owner_full_xpath"),
        clipboard=clipboard,
        expected_marker="CODEX_NEXT_PROMPT",
    )

    assert_strategy_result(result, "owner_full_xpath")


def test_generic_fallback_used_when_selectors_fail():
    clipboard = FakeClipboard()

    result = copy_response_with_strategies(
        dom=CopyStrategyDom(clipboard, success_strategy=None),
        clipboard=clipboard,
        expected_marker="CODEX_NEXT_PROMPT",
        generic_copy_fallback=lambda: clipboard.copy_text("```CODEX_NEXT_PROMPT\nfallback\n```"),
    )

    assert_strategy_result(result, "generic_fallback")


def test_copy_fails_if_clipboard_does_not_change():
    clipboard = FakeClipboard("before")

    with pytest.raises(ChatGPTStateMachineError, match="Clipboard did not change"):
        copy_response_with_strategies(
            dom=CopyStrategyDom(
                clipboard,
                success_strategy="latest_copy_button",
                copied_text="before",
            ),
            clipboard=clipboard,
        )


def test_copy_fails_if_text_is_empty():
    clipboard = FakeClipboard("before")

    with pytest.raises(ChatGPTStateMachineError, match="empty"):
        copy_response_with_strategies(
            dom=CopyStrategyDom(clipboard, success_strategy="latest_copy_button", copied_text=""),
            clipboard=clipboard,
        )


def test_copy_fails_if_expected_marker_is_missing():
    clipboard = FakeClipboard("before")

    with pytest.raises(ChatGPTStateMachineError, match="did not contain CODEX_NEXT_PROMPT"):
        copy_response_with_strategies(
            dom=CopyStrategyDom(clipboard, success_strategy="latest_copy_button", copied_text="no marker"),
            clipboard=clipboard,
            expected_marker="CODEX_NEXT_PROMPT",
        )


def test_macos_chrome_dom_client_uses_nested_active_tab_javascript_syntax(monkeypatch):
    captured: dict[str, list[str]] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout="ready", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = MacOSChromeJavaScriptDomClient(app_name="Google Chrome").evaluate_javascript(
        """
        (() => {
          return document.readyState;
        })()
        """
    )

    apple_script = captured["command"][2]
    assert result == "ready"
    assert 'tell application "Google Chrome"' in apple_script
    assert "tell active tab of front window" in apple_script
    assert 'execute javascript "' in apple_script
    assert "in active tab of front window" not in apple_script
    assert "execute active tab of front window javascript" not in apple_script


def test_macos_chrome_dom_client_escapes_javascript_for_applescript(monkeypatch):
    captured: dict[str, list[str]] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    MacOSChromeJavaScriptDomClient(app_name='Google "Chrome"').evaluate_javascript(
        '(() => "quoted \\\\ value")()'
    )

    apple_script = captured["command"][2]
    assert 'tell application "Google \\"Chrome\\""' in apple_script
    assert '\\"quoted \\\\\\\\ value\\"' in apple_script
