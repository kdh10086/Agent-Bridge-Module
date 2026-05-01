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

## Prompt and Report Style

Keep PM-to-Codex prompts concise and task-focused. Standing rules belong in
`AGENTS.md`, this Skill, and bridge docs; generated prompts should contain only
the current goal, constraints, execution steps, tests, and report expectations.
Preserve the `CODEX_NEXT_PROMPT` fence/body-label compatibility contract.

Keep `latest_agent_report.md` concise and decision-useful: changed files,
checks run, pass/fail, blocker, and next recommended task.

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

Portable queue helpers respect `.agent-bridge/workspace/queue/queue.lock`.
New portable command records should use `prompt_path` or `prompt_text`;
legacy `payload_path` records remain readable.

Create the next dry-run local-agent prompt:

```bash
bash .agent-bridge/scripts/dispatch_next.sh --dry-run
```

In the standalone repository, malformed queue records can be inspected and
conservatively repaired:

```bash
python -m agent_bridge.cli queue malformed list
python -m agent_bridge.cli queue malformed inspect 1
python -m agent_bridge.cli queue repair
python -m agent_bridge.cli queue repair --apply
```

Repair is dry-run by default and must not bypass SafetyGate or CommandQueue.

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
python -m agent_bridge.cli preflight-report-roundtrip
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
    app_path: "/Applications/ChatGPT.app"
    bundle_id: "com.openai.chat"
    backend: "chatgpt_mac_visual"
    profile: "chatgpt_mac"
    require_backend_preflight: false
    visual_asset_profile: "chatgpt_mac"
    click_backend: "pyautogui"
    visual_anchor_click_backend: "pyautogui"
    paste_backend: "menu_paste_accessibility"
    paste_backends:
      - menu_paste_accessibility
      - system_events_key_code_v_command
    window_hint: "ChatGPT"
    idle_empty_timeout_seconds: 600
    idle_empty_poll_interval_seconds: 10
  local_agent:
    app_name: "Codex"
    app_path: null
    bundle_id: null
    window_hint: "Agent Bridge"
    focus_strategy: "direct_plus_anchor"
    input_focus_strategy: null
    input_click_x_ratio: null
    input_click_y_ratio: null
    require_prompt_presence_verification: true
    allow_unverified_submit: false
    allow_unverified_submit_for_noop_dogfood: true
    composer_placeholder_text: "후속 변경 사항을 부탁하세요"
    idle_empty_wait_timeout_seconds: 600
    idle_empty_poll_interval_seconds: 10
    dedicated_automation_session: true
    allow_overwrite_after_idle_timeout: true
    stop_on_idle_timeout: false
    plus_anchor_enabled: true
    plus_anchor_x_offset: 0
    plus_anchor_y_offset: 50
    direct_plus_anchor_enabled: true
    direct_plus_anchor_x_offset: 0
    direct_plus_anchor_y_offset: 50
    direct_plus_anchor_y_offset_candidates: [50]
