from __future__ import annotations

import hashlib
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable
from uuid import uuid4

from agent_bridge.codex.dispatcher import Dispatcher
from agent_bridge.codex.prompt_builder import PromptBuilder
from agent_bridge.core.command_queue import CommandQueue
from agent_bridge.core.event_log import EventLog
from agent_bridge.core.models import BridgeStateName, Command, CommandType
from agent_bridge.core.safety_gate import SafetyGate
from agent_bridge.core.state_store import StateStore
from agent_bridge.gui.codex_ui_detector import LocalAgentPreSubmitCheck
from agent_bridge.gui.gui_automation import GuiAutomationAdapter
from agent_bridge.gui.macos_apps import GuiTargets
from agent_bridge.gui.pm_backend import (
    PMBackendPreflightResult,
    format_pm_backend_preflight_result,
    preflight_pm_backend,
)


class ReportRoundtripError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReportRoundtripConfig:
    workspace_dir: Path
    template_dir: Path
    targets: GuiTargets
    auto_confirm: bool
    max_cycles: int = 1
    max_runtime_seconds: int = 180
    pm_response_timeout_seconds: int = 45
    require_pm_backend_preflight: bool = True
    submit_local_agent: bool = True
    stop_after_local_agent_submit: bool = False
    wait_for_artifact_confirmation: bool = True
    bridge_attempt_id: str | None = None
    debug_state_machine: bool = False
    debug_gui_actions: bool = False
    debug_screenshots: bool = False
    debug_all_template_comparisons: bool = False
    debug_logs_dir: Path | None = None
    debug_output_fn: Callable[[str], None] | None = None


@dataclass(frozen=True)
class ReportRoundtripResult:
    completed: bool
    reason: str
    cycles_completed: int = 0
    pm_prompt_path: Path | None = None
    pm_response_path: Path | None = None
    extracted_prompt_path: Path | None = None
    local_agent_prompt_path: Path | None = None
    safety_paused: bool = False


NOOP_VALIDATION_TASK_ID = "AB-ROUNDTRIP-NOOP-VALIDATION"
NOOP_VALIDATION_SUCCESS_TITLE = "# Agent Report: GUI Roundtrip No-Op Validation Success"


def _normalize_policy_text(text: str) -> str:
    return re.sub(r"[^a-z0-9#]+", " ", text.lower()).strip()


def is_noop_validation_success_report(report: str) -> bool:
    if _first_nonempty_line(report) != NOOP_VALIDATION_SUCCESS_TITLE:
        return False
    normalized = _normalize_policy_text(report)
    required_phrases = (
        "no source code changes were made",
        "no push or auto merge was performed",
        "no long or unbounded loop was run",
    )
    if not all(phrase in normalized for phrase in required_phrases):
        return False
    external_mutation_phrases = (
        "no github gmail or external mutation was performed",
        "no github gmail external mutation was performed",
        "no github or gmail or external mutation was performed",
        "no github gmail and external mutation was performed",
    )
    return any(phrase in normalized for phrase in external_mutation_phrases)


class _RuntimeGuard:
    def __init__(self, *, max_runtime_seconds: int, monotonic_fn: Callable[[], float]):
        self.max_runtime_seconds = max_runtime_seconds
        self.monotonic_fn = monotonic_fn
        self.started_at = monotonic_fn()

    def check(self) -> None:
        if self.monotonic_fn() - self.started_at > self.max_runtime_seconds:
            raise ReportRoundtripError("Max runtime reached.")


PM_PROMPT_SENTINEL_PREFIX = "AGENT_BRIDGE_PM_PROMPT_SENTINEL:"


def build_pm_prompt_sentinel(bridge_attempt_id: str) -> str:
    return f"{PM_PROMPT_SENTINEL_PREFIX} {bridge_attempt_id}"


def build_report_roundtrip_pm_prompt(
    report: str,
    *,
    bridge_attempt_id: str | None = None,
) -> str:
    sentinel_section = (
        "Paste verification sentinel:\n"
        f"{build_pm_prompt_sentinel(bridge_attempt_id)}\n\n"
        if bridge_attempt_id
        else ""
    )
    return (
        "You are the PM assistant for Agent Bridge.\n\n"
        f"{sentinel_section}"
        "Read the full latest agent report below and return the next Codex instruction.\n\n"
        "Response contract:\n"
        "- Return exactly one fenced Markdown code block.\n"
        "- The fence info string must start with CODEX_NEXT_PROMPT.\n"
        "- The first non-empty line inside the block must be exactly CODEX_NEXT_PROMPT.\n"
        "- Do not include prose before or after the block.\n"
        "- Put only that CODEX_NEXT_PROMPT marker line and the next Codex instruction inside that block.\n"
        "- Do not include CODEX_NEXT_PROMPT anywhere else.\n"
        "- Keep the instruction generic to Agent Bridge and do not add downstream project assumptions.\n\n"
        "The body marker is required because native ChatGPT Mac response-copy may copy only the "
        "rendered code block body and omit the Markdown fence info string.\n\n"
        "If the report contains AGENT_BRIDGE_GUI_ROUNDTRIP_TEST, the returned Codex prompt must be a "
        "safe no-op validation prompt. That no-op prompt must:\n"
        f"- contain Task ID: {NOOP_VALIDATION_TASK_ID};\n"
        "- explicitly say do not modify source code and avoid code changes;\n"
        "- explicitly say do not mutate GitHub;\n"
        "- explicitly say do not send Gmail;\n"
        "- explicitly say do not push commits;\n"
        "- explicitly say do not auto-merge;\n"
        "- ask only to write a short success note to workspace/reports/latest_agent_report.md;\n"
        f"- require the success note title to be exactly: {NOOP_VALIDATION_SUCCESS_TITLE}.\n\n"
        "Latest agent report:\n\n"
        f"{report}"
    )


def extract_codex_next_prompt(response: str) -> str:
    matches: list[str] = []
    lines = response.splitlines(keepends=True)
    opening_pattern = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})(?P<info>[^\r\n]*)$")
    index = 0
    while index < len(lines):
        line_without_newline = lines[index].rstrip("\r\n")
        opening_match = opening_pattern.match(line_without_newline)
        if not opening_match:
            index += 1
            continue

        info_string = opening_match.group("info").strip()
        fence = opening_match.group("fence")
        fence_char = fence[0]
        closing_pattern = re.compile(
            rf"^[ \t]{{0,3}}{re.escape(fence_char)}{{{len(fence)},}}[ \t]*$"
        )
        content_lines: list[str] = []
        index += 1
        found_closing_fence = False
        while index < len(lines):
            candidate = lines[index].rstrip("\r\n")
            if closing_pattern.match(candidate):
                found_closing_fence = True
                break
            content_lines.append(lines[index])
            index += 1

        if found_closing_fence:
            content = "".join(content_lines)
            if info_string.startswith("CODEX_NEXT_PROMPT"):
                matches.append(_strip_codex_next_prompt_body_label(content))
            elif _body_starts_with_codex_next_prompt_label(content):
                matches.append(_strip_codex_next_prompt_body_label(content))
            index += 1
            continue

        break

    body_only_prompt = _extract_body_only_codex_next_prompt(response)
    if body_only_prompt is not None:
        matches.append(body_only_prompt)

    if len(matches) != 1:
        raise ReportRoundtripError("Expected exactly one CODEX_NEXT_PROMPT fenced block.")
    prompt = matches[0]
    if not prompt.strip():
        raise ReportRoundtripError("CODEX_NEXT_PROMPT block was empty.")
    return prompt


def _body_starts_with_codex_next_prompt_label(text: str) -> bool:
    for line in text.splitlines():
        if not line.strip():
            continue
        return line.strip() == "CODEX_NEXT_PROMPT"
    return False


def _strip_codex_next_prompt_body_label(text: str) -> str:
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if line.strip() != "CODEX_NEXT_PROMPT":
            return text
        return "".join([*lines[:index], *lines[index + 1 :]])
    return text


def _extract_body_only_codex_next_prompt(text: str) -> str | None:
    if "```" in text or "~~~" in text:
        return None
    lines = text.splitlines(keepends=True)
    label_indexes = [
        index for index, line in enumerate(lines) if line.strip() == "CODEX_NEXT_PROMPT"
    ]
    if len(label_indexes) != 1:
        return None
    label_index = label_indexes[0]
    if any(line.strip() for line in lines[:label_index]):
        return None
    return "".join(lines[label_index + 1 :])


def _safety_pause(
    *,
    workspace_dir: Path,
    state_store: StateStore,
    event_log: EventLog,
    source_text: str,
    phase: str,
) -> ReportRoundtripResult:
    gate = SafetyGate()
    decision = gate.check_text(source_text)
    gate.write_decision_request(workspace_dir, decision, source_text)
    state = state_store.load()
    state.safety_pause = True
    state.state = BridgeStateName.PAUSED_FOR_USER_DECISION
    state_store.save(state)
    event_log.append(
        "report_roundtrip_safety_blocked",
        phase=phase,
        matched_keywords=decision.matched_keywords,
    )
    return ReportRoundtripResult(completed=False, reason="SAFETY_BLOCKED", safety_paused=True)


def _check_safety(
    *,
    workspace_dir: Path,
    state_store: StateStore,
    event_log: EventLog,
    source_text: str,
    phase: str,
) -> ReportRoundtripResult | None:
    decision = SafetyGate().check_text(source_text)
    if decision.allowed:
        return None
    return _safety_pause(
        workspace_dir=workspace_dir,
        state_store=state_store,
        event_log=event_log,
        source_text=source_text,
        phase=phase,
    )


def is_noop_validation_prompt(prompt: str) -> bool:
    normalized = " ".join(prompt.lower().split())
    required_phrases = (
        NOOP_VALIDATION_TASK_ID.lower(),
        "do not modify source code",
        "avoid code changes",
        "do not mutate github",
        "do not send gmail",
        "do not push commits",
        "do not auto-merge",
        "workspace/reports/latest_agent_report.md",
    )
    if not all(phrase in normalized for phrase in required_phrases):
        return False
    if "success note" not in normalized and "success report" not in normalized:
        return False
    return True


