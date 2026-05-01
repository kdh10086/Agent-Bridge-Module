# CLI Spec

Required commands:

```bash
agent-bridge init
agent-bridge status
agent-bridge enqueue --type CHATGPT_PM_NEXT_TASK --payload path/to/file.md
agent-bridge queue enqueue --type USER_MANUAL_COMMAND --prompt-text "..."
agent-bridge queue enqueue --type CHATGPT_PM_NEXT_TASK --payload path/to/file.md
agent-bridge queue list
agent-bridge queue list --status all
agent-bridge queue peek
agent-bridge queue pop
agent-bridge queue mark-in-progress COMMAND_ID
agent-bridge queue mark-completed
agent-bridge queue mark-failed --reason "..."
agent-bridge queue mark-blocked --reason "..."
agent-bridge queue malformed list
agent-bridge queue malformed inspect 1
agent-bridge queue repair
agent-bridge queue repair --apply
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
agent-bridge preflight-gui-apps --pm-app "Google Chrome" --activate
agent-bridge preflight-gui-apps --local-agent-app "Codex" --activate
agent-bridge preflight-pm-backend --dry-run
agent-bridge preflight-pm-backend --activate
agent-bridge list-gui-apps
agent-bridge diagnose-gui-app --app-path /Applications/ChatGPT.app
agent-bridge diagnose-gui-app --app-path /Applications/Codex.app
agent-bridge diagnose-gui-apps
agent-bridge diagnose-codex-ui
agent-bridge dump-codex-ui-tree
agent-bridge diagnose-codex-windows
agent-bridge diagnose-codex-input-target
agent-bridge diagnose-codex-input-target --show-click-target
agent-bridge diagnose-codex-input-target --direct-plus-anchor-preview
agent-bridge diagnose-codex-input-target --visual-debug
agent-bridge diagnose-codex-input-target --click-test
agent-bridge diagnose-codex-input-target --paste-test
agent-bridge diagnose-macos-permissions
agent-bridge preflight-external-runner
agent-bridge preflight-iterm-ghost-runner
agent-bridge run-iterm-ghost-runner --auto-confirm --watch-report --max-roundtrips 1
agent-bridge preflight-report-roundtrip
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

## Command Queue

The command queue is stored under `workspace/queue/` and is the durable handoff point between
producers and Dispatcher. Producers may enqueue commands, inspect status, or mark commands blocked,
but only Dispatcher may send a command to the local coding agent.

Queue storage files:

```text
workspace/queue/pending_commands.jsonl
workspace/queue/in_progress.json
workspace/queue/completed_commands.jsonl
workspace/queue/failed_commands.jsonl
workspace/queue/blocked_commands.jsonl
workspace/queue/malformed_commands.jsonl
workspace/queue/queue.lock
```

Command schema:

```json
{
  "id": "cmd_...",
  "created_at": "2026-04-30T00:00:00+00:00",
  "source": "pm_assistant_report_roundtrip",
  "prompt_path": "workspace/outbox/extracted_codex_next_prompt.md",
  "prompt_text": null,
  "status": "pending",
  "priority": 95,
  "metadata": {}
}
```

`payload_path` remains accepted as a backward-compatible alias for `prompt_path`. At least one of
`prompt_path`, `payload_path`, or `prompt_text` is required. Supported statuses are `pending`,
`in_progress`, `completed`, `failed`, and `blocked`.

Queue mutations use an advisory file lock at `workspace/queue/queue.lock`. The lock covers enqueue,
status transitions, malformed-record quarantine, and dedupe checks that depend on the current queue
state. Lock acquisition is bounded; timeout fails clearly instead of writing partial queue state.
The lock is released in `finally` blocks after success or exception. `CommandQueue(debug=True)` or
`AGENT_BRIDGE_QUEUE_DEBUG=1` records lock acquire/release/timeout diagnostics for targeted tests.

Dispatcher prompt resolution order is:

1. `prompt_text`
2. `prompt_path`
3. `payload_path` legacy fallback

New producers should write `prompt_path` for file-backed prompts or `prompt_text` for inline prompts.
Records that provide both `prompt_text` and a path are considered ambiguous and malformed.

Queue operations are deterministic under repeated runs:

- `queue enqueue` appends a pending command unless its `dedupe_key` already exists in pending,
  in-progress, completed, failed, or blocked records.
- `queue list --status pending|in_progress|completed|failed|blocked|all` reads persisted state.
- `queue peek` returns the next pending command by highest priority, then `created_at`, then `id`
  without mutating the queue.
- `queue mark-in-progress` moves a pending command to `in_progress.json`.
- `queue mark-completed`, `queue mark-failed`, and `queue mark-blocked` persist terminal state and
  clear `in_progress.json` when applicable.
- Malformed queue lines are quarantined to `malformed_commands.jsonl` and skipped so the remaining
  queue can still be inspected and dispatched.
- `queue malformed list` shows quarantined malformed records without printing full prompt text.
- `queue malformed inspect INDEX` shows quarantine metadata, raw-line length/hash, prompt source
  summary, and optionally the raw line with `--show-raw`.
- `queue repair` is dry-run by default. `queue repair --apply` re-enqueues only quarantined raw
  records that validate against the current schema. The original quarantine record remains unless a
  future explicit purge command is added.

`queue list` displays `id`, `status`, `priority`, `source`, prompt source type, `created_at`, and a
failed/blocked reason when present. Inline prompt text is not printed by default.

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
agent-bridge preflight-gui-apps --pm-app "Google Chrome" --activate
agent-bridge preflight-gui-apps --local-agent-app "Codex" --activate
agent-bridge list-gui-apps
agent-bridge diagnose-gui-app --app-path /Applications/ChatGPT.app
agent-bridge diagnose-gui-app --app-path /Applications/Codex.app
agent-bridge diagnose-gui-apps
agent-bridge preflight-external-runner
agent-bridge preflight-report-roundtrip
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
    app_name: "ChatGPT"
    app_path: null
    bundle_id: "com.openai.chat"
    backend: "chatgpt_mac_visual"
    profile: "chatgpt_mac"
    require_backend_preflight: false
    window_hint: "ChatGPT"
    idle_empty_timeout_seconds: 600
    idle_empty_poll_interval_seconds: 10
    paste_instruction: "Paste into the ChatGPT composer, then review manually."
    focus_strategy: "visual_plus_anchor"
    visual_asset_profile: "chatgpt_mac"
    click_backend: "pyautogui"
    visual_anchor_click_backend: "pyautogui"
    paste_backend: "menu_paste_accessibility"
    paste_backends:
      - menu_paste_accessibility
      - system_events_key_code_v_command
    visual_plus_templates:
      - assets/gui/chatgpt_mac/chatgpt_mac_plus_button_light.png
      - assets/gui/chatgpt_mac/chatgpt_mac_plus_button_dark.png
    visual_send_disabled_templates:
      - assets/gui/chatgpt_mac/chatgpt_mac_send_disabled_button_light.png
      - assets/gui/chatgpt_mac/chatgpt_mac_send_disabled_button_dark.png
    visual_send_templates:
      - assets/gui/chatgpt_mac/chatgpt_mac_send_button_light.png
      - assets/gui/chatgpt_mac/chatgpt_mac_send_button_dark.png
    visual_stop_templates:
      - assets/gui/chatgpt_mac/chatgpt_mac_stop_button_light.png
      - assets/gui/chatgpt_mac/chatgpt_mac_stop_button_dark.png
    visual_plus_confidence_threshold: 0.60
  local_agent:
    app_name: "Codex"
    app_path: null
    bundle_id: null
    window_hint: "Agent Bridge"
    paste_instruction: "Paste into Codex input, then review manually."
    focus_strategy: "direct_plus_anchor"
    click_backend: "pyautogui"
    visual_anchor_click_backend: "pyautogui"
    paste_backend: "pyautogui"
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
    composer_policy:
      mode: dedicated_automation_session
      busy_placeholder_wait_timeout_seconds: 600
      busy_placeholder_poll_interval_seconds: 10
      on_busy_timeout: overwrite
    visual_plus_templates:
      - assets/gui/codex/codex_plus_button_light.png
      - assets/gui/codex/codex_plus_button_dark.png
    visual_send_disabled_templates:
      - assets/gui/codex/codex_send_disabled_button_light.png
      - assets/gui/codex/codex_send_disabled_button_dark.png
    visual_send_templates:
      - assets/gui/codex/codex_send_button_light.png
      - assets/gui/codex/codex_send_button_dark.png
    visual_stop_templates:
      - assets/gui/codex/codex_stop_button_light.png
      - assets/gui/codex/codex_stop_button_dark.png
    visual_plus_confidence_threshold: 0.80
    visual_state_confidence_threshold: 0.95
    visual_state_ambiguity_margin: 0.02
    visual_state_search_region: "lower_control_band"
```

