# Safety

Portable Agent Bridge stops dispatch when a pending command payload contains a hard-stop keyword.

## Hard Stops

```text
NEEDS_USER_DECISION
APPROVAL_REQUIRED
RISK_HIGH
PAID_API
LICENSE_UNKNOWN
PRIVACY_RISK
MAIN_MERGE
DATA_MIGRATION
ARCHITECTURE_CHANGE
DELETE_OR_REWRITE_LARGE_SCOPE
CI_FAILED_REPEATEDLY
MAX_CYCLE_REACHED
```

## Portable Safety Behavior

When `.agent-bridge/scripts/dispatch_next.sh --dry-run` detects a hard stop:

1. it does not write a normal local-agent dispatch prompt;
2. it writes `.agent-bridge/workspace/inbox/user_decision_request.md`;
3. it writes `.agent-bridge/workspace/outbox/owner_decision_email.md`;
4. it writes `.agent-bridge/workspace/state/state.json` with `safety_pause: true`;
5. it prints a clear safety pause message.

## Owner Decision Files

The owner decision request is for the local owner or Codex agent to inspect. The owner email file is a draft artifact only. The portable MVP does not send email.

## Scope Boundary

Safety files and logs are written under `.agent-bridge/workspace/`. Portable scripts must not change target project source files while handling safety pauses.
