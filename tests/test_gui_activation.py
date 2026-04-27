from __future__ import annotations

import subprocess
from pathlib import Path

from typer.testing import CliRunner

import agent_bridge.cli as cli_module
from agent_bridge.gui.gui_automation import GuiAutomationAdapter
from agent_bridge.gui.macos_apps import (
    MacOSAppActivator,
    ManualStageTarget,
    load_gui_targets,
)
from agent_bridge.gui.report_roundtrip import (
    ReportRoundtripConfig,
    ReportRoundtripError,
    run_report_roundtrip,
)


def completed(command: list[str], returncode: int, stderr: str = ""):
    return subprocess.CompletedProcess(command, returncode, stdout="", stderr=stderr)


class SequenceRunner:
    def __init__(self, returncodes: list[int]):
        self.returncodes = returncodes
        self.commands: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.commands.append(list(command))
        returncode = self.returncodes.pop(0)
        return completed(list(command), returncode, stderr=f"failed:{returncode}")


def test_osascript_success():
    runner = SequenceRunner([0])
    activator = MacOSAppActivator(runner=runner)

    result = activator.activate_with_result("ChatGPT")

    assert result.succeeded
    assert result.winning_strategy == "osascript"
    assert len(runner.commands) == 1
    assert runner.commands[0][0] == "osascript"


def test_osascript_failure_then_open_app_success():
    runner = SequenceRunner([1, 0])
    activator = MacOSAppActivator(runner=runner)

    result = activator.activate_with_result("ChatGPT")

    assert result.succeeded
    assert result.winning_strategy == "open-app-name"
    assert [command[0] for command in runner.commands] == ["osascript", "open"]
    assert runner.commands[1] == ["open", "-a", "ChatGPT"]


def test_all_activation_strategies_fail():
    runner = SequenceRunner([1, 1, 1, 1])
    activator = MacOSAppActivator(runner=runner)

    result = activator.activate_with_result(
        "ChatGPT",
        app_path="/Applications/ChatGPT.app",
        bundle_id="com.openai.chat",
    )

    assert not result.succeeded
    assert [attempt.strategy for attempt in result.attempts] == [
        "osascript",
        "open-app-name",
        "open-app-path",
        "open-bundle-id",
    ]


def test_app_path_and_bundle_id_config_are_respected(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "default.yaml").write_text(
        """
apps:
  pm_assistant:
    app_name: "ChatGPT"
    app_path: "/Applications/ChatGPT.app"
    bundle_id: "com.openai.chat"
""".lstrip(),
        encoding="utf-8",
    )

    target = load_gui_targets(config_dir).pm_assistant
    plan = MacOSAppActivator().activation_plan(
        target.app_name,
        app_path=target.app_path,
        bundle_id=target.bundle_id,
    )

    assert target.app_path == "/Applications/ChatGPT.app"
    assert target.bundle_id == "com.openai.chat"
    assert ("open-app-path", ("open", "/Applications/ChatGPT.app")) in plan
    assert ("open-bundle-id", ("open", "-b", "com.openai.chat")) in plan


def test_preflight_dry_run_does_not_activate(monkeypatch, tmp_path: Path):
    config_dir = tmp_path / "config"
    workspace = tmp_path / "workspace"
    config_dir.mkdir()
    (config_dir / "default.yaml").write_text("apps: {}\n", encoding="utf-8")
    monkeypatch.setattr(cli_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(cli_module, "WORKSPACE", workspace)

    result = CliRunner().invoke(cli_module.app, ["preflight-gui-apps", "--dry-run"])

    assert result.exit_code == 0
    assert "DRY RUN: activation skipped" in result.output
    assert "No paste, submit, Enter/Return" in result.output


class FailingActivationGui(GuiAutomationAdapter):
    def __init__(self):
        self.actions: list[str] = []

    def activate_app(self, target: ManualStageTarget) -> None:
        self.actions.append(f"activate:{target.app_name}")
        raise RuntimeError("activation preflight failed")

    def copy_text_to_clipboard(self, text: str) -> None:
        self.actions.append("copy_text")

    def paste_clipboard(self) -> None:
        self.actions.append("paste")

    def submit(self) -> None:
        self.actions.append("submit")

    def wait_for_response(self, timeout_seconds: int) -> None:
        self.actions.append("wait")

    def copy_response_text(self) -> str:
        self.actions.append("copy_response")
        return "```CODEX_NEXT_PROMPT\nnoop\n```"


def test_roundtrip_aborts_before_paste_if_pm_activation_preflight_fails(tmp_path: Path):
    workspace = tmp_path / "workspace"
    template_dir = tmp_path / "templates"
    (workspace / "reports").mkdir(parents=True)
    (workspace / "reports" / "latest_agent_report.md").write_text("# Report\n\nReady.", encoding="utf-8")
    template_dir.mkdir()
    (template_dir / "codex_command_wrapper.md").write_text(
        "Type={command_type}\nSource={source}\nPayload={payload}",
        encoding="utf-8",
    )
    gui = FailingActivationGui()

    try:
        run_report_roundtrip(
            config=ReportRoundtripConfig(
                workspace_dir=workspace,
                template_dir=template_dir,
                targets=load_gui_targets(tmp_path / "missing-config"),
                auto_confirm=True,
                max_cycles=1,
                max_runtime_seconds=180,
            ),
            gui=gui,
        )
    except ReportRoundtripError as error:
        assert "activation preflight failed" in str(error)
    else:
        raise AssertionError("Expected activation preflight failure.")

    assert gui.actions == ["activate:Google Chrome"]
