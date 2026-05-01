# External GUI Runner

Agent Bridge GUI automation should be launched from a normal macOS Terminal, not from an active Codex task process.

The Codex execution context may include markers such as `CODEX_SANDBOX`, `CODEX_SHELL`, and `CODEX_THREAD_ID`. `CODEX_SANDBOX` is a hard block for GUI automation. `CODEX_SHELL` and `CODEX_THREAD_ID` without `CODEX_SANDBOX` indicate a Full Access Codex context; this mode is allowed only after app activation, clipboard, and SafetyGate preflights pass.

## Responsibility Split

Codex may implement, test, stage prompts, inspect queue state, and write reports.

The external GUI runner is responsible for GUI side effects:

- app activation;
- clipboard writes/reads;
- paste;
- submit;
- response capture.

Agent Bridge state, queue, report, inbox, and outbox files remain shared through this repository workspace.

## Preflight From Codex

Codex may run this diagnostic command:

```bash
python -m agent_bridge.cli preflight-external-runner
python -m agent_bridge.cli preflight-iterm-ghost-runner
```

If it reports that Codex sandbox markers are present, do not run GUI automation from that process.

For a Full Access Codex context, run:

```bash
python -m agent_bridge.cli preflight-report-roundtrip
```

This command confirms `CODEX_SANDBOX` is not set, checks clipboard tools, activates the configured PM assistant and local-agent apps, and checks SafetyGate against the PM prompt. It does not paste or submit.

It also verifies the configured PM assistant backend. The backend preflight must prove that the target can run harmless DOM JavaScript through Apple Events and query the ChatGPT composer/send/streaming/copy selectors before a full roundtrip is allowed.

## Run From Normal Terminal

Open a normal macOS Terminal and run:

```bash
cd /path/to/agent-bridge-portable-handoff
bash scripts/run_gui_roundtrip_external.sh
```

The script refuses to run if `CODEX_SANDBOX` is set. If only `CODEX_SHELL` or `CODEX_THREAD_ID` are set, it warns and continues only after preflights pass. It then runs:

```bash
python -m agent_bridge.cli preflight-external-runner
python -m agent_bridge.cli preflight-gui-apps --pm-app "Google Chrome" --activate
python -m agent_bridge.cli preflight-pm-backend --activate
python -m agent_bridge.cli preflight-gui-apps --local-agent-app "Codex" --activate
```

Only after activation and PM backend preflights pass does it run:

```bash
python -m agent_bridge.cli dogfood-report-roundtrip \
  --auto-confirm \
  --max-cycles 1 \
  --max-runtime-seconds 180 \
  --submit-local-agent \
  --stop-after-local-agent-submit \
  --no-artifact-confirmation-wait
```

For queue-handoff dogfood, the stop-after-submit flags prevent the runner from waiting for the no-op success artifact in the same active Codex task. The submitted Codex prompt writes the success report after Codex becomes idle.

## Safety Rules

The external runner must not be used to bypass SafetyGate or CommandQueue. If SafetyGate blocks a prompt, the run stops.

The external runner must not mutate GitHub, send Gmail, push commits, auto-merge, or run without max-cycle and max-runtime bounds.

Do not retry the full GUI roundtrip until `preflight-external-runner`, PM backend preflight, and both activation preflights pass from a normal Terminal.

Codex activation alone is not enough for a verified local-agent handoff. Agent Bridge must focus the Codex prompt composer and verify that the staged local-agent prompt appears in the input before submit. Use these read-only diagnostics before a live retry:

```bash
python -m agent_bridge.cli diagnose-codex-ui
python -m agent_bridge.cli dump-codex-ui-tree
python -m agent_bridge.cli diagnose-codex-windows
python -m agent_bridge.cli diagnose-codex-input-target
python -m agent_bridge.cli diagnose-codex-input-target --show-click-target
python -m agent_bridge.cli diagnose-codex-input-target --direct-plus-anchor-preview
python -m agent_bridge.cli diagnose-codex-input-target --paste-test
python -m agent_bridge.cli diagnose-macos-permissions
```

`dump-codex-ui-tree` writes `workspace/logs/codex_ui_tree.json` and `workspace/logs/codex_ui_tree.txt`. `diagnose-codex-windows` lists Codex windows, rejects tiny utility/popover windows using `min_main_window_width`, `min_main_window_height`, and `min_main_window_area`, and selects the largest plausible visible normal window. `diagnose-codex-input-target` reports the active app, selected main window bounds, rejected windows, Accessibility input candidate count, best candidate, fallback strategy, click preview, prompt-presence verification status, and whether live submit would be allowed.

