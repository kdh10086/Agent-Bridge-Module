# Project Brief: Agent Bridge

## Purpose

Agent Bridge is a reusable automation system for coordinating:

- a PM assistant,
- a local coding agent,
- GitHub review comments,
- CI results,
- owner escalation.

It is project-agnostic and must work across arbitrary repositories.

## Two Deployment Modes

### Standalone mode

Agent Bridge is developed and tested as an independent Python project.

### Portable module mode

Agent Bridge is copied into a target project root as:

```text
.agent-bridge/
```

The target repo's Codex agent then uses the Codex Skill to operate the bridge.

## Core Abstraction

```text
Command Producer → Command Queue → Dispatcher → Local Coding Agent
```

## Producers

- PM assistant response
- GitHub review watcher
- CI watcher
- manual owner command
- scheduled status request

## Consumer

- single Dispatcher
