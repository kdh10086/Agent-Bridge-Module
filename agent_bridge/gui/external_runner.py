from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Mapping

from agent_bridge.gui.app_diagnostics import CommandDiagnostic
from agent_bridge.gui.macos_apps import ManualStageTarget, Runner


CODEX_SANDBOX_MARKERS = ("CODEX_SANDBOX", "CODEX_SHELL", "CODEX_THREAD_ID")


@dataclass(frozen=True)
class ExternalRunnerPreflight:
    codex_markers: dict[str, str]
    pbcopy_path: str | None
    pbpaste_path: str | None
    pm_app_resolution: CommandDiagnostic
    local_agent_resolution: CommandDiagnostic

    @property
    def running_inside_codex(self) -> bool:
        return bool(self.codex_markers)

    @property
    def clipboard_tools_available(self) -> bool:
        return bool(self.pbcopy_path and self.pbpaste_path)

    @property
    def apps_resolve(self) -> bool:
        return self.pm_app_resolution.succeeded and self.local_agent_resolution.succeeded

    @property
    def can_run_external_gui(self) -> bool:
        return not self.running_inside_codex and self.clipboard_tools_available and self.apps_resolve


def detect_codex_sandbox(env: Mapping[str, str] | None = None) -> dict[str, str]:
    source_env = env if env is not None else os.environ
    return {key: source_env[key] for key in CODEX_SANDBOX_MARKERS if source_env.get(key)}


def _resolve_app_with_osascript(
    target: ManualStageTarget,
    *,
    runner: Runner,
) -> CommandDiagnostic:
    escaped_app_name = target.app_name.replace("\\", "\\\\").replace('"', '\\"')
    command = ("osascript", "-e", f'id of application "{escaped_app_name}"')
    try:
        completed = runner(
            list(command),
            check=False,
            text=True,
            capture_output=True,
            timeout=10,
        )
    except FileNotFoundError:
        return CommandDiagnostic(
            label=f"osascript resolve {target.app_name}",
            command=command,
            output="Command not found: osascript",
        )
    except subprocess.TimeoutExpired:
        return CommandDiagnostic(
            label=f"osascript resolve {target.app_name}",
            command=command,
            output="Timed out after 10s",
        )
    output = (completed.stdout or completed.stderr or "").strip()
    return CommandDiagnostic(
        label=f"osascript resolve {target.app_name}",
        command=command,
        returncode=completed.returncode,
        output=output,
    )


def preflight_external_runner(
    *,
    pm_target: ManualStageTarget,
    local_agent_target: ManualStageTarget,
    env: Mapping[str, str] | None = None,
    runner: Runner = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> ExternalRunnerPreflight:
    return ExternalRunnerPreflight(
        codex_markers=detect_codex_sandbox(env),
        pbcopy_path=which("pbcopy"),
        pbpaste_path=which("pbpaste"),
        pm_app_resolution=_resolve_app_with_osascript(pm_target, runner=runner),
        local_agent_resolution=_resolve_app_with_osascript(local_agent_target, runner=runner),
    )


def _format_command_diagnostic(diagnostic: CommandDiagnostic) -> list[str]:
    status = "ok" if diagnostic.succeeded else "failed"
    lines = [
        f"- {diagnostic.label}: {status}",
        f"  command: {' '.join(diagnostic.command) if diagnostic.command else 'not run'}",
    ]
    if diagnostic.output:
        lines.append(f"  output: {diagnostic.output}")
    return lines


def format_external_runner_preflight(preflight: ExternalRunnerPreflight) -> str:
    lines = [
        "# External GUI Runner Preflight",
        "",
        "## Codex Sandbox",
        f"- Running inside Codex sandbox: {'yes' if preflight.running_inside_codex else 'no'}",
    ]
    if preflight.codex_markers:
        for key in sorted(preflight.codex_markers):
            lines.append(f"- {key}: set")
    else:
        lines.append("- Sandbox markers: none detected")

    lines.extend(
        [
            "",
            "## Clipboard Tools",
            f"- pbcopy: {preflight.pbcopy_path or 'missing'}",
            f"- pbpaste: {preflight.pbpaste_path or 'missing'}",
            "",
            "## App Resolution",
        ]
    )
    lines.extend(_format_command_diagnostic(preflight.pm_app_resolution))
    lines.extend(_format_command_diagnostic(preflight.local_agent_resolution))

    lines.extend(["", "## Recommended Next Command"])
    if preflight.running_inside_codex:
        lines.append(
            "- Open a normal macOS Terminal, unset Codex sandbox environment markers, and run "
            "`bash scripts/run_gui_roundtrip_external.sh`."
        )
    elif not preflight.clipboard_tools_available:
        lines.append("- Fix missing `pbcopy`/`pbpaste` availability before running GUI automation.")
    elif not preflight.apps_resolve:
        lines.append(
            "- Fix macOS app resolution or update `config/local.yaml`, then rerun "
            "`python -m agent_bridge.cli preflight-external-runner`."
        )
    else:
        lines.append("- Run `bash scripts/run_gui_roundtrip_external.sh` from this normal Terminal.")

    lines.append("")
    lines.append("No paste, submit, Enter/Return, GitHub, or Gmail action was attempted.")
    return "\n".join(lines)
