# Architecture

## High-Level Architecture

```text
PM Assistant Bridge ─┐
GitHub Review Watcher├── Command Queue ── Dispatcher ── Local Coding Agent
CI Watcher ──────────┘
                                 │
                                 ▼
                         Agent Report Collector
                                 │
                                 ▼
                           PM Assistant
```

## Module Responsibilities

| Module | Responsibility |
|---|---|
| `core.models` | Command/state models |
| `core.state_store` | File-backed state |
| `core.command_queue` | Priority queue and deduplication |
| `core.safety_gate` | Safety stops |
| `core.event_log` | JSONL audit log |
| `codex.dispatcher` | Sole sender into local coding agent |
| `codex.output_collector` | Collect latest agent report |
| `codex.prompt_builder` | Build prompts from templates |
| `chatgpt.bridge` | PM assistant bridge |
| `github.review_watcher` | Review digest producer |
| `github.ci_watcher` | CI digest producer |
| `gmail.escalation` | Owner escalation producer |

## Key Invariant

No module except Dispatcher may control local coding agent input.

## Run-Loop Orchestrator

The run-loop orchestrator coordinates bounded automation cycles.

It may:

- load and persist bridge state;
- stop on `safety_pause`;
- enforce max-cycle and max-runtime limits;
- wait between cycles using a configured polling interval;
- call producer watchers;
- inspect `CommandQueue`;
- call Dispatcher in dry-run mode.

It must not:

- send commands directly to the local coding agent;
- bypass `CommandQueue`;
- perform GUI automation;
- send Gmail;
- mutate GitHub;
- auto-fix code;
- auto-merge or push commits.

All producers still enqueue commands. Dispatcher remains the sole sender.

## GitHub CLI Adapter

The real GitHub CLI adapter is read-only.

It may use `gh` to read:

- PR review comments;
- PR issue comments;
- PR status check rollups.

It must not:

- write GitHub comments;
- create pull requests;
- commit or push changes;
- auto-fix code;
- dispatch commands to the local coding agent.

GitHub review and CI watchers only write canonical digest files and enqueue commands. Dispatcher remains the only sender.
