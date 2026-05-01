from __future__ import annotations

import errno
import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class PortableQueueLockTimeoutError(TimeoutError):
    pass


@contextmanager
def portable_queue_lock(
    queue_dir: Path,
    *,
    timeout_seconds: float | None = None,
    poll_interval_seconds: float | None = None,
) -> Iterator[None]:
    timeout_seconds = float(os.environ.get("AB_QUEUE_LOCK_TIMEOUT_SECONDS", timeout_seconds or 5.0))
    poll_interval_seconds = float(
        os.environ.get("AB_QUEUE_LOCK_POLL_INTERVAL_SECONDS", poll_interval_seconds or 0.05)
    )
    queue_dir.mkdir(parents=True, exist_ok=True)
    lock_path = queue_dir / "queue.lock"
    deadline = time.monotonic() + timeout_seconds
    handle = lock_path.open("a+", encoding="utf-8")
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError as error:
            if error.errno not in {errno.EACCES, errno.EAGAIN}:
                handle.close()
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                handle.close()
                raise PortableQueueLockTimeoutError(
                    f"Timed out acquiring queue lock at {lock_path}"
                ) from error
            time.sleep(min(poll_interval_seconds, remaining))
    try:
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def command_prompt_path(command: dict) -> str:
    return command.get("prompt_path") or command.get("payload_path") or ""
