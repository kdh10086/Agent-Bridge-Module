from __future__ import annotations

import getpass
import os
import plistlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agent_bridge.gui.macos_apps import ActivationResult, MacOSAppActivator, ManualStageTarget, Runner


@dataclass(frozen=True)
class CommandDiagnostic:
    label: str
    command: tuple[str, ...] | None
    returncode: int | None = None
    output: str = ""
    skipped_reason: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0 and self.skipped_reason is None


@dataclass(frozen=True)
class AppBundleDiagnostic:
    app_path: Path
    exists: bool
    is_dir: bool
    is_symlink: bool
    realpath: Path
    info_plist_exists: bool
    macos_dir_exists: bool
    metadata: dict[str, str]
    executable_path: Path | None
    executable_exists: bool
    executable_is_executable: bool
    launchservices_checks: tuple[CommandDiagnostic, ...]
    process_context: dict[str, str]
    suggested_app_name: str
    suggested_app_path: str
    suggested_bundle_id: str | None
    activation_recommendation: str
    activation_result: ActivationResult | None = None


def _run_command(
    label: str,
    command: tuple[str, ...],
    *,
    runner: Runner,
    timeout_seconds: int = 10,
) -> CommandDiagnostic:
    try:
        completed = runner(
            list(command),
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError:
        return CommandDiagnostic(
            label=label,
            command=command,
            returncode=None,
            output=f"Command not found: {command[0]}",
        )
    except subprocess.TimeoutExpired:
        return CommandDiagnostic(
            label=label,
            command=command,
            returncode=None,
            output=f"Timed out after {timeout_seconds}s",
        )

    output = (completed.stdout or completed.stderr or "").strip()
    return CommandDiagnostic(label=label, command=command, returncode=completed.returncode, output=output)


def _read_metadata(info_plist_path: Path) -> dict[str, str]:
    if not info_plist_path.exists():
        return {}
    try:
        with info_plist_path.open("rb") as plist_file:
            plist = plistlib.load(plist_file)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return {}

    keys = [
        "CFBundleName",
        "CFBundleDisplayName",
        "CFBundleIdentifier",
        "CFBundleExecutable",
        "CFBundlePackageType",
        "LSMinimumSystemVersion",
    ]
    metadata: dict[str, str] = {}
    for key in keys:
        value = plist.get(key)
        if value is not None:
            metadata[key] = str(value)
    return metadata


def _process_context(cwd: Path) -> dict[str, str]:
    codex_env_keys = sorted(key for key in os.environ if "CODEX" in key.upper())
    return {
        "user": getpass.getuser(),
        "shell": os.environ.get("SHELL", ""),
        "path": os.environ.get("PATH", ""),
        "cwd": str(cwd),
        "codex_env_keys": ", ".join(codex_env_keys) if codex_env_keys else "none detected",
    }


def _launchservices_checks(
    *,
    app_name: str,
    bundle_id: str | None,
    runner: Runner,
) -> tuple[CommandDiagnostic, ...]:
    checks: list[CommandDiagnostic] = []
    mdfind_path = shutil.which("mdfind")
    if bundle_id and mdfind_path:
        mdfind_check = _run_command(
            "mdfind bundle id",
            (mdfind_path, f"kMDItemCFBundleIdentifier == '{bundle_id}'"),
            runner=runner,
        )
        if not mdfind_check.output:
            mdfind_check = CommandDiagnostic(
                label=mdfind_check.label,
                command=mdfind_check.command,
                returncode=mdfind_check.returncode,
                output="(no results)",
            )
        checks.append(
            mdfind_check
        )
    elif bundle_id:
        checks.append(
            CommandDiagnostic(
                label="mdfind bundle id",
                command=None,
                skipped_reason="mdfind is unavailable in this environment",
            )
        )
    else:
        checks.append(
            CommandDiagnostic(
                label="mdfind bundle id",
                command=None,
                skipped_reason="bundle id is unavailable",
            )
        )

    escaped_app_name = app_name.replace("\\", "\\\\").replace('"', '\\"')
    checks.append(
        _run_command(
            "osascript resolve app id",
            ("osascript", "-e", f'id of application "{escaped_app_name}"'),
            runner=runner,
        )
    )
    checks.append(
        CommandDiagnostic(
            label="open -Ra",
            command=("open", "-Ra", app_name),
            skipped_reason="skipped because Finder reveal has a GUI side effect and open has no dry-run mode",
        )
    )
    checks.append(
        CommandDiagnostic(
            label="open -a dry-run-like check",
            command=("open", "-a", app_name),
            skipped_reason="skipped because open has no safe dry-run mode; use --activate to run activation attempts",
        )
    )
    return tuple(checks)


def _activation_recommendation(
    *,
    exists: bool,
    is_dir: bool,
    info_plist_exists: bool,
    executable_exists: bool,
    executable_is_executable: bool,
    bundle_id: str | None,
) -> str:
    if not exists:
        return "App path does not exist; update config/local.yaml with an installed .app path."
    if not is_dir:
        return "App path is not a directory; use the .app bundle directory."
    if not info_plist_exists:
        return "Bundle is missing Contents/Info.plist; reinstall or select a valid .app bundle."
    if not executable_exists:
        return "Bundle executable is missing; reinstall the app or select a valid bundle."
    if not executable_is_executable:
        return "Bundle executable is not executable; fix app permissions or reinstall."
    if bundle_id:
        return "Bundle structure is valid; prefer app_path or bundle_id if app-name activation cannot resolve."
    return "Bundle structure is valid; prefer app_path if app-name activation cannot resolve."


def diagnose_app_bundle(
    app_path: Path,
    *,
    activate: bool = False,
    runner: Runner = subprocess.run,
    check_launchservices: bool = True,
) -> AppBundleDiagnostic:
    app_path = app_path.expanduser()
    exists = app_path.exists()
    is_dir = app_path.is_dir()
    is_symlink = app_path.is_symlink()
    realpath = app_path.resolve(strict=False)
    info_plist_path = app_path / "Contents" / "Info.plist"
    macos_dir = app_path / "Contents" / "MacOS"
    metadata = _read_metadata(info_plist_path)
    executable_name = metadata.get("CFBundleExecutable")
    executable_path = macos_dir / executable_name if executable_name else None
    executable_exists = executable_path.exists() if executable_path else False
    executable_is_executable = os.access(executable_path, os.X_OK) if executable_path else False
    suggested_app_name = (
        metadata.get("CFBundleDisplayName")
        or metadata.get("CFBundleName")
        or app_path.stem.removesuffix(".app")
    )
    suggested_bundle_id = metadata.get("CFBundleIdentifier")
    launchservices_checks = (
        _launchservices_checks(app_name=suggested_app_name, bundle_id=suggested_bundle_id, runner=runner)
        if check_launchservices
        else ()
    )
    activation_result = None
    if activate:
        activation_result = MacOSAppActivator(runner=runner).activate_with_result(
            suggested_app_name,
            app_path=str(app_path),
            bundle_id=suggested_bundle_id,
        )

    return AppBundleDiagnostic(
        app_path=app_path,
        exists=exists,
        is_dir=is_dir,
        is_symlink=is_symlink,
        realpath=realpath,
        info_plist_exists=info_plist_path.exists(),
        macos_dir_exists=macos_dir.exists(),
        metadata=metadata,
        executable_path=executable_path,
        executable_exists=executable_exists,
        executable_is_executable=executable_is_executable,
        launchservices_checks=launchservices_checks,
        process_context=_process_context(Path.cwd()),
        suggested_app_name=suggested_app_name,
        suggested_app_path=str(app_path),
        suggested_bundle_id=suggested_bundle_id,
        activation_recommendation=_activation_recommendation(
            exists=exists,
            is_dir=is_dir,
            info_plist_exists=info_plist_path.exists(),
            executable_exists=executable_exists,
            executable_is_executable=executable_is_executable,
            bundle_id=suggested_bundle_id,
        ),
        activation_result=activation_result,
    )


def app_path_for_target(target: ManualStageTarget, discovered_apps: list[Path] | None = None) -> Path | None:
    if target.app_path:
        return Path(target.app_path)
    apps = discovered_apps if discovered_apps is not None else []
    for app_path in apps:
        if app_path.stem == target.app_name:
            return app_path
    return None


def format_app_diagnostic(diagnostic: AppBundleDiagnostic) -> str:
    lines = [
        f"# GUI App Diagnostic: {diagnostic.app_path}",
        "",
        "## Path",
        f"- Exists: {'yes' if diagnostic.exists else 'no'}",
        f"- Is directory: {'yes' if diagnostic.is_dir else 'no'}",
        f"- Is symlink: {'yes' if diagnostic.is_symlink else 'no'}",
        f"- Real path: {diagnostic.realpath}",
        "",
        "## Bundle Structure",
        f"- Contents/Info.plist exists: {'yes' if diagnostic.info_plist_exists else 'no'}",
        f"- Contents/MacOS exists: {'yes' if diagnostic.macos_dir_exists else 'no'}",
        f"- Executable path: {diagnostic.executable_path or 'unavailable'}",
        f"- Executable exists: {'yes' if diagnostic.executable_exists else 'no'}",
        f"- Executable is executable: {'yes' if diagnostic.executable_is_executable else 'no'}",
        "",
        "## Bundle Metadata",
    ]
    for key in [
        "CFBundleName",
        "CFBundleDisplayName",
        "CFBundleIdentifier",
        "CFBundleExecutable",
        "CFBundlePackageType",
        "LSMinimumSystemVersion",
    ]:
        lines.append(f"- {key}: {diagnostic.metadata.get(key, 'unavailable')}")

    lines.extend(["", "## LaunchServices Visibility"])
    if diagnostic.launchservices_checks:
        for check in diagnostic.launchservices_checks:
            lines.append(f"- {check.label}:")
            lines.append(f"  command: {' '.join(check.command) if check.command else 'not run'}")
            if check.skipped_reason:
                lines.append(f"  result: skipped ({check.skipped_reason})")
            else:
                status = "ok" if check.succeeded else f"failed ({check.returncode})"
                lines.append(f"  result: {status}")
                if check.output:
                    lines.append(f"  output: {check.output}")
    else:
        lines.append("- LaunchServices checks skipped by caller.")

    lines.extend(["", "## Process Context"])
    for key, value in diagnostic.process_context.items():
        lines.append(f"- {key}: {value}")

    lines.extend(
        [
            "",
            "## Suggested Config",
            "```yaml",
            "apps:",
            "  <target>:",
            f'    app_name: "{diagnostic.suggested_app_name}"',
            f'    app_path: "{diagnostic.suggested_app_path}"',
            (
                f'    bundle_id: "{diagnostic.suggested_bundle_id}"'
                if diagnostic.suggested_bundle_id
                else "    bundle_id: null"
            ),
            "```",
            f"- Activation recommendation: {diagnostic.activation_recommendation}",
        ]
    )
    if diagnostic.activation_result:
        from agent_bridge.gui.macos_apps import format_activation_result

        lines.extend(["", "## Activation Attempts", format_activation_result(diagnostic.activation_result)])
    return "\n".join(lines)