```

Use `preflight-gui-apps --dry-run` to inspect the activation plan. Use `preflight-gui-apps --pm-app "ChatGPT" --activate` and `preflight-gui-apps --local-agent-app "Codex" --activate` before any live GUI handoff. Activation preflight must not paste, submit, press Enter/Return, mutate GitHub, or send Gmail.

For the current ChatGPT for Mac and Codex visual path, use:

```bash
python -m agent_bridge.cli diagnose-visual-state --app chatgpt_mac
python -m agent_bridge.cli diagnose-visual-state --app chatgpt_chrome_app
python -m agent_bridge.cli diagnose-visual-state --app codex
python -m agent_bridge.cli diagnose-chatgpt-app-targets
python -m agent_bridge.cli preflight-chatgpt-mac-native-target
python -m agent_bridge.cli diagnose-chatgpt-mac-windows
python -m agent_bridge.cli diagnose-chatgpt-mac-composer-text-state
python -m agent_bridge.cli diagnose-chatgpt-mac-response-capture
python -m agent_bridge.cli diagnose-response-capture --app chatgpt_chrome_app
```

These commands are no-submit diagnostics. They activate only the selected app, select the main visible window, capture only that window, and match only the selected app's assets. `chatgpt_mac` uses `assets/gui/chatgpt_mac/chatgpt_mac_*`; `codex` uses `assets/gui/codex/codex_*`. Send-disabled means `IDLE`, send means `COMPOSER_HAS_TEXT`, stop means `RUNNING`, and plus means composer anchor. The output reports every light and dark template separately with search region, best-match confidence, appearance score, configured threshold, effective threshold, cap-applied status, accepted/rejected status, and rejection reason. The shared matcher caps effective visual button/control thresholds at 0.70 while preserving lower configured thresholds. ChatGPT Mac plus-anchor matching is bounded to the lower-left composer-control region. ChatGPT Mac visual state selection is confidence-first and returns `AMBIGUOUS` when incompatible state assets match within `visual_state_ambiguity_margin`; ambiguous state blocks paste/submit. If enabled-send and disabled-send match the same ChatGPT Mac button at near-equal confidence, a bounded RGB appearance-score comparison may resolve the state only when one template is clearly closer to the matched pixels. A weaker stop match must not override stronger send/send-disabled evidence. Codex may use a stricter configured state threshold than plus-anchor matching, but diagnostics still show the shared effective threshold used for acceptance. The default wait is 600 seconds with 10-second polling.

`diagnose-chatgpt-mac-composer-text-state` clicks the safe composer anchor, types exactly `x`, validates `COMPOSER_HAS_TEXT`, and cleans up with Backspace only. It never submits or presses Enter/Return.

`diagnose-chatgpt-mac-response-capture` checks the ChatGPT Mac visual response-copy path. It captures only the selected ChatGPT Mac window and looks for `assets/gui/chatgpt_mac/chatgpt_mac_copy_response_button_light.png` or `assets/gui/chatgpt_mac/chatgpt_mac_copy_response_button_dark.png`. If no copy control is visible, it can use `chatgpt_mac_scroll_down_button_light.png` or `chatgpt_mac_scroll_down_button_dark.png` to click the bounded scroll-down control, recapture the same ChatGPT Mac window, and retry copy-control detection. Add `--attempt-copy` only when you want the diagnostic to click the copy button; it must verify a changed non-empty clipboard before saving `workspace/outbox/chatgpt_mac_response_capture.md`. If those assets are missing or no copy control is found after retry, response capture is unsupported and full report roundtrip must not run. Do not fall back to Google Chrome for this ChatGPT Mac workstream.

The native ChatGPT Mac + Codex PyAutoGUI visual path has an artifact-confirmed safe no-op roundtrip. Treat that success narrowly: it only applies to `AB-ROUNDTRIP-NOOP-VALIDATION`, routed through CommandQueue/Dispatcher, and confirmed by `workspace/reports/latest_agent_report.md` containing `# Agent Report: GUI Roundtrip No-Op Validation Success` plus the no-source-code-change, no-GitHub/Gmail/external-mutation, no-push/auto-merge, and no-long-loop statements. Arbitrary local-agent tasks still need normal SafetyGate, prompt-presence, and submit-confirmation policy.

