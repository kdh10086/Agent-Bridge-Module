from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from rich.console import Console

from agent_bridge.codex.dispatcher import Dispatcher
from agent_bridge.codex.prompt_builder import PromptBuilder
from agent_bridge.core.command_queue import CommandQueue
from agent_bridge.core.event_log import EventLog
from agent_bridge.core.models import BridgeStateName, Command, CommandType
from agent_bridge.core.state_store import StateStore
from agent_bridge.gui.local_agent_bridge import dispatch_local_agent_prompt, stage_local_agent_prompt
from agent_bridge.gui.macos_apps import LOCAL_AGENT_TARGET, automatic_submit_supported
from agent_bridge.gui.manual_confirmation import ManualConfirmation


class FakeClipboard:
    def __init__(self):
        self.copied: list[str] = []

    def copy_text(self, text: str) -> None:
        self.copied.append(text)


class FakeAppActivator:
    def __init__(self):
        self.activated: list[str] = []

    def activate(self, app_name: str) -> None:
        self.activated.append(app_name)


def write_templates(template_dir: Path) -> None:
    template_dir.mkdir(parents=True)
    (template_dir / "codex_command_wrapper.md").write_text(
        "Type={command_type}\nSource={source}\nPayload={payload}",
        encoding="utf-8",
    )


def enqueue_payload(workspace: Path, payload: Path, *, command_id: str = "cmd_manual") -> None:
    CommandQueue(workspace / "queue").enqueue(
        Command(
            id=command_id,
            type=CommandType.REQUEST_STATUS_REPORT,
            source="test",
            payload_path=str(payload),
            dedupe_key=f"manual:{command_id}:{payload.name}",
        )
    )


def read_events(workspace: Path) -> list[dict]:
    path = workspace / "logs" / "bridge.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_dispatch_next_dry_run_does_not_copy_or_activate(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    payload = tmp_path / "payload.md"
    payload.write_text("# Task\n\nReport status.", encoding="utf-8")
    write_templates(template_dir)
    enqueue_payload(workspace, payload)

    prompt = Dispatcher(
        queue=CommandQueue(workspace / "queue"),
        prompt_builder=PromptBuilder(template_dir),
        workspace_dir=workspace,
        console=Console(file=StringIO()),
    ).dispatch_next(dry_run=True)

    assert prompt is not None
    assert (workspace / "outbox" / "next_local_agent_prompt.md").exists()
    assert (workspace / "queue" / "in_progress.json").exists()


