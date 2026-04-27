from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any


class GhClientError(RuntimeError):
    pass


class GhNotFoundError(GhClientError):
    pass


class GhCommandError(GhClientError):
    def __init__(self, args: list[str], returncode: int, stderr: str):
        self.args = args
        self.returncode = returncode
        self.stderr = stderr.strip()
        super().__init__(
            f"gh command failed with exit code {returncode}: gh {' '.join(args)}\n{self.stderr}"
        )


REVIEW_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $cursor) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          id
          isResolved
          comments(first: 100) {
            pageInfo {
              hasNextPage
              endCursor
            }
            nodes {
              id
              databaseId
              body
              path
              line
              originalLine
              createdAt
              url
              author {
                login
                __typename
              }
            }
          }
        }
      }
    }
  }
}
"""


REVIEW_THREAD_COMMENTS_QUERY = """
query($threadId: ID!, $cursor: String) {
  node(id: $threadId) {
    ... on PullRequestReviewThread {
      comments(first: 100, after: $cursor) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          id
          databaseId
          body
          path
          line
          originalLine
          createdAt
          url
          author {
            login
            __typename
          }
        }
      }
    }
  }
}
"""


ISSUE_COMMENTS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      comments(first: 100, after: $cursor) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          id
          databaseId
          body
          createdAt
          url
          author {
            login
            __typename
          }
        }
      }
    }
  }
}
"""


