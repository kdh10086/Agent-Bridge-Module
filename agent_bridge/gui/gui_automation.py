from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Callable

from agent_bridge.gui.clipboard import Clipboard, MacOSClipboard
from agent_bridge.gui.macos_apps import AppActivator, MacOSAppActivator, ManualStageTarget


class GuiAutomationError(RuntimeError):
    pass


class GuiAutomationAdapter:
    def activate_app(self, target: ManualStageTarget) -> None:
        raise NotImplementedError

    def copy_text_to_clipboard(self, text: str) -> None:
        raise NotImplementedError

    def paste_clipboard(self) -> None:
        raise NotImplementedError

    def submit(self) -> None:
        raise NotImplementedError

    def wait_for_response(self, timeout_seconds: int) -> None:
        raise NotImplementedError

    def copy_response_text(self) -> str:
        raise NotImplementedError


@dataclass
class MacOSSystemEventsGuiAdapter(GuiAutomationAdapter):
    clipboard: Clipboard | None = None
    app_activator: AppActivator | None = None
    osascript_executable: str = "osascript"
    sleep_fn: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        if self.clipboard is None:
            self.clipboard = MacOSClipboard()
        if self.app_activator is None:
            self.app_activator = MacOSAppActivator()

    def _run_system_events(self, script: str) -> None:
        completed = subprocess.run(
            [self.osascript_executable, "-e", script],
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            raise GuiAutomationError(completed.stderr.strip() or "macOS GUI automation failed.")

    def activate_app(self, target: ManualStageTarget) -> None:
        if self.app_activator is None:
            raise GuiAutomationError("App activator is not configured.")
        self.app_activator.activate(
            target.app_name,
            app_path=target.app_path,
            bundle_id=target.bundle_id,
        )

    def copy_text_to_clipboard(self, text: str) -> None:
        if self.clipboard is None:
            raise GuiAutomationError("Clipboard is not configured.")
        self.clipboard.copy_text(text)

    def paste_clipboard(self) -> None:
        self._run_system_events('tell application "System Events" to keystroke "v" using command down')

    def submit(self) -> None:
        self._run_system_events('tell application "System Events" to key code 36')

    def wait_for_response(self, timeout_seconds: int) -> None:
        self.sleep_fn(timeout_seconds)

    def copy_response_text(self) -> str:
        if self.clipboard is None:
            raise GuiAutomationError("Clipboard is not configured.")
        self._run_system_events('tell application "System Events" to keystroke "c" using command down')
        text = self.clipboard.read_text()
        if not text.strip():
            raise GuiAutomationError("Copied PM response was empty.")
        return text
