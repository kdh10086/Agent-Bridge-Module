from io import StringIO
from pathlib import Path

from rich.console import Console

from agent_bridge.codex.dispatcher import Dispatcher
from agent_bridge.codex.prompt_builder import PromptBuilder
from agent_bridge.core.command_queue import CommandQueue
from agent_bridge.core.models import BridgeStateName, Command, CommandType
from agent_bridge.core.state_store import StateStore


def test_dispatcher_persists_safety_pause_for_blocked_command(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "codex_command_wrapper.md").write_text(
        "Type={command_type}\nSource={source}\nPayload={payload}",
        encoding="utf-8",
    )
    payload = tmp_path / "payload.md"
    payload.write_text("NEEDS_USER_DECISION before continuing.", encoding="utf-8")

    queue = CommandQueue(workspace / "queue")
    queue.enqueue(
        Command(
            id="cmd_blocked",
            type=CommandType.CHATGPT_PM_NEXT_TASK,
            source="test",
            payload_path=str(payload),
            dedupe_key="blocked",
        )
    )

    result = Dispatcher(
        queue=queue,
        prompt_builder=PromptBuilder(template_dir),
        workspace_dir=workspace,
        console=Console(file=StringIO()),
    ).dispatch_next(dry_run=True)

    state = StateStore(workspace / "state" / "state.json").load()
    assert result is None
    assert state.safety_pause
    assert state.state == BridgeStateName.PAUSED_FOR_USER_DECISION
    assert (workspace / "inbox" / "user_decision_request.md").exists()
    assert (workspace / "outbox" / "owner_decision_email.md").exists()
    assert "dispatch_blocked" in (workspace / "logs" / "bridge.jsonl").read_text(
        encoding="utf-8"
    )
