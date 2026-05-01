from __future__ import annotations

import json
from pathlib import Path

from agent_bridge.gui.roundtrip_verifier import (
    format_roundtrip_verification,
    verify_roundtrip_artifacts,
)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def append_event(workspace: Path, event_type: str, **metadata) -> None:
    log_path = workspace / "logs" / "bridge.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event_type": event_type, "metadata": metadata}) + "\n")


NOOP_VALIDATION_PROMPT = """Task ID: AB-ROUNDTRIP-NOOP-VALIDATION

This is a safe no-op validation prompt for Agent Bridge GUI roundtrip testing.

Do not modify source code.
Avoid code changes.
Do not mutate GitHub.
Do not send Gmail.
Do not push commits.
Do not auto-merge.

Write a short success note only to:

workspace/reports/latest_agent_report.md

The success note title must be exactly:

# Agent Report: GUI Roundtrip No-Op Validation Success
"""


NOOP_VALIDATION_SUCCESS_REPORT = """# Agent Report: GUI Roundtrip No-Op Validation Success

## Summary

Received and executed the safe no-op validation prompt.

## Confirmation

This prompt was received through the Agent Bridge ChatGPT-to-local-agent roundtrip.

## Source Code Changes

No source code changes were made.

## External Mutation

No GitHub, Gmail, or external mutation was performed.

## Push Or Auto-Merge

No push or auto-merge was performed.

## Loop Bound

No long or unbounded loop was run.
"""


def write_success_artifacts(workspace: Path) -> None:
    extracted = "Confirm receipt and write a short success note.\n"
    write(workspace / "reports" / "latest_agent_report.md", "# Report\n\nReady.")
    write(workspace / "outbox" / "pm_assistant_prompt.md", "PM prompt")
    write(workspace / "outbox" / "pm_response.md", f"```CODEX_NEXT_PROMPT\n{extracted}```")
    write(workspace / "outbox" / "extracted_codex_next_prompt.md", extracted)
    write(workspace / "outbox" / "next_local_agent_prompt.md", "Local prompt")
    append_event(workspace, "pm_command_enqueued")
    append_event(workspace, "local_agent_app_activated")
    append_event(workspace, "local_agent_prompt_pasted")
    append_event(workspace, "local_agent_pre_submit_verification", prompt_text_present=True)
    append_event(workspace, "local_agent_prompt_present_before_submit")
    append_event(workspace, "local_agent_submit_attempted")
    append_event(workspace, "local_agent_submit_confirmed", confirmation_reason="input_cleared")
    append_event(workspace, "report_roundtrip_completed")


