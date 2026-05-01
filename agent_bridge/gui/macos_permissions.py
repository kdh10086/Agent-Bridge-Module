from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path


ACCESSIBILITY_DENIAL_MARKERS = (
    "-25211",
    "not allowed assistive access",
    "assistive access",
    "not authorized to send apple events to system events",
    "보조 접근이 허용되지",
)


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int | None
    command: str


@dataclass(frozen=True)
class PermissionProbe:
    label: str
    command: tuple[str, ...]
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0 and self.error is None

    @property
    def output(self) -> str:
        return "\n".join(part for part in (self.stdout, self.stderr, self.error or "") if part)

    @property
    def accessibility_denied(self) -> bool:
        return is_accessibility_denied(self.output)


@dataclass(frozen=True)
class MacOSPermissionDiagnostic:
    current_executable_path: str
    current_python_path: str
    current_user: str
    current_shell: str | None
    cwd: str
    osascript_path: str | None
    python_path: str | None
    parent_process_chain: tuple[ProcessInfo, ...]
    codex_markers: dict[str, str]
    running_under_codex_context: bool
    running_under_terminal_context: bool
    terminal_context_process: str | None
    system_events_name_probe: PermissionProbe
    frontmost_process_probe: PermissionProbe
    non_click_ui_probe: PermissionProbe
    click_path_preflight_status: str
    likely_permission_target: str
    remediation_steps: tuple[str, ...] = field(default_factory=tuple)


Runner = Callable[..., subprocess.CompletedProcess[str]]
Which = Callable[[str], str | None]


def is_accessibility_denied(output: str | None) -> bool:
    if not output:
        return False
    normalized = output.lower()
    return any(marker.lower() in normalized for marker in ACCESSIBILITY_DENIAL_MARKERS)


def diagnose_macos_permissions(
    *,
    runner: Runner = subprocess.run,
    which: Which = shutil.which,
    environ: Mapping[str, str] | None = None,
    parent_process_chain: Sequence[ProcessInfo] | None = None,
    cwd: Path | None = None,
    current_executable_path: str | None = None,
    current_python_path: str | None = None,
    current_user: str | None = None,
    current_shell: str | None = None,
) -> MacOSPermissionDiagnostic:
    env = environ if environ is not None else os.environ
    chain = tuple(parent_process_chain) if parent_process_chain is not None else _parent_process_chain()
    osascript_path = which("osascript")
    python_path = which("python")
    current_executable = current_executable_path or sys.executable
    current_python = current_python_path or sys.executable
    user = current_user or getpass.getuser()
    shell = current_shell if current_shell is not None else env.get("SHELL")
    working_dir = str(cwd or Path.cwd())

    codex_markers = {
        name: value
        for name in ("CODEX_SANDBOX", "CODEX_SHELL", "CODEX_THREAD_ID")
        if (value := env.get(name))
    }
    running_under_codex = bool(codex_markers) or _chain_contains(chain, "Codex")
    terminal_process = _terminal_process(chain)
    running_under_terminal = terminal_process is not None

    name_probe = _run_probe(
        "System Events name",
        ("osascript", "-e", 'tell application "System Events" to get name'),
        runner,
    )
    frontmost_probe = _run_probe(
        "System Events frontmost process",
        (
            "osascript",
            "-e",
            'tell application "System Events" to get name of first process whose frontmost is true',
        ),
        runner,
    )
    non_click_probe = _run_probe(
        "System Events non-click UI scripting",
        (
            "osascript",
            "-e",
            'tell application "System Events" to get UI elements enabled',
        ),
        runner,
    )

    probes = (name_probe, frontmost_probe, non_click_probe)
    click_path_status = _click_path_preflight_status(probes)
    likely_target = _likely_permission_target(
        chain=chain,
        running_under_codex=running_under_codex,
        terminal_process=terminal_process,
        probes=probes,
        current_executable=current_executable,
    )

    return MacOSPermissionDiagnostic(
        current_executable_path=current_executable,
        current_python_path=current_python,
        current_user=user,
        current_shell=shell,
        cwd=working_dir,
        osascript_path=osascript_path,
        python_path=python_path,
        parent_process_chain=chain,
        codex_markers=codex_markers,
        running_under_codex_context=running_under_codex,
        running_under_terminal_context=running_under_terminal,
        terminal_context_process=terminal_process,
        system_events_name_probe=name_probe,
        frontmost_process_probe=frontmost_probe,
        non_click_ui_probe=non_click_probe,
        click_path_preflight_status=click_path_status,
        likely_permission_target=likely_target,
        remediation_steps=_remediation_steps(likely_target),
    )