Chrome app support is a separate visual PM diagnostics profile named `chatgpt_chrome_app`, not a fallback from ChatGPT Mac. It uses PyAutoGUI click-point computation, selected-window-bounded screenshot matching, and assets under `assets/gui/chatgpt_chrome_app/`; it does not depend on Chrome DOM JavaScript by default. If a Chrome/PWA bundle candidate reports `windows=0`, diagnostics try bundle-id activation with `open -b <selected com.google.Chrome.app.* bundle id>` and re-enumerate windows before failing. Chrome app search regions are computed from selected window dimensions, and matching is grayscale/multiscale with profile-specific thresholds and bounded RGB appearance-score false-positive rejection. Assets are plus, voice, send, stop, copy-response, and scroll-down light/dark crops. Voice maps to `IDLE`, send to `COMPOSER_HAS_TEXT`, stop to `RUNNING`, plus to `COMPOSER_ANCHOR`, copy-response to `RESPONSE_COPY`, and scroll-down to `SCROLL_DOWN`. Selection is explicit: default `apps.pm_assistant.profile: "chatgpt_mac"` and diagnostic override `--pm-target chatgpt_mac` or `--pm-target chatgpt_chrome_app` where supported. Never mix assets or silently fall back between PM profiles. Chrome app diagnostics may activate the selected Chrome/PWA app and capture bounded screenshots, but they must not submit a PM prompt or touch Codex.

For Chrome/PWA cross-size validation, use `set-app-window-bounds --app chatgpt_chrome_app --bounds x,y,width,height` or `resize-chatgpt-chrome-app-window --bounds x,y,width,height`. These commands only operate on the selected `com.google.Chrome.app.*` profile, re-enumerate actual bounds afterward, and must not paste, submit, click response-copy, or touch Codex.

Use `diagnose-chatgpt-mac-windows` first when visual diagnostics report unavailable bounds. The ChatGPT Mac visual path must activate the native bundle id `com.openai.chat`: AppleScript `tell application id "com.openai.chat" to activate`, then `open -b com.openai.chat`, then the explicit app path, then verified display-name activation only as a last fallback. `diagnose-chatgpt-app-targets` reports native and Chrome/PWA candidates and rejects bundle ids beginning with `com.google.Chrome`; `preflight-chatgpt-mac-native-target` reports the winning activation strategy and selected native bundle id. Window diagnostics prefer the native ChatGPT Mac bundle id, list window title/bounds/area/role/subrole/minimized state, reject tiny utility windows, and select the largest plausible conversation window. If no usable window is found, stop and make the native ChatGPT Mac conversation window visible before retrying.

Use PM backend preflight before any report roundtrip:

```bash
python -m agent_bridge.cli preflight-pm-backend --dry-run
python -m agent_bridge.cli preflight-pm-backend --activate
```

Supported backend identifiers are `chrome_js`, `chatgpt_pwa_js`, `browser_apple_events`, `accessibility_fallback`, and `unsupported`. Use `Google Chrome` with `chrome_js` for the current DOM JavaScript bridge. `chatgpt_pwa_js` is unsupported for Chrome tab JavaScript because the ChatGPT app/PWA does not support Chrome tab AppleScript syntax. Current JS backends must prove that harmless DOM JavaScript can run through Apple Events and that ChatGPT composer/send/streaming/copy selectors are queryable. If the AppleScript probe fails for a Chrome target, enable JavaScript from Apple Events in the browser or configure a different supported PM assistant target. Do not run the full roundtrip until backend preflight passes.

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

If `CODEX_SANDBOX` is present, do not run GUI automation from Codex. Tell the owner to run this manually from Terminal:

```bash
bash scripts/run_gui_roundtrip_external.sh
```

The external runner shares the same repository workspace, refuses sandboxed execution, runs activation and PM backend preflights, and only then starts the bounded one-cycle roundtrip.

If only `CODEX_SHELL` or `CODEX_THREAD_ID` are present, treat the session as Full Access Codex context. Run `preflight-report-roundtrip` before any live roundtrip; it checks clipboard tools, app activation, PM backend proof, and SafetyGate without paste or submit.

Codex activation is not enough for a verified local-agent handoff. Agent Bridge must focus the Codex prompt composer and verify that the staged local-agent prompt is present before submit. Use these diagnostics when local-agent submit confirmation is uncertain:

```bash
python -m agent_bridge.cli diagnose-codex-ui
python -m agent_bridge.cli dump-codex-ui-tree
python -m agent_bridge.cli diagnose-codex-windows
python -m agent_bridge.cli diagnose-codex-input-target
python -m agent_bridge.cli diagnose-codex-input-target --show-click-target
python -m agent_bridge.cli diagnose-codex-input-target --direct-plus-anchor-preview
python -m agent_bridge.cli diagnose-codex-input-target --visual-debug
python -m agent_bridge.cli diagnose-codex-input-target --paste-test
python -m agent_bridge.cli diagnose-macos-permissions
python -m agent_bridge.cli preflight-iterm-ghost-runner
python -m agent_bridge.cli run-iterm-ghost-runner --auto-confirm --watch-report --max-roundtrips 1
```

`dump-codex-ui-tree` writes `workspace/logs/codex_ui_tree.json` and `workspace/logs/codex_ui_tree.txt` without paste or submit. `diagnose-codex-windows` enumerates Codex windows, rejects tiny utility/popover windows using configured minimum size/area thresholds, and selects the largest plausible visible normal window. `diagnose-codex-input-target` reports whether Codex is active, selected main window bounds, rejected windows, Accessibility candidate count, best candidate, configured fallback strategy, click preview, prompt-presence verification status, and whether live submit would be allowed.

When Codex Accessibility does not expose the composer, use visual anchor diagnostics before any owner-reviewed coordinate fallback. The visual detector activates Codex, verifies it is frontmost, selects the main Codex window rather than blindly using a tiny front utility window, captures only those bounded window coordinates, searches the lower composer band, excludes conservative side regions, and never matches the whole screen. This avoids false positives from ChatGPT's similar plus button. The placeholder `후속 변경 사항을 부탁하세요` is the first-priority idle-empty signal when bounded OCR/text detection is available. The plus button is the fallback anchor because it remains visible for both empty and non-empty composers. Keep template sets app-specific: ChatGPT/Chrome targets use `assets/gui/chatgpt_plus_button_light.png` and `assets/gui/chatgpt_plus_button_dark.png`; Codex targets use `assets/gui/codex_plus_button_light.png` and `assets/gui/codex_plus_button_dark.png`. Templates must not include private screenshot content. Plus matching uses grayscale OpenCV matching, optional multiscale matching, and all configured `visual_plus_templates`, then reports selected window bounds, rejected windows, best-match confidence, bbox, threshold, template path, and search region. Fixed whole-window coordinates are unsafe as a primary strategy, and unverified submit remains disabled by default.

The current owner-approved focus rule is `direct_plus_anchor`: detect the Codex plus button inside the selected main window, keep the plus center x-coordinate, and click slightly above it using `direct_plus_anchor_y_offset` (default 50). The click point must stay inside the selected main window and safe composer band and must not overlap the plus button. `--direct-plus-anchor-preview` reports this point without clicking.

During active bridge development, the owner may validate Codex local-agent click/paste diagnostics from a Full Access Codex context. Use PyAutoGUI as the preferred visual plus-anchor click backend because System Events click against Codex is unreliable in this environment. The iTerm/Terminal ghost runner remains the intended long-term host after stabilization. Use:

```bash
python -m agent_bridge.cli diagnose-codex-input-target --click-test --click-backend pyautogui
python -m agent_bridge.cli diagnose-codex-input-target --paste-test --click-backend pyautogui --paste-backend pyautogui
python -m agent_bridge.cli diagnose-codex-input-target --focus-target-test --click-backend pyautogui
```

