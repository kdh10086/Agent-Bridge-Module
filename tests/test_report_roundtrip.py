from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import agent_bridge.cli as cli_module
from agent_bridge.core.command_queue import CommandQueue, CommandQueueEnqueueResult
from agent_bridge.core.event_log import EventLog
from agent_bridge.core.models import BridgeStateName, Command, CommandType
from agent_bridge.core.state_store import StateStore
from agent_bridge.gui.gui_automation import (
    GuiAutomationAdapter,
    LocalAgentPostSubmitCheck,
    LocalAgentPreSubmitCheck,
)
from agent_bridge.gui.macos_apps import ManualStageTarget, default_gui_targets
from agent_bridge.gui.report_roundtrip import (
    NOOP_VALIDATION_SUCCESS_TITLE,
    NOOP_VALIDATION_TASK_ID,
    ReportRoundtripConfig,
    ReportRoundtripError,
    build_pm_prompt_sentinel,
    build_report_roundtrip_pm_prompt,
    extract_codex_next_prompt,
    is_noop_validation_prompt,
    run_report_roundtrip,
    _codex_activation_phase_order_block_reason,
    _local_agent_submit_block_reason,
    _wait_for_noop_artifact_confirmation,
)
from agent_bridge.gui.pm_backend import (
    PMAssistantBackend,
    PMBackendCheck,
    PMBackendPreflightResult,
)


class FakeGui(GuiAutomationAdapter):
    def __init__(self, response: str):
        self.response = response
        self.actions: list[str] = []
        self.clipboard: list[str] = []
        self.local_agent_queue_handoff_modes: list[bool] = []

    def activate_app(self, target: ManualStageTarget) -> None:
        self.actions.append(f"activate:{target.app_name}")

    def copy_text_to_clipboard(self, text: str) -> None:
        self.actions.append("copy_text")
        self.clipboard.append(text)

    def read_clipboard_text(self) -> str | None:
        return self.clipboard[-1] if self.clipboard else None

    def paste_clipboard(self) -> None:
        self.actions.append("paste")

    def submit(self) -> None:
        self.actions.append("submit")

    def wait_for_response(self, timeout_seconds: int) -> None:
        self.actions.append(f"wait:{timeout_seconds}")

    def copy_response_text(self) -> str:
        self.actions.append("copy_response")
        return self.response

    def set_local_agent_queue_handoff_mode(self, enabled: bool) -> None:
        self.actions.append(f"queue_handoff_mode:{enabled}")
        self.local_agent_queue_handoff_modes.append(enabled)

    def inspect_local_agent_before_submit(
        self,
        target: ManualStageTarget,
        prompt: str,
    ) -> LocalAgentPreSubmitCheck:
        self.actions.append("pre_submit_check")
        return LocalAgentPreSubmitCheck(
            active_app=target.app_name,
            target_app=target.app_name,
            prompt_length=len(prompt),
            clipboard_length=len(self.clipboard[-1]) if self.clipboard else 0,
            focused_element_summary="AXTextArea",
            focused_text_length=len(prompt),
            prompt_text_present=True,
        )

    def inspect_local_agent_after_submit(
        self,
        target: ManualStageTarget,
        prompt: str,
        before: LocalAgentPreSubmitCheck,
    ) -> LocalAgentPostSubmitCheck:
        self.actions.append("post_submit_check")
        return LocalAgentPostSubmitCheck(
            active_app_before=before.active_app,
            active_app_after=target.app_name,
            focused_element_summary_after="AXTextArea",
            focused_text_length_before=before.focused_text_length,
            focused_text_length_after=0,
            input_cleared=True,
            confirmed=True,
            confirmation_reason="input_cleared",
        )


class UnconfirmedSubmitGui(FakeGui):
    def inspect_local_agent_after_submit(
        self,
        target: ManualStageTarget,
        prompt: str,
        before: LocalAgentPreSubmitCheck,
    ) -> LocalAgentPostSubmitCheck:
        self.actions.append("post_submit_check")
        return LocalAgentPostSubmitCheck(
            active_app_before=before.active_app,
            active_app_after=target.app_name,
            focused_element_summary_after="unknown",
            focused_text_length_before=before.focused_text_length,
            focused_text_length_after=None,
            input_cleared=None,
            confirmed=None,
            confirmation_reason="not_detectable",
        )


class PromptMissingBeforeSubmitGui(FakeGui):
    def inspect_local_agent_before_submit(
        self,
        target: ManualStageTarget,
        prompt: str,
    ) -> LocalAgentPreSubmitCheck:
        self.actions.append("pre_submit_check")
        return LocalAgentPreSubmitCheck(
            active_app=target.app_name,
            target_app=target.app_name,
            app_frontmost=True,
            prompt_length=len(prompt),
            clipboard_length=len(self.clipboard[-1]) if self.clipboard else 0,
            focused_element_summary="AXTextArea",
            focused_text_length=0,
            input_candidate_count=1,
            selected_input_candidate_summary="AXTextArea: Prompt input",
            input_text_length_before_paste=0,
            input_text_length_after_paste=0,
            prompt_text_present=False,
        )


class VisualSendReadyPromptMissingGui(PromptMissingBeforeSubmitGui):
    def __init__(self, response: str):
        super().__init__(response)
        self.paste_count = 0

    def paste_clipboard(self) -> bool:
        self.paste_count += 1
        self.actions.append("paste")
        if self.paste_count == 2:
            self.last_local_agent_paste_attempted = True
            self.last_local_agent_paste_backend_success = True
            self.last_local_agent_paste_send_ready = True
            self.last_local_agent_paste_state_before = "IDLE"
            self.last_local_agent_paste_state_after = "COMPOSER_HAS_TEXT"
            self.last_local_agent_paste_state_after_confidence = 0.99
            self.last_local_agent_paste_state_after_asset = (
                "assets/gui/codex/codex_send_button_light.png"
            )
            self.last_local_agent_clipboard_readback_matches_prompt_hash = True
        return True


