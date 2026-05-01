from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from agent_bridge.core.event_log import EventLog
from agent_bridge.gui.clipboard import Clipboard


SEND_READY_SELECTORS = (
    '[data-testid="send-button"]',
    '#composer-submit-button',
    '[aria-label="프롬프트 보내기"]',
)
EMPTY_COMPOSER_SELECTORS = (
    '[data-testid="composer-speech-button"]',
    '[aria-label="Voice 시작"]',
)
STREAMING_SELECTORS = (
    '[data-testid="stop-button"]',
    '[aria-label="스트리밍 중지"]',
)
COPY_READY_SELECTORS = (
    '[data-testid="copy-turn-action-button"]',
    '[aria-label="응답 복사"]',
)
DEFAULT_CHATGPT_IDLE_EMPTY_TIMEOUT_SECONDS = 600
DEFAULT_CHATGPT_IDLE_EMPTY_POLL_INTERVAL_SECONDS = 10
COMPOSER_SELECTORS = (
    "textarea",
    'div[contenteditable="true"]',
    '[contenteditable="true"]',
    "#prompt-textarea",
    '[data-testid="composer-text-input"]',
)
OWNER_RESPONSE_COPY_CSS_SELECTOR = (
    "#thread > div > div.relative.basis-auto.flex-col.-mb-\\(--composer-overlap-px\\)"
    ".pb-\\(--composer-overlap-px\\).\\[--composer-overlap-px\\:28px\\].grow.flex > div > "
    "section:nth-child(154) > div > div > div.z-0.flex.min-h-\\[46px\\].justify-start > "
    "div > button:nth-child(1) > span > svg"
)
OWNER_RESPONSE_COPY_XPATH = (
    '//*[@id="thread"]/div/div[1]/div/section[154]/div/div/div[2]/div/button[1]/span/svg'
)
OWNER_RESPONSE_COPY_FULL_XPATH = (
    "/html/body/div[2]/div/div[1]/div/div[2]/div/main/div/div/div[1]/div/section[154]/div/div/"
    "div[2]/div/button[1]/span/svg"
)


class ChatGPTStateMachineError(RuntimeError):
    pass


class DomClient(Protocol):
    def evaluate_javascript(self, script: str) -> str:
        raise NotImplementedError


@dataclass(frozen=True)
class ChatGPTDomState:
    composer_empty: bool = False
    send_ready: bool = False
    streaming: bool = False
    response_copy_ready: bool = False
    copy_button_count: int = 0
    composer_text_length: int = 0
    button_state: str = "unknown"
    composer_selector: str | None = None
    active_element_summary: str = "unknown"


@dataclass(frozen=True)
class ComposerFocusResult:
    selector: str
    active_inside: bool
    active_element_summary: str
    text_length: int
    button_state: str


@dataclass(frozen=True)
class ComposerTextVerification:
    selector: str
    text_length: int
    contains_expected_marker: bool
    button_state: str
    active_element_summary: str


@dataclass(frozen=True)
class ResponseCopySelectors:
    css_selector: str = OWNER_RESPONSE_COPY_CSS_SELECTOR
    xpath: str = OWNER_RESPONSE_COPY_XPATH
    full_xpath: str = OWNER_RESPONSE_COPY_FULL_XPATH


@dataclass(frozen=True)
class ResponseCopyResult:
    strategy: str
    text: str


@dataclass(frozen=True)
class ResponseCopyClickMetadata:
    clicked: bool
    copy_button_count: int = 0
    selected_copy_button_index: int | None = None
    container_summary: str = "unknown"
    selected_button_summary: str = "unknown"
    error: str | None = None


@dataclass(frozen=True)
class ResponseTextExtraction:
    found: bool
    text: str
    text_length: int
    copy_button_count: int = 0
    container_summary: str = "unknown"
    error: str | None = None


def detect_state_from_html(html: str) -> ChatGPTDomState:
    streaming = 'data-testid="stop-button"' in html or 'aria-label="스트리밍 중지"' in html
    send_ready = (
        'data-testid="send-button"' in html
        or 'aria-label="프롬프트 보내기"' in html
        or ('id="composer-submit-button"' in html and not streaming)
    )
    if streaming:
        button_state = "stop-button"
    elif send_ready:
        button_state = "send-button"
    elif 'data-testid="composer-speech-button"' in html or 'aria-label="Voice 시작"' in html:
        button_state = "speech-button"
    else:
        button_state = "unknown"
    return ChatGPTDomState(
        composer_empty=(
            'data-testid="composer-speech-button"' in html
            or 'aria-label="Voice 시작"' in html
        ),
        send_ready=send_ready,
        streaming=streaming,
        response_copy_ready=(
            'data-testid="copy-turn-action-button"' in html
            or 'aria-label="응답 복사"' in html
        ),
        copy_button_count=html.count('data-testid="copy-turn-action-button"'),
        button_state=button_state,
    )


