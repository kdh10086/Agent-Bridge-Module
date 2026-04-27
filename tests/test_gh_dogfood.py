from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import agent_bridge.cli as cli_module
from agent_bridge.core.command_queue import CommandQueue
from agent_bridge.core.models import CIDigest, CIFailureItem, ReviewActionItem, ReviewDigest
from agent_bridge.github.dogfood import dogfood_gh_dry_run
from agent_bridge.github.gh_client import GhClientError


def test_dogfood_gh_dry_run_does_not_mutate_queue_or_dispatch(tmp_path: Path):
    workspace = tmp_path / "workspace"
    calls: list[tuple[str, bool]] = []

    def fake_review_watcher(**kwargs):
        calls.append(("review", kwargs["dry_run"]))
        digest = ReviewDigest(
            source="test",
            repository="owner/repo",
            pr_number=123,
            summary="review",
            action_items=[
                ReviewActionItem(
                    title="review item",
                    original_comment="Codex automated review item.",
                )
            ],
            dedupe_key="review:test",
        )
        return False, None, workspace / "inbox" / "github_review_digest.md", digest, "# Review\n"

    def fake_ci_watcher(**kwargs):
        calls.append(("ci", kwargs["dry_run"]))
        digest = CIDigest(
            source="test",
            repository="owner/repo",
            pr_number=123,
            summary="ci",
            failures=[CIFailureItem(job_name="tests", status="failed")],
            dedupe_key="ci:test",
        )
        return False, None, workspace / "inbox" / "ci_failure_digest.md", digest, "# CI\n"

    result = dogfood_gh_dry_run(
        owner="owner",
        repo="repo",
        pr_number=123,
        workspace_dir=workspace,
        review_watcher=fake_review_watcher,
        ci_watcher=fake_ci_watcher,
    )

    assert calls == [("review", True), ("ci", True)]
    assert result.queue_pending_before == 0
    assert result.queue_pending_after == 0
    assert result.review_action_items == 1
    assert result.ci_failures == 1
    assert CommandQueue(workspace / "queue").list_pending() == []
    assert not (workspace / "queue" / "in_progress.json").exists()


@pytest.mark.parametrize(
    ("owner", "repo", "pr_number", "message"),
    [
        ("", "repo", 123, "owner is required"),
        ("owner", "", 123, "repo is required"),
        ("owner", "repo", 0, "positive integer"),
    ],
)
def test_dogfood_gh_dry_run_validates_required_inputs(
    tmp_path: Path,
    owner: str,
    repo: str,
    pr_number: int,
    message: str,
):
    with pytest.raises(ValueError, match=message):
        dogfood_gh_dry_run(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            workspace_dir=tmp_path / "workspace",
        )


def test_dogfood_gh_cli_missing_pr_fails_clearly():
    result = CliRunner().invoke(
        cli_module.app,
        ["dogfood-gh", "--owner", "owner", "--repo", "repo", "--dry-run"],
    )

    assert result.exit_code != 0
    assert "--pr" in result.output


def test_dogfood_gh_cli_rejects_non_dry_run():
    result = CliRunner().invoke(
        cli_module.app,
        ["dogfood-gh", "--owner", "owner", "--repo", "repo", "--pr", "123", "--no-dry-run"],
    )

    assert result.exit_code == 1
    assert "only supports --dry-run" in result.output


def test_dogfood_gh_cli_reports_watcher_errors(monkeypatch: pytest.MonkeyPatch):
    def fake_dogfood(**kwargs):
        raise GhClientError("gh auth failed")

    monkeypatch.setattr(cli_module, "dogfood_gh_dry_run", fake_dogfood)

    result = CliRunner().invoke(
        cli_module.app,
        ["dogfood-gh", "--owner", "owner", "--repo", "repo", "--pr", "123", "--dry-run"],
    )

    assert result.exit_code == 1
    assert "gh auth failed" in result.output
