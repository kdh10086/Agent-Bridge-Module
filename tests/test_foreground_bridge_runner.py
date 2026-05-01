from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

import agent_bridge.cli as cli_module
from agent_bridge.core.models import BridgeStateName
from agent_bridge.core.state_store import StateStore
from agent_bridge.gui.foreground_bridge_runner import (
    FOREGROUND_TRIGGER_MARKER,
    ForegroundBridgeRunner,
    ForegroundBridgeRunnerConfig,
    targets_with_pm_profile_for_startup,
)
from agent_bridge.gui.macos_apps import GuiTargets, default_gui_targets
from agent_bridge.gui.report_roundtrip import ReportRoundtripResult


class FakeRoundtripRunner:
    def __init__(self, result: ReportRoundtripResult | None = None):
        self.calls = 0
        self.configs = []
        self.result = result or ReportRoundtripResult(
            completed=True,
            reason="LOCAL_AGENT_SUBMIT_ATTEMPTED_STOPPED_FOR_QUEUE",
        )

    def __call__(self, config, gui, queue, event_log, state_store):
        self.calls += 1
        self.configs.append(config)
        return self.result


class InterruptingRoundtripRunner:
    def __init__(self):
        self.calls = 0

    def __call__(self, config, gui, queue, event_log, state_store):
        self.calls += 1
        raise KeyboardInterrupt


class FakeGui:
    pass


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def write_report(workspace: Path, text: str) -> Path:
    path = workspace / "reports" / "latest_agent_report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_config(
    workspace: Path,
    *,
    targets: GuiTargets | None = None,
    max_runtime_seconds: int = 5,
    max_roundtrips: int = 1,
    require_trigger_marker: bool = False,
    process_existing_trigger: bool = False,
    debug: bool = False,
    debug_state_machine: bool = False,
    debug_gui_actions: bool = False,
    debug_screenshots: bool = False,
) -> ForegroundBridgeRunnerConfig:
    return ForegroundBridgeRunnerConfig(
        workspace_dir=workspace,
        template_dir=workspace / "templates",
        targets=targets or default_gui_targets(),
        pm_target_profile=(targets or default_gui_targets()).pm_assistant.profile or "chatgpt_mac",
        watch_report_path=workspace / "reports" / "latest_agent_report.md",
        polling_interval_seconds=1,
        debounce_seconds=0,
        cooldown_seconds=1,
        max_runtime_seconds=max_runtime_seconds,
        max_roundtrips=max_roundtrips,
        require_trigger_marker=require_trigger_marker,
        process_existing_trigger=process_existing_trigger,
        debug=debug,
        debug_state_machine=debug_state_machine,
        debug_gui_actions=debug_gui_actions,
        debug_screenshots=debug_screenshots,
    )


def run_runner(config: ForegroundBridgeRunnerConfig, *, sleep_fn=None, runner=None, clock=None):
    fake_runner = runner or FakeRoundtripRunner()
    fake_clock = clock or Clock()
    result = ForegroundBridgeRunner(
        config,
        roundtrip_runner=fake_runner,
        gui_factory=FakeGui,
        monotonic_fn=fake_clock,
        time_fn=fake_clock,
        sleep_fn=sleep_fn or fake_clock.sleep,
        output_fn=lambda _message: None,
    ).run()
    return result, fake_runner


def read_events(workspace: Path) -> list[dict]:
    path = workspace / "logs" / "bridge.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def report_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_report_hash_change_with_marker_triggers_by_default(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace, "# Report\n\nNo trigger yet.\n")
    clock = Clock()
    changed = False

    def sleep(seconds: float) -> None:
        nonlocal changed
        if not changed:
            write_report(workspace, f"# Report\n\n{FOREGROUND_TRIGGER_MARKER}\n")
            changed = True
        clock.sleep(seconds)

    result, runner = run_runner(make_config(workspace), sleep_fn=sleep, clock=clock)

    assert result.roundtrips_started == 1
    assert result.roundtrips_completed == 1
    assert runner.calls == 1