CI_CONTEXTS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      commits(last: 1) {
        nodes {
          commit {
            oid
            statusCheckRollup {
              contexts(first: 100, after: $cursor) {
                pageInfo {
                  hasNextPage
                  endCursor
                }
                nodes {
                  __typename
                  ... on CheckRun {
                    databaseId
                    name
                    status
                    conclusion
                    detailsUrl
                    startedAt
                    completedAt
                    checkSuite {
                      app {
                        name
                        slug
                      }
                    }
                  }
                  ... on StatusContext {
                    context
                    state
                    targetUrl
                    description
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


REVIEW_QUERY = REVIEW_THREADS_QUERY
CI_QUERY = CI_CONTEXTS_QUERY


def _pull_request(data: dict[str, Any]) -> dict[str, Any]:
    return (((data.get("data") or {}).get("repository") or {}).get("pullRequest") or {})


def _page_info(connection: dict[str, Any]) -> dict[str, Any]:
    page_info = connection.get("pageInfo") or {}
    return page_info if isinstance(page_info, dict) else {}


def _nodes(connection: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = connection.get("nodes") or []
    return [node for node in nodes if isinstance(node, dict)]


def _dedupe_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for index, node in enumerate(nodes):
        identity = str(
            node.get("id")
            or node.get("databaseId")
            or node.get("name")
            or node.get("context")
            or node.get("url")
            or node.get("detailsUrl")
            or node.get("targetUrl")
            or index
        )
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(node)
    return deduped


@dataclass(frozen=True)
class GhClient:
    executable: str = "gh"

    def _ensure_available(self) -> None:
        if shutil.which(self.executable) is None:
            raise GhNotFoundError(
                f"`{self.executable}` was not found. Install GitHub CLI and run `gh auth login`."
            )

    def run_json(self, args: list[str]) -> dict[str, Any]:
        self._ensure_available()
        completed = subprocess.run(
            [self.executable, *args],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise GhCommandError(args, completed.returncode, completed.stderr)
        try:
            data = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise GhClientError(f"gh returned non-JSON output: {error}") from error
        if not isinstance(data, dict):
            raise GhClientError("gh returned JSON that was not an object.")
        return data

    def graphql(
        self,
        query: str,
        *,
        owner: str | None = None,
        repo: str | None = None,
        pr_number: int | None = None,
        variables: dict[str, str | int | bool | None] | None = None,
    ) -> dict[str, Any]:
        args = ["api", "graphql"]
        if owner is not None:
            args.extend(["-f", f"owner={owner}"])
        if repo is not None:
            args.extend(["-f", f"name={repo}"])
        if pr_number is not None:
            args.extend(["-F", f"number={pr_number}"])
        for key, value in (variables or {}).items():
            if value is None:
                continue
            flag = "-F" if isinstance(value, (bool, int)) else "-f"
            args.extend([flag, f"{key}={value}"])
        args.extend(["-f", f"query={query}"])
        return self.run_json(args)

    def fetch_pr_review_data(self, *, owner: str, repo: str, pr_number: int) -> dict[str, Any]:
        review_threads = self._fetch_all_review_threads(owner=owner, repo=repo, pr_number=pr_number)
        issue_comments = self._fetch_all_issue_comments(owner=owner, repo=repo, pr_number=pr_number)
        return {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {"nodes": review_threads},
                        "comments": {"nodes": issue_comments},
                    }
                }
            }
        }

    def fetch_pr_ci_data(self, *, owner: str, repo: str, pr_number: int) -> dict[str, Any]:
        contexts, commit_oid = self._fetch_all_ci_contexts(owner=owner, repo=repo, pr_number=pr_number)
        return {
            "data": {
                "repository": {
                    "pullRequest": {
                        "commits": {
                            "nodes": [
                                {
                                    "commit": {
                                        "oid": commit_oid,
                                        "statusCheckRollup": {"contexts": {"nodes": contexts}},
                                    }
                                }
                            ]
                        }
                    }
                }
            }
        }

    def _fetch_all_review_threads(self, *, owner: str, repo: str, pr_number: int) -> list[dict[str, Any]]:
        cursor: str | None = None
        threads: list[dict[str, Any]] = []
        while True:
            data = self.graphql(
                REVIEW_THREADS_QUERY,
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                variables={"cursor": cursor},
            )
            connection = _pull_request(data).get("reviewThreads") or {}
            page_threads = _nodes(connection)
            for thread in page_threads:
                self._expand_review_thread_comments(thread)
            threads.extend(page_threads)

            page_info = _page_info(connection)
            if not page_info.get("hasNextPage"):
                return _dedupe_nodes(threads)
            cursor = page_info.get("endCursor")
            if not cursor:
                raise GhClientError("GitHub reviewThreads pagination did not return an endCursor.")

    def _expand_review_thread_comments(self, thread: dict[str, Any]) -> None:
        comments_connection = thread.get("comments") or {}
        comments = _nodes(comments_connection)
        page_info = _page_info(comments_connection)
        thread_id = thread.get("id")
        cursor = page_info.get("endCursor")

        while page_info.get("hasNextPage"):
            if not thread_id:
                raise GhClientError("GitHub review thread comments require a thread id for pagination.")
            if not cursor:
                raise GhClientError("GitHub review thread comments pagination did not return an endCursor.")
            data = self.graphql(
                REVIEW_THREAD_COMMENTS_QUERY,
                variables={"threadId": str(thread_id), "cursor": str(cursor)},
            )
            connection = (((data.get("data") or {}).get("node") or {}).get("comments") or {})
            comments.extend(_nodes(connection))
            page_info = _page_info(connection)
            cursor = page_info.get("endCursor")

        thread["comments"] = {"nodes": _dedupe_nodes(comments)}

    def _fetch_all_issue_comments(self, *, owner: str, repo: str, pr_number: int) -> list[dict[str, Any]]:
        cursor: str | None = None
        comments: list[dict[str, Any]] = []
        while True:
            data = self.graphql(
                ISSUE_COMMENTS_QUERY,
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                variables={"cursor": cursor},
            )
            connection = _pull_request(data).get("comments") or {}
            comments.extend(_nodes(connection))

            page_info = _page_info(connection)
            if not page_info.get("hasNextPage"):
                return _dedupe_nodes(comments)
            cursor = page_info.get("endCursor")
            if not cursor:
                raise GhClientError("GitHub issue comments pagination did not return an endCursor.")

    def _fetch_all_ci_contexts(self, *, owner: str, repo: str, pr_number: int) -> tuple[list[dict[str, Any]], str | None]:
        cursor: str | None = None
        contexts: list[dict[str, Any]] = []
        commit_oid: str | None = None
        while True:
            data = self.graphql(
                CI_CONTEXTS_QUERY,
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                variables={"cursor": cursor},
            )
            commits = ((_pull_request(data).get("commits") or {}).get("nodes") or [])
            latest_commit = (commits[-1].get("commit") if commits else {}) or {}
            commit_oid = commit_oid or latest_commit.get("oid")
            rollup = latest_commit.get("statusCheckRollup") or {}
            connection = rollup.get("contexts") or {}
            contexts.extend(_nodes(connection))

            page_info = _page_info(connection)
            if not page_info.get("hasNextPage"):
                return _dedupe_nodes(contexts), commit_oid
            cursor = page_info.get("endCursor")
            if not cursor:
                raise GhClientError("GitHub statusCheckRollup contexts pagination did not return an endCursor.")
