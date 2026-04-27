# Agent Bridge Project Snippet

This project uses Agent Bridge through the portable `.agent-bridge/` module.

Before using automation, read:

```text
.agent-bridge/README.md
.agent-bridge/docs/OPERATING_MODEL.md
.agent-bridge/docs/SAFETY.md
```

Rules:

1. Do not bypass `.agent-bridge/workspace/queue/`.
2. Do not paste watcher outputs directly into the local coding agent.
3. Use queue-first workflow for PM, review, CI, manual, and status commands.
4. Only `.agent-bridge/scripts/dispatch_next.sh --dry-run` may create a local-agent prompt.
5. Review and CI ingest scripts must not dispatch.
6. Write reports to `.agent-bridge/workspace/reports/latest_agent_report.md`.
7. Run `.agent-bridge/scripts/self_test.sh` before real bridge operation.
8. Stop on safety flags and inspect `.agent-bridge/workspace/inbox/user_decision_request.md`.
9. Portable bridge scripts must not modify target source files.