Visual PM profiles use the same production sequence. `chatgpt_mac` and
`chatgpt_chrome_app` differ through target resolver, assets, thresholds, and
paste backend chain config only; production should not branch into separate
native or Chrome-only PM flows after profile resolution. Full PM paste is
accepted only when the action is reflected in the UI as `COMPOSER_HAS_TEXT` or
another explicit prompt-present signal; raw-v-prone variants are
diagnostic-only.

`show-gui-targets` prints this metadata only. It must not activate apps, paste, submit, or press keys.

`diagnose-visual-state` reports the app-window-bounded asset state for ChatGPT Mac or Codex:

```bash
agent-bridge diagnose-visual-state --app chatgpt_mac
agent-bridge diagnose-visual-state --app chatgpt_chrome_app
agent-bridge diagnose-visual-state --app codex
```

The command activates only the selected app, selects its main visible window, captures only that window, and matches only that app's asset profile. `chatgpt_mac` uses `assets/gui/chatgpt_mac/chatgpt_mac_*`; `chatgpt_chrome_app` uses `assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_*`; `codex` uses `assets/gui/codex/codex_*`. Send-disabled or voice assets mean `IDLE`, send assets mean `COMPOSER_HAS_TEXT`, stop assets mean `RUNNING`, and plus assets provide the composer anchor. The output includes per-template diagnostics for both light and dark assets: existence, search region, original template size, selected scale, scaled template size, best-match bbox, confidence, appearance score, configured threshold, effective threshold, cap-applied status, accepted/rejected state, and rejection reason. The shared matcher caps effective visual button/control thresholds at 0.70 while preserving lower configured thresholds. Chrome app matching is selected-window-bounded and window-size tolerant: composer/control regions are computed from selected window dimensions, grayscale matching is run across the configured scale range, and bounded RGB appearance-score validation can reject high-correlation false positives. ChatGPT Mac plus-anchor matching is scoped to the bounded lower-left composer-control region so a lower plus threshold can tolerate small UI overlays without matching conversation text. ChatGPT Mac state selection compares accepted state candidates by confidence. It returns `AMBIGUOUS` when incompatible states are within `visual_state_ambiguity_margin`, and it does not let a weaker stop match override stronger send/send-disabled evidence. If ChatGPT Mac send-disabled and enabled-send match the same control at near-equal confidence, a bounded RGB appearance-score comparison may resolve the state only when one template is clearly closer to the matched pixels; otherwise `AMBIGUOUS` remains. `AMBIGUOUS` blocks paste/submit. Codex can use a stricter configured `visual_state_confidence_threshold` than `visual_plus_confidence_threshold`, but button/control matching still reports the capped effective threshold used for acceptance. The default idle wait policy is 600 seconds with 10-second polling; timeout overwrites only in dedicated automation mode and aborts in conservative mode. The command does not paste, submit, press Enter/Return, mutate GitHub, or send Gmail.