def test_report_hash_change_without_marker_triggers_by_default(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace, "# Report\n\nInitial.\n")
    clock = Clock()
    changed = False

    def sleep(seconds: float) -> None:
        nonlocal changed
        if not changed:
            write_report(workspace, "# Report\n\nChanged without marker.\n")
            changed = True
        clock.sleep(seconds)

    result, runner = run_runner(make_config(workspace), sleep_fn=sleep, clock=clock)
    change_events = [
        event
        for event in read_events(workspace)
        if event["event_type"] == "foreground_bridge_report_change_detected"
    ]

    assert result.roundtrips_started == 1
    assert result.roundtrips_completed == 1
    assert runner.calls == 1
    assert change_events[-1]["metadata"]["trigger_marker_present"] is False


def test_same_hash_does_not_retrigger(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace, f"# Report\n\n{FOREGROUND_TRIGGER_MARKER}\n")

    result, runner = run_runner(
        make_config(workspace, max_runtime_seconds=2, max_roundtrips=0)
    )

    assert result.reason == "MAX_RUNTIME_REACHED"
    assert runner.calls == 0


def test_startup_existing_report_with_marker_does_not_trigger_by_default(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace, f"# Report\n\n{FOREGROUND_TRIGGER_MARKER}\n")
    stale_seen = workspace / "state" / "last_seen_report_hash"
    stale_seen.parent.mkdir(parents=True, exist_ok=True)
    stale_seen.write_text("stale-hash\n", encoding="utf-8")

    result, runner = run_runner(
        make_config(workspace, max_runtime_seconds=2, max_roundtrips=0)
    )
    event_types = [event["event_type"] for event in read_events(workspace)]

    assert result.reason == "MAX_RUNTIME_REACHED"
    assert runner.calls == 0
    assert "foreground_bridge_initial_baseline_recorded" in event_types
    assert "foreground_bridge_report_change_detected" not in event_types


def test_startup_existing_report_without_marker_does_not_trigger(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace, "# Report\n\nNo marker.\n")

    result, runner = run_runner(
        make_config(workspace, max_runtime_seconds=2, max_roundtrips=0)
    )
    event_types = [event["event_type"] for event in read_events(workspace)]

    assert result.reason == "MAX_RUNTIME_REACHED"
    assert runner.calls == 0
    assert "foreground_bridge_initial_baseline_recorded" in event_types
    assert "foreground_bridge_report_change_detected" not in event_types


def test_startup_timing_is_logged(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace, "# Report\n\nInitial.\n")

    result, runner = run_runner(
        make_config(workspace, max_runtime_seconds=2, max_roundtrips=0)
    )
    started_events = [
        event
        for event in read_events(workspace)
        if event["event_type"] == "foreground_bridge_runner_started"
    ]

    assert result.reason == "MAX_RUNTIME_REACHED"
    assert runner.calls == 0
    assert started_events
    assert "startup_elapsed_seconds" in started_events[-1]["metadata"]


def test_process_existing_trigger_processes_existing_report_once(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace, "# Report\n\nNo marker.\n")

    result, runner = run_runner(
        make_config(
            workspace,
            max_runtime_seconds=30,
            max_roundtrips=1,
            process_existing_trigger=True,
        )
    )
    event_types = [event["event_type"] for event in read_events(workspace)]

    assert result.reason == "MAX_ROUNDTRIPS_REACHED"
    assert result.triggers_accepted == 1
    assert runner.calls == 1
    assert "foreground_bridge_existing_trigger_detected" in event_types


