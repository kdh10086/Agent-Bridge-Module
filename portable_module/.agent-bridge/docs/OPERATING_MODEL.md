# Agent Bridge Operating Model

```text
PM Assistant Bridge ─┐
GitHub Review Watcher├── Command Queue ── Dispatcher ── Local Coding Agent
CI Watcher ──────────┘
```

## Invariant

All producers enqueue commands.

Only the Dispatcher may create a local-agent prompt.

## Portable Producer Scripts

These scripts are producers only:

```bash
bash .agent-bridge/scripts/ingest_review.sh path/to/review_digest.md
bash .agent-bridge/scripts/ingest_ci.sh path/to/ci_failure_digest.md
```

They write canonical digest files under `.agent-bridge/workspace/inbox/` and append pending commands to:

```text
.agent-bridge/workspace/queue/pending_commands.jsonl
```

They do not dispatch.

## Portable Dispatcher Script

This is the only portable script that creates a local-agent prompt:

```bash
bash .agent-bridge/scripts/dispatch_next.sh --dry-run
```

It writes:

```text
.agent-bridge/workspace/outbox/next_local_agent_prompt.md
```

Real GUI dispatch is intentionally not implemented in the portable MVP.

## Queue Priorities

```text
CI_FAILURE_FIX       80
GITHUB_REVIEW_FIX   70
CHATGPT_PM_NEXT_TASK 50
REQUEST_STATUS_REPORT 40
```

Higher priority commands are selected first.

## State and Logs

Portable scripts write state and logs under:

```text
.agent-bridge/workspace/state/
.agent-bridge/workspace/logs/
```

No portable script should modify target source files during bridge setup or dry-run operation.
