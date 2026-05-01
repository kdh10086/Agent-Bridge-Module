from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

import agent_bridge.cli as cli_module
from agent_bridge.core.models import BridgeStateName
from agent_bridge.core.state_store import StateStore
from agent_bridge.gui.gui_automation import GuiAutomationAdapter
from agent_bridge.gui.gui_dogfood import GuiDogfoodConfig, GuiDogfoodError, run_gui_bridge_dogfood
from agent_bridge.gui.macos_apps import ManualStageTarget, default_gui_targets


class FakeGui(GuiAutomationAdapter):
    def __init__(self, pm_response: str = "# PM Response\n\nImplement the next safe step."):
        self.pm_response = pm_response
        self.actions: list[str] = []
        self.clipboard: list[str] = []

    def activate_app(self, target: ManualStageTarget) -> None:
        self.actions.append(f"activate:{target.app_name}")

    def copy_text_to_clipboard(self, text: str) -> None:
        self.actions.append("copy_text")
        self.clipboard.append(text)

    def paste_clipboard(self) -> None:
        self.actions.append("paste")

    def submit(self) -> None:
        self.actions.append("submit")

    def wait_for_response(self, timeout_seconds: int) -> None:
        self.actions.append(f"wait:{timeout_seconds}")

    def copy_response_text(self) -> str:
        self.actions.append("copy_response")
        return self.pm_response


def write_templates(template_dir: Path) -> None:
    template_dir.mkdir(parents=True)
    (template_dir / "pm_report_prompt.md").write_text("PM prompt:\n{report}", encoding="utf-8")
    (template_dir / "codex_command_wrapper.md").write_text(
        "Type={command_type}\nSource={source}\nPayload={payload}",
        encoding="utf-8",
    )


def write_report(workspace: Path, text: str = "# Report\n\nReady.") -> None:
    reports = workspace / "reports"
    reports.mkdir(parents=True)
    (reports / "latest_agent_report.md").write_text(text, encoding="utf-8")


def make_config(
    workspace: Path,
    template_dir: Path,
    *,
    auto_confirm: bool = True,
    max_cycles: int = 1,
    max_runtime_seconds: int = 120,
) -> GuiDogfoodConfig:
    return GuiDogfoodConfig(
        workspace_dir=workspace,
        template_dir=template_dir,
        targets=default_gui_targets(),
        auto_confirm=auto_confirm,
        max_cycles=max_cycles,
        max_runtime_seconds=max_runtime_seconds,
        pm_response_timeout_seconds=7,
    )


def read_events(workspace: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (workspace / "logs" / "bridge.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def configure_cli(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    config_dir = tmp_path / "config"
    write_templates(template_dir)
    write_report(workspace)
    config_dir.mkdir(parents=True)
    (config_dir / "default.yaml").write_text("apps: {}\n", encoding="utf-8")
    monkeypatch.setattr(cli_module, "ROOT", tmp_path)
    monkeypatch.setattr(cli_module, "WORKSPACE", workspace)
    monkeypatch.setattr(cli_module, "QUEUE_DIR", workspace / "queue")
    monkeypatch.setattr(cli_module, "STATE_PATH", workspace / "state" / "state.json")
    monkeypatch.setattr(cli_module, "LOG_PATH", workspace / "logs" / "bridge.jsonl")
    monkeypatch.setattr(cli_module, "TEMPLATE_DIR", template_dir)
    monkeypatch.setattr(cli_module, "CONFIG_DIR", config_dir)


def test_dogfood_command_requires_auto_confirm(monkeypatch, tmp_path: Path):
    configure_cli(monkeypatch, tmp_path)

    result = CliRunner().invoke(
        cli_module.app,
        ["dogfood-gui-bridge", "--max-cycles", "1", "--max-runtime-seconds", "120"],
    )

    assert result.exit_code == 1
    assert "requires --auto-confirm" in result.output


def test_gui_adapter_calls_happen_in_expected_order(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace)
    gui = FakeGui()

    result = run_gui_bridge_dogfood(config=make_config(workspace, template_dir), gui=gui)

    assert result.completed
    assert result.reason == "MAX_CYCLES_REACHED"
    assert gui.actions == [
        "activate:ChatGPT",
        "copy_text",
        "paste",
        "submit",
        "wait:7",
        "activate:ChatGPT",
        "copy_response",
        "activate:Codex",
        "copy_text",
        "paste",
        "submit",
    ]
    assert (workspace / "outbox" / "pm_assistant_prompt.md").exists()
    assert (workspace / "outbox" / "pm_response.md").exists()
    assert (workspace / "outbox" / "next_local_agent_prompt.md").exists()
    assert (workspace / "queue" / "in_progress.json").exists()


def test_safety_gate_blocks_before_gui_submit(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace, "# Report\n\nRISK_HIGH requires owner review.")
    gui = FakeGui()

    result = run_gui_bridge_dogfood(config=make_config(workspace, template_dir), gui=gui)

    state = StateStore(workspace / "state" / "state.json").load()
    assert not result.completed
    assert result.safety_paused
    assert state.safety_pause
    assert state.state == BridgeStateName.PAUSED_FOR_USER_DECISION
    assert gui.actions == []
    assert (workspace / "inbox" / "user_decision_request.md").exists()


def test_max_cycle_limit_bounds_the_dogfood_loop(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace)
    gui = FakeGui()

    result = run_gui_bridge_dogfood(
        config=make_config(workspace, template_dir, max_cycles=1),
        gui=gui,
    )

    assert result.cycles_completed == 1
    assert gui.actions.count("submit") == 2


def test_max_runtime_limit_stops_before_gui_actions(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace)
    gui = FakeGui()
    times = iter([0.0, 121.0])

    try:
        run_gui_bridge_dogfood(
            config=make_config(workspace, template_dir, max_runtime_seconds=120),
            gui=gui,
            monotonic_fn=lambda: next(times),
        )
    except GuiDogfoodError as error:
        assert "Max runtime reached" in str(error)
    else:
        raise AssertionError("Expected max runtime failure.")

    assert gui.actions == []
    event_types = [event["event_type"] for event in read_events(workspace)]
    assert "gui_dogfood_failed" in event_types


def test_event_logs_are_written_and_no_github_events_exist(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    write_templates(template_dir)
    write_report(workspace)

    run_gui_bridge_dogfood(config=make_config(workspace, template_dir), gui=FakeGui())

    event_types = [event["event_type"] for event in read_events(workspace)]
    assert "gui_dogfood_started" in event_types
    assert "gui_auto_confirm_enabled" in event_types
    assert "pm_prompt_submitted" in event_types
    assert "local_agent_prompt_submitted" in event_types
    assert "gui_dogfood_completed" in event_types
    assert all("github" not in event_type.lower() for event_type in event_types)
