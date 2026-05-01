from io import StringIO
from pathlib import Path

from rich.console import Console

from agent_bridge.codex.dispatcher import Dispatcher
from agent_bridge.codex.prompt_builder import PromptBuilder
from agent_bridge.core.command_queue import CommandQueue
from agent_bridge.core.models import BridgeStateName, Command, CommandStatus, CommandType
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


def make_dispatcher(workspace: Path, queue: CommandQueue, template_dir: Path) -> Dispatcher:
    template_dir.mkdir(exist_ok=True)
    (template_dir / "codex_command_wrapper.md").write_text(
        "Type={command_type}\nSource={source}\nPayload={payload}",
        encoding="utf-8",
    )
    return Dispatcher(
        queue=queue,
        prompt_builder=PromptBuilder(template_dir),
        workspace_dir=workspace,
        console=Console(file=StringIO()),
    )


def test_dispatcher_resolves_prompt_text(tmp_path: Path):
    workspace = tmp_path / "workspace"
    queue = CommandQueue(workspace / "queue")
    queue.enqueue(
        Command(
            id="cmd_text",
            type=CommandType.USER_MANUAL_COMMAND,
            source="test",
            prompt_text="inline command",
            dedupe_key="text",
        )
    )

    result = make_dispatcher(workspace, queue, tmp_path / "templates").prepare_next_local_agent_prompt(
        consume=False
    )

    assert result.staged
    assert "inline command" in result.prompt


def test_dispatcher_stages_exact_command_id_instead_of_stale_pending(tmp_path: Path):
    workspace = tmp_path / "workspace"
    stale_payload = tmp_path / "stale.md"
    stale_payload.write_text("stale command", encoding="utf-8")
    fresh_payload = tmp_path / "fresh.md"
    fresh_payload.write_text("fresh command", encoding="utf-8")
    queue = CommandQueue(workspace / "queue")
    queue.enqueue(
        Command(
            id="cmd_stale",
            type=CommandType.GITHUB_REVIEW_FIX,
            priority=99,
            source="test",
            prompt_path=str(stale_payload),
            dedupe_key="stale",
        )
    )
    queue.enqueue(
        Command(
            id="cmd_fresh",
            type=CommandType.USER_MANUAL_COMMAND,
            priority=10,
            source="test",
            prompt_path=str(fresh_payload),
            dedupe_key="fresh",
        )
    )

    result = make_dispatcher(workspace, queue, tmp_path / "templates").stage_command_by_id(
        "cmd_fresh"
    )

    assert result.staged
    assert result.command is not None
    assert result.command.id == "cmd_fresh"
    assert "fresh command" in result.prompt
    assert "stale command" not in result.prompt


def test_dispatcher_by_id_reports_missing_command(tmp_path: Path):
    workspace = tmp_path / "workspace"
    queue = CommandQueue(workspace / "queue")

    result = make_dispatcher(workspace, queue, tmp_path / "templates").stage_command_by_id(
        "cmd_missing"
    )

    assert not result.staged
    assert result.command is None
    assert result.reason == "command_id_not_found_after_enqueue"


def test_dispatcher_by_id_blocks_completed_command(tmp_path: Path):
    workspace = tmp_path / "workspace"
    payload = tmp_path / "payload.md"
    payload.write_text("done command", encoding="utf-8")
    queue = CommandQueue(workspace / "queue")
    queue.enqueue(
        Command(
            id="cmd_done",
            type=CommandType.USER_MANUAL_COMMAND,
            source="test",
            prompt_path=str(payload),
            dedupe_key="done",
        )
    )
    queue.mark_completed("cmd_done")

    result = make_dispatcher(workspace, queue, tmp_path / "templates").stage_command_by_id(
        "cmd_done"
    )

    assert not result.staged
    assert result.command is None
    assert result.reason == "command_not_dispatchable_after_enqueue"
    assert result.command_status == CommandStatus.COMPLETED.value


def test_dispatcher_resolves_prompt_path(tmp_path: Path):
    workspace = tmp_path / "workspace"
    payload = tmp_path / "payload.md"
    payload.write_text("file command", encoding="utf-8")
    queue = CommandQueue(workspace / "queue")
    queue.enqueue(
        Command(
            id="cmd_path",
            type=CommandType.USER_MANUAL_COMMAND,
            source="test",
            prompt_path=str(payload),
            dedupe_key="path",
        )
    )

    result = make_dispatcher(workspace, queue, tmp_path / "templates").prepare_next_local_agent_prompt(
        consume=False
    )

    assert result.staged
    assert "file command" in result.prompt


def test_dispatcher_by_id_stages_prompt_text(tmp_path: Path):
    workspace = tmp_path / "workspace"
    queue = CommandQueue(workspace / "queue")
    queue.enqueue(
        Command(
            id="cmd_text",
            type=CommandType.USER_MANUAL_COMMAND,
            source="test",
            prompt_text="inline by id",
            dedupe_key="text-by-id",
        )
    )

    result = make_dispatcher(workspace, queue, tmp_path / "templates").stage_command_by_id(
        "cmd_text"
    )

    assert result.staged
    assert "inline by id" in result.prompt


def test_dispatcher_by_id_stages_legacy_payload_path(tmp_path: Path):
    workspace = tmp_path / "workspace"
    payload = tmp_path / "legacy.md"
    payload.write_text("legacy by id", encoding="utf-8")
    queue = CommandQueue(workspace / "queue")
    queue.enqueue(
        Command(
            id="cmd_legacy",
            type=CommandType.USER_MANUAL_COMMAND,
            source="test",
            payload_path=str(payload),
            dedupe_key="legacy-by-id",
        )
    )

    result = make_dispatcher(workspace, queue, tmp_path / "templates").stage_command_by_id(
        "cmd_legacy"
    )

    assert result.staged
    assert "legacy by id" in result.prompt


def test_dispatcher_reads_legacy_payload_path_records(tmp_path: Path):
    workspace = tmp_path / "workspace"
    payload = tmp_path / "legacy.md"
    payload.write_text("legacy command", encoding="utf-8")
    queue = CommandQueue(workspace / "queue")
    queue.enqueue(
        Command(
            id="cmd_legacy",
            type=CommandType.USER_MANUAL_COMMAND,
            source="test",
            payload_path=str(payload),
            dedupe_key="legacy",
        )
    )

    result = make_dispatcher(workspace, queue, tmp_path / "templates").prepare_next_local_agent_prompt(
        consume=False
    )

    assert result.staged
    assert "legacy command" in result.prompt


def test_dispatcher_skips_malformed_pending_record_and_processes_valid(tmp_path: Path):
    workspace = tmp_path / "workspace"
    payload = tmp_path / "valid.md"
    payload.write_text("valid command", encoding="utf-8")
    queue = CommandQueue(workspace / "queue")
    queue.pending_path.write_text(
        "{not-json\n"
        + Command(
            id="cmd_valid",
            type=CommandType.USER_MANUAL_COMMAND,
            source="test",
            prompt_path=str(payload),
            dedupe_key="valid",
        ).model_dump_json()
        + "\n",
        encoding="utf-8",
    )

    result = make_dispatcher(workspace, queue, tmp_path / "templates").prepare_next_local_agent_prompt(
        consume=False
    )

    assert result.staged
    assert "valid command" in result.prompt
    assert (workspace / "queue" / "malformed_commands.jsonl").exists()
