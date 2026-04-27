# Safety and Approval Gates

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

## Behavior

- Do not dispatch blocked commands.
- Set safety pause.
- Write owner decision request.
- Write owner email draft.
- Stop loop.
