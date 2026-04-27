from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_bridge.core.command_queue import CommandQueue
from agent_bridge.core.event_log import EventLog
from agent_bridge.github.ci_watcher import watch_ci_failures
from agent_bridge.github.review_watcher import watch_review_comments


Watcher = Callable[..., tuple[bool, Any, Path, Any, str]]


@dataclass(frozen=True)
class GhDogfoodDryRunResult:
    review_markdown: str
    ci_markdown: str
    review_action_items: int
    ci_failures: int
    queue_pending_before: int
    queue_pending_after: int


def dogfood_gh_dry_run(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    workspace_dir: Path,
    queue: CommandQueue | None = None,
    event_log: EventLog | None = None,
    review_watcher: Watcher = watch_review_comments,
    ci_watcher: Watcher = watch_ci_failures,
) -> GhDogfoodDryRunResult:
    if not owner:
        raise ValueError("owner is required.")
    if not repo:
        raise ValueError("repo is required.")
    if pr_number <= 0:
        raise ValueError("pr_number must be a positive integer.")

    command_queue = queue or CommandQueue(workspace_dir / "queue")
    log = event_log or EventLog(workspace_dir / "logs" / "bridge.jsonl")
    pending_before = len(command_queue.list_pending())

    _, _, _, review_digest, review_markdown = review_watcher(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        workspace_dir=workspace_dir,
        dry_run=True,
        queue=command_queue,
        event_log=log,
    )
    _, _, _, ci_digest, ci_markdown = ci_watcher(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        workspace_dir=workspace_dir,
        dry_run=True,
        queue=command_queue,
        event_log=log,
    )

    pending_after = len(command_queue.list_pending())
    log.append(
        "dogfood_gh_dry_run",
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        review_action_items=len(review_digest.action_items),
        ci_failures=len(ci_digest.failures),
        queue_pending_before=pending_before,
        queue_pending_after=pending_after,
    )
    return GhDogfoodDryRunResult(
        review_markdown=review_markdown,
        ci_markdown=ci_markdown,
        review_action_items=len(review_digest.action_items),
        ci_failures=len(ci_digest.failures),
        queue_pending_before=pending_before,
        queue_pending_after=pending_after,
    )