Accessibility input discovery is preferred. A window-relative click fallback exists for environments where Accessibility cannot expose the composer, but it is disabled by default:

```yaml
apps:
  local_agent:
    input_focus_strategy: "window_relative_click"
    input_click_x_ratio: 0.50
    input_click_y_ratio: 0.92
    require_prompt_presence_verification: false
    allow_unverified_submit: false
```

The fallback point is computed from the selected main Codex window bounds. `--show-click-target` does not click. `--click-test` may click only when explicitly requested and still does not paste or submit. Unverified submit is blocked by default; `allow_unverified_submit: true` is a dangerous override and does not allow Agent Bridge to claim success without post-submit UI evidence.

When Codex Accessibility is opaque, visual anchor detection is the preferred diagnostic fallback before any manual coordinate strategy. Run:

```bash
python -m agent_bridge.cli diagnose-visual-state --app chatgpt_mac
python -m agent_bridge.cli diagnose-visual-state --app chatgpt_chrome_app
python -m agent_bridge.cli diagnose-visual-state --app codex
python -m agent_bridge.cli diagnose-chatgpt-app-targets
python -m agent_bridge.cli diagnose-chatgpt-app-targets --pm-target chatgpt_chrome_app
python -m agent_bridge.cli preflight-chatgpt-mac-native-target
python -m agent_bridge.cli diagnose-chatgpt-mac-windows
python -m agent_bridge.cli diagnose-chatgpt-chrome-app-windows
python -m agent_bridge.cli diagnose-chatgpt-mac-composer-text-state
python -m agent_bridge.cli diagnose-chatgpt-mac-response-capture
python -m agent_bridge.cli diagnose-response-capture --app chatgpt_chrome_app
python -m agent_bridge.cli diagnose-codex-input-target --visual-debug
```

`diagnose-visual-state` is the generic app-window-bounded asset state diagnostic. It activates the selected app, selects the main visible app window, captures only that window, searches the lower composer/control band, and never mixes profiles. ChatGPT for Mac uses `assets/gui/chatgpt_mac/chatgpt_mac_plus_button_*`, `chatgpt_mac_send_disabled_button_*`, `chatgpt_mac_send_button_*`, and `chatgpt_mac_stop_button_*`. Codex uses the corresponding `assets/gui/codex/codex_*` assets. Send-disabled maps to `IDLE`, send maps to `COMPOSER_HAS_TEXT`, stop maps to `RUNNING`, and plus maps to the composer anchor. The diagnostic reports each light and dark template separately with search region, confidence, appearance score, configured threshold, effective threshold, cap-applied status, accepted/rejected status, and rejection reason. The shared matcher caps effective visual button/control thresholds at 0.70 while preserving lower configured thresholds. ChatGPT Mac plus-anchor matching is limited to the bounded lower-left composer-control region, so a lower plus threshold can tolerate small UI overlays without matching conversation text. ChatGPT Mac selection is confidence-first and returns `AMBIGUOUS` when incompatible states are accepted within `visual_state_ambiguity_margin`; `AMBIGUOUS` blocks paste/submit instead of guessing. When the enabled-send and disabled-send assets match the same ChatGPT Mac control at near-equal confidence, Agent Bridge uses a bounded RGB appearance-score comparison only if it clearly separates the two templates. A weaker stop match must not override stronger send or send-disabled evidence. Codex state matching can use a stricter configured threshold than plus-anchor matching, but diagnostics still show the shared effective threshold used for acceptance. Both apps use a 600-second idle wait with 10-second repolling by default. Dedicated automation mode may overwrite on timeout; conservative mode aborts.

`chatgpt_mac_visual` must target the native ChatGPT Mac bundle `com.openai.chat`; it must not silently switch to Chrome or a PWA. Activation is bundle-id first: AppleScript `tell application id "com.openai.chat" to activate`, then `open -b com.openai.chat`, then the explicit app path, then verified display-name activation as the last fallback. `diagnose-chatgpt-app-targets` reports native and Chrome/PWA candidates and rejects bundle ids beginning with `com.google.Chrome`. `preflight-chatgpt-mac-native-target` reports the winning activation strategy and selected native bundle id. `diagnose-chatgpt-mac-windows` is the no-submit window-selection diagnostic for the native ChatGPT Mac target. It prefers the configured bundle id, reports every visible window with title, bounds, area, role/subrole, minimized/visible state, rejection reasons, and selected main window. It fails clearly when no plausible conversation window is available.

