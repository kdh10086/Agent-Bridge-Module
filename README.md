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

## Command Queue

Bridge producers stage local-agent work in `workspace/queue/`. Dispatcher is the only component
that consumes commands for local-agent handoff. Queue files are durable JSONL/state files:

- `pending_commands.jsonl`
- `in_progress.json`
- `completed_commands.jsonl`
- `failed_commands.jsonl`
- `blocked_commands.jsonl`
- `malformed_commands.jsonl`

Each command record includes `id`, `created_at`, `source`, `prompt_path` or `prompt_text`,
`status`, `priority`, and `metadata`. Supported statuses are `pending`, `in_progress`,
`completed`, `failed`, and `blocked`.

Queue mutations are protected by `workspace/queue/queue.lock`, an advisory file lock used for
enqueue, status transitions, malformed-record quarantine, and dedupe checks. Lock acquisition has a
bounded timeout and releases in `finally` blocks so failed writes do not leave the queue locked.
Set `AGENT_BRIDGE_QUEUE_DEBUG=1` to collect lock acquire/release diagnostics on `CommandQueue`
instances during targeted tests or diagnostics.
Dispatcher resolves prompt content in this order: `prompt_text`, `prompt_path`, then legacy
`payload_path`.

Basic queue operations:

```bash
python -m agent_bridge.cli queue enqueue \
  --type USER_MANUAL_COMMAND \
  --prompt-text "Write a concise status report." \
  --source manual

python -m agent_bridge.cli queue enqueue \
  --type CHATGPT_PM_NEXT_TASK \
  --payload workspace/outbox/extracted_codex_next_prompt.md \
  --source pm_assistant_report_roundtrip

python -m agent_bridge.cli queue list --status pending
python -m agent_bridge.cli queue list --status all
python -m agent_bridge.cli queue peek
python -m agent_bridge.cli queue mark-in-progress <command-id>
python -m agent_bridge.cli queue mark-completed
python -m agent_bridge.cli queue mark-failed --reason "manual failure reason"
python -m agent_bridge.cli queue mark-blocked --reason "requires owner decision"
python -m agent_bridge.cli queue malformed list
python -m agent_bridge.cli queue malformed inspect 1
python -m agent_bridge.cli queue repair
python -m agent_bridge.cli queue repair --apply
```

Repeated enqueue calls are deduped by `dedupe_key`. Malformed queue records are skipped and
quarantined to `workspace/queue/malformed_commands.jsonl` so a bad line does not block the whole
queue. `queue repair` is dry-run by default; `--apply` only re-enqueues quarantined raw records that
validate against the current schema and leaves the original quarantine record in place.

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
python -m agent_bridge.cli diagnose-codex-ui
python -m agent_bridge.cli dump-codex-ui-tree
python -m agent_bridge.cli diagnose-codex-input-target
python -m agent_bridge.cli diagnose-codex-input-target --show-click-target
python -m agent_bridge.cli diagnose-codex-input-target --direct-plus-anchor-preview
python -m agent_bridge.cli diagnose-codex-input-target --paste-test
python -m agent_bridge.cli diagnose-codex-input-target --focus-target-test --click-backend pyautogui
python -m agent_bridge.cli diagnose-macos-permissions
python -m agent_bridge.cli preflight-external-runner
python -m agent_bridge.cli preflight-iterm-ghost-runner
python -m agent_bridge.cli preflight-pm-backend --dry-run
python -m agent_bridge.cli preflight-pm-backend --activate
python -m agent_bridge.cli preflight-report-roundtrip
python -m agent_bridge.cli run-iterm-ghost-runner --help
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
    app_path: "/Applications/ChatGPT.app"
    bundle_id: "com.openai.chat"
    backend: "chatgpt_mac_visual"
    profile: "chatgpt_mac"
    require_backend_preflight: false
    idle_empty_timeout_seconds: 600
    idle_empty_poll_interval_seconds: 10
    window_hint: "ChatGPT"
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
    visual_state_confidence_threshold: 0.95
    visual_state_ambiguity_margin: 0.03
    visual_state_search_region: "lower_control_band"
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
    visual_plus_multiscale_enabled: true
    min_main_window_width: 400
    min_main_window_height: 300
    min_main_window_area: 120000
    window_selection_strategy: "largest_visible_normal"
    owner_reviewed_focus_candidates: []
    visual_text_recognition:
      enabled: true
      ocr_backend: "pytesseract"
      marker_text: "AGENT_BRIDGE_CODEX_PASTE_TEST_DO_NOT_SUBMIT"
      placeholder_text: "후속 변경 사항을 부탁하세요"
      search_region: "lower_composer_band"
