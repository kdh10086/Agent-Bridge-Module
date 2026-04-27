# Agent Bridge Portable Handoff

Agent Bridge is a general-purpose automation bridge that coordinates:

- a long-context PM assistant,
- a local coding agent,
- GitHub PR review comments,
- CI failures,
- owner escalation.

It is designed in two layers:

1. **Standalone development repository**  
   Used to develop and test Agent Bridge itself.

2. **Portable project module**  
   A `.agent-bridge/` folder and Codex Skill that can be copied into any project root.

## Why Portable?

The final goal is to use Agent Bridge across arbitrary projects:

- application repositories,
- ML/research codebases,
- paper experiment repositories,
- infrastructure repositories,
- documentation-heavy projects.

No downstream project assumptions are hard-coded.

## Quick Start for Developing Agent Bridge Itself

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
python -m agent_bridge.cli init --force
python -m agent_bridge.cli run-once --dry-run --fixture fixtures/fake_agent_report.md
python -m agent_bridge.cli queue list
python -m agent_bridge.cli dispatch-next --dry-run
python -m agent_bridge.cli simulate-dogfood
```

## Bounded Run Loop

The standalone run loop is dry-run-first and bounded by both cycle and runtime limits:

```bash
python -m agent_bridge.cli run-loop --dry-run --max-cycles 3 --polling-interval-seconds 5
python -m agent_bridge.cli run-loop --dry-run --max-runtime-seconds 60
```

It may inspect the queue, optionally poll read-only watcher producers, and call Dispatcher in dry-run mode to build the next local-agent prompt. It does not perform GUI automation, send Gmail, mutate GitHub, auto-fix, auto-merge, push commits, or bypass `CommandQueue`. If `safety_pause` is set, the loop stops.

## GUI Bridge Stage-Only Mode

The GUI bridge can stage prompts for manual use:

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
python -m agent_bridge.cli dispatch-next --copy-to-clipboard --confirmation-timeout-seconds 120
```

Staged files:

```text
workspace/outbox/pm_assistant_prompt.md
workspace/outbox/next_local_agent_prompt.md
```

Clipboard copy requires explicit manual confirmation. Dry-run never copies. These manual/stage commands never press Enter, submit messages, perform unattended GUI automation, or bypass Dispatcher.

Target guidance is loaded from `config/default.yaml` with optional local overrides in `config/local.yaml`. Use `list-gui-apps` to inspect likely app names and paths, then override targets locally:

```yaml
apps:
  pm_assistant:
    app_name: "ChatGPT"
    app_path: null
    bundle_id: null
    window_hint: "ChatGPT"
    paste_instruction: "Paste into the ChatGPT composer, then review manually."
  local_agent:
    app_name: "Codex"
    app_path: null
    bundle_id: null
    window_hint: "Agent Bridge"
    paste_instruction: "Paste into Codex input, then review manually."
```

`preflight-gui-apps --dry-run` prints the activation fallback plan without touching apps. `preflight-gui-apps --pm-app "ChatGPT" --activate` and `preflight-gui-apps --local-agent-app "Codex" --activate` try activation only; they do not paste, submit, press Enter/Return, mutate GitHub, or send Gmail. Activation tries AppleScript first, then `open -a`, then an explicit `app_path`, then a `bundle_id` when configured.

If activation fails, diagnose the app bundle before retrying:

```bash
python -m agent_bridge.cli diagnose-gui-app --app-path /Applications/ChatGPT.app
python -m agent_bridge.cli diagnose-gui-app --app-path /Applications/Codex.app
python -m agent_bridge.cli diagnose-gui-apps
```

Diagnostics inspect path existence, `Contents/Info.plist`, `Contents/MacOS`, the executable named by `CFBundleExecutable`, bundle identifiers, LaunchServices visibility, and the current process context. The output includes a suggested `config/local.yaml` block. `--activate` may be added to run the same activation fallback attempts, but diagnostics still never paste, submit, press Enter/Return, mutate GitHub, or send Gmail.

GUI automation should run from a normal macOS Terminal, not from an active Codex task process. Check the environment first:

```bash
python -m agent_bridge.cli preflight-external-runner
```

If Codex sandbox markers are present, do not run GUI automation from that process. Open Terminal and run:

```bash
bash scripts/run_gui_roundtrip_external.sh
```

The external runner refuses to run inside the Codex sandbox, runs activation preflights for ChatGPT and Codex, and only then starts the bounded one-cycle report roundtrip. See `docs/EXTERNAL_GUI_RUNNER.md`.

`dispatch-next --stage-only` writes the next local-agent prompt without popping the queue. `dispatch-next --dry-run` keeps the legacy Dispatcher behavior and moves the selected command to in-progress while printing the prompt. `dispatch-next --copy-to-clipboard` stages the prompt, asks for manual confirmation, copies only after confirmation, and then moves the command to in-progress. `--activate-app` asks for a separate confirmation before focusing the configured local-agent app. It does not paste or submit.

For real local-agent side effects, confirmation defaults to `--confirmation-mode terminal-window`. Agent Bridge writes a request under `workspace/confirmations/`, opens a new macOS Terminal window, shows the action summary, target app/window hint, prompt path, what will happen, and what will not happen, then waits for `y` or `n`. Timeout or window close cancels safely. Use `--confirmation-mode inline` only when a visible Terminal confirmation is not appropriate.

