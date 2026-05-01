# Computer Use Terminal Trigger

Computer Use must do only this:

1. Focus an already-open normal macOS Terminal window outside the Codex sandbox.
2. Paste exactly one shell command:

```bash
cd /Users/kimdohyeong/Desktop/agent-bridge-portable-handoff && source .venv/bin/activate && bash scripts/run_gui_roundtrip_external.sh
```

3. Press Enter once.
4. Stop.

Computer Use must not operate ChatGPT directly.
Computer Use must not operate Codex directly.
Computer Use must not manually copy ChatGPT responses.
Computer Use must not paste into Codex manually.

Agent Bridge Python code remains responsible for:

- reading `workspace/reports/latest_agent_report.md`;
- building the PM prompt;
- pasting/submitting to ChatGPT;
- waiting for ChatGPT response completion;
- copying the full ChatGPT response;
- extracting `CODEX_NEXT_PROMPT`;
- enqueuing/staging the local-agent command;
- pasting/submitting to Codex;
- enforcing SafetyGate;
- enforcing one-cycle and max-runtime bounds;
- logging all events.

Terminal must be outside the restricted Codex sandbox.
Agent Bridge stops after one cycle.
Agent Bridge does not touch GitHub or Gmail in this flow.