`diagnose-chatgpt-mac-composer-text-state` checks the ChatGPT Mac enabled-send state without submitting. It clicks the safe plus-anchor composer point, types exactly `x`, confirms `COMPOSER_HAS_TEXT`, then uses Backspace only for cleanup and confirms `IDLE`.

`diagnose-chatgpt-mac-response-capture` checks the ChatGPT Mac visual response-copy path without submitting a prompt. It captures only the selected ChatGPT Mac window and looks for `chatgpt_mac_copy_response_button_light.png` or `chatgpt_mac_copy_response_button_dark.png` under `assets/gui/chatgpt_mac/`. If the copy control is not visible, it can match `chatgpt_mac_scroll_down_button_light.png` or `chatgpt_mac_scroll_down_button_dark.png`, click the bounded scroll-down control, recapture the same ChatGPT Mac window, and retry copy-control detection. `--attempt-copy` is required before it clicks the copy button; successful capture requires a changed non-empty clipboard and writes `workspace/outbox/chatgpt_mac_response_capture.md`. If these assets are missing, or no copy control is detected after retry, response capture is unsupported and `scripts/run_gui_roundtrip_external.sh` refuses the full report roundtrip. The ChatGPT Mac workstream must not fall back to Google Chrome.

The native ChatGPT Mac + Codex PyAutoGUI asset-state-machine path has an artifact-confirmed safe no-op report roundtrip. Artifact success is narrow: it applies only to the owner-approved `AB-ROUNDTRIP-NOOP-VALIDATION` local-agent prompt, routed through CommandQueue/Dispatcher, and confirmed only when `workspace/reports/latest_agent_report.md` contains `# Agent Report: GUI Roundtrip No-Op Validation Success` with statements that no source code changed, no GitHub/Gmail/external mutation occurred, no push or auto-merge occurred, and no long or unbounded loop ran. That artifact path is not a submit-confirmation shortcut for arbitrary prompts.

Chrome app diagnostics are available as a separate PM target profile, not as fallback from ChatGPT Mac. Profile name: `chatgpt_chrome_app`. It uses PyAutoGUI click-point computation, selected-app window-bounded screenshots, and `assets/gui/chatgpt_chrome_app/` assets. If a Chrome/PWA bundle candidate is present but reports `windows=0`, diagnostics attempt `open -b <selected com.google.Chrome.app.* bundle id>` and re-enumerate windows before failing. Search regions are computed from the selected window dimensions, and matching is grayscale/multiscale with profile-specific thresholds plus bounded RGB appearance-score rejection. It does not depend on Chrome DOM JavaScript by default. In this diagnostics-only phase it must not submit a PM prompt or touch Codex. Assets are:

```text
assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_plus_button_light.png
assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_plus_button_dark.png
assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_voice_button_light.png
assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_voice_button_dark.png
assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_send_button_light.png
assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_send_button_dark.png
assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_stop_button_light.png
assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_stop_button_dark.png
assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_copy_response_button_light.png
assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_copy_response_button_dark.png
assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_scroll_down_button_light.png
assets/gui/chatgpt_chrome_app/chatgpt_chrome_app_scroll_down_button_dark.png
```

State mapping for `chatgpt_chrome_app`: voice button means `IDLE`, send button means `COMPOSER_HAS_TEXT`, stop button means `RUNNING`, plus button means `COMPOSER_ANCHOR`, copy-response button means `RESPONSE_COPY`, and scroll-down button means `SCROLL_DOWN`. Selection defaults to `apps.pm_assistant.profile: "chatgpt_mac"` and supports explicit diagnostic overrides such as `--pm-target chatgpt_mac` or `--pm-target chatgpt_chrome_app`. Selected profiles must never mix assets or silently fall back to another PM target; missing assets should fail diagnostics clearly.