Use `diagnose-codex-input-target --paste-test` only as a diagnostic. It first runs the bounded visual composer readiness policy, then it may click the selected safe visual focus target and paste the harmless marker `AGENT_BRIDGE_CODEX_PASTE_TEST_DO_NOT_SUBMIT` using the configured paste backend. Codex paste defaults to PyAutoGUI. Paste-test tries `command-v`, `cmd-v`, explicit `keyDown("command")`/`press("v")`/`keyUp("command")`, and explicit `cmd` keyDown/keyUp variants, then uses `pyautogui.write()` only as a diagnostic fallback for the short ASCII marker. Full local-agent prompts must never use typewrite fallback. System Events paste is an explicit fallback. After paste it captures a fresh bounded Codex-window screenshot and searches only the lower composer band for the marker through the reusable Codex prompt-presence detector. OCR is optional; if OCR is unavailable, marker presence is reported as unknown and bounded debug images plus `codex_marker_presence_ocr.txt` are written under `workspace/logs/` for owner inspection when feasible. Treat the `pytesseract` Python package, system `tesseract` executable, and OCR language data as separate dependencies. English OCR is sufficient for the paste marker; Korean OCR is required for the placeholder. It must not submit, press Enter/Return, or run a queued command. Marker detection does not enable submit. If a failed variant leaves a literal `v` or partial marker, paste-test may use Command-A/Backspace cleanup only. If the marker cannot be cleared automatically because Codex Accessibility remains opaque, tell the owner to clear the composer manually.

Use `diagnose-codex-input-target --focus-target-test --click-backend pyautogui` when paste-test proves the paste variants run but OCR still cannot see the marker. It compares bounded click candidates from placeholder bbox centers, plus-anchor offsets, composer-band safe points, and optional owner-reviewed config points. Owner-reviewed points can use selected-main-window ratios, safe-composer-band ratios, or plus-anchor offsets, but they are still rejected if they leave the selected window, leave the safe composer band, or overlap the plus button. For each candidate it types only `x`, runs bounded OCR, and cleans up with Backspace only. It must not paste full prompts, submit, press Enter/Return, or run a queued command. Use the reported selected candidate and artifacts under `workspace/logs/codex_focus_target_comparison.*` before changing local-agent handoff targeting.

Use `diagnose-macos-permissions` to identify the actual runner app that needs macOS TCC permission. It prints the current executable, Python path, parent process chain, shell, user, Codex markers, Terminal/iTerm context, `osascript` path, and read-only System Events probes. It does not click. Grant Accessibility to Codex for Full Access Codex runs, or to Terminal/iTerm2 for external runner runs; then grant Automation from that runner app to System Events and Codex. Documented reset commands `tccutil reset Accessibility` and `tccutil reset AppleEvents` are owner-run remediation options only, not automation steps.

For production-style GUI dogfood, use the iTerm/Terminal ghost runner. Codex may implement and inspect Agent Bridge, but GUI click/paste should be performed by a normal iTerm/Terminal-launched runner:

```bash
cd /Users/kimdohyeong/Desktop/agent-bridge-portable-handoff
source .venv/bin/activate
python -m agent_bridge.cli preflight-iterm-ghost-runner
python -m agent_bridge.cli run-iterm-ghost-runner \
  --auto-confirm \
  --watch-report \
  --polling-interval-seconds 3 \
  --max-runtime-seconds 3600 \
  --max-roundtrips 1
```

The ghost runner watches `workspace/reports/latest_agent_report.md` for content-hash changes and uses `workspace/state/last_processed_report_hash`, `workspace/state/ghost_runner.lock`, cooldown, debounce, and `--max-roundtrips` to prevent feedback loops. It refuses `CODEX_SANDBOX` and rejects Codex-hosted execution. iTerm/Terminal must have Accessibility and Automation permissions. Codex should not run GUI click/paste from its own hosted process.

Accessibility discovery is preferred. A manual `window_relative_click` fallback is available but disabled by default and must be configured explicitly. It computes a click point relative to the selected main Codex window, not absolute coordinates. `--show-click-target` only previews; `--click-test` may click only when explicitly requested and still must not paste or submit. Unverified submit is blocked by default. `allow_unverified_submit: true` is a dangerous override and still cannot make Agent Bridge claim success without post-submit UI evidence. `allow_unverified_submit_for_noop_dogfood: true` is narrower and only applies to the safe `AB-ROUNDTRIP-NOOP-VALIDATION` prompt; success requires the no-op report artifact title `# Agent Report: GUI Roundtrip No-Op Validation Success` and the required no-mutation statements. `verify-roundtrip-result` reports the confirmation basis as `ui_confirmed`, `artifact_confirmed`, `attempted_only`, or `none`; stop-after-local-agent-submit counts as successful only when the safe no-op artifact later appears.

