# External GUI Runner

Agent Bridge GUI automation should be launched from a normal macOS Terminal, not from an active Codex task process.

The Codex execution context may include sandbox markers such as `CODEX_SANDBOX`, `CODEX_SHELL`, and `CODEX_THREAD_ID`. In that context, macOS LaunchServices may not resolve GUI apps such as ChatGPT or Codex even when the `.app` bundles are valid on disk.

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
```

If it reports that Codex sandbox markers are present, do not run GUI automation from that process.

## Run From Normal Terminal

Open a normal macOS Terminal and run:

```bash
cd /path/to/agent-bridge-portable-handoff
bash scripts/run_gui_roundtrip_external.sh
```

The script refuses to run if it detects Codex sandbox markers. It then runs:

```bash
python -m agent_bridge.cli preflight-external-runner
python -m agent_bridge.cli preflight-gui-apps --pm-app "ChatGPT" --activate
python -m agent_bridge.cli preflight-gui-apps --local-agent-app "Codex" --activate
```

Only after both activation preflights pass does it run:

```bash
python -m agent_bridge.cli dogfood-report-roundtrip \
  --auto-confirm \
  --max-cycles 1 \
  --max-runtime-seconds 180
```

## Safety Rules

The external runner must not be used to bypass SafetyGate or CommandQueue. If SafetyGate blocks a prompt, the run stops.

The external runner must not mutate GitHub, send Gmail, push commits, auto-merge, or run without max-cycle and max-runtime bounds.

Do not retry the full GUI roundtrip until `preflight-external-runner` and both activation preflights pass from a normal Terminal.