The visual detector activates Codex, verifies it is frontmost, enumerates Codex windows, rejects undersized utility windows such as tiny popovers, selects the main plausible window, captures only that bounded window region, searches only the lower 35-40% composer/control region, and never matches the whole screen, which prevents false positives against ChatGPT's similar plus button. The placeholder `후속 변경 사항을 부탁하세요` is the first-priority idle-empty signal when bounded OCR/text detection is available. The plus button is the fallback anchor because it remains visible whether the composer is empty or non-empty. The matcher keeps templates app-specific: ChatGPT Mac targets use `assets/gui/chatgpt_mac/chatgpt_mac_*`, and Codex targets use `assets/gui/codex/codex_*`. If OpenCV is available, the matcher tries configured templates with grayscale matching and optional multiscale matching, then reports the selected window bounds, rejected windows, best-match bbox, confidence, threshold, template path, template size, and bounded search region. Debug images are written to `workspace/logs/` when feasible, including `codex_window_bounded_detection.png`. Fixed whole-window coordinates are unsafe as a primary strategy.

The current owner-approved Codex focus rule is `focus_strategy: "direct_plus_anchor"`. It keeps the detected Codex plus button center x-coordinate and clicks slightly above the plus button using `direct_plus_anchor_y_offset`, default 50 pixels. The point is computed only inside the selected main Codex window and rejected if it overlaps the plus button, leaves the selected window, or leaves the safe lower composer band. This rule is used for active Full Access dogfood only; the long-term unattended host remains the Terminal/iTerm ghost runner.

For active bridge development, the owner may run local Codex click/paste diagnostics from a Full Access Codex context. PyAutoGUI is the preferred Codex visual click backend because System Events click against Codex has failed with macOS Accessibility error `-25211` in this environment. The iTerm/Terminal ghost runner remains the intended long-running production host after stabilization. Use explicit backend flags when validating:

```bash
python -m agent_bridge.cli diagnose-codex-input-target --click-test --click-backend pyautogui
python -m agent_bridge.cli diagnose-codex-input-target --paste-test --click-backend pyautogui --paste-backend pyautogui
python -m agent_bridge.cli diagnose-codex-input-target --focus-target-test --click-backend pyautogui
```

`diagnose-codex-input-target --paste-test` is the narrowest live local-agent paste diagnostic. It activates Codex, runs the visual composer readiness state machine, uses the selected visual focus path, copies `AGENT_BRIDGE_CODEX_PASTE_TEST_DO_NOT_SUBMIT` to the clipboard, pastes it with the configured paste backend, then captures a fresh bounded Codex-window screenshot and searches only the lower composer band for the marker. Codex paste defaults to PyAutoGUI. Paste-test tries `command-v`, `cmd-v`, explicit `keyDown("command")`/`press("v")`/`keyUp("command")`, and explicit `cmd` keyDown/keyUp variants before using a diagnostic-only `pyautogui.write()` fallback for the short ASCII marker. Full local-agent prompts never use typewrite fallback. System Events paste is available through explicit `--paste-backend system_events` or config override. The marker search is the reusable Codex prompt-presence detector prepared for future local-agent handoff gating. OCR is optional; if unavailable, marker presence is reported as unknown and `workspace/logs/codex_marker_presence.png`, `workspace/logs/codex_marker_presence_annotated.png`, and `workspace/logs/codex_marker_presence_ocr.txt` are written for owner inspection when feasible. The Python `pytesseract` package, system `tesseract` executable, and `eng`/`kor` language data are diagnosed separately. English OCR is enough for the paste marker; Korean OCR is required for the placeholder. It must not submit, press Enter/Return, run a queued command, mutate GitHub, or send Gmail. Marker detection does not enable submit. If a failed variant leaves a literal `v` or partial marker, paste-test may use Command-A/Backspace cleanup only. If marker presence or cleanup is not detectable, clear the Codex composer manually if the marker is visible.

If paste-test variants do not make the marker visible, run `diagnose-codex-input-target --focus-target-test --click-backend pyautogui`. This diagnostic compares placeholder-derived points, plus-anchor y-offsets, composer-band safe points, and owner-reviewed candidates. Owner-reviewed candidates are still bounded to the selected main window and safe composer band, and may use main-window ratios, composer-band ratios, or plus-anchor offsets. It types only `x`, runs bounded OCR after each candidate, and cleans up with Backspace only. It does not paste a full marker, submit, press Enter/Return, or run a queued command. Use its selected candidate and `workspace/logs/codex_focus_target_comparison.json` before changing any live handoff policy.