def _file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _command_prompt_source_type(command: Command) -> str:
    if command.prompt_text is not None:
        return "prompt_text"
    if command.prompt_path:
        return "prompt_path"
    if command.payload_path:
        return "payload_path"
    return "none"


def _debug_terminal(config: ReportRoundtripConfig, message: str) -> None:
    if config.debug_output_fn is not None:
        config.debug_output_fn(message)


def _local_agent_submit_block_reason(
    *,
    prompt_length: int,
    safety_passed: bool,
    noop_eligible: bool,
    focus_completed: bool,
    paste_attempted: bool,
    paste_succeeded: bool,
    clipboard_set_attempted: bool = True,
    clipboard_set_succeeded: bool = True,
    clipboard_readback_matches_prompt_hash: bool | None = None,
    paste_backend_succeeded: bool = True,
    prompt_presence_verified: bool | None = None,
    codex_send_ready_after_paste: bool | None = None,
) -> str | None:
    if prompt_length <= 0:
        return "local_agent_prompt_empty"
    if not clipboard_set_attempted:
        return "local_agent_clipboard_set_not_attempted"
    if not clipboard_set_succeeded:
        return "local_agent_clipboard_set_failed"
    if clipboard_readback_matches_prompt_hash is False:
        return "local_agent_clipboard_readback_mismatch"
    if not safety_passed:
        return "local_agent_safety_not_passed"
    if not noop_eligible and not safety_passed:
        return "local_agent_noop_eligibility_not_passed"
    if not focus_completed:
        return "local_agent_focus_not_completed"
    if not paste_attempted:
        return "local_agent_paste_not_attempted"
    if not paste_backend_succeeded:
        return "local_agent_paste_backend_failed"
    if not paste_succeeded:
        return "local_agent_click_succeeded_but_paste_missing"
    return None


def _codex_activation_phase_order_block_reason(
    *,
    pm_response_copy_attempted: bool,
    pm_response_copy_succeeded: bool,
    pm_response_saved: bool,
    codex_next_prompt_extracted: bool,
    local_agent_prompt_staged: bool,
) -> str | None:
    if not pm_response_copy_attempted:
        return "pm_response_copy_not_attempted"
    if not pm_response_copy_succeeded:
        return "pm_response_copy_not_succeeded"
    if not pm_response_saved:
        return "pm_response_not_saved"
    if not codex_next_prompt_extracted:
        return "codex_next_prompt_not_extracted"
    if not local_agent_prompt_staged:
        return "local_agent_prompt_not_staged"
    return None


def _append_debug(
    log: EventLog | None,
    event_type: str,
    *,
    bridge_attempt_id: str,
    phase: str,
    result: str,
    **metadata: object,
) -> None:
    if log is None:
        return
    log.append(
        event_type,
        bridge_attempt_id=bridge_attempt_id,
        phase=phase,
        result=result,
        **metadata,
    )


def _first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _wait_for_noop_artifact_confirmation(
    *,
    report_path: Path,
    event_log: EventLog,
    timeout_seconds: int,
    poll_interval_seconds: float,
    monotonic_fn: Callable[[], float],
    sleep_fn: Callable[[float], None],
    initial_report_hash: str | None = None,
    min_report_mtime_ns: int | None = None,
) -> bool:
    event_log.append(
        "local_agent_artifact_confirmation_wait_started",
        report_path=str(report_path),
        expected_title=NOOP_VALIDATION_SUCCESS_TITLE,
        timeout_seconds=timeout_seconds,
        initial_report_hash=initial_report_hash,
        min_report_mtime_ns=min_report_mtime_ns,
    )
    deadline = monotonic_fn() + timeout_seconds
    while True:
        report_hash: str | None = None
        report_mtime_ns: int | None = None
        try:
            report_bytes = report_path.read_bytes()
            report_text = report_bytes.decode("utf-8")
            report_hash = hashlib.sha256(report_bytes).hexdigest()
            report_mtime_ns = report_path.stat().st_mtime_ns
        except OSError:
            report_text = ""
        except UnicodeDecodeError:
            report_text = ""
        success_report_valid = is_noop_validation_success_report(report_text)
        changed_since_start = (
            initial_report_hash is None or report_hash is None or report_hash != initial_report_hash
        )
        mtime_after_submit = (
            min_report_mtime_ns is None
            or report_mtime_ns is None
            or report_mtime_ns >= min_report_mtime_ns
        )
        if success_report_valid and (changed_since_start or mtime_after_submit):
            event_log.append(
                "local_agent_artifact_confirmation_succeeded",
                report_path=str(report_path),
                expected_title=NOOP_VALIDATION_SUCCESS_TITLE,
                report_hash_changed=changed_since_start,
                report_mtime_ns=report_mtime_ns,
            )
            return True
        if monotonic_fn() >= deadline:
            event_log.append(
                "local_agent_artifact_confirmation_failed",
                report_path=str(report_path),
                expected_title=NOOP_VALIDATION_SUCCESS_TITLE,
                timeout_seconds=timeout_seconds,
                success_report_valid=success_report_valid,
                report_hash_changed=changed_since_start,
                report_mtime_ns=report_mtime_ns,
            )
            return False
        sleep_fn(poll_interval_seconds)


