from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

import agent_bridge.cli as cli_module
from agent_bridge.core.command_queue import CommandQueue
from agent_bridge.core.models import Command, CommandType
from agent_bridge.gui.clipboard import Clipboard
from agent_bridge.gui.local_agent_bridge import dispatch_local_agent_prompt
from agent_bridge.gui.macos_apps import LOCAL_AGENT_TARGET
from agent_bridge.gui.macos_terminal_confirmation import MacOSTerminalConfirmation
from agent_bridge.gui.manual_confirmation import ConfirmationRequest


class FakeClipboard(Clipboard):
    def __init__(self):
        self.copied: list[str] = []

    def copy_text(self, text: str) -> None:
        self.copied.append(text)


def write_templates(template_dir: Path) -> None:
    template_dir.mkdir(parents=True)
    (template_dir / "codex_command_wrapper.md").write_text(
        "Type={command_type}\nSource={source}\nPayload={payload}",
        encoding="utf-8",
    )


def write_config(config_dir: Path) -> None:
    config_dir.mkdir(parents=True)
    (config_dir / "default.yaml").write_text(
        """
apps:
  local_agent:
    app_name: "Codex"
    window_hint: "Agent Bridge"
    paste_instruction: "Paste into Codex input, then review manually."
""".lstrip(),
        encoding="utf-8",
    )