`diagnose-chatgpt-mac-composer-text-state` validates ChatGPT Mac `COMPOSER_HAS_TEXT` without submitting:

```bash
agent-bridge diagnose-chatgpt-mac-composer-text-state
```

It activates native ChatGPT Mac, requires a safe plus-anchor click point, clicks the composer with PyAutoGUI, types exactly `x`, confirms the enabled-send state, and cleans up with Backspace only. It never presses Enter/Return.

`diagnose-chatgpt-mac-response-capture` reports whether the ChatGPT Mac visual PM backend has a bounded response-copy path:

```bash
agent-bridge diagnose-chatgpt-mac-windows
agent-bridge diagnose-chatgpt-mac-response-capture
agent-bridge diagnose-chatgpt-mac-response-capture --attempt-copy
agent-bridge diagnose-chatgpt-app-targets
agent-bridge preflight-chatgpt-mac-native-target
```

`chatgpt_mac_visual` native activation is bundle-id first: AppleScript `tell application id "com.openai.chat" to activate`, then `open -b com.openai.chat`, then the explicit app path, then verified display-name activation as the last fallback. `diagnose-chatgpt-app-targets` lists native ChatGPT and Chrome/PWA candidates, rejects bundle ids beginning with `com.google.Chrome`, and reports the selected native bundle id. `preflight-chatgpt-mac-native-target` activates the native app and reports the winning activation strategy. `diagnose-chatgpt-mac-windows` activates the configured native ChatGPT Mac target, prefers its bundle id, enumerates windows, rejects tiny/utility windows, and selects the largest plausible visible conversation window. It fails clearly if no usable ChatGPT Mac window exists.

The command activates ChatGPT for Mac, selects its main visible window, captures only that window, and checks for response-copy assets under `assets/gui/chatgpt_mac/`: `chatgpt_mac_copy_response_button_light.png` and `chatgpt_mac_copy_response_button_dark.png`. If the copy button is not visible, it checks `chatgpt_mac_scroll_down_button_light.png` and `chatgpt_mac_scroll_down_button_dark.png`, clicks the bounded scroll-down control when detected, recaptures the same ChatGPT Mac window, and retries copy-button detection. It does not submit a prompt. `--attempt-copy` is required before the diagnostic clicks the response-copy button; when capture succeeds, it verifies the clipboard changed to non-empty text and writes `workspace/outbox/chatgpt_mac_response_capture.md`. If these assets are missing or no copy button is detected after retry, response capture is reported as unsupported and full report roundtrip must not run. The ChatGPT Mac visual workstream must not use Google Chrome fallback.

`list-gui-apps` scans `/Applications` and `~/Applications` to help identify app names and paths for `config/local.yaml`. It does not activate apps.

`preflight-gui-apps` verifies configured GUI targets before live handoff:

```bash
agent-bridge preflight-gui-apps --dry-run
agent-bridge preflight-gui-apps --pm-app "Google Chrome" --activate
agent-bridge preflight-gui-apps --local-agent-app "Codex" --activate
```

Dry-run prints the activation plan only. Bundle-id targets activate by AppleScript application id first, then `open -b`, then an explicit `app_path`, with display-name activation only as a verified fallback. Targets without a bundle id use display-name AppleScript and `open -a`. It must not paste, submit, press Enter/Return, mutate GitHub, or send Gmail. If app activation fails, update `config/local.yaml` with the app name, app path, or bundle id reported by `list-gui-apps`, then rerun preflight.

`preflight-pm-backend` verifies the configured PM assistant backend before a live report roundtrip:

```bash
agent-bridge preflight-pm-backend --dry-run
agent-bridge preflight-pm-backend --activate
agent-bridge preflight-pm-backend --app-name "Google Chrome" --backend chrome_js --activate
```

Supported backend identifiers are `chrome_js`, `chatgpt_pwa_js`, `browser_apple_events`, `accessibility_fallback`, and `unsupported`. Use `Google Chrome` with `chrome_js` for the current DOM JavaScript bridge; `chatgpt_pwa_js` is not selected for Chrome tab JavaScript because the ChatGPT app/PWA does not support Chrome tab AppleScript syntax. Current working backends must prove DOM JavaScript execution through Apple Events with a harmless `document.readyState` probe and must prove that composer/send/streaming/copy selectors can be queried. The command does not paste, submit, press Enter/Return, mutate GitHub, or send Gmail.

For Chrome targets, JavaScript execution uses the nested AppleScript form `tell application "Google Chrome" / tell active tab of front window / execute javascript ...`. It can fail unless the browser allows JavaScript from Apple Events. If the preflight reports an AppleScript error, enable that browser setting or configure a different supported PM assistant target. `accessibility_fallback` remains blocked until it can reliably prove send-ready detection, streaming detection, and latest-response copy-button detection.

`diagnose-gui-app` inspects a specific `.app` bundle without activating it by default:

```bash
agent-bridge diagnose-gui-app --app-path /Applications/ChatGPT.app
agent-bridge diagnose-gui-app --app-path /Applications/Codex.app
```

It prints path existence, directory/symlink state, resolved real path, `Contents/Info.plist`, `Contents/MacOS`, `CFBundleName`, `CFBundleDisplayName`, `CFBundleIdentifier`, `CFBundleExecutable`, `CFBundlePackageType`, `LSMinimumSystemVersion`, executable existence/permissions, read-only LaunchServices visibility checks, current process context, and suggested `config/local.yaml` values.

