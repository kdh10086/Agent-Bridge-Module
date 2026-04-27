# Self-Test and Dogfooding Protocol

## Standalone Self-Test

```bash
pytest
python -m agent_bridge.cli init --force
python -m agent_bridge.cli run-once --dry-run --fixture fixtures/fake_agent_report.md
python -m agent_bridge.cli queue list
python -m agent_bridge.cli dispatch-next --dry-run
python -m agent_bridge.cli simulate-dogfood
python -m agent_bridge.cli status
```

## Portable Module Self-Test

From a target project root:

```bash
bash .agent-bridge/scripts/self_test.sh
```

Expected result:

- `.agent-bridge/workspace/` exists.
- queue files exist.
- fake review and CI digest commands can be enqueued.
- queue list prints review and CI commands.
- dry-run dispatch prints prompt for the highest-priority command.
- safety pause files are written when a risky digest contains a hard-stop keyword.
- no project source code is modified.

## Portable Installer Self-Test

From the standalone Agent Bridge repository:

```bash
python -m agent_bridge.cli install-portable --target /tmp/agent-bridge-install-test --dry-run
python -m agent_bridge.cli install-portable --target /tmp/agent-bridge-install-test
python -m agent_bridge.cli verify-portable --target /tmp/agent-bridge-install-test
bash /tmp/agent-bridge-install-test/.agent-bridge/scripts/self_test.sh
```

Expected result:

- dry-run modifies nothing;
- real install copies `.agent-bridge/`;
- `AGENTS.agent-bridge.snippet.md` is copied by default;
- reinstall without `--force` fails safely;
- reinstall with `--force` succeeds;
- target self-test passes.

## Bounded Run-Loop Self-Test

From the standalone Agent Bridge repository:

```bash
python -m agent_bridge.cli run-loop --dry-run --max-cycles 1 --polling-interval-seconds 1
python -m agent_bridge.cli run-loop --dry-run --max-cycles 3 --polling-interval-seconds 1
python -m agent_bridge.cli enqueue --type REQUEST_STATUS_REPORT --payload fixtures/fake_pm_response.md
python -m agent_bridge.cli run-loop --dry-run --max-cycles 1 --polling-interval-seconds 1
```

Expected result:

- the loop stops at the configured max-cycle limit;
- the loop stops at the configured max-runtime limit when reached;
- `safety_pause` stops the loop;
- empty queue cycles are logged;
- queued commands are passed only to Dispatcher;
- Dispatcher builds a dry-run local-agent prompt;
- no GUI automation, Gmail sending, GitHub mutation, auto-fix, auto-merge, commit, or push occurs.

## Dogfooding Goal

Agent Bridge must be able to test its own automation loop without real GUI or GitHub dependencies.

## Read-Only GitHub CLI Dogfood

Live GitHub dogfood is optional and must be safe. It requires the user to authenticate manually:

```bash
gh auth login
```

Use only a safe PR selected by the owner:

```bash
python -m agent_bridge.cli dogfood-gh --owner OWNER --repo REPO --pr 123 --dry-run
python -m agent_bridge.cli watch-reviews --owner OWNER --repo REPO --pr 123 --dry-run
python -m agent_bridge.cli watch-ci --owner OWNER --repo REPO --pr 123 --dry-run
python -m agent_bridge.cli queue list
python -m agent_bridge.cli run-loop --dry-run --max-cycles 2 --polling-interval-seconds 1 --no-dispatch
```

Expected dry-run result:

- review and CI data are read through `gh`;
- paginated review threads, review comments, issue comments, and status contexts are included;
- planned digest markdown is printed;
- no queue entry is created;
- no dispatch is attempted;
- no GitHub comments, PRs, commits, pushes, or auto-fixes are performed.

Only remove `--dry-run` when the owner explicitly wants Agent Bridge to write the canonical digest file and enqueue the resulting command. Non-dry-run watcher commands still do not dispatch; Dispatcher remains the only sender.

Full procedure:

```text
docs/LIVE_SAFE_PR_DOGFOOD.md
```
