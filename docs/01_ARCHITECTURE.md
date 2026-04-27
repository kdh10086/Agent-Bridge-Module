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
