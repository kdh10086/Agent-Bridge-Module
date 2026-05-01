from __future__ import annotations

import subprocess

from typer.testing import CliRunner

import agent_bridge.cli as cli_module
from agent_bridge.gui.codex_ui_detector import (
    CODEX_PASTE_TEST_MARKER,
    CodexPasteTestResult,
    format_codex_paste_test_result,
)
from agent_bridge.gui.macos_permissions import (
    PermissionProbe,
    ProcessInfo,
    diagnose_macos_permissions,
    format_macos_permission_diagnostic,
    is_accessibility_denied,
)


def completed(command: list[str], returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)


class SequenceRunner:
    def __init__(self, results: list[tuple[int, str, str]]):
        self.results = results
        self.commands: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.commands.append(list(command))
        returncode, stdout, stderr = self.results.pop(0)
        return completed(list(command), returncode, stdout=stdout, stderr=stderr)


def test_detects_25211_as_accessibility_denial():
    assert is_accessibility_denied(
        "System Events에 오류 발생: osascript에 보조 접근이 허용되지 않습니다. (-25211)"
    )


def test_reports_likely_codex_permission_target_when_codex_context_denied():
    runner = SequenceRunner(
        [
            (1, "", "execution error: osascript is not allowed assistive access (-25211)"),
            (1, "", "execution error: osascript is not allowed assistive access (-25211)"),
            (1, "", "execution error: osascript is not allowed assistive access (-25211)"),
        ]
    )

    diagnostic = diagnose_macos_permissions(
        runner=runner,
        which=lambda name: f"/usr/bin/{name}",
        environ={"CODEX_SHELL": "1"},
        parent_process_chain=(
            ProcessInfo(pid=42, ppid=1, command="/Applications/Codex.app/Contents/MacOS/Codex"),
        ),
        current_executable_path="/repo/.venv/bin/python",
        current_python_path="/repo/.venv/bin/python",
        current_user="owner",
        current_shell="/bin/zsh",
    )

    output = format_macos_permission_diagnostic(diagnostic)

    assert diagnostic.system_events_name_probe.accessibility_denied
    assert "click path is expected to fail with -25211" in diagnostic.click_path_preflight_status
    assert "Codex.app is the likely Accessibility permission target" in diagnostic.likely_permission_target
    assert "CODEX_SHELL=set" in output
    assert "No click was performed by this diagnostic." in output
    assert not any("click at" in " ".join(command) for command in runner.commands)


def test_reports_terminal_permission_target_when_terminal_hosts_runner():
    runner = SequenceRunner(
        [
            (0, "System Events\n", ""),
            (0, "Terminal\n", ""),
            (0, "true\n", ""),
        ]
    )

    diagnostic = diagnose_macos_permissions(
        runner=runner,
        which=lambda name: f"/usr/bin/{name}",
        environ={},
        parent_process_chain=(
            ProcessInfo(pid=10, ppid=9, command="/repo/.venv/bin/python"),
            ProcessInfo(pid=9, ppid=1, command="/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal"),
        ),
    )

    assert diagnostic.running_under_terminal_context
    assert "Terminal.app" in diagnostic.likely_permission_target
    assert "Basic System Events UI scripting probes passed" in diagnostic.click_path_preflight_status


def test_cli_diagnose_macos_permissions_uses_formatter(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_module, "WORKSPACE", tmp_path / "workspace")
    monkeypatch.setattr(cli_module, "LOG_PATH", tmp_path / "workspace" / "logs" / "bridge.jsonl")

    probe = PermissionProbe(
        label="System Events name",
        command=("osascript", "-e", "mock"),
        returncode=0,
        stdout="System Events",
    )

    class FakeDiagnostic:
        running_under_codex_context = False
        running_under_terminal_context = True
        terminal_context_process = "Terminal"
        osascript_path = "/usr/bin/osascript"
        system_events_name_probe = probe
        frontmost_process_probe = probe
        non_click_ui_probe = probe
        likely_permission_target = "Terminal is the likely target"

    monkeypatch.setattr(cli_module, "run_macos_permission_diagnostic", lambda: FakeDiagnostic())
    monkeypatch.setattr(cli_module, "format_macos_permission_diagnostic", lambda _diagnostic: "formatted")

    result = CliRunner().invoke(cli_module.app, ["diagnose-macos-permissions"])

    assert result.exit_code == 0
    assert "formatted" in result.output


def test_paste_test_reports_actionable_accessibility_remediation():
    result = CodexPasteTestResult(
        target_app="Codex",
        marker_text=CODEX_PASTE_TEST_MARKER,
        visual_detection_backend_available=True,
        visual_screenshot_captured=True,
        visual_plus_button_found=True,
        visual_plus_button_bbox=(1, 2, 3, 4),
        visual_plus_button_confidence=1.0,
        visual_selected_strategy="visual_plus_anchor",
        visual_click_point=(10, 20),
        visual_click_point_safe=True,
        click_attempted=True,
        error="execution error: osascript에 보조 접근이 허용되지 않습니다. (-25211)",
    )

    output = format_codex_paste_test_result(result)

    assert "Accessibility permission denied for System Events click path" in output
    assert "python -m agent_bridge.cli diagnose-macos-permissions" in output