```

The visual PM bridge uses one shared profile-driven sequence for
`chatgpt_mac` and `chatgpt_chrome_app`. Profiles supply only target
resolution, asset paths, thresholds, and the configured paste backend chain.
The shared paste controller distinguishes a keyboard action returning from a
verified paste: production PM paste succeeds only after `COMPOSER_HAS_TEXT` or
another explicit prompt-present signal. Raw-v-prone variants such as
`command_v_hotkey` and keyDown/press/keyUp forms are diagnostic-only for full PM
prompts.

`preflight-gui-apps --dry-run` prints the activation fallback plan without touching apps. `preflight-gui-apps --pm-app "ChatGPT" --activate` and `preflight-gui-apps --local-agent-app "Codex" --activate` try activation only; they do not paste, submit, press Enter/Return, mutate GitHub, or send Gmail. Bundle-id targets activate by AppleScript application id first, then `open -b`, then explicit `app_path`, with display-name activation only as a verified fallback. Targets without a bundle id use display-name AppleScript and `open -a`.

The primary PM assistant target is now ChatGPT for Mac with `backend: "chatgpt_mac_visual"`. Run asset-state diagnostics without paste or submit:

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
```

Both state diagnostics activate the selected app, select its main visible window, capture only that window, search the lower composer/control band, and use only that app's asset profile. `chatgpt_mac` assets are loaded from `assets/gui/chatgpt_mac/chatgpt_mac_*`; Codex assets are loaded from `assets/gui/codex/codex_*`. Send-disabled assets map to `IDLE`, send assets map to `COMPOSER_HAS_TEXT`, stop assets map to `RUNNING`, and plus assets are composer anchors. Per-template diagnostics report both light and dark assets, search region, confidence, threshold, appearance score, accepted/rejected status, and rejection reason. ChatGPT Mac plus-anchor matching is restricted to the bounded lower-left composer-control region and uses the configured `visual_plus_confidence_threshold`; this avoids false matches in conversation text while tolerating small UI overlays on the plus icon. ChatGPT Mac state selection is confidence-first: a weaker stop match does not override a stronger send/send-disabled match, and incompatible state matches within `visual_state_ambiguity_margin` return `AMBIGUOUS`, which blocks paste/submit. When ChatGPT Mac send-disabled and enabled-send templates match the same button region at near-equal confidence, Agent Bridge may resolve the state only if a bounded RGB appearance-score comparison clearly favors one template; otherwise it remains `AMBIGUOUS`. Codex uses a stricter state threshold than the plus anchor so partial send/send-disabled matches do not override stop/idle state. The idle wait policy polls every 10 seconds for up to 600 seconds. In dedicated automation mode, timeout may use the plus anchor overwrite path; conservative mode can abort instead.

For `chatgpt_mac_visual`, native ChatGPT Mac activation is bundle-id first: AppleScript `tell application id "com.openai.chat" to activate`, then `open -b com.openai.chat`, then `/Applications/ChatGPT.app`, with display-name activation only as a verified last fallback. `diagnose-chatgpt-app-targets` lists native and Chrome/PWA candidates and rejects bundle ids beginning with `com.google.Chrome`. `preflight-chatgpt-mac-native-target` reports the activation method and selected native bundle id. `diagnose-chatgpt-mac-windows` activates the configured native ChatGPT Mac app, prefers the configured bundle id, enumerates visible windows, rejects tiny utility windows, and selects the largest plausible conversation window. If no usable window exists, it reports `ChatGPT Mac visible conversation window is unavailable` and no visual state or response-copy test should proceed.

`diagnose-chatgpt-mac-composer-text-state` is a no-submit live diagnostic for ChatGPT Mac. It requires a safe plus-anchor click point, clicks the composer with PyAutoGUI, types exactly `x`, verifies `COMPOSER_HAS_TEXT`, then cleans up with Backspace only and verifies `IDLE`. It never presses Enter/Return and never submits.

`diagnose-chatgpt-mac-response-capture` is the no-submit response-copy diagnostic for the ChatGPT Mac visual backend. It is app-window bounded and looks for `assets/gui/chatgpt_mac/chatgpt_mac_copy_response_button_light.png` and `assets/gui/chatgpt_mac/chatgpt_mac_copy_response_button_dark.png`. If the copy button is not visible, it can use `chatgpt_mac_scroll_down_button_light.png` or `chatgpt_mac_scroll_down_button_dark.png` to click the bounded scroll-down control, recapture the ChatGPT Mac window, and retry copy-button detection. Add `--attempt-copy` to explicitly click the detected copy button, verify the clipboard changed to non-empty text, and write `workspace/outbox/chatgpt_mac_response_capture.md` only when diagnostic capture succeeds. If these assets are missing or no latest-response copy control is detected after retry, ChatGPT Mac response capture is reported as unsupported and full roundtrip must not run. This workstream must not fall back to Google Chrome.