def _query_state_script() -> str:
    return """
(() => {
  const exists = (selector) => Boolean(document.querySelector(selector));
  const copyButtons = document.querySelectorAll('[data-testid="copy-turn-action-button"], [aria-label="응답 복사"]');
  const sendButton = document.querySelector('[data-testid="send-button"], [aria-label="프롬프트 보내기"]');
  const submitButton = document.querySelector('#composer-submit-button');
  const stopButton = document.querySelector('[data-testid="stop-button"], [aria-label="스트리밍 중지"]');
  const speechButton = document.querySelector('[data-testid="composer-speech-button"], [aria-label="Voice 시작"]');
  const composerSelectors = ['textarea', 'div[contenteditable="true"]', '[contenteditable="true"]', '#prompt-textarea', '[data-testid="composer-text-input"]'];
  const isVisible = (el) => Boolean(el && el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none');
  let composer = null;
  let composerSelector = null;
  for (const selector of composerSelectors) {
    const candidates = Array.from(document.querySelectorAll(selector)).filter(isVisible);
    if (candidates.length) {
      composer = candidates[candidates.length - 1];
      composerSelector = selector;
      break;
    }
  }
  const text = composer ? ('value' in composer ? composer.value : composer.innerText || composer.textContent || '') : '';
  const active = document.activeElement;
  const activeSummary = active ? `${active.tagName.toLowerCase()}${active.id ? '#' + active.id : ''}${active.getAttribute('data-testid') ? '[data-testid=' + active.getAttribute('data-testid') + ']' : ''}` : 'none';
  const buttonState = stopButton ? 'stop-button' : (sendButton || (submitButton && submitButton.getAttribute('data-testid') !== 'stop-button') ? 'send-button' : (speechButton ? 'speech-button' : 'unknown'));
  return JSON.stringify({
    composer_empty: exists('[data-testid="composer-speech-button"], [aria-label="Voice 시작"]'),
    send_ready: buttonState === 'send-button',
    streaming: buttonState === 'stop-button',
    response_copy_ready: copyButtons.length > 0,
    copy_button_count: copyButtons.length,
    composer_text_length: text.length,
    button_state: buttonState,
    composer_selector: composerSelector,
    active_element_summary: activeSummary
  });
})()
""".strip()


def query_dom_state(dom: DomClient) -> ChatGPTDomState:
    raw = dom.evaluate_javascript(_query_state_script())
    data = json.loads(raw)
    return ChatGPTDomState(
        composer_empty=bool(data.get("composer_empty")),
        send_ready=bool(data.get("send_ready")),
        streaming=bool(data.get("streaming")),
        response_copy_ready=bool(data.get("response_copy_ready")),
        copy_button_count=int(data.get("copy_button_count") or 0),
        composer_text_length=int(data.get("composer_text_length") or 0),
        button_state=str(data.get("button_state") or "unknown"),
        composer_selector=data.get("composer_selector"),
        active_element_summary=str(data.get("active_element_summary") or "unknown"),
    )


def _focus_composer_script() -> str:
    return """
(() => {
  const selectors = ['textarea', 'div[contenteditable="true"]', '[contenteditable="true"]', '#prompt-textarea', '[data-testid="composer-text-input"]'];
  const isVisible = (el) => Boolean(el && el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none');
  const summarize = (el) => el ? `${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}${el.getAttribute('data-testid') ? '[data-testid=' + el.getAttribute('data-testid') + ']' : ''}` : 'none';
  const buttonState = () => {
    if (document.querySelector('[data-testid="stop-button"], [aria-label="스트리밍 중지"]')) return 'stop-button';
    const send = document.querySelector('[data-testid="send-button"], [aria-label="프롬프트 보내기"]');
    const submit = document.querySelector('#composer-submit-button');
    if (send || (submit && submit.getAttribute('data-testid') !== 'stop-button')) return 'send-button';
    if (document.querySelector('[data-testid="composer-speech-button"], [aria-label="Voice 시작"]')) return 'speech-button';
    return 'unknown';
  };
  for (const selector of selectors) {
    const candidates = Array.from(document.querySelectorAll(selector)).filter(isVisible);
    if (!candidates.length) continue;
    const composer = candidates[candidates.length - 1];
    composer.scrollIntoView({block: 'center'});
    composer.focus({preventScroll: true});
    const active = document.activeElement;
    const activeInside = active === composer || composer.contains(active);
    const text = 'value' in composer ? composer.value : composer.innerText || composer.textContent || '';
    return JSON.stringify({
      found: true,
      selector,
      active_inside: activeInside,
      active_element_summary: summarize(active),
      text_length: text.length,
      button_state: buttonState()
    });
  }
  return JSON.stringify({
    found: false,
    selector: null,
    active_inside: false,
    active_element_summary: summarize(document.activeElement),
    text_length: 0,
    button_state: buttonState()
  });
})()
""".strip()


