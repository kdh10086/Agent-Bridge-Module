# Agent Bridge Portable Handoff

Agent Bridge is a general-purpose automation bridge that coordinates:

- a long-context PM assistant,
- a local coding agent,
- GitHub PR review comments,
- CI failures,
- owner escalation.

It is designed in two layers:

1. **Standalone development repository**  
   Used to develop and test Agent Bridge itself.

2. **Portable project module**  
   A `.agent-bridge/` folder and Codex Skill that can be copied into any project root.

## Why Portable?

The final goal is to use Agent Bridge across arbitrary projects:

- application repositories,
- ML/research codebases,
- paper experiment repositories,
- infrastructure repositories,
- documentation-heavy projects.

No downstream project assumptions are hard-coded.

## Quick Start for Developing Agent Bridge Itself

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
```

## Quick Start for Using Agent Bridge in Another Project

Copy these into the target project root:

```text
.agent-bridge/
AGENTS.agent-bridge.snippet.md
```

Install the Codex skill:

```text
codex_skill/agent-bridge/SKILL.md
→ ~/.codex/skills/agent-bridge/SKILL.md
```

Then tell Codex:

```text
Read ~/.codex/skills/agent-bridge/SKILL.md and .agent-bridge/README.md.
Use Agent Bridge for this project.
Do not modify project code yet.
Initialize the bridge workspace and run the dry-run self-test.
```
