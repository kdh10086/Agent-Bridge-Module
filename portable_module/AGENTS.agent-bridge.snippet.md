# Agent Bridge Project Snippet

This project uses Agent Bridge.

Before using automation, read:

```text
.agent-bridge/README.md
.agent-bridge/docs/OPERATING_MODEL.md
.agent-bridge/docs/SAFETY.md
```

Rules:

1. Do not bypass `.agent-bridge/workspace/queue/`.
2. Do not paste watcher outputs directly into the local coding agent.
3. Use queue-first workflow.
4. Write reports to `.agent-bridge/workspace/reports/latest_agent_report.md`.
5. Run `.agent-bridge/scripts/self_test.sh` before real automation.
6. Stop on safety flags.