`--yes` may be used only in a manually supervised session to skip confirmation prompts for clipboard copy or app activation. It still does not paste, press Enter, or submit.

## Owner-Approved GUI Dogfood

Unattended ChatGPT-to-local-agent GUI dogfood is available only behind an explicit owner-approved flag:

```bash
python -m agent_bridge.cli dogfood-gui-bridge \
  --auto-confirm \
  --max-cycles 1 \
  --max-runtime-seconds 120
```

This command is bounded by max cycles and max runtime. It stages the PM prompt, activates the configured PM assistant app, copies/pastes/submits the PM prompt, waits for a PM response, saves it to `workspace/outbox/pm_response.md`, enqueues a PM command, stages the local-agent prompt through Dispatcher, then copies/pastes/submits it to the configured local-agent app.

SafetyGate still runs before each submit. If blocked, Agent Bridge writes the decision request files, sets `safety_pause`, logs the block, and stops. The command does not mutate GitHub, send Gmail, push commits, auto-merge, or modify downstream project source code.

## Report-to-PM-to-Local-Agent Roundtrip

A stricter one-cycle report roundtrip is available for owner-approved dogfood:

```bash
python -m agent_bridge.cli dogfood-report-roundtrip \
  --auto-confirm \
  --max-cycles 1 \
  --max-runtime-seconds 180
```

This command reads the full `workspace/reports/latest_agent_report.md`, builds a PM prompt asking ChatGPT to return exactly one fenced block labeled `CODEX_NEXT_PROMPT`, submits it to the configured PM assistant target, saves the raw PM response to `workspace/outbox/pm_response.md`, extracts the fenced block to `workspace/outbox/extracted_codex_next_prompt.md`, enqueues that extracted prompt, stages the local-agent prompt through Dispatcher, and submits the staged local-agent prompt to the configured Codex target.

The command refuses to run without `--auto-confirm`, is limited to one cycle, and does not retry silently. SafetyGate runs before ChatGPT submit and before Codex submit.

Before running a live report roundtrip, verify both GUI targets:

```bash
python -m agent_bridge.cli preflight-gui-apps --pm-app "ChatGPT" --activate
python -m agent_bridge.cli preflight-gui-apps --local-agent-app "Codex" --activate
```

Run the full roundtrip only after the activation preflight passes for both targets.
If diagnostics show a missing plist, missing executable, non-executable binary, or LaunchServices cannot resolve the app, update `config/local.yaml` from the suggested config or fix macOS app registration before retrying.
If the active Codex process cannot resolve apps, use `scripts/run_gui_roundtrip_external.sh` from normal Terminal instead of retrying inside Codex.

## Read-Only GitHub CLI Adapter

Live GitHub review/CI ingestion uses the GitHub CLI in read-only mode:

```bash
gh auth login
python -m agent_bridge.cli watch-reviews --owner OWNER --repo REPO --pr 123 --dry-run
python -m agent_bridge.cli watch-ci --owner OWNER --repo REPO --pr 123 --dry-run
```

Use a safe PR for live testing. The adapter only reads PR review comments, issue comments, and status check rollups. It does not comment, create PRs, commit, push, auto-fix, or dispatch to the local coding agent. Watchers enqueue commands only in non-dry-run mode; Dispatcher remains the only sender.

The GitHub CLI adapter paginates review threads, review thread comments, PR issue comments, and status check rollup contexts beyond the first 100 returned nodes. Live dogfood remains dry-run-first:

```bash
python -m agent_bridge.cli watch-reviews --owner OWNER --repo REPO --pr 123 --dry-run
python -m agent_bridge.cli watch-ci --owner OWNER --repo REPO --pr 123 --dry-run
```

Only remove `--dry-run` after confirming the PR is safe and the resulting command should be enqueued. Non-dry-run watcher commands still do not dispatch.

For the full live safe-PR dogfood procedure, read `docs/LIVE_SAFE_PR_DOGFOOD.md`.

Dry-run helper:

```bash
python -m agent_bridge.cli dogfood-gh --owner OWNER --repo REPO --pr 123 --dry-run
```

The helper runs review and CI watcher paths in dry-run mode, prints planned digests, does not mutate the queue, and does not dispatch.

## Quick Start for Using Agent Bridge in Another Project

Dry-run the portable install first:

```bash
python -m agent_bridge.cli install-portable --target /path/to/project --dry-run
```

Then install:

```bash
python -m agent_bridge.cli install-portable --target /path/to/project
python -m agent_bridge.cli verify-portable --target /path/to/project
```

This copies only:

```text
.agent-bridge/
AGENTS.agent-bridge.snippet.md
```

If `.agent-bridge/` or `AGENTS.agent-bridge.snippet.md` already exists, the installer fails safely unless `--force` is passed. Use `--no-include-agents-snippet` to skip the snippet.

Install the Codex skill:

```text
codex_skill/agent-bridge/SKILL.md
→ ~/.codex/skills/agent-bridge/SKILL.md
```

Then tell Codex:

```text
Read ~/.codex/skills/agent-bridge/SKILL.md and .agent-bridge/README.md.
Use Agent Bridge for this project.
Do not modify project code yet.
Initialize the bridge workspace and run the dry-run self-test.
```
