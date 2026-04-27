# Detailed TODO

## Core MVP

- [ ] Verify package scaffold.
- [ ] Run tests.
- [ ] Fix failures.
- [ ] Confirm CLI help.
- [ ] Confirm workspace initialization.
- [ ] Confirm command queue behavior.
- [ ] Confirm safety gate.
- [ ] Confirm dry-run dispatcher.
- [ ] Confirm run-once dry-run.

## Portable Module

- [ ] Verify `portable_module/.agent-bridge/`.
- [ ] Verify `AGENTS.agent-bridge.snippet.md`.
- [ ] Verify Codex Skill.
- [ ] Add portable self-test script.
- [ ] Ensure portable files avoid project-specific assumptions.
- [ ] Add instructions for copying into target project.

## Portable Installer

- [ ] Add `install-portable --target`.
- [ ] Add dry-run output.
- [ ] Refuse overwrite unless `--force`.
- [ ] Add optional `--no-include-agents-snippet`.
- [ ] Add `verify-portable --target`.
- [ ] Confirm installed portable self-test works from target root.
- [ ] Confirm installer does not modify target files outside `.agent-bridge/` and optional snippet.

## Dogfooding

- [ ] Simulate local agent report.
- [ ] Simulate PM response.
- [ ] Simulate review digest.
- [ ] Confirm review-fix priority.
- [ ] Simulate risky response.
- [ ] Confirm safety pause.

## Later

- [ ] GUI bridge.
- [ ] GitHub watcher.
- [ ] CI watcher.
- [ ] Gmail draft.

## Continuous Run Loop

- [x] Add bounded `run-loop` command.
- [x] Enforce max cycles.
- [x] Enforce max runtime.
- [x] Enforce polling interval.
- [x] Stop on `safety_pause`.
- [x] Dispatch only through Dispatcher in dry-run mode.
- [x] Log structured run-loop events.
- [ ] GUI bridge.
- [ ] Gmail draft.
