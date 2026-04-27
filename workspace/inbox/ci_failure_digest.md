# CI Failure Digest

## Metadata

- Source: ci_fixture
- Repository: example/repository
- PR Number: 42
- Check Run ID: check-2001
- Detected At: 2026-04-27T00:05:00Z
- Raw Source Path: fixtures/fake_ci_failure.json
- Dedupe Key: ci:example-repository:42:check-2001

## Summary

The test job failed in a deterministic unit test.

## Failures

### 1. test

- Step: pytest
- Status: failed
- Requires User Decision: no

Error Excerpt:

AssertionError: expected command priority 80

Suspected Cause:

The test fixture expects CI failure commands to outrank review fixes.

Suggested Local Agent Action:

Inspect command priority configuration and update the failing test or implementation.