def test_stage_only_writes_prompt_and_does_not_pop_queue(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    payload = tmp_path / "payload.md"
    payload.write_text("# Task\n\nReport status.", encoding="utf-8")
    write_templates(template_dir)
    enqueue_payload(workspace, payload)

    result = stage_local_agent_prompt(workspace_dir=workspace, template_dir=template_dir, dry_run=True)

    assert result.staged
    assert result.prompt_path.exists()
    assert len(CommandQueue(workspace / "queue").list_pending()) == 1
    assert not (workspace / "queue" / "in_progress.json").exists()


def test_clipboard_copy_requires_confirmation_and_consumes_only_when_confirmed(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    payload = tmp_path / "payload.md"
    clipboard = FakeClipboard()
    payload.write_text("# Task\n\nReport status.", encoding="utf-8")
    write_templates(template_dir)
    enqueue_payload(workspace, payload)

    declined = dispatch_local_agent_prompt(
        workspace_dir=workspace,
        template_dir=template_dir,
        copy_to_clipboard=True,
        activate_app=False,
        local_agent_target=LOCAL_AGENT_TARGET,
        clipboard=clipboard,
        confirmation=ManualConfirmation(lambda _: False),
    )
    accepted = dispatch_local_agent_prompt(
        workspace_dir=workspace,
        template_dir=template_dir,
        copy_to_clipboard=True,
        activate_app=False,
        local_agent_target=LOCAL_AGENT_TARGET,
        clipboard=clipboard,
        confirmation=ManualConfirmation(lambda _: True),
    )

    assert declined.staged
    assert not declined.copied
    assert not declined.consumed
    assert accepted.copied
    assert accepted.consumed
    assert len(clipboard.copied) == 1
    assert CommandQueue(workspace / "queue").list_pending() == []
    assert (workspace / "queue" / "in_progress.json").exists()


def test_app_activation_requires_confirmation(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    payload = tmp_path / "payload.md"
    activator = FakeAppActivator()
    payload.write_text("# Task\n\nReport status.", encoding="utf-8")
    write_templates(template_dir)
    enqueue_payload(workspace, payload)

    declined = dispatch_local_agent_prompt(
        workspace_dir=workspace,
        template_dir=template_dir,
        copy_to_clipboard=False,
        activate_app=True,
        local_agent_target=LOCAL_AGENT_TARGET,
        app_activator=activator,
        confirmation=ManualConfirmation(lambda _: False),
    )
    accepted = dispatch_local_agent_prompt(
        workspace_dir=workspace,
        template_dir=template_dir,
        copy_to_clipboard=False,
        activate_app=True,
        local_agent_target=LOCAL_AGENT_TARGET,
        app_activator=activator,
        confirmation=ManualConfirmation(lambda _: True),
    )

    assert not declined.activated
    assert not declined.consumed
    assert accepted.activated
    assert accepted.consumed
    assert activator.activated == [LOCAL_AGENT_TARGET.app_name]


def test_safety_gated_payload_blocks_manual_dispatch(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    payload = tmp_path / "payload.md"
    clipboard = FakeClipboard()
    activator = FakeAppActivator()
    payload.write_text("# Task\n\nRISK_HIGH requires owner review.", encoding="utf-8")
    write_templates(template_dir)
    enqueue_payload(workspace, payload)

    result = dispatch_local_agent_prompt(
        workspace_dir=workspace,
        template_dir=template_dir,
        copy_to_clipboard=True,
        activate_app=True,
        local_agent_target=LOCAL_AGENT_TARGET,
        clipboard=clipboard,
        app_activator=activator,
        confirmation=ManualConfirmation(lambda _: True),
    )

    state = StateStore(workspace / "state" / "state.json").load()
    assert result.blocked
    assert not result.staged
    assert not result.copied
    assert not result.activated
    assert not result.consumed
    assert state.safety_pause
    assert state.state == BridgeStateName.PAUSED_FOR_USER_DECISION
    assert clipboard.copied == []
    assert activator.activated == []
    assert len(CommandQueue(workspace / "queue").list_pending()) == 1
    assert (workspace / "inbox" / "user_decision_request.md").exists()
    assert (workspace / "outbox" / "owner_decision_email.md").exists()


def test_manual_dispatch_event_logs_are_written(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    payload = tmp_path / "payload.md"
    clipboard = FakeClipboard()
    activator = FakeAppActivator()
    payload.write_text("# Task\n\nReport status.", encoding="utf-8")
    write_templates(template_dir)
    enqueue_payload(workspace, payload)

    dispatch_local_agent_prompt(
        workspace_dir=workspace,
        template_dir=template_dir,
        copy_to_clipboard=True,
        activate_app=True,
        local_agent_target=LOCAL_AGENT_TARGET,
        clipboard=clipboard,
        app_activator=activator,
        confirmation=ManualConfirmation(lambda _: True),
        event_log=EventLog(workspace / "logs" / "bridge.jsonl"),
    )

    event_types = [event["event_type"] for event in read_events(workspace)]
    assert "local_agent_dispatch_requested" in event_types
    assert "local_agent_prompt_staged" in event_types
    assert "local_agent_clipboard_copy_confirmed" in event_types
    assert "local_agent_activation_confirmed" in event_types


def test_no_submit_or_enter_behavior_exists():
    assert automatic_submit_supported() is False
    assert not hasattr(FakeAppActivator, "submit")
    assert not hasattr(FakeAppActivator, "press_enter")