For automated report roundtrips, Agent Bridge treats the configured Codex window as a dedicated automation session by default. It waits up to 600 seconds for the Codex composer placeholder `후속 변경 사항을 부탁하세요`, polling every 10 seconds. Each poll reactivates/rechecks Codex, rereads the selected main window bounds, captures a fresh bounded screenshot, and reruns placeholder detection. If the placeholder stays absent and `composer_policy.on_busy_timeout: overwrite`, Agent Bridge may use the plus button only as an anchor, click above it by the configured offset, select existing composer text, and replace it with the staged local-agent prompt. It must not click the plus button itself.

Use conservative mode when the Codex window may contain user work:

```yaml
apps:
  local_agent:
    dedicated_automation_session: false
    allow_overwrite_after_idle_timeout: false
    stop_on_idle_timeout: true
    composer_policy:
      mode: dedicated_automation_session
      busy_placeholder_wait_timeout_seconds: 600
      busy_placeholder_poll_interval_seconds: 10
      on_busy_timeout: abort
```

Conservative timeout stops safely, does not paste or submit, and leaves the command pending. Submit success still requires UI evidence; attempted submit is not enough.

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
  --max-runtime-seconds 180 \
  --submit-local-agent \
  --stop-after-local-agent-submit \
  --no-artifact-confirmation-wait
