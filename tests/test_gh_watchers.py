from __future__ import annotations

from pathlib import Path

from agent_bridge.core.command_queue import CommandQueue
from agent_bridge.core.models import CommandType
from agent_bridge.github.ci_watcher import watch_ci_failures
from agent_bridge.github.digest_builder import (
    build_ci_digest_from_gh_data,
    build_review_digest_from_gh_data,
)
from agent_bridge.github.review_watcher import watch_review_comments


def sample_review_data() -> dict:
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [
                            {
                                "comments": {
                                    "nodes": [
                                        {
                                            "id": "review-comment-1",
                                            "body": "Codex automated review: add a test.",
                                            "path": "tests/test_example.py",
                                            "line": 12,
                                            "author": {
                                                "login": "codex[bot]",
                                                "__typename": "Bot",
                                            },
                                        },
                                        {
                                            "id": "human-comment",
                                            "body": "Human note unrelated to automation.",
                                            "path": "README.md",
                                            "line": 1,
                                            "author": {
                                                "login": "maintainer",
                                                "__typename": "User",
                                            },
                                        },
                                    ]
                                }
                            }
                        ]
                    },
                    "comments": {
                        "nodes": [
                            {
                                "id": "issue-comment-1",
                                "body": "Automated reviewer found another issue.",
                                "author": {
                                    "login": "review-bot",
                                    "__typename": "Bot",
                                },
                            }
                        ]
                    },
                }
            }
        }
    }


def sample_ci_data() -> dict:
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "commits": {
                        "nodes": [
                            {
                                "commit": {
                                    "oid": "abc123",
                                    "statusCheckRollup": {
                                        "contexts": {
                                            "nodes": [
                                                {
                                                    "__typename": "CheckRun",
                                                    "databaseId": 1001,
                                                    "name": "tests",
                                                    "status": "COMPLETED",
                                                    "conclusion": "FAILURE",
                                                    "detailsUrl": "https://example.test/check/1001",
                                                    "checkSuite": {
                                                        "app": {
                                                            "name": "CI",
                                                            "slug": "ci",
                                                        }
                                                    },
                                                },
                                                {
                                                    "__typename": "CheckRun",
                                                    "databaseId": 1002,
                                                    "name": "lint",
                                                    "status": "COMPLETED",
                                                    "conclusion": "SUCCESS",
                                                },
                                                {
                                                    "__typename": "StatusContext",
                                                    "context": "legacy-ci",
                                                    "state": "ERROR",
                                                    "targetUrl": "https://example.test/status",
                                                    "description": "legacy job errored",
                                                },
                                            ]
                                        }
                                    },
                                }
                            }
                        ]
                    }
                }
            }
        }
    }


class FakeGhClient:
    def fetch_pr_review_data(self, *, owner: str, repo: str, pr_number: int) -> dict:
        return sample_review_data()

    def fetch_pr_ci_data(self, *, owner: str, repo: str, pr_number: int) -> dict:
        return sample_ci_data()


def test_review_digest_from_gh_data_filters_automated_comments():
    digest = build_review_digest_from_gh_data(
        sample_review_data(),
        owner="owner",
        repo="repo",
        pr_number=123,
    )

    assert digest.source == "gh_cli"
    assert digest.repository == "owner/repo"
    assert len(digest.action_items) == 2
    assert digest.action_items[0].file == "tests/test_example.py"
    assert "Human note" not in "\n".join(item.original_comment for item in digest.action_items)


def test_ci_digest_from_gh_data_includes_only_failed_or_cancelled_checks():
    digest = build_ci_digest_from_gh_data(
        sample_ci_data(),
        owner="owner",
        repo="repo",
        pr_number=123,
    )

    assert digest.source == "gh_cli"
    assert digest.repository == "owner/repo"
    assert len(digest.failures) == 2
    assert {failure.job_name for failure in digest.failures} == {"tests", "legacy-ci"}


