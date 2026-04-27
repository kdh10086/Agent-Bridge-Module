# Agent Report: GUI Activation Retest with Full Access

## Summary

Retested ChatGPT and Codex app resolution/activation from the Codex execution context after Full Access was enabled.

Full Access changed the previous failure: AppleScript can now resolve and activate both ChatGPT and Codex from this process. No full GUI roundtrip was run. No paste, submit, GitHub, Gmail, push, merge, or downstream source-code action was attempted.

## Commands Run

- `.venv/bin/python -m agent_bridge.cli preflight-external-runner`
- `.venv/bin/python -m agent_bridge.cli preflight-gui-apps --pm-app "ChatGPT" --activate`
- `.venv/bin/python -m agent_bridge.cli preflight-gui-apps --local-agent-app "Codex" --activate`
- `osascript -e 'id of application "ChatGPT"'`
- `osascript -e 'tell application "ChatGPT" to activate'`
- `osascript -e 'id of application "Codex"'`
- `osascript -e 'tell application "Codex" to activate'`
- `env | rg '^(CODEX_SANDBOX|CODEX_SHELL|CODEX_THREAD_ID)=' || true`

## Codex Environment Markers

- `CODEX_SANDBOX`: not set.
- `CODEX_SHELL`: set.
- `CODEX_THREAD_ID`: set.

The previous sandbox marker most directly tied to restricted execution, `CODEX_SANDBOX`, is no longer present. The process still has Codex session markers.

## ChatGPT Resolution And Activation

`preflight-external-runner` result:

- AppleScript resolution for `ChatGPT`: succeeded.
- Resolved id: `com.google.Chrome.app.cadlkienfkclaiaibeoongdcgmdikeeg`.

`preflight-gui-apps --pm-app "ChatGPT" --activate` result:

- Activation succeeded.
- Winning strategy: AppleScript.

Direct AppleScript checks:

- `id of application "ChatGPT"` succeeded.
- `tell application "ChatGPT" to activate` succeeded.

Note: the resolved id is a Chrome app wrapper id, not `com.openai.chat`, even though `config/local.yaml` still includes `/Applications/ChatGPT.app` and `com.openai.chat` as fallback metadata.

## Codex Resolution And Activation

`preflight-external-runner` result:

- AppleScript resolution for `Codex`: succeeded.
- Resolved id: `com.openai.codex`.

`preflight-gui-apps --local-agent-app "Codex" --activate` result:

- Activation succeeded.
- Winning strategy: AppleScript.

Direct AppleScript checks:

- `id of application "Codex"` succeeded.
- `tell application "Codex" to activate` succeeded.

## Full Access Impact

Full Access resolved the prior LaunchServices/AppleScript activation blocker for both targets.

Previous behavior:

- AppleScript could not resolve ChatGPT or Codex.
- Activation failed before any GUI handoff could be attempted.

Current behavior:

- AppleScript resolves ChatGPT and Codex.
- AppleScript activates ChatGPT and Codex.
- Clipboard tools are available at `/usr/bin/pbcopy` and `/usr/bin/pbpaste`.

## Roundtrip Retry Assessment

The activation-specific blocker is cleared.

However, the current `preflight-external-runner` still classifies this process as a Codex context because `CODEX_SHELL` and `CODEX_THREAD_ID` remain set. As currently implemented, `scripts/run_gui_roundtrip_external.sh` would still refuse to run if launched from this process.

`dogfood-report-roundtrip` can now be retried safely from an app-activation perspective, but it was not run in this task. The safest next step is one of:

1. Run `bash scripts/run_gui_roundtrip_external.sh` from a normal macOS Terminal.
2. In a follow-up task, refine external-runner sandbox detection so Full Access mode without `CODEX_SANDBOX` is treated separately from restricted Codex sandbox mode.

## Known Limitations

This task only retested activation. It did not validate paste, submit, PM response capture, prompt extraction, queue handoff, or local Codex submission.

The ChatGPT target resolves to a Chrome app wrapper id in AppleScript. If that is the intended PM assistant surface, no change is required. If the native ChatGPT app is required, target metadata may need a separate follow-up.

## Next Recommended Task

Run a bounded one-cycle GUI roundtrip from normal Terminal, or first update external-runner sandbox detection to distinguish Full Access from restricted sandbox execution.