def focus_composer(dom: DomClient, *, event_log: EventLog | None = None) -> ComposerFocusResult:
    raw = dom.evaluate_javascript(_focus_composer_script())
    data = json.loads(raw)
    if not data.get("found"):
        raise ChatGPTStateMachineError(
            "ChatGPT composer was not found. "
            f"active_element={data.get('active_element_summary', 'unknown')} "
            f"button_state={data.get('button_state', 'unknown')}"
        )
    result = ComposerFocusResult(
        selector=str(data.get("selector")),
        active_inside=bool(data.get("active_inside")),
        active_element_summary=str(data.get("active_element_summary") or "unknown"),
        text_length=int(data.get("text_length") or 0),
        button_state=str(data.get("button_state") or "unknown"),
    )
    if not result.active_inside:
        raise ChatGPTStateMachineError(
            "ChatGPT composer focus failed. "
            f"selector={result.selector} active_element={result.active_element_summary} "
            f"button_state={result.button_state}"
        )
    if event_log:
        event_log.append(
            "pm_composer_focused",
            selector=result.selector,
            active_element=result.active_element_summary,
            text_length=result.text_length,
            button_state=result.button_state,
        )
    return result


def _insert_text_script(text: str) -> str:
    return f"""
(() => {{
  const text = {json.dumps(text)};
  const selectors = ['textarea', 'div[contenteditable="true"]', '[contenteditable="true"]', '#prompt-textarea', '[data-testid="composer-text-input"]'];
  const isVisible = (el) => Boolean(el && el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none');
  const summarize = (el) => el ? `${{el.tagName.toLowerCase()}}${{el.id ? '#' + el.id : ''}}${{el.getAttribute('data-testid') ? '[data-testid=' + el.getAttribute('data-testid') + ']' : ''}}` : 'none';
  const buttonState = () => {{
    if (document.querySelector('[data-testid="stop-button"], [aria-label="스트리밍 중지"]')) return 'stop-button';
    const send = document.querySelector('[data-testid="send-button"], [aria-label="프롬프트 보내기"]');
    const submit = document.querySelector('#composer-submit-button');
    if (send || (submit && submit.getAttribute('data-testid') !== 'stop-button')) return 'send-button';
    if (document.querySelector('[data-testid="composer-speech-button"], [aria-label="Voice 시작"]')) return 'speech-button';
    return 'unknown';
  }};
  const emit = (el, type) => {{
    const event = type === 'input'
      ? new InputEvent('input', {{bubbles: true, cancelable: true, inputType: 'insertText', data: text}})
      : new Event(type, {{bubbles: true, cancelable: true}});
    el.dispatchEvent(event);
  }};
  for (const selector of selectors) {{
    const candidates = Array.from(document.querySelectorAll(selector)).filter(isVisible);
    if (!candidates.length) continue;
    const composer = candidates[candidates.length - 1];
    composer.scrollIntoView({{block: 'center'}});
    composer.focus({{preventScroll: true}});
    if ('value' in composer) {{
      const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(composer), 'value')?.set;
      if (setter) setter.call(composer, text);
      else composer.value = text;
      emit(composer, 'input');
      emit(composer, 'change');
    }} else {{
      document.execCommand('selectAll', false, null);
      const inserted = document.execCommand('insertText', false, text);
      let current = composer.innerText || composer.textContent || '';
      if (!inserted || !current.includes(text.slice(0, Math.min(40, text.length)))) {{
        composer.textContent = text;
        emit(composer, 'input');
      }}
      emit(composer, 'change');
    }}
    const active = document.activeElement;
    const currentText = 'value' in composer ? composer.value : composer.innerText || composer.textContent || '';
    return JSON.stringify({{
      found: true,
      selector,
      active_inside: active === composer || composer.contains(active),
      active_element_summary: summarize(active),
      text_length: currentText.length,
      text: currentText,
      button_state: buttonState()
    }});
  }}
  return JSON.stringify({{
    found: false,
    selector: null,
    active_inside: false,
    active_element_summary: summarize(document.activeElement),
    text_length: 0,
    text: '',
    button_state: buttonState()
  }});
}})()
""".strip()


