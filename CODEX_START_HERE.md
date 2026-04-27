# CODEX START HERE

You are inside the Agent Bridge handoff repository.

## Goal

Make Agent Bridge production-ready as both:

1. a standalone automation project, and
2. a portable module that can be copied into any project root.

## Read First

1. `AGENTS.md`
2. `docs/00_PROJECT_BRIEF.md`
3. `docs/01_ARCHITECTURE.md`
4. `docs/02_PORTABLE_MODULE_DESIGN.md`
5. `docs/03_IMPLEMENTATION_PLAN.md`
6. `docs/04_DETAILED_TODO.md`
7. `docs/05_SELF_TEST_AND_DOGFOOD_PROTOCOL.md`
8. `portable_module/.agent-bridge/README.md`
9. `codex_skill/agent-bridge/SKILL.md`

## First Task

Verify and improve the MVP file-based dry-run core.

Do not implement GUI automation yet.
Do not implement real GitHub polling yet.
Do not implement Gmail sending yet.
Do not implement a downstream app.

## Required Commands

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
python -m agent_bridge.cli init --force
python -m agent_bridge.cli run-once --dry-run --fixture fixtures/fake_agent_report.md
python -m agent_bridge.cli queue list
python -m agent_bridge.cli dispatch-next --dry-run
python -m agent_bridge.cli simulate-dogfood
python -m agent_bridge.cli status
```

## Required Final Report

Write:

```text
workspace/reports/latest_agent_report.md
```

Include:

- summary,
- files inspected,
- files changed,
- CLI commands verified,
- tests run,
- self-test result,
- portable module readiness,
- known limitations,
- next recommended task.
