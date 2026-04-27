# CLI Spec

Required commands:

```bash
agent-bridge init
agent-bridge status
agent-bridge enqueue --type CHATGPT_PM_NEXT_TASK --payload path/to/file.md
agent-bridge queue list
agent-bridge queue pop
agent-bridge dispatch-next
agent-bridge collect-agent-report
agent-bridge send-report-to-pm
agent-bridge run-once
agent-bridge ingest-review --fixture path/to/review.json --dry-run
agent-bridge ingest-ci --fixture path/to/ci_failure.json --dry-run
agent-bridge simulate-dogfood
agent-bridge pause
agent-bridge resume
agent-bridge reset-state
```

Future commands:

```bash
agent-bridge watch-reviews --pr 12  # future real provider polling
agent-bridge watch-ci --pr 12       # future real provider polling
agent-bridge run-loop
agent-bridge install-portable --target /path/to/project
```
