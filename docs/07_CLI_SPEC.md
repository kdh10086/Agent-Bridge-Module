# CLI Spec

Required commands:

```bash
agent-bridge init
agent-bridge status
agent-bridge enqueue --type CHATGPT_PM_NEXT_TASK --payload path/to/file.md
agent-bridge queue list
agent-bridge queue pop
agent-bridge dispatch-next --dry-run
agent-bridge dispatch-next --stage-only
agent-bridge dispatch-next --copy-to-clipboard
agent-bridge dispatch-next --copy-to-clipboard --activate-app
agent-bridge dispatch-next --copy-to-clipboard --confirmation-mode terminal-window
agent-bridge dispatch-next --copy-to-clipboard --confirmation-mode inline
agent-bridge dispatch-next --copy-to-clipboard --confirmation-timeout-seconds 120
agent-bridge collect-agent-report
agent-bridge send-report-to-pm
agent-bridge show-gui-targets
agent-bridge preflight-gui-apps --dry-run
agent-bridge preflight-gui-apps --pm-app "ChatGPT" --activate
agent-bridge preflight-gui-apps --local-agent-app "Codex" --activate
agent-bridge list-gui-apps
agent-bridge diagnose-gui-app --app-path /Applications/ChatGPT.app
agent-bridge diagnose-gui-app --app-path /Applications/Codex.app
agent-bridge diagnose-gui-apps
agent-bridge preflight-external-runner
agent-bridge stage-pm-prompt --dry-run
agent-bridge stage-pm-prompt --copy-to-clipboard
agent-bridge stage-local-agent-prompt --dry-run
agent-bridge stage-local-agent-prompt --copy-to-clipboard
agent-bridge run-once
agent-bridge ingest-review --fixture path/to/review.json --dry-run
agent-bridge ingest-ci --fixture path/to/ci_failure.json --dry-run
agent-bridge watch-reviews --owner OWNER --repo REPO --pr 123 --dry-run
agent-bridge watch-ci --owner OWNER --repo REPO --pr 123 --dry-run
agent-bridge dogfood-gh --owner OWNER --repo REPO --pr 123 --dry-run
agent-bridge dogfood-gui-bridge --auto-confirm --max-cycles 1 --max-runtime-seconds 120
agent-bridge dogfood-report-roundtrip --auto-confirm --max-cycles 1 --max-runtime-seconds 180
agent-bridge run-loop --dry-run
agent-bridge run-loop --dry-run --max-cycles 3 --polling-interval-seconds 5
agent-bridge run-loop --dry-run --max-runtime-seconds 60
agent-bridge install-portable --target /path/to/project --dry-run
agent-bridge install-portable --target /path/to/project
agent-bridge verify-portable --target /path/to/project
agent-bridge simulate-dogfood
agent-bridge pause
agent-bridge resume
agent-bridge reset-state
```

`install-portable` options:

```bash
--dry-run
--force
--include-agents-snippet / --no-include-agents-snippet
```

`--force` is required to overwrite an existing `.agent-bridge/` or `AGENTS.agent-bridge.snippet.md`.

Stage-only GUI bridge commands:

```bash
agent-bridge show-gui-targets
agent-bridge preflight-gui-apps --dry-run
agent-bridge preflight-gui-apps --pm-app "ChatGPT" --activate
agent-bridge preflight-gui-apps --local-agent-app "Codex" --activate
agent-bridge list-gui-apps
agent-bridge diagnose-gui-app --app-path /Applications/ChatGPT.app
agent-bridge diagnose-gui-app --app-path /Applications/Codex.app
agent-bridge diagnose-gui-apps
agent-bridge preflight-external-runner
agent-bridge stage-pm-prompt --dry-run
agent-bridge stage-pm-prompt --copy-to-clipboard
agent-bridge stage-local-agent-prompt --dry-run
agent-bridge stage-local-agent-prompt --copy-to-clipboard
```

Behavior:

- `stage-pm-prompt` builds from `workspace/reports/latest_agent_report.md`;
- `stage-pm-prompt` writes `workspace/outbox/pm_assistant_prompt.md`;
- `stage-local-agent-prompt` builds from the highest-priority pending command without popping it;
- `stage-local-agent-prompt` writes `workspace/outbox/next_local_agent_prompt.md`;
- `--copy-to-clipboard` requires explicit manual confirmation;
- `--dry-run` never copies to clipboard;
- no command presses Enter, submits a message, or performs autonomous GUI automation;
- safety-gated staged prompts are blocked and create owner decision request files.

GUI target metadata is read from `config/default.yaml` with optional `config/local.yaml` overrides:

