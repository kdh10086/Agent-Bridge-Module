from __future__ import annotations

import subprocess
from dataclasses import dataclass


class ClipboardError(RuntimeError):
    pass


class Clipboard:
    def copy_text(self, text: str) -> None:
        raise NotImplementedError

    def read_text(self) -> str:
        raise NotImplementedError


@dataclass(frozen=True)
class MacOSClipboard(Clipboard):
    executable: str = "pbcopy"
    read_executable: str = "pbpaste"

    def copy_text(self, text: str) -> None:
        completed = subprocess.run(
            [self.executable],
            input=text,
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            raise ClipboardError(completed.stderr.strip() or "Clipboard copy failed.")

    def read_text(self) -> str:
        completed = subprocess.run(
            [self.read_executable],
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            raise ClipboardError(completed.stderr.strip() or "Clipboard read failed.")
        return completed.stdout
