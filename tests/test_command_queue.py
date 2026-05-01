import json
import multiprocessing as mp
from pathlib import Path

import pytest
from typer.testing import CliRunner

import agent_bridge.cli as cli_module
from agent_bridge.core.command_queue import CommandQueue, QueueLockTimeoutError
from agent_bridge.core.models import Command, CommandStatus, CommandType


def make_command(
    id_: str,
    type_: CommandType,
    priority: int,
    dedupe: str,
    payload: str,
) -> Command:
    return Command(
        id=id_,
        type=type_,
        priority=priority,
        source="test",
        payload_path=payload,
        dedupe_key=dedupe,
    )


def hold_queue_lock(queue_dir: str, ready, release) -> None:
    import fcntl

    lock_path = Path(queue_dir) / "queue.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        ready.set()
        release.wait(5)
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def enqueue_from_process(queue_dir: str, index: int) -> None:
    CommandQueue(Path(queue_dir)).enqueue(
        Command(
            id=f"cmd_{index}",
            type=CommandType.TEST,
            source="process-test",
            prompt_text=f"hello {index}",
            dedupe_key=f"process:{index}",
        )
    )


def test_queue_priority_and_dedupe(tmp_path: Path):
    payload = tmp_path / "payload.md"
    payload.write_text("hello", encoding="utf-8")
    queue = CommandQueue(tmp_path / "queue")

    assert queue.enqueue(make_command("low", CommandType.CHATGPT_PM_NEXT_TASK, 50, "same", str(payload)))
    assert not queue.enqueue(make_command("dup", CommandType.CHATGPT_PM_NEXT_TASK, 50, "same", str(payload)))
    assert queue.enqueue(make_command("high", CommandType.CI_FAILURE_FIX, 80, "ci", str(payload)))

    popped = queue.pop_next()
    assert popped is not None
    assert popped.id == "high"


def test_enqueue_with_result_returns_new_command_id(tmp_path: Path):
    payload = tmp_path / "payload.md"
    payload.write_text("hello", encoding="utf-8")
    queue = CommandQueue(tmp_path / "queue")

    result = queue.enqueue_with_result(
        make_command("cmd_new", CommandType.USER_MANUAL_COMMAND, 95, "new", str(payload))
    )

    assert result.added is True
    assert result.deduped is False
    assert result.command_id == "cmd_new"
    assert result.command is not None
    assert result.command.status == CommandStatus.PENDING


def test_enqueue_with_result_returns_existing_pending_dedupe_id(tmp_path: Path):
    payload = tmp_path / "payload.md"
    payload.write_text("hello", encoding="utf-8")
    queue = CommandQueue(tmp_path / "queue")
    assert queue.enqueue(
        make_command("cmd_existing", CommandType.USER_MANUAL_COMMAND, 95, "same", str(payload))
    )

    result = queue.enqueue_with_result(
        make_command("cmd_duplicate", CommandType.USER_MANUAL_COMMAND, 95, "same", str(payload))
    )

    assert result.added is False
    assert result.deduped is True
    assert result.command_id == "cmd_existing"
    assert result.existing_command_id == "cmd_existing"
    assert result.existing_status == CommandStatus.PENDING


def test_enqueue_with_result_reports_completed_dedupe_as_terminal(tmp_path: Path):
    payload = tmp_path / "payload.md"
    payload.write_text("hello", encoding="utf-8")
    queue = CommandQueue(tmp_path / "queue")
    assert queue.enqueue(
        make_command("cmd_existing", CommandType.USER_MANUAL_COMMAND, 95, "same", str(payload))
    )
    assert queue.mark_completed("cmd_existing") is not None

    result = queue.enqueue_with_result(
        make_command("cmd_duplicate", CommandType.USER_MANUAL_COMMAND, 95, "same", str(payload))
    )

    assert result.added is False
    assert result.deduped is True
    assert result.command_id == "cmd_existing"
    assert result.existing_status == CommandStatus.COMPLETED