When System Events reports `-25211`, run:

```bash
python -m agent_bridge.cli diagnose-macos-permissions
```

This command prints the executable path, Python path, parent process chain, shell, user, Codex sandbox/Full Access markers, Terminal/iTerm context, `osascript` path, and read-only System Events probes. It does not perform a click. Grant Accessibility to the app that hosts the runner process: Codex when running from the Full Access Codex context, or Terminal/iTerm2 when running the external runner. Also grant Automation from that runner app to System Events and Codex. If macOS TCC state appears stale, owner-run reset options are:

```bash
tccutil reset Accessibility
tccutil reset AppleEvents
```

Do not run resets from Agent Bridge automation. After permission changes, quit and reopen the runner app, then rerun `diagnose-macos-permissions` and `diagnose-codex-input-target --paste-test`.

Before local-agent paste, Agent Bridge waits for the Codex composer placeholder `후속 변경 사항을 부탁하세요`. The visual state machine polls every 10 seconds for up to 600 seconds by default. Each poll reactivates/rechecks Codex, rereads selected main window bounds, captures a fresh bounded screenshot, and reruns placeholder detection. The default local-agent policy assumes a dedicated Agent Bridge automation session. If the placeholder stays absent until timeout, Agent Bridge may perform a controlled overwrite: detect the plus button, compute a click point above it from `plus_anchor_x_offset` and `plus_anchor_y_offset`, click the composer area, select existing composer text, and replace it with the staged prompt. The plus button is only an anchor and must not be clicked directly.

Use conservative stop mode when the Codex window may contain user work:

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

In conservative mode, timeout stops safely, does not paste or submit, and leaves the command pending. In all modes, submit success still requires prompt presence before submit and post-submit UI evidence such as input clearing, a new user message, or a running/responding state. The only narrow exception is owner-approved no-op dogfood: `allow_unverified_submit_for_noop_dogfood: true` can submit only a prompt containing `AB-ROUNDTRIP-NOOP-VALIDATION` plus the required no-GitHub/no-Gmail/no-code-change constraints, and success is accepted only if `workspace/reports/latest_agent_report.md` later contains `# Agent Report: GUI Roundtrip No-Op Validation Success`.

## External Runner Daemon

If Computer Use cannot control Terminal or iTerm2, do not depend on Computer Use terminal access. Start the external GUI runner once from a normal macOS Terminal or later from a LaunchAgent:

```bash
python -m agent_bridge.cli run-external-gui-runner \
  --auto-confirm \
  --watch-reports \
  --watch-queue \
  --polling-interval-seconds 3 \
  --max-runtime-seconds 3600
```

Helper scripts are available:

```bash
bash scripts/start_external_gui_runner.sh
bash scripts/status_external_gui_runner.sh
bash scripts/stop_external_gui_runner.sh
```

The runner watches:

```text
workspace/reports/latest_agent_report.md
workspace/queue/pending_commands.jsonl
workspace/triggers/report_roundtrip.request
workspace/triggers/queue_dispatch.request
```

The daemon records baseline file mtimes at startup. It does not immediately run on existing report or queue files; it runs when a watched file changes after startup or when a trigger marker appears. Each trigger batch causes at most one bounded report roundtrip. Trigger marker files are renamed to `.consumed.<timestamp>` after a run.

The runner uses:

```text
workspace/state/external_runner.lock
workspace/logs/external_gui_runner.log
workspace/logs/bridge.jsonl
```

The lock prevents overlapping GUI roundtrips. Stale locks are replaced based on the configured stale-lock timeout. Cooldown and debounce settings prevent repeated immediate runs.

The runner still refuses `CODEX_SANDBOX`. `CODEX_SHELL` or `CODEX_THREAD_ID` without `CODEX_SANDBOX` are treated as Full Access Codex context markers; they warn but do not block when app and clipboard preflights pass.

The runner still does not mutate GitHub, send Gmail, push commits, auto-merge, bypass SafetyGate, bypass CommandQueue, or run without max-runtime bounds.

## iTerm/Terminal Ghost Runner

The production-oriented GUI host is the iTerm/Terminal ghost runner. It is started manually from a normal terminal session, watches Agent Bridge workspace files, and performs the bounded ChatGPT-to-Codex GUI bridge outside the active Codex-hosted process.

