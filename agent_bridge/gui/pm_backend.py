from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from agent_bridge.core.event_log import EventLog
from agent_bridge.gui.chatgpt_state_machine import (
    ChatGPTStateMachineError,
    DomClient,
    MacOSChromeJavaScriptDomClient,
    query_dom_state,
)
from agent_bridge.gui.macos_apps import AppActivator, MacOSAppActivator, ManualStageTarget


class PMAssistantBackend(str, Enum):
    CHROME_JS = "chrome_js"
    CHATGPT_PWA_JS = "chatgpt_pwa_js"
    BROWSER_APPLE_EVENTS = "browser_apple_events"
    ACCESSIBILITY_FALLBACK = "accessibility_fallback"
    UNSUPPORTED = "unsupported"


JS_BACKENDS = {
    PMAssistantBackend.CHROME_JS,
    PMAssistantBackend.BROWSER_APPLE_EVENTS,
}


@dataclass(frozen=True)
class PMBackendCheck:
    name: str
    succeeded: bool
    detail: str
    skipped: bool = False


@dataclass(frozen=True)
class PMBackendPreflightResult:
    backend: PMAssistantBackend
    target: ManualStageTarget
    dry_run: bool
    activate: bool
    checks: tuple[PMBackendCheck, ...]

    @property
    def succeeded(self) -> bool:
        return all(check.succeeded or check.skipped for check in self.checks)

    @property
    def failure_reason(self) -> str | None:
        for check in self.checks:
            if not check.succeeded and not check.skipped:
                return check.detail
        return None


DomClientFactory = Callable[[ManualStageTarget], DomClient]
ClipboardToolChecker = Callable[[str], str | None]


def normalize_pm_backend(value: str | None) -> PMAssistantBackend:
    if not value:
        return PMAssistantBackend.UNSUPPORTED
    try:
        return PMAssistantBackend(value)
    except ValueError:
        return PMAssistantBackend.UNSUPPORTED


def _default_dom_client_factory(target: ManualStageTarget) -> DomClient:
    return MacOSChromeJavaScriptDomClient(app_name=target.app_name)


def _clipboard_tools_check(which: ClipboardToolChecker) -> PMBackendCheck:
    missing = [tool for tool in ("pbcopy", "pbpaste") if which(tool) is None]
    if missing:
        return PMBackendCheck(
            name="clipboard_tools",
            succeeded=False,
            detail=f"Missing clipboard tool(s): {', '.join(missing)}",
        )
    return PMBackendCheck(
        name="clipboard_tools",
        succeeded=True,
        detail="pbcopy and pbpaste are available.",
    )


def _activation_check(
    *,
    target: ManualStageTarget,
    activator: AppActivator,
) -> PMBackendCheck:
    try:
        activator.activate(
            target.app_name,
            app_path=target.app_path,
            bundle_id=target.bundle_id,
        )
    except Exception as error:
        return PMBackendCheck(
            name="app_activation",
            succeeded=False,
            detail=f"App activation failed for {target.app_name}: {error}",
        )
    return PMBackendCheck(
        name="app_activation",
        succeeded=True,
        detail=f"App activation succeeded for {target.app_name}.",
    )


def _javascript_ready_state_check(dom: DomClient) -> PMBackendCheck:
    try:
        ready_state = dom.evaluate_javascript("document.readyState")
    except Exception as error:
        return PMBackendCheck(
            name="dom_javascript",
            succeeded=False,
            detail=(
                "DOM JavaScript execution failed through Apple Events: "
                f"{error}. For Chrome targets, enable Chrome's "
                '"Allow JavaScript from Apple Events" setting or use a supported PM assistant target.'
            ),
        )
    return PMBackendCheck(
        name="dom_javascript",
        succeeded=True,
        detail=f"Safe JavaScript probe succeeded; document.readyState={ready_state!r}.",
    )


def _selector_query_check(dom: DomClient) -> PMBackendCheck:
    try:
        state = query_dom_state(dom)
    except (ChatGPTStateMachineError, ValueError, TypeError) as error:
        return PMBackendCheck(
            name="selector_query",
            succeeded=False,
            detail=f"Composer/copy selector state query failed: {error}",
        )
    return PMBackendCheck(
        name="selector_query",
        succeeded=True,
        detail=(
            "Composer/copy selectors are queryable "
            f"(send_ready={state.send_ready}, streaming={state.streaming}, "
            f"copy_button_count={state.copy_button_count})."
        ),
    )