def insert_text_into_composer(
    dom: DomClient,
    text: str,
    *,
    expected_marker: str | None = None,
    event_log: EventLog | None = None,
) -> ComposerTextVerification:
    if not text.strip():
        raise ChatGPTStateMachineError("Cannot insert empty text into ChatGPT composer.")
    focus_composer(dom, event_log=event_log)
    raw = dom.evaluate_javascript(_insert_text_script(text))
    data = json.loads(raw)
    if not data.get("found"):
        raise ChatGPTStateMachineError(
            "ChatGPT composer was not found for text insertion. "
            f"active_element={data.get('active_element_summary', 'unknown')} "
            f"button_state={data.get('button_state', 'unknown')}"
        )
    current_text = str(data.get("text") or "")
    contains_marker = bool(expected_marker and expected_marker in current_text)
    verification = ComposerTextVerification(
        selector=str(data.get("selector")),
        text_length=int(data.get("text_length") or 0),
        contains_expected_marker=contains_marker,
        button_state=str(data.get("button_state") or "unknown"),
        active_element_summary=str(data.get("active_element_summary") or "unknown"),
    )
    if verification.text_length <= 0:
        raise ChatGPTStateMachineError(
            "ChatGPT composer text verification failed: text was empty after paste/insertion. "
            f"selector={verification.selector} button_state={verification.button_state} "
            f"active_element={verification.active_element_summary}"
        )
    if expected_marker and not verification.contains_expected_marker:
        raise ChatGPTStateMachineError(
            "ChatGPT composer text verification failed: expected marker was missing after paste/insertion. "
            f"marker={expected_marker} text_length={verification.text_length} "
            f"button_state={verification.button_state} active_element={verification.active_element_summary}"
        )
    if event_log:
        event_log.append(
            "pm_composer_text_verified",
            selector=verification.selector,
            text_length=verification.text_length,
            contains_expected_marker=verification.contains_expected_marker,
            button_state=verification.button_state,
            active_element=verification.active_element_summary,
        )
    return verification


def wait_for_send_ready(
    dom: DomClient,
    *,
    timeout_seconds: float = 10,
    sleep_fn: Callable[[float], None] = time.sleep,
    event_log: EventLog | None = None,
) -> ChatGPTDomState:
    deadline = time.monotonic() + timeout_seconds
    saw_empty = False
    while time.monotonic() <= deadline:
        state = query_dom_state(dom)
        if state.composer_empty:
            saw_empty = True
            if event_log:
                event_log.append("pm_composer_empty_detected")
        if state.send_ready and not state.streaming:
            if event_log:
                event_log.append("pm_composer_send_ready_detected")
            return state
        sleep_fn(0.25)
    try:
        state = query_dom_state(dom)
        diagnostic = (
            f"composer_text_length={state.composer_text_length} "
            f"button_state={state.button_state} "
            f"composer_selector={state.composer_selector or 'none'} "
            f"active_element={state.active_element_summary}"
        )
    except Exception as error:
        diagnostic = f"diagnostic_unavailable={error}"
    if saw_empty:
        raise ChatGPTStateMachineError(
            "ChatGPT composer did not enter send-ready state after paste. " + diagnostic
        )
    raise ChatGPTStateMachineError(
        "ChatGPT composer send-ready state was not detected. " + diagnostic
    )


def _pre_paste_state_name(state: ChatGPTDomState) -> str:
    if state.streaming:
        return "streaming"
    if state.send_ready:
        return "user_pending_message"
    if state.composer_empty:
        return "idle_empty"
    return "unknown"


