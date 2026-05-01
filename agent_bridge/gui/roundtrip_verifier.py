from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from agent_bridge.gui.report_roundtrip import (
    ReportRoundtripError,
    extract_codex_next_prompt,
    is_noop_validation_prompt,
    is_noop_validation_success_report,
)


FAILURE_EVENT_MAP = {
    "report_roundtrip_failed": "reported failure",
    "report_roundtrip_safety_blocked": "SafetyGate block",
    "gui_dogfood_safety_blocked": "SafetyGate block",
}


@dataclass(frozen=True)
class RoundtripVerification:
    success: bool
    failure_point: str | None
    checks: dict[str, bool | str]
    messages: list[str] = field(default_factory=list)
    last_failure_event: dict | None = None


def _read_events(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    events = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def _nonempty(path: Path) -> bool:
    return path.exists() and bool(path.read_text(encoding="utf-8").strip())


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _path_contains_noop_validation_prompt(path: Path) -> bool:
    text = _read_text(path)
    return bool(text and is_noop_validation_prompt(text))


def _failure_point_from_error(error: str | None) -> str | None:
    if not error:
        return None
    normalized = error.lower()
    if "pm backend preflight" in normalized or "dom javascript" in normalized:
        return "PM backend preflight"
    if (
        "codex composer" in normalized
        or "stop_on_idle_timeout" in normalized
        or "plus-button anchor" in normalized
    ):
        return "local Codex paste"
    if "send-ready" in normalized or "send button" in normalized or "composer" in normalized:
        return "ChatGPT send-ready detection"
    if "copy button" in normalized or "copy-ready" in normalized:
        return "ChatGPT response copy-ready detection"
    if "clipboard" in normalized or "response copy" in normalized or "copied pm response" in normalized:
        return "ChatGPT response copy"
    if "codex_next_prompt" in normalized:
        return "CODEX_NEXT_PROMPT extraction"
    if "activate" in normalized and "chatgpt" in normalized:
        return "ChatGPT activation"
    if "activate" in normalized and "codex" in normalized:
        return "local Codex activation"
    if "submit" in normalized and "confirm" in normalized:
        return "local Codex submit confirmation"
    if "max runtime" in normalized:
        return "one-cycle completion"
    return None


def verify_roundtrip_artifacts(workspace_dir: Path) -> RoundtripVerification:
    outbox = workspace_dir / "outbox"
    log_path = workspace_dir / "logs" / "bridge.jsonl"
    report_path = workspace_dir / "reports" / "latest_agent_report.md"
    pm_prompt_path = outbox / "pm_assistant_prompt.md"
    pm_response_path = outbox / "pm_response.md"
    extracted_path = outbox / "extracted_codex_next_prompt.md"
    local_prompt_path = outbox / "next_local_agent_prompt.md"
    events = _read_events(log_path)
    latest_run_start_index = next(
        (
            index
            for index in range(len(events) - 1, -1, -1)
            if events[index].get("event_type") == "report_roundtrip_started"
        ),
        None,
    )
    if latest_run_start_index is not None:
        latest_run_end_index = len(events)
        for index in range(latest_run_start_index + 1, len(events)):
            if events[index].get("event_type") in {
                "report_roundtrip_completed",
                "report_roundtrip_failed",
                "report_roundtrip_safety_blocked",
                "report_roundtrip_stopped_after_local_agent_submit",
            }:
                latest_run_end_index = index + 1
                break
        run_events = events[latest_run_start_index:latest_run_end_index]
    else:
        run_events = events
    event_types = [event.get("event_type") for event in run_events]
    legacy_submit_attempted = "local_agent_prompt_submitted" in event_types
    submit_attempted = "local_agent_submit_attempted" in event_types or legacy_submit_attempted
    pre_submit_event = next(
        (
            event
            for event in run_events
            if event.get("event_type") == "local_agent_pre_submit_verification"
        ),
        None,
    )
    prompt_present_event = next(
        (
            event
            for event in run_events
            if event.get("event_type") == "local_agent_prompt_present_before_submit"
        ),
        None,
    )
    prompt_not_present_event = next(
        (
            event
            for event in run_events
            if event.get("event_type") == "local_agent_prompt_not_present_before_submit"
        ),
        None,
    )
    input_selected_event = next(
        (event for event in run_events if event.get("event_type") == "codex_input_candidate_selected"),
        None,
    )
    input_not_found_event = next(
        (event for event in run_events if event.get("event_type") == "codex_input_candidate_not_found"),
        None,
    )
    fallback_event = next(
        (
            event
            for event in run_events
            if event.get("event_type")
            in {
                "codex_input_fallback_configured",
                "codex_input_fallback_click_attempted",
            }
        ),
        None,
    )
    blocked_unverified_event = next(
        (
            event
            for event in run_events
            if event.get("event_type") == "local_agent_submit_blocked_unverified_prompt_presence"
        ),
        None,
    )
    idle_timeout_stop_event = next(
        (
            event
            for event in run_events
            if event.get("event_type") == "local_agent_timeout_policy_stop"
        ),
        None,
    )
    overwrite_blocked_event = next(
        (
            event
            for event in run_events
            if event.get("event_type") == "local_agent_overwrite_after_timeout_blocked"
        ),
        None,
    )
    pre_submit_metadata = pre_submit_event.get("metadata", {}) if pre_submit_event else {}
    candidate_count = pre_submit_metadata.get("input_candidate_count")
    if input_selected_event is not None or (
        isinstance(candidate_count, int) and candidate_count > 0
    ):
        codex_input_candidate_found = "yes"
    elif input_not_found_event is not None or candidate_count == 0:
        codex_input_candidate_found = "no"
    else:
        codex_input_candidate_found = "unknown"
    if fallback_event is not None:
        codex_focus_strategy = "window_relative_click"
    elif codex_input_candidate_found == "yes":
        codex_focus_strategy = "accessibility"
    else:
        codex_focus_strategy = "unknown"
    if prompt_present_event is not None or codex_input_candidate_found == "yes":
        prompt_presence_verifiable = "yes"
    elif prompt_not_present_event is not None or blocked_unverified_event is not None:
        prompt_presence_verifiable = "no"
    else:
        prompt_presence_verifiable = "unknown"
    if blocked_unverified_event is not None:
        submit_blocked_reason = "unverified_prompt_presence"
    elif idle_timeout_stop_event is not None:
        submit_blocked_reason = "codex_idle_timeout_stop"
    elif overwrite_blocked_event is not None:
        submit_blocked_reason = "codex_overwrite_after_timeout_blocked"
    else:
        submit_blocked_reason = ""
    if prompt_present_event is not None:
        prompt_present_before_submit = "yes"
    elif prompt_not_present_event is not None:
        prompt_present_before_submit = "no"
    elif pre_submit_event is not None:
        value = pre_submit_event.get("metadata", {}).get("prompt_text_present")
        if value is True:
            prompt_present_before_submit = "yes"
        elif value is False:
            prompt_present_before_submit = "no"
        else:
            prompt_present_before_submit = "unknown"
    else:
        prompt_present_before_submit = "unknown"
    paste_summary_event = next(
        (
            event
            for event in reversed(run_events)
            if event.get("event_type") == "local_agent_paste_state_summary"
        ),
        None,
    )
    submit_guard_event = next(
        (
            event
            for event in reversed(run_events)
            if event.get("event_type") == "local_agent_submit_guard_checked"
        ),
        None,
    )
    submit_allowed_after_paste_event = next(
        (
            event
            for event in reversed(run_events)
            if event.get("event_type") == "local_agent_submit_allowed_after_paste_checkpoint"
        ),
        None,
    )
    noop_submit_exception_event = next(
        (
            event
            for event in reversed(run_events)
            if event.get("event_type") == "local_agent_noop_submit_exception_allowed"
        ),
        None,
    )
    visual_send_ready_value = None
    if submit_guard_event is not None:
        visual_send_ready_value = submit_guard_event.get("metadata", {}).get(
            "visual_send_ready_before_submit"
        )
    if visual_send_ready_value is None and paste_summary_event is not None:
        visual_send_ready_value = paste_summary_event.get("metadata", {}).get(
            "codex_send_ready_after_paste"
        )
    if visual_send_ready_value is True:
        visual_send_ready_before_submit = "yes"
    elif visual_send_ready_value is False:
        visual_send_ready_before_submit = "no"
    else:
        visual_send_ready_before_submit = "unknown"
    paste_checkpoint_value = None
    if submit_guard_event is not None:
        paste_checkpoint_value = submit_guard_event.get("metadata", {}).get(
            "paste_checkpoint_passed"
        )
    if paste_checkpoint_value is None and submit_allowed_after_paste_event is not None:
        paste_checkpoint_value = submit_allowed_after_paste_event.get("metadata", {}).get(
            "paste_checkpoint_passed"
        )
    if paste_checkpoint_value is True:
        paste_checkpoint_passed = "yes"
    elif paste_checkpoint_value is False:
        paste_checkpoint_passed = "no"
    else:
        paste_checkpoint_passed = "unknown"
    noop_submit_exception_used = "yes" if noop_submit_exception_event is not None else "no"
    submit_confirmed_event = next(
        (event for event in run_events if event.get("event_type") == "local_agent_submit_confirmed"),
        None,
    )
    artifact_confirmed_event = next(
        (
            event
            for event in run_events
            if event.get("event_type") == "local_agent_submit_confirmed_by_artifact"
        ),
        None,
    )
    artifact_failed_event = next(
        (
            event
            for event in run_events
            if event.get("event_type") == "local_agent_artifact_confirmation_failed"
        ),
        None,
    )
    stop_after_submit_event = next(
        (
            event
            for event in run_events
            if event.get("event_type") == "report_roundtrip_stopped_after_local_agent_submit"
        ),
        None,
    )
    submit_unconfirmed_event = next(
        (event for event in run_events if event.get("event_type") == "local_agent_submit_unconfirmed"),
        None,
    )
    noop_artifact_report_valid = is_noop_validation_success_report(_read_text(report_path))
    noop_artifact_prompt_eligible = _path_contains_noop_validation_prompt(
        extracted_path
    ) and _path_contains_noop_validation_prompt(local_prompt_path)
    artifact_confirmation_valid = (
        submit_attempted
        and noop_artifact_report_valid
        and noop_artifact_prompt_eligible
        and (artifact_confirmed_event is not None or stop_after_submit_event is not None)
    )
    if submit_confirmed_event is not None:
        submit_confirmed: str = "yes"
        confirmation_signal = str(
            submit_confirmed_event.get("metadata", {}).get("confirmation_reason") or "unknown"
        )
        unconfirmed_reason = ""
        confirmation_basis = "ui_confirmed"
    elif artifact_confirmation_valid:
        submit_confirmed = "unknown"
        confirmation_signal = "artifact_confirmed"
        unconfirmed_reason = ""
        confirmation_basis = "artifact_confirmed"
    elif submit_unconfirmed_event is not None or legacy_submit_attempted or submit_attempted:
        submit_confirmed = "unknown"
        confirmation_signal = "unknown"
        unconfirmed_reason = str(
            (submit_unconfirmed_event or {}).get("metadata", {}).get("confirmation_reason")
            or "not_detectable"
        )
        confirmation_basis = "attempted_only"
    else:
        submit_confirmed = "no"
        confirmation_signal = "unknown"
        unconfirmed_reason = "not_attempted"
        confirmation_basis = "none"
    last_failure_event = next(
        (
            event
            for event in reversed(run_events)
            if event.get("event_type") in FAILURE_EVENT_MAP
        ),
        None,
    )
    use_run_events_for_artifacts = latest_run_start_index is not None
    last_failure_error = None
    if last_failure_event:
        last_failure_error = str(last_failure_event.get("metadata", {}).get("error") or "")
    checks = {
        "latest_report_exists": _nonempty(report_path),
        "pm_prompt_staged": "pm_prompt_staged" in event_types
        or (not use_run_events_for_artifacts and _nonempty(pm_prompt_path)),
        "pm_response_captured": "pm_response_copied" in event_types
        or (not use_run_events_for_artifacts and _nonempty(pm_response_path)),
        "codex_next_prompt_extracted": "codex_next_prompt_extracted" in event_types
        or (not use_run_events_for_artifacts and _nonempty(extracted_path)),
        "local_agent_prompt_staged": "local_agent_prompt_staged" in event_types
        or (not use_run_events_for_artifacts and _nonempty(local_prompt_path)),
        "codex_input_candidate_found": codex_input_candidate_found,
        "codex_input_focus_strategy": codex_focus_strategy,
        "local_agent_prompt_presence_verifiable": prompt_presence_verifiable,
        "local_agent_prompt_present_before_submit": prompt_present_before_submit,
        "prompt_presence_before_submit": prompt_present_before_submit,
        "visual_send_ready_before_submit": visual_send_ready_before_submit,
        "noop_submit_exception_used": noop_submit_exception_used,
        "paste_checkpoint_passed": paste_checkpoint_passed,
        "local_agent_submit_attempted": submit_attempted,
        "local_agent_submit_confirmed": submit_confirmed,
        "local_agent_submit_confirmed_by_ui": "yes" if submit_confirmed_event else "no",
        "local_agent_submit_confirmed_by_artifact": "yes"
        if artifact_confirmation_valid
        else "no",
        "local_agent_submit_confirmation_signal": confirmation_signal,
        "local_agent_submit_confirmation_basis": confirmation_basis,
        "local_agent_submit_unconfirmed_reason": unconfirmed_reason,
        "local_agent_submit_blocked_reason": submit_blocked_reason,
        "noop_artifact_report_valid": noop_artifact_report_valid,
        "noop_artifact_prompt_eligible": noop_artifact_prompt_eligible,
        "stop_after_local_agent_submit": stop_after_submit_event is not None,
        "safety_blocked": any("safety_blocked" in str(event_type) for event_type in event_types),
        "one_cycle_completed": "report_roundtrip_completed" in event_types
        or (stop_after_submit_event is not None and artifact_confirmation_valid),
        "full_success_basis": confirmation_basis
        if confirmation_basis in {"ui_confirmed", "artifact_confirmed"}
        else "none",
    }
    messages: list[str] = []

    extraction_valid = False
    if pm_response_path.exists() and checks["pm_response_captured"]:
        try:
            extracted = extract_codex_next_prompt(pm_response_path.read_text(encoding="utf-8"))
            extraction_valid = True
            if extracted_path.exists():
                checks["extracted_file_matches_response"] = (
                    extracted_path.read_text(encoding="utf-8") == extracted
                )
        except ReportRoundtripError as error:
            messages.append(f"Extraction check failed: {error}")
    checks["pm_response_has_exactly_one_codex_next_prompt"] = extraction_valid

    failure_point = _failure_point_from_error(last_failure_error)
    if checks["safety_blocked"]:
        failure_point = "SafetyGate block"
    elif failure_point:
        pass
    elif not checks["pm_prompt_staged"]:
        failure_point = "external terminal trigger or PM prompt staging"
    elif "pm_app_activated" not in event_types and not checks["pm_response_captured"]:
        failure_point = "ChatGPT activation"
    elif "pm_prompt_pasted" not in event_types and not checks["pm_response_captured"]:
        failure_point = "ChatGPT prompt paste"
    elif "pm_prompt_submitted" not in event_types and not checks["pm_response_captured"]:
        failure_point = "ChatGPT prompt submit"
    elif not checks["pm_response_captured"]:
        failure_point = "ChatGPT response copy"
    elif not extraction_valid:
        failure_point = "CODEX_NEXT_PROMPT extraction"
    elif not checks["codex_next_prompt_extracted"]:
        failure_point = "CODEX_NEXT_PROMPT extraction"
    elif "pm_command_enqueued" not in event_types:
        failure_point = "queue enqueue"
    elif not checks["local_agent_prompt_staged"]:
        failure_point = "local-agent prompt staging"
    elif "local_agent_app_activated" not in event_types:
        failure_point = "local Codex activation"
    elif "local_agent_prompt_pasted" not in event_types:
        failure_point = "local Codex paste"
    elif (
        checks["local_agent_prompt_present_before_submit"] == "no"
        and checks["local_agent_submit_confirmed_by_artifact"] != "yes"
    ):
        failure_point = "local Codex prompt presence before submit"
    elif not checks["local_agent_submit_attempted"]:
        failure_point = "local Codex submit"
    elif (
        checks["local_agent_submit_confirmed"] != "yes"
        and checks["local_agent_submit_confirmed_by_artifact"] != "yes"
    ):
        failure_point = "local Codex submit confirmation"
    elif not checks["one_cycle_completed"]:
        failure_point = "one-cycle completion"

    if last_failure_event:
        messages.append(
            f"Last failure event: {last_failure_event.get('event_type')} "
            f"{last_failure_event.get('metadata', {})}"
        )
    if submit_attempted and submit_confirmed != "yes":
        if artifact_confirmation_valid:
            messages.append(
                "Local-agent submit was not confirmed by UI evidence, but the no-op "
                "artifact confirmation succeeded."
            )
        else:
            messages.append("Local-agent submit was attempted but not confirmed by UI evidence.")
        if submit_unconfirmed_event:
            messages.append(
                "Submit confirmation diagnostics: "
                f"{submit_unconfirmed_event.get('metadata', {})}"
            )
    if (artifact_confirmed_event or stop_after_submit_event) and not artifact_confirmation_valid:
        messages.append(
            "No-op artifact confirmation was not accepted because the latest report "
            "or staged prompt did not satisfy the no-op validation policy."
        )
    if artifact_failed_event:
        messages.append(
            "No-op artifact confirmation failed: "
            f"{artifact_failed_event.get('metadata', {})}"
        )
    if prompt_not_present_event:
        messages.append(
            "Local-agent prompt was not verified in the Codex input before submit: "
            f"{prompt_not_present_event.get('metadata', {})}"
        )
    if blocked_unverified_event:
        messages.append(
            "Local-agent submit was blocked because prompt presence was unverifiable: "
            f"{blocked_unverified_event.get('metadata', {})}"
        )
    if idle_timeout_stop_event:
        messages.append(
            "Local-agent paste was blocked by conservative Codex idle-timeout policy: "
            f"{idle_timeout_stop_event.get('metadata', {})}"
        )
    if overwrite_blocked_event:
        messages.append(
            "Local-agent controlled overwrite after idle timeout was blocked: "
            f"{overwrite_blocked_event.get('metadata', {})}"
        )

    success = (
        failure_point is None
        and checks["one_cycle_completed"]
        and checks["local_agent_submit_attempted"]
        and (
            checks["local_agent_submit_confirmed"] == "yes"
            or checks["local_agent_submit_confirmed_by_artifact"] == "yes"
        )
        and extraction_valid
    )
    return RoundtripVerification(
        success=success,
        failure_point=failure_point,
        checks=checks,
        messages=messages,
        last_failure_event=last_failure_event,
    )


def format_roundtrip_verification(result: RoundtripVerification) -> str:
    lines = [
        "# Roundtrip Verification",
        "",
        f"Success: {'yes' if result.success else 'no'}",
        f"Failure point: {result.failure_point or 'none'}",
        "",
        "## Checks",
    ]
    for name, value in result.checks.items():
        if isinstance(value, str):
            display = value
        else:
            display = "yes" if value else "no"
        lines.append(f"- {name}: {display}")
    if result.messages:
        lines.extend(["", "## Messages"])
        lines.extend(f"- {message}" for message in result.messages)
    return "\n".join(lines)