def test_queue_schema_accepts_prompt_path_and_prompt_text(tmp_path: Path):
    queue = CommandQueue(tmp_path / "queue")
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text("from file", encoding="utf-8")

    path_command = Command(
        id="path",
        type=CommandType.USER_MANUAL_COMMAND,
        source="test",
        prompt_path=str(prompt_path),
        dedupe_key="path",
    )
    text_command = Command(
        id="text",
        type=CommandType.USER_MANUAL_COMMAND,
        source="test",
        prompt_text="inline prompt",
        dedupe_key="text",
    )

    assert path_command.payload_path == str(prompt_path)
    assert path_command.prompt_path == str(prompt_path)
    assert text_command.prompt_text == "inline prompt"
    assert queue.enqueue(path_command)
    assert queue.enqueue(text_command)
    assert {command.id for command in queue.list_pending()} == {"path", "text"}


def test_queue_schema_rejects_ambiguous_or_invalid_records(tmp_path: Path):
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text("from file", encoding="utf-8")

    with pytest.raises(ValueError, match="ambiguous"):
        Command(
            id="ambiguous",
            type=CommandType.USER_MANUAL_COMMAND,
            source="test",
            prompt_path=str(prompt_path),
            prompt_text="inline prompt",
            dedupe_key="ambiguous",
        )

    with pytest.raises(ValueError, match="created_at"):
        Command(
            id="bad-date",
            type=CommandType.USER_MANUAL_COMMAND,
            source="test",
            prompt_text="inline prompt",
            created_at="not-a-date",
            dedupe_key="bad-date",
        )


def test_enqueue_and_status_transitions_are_protected_by_lock(tmp_path: Path):
    payload = tmp_path / "payload.md"
    payload.write_text("hello", encoding="utf-8")
    queue = CommandQueue(tmp_path / "queue", debug=True)

    assert queue.enqueue(make_command("cmd", CommandType.USER_MANUAL_COMMAND, 95, "cmd", str(payload)))
    assert queue.mark_in_progress("cmd") is not None
    assert queue.mark_completed() is not None

    lock_events = [(event["event"], event["operation"]) for event in queue.debug_events]
    assert ("queue_lock_acquired", "enqueue") in lock_events
    assert ("queue_lock_released", "enqueue") in lock_events
    assert ("queue_lock_acquired", "mark_in_progress") in lock_events
    assert ("queue_lock_acquired", "mark_completed") in lock_events