def preflight_pm_backend(
    *,
    target: ManualStageTarget,
    backend: str | PMAssistantBackend | None = None,
    dry_run: bool = False,
    activate: bool = False,
    activator: AppActivator | None = None,
    dom_client_factory: DomClientFactory = _default_dom_client_factory,
    clipboard_tool_checker: ClipboardToolChecker = shutil.which,
    event_log: EventLog | None = None,
) -> PMBackendPreflightResult:
    selected_backend = (
        backend
        if isinstance(backend, PMAssistantBackend)
        else normalize_pm_backend(backend or target.backend)
    )
    checks: list[PMBackendCheck] = []

    if event_log:
        event_log.append(
            "pm_backend_preflight_started",
            backend=selected_backend.value,
            app_name=target.app_name,
            dry_run=dry_run,
            activate=activate,
        )

    if selected_backend == PMAssistantBackend.UNSUPPORTED:
        checks.append(
            PMBackendCheck(
                name="backend_supported",
                succeeded=False,
                detail="PM assistant backend is unsupported or not configured.",
            )
        )
    else:
        checks.append(
            PMBackendCheck(
                name="backend_supported",
                succeeded=True,
                detail=f"Configured backend: {selected_backend.value}.",
            )
        )

    if selected_backend == PMAssistantBackend.ACCESSIBILITY_FALLBACK:
        checks.append(
            PMBackendCheck(
                name="accessibility_fallback",
                succeeded=False,
                detail=(
                    "accessibility_fallback is not enabled because it cannot yet prove "
                    "send-ready, streaming, and latest-response copy-button detection."
                ),
            )
        )
    if selected_backend == PMAssistantBackend.CHATGPT_PWA_JS:
        checks.append(
            PMBackendCheck(
                name="chatgpt_pwa_js",
                succeeded=False,
                detail=(
                    "chatgpt_pwa_js is not supported for Chrome DOM JavaScript. "
                    "Use Google Chrome with backend=chrome_js unless a future PWA backend proves "
                    "tab JavaScript support."
                ),
            )
        )

    checks.append(_clipboard_tools_check(clipboard_tool_checker))

    if dry_run:
        checks.append(
            PMBackendCheck(
                name="dry_run",
                succeeded=True,
                skipped=True,
                detail="Dry-run only: activation and DOM JavaScript probes were not executed.",
            )
        )
        result = PMBackendPreflightResult(
            backend=selected_backend,
            target=target,
            dry_run=True,
            activate=activate,
            checks=tuple(checks),
        )
        if event_log:
            event_log.append(
                "pm_backend_preflight_dry_run",
                backend=selected_backend.value,
                succeeded=result.succeeded,
            )
        return result

    if activate:
        checks.append(
            _activation_check(
                target=target,
                activator=activator or MacOSAppActivator(),
            )
        )

    if selected_backend in JS_BACKENDS and all(check.succeeded or check.skipped for check in checks):
        dom = dom_client_factory(target)
        checks.append(_javascript_ready_state_check(dom))
        if checks[-1].succeeded:
            checks.append(_selector_query_check(dom))

    result = PMBackendPreflightResult(
        backend=selected_backend,
        target=target,
        dry_run=False,
        activate=activate,
        checks=tuple(checks),
    )
    if event_log:
        event_log.append(
            "pm_backend_preflight_succeeded" if result.succeeded else "pm_backend_preflight_failed",
            backend=selected_backend.value,
            app_name=target.app_name,
            failure_reason=result.failure_reason,
        )
    return result


def format_pm_backend_preflight_result(result: PMBackendPreflightResult) -> str:
    lines = [
        "# PM Assistant Backend Preflight",
        "",
        f"Backend: {result.backend.value}",
        f"App name: {result.target.app_name}",
        f"App path: {result.target.app_path or 'unspecified'}",
        f"Bundle id: {result.target.bundle_id or 'unspecified'}",
        f"Dry-run: {'yes' if result.dry_run else 'no'}",
        f"Activation requested: {'yes' if result.activate else 'no'}",
        f"Result: {'passed' if result.succeeded else 'failed'}",
        "",
        "## Checks",
    ]
    for check in result.checks:
        status = "skipped" if check.skipped else ("ok" if check.succeeded else "failed")
        lines.append(f"- {check.name}: {status}")
        lines.append(f"  {check.detail}")
    lines.extend(
        [
            "",
            "No paste, submit, Enter/Return, GitHub, or Gmail action was attempted.",
        ]
    )
    if result.failure_reason:
        lines.extend(["", f"Failure reason: {result.failure_reason}"])
    return "\n".join(lines)


def merge_pm_target_override(
    target: ManualStageTarget,
    *,
    app_name: str | None = None,
    app_path: Path | None = None,
    bundle_id: str | None = None,
    backend: str | None = None,
) -> ManualStageTarget:
    return ManualStageTarget(
        app_name=app_name or target.app_name,
        app_path=str(app_path) if app_path is not None else target.app_path,
        bundle_id=bundle_id if bundle_id is not None else target.bundle_id,
        window_hint=target.window_hint,
        paste_instruction=target.paste_instruction,
        response_copy_css_selector=target.response_copy_css_selector,
        response_copy_xpath=target.response_copy_xpath,
        response_copy_full_xpath=target.response_copy_full_xpath,
        response_copy_strategy=target.response_copy_strategy,
        backend=backend if backend is not None else target.backend,
        require_backend_preflight=target.require_backend_preflight,
    )
