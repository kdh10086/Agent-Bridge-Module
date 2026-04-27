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

## Milestone 3: Self-Dogfooding

- Fake agent report
- Fake PM response
- Fake review digest
- Queue priority check
- Safety pause check
- Self-test report

## Milestone 4: GUI Bridge

- Clipboard adapter
- macOS activation adapter
- local-agent UI adapter
- PM-assistant UI adapter
- dry-run-first real mode

## Milestone 5: GitHub Watchers

- `gh` CLI wrapper
- PR review fetch
- PR comment fetch
- CI check fetch
- Digest creation
- Queue integration

## Milestone 6: Escalation

- user decision request markdown
- owner email markdown
- optional Gmail draft later

## Milestone 7: Continuous Loop

- max cycles
- max runtime
- pause/resume
- recovery