def test_verify_roundtrip_result_reports_success_when_artifacts_exist(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_success_artifacts(workspace)

    result = verify_roundtrip_artifacts(workspace)

    assert result.success
    assert result.failure_point is None
    assert result.checks["pm_prompt_staged"]
    assert result.checks["local_agent_submit_attempted"]
    assert result.checks["local_agent_prompt_present_before_submit"] == "yes"
    assert result.checks["local_agent_submit_confirmed"] == "yes"
    assert result.checks["local_agent_submit_confirmation_basis"] == "ui_confirmed"
    assert result.checks["full_success_basis"] == "ui_confirmed"
    assert result.checks["local_agent_submit_confirmation_signal"] == "input_cleared"
    assert result.checks["pm_response_has_exactly_one_codex_next_prompt"]
    assert result.checks["extracted_file_matches_response"]


def test_verify_roundtrip_result_reports_missing_response_artifact(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write(workspace / "reports" / "latest_agent_report.md", "# Report\n")
    write(workspace / "outbox" / "pm_assistant_prompt.md", "PM prompt")
    append_event(workspace, "pm_app_activated")
    append_event(workspace, "pm_prompt_pasted")
    append_event(workspace, "pm_prompt_submitted")
    append_event(workspace, "pm_response_wait_started")

    result = verify_roundtrip_artifacts(workspace)

    assert not result.success
    assert result.failure_point == "ChatGPT response copy"


def test_verify_roundtrip_result_reports_extraction_failure(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write(workspace / "reports" / "latest_agent_report.md", "# Report\n")
    write(workspace / "outbox" / "pm_assistant_prompt.md", "PM prompt")
    write(workspace / "outbox" / "pm_response.md", "No CODEX_NEXT_PROMPT block.")

    result = verify_roundtrip_artifacts(workspace)
    output = format_roundtrip_verification(result)

    assert not result.success
    assert result.failure_point == "CODEX_NEXT_PROMPT extraction"
    assert "Extraction check failed" in output


def test_verify_roundtrip_result_reports_safety_gate_block(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write(workspace / "reports" / "latest_agent_report.md", "# Report\n")
    append_event(workspace, "report_roundtrip_safety_blocked", phase="pm_prompt_submit")

    result = verify_roundtrip_artifacts(workspace)

    assert not result.success
    assert result.failure_point == "SafetyGate block"


def test_verify_roundtrip_result_reports_last_failure_event(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write(workspace / "reports" / "latest_agent_report.md", "# Report\n")
    write(workspace / "outbox" / "pm_assistant_prompt.md", "PM prompt")
    append_event(workspace, "report_roundtrip_failed", error="copy failed")

    result = verify_roundtrip_artifacts(workspace)
    output = format_roundtrip_verification(result)

    assert result.last_failure_event is not None
    assert result.last_failure_event["metadata"]["error"] == "copy failed"
    assert "Last failure event" in output


def test_verify_roundtrip_result_maps_send_ready_failure(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write(workspace / "reports" / "latest_agent_report.md", "# Report\n")
    write(workspace / "outbox" / "pm_assistant_prompt.md", "PM prompt")
    append_event(
        workspace,
        "report_roundtrip_failed",
        error="ChatGPT composer did not enter send-ready state after paste.",
    )

    result = verify_roundtrip_artifacts(workspace)

    assert result.failure_point == "ChatGPT send-ready detection"


def test_verify_roundtrip_result_ignores_stale_pm_response_for_latest_failed_run(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write(workspace / "reports" / "latest_agent_report.md", "# Report\n")
    write(workspace / "outbox" / "pm_response.md", "stale response without current run")
    append_event(workspace, "report_roundtrip_started")
    append_event(workspace, "pm_prompt_staged")
    append_event(workspace, "pm_app_activated")
    append_event(workspace, "pm_prompt_pasted")
    append_event(workspace, "report_roundtrip_failed", error="AppleScript syntax error")

    result = verify_roundtrip_artifacts(workspace)

    assert not result.checks["pm_response_captured"]
    assert result.failure_point == "ChatGPT prompt submit"


def test_verify_roundtrip_result_ignores_events_after_latest_roundtrip_terminal_event(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write(workspace / "reports" / "latest_agent_report.md", "# Report\n")
    append_event(workspace, "report_roundtrip_started")
    append_event(workspace, "pm_prompt_staged")
    append_event(workspace, "pm_app_activated")
    append_event(workspace, "pm_prompt_pasted")
    append_event(workspace, "report_roundtrip_failed", error="AppleScript syntax error")
    append_event(workspace, "local_agent_prompt_staged")
    append_event(workspace, "local_agent_prompt_submitted")

    result = verify_roundtrip_artifacts(workspace)

    assert not result.checks["local_agent_prompt_staged"]
    assert not result.checks["local_agent_submit_attempted"]
    assert result.failure_point == "ChatGPT prompt submit"


def test_verify_roundtrip_result_distinguishes_attempted_unconfirmed_submit(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_success_artifacts(workspace)
    log_path = workspace / "logs" / "bridge.jsonl"
    lines = [
        line
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if "local_agent_submit_confirmed" not in line and "report_roundtrip_completed" not in line
    ]
    lines.append(
        json.dumps(
            {
                "event_type": "local_agent_submit_unconfirmed",
                "metadata": {"confirmation_reason": "not_detectable"},
            }
        )
    )
    lines.append(json.dumps({"event_type": "report_roundtrip_completed", "metadata": {}}))
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = verify_roundtrip_artifacts(workspace)
    output = format_roundtrip_verification(result)

    assert not result.success
    assert result.failure_point == "local Codex submit confirmation"
    assert result.checks["local_agent_submit_attempted"]
    assert result.checks["local_agent_submit_confirmed"] == "unknown"
    assert result.checks["local_agent_submit_confirmation_signal"] == "unknown"
    assert result.checks["local_agent_submit_unconfirmed_reason"] == "not_detectable"
    assert "attempted but not confirmed" in output


def test_verify_roundtrip_result_reports_prompt_missing_before_submit(tmp_path: Path):
    workspace = tmp_path / "workspace"
    extracted = "Confirm receipt and write a short success note.\n"
    write(workspace / "reports" / "latest_agent_report.md", "# Report\n\nReady.")
    write(workspace / "outbox" / "pm_assistant_prompt.md", "PM prompt")
    write(workspace / "outbox" / "pm_response.md", f"```CODEX_NEXT_PROMPT\n{extracted}```")
    write(workspace / "outbox" / "extracted_codex_next_prompt.md", extracted)
    write(workspace / "outbox" / "next_local_agent_prompt.md", "Local prompt")
    append_event(workspace, "report_roundtrip_started")
    append_event(workspace, "pm_prompt_staged")
    append_event(workspace, "pm_response_copied")
    append_event(workspace, "codex_next_prompt_extracted")
    append_event(workspace, "pm_command_enqueued")
    append_event(workspace, "local_agent_prompt_staged")
    append_event(workspace, "local_agent_app_activated")
    append_event(workspace, "local_agent_prompt_pasted")
    append_event(
        workspace,
        "local_agent_pre_submit_verification",
        prompt_text_present=False,
        focused_element_summary="AXTextArea",
        input_candidate_count=1,
    )
    append_event(
        workspace,
        "local_agent_prompt_not_present_before_submit",
        prompt_text_present=False,
        focused_element_summary="AXTextArea",
        input_candidate_count=1,
    )

    result = verify_roundtrip_artifacts(workspace)
    output = format_roundtrip_verification(result)

    assert not result.success
    assert result.failure_point == "local Codex prompt presence before submit"
    assert result.checks["local_agent_prompt_present_before_submit"] == "no"
    assert not result.checks["local_agent_submit_attempted"]
    assert "not verified in the Codex input" in output


def test_verify_roundtrip_result_accepts_noop_artifact_confirmation(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write(
        workspace / "reports" / "latest_agent_report.md",
        NOOP_VALIDATION_SUCCESS_REPORT,
    )
    write(workspace / "outbox" / "pm_assistant_prompt.md", "PM prompt")
    write(
        workspace / "outbox" / "pm_response.md",
        f"```CODEX_NEXT_PROMPT\n{NOOP_VALIDATION_PROMPT}```",
    )
    write(workspace / "outbox" / "extracted_codex_next_prompt.md", NOOP_VALIDATION_PROMPT)
    write(workspace / "outbox" / "next_local_agent_prompt.md", NOOP_VALIDATION_PROMPT)
    append_event(workspace, "report_roundtrip_started")
    append_event(workspace, "pm_prompt_staged")
    append_event(workspace, "pm_response_copied")
    append_event(workspace, "codex_next_prompt_extracted")
    append_event(workspace, "pm_command_enqueued")
    append_event(workspace, "local_agent_prompt_staged")
    append_event(workspace, "local_agent_app_activated")
    append_event(workspace, "local_agent_prompt_pasted")
    append_event(
        workspace,
        "local_agent_paste_state_summary",
        codex_send_ready_after_paste=True,
        codex_state_after_paste="COMPOSER_HAS_TEXT",
    )
    append_event(workspace, "local_agent_prompt_not_present_before_submit")
    append_event(
        workspace,
        "local_agent_submit_allowed_after_paste_checkpoint",
        basis="visual_send_ready_after_paste",
        paste_checkpoint_passed=True,
    )
    append_event(
        workspace,
        "local_agent_submit_guard_checked",
        visual_send_ready_before_submit=True,
        paste_checkpoint_passed=True,
    )
    append_event(workspace, "local_agent_unverified_submit_attempted")
    append_event(workspace, "local_agent_submit_attempted")
    append_event(workspace, "local_agent_submit_confirmed_by_artifact")
    append_event(workspace, "report_roundtrip_completed", full_success_basis="artifact_confirmed")

    result = verify_roundtrip_artifacts(workspace)
    output = format_roundtrip_verification(result)

    assert result.success
    assert result.failure_point is None
    assert result.checks["local_agent_submit_confirmed_by_ui"] == "no"
    assert result.checks["local_agent_submit_confirmed_by_artifact"] == "yes"
    assert result.checks["local_agent_submit_confirmation_basis"] == "artifact_confirmed"
    assert result.checks["full_success_basis"] == "artifact_confirmed"
    assert "artifact confirmation succeeded" in output


def test_verify_roundtrip_result_accepts_stop_for_queue_noop_artifact(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    write(workspace / "reports" / "latest_agent_report.md", NOOP_VALIDATION_SUCCESS_REPORT)
    write(workspace / "outbox" / "pm_assistant_prompt.md", "PM prompt")
    write(
        workspace / "outbox" / "pm_response.md",
        f"```CODEX_NEXT_PROMPT\nCODEX_NEXT_PROMPT\n{NOOP_VALIDATION_PROMPT}```",
    )
    write(workspace / "outbox" / "extracted_codex_next_prompt.md", NOOP_VALIDATION_PROMPT)
    write(workspace / "outbox" / "next_local_agent_prompt.md", NOOP_VALIDATION_PROMPT)
    append_event(workspace, "report_roundtrip_started")
    append_event(workspace, "pm_prompt_staged")
    append_event(workspace, "pm_app_activated")
    append_event(workspace, "pm_prompt_pasted")
    append_event(workspace, "pm_prompt_submitted")
    append_event(workspace, "pm_response_copied")
    append_event(workspace, "codex_next_prompt_extracted")
    append_event(workspace, "pm_command_enqueued")
    append_event(workspace, "local_agent_prompt_staged")
    append_event(workspace, "local_agent_app_activated")
    append_event(workspace, "local_agent_prompt_pasted")
    append_event(
        workspace,
        "local_agent_paste_state_summary",
        codex_send_ready_after_paste=True,
        codex_state_after_paste="COMPOSER_HAS_TEXT",
    )
    append_event(workspace, "local_agent_prompt_not_present_before_submit")
    append_event(
        workspace,
        "local_agent_submit_allowed_after_paste_checkpoint",
        basis="visual_send_ready_after_paste",
        paste_checkpoint_passed=True,
    )
    append_event(
        workspace,
        "local_agent_submit_guard_checked",
        visual_send_ready_before_submit=True,
        paste_checkpoint_passed=True,
    )
    append_event(workspace, "local_agent_unverified_submit_attempted")
    append_event(workspace, "local_agent_submit_attempted")
    append_event(
        workspace,
        "report_roundtrip_stopped_after_local_agent_submit",
        wait_for_artifact_confirmation=False,
    )

    result = verify_roundtrip_artifacts(workspace)

    assert result.success
    assert result.failure_point is None
    assert result.checks["one_cycle_completed"]
    assert result.checks["stop_after_local_agent_submit"]
    assert result.checks["local_agent_submit_confirmed_by_artifact"] == "yes"
    assert result.checks["full_success_basis"] == "artifact_confirmed"
    assert result.checks["prompt_presence_before_submit"] == "no"
    assert result.checks["visual_send_ready_before_submit"] == "yes"
    assert result.checks["paste_checkpoint_passed"] == "yes"
    assert result.checks["noop_submit_exception_used"] == "no"


def test_verify_roundtrip_result_rejects_artifact_for_non_noop_prompt(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    write(workspace / "reports" / "latest_agent_report.md", NOOP_VALIDATION_SUCCESS_REPORT)
    write(workspace / "outbox" / "pm_assistant_prompt.md", "PM prompt")
    write(
        workspace / "outbox" / "pm_response.md",
        "```CODEX_NEXT_PROMPT\nTask ID: AB-REAL-WORK\nModify source code.\n```",
    )
    write(workspace / "outbox" / "extracted_codex_next_prompt.md", "Task ID: AB-REAL-WORK")
    write(workspace / "outbox" / "next_local_agent_prompt.md", "Task ID: AB-REAL-WORK")
    append_event(workspace, "report_roundtrip_started")
    append_event(workspace, "pm_prompt_staged")
    append_event(workspace, "pm_response_copied")
    append_event(workspace, "codex_next_prompt_extracted")
    append_event(workspace, "pm_command_enqueued")
    append_event(workspace, "local_agent_prompt_staged")
    append_event(workspace, "local_agent_app_activated")
    append_event(workspace, "local_agent_prompt_pasted")
    append_event(workspace, "local_agent_submit_attempted")
    append_event(workspace, "report_roundtrip_stopped_after_local_agent_submit")

    result = verify_roundtrip_artifacts(workspace)

    assert not result.success
    assert result.checks["local_agent_submit_confirmed_by_artifact"] == "no"
    assert result.checks["noop_artifact_prompt_eligible"] is False


def test_verify_roundtrip_result_rejects_artifact_when_success_title_missing(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    write(
        workspace / "reports" / "latest_agent_report.md",
        NOOP_VALIDATION_SUCCESS_REPORT.replace(
            "# Agent Report: GUI Roundtrip No-Op Validation Success",
            "# Agent Report: Different Title",
        ),
    )
    write(workspace / "outbox" / "pm_assistant_prompt.md", "PM prompt")
    write(
        workspace / "outbox" / "pm_response.md",
        f"```CODEX_NEXT_PROMPT\n{NOOP_VALIDATION_PROMPT}```",
    )
    write(workspace / "outbox" / "extracted_codex_next_prompt.md", NOOP_VALIDATION_PROMPT)
    write(workspace / "outbox" / "next_local_agent_prompt.md", NOOP_VALIDATION_PROMPT)
    append_event(workspace, "report_roundtrip_started")
    append_event(workspace, "pm_prompt_staged")
    append_event(workspace, "pm_response_copied")
    append_event(workspace, "codex_next_prompt_extracted")
    append_event(workspace, "pm_command_enqueued")
    append_event(workspace, "local_agent_prompt_staged")
    append_event(workspace, "local_agent_app_activated")
    append_event(workspace, "local_agent_prompt_pasted")
    append_event(workspace, "local_agent_submit_attempted")
    append_event(workspace, "report_roundtrip_stopped_after_local_agent_submit")

    result = verify_roundtrip_artifacts(workspace)

    assert not result.success
    assert result.checks["local_agent_submit_confirmed_by_artifact"] == "no"
    assert result.checks["noop_artifact_report_valid"] is False


def test_verify_roundtrip_result_rejects_artifact_with_mutation_text(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    write(
        workspace / "reports" / "latest_agent_report.md",
        NOOP_VALIDATION_SUCCESS_REPORT.replace(
            "No source code changes were made.",
            "Source code changes were made.",
        ),
    )
    write(workspace / "outbox" / "pm_assistant_prompt.md", "PM prompt")
    write(
        workspace / "outbox" / "pm_response.md",
        f"```CODEX_NEXT_PROMPT\n{NOOP_VALIDATION_PROMPT}```",
    )
    write(workspace / "outbox" / "extracted_codex_next_prompt.md", NOOP_VALIDATION_PROMPT)
    write(workspace / "outbox" / "next_local_agent_prompt.md", NOOP_VALIDATION_PROMPT)
    append_event(workspace, "report_roundtrip_started")
    append_event(workspace, "pm_prompt_staged")
    append_event(workspace, "pm_response_copied")
    append_event(workspace, "codex_next_prompt_extracted")
    append_event(workspace, "pm_command_enqueued")
    append_event(workspace, "local_agent_prompt_staged")
    append_event(workspace, "local_agent_app_activated")
    append_event(workspace, "local_agent_prompt_pasted")
    append_event(workspace, "local_agent_submit_attempted")
    append_event(workspace, "report_roundtrip_stopped_after_local_agent_submit")

    result = verify_roundtrip_artifacts(workspace)

    assert not result.success
    assert result.checks["local_agent_submit_confirmed_by_artifact"] == "no"
    assert result.checks["noop_artifact_report_valid"] is False


def test_verify_roundtrip_result_reports_codex_idle_timeout_stop(tmp_path: Path):
    workspace = tmp_path / "workspace"
    extracted = "Confirm receipt and write a short success note.\n"
    write(workspace / "reports" / "latest_agent_report.md", "# Report\n\nReady.")
    write(workspace / "outbox" / "pm_assistant_prompt.md", "PM prompt")
    write(workspace / "outbox" / "pm_response.md", f"```CODEX_NEXT_PROMPT\n{extracted}```")
    write(workspace / "outbox" / "extracted_codex_next_prompt.md", extracted)
    write(workspace / "outbox" / "next_local_agent_prompt.md", "Local prompt")
    append_event(workspace, "report_roundtrip_started")
    append_event(workspace, "pm_prompt_staged")
    append_event(workspace, "pm_response_copied")
    append_event(workspace, "codex_next_prompt_extracted")
    append_event(workspace, "pm_command_enqueued")
    append_event(workspace, "local_agent_prompt_staged")
    append_event(workspace, "local_agent_app_activated")
    append_event(workspace, "local_agent_timeout_policy_stop", timeout_seconds=600)
    append_event(
        workspace,
        "report_roundtrip_failed",
        error=(
            "Codex composer did not become idle-empty within 600 seconds "
            "and stop_on_idle_timeout is enabled."
        ),
    )

    result = verify_roundtrip_artifacts(workspace)
    output = format_roundtrip_verification(result)

    assert not result.success
    assert result.failure_point == "local Codex paste"
    assert result.checks["local_agent_submit_blocked_reason"] == "codex_idle_timeout_stop"
    assert "conservative Codex idle-timeout policy" in output


def test_verify_roundtrip_result_confirms_submit_from_input_cleared_event(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_success_artifacts(workspace)

    result = verify_roundtrip_artifacts(workspace)

    assert result.success
    assert result.checks["local_agent_submit_confirmed"] == "yes"


def test_verify_roundtrip_result_handles_old_submit_logs_as_unknown(tmp_path: Path):
    workspace = tmp_path / "workspace"
    extracted = "Confirm receipt and write a short success note.\n"
    write(workspace / "reports" / "latest_agent_report.md", "# Report\n\nReady.")
    write(workspace / "outbox" / "pm_assistant_prompt.md", "PM prompt")
    write(workspace / "outbox" / "pm_response.md", f"```CODEX_NEXT_PROMPT\n{extracted}```")
    write(workspace / "outbox" / "extracted_codex_next_prompt.md", extracted)
    write(workspace / "outbox" / "next_local_agent_prompt.md", "Local prompt")
    append_event(workspace, "pm_command_enqueued")
    append_event(workspace, "local_agent_app_activated")
    append_event(workspace, "local_agent_prompt_pasted")
    append_event(workspace, "local_agent_prompt_submitted")
    append_event(workspace, "report_roundtrip_completed")

    result = verify_roundtrip_artifacts(workspace)

    assert not result.success
    assert result.failure_point == "local Codex submit confirmation"
    assert result.checks["local_agent_submit_attempted"]
    assert result.checks["local_agent_submit_confirmed"] == "unknown"
