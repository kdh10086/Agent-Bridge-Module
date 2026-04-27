from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

import agent_bridge.cli as cli_module
from agent_bridge.core.command_queue import CommandQueue
from agent_bridge.core.models import Command, CommandType
from agent_bridge.gui.macos_apps import automatic_submit_supported, load_gui_targets


def write_templates(template_dir: Path) -> None:
    template_dir.mkdir(parents=True)
    (template_dir / "pm_report_prompt.md").write_text("PM prompt:\n{report}", encoding="utf-8")
    (template_dir / "codex_command_wrapper.md").write_text(
        "Type={command_type}\nSource={source}\nPayload={payload}",
        encoding="utf-8",
    )


def write_config(config_dir: Path) -> None:
    config_dir.mkdir(parents=True)
    (config_dir / "default.yaml").write_text(
        """
apps:
  pm_assistant:
    app_name: "Google Chrome"
    window_hint: "ChatGPT"
    paste_instruction: "Paste into the ChatGPT composer, then review manually."
  local_agent:
    app_name: "Codex"
    window_hint: "Agent Bridge"
    paste_instruction: "Paste into Codex input, then review manually."
""".lstrip(),
        encoding="utf-8",
    )


def configure_cli(monkeypatch, tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path
    workspace = root / "workspace"
    template_dir = root / "templates"
    config_dir = root / "config"
    write_templates(template_dir)
    write_config(config_dir)
    (workspace / "reports").mkdir(parents=True)
    (workspace / "reports" / "latest_agent_report.md").write_text("# Report\n\nReady.", encoding="utf-8")
    monkeypatch.setattr(cli_module, "ROOT", root)
    monkeypatch.setattr(cli_module, "WORKSPACE", workspace)
    monkeypatch.setattr(cli_module, "QUEUE_DIR", workspace / "queue")
    monkeypatch.setattr(cli_module, "STATE_PATH", workspace / "state" / "state.json")
    monkeypatch.setattr(cli_module, "LOG_PATH", workspace / "logs" / "bridge.jsonl")
    monkeypatch.setattr(cli_module, "TEMPLATE_DIR", template_dir)
    monkeypatch.setattr(cli_module, "CONFIG_DIR", config_dir)
    return workspace, template_dir, config_dir


def test_target_metadata_loads_from_default_and_local_override(tmp_path: Path):
    config_dir = tmp_path / "config"
    write_config(config_dir)
    (config_dir / "local.yaml").write_text(
        """
apps:
  local_agent:
    app_name: "Codex Nightly"
""".lstrip(),
        encoding="utf-8",
    )

    targets = load_gui_targets(config_dir)

    assert targets.pm_assistant.app_name == "Google Chrome"
    assert targets.pm_assistant.window_hint == "ChatGPT"
    assert targets.local_agent.app_name == "Codex Nightly"
    assert targets.local_agent.window_hint == "Agent Bridge"


def test_stage_pm_prompt_output_includes_pm_target_guidance(monkeypatch, tmp_path: Path):
    configure_cli(monkeypatch, tmp_path)

    result = CliRunner().invoke(cli_module.app, ["stage-pm-prompt", "--dry-run"])

    assert result.exit_code == 0
    assert "PM assistant target:" in result.output
    assert "Google Chrome" in result.output
    assert "ChatGPT" in result.output


def test_stage_local_agent_prompt_output_includes_local_target_guidance(monkeypatch, tmp_path: Path):
    workspace, _, _ = configure_cli(monkeypatch, tmp_path)
    payload = tmp_path / "payload.md"
    payload.write_text("# Task\n\nReport status.", encoding="utf-8")
    CommandQueue(workspace / "queue").enqueue(
        Command(
            id="cmd_test",
            type=CommandType.REQUEST_STATUS_REPORT,
            source="test",
            payload_path=str(payload),
            dedupe_key="target-test",
        )
    )

    result = CliRunner().invoke(cli_module.app, ["stage-local-agent-prompt", "--dry-run"])

    assert result.exit_code == 0
    assert "Local coding agent target:" in result.output
    assert "Codex" in result.output
    assert "Agent Bridge" in result.output
    assert not (workspace / "queue" / "in_progress.json").exists()


def test_show_gui_targets_prints_metadata_without_activation(monkeypatch, tmp_path: Path):
    configure_cli(monkeypatch, tmp_path)

    result = CliRunner().invoke(cli_module.app, ["show-gui-targets"])

    assert result.exit_code == 0
    assert "PM assistant target:" in result.output
    assert "Local coding agent target:" in result.output
    assert "App activation: manual-confirmation only for local-agent dispatch." in result.output
    assert "Automatic submit/Enter: not supported." in result.output
    assert automatic_submit_supported() is False