def configure_cli(monkeypatch, tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    config_dir = tmp_path / "config"
    write_templates(template_dir)
    write_config(config_dir)
    monkeypatch.setattr(cli_module, "ROOT", tmp_path)
    monkeypatch.setattr(cli_module, "WORKSPACE", workspace)
    monkeypatch.setattr(cli_module, "QUEUE_DIR", workspace / "queue")
    monkeypatch.setattr(cli_module, "STATE_PATH", workspace / "state" / "state.json")
    monkeypatch.setattr(cli_module, "LOG_PATH", workspace / "logs" / "bridge.jsonl")
    monkeypatch.setattr(cli_module, "TEMPLATE_DIR", template_dir)
    monkeypatch.setattr(cli_module, "CONFIG_DIR", config_dir)
    return workspace


def enqueue_payload(workspace: Path, payload: Path) -> None:
    CommandQueue(workspace / "queue").enqueue(
        Command(
            id="cmd_terminal",
            type=CommandType.REQUEST_STATUS_REPORT,
            source="test",
            payload_path=str(payload),
            dedupe_key=f"terminal:{payload.name}",
        )
    )


def terminal_confirmation(workspace: Path, answer: str | None, timeout_seconds: int = 120) -> MacOSTerminalConfirmation:
    def opener(_script_path: Path, _request_path: Path, result_path: Path) -> None:
        if answer is not None:
            result_path.write_text(answer, encoding="utf-8")

    return MacOSTerminalConfirmation(
        workspace_dir=workspace,
        timeout_seconds=timeout_seconds,
        terminal_opener=opener,
        sleep_fn=lambda _: None,
    )


def read_event_types(workspace: Path) -> list[str]:
    return [
        json.loads(line)["event_type"]
        for line in (workspace / "logs" / "bridge.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def test_terminal_confirmation_yes_permits_clipboard_copy(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    payload = tmp_path / "payload.md"
    clipboard = FakeClipboard()
    payload.write_text("# Task\n\nReport status.", encoding="utf-8")
    write_templates(template_dir)
    enqueue_payload(workspace, payload)

    result = dispatch_local_agent_prompt(
        workspace_dir=workspace,
        template_dir=template_dir,
        copy_to_clipboard=True,
        activate_app=False,
        local_agent_target=LOCAL_AGENT_TARGET,
        clipboard=clipboard,
        confirmation=terminal_confirmation(workspace, "yes"),
    )

    assert result.copied
    assert result.consumed
    assert len(clipboard.copied) == 1
    assert "terminal_confirmation_confirmed" in read_event_types(workspace)


def test_terminal_confirmation_no_cancels_clipboard_copy_without_consuming(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    payload = tmp_path / "payload.md"
    clipboard = FakeClipboard()
    payload.write_text("# Task\n\nReport status.", encoding="utf-8")
    write_templates(template_dir)
    enqueue_payload(workspace, payload)

    result = dispatch_local_agent_prompt(
        workspace_dir=workspace,
        template_dir=template_dir,
        copy_to_clipboard=True,
        activate_app=False,
        local_agent_target=LOCAL_AGENT_TARGET,
        clipboard=clipboard,
        confirmation=terminal_confirmation(workspace, "no"),
    )

    assert result.staged
    assert not result.copied
    assert not result.consumed
    assert clipboard.copied == []
    assert len(CommandQueue(workspace / "queue").list_pending()) == 1
    assert "terminal_confirmation_denied" in read_event_types(workspace)


def test_terminal_confirmation_timeout_cancels_without_consuming(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    payload = tmp_path / "payload.md"
    clipboard = FakeClipboard()
    payload.write_text("# Task\n\nReport status.", encoding="utf-8")
    write_templates(template_dir)
    enqueue_payload(workspace, payload)

    result = dispatch_local_agent_prompt(
        workspace_dir=workspace,
        template_dir=template_dir,
        copy_to_clipboard=True,
        activate_app=False,
        local_agent_target=LOCAL_AGENT_TARGET,
        clipboard=clipboard,
        confirmation=terminal_confirmation(workspace, None, timeout_seconds=0),
    )

    assert not result.copied
    assert not result.consumed
    assert clipboard.copied == []
    assert len(CommandQueue(workspace / "queue").list_pending()) == 1
    assert "terminal_confirmation_timeout" in read_event_types(workspace)


def test_safety_gate_blocks_before_terminal_window_opens(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    payload = tmp_path / "payload.md"
    clipboard = FakeClipboard()
    opened = False
    payload.write_text("# Task\n\nRISK_HIGH requires owner review.", encoding="utf-8")
    write_templates(template_dir)
    enqueue_payload(workspace, payload)

    def opener(_script_path: Path, _request_path: Path, _result_path: Path) -> None:
        nonlocal opened
        opened = True

    result = dispatch_local_agent_prompt(
        workspace_dir=workspace,
        template_dir=template_dir,
        copy_to_clipboard=True,
        activate_app=False,
        local_agent_target=LOCAL_AGENT_TARGET,
        clipboard=clipboard,
        confirmation=MacOSTerminalConfirmation(workspace_dir=workspace, terminal_opener=opener),
    )

    assert result.blocked
    assert not opened
    assert clipboard.copied == []
    assert "terminal_confirmation_requested" not in read_event_types(workspace)


def test_dispatch_next_dry_run_and_stage_only_do_not_construct_terminal_confirmation(monkeypatch, tmp_path: Path):
    workspace = configure_cli(monkeypatch, tmp_path)
    payload = tmp_path / "payload.md"
    payload.write_text("# Task\n\nReport status.", encoding="utf-8")
    enqueue_payload(workspace, payload)

    class ExplodingTerminalConfirmation:
        def __init__(self, *args, **kwargs):
            raise AssertionError("Terminal confirmation should not be constructed.")

    monkeypatch.setattr(cli_module, "MacOSTerminalConfirmation", ExplodingTerminalConfirmation)

    stage_result = CliRunner().invoke(cli_module.app, ["dispatch-next", "--stage-only"])
    dry_run_result = CliRunner().invoke(cli_module.app, ["dispatch-next", "--dry-run"])

    assert stage_result.exit_code == 0
    assert dry_run_result.exit_code == 0


def test_terminal_confirmation_text_states_forbidden_actions(tmp_path: Path):
    workspace = tmp_path / "workspace"
    seen_request = ""

    def opener(_script_path: Path, request_path: Path, result_path: Path) -> None:
        nonlocal seen_request
        seen_request = request_path.read_text(encoding="utf-8")
        result_path.write_text("yes", encoding="utf-8")

    confirmation = MacOSTerminalConfirmation(workspace_dir=workspace, terminal_opener=opener)
    confirmed = confirmation.confirm_request(
        ConfirmationRequest(
            action_summary="Copy staged local-agent prompt to clipboard",
            target_app_name="Codex",
            target_window_hint="Agent Bridge",
            prompt_path=workspace / "outbox" / "next_local_agent_prompt.md",
            will_do=("Copy text to clipboard.",),
        )
    )

    assert confirmed
    assert "Agent Bridge will NOT paste automatically." in seen_request
    assert "Agent Bridge will NOT press Enter or Return." in seen_request
    assert "Agent Bridge will NOT submit the message." in seen_request
