from pathlib import Path

from agent_bridge.github.digest_builder import (
    build_ci_digest_markdown,
    build_review_digest_markdown,
    parse_ci_fixture,
    parse_review_fixture,
)


def test_parse_review_json_fixture_and_build_markdown():
    digest = parse_review_fixture(Path("fixtures/fake_github_review.json"))
    markdown = build_review_digest_markdown(digest)

    assert digest.repository == "example/repository"
    assert digest.pr_number == 42
    assert digest.dedupe_key == "review:example-repository:42:review-1001"
    assert digest.action_items[0].title == "Add command queue dedupe coverage"
    assert "# GitHub Review Digest" in markdown
    assert "Suggested Local Agent Action" in markdown


def test_parse_ci_json_fixture_and_build_markdown():
    digest = parse_ci_fixture(Path("fixtures/fake_ci_failure.json"))
    markdown = build_ci_digest_markdown(digest)

    assert digest.repository == "example/repository"
    assert digest.pr_number == 42
    assert digest.dedupe_key == "ci:example-repository:42:check-2001"
    assert digest.failures[0].job_name == "test"
    assert "# CI Failure Digest" in markdown
    assert "AssertionError" in markdown


def test_markdown_review_fixture_gets_stable_dedupe_key(tmp_path: Path):
    fixture = tmp_path / "review.md"
    fixture.write_text("# Review\n\nPlease add a focused test.", encoding="utf-8")

    first = parse_review_fixture(fixture)
    second = parse_review_fixture(fixture)

    assert first.dedupe_key == second.dedupe_key
    assert first.action_items[0].original_comment.startswith("# Review")
