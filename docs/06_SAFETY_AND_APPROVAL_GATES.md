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

## Run-Loop Behavior

The continuous run loop must check persisted state before each cycle. If `safety_pause` is true, it stops before polling watchers or dispatching.

If Dispatcher blocks a queued command because the payload contains a hard-stop keyword or requires owner approval, Dispatcher writes the decision request files and sets `safety_pause`; the run loop then stops.

Max-cycle and max-runtime limits also stop the loop. They are control limits, not approval grants. Hitting a limit must not trigger GUI dispatch, Gmail sending, GitHub mutation, auto-fix, auto-merge, commit, or push.

## Stage-Only GUI Bridge Behavior

Stage-only GUI bridge commands must scan the full staged prompt before writing or copying it.

If a hard-stop keyword is detected:

- do not stage the prompt file;
- do not copy to clipboard;
- write `workspace/inbox/user_decision_request.md`;
- write `workspace/outbox/owner_decision_email.md`;
- set `safety_pause = true`;
- log the blocked staging event.

Clipboard copy requires explicit manual confirmation. Dry-run mode never copies to clipboard. No stage-only command may press Enter, submit a message, or perform autonomous GUI automation.

## Manual Local-Agent Dispatch Behavior

Manual local-agent dispatch must run the same SafetyGate before staging, clipboard copy, or app activation.

If safety blocks the dispatch:

- do not stage a usable local-agent prompt;
- do not copy to clipboard;
- do not activate the local-agent app;
- write `workspace/inbox/user_decision_request.md`;
- write `workspace/outbox/owner_decision_email.md`;
- set `safety_pause = true`;
- log `local_agent_dispatch_blocked_by_safety`;
- leave the command pending unless the legacy consuming Dispatcher path already moved it to failed.

Manual dispatch side effects are split:

- clipboard copy requires explicit confirmation;
- app activation requires separate explicit confirmation;
- cancelled confirmation leaves the command pending;
- confirmed clipboard copy or confirmed app activation moves the command to in-progress;
- `--stage-only` never pops the queue;
- `--dry-run` does not perform GUI-side effects;
- no mode may paste automatically, press Enter, submit a message, or run unattended GUI automation.

For real local-agent GUI-side-effect actions, confirmation defaults to a new Terminal window on macOS. Agent Bridge writes request/result files under `workspace/confirmations/`, opens Terminal with `osascript`, displays the action summary, target app/window hint, prompt path, what will happen, and what will not happen, then waits for owner input.

Terminal confirmation must state that Agent Bridge will not paste automatically, will not press Enter or Return, and will not submit. A yes response allows only the requested clipboard copy or app activation. A no response, timeout, Terminal close without a result, or opener error cancels safely and must not consume the queue command.

SafetyGate runs before any Terminal confirmation window opens. A blocked prompt must not request terminal confirmation.

## Owner-Approved Unattended GUI Dogfood

`dogfood-gui-bridge --auto-confirm` is the only unattended GUI bridge dogfood path. Without `--auto-confirm`, it must refuse to run. The default `dispatch-next` and stage commands remain manual-confirmation based.

The unattended dogfood path is still bounded and safety-gated:

- `--max-cycles` is required;
- `--max-runtime-seconds` is required;
- the PM response wait has a timeout;
- no silent retry loop is allowed;
- SafetyGate runs before PM submit and before local-agent submit;
- a safety block writes the decision request files, sets `safety_pause`, logs `gui_dogfood_safety_blocked`, and stops;
- GitHub mutation, Gmail sending, commit push, auto-merge, and downstream source edits are out of scope.

Unattended mode may paste and submit only inside this explicit dogfood command after the owner has provided `--auto-confirm`. Dispatcher remains the only component that prepares local-agent prompts.

## Report Roundtrip Safety

`dogfood-report-roundtrip --auto-confirm` is a stricter one-cycle GUI path for sending the full latest report to the PM assistant and sending the extracted next Codex prompt back to the local agent.

Safety requirements:

- it refuses to run without `--auto-confirm`;
- it is limited to exactly one cycle;
- `--max-runtime-seconds` bounds the run;
- PM response wait has a timeout;
- missing or duplicate `CODEX_NEXT_PROMPT` blocks fail safely;
- SafetyGate runs before the PM assistant submit;
- SafetyGate runs before the local Codex submit;
- a safety block writes decision files, sets `safety_pause`, logs the block, and stops;
- GitHub mutation, Gmail sending, commit push, auto-merge, and downstream source edits remain out of scope.
