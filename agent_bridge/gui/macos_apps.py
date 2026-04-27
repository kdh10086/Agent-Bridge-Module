from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml


@dataclass(frozen=True)
class ManualStageTarget:
    app_name: str
    app_path: str | None = None
    bundle_id: str | None = None
    window_hint: str | None = None
    paste_instruction: str | None = None


@dataclass(frozen=True)
class GuiTargets:
    pm_assistant: ManualStageTarget
    local_agent: ManualStageTarget


class AppActivationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ActivationAttempt:
    strategy: str
    command: tuple[str, ...]
    succeeded: bool
    output: str = ""


@dataclass(frozen=True)
class ActivationResult:
    app_name: str
    succeeded: bool
    attempts: tuple[ActivationAttempt, ...]

    @property
    def winning_strategy(self) -> str | None:
        for attempt in self.attempts:
            if attempt.succeeded:
                return attempt.strategy
        return None


class AppActivator:
    def activate(
        self,
        app_name: str,
        *,
        app_path: str | None = None,
        bundle_id: str | None = None,
    ) -> None:
        raise NotImplementedError


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass
class MacOSAppActivator(AppActivator):
    osascript_executable: str = "osascript"
    open_executable: str = "open"
    runner: Runner = subprocess.run

    def _activation_commands(
        self,
        app_name: str,
        *,
        app_path: str | None = None,
        bundle_id: str | None = None,
    ) -> list[tuple[str, tuple[str, ...]]]:
        escaped_app_name = app_name.replace("\\", "\\\\").replace('"', '\\"')
        commands = [
            (
                "osascript",
                (
                    self.osascript_executable,
                    "-e",
                    f'tell application "{escaped_app_name}" to activate',
                ),
            ),
            ("open-app-name", (self.open_executable, "-a", app_name)),
        ]
        if app_path:
            commands.append(("open-app-path", (self.open_executable, app_path)))
        if bundle_id:
            commands.append(("open-bundle-id", (self.open_executable, "-b", bundle_id)))
        return commands

    def activation_plan(
        self,
        app_name: str,
        *,
        app_path: str | None = None,
        bundle_id: str | None = None,
    ) -> list[tuple[str, tuple[str, ...]]]:
        return self._activation_commands(app_name, app_path=app_path, bundle_id=bundle_id)

    def activate_with_result(
        self,
        app_name: str,
        *,
        app_path: str | None = None,
        bundle_id: str | None = None,
    ) -> ActivationResult:
        attempts: list[ActivationAttempt] = []
        for strategy, command in self._activation_commands(
            app_name,
            app_path=app_path,
            bundle_id=bundle_id,
        ):
            completed = self.runner(
                list(command),
                check=False,
                text=True,
                capture_output=True,
            )
            output = (completed.stderr or completed.stdout or "").strip()
            attempt = ActivationAttempt(
                strategy=strategy,
                command=command,
                succeeded=completed.returncode == 0,
                output=output,
            )
            attempts.append(attempt)
            if attempt.succeeded:
                return ActivationResult(app_name=app_name, succeeded=True, attempts=tuple(attempts))
        return ActivationResult(app_name=app_name, succeeded=False, attempts=tuple(attempts))

    def activate(
        self,
        app_name: str,
        *,
        app_path: str | None = None,
        bundle_id: str | None = None,
    ) -> None:
        result = self.activate_with_result(app_name, app_path=app_path, bundle_id=bundle_id)
        if not result.succeeded:
            raise AppActivationError(format_activation_result(result))


PM_ASSISTANT_TARGET = ManualStageTarget(
    app_name="Google Chrome",
    app_path=None,
    bundle_id=None,
    window_hint="ChatGPT",
    paste_instruction="Paste into the ChatGPT composer, then review manually. Do not submit automatically.",
)
LOCAL_AGENT_TARGET = ManualStageTarget(
    app_name="Codex",
    app_path=None,
    bundle_id=None,
    window_hint="Agent Bridge",
    paste_instruction="Paste into Codex input, then review manually. Do not submit automatically.",
)


def default_gui_targets() -> GuiTargets:
    return GuiTargets(pm_assistant=PM_ASSISTANT_TARGET, local_agent=LOCAL_AGENT_TARGET)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in config file: {path}")
    return data


def _target_from_config(data: dict[str, Any], fallback: ManualStageTarget) -> ManualStageTarget:
    return ManualStageTarget(
        app_name=str(data.get("app_name") or fallback.app_name),
        app_path=data.get("app_path") if data.get("app_path") is not None else fallback.app_path,
        bundle_id=data.get("bundle_id") if data.get("bundle_id") is not None else fallback.bundle_id,
        window_hint=data.get("window_hint") if data.get("window_hint") is not None else fallback.window_hint,
        paste_instruction=(
            data.get("paste_instruction")
            if data.get("paste_instruction") is not None
            else fallback.paste_instruction
        ),
    )


def _merge_app_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return merged


def load_gui_targets(config_dir: Path) -> GuiTargets:
    defaults = default_gui_targets()
    default_config = _load_yaml(config_dir / "default.yaml")
    local_config = _load_yaml(config_dir / "local.yaml")
    apps = _merge_app_config(
        (default_config.get("apps") or {}) if isinstance(default_config.get("apps") or {}, dict) else {},
        (local_config.get("apps") or {}) if isinstance(local_config.get("apps") or {}, dict) else {},
    )
    pm_config = apps.get("pm_assistant") if isinstance(apps.get("pm_assistant"), dict) else {}
    local_config_data = apps.get("local_agent") if isinstance(apps.get("local_agent"), dict) else {}
    return GuiTargets(
        pm_assistant=_target_from_config(pm_config, defaults.pm_assistant),
        local_agent=_target_from_config(local_config_data, defaults.local_agent),
    )


def format_target_guidance(label: str, target: ManualStageTarget) -> str:
    lines = [
        f"{label} target:",
        f"  App: {target.app_name}",
        f"  App path: {target.app_path or 'unspecified'}",
        f"  Bundle id: {target.bundle_id or 'unspecified'}",
        f"  Window hint: {target.window_hint or 'unspecified'}",
        f"  Paste instruction: {target.paste_instruction or 'Paste manually, then review before submitting.'}",
    ]
    return "\n".join(lines)


def format_activation_plan(label: str, target: ManualStageTarget) -> str:
    activator = MacOSAppActivator()
    lines = [f"{label} activation plan:"]
    for strategy, command in activator.activation_plan(
        target.app_name,
        app_path=target.app_path,
        bundle_id=target.bundle_id,
    ):
        lines.append(f"  - {strategy}: {' '.join(command)}")
    return "\n".join(lines)


def format_activation_result(result: ActivationResult) -> str:
    lines = [
        f"Activation {'succeeded' if result.succeeded else 'failed'} for app: {result.app_name}",
    ]
    if result.winning_strategy:
        lines.append(f"Winning strategy: {result.winning_strategy}")
    for attempt in result.attempts:
        status = "ok" if attempt.succeeded else "failed"
        lines.append(f"- {attempt.strategy}: {status}")
        lines.append(f"  command: {' '.join(attempt.command)}")
        if attempt.output:
            lines.append(f"  output: {attempt.output}")
    return "\n".join(lines)


def discover_gui_apps(search_roots: list[Path] | None = None) -> list[Path]:
    roots = search_roots or [Path("/Applications"), Path.home() / "Applications"]
    apps: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        apps.extend(sorted(root.glob("*.app")))
        apps.extend(sorted(root.glob("*/*.app")))
    return apps


def automatic_submit_supported() -> bool:
    return False