`diagnose-gui-apps` runs the same diagnostics for the configured PM assistant and local-agent targets. Add `--activate` only when you explicitly want to try the existing activation strategies. Even with `--activate`, diagnostics must not paste, submit, press Enter/Return, mutate GitHub, or send Gmail.

`diagnose-codex-ui` inspects Codex UI accessibility state without paste or submit:

```bash
agent-bridge diagnose-codex-ui
```

It reports whether Codex is the active app, whether the focused input is detectable, whether conversation text elements are detectable, whether running/responding indicators are visible, and any Accessibility limitation. Confirmed local-agent submit depends on this kind of UI evidence; if Accessibility data is unavailable, Agent Bridge reports submit confirmation as `unknown` rather than success.

`dump-codex-ui-tree` writes a read-only Codex Accessibility tree dump:

```bash
agent-bridge dump-codex-ui-tree
```

Outputs:

```text
workspace/logs/codex_ui_tree.json
workspace/logs/codex_ui_tree.txt
```

It must not paste, submit, press Enter/Return, mutate GitHub, or send Gmail.

`diagnose-codex-input-target` reports whether Agent Bridge can focus the Codex prompt composer:

```bash
agent-bridge diagnose-codex-windows
agent-bridge diagnose-codex-input-target
agent-bridge diagnose-codex-input-target --show-click-target
agent-bridge diagnose-codex-input-target --direct-plus-anchor-preview
agent-bridge diagnose-codex-input-target --visual-debug
agent-bridge diagnose-codex-input-target --click-test
agent-bridge diagnose-codex-input-target --click-test --click-backend pyautogui
agent-bridge diagnose-codex-input-target --paste-test
agent-bridge diagnose-codex-input-target --paste-test --click-backend pyautogui
agent-bridge diagnose-codex-input-target --paste-test --click-backend pyautogui --paste-backend pyautogui
agent-bridge diagnose-codex-input-target --focus-target-test --click-backend pyautogui
```

`diagnose-codex-windows` enumerates Codex windows, rejects tiny utility/popover windows using `min_main_window_width`, `min_main_window_height`, and `min_main_window_area`, and selects the largest plausible visible normal window when `window_selection_strategy: "largest_visible_normal"` is configured. `diagnose-codex-input-target` reports Codex frontmost state, selected main window bounds, rejected windows, Accessibility input candidate count, best candidate, fallback strategy, fallback click point, placeholder visibility, placeholder bounds when available, plus-button bounds when available, plus-anchor click point, direct plus-anchor click point, visual detection backend status, screenshot capture status, visual plus-button confidence, visual placeholder status, selected visual strategy, safe region bounds, computed visual click point, selected click backend, PyAutoGUI availability, local idle-empty wait timeout/poll interval, effective timeout policy, whether overwrite would be allowed, whether prompt presence verification is possible, and whether live submit would be allowed. `--show-click-target` and `--direct-plus-anchor-preview` preview only. `--visual-debug` writes screenshot/debug images when feasible. `--click-test` may click the selected target but still must not paste or submit. For Codex visual plus-anchor targeting, `pyautogui` is the default backend during active bridge development; `system_events` is available only when explicitly selected or configured.

The direct plus-anchor policy is owner-reviewed and window-bounded. It keeps the plus button x-coordinate, subtracts `direct_plus_anchor_y_offset` from the plus center y-coordinate, and rejects the point if it overlaps the plus button, leaves the selected main Codex window, or leaves the lower composer band. The default offset is 50 pixels and candidate offsets are configurable for diagnostics.

`--paste-test` is diagnostic only. It activates Codex, runs the bounded visual composer readiness policy first, requires a safe visual focus target in the selected main Codex window, copies the marker `AGENT_BRIDGE_CODEX_PASTE_TEST_DO_NOT_SUBMIT` to the clipboard, pastes with the configured paste backend, captures a fresh screenshot scoped to that selected window, and searches only the lower composer band for the marker. The Codex default paste backend is PyAutoGUI. In paste-test mode it tries `command-v`, `cmd-v`, explicit `keyDown("command")`/`press("v")`/`keyUp("command")`, and explicit `cmd` keyDown/keyUp variants, with bounded OCR after each attempt. If those do not make the short ASCII marker visible, paste-test may use a diagnostic-only `pyautogui.write()` fallback for that marker. Typewrite fallback is never used for full local-agent prompts. System Events paste remains available through explicit `--paste-backend system_events` or config override. OCR matching uses the reusable Codex prompt-presence detector that future submit gating can call before any submit action. OCR is optional; unavailable OCR is reported as `unknown`, not success or failure, and bounded debug artifacts are written to `workspace/logs/codex_marker_presence.png`, `workspace/logs/codex_marker_presence_annotated.png`, and `workspace/logs/codex_marker_presence_ocr.txt` when feasible. Diagnostics report whether the `pytesseract` Python package, system `tesseract` executable, English OCR data, and Korean OCR data are available. English OCR is sufficient for the marker; Korean OCR is required for the placeholder. It must not submit, press Enter/Return, run a local-agent command, mutate GitHub, or send Gmail. If a failed variant leaves a literal `v` or partial marker, the diagnostic may use Command-A/Backspace cleanup only; if cleanup is not safe or verifiable, it prints a manual cleanup instruction. Marker detection is diagnostic only, and unverified submit remains disabled.