def test_review_digest_dedupes_comments_across_pages():
    data = sample_review_data()
    pull_request = data["data"]["repository"]["pullRequest"]
    pull_request["reviewThreads"]["nodes"].append(
        {
            "comments": {
                "nodes": [
                    {
                        "id": "review-comment-1",
                        "body": "Codex automated review: add a test.",
                        "path": "tests/test_example.py",
                        "line": 12,
                        "author": {
                            "login": "codex[bot]",
                            "__typename": "Bot",
                        },
                    }
                ]
            }
        }
    )
    pull_request["comments"]["nodes"].append(
        {
            "id": "issue-comment-1",
            "body": "Automated reviewer found another issue.",
            "author": {
                "login": "review-bot",
                "__typename": "Bot",
            },
        }
    )

    digest = build_review_digest_from_gh_data(data, owner="owner", repo="repo", pr_number=123)

    assert len(digest.action_items) == 2
    assert digest.review_id == "issue-comment-1,review-comment-1"


def test_ci_digest_dedupes_contexts_across_pages():
    data = sample_ci_data()
    contexts = (
        data["data"]["repository"]["pullRequest"]["commits"]["nodes"][0]["commit"]["statusCheckRollup"][
            "contexts"
        ]["nodes"]
    )
    contexts.append(
        {
            "__typename": "CheckRun",
            "databaseId": 1001,
            "name": "tests",
            "status": "COMPLETED",
            "conclusion": "FAILURE",
            "detailsUrl": "https://example.test/check/1001",
        }
    )

    digest = build_ci_digest_from_gh_data(data, owner="owner", repo="repo", pr_number=123)

    assert len(digest.failures) == 2
    assert digest.check_run_id == "1001,legacy-ci"


def test_watch_reviews_dry_run_does_not_mutate_queue(tmp_path: Path):
    added, command, digest_path, digest, markdown = watch_review_comments(
        owner="owner",
        repo="repo",
        pr_number=123,
        workspace_dir=tmp_path / "workspace",
        dry_run=True,
        gh_client=FakeGhClient(),
    )

    assert not added
    assert command is not None
    assert digest.action_items
    assert "# GitHub Review Digest" in markdown
    assert not digest_path.exists()
    assert not (tmp_path / "workspace" / "queue" / "pending_commands.jsonl").exists()


def test_watch_ci_dry_run_does_not_mutate_queue(tmp_path: Path):
    added, command, digest_path, digest, markdown = watch_ci_failures(
        owner="owner",
        repo="repo",
        pr_number=123,
        workspace_dir=tmp_path / "workspace",
        dry_run=True,
        gh_client=FakeGhClient(),
    )

    assert not added
    assert command is not None
    assert digest.failures
    assert "# CI Failure Digest" in markdown
    assert not digest_path.exists()
    assert not (tmp_path / "workspace" / "queue" / "pending_commands.jsonl").exists()


def test_watch_ci_enqueue_only_and_dedupe(tmp_path: Path):
    workspace = tmp_path / "workspace"

    first_added, first_command, digest_path, digest, _ = watch_ci_failures(
        owner="owner",
        repo="repo",
        pr_number=123,
        workspace_dir=workspace,
        dry_run=False,
        gh_client=FakeGhClient(),
    )
    second_added, _, _, _, _ = watch_ci_failures(
        owner="owner",
        repo="repo",
        pr_number=123,
        workspace_dir=workspace,
        dry_run=False,
        gh_client=FakeGhClient(),
    )

    pending = CommandQueue(workspace / "queue").list_pending()
    assert first_added
    assert not second_added
    assert digest.failures
    assert digest_path.exists()
    assert first_command is not None
    assert first_command.type == CommandType.CI_FAILURE_FIX
    assert len(pending) == 1
    assert pending[0].type == CommandType.CI_FAILURE_FIX
    assert not (workspace / "queue" / "in_progress.json").exists()


def test_watch_reviews_enqueue_only(tmp_path: Path):
    workspace = tmp_path / "workspace"

    added, command, digest_path, digest, _ = watch_review_comments(
        owner="owner",
        repo="repo",
        pr_number=123,
        workspace_dir=workspace,
        dry_run=False,
        gh_client=FakeGhClient(),
    )

    pending = CommandQueue(workspace / "queue").list_pending()
    assert added
    assert digest.action_items
    assert digest_path.exists()
    assert command is not None
    assert command.type == CommandType.GITHUB_REVIEW_FIX
    assert len(pending) == 1
    assert pending[0].type == CommandType.GITHUB_REVIEW_FIX
    assert not (workspace / "queue" / "in_progress.json").exists()
