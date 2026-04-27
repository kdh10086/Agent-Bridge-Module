# Implementation Plan

## Milestone 1: File-Based Core

- Pydantic models
- StateStore
- CommandQueue
- SafetyGate
- EventLog
- PromptBuilder
- Dry-run Dispatcher
- ReportCollector
- run-once dry-run
- Tests

## Milestone 2: Portable Module Packaging

- Generate `.agent-bridge/` template
- Add `AGENTS.agent-bridge.snippet.md`
- Add portable self-test scripts
- Add Codex Skill
- Add install/copy instructions

## Milestone 3: Portable Installer CLI

- dry-run install plan
- safe target validation
- copy `.agent-bridge/`
- optionally copy `AGENTS.agent-bridge.snippet.md`
- block overwrite unless `--force`
- verify installed portable module
- tests

## Milestone 4: Self-Dogfooding

- Fake agent report
- Fake PM response
- Fake review digest
- Queue priority check
- Safety pause check
- Self-test report

## Milestone 5: GUI Bridge

- Clipboard adapter
- macOS activation adapter
- local-agent UI adapter
- PM-assistant UI adapter
- dry-run-first real mode

## Milestone 6: Read-Only GitHub CLI Adapter

- `gh` CLI read-only client
- PR review comment fetch
- PR issue comment fetch
- PR status check rollup fetch
- digest creation
- queue integration
- dry-run without queue mutation
- mocked tests with no live GitHub dependency

## Later: GitHub Watchers

- real provider polling loops
- pagination expansion
- retry and backoff policy
- rate limit handling

## Milestone 7: Escalation

- user decision request markdown
- owner email markdown
- optional Gmail draft later

## Milestone 8: Continuous Loop

- max cycles
- max runtime
- polling interval
- pause/resume
- safety pause stop
- dry-run Dispatcher only
- structured run-loop event logging
- recovery

## Later: GUI and Owner Channels

- GUI bridge adapter for PM assistant and local coding agent
- Gmail draft integration
- live safe-PR dogfood with bounded run-loop