def run_report_roundtrip(
    *,
    config: ReportRoundtripConfig,
    gui: GuiAutomationAdapter,
    queue: CommandQueue | None = None,
    event_log: EventLog | None = None,
    state_store: StateStore | None = None,
    pm_backend_preflight: Callable[[], PMBackendPreflightResult] | None = None,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> ReportRoundtripResult:
    if not config.auto_confirm:
        raise ReportRoundtripError("dogfood-report-roundtrip requires --auto-confirm.")
    if config.max_cycles != 1:
        raise ReportRoundtripError("dogfood-report-roundtrip is limited to exactly one cycle.")
    if config.max_runtime_seconds <= 0:
        raise ReportRoundtripError("--max-runtime-seconds must be greater than zero.")
    if config.pm_response_timeout_seconds <= 0:
        raise ReportRoundtripError("--pm-response-timeout-seconds must be greater than zero.")

    workspace_dir = config.workspace_dir
    outbox_dir = workspace_dir / "outbox"
    report_path = workspace_dir / "reports" / "latest_agent_report.md"
    pm_prompt_path = outbox_dir / "pm_assistant_prompt.md"
    pm_response_path = outbox_dir / "pm_response.md"
    extracted_prompt_path = outbox_dir / "extracted_codex_next_prompt.md"
    command_queue = queue or CommandQueue(workspace_dir / "queue")
    log = event_log or EventLog(workspace_dir / "logs" / "bridge.jsonl")
    store = state_store or StateStore(workspace_dir / "state" / "state.json")
    bridge_attempt_id = config.bridge_attempt_id or f"bridge_{uuid4().hex[:12]}"
    debug_logs_dir = config.debug_logs_dir or workspace_dir / "logs"
    state_debug_log = (
        EventLog(debug_logs_dir / "gui_state_machine_debug.jsonl")
        if config.debug_state_machine
        else None
    )
    action_debug_log = (
        EventLog(debug_logs_dir / "gui_actions_debug.jsonl") if config.debug_gui_actions else None
    )
    if getattr(gui, "event_log", None) is None and hasattr(gui, "event_log"):
        setattr(gui, "event_log", log)
    for attr_name, value in (
        ("bridge_attempt_id", bridge_attempt_id),
        ("debug_state_machine_log", state_debug_log),
        ("debug_gui_actions_log", action_debug_log),
        ("debug_screenshots", config.debug_screenshots),
        ("debug_all_template_comparisons", config.debug_all_template_comparisons),
        ("debug_logs_dir", debug_logs_dir),
        ("debug_output_fn", config.debug_output_fn),
    ):
        if hasattr(gui, attr_name):
            setattr(gui, attr_name, value)
    guard = _RuntimeGuard(
        max_runtime_seconds=config.max_runtime_seconds,
        monotonic_fn=monotonic_fn,
    )

    log.append(
        "report_roundtrip_started",
        bridge_attempt_id=bridge_attempt_id,
        max_cycles=config.max_cycles,
        max_runtime_seconds=config.max_runtime_seconds,
        pm_response_timeout_seconds=config.pm_response_timeout_seconds,
    )
    _append_debug(
        state_debug_log,
        "bridge_state_transition",
        bridge_attempt_id=bridge_attempt_id,
        phase="roundtrip",
        state_name="report_roundtrip_started",
        result="started",
        app=config.targets.pm_assistant.app_name,
        profile=config.targets.pm_assistant.profile,
    )
    _append_debug(
        action_debug_log,
        "gui_action_trace_enabled",
        bridge_attempt_id=bridge_attempt_id,
        phase="roundtrip",
        action="debug_trace",
        result="enabled",
    )
    log.append("report_roundtrip_auto_confirm_enabled")

    try:
        pm_response_copy_attempted = False
        pm_response_copy_succeeded = False
        pm_response_saved = False
        codex_next_prompt_extracted = False
        local_agent_prompt_staged = False

        guard.check()
        report = report_path.read_text(encoding="utf-8")
        pm_prompt = build_report_roundtrip_pm_prompt(
            report,
            bridge_attempt_id=bridge_attempt_id,
        )
        pm_prompt_sentinel = build_pm_prompt_sentinel(bridge_attempt_id)
        pm_prompt_sentinel_hash = _text_sha256(pm_prompt_sentinel)
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="pm_prompt_stage",
            app=config.targets.pm_assistant.app_name,
            profile=config.targets.pm_assistant.profile,
            action="pm_prompt_sentinel_created",
            result="succeeded",
            sentinel_id=bridge_attempt_id,
            sentinel_hash=pm_prompt_sentinel_hash,
            prompt_length=len(pm_prompt),
            prompt_hash=_text_sha256(pm_prompt),
        )
        log.append(
            "pm_prompt_sentinel_created",
            sentinel_id=bridge_attempt_id,
            sentinel_hash=pm_prompt_sentinel_hash,
        )
        blocked = _check_safety(
            workspace_dir=workspace_dir,
            state_store=store,
            event_log=log,
            source_text=pm_prompt,
            phase="pm_prompt_submit",
        )
        if blocked:
            return blocked

        outbox_dir.mkdir(parents=True, exist_ok=True)
        pm_prompt_path.write_text(pm_prompt, encoding="utf-8")
        log.append("pm_prompt_staged", prompt_path=str(pm_prompt_path), mode="report_roundtrip")
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="pm_prompt_stage",
            app=config.targets.pm_assistant.app_name,
            profile=config.targets.pm_assistant.profile,
            action="stage_pm_prompt",
            result="succeeded",
            prompt_length=len(pm_prompt),
            prompt_hash=_text_sha256(pm_prompt),
            prompt_path=str(pm_prompt_path),
        )

        if config.require_pm_backend_preflight:
            guard.check()
            preflight_result = (
                pm_backend_preflight()
                if pm_backend_preflight
                else preflight_pm_backend(
                    target=config.targets.pm_assistant,
                    activate=True,
                    event_log=log,
                )
            )
            if not preflight_result.succeeded:
                failure = preflight_result.failure_reason or "PM assistant backend preflight failed."
                log.append(
                    "pm_backend_preflight_failed",
                    failure_reason=failure,
                    preflight=format_pm_backend_preflight_result(preflight_result),
                )
                raise ReportRoundtripError(f"PM backend preflight failed: {failure}")

        guard.check()
        log.append(
            "pm_phase_started",
            app_name=config.targets.pm_assistant.app_name,
            profile=config.targets.pm_assistant.profile,
        )
        _append_debug(
            state_debug_log,
            "bridge_state_transition",
            bridge_attempt_id=bridge_attempt_id,
            phase="pm",
            state_name="pm_phase_started",
            result="started",
            app=config.targets.pm_assistant.app_name,
            profile=config.targets.pm_assistant.profile,
        )
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="pm_activate",
            app=config.targets.pm_assistant.app_name,
            profile=config.targets.pm_assistant.profile,
            action="activate_app",
            result="attempted",
        )
        gui.activate_app(config.targets.pm_assistant)
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="pm_activate",
            app=config.targets.pm_assistant.app_name,
            profile=config.targets.pm_assistant.profile,
            action="activate_app",
            result="succeeded",
        )
        log.append("pm_app_activated", app_name=config.targets.pm_assistant.app_name)
        guard.check()
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="pm_clipboard",
            app=config.targets.pm_assistant.app_name,
            profile=config.targets.pm_assistant.profile,
            action="set_clipboard",
            result="attempted",
            prompt_length=len(pm_prompt),
            prompt_hash=_text_sha256(pm_prompt),
        )
        gui.copy_text_to_clipboard(pm_prompt)
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="pm_clipboard",
            app=config.targets.pm_assistant.app_name,
            profile=config.targets.pm_assistant.profile,
            action="set_clipboard",
            result="succeeded",
            prompt_length=len(pm_prompt),
            prompt_hash=_text_sha256(pm_prompt),
        )
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="pm_paste",
            app=config.targets.pm_assistant.app_name,
            profile=config.targets.pm_assistant.profile,
            action="paste",
            result="attempted",
            paste_backend=config.targets.pm_assistant.paste_backend,
            prompt_length=len(pm_prompt),
            prompt_hash=_text_sha256(pm_prompt),
        )
        pm_paste_result = gui.paste_clipboard()
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="pm_paste",
            app=config.targets.pm_assistant.app_name,
            profile=config.targets.pm_assistant.profile,
            action="paste",
            result="succeeded" if pm_paste_result is not False else "failed",
            paste_backend=config.targets.pm_assistant.paste_backend,
            prompt_length=len(pm_prompt),
            prompt_hash=_text_sha256(pm_prompt),
        )
        if pm_paste_result is False:
            raise ReportRoundtripError("PM prompt paste did not report success.")
        log.append("pm_prompt_pasted", prompt_path=str(pm_prompt_path))
        guard.check()
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="pm_submit",
            app=config.targets.pm_assistant.app_name,
            profile=config.targets.pm_assistant.profile,
            action="submit",
            result="attempted",
        )
        gui.submit()
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="pm_submit",
            app=config.targets.pm_assistant.app_name,
            profile=config.targets.pm_assistant.profile,
            action="submit",
            result="succeeded",
        )
        log.append("pm_prompt_submitted", mode="report_roundtrip")

        guard.check()
        log.append("pm_response_wait_started", timeout_seconds=config.pm_response_timeout_seconds)
        _append_debug(
            state_debug_log,
            "bridge_state_transition",
            bridge_attempt_id=bridge_attempt_id,
            phase="pm_response_wait",
            state_name="pm_response_wait_started",
            result="started",
            app=config.targets.pm_assistant.app_name,
            profile=config.targets.pm_assistant.profile,
            timeout_seconds=config.pm_response_timeout_seconds,
        )
        gui.expect_response_contains("CODEX_NEXT_PROMPT")
        gui.wait_for_response(config.pm_response_timeout_seconds)
        guard.check()
        log.append(
            "pm_response_generation_finished",
            app_name=config.targets.pm_assistant.app_name,
            profile=config.targets.pm_assistant.profile,
        )
        _append_debug(
            state_debug_log,
            "bridge_state_transition",
            bridge_attempt_id=bridge_attempt_id,
            phase="pm_response_wait",
            state_name="pm_response_generation_finished",
            result="succeeded",
            app=config.targets.pm_assistant.app_name,
            profile=config.targets.pm_assistant.profile,
        )
        log.append(
            "pm_response_copy_phase_started",
            app_name=config.targets.pm_assistant.app_name,
            profile=config.targets.pm_assistant.profile,
        )
        _append_debug(
            state_debug_log,
            "bridge_state_transition",
            bridge_attempt_id=bridge_attempt_id,
            phase="pm_response_copy",
            state_name="pm_response_copy_phase_started",
            result="started",
            app=config.targets.pm_assistant.app_name,
            profile=config.targets.pm_assistant.profile,
        )
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="pm_response_copy",
            app=config.targets.pm_assistant.app_name,
            profile=config.targets.pm_assistant.profile,
            action="activate_app",
            result="attempted",
            reason="reactivate_before_response_copy",
        )
        gui.activate_app(config.targets.pm_assistant)
        log.append(
            "pm_target_reactivated_before_copy",
            app_name=config.targets.pm_assistant.app_name,
            profile=config.targets.pm_assistant.profile,
        )
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="pm_response_copy",
            app=config.targets.pm_assistant.app_name,
            profile=config.targets.pm_assistant.profile,
            action="activate_app",
            result="succeeded",
            reason="reactivate_before_response_copy",
        )
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="pm_response_capture",
            app=config.targets.pm_assistant.app_name,
            profile=config.targets.pm_assistant.profile,
            action="copy_response",
            result="attempted",
        )
        pm_response_copy_attempted = True
        pm_response = gui.copy_response_text()
        pm_response_copy_succeeded = True
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="pm_response_capture",
            app=config.targets.pm_assistant.app_name,
            profile=config.targets.pm_assistant.profile,
            action="copy_response",
            result="succeeded",
            response_length=len(pm_response),
            response_hash=_text_sha256(pm_response),
        )
        log.append(
            "pm_response_copy_clicked",
            app_name=config.targets.pm_assistant.app_name,
            profile=config.targets.pm_assistant.profile,
            response_length=len(pm_response),
        )
        log.append("pm_response_copied", length=len(pm_response), mode="report_roundtrip")
        pm_response_path.write_text(pm_response, encoding="utf-8")
        pm_response_saved = True
        log.append("pm_response_saved", response_path=str(pm_response_path))
        _append_debug(
            state_debug_log,
            "bridge_state_transition",
            bridge_attempt_id=bridge_attempt_id,
            phase="pm_response_copy",
            state_name="pm_response_saved",
            result="succeeded",
            response_path=str(pm_response_path),
            response_length=len(pm_response),
        )

        extracted_prompt = extract_codex_next_prompt(pm_response)
        extracted_prompt_path.write_text(extracted_prompt, encoding="utf-8")
        codex_next_prompt_extracted = True
        log.append("codex_next_prompt_extracted", prompt_path=str(extracted_prompt_path))
        _append_debug(
            state_debug_log,
            "bridge_state_transition",
            bridge_attempt_id=bridge_attempt_id,
            phase="pm_response_extract",
            state_name="codex_next_prompt_extracted",
            result="succeeded",
            prompt_path=str(extracted_prompt_path),
            prompt_length=len(extracted_prompt),
            prompt_hash=_text_sha256(extracted_prompt),
        )

        extracted_prompt_hash = _text_sha256(extracted_prompt)
        command_id = f"cmd_{uuid4().hex[:12]}"
        command_prompt_dir = outbox_dir / "local_agent_commands"
        command_prompt_dir.mkdir(parents=True, exist_ok=True)
        command_prompt_path = command_prompt_dir / f"{command_id}.md"
        command_prompt_path.write_text(extracted_prompt, encoding="utf-8")
        dedupe_key = f"report_roundtrip:{bridge_attempt_id}:{extracted_prompt_hash}"
        command = Command(
            id=command_id,
            type=CommandType.USER_MANUAL_COMMAND,
            source="pm_assistant_report_roundtrip",
            prompt_path=str(command_prompt_path),
            dedupe_key=dedupe_key,
            metadata={
                "bridge_attempt_id": bridge_attempt_id,
                "latest_extracted_prompt_path": str(extracted_prompt_path),
                "prompt_hash": extracted_prompt_hash,
            },
        )
        log.append(
            "local_agent_command_enqueue_started",
            bridge_attempt_id=bridge_attempt_id,
            command_id=command.id,
            command_status=command.status.value,
            dedupe_key=dedupe_key,
            prompt_source_type=_command_prompt_source_type(command),
            prompt_length=len(extracted_prompt),
            prompt_hash=extracted_prompt_hash,
        )
        _debug_terminal(config, "Bridge: enqueue local-agent command")
        enqueue_result = command_queue.enqueue_with_result(command)
        selected_command_id = enqueue_result.command_id
        log.append(
            "local_agent_command_enqueue_result",
            bridge_attempt_id=bridge_attempt_id,
            command_id=selected_command_id,
            requested_command_id=command.id,
            command_status=(
                enqueue_result.existing_status.value
                if enqueue_result.existing_status
                else command.status.value
            ),
            added=enqueue_result.added,
            deduped=enqueue_result.deduped,
            dedupe_key=dedupe_key,
            prompt_source_type=_command_prompt_source_type(enqueue_result.command or command),
            prompt_length=len(extracted_prompt),
            prompt_hash=extracted_prompt_hash,
            reason=enqueue_result.reason,
        )
        if enqueue_result.deduped:
            log.append(
                "local_agent_command_dedupe_result",
                bridge_attempt_id=bridge_attempt_id,
                command_id=enqueue_result.existing_command_id,
                command_status=enqueue_result.existing_status.value
                if enqueue_result.existing_status
                else None,
                dedupe_key=dedupe_key,
                prompt_source_type=_command_prompt_source_type(enqueue_result.command or command),
                prompt_length=len(extracted_prompt),
                prompt_hash=extracted_prompt_hash,
            )
        log.append(
            "pm_command_enqueued",
            bridge_attempt_id=bridge_attempt_id,
            command_id=selected_command_id or command.id,
            requested_command_id=command.id,
            added=enqueue_result.added,
            deduped=enqueue_result.deduped,
            mode="report_roundtrip",
        )
        _debug_terminal(
            config,
            "Bridge: enqueue result "
            f"command_id={selected_command_id or '<missing>'} "
            f"status={enqueue_result.existing_status.value if enqueue_result.existing_status else command.status.value} "
            f"dedupe={'yes' if enqueue_result.deduped else 'no'}",
        )
        if selected_command_id is None:
            log.append(
                "local_agent_command_not_available_after_enqueue",
                bridge_attempt_id=bridge_attempt_id,
                command_id=None,
                requested_command_id=command.id,
                failure_reason="command_id_missing_after_enqueue",
                dedupe_key=dedupe_key,
                prompt_length=len(extracted_prompt),
                prompt_hash=extracted_prompt_hash,
            )
            raise ReportRoundtripError("command_id_missing_after_enqueue")
        log.append(
            "local_agent_command_id_selected",
            bridge_attempt_id=bridge_attempt_id,
            command_id=selected_command_id,
            requested_command_id=command.id,
            command_status=(
                enqueue_result.existing_status.value
                if enqueue_result.existing_status
                else command.status.value
            ),
            dedupe_key=dedupe_key,
            prompt_source_type=_command_prompt_source_type(enqueue_result.command or command),
        )

        guard.check()
        log.append(
            "local_agent_dispatch_by_id_started",
            bridge_attempt_id=bridge_attempt_id,
            command_id=selected_command_id,
            dedupe_key=dedupe_key,
            prompt_source_type=_command_prompt_source_type(enqueue_result.command or command),
            prompt_length=len(extracted_prompt),
            prompt_hash=extracted_prompt_hash,
        )
        _debug_terminal(config, f"Bridge: dispatch command by id={selected_command_id}")
        dispatch_result = Dispatcher(
            queue=command_queue,
            prompt_builder=PromptBuilder(config.template_dir),
            workspace_dir=workspace_dir,
            event_log=log,
        ).stage_command_by_id(selected_command_id, dry_run=False)
        log.append(
            "local_agent_dispatch_by_id_result",
            bridge_attempt_id=bridge_attempt_id,
            command_id=selected_command_id,
            command_status=dispatch_result.command_status,
            result="succeeded" if dispatch_result.staged else "failed",
            failure_reason=dispatch_result.reason,
            prompt_source_type=_command_prompt_source_type(dispatch_result.command)
            if dispatch_result.command
            else None,
            prompt_length=len(dispatch_result.prompt) if dispatch_result.prompt else 0,
            prompt_hash=_text_sha256(dispatch_result.prompt) if dispatch_result.prompt else None,
        )
        if dispatch_result.blocked:
            log.append("report_roundtrip_safety_blocked", phase="local_agent_prompt_stage")
            return ReportRoundtripResult(completed=False, reason="SAFETY_BLOCKED", safety_paused=True)
        if dispatch_result.command is None:
            failure_reason = dispatch_result.reason or "local_agent_command_not_available_after_enqueue"
            log.append(
                "local_agent_command_not_available_after_enqueue",
                bridge_attempt_id=bridge_attempt_id,
                command_id=selected_command_id,
                command_status=dispatch_result.command_status,
                failure_reason=failure_reason,
                dedupe_key=dedupe_key,
                prompt_length=len(extracted_prompt),
                prompt_hash=extracted_prompt_hash,
            )
            _debug_terminal(
                config,
                "Bridge: cannot dispatch command after enqueue: "
                f"{failure_reason}",
            )
            raise ReportRoundtripError(failure_reason)
        _debug_terminal(
            config,
            "Bridge: staged next_local_agent_prompt.md "
            f"len={len(dispatch_result.prompt)}",
        )
        _debug_terminal(config, "Bridge: proceeding to Codex handoff")

        log.append(
            "local_agent_prompt_staged",
            command_id=dispatch_result.command.id,
            prompt_path=str(dispatch_result.prompt_path),
            mode="report_roundtrip",
        )
        log.append(
            "local_agent_prompt_staged_from_command_id",
            bridge_attempt_id=bridge_attempt_id,
            command_id=dispatch_result.command.id,
            command_status=dispatch_result.command.status.value,
            prompt_path=str(dispatch_result.prompt_path),
            prompt_source_type=_command_prompt_source_type(dispatch_result.command),
            prompt_length=len(dispatch_result.prompt),
            prompt_hash=_text_sha256(dispatch_result.prompt),
        )
        local_agent_prompt_staged = True
        local_agent_prompt_length = len(dispatch_result.prompt)
        local_agent_prompt_hash = _text_sha256(dispatch_result.prompt)
        log.append(
            "local_agent_prompt_debug_checkpoint",
            command_id=dispatch_result.command.id,
            local_agent_prompt_path=str(dispatch_result.prompt_path),
            local_agent_prompt_length=local_agent_prompt_length,
            local_agent_prompt_hash=local_agent_prompt_hash,
        )
        log.append(
            "local_agent_prompt_loaded",
            command_id=dispatch_result.command.id,
            local_agent_prompt_path=str(dispatch_result.prompt_path),
            local_agent_prompt_length=local_agent_prompt_length,
        )
        log.append(
            "local_agent_prompt_hash_computed",
            command_id=dispatch_result.command.id,
            local_agent_prompt_path=str(dispatch_result.prompt_path),
            local_agent_prompt_hash=local_agent_prompt_hash,
        )
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="local_agent_stage",
            app=config.targets.local_agent.app_name,
            profile=config.targets.local_agent.profile,
            action="stage_local_agent_prompt",
            result="succeeded",
            command_id=dispatch_result.command.id,
            prompt_path=str(dispatch_result.prompt_path),
            prompt_length=local_agent_prompt_length,
            prompt_hash=local_agent_prompt_hash,
        )
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="local_agent_stage",
            app=config.targets.local_agent.app_name,
            profile=config.targets.local_agent.profile,
            action="local_agent_prompt_loaded",
            result="succeeded",
            command_id=dispatch_result.command.id,
            prompt_path=str(dispatch_result.prompt_path),
            prompt_length=local_agent_prompt_length,
            prompt_hash=local_agent_prompt_hash,
        )
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="local_agent_stage",
            app=config.targets.local_agent.app_name,
            profile=config.targets.local_agent.profile,
            action="local_agent_prompt_hash_computed",
            result="succeeded",
            command_id=dispatch_result.command.id,
            prompt_path=str(dispatch_result.prompt_path),
            prompt_length=local_agent_prompt_length,
            prompt_hash=local_agent_prompt_hash,
        )
        if local_agent_prompt_length <= 0:
            log.append(
                "local_agent_prompt_empty",
                command_id=dispatch_result.command.id,
                prompt_path=str(dispatch_result.prompt_path),
            )
            _append_debug(
                action_debug_log,
                "gui_action",
                bridge_attempt_id=bridge_attempt_id,
                phase="local_agent_submit_guard",
                app=config.targets.local_agent.app_name,
                profile=config.targets.local_agent.profile,
                action="local_agent_submit_guard_checked",
                result="failed",
                failure_reason="local_agent_prompt_empty",
            )
            return ReportRoundtripResult(
                completed=False,
                reason="local_agent_prompt_empty",
                cycles_completed=0,
                pm_prompt_path=pm_prompt_path,
                pm_response_path=pm_response_path,
                extracted_prompt_path=extracted_prompt_path,
                local_agent_prompt_path=dispatch_result.prompt_path,
            )
        if not config.submit_local_agent:
            log.append(
                "report_roundtrip_stopped_before_local_agent_submit",
                command_id=dispatch_result.command.id,
                prompt_path=str(dispatch_result.prompt_path),
            )
            return ReportRoundtripResult(
                completed=True,
                reason="LOCAL_AGENT_PROMPT_STAGED_ONLY",
                cycles_completed=1,
                pm_prompt_path=pm_prompt_path,
                pm_response_path=pm_response_path,
                extracted_prompt_path=extracted_prompt_path,
                local_agent_prompt_path=dispatch_result.prompt_path,
            )
        blocked = _check_safety(
            workspace_dir=workspace_dir,
            state_store=store,
            event_log=log,
            source_text=dispatch_result.prompt,
            phase="local_agent_prompt_submit",
        )
        if blocked:
            return blocked
        safety_passed = True

        log.append(
            "local_agent_phase_started",
            command_id=dispatch_result.command.id,
            prompt_path=str(dispatch_result.prompt_path),
        )
        _append_debug(
            state_debug_log,
            "bridge_state_transition",
            bridge_attempt_id=bridge_attempt_id,
            phase="local_agent",
            state_name="local_agent_phase_started",
            result="started",
            app=config.targets.local_agent.app_name,
            profile=config.targets.local_agent.profile,
            prompt_length=local_agent_prompt_length,
            prompt_hash=local_agent_prompt_hash,
        )

        queue_handoff_mode = (
            config.stop_after_local_agent_submit
            and is_noop_validation_prompt(dispatch_result.prompt)
        )
        gui.set_local_agent_queue_handoff_mode(queue_handoff_mode)
        if queue_handoff_mode:
            log.append(
                "local_agent_queue_handoff_mode_enabled",
                command_id=dispatch_result.command.id,
                task_id=NOOP_VALIDATION_TASK_ID,
            )

        guard.check()
        phase_order_block_reason = _codex_activation_phase_order_block_reason(
            pm_response_copy_attempted=pm_response_copy_attempted,
            pm_response_copy_succeeded=pm_response_copy_succeeded,
            pm_response_saved=pm_response_saved,
            codex_next_prompt_extracted=codex_next_prompt_extracted,
            local_agent_prompt_staged=local_agent_prompt_staged,
        )
        if phase_order_block_reason is not None:
            log.append(
                "phase_order_violation",
                requested_action="activate_codex",
                failure_reason=phase_order_block_reason,
                pm_response_copy_attempted=pm_response_copy_attempted,
                pm_response_copy_succeeded=pm_response_copy_succeeded,
                pm_response_saved=pm_response_saved,
                codex_next_prompt_extracted=codex_next_prompt_extracted,
                local_agent_prompt_staged=local_agent_prompt_staged,
            )
            _append_debug(
                action_debug_log,
                "gui_action",
                bridge_attempt_id=bridge_attempt_id,
                phase="local_agent_activate",
                app=config.targets.local_agent.app_name,
                profile=config.targets.local_agent.profile,
                action="activate_app",
                result="blocked",
                failure_reason=phase_order_block_reason,
            )
            return ReportRoundtripResult(
                completed=False,
                reason=phase_order_block_reason,
                cycles_completed=0,
                pm_prompt_path=pm_prompt_path,
                pm_response_path=pm_response_path,
                extracted_prompt_path=extracted_prompt_path,
                local_agent_prompt_path=dispatch_result.prompt_path,
            )
        log.append(
            "codex_activation_started",
            command_id=dispatch_result.command.id,
            app_name=config.targets.local_agent.app_name,
            profile=config.targets.local_agent.profile,
        )
        _append_debug(
            state_debug_log,
            "bridge_state_transition",
            bridge_attempt_id=bridge_attempt_id,
            phase="local_agent_activate",
            state_name="codex_activation_started",
            result="started",
            app=config.targets.local_agent.app_name,
            profile=config.targets.local_agent.profile,
        )
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="local_agent_activate",
            app=config.targets.local_agent.app_name,
            profile=config.targets.local_agent.profile,
            action="local_agent_codex_focus_started",
            result="attempted",
            prompt_length=local_agent_prompt_length,
            prompt_hash=local_agent_prompt_hash,
        )
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="local_agent_activate",
            app=config.targets.local_agent.app_name,
            profile=config.targets.local_agent.profile,
            action="activate_app",
            result="attempted",
            prompt_length=local_agent_prompt_length,
            prompt_hash=local_agent_prompt_hash,
        )
        gui.activate_app(config.targets.local_agent)
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="local_agent_activate",
            app=config.targets.local_agent.app_name,
            profile=config.targets.local_agent.profile,
            action="local_agent_codex_focus_succeeded",
            result="succeeded",
            prompt_length=local_agent_prompt_length,
            prompt_hash=local_agent_prompt_hash,
        )
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="local_agent_activate",
            app=config.targets.local_agent.app_name,
            profile=config.targets.local_agent.profile,
            action="activate_app",
            result="succeeded",
            prompt_length=local_agent_prompt_length,
            prompt_hash=local_agent_prompt_hash,
        )
        log.append("local_agent_app_activated", app_name=config.targets.local_agent.app_name)
        guard.check()
        local_agent_clipboard_set_attempted = False
        log.append(
            "local_agent_clipboard_set_attempted",
            command_id=dispatch_result.command.id,
            local_agent_prompt_length=local_agent_prompt_length,
            local_agent_prompt_hash=local_agent_prompt_hash,
        )
        local_agent_clipboard_set_attempted = True
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="local_agent_clipboard",
            app=config.targets.local_agent.app_name,
            profile=config.targets.local_agent.profile,
            action="set_clipboard",
            result="attempted",
            prompt_length=local_agent_prompt_length,
            prompt_hash=local_agent_prompt_hash,
        )
        local_agent_clipboard_set_succeeded = False
        try:
            gui.copy_text_to_clipboard(dispatch_result.prompt)
        except Exception as error:
            log.append(
                "local_agent_clipboard_set_failed",
                command_id=dispatch_result.command.id,
                local_agent_prompt_length=local_agent_prompt_length,
                local_agent_prompt_hash=local_agent_prompt_hash,
                error=str(error),
            )
            _append_debug(
                action_debug_log,
                "gui_action",
                bridge_attempt_id=bridge_attempt_id,
                phase="local_agent_clipboard",
                app=config.targets.local_agent.app_name,
                profile=config.targets.local_agent.profile,
                action="set_clipboard",
                result="failed",
                prompt_length=local_agent_prompt_length,
                prompt_hash=local_agent_prompt_hash,
                error=str(error),
            )
            return ReportRoundtripResult(
                completed=False,
                reason="local_agent_clipboard_set_failed",
                cycles_completed=0,
                pm_prompt_path=pm_prompt_path,
                pm_response_path=pm_response_path,
                extracted_prompt_path=extracted_prompt_path,
                local_agent_prompt_path=dispatch_result.prompt_path,
            )
        local_agent_clipboard_set_succeeded = True
        log.append(
            "local_agent_clipboard_set_succeeded",
            command_id=dispatch_result.command.id,
            local_agent_prompt_length=local_agent_prompt_length,
            local_agent_prompt_hash=local_agent_prompt_hash,
        )
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="local_agent_clipboard",
            app=config.targets.local_agent.app_name,
            profile=config.targets.local_agent.profile,
            action="set_clipboard",
            result="succeeded",
            prompt_length=local_agent_prompt_length,
            prompt_hash=local_agent_prompt_hash,
        )
        clipboard_readback = gui.read_clipboard_text()
        local_agent_clipboard_readback_matches_prompt_hash: bool | None = None
        if clipboard_readback is not None:
            clipboard_readback_hash = _text_sha256(clipboard_readback)
            local_agent_clipboard_readback_matches_prompt_hash = (
                clipboard_readback_hash == local_agent_prompt_hash
            )
            log.append(
                "local_agent_clipboard_readback_verified",
                command_id=dispatch_result.command.id,
                clipboard_length=len(clipboard_readback),
                clipboard_hash=clipboard_readback_hash,
                clipboard_readback_matches_prompt_hash=(
                    local_agent_clipboard_readback_matches_prompt_hash
                ),
            )
            _append_debug(
                action_debug_log,
                "gui_action",
                bridge_attempt_id=bridge_attempt_id,
                phase="local_agent_clipboard",
                app=config.targets.local_agent.app_name,
                profile=config.targets.local_agent.profile,
                action="local_agent_clipboard_readback_verified",
                result=(
                    "succeeded"
                    if local_agent_clipboard_readback_matches_prompt_hash
                    else "failed"
                ),
                clipboard_length=len(clipboard_readback),
                clipboard_hash=clipboard_readback_hash,
                prompt_length=local_agent_prompt_length,
                prompt_hash=local_agent_prompt_hash,
            )
            if not local_agent_clipboard_readback_matches_prompt_hash:
                return ReportRoundtripResult(
                    completed=False,
                    reason="local_agent_clipboard_readback_mismatch",
                    cycles_completed=0,
                    pm_prompt_path=pm_prompt_path,
                    pm_response_path=pm_response_path,
                    extracted_prompt_path=extracted_prompt_path,
                    local_agent_prompt_path=dispatch_result.prompt_path,
                )
        local_agent_paste_attempted = False
        local_agent_paste_succeeded = False
        local_agent_paste_backend_succeeded = False
        local_agent_focus_completed = True
        log.append(
            "local_agent_paste_attempted",
            command_id=dispatch_result.command.id,
            local_agent_focus_strategy=config.targets.local_agent.focus_strategy,
            local_agent_click_backend=config.targets.local_agent.click_backend,
            local_agent_paste_backend=config.targets.local_agent.paste_backend,
            local_agent_prompt_length=local_agent_prompt_length,
            local_agent_prompt_hash=local_agent_prompt_hash,
        )
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="local_agent_paste",
            app=config.targets.local_agent.app_name,
            profile=config.targets.local_agent.profile,
            action="paste",
            result="attempted",
            focus_strategy=config.targets.local_agent.focus_strategy,
            click_backend=config.targets.local_agent.click_backend,
            paste_backend=config.targets.local_agent.paste_backend,
            prompt_length=local_agent_prompt_length,
            prompt_hash=local_agent_prompt_hash,
        )
        local_agent_paste_attempted = True
        try:
            paste_result = gui.paste_clipboard()
        except Exception as error:
            if "stop_on_idle_timeout is enabled" in str(error):
                raise
            known_reasons = (
                "local_agent_prompt_empty",
                "local_agent_clipboard_set_failed",
                "local_agent_clipboard_readback_mismatch",
                "local_agent_composer_click_failed",
                "local_agent_paste_not_attempted",
                "local_agent_paste_backend_failed",
                "local_agent_paste_not_reflected_in_codex_state",
            )
            error_text = str(error)
            failure_reason = next(
                (reason for reason in known_reasons if reason in error_text),
                "local_agent_paste_backend_failed",
            )
            log.append(
                failure_reason,
                command_id=dispatch_result.command.id,
                prompt_path=str(dispatch_result.prompt_path),
                error=error_text,
            )
            _append_debug(
                action_debug_log,
                "gui_action",
                bridge_attempt_id=bridge_attempt_id,
                phase="local_agent_paste",
                app=config.targets.local_agent.app_name,
                profile=config.targets.local_agent.profile,
                action="paste",
                result="failed",
                failure_reason=failure_reason,
                error=error_text,
                prompt_length=local_agent_prompt_length,
                prompt_hash=local_agent_prompt_hash,
            )
            return ReportRoundtripResult(
                completed=False,
                reason=failure_reason,
                cycles_completed=0,
                pm_prompt_path=pm_prompt_path,
                pm_response_path=pm_response_path,
                extracted_prompt_path=extracted_prompt_path,
                local_agent_prompt_path=dispatch_result.prompt_path,
            )
        local_agent_paste_attempted = bool(
            getattr(gui, "last_local_agent_paste_attempted", local_agent_paste_attempted)
        )
        local_agent_paste_backend_succeeded = bool(
            getattr(gui, "last_local_agent_paste_backend_success", paste_result is not False)
        )
        local_agent_paste_succeeded = paste_result is not False
        if not local_agent_paste_succeeded:
            log.append(
                "local_agent_click_succeeded_but_paste_missing",
                command_id=dispatch_result.command.id,
                prompt_path=str(dispatch_result.prompt_path),
            )
            _append_debug(
                action_debug_log,
                "gui_action",
                bridge_attempt_id=bridge_attempt_id,
                phase="local_agent_paste",
                app=config.targets.local_agent.app_name,
                profile=config.targets.local_agent.profile,
                action="paste",
                result="failed",
                failure_reason="local_agent_click_succeeded_but_paste_missing",
                prompt_length=local_agent_prompt_length,
                prompt_hash=local_agent_prompt_hash,
            )
            return ReportRoundtripResult(
                completed=False,
                reason="local_agent_click_succeeded_but_paste_missing",
                cycles_completed=0,
                pm_prompt_path=pm_prompt_path,
                pm_response_path=pm_response_path,
                extracted_prompt_path=extracted_prompt_path,
                local_agent_prompt_path=dispatch_result.prompt_path,
            )
        codex_state_before_paste = getattr(gui, "last_local_agent_paste_state_before", None)
        codex_state_after_paste = getattr(gui, "last_local_agent_paste_state_after", None)
        codex_send_ready_after_paste = getattr(gui, "last_local_agent_paste_send_ready", None)
        codex_state_after_paste_confidence = getattr(
            gui,
            "last_local_agent_paste_state_after_confidence",
            None,
        )
        codex_state_after_paste_asset = getattr(
            gui,
            "last_local_agent_paste_state_after_asset",
            None,
        )
        adapter_readback_match = getattr(
            gui,
            "last_local_agent_clipboard_readback_matches_prompt_hash",
            None,
        )
        if adapter_readback_match is not None:
            local_agent_clipboard_readback_matches_prompt_hash = adapter_readback_match
        log.append(
            "local_agent_paste_succeeded",
            command_id=dispatch_result.command.id,
            local_agent_prompt_length=local_agent_prompt_length,
            local_agent_prompt_hash=local_agent_prompt_hash,
        )
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="local_agent_paste",
            app=config.targets.local_agent.app_name,
            profile=config.targets.local_agent.profile,
            action="paste",
            result="succeeded",
            prompt_length=local_agent_prompt_length,
            prompt_hash=local_agent_prompt_hash,
        )
        log.append(
            "local_agent_paste_state_summary",
            command_id=dispatch_result.command.id,
            codex_state_before_paste=codex_state_before_paste,
            codex_state_after_paste=codex_state_after_paste,
            codex_send_ready_after_paste=codex_send_ready_after_paste,
            codex_state_after_paste_confidence=codex_state_after_paste_confidence,
            codex_state_after_paste_asset=codex_state_after_paste_asset,
            paste_backend_succeeded=local_agent_paste_backend_succeeded,
            clipboard_readback_matches_prompt_hash=(
                local_agent_clipboard_readback_matches_prompt_hash
            ),
        )
        log.append("local_agent_prompt_pasted", prompt_path=str(dispatch_result.prompt_path))
        try:
            pre_submit_check = gui.inspect_local_agent_before_submit(
                config.targets.local_agent,
                dispatch_result.prompt,
            )
        except Exception as error:
            pre_submit_check = LocalAgentPreSubmitCheck(
                target_app=config.targets.local_agent.app_name,
                prompt_length=local_agent_prompt_length,
                clipboard_length=len(clipboard_readback) if clipboard_readback is not None else 0,
                prompt_text_present=None,
                focused_element_summary=f"diagnostic unavailable: {error}",
            )
            log.append(
                "local_agent_prompt_presence_check_result",
                command_id=dispatch_result.command.id,
                prompt_text_present=None,
                diagnostic_only=True,
                error=str(error),
            )
        extracted_prompt_noop_eligible = is_noop_validation_prompt(extracted_prompt)
        staged_prompt_noop_eligible = is_noop_validation_prompt(dispatch_result.prompt)
        noop_unverified_submit_eligible = (
            config.targets.local_agent.allow_unverified_submit_for_noop_dogfood
            and (extracted_prompt_noop_eligible or staged_prompt_noop_eligible)
        )
        paste_checkpoint_passed = (
            local_agent_prompt_length > 0
            and safety_passed
            and local_agent_focus_completed
            and local_agent_clipboard_set_attempted
            and local_agent_clipboard_set_succeeded
            and local_agent_clipboard_readback_matches_prompt_hash is not False
            and local_agent_paste_attempted
            and local_agent_paste_succeeded
            and local_agent_paste_backend_succeeded
        )
        visual_send_ready_before_submit = codex_send_ready_after_paste is True
        noop_submit_exception_allowed = (
            noop_unverified_submit_eligible
            and paste_checkpoint_passed
            and codex_state_after_paste != "IDLE"
        )
        log.append(
            "local_agent_prompt_presence_check_started",
            command_id=dispatch_result.command.id,
            prompt_length=local_agent_prompt_length,
            prompt_hash=local_agent_prompt_hash,
            prompt_presence_verification_method=(
                pre_submit_check.selected_input_candidate_summary
                or pre_submit_check.focused_element_summary
                or "unknown"
            ),
        )
        log.append(
            "local_agent_prompt_presence_check_result",
            command_id=dispatch_result.command.id,
            prompt_text_present=pre_submit_check.prompt_text_present,
            prompt_presence_verification_method=(
                pre_submit_check.selected_input_candidate_summary
                or pre_submit_check.focused_element_summary
                or "unknown"
            ),
        )
        log.append(
            "local_agent_visual_send_ready_check_result",
            command_id=dispatch_result.command.id,
            visual_send_ready_before_submit=visual_send_ready_before_submit,
            codex_state_after_paste=codex_state_after_paste,
            codex_send_ready_after_paste=codex_send_ready_after_paste,
        )
        log.append(
            "local_agent_noop_submit_exception_checked",
            command_id=dispatch_result.command.id,
            noop_unverified_submit_eligible=noop_unverified_submit_eligible,
            extracted_prompt_noop_eligible=extracted_prompt_noop_eligible,
            staged_prompt_noop_eligible=staged_prompt_noop_eligible,
            paste_checkpoint_passed=paste_checkpoint_passed,
            codex_state_after_paste=codex_state_after_paste,
        )
        log.append(
            "paste_checkpoint_passed",
            phase="local_agent",
            command_id=dispatch_result.command.id,
            paste_checkpoint_passed=paste_checkpoint_passed,
            prompt_length=local_agent_prompt_length,
            prompt_hash=local_agent_prompt_hash,
        )
        log.append(
            "local_agent_submit_after_verified_paste",
            command_id=dispatch_result.command.id,
            paste_checkpoint_passed=paste_checkpoint_passed,
            prompt_length=local_agent_prompt_length,
            prompt_hash=local_agent_prompt_hash,
        )
        log.append(
            "submit_after_paste_policy_used",
            phase="local_agent",
            command_id=dispatch_result.command.id,
            paste_checkpoint_passed=paste_checkpoint_passed,
            prompt_length=local_agent_prompt_length,
            prompt_hash=local_agent_prompt_hash,
        )
        log.append(
            "send_ready_check_skipped_by_policy",
            phase="local_agent",
            command_id=dispatch_result.command.id,
            diagnostic_only=True,
            codex_send_ready_after_paste=codex_send_ready_after_paste,
        )
        log.append(
            "prompt_presence_check_skipped_by_policy",
            phase="local_agent",
            command_id=dispatch_result.command.id,
            diagnostic_only=True,
            prompt_text_present=pre_submit_check.prompt_text_present,
        )
        log.append(
            "attachment_verification_skipped_by_policy",
            phase="local_agent",
            command_id=dispatch_result.command.id,
            diagnostic_only=True,
        )
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="local_agent_submit_guard",
            app=config.targets.local_agent.app_name,
            profile=config.targets.local_agent.profile,
            action="local_agent_prompt_presence_check_result",
            result="succeeded" if pre_submit_check.prompt_text_present is True else "unavailable",
            prompt_length=local_agent_prompt_length,
            prompt_hash=local_agent_prompt_hash,
            prompt_text_present=pre_submit_check.prompt_text_present,
            visual_send_ready_before_submit=visual_send_ready_before_submit,
            noop_unverified_submit_eligible=noop_unverified_submit_eligible,
            paste_checkpoint_passed=paste_checkpoint_passed,
            codex_state_after_paste=codex_state_after_paste,
        )
        log.append(
            "local_agent_pre_submit_verification",
            **asdict(pre_submit_check),
        )
        if pre_submit_check.prompt_text_present is not True:
            log.append(
                "codex_prompt_presence_unverifiable",
                command_id=dispatch_result.command.id,
                prompt_text_present=pre_submit_check.prompt_text_present,
                input_candidate_count=pre_submit_check.input_candidate_count,
                selected_input_candidate_summary=pre_submit_check.selected_input_candidate_summary,
                focused_element_summary=pre_submit_check.focused_element_summary,
            )
            log.append(
                "local_agent_prompt_not_present_before_submit",
                command_id=dispatch_result.command.id,
                **asdict(pre_submit_check),
            )
            if noop_unverified_submit_eligible:
                log.append(
                    "local_agent_noop_unverified_submit_eligible",
                    command_id=dispatch_result.command.id,
                    task_id=NOOP_VALIDATION_TASK_ID,
                    allow_unverified_submit_for_noop_dogfood=True,
                    paste_checkpoint_passed=paste_checkpoint_passed,
                    noop_submit_exception_allowed=noop_submit_exception_allowed,
                )
            else:
                log.append(
                    "local_agent_noop_unverified_submit_rejected",
                    command_id=dispatch_result.command.id,
                    task_id=NOOP_VALIDATION_TASK_ID,
                    allow_unverified_submit_for_noop_dogfood=(
                        config.targets.local_agent.allow_unverified_submit_for_noop_dogfood
                    ),
                    prompt_is_noop_validation=staged_prompt_noop_eligible,
                    extracted_prompt_is_noop_validation=extracted_prompt_noop_eligible,
                )
            unverified_submit_allowed = (
                not config.targets.local_agent.require_prompt_presence_verification
                and config.targets.local_agent.allow_unverified_submit
            )
            if visual_send_ready_before_submit:
                log.append(
                    "local_agent_submit_allowed_after_paste_checkpoint",
                    command_id=dispatch_result.command.id,
                    basis="visual_send_ready_after_paste",
                    prompt_text_present=pre_submit_check.prompt_text_present,
                    codex_state_after_paste=codex_state_after_paste,
                    paste_checkpoint_passed=paste_checkpoint_passed,
                )
                _append_debug(
                    action_debug_log,
                    "gui_action",
                    bridge_attempt_id=bridge_attempt_id,
                    phase="local_agent_submit_guard",
                    app=config.targets.local_agent.app_name,
                    profile=config.targets.local_agent.profile,
                    action="local_agent_submit_allowed_after_paste_checkpoint",
                    result="allowed",
                    basis="visual_send_ready_after_paste",
                    prompt_length=local_agent_prompt_length,
                    prompt_hash=local_agent_prompt_hash,
                    codex_state_after_paste=codex_state_after_paste,
                    paste_checkpoint_passed=paste_checkpoint_passed,
                )
            elif noop_submit_exception_allowed:
                log.append(
                    "prompt_presence_unavailable_but_noop_paste_verified",
                    command_id=dispatch_result.command.id,
                    prompt_text_present=pre_submit_check.prompt_text_present,
                    codex_state_after_paste=codex_state_after_paste,
                    paste_checkpoint_passed=paste_checkpoint_passed,
                )
                log.append(
                    "local_agent_noop_submit_exception_allowed",
                    command_id=dispatch_result.command.id,
                    confirmation_basis="noop_unverified_submit_after_paste_checkpoint",
                    task_id=NOOP_VALIDATION_TASK_ID,
                    paste_checkpoint_passed=paste_checkpoint_passed,
                    codex_state_after_paste=codex_state_after_paste,
                )
                log.append(
                    "local_agent_submit_allowed_after_paste_checkpoint",
                    command_id=dispatch_result.command.id,
                    basis="noop_unverified_submit_after_paste_checkpoint",
                    prompt_text_present=pre_submit_check.prompt_text_present,
                    codex_state_after_paste=codex_state_after_paste,
                    paste_checkpoint_passed=paste_checkpoint_passed,
                )
                _append_debug(
                    action_debug_log,
                    "gui_action",
                    bridge_attempt_id=bridge_attempt_id,
                    phase="local_agent_submit_guard",
                    app=config.targets.local_agent.app_name,
                    profile=config.targets.local_agent.profile,
                    action="local_agent_noop_submit_exception_allowed",
                    result="allowed",
                    prompt_length=local_agent_prompt_length,
                    prompt_hash=local_agent_prompt_hash,
                    codex_state_after_paste=codex_state_after_paste,
                    paste_checkpoint_passed=paste_checkpoint_passed,
                )
            else:
                log.append(
                    "local_agent_noop_submit_exception_rejected",
                    command_id=dispatch_result.command.id,
                    noop_unverified_submit_eligible=noop_unverified_submit_eligible,
                    paste_checkpoint_passed=paste_checkpoint_passed,
                    codex_state_after_paste=codex_state_after_paste,
                )
                if paste_checkpoint_passed:
                    log.append(
                        "local_agent_submit_allowed_after_paste_checkpoint",
                        command_id=dispatch_result.command.id,
                        basis="submit_after_verified_paste",
                        prompt_text_present=pre_submit_check.prompt_text_present,
                        codex_state_after_paste=codex_state_after_paste,
                        paste_checkpoint_passed=paste_checkpoint_passed,
                    )
                    _append_debug(
                        action_debug_log,
                        "gui_action",
                        bridge_attempt_id=bridge_attempt_id,
                        phase="local_agent_submit_guard",
                        app=config.targets.local_agent.app_name,
                        profile=config.targets.local_agent.profile,
                        action="local_agent_submit_allowed_after_paste_checkpoint",
                        result="allowed",
                        basis="submit_after_verified_paste",
                        prompt_length=local_agent_prompt_length,
                        prompt_hash=local_agent_prompt_hash,
                        codex_state_after_paste=codex_state_after_paste,
                        paste_checkpoint_passed=paste_checkpoint_passed,
                    )
            if (
                unverified_submit_allowed
                or noop_unverified_submit_eligible
                or visual_send_ready_before_submit
            ):
                log.append(
                    "local_agent_unverified_submit_allowed",
                    command_id=dispatch_result.command.id,
                    allow_unverified_submit=unverified_submit_allowed,
                    noop_dogfood=noop_unverified_submit_eligible,
                    visual_send_ready=visual_send_ready_before_submit,
                    basis=(
                        "visual_send_ready_after_paste"
                        if visual_send_ready_before_submit
                        else (
                            "noop_unverified_submit_after_paste_checkpoint"
                            if noop_submit_exception_allowed
                            else "explicit_unverified_submit_config"
                        )
                    ),
                )
        if pre_submit_check.prompt_text_present is True:
            log.append(
                "local_agent_prompt_present_before_submit",
                command_id=dispatch_result.command.id,
                prompt_length=pre_submit_check.prompt_length,
                input_text_length_after_paste=pre_submit_check.input_text_length_after_paste,
                focused_element_summary=pre_submit_check.focused_element_summary,
            )
        guard.check()
        if noop_unverified_submit_eligible and pre_submit_check.prompt_text_present is not True:
            log.append(
                "local_agent_unverified_submit_attempted",
                command_id=dispatch_result.command.id,
                task_id=NOOP_VALIDATION_TASK_ID,
                full_success_requires_artifact_confirmation=True,
            )
        noop_artifact_initial_hash = (
            _file_sha256(report_path) if noop_unverified_submit_eligible else None
        )
        noop_artifact_min_mtime_ns = (
            time.time_ns() if noop_unverified_submit_eligible else None
        )
        submit_block_reason = _local_agent_submit_block_reason(
            prompt_length=local_agent_prompt_length,
            safety_passed=safety_passed,
            noop_eligible=(
                noop_unverified_submit_eligible or pre_submit_check.prompt_text_present is True
            ),
            focus_completed=local_agent_focus_completed,
            paste_attempted=local_agent_paste_attempted,
            paste_succeeded=local_agent_paste_succeeded,
            clipboard_set_attempted=local_agent_clipboard_set_attempted,
            clipboard_set_succeeded=local_agent_clipboard_set_succeeded,
            clipboard_readback_matches_prompt_hash=(
                local_agent_clipboard_readback_matches_prompt_hash
            ),
            paste_backend_succeeded=local_agent_paste_backend_succeeded,
            prompt_presence_verified=pre_submit_check.prompt_text_present,
            codex_send_ready_after_paste=codex_send_ready_after_paste,
        )
        log.append(
            "local_agent_submit_guard_checked",
            command_id=dispatch_result.command.id,
            prompt_length=local_agent_prompt_length,
            prompt_hash=local_agent_prompt_hash,
            safety_passed=safety_passed,
            noop_unverified_submit_eligible=noop_unverified_submit_eligible,
            noop_submit_exception_used=noop_submit_exception_allowed
            and pre_submit_check.prompt_text_present is not True
            and not visual_send_ready_before_submit,
            visual_send_ready_before_submit=visual_send_ready_before_submit,
            paste_checkpoint_passed=paste_checkpoint_passed,
            focus_completed=local_agent_focus_completed,
            paste_attempted=local_agent_paste_attempted,
            paste_succeeded=local_agent_paste_succeeded,
            clipboard_set_attempted=local_agent_clipboard_set_attempted,
            clipboard_set_succeeded=local_agent_clipboard_set_succeeded,
            clipboard_readback_matches_prompt_hash=(
                local_agent_clipboard_readback_matches_prompt_hash
            ),
            paste_backend_succeeded=local_agent_paste_backend_succeeded,
            codex_state_before_paste=codex_state_before_paste,
            codex_state_after_paste=codex_state_after_paste,
            codex_send_ready_after_paste=codex_send_ready_after_paste,
            block_reason=submit_block_reason,
        )
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="local_agent_submit_guard",
            app=config.targets.local_agent.app_name,
            profile=config.targets.local_agent.profile,
            action="local_agent_submit_guard_checked",
            result="succeeded" if submit_block_reason is None else "failed",
            failure_reason=submit_block_reason,
            prompt_length=local_agent_prompt_length,
            prompt_hash=local_agent_prompt_hash,
            paste_attempted=local_agent_paste_attempted,
            paste_succeeded=local_agent_paste_succeeded,
            clipboard_set_attempted=local_agent_clipboard_set_attempted,
            clipboard_set_succeeded=local_agent_clipboard_set_succeeded,
            clipboard_readback_matches_prompt_hash=(
                local_agent_clipboard_readback_matches_prompt_hash
            ),
            paste_backend_succeeded=local_agent_paste_backend_succeeded,
            codex_state_before_paste=codex_state_before_paste,
            codex_state_after_paste=codex_state_after_paste,
            codex_send_ready_after_paste=codex_send_ready_after_paste,
        )
        if submit_block_reason is not None:
            if submit_block_reason in {
                "local_agent_clipboard_set_not_attempted",
                "local_agent_clipboard_set_failed",
                "local_agent_clipboard_readback_mismatch",
                "local_agent_paste_not_attempted",
                "local_agent_paste_backend_failed",
                "local_agent_click_succeeded_but_paste_missing",
                "local_agent_submit_blocked_missing_paste_confirmation",
            }:
                log.append(
                    "local_agent_submit_blocked_missing_paste",
                    command_id=dispatch_result.command.id,
                    failure_reason=submit_block_reason,
                    codex_state_after_paste=codex_state_after_paste,
                    codex_send_ready_after_paste=codex_send_ready_after_paste,
                    prompt_text_present=pre_submit_check.prompt_text_present,
                )
                _append_debug(
                    action_debug_log,
                    "gui_action",
                    bridge_attempt_id=bridge_attempt_id,
                    phase="local_agent_submit_guard",
                    app=config.targets.local_agent.app_name,
                    profile=config.targets.local_agent.profile,
                    action="local_agent_submit_blocked_missing_paste",
                    result="blocked",
                    failure_reason=submit_block_reason,
                    prompt_length=local_agent_prompt_length,
                    prompt_hash=local_agent_prompt_hash,
                    paste_attempted=local_agent_paste_attempted,
                    paste_succeeded=local_agent_paste_succeeded,
                    clipboard_set_attempted=local_agent_clipboard_set_attempted,
                    clipboard_set_succeeded=local_agent_clipboard_set_succeeded,
                    paste_backend_succeeded=local_agent_paste_backend_succeeded,
                    codex_state_after_paste=codex_state_after_paste,
                    codex_send_ready_after_paste=codex_send_ready_after_paste,
                )
            if submit_block_reason == "local_agent_submit_blocked_missing_paste_confirmation":
                log.append(
                    "local_agent_submit_blocked_missing_paste_confirmation",
                    command_id=dispatch_result.command.id,
                    codex_state_after_paste=codex_state_after_paste,
                    codex_send_ready_after_paste=codex_send_ready_after_paste,
                    prompt_text_present=pre_submit_check.prompt_text_present,
                )
            return ReportRoundtripResult(
                completed=False,
                reason=submit_block_reason,
                cycles_completed=0,
                pm_prompt_path=pm_prompt_path,
                pm_response_path=pm_response_path,
                extracted_prompt_path=extracted_prompt_path,
                local_agent_prompt_path=dispatch_result.prompt_path,
            )
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="local_agent_submit",
            app=config.targets.local_agent.app_name,
            profile=config.targets.local_agent.profile,
            action="submit",
            result="attempted",
            prompt_length=local_agent_prompt_length,
            prompt_hash=local_agent_prompt_hash,
        )
        gui.submit()
        _append_debug(
            action_debug_log,
            "gui_action",
            bridge_attempt_id=bridge_attempt_id,
            phase="local_agent_submit",
            app=config.targets.local_agent.app_name,
            profile=config.targets.local_agent.profile,
            action="submit",
            result="succeeded",
            prompt_length=local_agent_prompt_length,
            prompt_hash=local_agent_prompt_hash,
        )
        log.append(
            "local_agent_submit_attempted",
            command_id=dispatch_result.command.id,
            prompt_length=len(dispatch_result.prompt),
            clipboard_length=pre_submit_check.clipboard_length,
            active_app_before=pre_submit_check.active_app,
            focused_element_summary=pre_submit_check.focused_element_summary,
            prompt_text_present=pre_submit_check.prompt_text_present,
        )
        if config.stop_after_local_agent_submit:
            command_queue.pop_by_id(dispatch_result.command.id)
            log.append(
                "report_roundtrip_stopped_after_local_agent_submit",
                command_id=dispatch_result.command.id,
                reason="stop_after_local_agent_submit",
                wait_for_artifact_confirmation=config.wait_for_artifact_confirmation,
            )
            return ReportRoundtripResult(
                completed=True,
                reason="LOCAL_AGENT_SUBMIT_ATTEMPTED_STOPPED_FOR_QUEUE",
                cycles_completed=1,
                pm_prompt_path=pm_prompt_path,
                pm_response_path=pm_response_path,
                extracted_prompt_path=extracted_prompt_path,
                local_agent_prompt_path=dispatch_result.prompt_path,
            )
        post_submit_check = gui.inspect_local_agent_after_submit(
            config.targets.local_agent,
            dispatch_result.prompt,
            pre_submit_check,
        )
        if post_submit_check.input_cleared:
            log.append(
                "local_agent_input_cleared_after_submit",
                command_id=dispatch_result.command.id,
                focused_text_length_before=post_submit_check.focused_text_length_before,
                focused_text_length_after=post_submit_check.focused_text_length_after,
            )
        if post_submit_check.new_user_message_detected:
            log.append(
                "local_agent_new_user_message_detected",
                command_id=dispatch_result.command.id,
            )
        if post_submit_check.running_state_detected:
            log.append(
                "local_agent_running_state_detected",
                command_id=dispatch_result.command.id,
            )
        if post_submit_check.confirmed is True:
            log.append(
                "local_agent_submit_confirmed",
                command_id=dispatch_result.command.id,
                **asdict(post_submit_check),
            )
            command_queue.pop_by_id(dispatch_result.command.id)
        elif noop_unverified_submit_eligible and config.wait_for_artifact_confirmation:
            confirmed_by_artifact = _wait_for_noop_artifact_confirmation(
                report_path=report_path,
                event_log=log,
                timeout_seconds=min(90, max(1, config.max_runtime_seconds)),
                poll_interval_seconds=2.0,
                monotonic_fn=monotonic_fn,
                sleep_fn=getattr(gui, "sleep_fn", time.sleep),
                initial_report_hash=noop_artifact_initial_hash,
                min_report_mtime_ns=noop_artifact_min_mtime_ns,
            )
            if confirmed_by_artifact:
                log.append(
                    "local_agent_submit_confirmed_by_artifact",
                    command_id=dispatch_result.command.id,
                    task_id=NOOP_VALIDATION_TASK_ID,
                    expected_title=NOOP_VALIDATION_SUCCESS_TITLE,
                    ui_confirmed=False,
                )
                command_queue.pop_by_id(dispatch_result.command.id)
            else:
                log.append(
                    "local_agent_submit_unconfirmed",
                    command_id=dispatch_result.command.id,
                    **asdict(post_submit_check),
                )
                return ReportRoundtripResult(
                    completed=False,
                    reason="LOCAL_AGENT_ARTIFACT_CONFIRMATION_TIMEOUT",
                    cycles_completed=0,
                    pm_prompt_path=pm_prompt_path,
                    pm_response_path=pm_response_path,
                    extracted_prompt_path=extracted_prompt_path,
                    local_agent_prompt_path=dispatch_result.prompt_path,
                )
        elif noop_unverified_submit_eligible:
            log.append(
                "local_agent_artifact_confirmation_skipped",
                command_id=dispatch_result.command.id,
                task_id=NOOP_VALIDATION_TASK_ID,
                reason="wait_for_artifact_confirmation_disabled",
            )
            log.append(
                "local_agent_submit_unconfirmed",
                command_id=dispatch_result.command.id,
                **asdict(post_submit_check),
            )
            return ReportRoundtripResult(
                completed=False,
                reason="LOCAL_AGENT_SUBMIT_UNCONFIRMED_NO_ARTIFACT_WAIT",
                cycles_completed=0,
                pm_prompt_path=pm_prompt_path,
                pm_response_path=pm_response_path,
                extracted_prompt_path=extracted_prompt_path,
                local_agent_prompt_path=dispatch_result.prompt_path,
            )
        else:
            log.append(
                "local_agent_submit_unconfirmed",
                command_id=dispatch_result.command.id,
                **asdict(post_submit_check),
            )
            return ReportRoundtripResult(
                completed=False,
                reason="LOCAL_AGENT_SUBMIT_UNCONFIRMED",
                cycles_completed=0,
                pm_prompt_path=pm_prompt_path,
                pm_response_path=pm_response_path,
                extracted_prompt_path=extracted_prompt_path,
                local_agent_prompt_path=dispatch_result.prompt_path,
            )

        log.append(
            "report_roundtrip_completed",
            cycles_completed=1,
            full_success_basis="artifact_confirmed"
            if noop_unverified_submit_eligible and post_submit_check.confirmed is not True
            else "ui_confirmed",
        )
        return ReportRoundtripResult(
            completed=True,
            reason="ONE_CYCLE_COMPLETE",
            cycles_completed=1,
            pm_prompt_path=pm_prompt_path,
            pm_response_path=pm_response_path,
            extracted_prompt_path=extracted_prompt_path,
            local_agent_prompt_path=dispatch_result.prompt_path,
        )
    except Exception as error:
        log.append("report_roundtrip_failed", error=str(error))
        if isinstance(error, ReportRoundtripError):
            raise
        raise ReportRoundtripError(str(error)) from error