def format_macos_permission_diagnostic(diagnostic: MacOSPermissionDiagnostic) -> str:
    lines = [
        "# macOS Permission Diagnostic",
        "",
        "## Execution Context",
        f"Current executable: {diagnostic.current_executable_path}",
        f"Python path: {diagnostic.current_python_path}",
        f"`python` on PATH: {diagnostic.python_path or 'not found'}",
        f"`osascript` on PATH: {diagnostic.osascript_path or 'not found'}",
        f"Current user: {diagnostic.current_user}",
        f"Shell: {diagnostic.current_shell or 'unknown'}",
        f"CWD: {diagnostic.cwd}",
        (
            "Codex context: "
            + ("yes" if diagnostic.running_under_codex_context else "no")
        ),
        (
            "Terminal/iTerm context: "
            + (
                diagnostic.terminal_context_process
                if diagnostic.terminal_context_process
                else "no"
            )
        ),
        (
            "Codex markers: "
            + (
                ", ".join(f"{key}=set" for key in sorted(diagnostic.codex_markers))
                if diagnostic.codex_markers
                else "none"
            )
        ),
        "",
        "## Parent Process Chain",
    ]
    if diagnostic.parent_process_chain:
        lines.extend(
            f"- pid={process.pid} ppid={process.ppid if process.ppid is not None else 'unknown'} command={process.command}"
            for process in diagnostic.parent_process_chain
        )
    else:
        lines.append("- unavailable")

    lines.extend(
        [
            "",
            "## System Events Permission Probes",
            _format_probe(diagnostic.system_events_name_probe),
            _format_probe(diagnostic.frontmost_process_probe),
            _format_probe(diagnostic.non_click_ui_probe),
            "",
            "## Click Path Preflight",
            diagnostic.click_path_preflight_status,
            "No click was performed by this diagnostic.",
            "",
            "## Likely Permission Target",
            diagnostic.likely_permission_target,
            "",
            "## Remediation Guidance",
        ]
    )
    lines.extend(f"- {step}" for step in diagnostic.remediation_steps)
    lines.extend(
        [
            "",
            "Owner-run reset options, if macOS TCC state is stale:",
            "- `tccutil reset Accessibility`",
            "- `tccutil reset AppleEvents`",
            "",
            "After changing permissions, rerun:",
            "- `python -m agent_bridge.cli diagnose-macos-permissions`",
            "- `python -m agent_bridge.cli diagnose-codex-input-target --paste-test`",
            "",
            "No paste, submit, Enter/Return, GitHub, or Gmail action was attempted.",
        ]
    )
    return "\n".join(lines)


def accessibility_denied_remediation_message(error: str) -> str | None:
    if not is_accessibility_denied(error):
        return None
    return "\n".join(
        [
            "Accessibility permission denied for System Events click path.",
            "Run: python -m agent_bridge.cli diagnose-macos-permissions",
            (
                "Grant Accessibility to the app/process hosting this Python command "
                "(Codex, Terminal, or iTerm2) and grant Automation from that app to "
                "System Events and Codex."
            ),
        ]
    )


def _format_probe(probe: PermissionProbe) -> str:
    status = "passed" if probe.succeeded else "failed"
    denial = " (Accessibility denied)" if probe.accessibility_denied else ""
    output = probe.output.strip()
    lines = [
        f"- {probe.label}: {status}{denial}",
        f"  command: {' '.join(probe.command)}",
    ]
    if probe.returncode is not None:
        lines.append(f"  returncode: {probe.returncode}")
    if output:
        lines.append(f"  output: {output}")
    return "\n".join(lines)