`--focus-target-test` is diagnostic only. It activates Codex, captures only the bounded selected main Codex window, builds candidate click targets from placeholder bbox centers, plus-anchor offsets of 50/70/90/110 pixels, safe composer-band points, and optional owner-reviewed points from config. Owner-reviewed points can use `basis: "main_window"` with `x_ratio`/`y_ratio`, `basis: "composer_band"` with safe-region ratios, or `basis: "plus_anchor"` with `x_offset`/`y_offset`. Every candidate is rejected if it falls outside the selected main window, outside the safe composer band, or inside the plus-button bbox. For each safe candidate it clicks with the selected backend, types a one-character ASCII marker `x`, runs bounded OCR in the lower composer band, and cleans up with Backspace only. It never pastes a full marker, submits, presses Enter/Return, runs a queued command, mutates GitHub, or sends Gmail. It reports each candidate point, safety/rejection reason, OCR result, confidence, OCR text, cleanup result, and selected candidate if any. Artifacts are written to `workspace/logs/codex_focus_target_comparison.png`, `workspace/logs/codex_focus_target_comparison_annotated.png`, `workspace/logs/codex_focus_target_comparison_ocr.txt`, and `workspace/logs/codex_focus_target_comparison.json`.

`diagnose-macos-permissions` is read-only:

```bash
agent-bridge diagnose-macos-permissions
```

It reports current executable path, Python path, parent process chain, shell, current user, Codex context markers, Terminal/iTerm context, `osascript` path, whether System Events read-only probes pass, and whether a System Events UI-scripting denial implies the click path will fail with `-25211`. It does not perform a click. Use it to identify whether Accessibility should be granted to Codex, Terminal, iTerm2, or another hosting process. Owner-run remediation may include granting Accessibility and Automation in System Settings, or manually running `tccutil reset Accessibility` and `tccutil reset AppleEvents` before re-granting permissions.

Accessibility discovery is preferred. The guarded fallback is disabled by default and can be configured locally:

```yaml
apps:
  local_agent:
    input_focus_strategy: "window_relative_click"
    input_click_x_ratio: 0.50
    input_click_y_ratio: 0.92
    require_prompt_presence_verification: false
    allow_unverified_submit: false
```

The fallback click is relative to the selected main Codex window bounds, not absolute screen coordinates. Submit remains blocked by default when prompt presence cannot be verified. `allow_unverified_submit: true` is an explicit dangerous override and still does not count as success unless post-submit UI evidence confirms the handoff. `allow_unverified_submit_for_noop_dogfood: true` is narrower: it only allows the `AB-ROUNDTRIP-NOOP-VALIDATION` prompt, still uses SafetyGate and CommandQueue, and requires artifact confirmation from `workspace/reports/latest_agent_report.md`.

Visual detection is preferred before owner-reviewed coordinate fallback when Accessibility is opaque. It activates Codex first, verifies the frontmost app, reads the selected main Codex window bounds, and captures only that bounded window region. It searches only within the selected main Codex window lower composer band, excludes conservative side regions, and prefers the plus button as the primary anchor. The placeholder is checked visually inside the same Codex window bounds; if OCR/text detection is unavailable, the diagnostic reports that explicitly instead of claiming placeholder absence. Template matching is app-specific: Google Chrome/ChatGPT targets use only `assets/gui/chatgpt_plus_button_light.png` and `assets/gui/chatgpt_plus_button_dark.png`, while Codex targets use only `assets/gui/codex_plus_button_light.png` and `assets/gui/codex_plus_button_dark.png`. These assets must be small plus-button crops without private screenshot content. Matching is grayscale, can run at multiple scales, is governed by `visual_plus_confidence_threshold`, and reports the best-match bbox, confidence, template path, template size, and bounded search region even when no match clears the threshold. Fixed whole-window coordinates and whole-screen matching are unsafe as a primary strategy.

Codex composer visual state detection uses the placeholder text `후속 변경 사항을 부탁하세요` as the first-priority idle-empty signal. Before local-agent paste, Agent Bridge can wait up to `composer_policy.busy_placeholder_wait_timeout_seconds` seconds and polls every `composer_policy.busy_placeholder_poll_interval_seconds` seconds. Each poll reactivates/rechecks Codex, rereads selected main window bounds, captures a new bounded screenshot, and reruns placeholder detection. If the placeholder is absent until timeout, the default dedicated-session policy may perform controlled overwrite only when `composer_policy.mode: dedicated_automation_session` and `composer_policy.on_busy_timeout: overwrite`. Set `on_busy_timeout: abort` for conservative mode. Controlled overwrite uses the plus button only as an anchor:

```text
click_x = plus_center_x + plus_anchor_x_offset
click_y = plus_center_y - plus_anchor_y_offset
```

It must not click the plus button itself. It selects existing composer text, replaces it with the Agent Bridge prompt, and still requires prompt-presence and submit-confirmation evidence. Conservative mode sets `dedicated_automation_session: false`, `allow_overwrite_after_idle_timeout: false`, and `stop_on_idle_timeout: true`; timeout then stops safely and leaves the command pending.

`preflight-external-runner` checks whether the current process is suitable for GUI automation:

```bash
agent-bridge preflight-external-runner
```

`preflight-iterm-ghost-runner` is stricter. It is intended for the long-term iTerm/Terminal-launched GUI bridge host:

```bash
agent-bridge preflight-iterm-ghost-runner
```

It reports the parent process chain, refuses `CODEX_SANDBOX`, rejects Codex-hosted execution, checks clipboard tools and app resolution, runs PM backend preflight, runs Codex activation preflight, and checks the bounded visual plus-anchor diagnostic without paste or submit. If the process is hosted by Codex, it reports that the preflight must be run from iTerm/Terminal.

`run-iterm-ghost-runner` watches report changes from iTerm/Terminal:

```bash
agent-bridge run-iterm-ghost-runner \
  --auto-confirm \
  --watch-report \
  --polling-interval-seconds 3 \
  --max-runtime-seconds 3600 \
  --max-roundtrips 1
```

It refuses Codex-hosted GUI execution, watches `workspace/reports/latest_agent_report.md`, detects content-hash changes, debounces changes, acquires `workspace/state/ghost_runner.lock`, records `workspace/state/last_processed_report_hash`, and starts at most one bounded report roundtrip per new report hash while `--max-roundtrips` permits it. It writes activity to `workspace/logs/external_gui_runner.log` and structured events to `workspace/logs/bridge.jsonl`. It does not mutate GitHub, send Gmail, push commits, auto-merge, bypass SafetyGate, bypass CommandQueue, or run without max-runtime bounds.