```

This sends the full latest report to the PM assistant, requires exactly one `CODEX_NEXT_PROMPT` fenced block in the PM response, and requires the first non-empty line inside that block to also be exactly `CODEX_NEXT_PROMPT`. This body label keeps native ChatGPT Mac response-copy compatible when it copies only the rendered code block body and drops the fence info string. Agent Bridge extracts either the fenced block or a body-only `CODEX_NEXT_PROMPT` copy, enqueues it, and uses Dispatcher to stage the local-agent prompt before submitting it to Codex. SafetyGate still runs before both submits.

For the safe no-op queue handoff, stop immediately after local-agent submit/queue is attempted. Do not wait in the same Codex task for the no-op artifact; Codex must return idle so the queued prompt can execute.

Run a live report roundtrip only after PM assistant activation, PM backend preflight, and local-agent activation preflight all pass. If activation or backend proof fails, stop and update `config/local.yaml` with the correct app name, app path, bundle id, or backend instead of attempting paste or submit.
Do not retry the full roundtrip while diagnostics show a malformed bundle or unresolved LaunchServices registration.
If app resolution fails only inside Codex, use the external runner script from normal Terminal instead of trying to automate GUI side effects from Codex.

For Computer Use assisted launches, Codex must prepare a terminal trigger and let Computer Use start only that command:

```bash
python -m agent_bridge.cli prepare-computer-use-terminal-trigger
python -m agent_bridge.cli show-computer-use-terminal-trigger
```

Computer Use may focus an already-open normal macOS Terminal, paste the single command from `workspace/outbox/computer_use_terminal_trigger.md`, press Enter once, and stop. It must not operate ChatGPT directly, operate Codex directly, manually copy ChatGPT responses, or paste into Codex. Agent Bridge performs the actual PM prompt submit, response capture, `CODEX_NEXT_PROMPT` extraction, queue enqueue, Dispatcher staging, Codex submit, SafetyGate checks, one-cycle bounds, and event logging.

After a Computer Use triggered run, verify the result from Codex:

```bash
python -m agent_bridge.cli verify-roundtrip-result
```

The verifier inspects the staged PM prompt, captured PM response, extracted `CODEX_NEXT_PROMPT`, staged local-agent prompt, Codex input candidate discovery, prompt presence before submit, event log, SafetyGate state, and one-cycle completion. It separates local-agent prompt presence, submit attempted, and submit confirmed. Attempted means Agent Bridge sent the submit action; confirmed requires UI evidence: input cleared, a new user message was detected, or a running/responding state was detected. If prompt presence or confirmation is unavailable, the verifier reports that clearly and must not claim full success. Use `diagnose-codex-ui`, `dump-codex-ui-tree`, and `diagnose-codex-input-target` to inspect whether Codex UI Accessibility exposes the composer, input text, conversation, and running-state signals. If the verifier reports a failure inside Agent Bridge, fix code or config, rerun tests, regenerate the trigger if needed, and retry no more than three times. Do not claim success unless the full one-cycle roundtrip completed with verified prompt presence and confirmed handoff.

The ChatGPT GUI bridge uses HTML state signals when available:

- empty composer: `data-testid="composer-speech-button"` or `aria-label="Voice 시작"`;
- send-ready: `data-testid="send-button"`, `id="composer-submit-button"`, or `aria-label="프롬프트 보내기"`;
- streaming: `data-testid="stop-button"` or `aria-label="스트리밍 중지"`;
- response copy-ready: `data-testid="copy-turn-action-button"` or `aria-label="응답 복사"`.

For the Google Chrome backend, Agent Bridge must wait for the ChatGPT composer to be idle-empty before paste. Idle-empty means `data-testid="composer-speech-button"` or `aria-label="Voice 시작"`. If a stop button or pre-existing send button is visible before paste, Agent Bridge waits instead of overwriting the UI. The default wait timeout is 600 seconds and the default poll interval is 10 seconds. Each poll logs `pm_idle_empty_poll` with observed state, elapsed seconds, and remaining seconds. After idle-empty, Agent Bridge must focus a visible composer, insert the prompt through DOM JavaScript, dispatch input/change events, and verify the composer text before submit. It must wait for send-ready before submit, wait for streaming to finish before copy, prefer the copy button inside the latest assistant response container, and treat owner-provided CSS/XPath selectors as brittle fallbacks. If button-based copy does not update the clipboard, Agent Bridge may extract the latest assistant turn text through DOM JavaScript and write it to the clipboard, but it must still verify non-empty text and `CODEX_NEXT_PROMPT` when expected. If idle-empty wait, focus, send-ready, or response copy fails, inspect the logged selector/container summary, active element summary, text length, clipboard lengths, button state, and last observed pre-paste state before retrying.

If Computer Use cannot access Terminal or iTerm2, use the external runner daemon/file-trigger mode instead of trying to force Computer Use terminal control. The owner should start the runner once from a normal macOS Terminal:

```bash
bash scripts/start_external_gui_runner.sh
```

or run it directly:

```bash
python -m agent_bridge.cli run-external-gui-runner \
  --auto-confirm \
  --watch-reports \
  --watch-queue \
  --polling-interval-seconds 3 \
  --max-runtime-seconds 3600
