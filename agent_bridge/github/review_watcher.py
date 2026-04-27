from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from agent_bridge.core.command_queue import CommandQueue
from agent_bridge.core.event_log import EventLog
from agent_bridge.core.models import Command, CommandType, ReviewDigest
from agent_bridge.github.digest_builder import (
    build_review_digest_from_gh_data,
    build_review_digest_markdown,
    parse_review_fixture,
)
from agent_bridge.github.gh_client import GhClient


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


def command_from_review_digest(digest: ReviewDigest, digest_path: Path) -> Command:
    return Command(
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


def watch_review_comments(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    workspace_dir: Path,
    dry_run: bool,
    gh_client: GhClient | None = None,
    queue: CommandQueue | None = None,
    event_log: EventLog | None = None,
) -> tuple[bool, Command | None, Path, ReviewDigest, str]:
    workspace_dir.mkdir(parents=True, exist_ok=True)
    inbox_dir = workspace_dir / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    digest_path = inbox_dir / CANONICAL_REVIEW_DIGEST

    client = gh_client or GhClient()
    data = client.fetch_pr_review_data(owner=owner, repo=repo, pr_number=pr_number)
    digest = build_review_digest_from_gh_data(data, owner=owner, repo=repo, pr_number=pr_number)
    markdown = build_review_digest_markdown(digest)
    command = command_from_review_digest(digest, digest_path) if digest.action_items else None

    log = event_log or EventLog(workspace_dir / "logs" / "bridge.jsonl")
    if dry_run:
        log.append(
            "github_review_watch_dry_run",
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            action_items=len(digest.action_items),
        )
        return False, command, digest_path, digest, markdown

    digest_path.write_text(markdown, encoding="utf-8")
    added = False
    if command is not None:
        command_queue = queue or CommandQueue(workspace_dir / "queue")
        added = command_queue.enqueue(command)
    log.append(
        "github_review_watch_ingested",
        added=added,
        command_id=command.id if command else None,
        digest_path=str(digest_path),
        dedupe_key=command.dedupe_key if command else digest.dedupe_key,
        action_items=len(digest.action_items),
    )
    return added, command, digest_path, digest, markdown