Run from iTerm/Terminal:

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

The preflight prints the parent process chain and refuses Codex-hosted execution with:

```text
This preflight must be run from iTerm/Terminal for the ghost runner path.
```

`run-iterm-ghost-runner` refuses `CODEX_SANDBOX`, rejects Codex-hosted execution, and allows only a Terminal/iTerm-hosted process to continue. iTerm/Terminal must have Accessibility permission, and Automation permission to control System Events, Google Chrome/ChatGPT target, and Codex.

The ghost runner watches `workspace/reports/latest_agent_report.md`. It uses content hashes rather than mtime alone:

```text
workspace/state/last_processed_report_hash
workspace/state/ghost_runner.lock
workspace/logs/external_gui_runner.log
workspace/logs/bridge.jsonl
```

The hash guard prevents retriggering on already processed report content, the lock prevents concurrent roundtrips, cooldown/debounce prevent immediate repeats, and `--max-roundtrips 1` is the default dogfood bound. A report written by the no-op validation does not create an unbounded feedback loop because the runner stops after the configured max roundtrips.

Helper scripts:

```bash
bash scripts/start_iterm_ghost_runner.sh
bash scripts/status_iterm_ghost_runner.sh
bash scripts/stop_iterm_ghost_runner.sh
```

The start script runs foreground by default. Add `--background` to write `workspace/state/iterm_ghost_runner.pid` and append output to `workspace/logs/external_gui_runner.log`.

## Foreground Bridge Runner

The recommended user-facing runner is a foreground terminal process, not a background daemon. It watches `workspace/reports/latest_agent_report.md` by content hash and triggers on any post-startup report content change by default. At startup, the current report hash is recorded as the session baseline and ignored by default; pass `--process-existing-trigger` only when the owner intentionally wants to process the report that already exists at runner startup. `--require-trigger-marker` is optional compatibility/safety mode for requiring `AGENT_BRIDGE_GUI_ROUNDTRIP_TEST`.

Preflight:

```bash
python -m agent_bridge.cli preflight-run-bridge --pm-target chatgpt_mac
python -m agent_bridge.cli preflight-run-bridge --pm-target chatgpt_chrome_app
```

Run:

```bash
python -m agent_bridge.cli run-bridge \
  --pm-target chatgpt_mac \
  --watch-report workspace/reports/latest_agent_report.md \
  --polling-interval-seconds 3
```

```bash
python -m agent_bridge.cli run-bridge \
  --pm-target chatgpt_chrome_app \
  --watch-report workspace/reports/latest_agent_report.md \
  --polling-interval-seconds 3
```

or:

```bash
bash scripts/run_bridge.sh --pm-target chatgpt_mac
bash scripts/run_bridge.sh --pm-target chatgpt_chrome_app
```

The runner persists loop-prevention state in `workspace/state/foreground_bridge_runner.lock`, `workspace/state/last_seen_report_hash`, and `workspace/state/last_processed_report_hash`. It logs concise terminal status, appends structured events to `workspace/logs/bridge.jsonl`, and writes foreground-runner text logs to `workspace/logs/foreground_bridge_runner.log`. Ctrl-C exits cleanly. No LaunchAgent is created or started.

For owner debugging, add `--debug`:

```bash
python -m agent_bridge.cli run-bridge \
  --pm-target chatgpt_chrome_app \
  --watch-report workspace/reports/latest_agent_report.md \
  --polling-interval-seconds 3 \
  --debug
```

Debug mode writes state-machine events to `workspace/logs/gui_state_machine_debug.jsonl` and GUI action events to `workspace/logs/gui_actions_debug.jsonl`, with `bridge_attempt_id` on each debug event. `--debug-screenshots` additionally writes bounded screenshot artifacts during visual checks. The Codex handoff logs staged prompt length/hash, clipboard set, paste attempt/result, and submit guard result. Submit is blocked if the prompt is empty, paste was not attempted, or the click/focus path succeeded but paste did not report success.

`run-bridge` startup is deliberately light. It records the current report hash, prints readiness, and waits. It does not preflight both PM profiles, scan all assets, or run PM/Codex visual diagnostics at startup. Live app/window resolution and visual diagnostics run only when a post-startup report change starts a bridge attempt, unless the owner explicitly runs `preflight-run-bridge`.

## Shared PM Visual Policy

