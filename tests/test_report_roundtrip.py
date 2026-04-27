from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import agent_bridge.cli as cli_module
from agent_bridge.core.command_queue import CommandQueue
from agent_bridge.core.models import BridgeStateName, CommandType
from agent_bridge.core.state_store import StateStore
from agent_bridge.gui.gui_automation import GuiAutomationAdapter
from agent_bridge.gui.macos_apps import ManualStageTarget, default_gui_targets
from agent_bridge.gui.report_roundtrip import (
    ReportRoundtripConfig,
    ReportRoundtripError,
    build_report_roundtrip_pm_prompt,
    extract_codex_next_prompt,
    run_report_roundtrip,
)


class FakeGui(GuiAutomationAdapter):
    def __init__(self, response: str):
        self.response = response
        self.actions: list[str] = []
        self.clipboard: list[str] = []

    def activate_app(self, target: ManualStageTarget) -> None:
        self.actions.append(f"activate:{target.app_name}")

    def copy_text_to_clipboard(self, text: str) -> None:
        self.actions.append("copy_text")
        self.clipboard.append(text)

    def paste_clipboard(self) -> None:
        self.actions.append("paste")

    def submit(self) -> None:
        self.actions.append("submit")

    def wait_for_response(self, timeout_seconds: int) -> None:
        self.actions.append(f"wait:{timeout_seconds}")

    def copy_response_text(self) -> str:
        self.actions.append("copy_response")
        return self.response


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
) -> ReportRoundtripConfig:
    return ReportRoundtripConfig(
        workspace_dir=workspace,
        template_dir=template_dir,
        targets=default_gui_targets(),
        auto_confirm=auto_confirm,
        max_cycles=max_cycles,
        max_runtime_seconds=180,
        pm_response_timeout_seconds=9,
    )


def valid_response(prompt: str = "Implement the next Agent Bridge task.") -> str:
    return f"```CODEX_NEXT_PROMPT\n{prompt}\n```"


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
    assert "Do not include prose before or after the block" in prompt


def test_pm_prompt_keeps_roundtrip_test_noop_requirement():
    prompt = build_report_roundtrip_pm_prompt("# Report\n\nAGENT_BRIDGE_GUI_ROUNDTRIP_TEST")

    assert "safe no-op validation prompt" in prompt
    assert "avoid code changes" in prompt


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
        "activate:Google Chrome",
        "copy_text",
        "paste",
        "submit",
        "wait:9",
        "copy_response",
        "activate:Codex",
        "copy_text",
        "paste",
        "submit",
    ]


def test_report_roundtrip_command_requires_auto_confirm(monkeypatch, tmp_path: Path):
    configure_cli(monkeypatch, tmp_path)

    result = CliRunner().invoke(cli_module.app, ["dogfood-report-roundtrip"])

    assert result.exit_code == 1
    assert "requires --auto-confirm" in result.output
