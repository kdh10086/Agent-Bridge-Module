from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_bridge.core.models import BridgeStateName
from agent_bridge.core.state_store import StateStore
from agent_bridge.gui.external_runner_daemon import (
    QUEUE_TRIGGER,
    REPORT_TRIGGER,
    ExternalGuiRunner,
    ExternalGuiRunnerConfig,
    ExternalGuiRunnerError,
)
from agent_bridge.gui.macos_apps import default_gui_targets
from agent_bridge.gui.report_roundtrip import ReportRoundtripResult


class Clock:
    def __init__(self, values: list[float]):
        self.values = values
        self.last = values[-1] if values else 0.0

    def __call__(self) -> float:
        if self.values:
            self.last = self.values.pop(0)
        return self.last


class FakeRoundtripRunner:
    def __init__(self, result: ReportRoundtripResult | None = None):
        self.calls = 0
        self.result = result or ReportRoundtripResult(completed=True, reason="ONE_CYCLE_COMPLETE")

    def __call__(self, config, gui, queue, event_log, state_store):
        self.calls += 1
        return self.result


class FakeGui:
    pass


def make_config(
    workspace: Path,
    *,
    watch_reports: bool = True,
    watch_queue: bool = True,
    max_runtime_seconds: int = 5,
    max_roundtrips: int | None = 1,
    use_report_hash_guard: bool = False,
) -> ExternalGuiRunnerConfig:
    return ExternalGuiRunnerConfig(
        workspace_dir=workspace,
        template_dir=workspace / "templates",
        targets=default_gui_targets(),
        auto_confirm=True,
        watch_reports=watch_reports,
        watch_queue=watch_queue,
        polling_interval_seconds=1,
        max_runtime_seconds=max_runtime_seconds,
        debounce_seconds=0,
        cooldown_seconds=0,
        stale_lock_seconds=10,
        max_roundtrips=max_roundtrips,
        use_report_hash_guard=use_report_hash_guard,
    )


def write_report(workspace: Path) -> None:
    path = workspace / "reports" / "latest_agent_report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Report\n", encoding="utf-8")


def write_queue(workspace: Path) -> None:
    path = workspace / "queue" / "pending_commands.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def write_trigger(workspace: Path, name: str) -> Path:
    path = workspace / "triggers" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("trigger\n", encoding="utf-8")
    return path


