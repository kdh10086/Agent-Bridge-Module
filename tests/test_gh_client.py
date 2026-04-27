from __future__ import annotations

import subprocess

import pytest

import agent_bridge.github.gh_client as gh_client_module
from agent_bridge.github.gh_client import GhClient, GhCommandError, GhNotFoundError


def _arg_value(args: list[str], name: str) -> str | None:
    prefix = f"{name}="
    for arg in args:
        if arg.startswith(prefix):
            return arg.removeprefix(prefix)
    return None


def test_gh_client_reports_missing_gh(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(gh_client_module.shutil, "which", lambda _: None)

    with pytest.raises(GhNotFoundError):
        GhClient().run_json(["api", "graphql"])


def test_gh_client_reports_command_failure(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(gh_client_module.shutil, "which", lambda _: "/usr/bin/gh")

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=2, stdout="", stderr="auth failed")

    monkeypatch.setattr(gh_client_module.subprocess, "run", fake_run)

    with pytest.raises(GhCommandError) as exc:
        GhClient().run_json(["api", "graphql"])

    assert exc.value.returncode == 2
    assert "auth failed" in exc.value.stderr


def test_gh_client_parses_json(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(gh_client_module.shutil, "which", lambda _: "/usr/bin/gh")

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr(gh_client_module.subprocess, "run", fake_run)

    assert GhClient().run_json(["api", "graphql"]) == {"ok": True}


def test_fetch_pr_review_data_paginates_threads_thread_comments_and_issue_comments(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[list[str]] = []

    def fake_run_json(self, args):
        calls.append(args)
        query = _arg_value(args, "query") or ""
        cursor = _arg_value(args, "cursor")
        thread_id = _arg_value(args, "threadId")

        if "reviewThreads(first: 100" in query:
            if cursor is None:
                return {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "reviewThreads": {
                                    "pageInfo": {"hasNextPage": True, "endCursor": "thread-page-2"},
                                    "nodes": [
                                        {
                                            "id": "thread-1",
                                            "comments": {
                                                "pageInfo": {
                                                    "hasNextPage": True,
                                                    "endCursor": "comment-page-2",
                                                },
                                                "nodes": [{"id": "review-comment-1", "body": "first"}],
                                            },
                                        }
                                    ],
                                }
                            }
                        }
                    }
                }
            return {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [
                                    {
                                        "id": "thread-2",
                                        "comments": {
                                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                                            "nodes": [{"id": "review-comment-3", "body": "third"}],
                                        },
                                    }
                                ],
                            }
                        }
                    }
                }
            }

        if "node(id: $threadId)" in query:
            assert thread_id == "thread-1"
            assert cursor == "comment-page-2"
            return {
                "data": {
                    "node": {
                        "comments": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [{"id": "review-comment-2", "body": "second"}],
                        }
                    }
                }
            }

        if "pullRequest(number: $number)" in query and "comments(first: 100" in query:
            if cursor is None:
                return {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "comments": {
                                    "pageInfo": {"hasNextPage": True, "endCursor": "issue-page-2"},
                                    "nodes": [{"id": "issue-comment-1", "body": "first issue"}],
                                }
                            }
                        }
                    }
                }
            return {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "comments": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [{"id": "issue-comment-2", "body": "second issue"}],
                            }
                        }
                    }
                }
            }

        raise AssertionError("unexpected GraphQL query")

    monkeypatch.setattr(GhClient, "run_json", fake_run_json)

    data = GhClient().fetch_pr_review_data(owner="owner", repo="repo", pr_number=123)
    pull_request = data["data"]["repository"]["pullRequest"]
    threads = pull_request["reviewThreads"]["nodes"]
    issue_comments = pull_request["comments"]["nodes"]

    assert len(threads) == 2
    assert [comment["id"] for comment in threads[0]["comments"]["nodes"]] == [
        "review-comment-1",
        "review-comment-2",
    ]
    assert len(issue_comments) == 2
    assert len(calls) == 5


def test_fetch_pr_ci_data_paginates_status_contexts(monkeypatch: pytest.MonkeyPatch):
    def fake_run_json(self, args):
        cursor = _arg_value(args, "cursor")
        if cursor is None:
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
                                                    "pageInfo": {
                                                        "hasNextPage": True,
                                                        "endCursor": "context-page-2",
                                                    },
                                                    "nodes": [
                                                        {
                                                            "__typename": "CheckRun",
                                                            "databaseId": 1,
                                                            "name": "tests",
                                                        }
                                                    ],
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
                                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                                "nodes": [
                                                    {
                                                        "__typename": "StatusContext",
                                                        "context": "legacy-ci",
                                                    }
                                                ],
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

    monkeypatch.setattr(GhClient, "run_json", fake_run_json)

    data = GhClient().fetch_pr_ci_data(owner="owner", repo="repo", pr_number=123)
    contexts = (
        data["data"]["repository"]["pullRequest"]["commits"]["nodes"][0]["commit"]["statusCheckRollup"][
            "contexts"
        ]["nodes"]
    )

    assert data["data"]["repository"]["pullRequest"]["commits"]["nodes"][0]["commit"]["oid"] == "abc123"
    assert [context.get("name") or context.get("context") for context in contexts] == ["tests", "legacy-ci"]
