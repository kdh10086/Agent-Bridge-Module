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
- fake command can be enqueued.
- dry-run dispatch prints prompt.
- no project source code is modified.

## Dogfooding Goal

Agent Bridge must be able to test its own automation loop without real GUI or GitHub dependencies.