def wait_for_idle_empty_composer(
    dom: DomClient,
    *,
    timeout_seconds: float = DEFAULT_CHATGPT_IDLE_EMPTY_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_CHATGPT_IDLE_EMPTY_POLL_INTERVAL_SECONDS,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
    event_log: EventLog | None = None,
) -> ChatGPTDomState:
    start_time = monotonic_fn()
    deadline = start_time + timeout_seconds
    last_observed_state = "unknown"
    last_logged_state: str | None = None
    if event_log:
        event_log.append(
            "pm_idle_empty_wait_started",
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
    while True:
        now = monotonic_fn()
        if now > deadline:
            break
        state = query_dom_state(dom)
        observed_state = _pre_paste_state_name(state)
        last_observed_state = observed_state
        elapsed_seconds = max(0.0, now - start_time)
        remaining_seconds = max(0.0, deadline - now)
        if event_log:
            event_log.append(
                "pm_idle_empty_poll",
                observed_state=observed_state,
                elapsed_seconds=elapsed_seconds,
                remaining_seconds=remaining_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )
        if observed_state != last_logged_state and event_log:
            if observed_state == "streaming":
                event_log.append("pm_streaming_state_observed_before_paste")
            elif observed_state == "user_pending_message":
                event_log.append("pm_user_pending_message_observed_before_paste")
            elif observed_state == "idle_empty":
                event_log.append("pm_idle_empty_detected")
        last_logged_state = observed_state
        if observed_state == "idle_empty":
            return state
        sleep_fn(poll_interval_seconds)
    if event_log:
        event_log.append(
            "pm_idle_empty_wait_timeout",
            timeout_seconds=timeout_seconds,
            last_observed_state=last_observed_state,
        )
    timeout_display = int(timeout_seconds) if float(timeout_seconds).is_integer() else timeout_seconds
    raise ChatGPTStateMachineError(
        "ChatGPT did not reach idle-empty composer state within "
        f"{timeout_display} seconds before PM prompt paste. "
        f"last_observed_state={last_observed_state}"
    )


def click_send_button(dom: DomClient) -> None:
    result = dom.evaluate_javascript(
        """
(() => {
  let button = document.querySelector('[data-testid="send-button"], [aria-label="프롬프트 보내기"]');
  const submit = document.querySelector('#composer-submit-button');
  if (!button && submit && submit.getAttribute('data-testid') !== 'stop-button') button = submit;
  if (!button) return 'missing';
  if (button.getAttribute('data-testid') === 'composer-speech-button' || button.getAttribute('aria-label') === 'Voice 시작') return 'speech-button';
  button.click();
  return 'clicked';
})()
""".strip()
    )
    if result != "clicked":
        raise ChatGPTStateMachineError("ChatGPT send button was not available for click.")


def wait_for_response_copy_ready(
    dom: DomClient,
    *,
    timeout_seconds: float,
    sleep_fn: Callable[[float], None] = time.sleep,
    event_log: EventLog | None = None,
) -> ChatGPTDomState:
    deadline = time.monotonic() + timeout_seconds
    streaming_started = False
    while time.monotonic() <= deadline:
        state = query_dom_state(dom)
        if state.streaming:
            streaming_started = True
            if event_log:
                event_log.append("pm_streaming_started")
            sleep_fn(0.5)
            continue
        if streaming_started and event_log:
            event_log.append("pm_streaming_finished")
            streaming_started = False
        if state.response_copy_ready:
            if event_log:
                event_log.append("pm_copy_button_ready", copy_button_count=state.copy_button_count)
            return state
        sleep_fn(0.5)
    raise ChatGPTStateMachineError("ChatGPT response copy button was not ready before timeout.")


def _click_latest_copy_button_script() -> str:
    return """
(() => {
  const isVisible = (el) => Boolean(
    el &&
    el.getClientRects().length &&
    getComputedStyle(el).visibility !== 'hidden' &&
    getComputedStyle(el).display !== 'none'
  );
  const summarize = (el) => {
    if (!el) return 'none';
    const attrs = [
      el.id ? `#${el.id}` : '',
      el.getAttribute('data-testid') ? `[data-testid=${el.getAttribute('data-testid')}]` : '',
      el.getAttribute('data-message-author-role') ? `[role=${el.getAttribute('data-message-author-role')}]` : '',
      el.getAttribute('aria-label') ? `[aria-label=${el.getAttribute('aria-label')}]` : ''
    ].join('');
    const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 80);
    return `${el.tagName.toLowerCase()}${attrs}${text ? ` text="${text}"` : ''}`;
  };
  const unique = (items) => Array.from(new Set(items.filter(Boolean)));
  const copyControls = unique(
    Array.from(document.querySelectorAll('[data-testid="copy-turn-action-button"], [aria-label="응답 복사"]'))
      .map((el) => el.closest('button') || el)
  ).filter(isVisible);
  const assistantContainerFor = (button) => {
    const selectors = [
      '[data-message-author-role="assistant"]',
      '[data-testid="conversation-turn-assistant"]',
      '[data-testid*="assistant"]',
      'article',
      'section'
    ];
    for (const selector of selectors) {
      const container = button.closest(selector);
      if (container && isVisible(container)) return container;
    }
    return button.closest('div');
  };
  const candidates = copyControls
    .map((button, index) => ({button, index, container: assistantContainerFor(button)}))
    .filter((candidate) => candidate.container && isVisible(candidate.container));
  const selected = candidates.length
    ? candidates[candidates.length - 1]
    : (copyControls.length ? {button: copyControls[copyControls.length - 1], index: copyControls.length - 1, container: null} : null);
  const latestButton = selected ? selected.button : null;
  if (!latestButton) {
    return JSON.stringify({
      clicked: false,
      copy_button_count: copyControls.length,
      selected_copy_button_index: null,
      container_summary: 'none',
      selected_button_summary: 'none',
      error: 'missing'
    });
  }
  latestButton.scrollIntoView({block: 'center'});
  latestButton.click();
  return JSON.stringify({
    clicked: true,
    copy_button_count: copyControls.length,
    selected_copy_button_index: selected.index,
    container_summary: summarize(selected.container),
    selected_button_summary: summarize(latestButton),
    error: null
  });
})()
""".strip()


def _click_css_selector_script(selector: str) -> str:
    return f"""
(() => {{
  const el = document.querySelector({json.dumps(selector)});
  const button = el ? (el.closest('button') || el) : null;
  if (!button) return 'missing';
  button.click();
  return 'clicked';
}})()
""".strip()


def _click_xpath_script(xpath: str) -> str:
    return f"""
(() => {{
  const result = document.evaluate({json.dumps(xpath)}, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
  const el = result.singleNodeValue;
  const button = el ? (el.closest('button') || el) : null;
  if (!button) return 'missing';
  button.click();
  return 'clicked';
}})()
""".strip()


def _verify_copied_text(
    *,
    before: str,
    after: str,
    expected_marker: str | None,
) -> None:
    if after == before:
        raise ChatGPTStateMachineError("Clipboard did not change after response copy attempt.")
    if not after.strip():
        raise ChatGPTStateMachineError("Copied ChatGPT response was empty.")
    if expected_marker and expected_marker not in after:
        raise ChatGPTStateMachineError(f"Copied ChatGPT response did not contain {expected_marker}.")


def _extract_latest_assistant_text_script() -> str:
    return """
(() => {
  const agentBridgePurpose = 'extract_latest_assistant_response_text';
  const isVisible = (el) => Boolean(
    el &&
    el.getClientRects().length &&
    getComputedStyle(el).visibility !== 'hidden' &&
    getComputedStyle(el).display !== 'none'
  );
  const summarize = (el) => {
    if (!el) return 'none';
    const attrs = [
      el.id ? `#${el.id}` : '',
      el.getAttribute('data-testid') ? `[data-testid=${el.getAttribute('data-testid')}]` : '',
      el.getAttribute('data-message-author-role') ? `[role=${el.getAttribute('data-message-author-role')}]` : '',
      el.getAttribute('aria-label') ? `[aria-label=${el.getAttribute('aria-label')}]` : ''
    ].join('');
    const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 80);
    return `${el.tagName.toLowerCase()}${attrs}${text ? ` text="${text}"` : ''}`;
  };
  const copyButtons = Array.from(
    document.querySelectorAll('[data-testid="copy-turn-action-button"], [aria-label="응답 복사"]')
  ).map((el) => el.closest('button') || el).filter((button, index, buttons) => (
    button && buttons.indexOf(button) === index && isVisible(button)
  ));
  const assistantContainerFor = (button) => {
    const selectors = [
      '[data-message-author-role="assistant"]',
      '[data-testid="conversation-turn-assistant"]',
      '[data-testid*="assistant"]',
      'article',
      'section'
    ];
    for (const selector of selectors) {
      const container = button.closest(selector);
      if (container && isVisible(container)) return container;
    }
    return button.closest('div');
  };
  let containers = Array.from(document.querySelectorAll('[data-message-author-role="assistant"]')).filter(isVisible);
  if (!containers.length) {
    containers = copyButtons.map(assistantContainerFor).filter((container, index, all) => (
      container && isVisible(container) && all.indexOf(container) === index
    ));
  }
  const container = containers.length ? containers[containers.length - 1] : null;
  if (!container) {
    return JSON.stringify({
      purpose: agentBridgePurpose,
      found: false,
      text: '',
      text_length: 0,
      copy_button_count: copyButtons.length,
      container_summary: 'none',
      error: 'missing latest assistant response container'
    });
  }
  const codeBlockMarkdown = Array.from(container.querySelectorAll('pre')).filter(isVisible).map((pre) => {
    const code = pre.querySelector('code') || pre;
    const codeText = (code.innerText || code.textContent || '').trim();
    if (!codeText) return null;
    const wrapper = pre.parentElement ? pre.parentElement.parentElement || pre.parentElement : pre;
    const wrapperText = (wrapper.innerText || wrapper.textContent || '').trim();
    let info = '';
    const className = code.className || pre.className || '';
    const languageMatch = className.match(/language-([A-Za-z0-9_-]+)/);
    if (wrapperText.includes('CODEX_NEXT_PROMPT')) info = 'CODEX_NEXT_PROMPT';
    else if (languageMatch) info = languageMatch[1];
    return '```' + info + '\\n' + codeText + '\\n```';
  }).filter(Boolean);
  if (codeBlockMarkdown.length) {
    const text = codeBlockMarkdown.join('\\n\\n');
    return JSON.stringify({
      purpose: agentBridgePurpose,
      found: true,
      text,
      text_length: text.length,
      copy_button_count: copyButtons.length,
      container_summary: summarize(container),
      error: null
    });
  }
  const clone = container.cloneNode(true);
  for (const selector of [
    'button',
    'svg',
    'nav',
    'form',
    'textarea',
    '[contenteditable="true"]',
    '[data-testid="copy-turn-action-button"]',
    '[data-testid="composer-text-input"]',
    '[data-testid="composer-speech-button"]',
    '[data-testid="send-button"]',
    '[data-testid="stop-button"]'
  ]) {
    clone.querySelectorAll(selector).forEach((el) => el.remove());
  }
  const text = (clone.innerText || clone.textContent || '').replace(/\\n{3,}/g, '\\n\\n').trim();
  return JSON.stringify({
    purpose: agentBridgePurpose,
    found: true,
    text,
    text_length: text.length,
    copy_button_count: copyButtons.length,
    container_summary: summarize(container),
    error: null
  });
})()
""".strip()


def _normalize_dom_extracted_text(text: str, expected_marker: str | None) -> str:
    if not expected_marker:
        return text
    normalized_fence = _normalize_expected_marker_fenced_text(text, expected_marker)
    if normalized_fence != text:
        return normalized_fence
    if "```" in text:
        return text
    stripped = text.strip()
    if not stripped.startswith(expected_marker):
        return text
    body = stripped[len(expected_marker) :].strip()
    if not body:
        return text
    return f"```{expected_marker}\n{body}\n```"


def _normalize_expected_marker_fenced_text(text: str, expected_marker: str) -> str:
    lines = text.splitlines(keepends=True)
    if len(lines) < 3:
        return text
    opening = lines[0].strip()
    if not opening.startswith("```"):
        return text
    info_string = opening[3:].strip()
    if not info_string.startswith(expected_marker):
        return text

    closing_index = None
    for index in range(len(lines) - 1, 0, -1):
        if lines[index].strip().startswith("```"):
            closing_index = index
            break
    if closing_index is None or closing_index <= 1:
        return text

    content_lines = lines[1:closing_index]
    if not content_lines or content_lines[0].strip() != expected_marker:
        return text
    return "".join([lines[0], *content_lines[1:], *lines[closing_index:]])


def _parse_copy_click_metadata(raw: str) -> ResponseCopyClickMetadata:
    if raw == "clicked":
        return ResponseCopyClickMetadata(clicked=True)
    if raw == "missing":
        return ResponseCopyClickMetadata(clicked=False, error="missing")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ResponseCopyClickMetadata(clicked=False, error=raw or "unknown result")
    return ResponseCopyClickMetadata(
        clicked=bool(data.get("clicked")),
        copy_button_count=int(data.get("copy_button_count") or 0),
        selected_copy_button_index=data.get("selected_copy_button_index"),
        container_summary=str(data.get("container_summary") or "unknown"),
        selected_button_summary=str(data.get("selected_button_summary") or "unknown"),
        error=data.get("error"),
    )


def _extract_latest_assistant_text(dom: DomClient) -> ResponseTextExtraction:
    raw = dom.evaluate_javascript(_extract_latest_assistant_text_script())
    data = json.loads(raw)
    text = str(data.get("text") or "")
    return ResponseTextExtraction(
        found=bool(data.get("found")),
        text=text,
        text_length=int(data.get("text_length") or len(text)),
        copy_button_count=int(data.get("copy_button_count") or 0),
        container_summary=str(data.get("container_summary") or "unknown"),
        error=data.get("error"),
    )


def copy_response_with_strategies(
    *,
    dom: DomClient,
    clipboard: Clipboard,
    selectors: ResponseCopySelectors = ResponseCopySelectors(),
    expected_marker: str | None = None,
    generic_copy_fallback: Callable[[], None] | None = None,
    event_log: EventLog | None = None,
) -> ResponseCopyResult:
    strategies: list[tuple[str, Callable[[], str]]] = [
        ("latest_copy_button", lambda: dom.evaluate_javascript(_click_latest_copy_button_script())),
        ("owner_css_selector", lambda: dom.evaluate_javascript(_click_css_selector_script(selectors.css_selector))),
        ("owner_xpath", lambda: dom.evaluate_javascript(_click_xpath_script(selectors.xpath))),
        ("owner_full_xpath", lambda: dom.evaluate_javascript(_click_xpath_script(selectors.full_xpath))),
    ]
    if generic_copy_fallback:
        strategies.append(("generic_fallback", lambda: _run_generic_copy(generic_copy_fallback)))

    errors: list[str] = []
    for strategy_name, click in strategies:
        before = clipboard.read_text()
        if event_log:
            event_log.append(
                "pm_response_copy_attempted",
                strategy=strategy_name,
                clipboard_before_length=len(before),
            )
        try:
            result = click()
            metadata = _parse_copy_click_metadata(result)
            if not metadata.clicked:
                raise ChatGPTStateMachineError(f"Copy strategy {strategy_name} did not find a target.")
            after = clipboard.read_text()
            _verify_copied_text(before=before, after=after, expected_marker=expected_marker)
            if event_log:
                event_log.append(
                    "pm_response_copy_strategy_succeeded",
                    strategy=strategy_name,
                    clipboard_before_length=len(before),
                    clipboard_after_length=len(after),
                    copy_button_count=metadata.copy_button_count,
                    selected_copy_button_index=metadata.selected_copy_button_index,
                    response_container=metadata.container_summary,
                )
            return ResponseCopyResult(strategy=strategy_name, text=after)
        except Exception as error:
            errors.append(f"{strategy_name}: {error}")
            if event_log:
                after = clipboard.read_text()
                event_log.append(
                    "pm_response_copy_strategy_failed",
                    strategy=strategy_name,
                    error=str(error),
                    clipboard_before_length=len(before),
                    clipboard_after_length=len(after),
                )
    try:
        before = clipboard.read_text()
        if event_log:
            event_log.append(
                "pm_response_copy_attempted",
                strategy="dom_text_fallback",
                clipboard_before_length=len(before),
            )
        extraction = _extract_latest_assistant_text(dom)
        if not extraction.found:
            raise ChatGPTStateMachineError(extraction.error or "Latest assistant response container was not found.")
        fallback_text = _normalize_dom_extracted_text(extraction.text, expected_marker)
        if not fallback_text.strip():
            raise ChatGPTStateMachineError("DOM text fallback extracted empty ChatGPT response text.")
        if expected_marker and expected_marker not in fallback_text:
            raise ChatGPTStateMachineError(
                f"DOM text fallback response did not contain {expected_marker}."
            )
        clipboard.copy_text(fallback_text)
        after = clipboard.read_text()
        _verify_copied_text(before=before, after=after, expected_marker=expected_marker)
        if event_log:
            event_log.append(
                "pm_response_copy_strategy_succeeded",
                strategy="dom_text_fallback",
                clipboard_before_length=len(before),
                clipboard_after_length=len(after),
                copy_button_count=extraction.copy_button_count,
                selected_copy_button_index=None,
                response_container=extraction.container_summary,
                extracted_text_length=extraction.text_length,
                clipboard_text_length=len(fallback_text),
                reconstructed_expected_fence=fallback_text != extraction.text,
            )
        return ResponseCopyResult(strategy="dom_text_fallback", text=after)
    except Exception as error:
        errors.append(f"dom_text_fallback: {error}")
        if event_log:
            after = clipboard.read_text()
            event_log.append(
                "pm_response_copy_strategy_failed",
                strategy="dom_text_fallback",
                error=str(error),
                clipboard_before_length=len(before),
                clipboard_after_length=len(after),
            )
    raise ChatGPTStateMachineError(
        "All ChatGPT response copy strategies failed: " + "; ".join(errors)
    )


def _run_generic_copy(callback: Callable[[], None]) -> str:
    callback()
    return "clicked"


@dataclass
class MacOSChromeJavaScriptDomClient(DomClient):
    app_name: str
    osascript_executable: str = "osascript"

    def evaluate_javascript(self, script: str) -> str:
        escaped_app = self.app_name.replace("\\", "\\\\").replace('"', '\\"')
        compact_script = " ".join(line.strip() for line in script.splitlines() if line.strip())
        escaped_script = compact_script.replace("\\", "\\\\").replace('"', '\\"')
        apple_script = (
            f'tell application "{escaped_app}"\n'
            "    tell active tab of front window\n"
            f'        execute javascript "{escaped_script}"\n'
            "    end tell\n"
            "end tell"
        )
        completed = subprocess.run(
            [self.osascript_executable, "-e", apple_script],
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            raise ChatGPTStateMachineError(completed.stderr.strip() or "JavaScript execution failed.")
        return completed.stdout.strip()
