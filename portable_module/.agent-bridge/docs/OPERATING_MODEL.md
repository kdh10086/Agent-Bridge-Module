# Agent Bridge Operating Model

```text
PM Assistant Bridge ─┐
GitHub Review Watcher├── Command Queue ── Dispatcher ── Local Coding Agent
CI Watcher ──────────┘
```

Producers enqueue commands.

Dispatcher is the only consumer that sends commands to the local coding agent.