It prints whether Codex sandbox markers are present, whether `pbcopy` and `pbpaste` are available, whether AppleScript can resolve the configured PM assistant and local-agent apps, and the recommended next command. It must not activate apps, paste, submit, press Enter/Return, mutate GitHub, or send Gmail.

If `CODEX_SANDBOX` is present, Codex should stop and tell the owner to run:

```bash
bash scripts/run_gui_roundtrip_external.sh
```

from a normal macOS Terminal. The script refuses to run inside the Codex sandbox, runs external-runner and activation preflights, then runs the bounded one-cycle report roundtrip only after preflights pass.

If only `CODEX_SHELL` or `CODEX_THREAD_ID` are present, Agent Bridge treats it as a Full Access Codex context. Run:

```bash
agent-bridge preflight-report-roundtrip
```

This confirms `CODEX_SANDBOX` is not set, checks clipboard tools, activates the configured PM assistant and local-agent apps, verifies the PM assistant backend, and checks SafetyGate against the PM prompt. It must not paste, submit, mutate GitHub, or send Gmail.

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
--submit-local-agent / --no-submit-local-agent
--stop-after-local-agent-submit
--artifact-confirmation-wait / --no-artifact-confirmation-wait
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

`dogfood-report-roundtrip` is a one-cycle owner-approved GUI roundtrip. It reads the full `workspace/reports/latest_agent_report.md`, writes `workspace/outbox/pm_assistant_prompt.md`, asks the PM assistant to respond with exactly one fenced block labeled `CODEX_NEXT_PROMPT`, and requires the first non-empty line inside that block to also be exactly `CODEX_NEXT_PROMPT`. The inner body label preserves compatibility with native ChatGPT Mac response-copy when only the rendered code block body is copied. Agent Bridge saves the raw response to `workspace/outbox/pm_response.md`, extracts either the fenced block or a body-only `CODEX_NEXT_PROMPT` copy to `workspace/outbox/extracted_codex_next_prompt.md`, enqueues that extracted prompt, and asks Dispatcher to stage `workspace/outbox/next_local_agent_prompt.md`.

It refuses to run without `--auto-confirm`, enforces exactly one cycle, requires a runtime bound, does not retry silently, and stops if response copy or `CODEX_NEXT_PROMPT` extraction fails. SafetyGate runs before ChatGPT submit and before Codex submit.

Use `--stop-after-local-agent-submit --no-artifact-confirmation-wait` for the owner-approved no-op queue handoff: Agent Bridge submits or queues the safe local-agent prompt to Codex once, records the attempt, then returns immediately so the active Codex task can end and the queued prompt can run. `--no-submit-local-agent` is a staging-only diagnostic and must not be used to claim a completed handoff.

Before running a live `dogfood-report-roundtrip`, run activation preflight for both configured targets and PM backend preflight. The roundtrip must stop before paste or submit if PM assistant activation, PM backend proof, or local-agent activation fails.
If diagnostics show a malformed bundle or unresolved LaunchServices registration, do not retry the full roundtrip until the target app is fixed and activation preflight passes.
If app resolution fails only from the Codex execution context, use the external runner script from normal Terminal instead of running GUI automation directly from Codex.

`prepare-computer-use-terminal-trigger`:

```bash
python -m agent_bridge.cli prepare-computer-use-terminal-trigger
```

Writes `workspace/outbox/computer_use_terminal_trigger.md`. The file tells Computer Use to focus an already-open normal macOS Terminal, paste exactly one shell command, press Enter once, and stop. Computer Use must not operate ChatGPT, operate Codex, copy ChatGPT responses, or paste into Codex directly. The generated command uses `scripts/run_gui_roundtrip_external.sh` so Agent Bridge remains responsible for SafetyGate, CommandQueue, one-cycle bounds, and logging.

`show-computer-use-terminal-trigger`:

```bash
python -m agent_bridge.cli show-computer-use-terminal-trigger
```

Prints the existing trigger file or previews the trigger content without creating a GUI side effect.

`verify-roundtrip-result`:

```bash
python -m agent_bridge.cli verify-roundtrip-result
```

Inspects:

```text
workspace/reports/latest_agent_report.md
workspace/outbox/pm_assistant_prompt.md
workspace/outbox/pm_response.md
workspace/outbox/extracted_codex_next_prompt.md
workspace/outbox/next_local_agent_prompt.md
workspace/logs/bridge.jsonl
```

It reports whether the PM prompt was staged, PM response was captured, exactly one `CODEX_NEXT_PROMPT` block was extracted, the local-agent prompt was staged, Codex input candidate discovery succeeded, local-agent prompt presence was verifiable, prompt text was present before submit, local-agent submit was attempted, local-agent submit was confirmed by UI, local-agent submit was confirmed by artifact, SafetyGate blocked, and one cycle completed. `local_agent_submit_attempted` means Agent Bridge sent the submit action. `local_agent_submit_confirmed_by_ui` is `yes` only when UI evidence exists: input cleared, a new user message was detected, or a running/responding state was detected. `local_agent_submit_confirmed_by_artifact` is only valid for the safe no-op dogfood title `# Agent Report: GUI Roundtrip No-Op Validation Success`. The verifier also reports `codex_input_focus_strategy`, `local_agent_submit_confirmation_signal`, and `local_agent_submit_blocked_reason`. Old logs without confirmation are reported as `unknown`, and verification must not claim full success from attempted submit alone. Failure output identifies the likely failure point: external terminal trigger, PM backend preflight, ChatGPT response copy, `CODEX_NEXT_PROMPT` extraction, queue/local-agent staging, Codex prompt presence before submit, Codex submit, Codex submit confirmation, one-cycle completion, or SafetyGate block.

