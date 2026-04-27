# .agent-bridge

This folder contains a portable Agent Bridge module for this project.

It is project-agnostic. It can be used in app repos, research repos, infrastructure repos, or documentation repos.

## What This Module Does

- Stores bridge state.
- Stores command queue.
- Stores local agent reports.
- Stores PM assistant instructions.
- Stores GitHub review/CI digests.
- Provides templates and scripts for dry-run operation.

## What This Module Does Not Do

- It does not implement project features.
- It does not modify source code by itself.
- It does not send emails in MVP.
- It does not auto-merge PRs.

## First Command

```bash
bash .agent-bridge/scripts/self_test.sh
```

## Key Rule

Every command goes through the queue.

Only Dispatcher sends commands to the local coding agent.