The Chrome/PWA PM path is the canonical visual sequence and native ChatGPT Mac uses the same production mechanism. Both profiles perform:

- selected profile resolution;
- profile-specific app/window selection;
- bounded screenshot visual state detection;
- state-machine wait/retry;
- plus-anchor composer focus;
- clipboard set/readback;
- PyAutoGUI paste retry;
- `COMPOSER_HAS_TEXT` verification;
- PM submit;
- response generation wait;
- PM reactivation before response-copy;
- response-copy detection/click;
- `pm_response.md` save;
- `CODEX_NEXT_PROMPT` extraction;
- SafetyGate, CommandQueue, Dispatcher, and Codex handoff.

The only expected profile differences are target resolution, asset set, thresholds, and response-copy assets. `chatgpt_mac` targets `com.openai.chat` and `assets/gui/chatgpt_mac/`; `chatgpt_chrome_app` targets the selected `com.google.Chrome.app.*` process and `assets/gui/chatgpt_chrome_app/`. Production visual PM profiles do not use Chrome DOM JavaScript and must not fall back across profiles.

## Prompt And Report Style

Future PM-to-Codex bridge prompts should stay concise: current task goal, constraints, execution steps, relevant tests, and report expectations. Standing rules belong in `AGENTS.md`, the Agent Bridge README, and the Codex skill. The `CODEX_NEXT_PROMPT` fence plus first-body-line label remains required for copy-safe extraction.

Agent reports should be decision-useful: summary, changed files, checks run, pass/fail result, exact blocker or failure point, and next recommended task. Avoid long narrative unless it contains debug evidence needed for the next owner decision.

## Future Roadmap

Planned work after stabilization: reusable repo packaging, Skill installation documentation, external-project `.agent-bridge/` usage, a ChatGPT-to-local-Codex-to-GitHub/Codex-review workflow, and cleanup of temporary dogfood scaffolding once the bridge is stable.

## LaunchAgent Template

A template is provided but is not installed automatically:

```text
packaging/macos/com.agentbridge.runner.plist.template
```

Review and edit paths before installing it manually with macOS LaunchAgent tooling.

## Computer Use Terminal Trigger

Computer Use may be used only to start the external runner command in an already-open normal Terminal. It must not operate ChatGPT or Codex directly.

Prepare the trigger file:

```bash
python -m agent_bridge.cli prepare-computer-use-terminal-trigger
python -m agent_bridge.cli show-computer-use-terminal-trigger
```

The file is written to:

```text
workspace/outbox/computer_use_terminal_trigger.md
```

Computer Use should follow only the instructions in that file:

- focus an already-open normal macOS Terminal outside the restricted Codex sandbox;
- paste exactly one shell command;
- press Enter once;
- stop.

Computer Use must not manually copy ChatGPT responses, paste into Codex, submit prompts in either app, or perform any part of the ChatGPT-to-Codex handoff itself. Agent Bridge remains responsible for the full GUI roundtrip, SafetyGate checks, CommandQueue usage, one-cycle bounds, and event logging.

## ChatGPT HTML State Machine

The PM assistant bridge uses observed ChatGPT HTML signals before submitting or copying:

- empty composer: `data-testid="composer-speech-button"` or `aria-label="Voice 시작"`;
- send ready: `data-testid="send-button"`, `id="composer-submit-button"`, or `aria-label="프롬프트 보내기"`;
- streaming: `data-testid="stop-button"` or `aria-label="스트리밍 중지"`;
- response copy ready: `data-testid="copy-turn-action-button"` or `aria-label="응답 복사"`.

For the Google Chrome backend, the bridge first waits up to 600 seconds for the ChatGPT composer to be idle-empty: `data-testid="composer-speech-button"` or `aria-label="Voice 시작"`. During that window it polls every 10 seconds by default. If the stop button is visible, ChatGPT is still generating and Agent Bridge waits. If the send button is visible before Agent Bridge paste, a user has a pending composed message and Agent Bridge waits rather than clearing or overwriting it. Only after idle-empty is detected does the bridge focus a visible ChatGPT composer, insert the PM prompt through DOM JavaScript, dispatch input/change events, and verify that the composer text is non-empty and contains an expected Agent Bridge marker. It then waits for send-ready and refuses to submit if the composer remains in the empty voice-button state. After submit it waits while the stop button is present, then clicks the copy button inside the latest assistant response container. It verifies that the clipboard changed, the copied text is non-empty, and, for report roundtrips, that the copied response contains `CODEX_NEXT_PROMPT`. The PM response contract also requires the first non-empty line inside the returned code block body to be exactly `CODEX_NEXT_PROMPT`, so native ChatGPT Mac response-copy remains extractable when it copies only the rendered block body and omits the Markdown fence info string.

