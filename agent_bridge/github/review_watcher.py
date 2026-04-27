from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from agent_bridge.core.command_queue import CommandQueue
from agent_bridge.core.event_log import EventLog
from agent_bridge.core.models import Command, CommandType
from agent_bridge.github.digest_builder import build_review_digest_markdown, parse_review_fixture


CANONICAL_REVIEW_DIGEST = "github_review_digest.md"


def ingest_review_fixture(
    fixture: Path,
    workspace_dir: Path,
    queue: CommandQueue | None = None,
    event_log: EventLog | None = None,
) -> tuple[bool, Command, Path]:
    workspace_dir.mkdir(parents=True, exist_ok=True)
    inbox_dir = workspace_dir / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)

    digest = parse_review_fixture(fixture)
    digest_path = inbox_dir / CANONICAL_REVIEW_DIGEST
    digest_path.write_text(build_review_digest_markdown(digest), encoding="utf-8")

    command = Command(
        id=f"cmd_{uuid4().hex[:12]}",
        type=CommandType.GITHUB_REVIEW_FIX,
        source="github_review_watcher",
        pr_number=digest.pr_number,
        payload_path=str(digest_path),
        requires_user_approval=any(item.requires_user_decision for item in digest.action_items),
        dedupe_key=digest.dedupe_key,
        metadata={
            "digest_source": digest.source,
            "repository": digest.repository,
            "review_id": digest.review_id,
            "raw_source_path": digest.raw_source_path,
        },
    )
    command_queue = queue or CommandQueue(workspace_dir / "queue")
    added = command_queue.enqueue(command)

    log = event_log or EventLog(workspace_dir / "logs" / "bridge.jsonl")
    log.append(
        "github_review_digest_ingested",
        added=added,
        command_id=command.id,
        digest_path=str(digest_path),
        dedupe_key=command.dedupe_key,
    )
    return added, command, digest_path
