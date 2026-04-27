from pathlib import Path
from agent_bridge.core.models import Command, CommandStatus


class CommandQueue:
    def __init__(self, queue_dir: Path):
        self.queue_dir = queue_dir
        self.pending_path = queue_dir / "pending_commands.jsonl"
        self.completed_path = queue_dir / "completed_commands.jsonl"
        self.failed_path = queue_dir / "failed_commands.jsonl"
        self.in_progress_path = queue_dir / "in_progress.json"
        self.queue_dir.mkdir(parents=True, exist_ok=True)

    def _read_jsonl(self, path: Path) -> list[Command]:
        if not path.exists():
            return []
        commands = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                commands.append(Command.model_validate_json(line))
        return commands

    def _write_jsonl(self, path: Path, commands: list[Command]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(c.model_dump_json() for c in commands)
        path.write_text(content + ("\n" if content else ""), encoding="utf-8")

    def list_pending(self) -> list[Command]:
        return self._read_jsonl(self.pending_path)

    def enqueue(self, command: Command) -> bool:
        pending = self.list_pending()
        existing = {c.dedupe_key for c in pending}
        for path in [self.completed_path, self.failed_path]:
            existing.update(c.dedupe_key for c in self._read_jsonl(path))
        if command.dedupe_key in existing:
            return False
        pending.append(command)
        self._write_jsonl(self.pending_path, pending)
        return True

    def pop_next(self) -> Command | None:
        pending = self.list_pending()
        if not pending:
            return None
        pending.sort(key=lambda c: (-(c.priority or 0), c.created_at))
        command = pending.pop(0)
        command.status = CommandStatus.IN_PROGRESS
        self._write_jsonl(self.pending_path, pending)
        self.in_progress_path.write_text(command.model_dump_json(indent=2), encoding="utf-8")
        return command

    def pop_by_id(self, command_id: str) -> Command | None:
        pending = self.list_pending()
        for index, command in enumerate(pending):
            if command.id != command_id:
                continue
            pending.pop(index)
            command.status = CommandStatus.IN_PROGRESS
            self._write_jsonl(self.pending_path, pending)
            self.in_progress_path.write_text(command.model_dump_json(indent=2), encoding="utf-8")
            return command
        return None

    def get_in_progress(self) -> Command | None:
        if not self.in_progress_path.exists():
            return None
        return Command.model_validate_json(self.in_progress_path.read_text(encoding="utf-8"))

    def complete_in_progress(self) -> None:
        command = self.get_in_progress()
        if command is None:
            return
        command.status = CommandStatus.COMPLETED
        completed = self._read_jsonl(self.completed_path)
        completed.append(command)
        self._write_jsonl(self.completed_path, completed)
        self.in_progress_path.unlink(missing_ok=True)

    def fail_in_progress(self, reason: str) -> None:
        command = self.get_in_progress()
        if command is None:
            return
        command.status = CommandStatus.FAILED
        command.metadata["failure_reason"] = reason
        failed = self._read_jsonl(self.failed_path)
        failed.append(command)
        self._write_jsonl(self.failed_path, failed)
        self.in_progress_path.unlink(missing_ok=True)
