# Portable Module Design

## Goal

Agent Bridge must be usable by copying a module into any project root.

## Target Project Layout

```text
target-project/
  .agent-bridge/
    README.md
    config.yaml
    workspace/
      state/
      queue/
      inbox/
      outbox/
      reports/
      reviews/
      logs/
    templates/
      local_agent_command_wrapper.md
      pm_report_prompt.md
      review_fix_prompt.md
      ci_fix_prompt.md
      owner_decision_email.md
    docs/
      OPERATING_MODEL.md
      TASK_BRIEF_TEMPLATE.md
      REPORT_TEMPLATE.md
      SAFETY.md
    scripts/
      self_test.sh
      ingest_review.sh
      ingest_ci.sh
      queue_list.sh
      run_once.sh
      dispatch_next.sh
      write_report.sh
    fixtures/
      fake_review_digest.md
      fake_ci_failure_digest.md
      risky_review_digest.md
  AGENTS.agent-bridge.snippet.md
```

## Codex Skill Integration

The Codex skill lives at:

```text
~/.codex/skills/agent-bridge/SKILL.md
```

The skill teaches Codex:

1. how to detect `.agent-bridge/`;
2. how to initialize the bridge workspace;
3. how to write reports;
4. how to enqueue commands;
5. how to run dry-run self-tests;
6. how to respect safety gates.

## Design Requirements

- No target-language assumptions.
- No app framework assumptions.
- No repository structure assumptions beyond project root.
- All project-specific context comes from task briefs and reports.
- Portable module must run self-test without modifying project source code.

## Installer Behavior

Standalone Agent Bridge provides:

```bash
python -m agent_bridge.cli install-portable --target /path/to/project --dry-run
python -m agent_bridge.cli install-portable --target /path/to/project
python -m agent_bridge.cli verify-portable --target /path/to/project
```

Rules:

- dry-run prints the install plan and modifies nothing;
- real install copies `.agent-bridge/` into the target root;
- `AGENTS.agent-bridge.snippet.md` is copied by default;
- `--no-include-agents-snippet` skips the snippet;
- existing `.agent-bridge/` or snippet files block installation unless `--force` is used;
- installer writes only inside the target `.agent-bridge/` and optional snippet path;
- installed workspace runtime files start clean, with required workspace folders present.
