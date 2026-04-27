from __future__ import annotations

import json
from pathlib import Path

from agent_bridge.core.command_queue import CommandQueue
from agent_bridge.core.models import BridgeStateName, Command, CommandType
from agent_bridge.core.state_store import StateStore
from agent_bridge.orchestrator import RunLoop, RunLoopConfig


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "agent_bridge" / "templates"


def read_events(workspace: Path) -> list[dict]:
    log_path = workspace / "logs" / "bridge.jsonl"
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def make_loop(workspace: Path, **overrides) -> RunLoop:
    values = {
        "workspace_dir": workspace,
        "template_dir": TEMPLATE_DIR,
        "max_cycles": 1,
        "max_runtime_seconds": 60,
        "polling_interval_seconds": 0,
    }
    values.update(overrides)
    config = RunLoopConfig(**values)
    return RunLoop(config, sleep_fn=lambda _: None)


def enqueue_command(workspace: Path, payload: Path, command_type: CommandType = CommandType.REQUEST_STATUS_REPORT) -> None:
    CommandQueue(workspace / "queue").enqueue(
        Command(
            id="cmd_test",
            type=command_type,
            source="test",
            payload_path=str(payload),
            dedupe_key=f"test:{payload.name}:{command_type.value}",
        )
    )


def test_run_loop_stops_at_max_cycles(tmp_path: Path):
    workspace = tmp_path / "workspace"

    result = make_loop(workspace, max_cycles=2).run()

    events = [event["event_type"] for event in read_events(workspace)]
    assert result.reason == "MAX_CYCLES_REACHED"
    assert result.cycles_completed == 2
    assert events.count("run_loop_cycle_start") == 2
    assert "run_loop_max_cycles_reached" in events
    assert events[-1] == "run_loop_complete"


def test_run_loop_stops_at_max_runtime(tmp_path: Path):
    workspace = tmp_path / "workspace"

    result = make_loop(workspace, max_runtime_seconds=0).run()

    events = [event["event_type"] for event in read_events(workspace)]
    assert result.reason == "MAX_RUNTIME_REACHED"
    assert result.cycles_completed == 0
    assert "run_loop_max_runtime_reached" in events


def test_run_loop_stops_immediately_when_safety_pause_is_true(tmp_path: Path):
    workspace = tmp_path / "workspace"
    state_store = StateStore(workspace / "state" / "state.json")
    state = state_store.load()
    state.safety_pause = True
    state.state = BridgeStateName.PAUSED_FOR_USER_DECISION
    state_store.save(state)

    result = make_loop(workspace).run()

    events = [event["event_type"] for event in read_events(workspace)]
    assert result.reason == "SAFETY_PAUSED"
    assert result.cycles_completed == 0
    assert "run_loop_safety_pause" in events
    assert "run_loop_cycle_start" not in events


def test_run_loop_logs_queue_empty_when_no_commands_exist(tmp_path: Path):
    workspace = tmp_path / "workspace"

    result = make_loop(workspace).run()

    events = [event["event_type"] for event in read_events(workspace)]
    assert result.queue_empty_count == 1
    assert "run_loop_queue_empty" in events


def test_run_loop_dry_runs_dispatch_when_command_exists(tmp_path: Path):
    workspace = tmp_path / "workspace"
    payload = tmp_path / "payload.md"
    payload.write_text("# Status\n\nReport current status.", encoding="utf-8")
    enqueue_command(workspace, payload)

    result = make_loop(workspace).run()

    events = read_events(workspace)
    event_types = [event["event_type"] for event in events]
    dispatch_started = [event for event in events if event["event_type"] == "dispatch_started"]
    assert result.dispatched_count == 1
    assert "run_loop_command_dispatched_dry_run" in event_types
    assert dispatch_started[0]["metadata"]["dry_run"] is True
    assert (workspace / "queue" / "in_progress.json").exists()


def test_run_loop_stops_when_dispatcher_triggers_safety_pause(tmp_path: Path):
    workspace = tmp_path / "workspace"
    payload = tmp_path / "risky.md"
    payload.write_text("# Risky\n\nRISK_HIGH needs owner approval.", encoding="utf-8")
    enqueue_command(workspace, payload)

    result = make_loop(workspace).run()

    state = StateStore(workspace / "state" / "state.json").load()
    events = [event["event_type"] for event in read_events(workspace)]
    assert result.reason == "SAFETY_PAUSED"
    assert state.safety_pause
    assert "run_loop_safety_pause" in events
    assert (workspace / "inbox" / "user_decision_request.md").exists()
    assert (workspace / "queue" / "failed_commands.jsonl").exists()


def test_run_loop_watcher_dry_run_does_not_mutate_queue(tmp_path: Path):
    workspace = tmp_path / "workspace"
    calls: list[bool] = []

    def fake_review_watcher(**kwargs):
        calls.append(kwargs["dry_run"])
        return False, None, workspace / "inbox" / "github_review_digest.md", None, ""

    config = RunLoopConfig(
        workspace_dir=workspace,
        template_dir=TEMPLATE_DIR,
        dry_run=True,
        max_cycles=1,
        max_runtime_seconds=60,
        polling_interval_seconds=0,
        watch_reviews=True,
        owner="owner",
        repo="repo",
        pr_number=123,
    )
    result = RunLoop(config, sleep_fn=lambda _: None, review_watcher=fake_review_watcher).run()

    assert result.queue_empty_count == 1
    assert calls == [True]
    assert CommandQueue(workspace / "queue").list_pending() == []
    assert not (workspace / "queue" / "in_progress.json").exists()


def test_run_loop_records_structured_jsonl_events(tmp_path: Path):
    workspace = tmp_path / "workspace"

    make_loop(workspace).run()

    events = read_events(workspace)
    assert events
    assert all("timestamp" in event for event in events)
    assert all("event_type" in event for event in events)
    assert all("metadata" in event for event in events)
