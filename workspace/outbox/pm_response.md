You are the PM assistant for Agent Bridge.

Read the full latest agent report below and return the next Codex instruction.

Response contract:
- Return exactly one fenced Markdown code block.
- The fence info string must be CODEX_NEXT_PROMPT.
- Put only the next Codex instruction inside that block.
- Do not include prose before or after the block.
- Keep the instruction generic to Agent Bridge and do not add downstream project assumptions.

Latest agent report:

# Agent Report: External GUI Runner Mode

## Summary

Added an external GUI runner mode so Agent Bridge GUI automation can be launched from a normal macOS Terminal instead of the active Codex execution context. The runner refuses to operate when Codex sandbox markers are present, runs environment and activation preflights first, and only then runs the bounded one-cycle report roundtrip.

No full GUI roundtrip was run from Codex. No paste, submit, GitHub, Gmail, push, merge, or downstream source-code action was attempted.

## Files Changed

- `agent_bridge/gui/external_runner.py`
- `agent_bridge/cli.py`
- `scripts/run_gui_roundtrip_external.sh`
- `docs/EXTERNAL_GUI_RUNNER.md`
- `tests/test_external_runner.py`
- `README.md`
- `docs/07_CLI_SPEC.md`
- `codex_skill/agent-bridge/SKILL.md`
- `workspace/reports/latest_agent_report.md`

## External Runner Design

Codex prepares Agent Bridge code, queue state, reports, and staged prompts. A normal macOS Terminal process is responsible for GUI side effects because LaunchServices app resolution can fail inside the Codex sandbox.

The shared state remains the repository workspace:

- `workspace/state/`
- `workspace/queue/`
- `workspace/inbox/`
- `workspace/outbox/`
- `workspace/reports/`
- `workspace/logs/`

## Sandbox Detection

The external runner checks for these markers:

- `CODEX_SANDBOX`
- `CODEX_SHELL`
- `CODEX_THREAD_ID`

If any are set, `scripts/run_gui_roundtrip_external.sh` exits before app activation, clipboard access, paste, submit, or roundtrip launch.

The required preflight was run from the active Codex task process and correctly reported:

- Running inside Codex sandbox: yes.
- `CODEX_SANDBOX`: set.
- `CODEX_SHELL`: set.
- `CODEX_THREAD_ID`: set.

## App Activation Preflight

Added:

```bash
python -m agent_bridge.cli preflight-external-runner
```

It reports:

- Codex sandbox marker status.
- `pbcopy` and `pbpaste` availability.
- AppleScript resolution for the configured PM assistant app.
- AppleScript resolution for the configured local-agent app.
- Recommended next command.

From the active Codex context:

- `pbcopy`: available at `/usr/bin/pbcopy`.
- `pbpaste`: available at `/usr/bin/pbpaste`.
- AppleScript resolution for `ChatGPT`: failed.
- AppleScript resolution for `Codex`: failed.
- Recommended command: run `bash scripts/run_gui_roundtrip_external.sh` from normal Terminal.

## Runner Script

Added:

```bash
bash scripts/run_gui_roundtrip_external.sh
```

The script:

1. Refuses Codex sandbox execution.
2. Chooses `.venv/bin/python` when available.
3. Runs `preflight-external-runner`.
4. Runs PM assistant activation preflight.
5. Runs local-agent activation preflight.
6. Runs the bounded report roundtrip only after preflights pass:

```bash
python -m agent_bridge.cli dogfood-report-roundtrip \
  --auto-confirm \
  --max-cycles 1 \
  --max-runtime-seconds 180
```

## Tests Run

- `.venv/bin/python -m pytest`
- `.venv/bin/ruff check .`
- `.venv/bin/python -m agent_bridge.cli preflight-external-runner`
- `PATH="$PWD/.venv/bin:$PATH" bash scripts/self_test.sh`
- `bash portable_module/.agent-bridge/scripts/self_test.sh`

## Test Results

- `pytest`: 110 passed.
- `ruff`: all checks passed.
- `preflight-external-runner`: completed and reported the current Codex sandbox state.
- Standalone self-test: passed.
- Portable self-test: passed.

## Known Limitations

The external runner was implemented and tested, but the full live GUI roundtrip was not run because this task explicitly forbids retrying the full roundtrip from inside Codex.

The script must be run manually from a normal macOS Terminal. If activation preflight still fails there, the script stops before the roundtrip.

## Next Recommended Task

Run this manually from a normal macOS Terminal:

```bash
cd /Users/kimdohyeong/Desktop/agent-bridge-portable-handoff
bash scripts/run_gui_roundtrip_external.sh
```

If both activation preflights pass, the script will run the bounded one-cycle report-to-PM-to-local-agent roundtrip. If either preflight fails, use the diagnostic output to repair macOS app resolution before retrying.