class NoopPasteCheckpointPromptMissingGui(PromptMissingBeforeSubmitGui):
    def __init__(self, response: str):
        super().__init__(response)
        self.paste_count = 0

    def paste_clipboard(self) -> bool:
        self.paste_count += 1
        self.actions.append("paste")
        if self.paste_count == 2:
            self.last_local_agent_paste_attempted = True
            self.last_local_agent_paste_backend_success = True
            self.last_local_agent_paste_send_ready = None
            self.last_local_agent_paste_state_before = "IDLE"
            self.last_local_agent_paste_state_after = "UNKNOWN"
            self.last_local_agent_clipboard_readback_matches_prompt_hash = True
        return True


class LocalPasteFailureGui(FakeGui):
    def __init__(self, response: str):
        super().__init__(response)
        self.paste_count = 0

    def paste_clipboard(self) -> None:
        self.paste_count += 1
        self.actions.append("paste")
        if self.paste_count == 2:
            raise RuntimeError(
                "Codex composer did not become idle-empty within 600 seconds and stop_on_idle_timeout is enabled."
            )


class LocalPasteMissingGui(FakeGui):
    def __init__(self, response: str):
        super().__init__(response)
        self.paste_count = 0

    def paste_clipboard(self) -> bool | None:
        self.paste_count += 1
        self.actions.append("paste")
        if self.paste_count == 2:
            return False
        return None


class LocalClipboardSetFailureGui(FakeGui):
    def copy_text_to_clipboard(self, text: str) -> None:
        self.actions.append("copy_text")
        if text.startswith("Type=USER_MANUAL_COMMAND"):
            raise RuntimeError("clipboard unavailable")
        self.clipboard.append(text)


class LocalClipboardReadbackMismatchGui(FakeGui):
    def read_clipboard_text(self) -> str | None:
        if self.clipboard and self.clipboard[-1].startswith("Type=USER_MANUAL_COMMAND"):
            return "wrong local-agent prompt"
        return super().read_clipboard_text()


class LocalPasteBackendFailureGui(FakeGui):
    def __init__(self, response: str):
        super().__init__(response)
        self.paste_count = 0

    def paste_clipboard(self) -> None:
        self.paste_count += 1
        self.actions.append("paste")
        if self.paste_count == 2:
            raise RuntimeError("local_agent_paste_backend_failed")


class LocalPasteNotReflectedGui(FakeGui):
    def __init__(self, response: str):
        super().__init__(response)
        self.paste_count = 0

    def paste_clipboard(self) -> None:
        self.paste_count += 1
        self.actions.append("paste")
        if self.paste_count == 2:
            raise RuntimeError("local_agent_paste_not_reflected_in_codex_state")


class CopyRequiresFreshPmActivationGui(FakeGui):
    def wait_for_response(self, timeout_seconds: int) -> None:
        super().wait_for_response(timeout_seconds)
        self.actions.append("simulate:codex_frontmost")

    def copy_response_text(self) -> str:
        if not self.actions or self.actions[-1] != "activate:ChatGPT":
            raise RuntimeError("PM target was not reactivated immediately before response copy.")
        return super().copy_response_text()


class NoopArtifactConfirmedGui(PromptMissingBeforeSubmitGui):
    def __init__(self, response: str, report_path: Path):
        super().__init__(response)
        self.report_path = report_path

    def submit(self) -> None:
        self.actions.append("submit")
        self.report_path.write_text(
            f"{NOOP_VALIDATION_SUCCESS_TITLE}\n\n"
            "## Summary\n\n"
            "No-op validation completed.\n\n"
            "## Source Code Changes\n\n"
            "No source code changes were made.\n\n"
            "## External Mutation\n\n"
            "No GitHub, Gmail, or external mutation was performed.\n\n"
            "## Push Or Auto-Merge\n\n"
            "No push or auto-merge was performed.\n\n"
            "## Loop Bound\n\n"
            "No long or unbounded loop was run.\n",
            encoding="utf-8",
        )

    def inspect_local_agent_after_submit(
        self,
        target: ManualStageTarget,
        prompt: str,
        before: LocalAgentPreSubmitCheck,
    ) -> LocalAgentPostSubmitCheck:
        self.actions.append("post_submit_check")
        return LocalAgentPostSubmitCheck(
            active_app_before=before.active_app,
            active_app_after=target.app_name,
            focused_element_summary_after="unknown",
            focused_text_length_before=before.focused_text_length,
            focused_text_length_after=None,
            input_cleared=None,
            confirmed=None,
            confirmation_reason="not_detectable",
        )


class NewMessageConfirmedGui(FakeGui):
    def inspect_local_agent_after_submit(
        self,
        target: ManualStageTarget,
        prompt: str,
        before: LocalAgentPreSubmitCheck,
    ) -> LocalAgentPostSubmitCheck:
        self.actions.append("post_submit_check")
        return LocalAgentPostSubmitCheck(
            active_app_before=before.active_app,
            active_app_after=target.app_name,
            focused_element_summary_after="conversation",
            focused_text_length_before=before.focused_text_length,
            focused_text_length_after=before.focused_text_length,
            input_cleared=False,
            new_user_message_detected=True,
            running_state_detected=None,
            confirmed=True,
            confirmation_reason="new_user_message_detected",
        )


def write_templates(template_dir: Path) -> None:
    template_dir.mkdir(parents=True)
    (template_dir / "codex_command_wrapper.md").write_text(
        "Type={command_type}\nSource={source}\nPayload={payload}",
        encoding="utf-8",
    )


def write_report(workspace: Path, text: str) -> None:
    reports = workspace / "reports"
    reports.mkdir(parents=True)
    (reports / "latest_agent_report.md").write_text(text, encoding="utf-8")


def make_config(
    workspace: Path,
    template_dir: Path,
    *,
    auto_confirm: bool = True,
    max_cycles: int = 1,
    require_pm_backend_preflight: bool = False,
) -> ReportRoundtripConfig:
    return ReportRoundtripConfig(
        workspace_dir=workspace,
        template_dir=template_dir,
        targets=default_gui_targets(),
        auto_confirm=auto_confirm,
        max_cycles=max_cycles,
        max_runtime_seconds=180,
        pm_response_timeout_seconds=9,
        require_pm_backend_preflight=require_pm_backend_preflight,
    )