def test_queue_lock_is_released_after_exception(tmp_path: Path, monkeypatch):
    payload = tmp_path / "payload.md"
    payload.write_text("hello", encoding="utf-8")
    queue_dir = tmp_path / "queue"
    queue = CommandQueue(queue_dir)

    def fail_write(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(queue, "_write_jsonl", fail_write)
    with pytest.raises(RuntimeError, match="boom"):
        queue.enqueue(make_command("cmd", CommandType.USER_MANUAL_COMMAND, 95, "cmd", str(payload)))

    unlocked_queue = CommandQueue(queue_dir, lock_timeout_seconds=0.2)
    assert unlocked_queue.enqueue(
        make_command("after", CommandType.USER_MANUAL_COMMAND, 95, "after", str(payload))
    )


def test_queue_lock_timeout_fails_clearly(tmp_path: Path):
    queue_dir = tmp_path / "queue"
    ctx = mp.get_context("spawn")
    ready = ctx.Event()
    release = ctx.Event()
    process = ctx.Process(target=hold_queue_lock, args=(str(queue_dir), ready, release))
    process.start()
    try:
        assert ready.wait(5)
        queue = CommandQueue(queue_dir, lock_timeout_seconds=0.05, lock_poll_interval_seconds=0.01)
        with pytest.raises(QueueLockTimeoutError, match="Timed out acquiring queue lock"):
            queue.enqueue(
                Command(
                    id="blocked",
                    type=CommandType.TEST,
                    source="test",
                    prompt_text="hello",
                    dedupe_key="blocked",
                )
            )
    finally:
        release.set()
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join()


def test_concurrent_enqueue_does_not_corrupt_queue_file(tmp_path: Path):
    queue_dir = tmp_path / "queue"
    ctx = mp.get_context("spawn")
    processes = [
        ctx.Process(target=enqueue_from_process, args=(str(queue_dir), index))
        for index in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(5)
        assert process.exitcode == 0

    commands = CommandQueue(queue_dir).list_pending()
    assert sorted(command.id for command in commands) == ["cmd_0", "cmd_1", "cmd_2", "cmd_3"]
    pending_lines = (queue_dir / "pending_commands.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(pending_lines) == 4
    assert all(json.loads(line)["status"] == "pending" for line in pending_lines)


def test_queue_peek_and_status_transitions_are_persistent(tmp_path: Path):
    payload = tmp_path / "payload.md"
    payload.write_text("hello", encoding="utf-8")
    queue_dir = tmp_path / "queue"
    queue = CommandQueue(queue_dir)

    assert queue.enqueue(make_command("medium", CommandType.CHATGPT_PM_NEXT_TASK, 50, "m", str(payload)))
    assert queue.enqueue(make_command("high", CommandType.CI_FAILURE_FIX, 80, "h", str(payload)))

    next_command = queue.peek_next()
    assert next_command is not None
    assert next_command.id == "high"
    assert {command.id for command in queue.list_pending()} == {"medium", "high"}

    in_progress = queue.mark_in_progress("high")
    assert in_progress is not None
    assert in_progress.status == CommandStatus.IN_PROGRESS
    persisted_in_progress = CommandQueue(queue_dir).get_in_progress()
    assert persisted_in_progress is not None
    assert persisted_in_progress.id == "high"

    completed = CommandQueue(queue_dir).mark_completed()
    assert completed is not None
    assert completed.status == CommandStatus.COMPLETED
    assert CommandQueue(queue_dir).get_in_progress() is None
    assert [command.id for command in CommandQueue(queue_dir).list_commands(CommandStatus.COMPLETED)] == [
        "high"
    ]

    failed = queue.mark_failed("manual failure", "medium")
    assert failed is not None
    assert failed.status == CommandStatus.FAILED
    assert failed.metadata["failure_reason"] == "manual failure"
    assert CommandQueue(queue_dir).list_pending() == []


def test_queue_mark_blocked_from_pending_and_in_progress(tmp_path: Path):
    payload = tmp_path / "payload.md"
    payload.write_text("hello", encoding="utf-8")
    queue = CommandQueue(tmp_path / "queue")

    assert queue.enqueue(make_command("pending", CommandType.USER_MANUAL_COMMAND, 95, "pending", str(payload)))
    blocked = queue.mark_blocked("needs owner", "pending")
    assert blocked is not None
    assert blocked.status == CommandStatus.BLOCKED
    assert blocked.metadata["blocked_reason"] == "needs owner"

    assert queue.enqueue(make_command("active", CommandType.USER_MANUAL_COMMAND, 95, "active", str(payload)))
    assert queue.mark_in_progress("active") is not None
    blocked_active = queue.block_in_progress("safety gate")
    assert blocked_active is not None
    assert blocked_active.id == "active"
    blocked_commands = queue.list_commands(CommandStatus.BLOCKED)
    assert [command.id for command in blocked_commands] == ["pending", "active"]
    assert blocked_commands[-1].metadata["blocked_reason"] == "safety gate"


def test_malformed_queue_lines_are_quarantined_once(tmp_path: Path):
    queue_dir = tmp_path / "queue"
    queue = CommandQueue(queue_dir)
    queue.pending_path.write_text(
        "\n".join(
            [
                "{not-json",
                '{"id":"bad-status","type":"test","source":"test","prompt_text":"hello","status":"bogus","dedupe_key":"bad-status"}',
                make_command("ok", CommandType.USER_MANUAL_COMMAND, 95, "ok", "payload.md").model_dump_json(),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert [command.id for command in queue.list_pending()] == ["ok"]
    assert [command.id for command in queue.list_pending()] == ["ok"]

    malformed_lines = queue.malformed_path.read_text(encoding="utf-8").splitlines()
    assert len(malformed_lines) == 2
    malformed_record = json.loads(malformed_lines[0])
    assert malformed_record["source_path"].endswith("pending_commands.jsonl")
    assert malformed_record["line_number"] == 1
    assert malformed_record["raw_line"] == "{not-json"
    bad_status_record = json.loads(malformed_lines[1])
    assert bad_status_record["line_number"] == 2
    assert '"status":"bogus"' in bad_status_record["raw_line"]


def test_queue_repair_dry_run_and_apply_preserve_quarantine(tmp_path: Path):
    queue = CommandQueue(tmp_path / "queue", debug=True)
    repairable = Command(
        id="repairable",
        type=CommandType.TEST,
        source="test",
        prompt_text="repair me",
        dedupe_key="repairable",
    ).model_dump_json()
    queue.malformed_path.write_text(
        json.dumps(
            {
                "id": "malformed_1",
                "source_path": str(queue.pending_path),
                "line_number": 1,
                "raw_line": repairable,
                "error": "test quarantine",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    dry_run = queue.repair_malformed_records(apply=False)
    assert dry_run[0]["repairable"] is True
    assert dry_run[0]["applied"] is False
    assert queue.list_pending() == []

    applied = queue.repair_malformed_records(apply=True)
    assert applied[0]["repairable"] is True
    assert applied[0]["applied"] is True
    assert [command.id for command in queue.list_pending()] == ["repairable"]
    assert queue.malformed_path.exists()
    assert any(
        event["event"] == "queue_lock_acquired" and event["operation"] == "repair_malformed"
        for event in queue.debug_events
    )


def test_queue_cli_basic_operations(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(cli_module, "WORKSPACE", workspace)
    monkeypatch.setattr(cli_module, "QUEUE_DIR", workspace / "queue")
    monkeypatch.setattr(cli_module, "LOG_PATH", workspace / "logs" / "bridge.jsonl")
    monkeypatch.setattr(cli_module, "STATE_PATH", workspace / "state" / "state.json")
    runner = CliRunner()

    result = runner.invoke(
        cli_module.app,
        [
            "queue",
            "enqueue",
            "--type",
            "USER_MANUAL_COMMAND",
            "--prompt-text",
            "hello",
            "--source",
            "test",
            "--priority",
            "99",
            "--dedupe-key",
            "cli",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Enqueued" in result.output

    queue = CommandQueue(workspace / "queue")
    command = queue.peek_next()
    assert command is not None
    assert command.prompt_text == "hello"

    list_result = runner.invoke(cli_module.app, ["queue", "list", "--status", "pending"])
    assert list_result.exit_code == 0, list_result.output
    assert "pending" in list_result.output
    assert "len=5" in list_result.output

    progress_result = runner.invoke(cli_module.app, ["queue", "mark-in-progress", command.id])
    assert progress_result.exit_code == 0, progress_result.output
    assert '"status":"in_progress"' in progress_result.output.replace(" ", "")

    completed_result = runner.invoke(cli_module.app, ["queue", "mark-completed"])
    assert completed_result.exit_code == 0, completed_result.output
    assert '"status":"completed"' in completed_result.output.replace(" ", "")


def test_queue_cli_malformed_list_and_repair(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(cli_module, "WORKSPACE", workspace)
    monkeypatch.setattr(cli_module, "QUEUE_DIR", workspace / "queue")
    monkeypatch.setattr(cli_module, "LOG_PATH", workspace / "logs" / "bridge.jsonl")
    monkeypatch.setattr(cli_module, "STATE_PATH", workspace / "state" / "state.json")
    queue = CommandQueue(workspace / "queue")
    raw_line = Command(
        id="repair_cli",
        type=CommandType.TEST,
        source="test",
        prompt_text="repair me",
        dedupe_key="repair_cli",
    ).model_dump_json()
    queue.malformed_path.write_text(
        json.dumps(
            {
                "id": "malformed_cli",
                "source_path": str(queue.pending_path),
                "line_number": 1,
                "raw_line": raw_line,
                "error": "test quarantine",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    runner = CliRunner()

    list_result = runner.invoke(cli_module.app, ["queue", "malformed", "list"])
    assert list_result.exit_code == 0, list_result.output
    assert "malformed_cli"[:12] in list_result.output
    assert "prompt_text" in list_result.output

    dry_run = runner.invoke(cli_module.app, ["queue", "repair"])
    assert dry_run.exit_code == 0, dry_run.output
    assert "dry-run" in dry_run.output
    assert CommandQueue(workspace / "queue").list_pending() == []

    apply_result = runner.invoke(cli_module.app, ["queue", "repair", "--apply"])
    assert apply_result.exit_code == 0, apply_result.output
    assert "repair_cli" in apply_result.output
    assert [command.id for command in CommandQueue(workspace / "queue").list_pending()] == [
        "repair_cli"
    ]