The native ChatGPT Mac + Codex PyAutoGUI asset-state-machine path has one artifact-confirmed safe no-op roundtrip. The accepted success path is narrow: ChatGPT Mac stages and submits the PM prompt, captures a copy-safe `CODEX_NEXT_PROMPT`, CommandQueue/Dispatcher stages the local-agent prompt, Codex receives the owner-approved `AB-ROUNDTRIP-NOOP-VALIDATION` prompt, and `workspace/reports/latest_agent_report.md` is later written with `# Agent Report: GUI Roundtrip No-Op Validation Success`. This artifact confirmation is valid only for the no-op validation prompt and only when the success report states no source-code changes, no GitHub/Gmail/external mutation, no push or auto-merge, and no long or unbounded loop. Arbitrary local-agent tasks still require the normal SafetyGate, CommandQueue/Dispatcher, prompt-presence, and submit-confirmation policy.

Chrome app profile diagnostics reuse the same PyAutoGUI/window-bounded asset-state-machine architecture rather than Chrome DOM JavaScript by default. The profile name is `chatgpt_chrome_app`, with explicit selection through `apps.pm_assistant.profile: "chatgpt_mac"` by default and CLI override `--pm-target chatgpt_mac` or `--pm-target chatgpt_chrome_app` where supported. `chatgpt_mac` uses the native bundle id `com.openai.chat` and `assets/gui/chatgpt_mac/`; `chatgpt_chrome_app` selects a Chrome/PWA ChatGPT app bundle such as `com.google.Chrome.app.*` and uses `assets/gui/chatgpt_chrome_app/`. If a Chrome/PWA candidate is present but reports `windows=0`, the target diagnostic tries `open -b <selected com.google.Chrome.app.* bundle id>`, waits briefly, and re-enumerates System Events windows; generic Google Chrome is still rejected. Chrome app search regions are computed from the selected window dimensions, not fixed screen coordinates, and templates are matched in grayscale across the configured scale range with bounded RGB appearance-score rejection for false positives. Asset sets must never be mixed, and Agent Bridge must fail diagnostics clearly when a selected profile is missing assets instead of silently falling back to another PM target. These Chrome app diagnostics are no-submit: they may activate the selected app, enumerate windows, capture bounded screenshots, report click points, and detect response-copy controls, but they do not submit a PM prompt or touch Codex.

For bounded Chrome/PWA window-size diagnostics, `set-app-window-bounds --app chatgpt_chrome_app --bounds x,y,width,height` and `resize-chatgpt-chrome-app-window --bounds x,y,width,height` operate only on the selected `com.google.Chrome.app.*` target. They set System Events window position/size, re-enumerate actual bounds, and do not paste, submit, click response-copy, or touch Codex.

`chatgpt_chrome_app` assets:

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

For that future profile, voice-button assets map to `IDLE`, send-button assets map to `COMPOSER_HAS_TEXT`, stop-button assets map to `RUNNING`, plus-button assets map to `COMPOSER_ANCHOR`, copy-response assets map to `RESPONSE_COPY`, and scroll-down assets map to `SCROLL_DOWN`.

The PM assistant also has an explicit GUI backend. Supported backend names are `chrome_js`, `chatgpt_pwa_js`, `browser_apple_events`, `accessibility_fallback`, and `unsupported`. Use `Google Chrome` with `backend: "chrome_js"` for the DOM JavaScript bridge; the ChatGPT app/PWA backend is marked unsupported for Chrome tab JavaScript until a future backend proves otherwise. Active JS backends must prove that Apple Events can run harmless DOM JavaScript before a full report roundtrip starts:

```bash
python -m agent_bridge.cli preflight-pm-backend --dry-run
python -m agent_bridge.cli preflight-pm-backend --activate
```

For Chrome targets, the preflight runs a safe probe equivalent to `document.readyState` using the nested AppleScript form `tell application "Google Chrome" / tell active tab of front window / execute javascript ...`, then checks that composer/send/streaming/copy selectors are queryable. If this fails, enable the browser setting that allows JavaScript from Apple Events or configure a different supported PM assistant target. A full `dogfood-report-roundtrip` aborts before paste/submit when `require_backend_preflight: true` and this backend proof fails.

If activation fails, diagnose the app bundle before retrying:

```bash
python -m agent_bridge.cli diagnose-gui-app --app-path /Applications/ChatGPT.app
python -m agent_bridge.cli diagnose-gui-app --app-path /Applications/Codex.app
python -m agent_bridge.cli diagnose-gui-apps
```

Diagnostics inspect path existence, `Contents/Info.plist`, `Contents/MacOS`, the executable named by `CFBundleExecutable`, bundle identifiers, LaunchServices visibility, and the current process context. The output includes a suggested `config/local.yaml` block. `--activate` may be added to run the same activation fallback attempts, but diagnostics still never paste, submit, press Enter/Return, mutate GitHub, or send Gmail.

Use `diagnose-codex-ui` to inspect local Codex UI Accessibility state without paste or submit:

```bash
python -m agent_bridge.cli diagnose-codex-ui
```