def _run_probe(label: str, command: tuple[str, ...], runner: Runner) -> PermissionProbe:
    try:
        completed = runner(
            list(command),
            check=False,
            text=True,
            capture_output=True,
            timeout=10,
        )
    except Exception as error:  # pragma: no cover - defensive path
        return PermissionProbe(label=label, command=command, returncode=None, error=str(error))
    return PermissionProbe(
        label=label,
        command=command,
        returncode=completed.returncode,
        stdout=(completed.stdout or "").strip(),
        stderr=(completed.stderr or "").strip(),
    )


def _parent_process_chain(limit: int = 12) -> tuple[ProcessInfo, ...]:
    chain: list[ProcessInfo] = []
    pid = os.getpid()
    for _ in range(limit):
        process = _process_info(pid)
        if process is None:
            break
        chain.append(process)
        if process.ppid in (None, 0, 1) or process.ppid == process.pid:
            break
        pid = process.ppid
    return tuple(chain)


def _process_info(pid: int) -> ProcessInfo | None:
    try:
        completed = subprocess.run(
            ["ps", "-o", "pid=", "-o", "ppid=", "-o", "comm=", "-p", str(pid)],
            check=False,
            text=True,
            capture_output=True,
            timeout=5,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    text = completed.stdout.strip()
    if not text:
        return None
    parts = text.split(maxsplit=2)
    if len(parts) < 2:
        return None
    command = parts[2] if len(parts) > 2 else "unknown"
    try:
        return ProcessInfo(pid=int(parts[0]), ppid=int(parts[1]), command=command)
    except ValueError:
        return None


def _chain_contains(chain: Sequence[ProcessInfo], needle: str) -> bool:
    needle_lower = needle.lower()
    return any(needle_lower in process.command.lower() for process in chain)


def _terminal_process(chain: Sequence[ProcessInfo]) -> str | None:
    for process in chain:
        command = process.command.lower()
        if "terminal.app" in command or command.endswith("/terminal"):
            return process.command
        if "iterm" in command or "iterm2" in command:
            return process.command
    return None


def _click_path_preflight_status(probes: Sequence[PermissionProbe]) -> str:
    if any(probe.accessibility_denied for probe in probes):
        return (
            "System Events UI scripting is denied by macOS Accessibility; "
            "the click path is expected to fail with -25211."
        )
    if all(probe.succeeded for probe in probes):
        return (
            "Basic System Events UI scripting probes passed. This diagnostic did not "
            "perform a click; paste-test remains the authoritative click-path check."
        )
    return (
        "System Events UI scripting probes did not all pass. This diagnostic did not "
        "perform a click; inspect probe output before retrying paste-test."
    )


def _likely_permission_target(
    *,
    chain: Sequence[ProcessInfo],
    running_under_codex: bool,
    terminal_process: str | None,
    probes: Sequence[PermissionProbe],
    current_executable: str,
) -> str:
    denied = any(probe.accessibility_denied for probe in probes)
    if running_under_codex:
        suffix = " Accessibility is currently denied." if denied else ""
        return (
            "Codex.app is the likely Accessibility permission target for this run. "
            "Also grant Codex Automation permission to control System Events and Codex."
            + suffix
        )
    if terminal_process:
        suffix = " Accessibility is currently denied." if denied else ""
        return (
            f"{terminal_process} is the likely Accessibility permission target for this run. "
            "Also grant that terminal app Automation permission to control System Events and Codex."
            + suffix
        )
    process_hint = chain[0].command if chain else current_executable
    return (
        f"The hosting process appears to be {process_hint}. Grant Accessibility to the "
        "GUI app that launched this Python process; granting only `.venv` Python or "
        "`osascript` may not be sufficient."
    )


def _remediation_steps(likely_target: str) -> tuple[str, ...]:
    return (
        "Open System Settings > Privacy & Security > Accessibility.",
        f"Grant Accessibility to the likely runner app: {likely_target}",
        "Open System Settings > Privacy & Security > Automation.",
        "Allow the runner app to control System Events.",
        "Allow the runner app to control Codex when prompted or listed.",
        "If using an external Terminal runner, grant permissions to Terminal or iTerm2, not only to Python.",
        "If using the Codex Full Access context, grant permissions to Codex.app.",
        "Quit and reopen the runner app after changing permissions, then rerun the diagnostics.",
    )
