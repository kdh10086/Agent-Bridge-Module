from __future__ import annotations

from dataclasses import dataclass

from agent_bridge.gui.macos_permissions import MacOSPermissionDiagnostic


@dataclass(frozen=True)
class ITermGhostRunnerContext:
    allowed: bool
    reason: str
    warning: str | None = None
    hard_blocked: bool = False


def evaluate_iterm_ghost_runner_context(
    diagnostic: MacOSPermissionDiagnostic,
) -> ITermGhostRunnerContext:
    if "CODEX_SANDBOX" in diagnostic.codex_markers:
        return ITermGhostRunnerContext(
            allowed=False,
            reason="CODEX_SANDBOX is set; GUI automation is hard-blocked.",
            hard_blocked=True,
        )
    if diagnostic.running_under_terminal_context:
        warning = None
        if diagnostic.codex_markers:
            warning = (
                "Codex environment markers are present, but the process is hosted by "
                "Terminal/iTerm. The ghost runner may proceed if preflights pass."
            )
        return ITermGhostRunnerContext(
            allowed=True,
            reason="Process is hosted by Terminal/iTerm.",
            warning=warning,
        )
    if diagnostic.running_under_codex_context:
        return ITermGhostRunnerContext(
            allowed=False,
            reason="This preflight must be run from iTerm/Terminal for the ghost runner path.",
        )
    return ITermGhostRunnerContext(
        allowed=False,
        reason=(
            "Process is not hosted by iTerm/Terminal. Start the ghost runner from a "
            "normal Terminal or iTerm session."
        ),
    )


def format_iterm_ghost_runner_context(context: ITermGhostRunnerContext) -> str:
    lines = [
        "# iTerm Ghost Runner Context",
        "",
        f"Allowed: {'yes' if context.allowed else 'no'}",
        f"Reason: {context.reason}",
    ]
    if context.warning:
        lines.append(f"Warning: {context.warning}")
    if context.hard_blocked:
        lines.append("Hard block: yes")
    return "\n".join(lines)
