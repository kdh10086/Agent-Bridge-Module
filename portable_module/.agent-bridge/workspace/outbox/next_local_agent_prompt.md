You are the local coding agent working under Agent Bridge.

Execute only the command below.

Rules:
- Keep scope minimal.
- Do not start unrelated work.
- Add or update tests when applicable.
- Write `.agent-bridge/workspace/reports/latest_agent_report.md` when done.
- Stop and report if owner approval is required.

[Command Type]
CI_FAILURE_FIX

[Command Source]
portable_ingest_ci

[Payload]
# CI Failure Digest

## Summary

A generic test job failed and needs a local dry-run fix attempt.

## Failures

### 1. test

- Step: test command
- Status: failed
- Requires User Decision: no

Error Excerpt:

AssertionError: expected queue priority ordering.

Suggested Local Agent Action:

Inspect the failing test output and make the smallest generic correction.


