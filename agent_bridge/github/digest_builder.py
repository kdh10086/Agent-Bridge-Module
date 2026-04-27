from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agent_bridge.core.models import (
    CIDigest,
    CIFailureItem,
    ReviewActionItem,
    ReviewDigest,
    utc_now_iso,
)


def _stable_key(prefix: str, *parts: object) -> str:
    text = "|".join("" if part is None else str(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(text.encode('utf-8')).hexdigest()[:24]}"


AUTOMATED_REVIEW_MARKERS = [
    "bot",
    "app",
    "github-actions",
    "copilot",
    "codex",
    "openai",
    "automated",
    "reviewer",
]

FAILED_CI_STATES = {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"}


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _author_login(node: dict[str, Any]) -> str:
    author = node.get("author") or {}
    return str(author.get("login") or "")


def _author_type(node: dict[str, Any]) -> str:
    author = node.get("author") or {}
    return str(author.get("__typename") or "")


def is_likely_automated_review_comment(node: dict[str, Any]) -> bool:
    text = " ".join(
        [
            _author_login(node),
            _author_type(node),
            str(node.get("body") or ""),
        ]
    ).lower()
    return any(marker in text for marker in AUTOMATED_REVIEW_MARKERS)


def _review_comment_identity(node: dict[str, Any]) -> str:
    return str(
        node.get("id")
        or node.get("databaseId")
        or "|".join(
            [
                _author_login(node),
                str(node.get("path") or ""),
                str(node.get("line") or node.get("originalLine") or ""),
                str(node.get("body") or ""),
            ]
        )
    )


def _first_text(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        text = line.strip().lstrip("#").strip()
        if text:
            return text
    return fallback


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in fixture: {path}")
    return data


def _review_action_from_mapping(data: dict[str, Any]) -> ReviewActionItem:
    return ReviewActionItem(
        title=str(data.get("title") or data.get("summary") or "Review action item"),
        severity=str(data.get("severity") or "unknown"),
        file=data.get("file"),
        line=_as_int(data.get("line")),
        original_comment=str(data.get("original_comment") or data.get("comment") or ""),
        suggested_local_agent_action=str(
            data.get("suggested_local_agent_action")
            or data.get("suggested_action")
            or data.get("action")
            or ""
        ),
        requires_user_decision=_as_bool(data.get("requires_user_decision", False)),
    )


def _ci_failure_from_mapping(data: dict[str, Any]) -> CIFailureItem:
    return CIFailureItem(
        job_name=str(data.get("job_name") or data.get("job") or "CI job"),
        step_name=data.get("step_name") or data.get("step"),
        status=str(data.get("status") or "failed"),
        error_excerpt=str(data.get("error_excerpt") or data.get("error") or ""),
        suspected_cause=str(data.get("suspected_cause") or data.get("cause") or ""),
        suggested_local_agent_action=str(
            data.get("suggested_local_agent_action")
            or data.get("suggested_action")
            or data.get("action")
            or ""
        ),
        requires_user_decision=_as_bool(data.get("requires_user_decision", False)),
    )


def parse_review_fixture(path: Path) -> ReviewDigest:
    path = path.expanduser()
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = _load_json(path)
        items = data.get("action_items") or data.get("comments") or data.get("items") or []
        if not isinstance(items, list):
            raise ValueError("Review fixture action_items/comments/items must be a list.")
        source = str(data.get("source") or "file_fixture")
        repository = data.get("repository")
        pr_number = _as_int(data.get("pr_number"))
        review_id = data.get("review_id")
        summary = str(data.get("summary") or "")
        raw_source_path = str(path)
        dedupe_key = str(
            data.get("dedupe_key")
            or _stable_key("review", source, repository, pr_number, review_id, summary)
        )
        return ReviewDigest(
            source=source,
            repository=repository,
            pr_number=pr_number,
            review_id=review_id,
            detected_at=str(data.get("detected_at") or utc_now_iso()),
            summary=summary,
            action_items=[_review_action_from_mapping(item) for item in items],
            raw_source_path=raw_source_path,
            dedupe_key=dedupe_key,
        )

    markdown = path.read_text(encoding="utf-8")
    return ReviewDigest(
        source="markdown_fixture",
        review_id=path.stem,
        summary=_first_text(markdown, "Review digest from markdown fixture."),
        action_items=[
            ReviewActionItem(
                title="Review digest from markdown fixture",
                severity="unknown",
                original_comment=markdown,
                suggested_local_agent_action="Review the digest and address applicable comments.",
            )
        ],
        raw_source_path=str(path),
        dedupe_key=_stable_key("review", path.name, markdown),
    )


def build_review_digest_from_gh_data(
    data: dict[str, Any],
    *,
    owner: str,
    repo: str,
    pr_number: int,
) -> ReviewDigest:
    repository = f"{owner}/{repo}"
    pull_request = (((data.get("data") or {}).get("repository") or {}).get("pullRequest") or {})
    action_items: list[ReviewActionItem] = []
    comment_ids: list[str] = []
    seen_comments: set[str] = set()

    review_threads = ((pull_request.get("reviewThreads") or {}).get("nodes") or [])
    for thread in review_threads:
        comments = ((thread.get("comments") or {}).get("nodes") or [])
        for comment in comments:
            if not isinstance(comment, dict) or not is_likely_automated_review_comment(comment):
                continue
            identity = _review_comment_identity(comment)
            if identity in seen_comments:
                continue
            seen_comments.add(identity)
            comment_id = str(comment.get("id") or comment.get("databaseId") or identity)
            if comment_id:
                comment_ids.append(comment_id)
            line = _as_int(comment.get("line")) or _as_int(comment.get("originalLine"))
            path = comment.get("path")
            title = f"Automated review comment from {_author_login(comment) or 'unknown author'}"
            if path:
                title = f"{title} on {path}"
            action_items.append(
                ReviewActionItem(
                    title=title,
                    severity="medium",
                    file=path,
                    line=line,
                    original_comment=str(comment.get("body") or ""),
                    suggested_local_agent_action=(
                        "Review this automated PR comment and make the smallest relevant code or test "
                        "change, unless owner approval is required."
                    ),
                )
            )

    issue_comments = ((pull_request.get("comments") or {}).get("nodes") or [])
    for comment in issue_comments:
        if not isinstance(comment, dict) or not is_likely_automated_review_comment(comment):
            continue
        identity = _review_comment_identity(comment)
        if identity in seen_comments:
            continue
        seen_comments.add(identity)
        comment_id = str(comment.get("id") or comment.get("databaseId") or identity)
        if comment_id:
            comment_ids.append(comment_id)
        action_items.append(
            ReviewActionItem(
                title=f"Automated PR comment from {_author_login(comment) or 'unknown author'}",
                severity="medium",
                original_comment=str(comment.get("body") or ""),
                suggested_local_agent_action=(
                    "Review this automated PR comment and make the smallest relevant code or test "
                    "change, unless owner approval is required."
                ),
            )
        )

    summary = (
        f"Found {len(action_items)} likely automated review or issue comments for {repository} PR #{pr_number}."
    )
    dedupe_key = _stable_key("gh_review", repository, pr_number, ",".join(sorted(comment_ids)))
    return ReviewDigest(
        source="gh_cli",
        repository=repository,
        pr_number=pr_number,
        review_id=",".join(sorted(comment_ids)) or None,
        summary=summary,
        action_items=action_items,
        raw_source_path=f"gh://{repository}/pull/{pr_number}/reviews",
        dedupe_key=dedupe_key,
    )


def parse_ci_fixture(path: Path) -> CIDigest:
    path = path.expanduser()
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = _load_json(path)
        failures = data.get("failures") or data.get("failure_items") or data.get("jobs") or []
        if not isinstance(failures, list):
            raise ValueError("CI fixture failures/failure_items/jobs must be a list.")
        source = str(data.get("source") or "file_fixture")
        repository = data.get("repository")
        pr_number = _as_int(data.get("pr_number"))
        check_run_id = data.get("check_run_id")
        summary = str(data.get("summary") or "")
        raw_source_path = str(path)
        dedupe_key = str(
            data.get("dedupe_key")
            or _stable_key("ci", source, repository, pr_number, check_run_id, summary)
        )
        return CIDigest(
            source=source,
            repository=repository,
            pr_number=pr_number,
            check_run_id=check_run_id,
            detected_at=str(data.get("detected_at") or utc_now_iso()),
            summary=summary,
            failures=[_ci_failure_from_mapping(item) for item in failures],
            raw_source_path=raw_source_path,
            dedupe_key=dedupe_key,
        )

    markdown = path.read_text(encoding="utf-8")
    return CIDigest(
        source="markdown_fixture",
        check_run_id=path.stem,
        summary=_first_text(markdown, "CI failure digest from markdown fixture."),
        failures=[
            CIFailureItem(
                job_name="CI job",
                status="failed",
                error_excerpt=markdown,
                suggested_local_agent_action="Review the digest and fix the failing check.",
            )
        ],
        raw_source_path=str(path),
        dedupe_key=_stable_key("ci", path.name, markdown),
    )


def _ci_identity(node: dict[str, Any]) -> str:
    return str(
        node.get("databaseId")
        or node.get("name")
        or node.get("context")
        or node.get("detailsUrl")
        or node.get("targetUrl")
        or ""
    )


def build_ci_digest_from_gh_data(
    data: dict[str, Any],
    *,
    owner: str,
    repo: str,
    pr_number: int,
) -> CIDigest:
    repository = f"{owner}/{repo}"
    pull_request = (((data.get("data") or {}).get("repository") or {}).get("pullRequest") or {})
    commits = ((pull_request.get("commits") or {}).get("nodes") or [])
    latest_commit = (commits[-1].get("commit") if commits else {}) or {}
    rollup = latest_commit.get("statusCheckRollup") or {}
    contexts = ((rollup.get("contexts") or {}).get("nodes") or [])

    failures: list[CIFailureItem] = []
    failed_ids: list[str] = []
    seen_contexts: set[str] = set()
    for context in contexts:
        if not isinstance(context, dict):
            continue
        identity = _ci_identity(context)
        typename = context.get("__typename")
        if typename == "CheckRun":
            conclusion = str(context.get("conclusion") or context.get("status") or "").upper()
            if conclusion not in FAILED_CI_STATES:
                continue
            if identity in seen_contexts:
                continue
            seen_contexts.add(identity)
            failed_ids.append(identity)
            app = ((context.get("checkSuite") or {}).get("app") or {}).get("name")
            job_name = str(context.get("name") or "CI check")
            failures.append(
                CIFailureItem(
                    job_name=job_name,
                    step_name=str(app) if app else None,
                    status=conclusion.lower(),
                    error_excerpt=str(context.get("detailsUrl") or "No details URL provided."),
                    suspected_cause="GitHub reported this check as failed or cancelled.",
                    suggested_local_agent_action=(
                        "Inspect the failing check output and make the smallest relevant code or test "
                        "change, unless owner approval is required."
                    ),
                )
            )
        elif typename == "StatusContext":
            state = str(context.get("state") or "").upper()
            if state not in FAILED_CI_STATES:
                continue
            if identity in seen_contexts:
                continue
            seen_contexts.add(identity)
            failed_ids.append(identity)
            failures.append(
                CIFailureItem(
                    job_name=str(context.get("context") or "CI status"),
                    status=state.lower(),
                    error_excerpt=str(context.get("description") or context.get("targetUrl") or ""),
                    suspected_cause="GitHub reported this status context as failed or cancelled.",
                    suggested_local_agent_action=(
                        "Inspect the failing status output and make the smallest relevant code or test "
                        "change, unless owner approval is required."
                    ),
                )
            )

    summary = f"Found {len(failures)} failed or cancelled CI checks for {repository} PR #{pr_number}."
    check_run_id = ",".join(sorted(filter(None, failed_ids))) or None
    return CIDigest(
        source="gh_cli",
        repository=repository,
        pr_number=pr_number,
        check_run_id=check_run_id,
        summary=summary,
        failures=failures,
        raw_source_path=f"gh://{repository}/pull/{pr_number}/checks",
        dedupe_key=_stable_key("gh_ci", repository, pr_number, check_run_id),
    )


def build_review_digest_markdown(digest: ReviewDigest) -> str:
    lines = [
        "# GitHub Review Digest",
        "",
        "## Metadata",
        "",
        f"- Source: {digest.source}",
        f"- Repository: {digest.repository or 'unspecified'}",
        f"- PR Number: {digest.pr_number if digest.pr_number is not None else 'unspecified'}",
        f"- Review ID: {digest.review_id or 'unspecified'}",
        f"- Detected At: {digest.detected_at}",
        f"- Raw Source Path: {digest.raw_source_path or 'unspecified'}",
        f"- Dedupe Key: {digest.dedupe_key}",
        "",
        "## Summary",
        "",
        digest.summary or "No summary provided.",
        "",
        "## Action Items",
        "",
    ]
    if not digest.action_items:
        lines.append("No review action items were provided.")
    for index, item in enumerate(digest.action_items, start=1):
        lines.extend(
            [
                f"### {index}. {item.title}",
                "",
                f"- Severity: {item.severity}",
                f"- File: {item.file or 'unspecified'}",
                f"- Line: {item.line if item.line is not None else 'unspecified'}",
                f"- Requires User Decision: {'yes' if item.requires_user_decision else 'no'}",
                "",
                "Original Comment:",
                "",
                item.original_comment or "No original comment provided.",
                "",
                "Suggested Local Agent Action:",
                "",
                item.suggested_local_agent_action or "No suggested action provided.",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def build_ci_digest_markdown(digest: CIDigest) -> str:
    lines = [
        "# CI Failure Digest",
        "",
        "## Metadata",
        "",
        f"- Source: {digest.source}",
        f"- Repository: {digest.repository or 'unspecified'}",
        f"- PR Number: {digest.pr_number if digest.pr_number is not None else 'unspecified'}",
        f"- Check Run ID: {digest.check_run_id or 'unspecified'}",
        f"- Detected At: {digest.detected_at}",
        f"- Raw Source Path: {digest.raw_source_path or 'unspecified'}",
        f"- Dedupe Key: {digest.dedupe_key}",
        "",
        "## Summary",
        "",
        digest.summary or "No summary provided.",
        "",
        "## Failures",
        "",
    ]
    if not digest.failures:
        lines.append("No CI failures were provided.")
    for index, failure in enumerate(digest.failures, start=1):
        lines.extend(
            [
                f"### {index}. {failure.job_name}",
                "",
                f"- Step: {failure.step_name or 'unspecified'}",
                f"- Status: {failure.status}",
                f"- Requires User Decision: {'yes' if failure.requires_user_decision else 'no'}",
                "",
                "Error Excerpt:",
                "",
                failure.error_excerpt or "No error excerpt provided.",
                "",
                "Suspected Cause:",
                "",
                failure.suspected_cause or "No suspected cause provided.",
                "",
                "Suggested Local Agent Action:",
                "",
                failure.suggested_local_agent_action or "No suggested action provided.",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
