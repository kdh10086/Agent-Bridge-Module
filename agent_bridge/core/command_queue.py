import errno
import fcntl
import json
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError

from agent_bridge.core.models import Command, CommandStatus, utc_now_iso


STATUS_FILES: dict[CommandStatus, str] = {
    CommandStatus.PENDING: "pending_commands.jsonl",
    CommandStatus.COMPLETED: "completed_commands.jsonl",
    CommandStatus.FAILED: "failed_commands.jsonl",
    CommandStatus.BLOCKED: "blocked_commands.jsonl",
}


class QueueLockTimeoutError(TimeoutError):
    """Raised when the durable queue lock cannot be acquired before timeout."""


@dataclass(frozen=True)
class CommandQueueEnqueueResult:
    command_id: str | None
    added: bool
    deduped: bool = False
    existing_command_id: str | None = None
    existing_status: CommandStatus | None = None
    command: Command | None = None
    reason: str | None = None


class CommandQueue:
    def __init__(
        self,
        queue_dir: Path,
        *,
        lock_timeout_seconds: float = 5.0,
        lock_poll_interval_seconds: float = 0.05,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
        debug: bool | None = None,
    ):
        self.queue_dir = queue_dir
        self.pending_path = queue_dir / "pending_commands.jsonl"
        self.completed_path = queue_dir / "completed_commands.jsonl"
        self.failed_path = queue_dir / "failed_commands.jsonl"
        self.blocked_path = queue_dir / "blocked_commands.jsonl"
        self.in_progress_path = queue_dir / "in_progress.json"
        self.malformed_path = queue_dir / "malformed_commands.jsonl"
        self.lock_path = queue_dir / "queue.lock"
        self.lock_timeout_seconds = lock_timeout_seconds
        self.lock_poll_interval_seconds = lock_poll_interval_seconds
        self.sleep_fn = sleep_fn
        self.monotonic_fn = monotonic_fn
        self.debug = bool(debug if debug is not None else os.environ.get("AGENT_BRIDGE_QUEUE_DEBUG"))
        self.debug_events: list[dict[str, object]] = []
        self._lock_depth = 0
        self._lock_handle = None
        self.queue_dir.mkdir(parents=True, exist_ok=True)

    def _debug_event(
        self,
        event: str,
        *,
        operation: str,
        command_id: str | None = None,
        error: str | None = None,
    ) -> None:
        if not self.debug:
            return
        record = {
            "event": event,
            "operation": operation,
            "queue_path": str(self.queue_dir),
            "lock_path": str(self.lock_path),
            "command_id": command_id,
        }
        if error:
            record["error"] = error
        self.debug_events.append(record)

    @contextmanager
    def _locked(self, operation: str, command_id: str | None = None) -> Iterator[None]:
        if self._lock_depth > 0:
            yield
            return

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+", encoding="utf-8")
        deadline = self.monotonic_fn() + self.lock_timeout_seconds
        self._debug_event(
            "queue_lock_acquire_attempted",
            operation=operation,
            command_id=command_id,
        )
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN}:
                    handle.close()
                    raise
                remaining = deadline - self.monotonic_fn()
                if remaining <= 0:
                    handle.close()
                    message = (
                        f"Timed out acquiring queue lock for {operation} at {self.lock_path}"
                    )
                    self._debug_event(
                        "queue_lock_timeout",
                        operation=operation,
                        command_id=command_id,
                        error=message,
                    )
                    raise QueueLockTimeoutError(message) from error
                self.sleep_fn(min(self.lock_poll_interval_seconds, remaining))

        self._lock_depth = 1
        self._lock_handle = handle
        self._debug_event("queue_lock_acquired", operation=operation, command_id=command_id)
        try:
            yield
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                self._lock_depth = 0
                self._lock_handle = None
                handle.close()
                self._debug_event("queue_lock_released", operation=operation, command_id=command_id)

    def _read_jsonl(self, path: Path) -> list[Command]:
        if not path.exists():
            return []
        commands = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.strip():
                try:
                    commands.append(Command.model_validate_json(line))
                except (ValidationError, ValueError) as error:
                    self._quarantine_malformed_line(path, line_number, line, error)
        return commands

    def _quarantine_malformed_line(
        self,
        path: Path,
        line_number: int,
        raw_line: str,
        error: Exception,
    ) -> None:
        with self._locked("quarantine_malformed"):
            self.malformed_path.parent.mkdir(parents=True, exist_ok=True)
            malformed_id = sha256(f"{path}:{line_number}:{raw_line}".encode("utf-8")).hexdigest()
            existing_ids: set[str] = set()
            if self.malformed_path.exists():
                for existing_line in self.malformed_path.read_text(encoding="utf-8").splitlines():
                    if not existing_line.strip():
                        continue
                    try:
                        existing_ids.add(str(json.loads(existing_line).get("id", "")))
                    except json.JSONDecodeError:
                        continue
            if malformed_id in existing_ids:
                return
            record = {
                "id": malformed_id,
                "detected_at": utc_now_iso(),
                "source_path": str(path),
                "line_number": line_number,
                "raw_line": raw_line,
                "error": str(error),
            }
            with self.malformed_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")

    def _write_jsonl(self, path: Path, commands: list[Command]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(c.model_dump_json() for c in commands)
        path.write_text(content + ("\n" if content else ""), encoding="utf-8")

    def list_malformed_records(self) -> list[dict[str, object]]:
        with self._locked("list_malformed"):
            if not self.malformed_path.exists():
                return []
            records: list[dict[str, object]] = []
            for index, line in enumerate(
                self.malformed_path.read_text(encoding="utf-8").splitlines(),
                start=1,
            ):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    record = {
                        "id": sha256(line.encode("utf-8")).hexdigest(),
                        "raw_line": line,
                        "error": "Malformed quarantine record.",
                    }
                record["index"] = index
                records.append(record)
            return records

    def repair_malformed_records(self, *, apply: bool = False) -> list[dict[str, object]]:
        with self._locked("repair_malformed"):
            results: list[dict[str, object]] = []
            for record in self.list_malformed_records():
                raw_line = str(record.get("raw_line", ""))
                result = {
                    "index": record.get("index"),
                    "id": record.get("id"),
                    "repairable": False,
                    "applied": False,
                    "reason": "",
                    "command_id": None,
                }
                try:
                    raw_record = json.loads(raw_line)
                    command = Command.model_validate(raw_record)
                except (json.JSONDecodeError, ValidationError, ValueError) as error:
                    result["reason"] = str(error)
                    results.append(result)
                    continue
                command.status = CommandStatus.PENDING
                result["repairable"] = True
                result["command_id"] = command.id
                if apply:
                    result["applied"] = self._enqueue_unlocked(command)
                    if not result["applied"]:
                        result["reason"] = "Duplicate dedupe_key already exists."
                results.append(result)
            return results

    def list_pending(self) -> list[Command]:
        with self._locked("list_pending"):
            return self._read_jsonl(self.pending_path)

    def list_commands(self, status: CommandStatus | None = None) -> list[Command]:
        with self._locked("list_commands"):
            if status == CommandStatus.IN_PROGRESS:
                command = self.get_in_progress()
                return [command] if command else []
            if status is not None:
                return self._read_jsonl(self.queue_dir / STATUS_FILES[status])
            commands: list[Command] = []
            for command_status in STATUS_FILES:
                commands.extend(self.list_commands(command_status))
            commands.extend(self.list_commands(CommandStatus.IN_PROGRESS))
            return commands

    def get_pending_by_id(self, command_id: str) -> Command | None:
        with self._locked("get_pending_by_id", command_id):
            for command in self._read_jsonl(self.pending_path):
                if command.id == command_id:
                    return command
            return None

    def get_by_id(self, command_id: str) -> Command | None:
        with self._locked("get_by_id", command_id):
            return self._find_by_id_unlocked(command_id)

    def enqueue(self, command: Command) -> bool:
        return self.enqueue_with_result(command).added

    def enqueue_with_result(self, command: Command) -> CommandQueueEnqueueResult:
        with self._locked("enqueue", command.id):
            existing = self._find_by_dedupe_key_unlocked(command.dedupe_key)
            if existing is not None:
                return CommandQueueEnqueueResult(
                    command_id=existing.id,
                    added=False,
                    deduped=True,
                    existing_command_id=existing.id,
                    existing_status=existing.status,
                    command=existing,
                    reason=f"Duplicate dedupe_key already exists with status {existing.status.value}.",
                )
            command.status = CommandStatus.PENDING
            pending = self._read_jsonl(self.pending_path)
            pending.append(command)
            self._write_jsonl(self.pending_path, pending)
            return CommandQueueEnqueueResult(
                command_id=command.id,
                added=True,
                deduped=False,
                existing_command_id=None,
                existing_status=None,
                command=command,
            )

    def _enqueue_unlocked(self, command: Command) -> bool:
        if self._find_by_dedupe_key_unlocked(command.dedupe_key) is not None:
            return False
        command.status = CommandStatus.PENDING
        pending = self._read_jsonl(self.pending_path)
        pending.append(command)
        self._write_jsonl(self.pending_path, pending)
        return True

    def _commands_by_status_unlocked(self, status: CommandStatus) -> list[Command]:
        if status == CommandStatus.IN_PROGRESS:
            command = self._get_in_progress_unlocked()
            return [command] if command else []
        return self._read_jsonl(self.queue_dir / STATUS_FILES[status])

    def _find_by_id_unlocked(self, command_id: str) -> Command | None:
        for status in (
            CommandStatus.PENDING,
            CommandStatus.IN_PROGRESS,
            CommandStatus.COMPLETED,
            CommandStatus.FAILED,
            CommandStatus.BLOCKED,
        ):
            for command in self._commands_by_status_unlocked(status):
                if command.id == command_id:
                    return command
        return None

    def _find_by_dedupe_key_unlocked(self, dedupe_key: str) -> Command | None:
        for status in (
            CommandStatus.PENDING,
            CommandStatus.IN_PROGRESS,
            CommandStatus.COMPLETED,
            CommandStatus.FAILED,
            CommandStatus.BLOCKED,
        ):
            for command in self._commands_by_status_unlocked(status):
                if command.dedupe_key == dedupe_key:
                    return command
        return None

    def peek_next(self) -> Command | None:
        with self._locked("peek_next"):
            pending = self._read_jsonl(self.pending_path)
            if not pending:
                return None
            return sorted(pending, key=lambda c: (-(c.priority or 0), c.created_at, c.id))[0]

    def pop_next(self) -> Command | None:
        with self._locked("pop_next"):
            pending = self._read_jsonl(self.pending_path)
            if not pending:
                return None
            next_command = sorted(pending, key=lambda c: (-(c.priority or 0), c.created_at, c.id))[0]
            return self._mark_in_progress_unlocked(next_command.id)

    def pop_by_id(self, command_id: str) -> Command | None:
        return self.mark_in_progress(command_id)

    def mark_in_progress(self, command_id: str) -> Command | None:
        with self._locked("mark_in_progress", command_id):
            return self._mark_in_progress_unlocked(command_id)

    def _mark_in_progress_unlocked(self, command_id: str) -> Command | None:
        if self.get_in_progress() is not None:
            return None
        pending = self._read_jsonl(self.pending_path)
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
        with self._locked("get_in_progress"):
            return self._get_in_progress_unlocked()

    def _get_in_progress_unlocked(self) -> Command | None:
        if not self.in_progress_path.exists():
            return None
        try:
            return Command.model_validate_json(self.in_progress_path.read_text(encoding="utf-8"))
        except (ValidationError, ValueError) as error:
            self._quarantine_malformed_line(
                self.in_progress_path,
                1,
                self.in_progress_path.read_text(encoding="utf-8"),
                error,
            )
            return None

    def complete_in_progress(self) -> Command | None:
        return self.mark_completed()

    def fail_in_progress(self, reason: str) -> Command | None:
        return self.mark_failed(reason)

    def block_in_progress(self, reason: str) -> Command | None:
        return self.mark_blocked(reason)

    def mark_completed(self, command_id: str | None = None) -> Command | None:
        with self._locked("mark_completed", command_id):
            command = self._take_active_command(command_id)
            if command is None:
                return None
            command.status = CommandStatus.COMPLETED
            completed = self._read_jsonl(self.completed_path)
            completed.append(command)
            self._write_jsonl(self.completed_path, completed)
            return command

    def mark_failed(self, reason: str, command_id: str | None = None) -> Command | None:
        with self._locked("mark_failed", command_id):
            command = self._take_active_command(command_id)
            if command is None:
                return None
            command.status = CommandStatus.FAILED
            command.metadata["failure_reason"] = reason
            failed = self._read_jsonl(self.failed_path)
            failed.append(command)
            self._write_jsonl(self.failed_path, failed)
            return command

    def mark_blocked(self, reason: str, command_id: str | None = None) -> Command | None:
        with self._locked("mark_blocked", command_id):
            command = self._take_active_command(command_id)
            if command is None:
                return None
            command.status = CommandStatus.BLOCKED
            command.metadata["blocked_reason"] = reason
            blocked = self._read_jsonl(self.blocked_path)
            blocked.append(command)
            self._write_jsonl(self.blocked_path, blocked)
            return command

    def _take_active_command(self, command_id: str | None) -> Command | None:
        in_progress = self.get_in_progress()
        if in_progress is not None and (command_id is None or in_progress.id == command_id):
            self.in_progress_path.unlink(missing_ok=True)
            return in_progress
        if command_id is None:
            return None
        pending = self.list_pending()
        for index, command in enumerate(pending):
            if command.id != command_id:
                continue
            pending.pop(index)
            self._write_jsonl(self.pending_path, pending)
            return command
        return None