def test_process_existing_trigger_with_required_marker_processes_existing_marker_once(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    write_report(workspace, f"# Report\n\n{FOREGROUND_TRIGGER_MARKER}\n")

    result, runner = run_runner(
        make_config(
            workspace,
            max_runtime_seconds=30,
            max_roundtrips=1,
            require_trigger_marker=True,
            process_existing_trigger=True,
        )
    )

    assert result.reason == "MAX_ROUNDTRIPS_REACHED"
    assert result.triggers_accepted == 1
    assert runner.calls == 1


def test_already_processed_hash_does_not_retrigger(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace, "# Report\n\nInitial.\n")
    marker_report = f"# Report\n\n{FOREGROUND_TRIGGER_MARKER}\n"
    processed_path = workspace / "state" / "last_processed_report_hash"
    processed_path.parent.mkdir(parents=True, exist_ok=True)
    processed_path.write_text(f"{report_hash(marker_report)}\n", encoding="utf-8")
    clock = Clock()
    changed = False

    def sleep(seconds: float) -> None:
        nonlocal changed
        if not changed:
            write_report(workspace, marker_report)
            changed = True
        clock.sleep(seconds)

    result, runner = run_runner(
        make_config(workspace, max_runtime_seconds=3, max_roundtrips=0),
        sleep_fn=sleep,
        clock=clock,
    )
    ignored_events = [
        event
        for event in read_events(workspace)
        if event["event_type"] == "foreground_bridge_report_hash_ignored"
    ]

    assert result.reason == "MAX_RUNTIME_REACHED"
    assert runner.calls == 0
    assert ignored_events
    assert ignored_events[-1]["metadata"]["reason"] == "already_processed"


def test_required_trigger_marker_missing_does_not_run_bridge(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace, "# Report\n\nInitial.\n")
    clock = Clock()
    changed = False

    def sleep(seconds: float) -> None:
        nonlocal changed
        if not changed:
            write_report(workspace, "# Report\n\nChanged without marker.\n")
            changed = True
        clock.sleep(seconds)

    result, runner = run_runner(
        make_config(
            workspace,
            max_runtime_seconds=3,
            max_roundtrips=0,
            require_trigger_marker=True,
        ),
        sleep_fn=sleep,
        clock=clock,
    )

    assert result.reason == "MAX_RUNTIME_REACHED"
    assert result.changes_seen == 1
    assert runner.calls == 0


def test_required_trigger_marker_present_runs_bridge(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace, "# Report\n\nInitial.\n")
    clock = Clock()
    changed = False

    def sleep(seconds: float) -> None:
        nonlocal changed
        if not changed:
            write_report(workspace, f"# Report\n\n{FOREGROUND_TRIGGER_MARKER}\n")
            changed = True
        clock.sleep(seconds)

    result, runner = run_runner(
        make_config(workspace, require_trigger_marker=True),
        sleep_fn=sleep,
        clock=clock,
    )

    assert result.roundtrips_started == 1
    assert runner.calls == 1


def test_lock_prevents_concurrent_bridge_run(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace, "# Report\n\nInitial.\n")
    lock_path = workspace / "state" / "foreground_bridge_runner.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps({"pid": 1, "created_at_epoch": 0}), encoding="utf-8")
    clock = Clock()
    changed = False

    def sleep(seconds: float) -> None:
        nonlocal changed
        if not changed:
            write_report(workspace, f"# Report\n\n{FOREGROUND_TRIGGER_MARKER}\n")
            changed = True
        clock.sleep(seconds)

    result, runner = run_runner(
        make_config(workspace, max_runtime_seconds=3, max_roundtrips=0),
        sleep_fn=sleep,
        clock=clock,
    )

    assert result.reason == "MAX_RUNTIME_REACHED"
    assert runner.calls == 0
    assert lock_path.exists()


def test_keyboard_interrupt_exits_cleanly(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace, "# Report\n\nInitial.\n")

    def sleep(_seconds: float) -> None:
        raise KeyboardInterrupt

    result, runner = run_runner(make_config(workspace), sleep_fn=sleep)

    assert result.keyboard_interrupt
    assert result.reason == "KEYBOARD_INTERRUPT"
    assert runner.calls == 0


def test_keyboard_interrupt_during_active_bridge_counts_interrupted(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace, "# Report\n\nInitial.\n")
    clock = Clock()
    changed = False

    def sleep(seconds: float) -> None:
        nonlocal changed
        if not changed:
            write_report(workspace, "# Report\n\nChanged after startup.\n")
            changed = True
        clock.sleep(seconds)

    result, runner = run_runner(
        make_config(workspace, max_runtime_seconds=30),
        sleep_fn=sleep,
        runner=InterruptingRoundtripRunner(),
        clock=clock,
    )

    assert result.keyboard_interrupt
    assert result.roundtrips_started == 1
    assert result.roundtrips_interrupted == 1
    assert runner.calls == 1


def test_max_roundtrips_stops_after_configured_count(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace, "# Report\n\nInitial.\n")
    clock = Clock()
    changed = False

    def sleep(seconds: float) -> None:
        nonlocal changed
        if not changed:
            write_report(workspace, f"# Report\n\n{FOREGROUND_TRIGGER_MARKER}\n")
            changed = True
        clock.sleep(seconds)

    result, runner = run_runner(
        make_config(workspace, max_runtime_seconds=30, max_roundtrips=1),
        sleep_fn=sleep,
        clock=clock,
    )

    assert result.reason == "MAX_ROUNDTRIPS_REACHED"
    assert runner.calls == 1


def test_selected_pm_profile_is_passed_to_roundtrip(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace, "# Report\n\nInitial.\n")
    targets = default_gui_targets()
    targets = GuiTargets(
        pm_assistant=targets.pm_assistant.__class__(
            **{**targets.pm_assistant.__dict__, "profile": "chatgpt_chrome_app"}
        ),
        local_agent=targets.local_agent,
    )
    clock = Clock()
    changed = False

    def sleep(seconds: float) -> None:
        nonlocal changed
        if not changed:
            write_report(workspace, f"# Report\n\n{FOREGROUND_TRIGGER_MARKER}\n")
            changed = True
        clock.sleep(seconds)

    monkeypatch.setattr(
        "agent_bridge.gui.foreground_bridge_runner.targets_with_pm_profile",
        lambda runtime_targets, _profile: runtime_targets,
    )
    result, runner = run_runner(
        make_config(workspace, targets=targets),
        sleep_fn=sleep,
        clock=clock,
    )

    assert result.roundtrips_started == 1
    assert runner.configs[0].targets.pm_assistant.profile == "chatgpt_chrome_app"


def test_startup_target_selection_does_not_run_live_pm_resolution(monkeypatch):
    targets = default_gui_targets()

    def fail_live_resolution(*_args, **_kwargs):
        raise AssertionError("live PM diagnostics should not run during startup target selection")

    monkeypatch.setattr(
        "agent_bridge.gui.foreground_bridge_runner.resolve_runtime_pm_target",
        fail_live_resolution,
    )

    selected = targets_with_pm_profile_for_startup(targets, "chatgpt_chrome_app")

    assert selected.pm_assistant.profile == "chatgpt_chrome_app"
    assert selected.pm_assistant.visual_asset_profile == "chatgpt_chrome_app"
    assert selected.pm_assistant.bundle_id is None


def test_safety_pause_stops_runner(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_report(workspace, "# Report\n\nInitial.\n")
    store = StateStore(workspace / "state" / "state.json")
    state = store.load()
    state.safety_pause = True
    state.state = BridgeStateName.PAUSED_FOR_USER_DECISION
    store.save(state)

    result, runner = run_runner(make_config(workspace))

    assert result.safety_paused
    assert runner.calls == 0


def configure_cli(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    config_dir = tmp_path / "config"
    template_dir = tmp_path / "templates"
    write_report(workspace, "# Report\n\nInitial.\n")
    (workspace / "queue").mkdir(parents=True)
    config_dir.mkdir(parents=True)
    template_dir.mkdir(parents=True)
    (config_dir / "default.yaml").write_text("apps: {}\n", encoding="utf-8")
    monkeypatch.setattr(cli_module, "ROOT", tmp_path)
    monkeypatch.setattr(cli_module, "WORKSPACE", workspace)
    monkeypatch.setattr(cli_module, "QUEUE_DIR", workspace / "queue")
    monkeypatch.setattr(cli_module, "STATE_PATH", workspace / "state" / "state.json")
    monkeypatch.setattr(cli_module, "LOG_PATH", workspace / "logs" / "bridge.jsonl")
    monkeypatch.setattr(cli_module, "TEMPLATE_DIR", template_dir)
    monkeypatch.setattr(cli_module, "CONFIG_DIR", config_dir)


def test_run_bridge_cli_accepts_chatgpt_mac(monkeypatch, tmp_path: Path):
    configure_cli(monkeypatch, tmp_path)
    captured = {}

    class FakeRunner:
        def __init__(self, config, **_kwargs):
            captured["profile"] = config.pm_target_profile
            captured["require_trigger_marker"] = config.require_trigger_marker

        def run(self):
            return SimpleNamespace(reason="MAX_RUNTIME_REACHED", roundtrips_started=0, roundtrips_completed=0)

    monkeypatch.setattr(cli_module, "targets_with_pm_profile", lambda targets, profile: targets)
    monkeypatch.setattr(cli_module, "ForegroundBridgeRunner", FakeRunner)

    result = CliRunner().invoke(
        cli_module.app,
        ["run-bridge", "--pm-target", "chatgpt_mac", "--max-runtime-seconds", "1"],
    )

    assert result.exit_code == 0
    assert captured["profile"] == "chatgpt_mac"
    assert captured["require_trigger_marker"] is False


def test_run_bridge_cli_accepts_chatgpt_chrome_app(monkeypatch, tmp_path: Path):
    configure_cli(monkeypatch, tmp_path)
    captured = {}

    class FakeRunner:
        def __init__(self, config, **_kwargs):
            captured["profile"] = config.pm_target_profile

        def run(self):
            return SimpleNamespace(reason="MAX_RUNTIME_REACHED", roundtrips_started=0, roundtrips_completed=0)

    monkeypatch.setattr(cli_module, "targets_with_pm_profile", lambda targets, profile: targets)
    monkeypatch.setattr(cli_module, "ForegroundBridgeRunner", FakeRunner)

    result = CliRunner().invoke(
        cli_module.app,
        ["run-bridge", "--pm-target", "chatgpt_chrome_app", "--max-runtime-seconds", "1"],
    )

    assert result.exit_code == 0
    assert captured["profile"] == "chatgpt_chrome_app"


def test_run_bridge_cli_defers_live_pm_resolution(monkeypatch, tmp_path: Path):
    configure_cli(monkeypatch, tmp_path)

    class FakeRunner:
        def __init__(self, config, **_kwargs):
            self.config = config

        def run(self):
            return SimpleNamespace(reason="MAX_RUNTIME_REACHED", roundtrips_started=0, roundtrips_completed=0)

    def fail_live_resolution(_targets, _profile):
        raise AssertionError("run-bridge startup must not run live PM target resolution")

    monkeypatch.setattr(cli_module, "targets_with_pm_profile", fail_live_resolution)
    monkeypatch.setattr(cli_module, "ForegroundBridgeRunner", FakeRunner)

    result = CliRunner().invoke(
        cli_module.app,
        ["run-bridge", "--pm-target", "chatgpt_chrome_app", "--max-runtime-seconds", "1"],
    )

    assert result.exit_code == 0


def test_run_bridge_cli_accepts_process_existing_trigger(monkeypatch, tmp_path: Path):
    configure_cli(monkeypatch, tmp_path)
    captured = {}

    class FakeRunner:
        def __init__(self, config, **_kwargs):
            captured["process_existing_trigger"] = config.process_existing_trigger

        def run(self):
            return SimpleNamespace(reason="MAX_RUNTIME_REACHED", roundtrips_started=0, roundtrips_completed=0)

    monkeypatch.setattr(cli_module, "targets_with_pm_profile", lambda targets, profile: targets)
    monkeypatch.setattr(cli_module, "ForegroundBridgeRunner", FakeRunner)

    result = CliRunner().invoke(
        cli_module.app,
        [
            "run-bridge",
            "--pm-target",
            "chatgpt_mac",
            "--max-runtime-seconds",
            "1",
            "--process-existing-trigger",
        ],
    )

    assert result.exit_code == 0
    assert captured["process_existing_trigger"] is True


def test_run_bridge_cli_accepts_debug_flags(monkeypatch, tmp_path: Path):
    configure_cli(monkeypatch, tmp_path)
    captured = {}

    class FakeRunner:
        def __init__(self, config, **_kwargs):
            captured["debug"] = config.debug
            captured["debug_state_machine"] = config.debug_state_machine
            captured["debug_gui_actions"] = config.debug_gui_actions
            captured["debug_screenshots"] = config.debug_screenshots
            captured["debug_all_template_comparisons"] = config.debug_all_template_comparisons

        def run(self):
            return SimpleNamespace(reason="MAX_RUNTIME_REACHED", roundtrips_started=0, roundtrips_completed=0)

    monkeypatch.setattr(cli_module, "targets_with_pm_profile", lambda targets, profile: targets)
    monkeypatch.setattr(cli_module, "ForegroundBridgeRunner", FakeRunner)

    result = CliRunner().invoke(
        cli_module.app,
        [
            "run-bridge",
            "--pm-target",
            "chatgpt_mac",
            "--max-runtime-seconds",
            "1",
            "--debug",
            "--debug-state-machine",
            "--debug-gui-actions",
            "--debug-screenshots",
            "--debug-all-template-comparisons",
        ],
    )

    assert result.exit_code == 0
    assert captured == {
        "debug": True,
        "debug_state_machine": True,
        "debug_gui_actions": True,
        "debug_screenshots": True,
        "debug_all_template_comparisons": True,
    }


def test_run_bridge_cli_accepts_require_trigger_marker(monkeypatch, tmp_path: Path):
    configure_cli(monkeypatch, tmp_path)
    captured = {}

    class FakeRunner:
        def __init__(self, config, **_kwargs):
            captured["require_trigger_marker"] = config.require_trigger_marker

        def run(self):
            return SimpleNamespace(reason="MAX_RUNTIME_REACHED", roundtrips_started=0, roundtrips_completed=0)

    monkeypatch.setattr(cli_module, "targets_with_pm_profile", lambda targets, profile: targets)
    monkeypatch.setattr(cli_module, "ForegroundBridgeRunner", FakeRunner)

    result = CliRunner().invoke(
        cli_module.app,
        [
            "run-bridge",
            "--pm-target",
            "chatgpt_mac",
            "--max-runtime-seconds",
            "1",
            "--require-trigger-marker",
        ],
    )

    assert result.exit_code == 0
    assert captured["require_trigger_marker"] is True


def test_run_bridge_help_describes_marker_free_default():
    result = CliRunner().invoke(cli_module.app, ["run-bridge", "--help"])

    assert result.exit_code == 0
    assert "Require" in result.output
    assert "post-startup" in result.output
    assert "no-require-trigg" in result.output
    assert "--debug" in result.output
    assert "debug-all" in result.output
    assert "all comparisons" in result.output


def test_run_bridge_helper_script_does_not_force_trigger_marker():
    script = Path("scripts/run_bridge.sh").read_text(encoding="utf-8")
    exec_lines = [line for line in script.splitlines() if line.startswith("exec ")]

    assert exec_lines
    assert "--require-trigger-marker" not in exec_lines[-1]


def test_diagnose_pm_visual_sequence_help_supports_both_profiles():
    result = CliRunner().invoke(cli_module.app, ["diagnose-pm-visual-sequence", "--help"])

    assert result.exit_code == 0
    assert "--pm-target" in result.output
    assert "chatgpt_mac" in result.output
    assert "chatgpt_chrome_app" in result.output
    assert "--click-test" in result.output


def test_diagnose_paste_backends_help_supports_both_profiles():
    result = CliRunner().invoke(cli_module.app, ["diagnose-paste-backends", "--help"])

    assert result.exit_code == 0
    assert "--pm-target" in result.output
    assert "chatgpt_mac" in result.output
    assert "chatgpt_chrome_app" in result.output


def test_run_bridge_cli_rejects_invalid_pm_target(monkeypatch, tmp_path: Path):
    configure_cli(monkeypatch, tmp_path)

    result = CliRunner().invoke(
        cli_module.app,
        ["run-bridge", "--pm-target", "invalid", "--max-runtime-seconds", "1"],
    )

    assert result.exit_code != 0
    assert "invalid" in result.output