Artifact confirmation is intentionally no-op-only. The staged or extracted local-agent prompt must satisfy the `AB-ROUNDTRIP-NOOP-VALIDATION` contract, and the latest report must contain the exact title plus statements that no source code changed, no GitHub/Gmail/external mutation occurred, no push or auto-merge occurred, and no long or unbounded loop ran. The verifier reports `local_agent_submit_confirmation_basis` as `ui_confirmed`, `artifact_confirmed`, `attempted_only`, or `none`, and `full_success_basis` mirrors the accepted success basis. A stop-after-local-agent-submit run can be successful only when this safe no-op artifact appears after the queued prompt executes. Arbitrary local-agent tasks cannot use no-op artifact confirmation.

Native ChatGPT Mac + Codex PyAutoGUI visual roundtrip status: the safe no-op path is artifact-confirmed with ChatGPT Mac as the PM target and Codex as the local-agent target. Chrome/PWA fallback remains blocked for the `chatgpt_mac_visual` workstream.

PM target profiles:

```yaml
apps:
  pm_assistant:
    profile: "chatgpt_mac"
```

CLI target selection is explicit:

```bash
python -m agent_bridge.cli diagnose-visual-state --app chatgpt_mac
python -m agent_bridge.cli diagnose-visual-state --app chatgpt_chrome_app
python -m agent_bridge.cli diagnose-chatgpt-app-targets --pm-target chatgpt_chrome_app
python -m agent_bridge.cli diagnose-response-capture --app chatgpt_chrome_app
```

`chatgpt_mac` uses native bundle id `com.openai.chat` and `assets/gui/chatgpt_mac/`. The `chatgpt_chrome_app` diagnostics profile uses a Chrome/PWA app target with bundle id matching `com.google.Chrome.app.*` and `assets/gui/chatgpt_chrome_app/`, still through PyAutoGUI click-point computation and window-bounded screenshot matching. `diagnose-chatgpt-app-targets --pm-target chatgpt_chrome_app` reports process name, bundle id, PID, frontmost/visible state, window count, bounds, minimized/fullscreen data when available, and rejection reasons. If a Chrome/PWA bundle candidate exists with `windows=0`, it attempts `open -b <bundle id>` and re-enumerates before stopping. Chrome app regions are selected-window-relative, templates are matched across the configured scale range, and diagnostics report original template size, selected scale, scaled size, confidence, appearance score, and rejection reason. Selected profiles must never mix assets or silently fall back to another profile. Missing assets must fail diagnostics clearly. In this phase, `chatgpt_chrome_app` commands are no-submit diagnostics and do not run a report roundtrip.

Chrome/PWA window bounds helpers:

```bash
python -m agent_bridge.cli set-app-window-bounds --app chatgpt_chrome_app --bounds 100,100,1000,700
python -m agent_bridge.cli resize-chatgpt-chrome-app-window --bounds 100,100,1000,700
```

These helpers are limited to the selected `chatgpt_chrome_app` profile and reject other apps. They set position/size through System Events, re-enumerate the selected window, and do not paste, submit, click response-copy, or use Chrome DOM JavaScript.

`chatgpt_chrome_app` assets and state mapping:

```text
IDLE:
  assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_voice_button_light.png
  assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_voice_button_dark.png
COMPOSER_HAS_TEXT:
  assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_send_button_light.png
  assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_send_button_dark.png
RUNNING:
  assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_stop_button_light.png
  assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_stop_button_dark.png
COMPOSER_ANCHOR:
  assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_plus_button_light.png
  assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_plus_button_dark.png
RESPONSE_COPY:
  assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_copy_response_button_light.png
  assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_copy_response_button_dark.png
SCROLL_DOWN:
  assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_scroll_down_button_light.png
  assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_scroll_down_button_dark.png
```

ChatGPT state-machine behavior for report roundtrips:

- empty composer: `data-testid="composer-speech-button"` or `aria-label="Voice 시작"`;
- send-ready: `data-testid="send-button"`, `id="composer-submit-button"`, or `aria-label="프롬프트 보내기"`;
- streaming: `data-testid="stop-button"` or `aria-label="스트리밍 중지"`;
- response copy-ready: `data-testid="copy-turn-action-button"` or `aria-label="응답 복사"`.

For the Google Chrome backend, Agent Bridge waits before paste until the composer is idle-empty (`data-testid="composer-speech-button"` or `aria-label="Voice 시작"`). It waits rather than pasting when ChatGPT is streaming (`data-testid="stop-button"` or `aria-label="스트리밍 중지"`) or when the send button is visible before Agent Bridge paste, which means a user has a pending composed message. The default idle-empty wait timeout is 600 seconds and the default poll interval is 10 seconds. Each poll logs `pm_idle_empty_poll` with `observed_state`, `elapsed_seconds`, and `remaining_seconds`; timeout logs `pm_idle_empty_wait_timeout` with the last observed state. After idle-empty is detected, Agent Bridge focuses a visible composer using `textarea`, `#prompt-textarea`, `[contenteditable="true"]`, or `[data-testid="composer-text-input"]`, inserts the prompt through DOM JavaScript, dispatches input/change events, and verifies that the composer text is non-empty and contains an expected Agent Bridge marker. Agent Bridge waits for send-ready before clicking send, refuses to submit if the composer remains in the empty state, waits while streaming is active, then copies the latest assistant response. Copy strategies are latest assistant response container copy button, owner CSS selector, owner XPath, owner full XPath, generic fallback, then latest assistant DOM text extraction copied to the clipboard. Each strategy must change the clipboard, produce non-empty text, and include `CODEX_NEXT_PROMPT` when expected.

