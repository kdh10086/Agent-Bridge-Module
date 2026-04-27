---
name: agent-bridge
description: Use Agent Bridge to coordinate a PM assistant, local coding agent, review/CI digests, and owner escalation in any project.
---

# Agent Bridge Skill

## Purpose

Use this skill when a project root contains `.agent-bridge/` or when the user asks to run an Agent Bridge workflow.

Agent Bridge is project-agnostic. It can be used in application repositories, research codebases, experiment repositories, infrastructure repositories, libraries, or documentation-heavy projects.

## Core Rule

All command producers enqueue commands.

Only the Dispatcher may create or send a local-agent prompt.

Never bypass the queue.

## First Steps in Any Project

1. Check whether `.agent-bridge/` exists.
2. Read `.agent-bridge/README.md`.
3. Read `.agent-bridge/docs/OPERATING_MODEL.md`.
4. Read `.agent-bridge/docs/SAFETY.md`.
5. Run `.agent-bridge/scripts/self_test.sh` before real bridge operation.
6. Do not modify project source code during bridge initialization.

## Portable Commands

Run self-test:

```bash
bash .agent-bridge/scripts/self_test.sh
```

Ingest a review digest without dispatching:

```bash
bash .agent-bridge/scripts/ingest_review.sh path/to/review_digest.md
```

Ingest a CI failure digest without dispatching:

```bash
bash .agent-bridge/scripts/ingest_ci.sh path/to/ci_failure_digest.md
```

List queued commands:

```bash
bash .agent-bridge/scripts/queue_list.sh
```

Create the next dry-run local-agent prompt:

```bash
bash .agent-bridge/scripts/dispatch_next.sh --dry-run
```

Write a report:

```bash
bash .agent-bridge/scripts/write_report.sh "short summary"
```

## Standalone GitHub CLI Watchers

From the standalone Agent Bridge repository, live GitHub ingestion uses `gh` in read-only mode:

```bash
gh auth login
python -m agent_bridge.cli dogfood-gh --owner OWNER --repo REPO --pr 123 --dry-run
python -m agent_bridge.cli watch-reviews --owner OWNER --repo REPO --pr 123 --dry-run
python -m agent_bridge.cli watch-ci --owner OWNER --repo REPO --pr 123 --dry-run
```

Only run live commands against a safe PR. These watchers must not comment, create PRs, commit, push, auto-fix, or dispatch. They may only read GitHub data, create digest files, and enqueue commands in non-dry-run mode. Dispatcher remains the only sender.

The standalone `gh` adapter paginates review threads, review thread comments, PR issue comments, and status check rollup contexts beyond the first 100 nodes. Keep live dogfood dry-run-first. If no safe PR is explicitly available, do not run live GitHub commands; rely on mocked tests and report that live verification was skipped.

For the full live safe-PR dogfood procedure, read:

```text
docs/LIVE_SAFE_PR_DOGFOOD.md
```

`dogfood-gh` is dry-run only in this milestone. It prints planned review and CI digests, does not write canonical digest files, does not enqueue commands, and does not dispatch.

## Standalone Bounded Run Loop

From the standalone Agent Bridge repository, run-loop orchestration is bounded and dry-run-first:

```bash
python -m agent_bridge.cli run-loop --dry-run --max-cycles 3 --polling-interval-seconds 5
python -m agent_bridge.cli run-loop --dry-run --max-runtime-seconds 60
```

The run loop may inspect queue state, optionally call watcher producers, and ask Dispatcher to build a dry-run local-agent prompt. It must stop on `safety_pause`, max cycles, or max runtime. It must not perform real GUI automation, Gmail sending, GitHub mutation, auto-fix, auto-merge, commit, push, or direct local-agent control.

## Standalone GUI Stage-Only Bridge

From the standalone Agent Bridge repository, GUI bridge support is stage-only:

```bash
python -m agent_bridge.cli show-gui-targets
python -m agent_bridge.cli preflight-gui-apps --dry-run
python -m agent_bridge.cli list-gui-apps
python -m agent_bridge.cli diagnose-gui-app --app-path /Applications/ChatGPT.app
python -m agent_bridge.cli diagnose-gui-app --app-path /Applications/Codex.app
python -m agent_bridge.cli diagnose-gui-apps
python -m agent_bridge.cli preflight-external-runner
python -m agent_bridge.cli stage-pm-prompt --dry-run
python -m agent_bridge.cli stage-local-agent-prompt --dry-run
python -m agent_bridge.cli stage-pm-prompt --copy-to-clipboard
python -m agent_bridge.cli stage-local-agent-prompt --copy-to-clipboard
python -m agent_bridge.cli dispatch-next --stage-only
python -m agent_bridge.cli dispatch-next --copy-to-clipboard
python -m agent_bridge.cli dispatch-next --copy-to-clipboard --activate-app
python -m agent_bridge.cli dispatch-next --copy-to-clipboard --confirmation-mode terminal-window
python -m agent_bridge.cli dispatch-next --copy-to-clipboard --confirmation-mode inline
```

The commands write staged prompts to `workspace/outbox/`. Clipboard copy requires explicit manual confirmation. Dry-run never copies. Do not press Enter, submit messages, or perform autonomous GUI automation.

GUI target metadata comes from `config/default.yaml` with optional `config/local.yaml` overrides. Use `list-gui-apps` to inspect likely app names and paths, then set local overrides when needed:

```yaml
apps:
  pm_assistant:
    app_name: "ChatGPT"
    app_path: null
    bundle_id: null
    window_hint: "ChatGPT"
  local_agent:
    app_name: "Codex"
    app_path: null
    bundle_id: null
    window_hint: "Agent Bridge"
```