If composer focus, text verification, or send-ready detection fails, Agent Bridge logs the composer selector used, the active element summary, composer text length, and current button state.

Before paste or submit, `preflight-pm-backend` must pass for the configured backend:

```bash
python -m agent_bridge.cli preflight-pm-backend --dry-run
python -m agent_bridge.cli preflight-pm-backend --activate
```

Supported backend identifiers are `chrome_js`, `chatgpt_pwa_js`, `browser_apple_events`, `accessibility_fallback`, and `unsupported`. Use `Google Chrome` with `chrome_js` for the current DOM JavaScript bridge; the ChatGPT app/PWA backend is unsupported for Chrome tab JavaScript. Chrome backends run a safe `document.readyState` JavaScript probe through Apple Events using the nested `tell active tab of front window` form. If this fails, enable JavaScript from Apple Events in the browser or configure a different supported PM assistant target. The full report roundtrip aborts before paste/submit when backend preflight fails.

Fallback response copy strategies are tried in this order:

1. copy button inside the latest assistant response container;
2. owner-provided CSS selector;
3. owner-provided XPath;
4. owner-provided full XPath;
5. generic copy fallback.
6. latest assistant response DOM text extraction copied through `pbcopy`.

Owner-provided selectors are intentionally treated as brittle fallbacks. Prefer the latest assistant response container when possible. The DOM text fallback is used only after button-based copy strategies fail to update the clipboard, and it still requires non-empty text plus `CODEX_NEXT_PROMPT` when that marker is expected.

## Verify Results

After Computer Use triggers the external command, inspect the run:

```bash
python -m agent_bridge.cli verify-roundtrip-result
```

The verifier checks the staged PM prompt, captured PM response, extracted `CODEX_NEXT_PROMPT`, staged local-agent prompt, Codex input candidate discovery, prompt presence before submit, local-agent submit event, UI confirmation, artifact confirmation for the no-op dogfood path, SafetyGate status, and one-cycle completion event. Artifact confirmation is accepted only for `AB-ROUNDTRIP-NOOP-VALIDATION` when the latest report title is `# Agent Report: GUI Roundtrip No-Op Validation Success` and the report states no source-code changes, no GitHub/Gmail/external mutation, no push or auto-merge, and no long or unbounded loop. Stop-after-local-agent-submit followed by that artifact can report `full_success_basis: artifact_confirmed`; arbitrary prompts cannot. If the run failed, it reports the likely failure point. Codex may fix Agent Bridge code or config and retry, but retry attempts should be capped at three and success must not be claimed unless the full one-cycle roundtrip completed or the safe no-op stop-for-queue artifact was confirmed.

Local-agent submit is split into attempted and confirmed. Attempted means Agent Bridge sent the submit action. Confirmed requires Codex UI evidence: input cleared, a new user message was detected, or a running/responding state was detected. If macOS Accessibility does not expose those signals, Agent Bridge reports the submit as unconfirmed instead of claiming success. Use:

```bash
python -m agent_bridge.cli diagnose-codex-ui
```

to inspect whether Codex UI input, conversation text, and running/responding indicators are detectable. The diagnostic does not paste or submit.

If the verifier reports `local_agent_prompt_present_before_submit: no` or `local_agent_prompt_presence_verifiable: no`, do not rerun the live roundtrip until `diagnose-codex-input-target` shows a plausible Accessibility candidate or an explicitly configured fallback has been tested.

## Prompt and Report Style

PM-to-Codex prompts should stay concise and task-focused. Put standing rules in
`AGENTS.md`, this Skill, or bridge docs; each generated prompt should include
only the current goal, constraints, execution steps, tests, and report
expectations. Keep the `CODEX_NEXT_PROMPT` fence/body-label compatibility
contract intact.

`workspace/reports/latest_agent_report.md` should read like a concise PM-to-CTO
status note: what changed, what passed, what failed, the exact blocker if any,
commands run, and the next recommended task. Use longer narrative only when it
is needed for debugging evidence.
