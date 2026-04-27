from pathlib import Path

from agent_bridge.core.command_queue import CommandQueue
from agent_bridge.core.models import CommandType
from agent_bridge.github.ci_watcher import ingest_ci_fixture
from agent_bridge.github.review_watcher import ingest_review_fixture


def test_review_watcher_writes_digest_and_enqueues_only(tmp_path: Path):
    workspace = tmp_path / "workspace"

    added, command, digest_path = ingest_review_fixture(
        fixture=Path("fixtures/fake_github_review.json"),
        workspace_dir=workspace,
    )

    pending = CommandQueue(workspace / "queue").list_pending()
    assert added
    assert digest_path == workspace / "inbox" / "github_review_digest.md"
    assert digest_path.exists()
    assert command.type == CommandType.GITHUB_REVIEW_FIX
    assert command.priority == 70
    assert command.payload_path == str(digest_path)
    assert len(pending) == 1
    assert pending[0].status == "pending"
    assert not (workspace / "queue" / "in_progress.json").exists()


def test_ci_watcher_writes_digest_and_enqueues_only(tmp_path: Path):
    workspace = tmp_path / "workspace"

    added, command, digest_path = ingest_ci_fixture(
        fixture=Path("fixtures/fake_ci_failure.json"),
        workspace_dir=workspace,
    )

    pending = CommandQueue(workspace / "queue").list_pending()
    assert added
    assert digest_path == workspace / "inbox" / "ci_failure_digest.md"
    assert digest_path.exists()
    assert command.type == CommandType.CI_FAILURE_FIX
    assert command.priority == 80
    assert command.payload_path == str(digest_path)
    assert len(pending) == 1
    assert pending[0].status == "pending"
    assert not (workspace / "queue" / "in_progress.json").exists()


def test_file_based_watchers_dedupe_repeated_ingest(tmp_path: Path):
    workspace = tmp_path / "workspace"

    first, _, _ = ingest_review_fixture(
        fixture=Path("fixtures/fake_github_review.json"),
        workspace_dir=workspace,
    )
    second, _, _ = ingest_review_fixture(
        fixture=Path("fixtures/fake_github_review.json"),
        workspace_dir=workspace,
    )

    assert first
    assert not second
    assert len(CommandQueue(workspace / "queue").list_pending()) == 1


def test_review_watcher_marks_command_when_user_decision_required(tmp_path: Path):
    fixture = tmp_path / "review_requires_decision.json"
    fixture.write_text(
        """
{
  "source": "review_fixture",
  "review_id": "needs-decision",
  "dedupe_key": "review:needs-decision",
  "action_items": [
    {
      "title": "Confirm risky scope",
      "requires_user_decision": true,
      "suggested_local_agent_action": "Stop and request owner approval."
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )

    _, command, _ = ingest_review_fixture(
        fixture=fixture,
        workspace_dir=tmp_path / "workspace",
    )

    assert command.requires_user_approval