Use `preflight-gui-apps --dry-run` to inspect the activation plan. Use `preflight-gui-apps --pm-app "ChatGPT" --activate` and `preflight-gui-apps --local-agent-app "Codex" --activate` before any live GUI handoff. Activation preflight must not paste, submit, press Enter/Return, mutate GitHub, or send Gmail.

If activation fails, diagnose the bundle before retrying:

```bash
python -m agent_bridge.cli diagnose-gui-app --app-path /Applications/ChatGPT.app
python -m agent_bridge.cli diagnose-gui-app --app-path /Applications/Codex.app
python -m agent_bridge.cli diagnose-gui-apps
```

Diagnostics inspect `Contents/Info.plist`, `Contents/MacOS`, `CFBundleExecutable`, bundle identifiers, LaunchServices visibility, executable permissions, and process context. Use the suggested config block to update `config/local.yaml`. Add `--activate` only for explicit activation attempts; diagnostics must still not paste or submit.

GUI automation should run from a normal macOS Terminal, not from an active Codex task process. Codex may run:

```bash
python -m agent_bridge.cli preflight-external-runner
```

If Codex sandbox markers are present, do not run GUI automation from Codex. Tell the owner to run this manually from Terminal:

```bash
bash scripts/run_gui_roundtrip_external.sh
```

The external runner shares the same repository workspace, refuses sandboxed execution, runs activation preflights, and only then starts the bounded one-cycle roundtrip.

For local-agent handoff, prefer `dispatch-next --stage-only` first. It writes the next prompt without consuming the queue. Use `dispatch-next --copy-to-clipboard` only in a supervised session after reviewing the staged prompt. `--activate-app` asks separately before focusing the configured local-agent app. These commands still do not paste or submit.

Real local-agent side-effect confirmation defaults to a visible new Terminal window on macOS. The Terminal confirmation shows the action summary, target app/window hint, prompt path, what will happen, and what will not happen. The owner must type yes or no. Closing the window or timing out cancels safely. Use `--confirmation-mode inline` only as a fallback.

## Owner-Approved GUI Dogfood

Only run unattended GUI bridge dogfood when the owner explicitly asks for it and includes `--auto-confirm`:

```bash
python -m agent_bridge.cli dogfood-gui-bridge \
  --auto-confirm \
  --max-cycles 1 \
  --max-runtime-seconds 120
```

This bounded dogfood may activate the configured PM assistant and local-agent apps, copy/paste prompt text, and submit through the focused app. SafetyGate still runs before each submit. It must not mutate GitHub, send Gmail, push commits, auto-merge, edit downstream source files, bypass CommandQueue, or run without max-cycle and max-runtime bounds.

For a stricter one-cycle report-to-PM-to-Codex roundtrip, use only when the owner explicitly approves unattended GUI handoff:

```bash
python -m agent_bridge.cli dogfood-report-roundtrip \
  --auto-confirm \
  --max-cycles 1 \
  --max-runtime-seconds 180
```

This sends the full latest report to the PM assistant, requires exactly one `CODEX_NEXT_PROMPT` fenced block in the PM response, extracts that block, enqueues it, and uses Dispatcher to stage the local-agent prompt before submitting it to Codex. SafetyGate still runs before both submits.

Run a live report roundtrip only after PM assistant and local-agent activation preflight both pass. If activation fails, stop and update `config/local.yaml` with the correct app name, app path, or bundle id instead of attempting paste or submit.
Do not retry the full roundtrip while diagnostics show a malformed bundle or unresolved LaunchServices registration.
If app resolution fails only inside Codex, use the external runner script from normal Terminal instead of trying to automate GUI side effects from Codex.

## Report Location

Always write local-agent reports to:

```text
.agent-bridge/workspace/reports/latest_agent_report.md
```

unless the user explicitly configured another bridge workspace.

## Safe Write Scope

Portable bridge automation may write under:

```text
.agent-bridge/workspace/
```

It must not modify target project source files while initializing, ingesting digests, listing queue entries, or dry-run dispatching.

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

When `.agent-bridge/scripts/dispatch_next.sh --dry-run` triggers a safety pause, inspect:

```text
.agent-bridge/workspace/inbox/user_decision_request.md
.agent-bridge/workspace/outbox/owner_decision_email.md
.agent-bridge/workspace/state/state.json
```

## When Asked to Use Agent Bridge

Do not immediately implement product or research code.

First:

1. initialize or inspect `.agent-bridge/`;
2. run the dry-run self-test;
3. verify queue, report, inbox, outbox, state, and log paths;
4. write a setup report;
5. wait for the next task brief, PM instruction, review digest, CI digest, or owner instruction.

## Portable Install Pattern

If `.agent-bridge/` is missing, ask the user to provide or copy the portable module.

If the portable module is present, use it without making assumptions about project language, framework, domain, repository structure, or deployment target.

From the standalone Agent Bridge repository, prefer dry-run installation:

```bash
python -m agent_bridge.cli install-portable --target /path/to/project --dry-run
python -m agent_bridge.cli install-portable --target /path/to/project
python -m agent_bridge.cli verify-portable --target /path/to/project
```

Installer rules:

- dry-run modifies nothing;
- install copies only `.agent-bridge/` and, by default, `AGENTS.agent-bridge.snippet.md`;
- use `--no-include-agents-snippet` to skip the snippet;
- use `--force` only when the owner explicitly wants to overwrite an existing portable module or snippet;
- after install, run `bash .agent-bridge/scripts/self_test.sh` from the target project root.

Do not automatically edit a target project's existing `AGENTS.md`. The snippet can be reviewed and merged manually.