It reports whether Codex is active, whether the focused input is detectable, whether conversation text is detectable, whether running/responding indicators are visible, and any Accessibility limitation.

Codex activation is not enough for a verified local-agent handoff. Agent Bridge must focus the Codex prompt composer, paste the staged local-agent prompt, and verify that the prompt text is present before submit. Use these diagnostics when Codex Accessibility is opaque:

```bash
python -m agent_bridge.cli dump-codex-ui-tree
python -m agent_bridge.cli diagnose-codex-windows
python -m agent_bridge.cli diagnose-codex-input-target
python -m agent_bridge.cli diagnose-codex-input-target --show-click-target
python -m agent_bridge.cli diagnose-codex-input-target --direct-plus-anchor-preview
```

`dump-codex-ui-tree` writes `workspace/logs/codex_ui_tree.json` and `workspace/logs/codex_ui_tree.txt` without paste or submit. `diagnose-codex-windows` enumerates visible Codex windows, rejects tiny utility/popover windows by size/area, and selects the largest plausible normal window for composer targeting. `diagnose-codex-input-target` reports Codex frontmost state, selected main window bounds, rejected windows, Accessibility candidate count, selected candidate, fallback configuration, whether prompt presence verification is possible, and whether live submit would be allowed.

The active Codex development path can use the owner-approved direct plus-anchor rule. It is still window-bounded: Agent Bridge activates Codex, selects the main Codex window, detects the Codex plus button inside that window only, and computes `click_x = plus_center_x + direct_plus_anchor_x_offset`, `click_y = plus_center_y - direct_plus_anchor_y_offset`. The default y offset is 50 pixels. The click point is rejected if it overlaps the plus button, leaves the selected main window, or leaves the safe lower composer band. `--direct-plus-anchor-preview` reports the computed point without clicking.

Accessibility discovery is preferred. A guarded manual fallback can be enabled only through `config/local.yaml`:

```yaml
apps:
  local_agent:
    input_focus_strategy: "window_relative_click"
    input_click_x_ratio: 0.50
    input_click_y_ratio: 0.92
    require_prompt_presence_verification: false
    allow_unverified_submit: false
```

The fallback computes a click point relative to the selected main Codex window, not absolute screen coordinates. It is disabled by default. `--show-click-target` previews the point without clicking; `--click-test` explicitly clicks the configured point but still does not paste or submit. Unverified submit remains blocked unless `allow_unverified_submit: true` is explicitly configured. The only built-in narrow exception is `allow_unverified_submit_for_noop_dogfood: true`, which applies only to the safe `AB-ROUNDTRIP-NOOP-VALIDATION` prompt and still requires the expected no-op success report artifact before verification can pass.

When Codex Accessibility does not expose the composer, Agent Bridge uses visual anchor diagnostics before any owner-reviewed fallback. `diagnose-codex-input-target --visual-debug` activates Codex, verifies it is frontmost, selects the main Codex window instead of blindly using the front utility window, captures only those selected bounds, searches only the lower composer band, excludes conservative unsafe regions, and reports whether a plus-button visual anchor or placeholder anchor is available. The plus button is preferred because it is visible even when the composer contains text; the placeholder is the first-priority idle-empty signal when visual text/OCR detection is available. OpenCV matching uses grayscale matching, optional multiscale matching, and all configured owner-reviewed templates in `visual_plus_templates`. Keep templates app-specific: ChatGPT targets use only `chatgpt_plus_button_light.png` and `chatgpt_plus_button_dark.png`; Codex targets use only `codex_plus_button_light.png` and `codex_plus_button_dark.png`. Diagnostics report selected window bounds, rejected tiny windows, template path, template size, best-match bbox, best-match confidence, threshold, and search region. If no template matches, provide a new tight crop of the current app's plus button only; do not include private text. Debug images may be written to `workspace/logs/codex_visual_detection.png`, `workspace/logs/codex_visual_detection_annotated.png`, `workspace/logs/codex_window_bounded_detection.png`, and `workspace/logs/codex_window_bounded_detection_annotated.png` when screenshot capture is available. Fixed whole-window coordinates and whole-screen matching are not used as the primary strategy, and unverified submit remains disabled by default.

During active bridge development, the owner may run these Codex local-agent diagnostics from a Full Access Codex context. The intended long-term production host remains the iTerm/Terminal ghost runner after the bridge stabilizes. For Codex visual plus-anchor targeting, PyAutoGUI is the default click backend because System Events click is unreliable against Codex in this environment. Override only for diagnostics:

```bash
python -m agent_bridge.cli diagnose-codex-input-target --click-test --click-backend pyautogui
python -m agent_bridge.cli diagnose-codex-input-target --paste-test --click-backend pyautogui --paste-backend pyautogui
```