def read_event_types(workspace: Path) -> list[str]:
    log_path = workspace / "logs" / "bridge.jsonl"
    if not log_path.exists():
        return []
    return [
        json.loads(line)["event_type"]
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def run_runner(
    config: ExternalGuiRunnerConfig,
    *,
    env: dict[str, str] | None = None,
    runner: FakeRoundtripRunner | None = None,
    monotonic: Clock | None = None,
    time_clock: Clock | None = None,
):
    fake_runner = runner or FakeRoundtripRunner()
    return (
        ExternalGuiRunner(
            config,
            env=env or {},
            roundtrip_runner=fake_runner,
            gui_factory=FakeGui,
            monotonic_fn=monotonic or Clock([0, 0]),
            time_fn=time_clock or Clock([100, 100, 100]),
            sleep_fn=lambda _: None,
        ).run(),
        fake_runner,
    )


def test_refuses_when_codex_sandbox_is_set(tmp_path: Path):
    workspace = tmp_path / "workspace"

    with pytest.raises(ExternalGuiRunnerError, match="CODEX_SANDBOX"):
        run_runner(make_config(workspace), env={"CODEX_SANDBOX": "1"})

    assert "external_runner_stopped" in read_event_types(workspace)


def test_allows_full_access_context_without_codex_sandbox(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace)
    write_queue(workspace)
    write_trigger(workspace, REPORT_TRIGGER)

    result, runner = run_runner(
        make_config(workspace),
        env={"CODEX_SHELL": "1", "CODEX_THREAD_ID": "thread"},
    )

    assert result.roundtrips_started == 1
    assert result.roundtrips_completed == 1
    assert runner.calls == 1


def test_detects_report_trigger(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace)
    write_queue(workspace)
    trigger = write_trigger(workspace, REPORT_TRIGGER)

    result, runner = run_runner(make_config(workspace))

    assert result.triggers_detected == 1
    assert runner.calls == 1
    assert not trigger.exists()
    assert list((workspace / "triggers").glob(f"{REPORT_TRIGGER}.consumed.*"))
    assert "external_runner_trigger_detected" in read_event_types(workspace)


def test_detects_queue_trigger(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace)
    write_queue(workspace)
    write_trigger(workspace, QUEUE_TRIGGER)

    result, runner = run_runner(make_config(workspace))

    assert result.triggers_detected == 1
    assert runner.calls == 1


def test_lock_prevents_concurrent_run(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace)
    write_queue(workspace)
    trigger = write_trigger(workspace, REPORT_TRIGGER)
    lock_path = workspace / "state" / "external_runner.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps({"pid": 1, "created_at_epoch": 100}), encoding="utf-8")

    result, runner = run_runner(
        make_config(workspace, max_roundtrips=None),
        monotonic=Clock([0, 0, 20]),
        time_clock=Clock([100, 100, 100]),
    )

    assert result.roundtrips_started == 0
    assert runner.calls == 0
    assert trigger.exists()
    assert lock_path.exists()


def test_stale_lock_is_replaced_and_released(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace)
    write_queue(workspace)
    write_trigger(workspace, REPORT_TRIGGER)
    lock_path = workspace / "state" / "external_runner.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps({"pid": 1, "created_at_epoch": 0}), encoding="utf-8")

    result, runner = run_runner(
        make_config(workspace),
        time_clock=Clock([100, 100, 100, 100]),
    )

    assert result.roundtrips_started == 1
    assert runner.calls == 1
    assert not lock_path.exists()


def test_safety_pause_stops_runner(tmp_path: Path):
    workspace = tmp_path / "workspace"
    state_store = StateStore(workspace / "state" / "state.json")
    state = state_store.load()
    state.safety_pause = True
    state.state = BridgeStateName.PAUSED_FOR_USER_DECISION
    state_store.save(state)
    write_report(workspace)
    write_queue(workspace)
    write_trigger(workspace, REPORT_TRIGGER)

    result, runner = run_runner(make_config(workspace))

    assert result.reason == "SAFETY_PAUSE"
    assert result.safety_paused
    assert runner.calls == 0


def test_max_runtime_stops_runner_without_trigger(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace)
    write_queue(workspace)

    result, runner = run_runner(
        make_config(workspace, max_runtime_seconds=1, max_roundtrips=None),
        monotonic=Clock([0, 2]),
    )

    assert result.reason == "MAX_RUNTIME_REACHED"
    assert result.max_runtime_reached
    assert runner.calls == 0


def test_debounce_groups_immediate_triggers_into_one_roundtrip(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace)
    write_queue(workspace)
    write_trigger(workspace, REPORT_TRIGGER)
    write_trigger(workspace, QUEUE_TRIGGER)

    result, runner = run_runner(make_config(workspace))

    assert result.triggers_detected == 2
    assert result.roundtrips_started == 1
    assert runner.calls == 1


def test_report_hash_change_triggers_once_with_hash_guard(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace)
    write_queue(workspace)
    runner = ExternalGuiRunner(
        make_config(workspace, use_report_hash_guard=True),
        env={},
        roundtrip_runner=FakeRoundtripRunner(),
        gui_factory=FakeGui,
        sleep_fn=lambda _: None,
    )
    report_path = workspace / "reports" / "latest_agent_report.md"
    old_hash = runner._current_report_hash()
    report_path.write_text("# Report\n\nchanged\n", encoding="utf-8")

    triggers = runner._detect_triggers(
        baseline_report_mtime=0,
        baseline_queue_mtime=None,
        baseline_report_hash=old_hash,
    )

    assert len(triggers) == 1
    assert triggers[0].kind == "report_roundtrip"
    assert triggers[0].reason == "report_hash_changed"


def test_same_report_hash_does_not_retrigger_with_hash_guard(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace)
    write_queue(workspace)
    runner = ExternalGuiRunner(
        make_config(workspace, use_report_hash_guard=True),
        env={},
        roundtrip_runner=FakeRoundtripRunner(),
        gui_factory=FakeGui,
        sleep_fn=lambda _: None,
    )
    report_hash = runner._current_report_hash()

    triggers = runner._detect_triggers(
        baseline_report_mtime=0,
        baseline_queue_mtime=None,
        baseline_report_hash=report_hash,
    )

    assert triggers == []


def test_processed_report_hash_does_not_retrigger_with_hash_guard(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace)
    write_queue(workspace)
    runner = ExternalGuiRunner(
        make_config(workspace, use_report_hash_guard=True),
        env={},
        roundtrip_runner=FakeRoundtripRunner(),
        gui_factory=FakeGui,
        sleep_fn=lambda _: None,
    )
    report_hash = runner._current_report_hash()
    (workspace / "state").mkdir(parents=True, exist_ok=True)
    (workspace / "state" / "last_processed_report_hash").write_text(
        f"{report_hash}\n",
        encoding="utf-8",
    )

    triggers = runner._detect_triggers(
        baseline_report_mtime=0,
        baseline_queue_mtime=None,
        baseline_report_hash="old",
    )

    assert triggers == []


def test_no_github_or_gmail_events_are_introduced(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace)
    write_queue(workspace)
    write_trigger(workspace, REPORT_TRIGGER)

    run_runner(make_config(workspace))

    event_types = read_event_types(workspace)
    assert all("github" not in event.lower() for event in event_types)
    assert all("gmail" not in event.lower() for event in event_types)