```

The runner watches `workspace/reports/latest_agent_report.md`, `workspace/queue/pending_commands.jsonl`, and trigger markers under `workspace/triggers/`. It uses `workspace/state/external_runner.lock`, writes `workspace/logs/external_gui_runner.log`, appends bridge events, and still enforces SafetyGate and CommandQueue. It refuses `CODEX_SANDBOX`; Full Access Codex markers without `CODEX_SANDBOX` are warnings when preflights pass.

Trigger marker files:

```text
workspace/triggers/report_roundtrip.request
workspace/triggers/queue_dispatch.request
```

Do not install the LaunchAgent template automatically. Review `packaging/macos/com.agentbridge.runner.plist.template` before any manual installation.

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

## Foreground Bridge Runner

When the owner wants the GUI bridge active from a normal terminal, use the foreground runner rather than a background daemon:

```bash
python -m agent_bridge.cli preflight-run-bridge --pm-target chatgpt_mac
python -m agent_bridge.cli run-bridge --pm-target chatgpt_mac --watch-report workspace/reports/latest_agent_report.md --polling-interval-seconds 3
```

or:

```bash
python -m agent_bridge.cli preflight-run-bridge --pm-target chatgpt_chrome_app
python -m agent_bridge.cli run-bridge --pm-target chatgpt_chrome_app --watch-report workspace/reports/latest_agent_report.md --polling-interval-seconds 3
```

The helper script is:

```bash
bash scripts/run_bridge.sh --pm-target chatgpt_mac
bash scripts/run_bridge.sh --pm-target chatgpt_chrome_app
```

The runner watches report content hashes, triggers on any post-startup `workspace/reports/latest_agent_report.md` content change by default, uses `workspace/state/foreground_bridge_runner.lock`, and stops on Ctrl-C, terminal close, max runtime, max roundtrips, or safety pause. It records the current report hash as the startup baseline and waits for a later content change by default; use `--require-trigger-marker` only when the owner explicitly wants to require `AGENT_BRIDGE_GUI_ROUNDTRIP_TEST`, and use `--process-existing-trigger` only when the owner explicitly wants the already-existing startup report to run. It must not silently switch between PM profiles. It does not create a LaunchAgent or run as a background daemon by default.

Use `--debug` when validating owner-run GUI handoffs. It enables state-machine and GUI-action traces in `workspace/logs/gui_state_machine_debug.jsonl` and `workspace/logs/gui_actions_debug.jsonl`, with a `bridge_attempt_id` on each debug event. The local-agent Codex handoff must log prompt length/hash, clipboard set, paste attempt/result, and submit guard outcome; submit is blocked if the prompt is empty, paste was not attempted, or click/focus succeeded but paste did not report success.

`run-bridge` startup should stay fast. It records the startup baseline and prints readiness without running full visual diagnostics. Use `preflight-run-bridge` for heavier no-submit checks. Live PM target resolution, asset matching, response-copy diagnostics, and Codex visual checks should happen only when a post-startup report change starts a bridge attempt.

## Shared PM Visual Policy

Treat `chatgpt_chrome_app` as the known-good canonical visual sequence and make `chatgpt_mac` use the same production mechanism. The shared sequence is: select PM profile, resolve app/window, detect visual state, apply bounded retry/wait policy, detect plus anchor, focus composer, set/read clipboard, paste with retry, verify `COMPOSER_HAS_TEXT`, submit PM prompt, wait for response completion, reactivate PM before response-copy, copy/save PM response, extract `CODEX_NEXT_PROMPT`, then hand off through SafetyGate, CommandQueue, Dispatcher, and Codex visual handoff.

The profiles should differ only by target resolution, asset profile, thresholds, and response-copy assets. `chatgpt_mac` uses native bundle id `com.openai.chat` and `assets/gui/chatgpt_mac/`. `chatgpt_chrome_app` uses the selected `com.google.Chrome.app.*` PWA process and `assets/gui/chatgpt_chrome_app/`. Do not use Chrome DOM JavaScript by default for visual profiles, and never silently fall back between PM profiles.

## Prompt And Report Style

PM-to-Codex prompts should be concise: goal, constraints, execution steps, tests, and report expectations. Do not repeat all standing rules when `AGENTS.md`, this skill, or project docs already cover them. Preserve the `CODEX_NEXT_PROMPT` fence and first-body-line label.

Agent reports should be concise and decision-useful: what changed, what passed, what failed, current blocker, exact failure point if any, tests/commands run, and next recommended task. Use detailed narrative only when it is debug evidence.

## Future Roadmap

Later work may package Agent Bridge as a reusable repo/module, improve Skill installation docs, validate external project `.agent-bridge/` use, extend the pipeline into ChatGPT to local Codex to GitHub/Codex review, and remove dogfood scaffolding after stable validation. Do not implement packaging or GitHub upload as part of routine bridge stabilization tasks unless explicitly requested.
