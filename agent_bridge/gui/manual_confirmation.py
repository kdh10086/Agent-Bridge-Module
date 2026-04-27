from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CONFIRMATION_NON_ACTIONS = (
    "Agent Bridge will NOT paste automatically.",
    "Agent Bridge will NOT press Enter or Return.",
    "Agent Bridge will NOT submit the message.",
)


@dataclass(frozen=True)
class ConfirmationRequest:
    action_summary: str
    target_app_name: str | None = None
    target_window_hint: str | None = None
    prompt_path: Path | None = None
    will_do: tuple[str, ...] = ()
    will_not_do: tuple[str, ...] = DEFAULT_CONFIRMATION_NON_ACTIONS


def format_confirmation_request(request: ConfirmationRequest) -> str:
    lines = [
        "Agent Bridge Owner Confirmation",
        "",
        "Action:",
        f"  {request.action_summary}",
        "",
        "Target:",
        f"  App: {request.target_app_name or 'unspecified'}",
        f"  Window hint: {request.target_window_hint or 'unspecified'}",
    ]
    if request.prompt_path is not None:
        lines.extend(["", "Prompt file:", f"  {request.prompt_path}"])
    lines.extend(["", "What will happen:"])
    lines.extend(f"  - {item}" for item in (request.will_do or ("No side effect will run unless confirmed.",)))
    lines.extend(["", "What will NOT happen:"])
    lines.extend(f"  - {item}" for item in request.will_not_do)
    lines.extend(["", "Type y/yes to confirm or n/no to cancel."])
    return "\n".join(lines)


@dataclass(frozen=True)
class ManualConfirmation:
    confirm_fn: Callable[[str], bool] | None = None

    def confirm(self, message: str) -> bool:
        if self.confirm_fn is None:
            return False
        return bool(self.confirm_fn(message))

    def confirm_request(self, request: ConfirmationRequest) -> bool:
        return self.confirm(format_confirmation_request(request))