def valid_response(prompt: str = "Implement the next Agent Bridge task.") -> str:
    return f"```CODEX_NEXT_PROMPT\n{prompt}\n```"


def valid_body_labeled_response(prompt: str = "Implement the next Agent Bridge task.") -> str:
    return f"```CODEX_NEXT_PROMPT\nCODEX_NEXT_PROMPT\n{prompt}\n```"


def valid_noop_response() -> str:
    return valid_body_labeled_response(
        "\n".join(
            [
                f"Task ID: {NOOP_VALIDATION_TASK_ID}",
                "",
                "This is a no-op validation prompt.",
                "Do not modify source code.",
                "Avoid code changes.",
                "Do not mutate GitHub.",
                "Do not send Gmail.",
                "Do not push commits.",
                "Do not auto-merge.",
                "Only write a short success note to workspace/reports/latest_agent_report.md.",
                f"The success note title must be: {NOOP_VALIDATION_SUCCESS_TITLE}",
            ]
        )
    )


def configure_cli(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    config_dir = tmp_path / "config"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nReady.")
    config_dir.mkdir(parents=True)
    (config_dir / "default.yaml").write_text("apps: {}\n", encoding="utf-8")
    monkeypatch.setattr(cli_module, "ROOT", tmp_path)
    monkeypatch.setattr(cli_module, "WORKSPACE", workspace)
    monkeypatch.setattr(cli_module, "QUEUE_DIR", workspace / "queue")
    monkeypatch.setattr(cli_module, "STATE_PATH", workspace / "state" / "state.json")
    monkeypatch.setattr(cli_module, "LOG_PATH", workspace / "logs" / "bridge.jsonl")
    monkeypatch.setattr(cli_module, "TEMPLATE_DIR", template_dir)
    monkeypatch.setattr(cli_module, "CONFIG_DIR", config_dir)


def test_pm_prompt_includes_full_report_and_requests_one_codex_block():
    report = "# Agent Report\n\nFull report body.\n\n## Details\n\nEverything relevant."

    prompt = build_report_roundtrip_pm_prompt(report)

    assert report in prompt
    assert "exactly one fenced Markdown code block" in prompt
    assert "CODEX_NEXT_PROMPT" in prompt
    assert "first non-empty line inside the block must be exactly CODEX_NEXT_PROMPT" in prompt
    assert "rendered code block body" in prompt
    assert "Do not include prose before or after the block" in prompt


def test_pm_prompt_includes_sentinel_when_bridge_attempt_id_is_available():
    prompt = build_report_roundtrip_pm_prompt(
        "# Agent Report\n\nReady.",
        bridge_attempt_id="bridge_test_123",
    )

    assert build_pm_prompt_sentinel("bridge_test_123") in prompt
    assert prompt.index(build_pm_prompt_sentinel("bridge_test_123")) < prompt.index(
        "Latest agent report:"
    )


def test_pm_prompt_keeps_roundtrip_test_noop_requirement():
    prompt = build_report_roundtrip_pm_prompt("# Report\n\nAGENT_BRIDGE_GUI_ROUNDTRIP_TEST")

    assert "safe no-op validation prompt" in prompt
    assert NOOP_VALIDATION_TASK_ID in prompt
    assert NOOP_VALIDATION_SUCCESS_TITLE in prompt
    assert "avoid code changes" in prompt


def test_noop_validation_prompt_eligibility_requires_safe_constraints():
    safe_prompt = extract_codex_next_prompt(valid_noop_response())

    assert is_noop_validation_prompt(safe_prompt)
    assert not is_noop_validation_prompt("Task ID: AB-ROUNDTRIP-NOOP-VALIDATION\nRun tests.")


def test_extract_codex_next_prompt_exact_info_string():
    extracted = extract_codex_next_prompt(
        "```CODEX_NEXT_PROMPT\nRun the next safe Agent Bridge task.\n```"
    )

    assert extracted == "Run the next safe Agent Bridge task.\n"


def test_extract_codex_next_prompt_with_metadata_info_string():
    extracted = extract_codex_next_prompt(
        '```CODEX_NEXT_PROMPT id="abc"\nRun the next safe Agent Bridge task.\n```'
    )

    assert extracted == "Run the next safe Agent Bridge task.\n"


def test_extract_codex_next_prompt_strips_body_label_inside_fenced_block():
    extracted = extract_codex_next_prompt(
        "```CODEX_NEXT_PROMPT\nCODEX_NEXT_PROMPT\nRun the next safe Agent Bridge task.\n```"
    )

    assert extracted == "Run the next safe Agent Bridge task.\n"


def test_extract_codex_next_prompt_accepts_generic_fence_with_body_label():
    extracted = extract_codex_next_prompt(
        "```text\nCODEX_NEXT_PROMPT\nRun the next safe Agent Bridge task.\n```"
    )

    assert extracted == "Run the next safe Agent Bridge task.\n"


def test_extract_codex_next_prompt_accepts_chatgpt_mac_body_only_copy():
    extracted = extract_codex_next_prompt(
        "CODEX_NEXT_PROMPT\nRun the next safe Agent Bridge task.\n"
    )

    assert extracted == "Run the next safe Agent Bridge task.\n"


def test_extract_codex_next_prompt_rejects_multiple_body_labels():
    with pytest.raises(ReportRoundtripError, match="Expected exactly one CODEX_NEXT_PROMPT"):
        extract_codex_next_prompt("CODEX_NEXT_PROMPT\nFirst.\nCODEX_NEXT_PROMPT\nSecond.\n")


def test_extract_codex_next_prompt_rejects_body_label_after_prose():
    with pytest.raises(ReportRoundtripError, match="Expected exactly one CODEX_NEXT_PROMPT"):
        extract_codex_next_prompt("Prose before marker.\nCODEX_NEXT_PROMPT\nRun task.\n")


def test_extract_codex_next_prompt_fails_on_missing_block():
    with pytest.raises(ReportRoundtripError, match="Expected exactly one CODEX_NEXT_PROMPT"):
        extract_codex_next_prompt("```text\nNo command here.\n```")


def test_extract_codex_next_prompt_fails_on_multiple_blocks():
    response = (
        "```CODEX_NEXT_PROMPT\nFirst.\n```\n"
        "```CODEX_NEXT_PROMPT id=\"second\"\nSecond.\n```"
    )

    with pytest.raises(ReportRoundtripError, match="Expected exactly one CODEX_NEXT_PROMPT"):
        extract_codex_next_prompt(response)


def test_extract_codex_next_prompt_preserves_content_exactly():
    content = "Line 1\n\n  Indented line\nTrailing spaces   \n"
    response = f'````CODEX_NEXT_PROMPT id="preserve"\n{content}````'

    assert extract_codex_next_prompt(response) == content


def test_missing_codex_next_prompt_block_fails_safely(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nReady.")
    gui = FakeGui("No fenced block here.")

    with pytest.raises(ReportRoundtripError, match="CODEX_NEXT_PROMPT"):
        run_report_roundtrip(config=make_config(workspace, template_dir), gui=gui)

    assert CommandQueue(workspace / "queue").list_pending() == []
    assert not (workspace / "outbox" / "extracted_codex_next_prompt.md").exists()
    assert "submit" in gui.actions
    assert "activate:Codex" not in gui.actions


def test_risky_extracted_prompt_is_blocked_before_codex_submit(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nReady.")
    gui = FakeGui(valid_response("RISK_HIGH needs owner review."))

    result = run_report_roundtrip(config=make_config(workspace, template_dir), gui=gui)

    state = StateStore(workspace / "state" / "state.json").load()
    assert not result.completed
    assert result.safety_paused
    assert state.safety_pause
    assert state.state == BridgeStateName.PAUSED_FOR_USER_DECISION
    assert "activate:Codex" not in gui.actions
    assert (workspace / "inbox" / "user_decision_request.md").exists()


def test_extracted_prompt_is_enqueued_and_staged_for_local_agent(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nReady.")
    gui = FakeGui(valid_response("Implement the next safe Agent Bridge task."))

    result = run_report_roundtrip(config=make_config(workspace, template_dir), gui=gui)

    queue = CommandQueue(workspace / "queue")
    in_progress = queue.get_in_progress()
    assert result.completed
    assert in_progress is not None
    assert in_progress.type == CommandType.USER_MANUAL_COMMAND
    assert in_progress.source == "pm_assistant_report_roundtrip"
    assert Path(in_progress.payload_path).read_text(encoding="utf-8") == (
        "Implement the next safe Agent Bridge task.\n"
    )
    assert (workspace / "outbox" / "next_local_agent_prompt.md").exists()


def test_report_roundtrip_stages_newly_enqueued_command_not_stale_higher_priority(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    stale_payload = workspace / "inbox" / "stale.md"
    stale_payload.parent.mkdir(parents=True)
    stale_payload.write_text("Stale high-priority command.", encoding="utf-8")
    queue = CommandQueue(workspace / "queue")
    queue.enqueue(
        Command(
            id="cmd_stale_high_priority",
            type=CommandType.GITHUB_REVIEW_FIX,
            priority=90,
            source="stale_test",
            payload_path=str(stale_payload),
            dedupe_key="stale_high_priority",
        )
    )
    write_report(workspace, "# Report\n\nReady.")
    gui = FakeGui(valid_response("Implement the report roundtrip command."))

    result = run_report_roundtrip(
        config=make_config(workspace, template_dir),
        gui=gui,
        queue=queue,
    )

    staged_prompt = (workspace / "outbox" / "next_local_agent_prompt.md").read_text(
        encoding="utf-8"
    )
    assert result.completed
    assert "Implement the report roundtrip command." in staged_prompt
    assert "Stale high-priority command." not in staged_prompt


def test_report_roundtrip_dispatches_existing_pending_dedupe_by_id(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nReady.")
    bridge_attempt_id = "bridge_existing_dedupe"
    response = valid_response("Implement the deduped report roundtrip command.")
    extracted_prompt = extract_codex_next_prompt(response)
    prompt_hash = hashlib.sha256(extracted_prompt.encode("utf-8")).hexdigest()
    existing_prompt_path = workspace / "outbox" / "existing_pending.md"
    existing_prompt_path.parent.mkdir(parents=True)
    existing_prompt_path.write_text(extracted_prompt, encoding="utf-8")
    queue = CommandQueue(workspace / "queue")
    queue.enqueue(
        Command(
            id="cmd_existing_pending",
            type=CommandType.USER_MANUAL_COMMAND,
            source="pm_assistant_report_roundtrip",
            prompt_path=str(existing_prompt_path),
            dedupe_key=f"report_roundtrip:{bridge_attempt_id}:{prompt_hash}",
        )
    )
    config = replace(
        make_config(workspace, template_dir),
        bridge_attempt_id=bridge_attempt_id,
        submit_local_agent=False,
    )

    result = run_report_roundtrip(config=config, gui=FakeGui(response), queue=queue)

    staged_prompt = (workspace / "outbox" / "next_local_agent_prompt.md").read_text(
        encoding="utf-8"
    )
    events = (workspace / "logs" / "bridge.jsonl").read_text(encoding="utf-8")
    assert result.completed
    assert "Implement the deduped report roundtrip command." in staged_prompt
    assert "local_agent_command_dedupe_result" in events
    assert "cmd_existing_pending" in events
    assert "local_agent_prompt_staged_from_command_id" in events


def test_report_roundtrip_completed_dedupe_is_not_available(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nReady.")
    bridge_attempt_id = "bridge_completed_dedupe"
    response = valid_response("Implement the completed duplicate command.")
    extracted_prompt = extract_codex_next_prompt(response)
    prompt_hash = hashlib.sha256(extracted_prompt.encode("utf-8")).hexdigest()
    existing_prompt_path = workspace / "outbox" / "completed.md"
    existing_prompt_path.parent.mkdir(parents=True)
    existing_prompt_path.write_text(extracted_prompt, encoding="utf-8")
    queue = CommandQueue(workspace / "queue")
    queue.enqueue(
        Command(
            id="cmd_completed",
            type=CommandType.USER_MANUAL_COMMAND,
            source="pm_assistant_report_roundtrip",
            prompt_path=str(existing_prompt_path),
            dedupe_key=f"report_roundtrip:{bridge_attempt_id}:{prompt_hash}",
        )
    )
    queue.mark_completed("cmd_completed")
    config = replace(
        make_config(workspace, template_dir),
        bridge_attempt_id=bridge_attempt_id,
        submit_local_agent=False,
    )

    with pytest.raises(ReportRoundtripError, match="command_not_dispatchable_after_enqueue"):
        run_report_roundtrip(config=config, gui=FakeGui(response), queue=queue)

    events = (workspace / "logs" / "bridge.jsonl").read_text(encoding="utf-8")
    assert "local_agent_command_dedupe_result" in events
    assert "command_not_dispatchable_after_enqueue" in events


def test_report_roundtrip_fails_when_enqueue_returns_no_command_id(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nReady.")

    class MissingCommandIdQueue(CommandQueue):
        def enqueue_with_result(self, command: Command) -> CommandQueueEnqueueResult:
            return CommandQueueEnqueueResult(
                command_id=None,
                added=False,
                command=command,
                reason="test missing id",
            )

    with pytest.raises(ReportRoundtripError, match="command_id_missing_after_enqueue"):
        run_report_roundtrip(
            config=make_config(workspace, template_dir),
            gui=FakeGui(valid_response("Implement the command.")),
            queue=MissingCommandIdQueue(workspace / "queue"),
        )


def test_one_cycle_bound_is_enforced(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nReady.")

    with pytest.raises(ReportRoundtripError, match="exactly one cycle"):
        run_report_roundtrip(
            config=make_config(workspace, template_dir, max_cycles=2),
            gui=FakeGui(valid_response()),
        )


def test_gui_calls_are_mocked_in_expected_order(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nReady.")
    gui = FakeGui(valid_response())

    run_report_roundtrip(config=make_config(workspace, template_dir), gui=gui)

    assert gui.actions == [
        "activate:ChatGPT",
        "copy_text",
        "paste",
        "submit",
        "wait:9",
        "activate:ChatGPT",
        "copy_response",
        "queue_handoff_mode:False",
        "activate:Codex",
        "copy_text",
        "paste",
        "pre_submit_check",
        "submit",
        "post_submit_check",
    ]


def test_pm_response_copy_reactivates_pm_before_copy_after_focus_change(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nReady.")
    gui = CopyRequiresFreshPmActivationGui(valid_response())

    result = run_report_roundtrip(config=make_config(workspace, template_dir), gui=gui)

    wait_index = gui.actions.index("wait:9")
    copy_index = gui.actions.index("copy_response")
    assert result.completed
    assert gui.actions[wait_index + 1] == "simulate:codex_frontmost"
    assert gui.actions[copy_index - 1] == "activate:ChatGPT"
    assert copy_index < gui.actions.index("activate:Codex")


def test_phase_events_order_pm_copy_before_codex_activation(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nReady.")
    config = replace(
        make_config(workspace, template_dir),
        bridge_attempt_id="bridge_phase_order",
        debug_state_machine=True,
        debug_gui_actions=True,
    )

    result = run_report_roundtrip(config=config, gui=FakeGui(valid_response()))

    assert result.completed
    events = [
        json.loads(line)["event_type"]
        for line in (workspace / "logs" / "bridge.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    ordered = [
        "pm_phase_started",
        "pm_response_wait_started",
        "pm_response_generation_finished",
        "pm_response_copy_phase_started",
        "pm_target_reactivated_before_copy",
        "pm_response_copy_clicked",
        "pm_response_saved",
        "codex_next_prompt_extracted",
        "local_agent_phase_started",
        "codex_activation_started",
        "local_agent_app_activated",
    ]
    indexes = [events.index(event_type) for event_type in ordered]
    assert indexes == sorted(indexes)
    state_debug = [
        json.loads(line)
        for line in (workspace / "logs" / "gui_state_machine_debug.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    state_names = [
        event["metadata"].get("state_name")
        for event in state_debug
        if event["event_type"] == "bridge_state_transition"
    ]
    assert "codex_next_prompt_extracted" in state_names
    assert "local_agent_phase_started" in state_names
    assert "codex_activation_started" in state_names


def test_unconfirmed_local_agent_submit_does_not_complete_or_consume_command(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nReady.")
    gui = UnconfirmedSubmitGui(valid_response("Implement the next safe Agent Bridge task."))

    result = run_report_roundtrip(config=make_config(workspace, template_dir), gui=gui)

    queue = CommandQueue(workspace / "queue")
    events = (workspace / "logs" / "bridge.jsonl").read_text(encoding="utf-8")
    assert not result.completed
    assert result.reason == "LOCAL_AGENT_SUBMIT_UNCONFIRMED"
    assert queue.get_in_progress() is None
    assert len(queue.list_pending()) == 1
    assert "local_agent_submit_attempted" in events
    assert "local_agent_submit_unconfirmed" in events
    assert "local_agent_submit_confirmed" not in events


def test_local_agent_submit_skipped_when_prompt_not_present_before_submit(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nReady.")
    gui = PromptMissingBeforeSubmitGui(valid_response("Implement the next safe Agent Bridge task."))

    result = run_report_roundtrip(config=make_config(workspace, template_dir), gui=gui)

    queue = CommandQueue(workspace / "queue")
    events = (workspace / "logs" / "bridge.jsonl").read_text(encoding="utf-8")
    assert result.completed
    assert result.reason == "ONE_CYCLE_COMPLETE"
    assert queue.get_in_progress() is not None
    assert len(queue.list_pending()) == 0
    assert gui.actions == [
        "activate:ChatGPT",
        "copy_text",
        "paste",
        "submit",
        "wait:9",
        "activate:ChatGPT",
        "copy_response",
        "queue_handoff_mode:False",
        "activate:Codex",
        "copy_text",
        "paste",
        "pre_submit_check",
        "submit",
        "post_submit_check",
    ]
    assert "local_agent_prompt_not_present_before_submit" in events
    assert "prompt_presence_check_skipped_by_policy" in events
    assert "local_agent_submit_after_verified_paste" in events
    assert "local_agent_submit_blocked_unverified_prompt_presence" not in events
    assert "local_agent_submit_attempted" in events


def test_visual_send_ready_allows_submit_when_prompt_presence_unavailable(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nReady.")
    gui = VisualSendReadyPromptMissingGui(
        valid_response("Implement the next safe Agent Bridge task.")
    )

    result = run_report_roundtrip(config=make_config(workspace, template_dir), gui=gui)

    events = (workspace / "logs" / "bridge.jsonl").read_text(encoding="utf-8")
    assert result.completed
    assert "local_agent_prompt_not_present_before_submit" in events
    assert "local_agent_visual_send_ready_check_result" in events
    assert "local_agent_submit_allowed_after_paste_checkpoint" in events
    assert "local_agent_submit_attempted" in events
    assert "local_agent_submit_blocked_unverified_prompt_presence" not in events


def test_noop_paste_checkpoint_allows_submit_when_presence_unavailable(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nAGENT_BRIDGE_GUI_ROUNDTRIP_TEST")
    gui = NoopPasteCheckpointPromptMissingGui(valid_noop_response())

    result = run_report_roundtrip(config=make_config(workspace, template_dir), gui=gui)

    events = (workspace / "logs" / "bridge.jsonl").read_text(encoding="utf-8")
    assert result.completed
    assert "local_agent_noop_submit_exception_allowed" in events
    assert "prompt_presence_unavailable_but_noop_paste_verified" in events
    assert "noop_unverified_submit_after_paste_checkpoint" in events
    assert "local_agent_submit_attempted" in events
    assert "local_agent_submit_blocked_unverified_prompt_presence" not in events


def test_local_agent_command_remains_pending_when_conservative_idle_timeout_blocks(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nReady.")
    gui = LocalPasteFailureGui(valid_response())

    with pytest.raises(ReportRoundtripError, match="stop_on_idle_timeout is enabled"):
        run_report_roundtrip(config=make_config(workspace, template_dir), gui=gui)

    pending = CommandQueue(workspace / "queue").list_pending()
    assert len(pending) == 1
    assert pending[0].source == "pm_assistant_report_roundtrip"
    assert gui.actions.count("submit") == 1


def test_local_agent_submit_guard_blocks_empty_prompt():
    reason = _local_agent_submit_block_reason(
        prompt_length=0,
        safety_passed=True,
        noop_eligible=True,
        focus_completed=True,
        paste_attempted=True,
        paste_succeeded=True,
    )

    assert reason == "local_agent_prompt_empty"


def test_local_agent_submit_guard_blocks_when_paste_not_attempted():
    reason = _local_agent_submit_block_reason(
        prompt_length=10,
        safety_passed=True,
        noop_eligible=True,
        focus_completed=True,
        paste_attempted=False,
        paste_succeeded=False,
    )

    assert reason == "local_agent_paste_not_attempted"


def test_local_agent_submit_guard_blocks_when_clipboard_set_failed():
    reason = _local_agent_submit_block_reason(
        prompt_length=10,
        safety_passed=True,
        noop_eligible=True,
        focus_completed=True,
        paste_attempted=True,
        paste_succeeded=True,
        clipboard_set_succeeded=False,
    )

    assert reason == "local_agent_clipboard_set_failed"


def test_local_agent_submit_guard_blocks_when_clipboard_set_not_attempted():
    reason = _local_agent_submit_block_reason(
        prompt_length=10,
        safety_passed=True,
        noop_eligible=True,
        focus_completed=True,
        paste_attempted=True,
        paste_succeeded=True,
        clipboard_set_attempted=False,
    )

    assert reason == "local_agent_clipboard_set_not_attempted"


def test_local_agent_submit_guard_blocks_when_clipboard_readback_mismatches():
    reason = _local_agent_submit_block_reason(
        prompt_length=10,
        safety_passed=True,
        noop_eligible=True,
        focus_completed=True,
        paste_attempted=True,
        paste_succeeded=True,
        clipboard_readback_matches_prompt_hash=False,
    )

    assert reason == "local_agent_clipboard_readback_mismatch"


def test_local_agent_submit_guard_blocks_when_paste_backend_failed():
    reason = _local_agent_submit_block_reason(
        prompt_length=10,
        safety_passed=True,
        noop_eligible=True,
        focus_completed=True,
        paste_attempted=True,
        paste_succeeded=True,
        paste_backend_succeeded=False,
    )

    assert reason == "local_agent_paste_backend_failed"


def test_local_agent_submit_guard_blocks_when_state_remains_idle_after_paste():
    reason = _local_agent_submit_block_reason(
        prompt_length=10,
        safety_passed=True,
        noop_eligible=True,
        focus_completed=True,
        paste_attempted=True,
        paste_succeeded=True,
        prompt_presence_verified=None,
        codex_send_ready_after_paste=False,
    )

    assert reason is None


def test_codex_activation_phase_order_guard_blocks_early_activation():
    assert (
        _codex_activation_phase_order_block_reason(
            pm_response_copy_attempted=False,
            pm_response_copy_succeeded=False,
            pm_response_saved=False,
            codex_next_prompt_extracted=False,
            local_agent_prompt_staged=False,
        )
        == "pm_response_copy_not_attempted"
    )
    assert (
        _codex_activation_phase_order_block_reason(
            pm_response_copy_attempted=True,
            pm_response_copy_succeeded=True,
            pm_response_saved=True,
            codex_next_prompt_extracted=True,
            local_agent_prompt_staged=True,
        )
        is None
    )


def test_local_agent_submit_blocked_when_click_succeeds_but_paste_missing(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nReady.")
    gui = LocalPasteMissingGui(valid_response("Implement the next safe Agent Bridge task."))

    result = run_report_roundtrip(config=make_config(workspace, template_dir), gui=gui)

    events = (workspace / "logs" / "bridge.jsonl").read_text(encoding="utf-8")
    assert not result.completed
    assert result.reason == "local_agent_click_succeeded_but_paste_missing"
    assert gui.actions.count("submit") == 1
    assert "local_agent_click_succeeded_but_paste_missing" in events
    assert "local_agent_submit_attempted" not in events


def test_local_agent_submit_blocked_when_clipboard_set_fails(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nReady.")
    gui = LocalClipboardSetFailureGui(
        valid_response("Implement the next safe Agent Bridge task.")
    )

    result = run_report_roundtrip(config=make_config(workspace, template_dir), gui=gui)

    events = (workspace / "logs" / "bridge.jsonl").read_text(encoding="utf-8")
    assert not result.completed
    assert result.reason == "local_agent_clipboard_set_failed"
    assert "local_agent_clipboard_set_failed" in events
    assert "local_agent_submit_attempted" not in events


def test_local_agent_submit_blocked_when_clipboard_readback_mismatches(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nReady.")
    gui = LocalClipboardReadbackMismatchGui(
        valid_response("Implement the next safe Agent Bridge task.")
    )

    result = run_report_roundtrip(config=make_config(workspace, template_dir), gui=gui)

    events = (workspace / "logs" / "bridge.jsonl").read_text(encoding="utf-8")
    assert not result.completed
    assert result.reason == "local_agent_clipboard_readback_mismatch"
    assert "local_agent_clipboard_readback_verified" in events
    assert "local_agent_submit_attempted" not in events


def test_local_agent_submit_blocked_when_paste_backend_fails(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nReady.")
    gui = LocalPasteBackendFailureGui(
        valid_response("Implement the next safe Agent Bridge task.")
    )

    result = run_report_roundtrip(config=make_config(workspace, template_dir), gui=gui)

    events = (workspace / "logs" / "bridge.jsonl").read_text(encoding="utf-8")
    assert not result.completed
    assert result.reason == "local_agent_paste_backend_failed"
    assert "local_agent_paste_backend_failed" in events
    assert "local_agent_submit_attempted" not in events


def test_local_agent_submit_blocked_when_paste_not_reflected_in_codex_state(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nReady.")
    gui = LocalPasteNotReflectedGui(
        valid_response("Implement the next safe Agent Bridge task.")
    )

    result = run_report_roundtrip(config=make_config(workspace, template_dir), gui=gui)

    events = (workspace / "logs" / "bridge.jsonl").read_text(encoding="utf-8")
    assert not result.completed
    assert result.reason == "local_agent_paste_not_reflected_in_codex_state"
    assert "local_agent_paste_not_reflected_in_codex_state" in events
    assert "local_agent_submit_attempted" not in events


def test_debug_logs_include_bridge_attempt_id(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nReady.")
    config = replace(
        make_config(workspace, template_dir),
        bridge_attempt_id="bridge_test_123",
        debug_state_machine=True,
        debug_gui_actions=True,
    )

    result = run_report_roundtrip(
        config=config,
        gui=FakeGui(valid_response("Implement the next safe Agent Bridge task.")),
    )

    assert result.completed
    action_events = [
        line
        for line in (workspace / "logs" / "gui_actions_debug.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if "bridge_test_123" in line
    ]
    state_events = [
        line
        for line in (workspace / "logs" / "gui_state_machine_debug.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if "bridge_test_123" in line
    ]
    assert action_events
    assert state_events
    action_names = [
        json.loads(line)["metadata"].get("action")
        for line in (workspace / "logs" / "gui_actions_debug.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert "local_agent_prompt_loaded" in action_names
    assert "local_agent_prompt_hash_computed" in action_names
    assert "local_agent_submit_guard_checked" in action_names


def test_unverified_local_agent_submit_requires_explicit_dangerous_config(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nReady.")
    gui = PromptMissingBeforeSubmitGui(valid_response("Implement the next safe Agent Bridge task."))
    base_config = make_config(workspace, template_dir)
    config = replace(
        base_config,
        targets=replace(
            base_config.targets,
            local_agent=replace(
                base_config.targets.local_agent,
                require_prompt_presence_verification=False,
                allow_unverified_submit=True,
            ),
        ),
    )

    result = run_report_roundtrip(config=config, gui=gui)

    events = (workspace / "logs" / "bridge.jsonl").read_text(encoding="utf-8")
    assert result.completed
    assert "local_agent_unverified_submit_allowed" in events
    assert "local_agent_submit_attempted" in events


def test_noop_unverified_submit_can_complete_with_artifact_confirmation(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nAGENT_BRIDGE_GUI_ROUNDTRIP_TEST")
    gui = NoopArtifactConfirmedGui(
        valid_noop_response(),
        workspace / "reports" / "latest_agent_report.md",
    )

    result = run_report_roundtrip(config=make_config(workspace, template_dir), gui=gui)

    events = (workspace / "logs" / "bridge.jsonl").read_text(encoding="utf-8")
    assert result.completed
    assert result.reason == "ONE_CYCLE_COMPLETE"
    assert "local_agent_noop_unverified_submit_eligible" in events
    assert "local_agent_unverified_submit_attempted" in events
    assert "local_agent_artifact_confirmation_succeeded" in events
    assert "local_agent_submit_confirmed_by_artifact" in events


def test_noop_roundtrip_can_stop_after_local_agent_submit_without_artifact_wait(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nAGENT_BRIDGE_GUI_ROUNDTRIP_TEST")
    gui = PromptMissingBeforeSubmitGui(valid_noop_response())
    base_config = make_config(workspace, template_dir)
    config = replace(
        base_config,
        stop_after_local_agent_submit=True,
        wait_for_artifact_confirmation=False,
    )

    result = run_report_roundtrip(config=config, gui=gui)

    queue = CommandQueue(workspace / "queue")
    events = (workspace / "logs" / "bridge.jsonl").read_text(encoding="utf-8")
    assert result.completed
    assert result.reason == "LOCAL_AGENT_SUBMIT_ATTEMPTED_STOPPED_FOR_QUEUE"
    assert gui.actions == [
        "activate:ChatGPT",
        "copy_text",
        "paste",
        "submit",
        "wait:9",
        "activate:ChatGPT",
        "copy_response",
        "queue_handoff_mode:True",
        "activate:Codex",
        "copy_text",
        "paste",
        "pre_submit_check",
        "submit",
    ]
    assert queue.get_in_progress() is not None
    assert "local_agent_noop_unverified_submit_eligible" in events
    assert "local_agent_queue_handoff_mode_enabled" in events
    assert "local_agent_submit_attempted" in events
    assert "report_roundtrip_stopped_after_local_agent_submit" in events
    assert "local_agent_artifact_confirmation_wait_started" not in events
    assert "post_submit_check" not in gui.actions
    assert gui.local_agent_queue_handoff_modes == [True]


def test_roundtrip_can_stage_local_agent_prompt_without_submitting(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nReady.")
    base_config = make_config(workspace, template_dir)
    config = replace(base_config, submit_local_agent=False)
    gui = FakeGui(valid_response("Implement the next safe Agent Bridge task."))

    result = run_report_roundtrip(config=config, gui=gui)

    events = (workspace / "logs" / "bridge.jsonl").read_text(encoding="utf-8")
    assert result.completed
    assert result.reason == "LOCAL_AGENT_PROMPT_STAGED_ONLY"
    assert "activate:Codex" not in gui.actions
    assert "report_roundtrip_stopped_before_local_agent_submit" in events
    assert CommandQueue(workspace / "queue").get_in_progress() is None


def test_noop_artifact_confirmation_requires_success_title_as_report_title(tmp_path: Path):
    workspace = tmp_path / "workspace"
    report_path = workspace / "reports" / "latest_agent_report.md"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        "# Source Report\n\n"
        "The local-agent prompt must write a report titled:\n\n"
        f"{NOOP_VALIDATION_SUCCESS_TITLE}\n",
        encoding="utf-8",
    )
    initial_hash = report_path.read_bytes()
    event_log = EventLog(workspace / "logs" / "bridge.jsonl")
    current_time = 0.0

    def monotonic() -> float:
        return current_time

    def sleep(seconds: float) -> None:
        nonlocal current_time
        current_time += seconds

    confirmed = _wait_for_noop_artifact_confirmation(
        report_path=report_path,
        event_log=event_log,
        timeout_seconds=1,
        poll_interval_seconds=0.5,
        monotonic_fn=monotonic,
        sleep_fn=sleep,
        initial_report_hash=hashlib.sha256(initial_hash).hexdigest(),
        min_report_mtime_ns=report_path.stat().st_mtime_ns + 1,
    )

    events = (workspace / "logs" / "bridge.jsonl").read_text(encoding="utf-8")
    assert not confirmed
    assert "local_agent_artifact_confirmation_failed" in events
    assert "local_agent_artifact_confirmation_succeeded" not in events


def test_local_agent_submit_confirmed_when_new_user_message_detected(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nReady.")
    gui = NewMessageConfirmedGui(valid_response("Implement the next safe Agent Bridge task."))

    result = run_report_roundtrip(config=make_config(workspace, template_dir), gui=gui)

    events = (workspace / "logs" / "bridge.jsonl").read_text(encoding="utf-8")
    assert result.completed
    assert "local_agent_submit_confirmed" in events
    assert "new_user_message_detected" in events


def test_roundtrip_aborts_before_paste_when_pm_backend_preflight_fails(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nReady.")
    gui = FakeGui(valid_response())

    def failing_preflight() -> PMBackendPreflightResult:
        return PMBackendPreflightResult(
            backend=PMAssistantBackend.CHROME_JS,
            target=default_gui_targets().pm_assistant,
            dry_run=False,
            activate=True,
            checks=(
                PMBackendCheck(
                    name="dom_javascript",
                    succeeded=False,
                    detail="DOM JavaScript execution failed through Apple Events.",
                ),
            ),
        )

    with pytest.raises(ReportRoundtripError, match="PM backend preflight failed"):
        run_report_roundtrip(
            config=make_config(
                workspace,
                template_dir,
                require_pm_backend_preflight=True,
            ),
            gui=gui,
            pm_backend_preflight=failing_preflight,
        )

    assert gui.actions == []
    assert (workspace / "outbox" / "pm_assistant_prompt.md").exists()


def test_report_roundtrip_command_requires_auto_confirm(monkeypatch, tmp_path: Path):
    configure_cli(monkeypatch, tmp_path)

    result = CliRunner().invoke(cli_module.app, ["dogfood-report-roundtrip"])

    assert result.exit_code == 1
    assert "requires --auto-confirm" in result.output


def test_report_roundtrip_command_applies_pm_target_override(monkeypatch, tmp_path: Path):
    configure_cli(monkeypatch, tmp_path)
    captured: dict[str, str | None] = {}

    def fake_run_report_roundtrip(**kwargs):
        config = kwargs["config"]
        captured["profile"] = config.targets.pm_assistant.profile
        captured["backend"] = config.targets.pm_assistant.backend
        captured["asset_profile"] = config.targets.pm_assistant.visual_asset_profile
        captured["bundle_id"] = config.targets.pm_assistant.bundle_id
        return SimpleNamespace(
            safety_paused=False,
            reason="LOCAL_AGENT_PROMPT_STAGED_ONLY",
            cycles_completed=1,
            pm_response_path=None,
            extracted_prompt_path=None,
            local_agent_prompt_path=None,
        )

    monkeypatch.setattr(cli_module, "run_report_roundtrip", fake_run_report_roundtrip)
    monkeypatch.setattr(
        cli_module,
        "targets_with_pm_profile",
        lambda targets, _profile: (
            targets.__class__(
                pm_assistant=targets.pm_assistant.__class__(
                    **{
                        **targets.pm_assistant.__dict__,
                        "profile": "chatgpt_chrome_app",
                        "backend": "chatgpt_chrome_app_visual",
                        "visual_asset_profile": "chatgpt_chrome_app",
                        "bundle_id": "com.google.Chrome.app.test",
                    }
                ),
                local_agent=targets.local_agent,
            )
        ),
    )
    monkeypatch.setattr(cli_module, "MacOSSystemEventsGuiAdapter", lambda: SimpleNamespace())

    result = CliRunner().invoke(
        cli_module.app,
        [
            "dogfood-report-roundtrip",
            "--auto-confirm",
            "--pm-target",
            "chatgpt_chrome_app",
            "--no-submit-local-agent",
        ],
    )

    assert result.exit_code == 0
    assert captured == {
        "profile": "chatgpt_chrome_app",
        "backend": "chatgpt_chrome_app_visual",
        "asset_profile": "chatgpt_chrome_app",
        "bundle_id": "com.google.Chrome.app.test",
    }
