# Live Safe-PR Dogfood Protocol

This protocol validates Agent Bridge against a real pull request while keeping all operations read-only and dry-run-first.

## Prerequisites

- GitHub CLI is installed.
- The user has manually completed:

```bash
gh auth login
```

- The owner has selected a safe test PR.
- The PR can be inspected without posting comments, pushing commits, auto-fixing code, merging, or changing project source.

Do not run live GitHub commands if a safe PR is not explicitly available.

## Read-Only Guarantees

The live `gh` adapter may only read:

- PR review threads and review comments;
- PR issue comments;
- PR status check rollups.

It must not:

- post GitHub comments;
- create or mutate PRs;
- push commits;
- auto-fix code;
- auto-merge;
- perform GUI dispatch;
- send Gmail.

All watcher outputs go through `CommandQueue` when non-dry-run ingestion is explicitly requested. Dispatcher remains the only local-agent sender, and real GUI dispatch is not implemented.

## Dry-Run-First Workflow

Replace `OWNER`, `REPO`, and `123` with the safe PR selected by the owner:

```bash
.venv/bin/python -m agent_bridge.cli dogfood-gh --owner OWNER --repo REPO --pr 123 --dry-run
.venv/bin/python -m agent_bridge.cli watch-reviews --owner OWNER --repo REPO --pr 123 --dry-run
.venv/bin/python -m agent_bridge.cli watch-ci --owner OWNER --repo REPO --pr 123 --dry-run
.venv/bin/python -m agent_bridge.cli queue list
.venv/bin/python -m agent_bridge.cli run-loop --dry-run --max-cycles 2 --polling-interval-seconds 1 --no-dispatch
```

Expected dry-run result:

- review and CI data are read through `gh`;
- planned review and CI digest markdown is printed;
- no digest file is written by watcher dry-run commands;
- no queue entry is created by watcher dry-run commands;
- no dispatch is attempted by `dogfood-gh`;
- bounded run-loop stops after the configured limit;
- no GitHub, Gmail, GUI, source-code, commit, push, merge, or auto-fix mutation occurs.

## Optional Queue Ingestion

Only run this section if the owner explicitly confirms the PR is safe and approves queue mutation:

```bash
.venv/bin/python -m agent_bridge.cli watch-reviews --owner OWNER --repo REPO --pr 123
.venv/bin/python -m agent_bridge.cli watch-ci --owner OWNER --repo REPO --pr 123
.venv/bin/python -m agent_bridge.cli queue list
.venv/bin/python -m agent_bridge.cli run-loop --dry-run --max-cycles 2 --polling-interval-seconds 1
```

Expected result:

- watchers may write canonical digest files under `workspace/inbox/`;
- watchers may enqueue `GITHUB_REVIEW_FIX` or `CI_FAILURE_FIX`;
- Dispatcher is still dry-run only;
- no real local-agent GUI dispatch occurs.

## Queue Inspection

Inspect pending commands with:

```bash
.venv/bin/python -m agent_bridge.cli queue list
```

Pending commands should preserve priority ordering:

- `CI_FAILURE_FIX`: priority 80;
- `GITHUB_REVIEW_FIX`: priority 70;
- `CHATGPT_PM_NEXT_TASK`: priority 50.

## Bounded Run-Loop Usage

Use bounded dry-run loops:

```bash
.venv/bin/python -m agent_bridge.cli run-loop --dry-run --max-cycles 2 --polling-interval-seconds 1
.venv/bin/python -m agent_bridge.cli run-loop --dry-run --max-runtime-seconds 60 --polling-interval-seconds 5
```

Use `--no-dispatch` when validating watcher and queue behavior without asking Dispatcher to build prompts:

```bash
.venv/bin/python -m agent_bridge.cli run-loop --dry-run --max-cycles 2 --polling-interval-seconds 1 --no-dispatch
```

## Safety Pause Behavior

If a payload contains a hard-stop keyword, Dispatcher writes:

```text
workspace/inbox/user_decision_request.md
workspace/outbox/owner_decision_email.md
```

and sets `safety_pause: true` in:

```text
workspace/state/state.json
```

The run loop must stop when `safety_pause` is true. Resume only after the owner has reviewed the decision request:

```bash
.venv/bin/python -m agent_bridge.cli resume
```

## Cleanup

Live dogfood may create workspace artifacts. To reset standalone state:

```bash
.venv/bin/python -m agent_bridge.cli init --force
```

Manual cleanup targets, if needed:

```text
workspace/inbox/github_review_digest.md
workspace/inbox/ci_failure_digest.md
workspace/outbox/
workspace/queue/
workspace/logs/bridge.jsonl
workspace/state/state.json
```

Do not delete or modify downstream project source files as part of dogfood cleanup.