```yaml
apps:
  pm_assistant:
    app_name: "Google Chrome"
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

`show-gui-targets` prints this metadata only. It must not activate apps, paste, submit, or press keys.

`list-gui-apps` scans `/Applications` and `~/Applications` to help identify app names and paths for `config/local.yaml`. It does not activate apps.

`preflight-gui-apps` verifies configured GUI targets before live handoff:

```bash
agent-bridge preflight-gui-apps --dry-run
agent-bridge preflight-gui-apps --pm-app "ChatGPT" --activate
agent-bridge preflight-gui-apps --local-agent-app "Codex" --activate
```

Dry-run prints the activation plan only. Activation mode tries AppleScript first, then `open -a`, then an explicit `app_path`, then a `bundle_id` when configured. It must not paste, submit, press Enter/Return, mutate GitHub, or send Gmail. If app activation fails, update `config/local.yaml` with the app name, app path, or bundle id reported by `list-gui-apps`, then rerun preflight.

`diagnose-gui-app` inspects a specific `.app` bundle without activating it by default:

```bash
agent-bridge diagnose-gui-app --app-path /Applications/ChatGPT.app
agent-bridge diagnose-gui-app --app-path /Applications/Codex.app
```

It prints path existence, directory/symlink state, resolved real path, `Contents/Info.plist`, `Contents/MacOS`, `CFBundleName`, `CFBundleDisplayName`, `CFBundleIdentifier`, `CFBundleExecutable`, `CFBundlePackageType`, `LSMinimumSystemVersion`, executable existence/permissions, read-only LaunchServices visibility checks, current process context, and suggested `config/local.yaml` values.

`diagnose-gui-apps` runs the same diagnostics for the configured PM assistant and local-agent targets. Add `--activate` only when you explicitly want to try the existing activation strategies. Even with `--activate`, diagnostics must not paste, submit, press Enter/Return, mutate GitHub, or send Gmail.

`preflight-external-runner` checks whether the current process is suitable for GUI automation:

```bash
agent-bridge preflight-external-runner
```

It prints whether Codex sandbox markers are present, whether `pbcopy` and `pbpaste` are available, whether AppleScript can resolve the configured PM assistant and local-agent apps, and the recommended next command. It must not activate apps, paste, submit, press Enter/Return, mutate GitHub, or send Gmail.

If Codex sandbox markers are present, Codex should stop and tell the owner to run:

```bash
bash scripts/run_gui_roundtrip_external.sh
```

from a normal macOS Terminal. The script refuses to run inside the Codex sandbox, runs external-runner and activation preflights, then runs the bounded one-cycle report roundtrip only after preflights pass.

Local-agent manual dispatch commands:

```bash
agent-bridge dispatch-next --dry-run
agent-bridge dispatch-next --stage-only
agent-bridge dispatch-next --copy-to-clipboard
agent-bridge dispatch-next --copy-to-clipboard --activate-app
agent-bridge dispatch-next --copy-to-clipboard --activate-app --yes
agent-bridge dispatch-next --copy-to-clipboard --confirmation-mode terminal-window
agent-bridge dispatch-next --copy-to-clipboard --confirmation-mode inline
agent-bridge dispatch-next --copy-to-clipboard --confirmation-timeout-seconds 120
```

Behavior:

- `dispatch-next --dry-run` uses Dispatcher, writes `workspace/outbox/next_local_agent_prompt.md`, prints the prompt, and keeps the existing dispatch semantics by moving the command to in-progress;
- `dispatch-next --stage-only` writes `workspace/outbox/next_local_agent_prompt.md` without popping the queue;
- `dispatch-next --copy-to-clipboard` writes the prompt, asks for explicit manual confirmation, copies only after confirmation, then moves the command to in-progress;
- `dispatch-next --activate-app` asks for a separate confirmation before focusing the configured local-agent app;
- cancelled confirmation leaves the command pending;
- app activation uses the configured `apps.local_agent.app_name`;
- activation failure leaves the staged prompt in outbox and reports the error;
- no mode pastes automatically;
- no mode presses Enter or submits;
- `--yes` skips confirmation prompts for clipboard/app activation only and must be used only in a supervised session.

Confirmation modes:

- `terminal-window` is the default for real local-agent side effects;
- terminal-window mode writes request/result files under `workspace/confirmations/`;
- terminal-window mode opens a new macOS Terminal window using `osascript`;
- the Terminal window shows the action summary, target app/window hint, prompt file path, what will happen, and what will not happen;
- owner input `y` or `yes` allows the side effect;
- owner input `n` or `no`, closing the window without a result, or timeout cancels safely;
- `--confirmation-timeout-seconds` controls how long the main process waits;
- `inline` uses the current terminal prompt and is intended as a fallback.

`watch-reviews` and `watch-ci` require GitHub CLI for live use:

```bash
gh auth login
```

Live testing should use a safe PR. These commands are read-only: they do not comment, create PRs, commit, push, auto-fix, or dispatch. Watchers create canonical digest markdown and enqueue commands only when not using `--dry-run`. Dry-run prints a digest preview and does not mutate the command queue.

The read-only `gh` adapter paginates:

- PR review threads beyond the first 100 nodes;
- review thread comments beyond the first 100 nodes when GitHub reports more pages;
- PR issue comments beyond the first 100 nodes;
- PR status check rollup contexts beyond the first 100 nodes.

Safe live dogfood pattern:

```bash
gh auth login
python -m agent_bridge.cli dogfood-gh --owner OWNER --repo REPO --pr 123 --dry-run
python -m agent_bridge.cli watch-reviews --owner OWNER --repo REPO --pr 123 --dry-run
python -m agent_bridge.cli watch-ci --owner OWNER --repo REPO --pr 123 --dry-run
python -m agent_bridge.cli queue list
python -m agent_bridge.cli run-loop --dry-run --max-cycles 2 --polling-interval-seconds 1 --no-dispatch
```

Only remove `--dry-run` when the owner has identified the PR as safe and wants a queue entry created. Removing `--dry-run` still only writes digest files and enqueues commands; Dispatcher remains the only sender to the local coding agent.

`dogfood-gh` is dry-run only in this milestone. It runs the review and CI watcher paths, prints planned digests, does not write canonical digest files, does not enqueue commands, and does not dispatch.

`dogfood-gui-bridge` options:

```bash
--auto-confirm
--max-cycles INTEGER
--max-runtime-seconds INTEGER
--pm-response-timeout-seconds INTEGER
```

`dogfood-gui-bridge` is an owner-approved unattended dogfood harness. It refuses to run without `--auto-confirm`, and it must always be bounded by max cycles and max runtime. It may activate the configured PM assistant and local-agent apps, copy prompt text to the clipboard, paste via the system paste command, and submit through the focused app. SafetyGate runs before each submit. If blocked, the command writes decision request files, sets `safety_pause`, logs `gui_dogfood_safety_blocked`, and stops.

It does not call GitHub write APIs, send Gmail, push commits, auto-merge, or modify downstream project source code. Watchers remain producer-only, and Dispatcher remains the component that prepares local-agent prompts.

`dogfood-report-roundtrip` options:

```bash
--auto-confirm
--max-cycles INTEGER
--max-runtime-seconds INTEGER
--pm-response-timeout-seconds INTEGER
```

`dogfood-report-roundtrip` is a one-cycle owner-approved GUI roundtrip. It reads the full `workspace/reports/latest_agent_report.md`, writes `workspace/outbox/pm_assistant_prompt.md`, asks the PM assistant to respond with exactly one fenced block labeled `CODEX_NEXT_PROMPT`, saves the raw response to `workspace/outbox/pm_response.md`, extracts the block to `workspace/outbox/extracted_codex_next_prompt.md`, enqueues that extracted prompt, and asks Dispatcher to stage `workspace/outbox/next_local_agent_prompt.md`.

It refuses to run without `--auto-confirm`, enforces exactly one cycle, requires a runtime bound, does not retry silently, and stops if response copy or `CODEX_NEXT_PROMPT` extraction fails. SafetyGate runs before ChatGPT submit and before Codex submit.

Before running a live `dogfood-report-roundtrip`, run activation preflight for both configured targets. The roundtrip must stop before paste or submit if PM assistant activation or local-agent activation fails.
If diagnostics show a malformed bundle or unresolved LaunchServices registration, do not retry the full roundtrip until the target app is fixed and activation preflight passes.
If app resolution fails only from the Codex execution context, use the external runner script from normal Terminal instead of running GUI automation directly from Codex.

`run-loop` options:

```bash
--dry-run / --no-dry-run
--max-cycles INTEGER
--max-runtime-seconds INTEGER
--polling-interval-seconds FLOAT
--watch-reviews
--watch-ci
--owner OWNER
--repo REPO
--pr 123
--dispatch / --no-dispatch
```

Run-loop behavior:

- dry-run is the default;
- max cycles and max runtime are mandatory bounds;
- polling interval controls the wait between cycles;
- `safety_pause` stops the loop;
- watcher polling requires `--owner`, `--repo`, and `--pr`;
- watchers remain producer-only;
- Dispatcher remains the only local-agent sender and is called in dry-run mode;
- real GUI dispatch is not implemented;
- Gmail sending, GitHub mutation, auto-fix, auto-merge, commit, and push are out of scope.
