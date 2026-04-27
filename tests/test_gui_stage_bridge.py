from __future__ import annotations

from pathlib import Path

from agent_bridge.core.command_queue import CommandQueue
from agent_bridge.core.models import Command, CommandType
from agent_bridge.core.state_store import StateStore
from agent_bridge.gui.local_agent_bridge import stage_local_agent_prompt
from agent_bridge.gui.macos_apps import automatic_submit_supported
from agent_bridge.gui.manual_confirmation import ManualConfirmation
from agent_bridge.gui.pm_assistant_bridge import stage_pm_prompt


class FakeClipboard:
    def __init__(self):
        self.copied: list[str] = []

    def copy_text(self, text: str) -> None:
        self.copied.append(text)


def write_templates(template_dir: Path) -> None:
    template_dir.mkdir(parents=True)
    (template_dir / "pm_report_prompt.md").write_text("PM prompt:\n{report}", encoding="utf-8")
    (template_dir / "codex_command_wrapper.md").write_text(
        "Type={command_type}\nSource={source}\nPayload={payload}",
        encoding="utf-8",
    )


def write_report(workspace: Path, text: str = "# Report\n\nAll clear.") -> None:
    reports = workspace / "reports"
    reports.mkdir(parents=True)
    (reports / "latest_agent_report.md").write_text(text, encoding="utf-8")


def enqueue_payload(workspace: Path, payload: Path, *, requires_user_approval: bool = False) -> None:
    CommandQueue(workspace / "queue").enqueue(
        Command(
            id="cmd_test",
            type=CommandType.REQUEST_STATUS_REPORT,
            source="test",
            payload_path=str(payload),
            requires_user_approval=requires_user_approval,
            dedupe_key=f"test:{payload.name}",
        )
    )


def test_pm_prompt_staging_writes_outbox_file(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace)

    result = stage_pm_prompt(workspace_dir=workspace, template_dir=template_dir, dry_run=True)

    assert result.staged
    assert result.prompt_path == workspace / "outbox" / "pm_assistant_prompt.md"
    assert result.prompt_path.read_text(encoding="utf-8").startswith("PM prompt:")


def test_local_agent_prompt_staging_writes_outbox_file_without_popping_queue(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    payload = tmp_path / "payload.md"
    write_templates(template_dir)
    payload.write_text("# Task\n\nReport status.", encoding="utf-8")
    enqueue_payload(workspace, payload)

    result = stage_local_agent_prompt(workspace_dir=workspace, template_dir=template_dir, dry_run=True)

    assert result.staged
    assert result.prompt_path == workspace / "outbox" / "next_local_agent_prompt.md"
    assert "REQUEST_STATUS_REPORT" in result.prompt_path.read_text(encoding="utf-8")
    assert len(CommandQueue(workspace / "queue").list_pending()) == 1
    assert not (workspace / "queue" / "in_progress.json").exists()


def test_dry_run_does_not_copy_clipboard(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    clipboard = FakeClipboard()
    write_templates(template_dir)
    write_report(workspace)

    result = stage_pm_prompt(
        workspace_dir=workspace,
        template_dir=template_dir,
        dry_run=True,
        copy_to_clipboard=True,
        clipboard=clipboard,
        confirmation=ManualConfirmation(lambda _: True),
    )

    assert result.staged
    assert not result.copied
    assert clipboard.copied == []


def test_clipboard_copy_requires_confirmation(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    clipboard = FakeClipboard()
    write_templates(template_dir)
    write_report(workspace)

    declined = stage_pm_prompt(
        workspace_dir=workspace,
        template_dir=template_dir,
        dry_run=False,
        copy_to_clipboard=True,
        clipboard=clipboard,
        confirmation=ManualConfirmation(lambda _: False),
    )
    accepted = stage_pm_prompt(
        workspace_dir=workspace,
        template_dir=template_dir,
        dry_run=False,
        copy_to_clipboard=True,
        clipboard=clipboard,
        confirmation=ManualConfirmation(lambda _: True),
    )

    assert not declined.copied
    assert accepted.copied
    assert len(clipboard.copied) == 1


def test_safety_gated_prompt_is_blocked(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nRISK_HIGH requires owner review.")

    result = stage_pm_prompt(workspace_dir=workspace, template_dir=template_dir, dry_run=True)

    state = StateStore(workspace / "state" / "state.json").load()
    assert result.blocked
    assert not result.staged
    assert state.safety_pause
    assert (workspace / "inbox" / "user_decision_request.md").exists()
    assert (workspace / "outbox" / "owner_decision_email.md").exists()
    assert not (workspace / "outbox" / "pm_assistant_prompt.md").exists()


def test_no_submit_or_enter_behavior_exists():
    assert automatic_submit_supported() is False
