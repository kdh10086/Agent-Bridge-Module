from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

from agent_bridge.gui.app_diagnostics import diagnose_app_bundle, format_app_diagnostic


def _write_fake_app(
    root: Path,
    *,
    name: str = "FakeApp",
    bundle_id: str = "com.example.fake",
    executable_name: str = "FakeApp",
    write_info_plist: bool = True,
    write_executable: bool = True,
    executable_mode: int = 0o755,
) -> Path:
    app_path = root / f"{name}.app"
    macos_dir = app_path / "Contents" / "MacOS"
    macos_dir.mkdir(parents=True)
    if write_info_plist:
        with (app_path / "Contents" / "Info.plist").open("wb") as plist_file:
            plistlib.dump(
                {
                    "CFBundleName": name,
                    "CFBundleDisplayName": name,
                    "CFBundleIdentifier": bundle_id,
                    "CFBundleExecutable": executable_name,
                    "CFBundlePackageType": "APPL",
                    "LSMinimumSystemVersion": "13.0",
                },
                plist_file,
            )
    if write_executable:
        executable_path = macos_dir / executable_name
        executable_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable_path.chmod(executable_mode)
    return app_path


def completed(command: list[str], returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)


class SequenceRunner:
    def __init__(self, returncodes: list[int]):
        self.returncodes = returncodes
        self.commands: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.commands.append(list(command))
        return completed(list(command), self.returncodes.pop(0), stderr="mocked")


def test_valid_app_bundle_diagnostic(tmp_path: Path):
    app_path = _write_fake_app(tmp_path)

    diagnostic = diagnose_app_bundle(app_path, check_launchservices=False)

    assert diagnostic.exists
    assert diagnostic.is_dir
    assert diagnostic.info_plist_exists
    assert diagnostic.macos_dir_exists
    assert diagnostic.executable_path == app_path / "Contents" / "MacOS" / "FakeApp"
    assert diagnostic.executable_exists
    assert diagnostic.executable_is_executable
    assert diagnostic.metadata["CFBundleIdentifier"] == "com.example.fake"


def test_app_bundle_missing_info_plist(tmp_path: Path):
    app_path = _write_fake_app(tmp_path, write_info_plist=False)

    diagnostic = diagnose_app_bundle(app_path, check_launchservices=False)

    assert not diagnostic.info_plist_exists
    assert diagnostic.executable_path is None
    assert not diagnostic.executable_exists
    assert "Info.plist" in diagnostic.activation_recommendation


def test_app_bundle_missing_executable(tmp_path: Path):
    app_path = _write_fake_app(tmp_path, write_executable=False)

    diagnostic = diagnose_app_bundle(app_path, check_launchservices=False)

    assert diagnostic.info_plist_exists
    assert diagnostic.executable_path == app_path / "Contents" / "MacOS" / "FakeApp"
    assert not diagnostic.executable_exists
    assert "executable is missing" in diagnostic.activation_recommendation


def test_app_bundle_non_executable_file(tmp_path: Path):
    app_path = _write_fake_app(tmp_path, executable_mode=0o644)

    diagnostic = diagnose_app_bundle(app_path, check_launchservices=False)

    assert diagnostic.executable_exists
    assert not diagnostic.executable_is_executable
    assert "not executable" in diagnostic.activation_recommendation


def test_diagnostic_output_includes_suggested_config(tmp_path: Path):
    app_path = _write_fake_app(tmp_path, name="DiagnosticApp", bundle_id="com.example.diagnostic")

    output = format_app_diagnostic(diagnose_app_bundle(app_path, check_launchservices=False))

    assert "## Suggested Config" in output
    assert 'app_name: "DiagnosticApp"' in output
    assert f'app_path: "{app_path}"' in output
    assert 'bundle_id: "com.example.diagnostic"' in output


def test_activate_path_uses_mocked_activation_runner(tmp_path: Path):
    app_path = _write_fake_app(tmp_path, name="ActivationApp", bundle_id="com.example.activation")
    runner = SequenceRunner([1, 0])

    diagnostic = diagnose_app_bundle(
        app_path,
        activate=True,
        runner=runner,
        check_launchservices=False,
    )

    assert diagnostic.activation_result is not None
    assert diagnostic.activation_result.succeeded
    assert diagnostic.activation_result.winning_strategy == "open-bundle-id"
    assert runner.commands == [
        ["osascript", "-e", 'tell application id "com.example.activation" to activate'],
        ["open", "-b", "com.example.activation"],
    ]
