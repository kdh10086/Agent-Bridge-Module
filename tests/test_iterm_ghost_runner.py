from __future__ import annotations

from agent_bridge.gui.iterm_ghost_runner import evaluate_iterm_ghost_runner_context
from agent_bridge.gui.macos_permissions import MacOSPermissionDiagnostic, PermissionProbe, ProcessInfo


def probe() -> PermissionProbe:
    return PermissionProbe(
        label="probe",
        command=("osascript", "-e", "mock"),
        returncode=0,
        stdout="ok",
    )


def diagnostic(
    *,
    codex_markers: dict[str, str] | None = None,
    chain: tuple[ProcessInfo, ...] = (),
    running_under_codex: bool = False,
    running_under_terminal: bool = False,
    terminal_process: str | None = None,
) -> MacOSPermissionDiagnostic:
    return MacOSPermissionDiagnostic(
        current_executable_path="/repo/.venv/bin/python",
        current_python_path="/repo/.venv/bin/python",
        current_user="owner",
        current_shell="/bin/zsh",
        cwd="/repo",
        osascript_path="/usr/bin/osascript",
        python_path="/repo/.venv/bin/python",
        parent_process_chain=chain,
        codex_markers=codex_markers or {},
        running_under_codex_context=running_under_codex,
        running_under_terminal_context=running_under_terminal,
        terminal_context_process=terminal_process,
        system_events_name_probe=probe(),
        frontmost_process_probe=probe(),
        non_click_ui_probe=probe(),
        click_path_preflight_status="ok",
        likely_permission_target="Terminal",
    )


def test_codex_sandbox_hard_blocks_ghost_runner():
    context = evaluate_iterm_ghost_runner_context(
        diagnostic(codex_markers={"CODEX_SANDBOX": "1"}, running_under_codex=True)
    )

    assert not context.allowed
    assert context.hard_blocked
    assert "CODEX_SANDBOX" in context.reason


def test_codex_context_without_terminal_is_rejected():
    context = evaluate_iterm_ghost_runner_context(
        diagnostic(
            codex_markers={"CODEX_SHELL": "1", "CODEX_THREAD_ID": "thread"},
            running_under_codex=True,
        )
    )

    assert not context.allowed
    assert "iTerm/Terminal" in context.reason


def test_iterm_terminal_context_passes():
    context = evaluate_iterm_ghost_runner_context(
        diagnostic(
            running_under_terminal=True,
            terminal_process="/Applications/iTerm.app/Contents/MacOS/iTerm2",
        )
    )

    assert context.allowed
    assert "Terminal/iTerm" in context.reason


def test_iterm_terminal_context_with_codex_markers_warns_but_allows():
    context = evaluate_iterm_ghost_runner_context(
        diagnostic(
            codex_markers={"CODEX_SHELL": "1"},
            running_under_terminal=True,
            terminal_process="/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal",
        )
    )

    assert context.allowed
    assert context.warning is not None
