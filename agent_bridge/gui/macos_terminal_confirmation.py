from __future__ import annotations

import shlex
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from agent_bridge.core.event_log import EventLog
from agent_bridge.gui.manual_confirmation import ConfirmationRequest, format_confirmation_request


class TerminalConfirmationError(RuntimeError):
    pass


TerminalOpener = Callable[[Path, Path, Path], None]


def _applescript_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _write_confirmation_script(request_path: Path, result_path: Path, script_path: Path) -> None:
    script_path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"REQUEST_PATH={shlex.quote(str(request_path))}\n"
        f"RESULT_PATH={shlex.quote(str(result_path))}\n"
        "clear\n"
        "cat \"$REQUEST_PATH\"\n"
        "printf '\\nConfirm? [y/N] '\n"
        "if IFS= read -r answer; then\n"
        "  case \"$(printf '%s' \"$answer\" | tr '[:upper:]' '[:lower:]')\" in\n"
        "    y|yes) printf 'yes\\n' > \"$RESULT_PATH\"; printf '\\nConfirmed. You may close this window.\\n' ;;\n"
        "    *) printf 'no\\n' > \"$RESULT_PATH\"; printf '\\nCancelled. You may close this window.\\n' ;;\n"
        "  esac\n"
        "else\n"
        "  printf 'no\\n' > \"$RESULT_PATH\"\n"
        "fi\n",
        encoding="utf-8",
    )
    script_path.chmod(0o700)


@dataclass
class MacOSTerminalConfirmation:
    workspace_dir: Path
    timeout_seconds: int = 120
    event_log: EventLog | None = None
    terminal_opener: TerminalOpener | None = None
    sleep_fn: Callable[[float], None] = time.sleep
    monotonic_fn: Callable[[], float] = time.monotonic
    osascript_executable: str = "osascript"

    def _open_terminal_window(self, script_path: Path, _request_path: Path, _result_path: Path) -> None:
        command = f"bash {shlex.quote(str(script_path))}"
        script = f'tell application "Terminal" to do script {_applescript_quote(command)}'
        completed = subprocess.run(
            [self.osascript_executable, "-e", script],
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            raise TerminalConfirmationError(
                completed.stderr.strip() or "Could not open Terminal confirmation window."
            )

    def confirm_request(self, request: ConfirmationRequest) -> bool:
        log = self.event_log or EventLog(self.workspace_dir / "logs" / "bridge.jsonl")
        confirmations_dir = self.workspace_dir / "confirmations"
        confirmations_dir.mkdir(parents=True, exist_ok=True)
        token = uuid4().hex
        request_path = confirmations_dir / f"{token}_request.txt"
        result_path = confirmations_dir / f"{token}_result.txt"
        script_path = confirmations_dir / f"{token}_confirm.sh"

        request_path.write_text(format_confirmation_request(request), encoding="utf-8")
        _write_confirmation_script(request_path, result_path, script_path)
        log.append(
            "terminal_confirmation_requested",
            action_summary=request.action_summary,
            request_path=str(request_path),
            result_path=str(result_path),
            prompt_path=str(request.prompt_path) if request.prompt_path else None,
            timeout_seconds=self.timeout_seconds,
        )

        opener = self.terminal_opener or self._open_terminal_window
        try:
            opener(script_path, request_path, result_path)
        except Exception as error:
            log.append(
                "terminal_confirmation_error",
                error=str(error),
                request_path=str(request_path),
                result_path=str(result_path),
            )
            return False

        start = self.monotonic_fn()
        while self.monotonic_fn() - start < self.timeout_seconds:
            if result_path.exists():
                answer = result_path.read_text(encoding="utf-8").strip().lower()
                if answer in {"y", "yes"}:
                    log.append(
                        "terminal_confirmation_confirmed",
                        request_path=str(request_path),
                        result_path=str(result_path),
                    )
                    return True
                log.append(
                    "terminal_confirmation_denied",
                    request_path=str(request_path),
                    result_path=str(result_path),
                    answer=answer,
                )
                return False
            self.sleep_fn(0.2)

        log.append(
            "terminal_confirmation_timeout",
            request_path=str(request_path),
            result_path=str(result_path),
            timeout_seconds=self.timeout_seconds,
        )
        return False
