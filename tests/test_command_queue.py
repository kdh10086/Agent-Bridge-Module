from pathlib import Path
from agent_bridge.core.command_queue import CommandQueue
from agent_bridge.core.models import Command, CommandType


def make_command(id_: str, type_: CommandType, priority: int, dedupe: str, payload: str) -> Command:
    return Command(id=id_, type=type_, priority=priority, source="test", payload_path=payload, dedupe_key=dedupe)


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