`diagnose-codex-input-target --paste-test` is a paste-only diagnostic. It first runs the bounded Codex composer readiness policy: detect the idle placeholder, otherwise poll, and only use the configured timeout fallback when allowed. It then uses the selected visual focus path, copies the harmless marker `AGENT_BRIDGE_CODEX_PASTE_TEST_DO_NOT_SUBMIT`, pastes it into Codex, and captures a fresh screenshot scoped to the selected main Codex window and lower composer band. Codex paste defaults to PyAutoGUI and tries `command-v`, `cmd-v`, explicit `keyDown("command")`/`press("v")`/`keyUp("command")`, and explicit `cmd` keyDown/keyUp variants. If those variants do not make the short ASCII diagnostic marker visible, paste-test may use `pyautogui.write()` for that marker only; full local-agent prompts never use typewrite fallback. System Events paste remains available only through explicit `--paste-backend system_events` or config override. Marker-presence detection uses the same reusable bounded OCR prompt-presence API intended for future local-agent handoff gating. If OCR is unavailable, it reports `unknown` instead of guessing and writes bounded debug images to `workspace/logs/codex_marker_presence.png` and `workspace/logs/codex_marker_presence_annotated.png` plus OCR text/diagnostics to `workspace/logs/codex_marker_presence_ocr.txt` for owner inspection. The Python `pytesseract` package is optional and separate from the system `tesseract` executable and language data. English OCR is sufficient for the paste marker; Korean OCR language support is needed for the placeholder `후속 변경 사항을 부탁하세요`. The command does not submit, press Enter/Return, or run a queued command. Marker detection is diagnostic only and does not enable submit. If a failed variant leaves a literal `v` or partial marker, paste-test may use Command-A/Backspace cleanup only; if the marker cannot be cleared safely, Agent Bridge prints a manual cleanup instruction.

Use `diagnose-codex-input-target --focus-target-test --click-backend pyautogui` when paste variants run but the marker is still not visible. It compares bounded Codex composer click targets derived from the placeholder bbox, plus-anchor y-offsets, composer-band safe points, and optional owner-reviewed candidates. For each safe candidate it clicks with PyAutoGUI, types only `x`, runs bounded OCR in the lower composer band, and cleans up with Backspace only. It does not paste the full marker, submit, press Enter/Return, or run a queued command. Artifacts are written to `workspace/logs/codex_focus_target_comparison.png`, `workspace/logs/codex_focus_target_comparison_annotated.png`, `workspace/logs/codex_focus_target_comparison_ocr.txt`, and `workspace/logs/codex_focus_target_comparison.json`. Owner-reviewed candidates are bounded to the selected main window and safe composer band; they may use `basis: "main_window"` ratios or `basis: "plus_anchor"` offsets:

```yaml
apps:
  local_agent:
    owner_reviewed_focus_candidates:
      - name: "composer_text_mid"
        basis: "main_window"
        x_ratio: 0.55
        y_ratio: 0.78
      - name: "composer_above_plus_mid"
        basis: "plus_anchor"
        x_offset: 80
        y_offset: 100
      - name: "composer_band_center"
        basis: "composer_band"
        x_ratio: 0.50
        y_ratio: 0.50
    allow_unverified_submit: false
```

Use `diagnose-macos-permissions` when System Events reports `-25211` or an assistive-access denial:

```bash
python -m agent_bridge.cli diagnose-macos-permissions
```

The diagnostic reports the current executable, Python path, parent process chain, shell, user, Codex/Terminal context markers, `osascript` path, and read-only System Events probes. It does not click, paste, submit, press Enter/Return, mutate GitHub, or send Gmail. Grant Accessibility to the actual runner app in System Settings > Privacy & Security > Accessibility: Codex for Full Access Codex runs, or Terminal/iTerm2 for external runner runs. In System Settings > Privacy & Security > Automation, allow that same runner app to control System Events and Codex. If macOS TCC state is stale, the owner may reset permissions manually with `tccutil reset Accessibility` and `tccutil reset AppleEvents`, then reopen the runner app and rerun the diagnostic and paste-test.

For the local Codex handoff, Agent Bridge first waits for the composer placeholder `후속 변경 사항을 부탁하세요`. The default policy assumes a dedicated Agent Bridge automation session: if the placeholder stays absent for 600 seconds, Agent Bridge may use the plus button only as an anchor, click above it by the configured offset, select existing composer text, and replace it with the staged prompt. It must not click the plus button itself, and submit remains blocked unless prompt presence and post-submit UI evidence are available. Conservative mode is available in `config/local.yaml`:

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

In conservative mode, timeout stops safely, no paste or submit is attempted, and the queued command remains pending.

The visual composer state machine re-detects every 10 seconds during the 600-second wait. Each poll reactivates/rechecks Codex, rereads selected main window bounds, captures a fresh bounded screenshot, and reruns placeholder detection. The default `composer_policy.on_busy_timeout: overwrite` treats the Codex window as a dedicated Agent Bridge automation session; set `on_busy_timeout: abort` when the window may contain user work.

GUI automation should run from a normal macOS Terminal, not from an active Codex task process. Check the environment first:

```bash
python -m agent_bridge.cli preflight-external-runner
```

If `CODEX_SANDBOX` is present, do not run GUI automation from that process. If only `CODEX_SHELL` or `CODEX_THREAD_ID` are present, Agent Bridge treats it as a Full Access Codex context and allows roundtrip execution only after app activation, PM backend, clipboard, and SafetyGate preflights pass:

```bash
python -m agent_bridge.cli preflight-report-roundtrip
```

From a normal Terminal, run:

```bash
bash scripts/run_gui_roundtrip_external.sh
```

The external runner refuses to run inside the Codex sandbox, runs activation/backend preflights for ChatGPT and activation preflight for Codex, and only then starts the bounded one-cycle report roundtrip. See `docs/EXTERNAL_GUI_RUNNER.md`.

For Computer Use assisted launches, Codex should prepare a terminal trigger instead of asking Computer Use to operate ChatGPT or Codex directly:

```bash
python -m agent_bridge.cli prepare-computer-use-terminal-trigger
python -m agent_bridge.cli show-computer-use-terminal-trigger
```

The trigger file is written to `workspace/outbox/computer_use_terminal_trigger.md`. It instructs Computer Use to focus an already-open normal macOS Terminal window, paste exactly one shell command, press Enter once, and stop. Computer Use must not copy ChatGPT responses, paste into Codex, or operate either app directly. Agent Bridge handles the PM prompt, ChatGPT submit, response capture, `CODEX_NEXT_PROMPT` extraction, queue enqueue, local-agent submit, SafetyGate checks, one-cycle bounds, and event logging.

After an external run, verify artifacts and logs:

```bash
python -m agent_bridge.cli verify-roundtrip-result
```

Verification inspects `workspace/outbox/pm_assistant_prompt.md`, `workspace/outbox/pm_response.md`, `workspace/outbox/extracted_codex_next_prompt.md`, `workspace/outbox/next_local_agent_prompt.md`, and `workspace/logs/bridge.jsonl`. It distinguishes `local_agent_prompt_present_before_submit`, `local_agent_submit_attempted`, `local_agent_submit_confirmed_by_ui`, `local_agent_submit_confirmed_by_artifact`, and `local_agent_submit_confirmation_basis` (`ui_confirmed`, `artifact_confirmed`, `attempted_only`, or `none`). Attempted means the submit action was sent, while UI confirmation requires input clearing, a new user message, or a running/responding state. Artifact confirmation is accepted only for the owner-approved no-op validation path when the staged prompt is `AB-ROUNDTRIP-NOOP-VALIDATION` and `workspace/reports/latest_agent_report.md` contains `# Agent Report: GUI Roundtrip No-Op Validation Success` plus the required no-mutation statements. A stop-after-local-agent-submit run followed by that safe no-op artifact may report `full_success_basis: artifact_confirmed`; arbitrary tasks cannot use this artifact path. The verifier reports Codex input candidate discovery, focus strategy, whether prompt presence was verifiable, the confirmation signal, and any submit-block reason. If prompt presence or submit confirmation is unavailable outside that no-op artifact path, full roundtrip success is not claimed. If verification fails, it reports the likely failure point, such as PM backend preflight, ChatGPT send-ready detection, response copy, `CODEX_NEXT_PROMPT` extraction, Codex prompt presence, Codex submit confirmation, or SafetyGate block. Retry attempts should be limited and should only follow code/config fixes when the failure is inside Agent Bridge.

If Computer Use is blocked from Terminal/iTerm2, use the external GUI runner daemon/file-trigger mode instead. Start it once from a normal macOS Terminal:

```bash
bash scripts/start_external_gui_runner.sh
```

Or run it in the foreground:

```bash
python -m agent_bridge.cli run-external-gui-runner \
  --auto-confirm \
  --watch-reports \
  --watch-queue \
  --polling-interval-seconds 3 \
  --max-runtime-seconds 3600
```

The runner refuses `CODEX_SANDBOX`, warns but allows Full Access Codex markers when preflights pass, watches `workspace/reports/latest_agent_report.md`, watches `workspace/queue/pending_commands.jsonl`, and also accepts trigger files:

```text
workspace/triggers/report_roundtrip.request
workspace/triggers/queue_dispatch.request
```

Each trigger causes at most one bounded report roundtrip. A lock at `workspace/state/external_runner.lock` prevents overlapping GUI runs, stale locks are replaced, and runner output goes to `workspace/logs/external_gui_runner.log` plus structured events in `workspace/logs/bridge.jsonl`. Stop or inspect the runner with:

```bash
bash scripts/stop_external_gui_runner.sh
bash scripts/status_external_gui_runner.sh
```

A LaunchAgent template is available at `packaging/macos/com.agentbridge.runner.plist.template`; it is not installed automatically.

## iTerm Ghost Runner

