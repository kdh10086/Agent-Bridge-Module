# Agent Report: File-Based GitHub Review and CI Digest Producer Adapters

## Summary

Implemented file-based digest producer adapters for review and CI inputs. The adapters parse local JSON or markdown fixtures, normalize them into provider-neutral digest models, write canonical markdown digests under `workspace/inbox/`, and enqueue commands into `CommandQueue`. They do not dispatch, call provider APIs, call `gh`, automate a GUI, send email, or modify downstream project code.

## Files Inspected

- `AGENTS.md`
- `CODEX_START_HERE.md`
- `docs/01_ARCHITECTURE.md`
- `docs/02_PORTABLE_MODULE_DESIGN.md`
- `docs/03_IMPLEMENTATION_PLAN.md`
- `docs/04_DETAILED_TODO.md`
- `docs/05_SELF_TEST_AND_DOGFOOD_PROTOCOL.md`
- `docs/06_SAFETY_AND_APPROVAL_GATES.md`
- `docs/07_CLI_SPEC.md`
- `docs/08_CODEX_MASTER_PROMPT.md`
- `workspace/reports/latest_agent_report.md`
- `agent_bridge/core/models.py`
- `agent_bridge/core/command_queue.py`
- `agent_bridge/core/event_log.py`
- `agent_bridge/cli.py`
- `agent_bridge/github/__init__.py`
- `tests/*`
- `fixtures/*`

## Files Changed

- `agent_bridge/core/models.py`: added `ReviewDigest`, `ReviewActionItem`, `CIDigest`, and `CIFailureItem`; fixed default command priority materialization.
- `agent_bridge/github/digest_builder.py`: added JSON/markdown fixture parsers and canonical markdown builders.
- `agent_bridge/github/review_watcher.py`: added file-based review digest producer that writes `workspace/inbox/github_review_digest.md` and enqueues `GITHUB_REVIEW_FIX`.
- `agent_bridge/github/ci_watcher.py`: added file-based CI digest producer that writes `workspace/inbox/ci_failure_digest.md` and enqueues `CI_FAILURE_FIX`.
- `agent_bridge/cli.py`: added `ingest-review` and `ingest-ci` CLI commands.
- `fixtures/fake_github_review.json`: added generic review fixture.
- `fixtures/fake_ci_failure.json`: added generic CI failure fixture.
- `tests/test_digest_builder.py`: added parser and markdown builder tests.
- `tests/test_file_based_watchers.py`: added producer enqueue, priority, dedupe, and user-decision propagation tests.
- `docs/07_CLI_SPEC.md`: documented the new file-based ingest commands.
- `workspace/reports/latest_agent_report.md`: wrote this milestone report.

## CLI Commands Verified

- `.venv/bin/python -m agent_bridge.cli --help`
- `.venv/bin/python -m agent_bridge.cli ingest-review --help`
- `.venv/bin/python -m agent_bridge.cli ingest-ci --help`
- `.venv/bin/python -m agent_bridge.cli init --force`
- `.venv/bin/python -m agent_bridge.cli ingest-review --fixture fixtures/fake_github_review.json --dry-run`
- `.venv/bin/python -m agent_bridge.cli ingest-review --fixture fixtures/fake_github_review.json`
- `.venv/bin/python -m agent_bridge.cli ingest-ci --fixture fixtures/fake_ci_failure.json --dry-run`
- `.venv/bin/python -m agent_bridge.cli ingest-ci --fixture fixtures/fake_ci_failure.json`
- `.venv/bin/python -m agent_bridge.cli queue list`

## Producer Behavior Verified

- Review producer writes `workspace/inbox/github_review_digest.md`.
- Review producer enqueues `GITHUB_REVIEW_FIX` with priority `70`.
- CI producer writes `workspace/inbox/ci_failure_digest.md`.
- CI producer enqueues `CI_FAILURE_FIX` with priority `80`.
- Re-ingesting the same fixture is deduped by stable dedupe key.
- `requires_user_decision` in review action items is propagated to `command.requires_user_approval`.
- Producers leave no `workspace/queue/in_progress.json`; they do not dispatch.

## Tests Run

- `.venv/bin/python -m pytest`
- `.venv/bin/ruff check .`
- `PATH="$PWD/.venv/bin:$PATH" bash scripts/self_test.sh`
- `bash portable_module/.agent-bridge/scripts/self_test.sh`

## Test Results

- `pytest`: 13 passed.
- `ruff`: all checks passed.
- Standalone self-test: passed.
- Portable self-test: passed.

## Safety and Architecture Verification

The producer-queue-dispatcher rule is preserved. `review_watcher` and `ci_watcher` only parse local files, write canonical digest markdown, append audit log events, and enqueue commands. Dispatcher remains the only component that can build and send local-agent prompts. No real GitHub polling, `gh` calls, GUI automation, Gmail sending, continuous run-loop automation, or downstream project implementation was added.

## Canonical Digest Outputs

- `workspace/inbox/github_review_digest.md`
- `workspace/inbox/ci_failure_digest.md`

Portable module equivalent paths remain:

- `.agent-bridge/workspace/inbox/github_review_digest.md`
- `.agent-bridge/workspace/inbox/ci_failure_digest.md`

## Known Limitations

- The parsers are MVP file parsers for JSON fixtures and simple markdown digests.
- Real provider adapters are not implemented.
- Real GitHub API polling and `gh` CLI integration are not implemented.
- Portable scripts still use the existing portable self-test path; they do not yet wrap the new Python ingest commands.
- Continuous run-loop automation remains intentionally out of scope.

## Next Recommended Task

Add portable script wrappers for `ingest-review` and `ingest-ci`, then add documentation showing how a target project can drop review and CI digest files into `.agent-bridge/workspace/inbox/` without enabling real provider polling.
