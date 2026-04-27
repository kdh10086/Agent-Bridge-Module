---
name: agent-bridge
description: Use Agent Bridge to coordinate a PM assistant, local coding agent, GitHub review/CI digests, and owner escalation in any project.
---

# Agent Bridge Skill

## Purpose

Use this skill when a project root contains `.agent-bridge/` or when the user asks to run a PM-agent bridge workflow.

Agent Bridge is project-agnostic.

It can be used for:

- app development repositories,
- research codebases,
- ML experiment repositories,
- infrastructure repositories,
- documentation repositories.

## Core Rule

All command producers enqueue commands.

Only the Dispatcher may send commands to the local coding agent.

Never bypass the queue.

## First Steps in Any Project

1. Check whether `.agent-bridge/` exists.
2. Read `.agent-bridge/README.md`.
3. Read `.agent-bridge/docs/OPERATING_MODEL.md`.
4. Read `.agent-bridge/docs/SAFETY.md`.
5. Run `.agent-bridge/scripts/self_test.sh` before real automation.
6. Do not modify project source code during bridge initialization.

## Report Location

Always write local-agent reports to:

```text
.agent-bridge/workspace/reports/latest_agent_report.md
```

unless the user explicitly configured another workspace.

## Safety

Stop and request owner approval if any of these are involved:

```text
NEEDS_USER_DECISION
APPROVAL_REQUIRED
RISK_HIGH
PAID_API
LICENSE_UNKNOWN
PRIVACY_RISK
MAIN_MERGE
DATA_MIGRATION
ARCHITECTURE_CHANGE
DELETE_OR_REWRITE_LARGE_SCOPE
CI_FAILED_REPEATEDLY
MAX_CYCLE_REACHED
```

## When Asked to Use Agent Bridge

Do not immediately implement product code.

First:

1. initialize or inspect `.agent-bridge/`;
2. run dry-run self-test;
3. verify queue/report/log paths;
4. write a setup report;
5. wait for the next task brief or PM instruction.

## Portable Install Pattern

If `.agent-bridge/` is missing, ask the user to provide or copy the portable module.

If the portable module is present in the current repo, use it without making assumptions about project language or framework.
