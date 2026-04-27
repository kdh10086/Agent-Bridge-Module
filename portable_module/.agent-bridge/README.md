# .agent-bridge

This folder is a portable Agent Bridge module. It can be copied into any project root as `.agent-bridge/`.

Agent Bridge is project-agnostic. Project-specific instructions belong in task briefs, reports, digest files, templates, and configuration, not in the bridge code.

## Install Pattern

From the standalone Agent Bridge repository, dry-run the install first:

```bash
python -m agent_bridge.cli install-portable --target /path/to/project --dry-run
```

Then install:

```bash
python -m agent_bridge.cli install-portable --target /path/to/project
python -m agent_bridge.cli verify-portable --target /path/to/project
```

The installer copies:

```text
.agent-bridge/
AGENTS.agent-bridge.snippet.md
```

`AGENTS.agent-bridge.snippet.md` is copied by default. Use `--no-include-agents-snippet` to skip it.

If `.agent-bridge/` or the snippet already exists, install fails safely unless `--force` is used.

The snippet is a standalone file. To apply it to a project-wide `AGENTS.md`, paste its contents manually or ask Codex to merge it after reviewing the target project's existing instructions.

Install the Codex Skill by copying:

```text
codex_skill/agent-bridge/SKILL.md
```

to:

```text
~/.codex/skills/agent-bridge/SKILL.md
```

Then ask Codex to read:

```text
~/.codex/skills/agent-bridge/SKILL.md
.agent-bridge/README.md
.agent-bridge/docs/OPERATING_MODEL.md
.agent-bridge/docs/SAFETY.md
```

## Core Rule

All producers enqueue commands.

Only the Dispatcher may create a local-agent prompt.

Review and CI ingest scripts must not dispatch directly.

## First Command

Run the portable self-test from the target project root:

```bash
bash .agent-bridge/scripts/self_test.sh
```

The self-test only writes under `.agent-bridge/workspace/`.

## File-Based Review and CI Ingest

Ingest a review digest:

```bash
bash .agent-bridge/scripts/ingest_review.sh path/to/review_digest.md
```

This writes:

```text
.agent-bridge/workspace/inbox/github_review_digest.md
```

and enqueues `GITHUB_REVIEW_FIX` with priority `70`.

Ingest a CI failure digest:

```bash
bash .agent-bridge/scripts/ingest_ci.sh path/to/ci_failure_digest.md
```

This writes:

```text
.agent-bridge/workspace/inbox/ci_failure_digest.md
```

and enqueues `CI_FAILURE_FIX` with priority `80`.

## Queue and Dispatch

List pending commands:

```bash
bash .agent-bridge/scripts/queue_list.sh
```

Generate the next dry-run local-agent prompt:

```bash
bash .agent-bridge/scripts/dispatch_next.sh --dry-run
```

The prompt is written to:

```text
.agent-bridge/workspace/outbox/next_local_agent_prompt.md
```

Real GUI dispatch is not implemented in the portable MVP.

## Reports

Write a simple local-agent report:

```bash
bash .agent-bridge/scripts/write_report.sh "short summary"
```

Reports are written to:

```text
.agent-bridge/workspace/reports/latest_agent_report.md
```

## Safe Files to Edit

Automation may write under:

```text
.agent-bridge/workspace/
```

Humans or agents may edit:

```text
.agent-bridge/config.yaml
.agent-bridge/docs/
.agent-bridge/templates/
```

when changing bridge behavior.

## Files Automation Must Not Modify

Portable bridge scripts must not modify target project source files. They must not edit files outside `.agent-bridge/workspace/` during normal operation.

## What This Module Does Not Do

- It does not implement project features.
- It does not modify source code by itself.
- It does not perform real GitHub polling in the portable MVP.
- It does not call `gh`.
- It does not perform GUI automation.
- It does not send emails.
- It does not run a continuous automation loop.
- It does not auto-merge changes.