If send-ready does not appear, the failure diagnostic includes the composer selector, active element summary, composer text length, and current button state (`speech-button`, `send-button`, `stop-button`, or `unknown`).

`run-external-gui-runner` options:

```bash
--auto-confirm
--watch-reports
--watch-queue
--polling-interval-seconds FLOAT
--max-runtime-seconds INTEGER
--debounce-seconds FLOAT
--cooldown-seconds FLOAT
--stale-lock-seconds FLOAT
--pm-response-timeout-seconds INTEGER
```

The external GUI runner is the preferred unattended trigger when Computer Use cannot control Terminal or iTerm2. It must be started from a normal macOS Terminal or LaunchAgent, refuses `CODEX_SANDBOX`, and only continues when app/clipboard preflight passes. `CODEX_SHELL` or `CODEX_THREAD_ID` without `CODEX_SANDBOX` are warnings, not hard blocks.

It watches:

```text
workspace/reports/latest_agent_report.md
workspace/queue/pending_commands.jsonl
workspace/triggers/report_roundtrip.request
workspace/triggers/queue_dispatch.request
```

It records startup baselines for report/queue mtimes so existing files do not trigger an immediate GUI run. Trigger files or later mtime changes cause one bounded report roundtrip through the existing report-roundtrip path. The runner uses `workspace/state/external_runner.lock` to prevent overlap, renames trigger files to `.consumed.<timestamp>` after a run, writes text logs to `workspace/logs/external_gui_runner.log`, and appends structured events to `workspace/logs/bridge.jsonl`.

Helper scripts:

```bash
bash scripts/start_external_gui_runner.sh
bash scripts/status_external_gui_runner.sh
bash scripts/stop_external_gui_runner.sh
```

The LaunchAgent template is `packaging/macos/com.agentbridge.runner.plist.template` and is not installed automatically.

`preflight-run-bridge` options:

```bash
--pm-target chatgpt_mac|chatgpt_chrome_app
```

The command performs no-submit checks for the selected foreground bridge profile: PM target selection, PM visual-state detection, PM response-copy asset readiness, Codex visual-state detection, `pbcopy`/`pbpaste`, PyAutoGUI availability, config readability, and queue directory presence. It does not submit a PM prompt or touch Codex input.

`run-bridge` options:

```bash
--pm-target chatgpt_mac|chatgpt_chrome_app
--watch-report PATH
--polling-interval-seconds FLOAT
--debounce-seconds FLOAT
--cooldown-seconds FLOAT
--max-runtime-seconds INTEGER
--max-roundtrips INTEGER
--require-trigger-marker / --no-require-trigger-marker
--process-existing-trigger
--debug
--debug-state-machine
--debug-gui-actions
--debug-screenshots
--roundtrip-max-runtime-seconds INTEGER
--pm-response-timeout-seconds INTEGER
```

`run-bridge` is the foreground terminal runner. It watches `workspace/reports/latest_agent_report.md` by SHA-256 content hash, ignores unchanged or already processed report content, debounces rapid writes, and triggers on any post-startup report content change by default. Use `--require-trigger-marker` only when compatibility/safety mode should require `AGENT_BRIDGE_GUI_ROUNDTRIP_TEST` before starting a bridge attempt. On startup it records the current report hash as the session baseline and does not process that existing report unless `--process-existing-trigger` is passed. Startup is lightweight: the command selects only the requested PM profile, logs `runner_start_time`, `baseline_recorded_time`, `ready_to_watch_time`, and `startup_elapsed_seconds`, and defers app/window visual diagnostics until a bridge attempt starts. `max-runtime-seconds 0` and `max-roundtrips 0` mean unlimited until Ctrl-C, terminal close, or safety pause. The selected PM profile is explicit and must not silently fall back to another profile. `chatgpt_mac` uses native ChatGPT Mac assets; `chatgpt_chrome_app` uses Chrome/PWA app assets. Bridge execution still goes through PM prompt staging, shared visual PM submit/copy, `CODEX_NEXT_PROMPT` extraction, SafetyGate, CommandQueue, Dispatcher, and Codex visual/PyAutoGUI handoff. By default the foreground runner uses stop-after-local-agent-submit and skips artifact wait to avoid Codex queue deadlock.

`--debug` enables both `--debug-state-machine` and `--debug-gui-actions`. State-machine traces go to `workspace/logs/gui_state_machine_debug.jsonl`, GUI action traces go to `workspace/logs/gui_actions_debug.jsonl`, and bounded screenshots are written during visual checks only when `--debug-screenshots` is enabled. Debug events include `bridge_attempt_id`, phase, app/profile, action or state name, result, and error when available. The local-agent submit guard blocks submit with `local_agent_prompt_empty`, `local_agent_paste_not_attempted`, or `local_agent_click_succeeded_but_paste_missing` before any Codex submit attempt.

Shared visual PM controller policy:

- `chatgpt_mac` and `chatgpt_chrome_app` use the same production PM visual sequence.
- The sequence is profile selection, app/window resolution, visual state detection, retry policy, plus-anchor composer focus, clipboard set/readback, paste retry, `COMPOSER_HAS_TEXT` verification, PM submit, response wait, PM reactivation, response-copy detection/click, PM response save, `CODEX_NEXT_PROMPT` extraction, and Codex handoff.
- The profiles differ only by target resolver, asset profile, thresholds, and response-copy assets.
- `chatgpt_mac` targets `com.openai.chat` with `assets/gui/chatgpt_mac/`.
- `chatgpt_chrome_app` targets a selected `com.google.Chrome.app.*` PWA process with `assets/gui/chatgpt_chrome_app/`.
- Production visual PM profiles do not use Chrome DOM JavaScript and do not silently fall back across profiles.

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
