from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

import agent_bridge.cli as cli_module
from agent_bridge.core.event_log import EventLog
from agent_bridge.gui.computer_use_trigger import (
    build_computer_use_terminal_trigger,
    build_external_runner_command,
    write_computer_use_terminal_trigger,
)


def test_external_runner_command_uses_absolute_repo_path(tmp_path: Path):
    command = build_external_runner_command(tmp_path)

    assert f"cd {tmp_path.resolve()}" in command
    assert "source .venv/bin/activate" in command
    assert "bash scripts/run_gui_roundtrip_external.sh" in command


def test_trigger_file_content_limits_computer_use_to_terminal_trigger(tmp_path: Path):
    trigger = build_computer_use_terminal_trigger(tmp_path, tmp_path / "workspace")

    assert str(tmp_path.resolve()) in trigger.content
    assert "already-open normal macOS Terminal" in trigger.content
    assert "Paste exactly one shell command" in trigger.content
    assert "Press Enter once" in trigger.content
    assert "Computer Use must not operate ChatGPT directly" in trigger.content
    assert "Computer Use must not paste into Codex manually" in trigger.content
    assert "enforcing SafetyGate" in trigger.content
    assert "one-cycle and max-runtime bounds" in trigger.content


def test_write_trigger_creates_file_and_logs_events(tmp_path: Path):
    workspace = tmp_path / "workspace"
    log_path = workspace / "logs" / "bridge.jsonl"

    trigger = write_computer_use_terminal_trigger(
        repo_root=tmp_path,
        workspace_dir=workspace,
        event_log=EventLog(log_path),
    )

    assert trigger.path.exists()
    assert trigger.path == workspace / "outbox" / "computer_use_terminal_trigger.md"
    assert trigger.path.read_text(encoding="utf-8") == trigger.content
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert [event["event_type"] for event in events] == [
        "computer_use_trigger_prepared",
        "external_terminal_trigger_expected",
    ]


def test_prepare_trigger_cli_writes_workspace_file(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(cli_module, "ROOT", tmp_path)
    monkeypatch.setattr(cli_module, "WORKSPACE", workspace)
    monkeypatch.setattr(cli_module, "LOG_PATH", workspace / "logs" / "bridge.jsonl")

    result = CliRunner().invoke(cli_module.app, ["prepare-computer-use-terminal-trigger"])

    assert result.exit_code == 0
    assert "Computer Use terminal trigger written" in result.output
    assert (workspace / "outbox" / "computer_use_terminal_trigger.md").exists()


def test_show_trigger_cli_previews_without_existing_file(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(cli_module, "ROOT", tmp_path)
    monkeypatch.setattr(cli_module, "WORKSPACE", workspace)
    monkeypatch.setattr(cli_module, "LOG_PATH", workspace / "logs" / "bridge.jsonl")

    result = CliRunner().invoke(cli_module.app, ["show-computer-use-terminal-trigger"])

    assert result.exit_code == 0
    assert "Computer Use Terminal Trigger" in result.output
    assert "Computer Use must not operate ChatGPT directly" in result.output
