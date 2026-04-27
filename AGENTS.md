# AGENTS.md

## Role

You are implementing **Agent Bridge**, a standalone and general-purpose automation project.

Agent Bridge must be usable in two modes:

1. **Standalone mode**: developed and tested as its own repository.
2. **Portable module mode**: copied into any target project root as `.agent-bridge/`.

The target project may be an app, research codebase, infrastructure repo, paper experiment repo, library, or documentation repo.

## Non-Negotiable Rules

1. Do not implement a downstream app or research project.
2. Do not hard-code a target product, repository, language, framework, or research topic.
3. Keep Agent Bridge generic and project-agnostic.
4. All project-specific behavior must come from config, task briefs, reports, templates, and adapters.
5. Watchers must never paste directly into the local coding agent.
6. PM bridge must never paste directly into the local coding agent.
7. All producers enqueue commands.
8. Only the Dispatcher may send commands to the local coding agent.
9. Every state transition must be persisted.
10. Every automated action must be logged.
11. Dry-run mode is mandatory.
12. Max-cycle and max-runtime limits are mandatory.
13. Important decisions must pause the loop.
14. File-based core logic comes before GUI automation.
15. Portable module support is a first-class requirement.
16. Tests must be added before expanding automation.

## Portable Module Requirement

The final design must allow this:

```text
target-project/
  .agent-bridge/
    README.md
    config.yaml
    workspace/
    templates/
    scripts/
    docs/
```

Codex must be able to use the bridge after reading:

```text
~/.codex/skills/agent-bridge/SKILL.md
target-project/.agent-bridge/README.md
```

## Producer-Queue-Dispatcher Rule

```text
PM Assistant Bridge ─┐
GitHub Review Watcher├── Command Queue ── Dispatcher ── Local Coding Agent
CI Watcher ──────────┘
```

Only the Dispatcher may send commands to the local coding agent.

## Safety Stop Keywords

Block dispatch on:

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

When blocked:

1. set `safety_pause = true`;
2. write `.agent-bridge/workspace/inbox/user_decision_request.md` or `workspace/inbox/user_decision_request.md`;
3. write `.agent-bridge/workspace/outbox/owner_decision_email.md` or `workspace/outbox/owner_decision_email.md`;
4. stop the automation loop.
