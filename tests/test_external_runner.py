from __future__ import annotations

import os
import subprocess
from pathlib import Path

from agent_bridge.gui.external_runner import (
    detect_codex_sandbox,
    format_external_runner_preflight,
    preflight_external_runner,
)
from agent_bridge.gui.macos_apps import ManualStageTarget


def completed(command: list[str], returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)


class SequenceRunner:
    def __init__(self, returncodes: list[int]):
        self.returncodes = returncodes
        self.commands: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.commands.append(list(command))
        return completed(list(command), self.returncodes.pop(0), stdout="com.example.app")


def target(name: str) -> ManualStageTarget:
    return ManualStageTarget(app_name=name)


def test_codex_sandbox_marker_causes_external_runner_preflight_failure():
    preflight = preflight_external_runner(
        pm_target=target("ChatGPT"),
        local_agent_target=target("Codex"),
        env={"CODEX_SANDBOX": "1", "CODEX_THREAD_ID": "thread"},
        runner=SequenceRunner([0, 0]),
        which=lambda _: "/usr/bin/tool",
    )

    assert preflight.running_inside_codex
    assert preflight.restricted_codex_sandbox
    assert not preflight.can_run_external_gui
    assert "Restricted Codex sandbox: yes" in format_external_runner_preflight(preflight)


def test_full_access_codex_context_warns_but_allows_when_preflights_pass():
    preflight = preflight_external_runner(
        pm_target=target("ChatGPT"),
        local_agent_target=target("Codex"),
        env={"CODEX_SHELL": "1", "CODEX_THREAD_ID": "thread"},
        runner=SequenceRunner([0, 0]),
        which=lambda _: "/usr/bin/tool",
    )
    output = format_external_runner_preflight(preflight)

    assert preflight.running_inside_codex
    assert not preflight.restricted_codex_sandbox
    assert preflight.full_access_codex_context
    assert preflight.can_run_external_gui
    assert "Full Access Codex context: yes" in output


def test_normal_environment_passes_sandbox_check():
    assert detect_codex_sandbox({}) == {}
    preflight = preflight_external_runner(
        pm_target=target("ChatGPT"),
        local_agent_target=target("Codex"),
        env={},
        runner=SequenceRunner([0, 0]),
        which=lambda _: "/usr/bin/tool",
    )

    assert not preflight.running_inside_codex
    assert preflight.can_run_external_gui


def test_missing_clipboard_tools_are_reported():
    preflight = preflight_external_runner(
        pm_target=target("ChatGPT"),
        local_agent_target=target("Codex"),
        env={},
        runner=SequenceRunner([0, 0]),
        which=lambda _: None,
    )
    output = format_external_runner_preflight(preflight)

    assert not preflight.clipboard_tools_available
    assert "pbcopy: missing" in output
    assert "pbpaste: missing" in output


def test_app_activation_preflight_failure_is_reported():
    preflight = preflight_external_runner(
        pm_target=target("ChatGPT"),
        local_agent_target=target("Codex"),
        env={},
        runner=SequenceRunner([1, 0]),
        which=lambda _: "/usr/bin/tool",
    )
    output = format_external_runner_preflight(preflight)

    assert not preflight.apps_resolve
    assert not preflight.can_run_external_gui
    assert "osascript resolve ChatGPT: failed" in output


def test_runner_script_refuses_inside_sandbox():
    env = os.environ.copy()
    env["CODEX_SANDBOX"] = "1"
    result = subprocess.run(
        ["bash", "scripts/run_gui_roundtrip_external.sh"],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 1
    assert "Refusing to run GUI automation from the restricted Codex sandbox" in result.stdout
    assert "dogfood-report-roundtrip" not in result.stdout


def test_runner_script_allows_full_access_context_when_preflights_pass(tmp_path: Path):
    log_path = tmp_path / "fake_python.log"
    fake_python = tmp_path / "fake_python.sh"
    fake_python.write_text(
        f"""#!/usr/bin/env bash
echo "$*" >> "{log_path}"
exit 0
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    env.pop("CODEX_SANDBOX", None)
    env["CODEX_SHELL"] = "1"
    env["CODEX_THREAD_ID"] = "thread"
    env["PYTHON"] = str(fake_python)

    result = subprocess.run(
        ["bash", "scripts/run_gui_roundtrip_external.sh"],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 0
    assert "Full Access Codex context markers detected" in result.stdout
    calls = log_path.read_text(encoding="utf-8")
    assert "preflight-external-runner" in calls
    assert "dogfood-report-roundtrip --auto-confirm --max-cycles 1 --max-runtime-seconds 180" in calls


def test_runner_script_does_not_launch_roundtrip_when_preflight_fails(tmp_path: Path):
    log_path = tmp_path / "fake_python.log"
    fake_python = tmp_path / "fake_python.sh"
    fake_python.write_text(
        f"""#!/usr/bin/env bash
echo "$*" >> "{log_path}"
if [[ "$*" == *"preflight-gui-apps --pm-app Google Chrome --activate"* ]]; then
  exit 1
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    for key in ["CODEX_SANDBOX", "CODEX_SHELL", "CODEX_THREAD_ID"]:
        env.pop(key, None)
    env["PYTHON"] = str(fake_python)

    result = subprocess.run(
        ["bash", "scripts/run_gui_roundtrip_external.sh"],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 1
    calls = log_path.read_text(encoding="utf-8")
    assert "preflight-external-runner" in calls
    assert "preflight-gui-apps --pm-app Google Chrome --activate" in calls
    assert "dogfood-report-roundtrip" not in calls


def test_runner_script_uses_chatgpt_mac_visual_preflight_and_blocks_unsupported_capture(
    tmp_path: Path,
):
    log_path = tmp_path / "fake_python.log"
    fake_python = tmp_path / "fake_python.sh"
    fake_python.write_text(
        f"""#!/usr/bin/env bash
if [[ "$1" == "-" ]]; then
  echo "chatgpt_mac_visual|chatgpt_mac"
  exit 0
fi
echo "$*" >> "{log_path}"
if [[ "$*" == *"diagnose-visual-state --app chatgpt_mac"* ]]; then
  echo "Matched state: IDLE"
fi
if [[ "$*" == *"diagnose-chatgpt-mac-response-capture"* ]]; then
  echo "Response capture supported: no"
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    for key in ["CODEX_SANDBOX", "CODEX_SHELL", "CODEX_THREAD_ID"]:
        env.pop(key, None)
    env["PYTHON"] = str(fake_python)

    result = subprocess.run(
        ["bash", "scripts/run_gui_roundtrip_external.sh"],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 1
    calls = log_path.read_text(encoding="utf-8")
    assert "preflight-gui-apps --pm-app ChatGPT --activate" in calls
    assert "diagnose-visual-state --app chatgpt_mac" in calls
    assert "diagnose-chatgpt-mac-response-capture" in calls
    assert "preflight-pm-backend" not in calls
    assert "dogfood-report-roundtrip" not in calls