The intended long-term GUI bridge host is an iTerm or Terminal launched ghost runner, not the active Codex execution context. Codex Full Access remains useful for implementation and diagnostics, but the GUI click/paste path should run from a normal terminal process that has macOS Accessibility and Automation permission.

From iTerm/Terminal:

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

Or use the helper script:

```bash
bash scripts/start_iterm_ghost_runner.sh
```

`run-iterm-ghost-runner` refuses `CODEX_SANDBOX`, rejects Codex-hosted execution, prints the parent process chain, and proceeds only when the process is hosted by Terminal/iTerm. It watches `workspace/reports/latest_agent_report.md` for content-hash changes. A report change can trigger at most one bounded report roundtrip when `--max-roundtrips 1` is used.

Loop prevention uses:

```text
workspace/state/last_processed_report_hash
workspace/state/ghost_runner.lock
workspace/logs/external_gui_runner.log
workspace/logs/bridge.jsonl
```

The hash guard ignores already processed report content, the lock prevents concurrent GUI roundtrips, cooldown/debounce prevent immediate repeats, and `--max-roundtrips` bounds dogfood runs. To trigger a validation run, update `workspace/reports/latest_agent_report.md` after the ghost runner is already running, or use an explicit trigger file if supported by the external runner:

```bash
mkdir -p workspace/triggers
touch workspace/triggers/report_roundtrip.request
```

Check or stop a background ghost runner with:

```bash
bash scripts/status_iterm_ghost_runner.sh
bash scripts/stop_iterm_ghost_runner.sh
```

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
  --max-runtime-seconds 180 \
  --submit-local-agent \
  --stop-after-local-agent-submit \
  --no-artifact-confirmation-wait
```

This command reads the full `workspace/reports/latest_agent_report.md`, builds a PM prompt asking ChatGPT to return exactly one fenced block labeled `CODEX_NEXT_PROMPT`, with the first non-empty line inside the block also exactly `CODEX_NEXT_PROMPT`. The body label is required because native ChatGPT Mac response-copy may copy only the rendered code block body and omit the Markdown fence info string. Agent Bridge saves the raw PM response to `workspace/outbox/pm_response.md`, extracts either the fenced block or a body-only `CODEX_NEXT_PROMPT` copy, enqueues that extracted prompt, stages the local-agent prompt through Dispatcher, and submits the staged local-agent prompt to the configured Codex target.

For the safe no-op queue handoff workflow, `--stop-after-local-agent-submit --no-artifact-confirmation-wait` stops immediately after Codex submit/queue is attempted. This lets the current Codex task become idle so the queued no-op prompt can run and write the later success artifact.

The command refuses to run without `--auto-confirm`, is limited to one cycle, and does not retry silently. SafetyGate runs before ChatGPT submit and before Codex submit.

The ChatGPT GUI flow uses HTML state signals when available. For the Google Chrome backend, Agent Bridge first waits up to `idle_empty_timeout_seconds` seconds for the composer to be idle-empty (`data-testid="composer-speech-button"` or `aria-label="Voice 시작"`), rechecking every `idle_empty_poll_interval_seconds` seconds. It does not paste over a streaming response (`data-testid="stop-button"` / `aria-label="스트리밍 중지"`) or a user-pending composed message (`data-testid="send-button"` / `aria-label="프롬프트 보내기"`). It then focuses a visible composer (`textarea`, `#prompt-textarea`, `[contenteditable="true"]`, or `[data-testid="composer-text-input"]`), inserts the prompt through DOM JavaScript, dispatches input/change events, and verifies the composer text contains the expected Agent Bridge marker before submit. It waits for the composer send-ready button and refuses to submit while the composer only shows the empty voice button. During response capture it waits while the stop button is present, then copies the latest assistant response using the copy button inside the latest assistant turn. Owner-provided CSS/XPath selectors are fallback strategies only. If button-based copy does not change the clipboard, Agent Bridge extracts the latest assistant turn text through DOM JavaScript, writes it to the clipboard, and still verifies non-empty text plus `CODEX_NEXT_PROMPT` when expected.

Before running a live report roundtrip, verify both GUI targets and the PM backend:

```bash
python -m agent_bridge.cli preflight-gui-apps --pm-app "Google Chrome" --activate
python -m agent_bridge.cli preflight-pm-backend --activate
python -m agent_bridge.cli preflight-gui-apps --local-agent-app "Codex" --activate
```

Run the full roundtrip only after activation preflight passes for both targets and PM backend preflight passes.
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

## Foreground Bridge Runner

Agent Bridge now provides a foreground terminal runner for user-controlled GUI bridge execution. It is active only while the terminal command is running and stops on Ctrl-C, terminal close, max runtime, max roundtrips, or safety pause. It is not a LaunchAgent or background daemon by default.

Preflight a PM target profile without submitting prompts:

```bash
python -m agent_bridge.cli preflight-run-bridge --pm-target chatgpt_mac
python -m agent_bridge.cli preflight-run-bridge --pm-target chatgpt_chrome_app
```

Run the foreground watcher:

```bash
python -m agent_bridge.cli run-bridge \
  --pm-target chatgpt_mac \
  --watch-report workspace/reports/latest_agent_report.md \
  --polling-interval-seconds 3
```

or:

```bash
python -m agent_bridge.cli run-bridge \
  --pm-target chatgpt_chrome_app \
  --watch-report workspace/reports/latest_agent_report.md \
  --polling-interval-seconds 3
```

The helper script is equivalent and remains foreground:

```bash
bash scripts/run_bridge.sh --pm-target chatgpt_mac
bash scripts/run_bridge.sh --pm-target chatgpt_chrome_app
```

The runner watches `workspace/reports/latest_agent_report.md` by content hash, debounces changes, and triggers on any post-startup report content change by default. `--require-trigger-marker` is an optional compatibility/safety mode that requires `AGENT_BRIDGE_GUI_ROUNDTRIP_TEST` before starting a bridge attempt. The runner acquires `workspace/state/foreground_bridge_runner.lock`, records `workspace/state/last_seen_report_hash` and `workspace/state/last_processed_report_hash`, and logs to `workspace/logs/foreground_bridge_runner.log` plus `workspace/logs/bridge.jsonl`. The selected PM profile controls the app target, backend, asset profile, and response-copy path. Agent Bridge must not silently fall back between `chatgpt_mac` and `chatgpt_chrome_app`.

At startup, `run-bridge` records the current report content hash as the session baseline and waits for changes after startup. It does not process an already-existing report, even if that report contains `AGENT_BRIDGE_GUI_ROUNDTRIP_TEST`, unless `--process-existing-trigger` is explicitly passed.

Startup is intentionally lightweight. `run-bridge` selects only the configured PM profile and records the baseline before doing live visual diagnostics. App/window resolution, asset matching, response-copy checks, and Codex visual checks are deferred until a post-startup report change actually starts a bridge attempt. Use `preflight-run-bridge` when the owner wants the heavier no-submit visual checks before starting the foreground watcher.

Add `--debug` to enable both state-machine and GUI-action debug traces. More specific flags are `--debug-state-machine`, `--debug-gui-actions`, and `--debug-screenshots`. Debug output is written to `workspace/logs/gui_state_machine_debug.jsonl`, `workspace/logs/gui_actions_debug.jsonl`, `workspace/logs/foreground_bridge_runner.log`, and `workspace/logs/bridge.jsonl`; every debug event includes a `bridge_attempt_id`. The local-agent handoff refuses to submit if the staged prompt is empty, if Codex paste was not attempted, or if the focus/click path succeeded but paste did not report success.

### Canonical PM Visual Bridge Sequence

`chatgpt_chrome_app` and `chatgpt_mac` share the same visual PM controller sequence:

1. select the PM target profile;
2. resolve the profile-specific app/window;
3. run bounded visual state detection and retry policy;
4. detect the plus-anchor composer control;
5. focus the composer with PyAutoGUI;
6. set/read clipboard and paste with retry;
7. verify `COMPOSER_HAS_TEXT`;
8. submit the PM prompt;
9. wait for response generation to run and complete;
10. re-activate the PM target, detect/click response-copy, and save `pm_response.md`;
11. extract `CODEX_NEXT_PROMPT`;
12. hand off through SafetyGate, CommandQueue, Dispatcher, and the Codex visual path.

The two PM profiles differ only by app/window resolution, asset profile, thresholds, and response-copy assets. `chatgpt_mac` uses native bundle id `com.openai.chat` and `assets/gui/chatgpt_mac/`. `chatgpt_chrome_app` uses a selected `com.google.Chrome.app.*` PWA process and `assets/gui/chatgpt_chrome_app/`. No profile silently falls back to the other, and the visual profiles do not depend on Chrome DOM JavaScript by default.

### Prompt And Report Style

PM-to-Codex prompts should be concise and task-focused. Rely on `AGENTS.md`, this README, and the Codex skill for standing rules instead of repeating long boilerplate. Include the current goal, constraints, execution steps, tests, and report expectations. Keep the `CODEX_NEXT_PROMPT` fence and body-label compatibility requirement intact.

`workspace/reports/latest_agent_report.md` should read like a concise PM-to-CTO status note: what changed, what passed, what failed, the current blocker, exact failure point when any, commands run, and the next recommended task. Use longer narrative only for debugging evidence.

### Roadmap

Current Agent Bridge supports `chatgpt_mac` and `chatgpt_chrome_app` PM targets through the foreground runner. Planned future work is packaging this repository as a reusable module, tightening Skill installation docs, validating use from external project directories, extending the ChatGPT-to-local-Codex flow into a ChatGPT-Codex-GitHub/Codex-review pipeline, and pruning dogfood scaffolding after stable validation.
